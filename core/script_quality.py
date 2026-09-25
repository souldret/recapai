"""
RecapAI - Script kalite döngüsü: lint → hatalı beat'leri yeniden yaz → doğrula.
A/B kanca metinleri VO'ya gömülmez; script_meta'da tutulur.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from core.models import SegmentData
from core.script_linter import issue_count, lint_segments

logger = logging.getLogger(__name__)

_ERROR_HINTS = (
    "jenerik karakter",
    "görsel tasvir",
    "panel meta",
    "kanca değil",
    "outro",
    "saç/görünüm",
    "sahte beat filler",
    "isim gizli",
    "ham diyalog / çevrilmemiş alıntı",
)


def _is_error_issue(msg: str) -> bool:
    low = (msg or "").lower()
    return any(h in low for h in _ERROR_HINTS)


def _apply_rewrite(generator, chapter, seg: SegmentData, text: str, language: str, index: int) -> None:
    from core.script_generator import (
        ScriptGenerator,
        _clip_to_budget,
        _is_meta_filler,
        _scrub_generic_labels,
        _wrong_language,
    )
    from core.script_linter import lint_text
    from core.beat_engine import panel_word_cap

    cleaned = (text or "").strip().strip('"').strip("`")
    if cleaned.lower().startswith("text:"):
        cleaned = cleaned[5:].strip().strip('"')
    cleaned = _scrub_generic_labels(cleaned, language, project=None)
    role = getattr(seg, "role", "") or "beat"
    cleaned = _clip_to_budget(cleaned, panel_word_cap("medium", role))
    from core.text_utils import normalize_caps
    cleaned = normalize_caps(cleaned)
    if len(cleaned) < 5 or _wrong_language(cleaned, language) or _is_meta_filler(cleaned):
        raise ValueError("Toplu düzeltme kullanılamadı.")
    seg.text = cleaned
    seg.duration = ScriptGenerator.estimate_duration(cleaned, language)
    image_idx = int(getattr(seg, "image_index", 0) or 0)
    if not seg.image_path and chapter is not None:
        images = getattr(chapter, "images", None) or []
        if 0 <= image_idx < len(images):
            seg.image_path = getattr(images[image_idx], "path", None)
    n = len(getattr(chapter, "segments", None) or [])
    seg.lint_issues = lint_text(cleaned, role=role, is_first=index == 0, is_last=index == n - 1)


def _rewrite_batch(generator, chapter, indices: List[int], *, model: str, language: str) -> bool:
    """Hatalı satırları tek JSON isteğinde düzeltir. Başarısızsa False döner."""
    from core.script_generator import _extract_json_obj

    chat = getattr(generator, "_chat", None)
    if not callable(chat) or not indices:
        return False
    lines = []
    for idx in indices:
        seg = chapter.segments[idx]
        issues = " | ".join(getattr(seg, "lint_issues", None) or [])
        lines.append(
            f'{idx}. role={getattr(seg, "role", "") or "beat"} issues={issues}\n'
            f'   text: {(seg.text or "").strip()}'
        )
    en = (language or "").lower().startswith("en")
    prompt = (
        "Rewrite ONLY the numbered lines as third-person story, like reading a book aloud.\n"
        "Keep the same story facts. Do not add new events.\n"
        "Do not describe the picture, hair, clothes, or the room.\n"
        "No generic labels (protagonist, main character, yellow hair).\n"
        "No panel captions (we see, this panel, bu panelde, on screen).\n"
        "No filler (on this beat, pressure shifts).\n"
        + ("Write English only.\n" if en else "Yalnızca Türkçe yaz.\n")
        + "Return JSON only: {\"lines\":[{\"index\":0,\"text\":\"...\"}]}\n\n"
        + "\n".join(lines)
    )
    raw = chat(model, prompt, temperature=0.4, max_tokens=900, language=language, retries=0)
    data = _extract_json_obj(raw) or {}
    rows = data.get("lines") or data.get("rewrites") or []
    if not isinstance(rows, list):
        return False
    applied = 0
    by_index = {int(i) for i in indices}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if idx not in by_index:
            continue
        try:
            _apply_rewrite(generator, chapter, chapter.segments[idx], str(row.get("text") or ""), language, idx)
            applied += 1
        except Exception as exc:
            logger.warning("Toplu lint satırı atlandı [%s]: %s", idx, exc)
    return applied > 0


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
                i for i, seg in enumerate(working)
                if any(_is_error_issue(m) for m in (seg.lint_issues or []))
            ]
            if not error_idx:
                if stream_callback and round_i == 0:
                    stream_callback(f"\n[Lint: {total} uyarı, otomatik düzeltme gerekmedi]\n")
                break
            batch_size = max(1, int(limit or 1))
            batches = [
                error_idx[start:start + batch_size]
                for start in range(0, len(error_idx), batch_size)
            ]
            logger.info(
                "%d hatalı segment var, bu turda %d tanesi işlendi",
                len(error_idx),
                len(error_idx),
            )
            if stream_callback:
                stream_callback(
                    f"\n[Lint tur {round_i + 1}: {len(error_idx)} hatalı segment, "
                    f"bu turda {len(error_idx)} tanesi işlendi]\n"
                )
            for batch in batches:
                if stop_flag and stop_flag():
                    break
                batched = False
                try:
                    batched = _rewrite_batch(
                        generator, chapter, batch, model=model, language=language,
                    )
                except Exception as exc:
                    logger.warning("Toplu lint düzeltmesi düştü, satır satır denenecek: %s", exc)
                    batched = False
                if not batched:
                    for idx in batch:
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
