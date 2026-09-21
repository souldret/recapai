"""P0/P1 regression: 429, altyazı overflow, TTS cache, exporter timeline."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.openrouter_client import OpenRouterClient, OpenRouterError, RETRY_COUNT
from core.subtitle_generator import _seconds_to_ass_ts, _seconds_to_srt_ts
from core.tts_cache import TTSCache


class TestSubtitleTimestamps:
    def test_srt_does_not_overflow_ms(self):
        assert _seconds_to_srt_ts(1.9996) == "00:00:02,000"
        assert _seconds_to_srt_ts(59.9996) == "00:01:00,000"

    def test_ass_does_not_overflow_cs(self):
        assert _seconds_to_ass_ts(1.9996) == "0:00:02.00"
        assert _seconds_to_ass_ts(0.995) == "0:00:01.00"


class TestAudioExportNames:
    def test_chapter_number_is_short(self):
        from core.exporter import audio_export_stem
        assert audio_export_stem("Bölüm 35", 1) == "35_001"
        assert audio_export_stem("Bolum_35", 12) == "35_012"

    def test_nameless_falls_back(self):
        from core.exporter import audio_export_stem
        assert audio_export_stem("Prologue", 3) == "Prologue_003"
        assert audio_export_stem("", 1) == "s_001"


class TestExporterTimeline:
    def test_export_srt_has_no_gap(self, tmp_path):
        from core.exporter import export_srt
        from core.models import Chapter, SegmentData

        chapter = Chapter(
            id="c",
            name="n",
            segments=[
                SegmentData(0, "one", duration=1.0),
                SegmentData(1, "two", duration=1.0),
            ],
        )
        out = tmp_path / "a.srt"
        export_srt(chapter, str(out))
        text = out.read_text(encoding="utf-8")
        assert "00:00:01,000 --> 00:00:02,000" in text
        assert "00:00:01,300" not in text


class TestOpenRouter429:
    def test_last_429_raises_status(self, monkeypatch):
        client = OpenRouterClient.__new__(OpenRouterClient)
        client._settings_manager = None
        client._explicit_key = "sk-test"
        client._session = MagicMock()
        client._total_tokens = 0
        monkeypatch.setattr(client, "_current_base_url", lambda: "https://example.invalid/v1")

        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {}
        client._session.post.return_value = resp

        sleeps = []
        monkeypatch.setattr("core.openrouter_client.time.sleep", lambda s: sleeps.append(s))

        with pytest.raises(OpenRouterError) as exc:
            client._post("chat/completions", {"model": "x"})
        assert exc.value.status_code == 429
        assert "Bilinmeyen hata" not in str(exc.value)
        assert client._session.post.call_count == RETRY_COUNT
        assert len(sleeps) == RETRY_COUNT - 1

    def test_401_on_claude_falls_back_to_gemini(self, monkeypatch):
        client = OpenRouterClient.__new__(OpenRouterClient)
        client._settings_manager = None
        client._explicit_key = "sk-test"
        client._session = MagicMock()
        client._total_tokens = 0
        client._usage_events = []
        monkeypatch.setattr(client, "_current_base_url", lambda: "https://example.invalid/v1")

        denied = MagicMock()
        denied.status_code = 401
        denied.headers = {}
        denied.json.return_value = {"error": {"message": "User not found."}}

        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "model": "google/gemini-2.5-flash",
            "usage": {},
        }

        def post(_url, **kwargs):
            model = (kwargs.get("json") or {}).get("model")
            return denied if "anthropic" in str(model) else ok

        client._session.post.side_effect = post
        data = client._post_with_fallback(
            "chat/completions",
            {"model": "anthropic/claude-sonnet-4", "messages": []},
            ["google/gemini-2.5-flash"],
        )
        assert data["model"] == "google/gemini-2.5-flash"


class TestTTSCache:
    def test_put_get_roundtrip(self, tmp_path):
        cache = TTSCache(tmp_path / "c", max_size_mb=10)
        src = tmp_path / "a.mp3"
        src.write_bytes(b"audio")
        key = cache.get_cache_key("hello", "voice", {"rate": 1})
        cache.put(key, str(src))
        hit = cache.get(key)
        assert hit and Path(hit).exists()
        assert cache.entry_count() == 1


class TestPipelineCheckpoint:
    def test_stale_script_does_not_skip_when_analysis_missing(self):
        from core.models import Chapter, ImageData, SegmentData
        from core.pipeline import (
            STAGE_ANALYSIS,
            STAGE_RENDER,
            STAGE_SCRIPT,
            STAGE_TTS,
            pending_stages,
        )

        chapter = Chapter(
            id="c",
            name="n",
            images=[ImageData("a.jpg", "a.jpg", 0)],
            analysis_data={},
            segments=[
                SegmentData(0, "eski script", audio_path="missing.mp3", duration=1.0),
            ],
        )
        stages = pending_stages(chapter)
        assert stages == [STAGE_ANALYSIS, STAGE_SCRIPT, STAGE_TTS, STAGE_RENDER]

    def test_complete_prefix_skips_analysis_and_script(self):
        from core.models import Chapter, ImageData, SegmentData
        from core.pipeline import STAGE_ANALYSIS, STAGE_SCRIPT, STAGE_TTS, pending_stages

        chapter = Chapter(
            id="c",
            name="n",
            images=[ImageData("a.jpg", "a.jpg", 0)],
            analysis_data={"0": {"scene": "Kapı", "action": "Açar"}},
            segments=[SegmentData(0, "He opens the gate.")],
        )
        stages = pending_stages(chapter)
        assert STAGE_ANALYSIS not in stages
        assert STAGE_SCRIPT not in stages
        assert STAGE_TTS in stages


class TestSettingsMerge:
    def test_load_fills_new_tts_keys(self, tmp_path, monkeypatch):
        from core import settings_manager as sm_mod

        path = tmp_path / "settings.json"
        path.write_text('{"tts": {"default_speed": 1.2}}', encoding="utf-8")
        monkeypatch.setattr(sm_mod, "SETTINGS_PATH", path)
        previous = sm_mod.SettingsManager._instance
        sm_mod.SettingsManager._instance = None
        try:
            mgr = sm_mod.SettingsManager()
            assert mgr.get("tts.default_speed") == 1.2
            assert mgr.get("tts.mix_engines") is False
            assert mgr.get("tts.narrator_voice") == "am_adam"
            assert mgr.get("defaults.vision_model")
        finally:
            sm_mod.SettingsManager._instance = previous
