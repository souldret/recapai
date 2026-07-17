"""
RecapAI - Script Sayfası (v2).
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QSplitter, QScrollArea, QTextEdit,
    QFileDialog, QMessageBox, QSizePolicy, QSlider, QAbstractItemView,
    QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QPixmap, QUndoStack, QUndoCommand

from core.context import AppContext
from core.models import SegmentData
from ui.utils.icons import Icons

logger = logging.getLogger(__name__)

STYLES = [
    ("fresh",      "Manhwa Fresh"),
    ("epic",       "Epik"),
    ("casual",     "Samimi"),
    ("funny",      "Mizahi"),
    ("mysterious", "Gizemli"),
    ("narrator",   "Anlatıcı"),
    ("quick",      "Hızlı"),
]
LENGTHS = ["short", "medium", "long"]
LENGTH_LABELS = {"short": "Kısa", "medium": "Orta", "long": "Uzun"}
LANGUAGES = [("tr", "Türkçe"), ("en", "İngilizce")]
# Niş modülleri (PDF Prompt 2) — runtime'da list_niches ile de doldurulabilir
NICHES = [
    ("power_fantasy", "Isekai / Power Fantasy"),
    ("romance",       "Romantik / Duygusal"),
    ("dark_action",   "Karanlık Aksiyon / İntikam"),
    ("comedy",        "Komedi / Slice of Life"),
]


# ── Undo Command ───────────────────────────────────────────────────────────────

class EditSegmentCommand(QUndoCommand):
    """QUndo için segment metin değişikliği."""

    def __init__(self, card: "SegmentCard", old_text: str, new_text: str) -> None:
        super().__init__("Segment Düzenle")
        self._card = card
        self._old = old_text
        self._new = new_text

    def undo(self) -> None:
        self._card.set_text_silent(self._old)

    def redo(self) -> None:
        self._card.set_text_silent(self._new)


# ── Focus-aware TextEdit ───────────────────────────────────────────────────────

class FocusAwareTextEdit(QTextEdit):
    """focusInEvent zincirini bozmadan odak callback'i tetikleyen editör."""

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        card = self.parent()
        while card is not None and not isinstance(card, SegmentCard):
            card = card.parent()
        if card is not None and getattr(card, "_focus_callback", None):
            card._focus_callback(card)


# ── Segment Kartı ──────────────────────────────────────────────────────────────

class SegmentCard(QFrame):
    """Tek bir script segmenti kartı."""

    def __init__(
        self,
        index: int,
        segment: SegmentData,
        thumbnail_path: Optional[str],
        undo_stack: QUndoStack,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.segment_index = index
        self.segment = segment
        self._undo_stack = undo_stack
        self._last_text = segment.text
        self._thumb_path = thumbnail_path
        self._building = False
        self._focus_callback = None
        self.setObjectName("segmentCard")
        self._build_ui()

    def _build_ui(self) -> None:
        self._building = True
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        # Thumbnail
        thumb = QLabel()
        thumb.setFixedSize(80, 80)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._thumb_path and Path(self._thumb_path).exists():
            px = QPixmap(self._thumb_path).scaled(
                80, 80, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            thumb.setPixmap(px)
        else:
            thumb.setText(f"[{self.segment.image_index + 1}]")
        root.addWidget(thumb)

        # Orta: başlık + editör
        mid = QVBoxLayout()
        mid.setSpacing(4)

        hdr = QHBoxLayout()
        self.lbl_no = QLabel(f"Segment {self.segment_index + 1}")
        self.lbl_no.setObjectName("cardValue")
        hdr.addWidget(self.lbl_no)
        self.lbl_duration = QLabel(self._dur_label())
        self.lbl_duration.setObjectName("pageSubtitle")
        hdr.addWidget(self.lbl_duration)
        hdr.addStretch()
        mid.addLayout(hdr)

        self.text_edit = FocusAwareTextEdit(self)
        self.text_edit.setPlainText(self.segment.text)
        self.text_edit.setFixedHeight(72)
        self.text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.text_edit.textChanged.connect(self._on_text_changed)
        mid.addWidget(self.text_edit)

        root.addLayout(mid, 1)

        # Sağ: butonlar
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.btn_regen = QPushButton()
        self.btn_regen.setFixedSize(32, 32)
        self.btn_regen.setObjectName("secondaryBtn")
        self.btn_regen.setIcon(Icons.get(Icons.REFRESH))
        self.btn_regen.setIconSize(QSize(16, 16))
        self.btn_regen.setToolTip("Yeniden Üret")
        btn_col.addWidget(self.btn_regen)
        self.btn_del = QPushButton()
        self.btn_del.setFixedSize(32, 32)
        self.btn_del.setObjectName("secondaryBtn")
        self.btn_del.setIcon(Icons.get(Icons.DELETE, color="#ef4444"))
        self.btn_del.setIconSize(QSize(16, 16))
        self.btn_del.setToolTip("Sil")
        btn_col.addWidget(self.btn_del)
        btn_col.addStretch()
        root.addLayout(btn_col)

        self._building = False

    def _dur_label(self) -> str:
        return f"⏱ {self.segment.duration:.1f}s"

    def _on_text_changed(self) -> None:
        if self._building:
            return
        new_text = self.text_edit.toPlainText()
        if new_text == self._last_text:
            return
        cmd = EditSegmentCommand(self, self._last_text, new_text)
        self._undo_stack.push(cmd)
        self._last_text = new_text
        self.segment.text = new_text
        # Süre güncelle
        from core.script_generator import ScriptGenerator
        self.segment.duration = ScriptGenerator.estimate_duration(new_text)
        self.lbl_duration.setText(self._dur_label())

    def set_text_silent(self, text: str) -> None:
        """Undo/Redo sırasında signal döngüsünü kırar."""
        self._building = True
        self.text_edit.setPlainText(text)
        self._last_text = text
        self.segment.text = text
        self._building = False

    def update_segment(self, segment: SegmentData) -> None:
        self.segment = segment
        self._last_text = segment.text
        self._building = True
        self.text_edit.setPlainText(segment.text)
        self._building = False
        self.lbl_duration.setText(self._dur_label())


# ── Sol Panel: Görsel Önizleme ─────────────────────────────────────────────────

class ImagePreviewSide(QFrame):
    """Sol panel: aktif segmentin görsel önizlemesi."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("imgPreview")
        self._build_ui()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        lbl = QLabel("Panel Önizleme")
        lbl.setObjectName("cardSubtitle")
        v.addWidget(lbl)

        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setMinimumHeight(300)
        self.img_lbl.setText("← Sağdan segment seçin")
        v.addWidget(self.img_lbl, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("separator")
        v.addWidget(sep)

        self.lbl_scene = QLabel("Sahne: —")
        self.lbl_scene.setObjectName("pageSubtitle")
        self.lbl_scene.setWordWrap(True)
        v.addWidget(self.lbl_scene)

        self.lbl_chars = QLabel("Karakterler: —")
        self.lbl_chars.setObjectName("pageSubtitle")
        self.lbl_chars.setWordWrap(True)
        v.addWidget(self.lbl_chars)

        self.lbl_mood = QLabel("Atmosfer: —")
        self.lbl_mood.setObjectName("pageSubtitle")
        v.addWidget(self.lbl_mood)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("◀  Önceki")
        self.btn_prev.setObjectName("secondaryBtn")
        nav.addWidget(self.btn_prev)
        self.btn_next = QPushButton("Sonraki  ▶")
        self.btn_next.setObjectName("secondaryBtn")
        nav.addWidget(self.btn_next)
        v.addLayout(nav)

    def show_panel(self, image_path: str, analysis: dict) -> None:
        px = QPixmap(image_path)
        if not px.isNull():
            scaled = px.scaled(
                self.img_lbl.width() - 8, self.img_lbl.height() - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_lbl.setPixmap(scaled)
        else:
            self.img_lbl.setText("Yüklenemedi")

        self.lbl_scene.setText(f"Sahne: {analysis.get('scene', '—')[:80]}")
        chars = ", ".join(analysis.get("characters", [])) or "—"
        self.lbl_chars.setText(f"Karakterler: {chars}")
        self.lbl_mood.setText(f"Atmosfer: {analysis.get('mood', '—')}")

    def clear(self) -> None:
        self.img_lbl.setPixmap(QPixmap())
        self.img_lbl.setText("← Sağdan segment seçin")
        self.lbl_scene.setText("Sahne: —")
        self.lbl_chars.setText("Karakterler: —")
        self.lbl_mood.setText("Atmosfer: —")


# ── Ana Sayfa ──────────────────────────────────────────────────────────────────

class ScriptPage(QWidget):
    """Script oluşturma ve düzenleme sayfası (v2)."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._regen_worker = None
        self._cards: List[SegmentCard] = []
        self._active_card_index: int = -1
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(100)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(5000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._build_ui()
        self._connect_app_state()
        logger.debug("ScriptPage oluşturuldu.")

    # ── UI Build ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)

        root.addWidget(self._make_header())
        root.addWidget(self._make_toolbar())
        root.addWidget(self._make_stream_indicator())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: görsel önizleme
        self.preview_side = ImagePreviewSide()
        self.preview_side.btn_prev.clicked.connect(self._go_prev)
        self.preview_side.btn_next.clicked.connect(self._go_next)
        splitter.addWidget(self.preview_side)

        # Sağ: segment listesi
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(4, 4, 4, 4)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_container)
        rv.addWidget(scroll, 1)

        splitter.addWidget(right)
        splitter.setSizes([380, 680])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        root.addWidget(self._make_bottom_bar())

    def _make_header(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        title = QLabel("Script")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        sub = QLabel("AI ile script üretin, düzenleyin ve dışa aktarın.")
        sub.setObjectName("pageSubtitle")
        v.addWidget(sub)
        return w

    def _make_toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(14, 10, 14, 10)
        vbox.setSpacing(10)

        # Row 1: Parameters
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(10)

        # Bölüm
        row1.addWidget(QLabel("Bölüm:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(170)
        self.chapter_combo.setPlaceholderText("Seçin…")
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        row1.addWidget(self.chapter_combo, 1)

        # Model
        row1.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        self._populate_model_combo()
        row1.addWidget(self.model_combo, 1)

        # Stil
        row1.addWidget(QLabel("Stil:"))
        self.style_combo = QComboBox()
        for key, label in STYLES:
            self.style_combo.addItem(label, key)
        self.style_combo.setMinimumWidth(140)
        # Varsayılan: Manhwa Fresh
        idx_fresh = self.style_combo.findData("fresh")
        if idx_fresh >= 0:
            self.style_combo.setCurrentIndex(idx_fresh)
        row1.addWidget(self.style_combo)

        row1.addStretch()
        vbox.addLayout(row1)

        # Row 1b: Tuning controls
        row1b = QHBoxLayout()
        row1b.setContentsMargins(0, 0, 0, 0)
        row1b.setSpacing(10)

        # Niş (Manhwa Fresh Prompt 2)
        row1b.addWidget(QLabel("Niş:"))
        self.niche_combo = QComboBox()
        self.niche_combo.setMinimumWidth(190)
        self.niche_combo.setToolTip(
            "Manhwa türüne göre ton: power fantasy, romance, dark action, comedy."
        )
        for key, label in NICHES:
            self.niche_combo.addItem(label, key)
        # list_niches ile zenginleştir (varsa)
        try:
            from core.script_generator import list_niches
            for n in list_niches():
                idx = self.niche_combo.findData(n["id"])
                if idx >= 0 and n.get("description"):
                    self.niche_combo.setItemData(idx, n["description"], Qt.ItemDataRole.ToolTipRole)
        except Exception:
            pass
        row1b.addWidget(self.niche_combo)

        # Uzunluk
        row1b.addWidget(QLabel("Uzunluk:"))
        self.length_slider = QSlider(Qt.Orientation.Horizontal)
        self.length_slider.setRange(0, 2)
        self.length_slider.setValue(1)
        self.length_slider.setMinimumWidth(120)
        self.length_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.length_slider.valueChanged.connect(
            lambda v: self.lbl_length.setText(LENGTH_LABELS[LENGTHS[v]])
        )
        row1b.addWidget(self.length_slider)
        self.lbl_length = QLabel("Orta")
        self.lbl_length.setMinimumWidth(48)
        self.lbl_length.setObjectName("pageSubtitle")
        row1b.addWidget(self.lbl_length)

        # Dil
        row1b.addWidget(QLabel("Dil:"))
        self.lang_combo = QComboBox()
        for key, label in LANGUAGES:
            self.lang_combo.addItem(label, key)
        self.lang_combo.setMinimumWidth(110)
        row1b.addWidget(self.lang_combo)

        # Chapter 1 Hook (Prompt 3)
        self.hook_check = QCheckBox("Ch.1 Hook")
        self.hook_check.setToolTip(
            "Açıkken Prompt 3 (Chapter 1 Hook) eklenir — açılış cümlesini güçlendirir.\n"
            "Bölüm seçilince otomatik önerilir; istersen kapatabilirsin.\n"
            "Bölüm 2+ için kapalı tut."
        )
        row1b.addWidget(self.hook_check)

        row1b.addStretch()
        vbox.addLayout(row1b)

        # Row 2: Actions
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(10)

        # Undo/Redo
        btn_undo = QPushButton()
        btn_undo.setFixedSize(32, 32)
        btn_undo.setObjectName("secondaryBtn")
        btn_undo.setIcon(Icons.get(Icons.UNDO))
        btn_undo.setIconSize(QSize(16, 16))
        btn_undo.setToolTip("Geri Al (Ctrl+Z)")
        btn_undo.clicked.connect(self._undo_stack.undo)
        row2.addWidget(btn_undo)

        btn_redo = QPushButton()
        btn_redo.setFixedSize(32, 32)
        btn_redo.setObjectName("secondaryBtn")
        btn_redo.setIcon(Icons.get(Icons.REDO))
        btn_redo.setIconSize(QSize(16, 16))
        btn_redo.setToolTip("Yinele (Ctrl+Y)")
        btn_redo.clicked.connect(self._undo_stack.redo)
        row2.addWidget(btn_redo)

        row2.addStretch()

        # Kaydet
        self.btn_save = QPushButton("  Kaydet")
        self.btn_save.setFixedWidth(110)
        self.btn_save.setObjectName("secondaryBtn")
        self.btn_save.setIcon(Icons.get(Icons.SAVE))
        self.btn_save.setIconSize(QSize(16, 16))
        self.btn_save.clicked.connect(self._save_project)
        row2.addWidget(self.btn_save)

        # Script Üret
        self.btn_generate = QPushButton("  Script Üret")
        self.btn_generate.setFixedWidth(150)
        self.btn_generate.setIcon(Icons.get(Icons.AI, color="#ffffff"))
        self.btn_generate.setIconSize(QSize(16, 16))
        self.btn_generate.clicked.connect(self._start_generation)
        row2.addWidget(self.btn_generate)

        self.btn_stop = QPushButton("  Dur")
        self.btn_stop.setFixedWidth(90)
        self.btn_stop.setObjectName("secondaryBtn")
        self.btn_stop.setIcon(Icons.get(Icons.STOP, color="#ef4444"))
        self.btn_stop.setIconSize(QSize(16, 16))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_generation)
        row2.addWidget(self.btn_stop)

        vbox.addLayout(row2)
        return frame

    def _make_stream_indicator(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.lbl_stream = QLabel("")
        self.lbl_stream.setObjectName("pageSubtitle")
        h.addWidget(self.lbl_stream)
        h.addStretch()
        return w

    def _make_bottom_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        hbox = QHBoxLayout(frame)
        hbox.setContentsMargins(16, 10, 16, 10)
        hbox.setSpacing(16)

        self.lbl_words = QLabel("Kelime: 0")
        self.lbl_words.setObjectName("pageSubtitle")
        hbox.addWidget(self.lbl_words)

        self.lbl_total_dur = QLabel("Tahmini süre: 0:00")
        self.lbl_total_dur.setObjectName("pageSubtitle")
        hbox.addWidget(self.lbl_total_dur)

        hbox.addStretch()

        btn_txt = QPushButton("  TXT Export")
        btn_txt.setFixedWidth(140)
        btn_txt.setObjectName("secondaryBtn")
        btn_txt.setIcon(Icons.get(Icons.EXPORT))
        btn_txt.setIconSize(QSize(16, 16))
        btn_txt.clicked.connect(self._export_txt)
        hbox.addWidget(btn_txt)

        btn_srt = QPushButton("  SRT Export")
        btn_srt.setFixedWidth(140)
        btn_srt.setObjectName("secondaryBtn")
        btn_srt.setIcon(Icons.get(Icons.EXPORT))
        btn_srt.setIconSize(QSize(16, 16))
        btn_srt.clicked.connect(self._export_srt)
        hbox.addWidget(btn_srt)

        return frame

    # ── Helpers ────────────────────────────────────────────────────

    def _populate_model_combo(self) -> None:
        self.model_combo.clear()
        try:
            data = json.loads(Path("config/models.json").read_text(encoding="utf-8"))
            for m in data.get("script_models", []):
                label = m["name"]
                if m.get("recommended"):
                    label += " (Tavsiye Edilen)"
                self.model_combo.addItem(label, m["id"])
        except Exception as exc:
            logger.error("Model listesi yüklenemedi: %s", exc)
            self.model_combo.addItem("Claude 3.5 Sonnet", "anthropic/claude-3.5-sonnet")

    def _get_api_key(self) -> str:
        return self.ctx.app_state.get_setting("api", "openrouter_api_key", default="")

    def _get_thumbnail(self, image_index: int) -> Optional[str]:
        state = self.ctx.app_state
        if not state.current_project or not state.current_chapter:
            return None
        images = state.current_chapter.images
        if image_index >= len(images):
            return None
        img = images[image_index]
        if img.thumbnail_path and Path(img.thumbnail_path).exists():
            return img.thumbnail_path
        return img.path

    def _connect_app_state(self) -> None:
        self.ctx.app_state.project_changed.connect(self._on_project_changed)
        self.ctx.app_state.chapter_changed.connect(self._on_chapter_changed_state)

    # ── App State Callbacks ────────────────────────────────────────

    def _on_project_changed(self, project) -> None:
        self.chapter_combo.clear()
        self._clear_cards()
        self.preview_side.clear()
        if project:
            for ch in project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)

    def _on_chapter_changed_state(self, chapter) -> None:
        pass

    def _on_chapter_changed(self, index: int) -> None:
        if index < 0:
            return
        state = self.ctx.app_state
        if not state.current_project:
            return
        chapter_id = self.chapter_combo.itemData(index)
        chapter = state.current_project.get_chapter(chapter_id)
        if chapter:
            state.current_chapter = chapter
            self._load_segments(chapter)
            # İlk bölüm sezgisine göre hook kutusunu öner
            try:
                from core.script_generator import _is_first_chapter
                self.hook_check.setChecked(_is_first_chapter(chapter))
            except Exception:
                pass

    # ── Segment Loading ────────────────────────────────────────────

    def _load_segments(self, chapter) -> None:
        self._clear_cards()
        if not chapter.segments:
            return
        for i, seg in enumerate(chapter.segments):
            thumb = self._get_thumbnail(seg.image_index)
            self._add_card(i, seg, thumb)
        self._update_stats()
        if self._cards:
            self._activate_card(0)

    def _clear_cards(self) -> None:
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        self._active_card_index = -1
        # Stretch'i koru
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_card(self, index: int, seg: SegmentData, thumb: Optional[str]) -> SegmentCard:
        card = SegmentCard(index, seg, thumb, self._undo_stack)
        card.btn_del.clicked.connect(lambda: self._delete_card(card))
        card.btn_regen.clicked.connect(lambda: self._regen_card(card))
        card.text_edit.textChanged.connect(self._update_stats)
        card._focus_callback = self._on_card_focus
        # Stretch'in önüne ekle
        stretch_idx = self._cards_layout.count() - 1
        self._cards_layout.insertWidget(stretch_idx, card)
        self._cards.append(card)
        return card

    def _activate_card(self, index: int) -> None:
        if index < 0 or index >= len(self._cards):
            return
        self._active_card_index = index
        card = self._cards[index]
        state = self.ctx.app_state
        ch = state.current_chapter
        if not ch:
            return
        images = ch.images
        img_idx = card.segment.image_index
        if img_idx < len(images):
            image_path = images[img_idx].path
            analysis = ch.analysis_data.get(str(img_idx), {})
            self.preview_side.show_panel(image_path, analysis)

    def _on_card_focus(self, card: SegmentCard) -> None:
        idx = self._cards.index(card) if card in self._cards else -1
        if idx >= 0:
            self._activate_card(idx)

    def _go_prev(self) -> None:
        if self._active_card_index > 0:
            self._activate_card(self._active_card_index - 1)

    def _go_next(self) -> None:
        if self._active_card_index < len(self._cards) - 1:
            self._activate_card(self._active_card_index + 1)

    # ── Script Generation ──────────────────────────────────────────

    def _start_generation(self) -> None:
        state = self.ctx.app_state
        if not state.current_project:
            QMessageBox.warning(self, "Uyarı", "Önce bir proje seçin.")
            return
        if self.chapter_combo.currentIndex() < 0:
            QMessageBox.warning(self, "Uyarı", "Önce bir bölüm seçin.")
            return

        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return

        if not chapter.analysis_data:
            QMessageBox.warning(
                self, "Analiz Verisi Yok",
                f"'{chapter.name}' bölümü için önce AI Analiz yapın."
            )
            return

        api_key = self._get_api_key()
        if not api_key:
            QMessageBox.warning(self, "API Anahtarı Eksik",
                                "Ayarlar > API sekmesinden OpenRouter anahtarını girin.")
            return

        model = self.model_combo.currentData() or "anthropic/claude-3.5-sonnet"
        style = self.style_combo.currentData() or "fresh"
        length = LENGTHS[self.length_slider.value()]
        language = self.lang_combo.currentData() or "tr"
        niche = self.niche_combo.currentData() or "power_fantasy"
        # Checkbox doğrudan True/False; None kullanma (kullanıcı kapatınca
        # otomatik ch.1 sezgisi hook'u tekrar açmasın)
        use_hook = self.hook_check.isChecked()

        from ui.workers.script_worker import ScriptWorker
        self._worker = ScriptWorker(
            chapter, model, api_key, style, length, language,
            niche=niche, use_hook=use_hook,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_generation_finished)
        self._worker.error.connect(self._on_generation_error)

        self._clear_cards()
        self.btn_generate.setEnabled(False)
        self.btn_stop.setEnabled(True)
        hook_note = " + Ch.1 Hook" if use_hook else ""
        self.lbl_stream.setText(
            f"<span style='color:#6366f1;'><b>ÜRETİLİYOR:</b> "
            f"{self.style_combo.currentText()} · {self.niche_combo.currentText()}{hook_note}...</span>"
        )
        self.ctx.app_state.status_message.emit("Script üretimi başladı…")
        self._autosave_timer.start()
        self._worker.start()

    def _stop_generation(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self.lbl_stream.setText("<span style='color:#ef4444;'><b>DURDURULDU:</b> İşlem kesildi.</span>")
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_chunk(self, chunk: str) -> None:
        current = self.lbl_stream.text()
        # Son 120 karakteri göster
        preview = (current + chunk)[-120:]
        self.lbl_stream.setText(preview)

    def _on_progress(self, msg: str) -> None:
        self.ctx.app_state.status_message.emit(msg)

    def _on_generation_finished(self, segments: list) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_stream.setText(f"<span style='color:#22c55e;'><b>BAŞARILI:</b> {len(segments)} segment üretildi.</span>")

        state = self.ctx.app_state
        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id) if state.current_project else None
        if not chapter:
            return

        chapter.segments = segments
        self._load_segments(chapter)
        self._save_project()
        self.ctx.app_state.status_message.emit(f"Script hazır: {len(segments)} segment")

    def _on_generation_error(self, msg: str) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_stream.setText(f"<span style='color:#ef4444;'><b>HATA:</b> {msg[:80]}</span>")
        QMessageBox.critical(self, "Script Üretim Hatası", msg)
        self._autosave_timer.stop()

    # ── Card Actions ───────────────────────────────────────────────

    def _delete_card(self, card: SegmentCard) -> None:
        if card not in self._cards:
            return
        reply = QMessageBox.question(
            self, "Segmenti Sil", "Bu segmenti silmek istiyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        idx = self._cards.index(card)
        self._cards_layout.removeWidget(card)
        card.deleteLater()
        self._cards.pop(idx)

        state = self.ctx.app_state
        chapter_id = self.chapter_combo.currentData()
        if state.current_project and chapter_id:
            chapter = state.current_project.get_chapter(chapter_id)
            if chapter and idx < len(chapter.segments):
                chapter.segments.pop(idx)

        # Numaraları güncelle
        for i, c in enumerate(self._cards):
            c.segment_index = i
            c.lbl_no.setText(f"Segment {i + 1}")

        self._update_stats()
        self._save_project()

    def _regen_card(self, card: SegmentCard) -> None:
        api_key = self._get_api_key()
        if not api_key:
            QMessageBox.warning(self, "API Anahtarı Eksik", "Ayarlar bölümünden API anahtarı girin.")
            return
        state = self.ctx.app_state
        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id) if state.current_project else None
        if not chapter:
            return

        model = self.model_combo.currentData() or "anthropic/claude-3.5-sonnet"
        style = self.style_combo.currentData() or "fresh"
        language = self.lang_combo.currentData() or "tr"
        length = LENGTHS[self.length_slider.value()]
        niche = self.niche_combo.currentData() or "power_fantasy"
        idx = self._cards.index(card)

        from ui.workers.script_worker import RegenerateSegmentWorker
        self._regen_worker = RegenerateSegmentWorker(
            chapter, idx, model, api_key, style, language, length, niche=niche,
        )
        self._regen_worker.finished.connect(lambda i, s: self._on_regen_done(i, s))
        self._regen_worker.error.connect(lambda m: QMessageBox.critical(self, "Hata", m))
        card.btn_regen.setEnabled(False)
        card.btn_regen.setIcon(Icons.get(Icons.PROCESSING, color="#e0af68"))
        self._regen_worker.start()

    def _on_regen_done(self, index: int, segment: SegmentData) -> None:
        if index < len(self._cards):
            card = self._cards[index]
            card.update_segment(segment)
            card.btn_regen.setEnabled(True)
            card.btn_regen.setIcon(Icons.get(Icons.REFRESH))
        self._update_stats()
        self._save_project()

    # ── Stats ──────────────────────────────────────────────────────

    def _update_stats(self) -> None:
        total_words = 0
        total_dur = 0.0
        for card in self._cards:
            text = card.text_edit.toPlainText()
            total_words += len(text.split())
            total_dur += card.segment.duration

        self.lbl_words.setText(f"Kelime: {total_words}")
        mins = int(total_dur // 60)
        secs = int(total_dur % 60)
        self.lbl_total_dur.setText(f"Tahmini süre: {mins}:{secs:02d}")

    # ── Save ───────────────────────────────────────────────────────

    def _save_project(self) -> None:
        state = self.ctx.app_state
        if not state.current_project:
            return
        # Kartlardan segment metinlerini geri yaz
        chapter_id = self.chapter_combo.currentData()
        if not chapter_id:
            return
        chapter = state.current_project.get_chapter(chapter_id)
        if chapter:
            for card in self._cards:
                if card.segment_index < len(chapter.segments):
                    chapter.segments[card.segment_index].text = card.text_edit.toPlainText()
            from core.project_manager import save_project
            try:
                save_project(state.current_project)
                logger.debug("Proje otomatik kaydedildi.")
            except Exception as exc:
                logger.error("Kaydetme hatası: %s", exc)

    def _autosave(self) -> None:
        self._save_project()

    # ── Export ─────────────────────────────────────────────────────

    def _export_txt(self) -> None:
        state = self.ctx.app_state
        if not state.current_chapter or not state.current_chapter.segments:
            QMessageBox.warning(self, "Uyarı", "Dışa aktarılacak segment yok.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "TXT Kaydet",
            f"{state.current_chapter.name}_script.txt", "Metin (*.txt)"
        )
        if path:
            try:
                from core.exporter import export_txt
                export_txt(state.current_chapter, path)
                self.ctx.app_state.status_message.emit(f"TXT export: {path}")
            except Exception as exc:
                QMessageBox.critical(self, "Hata", str(exc))

    def _export_srt(self) -> None:
        state = self.ctx.app_state
        if not state.current_chapter or not state.current_chapter.segments:
            QMessageBox.warning(self, "Uyarı", "Dışa aktarılacak segment yok.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "SRT Kaydet",
            f"{state.current_chapter.name}_script.srt", "SRT (*.srt)"
        )
        if path:
            try:
                from core.exporter import export_srt
                export_srt(state.current_chapter, path)
                self.ctx.app_state.status_message.emit(f"SRT export: {path}")
            except Exception as exc:
                QMessageBox.critical(self, "Hata", str(exc))

    # ── Page Lifecycle ─────────────────────────────────────────────

    def showEvent(self, event) -> None:
        state = self.ctx.app_state
        if state.current_project:
            current_data = self.chapter_combo.currentData()
            self.chapter_combo.clear()
            for ch in state.current_project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
            if current_data:
                idx = self.chapter_combo.findData(current_data)
                if idx >= 0:
                    self.chapter_combo.setCurrentIndex(idx)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._autosave_timer.stop()
        self._save_project()
        super().hideEvent(event)
