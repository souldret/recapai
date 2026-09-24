"""
RecapAI - Recap script kalite kapısı.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from core.models import SegmentData

# (severity, pattern, message_tr)
_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    ("error", re.compile(r"\b(protagonist|main\s*character|\bmc\b|ana\s*karakter|kahramanımız|our\s+hero)\b", re.I),
     "Jenerik karakter etiketi"),
    ("error", re.compile(
        r"\b(we see|görüyoruz|ekranda|bu panelde|this panel|in this scene|"
        r"on screen|the panel shows|bu karede|şu karede|o karede)\b",
        re.I,
    ),
     "Görsel tasvir / panel meta"),
    ("warn", re.compile(r"\b(like and subscribe|beğen.*abone|abone ol|kanalımıza)\b", re.I),
     "Outro / CTA"),
    ("warn", re.compile(r"\b(hoş geldiniz|welcome back|this chapter|bu bölümde)\b", re.I),
     "Zayıf açılış kalıbı"),
    ("warn", re.compile(r"\b(peki ya sizce|what do you think|yorumlara)\b", re.I),
     "İzleyiciye soru"),
    ("warn", re.compile(r"(sonra\s+){2,}|(and then\s+){2,}|(suddenly\s+){2,}", re.I),
     "Sonra/suddenly spam"),
    ("warn", re.compile(r"\b[A-ZÇĞİÖŞÜ]{8,}\b"),
     "ALL CAPS vurgu"),
    ("warn", re.compile(r"\b[A-ZÇĞİÖŞÜ]{4,}(?:\s+[A-ZÇĞİÖŞÜ]{4,})+\b"),
     "ALL CAPS vurgu"),
    ("warn", re.compile(
        r"\b(yellow\s+hair|black\s+hair|blonde\s+woman|kızıl\s+saçlı|kizil\s+sacli|"
        r"dark-haired|dark\s+hair(?:ed)?\s+man)\b",
        re.I,
    ),
     "Görsel saç/görünüm etiketi"),
    ("warn", re.compile(
        r"\b(frantically|completely flustered|keeps staring|becomes completely|"
        r"panik içinde|tamamen şaşkın)\b",
        re.I,
    ),
     "Ekran caption'ı / duygu tarifi"),
    ("error", re.compile(
        r"\b(pressure shifts on this beat|next cut tightens|story drives the next move|"
        r"on this beat|bu beat)\b",
        re.I,
    ),
     "Sahte beat filler"),
]


def _word_count(text: str) -> int:
    return len((text or "").split())


def lint_text(text: str, *, role: str = "", is_first: bool = False, is_last: bool = False) -> List[str]:
    issues: List[str] = []
    raw = (text or "").strip()
    if not raw:
        return issues
    wc = _word_count(raw)
    if role == "filler":
        if wc > 20:
            issues.append(f"Dolgu beat çok uzun ({wc} kelime)")
    elif role == "cold_open":
        if wc > 24:
            issues.append(f"Açılış kancası uzun ({wc} kelime)")
        if wc < 8:
            issues.append("Açılış kancası çok kısa")
    elif role == "last_time":
        if wc > 24:
            issues.append(f"Last time uzun ({wc} kelime)")
    else:
        if wc > 28:
            issues.append(f"Segment uzun ({wc} kelime)")
        if 0 < wc <= 3:
            issues.append(f"Segment kırıntı ({wc} kelime)")

    for _sev, pat, msg in _PATTERNS:
        if pat.search(raw):
            issues.append(f"{msg}")

    if is_first:
        low = raw.lower()
        if low.startswith(("bu bölüm", "this chapter", "bugün", "today ", "hoş gel", "welcome")):
            issues.append("İlk cümle kanca değil")
    if is_last:
        low = raw.lower()
        if any(x in low for x in ("bölüm bitti", "that's all", "beğen", "subscribe", "görüşürüz", "end of")):
            issues.append("Son cümle outro")
    return issues


def lint_segments(segments: List[SegmentData]) -> List[SegmentData]:
    n = len(segments)
    for i, seg in enumerate(segments):
        if not (seg.text or "").strip():
            seg.lint_issues = []
            continue
        seg.lint_issues = lint_text(
            seg.text,
            role=seg.role or "",
            is_first=i == 0,
            is_last=i == n - 1,
        )
    # Ardışık "sonra" zinciri
    then_run = 0
    for seg in segments:
        t = (seg.text or "").strip().lower()
        if t.startswith("sonra") or t.startswith("and then") or t.startswith("daha sonra"):
            then_run += 1
            if then_run >= 3:
                if "Sonra zinciri" not in seg.lint_issues:
                    seg.lint_issues.append("Sonra zinciri")
        else:
            then_run = 0
    caption_run = 0
    for seg in segments:
        t = (seg.text or "").strip()
        wc = _word_count(t)
        role = (seg.role or "").lower()
        if t and wc <= 16 and role not in ("cold_open", "cliffhanger", "filler", "last_time"):
            caption_run += 1
            if caption_run >= 3 and "Panel caption zinciri" not in (seg.lint_issues or []):
                seg.lint_issues.append("Panel caption zinciri")
        else:
            caption_run = 0
    return segments


def issue_count(segments: List[SegmentData]) -> int:
    return sum(len(s.lint_issues or []) for s in segments)


def worst_indices(segments: List[SegmentData], limit: int = 8) -> List[int]:
    ranked = sorted(
        ((i, len(s.lint_issues or [])) for i, s in enumerate(segments)),
        key=lambda x: x[1],
        reverse=True,
    )
    return [i for i, n in ranked if n > 0][:limit]
