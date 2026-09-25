"""Metin normalizasyonu. TTS ve script üretimi aynı kuralları paylaşır."""

import re

# Gerçek kısaltmalar — ALL CAPS olarak kalması gereken, TTS'in harf harf okuması DOĞRU olan
# (vurgu kelimeleri buraya EKLENMEMELI: NO, GO, SO gibi bunlar capitalize edilmeli)
_KNOWN_ACRONYMS = {"OK", "US", "UK", "EU", "AI", "TV", "PC", "DJ", "FBI", "CIA", "NASA", "NATO"}


def normalize_caps(text: str) -> str:
    """
    ALL CAPS kelimeleri TTS motorlarının kelime olarak okuması için normalize eder.

    Kural:
    - Gerçek kısaltmalar (_KNOWN_ACRONYMS) → olduğu gibi bırak  (AI, TV, NASA…)
    - "I" (tekil büyük harf, İngilizce özne) → olduğu gibi bırak
    - Diğer tüm ALL CAPS kelimeler → Title Case  (DEFIED → Defied, AS → As, NO → No)
    - Kısa çizgili bileşikler (WEISS-SAMA) → her parçaya aynı kural uygulanır
    """
    def _convert_word(word: str) -> str:
        # Kelimenin önündeki/arkasındaki noktalama işaretlerini ayır
        m = re.fullmatch(r"([^A-Za-z0-9']*)([A-Za-z0-9\-']+)([^A-Za-z0-9']*)", word)
        if not m:
            return word
        prefix, core, suffix = m.group(1), m.group(2), m.group(3)

        # Kısa çizgili bileşikleri (WEISS-SAMA) parçalara böl ve her birini işle
        if "-" in core:
            parts = core.split("-")
            converted_parts = [_convert_core(p) for p in parts]
            return prefix + "-".join(converted_parts) + suffix

        return prefix + _convert_core(core) + suffix

    def _convert_core(token: str) -> str:
        if token == "I":
            return token
        if token in _KNOWN_ACRONYMS:
            return token
        letters = [c for c in token if c.isalpha()]
        leftover_caps = "'" in token and any(c.isupper() for c in letters[1:])
        # ALL CAPS, ya da Could'VE / Doesn'T gibi kesme işaretinde kalmış büyük harf
        if token.isupper() or leftover_caps:
            return token.capitalize()
        return token

    tokens = text.split(" ")
    return " ".join(_convert_word(t) for t in tokens)


def normalize_segment_caps(segments):
    """Bozuk ALL CAPS'i segment metnine kalıcı yazar. Sessiz satıra dokunmaz."""
    for seg in segments or []:
        raw = getattr(seg, "text", None) or ""
        if not raw.strip():
            continue
        seg.text = normalize_caps(raw)
    return segments
