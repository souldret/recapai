"""
RecapAI - Script üretim worker thread.
"""

import logging
from typing import List, Optional

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
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        chapter: Chapter,
        model: str,
        api_key: str,
        style: str = "fresh",
        length: str = "medium",
        language: str = "tr",
        niche: str = "power_fantasy",
        use_hook: Optional[bool] = None,
        parent=None,
        project=None,
        target_minutes: Optional[float] = None,
        auto_niche: bool = False,
        include_last_time: Optional[bool] = None,
        compile_chapters=None,
        hook_variants: int = 1,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._model = model
        self._api_key = api_key
        self._style = style
        self._length = length
        self._language = language
        self._niche = niche
        self._use_hook = use_hook
        self._project = project
        self._target_minutes = target_minutes
        self._auto_niche = auto_niche
        self._include_last_time = include_last_time
        self._compile_chapters = compile_chapters
        self._hook_variants = max(1, int(hook_variants or 1))
        self._pending_hooks = []
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

            api_key = sm.get_api_key() or (self._api_key or "").strip()

            if not api_key:
                self.error.emit(
                    "API anahtarı tanımlı değil.\n"
                    "Ayarlar > API sekmesine gerçek OpenRouter anahtarını yazıp Kaydet'e basın.\n"
                    ".env içindeki YOUR_..._HERE örneği geçerli değildir."
                )
                return

            client = OpenRouterClient.instance()
            client.update_api_key(api_key)

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
                niche=self._niche,
                use_hook=self._use_hook,
                stream_callback=on_chunk,
                project=self._project,
                target_minutes=self._target_minutes,
                auto_niche=self._auto_niche,
                include_last_time=self._include_last_time,
                compile_chapters=self._compile_chapters,
                stop_flag=lambda: self._stop,
            )
            if self._hook_variants > 1 and not self._stop:
                from core.script_quality import cold_open_text, store_hook_variants
                primary = cold_open_text(segments)
                try:
                    alt_hook = generator.generate_alt_hook(
                        chapter=self._chapter,
                        model=self._model,
                        style=self._style,
                        language=self._language,
                        niche=self._niche,
                        project=self._project,
                        primary=primary,
                        stop_flag=lambda: self._stop,
                    )
                except Exception as exc:
                    logger.warning("A/B kanca üretilemedi, ana script korunuyor: %s", exc)
                    alt_hook = ""
                variants = []
                if primary:
                    variants.append({"id": "A", "text": primary})
                if alt_hook and alt_hook != primary:
                    variants.append({"id": "B", "text": alt_hook})
                if variants:
                    if self._compile_chapters:
                        self._pending_hooks = variants
                    else:
                        store_hook_variants(self._chapter, variants, selected="A")
                    self.progress.emit("A/B kanca meta olarak kaydedildi (VO'ya gömülmedi).")

            if self._stop:
                logger.info("ScriptWorker durduruldu.")
                self.cancelled.emit()
            else:
                self.progress.emit(f"{len(segments)} segment üretildi.")
                self.finished.emit(segments)

        except ValueError as exc:
            if self._stop or "durduruldu" in str(exc).lower():
                logger.info("ScriptWorker durduruldu.")
                self.cancelled.emit()
                return
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
        style: str = "fresh",
        language: str = "tr",
        length: str = "medium",
        niche: str = "power_fantasy",
        parent=None,
        project=None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._segment_index = segment_index
        self._model = model
        self._api_key = api_key
        self._style = style
        self._language = language
        self._length = length
        self._niche = niche
        self._project = project
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

            api_key = sm.get_api_key() or (self._api_key or "").strip()

            if not api_key:
                self.error.emit(
                    "API anahtarı tanımlı değil.\n"
                    "Ayarlar > API sekmesine gerçek OpenRouter anahtarını yazıp Kaydet'e basın."
                )
                return

            client = OpenRouterClient.instance()
            client.update_api_key(api_key)

            generator = ScriptGenerator(client)
            if self._stop:
                return
            seg = generator.regenerate_segment(
                self._chapter, self._segment_index,
                self._model, self._style, self._language, self._length,
                niche=self._niche, project=self._project,
            )
            if self._stop:
                return
            self.finished.emit(self._segment_index, seg)
        except OpenRouterError as exc:
            logger.error("RegenerateSegmentWorker OpenRouterError: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:
            logger.error("RegenerateSegmentWorker hata: %s", exc)
            self.error.emit(str(exc))