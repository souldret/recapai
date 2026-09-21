"""
RecapAI - Script kalite döngüsü: lint → hatalı beat'leri yeniden yaz → doğrula.
A/B kanca metinleri VO'ya gömülmez; script_meta'da tutulur.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from core.models import SegmentData
from core.script_linter import issue_count, lint_segments, worst_indices

logger = logging.getLogger(__name__)

_ERROR_HINTS = (
    "jenerik karakter",
    "görsel tasvir",
    "panel meta",
    "kanca değil",
    "outro",
    "saç/görünüm",
    "sahte beat filler",
)


def _is_error_issue(msg: str) -> bool:
    low = (msg or "").lower()
    return any(h in low for h in _ERROR_HINTS)


def cold_open_text(segments: List[SegmentData]) -> str:
    for seg in segments or []:
        if (getattr(seg, "role", "") or "") == "cold_open" and (seg.text or "").strip():
            return seg.text.strip()
    for seg in segments or []:
        if (seg.text or "").strip():
            return seg.text.strip()
    return ""


def apply_selected_hook(
    segments: List[SegmentData],
    hook_text: str,
    language: str = "en",
) -> List[SegmentData]:
    """Seçilen A/B kancayı cold_open (veya ilk konuşulan) segmente yazar."""
    text = (hook_text or "").strip()
    if not text or not segments:
        return segments
    target = None
    for seg in segments:
        if (getattr(seg, "role", "") or "") == "cold_open":
            target = seg
            break
    if target is None:
        for seg in segments:
            if (seg.text or "").strip():
                target = seg
                break
    if target is None:
        return segments
    target.text = text
    from core.script_generator import ScriptGenerator
    target.duration = ScriptGenerator.estimate_duration(text, language)
    return segments


def store_hook_variants(chapter, variants: List[Dict[str, str]], selected: str = "A") -> None:
    meta = dict(getattr(chapter, "script_meta", None) or {})
    cleaned = []
    for item in variants:
        if not isinstance(item, dict):
            continue
        hid = str(item.get("id") or "").strip() or chr(ord("A") + len(cleaned))
        text = str(item.get("text") or "").strip()
        if text:
            cleaned.append({"id": hid, "text": text})
    meta["hook_variants"] = cleaned
    meta["selected_hook"] = selected
    chapter.script_meta = meta


def polish_segments(
    generator,
    chapter,
    segments: List[SegmentData],
    *,
    model: str,
    style: str = "fresh",
    language: str = "en",
    length: str = "medium",
    niche: str = "power_fantasy",
    project=None,
    rounds: int = 2,
    limit: int = 6,
    stop_flag: Optional[Callable[[], bool]] = None,
    stream_callback: Optional[Callable[[str], None]] = None,
    assign: bool = True,
) -> List[SegmentData]:
    """
    Lint hatalı segmentleri regenerate_segment ile düzeltir.
    LLM yoksa / hata olursa mevcut metni korur.

    assign=False iken bölümün kayıtlı segmentleri değiştirilmez. A/B kanca
    ve çok bölümlü derleme bu yüzden ana senaryonun üzerine yazmaz.
    """
    if not segments or generator is None:
        return segments
    backup = list(chapter.segments or [])
    working = list(segments)
    chapter.segments = working
    try:
        for round_i in range(max(1, rounds)):
            if stop_flag and stop_flag():
                break
            lint_segments(working)
            total = issue_count(working)
            error_idx = [
                i for i in worst_indices(working, limit=limit)
                if any(_is_error_issue(m) for m in (working[i].lint_issues or []))
            ]
            if not error_idx:
                if stream_callback and round_i == 0:
                    stream_callback(f"\n[Lint: {total} uyarı, otomatik düzeltme gerekmedi]\n")
                break
            if stream_callback:
                stream_callback(f"\n[Lint tur {round_i + 1}: {len(error_idx)} beat yeniden yazılıyor]\n")
            for idx in error_idx:
                if stop_flag and stop_flag():
                    break
                try:
                    generator.regenerate_segment(
                        chapter, idx, model,
                        style=style, language=language, length=length,
                        niche=niche, project=project,
                    )
                except Exception as exc:
                    logger.warning("Lint rewrite atlandı [%d]: %s", idx, exc)
            working = list(chapter.segments or working)
            lint_segments(working)
            if issue_count(working) >= total:
                break
        return working
    finally:
        chapter.segments = working if assign else backup
