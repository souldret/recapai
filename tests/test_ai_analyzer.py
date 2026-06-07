"""
RecapAI - AIAnalyzer / _parse_json_response testleri.
Çalıştırma: pytest tests/test_ai_analyzer.py -v
"""

import pytest

# _parse_json_response doğrudan test ediyoruz (LLM bağlantısı gerektirmiyor)
from core.ai_analyzer import _parse_json_response


class TestParseJsonResponse:
    """_parse_json_response — 3 kademeli ayrıştırma mantığı."""

    # ── Kademe 1: Temiz JSON ──────────────────────────────────────

    def test_plain_json_object(self):
        text = '{"characters": ["Adam"], "mood": "tense"}'
        result = _parse_json_response(text)
        assert result["characters"] == ["Adam"]
        assert result["mood"] == "tense"

    def test_plain_json_with_whitespace(self):
        text = '   \n{"key": "value"}\n   '
        result = _parse_json_response(text)
        assert result["key"] == "value"

    def test_plain_json_with_unicode(self):
        text = '{"description": "Karanlık bir sokak, yağmur yağıyor."}'
        result = _parse_json_response(text)
        assert "yağmur" in result["description"]

    # ── Kademe 2: Markdown kod bloğu içinde JSON ──────────────────

    def test_json_in_markdown_block(self):
        text = '```json\n{"panel_count": 5, "action": true}\n```'
        result = _parse_json_response(text)
        assert result["panel_count"] == 5
        assert result["action"] is True

    def test_json_in_plain_code_block(self):
        text = '```\n{"mood": "happy"}\n```'
        result = _parse_json_response(text)
        assert result["mood"] == "happy"

    def test_json_block_with_surrounding_text(self):
        text = (
            "İşte analiz sonucu:\n"
            "```json\n"
            '{"characters": ["Kahraman", "Düşman"]}\n'
            "```\n"
            "Umarım yararlı olmuştur."
        )
        result = _parse_json_response(text)
        assert "Kahraman" in result["characters"]

    # ── Kademe 3: Metin içinde {...} taraması ─────────────────────

    def test_json_embedded_in_prose(self):
        text = 'Model yanıtı: {"scene": "battle", "intensity": 9} devam ediyor.'
        result = _parse_json_response(text)
        assert result["scene"] == "battle"
        assert result["intensity"] == 9

    def test_json_with_nested_object(self):
        text = '{"outer": {"inner": "value"}, "list": [1, 2, 3]}'
        result = _parse_json_response(text)
        assert result["outer"]["inner"] == "value"
        assert result["list"] == [1, 2, 3]

    # ── Hata durumları ────────────────────────────────────────────

    def test_empty_string_returns_dict(self):
        result = _parse_json_response("")
        assert isinstance(result, dict)

    def test_plain_text_no_json_returns_dict(self):
        result = _parse_json_response("Bu bir analiz metnidir, JSON içermiyor.")
        assert isinstance(result, dict)

    def test_malformed_json_returns_dict(self):
        result = _parse_json_response('{"key": "unclosed string}')
        assert isinstance(result, dict)

    def test_array_response_returns_dict(self):
        # Model yanlışlıkla array döndüğünde fonksiyon dict dönmeli —
        # çünkü tüm çağrıcılar result["key"] şeklinde erişiyor.
        # Fonksiyon list yanıtı için boş {} döndürür (3. kademe taraması eşleşmez).
        result = _parse_json_response('[{"item": 1}, {"item": 2}]')
        assert isinstance(result, dict)

    def test_whitespace_only_returns_dict(self):
        result = _parse_json_response("   \n\t  ")
        assert isinstance(result, dict)