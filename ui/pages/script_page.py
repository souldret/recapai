"""
RecapAI - Script Sayfası (v2).
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QSplitter, QScrollArea, QPlainTextEdit,
    QFileDialog, QMessageBox, QSizePolicy, QSlider,
    QCheckBox, QSpinBox,
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QPixmap, QUndoStack, QUndoCommand, QTextCursor

from core.context import AppContext
from core.models import SegmentData
from core.qt_image import load_pixmap
from ui.utils.icons import Icons

logger = logging.getLogger(__name__)

LENGTHS = ["short", "medium", "long"]
LENGTH_LABELS = {"short": "Kısa", "medium": "Orta", "long": "Uzun"}
LANGUAGES = [("en", "İngilizce"), ("tr", "Türkçe")]
# Niş modülleri (PDF Prompt 2) — runtime'da list_niches ile de doldurulabilir
NICHES = [
    ("auto",          "Otomatik"),
    ("power_fantasy", "Isekai / Power Fantasy"),
    ("romance",       "Romantik / Duygusal"),
    ("dark_action",   "Karanlık Aksiyon / İntikam"),
    ("comedy",        "Komedi / Slice of Life"),
]
ROLE_LABELS = {
    "cold_open": "Cold open",
    "last_time": "Last time",
    "setup": "Setup",
    "rehook": "Rehook",
    "cliffhanger": "Cliffhanger",
    "filler": "Dolgu",
    "beat": "Beat",
}


# ── Undo Command ───────────────────────────────────────────────────────────────

class EditSegmentCommand(QUndoCommand):
    """QUndo için segment metin değişikliği.

    push() redo() çağırır; metin zaten yazıldığı için ilk redo atlanır.
    Aksi halde setPlainText imleci satır başına atar.
    """

    def __init__(self, card: "SegmentCard", old_text: str, new_text: str) -> None:
        super().__init__("Segment Düzenle")
        self._card = card
        self._old = old_text
        self._new = new_text
        self._closed = False
        self._skip_redo = True

    def undo(self) -> None:
        try:
            self._card.set_text_silent(self._old)
        except RuntimeError:
            pass

    def redo(self) -> None:
        if self._skip_redo:
            self._skip_redo = False
            return
        try:
            self._card.set_text_silent(self._new)
        except RuntimeError:
            pass


# ── Focus-aware editor ─────────────────────────────────────────────────────────

class FocusAwareTextEdit(QPlainTextEdit):
    """Odak callback'i tetikler; yazarken stylesheet imleci sıfırlamaz."""

    def _card(self):
        w = self.parent()
        while w is not None and not isinstance(w, SegmentCard):
            w = w.parent()
        return w

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        card = self._card()
        if card is not None and getattr(card, "_focus_callback", None):
            card._focus_callback(card)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        card = self._card()
        if card is not None:
            card.apply_lint_border()


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
        self._lint_level = None
        self._merge_timer = QTimer(self)
        self._merge_timer.setSingleShot(True)
        self._merge_timer.setInterval(800)
        self._merge_timer.timeout.connect(self._close_undo_merge)
        self._lint_timer = QTimer(self)
        self._lint_timer.setSingleShot(True)
        self._lint_timer.setInterval(280)
        self._lint_timer.timeout.connect(self._run_lint)
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
            px = load_pixmap(self._thumb_path).scaled(
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
        self.lbl_no = QLabel(self._title_label())
        self.lbl_no.setObjectName("cardValue")
        hdr.addWidget(self.lbl_no)
        self.lbl_duration = QLabel(self._dur_label())
        self.lbl_duration.setObjectName("pageSubtitle")
        hdr.addWidget(self.lbl_duration)
        self.lbl_lint = QLabel("")
        self.lbl_lint.setObjectName("pageSubtitle")
        self.lbl_lint.setWordWrap(True)
        hdr.addWidget(self.lbl_lint, 1)
        hdr.addStretch()
        mid.addLayout(hdr)

        self.text_edit = FocusAwareTextEdit(self)
        self.text_edit.setUndoRedoEnabled(False)
        self.text_edit.setPlainText(self.segment.text)
        self.text_edit.setFixedHeight(110)
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
        self._refresh_lint()

    def _title_label(self) -> str:
        role = ROLE_LABELS.get(getattr(self.segment, "role", "") or "", "")
        img = getattr(self.segment, "image_index", None)
        try:
            img_n = int(img) + 1 if img is not None else self.segment_index + 1
        except (TypeError, ValueError):
            img_n = self.segment_index + 1
        base = f"Görsel {img_n}"
        return f"{base} · {role}" if role else base

    def _dur_label(self) -> str:
        return f"⏱ {self.segment.duration:.1f}s"

    def _lint_state(self) -> str:
        issues = getattr(self.segment, "lint_issues", None) or []
        if not issues:
            return ""
        err = any(
            "jenerik" in x.lower() or "panel meta" in x.lower() or "etiket" in x.lower()
            for x in issues
        )
        return "error" if err else "warn"

    def _refresh_lint(self, *, apply_border: bool = False) -> None:
        issues = getattr(self.segment, "lint_issues", None) or []
        level = self._lint_state()
        if issues:
            self.lbl_lint.setText("⚠ " + " · ".join(issues[:3]))
            self.lbl_lint.setStyleSheet(
                "color: #ef4444;" if level == "error" else "color: #f59e0b;"
            )
        else:
            self.lbl_lint.setText("")
            self.lbl_lint.setStyleSheet("")
        if apply_border or not self.text_edit.hasFocus():
            self.apply_lint_border()

    def apply_lint_border(self) -> None:
        """Kart kenarlığını yalnızca seviye değişince günceller (imleç sıçramasın)."""
        level = self._lint_state()
        if level == self._lint_level:
            return
        self._lint_level = level
        if level == "error":
            self.setStyleSheet("#segmentCard { border: 1px solid #7f1d1d; }")
        elif level == "warn":
            self.setStyleSheet("#segmentCard { border: 1px solid #92400e; }")
        else:
            self.setStyleSheet("")

    def _on_text_changed(self) -> None:
        if self._building:
            return
        new_text = self.text_edit.toPlainText()
        if new_text == self._last_text:
            return
        old_text = self._last_text
        self._last_text = new_text
        self.segment.text = new_text
        self._lint_timer.start()
        self._push_or_merge_undo(old_text, new_text)

    def _is_last_card(self) -> bool:
        page = self.parent()
        while page is not None and not hasattr(page, "_cards"):
            page = page.parent() if hasattr(page, "parent") else None
        cards = getattr(page, "_cards", None) if page is not None else None
        if not cards:
            return False
        return cards[-1] is self

    def _run_lint(self) -> None:
        text = self.text_edit.toPlainText()
        from core.script_generator import ScriptGenerator
        language = "en"
        page = self.parent()
        while page is not None and not hasattr(page, "lang_combo"):
            page = page.parent() if hasattr(page, "parent") else None
        if page is not None and hasattr(page, "lang_combo"):
            language = page.lang_combo.currentData() or "en"
        self.segment.duration = ScriptGenerator.estimate_duration(text, language)
        self.lbl_duration.setText(self._dur_label())
        from core.script_linter import lint_text
        self.segment.lint_issues = lint_text(
            text,
            role=getattr(self.segment, "role", "") or "",
            is_first=self.segment_index == 0,
            is_last=self._is_last_card(),
        )
        self._refresh_lint(apply_border=False)

    def _push_or_merge_undo(self, old_text: str, new_text: str) -> None:
        stack = self._undo_stack
        last = stack.command(stack.count() - 1) if stack.count() else None
        if (
            isinstance(last, EditSegmentCommand)
            and last._card is self
            and not last._closed
            and stack.index() == stack.count()
        ):
            last._new = new_text
        else:
            stack.push(EditSegmentCommand(self, old_text, new_text))
        self._merge_timer.start()

    def _close_undo_merge(self) -> None:
        stack = self._undo_stack
        last = stack.command(stack.count() - 1) if stack.count() else None
        if isinstance(last, EditSegmentCommand) and last._card is self:
            last._closed = True

    def set_text_silent(self, text: str) -> None:
        """Undo/Redo sırasında signal döngüsünü kırar; imleci korur."""
        self._building = True
        edit = self.text_edit
        cursor = edit.textCursor()
        pos = cursor.position()
        edit.blockSignals(True)
        current = edit.toPlainText()
        if current != text:
            cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(text)
            cursor.endEditBlock()
            cursor = edit.textCursor()
            cursor.setPosition(min(max(0, pos), len(text)))
            edit.setTextCursor(cursor)
        edit.blockSignals(False)
        self._last_text = text
        self.segment.text = text
        self._building = False

    def update_segment(self, segment: SegmentData) -> None:
        self.segment = segment
        self._last_text = segment.text
        self.set_text_silent(segment.text)
        self.lbl_duration.setText(self._dur_label())
        self.lbl_no.setText(self._title_label())
        self._refresh_lint()


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
        px = load_pixmap(image_path)
        if not px.isNull():
            scaled = px.scaled(
                self.img_lbl.width() - 8, self.img_lbl.height() - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_lbl.setPixmap(scaled)
        else:
            self.img_lbl.setText("Yüklenemedi")

        scene = analysis.get("scene", "—") if isinstance(analysis, dict) else "—"
        self.lbl_scene.setText(f"Sahne: {str(scene)[:80]}")
        raw_chars = analysis.get("characters", []) if isinstance(analysis, dict) else []
        try:
            from core.script_generator import format_characters_display
            shown = format_characters_display(raw_chars)
        except Exception:
            shown = "—"
        self.lbl_chars.setText(f"Karakterler: {shown}")
        mood = analysis.get("mood", "—") if isinstance(analysis, dict) else "—"
        self.lbl_mood.setText(f"Atmosfer: {mood}")

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
        self._live_workers: list = []
        self._cards: List[SegmentCard] = []
        self._active_card_index: int = -1
        self._loaded_chapter_id = None
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
        sub = QLabel("Tek bir hikâye anlatısı üretir — her beat bir VO, paneller bağımsız cümle değil.")
        sub.setObjectName("pageSubtitle")
        v.addWidget(sub)
        return w

    def _make_toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(10)

        # Satır 1: Bölüm + model
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(12)

        row1.addWidget(QLabel("Bölüm:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(180)
        self.chapter_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.chapter_combo.setPlaceholderText("Seçin…")
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        row1.addWidget(self.chapter_combo, 1)

        row1.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._populate_model_combo()
        row1.addWidget(self.model_combo, 2)
        vbox.addLayout(row1)

        # Satır 2: Niş, uzunluk, dil, süre
        row2a = QHBoxLayout()
        row2a.setContentsMargins(0, 0, 0, 0)
        row2a.setSpacing(12)

        row2a.addWidget(QLabel("Niş:"))
        self.niche_combo = QComboBox()
        self.niche_combo.setMinimumWidth(200)
        self.niche_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.niche_combo.setToolTip(
            "Manhwa türüne göre ton: power fantasy, romance, dark action, comedy."
        )
        for key, label in NICHES:
            self.niche_combo.addItem(label, key)
        try:
            from core.script_generator import list_niches
            for n in list_niches():
                idx = self.niche_combo.findData(n["id"])
                if idx >= 0 and n.get("description"):
                    self.niche_combo.setItemData(idx, n["description"], Qt.ItemDataRole.ToolTipRole)
        except Exception:
            pass
        row2a.addWidget(self.niche_combo, 2)

        row2a.addWidget(QLabel("Uzunluk:"))
        self.length_slider = QSlider(Qt.Orientation.Horizontal)
        self.length_slider.setRange(0, 2)
        self.length_slider.setValue(1)
        self.length_slider.setMinimumWidth(140)
        self.length_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        row2a.addWidget(self.length_slider, 1)
        self.lbl_length = QLabel("Orta")
        self.lbl_length.setMinimumWidth(48)
        self.lbl_length.setObjectName("pageSubtitle")
        row2a.addWidget(self.lbl_length)

        row2a.addWidget(QLabel("Dil:"))
        self.lang_combo = QComboBox()
        for key, label in LANGUAGES:
            self.lang_combo.addItem(label, key)
        self.lang_combo.setMinimumWidth(120)
        row2a.addWidget(self.lang_combo)

        self.auto_duration_check = QCheckBox("Süre otomatik")
        self.auto_duration_check.setChecked(True)
        self.auto_duration_check.setToolTip(
            "Açıkken süre görsel sayısına göre hesaplanır (Orta ~6–8 sn/kare).\n"
            "14 görsel ≈ 1.5–2 dk. 6 dakikaya şişirmez, cümleyi kelime kelime kesmez.\n"
            "Kısa: sıkı 1–2 cümle/beat. Kapatınca dakikayı elle seçersin."
        )
        self.auto_duration_check.toggled.connect(self._on_auto_duration_toggled)
        row2a.addWidget(self.auto_duration_check)

        row2a.addWidget(QLabel("Hedef dk:"))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(2, 180)
        self.minutes_spin.setValue(6)
        self.minutes_spin.setEnabled(False)
        self.minutes_spin.setFixedWidth(72)
        self.minutes_spin.setToolTip(
            "Elle hedef süre. Süre otomatik kapalıyken kullanılır.\n"
            "Hikayeyi kesmez; yalnızca üst tavan. 14 görseli 6 dk'ya şişirmez."
        )
        row2a.addWidget(self.minutes_spin)
        vbox.addLayout(row2a)

        # Satır 3: Anlatı seçenekleri
        row2b = QHBoxLayout()
        row2b.setContentsMargins(0, 0, 0, 0)
        row2b.setSpacing(16)

        self.hook_check = QCheckBox("Cold open")
        self.hook_check.setChecked(True)
        self.hook_check.setToolTip(
            "Her videonun ilk 3 saniyesine flash-forward kanca ekler.\n"
            "Kapalıysa düz kronolojik açılış."
        )
        row2b.addWidget(self.hook_check)

        self.ab_hook_check = QCheckBox("A/B kanca")
        self.ab_hook_check.setToolTip(
            "İkinci kancayı üretir; VO'ya gömmez. A/B seçiciden hangisinin açılışta duracağını seç."
        )
        row2b.addWidget(self.ab_hook_check)

        row2b.addWidget(QLabel("Kanca:"))
        self.hook_variant_combo = QComboBox()
        self.hook_variant_combo.setMinimumWidth(72)
        self.hook_variant_combo.setToolTip("Üretilen A/B kancalardan birini cold open'a uygula.")
        self.hook_variant_combo.currentIndexChanged.connect(self._on_hook_variant_chosen)
        row2b.addWidget(self.hook_variant_combo)

        self.last_time_check = QCheckBox("Last time")
        self.last_time_check.setToolTip(
            "Bölüm 2+ için önceki bölümden 1-2 cümle köprü.\n"
            "İlk bölümde otomatik kapalı önerilir."
        )
        row2b.addWidget(self.last_time_check)

        self.compile_check = QCheckBox("Derleme (tüm bölümler)")
        self.compile_check.setToolTip(
            "Projedeki analizli bölümleri tek recap'te birleştirir."
        )
        row2b.addWidget(self.compile_check)
        row2b.addStretch()
        vbox.addLayout(row2b)

        self.length_slider.valueChanged.connect(self._on_length_changed)

        # Satır 4: Aksiyonlar
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
        self.btn_generate.setObjectName("primaryButton")
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

        self.lbl_lint_summary = QLabel("Lint: 0")
        self.lbl_lint_summary.setObjectName("pageSubtitle")
        hbox.addWidget(self.lbl_lint_summary)

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

    def _on_length_changed(self, value: int) -> None:
        self.lbl_length.setText(LENGTH_LABELS[LENGTHS[value]])
        defaults = {"short": 3, "medium": 6, "long": 9}
        self.minutes_spin.setValue(defaults.get(LENGTHS[value], 6))
        self._refresh_auto_duration_hint()

    def _on_auto_duration_toggled(self, checked: bool) -> None:
        self.minutes_spin.setEnabled(not checked)
        self._refresh_auto_duration_hint()

    def _refresh_auto_duration_hint(self) -> None:
        if not getattr(self, "auto_duration_check", None):
            return
        if not self.auto_duration_check.isChecked():
            return
        chapter_id = self.chapter_combo.currentData() if getattr(self, "chapter_combo", None) else None
        chapter = None
        state = self.ctx.app_state
        if chapter_id and state.current_project:
            chapter = state.current_project.get_chapter(chapter_id)
        n = len(getattr(chapter, "images", None) or []) if chapter else 0
        if n <= 0:
            return
        from core.script_generator import resolve_target_minutes
        length = LENGTHS[self.length_slider.value()]
        language = self.lang_combo.currentData() or "en"
        resolved = resolve_target_minutes(length, None, n_images=n, language=language)
        if resolved:
            self.minutes_spin.setValue(max(2, min(180, int(round(resolved)))))

    # ── Helpers ────────────────────────────────────────────────────

    def _populate_model_combo(self) -> None:
        self.model_combo.clear()
        try:
            from core.model_catalog import get_models, format_model_label, model_tooltip, default_model_id
            models = get_models("script_models")
            if not models:
                raise ValueError("boş katalog")
            for m in models:
                label = format_model_label(m)
                self.model_combo.addItem(label, m["id"])
                idx = self.model_combo.count() - 1
                tip = model_tooltip(m)
                if tip:
                    self.model_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
            pref = None
            try:
                pref = self.ctx.settings_manager.get("defaults.script_model", "")
            except Exception:
                pref = ""
            if not pref:
                pref = default_model_id("script_models")
            if pref:
                i = self.model_combo.findData(pref)
                if i >= 0:
                    self.model_combo.setCurrentIndex(i)
        except Exception:
            self.model_combo.addItem(
                "Gemini 2.5 Flash  ·  Fiyat/Performans  [$]",
                "google/gemini-2.5-flash",
            )
            self.model_combo.addItem(
                "DeepSeek V3  ·  Bütçe  [$]",
                "deepseek/deepseek-chat-v3-0324",
            )
            self.model_combo.addItem(
                "Claude Sonnet 4  ·  Performans  [$$$]",
                "anthropic/claude-sonnet-4",
            )

    def _get_api_key(self) -> str:
        try:
            return self.ctx.settings_manager.get_api_key()
        except Exception:
            return self.ctx.app_state.get_setting("api", "openrouter_api_key", default="")

    def _get_thumbnail(self, image_index: int, image_path: Optional[str] = None) -> Optional[str]:
        if image_path:
            return image_path
        state = self.ctx.app_state
        chapter = state.current_chapter
        if not chapter:
            return None
        images = chapter.images
        if image_index < 0 or image_index >= len(images):
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
        if chapter is None:
            return
        idx = self.chapter_combo.findData(getattr(chapter, "id", None))
        if idx >= 0 and self.chapter_combo.currentIndex() != idx:
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.setCurrentIndex(idx)
            self.chapter_combo.blockSignals(False)
            self._load_segments(chapter)
            self._refresh_hook_variants(chapter)

    def _refresh_hook_variants(self, chapter=None) -> None:
        if not getattr(self, "hook_variant_combo", None):
            return
        chapter = chapter or self.ctx.app_state.current_chapter
        meta = dict(getattr(chapter, "script_meta", None) or {}) if chapter else {}
        variants = meta.get("hook_variants") or []
        selected = str(meta.get("selected_hook") or "A")
        self.hook_variant_combo.blockSignals(True)
        self.hook_variant_combo.clear()
        if not variants:
            self.hook_variant_combo.addItem("A", "A")
        else:
            for item in variants:
                hid = str(item.get("id") or "A")
                preview = (item.get("text") or "")[:40]
                self.hook_variant_combo.addItem(f"{hid}: {preview}", hid)
        idx = self.hook_variant_combo.findData(selected)
        if idx >= 0:
            self.hook_variant_combo.setCurrentIndex(idx)
        self.hook_variant_combo.blockSignals(False)

    def _on_hook_variant_chosen(self, _index: int) -> None:
        chapter = self.ctx.app_state.current_chapter
        if not chapter or not getattr(self, "hook_variant_combo", None):
            return
        hid = self.hook_variant_combo.currentData()
        meta = dict(getattr(chapter, "script_meta", None) or {})
        variants = meta.get("hook_variants") or []
        chosen = next((v for v in variants if str(v.get("id")) == str(hid)), None)
        if not chosen:
            return
        from core.script_quality import apply_selected_hook, store_hook_variants
        language = self.lang_combo.currentData() or "en"
        apply_selected_hook(chapter.segments or [], chosen.get("text") or "", language)
        store_hook_variants(chapter, variants, selected=str(hid))
        self._load_segments(chapter)
        self._save_project()

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
            try:
                from core.script_generator import _is_first_chapter
                first = _is_first_chapter(chapter)
                self.hook_check.setChecked(True)
                self.last_time_check.setChecked(not first)
            except Exception:
                self.hook_check.setChecked(True)
            self._refresh_auto_duration_hint()
            self._refresh_hook_variants(chapter)

    # ── Segment Loading ────────────────────────────────────────────

    def _load_segments(self, chapter) -> None:
        self._undo_stack.clear()
        self._clear_cards()
        self._loaded_chapter_id = getattr(chapter, "id", None)
        if not chapter.segments:
            return
        for i, seg in enumerate(chapter.segments):
            thumb = self._get_thumbnail(seg.image_index, getattr(seg, "image_path", None))
            self._add_card(i, seg, thumb)
        self._update_stats()
        if self._cards:
            self._activate_card(0)

    def _clear_cards(self) -> None:
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        self._active_card_index = -1
        self._loaded_chapter_id = None
        # Stretch'i koru
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_card(self, index: int, seg: SegmentData, thumb: Optional[str]) -> SegmentCard:
        card = SegmentCard(index, seg, thumb, self._undo_stack)
        card.btn_del.clicked.connect(lambda _checked=False, c=card: self._delete_card(c))
        card.btn_regen.clicked.connect(lambda _checked=False, c=card: self._regen_card(c))
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
        extra = getattr(card.segment, "image_path", None)
        if extra:
            image_path = extra
        elif isinstance(img_idx, int) and 0 <= img_idx < len(images):
            image_path = images[img_idx].path
        else:
            image_path = None
        if image_path:
            analysis = ch.analysis_data.get(str(img_idx), {}) if 0 <= img_idx < len(images) else {}
            if not isinstance(analysis, dict):
                analysis = {}
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

        has_analysis = any(str(k).isdigit() for k in (chapter.analysis_data or {}))
        if not has_analysis and not self.compile_check.isChecked():
            QMessageBox.warning(
                self, "Analiz Verisi Yok",
                f"'{chapter.name}' bölümü için önce AI Analiz yapın."
            )
            return

        try:
            from core.pipeline import require_character_bible
            require_character_bible(state.current_project, chapter)
        except ValueError as bible_exc:
            QMessageBox.warning(self, "Karakter Bible", str(bible_exc))
            return

        api_key = self._get_api_key()
        if not api_key:
            QMessageBox.warning(self, "API Anahtarı Eksik",
                                "Ayarlar > API sekmesinden OpenRouter anahtarını girin.")
            return

        model = self.model_combo.currentData() or "google/gemini-2.5-flash"
        style = "fresh"
        length = LENGTHS[self.length_slider.value()]
        language = self.lang_combo.currentData() or "en"
        niche = self.niche_combo.currentData() or "auto"
        use_hook = self.hook_check.isChecked()
        auto_niche = niche == "auto"
        compile_chapters = None
        if self.compile_check.isChecked() and state.current_project:
            compile_chapters = [
                ch for ch in state.current_project.chapters
                if any(str(k).isdigit() for k in (ch.analysis_data or {}))
            ]
            if not compile_chapters:
                QMessageBox.warning(self, "Derleme", "Analizli bölüm yok.")
                return

        from core.script_generator import resolve_target_minutes

        target_minutes = None
        if not self.auto_duration_check.isChecked():
            target_minutes = float(self.minutes_spin.value())
        n_images = len(getattr(chapter, "images", None) or [])
        if self.compile_check.isChecked() and compile_chapters:
            n_images = sum(len(getattr(ch, "images", None) or []) for ch in compile_chapters)
        resolved_minutes = resolve_target_minutes(
            length, target_minutes, n_images=n_images, language=language,
        )

        from ui.workers.script_worker import ScriptWorker
        from ui.workers.thread_utils import start_worker
        self._worker = ScriptWorker(
            chapter, model, api_key, style, length, language,
            niche=niche, use_hook=use_hook,
            project=state.current_project,
            target_minutes=target_minutes,
            auto_niche=auto_niche,
            include_last_time=self.last_time_check.isChecked(),
            compile_chapters=compile_chapters,
            hook_variants=2 if self.ab_hook_check.isChecked() else 1,
            parent=self,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_generation_finished)
        self._worker.cancelled.connect(self._on_generation_cancelled)
        self._worker.error.connect(self._on_generation_error)

        self._clear_cards()
        self.btn_generate.setEnabled(False)
        self.btn_stop.setEnabled(True)
        extra = []
        if use_hook:
            extra.append("cold open")
        if self.last_time_check.isChecked():
            extra.append("last time")
        if self.compile_check.isChecked():
            extra.append("derleme")
        if resolved_minutes:
            extra.append(f"~{resolved_minutes:.1f} dk")
        else:
            extra.append("kısa / sıkı")
        note = (" · " + " + ".join(extra)) if extra else ""
        self.lbl_stream.setText(
            f"<span style='color:#6366f1;'><b>ÜRETİLİYOR:</b> "
            f"Manhwa Fresh · {self.niche_combo.currentText()}{note}...</span>"
        )
        self.ctx.app_state.status_message.emit("Script üretimi başladı…")
        start_worker(self, self._worker)

    def _stop_generation(self) -> None:
        if self._worker and self._worker.isRunning():
            from ui.workers.thread_utils import abort_worker
            abort_worker(self, self._worker)
            self.lbl_stream.setText(
                "<span style='color:#ef4444;'><b>DURDURULDU:</b> Durdurma sinyali gönderildi…</span>"
            )
            self.btn_stop.setEnabled(False)
            return
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)
        chapter = self.ctx.app_state.current_chapter
        if chapter and chapter.segments and not self._cards:
            self._load_segments(chapter)

    def _on_chunk(self, chunk: str) -> None:
        current = self.lbl_stream.text()
        # Son 120 karakteri göster
        preview = (current + chunk)[-120:]
        self.lbl_stream.setText(preview)

    def _on_progress(self, msg: str) -> None:
        self.ctx.app_state.status_message.emit(msg)

    def _on_generation_finished(self, segments: list) -> None:
        self._autosave_timer.stop()
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
        self._refresh_hook_variants(chapter)
        self._save_project()
        self.ctx.app_state.status_message.emit(f"Script hazır: {len(segments)} segment")

    def _on_generation_cancelled(self) -> None:
        self._autosave_timer.stop()
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_stream.setText(
            "<span style='color:#e0af68;'><b>DURDURULDU:</b> Script üretimi kesildi.</span>"
        )
        chapter = self.ctx.app_state.current_chapter
        if chapter and chapter.segments and not self._cards:
            self._load_segments(chapter)

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
        was_active = idx == self._active_card_index
        seg_idx = getattr(card, "segment_index", idx)
        self._cards_layout.removeWidget(card)
        card.deleteLater()
        self._cards.pop(idx)

        state = self.ctx.app_state
        chapter_id = self.chapter_combo.currentData()
        if state.current_project and chapter_id:
            chapter = state.current_project.get_chapter(chapter_id)
            if chapter and 0 <= seg_idx < len(chapter.segments):
                chapter.segments.pop(seg_idx)

        last_i = len(self._cards) - 1
        for i, c in enumerate(self._cards):
            if getattr(c, "segment_index", 0) > seg_idx:
                c.segment_index -= 1
            c.lbl_no.setText(c._title_label())
            from core.script_linter import lint_text
            c.segment.lint_issues = lint_text(
                c.text_edit.toPlainText(),
                role=getattr(c.segment, "role", "") or "",
                is_first=i == 0,
                is_last=i == last_i,
            )
            c._refresh_lint(apply_border=True)

        if not self._cards:
            self._active_card_index = -1
            self.preview_side.clear()
        elif was_active or self._active_card_index >= len(self._cards):
            self._activate_card(min(idx, len(self._cards) - 1))
        elif self._active_card_index > idx:
            self._active_card_index -= 1

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

        model = self.model_combo.currentData() or "google/gemini-2.5-flash"
        style = "fresh"
        language = self.lang_combo.currentData() or "en"
        length = LENGTHS[self.length_slider.value()]
        niche = self.niche_combo.currentData() or "auto"
        if niche == "auto":
            niche = "power_fantasy"
        idx = getattr(card, "segment_index", self._cards.index(card))

        from ui.workers.script_worker import RegenerateSegmentWorker
        from ui.workers.thread_utils import start_worker
        self._regen_worker = RegenerateSegmentWorker(
            chapter, idx, model, api_key, style, language, length, niche=niche,
            project=state.current_project,
            parent=self,
        )
        self._regen_worker.finished.connect(lambda i, s, w=self._regen_worker: self._on_regen_done(i, s, w))
        self._regen_worker.error.connect(lambda m, w=self._regen_worker: self._on_regen_error(m, w))
        card.btn_regen.setEnabled(False)
        card.btn_regen.setIcon(Icons.get(Icons.PROCESSING, color="#e0af68"))
        start_worker(self, self._regen_worker)

    def _on_regen_done(self, index: int, segment: SegmentData, worker=None) -> None:
        if worker is not None and worker is not self._regen_worker:
            return
        for card in self._cards:
            if getattr(card, "segment_index", -1) == index:
                card.update_segment(segment)
                card.btn_regen.setEnabled(True)
                card.btn_regen.setIcon(Icons.get(Icons.REFRESH))
                break
        self._update_stats()
        self._save_project(quiet=True)

    def _on_regen_error(self, msg: str, worker=None) -> None:
        if worker is not None and worker is not self._regen_worker:
            return
        for card in self._cards:
            card.btn_regen.setEnabled(True)
            card.btn_regen.setIcon(Icons.get(Icons.REFRESH))
        QMessageBox.critical(self, "Hata", msg)

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
        issues = 0
        for card in self._cards:
            issues += len(getattr(card.segment, "lint_issues", None) or [])
        self.lbl_lint_summary.setText(f"Lint: {issues}")
        if issues:
            self.lbl_lint_summary.setStyleSheet("color: #f59e0b;")
        else:
            self.lbl_lint_summary.setStyleSheet("color: #22c55e;")

    # ── Save ───────────────────────────────────────────────────────

    def _save_project(self, *, quiet: bool = False) -> None:
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
                    chapter.segments[card.segment_index].duration = card.segment.duration
            from core.project_manager import save_project
            try:
                save_project(state.current_project)
                logger.debug("Proje otomatik kaydedildi.")
            except Exception as exc:
                logger.error("Kaydetme hatası: %s", exc)
                if not quiet:
                    QMessageBox.warning(self, "Kayıt hatası", f"Proje kaydedilemedi:\n{exc}")

    def _autosave(self) -> None:
        self._save_project(quiet=True)

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
        super().showEvent(event)
        state = self.ctx.app_state
        if not state.current_project:
            return
        current_data = self.chapter_combo.currentData()
        self.chapter_combo.blockSignals(True)
        self.chapter_combo.clear()
        for ch in state.current_project.chapters:
            self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
        target = current_data or (state.current_chapter.id if state.current_chapter else None)
        if target:
            idx = self.chapter_combo.findData(target)
            if idx >= 0:
                self.chapter_combo.setCurrentIndex(idx)
        self.chapter_combo.blockSignals(False)
        selected = self.chapter_combo.currentData()
        if selected and selected == self._loaded_chapter_id and self._cards:
            return
        if self.chapter_combo.currentIndex() >= 0:
            self._on_chapter_changed(self.chapter_combo.currentIndex())

    def hideEvent(self, event) -> None:
        self._autosave_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.stop()
        if self._regen_worker and self._regen_worker.isRunning() and hasattr(self._regen_worker, "stop"):
            self._regen_worker.stop()
        self._save_project()
        super().hideEvent(event)
