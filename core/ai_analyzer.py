"""
RecapAI - AI analiz motoru.
"""

import json
import logging
import re
import time
from typing import Any, Callable, Dict, Optional

from core.models import Chapter
from core.openrouter_client import OpenRouterClient, OpenRouterError
from core.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

RATE_LIMIT_DELAY = 1.0   # saniye (istek arası bekleme)


def _load_vision_prompt() -> str:
    """prompts.json'dan vision analiz promptunu yükler."""
    from pathlib import Path
    try:
        data = json.loads(Path("config/prompts.json").read_text(encoding="utf-8"))
        return data.get("vision_analysis", "Bu görseli analiz et ve JSON formatında döndür.")
    except Exception as exc:
        logger.warning("prompts.json yüklenemedi: %s", exc)
        return "Bu görseli analiz et ve JSON formatında döndür."


def _parse_json_response(text: str) -> Dict:
    """
    Model çıktısından JSON bloğunu ayrıştırır.
    Kod bloğu içinde olabilir: ```json ... ```
    """
    # Önce doğrudan parse dene
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Markdown kod bloğunu çıkar
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # İlk { ... } bloğunu bul
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("JSON ayrıştırılamadı, ham metin döndürülüyor.")
    return {"raw_text": text, "parse_error": True}


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

        effective_prompt = prompt or _load_vision_prompt()
        logger.debug(
            "analyze_image: model=%s api_key_len=%d path=%s",
            model,
            len(self._settings.get_api_key()),
            image_path,
        )
        try:
            result = self._client.vision_analyze(model, image_path, effective_prompt)
            parsed = _parse_json_response(result["content"])
            parsed["_model"] = result.get("model", model)
            parsed["_usage"] = result.get("usage", {})
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
                    "• Önerilen: google/gemini-2.5-flash-preview\n"
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
                    "• Önerilen: google/gemini-2.5-flash-preview\n"
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
        results: Dict[str, Any] = dict(chapter.analysis_data)  # önbellekten başla
        total = len(chapter.images)

        for i, image_data in enumerate(chapter.images):
            if stop_flag and stop_flag():
                logger.info("Analiz kullanıcı tarafından durduruldu.")
                break

            # Önbellekte varsa atla
            cache_key = str(i)
            if cache_key in results and not results[cache_key].get("error"):
                msg = f"{i + 1}/{total} önbellekten yüklendi: {image_data.filename}"
                logger.debug(msg)
                if progress_callback:
                    progress_callback(i + 1, total, msg)
                continue

            msg = f"{i + 1}/{total} analiz ediliyor: {image_data.filename}"
            logger.info(msg)
            if progress_callback:
                progress_callback(i + 1, total, msg)

            result = self.analyze_image(image_data.path, model)
            results[cache_key] = result

            # chapter.analysis_data'ya hemen kaydet
            chapter.analysis_data[cache_key] = result

            # Rate limit: istekler arası bekleme
            if i < total - 1 and not (stop_flag and stop_flag()):
                time.sleep(RATE_LIMIT_DELAY)

        return results

    # ── Tekrar Analiz ──────────────────────────────────────────────

    def reanalyze_image(
        self,
        chapter: Chapter,
        image_index: int,
        model: str,
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
        result = self.analyze_image(image_data.path, model)
        chapter.analysis_data[str(image_index)] = result
        return result