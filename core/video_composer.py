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

logger = logging.getLogger(__name__)


# ── Varsayılan ayarlar ────────────────────────────────────────────────────────

DEFAULT_SETTINGS: Dict[str, Any] = {
    "resolution": [1920, 1080],
    "fps": 30,
    "transitions": "fade",
    "transition_duration": 0.5,
    "ken_burns": True,
    "ken_burns_intensity": 0.15,
    "image_motion": "zoom_in",   # zoom_in, zoom_out, slide_top, slide_bot, slide_right, slide_left, large_pan, full_pan
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
    "watermark_path": None,
    "watermark_position": "br",
    "watermark_scale": 0.08,
    "watermark_opacity": 0.85,
}

# ── Ken Burns pan yön ön ayarları ─────────────────────────────────────────────
# Her tuple: (pan_x_expr, pan_y_expr) — zoom büyüdükçe görüntü bu yönde kayar
_PAN_PRESETS = [
    # (pan_x_expr, pan_y_expr) — FFmpeg zoompan x/y crop sol-üst köşesidir.
    # x/y arttıkça kamera o yönün tersine doğru kayar (crop penceresi hareket eder).
    ("iw/2-(iw/zoom/2)",  "ih/2-(ih/zoom/2)"),   # 0: merkez zoom-in (pan yok)
    ("iw*(1-1/zoom)",     "ih*(1-1/zoom)"),        # 1: sağ-alt köşeye zoom-in
    ("0",                 "0"),                    # 2: sol-üst köşeye zoom-in
    ("iw*(1-1/zoom)",     "0"),                    # 3: sağ-üst köşeye zoom-in
    ("0",                 "ih*(1-1/zoom)"),        # 4: sol-alt köşeye zoom-in
    ("iw/2-(iw/zoom/2)",  "0"),                    # 5: üst-ortaya zoom-in
    ("iw/2-(iw/zoom/2)",  "ih*(1-1/zoom)"),        # 6: alt-ortaya zoom-in
    ("0",                 "ih/2-(ih/zoom/2)"),     # 7: sol-ortaya zoom-in
]


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
        # libx264 yuv420p için çift boyut zorunlu
        if self._width % 2:
            self._width -= 1
        if self._height % 2:
            self._height -= 1
        self._fps = int(self.settings.get("fps", 30) or 30)
        if self._fps <= 0:
            self._fps = 30
        self._codec = self.settings.get("codec") or "libx264"
        self._bitrate = self.settings.get("bitrate") or "8000k"

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
                # xfade bindirmesi varsa altyazı zamanını kaydır
                t_name = self.settings.get("transitions", "none")
                t_dur = float(self.settings.get("transition_duration", 0) or 0)
                if t_name == "none" or t_dur <= 0:
                    eff_t_dur = 0.0
                else:
                    # _xfade_concat ile aynı clamp mantığı (yaklaşık)
                    segs_durs = [
                        (s.duration if s.duration > 0 else 4.0) for s in segments
                    ]
                    min_d = min(segs_durs) if segs_durs else t_dur
                    eff_t_dur = min(t_dur, max(0.05, min_d * 0.45))
                generate_ass(
                    chapter, sub_path, sub_style,
                    transition_duration=eff_t_dur,
                )
                subs_out = str(tmp_dir / "with_subs.mp4")
                self._burn_subtitles(merged, sub_path, subs_out)
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
            try:
                self._make_clip(img_path, audio_path, duration, clip_out, clip_index=idx)
            except Exception as exc:
                logger.error("Klip oluşturma istisnası (segment %d): %s", idx, exc)

            if Path(clip_out).exists() and Path(clip_out).stat().st_size > 0:
                clip_paths.append(clip_out)
            else:
                logger.warning("Klip oluşturulamadı: %s", clip_out)

        if not clip_paths:
            raise RuntimeError(
                f"0/{total} klip üretilebildi. Görseller veya FFmpeg filtresi hatalı olabilir."
            )
        if len(clip_paths) < total:
            logger.warning(
                "Bazı klipler atlandı: %d/%d başarılı.", len(clip_paths), total
            )
        return clip_paths

    def _make_clip(
        self,
        image_path: str,
        audio_path: Optional[str],
        duration: float,
        output_path: str,
        clip_index: int = 0,
    ) -> None:
        """
        Tek bir görsel + ses'ten MP4 klip üretir.
        Ken Burns efekti FFmpeg zoompan filtresi ile uygulanır.
        Arka plan efektleri: none, blur, gradient_tb, gradient_lr, vignette_blur, cinematic
        Görsel animasyon modları (image_motion): zoom_in, zoom_out, slide_top, slide_bot,
            slide_right, slide_left, large_pan, full_pan

        TİTREME NOTU: zoompan filtresinde titreşimi önlemek için:
        - z ifadesinde if(eq(on,1),1.0,zoom)+delta kullanılır (başlangıç zoom'u açıkça 1.0)
        - fps parametresi zoompan'a verilmez; klip fps'i encoding aşamasında -r ile ayarlanır
        - zoompan çıktısından sonra ikinci scale KULLANILMAZ (sıçrama yaratır)
        - İki aşamalı scale: önce büyük, sonra zoompan; final boyut zoompan'ın s= parametresiyle
        """
        w, h = self._width, self._height
        fps = self._fps
        ken_burns = self.settings.get("ken_burns", True)
        intensity = self.settings.get("ken_burns_intensity", 0.15)
        blur_bg = self.settings.get("blur_background", False)
        bg_effect = self.settings.get("bg_effect", "none")
        image_motion = self.settings.get("image_motion", "zoom_in")

        max_zoom = 1.0 + intensity

        # zoompan için kaynak çözünürlük: 2x — zoompan sınır dışı kırpmayı önler
        w2, h2 = w * 2, h * 2

        # zoompan fps limiti: 30fps üzerinde çok yavaş — içeriden sabitle
        zoompan_fps = min(fps, 30)

        # bg_effect "blur" ise blur_bg de açık say
        use_blur = blur_bg or bg_effect in ("blur", "vignette_blur", "cinematic")

        # ── Slide modlar için yardımcı fonksiyon ──────────────────────────────
        def _build_slide_vf(bg_label: Optional[str]) -> str:
            """
            Slide modunda vf zinciri oluşturur.
            bg_label: blur/gradient durumunda arka planın [comp] etiketini taşıyan label.
                      None ise siyah renk kaynağı kullanılır.
            Slide modlar: slide_top, slide_bot, slide_right, slide_left
            """
            # Slide yön mantığı: isim görselin GELDİĞİ yönü belirtir.
            # overlay y/x: t=0'da ekran dışı başlar, t=duration'da yerleşir (y/x=0).
            # overlay x/y: filter_complex'te virgül parametre ayirici
            # if() icindeki virgulleri \, ile escape et
            if image_motion == "slide_top":
                # Gorsel yukaridan iner: y=-h -> 0
                slide_expr = f"if(gte(t\\,0)\\,-{h}+(t/{duration:.4f})*{h}\\,-{h})"
                overlay_xy = f"x=0:y={slide_expr}"
            elif image_motion == "slide_bot":
                # Gorsel asagidan cikar: y=h -> 0
                slide_expr = f"if(gte(t\\,0)\\,{h}-(t/{duration:.4f})*{h}\\,{h})"
                overlay_xy = f"x=0:y={slide_expr}"
            elif image_motion == "slide_right":
                # Gorsel sagdan gelir: x=w -> 0
                slide_expr = f"if(gte(t\\,0)\\,{w}-(t/{duration:.4f})*{w}\\,{w})"
                overlay_xy = f"x={slide_expr}:y=0"
            else:  # slide_left
                # Gorsel soldan gelir: x=-w -> 0
                slide_expr = f"if(gte(t\\,0)\\,-{w}+(t/{duration:.4f})*{w}\\,-{w})"
                overlay_xy = f"x={slide_expr}:y=0"

            if bg_label:
                # Blur/gradient arka planı zaten hazır; [comp] etiketini [bg] olarak kullan
                return (
                    f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
                    f"{bg_label}[fg]overlay={overlay_xy}[vout]"
                )
            else:
                # Siyah arka plan: split ile ikiye bol, fg overlay ile kaydır
                # (crop+t desteklenmiyor; split+overlay güvenilir)
                vf_parts = (
                    f"[0:v]split=2[bg_s][fg_s];"
                    f"[bg_s]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[bg_black];"
                    f"[fg_s]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
                )
                if image_motion == "slide_top":
                    slide_expr = f"if(gte(t\\,0)\\,-{h}+(t/{duration:.4f})*{h}\\,-{h})"
                    return vf_parts + f"[bg_black][fg]overlay=x=0:y={slide_expr}[vout]"
                elif image_motion == "slide_bot":
                    slide_expr = f"if(gte(t\\,0)\\,{h}-(t/{duration:.4f})*{h}\\,{h})"
                    return vf_parts + f"[bg_black][fg]overlay=x=0:y={slide_expr}[vout]"
                elif image_motion == "slide_right":
                    slide_expr = f"if(gte(t\\,0)\\,{w}-(t/{duration:.4f})*{w}\\,{w})"
                    return vf_parts + f"[bg_black][fg]overlay=x={slide_expr}:y=0[vout]"
                else:  # slide_left
                    slide_expr = f"if(gte(t\\,0)\\,-{w}+(t/{duration:.4f})*{w}\\,-{w})"
                    return vf_parts + f"[bg_black][fg]overlay=x={slide_expr}:y=0[vout]"

        # ── Zoompan ifadesi oluştur (image_motion'a göre) ─────────────────────
        # zoompan için frame sayısı: max 30fps ile hesapla (60fps çok yavaş)
        zp_total_frames = max(1, int(duration * zoompan_fps))
        zp_zoom_delta = intensity / zp_total_frames

        def _build_zoompan_vf(input_label: str) -> str:
            """
            Zoompan tabanlı vf parçası oluşturur.
            input_label: zoompan'ın alacağı giriş etiketi, ör. "[comp]" veya boş string (chain)
            """
            if image_motion == "zoom_out":
                zoom_expr_out = f"if(eq(on\\,1)\\,{max_zoom:.4f}\\,zoom)-{zp_zoom_delta:.6f}"
                zoom_clamp_out = f"max({zoom_expr_out}\\,1.0)"
                return (
                    f"{input_label}zoompan=z='{zoom_clamp_out}':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}[vout]"
                )
            elif image_motion == "large_pan":
                pan_x_lp, pan_y_lp = _PAN_PRESETS[clip_index % len(_PAN_PRESETS)]
                strong_intensity = min(intensity * 2, 0.40)
                strong_delta = strong_intensity / zp_total_frames
                strong_max = 1.0 + strong_intensity
                zoom_expr_lp = f"if(eq(on\\,1)\\,1.0\\,zoom)+{strong_delta:.6f}"
                zoom_clamp_lp = f"min({zoom_expr_lp}\\,{strong_max:.4f})"
                return (
                    f"{input_label}zoompan=z='{zoom_clamp_lp}':"
                    f"x='{pan_x_lp}':y='{pan_y_lp}':d=1:s={w}x{h}[vout]"
                )
            elif image_motion == "full_pan":
                fp_zoom = 1.30
                fp_x = f"(on-1)/({zp_total_frames}-1)*iw*(1-1/{fp_zoom:.2f})"
                return (
                    f"{input_label}zoompan=z='{fp_zoom:.2f}':"
                    f"x='{fp_x}':y='ih/2-(ih/{fp_zoom:.2f}/2)':d=1:s={w}x{h}[vout]"
                )
            else:
                # zoom_in
                zoom_expr_in = f"if(eq(on\\,1)\\,1.0\\,zoom)+{zp_zoom_delta:.6f}"
                zoom_clamp_in = f"min({zoom_expr_in}\\,{max_zoom:.4f})"
                return (
                    f"{input_label}zoompan=z='{zoom_clamp_in}':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}[vout]"
                )

        # ── Slide mod mu? ──────────────────────────────────────────────────────
        is_slide = image_motion in ("slide_top", "slide_bot", "slide_right", "slide_left")

        # ── VF zinciri oluştur ─────────────────────────────────────────────────
        if use_blur:
            # Arka plan blur filtresi
            blur_str = "boxblur=40:40"
            if bg_effect == "cinematic":
                blur_str = "boxblur=30:30,colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3"
            elif bg_effect == "vignette_blur":
                blur_str = "boxblur=50:50"

            if not ken_burns:
                # Sabit görsel: w x h blur arka plan + fg overlay
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={w}:{h},{blur_str},scale={w}:{h}:flags=lanczos[bg];"
                    f"[fg_in]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
                )
            elif is_slide:
                # Slide: blur arka plan (w x h) + fg kaydırma
                if image_motion == "slide_top":
                    slide_expr = f"if(gte(t\\,0)\\,-{h}+(t/{duration:.4f})*{h}\\,-{h})"
                    slide_part = f"[bg_blur][fg]overlay=x=0:y={slide_expr}[vout]"
                elif image_motion == "slide_bot":
                    slide_expr = f"if(gte(t\\,0)\\,{h}-(t/{duration:.4f})*{h}\\,{h})"
                    slide_part = f"[bg_blur][fg]overlay=x=0:y={slide_expr}[vout]"
                elif image_motion == "slide_right":
                    slide_expr = f"if(gte(t\\,0)\\,{w}-(t/{duration:.4f})*{w}\\,{w})"
                    slide_part = f"[bg_blur][fg]overlay=x={slide_expr}:y=0[vout]"
                else:
                    slide_expr = f"if(gte(t\\,0)\\,-{w}+(t/{duration:.4f})*{w}\\,-{w})"
                    slide_part = f"[bg_blur][fg]overlay=x={slide_expr}:y=0[vout]"
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={w}:{h},{blur_str},scale={w}:{h}:flags=lanczos[bg_blur];"
                    f"[fg_in]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
                    + slide_part
                )
            else:
                # zoom modlar: bg=blur w x h, fg=w x h zoompan, sonra bg üstüne overlay
                # w2/h2 yerine w/h: blur modda zoompan kaynagi daha kucuk, daha hizli
                zp = _build_zoompan_vf("[fg_big]").replace("[vout]", "[fg_zoomed]") + ";"
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={w}:{h},{blur_str},scale={w}:{h}:flags=lanczos[bg];"
                    f"[fg_in]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg_big];"
                    + zp +
                    "[bg][fg_zoomed]overlay=(W-w)/2:(H-h)/2[vout]"
                )

        elif bg_effect in ("gradient_tb", "gradient_lr"):
            # Gradient: blur gibi split+overlay — color source filter yok
            grad_blur = "boxblur=30:30"
            if not ken_burns:
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={w}:{h},{grad_blur},scale={w}:{h}:flags=lanczos[bg];"
                    f"[fg_in]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
                )
            elif is_slide:
                if image_motion == "slide_top":
                    slide_expr = f"if(gte(t\\,0)\\,-{h}+(t/{duration:.4f})*{h}\\,-{h})"
                    slide_part = f"[bg_blur][fg]overlay=x=0:y={slide_expr}[vout]"
                elif image_motion == "slide_bot":
                    slide_expr = f"if(gte(t\\,0)\\,{h}-(t/{duration:.4f})*{h}\\,{h})"
                    slide_part = f"[bg_blur][fg]overlay=x=0:y={slide_expr}[vout]"
                elif image_motion == "slide_right":
                    slide_expr = f"if(gte(t\\,0)\\,{w}-(t/{duration:.4f})*{w}\\,{w})"
                    slide_part = f"[bg_blur][fg]overlay=x={slide_expr}:y=0[vout]"
                else:
                    slide_expr = f"if(gte(t\\,0)\\,-{w}+(t/{duration:.4f})*{w}\\,-{w})"
                    slide_part = f"[bg_blur][fg]overlay=x={slide_expr}:y=0[vout]"
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={w}:{h},{grad_blur},scale={w}:{h}:flags=lanczos[bg_blur];"
                    f"[fg_in]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
                    + slide_part
                )
            else:
                # zoom modlar: bg=gradient blur w x h, fg=w x h zoompan → w x h
                zp = _build_zoompan_vf("[fg_big]").replace("[vout]", "[fg_zoomed]") + ";"
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={w}:{h},{grad_blur},scale={w}:{h}:flags=lanczos[bg];"
                    f"[fg_in]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[fg_big];"
                    + zp +
                    "[bg][fg_zoomed]overlay=(W-w)/2:(H-h)/2[vout]"
                )

        else:
            # Standart siyah arka plan
            if not ken_burns:
                vf = (
                    f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[vout]"
                )
            elif is_slide:
                vf = _build_slide_vf(None)
            else:
                # zoom_in, zoom_out, large_pan, full_pan — standart siyah arka plan
                vf = (
                    f"[0:v]scale={w2}:{h2}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={w2}:{h2}:(ow-iw)/2:(oh-ih)/2:black,"
                    + _build_zoompan_vf("")
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

        # Giriş: görsel (loop ile duration kadar)
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", image_path]

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

        # Encoding ayarları — zoompan modlarda fps'i 30 ile sınırla (hız)
        out_fps = zoompan_fps if not is_slide else fps
        cmd += [
            "-c:v", self._codec,
            "-preset", "fast",
            "-crf", "23",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(out_fps),
        ]

        if has_audio:
            cmd += [
                "-map", f"{audio_index}:a",
                "-c:a", "aac", "-b:a", "192k",
                "-ar", "44100", "-ac", "2",
                "-shortest",
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
        ret = self._run(cmd)
        if ret != 0:
            logger.error("Klip oluşturulamadı (rc=%d): %s", ret, output_path)
            # Hata ayıklama: sadece başarısız kliplerde VF log yaz
            try:
                log_dir = Path(__file__).resolve().parent.parent / "logs"
                log_dir.mkdir(exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                (log_dir / f"vf_fail_{ts}.txt").write_text(
                    f"image_motion={image_motion}\nbg_effect={bg_effect}\nuse_blur={use_blur}\n"
                    f"rc={ret}\n\nVF:\n{vf}\n\nCMD:\n{' '.join(str(a) for a in cmd)}",
                    encoding="utf-8",
                )
            except Exception:
                pass

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
            "-c:v", self._codec, "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-ar", "44100", "-ac", "2",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-shortest",
        ]
        return cmd

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

        # Her klip süresini öğren
        durations: List[float] = []
        for cp in clip_paths:
            d = get_media_duration(cp)
            if d <= 0:
                d = 4.0
            durations.append(d)

        # Tek klip — xfade zinciri gereksiz, doğrudan simple_concat
        if len(clip_paths) == 1:
            self._simple_concat(clip_paths, output_path)
            return

        # Ses normalizasyonu — her klibi normalize et (sessiz kliplerde atlanır)
        norm_dir = Path(output_path).parent
        normalized_clips: List[str] = []
        for i, cp in enumerate(clip_paths):
            norm_out = str(norm_dir / f"norm_{i:04d}.mp4")
            if self._normalize_clip_audio(cp, norm_out):
                normalized_clips.append(norm_out)
            else:
                normalized_clips.append(cp)  # başarısız olursa orijinali kullan
        clip_paths_to_use = normalized_clips

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
            "-preset", "fast",
            "-crf", "23",
            "-b:v", self._bitrate,
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p",
            "-r", str(self._fps),
            output_path,
        ]

        ret = self._run(cmd)
        if ret != 0 or not Path(output_path).exists() or Path(output_path).stat().st_size <= 0:
            # xfade başarısız — orijinal kliplerle simple concat'e dön
            logger.warning("xfade başarısız, orijinal kliplerle basit concat'e geçiliyor.")
            self._simple_concat(clip_paths, output_path)

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

        offset = max(0.0, durations[0] - t_dur)

        for i in range(1, n):
            is_last = (i == n - 1)
            out_v = "[vout]" if is_last else f"[v{i}]"
            out_a = "[aout]" if is_last else f"[a{i}]"

            # random mod: her geçiş için havuzdan rastgele seç
            cur_transition = _random.choice(random_pool) if random_pool else transition

            fc_parts.append(
                f"{v_label}[{i}:v]xfade=transition={cur_transition}:"
                f"duration={t_dur:.2f}:offset={offset:.2f}{out_v}"
            )
            fc_parts.append(
                f"{a_label}[{i}:a]acrossfade=d={t_dur:.2f}{out_a}"
            )

            # Bir sonraki iterasyon için giriş etiketleri bu iterasyonun çıkışlarıdır
            v_label = out_v
            a_label = out_a

            offset = max(0.0, offset + durations[i] - t_dur)

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

    def _normalize_clip_audio(self, clip_path: str, output_path: str) -> bool:
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
        ])
        return ret == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0

    def _burn_subtitles(self, input_path: str, sub_path: str, output_path: str) -> None:
        """ASS altyazıları video üzerine yazar (FFmpeg subtitles filtresi).

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
            "-c:v", self._codec, "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            output_path,
        ])

    def _mix_bgm(self, input_path: str, bgm_path: str, output_path: str) -> None:
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
        ])
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
                "-c:v", self._codec, "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-ar", "44100", "-ac", "2",
                norm_out,
            ])
            # 2) Ses yoksa: anullsrc ile sessiz ses ekle
            if ret != 0 or not Path(norm_out).exists() or Path(norm_out).stat().st_size <= 0:
                ret = self._run([
                    "-y",
                    "-i", clip,
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-vf", vf,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", self._codec, "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "192k",
                    "-ar", "44100", "-ac", "2",
                    "-shortest",
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
