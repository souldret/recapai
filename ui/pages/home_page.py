"""
RecapAI - Ana Sayfa (Dashboard + Tek Tıkla Pipeline).
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QProgressBar, QTextEdit, QSizePolicy,
    QComboBox, QDialog, QDialogButtonBox, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QSize, pyqtSlot

from core.context import AppContext
from ui.widgets.page_header import PageHeader
from ui.widgets.cards import StatCard, ActionCard
from ui.utils.icons import Icons, ICON_COLOR_ACTIVE, ICON_COLOR_SUCCESS
from ui.utils.icons import ICON_COLOR_WARNING, ICON_COLOR_ERROR

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# RecentProjectItem
# ─────────────────────────────────────────────────────────────────────────────

class RecentProjectItem(QFrame):
    """Son projeler listesinde tek bir satır."""

    def __init__(self, summary: dict, on_open, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._summary = summary
        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(14, 10, 14, 10)
        hbox.setSpacing(12)

        # Ikon
        icon_lbl = QLabel()
        pm = Icons.pixmap(Icons.PROJECTS, size=20, color=ICON_COLOR_ACTIVE)
        if not pm.isNull():
            icon_lbl.setPixmap(pm)
        else:
            icon_lbl.setText("[]")
        icon_lbl.setFixedWidth(24)
        hbox.addWidget(icon_lbl)

        # Bilgi
        vbox = QVBoxLayout()
        vbox.setSpacing(2)
        name_lbl = QLabel(summary.get("name", "—"))
        name_lbl.setObjectName("cardTitle")
        vbox.addWidget(name_lbl)

        updated = str(summary.get("updated_at") or "")[:10] or "—"
        meta = QLabel(
            f"{summary.get('chapter_count', 0)} bölüm  ·  "
            f"{summary.get('image_count', 0)} görsel  ·  "
            f"{updated}"
        )
        meta.setObjectName("mutedLabel")
        vbox.addWidget(meta)
        hbox.addLayout(vbox, 1)

        # Ac butonu
        btn_open = QPushButton("Aç")
        btn_open.setFixedWidth(60)
        btn_open.setObjectName("ghostButton")
        btn_open.clicked.connect(lambda: on_open(summary))
        hbox.addWidget(btn_open)


# ─────────────────────────────────────────────────────────────────────────────
# PipelineDialog
# ─────────────────────────────────────────────────────────────────────────────

class PipelineConfigDialog(QDialog):
    """Pipeline baslamadan once hizli konfigurasyon."""

    def __init__(self, project, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tek Tikla Pipeline")
        self.setMinimumWidth(420)
        self._project = project
        self._build_ui()

    def _build_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setSpacing(14)
        vbox.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Pipeline Ayarlari")
        title.setObjectName("headingLabel")
        vbox.addWidget(title)

        sub = QLabel("Tum adimlar otomatik calisacak: Analiz > Script > TTS > Render")
        sub.setObjectName("cardSubtitle")
        sub.setWordWrap(True)
        vbox.addWidget(sub)

        # Bolum secici
        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Bolum:"))
        self.chapter_combo = QComboBox()
        for ch in self._project.chapters:
            self.chapter_combo.addItem(ch.name, ch.id)
        self.chapter_combo.currentIndexChanged.connect(self._refresh_cost)
        hbox.addWidget(self.chapter_combo, 1)
        vbox.addLayout(hbox)

        self.force_check = QCheckBox("Tum asamalari bastan calistir")
        self.force_check.setToolTip("Kapalıysa tamamlanan analiz/script/TTS atlanır.")
        self.force_check.toggled.connect(self._refresh_cost)
        vbox.addWidget(self.force_check)

        self.ab_hook_check = QCheckBox("A/B kanca (2 cold open)")
        self.ab_hook_check.setToolTip("İkinci kısa kancayı ilk segmente not olarak ekler.")
        vbox.addWidget(self.ab_hook_check)

        self.cost_lbl = QLabel("")
        self.cost_lbl.setObjectName("mutedLabel")
        self.cost_lbl.setWordWrap(True)
        vbox.addWidget(self.cost_lbl)

        note = QLabel(
            "Pipeline kayitli render preset + GPU codec kullanir.\n"
            "Detayli ayarlar icin ilgili sayfalari kullanin."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        vbox.addWidget(note)
        self._refresh_cost()

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("Baslat")
            ok_btn.setObjectName("primaryButton")
            ok_btn.setIcon(Icons.get(Icons.PIPELINE, color="#ffffff"))
        vbox.addWidget(btn_box)

    def get_chapter(self):
        idx = self.chapter_combo.currentIndex()
        if idx >= 0:
            return self._project.chapters[idx]
        return None

    def force_rerun(self) -> bool:
        return bool(self.force_check.isChecked())

    def hook_variants(self) -> int:
        return 2 if self.ab_hook_check.isChecked() else 1

    def _refresh_cost(self) -> None:
        chapter = self.get_chapter()
        if not chapter:
            self.cost_lbl.setText("")
            return
        try:
            from core.pipeline import estimate_pipeline_cost, pending_stages
            from core.settings_manager import SettingsManager
            sm = SettingsManager.instance()
            vision = sm.get("defaults.vision_model", "google/gemini-2.5-flash")
            script = sm.get("defaults.script_model", "anthropic/claude-sonnet-4")
            force = self.force_rerun()
            stages = pending_stages(chapter, force=force)
            skip_a = "analysis" not in stages
            skip_s = "script" not in stages
            est = estimate_pipeline_cost(
                chapter, vision, script,
                skip_analysis=skip_a, skip_script=skip_s,
            )
            pending = ", ".join(stages) if stages else "render"
            self.cost_lbl.setText(
                f"Calisacak: {pending}\n"
                f"Tahmini maliyet: ${est['usd_low']:.3f} – ${est['usd_high']:.3f} USD"
            )
        except Exception as exc:
            self.cost_lbl.setText(f"Maliyet tahmini yok: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# HomePage
# ─────────────────────────────────────────────────────────────────────────────

class HomePage(QWidget):
    """Ana sayfa - dashboard, istatistikler, son projeler, pipeline."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._app_state = self.ctx.app_state
        self._pipeline_worker = None
        self._pipeline_output: Optional[str] = None
        self._live_workers: list = []

        self._card_projects: Optional[StatCard] = None
        self._card_chapters: Optional[StatCard] = None
        self._card_renders:  Optional[StatCard] = None
        self._card_duration: Optional[StatCard] = None
        self._stats_loaded = False

        self._build_ui()
        self._connect_signals()
        logger.debug("HomePage olusturuldu.")

    # ── UI Olusturma ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        btn_refresh = QPushButton("Yenile")
        btn_refresh.setObjectName("secondaryBtn")
        btn_refresh.setIcon(Icons.get(Icons.REFRESH))
        btn_refresh.setIconSize(QSize(16, 16))
        btn_refresh.setFixedWidth(90)
        btn_refresh.clicked.connect(self._refresh_all)

        self._header = PageHeader(
            "Ana Sayfa",
            "RecapAI ile manhwa recap videolari uretmeye basla.",
            actions=[btn_refresh],
        )
        root.addWidget(self._header)

        # Kaydirma alani
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        main_vbox = QVBoxLayout(content)
        main_vbox.setContentsMargins(28, 24, 28, 28)
        main_vbox.setSpacing(24)

        # Istatistik kartlari
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self._card_projects = StatCard(Icons.PROJECTS, "Toplam Proje",  "0")
        self._card_chapters = StatCard(Icons.SCRIPT,   "Toplam Bolum",  "0")
        self._card_renders  = StatCard(Icons.RENDER,   "Uretilen Video","0")
        self._card_duration = StatCard(Icons.TTS,      "Toplam Ses",    "0 dk")

        for card in [self._card_projects, self._card_chapters,
                     self._card_renders, self._card_duration]:
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            stats_layout.addWidget(card)
        main_vbox.addLayout(stats_layout)

        self._api_banner = QFrame()
        self._api_banner.setObjectName("card")
        banner_row = QHBoxLayout(self._api_banner)
        banner_row.setContentsMargins(16, 10, 16, 10)
        self._api_banner_lbl = QLabel(
            "OpenRouter API anahtari yok. Ayarlar'dan ekle — pipeline ve analiz icin gerekli."
        )
        self._api_banner_lbl.setWordWrap(True)
        banner_row.addWidget(self._api_banner_lbl, 1)
        btn_api = QPushButton("Ayarlara git")
        btn_api.setObjectName("secondaryBtn")
        btn_api.clicked.connect(lambda: self._navigate_named("settings"))
        banner_row.addWidget(btn_api)
        self._api_banner.setVisible(False)
        main_vbox.addWidget(self._api_banner)

        self._continue_card = ActionCard(
            Icons.PIPELINE, "Devam et",
            "Son projedeki sonraki eksik adim.",
        )
        self._continue_card.clicked.connect(self._continue_next_step)
        main_vbox.addWidget(self._continue_card)

        # Iki sutun: son projeler | hizli baslat
        col_layout = QHBoxLayout()
        col_layout.setSpacing(16)

        self._recent_container = self._make_recent_projects_panel()
        col_layout.addWidget(self._recent_container, 3)
        col_layout.addWidget(self._make_quick_start_panel(), 2)
        main_vbox.addLayout(col_layout)

        # Pipeline paneli
        self._pipeline_panel = self._make_pipeline_panel()
        main_vbox.addWidget(self._pipeline_panel)
        self._pipeline_panel.setVisible(False)

        main_vbox.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

        # Hosgeldin etiketi (header altinda guncellenecek)
        self._welcome_lbl = None

    def _make_recent_projects_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(20, 16, 20, 16)
        vbox.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("Son Projeler")
        title.setObjectName("headingLabel")
        title_row.addWidget(title)
        title_row.addStretch()

        btn_all = QPushButton("Tumunu Gor")
        btn_all.setObjectName("ghostButton")
        btn_all.clicked.connect(self._navigate_to_projects)
        title_row.addWidget(btn_all)
        vbox.addLayout(title_row)

        self._recent_list_widget = QWidget()
        self._recent_list_layout = QVBoxLayout(self._recent_list_widget)
        self._recent_list_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_list_layout.setSpacing(6)
        vbox.addWidget(self._recent_list_widget)

        self._no_projects_lbl = QLabel("Henuz proje yok. Yeni bir proje olusturun.")
        self._no_projects_lbl.setObjectName("mutedLabel")
        self._no_projects_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._no_projects_lbl)
        vbox.addStretch()
        return frame

    def _make_quick_start_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(20, 16, 20, 16)
        vbox.setSpacing(14)

        title = QLabel("Hizli Baslat")
        title.setObjectName("headingLabel")
        vbox.addWidget(title)

        # Aksiyon kartlari
        new_proj_card = ActionCard(
            Icons.ADD, "Yeni Proje",
            "Yeni bir recap projesi oluştur.",
        )
        new_proj_card.clicked.connect(self._create_new_project)
        vbox.addWidget(new_proj_card)

        img_card = ActionCard(
            Icons.IMAGES, "Görsel Ekle",
            "Mevcut projeye görsel ekle.",
        )
        img_card.clicked.connect(self._navigate_to_images)
        vbox.addWidget(img_card)

        render_card = ActionCard(
            Icons.RENDER, "Render",
            "Video oluşturma ekranına git.",
        )
        render_card.clicked.connect(self._navigate_to_render)
        vbox.addWidget(render_card)

        vbox.addStretch()

        # Ayiric
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("separator")
        vbox.addWidget(sep)

        # Pipeline butonu
        btn_pipeline = QPushButton("Tek Tikla Pipeline")
        btn_pipeline.setObjectName("successButton")
        btn_pipeline.setMinimumHeight(42)
        btn_pipeline.setIcon(Icons.get(Icons.PIPELINE, color="#ffffff"))
        btn_pipeline.setIconSize(QSize(18, 18))
        btn_pipeline.clicked.connect(self._launch_pipeline)
        vbox.addWidget(btn_pipeline)

        note = QLabel("Analiz > Script > TTS > Render")
        note.setObjectName("mutedLabel")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(note)
        return frame

    def _make_pipeline_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(10)

        # Baslik
        title_row = QHBoxLayout()
        self._pipeline_title = QLabel("Pipeline Calisiyor...")
        self._pipeline_title.setObjectName("headingLabel")
        title_row.addWidget(self._pipeline_title)
        title_row.addStretch()

        self._btn_cancel_pipeline = QPushButton("Iptal")
        self._btn_cancel_pipeline.setObjectName("dangerButton")
        self._btn_cancel_pipeline.setIcon(Icons.get(Icons.STOP, color="#ef4444"))
        self._btn_cancel_pipeline.setFixedWidth(80)
        self._btn_cancel_pipeline.clicked.connect(self._cancel_pipeline)
        title_row.addWidget(self._btn_cancel_pipeline)
        vbox.addLayout(title_row)

        # Asama gostergesi
        stage_row = QHBoxLayout()
        stage_row.setSpacing(6)
        self._stage_labels = []
        stages = ["Analiz", "Script", "TTS", "Render"]
        for s in stages:
            lbl = QLabel(s)
            lbl.setObjectName("stageLabel")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Varsayilan: bekliyor durumu
            lbl.setStyleSheet(
                "background-color: #2a2b30; color: #71717a; border-radius: 6px; "
                "padding: 5px 12px; font-size: 11px;"
            )
            stage_row.addWidget(lbl, 1)
            self._stage_labels.append(lbl)
            if s != stages[-1]:
                arr = QLabel("›")
                arr.setObjectName("mutedLabel")
                stage_row.addWidget(arr)
        vbox.addLayout(stage_row)

        self._pipeline_progress = QProgressBar()
        self._pipeline_progress.setRange(0, 100)
        self._pipeline_progress.setValue(0)
        vbox.addWidget(self._pipeline_progress)

        self._pipeline_stage_lbl = QLabel("Hazirlaniyor...")
        self._pipeline_stage_lbl.setObjectName("cardSubtitle")
        vbox.addWidget(self._pipeline_stage_lbl)

        self._pipeline_log = QTextEdit()
        self._pipeline_log.setReadOnly(True)
        self._pipeline_log.setMaximumHeight(120)
        self._pipeline_log.setObjectName("logView")
        vbox.addWidget(self._pipeline_log)

        # Sonuc satiri
        self._pipeline_result_row = QWidget()
        result_hbox = QHBoxLayout(self._pipeline_result_row)
        result_hbox.setContentsMargins(0, 0, 0, 0)
        self._pipeline_result_lbl = QLabel()
        result_hbox.addWidget(self._pipeline_result_lbl)

        self._btn_open_result = QPushButton("Klasörü Aç")
        self._btn_open_result.setObjectName("secondaryBtn")
        self._btn_open_result.setIcon(Icons.get(Icons.FOLDER))
        self._btn_open_result.clicked.connect(self._open_pipeline_result_folder)
        result_hbox.addWidget(self._btn_open_result)
        result_hbox.addStretch()

        btn_dismiss = QPushButton("Kapat")
        btn_dismiss.setObjectName("ghostButton")
        btn_dismiss.setIcon(Icons.get(Icons.CLOSE))
        btn_dismiss.clicked.connect(lambda: self._pipeline_panel.setVisible(False))
        result_hbox.addWidget(btn_dismiss)

        self._pipeline_result_row.setVisible(False)
        vbox.addWidget(self._pipeline_result_row)
        return frame

    # ── Sinyal baglantilari ───────────────────────────────────────

    def _connect_signals(self) -> None:
        self._app_state.project_changed.connect(self._on_project_changed)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._stats_loaded:
            self._refresh_all()
            self._stats_loaded = True

    # ── Yenileme ─────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._update_stats()
        self._update_recent_projects()
        self._refresh_continue_card()
        self._refresh_api_banner()
        mw = self._get_main_window()
        if mw and hasattr(mw, "refresh_sidebar_badges"):
            mw.refresh_sidebar_badges()

    def _on_project_changed(self, project) -> None:
        self._refresh_all()
        self._stats_loaded = True

    def _update_stats(self) -> None:
        try:
            import core.project_manager as pm_mod
            summaries = pm_mod.list_projects()

            total_projects = len(summaries)
            total_chapters = sum(s.get("chapter_count", 0) for s in summaries)
            total_duration_min = sum(float(s.get("duration_sec") or 0.0) for s in summaries) / 60.0

            total_renders = 0
            for s in summaries:
                out_dir = Path(s["path"]) / "output"
                if out_dir.exists():
                    total_renders += len(list(out_dir.glob("*.mp4")))

            if self._card_projects:
                self._card_projects.set_value(str(total_projects))
            if self._card_chapters:
                self._card_chapters.set_value(str(total_chapters))
            if self._card_renders:
                self._card_renders.set_value(str(total_renders))
            if self._card_duration:
                if total_duration_min >= 60:
                    dur_str = f"{total_duration_min / 60:.1f} sa"
                else:
                    dur_str = f"{int(total_duration_min)} dk"
                self._card_duration.set_value(dur_str)

        except Exception as exc:
            logger.warning("Istatistik guncellenemedi: %s", exc)

    def _update_recent_projects(self) -> None:
        while self._recent_list_layout.count():
            item = self._recent_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            import core.project_manager as pm_mod
            summaries = pm_mod.list_projects()

            if not summaries:
                self._no_projects_lbl.setVisible(True)
                return

            self._no_projects_lbl.setVisible(False)
            recent = sorted(summaries, key=lambda s: s.get("updated_at", ""), reverse=True)[:5]

            for s in recent:
                item = RecentProjectItem(s, self._open_project)
                self._recent_list_layout.addWidget(item)

        except Exception as exc:
            logger.warning("Son projeler yuklenemedi: %s", exc)
            self._no_projects_lbl.setVisible(True)

    # ── Navigasyon ────────────────────────────────────────────────

    def _navigate_named(self, name: str) -> None:
        mw = self._get_main_window()
        if mw:
            mw.navigate_to(name)

    def _continue_next_step(self) -> None:
        from core.pipeline import next_incomplete_step
        page, _label = next_incomplete_step(self._app_state.current_project)
        self._navigate_named(page)

    def _refresh_continue_card(self) -> None:
        from core.pipeline import next_incomplete_step
        page, label = next_incomplete_step(self._app_state.current_project)
        proj = self._app_state.current_project
        name = getattr(proj, "name", "") if proj else ""
        title = f"Devam et{f' — {name}' if name else ''}"
        if hasattr(self._continue_card, "set_title"):
            self._continue_card.set_title(title)
        if hasattr(self._continue_card, "set_subtitle"):
            self._continue_card.set_subtitle(label)
        else:
            self._continue_card.setToolTip(f"{title}: {label}")

    def _refresh_api_banner(self) -> None:
        try:
            from core.settings_manager import SettingsManager
            has_key = SettingsManager.instance().has_api_key()
        except Exception:
            has_key = bool(self._app_state.get_setting("api", "openrouter_api_key", default=""))
        self._api_banner.setVisible(not has_key)

    def _navigate_to_projects(self) -> None:
        mw = self._get_main_window()
        if mw:
            mw.navigate_to("projects")

    def _navigate_to_images(self) -> None:
        mw = self._get_main_window()
        if mw: mw.navigate_to("images")

    def _navigate_to_render(self) -> None:
        mw = self._get_main_window()
        if mw: mw.navigate_to("render")

    def _create_new_project(self) -> None:
        mw = self._get_main_window()
        if mw: mw.navigate_to("projects")

    def _open_project(self, summary: dict) -> None:
        try:
            import core.project_manager as pm_mod
            project = pm_mod.load_project(summary["path"])
        except Exception as exc:
            logger.warning("Proje acilamadi (%s): %s", summary.get("name"), exc)
            return
        self._app_state.current_project = project
        if project.chapters:
            self._app_state.current_chapter = project.chapters[0]
        mw = self._get_main_window()
        if mw: mw.navigate_to("projects")

    def _get_main_window(self):
        w = self.parent()
        while w:
            from ui.main_window import MainWindow
            if isinstance(w, MainWindow):
                return w
            w = w.parent() if hasattr(w, "parent") else None
        return None

    # ── Pipeline ──────────────────────────────────────────────────

    def _launch_pipeline(self) -> None:
        project = self._app_state.current_project
        if not project:
            QMessageBox.warning(self, "Proje Yok", "Önce bir proje seçin veya oluşturun.")
            return
        if not project.chapters:
            QMessageBox.warning(self, "Bölüm Yok", "Projede hiç bölüm bulunamadı.")
            return
        if self._pipeline_worker and self._pipeline_worker.isRunning():
            QMessageBox.information(self, "Pipeline", "Zaten bir pipeline çalışıyor.")
            return

        dlg = PipelineConfigDialog(project, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chapter = dlg.get_chapter()
        if not chapter:
            return
        if not chapter.images:
            QMessageBox.warning(
                self, "Görsel Yok",
                "Seçilen bölümde görsel yok. Önce Görseller sayfasından ekleyin.",
            )
            return

        app = self._app_state
        api_key      = app.get_setting("api", "openrouter_api_key", default="")
        if not api_key:
            QMessageBox.warning(
                self, "API Anahtarı Eksik",
                "Önce Ayarlar sayfasından OpenRouter API anahtarını girin.",
            )
            return
        vision_model = app.get_setting("defaults", "vision_model",  default="google/gemini-2.5-flash")
        script_model = app.get_setting("defaults", "script_model",  default="anthropic/claude-sonnet-4")
        tts_engine   = app.get_setting("defaults", "tts_engine",    default="edge-tts")
        tts_voice    = app.get_setting("defaults", "tts_voice",     default="tr-TR-AhmetNeural")

        try:
            import core.project_manager as pm_mod
            from core.pipeline import output_filename
            proj_dir = pm_mod.get_project_dir(project)
            if not proj_dir:
                raise ValueError("Proje dizini bulunamadı")
            audio_dir = str(proj_dir / "audio" / chapter.id)
            out_dir = proj_dir / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            render_output = str(out_dir / output_filename(project, chapter))
        except Exception as exc:
            logger.error("Pipeline cikti yolu hatasi: %s", exc)
            render_output = f"./output/{chapter.name}_pipeline.mp4"
            audio_dir = "./audio"

        from core.pipeline import pending_stages, resolve_render_settings
        render_settings = resolve_render_settings(app)
        stages = pending_stages(chapter, force=dlg.force_rerun())
        skip_analysis = "analysis" not in stages
        skip_script = "script" not in stages
        skip_tts = "tts" not in stages

        self._pipeline_panel.setVisible(True)
        self._pipeline_title.setText(f"Pipeline: {chapter.name}")
        self._pipeline_progress.setValue(0)
        self._pipeline_log.clear()
        self._pipeline_result_row.setVisible(False)
        self._btn_cancel_pipeline.setEnabled(True)
        self._reset_stage_labels()

        from ui.workers.render_worker import PipelineWorker
        from ui.workers.thread_utils import start_worker
        self._pipeline_worker = PipelineWorker(
            project=project, chapter=chapter,
            render_settings=render_settings, render_output=render_output,
            api_key=api_key, vision_model=vision_model, script_model=script_model,
            script_style="fresh", script_length="medium",
            script_language=app.get_setting("app", "language", default="tr") or "tr",
            tts_engine=tts_engine, tts_voice=tts_voice, audio_dir=audio_dir,
            parent=self,
            skip_analysis=skip_analysis,
            skip_script=skip_script,
            skip_tts=skip_tts,
            hook_variants=dlg.hook_variants(),
            mix_engines=bool(app.get_setting("tts", "mix_engines", default=False)),
        )
        self._pipeline_worker.stage_started.connect(self._on_pipeline_stage_started)
        self._pipeline_worker.stage_progress.connect(self._on_pipeline_stage_progress)
        self._pipeline_worker.stage_finished.connect(self._on_pipeline_stage_finished)
        self._pipeline_worker.overall_progress.connect(self._pipeline_progress.setValue)
        self._pipeline_worker.log.connect(self._pipeline_log_append)
        self._pipeline_worker.finished.connect(self._on_pipeline_finished)
        self._pipeline_worker.error.connect(self._on_pipeline_error)
        start_worker(self, self._pipeline_worker)
        self._pipeline_log_append("Pipeline baslatildi...")

    def _persist_pipeline_project(self) -> None:
        project = self._app_state.current_project
        if not project:
            return
        try:
            import core.project_manager as pm_mod
            pm_mod.save_project(project)
        except Exception as exc:
            logger.error("Pipeline proje kaydi basarisiz: %s", exc)
            QMessageBox.warning(self, "Kayıt hatası", f"Pipeline sonrası proje kaydedilemedi:\n{exc}")

    def _cancel_pipeline(self) -> None:
        if self._pipeline_worker and self._pipeline_worker.isRunning():
            from ui.workers.thread_utils import abort_worker
            abort_worker(self, self._pipeline_worker)
            self._btn_cancel_pipeline.setEnabled(False)
            self._pipeline_stage_lbl.setText("Iptal ediliyor...")
            self._pipeline_log_append("Iptal sinyali gonderildi...")

    @pyqtSlot(str)
    def _on_pipeline_stage_started(self, stage: str) -> None:
        stage_map = {"Analiz": 0, "Script": 1, "Seslendirme": 2, "Render": 3}
        idx = stage_map.get(stage, -1)
        self._pipeline_stage_lbl.setText(f"{stage} asamasi...")
        _done  = "background-color: #166534; color: #22c55e; border-radius: 6px; padding: 5px 12px; font-size: 11px;"
        _active = "background-color: #312e81; color: #818cf8; border-radius: 6px; padding: 5px 12px; font-size: 11px; font-weight: 600;"
        _wait  = "background-color: #2a2b30; color: #71717a; border-radius: 6px; padding: 5px 12px; font-size: 11px;"
        for i, lbl in enumerate(self._stage_labels):
            lbl.setStyleSheet(_done if i < idx else (_active if i == idx else _wait))

    @pyqtSlot(int, str)
    def _on_pipeline_stage_progress(self, percent: int, msg: str) -> None:
        if msg:
            self._pipeline_stage_lbl.setText(f"{msg}  %{percent}")

    @pyqtSlot(str)
    def _on_pipeline_stage_finished(self, stage: str) -> None:
        stage_map = {"Analiz": 0, "Script": 1, "Seslendirme": 2, "Render": 3}
        idx = stage_map.get(stage, -1)
        if 0 <= idx < len(self._stage_labels):
            self._stage_labels[idx].setStyleSheet(
                "background-color: #166534; color: #22c55e; border-radius: 6px; "
                "padding: 5px 12px; font-size: 11px;"
            )

    @pyqtSlot(str)
    def _on_pipeline_finished(self, output_path: str) -> None:
        self._pipeline_output = output_path
        self._pipeline_progress.setValue(100)
        self._pipeline_title.setText("Pipeline Tamamlandi!")
        self._pipeline_title.setStyleSheet("color: #22c55e; font-weight: 600;")
        self._btn_cancel_pipeline.setEnabled(False)
        self._pipeline_result_lbl.setText(f"Video olusturuldu: {Path(output_path).name}")
        self._pipeline_result_lbl.setStyleSheet("color: #22c55e; font-weight: 600;")
        self._pipeline_result_row.setVisible(True)
        self._pipeline_log_append(f"Tamamlandi: {output_path}")
        self._persist_pipeline_project()
        self._update_stats()

    @pyqtSlot(str)
    def _on_pipeline_error(self, message: str) -> None:
        self._pipeline_title.setText("Pipeline Hatasi")
        self._pipeline_title.setStyleSheet("color: #ef4444; font-weight: 600;")
        self._btn_cancel_pipeline.setEnabled(False)
        self._pipeline_result_lbl.setText(f"HATA: {message}")
        self._pipeline_result_lbl.setStyleSheet("color: #ef4444; font-weight: 600;")
        self._pipeline_result_row.setVisible(True)
        self._pipeline_log_append(f"HATA: {message}")
        self._persist_pipeline_project()

    def _pipeline_log_append(self, msg: str) -> None:
        self._pipeline_log.append(msg)
        self._pipeline_log.verticalScrollBar().setValue(
            self._pipeline_log.verticalScrollBar().maximum()
        )

    def _reset_stage_labels(self) -> None:
        for lbl in self._stage_labels:
            lbl.setStyleSheet(
                "background-color: #2a2b30; color: #71717a; border-radius: 6px; "
                "padding: 5px 12px; font-size: 11px;"
            )

    def _open_pipeline_result_folder(self) -> None:
        if self._pipeline_output:
            folder = str(Path(self._pipeline_output).parent)
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])