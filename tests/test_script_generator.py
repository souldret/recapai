"""
RecapAI - ScriptGenerator yardımcı fonksiyon testleri.
Çalıştırma: pytest tests/test_script_generator.py -v
"""

import pytest

import re

from core.script_generator import (
    _sanitize_characters,
    _scrub_generic_labels,
    _is_first_chapter,
    list_niches,
    CHUNK_SIZE,
    DEFAULT_NICHE,
    VALID_NICHES,
)
from core.models import Chapter


class TestSanitizeCharacters:
    """_sanitize_characters — jenerik etiketleri SİLER (Protagonist ile değiştirmez)."""

    def test_dict_characters_do_not_crash(self):
        # Vision bazen {"name": "..."} döndürür
        result = _sanitize_characters(
            [{"name": "Jin-Woo"}, {"name": "Protagonist"}, {"role": "bir adam"}, "Ahmet"],
            language="en",
        )
        assert "Jin-Woo" in result
        assert "Ahmet" in result
        assert "Protagonist" not in result

    def test_single_dict(self):
        result = _sanitize_characters({"name": "Varkas"}, language="en")
        assert result == ["Varkas"]

    def test_drops_bir_adam(self):
        result = _sanitize_characters(["Bir adam"], language="tr")
        assert result == []

    def test_drops_gizemli_figur(self):
        result = _sanitize_characters(["Gizemli Figür"], language="tr")
        assert "Gizemli Figür" not in result
        assert "Protagonist" not in result

    def test_drops_protagonist(self):
        result = _sanitize_characters(["Protagonist", "Main Character", "MC"], language="en")
        assert result == []

    def test_drops_ana_karakter(self):
        result = _sanitize_characters(["Ana Karakter", "Kahramanımız"], language="tr")
        assert result == []

    def test_keeps_named_characters(self):
        result = _sanitize_characters(["Ahmet", "Mehmet"], language="tr")
        assert "Ahmet" in result
        assert "Mehmet" in result

    def test_mixed_list(self):
        chars = ["Ahmet", "Bir adam", "Protagonist", "Gizemli Figür"]
        result = _sanitize_characters(chars, language="tr")
        assert result == ["Ahmet"]

    def test_empty_list(self):
        assert _sanitize_characters([], language="tr") == []

    def test_single_named_char(self):
        assert _sanitize_characters(["Leyla"], language="tr") == ["Leyla"]

    def test_replaces_a_man_english(self):
        assert "A man" not in _sanitize_characters(["A man"], language="en")

    def test_keeps_english_named_chars(self):
        result = _sanitize_characters(["John", "Sarah"], language="en")
        assert "John" in result and "Sarah" in result

    def test_output_length_not_longer_than_input(self):
        chars = ["Ahmet", "Bir adam", "Gizemli Figür", "Fatma"]
        result = _sanitize_characters(chars, language="tr")
        assert len(result) <= len(chars)
        assert "Ahmet" in result and "Fatma" in result

    def test_large_list_of_named_chars(self):
        chars = ["Ahmet", "Fatma", "Leyla", "Can", "Ali", "Zeynep",
                 "Hasan", "Ayşe", "Mehmet", "Selin"]
        result = _sanitize_characters(chars, language="tr")
        assert len(result) == len(chars)


class TestScrubGenericLabels:
    def test_scrub_en_protagonist(self):
        t = _scrub_generic_labels("The protagonist walks in. Main character fights.", "en")
        assert "protagonist" not in t.lower()
        assert "main character" not in t.lower()
        assert "he" in t.lower()

    def test_scrub_tr_ana_karakter(self):
        t = _scrub_generic_labels("Ana karakter kapıyı açar. Protagonist kaçar.", "tr")
        assert "ana karakter" not in t.lower()
        assert "protagonist" not in t.lower()
        # TR dilinde İngilizce 'he' enjekte edilmemeli
        assert re.search(r"\bhe\b", t, re.I) is None

    def test_keeps_real_names(self):
        t = _scrub_generic_labels("Jin-Woo draws his blade.", "en")
        assert "Jin-Woo" in t

    def test_keeps_visual_hints(self):
        # Kısa görsel ipucu silinmemeli
        result = _sanitize_characters(["kırmızı pelerinli", "saçlı kız", "Protagonist"], "tr")
        assert "Protagonist" not in result
        assert "kırmızı pelerinli" in result
        assert "saçlı kız" in result


class TestChunkSize:
    """CHUNK_SIZE sabiti — API güvenlik sınırı."""

    def test_chunk_size_is_positive(self):
        assert CHUNK_SIZE > 0

    def test_chunk_size_within_safe_range(self):
        # 40 panel × ~120 token = ~4800 token — tüm modellerde güvenli
        assert CHUNK_SIZE <= 40

    def test_chunk_logic_splits_correctly(self):
        """60 panel → 2 chunk: [0:40] ve [40:60]."""
        panels = list(range(60))
        chunks = [panels[i:i + CHUNK_SIZE] for i in range(0, len(panels), CHUNK_SIZE)]
        assert len(chunks) == 2
        assert len(chunks[0]) == CHUNK_SIZE
        assert len(chunks[1]) == 20

    def test_chunk_logic_exact_size(self):
        """Tam CHUNK_SIZE panel → 1 chunk."""
        panels = list(range(CHUNK_SIZE))
        chunks = [panels[i:i + CHUNK_SIZE] for i in range(0, len(panels), CHUNK_SIZE)]
        assert len(chunks) == 1

    def test_chunk_logic_less_than_chunk(self):
        """CHUNK_SIZE'dan az panel → 1 chunk."""
        panels = list(range(10))
        chunks = [panels[i:i + CHUNK_SIZE] for i in range(0, len(panels), CHUNK_SIZE)]
        assert len(chunks) == 1
        assert chunks[0] == panels


class TestNichesAndHook:
    """Manhwa Fresh niş listesi ve Chapter-1 hook sezgisi."""

    def test_list_niches_has_four(self):
        niches = list_niches()
        ids = {n["id"] for n in niches}
        assert set(VALID_NICHES).issubset(ids)
        assert DEFAULT_NICHE in ids

    def test_list_niches_has_labels(self):
        for n in list_niches():
            assert n["label"]
            assert n["id"]

    def test_is_first_chapter_by_name(self):
        ch = Chapter(id="c1", name="Chapter 1", images=[])
        assert _is_first_chapter(ch) is True

    def test_is_first_chapter_bolum(self):
        ch = Chapter(id="c1", name="Bölüm 1 - Başlangıç", images=[])
        assert _is_first_chapter(ch) is True

    def test_is_first_chapter_leading_number(self):
        ch = Chapter(id="c1", name="1 - Prologue", images=[])
        assert _is_first_chapter(ch) is True

    def test_is_not_first_chapter_12(self):
        ch = Chapter(id="c2", name="Chapter 12", images=[])
        assert _is_first_chapter(ch) is False

    def test_is_not_first_chapter_10(self):
        ch = Chapter(id="c10", name="Bölüm 10", images=[])
        assert _is_first_chapter(ch) is False

    def test_retention_prompt_exists(self):
        from pathlib import Path
        import json
        data = json.loads(
            (Path(__file__).resolve().parent.parent / "config" / "prompts.json").read_text(encoding="utf-8")
        )
        assert "script_prompt_retention" in data
        assert "{retention_layer}" in data["script_generation"]
        assert "kanca" in data["script_prompt_retention"].lower() or "hook" in data["script_prompt_retention"].lower()