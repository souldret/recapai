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


def apply_selected_hook(segments: List[SegmentData], hook_text: str) -> List[SegmentData]:
    """Seçilen A/B kancayı cold_open (veya ilk konuşulan) segmente yazar."""
    text = (hook_text or "").strip()
    if not text or not segments:
        return segments
    for seg in segments:
        if (getattr(seg, "role", "") or "") == "cold_open":
            seg.text = text
            return segments
    for seg in segments:
        if (seg.text or "").strip():
            seg.text = text
            break
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
) -> List[SegmentData]:
    """
    Lint hatalı segmentleri regenerate_segment ile düzeltir.
    LLM yoksa / hata olursa mevcut metni korur.
    """
    if not segments or generator is None:
        return segments
    chapter.segments = segments
    for round_i in range(max(1, rounds)):
        if stop_flag and stop_flag():
            break
        lint_segments(segments)
        total = issue_count(segments)
        error_idx = [
            i for i in worst_indices(segments, limit=limit)
            if any(_is_error_issue(m) for m in (segments[i].lint_issues or []))
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
        segments = list(chapter.segments or segments)
        lint_segments(segments)
        if issue_count(segments) >= total:
            break
    chapter.segments = segments
    return segments
