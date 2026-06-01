"""
RecapAI - Projeler Sayfası (v2).
"""

import logging
import re
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QLineEdit, QSplitter,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QAbstractItemView,
    QSizePolicy, QButtonGroup, QRadioButton
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont

from core.models import (
    Project, Chapter,
    CHAPTER_STATUS_RAW, CHAPTER_STATUS_DETECTED, CHAPTER_STATUS_COMPLETED,
)
from core.context import AppContext
from ui.utils.icons import Icons, ICON_COLOR_ACTIVE

logger = logging.getLogger(__name__)


# ── Yeni Proje Dialog ──────────────────────────────────────────────────────────

class NewProjectDialog(QDialog):
    """Proje adı ve seri türü seçimi modal dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yeni Proje Oluştur")
        self.setFixedSize(440, 280)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Proje Adı (en az 3 karakter):"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Örn: Solo Leveling Recap")
        layout.addWidget(self.name_input)

        # ── Seri Türü ──────────────────────────────────────────────
        type_lbl = QLabel("Seri Türü:")
        type_lbl.setObjectName("headingLabel")
        layout.addWidget(type_lbl)

        self._type_group = QButtonGroup(self)

        manga_frame = QFrame()
        manga_frame.setObjectName("statCard")
        manga_layout = QVBoxLayout(manga_frame)
        manga_layout.setContentsMargins(12, 10, 12, 10)
        manga_layout.setSpacing(4)
        self.radio_manga = QRadioButton("Manga")
        self.radio_manga.setChecked(True)
        font = QFont()
        font.setBold(True)
        self.radio_manga.setFont(font)
        manga_layout.addWidget(self.radio_manga)
        manga_desc = QLabel("Sayfa bazlı görsel (tek görsel = bir sayfa). Panel tespiti ve kırpma araçları aktif olur.")
        manga_desc.setObjectName("pageSubtitle")
        manga_desc.setWordWrap(True)
        manga_layout.addWidget(manga_desc)

        webtoon_frame = QFrame()
        webtoon_frame.setObjectName("statCard")
        webtoon_layout = QVBoxLayout(webtoon_frame)
        webtoon_layout.setContentsMargins(12, 10, 12, 10)
        webtoon_layout.setSpacing(4)
        self.radio_webtoon = QRadioButton("Webtoon")
        self.radio_webtoon.setFont(font)
        webtoon_layout.addWidget(self.radio_webtoon)
        webtoon_desc = QLabel("Dikey şerit format. Görseller birleştirilir (stitch) ardından panel tespiti + manuel düzenleme yapılır.")
        webtoon_desc.setObjectName("pageSubtitle")
        webtoon_desc.setWordWrap(True)
        webtoon_layout.addWidget(webtoon_desc)

        self._type_group.addButton(self.radio_manga, 0)
        self._type_group.addButton(self.radio_webtoon, 1)

        type_row = QHBoxLayout()
        type_row.setSpacing(10)
        type_row.addWidget(manga_frame)
        type_row.addWidget(webtoon_frame)
        layout.addLayout(type_row)

        self.error_lbl = QLabel("")
        self.error_lbl.setObjectName("errorLabel")
        layout.addWidget(self.error_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        name = self.name_input.text().strip()
        if len(name) < 3:
            self.error_lbl.setText("<b>Hata:</b> En az 3 karakter giriniz.")
            return
        if re.search(r"[<>:/\\|?*\"]", name):
            self.error_lbl.setText("<b>Hata:</b> Özel karakterler kullanılamaz.")
            return
        self.accept()

    def get_name(self) -> str:
        return self.name_input.text().strip()

    def get_series_type(self) -> str:
        return "webtoon" if self.radio_webtoon.isChecked() else "manga"


# ── Sol Panel: Proje Listesi ───────────────────────────────────────────────────

class ProjectListPanel(QWidget):
    """Sol panel: proje listesi + arama + yeni/sil butonları."""

    project_selected = pyqtSignal(str)   # proje klasör yolu
    project_deleted = pyqtSignal()        # proje silindi — sağ paneli temizle

    def __init__(self, ctx: "AppContext", parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)

        btn_new = QPushButton("  Yeni Proje Oluştur")
        btn_new.setObjectName("newProjectBtn")
        btn_new.setFixedHeight(44)
        btn_new.setIcon(Icons.get(Icons.ADD, color="#ffffff"))
        btn_new.setIconSize(QSize(18, 18))
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        btn_new.setFont(font)
        btn_new.clicked.connect(self._on_new_project)
        layout.addWidget(btn_new)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Proje ara...")
        self.search_box.addAction(Icons.get(Icons.SEARCH, color="#a1a1aa"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_box.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_box)

        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setSpacing(2)
        self.project_list.setUniformItemSizes(False)
        self.project_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.project_list, 1)

        self.btn_delete = QPushButton("  Projeyi Sil")
        self.btn_delete.setObjectName("secondaryBtn")
        self.btn_delete.setFixedHeight(36)
        self.btn_delete.setIcon(Icons.get(Icons.DELETE, color="#ef4444"))
        self.btn_delete.setIconSize(QSize(16, 16))
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._on_delete_project)
        layout.addWidget(self.btn_delete)

        self._all_items: list[dict] = []

    def load_projects(self) -> None:
        """Proje listesini diskten yeniler."""
        from core.project_manager import list_projects
        self._all_items = list_projects()
        self._render_list(self._all_items)

    def _render_list(self, items: list[dict]) -> None:
        self.project_list.clear()
        if not items:
            placeholder = QListWidgetItem("Henüz proje yok.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setForeground(Qt.GlobalColor.gray)
            self.project_list.addItem(placeholder)
            return
        for info in items:
            label = (
                f"{info['name']}\n"
                f"  Tarih: {info['created_at'][:10]}   "
                f"  Bölüm: {info['chapter_count']}"
            )
            item = QListWidgetItem()
            item.setIcon(Icons.get(Icons.PROJECTS, color=ICON_COLOR_ACTIVE))
            item.setText(label)
            item.setData(Qt.ItemDataRole.UserRole, info)
            item.setSizeHint(QSize(0, 60))
            self.project_list.addItem(item)

    def _filter_list(self, text: str) -> None:
        filtered = [
            p for p in self._all_items
            if text.lower() in p["name"].lower()
        ]
        self._render_list(filtered)

    def _on_selection_changed(self, row: int) -> None:
        item = self.project_list.item(row)
        self.btn_delete.setEnabled(item is not None and bool(item.data(Qt.ItemDataRole.UserRole)))
        if item:
            info = item.data(Qt.ItemDataRole.UserRole)
            if info:
                self.project_selected.emit(info["path"])

    def _on_new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.get_name()
        series_type = dialog.get_series_type()
        try:
            from core.project_manager import create_project
            project = create_project(name, series_type=series_type)
            self.ctx.app_state.current_project = project
            self.load_projects()
            type_label = "Manga" if series_type == "manga" else "Webtoon"
            self.ctx.app_state.status_message.emit(f"Proje oluşturuldu: {name} [{type_label}]")
        except (ValueError, FileExistsError) as exc:
            QMessageBox.warning(self, "Hata", str(exc))

    def _on_delete_project(self) -> None:
        item = self.project_list.currentItem()
        if not item:
            return
        info = item.data(Qt.ItemDataRole.UserRole)
        if not info:
            return
        reply = QMessageBox.question(
            self, "Projeyi Sil",
            f"'{info['name']}' projesini silmek istediğinize emin misiniz?\n"
            "Bu işlem geri alınamaz.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from core.project_manager import delete_project
            delete_project(info["id"])
            self.load_projects()
            self.project_deleted.emit()
            self.ctx.app_state.status_message.emit(f"Proje silindi: {info['name']}")
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Proje silinemedi:\n{exc}")


# ── Sağ Panel: Proje Detayı ────────────────────────────────────────────────────

class ProjectDetailPanel(QWidget):
    """Sağ panel: seçili projenin detayları ve bölüm yönetimi."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._project: Optional[Project] = None
        self._build_ui()
        self._show_empty()

    def _build_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 0, 0, 0)
        self._layout.setSpacing(16)

        # Başlık
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("pageTitle")
        self._layout.addWidget(self.title_lbl)

        # Bilgi kartları
        self.info_row = QHBoxLayout()
        self.info_row.setSpacing(12)
        self._layout.addLayout(self.info_row)

        # Bölümler başlık
        hbox = QHBoxLayout()
        lbl = QLabel("Bölümler")
        lbl.setObjectName("headingLabel")
        hbox.addWidget(lbl)
        hbox.addStretch()
        self.btn_bulk_import = QPushButton("  Klasörden Aktar")
        self.btn_bulk_import.setObjectName("secondaryBtn")
        self.btn_bulk_import.setFixedWidth(160)
        self.btn_bulk_import.setIcon(Icons.get(Icons.FOLDER))
        self.btn_bulk_import.setIconSize(QSize(14, 14))
        self.btn_bulk_import.setToolTip("Alt klasörler = bölümler olarak otomatik içe aktar")
        self.btn_bulk_import.clicked.connect(self._on_bulk_import)
        hbox.addWidget(self.btn_bulk_import)
        self.btn_add_chapter = QPushButton("  Bölüm Ekle")
        self.btn_add_chapter.setFixedWidth(130)
        self.btn_add_chapter.setIcon(Icons.get(Icons.ADD, color="#ffffff"))
        self.btn_add_chapter.setIconSize(QSize(14, 14))
        self.btn_add_chapter.clicked.connect(self._on_add_chapter)
        hbox.addWidget(self.btn_add_chapter)
        self._layout.addLayout(hbox)

        # Bölüm listesi
        self.chapter_list = QListWidget()
        self.chapter_list.setObjectName("chapterList")
        self.chapter_list.setSpacing(2)
        self._layout.addWidget(self.chapter_list, 1)

        # Placeholder
        self._empty_lbl = QLabel("← Soldan bir proje seçin veya yeni proje oluşturun.")
        self._empty_lbl.setObjectName("placeholderText")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _show_empty(self) -> None:
        self.title_lbl.hide()
        self.chapter_list.hide()
        self.btn_add_chapter.hide()
        self.btn_bulk_import.hide()
        self._empty_lbl.setParent(self)
        self._layout.addWidget(self._empty_lbl, 1, Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.show()

    def load_project(self, path: str) -> None:
        """Projeyi yükler ve panel içeriğini günceller."""
        from core.project_manager import load_project
        try:
            self._project = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Proje yüklenemedi:\n{exc}")
            return

        self._empty_lbl.hide()
        self.title_lbl.setText(self._project.name)
        self.title_lbl.show()

        # Bilgi kartlarını temizle ve yeniden çiz
        while self.info_row.count():
            item = self.info_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        type_label = "Manga" if self._project.series_type == "manga" else "Webtoon"
        for icon, label, value in [
            ("", "Seri Türü", type_label),
            ("", "Oluşturulma", self._project.created_at[:10]),
            ("", "Bölüm Sayısı", str(self._project.chapter_count)),
            ("", "Toplam Görsel", str(self._project.total_images)),
        ]:
            card = self._make_info_card(icon, label, value)
            self.info_row.addWidget(card)
        self.info_row.addStretch()

        self.btn_add_chapter.show()
        self.btn_bulk_import.show()
        self._render_chapters()
        self.chapter_list.show()

        self.ctx.app_state.current_project = self._project

    def clear(self) -> None:
        self._project = None
        self._show_empty()

    def _render_chapters(self) -> None:
        if not self._project:
            return
        self.chapter_list.clear()
        if not self._project.chapters:
            item = QListWidgetItem("Henüz bölüm yok. '+ Bölüm Ekle' ile başlayın.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(Qt.GlobalColor.gray)
            self.chapter_list.addItem(item)
            return

        # Bölüm ilerleme özeti
        total = len(self._project.chapters)
        completed = sum(1 for c in self._project.chapters
                        if c.status == CHAPTER_STATUS_COMPLETED)
        detected  = sum(1 for c in self._project.chapters
                        if c.status == CHAPTER_STATUS_DETECTED)

        if total > 0:
            if not hasattr(self, "_progress_bar"):
                from PyQt6.QtWidgets import QProgressBar as _PB
                self._progress_bar = _PB()
                self._progress_bar.setFixedHeight(6)
                self._progress_bar.setTextVisible(False)
                # Listeye üstten ekle
                insert_idx = self._layout.indexOf(self.chapter_list)
                self._layout.insertWidget(insert_idx, self._progress_bar)
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(completed)
            self._progress_bar.setToolTip(
                f"Tamamlanan: {completed}/{total}  |  "
                f"Panel tespiti: {detected}  |  Ham: {total - completed - detected}"
            )
            self._progress_bar.show()

        _STATUS_COLOR = {
            CHAPTER_STATUS_RAW:       "#71717a",
            CHAPTER_STATUS_DETECTED:  "#e0af68",
            CHAPTER_STATUS_COMPLETED: "#9ece6a",
        }
        _STATUS_LABEL = {
            CHAPTER_STATUS_RAW:       "Ham",
            CHAPTER_STATUS_DETECTED:  "Tespit Edildi",
            CHAPTER_STATUS_COMPLETED: "Tamamlandı",
        }

        for ch in self._project.chapters:
            row = QWidget()
            hbox = QHBoxLayout(row)
            hbox.setContentsMargins(12, 6, 12, 6)
            hbox.setSpacing(8)

            # Folder Icon
            icon_lbl = QLabel()
            icon_lbl.setPixmap(Icons.pixmap(Icons.FOLDER, size=16, color=ICON_COLOR_ACTIVE))
            hbox.addWidget(icon_lbl)

            # İsim + durum
            info_col = QVBoxLayout()
            info_col.setSpacing(1)
            lbl = QLabel(f"{ch.name}   —   {ch.image_count} görsel  |  {ch.total_panels} panel")
            info_col.addWidget(lbl)
            status_col = _STATUS_COLOR.get(ch.status, "#71717a")
            status_txt = _STATUS_LABEL.get(ch.status, ch.status)
            status_lbl = QLabel(status_txt)
            status_lbl.setStyleSheet(f"color: {status_col}; font-size: 9pt;")
            info_col.addWidget(status_lbl)
            hbox.addLayout(info_col, 1)

            # Durum geçiş butonu
            btn_status = QPushButton()
            btn_status.setFixedSize(28, 28)
            btn_status.setObjectName("secondaryBtn")
            btn_status.setToolTip("Durumu değiştir")
            if ch.status == CHAPTER_STATUS_COMPLETED:
                btn_status.setText("✓")
                btn_status.setStyleSheet("color: #9ece6a;")
            elif ch.status == CHAPTER_STATUS_DETECTED:
                btn_status.setText("◑")
                btn_status.setStyleSheet("color: #e0af68;")
            else:
                btn_status.setText("○")
            btn_status.clicked.connect(lambda _, cid=ch.id: self._cycle_chapter_status(cid))
            hbox.addWidget(btn_status)

            # Kopyala butonu
            btn_copy = QPushButton()
            btn_copy.setFixedSize(28, 28)
            btn_copy.setObjectName("secondaryBtn")
            btn_copy.setIcon(Icons.get(Icons.COPY if hasattr(Icons, "COPY") else Icons.ADD))
            btn_copy.setIconSize(QSize(12, 12))
            btn_copy.setToolTip("Bölümü Kopyala")
            btn_copy.clicked.connect(lambda _, cid=ch.id: self._copy_chapter(cid))
            hbox.addWidget(btn_copy)

            btn_del = QPushButton()
            btn_del.setFixedSize(28, 28)
            btn_del.setObjectName("secondaryBtn")
            btn_del.setIcon(Icons.get(Icons.DELETE, color="#ef4444"))
            btn_del.setIconSize(QSize(12, 12))
            btn_del.setToolTip("Bölümü Sil")
            btn_del.clicked.connect(lambda _, cid=ch.id: self._delete_chapter(cid))
            hbox.addWidget(btn_del)

            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 54))
            item.setData(Qt.ItemDataRole.UserRole, ch.id)
            self.chapter_list.addItem(item)
            self.chapter_list.setItemWidget(item, row)

    def _on_add_chapter(self) -> None:
        if not self._project:
            return
        folder = QFileDialog.getExistingDirectory(self, "Görsel Klasörü Seç")
        if not folder:
            return

        from core.image_processor import load_images_from_folder
        from core.models import ImageData
        from core.project_manager import add_chapter
        from pathlib import Path

        paths = load_images_from_folder(folder)
        if not paths:
            QMessageBox.warning(self, "Uyarı", "Seçilen klasörde desteklenen görsel bulunamadı.")
            return

        chapter_name, ok = _simple_input(
            self, "Bölüm Adı", "Bölüm adını girin:", f"Bölüm {len(self._project.chapters) + 1}"
        )
        if not ok:
            return

        images = [
            ImageData(path=p, filename=Path(p).name, order=i)
            for i, p in enumerate(paths)
        ]
        try:
            chapter = add_chapter(self._project, chapter_name, images)
            self.ctx.app_state.status_message.emit(
                f"Bölüm eklendi: {chapter_name} ({len(images)} görsel)"
            )
            self._render_chapters()
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Bölüm eklenemedi:\n{exc}")

    def _on_bulk_import(self) -> None:
        """Alt klasörler = bölümler olarak otomatik içe aktarır."""
        if not self._project:
            return
        root_folder = QFileDialog.getExistingDirectory(
            self, "Ana Klasörü Seç (Alt Klasörler = Bölümler)"
        )
        if not root_folder:
            return

        from pathlib import Path as _Path
        from core.image_processor import load_images_from_folder
        from core.models import ImageData
        from core.project_manager import add_chapter, save_project

        root = _Path(root_folder)
        subfolders = sorted([d for d in root.iterdir() if d.is_dir()])

        if not subfolders:
            # Üst klasörde direkt görseller varsa tek bölüm olarak ekle
            paths = load_images_from_folder(str(root))
            if paths:
                subfolders_as_images = [(root.name, paths)]
            else:
                QMessageBox.warning(self, "Uyarı", "Alt klasör veya görsel bulunamadı.")
                return
        else:
            subfolders_as_images = []
            for sub in subfolders:
                paths = load_images_from_folder(str(sub))
                if paths:
                    subfolders_as_images.append((sub.name, paths))

        if not subfolders_as_images:
            QMessageBox.warning(self, "Uyarı", "Hiçbir klasörde desteklenen görsel bulunamadı.")
            return

        imported = 0
        for chapter_name, paths in subfolders_as_images:
            images = [
                ImageData(path=p, filename=_Path(p).name, order=i)
                for i, p in enumerate(paths)
            ]
            try:
                add_chapter(self._project, chapter_name, images)
                imported += 1
            except Exception as exc:
                QMessageBox.warning(self, "Uyarı", f"'{chapter_name}' eklenemedi:\n{exc}")

        if imported:
            save_project(self._project)
            self._render_chapters()
            self.ctx.app_state.status_message.emit(
                f"{imported} bölüm otomatik olarak içe aktarıldı."
            )
            QMessageBox.information(
                self, "İçe Aktarma Tamamlandı",
                f"{imported} bölüm başarıyla eklendi."
            )

    def _cycle_chapter_status(self, chapter_id: str) -> None:
        """Bölüm durumunu döngüsel olarak değiştirir: Ham → Tespit Edildi → Tamamlandı → Ham."""
        if not self._project:
            return
        ch = self._project.get_chapter(chapter_id)
        if not ch:
            return
        _cycle = {
            CHAPTER_STATUS_RAW:       CHAPTER_STATUS_DETECTED,
            CHAPTER_STATUS_DETECTED:  CHAPTER_STATUS_COMPLETED,
            CHAPTER_STATUS_COMPLETED: CHAPTER_STATUS_RAW,
        }
        ch.status = _cycle.get(ch.status, CHAPTER_STATUS_RAW)
        from core.project_manager import save_project
        save_project(self._project)
        self._render_chapters()
        self.ctx.app_state.status_message.emit(f"'{ch.name}' durumu güncellendi: {ch.status}")

    def _copy_chapter(self, chapter_id: str) -> None:
        """Bölümün panel düzenini yeni bir bölüme kopyalar."""
        if not self._project:
            return
        src_ch = self._project.get_chapter(chapter_id)
        if not src_ch:
            return

        # Hedef bölüm seçimi
        if len(self._project.chapters) < 2:
            QMessageBox.information(self, "Bilgi", "Kopyalama için en az 2 bölüm gereklidir.")
            return

        from PyQt6.QtWidgets import QInputDialog
        chapter_names = [
            ch.name for ch in self._project.chapters if ch.id != chapter_id
        ]
        target_name, ok = QInputDialog.getItem(
            self, "Hedef Bölüm",
            f"'{src_ch.name}' panel düzenini kopyala →",
            chapter_names, 0, False
        )
        if not ok or not target_name:
            return

        target_ch = next(
            (ch for ch in self._project.chapters if ch.name == target_name), None
        )
        if not target_ch:
            return

        import copy
        target_ch.panel_data = copy.deepcopy(src_ch.panel_data)
        if src_ch.panel_data:
            target_ch.status = CHAPTER_STATUS_DETECTED

        from core.project_manager import save_project
        save_project(self._project)
        self._render_chapters()
        self.ctx.app_state.status_message.emit(
            f"'{src_ch.name}' panel düzeni → '{target_ch.name}' kopyalandı."
        )

    def _delete_chapter(self, chapter_id: str) -> None:
        if not self._project:
            return
        ch = self._project.get_chapter(chapter_id)
        if not ch:
            return
        reply = QMessageBox.question(
            self, "Bölümü Sil",
            f"'{ch.name}' bölümünü silmek istediğinize emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from core.project_manager import delete_chapter
        try:
            delete_chapter(self._project, chapter_id)
            self._render_chapters()
            self.ctx.app_state.status_message.emit(f"Bölüm silindi: {ch.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Hata", f"Bölüm silinemedi:\n{exc}")

    def _make_info_card(self, icon: str, label: str, value: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("statCard")
        frame.setMinimumSize(140, 75)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(12, 10, 12, 10)
        vbox.setSpacing(4)
        title_lbl = QLabel(label)
        title_lbl.setObjectName("cardSubtitle")
        vbox.addWidget(title_lbl)
        val_lbl = QLabel(value)
        val_lbl.setObjectName("cardValueSmall")
        val_lbl.setWordWrap(True)
        vbox.addWidget(val_lbl)
        return frame


# ── Yardımcı ───────────────────────────────────────────────────────────────────

def _simple_input(parent, title: str, label: str, default: str = "") -> tuple[str, bool]:
    """Basit tek satır metin girişi dialog."""
    from PyQt6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text.strip(), ok


# ── Ana Sayfa ──────────────────────────────────────────────────────────────────

class ProjectPage(QWidget):
    """Proje yönetim sayfası — sol liste + sağ detay."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()
        self.left_panel.load_projects()
        logger.debug("ProjectPage oluşturuldu.")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Başlık
        header = QVBoxLayout()
        title = QLabel("Projeler")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        subtitle = QLabel("Recap projelerinizi oluşturun ve yönetin.")
        subtitle.setObjectName("pageSubtitle")
        header.addWidget(subtitle)
        root.addLayout(header)

        # Split view
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        self.left_panel = ProjectListPanel(self.ctx, splitter)
        self.left_panel.project_selected.connect(self.load_project_detail)
        self.left_panel.project_deleted.connect(self.clear_detail)
        splitter.addWidget(self.left_panel)

        self.right_panel = ProjectDetailPanel(self.ctx, splitter)
        splitter.addWidget(self.right_panel)

        splitter.setSizes([400, 600])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

    def load_project_detail(self, path: str) -> None:
        self.right_panel.load_project(path)

    def clear_detail(self) -> None:
        self.right_panel.clear()

    def showEvent(self, event) -> None:
        """Sayfa gösterildiğinde listeyi yenile."""
        self.left_panel.load_projects()
        super().showEvent(event)