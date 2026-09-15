"""
RecapAI - Kart bileşenleri (minimalist).
StatCard, ActionCard — tüm stiller QSS'den.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ui.utils.icons import Icons, ICON_COLOR_ACTIVE, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)


class StatCard(QFrame):
    """
    İstatistik kartı (ana sayfa için).
    İkon + başlık + büyük değer.
    """

    def __init__(self, icon_name: str, title: str,
                 value: str = "0",
                 color: str = ICON_COLOR_ACTIVE,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        self._icon_name = icon_name
        self._color = color
        self._build_ui(title, value, icon_name, color)
        self.setMinimumWidth(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _build_ui(self, title: str, value: str,
                  icon_name: str, color: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        # İkon
        icon_lbl = QLabel()
        pm = Icons.pixmap(icon_name, size=24, color=ICON_COLOR_MUTED)
        if not pm.isNull():
            icon_lbl.setPixmap(pm)
        else:
            icon_lbl.setText("■")
        layout.addWidget(icon_lbl)

        # Başlık
        title_lbl = QLabel(title)
        title_lbl.setObjectName("cardSubtitle")
        layout.addWidget(title_lbl)

        # Değer
        self._value_lbl = QLabel(str(value))
        self._value_lbl.setObjectName("cardValue")
        layout.addWidget(self._value_lbl)

    def set_value(self, value: str) -> None:
        """Değer etiketini güncelle."""
        self._value_lbl.setText(str(value))


class ActionCard(QFrame):
    """
    Tıklanabilir aksiyon kartı.
    İkon + başlık + açıklama.
    """

    clicked = pyqtSignal()

    def __init__(self, icon_name: str, title: str, description: str,
                 color: str = ICON_COLOR_ACTIVE,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui(icon_name, title, description)

    def _build_ui(self, icon_name: str, title: str,
                  description: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        # İkon satırı
        top_row = QHBoxLayout()
        icon_lbl = QLabel()
        pm = Icons.pixmap(icon_name, size=22, color="#6366f1")
        if not pm.isNull():
            icon_lbl.setPixmap(pm)
        else:
            icon_lbl.setText("■")
        top_row.addWidget(icon_lbl)
        top_row.addStretch()

        arrow = QLabel("→")
        arrow.setObjectName("mutedLabel")
        top_row.addWidget(arrow)
        layout.addLayout(top_row)

        # Başlık
        title_lbl = QLabel(title)
        title_lbl.setObjectName("cardTitle")
        layout.addWidget(title_lbl)
        self._title_lbl = title_lbl

        # Açıklama
        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("cardSubtitle")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)
        self._desc_lbl = desc_lbl

        layout.addStretch()

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def set_subtitle(self, text: str) -> None:
        self._desc_lbl.setText(text)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)