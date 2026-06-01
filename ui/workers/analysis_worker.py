"""
RecapAI - AI analiz worker thread.
SettingsManager üzerinden API key okunur; her zaman güncel.
"""

import logging
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.models import Chapter

logger = logging.getLogger(__name__)


class AnalysisWorker(QThread):
    """
    Arka planda bölüm analizi yapan iş parçacığı.

    Sinyaller:
        progress(current, total, message): İlerleme bildirimi.
        image_analyzed(index, result_dict): Tek görsel tamamlandı.
        finished(): Tüm analiz tamamlandı.
        error(message): Kurtarılamaz hata oluştu.
    """

    progress = pyqtSignal(int, int, str)
    image_analyzed = pyqtSignal(int, dict)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        chapter: Chapter,
        model: str,
        api_key: str = "",   # geriye dönük uyumluluk; artık SettingsManager kullanılır
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._model = model
        self._api_key = api_key   # geriye dönük uyumluluk
        self._stop = False

    def stop(self) -> None:
        """Durdurma bayrağını ayarlar."""
        self._stop = True
        logger.info("AnalysisWorker durdurma sinyali alındı.")

    def run(self) -> None:
        """Thread giriş noktası: bölümü analiz eder."""
        from core.openrouter_client import OpenRouterClient, OpenRouterError
        from core.ai_analyzer import AIAnalyzer
        from core.settings_manager import SettingsManager

        try:
            # KRİTİK: Worker başlarken disk'ten taze yükle
            sm = SettingsManager.instance()
            sm.reload()

            # API key kontrolü
            api_key = sm.get_api_key()
            # Geriye dönük: explicit key verilmişse onu kullan
            if not api_key and self._api_key:
                api_key = self._api_key

            logger.info(
                "AnalysisWorker.run başladı. API key uzunluğu: %d, model: %s",
                len(api_key),
                self._model,
            )

            if not api_key:
                self.error.emit(
                    "API anahtarı tanımlı değil.\n"
                    "Ayarlar sayfasından API key girin ve kaydedin."
                )
                return

            # Singleton client kullan; key SettingsManager'dan okunur
            client = OpenRouterClient.instance()
            # Geriye dönük uyumluluk: explicit key verilmişse client'a bildir
            if self._api_key and not sm.has_api_key():
                client.update_api_key(self._api_key)

            analyzer = AIAnalyzer(client)

            total = len(self._chapter.images)

            for i, image_data in enumerate(self._chapter.images):
                if self._stop:
                    logger.info("Analiz durduruldu (indeks %d).", i)
                    break

                cache_key = str(i)
                cached = self._chapter.analysis_data.get(cache_key)
                if cached and not cached.get("error"):
                    msg = f"{i + 1}/{total} önbellekten: {image_data.filename}"
                    self.progress.emit(i + 1, total, msg)
                    self.image_analyzed.emit(i, cached)
                    continue

                self.progress.emit(
                    i + 1, total,
                    f"{i + 1}/{total} analiz ediliyor: {image_data.filename}",
                )
                result = analyzer.analyze_image(image_data.path, self._model)
                self._chapter.analysis_data[cache_key] = result
                self.image_analyzed.emit(i, result)

                if i < total - 1 and not self._stop:
                    time.sleep(1.0)

        except OpenRouterError as exc:
            logger.error("AnalysisWorker OpenRouterError: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:
            logger.exception("AnalysisWorker beklenmedik hata: %s", exc)
            self.error.emit(f"Beklenmedik hata: {exc}")
        finally:
            self.finished.emit()