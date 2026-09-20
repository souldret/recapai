"""
RecapAI - TTS arka plan worker'ı.
Edge-TTS için paralel (asyncio, max 3), Kokoro için sıralı çalışır.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

# Edge-TTS için maksimum eşzamanlı görev sayısı
EDGE_MAX_CONCURRENT = 3

NARRATOR_ROLES = {
    "cold_open", "last_time", "setup", "rehook",
    "cliffhanger", "filler", "beat", "narrator", "",
}


def engine_for_segment(
    seg,
    *,
    mix: bool,
    default_engine: str,
    narrator_engine: str = "kokoro",
    dialogue_engine: str = "edge-tts",
) -> str:
    if not mix:
        return default_engine
    role = (getattr(seg, "role", "") or "").lower()
    if role in NARRATOR_ROLES:
        return narrator_engine or "kokoro"
    return dialogue_engine or "edge-tts"


def voice_for_engine(
    engine_name: str,
    *,
    mix: bool,
    default_engine: str,
    default_voice: str,
    narrator_engine: str = "kokoro",
    narrator_voice: str = "",
    dialogue_engine: str = "edge-tts",
    dialogue_voice: str = "",
) -> str:
    if mix:
        if engine_name == (narrator_engine or "kokoro") and narrator_voice:
            return narrator_voice
        if engine_name == (dialogue_engine or "edge-tts") and dialogue_voice:
            return dialogue_voice
    if engine_name == default_engine:
        return default_voice
    return dialogue_voice or narrator_voice or default_voice


class TTSWorker(QThread):
    """
    Bir chapter'ın tüm segmentlerini arka planda seslendirir.

    Sinyaller:
        segment_started(index)                   — segment başladı
        segment_done(index, audio_path, duration) — segment bitti
        progress(current, total, message)         — ilerleme
        finished()                                — tüm segmentler bitti
        error(index, message)                     — segment hatası
    """

    segment_started = pyqtSignal(int)
    segment_done    = pyqtSignal(int, str, float)   # index, path, duration
    progress        = pyqtSignal(int, int, str)      # current, total, msg
    finished        = pyqtSignal()
    error           = pyqtSignal(int, str)

    def __init__(
        self,
        chapter,
        engine_name: str,
        voice: str,
        audio_dir: str,
        params: Optional[Dict[str, Any]] = None,
        cache=None,
        parent=None,
        only_indices: Optional[List[int]] = None,
        mix_engines: bool = False,
        narrator_engine: str = "kokoro",
        narrator_voice: str = "",
        dialogue_engine: str = "edge-tts",
        dialogue_voice: str = "",
    ) -> None:
        """
        Args:
            chapter:     core.models.Chapter nesnesi.
            engine_name: "edge-tts" veya "kokoro".
            voice:       Ses ID'si.
            audio_dir:   Segment ses dosyalarının kaydedileceği dizin.
            params:      Engine'e özgü parametreler (rate, pitch, speed vb.).
            cache:       TTSCache örneği (None ise kullanılmaz).
            only_indices: Sadece bu segment indekslerini sentezle (None = hepsi).
        """
        super().__init__(parent)
        self.chapter     = chapter
        self.engine_name = engine_name
        self.voice       = voice
        self.audio_dir   = Path(audio_dir)
        self.params      = params or {}
        self.cache       = cache
        self._only_indices = set(only_indices) if only_indices is not None else None
        self._mix_engines = mix_engines
        self._narrator_engine = narrator_engine or "kokoro"
        self._narrator_voice = narrator_voice or ""
        self._dialogue_engine = dialogue_engine or "edge-tts"
        self._dialogue_voice = dialogue_voice or ""
        self._stop_flag  = False
        self._silence_cfg = self._load_silence_settings()

    def _engine_for_segment(self, seg) -> str:
        return engine_for_segment(
            seg,
            mix=self._mix_engines,
            default_engine=self.engine_name,
            narrator_engine=self._narrator_engine,
            dialogue_engine=self._dialogue_engine,
        )

    def _voice_for_engine(self, engine_name: str) -> str:
        return voice_for_engine(
            engine_name,
            mix=self._mix_engines,
            default_engine=self.engine_name,
            default_voice=self.voice,
            narrator_engine=self._narrator_engine,
            narrator_voice=self._narrator_voice,
            dialogue_engine=self._dialogue_engine,
            dialogue_voice=self._dialogue_voice,
        )

    @staticmethod
    def _load_silence_settings() -> Optional[Dict[str, Any]]:
        """Ayarlardan silence-remover yapılandırmasını okur (kapalıysa None)."""
        try:
            from core.settings_manager import SettingsManager
            sm = SettingsManager.instance()
            tts_cfg = sm.get("tts", {}) or {}
            if not tts_cfg.get("remove_silence", False):
                return None
            return {
                "silence_thresh": float(tts_cfg.get("silence_thresh_db", -42.0)),
                "min_silence_len": int(tts_cfg.get("min_silence_len_ms", 700)),
                "keep_silence": int(tts_cfg.get("keep_silence_ms", 220)),
            }
        except Exception:
            return None

    def _apply_silence_removal(self, out_path: str, duration: float) -> float:
        """Ayar açıksa sessizlik kaldırma uygular ve güncel süreyi döner."""
        if not self._silence_cfg:
            return duration
        try:
            from core.audio_processor import remove_silence
            return remove_silence(out_path, **self._silence_cfg)
        except Exception as exc:
            logger.warning("Silence-remover uygulanamadı (%s): %s", out_path, exc)
            return duration

    # ── Stop ──────────────────────────────────────────────────────

    def stop(self) -> None:
        """Worker'ı iptal bayrağıyla durdurur."""
        self._stop_flag = True
        logger.info("TTSWorker: durdurma isteği alındı.")

    # ── Ana çalışma ───────────────────────────────────────────────

    def run(self) -> None:
        self._stop_flag = False
        self.audio_dir.mkdir(parents=True, exist_ok=True)

        segments = self.chapter.segments
        total = len(segments)

        if total == 0:
            self.progress.emit(0, 0, "Seslendirilecek segment yok.")
            self.finished.emit()
            return

        logger.info(
            "TTSWorker başladı: engine=%s, voice=%s, %d segment",
            self.engine_name, self.voice, total,
        )

        if self._mix_engines:
            self._run_sequential(segments, total)
        elif self.engine_name == "edge-tts":
            self._run_edge_parallel(segments, total)
        else:
            self._run_sequential(segments, total)

        if not self._stop_flag:
            self.progress.emit(total, total, "Tüm segmentler tamamlandı.")
        self.finished.emit()

    # ── Edge-TTS: paralel ─────────────────────────────────────────

    def _run_edge_parallel(self, segments, total: int) -> None:
        """Edge-TTS için asyncio ile paralel sentez (max 3 eşzamanlı)."""

        async def _synth_one(idx: int, seg) -> None:
            if self._stop_flag or not seg.text.strip():
                return

            from core.tts_engine import normalize_caps

            voice    = self.voice
            out_path = str(self.audio_dir / f"segment_{idx:04d}.mp3")
            params   = self.params.copy()
            tts_text = normalize_caps(seg.text)

            # Cache kontrolü
            cache_key = None
            if self.cache:
                cp = {"engine": self.engine_name, "voice": voice, **params}
                cache_key = self.cache.get_cache_key(tts_text, voice, cp)
                cached = self.cache.get(cache_key)
                if cached:
                    seg.audio_path = cached
                    from core.audio_processor import get_duration
                    seg.duration = get_duration(cached)
                    self.segment_done.emit(idx, cached, seg.duration)
                    self.progress.emit(idx + 1, total, f"[Cache] Segment {idx+1}/{total}")
                    return

            self.segment_started.emit(idx)
            self.progress.emit(idx + 1, total, f"Sentezleniyor: Segment {idx+1}/{total}")

            import time as _time

            max_retries = 3
            last_exc = None
            for attempt in range(max_retries):
                try:
                    import edge_tts
                    comm = edge_tts.Communicate(
                        text=tts_text,
                        voice=voice,
                        rate=params.get("rate", "+0%"),
                        pitch=params.get("pitch", "+0Hz"),
                        volume=params.get("volume", "+0%"),
                    )
                    await comm.save(out_path)

                    from core.audio_processor import get_duration
                    duration = get_duration(out_path)
                    duration = self._apply_silence_removal(out_path, duration)
                    seg.audio_path = out_path
                    seg.duration   = duration

                    if self.cache and cache_key:
                        self.cache.put(cache_key, out_path)

                    self.segment_done.emit(idx, out_path, duration)

                    # Rate-limit önleme: segmentler arası kısa bekleme
                    await asyncio.sleep(0.3)
                    last_exc = None
                    break

                except Exception as exc:
                    last_exc = exc
                    err_str = str(exc)
                    logger.warning(
                        "Edge-TTS segment %d deneme %d/%d basarisiz: %s",
                        idx, attempt + 1, max_retries, err_str,
                    )
                    if "403" in err_str:
                        wait = 3 + attempt * 2
                        logger.info("403 hatasi, %ds bekleniyor (segment %d)...", wait, idx)
                        await asyncio.sleep(wait)
                    else:
                        await asyncio.sleep(1)

            if last_exc is not None:
                logger.error("Edge-TTS segment %d tum denemeler basarisiz: %s", idx, last_exc)
                self.error.emit(idx, str(last_exc))

        async def _run_all():
            sem = asyncio.Semaphore(EDGE_MAX_CONCURRENT)

            async def _limited(idx, seg):
                async with sem:
                    if not self._stop_flag:
                        await _synth_one(idx, seg)

            tasks = [
                asyncio.create_task(_limited(i, seg))
                for i, seg in enumerate(segments)
                if seg.text.strip()
                and (self._only_indices is None or i in self._only_indices)
            ]
            await asyncio.gather(*tasks)

        # QThread içinde yeni event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_all())
        finally:
            loop.close()

    # ── Diğer engine'ler: sıralı ──────────────────────────────────

    def _run_sequential(self, segments, total: int) -> None:
        """Kokoro ve diğerleri için sıralı sentez."""
        from core.tts_engine import TTSManager
        mgr = TTSManager.get_instance()

        for idx, seg in enumerate(segments):
            if self._stop_flag:
                logger.info("TTSWorker: durduruldu (segment %d).", idx)
                break

            if self._only_indices is not None and idx not in self._only_indices:
                continue

            if not seg.text.strip():
                continue

            from core.tts_engine import normalize_caps

            engine_name = self._engine_for_segment(seg)
            engine = mgr.get_engine(engine_name)
            voice    = self._voice_for_engine(engine_name)
            out_path = str(self.audio_dir / f"segment_{idx:04d}.mp3")
            params   = self.params.copy()
            tts_text = normalize_caps(seg.text)

            # Cache kontrolü
            cache_key = None
            if self.cache:
                cp = {"engine": engine_name, "voice": voice, **params}
                cache_key = self.cache.get_cache_key(tts_text, voice, cp)
                cached = self.cache.get(cache_key)
                if cached:
                    seg.audio_path = cached
                    from core.audio_processor import get_duration
                    seg.duration = get_duration(cached)
                    self.segment_done.emit(idx, cached, seg.duration)
                    self.progress.emit(idx + 1, total, f"[Cache] Segment {idx+1}/{total}")
                    continue

            self.segment_started.emit(idx)
            self.progress.emit(idx + 1, total, f"Sentezleniyor: Segment {idx+1}/{total}")

            try:
                synth_kwargs = dict(params)
                if engine_name == "kokoro" and "speed" in synth_kwargs:
                    try:
                        synth_kwargs["speed"] = float(synth_kwargs["speed"])
                    except (TypeError, ValueError):
                        synth_kwargs["speed"] = 1.0
                    logger.info(
                        "TTSWorker Kokoro segment %d speed=%.2f",
                        idx, synth_kwargs["speed"],
                    )
                duration = engine.synthesize(
                    text=tts_text,
                    voice=voice,
                    output_path=out_path,
                    **synth_kwargs,
                )
                duration = self._apply_silence_removal(out_path, duration)
                seg.audio_path = out_path
                seg.duration   = duration

                if self.cache and cache_key:
                    self.cache.put(cache_key, out_path)

                self.segment_done.emit(idx, out_path, duration)

            except Exception as exc:
                logger.error("Segment %d seslendirme hatası: %s", idx, exc)
                self.error.emit(idx, str(exc))


class KokoroModelDownloadWorker(QThread):
    """
    Kokoro modelini arka planda indirir.
    İlk açılışta gösterilecek ilerleme ekranı için kullanılır.
    """

    progress_msg = pyqtSignal(str)
    finished     = pyqtSignal(bool)   # True = başarılı

    def run(self) -> None:
        try:
            from core.tts_engine import KokoroTTSEngine
            engine = KokoroTTSEngine()

            def _cb(msg: str):
                self.progress_msg.emit(msg)

            ok = engine.download_model(progress_callback=_cb)
            self.finished.emit(ok)
        except Exception as exc:
            logger.error("Model indirme worker hatası: %s", exc)
            self.progress_msg.emit(f"Hata: {exc}")
            self.finished.emit(False)