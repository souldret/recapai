"""
RecapAI - Render worker (QThread).
"""

import logging
from pathlib import Path
from typing import Any, Dict

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
                out = Path(result)
                if not out.exists() or out.stat().st_size <= 0:
                    self.error.emit(
                        "Render tamamlandı ama çıktı dosyası bulunamadı veya boş.\n"
                        "FFmpeg loglarına (logs/ffmpeg_error_*.log) bakın."
                    )
                    return

                size_mb = out.stat().st_size / 1024 / 1024

                self.log.emit("─" * 50)
                self.log.emit("Render tamamlandı!")
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
        skip_analysis: bool = False,
        skip_script: bool = False,
        skip_tts: bool = False,
        hook_variants: int = 1,
        target_minutes: float | None = None,
        mix_engines: bool = False,
        narrator_engine: str = "kokoro",
        narrator_voice: str = "",
        dialogue_engine: str = "edge-tts",
        dialogue_voice: str = "",
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
        self._skip_analysis = skip_analysis
        self._skip_script = skip_script
        self._skip_tts = skip_tts
        self._hook_variants = max(1, int(hook_variants or 1))
        self._target_minutes = target_minutes
        self._mix_engines = mix_engines
        self._narrator_engine = narrator_engine or "kokoro"
        self._narrator_voice = narrator_voice or ""
        self._dialogue_engine = dialogue_engine or "edge-tts"
        self._dialogue_voice = dialogue_voice or ""
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        chapter = self._chapter

        # ── Aşama 1: Analiz ──────────────────────────────────────
        if self._skip_analysis:
            self.log.emit("  [Analiz] Checkpoint: mevcut analiz kullanılıyor.")
            self._emit_stage_done("Analiz")
            self.overall_progress.emit(25)
        else:
            self._emit_stage("Analiz", 0)
            try:
                from core.openrouter_client import OpenRouterClient
                from core.ai_analyzer import AIAnalyzer

                client = OpenRouterClient.instance()
                from core.settings_manager import SettingsManager
                key = (self._api_key or "").strip() or SettingsManager.instance().get_api_key()
                if key:
                    client.update_api_key(key)
                analyzer = AIAnalyzer(client)

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
                    project=self._project,
                )
            except Exception as exc:
                self.error.emit(f"Analiz hatası: {exc}")
                return

            if self._cancelled:
                self.error.emit("Pipeline iptal edildi (Analiz sonrası).")
                return
            self._emit_stage_done("Analiz")
            self.overall_progress.emit(25)

        try:
            from core.pipeline import require_character_bible
            n_bible = require_character_bible(self._project, chapter)
            self.log.emit(f"  [Bible] {n_bible} karakter kaydı hazır.")
        except Exception as bible_exc:
            if not self._skip_script:
                self.error.emit(f"Karakter bible: {bible_exc}")
                return
            self.log.emit(f"  [Bible] atlandı: {bible_exc}")

        # ── Aşama 2: Script ──────────────────────────────────────
        if self._skip_script:
            self.log.emit("  [Script] Checkpoint: mevcut script kullanılıyor.")
            self._emit_stage_done("Script")
            self.overall_progress.emit(50)
        else:
            self._emit_stage("Script", 25)
            try:
                from core.script_generator import ScriptGenerator
                from core.openrouter_client import OpenRouterClient
                from core.settings_manager import SettingsManager

                sm = SettingsManager.instance()
                sm.reload()
                client = OpenRouterClient.instance()
                key = sm.get_api_key() or (self._api_key or "").strip()
                if key:
                    client.update_api_key(key)

                generator = ScriptGenerator(client)
                segments = generator.generate_script(
                    chapter,
                    self._script_model,
                    style=self._script_style or "fresh",
                    length=self._script_length,
                    language=self._script_language,
                    niche="auto",
                    use_hook=True,
                    stream_callback=lambda chunk: self.log.emit(chunk),
                    project=self._project,
                    auto_niche=True,
                    include_last_time=None,
                    target_minutes=self._target_minutes,
                )
                if self._hook_variants > 1:
                    from core.script_quality import cold_open_text, store_hook_variants
                    primary = cold_open_text(segments)
                    try:
                        alt_hook = generator.generate_alt_hook(
                            chapter,
                            self._script_model,
                            style=self._script_style or "fresh",
                            language=self._script_language,
                            niche="auto",
                            project=self._project,
                            primary=primary,
                        )
                    except Exception as exc:
                        self.log.emit(f"  [Script] A/B kanca atlandı: {exc}")
                        alt_hook = ""
                    variants = []
                    if primary:
                        variants.append({"id": "A", "text": primary})
                    if alt_hook and alt_hook != primary:
                        variants.append({"id": "B", "text": alt_hook})
                    if variants:
                        store_hook_variants(chapter, variants, selected="A")
                        self.log.emit("  [Script] A/B kanca meta olarak kaydedildi.")
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
        if self._skip_tts:
            self.log.emit("  [TTS] Checkpoint: mevcut sesler kullanılıyor.")
            self._emit_stage_done("Seslendirme")
            self.overall_progress.emit(75)
        else:
            self._emit_stage("Seslendirme", 50)
            try:
                from core.tts_engine import TTSManager
                from core.tts_cache import TTSCache
                from core.audio_processor import get_duration as _get_dur, remove_silence

                audio_dir = Path(self._audio_dir)
                audio_dir.mkdir(parents=True, exist_ok=True)

                cache = TTSCache.get_global_cache()
                manager = TTSManager.get_instance()
                mix = bool(self._mix_engines)
                try:
                    from core.settings_manager import SettingsManager
                    tts_cfg = SettingsManager.instance().get("tts", {}) or {}
                    if not mix:
                        mix = bool(tts_cfg.get("mix_engines", False))
                    if mix:
                        self._narrator_engine = tts_cfg.get("narrator_engine") or self._narrator_engine
                        self._narrator_voice = tts_cfg.get("narrator_voice") or self._narrator_voice
                        self._dialogue_engine = tts_cfg.get("dialogue_engine") or self._dialogue_engine
                        self._dialogue_voice = tts_cfg.get("dialogue_voice") or self._dialogue_voice
                except Exception:
                    pass
                if mix:
                    self.log.emit("  [TTS] Karışık motor: anlatıcı Kokoro, diyalog Edge.")

                from ui.workers.tts_worker import engine_for_segment, voice_for_engine

                silence_cfg = None
                try:
                    from core.settings_manager import SettingsManager
                    tts_cfg = SettingsManager.instance().get("tts", {}) or {}
                    if tts_cfg.get("remove_silence", True):
                        silence_cfg = {
                            "silence_thresh": float(tts_cfg.get("silence_thresh_db", -42.0)),
                            "min_silence_len": int(tts_cfg.get("min_silence_len_ms", 700)),
                            "keep_silence": int(tts_cfg.get("keep_silence_ms", 220)),
                        }
                except Exception:
                    silence_cfg = None

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
                    engine_name = engine_for_segment(
                        seg,
                        mix=mix,
                        default_engine=self._tts_engine,
                        narrator_engine=self._narrator_engine,
                        dialogue_engine=self._dialogue_engine,
                    )
                    voice = voice_for_engine(
                        engine_name,
                        mix=mix,
                        default_engine=self._tts_engine,
                        default_voice=self._tts_voice,
                        narrator_engine=self._narrator_engine,
                        narrator_voice=self._narrator_voice,
                        dialogue_engine=self._dialogue_engine,
                        dialogue_voice=self._dialogue_voice,
                    )
                    engine = manager.get_engine(engine_name)

                    try:
                        from core.settings_manager import SettingsManager
                        default_speed = float(
                            SettingsManager.instance().get("tts.default_speed", 1.0)
                        )
                    except Exception:
                        default_speed = 1.0

                    synth_params = {"engine": engine_name}
                    if engine_name == "kokoro":
                        synth_params["speed"] = default_speed
                    elif engine_name == "edge-tts":
                        rate_pct = int(round((default_speed - 1.0) * 100))
                        rate_pct = max(-50, min(50, rate_pct))
                        synth_params["rate"] = f"{rate_pct:+d}%"

                    cache_key = cache.get_cache_key(seg.text, voice, synth_params)
                    cached = cache.get(cache_key)
                    if cached and Path(cached).exists():
                        seg.audio_path = cached
                        seg.duration = _get_dur(cached)
                        self.log.emit(f"  [TTS] Segment {i+1}: cache'den ({engine_name})")
                        continue

                    try:
                        call_kwargs = {
                            k: v for k, v in synth_params.items() if k != "engine"
                        }
                        duration = engine.synthesize(
                            text=seg.text,
                            voice=voice,
                            output_path=out_path,
                            **call_kwargs,
                        )
                        if Path(out_path).exists():
                            if silence_cfg:
                                try:
                                    duration = remove_silence(out_path, **silence_cfg)
                                except Exception as sil_exc:
                                    self.log.emit(f"  [TTS] Segment {i+1} sessizlik kırpılamadı: {sil_exc}")
                            cache.put(cache_key, out_path)
                            seg.audio_path = out_path
                            seg.duration = duration
                            self.log.emit(f"  [TTS] Segment {i+1}: Tamamlandı")
                    except Exception as seg_exc:
                        logger.error("PipelineWorker TTS segment %d hatası: %s", i, seg_exc)
                        self.log.emit(f"  [TTS] Segment {i+1} HATA: {seg_exc}")

                voiced = sum(
                    1 for seg in chapter.segments
                    if seg.audio_path and Path(seg.audio_path).exists()
                )
                if voiced == 0 and any(seg.text.strip() for seg in chapter.segments):
                    self.error.emit("TTS hatası: hiçbir segment seslendirilemedi.")
                    return

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

            composer.compose_chapter(
                chapter,
                self._render_output,
                progress_callback=render_progress,
                cancel_check=lambda: self._cancelled,
            )

        except Exception as exc:
            self.error.emit(f"Render hatası: {exc}")
            return

        if self._cancelled:
            self.error.emit("Pipeline iptal edildi (Render).")
            return

        try:
            from core.pipeline import export_youtube_thumbnail
            thumb = export_youtube_thumbnail(chapter, self._render_output)
            if thumb:
                self.log.emit(f"  [Kapak] {thumb}")
        except Exception as thumb_exc:
            self.log.emit(f"  [Kapak] yazılamadı: {thumb_exc}")

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