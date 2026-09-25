"""
RecapAI - Video besteci (FFmpeg tabanlı).
Görseller + ses segmentlerini alarak MP4 video oluşturur.
"""

import logging
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.video_filters import (  # noqa: F401 — dış API uyumu
    RANDOM_MOTIONS,
    _even,
    _finish_look_chain,
    _ken_burns_fullframe,
    _motion_zoompan_exprs,
    _place_fg_with_shadow,
    _zoom_expr,
    build_clip_filter,
    compute_fitted_size,
)

logger = logging.getLogger(__name__)


# ── Varsayılan ayarlar ────────────────────────────────────────────────────────

DEFAULT_SETTINGS: Dict[str, Any] = {
    "resolution": [1920, 1080],
    "fps": 30,
    "transitions": "fade",
    "transition_duration": 0.5,
    "ken_burns": True,
    "ken_burns_intensity": 0.15,
    "image_motion": "zoom_in",   # zoom_in, zoom_out, pan_down, pan_up, random
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
    "bitrate": "12000k",
    "watermark_path": None,
    "watermark_position": "br",
    "watermark_scale": 0.08,
    "watermark_opacity": 0.85,
}

def _parse_resolution(value: Any) -> tuple:
    """
    resolution ayarını (width, height) olarak normalize eder.
    Desteklenen: [1920, 1080], (1920, 1080), "1920x1080", "1920×1080", "1920*1080"
    """
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return int(value[0]), int(value[1])
    if isinstance(value, str):
        cleaned = value.strip().lower().replace("×", "x").replace("*", "x")
        if "x" in cleaned:
            left, right = cleaned.split("x", 1)
            return int(left.strip()), int(right.strip())
    raise ValueError(
        f"Geçersiz resolution ayarı: {value!r}. "
        "Beklenen: [1920, 1080] veya '1920x1080'."
    )


def _is_lock_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    errno = getattr(exc, "errno", None)
    winerr = getattr(exc, "winerror", None)
    return errno in (13, 11) or winerr in (5, 32)


def _copy_output(src: str, dst: str) -> str:
    """Hedef dosya kilitliyse (Windows oynatıcı) benzersiz ada yazar."""
    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.exists():
        raise FileNotFoundError(f"Render çıktısı bulunamadı: {src}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src_path, dst_path)
        return str(dst_path)
    except OSError as exc:
        if not _is_lock_error(exc):
            raise
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        alt_path = dst_path.with_name(f"{dst_path.stem}_{stamp}{dst_path.suffix}")
        shutil.copy2(src_path, alt_path)
        logger.warning("Hedef kilitli, alternatif yazıldı: %s", alt_path)
        return str(alt_path)


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
        user_sub = self.settings.get("subtitle_style", {}) or {}
        if not isinstance(user_sub, dict):
            user_sub = {}
        self.settings["subtitle_style"] = {**default_sub, **user_sub}

        self._width, self._height = _parse_resolution(self.settings.get("resolution", [1920, 1080]))
        # Son encode yuv420p: 4'un katı (chroma + merkez /4)
        self._width -= self._width % 4
        self._height -= self._height % 4
        self._fps = int(self.settings.get("fps", 30) or 30)
        if self._fps <= 0:
            self._fps = 30
        # Ken Burns crop n sayacı ve klip encode bu fps ile; 60 çok yavaş
        self._clip_fps = min(self._fps, 30)
        self._codec = self.settings.get("codec") or "libx264"
        self._bitrate = self.settings.get("bitrate") or "8000k"
        self._is_gpu_codec = self._codec in ("h264_nvenc", "h264_qsv", "h264_amf")
        if self._is_gpu_codec:
            from core.ffmpeg_helper import get_available_gpu_encoders
            if self._codec not in get_available_gpu_encoders():
                logger.warning(
                    "GPU encoder '%s' bu sistemde kullanılamıyor; libx264'e (CPU) düşülüyor.",
                    self._codec,
                )
                self._codec = "libx264"
                self._is_gpu_codec = False

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
                raise RuntimeError("Render iptal edildi.")
            if not clip_paths:
                raise RuntimeError(
                    "Hiçbir video klibi oluşturulamadı. "
                    "Görsel yollarını ve ses dosyalarını kontrol edin."
                )

            # 2) Klipleri birleştir
            _cb(70, "—")
            self._log("Klipler birleştiriliyor...")
            merged = str(tmp_dir / "merged.mp4")
            self._concat_clips(clip_paths, merged, _cancelled)
            if _cancelled():
                raise RuntimeError("Render iptal edildi.")
            if not Path(merged).exists() or Path(merged).stat().st_size <= 0:
                raise RuntimeError("Klip birleştirme başarısız: çıktı dosyası boş.")

            # 3) Altyazı ekle
            with_subs = merged
            if self.settings.get("subtitles") and any(s.text for s in segments):
                _cb(80, "—")
                self._log("Altyazılar işleniyor...")
                sub_path = str(tmp_dir / "subtitles.ass")
                from core.subtitle_generator import generate_ass
                sub_style = dict(self.settings.get("subtitle_style") or {})
                # PlayRes gerçek çıktı çözünürlüğüne uysun (Shorts/4K dahil)
                sub_style["play_res_x"] = self._width
                sub_style["play_res_y"] = self._height
                # NOT: xfade geçişlerinde SES artık sıkıştırılmıyor (bkz.
                # _xfade_chain — konuşma kırpılmaması için ses tam/ham haliyle
                # art arda ekleniyor, video tarafı tpad ile telafi ediliyor).
                # Bu yüzden altyazı zaman çizelgesi de HAM (kaydırmasız,
                # transition_duration=0) kümülatif süreye göre hesaplanmalı;
                # aksi halde altyazılar sesle senkronsuz kayar.
                generate_ass(
                    chapter, sub_path, sub_style,
                    transition_duration=0.0,
                )
                subs_out = str(tmp_dir / "with_subs.mp4")
                self._burn_subtitles(merged, sub_path, subs_out, _cancelled)
                if Path(subs_out).exists() and Path(subs_out).stat().st_size > 0:
                    with_subs = subs_out
                else:
                    logger.warning("Altyazı yakma başarısız; altyazısız devam.")

            # 4) BGM ekle
            with_bgm = with_subs
            bgm_path = self.settings.get("bgm_path")
            if bgm_path and Path(bgm_path).exists():
                _cb(88, "—")
                self._log("BGM ekleniyor...")
                bgm_out = str(tmp_dir / "with_bgm.mp4")
                self._mix_bgm(with_subs, bgm_path, bgm_out, _cancelled)
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
                self._add_bookends(with_bgm, intro_path, outro_path, bookend_out, _cancelled)
                if Path(bookend_out).exists() and Path(bookend_out).stat().st_size > 0:
                    final_tmp = bookend_out

            # 6) Final kopyalama
            _cb(97, "—")
            self._log("Sonuç kopyalanıyor...")
            output_path = _copy_output(final_tmp, output_path)
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

            src = trim_out if Path(trim_out).exists() else merged
            output_path = _copy_output(src, output_path)
            _cb(100, "0s")
            return output_path

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Segment klip oluşturma
    # ─────────────────────────────────────────────────────────────────────────

    def _clip_worker_count(self) -> int:
        """
        Segment klipleri için kaç işlemin paralel çalışacağını belirler.

        FFmpeg klip üretimi CPU-bound filtrelerden (boxblur, zoompan, scale)
        oluştuğundan tek bir klip render'ı GPU/CPU codec'inden bağımsız
        olarak aynı hızda sürer (bkz. GPU encoder yalnızca encode adımını
        hızlandırır, filtre grafiğini hızlandırmaz). Bu nedenle GERÇEK
        performans kazancı, birden fazla klibi AYNI ANDA (paralel) işlemekten
        gelir: her FFmpeg process'i CPU'nun farklı çekirdeklerini kullanır ve
        GPU encoder'lar (NVENC/QSV/AMF) donanımsal olarak eşzamanlı birden
        fazla encode oturumunu destekler.
        """
        import os
        cpu_count = os.cpu_count() or 4
        if self._is_gpu_codec:
            # GPU encoder'lar birden çok eşzamanlı oturumu destekler;
            # CPU çekirdek sayısıyla sınırla (filtre grafiği hâlâ CPU'da çalışır).
            return max(2, min(6, cpu_count // 2))
        # CPU codec: her klip zaten çok çekirdek kullanabilir (libx264 threads);
        # aşırı paralellik çekirdekleri bölüştürüp yavaşlatabilir.
        return max(2, min(4, cpu_count // 3))

    def _build_segment_clips(
        self,
        chapter,
        tmp_dir: Path,
        progress_callback: Callable[[int, str], None],
        cancel_check: Optional[Callable[[], bool]],
    ) -> List[str]:
        """Her segment için görsel + ses → ayrı MP4 klip üretir (paralel)."""
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        segments = chapter.segments
        images = chapter.images
        total = len(segments)

        results: Dict[int, Optional[str]] = {}
        progress_lock = threading.Lock()
        completed = 0

        def _process(idx: int, seg) -> Optional[str]:
            if cancel_check and cancel_check():
                return None
            if not (getattr(seg, "text", None) or "").strip() and not getattr(seg, "audio_path", None):
                return ""

            img_path: Optional[str] = None
            extra_path = getattr(seg, "image_path", None)
            if extra_path and Path(str(extra_path)).exists():
                img_path = str(extra_path)
            elif 0 <= getattr(seg, "image_index", -1) < len(images):
                img_path = images[seg.image_index].path

            duration = _segment_clip_duration(seg)
            if duration <= 0:
                return ""
            clip_out = str(tmp_dir / f"clip_{idx:04d}.mp4")

            if not img_path or not Path(img_path).exists():
                logger.warning(
                    "Görsel bulunamadı segment %d için, placeholder klip üretiliyor.", idx,
                )
                if self._make_placeholder_clip(duration, clip_out, cancel_check):
                    return clip_out
                return None

            audio_path = seg.audio_path
            try:
                ok = self._make_clip(
                    img_path, audio_path, duration, clip_out,
                    clip_index=idx, cancel_check=cancel_check,
                )
                if not ok:
                    logger.warning("Ken Burns klip basarisiz, hareketsiz yeniden deneniyor (segment %d).", idx)
                    ok = self._make_clip(
                        img_path, audio_path, duration, clip_out,
                        clip_index=idx, cancel_check=cancel_check,
                        ken_burns_override=False,
                    )
            except Exception as exc:
                logger.error("Klip oluşturma istisnası (segment %d): %s", idx, exc)
                ok = False

            if ok and self._clip_file_ok(clip_out):
                return clip_out
            logger.warning("Klip oluşturulamadı, placeholder kullanılıyor: %s", clip_out)
            placeholder = str(tmp_dir / f"clip_{idx:04d}_ph.mp4")
            if self._make_placeholder_clip(duration, placeholder, cancel_check):
                return placeholder
            return None

        max_workers = self._clip_worker_count()
        self._log(f"Klipler {max_workers} paralel işlemle üretiliyor...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_process, idx, seg): idx
                for idx, seg in enumerate(segments)
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    logger.error("Klip worker hatası (segment %d): %s", idx, exc)
                    results[idx] = None
                with progress_lock:
                    completed += 1
                    pct = int(5 + (completed / total) * 60)
                    progress_callback(pct, "—")
                    self._log(f"Klip tamamlandı: {completed}/{total}")
                if cancel_check and cancel_check():
                    for pending in future_map:
                        pending.cancel()
                    break

        # Yoğun liste: başarısız klipler placeholder ile doldurulur.
        # Seyrek concat altyazı/ses zaman çizelgesini kaydırır.
        clip_paths: List[str] = []
        missing = 0
        for i in range(total):
            path = results.get(i)
            if path == "":
                continue
            if path:
                clip_paths.append(path)
                continue
            if cancel_check and cancel_check():
                break
            duration = _segment_clip_duration(segments[i])
            placeholder = str(tmp_dir / f"clip_{i:04d}_gap.mp4")
            if self._make_placeholder_clip(duration, placeholder, cancel_check):
                clip_paths.append(placeholder)
                missing += 1
            else:
                missing += 1
                logger.error("Placeholder klip de üretilemedi (segment %d).", i)

        if not clip_paths:
            raise RuntimeError(
                f"0/{total} klip üretilebildi. Görseller veya FFmpeg filtresi hatalı olabilir."
            )
        if missing:
            logger.warning(
                "Bazı klipler placeholder ile dolduruldu: %d/%d.", missing, total,
            )
        if len(clip_paths) != total and not (cancel_check and cancel_check()):
            logger.warning(
                "Klip sayısı segment sayısıyla uyuşmuyor: %d/%d — altyazı kayabilir.",
                len(clip_paths), total,
            )
        return clip_paths

    def _make_clip(
        self,
        image_path: str,
        audio_path: Optional[str],
        duration: float,
        output_path: str,
        clip_index: int = 0,
        cancel_check: Optional[Callable[[], bool]] = None,
        ken_burns_override: Optional[bool] = None,
    ) -> bool:
        """
        Tek bir görsel + ses'ten MP4 klip üretir.
        Ken Burns tuvalin tamamına uygulanır (görsel ekrana yaklaşır).
        Arka plan: none, blur, gradient_tb, gradient_lr, vignette_blur, cinematic
        """
        w, h = self._width, self._height
        ken_burns = self.settings.get("ken_burns", True) if ken_burns_override is None else ken_burns_override
        intensity = self.settings.get("ken_burns_intensity", 0.15)
        blur_bg = self.settings.get("blur_background", False)
        bg_effect = self.settings.get("bg_effect", "none")
        image_motion = self.settings.get("image_motion", "zoom_in")

        out_fps = self._clip_fps

        try:
            from PIL import Image as _PILImage
            with _PILImage.open(image_path) as _im:
                iw, ih = _im.size
        except Exception:
            iw, ih = w, h
        fw, fh = compute_fitted_size(iw, ih, w, h)

        vf = build_clip_filter(
            w, h, out_fps, duration, fw, fh,
            ken_burns=ken_burns,
            intensity=intensity,
            image_motion=image_motion,
            bg_effect=bg_effect,
            blur_background=blur_bg,
            clip_index=clip_index,
        )

        # Watermark overlay
        watermark_path = self.settings.get("watermark_path")
        has_watermark = watermark_path and Path(watermark_path).exists()
        if has_watermark:
            wm_pos = self.settings.get("watermark_position", "br")
            wm_scale = self.settings.get("watermark_scale", 0.08)
            wm_opacity = self.settings.get("watermark_opacity", 0.85)
            wm_w = int(w * wm_scale)
            margin = 20
            pos_map = {
                "tl": f"x={margin}:y={margin}",
                "tr": f"x=W-w-{margin}:y={margin}",
                "bl": f"x={margin}:y=H-h-{margin}",
                "br": f"x=W-w-{margin}:y=H-h-{margin}",
            }
            overlay_pos = pos_map.get(wm_pos, pos_map["br"])
            # [vout] etiketini ara katmana al; watermark son adım olarak eklenir
            vf = vf.replace("[vout]", "[pre_wm]")
            vf += (
                f";[1:v]scale={wm_w}:-1,format=rgba,"
                f"colorchannelmixer=aa={wm_opacity:.2f}[wm];"
                f"[pre_wm][wm]overlay={overlay_pos}[vout]"
            )

        cmd = ["-y"]

        # Giriş: görsel — framerate çıktı fps ile aynı olmalı (crop n sayacı)
        cmd += ["-loop", "1", "-framerate", str(out_fps), "-i", image_path]

        # Giriş: watermark (varsa) — index 1
        if has_watermark:
            cmd += ["-i", watermark_path]

        # Giriş: ses (varsa) — watermark yoksa index 1, varsa index 2
        has_audio = audio_path and Path(audio_path).exists()
        audio_index = 2 if has_watermark else 1
        if has_audio:
            cmd += ["-i", audio_path]

        # Video filtresi (filter_complex)
        cmd += ["-filter_complex", vf, "-map", "[vout]"]

        cmd += [
            "-c:v", self._codec,
            *self._quality_args(),
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(out_fps),
            "-movflags", "+faststart",
        ]

        if has_audio:
            cmd += [
                "-map", f"{audio_index}:a",
                "-c:a", "aac", "-b:a", "192k",
                "-ar", "44100", "-ac", "2",
            ]
        else:
            # Sessiz audio ekle
            cmd = self._build_silent_clip_cmd(
                image_path,
                duration,
                vf,
                watermark_path if has_watermark else None,
                out_fps=out_fps,
            )

        cmd.append(output_path)
        ret = self._run(cmd, cancel_check)
        if ret != 0:
            logger.error("Klip oluşturulamadı (rc=%d): %s", ret, output_path)
            try:
                log_dir = Path(__file__).resolve().parent.parent / "logs"
                log_dir.mkdir(exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                (log_dir / f"vf_fail_{ts}.txt").write_text(
                    f"image_motion={image_motion}\nbg_effect={bg_effect}\nblur_background={blur_bg}\n"
                    f"rc={ret}\n\nVF:\n{vf}\n\nCMD:\n{' '.join(str(a) for a in cmd)}",
                    encoding="utf-8",
                )
            except Exception:
                pass
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
            return False
        if not self._clip_file_ok(output_path):
            logger.error("Klip dosyasi bos veya gecersiz: %s", output_path)
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
            return False
        return True

    def _make_placeholder_clip(
        self,
        duration: float,
        output_path: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Sessiz siyah klip — başarısız segmentin süresini korur, altyazı kaymaz."""
        duration = max(0.1, float(duration or 4.0))
        w, h = self._width, self._height
        fps = self._clip_fps
        cmd = [
            "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", self._codec, *self._quality_args(),
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]
        ret = self._run(cmd, cancel_check)
        if ret != 0 or not self._clip_file_ok(output_path):
            logger.error("Placeholder klip oluşturulamadı: %s", output_path)
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
            return False
        return True

    def _build_silent_clip_cmd(
        self,
        image_path: str,
        duration: float,
        vf: str,
        watermark_path: Optional[str] = None,
        out_fps: Optional[int] = None,
    ) -> List[str]:
        """Sessiz video klibi için FFmpeg komutu."""
        fps = out_fps if out_fps is not None else self._fps
        cmd = [
            "-y",
            "-loop", "1", "-framerate", str(fps), "-i", image_path,
        ]
        if watermark_path and Path(watermark_path).exists():
            cmd += ["-i", watermark_path]
        # anullsrc: aevalsrc=0 AAC'de NaN/+Inf üretip loudnorm/xfade'i kırıyor
        # Çok düşük genlikli sine de kullanılabilir; anullsrc en temiz sessizlik
        cmd += [
            "-f", "lavfi",
            "-t", str(max(0.1, float(duration))),
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        silent_audio_index = 2 if (watermark_path and Path(watermark_path).exists()) else 1
        cmd += [
            "-filter_complex", vf,
            "-map", "[vout]", "-map", f"{silent_audio_index}:a",
            "-c:v", self._codec, *self._quality_args(),
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-movflags", "+faststart",
        ]
        return cmd

    # ─────────────────────────────────────────────────────────────────────────
    # Birleştirme ve post-process
    # ─────────────────────────────────────────────────────────────────────────

    def _clip_file_ok(self, path: str) -> bool:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 1024:
            return False
        from core.ffmpeg_helper import get_ffprobe_path, get_media_duration
        if not get_ffprobe_path():
            return True
        dur = get_media_duration(str(p))
        if dur > 0.05:
            return True
        if dur == 0.0 and p.stat().st_size > 8192:
            return True
        return False

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
            self._simple_concat(clip_paths, output_path, cancel_check)
        else:
            self._xfade_concat(clip_paths, output_path, transition, t_dur, cancel_check)

    def _simple_concat(
        self,
        clip_paths: List[str],
        output_path: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Geçiş efekti olmadan basit birleştirme."""
        list_file = Path(output_path).with_suffix(".list.txt")
        try:
            lines = []
            for p in clip_paths:
                # Windows yollarını FFmpeg concat demuxer için güvenli hale getir
                safe = str(Path(p).resolve()).replace("\\", "/")
                safe = safe.replace("'", "'\\''")
                lines.append(f"file '{safe}'")
            list_file.write_text("\n".join(lines), encoding="utf-8")
            self._run([
                "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                output_path,
            ], cancel_check)
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
            "horzopen":    "horzopen",
            "horzclose":   "horzclose",
            "vertopen":    "vertopen",
            "vertclose":   "vertclose",
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
            "horzopen", "vertopen",
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

        # Tek klip — xfade zinciri gereksiz, doğrudan simple_concat
        if len(clip_paths) == 1:
            self._simple_concat(clip_paths, output_path, cancel_check)
            return

        # Ses normalizasyonu — her klibi normalize et (sessiz kliplerde atlanır)
        norm_dir = Path(output_path).parent
        normalized_clips: List[str] = []
        for i, cp in enumerate(clip_paths):
            norm_out = str(norm_dir / f"norm_{i:04d}.mp4")
            if self._normalize_clip_audio(cp, norm_out, cancel_check):
                normalized_clips.append(norm_out)
            else:
                normalized_clips.append(cp)  # başarısız olursa orijinali kullan
        clip_paths_to_use = normalized_clips

        # Süreleri NORMALİZE SONRASI ölç — loudnorm AAC padding xfade offset'i bozmasın
        durations: List[float] = []
        for cp in clip_paths_to_use:
            d = get_media_duration(cp)
            if d <= 0:
                d = 4.0
            durations.append(d)

        # Geçiş süresi klipten uzun olamaz — aksi halde xfade offset bozulur
        min_dur = min(durations) if durations else t_dur
        safe_t_dur = min(float(t_dur), max(0.05, min_dur * 0.45))
        if safe_t_dur < t_dur:
            logger.warning(
                "transition_duration %.2fs çok uzun (min klip %.2fs); %.2fs kullanılıyor.",
                t_dur, min_dur, safe_t_dur,
            )
            t_dur = safe_t_dur

        # Yığımlı birleştirme: xfade zinciri
        use_pool = (transition == "random") or isinstance(transition, list)
        result = self._xfade_chain(
            clip_paths_to_use, durations, xf_transition or "fade", t_dur,
            random_pool=_random_transitions if use_pool else None,
        )
        cmd = ["-y"] + result["inputs"]

        cmd += [
            "-filter_complex", result["filter"],
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", self._codec,
            *self._quality_args(),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p",
            "-r", str(self._clip_fps),
            "-movflags", "+faststart",
            output_path,
        ]

        ret = self._run(cmd, cancel_check)
        if ret != 0 or not Path(output_path).exists() or Path(output_path).stat().st_size <= 0:
            # xfade başarısız — orijinal kliplerle simple concat'e dön
            logger.warning("xfade başarısız, orijinal kliplerle basit concat'e geçiliyor.")
            self._simple_concat(clip_paths, output_path, cancel_check)

        # Normalizasyon geçici dosyalarını temizle
        for norm_file in normalized_clips:
            try:
                if norm_file not in clip_paths and Path(norm_file).exists():
                    Path(norm_file).unlink()
            except Exception:
                pass

    def _xfade_chain(
        self,
        clips: List[str],
        durations: List[float],
        transition: str,
        t_dur: float,
        random_pool: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """xfade filter_complex zinciri oluşturur.

        NOT (SES ÜST ÜSTE BİNME DÜZELTMESİ): Video geçişleri için xfade
        kullanılırken ses için önceden `acrossfade` kullanılıyordu. Bu,
        her klibin sonundaki t_dur kadarlık kısmı bir SONRAKİ klibin
        başındaki t_dur kadarlık kısımla KARIŞTIRIR (mixer gibi toplar) —
        yani iki farklı seslendirme (TTS) segmenti aynı anda duyulur hale
        gelir. TTS ses dosyaları neredeyse hiç sessizlik içermediğinden
        (konuşma baştan sona dolu) bu üst üste binme çok belirgin olur.
        Video tarafında xfade görsel olarak sorunsuz bir geçiş efekti
        oluştursa da, ses tarafında akustik olarak buna karşılık gelen bir
        şey YOKTUR — sesler basitçe ardı ardına (concat) çalınmalıdır.
        Bu yüzden ses için ayrı bir `concat` filtresi kullanılır; video
        geçişi görsel olarak devam ederken ses hiçbir üst üste binme
        olmadan bir segmentten diğerine kesintisiz geçer.

        ÖNEMLİ (KONUŞMA KIRPILMASI DÜZELTMESİ): Ses hiç kırpılmadan tam
        haliyle art arda eklenir — hiçbir kelime kaybolmaz.

        KRİTİK (BİRİKEN SENKRON KAYMASI DÜZELTMESİ): xfade'in doğal offset
        zinciri `offset_i = offset_(i-1) + duration_i - t_dur` şeklinde
        ilerler; yani HER geçişte video zaman çizelgesi t_dur kadar kısalır
        (klipler üst üste bindirilir). Ses tarafı artık ham/kırpılmadan
        art arda eklendiğinden (yukarıdaki düzeltme), video ile ses
        arasında geçiş başına t_dur'luk bir sapma birikir: 5. geçişten
        sonra video, karşılık gelen sesin bitişinden 5×t_dur kadar ÖNCE bir
        sonraki görsele geçmiş olur (görsel, ses bitmeden değişir).
        Çözüm: xfade'e vermeden ÖNCE her klibin (son klip hariç) sonuna
        `tpad` ile t_dur kadar "donmuş kare" eklenir; xfade bu dondurulmuş
        kısmı kullanarak geçiş yapar, gerçek video içeriğinden bir şey
        çalınmaz. offset değerleri de HAM (kırpılmamış) kümülatif süreye
        göre hesaplanır. Sonuç: video toplam süresi = ses toplam süresi
        (sum(durations)) — geçiş sayısından bağımsız, sıfır sapma.
        """
        import random as _random

        n = len(clips)
        inputs = []
        for cp in clips:
            inputs += ["-i", cp]

        fc_parts: List[str] = []

        # İlk n-1 klibin video akışını t_dur kadar "donmuş kare" ile uzat;
        # böylece xfade bindirmesi gerçek içerikten değil, bu ek pay'dan
        # zaman çalar ve toplam video süresi HAM toplam süreyle eşit kalır.
        v_label = "[0p]" if n > 1 else "[0:v]"
        if n > 1:
            fc_parts.append(f"[0:v]tpad=stop_mode=clone:stop_duration={t_dur:.3f}[0p]")

        # xfade offset'leri HAM (kırpılmamış) kümülatif süreye göre —
        # her segmentin gerçek bitiş zamanı.
        cumulative = durations[0]

        for i in range(1, n):
            is_last = (i == n - 1)
            out_v = "[vraw]" if is_last else f"[v{i}]"

            # random mod: her geçiş için havuzdan rastgele seç
            cur_transition = _random.choice(random_pool) if random_pool else transition

            if is_last:
                next_label = f"[{i}:v]"
            else:
                next_label = f"[{i}p]"
                fc_parts.append(f"[{i}:v]tpad=stop_mode=clone:stop_duration={t_dur:.3f}[{i}p]")

            fc_parts.append(
                f"{v_label}{next_label}xfade=transition={cur_transition}:"
                f"duration={t_dur:.2f}:offset={cumulative:.3f}{out_v}"
            )

            v_label = out_v
            cumulative += durations[i]

        final_v_label = v_label if n > 1 else "[0:v]"
        fc_parts.append(f"{final_v_label}null[vout]")

        # Ses: crossfade YOK, kırpma YOK — tüm segmentler tam haliyle art
        # arda (concat) eklenir. Hiçbir kelime kaybolmaz. Toplam ses süresi
        # = toplam video süresi (cumulative) olduğundan sync sapması olmaz.
        audio_labels = "".join(f"[{i}:a]" for i in range(n))
        fc_parts.append(f"{audio_labels}concat=n={n}:v=0:a=1[aout]")

        return {
            "inputs": inputs,
            "filter": ";".join(fc_parts),
        }

    def _clip_has_audible_audio(self, clip_path: str) -> bool:
        """Sessiz klipleri tespit eder (loudnorm NaN üretmesin diye)."""
        try:
            result = subprocess.run(
                [
                    self._ffmpeg, "-hide_banner", "-i", clip_path,
                    "-af", "volumedetect",
                    "-f", "null", "-",
                ],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20,
            )
            mean = None
            for line in (result.stderr or "").splitlines():
                if "mean_volume:" in line:
                    part = line.split("mean_volume:")[-1].strip().split()[0]
                    mean = float(part)
                    break
            if mean is None:
                return True
            return mean > -50.0
        except Exception as exc:
            logger.debug("Ses seviyesi ölçülemedi (%s): %s", clip_path, exc)
            return True

    def _normalize_clip_audio(
        self,
        clip_path: str,
        output_path: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Ses seviyesini loudnorm filtresi ile normalize eder.
        Sessiz / neredeyse sessiz kliplerde loudnorm NaN üretir → atlanır.
        """
        try:
            from core.ffmpeg_helper import get_media_duration
            if get_media_duration(clip_path) < 0.25:
                return False
        except Exception:
            pass

        if not self._clip_has_audible_audio(clip_path):
            logger.debug("Sessiz klip — loudnorm atlandı: %s", clip_path)
            return False

        ret = self._run([
            "-y", "-i", clip_path,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:linear=true",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            output_path,
        ], cancel_check)
        return ret == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0

    def _burn_subtitles(
        self,
        input_path: str,
        sub_path: str,
        output_path: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        r"""ASS altyazıları video üzerine yazar (FFmpeg subtitles filtresi).

        Windows yol güvenliği:
        - Ters slash → ileri slash
        - İki nokta üst üste escape (C: → C\:)
        - Boşluk veya özel karakter içeren yollar için tek tırnak korunur
        """
        # Windows: C:\Users\Ad Soyad\... → C\:/Users/Ad Soyad/...
        safe_sub = sub_path.replace("\\", "/")
        # Sürücü harfi iki noktasını escape et (C: → C\:)
        import re as _re
        safe_sub = _re.sub(r"^([A-Za-z])(:/)", r"\1\\:/", safe_sub)
        # Tek tırnak içindeki tek tırnakları escape et
        safe_sub = safe_sub.replace("'", "'\\''")
        self._run([
            "-y",
            "-i", input_path,
            "-vf", f"ass='{safe_sub}'",
            "-c:v", self._codec, *self._quality_args(),
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ], cancel_check)

    def _mix_bgm(
        self,
        input_path: str,
        bgm_path: str,
        output_path: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """BGM'i ana ses ile karıştırır (ducking ile)."""
        bgm_vol = self.settings.get("bgm_volume", 0.15)
        ducking = self.settings.get("bgm_ducking", True)

        if ducking:
            # sidechaincompress: narrasyon yükselince BGM otomatik kısılır
            audio_filter = (
                f"[1:a]volume={bgm_vol:.2f}[bgm];"
                f"[bgm][0:a]sidechaincompress=threshold=0.02:ratio=8:attack=50:release=300[bgm_ducked];"
                f"[0:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=3,"
                f"aformat=sample_rates=44100:channel_layouts=stereo[aout]"
            )
        else:
            audio_filter = (
                f"[1:a]volume={bgm_vol:.2f}[bgm];"
                f"[0:a][bgm]amix=inputs=2:duration=first,"
                f"aformat=sample_rates=44100:channel_layouts=stereo[aout]"
            )

        ret = self._run([
            "-y",
            "-i", input_path,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex", audio_filter,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-shortest",
            output_path,
        ], cancel_check)
        # Ducking filtreleri yoksa veya fail olursa basit mix dene
        if (ret != 0 or not Path(output_path).exists() or Path(output_path).stat().st_size <= 0) and ducking:
            logger.warning("BGM ducking başarısız; basit mix deneniyor.")
            simple = (
                f"[1:a]volume={bgm_vol:.2f}[bgm];"
                f"[0:a][bgm]amix=inputs=2:duration=first,"
                f"aformat=sample_rates=44100:channel_layouts=stereo[aout]"
            )
            self._run([
                "-y",
                "-i", input_path,
                "-stream_loop", "-1", "-i", bgm_path,
                "-filter_complex", simple,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-ar", "44100", "-ac", "2",
                "-shortest",
                output_path,
            ], cancel_check)

    def _add_bookends(
        self,
        main_path: str,
        intro_path: Optional[str],
        outro_path: Optional[str],
        output_path: str,
        cancel_check: Optional[Callable[[], bool]] = None,
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

        # Tüm klipler aynı çözünürlük/fps/ses'e dönüştür
        tmp_dir = Path(output_path).parent
        normalized: List[str] = []
        for i, clip in enumerate(to_concat):
            norm_out = str(tmp_dir / f"bookend_norm_{i}.mp4")
            vf = (
                f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease,"
                f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={self._fps},format=yuv420p"
            )
            # 1) Ses varsa: video scale + ses aformat
            ret = self._run([
                "-y", "-i", clip,
                "-vf", vf,
                "-af", "aformat=sample_rates=44100:channel_layouts=stereo",
                "-c:v", self._codec, *self._quality_args(),
                "-c:a", "aac", "-b:a", "192k",
                "-ar", "44100", "-ac", "2",
                norm_out,
            ], cancel_check)
            # 2) Ses yoksa: anullsrc ile sessiz ses ekle
            if ret != 0 or not Path(norm_out).exists() or Path(norm_out).stat().st_size <= 0:
                ret = self._run([
                    "-y",
                    "-i", clip,
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-vf", vf,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", self._codec, *self._quality_args(),
                    "-c:a", "aac", "-b:a", "192k",
                    "-ar", "44100", "-ac", "2",
                    "-shortest",
                    norm_out,
                ], cancel_check)
            if ret == 0 and Path(norm_out).exists() and Path(norm_out).stat().st_size > 0:
                normalized.append(norm_out)
            else:
                logger.warning("Bookend normalleştirme başarısız: %s, orijinal kullanılıyor", clip)
                normalized.append(clip)

        self._simple_concat(normalized, output_path, cancel_check)

    # ─────────────────────────────────────────────────────────────────────────
    # FFmpeg çalıştırma
    # ─────────────────────────────────────────────────────────────────────────

    def _quality_args(self, crf: int = 18) -> List[str]:
        """
        Seçili codec'e (CPU/GPU) uygun preset/kalite argümanlarını döner.
        GPU encoder'ları (nvenc/qsv/amf) -crf desteklemez, kendi kalite
        parametrelerine sahiptir. CRF 18 ≈ görsel olarak kayıpsıza yakın.
        """
        bitrate = str(self._bitrate or "").strip()
        use_bitrate = bool(self.settings.get("use_bitrate")) and bool(bitrate)
        if self._codec == "h264_nvenc":
            if use_bitrate:
                return ["-preset", "p5", "-rc", "vbr", "-b:v", bitrate, "-maxrate", bitrate]
            return ["-preset", "p5", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
        if self._codec == "h264_qsv":
            if use_bitrate:
                return ["-preset", "medium", "-b:v", bitrate]
            return ["-preset", "medium", "-global_quality", str(crf)]
        if self._codec == "h264_amf":
            if use_bitrate:
                return ["-quality", "quality", "-b:v", bitrate]
            return ["-quality", "quality", "-qp_i", str(crf), "-qp_p", str(crf)]
        if self._codec == "libx265":
            if use_bitrate:
                return ["-preset", "medium", "-b:v", bitrate, "-tag:v", "hvc1"]
            return ["-preset", "medium", "-crf", str(crf), "-tag:v", "hvc1"]
        if use_bitrate:
            return ["-preset", "medium", "-b:v", bitrate, "-profile:v", "high"]
        return ["-preset", "medium", "-crf", str(crf), "-profile:v", "high"]

    def _run(self, args: List[str], cancel_check: Optional[Callable[[], bool]] = None) -> int:
        """FFmpeg komutunu çalıştırır. cancel_check ile gerçek iptal desteği."""
        import threading
        import time as _time

        cmd = [self._ffmpeg] + args
        logger.debug("FFmpeg: %s", " ".join(str(a) for a in cmd))
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stderr_lines: List[str] = []

            def _read_stderr() -> None:
                for line in process.stderr:
                    stderr_lines.append(line)

            t = threading.Thread(target=_read_stderr, daemon=True)
            t.start()

            while process.poll() is None:
                if cancel_check and cancel_check():
                    process.kill()
                    process.wait()
                    logger.info("FFmpeg iptal edildi (kill).")
                    return -1
                _time.sleep(0.1)

            t.join(timeout=2.0)
            ret = process.returncode
            if ret != 0:
                tail = "".join(stderr_lines[-20:])
                logger.error("FFmpeg HATA (rc=%d): %s", ret, tail[-2000:])
                # Hata detayını debug log dosyasına yaz
                try:
                    import pathlib, datetime
                    log_dir = pathlib.Path(__file__).parent.parent / "logs"
                    log_dir.mkdir(exist_ok=True)
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    (log_dir / f"ffmpeg_error_{ts}.log").write_text(
                        f"CMD: {' '.join(str(a) for a in cmd)}\n\nSTDERR:\n{''.join(stderr_lines)}",
                        encoding="utf-8"
                    )
                except Exception:
                    pass
            return ret
        except Exception as exc:
            logger.error("FFmpeg çalıştırma hatası: %s", exc)
            return -1

    def _log(self, msg: str) -> None:
        logger.info("[VideoComposer] %s", msg)


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı
# ─────────────────────────────────────────────────────────────────────────────

def _segment_clip_duration(seg, default: float = 4.0) -> float:
    """Klip ve altyazı için aynı süre kaynağı."""
    duration = float(getattr(seg, "duration", 0) or 0.0)
    if duration > 0:
        return duration
    audio_path = getattr(seg, "audio_path", None)
    if audio_path and Path(str(audio_path)).exists():
        try:
            from core.ffmpeg_helper import get_media_duration
            duration = float(get_media_duration(str(audio_path)) or 0.0)
        except Exception:
            duration = 0.0
    return duration if duration > 0 else default


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

    # Encode varsayılanı CRF 18'dir; bitrate yalnızca use_bitrate açıksa kullanılır.
    if settings.get("use_bitrate") and settings.get("bitrate"):
        bitrate_str = str(settings.get("bitrate") or "8000k")
        try:
            if bitrate_str.lower().endswith("k"):
                bitrate_kbps = float(bitrate_str[:-1])
            elif bitrate_str.lower().endswith("m"):
                bitrate_kbps = float(bitrate_str[:-1]) * 1000
            else:
                bitrate_kbps = float(bitrate_str) / 1000
        except ValueError:
            bitrate_kbps = 8000.0
    else:
        # CRF 18 ≈ 1080p30 için kabaca 8–12 Mbps; çözünürlüğe göre ölçekle.
        res = settings.get("resolution") or [1920, 1080]
        try:
            pixels = int(res[0]) * int(res[1])
        except (TypeError, ValueError, IndexError):
            pixels = 1920 * 1080
        bitrate_kbps = max(4000.0, min(20000.0, 8000.0 * (pixels / (1920 * 1080))))

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
