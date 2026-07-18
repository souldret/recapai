"""
RecapAI - Script üretim motoru.
"""

import json
import logging
import re
from pathlib import Path
from typing import Callable, List, Optional

from core.models import Chapter, SegmentData
from core.openrouter_client import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)

# Kelime/dakika oranları (TTS hızına göre)
WPM = {"tr": 150, "en": 160}

LANGUAGE_LABELS = {"tr": "Türkçe", "en": "İngilizce"}

# Tek API çağrısında işlenecek maksimum panel sayısı.
# 40 panel × ~120 token = ~4800 token çıktı → her modelde güvenli.
CHUNK_SIZE = 40

# Manhwa Fresh niş modülleri (PDF Prompt 2)
DEFAULT_NICHE = "power_fantasy"
VALID_NICHES = ("power_fantasy", "romance", "dark_action", "comedy")


def _load_prompts() -> dict:
    try:
        return json.loads((Path(__file__).resolve().parent.parent / "config" / "prompts.json").read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("prompts.json yüklenemedi: %s", exc)
        return {}


def list_niches() -> List[dict]:
    """UI için niş modül listesi: [{id, label, description}, ...]."""
    prompts = _load_prompts()
    niches = prompts.get("script_niches", {})
    result = []
    for key in VALID_NICHES:
        data = niches.get(key, {})
        if isinstance(data, dict):
            result.append({
                "id": key,
                "label": data.get("label", key),
                "description": data.get("description", ""),
            })
        else:
            result.append({"id": key, "label": key, "description": ""})
    return result


def _resolve_niche_prompt(prompts: dict, niche: str) -> str:
    niches = prompts.get("script_niches", {})
    key = niche if niche in niches else DEFAULT_NICHE
    data = niches.get(key, {})
    if isinstance(data, dict):
        return data.get("prompt", "")
    return str(data) if data else ""


def _is_first_chapter(chapter: Chapter) -> bool:
    """
    Bölümün serinin ilk bölümü olup olmadığını tahmin eder.
    name / order üzerinden sezgisel kontrol.
    'Chapter 12' gibi çok haneli numaralarda yanlış pozitif vermez.
    """
    name = (getattr(chapter, "name", "") or "").strip().lower()

    # Sayıyı yakala: "chapter 1", "bölüm 01", "ep.1", "#1", başta "1 - ..."
    # Sonra sadece 1 kabul et (01 dahil, 10/11/12 değil)
    num_patterns = (
        r"\b(?:ch(?:apter)?|ep(?:isode)?|part|bölüm|bolum)\s*\.?\s*0*1\b",
        r"#\s*0*1\b",
        r"^0*1(?:\b|[\s\-:._])",
    )
    for pat in num_patterns:
        if re.search(pat, name, re.IGNORECASE):
            return True

    order = getattr(chapter, "order", None)
    if order is not None:
        try:
            if int(order) == 1 or int(order) == 0:
                return True
        except (TypeError, ValueError):
            pass

    index = getattr(chapter, "index", None)
    if index is not None:
        try:
            if int(index) == 1 or int(index) == 0:
                return True
        except (TypeError, ValueError):
            pass

    return False


# İtici / jenerik etiketler — prompt'a ve metne girmemeli
_BANNED_LABEL_RE = re.compile(
    r"\b("
    r"protagonist|main\s*character|main\s*char|mc\b"
    r"|ana\s*karakter|baş\s*karakter|bas\s*karakter|kahramanımız|our\s*hero"
    r"|the\s*hero|the\s*mc|gizemli\s*yabancı|mysterious\s*stranger"
    r"|gizemli\s*figür|mysterious\s*figure|cloaked\s*figure"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

_GENERIC_CHAR_RE = re.compile(
    r"\b("
    r"figür|figure|adam|kadın|kişi|kız|erkek|çocuk|yaşlı|genç|insan"
    r"|man|woman|person|girl|boy|child|elder|young|human"
    r"|karakter|character|birisi|someone|biri"
    r"|protagonist|hero|heroine|mc"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def _sanitize_characters(chars: List[str], language: str = "tr") -> List[str]:
    """
    Vision modelinin ürettiği jenerik karakter etiketlerini temizler.

    'Protagonist', 'main character', 'bir adam', 'Gizemli Figür' gibi
    etiketler ATIRENİN DIŞINA atılır. Script motoru doğal zamir kullanır.
    Gerçek isimler (Jin-Woo, Ahmet, Varkas…) korunur.
    """
    result: List[str] = []
    seen = set()
    for raw in chars or []:
        name = (raw or "").strip()
        if not name:
            continue
        # Yasaklı / jenerik etiketleri tamamen çıkar — "Protagonist" ile DEĞİŞTİRME
        if _BANNED_LABEL_RE.search(name) or _GENERIC_CHAR_RE.search(name):
            continue
        # Çok genel tek kelimeler
        low = name.lower().strip()
        if low in {
            "protagonist", "hero", "heroine", "mc", "character", "karakter",
            "someone", "birisi", "biri", "figure", "figür",
        }:
            continue
        key = low
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _scrub_generic_labels(text: str, language: str = "tr") -> str:
    """
    Üretilmiş script metninden itici etiketleri temizler.
    'the protagonist', 'ana karakter', 'main character' vb.
    """
    if not text:
        return text

    # Yaygın kalıplar → doğal zamir / boş
    replacements = [
        # EN
        (r"\bthe\s+main\s+character\b", "he"),
        (r"\bour\s+main\s+character\b", "he"),
        (r"\bmain\s+character\b", "he"),
        (r"\bthe\s+protagonist\b", "he"),
        (r"\bour\s+protagonist\b", "he"),
        (r"\bprotagonist\b", "he"),
        (r"\bthe\s+MC\b", "he"),
        (r"\bMC\b", "he"),
        (r"\bour\s+hero\b", "he"),
        (r"\bthe\s+hero\b", "he"),
        # TR
        (r"\bana\s+karakter(?:imiz|i|in)?\b", "o"),
        (r"\bbaş\s+karakter(?:imiz|i|in)?\b", "o"),
        (r"\bbas\s+karakter(?:imiz|i|in)?\b", "o"),
        (r"\bkahramanımız\b", "o"),
        (r"\bkahramanı\b", "o"),
        (r"\bprotagonist(?:imiz|i)?\b", "o"),
        (r"\bmain\s+character\b", "o"),
    ]
    out = text
    for pat, repl in replacements:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)

    # Çift boşluk / garip "he he" temizliği
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\b(he|she|they|o)\s+\1\b", r"\1", out, flags=re.IGNORECASE)
    # Cümle başı küçük harf düzeltmesi (basit)
    def _cap(m: re.Match) -> str:
        s = m.group(0)
        return s[0] + s[1:].capitalize() if len(s) > 1 else s.upper()

    out = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out.strip()


def _parse_segments(text: str) -> List[dict]:
    """
    LLM çıktısından segment listesini ayrıştırır.
    JSON bloğu, kod bloğu veya regex fallback dener.
    """
    # 1. Doğrudan JSON
    try:
        data = json.loads(text.strip())
        if "segments" in data:
            return data["segments"]
    except json.JSONDecodeError:
        pass

    # 2. Markdown kod bloğu
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if "segments" in data:
                return data["segments"]
        except json.JSONDecodeError:
            pass

    # 3. İlk { ... } bloğu
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if "segments" in data:
                return data["segments"]
        except json.JSONDecodeError:
            pass

    # 4. Regex fallback: "image_index": N, "text": "..."
    fallback = []
    for m in re.finditer(
        r'"image_index"\s*:\s*(\d+).*?"text"\s*:\s*"(.*?)"(?=\s*[,}])',
        text, re.DOTALL
    ):
        fallback.append({"image_index": int(m.group(1)), "text": m.group(2)})
    if fallback:
        logger.warning("JSON parse başarısız; regex fallback %d segment buldu.", len(fallback))
        return fallback

    logger.error("Segment ayrıştırılamadı. Ham metin: %s", text[:200])
    return []


class ScriptGenerator:
    """
    chapter.analysis_data'sından LLM ile script üretir.
    """

    def __init__(self, client: Optional[OpenRouterClient] = None) -> None:
        self._client = client or OpenRouterClient.instance()

    # ── Ana Üretim ─────────────────────────────────────────────────

    def generate_script(
        self,
        chapter: Chapter,
        model: str,
        style: str = "fresh",
        length: str = "medium",
        language: str = "tr",
        niche: str = DEFAULT_NICHE,
        use_hook: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> List[SegmentData]:
        """
        chapter.analysis_data kullanarak segment listesi üretir.
        Manhwa Fresh 3-katmanlı prompt sistemi:
          1) Universal Compression Engine
          2) Niche module (power_fantasy / romance / dark_action / comedy)
          3) Chapter-1 hook (opsiyonel)

        Görsel sayısı CHUNK_SIZE'ı aşarsa otomatik olarak parça parça üretir.

        Args:
            chapter: Hedef bölüm.
            model: LLM modeli ID.
            style: Stil anahtarı (fresh/epic/casual/...).
            length: Uzunluk anahtarı (short/medium/long).
            language: Dil kodu (tr/en).
            niche: Niş modül anahtarı.
            use_hook: True/False zorla; None ise ilk bölüm sezgisi.
            stream_callback: Durum mesajı callback'i.

        Returns:
            SegmentData listesi.
        """
        if not chapter.analysis_data:
            raise ValueError(
                f"'{chapter.name}' bölümünde analiz verisi yok. "
                "Önce AI Analiz sayfasından analiz yapın."
            )

        if use_hook is None:
            use_hook = _is_first_chapter(chapter)

        total_images = len(chapter.images)
        logger.info(
            "Script üretimi: style=%s niche=%s hook=%s length=%s lang=%s images=%d",
            style, niche, use_hook, length, language, total_images,
        )

        if total_images <= CHUNK_SIZE:
            segments = self._generate_range(
                chapter=chapter,
                model=model,
                style=style,
                length=length,
                language=language,
                niche=niche,
                use_hook=use_hook,
                start=0,
                end=total_images,
                stream_callback=stream_callback,
            )
        else:
            chunks = list(range(0, total_images, CHUNK_SIZE))
            total_chunks = len(chunks)
            logger.info(
                "%d görsel %d chunk'a bölündü (chunk_size=%d)",
                total_images, total_chunks, CHUNK_SIZE,
            )
            all_segments: List[SegmentData] = []
            for chunk_i, start in enumerate(chunks):
                end = min(start + CHUNK_SIZE, total_images)
                logger.info(
                    "Chunk %d/%d üretiliyor: panel %d-%d",
                    chunk_i + 1, total_chunks, start + 1, end,
                )
                if stream_callback:
                    stream_callback(
                        f"\n[Bölüm {chunk_i + 1}/{total_chunks}: Panel {start + 1}-{end}]\n"
                    )
                # Hook sadece ilk chunk'ta (seri açılışı)
                chunk_hook = bool(use_hook) and chunk_i == 0
                chunk_segments = self._generate_range(
                    chapter=chapter,
                    model=model,
                    style=style,
                    length=length,
                    language=language,
                    niche=niche,
                    use_hook=chunk_hook,
                    start=start,
                    end=end,
                    stream_callback=stream_callback,
                )
                all_segments.extend(chunk_segments)
                logger.info(
                    "Chunk %d/%d tamamlandı: %d segment",
                    chunk_i + 1, total_chunks, len(chunk_segments),
                )
            segments = all_segments

        chapter.segments = segments
        logger.info("Script tamamlandı: %d segment (toplam %d görsel)", len(segments), total_images)
        return segments

    # ── Belirli Aralık için Üretim ─────────────────────────────────

    def _generate_range(
        self,
        chapter: Chapter,
        model: str,
        style: str,
        length: str,
        language: str,
        start: int,
        end: int,
        niche: str = DEFAULT_NICHE,
        use_hook: bool = False,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> List[SegmentData]:
        """
        chapter.images[start:end] için script üretir.
        image_index değerleri orijinal (global) indeksleri korur.

        NOT: JSON güvenilirliği için her zaman chat_completion (non-streaming) kullanır.
        stream_callback varsa sadece durum mesajları için kullanılır.
        """
        panel_count = end - start
        # short daha kompakt; long daha geniş
        length_multiplier = {"short": 0.85, "medium": 1.0, "long": 1.6}.get(length, 1.0)
        max_tokens = min(8000, max(2000, int(panel_count * 120 * length_multiplier)))

        prompt = self._build_prompt_for_range(
            chapter, style, length, language, start, end,
            niche=niche, use_hook=use_hook,
        )
        messages = [{"role": "user", "content": prompt}]

        logger.info(
            "Aralık üretimi: panel %d-%d, niche=%s hook=%s max_tokens=%d",
            start + 1, end, niche, use_hook, max_tokens,
        )

        # Streaming KULLANMA — JSON chunk'lar halinde gelince parse başarısız olur.
        result = self._client.chat_completion(
            model, messages, temperature=0.75, max_tokens=max_tokens,
        )
        full_text = result["content"]
        logger.debug(
            "Aralık tamamlandı: %d karakter (token: %s)",
            len(full_text), result.get("usage", {}).get("total_tokens", "?"),
        )

        if stream_callback:
            stream_callback(f" [{panel_count} panel tamamlandı]\n")

        raw_segments = _parse_segments(full_text)
        if not raw_segments:
            logger.error("Panel %d-%d için segment üretilemedi.", start + 1, end)
            return []

        return self._build_segment_data(raw_segments, language)

    def _stream_generate(
        self,
        model: str,
        messages: list,
        callback: Callable[[str], None],
        max_tokens: int = 4000,
    ) -> str:
        """Streaming ile metin üretir; her token'ı callback'e iletir."""
        full = []
        try:
            for chunk in self._client.chat_stream(model, messages, max_tokens=max_tokens):
                full.append(chunk)
                callback(chunk)
        except OpenRouterError as exc:
            logger.error("Stream hatası: %s", exc)
            raise
        return "".join(full)

    # ── Prompt ────────────────────────────────────────────────────

    def _build_prompt(
        self,
        chapter: Chapter,
        style: str,
        length: str,
        language: str,
        niche: str = DEFAULT_NICHE,
        use_hook: bool = False,
    ) -> str:
        """Tüm görseller için prompt üretir (geriye dönük uyumluluk)."""
        return self._build_prompt_for_range(
            chapter, style, length, language,
            start=0, end=len(chapter.images),
            niche=niche, use_hook=use_hook,
        )

    def _build_prompt_for_range(
        self,
        chapter: Chapter,
        style: str,
        length: str,
        language: str,
        start: int,
        end: int,
        niche: str = DEFAULT_NICHE,
        use_hook: bool = False,
    ) -> str:
        """
        chapter.images[start:end] aralığı için 3-katmanlı Manhwa Fresh prompt üretir.
        image_index değerleri orijinal (global) indeksleri yansıtır.
        """
        prompts = _load_prompts()
        template = prompts.get("script_generation", "{analysis_data}")
        styles = prompts.get("script_styles", {})
        lengths = prompts.get("script_lengths", {})

        if style not in styles:
            style = "fresh" if "fresh" in styles else style
        style_desc = styles.get(style, style)
        length_desc = lengths.get(length, length)
        lang_label = LANGUAGE_LABELS.get(language, language)

        prompt_1 = prompts.get("script_prompt_1_universal", "")
        niche_module = _resolve_niche_prompt(prompts, niche)
        hook_layer = prompts.get("script_prompt_3_hook", "") if use_hook else ""

        analysis_lines = []
        for i in range(start, end):
            key = str(i)
            data = chapter.analysis_data.get(key, {})
            if data.get("error") or data.get("parse_error"):
                analysis_lines.append(f"Panel {i + 1}: [analiz başarısız]")
                continue
            scene     = _scrub_generic_labels(data.get("scene", "") or "", language)
            action    = _scrub_generic_labels(data.get("action", "") or "", language)
            clean_chars = _sanitize_characters(data.get("characters", []), language)
            if clean_chars:
                chars = ", ".join(clean_chars)
            else:
                chars = "(isim yok — zamir kullan, protagonist/main character deme)"
            dialogues = "; ".join(data.get("dialogues", [])[:2])
            mood      = data.get("mood", "")
            setting   = data.get("setting", "")
            important = data.get("important", None)
            line = (
                f'Panel {i + 1} (image_index={i}): '
                f'Sahne: {scene}. Aksiyon: {action}. '
                f'Karakterler: {chars}. '
                f'Diyalog: {dialogues}. Atmosfer: {mood}.'
            )
            if setting:
                line += f" Mekan: {setting}."
            if important is True:
                line += " [ÖNEMLİ BEAT]"
            analysis_lines.append(line)

        analysis_data = "\n".join(analysis_lines)

        # str.format() YERİNE replace() — JSON örneklerindeki {..} KeyError vermesin
        result = template
        result = result.replace("{language}",      lang_label)
        result = result.replace("{style_desc}",    style_desc)
        result = result.replace("{length_desc}",   length_desc)
        result = result.replace("{prompt_1}",      prompt_1)
        result = result.replace("{niche_module}",  niche_module)
        result = result.replace("{hook_layer}",    hook_layer)
        result = result.replace("{analysis_data}", analysis_data)
        return result

    # ── Segment Üretimi ────────────────────────────────────────────

    def _build_segment_data(
        self, raw: List[dict], language: str
    ) -> List[SegmentData]:
        segments = []
        for item in raw:
            text = item.get("text", "").strip()
            if len(text) < 5:
                continue
            text = _scrub_generic_labels(text, language)
            if len(text) < 5:
                continue
            idx = int(item.get("image_index", len(segments)))
            duration = self.estimate_duration(text, language)
            segments.append(SegmentData(
                image_index=idx,
                text=text,
                duration=duration,
            ))
        return segments

    # ── Tek Segment Yeniden Üretimi ────────────────────────────────

    def regenerate_segment(
        self,
        chapter: Chapter,
        segment_index: int,
        model: str,
        style: str = "fresh",
        language: str = "tr",
        length: str = "medium",
        niche: str = DEFAULT_NICHE,
    ) -> SegmentData:
        """
        Tek bir segmenti yeniden üretir (Manhwa Fresh kurallarıyla).
        """
        if segment_index < 0 or segment_index >= len(chapter.segments):
            raise IndexError(f"Geçersiz segment indeksi: {segment_index}")

        seg = chapter.segments[segment_index]
        image_idx = seg.image_index
        analysis = chapter.analysis_data.get(str(image_idx), {})

        prompts = _load_prompts()
        styles = prompts.get("script_styles", {})
        if style not in styles:
            style = "fresh" if "fresh" in styles else style
        style_desc = styles.get(style, style)

        lengths = prompts.get("script_lengths", {})
        length_desc = lengths.get(length, length)
        lang_label = LANGUAGE_LABELS.get(language, language)

        scene = _scrub_generic_labels(analysis.get("scene", "") or "", language)
        action = _scrub_generic_labels(analysis.get("action", "") or "", language)
        clean_chars = _sanitize_characters(analysis.get("characters", []), language)
        chars = (
            ", ".join(clean_chars)
            if clean_chars
            else "(isim yok — zamir kullan, protagonist/main character deme)"
        )
        dialogues = "; ".join(analysis.get("dialogues", [])[:2])
        mood = analysis.get("mood", "")

        # Komşu segmentler — anlatı sürekliliği
        prev_text = ""
        next_text = ""
        if segment_index > 0:
            prev_text = chapter.segments[segment_index - 1].text
        if segment_index < len(chapter.segments) - 1:
            next_text = chapter.segments[segment_index + 1].text

        context_block = ""
        if prev_text:
            context_block += f"\n[ÖNCEKİ SAHNE — senin metnin bu sahnenin hemen DEVAMI olmalı]:\n\"{prev_text}\"\n"
        if next_text:
            context_block += f"\n[SONRAKİ SAHNE — senin metnin bu sahneye doğal bir köprü kurmalı]:\n\"{next_text}\"\n"
        if not context_block:
            context_block = "(Bağlam yok — tek segment.)"

        template = prompts.get("script_regenerate", "")
        if not template:
            # Minimal fallback
            template = (
                "Manhwa Fresh recap. Dil: {language}. Stil: {style_desc}. Uzunluk: {length_desc}.\n"
                "{prompt_1}\n{niche_module}\n{context_block}\n"
                "Sahne: {scene}\nAksiyon: {action}\nKarakterler: {chars}\n"
                "Diyalog: {dialogues}\nAtmosfer: {mood}\nSadece düz metin."
            )

        prompt = template
        prompt = prompt.replace("{language}", lang_label)
        prompt = prompt.replace("{style_desc}", style_desc)
        prompt = prompt.replace("{length_desc}", length_desc)
        prompt = prompt.replace("{prompt_1}", prompts.get("script_prompt_1_universal", ""))
        prompt = prompt.replace("{niche_module}", _resolve_niche_prompt(prompts, niche))
        prompt = prompt.replace("{context_block}", context_block)
        prompt = prompt.replace("{scene}", scene)
        prompt = prompt.replace("{action}", action)
        prompt = prompt.replace("{chars}", chars)
        prompt = prompt.replace("{dialogues}", dialogues)
        prompt = prompt.replace("{mood}", mood)

        max_tokens = {"short": 180, "medium": 280, "long": 420}.get(length, 280)
        result = self._client.chat_completion(
            model, [{"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=max_tokens,
        )
        text = result["content"].strip().strip('"').strip("`")
        # Model bazen "text:" öneki koyabilir
        if text.lower().startswith("text:"):
            text = text[5:].strip().strip('"')
        text = _scrub_generic_labels(text, language)
        if len(text) < 5:
            raise ValueError("LLM çok kısa metin döndürdü.")

        seg.text = text
        seg.duration = self.estimate_duration(text, language)
        chapter.segments[segment_index] = seg
        return seg

    # ── Süre Tahmini ───────────────────────────────────────────────

    @staticmethod
    def estimate_duration(text: str, language: str = "tr") -> float:
        """
        Metinden tahmini okuma/TTS süresi hesaplar (saniye).

        Args:
            text: Anlatılacak metin.
            language: Dil kodu.

        Returns:
            Saniye cinsinden tahmini süre.
        """
        wpm = WPM.get(language, 150)
        word_count = len(text.split())
        return round((word_count / wpm) * 60, 2)