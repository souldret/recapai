"""
RecapAI - Render Sayfası (tam implementasyon).
"""

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QSpinBox, QDoubleSpinBox, QProgressBar,
    QCheckBox, QLineEdit, QFileDialog, QTextEdit, QSlider,
    QScrollArea, QSplitter, QSizePolicy, QToolButton, QGroupBox,
    QListWidget, QListWidgetItem, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QUrl, pyqtSlot, QSize
from PyQt6.QtGui import QFont
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from core.context import AppContext
from ui.widgets.color_picker import ColorPickerButton
from ui.utils.icons import Icons

logger = logging.getLogger(__name__)


# ── Preset tanımları ──────────────────────────────────────────────────────────

PRESETS = {
    "youtube": {
        "label": "YouTube",
        "icon": Icons.YOUTUBE,
        "resolution": [1920, 1080],
        "fps": 30,
        "bitrate": "8000k",
        "codec": "libx264",
    },
    "shorts": {
        "label": "Shorts",
        "icon": Icons.TIKTOK,
        "resolution": [1080, 1920],
        "fps": 30,
        "bitrate": "6000k",
        "codec": "libx264",
    },
    "cinema": {
        "label": "Cinema",
        "icon": Icons.CINEMA,
        "resolution": [3840, 2160],
        "fps": 24,
        "bitrate": "20000k",
        "codec": "libx264",
    },
}

RESOLUTION_OPTIONS = [
    ("1920×1080 (Full HD)", [1920, 1080]),
    ("1280×720 (HD)", [1280, 720]),
    ("1080×1920 (Dikey/Shorts)", [1080, 1920]),
    ("3840×2160 (4K)", [3840, 2160]),
    ("2560×1440 (2K)", [2560, 1440]),
    ("Özel...", None),
]

TRANSITION_OPTIONS = [
    # Özel
    ("Rastgele",                    "random"),
    ("Yok",                         "none"),
    # Temel
    ("Fade (Soluk)",                "fade"),
    ("Dissolve (Çözülme)",          "dissolve"),
    # Yatay / Dikey Kayma
    ("Slide ← Sol",                 "slide"),
    ("Slide → Sağ",                 "slideright"),
    ("Slide ↑ Yukarı",              "slideup"),
    ("Slide ↓ Aşağı",               "slidedown"),
    # Cover — üzerine kapanma
    ("Cover ← Sol",                 "coverleft"),
    ("Cover → Sağ",                 "coverright"),
    ("Cover ↑ Yukarı",              "coverup"),
    ("Cover ↓ Aşağı",               "coverdown"),
    # Wipe — standart
    ("Wipe → Sağ",                  "wipe"),
    ("Wipe ← Sol",                  "wipeleft"),
    ("Wipe ↑ Yukarı",               "wipeup"),
    ("Wipe ↓ Aşağı",                "wipedown"),
    # Wipe — köşeden
    ("Wipe ↖ Sol Üst",              "wipetl"),
    ("Wipe ↗ Sağ Üst",              "wipetr"),
    ("Wipe ↙ Sol Alt",              "wipebl"),
    ("Wipe ↘ Sağ Alt",              "wipebr"),
    # Çapraz (Diagonal)
    ("Diagonal ↖ Sol Üst",          "diagtl"),
    ("Diagonal ↗ Sağ Üst",          "diagtr"),
    ("Diagonal ↙ Sol Alt",          "diagbl"),
    ("Diagonal ↘ Sağ Alt",          "diagbr"),
    # Kutu efekti
    ("Kutu İçeri (Yatay)",          "horzopen"),
    ("Kutu Dışarı (Yatay)",         "horzclose"),
    ("Kutu İçeri (Dikey)",          "vertopen"),
    ("Kutu Dışarı (Dikey)",         "vertclose"),
    # Zoom / Daire
    ("Zoom İçeri",                  "zoom"),
    ("Daire Açılır",                "circleopen"),
    ("Daire Kapanır",               "circleclose"),
    # Diğer
    ("Radial (Döngüsel)",           "radial"),
    ("Pikselleşme",                 "pixelize"),
    ("Yatay Sıkıştırma",            "squeezeh"),
    ("Dikey Sıkıştırma",            "squeezev"),
]
BACKGROUND_EFFECT_OPTIONS = [
    ("Yok (Siyah)", "none"),
    ("Blur Arka Plan", "blur"),
    ("Gradient (Üstten Alta)", "gradient_tb"),
    ("Gradient (Soldan Sağa)", "gradient_lr"),
    ("Vinyette + Blur", "vignette_blur"),
    ("Renk Tonu (Sinematik)", "cinematic"),
]
FONT_OPTIONS = ["Arial", "Helvetica", "Verdana", "Times New Roman", "Courier New", "Roboto"]
CODEC_OPTIONS = [("libx264 (H.264)", "libx264"), ("libx265 (H.265 / HEVC)", "libx265")]
FPS_OPTIONS = [24, 30, 60]


# ─────────────────────────────────────────────────────────────────────────────
# Accordion section widget
# ─────────────────────────────────────────────────────────────────────────────

class AccordionSection(QWidget):
    """Açılıp kapanabilen bölüm widget'ı."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._expanded = True
        self._build_ui(title)

    def _build_ui(self, title: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Başlık satırı
        self._header = QPushButton(f"▼  {title}")
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.setObjectName("secondaryBtn")
        self._header.clicked.connect(self._toggle)
        layout.addWidget(self._header)

        # İçerik widget
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 8, 12, 12)
        self._content_layout.setSpacing(10)
        layout.addWidget(self._content)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        prefix = "▼" if self._expanded else "▶"
        text = self._header.text()
        new_text = prefix + text[1:]
        self._header.setText(new_text)

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._content_layout.addLayout(layout)

    def content_layout(self):
        return self._content_layout


# ─────────────────────────────────────────────────────────────────────────────
# RenderPage
# ─────────────────────────────────────────────────────────────────────────────

class RenderPage(QWidget):
    """Tam render sayfası - ayarlar, önizleme, render kontrolleri."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._app_state = self.ctx.app_state
        self._render_worker = None
        self._preview_worker = None
        self._output_path: Optional[str] = None

        # Altyazı renk seçiciler (build sonrası referans için)
        self._sub_color_picker: Optional[ColorPickerButton] = None
        self._sub_stroke_picker: Optional[ColorPickerButton] = None

        self._build_ui()
        self._connect_signals()
        logger.debug("RenderPage oluşturuldu.")

    # ─────────────────────────────────────────────────────────────────────────
    # UI Oluşturma
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_toolbar())

        # ── Ana splitter (sol: ayarlar | sağ: önizleme+render) ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        

        # Sol: ayarlar scroll alanı
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        left_scroll.setWidget(self._make_settings_panel())
        splitter.addWidget(left_scroll)

        # Sağ: önizleme + render
        splitter.addWidget(self._make_preview_render_panel())
        splitter.setSizes([420, 600])

        root.addWidget(splitter, 1)

    def _make_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("sectionFrame")
        hbox = QHBoxLayout(bar)
        hbox.setContentsMargins(20, 8, 20, 8)
        hbox.setSpacing(16)

        # Başlık
        title = QLabel("Render")
        title.setObjectName("pageTitle")
        hbox.addWidget(title)

        # Bölüm seçici
        hbox.addWidget(QLabel("Bölüm:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(200)
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_selected)
        hbox.addWidget(self.chapter_combo)

        hbox.addSpacing(16)

        # Preset butonları
        preset_lbl = QLabel("Hızlı Preset:")
        preset_lbl.setObjectName("pageSubtitle")
        hbox.addWidget(preset_lbl)

        for key, preset in PRESETS.items():
            btn = QPushButton(" " + preset["label"])
            btn.setFixedHeight(36)
            btn.setMinimumWidth(100)
            btn.setIcon(Icons.get(preset["icon"]))
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(lambda checked, k=key: self._apply_preset(k))
            hbox.addWidget(btn)

        hbox.addSpacing(16)

        # Kullanıcı preset'leri (kaydet/yükle/sil)
        user_preset_lbl = QLabel("Benim Preset'lerim:")
        user_preset_lbl.setObjectName("pageSubtitle")
        hbox.addWidget(user_preset_lbl)

        self.user_preset_combo = QComboBox()
        self.user_preset_combo.setMinimumWidth(140)
        self.user_preset_combo.currentIndexChanged.connect(self._on_user_preset_selected)
        hbox.addWidget(self.user_preset_combo)

        btn_save_preset = QPushButton()
        btn_save_preset.setToolTip("Mevcut ayarları preset olarak kaydet")
        btn_save_preset.setIcon(Icons.get(Icons.SAVE))
        btn_save_preset.setFixedWidth(36)
        btn_save_preset.clicked.connect(self._save_user_preset)
        hbox.addWidget(btn_save_preset)

        btn_delete_preset = QPushButton()
        btn_delete_preset.setToolTip("Seçili preset'i sil")
        btn_delete_preset.setIcon(Icons.get(Icons.CLOSE))
        btn_delete_preset.setIconSize(QSize(14, 14))
        btn_delete_preset.setFixedWidth(28)
        btn_delete_preset.clicked.connect(self._delete_user_preset)
        hbox.addWidget(btn_delete_preset)

        self._refresh_user_presets()

        hbox.addStretch()
        return bar

    def _make_settings_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(8)

        # ── Video Ayarları ────────────────────────────────────────
        sec_video = AccordionSection("Video Ayarları")
        sec_video.add_widget(self._make_video_settings_content())
        vbox.addWidget(sec_video)

        # ── Geçiş Efektleri ───────────────────────────────────────
        sec_trans = AccordionSection("Geçiş Efektleri")
        sec_trans.add_widget(self._make_transition_content())
        vbox.addWidget(sec_trans)

        # ── Altyazı ───────────────────────────────────────────────
        sec_sub = AccordionSection("Altyazı")
        sec_sub.add_widget(self._make_subtitle_content())
        vbox.addWidget(sec_sub)

        # ── Ses ───────────────────────────────────────────────────
        sec_audio = AccordionSection("Ses")
        sec_audio.add_widget(self._make_audio_content())
        vbox.addWidget(sec_audio)

        # ── Intro / Outro ─────────────────────────────────────────
        sec_bookend = AccordionSection("Intro / Outro")
        sec_bookend.add_widget(self._make_bookend_content())
        vbox.addWidget(sec_bookend)

        vbox.addStretch()
        return container

    # ── Video ayarları içeriği ────────────────────────────────────

    def _make_video_settings_content(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        def row(label, widget):
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setObjectName("pageSubtitle")
            lbl.setFixedWidth(110)
            h.addWidget(lbl)
            h.addWidget(widget, 1)
            return h

        # Çözünürlük
        self.res_combo = QComboBox()
        for label, _ in RESOLUTION_OPTIONS:
            self.res_combo.addItem(label)
        self.res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        vbox.addLayout(row("Çözünürlük:", self.res_combo))

        # Custom resolution
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("W:"))
        self.custom_w = QSpinBox()
        self.custom_w.setRange(320, 7680)
        self.custom_w.setValue(1920)
        self.custom_w.setFixedWidth(80)
        custom_row.addWidget(self.custom_w)
        custom_row.addSpacing(8)
        custom_row.addWidget(QLabel("H:"))
        self.custom_h = QSpinBox()
        self.custom_h.setRange(240, 4320)
        self.custom_h.setValue(1080)
        self.custom_h.setFixedWidth(80)
        custom_row.addWidget(self.custom_h)
        custom_row.addStretch()
        self._custom_res_row = QWidget()
        self._custom_res_row.setLayout(custom_row)
        self._custom_res_row.setVisible(False)
        vbox.addWidget(self._custom_res_row)

        # FPS
        self.fps_combo = QComboBox()
        for fps in FPS_OPTIONS:
            self.fps_combo.addItem(f"{fps} fps")
        self.fps_combo.setCurrentIndex(1)  # 30 fps default
        vbox.addLayout(row("FPS:", self.fps_combo))

        # Codec
        self.codec_combo = QComboBox()
        for label, val in CODEC_OPTIONS:
            self.codec_combo.addItem(label, val)
        try:
            from core.ffmpeg_helper import get_available_gpu_encoders, GPU_ENCODER_LABELS
            for encoder in get_available_gpu_encoders():
                self.codec_combo.addItem(GPU_ENCODER_LABELS[encoder], encoder)
        except Exception:
            pass
        vbox.addLayout(row("Codec:", self.codec_combo))

        # Bitrate
        self.bitrate_edit = QLineEdit("8000k")
        self.bitrate_edit.setPlaceholderText("örn. 8000k veya 20M")
        vbox.addLayout(row("Bitrate:", self.bitrate_edit))

        return w

    # ── Geçiş içeriği ─────────────────────────────────────────────

    def _make_transition_content(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        def row(label, widget):
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setObjectName("pageSubtitle")
            lbl.setFixedWidth(120)
            h.addWidget(lbl)
            h.addWidget(widget, 1)
            return h

        # Geçiş tipi — çoklu seçim listesi
        trans_label = QLabel("Geçiş efektleri:")
        trans_label.setObjectName("pageSubtitle")
        vbox.addWidget(trans_label)

        self.transition_list = QListWidget()
        self.transition_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.transition_list.setFixedHeight(160)
        self.transition_list.setSpacing(1)

        # "Yok" özel item (tekil davranış)
        _none_item = QListWidgetItem("Yok (geçiş olmadan birleştir)")
        _none_item.setData(Qt.ItemDataRole.UserRole, "none")
        _none_item.setFlags(_none_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        _none_item.setCheckState(Qt.CheckState.Unchecked)
        self.transition_list.addItem(_none_item)

        # Gerçek geçişler (none ve random hariç)
        _skip = {"none", "random"}
        for lbl, key in TRANSITION_OPTIONS:
            if key in _skip:
                continue
            item = QListWidgetItem(lbl)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.transition_list.addItem(item)

        # Varsayılan: Fade seçili
        for i in range(self.transition_list.count()):
            it = self.transition_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == "fade":
                it.setCheckState(Qt.CheckState.Checked)
                break

        vbox.addWidget(self.transition_list)

        # Hızlı seçim butonları
        trans_btn_row = QHBoxLayout()
        btn_all = QPushButton("Tümünü Seç")
        btn_none = QPushButton("Temizle")
        btn_all.setFixedHeight(24)
        btn_none.setFixedHeight(24)
        btn_all.clicked.connect(self._trans_select_all)
        btn_none.clicked.connect(self._trans_clear_all)
        trans_btn_row.addWidget(btn_all)
        trans_btn_row.addWidget(btn_none)
        trans_btn_row.addStretch()
        vbox.addLayout(trans_btn_row)

        # Arka plan efekti
        self.bg_effect_combo = QComboBox()
        for label, val in BACKGROUND_EFFECT_OPTIONS:
            self.bg_effect_combo.addItem(label, val)
        vbox.addLayout(row("Arka plan efekti:", self.bg_effect_combo))

        # Geçiş süresi
        trans_row = QHBoxLayout()
        self.transition_dur = QSlider(Qt.Orientation.Horizontal)
        self.transition_dur.setRange(20, 200)   # 0.2 – 2.0 sn (×0.01)
        self.transition_dur.setValue(50)
        self.transition_dur_lbl = QLabel("0.50 sn")
        self.transition_dur_lbl.setFixedWidth(55)
        self.transition_dur_lbl.setObjectName("pageSubtitle")
        self.transition_dur.valueChanged.connect(
            lambda v: self.transition_dur_lbl.setText(f"{v/100:.2f} sn")
        )
        trans_row.addWidget(self.transition_dur, 1)
        trans_row.addWidget(self.transition_dur_lbl)
        vbox.addLayout(row("Geçiş süresi:", self._wrap(trans_row)))

        # Görsel Animasyon Modu
        motion_lbl = QLabel("Görsel Hareketi")
        motion_lbl.setObjectName("pageSubtitle")
        vbox.addWidget(motion_lbl)

        # 8 animasyon butonu — görseldeki gibi
        _MOTION_OPTIONS = [
            ("zoom_in",    "Zoom In"),
            ("zoom_out",   "Zoom Out"),
            ("slide_top",  "Slide Top"),
            ("slide_bot",  "Slide Bot"),
            ("slide_right","Slide Right"),
            ("slide_left", "Slide Left"),
            ("large_pan",  "Large Pan"),
            ("full_pan",   "Full Pan"),
        ]
        self._motion_buttons: Dict[str, QPushButton] = {}
        motion_grid1 = QHBoxLayout()
        motion_grid2 = QHBoxLayout()
        for i, (val, lbl_text) in enumerate(_MOTION_OPTIONS):
            btn = QPushButton(lbl_text)
            btn.setCheckable(True)
            btn.setFixedHeight(36)
            btn.setObjectName("motionBtn")
            btn.clicked.connect(lambda checked, v=val: self._select_motion(v))
            self._motion_buttons[val] = btn
            if i < 4:
                motion_grid1.addWidget(btn)
            else:
                motion_grid2.addWidget(btn)
        vbox.addLayout(motion_grid1)
        vbox.addLayout(motion_grid2)
        # Varsayılan: zoom_in seçili
        self._current_motion = "zoom_in"
        self._motion_buttons["zoom_in"].setChecked(True)

        # Ken Burns
        self.chk_ken_burns = QCheckBox("Hareket efekti aktif")
        self.chk_ken_burns.setChecked(True)
        self.chk_ken_burns.stateChanged.connect(self._on_ken_burns_toggled)
        vbox.addWidget(self.chk_ken_burns)

        # Yoğunluk (zoom/pan kuvveti)
        kb_row = QHBoxLayout()
        self.kb_intensity = QSlider(Qt.Orientation.Horizontal)
        self.kb_intensity.setRange(5, 30)   # 0.05 – 0.30
        self.kb_intensity.setValue(15)
        self.kb_intensity_lbl = QLabel("0.15")
        self.kb_intensity_lbl.setFixedWidth(40)
        self.kb_intensity_lbl.setObjectName("pageSubtitle")
        self.kb_intensity.valueChanged.connect(
            lambda v: self.kb_intensity_lbl.setText(f"{v/100:.2f}")
        )
        kb_row.addWidget(self.kb_intensity, 1)
        kb_row.addWidget(self.kb_intensity_lbl)
        self._kb_row_widget = self._wrap(kb_row)
        vbox.addLayout(row("Yoğunluk:", self._kb_row_widget))

        return w

    # ── Altyazı içeriği ───────────────────────────────────────────

    def _make_subtitle_content(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        def row(label, widget):
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setObjectName("pageSubtitle")
            lbl.setFixedWidth(100)
            h.addWidget(lbl)
            h.addWidget(widget, 1)
            return h

        self.chk_subtitles = QCheckBox("Altyazıları dahil et")
        self.chk_subtitles.setChecked(True)
        vbox.addWidget(self.chk_subtitles)

        self.sub_font_combo = QComboBox()
        self.sub_font_combo.addItems(FONT_OPTIONS)
        vbox.addLayout(row("Font:", self.sub_font_combo))

        self.sub_size_spin = QSpinBox()
        self.sub_size_spin.setRange(12, 120)
        self.sub_size_spin.setValue(48)
        vbox.addLayout(row("Boyut:", self.sub_size_spin))

        self._sub_color_picker = ColorPickerButton("#ffffff")
        vbox.addLayout(row("Renk:", self._sub_color_picker))

        self._sub_stroke_picker = ColorPickerButton("#000000")
        vbox.addLayout(row("Stroke:", self._sub_stroke_picker))

        self.sub_stroke_spin = QSpinBox()
        self.sub_stroke_spin.setRange(0, 10)
        self.sub_stroke_spin.setValue(2)
        vbox.addLayout(row("Stroke px:", self.sub_stroke_spin))

        self.sub_pos_combo = QComboBox()
        self.sub_pos_combo.addItems(["Alt (Bottom)", "Orta (Middle)", "Üst (Top)"])
        vbox.addLayout(row("Pozisyon:", self.sub_pos_combo))

        return w

    # ── Ses içeriği ───────────────────────────────────────────────

    def _make_audio_content(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        def row(label, widget):
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setObjectName("pageSubtitle")
            lbl.setFixedWidth(110)
            h.addWidget(lbl)
            h.addWidget(widget, 1)
            return h

        # BGM dosya
        bgm_row = QHBoxLayout()
        self.bgm_path_edit = QLineEdit()
        self.bgm_path_edit.setPlaceholderText("Arka plan müziği seçin...")
        self.bgm_path_edit.setReadOnly(True)
        bgm_btn = QPushButton()
        bgm_btn.setIcon(Icons.get(Icons.FOLDER))
        bgm_btn.setIconSize(QSize(16, 16))
        bgm_btn.setFixedWidth(36)
        bgm_btn.clicked.connect(self._browse_bgm)
        bgm_clear = QPushButton()
        bgm_clear.setIcon(Icons.get(Icons.CLOSE))
        bgm_clear.setIconSize(QSize(14, 14))
        bgm_clear.setFixedWidth(28)
        bgm_clear.clicked.connect(lambda: self.bgm_path_edit.clear())
        bgm_row.addWidget(self.bgm_path_edit, 1)
        bgm_row.addWidget(bgm_btn)
        bgm_row.addWidget(bgm_clear)
        vbox.addLayout(row("BGM:", self._wrap(bgm_row)))

        # BGM volume
        vol_row = QHBoxLayout()
        self.bgm_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.bgm_vol_slider.setRange(0, 100)
        self.bgm_vol_slider.setValue(15)
        self.bgm_vol_lbl = QLabel("15%")
        self.bgm_vol_lbl.setFixedWidth(40)
        self.bgm_vol_lbl.setObjectName("pageSubtitle")
        self.bgm_vol_slider.valueChanged.connect(
            lambda v: self.bgm_vol_lbl.setText(f"{v}%")
        )
        vol_row.addWidget(self.bgm_vol_slider, 1)
        vol_row.addWidget(self.bgm_vol_lbl)
        vbox.addLayout(row("Ses:", self._wrap(vol_row)))

        # Ducking
        self.chk_ducking = QCheckBox("BGM ducking (ana ses yükselince BGM düşer)")
        self.chk_ducking.setChecked(True)
        vbox.addWidget(self.chk_ducking)

        return w

    # ── Intro/Outro içeriği ───────────────────────────────────────

    def _make_bookend_content(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        def file_row(label, attr_name):
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setObjectName("pageSubtitle")
            lbl.setFixedWidth(60)
            edit = QLineEdit()
            edit.setPlaceholderText("Video dosyası seç...")
            edit.setReadOnly(True)
            setattr(self, attr_name, edit)
            btn = QPushButton()
            btn.setIcon(Icons.get(Icons.FOLDER))
            btn.setIconSize(QSize(16, 16))
            btn.setFixedWidth(36)
            btn.clicked.connect(lambda checked, e=edit: self._browse_video(e))
            clr = QPushButton()
            clr.setIcon(Icons.get(Icons.CLOSE))
            clr.setIconSize(QSize(14, 14))
            clr.setFixedWidth(28)
            clr.clicked.connect(edit.clear)
            h.addWidget(lbl)
            h.addWidget(edit, 1)
            h.addWidget(btn)
            h.addWidget(clr)
            return h

        vbox.addLayout(file_row("Intro:", "intro_path_edit"))
        vbox.addLayout(file_row("Outro:", "outro_path_edit"))

        # ── Watermark ─────────────────────────────────────────────
        wm_sep = QLabel("Watermark / Logo")
        wm_sep.setObjectName("pageSubtitle")
        vbox.addWidget(wm_sep)

        wm_row = QHBoxLayout()
        self.watermark_path_edit = QLineEdit()
        self.watermark_path_edit.setPlaceholderText("Logo/watermark görseli seç (PNG önerilir)...")
        self.watermark_path_edit.setReadOnly(True)
        wm_btn = QPushButton()
        wm_btn.setIcon(Icons.get(Icons.FOLDER))
        wm_btn.setIconSize(QSize(16, 16))
        wm_btn.setFixedWidth(36)
        wm_btn.clicked.connect(self._browse_watermark)
        wm_clr = QPushButton()
        wm_clr.setIcon(Icons.get(Icons.CLOSE))
        wm_clr.setIconSize(QSize(14, 14))
        wm_clr.setFixedWidth(28)
        wm_clr.clicked.connect(self.watermark_path_edit.clear)
        wm_row.addWidget(self.watermark_path_edit, 1)
        wm_row.addWidget(wm_btn)
        wm_row.addWidget(wm_clr)
        vbox.addLayout(wm_row)

        # Watermark konum + boyut
        wm_opts = QHBoxLayout()
        wm_opts.addWidget(QLabel("Konum:"))
        self.wm_pos_combo = QComboBox()
        for lbl, val in [("Sağ Alt", "br"), ("Sol Alt", "bl"), ("Sağ Üst", "tr"), ("Sol Üst", "tl")]:
            self.wm_pos_combo.addItem(lbl, val)
        wm_opts.addWidget(self.wm_pos_combo)
        wm_opts.addSpacing(12)
        wm_opts.addWidget(QLabel("Boyut %:"))
        self.wm_scale_spin = QSpinBox()
        self.wm_scale_spin.setRange(2, 30)
        self.wm_scale_spin.setValue(8)
        self.wm_scale_spin.setSuffix("%")
        self.wm_scale_spin.setFixedWidth(70)
        wm_opts.addWidget(self.wm_scale_spin)
        wm_opts.addSpacing(12)
        wm_opts.addWidget(QLabel("Opaklık:"))
        self.wm_opacity_spin = QSpinBox()
        self.wm_opacity_spin.setRange(10, 100)
        self.wm_opacity_spin.setValue(85)
        self.wm_opacity_spin.setSuffix("%")
        self.wm_opacity_spin.setFixedWidth(70)
        wm_opts.addWidget(self.wm_opacity_spin)
        wm_opts.addStretch()
        vbox.addLayout(wm_opts)

        return w

    # ── Sağ panel: önizleme + render ──────────────────────────────

    def _make_preview_render_panel(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(16, 16, 16, 16)
        vbox.setSpacing(12)

        # ── Video önizleme ────────────────────────────────────────
        preview_frame = QFrame()
        preview_frame.setObjectName("card")
        preview_vbox = QVBoxLayout(preview_frame)
        preview_vbox.setContentsMargins(8, 8, 8, 8)
        preview_vbox.setSpacing(8)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(200)
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        
        preview_vbox.addWidget(self._video_widget, 1)

        # Oynatıcı
        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(self._video_widget)

        # Oynatma kontrolleri
        ctrl_row = QHBoxLayout()
        self.btn_play_preview = QPushButton("  Oynat")
        self.btn_play_preview.setIcon(Icons.get(Icons.PLAY))
        self.btn_play_preview.setIconSize(QSize(16, 16))
        self.btn_play_preview.setEnabled(False)
        self.btn_play_preview.clicked.connect(self._toggle_playback)
        ctrl_row.addWidget(self.btn_play_preview)

        self.btn_gen_preview = QPushButton("10sn Onizleme Uret")
        self.btn_gen_preview.clicked.connect(self._generate_preview)
        ctrl_row.addWidget(self.btn_gen_preview)
        ctrl_row.addStretch()
        preview_vbox.addLayout(ctrl_row)

        # Tahmini süre ve boyut
        self.lbl_estimate = QLabel("Süre: —  |  Tahmini boyut: —")
        self.lbl_estimate.setObjectName("pageSubtitle")
        preview_vbox.addWidget(self.lbl_estimate)

        vbox.addWidget(preview_frame, 1)

        # ── Output yolu ───────────────────────────────────────────
        out_frame = QFrame()
        out_frame.setObjectName("card")
        out_hbox = QHBoxLayout(out_frame)
        out_hbox.setContentsMargins(12, 8, 12, 8)

        out_hbox.addWidget(QLabel("Çıktı:"))
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setPlaceholderText("Çıktı yolu otomatik belirlenir...")
        out_hbox.addWidget(self.output_path_edit, 1)

        btn_browse_out = QPushButton()
        btn_browse_out.setIcon(Icons.get(Icons.FOLDER))
        btn_browse_out.setIconSize(QSize(16, 16))
        btn_browse_out.setFixedWidth(36)
        btn_browse_out.clicked.connect(self._browse_output)
        out_hbox.addWidget(btn_browse_out)
        vbox.addWidget(out_frame)

        # ── Render başlat butonu ──────────────────────────────────
        render_row = QHBoxLayout()
        self.btn_render = QPushButton("RENDER BAŞLAT")
        self.btn_render.setObjectName("primaryButton")
        self.btn_render.setMinimumHeight(48)
        self.btn_render.clicked.connect(self._start_render)
        render_row.addWidget(self.btn_render, 1)

        self.btn_cancel = QPushButton("  İptal")
        self.btn_cancel.setIcon(Icons.get(Icons.STOP, color="#ef4444"))
        self.btn_cancel.setIconSize(QSize(16, 16))
        self.btn_cancel.setMinimumHeight(48)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setObjectName("secondaryBtn")
        self.btn_cancel.clicked.connect(self._cancel_render)
        render_row.addWidget(self.btn_cancel)
        vbox.addLayout(render_row)

        # ── Progress bar ──────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(20)
        vbox.addWidget(self.progress_bar)

        self.lbl_eta = QLabel("Hazır")
        self.lbl_eta.setObjectName("pageSubtitle")
        vbox.addWidget(self.lbl_eta)

        # ── Log alanı ────────────────────────────────────────────
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        self.log_area.setObjectName("logView")
        vbox.addWidget(self.log_area)

        # ── Render sonrası butonlar ───────────────────────────────
        self._post_render_widget = QWidget()
        post_row = QHBoxLayout(self._post_render_widget)
        post_row.setContentsMargins(0, 0, 0, 0)

        self.btn_open_folder = QPushButton("Klasörü Aç")
        self.btn_open_folder.clicked.connect(self._open_output_folder)
        post_row.addWidget(self.btn_open_folder)

        self.btn_open_video = QPushButton("  Videoyu Oynat")
        self.btn_open_video.setIcon(Icons.get(Icons.PLAY))
        self.btn_open_video.setIconSize(QSize(16, 16))
        self.btn_open_video.clicked.connect(self._open_video_player)
        post_row.addWidget(self.btn_open_video)

        self.lbl_success = QLabel("Render tamamlandı!")
        self.lbl_success.setObjectName("successLabel")
        post_row.addWidget(self.lbl_success)
        post_row.addStretch()

        self._post_render_widget.setVisible(False)
        vbox.addWidget(self._post_render_widget)

        return w

    # ─────────────────────────────────────────────────────────────────────────
    # Sinyal bağlamaları
    # ─────────────────────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self._app_state.project_changed.connect(self._refresh_chapters)
        self._app_state.chapter_changed.connect(self._on_chapter_from_state)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_chapters()
        self._update_output_path()
        self._update_estimate()

    # ─────────────────────────────────────────────────────────────────────────
    # Chapter yönetimi
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_chapters(self, project=None) -> None:
        if project is None:
            project = self._app_state.current_project
        self.chapter_combo.blockSignals(True)
        self.chapter_combo.clear()
        if project:
            for ch in project.chapters:
                self.chapter_combo.addItem(ch.name, ch.id)
        self.chapter_combo.blockSignals(False)
        self._update_output_path()
        self._update_estimate()

    @pyqtSlot(int)
    def _on_chapter_selected(self, index: int) -> None:
        self._update_output_path()
        self._update_estimate()

    def _on_chapter_from_state(self, chapter) -> None:
        if chapter:
            for i in range(self.chapter_combo.count()):
                if self.chapter_combo.itemData(i) == chapter.id:
                    self.chapter_combo.setCurrentIndex(i)
                    break

    def _current_chapter(self):
        """Seçili bölümü döndürür. ID ile arar, index uyumsuzluğuna karşı güvenli."""
        project = self._app_state.current_project
        if not project:
            return None
        idx = self.chapter_combo.currentIndex()
        if idx < 0:
            return None
        # Önce ID ile ara (combo yeniden sıralanmış veya güncellenmişse güvenli)
        chapter_id = self.chapter_combo.itemData(idx)
        if chapter_id is not None:
            for ch in project.chapters:
                if ch.id == chapter_id:
                    return ch
        # Fallback: index ile eriş
        if idx < len(project.chapters):
            return project.chapters[idx]
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Preset uygulama
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_preset(self, key: str) -> None:
        preset = PRESETS.get(key)
        if not preset:
            return
        w, h = preset["resolution"]

        # Çözünürlük combo'yu bul
        for i, (_, res) in enumerate(RESOLUTION_OPTIONS):
            if res == [w, h]:
                self.res_combo.setCurrentIndex(i)
                break
        else:
            # Özel seçeneği işaretle
            self.res_combo.setCurrentIndex(len(RESOLUTION_OPTIONS) - 1)
            self.custom_w.setValue(w)
            self.custom_h.setValue(h)

        fps_idx = {24: 0, 30: 1, 60: 2}.get(preset["fps"], 1)
        self.fps_combo.setCurrentIndex(fps_idx)

        for i, (_, codec) in enumerate(CODEC_OPTIONS):
            if codec == preset["codec"]:
                self.codec_combo.setCurrentIndex(i)
                break

        self.bitrate_edit.setText(preset["bitrate"])
        self._log(f"Preset uygulandı: {preset['label']}")

    # ─────────────────────────────────────────────────────────────────────────
    # Kullanıcı preset'leri (kaydet / yükle / sil)
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_user_presets(self) -> None:
        from core.render_presets import list_presets
        self.user_preset_combo.blockSignals(True)
        self.user_preset_combo.clear()
        self.user_preset_combo.addItem("— Preset seç —", None)
        for name in list_presets():
            self.user_preset_combo.addItem(name, name)
        self.user_preset_combo.blockSignals(False)

    def _on_user_preset_selected(self, index: int) -> None:
        name = self.user_preset_combo.currentData()
        if not name:
            return
        from core.render_presets import get_preset
        settings = get_preset(name)
        if settings:
            self._apply_settings_dict(settings)
            self._log(f"Preset yüklendi: {name}")

    def _save_user_preset(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        from core.render_presets import save_preset

        current_name = self.user_preset_combo.currentData() or ""
        name, ok = QInputDialog.getText(
            self, "Preset Kaydet", "Preset adı:", text=current_name
        )
        if not ok or not name.strip():
            return
        save_preset(name.strip(), self._collect_settings())
        self._refresh_user_presets()
        idx = self.user_preset_combo.findData(name.strip())
        if idx >= 0:
            self.user_preset_combo.setCurrentIndex(idx)
        self._log(f"Preset kaydedildi: {name.strip()}")

    def _delete_user_preset(self) -> None:
        name = self.user_preset_combo.currentData()
        if not name:
            return
        from core.render_presets import delete_preset
        delete_preset(name)
        self._refresh_user_presets()
        self._log(f"Preset silindi: {name}")

    def _apply_settings_dict(self, settings: Dict[str, Any]) -> None:
        """Kaydedilmiş bir ayar sözlüğünü tüm render widget'larına uygular."""
        # Çözünürlük
        resolution = settings.get("resolution")
        if resolution:
            for i, (_, res) in enumerate(RESOLUTION_OPTIONS):
                if res == resolution:
                    self.res_combo.setCurrentIndex(i)
                    break
            else:
                self.res_combo.setCurrentIndex(len(RESOLUTION_OPTIONS) - 1)
                self.custom_w.setValue(resolution[0])
                self.custom_h.setValue(resolution[1])

        # FPS
        fps = settings.get("fps")
        if fps in FPS_OPTIONS:
            self.fps_combo.setCurrentIndex(FPS_OPTIONS.index(fps))

        # Codec
        codec = settings.get("codec")
        if codec:
            idx = self.codec_combo.findData(codec)
            if idx >= 0:
                self.codec_combo.setCurrentIndex(idx)

        if settings.get("bitrate"):
            self.bitrate_edit.setText(settings["bitrate"])

        # Geçiş efektleri
        transitions = settings.get("transitions")
        if transitions is not None:
            selected = transitions if isinstance(transitions, list) else [transitions]
            for i in range(self.transition_list.count()):
                it = self.transition_list.item(i)
                it.setCheckState(
                    Qt.CheckState.Checked if it.data(Qt.ItemDataRole.UserRole) in selected
                    else Qt.CheckState.Unchecked
                )

        if settings.get("transition_duration") is not None:
            self.transition_dur.setValue(int(settings["transition_duration"] * 100))

        # Görsel hareket
        if settings.get("ken_burns") is not None:
            self.chk_ken_burns.setChecked(bool(settings["ken_burns"]))
        if settings.get("ken_burns_intensity") is not None:
            self.kb_intensity.setValue(int(settings["ken_burns_intensity"] * 100))
        motion = settings.get("image_motion")
        if motion in self._motion_buttons:
            self._select_motion(motion)

        bg_effect = settings.get("bg_effect")
        if bg_effect and hasattr(self, "bg_effect_combo"):
            idx = self.bg_effect_combo.findData(bg_effect)
            if idx >= 0:
                self.bg_effect_combo.setCurrentIndex(idx)

        # Altyazı
        if settings.get("subtitles") is not None:
            self.chk_subtitles.setChecked(bool(settings["subtitles"]))
        sub_style = settings.get("subtitle_style") or {}
        if sub_style:
            if sub_style.get("font"):
                idx = self.sub_font_combo.findText(sub_style["font"])
                if idx >= 0:
                    self.sub_font_combo.setCurrentIndex(idx)
            if sub_style.get("size"):
                self.sub_size_spin.setValue(int(sub_style["size"]))
            if sub_style.get("color") and self._sub_color_picker:
                self._sub_color_picker.set_color(sub_style["color"])
            if sub_style.get("stroke_color") and self._sub_stroke_picker:
                self._sub_stroke_picker.set_color(sub_style["stroke_color"])
            if sub_style.get("stroke_width") is not None:
                self.sub_stroke_spin.setValue(int(sub_style["stroke_width"]))
            pos_reverse = {"bottom": 0, "middle": 1, "top": 2}
            if sub_style.get("position") in pos_reverse:
                self.sub_pos_combo.setCurrentIndex(pos_reverse[sub_style["position"]])

        # Ses
        if settings.get("bgm_path") and hasattr(self, "bgm_path_edit"):
            self.bgm_path_edit.setText(settings["bgm_path"])
        if settings.get("bgm_volume") is not None:
            self.bgm_vol_slider.setValue(int(settings["bgm_volume"] * 100))
        if settings.get("bgm_ducking") is not None:
            self.chk_ducking.setChecked(bool(settings["bgm_ducking"]))

        # Intro/Outro/Watermark
        if settings.get("intro_path") and hasattr(self, "intro_path_edit"):
            self.intro_path_edit.setText(settings["intro_path"])
        if settings.get("outro_path") and hasattr(self, "outro_path_edit"):
            self.outro_path_edit.setText(settings["outro_path"])
        if settings.get("watermark_path") and hasattr(self, "watermark_path_edit"):
            self.watermark_path_edit.setText(settings["watermark_path"])
        if settings.get("watermark_position") and hasattr(self, "wm_pos_combo"):
            idx = self.wm_pos_combo.findData(settings["watermark_position"])
            if idx >= 0:
                self.wm_pos_combo.setCurrentIndex(idx)
        if settings.get("watermark_scale") is not None and hasattr(self, "wm_scale_spin"):
            self.wm_scale_spin.setValue(int(settings["watermark_scale"] * 100))
        if settings.get("watermark_opacity") is not None and hasattr(self, "wm_opacity_spin"):
            self.wm_opacity_spin.setValue(int(settings["watermark_opacity"] * 100))

    # ─────────────────────────────────────────────────────────────────────────
    # Geçiş listesi yardımcıları
    # ─────────────────────────────────────────────────────────────────────────

    def _trans_select_all(self) -> None:
        """Tüm geçiş efektlerini işaretle ("Yok" hariç)."""
        for i in range(self.transition_list.count()):
            it = self.transition_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) != "none":
                it.setCheckState(Qt.CheckState.Checked)

    def _trans_clear_all(self) -> None:
        """Tüm seçimleri temizle."""
        for i in range(self.transition_list.count()):
            self.transition_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _get_selected_transitions(self) -> list:
        """İşaretli geçiş anahtarlarını döndürür."""
        selected = []
        for i in range(self.transition_list.count()):
            it = self.transition_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                selected.append(it.data(Qt.ItemDataRole.UserRole))
        return selected

    # ─────────────────────────────────────────────────────────────────────────
    # Ayarları topla
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_settings(self) -> Dict[str, Any]:
        """UI'dan render ayarlarını toplar."""
        res_idx = self.res_combo.currentIndex()
        if res_idx == len(RESOLUTION_OPTIONS) - 1:  # Özel
            resolution = [self.custom_w.value(), self.custom_h.value()]
        else:
            resolution = RESOLUTION_OPTIONS[res_idx][1] or [1920, 1080]

        fps = FPS_OPTIONS[self.fps_combo.currentIndex()]
        codec = self.codec_combo.currentData() or "libx264"
        bitrate = self.bitrate_edit.text().strip() or "8000k"
        selected_transitions = self._get_selected_transitions()
        if not selected_transitions:
            transition = "none"
        elif "none" in selected_transitions:
            transition = "none"
        elif len(selected_transitions) == 1:
            transition = selected_transitions[0]
        else:
            transition = selected_transitions  # liste → backend'e gönderilir
        transition_dur = self.transition_dur.value() / 100.0
        ken_burns = self.chk_ken_burns.isChecked()
        kb_intensity = self.kb_intensity.value() / 100.0
        image_motion = getattr(self, "_current_motion", "zoom_in")
        bg_effect = self.bg_effect_combo.currentData() if hasattr(self, "bg_effect_combo") else "none"
        # blur_background: arka plan efekti dropdown'dan belirleniyor
        blur_bg = (bg_effect == "blur")

        subtitles = self.chk_subtitles.isChecked()
        pos_map = {0: "bottom", 1: "middle", 2: "top"}
        sub_style = {
            "font": self.sub_font_combo.currentText(),
            "size": self.sub_size_spin.value(),
            "color": self._sub_color_picker.color if self._sub_color_picker else "#ffffff",
            "stroke_color": self._sub_stroke_picker.color if self._sub_stroke_picker else "#000000",
            "stroke_width": self.sub_stroke_spin.value(),
            "position": pos_map.get(self.sub_pos_combo.currentIndex(), "bottom"),
        }

        bgm_path = self.bgm_path_edit.text().strip() or None
        bgm_volume = self.bgm_vol_slider.value() / 100.0
        bgm_ducking = self.chk_ducking.isChecked()

        intro_path = getattr(self, "intro_path_edit", None)
        outro_path = getattr(self, "outro_path_edit", None)

        watermark_path = getattr(self, "watermark_path_edit", None)
        wm_path = (watermark_path.text().strip() or None) if watermark_path else None
        wm_pos = self.wm_pos_combo.currentData() if hasattr(self, "wm_pos_combo") else "br"
        wm_scale = (self.wm_scale_spin.value() / 100.0) if hasattr(self, "wm_scale_spin") else 0.08
        wm_opacity = (self.wm_opacity_spin.value() / 100.0) if hasattr(self, "wm_opacity_spin") else 0.85

        return {
            "resolution": resolution,
            "fps": fps,
            "codec": codec,
            "bitrate": bitrate,
            "transitions": transition,
            "transition_duration": transition_dur,
            "ken_burns": ken_burns,
            "ken_burns_intensity": kb_intensity,
            "image_motion": image_motion,
            "blur_background": blur_bg,
            "bg_effect": bg_effect,
            "subtitles": subtitles,
            "subtitle_style": sub_style,
            "bgm_path": bgm_path,
            "bgm_volume": bgm_volume,
            "bgm_ducking": bgm_ducking,
            "intro_path": (intro_path.text().strip() or None) if intro_path else None,
            "outro_path": (outro_path.text().strip() or None) if outro_path else None,
            "watermark_path": wm_path,
            "watermark_position": wm_pos,
            "watermark_scale": wm_scale,
            "watermark_opacity": wm_opacity,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Output yolu
    # ─────────────────────────────────────────────────────────────────────────

    def _update_output_path(self) -> None:
        """Otomatik çıktı yolunu hesaplar."""
        chapter = self._current_chapter()
        project = self._app_state.current_project
        if not chapter or not project:
            return

        import core.project_manager as pm_mod
        proj_dir = pm_mod.get_project_dir(project)
        if not proj_dir:
            logger.warning("Çıktı yolu güncellenemedi: proje dizini bulunamadı.")
            return
        out_dir = proj_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in chapter.name if c.isalnum() or c in " _-").strip()
        filename = f"{safe_name}_{ts}.mp4"
        self.output_path_edit.setText(str(out_dir / filename))

    # ─────────────────────────────────────────────────────────────────────────
    # Tahmin güncelleme
    # ─────────────────────────────────────────────────────────────────────────

    def _update_estimate(self) -> None:
        chapter = self._current_chapter()
        if not chapter or not chapter.segments:
            self.lbl_estimate.setText("Süre: —  |  Tahmini boyut: —")
            return
        try:
            from core.video_composer import estimate_render_size
            info = estimate_render_size(chapter, self._collect_settings())
            self.lbl_estimate.setText(
                f"Süre: {info['duration_str']}  |  Tahmini boyut: {info['size_str']}"
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Render
    # ─────────────────────────────────────────────────────────────────────────

    def _start_render(self) -> None:
        chapter = self._current_chapter()
        if not chapter:
            self._log("<span style='color:#ef4444;'><b>HATA:</b> Önce bir bölüm seçin.</span>")
            return
        if not chapter.segments:
            self._log("<span style='color:#ef4444;'><b>HATA:</b> Bu bölümde segment yok. Önce TTS tamamlayın.</span>")
            return

        # FFmpeg kontrolü
        from core.ffmpeg_helper import check_ffmpeg
        if not check_ffmpeg():
            self._log("<span style='color:#ef4444;'><b>HATA:</b> FFmpeg bulunamadı!</span>")
            self._log("   İndirin: https://www.gyan.dev/ffmpeg/builds/ (Windows)")
            self._log("   Kurulumdan sonra sistemi yeniden başlatın.")
            return

        output_path = self.output_path_edit.text().strip()
        if not output_path:
            self._update_output_path()
            output_path = self.output_path_edit.text().strip()

        settings = self._collect_settings()

        self._post_render_widget.setVisible(False)
        self.progress_bar.setValue(0)
        self.btn_render.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.log_area.clear()

        from ui.workers.render_worker import RenderWorker
        self._render_worker = RenderWorker(chapter, settings, output_path)
        self._render_worker.progress.connect(self._on_render_progress)
        self._render_worker.log.connect(self._log)
        self._render_worker.finished.connect(self._on_render_finished)
        self._render_worker.error.connect(self._on_render_error)
        self._render_worker.start()

        self._log(f"<span style='color:#6366f1;'><b>BAŞLADI:</b> Render başlatıldı: {chapter.name}</span>")

    def _cancel_render(self) -> None:
        if self._render_worker and self._render_worker.isRunning():
            self._render_worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.lbl_eta.setText("İptal ediliyor...")

    @pyqtSlot(int, str)
    def _on_render_progress(self, percent: int, eta: str) -> None:
        self.progress_bar.setValue(percent)
        self.lbl_eta.setText(f"İlerleme: %{percent}  ETA: {eta}")

    @pyqtSlot(str)
    def _on_render_finished(self, output_path: str) -> None:
        self._output_path = output_path
        self.progress_bar.setValue(100)
        self.lbl_eta.setText("Tamamlandı!")
        self.btn_render.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._post_render_widget.setVisible(True)

        # Videoyu oynatıcıya yükle
        self._player.setSource(QUrl.fromLocalFile(output_path))
        self.btn_play_preview.setEnabled(True)
        self._log(f"<span style='color:#22c55e;'><b>BAŞARILI:</b> Render tamamlandı: {output_path}</span>")

    @pyqtSlot(str)
    def _on_render_error(self, message: str) -> None:
        self.progress_bar.setValue(0)
        self.lbl_eta.setText("Hata oluştu")
        self.btn_render.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log(f"<span style='color:#ef4444;'><b>HATA:</b> {message}</span>")

    # ─────────────────────────────────────────────────────────────────────────
    # Önizleme
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_preview(self) -> None:
        chapter = self._current_chapter()
        if not chapter or not chapter.segments:
            self._log("<span style='color:#ef4444;'><b>HATA:</b> Önizleme için segment gerekli.</span>")
            return

        from core.ffmpeg_helper import check_ffmpeg
        if not check_ffmpeg():
            self._log("<span style='color:#ef4444;'><b>HATA:</b> FFmpeg bulunamadı. Önizleme üretilemez.</span>")
            return

        import tempfile
        preview_path = str(
            Path(tempfile.gettempdir()) / f"recapai_preview_{chapter.id}.mp4"
        )

        settings = self._collect_settings()
        self._log("<span style='color:#6366f1;'><b>ÜRETİLİYOR:</b> Önizleme üretiliyor (10 saniye)...</span>")
        self.btn_gen_preview.setEnabled(False)

        from ui.workers.render_worker import RenderWorker

        class _PreviewWorker(RenderWorker):
            def run(self_inner):
                from core.video_composer import VideoComposer
                try:
                    composer = VideoComposer(settings)
                    result = composer.generate_preview(
                        chapter, preview_path, duration=10.0,
                        progress_callback=lambda p, e: self_inner.progress.emit(p, e),
                    )
                    self_inner.finished.emit(result)
                except Exception as exc:
                    self_inner.error.emit(str(exc))

        self._preview_worker = _PreviewWorker(chapter, settings, preview_path)
        self._preview_worker.progress.connect(self._on_render_progress)
        self._preview_worker.log.connect(self._log)
        self._preview_worker.finished.connect(self._on_preview_ready)
        self._preview_worker.error.connect(lambda e: (
            self._log(f"<span style='color:#ef4444;'><b>HATA:</b> Önizleme hatası: {e}</span>"),
            self.btn_gen_preview.setEnabled(True),
        ))
        self._preview_worker.start()

    @pyqtSlot(str)
    def _on_preview_ready(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        self.btn_play_preview.setEnabled(True)
        self.btn_play_preview.setText("  Durdur")
        self.btn_play_preview.setIcon(Icons.get(Icons.PAUSE))
        self.btn_gen_preview.setEnabled(True)
        self._log(f"<span style='color:#22c55e;'><b>BAŞARILI:</b> Önizleme hazır: {path}</span>")

    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self.btn_play_preview.setText("  Oynat")
            self.btn_play_preview.setIcon(Icons.get(Icons.PLAY))
        else:
            self._player.play()
            self.btn_play_preview.setText("  Durdur")
            self.btn_play_preview.setIcon(Icons.get(Icons.PAUSE))

    # ─────────────────────────────────────────────────────────────────────────
    # Render sonrası
    # ─────────────────────────────────────────────────────────────────────────

    def _open_output_folder(self) -> None:
        if self._output_path:
            folder = str(Path(self._output_path).parent)
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])

    def _open_video_player(self) -> None:
        if self._output_path and Path(self._output_path).exists():
            if sys.platform == "win32":
                os.startfile(self._output_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self._output_path])
            else:
                subprocess.Popen(["xdg-open", self._output_path])

    # ─────────────────────────────────────────────────────────────────────────
    # Browse
    # ─────────────────────────────────────────────────────────────────────────

    def _browse_watermark(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Watermark/Logo Seç", "", "Görsel (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self.watermark_path_edit.setText(path)

    def _browse_bgm(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "BGM Seç", "", "Ses (*.mp3 *.wav *.ogg *.aac *.m4a)"
        )
        if path:
            self.bgm_path_edit.setText(path)

    def _browse_video(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Video Seç", "", "Video (*.mp4 *.mkv *.mov *.avi)"
        )
        if path:
            edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Çıktı Dosyasını Kaydet", self.output_path_edit.text(), "Video (*.mp4)"
        )
        if path:
            self.output_path_edit.setText(path)

    # ─────────────────────────────────────────────────────────────────────────
    # Event handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_resolution_changed(self, index: int) -> None:
        is_custom = index == len(RESOLUTION_OPTIONS) - 1
        self._custom_res_row.setVisible(is_custom)

    def _select_motion(self, value: str) -> None:
        """Animasyon modu seçimi — tek buton aktif."""
        self._current_motion = value
        for v, btn in self._motion_buttons.items():
            btn.setChecked(v == value)

    def _on_ken_burns_toggled(self, state: int) -> None:
        self._kb_row_widget.setEnabled(bool(state))
        # Butonları da etkinleştir/devre dışı bırak
        for btn in self._motion_buttons.values():
            btn.setEnabled(bool(state))

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self.log_area.append(msg)
        self.log_area.verticalScrollBar().setValue(
            self.log_area.verticalScrollBar().maximum()
        )

    def _wrap(self, layout) -> QWidget:
        """Layout'u QWidget içine sarar."""
        w = QWidget()
        w.setLayout(layout)
        return w