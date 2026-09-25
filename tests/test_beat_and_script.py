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
    estimate_auto_minutes,
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

    def test_short_clusters_panels(self):
        analyses = {str(i): {"scene": "x", "action": "y", "important": False} for i in range(5)}
        ch = _chapter(5, analyses)
        beats = cluster_beats(ch, length="short")
        assert len(beats) < 5
        assert len(beats) <= 8
        assert sum(len(b.panel_indices) for b in beats) == 5

    def test_single_rehook_not_every_important_panel(self):
        analyses = {
            "0": {"scene": "School hallway morning", "action": "Students gossip about a feared senior", "important": False, "setting": "hall", "mood": "tense"},
            "1": {"scene": "Nurse office skip", "action": "The senior hides from class again", "important": False, "setting": "nurse", "mood": "quiet"},
            "2": {"scene": "Cleaning duty assignment", "action": "Kurose gets stuck wiping the floor", "important": False, "setting": "classroom", "mood": "tired"},
            "3": {"scene": "Death threat from curtain", "action": "Someone yells leave or die", "important": True, "setting": "curtain", "mood": "fear", "dialogues": ["Get out"]},
            "4": {"scene": "Identity reveal", "action": "It is Haimiya the feared senior", "important": True, "setting": "curtain", "mood": "shock"},
            "5": {"scene": "Unexpected kindness", "action": "She teases him instead of hitting him", "important": True, "setting": "classroom", "mood": "soft"},
            "6": {"scene": "Intense staring", "action": "He cannot look away from her", "important": False, "setting": "desk", "mood": "awkward"},
            "7": {"scene": "Confession of pleasure", "action": "He says this feels really good and she recoils", "important": True, "setting": "desk", "mood": "twist", "dialogues": ["This feels good"]},
        }
        ch = _chapter(8, analyses)
        beats = cluster_beats(ch, length="short")
        rehooks = [b for b in beats if b.role == "rehook"]
        assert len(beats) >= 4
        assert len(rehooks) <= 1
        assert beats[0].role == "setup"
        assert beats[-1].role == "cliffhanger"

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
        assert _wrong_language("Sunho konuşuyor ve kalbi atıyor.", "en")
        assert not _wrong_language("Jin-Woo opens the gate.", "en", ["Jin-Woo"])
        assert not _wrong_language("The surveyor checks the gate and they attack.", "en")
        assert not _wrong_language("He makes a remark and they attack.", "en")

    def test_turkish_target_keeps_latin_turkish(self):
        from core.script_generator import _wrong_language
        assert not _wrong_language("Jin kapıyı açar ve içeri girer.", "tr")
        assert not _wrong_language("Avcı kapıdan geçer.", "tr")
        assert _wrong_language("The hunter opens the gate and they attack.", "tr")
        assert not _wrong_language("Jin-Woo kapıyı açar.", "tr", ["Jin-Woo"])

    def test_short_budget_is_tight(self):
        from core.beat_engine import _word_budget
        short = _word_budget("beat", 4, None, 10, length="short")
        medium = _word_budget("beat", 4, None, 10, length="medium")
        assert short <= 32
        assert medium >= 36
        timed = _word_budget("beat", 4, 2.0, 20, length="medium")
        assert timed == 4 * 26

    def test_one_panel_medium_stays_under_ten_seconds(self):
        """Orta: tek görsel ~26 kelime; 6 dk hedefi 90 kelimelik paragraf üretmez."""
        from core.beat_engine import _word_budget, panel_word_cap, LENGTH_MINUTES
        assert LENGTH_MINUTES["medium"] == 6.0
        one = _word_budget("beat", 1, 6.0, 10, length="medium")
        auto = _word_budget("beat", 1, None, 10, length="medium")
        assert one == auto
        assert one <= panel_word_cap("medium", "beat")
        assert one <= 26
        four = _word_budget("beat", 4, 6.0, 10, length="medium")
        assert four > one
        assert four == 4 * panel_word_cap("medium", "beat")
        eight = _word_budget("beat", 8, 6.0, 10, length="medium")
        assert eight == 8 * panel_word_cap("medium", "beat")


class TestDurationAndLanguageLock:
    def test_resolve_target_minutes_presets(self):
        assert resolve_target_minutes("short", None) is None
        assert resolve_target_minutes("medium", None) == 6.0
        assert resolve_target_minutes("long", None) == 9.0
        assert resolve_target_minutes("medium", 0) == 6.0
        assert resolve_target_minutes("medium", 3.5) == 3.5
        assert resolve_target_minutes("short", 2.0) == 2.0
        assert resolve_target_minutes("unknown", None) == 6.0

    def test_auto_minutes_follows_image_count_not_six(self):
        auto14 = resolve_target_minutes("medium", None, n_images=14, language="en")
        auto54 = resolve_target_minutes("medium", None, n_images=54, language="en")
        assert auto14 is not None
        assert 1.0 <= auto14 <= 2.5
        assert auto54 is not None
        assert auto14 < auto54
        assert auto54 <= 6.0
        assert estimate_auto_minutes(14, "medium", "en") == auto14
        assert resolve_target_minutes("short", None, n_images=14) is None
        assert resolve_target_minutes("medium", 4.0, n_images=14) == 4.0

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

    def test_duration_note_medium_caps_per_image(self):
        from core.beat_engine import StoryBeat
        chunk = [
            StoryBeat(0, [0], "setup", word_budget=26),
            StoryBeat(1, [1, 2], "beat", word_budget=52),
        ]
        note = _duration_note("medium", 0.0, "en", chunk, 10)
        assert "STORY" in note
        assert "Third person" in note
        assert "30-second" in note
        short = _duration_note("short", 0.0, "en", chunk, 10)
        assert "1–2 sentences" in short or "1-2 sentences" in short or "story beat" in short

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
        assert len(spoken[0].text.split()) <= 28
        assert all(len(s.text.split()) <= 28 for s in spoken)

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
            {}, "en", 0, use_hook=False, length="short",
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken = [s for s in story if (s.text or "").strip()]
        by_index = {s.image_index: (s.text or "") for s in story}
        assert {s.image_index for s in story} == {0, 1, 2}
        assert by_index[0].startswith("He opens")
        assert "rank board" in by_index[1].lower()
        assert "steel cracks" in by_index[2].lower()
        assert not any("hikaye" in (s.text or "").lower() for s in story)
        assert len(spoken) == 3

    def test_medium_spreads_long_vo_across_panels(self):
        from core.beat_engine import StoryBeat
        from core.script_generator import ScriptGenerator as SG
        ch = _chapter(3, {str(i): {"scene": "x", "action": "y"} for i in range(3)})
        gen = SG.__new__(SG)
        long_vo = (
            "Kurose gets stuck with cleaning duty again while classmates dump the work on him. "
            "Everyone is whispering about a senior who terrifies the whole school. "
            "For him that name is a different kind of danger in the hallway hierarchy."
        )
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0, 1, 2], "setup", word_budget=56)],
            {0: long_vo}, {}, "en", 0, use_hook=False, length="medium",
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken = [s for s in story if (s.text or "").strip()]
        assert len(spoken) >= 2
        assert all(len(s.text.split()) <= 26 for s in spoken)
        assert all(s.duration <= 12.0 for s in spoken)
        for s in spoken:
            expected = SG.estimate_duration(s.text, "en")
            assert abs((s.duration or 0) - expected) < 0.05

    def test_medium_one_image_clips_thirty_second_paragraph(self):
        from core.beat_engine import StoryBeat
        from core.script_generator import ScriptGenerator as SG
        ch = _chapter(1, {"0": {"scene": "x", "action": "y"}})
        gen = SG.__new__(SG)
        paragraph = " ".join(
            "Kurose is tired of cleaning duty and the feared senior and the hallway rumors".split()
            * 12
        )
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0], "setup", word_budget=26)],
            {0: paragraph}, {}, "en", 0, use_hook=False, length="medium",
        )
        spoken = [s for s in segs if (s.text or "").strip()]
        assert spoken
        assert len(spoken[0].text.split()) <= 26
        assert spoken[0].duration <= 12.0
        expected = SG.estimate_duration(spoken[0].text, "en")
        assert abs(spoken[0].duration - expected) < 0.05

    def test_fourteen_images_all_story_panels_get_text(self):
        """14 görsel 10 beat'e sıkışsa bile hikaye kareleri boş kalmaz."""
        from core.beat_engine import StoryBeat
        analyses = {
            str(i): {"scene": "Hall", "action": f"Beat move {i} changes the stakes now."}
            for i in range(14)
        }
        ch = _chapter(14, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        beats = []
        vo = {}
        idx = 0
        for bid in range(10):
            take = 2 if bid < 4 else 1
            panels = list(range(idx, idx + take))
            idx += take
            beats.append(StoryBeat(bid, panels, "setup" if bid == 0 else "beat", word_budget=40))
            vo[bid] = (
                "Kurose gets stuck with cleaning duty again while classmates dump the work. "
                "The feared senior is the only name that still makes the hallway go quiet."
            )
        segs = gen._materialize_segments(
            ch, beats, vo, {}, "en", 0, use_hook=False, length="medium",
        )
        story = [s for s in segs if s.role not in ("cold_open", "last_time")]
        assert {s.image_index for s in story} == set(range(14))
        spoken = [s for s in story if (s.text or "").strip()]
        assert spoken
        assert all(len(s.text.split()) <= 26 for s in spoken)
        blob = " ".join(s.text for s in spoken).lower()
        assert "changes the stakes" not in blob

    def test_beat_role_panels_get_text_not_only_rehook(self):
        """54 panel / 10 beat: rehook dolu, beat boş kalmamalı."""
        from core.beat_engine import StoryBeat
        n = 16
        analyses = {
            str(i): {"scene": "Office", "action": f"Sunho answers Ms. Haeseon in panel {i} now."}
            for i in range(n)
        }
        ch = _chapter(n, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        beats = [
            StoryBeat(0, list(range(0, 4)), "setup", word_budget=104),
            StoryBeat(1, list(range(4, 8)), "beat", word_budget=104),
            StoryBeat(2, list(range(8, 12)), "rehook", word_budget=104),
            StoryBeat(3, list(range(12, 16)), "beat", word_budget=104),
        ]
        vo = {
            0: "Sunho's heart pounds after Ms. Haeseon calls him mean. The friendship is shifting.",
            2: "Then she demands his complete attention and forget Baek Yuyeon.",
        }
        segs = gen._materialize_segments(
            ch, beats, vo, {}, "en", 0, use_hook=False, length="medium",
        )
        story = [s for s in segs if s.role not in ("cold_open", "last_time")]
        by_role = {}
        for s in story:
            by_role.setdefault(s.role, []).append(s)
        assert by_role["rehook"]
        assert by_role["beat"]
        spoken_beats = [s for s in by_role["beat"] if (s.text or "").strip()]
        spoken_rehook = [s for s in by_role["rehook"] if (s.text or "").strip()]
        assert spoken_beats or spoken_rehook
        blob = " ".join(s.text or "" for s in story).lower()
        assert "answers ms. haeseon in panel" in blob

    def test_clip_keeps_complete_sentences_under_budget(self):
        from core.script_generator import _clip_to_budget
        text = (
            "Sunho's heart pounds as his face turns bright red. "
            "This isn't just embarrassment anymore."
        )
        clipped = _clip_to_budget(text, 8)
        assert clipped.endswith(".")
        assert "pounds" in clipped
        assert "embarrassment" not in clipped
        assert clipped == "Sunho's heart pounds as his face turns bright red."

    def test_short_spreads_sentences_across_panels(self):
        from core.beat_engine import StoryBeat
        vo = "He opens the gate. She follows him inside."
        ch = _chapter(2, {str(i): {"action": "He opens the gate."} for i in range(2)})
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, [0, 1], "setup", word_budget=20)],
            {0: vo}, {}, "en", 0, use_hook=False, length="short",
        )
        story = [s for s in segs if (s.role or "") != "cold_open"]
        spoken = [s.text.strip() for s in story if (s.text or "").strip()]
        assert len(spoken) == 2
        assert spoken[0].startswith("He opens")
        assert "follows" in spoken[1]

    def test_split_sentences_keeps_ms_title(self):
        parts = _split_sentences("Ms. Haeseon cuts him off sharply. He stares.")
        assert parts == [
            "Ms. Haeseon cuts him off sharply.",
            "He stares.",
        ]

    def test_medium_does_not_split_into_one_word_crumbs(self):
        from core.beat_engine import StoryBeat
        from core.script_generator import _pace_distribute
        vo = (
            "Sunho's heart pounds as his face turns bright red after Ms. Haeseon playfully calls him mean. "
            "This isn't just embarrassment. It's the moment their casual friendship starts becoming something more intense. "
            "But his hiccups are getting worse instead of better."
        )
        parts = _pace_distribute(vo, 8, 26)
        spoken = [p.strip() for p in parts if p.strip()]
        assert spoken
        assert all(len(p.split()) >= 4 for p in spoken)
        assert not any(p.rstrip(".") in ("Ms", "as", "to", "his", "red") for p in spoken)
        assert "Ms. Haeseon" in " ".join(spoken)
        ch = _chapter(8, {str(i): {"scene": "x", "action": "She presses a pressure point now."} for i in range(8)})
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch, [StoryBeat(0, list(range(8)), "setup", word_budget=80)],
            {0: vo}, {}, "en", 0, use_hook=False, length="medium",
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken_segs = [s for s in story if (s.text or "").strip()]
        assert spoken_segs
        assert all(len(s.text.split()) >= 4 for s in spoken_segs)
        assert all(len(s.text.split()) <= 26 for s in spoken_segs)
        assert not any((s.text or "").strip() in ("Ms.", "as.", "to.") for s in spoken_segs)

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

    def test_english_medium_does_not_copy_turkish_analysis(self):
        from core.beat_engine import StoryBeat
        dump = "Bu sahne hikayenin aksiyon seviyesini yükseltir ve karakter konuşuyor."
        analyses = {
            str(i): {"scene": dump, "action": dump}
            for i in range(8)
        }
        ch = _chapter(8, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch,
            [
                StoryBeat(0, [0, 1, 2, 3], "setup", word_budget=80),
                StoryBeat(1, [4, 5, 6, 7], "beat", word_budget=80),
            ],
            {0: "Sunho's heart pounds after Ms. Haeseon calls him mean."},
            {},
            "en",
            0,
            use_hook=False,
            length="medium",
        )
        from core.script_generator import _drop_wrong_language_segments
        _drop_wrong_language_segments(segs, "en", chapter=ch, length="medium")
        blob = " ".join(s.text or "" for s in segs)
        assert "hikaye" not in blob.lower()
        assert "aksiyon" not in blob.lower()
        assert "konuşuyor" not in blob.lower()
        assert "yükseltir" not in blob.lower()
        spoken = [s for s in segs if (s.text or "").strip()]
        assert spoken
        assert any("Sunho" in (s.text or "") for s in spoken)
        blob = " ".join(s.text or "" for s in segs)
        assert "pressure shifts" not in blob.lower()
        assert "this beat" not in blob.lower()

    def test_wrong_language_analysis_stays_silent_instead_of_invented_vo(self):
        from core.beat_engine import StoryBeat
        from core.script_generator import SILENT_HOLD_SEC, _fill_empty_story_panels
        dump = "Bu sahne hikayenin aksiyon seviyesini yükseltir."
        analyses = {
            str(i): {
                "scene": dump,
                "action": dump,
                "characters": [{"name": "Sunho"}, {"name": "Haeseon"}],
            }
            for i in range(6)
        }
        ch = _chapter(6, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        segs = gen._materialize_segments(
            ch,
            [StoryBeat(0, list(range(6)), "setup", word_budget=80)],
            {0: "Sunho's heart pounds after Ms. Haeseon calls him mean. He still cannot look away from her."},
            {},
            "en",
            0,
            use_hook=False,
            length="medium",
        )
        segs = _fill_empty_story_panels(segs, ch, "en", "medium")
        story = [s for s in segs if (s.role or "") not in ("cold_open", "last_time")]
        assert len(story) == 6
        spoken = [s for s in story if (s.text or "").strip()]
        silent = [s for s in story if not (s.text or "").strip()]
        assert spoken
        assert silent
        blob = " ".join(s.text or "" for s in story)
        assert "hikaye" not in blob.lower()
        assert "pressure shifts" not in blob.lower()
        assert "this beat" not in blob.lower()
        assert "still in it" not in blob.lower()
        assert "doesn't look away" not in blob.lower()
        from core.script_generator import ScriptGenerator as SG
        for s in spoken:
            expected = SG.estimate_duration(s.text, "en")
            assert abs((s.duration or 0) - expected) < 0.05
            assert (s.duration or 0) < 12.0
        for s in silent:
            assert abs((s.duration or 0) - SILENT_HOLD_SEC) < 0.05

    def test_duration_is_tts_words_not_padded_floor(self):
        from core.script_generator import ScriptGenerator as SG
        six = SG.estimate_duration("The pressure shifts on this beat.", "en")
        assert 0.4 <= six <= 3.0
        spoken = (
            "Sunho keeps the same look, as if the last word is still hanging in the air."
        )
        longish = SG.estimate_duration(spoken, "en")
        assert longish > six
        assert abs(longish - (len(spoken.split()) / 160.0) * 60) < 0.05

    def test_medium_54_panels_stay_silent_without_invented_fill(self):
        from core.beat_engine import StoryBeat
        n = 54
        analyses = {
            str(i): {
                "scene": "Bu sahne hikayenin aksiyon seviyesini yükseltir.",
                "action": "Bu sahne hikayenin aksiyon seviyesini yükseltir.",
                "characters": [{"name": "Sunho"}, {"name": "Haeseon"}],
            }
            for i in range(n)
        }
        ch = _chapter(n, analyses)
        gen = ScriptGenerator.__new__(ScriptGenerator)
        beats = []
        vo = {}
        idx = 0
        sizes = [8, 8, 4, 5, 7, 4, 4, 4, 4, 6]
        assert sum(sizes) == n
        for bid, take in enumerate(sizes):
            panels = list(range(idx, idx + take))
            idx += take
            role = "setup" if bid == 0 else ("cliffhanger" if bid == 9 else "beat")
            beats.append(StoryBeat(bid, panels, role, word_budget=take * 26))
            vo[bid] = (
                "Sunho's heart pounds after Ms. Haeseon calls him mean. "
                "He still cannot look away from her in the crowded hallway."
            )
        segs = gen._materialize_segments(
            ch, beats, vo, {}, "en", 0, use_hook=False, length="medium",
        )
        story = [s for s in segs if (s.role or "") not in ("cold_open", "last_time")]
        assert {s.image_index for s in story} == set(range(n))
        blob = " ".join((s.text or "") for s in story).lower()
        assert "pressure shifts" not in blob
        assert "this beat" not in blob
        assert "hikaye" not in blob
        empty = [s for s in story if not (s.text or "").strip()]
        spoken = [s for s in story if (s.text or "").strip()]
        assert spoken
        assert empty
        blob = " ".join((s.text or "") for s in spoken).lower()
        assert "still in it" not in blob
        assert "doesn't look away" not in blob
        from core.script_generator import SILENT_HOLD_SEC
        for s in spoken:
            expected = ScriptGenerator.estimate_duration(s.text, "en")
            assert abs((s.duration or 0) - expected) < 0.05
            assert (s.duration or 0) != 3.8
            words = len((s.text or "").split())
            if words <= 8:
                assert (s.duration or 0) < 3.2
        for s in empty:
            assert abs((s.duration or 0) - SILENT_HOLD_SEC) < 0.05

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
            length="short",
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken = [(s.text or "").strip() for s in story if (s.text or "").strip()]
        assert len(spoken) == 1
        assert spoken[0].startswith("He opens")
        assert spoken.count("He opens the gate.") == 1
        assert not any("Gergin" in t or "heybetli" in t for t in spoken)
        assert {s.image_index for s in story} == set(range(8))

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
            {}, "en", 0, use_hook=False, length="short",
        )
        story = [s for s in segs if s.role != "cold_open"]
        spoken = [s for s in story if (s.text or "").strip()]
        by_index = {s.image_index: (s.text or "") for s in story}
        blob = " ".join(s.text or "" for s in spoken)
        assert by_index[0].startswith("He opens")
        assert not by_index[1].strip()
        assert "force hits" in by_index[2].lower()
        assert len(spoken) == 2
        assert {s.image_index for s in story} == {0, 1, 2}
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

    def test_retry_does_not_wipe_filled_beats(self):
        from core.script_generator import _merge_vo
        first = {0: "He opens the gate and the guards freeze.", 1: "She blocks the blow."}
        retry = {0: "", 1: "She blocks the blow before it lands on him."}
        merged = _merge_vo(first, retry, "en")
        assert "opens the gate" in merged[0]
        assert "before it lands" in merged[1]

    def test_retry_does_not_replace_english_with_turkish(self):
        from core.script_generator import _merge_vo
        first = {0: "He opens the gate and the guards freeze."}
        retry = {0: "Kapıyı açar ve hikaye aksiyon seviyesini yükseltir burada."}
        merged = _merge_vo(first, retry, "en")
        assert "opens the gate" in merged[0]
        assert "hikaye" not in merged[0].lower()

    def test_voiceover_accepts_payload_and_voiceover_keys(self):
        raw = '{"beats":[{"id":0,"voiceover":"He opens the gate."},{"id":1,"payload":"She blocks the blow."}]}'
        parsed = _parse_beats_voiceover(raw)
        assert "opens the gate" in parsed[0]
        assert "blocks the blow" in parsed[1]

    def test_spoken_comma_clause_is_not_mood_dump(self):
        from core.script_generator import _is_atmosphere_dump
        assert not _is_atmosphere_dump("He opens the gate, then he freezes.")
        assert _is_atmosphere_dump("Gergin, heybetli, tehditkar")


class TestLinter:
    def test_flags_protagonist(self):
        issues = lint_text("The protagonist walks in.", role="beat", is_first=True)
        assert any("etiket" in i.lower() or "jenerik" in i.lower() for i in issues)

    def test_flags_meta_filler(self):
        issues = lint_text("The pressure shifts on this beat.", role="beat")
        assert any("filler" in i.lower() for i in issues)

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

    def test_flags_one_word_crumb(self):
        issues = lint_text("Ms.", role="beat")
        assert any("kırıntı" in i.lower() for i in issues)

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
        spoken = [s for s in story if (s.text or "").strip()]
        assert len(spoken) >= 2
        assert story[0].image_index == 0
        assert {s.image_index for s in story} == {0, 1, 2}

    def test_segment_roundtrip(self):
        s = SegmentData(3, "Merhaba", beat_id=1, role="rehook", lint_issues=["x"])
        d = s.to_dict()
        s2 = SegmentData.from_dict(d)
        assert s2.beat_id == 1
        assert s2.role == "rehook"
        assert s2.lint_issues == ["x"]

    def test_silent_hold_moves_to_next_spoken(self):
        from core.models import SegmentData
        from core.script_generator import absorb_silent_holds
        segs = [
            SegmentData(0, "He opens the gate.", duration=2.0),
            SegmentData(1, "", duration=0.75),
            SegmentData(2, "She follows.", duration=1.5),
        ]
        absorb_silent_holds(segs)
        assert segs[1].duration == 0
        assert segs[2].duration == 1.5

    def test_reading_roundtrip_keeps_spoken_order(self):
        from core.models import SegmentData
        from core.script_generator import distribute_reading, reading_text
        segs = [
            SegmentData(0, "He opens the gate."),
            SegmentData(1, ""),
            SegmentData(2, "She follows him inside."),
        ]
        assert reading_text(segs) == "He opens the gate.\n\nShe follows him inside."
        distribute_reading(segs, "He shuts the gate. She waits outside. The hall stays quiet.", "en")
        assert segs[0].text.startswith("He shuts")
        assert "waits" in segs[1].text
        assert "quiet" in segs[2].text
        assert all(s.text.endswith((".", "!", "?")) for s in segs)

    def test_flow_keeps_original_when_one_beat_is_missing(self):
        from core.beat_engine import StoryBeat
        gen = ScriptGenerator.__new__(ScriptGenerator)
        gen._pick_model = lambda model, premium=False: model
        gen._chat = lambda *a, **k: (
            '{"beats":[{"id":0,"text":"Jin-Woo opens the gate and the alarms start."}]}'
        )
        beats = [
            StoryBeat(0, [0], "setup"),
            StoryBeat(1, [1], "beat"),
        ]
        original = {
            0: "Jin-Woo opens the gate and the system wakes.",
            1: "The rankers freeze when the name appears.",
        }
        out = gen._flow_narrative(beats, original, "m", "en", None)
        assert "system wakes" in out[0]
        assert "rankers" in out[1]

    def test_single_cast_name_replaces_bare_pronoun(self):
        from core.models import SegmentData
        from core.script_linter import restore_hidden_names
        segs = [SegmentData(0, "He opens the gate.", role="beat")]
        restore_hidden_names(segs, ["Jin-Woo"])
        assert segs[0].text.startswith("Jin-Woo")

    def test_commentary_drops_and_name_is_not_shouted(self):
        from core.models import SegmentData
        from core.script_generator import _spoken_story_pass
        segs = [
            SegmentData(0, "MS. HAESEON has Sunbae pinned to the floor, leaving him flustered.", role="beat"),
            SegmentData(1, "This scene highlights the romantic tension of the story.", role="beat"),
            SegmentData(2, "Sunbae's presence indicates his involvement.", role="beat"),
            SegmentData(3, "It demonstrates Sunbae's determination and focus.", role="beat"),
            SegmentData(4, "MS. HAESEON asks if he is okay, creating tension between them.", role="beat"),
        ]
        _spoken_story_pass(segs, None, "en")
        blob = " ".join(s.text for s in segs)
        assert "MS." not in blob
        for banned in ("highlights", "this scene", "indicates", "demonstrates", "creating tension"):
            assert banned not in blob.lower()
        assert segs[0].text.startswith("Ms. Haeseon")
        assert segs[1].text == ""
        assert segs[2].text == ""
        assert segs[3].text == ""
        assert segs[4].text.lower().startswith("she")

    def test_cut_quote_gets_its_mark_back(self):
        from core.script_generator import _strip_commentary
        text = "She tells him he is a 'Teto Guy,' surprising him with her observation about his personality."
        cleaned = _strip_commentary(text)
        assert cleaned.endswith("Guy'.")
        assert "observation" not in cleaned

    def test_dangling_tail_and_quote_are_repaired(self):
        from core.models import SegmentData
        from core.script_generator import _spoken_story_pass
        segs = [
            SegmentData(0, "She tells Sunbae he is a 'Teto Guy.", role="beat"),
            SegmentData(1, "She confronts Sunbae, asking if his actions are 'because of Baek Yuyeon.", role="beat"),
            SegmentData(2, "She grabs his arm, as he looks on with surprise.", role="beat"),
        ]
        _spoken_story_pass(segs, None, "en")
        assert segs[0].text.endswith("Guy'.")
        assert segs[1].text.endswith("Yuyeon'.")
        assert "as he looks" not in segs[2].text

    def test_compiled_segments_keep_source_chapter(self):
        from core.models import Chapter, ImageData, SegmentData
        from core.script_generator import ScriptGenerator

        def chapter(cid, name):
            return Chapter(
                id=cid,
                name=name,
                images=[ImageData(path=f"{name}.png", filename=f"{name}.png", order=0)],
                analysis_data={"0": {"action": "He opens the gate.", "scene": "Gate"}},
                segments=[SegmentData(0, "old", role="beat")],
            )

        a, b = chapter("a1", "A"), chapter("b1", "B")
        gen = ScriptGenerator.__new__(ScriptGenerator)

        def fake_generate(chapter, **kwargs):
            return [SegmentData(
                0, f"{chapter.name} opens the gate.", duration=2.0, role="beat",
                image_path=chapter.images[0].path,
            )]

        gen.generate_script = fake_generate
        out = gen._generate_compiled([a, b], "m", "fresh", "short", "en", "auto")
        assert [s.source_chapter_id for s in out] == ["a1", "b1"]
        assert [s.source_image_index for s in out] == [0, 0]
        assert a.segments[0].text == "old"
        assert b.segments[0].text == "old"

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
        spoken = [s for s in story if (s.text or "").strip()]
        assert spoken
        assert {s.image_index for s in story} == {0, 1}
        assert all(s.image_path for s in segs)
