"""
RecapAI - Standart sayfa header bileşeni.
"""

import logging
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PyQt6.QtCore import Qt

logger = logging.getLogger(__name__)


class PageHeader(QWidget):
    """
    Her sayfanın üstündeki standart header.
    Baslik + altyazi + opsiyonel aksiyon butonlari.
    """

    def __init__(self, title: str, subtitle: str = "",
                 actions: Optional[List[QWidget]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self.setFixedHeight(70)
        self._build_ui(title, subtitle, actions or [])

    def _build_ui(self, title: str, subtitle: str,
                  actions: List[QWidget]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Üst satir: baslik + aksiyon butonlari
        top_widget = QWidget()
        top = QHBoxLayout(top_widget)
        top.setContentsMargins(24, 14, 24, 2 if subtitle else 14)
        top.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("pageTitle")
        top.addWidget(title_lbl)
        top.addStretch()

        for action in actions:
            top.addWidget(action)

        layout.addWidget(top_widget)

        # Alt satir: aciklama
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setObjectName("pageSubtitle")
            sub_lbl.setContentsMargins(24, 0, 24, 14)
            layout.addWidget(sub_lbl)

    def set_title(self, title: str) -> None:
        """Baslik metnini guncelle."""
        for i in range(self.layout().count()):
            item = self.layout().itemAt(i)
            if item and item.widget():
                inner_layout = item.widget().layout()
                if inner_layout:
                    for j in range(inner_layout.count()):
                        w = inner_layout.itemAt(j)
                        if w and isinstance(w.widget(), QLabel):
                            lbl = w.widget()
                            if lbl.objectName() == "pageTitle":
                                lbl.setText(title)
                                return