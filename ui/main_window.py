"""
RecapAI - Ana pencere.
"""

import logging
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QStackedWidget, QStatusBar,
    QApplication,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

from ui.widgets.sidebar import Sidebar
from ui.utils.icons import Icons, ICON_COLOR_SUCCESS, ICON_COLOR_ERROR
from core.context import AppContext

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Ana uygulama penceresi."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("RecapAI - Manhwa Recap Generator")
        self.resize(1400, 900)
        self.setMinimumSize(QSize(1200, 800))
        self._build_ui()
        self._connect_signals()
        self._select_page(0)
        self._center_on_screen()
        logger.debug("MainWindow olusturuldu.")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        # Sayfa stack
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        root.addWidget(self.stack)

        self._load_pages()
        self._build_status_bar()

    def _load_pages(self) -> None:
        """Sayfa listesini oluşturur (lazy loading ile)."""
        self._page_classes = [
            ("ui.pages.home_page", "HomePage"),
            ("ui.pages.project_page", "ProjectPage"),
            ("ui.pages.images_page", "ImagesPage"),
            ("ui.pages.analysis_page", "AnalysisPage"),
            ("ui.pages.script_page", "ScriptPage"),
            ("ui.pages.tts_page", "TtsPage"),
            ("ui.pages.render_page", "RenderPage"),
            ("ui.pages.settings_page", "SettingsPage"),
        ]
        self._page_cache: dict = {}
        self.pages = []

        # Placeholder widget'lar oluştur (hızlı başlangıç için)
        for i in range(len(self._page_classes)):
            placeholder = QWidget()
            placeholder.setObjectName("pageStack")
            self.stack.addWidget(placeholder)
            self.pages.append(placeholder)

        # İlk sayfa ve ayarlar sayfasını hemen yükle
        self._ensure_page(0)
        self._ensure_page(7)

    def _ensure_page(self, index: int) -> None:
        """Sayfa henüz yüklenmemişse lazy olarak yükler."""
        if index in self._page_cache:
            return
        if index >= len(self._page_classes):
            return

        module_path, class_name = self._page_classes[index]
        try:
            import importlib
            module = importlib.import_module(module_path)
            page_class = getattr(module, class_name)
            page = page_class(self.ctx)

            # Eski placeholder'ı gerçek sayfa ile değiştir
            old_widget = self.stack.widget(index)
            self.stack.insertWidget(index, page)
            self.stack.removeWidget(old_widget)
            old_widget.deleteLater()
            self.pages[index] = page
            self._page_cache[index] = page
            logger.debug("Sayfa yüklendi (lazy): %s", class_name)
        except Exception as exc:
            logger.error("Sayfa yüklenirken hata: %s - %s", class_name, exc, exc_info=True)

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setObjectName("statusBar")
        self.setStatusBar(bar)

        self.lbl_project = QLabel("Proje: —")
        self.lbl_project.setObjectName("statusLabel")
        bar.addWidget(self.lbl_project)

        self.lbl_message = QLabel("")
        self.lbl_message.setObjectName("statusLabel")
        bar.addWidget(self.lbl_message, 1)

        # API durum göstergesi (ikon + metin)
        self.lbl_api = QLabel("  API Bagli")
        self.lbl_api.setObjectName("statusApiConnected")
        self._update_api_icon(connected=True)
        bar.addPermanentWidget(self.lbl_api)

    def _update_api_icon(self, connected: bool) -> None:
        """API durum ikonunu guncelle."""
        pm = Icons.pixmap(
            Icons.SUCCESS if connected else Icons.ERROR,
            size=12,
            color=ICON_COLOR_SUCCESS if connected else ICON_COLOR_ERROR,
        )
        if not pm.isNull():
            self.lbl_api.setPixmap(pm)
        else:
            # Fallback: unicode nokta
            dot = "⬤" if connected else "○"
            self.lbl_api.setText(f"{dot}  {'API Bagli' if connected else 'API Bagli Degil'}")

    def _connect_signals(self) -> None:
        # Sidebar page_changed sinyali
        self.sidebar.page_changed.connect(self._select_page)
        self._connect_app_state()

    def _connect_app_state(self) -> None:
        """AppState sinyallerini status bar'a baglar."""
        state = self.ctx.app_state
        state.status_message.connect(self.set_status)
        state.project_changed.connect(self._on_project_changed)

    def _on_project_changed(self, project) -> None:
        name = project.name if project else "—"
        self.set_active_project(name)
        self.refresh_sidebar_badges()

    def refresh_sidebar_badges(self) -> None:
        try:
            from core.pipeline import step_badges
            badges = step_badges(self.ctx.app_state.current_project)
            self.sidebar.set_step_badges(badges)
        except Exception as exc:
            logger.debug("Sidebar rozetleri güncellenemedi: %s", exc)

    def _select_page(self, index: int) -> None:
        self.sidebar.set_active(index)
        # Flush the UI events so the sidebar immediately updates visually
        # before any heavy lazy loading occurs, making it feel fluent.
        QApplication.processEvents()
        
        # Lazy: sayfa henüz yüklenmemişse şimdi yükle
        if hasattr(self, "_page_cache") and index not in self._page_cache:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._ensure_page(index)
            QApplication.restoreOverrideCursor()
            
        self.stack.setCurrentIndex(index)
        self.refresh_sidebar_badges()
        logger.debug("Sayfa degisti: %d", index)

    # Sayfa adı → index eşleşmesi
    _PAGE_NAMES = {
        "home": 0, "projects": 1, "images": 2,
        "analysis": 3, "script": 4, "tts": 5,
        "render": 6, "settings": 7,
    }

    def navigate_to(self, page_name: str) -> None:
        """Sayfa adiyla gezinme."""
        idx = self._PAGE_NAMES.get(page_name.lower())
        if idx is not None:
            self._select_page(idx)
        else:
            logger.warning("Bilinmeyen sayfa adi: %s", page_name)

    def set_status(self, message: str) -> None:
        """Alt cubuk mesajini gunceller."""
        self.lbl_message.setText(message)

    def set_active_project(self, name: str) -> None:
        """Aktif proje adini gunceller."""
        self.lbl_project.setText(f"Proje: {name}")

    def _center_on_screen(self) -> None:
        """Pencereyi ekranın ortasına konumlandırır."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width()  - self.width())  // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(max(geo.x(), x), max(geo.y(), y))

    def set_api_status(self, connected: bool) -> None:
        """API baglanti durumunu gunceller."""
        if connected:
            self.lbl_api.setObjectName("statusApiConnected")
            self.lbl_api.setText("  API Bagli")
        else:
            self.lbl_api.setObjectName("statusApiDisconnected")
            self.lbl_api.setText("  API Bagli Degil")
        self._update_api_icon(connected)
        self.lbl_api.style().unpolish(self.lbl_api)
        self.lbl_api.style().polish(self.lbl_api)

    def closeEvent(self, event) -> None:
        """Pencere kapanırken çalışan QThread'leri durdur — aksi halde native crash."""
        from ui.workers.thread_utils import shutdown_page
        for page in list(getattr(self, "_page_cache", {}).values()):
            shutdown_page(page)
        super().closeEvent(event)