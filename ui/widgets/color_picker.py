"""
RecapAI - Renk seçici widget.
"""

import logging
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QColorDialog
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

logger = logging.getLogger(__name__)


class ColorPickerButton(QWidget):
    """
    Renk seçici buton widget'ı.
    Tıklandığında QColorDialog açar, seçilen rengi gösterir.
    """

    color_changed = pyqtSignal(str)  # Hex renk kodu (#RRGGBB)

    def __init__(
        self,
        initial_color: str = "#ffffff",
        label: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._color = QColor(initial_color) if QColor(initial_color).isValid() else QColor("#ffffff")
        self._label_text = label
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        if self._label_text:
            lbl = QLabel(self._label_text)
            lbl.setObjectName("pageSubtitle")
            layout.addWidget(lbl)

        self._swatch = QPushButton()
        self._swatch.setFixedSize(36, 28)
        self._swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._swatch.setToolTip("Renk seç")
        self._swatch.clicked.connect(self._open_dialog)
        layout.addWidget(self._swatch)

        self._hex_label = QLabel()
        self._hex_label.setObjectName("pageSubtitle")
        self._hex_label.setFixedWidth(70)
        layout.addWidget(self._hex_label)

        self._update_display()

    def _open_dialog(self) -> None:
        """QColorDialog'u açar ve seçilen rengi uygular."""
        chosen = QColorDialog.getColor(
            self._color,
            self,
            "Renk Seç",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if chosen.isValid():
            self._color = chosen
            self._update_display()
            self.color_changed.emit(self._color_hex())

    def _color_hex(self) -> str:
        """Alpha dahil hex rengi döndürür (#AARRGGBB veya #RRGGBB)."""
        if self._color.alpha() < 255:
            return self._color.name(QColor.NameFormat.HexArgb)
        return self._color.name(QColor.NameFormat.HexRgb)

    def _update_display(self) -> None:
        """Renk örneği ve hex etiketi günceller."""
        # Kontrast ön plan rengi
        lum = 0.299 * self._color.red() + 0.587 * self._color.green() + 0.114 * self._color.blue()
        fg = "#000000" if lum > 128 else "#ffffff"
        alpha = self._color.alpha() / 255.0
        self._swatch.setStyleSheet(
            f"QPushButton {{ background-color: rgba({self._color.red()},{self._color.green()},"
            f"{self._color.blue()},{alpha:.2f}); color: {fg}; "
            f"border: 1px solid #414868; border-radius: 4px; }}"
            f"QPushButton:hover {{ border: 2px solid #7aa2f7; }}"
        )
        self._hex_label.setText(self._color_hex().upper())

    # ── Public API ────────────────────────────────────────────────

    @property
    def color(self) -> str:
        """Seçili rengin hex kodunu döndürür (alpha varsa dahil)."""
        return self._color_hex()

    def set_color(self, color: str) -> None:
        """Rengi programatik olarak ayarlar."""
        c = QColor(color)
        if c.isValid():
            self._color = c
            self._update_display()

    def get_color_name(self) -> str:
        """Qt renk adını döndürür (örn. 'white', 'black')."""
        return self._color_hex()

    def get_rgb_tuple(self):
        """(R, G, B) tuple döndürür."""
        return (self._color.red(), self._color.green(), self._color.blue())