"""Segment bazlı TTS motor karışımı."""

from types import SimpleNamespace

from ui.workers.tts_worker import engine_for_segment, voice_for_engine


def test_mix_routes_narrator_and_dialogue():
    narr = SimpleNamespace(role="cold_open")
    dlg = SimpleNamespace(role="dialogue")
    assert engine_for_segment(narr, mix=True, default_engine="edge-tts") == "kokoro"
    assert engine_for_segment(dlg, mix=True, default_engine="edge-tts") == "edge-tts"
    assert engine_for_segment(dlg, mix=False, default_engine="edge-tts") == "edge-tts"


def test_voice_for_engine_uses_named_voices():
    voice = voice_for_engine(
        "kokoro",
        mix=True,
        default_engine="edge-tts",
        default_voice="tr-TR-AhmetNeural",
        narrator_voice="am_adam",
        dialogue_voice="en-US-AndrewNeural",
    )
    assert voice == "am_adam"


def test_kokoro_fable_maps_to_fenrir():
    from core.tts_engine import KokoroTTSEngine
    assert KokoroTTSEngine._resolve_voice("am_fable") == "am_fenrir"
    assert KokoroTTSEngine._resolve_voice("am_fenrir") == "am_fenrir"
    ids = {v["id"] for lang in KokoroTTSEngine.VOICE_CATALOG.values() for v in lang}
    assert "am_fable" not in ids
    assert "am_fenrir" in ids
