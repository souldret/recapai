from core.tts_engine import normalize_caps


def test_normalize_caps_apostrophe_words():
    assert normalize_caps("I Could'VE Used My Foam Roller") == "I Could've Used My Foam Roller"
    assert normalize_caps("Promise Me You'LL Pretend Baek Yuyeon Doesn'T Exist") == \
        "Promise Me You'll Pretend Baek Yuyeon Doesn't Exist"
    assert normalize_caps("Well, I'M Obviously AN Egen Guy") == "Well, I'm Obviously An Egen Guy"


def test_normalize_caps_still_handles_plain_words():
    assert normalize_caps("IF WE Were AT MY Place") == "If We Were At My Place"


def test_normalize_caps_keeps_acronyms_and_i():
    assert normalize_caps("I went to the US with AI") == "I went to the US with AI"
