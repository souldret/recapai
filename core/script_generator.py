"""
RecapAI - Script üretim motoru.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.models import Chapter, SegmentData
from core.openrouter_client import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)

# Kelime/dakika oranları (TTS hızına göre)
WPM = {"tr": 150, "en": 160}

LANGUAGE_LABELS = {"tr": "Türkçe", "en": "English"}

_TR_CHARS_RE = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TR_WORDS_RE = re.compile(
    r"\b(bir|bu|şu|ile|için|gibi|daha|çok|olarak|sahne|sahnenin|"
    r"hikaye|hikayenin|hikâye|hikâyenin|"
    r"karakter|karakterin|aksiyon|okuyucu|okuyucuya|"
    r"gücünü|tehlikenin|seviyesini|yükseltir|yükselterek|"
    r"çatışma|öykü|öyküsel|maruz|boyutunu|"
    r"bölüm|sonra|çünkü|karşı|üzerine|içinde|kızıl|kizil|saçlı|sacli|"
    r"pelerinli|görüyoruz|ekranda|şimdi|artık|ancak|fakat|lakin|değil|"
    r"onun|onların|kendini|kendisine|üzerinden|yüzünden|dolayı)\b",
    re.IGNORECASE,
)
_TR_SUFFIX_RE = re.compile(
    r"[A-Za-zçğıöşüÇĞİÖŞÜ]{3,}(ıyor|iyor|uyor|üyor|yorsun|yoruz|"
    r"acak|ecek|miş|mış|muş|müş)\b",
    re.IGNORECASE,
)

# Geriye dönük: panel chunk (eski yol). Yeni yol beat chunk kullanır.
CHUNK_SIZE = 40
BEAT_CHUNK_SIZE = 10
LENGTH_MINUTES = {"short": 4.0, "medium": 6.0, "long": 9.0}

# Manhwa Fresh niş modülleri (PDF Prompt 2)
DEFAULT_NICHE = "power_fantasy"
VALID_NICHES = ("power_fantasy", "romance", "dark_action", "comedy")


def _load_prompts() -> dict:
    try:
        return json.loads((Path(__file__).resolve().parent.parent / "config" / "prompts.json").read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("prompts.json yüklenemedi: %s", exc)
        return {}


def list_niches() -> List[dict]:
    """UI için niş modül listesi: [{id, label, description}, ...]."""
    prompts = _load_prompts()
    niches = prompts.get("script_niches", {})
    result = []
    for key in VALID_NICHES:
        data = niches.get(key, {})
        if isinstance(data, dict):
            result.append({
                "id": key,
                "label": data.get("label", key),
                "description": data.get("description", ""),
            })
        else:
            result.append({"id": key, "label": key, "description": ""})
    return result


def _resolve_niche_prompt(prompts: dict, niche: str) -> str:
    niches = prompts.get("script_niches", {})
    key = niche if niche in niches else DEFAULT_NICHE
    data = niches.get(key, {})
    if isinstance(data, dict):
        return data.get("prompt", "")
    return str(data) if data else ""


def _is_first_chapter(chapter: Chapter) -> bool:
    """
    Bölümün serinin ilk bölümü olup olmadığını tahmin eder.
    name / order üzerinden sezgisel kontrol.
    'Chapter 12' gibi çok haneli numaralarda yanlış pozitif vermez.
    """
    name = (getattr(chapter, "name", "") or "").strip().lower()

    # Sayıyı yakala: "chapter 1", "bölüm 01", "ep.1", "#1", başta "1 - ..."
    # Sonra sadece 1 kabul et (01 dahil, 10/11/12 değil)
    num_patterns = (
        r"\b(?:ch(?:apter)?|ep(?:isode)?|part|bölüm|bolum)\s*\.?\s*0*1\b",
        r"#\s*0*1\b",
        r"^0*1(?:\b|[\s\-:._])",
    )
    for pat in num_patterns:
        if re.search(pat, name, re.IGNORECASE):
            return True

    order = getattr(chapter, "order", None)
    if order is not None:
        try:
            if int(order) == 1 or int(order) == 0:
                return True
        except (TypeError, ValueError):
            pass

    index = getattr(chapter, "index", None)
    if index is not None:
        try:
            if int(index) == 1 or int(index) == 0:
                return True
        except (TypeError, ValueError):
            pass

    return False


# İtici / jenerik etiketler — prompt'a ve metne girmemeli
_BANNED_LABEL_RE = re.compile(
    r"\b("
    r"protagonist|main\s*character|main\s*char|\bmc\b"
    r"|ana\s*karakter|baş\s*karakter|bas\s*karakter|kahramanımız|our\s*hero"
    r"|the\s*hero|the\s*mc|gizemli\s*yabancı|mysterious\s*stranger"
    r"|gizemli\s*figür|mysterious\s*figure|cloaked\s*figure"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

_FULL_GENERIC_LABEL_RE = re.compile(
    r"^(?:"
    r"(?:(?:bir|a|an|the|some|iki|bu|that|this)\s+)?"
    r"(?:"
    r"figür|figure|adam|kadın|kişi|kız|erkek|çocuk|yaşlı|genç|insan"
    r"|man|woman|person|girl|boy|child|elder|human"
    r"|karakter|character|birisi|someone|biri"
    r"|protagonist|hero|heroine|mc"
    r"|young\s+man|young\s+woman|old\s+man|old\s+woman"
    r"|genç\s+adam|genç\s+kadın|yaşlı\s+adam|yaşlı\s+kadın"
    r")s?"
    r"(?:\s+\d+)?"
    r")$",
    re.IGNORECASE | re.UNICODE,
)

_ROLE_GENERIC_RE = re.compile(
    r"^(?:(?:bir|a|an|the|some|iki|bu|that|this)\s+)?"
    r"(?:şövalye|sovalye|knight|soldier|asker|guard|muhafız|muhafiz|"
    r"miner|madenci|mage|büyücü|buyucu|elf|warrior|savaşçı|savasci|"
    r"zırhlı|zirhli|armored)"
    r"(?:\s+(?:adam|kadın|man|woman|kız|erkek|savaşçı|savasci|şövalye|knight|soldier))?"
    r"(?:\s+\d+)?$",
    re.IGNORECASE | re.UNICODE,
)

_APPEARANCE_RE = re.compile(
    r"\b("
    r"blonde|brunette|redhead|ginger|"
    r"red-?haired|dark-?haired|black-?haired|white-?haired|brown-?haired|"
    r"grey-?haired|gray-?haired|silver-?haired|golden-?haired|pink-?haired|"
    r"yellow-?haired|yellow\s+hair|golden\s+hair|"
    r"dark\s+hair|black\s+hair|white\s+hair|red\s+hair|brown\s+hair|"
    r"saçlı|sacli|saçları|kızıl|kizil|sarışın|esmer|kumral|"
    r"pelerinli|cloaked|hooded|masked|gözlüklü|"
    r"kırmızı\s+pelerin|red\s+cloak"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

_APPEARANCE_PERSON_RE = re.compile(
    r"\b("
    r"blonde|brunette|redhead|muscular|tall|young|old|dark|black|white|"
    r"red|blue|green|yellow|golden|armored|"
    r"kırmızı|siyah|beyaz|sarı|kızıl|esmer|sarışın|zırhlı|zirhli"
    r")\s+("
    r"man|woman|girl|boy|guy|lady|person|figure|knight|"
    r"adam|kadın|kız|erkek|kişi|figür|elf|şövalye"
    r")s?\b",
    re.IGNORECASE | re.UNICODE,
)

_APPEARANCE_NOUN_RE = re.compile(
    r"^(?:the\s+|a\s+|an\s+|bir\s+)?"
    r"(?:[\w\-]+\s+)*(?:haired|hair|saçlı|sacli|pelerinli|cloaked|hooded|masked|zırhlı|zirhli|armored)"
    r"(?:\s+\w+)?$",
    re.IGNORECASE | re.UNICODE,
)

_HAIR_PHRASE_RE = re.compile(
    r"\b(?:the\s+|a\s+|an\s+|bir\s+)?"
    r"(?:(?:bright|dark|light|pale)\s+)?"
    r"(?:yellow|golden|blonde|black|brown|red|white|silver|pink|grey|gray|dark)\s*-?\s*(?:haired|hair)\b",
    re.IGNORECASE | re.UNICODE,
)

_TR_HAIR_PHRASE_RE = re.compile(
    r"\b(?:the\s+|bir\s+)?"
    r"(?:kızıl|kizil|sarı|sari|siyah|beyaz|kumral|esmer|altın|altin)\s+"
    r"(?:saçlı|sacli)(?:\s+\w+)?\b",
    re.IGNORECASE | re.UNICODE,
)

_GENERIC_SINGLE_WORDS = {
    "protagonist", "hero", "heroine", "mc", "character", "karakter",
    "someone", "birisi", "biri", "figure", "figür", "adam", "kadın",
    "man", "woman", "person", "girl", "boy", "kişi", "kız", "erkek",
    "knight", "şövalye", "sovalye", "soldier", "asker", "guard",
    "miner", "madenci", "mage", "elf", "muhafız", "büyücü",
}

_NAME_KEYS = ("name", "character", "char", "label", "id", "canonical")
_NON_NAME_KEYS = {
    "role", "title", "gender", "sex", "appearance", "looks", "desc",
    "description", "aliases", "alias", "notes", "visual",
}


def _is_generic_character_label(name: str) -> bool:
    """İsim etiketi script'e girmemeli mi? Görsel tarifler (blonde woman) de düşer."""
    low = (name or "").strip().lower()
    if not low:
        return True
    if low in _GENERIC_SINGLE_WORDS:
        return True
    if _BANNED_LABEL_RE.search(name):
        return True
    if _FULL_GENERIC_LABEL_RE.match(low):
        return True
    if _ROLE_GENERIC_RE.match(low) or _APPEARANCE_NOUN_RE.match(low):
        return True
    if _APPEARANCE_RE.search(low) or _APPEARANCE_PERSON_RE.search(low):
        return True
    if _HAIR_PHRASE_RE.search(low) or _TR_HAIR_PHRASE_RE.search(low):
        return True
    return False


def _character_name_from_item(raw) -> str:
    """Vision karakter öğesinden gerçek isim. role/title isim sayılmaz."""
    rec = _parse_character_record(raw)
    return (rec.get("name") or "").strip()


def _as_str_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif item is not None:
                s = str(item).strip()
                if s:
                    out.append(s)
        return out
    s = str(value).strip()
    return [s] if s else []


def _normalize_gender(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    s = str(value).strip().lower()
    if s in {"m", "male", "man", "boy", "guy", "he", "him", "erkek", "adam", "oğlan", "oglan"}:
        return "male"
    if s in {"f", "female", "woman", "girl", "lady", "she", "her", "kadın", "kadin", "kız", "kiz"}:
        return "female"
    if s in {"nb", "nonbinary", "non-binary", "they", "them", "other", "unknown"}:
        return "unknown"
    return ""


def _gender_from_phrase(text: str) -> str:
    low = (text or "").strip().lower()
    if not low:
        return ""
    if re.search(r"\b(woman|girl|lady|female|kadın|kadin|kız|kiz|she|her|heroine)\b", low, re.UNICODE):
        return "female"
    if re.search(r"\b(man|boy|guy|male|adam|erkek|oğlan|oglan|şövalye|sovalye|knight|he|him)\b", low, re.UNICODE):
        return "male"
    return ""


def _pronoun_for(gender: str, language: str) -> str:
    if not (language or "").lower().startswith("en"):
        return "o"
    g = _normalize_gender(gender) or gender
    if g == "female":
        return "she"
    if g == "male":
        return "he"
    return "they"


def _looks_like_appearance(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if _APPEARANCE_RE.search(low) or _APPEARANCE_PERSON_RE.search(low):
        return True
    return bool(_APPEARANCE_NOUN_RE.match(low))


def _normalize_known_key(name: str) -> str:
    s = (name or "").strip().lower().replace("–", "-").replace("—", "-")
    s = re.sub(r"[^\w\-]+", " ", s, flags=re.UNICODE)
    return re.sub(r"[\s\-]+", " ", s).strip()


def _match_known_name(name: str, known_names: Optional[List[str]] = None) -> str:
    q = _normalize_known_key(name)
    if not q or not known_names:
        return ""
    for n in known_names:
        kn = (n or "").strip()
        if kn and _normalize_known_key(kn) == q:
            return kn
    return ""


def _parse_character_record(raw) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"name": "", "aliases": [], "gender": "", "appearance": ""}
    if raw is None:
        return rec
    if isinstance(raw, str):
        name = raw.strip()
        rec["name"] = name
        rec["gender"] = _gender_from_phrase(name)
        if _looks_like_appearance(name):
            rec["appearance"] = name
        return rec
    if isinstance(raw, dict):
        name = ""
        for key in _NAME_KEYS:
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                name = val.strip()
                break
        if not name:
            for key, val in raw.items():
                if str(key).lower() in _NON_NAME_KEYS:
                    continue
                if isinstance(val, str) and val.strip():
                    name = val.strip()
                    break
        aliases = _as_str_list(raw.get("aliases") or raw.get("alias"))
        appearance = ""
        for key in ("appearance", "looks", "visual", "desc", "description"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                appearance = val.strip()
                break
        gender = _normalize_gender(raw.get("gender") or raw.get("sex"))
        if not gender:
            gender = _gender_from_phrase(" ".join([name, appearance] + aliases))
        rec.update(name=name, aliases=aliases, gender=gender, appearance=appearance)
        return rec
    if isinstance(raw, (list, tuple)):
        for item in raw:
            parsed = _parse_character_record(item)
            if parsed.get("name") or parsed.get("appearance"):
                return parsed
        return rec
    rec["name"] = str(raw).strip()
    return rec


def _normalize_string_list(items) -> List[str]:
    """dialogues vb. alanları string listesine çevirir."""
    if items is None:
        return []
    if isinstance(items, str):
        s = items.strip()
        return [s] if s else []
    if not isinstance(items, (list, tuple)):
        s = str(items).strip()
        return [s] if s else []
    out: List[str] = []
    for it in items:
        if it is None:
            continue
        if isinstance(it, str):
            s = it.strip()
            if s:
                out.append(s)
        elif isinstance(it, dict):
            # {"text": "..."} / {"dialogue": "..."}
            for key in ("text", "dialogue", "line", "content", "speech"):
                val = it.get(key)
                if isinstance(val, str) and val.strip():
                    out.append(val.strip())
                    break
            else:
                for val in it.values():
                    if isinstance(val, str) and val.strip():
                        out.append(val.strip())
                        break
        else:
            s = str(it).strip()
            if s:
                out.append(s)
    return out


def _sanitize_character_records(
    chars,
    language: str = "tr",
    known_names: Optional[List[str]] = None,
    project=None,
) -> List[Dict[str, Any]]:
    """
    Vision karakter listesini kalıcı kayıtlara çevirir.

    Jenerik etiketler (Protagonist, blonde woman, yellow hair, şövalye) isim olmaz.
    Görünüm kadrodaki tek eşleşmeye çözülürse kanonik isim yazılır.
    İsimsiz ama görünümlü kayıtlar saklanır — sonraki panellerde tanınsın.
    """
    result: List[Dict[str, Any]] = []
    seen_names = set()
    seen_apps = set()
    if chars is None:
        return result
    if isinstance(chars, (str, dict)):
        chars = [chars]
    if not isinstance(chars, (list, tuple)):
        chars = [chars]

    resolve = None
    if project is not None:
        from core.character_bible import resolve_name
        resolve = lambda q: resolve_name(project, q)

    for raw in chars:
        rec = _parse_character_record(raw)
        name = (rec.get("name") or "").strip()
        appearance = (rec.get("appearance") or "").strip()
        aliases = [a for a in (rec.get("aliases") or []) if a and str(a).strip()]
        gender = _normalize_gender(rec.get("gender"))

        if name and _is_generic_character_label(name):
            if _looks_like_appearance(name) and not appearance:
                appearance = name
            name = ""

        clean_aliases: List[str] = []
        for alias in aliases:
            a = str(alias).strip()
            if not a or _is_generic_character_label(a):
                if _looks_like_appearance(a) and not appearance:
                    appearance = a
                continue
            if a not in clean_aliases and a != name:
                clean_aliases.append(a)

        resolved = ""
        if resolve:
            resolved = resolve(name) or resolve(appearance)
            if not resolved:
                for alias in clean_aliases:
                    resolved = resolve(alias)
                    if resolved:
                        break
        if not resolved:
            resolved = _match_known_name(name, known_names)
        if resolved:
            name = resolved
        elif name and _is_generic_character_label(name):
            name = ""

        if not name and not appearance:
            continue

        key = _normalize_known_key(name) if name else f"app:{appearance.lower()}"
        if name:
            if key in seen_names:
                for prev in result:
                    if _normalize_known_key(prev.get("name") or "") == key:
                        if appearance and not prev.get("appearance"):
                            prev["appearance"] = appearance
                        if gender and not prev.get("gender"):
                            prev["gender"] = gender
                        for alias in clean_aliases:
                            if alias not in prev["aliases"]:
                                prev["aliases"].append(alias)
                        break
                continue
            seen_names.add(key)
        else:
            app_key = appearance.lower()
            if app_key in seen_apps:
                continue
            seen_apps.add(app_key)

        result.append({
            "name": name,
            "aliases": clean_aliases,
            "gender": gender,
            "appearance": appearance,
        })
    return result


def _sanitize_characters(
    chars,
    language: str = "tr",
    known_names: Optional[List[str]] = None,
    project=None,
) -> List[str]:
    """
    Vision modelinin ürettiği jenerik karakter etiketlerini temizler.

    'Protagonist', 'blonde woman', 'yellow hair', 'kızıl saçlı', 'bir adam' silinir.
    Yalnız gerçek isimler (Jin-Woo, Varkas) kalır.
    """
    records = _sanitize_character_records(
        chars, language=language, known_names=known_names, project=project,
    )
    result: List[str] = []
    seen = set()
    for rec in records:
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_known_key(name)
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def format_characters_display(chars) -> str:
    """Analiz kartı / UI için kısa kadro metni."""
    records = _sanitize_character_records(chars)
    names = [r["name"] for r in records if (r.get("name") or "").strip()]
    unnamed = sum(1 for r in records if not (r.get("name") or "").strip())
    if names and unnamed:
        return ", ".join(names) + f" +{unnamed}"
    if names:
        return ", ".join(names)
    if unnamed:
        return f"{unnamed} tanımsız"
    return "—"


def _looks_turkish(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _TR_CHARS_RE.search(raw):
        return True
    if _TR_WORDS_RE.search(raw):
        return True
    return bool(_TR_SUFFIX_RE.search(raw))


def _strip_known_names(text: str, names: Optional[List[str]] = None) -> str:
    out = text or ""
    for name in sorted((names or []), key=len, reverse=True):
        n = (name or "").strip()
        if len(n) < 2:
            continue
        out = re.sub(re.escape(n), " ", out, flags=re.IGNORECASE)
    return out


def _wrong_language(text: str, language: str, known_names: Optional[List[str]] = None) -> bool:
    lang = (language or "tr").lower()
    raw = _strip_known_names((text or "").strip(), known_names)
    if not raw.strip():
        return False
    if lang.startswith("en"):
        return _looks_turkish(raw)
    if lang.startswith("tr"):
        if _looks_turkish(raw):
            return False
        letters = re.findall(r"[A-Za-z]+", raw)
        return len(letters) >= 4
    return False


def _language_lock(language: str) -> str:
    if (language or "").lower().startswith("en"):
        return (
            "OUTPUT LANGUAGE = English. Every JSON text field MUST be English. "
            "Vision/analysis notes are often Turkish. TRANSLATE the plot into English spoken VO. "
            "Never copy Turkish words, suffixes, or whole sentences into hook/payload/text. "
            "Forbidden in output: kızıl, saçlı, hikaye, hikâye, sahne, aksiyon, karakter, bölüm, "
            "okuyucuya, yükseltir, çatışma, öyküsel. "
            "Never mix: no 'The kızıl saçlı'. "
            "If the NAMED CAST lists a real name, SAY THAT NAME when the character appears. "
            "Do not hide named characters behind he/she/they or hair-color labels "
            "(yellow hair, black hair, blonde woman, dark-haired man). "
            "If a character has no real name, use he/she/they. Never write those visual labels."
        )
    return (
        "ÇIKTI DİLİ: yalnızca Türkçe. Kaynak notları İngilizce olabilir. "
        "Hikayeyi Türkçe yaz. İngilizce cümle kopyalama. JSON text alanları Türkçe. "
        "Kadrodaki gerçek isimleri VO'da kullan. İsimli karakteri o/he/she veya "
        "sarı saç / black hair / kızıl saçlı ile gizleme. "
        "İsimsiz karakter için zamir kullan. blonde woman / kızıl saçlı yazma."
    )


def resolve_target_minutes(length: str, target_minutes: Optional[float] = None) -> Optional[float]:
    """Elle dakika varsa onu kullan. Yoksa Orta/Uzun için length varsayılanı (6/9 dk). Kısa: sıkı cümle modu."""
    try:
        if target_minutes is not None and float(target_minutes) > 0:
            return float(target_minutes)
    except (TypeError, ValueError):
        pass
    key = (length or "medium").lower()
    if key == "short":
        return None
    return float(LENGTH_MINUTES.get(key, LENGTH_MINUTES["medium"]))


def _source_note(text: str, language: str, known_names: Optional[List[str]] = None) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if not _wrong_language(raw, language, known_names):
        return raw
    if (language or "").lower().startswith("en"):
        return "SOURCE NOTES (Turkish — TRANSLATE into English spoken VO, never copy):\n" + raw
    return "KAYNAK NOTLARI (çevir, kopyalama):\n" + raw


def _sanitize_outline_beats(raw_beats, beats, language: str, known_names: Optional[List[str]] = None) -> List[dict]:
    """Outline payload/stakes'i hedef dile kilitle; Türkçe analiz notunu kopyalama."""
    by_id: Dict[int, dict] = {}
    if isinstance(raw_beats, list):
        for item in raw_beats:
            if not isinstance(item, dict):
                continue
            bid = _safe_int(item.get("id", item.get("beat_id")))
            if bid is None:
                continue
            payload = (item.get("payload") or item.get("text") or "").strip()
            stakes = (item.get("stakes") or "").strip()
            if payload and _wrong_language(payload, language, known_names):
                payload = ""
            if stakes and _wrong_language(stakes, language, known_names):
                stakes = ""
            by_id[bid] = {"id": bid, "payload": payload, "stakes": stakes}
    out: List[dict] = []
    for b in beats:
        item = by_id.get(b.beat_id, {"id": b.beat_id, "payload": "", "stakes": ""})
        payload = (item.get("payload") or "").strip()
        if not payload:
            for candidate in (b.summary, b.action, b.scene):
                cand = (candidate or "").strip()
                if cand and not _wrong_language(cand, language, known_names) and not _is_atmosphere_dump(cand):
                    payload = cand[:180]
                    break
        out.append({
            "id": b.beat_id,
            "payload": payload,
            "stakes": (item.get("stakes") or "").strip(),
        })
    return out


def _duration_note(length: str, target_minutes: float, language: str, chunk, total_beats: int) -> str:
    en = (language or "").lower().startswith("en")
    if length == "short":
        return (
            "SHORT MODE: exactly 1 sentence per beat, max 16 words. No extra paragraphs."
            if en else
            "KISA MOD: her beat 1 cümle, en fazla 16 kelime. "
            "Panel başına ayrı paragraf YASAK. Bütçeyi aşma."
        )
    minutes = target_minutes if target_minutes and target_minutes > 0 else float(
        LENGTH_MINUTES.get(length, LENGTH_MINUTES["medium"])
    )
    wpm = WPM.get("en" if en else "tr", 150)
    total_words = int(minutes * wpm)
    budgets = ", ".join(f"{b.beat_id}:{b.word_budget}" for b in chunk)
    if en:
        return (
            f"TARGET DURATION: ~{minutes:.0f} minutes for the FULL chapter "
            f"(~{total_words} spoken words across {total_beats} beats). "
            f"This chunk beat word budgets: {budgets}. "
            f"Write enough spoken sentences to FILL each beat budget. "
            "Two sentences is a minimum, not the target. Empty JSON text is forbidden."
        )
    return (
        f"HEDEF SÜRE: tüm bölüm ~{minutes:.0f} dakika "
        f"(~{total_words} konuşma kelimesi, {total_beats} beat). "
        f"Bu chunk bütçeleri: {budgets}. "
        "Her beat bütçesini DOLDUR. 2 cümle minimum, hedef değil. Boş text YASAK."
    )


def _clause_text(value) -> str:
    """Prompt satırı için metni sadeleştir; sondaki noktalamayı kırp."""
    if value is None:
        return ""
    if not isinstance(value, str):
        if isinstance(value, dict):
            # {"text": "..."} gibi
            for key in ("text", "description", "scene", "action", "summary"):
                if isinstance(value.get(key), str):
                    value = value[key]
                    break
            else:
                value = " ".join(str(v) for v in value.values() if v)
        elif isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value if v)
        else:
            value = str(value)
    return value.strip().rstrip(" .;:")


_COLOR_TOKENS = {
    "yellow", "golden", "blonde", "black", "brown", "red", "white",
    "silver", "pink", "grey", "gray", "dark", "sarı", "sari", "kızıl",
    "kizil", "siyah", "beyaz", "kumral", "esmer", "altın", "altin",
}

_SCRUB_APPEARANCE_RE = re.compile(
    r"\b(?:the\s+|a\s+|an\s+|bir\s+)?"
    r"(?:"
    r"(?:(?:bright|dark|light|pale)\s+)?"
    r"(?:yellow|golden|blonde|black|brown|red|white|silver|pink|grey|gray|dark|"
    r"kızıl|kizil|sarı|sari|siyah|beyaz|kumral|esmer|altın|altin)"
    r"\s*-?\s*(?:haired|hair|saçlı|sacli)"
    r"(?:\s+(?:man|woman|girl|boy|guy|lady|person|figure|knight|"
    r"adam|kadın|kız|erkek|kişi|figür|elf|şövalye))?"
    r"|"
    r"(?:blonde|brunette|redhead|muscular|tall|young|old|dark|black|white|"
    r"red|blue|green|yellow|golden|armored|"
    r"kırmızı|siyah|beyaz|sarı|kızıl|esmer|sarışın|zırhlı|zirhli)"
    r"\s+"
    r"(?:man|woman|girl|boy|guy|lady|person|figure|knight|"
    r"adam|kadın|kız|erkek|kişi|figür|elf|şövalye)"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def _color_tokens(text: str) -> set:
    low = (text or "").lower()
    return {t for t in re.findall(r"[\w\-]+", low, flags=re.UNICODE) if t in _COLOR_TOKENS}


def _fallback_pronoun(phrase: str, language: str) -> str:
    gender = _gender_from_phrase(phrase)
    if gender:
        return _pronoun_for(gender, language)
    if (language or "").lower().startswith("tr"):
        return "o"
    return "he"


def _cast_records(cast=None, project=None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen = set()
    if project is not None:
        from core.character_bible import get_entries
        for ent in get_entries(project):
            name = (ent.get("canonical") or "").strip()
            key = _normalize_known_key(name)
            if not name or key in seen:
                continue
            seen.add(key)
            records.append({
                "name": name,
                "aliases": list(ent.get("aliases") or []),
                "gender": ent.get("gender") or "",
                "appearance": (ent.get("appearance") or "").strip(),
            })
    if cast is None:
        return records
    if isinstance(cast, (str, dict)):
        cast = [cast]
    for item in cast:
        rec = _parse_character_record(item)
        name = (rec.get("name") or "").strip()
        if name and _is_generic_character_label(name):
            if _looks_like_appearance(name) and not rec.get("appearance"):
                rec["appearance"] = name
            rec["name"] = ""
            name = ""
        key = _normalize_known_key(name) if name else ""
        if name and key in seen:
            continue
        if name:
            seen.add(key)
            records.append(rec)
        elif rec.get("appearance"):
            records.append(rec)
    return records


def _resolve_scrub_label(phrase: str, records: List[Dict[str, Any]], project, language: str) -> str:
    raw = (phrase or "").strip()
    if not raw:
        return _fallback_pronoun("", language)
    resolved = ""
    if project is not None:
        from core.character_bible import resolve_name
        resolved = resolve_name(project, raw)
    if not resolved:
        hits: List[str] = []
        colors = _color_tokens(raw)
        low = raw.lower()
        for rec in records:
            name = (rec.get("name") or "").strip()
            if not name:
                continue
            app = (rec.get("appearance") or "").strip().lower()
            if app and (app in low or low in app):
                hits.append(name)
                continue
            if colors and colors <= _color_tokens(rec.get("appearance") or ""):
                hits.append(name)
        uniq = list(dict.fromkeys(hits))
        if len(uniq) == 1:
            resolved = uniq[0]
    if resolved:
        return resolved
    return _fallback_pronoun(raw, language)


def _unique_cast_name(records: List[Dict[str, Any]]) -> str:
    named = [(r.get("name") or "").strip() for r in records if (r.get("name") or "").strip()]
    uniq = list(dict.fromkeys(named))
    return uniq[0] if len(uniq) == 1 else ""


def _scrub_generic_labels(text: str, language: str = "tr", cast=None, project=None) -> str:
    """
    Üretilmiş script metninden itici etiketleri temizler.
    Görünüm (yellow hair, black hair, kızıl saçlı) kadrodaki isme çözülür.
    """
    if not text:
        return text

    records = _cast_records(cast, project)
    unique = _unique_cast_name(records)
    default = unique or _fallback_pronoun("", language)

    replacements = [
        (r"\bthe\s+main\s+character\b", default),
        (r"\bour\s+main\s+character\b", default),
        (r"\bmain\s+character\b", default),
        (r"\bthe\s+protagonist\b", default),
        (r"\bour\s+protagonist\b", default),
        (r"\bprotagonist(?:imiz|i)?\b", default),
        (r"\bthe\s+MC\b", default),
        (r"\bMC\b", default),
        (r"\bour\s+hero\b", default),
        (r"\bthe\s+hero\b", default),
        (r"\bana\s+karakter(?:imiz|i|in)?\b", default),
        (r"\bbaş\s+karakter(?:imiz|i|in)?\b", default),
        (r"\bbas\s+karakter(?:imiz|i|in)?\b", default),
        (r"\bkahramanımız\b", default),
        (r"\bkahramanı\b", default),
    ]
    out = text
    for pat, repl in replacements:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)

    def _sub_label(match) -> str:
        return _resolve_scrub_label(match.group(0), records, project, language)

    out = _SCRUB_APPEARANCE_RE.sub(_sub_label, out)
    out = _HAIR_PHRASE_RE.sub(_sub_label, out)
    out = _TR_HAIR_PHRASE_RE.sub(_sub_label, out)
    out = _APPEARANCE_PERSON_RE.sub(_sub_label, out)

    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(
        rf"\b({re.escape(default)}|he|she|they|o)\s+\1\b",
        r"\1",
        out,
        flags=re.IGNORECASE,
    )
    out = out.replace("—", ",").replace("–", ",")
    out = out.replace(";", ".")
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r"\.{4,}", "...", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(
        r"(^|(?<!\.)[.!?]\s+)([a-zçğıöşü])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
        flags=re.UNICODE,
    )
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def _extract_json_obj(text: str) -> Optional[dict]:
    """LLM çıktısından ilk JSON objesini çıkarır."""
    raw = (text or "").strip()
    if not raw:
        return None

    def _load(candidate: str) -> Optional[dict]:
        candidate = (candidate or "").strip()
        if not candidate:
            return None
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        start = candidate.find("{")
        if start < 0:
            return None
        try:
            data, _ = json.JSONDecoder().raw_decode(candidate[start:])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    loaded = _load(raw)
    if loaded is not None:
        return loaded
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        loaded = _load(match.group(1))
        if loaded is not None:
            return loaded
    return None


def _safe_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+", str(value or ""))
        return int(match.group(0)) if match else None


def _parse_segments(text: str) -> List[dict]:
    """
    LLM çıktısından segment listesini ayrıştırır.
    JSON bloğu, kod bloğu veya regex fallback dener.
    """
    data = _extract_json_obj(text)
    if data:
        if "segments" in data and isinstance(data["segments"], list):
            return data["segments"]
        if "beats" in data and isinstance(data["beats"], list):
            return data["beats"]

    fallback = []
    for m in re.finditer(
        r'"image_index"\s*:\s*(\d+).*?"text"\s*:\s*"(.*?)"(?=\s*[,}])',
        text, re.DOTALL
    ):
        fallback.append({"image_index": int(m.group(1)), "text": m.group(2)})
    if fallback:
        logger.warning("JSON parse başarısız; regex fallback %d segment buldu.", len(fallback))
        return fallback

    logger.error("Segment ayrıştırılamadı. Ham metin: %s", text[:200])
    return []


def _parse_beats_voiceover(text: str) -> Dict[int, str]:
    data = _extract_json_obj(text) or {}
    out: Dict[int, str] = {}
    items = data.get("beats") or data.get("segments") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id", item.get("beat_id", item.get("image_index")))
            body = (item.get("text") or item.get("payload") or "").strip()
            beat_id = _safe_int(raw_id)
            if beat_id is None or not body:
                continue
            out[beat_id] = body
    hook = (data.get("hook") or "").strip()
    if hook:
        out[-1] = hook
    return out


def _align_vo_to_beats(parsed: Dict[int, str], beat_ids: List[int]) -> Dict[int, str]:
    """LLM 1-tabanlı id veya sırasız liste dönerse beat id'lerine hizalar."""
    if not beat_ids:
        return parsed
    hook = parsed.get(-1, "")
    if all((parsed.get(bid) or "").strip() for bid in beat_ids):
        return parsed
    if all((parsed.get(bid + 1) or "").strip() for bid in beat_ids):
        aligned = {bid: parsed[bid + 1] for bid in beat_ids}
        if hook:
            aligned[-1] = hook
        return aligned
    values = [parsed[k] for k in sorted(k for k in parsed if k >= 0) if (parsed.get(k) or "").strip()]
    aligned: Dict[int, str] = {}
    used = 0
    for bid in beat_ids:
        text = (parsed.get(bid) or "").strip()
        if text:
            aligned[bid] = text
        elif used < len(values):
            aligned[bid] = values[used]
            used += 1
    if hook:
        aligned[-1] = hook
    return aligned


def _split_sentences(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?…])\s+", raw)
    return [p.strip() for p in parts if p.strip()]


def _first_sentence(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    sents = _split_sentences(raw)
    return sents[0] if sents else raw


def _clip_to_budget(text: str, max_words: int) -> str:
    raw = (text or "").strip()
    if max_words <= 0 or not raw:
        return raw
    words = raw.split()
    if len(words) <= max_words:
        return raw
    clipped = " ".join(words[:max_words]).rstrip(" ,;:")
    if clipped and clipped[-1] not in ".!?…":
        clipped += "."
    return clipped


def _is_atmosphere_dump(text: str) -> bool:
    """'Gergin, heybetli, tehditkar' gibi plot'suz mood listesi."""
    raw = (text or "").strip().rstrip(".!?,;:")
    if not raw:
        return False
    parts = [p.strip() for p in re.split(r"[,;/|]", raw) if p.strip()]
    if len(parts) >= 2 and all(len(p.split()) <= 3 for p in parts):
        return True
    words = raw.split()
    if len(words) <= 6 and "," in (text or "") and not re.search(
        r"\b(he|she|they|the|and|opens|hits|walks|screams|blocks|o|bir|bu)\b",
        raw,
        re.I,
    ):
        return True
    return False


def _panel_glue(chapter, index: int, language: str, avoid: str = "", project=None) -> str:
    """Boş panel için yalnızca plot (aksiyon/sahne). Mood asla kopyalanmaz."""
    data = (getattr(chapter, "analysis_data", None) or {}).get(str(index), {})
    if not isinstance(data, dict) or data.get("error") or data.get("parse_error"):
        return ""
    avoid_n = (avoid or "").strip().rstrip(".").lower()
    for key in ("action", "scene"):
        bit = _clause_text(data.get(key) or "")
        bit = _scrub_generic_labels(bit, language, project=project)
        if not bit or _is_atmosphere_dump(bit):
            continue
        if bit.strip().rstrip(".").lower() == avoid_n:
            continue
        if not bit.endswith((".", "!", "?", "…")):
            bit += "."
        bit = _first_sentence(bit)
        if _wrong_language(bit, language):
            continue
        return bit
    return ""


def _spread_parts(parts: List[str], panel_count: int) -> List[str]:
    slots = [""] * panel_count
    n = len(parts)
    if n <= 0:
        return slots
    for i in range(panel_count):
        start = int(i * n / panel_count)
        end = int((i + 1) * n / panel_count)
        if end <= start:
            end = min(n, start + 1)
        slots[i] = " ".join(parts[start:end]).strip()
    return slots


def _distribute_text(text: str, panel_count: int) -> List[str]:
    """Cümleleri panellere yayar. Kelime bölmez; eksik paneller boş kalır (glue doldurur)."""
    if panel_count <= 0:
        return []
    raw = (text or "").strip()
    if not raw:
        return [""] * panel_count
    sentences = _split_sentences(raw)
    if not sentences:
        sentences = [raw]
    if len(sentences) >= panel_count:
        return _spread_parts(sentences, panel_count)
    slots = [""] * panel_count
    for i, s in enumerate(sentences):
        slots[i] = s
    return slots


def _fill_placeholders(template: str, mapping: Dict[str, Any]) -> str:
    result = template or ""
    for key, val in mapping.items():
        if val is None:
            val = ""
        elif not isinstance(val, str):
            val = str(val)
        result = result.replace("{" + key + "}", val)
    return result


class ScriptGenerator:
    """
    chapter.analysis_data'sından LLM ile script üretir.
    """

    def __init__(self, client: Optional[OpenRouterClient] = None) -> None:
        self._client = client or OpenRouterClient.instance()

    def _fallback_models(self) -> list:
        """Ayarlardan script-model fallback zincirini okur."""
        try:
            from core.settings_manager import SettingsManager
            return SettingsManager.instance().get("api.script_fallback_models", []) or []
        except Exception:
            return []

    # ── Ana Üretim ─────────────────────────────────────────────────

    def generate_script(
        self,
        chapter: Chapter,
        model: str,
        style: str = "fresh",
        length: str = "medium",
        language: str = "tr",
        niche: str = DEFAULT_NICHE,
        use_hook: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        project=None,
        target_minutes: Optional[float] = None,
        auto_niche: bool = False,
        include_last_time: Optional[bool] = None,
        compile_chapters: Optional[List[Chapter]] = None,
        stop_flag: Optional[Callable[[], bool]] = None,
        assign: bool = True,
        last_src_override: Optional[str] = None,
    ) -> List[SegmentData]:
        """
        Beat tabanlı 2 aşamalı YouTube recap üretimi:
          1) Outline (düşük temperature)
          2) Voiceover (niş + stil)
        Paneller hikâye beat'lerine kümelenir; VO panel süresine yayılır.
        """
        if compile_chapters:
            return self._generate_compiled(
                compile_chapters, model, style, length, language, niche,
                stream_callback=stream_callback, project=project,
                target_minutes=target_minutes, auto_niche=auto_niche,
                stop_flag=stop_flag,
            )

        if not any(str(k).isdigit() for k in (chapter.analysis_data or {})):
            raise ValueError(
                f"'{chapter.name}' bölümünde analiz verisi yok. "
                "Önce AI Analiz sayfasından analiz yapın."
            )

        if auto_niche or niche == "auto":
            from core.beat_engine import detect_niche
            niche = detect_niche(chapter)
            if stream_callback:
                stream_callback(f"\n[Niş: {niche}]\n")

        minutes = resolve_target_minutes(length, target_minutes)
        if use_hook is None:
            use_hook = True
        if include_last_time is None:
            include_last_time = not _is_first_chapter(chapter)

        from core.beat_engine import cluster_beats, pick_cold_open_image
        from core import character_bible as bible
        from core.script_linter import lint_segments

        if project is not None:
            bible.upsert_characters(
                project,
                bible.extract_records_from_chapter(chapter, project),
                getattr(chapter, "id", ""),
            )

        if last_src_override is not None:
            last_src = last_src_override
        elif include_last_time:
            last_src = bible.last_time_source(project, chapter)
        else:
            last_src = ""
        beats = cluster_beats(
            chapter,
            target_minutes=minutes,
            include_last_time=False,
            length=length,
            project=project,
        )
        if not beats:
            raise ValueError("Beat kümeleme boş döndü.")
        if project is not None:
            bible.apply_names_to_beats(beats, project)
        if stop_flag and stop_flag():
            raise ValueError("Script üretimi durduruldu.")

        cold_idx = pick_cold_open_image(chapter, beats)
        logger.info(
            "Script: style=%s niche=%s hook=%s last_time=%s minutes=%s beats=%d cold=%d",
            style, niche, use_hook, bool(last_src),
            f"{minutes:.1f}" if minutes else "auto",
            len(beats), cold_idx,
        )
        if stream_callback:
            if minutes:
                stream_callback(f"\n[{len(beats)} beat · hedef {minutes:.0f} dk]\n")
            else:
                stream_callback(f"\n[{len(beats)} beat · süre otomatik]\n")

        if stop_flag and stop_flag():
            raise ValueError("Script üretimi durduruldu.")
        outline = self._generate_outline(
            chapter, beats, model, language, project, last_src, stream_callback,
        )
        if stop_flag and stop_flag():
            raise ValueError("Script üretimi durduruldu.")
        vo_by_id = self._generate_voiceover_chunks(
            chapter=chapter,
            beats=beats,
            outline=outline,
            model=model,
            style=style,
            length=length,
            language=language,
            niche=niche,
            use_hook=bool(use_hook),
            project=project,
            last_src=last_src,
            target_minutes=minutes or 0.0,
            stream_callback=stream_callback,
            stop_flag=stop_flag,
        )

        segments = self._materialize_segments(
            chapter, beats, vo_by_id, outline, language, cold_idx,
            use_hook=bool(use_hook), include_last_time=bool(include_last_time),
            last_src=last_src, length=length, project=project,
        )
        lint_segments(segments)
        if minutes:
            segments = fit_segments_to_target(segments, minutes, language)
            lint_segments(segments)
        if assign:
            chapter.segments = segments
            chapter.script_meta = {
                "niche": niche,
                "target_minutes": minutes,
                "beat_count": len(beats),
                "cold_open_image": cold_idx,
            }
        logger.info("Script tamamlandı: %d segment / %d beat", len(segments), len(beats))
        return segments

    def _chat(
        self,
        model: str,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        language: str = "tr",
        known_names: Optional[List[str]] = None,
        retries: int = 1,
    ) -> str:
        lock = _language_lock(language)
        messages = [
            {"role": "system", "content": lock},
            {"role": "user", "content": lock + "\n\n" + prompt},
        ]
        text = ""
        for attempt in range(max(1, retries + 1)):
            result = self._client.chat_completion(
                model,
                messages,
                temperature=temperature if attempt == 0 else min(0.55, temperature),
                max_tokens=max_tokens,
                fallback_models=self._fallback_models(),
            )
            text = (result.get("content") or "") if isinstance(result, dict) else str(result or "")
            if not _wrong_language(text, language, known_names):
                return text
            retry_lock = (
                lock
                + "\nRETRY: previous output used the WRONG language. "
                + "Rewrite the entire answer in the required language. No mixed words."
            )
            messages = [
                {"role": "system", "content": retry_lock},
                {"role": "user", "content": retry_lock + "\n\n" + prompt},
            ]
            logger.warning("LLM dil kaçışı (deneme %d), yeniden isteniyor.", attempt + 1)
        return text

    def _shared_layers(self, language: str, niche: str, style: str, use_hook: bool) -> Dict[str, Any]:
        prompts = _load_prompts()
        styles = prompts.get("script_styles", {})
        if style not in styles:
            style = "fresh" if "fresh" in styles else style
        return {
            "prompts": prompts,
            "lang_label": LANGUAGE_LABELS.get(language, language),
            "style_desc": styles.get(style, style),
            "prompt_1": prompts.get("script_prompt_1_universal", ""),
            "niche_module": _resolve_niche_prompt(prompts, niche),
            "retention_layer": prompts.get("script_prompt_retention", ""),
            "hook_layer": prompts.get("script_prompt_3_hook", "") if use_hook else "",
            "last_time_layer": prompts.get("script_prompt_last_time", ""),
            "language_lock": _language_lock(language),
        }

    def _generate_outline(
        self,
        chapter: Chapter,
        beats,
        model: str,
        language: str,
        project,
        last_src: str,
        stream_callback: Optional[Callable[[str], None]],
    ) -> dict:
        from core.beat_engine import format_beats_for_prompt
        from core import character_bible as bible

        prompts = _load_prompts()
        template = prompts.get("script_outline", "")
        last_block = ""
        if last_src:
            last_block = (
                prompts.get("script_prompt_last_time", "")
                + "\nKAYNAK:\n"
                + _source_note(last_src, language)
            ).strip()
        mapping = {
            "language": LANGUAGE_LABELS.get(language, language),
            "bible": bible.format_for_prompt(project, language),
            "stakes": _source_note(bible.chapter_stakes(chapter, language), language),
            "series_so_far": "",
            "last_time_block": last_block,
            "beats_block": format_beats_for_prompt(beats, language),
            "language_lock": _language_lock(language),
        }
        prompt = _fill_placeholders(template, mapping) if template else (
            "JSON outline üret. beats: [{id,payload,stakes}] hook last_time.\n"
            + mapping["beats_block"]
        )
        if stream_callback:
            stream_callback("\n[Outline]\n")
        known = [e.get("canonical", "") for e in bible.get_entries(project)]
        raw = self._chat(
            model, prompt, temperature=0.25, max_tokens=2200,
            language=language, known_names=known, retries=1,
        )
        data = _extract_json_obj(raw) or {}
        if not isinstance(data.get("beats"), list):
            data["beats"] = [
                {"id": b.beat_id, "payload": (b.summary or b.action or "")[:120], "stakes": ""}
                for b in beats
            ]
        hook = (data.get("hook") or "").strip()
        if hook and _wrong_language(hook, language):
            data["hook"] = ""
        last_time = (data.get("last_time") or "").strip()
        if last_time and _wrong_language(last_time, language):
            data["last_time"] = ""
        data["beats"] = _sanitize_outline_beats(data.get("beats"), beats, language, known)
        return data

    def _generate_voiceover_chunks(
        self,
        chapter: Chapter,
        beats,
        outline: dict,
        model: str,
        style: str,
        length: str,
        language: str,
        niche: str,
        use_hook: bool,
        project,
        last_src: str,
        target_minutes: float,
        stream_callback: Optional[Callable[[str], None]],
        stop_flag: Optional[Callable[[], bool]] = None,
    ) -> Dict[int, str]:
        from core.beat_engine import format_beats_for_prompt
        from core import character_bible as bible

        layers = self._shared_layers(language, niche, style, use_hook)
        prompts = layers["prompts"]
        template = prompts.get("script_voiceover") or prompts.get("script_generation", "")
        outline_beats: Dict[int, dict] = {}
        for item in (outline.get("beats") or []):
            if not isinstance(item, dict):
                continue
            bid = _safe_int(item.get("id", item.get("beat_id")))
            if bid is not None:
                outline_beats[bid] = item
        vo: Dict[int, str] = {}
        prev_tail = ""
        total_chunks = max(1, (len(beats) + BEAT_CHUNK_SIZE - 1) // BEAT_CHUNK_SIZE)

        for chunk_i, start in enumerate(range(0, len(beats), BEAT_CHUNK_SIZE)):
            if stop_flag and stop_flag():
                raise ValueError("Script üretimi durduruldu.")
            end = min(start + BEAT_CHUNK_SIZE, len(beats))
            chunk = beats[start:end]
            if stream_callback:
                stream_callback(f"\n[VO {chunk_i + 1}/{total_chunks}: beat {chunk[0].beat_id}-{chunk[-1].beat_id}]\n")

            known = [e.get("canonical", "") for e in bible.get_entries(project)]
            outline_lines = []
            for b in chunk:
                item = outline_beats.get(b.beat_id, {})
                payload = item.get("payload") or ""
                if not payload or _wrong_language(payload, language, known):
                    payload = ""
                    for candidate in (b.summary, b.action, b.scene):
                        cand = (candidate or "").strip()
                        if cand and not _wrong_language(cand, language, known) and not _is_atmosphere_dump(cand):
                            payload = cand
                            break
                    if not payload:
                        payload = _source_note(
                            (item.get("payload") or b.summary or b.action or b.scene or "").strip(),
                            language,
                            known,
                        )
                stakes = item.get("stakes") or ""
                if stakes and _wrong_language(stakes, language, known):
                    stakes = _source_note(stakes, language, known)
                outline_lines.append(
                    f"id={b.beat_id} role={b.role} budget={b.word_budget}: {payload}"
                    + (f" | stakes: {stakes}" if stakes else "")
                )
            hook_text = (outline.get("hook") or "").strip() if chunk_i == 0 else ""
            if hook_text and _wrong_language(hook_text, language, known):
                hook_text = ""
            last_text = (outline.get("last_time") or last_src or "").strip() if chunk_i == 0 else ""
            if last_text and _wrong_language(last_text, language, known):
                last_text = _source_note(last_text, language, known)
            context = prev_tail
            if hook_text:
                context = ("COLD OPEN HOOK: " + hook_text + "\n" + context).strip()
            if last_text:
                context = (layers["last_time_layer"] + "\n" + last_text + "\n" + context).strip()

            duration_note = _duration_note(length, target_minutes, language, chunk, len(beats))

            mapping = {
                "language": layers["lang_label"],
                "prompt_1": layers["prompt_1"] if chunk_i == 0 else "",
                "niche_module": layers["niche_module"],
                "retention_layer": layers["retention_layer"] if chunk_i == 0 else "",
                "hook_layer": layers["hook_layer"] if chunk_i == 0 else "",
                "style_desc": layers["style_desc"],
                "target_minutes": duration_note,
                "length_desc": prompts.get("script_lengths", {}).get(length, length),
                "bible": bible.format_for_prompt(project, language),
                "stakes": _source_note(bible.chapter_stakes(chapter, language), language, known),
                "context_block": context or "(ilk chunk)",
                "beats_block": format_beats_for_prompt(chunk, language),
                "outline_block": "\n".join(outline_lines),
                "language_lock": layers.get("language_lock") or _language_lock(language),
            }
            prompt = _fill_placeholders(template, mapping)
            tok_floor = 900 if length == "short" else 1800 if length == "medium" else 2500
            tok_mult = 5 if length == "short" else 8 if length == "medium" else 10
            max_tokens = min(8000, max(tok_floor, sum(max(b.word_budget, 12) for b in chunk) * tok_mult))
            raw = self._chat(
                model, prompt, temperature=0.72, max_tokens=max_tokens,
                language=language, known_names=known, retries=1,
            )
            parsed = _align_vo_to_beats(
                _parse_beats_voiceover(raw),
                [b.beat_id for b in chunk],
            )
            missing = [b.beat_id for b in chunk if not (parsed.get(b.beat_id) or "").strip()
                       or _wrong_language(parsed.get(b.beat_id) or "", language, known)]
            if missing:
                logger.warning("VO chunk %d eksik/yanlış dil beat: %s — yeniden denenecek", chunk_i + 1, missing)
                raw = self._chat(
                    model, prompt, temperature=0.45, max_tokens=max_tokens,
                    language=language, known_names=known, retries=1,
                )
                parsed = _align_vo_to_beats(
                    _parse_beats_voiceover(raw),
                    [b.beat_id for b in chunk],
                ) or parsed
            if not parsed:
                logger.error("VO chunk %d parse edilemedi.", chunk_i + 1)
            for b in chunk:
                text = (parsed.get(b.beat_id) or "").strip()
                if not text or _wrong_language(text, language, known) or _is_atmosphere_dump(text):
                    item = outline_beats.get(b.beat_id, {})
                    fallback = (item.get("payload") or b.summary or b.action or "").strip()
                    if fallback and not _wrong_language(fallback, language, known) and not _is_atmosphere_dump(fallback):
                        text = fallback
                    else:
                        text = ""
                text = _scrub_generic_labels(text, language, project=project)
                if length == "short" and text:
                    text = _clip_to_budget(_first_sentence(text), min(16, b.word_budget or 16))
                vo[b.beat_id] = text
            last_written = next((vo[b.beat_id] for b in reversed(chunk) if vo.get(b.beat_id)), "")
            if (language or "").lower().startswith("en"):
                prev_tail = f"[PREVIOUS BEAT — continue, do not restart]:\n\"{last_written}\"" if last_written else ""
            else:
                prev_tail = f"[ÖNCEKİ BEAT — devam et, yeniden açma]:\n\"{last_written}\"" if last_written else ""

        return vo

    def _materialize_segments(
        self,
        chapter: Chapter,
        beats,
        vo_by_id: Dict[int, str],
        outline: dict,
        language: str,
        cold_idx: int,
        use_hook: bool = True,
        include_last_time: bool = False,
        last_src: str = "",
        length: str = "medium",
        project=None,
    ) -> List[SegmentData]:
        n = len(chapter.images)
        if n <= 0:
            return []
        if cold_idx < 0 or cold_idx >= n:
            cold_idx = 0
        texts = [""] * n
        roles = [""] * n
        beat_ids: List[Optional[int]] = [None] * n

        for b in beats:
            body = (vo_by_id.get(b.beat_id) or "").strip()
            if body and (_wrong_language(body, language) or _is_atmosphere_dump(body)):
                body = ""
            if body:
                body = _scrub_generic_labels(body, language, project=project)
            if length == "short":
                if body:
                    body = _clip_to_budget(_first_sentence(body), min(16, b.word_budget or 16))
                parts = [body] + [""] * (len(b.panel_indices) - 1)
            else:
                parts = _distribute_text(body, len(b.panel_indices)) if body else [""] * len(b.panel_indices)
            for panel_i, part in zip(b.panel_indices, parts):
                if 0 <= panel_i < n:
                    texts[panel_i] = part
                    roles[panel_i] = b.role
                    beat_ids[panel_i] = b.beat_id

        last_spoken = ""
        for i in range(n):
            if _wrong_language(texts[i], language) or _is_atmosphere_dump(texts[i]):
                texts[i] = ""
            if (texts[i] or "").strip():
                last_spoken = texts[i].strip()
                continue
            glue = _panel_glue(chapter, i, language, avoid=last_spoken)
            if glue and glue.strip().rstrip(".").lower() != last_spoken.rstrip(".").lower():
                texts[i] = glue
                last_spoken = glue
            if not roles[i]:
                roles[i] = "beat"

        hook = ""
        last_time = ""
        if use_hook:
            hook = _scrub_generic_labels(
                (outline.get("hook") or vo_by_id.get(-1) or "").strip(), language, project=project
            )
            if _wrong_language(hook, language):
                hook = ""
        if include_last_time:
            last_time = _scrub_generic_labels(
                (outline.get("last_time") or last_src or "").strip(), language, project=project
            )
            if _wrong_language(last_time, language):
                last_time = ""

        prefix: List[SegmentData] = []
        if hook and n > 0:
            if length == "short":
                hook = _clip_to_budget(_first_sentence(hook), 16)
            prefix.append(self._make_segment(
                chapter, cold_idx, hook, language,
                beat_id=beats[0].beat_id if beats else 0,
                role="cold_open",
            ))
        if last_time and n > 0:
            if length == "short":
                last_time = _clip_to_budget(_first_sentence(last_time), 14)
            bridge_idx = 0 if n == 1 else (1 if cold_idx == 0 else 0)
            prefix.append(self._make_segment(
                chapter, bridge_idx, last_time, language,
                beat_id=None, role="last_time",
            ))

        story = [
            self._make_segment(
                chapter, i, (texts[i] or "").strip(), language,
                beat_id=beat_ids[i], role=roles[i],
            )
            for i in range(n)
        ]
        return prefix + story

    def _make_segment(
        self,
        chapter: Chapter,
        image_index: int,
        text: str,
        language: str,
        beat_id: Optional[int] = None,
        role: str = "",
    ) -> SegmentData:
        text = (text or "").strip()
        n = len(getattr(chapter, "images", None) or [])
        if n > 0:
            try:
                image_index = max(0, min(int(image_index), n - 1))
            except (TypeError, ValueError):
                image_index = 0
        img_path = None
        if 0 <= image_index < n:
            img_path = getattr(chapter.images[image_index], "path", None)
        duration = self.estimate_duration(text, language) if text else 0.0
        return SegmentData(
            image_index=image_index,
            text=text,
            duration=duration,
            beat_id=beat_id,
            role=role,
            image_path=img_path,
        )

    def _generate_compiled(
        self,
        chapters: List[Chapter],
        model: str,
        style: str,
        length: str,
        language: str,
        niche: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        project=None,
        target_minutes: Optional[float] = None,
        auto_niche: bool = False,
        stop_flag: Optional[Callable[[], bool]] = None,
    ) -> List[SegmentData]:
        """Çok bölüm derleme: her bölümü üret, filler'ı seyrelt, tek anlatı gibi birleştir."""
        if target_minutes is not None and float(target_minutes) > 0:
            per = max(3.0, float(target_minutes) / max(1, len(chapters)))
        else:
            per = resolve_target_minutes(length, None)
        all_segs: List[SegmentData] = []
        prev_tail = ""
        for i, ch in enumerate(chapters):
            if not any(str(k).isdigit() for k in (ch.analysis_data or {})):
                logger.warning("Derleme: '%s' analiz yok, atlanıyor.", ch.name)
                continue
            if stop_flag and stop_flag():
                raise ValueError("Script üretimi durduruldu.")
            if stream_callback:
                stream_callback(f"\n[Derleme {i + 1}/{len(chapters)}: {ch.name}]\n")
            segs = self.generate_script(
                chapter=ch,
                model=model,
                style=style,
                length=length,
                language=language,
                niche=niche,
                use_hook=(i == 0),
                stream_callback=stream_callback,
                project=project,
                target_minutes=per,
                auto_niche=auto_niche and i == 0,
                include_last_time=(i > 0),
                stop_flag=stop_flag,
                assign=False,
                last_src_override=prev_tail if i > 0 else "",
            )
            for s in segs:
                if per and s.role == "filler" and len((s.text or "").split()) > 18:
                    s.text = " ".join((s.text or "").split()[:14])
                    s.duration = self.estimate_duration(s.text, language)
                if not s.image_path and 0 <= s.image_index < len(ch.images):
                    s.image_path = getattr(ch.images[s.image_index], "path", None)
            all_segs.extend(segs)
            prev_tail = " ".join(s.text.strip() for s in segs[-4:] if (s.text or "").strip())
        if not all_segs:
            raise ValueError("Derleme için üretilebilen bölüm yok.")
        return all_segs

    # ── Belirli Aralık için Üretim ─────────────────────────────────

    def _generate_range(
        self,
        chapter: Chapter,
        model: str,
        style: str,
        length: str,
        language: str,
        start: int,
        end: int,
        niche: str = DEFAULT_NICHE,
        use_hook: bool = False,
        stream_callback: Optional[Callable[[str], None]] = None,
        project=None,
    ) -> List[SegmentData]:
        """
        chapter.images[start:end] için script üretir.
        image_index değerleri orijinal (global) indeksleri korur.

        NOT: JSON güvenilirliği için her zaman chat_completion (non-streaming) kullanır.
        stream_callback varsa sadece durum mesajları için kullanılır.
        """
        panel_count = end - start
        # short daha kompakt; long daha geniş
        length_multiplier = {"short": 0.85, "medium": 1.0, "long": 1.6}.get(length, 1.0)
        max_tokens = min(8000, max(2000, int(panel_count * 120 * length_multiplier)))

        prompt = self._build_prompt_for_range(
            chapter, style, length, language, start, end,
            niche=niche, use_hook=use_hook, project=project,
        )
        messages = [{"role": "user", "content": prompt}]

        logger.info(
            "Aralık üretimi: panel %d-%d, niche=%s hook=%s max_tokens=%d",
            start + 1, end, niche, use_hook, max_tokens,
        )

        # Streaming KULLANMA — JSON chunk'lar halinde gelince parse başarısız olur.
        result = self._client.chat_completion(
            model, messages, temperature=0.75, max_tokens=max_tokens,
            fallback_models=self._fallback_models(),
        )
        full_text = result["content"]
        logger.debug(
            "Aralık tamamlandı: %d karakter (token: %s)",
            len(full_text), result.get("usage", {}).get("total_tokens", "?"),
        )

        if stream_callback:
            stream_callback(f" [{panel_count} panel tamamlandı]\n")

        raw_segments = _parse_segments(full_text)
        if not raw_segments:
            logger.error("Panel %d-%d için segment üretilemedi.", start + 1, end)
            return []

        return self._build_segment_data(raw_segments, language, project=project)

    def _stream_generate(
        self,
        model: str,
        messages: list,
        callback: Callable[[str], None],
        max_tokens: int = 4000,
    ) -> str:
        """Streaming ile metin üretir; her token'ı callback'e iletir."""
        full = []
        try:
            for chunk in self._client.chat_stream(model, messages, max_tokens=max_tokens):
                full.append(chunk)
                callback(chunk)
        except OpenRouterError as exc:
            logger.error("Stream hatası: %s", exc)
            raise
        return "".join(full)

    # ── Prompt ────────────────────────────────────────────────────

    def _build_prompt(
        self,
        chapter: Chapter,
        style: str,
        length: str,
        language: str,
        niche: str = DEFAULT_NICHE,
        use_hook: bool = False,
        project=None,
    ) -> str:
        """Tüm görseller için prompt üretir (geriye dönük uyumluluk)."""
        return self._build_prompt_for_range(
            chapter, style, length, language,
            start=0, end=len(chapter.images),
            niche=niche, use_hook=use_hook, project=project,
        )

    def _build_prompt_for_range(
        self,
        chapter: Chapter,
        style: str,
        length: str,
        language: str,
        start: int,
        end: int,
        niche: str = DEFAULT_NICHE,
        use_hook: bool = False,
        project=None,
    ) -> str:
        """
        chapter.images[start:end] aralığı için 3-katmanlı Manhwa Fresh prompt üretir.
        image_index değerleri orijinal (global) indeksleri yansıtır.
        """
        prompts = _load_prompts()
        template = prompts.get("script_generation", "{analysis_data}")
        styles = prompts.get("script_styles", {})
        lengths = prompts.get("script_lengths", {})

        if style not in styles:
            style = "fresh" if "fresh" in styles else style
        style_desc = styles.get(style, style)
        length_desc = lengths.get(length, length)
        lang_label = LANGUAGE_LABELS.get(language, language)

        prompt_1 = prompts.get("script_prompt_1_universal", "")
        niche_module = _resolve_niche_prompt(prompts, niche)
        retention_layer = prompts.get("script_prompt_retention", "")
        hook_layer = prompts.get("script_prompt_3_hook", "") if use_hook else ""

        analysis_lines = []
        for i in range(start, end):
            key = str(i)
            data = chapter.analysis_data.get(key, {})
            if data.get("error") or data.get("parse_error"):
                analysis_lines.append(f"Panel {i + 1}: [analiz başarısız]")
                continue
            clean_chars = _sanitize_characters(
                data.get("characters", []), language, project=project,
            )
            scene = _source_note(
                _clause_text(_scrub_generic_labels(
                    _clause_text(data.get("scene", "")), language,
                    cast=clean_chars, project=project,
                )),
                language,
                clean_chars,
            )
            action = _source_note(
                _clause_text(_scrub_generic_labels(
                    _clause_text(data.get("action", "")), language,
                    cast=clean_chars, project=project,
                )),
                language,
                clean_chars,
            )
            if clean_chars:
                chars = ", ".join(clean_chars)
            else:
                chars = "(isim yok — zamir kullan, protagonist/main character deme)"
            dialogues = _source_note(
                "; ".join(_normalize_string_list(data.get("dialogues", []))[:2]),
                language,
                clean_chars,
            )
            mood = _source_note(_clause_text(data.get("mood", "")), language, clean_chars)
            setting = _source_note(_clause_text(data.get("setting", "")), language, clean_chars)
            important = data.get("important", None)
            parts = [f"Panel {i + 1} (image_index={i}):"]
            if scene:
                parts.append(f"Sahne: {scene}.")
            if action:
                parts.append(f"Aksiyon: {action}.")
            parts.append(f"Karakterler: {chars}.")
            if dialogues:
                parts.append(f"Diyalog: {dialogues}.")
            if mood:
                parts.append(f"Atmosfer: {mood}.")
            if setting:
                parts.append(f"Mekan: {setting}.")
            if important is True:
                parts.append("[ÖNEMLİ BEAT]")
            analysis_lines.append(" ".join(parts))

        analysis_data = "\n".join(analysis_lines)

        # str.format() YERİNE replace() — JSON örneklerindeki {..} KeyError vermesin
        result = template
        result = result.replace("{language}",      lang_label)
        result = result.replace("{style_desc}",    style_desc)
        result = result.replace("{length_desc}",   length_desc)
        result = result.replace("{prompt_1}",      prompt_1)
        result = result.replace("{niche_module}",  niche_module)
        result = result.replace("{retention_layer}", retention_layer)
        result = result.replace("{hook_layer}",    hook_layer)
        result = result.replace("{analysis_data}", analysis_data)
        bible_text = ""
        if project is not None:
            from core import character_bible as bible
            bible_text = bible.format_for_prompt(project, language)
        result = result.replace("{bible}", bible_text)
        result = result.replace("{stakes}", "")
        result = result.replace("{context_block}", "")
        minutes = resolve_target_minutes(length, None)
        if length == "short":
            duration_note = (
                "SHORT MODE: exactly 1 sentence per beat, max 16 words."
                if (language or "").lower().startswith("en") else
                "KISA MOD: her beat 1 cümle, en fazla 16 kelime."
            )
        elif minutes:
            duration_note = (
                f"TARGET DURATION: ~{minutes:.0f} minutes. Fill the spoken word budget. Two sentences is a minimum, not the target."
                if (language or "").lower().startswith("en") else
                f"HEDEF SÜRE: ~{minutes:.0f} dakika. Kelime bütçesini doldur. 2 cümle minimum, hedef değil."
            )
        else:
            duration_note = "Fill the length budget. Do not write a 40-second recap."
        result = result.replace("{beats_block}", analysis_data)
        result = result.replace("{target_minutes}", duration_note)
        result = result.replace("{language_lock}", _language_lock(language))
        return result

    # ── Segment Üretimi ────────────────────────────────────────────

    def _build_segment_data(
        self, raw: List[dict], language: str, project=None,
    ) -> List[SegmentData]:
        segments = []
        for item in raw:
            text = item.get("text", "").strip()
            if len(text) < 5:
                continue
            text = _scrub_generic_labels(text, language, project=project)
            if len(text) < 5:
                continue
            idx = _safe_int(item.get("image_index", len(segments)))
            if idx is None:
                idx = len(segments)
            duration = self.estimate_duration(text, language)
            segments.append(SegmentData(
                image_index=idx,
                text=text,
                duration=duration,
            ))
        return segments

    # ── Tek Segment Yeniden Üretimi ────────────────────────────────

    def regenerate_segment(
        self,
        chapter: Chapter,
        segment_index: int,
        model: str,
        style: str = "fresh",
        language: str = "tr",
        length: str = "medium",
        niche: str = DEFAULT_NICHE,
        project=None,
    ) -> SegmentData:
        """
        Tek bir segmenti yeniden üretir (Manhwa Fresh kurallarıyla).
        """
        if segment_index < 0 or segment_index >= len(chapter.segments):
            raise IndexError(f"Geçersiz segment indeksi: {segment_index}")

        seg = chapter.segments[segment_index]
        image_idx = seg.image_index
        analysis = (chapter.analysis_data or {}).get(str(image_idx), {})
        if not isinstance(analysis, dict):
            analysis = {}

        prompts = _load_prompts()
        styles = prompts.get("script_styles", {})
        if style not in styles:
            style = "fresh" if "fresh" in styles else style
        style_desc = styles.get(style, style)

        lengths = prompts.get("script_lengths", {})
        length_desc = lengths.get(length, length)
        lang_label = LANGUAGE_LABELS.get(language, language)

        clean_chars = _sanitize_characters(analysis.get("characters", []), language)
        scene = _source_note(
            _clause_text(_scrub_generic_labels(_clause_text(analysis.get("scene", "")), language)),
            language,
            clean_chars,
        )
        action = _source_note(
            _clause_text(_scrub_generic_labels(_clause_text(analysis.get("action", "")), language)),
            language,
            clean_chars,
        )
        dialogues = _source_note(
            "; ".join(_normalize_string_list(analysis.get("dialogues", []))[:2]),
            language,
            clean_chars,
        )
        mood = _source_note(_clause_text(analysis.get("mood", "")), language, clean_chars)

        # Komşu segmentler — anlatı sürekliliği
        prev_text = ""
        next_text = ""
        if segment_index > 0:
            prev_text = chapter.segments[segment_index - 1].text
        if segment_index < len(chapter.segments) - 1:
            next_text = chapter.segments[segment_index + 1].text

        en = (language or "").lower().startswith("en")
        context_block = ""
        if prev_text:
            context_block += (
                f"\n[PREVIOUS LINE — continue from this, do not restart]:\n\"{prev_text}\"\n"
                if en else
                f"\n[ÖNCEKİ SAHNE — senin metnin bu sahnenin hemen DEVAMI olmalı]:\n\"{prev_text}\"\n"
            )
        if next_text:
            context_block += (
                f"\n[NEXT LINE — bridge toward this]:\n\"{next_text}\"\n"
                if en else
                f"\n[SONRAKİ SAHNE — senin metnin bu sahneye doğal bir köprü kurmalı]:\n\"{next_text}\"\n"
            )
        if not context_block:
            context_block = "(No extra context.)" if en else "(Bağlam yok — tek segment.)"

        template = prompts.get("script_regenerate", "")
        if not template:
            template = (
                "Manhwa Fresh recap. Dil: {language}. Stil: {style_desc}.\n"
                "{prompt_1}\n{niche_module}\n{context_block}\n"
                "Sahne: {scene}\nAksiyon: {action}\nKarakterler: {chars}\n"
                "Diyalog: {dialogues}\nAtmosfer: {mood}\nSadece düz metin."
            )

        beat_mates = [
            s.text for s in chapter.segments
            if getattr(s, "beat_id", None) is not None
            and s.beat_id == getattr(seg, "beat_id", None)
            and s is not seg
            and (s.text or "").strip()
        ]
        if beat_mates:
            header = "[SAME BEAT LINES]:" if en else "[AYNI BEAT KARDEŞLERİ]:"
            context_block += "\n" + header + "\n" + "\n".join(f"- {t}" for t in beat_mates[:4])

        from core import character_bible as bible
        known = [e.get("canonical", "") for e in bible.get_entries(project)]
        no_name = "(no name — use he/she/they, never protagonist)" if en else "(isim yok — zamir kullan, protagonist/main character deme)"
        chars = ", ".join(clean_chars) if clean_chars else no_name

        prompt = _fill_placeholders(template, {
            "language": lang_label,
            "style_desc": style_desc,
            "length_desc": length_desc,
            "prompt_1": prompts.get("script_prompt_1_universal", ""),
            "niche_module": _resolve_niche_prompt(prompts, niche),
            "retention_layer": prompts.get("script_prompt_retention", ""),
            "context_block": context_block,
            "scene": scene,
            "action": action,
            "chars": chars,
            "dialogues": dialogues,
            "mood": mood,
            "role": getattr(seg, "role", "") or "beat",
            "word_budget": "12" if length == "short" else "90" if length == "medium" else "140",
            "bible": bible.format_for_prompt(project, language),
            "panels": str(image_idx),
            "language_lock": _language_lock(language),
        })

        max_tokens = {"short": 180, "medium": 900, "long": 1400}.get(length, 900)
        raw = self._chat(
            model, prompt, temperature=0.55, max_tokens=max_tokens,
            language=language, known_names=known, retries=2,
        )
        text = (raw or "").strip().strip('"').strip("`")
        if text.lower().startswith("text:"):
            text = text[5:].strip().strip('"')
        text = _scrub_generic_labels(text, language, cast=clean_chars, project=project)
        if length == "short":
            text = _clip_to_budget(_first_sentence(text), 16)
        if _wrong_language(text, language, known):
            raise ValueError(
                "The model mixed languages. Try regenerate again (English only)."
                if en else
                "Model seçilen dilde yazmadı. Tekrar dene."
            )
        if len(text) < 5:
            raise ValueError("LLM çok kısa metin döndürdü.")

        seg.text = text
        seg.duration = self.estimate_duration(text, language)
        if not seg.image_path and 0 <= image_idx < len(chapter.images):
            seg.image_path = getattr(chapter.images[image_idx], "path", None)
        chapter.segments[segment_index] = seg
        from core.script_linter import lint_text
        seg.lint_issues = lint_text(
            text,
            role=seg.role or "",
            is_first=segment_index == 0,
            is_last=segment_index == len(chapter.segments) - 1,
        )
        return seg

    # ── Süre Tahmini ───────────────────────────────────────────────

    @staticmethod
    def estimate_duration(text: str, language: str = "tr") -> float:
        """
        Metinden tahmini okuma/TTS süresi hesaplar (saniye).

        Args:
            text: Anlatılacak metin.
            language: Dil kodu.

        Returns:
            Saniye cinsinden tahmini süre.
        """
        wpm = WPM.get(language, 150)
        word_count = len((text or "").split())
        if word_count <= 0:
            return 0.0
        return round((word_count / wpm) * 60, 2)


def fit_segments_to_target(segments, target_minutes: float, language: str = "tr"):
    """TTS tahmini süre hedefin ~%12 üstündeyse filler'ı kısaltır."""
    if not segments or not target_minutes or target_minutes <= 0:
        return segments
    target_s = float(target_minutes) * 60.0
    total = sum(float(getattr(s, "duration", 0) or 0) for s in segments)
    if total <= target_s * 1.12:
        return segments
    fillers = [
        s for s in segments
        if (getattr(s, "role", "") or "").lower() == "filler"
        and (s.text or "").strip()
    ]
    extra = total - target_s
    for seg in fillers:
        if extra <= 0:
            break
        words = (seg.text or "").split()
        if len(words) <= 4:
            continue
        keep = max(4, int(len(words) * 0.55))
        seg.text = " ".join(words[:keep])
        old = float(seg.duration or 0)
        seg.duration = ScriptGenerator.estimate_duration(seg.text, language)
        extra -= max(0.0, old - float(seg.duration or 0))
    return segments