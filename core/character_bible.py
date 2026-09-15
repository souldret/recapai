"""
RecapAI - Seri karakter hafızası ve önceki bölüm özeti.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BIBLE_KEY = "character_bible"

_ALIAS_STRIP_RE = re.compile(r"[^\w\-]+", re.UNICODE)


def _norm(name: str) -> str:
    return _ALIAS_STRIP_RE.sub(" ", (name or "").strip().lower()).strip()


def _ensure_entry(ent: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ent)
    out["canonical"] = (out.get("canonical") or "").strip()
    aliases = []
    seen = {_norm(out["canonical"])} if out["canonical"] else set()
    for alias in out.get("aliases") or []:
        a = str(alias).strip()
        k = _norm(a)
        if not a or k in seen:
            continue
        seen.add(k)
        aliases.append(a)
    out["aliases"] = aliases
    out["gender"] = (out.get("gender") or "").strip().lower()
    if out["gender"] not in {"male", "female", "unknown"}:
        out["gender"] = ""
    out["appearance"] = (out.get("appearance") or "").strip()
    out["notes"] = (out.get("notes") or "").strip()
    out["first_seen_chapter"] = out.get("first_seen_chapter") or ""
    return out


def has_named_characters(project) -> bool:
    """Script kapısı: en az bir kanonik isim kayıtlı mı."""
    return any((e.get("canonical") or "").strip() for e in get_entries(project))


def get_entries(project) -> List[Dict[str, Any]]:
    if project is None:
        return []
    settings = getattr(project, "settings", None)
    if not isinstance(settings, dict):
        return []
    raw = settings.get(BIBLE_KEY) or {}
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("entries") or []
    else:
        entries = []
    out: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("canonical"):
            continue
        out.append(_ensure_entry(e))
    return out


def set_entries(project, entries: List[Dict[str, Any]]) -> None:
    if project is None:
        return
    if not isinstance(getattr(project, "settings", None), dict):
        project.settings = {}
    cleaned = [_ensure_entry(e) for e in entries if isinstance(e, dict) and e.get("canonical")]
    project.settings[BIBLE_KEY] = {"entries": cleaned}


def _index_entries(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_norm: Dict[str, Dict[str, Any]] = {}
    for ent in entries:
        canon = _norm(ent.get("canonical") or "")
        if canon:
            by_norm[canon] = ent
        for alias in ent.get("aliases") or []:
            key = _norm(str(alias))
            if key:
                by_norm[key] = ent
    return by_norm


def _hair_tokens(text: str) -> set:
    low = _norm(text)
    if not low:
        return set()
    colors = {
        "yellow", "golden", "blonde", "black", "brown", "red", "white",
        "silver", "pink", "grey", "gray", "dark", "sari", "sarı", "kizil",
        "kızıl", "siyah", "beyaz", "kumral", "esmer", "altin", "altın",
        "gold", "redhead", "brunette",
    }
    return {tok for tok in low.split() if tok in colors}


def resolve_name(project, query: str, entries: Optional[List[Dict[str, Any]]] = None) -> str:
    """Görünüm / alias / kanonik metni kadrodaki tek isme çevirir."""
    raw = (query or "").strip()
    if not raw:
        return ""
    key = _norm(raw)
    if not key:
        return ""
    pool = entries if entries is not None else get_entries(project)
    if not pool:
        return ""

    by_norm = _index_entries(pool)
    hit = by_norm.get(key)
    if hit:
        return hit.get("canonical") or ""

    from core.script_generator import _looks_like_appearance, _is_generic_character_label

    appearance_hits: List[Dict[str, Any]] = []
    color_hits: List[Dict[str, Any]] = []
    q_colors = _hair_tokens(raw)
    for ent in pool:
        app = (ent.get("appearance") or "").strip()
        if not app:
            continue
        app_key = _norm(app)
        if not app_key:
            continue
        if app_key == key or key in app_key or app_key in key:
            appearance_hits.append(ent)
            continue
        if q_colors and q_colors <= _hair_tokens(app):
            color_hits.append(ent)

    unique = appearance_hits if len(appearance_hits) == 1 else (
        color_hits if len(color_hits) == 1 and not appearance_hits else []
    )
    if len(unique) == 1:
        return unique[0].get("canonical") or ""

    if _looks_like_appearance(raw) or _is_generic_character_label(raw):
        return ""
    return ""


def _merge_into(ent: Dict[str, Any], *, alias: str = "", gender: str = "",
                appearance: str = "", notes: str = "", chapter_id: str = "") -> None:
    if alias:
        aliases = list(ent.get("aliases") or [])
        if alias != ent.get("canonical") and alias not in aliases:
            aliases.append(alias)
            ent["aliases"] = aliases
    if gender and not ent.get("gender"):
        ent["gender"] = gender
    if appearance and not ent.get("appearance"):
        ent["appearance"] = appearance
    if notes and not ent.get("notes"):
        ent["notes"] = notes
    if chapter_id and not ent.get("first_seen_chapter"):
        ent["first_seen_chapter"] = chapter_id


def upsert_characters(project, records: List[Dict[str, Any]], chapter_id: str = "") -> List[Dict[str, Any]]:
    """İsim/alias/görünüm kayıtlarını bible'a yazar; görünümü kanonik isme bağlar."""
    from core.script_generator import (
        _is_generic_character_label,
        _looks_like_appearance,
        _normalize_gender,
        _parse_character_record,
    )

    entries = get_entries(project)
    by_norm = _index_entries(entries)

    for raw in records or []:
        rec = _parse_character_record(raw) if not isinstance(raw, dict) or "canonical" in (raw or {}) else dict(raw)
        if isinstance(raw, dict) and raw.get("canonical") and not rec.get("name"):
            rec = {
                "name": raw.get("canonical") or raw.get("name") or "",
                "aliases": list(raw.get("aliases") or []),
                "gender": raw.get("gender") or "",
                "appearance": raw.get("appearance") or "",
                "notes": raw.get("notes") or "",
            }
        name = (rec.get("name") or rec.get("canonical") or "").strip()
        aliases = [str(a).strip() for a in (rec.get("aliases") or []) if str(a).strip()]
        gender = _normalize_gender(rec.get("gender"))
        appearance = (rec.get("appearance") or "").strip()
        notes = (rec.get("notes") or "").strip()

        if name and (_is_generic_character_label(name) or _looks_like_appearance(name)):
            if _looks_like_appearance(name) and not appearance:
                appearance = name
            name = ""

        resolved = ""
        for query in [name, appearance, *aliases]:
            resolved = resolve_name(project, query, entries)
            if resolved:
                break
        if resolved:
            ent = by_norm.get(_norm(resolved))
            if ent is None:
                continue
            if name and name != ent["canonical"]:
                _merge_into(ent, alias=name)
                by_norm[_norm(name)] = ent
            for alias in aliases:
                if not _is_generic_character_label(alias):
                    _merge_into(ent, alias=alias)
                    by_norm[_norm(alias)] = ent
            _merge_into(ent, gender=gender, appearance=appearance, notes=notes, chapter_id=chapter_id)
            continue

        if not name or _is_generic_character_label(name):
            continue

        key = _norm(name)
        existing = by_norm.get(key)
        if existing:
            for alias in aliases:
                if not _is_generic_character_label(alias):
                    _merge_into(existing, alias=alias)
                    by_norm[_norm(alias)] = existing
            _merge_into(existing, gender=gender, appearance=appearance, notes=notes, chapter_id=chapter_id)
            continue

        ent = _ensure_entry({
            "canonical": name,
            "aliases": [a for a in aliases if a != name and not _is_generic_character_label(a)],
            "gender": gender,
            "appearance": appearance,
            "notes": notes,
            "first_seen_chapter": chapter_id,
        })
        entries.append(ent)
        by_norm[key] = ent
        for alias in ent["aliases"]:
            by_norm[_norm(alias)] = ent

    set_entries(project, entries)
    return entries


def upsert_names(project, names: List[str], chapter_id: str = "") -> List[Dict[str, Any]]:
    """İsimleri bible'a ekler; alias çakışmasında kanonik adı korur."""
    records = [{"name": n, "aliases": [], "gender": "", "appearance": ""} for n in (names or [])]
    return upsert_characters(project, records, chapter_id)


def extract_records_from_chapter(chapter, project=None) -> List[Dict[str, Any]]:
    from core.script_generator import _sanitize_character_records

    records: List[Dict[str, Any]] = []
    analysis = getattr(chapter, "analysis_data", None) or {}
    for key, data in analysis.items():
        if not str(key).isdigit() or not isinstance(data, dict) or data.get("error"):
            continue
        records.extend(
            _sanitize_character_records(data.get("characters", []), project=project)
        )
    merged: List[Dict[str, Any]] = []
    seen = set()
    unnamed: List[Dict[str, Any]] = []
    for rec in records:
        name = (rec.get("name") or "").strip()
        if not name:
            if rec.get("appearance"):
                unnamed.append(rec)
            continue
        k = _norm(name)
        if k in seen:
            for prev in merged:
                if _norm(prev.get("name") or "") == k:
                    if rec.get("appearance") and not prev.get("appearance"):
                        prev["appearance"] = rec["appearance"]
                    if rec.get("gender") and not prev.get("gender"):
                        prev["gender"] = rec["gender"]
                    for alias in rec.get("aliases") or []:
                        if alias not in prev["aliases"]:
                            prev["aliases"].append(alias)
                    break
            continue
        seen.add(k)
        merged.append({
            "name": name,
            "aliases": list(rec.get("aliases") or []),
            "gender": rec.get("gender") or "",
            "appearance": rec.get("appearance") or "",
            "notes": rec.get("notes") or "",
        })
    merged.extend(unnamed)
    return merged


def extract_names_from_chapter(chapter) -> List[str]:
    names: List[str] = []
    seen = set()
    for rec in extract_records_from_chapter(chapter):
        n = (rec.get("name") or "").strip()
        k = _norm(n)
        if not n or k in seen:
            continue
        seen.add(k)
        names.append(n)
    return names


def prune_generic_entries(project, persist: bool = True) -> List[Dict[str, Any]]:
    from core.script_generator import _is_generic_character_label, _looks_like_appearance
    kept = []
    for ent in get_entries(project):
        canon = (ent.get("canonical") or "").strip()
        if not canon or _is_generic_character_label(canon) or _looks_like_appearance(canon):
            continue
        aliases = [
            a for a in (ent.get("aliases") or [])
            if a and a != canon
            and not _is_generic_character_label(str(a))
            and not _looks_like_appearance(str(a))
        ]
        ent = _ensure_entry(ent)
        ent["canonical"] = canon
        ent["aliases"] = aliases
        kept.append(ent)
    if persist:
        set_entries(project, kept)
    return kept


def canonical_names(project) -> List[str]:
    return [e["canonical"] for e in get_entries(project) if e.get("canonical")]


def get_entry(project, canonical: str) -> Optional[Dict[str, Any]]:
    key = _norm(canonical)
    if not key:
        return None
    for ent in get_entries(project):
        if _norm(ent.get("canonical") or "") == key:
            return ent
    return None


def update_character(
    project,
    old_canonical: str,
    *,
    canonical: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    gender: Optional[str] = None,
    appearance: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Kadro kaydını düzenler; verilen alanları override eder (boş bırakmak siler)."""
    from core.script_generator import (
        _is_generic_character_label,
        _looks_like_appearance,
        _normalize_gender,
    )

    old = (old_canonical or "").strip()
    if not old or project is None:
        return None
    entries = get_entries(project)
    old_key = _norm(old)
    target = None
    for ent in entries:
        if _norm(ent.get("canonical") or "") == old_key:
            target = ent
            break
    if target is None:
        return None

    merged = False
    renamed_from = ""
    if canonical is not None:
        new_name = (canonical or "").strip()
        if not new_name or _is_generic_character_label(new_name) or _looks_like_appearance(new_name):
            return None
        new_key = _norm(new_name)
        other = None
        for ent in entries:
            if ent is target:
                continue
            if _norm(ent.get("canonical") or "") == new_key:
                other = ent
                break
        if other is not None:
            prev_name = target.get("canonical") or ""
            _merge_into(other, alias=prev_name)
            for alias in target.get("aliases") or []:
                _merge_into(other, alias=str(alias))
            _merge_into(
                other,
                gender=target.get("gender") or "",
                appearance=target.get("appearance") or "",
                notes=target.get("notes") or "",
                chapter_id=target.get("first_seen_chapter") or "",
            )
            entries = [e for e in entries if e is not target]
            target = other
            merged = True
        else:
            prev_name = target.get("canonical") or ""
            if prev_name and _norm(prev_name) != new_key:
                als = list(target.get("aliases") or [])
                if prev_name not in als:
                    als.append(prev_name)
                target["aliases"] = als
                renamed_from = prev_name
            target["canonical"] = new_name

    if aliases is not None:
        incoming = [str(a).strip() for a in aliases if str(a).strip()]
        if merged:
            incoming = list(target.get("aliases") or []) + incoming
        elif renamed_from:
            # UI alias alanı eski kanoniği içermez; isim değişince kaybolmasın.
            incoming = incoming + [renamed_from]
        cleaned: List[str] = []
        seen = {_norm(target.get("canonical") or "")}
        for alias in incoming:
            key = _norm(alias)
            if (
                not alias or key in seen
                or _is_generic_character_label(alias)
                or _looks_like_appearance(alias)
            ):
                continue
            seen.add(key)
            cleaned.append(alias)
        target["aliases"] = cleaned
    if gender is not None:
        target["gender"] = _normalize_gender(gender)
    if appearance is not None:
        target["appearance"] = (appearance or "").strip()
    if notes is not None:
        target["notes"] = (notes or "").strip()

    set_entries(project, entries)
    return get_entry(project, target.get("canonical") or "")


def format_known_for_vision(project) -> List[str]:
    """Vision prompt'u için kadro satırları (isim + görünüm)."""
    lines: List[str] = []
    for ent in get_entries(project):
        canon = (ent.get("canonical") or "").strip()
        if not canon:
            continue
        bits = [canon]
        aliases = [a for a in (ent.get("aliases") or []) if a and a != canon]
        if aliases:
            bits.append("aka " + ", ".join(aliases[:3]))
        if ent.get("gender"):
            bits.append(str(ent["gender"]))
        if ent.get("appearance"):
            bits.append(str(ent["appearance"]))
        lines.append(" — ".join(bits))
    return lines


def apply_names_to_beats(beats, project) -> None:
    """Beat'te geçen veya görünümü eşleşen isimleri yazar; tüm kadroyu her beat'e basmaz."""
    from core.script_generator import _is_generic_character_label

    names = canonical_names(project)
    entries = get_entries(project)
    if not names:
        return
    for b in beats:
        blob = " ".join(
            str(x) for x in (b.scene, b.action, b.summary, " ".join(b.characters or []))
        ).lower()
        ordered: List[str] = []

        def _add(n: str) -> None:
            n = (n or "").strip()
            if n and n not in ordered and not _is_generic_character_label(n):
                ordered.append(n)

        for c in list(b.characters or []):
            _add(resolve_name(project, c) or ("" if _is_generic_character_label(c) else c))
        for n in names:
            if n.lower() in blob:
                _add(n)
        for ent in entries:
            app = (ent.get("appearance") or "").strip()
            if app and app.lower() in blob:
                _add(ent.get("canonical") or "")
            for alias in ent.get("aliases") or []:
                a = str(alias).strip()
                if a and a.lower() in blob:
                    _add(ent.get("canonical") or "")
        b.characters = ordered[:8]


def format_for_prompt(project, language: str = "tr") -> str:
    entries = (
        prune_generic_entries(project, persist=False)
        if project is not None else get_entries(project)
    )
    if not entries:
        return ""
    lines = []
    for ent in entries[:40]:
        canon = ent["canonical"]
        aliases = [a for a in (ent.get("aliases") or []) if a and a != canon]
        extra = []
        if aliases:
            extra.append("aka: " + ", ".join(aliases[:4]))
        if ent.get("gender"):
            extra.append(str(ent["gender"]))
        if ent.get("appearance"):
            extra.append("looks: " + str(ent["appearance"]))
        if extra:
            lines.append(f"- {canon} ({'; '.join(extra)})")
        else:
            lines.append(f"- {canon}")
    header = (
        "KARAKTER KADROSU — bu isimleri VO'da kullan, he/she veya sarı saç/black hair ile gizleme. "
        "Görsel tarif (blonde/yellow hair/kızıl saçlı) YASAK; kadrodaki kanonik ismi söyle:"
    )
    if (language or "").startswith("en"):
        header = (
            "NAMED CAST — say these names in the voiceover when they appear. "
            "Do not hide them behind he/she, yellow hair, black hair, or blonde woman. "
            "Never write hair-color labels:"
        )
    return header + "\n" + "\n".join(lines)


def previous_chapter(project, chapter):
    if project is None or chapter is None:
        return None
    chapters = list(getattr(project, "chapters", None) or [])
    for i, ch in enumerate(chapters):
        if ch is chapter or getattr(ch, "id", None) == getattr(chapter, "id", None):
            return chapters[i - 1] if i > 0 else None
    return None


def last_time_source(project, chapter, max_chars: int = 700) -> str:
    """Önceki bölümün son anlatısından kısa 'last time on' kaynağı."""
    prev = previous_chapter(project, chapter)
    if prev is None:
        return ""
    parts: List[str] = []
    segs = [s for s in (getattr(prev, "segments", None) or []) if getattr(s, "text", "").strip()]
    if segs:
        for seg in segs[-4:]:
            parts.append(seg.text.strip())
    else:
        analysis = getattr(prev, "analysis_data", None) or {}
        keys = sorted(analysis.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)
        for key in keys[-8:]:
            data = analysis.get(key) or {}
            if not isinstance(data, dict) or data.get("error"):
                continue
            bit = (data.get("action") or data.get("scene") or "").strip()
            if bit:
                parts.append(bit)
    text = " ".join(parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def chapter_stakes(chapter, language: str = "tr", max_items: int = 6) -> str:
    analysis = getattr(chapter, "analysis_data", None) or {}
    lines: List[str] = []
    keys = sorted(
        (k for k in analysis.keys() if str(k).isdigit()),
        key=lambda k: int(k),
    )
    for key in keys:
        data = analysis.get(key) or {}
        if not isinstance(data, dict) or data.get("error"):
            continue
        if data.get("important") is not True:
            continue
        bit = (data.get("action") or data.get("scene") or "").strip()
        if bit:
            lines.append(bit.rstrip(" .;:"))
        if len(lines) >= max_items:
            break
    if not lines:
        return ""
    en = (language or "").startswith("en")
    title = "OPEN STAKES (source notes — TRANSLATE, never copy):" if en else "AÇIK STAKES (kaynak — çevir, kopyalama):"
    return title + "\n- " + "\n- ".join(lines)
