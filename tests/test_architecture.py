"""Yeni mimari yardımcıları: secrets, maliyet, lint döngüsü, seri adımı, preset."""

from core.models import Chapter, ImageData, Project, SegmentData


def test_secrets_not_written_to_settings(tmp_path, monkeypatch):
    from core import secrets as sec
    from core import settings_manager as sm_mod

    secrets_path = tmp_path / "secrets.json"
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(sec, "SECRETS_PATH", secrets_path)
    monkeypatch.setattr(sm_mod, "SETTINGS_PATH", settings_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sec, "load_dotenv", lambda: None)
    previous = sm_mod.SettingsManager._instance
    sm_mod.SettingsManager._instance = None
    try:
        mgr = sm_mod.SettingsManager()
        mgr.set_api_key("sk-or-test-key-1234567890")
        disk = settings_path.read_text(encoding="utf-8")
        assert "sk-or-test-key" not in disk
        assert mgr.get_api_key() == "sk-or-test-key-1234567890"
        assert secrets_path.exists()
        assert "sk-or-test-key" in secrets_path.read_text(encoding="utf-8")
    finally:
        sm_mod.SettingsManager._instance = previous


def test_settings_save_is_atomic(tmp_path, monkeypatch):
    from core import settings_manager as sm_mod

    path = tmp_path / "settings.json"
    monkeypatch.setattr(sm_mod, "SETTINGS_PATH", path)
    previous = sm_mod.SettingsManager._instance
    sm_mod.SettingsManager._instance = None
    try:
        mgr = sm_mod.SettingsManager()
        mgr.set("app.theme", "light")
        assert path.exists()
        assert not path.with_suffix(".json.tmp").exists()
        assert '"theme": "light"' in path.read_text(encoding="utf-8")
    finally:
        sm_mod.SettingsManager._instance = previous


def test_active_chapter_skips_complete_first():
    from core.pipeline import active_chapter, next_incomplete_step

    done = Chapter(
        id="1",
        name="Bölüm 1",
        images=[ImageData("a.jpg", "a.jpg", 0)],
        analysis_data={"0": {"scene": "gate", "action": "opens"}},
        segments=[SegmentData(0, "He opens the gate.", audio_path=__file__, duration=1.0)],
    )
    nxt = Chapter(id="2", name="Bölüm 2", images=[])
    project = Project(id="p", name="Serie", created_at="", updated_at="", chapters=[done, nxt])
    ch = active_chapter(project)
    assert ch.id == "2"
    page, label = next_incomplete_step(project)
    assert page == "images"
    assert "Görsel" in label


def test_builtin_render_presets():
    from core.render_presets import builtin_preset

    yt = builtin_preset("youtube")
    shorts = builtin_preset("shorts")
    cinema = builtin_preset("4k")
    assert yt["resolution"] == [1920, 1080]
    assert shorts["resolution"] == [1080, 1920]
    assert shorts.get("max_duration") == 60.0
    assert cinema["resolution"] == [3840, 2160]


def test_costing_uses_usage():
    from core.costing import usd_from_usage, estimate_script_usd

    usd = usd_from_usage("google/gemini-2.5-flash", {"prompt_tokens": 1_000_000, "completion_tokens": 0})
    assert usd == 0.15
    est = estimate_script_usd("google/gemini-2.5-flash", n_beats=8)
    assert est["usd_high"] >= est["usd_low"] > 0


def test_ab_hook_not_embedded_in_vo():
    from core.script_quality import apply_selected_hook, cold_open_text, store_hook_variants

    segs = [
        SegmentData(0, "A hook line.", role="cold_open"),
        SegmentData(1, "Story continues.", role="beat"),
    ]
    chapter = Chapter(id="c", name="n", segments=list(segs))
    store_hook_variants(chapter, [
        {"id": "A", "text": "A hook line."},
        {"id": "B", "text": "B hook line."},
    ], selected="A")
    assert "[B kanca]" not in cold_open_text(chapter.segments)
    apply_selected_hook(chapter.segments, "B hook line.")
    assert cold_open_text(chapter.segments) == "B hook line."
    assert chapter.script_meta["selected_hook"] == "A"


def test_polish_rewrites_error_segments():
    from core.script_quality import polish_segments

    class FakeGen:
        def regenerate_segment(self, chapter, idx, model, **kwargs):
            chapter.segments[idx].text = "Jin-Woo walks through the gate."
            return chapter.segments[idx]

    segs = [
        SegmentData(0, "The protagonist walks through the gate.", role="beat"),
        SegmentData(1, "The rankers freeze.", role="beat"),
    ]
    chapter = Chapter(id="c", name="n", segments=segs)
    out = polish_segments(FakeGen(), chapter, segs, model="x", language="en")
    assert "protagonist" not in out[0].text.lower()


def test_last_time_prefers_cliffhanger():
    from core.character_bible import last_time_source

    prev = Chapter(
        id="1",
        name="Ch 1",
        segments=[
            SegmentData(0, "Setup talk.", role="setup"),
            SegmentData(1, "The system awakens.", role="cliffhanger"),
        ],
    )
    cur = Chapter(id="2", name="Ch 2")
    project = Project(id="p", name="s", created_at="", updated_at="", chapters=[prev, cur])
    src = last_time_source(project, cur)
    assert "system awakens" in src
    assert "Setup talk" not in src


def test_analyzer_economy_model_skips_middle_frames():
    from core.ai_analyzer import AIAnalyzer
    from core.settings_manager import SettingsManager

    class DummySettings:
        def get(self, key, default=None):
            data = {
                "analysis.skip_low_score_fillers": True,
                "api.economy_vision_model": "google/gemini-2.5-flash-lite",
            }
            return data.get(key, default)

        def has_api_key(self):
            return True

        def get_api_key(self):
            return "x"

        def reload(self):
            return {}

    an = AIAnalyzer.__new__(AIAnalyzer)
    an._settings = DummySettings()
    assert an._model_for_index(0, 12, "google/gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert an._model_for_index(1, 12, "google/gemini-2.5-flash") == "google/gemini-2.5-flash-lite"
    assert an._model_for_index(3, 12, "google/gemini-2.5-flash") == "google/gemini-2.5-flash"


def test_video_filters_reexport():
    from core.video_composer import RANDOM_MOTIONS, build_clip_filter, compute_fitted_size
    from core.video_filters import build_clip_filter as bf

    assert "zoom_in" in RANDOM_MOTIONS
    fw, fh = compute_fitted_size(800, 1600, 1920, 1080)
    assert fh == 1080
    graph = bf(1920, 1080, 30, 2.0, fw, fh)
    assert "[vout]" in graph
