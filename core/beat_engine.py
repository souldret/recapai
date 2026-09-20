"""
RecapAI - Panel → hikâye beat kümeleme.
YouTube recap: her panel ayrı VO değil; 2–6 panel = bir beat.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_BEAT_PANELS = 4
MIN_BEAT_PANELS = 1
FILLER_MAX_PANELS = 3
MAX_STORY_BEATS = {"short": 8, "medium": 10, "long": 12}

_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "with",
    "bir", "bu", "şu", "o", "ve", "ile", "için", "gibi", "daha", "çok",
    "is", "are", "was", "they", "he", "she", "it", "this", "that",
    "scene", "sahne", "panel",
}


@dataclass
class StoryBeat:
    beat_id: int
    panel_indices: List[int]
    role: str  # cold_open | last_time | setup | beat | rehook | cliffhanger | filler
    important: bool = False
    score: float = 0.0
    summary: str = ""
    characters: List[str] = field(default_factory=list)
    dialogues: List[str] = field(default_factory=list)
    mood: str = ""
    setting: str = ""
    action: str = ""
    scene: str = ""
    word_budget: int = 28

    @property
    def lead_index(self) -> int:
        return self.panel_indices[0] if self.panel_indices else 0

    @property
    def hero_index(self) -> int:
        return self.panel_indices[min(len(self.panel_indices) // 2, len(self.panel_indices) - 1)]


def _tokens(text: str) -> set:
    words = re.findall(r"[A-Za-zÀ-ÿÇĞİÖŞÜçğıöşü0-9\-]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _analysis_at(chapter, index: int) -> Dict[str, Any]:
    data = (getattr(chapter, "analysis_data", None) or {}).get(str(index), {})
    return data if isinstance(data, dict) else {}


def panel_score(data: Dict[str, Any]) -> float:
    if not data or data.get("error") or data.get("parse_error"):
        return 0.12
    score = 0.22
    if data.get("important") is True:
        score += 0.55
    dialogues = data.get("dialogues") or []
    if isinstance(dialogues, str):
        dialogues = [dialogues] if dialogues.strip() else []
    if dialogues:
        score += min(0.22, 0.08 * len(dialogues))
    action = str(data.get("action") or "")
    scene = str(data.get("scene") or "")
    blob = (action + " " + scene).lower()
    for kw in (
        "ölüm", "ihanet", "rank", "power", "sistem", "system", "betray",
        "kill", "death", "kiss", "aşk", "savaş", "fight", "reveal", "sır",
        "cliff", "tehdit", "threat", "awak", "level", "one-shot", "intikam",
        "revenge", "secret", "lies", "yalan",
    ):
        if kw in blob:
            score += 0.12
            break
    chars = data.get("characters") or []
    if isinstance(chars, list) and len(chars) >= 2:
        score += 0.08
    if len(action) > 80:
        score += 0.06
    return min(1.0, score)


def _panel_fingerprint(data: Dict[str, Any]) -> Tuple[set, str, str]:
    scene = str(data.get("scene") or "")
    action = str(data.get("action") or "")
    setting = str(data.get("setting") or "").strip().lower()
    mood = str(data.get("mood") or "").strip().lower()
    toks = _tokens(scene + " " + action + " " + setting)
    chars = data.get("characters") or []
    if isinstance(chars, list):
        for c in chars:
            if isinstance(c, str):
                toks.add(c.lower())
            elif isinstance(c, dict):
                n = c.get("name") or c.get("character") or ""
                if n:
                    toks.add(str(n).lower())
                for extra in (c.get("appearance"), *(c.get("aliases") or [])):
                    if extra:
                        toks.add(str(extra).lower())
    return toks, setting, mood


def _should_merge(prev: Dict[str, Any], cur: Dict[str, Any], cur_len: int) -> bool:
    if cur_len >= MAX_BEAT_PANELS:
        return False
    if cur.get("important") is True and prev.get("important") is not True:
        return False
    if prev.get("important") is True and panel_score(cur) < 0.4:
        return False
    pt, pset, pmood = _panel_fingerprint(prev)
    ct, cset, cmood = _panel_fingerprint(cur)
    if not pt or not ct:
        return cur_len < FILLER_MAX_PANELS and panel_score(cur) < 0.35
    overlap = len(pt & ct) / max(1, min(len(pt), len(ct)))
    same_place = bool(pset and cset and (pset == cset or pset in cset or cset in pset))
    same_mood = bool(pmood and cmood and pmood == cmood)
    if overlap >= 0.28 or same_place:
        return True
    if same_mood and overlap >= 0.12 and cur_len < FILLER_MAX_PANELS:
        return True
    if panel_score(cur) < 0.28 and panel_score(prev) < 0.4 and cur_len < FILLER_MAX_PANELS:
        return True
    return False


def _role_for(beat_i: int, total: int, important: bool, score: float) -> str:
    # Cold open ayrı flash-forward klip; ilk hikâye beat'i setup.
    # Rehook burada verilmez — tek gerçek dönüş _assign_single_rehook ile seçilir.
    if total <= 1:
        return "setup"
    if beat_i == 0:
        return "setup"
    if beat_i == total - 1:
        return "cliffhanger"
    if score < 0.32:
        return "filler"
    return "beat"


def _assign_single_rehook(beats: List[StoryBeat], start: int = 0) -> None:
    """Gövdeye en fazla BİR rehook koy: en yüksek skorlu orta beat."""
    body = [
        (i, b) for i, b in enumerate(beats)
        if i >= start and b.role in ("beat", "filler")
    ]
    if len(beats) - start < 4 or not body:
        return
    lo = start + max(1, (len(beats) - start) // 4)
    hi = start + max(lo + 1, int((len(beats) - start) * 0.75))
    mid = [(i, b) for i, b in body if lo <= i < hi] or body
    best_i, best_b = max(mid, key=lambda x: (x[1].score, x[1].important))
    if best_i <= start or best_i >= len(beats) - 1:
        return
    best_b.role = "rehook"
    best_b.important = True


def _collapse_to_cap(groups: List[List[int]], cap: int) -> List[List[int]]:
    """Çok fazla beat varsa komşuları birleştirerek tavanın altına iner."""
    if cap <= 0 or len(groups) <= cap:
        return groups
    out = [list(g) for g in groups]
    while len(out) > cap:
        best_i = 0
        best_n = len(out[0]) + len(out[1])
        for i in range(len(out) - 1):
            n = len(out[i]) + len(out[i + 1])
            if n < best_n:
                best_n = n
                best_i = i
        out[best_i] = out[best_i] + out[best_i + 1]
        del out[best_i + 1]
    return out


LENGTH_MINUTES = {"short": 2.5, "medium": 6.0, "long": 9.0}
# Tek görsel tavanı MAX_WORDS_PER_PANEL. Çok panelli beat toplamı n * per.
LENGTH_CAPS = {
    "short": {"beat": 28, "filler": 16, "cold_open": 22, "last_time": 18, "setup": 32, "rehook": 32, "cliffhanger": 24},
    "medium": {"beat": 26, "filler": 20, "cold_open": 22, "last_time": 22, "setup": 26, "rehook": 26, "cliffhanger": 24},
    "long": {"beat": 32, "filler": 24, "cold_open": 24, "last_time": 24, "setup": 32, "rehook": 32, "cliffhanger": 28},
}
# Recap temposu: bir still ~10 sn'den uzun tutulmasın (EN ~160 wpm → 26 kelime ≈ 9.8 sn).
MAX_WORDS_PER_PANEL = {"short": 18, "medium": 26, "long": 32}


def panel_word_cap(length: str = "medium", role: str = "beat") -> int:
    key = (length or "medium").lower()
    per = MAX_WORDS_PER_PANEL.get(key, MAX_WORDS_PER_PANEL["medium"])
    role = (role or "beat").lower()
    if role == "filler":
        return min(per, 16 if key != "long" else 20)
    if role in ("cold_open", "last_time"):
        return min(per, 22)
    if role == "cliffhanger":
        return min(per, 24)
    return per


def _word_budget(
    role: str,
    n_panels: int,
    target_minutes: Optional[float],
    total_beats: int,
    length: str = "medium",
) -> int:
    """Kelime bütçesi görsel temposuna kilitli. Dakika hedefi tek kareyi şişirmez."""
    n_panels = max(1, int(n_panels or 1))
    caps = LENGTH_CAPS.get(length, LENGTH_CAPS["medium"])
    per = panel_word_cap(length, role)
    role_cap = caps.get(role, caps["beat"])
    panel_cap = n_panels * per
    if length == "short":
        return max(8, min(role_cap, panel_cap))
    boost = {
        "cold_open": 1.0,
        "last_time": 0.9,
        "setup": 1.0,
        "rehook": 1.05,
        "cliffhanger": 1.0,
        "filler": 0.7,
        "beat": 1.0,
    }.get(role, 1.0)
    words = int(per * n_panels * boost)
    try:
        minutes = float(target_minutes) if target_minutes else 0.0
    except (TypeError, ValueError):
        minutes = 0.0
    if minutes > 0 and total_beats > 0:
        share = int(minutes * 150.0 / max(1, total_beats))
        if share > 0:
            words = min(words, max(per * n_panels, share))
    floor = 8 if role == "filler" else max(12, min(per, 16) if n_panels == 1 else per)
    return max(floor, min(panel_cap, words))


def cluster_beats(
    chapter,
    target_minutes: Optional[float] = None,
    include_last_time: bool = False,
    length: str = "medium",
    project=None,
) -> List[StoryBeat]:
    n = len(getattr(chapter, "images", None) or [])
    if n == 0:
        return []

    groups: List[List[int]] = []
    current: List[int] = [0]
    prev = _analysis_at(chapter, 0)
    for i in range(1, n):
        cur = _analysis_at(chapter, i)
        if _should_merge(prev, cur, len(current)):
            current.append(i)
        else:
            groups.append(current)
            current = [i]
        prev = cur
    groups.append(current)
    cap = MAX_STORY_BEATS.get((length or "medium").lower(), MAX_STORY_BEATS["medium"])
    groups = _collapse_to_cap(groups, cap)

    raw: List[StoryBeat] = []
    for gi, idxs in enumerate(groups):
        analyses = [_analysis_at(chapter, i) for i in idxs]
        scores = [panel_score(a) for a in analyses]
        important = any(a.get("important") is True for a in analyses)
        best = analyses[scores.index(max(scores))] if analyses else {}
        from core.script_generator import _sanitize_characters
        chars: List[str] = []
        dialogues: List[str] = []
        for a in analyses:
            for c in _sanitize_characters(a.get("characters") or [], project=project):
                if c not in chars:
                    chars.append(c)
            d = a.get("dialogues") or []
            if isinstance(d, str) and d.strip():
                dialogues.append(d.strip())
            elif isinstance(d, list):
                for item in d[:2]:
                    if isinstance(item, str) and item.strip():
                        dialogues.append(item.strip())
        beat = StoryBeat(
            beat_id=gi,
            panel_indices=list(idxs),
            role="beat",
            important=important,
            score=max(scores) if scores else 0.0,
            summary=str(best.get("action") or best.get("scene") or "").strip(),
            characters=chars[:6],
            dialogues=dialogues[:4],
            mood=str(best.get("mood") or ""),
            setting=str(best.get("setting") or ""),
            action=str(best.get("action") or ""),
            scene=str(best.get("scene") or ""),
        )
        raw.append(beat)

    if include_last_time and raw:
        raw[0].role = "last_time"
        start = 1
    else:
        start = 0

    total = len(raw)
    for i, beat in enumerate(raw):
        if i < start:
            continue
        logical = i - start
        logical_total = max(1, total - start)
        beat.role = _role_for(logical, logical_total, beat.important, beat.score)

    _assign_single_rehook(raw, start)

    for beat in raw:
        beat.word_budget = _word_budget(
            beat.role, len(beat.panel_indices), target_minutes, max(1, len(raw)), length=length,
        )

    logger.info(
        "Beat kümeleme: %d panel → %d beat (roller: %s)",
        n, len(raw), ", ".join(f"{b.role}:{len(b.panel_indices)}" for b in raw),
    )
    return raw


def pick_cold_open_image(chapter, beats: List[StoryBeat]) -> int:
    """Açılış görseli: en yüksek skorlu panel (kapak yürüyüşü değil)."""
    n = len(getattr(chapter, "images", None) or [])
    if n == 0:
        return 0
    best_i = 0
    best_s = -1.0
    # Son ~%35'i spoiler: kanca görseli final twist'i göstermesin.
    limit = n if n <= 4 else max(3, int(n * 0.65))
    for i in range(limit):
        s = panel_score(_analysis_at(chapter, i))
        if i < 2:
            s *= 0.72
        if s > best_s:
            best_s = s
            best_i = i
    if beats:
        candidates = [b for b in beats if b.lead_index < limit]
        if candidates:
            hero = max(candidates, key=lambda b: (b.score, b.important))
            hero_i = hero.hero_index if hero.hero_index < limit else hero.lead_index
            if panel_score(_analysis_at(chapter, hero_i)) >= best_s * 0.9:
                return hero_i
    return best_i


def detect_niche(chapter) -> str:
    """Analiz metninden niş tahmini."""
    blob_parts: List[str] = []
    for key, data in (getattr(chapter, "analysis_data", None) or {}).items():
        if not str(key).isdigit() or not isinstance(data, dict):
            continue
        blob_parts.append(str(data.get("scene") or ""))
        blob_parts.append(str(data.get("action") or ""))
        blob_parts.append(str(data.get("mood") or ""))
        blob_parts.append(str(data.get("setting") or ""))
    blob = " ".join(blob_parts).lower()
    scores = {
        "power_fantasy": 0,
        "romance": 0,
        "dark_action": 0,
        "comedy": 0,
    }
    for kw in ("system", "sistem", "rank", "level", "dungeon", "hunter", "stat", "awak", "skill", "isekai"):
        if kw in blob:
            scores["power_fantasy"] += 2
    for kw in ("kiss", "aşk", "love", "kalp", "blush", "evlilik", "marriage", "date", "romantik"):
        if kw in blob:
            scores["romance"] += 2
    for kw in ("intikam", "revenge", "kan", "blood", "öldür", "kill", "ihanet", "betray", "dark", "korku"):
        if kw in blob:
            scores["dark_action"] += 2
    for kw in ("komik", "gül", "joke", "slapstick", "utanç", "comedy", "funny"):
        if kw in blob:
            scores["comedy"] += 2
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "power_fantasy"
    return best


def format_beats_for_prompt(beats: List[StoryBeat], language: str = "tr") -> str:
    en = (language or "").lower().startswith("en")
    lines: List[str] = []
    if en:
        lines.append(
            "NOTE: Scene/Action/Dialogue below are SOURCE notes and may be Turkish. "
            "TRANSLATE into English spoken VO. Never copy Turkish words."
        )
    else:
        lines.append(
            "NOT: Sahne/Aksiyon/Diyalog kaynak notudur; başka dilde olabilir. "
            "Türkçe VO yaz. Kaynağı kopyalama, çevir."
        )
    from core.script_generator import _source_note
    for b in beats:
        panels = ", ".join(str(i) for i in b.panel_indices)
        if b.characters:
            chars = ", ".join(b.characters)
        else:
            chars = "(no name — pronoun)" if en else "(isim yok — zamir)"
        dlg = "; ".join(b.dialogues[:2])
        flag = " [KEY]" if en else " [ÖNEMLİ]"
        if not (b.important or b.role in ("cold_open", "rehook", "cliffhanger")):
            flag = ""
        scene = _source_note(b.scene, language, b.characters)
        action = _source_note(b.action, language, b.characters)
        dialogue = _source_note(dlg, language, b.characters) if dlg else ""
        if en:
            block = [
                f"BEAT {b.beat_id} role={b.role} panels=[{panels}] budget={b.word_budget} words{flag}",
                f"Scene: {scene}" if scene else "",
                f"Action: {action}" if action else "",
                f"Characters: {chars}",
            ]
            if dialogue:
                block.append(f"Dialogue: {dialogue}")
            mood = _source_note(b.mood, language, b.characters)
            setting = _source_note(b.setting, language, b.characters)
            if mood:
                block.append(f"Mood: {mood}")
            if setting:
                block.append(f"Setting: {setting}")
        else:
            block = [
                f"BEAT {b.beat_id} role={b.role} panels=[{panels}] budget={b.word_budget} kelime{flag}",
                f"Sahne: {scene}" if scene else "",
                f"Aksiyon: {action}" if action else "",
                f"Karakterler: {chars}",
            ]
            if dialogue:
                block.append(f"Diyalog: {dialogue}")
            mood = _source_note(b.mood, language, b.characters)
            setting = _source_note(b.setting, language, b.characters)
            if mood:
                block.append(f"Atmosfer: {mood}")
            if setting:
                block.append(f"Mekan: {setting}")
        lines.append(" | ".join(p for p in block if p))
    return "\n".join(lines)
