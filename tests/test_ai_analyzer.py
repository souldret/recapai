"""
RecapAI - AIAnalyzer / _parse_json_response testleri.
Çalıştırma: pytest tests/test_ai_analyzer.py -v
"""

import pytest

# _parse_json_response doğrudan test ediyoruz (LLM bağlantısı gerektirmiyor)
from core.ai_analyzer import (
    _parse_json_response,
    _sanitize_analysis_characters,
    _with_known_characters,
)
from core.models import Project


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
        # Kademe 1: json.loads() başarılı, list parse edilir →
        # _ensure_dict(list) → {} döner. Kademe 3'e hiç ulaşılmaz.
        result = _parse_json_response('[{"item": 1}, {"item": 2}]')
        assert isinstance(result, dict)

    def test_whitespace_only_returns_dict(self):
        result = _parse_json_response("   \n\t  ")
        assert isinstance(result, dict)

    def test_object_character_schema_parses(self):
        text = (
            '{"characters":[{"name":"Jin-Woo","aliases":["Sung"],'
            '"gender":"male","appearance":"black hair"}]}'
        )
        result = _parse_json_response(text)
        assert result["characters"][0]["name"] == "Jin-Woo"


class TestSanitizeAnalysisCharacters:
    def test_string_list_becomes_records(self):
        parsed = _sanitize_analysis_characters({"characters": ["Jin-Woo", "Protagonist"]})
        names = [r.get("name") for r in parsed["characters"]]
        assert "Jin-Woo" in names
        assert "Protagonist" not in names

    def test_object_list_keeps_appearance(self):
        parsed = _sanitize_analysis_characters({
            "characters": [
                {"name": "Jin-Woo", "gender": "male", "appearance": "black hair", "aliases": []},
                {"name": "yellow hair", "gender": "female", "appearance": "", "aliases": []},
            ]
        })
        named = [r for r in parsed["characters"] if r.get("name")]
        unnamed = [r for r in parsed["characters"] if not r.get("name")]
        assert named[0]["name"] == "Jin-Woo"
        assert unnamed and "yellow" in unnamed[0]["appearance"].lower()

    def test_project_resolves_visual_label(self):
        from core.character_bible import upsert_characters
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        parsed = _sanitize_analysis_characters(
            {"characters": [{"name": "", "appearance": "black hair"}]},
            project=p,
        )
        assert parsed["characters"][0]["name"] == "Jin-Woo"

    def test_with_known_characters_injects_roster(self):
        from core.character_bible import upsert_characters
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "aliases": ["Sung"],
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        out = _with_known_characters("BASE PROMPT", project=p)
        assert "Jin-Woo" in out
        assert "black hair" in out
        assert "KNOWN CAST" in out

    def test_analyze_image_locks_english(self, monkeypatch):
        from core.ai_analyzer import AIAnalyzer
        captured = {}

        class FakeClient:
            def vision_analyze(self, model, image_path, prompt, fallback_models=None):
                captured["prompt"] = prompt
                return {"content": '{"scene":"A gate.","action":"He opens it."}', "model": model, "usage": {}}

        analyzer = AIAnalyzer.__new__(AIAnalyzer)
        analyzer._settings = type("S", (), {
            "has_api_key": lambda self: True,
            "reload": lambda self: None,
            "get_api_key": lambda self: "sk-test",
            "get": lambda self, key, default=None: default,
        })()
        analyzer._client = FakeClient()
        result = analyzer.analyze_image("panel.png", "google/gemini-2.5-flash")
        assert "OUTPUT LANGUAGE: English only" in captured["prompt"]
        assert result["scene"] == "A gate."

    def test_vision_prompt_uses_object_schema(self):
        from pathlib import Path
        import json
        data = json.loads(
            (Path(__file__).resolve().parent.parent / "config" / "prompts.json").read_text(encoding="utf-8")
        )
        vision = data["vision_analysis"]
        assert "{name, aliases, gender, appearance}" in vision or '"appearance"' in vision
        assert "yellow hair" in vision.lower()
        p1 = data["script_prompt_1_universal"].lower()
        assert "yellow hair" in p1
        assert "isim" in p1 or "name" in p1


class TestUsableAnalysisCache:
    def test_parse_error_is_not_usable(self):
        from core.ai_analyzer import _is_usable_analysis
        assert not _is_usable_analysis({"raw_text": "oops", "parse_error": True})
        assert not _is_usable_analysis({"error": "timeout"})
        assert not _is_usable_analysis({"raw_text": "no fields"})
        assert _is_usable_analysis({"scene": "gate", "action": "opens"})

    def test_extra_braces_after_object_still_parse(self):
        text = 'note {ignored} then {"scene": "battle", "action": "slash"} extra }'
        result = _parse_json_response(text)
        assert result.get("scene") == "battle"
        assert result.get("parse_error") is not True

    def test_analyze_chapter_retries_parse_error(self, tmp_path, monkeypatch):
        from core.models import Chapter, ImageData
        from core.ai_analyzer import AIAnalyzer

        img = tmp_path / "p.png"
        img.write_bytes(b"x")
        chapter = Chapter(
            id="c1",
            name="ch",
            images=[ImageData(path=str(img), filename="p.png", order=0)],
            analysis_data={"0": {"raw_text": "garbled", "parse_error": True}},
        )
        analyzer = AIAnalyzer.__new__(AIAnalyzer)
        analyzer._client = None

        class _SM:
            def get(self, *args, **kwargs):
                return 0

        analyzer._settings = _SM()
        called = []

        def fake_analyze(path, model, known_names=None, project=None):
            called.append(path)
            return {"scene": "gate", "action": "opens"}

        monkeypatch.setattr(analyzer, "analyze_image", fake_analyze)
        out = analyzer.analyze_chapter(chapter, "dummy-model")
        assert called
        assert out["0"]["scene"] == "gate"
        assert not out["0"].get("parse_error")