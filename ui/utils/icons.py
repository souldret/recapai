"""
RecapAI - Merkezi ikon yöneticisi.
qtawesome (FontAwesome / Material Design Icons) tabanlı.
"""

import logging
from typing import Optional

from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtCore import QSize

logger = logging.getLogger(__name__)

# ── Tema renkleri ──────────────────────────────────────────────────────────────
ICON_COLOR         = "#c0caf5"
ICON_COLOR_ACTIVE  = "#7aa2f7"
ICON_COLOR_SUCCESS = "#9ece6a"
ICON_COLOR_WARNING = "#e0af68"
ICON_COLOR_ERROR   = "#f7768e"
ICON_COLOR_MUTED   = "#565f89"
ICON_COLOR_DARK    = "#1a1b26"

# qtawesome mevcut mu?
_QTA_AVAILABLE = False
try:
    import qtawesome as qta
    _QTA_AVAILABLE = True
except ImportError:
    logger.warning("qtawesome kurulu degil. 'pip install qtawesome==1.3.1' calistirin.")


def _get_icon(name: str, color: str = ICON_COLOR, **kwargs) -> QIcon:
    """qtawesome ile ikon olustur; yoksa bos QIcon don."""
    if not _QTA_AVAILABLE:
        return QIcon()
    try:
        return qta.icon(name, color=color, **kwargs)
    except Exception as e:
        logger.debug("Icon '%s' yuklenemedi: %s", name, e)
        return QIcon()


class Icons:
    """
    Merkezi ikon erisim sinifi.
    Kullanim:
        icon = Icons.get(Icons.HOME)
        icon = Icons.get(Icons.HOME, color="#ff0000")
        pixmap = Icons.pixmap(Icons.HOME, size=24)
    """

    # ── Sidebar Ikonlari ───────────────────────────────────────────
    HOME     = "mdi.home-outline"
    PROJECTS = "mdi.folder-multiple-outline"
    IMAGES   = "mdi.image-multiple-outline"
    ANALYSIS = "mdi.brain"
    SCRIPT   = "mdi.script-text-outline"
    TTS      = "mdi.microphone-outline"
    RENDER   = "mdi.movie-open-outline"
    SETTINGS = "mdi.cog-outline"

    # ── Aksiyon Ikonlari ───────────────────────────────────────────
    ADD      = "mdi.plus-circle-outline"
    DELETE   = "mdi.trash-can-outline"
    EDIT     = "mdi.pencil-outline"
    SAVE     = "mdi.content-save-outline"
    LOAD     = "mdi.folder-open-outline"
    REFRESH  = "mdi.refresh"
    SEARCH   = "mdi.magnify"
    FILTER   = "mdi.filter-outline"
    COPY     = "mdi.content-copy"
    CLEAR    = "mdi.broom"
    UNDO     = "mdi.undo"
    REDO     = "mdi.redo"
    MOOD     = "mdi.emoticon-neutral-outline"
    FLASH    = "mdi.flash"
    MAP_MARKER = "mdi.map-marker"
    CALENDAR = "mdi.calendar"
    BOOK     = "mdi.book-open-variant"
    ACCOUNT  = "mdi.account"

    # ── Media Ikonlari ─────────────────────────────────────────────
    PLAY     = "mdi.play"
    PAUSE    = "mdi.pause"
    STOP     = "mdi.stop"
    NEXT     = "mdi.skip-next"
    PREVIOUS = "mdi.skip-previous"
    VOLUME   = "mdi.volume-high"
    VOLUME_MUTE = "mdi.volume-off"

    # ── Durum Ikonlari ─────────────────────────────────────────────
    SUCCESS    = "mdi.check-circle-outline"
    ERROR      = "mdi.alert-circle-outline"
    WARNING    = "mdi.alert-outline"
    INFO       = "mdi.information-outline"
    PENDING    = "mdi.clock-outline"
    PROCESSING = "mdi.loading"
    CHECK      = "mdi.check"
    CLOSE      = "mdi.close"

    # ── Render/Preset Ikonlari ─────────────────────────────────────
    YOUTUBE  = "mdi.youtube"
    TIKTOK   = "mdi.cellphone"
    CINEMA   = "mdi.filmstrip"

    # ── Genel Ikonlar ──────────────────────────────────────────────
    AI       = "mdi.robot-outline"
    CHIP     = "mdi.chip"
    EXPORT   = "mdi.export"
    IMPORT   = "mdi.import"
    DOWNLOAD = "mdi.download-outline"
    UPLOAD   = "mdi.upload-outline"
    EYE      = "mdi.eye-outline"
    EYE_OFF  = "mdi.eye-off-outline"
    MENU     = "mdi.menu"
    ARROW_LEFT  = "mdi.arrow-left"
    ARROW_RIGHT = "mdi.arrow-right"
    ARROW_UP    = "mdi.arrow-up"
    ARROW_DOWN  = "mdi.arrow-down"
    EXPAND   = "mdi.chevron-down"
    COLLAPSE = "mdi.chevron-up"
    LINK     = "mdi.link-variant"
    KEY      = "mdi.key-variant"
    GLOBE    = "mdi.web"
    COMPUTER = "mdi.desktop-tower-monitor"
    FOLDER   = "mdi.folder-outline"
    FILE     = "mdi.file-outline"
    CANCEL   = "mdi.cancel"
    PIPELINE = "mdi.lightning-bolt"
    WRENCH   = "mdi.wrench-outline"
    PACKAGE  = "mdi.package-variant-closed"

    @staticmethod
    def get(name: str, color: str = ICON_COLOR, **kwargs) -> QIcon:
        """Ikon al. name: 'mdi.home' gibi qtawesome string."""
        return _get_icon(name, color=color, **kwargs)

    @staticmethod
    def get_active(name: str) -> QIcon:
        """Aktif (mavi) renkte ikon al."""
        return _get_icon(name, color=ICON_COLOR_ACTIVE)

    @staticmethod
    def get_success(name: str) -> QIcon:
        """Basari (yesil) renkte ikon al."""
        return _get_icon(name, color=ICON_COLOR_SUCCESS)

    @staticmethod
    def get_error(name: str) -> QIcon:
        """Hata (kirmizi) renkte ikon al."""
        return _get_icon(name, color=ICON_COLOR_ERROR)

    @staticmethod
    def get_warning(name: str) -> QIcon:
        """Uyari (sari) renkte ikon al."""
        return _get_icon(name, color=ICON_COLOR_WARNING)

    @staticmethod
    def get_muted(name: str) -> QIcon:
        """Soluk (gri) renkte ikon al."""
        return _get_icon(name, color=ICON_COLOR_MUTED)

    @staticmethod
    def pixmap(name: str, size: int = 18, color: str = ICON_COLOR) -> QPixmap:
        """Direkt QPixmap al."""
        icon = _get_icon(name, color=color)
        if icon.isNull():
            return QPixmap()
        return icon.pixmap(QSize(size, size))

    @staticmethod
    def is_available() -> bool:
        """qtawesome yuklu mu?"""
        return _QTA_AVAILABLE