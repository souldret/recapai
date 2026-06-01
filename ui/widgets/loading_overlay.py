"""
RecapAI - Yükleme overlay bileşeni.
"""

import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor

logger = logging.getLogger(__name__)


class LoadingOverlay(QWidget):
    """
    Ebeveyn widget üzerine yerleşen yarı saydam yükleme ekranı.

    Kullanım:
        overlay = LoadingOverlay(parent_widget)
        overlay.show_with_message("Thumbnail oluşturuluyor...")
        # iş bitince:
        overlay.hide_overlay()
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_idx = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_spinner)
        self._build_ui()
        self.hide()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        self._spinner_lbl = QLabel("⠋")
        self._spinner_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner_lbl.setStyleSheet("color: #6366f1; font-size: 36px;")
        layout.addWidget(self._spinner_lbl)

        self._message_lbl = QLabel("Yükleniyor...")
        self._message_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_lbl.setObjectName("headingLabel")
        layout.addWidget(self._message_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)          # indeterminate
        self._progress.setFixedWidth(260)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignCenter)

    # ── Public API ─────────────────────────────────────────────────

    def show_with_message(self, message: str = "Yükleniyor...") -> None:
        """Overlay'i gösterir ve animasyonu başlatır."""
        self._message_lbl.setText(message)
        self.resize(self.parent().size())
        self.raise_()
        self.show()
        self._timer.start(80)
        logger.debug("LoadingOverlay gösterildi: %s", message)

    def hide_overlay(self) -> None:
        """Overlay'i gizler ve animasyonu durdurur."""
        self._timer.stop()
        self.hide()
        logger.debug("LoadingOverlay gizlendi.")

    def set_message(self, message: str) -> None:
        """Mesajı günceller."""
        self._message_lbl.setText(message)

    def set_progress(self, value: int, maximum: int = 100) -> None:
        """Belirli ilerleme gösterir (0-maximum)."""
        self._progress.setRange(0, maximum)
        self._progress.setValue(value)

    # ── Internal ───────────────────────────────────────────────────

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_chars)
        self._spinner_lbl.setText(self._spinner_chars[self._spinner_idx])

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(26, 27, 38, 210))
        painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        if self.parent():
            self.resize(self.parent().size())
        super().resizeEvent(event)