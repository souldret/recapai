"""Ken Burns (tam kare dolly) ve drop shadow filtre grafiği."""

import subprocess
from pathlib import Path

import pytest

from core.ffmpeg_helper import get_ffmpeg_path
from core.video_composer import (
    RANDOM_MOTIONS,
    _copy_output,
    _motion_zoompan_exprs,
    _zoom_expr,
    build_clip_filter,
    compute_fitted_size,
)


def test_fitted_size_portrait_letterbox():
    fw, fh = compute_fitted_size(800, 1600, 1920, 1080)
    assert fh == 1080
    assert fw == 540
    assert fw < 1920


def test_fitted_size_landscape():
    fw, fh = compute_fitted_size(1920, 800, 1920, 1080)
    assert fw == 1920
    assert fh < 1080


def test_zoom_in_starts_at_one():
    expr = _zoom_expr("zoom_in", 0.15, 29)
    assert expr.startswith("1+")
    assert "0.150000" in expr


def test_zoom_out_starts_zoomed():
    expr = _zoom_expr("zoom_out", 0.15, 29)
    assert expr.startswith("(1.150000)")


def test_pan_down_keeps_zoom_and_moves_y():
    z, x, y = _motion_zoompan_exprs("pan_down", 0.15, 29)
    assert z == "1.150000"
    assert "iw/2-(iw/zoom/2)" in x and "on/" not in x
    assert "*on/29" in y
    assert "1-on/" not in y


def test_pan_up_keeps_zoom_and_moves_y_reversed():
    z, x, y = _motion_zoompan_exprs("pan_up", 0.15, 29)
    assert z == "1.150000"
    assert "1-on/29" in y


def test_pan_has_no_horizontal_slide():
    z, x, y = _motion_zoompan_exprs("pan_down", 0.2, 10)
    assert "on/" not in x
    vf = build_clip_filter(1920, 1080, 30, 1.0, 540, 1080, image_motion="pan_down")
    assert "pan_left" not in vf
    assert "y='(ih-ih/zoom)*on/" in vf
    assert "trunc(" not in vf


def test_zoompan_is_full_canvas_not_fitted_box():
    vf = build_clip_filter(
        1920, 1080, 30, 2.0, 540, 1080,
        ken_burns=True,
        intensity=0.15,
        image_motion="zoom_in",
        bg_effect="none",
    )
    assert "zoompan=" in vf
    assert "s=1920x1080" in vf
    assert "s=540x1080" not in vf
    assert "scale=3840:2160" in vf
    overlay_at = vf.find("overlay=")
    zoom_at = vf.find("zoompan=")
    assert overlay_at != -1 and zoom_at != -1
    assert overlay_at < zoom_at


def test_shadow_is_black_silhouette():
    vf = build_clip_filter(
        1920, 1080, 30, 2.0, 540, 1080,
        ken_burns=False,
        bg_effect="blur",
        blur_background=True,
    )
    assert "lutrgb=r=0:g=0:b=0" in vf
    assert "colorchannelmixer=aa=0.38" in vf
    assert "boxblur=" in vf
    assert "pad=" in vf
    assert "fg_sh" in vf


def test_full_bleed_has_no_shadow():
    vf = build_clip_filter(
        1920, 1080, 30, 2.0, 1920, 1080,
        ken_burns=False,
        bg_effect="none",
    )
    assert "fg_sh" not in vf
    assert "colorchannelmixer=aa=0.38" not in vf


def test_zoompan_uses_yuv444p():
    vf = build_clip_filter(1920, 1080, 30, 1.0, 540, 1080, ken_burns=True)
    assert "format=yuv444p,scale=3840:2160" in vf
    assert "x='iw/2-(iw/zoom/2)'" in vf
    assert "trunc(" not in vf


def test_overlay_coords_are_even():
    vf = build_clip_filter(1920, 1080, 30, 1.0, 541, 1079, ken_burns=False)
    import re
    overlays = re.findall(r"overlay=(-?\d+):(-?\d+)", vf)
    assert overlays
    for x, y in overlays:
        assert int(x) % 2 == 0
        assert int(y) % 2 == 0


def test_random_motion_is_deterministic():
    a = build_clip_filter(1920, 1080, 30, 1.0, 540, 1080, image_motion="random", clip_index=3)
    b = build_clip_filter(1920, 1080, 30, 1.0, 540, 1080, image_motion="random", clip_index=3)
    assert a == b


def test_random_pool_includes_pan():
    assert RANDOM_MOTIONS == ("zoom_in", "zoom_out", "pan_down", "pan_up")
    graphs = [
        build_clip_filter(1920, 1080, 30, 1.0, 540, 1080, image_motion="random", clip_index=i)
        for i in range(16)
    ]
    assert any("(ih-ih/zoom)*on/" in g or "(ih-ih/zoom)*(1-on/" in g for g in graphs)


BG_EFFECTS = ("none", "blur", "vignette_blur", "cinematic", "gradient_tb", "gradient_lr")
MOTIONS = ("zoom_in", "zoom_out", "pan_down", "pan_up")


@pytest.mark.parametrize("bg_effect", BG_EFFECTS)
@pytest.mark.parametrize("ken_burns", (True, False))
@pytest.mark.parametrize("motion", MOTIONS)
def test_filter_graphs_accepted_by_ffmpeg(bg_effect, ken_burns, motion, tmp_path):
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg yok")
    vf = build_clip_filter(
        1920, 1080, 30, 0.2, 540, 1080,
        ken_burns=ken_burns,
        intensity=0.15,
        image_motion=motion,
        bg_effect=bg_effect,
        blur_background=(bg_effect == "blur"),
    )
    src = tmp_path / "src.png"
    from PIL import Image
    Image.new("RGB", (800, 1600), (40, 180, 90)).save(src)
    out = tmp_path / "out.mp4"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-framerate", "30", "-i", str(src),
        "-filter_complex", vf, "-map", "[vout]",
        "-frames:v", "4", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert out.exists() and out.stat().st_size > 1000


def test_make_clip_end_to_end(tmp_path):
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg yok")
    from PIL import Image
    from core.video_composer import VideoComposer

    img = tmp_path / "panel.png"
    Image.new("RGB", (800, 1600), (220, 40, 40)).save(img)
    out = tmp_path / "clip.mp4"
    composer = VideoComposer({
        "resolution": [1280, 720],
        "fps": 30,
        "codec": "libx264",
        "ken_burns": True,
        "ken_burns_intensity": 0.15,
        "image_motion": "zoom_in",
        "bg_effect": "none",
        "subtitles": False,
    })
    ok = composer._make_clip(str(img), None, 0.4, str(out), clip_index=0)
    assert ok is True
    assert out.exists() and out.stat().st_size > 1000


def test_blur_background_clip_encodes(tmp_path):
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg yok")
    from PIL import Image
    from core.video_composer import VideoComposer

    img = tmp_path / "panel.png"
    Image.new("RGB", (800, 1600), (40, 180, 90)).save(img)
    out = tmp_path / "blur.mp4"
    composer = VideoComposer({
        "resolution": [1280, 720],
        "fps": 30,
        "codec": "libx264",
        "ken_burns": True,
        "ken_burns_intensity": 0.15,
        "image_motion": "random",
        "bg_effect": "blur",
        "blur_background": True,
        "subtitles": False,
    })
    assert composer._make_clip(str(img), None, 0.4, str(out), clip_index=1)
    assert out.exists() and out.stat().st_size > 1000


def test_pan_down_clip_encodes(tmp_path):
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg yok")
    from PIL import Image
    from core.video_composer import VideoComposer

    img = tmp_path / "panel.png"
    Image.new("RGB", (800, 1600), (40, 90, 210)).save(img)
    out = tmp_path / "pan.mp4"
    composer = VideoComposer({
        "resolution": [1280, 720],
        "fps": 30,
        "codec": "libx264",
        "ken_burns": True,
        "ken_burns_intensity": 0.20,
        "image_motion": "pan_down",
        "bg_effect": "none",
        "subtitles": False,
    })
    assert composer._make_clip(str(img), None, 0.4, str(out), clip_index=0)
    assert out.exists() and out.stat().st_size > 1000


def test_copy_output_falls_back_when_locked(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"abc")
    dst.write_bytes(b"old")

    real_copy = __import__("shutil").copy2
    calls = {"n": 0}

    def flaky_copy(a, b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Permission denied", str(b))
        return real_copy(a, b)

    monkeypatch.setattr("core.video_composer.shutil.copy2", flaky_copy)
    result = _copy_output(str(src), str(dst))
    assert result != str(dst)
    assert Path(result).exists()
    assert Path(result).read_bytes() == b"abc"


def _composer_without_ffmpeg(settings, monkeypatch):
    from core.video_composer import VideoComposer
    monkeypatch.setattr("core.ffmpeg_helper.get_ffmpeg_path", lambda: "ffmpeg")
    return VideoComposer(settings)


def test_failed_clips_keep_dense_timeline(tmp_path, monkeypatch):
    from core.models import Chapter, SegmentData

    composer = _composer_without_ffmpeg({
        "resolution": [64, 64],
        "fps": 15,
        "codec": "libx264",
        "subtitles": False,
    }, monkeypatch)
    chapter = Chapter(
        id="c",
        name="n",
        segments=[SegmentData(i, f"line {i}", duration=1.0) for i in range(4)],
    )

    def fake_ph(duration, output_path, cancel_check=None):
        Path(output_path).write_bytes(b"clip" * 50)
        return True

    monkeypatch.setattr(composer, "_make_placeholder_clip", fake_ph)
    paths = composer._build_segment_clips(chapter, tmp_path, lambda *a: None, None)
    assert len(paths) == 4


def test_quality_args_honor_bitrate_flag(monkeypatch):
    crf = _composer_without_ffmpeg({"codec": "libx264", "bitrate": "8000k"}, monkeypatch)
    assert "-crf" in crf._quality_args()
    abr = _composer_without_ffmpeg(
        {"codec": "libx264", "bitrate": "8000k", "use_bitrate": True}, monkeypatch,
    )
    args = abr._quality_args()
    assert "-b:v" in args
    assert "8000k" in args


def test_estimate_size_without_bitrate_flag_uses_crf_scale():
    from core.models import Chapter, SegmentData
    from core.video_composer import estimate_render_size
    chapter = Chapter(id="c", name="n", segments=[SegmentData(0, "hi", duration=10.0)])
    est = estimate_render_size(chapter, {"resolution": [1920, 1080], "bitrate": "50000k"})
    # 50 Mbps kullanıcı değeri encode'a gitmiyor; CRF tahmini ~8 Mbps × 10s ≈ 10 MB.
    assert est["size_mb"] < 20
