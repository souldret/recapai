"""
RecapAI - Görseller: canvas, liste, dialog ve thumbnail widget'ları.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QComboBox, QProgressBar,
    QFileDialog, QSplitter, QAbstractItemView, QMenu, QMessageBox,
    QSizePolicy, QSpinBox, QDoubleSpinBox, QGroupBox,
    QStackedWidget, QDialog, QDialogButtonBox, QSlider, QGridLayout,
    QCheckBox, QScrollArea,
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QPoint, QRect, QPointF, QRectF, QTimer
from PyQt6.QtGui import (
    QPixmap, QIcon, QPainter, QPen, QColor, QBrush, QImage,
    QCursor, QWheelEvent, QKeyEvent, QFont,
)

from core.qt_image import numpy_rgb_to_qimage as _numpy_rgb_to_qimage
from ui.utils.icons import Icons

logger = logging.getLogger(__name__)

PanelBox = Tuple[int, int, int, int]   # (x, y, w, h)


def _count_overlaps(boxes) -> int:
    n = 0
    for i, a in enumerate(boxes or []):
        ax, ay, aw, ah = a[:4]
        ar = (ax + aw, ay + ah)
        for b in (boxes or [])[i + 1:]:
            bx, by, bw, bh = b[:4]
            ix = max(0, min(ar[0], bx + bw) - max(ax, bx))
            iy = max(0, min(ar[1], by + bh) - max(ay, by))
            if ix > 4 and iy > 4:
                n += 1
    return n


def _engine_flags(engine: str) -> Tuple[bool, bool]:
    if engine == "opencv":
        return False, False
    if engine == "vision":
        return False, True
    return True, False


def _confirm_vision_cost(parent, pages: int, model: str | None = None) -> bool:
    from core.panel_ai import estimate_vision_cost
    from core.settings_manager import SettingsManager
    if not model:
        model = "google/gemini-2.5-flash"
        try:
            model = SettingsManager.instance().get("panel.vision_model") or SettingsManager.instance().get(
                "defaults.vision_model", model
            )
        except Exception:
            pass
    est = estimate_vision_cost(model, max(1, pages) * 3)
    msg = (
        f"Vision API kullanılacak (uzun şerit dilimlere bölünür).\n\n"
        f"Model: {est['label']}\n"
        f"Kaynak: {pages} görsel\n"
        f"Tahmini maliyet: ${est['usd_low']:.4f} – ${est['usd_high']:.4f} USD\n"
        f"(yaklaşık; dilim sayısı ve token'a göre değişir)\n\n"
        "Devam edilsin mi?"
    )
    return QMessageBox.question(
        parent, "Vision API maliyeti", msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes


# ══════════════════════════════════════════════════════════════════════════════
# Thumbnail Worker
# ══════════════════════════════════════════════════════════════════════════════

class ThumbnailWorker(QThread):
    """Arka planda thumbnail oluşturan iş parçacığı."""

    progress = pyqtSignal(int, int)
    thumbnail_ready = pyqtSignal(int, str)
    finished = pyqtSignal()

    def __init__(self, paths: List[str], output_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._paths = paths
        self._output_dir = Path(output_dir)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from core.image_processor import create_thumbnail
        self._output_dir.mkdir(parents=True, exist_ok=True)
        total = len(self._paths)
        for i, src in enumerate(self._paths):
            if self._cancelled:
                break
            src_path = Path(src)
            thumb_path = self._output_dir / f"{src_path.stem}_thumb.jpg"
            if thumb_path.exists() and thumb_path.stat().st_size > 0:
                self.thumbnail_ready.emit(i, str(thumb_path))
                self.progress.emit(i + 1, total)
                continue
            ok = create_thumbnail(src_path, thumb_path)
            if ok:
                self.thumbnail_ready.emit(i, str(thumb_path))
            self.progress.emit(i + 1, total)
        self.finished.emit()


# ══════════════════════════════════════════════════════════════════════════════
# Panel Editör Canvas (QLabel tabanlı — panelleri gösterir ve düzenlemesine
# olanak tanır)
# ══════════════════════════════════════════════════════════════════════════════

# ── Panel canvas sabitleri ─────────────────────────────────────────────────────
_HANDLE_SIZE   = 8           # piksel (ekran koordinatı)
_COL_NORMAL    = QColor("#00dc82")
_COL_SELECTED  = QColor("#4299e1")
_COL_HOVER     = QColor("#f6ad55")
_COL_DRAW      = QColor("#e53e3e")

_CURSOR_MAP = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n":  Qt.CursorShape.SizeVerCursor,   "s":  Qt.CursorShape.SizeVerCursor,
    "w":  Qt.CursorShape.SizeHorCursor,   "e":  Qt.CursorShape.SizeHorCursor,
}


def _handle_rects_for(px: float, py: float, pw: float, ph: float,
                      zoom: float) -> dict:
    """8 yeniden boyutlandırma tutamacının ekran-koordinat dikdörtgenlerini döndürür."""
    hs = _HANDLE_SIZE
    cx = px + pw / 2;  cy = py + ph / 2
    rx = px + pw;      by = py + ph
    pts = {
        "nw": (px, py), "n": (cx, py), "ne": (rx, py),
        "w":  (px, cy),                 "e":  (rx, cy),
        "sw": (px, by), "s": (cx, by), "se": (rx, by),
    }
    return {k: QRectF(sx - hs/2, sy - hs/2, hs, hs)
            for k, (sx, sy) in pts.items()}


class PanelCanvas(QWidget):
    """
    Tam etkileşimli panel düzenleme canvas'ı.

    Özellikler:
      • Tekerlek / Ctrl+±  → yakınlaştır / uzaklaştır
      • Space/Orta tuş + sürükle → kaydır
      • Boş alana sürükle → yeni panel çiz
      • Panele tıkla → seç / taşı
      • Seçili panelin 8 tutamacını sürükle → yeniden boyutlandır
      • Del / Backspace → seçili paneli sil
      • Ctrl+Z / Ctrl+Y → geri al / ileri al

    Sinyaller:
        panel_selected(int)  — seçilen panel indeksi (-1 = yok)
        panels_changed()     — panel listesi değişti
        zoom_changed(float)  — zoom faktörü değişti
    """

    panel_selected = pyqtSignal(int)
    panels_changed = pyqtSignal()
    zoom_changed   = pyqtSignal(float)
    reading_order_changed = pyqtSignal(str)   # "rtl" veya "ltr"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Görüntü verisi
        self._orig_image: Optional[np.ndarray] = None
        self._pixmap: Optional[QPixmap] = None
        self._orig_w = 0
        self._orig_h = 0

        # Panel verisi (görüntü koordinatı — float)
        self._panels: List[List[float]] = []   # [[x,y,w,h], ...]
        self._selected: int = -1
        self._hovered: int = -1

        # Zoom / pan
        self._zoom  = 1.0
        self._pan   = QPointF(0.0, 0.0)
        self._has_user_zoom = False

        # Çizim durumu
        self._drawing      = False
        self._draw_start   = QPointF()
        self._draw_current = QPointF()

        # Sürükleme durumu
        self._drag_idx    = -1
        self._drag_offset = QPointF()

        # Yeniden boyutlandırma durumu
        self._resize_idx    = -1
        self._resize_handle: Optional[str] = None
        self._resize_orig:   Optional[List[float]] = None
        self._resize_start  = QPointF()

        # Pan durumu
        self._panning          = False
        self._pan_start        = QPointF()
        self._pan_start_offset = QPointF()
        self._space_pressed    = False

        # Geçmiş (geri al/ileri al)
        self._history: list = []
        self._history_idx = -1

        # Okuma düzeni ve sıralama göstergesi
        self._reading_order: str = "rtl"
        self._show_order_arrows: bool = True

        # Çoklu seçim (birleştirme için)
        self._multi_selected: List[int] = []

    # ── Genel Yardımcılar ──────────────────────────────────────────

    def _to_image(self, sp: QPointF) -> QPointF:
        """Ekran noktasını → görüntü koordinatına çevirir."""
        return QPointF(
            (sp.x() - self._pan.x()) / self._zoom,
            (sp.y() - self._pan.y()) / self._zoom,
        )

    def _to_screen(self, ip: QPointF) -> QPointF:
        return QPointF(
            ip.x() * self._zoom + self._pan.x(),
            ip.y() * self._zoom + self._pan.y(),
        )

    def _fit_view(self) -> None:
        if not self._pixmap:
            return
        sw = max(1, self.width());  sh = max(1, self.height())
        scale = min(sw / self._orig_w, sh / self._orig_h)
        self._zoom = scale
        self._pan  = QPointF(
            (sw - self._orig_w * scale) / 2,
            (sh - self._orig_h * scale) / 2,
        )

    def _clamp_box(self, box: List[float]) -> None:
        box[0] = max(0.0, min(box[0], self._orig_w - 1))
        box[1] = max(0.0, min(box[1], self._orig_h - 1))
        box[2] = max(5.0, min(box[2], self._orig_w - box[0]))
        box[3] = max(5.0, min(box[3], self._orig_h - box[1]))

    def _hit_panel(self, img_pt: QPointF) -> int:
        for i, (x, y, w, h) in reversed(list(enumerate(self._panels))):
            if x <= img_pt.x() <= x + w and y <= img_pt.y() <= y + h:
                return i
        return -1

    def _hit_handle(self, idx: int, screen_pt: QPointF) -> Optional[str]:
        if idx < 0 or idx >= len(self._panels):
            return None
        x, y, w, h = self._panels[idx]
        sx = x * self._zoom + self._pan.x()
        sy = y * self._zoom + self._pan.y()
        sw = w * self._zoom;  sh = h * self._zoom
        for k, r in _handle_rects_for(sx, sy, sw, sh, self._zoom).items():
            if r.contains(screen_pt):
                return k
        return None

    # ── Geri Al / İleri Al ─────────────────────────────────────────

    def _push_history(self) -> None:
        import copy
        state = copy.deepcopy(self._panels)
        self._history = self._history[:self._history_idx + 1]
        self._history.append(state)
        if len(self._history) > 50:
            self._history.pop(0)
        else:
            self._history_idx += 1

    def undo(self) -> None:
        if self._history_idx > 0:
            import copy
            self._history_idx -= 1
            self._panels = copy.deepcopy(self._history[self._history_idx])
            self._selected = -1
            self.update()
            self.panels_changed.emit()
            self.panel_selected.emit(-1)

    def redo(self) -> None:
        if self._history_idx < len(self._history) - 1:
            import copy
            self._history_idx += 1
            self._panels = copy.deepcopy(self._history[self._history_idx])
            self._selected = -1
            self.update()
            self.panels_changed.emit()
            self.panel_selected.emit(-1)

    # ── Zoom ───────────────────────────────────────────────────────

    def zoom_in(self) -> None:
        self._zoom = min(10.0, self._zoom * 1.25)
        self._has_user_zoom = True
        self.update()
        self.zoom_changed.emit(self._zoom)

    def zoom_out(self) -> None:
        self._zoom = max(0.05, self._zoom / 1.25)
        self._has_user_zoom = True
        self.update()
        self.zoom_changed.emit(self._zoom)

    def zoom_fit(self) -> None:
        self._has_user_zoom = False
        self._fit_view()
        self.update()
        self.zoom_changed.emit(self._zoom)

    # ── Okuma Düzeni ───────────────────────────────────────────────

    def set_reading_order(self, order: str) -> None:
        """'rtl' veya 'ltr' okuma düzeni ayarlar."""
        self._reading_order = order
        self.update()
        self.reading_order_changed.emit(order)

    def set_show_order_arrows(self, show: bool) -> None:
        self._show_order_arrows = show
        self.update()

    # ── Panel Birleştirme ──────────────────────────────────────────

    def toggle_multi_select(self, index: int) -> None:
        """Ctrl+tıklama için çoklu seçime panel ekler/çıkarır."""
        if index in self._multi_selected:
            self._multi_selected.remove(index)
        else:
            self._multi_selected.append(index)
        self.update()

    def merge_selected_panels(self) -> bool:
        """
        _multi_selected içindeki panelleri bounding box union'ına birleştirir.
        En az 2 panel seçili olmalıdır. Başarı durumunda True döner.
        """
        sel = sorted(self._multi_selected)
        if len(sel) < 2:
            return False

        boxes_to_merge = [self._panels[i] for i in sel if i < len(self._panels)]
        if len(boxes_to_merge) < 2:
            return False

        # Bounding box union
        min_x = min(b[0] for b in boxes_to_merge)
        min_y = min(b[1] for b in boxes_to_merge)
        max_x = max(b[0] + b[2] for b in boxes_to_merge)
        max_y = max(b[1] + b[3] for b in boxes_to_merge)
        merged = [float(min_x), float(min_y), float(max_x - min_x), float(max_y - min_y)]

        # Yüksek indisten silerek panel listesini güncelle
        for i in reversed(sel):
            if i < len(self._panels):
                self._panels.pop(i)
        self._panels.insert(sel[0], merged)

        self._multi_selected = []
        self._selected = sel[0]
        self._push_history()
        self.update()
        self.panels_changed.emit()
        self.panel_selected.emit(sel[0])
        return True

    def clear_multi_selection(self) -> None:
        self._multi_selected = []
        self.update()

    # ── Genel API (mevcut arayüzle uyumlu) ─────────────────────────

    def image_size(self) -> Optional[Tuple[int, int]]:
        if self._orig_image is None:
            return None
        return self._orig_h, self._orig_w

    @property
    def selected_index(self) -> int:
        return self._selected

    def set_image(self, image: np.ndarray) -> None:
        self._orig_image = image
        self._orig_h, self._orig_w = image.shape[:2]
        # numpy BGR → QPixmap (bir kez dönüştür, zoom ile tekrar kullan)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self._pixmap = QPixmap.fromImage(_numpy_rgb_to_qimage(rgb))
        self._panels = []
        self._selected = -1
        self._hovered = -1
        self._history.clear(); self._history_idx = -1
        self._push_history()
        self._has_user_zoom = False
        self._fit_view()
        self.update()

    def set_panels(self, panels: List[PanelBox]) -> None:
        self._panels = [[float(x), float(y), float(w), float(h)]
                        for x, y, w, h in panels]
        self._selected = -1
        self._push_history()
        self.update()
        self.panels_changed.emit()

    def get_panels(self) -> List[PanelBox]:
        return [(int(x), int(y), int(w), int(h))
                for x, y, w, h in self._panels]

    def select_panel(self, index: int) -> None:
        self._selected = index
        self.update()
        self.panel_selected.emit(index)

    def delete_panel(self, index: int) -> None:
        if 0 <= index < len(self._panels):
            self._panels.pop(index)
            self._selected = -1
            self._push_history()
            self.update()
            self.panels_changed.emit()
            self.panel_selected.emit(-1)

    def update_panel(self, index: int, box: PanelBox) -> None:
        if 0 <= index < len(self._panels):
            self._panels[index] = [float(v) for v in box]
            self._push_history()
            self.update()
            self.panels_changed.emit()

    def add_panel(self, box: PanelBox) -> None:
        self._panels.append([float(v) for v in box])
        self._selected = len(self._panels) - 1
        self._push_history()
        self.update()
        self.panels_changed.emit()
        self.panel_selected.emit(self._selected)

    def clear(self) -> None:
        self._orig_image = None
        self._pixmap = None
        self._panels = []
        self._selected = -1
        self._hovered = -1
        self._history.clear()
        self._history_idx = -1
        self.update()

    def clear_pixmap(self) -> None:
        self.clear()

    # ── Paint ──────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#1a1b26"))

        if self._pixmap is None:
            p.setPen(QColor("#565f89"))
            p.setFont(QFont("Segoe UI", 14))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Görüntü yüklenmedi\n\nSol panel'den bir sayfa seçin")
            p.end()
            return

        # Görüntüyü zoom+pan ile çiz
        p.save()
        p.translate(self._pan)
        p.scale(self._zoom, self._zoom)
        p.drawPixmap(0, 0, self._pixmap)

        # Panelleri görüntü koordinatında çiz (zoom otomatik uygulanır)
        _COL_MULTI = QColor("#c084fc")   # çoklu seçim rengi
        for i, (x, y, w, h) in enumerate(self._panels):
            is_sel   = (i == self._selected)
            is_hov   = (i == self._hovered)
            is_multi = (i in self._multi_selected)
            color = (_COL_MULTI    if is_multi else
                     _COL_SELECTED if is_sel   else
                     _COL_HOVER    if is_hov   else
                     _COL_NORMAL)

            fill = QColor(color)
            fill.setAlpha(40 if is_multi else 35)
            p.fillRect(QRectF(x, y, w, h), fill)

            pen_w = 2.5 / self._zoom if is_multi else 2.0 / self._zoom
            pen = QPen(color, pen_w)
            if is_multi:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x, y, w, h))

            # Etiket — sıra numarası
            font_sz = max(1, int(10 / self._zoom))
            p.setFont(QFont("Consolas", font_sz, QFont.Weight.Bold))
            lbl = f" {i + 1} "
            fm  = p.fontMetrics()
            lw  = fm.horizontalAdvance(lbl)
            lh  = fm.height()
            pill = QRectF(x + 2/self._zoom, y + 2/self._zoom, lw, lh)
            bg   = QColor(color); bg.setAlpha(210)
            p.setBrush(bg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(pill, 2/self._zoom, 2/self._zoom)
            p.setPen(QColor("#ffffff"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, lbl)

        # Okuma sırası okları (panel merkezleri arası)
        if self._show_order_arrows and len(self._panels) >= 2:
            arrow_color = QColor("#e0af68")
            arrow_color.setAlpha(180)
            p.setPen(QPen(arrow_color, 1.5 / self._zoom))
            centers = [
                QPointF(b[0] + b[2] / 2, b[1] + b[3] / 2)
                for b in self._panels
            ]
            for j in range(len(centers) - 1):
                c1, c2 = centers[j], centers[j + 1]
                p.drawLine(c1, c2)
                # Küçük ok başı
                dx = c2.x() - c1.x()
                dy = c2.y() - c1.y()
                import math
                dist = math.sqrt(dx*dx + dy*dy)
                if dist > 0:
                    ux, uy = dx / dist, dy / dist
                    tip_len = 12 / self._zoom
                    tip_ang = 0.4
                    tx = c2.x() - ux * tip_len
                    ty = c2.y() - uy * tip_len
                    cos_a, sin_a = math.cos(tip_ang), math.sin(tip_ang)
                    p.drawLine(c2, QPointF(
                        tx + cos_a * (ux * tip_len) - sin_a * (uy * tip_len),
                        ty + sin_a * (ux * tip_len) + cos_a * (uy * tip_len),
                    ))
                    cos_a2, sin_a2 = math.cos(-tip_ang), math.sin(-tip_ang)
                    p.drawLine(c2, QPointF(
                        tx + cos_a2 * (ux * tip_len) - sin_a2 * (uy * tip_len),
                        ty + sin_a2 * (ux * tip_len) + cos_a2 * (uy * tip_len),
                    ))

        p.restore()

        # Seçili panelin tutamaçları (ekran koordinatında)
        if self._selected >= 0 and self._selected < len(self._panels):
            x, y, w, h = self._panels[self._selected]
            sx = x * self._zoom + self._pan.x()
            sy = y * self._zoom + self._pan.y()
            sw = w * self._zoom;  sh = h * self._zoom
            for hr in _handle_rects_for(sx, sy, sw, sh, self._zoom).values():
                p.fillRect(hr, QColor("#ffffff"))
                p.setPen(QPen(_COL_SELECTED, 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(hr)

        # Çizilen yeni dikdörtgen (canlı)
        if self._drawing:
            rx = min(self._draw_start.x(), self._draw_current.x())
            ry = min(self._draw_start.y(), self._draw_current.y())
            rw = abs(self._draw_current.x() - self._draw_start.x())
            rh = abs(self._draw_current.y() - self._draw_start.y())
            # Ekran koordinatında çiz
            sx = rx * self._zoom + self._pan.x()
            sy = ry * self._zoom + self._pan.y()
            sw = rw * self._zoom
            sh = rh * self._zoom
            pen = QPen(_COL_DRAW, 2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            fill = QColor(_COL_DRAW); fill.setAlpha(25)
            p.setBrush(fill)
            p.drawRect(QRectF(sx, sy, sw, sh))

        p.end()

    # ── Mouse ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if self._pixmap is None:
            return
        pos = event.position()

        # Kaydırma modu: Orta tuş veya Space+Sol veya Alt+Sol
        if (event.button() == Qt.MouseButton.MiddleButton or
                (event.button() == Qt.MouseButton.LeftButton and
                 (self._space_pressed or
                  event.modifiers() & Qt.KeyboardModifier.AltModifier))):
            self._panning          = True
            self._pan_start        = pos
            self._pan_start_offset = QPointF(self._pan)
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return

        if event.button() == Qt.MouseButton.LeftButton:
            img_pt = self._to_image(pos)
            ctrl_held = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

            # Ctrl + tıklama → çoklu seçim (birleştirme için)
            if ctrl_held:
                hit = self._hit_panel(img_pt)
                if hit >= 0:
                    self.toggle_multi_select(hit)
                    self._selected = hit
                    self.panel_selected.emit(hit)
                return

            # Çoklu seçimi normal tıklamada temizle
            if self._multi_selected:
                self._multi_selected = []
                self.update()

            # Tutamaç kontrolü (seçili panel varsa)
            if self._selected >= 0:
                h = self._hit_handle(self._selected, pos)
                if h:
                    import copy
                    self._resize_idx    = self._selected
                    self._resize_handle = h
                    self._resize_orig   = copy.copy(self._panels[self._selected])
                    self._resize_start  = img_pt
                    return

            # Panel çarpışma kontrolü
            hit = self._hit_panel(img_pt)
            if hit >= 0:
                self._selected    = hit
                self._drag_idx    = hit
                self._drag_offset = QPointF(
                    img_pt.x() - self._panels[hit][0],
                    img_pt.y() - self._panels[hit][1],
                )
                self.update()
                self.panel_selected.emit(hit)
                return

            # Yeni panel çiz
            self._selected  = -1
            self._drawing   = True
            self._draw_start   = img_pt
            self._draw_current = img_pt
            self.update()
            self.panel_selected.emit(-1)

    def mouseMoveEvent(self, event) -> None:
        if self._pixmap is None:
            return
        pos    = event.position()
        img_pt = self._to_image(pos)

        if self._panning:
            self._pan = self._pan_start_offset + (pos - self._pan_start)
            self.update()
            return

        if self._resize_idx >= 0 and self._resize_handle and self._resize_orig:
            orig = self._resize_orig
            dx   = img_pt.x() - self._resize_start.x()
            dy   = img_pt.y() - self._resize_start.y()
            box  = self._panels[self._resize_idx]
            hk   = self._resize_handle
            nx, ny, nw, nh = orig[0], orig[1], orig[2], orig[3]
            if "e" in hk: nw = max(5.0, orig[2] + dx)
            if "s" in hk: nh = max(5.0, orig[3] + dy)
            if "w" in hk:
                dc = min(dx, orig[2] - 5.0)
                nx = orig[0] + dc; nw = orig[2] - dc
            if "n" in hk:
                dc = min(dy, orig[3] - 5.0)
                ny = orig[1] + dc; nh = orig[3] - dc
            box[0], box[1], box[2], box[3] = nx, ny, nw, nh
            self._clamp_box(box)
            self.update()
            self.panels_changed.emit()
            return

        if self._drag_idx >= 0:
            box    = self._panels[self._drag_idx]
            box[0] = max(0.0, min(img_pt.x() - self._drag_offset.x(),
                                  self._orig_w - box[2]))
            box[1] = max(0.0, min(img_pt.y() - self._drag_offset.y(),
                                  self._orig_h - box[3]))
            self.update()
            self.panels_changed.emit()
            return

        if self._drawing:
            self._draw_current = img_pt
            self.update()
            return

        # Hover + imleç güncellemesi
        self._hovered = self._hit_panel(img_pt)
        cursor = Qt.CursorShape.CrossCursor
        if self._selected >= 0:
            hk = self._hit_handle(self._selected, pos)
            if hk:
                cursor = _CURSOR_MAP.get(hk, Qt.CursorShape.CrossCursor)
            elif self._hovered == self._selected:
                cursor = Qt.CursorShape.SizeAllCursor
        elif self._hovered >= 0:
            cursor = Qt.CursorShape.SizeAllCursor
        self.setCursor(QCursor(cursor))
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        changed = False

        if self._drawing:
            rx = min(self._draw_start.x(), self._draw_current.x())
            ry = min(self._draw_start.y(), self._draw_current.y())
            rw = abs(self._draw_current.x() - self._draw_start.x())
            rh = abs(self._draw_current.y() - self._draw_start.y())
            if rw > 5 and rh > 5:
                box = [rx, ry, rw, rh]
                self._clamp_box(box)
                self._panels.append(box)
                self._selected = len(self._panels) - 1
                changed = True
                self.panel_selected.emit(self._selected)
            self._drawing = False

        if self._drag_idx >= 0 or self._resize_idx >= 0:
            changed = True

        if changed:
            self._push_history()
            self.panels_changed.emit()

        self._drag_idx      = -1
        self._resize_idx    = -1
        self._resize_handle = None
        self._resize_orig   = None
        self._panning       = False
        if not self._space_pressed:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap is None:
            return
        pos    = event.position()
        factor = 1.13 if event.angleDelta().y() > 0 else 1 / 1.13
        nz     = max(0.05, min(self._zoom * factor, 10.0))
        ratio  = nz / self._zoom
        self._pan = QPointF(
            pos.x() - (pos.x() - self._pan.x()) * ratio,
            pos.y() - (pos.y() - self._pan.y()) * ratio,
        )
        self._zoom = nz
        self._has_user_zoom = True
        self.update()
        self.zoom_changed.emit(self._zoom)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected >= 0:
                self.delete_panel(self._selected)
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            if not self._drawing and self._drag_idx < 0:
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            return
        if event.key() == Qt.Key.Key_Z and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.redo()
            else:
                self.undo()
            return
        if event.key() == Qt.Key.Key_Y and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.redo()
            return
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_in()
            return
        if event.key() == Qt.Key.Key_Minus and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_out()
            return
        if event.key() == Qt.Key.Key_0 and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_fit()
            return
        if event.key() == Qt.Key.Key_M and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.merge_selected_panels()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.clear_multi_selection()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            if not self._panning:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            return
        super().keyReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        if self._pixmap and not self._has_user_zoom:
            self._fit_view()
        super().resizeEvent(event)


# ══════════════════════════════════════════════════════════════════════════════
# Panel Listeği (yan panel)
# ══════════════════════════════════════════════════════════════════════════════

class PanelListWidget(QFrame):
    """
    Tespit edilen panellerin listesini gösterir; seçme/silme/koordinat düzenleme
    işlemlerini sağlar.
    """

    panel_selection_changed = pyqtSignal(int)  # index veya -1
    panel_delete_requested = pyqtSignal(int)
    panel_update_requested = pyqtSignal(int, tuple)   # (index, (x,y,w,h))
    panel_add_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.setMinimumWidth(220)
        self.setMaximumWidth(300)
        self._panels: List[PanelBox] = []
        self._selected = -1
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("Paneller")
        title.setObjectName("headingLabel")
        root.addWidget(title)

        self.panel_list = QListWidget()
        self.panel_list.setObjectName("chapterList")
        self.panel_list.currentRowChanged.connect(self._on_row_changed)
        root.addWidget(self.panel_list, 1)

        # Koordinat düzenleme
        coord_group = QGroupBox("Seçili Panel Koordinatları")
        coord_layout = QGridLayout(coord_group)
        coord_layout.setSpacing(4)

        coord_layout.addWidget(QLabel("X:"), 0, 0)
        self.spin_x = QSpinBox()
        self.spin_x.setRange(0, 99999)
        coord_layout.addWidget(self.spin_x, 0, 1)

        coord_layout.addWidget(QLabel("Y:"), 1, 0)
        self.spin_y = QSpinBox()
        self.spin_y.setRange(0, 99999)
        coord_layout.addWidget(self.spin_y, 1, 1)

        coord_layout.addWidget(QLabel("G (w):"), 2, 0)
        self.spin_w = QSpinBox()
        self.spin_w.setRange(1, 99999)
        coord_layout.addWidget(self.spin_w, 2, 1)

        coord_layout.addWidget(QLabel("Y (h):"), 3, 0)
        self.spin_h = QSpinBox()
        self.spin_h.setRange(1, 99999)
        coord_layout.addWidget(self.spin_h, 3, 1)

        btn_apply = QPushButton("Uygula")
        btn_apply.setObjectName("secondaryBtn")
        btn_apply.clicked.connect(self._apply_coords)
        coord_layout.addWidget(btn_apply, 4, 0, 1, 2)

        root.addWidget(coord_group)

        # Aksiyon butonları
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.btn_add = QPushButton("+ Ekle")
        self.btn_add.setObjectName("secondaryBtn")
        self.btn_add.setToolTip("Yeni panel ekle (tam görüntü alanı)")
        self.btn_add.clicked.connect(lambda: self.panel_add_requested.emit())
        btn_row.addWidget(self.btn_add)

        self.btn_del = QPushButton("Sil")
        self.btn_del.setObjectName("secondaryBtn")
        self.btn_del.setEnabled(False)
        self.btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(self.btn_del)

        root.addLayout(btn_row)

        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("pageSubtitle")
        self.lbl_info.setWordWrap(True)
        root.addWidget(self.lbl_info)

    def load_panels(self, panels: List[PanelBox], keep_selection: int = -1) -> None:
        """
        Panel listesini yeniler.

        Args:
            panels: Yeni panel listesi.
            keep_selection: Yüklemeden sonra seçili kalacak indeks; -1 ise seçim sıfırlanır.
        """
        self._panels = list(panels)
        self.panel_list.blockSignals(True)
        self.panel_list.clear()
        for i, (x, y, w, h) in enumerate(panels):
            self.panel_list.addItem(f"Panel {i+1}  —  {w}×{h} @ ({x},{y})")
        self.panel_list.blockSignals(False)
        self.lbl_info.setText(f"{len(panels)} panel")

        # Seçimi geri yükle
        valid_sel = keep_selection if 0 <= keep_selection < len(panels) else -1
        self._selected = valid_sel
        if valid_sel >= 0:
            self.panel_list.blockSignals(True)
            self.panel_list.setCurrentRow(valid_sel)
            self.panel_list.blockSignals(False)
            x, y, w, h = panels[valid_sel]
            self.spin_x.setValue(x)
            self.spin_y.setValue(y)
            self.spin_w.setValue(w)
            self.spin_h.setValue(h)
            self.btn_del.setEnabled(True)
        else:
            self.panel_list.clearSelection()
            self._clear_spinboxes()
            self.btn_del.setEnabled(False)

    def select_panel(self, index: int) -> None:
        self._selected = index
        if 0 <= index < self.panel_list.count():
            self.panel_list.blockSignals(True)
            self.panel_list.setCurrentRow(index)
            self.panel_list.blockSignals(False)
            x, y, w, h = self._panels[index]
            self.spin_x.setValue(x)
            self.spin_y.setValue(y)
            self.spin_w.setValue(w)
            self.spin_h.setValue(h)
            self.btn_del.setEnabled(True)
        else:
            self.panel_list.clearSelection()
            self._clear_spinboxes()
            self.btn_del.setEnabled(False)

    def _on_row_changed(self, row: int) -> None:
        self._selected = row
        if 0 <= row < len(self._panels):
            x, y, w, h = self._panels[row]
            self.spin_x.setValue(x)
            self.spin_y.setValue(y)
            self.spin_w.setValue(w)
            self.spin_h.setValue(h)
            self.btn_del.setEnabled(True)
        else:
            self._clear_spinboxes()
            self.btn_del.setEnabled(False)
        self.panel_selection_changed.emit(row)

    def _apply_coords(self) -> None:
        if self._selected < 0:
            return
        box = (self.spin_x.value(), self.spin_y.value(),
               self.spin_w.value(), self.spin_h.value())
        self._panels[self._selected] = box
        self.panel_list.item(self._selected).setText(
            f"Panel {self._selected+1}  —  {box[2]}×{box[3]} @ ({box[0]},{box[1]})"
        )
        self.panel_update_requested.emit(self._selected, box)

    def _on_delete(self) -> None:
        if self._selected >= 0:
            self.panel_delete_requested.emit(self._selected)

    def _clear_spinboxes(self) -> None:
        for sp in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            sp.setValue(0)


# ══════════════════════════════════════════════════════════════════════════════
# Panel Ayarları Dialog
# ══════════════════════════════════════════════════════════════════════════════

class PanelDetectSettingsDialog(QDialog):
    """Panel tespiti parametrelerini ayarlamak için dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Panel Tespit Ayarları")
        self.setFixedSize(460, 420)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("Min Alan Oranı (%):"), 0, 0)
        self.spin_min = QDoubleSpinBox()
        self.spin_min.setRange(0.1, 20.0)
        self.spin_min.setSingleStep(0.5)
        self.spin_min.setValue(2.0)
        self.spin_min.setSuffix(" %")
        grid.addWidget(self.spin_min, 0, 1)

        grid.addWidget(QLabel("Max Alan Oranı (%):"), 1, 0)
        self.spin_max = QDoubleSpinBox()
        self.spin_max.setRange(20.0, 99.0)
        self.spin_max.setSingleStep(5.0)
        self.spin_max.setValue(80.0)
        self.spin_max.setSuffix(" %")
        grid.addWidget(self.spin_max, 1, 1)

        grid.addWidget(QLabel("Motor:"), 2, 0)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("YOLO + OpenCV (önerilen)", "yolo")
        self.engine_combo.addItem("Sadece OpenCV", "opencv")
        self.engine_combo.addItem("Vision API (ücretli)", "vision")
        grid.addWidget(self.engine_combo, 2, 1)

        grid.addWidget(QLabel("Vision model:"), 3, 0)
        self.vision_model_combo = QComboBox()
        try:
            from core.model_catalog import get_models, format_model_label
            for m in get_models("vision_models"):
                self.vision_model_combo.addItem(format_model_label(m), m.get("id"))
        except Exception:
            self.vision_model_combo.addItem("Gemini 2.5 Flash", "google/gemini-2.5-flash")
            self.vision_model_combo.addItem("GPT-4o Mini", "openai/gpt-4o-mini")
        if self.vision_model_combo.count() == 0:
            self.vision_model_combo.addItem("Gemini 2.5 Flash", "google/gemini-2.5-flash")
        grid.addWidget(self.vision_model_combo, 3, 1)

        layout.addLayout(grid)

        from core.panel_ai import yolo_status
        self.lbl_yolo = QLabel(f"YOLO: {yolo_status()}")
        self.lbl_yolo.setObjectName("pageSubtitle")
        self.lbl_yolo.setWordWrap(True)
        layout.addWidget(self.lbl_yolo)

        hint = QLabel(
            "YOLO ilk seferde ~15MB model indirir (ücretsiz, yerel).\n"
            "Vision API OpenRouter kotanızı kullanır; çalıştırmadan önce maliyet gösterilir."
        )
        hint.setObjectName("pageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self) -> Tuple[float, float]:
        return self.spin_min.value() / 100.0, self.spin_max.value() / 100.0

    def get_engine(self) -> str:
        return self.engine_combo.currentData() or "yolo"

    def get_vision_model(self) -> str:
        return self.vision_model_combo.currentData() or "google/gemini-2.5-flash"


# ══════════════════════════════════════════════════════════════════════════════
# Manga Görsel Grid Öğesi
# ══════════════════════════════════════════════════════════════════════════════

class ImageGridItem(QListWidgetItem):
    def __init__(self, path: str, index: int) -> None:
        super().__init__()
        self.image_path = path
        self.image_index = index
        self.filename = Path(path).name
        self.setText(f"{index + 1}\n{self.filename[:18]}{'…' if len(self.filename) > 18 else ''}")
        self.setSizeHint(QSize(210, 310))
        self.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self.setIcon(QIcon())


# ══════════════════════════════════════════════════════════════════════════════
# Önizleme Paneli
# ══════════════════════════════════════════════════════════════════════════════

class ImagePreviewPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.setMinimumWidth(220)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        lbl_title = QLabel("Önizleme")
        lbl_title.setObjectName("cardSubtitle")
        layout.addWidget(lbl_title)

        self.preview_img = QLabel()
        self.preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_img.setMinimumHeight(280)
        self.preview_img.setText("Görsel\nSeçilmedi")
        layout.addWidget(self.preview_img, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("separator")
        layout.addWidget(sep)

        self.lbl_order = QLabel("Sıra: —")
        self.lbl_order.setObjectName("pageSubtitle")
        layout.addWidget(self.lbl_order)

        self.lbl_name = QLabel("Dosya: —")
        self.lbl_name.setObjectName("pageSubtitle")
        self.lbl_name.setWordWrap(True)
        layout.addWidget(self.lbl_name)

        self.lbl_dim = QLabel("Boyut: —")
        self.lbl_dim.setObjectName("pageSubtitle")
        layout.addWidget(self.lbl_dim)

        self.lbl_size = QLabel("Dosya: —")
        self.lbl_size.setObjectName("pageSubtitle")
        layout.addWidget(self.lbl_size)

        layout.addStretch()

    def show_image(self, path: str, order: int) -> None:
        from core.image_processor import get_image_info
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.preview_img.width() - 8,
                self.preview_img.height() - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_img.setPixmap(scaled)
        else:
            self.preview_img.setText("Yüklenemedi")
        info = get_image_info(path)
        self.lbl_order.setText(f"Sıra: {order + 1}")
        self.lbl_name.setText(f"Dosya: {info['filename']}")
        self.lbl_dim.setText(f"Çözünürlük: {info['width']} × {info['height']}")
        self.lbl_size.setText(f"Boyut: {info['size_kb']} KB  ({info['format']})")

    def clear(self) -> None:
        self.preview_img.setPixmap(QPixmap())
        self.preview_img.setText("Görsel\nSeçilmedi")
        self.lbl_order.setText("Sıra: —")
        self.lbl_name.setText("Dosya: —")
        self.lbl_dim.setText("Boyut: —")
        self.lbl_size.setText("Dosya: —")


def _load_cv2_image(path: str) -> np.ndarray:
    """
    Görüntüyü OpenCV BGR formatında yükler.
    Pillow kullanır — cv2.imread'in ~65535 px yükseklik sınırı yoktur.
    PNG, JPEG, WebP ve tüm Pillow destekli formatları kapsar.
    """
    from PIL import Image as PILImage
    import warnings
    PILImage.MAX_IMAGE_PIXELS = 178_956_970 * 2
    warnings.filterwarnings("ignore", category=PILImage.DecompressionBombWarning)
    try:
        pil_img = PILImage.open(Path(path)).convert("RGB")
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        pil_img.close()
        return img
    except Exception as exc:
        raise ValueError(f"Görüntü okunamadı: {path} — {exc}") from exc

class PanelThumbnailStrip(QWidget):
    """
    Canvas'ın altında yatay kaydırılabilir küçük panel thumbnailları gösterir.
    Bir panele tıklanınca canvas o panele zoom yapar.

    Sinyaller:
        panel_clicked(index)  — kullanıcı bir panele tıkladı
    """

    panel_clicked = pyqtSignal(int)

    _THUMB_W = 100
    _THUMB_H = 72
    _SPACING = 4
    _BORDER  = QColor("#3a3b42")
    _SEL_COL = QColor("#4299e1")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self._THUMB_H + 24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._thumbnails: List[QPixmap] = []
        self._selected = -1
        self._scroll_x = 0
        self._drag_start: Optional[QPointF] = None
        self._drag_scroll = 0
        self.setMouseTracking(True)
        self._hover = -1

    def load_panels(self, image: "Optional[np.ndarray]", boxes: "List[PanelBox]") -> None:
        """Görüntüden panel thumbnaillarını oluşturur."""
        self._thumbnails = []
        self._selected = -1
        self._scroll_x = 0
        if image is None or not boxes:
            self.update()
            return

        for x, y, w, h in boxes:
            try:
                crop = image[y: y + h, x: x + w]
                rgb  = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                th, tw = rgb.shape[:2]
                scale = min(self._THUMB_W / max(tw, 1), self._THUMB_H / max(th, 1))
                nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
                import cv2 as _cv2
                resized = _cv2.resize(rgb, (nw, nh), interpolation=_cv2.INTER_AREA)
                self._thumbnails.append(QPixmap.fromImage(_numpy_rgb_to_qimage(resized)))
            except Exception:
                self._thumbnails.append(QPixmap())
        self.update()

    def set_selected(self, index: int) -> None:
        self._selected = index
        self.update()

    def clear(self) -> None:
        self._thumbnails = []
        self._selected = -1
        self._scroll_x = 0
        self.update()

    # ── Paint ──────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#13141a"))

        if not self._thumbnails:
            p.setPen(QColor("#4a4b55"))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Panel tespiti yapıldıktan sonra önizlemeler burada görünür")
            p.end()
            return

        x_start = 8 - self._scroll_x
        for i, pm in enumerate(self._thumbnails):
            rx = x_start + i * (self._THUMB_W + self._SPACING)
            ry = 4
            is_sel = (i == self._selected)
            is_hov = (i == self._hover)

            # Arka plan
            bg = QColor("#1e1f28") if not is_sel else QColor("#1a3050")
            p.fillRect(rx, ry, self._THUMB_W, self._THUMB_H, bg)

            # Sınır
            border_col = self._SEL_COL if is_sel else (QColor("#5a5b65") if is_hov else self._BORDER)
            p.setPen(QPen(border_col, 2 if is_sel else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rx, ry, self._THUMB_W, self._THUMB_H)

            # Thumbnail
            if not pm.isNull():
                pw, ph = pm.width(), pm.height()
                ox = rx + (self._THUMB_W - pw) // 2
                oy = ry + (self._THUMB_H - ph) // 2
                p.drawPixmap(ox, oy, pm)

            # Numara etiketi
            p.setPen(QColor("#e4e4e7"))
            p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            lbl_rect = QRect(rx, ry + self._THUMB_H - 14, self._THUMB_W, 14)
            bg2 = QColor("#000000"); bg2.setAlpha(160)
            p.fillRect(lbl_rect, bg2)
            p.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, str(i + 1))

        p.end()

    # ── Mouse ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._idx_at(event.position().x())
            if idx >= 0:
                self._selected = idx
                self.update()
                self.panel_clicked.emit(idx)
            else:
                self._drag_start  = event.position()
                self._drag_scroll = self._scroll_x

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None:
            dx = event.position().x() - self._drag_start.x()
            max_scroll = max(0, len(self._thumbnails) * (self._THUMB_W + self._SPACING) - self.width() + 16)
            self._scroll_x = max(0, min(max_scroll, self._drag_scroll - dx))
            self.update()
        else:
            self._hover = self._idx_at(event.position().x())
            self.update()

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_start = None

    def wheelEvent(self, event) -> None:
        delta = -event.angleDelta().y()
        max_scroll = max(0, len(self._thumbnails) * (self._THUMB_W + self._SPACING) - self.width() + 16)
        self._scroll_x = max(0, min(max_scroll, self._scroll_x + delta // 2))
        self.update()

    def _idx_at(self, mouse_x: float) -> int:
        x_start = 8 - self._scroll_x
        for i in range(len(self._thumbnails)):
            rx = x_start + i * (self._THUMB_W + self._SPACING)
            if rx <= mouse_x <= rx + self._THUMB_W:
                return i
        return -1

    def scroll_to(self, index: int) -> None:
        """Belirtilen panelin görünür olmasını sağlar."""
        if index < 0 or index >= len(self._thumbnails):
            return
        x_start = 8
        rx = x_start + index * (self._THUMB_W + self._SPACING)
        visible_end = self._scroll_x + self.width() - 16
        if rx < self._scroll_x:
            self._scroll_x = max(0, rx - 8)
        elif rx + self._THUMB_W > visible_end:
            self._scroll_x = rx + self._THUMB_W - self.width() + 16
        self.update()


# ══════════════════════════════════════════════════════════════════════════════
# Kısayol Referans Dialogu
# ══════════════════════════════════════════════════════════════════════════════

class ShortcutsDialog(QDialog):
    """Tüm klavye kısayollarını gösteren overlay dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Klavye Kısayolları")
        self.setFixedSize(520, 560)
        self.setModal(False)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("Klavye Kısayolları")
        title.setObjectName("headingLabel")
        layout.addWidget(title)

        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Kısayol", "İşlev"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)

        shortcuts = [
            ("Fare Tekerleği", "Yakınlaştır / uzaklaştır"),
            ("Ctrl + + / -", "Yakınlaştır / uzaklaştır"),
            ("Ctrl + 0", "Ekrana sığdır"),
            ("Space + Sürükle", "Görüntüyü kaydır (pan)"),
            ("Orta Tuş + Sürükle", "Görüntüyü kaydır (pan)"),
            ("Alt + Sürükle", "Görüntüyü kaydır (pan)"),
            ("", ""),
            ("Boş Alana Sürükle", "Yeni panel çiz"),
            ("Panel'e Tıkla", "Paneli seç / taşı"),
            ("Köşeye / Kenara Sürükle", "Paneli yeniden boyutlandır"),
            ("Ctrl + Tıkla", "Çoklu seçim (birleştirme için)"),
            ("Ctrl + M", "Seçili panelleri birleştir"),
            ("Delete / Backspace", "Seçili paneli sil"),
            ("Escape", "Çoklu seçimi temizle"),
            ("", ""),
            ("Ctrl + Z", "Geri al"),
            ("Ctrl + Y / Ctrl+Shift+Z", "İleri al"),
            ("", ""),
            ("? (Soru İşareti)", "Bu kısayol penceresini aç"),
        ]

        table.setRowCount(len(shortcuts))
        for row, (key, desc) in enumerate(shortcuts):
            key_item = QTableWidgetItem(key)
            key_item.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            desc_item = QTableWidgetItem(desc)
            table.setItem(row, 0, key_item)
            table.setItem(row, 1, desc_item)
            if not key:
                table.setRowHeight(row, 8)

        layout.addWidget(table, 1)

        btn_close = QPushButton("Kapat")
        btn_close.setObjectName("secondaryBtn")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.accept)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(btn_close)
        layout.addLayout(h)


# ══════════════════════════════════════════════════════════════════════════════
# Panel Kırpma Önizleme Dialogu
# ══════════════════════════════════════════════════════════════════════════════

class PanelCropPreviewDialog(QDialog):
    """
    "Kaydet" öncesi tespit edilen tüm panellerin grid önizlemesini gösterir.
    Kullanıcı istemediği panellerin işaretini kaldırabilir.

    Kabul edilen paneller: get_selected_boxes() ile alınır.
    """

    def __init__(
        self,
        image: "np.ndarray",
        boxes: "List[PanelBox]",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Panel Kırpma Önizleme")
        self.setMinimumSize(700, 500)
        self.setModal(True)
        self._image = image
        self._boxes = boxes
        self._checks: List["QCheckBox"] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel(f"Panel Önizleme — {len(self._boxes)} panel tespit edildi")
        title.setObjectName("headingLabel")
        layout.addWidget(title)

        hint = QLabel("İstemediğiniz panellerin işaretini kaldırın, ardından Kaydet'e basın.")
        hint.setObjectName("pageSubtitle")
        layout.addWidget(hint)

        # Tümünü seç / kaldır
        sel_row = QHBoxLayout()
        btn_all = QPushButton("Tümünü Seç")
        btn_all.setObjectName("secondaryBtn")
        btn_all.setFixedWidth(120)
        btn_none = QPushButton("Tümünü Kaldır")
        btn_none.setObjectName("secondaryBtn")
        btn_none.setFixedWidth(140)
        btn_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self._checks])
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb in self._checks])
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(10)

        cols = 4
        for i, (x, y, w, h) in enumerate(self._boxes):
            try:
                crop = self._image[y: y + h, x: x + w]
                rgb  = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                ch, cw = rgb.shape[:2]
                scale  = min(150 / max(cw, 1), 110 / max(ch, 1))
                nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
                resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
                pm    = QPixmap.fromImage(_numpy_rgb_to_qimage(resized))
            except Exception:
                pm = QPixmap()

            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(4, 4, 4, 4)
            cv.setSpacing(4)
            cv.setAlignment(Qt.AlignmentFlag.AlignCenter)

            img_lbl = QLabel()
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_lbl.setFixedSize(158, 118)
            img_lbl.setStyleSheet("background: #1a1b26; border: 1px solid #2a2b30; border-radius: 4px;")
            if not pm.isNull():
                img_lbl.setPixmap(pm)
            cv.addWidget(img_lbl)

            cb = QCheckBox(f"Panel {i+1}  ({w}×{h})")
            cb.setChecked(True)
            self._checks.append(cb)
            cv.addWidget(cb, alignment=Qt.AlignmentFlag.AlignCenter)

            row_idx = i // cols
            col_idx = i % cols
            grid.addWidget(cell, row_idx, col_idx)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Butonlar
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selected_boxes(self) -> "List[PanelBox]":
        """Kullanıcının seçili bıraktığı panellerin koordinatlarını döndürür."""
        return [box for box, cb in zip(self._boxes, self._checks) if cb.isChecked()]


# ══════════════════════════════════════════════════════════════════════════════
# Ana Sayfa
# ══════════════════════════════════════════════════════════════════════════════
