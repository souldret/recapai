"""
RecapAI - Manga ve Webtoon panel editörleri.
"""

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QProgressBar, QGroupBox, QCheckBox, QToolButton,
    QFileDialog, QSplitter, QMessageBox, QDialog,
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon

from core.context import AppContext
from ui.pages.images_widgets import (
    PanelBox,
    PanelCanvas,
    PanelListWidget,
    PanelDetectSettingsDialog,
    PanelThumbnailStrip,
    PanelCropPreviewDialog,
    ShortcutsDialog,
    _count_overlaps,
    _engine_flags,
    _confirm_vision_cost,
    _load_cv2_image,
)
from ui.utils.icons import Icons
from ui.workers.thread_utils import abort_worker as _abort_worker
from ui.workers.thread_utils import remember_worker as _remember_worker

logger = logging.getLogger(__name__)

class MangaPanelEditor(QWidget):
    """
    Manga modu: seçili sayfa üzerinde panel tespiti gösterir.
    Sol: sayfa görseli (PanelCanvas), sağ: panel listesi + aksiyon butonları.
    """

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._current_image_path: Optional[str] = None
        self._last_loaded_path: Optional[str] = None   # UI freeze cache
        self._cached_image: Optional[np.ndarray] = None  # perf: disk okumayı önle
        self._detect_worker = None
        self._load_worker = None
        self._batch_worker = None
        self._live_workers: list = []
        self._batch_results: dict = {}
        self._all_image_paths: List[str] = []          # batch detection için
        self._min_area = 0.015
        self._max_area = 0.92
        self._engine = "yolo"
        self._vision_model = "google/gemini-2.5-flash"
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(400)
        self._persist_timer.timeout.connect(self._flush_persist)
        self._pending_persist: Optional[tuple] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ── Araç çubuğu (satır 1) ──────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(6)

        self.btn_detect = QPushButton("  Tespit Et")
        self.btn_detect.setIcon(Icons.get(Icons.SEARCH, color="#ffffff"))
        self.btn_detect.setIconSize(QSize(14, 14))
        self.btn_detect.setEnabled(False)
        self.btn_detect.setToolTip("Aktif sayfada panel tespiti yap")
        self.btn_detect.clicked.connect(self._run_detection)
        tb.addWidget(self.btn_detect)

        self.btn_batch_detect = QPushButton("  Tüm Sayfalarda")
        self.btn_batch_detect.setObjectName("secondaryBtn")
        self.btn_batch_detect.setIcon(Icons.get(Icons.SEARCH))
        self.btn_batch_detect.setIconSize(QSize(14, 14))
        self.btn_batch_detect.setEnabled(False)
        self.btn_batch_detect.setToolTip("Tüm sayfalarda toplu panel tespiti yap")
        self.btn_batch_detect.clicked.connect(self._run_batch_detection)
        tb.addWidget(self.btn_batch_detect)

        self.btn_batch_stop = QPushButton("  Durdur")
        self.btn_batch_stop.setObjectName("dangerButton")
        self.btn_batch_stop.setEnabled(False)
        self.btn_batch_stop.setToolTip("Toplu tespiti durdur, kısmi sonuçları kaydet")
        self.btn_batch_stop.clicked.connect(self._stop_batch_detection)
        tb.addWidget(self.btn_batch_stop)

        self.btn_settings = QPushButton("  Ayarlar")
        self.btn_settings.setObjectName("secondaryBtn")
        self.btn_settings.setIcon(Icons.get(Icons.SETTINGS))
        self.btn_settings.setIconSize(QSize(14, 14))
        self.btn_settings.clicked.connect(self._open_settings)
        tb.addWidget(self.btn_settings)

        self.reading_order_combo = QComboBox()
        self.reading_order_combo.addItem("RTL (Manga)", "rtl")
        self.reading_order_combo.addItem("LTR (Manhwa)", "ltr")
        self.reading_order_combo.setFixedWidth(130)
        self.reading_order_combo.setToolTip("Panel okuma düzeni")
        self.reading_order_combo.currentIndexChanged.connect(self._on_reading_order_changed)
        tb.addWidget(self.reading_order_combo)

        self.chk_show_arrows = QCheckBox("Oklar")
        self.chk_show_arrows.setChecked(True)
        self.chk_show_arrows.setToolTip("Okuma sırası oklarını göster/gizle")
        self.chk_show_arrows.toggled.connect(lambda v: self.canvas.set_show_order_arrows(v))
        tb.addWidget(self.chk_show_arrows)

        sep_a = QFrame(); sep_a.setFrameShape(QFrame.Shape.VLine)
        sep_a.setObjectName("separator"); sep_a.setFixedWidth(1)
        tb.addWidget(sep_a)

        self.btn_merge = QPushButton("  Birleştir")
        self.btn_merge.setObjectName("secondaryBtn")
        self.btn_merge.setToolTip("Ctrl+tıklama ile seçilen panelleri birleştir (Ctrl+M)")
        self.btn_merge.setEnabled(False)
        self.btn_merge.clicked.connect(self._merge_panels)
        tb.addWidget(self.btn_merge)

        self.btn_save_panels = QPushButton("  Kaydet")
        self.btn_save_panels.setObjectName("secondaryBtn")
        self.btn_save_panels.setEnabled(False)
        self.btn_save_panels.setToolTip("Panelleri kırpıp kaydet (önizlemeli)")
        self.btn_save_panels.clicked.connect(self._save_panels)
        tb.addWidget(self.btn_save_panels)

        # Ayırıcı
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("separator"); sep.setFixedWidth(1)
        tb.addWidget(sep)

        self.btn_zoom_in = QToolButton()
        self.btn_zoom_in.setText("+")
        self.btn_zoom_in.setToolTip("Yakınlaştır (Ctrl++)")
        self.btn_zoom_in.setFixedSize(32, 32)
        tb.addWidget(self.btn_zoom_in)

        self.btn_zoom_out = QToolButton()
        self.btn_zoom_out.setText("−")
        self.btn_zoom_out.setToolTip("Uzaklaştır (Ctrl+-)")
        self.btn_zoom_out.setFixedSize(32, 32)
        tb.addWidget(self.btn_zoom_out)

        self.btn_zoom_fit = QPushButton("Sığdır")
        self.btn_zoom_fit.setObjectName("secondaryBtn")
        self.btn_zoom_fit.setToolTip("Ekrana sığdır (Ctrl+0)")
        tb.addWidget(self.btn_zoom_fit)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setObjectName("separator"); sep2.setFixedWidth(1)
        tb.addWidget(sep2)

        self.btn_undo = QToolButton()
        self.btn_undo.setText("↩")
        self.btn_undo.setToolTip("Geri al (Ctrl+Z)")
        self.btn_undo.setFixedSize(32, 32)
        tb.addWidget(self.btn_undo)

        self.btn_redo = QToolButton()
        self.btn_redo.setText("↪")
        self.btn_redo.setToolTip("İleri al (Ctrl+Y)")
        self.btn_redo.setFixedSize(32, 32)
        tb.addWidget(self.btn_redo)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setObjectName("separator"); sep3.setFixedWidth(1)
        tb.addWidget(sep3)

        btn_shortcuts = QToolButton()
        btn_shortcuts.setText("?")
        btn_shortcuts.setToolTip("Klavye kısayolları (?)")
        btn_shortcuts.setFixedSize(32, 32)
        btn_shortcuts.clicked.connect(self._show_shortcuts)
        tb.addWidget(btn_shortcuts)

        tb.addStretch()

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("pageSubtitle")
        self.lbl_zoom.setFixedWidth(44)
        tb.addWidget(self.lbl_zoom)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("pageSubtitle")
        tb.addWidget(self.lbl_status)

        root.addLayout(tb)

        # İlerleme çubuğu
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        # Batch ilerleme çubuğu
        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setFixedHeight(4)
        self.batch_progress_bar.setTextVisible(False)
        self.batch_progress_bar.hide()
        root.addWidget(self.batch_progress_bar)

        self.lbl_batch_status = QLabel("")
        self.lbl_batch_status.setObjectName("pageSubtitle")
        self.lbl_batch_status.hide()
        root.addWidget(self.lbl_batch_status)

        # Ana splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: canvas (doğrudan — kendi scroll/zoom mantığı var)
        canvas_container = QWidget()
        canvas_vbox = QVBoxLayout(canvas_container)
        canvas_vbox.setContentsMargins(0, 0, 0, 0)
        canvas_vbox.setSpacing(2)

        self.canvas = PanelCanvas()
        self.canvas.panel_selected.connect(self._on_canvas_panel_selected)
        self.canvas.panels_changed.connect(self._on_panels_changed)
        self.canvas.zoom_changed.connect(self._on_zoom_changed)
        canvas_vbox.addWidget(self.canvas, 1)

        # Thumbnail şeridi
        self.thumb_strip = PanelThumbnailStrip()
        self.thumb_strip.panel_clicked.connect(self._on_thumb_panel_clicked)
        canvas_vbox.addWidget(self.thumb_strip)

        splitter.addWidget(canvas_container)

        # Zoom butonlarını canvas'a bağla (canvas oluşturulduktan sonra)
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        self.btn_zoom_fit.clicked.connect(self.canvas.zoom_fit)
        self.btn_undo.clicked.connect(self.canvas.undo)
        self.btn_redo.clicked.connect(self.canvas.redo)

        # Sağ: panel listesi
        self.panel_list_widget = PanelListWidget()
        self.panel_list_widget.panel_selection_changed.connect(self.canvas.select_panel)
        self.panel_list_widget.panel_delete_requested.connect(self.canvas.delete_panel)
        self.panel_list_widget.panel_update_requested.connect(
            lambda idx, box: self.canvas.update_panel(idx, box)
        )
        self.panel_list_widget.panel_add_requested.connect(self._add_full_panel)
        splitter.addWidget(self.panel_list_widget)

        splitter.setSizes([700, 260])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

    def load_page(self, image_path: str) -> None:
        """Düzenlenecek sayfayı arka planda yükler. Aynı dosya zaten yüklüyse atlar."""
        if image_path == self._last_loaded_path:
            return  # Aynı sayfa zaten yüklü — UI freeze'i önle

        # Önceki yükleyiciyi durdur (GUI thread'i dondurma)
        if self._load_worker and self._load_worker.isRunning():
            _abort_worker(self, self._load_worker)

        self._current_image_path = image_path
        self.btn_detect.setEnabled(False)
        self.btn_save_panels.setEnabled(False)
        self.lbl_status.setText(f"Yükleniyor: {Path(image_path).name}...")

        from ui.workers.panel_worker import ImageLoadWorker
        self._load_worker = ImageLoadWorker(image_path, parent=self)
        self._load_worker.finished.connect(self._on_image_loaded)
        self._load_worker.error.connect(self._on_image_load_error)
        _remember_worker(self, self._load_worker)
        self._load_worker.start()

    def _on_image_loaded(self, image_path: str, img) -> None:
        """Arka plan yükleyiciden gelen görüntü."""
        if self.sender() is not self._load_worker:
            return
        if image_path != self._current_image_path:
            return
        self._last_loaded_path = image_path
        self._cached_image = img
        self.canvas.set_image(img)
        restored = self._restore_panels(image_path)
        self.canvas.set_panels(restored)
        self.panel_list_widget.load_panels(restored)
        if restored and img is not None:
            self.thumb_strip.load_panels(img, restored)
        else:
            self.thumb_strip.clear()
        self.btn_detect.setEnabled(True)
        self.btn_save_panels.setEnabled(bool(restored))
        self.btn_merge.setEnabled(bool(restored))
        extra = f"  ·  {len(restored)} panel" if restored else ""
        self.lbl_status.setText(Path(image_path).name + extra)

    def _restore_panels(self, image_path: str) -> list:
        boxes = self._batch_results.get(image_path)
        if boxes:
            return list(boxes)
        chapter = getattr(self.ctx.app_state, "current_chapter", None)
        if chapter is None:
            return []
        filename = Path(image_path).name
        stored = chapter.get_panels_for_image(filename)
        out = []
        for p in stored:
            try:
                out.append((int(p["x"]), int(p["y"]), int(p["w"]), int(p["h"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _persist_panels(self, image_path: str, boxes: list, immediate: bool = False) -> None:
        if not image_path:
            return
        self._batch_results[image_path] = list(boxes)
        self._pending_persist = (image_path, list(boxes))
        if immediate:
            self._flush_persist()
        else:
            self._persist_timer.start()

    def _flush_persist(self) -> None:
        if not self._pending_persist:
            return
        image_path, boxes = self._pending_persist
        self._pending_persist = None
        chapter = getattr(self.ctx.app_state, "current_chapter", None)
        project = getattr(self.ctx.app_state, "current_project", None)
        if chapter is None:
            return
        order = self.reading_order_combo.currentData() or "rtl"
        chapter.set_panels_for_image(Path(image_path).name, boxes, reading_order=order)
        if project is not None:
            try:
                from core.project_manager import save_project
                save_project(project)
            except Exception as exc:
                logger.warning("Panel verisi kaydedilemedi: %s", exc)

    def _on_image_load_error(self, msg: str) -> None:
        self.lbl_status.setText(f"Görüntü yüklenemedi: {msg}")
        self.btn_detect.setEnabled(False)

    def set_all_image_paths(self, paths: List[str]) -> None:
        """Toplu panel tespiti için tüm sayfa yollarını ayarlar."""
        self._all_image_paths = paths
        self.btn_batch_detect.setEnabled(len(paths) > 1)

    def get_batch_results(self) -> dict:
        """Toplu tespit sonuçlarını döndürür: {image_path: [(x,y,w,h)...]}"""
        return self._batch_results

    def clear(self) -> None:
        for w in (self._load_worker, self._detect_worker, self._batch_worker):
            _abort_worker(self, w)
        self._persist_timer.stop()
        self._pending_persist = None
        self._current_image_path = None
        self._last_loaded_path = None
        self._cached_image = None
        self._all_image_paths = []
        self._batch_results = {}
        self.canvas.clear()
        self.panel_list_widget.load_panels([])
        self.thumb_strip.clear()
        self.btn_detect.setEnabled(False)
        self.btn_batch_detect.setEnabled(False)
        self.btn_save_panels.setEnabled(False)
        self.btn_merge.setEnabled(False)
        self.batch_progress_bar.hide()
        self.lbl_batch_status.hide()
        self.lbl_status.setText("")

    # ── Panel Tespiti ───────────────────────────────────────────────

    def _open_settings(self) -> None:
        dlg = PanelDetectSettingsDialog(self)
        dlg.spin_min.setValue(self._min_area * 100)
        dlg.spin_max.setValue(self._max_area * 100)
        idx = dlg.engine_combo.findData(self._engine)
        if idx >= 0:
            dlg.engine_combo.setCurrentIndex(idx)
        vidx = dlg.vision_model_combo.findData(getattr(self, "_vision_model", None))
        if vidx >= 0:
            dlg.vision_model_combo.setCurrentIndex(vidx)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._min_area, self._max_area = dlg.get_values()
            self._engine = dlg.get_engine()
            self._vision_model = dlg.get_vision_model()

    def _run_detection(self) -> None:
        if not self._current_image_path:
            return
        if self._detect_worker and self._detect_worker.isRunning():
            return

        use_yolo, use_vision = _engine_flags(self._engine)
        if use_vision and not _confirm_vision_cost(self, 1, getattr(self, "_vision_model", None)):
            return

        from ui.workers.panel_worker import PanelDetectWorker
        self.progress_bar.show()
        self.btn_detect.setEnabled(False)
        self.lbl_status.setText("Tespit ediliyor...")

        order = self.reading_order_combo.currentData() or "rtl"
        self._detect_worker = PanelDetectWorker(
            image_path=self._current_image_path,
            reading_order=order,
            min_area_ratio=self._min_area,
            max_area_ratio=self._max_area,
            use_yolo=use_yolo,
            use_vision=use_vision,
            vision_model=getattr(self, "_vision_model", None),
            parent=self,
        )
        self._detect_worker.progress.connect(self.lbl_status.setText)
        self._detect_worker.finished.connect(self._on_detection_finished)
        self._detect_worker.error.connect(self._on_detection_error)
        _remember_worker(self, self._detect_worker)
        self._detect_worker.start()

    def _on_detection_finished(self, boxes: list, engine: str = "") -> None:
        self.progress_bar.hide()
        self.btn_detect.setEnabled(True)
        self.canvas.set_panels(boxes)
        self.panel_list_widget.load_panels(boxes)
        self.btn_save_panels.setEnabled(bool(boxes))
        self.btn_merge.setEnabled(bool(boxes))
        used = engine or getattr(self, "_engine", "yolo")
        overlaps = _count_overlaps(boxes)
        extra = [f"motor: {used}"]
        if overlaps:
            extra.append(f"{overlaps} örtüşen kutu")
        self.lbl_status.setText(
            f"{len(boxes)} panel tespit edildi ({', '.join(extra)})."
        )
        self.ctx.app_state.status_message.emit(self.lbl_status.text())
        if self._cached_image is not None and boxes:
            self.thumb_strip.load_panels(self._cached_image, boxes)
        self._persist_panels(self._current_image_path or "", boxes, immediate=True)

    def _on_detection_error(self, msg: str) -> None:
        self.progress_bar.hide()
        self.btn_detect.setEnabled(True)
        self.lbl_status.setText(f"Hata: {msg}")
        QMessageBox.critical(self, "Panel Tespiti Hatası", msg)

    # ── Batch Detection ─────────────────────────────────────────────

    def _run_batch_detection(self) -> None:
        """Tüm sayfalarda toplu panel tespiti."""
        if not self._all_image_paths:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return

        use_yolo, use_vision = _engine_flags(self._engine)
        if use_vision and not _confirm_vision_cost(self, len(self._all_image_paths), getattr(self, "_vision_model", None)):
            return

        from ui.workers.panel_worker import PanelBatchDetectWorker
        order = self.reading_order_combo.currentData() or "rtl"
        self._batch_worker = PanelBatchDetectWorker(
            image_paths=self._all_image_paths,
            reading_order=order,
            min_area_ratio=self._min_area,
            max_area_ratio=self._max_area,
            use_yolo=use_yolo,
            use_vision=use_vision,
            vision_model=getattr(self, "_vision_model", None),
            parent=self,
        )
        self._batch_results: dict = {}
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setRange(0, len(self._all_image_paths))
        self.batch_progress_bar.show()
        self.lbl_batch_status.show()
        self.btn_batch_detect.setEnabled(False)
        self.btn_detect.setEnabled(False)
        self.btn_batch_stop.setEnabled(True)

        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.page_done.connect(self._on_batch_page_done)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.error.connect(self._on_batch_error)
        _remember_worker(self, self._batch_worker)
        self._batch_worker.start()

    def _stop_batch_detection(self) -> None:
        if self._batch_worker and self._batch_worker.isRunning():
            _abort_worker(self, self._batch_worker)
            self.btn_batch_stop.setEnabled(False)
            self.lbl_batch_status.setText("Durduruluyor… kısmi sonuçlar kaydedildi.")

    def _on_batch_progress(self, done: int, total: int, msg: str) -> None:
        self.batch_progress_bar.setValue(done)
        self.lbl_batch_status.setText(msg)

    def _on_batch_page_done(self, page_index: int, image_path: str, boxes: list) -> None:
        self._persist_panels(image_path, boxes, immediate=True)
        if image_path == self._current_image_path:
            self.canvas.set_panels(boxes)
            self.panel_list_widget.load_panels(boxes)
            self.btn_save_panels.setEnabled(bool(boxes))
            if self._cached_image is not None and boxes:
                self.thumb_strip.load_panels(self._cached_image, boxes)

    def _on_batch_finished(self, results: dict) -> None:
        self.batch_progress_bar.hide()
        self.lbl_batch_status.hide()
        self.btn_batch_detect.setEnabled(True)
        self.btn_detect.setEnabled(bool(self._current_image_path))
        if hasattr(self, "btn_batch_stop"):
            self.btn_batch_stop.setEnabled(False)
        total_panels = sum(len(v) for v in results.values())
        self.ctx.app_state.status_message.emit(
            f"Toplu tespit tamamlandı: {len(results)} sayfa, {total_panels} panel."
        )
        QMessageBox.information(
            self, "Toplu Tespit",
            f"{len(results)} sayfa işlendi.\nToplam {total_panels} panel tespit edildi."
        )

    def _on_batch_error(self, msg: str) -> None:
        self.batch_progress_bar.hide()
        self.lbl_batch_status.hide()
        self.btn_batch_detect.setEnabled(True)
        if hasattr(self, "btn_batch_stop"):
            self.btn_batch_stop.setEnabled(False)
        QMessageBox.critical(self, "Toplu Tespit Hatası", msg)

    # ── Panel Birleştirme ───────────────────────────────────────────

    def _merge_panels(self) -> None:
        if len(self.canvas._multi_selected) < 2:
            QMessageBox.information(
                self, "Bilgi",
                "Birleştirmek için Ctrl+tıklama ile en az 2 panel seçin."
            )
            return
        ok = self.canvas.merge_selected_panels()
        if ok:
            self.ctx.app_state.status_message.emit("Paneller birleştirildi.")

    # ── Kısayollar ──────────────────────────────────────────────────

    def _show_shortcuts(self) -> None:
        dlg = ShortcutsDialog(self)
        dlg.exec()

    # ── Okuma Düzeni ────────────────────────────────────────────────

    def _on_reading_order_changed(self, _index: int) -> None:
        order = self.reading_order_combo.currentData() or "rtl"
        self.canvas.set_reading_order(order)
        boxes = self.canvas.get_panels()
        if not boxes:
            return
        from core.panel_detector import PanelDetector
        size = self.canvas.image_size()
        img_h = size[0] if size else 2000
        detector = PanelDetector()
        if order == "ltr":
            sorted_boxes = detector.sort_panels_ltr(boxes, img_h)
        else:
            sorted_boxes = detector.sort_panels(boxes, img_h)
        self.canvas.set_panels(sorted_boxes)
        self.panel_list_widget.load_panels(sorted_boxes)
        self._persist_panels(self._current_image_path or "", sorted_boxes, immediate=True)

    # ── Thumbnail Strip ─────────────────────────────────────────────

    def _on_thumb_panel_clicked(self, index: int) -> None:
        """Thumbnail şeridinde panele tıklanınca canvas'ı o panele zoom yap."""
        panels = self.canvas.get_panels()
        if index < 0 or index >= len(panels):
            return
        # Canvas'ı seç ve paneli ortala
        self.canvas.select_panel(index)
        self.thumb_strip.set_selected(index)
        self.panel_list_widget.select_panel(index)

    # ── Canvas sinyalleri ───────────────────────────────────────────

    def _on_canvas_panel_selected(self, index: int) -> None:
        self.panel_list_widget.select_panel(index)
        self.thumb_strip.set_selected(index)
        self.thumb_strip.scroll_to(index)

    def _on_panels_changed(self) -> None:
        panels = self.canvas.get_panels()
        self.panel_list_widget.load_panels(panels, keep_selection=self.canvas.selected_index)
        self.btn_save_panels.setEnabled(bool(panels))
        self.btn_merge.setEnabled(bool(panels))
        if self._cached_image is not None and panels:
            self.thumb_strip.load_panels(self._cached_image, panels)
        if self._current_image_path:
            self._persist_panels(self._current_image_path, panels)

    def _on_zoom_changed(self, zoom: float) -> None:
        self.lbl_zoom.setText(f"{round(zoom * 100)}%")

    def _add_full_panel(self) -> None:
        size = self.canvas.image_size()
        if size is None:
            return
        h, w = size
        margin = 10
        self.canvas.add_panel((margin, margin, w - 2 * margin, h - 2 * margin))

    # ── Panelleri Kaydet ────────────────────────────────────────────

    def _save_panels(self) -> None:
        if not self._current_image_path:
            return
        boxes = self.canvas.get_panels()
        if not boxes:
            QMessageBox.information(self, "Bilgi", "Kaydedilecek panel yok.")
            return

        # Önizleme dialogu
        try:
            img = _load_cv2_image(self._current_image_path)
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Görüntü okunamadı:\n{exc}")
            return

        preview_dlg = PanelCropPreviewDialog(img, boxes, parent=self)
        if preview_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected_boxes = preview_dlg.get_selected_boxes()
        if not selected_boxes:
            QMessageBox.information(self, "Bilgi", "Hiç panel seçilmedi.")
            return

        out_dir = QFileDialog.getExistingDirectory(
            self, "Panelleri Kaydet — Klasör Seç"
        )
        if not out_dir:
            return

        try:
            from core.panel_detector import PanelDetector
            panels_img = PanelDetector.crop_panels(img, selected_boxes)
            ext = Path(self._current_image_path).suffix or ".jpg"
            saved = PanelDetector.save_panels(
                panels_img, out_dir,
                prefix=Path(self._current_image_path).stem,
                ext=ext,
            )
            QMessageBox.information(
                self, "Kaydedildi",
                f"{len(saved)} panel kaydedildi:\n{out_dir}"
            )
            self.ctx.app_state.status_message.emit(f"{len(saved)} panel dosyaya kaydedildi.")
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Paneller kaydedilemedi:\n{exc}")


# ══════════════════════════════════════════════════════════════════════════════
# Webtoon Panel Editörü
# Bölüm görsellerini birleştir → panel tespiti → manuel düzenleme
# ══════════════════════════════════════════════════════════════════════════════

class WebtoonPanelEditor(QWidget):
    """
    Webtoon modu:
      1. Bölüm görsellerini dikey birleştir (stitch) — kalite/format seçimiyle
      2. Birleşik görsel üzerinde yatay çizgi tespiti + panel tespiti
      3. Manuel panel düzenleme (ekle/sil/boyutlandır)
    """

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._image_paths: List[str] = []
        self._stitched_path: Optional[str] = None
        self._output_dir: str = ""
        self._stitch_worker = None
        self._detect_worker = None
        self._load_worker = None
        self._live_workers: list = []
        self._min_area = 0.01
        self._engine = "yolo"
        self._vision_model = "google/gemini-2.5-flash"
        self._max_area = 0.80
        # Stitch kalite ayarları
        self._stitch_scale = 100       # %100 = orijinal
        self._stitch_jpeg_quality: Optional[int] = None  # None = PNG
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ── Adım 1: Stitch ─────────────────────────────────────────
        stitch_group = QGroupBox("Adım 1 — Görselleri Birleştir (Stitch)")
        stitch_layout = QVBoxLayout(stitch_group)
        stitch_layout.setSpacing(8)

        stitch_row1 = QHBoxLayout()
        self.lbl_stitch_info = QLabel("Bölüm görselleri seçilmedi.")
        self.lbl_stitch_info.setObjectName("pageSubtitle")
        stitch_row1.addWidget(self.lbl_stitch_info, 1)
        stitch_layout.addLayout(stitch_row1)

        # Kalite ayarları satırı
        stitch_row2 = QHBoxLayout()
        stitch_row2.setSpacing(8)
        stitch_row2.addWidget(QLabel("Çözünürlük:"))
        self.stitch_scale_combo = QComboBox()
        self.stitch_scale_combo.addItem("Orijinal (100%)", 100)
        self.stitch_scale_combo.addItem("%75", 75)
        self.stitch_scale_combo.addItem("%50", 50)
        self.stitch_scale_combo.setFixedWidth(150)
        self.stitch_scale_combo.currentIndexChanged.connect(self._on_stitch_scale_changed)
        stitch_row2.addWidget(self.stitch_scale_combo)

        stitch_row2.addSpacing(12)
        stitch_row2.addWidget(QLabel("Format:"))
        self.stitch_format_combo = QComboBox()
        self.stitch_format_combo.addItem("PNG (kayıpsız)", "png")
        self.stitch_format_combo.addItem("JPEG q=85", "jpeg85")
        self.stitch_format_combo.addItem("JPEG q=70", "jpeg70")
        self.stitch_format_combo.addItem("JPEG q=50", "jpeg50")
        self.stitch_format_combo.setFixedWidth(150)
        self.stitch_format_combo.currentIndexChanged.connect(self._on_stitch_format_changed)
        stitch_row2.addWidget(self.stitch_format_combo)

        stitch_row2.addStretch()
        self.btn_stitch = QPushButton("  Birleştir")
        self.btn_stitch.setIcon(Icons.get(Icons.ADD, color="#ffffff"))
        self.btn_stitch.setIconSize(QSize(14, 14))
        self.btn_stitch.setEnabled(False)
        self.btn_stitch.clicked.connect(self._run_stitch)
        stitch_row2.addWidget(self.btn_stitch)
        stitch_layout.addLayout(stitch_row2)

        root.addWidget(stitch_group)

        # ── Adım 2: Panel Tespiti ──────────────────────────────────
        detect_group = QGroupBox("Adım 2 — Panel Tespiti")
        detect_layout = QVBoxLayout(detect_group)
        detect_layout.setSpacing(6)

        info_row = QHBoxLayout()
        self.lbl_detect_info = QLabel("Önce görselleri birleştirin.")
        self.lbl_detect_info.setObjectName("pageSubtitle")
        info_row.addWidget(self.lbl_detect_info, 1)
        detect_layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        self.btn_hline_detect = QPushButton("  Yatay Çizgi Tespit")
        self.btn_hline_detect.setObjectName("secondaryBtn")
        self.btn_hline_detect.setToolTip("Beyaz/siyah yatay çizgileri kesim noktası olarak kullan")
        self.btn_hline_detect.setEnabled(False)
        self.btn_hline_detect.clicked.connect(self._run_hline_detection)
        btn_row.addWidget(self.btn_hline_detect)

        self.btn_detect_settings = QPushButton("  Ayarlar")
        self.btn_detect_settings.setObjectName("secondaryBtn")
        self.btn_detect_settings.setIcon(Icons.get(Icons.SETTINGS))
        self.btn_detect_settings.setIconSize(QSize(13, 13))
        self.btn_detect_settings.clicked.connect(self._open_detect_settings)
        btn_row.addWidget(self.btn_detect_settings)

        btn_row.addStretch()

        self.btn_detect = QPushButton("  Panelleri Tespit Et")
        self.btn_detect.setIcon(Icons.get(Icons.SEARCH, color="#ffffff"))
        self.btn_detect.setIconSize(QSize(14, 14))
        self.btn_detect.setEnabled(False)
        self.btn_detect.clicked.connect(self._run_detection)
        btn_row.addWidget(self.btn_detect)
        detect_layout.addLayout(btn_row)

        root.addWidget(detect_group)

        # İlerleme çubuğu
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("pageSubtitle")
        self.lbl_progress.hide()
        root.addWidget(self.lbl_progress)

        # ── Adım 3: Editör ─────────────────────────────────────────
        editor_group = QGroupBox("Adım 3 — Panel Düzenleyici")
        editor_layout = QVBoxLayout(editor_group)

        # Zoom & undo araç çubuğu
        wtb = QHBoxLayout()
        wtb.setSpacing(6)

        self.btn_zoom_in  = QToolButton()
        self.btn_zoom_in.setText("+")
        self.btn_zoom_in.setToolTip("Yakınlaştır (Ctrl++)")
        self.btn_zoom_in.setFixedSize(32, 32)
        wtb.addWidget(self.btn_zoom_in)

        self.btn_zoom_out = QToolButton()
        self.btn_zoom_out.setText("−")
        self.btn_zoom_out.setToolTip("Uzaklaştır (Ctrl+-)")
        self.btn_zoom_out.setFixedSize(32, 32)
        wtb.addWidget(self.btn_zoom_out)

        self.btn_zoom_fit = QPushButton("  Sığdır")
        self.btn_zoom_fit.setObjectName("secondaryBtn")
        self.btn_zoom_fit.setToolTip("Ekrana sığdır (Ctrl+0)")
        wtb.addWidget(self.btn_zoom_fit)

        sep_w = QFrame(); sep_w.setFrameShape(QFrame.Shape.VLine)
        sep_w.setObjectName("separator"); sep_w.setFixedWidth(1)
        wtb.addWidget(sep_w)

        self.btn_undo = QToolButton()
        self.btn_undo.setText("↩")
        self.btn_undo.setToolTip("Geri al (Ctrl+Z)")
        self.btn_undo.setFixedSize(32, 32)
        wtb.addWidget(self.btn_undo)

        self.btn_redo = QToolButton()
        self.btn_redo.setText("↪")
        self.btn_redo.setToolTip("İleri al (Ctrl+Y)")
        self.btn_redo.setFixedSize(32, 32)
        wtb.addWidget(self.btn_redo)

        wtb.addStretch()

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("pageSubtitle")
        self.lbl_zoom.setFixedWidth(48)
        wtb.addWidget(self.lbl_zoom)

        editor_layout.addLayout(wtb)

        editor_hint = QLabel(
            "🖱 Sürükle → yeni panel  |  Panele tıkla → seç/taşı  |  "
            "Köşe sürükle → boyutlandır  |  Del → sil  |  Space/Orta tuş → kaydır  |  Tekerlek → zoom"
        )
        editor_hint.setObjectName("pageSubtitle")
        editor_hint.setWordWrap(True)
        editor_layout.addWidget(editor_hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.canvas = PanelCanvas()
        self.canvas.panel_selected.connect(self._on_canvas_panel_selected)
        self.canvas.panels_changed.connect(self._on_panels_changed)
        self.canvas.zoom_changed.connect(lambda z: self.lbl_zoom.setText(f"{round(z*100)}%"))
        splitter.addWidget(self.canvas)

        # Zoom butonlarını canvas'a bağla
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        self.btn_zoom_fit.clicked.connect(self.canvas.zoom_fit)
        self.btn_undo.clicked.connect(self.canvas.undo)
        self.btn_redo.clicked.connect(self.canvas.redo)

        self.panel_list_widget = PanelListWidget()
        self.panel_list_widget.panel_selection_changed.connect(self.canvas.select_panel)
        self.panel_list_widget.panel_delete_requested.connect(self.canvas.delete_panel)
        self.panel_list_widget.panel_update_requested.connect(
            lambda idx, box: self.canvas.update_panel(idx, box)
        )
        self.panel_list_widget.panel_add_requested.connect(self._add_full_panel)
        splitter.addWidget(self.panel_list_widget)

        splitter.setSizes([700, 260])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        editor_layout.addWidget(splitter)

        # Kaydet butonu
        save_row = QHBoxLayout()
        save_row.addStretch()
        self.btn_save_panels = QPushButton("  Panelleri Kaydet")
        self.btn_save_panels.setEnabled(False)
        self.btn_save_panels.clicked.connect(self._save_panels)
        save_row.addWidget(self.btn_save_panels)
        editor_layout.addLayout(save_row)

        root.addWidget(editor_group, 1)

    def load_chapter_images(self, image_paths: List[str], output_dir: str) -> None:
        """Bölüm görsellerini ayarlar."""
        self._image_paths = image_paths
        self._output_dir = output_dir
        self._stitched_path = None
        self.canvas.clear()
        self.panel_list_widget.load_panels([])
        self.btn_detect.setEnabled(False)
        self.btn_save_panels.setEnabled(False)

        if image_paths:
            self.btn_stitch.setEnabled(True)
            self.lbl_stitch_info.setText(f"{len(image_paths)} görsel hazır.")
        else:
            self.btn_stitch.setEnabled(False)
            self.lbl_stitch_info.setText("Bölüm görseli bulunamadı.")
            self.lbl_detect_info.setText("Önce görselleri birleştirin.")

    def clear(self) -> None:
        for w in (self._stitch_worker, self._load_worker, self._detect_worker):
            _abort_worker(self, w)
        self._image_paths = []
        self._stitched_path = None
        self.canvas.clear()
        self.panel_list_widget.load_panels([])
        self.btn_stitch.setEnabled(False)
        self.btn_detect.setEnabled(False)
        self.btn_save_panels.setEnabled(False)
        self.lbl_stitch_info.setText("Bölüm görselleri seçilmedi.")
        self.lbl_detect_info.setText("Önce görselleri birleştirin.")

    # ── Stitch ─────────────────────────────────────────────────────

    def _on_stitch_scale_changed(self, _idx: int) -> None:
        self._stitch_scale = self.stitch_scale_combo.currentData() or 100

    def _on_stitch_format_changed(self, _idx: int) -> None:
        fmt = self.stitch_format_combo.currentData() or "png"
        if fmt == "png":
            self._stitch_jpeg_quality = None
        elif fmt == "jpeg85":
            self._stitch_jpeg_quality = 85
        elif fmt == "jpeg70":
            self._stitch_jpeg_quality = 70
        elif fmt == "jpeg50":
            self._stitch_jpeg_quality = 50

    def _run_stitch(self) -> None:
        if not self._image_paths:
            return
        if self._stitch_worker and self._stitch_worker.isRunning():
            return

        from ui.workers.panel_worker import StitchQualityWorker
        ext = "jpg" if self._stitch_jpeg_quality is not None else "png"
        out_path = str(Path(self._output_dir) / f"stitched.{ext}")
        self.progress_bar.show()
        self.lbl_progress.show()
        self.btn_stitch.setEnabled(False)
        self.btn_detect.setEnabled(False)

        self._stitch_worker = StitchQualityWorker(
            image_paths=self._image_paths,
            output_path=out_path,
            scale_percent=self._stitch_scale,
            jpeg_quality=self._stitch_jpeg_quality,
            gap=0,
            parent=self,
        )
        self._stitch_worker.progress.connect(self.lbl_progress.setText)
        self._stitch_worker.finished.connect(self._on_stitch_finished)
        self._stitch_worker.error.connect(self._on_stitch_error)
        _remember_worker(self, self._stitch_worker)
        self._stitch_worker.start()

    def _on_stitch_finished(self, out_path: str) -> None:
        # Stitch bitti; görsel yükleme devam ediyor — bar sıfırla ve güncelle
        self.progress_bar.setRange(0, 0)   # indeterminate devam
        self.lbl_progress.setText("Birleştirilmiş görsel yükleniyor...")
        self.progress_bar.show()
        self.lbl_progress.show()
        self.btn_stitch.setEnabled(True)
        self._stitched_path = out_path
        self.btn_detect.setEnabled(False)

        # Büyük stitched görüntüyü arka planda yükle — UI thread'ini bloke etme
        from ui.workers.panel_worker import ImageLoadWorker
        if self._load_worker and self._load_worker.isRunning():
            _abort_worker(self, self._load_worker)
        self._load_worker = ImageLoadWorker(out_path, parent=self)
        self._load_worker.finished.connect(self._on_stitched_image_loaded)
        self._load_worker.error.connect(self._on_stitch_load_error)
        _remember_worker(self, self._load_worker)
        self._load_worker.start()

    def _on_stitched_image_loaded(self, out_path: str, img) -> None:
        if self.sender() is not self._load_worker:
            return
        self.progress_bar.hide()
        self.lbl_progress.hide()
        self.canvas.set_image(img)
        self.canvas.set_panels([])
        self.panel_list_widget.load_panels([])
        size = self.canvas.image_size()
        if size:
            h, w = size
            self.lbl_stitch_info.setText(f"Birleştirildi: {w}×{h} px  →  {Path(out_path).name}")
        self.lbl_detect_info.setText("Birleştirilmiş görsel hazır. Panel tespitini çalıştırın.")
        self.btn_detect.setEnabled(True)
        self.btn_hline_detect.setEnabled(True)
        self.ctx.app_state.status_message.emit("Görseller birleştirildi.")

    def _on_stitch_load_error(self, msg: str) -> None:
        self.progress_bar.hide()
        self.lbl_progress.hide()
        QMessageBox.critical(self, "Hata", f"Birleştirilmiş görsel yüklenemedi:\n{msg}")

    def _on_stitch_error(self, msg: str) -> None:
        self.progress_bar.hide()
        self.lbl_progress.hide()
        self.btn_stitch.setEnabled(True)
        QMessageBox.critical(self, "Stitch Hatası", msg)

    # ── Panel Tespiti ───────────────────────────────────────────────

    def _open_detect_settings(self) -> None:
        dlg = PanelDetectSettingsDialog(self)
        dlg.spin_min.setValue(self._min_area * 100)
        dlg.spin_max.setValue(self._max_area * 100)
        idx = dlg.engine_combo.findData(getattr(self, "_engine", "yolo"))
        if idx >= 0:
            dlg.engine_combo.setCurrentIndex(idx)
        vidx = dlg.vision_model_combo.findData(getattr(self, "_vision_model", None))
        if vidx >= 0:
            dlg.vision_model_combo.setCurrentIndex(vidx)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._min_area, self._max_area = dlg.get_values()
            self._engine = dlg.get_engine()
            self._vision_model = dlg.get_vision_model()

    def _run_hline_detection(self) -> None:
        """Yatay beyaz/siyah çizgileri panel kesim noktası olarak kullanır."""
        if not self._stitched_path:
            return
        try:
            img = _load_cv2_image(self._stitched_path)
            from core.panel_detector import detect_horizontal_cuts
            h, w = img.shape[:2]
            min_run = max(8, int(h * 0.004))
            cut_ys = detect_horizontal_cuts(
                img, threshold=242, min_consecutive=min_run, dark_threshold=14,
            )
            if not cut_ys:
                QMessageBox.information(
                    self, "Yatay Çizgi Tespiti",
                    "Beyaz/siyah yatay çizgi bulunamadı.\n"
                    "Eşik değerlerini düşürmeyi deneyin."
                )
                return
            pad = max(2, int(h * 0.002))
            boundaries = [0] + cut_ys + [h]
            boxes = []
            for i in range(len(boundaries) - 1):
                y_start = boundaries[i] + (pad if i > 0 else 0)
                y_end = boundaries[i + 1] - (pad if i < len(boundaries) - 2 else 0)
                if y_end - y_start > 40:
                    boxes.append((0, y_start, w, y_end - y_start))
            if boxes:
                self.canvas.set_panels(boxes)
                self.panel_list_widget.load_panels(boxes)
                self.btn_save_panels.setEnabled(True)
                self.lbl_detect_info.setText(
                    f"{len(cut_ys)} kesim noktası → {len(boxes)} segment oluşturuldu."
                )
                self.ctx.app_state.status_message.emit(
                    f"Yatay çizgi tespiti: {len(boxes)} segment."
                )
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Yatay çizgi tespiti başarısız:\n{exc}")

    def _run_detection(self) -> None:
        if not self._stitched_path:
            QMessageBox.information(self, "Bilgi", "Önce görselleri birleştirin.")
            return
        if self._detect_worker and self._detect_worker.isRunning():
            return

        use_yolo, use_vision = _engine_flags(getattr(self, "_engine", "yolo"))
        if use_vision and not _confirm_vision_cost(self, 1, getattr(self, "_vision_model", None)):
            return

        from ui.workers.panel_worker import PanelDetectWorker
        self.progress_bar.show()
        self.lbl_progress.show()
        self.btn_detect.setEnabled(False)
        self.lbl_detect_info.setText("Panel tespiti çalışıyor...")

        self._detect_worker = PanelDetectWorker(
            image_path=self._stitched_path,
            reading_order="ltr",
            min_area_ratio=self._min_area,
            max_area_ratio=self._max_area,
            use_yolo=use_yolo,
            use_vision=use_vision,
            vision_model=getattr(self, "_vision_model", None),
            parent=self,
        )
        self._detect_worker.progress.connect(self.lbl_progress.setText)
        self._detect_worker.finished.connect(self._on_detection_finished)
        self._detect_worker.error.connect(self._on_detection_error)
        _remember_worker(self, self._detect_worker)
        self._detect_worker.start()

    def _on_detection_finished(self, boxes: list, engine: str = "") -> None:
        self.progress_bar.hide()
        self.lbl_progress.hide()
        self.btn_detect.setEnabled(True)
        self.canvas.set_panels(boxes)
        self.panel_list_widget.load_panels(boxes)
        self.btn_save_panels.setEnabled(bool(boxes))
        used = engine or getattr(self, "_engine", "yolo")
        overlaps = _count_overlaps(boxes)
        extra = [f"motor: {used}"]
        if overlaps:
            extra.append(f"{overlaps} örtüşen kutu")
        self.lbl_detect_info.setText(
            f"{len(boxes)} panel tespit edildi ({', '.join(extra)}). Düzenlemeler yapabilirsiniz."
        )
        self.ctx.app_state.status_message.emit(self.lbl_detect_info.text())

    def _on_detection_error(self, msg: str) -> None:
        self.progress_bar.hide()
        self.lbl_progress.hide()
        self.btn_detect.setEnabled(True)
        self.lbl_detect_info.setText(f"Hata: {msg}")
        QMessageBox.critical(self, "Panel Tespiti Hatası", msg)

    # ── Canvas sinyalleri ───────────────────────────────────────────

    def _on_canvas_panel_selected(self, index: int) -> None:
        self.panel_list_widget.select_panel(index)

    def _on_panels_changed(self) -> None:
        panels = self.canvas.get_panels()
        self.panel_list_widget.load_panels(panels, keep_selection=self.canvas.selected_index)
        self.btn_save_panels.setEnabled(bool(panels))

    def _add_full_panel(self) -> None:
        size = self.canvas.image_size()
        if size is None:
            return
        h, w = size
        margin = 10
        self.canvas.add_panel((margin, margin, w - 2 * margin, h - 2 * margin))

    # ── Panelleri Kaydet ────────────────────────────────────────────

    def _save_panels(self) -> None:
        if not self._stitched_path:
            return
        boxes = self.canvas.get_panels()
        if not boxes:
            QMessageBox.information(self, "Bilgi", "Kaydedilecek panel yok.")
            return

        # Önizleme dialogu
        try:
            img = _load_cv2_image(self._stitched_path)
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Görüntü okunamadı:\n{exc}")
            return

        preview_dlg = PanelCropPreviewDialog(img, boxes, parent=self)
        if preview_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected_boxes = preview_dlg.get_selected_boxes()
        if not selected_boxes:
            QMessageBox.information(self, "Bilgi", "Hiç panel seçilmedi.")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Panelleri Kaydet — Klasör Seç")
        if not out_dir:
            return

        try:
            from core.panel_detector import PanelDetector
            panels_img = PanelDetector.crop_panels(img, selected_boxes)
            saved = PanelDetector.save_panels(panels_img, out_dir, prefix="webtoon_panel", ext=".jpg")
            QMessageBox.information(
                self, "Kaydedildi",
                f"{len(saved)} panel kaydedildi:\n{out_dir}"
            )
            self.ctx.app_state.status_message.emit(f"{len(saved)} webtoon paneli kaydedildi.")
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Paneller kaydedilemedi:\n{exc}")
