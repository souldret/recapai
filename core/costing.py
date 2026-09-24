"""
RecapAI - Token / USD maliyet tahmini.
Kaba sayfa tahmini + gerçek usage kaydı.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.panel_ai import VISION_RATES, estimate_vision_cost


def rate_for(model: str) -> Dict[str, Any]:
    return VISION_RATES.get(model) or {"in": 3.0, "out": 15.0, "label": model}


def usd_from_usage(model: str, usage: Dict[str, Any]) -> float:
    rates = rate_for(model)
    prompt = float(usage.get("prompt_tokens") or 0)
    completion = float(usage.get("completion_tokens") or 0)
    total = float(usage.get("total_tokens") or 0)
    if prompt <= 0 and completion <= 0 and total > 0:
        prompt = total * 0.7
        completion = total * 0.3
    usd = (prompt / 1_000_000.0) * float(rates["in"]) + (completion / 1_000_000.0) * float(rates["out"])
    return round(max(usd, 0.0), 6)


def estimate_script_usd(model: str, n_beats: int = 10) -> Dict[str, Any]:
    rates = rate_for(model)
    beats = max(4, int(n_beats or 10))
    # outline ~2k in / 1k out + VO chunk'ları
    prompt = 2200 + beats * 280
    completion = 900 + beats * 90
    usd = (prompt / 1_000_000.0) * rates["in"] + (completion / 1_000_000.0) * rates["out"]
    usd = max(usd, 0.002)
    return {
        "label": rates.get("label", model),
        "usd_low": round(usd * 0.5, 4),
        "usd_high": round(usd * 2.2, 4),
        "usd": round(usd, 4),
    }


def estimate_script_plan(
    n_images: int,
    *,
    length: str = "medium",
    language: str = "en",
    model: str = "",
    target_minutes: Optional[float] = None,
    reuse_outline: bool = False,
    hook_variants: int = 1,
) -> Dict[str, Any]:
    """Üretimden önce çağrı sayısı, süre ve maliyet aralığı."""
    from core.beat_engine import MAX_STORY_BEATS
    from core.script_generator import BEAT_CHUNK_SIZE, resolve_target_minutes

    images = max(0, int(n_images or 0))
    key = (length or "medium").lower()
    cap = int(MAX_STORY_BEATS.get(key, MAX_STORY_BEATS["medium"]))
    beats = min(cap, max(1, images)) if images else 0
    minutes = resolve_target_minutes(key, target_minutes, n_images=images or None, language=language)
    chunks = max(1, (beats + BEAT_CHUNK_SIZE - 1) // BEAT_CHUNK_SIZE) if beats else 0
    calls = (0 if reuse_outline else 1) + chunks
    if int(hook_variants or 1) > 1:
        calls += 1
    cost = estimate_script_usd(model or "google/gemini-2.5-flash", beats or 4)
    if reuse_outline:
        cost = {
            **cost,
            "usd_low": round(float(cost["usd_low"]) * 0.55, 4),
            "usd_high": round(float(cost["usd_high"]) * 0.55, 4),
            "usd": round(float(cost["usd"]) * 0.55, 4),
        }
    return {
        "images": images,
        "beats": beats,
        "calls": calls,
        "minutes": minutes,
        "reuse_outline": bool(reuse_outline),
        "usd_low": cost["usd_low"],
        "usd_high": cost["usd_high"],
    }


def estimate_chapter_cost(
    chapter,
    vision_model: str,
    script_model: str,
    *,
    skip_analysis: bool = False,
    skip_script: bool = False,
    usable_fn=None,
    economy_pages: int = 0,
    economy_vision_model: str = "",
) -> Dict[str, Any]:
    pages = max(0, len(getattr(chapter, "images", None) or []))
    vision = {"usd_low": 0.0, "usd_high": 0.0, "usd": 0.0, "label": vision_model, "pages": 0}
    if not skip_analysis and pages:
        data = getattr(chapter, "analysis_data", None) or {}
        remaining = []
        for i in range(pages):
            item = data.get(str(i))
            ok = usable_fn(item) if usable_fn else bool(item)
            if not ok:
                remaining.append(i)
        n_full = len(remaining)
        n_econ = min(int(economy_pages or 0), n_full)
        n_main = max(0, n_full - n_econ)
        usd_low = usd_high = usd = 0.0
        if n_main:
            part = estimate_vision_cost(vision_model, n_main)
            usd_low += float(part.get("usd_low") or 0)
            usd_high += float(part.get("usd_high") or 0)
            usd += float(part.get("usd") or 0)
        if n_econ and economy_vision_model:
            part = estimate_vision_cost(economy_vision_model, n_econ)
            usd_low += float(part.get("usd_low") or 0)
            usd_high += float(part.get("usd_high") or 0)
            usd += float(part.get("usd") or 0)
        elif n_econ:
            part = estimate_vision_cost(vision_model, n_econ)
            usd_low += float(part.get("usd_low") or 0)
            usd_high += float(part.get("usd_high") or 0)
            usd += float(part.get("usd") or 0)
        vision = {
            "label": vision_model,
            "pages": n_full,
            "usd_low": round(usd_low, 4),
            "usd_high": round(usd_high, 4),
            "usd": round(usd, 4),
        }

    n_beats = 10
    try:
        from core.beat_engine import MAX_STORY_BEATS
        n_beats = MAX_STORY_BEATS.get("medium", 10)
    except Exception:
        pass
    script = {"usd_low": 0.0, "usd_high": 0.0, "usd": 0.0, "label": script_model}
    if not skip_script:
        script = estimate_script_usd(script_model, n_beats)

    return {
        "vision": vision,
        "script": script,
        "usd_low": round(float(vision.get("usd_low") or 0) + script["usd_low"], 4),
        "usd_high": round(float(vision.get("usd_high") or 0) + script["usd_high"], 4),
        "usd": round(float(vision.get("usd") or 0) + float(script.get("usd") or 0), 4),
    }


def session_cost(events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    total = 0.0
    tokens = 0
    for ev in events or []:
        total += usd_from_usage(str(ev.get("model") or ""), ev.get("usage") or {})
        tokens += int((ev.get("usage") or {}).get("total_tokens") or 0)
    return {"usd": round(total, 4), "tokens": tokens, "calls": len(events or [])}
