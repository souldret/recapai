"""
RecapAI - Video besteci (FFmpeg tabanlı).
Görseller + ses segmentlerini alarak MP4 video oluşturur.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Varsayılan ayarlar ────────────────────────────────────────────────────────

DEFAULT_SETTINGS: Dict[str, Any] = {
    "resolution": [1920, 1080],
    "fps": 30,
    "transitions": "fade",
    "transition_duration": 0.5,
    "ken_burns": True,
    "ken_burns_intensity": 0.15,
    "blur_background": False,
    "bg_effect": "none",
    "subtitles": True,
    "subtitle_style": {
        "font": "Arial",
        "size": 48,
        "color": "white",
        "stroke_color": "black",
        "stroke_width": 2,
        "position": "bottom",
    },
    "bgm_path": None,
    "bgm_volume": 0.15,
    "bgm_ducking": True,
    "intro_path": None,
    "outro_path": None,
    "codec": "libx264",
    "bitrate": "8000k",
}


class VideoComposer:
    """
    FFmpeg kullanarak video birleştirir.

    Desteklenen özellikler:
    - Ken Burns zoom/pan efekti
    - Fade / slide / dissolve geçişler
    - SRT/ASS altyazı overlay
    - BGM ducking ile arka plan müziği
    - Intro / outro video ekleme
    """

    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings: Dict[str, Any] = {**DEFAULT_SETTINGS, **settings}
        # subtitle_style içindeki eksik anahtarları doldur
        default_sub = DEFAULT_SETTINGS["subtitle_style"].copy()
        user_sub = self.settings.get("subtitle_style", {})
        self.settings["subtitle_style"] = {**default_sub, **user_sub}

        self._width, self._height = self.settings["resolution"]
        self._fps = self.settings["fps"]
        self._codec = self.settings["codec"]
        self._bitrate = self.settings["bitrate"]

        # FFmpeg yolunu doğrula
        from core.ffmpeg_helper import get_ffmpeg_path
        self._ffmpeg = get_ffmpeg_path()
        if not self._ffmpeg:
            raise RuntimeError(
                "FFmpeg bulunamadı. Lütfen FFmpeg'i yükleyin:\n"
                "https://ffmpeg.org/download.html"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def compose_chapter(
        self,
        chapter,
        output_path: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        Bir bölümün tüm segmentlerini birleştirerek MP4 üretir.

        Args:
            chapter: Chapter nesnesi
            output_path: Çıktı MP4 dosyasının yolu
            progress_callback: (percent: int, eta: str) callback
            cancel_check: True döndürürse iptal edilir

        Returns:
            Oluşturulan dosyanın yolu
        """
        segments = chapter.segments
        if not segments:
            raise ValueError("Render için segment bulunamadı. Önce TTS tamamlanmalı.")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        tmp_dir = Path(tempfile.mkdtemp(prefix="recapai_render_"))
        logger.info("Render başladı: %s → %s (tmp: %s)", chapter.name, output_path, tmp_dir)

        def _cb(p: int, eta: str) -> None:
            if progress_callback:
                progress_callback(p, eta)

        def _cancelled() -> bool:
            return cancel_check() if cancel_check else False

        try:
            _cb(2, "—")
            self._log("Geçici klasör hazırlandı.")

            # 1) Her segment için görsel + ses → kısa video klip
            _cb(5, "—")
            clip_paths = self._build_segment_clips(chapter, tmp_dir, _cb, _cancelled)
            if _cancelled():
                return output_path

            # 2) Klipleri birleştir
            _cb(70, "—")
            self._log("Klipler birleştiriliyor...")
            merged = str(tmp_dir / "merged.mp4")
            self._concat_clips(clip_paths, merged, _cancelled)
            if _cancelled():
                return output_path

            # 3) Altyazı ekle
            with_subs = merged
            if self.settings.get("subtitles") and any(s.text for s in segments):
                _cb(80, "—")
                self._log("Altyazılar işleniyor...")
                sub_path = str(tmp_dir / "subtitles.ass")
                from core.subtitle_generator import generate_ass
                generate_ass(chapter, sub_path, self.settings.get("subtitle_style"))
                subs_out = str(tmp_dir / "with_subs.mp4")
                self._burn_subtitles(merged, sub_path, subs_out)
                if Path(subs_out).exists() and Path(subs_out).stat().st_size > 0:
                    with_subs = subs_out

            # 4) BGM ekle
            with_bgm = with_subs
            bgm_path = self.settings.get("bgm_path")
            if bgm_path and Path(bgm_path).exists():
                _cb(88, "—")
                self._log("BGM ekleniyor...")
                bgm_out = str(tmp_dir / "with_bgm.mp4")
                self._mix_bgm(with_subs, bgm_path, bgm_out)
                if Path(bgm_out).exists() and Path(bgm_out).stat().st_size > 0:
                    with_bgm = bgm_out

            # 5) Intro / Outro ekle
            final_tmp = with_bgm
            intro_path = self.settings.get("intro_path")
            outro_path = self.settings.get("outro_path")
            if intro_path or outro_path:
                _cb(93, "—")
                self._log("Intro/Outro ekleniyor...")
                bookend_out = str(tmp_dir / "with_bookends.mp4")
                self._add_bookends(with_bgm, intro_path, outro_path, bookend_out)
                if Path(bookend_out).exists() and Path(bookend_out).stat().st_size > 0:
                    final_tmp = bookend_out

            # 6) Final kopyalama
            _cb(97, "—")
            self._log("Sonuç kopyalanıyor...")
            shutil.copy2(final_tmp, output_path)
            _cb(100, "0s")
            self._log(f"Render tamamlandı: {output_path}")
            logger.info("Render tamamlandı: %s", output_path)
            return output_path

        finally:
            # Geçici dosyaları temizle
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                logger.debug("Geçici klasör silindi: %s", tmp_dir)
            except Exception as e:
                logger.warning("Geçici klasör silinemedi: %s", e)

    def generate_preview(
        self,
        chapter,
        output_path: str,
        duration: float = 10.0,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> str:
        """
        Bölümün ilk `duration` saniyelik önizleme videosunu üretir.

        Args:
            chapter: Chapter nesnesi
            output_path: Çıktı dosyasının yolu
            duration: Önizleme süresi (saniye)
            progress_callback: İlerleme callback'i

        Returns:
            Oluşturulan dosyanın yolu
        """
        segments = chapter.segments
        if not segments:
            raise ValueError("Önizleme için segment bulunamadı.")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="recapai_preview_"))

        def _cb(p: int, eta: str) -> None:
            if progress_callback:
                progress_callback(p, eta)

        try:
            # İlk birkaç segmentle (duration kadar) kısa render yap
            preview_chapter = _clamp_chapter_to_duration(chapter, duration)
            clip_paths = self._build_segment_clips(preview_chapter, tmp_dir, _cb, None)
            merged = str(tmp_dir / "preview_merged.mp4")
            self._concat_clips(clip_paths, merged, None)

            # duration'ı aşmasın
            trim_out = str(tmp_dir / "preview_trimmed.mp4")
            self._run([
                "-y", "-i", merged,
                "-t", str(duration),
                "-c", "copy",
                trim_out,
            ])

            shutil.copy2(trim_out if Path(trim_out).exists() else merged, output_path)
            _cb(100, "0s")
            return output_path

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Segment klip oluşturma
    # ─────────────────────────────────────────────────────────────────────────

    def _build_segment_clips(
        self,
        chapter,
        tmp_dir: Path,
        progress_callback: Callable[[int, str], None],
        cancel_check: Optional[Callable[[], bool]],
    ) -> List[str]:
        """Her segment için görsel + ses → ayrı MP4 klip üretir."""
        segments = chapter.segments
        images = chapter.images
        clip_paths: List[str] = []
        total = len(segments)

        for idx, seg in enumerate(segments):
            if cancel_check and cancel_check():
                break

            pct = int(5 + (idx / total) * 60)
            progress_callback(pct, "—")
            self._log(f"Klip işleniyor: {idx + 1}/{total}")

            # Görsel yolunu bul
            img_path: Optional[str] = None
            if seg.image_index < len(images):
                img_path = images[seg.image_index].path

            if not img_path or not Path(img_path).exists():
                logger.warning("Görsel bulunamadı segment %d için, atlanıyor.", idx)
                continue

            # Ses süresi
            audio_path = seg.audio_path
            duration = seg.duration
            if duration <= 0:
                if audio_path and Path(audio_path).exists():
                    from core.ffmpeg_helper import get_media_duration
                    duration = get_media_duration(audio_path)
                if duration <= 0:
                    duration = 4.0  # fallback

            # Klip oluştur
            clip_out = str(tmp_dir / f"clip_{idx:04d}.mp4")
            self._make_clip(img_path, audio_path, duration, clip_out)

            if Path(clip_out).exists() and Path(clip_out).stat().st_size > 0:
                clip_paths.append(clip_out)
            else:
                logger.warning("Klip oluşturulamadı: %s", clip_out)

        return clip_paths

    def _make_clip(
        self,
        image_path: str,
        audio_path: Optional[str],
        duration: float,
        output_path: str,
    ) -> None:
        """
        Tek bir görsel + ses'ten MP4 klip üretir.
        Ken Burns efekti FFmpeg zoompan filtresi ile uygulanır.
        Arka plan efektleri: none, blur, gradient_tb, gradient_lr, vignette_blur, cinematic
        """
        w, h = self._width, self._height
        fps = self._fps
        ken_burns = self.settings.get("ken_burns", True)
        intensity = self.settings.get("ken_burns_intensity", 0.15)
        blur_bg = self.settings.get("blur_background", False)
        bg_effect = self.settings.get("bg_effect", "none")

        total_frames = max(1, int(duration * fps))
        w2, h2 = w * 2, h * 2

        zoom_delta = intensity / total_frames
        pan_x = f"iw/2-(iw/zoom/2)"
        pan_y = f"ih/2-(ih/zoom/2)"

        # bg_effect "blur" ise blur_bg de açık say
        use_blur = blur_bg or bg_effect in ("blur", "vignette_blur", "cinematic")

        if use_blur:
            # Arka plan blur filtresi
            blur_str = "boxblur=40:40"
            if bg_effect == "cinematic":
                blur_str = "boxblur=30:30,colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3"
            elif bg_effect == "vignette_blur":
                blur_str = "boxblur=50:50"

            vf = (
                f"[0:v]split=2[bg_in][fg_in];"
                f"[bg_in]scale={w2}:{h2}:force_original_aspect_ratio=increase,"
                f"crop={w2}:{h2},{blur_str}[bg];"
                f"[fg_in]scale={w2}:{h2}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[comp];"
            )
            if ken_burns:
                vf += (
                    f"[comp]zoompan=z='min(zoom+{zoom_delta:.6f},{1.0 + intensity:.3f})':"
                    f"x='{pan_x}':y='{pan_y}':d={total_frames}:s={w}x{h}:fps={fps},"
                    f"scale={w}:{h}[vout]"
                )
            else:
                vf += f"[comp]scale={w}:{h}[vout]"

        elif bg_effect == "gradient_tb":
            # Üstten alta gradient arka plan
            vf = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
                f"color=size={w}x{h}:color=0x0d1117:rate={fps}[grad];"
                f"[grad][fg]overlay=(W-w)/2:(H-h)/2[comp];"
            )
            if ken_burns:
                vf += (
                    f"[comp]zoompan=z='min(zoom+{zoom_delta:.6f},{1.0 + intensity:.3f})':"
                    f"x='{pan_x}':y='{pan_y}':d={total_frames}:s={w}x{h}:fps={fps},"
                    f"scale={w}:{h}[vout]"
                )
            else:
                vf += f"[comp]scale={w}:{h}[vout]"

        elif bg_effect == "gradient_lr":
            # Soldan sağa gradient arka plan
            vf = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
                f"color=size={w}x{h}:color=0x1a1a2e:rate={fps}[grad];"
                f"[grad][fg]overlay=(W-w)/2:(H-h)/2[comp];"
            )
            if ken_burns:
                vf += (
                    f"[comp]zoompan=z='min(zoom+{zoom_delta:.6f},{1.0 + intensity:.3f})':"
                    f"x='{pan_x}':y='{pan_y}':d={total_frames}:s={w}x{h}:fps={fps},"
                    f"scale={w}:{h}[vout]"
                )
            else:
                vf += f"[comp]scale={w}:{h}[vout]"

        else:
            # Standart siyah arka plan
            if ken_burns:
                vf = (
                    f"[0:v]scale={w2}:{h2}:force_original_aspect_ratio=decrease,"
                    f"pad={w2}:{h2}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"zoompan=z='min(zoom+{zoom_delta:.6f},{1.0 + intensity:.3f})':"
                    f"x='{pan_x}':y='{pan_y}':d={total_frames}:s={w}x{h}:fps={fps},"
                    f"scale={w}:{h}[vout]"
                )
            else:
                vf = (
                    f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[vout]"
                )

        cmd = ["-y"]

        # Giriş: görsel (loop ile duration kadar)
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", image_path]

        # Giriş: ses (varsa)
        has_audio = audio_path and Path(audio_path).exists()
        if has_audio:
            cmd += ["-i", audio_path]

        # Video filtresi (filter_complex)
        cmd += ["-filter_complex", vf, "-map", "[vout]"]

        # Encoding ayarları
        cmd += [
            "-c:v", self._codec,
            "-preset", "fast",
            "-crf", "23",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
        ]

        if has_audio:
            cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        else:
            # Sessiz audio ekle
            cmd = self._build_silent_clip_cmd(image_path, duration, vf)

        cmd.append(output_path)
        ret = self._run(cmd)
        if ret != 0:
            logger.error("Klip oluşturulamadı: %s", output_path)

    def _build_silent_clip_cmd(self, image_path: str, duration: float, vf: str) -> List[str]:
        """Sessiz video klibi için FFmpeg komutu."""
        w, h = self._width, self._height
        fps = self._fps
        return [
            "-y",
            "-loop", "1", "-framerate", str(fps), "-i", image_path,
            "-f", "lavfi", "-i", f"aevalsrc=0:c=stereo:r=44100",
            "-filter_complex", vf,
            "-map", "[vout]", "-map", "1:a",
            "-c:v", self._codec, "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-shortest",
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # Birleştirme ve post-process
    # ─────────────────────────────────────────────────────────────────────────

    def _concat_clips(
        self,
        clip_paths: List[str],
        output_path: str,
        cancel_check: Optional[Callable[[], bool]],
    ) -> None:
        """Klipler listesini geçiş efektiyle birleştirir."""
        if not clip_paths:
            raise ValueError("Birleştirilecek klip yok.")

        transition = self.settings.get("transitions", "fade")
        t_dur = float(self.settings.get("transition_duration", 0.5))

        if len(clip_paths) == 1:
            shutil.copy2(clip_paths[0], output_path)
            return

        if transition == "none" or t_dur <= 0:
            self._simple_concat(clip_paths, output_path)
        else:
            self._xfade_concat(clip_paths, output_path, transition, t_dur, cancel_check)

    def _simple_concat(self, clip_paths: List[str], output_path: str) -> None:
        """Geçiş efekti olmadan basit birleştirme."""
        list_file = Path(output_path).with_suffix(".list.txt")
        try:
            list_file.write_text(
                "\n".join(f"file '{p}'" for p in clip_paths),
                encoding="utf-8",
            )
            self._run([
                "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                output_path,
            ])
        finally:
            if list_file.exists():
                list_file.unlink()

    def _xfade_concat(
        self,
        clip_paths: List[str],
        output_path: str,
        transition,           # str veya List[str] (çoklu seçim)
        t_dur: float,
        cancel_check: Optional[Callable[[], bool]],
    ) -> None:
        """
        FFmpeg xfade filtresi ile geçişli birleştirme.
        Büyük listeler için klipleri 4'lü gruplar halinde işler.
        """
        # Desteklenen xfade geçişleri (FFmpeg xfade filtresi)
        xfade_map = {
            # ── Temel ──────────────────────────────────────────────
            "fade":        "fade",
            "dissolve":    "dissolve",
            "none":        None,
            # ── Yatay Kayma ────────────────────────────────────────
            "slide":       "slideleft",
            "slideright":  "slideright",
            "slideup":     "slideup",
            "slidedown":   "slidedown",
            # ── Cover (Üzerine Kapanma) ────────────────────────────
            "coverleft":   "coverleft",
            "coverright":  "coverright",
            "coverup":     "coverup",
            "coverdown":   "coverdown",
            # ── Wipe (Standart) ────────────────────────────────────
            "wipe":        "wiperight",
            "wipeleft":    "wipeleft",
            "wipeup":      "wipeup",
            "wipedown":    "wipedown",
            # ── Wipe (Köşeden) ─────────────────────────────────────
            "wipetl":      "wipetl",
            "wipetr":      "wipetr",
            "wipebl":      "wipebl",
            "wipebr":      "wipebr",
            # ── Çapraz (Diagonal) ──────────────────────────────────
            "diagtl":      "diagtl",
            "diagtr":      "diagtr",
            "diagbl":      "diagbl",
            "diagbr":      "diagbr",
            # ── Kutu Efekti ────────────────────────────────────────
            "hboxin":      "hboxin",
            "hboxout":     "hboxout",
            "vboxin":      "vboxin",
            "vboxout":     "vboxout",
            # ── Zoom / Daire ───────────────────────────────────────
            "zoom":        "zoomin",
            "circleopen":  "circleopen",
            "circleclose": "circleclose",
            # ── Diğer ──────────────────────────────────────────────
            "radial":      "radial",
            "pixelize":    "pixelize",
            "squeezeh":    "squeezeh",
            "squeezev":    "squeezev",
        }
        # random havuzu: görsel açıdan güçlü geçişlerin ağırlıklı listesi
        _random_transitions = [
            "fade", "dissolve",
            "slideleft", "slideright", "slideup", "slidedown",
            "coverleft", "coverright", "coverup", "coverdown",
            "wiperight", "wipeleft", "wipeup", "wipedown",
            "wipetl", "wipetr", "wipebl", "wipebr",
            "diagtl", "diagtr", "diagbl", "diagbr",
            "hboxin", "vboxin",
            "zoomin", "circleopen", "circleclose",
            "radial",
        ]

        # transitions: string ("fade", "random", "none") ya da list (çoklu seçim)
        if isinstance(transition, list):
            # Çoklu seçim: listedeki anahtarları FFmpeg adlarına çevir, random pool olarak kullan
            custom_pool = [xfade_map.get(t) for t in transition if xfade_map.get(t)]
            if not custom_pool:
                custom_pool = ["fade"]
            xf_transition = None   # _xfade_chain random_pool kullanacak
            _random_transitions = custom_pool
        elif transition == "random":
            xf_transition = None  # tam havuz — _random_transitions zaten dolu
        else:
            xf_transition = xfade_map.get(transition, "fade")

        from core.ffmpeg_helper import get_media_duration

        # Her klip süresini öğren
        durations: List[float] = []
        for cp in clip_paths:
            d = get_media_duration(cp)
            if d <= 0:
                d = 4.0
            durations.append(d)

        # Yığımlı birleştirme: 2'li xfade zinciri
        use_pool = (transition == "random") or isinstance(transition, list)
        result = self._xfade_chain(
            clip_paths, durations, xf_transition or "fade", t_dur,
            random_pool=_random_transitions if use_pool else None,
        )
        cmd = ["-y"] + result["inputs"]

        if len(clip_paths) == 1:
            self._simple_concat(clip_paths, output_path)
            return

        cmd += [
            "-filter_complex", result["filter"],
            "-map", f"[vout]",
            "-map", f"[aout]",
            "-c:v", self._codec,
            "-preset", "fast",
            "-crf", "23",
            "-b:v", self._bitrate,
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-r", str(self._fps),
            output_path,
        ]

        ret = self._run(cmd)
        if ret != 0:
            logger.warning("xfade başarısız, basit concat'e geçiliyor.")
            self._simple_concat(clip_paths, output_path)

    def _xfade_chain(
        self,
        clips: List[str],
        durations: List[float],
        transition: str,
        t_dur: float,
        random_pool: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """xfade filter_complex zinciri oluşturur."""
        import random as _random

        n = len(clips)
        inputs = []
        for cp in clips:
            inputs += ["-i", cp]

        fc_parts: List[str] = []
        # video stream etiketleri: [0:v], [1:v], ...
        v_label = "[0:v]"
        a_label = "[0:a]"

        offset = durations[0] - t_dur

        for i in range(1, n):
            out_v = f"[v{i}]" if i < n - 1 else "[vout]"
            out_a = f"[a{i}]" if i < n - 1 else "[aout]"

            # random mod: her geçiş için havuzdan rastgele seç
            cur_transition = _random.choice(random_pool) if random_pool else transition

            fc_parts.append(
                f"{v_label}[{i}:v]xfade=transition={cur_transition}:"
                f"duration={t_dur:.2f}:offset={offset:.2f}{out_v}"
            )
            fc_parts.append(
                f"{a_label}[{i}:a]acrossfade=d={t_dur:.2f}{out_a}"
            )

            v_label = out_v if i < n - 1 else "[vout]"
            a_label = out_a if i < n - 1 else "[aout]"

            offset += durations[i] - t_dur

        return {
            "inputs": inputs,
            "filter": ";".join(fc_parts),
        }

    def _burn_subtitles(self, input_path: str, sub_path: str, output_path: str) -> None:
        """ASS altyazıları video üzerine yazar (FFmpeg subtitles filtresi)."""
        # Windows'ta yol separatörlerini escape et
        safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")
        self._run([
            "-y",
            "-i", input_path,
            "-vf", f"ass='{safe_sub}'",
            "-c:v", self._codec, "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            output_path,
        ])

    def _mix_bgm(self, input_path: str, bgm_path: str, output_path: str) -> None:
        """BGM'i ana ses ile karıştırır (ducking ile)."""
        bgm_vol = self.settings.get("bgm_volume", 0.15)
        ducking = self.settings.get("bgm_ducking", True)

        if ducking:
            # Ses sıkıştırma: ana ses yükseldiğinde BGM düşer
            audio_filter = (
                f"[1:a]volume={bgm_vol:.2f}[bgm];"
                f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[aout]"
            )
        else:
            audio_filter = (
                f"[1:a]volume={bgm_vol:.2f}[bgm];"
                f"[0:a][bgm]amix=inputs=2:duration=first[aout]"
            )

        self._run([
            "-y",
            "-i", input_path,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex", audio_filter,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path,
        ])

    def _add_bookends(
        self,
        main_path: str,
        intro_path: Optional[str],
        outro_path: Optional[str],
        output_path: str,
    ) -> None:
        """
        Intro ve/veya outro videolarını ana videoya ekler.
        Farklı codec/çözünürlükte olabilecek videolar için yeniden kodlama yapılır.
        """
        to_concat: List[str] = []

        if intro_path and Path(intro_path).exists():
            to_concat.append(intro_path)

        to_concat.append(main_path)

        if outro_path and Path(outro_path).exists():
            to_concat.append(outro_path)

        if len(to_concat) == 1:
            shutil.copy2(main_path, output_path)
            return

        # Tüm klipler aynı çözünürlük/fps'e dönüştür
        tmp_dir = Path(output_path).parent
        normalized: List[str] = []
        for i, clip in enumerate(to_concat):
            norm_out = str(tmp_dir / f"bookend_norm_{i}.mp4")
            ret = self._run([
                "-y", "-i", clip,
                "-vf", f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease,"
                       f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2:black",
                "-r", str(self._fps),
                "-c:v", self._codec, "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-ar", "44100", "-ac", "2",
                norm_out,
            ])
            if ret == 0 and Path(norm_out).exists() and Path(norm_out).stat().st_size > 0:
                normalized.append(norm_out)
            else:
                logger.warning("Bookend normalleştirme başarısız: %s, orijinal kullanılıyor", clip)
                normalized.append(clip)

        self._simple_concat(normalized, output_path)

    # ─────────────────────────────────────────────────────────────────────────
    # FFmpeg çalıştırma
    # ─────────────────────────────────────────────────────────────────────────

    def _run(self, args: List[str]) -> int:
        """FFmpeg komutunu çalıştırır, çıkış kodunu döndürür."""
        cmd = [self._ffmpeg] + args
        logger.debug("FFmpeg: %s", " ".join(str(a) for a in cmd))
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                logger.debug("FFmpeg stderr:\n%s", result.stderr[-2000:])
            return result.returncode
        except Exception as exc:
            logger.error("FFmpeg çalıştırma hatası: %s", exc)
            return -1

    def _log(self, msg: str) -> None:
        logger.info("[VideoComposer] %s", msg)


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı
# ─────────────────────────────────────────────────────────────────────────────

def _clamp_chapter_to_duration(chapter, max_duration: float):
    """Verilen süreyle sınırlı bir Chapter kopyası döndürür."""
    import copy
    ch = copy.deepcopy(chapter)
    total = 0.0
    new_segs = []
    for seg in ch.segments:
        dur = seg.duration if seg.duration > 0 else 4.0
        if total + dur > max_duration:
            break
        new_segs.append(seg)
        total += dur
    ch.segments = new_segs
    return ch


def estimate_render_size(chapter, settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tahmini render süresi ve çıktı dosya boyutunu hesaplar.

    Returns:
        {"duration_sec": float, "size_mb": float, "size_str": str, "duration_str": str}
    """
    segments = chapter.segments
    total_sec = sum(
        s.duration if s.duration > 0 else 4.0 for s in segments
    )

    intro = settings.get("intro_path")
    outro = settings.get("outro_path")

    from core.ffmpeg_helper import get_media_duration
    if intro and Path(intro).exists():
        total_sec += get_media_duration(intro)
    if outro and Path(outro).exists():
        total_sec += get_media_duration(outro)

    # Bit hızına göre tahmini boyut (bitrate MB/s)
    bitrate_str = settings.get("bitrate", "8000k")
    try:
        if bitrate_str.endswith("k"):
            bitrate_kbps = float(bitrate_str[:-1])
        elif bitrate_str.endswith("M"):
            bitrate_kbps = float(bitrate_str[:-1]) * 1000
        else:
            bitrate_kbps = float(bitrate_str) / 1000
    except ValueError:
        bitrate_kbps = 8000.0

    # video + audio (128k)
    total_bits = (bitrate_kbps + 192) * total_sec
    size_mb = total_bits / 8 / 1024  # kbits → MB

    # Süre formatı
    m = int(total_sec // 60)
    s = int(total_sec % 60)
    dur_str = f"{m}:{s:02d}"

    return {
        "duration_sec": total_sec,
        "size_mb": size_mb,
        "size_str": f"{size_mb:.1f} MB",
        "duration_str": dur_str,
    }