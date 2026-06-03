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


def _load_prompts() -> dict:
    try:
        return json.loads(Path("config/prompts.json").read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("prompts.json yüklenemedi: %s", exc)
        return {}


def _sanitize_characters(chars: List[str], language: str = "tr") -> List[str]:
    """
    Vision modelinin ürettiği jenerik karakter etiketlerini temizler.
    'Gizemli Figür', 'Mantar Figürü', 'bir adam' gibi görsel tasvirler
    yerine anlatıcının doğal kullanabileceği rol/zamir döndürür.

    Gerçek isimler (tek kelimeli özel isim veya bilinen isim kalıbı) dokunulmadan bırakılır.
    """
    # Jenerik kelime kalıpları — bunları içeren etiketler temizlenir
    GENERIC_PATTERNS = re.compile(
        r"\b(figür|figure|adam|kadın|kişi|kız|erkek|çocuk|yaşlı|genç|insan"
        r"|man|woman|person|girl|boy|child|elder|young|human"
        r"|karakter|character|birisi|someone|biri)\b",
        re.IGNORECASE | re.UNICODE,
    )

    # Dile göre yedek unvanlar (sırayla tüketilir)
    FALLBACKS = {
        "tr": ["Protagonist", "Savaşçı", "Gizemli Yabancı", "Rakip", "Müttefik",
               "Lider", "Düşman", "Yardımcı", "Gizli Güç"],
        "en": ["Protagonist", "Warrior", "Mysterious Stranger", "Rival", "Ally",
               "Leader", "Enemy", "Companion", "Hidden Power"],
    }
    fallback_pool = list(FALLBACKS.get(language, FALLBACKS["tr"]))
    fallback_idx = 0

    result = []
    for name in chars:
        name = name.strip()
        if not name:
            continue
        if GENERIC_PATTERNS.search(name):
            # Jenerik — fallback unvan ata (aynı jenerik etiket tekrar gelirse
            # aynı unvanı korumak için basit cache kullanmıyoruz; sırayla ver)
            if fallback_idx < len(fallback_pool):
                result.append(fallback_pool[fallback_idx])
                fallback_idx += 1
            # Fallback bittiyse karakteri tamamen çıkar (anlatıcı zamir kullanır)
        else:
            result.append(name)
    return result


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
        style: str = "epic",
        length: str = "medium",
        language: str = "tr",
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> List[SegmentData]:
        """
        chapter.analysis_data kullanarak segment listesi üretir.
        Görsel sayısı CHUNK_SIZE'ı aşarsa otomatik olarak parça parça üretir.

        Args:
            chapter: Hedef bölüm.
            model: LLM modeli ID.
            style: Stil anahtarı (epic/casual/funny/mysterious/narrator).
            length: Uzunluk anahtarı (short/medium/long).
            language: Dil kodu (tr/en).
            stream_callback: Her token için çağrılacak fonksiyon (metin parçası).

        Returns:
            SegmentData listesi.

        Raises:
            ValueError: Analiz verisi yoksa.
            OpenRouterError: API hatası.
        """
        if not chapter.analysis_data:
            raise ValueError(
                f"'{chapter.name}' bölümünde analiz verisi yok. "
                "Önce AI Analiz sayfasından analiz yapın."
            )

        total_images = len(chapter.images)

        if total_images <= CHUNK_SIZE:
            # ── Küçük bölüm: tek çağrı ──────────────────────────────
            segments = self._generate_range(
                chapter=chapter,
                model=model,
                style=style,
                length=length,
                language=language,
                start=0,
                end=total_images,
                stream_callback=stream_callback,
            )
        else:
            # ── Büyük bölüm: chunk'lara böl ─────────────────────────
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
                chunk_segments = self._generate_range(
                    chapter=chapter,
                    model=model,
                    style=style,
                    length=length,
                    language=language,
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
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> List[SegmentData]:
        """
        chapter.images[start:end] için script üretir.
        image_index değerleri orijinal (global) indeksleri korur.

        NOT: JSON güvenilirliği için her zaman chat_completion (non-streaming) kullanır.
        stream_callback varsa sadece durum mesajları için kullanılır.
        """
        panel_count = end - start
        # Her panel için ortalama ~120 token; long stil 1.8x; min 2000, max 8000
        length_multiplier = 1.8 if length == "long" else 1.0
        max_tokens = min(8000, max(2000, int(panel_count * 120 * length_multiplier)))

        prompt = self._build_prompt_for_range(chapter, style, length, language, start, end)
        messages = [{"role": "user", "content": prompt}]

        logger.info(
            "Aralık üretimi: panel %d-%d, max_tokens=%d",
            start + 1, end, max_tokens,
        )

        # Streaming KULLLANMA — JSON chunk'lar halinde gelince parse başarısız olur.
        # stream_callback sadece durum mesajı için çağrılır, LLM çıktısı için değil.
        result = self._client.chat_completion(
            model, messages, temperature=0.8, max_tokens=max_tokens,
        )
        full_text = result["content"]
        logger.debug(
            "Aralık tamamlandı: %d karakter (token: %s)",
            len(full_text), result.get("usage", {}).get("total_tokens", "?"),
        )

        # Üretilen metni UI'ya aktar (JSON olduğu için çok büyük olabilir, özet yeterli)
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
        self, chapter: Chapter, style: str, length: str, language: str
    ) -> str:
        """Tüm görseller için prompt üretir (geriye dönük uyumluluk)."""
        return self._build_prompt_for_range(
            chapter, style, length, language,
            start=0, end=len(chapter.images),
        )

    def _build_prompt_for_range(
        self,
        chapter: Chapter,
        style: str,
        length: str,
        language: str,
        start: int,
        end: int,
    ) -> str:
        """
        chapter.images[start:end] aralığı için prompt üretir.
        image_index değerleri orijinal (global) indeksleri yansıtır.
        """
        prompts = _load_prompts()
        template = prompts.get("script_generation", "{analysis_data}")
        styles = prompts.get("script_styles", {})
        lengths = prompts.get("script_lengths", {})

        style_desc = styles.get(style, style)
        length_desc = lengths.get(length, length)
        lang_label = LANGUAGE_LABELS.get(language, language)

        # Sadece istenen aralıktaki panellerin analiz verisini ekle
        analysis_lines = []
        for i in range(start, end):
            key = str(i)
            data = chapter.analysis_data.get(key, {})
            if data.get("error") or data.get("parse_error"):
                analysis_lines.append(f"Panel {i + 1}: [analiz başarısız]")
                continue
            scene     = data.get("scene", "")
            action    = data.get("action", "")
            chars     = ", ".join(_sanitize_characters(data.get("characters", []), language))
            dialogues = "; ".join(data.get("dialogues", [])[:2])
            mood      = data.get("mood", "")
            line = (
                f'Panel {i + 1} (image_index={i}): '
                f'Sahne: {scene}. Aksiyon: {action}. '
                f'Karakterler: {chars}. '
                f'Diyalog: {dialogues}. Atmosfer: {mood}.'
            )
            analysis_lines.append(line)

        analysis_data = "\n".join(analysis_lines)

        # str.format() YERİNE replace() kullan —
        # template içindeki JSON örneklerindeki {..} parantezleri
        # str.format() tarafından placeholder olarak yorumlanır ve KeyError verir.
        result = template
        result = result.replace("{language}",      lang_label)
        result = result.replace("{style_desc}",    style_desc)
        result = result.replace("{length_desc}",   length_desc)
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
        style: str = "epic",
        language: str = "tr",
        length: str = "medium",
    ) -> SegmentData:
        """
        Tek bir segmenti yeniden üretir.

        Args:
            chapter: Hedef bölüm.
            segment_index: Segment listesindeki indeks.
            model: LLM modeli ID.
            style: Stil anahtarı.
            language: Dil kodu.
            length: Uzunluk seçeneği.

        Returns:
            Güncellenmiş SegmentData.
        """
        if segment_index < 0 or segment_index >= len(chapter.segments):
            raise IndexError(f"Geçersiz segment indeksi: {segment_index}")

        seg = chapter.segments[segment_index]
        image_idx = seg.image_index
        analysis = chapter.analysis_data.get(str(image_idx), {})

        prompts = _load_prompts()
        styles = prompts.get("script_styles", {})
        style_desc = styles.get(style, style)
        
        lengths = prompts.get("script_lengths", {})
        length_desc = lengths.get(length, length)

        lang_label = LANGUAGE_LABELS.get(language, language)

        scene = analysis.get("scene", "")
        action = analysis.get("action", "")
        chars = ", ".join(_sanitize_characters(analysis.get("characters", []), language))
        dialogues = "; ".join(analysis.get("dialogues", [])[:2])
        mood = analysis.get("mood", "")

        # Komşu segmentleri bağlam olarak al — anlatı sürekliliği için
        prev_text = ""
        next_text = ""
        if segment_index > 0:
            prev_seg = chapter.segments[segment_index - 1]
            prev_text = prev_seg.text
        if segment_index < len(chapter.segments) - 1:
            next_seg = chapter.segments[segment_index + 1]
            next_text = next_seg.text

        context_block = ""
        if prev_text:
            context_block += f"\n[ÖNCEKİ SAHNE — senin metnin bu sahnenin hemen DEVAMI olmalı]:\n\"{prev_text}\"\n"
        if next_text:
            context_block += f"\n[SONRAKİ SAHNE — senin metnin bu sahneye doğal bir köprü kurmalı]:\n\"{next_text}\"\n"

        prompt = (
            f"Sen deneyimli, akıcı ve sürükleyici bir manhwa recap anlatıcısısın.\n"
            f"Aşağıdaki TEK panel için {lang_label} dilinde metin yaz.\n"
            f"\n"
            f"STİL (HARFİYEN UY): {style_desc}\n"
            f"UZUNLUK (HARFİYEN UY): {length_desc}\n"
            f"\n"
            f"━━━ EN KRİTİK KURAL: GÖRSELİ BETIMLEME, HİKAYEYİ ANLAT ━━━\n"
            f"KÖTÜ (görsel tasvir — ASLA): 'A cloaked figure stands before an energy orb in a dim setting...'\n"
            f"İYİ (hikaye anlatımı — BÖYLE): 'He sensed it before he saw it — they were no longer alone.'\n"
            f"Paneldeki görseli tarif etme. O sahnenin ÖYKÜSEL ANLAMINI, gerilimini, duygusunu anlat.\n"
            f"\n"
            f"ANLATICI AKIŞ:\n"
            f"Bu panel daha büyük bir hikayenin parçası. Sıfırdan başlama — önceki sahneden devam et, sonrakine köprü kur.\n"
            f"{context_block}\n"
            f"KURALLAR:\n"
            f"- ÜÇÜNCÜ ŞAHIS: 'Ben'/'I' ASLA.\n"
            f"- DOLAYLI ANLATIM: Diyalogları tırnak içinde ASLA yazma; eylem/düşünce/niyet olarak yedir.\n"
            f"- İSİMLENDİRME: 'Bir adam', 'A man', 'A burly figure', 'A cloaked figure' KESİNLİKLE YASAK. İsim yoksa bağlamsal unvan: 'O', 'Savaşçı', 'He', 'The hooded one'.\n"
            f"- BETİMLEME: Dış görünüş (saç, kıyafet) betimleme — sadece hikaye anlat.\n"
            f"- SAYI: Tüm sayıları yazıyla yaz.\n"
            f"- SFX YASAK: Ses efektleri metne girmesin.\n"
            f"- BÜYÜK HARF: Tümü büyük harfli kelime kullanma.\n"
            f"\nPanel:\n"
            f"Sahne: {scene}\n"
            f"Aksiyon: {action}\n"
            f"Karakterler: {chars}\n"
            f"Diyalog: {dialogues}\n"
            f"Atmosfer: {mood}\n"
            f"\nSADECE {lang_label} dilinde düz metin yaz. JSON veya tırnak işareti kullanma."
        )

        result = self._client.chat_completion(
            model, [{"role": "user", "content": prompt}],
            temperature=0.9, max_tokens=300,
        )
        text = result["content"].strip().strip('"')
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