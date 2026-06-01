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
    QMessageBox, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QColor

from core.context import AppContext
from ui.utils.icons import Icons, ICON_COLOR_ACTIVE, ICON_COLOR_SUCCESS, ICON_COLOR_WARNING, ICON_COLOR_ERROR, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)

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
            for icon_name, key, label in [
                (Icons.GLOBE, "setting", "Mekan"),
                (Icons.MOOD, "mood", "Atmosfer"),
                (Icons.FLASH, "important", "Önemli"),
            ]:
                val = data.get(key, "—")
                if isinstance(val, bool):
                    val = "Evet" if val else "Hayır"
                self.cards_row.addWidget(self._mini_card(icon_name, label, str(val)[:30]))

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


# ── Ana Sayfa ──────────────────────────────────────────────────────────────────

class AnalysisPage(QWidget):
    """AI görsel analiz sayfası (v2)."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
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
        from pathlib import Path
        try:
            data = json.loads(Path("config/models.json").read_text(encoding="utf-8"))
            for m in data.get("vision_models", []):
                label = m["name"]
                if m.get("recommended"):
                    label += " (Tavsiye Edilen)"
                cost = m.get("cost", "")
                if cost:
                    label += f"  [{cost}]"
                self.model_combo.addItem(label, m["id"])
        except Exception as exc:
            logger.error("Model listesi yüklenemedi: %s", exc)
            self.model_combo.addItem("Gemini 2.0 Flash", "google/gemini-2.0-flash-exp:free")

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

        model = self.model_combo.currentData() or "google/gemini-2.0-flash-exp:free"

        from ui.workers.analysis_worker import AnalysisWorker
        # api_key parametresi artık opsiyonel; worker SettingsManager'dan okur
        self._worker = AnalysisWorker(chapter, model, api_key)
        self._worker.progress.connect(self._on_progress)
        self._worker.image_analyzed.connect(self._on_image_analyzed)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setRange(0, len(chapter.images))
        self._log(f"▶  Analiz başladı: {chapter.name} ({len(chapter.images)} görsel, model: {model})")

        self._worker.start()

    def _stop_analysis(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
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
            from core.project_manager import save_project
            save_project(state.current_project)

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
        self._log(f"<span style='color:#22c55e;'><b>BAŞARILI:</b> Analiz tamamlandı ({done}/{total} görsel işlendi).</span>")
        self._update_progress_label(done, total)

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

        model = self.model_combo.currentData() or "google/gemini-2.0-flash-exp:free"
        try:
            from core.openrouter_client import OpenRouterClient
            from core.ai_analyzer import AIAnalyzer
            client = OpenRouterClient.instance()
            if api_key:
                client.update_api_key(api_key)
            analyzer = AIAnalyzer(client)
            result = analyzer.reanalyze_image(state.current_chapter, item.image_index, model)
            item.set_status("error" if result.get("error") else "done")
            self.detail_panel.show_result(item.image_index, result)
            from core.project_manager import save_project
            save_project(state.current_project)
            self._log(f"<span style='color:#6366f1;'><b>YENİDEN:</b> Görsel {item.image_index + 1} tekrar analiz ediliyor...</span>")
        except Exception as exc:
            self._log(f"<span style='color:#ef4444;'><b>HATA:</b> Tekrar analiz hatası: {exc}</span>")

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
            self.chapter_combo.clear()
            for ch in state.current_project.chapters:
                self.chapter_combo.addItem(Icons.get(Icons.BOOK), ch.name, ch.id)
            if current_data:
                idx = self.chapter_combo.findData(current_data)
                if idx >= 0:
                    self.chapter_combo.setCurrentIndex(idx)
        super().showEvent(event)
