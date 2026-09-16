"""
RecapAI - Seslendirme (TTS) Sayfası.
Edge-TTS (online) + Kokoro TTS (offline) destekler.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QSlider, QProgressBar, QListWidget,
    QListWidgetItem, QSplitter, QTextEdit, QCheckBox,
    QSizePolicy, QScrollArea, QToolButton, QAbstractItemView,
    QMessageBox, QSpinBox, QDoubleSpinBox, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QPixmap, QFont

from core.context import AppContext
from ui.widgets.audio_player import AudioPlayerWidget
from ui.widgets.page_header import PageHeader
from ui.utils.icons import Icons, ICON_COLOR_SUCCESS, ICON_COLOR_ERROR
from ui.utils.icons import ICON_COLOR_WARNING, ICON_COLOR_MUTED, ICON_COLOR_ACTIVE

logger = logging.getLogger(__name__)

_LABEL_DIM = "color: #9aa5ce;"
_GREEN     = "color: #9ece6a;"
_RED       = "color: #f7768e;"
_BLUE      = "color: #7aa2f7;"


# ── Segment liste ögesi ────────────────────────────────────────────────────────

class SegmentListItem(QWidget):
    """Segment listesindeki her satir icin ozel widget."""

    play_clicked  = pyqtSignal(int)
    regen_clicked = pyqtSignal(int)
    edit_clicked  = pyqtSignal(int)

    _STATUS_COLORS = {
        "waiting":    ICON_COLOR_MUTED,
        "processing": ICON_COLOR_WARNING,
        "done":       ICON_COLOR_SUCCESS,
        "error":      ICON_COLOR_ERROR,
    }
    _STATUS_ICONS = {
        "waiting":    Icons.PENDING,
        "processing": Icons.PROCESSING,
        "done":       Icons.SUCCESS,
        "error":      Icons.ERROR,
    }

    def __init__(self, index: int, segment, parent=None) -> None:
        super().__init__(parent)
        self.index   = index
        self.segment = segment
        self._status = "waiting"
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(10)

        # Numara
        self._lbl_num = QLabel(f"#{self.index + 1}")
        self._lbl_num.setFixedWidth(32)
        self._lbl_num.setObjectName("mutedLabel")
        root.addWidget(self._lbl_num)

        # Thumbnail
        self._lbl_thumb = QLabel()
        self._lbl_thumb.setFixedSize(60, 60)
        self._lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._lbl_thumb)

        # Metin onizleme
        self._lbl_text = QLabel()
        self._lbl_text.setWordWrap(True)
        self._lbl_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root.addWidget(self._lbl_text, 1)

        # Sure
        self._lbl_dur = QLabel("--:--")
        self._lbl_dur.setFixedWidth(45)
        self._lbl_dur.setObjectName("mutedLabel")
        self._lbl_dur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self._lbl_dur)

        # Durum ikonu
        self._lbl_status = QLabel()
        self._lbl_status.setFixedSize(18, 18)
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._lbl_status)

        # Butonlar
        self._btn_play  = self._mini_btn(Icons.PLAY,    "Cal",    self._on_play)
        self._btn_regen = self._mini_btn(Icons.REFRESH, "Yeniden", self._on_regen)
        self._btn_edit  = self._mini_btn(Icons.EDIT,    "Duzenle", self._on_edit)
        root.addWidget(self._btn_play)
        root.addWidget(self._btn_regen)
        root.addWidget(self._btn_edit)

    def _mini_btn(self, icon_name: str, tip: str, slot) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(28, 28)
        btn.setToolTip(tip)
        btn.setObjectName("secondaryBtn")
        icon = Icons.get(icon_name)
        if not icon.isNull():
            btn.setIcon(icon)
        else:
            fallback = {"Cal": "Play", "Yeniden": "Ref", "Duzenle": "Edit"}
            btn.setText(fallback.get(tip, "?"))
        btn.clicked.connect(slot)
        return btn

    def _on_play(self):  self.play_clicked.emit(self.index)
    def _on_regen(self): self.regen_clicked.emit(self.index)
    def _on_edit(self):  self.edit_clicked.emit(self.index)

    def refresh(self) -> None:
        seg = self.segment
        preview = (seg.text or "").replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        self._lbl_text.setText(preview or "(metin yok)")

        if seg.duration and seg.duration > 0:
            total = int(seg.duration)
            m, s = divmod(total, 60)
            self._lbl_dur.setText(f"{m:02d}:{s:02d}")
        else:
            self._lbl_dur.setText("--:--")

        self._btn_play.setEnabled(bool(seg.audio_path and Path(seg.audio_path).exists()))
        self._update_status_icon()

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        self._lbl_thumb.setPixmap(
            pixmap.scaled(58, 58, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        )

    def set_status(self, status: str) -> None:
        self._status = status
        self._update_status_icon()
        self.refresh()

    def _update_status_icon(self) -> None:
        icon_name = self._STATUS_ICONS.get(self._status, Icons.PENDING)
        color     = self._STATUS_COLORS.get(self._status, ICON_COLOR_MUTED)
        pm = Icons.pixmap(icon_name, size=14, color=color)
        if not pm.isNull():
            self._lbl_status.setPixmap(pm)
        else:
            dot_map = {"waiting": "○", "processing": "◌", "done": "●", "error": "●"}
            self._lbl_status.setText(dot_map.get(self._status, "○"))
            self._lbl_status.setStyleSheet(f"color: {color}; font-size: 10px;")


# ── Ayarlar paneli ────────────────────────────────────────────────────────────

class SettingsPanel(QFrame):
    """Engine'e gore degisen ayarlar paneli (daraltilabilir)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionFrame")
        self._expanded = True
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(14, 8, 14, 8)
        self._btn_toggle = QToolButton()
        self._btn_toggle.setText("Ayarlar")
        self._btn_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._btn_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._btn_toggle.clicked.connect(self._toggle)
        hdr.addWidget(self._btn_toggle)
        hdr.addStretch()
        root.addLayout(hdr)

        self._content = QWidget()
        cl = QVBoxLayout(self._content)
        cl.setContentsMargins(16, 0, 16, 12)
        cl.setSpacing(10)

        # Edge-TTS ayarlari
        self._edge_widget = QWidget()
        ev = QVBoxLayout(self._edge_widget)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(8)
        ev.addWidget(QLabel("Edge-TTS Ayarlari:"))
        self._edge_rate_slider, self._edge_rate_lbl = self._slider_row(
            ev, "Hiz:", -50, 50, 0, lambda v: f"{v:+d}%"
        )
        self._edge_pitch_slider, self._edge_pitch_lbl = self._slider_row(
            ev, "Ton:", -10, 10, 0, lambda v: f"{v:+d}Hz"
        )
        self._edge_vol_slider, self._edge_vol_lbl = self._slider_row(
            ev, "Ses:", -50, 50, 0, lambda v: f"{v:+d}%"
        )
        cl.addWidget(self._edge_widget)

        # Kokoro ayarlari
        self._kokoro_widget = QWidget()
        kv = QVBoxLayout(self._kokoro_widget)
        kv.setContentsMargins(0, 0, 0, 0)
        kv.setSpacing(8)
        kv.addWidget(QLabel("Kokoro TTS Ayarlari:"))
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Hiz:"))
        self._kokoro_speed = QDoubleSpinBox()
        self._kokoro_speed.setRange(0.5, 2.0)
        self._kokoro_speed.setSingleStep(0.1)
        self._kokoro_speed.setValue(1.0)
        self._kokoro_speed.setFixedWidth(80)
        speed_row.addWidget(self._kokoro_speed)
        speed_row.addWidget(QLabel("x"))
        speed_row.addStretch()
        kv.addLayout(speed_row)

        gpu_row = QHBoxLayout()
        self._kokoro_gpu = QCheckBox("GPU kullan (CUDA)")
        gpu_row.addWidget(self._kokoro_gpu)
        gpu_row.addStretch()
        kv.addLayout(gpu_row)

        sr_lbl = QLabel("Ornekleme hizi: 24000 Hz (sabit)")
        sr_lbl.setObjectName("mutedLabel")
        kv.addWidget(sr_lbl)
        cl.addWidget(self._kokoro_widget)
        self._kokoro_widget.setVisible(False)

        self._mix_check = QCheckBox("Anlatıcı Kokoro, diyalog Edge (karışık motor)")
        self._mix_check.setToolTip(
            "Açıkken cold open / beat / filler Kokoro, diyalog ve kanca dışı roller Edge kullanır.\n"
            "Her iki motor da yüklü olmalı."
        )
        try:
            from core.settings_manager import SettingsManager
            self._mix_check.setChecked(bool(SettingsManager.instance().get("tts.mix_engines", False)))
        except Exception:
            pass
        cl.addWidget(self._mix_check)

        root.addWidget(self._content)
        self._check_gpu()

    def _slider_row(self, parent_layout, label: str, min_v: int, max_v: int,
                    default: int, fmt_fn) -> Tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(45)
        row.addWidget(lbl)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default)
        slider.setFixedWidth(200)
        row.addWidget(slider)
        val_lbl = QLabel(fmt_fn(default))
        val_lbl.setFixedWidth(60)
        val_lbl.setObjectName("mutedLabel")
        row.addWidget(val_lbl)
        row.addStretch()
        parent_layout.addLayout(row)
        slider.valueChanged.connect(lambda v: val_lbl.setText(fmt_fn(v)))
        return slider, val_lbl

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._btn_toggle.setArrowType(
            Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow
        )

    def _check_gpu(self) -> None:
        try:
            import torch
            if torch.cuda.is_available():
                self._kokoro_gpu.setChecked(True)
                self._kokoro_gpu.setText("GPU kullan (CUDA)")
        except (ImportError, OSError, Exception):
            self._kokoro_gpu.setEnabled(False)
            self._kokoro_gpu.setText("GPU kullan (CUDA mevcut degil)")

    def switch_engine(self, engine_name: str) -> None:
        self._edge_widget.setVisible(engine_name == "edge-tts")
        self._kokoro_widget.setVisible(engine_name == "kokoro")

    def get_edge_params(self) -> Dict:
        return {
            "rate":   f"{self._edge_rate_slider.value():+d}%",
            "pitch":  f"{self._edge_pitch_slider.value():+d}Hz",
            "volume": f"{self._edge_vol_slider.value():+d}%",
        }

    def get_kokoro_params(self) -> Dict:
        # float garanti + cache anahtarı için yuvarla
        speed = round(float(self._kokoro_speed.value()), 2)
        return {"speed": speed}

    def get_params(self, engine_name: str) -> Dict:
        if engine_name == "edge-tts":
            return self.get_edge_params()
        return self.get_kokoro_params()

    def mix_engines(self) -> bool:
        return bool(getattr(self, "_mix_check", None) and self._mix_check.isChecked())

    def apply_default_speed(self, speed: float = 1.0) -> None:
        """Ayarlar sayfasındaki varsayılan hızı panellere uygular."""
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 1.0
        speed = max(0.5, min(2.0, speed))
        self._kokoro_speed.setValue(speed)
        # Edge rate: 1.0x → +0%, 1.2x → +20%
        rate_pct = int(round((speed - 1.0) * 100))
        rate_pct = max(-50, min(50, rate_pct))
        self._edge_rate_slider.setValue(rate_pct)
        if hasattr(self, "_edge_rate_lbl") and self._edge_rate_lbl is not None:
            self._edge_rate_lbl.setText(f"{rate_pct:+d}%")


# ── Ses filtresi ──────────────────────────────────────────────────────────────

class FilteredVoiceCombo(QWidget):
    """Dil tab'lariyla filtrelenmis ses dropdown'u."""

    voice_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_voices: List[Dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._filter_row = QHBoxLayout()
        self._filter_row.setSpacing(4)
        self._filter_buttons: List[QPushButton] = []

        self._combo = QComboBox()
        self._combo.setMinimumWidth(260)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)

        root.addLayout(self._filter_row)
        root.addWidget(self._combo, 1)

    def _clear_filters(self) -> None:
        for btn in self._filter_buttons:
            self._filter_row.removeWidget(btn)
            btn.deleteLater()
        self._filter_buttons.clear()

    def _add_filter(self, label: str, lang_prefix: str) -> None:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setFixedHeight(26)
        btn.setFixedWidth(68)
        btn.setObjectName("secondaryBtn")
        btn.clicked.connect(lambda _, p=lang_prefix, b=btn: self._apply_filter(p, b))
        self._filter_row.addWidget(btn)
        self._filter_buttons.append(btn)

    def load_voices(self, voices: List[Dict], engine_name: str) -> None:
        self._all_voices = voices
        self._clear_filters()

        if engine_name == "edge-tts":
            filters = [("TR", "tr-TR"), ("EN-US", "en-US"), ("EN-GB", "en-GB"), ("Tümü", "")]
        else:
            filters = [("EN-US", "a"), ("EN-GB", "b"), ("JP", "j"), ("ES", "e"), ("FR", "f"), ("Tümü", "")]

        for label, prefix in filters:
            self._add_filter(label, prefix)

        if self._filter_buttons:
            self._filter_buttons[0].setChecked(True)
            prefix = filters[0][1]
            self._fill_combo(prefix, engine_name)

    def _apply_filter(self, lang_prefix: str, active_btn: QPushButton) -> None:
        for btn in self._filter_buttons:
            btn.setChecked(btn is active_btn)
        engine = "edge-tts" if any(
            v.get("engine") == "edge-tts" for v in self._all_voices
        ) else "kokoro"
        self._fill_combo(lang_prefix, engine)

    def _fill_combo(self, prefix: str, engine_name: str) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        for v in self._all_voices:
            match = False
            if not prefix:
                match = True
            elif engine_name == "edge-tts":
                match = v.get("language", "").startswith(prefix)
            else:
                match = v.get("lang_code", "") == prefix
            if match:
                self._combo.addItem(v["name"], v["id"])
        self._combo.blockSignals(False)
        if self._combo.count() > 0:
            self.voice_changed.emit(self._combo.currentData() or "")

    def _on_combo_changed(self, _index: int) -> None:
        vid = self._combo.currentData()
        if vid:
            self.voice_changed.emit(vid)

    def current_voice_id(self) -> str:
        return self._combo.currentData() or ""

    def set_voice(self, voice_id: str) -> None:
        idx = self._combo.findData(voice_id)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)


# ── Ana TTS Sayfası ───────────────────────────────────────────────────────────

class TtsPage(QWidget):
    """Text-to-Speech seslendirme sayfasi."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._state        = self.ctx.app_state
        self._worker       = None
        self._regen_workers: List = []   # tekli/seçili worker'ları GC'den korur
        self._live_workers: list = []
        self._cache        = None
        self._segment_items: List[SegmentListItem] = []
        self._selected_idx: Optional[int] = None
        self._total_done   = 0
        self._total_errors = 0

        self._build_ui()
        self._connect_app_signals()
        self._load_engines()
        logger.debug("TtsPage olusturuldu.")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

# Toolbar butonlari (header action olarak degil; ayri toolbar)
        self._btn_synthesize = QPushButton("Tumunu Seslendir")
        self._btn_synthesize.setObjectName("primaryButton")
        self._btn_synthesize.setIcon(Icons.get(Icons.TTS, color="#1a1b26"))
        self._btn_synthesize.setIconSize(QSize(16, 16))
        self._btn_synthesize.setMinimumWidth(180)
        self._btn_synthesize.clicked.connect(self._start_synthesis)

        self._btn_export = QPushButton("Disa Aktar")
        self._btn_export.setObjectName("secondaryBtn")
        self._btn_export.setIcon(Icons.get(Icons.EXPORT))
        self._btn_export.setIconSize(QSize(16, 16))
        self._btn_export.setMinimumWidth(130)
        self._btn_export.setToolTip("Uretilen tum ses dosyalarini secilen klasore kaydet")
        self._btn_export.clicked.connect(self._export_audio_files)

        header = PageHeader(
            "Seslendirme",
            "Script metnini sesli anlatima donusturun.",
            actions=[self._btn_export, self._btn_synthesize],
        )
        root.addWidget(header)

        # Toolbar
        root.addWidget(self._make_toolbar())

        # Ayarlar paneli
        self._settings_panel = SettingsPanel()
        root.addWidget(self._settings_panel)

        # Ana splitter
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._make_segment_list())
        splitter.addWidget(self._make_bottom_panel())
        splitter.setSizes([500, 260])
        root.addWidget(splitter, 1)

        root.addWidget(self._make_status_bar())

    def _make_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("sectionFrame")
        v = QVBoxLayout(bar)
        v.setContentsMargins(24, 8, 24, 8)
        v.setSpacing(10)

        # Row 1: Chapter and Engine selection
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(12)

        row1.addWidget(QLabel("Bolum:"))
        self._chapter_combo = QComboBox()
        self._chapter_combo.setMinimumWidth(170)
        self._chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        row1.addWidget(self._chapter_combo, 1)

        row1.addWidget(self._vsep())

        row1.addWidget(QLabel("Motor:"))
        self._engine_combo = QComboBox()
        self._engine_combo.setMinimumWidth(220)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        row1.addWidget(self._engine_combo, 2)

        # Kokoro hata butonu (gizli baslangicta)
        self._btn_kokoro_help = QPushButton("Kokoro Sorunu")
        self._btn_kokoro_help.setObjectName("dangerButton")
        self._btn_kokoro_help.setIcon(Icons.get(Icons.WRENCH, color="#f7768e"))
        self._btn_kokoro_help.setFixedWidth(130)
        self._btn_kokoro_help.setVisible(False)
        self._btn_kokoro_help.clicked.connect(self._open_kokoro_setup)
        row1.addWidget(self._btn_kokoro_help)

        row1.addStretch()
        v.addLayout(row1)

        # Separator line between rows
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        v.addWidget(sep)

        # Row 2: Voice selection and actions
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(12)

        row2.addWidget(QLabel("Ses:"))
        self._voice_selector = FilteredVoiceCombo()
        self._voice_selector.voice_changed.connect(self._on_voice_changed)
        row2.addWidget(self._voice_selector)

        self._btn_preview_voice = QPushButton("Onizle")
        self._btn_preview_voice.setObjectName("secondaryBtn")
        self._btn_preview_voice.setIcon(Icons.get(Icons.PLAY))
        self._btn_preview_voice.setFixedWidth(90)
        self._btn_preview_voice.clicked.connect(self._open_voice_preview)
        row2.addWidget(self._btn_preview_voice)

        row2.addStretch()

        self._btn_cancel = QPushButton("Iptal")
        self._btn_cancel.setObjectName("dangerButton")
        self._btn_cancel.setIcon(Icons.get(Icons.STOP, color="#f7768e"))
        self._btn_cancel.setFixedWidth(80)
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_synthesis)
        row2.addWidget(self._btn_cancel)

        v.addLayout(row2)
        return bar

    def _vsep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("separator")
        sep.setFixedWidth(1)
        return sep

    def _make_segment_list(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(24, 14, 24, 8)
        v.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Segmentler"))
        self._lbl_seg_count = QLabel("")
        self._lbl_seg_count.setObjectName("mutedLabel")
        hdr.addWidget(self._lbl_seg_count)
        hdr.addStretch()

        self._btn_regen_selected = QPushButton("Secilileri Yeniden Uret")
        self._btn_regen_selected.setObjectName("secondaryBtn")
        self._btn_regen_selected.setIcon(Icons.get(Icons.REFRESH))
        self._btn_regen_selected.setFixedHeight(28)
        self._btn_regen_selected.clicked.connect(self._regen_selected)
        hdr.addWidget(self._btn_regen_selected)
        v.addLayout(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_container)
        v.addWidget(scroll, 1)
        return container

    def _make_bottom_panel(self) -> QWidget:
        self._bottom_panel = QWidget()
        self._bottom_panel.setObjectName("bottomPanel")
        v = QVBoxLayout(self._bottom_panel)
        v.setContentsMargins(24, 14, 24, 14)
        v.setSpacing(10)

        self._lbl_segment_title = QLabel("Segment secin")
        self._lbl_segment_title.setObjectName("headingLabel")
        v.addWidget(self._lbl_segment_title)

        self._txt_segment_text = QTextEdit()
        self._txt_segment_text.setReadOnly(True)
        self._txt_segment_text.setMaximumHeight(80)
        self._txt_segment_text.textChanged.connect(self._on_segment_text_edited)
        v.addWidget(self._txt_segment_text)

        self._player = AudioPlayerWidget()
        v.addWidget(self._player)

        bottom_row = QHBoxLayout()
        self._lbl_total_dur = QLabel("Toplam sure: --")
        self._lbl_total_dur.setObjectName("mutedLabel")
        bottom_row.addWidget(self._lbl_total_dur)
        bottom_row.addStretch()
        v.addLayout(bottom_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("%p%  %v/%m segment")
        v.addWidget(self._progress_bar)

        self._lbl_progress_detail = QLabel("")
        self._lbl_progress_detail.setObjectName("mutedLabel")
        v.addWidget(self._lbl_progress_detail)
        return self._bottom_panel

    def _make_status_bar(self) -> QWidget:
        w = QWidget()
        w.setObjectName("bottomPanel")
        h = QHBoxLayout(w)
        h.setContentsMargins(24, 4, 24, 4)
        self._lbl_status = QLabel("Hazir")
        self._lbl_status.setObjectName("mutedLabel")
        h.addWidget(self._lbl_status)
        h.addStretch()
        return w

    # ── Engine / Voice yukleme ────────────────────────────────────

    def _load_engines(self) -> None:
        self._engine_combo.blockSignals(True)
        self._engine_combo.clear()

        try:
            from core.tts_engine import TTSManager
            mgr = TTSManager.get_instance()
            available = mgr.get_available_engines()
        except Exception:
            self._engine_combo.addItem("Edge-TTS (Online, Ucretsiz)", "edge-tts")
            self._engine_combo.addItem("Kokoro TTS (Offline)", "kokoro")
            self._engine_combo.blockSignals(False)
            return

        for eng in available:
            if eng["available"]:
                label = eng["name"]
            else:
                label = f"{eng['name']}  [Yuklenemedi]"
            self._engine_combo.addItem(label, eng["id"])
            idx = self._engine_combo.count() - 1
            self._engine_combo.setItemData(idx, eng.get("description", ""), Qt.ItemDataRole.ToolTipRole)
            if not eng["available"]:
                # Devre dışı: seçilemez hale getir
                item = self._engine_combo.model().item(idx)
                if item is not None:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

        # Varsayilan engine
        default_engine = self._state.get_setting("defaults", "tts_engine") or "edge-tts"
        idx = self._engine_combo.findData(default_engine)
        if idx >= 0:
            self._engine_combo.setCurrentIndex(idx)

        # Varsayılan hız (Ayarlar > TTS) → Kokoro/Edge paneline yansıt
        try:
            default_speed = self._state.get_setting("tts", "default_speed", default=1.0)
            self._settings_panel.apply_default_speed(default_speed)
        except Exception:
            pass

        self._engine_combo.blockSignals(False)
        self._on_engine_changed(self._engine_combo.currentIndex())

    def _load_voices_for_engine(self, engine_name: str) -> None:
        self._settings_panel.switch_engine(engine_name)

        # Kokoro hata butonu goster/gizle
        try:
            from core.tts_engine import TTSManager
            engine = TTSManager.get_instance().get_engine(engine_name)
            kokoro_ok = engine.is_available() if engine_name == "kokoro" else True
            self._btn_kokoro_help.setVisible(engine_name == "kokoro" and not kokoro_ok)
        except Exception:
            self._btn_kokoro_help.setVisible(False)

        try:
            from core.tts_engine import TTSManager
            engine = TTSManager.get_instance().get_engine(engine_name)
            voices = engine.list_voices()
        except Exception as exc:
            logger.error("Ses listesi yuklenemedi: %s", exc)
            voices = []

        self._voice_selector.load_voices(voices, engine_name)
        default_voice = self._state.get_setting("defaults", "tts_voice") or ""
        if default_voice:
            self._voice_selector.set_voice(default_voice)

    def _open_kokoro_setup(self) -> None:
        """Kokoro kurulum yardimcisini ac."""
        error = None
        try:
            from core.tts_engine import KokoroTTSEngine
            k = KokoroTTSEngine()
            error = k.get_error()
        except Exception:
            pass
        from ui.pages.settings_page import KokoroSetupDialog
        dlg = KokoroSetupDialog(error_message=error, parent=self)
        dlg.exec()

    # ── Chapter yukleme ───────────────────────────────────────────

    def _connect_app_signals(self) -> None:
        self._state.project_changed.connect(self._on_project_changed)
        self._state.chapter_changed.connect(self._on_chapter_changed_signal)

    def _on_project_changed(self, project) -> None:
        self._chapter_combo.blockSignals(True)
        self._chapter_combo.clear()
        if project:
            for ch in project.chapters:
                self._chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
        self._chapter_combo.blockSignals(False)
        active = self._state.current_chapter
        if active:
            self._load_chapter(active)

    def _on_chapter_changed_signal(self, chapter) -> None:
        if chapter:
            idx = self._chapter_combo.findData(chapter.id)
            if idx >= 0:
                self._chapter_combo.setCurrentIndex(idx)
            self._load_chapter(chapter)

    def _on_chapter_changed(self, _index: int) -> None:
        chapter_id = self._chapter_combo.currentData()
        if not chapter_id:
            return
        project = self._state.current_project
        if project:
            chapter = project.get_chapter(chapter_id)
            if chapter:
                self._state.current_chapter = chapter

    def _load_chapter(self, chapter) -> None:
        self._clear_list()
        self._total_done   = 0
        self._total_errors = 0

        if not chapter or not chapter.segments:
            self._lbl_seg_count.setText("(script uretilmemis)")
            self._set_status("Once script uretin.", warn=True)
            return

        self._lbl_seg_count.setText(f"({len(chapter.segments)} segment)")
        self._progress_bar.setMaximum(len(chapter.segments))
        self._progress_bar.setValue(0)

        project = self._state.current_project
        if project:
            import core.project_manager as pm_mod
            pf = pm_mod.get_project_dir(project)
            if pf:
                from core.tts_cache import TTSCache
                cache_dir = Path(pf) / "audio" / ".cache"
                self._cache = TTSCache(cache_dir)

        for i, seg in enumerate(chapter.segments):
            item = SegmentListItem(i, seg)
            item.play_clicked.connect(self._play_segment)
            item.regen_clicked.connect(self._regen_one_segment)
            item.edit_clicked.connect(self._edit_segment)
            self._load_thumbnail(item, chapter, seg)

            if seg.audio_path and Path(seg.audio_path).exists():
                item.set_status("done")
                self._total_done += 1
            else:
                item.set_status("waiting")

            self._list_layout.insertWidget(self._list_layout.count() - 1, item)
            self._segment_items.append(item)

        self._update_total_duration(chapter)
        self._set_status(f"Bolum yuklendi: {chapter.name} ({len(chapter.segments)} segment)")
        self._suggest_engine_for_chapter(chapter)

    def _load_thumbnail(self, item: SegmentListItem, chapter, seg) -> None:
        try:
            if seg.image_index < len(chapter.images):
                img = chapter.images[seg.image_index]
                thumb_path = img.thumbnail_path or img.path
                if thumb_path and Path(thumb_path).exists():
                    px = QPixmap(thumb_path)
                    if not px.isNull():
                        item.set_thumbnail(px)
        except Exception:
            pass

    def _suggest_engine_for_chapter(self, chapter) -> None:
        if not chapter.segments:
            return
        sample = " ".join(s.text for s in chapter.segments[:3])
        try:
            from core.tts_engine import TTSManager
            recommended = TTSManager.get_instance().recommend_engine(sample)
            current = self._engine_combo.currentData()
            if recommended != current:
                tip = "Turkce icin" if recommended == "edge-tts" else "Ingilizce icin"
                self._set_status(f"{tip} {recommended} onerilir.")
        except Exception:
            pass

    # ── Segment liste ─────────────────────────────────────────────

    def _clear_list(self) -> None:
        for item in self._segment_items:
            item.setParent(None)
            item.deleteLater()
        self._segment_items.clear()
        self._selected_idx = None
        self._lbl_segment_title.setText("Segment secin")
        self._txt_segment_text.clear()

    def _play_segment(self, idx: int) -> None:
        chapter = self._state.current_chapter
        if not chapter or idx >= len(chapter.segments):
            return
        seg = chapter.segments[idx]
        if seg.audio_path and Path(seg.audio_path).exists():
            self._selected_idx = idx
            self._update_bottom_panel(idx)
            self._player.load(seg.audio_path)
            self._player.play()

    def _edit_segment(self, idx: int) -> None:
        chapter = self._state.current_chapter
        if not chapter or idx >= len(chapter.segments):
            return
        self._selected_idx = idx
        self._update_bottom_panel(idx)
        self._txt_segment_text.setReadOnly(False)
        self._txt_segment_text.setFocus()

    def _on_segment_text_edited(self) -> None:
        if self._txt_segment_text.isReadOnly():
            return
        idx = self._selected_idx
        chapter = self._state.current_chapter
        if chapter is None or idx is None or idx >= len(chapter.segments):
            return
        chapter.segments[idx].text = self._txt_segment_text.toPlainText()
        if idx < len(self._segment_items):
            self._segment_items[idx].refresh()

    def _update_bottom_panel(self, idx: int) -> None:
        chapter = self._state.current_chapter
        if not chapter or idx >= len(chapter.segments):
            return
        seg = chapter.segments[idx]
        self._lbl_segment_title.setText(f"Segment #{idx + 1}")
        self._txt_segment_text.setReadOnly(True)
        self._txt_segment_text.setPlainText(seg.text)
        if seg.audio_path and Path(seg.audio_path).exists():
            self._player.load(seg.audio_path)

    def _update_total_duration(self, chapter) -> None:
        total = sum(s.duration for s in chapter.segments if s.duration)
        m, s = divmod(int(total), 60)
        self._lbl_total_dur.setText(f"Toplam sure: {m:02d}:{s:02d}")

    def _mix_kwargs(self, engine_name: str, voice_id: str) -> dict:
        mix = bool(self._settings_panel.mix_engines())
        if not mix:
            return {"mix_engines": False}
        if engine_name == "kokoro":
            narrator_voice = voice_id
            dialogue_voice = "en-US-AndrewNeural"
        else:
            dialogue_voice = voice_id
            narrator_voice = "am_adam"
        return {
            "mix_engines": True,
            "narrator_engine": "kokoro",
            "narrator_voice": narrator_voice,
            "dialogue_engine": "edge-tts",
            "dialogue_voice": dialogue_voice,
        }

    # ── Sentez ────────────────────────────────────────────────────

    def _start_synthesis(self) -> None:
        chapter = self._state.current_chapter
        if not chapter:
            QMessageBox.warning(self, "Uyari", "Aktif bolum secin.")
            return
        if not chapter.segments:
            QMessageBox.warning(self, "Uyari", "Once script uretin.")
            return

        engine_name = self._engine_combo.currentData()
        voice_id    = self._voice_selector.current_voice_id()

        if not voice_id:
            QMessageBox.warning(self, "Uyari", "Ses secin.")
            return

        try:
            from core.tts_engine import TTSManager
            engine = TTSManager.get_instance().get_engine(engine_name)
            if not engine.is_available():
                err = engine.get_error() or "Engine kurulu degil."
                QMessageBox.critical(
                    self, "Engine Kullanilamiyor",
                    f"'{engine_name}' kullanılamıyor:\n\n{err}\n\n"
                    "Kokoro Kurulum Yardimcisi icin Toolbar'daki 'Kokoro Sorunu' butonuna tiklayin."
                )
                return
        except Exception as exc:
            QMessageBox.critical(self, "Hata", str(exc))
            return

        # Kokoro + Türkce uyarisi
        if engine_name == "kokoro":
            sample = " ".join(s.text for s in chapter.segments[:3])
            try:
                from core.tts_engine import TTSManager
                lang = TTSManager.get_instance().detect_language(sample)
                if lang == "tr":
                    resp = QMessageBox.question(
                        self, "Dil Uyarisi",
                        "Kokoro TTS Turkcеyi desteklemiyor.\nEdge-TTS'e gecmek ister misiniz?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if resp == QMessageBox.StandardButton.Yes:
                        idx = self._engine_combo.findData("edge-tts")
                        if idx >= 0:
                            self._engine_combo.setCurrentIndex(idx)
                        return
            except Exception:
                pass

        params = self._settings_panel.get_params(engine_name)
        # Log: kullanıcı hız ayarının worker'a gittiğini doğrula
        import logging as _logging
        _logging.getLogger(__name__).info(
            "TTS başlatılıyor: engine=%s params=%s", engine_name, params
        )
        if engine_name == "kokoro":
            self._set_status(f"Kokoro hız: {params.get('speed', 1.0)}x — sentezleniyor…")

        project = self._state.current_project
        if project:
            import core.project_manager as pm_mod
            pf = pm_mod.get_project_dir(project)
            audio_dir = Path(str(pf) if pf else "projects") / "audio" / chapter.id
        else:
            audio_dir = Path("projects") / "audio" / chapter.id

        for item in self._segment_items:
            item.set_status("waiting")

        from ui.workers.tts_worker import TTSWorker
        from ui.workers.thread_utils import start_worker
        self._worker = TTSWorker(
            chapter=chapter, engine_name=engine_name,
            voice=voice_id, audio_dir=str(audio_dir),
            params=params, cache=self._cache,
            parent=self,
            **self._mix_kwargs(engine_name, voice_id),
        )
        self._worker.segment_started.connect(self._on_segment_started)
        self._worker.segment_done.connect(self._on_segment_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.error.connect(self._on_segment_error)
        self._worker.finished.connect(self._on_finished)

        self._btn_synthesize.setEnabled(False)
        self._btn_cancel.setVisible(True)
        self._progress_bar.setMaximum(len(chapter.segments))
        self._progress_bar.setValue(0)
        self._total_done   = 0
        self._total_errors = 0

        engine_display = "Edge-TTS" if engine_name == "edge-tts" else "Kokoro TTS"
        self._set_status(f"{engine_display} ile {len(chapter.segments)} segment seslendiriliyor...")
        start_worker(self, self._worker)

    def _cancel_synthesis(self) -> None:
        if self._worker and self._worker.isRunning():
            from ui.workers.thread_utils import abort_worker
            abort_worker(self, self._worker)
            self._set_status("Iptal edildi.")
        self._btn_cancel.setVisible(False)
        self._btn_synthesize.setEnabled(True)

    def _regen_one_segment(self, idx: int) -> None:
        chapter = self._state.current_chapter
        if not chapter or idx >= len(chapter.segments):
            return

        engine_name = self._engine_combo.currentData()
        voice_id    = self._voice_selector.current_voice_id()
        if not voice_id:
            return

        seg = chapter.segments[idx]
        params = self._settings_panel.get_params(engine_name)

        project = self._state.current_project
        if project:
            import core.project_manager as pm_mod
            pf = pm_mod.get_project_dir(project)
            audio_dir = Path(str(pf) if pf else "projects") / "audio" / chapter.id
        else:
            audio_dir = Path("projects") / "audio" / chapter.id

        audio_dir.mkdir(parents=True, exist_ok=True)

        if idx < len(self._segment_items):
            self._segment_items[idx].set_status("processing")

        from ui.workers.tts_worker import TTSWorker
        from ui.workers.thread_utils import start_worker

        worker = TTSWorker(
            chapter=chapter, engine_name=engine_name,
            voice=voice_id, audio_dir=str(audio_dir),
            params=params, cache=self._cache,
            only_indices=[idx],
            parent=self,
            **self._mix_kwargs(engine_name, voice_id),
        )
        worker.segment_done.connect(
            lambda i, path, dur, real_idx=idx: self._on_single_done(real_idx, path, dur)
        )
        worker.error.connect(
            lambda i, msg, real_idx=idx: self._on_segment_error(real_idx, msg)
        )
        # Worker'ı self'e bağla — fonksiyon dönünce GC tarafından yok edilmemesi için
        self._regen_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._regen_workers.remove(w) if w in self._regen_workers else None)
        start_worker(self, worker)
        self._set_status(f"Segment #{idx + 1} yeniden seslendiriliyor...")

    def _on_single_done(self, idx: int, path: str, duration: float) -> None:
        chapter = self._state.current_chapter
        if chapter and idx < len(chapter.segments):
            chapter.segments[idx].audio_path = path
            chapter.segments[idx].duration   = duration
        if idx < len(self._segment_items):
            self._segment_items[idx].set_status("done")
            self._segment_items[idx].refresh()
        self._save_project()

    def _regen_selected(self) -> None:
        for i, item in enumerate(self._segment_items):
            if item._status in ("error", "waiting"):
                self._regen_one_segment(i)

    # ── Worker sinyalleri ─────────────────────────────────────────

    def _on_segment_started(self, idx: int) -> None:
        if idx < len(self._segment_items):
            self._segment_items[idx].set_status("processing")

    def _on_segment_done(self, idx: int, path: str, duration: float) -> None:
        chapter = self._state.current_chapter
        if chapter and idx < len(chapter.segments):
            chapter.segments[idx].audio_path = path
            chapter.segments[idx].duration   = duration
        if idx < len(self._segment_items):
            self._segment_items[idx].set_status("done")
            self._segment_items[idx].refresh()
        self._total_done += 1
        self._progress_bar.setValue(self._progress_bar.value() + 1)
        if chapter:
            self._update_total_duration(chapter)
        self._save_project()

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self._lbl_progress_detail.setText(message)
        if total > 0:
            pct = int(current / total * 100)
            self._set_status(f"{message}  |  %{pct}  ({current}/{total})")

    def _on_segment_error(self, idx: int, msg: str) -> None:
        if idx >= 0 and idx < len(self._segment_items):
            self._segment_items[idx].set_status("error")
        self._total_errors += 1
        logger.error("TTS segment %d hatasi: %s", idx, msg)

        # 403 hatası için özel uyarı — conversation başına yalnızca bir kez göster
        if "403" in msg and not getattr(self, "_403_warned", False):
            self._403_warned = True
            QMessageBox.warning(
                self,
                "Edge-TTS Baglanti Hatasi (403)",
                "Microsoft Edge-TTS servisine baglanılamiyor.\n\n"
                "Olası nedenler:\n"
                "• edge-tts paketi guncel degil\n"
                "• Gecici Microsoft sunucu engellemesi\n"
                "• Cok fazla istek (rate limit)\n\n"
                "Cozum:\n"
                "1. Terminalde calistirin:\n"
                "   pip install --upgrade edge-tts\n\n"
                "2. Uygulamayi yeniden baslatın\n\n"
                "3. Hala calismiyorsa 5-10 dakika bekleyin"
            )

    def _on_finished(self) -> None:
        self._btn_cancel.setVisible(False)
        self._btn_synthesize.setEnabled(True)
        self._worker = None

        chapter = self._state.current_chapter
        total   = len(chapter.segments) if chapter else 0
        errors  = self._total_errors

        if errors == 0:
            total_sec = sum(s.duration for s in chapter.segments if chapter) if chapter else 0
            m, s = divmod(int(total_sec), 60)
            msg = f"{total} segment seslendirildi (toplam {m:02d}:{s:02d})"
        else:
            msg = f"{self._total_done} segment basarili, {errors} segment hatali"

        self._set_status(msg)
        self._state.status_message.emit(msg)
        self._progress_bar.setValue(self._total_done)

    # ── Yardimci slot'lar ─────────────────────────────────────────

    def _on_engine_changed(self, _index: int) -> None:
        engine_name = self._engine_combo.currentData()
        if engine_name:
            self._load_voices_for_engine(engine_name)

    def _on_voice_changed(self, voice_id: str) -> None:
        pass

    def _open_voice_preview(self) -> None:
        engine_name = self._engine_combo.currentData() or "edge-tts"
        preview_params = self._settings_panel.get_params(engine_name)
        current_voice = self._voice_selector.current_voice_id()
        try:
            from core.tts_engine import TTSManager
            engine = TTSManager.get_instance().get_engine(engine_name)
            if not engine.is_available():
                err = engine.get_error() or "Engine kullanilamiyor."
                QMessageBox.warning(self, "Uyari", err)
                return
            voices = engine.list_voices()
        except Exception as exc:
            QMessageBox.critical(self, "Hata", str(exc))
            return

        from ui.widgets.voice_preview import VoicePreviewDialog
        dlg = VoicePreviewDialog(
            engine_name=engine_name, voices=voices,
            current_voice=current_voice, params=preview_params, parent=self,
        )
        dlg.voice_selected.connect(self._voice_selector.set_voice)
        dlg.exec()

    def _save_project(self) -> None:
        try:
            project = self._state.current_project
            if project:
                import core.project_manager as pm_mod
                pm_mod.save_project(project)
        except Exception as exc:
            logger.error("Proje kaydedilemedi: %s", exc)
            QMessageBox.warning(self, "Kayıt hatası", f"Proje kaydedilemedi:\n{exc}")

    def _set_status(self, msg: str, warn: bool = False) -> None:
        self._lbl_status.setText(msg)
        self._lbl_status.setObjectName("warningLabel" if warn else "mutedLabel")
        self._lbl_status.style().unpolish(self._lbl_status)
        self._lbl_status.style().polish(self._lbl_status)

    def _export_audio_files(self) -> None:
        """Uretilen tum ses dosyalarini kullanicinin sectigi klasore kopyalar."""
        chapter = self._state.current_chapter
        if not chapter or not chapter.segments:
            QMessageBox.warning(self, "Uyari", "Aktif bolum yok veya segment bulunamadi.")
            return

        ready_segments = [
            (i, seg) for i, seg in enumerate(chapter.segments)
            if seg.audio_path and Path(seg.audio_path).exists()
        ]

        if not ready_segments:
            QMessageBox.information(
                self, "Disa Aktarma",
                "Henuz seslendirilmis segment yok.\nOnce 'Tumunu Seslendir' ile sesleri olusturun."
            )
            return

        export_dir = QFileDialog.getExistingDirectory(
            self,
            "Ses Dosyalarini Kaydet - Klasor Sec",
            str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )

        if not export_dir:
            return  # Kullanici iptal etti

        import shutil
        from core.exporter import audio_export_stem

        dest_dir = Path(export_dir)
        chapter_name = getattr(chapter, "name", "bolum")

        copied = 0
        errors = []

        for i, seg in ready_segments:
            src = Path(seg.audio_path)
            ext = src.suffix or ".wav"
            dest_name = f"{audio_export_stem(chapter_name, i + 1)}{ext}"
            dest_path = dest_dir / dest_name
            try:
                shutil.copy2(str(src), str(dest_path))
                copied += 1
            except Exception as exc:
                errors.append(f"Segment #{i + 1}: {exc}")
                logger.error("Export hatasi segment %d: %s", i + 1, exc)

        if errors:
            err_detail = "\n".join(errors[:5])
            if len(errors) > 5:
                err_detail += f"\n... ve {len(errors) - 5} hata daha"
            QMessageBox.warning(
                self, "Disa Aktarma Tamamlandi (Hatalarla)",
                f"{copied} dosya kopyalandi, {len(errors)} hata olustu:\n\n{err_detail}\n\nHedef: {export_dir}"
            )
        else:
            QMessageBox.information(
                self, "Disa Aktarma Basarili",
                f"{copied} ses dosyasi basariyla kopyalandi.\n\nHedef klasor:\n{export_dir}"
            )

        self._set_status(f"{copied} ses dosyasi disa aktarildi → {export_dir}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        project = self._state.current_project
        if project:
            self._on_project_changed(project)
        chapter = self._state.current_chapter
        if chapter:
            if not self._segment_items:
                self._load_chapter(chapter)
            else:
                for item in self._segment_items:
                    item.refresh()

    def hideEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            from ui.workers.thread_utils import abort_worker
            abort_worker(self, self._worker)
        if not self._txt_segment_text.isReadOnly():
            self._save_project()
        super().hideEvent(event)
