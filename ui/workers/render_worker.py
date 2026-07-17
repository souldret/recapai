"""
RecapAI - Render worker (QThread).
"""

import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class RenderWorker(QThread):
    """
    Video render işlemini ayrı bir thread'de çalıştırır.

    Signals:
        progress(int, str): İlerleme yüzdesi + ETA metni
        log(str):           Log satırı (UI log alanına yazdırılır)
        finished(str):      Başarıyla tamamlandı → çıktı dosyası yolu
        error(str):         Hata mesajı
    """

    progress = pyqtSignal(int, str)   # percent, eta
    log = pyqtSignal(str)             # log line
    finished = pyqtSignal(str)        # output_path
    error = pyqtSignal(str)           # error message

    def __init__(
        self,
        chapter,
        settings: Dict[str, Any],
        output_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._settings = settings
        self._output_path = output_path
        self._cancelled = False

    def cancel(self) -> None:
        """Render iptalini işaretler."""
        self._cancelled = True
        self.log.emit("İptal sinyali gönderildi...")
        logger.info("RenderWorker: iptal istendi.")

    def run(self) -> None:
        """Render işlemini çalıştırır."""
        from core.video_composer import VideoComposer
        from core.ffmpeg_helper import check_ffmpeg

        chapter = self._chapter
        settings = self._settings
        output_path = self._output_path

        # FFmpeg kontrolü
        if not check_ffmpeg():
            msg = (
                "FFmpeg bulunamadı!\n\n"
                "FFmpeg'i yükleyip sistem PATH'ine ekleyin:\n"
                "  Windows: https://www.gyan.dev/ffmpeg/builds/\n"
                "  macOS:   brew install ffmpeg\n"
                "  Linux:   sudo apt install ffmpeg"
            )
            self.error.emit(msg)
            return

        self.log.emit(f"Render başlıyor: {chapter.name}")
        self.log.emit(f"Çıktı: {output_path}")
        self.log.emit(f"Çözünürlük: {settings.get('resolution', [1920, 1080])}")
        self.log.emit(f"FPS: {settings.get('fps', 30)}")
        self.log.emit(f"Codec: {settings.get('codec', 'libx264')}")
        self.log.emit("─" * 50)

        def progress_cb(percent: int, eta: str) -> None:
            self.progress.emit(percent, eta)
            if percent % 10 == 0:
                self.log.emit(f"  → %{percent} tamamlandı  ETA: {eta}")

        def cancel_check() -> bool:
            return self._cancelled

        try:
            composer = VideoComposer(settings)
            result = composer.compose_chapter(
                chapter,
                output_path,
                progress_callback=progress_cb,
                cancel_check=cancel_check,
            )

            if not self._cancelled:
                # Eksik segmentleri raporla
                missing = [
                    i for i, seg in enumerate(chapter.segments)
                    if not seg.audio_path or not Path(seg.audio_path).exists()
                ]
                if missing:
                    self.log.emit(
                        f"<span style='color:#f59e0b;'><b>UYARI:</b> "
                        f"{len(missing)} segment ses dosyası bulunamadı "
                        f"(index: {', '.join(str(x) for x in missing[:5])}"
                        f"{'...' if len(missing) > 5 else ''}). "
                        f"Bu segmentler sessiz render edildi.</span>"
                    )

            if self._cancelled:
                self.log.emit("Render iptal edildi.")
                self.error.emit("Render kullanıcı tarafından iptal edildi.")
            else:
                size_mb = 0.0
                p = Path(result)
                if p.exists():
                    size_mb = p.stat().st_size / 1024 / 1024

                self.log.emit("─" * 50)
                self.log.emit(f"Render tamamlandı!")
                self.log.emit(f"Dosya boyutu: {size_mb:.1f} MB")
                self.log.emit(f"Konum: {result}")
                self.progress.emit(100, "0s")
                self.finished.emit(result)

        except Exception as exc:
            logger.error("RenderWorker hata: %s", exc, exc_info=True)
            self.log.emit(f"HATA: {exc}")
            self.error.emit(str(exc))


class PipelineWorker(QThread):
    """
    Tek tıkla pipeline: Analiz → Script → TTS → Render sırasıyla çalıştırır.

    Signals:
        stage_started(str):       Aşama adı başladı
        stage_progress(int, str): Aktif aşama ilerleme
        stage_finished(str):      Aşama tamamlandı
        overall_progress(int):    Genel ilerleme (0-100)
        log(str):                 Log satırı
        finished(str):            Pipeline tamamlandı → render çıktı yolu
        error(str):               Hata mesajı (hangi aşamada olursa)
    """

    stage_started = pyqtSignal(str)
    stage_progress = pyqtSignal(int, str)
    stage_finished = pyqtSignal(str)
    overall_progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    STAGES = [
        ("Analiz",       0,  25),
        ("Script",      25,  50),
        ("Seslendirme", 50,  75),
        ("Render",      75, 100),
    ]

    def __init__(
        self,
        project,
        chapter,
        render_settings: Dict[str, Any],
        render_output: str,
        api_key: str,
        vision_model: str,
        script_model: str,
        script_style: str,
        script_length: str,
        script_language: str,
        tts_engine: str,
        tts_voice: str,
        audio_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._chapter = chapter
        self._render_settings = render_settings
        self._render_output = render_output
        self._api_key = api_key
        self._vision_model = vision_model
        self._script_model = script_model
        self._script_style = script_style
        self._script_length = script_length
        self._script_language = script_language
        self._tts_engine = tts_engine
        self._tts_voice = tts_voice
        self._audio_dir = audio_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        chapter = self._chapter

        # ── Aşama 1: Analiz ──────────────────────────────────────
        self._emit_stage("Analiz", 0)
        try:
            from core.openrouter_client import OpenRouterClient
            from core.ai_analyzer import AIAnalyzer

            client = OpenRouterClient.instance()
            if self._api_key:
                client.update_api_key(self._api_key)
            analyzer = AIAnalyzer(client)

            total_imgs = len(chapter.images)

            def analysis_progress(done: int, total: int, msg: str) -> None:
                pct = int(done / max(total, 1) * 100)
                self.stage_progress.emit(pct, msg)
                self.overall_progress.emit(int(0 + pct * 0.25))
                self.log.emit(f"  [Analiz] {msg}")

            analyzer.analyze_chapter(
                chapter,
                self._vision_model,
                progress_callback=analysis_progress,
                stop_flag=lambda: self._cancelled,
            )
        except Exception as exc:
            self.error.emit(f"Analiz hatası: {exc}")
            return

        if self._cancelled:
            self.error.emit("Pipeline iptal edildi (Analiz sonrası).")
            return
        self._emit_stage_done("Analiz")
        self.overall_progress.emit(25)

        # ── Aşama 2: Script ──────────────────────────────────────
        self._emit_stage("Script", 25)
        try:
            from core.script_generator import ScriptGenerator
            from core.openrouter_client import OpenRouterClient
            from core.settings_manager import SettingsManager

            sm = SettingsManager.instance()
            sm.reload()
            client = OpenRouterClient.instance()
            if self._api_key and not sm.has_api_key():
                client.update_api_key(self._api_key)

            generator = ScriptGenerator(client)
            segments = generator.generate_script(
                chapter,
                self._script_model,
                style=self._script_style or "fresh",
                length=self._script_length,
                language=self._script_language,
                niche="power_fantasy",
                use_hook=None,  # ilk bölüm sezgisi
                stream_callback=lambda chunk: self.log.emit(chunk),
            )
            chapter.segments = segments
            self.log.emit(f"  [Script] {len(segments)} segment oluşturuldu.")
        except Exception as exc:
            self.error.emit(f"Script hatası: {exc}")
            return

        if self._cancelled:
            self.error.emit("Pipeline iptal edildi (Script sonrası).")
            return
        self._emit_stage_done("Script")
        self.overall_progress.emit(50)

        # ── Aşama 3: TTS ─────────────────────────────────────────
        self._emit_stage("Seslendirme", 50)
        try:
            from core.tts_engine import TTSManager
            from core.tts_cache import TTSCache
            from core.audio_processor import get_duration as _get_dur

            audio_dir = Path(self._audio_dir)
            audio_dir.mkdir(parents=True, exist_ok=True)

            # Global cache kullan (settings'te açıksa), yoksa proje-yerel cache
            cache = TTSCache.get_global_cache()

            manager = TTSManager.get_instance()
            engine = manager.get_engine(self._tts_engine)

            total_segs = len(chapter.segments)
            for i, seg in enumerate(chapter.segments):
                if self._cancelled:
                    break
                if not seg.text.strip():
                    continue

                pct_seg = int(i / max(total_segs, 1) * 100)
                self.stage_progress.emit(pct_seg, f"Segment {i+1}/{total_segs}")
                self.overall_progress.emit(int(50 + pct_seg * 0.25))

                out_path = str(audio_dir / f"segment_{i:04d}.mp3")

                # Cache kontrolü — TTSCache.get_cache_key() ile tutarlı key
                tts_params = {"engine": self._tts_engine}
                cache_key = cache.get_cache_key(seg.text, self._tts_voice, tts_params)
                cached = cache.get(cache_key)
                if cached and Path(cached).exists():
                    seg.audio_path = cached
                    seg.duration = _get_dur(cached)
                    self.log.emit(f"  [TTS] Segment {i+1}: cache'den")
                    continue

                try:
                    duration = engine.synthesize(
                        text=seg.text,
                        voice=self._tts_voice,
                        output_path=out_path,
                    )
                    if Path(out_path).exists():
                        cache.put(cache_key, out_path)
                        seg.audio_path = out_path
                        seg.duration = duration
                        self.log.emit(f"  [TTS] Segment {i+1}: Tamamlandı")
                except Exception as seg_exc:
                    logger.error("PipelineWorker TTS segment %d hatası: %s", i, seg_exc)
                    self.log.emit(f"  [TTS] Segment {i+1} HATA: {seg_exc}")

        except Exception as exc:
            self.error.emit(f"TTS hatası: {exc}")
            return

        if self._cancelled:
            self.error.emit("Pipeline iptal edildi (TTS sonrası).")
            return
        self._emit_stage_done("Seslendirme")
        self.overall_progress.emit(75)

        # ── Aşama 4: Render ──────────────────────────────────────
        self._emit_stage("Render", 75)
        try:
            from core.video_composer import VideoComposer
            from core.ffmpeg_helper import check_ffmpeg

            if not check_ffmpeg():
                self.error.emit("FFmpeg bulunamadı! Lütfen yükleyin ve PATH'e ekleyin.")
                return

            composer = VideoComposer(self._render_settings)

            def render_progress(pct: int, eta: str) -> None:
                self.stage_progress.emit(pct, eta)
                self.overall_progress.emit(int(75 + pct * 0.25))
                if pct % 10 == 0:
                    self.log.emit(f"  [Render] %{pct}  ETA: {eta}")

            result = composer.compose_chapter(
                chapter,
                self._render_output,
                progress_callback=render_progress,
                cancel_check=lambda: self._cancelled,
            )

        except Exception as exc:
            self.error.emit(f"Render hatası: {exc}")
            return

        self._emit_stage_done("Render")
        self.overall_progress.emit(100)
        self.log.emit("Pipeline tamamlandı!")
        self.finished.emit(self._render_output)

    # ── Helpers ───────────────────────────────────────────────────

    def _emit_stage(self, name: str, base_pct: int) -> None:
        self.stage_started.emit(name)
        self.log.emit(f"\n{'='*40}")
        self.log.emit(f"  ▶ {name} aşaması başladı")
        self.log.emit(f"{'='*40}")

    def _emit_stage_done(self, name: str) -> None:
        self.stage_finished.emit(name)
        self.log.emit(f"  {name} tamamlandı")