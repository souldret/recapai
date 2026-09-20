"""PipelineWorker: sahte LLM/TTS ile uçtan uca orkestrasyon."""

import pytest

from core.models import Chapter, ImageData, Project, SegmentData


class FakeClient:
    def update_api_key(self, key):
        self.key = key


class FakeAnalyzer:
    def __init__(self, client=None):
        self.client = client

    def analyze_chapter(self, chapter, model, progress_callback=None, stop_flag=None, project=None):
        for i, img in enumerate(chapter.images):
            chapter.analysis_data[str(i)] = {
                "scene": f"Scene {i}",
                "action": "Jin-Woo opens the gate.",
                "characters": [{"name": "Jin-Woo"}],
                "important": i == 0,
            }
            if progress_callback:
                progress_callback(i + 1, len(chapter.images), img.filename)
        return chapter.analysis_data


class FakeGenerator:
    def __init__(self, client=None):
        self.client = client

    def generate_script(self, chapter, model, **kwargs):
        segs = [
            SegmentData(0, "The gate opens on Jin-Woo.", role="cold_open", duration=2.0),
            SegmentData(0, "He steps through anyway.", role="beat", duration=2.0),
        ]
        if kwargs.get("assign", True):
            chapter.segments = segs
        return segs


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    import sys
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_pipeline_worker_runs_with_fakes(tmp_path, monkeypatch, qapp):
    from ui.workers import render_worker as rw

    project = Project(id="p", name="Serie", created_at="", updated_at="")
    chapter = Chapter(
        id="c1",
        name="Ch 1",
        images=[ImageData(str(tmp_path / "a.jpg"), "a.jpg", 0)],
    )
    project.chapters = [chapter]
    (tmp_path / "a.jpg").write_bytes(b"x")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    out = str(tmp_path / "out.mp4")

    class FakeComposer:
        def __init__(self, settings):
            self.settings = settings

        def compose_chapter(self, ch, output_path, progress_callback=None, cancel_check=None):
            PathOut = __import__("pathlib").Path
            PathOut(output_path).write_bytes(b"mp4")
            if progress_callback:
                progress_callback(100, "0s")
            return output_path

    class DummySM:
        def reload(self):
            return {}

        def has_api_key(self):
            return True

        def get_api_key(self):
            return "sk-test"

        def get(self, key, default=None):
            return default

    monkeypatch.setattr("core.openrouter_client.OpenRouterClient.instance", lambda: FakeClient())
    monkeypatch.setattr("core.ai_analyzer.AIAnalyzer", FakeAnalyzer)
    monkeypatch.setattr("core.script_generator.ScriptGenerator", FakeGenerator)
    monkeypatch.setattr("core.settings_manager.SettingsManager.instance", lambda: DummySM())
    monkeypatch.setattr("core.video_composer.VideoComposer", FakeComposer)
    monkeypatch.setattr("core.ffmpeg_helper.check_ffmpeg", lambda: True)
    monkeypatch.setattr(
        "core.pipeline.require_character_bible",
        lambda project, chapter: 1,
    )

    worker = rw.PipelineWorker(
        project=project,
        chapter=chapter,
        render_settings={"codec": "libx264"},
        render_output=out,
        api_key="sk-test",
        vision_model="google/gemini-2.5-flash",
        script_model="google/gemini-2.5-flash",
        script_style="fresh",
        script_length="short",
        script_language="en",
        tts_engine="edge-tts",
        tts_voice="en-US-AndrewNeural",
        audio_dir=str(audio_dir),
        skip_tts=True,
    )
    errors = []
    finished = []
    worker.error.connect(errors.append)
    worker.finished.connect(finished.append)
    worker.run()
    assert not errors, errors
    assert finished and finished[0] == out
    assert chapter.analysis_data
    assert chapter.segments
