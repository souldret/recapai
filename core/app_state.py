"""
RecapAI - Global uygulama durumu (Singleton).
"""

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class AppState(QObject):
    """
    Uygulama genelinde paylaşılan çalışma zamanı durumu.
    Ayarlar için tek yetkili kaynak SettingsManager'dır;
    bu sınıf yalnızca aktif proje/bölüm ve UI mesaj sinyallerini tutar.
    """

    project_changed = pyqtSignal(object)   # Project | None
    chapter_changed = pyqtSignal(object)   # Chapter | None
    status_message  = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        from core.models import Project, Chapter  # geç import
        self._current_project: Optional[Project] = None
        self._current_chapter: Optional[Chapter] = None

    # ── Settings (SettingsManager'a köprü) ────────────────────────
    @staticmethod
    def _sm():
        """SettingsManager singleton'una güvenli erişim."""
        from core.settings_manager import SettingsManager
        return SettingsManager.instance()

    def save_settings(self) -> None:
        """Ayarları SettingsManager üzerinden kaydeder (sinyal uyumlu)."""
        self._sm().save()

    @property
    def settings(self) -> dict:
        """Tüm ayarların anlık kopyasını döner."""
        return self._sm().get_all()

    def get_setting(self, *keys: str, default=None):
        """
        İç içe anahtar zinciri ile ayar değeri getirir.
        Örnek: get_setting("api", "openrouter_api_key")
        """
        key_path = ".".join(keys)
        return self._sm().get(key_path, default)

    def set_setting(self, value, *keys: str) -> None:
        """
        İç içe anahtar zinciri ile ayar değeri yazar ve kaydeder.
        Örnek: set_setting("dark", "app", "theme")
        """
        key_path = ".".join(keys)
        self._sm().set(key_path, value)

    # ── Current Project ────────────────────────────────────────────
    @property
    def current_project(self):
        return self._current_project

    @current_project.setter
    def current_project(self, project) -> None:
        self._current_project = project
        self.project_changed.emit(project)
        name = project.name if project else "—"
        self.status_message.emit(f"Proje yüklendi: {name}")
        logger.info("Aktif proje: %s", name)

    # ── Current Chapter ────────────────────────────────────────────
    @property
    def current_chapter(self):
        return self._current_chapter

    @current_chapter.setter
    def current_chapter(self, chapter) -> None:
        self._current_chapter = chapter
        self.chapter_changed.emit(chapter)
        name = chapter.name if chapter else "—"
        logger.debug("Aktif bölüm: %s", name)