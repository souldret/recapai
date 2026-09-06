"""
RecapAI - Görseller Sayfası (v3)
Manga ve Webtoon türlerine göre farklı görsel işleme akışı sunar.

Manga modu:
  - Sayfa grid görünümü
  - Seçili sayfa üzerinde panel tespiti (otomatik veya elle)
  - Panel listesi: genişlet/daralt/sil/ekle

Webtoon modu:
  - Bölüm görsellerini dikey birleştir (stitch)
  - Birleştirilmiş görsel üzerinde panel tespiti
  - Panel editörü: manuel genişlet/daralt/sil/ekle
"""

import logging
from pathlib import Path
from typing import Optional, List, Tuple

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

from core.context import AppContext
from ui.utils.icons import Icons

logger = logging.getLogger(__name__)

# Tip alias
PanelBox = Tuple[int, int, int, int]   # (x, y, w, h)


# ══════════════════════════════════════════════════════════════════════════════
# Thumbnail Worker
# ══════════════════════════════════════════════════════════════════════════════

class ThumbnailWorker(QThread):
    """Arka planda thumbnail oluşturan iş parçacığı."""

    progress = pyqtSignal(int, int)
    thumbnail_ready = pyqtSignal(int, str)
    finished = pyqtSignal()

    def __init__(self, paths: List[str], output_dir: str) -> None:
        super().__init__()
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
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data.tobytes(), w, h, ch * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)
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
        self.setFixedSize(360, 200)
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

        layout.addLayout(grid)

        hint = QLabel(
            "Min oranı düşürürseniz küçük paneller de yakalanır.\n"
            "Max oranı düşürürseniz tam sayfa paneller filtrelenir."
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


# ══════════════════════════════════════════════════════════════════════════════
# Manga Panel Editörü
# Seçili manga sayfası için panel tespiti + manuel düzenleme
# ══════════════════════════════════════════════════════════════════════════════

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
        self._batch_results: dict = {}
        self._all_image_paths: List[str] = []          # batch detection için
        self._min_area = 0.015
        self._max_area = 0.92
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

        self.btn_settings = QPushButton("  Ayarlar")
        self.btn_settings.setObjectName("secondaryBtn")
        self.btn_settings.setIcon(Icons.get(Icons.SETTINGS))
        self.btn_settings.setIconSize(QSize(14, 14))
        self.btn_settings.clicked.connect(self._open_settings)
        tb.addWidget(self.btn_settings)

        # Okuma düzeni seçimi
        from PyQt6.QtWidgets import QComboBox as _QCB
        self.reading_order_combo = _QCB()
        self.reading_order_combo.addItem("RTL (Manga)", "rtl")
        self.reading_order_combo.addItem("LTR (Manhwa)", "ltr")
        self.reading_order_combo.setFixedWidth(130)
        self.reading_order_combo.setToolTip("Panel okuma düzeni")
        self.reading_order_combo.currentIndexChanged.connect(self._on_reading_order_changed)
        tb.addWidget(self.reading_order_combo)

        # Ok göster/gizle
        from PyQt6.QtWidgets import QCheckBox as _QCB2
        self.chk_show_arrows = _QCB2("Oklar")
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

        # Zoom & Undo butonları
        from PyQt6.QtWidgets import QToolButton
        
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

        # Önceki yükleyiciyi durdur
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.cancel()
            self._load_worker.wait()

        self._current_image_path = image_path
        self.btn_detect.setEnabled(False)
        self.btn_save_panels.setEnabled(False)
        self.lbl_status.setText(f"Yükleniyor: {Path(image_path).name}...")

        from ui.workers.panel_worker import ImageLoadWorker
        self._load_worker = ImageLoadWorker(image_path)
        self._load_worker.finished.connect(self._on_image_loaded)
        self._load_worker.error.connect(self._on_image_load_error)
        self._load_worker.start()

    def _on_image_loaded(self, image_path: str, img) -> None:
        """Arka plan yükleyiciden gelen görüntü."""
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
        return [(int(p["x"]), int(p["y"]), int(p["w"]), int(p["h"])) for p in stored]

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
            except Exception:
                logger.warning("Panel verisi kaydedilemedi.", exc_info=True)

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
            if w and w.isRunning():
                w.cancel()
                w.wait(2000)   # max 2 sn bekle — UI thread'ini bloke etme
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
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._min_area, self._max_area = dlg.get_values()

    def _run_detection(self) -> None:
        if not self._current_image_path:
            return
        if self._detect_worker and self._detect_worker.isRunning():
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
        )
        self._detect_worker.progress.connect(self.lbl_status.setText)
        self._detect_worker.finished.connect(self._on_detection_finished)
        self._detect_worker.error.connect(self._on_detection_error)
        self._detect_worker.start()

    def _on_detection_finished(self, boxes: list) -> None:
        self.progress_bar.hide()
        self.btn_detect.setEnabled(True)
        self.canvas.set_panels(boxes)
        self.panel_list_widget.load_panels(boxes)
        self.btn_save_panels.setEnabled(bool(boxes))
        self.btn_merge.setEnabled(bool(boxes))
        self.lbl_status.setText(f"{len(boxes)} panel tespit edildi.")
        self.ctx.app_state.status_message.emit(f"Panel tespiti: {len(boxes)} panel bulundu.")
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

        from ui.workers.panel_worker import PanelBatchDetectWorker
        order = self.reading_order_combo.currentData() or "rtl"
        self._batch_worker = PanelBatchDetectWorker(
            image_paths=self._all_image_paths,
            reading_order=order,
            min_area_ratio=self._min_area,
            max_area_ratio=self._max_area,
        )
        self._batch_results: dict = {}
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setRange(0, len(self._all_image_paths))
        self.batch_progress_bar.show()
        self.lbl_batch_status.show()
        self.btn_batch_detect.setEnabled(False)
        self.btn_detect.setEnabled(False)

        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.page_done.connect(self._on_batch_page_done)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.start()

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
        self._min_area = 0.01   # webtoon için daha küçük eşik
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

        from PyQt6.QtWidgets import QToolButton
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
            if w and w.isRunning():
                w.cancel()
                w.wait(2000)   # max 2 sn bekle
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
        )
        self._stitch_worker.progress.connect(self.lbl_progress.setText)
        self._stitch_worker.finished.connect(self._on_stitch_finished)
        self._stitch_worker.error.connect(self._on_stitch_error)
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
            self._load_worker.cancel()
            self._load_worker.wait()
        self._load_worker = ImageLoadWorker(out_path)
        self._load_worker.finished.connect(self._on_stitched_image_loaded)
        self._load_worker.error.connect(self._on_stitch_load_error)
        self._load_worker.start()

    def _on_stitched_image_loaded(self, out_path: str, img) -> None:
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
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._min_area, self._max_area = dlg.get_values()

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
        )
        self._detect_worker.progress.connect(self.lbl_progress.setText)
        self._detect_worker.finished.connect(self._on_detection_finished)
        self._detect_worker.error.connect(self._on_detection_error)
        self._detect_worker.start()

    def _on_detection_finished(self, boxes: list) -> None:
        self.progress_bar.hide()
        self.lbl_progress.hide()
        self.btn_detect.setEnabled(True)
        self.canvas.set_panels(boxes)
        self.panel_list_widget.load_panels(boxes)
        self.btn_save_panels.setEnabled(bool(boxes))
        self.lbl_detect_info.setText(f"{len(boxes)} panel tespit edildi. Düzenlemeler yapabilirsiniz.")
        self.ctx.app_state.status_message.emit(f"Webtoon panel tespiti: {len(boxes)} panel.")

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


# ══════════════════════════════════════════════════════════════════════════════
# Panel Thumbnail Şeridi
# ══════════════════════════════════════════════════════════════════════════════

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
                qimg = QImage(resized.data.tobytes(), nw, nh, nw * 3, QImage.Format.Format_RGB888)
                self._thumbnails.append(QPixmap.fromImage(qimg))
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
                qimg  = QImage(resized.data.tobytes(), nw, nh, nw * 3, QImage.Format.Format_RGB888)
                pm    = QPixmap.fromImage(qimg)
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

class ImagesPage(QWidget):
    """
    Görsel yükleme ve yönetim sayfası (v3).
    Proje türüne (manga/webtoon) göre farklı arayüz sunar.
    """

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._worker: Optional[ThumbnailWorker] = None
        self._image_paths: List[str] = []
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
            self.btn_add.hide()
            self.btn_sort.hide()
        else:
            self.stack.setCurrentIndex(0)
            self.type_badge.setText("Mod: Manga  |  Sayfa bazlı panel tespiti aktif")
            self.btn_add.show()
            self.btn_sort.show()

    def _on_chapter_changed_state(self, chapter) -> None:
        """Dışarıdan (örn. ProjectPage) chapter değiştiğinde görselleri günceller."""
        if chapter is None:
            return
        state = self.ctx.app_state
        if not state.current_project:
            return
        series_type = getattr(state.current_project, "series_type", "manga")
        if series_type == "webtoon":
            self._load_chapter_webtoon(chapter, state.current_project)
        else:
            self._load_chapter_images(chapter)
        # Combo'yu senkronize et
        idx = self.chapter_combo.findData(chapter.id)
        if idx >= 0 and self.chapter_combo.currentIndex() != idx:
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.setCurrentIndex(idx)
            self.chapter_combo.blockSignals(False)

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

    def _load_chapter_images(self, chapter) -> None:
        self.grid.clear()
        self._image_paths = [img.path for img in chapter.images]
        for i, path in enumerate(self._image_paths):
            item = ImageGridItem(path, i)
            self.grid.addItem(item)
        self._update_count()
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
        offset = len(chapter.images)
        for i, path in enumerate(files):
            img = ImageData(path=path, filename=Path(path).name, order=offset + i)
            chapter.images.append(img)
            item = ImageGridItem(path, offset + i)
            self.grid.addItem(item)

        save_project(state.current_project)
        self._image_paths = [img.path for img in chapter.images]
        self._update_count()
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
        save_project(state.current_project)
        self._load_chapter_images(chapter)
        self.ctx.app_state.status_message.emit("Görseller otomatik sıralandı.")

    # ── Webtoon Görsel Yükleme ──────────────────────────────────────

    def _load_chapter_webtoon(self, chapter, project) -> None:
        self._image_paths = [img.path for img in chapter.images]
        self._update_count()

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
            self._worker.cancel()
            self._worker.wait()

        state = self.ctx.app_state
        from core.project_manager import get_project_dir
        project_dir = get_project_dir(state.current_project)
        if not project_dir:
            return

        thumb_dir = project_dir / "thumbnails"
        paths = [img.path for img in chapter.images]

        self.progress_bar.setRange(0, len(paths))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.lbl_progress.setText("Thumbnail oluşturuluyor...")
        self.lbl_progress.show()

        self._worker = ThumbnailWorker(paths, str(thumb_dir))
        self._worker.progress.connect(self._on_thumb_progress)
        self._worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._worker.finished.connect(self._on_thumb_finished)
        self._worker.start()

    def _on_thumb_progress(self, done: int, total: int) -> None:
        self.progress_bar.setValue(done)
        self.lbl_progress.setText(f"Thumbnail: {done}/{total}")

    def _on_thumbnail_ready(self, index: int, thumb_path: str) -> None:
        item = self.grid.item(index)
        if item:
            pixmap = QPixmap(thumb_path)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))

    def _on_thumb_finished(self) -> None:
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
            self._update_count()

    def _sync_order(self) -> None:
        state = self.ctx.app_state
        if not state.current_project or self.chapter_combo.currentIndex() < 0:
            return
        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return

        new_images = []
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if isinstance(it, ImageGridItem):
                from core.models import ImageData
                new_images.append(ImageData(path=it.image_path, filename=it.filename, order=i))
        chapter.images = new_images

        from core.project_manager import save_project
        save_project(state.current_project)
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
        state = self.ctx.app_state
        if state.current_project:
            current_data = self.chapter_combo.currentData()
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.clear()
            for ch in state.current_project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
            self.chapter_combo.blockSignals(False)
            if current_data:
                idx = self.chapter_combo.findData(current_data)
                if idx >= 0:
                    self.chapter_combo.setCurrentIndex(idx)  # sinyali bir kez kasıtlı tetikle
            self._apply_series_mode(state.current_project.series_type)
        super().showEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)


# ══════════════════════════════════════════════════════════════════════════════
# Yardımcı
# ══════════════════════════════════════════════════════════════════════════════

def _load_cv2_image(path: str) -> np.ndarray:
    """
    Görüntüyü OpenCV BGR formatında yükler.
    Pillow kullanır — cv2.imread'in ~65535 px yükseklik sınırı yoktur.
    PNG, JPEG, WebP ve tüm Pillow destekli formatları kapsar.
    """
    from PIL import Image as PILImage
    try:
        pil_img = PILImage.open(Path(path)).convert("RGB")
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        pil_img.close()
        return img
    except Exception as exc:
        raise ValueError(f"Görüntü okunamadı: {path} — {exc}") from exc
