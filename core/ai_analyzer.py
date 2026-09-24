"""
RecapAI - AI analiz motoru.
"""

import json
import logging
import re
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from core.models import Chapter
from core.openrouter_client import OpenRouterClient, OpenRouterError
from core.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

RATE_LIMIT_DELAY = 1.0   # saniye — ayarlar yoksa varsayılan


def _load_vision_prompt() -> str:
    """prompts.json'dan vision analiz promptunu yükler."""
    from pathlib import Path
    try:
        data = json.loads((Path(__file__).resolve().parent.parent / "config" / "prompts.json").read_text(encoding="utf-8"))
        return data.get("vision_analysis", "Bu görseli analiz et ve JSON formatında döndür.")
    except Exception as exc:
        logger.warning("prompts.json yüklenemedi: %s", exc)
        return "Bu görseli analiz et ve JSON formatında döndür."


def _known_roster_lines(known_names: Optional[List[str]] = None, project=None) -> List[str]:
    lines: List[str] = []
    if project is not None:
        from core.character_bible import format_known_for_vision
        lines = [ln.strip() for ln in format_known_for_vision(project) if ln and str(ln).strip()]
    if not lines:
        lines = [n.strip() for n in (known_names or []) if n and str(n).strip()]
    return lines[:40]


def _with_known_characters(
    prompt: str,
    known_names: Optional[List[str]] = None,
    project=None,
) -> str:
    names = _known_roster_lines(known_names, project)
    if not names:
        return prompt
    roster = "\n".join(f"- {n}" for n in names)
    extra = (
        "\n\nBİLİNEN KARAKTERLER (bu serinin kadrosu — isim + görünüm):\n"
        f"{roster}\n"
        "Bu panelde aynı kişi varsa characters[].name'e TAM kanonik ismi yaz. "
        "Görünüm (saç, zırh) roster ile uyuyorsa visual label'ı name'e koyma; ismi yaz. "
        "Yeni gerçek isim (balon/plaka) varsa ekle. "
        "İsim yoksa name=\"\" bırak; appearance'a kısa görsel not yazılabilir. "
        "YASAK name: Protagonist, MC, blonde woman, yellow hair, kızıl saçlı."
    )
    return prompt + extra


def _sanitize_analysis_characters(parsed: Dict[str, Any], known_names=None, project=None) -> Dict[str, Any]:
    from core.script_generator import _sanitize_character_records

    parsed["characters"] = _sanitize_character_records(
        parsed.get("characters") or [],
        known_names=known_names,
        project=project,
    )
    return parsed


def _parse_json_response(text: str) -> Dict:
    """
    Model çıktısından JSON bloğunu ayrıştırır.
    Kod bloğu içinde olabilir: ```json ... ```
    Her zaman dict döner; list veya diğer JSON türleri için boş {} döner.
    """
    def _ensure_dict(value) -> Dict:
        """Ayrıştırılan değerin dict olmasını garantiler."""
        if isinstance(value, dict):
            return value
        logger.warning("JSON ayrıştırıldı ama dict değil (%s); boş dict döndürülüyor.", type(value).__name__)
        return {}

    # Önce doğrudan parse dene
    try:
        return _ensure_dict(json.loads(text.strip()))
    except json.JSONDecodeError:
        pass

    # Markdown kod bloğunu çıkar
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return _ensure_dict(json.loads(match.group(1).strip()))
        except json.JSONDecodeError:
            pass

    # İlk geçerli { ... } nesnesini raw_decode ile tara (greedy last-} tuzaklarını atla)
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            obj, _ = decoder.raw_decode(text[start:])
            return _ensure_dict(obj)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)

    logger.warning("JSON ayrıştırılamadı, ham metin döndürülüyor.")
    return {"raw_text": text, "parse_error": True}


def _persist_analysis(project, chapter) -> None:
    """Analiz bellekte birikmesin; her kareden sonra proje dosyasına yaz."""
    if project is None:
        return
    try:
        from core.character_bible import extract_records_from_chapter, upsert_characters
        upsert_characters(
            project,
            extract_records_from_chapter(chapter, project),
            getattr(chapter, "id", ""),
        )
    except Exception as exc:
        logger.warning("Karakter bible güncellenemedi: %s", exc)
    try:
        from core.project_manager import persist_project
        persist_project(project)
    except Exception as exc:
        logger.warning("Analiz ara kaydı yazılamadı: %s", exc)


def _fatal_analysis_error(exc: BaseException) -> bool:
    """Anahtar, model veya vision hatası tüm bölümü durdurur."""
    if not isinstance(exc, OpenRouterError):
        return False
    status = int(getattr(exc, "status_code", 0) or 0)
    if status in (401, 404):
        return True
    msg = str(exc).lower()
    return any(hint in msg for hint in (
        "does not support image",
        "image input",
        "cannot read",
        "bulunamadı",
        "no endpoints",
        "not found",
    ))


def _analysis_workers(settings) -> int:
    try:
        workers = int(settings.get("analysis.parallel_workers", 2) or 2)
    except (TypeError, ValueError):
        workers = 2
    return max(1, min(3, workers))


def _is_usable_analysis(data: Any) -> bool:
    """Önbelleğe alınmış analiz tekrar API çağrısı olmadan kullanılabilir mi?"""
    if not isinstance(data, dict):
        return False
    if data.get("error") or data.get("parse_error"):
        return False
    scene = str(data.get("scene") or "").strip()
    action = str(data.get("action") or "").strip()
    if scene or action:
        return True
    # En azından anlamlı bir alan olsun; yalnızca raw_text yetmez.
    for key in ("mood", "setting", "characters", "dialogue"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return True
        if isinstance(val, list) and val:
            return True
    return False


class AIAnalyzer:
    """
    OpenRouterClient üzerinden görsel analizi yönetir.
    SettingsManager üzerinden API key her zaman güncel okunur.
    """

    def __init__(self, client: Optional[OpenRouterClient] = None) -> None:
        # Singleton kullan; dışarıdan verilen client geriye dönük uyumluluk için
        self._client = client or OpenRouterClient.instance()
        self._settings = SettingsManager.instance()

    # ── Tek Görsel ─────────────────────────────────────────────────

    def analyze_image(
        self,
        image_path: str,
        model: str,
        prompt: Optional[str] = None,
        known_names: Optional[List[str]] = None,
        project=None,
    ) -> Dict[str, Any]:
        """
        Tek bir görseli analiz eder.

        Args:
            image_path: Görsel dosya yolu.
            model: Vision modeli ID.
            prompt: Özel prompt (None ise prompts.json'daki kullanılır).

        Returns:
            Analiz sonucu dict. Hata varsa {"error": ..., "raw_text": ...}
        """
        # Her istekte API key kontrolü — SettingsManager'dan taze oku
        if not self._settings.has_api_key():
            self._settings.reload()
        if not self._settings.has_api_key():
            raise OpenRouterError(
                "API anahtarı tanımlı değil.\n\n"
                "Ayarlar > API Ayarları bölümünden OpenRouter API key'inizi girin ve kaydedin."
            )

        effective_prompt = _with_known_characters(
            prompt or _load_vision_prompt(), known_names, project=project,
        )
        logger.debug(
            "analyze_image: model=%s api_key_len=%d path=%s",
            model,
            len(self._settings.get_api_key()),
            image_path,
        )
        try:
            fallback_models = self._settings.get("api.vision_fallback_models", []) or []
            result = self._client.vision_analyze(model, image_path, effective_prompt, fallback_models)
            parsed = _parse_json_response(result["content"])
            parsed["_model"] = result.get("model", model)
            parsed["_usage"] = result.get("usage", {})
            _sanitize_analysis_characters(parsed, known_names=known_names, project=project)
            tokens = result.get("usage", {}).get("total_tokens", 0)
            logger.info("Görsel analiz OK: %s (token: %d)", image_path, tokens)
            return parsed
        except OpenRouterError as exc:
            msg = str(exc).lower()
            status = getattr(exc, "status_code", 0)

            # Vision desteklenmiyor → tüm analizi durdur
            if "does not support image" in msg or "image input" in msg or "cannot read" in msg:
                friendly = (
                    f"Seçili model görsel analizi (vision) desteklemiyor: '{model}'\n\n"
                    "Çözüm:\n"
                    "• AI Analiz sayfasında farklı bir model seçin\n"
                    "• Önerilen: google/gemini-2.5-flash\n"
                    "• Ya da: openai/gpt-4o-mini, anthropic/claude-3-haiku"
                )
                logger.error("Vision-unsupported hata (model=%s): %s", model, exc)
                raise OpenRouterError(friendly, status_code=status) from exc

            # 404 Model bulunamadı → tüm analizi durdur
            if status == 404 or "bulunamadı" in msg or "no endpoints" in msg or "not found" in msg:
                friendly = (
                    f"Model bulunamadı veya erişilemiyor: '{model}'\n\n"
                    "Olası nedenler:\n"
                    "• Model ID'si değişmiş veya kaldırılmış\n"
                    "• Hesabınızda bu modele erişim yok\n\n"
                    "Çözüm:\n"
                    "• AI Analiz sayfasında başka bir model seçin\n"
                    "• Önerilen: google/gemini-2.5-flash\n"
                    "• Ayarlar > Model Test'ten erişilebilir modelleri görün"
                )
                logger.error("Model-not-found hata (model=%s): %s", model, exc)
                raise OpenRouterError(friendly, status_code=status) from exc

            # 401 Geçersiz key → durdur
            if status == 401:
                raise

            # Diğer hatalar: loga yaz, dict olarak dön (tek görsel atlanır)
            logger.error("Görsel analiz hatası (%s): %s", image_path, exc)
            return {"error": str(exc), "raw_text": ""}
        except Exception as exc:
            logger.error("Beklenmedik hata (%s): %s", image_path, exc)
            return {"error": str(exc), "raw_text": ""}

    # ── Bölüm Analizi ──────────────────────────────────────────────

    def analyze_chapter(
        self,
        chapter: Chapter,
        model: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        stop_flag: Optional[Callable[[], bool]] = None,
        project=None,
    ) -> Dict[str, Any]:
        """
        Bölümdeki tüm görselleri sırayla analiz eder.

        Args:
            chapter: Analiz edilecek bölüm.
            model: Vision modeli ID.
            progress_callback: (mevcut, toplam, mesaj) çağrılır.
            stop_flag: True dönerse analiz durur.

        Returns:
            {image_index: analiz_dict, ...} formatında sonuçlar.
        """
        # analysis_data'daki tüm key'leri string'e normalize et (integer key uyumsuzluğunu önler)
        results: Dict[str, Any] = {str(k): v for k, v in chapter.analysis_data.items()}
        total = len(chapter.images)
        known = _known_roster_lines(project=project)
        pending: List[int] = []
        done = 0

        for i, image_data in enumerate(chapter.images):
            if stop_flag and stop_flag():
                logger.info("Analiz kullanıcı tarafından durduruldu.")
                return results
            cache_key = str(i)
            if cache_key in results and _is_usable_analysis(results[cache_key]):
                cleaned = _sanitize_analysis_characters(
                    dict(results[cache_key]), known_names=known, project=project,
                )
                results[cache_key] = cleaned
                chapter.analysis_data[cache_key] = cleaned
                done += 1
                msg = f"{done}/{total} önbellekten yüklendi: {image_data.filename}"
                logger.debug(msg)
                if progress_callback:
                    progress_callback(done, total, msg)
            else:
                pending.append(i)

        if done:
            _persist_analysis(project, chapter)
        if not pending:
            return results

        workers = _analysis_workers(self._settings)
        delay = self._settings.get("analysis.rate_limit_delay", RATE_LIMIT_DELAY)
        try:
            delay = max(0.0, float(delay))
        except (TypeError, ValueError):
            delay = RATE_LIMIT_DELAY

        def _one(index: int) -> tuple:
            if stop_flag and stop_flag():
                return index, None
            image_data = chapter.images[index]
            use_model = self._model_for_index(index, total, model)
            result = self.analyze_image(
                image_data.path, use_model, known_names=known, project=project,
            )
            if delay and not (stop_flag and stop_flag()):
                time.sleep(delay)
            return index, result

        fatal: Optional[BaseException] = None
        stopped = False
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            futures = [pool.submit(_one, i) for i in pending]
            for fut in as_completed(futures):
                if stop_flag and stop_flag():
                    stopped = True
                    for other in futures:
                        other.cancel()
                    logger.info("Analiz kullanıcı tarafından durduruldu.")
                try:
                    index, result = fut.result()
                except CancelledError:
                    continue
                except Exception as exc:
                    if _fatal_analysis_error(exc):
                        fatal = exc
                        for other in futures:
                            other.cancel()
                        logger.error("Analiz durdu: %s", exc)
                    else:
                        logger.error("Görsel analiz görevi düştü: %s", exc)
                    continue
                if result is None:
                    continue
                cache_key = str(index)
                results[cache_key] = result
                chapter.analysis_data[cache_key] = result
                _persist_analysis(project, chapter)
                done += 1
                label = ""
                used = str(result.get("_model") or "")
                if used and used != model:
                    label = f" (ekonomi: {used})"
                image_data = chapter.images[index]
                msg = f"{done}/{total} analiz edildi: {image_data.filename}{label}"
                logger.info(msg)
                if progress_callback:
                    progress_callback(done, total, msg)

        if fatal is not None:
            raise fatal
        if stopped:
            logger.info("Analiz durduruldu; tamamlanan kareler kaydedildi.")
        return results

    def _model_for_index(self, index: int, total: int, primary: str) -> str:
        """İlk/son ve her 3. kare birincil; diğerleri ekonomi model."""
        if not self._settings.get("analysis.skip_low_score_fillers", True):
            return primary
        economy = (self._settings.get("api.economy_vision_model", "") or "").strip()
        if not economy or economy == primary:
            return primary
        if total <= 4 or index == 0 or index == total - 1 or index % 3 == 0:
            return primary
        return economy

    # ── Tekrar Analiz ──────────────────────────────────────────────

    def reanalyze_image(
        self,
        chapter: Chapter,
        image_index: int,
        model: str,
        project=None,
    ) -> Dict[str, Any]:
        """
        Belirtilen indeksteki görseli önbelleği yok sayarak yeniden analiz eder.

        Returns:
            Yeni analiz sonucu.
        """
        if image_index < 0 or image_index >= len(chapter.images):
            raise IndexError(f"Geçersiz görsel indeksi: {image_index}")

        image_data = chapter.images[image_index]
        logger.info("Tekrar analiz: %s", image_data.filename)
        known = _known_roster_lines(project=project)
        result = self.analyze_image(
            image_data.path, model, known_names=known, project=project,
        )
        chapter.analysis_data[str(image_index)] = result
        if project is not None:
            from core.character_bible import extract_records_from_chapter, upsert_characters
            upsert_characters(
                project,
                extract_records_from_chapter(chapter, project),
                getattr(chapter, "id", ""),
            )
        return result