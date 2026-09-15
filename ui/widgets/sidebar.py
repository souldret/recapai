"""
RecapAI - Modern Sidebar bileşeni.
Bolumlu, ikonlu, animasyonsuz (sade).
"""

import logging
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize

from ui.utils.icons import Icons, ICON_COLOR, ICON_COLOR_ACTIVE, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)

# (index, metin, ikon_adi) — bolumler None bölücü
_MENU_ITEMS: list = [
    # BÖLÜM: CALISMA ALANI
    ("section", "ÇALIŞMA ALANI", None),
    (0, "Ana Sayfa",   Icons.HOME),
    (1, "Projeler",    Icons.PROJECTS),
    # BÖLÜM: ÜRETIM
    ("section", "ÜRETİM", None),
    (2, "Görseller",   Icons.IMAGES),
    (3, "AI Analiz",   Icons.ANALYSIS),
    (4, "Script",      Icons.SCRIPT),
    (5, "Seslendirme", Icons.TTS),
    (6, "Render",      Icons.RENDER),
]

_BOTTOM_ITEMS: list = [
    (7, "Ayarlar", Icons.SETTINGS),
]


class SidebarButton(QPushButton):
    """Sidebar menü butonu."""

    def __init__(self, label: str, icon_name: Optional[str], index: int,
                 parent=None) -> None:
        super().__init__(parent)
        self.page_index = index
        self.setObjectName("sidebarMenuItem")
        self.setCheckable(True)
        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.setText(label)
        self._base_label = label
        self._badge = ""

        if icon_name:
            icon = Icons.get(icon_name, color=ICON_COLOR_MUTED)
            if not icon.isNull():
                self.setIcon(icon)
                self.setIconSize(QSize(18, 18))

    def set_active(self, active: bool) -> None:
        """Aktif durumu ayarla ve ikonu renklendir."""
        self.setChecked(active)
        if hasattr(self, '_icon_name') and self._icon_name:
            color = ICON_COLOR_ACTIVE if active else ICON_COLOR_MUTED
            icon = Icons.get(self._icon_name, color=color)
            if not icon.isNull():
                self.setIcon(icon)


class Sidebar(QWidget):
    """
    Sol kenar çubuğu.
    Sinyal: page_changed(int) — tıklanan sayfa indeksi
    """

    page_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(240)
        self._buttons: List[SidebarButton] = []
        self._build_ui()
        logger.debug("Sidebar olusturuldu.")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo
        layout.addWidget(self._make_logo())

        # Ana menü öğeleri
        for item in _MENU_ITEMS:
            if item[0] == "section":
                layout.addWidget(self._make_section(item[1]))
            else:
                idx, label, icon_name = item
                btn = self._make_button(label, icon_name, idx)
                layout.addWidget(btn)

        layout.addStretch(1)

        # Alt menü öğeleri (Ayarlar vb.)
        layout.addWidget(self._make_section("SİSTEM"))
        for idx, label, icon_name in _BOTTOM_ITEMS:
            btn = self._make_button(label, icon_name, idx)
            layout.addWidget(btn)

        layout.addWidget(self._make_version())

    def _make_logo(self) -> QWidget:
        container = QWidget()
        container.setObjectName("logoContainer")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 16, 0, 16)
        vbox.setSpacing(8)

        from PyQt6.QtGui import QPixmap
        import os

        # Logo Image
        logo_img = QLabel()
        logo_img.setObjectName("sidebarLogoImg")
        logo_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        logo_path = os.path.join("assets", "logo.png")
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            # Logoyu boyutlandır
            pixmap = pixmap.scaled(180, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_img.setPixmap(pixmap)
            vbox.addWidget(logo_img)
        else:
            logo = QLabel("RecapAI")
            logo.setObjectName("sidebarLogo")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vbox.addWidget(logo)

            sub = QLabel("MANHWA RECAP STUDIO")
            sub.setObjectName("sidebarLogoSub")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vbox.addWidget(sub)

        return container

    def _make_section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sidebarSection")
        return lbl

    def _make_button(self, label: str, icon_name: Optional[str],
                     index: int) -> SidebarButton:
        btn = SidebarButton(label, icon_name, index)
        btn._icon_name = icon_name
        btn.clicked.connect(lambda _checked, i=index: self._on_button_clicked(i))
        self._buttons.append(btn)
        return btn

    def _make_version(self) -> QLabel:
        lbl = QLabel("RecapAI v1.0.0")
        lbl.setObjectName("sidebarVersion")
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        return lbl

    def _on_button_clicked(self, index: int) -> None:
        self.set_active(index)
        self.page_changed.emit(index)

    def set_active(self, index: int) -> None:
        """Aktif butonu işaretle, diğerlerini kaldır."""
        for btn in self._buttons:
            active = (btn.page_index == index)
            btn.setChecked(active)
            # İkon rengini güncelle
            if hasattr(btn, '_icon_name') and btn._icon_name:
                color = ICON_COLOR_ACTIVE if active else ICON_COLOR_MUTED
                icon = Icons.get(btn._icon_name, color=color)
                if not icon.isNull():
                    btn.setIcon(icon)
            # Style yenile
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_step_badges(self, badges: dict) -> None:
        """page index → empty | partial | done"""
        suffix = {"done": " ✓", "partial": " ·", "empty": ""}
        for btn in self._buttons:
            mark = suffix.get(badges.get(btn.page_index, "empty"), "")
            base = getattr(btn, "_base_label", btn.text().split(" ")[0] if btn.text() else "")
            if hasattr(btn, "_base_label"):
                btn.setText(btn._base_label + mark)

    @property
    def buttons(self) -> List[SidebarButton]:
        return self._buttons