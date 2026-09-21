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

            total = len(self._chapter.images)

            for i, image_data in enumerate(self._chapter.images):
                if self._stop:
                    logger.info("Analiz durduruldu (indeks %d).", i)
                    break

                cache_key = str(i)
                cached = self._chapter.analysis_data.get(cache_key)
                if cached and not cached.get("error"):
                    from core.ai_analyzer import _known_roster_lines, _sanitize_analysis_characters
                    known = _known_roster_lines(project=self._project)
                    cleaned = _sanitize_analysis_characters(
                        dict(cached), known_names=known, project=self._project,
                    )
                    self._chapter.analysis_data[cache_key] = cleaned
                    if self._project is not None:
                        from core.character_bible import extract_records_from_chapter, upsert_characters
                        upsert_characters(
                            self._project,
                            extract_records_from_chapter(self._chapter, self._project),
                            getattr(self._chapter, "id", ""),
                        )
                    msg = f"{i + 1}/{total} önbellekten: {image_data.filename}"
                    self.progress.emit(i + 1, total, msg)
                    self.image_analyzed.emit(i, cleaned)
                    continue

                self.progress.emit(
                    i + 1, total,
                    f"{i + 1}/{total} analiz ediliyor: {image_data.filename}",
                )
                from core.ai_analyzer import _known_roster_lines
                known = _known_roster_lines(project=self._project)
                result = analyzer.analyze_image(
                    image_data.path, self._model, known_names=known, project=self._project,
                )
                self._chapter.analysis_data[cache_key] = result
                if self._project is not None:
                    from core.character_bible import extract_records_from_chapter, upsert_characters
                    upsert_characters(
                        self._project,
                        extract_records_from_chapter(self._chapter, self._project),
                        getattr(self._chapter, "id", ""),
                    )
                self.image_analyzed.emit(i, result)

                if i < total - 1 and not self._stop:
                    time.sleep(1.0)

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