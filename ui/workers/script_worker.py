"""
RecapAI - Script üretim worker thread.
"""

import logging
from typing import List

from PyQt6.QtCore import QThread, pyqtSignal

from core.models import Chapter, SegmentData

logger = logging.getLogger(__name__)


class ScriptWorker(QThread):
    """
    Arka planda LLM script üretimi yapan iş parçacığı.

    Sinyaller:
        chunk_received(text): Streaming chunk geldi.
        progress(message): Durum mesajı.
        finished(segments): Tüm segmentler hazır.
        error(message): Hata oluştu.
    """

    chunk_received = pyqtSignal(str)
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        chapter: Chapter,
        model: str,
        api_key: str,
        style: str = "epic",
        length: str = "medium",
        language: str = "tr",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._model = model
        self._api_key = api_key
        self._style = style
        self._length = length
        self._language = language
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from core.openrouter_client import OpenRouterClient, OpenRouterError
        from core.script_generator import ScriptGenerator
        from core.settings_manager import SettingsManager

        try:
            sm = SettingsManager.instance()
            sm.reload()

            api_key = sm.get_api_key()
            if not api_key and self._api_key:
                api_key = self._api_key

            if not api_key:
                self.error.emit(
                    "API anahtarı tanımlı değil.\n"
                    "Ayarlar sayfasından API key girin ve kaydedin."
                )
                return

            client = OpenRouterClient.instance()
            if self._api_key and not sm.has_api_key():
                client.update_api_key(self._api_key)

            generator = ScriptGenerator(client)

            self.progress.emit(f"Script üretiliyor: {self._chapter.name}…")

            collected_chunks: List[str] = []

            def on_chunk(chunk: str) -> None:
                if self._stop:
                    return
                collected_chunks.append(chunk)
                self.chunk_received.emit(chunk)

            segments = generator.generate_script(
                chapter=self._chapter,
                model=self._model,
                style=self._style,
                length=self._length,
                language=self._language,
                stream_callback=on_chunk,
            )

            if not self._stop:
                self.progress.emit(f"{len(segments)} segment üretildi.")
                self.finished.emit(segments)

        except ValueError as exc:
            logger.warning("ScriptWorker ValueError: %s", exc)
            self.error.emit(str(exc))
        except OpenRouterError as exc:
            logger.error("ScriptWorker OpenRouterError: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:
            logger.exception("ScriptWorker beklenmedik hata: %s", exc)
            self.error.emit(f"Beklenmedik hata: {exc}")


class RegenerateSegmentWorker(QThread):
    """Tek bir segmenti yeniden üretir."""

    finished = pyqtSignal(int, object)   # (segment_index, SegmentData)
    error = pyqtSignal(str)

    def __init__(
        self,
        chapter: Chapter,
        segment_index: int,
        model: str,
        api_key: str,
        style: str = "epic",
        language: str = "tr",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._segment_index = segment_index
        self._model = model
        self._api_key = api_key
        self._style = style
        self._language = language

    def run(self) -> None:
        from core.openrouter_client import OpenRouterClient, OpenRouterError
        from core.script_generator import ScriptGenerator
        from core.settings_manager import SettingsManager
        try:
            sm = SettingsManager.instance()
            sm.reload()

            api_key = sm.get_api_key()
            if not api_key and self._api_key:
                api_key = self._api_key

            if not api_key:
                self.error.emit(
                    "API anahtarı tanımlı değil.\n"
                    "Ayarlar sayfasından API key girin ve kaydedin."
                )
                return

            client = OpenRouterClient.instance()
            if self._api_key and not sm.has_api_key():
                client.update_api_key(self._api_key)

            generator = ScriptGenerator(client)
            seg = generator.regenerate_segment(
                self._chapter, self._segment_index,
                self._model, self._style, self._language,
            )
            self.finished.emit(self._segment_index, seg)
        except OpenRouterError as exc:
            logger.error("RegenerateSegmentWorker OpenRouterError: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:
            logger.error("RegenerateSegmentWorker hata: %s", exc)
            self.error.emit(str(exc))