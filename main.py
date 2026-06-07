"""
RecapAI - Ana giriş noktası.
"""

import os
import sys

# Qt multimedia spam mesajlarini bastir (QMediaPlayer backend uyarilari)
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
os.environ.setdefault("QT_MEDIA_BACKEND", "windows")


class _FilteredStderr:
    """QMediaPlayer/QtMultimedia uyari mesajlarini stderr'den filtrele."""

    _FILTERS = (
        "No QtMultimedia backends found",
        "Failed to initialize QMediaPlayer",
        "Failed to create QVideoSink",
        "defaultServiceProvider",
        "qt.multimedia",
    )

    def __init__(self, original):
        self._orig = original

    def write(self, text: str) -> None:
        if not any(f in text for f in self._FILTERS):
            self._orig.write(text)

    def flush(self) -> None:
        self._orig.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


sys.stderr = _FilteredStderr(sys.stderr)

import logging
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from core.constants import THEME_PATH, APP_LOG_PATH, ERROR_LOG_PATH, LOGS_DIR

# Log dizini oluştur
LOGS_DIR.mkdir(exist_ok=True)

from logging.handlers import RotatingFileHandler as _RotatingFileHandler

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        _RotatingFileHandler(
            APP_LOG_PATH,
            encoding="utf-8",
            maxBytes=5 * 1024 * 1024,   # 5 MB
            backupCount=3,               # app.log, app.log.1, app.log.2, app.log.3
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def load_stylesheet(app: QApplication) -> None:
    """QSS tema dosyasini yukler ve uygular. settings.json'daki tema tercihini dikkate alır."""
    import json as _json
    theme = "dark"
    try:
        settings_path = Path("config/settings.json")
        if settings_path.exists():
            data = _json.loads(settings_path.read_text(encoding="utf-8"))
            theme = data.get("app", {}).get("theme", "dark")
    except Exception:
        pass

    theme_map = {
        "light": Path("config/theme_light.qss"),
        "space_blue": Path("config/theme_space_blue.qss"),
        "dark": THEME_PATH,
    }
    qss_path = theme_map.get(theme, THEME_PATH)
    if not qss_path.exists():
        qss_path = THEME_PATH

    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
        logger.debug("Tema yuklendi: %s (%s)", qss_path, theme)
    else:
        logger.warning("Tema dosyasi bulunamadi: %s", qss_path)


def setup_exception_handler() -> None:
    """Yakalanmamis istisnalari logs/error.log dosyasina yazar."""
    error_logger = logging.getLogger("uncaught")
    handler = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [CRITICAL] %(message)s"))
    error_logger.addHandler(handler)

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error_logger.critical(
            "Yakalanmamis istisna:", exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = handle_exception


def check_multimedia_support() -> bool:
    """QMediaPlayer kullanilabilir mi kontrol et."""
    try:
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
        player = QMediaPlayer()
        audio_out = QAudioOutput()
        player.setAudioOutput(audio_out)
        # Kisa sure bekleyip backend kontrolu yap
        logger.info("QtMultimedia: OK")
        del player, audio_out
        return True
    except ImportError:
        logger.warning("QtMultimedia: PyQt6-Qt6 multimedia modulu bulunamadi")
        return False
    except Exception as e:
        logger.warning("QtMultimedia: %s", e)
        return False


def check_edge_tts_version() -> bool:
    """
    Edge-TTS versiyonunu kontrol et; 7.0.0'dan eski ise uyar.
    Returns: True = guncel veya belirlenemiyor, False = eski.
    """
    try:
        import edge_tts
        version = getattr(edge_tts, "__version__", "unknown")
        logger.info("Edge-TTS versiyon: %s", version)
        if version != "unknown":
            major = int(version.split(".")[0])
            if major < 7:
                logger.warning(
                    "Edge-TTS %s eski! 403 hatalari alabilirsiniz. "
                    "Guncelleyin: pip install --upgrade edge-tts",
                    version,
                )
                return False
        return True
    except Exception as exc:
        logger.error("Edge-TTS versiyon kontrolu basarisiz: %s", exc)
        return False


def check_tts_dependencies() -> list:
    """
    TTS bagimliliklarini kontrol eder.
    Returns: Uyari mesajlarinin listesi (bosssa sorun yok).
    """
    warnings = []

    # Edge-TTS kontrolu
    try:
        import edge_tts  # noqa: F401
        if not check_edge_tts_version():
            warnings.append(
                "edge-tts surumu eski (7.0.0+ gerekli) -> "
                "'pip install --upgrade edge-tts'"
            )
        else:
            logger.info("edge-tts: OK")
    except ImportError:
        warnings.append("edge-tts kurulu degil -> 'pip install --upgrade edge-tts'")

    # pydub kontrolu
    try:
        import pydub  # noqa: F401
        logger.info("pydub: OK")
    except ImportError:
        warnings.append("pydub kurulu degil -> 'pip install pydub'  (FFmpeg da gerekli)")

    # Kokoro kontrolu (zorunlu degil, opsiyonel)
    try:
        import torch   # noqa: F401
        import kokoro  # noqa: F401
        import soundfile  # noqa: F401
        import numpy      # noqa: F401
        logger.info("Kokoro TTS: OK")
    except (ImportError, OSError, Exception) as exc:
        warnings.append(f"Kokoro TTS kullanilamıyor ({type(exc).__name__}) — Edge-TTS kullanilabilir")

    return warnings


def check_qtawesome() -> None:
    """qtawesome yuklu mu kontrol et."""
    try:
        import qtawesome  # noqa: F401
        logger.info("qtawesome: OK")
    except ImportError:
        logger.warning("qtawesome kurulu degil. Ikonlar gorunmeyebilir. 'pip install qtawesome==1.3.1'")


def main() -> None:
    setup_exception_handler()
    logger.info("RecapAI baslatiliyor...")

    app = QApplication(sys.argv)
    app.setApplicationName("RecapAI")
    app.setOrganizationName("RecapAI")
    app.setFont(QFont("Segoe UI", 10))

    load_stylesheet(app)
    check_multimedia_support()
    check_qtawesome()

    # KRİTİK: Manager'ları uygulama başlarken oluştur.
    from core.settings_manager import SettingsManager
    from core.openrouter_client import OpenRouterClient
    from core.app_state import AppState
    from core.context import AppContext

    settings_manager = SettingsManager()
    open_router_client = OpenRouterClient(settings_manager=settings_manager)
    app_state = AppState()

    ctx = AppContext(
        settings_manager=settings_manager,
        app_state=app_state,
        open_router_client=open_router_client
    )

    logger.info(
        "Manager'lar hazır. API key tanımlı: %s", settings_manager.has_api_key()
    )

    from ui.main_window import MainWindow
    window = MainWindow(ctx=ctx)
    window.show()

    logger.info("Ana pencere gosterildi.")

    # TTS bagimlilik kontrolu (pencere gosterildikten sonra)
    tts_warnings = check_tts_dependencies()
    if tts_warnings:
        logger.warning("TTS uyarilari: %s", tts_warnings)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
