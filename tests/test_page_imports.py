"""Tüm UI sayfalarını gerçekten yükle — eksik Qt import'ları NameError verir."""

from pathlib import Path

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


def test_load_pixmap_png_is_silent(qapp, tmp_path, capsys):
    """PNG yükleme Qt libpng iCCP uyarısı basmamalı."""
    from PIL import Image

    from core.qt_image import load_pixmap

    src = tmp_path / "panel.png"
    Image.new("RGB", (16, 16), (12, 34, 56)).save(src, "PNG")
    pixmap = load_pixmap(src)
    err = capsys.readouterr().err
    assert not pixmap.isNull()
    assert pixmap.width() == 16
    assert "iCCP" not in err
    assert "sRGB profile" not in err
    assert load_pixmap(tmp_path / "missing.png").isNull()


def test_stderr_filter_drops_libpng_iccp():
    from main import _FilteredStderr

    class _Buf:
        def __init__(self):
            self.chunks = []

        def write(self, text):
            self.chunks.append(text)

        def flush(self):
            pass

    buf = _Buf()
    filt = _FilteredStderr(buf)
    filt.write("qt.gui.imageio: libpng warning: iCCP: known incorrect sRGB profile\n")
    filt.write("Script tamamlandı: 14 segment\n")
    assert "".join(buf.chunks) == "Script tamamlandı: 14 segment\n"


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


def test_images_page_sync_order_updates_count_and_batch_paths(qapp, ctx, tmp_path, monkeypatch):
    """Silme/yeniden sıralama sayaç ve toplu panel listesini güncellemeli."""
    from core.models import Chapter, ImageData, Project
    from ui.pages.images_page import ImagesPage
    from ui.pages.images_widgets import ImageGridItem

    monkeypatch.setattr("core.project_manager.save_project", lambda project: True)

    project = Project(
        id="p1",
        name="Test",
        created_at="2026-01-01",
        updated_at="2026-01-01",
        series_type="manga",
        chapters=[Chapter(id="c1", name="Ch 1")],
    )
    chapter = project.chapters[0]
    paths = [str(tmp_path / name) for name in ("a.png", "b.png", "c.png")]
    chapter.images = [
        ImageData(path=path, filename=Path(path).name, order=i)
        for i, path in enumerate(paths)
    ]
    ctx.app_state.current_project = project
    ctx.app_state.current_chapter = chapter

    page = ImagesPage(ctx)
    page.chapter_combo.addItem(chapter.name, chapter.id)
    page.chapter_combo.setCurrentIndex(0)
    page._load_chapter_images(chapter, force=True)
    assert page.lbl_count.text() == "3 görsel"
    assert page.manga_panel_editor._all_image_paths == paths

    page.grid.takeItem(1)
    page._sync_order()

    remaining = [paths[0], paths[2]]
    assert [img.path for img in chapter.images] == remaining
    assert page._image_paths == remaining
    assert page.lbl_count.text() == "2 görsel"
    assert page.manga_panel_editor._all_image_paths == remaining
    assert isinstance(page.grid.item(1), ImageGridItem)
    assert page.grid.item(1).image_index == 1
    page.deleteLater()
