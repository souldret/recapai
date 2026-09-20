"""Beat kümeleme, linter, character bible ve script yardımcıları."""

from core.models import Chapter, ImageData, Project, SegmentData
from core.beat_engine import cluster_beats, detect_niche, pick_cold_open_image
from core.script_generator import (
    _distribute_text,
    _duration_note,
    _parse_beats_voiceover,
    _sanitize_outline_beats,
    _source_note,
    _split_sentences,
    fit_segments_to_target,
    resolve_target_minutes,
    ScriptGenerator,
)
from core.script_linter import lint_text, lint_segments, issue_count
from core.character_bible import (
    upsert_names,
    upsert_characters,
    format_for_prompt,
    last_time_source,
    extract_names_from_chapter,
    resolve_name,
    apply_names_to_beats,
    update_character,
    get_entry,
    get_entries,
    set_entries,
    prune_generic_entries,
)


def _img(i: int) -> ImageData:
    return ImageData(path=f"p{i}.jpg", filename=f"p{i}.jpg", order=i)


def _chapter(n: int, analyses: dict) -> Chapter:
    return Chapter(
        id="c1",
        name="Chapter 2",
        images=[_img(i) for i in range(n)],
        analysis_data=analyses,
    )


class TestBeatCluster:
    def test_merges_similar_fillers(self):
        analyses = {
            str(i): {
                "scene": "Koridorda yürüyorlar",
                "action": "İlerliyorlar",
                "mood": "calm",
                "setting": "koridor",
                "important": False,
                "characters": ["Jin-Woo"],
            }
            for i in range(6)
        }
        analyses["3"] = {
            "scene": "Sistem rank atladı",
            "action": "Güç patlaması, one-shot",
            "mood": "hype",
            "setting": "arena",
            "important": True,
            "characters": ["Jin-Woo"],
            "dialogues": ["You are not ready"],
        }
        ch = _chapter(6, analyses)
        beats = cluster_beats(ch, target_minutes=5)
        assert len(beats) < 6
        assert any(b.important for b in beats)
        assert beats[0].role in ("setup", "beat", "cold_open", "last_time")
        assert beats[-1].role == "cliffhanger"

    def test_cold_open_skips_cover(self):
        analyses = {
            "0": {"scene": "Kapak yürüyüşü", "action": "Yürüyor", "important": False},
            "1": {"scene": "Sakin", "action": "Konuşur", "important": False},
            "2": {
                "scene": "İhanet",
                "action": "Kralı öldürür, intikam",
                "important": True,
                "dialogues": ["Die"],
            },
        }
        ch = _chapter(3, analyses)
        beats = cluster_beats(ch)
        idx = pick_cold_open_image(ch, beats)
        assert idx == 2

    def test_detect_power_fantasy(self):
        ch = _chapter(2, {
            "0": {"scene": "Hunter dungeon", "action": "System rank up", "mood": "hype"},
            "1": {"scene": "Level skill", "action": "Awakening", "mood": "epic"},
        })
        assert detect_niche(ch) == "power_fantasy"

    def test_short_one_panel_one_beat(self):
        analyses = {str(i): {"scene": "x", "action": "y", "important": False} for i in range(5)}
        ch = _chapter(5, analyses)
        beats = cluster_beats(ch, length="short")
        assert len(beats) == 5
        assert all(len(b.panel_indices) == 1 for b in beats)

    def test_rejects_turkish_when_english(self):
        from core.script_generator import _wrong_language
        assert _wrong_language("Bu sahne hikayenin aksiyon seviyesini yükseltir.", "en")
        assert _wrong_language("The kızıl saçlı elf screams as invisible force attacks.", "en")
        assert _wrong_language(
            "Bu sahne, hikayenin aksiyon ve çatışma seviyesini yükselterek, karakterin gücünü gösterir.",
            "en",
        )
        assert not _wrong_language("The hunter screams as the force hits.", "en")
        assert _wrong_language("The hunter yükseltir the rank.", "en")
        assert not _wrong_language("Jin-Woo opens the gate.", "en", ["Jin-Woo"])
        assert not _wrong_language("The surveyor checks the gate and they attack.", "en")
        assert not _wrong_language("He makes a remark and they attack.", "en")

    def test_short_budget_is_tight(self):
        from core.beat_engine import _word_budget
        short = _word_budget("beat", 4, None, 10, length="short")
        medium = _word_budget("beat", 4, None, 10, length="medium")
        assert short <= 16
        assert medium >= 36
        timed = _word_budget("beat", 4, 2.0, 20, length="medium")
        assert 20 <= timed <= 90

    def test_medium_auto_budget_is_minutes_not_seconds(self):
        """Otomatik Orta ~6 dk; 40–50 saniyelik 2–5 cümle bütçesi değil."""
        from core.beat_engine import _word_budget, LENGTH_MINUTES
        assert LENGTH_MINUTES["medium"] == 6.0
        assert LENGTH_MINUTES["long"] == 9.0
        auto = _word_budget("beat", 4, None, 10, length="medium")
        explicit = _word_budget("beat", 4, 6.0, 10, length="medium")
        assert auto == explicit
        assert auto >= 80
        long_auto = _word_budget("beat", 4, None, 10, length="long")
        assert long_auto >= auto


class TestDurationAndLanguageLock:
    def test_resolve_target_minutes_presets(self):
        assert resolve_target_minutes("short", None) is None
        assert resolve_target_minutes("medium", None) == 6.0
        assert resolve_target_minutes("long", None) == 9.0
        assert resolve_target_minutes("medium", 0) == 6.0
        assert resolve_target_minutes("medium", 3.5) == 3.5
        assert resolve_target_minutes("short", 2.0) == 2.0
        assert resolve_target_minutes("unknown", None) == 6.0

    def test_fit_segments_shortens_filler(self):
        filler = " ".join(["kelime"] * 80)
        segs = [
            SegmentData(0, "hook", duration=3.0, role="cold_open"),
            SegmentData(1, filler, duration=40.0, role="filler"),
        ]
        out = fit_segments_to_target(segs, target_minutes=0.2, language="tr")
        assert len(out[1].text.split()) < 80
        assert out[1].duration < 40.0

    def test_source_note_wraps_turkish_for_english(self):
        dump = "Bu sahne hikayenin aksiyon seviyesini yükseltir."
        wrapped = _source_note(dump, "en")
        assert wrapped.startswith("SOURCE NOTES")
        assert "TRANSLATE" in wrapped
        assert dump in wrapped
        same = _source_note("He opens the gate.", "en")
        assert same == "He opens the gate."
        assert _source_note("", "en") == ""

    def test_source_note_wraps_english_for_turkish(self):
        wrapped = _source_note("He opens the iron gate now.", "tr")
        assert wrapped.startswith("KAYNAK NOTLARI")
        assert "He opens the iron gate now." in wrapped

    def test_sanitize_outline_drops_turkish_payload(self):
        from core.beat_engine import StoryBeat
        dump = "Bu sahne hikayenin aksiyon seviyesini yükseltir."
        beats = [
            StoryBeat(0, [0], "setup", summary="He opens the gate", action=dump, scene=dump),
            StoryBeat(1, [1], "beat", summary=dump, action=dump, scene=dump),
        ]
        raw = [
            {"id": 0, "payload": dump, "stakes": dump},
            {"id": 1, "payload": dump, "stakes": dump},
        ]
        out = _sanitize_outline_beats(raw, beats, "en")
        assert out[0]["payload"] == "He opens the gate"
        assert out[0]["stakes"] == ""
        assert out[1]["payload"] == ""
        assert out[1]["stakes"] == ""
        blob = " ".join(item["payload"] + " " + item["stakes"] for item in out)
        assert "hikaye" not in blob.lower()
        assert "aksiyon" not in blob.lower()

    def test_duration_note_medium_asks_for_minutes(self):
        from core.beat_engine import StoryBeat
        chunk = [StoryBeat(0, [0], "setup", word_budget=90), StoryBeat(1, [1], "beat", word_budget=90)]
        note = _duration_note("medium", 0.0, "en", chunk, 10)
        assert "~6 minutes" in note
        assert "Two sentences is a minimum, not the target" in note
        short = _duration_note("short", 0.0, "en", chunk, 10)
        assert "1 sentence" in short
        assert "16 words" in short

    def test_format_beats_wraps_turkish_source(self):
        from core.beat_engine import StoryBeat, format_beats_for_prompt
        dump = "Bu sahne hikayenin aksiyon seviyesini yükseltir."
        beats = [StoryBeat(0, [0], "setup", scene=dump, action=dump, mood="Gergin", setting="koridor")]
        prompt = format_beats_for_prompt(beats, "en")
        assert "SOURCE NOTES" in prompt
        assert "TRANSLATE" in prompt
        assert "never copy" in prompt.lower()


class TestDistribute:
    def test_one_sentence_stays_intact(self):
        parts = _distribute_text("He opens the heavy iron door now.", 4)
        assert parts[0].startswith("He opens")
        assert "door" in parts[0]
        assert parts[1] == ""
        assert parts[2] == ""

    def test_short_length_one_sentence_per_beat(self):
        from core.beat_engine import StoryBeat
        analyses = {str(i): {"scene": "x", "action": "The hunter ranks up in the dungeon"} for i in range(3)}
        ch = _chapter(3, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        long_vo = (
            "He walks into the dungeon and the system screams numbers at him. "
            "Then the rank board explodes and everyone stares. "
            "After that he one-shots the boss and the room goes silent."
        )
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0, 1, 2], "setup", word_budget=16)],
            {0: long_vo}, {}, "en", 0, use_hook=False, length="short",
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken = [s for s in story if (s.text or "").strip()]
        assert spoken
        assert len(spoken[0].text.split()) <= 16
        assert spoken[0].text.count(".") <= 2
        assert all(len(s.text.split()) <= 16 for s in spoken)

    def test_materialize_one_vo_per_beat_not_per_panel(self):
        from core.beat_engine import StoryBeat
        analyses = {
            "0": {"scene": "x", "action": "He opens the gate"},
            "1": {"scene": "Hall", "action": "The rank board lights up"},
            "2": {"scene": "x", "action": "Steel cracks under the blow"},
        }
        ch = _chapter(3, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0, 1, 2], "setup")],
            {0: "He opens the gate."},
            {}, "en", 0, use_hook=False,
        )
        story = [s for s in segs if s.role != "cold_open"]
        assert len(story) == 1
        assert story[0].text.startswith("He opens")
        assert not any("rank board" in (s.text or "").lower() for s in story)
        assert not any("hikaye" in (s.text or "").lower() for s in story)

    def test_user_bug_mixed_and_analysis_dump(self):
        from core.beat_engine import StoryBeat
        from core.script_generator import _wrong_language
        mixed = "The kızıl saçlı elf screams as invisible force attacks."
        dump = (
            "Bu sahne, hikayenin aksiyon ve çatışma seviyesini yükselterek, "
            "karakterin gücünü veya maruz kaldığı tehlikenin boyutunu okuyucuya."
        )
        assert _wrong_language(mixed, "en")
        assert _wrong_language(dump, "en")
        analyses = {
            "0": {"action": dump},
            "1": {"action": mixed},
            "2": {"action": "He blocks the strike"},
        }
        ch = _chapter(3, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch,
            [
                StoryBeat(0, [0], "setup", word_budget=16),
                StoryBeat(1, [1], "beat", word_budget=16),
                StoryBeat(2, [2], "cliffhanger", word_budget=16),
            ],
            {0: mixed, 1: dump, 2: "He blocks the strike."},
            {"hook": dump},
            "en", 0, use_hook=True, length="short",
        )
        blob = " ".join(s.text or "" for s in segs)
        assert "kızıl" not in blob
        assert "hikaye" not in blob.lower()
        assert "aksiyon" not in blob.lower()
        assert "okuyucuya" not in blob.lower()
        assert any("blocks" in (s.text or "").lower() for s in segs)

    def test_does_not_repeat_last_line_or_mood_dump(self):
        from core.beat_engine import StoryBeat
        analyses = {
            str(i): {"mood": "Gergin, heybetli, tehditkar", "action": ""}
            for i in range(1, 8)
        }
        analyses["0"] = {"action": "He opens the gate"}
        ch = _chapter(8, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch,
            [StoryBeat(0, list(range(8)), "setup")],
            {0: "He opens the gate."},
            {},
            "en",
            0,
            use_hook=False,
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken = [(s.text or "").strip() for s in story]
        assert len(spoken) == 1
        assert spoken[0].startswith("He opens")
        assert spoken.count("He opens the gate.") == 1
        assert not any("Gergin" in t or "heybetli" in t for t in spoken)

    def test_english_last_time_drops_turkish_source(self):
        from core.beat_engine import StoryBeat
        ch = _chapter(3, {str(i): {"scene": "x", "action": "He opens the gate"} for i in range(3)})
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch,
            [StoryBeat(0, [0, 1, 2], "setup")],
            {0: "He opens the gate."},
            {"hook": "The king falls."},
            "en",
            2,
            use_hook=True,
            include_last_time=True,
            last_src="Geçen bölümde ihanet oldu ve hikaye yükseltir.",
        )
        blob = " ".join(s.text or "" for s in segs)
        assert "ihanet" not in blob.lower()
        assert "hikaye" not in blob.lower()
        assert "yükseltir" not in blob.lower()
        assert any("king" in (s.text or "").lower() for s in segs)

    def test_english_script_drops_turkish_analysis(self):
        from core.beat_engine import StoryBeat
        analyses = {
            "0": {"scene": "x", "action": "He opens the gate"},
            "1": {"scene": "x", "action": "Bu sahne hikayenin aksiyon seviyesini yükseltir"},
            "2": {"scene": "x", "action": "The force hits her"},
        }
        ch = _chapter(3, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0, 1, 2], "setup")],
            {0: "He opens the gate."},
            {}, "en", 0, use_hook=False,
        )
        story = [s for s in segs if s.role != "cold_open"]
        blob = " ".join(s.text or "" for s in story)
        assert len(story) == 1
        assert "hikaye" not in blob.lower()
        assert "aksiyon" not in blob.lower()

    def test_sentences_spread(self):
        parts = _distribute_text("Bir. İki. Üç.", 3)
        assert all(parts)
        assert len(parts) == 3

    def test_split_sentences(self):
        assert len(_split_sentences("A. B! C?")) == 3


class TestParseVoiceover:
    def test_beats_json(self):
        raw = '{"hook":"Boom.","beats":[{"id":0,"text":"Açılış"},{"id":1,"text":"Devam"}]}'
        parsed = _parse_beats_voiceover(raw)
        assert parsed[0] == "Açılış"
        assert parsed[1] == "Devam"
        assert parsed[-1] == "Boom."

    def test_align_one_based_ids(self):
        from core.script_generator import _align_vo_to_beats
        parsed = {1: "Bir", 2: "İki", -1: "Hook"}
        aligned = _align_vo_to_beats(parsed, [0, 1])
        assert aligned[0] == "Bir"
        assert aligned[1] == "İki"
        assert aligned[-1] == "Hook"


class TestLinter:
    def test_flags_protagonist(self):
        issues = lint_text("The protagonist walks in.", role="beat", is_first=True)
        assert any("etiket" in i.lower() or "jenerik" in i.lower() for i in issues)

    def test_does_not_flag_emdash(self):
        issues = lint_text("He turns. The door opens.", role="beat")
        assert not any("em-dash" in i.lower() or "noktalı" in i.lower() for i in issues)

    def test_flags_hair_appearance_label(self):
        issues = lint_text("The yellow hair walks in.", role="beat")
        assert any("saç" in i.lower() or "görünüm" in i.lower() for i in issues)
        issues_tr = lint_text("Kızıl saçlı kapıyı açar.", role="beat")
        assert any("saç" in i.lower() or "görünüm" in i.lower() for i in issues_tr)

    def test_scrub_keeps_ellipsis(self):
        from core.script_generator import _scrub_generic_labels
        t = _scrub_generic_labels("And then... nothing.", "en")
        assert "..." in t
        assert "nothing" in t.lower()

    def test_flags_visual_meta(self):
        issues = lint_text("Bu panelde görüyoruz ki kapı açılır.", role="beat")
        assert issues

    def test_filler_too_long(self):
        issues = lint_text("kelime " * 50, role="filler")
        assert any("uzun" in i.lower() for i in issues)

    def test_lint_segments_then_chain(self):
        segs = [
            SegmentData(0, "Sonra gider."),
            SegmentData(1, "Sonra döner."),
            SegmentData(2, "Sonra kaçar."),
        ]
        lint_segments(segs)
        assert issue_count(segs) >= 1


class TestBible:
    def test_upsert_and_prompt(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_names(p, ["Jin-Woo", "Cha Hae-In"], "c1")
        text = format_for_prompt(p, "tr")
        assert "Jin-Woo" in text
        assert "Cha Hae-In" in text

    def test_apply_names_to_beats(self):
        from core.beat_engine import StoryBeat
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_names(p, ["Jin-Woo"], "c1")
        beats = [StoryBeat(0, [0], "setup", action="Jin-Woo opens the gate", characters=[])]
        apply_names_to_beats(beats, p)
        assert "Jin-Woo" in beats[0].characters

        # Panel-local: metinde/görünümde geçmeyen isim beat'e basılmaz
        other = [StoryBeat(1, [1], "beat", action="The gate opens", characters=[])]
        apply_names_to_beats(other, p)
        assert "Jin-Woo" not in other[0].characters

    def test_apply_names_matches_appearance(self):
        from core.beat_engine import StoryBeat
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        beats = [StoryBeat(0, [0], "setup", action="The black hair opens the gate", characters=[])]
        apply_names_to_beats(beats, p)
        assert "Jin-Woo" in beats[0].characters

    def test_resolve_name_from_appearance(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Cha Hae-In",
            "aliases": ["Hae-In"],
            "gender": "female",
            "appearance": "yellow hair",
        }], "c1")
        assert resolve_name(p, "yellow hair") == "Cha Hae-In"
        assert resolve_name(p, "Hae-In") == "Cha Hae-In"
        assert resolve_name(p, "blonde woman") == ""

    def test_cluster_beats_accepts_project(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        ch = _chapter(2, {
            "0": {
                "scene": "Gate",
                "action": "Opens",
                "characters": [{"name": "", "appearance": "black hair"}],
            },
            "1": {
                "scene": "Gate",
                "action": "Steps through",
                "characters": ["Jin-Woo"],
            },
        })
        beats = cluster_beats(ch, project=p)
        names = [n for b in beats for n in (b.characters or [])]
        assert "Jin-Woo" in names
        assert "black hair" not in names

    def test_extract_skips_generic(self):
        ch = _chapter(1, {"0": {"characters": ["Protagonist", "Varkas"]}})
        names = extract_names_from_chapter(ch)
        assert "Varkas" in names
        assert "Protagonist" not in names

    def test_extract_skips_appearance_labels(self):
        ch = _chapter(1, {"0": {"characters": ["blonde woman", "dark hair man", "kızıl saçlı", "Jin-Woo"]}})
        names = extract_names_from_chapter(ch)
        assert names == ["Jin-Woo"]

    def test_update_character_renames_and_keeps_old_as_alias(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        updated = update_character(p, "Jin-Woo", canonical="Sung Jin-Woo")
        assert updated is not None
        assert get_entry(p, "Jin-Woo") is None
        ent = get_entry(p, "Sung Jin-Woo")
        assert ent is not None
        assert "Jin-Woo" in (ent.get("aliases") or [])
        assert ent.get("appearance") == "black hair"
        assert ent.get("gender") == "male"

    def test_update_character_rename_keeps_old_alias_when_aliases_passed(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "aliases": ["Sung"],
            "gender": "male",
        }], "c1")
        updated = update_character(
            p, "Jin-Woo",
            canonical="Sung Jin-Woo",
            aliases=["Sung"],
        )
        assert updated is not None
        aliases = get_entry(p, "Sung Jin-Woo").get("aliases") or []
        assert "Jin-Woo" in aliases
        assert "Sung" in aliases

    def test_update_character_overrides_appearance(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        update_character(
            p, "Jin-Woo",
            appearance="dark hair, black coat",
            gender="male",
            notes="Shadow Monarch",
        )
        ent = get_entry(p, "Jin-Woo")
        assert ent["appearance"] == "dark hair, black coat"
        assert ent["notes"] == "Shadow Monarch"

    def test_update_character_rejects_generic_and_appearance_names(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{"name": "Jin-Woo", "gender": "male"}], "c1")
        assert update_character(p, "Jin-Woo", canonical="blonde woman") is None
        assert update_character(p, "Jin-Woo", canonical="Protagonist") is None
        assert get_entry(p, "Jin-Woo") is not None

    def test_update_character_merges_into_existing(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        upsert_characters(p, [{
            "name": "Jin-Woo",
            "gender": "male",
            "appearance": "black hair",
        }], "c1")
        upsert_characters(p, [{
            "name": "Sung Jin-Woo",
            "aliases": ["Sung"],
            "gender": "male",
        }], "c1")
        updated = update_character(p, "Jin-Woo", canonical="Sung Jin-Woo")
        assert updated is not None
        assert get_entry(p, "Jin-Woo") is None
        ent = get_entry(p, "Sung Jin-Woo")
        aliases = ent.get("aliases") or []
        assert "Jin-Woo" in aliases
        assert "Sung" in aliases
        assert ent.get("appearance") == "black hair"
        assert len(get_entries(p)) == 1

    def test_prune_generic_drops_appearance_canonical(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        set_entries(p, [
            {"canonical": "blonde woman", "aliases": [], "gender": "female"},
            {"canonical": "Jin-Woo", "aliases": ["black hair"], "gender": "male"},
        ])
        kept = prune_generic_entries(p)
        names = [e["canonical"] for e in kept]
        assert names == ["Jin-Woo"]
        assert "black hair" not in (get_entry(p, "Jin-Woo").get("aliases") or [])

    def test_format_for_prompt_does_not_persist_prune(self):
        p = Project(id="p", name="t", created_at="", updated_at="")
        set_entries(p, [
            {"canonical": "blonde woman", "aliases": [], "gender": "female"},
            {"canonical": "Jin-Woo", "aliases": [], "gender": "male"},
        ])
        text = format_for_prompt(p, "tr")
        assert "Jin-Woo" in text
        assert "blonde woman" not in text
        leftover = [e["canonical"] for e in get_entries(p)]
        assert "blonde woman" in leftover

    def test_generate_outline_sanitizes_turkish_payload(self):
        from core.beat_engine import StoryBeat
        dump = "Bu sahne hikayenin aksiyon seviyesini yükseltir."
        gen = ScriptGenerator(client=object())
        gen._chat = lambda *a, **k: (
            '{"hook":"He opens the iron gate now.",'
            '"last_time":"",'
            '"beats":[{"id":0,"payload":"%s","stakes":"%s"}]}' % (dump, dump)
        )
        chapter = Chapter(id="c1", name="ch", images=[_img(0)])
        beats = [StoryBeat(0, [0], "setup", summary="He opens the gate", action=dump, scene=dump)]
        data = gen._generate_outline(chapter, beats, "dummy", "en", None, "", None)
        assert data["beats"][0]["payload"] == "He opens the gate"
        assert "hikaye" not in (data["beats"][0]["payload"] + data["beats"][0]["stakes"]).lower()

    def test_last_time_from_segments(self):
        prev = Chapter(id="c0", name="Chapter 1", images=[_img(0)], segments=[
            SegmentData(0, "Düşman kapıdaydı ve bedel henüz ödenmedi.")
        ])
        cur = Chapter(id="c1", name="Chapter 2", images=[_img(0)])
        p = Project(id="p", name="t", created_at="", updated_at="", chapters=[prev, cur])
        src = last_time_source(p, cur)
        assert "Düşman" in src


class TestMaterialize:
    def test_flash_forward_prepends_hook(self):
        from core.beat_engine import StoryBeat
        ch = _chapter(3, {str(i): {"scene": "x", "action": "y"} for i in range(3)})
        beats = [
            StoryBeat(0, [0], "setup"),
            StoryBeat(1, [1, 2], "beat"),
        ]
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch, beats, {0: "Kurulum cümlesi.", 1: "Asıl vuruş. Bedel var."},
            {"hook": "Kralı kendi kılıcıyla kesti."}, "tr", 2,
            use_hook=True, include_last_time=False,
        )
        assert segs[0].role == "cold_open"
        assert segs[0].image_index == 2
        assert "Kralı" in segs[0].text
        assert any(s.image_index == 0 for s in segs)
        story = [s for s in segs if s.role not in ("cold_open", "last_time")]
        assert len(story) == 2
        assert story[0].image_index == 0

    def test_segment_roundtrip(self):
        s = SegmentData(3, "Merhaba", beat_id=1, role="rehook", lint_issues=["x"])
        d = s.to_dict()
        s2 = SegmentData.from_dict(d)
        assert s2.beat_id == 1
        assert s2.role == "rehook"
        assert s2.lint_issues == ["x"]

    def test_json_with_trailing_text(self):
        from core.script_generator import _extract_json_obj
        raw = '```json\n{"hook":"Boom.","beats":[{"id":"0","text":"A"}]}\n```\nThanks!'
        data = _extract_json_obj(raw)
        assert data["hook"] == "Boom."
        parsed = _parse_beats_voiceover(raw)
        assert parsed[0] == "A"
        assert parsed[-1] == "Boom."

    def test_last_time_uses_source_fallback(self):
        from core.beat_engine import StoryBeat
        ch = _chapter(3, {str(i): {"scene": "x", "action": "y"} for i in range(3)})
        beats = [StoryBeat(0, [0, 1, 2], "setup")]
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch, beats, {0: "Hikaye başlar. Devam eder. Cliff."},
            {"hook": "Kanca cümlesi burada."}, "tr", 2,
            use_hook=True, include_last_time=True,
            last_src="Geçen bölümde ihanet oldu.",
        )
        roles = [s.role for s in segs]
        assert "cold_open" in roles
        assert "last_time" in roles
        assert any("ihanet" in (s.text or "").lower() for s in segs)

    def test_compile_keeps_image_path(self):
        ch = _chapter(2, {"0": {"scene": "a"}, "1": {"scene": "b"}})
        gen = ScriptGenerator.__new__(ScriptGenerator)
        from core.beat_engine import StoryBeat
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0, 1], "setup")], {0: "Bir. İki."},
            {}, "tr", 0, use_hook=False,
        )
        story = [s for s in segs if s.role != "cold_open"]
        assert len(story) == 1
        assert all(s.image_path for s in segs)
