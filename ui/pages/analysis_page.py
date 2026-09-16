"""
RecapAI - AI Analiz Sayfası (v2).
"""

import json
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QProgressBar, QComboBox, QTextEdit, QListWidget,
    QListWidgetItem, QSplitter, QAbstractItemView, QFileDialog,
    QMessageBox, QSizePolicy, QLineEdit, QDialog, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QColor

from core.context import AppContext
from ui.utils.icons import Icons, ICON_COLOR_ACTIVE, ICON_COLOR_SUCCESS, ICON_COLOR_WARNING, ICON_COLOR_ERROR, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)


def _save_current_project(widget, project) -> bool:
    if not project:
        return False
    try:
        from core.project_manager import save_project
        save_project(project)
        return True
    except Exception as exc:
        logger.error("Proje kaydedilemedi: %s", exc)
        QMessageBox.warning(widget, "Kayıt hatası", f"Proje kaydedilemedi:\n{exc}")
        return False


STATUS_ICONS = {
    "pending":   (Icons.PENDING, ICON_COLOR_MUTED),
    "running":   (Icons.PROCESSING, ICON_COLOR_WARNING),
    "done":      (Icons.SUCCESS, ICON_COLOR_SUCCESS),
    "error":     (Icons.ERROR, ICON_COLOR_ERROR),
    "cached":    (Icons.SAVE, ICON_COLOR_ACTIVE),
}


# ── Görsel Listesi ─────────────────────────────────────────────────────────────

class ImageStatusItem(QListWidgetItem):
    """Analiz durum listesi öğesi."""

    def __init__(self, index: int, filename: str) -> None:
        super().__init__()
        self.image_index = index
        self.filename = filename
        self.status = "pending"
        self._update_text()
        self.setSizeHint(QSize(0, 48))

    def set_status(self, status: str) -> None:
        self.status = status
        self._update_text()

    def _update_text(self) -> None:
        icon_name, color = STATUS_ICONS.get(self.status, (Icons.PENDING, ICON_COLOR_MUTED))
        self.setIcon(Icons.get(icon_name, color=color))
        short = self.filename[:28] + "…" if len(self.filename) > 28 else self.filename
        self.setText(f"{self.image_index + 1:02d}.  {short}")


# ── Detay Paneli ───────────────────────────────────────────────────────────────

class AnalysisDetailPanel(QFrame):
    """Sağ panel: seçili görselin analiz detayları."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self._current_index: int = -1
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Başlık + buton
        hbox = QHBoxLayout()
        self.title_lbl = QLabel("Analiz Detayı")
        self.title_lbl.setObjectName("headingLabel")
        hbox.addWidget(self.title_lbl)
        hbox.addStretch()
        self.btn_reanalyze = QPushButton("  Tekrar Analiz")
        self.btn_reanalyze.setFixedWidth(160)
        self.btn_reanalyze.setObjectName("secondaryBtn")
        self.btn_reanalyze.setIcon(Icons.get(Icons.REFRESH))
        self.btn_reanalyze.setIconSize(QSize(16, 16))
        self.btn_reanalyze.setEnabled(False)
        hbox.addWidget(self.btn_reanalyze)
        layout.addLayout(hbox)

        # Bilgi kartları
        self.cards_row = QHBoxLayout()
        self.cards_row.setSpacing(8)
        layout.addLayout(self.cards_row)

        # JSON görünümü
        json_lbl = QLabel("Ham JSON:")
        json_lbl.setObjectName("pageSubtitle")
        layout.addWidget(json_lbl)

        self.json_view = QTextEdit()
        self.json_view.setReadOnly(True)
        self.json_view.setObjectName("logView")
        self.json_view.setPlaceholderText("Listeden bir görsel seçin…")
        layout.addWidget(self.json_view, 1)

    def show_result(self, index: int, data: dict) -> None:
        """Analiz sonucunu gösterir."""
        self._current_index = index
        self.btn_reanalyze.setEnabled(True)
        self.title_lbl.setText(f"Görsel {index + 1} — Analiz Sonucu")

        # Eski kartları temizle
        while self.cards_row.count():
            item = self.cards_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not data.get("parse_error") and not data.get("error"):
            from core.script_generator import format_characters_display

            for icon_name, key, label in [
                (Icons.GLOBE, "setting", "Mekan"),
                (Icons.MOOD, "mood", "Atmosfer"),
                (Icons.FLASH, "important", "Önemli"),
            ]:
                val = data.get(key, "—")
                if isinstance(val, bool):
                    val = "Evet" if val else "Hayır"
                self.cards_row.addWidget(self._mini_card(icon_name, label, str(val)[:30]))
            self.cards_row.addWidget(
                self._mini_card(
                    Icons.ACCOUNT,
                    "Karakterler",
                    format_characters_display(data.get("characters"))[:48],
                )
            )

        self.cards_row.addStretch()

        # JSON pretty print
        try:
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
        except Exception:
            pretty = str(data)
        self.json_view.setPlainText(pretty)

    def clear(self) -> None:
        self._current_index = -1
        self.btn_reanalyze.setEnabled(False)
        self.title_lbl.setText("Analiz Detayı")
        self.json_view.clear()
        while self.cards_row.count():
            item = self.cards_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _mini_card(self, icon_name: str, label: str, value: str) -> QFrame:
        f = QFrame()
        f.setObjectName("statCard")
        f.setMinimumSize(130, 65)
        f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(8)

        # Icon on the left
        icon_lbl = QLabel()
        icon_lbl.setPixmap(Icons.pixmap(icon_name, size=20, color=ICON_COLOR_ACTIVE))
        h.addWidget(icon_lbl)

        # Text fields on the right
        v = QVBoxLayout()
        v.setSpacing(2)
        v.setContentsMargins(0, 0, 0, 0)

        lbl_title = QLabel(label)
        lbl_title.setObjectName("cardSubtitle")
        v.addWidget(lbl_title)

        val_lbl = QLabel(value)
        val_lbl.setObjectName("cardValueSmall")
        val_lbl.setWordWrap(True)
        v.addWidget(val_lbl)

        h.addLayout(v, 1)
        return f


class EditCharacterDialog(QDialog):
    """Kadro kaydı: isim, cinsiyet, görünüm, alias, not."""

    def __init__(self, entry: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Karakteri düzenle")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._entry = entry or {}
        self._build_ui()

    def _build_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setSpacing(10)
        vbox.setContentsMargins(24, 20, 24, 20)

        vbox.addWidget(QLabel("İsim:"))
        self.name_edit = QLineEdit()
        self.name_edit.setText(self._entry.get("canonical") or "")
        self.name_edit.setPlaceholderText("Jin-Woo")
        vbox.addWidget(self.name_edit)

        vbox.addWidget(QLabel("Cinsiyet:"))
        self.gender_combo = QComboBox()
        self.gender_combo.addItem("—", "")
        self.gender_combo.addItem("Erkek", "male")
        self.gender_combo.addItem("Kadın", "female")
        self.gender_combo.addItem("Belirsiz", "unknown")
        gender = self._entry.get("gender") or ""
        idx = self.gender_combo.findData(gender)
        self.gender_combo.setCurrentIndex(idx if idx >= 0 else 0)
        vbox.addWidget(self.gender_combo)

        vbox.addWidget(QLabel("Görünüm:"))
        self.appearance_edit = QLineEdit()
        self.appearance_edit.setText(self._entry.get("appearance") or "")
        self.appearance_edit.setPlaceholderText("black hair, black coat")
        vbox.addWidget(self.appearance_edit)

        vbox.addWidget(QLabel("Takma adlar (virgülle):"))
        self.aliases_edit = QLineEdit()
        aliases = [a for a in (self._entry.get("aliases") or []) if a]
        self.aliases_edit.setText(", ".join(aliases))
        self.aliases_edit.setPlaceholderText("Sung, Hunter")
        vbox.addWidget(self.aliases_edit)

        vbox.addWidget(QLabel("Not:"))
        self.notes_edit = QLineEdit()
        self.notes_edit.setText(self._entry.get("notes") or "")
        vbox.addWidget(self.notes_edit)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("Kaydet")
            ok_btn.setObjectName("primaryButton")
        vbox.addWidget(btn_box)

    def values(self) -> dict:
        aliases = [
            a.strip() for a in (self.aliases_edit.text() or "").split(",") if a.strip()
        ]
        return {
            "canonical": (self.name_edit.text() or "").strip(),
            "gender": self.gender_combo.currentData() or "",
            "appearance": (self.appearance_edit.text() or "").strip(),
            "aliases": aliases,
            "notes": (self.notes_edit.text() or "").strip(),
        }


# ── Ana Sayfa ──────────────────────────────────────────────────────────────────

class AnalysisPage(QWidget):
    """AI görsel analiz sayfası (v2)."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._live_workers: list = []
        self._build_ui()
        self._connect_app_state()
        logger.debug("AnalysisPage oluşturuldu.")

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        root.addWidget(self._make_header())
        root.addWidget(self._make_toolbar())
        root.addWidget(self._make_progress_row())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: görsel listesi
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        lv.addWidget(QLabel("Görseller:"))
        self.image_list = QListWidget()
        self.image_list.setObjectName("imageStatusList")
        self.image_list.currentRowChanged.connect(self._on_image_selected)
        lv.addWidget(self.image_list, 1)
        lv.addWidget(self._make_roster_panel())
        splitter.addWidget(left)

        # Sağ: detay panel
        self.detail_panel = AnalysisDetailPanel()
        self.detail_panel.btn_reanalyze.clicked.connect(self._on_reanalyze)
        splitter.addWidget(self.detail_panel)

        splitter.setSizes([400, 600])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        root.addWidget(self._make_log_row())

    def _make_header(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        title = QLabel("AI Analiz")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        sub = QLabel("Vision AI ile manhwa görsellerini analiz edin.")
        sub.setObjectName("pageSubtitle")
        v.addWidget(sub)
        return w

    def _make_toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(16, 12, 16, 12)
        vbox.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(12)

        row1.addWidget(QLabel("Bölüm:"))
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(180)
        self.chapter_combo.setPlaceholderText("Bölüm seçin…")
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        row1.addWidget(self.chapter_combo, 1)

        row1.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self._populate_model_combo()
        row1.addWidget(self.model_combo, 2)

        vbox.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(10)
        row2.addStretch()

        self.btn_start = QPushButton("  Analizi Başlat")
        self.btn_start.setMinimumWidth(160)
        self.btn_start.setIcon(Icons.get(Icons.PLAY, color="#ffffff"))
        self.btn_start.setIconSize(QSize(16, 16))
        self.btn_start.clicked.connect(self._start_analysis)
        row2.addWidget(self.btn_start)

        self.btn_stop = QPushButton("  Durdur")
        self.btn_stop.setMinimumWidth(110)
        self.btn_stop.setObjectName("secondaryBtn")
        self.btn_stop.setIcon(Icons.get(Icons.STOP, color="#ef4444"))
        self.btn_stop.setIconSize(QSize(16, 16))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_analysis)
        row2.addWidget(self.btn_stop)

        self.btn_export = QPushButton("  JSON Export")
        self.btn_export.setMinimumWidth(130)
        self.btn_export.setObjectName("secondaryBtn")
        self.btn_export.setIcon(Icons.get(Icons.EXPORT))
        self.btn_export.setIconSize(QSize(16, 16))
        self.btn_export.clicked.connect(self._export_json)
        row2.addWidget(self.btn_export)

        vbox.addLayout(row2)
        return frame

    def _make_roster_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        title = QLabel("Karakter kadrosu")
        title.setObjectName("pageSubtitle")
        v.addWidget(title)
        hint = QLabel("Analiz isim, cinsiyet ve görünümü kaydeder. Script he/she veya saç rengi yerine bu isimleri kullanır.")
        hint.setObjectName("pageSubtitle")
        hint.setWordWrap(True)
        v.addWidget(hint)
        row = QHBoxLayout()
        self.roster_name_edit = QLineEdit()
        self.roster_name_edit.setPlaceholderText("İsim ekle (Jin-Woo)")
        self.roster_name_edit.returnPressed.connect(self._add_roster_name)
        row.addWidget(self.roster_name_edit, 1)
        btn_add = QPushButton("Ekle")
        btn_add.setObjectName("secondaryBtn")
        btn_add.clicked.connect(self._add_roster_name)
        row.addWidget(btn_add)
        v.addLayout(row)
        self.roster_list = QListWidget()
        self.roster_list.setMaximumHeight(140)
        self.roster_list.itemDoubleClicked.connect(self._edit_roster_name)
        v.addWidget(self.roster_list)
        btn_row = QHBoxLayout()
        btn_edit = QPushButton("  Düzenle")
        btn_edit.setObjectName("secondaryBtn")
        btn_edit.setIcon(Icons.get(Icons.EDIT))
        btn_edit.setIconSize(QSize(16, 16))
        btn_edit.clicked.connect(self._edit_roster_name)
        btn_row.addWidget(btn_edit)
        btn_del = QPushButton("Seçileni sil")
        btn_del.setObjectName("secondaryBtn")
        btn_del.clicked.connect(self._remove_roster_name)
        btn_row.addWidget(btn_del)
        v.addLayout(btn_row)
        return frame

    def _refresh_roster(self) -> None:
        if not hasattr(self, "roster_list"):
            return
        self.roster_list.clear()
        project = self.ctx.app_state.current_project
        if not project:
            return
        from core.character_bible import get_entries, prune_generic_entries
        prune_generic_entries(project)
        for ent in get_entries(project):
            canon = (ent.get("canonical") or "").strip()
            if not canon:
                continue
            bits = [canon]
            if ent.get("gender"):
                bits.append(str(ent["gender"]))
            if ent.get("appearance"):
                bits.append(str(ent["appearance"]))
            item = QListWidgetItem(" — ".join(bits))
            item.setData(Qt.ItemDataRole.UserRole, canon)
            self.roster_list.addItem(item)

    def _add_roster_name(self) -> None:
        name = (self.roster_name_edit.text() or "").strip()
        if not name:
            return
        from core.script_generator import _is_generic_character_label, _looks_like_appearance
        if _is_generic_character_label(name) or _looks_like_appearance(name):
            QMessageBox.warning(
                self, "Geçersiz isim",
                "Görsel tarif eklenemez. Gerçek isim yaz (Jin-Woo, Cha Hae-In).",
            )
            return
        project = self.ctx.app_state.current_project
        if not project:
            return
        from core.character_bible import upsert_names
        upsert_names(project, [name])
        self.roster_name_edit.clear()
        self._refresh_roster()
        _save_current_project(self, project)

    def _remove_roster_name(self) -> None:
        item = self.roster_list.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole) or item.text().split(" — ")[0].strip()
        project = self.ctx.app_state.current_project
        if not project:
            return
        from core.character_bible import get_entries, set_entries
        set_entries(project, [e for e in get_entries(project) if (e.get("canonical") or "") != name])
        self._refresh_roster()
        _save_current_project(self, project)

    def _edit_roster_name(self, item=None) -> None:
        if not isinstance(item, QListWidgetItem):
            item = self.roster_list.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole) or item.text().split(" — ")[0].strip()
        project = self.ctx.app_state.current_project
        if not project or not name:
            return
        from core.character_bible import get_entry, update_character
        from core.script_generator import _is_generic_character_label, _looks_like_appearance

        entry = get_entry(project, name)
        if not entry:
            self._refresh_roster()
            return
        dlg = EditCharacterDialog(entry, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        new_name = vals["canonical"]
        if not new_name:
            QMessageBox.warning(self, "Geçersiz isim", "İsim boş olamaz.")
            return
        if _is_generic_character_label(new_name) or _looks_like_appearance(new_name):
            QMessageBox.warning(
                self, "Geçersiz isim",
                "Görsel tarif eklenemez. Gerçek isim yaz (Jin-Woo, Cha Hae-In).",
            )
            return
        updated = update_character(
            project,
            name,
            canonical=new_name,
            aliases=vals["aliases"],
            gender=vals["gender"],
            appearance=vals["appearance"],
            notes=vals["notes"],
        )
        if updated is None:
            QMessageBox.warning(
                self, "Kayıt güncellenemedi",
                "İsim geçersiz veya kayıt bulunamadı.",
            )
            return
        self._refresh_roster()
        _save_current_project(self, project)

    def _make_progress_row(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        hbox = QHBoxLayout()
        self.lbl_progress = QLabel("Hazır")
        self.lbl_progress.setObjectName("pageSubtitle")
        hbox.addWidget(self.lbl_progress)
        self.lbl_percent = QLabel("0%")
        self.lbl_percent.setObjectName("pageSubtitle")
        hbox.addWidget(self.lbl_percent, 0, Qt.AlignmentFlag.AlignRight)
        v.addLayout(hbox)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        v.addWidget(self.progress_bar)
        return w

    def _make_log_row(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        frame.setMaximumHeight(140)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        lbl = QLabel("İşlem Logu")
        lbl.setObjectName("cardSubtitle")
        v.addWidget(lbl)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logView")
        v.addWidget(self.log_view)
        return frame

    # ── Helpers ────────────────────────────────────────────────────

    def _populate_model_combo(self) -> None:
        self.model_combo.clear()
        try:
            from core.model_catalog import get_models, format_model_label, model_tooltip, default_model_id
            models = get_models("vision_models")
            if not models:
                raise ValueError("boş katalog")
            for m in models:
                label = format_model_label(m)
                self.model_combo.addItem(label, m["id"])
                idx = self.model_combo.count() - 1
                tip = model_tooltip(m)
                if tip:
                    self.model_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
            # Varsayılan: fiyat/performans önerisi
            pref = default_model_id("vision_models")
            if pref:
                i = self.model_combo.findData(pref)
                if i >= 0:
                    self.model_combo.setCurrentIndex(i)
        except Exception as exc:
            logger.error("Model listesi yüklenemedi: %s", exc)
            self.model_combo.addItem(
                "Gemini 2.5 Flash  ·  Fiyat/Performans  [$]",
                "google/gemini-2.5-flash",
            )
            self.model_combo.addItem("GPT-4o Mini  ·  Bütçe  [$]", "openai/gpt-4o-mini")
            self.model_combo.addItem("Claude Sonnet 4  ·  Performans  [$$$]", "anthropic/claude-sonnet-4")

    def _log(self, message: str) -> None:
        self.log_view.append(message)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        self.ctx.app_state.status_message.emit(message)

    def _get_api_key(self) -> str:
        """Her zaman güncel API key döner (SettingsManager'dan)."""
        try:
            sm = self.ctx.settings_manager
            sm.reload()  # disk'ten taze oku (race-condition guard)
            return sm.get_api_key()
        except Exception:
            return self.ctx.app_state.get_setting("api", "openrouter_api_key", default="")

    # ── App State ──────────────────────────────────────────────────

    def _connect_app_state(self) -> None:
        self.ctx.app_state.project_changed.connect(self._on_project_changed)

    def _on_project_changed(self, project) -> None:
        self.chapter_combo.clear()
        self.image_list.clear()
        self.detail_panel.clear()
        if project:
            for ch in project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
        self._refresh_roster()

    def _on_chapter_changed(self, index: int) -> None:
        if index < 0:
            return
        state = self.ctx.app_state
        if not state.current_project:
            return
        chapter_id = self.chapter_combo.itemData(index)
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return
        state.current_chapter = chapter
        self._load_image_list(chapter)

    def _load_image_list(self, chapter) -> None:
        self.image_list.clear()
        total = len(chapter.images)
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(0)

        for i, img in enumerate(chapter.images):
            item = ImageStatusItem(i, img.filename)
            cache_key = str(i)
            if cache_key in chapter.analysis_data:
                cached = chapter.analysis_data[cache_key]
                item.set_status("error" if cached.get("error") else "cached")
            self.image_list.addItem(item)

        self._update_progress_label(0, total)

    def _update_progress_label(self, done: int, total: int) -> None:
        pct = int(done / total * 100) if total else 0
        self.lbl_progress.setText(f"{done}/{total} analiz edildi")
        self.lbl_percent.setText(f"{pct}%")
        self.progress_bar.setValue(done)

    def _on_image_selected(self, row: int) -> None:
        item = self.image_list.item(row)
        if not isinstance(item, ImageStatusItem):
            return
        state = self.ctx.app_state
        if not state.current_chapter:
            return
        cache_key = str(item.image_index)
        data = state.current_chapter.analysis_data.get(cache_key)
        if data:
            self.detail_panel.show_result(item.image_index, data)
        else:
            self.detail_panel.clear()

    # ── Analysis ───────────────────────────────────────────────────

    def _start_analysis(self) -> None:
        state = self.ctx.app_state
        if not state.current_project:
            QMessageBox.warning(self, "Uyarı", "Önce bir proje seçin.")
            return
        if self.chapter_combo.currentIndex() < 0:
            QMessageBox.warning(self, "Uyarı", "Önce bir bölüm seçin.")
            return

        api_key = self._get_api_key()
        if not api_key:
            QMessageBox.warning(
                self, "API Anahtarı Eksik",
                "OpenRouter API anahtarı girilmemiş.\n\n"
                "Çözüm:\n"
                "1. Ayarlar sayfasına gidin\n"
                "2. API Key alanına key'inizi yapıştırın\n"
                "3. 'Kaydet' butonuna basın\n"
                "4. Buraya geri dönüp tekrar deneyin"
            )
            return

        chapter_id = self.chapter_combo.currentData()
        chapter = state.current_project.get_chapter(chapter_id)
        if not chapter:
            return
        if not chapter.images:
            QMessageBox.warning(
                self, "Görsel Yok",
                "Bu bölümde görsel yok. Önce Görseller sayfasından görsel ekleyin.",
            )
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Bilgi", "Devam eden bir analiz var. Lütfen bitmesini bekleyin.")
            return

        model = self.model_combo.currentData() or "google/gemini-2.0-flash-exp:free"

        from ui.workers.analysis_worker import AnalysisWorker
        from ui.workers.thread_utils import start_worker
        # api_key parametresi artık opsiyonel; worker SettingsManager'dan okur
        self._worker = AnalysisWorker(
            chapter, model, api_key, project=state.current_project, parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.image_analyzed.connect(self._on_image_analyzed)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setRange(0, len(chapter.images))
        self._log(f"▶  Analiz başladı: {chapter.name} ({len(chapter.images)} görsel, model: {model})")

        start_worker(self, self._worker)

    def _stop_analysis(self) -> None:
        if self._worker and self._worker.isRunning():
            from ui.workers.thread_utils import abort_worker
            abort_worker(self, self._worker)
            self._log("⏹  Durdurma sinyali gönderildi…")

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self._update_progress_label(current, total)
        self._log(f"  {message}")

        # Listede durumu güncelle
        item = self.image_list.item(current - 1)
        if isinstance(item, ImageStatusItem):
            item.set_status("running")

    def _on_image_analyzed(self, index: int, data: dict) -> None:
        item = self.image_list.item(index)
        if isinstance(item, ImageStatusItem):
            item.set_status("error" if data.get("error") else "done")

        # Eğer bu görsel seçiliyse detayı göster
        if self.image_list.currentRow() == index:
            self.detail_panel.show_result(index, data)

        # Projeye kaydet
        state = self.ctx.app_state
        if state.current_project and state.current_chapter:
            _save_current_project(self, state.current_project)
            self._refresh_roster()

    def _on_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        state = self.ctx.app_state
        chapter = state.current_chapter
        total = len(chapter.images) if chapter else 0
        done = sum(
            1 for i in range(total)
            if str(i) in (chapter.analysis_data if chapter else {})
        )
        stopped = bool(getattr(self._worker, "_stop", False))
        if stopped:
            self._log(
                f"<span style='color:#e0af68;'><b>DURDURULDU:</b> "
                f"Analiz kesildi ({done}/{total} görsel işlendi).</span>"
            )
        else:
            self._log(
                f"<span style='color:#22c55e;'><b>BAŞARILI:</b> "
                f"Analiz tamamlandı ({done}/{total} görsel işlendi).</span>"
            )
        self._update_progress_label(done, total)
        if state.current_project:
            _save_current_project(self, state.current_project)

    def _on_error(self, message: str) -> None:
        self._log(f"<span style='color:#ef4444;'><b>HATA:</b> {message}</span>")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    # ── Reanalyze ──────────────────────────────────────────────────

    def _on_reanalyze(self) -> None:
        row = self.image_list.currentRow()
        item = self.image_list.item(row)
        if not isinstance(item, ImageStatusItem):
            return

        api_key = self._get_api_key()
        if not api_key:
            QMessageBox.warning(self, "API Anahtarı Eksik", "Ayarlar > API Ayarları bölümünden ekleyin.")
            return

        state = self.ctx.app_state
        if not state.current_chapter:
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Bilgi", "Devam eden bir analiz var. Lütfen bitmesini bekleyin.")
            return

        model = self.model_combo.currentData() or "google/gemini-2.0-flash-exp:free"
        image_index = item.image_index
        item.set_status("running")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._log(
            f"<span style='color:#6366f1;'><b>YENİDEN:</b> "
            f"Görsel {image_index + 1} arka planda tekrar analiz ediliyor...</span>"
        )

        from PyQt6.QtCore import QThread, pyqtSignal

        class _ReanalyzeWorker(QThread):
            done = pyqtSignal(int, dict)
            failed = pyqtSignal(str)

            def __init__(self, chapter, idx: int, model_name: str, key: str, project=None) -> None:
                super().__init__()
                self._chapter = chapter
                self._idx = idx
                self._model = model_name
                self._key = key
                self._project = project
                self._stop = False

            def stop(self) -> None:
                self._stop = True

            def run(self) -> None:
                try:
                    from core.openrouter_client import OpenRouterClient
                    from core.ai_analyzer import AIAnalyzer
                    client = OpenRouterClient.instance()
                    if self._key:
                        client.update_api_key(self._key)
                    analyzer = AIAnalyzer(client)
                    result = analyzer.reanalyze_image(
                        self._chapter, self._idx, self._model, project=self._project,
                    )
                    if getattr(self, "_stop", False):
                        return
                    self.done.emit(self._idx, result)
                except Exception as exc:
                    self.failed.emit(str(exc))

        from ui.workers.thread_utils import start_worker
        worker = _ReanalyzeWorker(
            state.current_chapter, image_index, model, api_key, project=state.current_project,
        )
        worker.done.connect(self._on_reanalyze_done)
        worker.failed.connect(self._on_reanalyze_failed)
        self._worker = start_worker(self, worker)

    def _on_reanalyze_done(self, image_index: int, result: dict) -> None:
        item = self.image_list.item(image_index)
        if isinstance(item, ImageStatusItem):
            item.set_status("error" if result.get("error") else "done")
        self.detail_panel.show_result(image_index, result)
        state = self.ctx.app_state
        if state.current_project:
            _save_current_project(self, state.current_project)
            self._refresh_roster()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._log(
            f"<span style='color:#22c55e;'><b>YENİDEN:</b> "
            f"Görsel {image_index + 1} tekrar analizi tamamlandı.</span>"
        )

    def _on_reanalyze_failed(self, message: str) -> None:
        row = self.image_list.currentRow()
        item = self.image_list.item(row)
        if isinstance(item, ImageStatusItem):
            item.set_status("error")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._log(f"<span style='color:#ef4444;'><b>HATA:</b> Tekrar analiz hatası: {message}</span>")

    # ── Export ─────────────────────────────────────────────────────

    def _export_json(self) -> None:
        state = self.ctx.app_state
        if not state.current_chapter:
            QMessageBox.warning(self, "Uyarı", "Analiz edilmiş bir bölüm seçin.")
            return
        data = state.current_chapter.analysis_data
        if not data:
            QMessageBox.information(self, "Bilgi", "Henüz analiz verisi yok.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "JSON Kaydet", f"{state.current_chapter.name}_analiz.json",
            "JSON (*.json)"
        )
        if path:
            try:
                from pathlib import Path
                Path(path).write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                self._log(f"JSON export: {path}")
            except Exception as exc:
                QMessageBox.critical(self, "Hata", f"Dışa aktarılamadı:\n{exc}")

    def showEvent(self, event) -> None:
        state = self.ctx.app_state
        if state.current_project:
            current_data = self.chapter_combo.currentData()
            self.chapter_combo.blockSignals(True)
            self.chapter_combo.clear()
            for ch in state.current_project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
            restored = False
            if current_data:
                idx = self.chapter_combo.findData(current_data)
                if idx >= 0:
                    self.chapter_combo.setCurrentIndex(idx)
                    restored = True
            elif state.current_chapter:
                idx = self.chapter_combo.findData(state.current_chapter.id)
                if idx >= 0:
                    self.chapter_combo.setCurrentIndex(idx)
                    restored = True
            self.chapter_combo.blockSignals(False)
            if restored or self.chapter_combo.currentIndex() >= 0:
                self._on_chapter_changed(self.chapter_combo.currentIndex())
        self._refresh_roster()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        if self._worker and self._worker.isRunning() and hasattr(self._worker, "stop"):
            self._worker.stop()
        super().hideEvent(event)
