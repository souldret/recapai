"""Ham diyalog kalıntısı: cümle başı hariç art arda üç büyük harfli kelime."""

from core.script_linter import lint_text
from core.script_quality import _is_error_issue


_RAW = "Ham diyalog / çevrilmemiş alıntı"


def _flagged(text: str) -> bool:
    issues = lint_text(text, role="beat")
    return any(_RAW in issue and _is_error_issue(issue) for issue in issues)


def test_where_d_hiccups_is_raw_dialogue():
    assert _flagged("Where'D Those Random Hiccups Even Come From, Anyway?")


def test_and_she_and_i_is_raw_dialogue():
    assert _flagged("And She And I.")


def test_can_you_focus_on_me_is_raw_dialogue():
    assert _flagged("Can You Focus Completely ON ME?")


def test_apostrophe_words_count_as_title_case():
    assert _flagged("IF WE Were AT MY Place, I Could'VE Used MY Foam Roller TO Loosen You UP.")


def test_normal_narration_is_not_raw_dialogue():
    issues = lint_text("Ms. Haeseon pins Sunbae to the floor and calls him mean.", role="beat")
    assert not any(_RAW in issue for issue in issues)


def test_teto_line_is_raw_speech_not_narration():
    from core.script_generator import _looks_like_raw_quote

    assert _looks_like_raw_quote("If I really am a Teto Girl")
    assert _looks_like_raw_quote("I don't wanna hear it,")
    assert not _looks_like_raw_quote(
        "Ms. Haeseon pins Sunbae to the floor and calls him mean."
    )


_PAST = "Geçmiş zaman anlatı"
_EMOTION = "Duygu etiketi tekrarı"


def test_present_tense_story_is_clean():
    issues = lint_text(
        "Frondier walks in. She explodes the core and he activates his skill.",
        role="beat",
    )
    assert not any(_PAST in issue for issue in issues)


def test_past_tense_story_is_flagged():
    issues = lint_text(
        "Frondier walked in. She has been hiding the skill, and he activated it.",
        role="beat",
    )
    assert any(_PAST in issue for issue in issues)


def test_last_time_may_stay_past_tense():
    issues = lint_text(
        "Frondier walked into the dungeon and activated his skill.",
        role="last_time",
    )
    assert not any(_PAST in issue for issue in issues)


def test_repeated_emotion_tag_is_flagged():
    issues = lint_text(
        "He drops the sword, leaving him flustered. "
        "The crowd laughs, leaving her stunned.",
        role="beat",
    )
    assert any(_EMOTION in issue for issue in issues)


def test_one_emotion_tag_is_not_flagged():
    issues = lint_text(
        "He drops the sword, leaving him flustered. Then he activates the skill.",
        role="beat",
    )
    assert not any(_EMOTION in issue for issue in issues)


def test_face_burns_repeat_is_flagged():
    issues = lint_text(
        "His face burns with embarrassment. Her face burns with shame.",
        role="beat",
    )
    assert any(_EMOTION in issue for issue in issues)


def test_leaked_speech_lines_are_raw_quotes():
    from core.script_generator import _looks_like_raw_quote

    samples = [
        "If We Were At My Place, I Could've Used My Foam Roller To Loosen You Up.",
        "If I Really Am A Teto Girl,",
        "Well, I Mean, I'm Obviously An Egen Guy, But.",
        "Can You Focus Completely On Me?",
    ]
    for sample in samples:
        assert _looks_like_raw_quote(sample), sample
