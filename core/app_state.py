"""
RecapAI - Global uygulama durumu (Singleton).
"""

import json
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from core.constants import SETTINGS_PATH

logger = logging.getLogger(__name__)


class AppState(QObject):
    """
    Uygulama genelinde paylaşılan durum nesnesi.
    Singleton pattern — get_instance() ile erişin.
    """

    project_changed = pyqtSignal(object)   # Project | None
    chapter_changed = pyqtSignal(object)   # Chapter | None
    status_message = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        from core.models import Project, Chapter  # geç import
        self._current_project: Optional[Project] = None
        self._current_chapter: Optional[Chapter] = None
        self._settings: dict = self._load_settings()

    # ── Settings ───────────────────────────────────────────────────
    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Ayarlar yüklenemedi: %s", exc)
            return {}

    def save_settings(self) -> None:
        """Ayarları config/settings.json'a yazar."""
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self._settings, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug("Ayarlar kaydedildi.")
        except Exception as exc:
            logger.error("Ayarlar kaydedilemedi: %s", exc)

    @property
    def settings(self) -> dict:
        return self._settings

    def get_setting(self, *keys: str, default=None):
        """İç içe anahtar zinciri ile ayar değeri getirir."""
        node = self._settings
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, default)
        return node

    def set_setting(self, value, *keys: str) -> None:
        """İç içe anahtar zinciri ile ayar değeri yazar."""
        node = self._settings
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

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