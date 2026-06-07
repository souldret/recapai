"""
RecapAI - ScriptGenerator yardımcı fonksiyon testleri.
Çalıştırma: pytest tests/test_script_generator.py -v
"""

import pytest

from core.script_generator import _sanitize_characters, CHUNK_SIZE


class TestSanitizeCharacters:
    """_sanitize_characters — jenerik etiketleri rol tabanlı isimlerle değiştirir."""

    # ── Türkçe jenerik etiketler ──────────────────────────────────

    def test_replaces_bir_adam(self):
        result = _sanitize_characters(["Bir adam"], language="tr")
        assert "Bir adam" not in result
        assert len(result) == 1

    def test_replaces_gizemli_figur(self):
        result = _sanitize_characters(["Gizemli Figür"], language="tr")
        assert "Gizemli Figür" not in result

    def test_keeps_named_characters(self):
        result = _sanitize_characters(["Ahmet", "Mehmet"], language="tr")
        assert "Ahmet" in result
        assert "Mehmet" in result

    def test_mixed_list(self):
        chars = ["Ahmet", "Bir adam", "Gizemli Figür"]
        result = _sanitize_characters(chars, language="tr")
        assert "Ahmet" in result
        assert "Bir adam" not in result

    def test_empty_list(self):
        result = _sanitize_characters([], language="tr")
        assert result == []

    def test_single_named_char(self):
        result = _sanitize_characters(["Leyla"], language="tr")
        assert result == ["Leyla"]

    # ── İngilizce jenerik etiketler ──────────────────────────────

    def test_replaces_a_man_english(self):
        result = _sanitize_characters(["A man"], language="en")
        assert "A man" not in result

    def test_replaces_mysterious_figure_english(self):
        result = _sanitize_characters(["Mysterious Figure"], language="en")
        assert "Mysterious Figure" not in result

    def test_keeps_english_named_chars(self):
        result = _sanitize_characters(["John", "Sarah"], language="en")
        assert "John" in result
        assert "Sarah" in result

    # ── Uzunluk koruması ─────────────────────────────────────────

    def test_output_length_matches_input(self):
        chars = ["Ahmet", "Bir adam", "Gizemli Figür", "Fatma"]
        result = _sanitize_characters(chars, language="tr")
        assert len(result) == len(chars)

    def test_large_list_of_named_chars(self):
        """Jenerik olmayan isimler değiştirilmez ve korunur."""
        # "Karakter X" jenerik etiket sayılmaz; dolayısıyla tüm liste korunmalı
        chars = ["Ahmet", "Fatma", "Leyla", "Can", "Ali", "Zeynep",
                 "Hasan", "Ayşe", "Mehmet", "Selin"]
        result = _sanitize_characters(chars, language="tr")
        assert len(result) == len(chars)


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