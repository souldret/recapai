"""
RecapAI - Durum göstergesi bileşeni.
İkon + renkli metin.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt

from ui.utils.icons import Icons, ICON_COLOR_SUCCESS, ICON_COLOR_ERROR
from ui.utils.icons import ICON_COLOR_WARNING, ICON_COLOR_ACTIVE, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)

# (ikon_adi, renk, varsayilan_metin)
STATUS_MAP = {
    "pending":    (Icons.PENDING,    ICON_COLOR_MUTED,   "Bekliyor"),
    "processing": (Icons.PROCESSING, ICON_COLOR_ACTIVE,  "İşleniyor"),
    "success":    (Icons.SUCCESS,    ICON_COLOR_SUCCESS, "Tamamlandı"),
    "done":       (Icons.SUCCESS,    ICON_COLOR_SUCCESS, "Tamam"),
    "error":      (Icons.ERROR,      ICON_COLOR_ERROR,   "Hata"),
    "warning":    (Icons.WARNING,    ICON_COLOR_WARNING, "Uyarı"),
    "info":       (Icons.INFO,       ICON_COLOR_ACTIVE,  "Bilgi"),
}


class StatusIndicator(QWidget):
    """
    Küçük durum göstergesi.
    Kullanim:
        indicator = StatusIndicator("pending")
        indicator.set_status("success", "Ses oluşturuldu")
    """

    def __init__(self, status: str = "pending", text: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.set_status(status, text)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(14, 14)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text_lbl = QLabel()
        self._text_lbl.setStyleSheet("font-size: 12px;")

        layout.addWidget(self._icon_lbl)
        layout.addWidget(self._text_lbl)
        layout.addStretch()

    def set_status(self, status: str, text: Optional[str] = None) -> None:
        """Durumu güncelle."""
        icon_name, color, default_text = STATUS_MAP.get(
            status, STATUS_MAP["pending"]
        )
        display = text if text is not None else default_text

        pm = Icons.pixmap(icon_name, size=14, color=color)
        if not pm.isNull():
            self._icon_lbl.setPixmap(pm)
        else:
            # Fallback: renkli nokta
            dot_map = {
                "pending": "○", "processing": "◌",
                "success": "●", "done": "●",
                "error": "●", "warning": "▲",
            }
            self._icon_lbl.setText(dot_map.get(status, "○"))
            self._icon_lbl.setStyleSheet(f"color: {color}; font-size: 10px;")

        self._text_lbl.setText(display)
        self._text_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")

    def set_text(self, text: str) -> None:
        """Sadece metni güncelle."""
        self._text_lbl.setText(text)