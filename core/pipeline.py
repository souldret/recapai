"""
RecapAI - Tek tıkla pipeline yardımcıları.
Checkpoint, maliyet tahmini, render ayarı, çıktı adı, kapak karesi.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STAGE_ANALYSIS = "analysis"
STAGE_SCRIPT = "script"
STAGE_TTS = "tts"
STAGE_RENDER = "render"


def _usable_analysis(data: Any) -> bool:
    if not isinstance(data, dict) or data.get("error") or data.get("parse_error"):
        return False
    return bool(
        data.get("scene")
        or data.get("action")
        or data.get("characters")
        or data.get("dialogues")
        or data.get("summary")
    )


def analysis_complete(chapter) -> bool:
    images = getattr(chapter, "images", None) or []
    if not images:
        return False
    data = getattr(chapter, "analysis_data", None) or {}
    for i in range(len(images)):
        if not _usable_analysis(data.get(str(i))):
            return False
    return True


def script_complete(chapter) -> bool:
    segs = getattr(chapter, "segments", None) or []
    return bool(segs) and any((s.text or "").strip() for s in segs)


def tts_complete(chapter) -> bool:
    segs = getattr(chapter, "segments", None) or []
    voiced = [s for s in segs if (s.text or "").strip()]
    if not voiced:
        return False
    return all(
        getattr(s, "audio_path", None) and Path(s.audio_path).exists()
        for s in voiced
    )


def pending_stages(chapter, *, force: bool = False) -> List[str]:
    """Eksik aşamaları döner; üst aşama eksikse alt aşamalar da yeniden çalışır."""
    if force:
        return [STAGE_ANALYSIS, STAGE_SCRIPT, STAGE_TTS, STAGE_RENDER]
    need_analysis = not analysis_complete(chapter)
    need_script = need_analysis or not script_complete(chapter)
    need_tts = need_script or not tts_complete(chapter)
    stages: List[str] = []
    if need_analysis:
        stages.append(STAGE_ANALYSIS)
    if need_script:
        stages.append(STAGE_SCRIPT)
    if need_tts:
        stages.append(STAGE_TTS)
    stages.append(STAGE_RENDER)
    return stages


def next_incomplete_step(project) -> Tuple[str, str]:
    """
    Returns (page_name, label) for Home 'devam et' kartı.
    page_name: images | analysis | script | tts | render | projects
    """
    if project is None:
        return "projects", "Proje oluştur veya aç"
    chapters = getattr(project, "chapters", None) or []
    if not chapters:
        return "projects", "Bölüm ekle"
    chapter = chapters[0]
    if not getattr(chapter, "images", None):
        return "images", "Görsel ekle"
    if not analysis_complete(chapter):
        return "analysis", "AI analizini tamamla"
    if not script_complete(chapter):
        return "script", "Script üret"
    if not tts_complete(chapter):
        return "tts", "Seslendirmeyi bitir"
    return "render", "Videoyu render et"


def step_badges(project) -> Dict[int, str]:
    """
    Sidebar sayfa index → empty | partial | done
    2 görseller, 3 analiz, 4 script, 5 tts, 6 render
    """
    badges = {2: "empty", 3: "empty", 4: "empty", 5: "empty", 6: "empty"}
    if project is None:
        return badges
    chapters = getattr(project, "chapters", None) or []
    if not chapters:
        return badges
    ch = chapters[0]
    n_img = len(getattr(ch, "images", None) or [])
    if n_img:
        badges[2] = "done"
    if analysis_complete(ch):
        badges[3] = "done"
    elif getattr(ch, "analysis_data", None):
        badges[3] = "partial"
    if script_complete(ch):
        badges[4] = "done"
    elif getattr(ch, "segments", None):
        badges[4] = "partial"
    if tts_complete(ch):
        badges[5] = "done"
    elif any(getattr(s, "audio_path", None) for s in (getattr(ch, "segments", None) or [])):
        badges[5] = "partial"
    out_dir = None
    try:
        from core.project_manager import get_project_dir
        pd = get_project_dir(project)
        if pd:
            out_dir = pd / "output"
    except Exception:
        out_dir = None
    if out_dir and out_dir.exists() and any(out_dir.glob("*.mp4")):
        badges[6] = "done"
    return badges


def estimate_pipeline_cost(
    chapter,
    vision_model: str,
    script_model: str,
    skip_analysis: bool = False,
    skip_script: bool = False,
) -> Dict[str, Any]:
    from core.panel_ai import VISION_RATES, estimate_vision_cost

    pages = max(1, len(getattr(chapter, "images", None) or []))
    vision = {"usd_low": 0.0, "usd_high": 0.0, "label": vision_model, "pages": 0}
    if not skip_analysis:
        remaining = pages
        data = getattr(chapter, "analysis_data", None) or {}
        remaining = sum(
            1 for i in range(pages) if not _usable_analysis(data.get(str(i)))
        )
        if remaining:
            vision = estimate_vision_cost(vision_model, remaining)
            vision["pages"] = remaining

    rates = VISION_RATES.get(script_model) or {"in": 3.0, "out": 15.0, "label": script_model}
    script = {"usd_low": 0.0, "usd_high": 0.0, "label": rates.get("label", script_model)}
    if not skip_script:
        # ~4k in + 2k out per chapter as rough recap script
        usd = (4000 / 1_000_000.0) * rates["in"] + (2500 / 1_000_000.0) * rates["out"]
        usd = max(usd, 0.002)
        script = {
            "label": rates.get("label", script_model),
            "usd_low": round(usd * 0.5, 4),
            "usd_high": round(usd * 2.2, 4),
        }

    return {
        "vision": vision,
        "script": script,
        "usd_low": round(float(vision.get("usd_low") or 0) + script["usd_low"], 4),
        "usd_high": round(float(vision.get("usd_high") or 0) + script["usd_high"], 4),
    }


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or "Recap"


def output_filename(project, chapter) -> str:
    series = _safe_filename(getattr(project, "name", "") or "Recap")
    ch = _safe_filename(getattr(chapter, "name", "") or "Bolum")
    return f"{series} - {ch} Recap.mp4"


def pick_hero_image(chapter) -> Optional[str]:
    images = getattr(chapter, "images", None) or []
    if not images:
        return None
    try:
        from core.beat_engine import cluster_beats, panel_score
    except Exception:
        return images[0].path
    try:
        beats = cluster_beats(chapter, target_minutes=6)
        best_idx = 0
        best = -1.0
        if beats:
            hero = beats[0].hero_index
            data = (getattr(chapter, "analysis_data", None) or {}).get(str(hero), {})
            best_idx = hero
            best = panel_score(data)
            for b in beats:
                data = (getattr(chapter, "analysis_data", None) or {}).get(str(b.hero_index), {})
                sc = panel_score(data) + (0.15 if b.important else 0)
                if sc > best:
                    best = sc
                    best_idx = b.hero_index
        else:
            for i, _img in enumerate(images):
                data = (getattr(chapter, "analysis_data", None) or {}).get(str(i), {})
                sc = panel_score(data)
                if sc > best:
                    best = sc
                    best_idx = i
        idx = max(0, min(best_idx, len(images) - 1))
        return images[idx].path
    except Exception:
        return images[0].path


def export_youtube_thumbnail(chapter, video_path: str) -> Optional[str]:
    src = pick_hero_image(chapter)
    if not src or not Path(src).exists():
        return None
    dest = Path(video_path).with_suffix(".jpg")
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((1280, 720), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (1280, 720), (10, 10, 14))
            x = (1280 - im.width) // 2
            y = (720 - im.height) // 2
            canvas.paste(im, (x, y))
            canvas.save(dest, "JPEG", quality=88, optimize=True)
        return str(dest)
    except Exception as exc:
        logger.warning("Kapak karesi yazılamadı: %s", exc)
        return None


def resolve_render_settings(app_state=None) -> Dict[str, Any]:
    """Kayıtlı preset + GPU codec. UI yokken pipeline için."""
    settings: Dict[str, Any] = {
        "resolution": [1920, 1080],
        "fps": 30,
        "codec": "libx264",
        "bitrate": "12000k",
        "transitions": "fade",
        "transition_duration": 0.5,
        "ken_burns": True,
        "ken_burns_intensity": 0.15,
        "image_motion": "zoom_in",
        "blur_background": False,
        "bg_effect": "none",
        "subtitles": True,
        "subtitle_style": {
            "font": "Arial", "size": 48,
            "color": "#ffffff", "stroke_color": "#000000",
            "stroke_width": 2, "position": "bottom",
        },
        "bgm_path": None, "bgm_volume": 0.15, "bgm_ducking": True,
        "intro_path": None, "outro_path": None,
        "watermark_path": None,
    }
    try:
        from core.render_presets import get_preset, list_presets
        from core.settings_manager import SettingsManager
        sm = SettingsManager.instance()
        name = sm.get("render.last_preset", "") or ""
        names = list_presets()
        if name and name in names:
            preset = get_preset(name)
            if preset:
                settings.update(preset)
        elif names:
            preset = get_preset(names[0])
            if preset:
                settings.update(preset)
    except Exception as exc:
        logger.debug("Render preset okunamadı: %s", exc)

    try:
        from core.ffmpeg_helper import get_available_gpu_encoders
        gpus = get_available_gpu_encoders()
        current = settings.get("codec") or "libx264"
        if current == "libx264" and gpus:
            settings["codec"] = gpus[0]
    except Exception as exc:
        logger.debug("GPU codec tespiti atlandı: %s", exc)
    return settings


def ensure_character_bible(project, chapter) -> int:
    if project is None or chapter is None:
        return 0
    try:
        from core.character_bible import extract_records_from_chapter, get_entries, upsert_characters
        upsert_characters(
            project,
            extract_records_from_chapter(chapter, project),
            getattr(chapter, "id", ""),
        )
        return len(get_entries(project))
    except Exception as exc:
        logger.warning("Character bible güncellenemedi: %s", exc)
        return 0


def require_character_bible(project, chapter) -> int:
    """Analizden bible'ı doldurur; isim yoksa ValueError."""
    n = ensure_character_bible(project, chapter)
    from core.character_bible import has_named_characters
    if not has_named_characters(project):
        raise ValueError(
            "Karakter bible boş. Analiz karakter ismi üretmedi; "
            "AI Analiz sayfasından kadroyu doldurup tekrar dene."
        )
    return n
