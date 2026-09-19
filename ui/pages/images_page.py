"""
RecapAI - Görseller Sayfası (v3)
Manga ve Webtoon türlerine göre farklı görsel işleme akışı sunar.
"""

import logging
from pathlib import Path
from typing import Optional, List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QComboBox, QProgressBar,
    QFileDialog, QSplitter, QAbstractItemView, QMenu, QMessageBox,
    QSizePolicy, QStackedWidget,
)
from PyQt6.QtCore import Qt, QSize, QPoint
from PyQt6.QtGui import QPixmap, QIcon

from core.context import AppContext
from core.qt_image import load_pixmap
from ui.pages.images_widgets import (
    ThumbnailWorker,
    ImageGridItem,
    ImagePreviewPanel,
)
from ui.pages.images_editors import MangaPanelEditor, WebtoonPanelEditor
from ui.utils.icons import Icons
from ui.workers.thread_utils import abort_worker as _abort_worker
from ui.workers.thread_utils import remember_worker as _remember_worker

logger = logging.getLogger(__name__)

class ImagesPage(QWidget):
    """
    Görsel yükleme ve yönetim sayfası (v3).
    Proje türüne (manga/webtoon) göre farklı arayüz sunar.
    """

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._worker: Optional[ThumbnailWorker] = None
        self._live_workers: list = []
        self._image_paths: List[str] = []
        self._loaded_chapter_id = None
        self._build_ui()
        self._connect_app_state()
        logger.debug("ImagesPage oluşturuldu.")

    # ── UI İnşası ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)

        # ── Üst araç çubuğu (tüm menüler burada) ──────────────────
        top_bar = QFrame()
        top_bar.setObjectName("sectionFrame")
        top_bar_layout = QVBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(12, 8, 12, 8)
        top_bar_layout.setSpacing(6)

        # Satır 1: Başlık + bölüm seçici + dosya butonları
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        title_lbl = QLabel("Görseller")
        title_lbl.setObjectName("pageTitle")
        row1.addWidget(title_lbl)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setObjectName("separator")
        row1.addWidget(sep1)

        row1.addWidget(QLabel("Bölüm:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(180)
        self.chapter_combo.setPlaceholderText("Bölüm seçin...")
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        row1.addWidget(self.chapter_combo, 1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setObjectName("separator")
        row1.addWidget(sep2)

        self.btn_add = QPushButton("  Resim Ekle")
        self.btn_add.setFixedHeight(32)
        self.btn_add.setMinimumWidth(130)
        self.btn_add.setIcon(Icons.get(Icons.ADD, color="#ffffff"))
        self.btn_add.setIconSize(QSize(14, 14))
        self.btn_add.clicked.connect(self._add_images)
        row1.addWidget(self.btn_add)

        self.btn_sort = QPushButton("  Otomatik Sırala")
        self.btn_sort.setFixedHeight(32)
        self.btn_sort.setMinimumWidth(150)
        self.btn_sort.setObjectName("secondaryBtn")
        self.btn_sort.setIcon(Icons.get(Icons.REFRESH))
        self.btn_sort.setIconSize(QSize(14, 14))
        self.btn_sort.clicked.connect(self._auto_sort)
        row1.addWidget(self.btn_sort)

        row1.addStretch()

        self.lbl_count = QLabel("0 görsel")
        self.lbl_count.setObjectName("pageSubtitle")
        row1.addWidget(self.lbl_count)

        # Tür rozeti
        self.type_badge = QLabel("")
        self.type_badge.setObjectName("pageSubtitle")
        row1.addWidget(self.type_badge)

        top_bar_layout.addLayout(row1)

        # İlerleme satırı
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        progress_row.addWidget(self.progress_bar, 1)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("pageSubtitle")
        self.lbl_progress.hide()
        progress_row.addWidget(self.lbl_progress)
        top_bar_layout.addLayout(progress_row)

        root.addWidget(top_bar)

        # QStackedWidget: sayfa 0 = manga görünümü, sayfa 1 = webtoon görünümü
        self.stack = QStackedWidget()

        # ── Manga görünümü ─────────────────────────────────────────
        manga_widget = QWidget()
        manga_layout = QVBoxLayout(manga_widget)
        manga_layout.setContentsMargins(0, 0, 0, 0)
        manga_layout.setSpacing(4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.grid = QListWidget()
        self.grid.setObjectName("imageGrid")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(200, 280))
        self.grid.setSpacing(8)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Snap)
        self.grid.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.grid.model().rowsMoved.connect(lambda *args: self._sync_order())
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._show_context_menu)
        self.grid.currentRowChanged.connect(self._on_selection_changed)
        self.grid.setMinimumWidth(300)
        splitter.addWidget(self.grid)

        # Sağ panel: kompakt önizleme bilgisi + manga panel editörü
        right_tabs = QWidget()
        right_layout = QVBoxLayout(right_tabs)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        # Kompakt önizleme bilgisi (yatay, az yer kaplar)
        self.preview_panel = ImagePreviewPanel()
        self.preview_panel.setMaximumHeight(160)
        right_layout.addWidget(self.preview_panel)

        # Manga panel editörü (sayfa seçilince aktif olur) — ana alan
        self.manga_panel_editor = MangaPanelEditor(self.ctx)
        right_layout.addWidget(self.manga_panel_editor, 1)

        splitter.addWidget(right_tabs)
        splitter.setSizes([480, 700])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        manga_layout.addWidget(splitter, 1)
        self.stack.addWidget(manga_widget)

        # ── Webtoon görünümü ───────────────────────────────────────
        webtoon_widget = QWidget()
        webtoon_layout = QVBoxLayout(webtoon_widget)
        webtoon_layout.setContentsMargins(0, 0, 0, 0)
        webtoon_layout.setSpacing(4)

        self.webtoon_editor = WebtoonPanelEditor(self.ctx)
        webtoon_layout.addWidget(self.webtoon_editor, 1)

        self.stack.addWidget(webtoon_widget)

        root.addWidget(self.stack, 1)

    def _make_header(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)
        title = QLabel("Görseller")
        title.setObjectName("pageTitle")
        vbox.addWidget(title)
        subtitle = QLabel("Bölüm görsellerini yükleyin, sıralayın ve panel tespiti yapın.")
        subtitle.setObjectName("pageSubtitle")
        vbox.addWidget(subtitle)
        return container

    def _make_toolbar(self) -> QWidget:
        container = QWidget()
        hbox = QHBoxLayout(container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(10)

        hbox.addWidget(QLabel("Bölüm:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(180)
        self.chapter_combo.setPlaceholderText("Bölüm seçin...")
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        hbox.addWidget(self.chapter_combo, 1)

        hbox.addSpacing(8)

        # Manga'ya özel butonlar
        self.btn_add = QPushButton("  Resim Ekle")
        self.btn_add.setMinimumWidth(130)
        self.btn_add.setIcon(Icons.get(Icons.ADD, color="#ffffff"))
        self.btn_add.setIconSize(QSize(16, 16))
        self.btn_add.clicked.connect(self._add_images)
        hbox.addWidget(self.btn_add)

        self.btn_sort = QPushButton("  Otomatik Sırala")
        self.btn_sort.setMinimumWidth(150)
        self.btn_sort.setObjectName("secondaryBtn")
        self.btn_sort.setIcon(Icons.get(Icons.REFRESH))
        self.btn_sort.setIconSize(QSize(16, 16))
        self.btn_sort.clicked.connect(self._auto_sort)
        hbox.addWidget(self.btn_sort)

        hbox.addStretch()

        self.lbl_count = QLabel("0 görsel")
        self.lbl_count.setObjectName("pageSubtitle")
        hbox.addWidget(self.lbl_count)

        return container

    def _make_progress_bar(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        vbox.addWidget(self.progress_bar)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("pageSubtitle")
        self.lbl_progress.hide()
        vbox.addWidget(self.lbl_progress)
        return container

    def _connect_app_state(self) -> None:
        state = self.ctx.app_state
        state.project_changed.connect(self._on_project_changed)
        state.chapter_changed.connect(self._on_chapter_changed_state)

    # ── App State ───────────────────────────────────────────────────

    def _on_project_changed(self, project) -> None:
        self.chapter_combo.blockSignals(True)
        self.chapter_combo.clear()
        self.chapter_combo.blockSignals(False)
        self.grid.clear()
        self.preview_panel.clear()
        self.manga_panel_editor.clear()
        self.webtoon_editor.clear()
        self._image_paths = []
        self._loaded_chapter_id = None
        if project:
            self.chapter_combo.blockSignals(True)
            for ch in project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
            self.chapter_combo.blockSignals(False)
            self._apply_series_mode(project.series_type)
        self._update_count()

    def _apply_series_mode(self, series_type: str) -> None:
        """Seri türüne göre UI'ı düzenler."""
        if series_type == "webtoon":
            self.stack.setCurrentIndex(1)
            self.type_badge.setText("Mod: Webtoon  |  Görsel birleştirme + panel tespiti aktif")
            self.btn_add.show()
            self.btn_sort.hide()
            default_order = "ltr"
        else:
            self.stack.setCurrentIndex(0)
            self.type_badge.setText("Mod: Manga  |  Sayfa bazlı panel tespiti aktif")
            self.btn_add.show()
            self.btn_sort.show()
            default_order = "rtl"
        if hasattr(self, "manga_panel_editor") and hasattr(self.manga_panel_editor, "reading_order_combo"):
            combo = self.manga_panel_editor.reading_order_combo
            idx = combo.findData(default_order)
            if idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)

    def _on_chapter_changed_state(self, chapter) -> None:
        """Dışarıdan (örn. ProjectPage) chapter değiştiğinde görselleri günceller."""
        if chapter is None:
            return
        state = self.ctx.app_state
        if not state.current_project:
            return
        idx = self.chapter_combo.findData(chapter.id)
        if idx >= 0 and self.chapter_combo.currentIndex() != idx:
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.setCurrentIndex(idx)
            self.chapter_combo.blockSignals(False)
        series_type = getattr(state.current_project, "series_type", "manga")
        if series_type == "webtoon":
            self._load_chapter_webtoon(chapter, state.current_project)
        else:
            self._load_chapter_images(chapter)

    def _on_chapter_changed(self, index: int) -> None:
        if index < 0:
            return
        state = self.ctx.app_state
        if not state.current_project:
            return
        chapter_id = self.chapter_combo.itemData(index)
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return
        state.current_chapter = chapter
        series_type = getattr(state.current_project, "series_type", "manga")

        if series_type == "webtoon":
            self._load_chapter_webtoon(chapter, state.current_project)
        else:
            self._load_chapter_images(chapter)

    # ── Manga Görsel Yükleme ────────────────────────────────────────

    def _load_chapter_images(self, chapter, force: bool = False) -> None:
        chapter_id = getattr(chapter, "id", None)
        if (
            not force
            and chapter_id is not None
            and self._loaded_chapter_id == chapter_id
            and self.grid.count() == len(chapter.images)
        ):
            return
        self.grid.clear()
        self._image_paths = [img.path for img in chapter.images]
        for i, path in enumerate(self._image_paths):
            item = ImageGridItem(path, i)
            self.grid.addItem(item)
        self._update_count()
        self._loaded_chapter_id = chapter_id
        self.manga_panel_editor.clear()
        self.manga_panel_editor.set_all_image_paths(self._image_paths)
        self._start_thumbnail_worker(chapter)

    def _add_images(self) -> None:
        state = self.ctx.app_state
        if not state.current_project:
            QMessageBox.information(self, "Bilgi", "Önce bir proje seçin.")
            return
        if self.chapter_combo.currentIndex() < 0:
            QMessageBox.information(self, "Bilgi", "Önce bir bölüm seçin.")
            return

        files, _ = QFileDialog.getOpenFileNames(
            self, "Görsel Seç", "",
            "Görseller (*.jpg *.jpeg *.png *.webp *.bmp)"
        )
        if not files:
            return

        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return

        from core.models import ImageData
        from core.project_manager import save_project
        series_type = getattr(state.current_project, "series_type", "manga")
        offset = len(chapter.images)
        for i, path in enumerate(files):
            img = ImageData(path=path, filename=Path(path).name, order=offset + i)
            chapter.images.append(img)
            if series_type != "webtoon":
                item = ImageGridItem(path, offset + i)
                self.grid.addItem(item)

        try:
            save_project(state.current_project)
        except Exception as exc:
            QMessageBox.warning(self, "Kayıt hatası", f"Proje kaydedilemedi:\n{exc}")
            return
        self._image_paths = [img.path for img in chapter.images]
        self._update_count()
        series_type = getattr(state.current_project, "series_type", "manga")
        if series_type == "webtoon":
            self._load_chapter_webtoon(chapter, state.current_project, force=True)
        else:
            self.manga_panel_editor.set_all_image_paths(self._image_paths)
            self._start_thumbnail_worker(chapter)
        self.ctx.app_state.status_message.emit(f"{len(files)} görsel eklendi.")

    def _auto_sort(self) -> None:
        from core.image_processor import natural_key
        state = self.ctx.app_state
        if not state.current_project or self.chapter_combo.currentIndex() < 0:
            return
        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return
        chapter.images.sort(key=lambda img: natural_key(img.filename))
        for i, img in enumerate(chapter.images):
            img.order = i
        from core.project_manager import save_project
        try:
            save_project(state.current_project)
        except Exception as exc:
            QMessageBox.warning(self, "Kayıt hatası", f"Proje kaydedilemedi:\n{exc}")
            return
        self._load_chapter_images(chapter, force=True)
        self.ctx.app_state.status_message.emit("Görseller otomatik sıralandı.")

    # ── Webtoon Görsel Yükleme ──────────────────────────────────────

    def _load_chapter_webtoon(self, chapter, project, force: bool = False) -> None:
        chapter_id = getattr(chapter, "id", None)
        if (
            not force
            and chapter_id is not None
            and self._loaded_chapter_id == chapter_id
            and self._image_paths == [img.path for img in chapter.images]
        ):
            return
        self._image_paths = [img.path for img in chapter.images]
        self._update_count()
        self._loaded_chapter_id = chapter_id

        from core.project_manager import get_project_dir
        project_dir = get_project_dir(project)
        if not project_dir:
            QMessageBox.warning(self, "Uyarı", "Proje klasörü bulunamadı.")
            return

        output_dir = str(project_dir / "output" / f"webtoon_{chapter.id}")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        self.webtoon_editor.load_chapter_images(self._image_paths, output_dir)
        self.lbl_count.setText(f"{len(self._image_paths)} görsel")

    # ── Thumbnail Worker ────────────────────────────────────────────

    def _start_thumbnail_worker(self, chapter) -> None:
        if self._worker and self._worker.isRunning():
            _abort_worker(self, self._worker)

        state = self.ctx.app_state
        from core.project_manager import get_project_dir
        project_dir = get_project_dir(state.current_project)
        if not project_dir:
            return

        thumb_dir = project_dir / "thumbnails"
        paths = [img.path for img in chapter.images]
        if not paths:
            self.progress_bar.hide()
            self.lbl_progress.hide()
            return

        self.progress_bar.setRange(0, len(paths))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.lbl_progress.setText("Thumbnail oluşturuluyor...")
        self.lbl_progress.show()

        self._worker = ThumbnailWorker(paths, str(thumb_dir), parent=self)
        self._worker.progress.connect(self._on_thumb_progress)
        self._worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._worker.finished.connect(self._on_thumb_finished)
        _remember_worker(self, self._worker)
        self._worker.start()

    def _on_thumb_progress(self, done: int, total: int) -> None:
        if self.sender() is not self._worker:
            return
        self.progress_bar.setValue(done)
        self.lbl_progress.setText(f"Thumbnail: {done}/{total}")

    def _on_thumbnail_ready(self, index: int, thumb_path: str) -> None:
        if self.sender() is not self._worker:
            return
        item = self.grid.item(index)
        if item:
            pixmap = load_pixmap(thumb_path)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))

    def _on_thumb_finished(self) -> None:
        if self.sender() is not self._worker:
            return
        self.progress_bar.hide()
        self.lbl_progress.hide()
        self.ctx.app_state.status_message.emit("Thumbnail oluşturma tamamlandı.")

    # ── Context Menu (manga grid) ────────────────────────────────────

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.grid.itemAt(pos)
        if not item or not isinstance(item, ImageGridItem):
            return

        menu = QMenu(self)
        act_detect = menu.addAction(Icons.get(Icons.SEARCH), "Bu Sayfada Panel Tespit Et")
        menu.addSeparator()
        act_up = menu.addAction(Icons.get(Icons.ARROW_UP), "Üst Sıraya Taşı")
        act_down = menu.addAction(Icons.get(Icons.ARROW_DOWN), "Alt Sıraya Taşı")
        menu.addSeparator()
        act_del = menu.addAction(Icons.get(Icons.DELETE, color="#ef4444"), "Sil")

        action = menu.exec(self.grid.mapToGlobal(pos))
        row = self.grid.row(item)

        if action == act_detect:
            self.manga_panel_editor.load_page(item.image_path)
        elif action == act_up and row > 0:
            self._move_item(row, row - 1)
        elif action == act_down and row < self.grid.count() - 1:
            self._move_item(row, row + 1)
        elif action == act_del:
            self._delete_item(row)

    def _move_item(self, from_row: int, to_row: int) -> None:
        item = self.grid.takeItem(from_row)
        self.grid.insertItem(to_row, item)
        self.grid.setCurrentRow(to_row)
        self._sync_order()

    def _delete_item(self, row: int) -> None:
        item = self.grid.item(row)
        if not isinstance(item, ImageGridItem):
            return
        reply = QMessageBox.question(
            self, "Görseli Sil",
            f"'{item.filename}' görseli listeden kaldırılsın mı?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.grid.takeItem(row)
            self._sync_order()

    def _sync_order(self) -> None:
        state = self.ctx.app_state
        if not state.current_project or self.chapter_combo.currentIndex() < 0:
            return
        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return

        by_path = {img.path: img for img in chapter.images}
        new_images = []
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if isinstance(it, ImageGridItem):
                from core.models import ImageData
                old = by_path.get(it.image_path)
                it.image_index = i
                it.setText(
                    f"{i + 1}\n{it.filename[:18]}"
                    f"{'…' if len(it.filename) > 18 else ''}"
                )
                new_images.append(ImageData(
                    path=it.image_path,
                    filename=it.filename,
                    order=i,
                    thumbnail_path=old.thumbnail_path if old else None,
                ))
        chapter.images = new_images
        self._image_paths = [img.path for img in chapter.images]
        self.manga_panel_editor.set_all_image_paths(self._image_paths)

        from core.project_manager import save_project
        try:
            save_project(state.current_project)
        except Exception as persist_exc:
            QMessageBox.warning(self, "Kayıt hatası", f"Proje kaydedilemedi:\n{persist_exc}")
            return
        self._update_count()

    # ── Preview ──────────────────────────────────────────────────────

    def _on_selection_changed(self, row: int) -> None:
        item = self.grid.item(row)
        if isinstance(item, ImageGridItem):
            self.preview_panel.show_image(item.image_path, item.image_index)
            # Manga panel editörüne de yükle
            self.manga_panel_editor.load_page(item.image_path)
        else:
            self.preview_panel.clear()
            self.manga_panel_editor.clear()

    # ── Helpers ──────────────────────────────────────────────────────

    def _update_count(self) -> None:
        count = len(self._image_paths)
        self.lbl_count.setText(f"{count} görsel")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        state = self.ctx.app_state
        if not state.current_project:
            return

        current_ids = [self.chapter_combo.itemData(i) for i in range(self.chapter_combo.count())]
        wanted_ids = [ch.id for ch in state.current_project.chapters]
        if current_ids != wanted_ids:
            current_data = self.chapter_combo.currentData()
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.clear()
            for ch in state.current_project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
            self.chapter_combo.blockSignals(False)
            target = current_data or (state.current_chapter.id if state.current_chapter else None)
            if target:
                idx = self.chapter_combo.findData(target)
                if idx >= 0:
                    self.chapter_combo.setCurrentIndex(idx)
        elif state.current_chapter:
            idx = self.chapter_combo.findData(state.current_chapter.id)
            if idx >= 0 and self.chapter_combo.currentIndex() != idx:
                self.chapter_combo.blockSignals(True)
                self.chapter_combo.setCurrentIndex(idx)
                self.chapter_combo.blockSignals(False)

        if self.chapter_combo.currentIndex() < 0 and self.chapter_combo.count() > 0:
            self.chapter_combo.setCurrentIndex(0)
        if self.chapter_combo.currentIndex() >= 0:
            self._on_chapter_changed(self.chapter_combo.currentIndex())
        self._apply_series_mode(state.current_project.series_type)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
