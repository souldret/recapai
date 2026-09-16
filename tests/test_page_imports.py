"""Tüm UI sayfalarını gerçekten yükle — eksik Qt import'ları NameError verir."""

import pytest

from core.app_state import AppState
from core.context import AppContext
from core.openrouter_client import OpenRouterClient
from core.settings_manager import SettingsManager

PAGES = [
    ("ui.pages.home_page", "HomePage"),
    ("ui.pages.project_page", "ProjectPage"),
    ("ui.pages.images_page", "ImagesPage"),
    ("ui.pages.analysis_page", "AnalysisPage"),
    ("ui.pages.script_page", "ScriptPage"),
    ("ui.pages.tts_page", "TtsPage"),
    ("ui.pages.settings_page", "SettingsPage"),
]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    import sys
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture(scope="module")
def ctx():
    sm = SettingsManager()
    return AppContext(
        settings_manager=sm,
        app_state=AppState(),
        open_router_client=OpenRouterClient(settings_manager=sm, api_key="sk-test"),
    )


@pytest.mark.parametrize("module_path,class_name", PAGES)
def test_page_constructs(qapp, ctx, module_path, class_name):
    import importlib
    page_class = getattr(importlib.import_module(module_path), class_name)
    page = page_class(ctx)
    assert page is not None
    page.deleteLater()


def test_script_card_keeps_cursor_while_typing(qapp):
    """Yazarken/tıklayınca imleç satır başına kaçmamalı."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QTextCursor, QUndoStack
    from PyQt6.QtTest import QTest

    from core.models import SegmentData
    from ui.pages.script_page import SegmentCard

    stack = QUndoStack()
    card = SegmentCard(0, SegmentData(image_index=0, text="Hello world"), None, stack)
    edit = card.text_edit
    edit.show()
    edit.setFocus()
    cursor = edit.textCursor()
    cursor.setPosition(5)
    edit.setTextCursor(cursor)
    QTest.keyClick(edit, Qt.Key.Key_X)
    assert edit.textCursor().position() == 6
    assert edit.toPlainText() == "Hellox world"
    QTest.qWait(350)
    qapp.processEvents()
    assert edit.textCursor().position() == 6
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    assert edit.toPlainText() == "Hello world"
    assert edit.textCursor().position() == 5
    end = edit.textCursor()
    end.movePosition(QTextCursor.MoveOperation.End)
    edit.setTextCursor(end)
    QTest.keyClicks(edit, "!")
    assert edit.toPlainText().endswith("!")
    assert edit.textCursor().position() == len(edit.toPlainText())
    card.deleteLater()


def test_render_page_constructs(qapp, ctx, monkeypatch):
    """QMediaPlayer bazı CI ortamlarında yok; yoksa stub."""
    try:
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput  # noqa: F401
        from PyQt6.QtMultimediaWidgets import QVideoWidget  # noqa: F401
    except Exception:
        pytest.skip("QtMultimedia yok")
    import importlib
    page = importlib.import_module("ui.pages.render_page").RenderPage(ctx)
    assert page is not None
    page.deleteLater()
