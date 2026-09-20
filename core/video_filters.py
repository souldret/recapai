"""
RecapAI - FFmpeg klip filter graph (Ken Burns, gölge, look).
VideoComposer'dan ayrıldı; mux/encode orada kalır.
"""

from __future__ import annotations

from typing import Optional

RANDOM_MOTIONS = ("zoom_in", "zoom_out", "pan_down", "pan_up")


def _even(n: int) -> int:
    n = int(n)
    return n if n % 2 == 0 else n - 1


def compute_fitted_size(img_w: int, img_h: int, canvas_w: int, canvas_h: int) -> tuple:
    """Görseli tuvale sığdıran (letterbox) çift piksel boyut."""
    if img_w <= 0 or img_h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return canvas_w, canvas_h
    scale = min(canvas_w / img_w, canvas_h / img_h)
    fw = _even(max(2, int(img_w * scale)))
    fh = _even(max(2, int(img_h * scale)))
    return min(fw, canvas_w), min(fh, canvas_h)


def _zoom_expr(image_motion: str, intensity: float, n_max: int) -> str:
    return _motion_zoompan_exprs(image_motion, intensity, n_max)[0]


def _motion_zoompan_exprs(image_motion: str, intensity: float, n_max: int) -> tuple:
    """(z, x, y) zoompan ifadeleri. pan_down/up sabit yakınlıkta dikey kayar."""
    intensity = max(0.0, float(intensity))
    n_max = max(1, int(n_max))
    max_zoom = 1.0 + intensity
    x_c = "iw/2-(iw/zoom/2)"
    y_c = "ih/2-(ih/zoom/2)"
    if image_motion == "zoom_out":
        return f"({max_zoom:.6f})-({intensity:.6f})*on/{n_max}", x_c, y_c
    if image_motion == "pan_down":
        return f"{max_zoom:.6f}", x_c, f"(ih-ih/zoom)*on/{n_max}"
    if image_motion == "pan_up":
        return f"{max_zoom:.6f}", x_c, f"(ih-ih/zoom)*(1-on/{n_max})"
    return f"1+({intensity:.6f})*on/{n_max}", x_c, y_c


def _ken_burns_fullframe(
    src: str,
    dst: str,
    w: int,
    h: int,
    fps: int,
    z_expr: str,
    x_expr: Optional[str] = None,
    y_expr: Optional[str] = None,
) -> str:
    if x_expr is None:
        x_expr = "iw/2-(iw/zoom/2)"
    if y_expr is None:
        y_expr = "ih/2-(ih/zoom/2)"
    src_w, src_h = w * 2, h * 2
    return (
        f"{src}format=yuv444p,scale={src_w}:{src_h}:flags=lanczos,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
        f":d=1:s={w}x{h}:fps={fps}{dst}"
    )


def _place_fg_with_shadow(
    fg_label: str,
    bg_label: str,
    ox: int,
    oy: int,
    use_shadow: bool,
    sh_dx: int,
    sh_dy: int,
    sh_blur: int,
    out_label: str = "[vpre]",
) -> str:
    if not use_shadow:
        return f"{bg_label}{fg_label}overlay={ox}:{oy}:format=auto{out_label}"
    pad = _even(max(4, int(sh_blur) * 2))
    ox_sh = _even(ox + sh_dx - pad)
    oy_sh = _even(oy + sh_dy - pad)
    return (
        f"{fg_label}split=2[fg_main][fg_sh];"
        f"[fg_sh]format=rgba,lutrgb=r=0:g=0:b=0,"
        f"colorchannelmixer=aa=0.38,"
        f"pad=iw+{pad * 2}:ih+{pad * 2}:{pad}:{pad}:black@0,"
        f"boxblur={sh_blur}:1:{sh_blur}:1:{sh_blur}:1[sh];"
        f"{bg_label}[sh]overlay={ox_sh}:{oy_sh}:format=auto[bgsh];"
        f"[bgsh][fg_main]overlay={ox}:{oy}:format=auto{out_label}"
    )


def _finish_look_chain(src: str, dst: str, bg_effect: str) -> str:
    parts = [
        "eq=contrast=1.05:brightness=0.012:saturation=1.08:gamma=1.02",
        "unsharp=3:3:0.15:3:3:0.0",
    ]
    if bg_effect == "vignette_blur":
        parts.append("vignette=PI/5")
    elif bg_effect == "cinematic":
        parts.append("colorbalance=rs=0.04:gs=-0.01:bs=-0.05")
        parts.append("vignette=PI/6")
    elif bg_effect in ("gradient_tb", "gradient_lr"):
        parts.append("vignette=PI/4.5")
    return f"{src}{','.join(parts)}{dst}"


def build_clip_filter(
    canvas_w: int,
    canvas_h: int,
    fps: int,
    duration: float,
    fitted_w: int,
    fitted_h: int,
    *,
    ken_burns: bool = True,
    intensity: float = 0.15,
    image_motion: str = "zoom_in",
    bg_effect: str = "none",
    blur_background: bool = False,
    clip_index: int = 0,
) -> str:
    """Tek klip için filter_complex (watermark hariç, [vout] ile biter)."""
    w, h = canvas_w, canvas_h
    fw, fh = _even(fitted_w), _even(fitted_h)
    ox = _even((w - fw) // 2)
    oy = _even((h - fh) // 2)
    use_shadow = (w - fw) >= 24 or (h - fh) >= 24
    sh_dx = _even(max(12, w // 140))
    sh_dy = _even(max(14, h // 80))
    sh_blur = _even(max(14, h // 72))

    if image_motion == "random":
        import random as _motion_random
        image_motion = _motion_random.Random(clip_index).choice(list(RANDOM_MOTIONS))

    use_blur = blur_background or bg_effect in ("blur", "vignette_blur", "cinematic")
    total_frames = max(2, int(round(duration * fps)))
    n_max = max(1, total_frames - 1)

    if use_blur:
        blur_str = "boxblur=32:2"
        if bg_effect == "cinematic":
            blur_str = "boxblur=24:2,colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3"
        elif bg_effect == "vignette_blur":
            blur_str = "boxblur=36:2"
        bg = (
            f"[0:v]split=2[bg_in][fg_in];"
            f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={w}:{h},{blur_str},scale={w}:{h}:flags=lanczos[bg];"
        )
    elif bg_effect in ("gradient_tb", "gradient_lr"):
        bg = (
            f"[0:v]split=2[bg_in][fg_in];"
            f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={w}:{h},boxblur=24:2,scale={w}:{h}:flags=lanczos[bg];"
        )
    else:
        bg = (
            f"[0:v]split=2[bg_in][fg_in];"
            f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={w}:{h},lutrgb=r=0:g=0:b=0[bg];"
        )

    vf = (
        bg
        + f"[fg_in]scale={fw}:{fh}:flags=lanczos,format=rgba[fg];"
        + _place_fg_with_shadow("[fg]", "[bg]", ox, oy, use_shadow, sh_dx, sh_dy, sh_blur)
    )
    if ken_burns:
        z_expr, x_expr, y_expr = _motion_zoompan_exprs(image_motion, intensity, n_max)
        vf += ";" + _ken_burns_fullframe("[vpre]", "[vkb]", w, h, fps, z_expr, x_expr, y_expr)
        vf += ";" + _finish_look_chain("[vkb]", "[vout]", bg_effect)
    else:
        vf += ";" + _finish_look_chain("[vpre]", "[vout]", bg_effect)
    return vf
