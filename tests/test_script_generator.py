"""
RecapAI - ScriptGenerator yardımcı fonksiyon testleri.
Çalıştırma: pytest tests/test_script_generator.py -v
"""

import pytest

import re

from core.script_generator import (
    _sanitize_characters,
    _sanitize_character_records,
    _scrub_generic_labels,
    _is_first_chapter,
    _is_generic_character_label,
    format_characters_display,
    list_niches,
    CHUNK_SIZE,
    DEFAULT_NICHE,
    VALID_NICHES,
)
from core.models import Chapter, Project


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

    def test_drops_visual_hints(self):
        result = _sanitize_characters(
            ["kırmızı pelerinli", "saçlı kız", "blonde woman", "dark hair man", "Protagonist", "Jin-Woo"],
            "tr",
        )
        assert result == ["Jin-Woo"]

    def test_generic_label_flags(self):
        assert _is_generic_character_label("blonde woman")
        assert _is_generic_character_label("dark-haired man")
        assert _is_generic_character_label("kızıl saçlı elf")
        assert _is_generic_character_label("yellow hair")
        assert _is_generic_character_label("black hair")
        assert _is_generic_character_label("şövalye")
        assert _is_generic_character_label("knight")
        assert not _is_generic_character_label("Jin-Woo")
        assert not _is_generic_character_label("Cha Hae-In")

    def test_drops_yellow_hair_and_knight(self):
        result = _sanitize_characters(
            ["yellow hair", "black hair", "şövalye", "genç adam", "Varkas"],
            language="en",
        )
        assert result == ["Varkas"]

    def test_records_keep_unnamed_appearance(self):
        recs = _sanitize_character_records(
            [{"name": "", "gender": "male", "appearance": "black hair", "aliases": []}],
            language="en",
        )
        assert len(recs) == 1
        assert recs[0]["name"] == ""
        assert recs[0]["appearance"] == "black hair"
        assert recs[0]["gender"] == "male"

    def test_known_name_resolves_generic(self):
        result = _sanitize_characters(
            [{"name": "", "appearance": "black hair"}],
            language="en",
            known_names=["Jin-Woo"],
        )
        # known_names exact-key eşleşmesi appearance'ı çözmez; isim listesi boş kalır
        assert result == []

    def test_project_resolves_appearance_to_canonical(self):
        from core.character_bible import upsert_characters
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "aliases": ["Sung"],
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        result = _sanitize_characters(
            [{"name": "", "appearance": "black hair"}],
            language="en",
            project=p,
        )
        assert result == ["Jin-Woo"]

    def test_scrub_cast_replaces_yellow_hair(self):
        t = _scrub_generic_labels(
            "The yellow hair walks in. The black hair follows.",
            "en",
            cast=[{"name": "Cha Hae-In", "appearance": "yellow hair", "gender": "female"}],
        )
        low = t.lower()
        assert "yellow hair" not in low
        assert "Cha Hae-In" in t

    def test_scrub_project_replaces_black_hair(self):
        from core.character_bible import upsert_characters
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        t = _scrub_generic_labels("The black hair draws a blade.", "en", project=p)
        assert "black hair" not in t.lower()
        assert "Jin-Woo" in t

    def test_scrub_blonde_woman_to_she_without_cast(self):
        t = _scrub_generic_labels("The blonde woman opens the door.", "en")
        assert "blonde woman" not in t.lower()
        assert "she" in t.lower()

    def test_format_characters_display_named_and_unnamed(self):
        text = format_characters_display([
            {"name": "Jin-Woo", "appearance": "black hair"},
            {"name": "", "appearance": "yellow hair"},
        ])
        assert "Jin-Woo" in text
        assert "+1" in text

    def test_format_characters_display_empty(self):
        assert format_characters_display([]) == "—"


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
        assert "script_outline" in data
        assert "script_voiceover" in data
        assert "{retention_layer}" in data["script_generation"]
        assert "{beats_block}" in data["script_voiceover"]
        ret = data["script_prompt_retention"].lower()
        assert "kanca" in ret or "hook" in ret or "cold" in ret
        assert "tek anlat" in ret or "yayılır" in ret
        vo = data["script_voiceover"].lower()
        p1 = data["script_prompt_1_universal"].lower()
        assert "üçüncü şahıs" in p1 or "ucuncu sahis" in p1
        assert "kitap" in p1
        assert "panellere" in vo


class TestResolveStyle:
    def test_default_fresh(self):
        from core.script_generator import resolve_style
        assert resolve_style(None) == "fresh"
        assert resolve_style("") == "fresh"
        assert resolve_style("unknown") == "fresh"
        assert resolve_style("epic") == "epic"


class TestPanelGlueFillsEmptyVo:
    """Analiz verisi olan bos VO, sessize dusmeden once glue ile dolar."""

    def test_empty_vo_fills_from_analysis(self):
        from core.models import SegmentData
        from core.script_generator import _fill_empty_story_panels

        chapter = Chapter(
            id="c1",
            name="Chapter 1",
            analysis_data={
                "0": {
                    "scene": "Sunho stands in the crowded hallway.",
                    "action": "He turns away before she can answer him.",
                },
            },
        )
        segments = [SegmentData(image_index=0, text="", duration=0.0, role="beat", beat_id=0)]
        _fill_empty_story_panels(segments, chapter, "en", "medium")
        text = (segments[0].text or "").strip()
        assert text
        assert "turns away" in text.lower() or "crowded hallway" in text.lower()
        assert segments[0].duration > 0.75

    def test_wrong_language_vo_fills_from_analysis(self):
        from core.models import SegmentData
        from core.script_generator import _drop_wrong_language_segments

        chapter = Chapter(
            id="c1",
            name="Chapter 1",
            analysis_data={
                "0": {"action": "She blocks the strike before it lands."},
            },
        )
        segments = [
            SegmentData(
                image_index=0,
                text="Bu sahne hikayenin aksiyon seviyesini yükseltir.",
                duration=3.0,
                role="beat",
                beat_id=0,
            )
        ]
        _drop_wrong_language_segments(segments, "en", chapter=chapter, length="medium")
        text = (segments[0].text or "").strip()
        assert text
        assert "hikaye" not in text.lower()
        assert "blocks the strike" in text.lower()

    def test_empty_analysis_stays_silent(self):
        from core.models import SegmentData
        from core.script_generator import SILENT_HOLD_SEC, _fill_empty_story_panels

        chapter = Chapter(id="c1", name="Chapter 1", analysis_data={})
        segments = [SegmentData(image_index=0, text="", duration=0.0, role="beat", beat_id=0)]
        _fill_empty_story_panels(segments, chapter, "en", "medium")
        assert not (segments[0].text or "").strip()
        assert abs((segments[0].duration or 0) - SILENT_HOLD_SEC) < 0.05


class TestChatRetriesTruncatedJson:
    """Kesik veya bozuk JSON, dil kaçışıyla aynı retry döngüsüne girer."""

    def test_truncated_json_retries_with_larger_max_tokens(self):
        from core.script_generator import ScriptGenerator, _extract_json_obj

        calls = []
        good = '{"beats":[{"id":0,"text":"He turns away before she can answer him."}]}'

        def chat_completion(model, messages, temperature=0.7, max_tokens=2000, fallback_models=None):
            calls.append(max_tokens)
            if len(calls) == 1:
                return {
                    "content": '{"beats":[{"id":0,"text":"He said \\"wait',
                    "finish_reason": "length",
                    "model": model,
                    "usage": {},
                }
            return {"content": good, "finish_reason": "stop", "model": model, "usage": {}}

        gen = ScriptGenerator.__new__(ScriptGenerator)
        gen._client = type("Client", (), {"chat_completion": staticmethod(chat_completion)})()
        gen._fallback_models = lambda: []
        prompt = 'SADECE JSON. {"beats":[{"id":0,"text":"..."}]}'
        text = gen._chat("test-model", prompt, temperature=0.7, max_tokens=2000, language="en", retries=1)
        assert _extract_json_obj(text)
        assert len(calls) == 2
        assert calls[1] == 3000
        assert calls[1] > calls[0]
        assert calls[1] <= 8000


class TestExtractJsonRepairsTruncation:
    """Kesilmiş JSON'dan kapanmış alanlar kurtarılır."""

    def test_missing_closers_keep_existing_fields(self, caplog):
        import logging
        from core.script_generator import _extract_json_obj

        raw = (
            '{"hook":"Boom.","beats":[{"id":0,"text":"He turns away before she can answer him."}'
        )
        with caplog.at_level(logging.WARNING):
            data = _extract_json_obj(raw)
        assert data["hook"] == "Boom."
        assert data["beats"][0]["id"] == 0
        assert "turns away" in data["beats"][0]["text"]
        assert "JSON onarıldı (kesilmiş çıktı)" in caplog.text

    def test_cut_inside_string_keeps_earlier_beat(self):
        from core.script_generator import _extract_json_obj

        raw = '{"beats":[{"id":0,"text":"She blocks the strike."},{"id":1,"text":"Then he said'
        data = _extract_json_obj(raw)
        assert data["beats"][0]["text"] == "She blocks the strike."
        assert data["beats"][1]["id"] == 1
        assert data["beats"][1]["text"].startswith("Then he said")

    def test_complete_json_is_not_repaired(self, caplog):
        import logging
        from core.script_generator import _extract_json_obj

        raw = '{"hook":"Boom.","beats":[{"id":0,"text":"A"}]}'
        with caplog.at_level(logging.WARNING):
            data = _extract_json_obj(raw)
        assert data["hook"] == "Boom."
        assert "JSON onarıldı" not in caplog.text
