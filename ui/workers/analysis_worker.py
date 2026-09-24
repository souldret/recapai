"""
RecapAI - AI analiz worker thread.
SettingsManager üzerinden API key okunur; her zaman güncel.
"""

import logging
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
        project=None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._model = model
        self._api_key = api_key   # geriye dönük uyumluluk
        self._stop = False
        self._project = project

    def stop(self) -> None:
        """Durdurma bayrağını ayarlar."""
        self._stop = True
        logger.info("AnalysisWorker durdurma sinyali alındı.")

    def run(self) -> None:
        """Thread giriş noktası: bölümü analiz eder."""
        from core.openrouter_client import OpenRouterClient, OpenRouterError
        from core.ai_analyzer import AIAnalyzer
        from core.settings_manager import SettingsManager

        failed = False
        try:
            # KRİTİK: Worker başlarken disk'ten taze yükle
            sm = SettingsManager.instance()
            sm.reload()

            # API key kontrolü
            api_key = sm.get_api_key() or (self._api_key or "").strip()

            logger.info(
                "AnalysisWorker.run başladı. API key uzunluğu: %d, model: %s",
                len(api_key),
                self._model,
            )

            if not api_key:
                failed = True
                self.error.emit(
                    "API anahtarı tanımlı değil.\n"
                    "Ayarlar > API sekmesine gerçek OpenRouter anahtarını yazıp Kaydet'e basın.\n"
                    ".env içindeki YOUR_..._HERE örneği geçerli değildir."
                )
                return

            client = OpenRouterClient.instance()
            client.update_api_key(api_key)

            analyzer = AIAnalyzer(client)
            seen = set()

            def _progress(done: int, total: int, message: str) -> None:
                self.progress.emit(done, total, message)
                data = getattr(self._chapter, "analysis_data", None) or {}
                for key, result in list(data.items()):
                    if not str(key).isdigit() or key in seen or not isinstance(result, dict):
                        continue
                    seen.add(key)
                    self.image_analyzed.emit(int(key), result)

            analyzer.analyze_chapter(
                self._chapter,
                self._model,
                progress_callback=_progress,
                stop_flag=lambda: self._stop,
                project=self._project,
            )

        except OpenRouterError as exc:
            failed = True
            logger.error("AnalysisWorker OpenRouterError: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:
            failed = True
            logger.exception("AnalysisWorker beklenmedik hata: %s", exc)
            self.error.emit(f"Beklenmedik hata: {exc}")
        finally:
            # error zaten butonları açar; finished+error birlikte "BAŞARILI" loguna yol açmasın
            if not failed:
                self.finished.emit()