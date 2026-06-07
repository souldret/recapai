"""
RecapAI - Ayarlar Sayfası.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGroupBox, QLineEdit, QComboBox, QTabWidget, QFileDialog,
    QMessageBox, QSizePolicy, QDialog, QDialogButtonBox,
    QTextEdit, QScrollArea, QListWidget, QListWidgetItem,
    QApplication,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QThread

from core.context import AppContext
from ui.widgets.page_header import PageHeader
from ui.utils.icons import Icons, ICON_COLOR_SUCCESS, ICON_COLOR_ERROR
from ui.utils.icons import ICON_COLOR_WARNING, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)
SETTINGS_PATH = Path("config/settings.json")


class APITestWorker(QThread):
    result_ready = pyqtSignal(bool, str, dict)

    def __init__(self, client, api_key: str = "") -> None:
        super().__init__()
        self.client = client
        self._test_key = api_key.strip()

    def run(self) -> None:
        try:
            # Geçici anahtar verilmişse, testin süresince in-memory olarak uygula
            # (kaydetme yok; SettingsManager.set kullanmıyoruz)
            from core.settings_manager import SettingsManager
            sm = SettingsManager.instance()
            original_key = sm.get_api_key()
            use_temp = self._test_key and self._test_key != original_key
            if use_temp:
                sm.set("api.openrouter_api_key", self._test_key, save=False)
            try:
                ok, msg = self.client.test_connection()
                info = self.client.get_account_info() if ok else {}
                self.result_ready.emit(ok, msg, info or {})
            finally:
                if use_temp:
                    sm.set("api.openrouter_api_key", original_key, save=False)
        except Exception as e:
            self.result_ready.emit(False, str(e), {})


class ModelRefreshWorker(QThread):
    result_ready = pyqtSignal(list, str)
    
    def __init__(self, client) -> None:
        super().__init__()
        self.client = client
        
    def run(self) -> None:
        try:
            models = self.client.list_models(force_refresh=True)
            self.result_ready.emit(models, "")
        except Exception as e:
            self.result_ready.emit([], str(e))


class ModelTestWorker(QThread):
    result_ready = pyqtSignal(dict, str)
    
    def __init__(self, client, model_id) -> None:
        super().__init__()
        self.client = client
        self.model_id = model_id
        
    def run(self) -> None:
        try:
            result = self.client.chat_completion(
                model=self.model_id,
                messages=[{"role": "user", "content": "Merhaba! Bir cümlede kendini tanıt."}],
                max_tokens=120,
            )
            self.result_ready.emit(result, "")
        except Exception as e:
            self.result_ready.emit({}, str(e))


def _load_models_list(category: str) -> list:
    try:
        data = json.loads(Path("config/models.json").read_text(encoding="utf-8"))
        return data.get(category, [])
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# KokoroSetupDialog
# ─────────────────────────────────────────────────────────────────────────────

class _FixWorker(QThread):
    """Pip komutunu arka planda calistirir."""
    output = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, cmd: list) -> None:
        super().__init__()
        self._cmd = cmd

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                self._cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                self.output.emit(line.rstrip())
            proc.wait()
            self.finished.emit(proc.returncode == 0)
        except Exception as e:
            self.output.emit(f"Hata: {e}")
            self.finished.emit(False)


class KokoroSetupDialog(QDialog):
    """
    Kokoro kurulum sorunlarini cozen dialog.
    Durum kontrolu + cozum onerileri + otomatik duzelt.
    """

    def __init__(self, error_message: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kokoro Kurulum Yardimcisi")
        self.setMinimumWidth(540)
        self.setMinimumHeight(480)
        self._error_message = error_message
        self._fix_worker: Optional[_FixWorker] = None
        self._build_ui()
        self._run_checks()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # Baslik
        title = QLabel("Kokoro TTS Kurulum Yardimcisi")
        title.setObjectName("headingLabel")
        layout.addWidget(title)

        if self._error_message:
            err_lbl = QLabel(self._error_message)
            err_lbl.setWordWrap(True)
            err_lbl.setObjectName("errorLabel")
            layout.addWidget(err_lbl)

        # Durum kontrol alani
        status_frame = QFrame()
        status_frame.setObjectName("sectionFrame")
        sv = QVBoxLayout(status_frame)
        sv.setContentsMargins(16, 12, 16, 12)
        sv.setSpacing(6)

        sv.addWidget(QLabel("Sistem Durumu:"))

        self._status_labels = {}
        checks = [
            ("python",   "Python versiyonu"),
            ("torch",    "PyTorch"),
            ("torch_run","PyTorch calisiyor"),
            ("kokoro",   "Kokoro paketi"),
            ("espeak",   "espeak-ng (opsiyonel)"),
            ("vcredist", "VC++ Redistributable (Windows)"),
        ]
        for key, label in checks:
            row = QHBoxLayout()
            row.setSpacing(8)
            icon_lbl = QLabel("...")
            icon_lbl.setFixedWidth(20)
            self._status_labels[key] = (icon_lbl, QLabel(label))
            row.addWidget(icon_lbl)
            row.addWidget(self._status_labels[key][1])
            row.addStretch()
            sv.addLayout(row)

        layout.addWidget(status_frame)

        # Cozum onerileri
        sol_frame = QFrame()
        sol_frame.setObjectName("sectionFrame")
        solv = QVBoxLayout(sol_frame)
        solv.setContentsMargins(16, 12, 16, 12)
        solv.setSpacing(8)

        solv.addWidget(QLabel("Cozum Onerileri:"))

        tips = [
            "CPU Torch kurulumu:",
            "  pip uninstall torch",
            "  pip install torch --index-url https://download.pytorch.org/whl/cpu",
            "",
            "CUDA (GPU) icin:",
            "  pip install torch --index-url https://download.pytorch.org/whl/cu121",
            "",
            "VC++ Redistributable (Windows):",
            "  microsoft.com/en-us/download/details.aspx?id=48145",
        ]
        tips_lbl = QLabel("\n".join(tips))
        tips_lbl.setObjectName("logView")
        tips_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        solv.addWidget(tips_lbl)
        layout.addWidget(sol_frame)

        # Log alani
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(100)
        self._log_area.setObjectName("logView")
        layout.addWidget(self._log_area)

        # Butonlar
        btn_row = QHBoxLayout()

        self._btn_fix = QPushButton("Otomatik Duzelt (CPU Torch)")
        self._btn_fix.setObjectName("primaryButton")
        self._btn_fix.setIcon(Icons.get(Icons.WRENCH, color="#1a1b26"))
        self._btn_fix.clicked.connect(self._auto_fix)
        btn_row.addWidget(self._btn_fix)

        self._btn_recheck = QPushButton("Tekrar Kontrol Et")
        self._btn_recheck.setObjectName("secondaryBtn")
        self._btn_recheck.setIcon(Icons.get(Icons.REFRESH))
        self._btn_recheck.clicked.connect(self._run_checks)
        btn_row.addWidget(self._btn_recheck)

        btn_row.addStretch()

        btn_close = QPushButton("Kapat")
        btn_close.setObjectName("ghostButton")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _set_status(self, key: str, ok: bool, text: Optional[str] = None) -> None:
        if key not in self._status_labels:
            return
        icon_lbl, name_lbl = self._status_labels[key]
        if ok:
            pm = Icons.pixmap(Icons.SUCCESS, size=14, color=ICON_COLOR_SUCCESS)
            if not pm.isNull():
                icon_lbl.setPixmap(pm)
            else:
                icon_lbl.setText("OK")
                icon_lbl.setStyleSheet(f"color: {ICON_COLOR_SUCCESS};")
        else:
            pm = Icons.pixmap(Icons.ERROR, size=14, color=ICON_COLOR_ERROR)
            if not pm.isNull():
                icon_lbl.setPixmap(pm)
            else:
                icon_lbl.setText("X")
                icon_lbl.setStyleSheet(f"color: {ICON_COLOR_ERROR};")
        if text:
            name_lbl.setText(text)

    def _run_checks(self) -> None:
        self._log_area.append("Kontrol ediliyor...")

        # Python
        ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ok = sys.version_info >= (3, 9)
        self._set_status("python", ok, f"Python {ver} {'(OK)' if ok else '(3.9+ gerekli)'}")

        # Torch
        try:
            import torch
            self._set_status("torch", True, f"PyTorch {torch.__version__}")
            # Torch calisiyor mu?
            try:
                _t = torch.tensor([1.0])
                del _t
                self._set_status("torch_run", True, "PyTorch calisiyor (DLL OK)")
            except Exception as e:
                self._set_status("torch_run", False, f"PyTorch DLL hatasi: {e}")
        except ImportError:
            self._set_status("torch", False, "PyTorch kurulu degil")
            self._set_status("torch_run", False, "PyTorch kurulu degil")
        except OSError as e:
            self._set_status("torch", False, f"PyTorch DLL hatasi")
            self._set_status("torch_run", False, f"DLL hatasi: {str(e)[:60]}")

        # Kokoro
        try:
            import kokoro  # noqa
            self._set_status("kokoro", True, "kokoro kurulu")
        except ImportError:
            self._set_status("kokoro", False, "kokoro kurulu degil")

        # espeak-ng
        try:
            import espeakng_loader
            self._set_status("espeak", True, "espeak-ng (via loader) kurulu")
        except ImportError:
            try:
                result = subprocess.run(["espeak-ng", "--version"],
                                        capture_output=True, text=True, timeout=5)
                ok = result.returncode == 0
                self._set_status("espeak", ok, "espeak-ng " + ("bulundu" if ok else "bulunamadi"))
            except Exception:
                self._set_status("espeak", False, "espeak-ng bulunamadi (opsiyonel)")

        # VC++ Redistributable (Windows)
        if sys.platform == "win32":
            try:
                import winreg
                key_path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    installed, _ = winreg.QueryValueEx(key, "Installed")
                    self._set_status("vcredist", bool(installed), "VC++ Redistributable kurulu")
            except Exception:
                self._set_status("vcredist", False, "VC++ Redistributable bulunamadi")
        else:
            self._set_status("vcredist", True, "Windows disi (gerekli degil)")

        self._log_area.append("Kontrol tamamlandi.")

    def _auto_fix(self) -> None:
        self._btn_fix.setEnabled(False)
        self._log_area.append("Torch yeniden kuruluyor (CPU)...")

        cmd = [sys.executable, "-m", "pip", "install",
               "torch", "--index-url", "https://download.pytorch.org/whl/cpu",
               "--force-reinstall"]

        self._fix_worker = _FixWorker(cmd)
        self._fix_worker.output.connect(lambda line: self._log_area.append(line))
        self._fix_worker.finished.connect(self._on_fix_done)
        self._fix_worker.start()

    def _on_fix_done(self, ok: bool) -> None:
        self._btn_fix.setEnabled(True)
        if ok:
            self._log_area.append("Kurulum tamamlandi. Uygulamayi yeniden baslatmaniz gerekebilir.")
        else:
            self._log_area.append("Kurulum basarisiz. Log ciktisini kontrol edin.")
        self._run_checks()


# ─────────────────────────────────────────────────────────────────────────────
# DiagnosticsTab
# ─────────────────────────────────────────────────────────────────────────────

class DiagnosticsTab(QWidget):
    """Sistem tanilama ve sorun giderme sekmesi."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QLabel("Sistem Tanilama")
        header.setObjectName("headingLabel")
        layout.addWidget(header)

        desc = QLabel(
            "Bagimliliklarin kurulum durumunu kontrol edin. "
            "Kirmizi satirlar eksik veya hatalı bilesenleri gosterir."
        )
        desc.setObjectName("cardSubtitle")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Scroll alani
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._status_container = QWidget()
        self._status_layout = QVBoxLayout(self._status_container)
        self._status_layout.setSpacing(4)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._status_container)
        layout.addWidget(scroll, 1)

        # Butonlar
        btn_row = QHBoxLayout()

        refresh_btn = QPushButton("Yenile")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.setIcon(Icons.get(Icons.REFRESH))
        refresh_btn.setFixedWidth(120)
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)

        fix_btn = QPushButton("Otomatik Duzelt")
        fix_btn.setObjectName("primaryButton")
        fix_btn.setIcon(Icons.get(Icons.DOWNLOAD, color="#1a1b26"))
        fix_btn.setFixedWidth(160)
        fix_btn.clicked.connect(self._auto_fix)
        btn_row.addWidget(fix_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ── Kontroller ────────────────────────────────────────────────

    def refresh(self) -> None:
        """Tum kontrolleri yeniden calistir."""
        # Eski satirlari temizle
        while self._status_layout.count():
            item = self._status_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        checks = [
            ("Python",      self._check_python()),
            ("PyQt6",       self._check_pyqt()),
            ("qtawesome",   self._check_qtawesome()),
            ("Pillow",      self._check_pillow()),
            ("pygame",      self._check_pygame()),
            ("pydub",       self._check_pydub()),
            ("FFmpeg",      self._check_ffmpeg()),
            ("edge-tts",    self._check_edge_tts()),
            ("numpy",       self._check_numpy()),
            ("torch",       self._check_torch()),
            ("kokoro",      self._check_kokoro()),
            ("espeak-ng",   self._check_espeak()),
        ]

        for name, (ok, msg) in checks:
            self._status_layout.addWidget(self._make_row(name, ok, msg))

        self._status_layout.addStretch()

    def _make_row(self, name: str, ok: bool, msg: str) -> QFrame:
        row = QFrame()
        row.setObjectName("card")
        row.setMaximumHeight(44)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 6, 12, 6)
        rl.setSpacing(10)

        # Durum ikonu
        icon_lbl = QLabel()
        color = ICON_COLOR_SUCCESS if ok else ICON_COLOR_ERROR
        icon_name = Icons.SUCCESS if ok else Icons.ERROR
        pm = Icons.pixmap(icon_name, size=16, color=color)
        if not pm.isNull():
            icon_lbl.setPixmap(pm)
        else:
            icon_lbl.setText("OK" if ok else "X")
            icon_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
        icon_lbl.setFixedWidth(20)
        rl.addWidget(icon_lbl)

        # Isim
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-weight: 600; min-width: 90px; max-width: 90px;")
        rl.addWidget(name_lbl)

        # Mesaj
        msg_lbl = QLabel(msg)
        msg_lbl.setStyleSheet(
            f"color: {ICON_COLOR_SUCCESS if ok else '#f7768e'};"
            if not ok else "color: #9aa5ce;"
        )
        msg_lbl.setWordWrap(False)
        rl.addWidget(msg_lbl, 1)

        return row

    # ── Bireysel kontroller ───────────────────────────────────────

    def _check_python(self):
        v = sys.version_info
        ok = v.major == 3 and 10 <= v.minor <= 12
        msg = f"{v.major}.{v.minor}.{v.micro}"
        if not ok:
            msg += "  (3.10–3.12 onerilir, 3.13 torch ile uyumsuz olabilir)"
        return ok, msg

    def _check_pyqt(self):
        try:
            from PyQt6.QtCore import QT_VERSION_STR
            return True, f"Qt {QT_VERSION_STR}"
        except Exception as e:
            return False, str(e)

    def _check_qtawesome(self):
        try:
            import qtawesome as qta  # noqa
            return True, "Kurulu"
        except ImportError:
            return False, "Kurulu degil — pip install qtawesome==1.3.1"

    def _check_pillow(self):
        try:
            from PIL import __version__ as v
            return True, v
        except ImportError:
            return False, "Kurulu degil — pip install Pillow"

    def _check_pygame(self):
        try:
            import pygame
            return True, pygame.version.ver
        except ImportError:
            return False, "Kurulu degil — pip install pygame==2.5.2"

    def _check_pydub(self):
        try:
            import pydub  # noqa
            return True, "Kurulu"
        except ImportError:
            return False, "Kurulu degil — pip install pydub"

    def _check_ffmpeg(self):
        try:
            r = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=4
            )
            if r.returncode == 0:
                first = r.stdout.split("\n")[0][:60]
                return True, first
        except Exception as exc:
            logger.debug("FFmpeg kontrolünde hata: %s", exc)
        return False, "PATH'te bulunamadi — https://www.gyan.dev/ffmpeg/builds/"

    def _check_edge_tts(self):
        try:
            import edge_tts  # noqa
            return True, "Kurulu"
        except ImportError:
            return False, "Kurulu degil — pip install edge-tts"

    def _check_numpy(self):
        try:
            import numpy as np
            v = np.__version__
            parts = v.split(".")
            major = int(parts[0])
            ok = major < 2
            msg = v + ("" if ok else "  (>=2.0 — torch ile uyumsuz! pip install 'numpy<2')")
            return ok, msg
        except ImportError:
            return False, "Kurulu degil — pip install numpy"

    def _check_torch(self):
        try:
            import torch
            _ = torch.tensor([1.0])
            cuda = "CUDA" if torch.cuda.is_available() else "CPU"
            return True, f"{torch.__version__} ({cuda})"
        except OSError:
            return False, "DLL hatasi — VC++ Redistributable kurun veya 'Otomatik Duzelt' tiklayin"
        except ImportError:
            return False, "Kurulu degil — python tools/install_dependencies.py calistirin"
        except Exception as e:
            return False, str(e)[:80]

    def _check_kokoro(self):
        try:
            from kokoro import KPipeline  # noqa
            return True, "Kurulu"
        except ImportError:
            return False, "Kurulu degil — pip install kokoro"
        except Exception as e:
            return False, str(e)[:80]

    def _check_espeak(self):
        try:
            import espeakng_loader
            return True, "Kurulu (espeakng_loader)"
        except ImportError:
            pass

        try:
            r = subprocess.run(
                ["espeak-ng", "--version"], capture_output=True, text=True, timeout=4
            )
            if r.returncode == 0:
                first = (r.stdout or r.stderr).split("\n")[0][:60]
                return True, first
        except Exception as exc:
            logger.debug("espeak-ng kontrolünde hata: %s", exc)
        return False, "Kurulu degil (Kokoro icin gerekli) — github.com/espeak-ng/espeak-ng/releases"

    # ── Otomatik duzelt ───────────────────────────────────────────

    def _auto_fix(self) -> None:
        script = Path("tools/install_dependencies.py")
        if not script.exists():
            QMessageBox.warning(
                self, "Dosya Bulunamadi",
                "tools/install_dependencies.py bulunamadi.\n"
                "Lutfen manuel olarak pip komutlarini calistirin."
            )
            return

        reply = QMessageBox.question(
            self, "Otomatik Duzelt",
            "tools/install_dependencies.py calistirilacak.\n"
            "Bu islem birkac dakika surebilir.\n\n"
            "Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            subprocess.Popen([sys.executable, str(script)])
            QMessageBox.information(
                self, "Baslatildi",
                "Kurulum yeni terminalde basladi.\n"
                "Tamamlandiginda uygulamayi yeniden baslatin."
            )


# ─────────────────────────────────────────────────────────────────────────────
# ModelTestTab
# ─────────────────────────────────────────────────────────────────────────────

class ModelTestTab(QWidget):
    """Model test ve doğrulama sekmesi."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Başlık
        header = QLabel("Model Test ve Doğrulama")
        header.setObjectName("headingLabel")
        layout.addWidget(header)

        info = QLabel(
            "OpenRouter bağlantısını test edin, modelleri listeleyin ve "
            "seçili modele örnek istek gönderin. 404 hatası alıyorsanız "
            "buradan hangi modellerin erişilebilir olduğunu kontrol edin."
        )
        info.setObjectName("cardSubtitle")
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── Bağlantı kartı ──────────────────────────────────────────
        conn_card = QFrame()
        conn_card.setObjectName("sectionFrame")
        cl = QVBoxLayout(conn_card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cl.addWidget(self._make_label("Bağlantı Durumu", bold=True))

        self.lbl_status = QLabel("Test edilmedi")
        self.lbl_status.setObjectName("mutedLabel")
        cl.addWidget(self.lbl_status)

        self.lbl_credit = QLabel("")
        self.lbl_credit.setObjectName("successLabel")
        cl.addWidget(self.lbl_credit)

        btn_row = QHBoxLayout()
        self.btn_test_conn = QPushButton("Bağlantıyı Test Et")
        self.btn_test_conn.setObjectName("primaryButton")
        self.btn_test_conn.setIcon(Icons.get(Icons.GLOBE, color="#1a1b26"))
        self.btn_test_conn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.btn_test_conn)

        self.btn_refresh = QPushButton("Modelleri Yenile")
        self.btn_refresh.setIcon(Icons.get(Icons.REFRESH))
        self.btn_refresh.clicked.connect(self._refresh_models)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch()
        cl.addLayout(btn_row)
        layout.addWidget(conn_card)

        # ── Model test kartı ────────────────────────────────────────
        test_card = QFrame()
        test_card.setObjectName("sectionFrame")
        tl = QVBoxLayout(test_card)
        tl.setContentsMargins(16, 14, 16, 14)
        tl.setSpacing(10)

        tl.addWidget(self._make_label("Model Testi", bold=True))

        sub = QLabel("Seçili modele kısa bir test isteği gönderir.")
        sub.setObjectName("cardSubtitle")
        tl.addWidget(sub)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(300)
        self._populate_model_combo()
        model_row.addWidget(self.model_combo, 1)

        self.btn_test_model = QPushButton("Modeli Test Et")
        self.btn_test_model.setObjectName("primaryButton")
        self.btn_test_model.setIcon(Icons.get(Icons.AI, color="#1a1b26"))
        self.btn_test_model.clicked.connect(self._test_model)
        model_row.addWidget(self.btn_test_model)
        tl.addLayout(model_row)

        self.txt_result = QTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setMaximumHeight(180)
        self.txt_result.setPlaceholderText("Test sonuçları burada görünecek…")
        tl.addWidget(self.txt_result)

        layout.addWidget(test_card)

        # ── Erişilebilir modeller listesi ───────────────────────────
        models_card = QFrame()
        models_card.setObjectName("sectionFrame")
        ml = QVBoxLayout(models_card)
        ml.setContentsMargins(16, 14, 16, 14)
        ml.setSpacing(8)

        self.lbl_model_count = QLabel("Henüz yüklenmedi — 'Modelleri Yenile' düğmesine basın.")
        self.lbl_model_count.setObjectName("cardSubtitle")
        ml.addWidget(self._make_label("Erişilebilir Modeller", bold=True))
        ml.addWidget(self.lbl_model_count)

        self.lst_models = QListWidget()
        self.lst_models.setMaximumHeight(220)
        ml.addWidget(self.lst_models)

        layout.addWidget(models_card)
        layout.addStretch()

    # ── Yardımcılar ────────────────────────────────────────────────

    def _make_label(self, text: str, bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        if bold:
            lbl.setObjectName("cardTitle")
        return lbl

    def _populate_model_combo(self) -> None:
        """Combo kutusunu config/models.json ile doldurur."""
        self.model_combo.clear()
        try:
            data = json.loads(Path("config/models.json").read_text(encoding="utf-8"))
            self.model_combo.addItem("── Vision Modelleri ──", None)
            for m in data.get("vision_models", []):
                cost = m.get("cost", "")
                label = f"{m['name']}  [{cost}]" if cost else m["name"]
                self.model_combo.addItem(label, m["id"])
            self.model_combo.addItem("── Script Modelleri ──", None)
            for m in data.get("script_models", []):
                cost = m.get("cost", "")
                label = f"{m['name']}  [{cost}]" if cost else m["name"]
                self.model_combo.addItem(label, m["id"])
        except Exception as exc:
            logger.error("Model combo doldurulamadı: %s", exc)

    def _get_client(self):
        """Geçerli API key ile OpenRouterClient döner veya None."""
        from core.openrouter_client import OpenRouterClient
        try:
            data = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
            api_key = data.get("api", {}).get("openrouter_api_key", "").strip()
        except Exception:
            api_key = ""

        if not api_key:
            QMessageBox.warning(
                self, "API Key Eksik",
                "Önce API sekmesinden API key girin ve kaydedin."
            )
            return None
        client = OpenRouterClient.instance()
        client.update_api_key(api_key)
        return client

    # ── Aksiyon metodları ──────────────────────────────────────────

    def _test_connection(self) -> None:
        """OpenRouter bağlantısını ve kredi bilgisini test eder."""
        client = self._get_client()
        if not client:
            return

        self.lbl_status.setText("Test ediliyor…")
        self.lbl_status.setStyleSheet("color: #e0af68;")
        self.lbl_credit.setText("")
        self.btn_test_conn.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        
        self._test_worker = APITestWorker(client)
        self._test_worker.result_ready.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, ok: bool, msg: str, info: dict) -> None:
        self.btn_test_conn.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        if ok:
            self.lbl_status.setText(f"Bağlantı Başarılı: {msg}")
            self.lbl_status.setStyleSheet("color: #9ece6a; font-weight: 600;")
            usage = info.get("usage", 0) or 0
            limit = info.get("limit")
            label = info.get("label", "")
            if limit is not None:
                self.lbl_credit.setText(f"Hesap: {label}  |  Kullanım: ${usage:.4f}  |  Kalan: ${limit - usage:.4f}")
            else:
                self.lbl_credit.setText(f"Hesap: {label}  |  Kullanım: ${usage:.4f}")
        else:
            self.lbl_status.setText(f"Bağlantı Başarısız: {msg}")
            self.lbl_status.setStyleSheet("color: #f7768e; font-weight: 600;")

    def _refresh_models(self) -> None:
        """OpenRouter'dan model listesini çeker ve listeleye doldurur."""
        client = self._get_client()
        if not client:
            return

        self.lst_models.clear()
        self.lbl_model_count.setText("Yükleniyor…")
        self.btn_refresh.setEnabled(False)
        
        self._refresh_worker = ModelRefreshWorker(client)
        self._refresh_worker.result_ready.connect(self._on_refresh_done)
        self._refresh_worker.start()

    def _on_refresh_done(self, models: list, error: str) -> None:
        self.btn_refresh.setEnabled(True)
        if error:
            self.lbl_model_count.setText(f"Hata: {error}")
            return
            
        if models:
            self.lbl_model_count.setText(f"Toplam {len(models)} model erişilebilir")
            for m in sorted(models, key=lambda x: x.get("id", "")):
                m_id = m.get("id", "")
                m_name = m.get("name", m_id)
                pricing = m.get("pricing", {})
                prompt_price = pricing.get("prompt", "0")
                item = QListWidgetItem(f"{m_id}  —  {m_name}")
                item.setToolTip(
                    f"ID: {m_id}\n"
                    f"İsim: {m_name}\n"
                    f"Prompt: ${prompt_price}/token\n"
                    f"Context: {m.get('context_length', '?')}"
                )
                self.lst_models.addItem(item)
        else:
            self.lbl_model_count.setText("Model listesi alınamadı (bağlantı veya key sorunu)")

    def _test_model(self) -> None:
        """Seçili modele test isteği gönderir."""
        model_id = self.model_combo.currentData()
        if not model_id:
            self.txt_result.setText("Lütfen bir model seçin (başlık satırları seçilemez).")
            return

        client = self._get_client()
        if not client:
            return

        self.txt_result.setText(f"Test ediliyor: {model_id}\n…")
        self.btn_test_model.setEnabled(False)
        
        self._model_test_worker = ModelTestWorker(client, model_id)
        self._model_test_worker.result_ready.connect(lambda res, err: self._on_model_test_done(model_id, res, err))
        self._model_test_worker.start()

    def _on_model_test_done(self, model_id: str, result: dict, error: str) -> None:
        self.btn_test_model.setEnabled(True)
        if error:
            self.txt_result.setText(
                f"HATA\n\n"
                f"Model : {model_id}\n"
                f"Hata  : {error}"
            )
            return

        content = result.get("content", "")
        usage = result.get("usage", {})
        self.txt_result.setText(
            f"BAŞARILI\n\n"
            f"Model  : {model_id}\n"
            f"Yanıt  : {content}\n\n"
            f"Token kullanımı:\n"
            f"  Prompt     : {usage.get('prompt_tokens', '?')}\n"
            f"  Completion : {usage.get('completion_tokens', '?')}\n"
            f"  Toplam     : {usage.get('total_tokens', '?')}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SettingsPage
# ─────────────────────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    """Uygulama ayarlari sayfasi."""

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()
        self._load_current_settings()
        logger.debug("SettingsPage olusturuldu.")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Kaydet butonu (header'a action olarak)
        btn_save = QPushButton("  Kaydet")
        btn_save.setObjectName("primaryButton")
        btn_save.setIcon(Icons.get(Icons.SAVE, color="#ffffff"))
        btn_save.setIconSize(QSize(16, 16))
        btn_save.setMinimumWidth(110)
        btn_save.setMinimumHeight(36)
        btn_save.clicked.connect(self._save_settings)

        header = PageHeader(
            "Ayarlar",
            "API anahtarlari, modeller ve uygulama tercihlerini yapilandirin.",
            actions=[btn_save],
        )
        layout.addWidget(header)

        # Tab widget icin scroll
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(28, 20, 28, 28)
        cv.setSpacing(0)
        cv.addWidget(self._make_tabs(), 1)

        layout.addWidget(content, 1)

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _make_tabs(self) -> QTabWidget:
        tabs = QTabWidget()

        api_tab = self._wrap_scroll(self._make_api_tab())
        tabs.addTab(api_tab, "API")
        tabs.setTabIcon(0, Icons.get(Icons.KEY))

        models_tab = self._wrap_scroll(self._make_models_tab())
        tabs.addTab(models_tab, "Modeller")
        tabs.setTabIcon(1, Icons.get(Icons.AI))

        paths_tab = self._wrap_scroll(self._make_paths_tab())
        tabs.addTab(paths_tab, "Dizinler")
        tabs.setTabIcon(2, Icons.get(Icons.FOLDER))

        tts_tab = self._make_tts_tab()  # has scroll internally
        tabs.addTab(tts_tab, "Seslendirme")
        tabs.setTabIcon(3, Icons.get(Icons.TTS))

        panel_tab = self._wrap_scroll(self._make_panel_export_tab())
        tabs.addTab(panel_tab, "Panel & Export")
        tabs.setTabIcon(4, Icons.get(Icons.SEARCH))

        model_test_tab = self._wrap_scroll(ModelTestTab(self.ctx))
        tabs.addTab(model_test_tab, "Model Test")
        tabs.setTabIcon(5, Icons.get(Icons.CHIP))

        diag_tab = DiagnosticsTab(self.ctx)  # has scroll internally
        tabs.addTab(diag_tab, "Tanilama")
        tabs.setTabIcon(6, Icons.get(Icons.WRENCH))

        about_tab = self._wrap_scroll(self._make_about_tab())
        tabs.addTab(about_tab, "Hakkinda")
        tabs.setTabIcon(7, Icons.get(Icons.INFO))

        return tabs

    # ── API Sekmesi ────────────────────────────────────────────────

    def _make_api_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(20)

        key_frame = self._section_frame("OpenRouter API Anahtari")
        kv = QVBoxLayout(key_frame)
        kv.setContentsMargins(16, 14, 16, 14)
        kv.setSpacing(10)

        kv.addWidget(self._field_label("API Anahtari:"))
        key_row = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-or-v1-...")
        key_row.addWidget(self.api_key_input, 1)

        self.btn_toggle = QPushButton("Goster")
        self.btn_toggle.setFixedWidth(80)
        self.btn_toggle.setObjectName("secondaryBtn")
        self.btn_toggle.setIcon(Icons.get(Icons.EYE))
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.toggled.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.btn_toggle)
        kv.addLayout(key_row)

        kv.addWidget(self._field_label("Base URL:"))
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("https://openrouter.ai/api/v1")
        kv.addWidget(self.base_url_input)
        v.addWidget(key_frame)

        # Test baglantisi
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("Baglantıyı Test Et")
        self.btn_test.setObjectName("secondaryBtn")
        self.btn_test.setIcon(Icons.get(Icons.GLOBE))
        self.btn_test.setFixedWidth(180)
        self.btn_test.clicked.connect(self._test_connection)
        test_row.addWidget(self.btn_test)
        self.lbl_test_result = QLabel("")
        test_row.addWidget(self.lbl_test_result)
        test_row.addStretch()
        v.addLayout(test_row)

        v.addStretch()
        return page

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        if visible:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle.setText("Gizle")
            self.btn_toggle.setIcon(Icons.get(Icons.EYE_OFF))
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle.setText("Goster")
            self.btn_toggle.setIcon(Icons.get(Icons.EYE))

    def _test_connection(self) -> None:
        api_key = self.api_key_input.text().strip()
        if not api_key:
            self.lbl_test_result.setText("API anahtari bos.")
            self.lbl_test_result.setStyleSheet("color: #e0af68;")
            return

        self.btn_test.setEnabled(False)
        self.lbl_test_result.setText("Test ediliyor...")
        self.lbl_test_result.setStyleSheet("color: #9aa5ce;")

        # Girilen anahtarı worker'a geçiriyoruz — kayıtlı anahtardan farklıysa
        # worker geçici olarak o anahtarı kullanır, diski değiştirmez.
        self._api_test_worker = APITestWorker(self.ctx.open_router_client, api_key)
        self._api_test_worker.result_ready.connect(self._on_api_test_done)
        self._api_test_worker.start()

    def _on_api_test_done(self, ok: bool, msg: str, info: dict) -> None:
        self.btn_test.setEnabled(True)
        if ok:
            self.lbl_test_result.setText(f"Bağlantı Başarılı: {msg}")
            self.lbl_test_result.setStyleSheet(f"color: {ICON_COLOR_SUCCESS};")
        else:
            self.lbl_test_result.setText(f"Bağlantı Başarısız: {msg}")
            self.lbl_test_result.setStyleSheet(f"color: {ICON_COLOR_ERROR};")

    # ── Modeller Sekmesi ───────────────────────────────────────────

    def _make_models_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(20)

        frame = self._section_frame("Varsayilan Modeller")
        fv = QVBoxLayout(frame)
        fv.setContentsMargins(16, 14, 16, 14)
        fv.setSpacing(12)

        fv.addWidget(self._field_label("Vision Modeli (gorsel analiz):"))
        self.vision_model_combo = QComboBox()
        self.vision_model_combo.setFixedWidth(360)
        for m in _load_models_list("vision_models"):
            label = m["name"]
            if m.get("recommended"):
                label += "  [Onerilen]"
            cost = m.get("cost", "")
            if cost:
                label += f"  [{cost}]"
            self.vision_model_combo.addItem(label, m["id"])
        fv.addWidget(self.vision_model_combo)

        fv.addWidget(self._field_label("Script Modeli (metin olusturma):"))
        self.script_model_combo = QComboBox()
        self.script_model_combo.setFixedWidth(360)
        for m in _load_models_list("script_models"):
            label = m["name"]
            if m.get("recommended"):
                label += "  [Onerilen]"
            self.script_model_combo.addItem(label, m["id"])
        fv.addWidget(self.script_model_combo)

        v.addWidget(frame)
        v.addStretch()
        return page

    # ── Dizinler Sekmesi ───────────────────────────────────────────

    def _make_paths_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(20)

        frame = self._section_frame("Klasor Yollari")
        fv = QVBoxLayout(frame)
        fv.setContentsMargins(16, 14, 16, 14)
        fv.setSpacing(12)

        fv.addWidget(self._field_label("Projeler Dizini:"))
        self.projects_dir_input, _ = self._path_row(fv, "./projects")

        fv.addWidget(self._field_label("Cikti Dizini:"))
        self.output_dir_input, _ = self._path_row(fv, "./output")

        fv.addWidget(self._field_label("Log Dizini:"))
        self.logs_dir_input, _ = self._path_row(fv, "./logs")

        v.addWidget(frame)
        v.addStretch()
        return page

    def _path_row(self, parent_layout, placeholder: str):
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit, 1)
        btn = QPushButton()
        btn.setFixedWidth(36)
        btn.setObjectName("secondaryBtn")
        btn.setIcon(Icons.get(Icons.FOLDER))
        btn.clicked.connect(lambda: self._browse_dir(edit))
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return edit, btn

    def _browse_dir(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Klasor Sec")
        if path:
            target.setText(path)

    # ── TTS Sekmesi ────────────────────────────────────────────────

    def _make_tts_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QDoubleSpinBox, QCheckBox
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)

        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(20)

        # Genel TTS
        gen_frame = self._section_frame("Varsayilan TTS Ayarlari")
        gv = QVBoxLayout(gen_frame)
        gv.setContentsMargins(16, 14, 16, 14)
        gv.setSpacing(12)

        gv.addWidget(self._field_label("Varsayilan Motor:"))
        self.tts_engine_combo = QComboBox()
        self.tts_engine_combo.setFixedWidth(300)

        # Kokoro durumunu kontrol et
        kokoro_available = False
        kokoro_error = None
        try:
            from core.tts_engine import KokoroTTSEngine
            k = KokoroTTSEngine()
            kokoro_available = k.is_available()
            kokoro_error = k.get_error()
        except Exception:
            pass

        self.tts_engine_combo.addItem("Edge-TTS (Online, Ucretsiz)", "edge-tts")
        if kokoro_available:
            self.tts_engine_combo.addItem("Kokoro TTS (Offline, Yuksek Kalite)", "kokoro")
        else:
            self.tts_engine_combo.addItem("Kokoro TTS  [Yuklenemedi — tikla]", "kokoro_error")
        gv.addWidget(self.tts_engine_combo)

        # Kokoro hata butonu
        if not kokoro_available:
            self._kokoro_error_msg = kokoro_error
            btn_kokoro_help = QPushButton("Kokoro Kurulum Sorununu Coz")
            btn_kokoro_help.setObjectName("dangerButton")
            btn_kokoro_help.setIcon(Icons.get(Icons.WRENCH, color="#f7768e"))
            btn_kokoro_help.setFixedWidth(240)
            btn_kokoro_help.clicked.connect(self._open_kokoro_setup)
            gv.addWidget(btn_kokoro_help)

        gv.addWidget(self._field_label("Varsayilan Hiz:"))
        speed_row = QHBoxLayout()
        self.tts_speed_spin = QDoubleSpinBox()
        self.tts_speed_spin.setRange(0.5, 2.0)
        self.tts_speed_spin.setSingleStep(0.1)
        self.tts_speed_spin.setValue(1.0)
        self.tts_speed_spin.setFixedWidth(80)
        speed_row.addWidget(self.tts_speed_spin)
        speed_row.addWidget(QLabel("x"))
        speed_row.addStretch()
        gv.addLayout(speed_row)
        v.addWidget(gen_frame)

        # Edge-TTS sesi
        edge_frame = self._section_frame("Edge-TTS Varsayilan Ses")
        ev = QVBoxLayout(edge_frame)
        ev.setContentsMargins(16, 14, 16, 14)
        ev.setSpacing(12)
        ev.addWidget(self._field_label("Varsayilan Ses (Edge-TTS):"))
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setFixedWidth(300)
        self._populate_edge_voices()
        ev.addWidget(self.tts_voice_combo)
        v.addWidget(edge_frame)

        # Kokoro ayarlari
        kokoro_frame = self._section_frame("Kokoro TTS Ayarlari")
        kv = QVBoxLayout(kokoro_frame)
        kv.setContentsMargins(16, 14, 16, 14)
        kv.setSpacing(12)

        kv.addWidget(self._field_label("Varsayilan Ses (Kokoro):"))
        self.kokoro_voice_combo = QComboBox()
        self.kokoro_voice_combo.setFixedWidth(300)
        self._populate_kokoro_voices()
        kv.addWidget(self.kokoro_voice_combo)

        kv.addWidget(self._field_label("Model Cache Yolu:"))
        self.kokoro_cache_input, _ = self._path_row(kv, "~/.cache/huggingface/hub")

        gpu_row = QHBoxLayout()
        self.kokoro_gpu_check = QCheckBox("GPU kullan (CUDA)")
        self._check_cuda_available()
        gpu_row.addWidget(self.kokoro_gpu_check)
        gpu_row.addStretch()
        kv.addLayout(gpu_row)

        status_row = QHBoxLayout()
        self.lbl_kokoro_model_status = QLabel("Model durumu: kontrol ediliyor...")
        self.lbl_kokoro_model_status.setObjectName("mutedLabel")
        status_row.addWidget(self.lbl_kokoro_model_status)
        status_row.addStretch()
        self.btn_download_model = QPushButton("Modeli Indir")
        self.btn_download_model.setObjectName("secondaryBtn")
        self.btn_download_model.setIcon(Icons.get(Icons.DOWNLOAD))
        self.btn_download_model.setFixedWidth(160)
        self.btn_download_model.clicked.connect(self._download_kokoro_model)
        status_row.addWidget(self.btn_download_model)
        kv.addLayout(status_row)
        v.addWidget(kokoro_frame)

        v.addStretch()
        self._check_kokoro_model_status()
        return scroll

    def _open_kokoro_setup(self) -> None:
        """Kokoro kurulum yardimcisini ac."""
        error = getattr(self, "_kokoro_error_msg", None)
        dlg = KokoroSetupDialog(error_message=error, parent=self)
        dlg.exec()
        # Tekrar kontrol et
        self._check_kokoro_model_status()

    def _populate_edge_voices(self) -> None:
        voices = [
            ("tr-TR-AhmetNeural",  "Ahmet (TR, Erkek, Dramatik)"),
            ("tr-TR-EmelNeural",   "Emel (TR, Kadin, Yumusak)"),
            ("en-US-GuyNeural",    "Guy (EN-US, Erkek)"),
            ("en-US-JennyNeural",  "Jenny (EN-US, Kadin)"),
            ("en-US-AndrewMultilingualNeural", "Andrew Multilingual (EN-US)"),
            ("en-GB-RyanNeural",   "Ryan (EN-GB, Erkek)"),
            ("en-GB-SoniaNeural",  "Sonia (EN-GB, Kadin)"),
        ]
        for vid, vname in voices:
            self.tts_voice_combo.addItem(vname, vid)

    def _populate_kokoro_voices(self) -> None:
        voices = [
            ("af_heart",   "Heart (Female, Warm)"),
            ("af_bella",   "Bella (Female, Professional)"),
            ("af_nicole",  "Nicole (Female, Casual)"),
            ("af_sarah",   "Sarah (Female, Narrator)"),
            ("am_adam",    "Adam (Male, Dramatic)"),
            ("am_michael", "Michael (Male, Deep)"),
            ("am_onyx",    "Onyx (Male, Strong)"),
            ("bf_emma",    "Emma (Female, British)"),
            ("bm_george",  "George (Male, British)"),
        ]
        for vid, vname in voices:
            self.kokoro_voice_combo.addItem(vname, vid)

    def _check_cuda_available(self) -> None:
        try:
            import torch
            if torch.cuda.is_available():
                self.kokoro_gpu_check.setChecked(True)
                self.kokoro_gpu_check.setText("GPU kullan (CUDA) — kullanilabilir")
            else:
                self.kokoro_gpu_check.setChecked(False)
                self.kokoro_gpu_check.setText("GPU kullan (CUDA) — mevcut degil")
                self.kokoro_gpu_check.setEnabled(False)
        except (ImportError, OSError, Exception):
            self.kokoro_gpu_check.setChecked(False)
            self.kokoro_gpu_check.setText("GPU kullan (torch kurulu degil)")
            self.kokoro_gpu_check.setEnabled(False)

    def _check_kokoro_model_status(self) -> None:
        try:
            from core.tts_engine import KokoroTTSEngine
            engine = KokoroTTSEngine()
            if not engine.is_available():
                self.lbl_kokoro_model_status.setText("Model durumu: Kokoro yuklenemedi")
                self.lbl_kokoro_model_status.setStyleSheet(f"color: {ICON_COLOR_ERROR};")
                self.btn_download_model.setEnabled(False)
                return
            if engine.is_model_downloaded():
                self.lbl_kokoro_model_status.setText("Model durumu: Yuklu")
                self.lbl_kokoro_model_status.setStyleSheet(f"color: {ICON_COLOR_SUCCESS};")
            else:
                self.lbl_kokoro_model_status.setText("Model durumu: Yuklu degil (~350MB)")
                self.lbl_kokoro_model_status.setStyleSheet(f"color: {ICON_COLOR_WARNING};")
        except Exception:
            self.lbl_kokoro_model_status.setText("Model durumu: bilinmiyor")

    def _download_kokoro_model(self) -> None:
        self.btn_download_model.setEnabled(False)
        self.lbl_kokoro_model_status.setText("Indiriliyor...")
        self.lbl_kokoro_model_status.setStyleSheet(f"color: {ICON_COLOR_WARNING};")
        from ui.workers.tts_worker import KokoroModelDownloadWorker
        self._kokoro_dl_worker = KokoroModelDownloadWorker()
        self._kokoro_dl_worker.progress_msg.connect(
            lambda msg: self.lbl_kokoro_model_status.setText(f"Indiriliyor: {msg}")
        )
        self._kokoro_dl_worker.finished.connect(self._on_kokoro_download_done)
        self._kokoro_dl_worker.start()

    def _on_kokoro_download_done(self, ok: bool) -> None:
        self.btn_download_model.setEnabled(True)
        if ok:
            self.lbl_kokoro_model_status.setText("Model indirildi.")
            self.lbl_kokoro_model_status.setStyleSheet(f"color: {ICON_COLOR_SUCCESS};")
        else:
            self.lbl_kokoro_model_status.setText("Indirme basarisiz.")
            self.lbl_kokoro_model_status.setStyleSheet(f"color: {ICON_COLOR_ERROR};")

    # ── Panel & Dışa Aktarma Sekmesi ──────────────────────────────

    def _make_panel_export_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QDoubleSpinBox, QCheckBox, QSpinBox
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(20)

        # ── Panel Tespit Varsayılanları ─────────────────────────────
        panel_frame = self._section_frame("Panel Tespit Varsayılanları")
        pv = QVBoxLayout(panel_frame)
        pv.setContentsMargins(16, 14, 16, 14)
        pv.setSpacing(12)

        pv.addWidget(self._field_label("Min Alan Oranı (%):"))
        self.panel_min_area_spin = QDoubleSpinBox()
        self.panel_min_area_spin.setRange(0.1, 20.0)
        self.panel_min_area_spin.setSingleStep(0.5)
        self.panel_min_area_spin.setValue(2.0)
        self.panel_min_area_spin.setSuffix(" %")
        self.panel_min_area_spin.setFixedWidth(120)
        pv.addWidget(self.panel_min_area_spin)

        pv.addWidget(self._field_label("Max Alan Oranı (%):"))
        self.panel_max_area_spin = QDoubleSpinBox()
        self.panel_max_area_spin.setRange(20.0, 99.0)
        self.panel_max_area_spin.setSingleStep(5.0)
        self.panel_max_area_spin.setValue(80.0)
        self.panel_max_area_spin.setSuffix(" %")
        self.panel_max_area_spin.setFixedWidth(120)
        pv.addWidget(self.panel_max_area_spin)

        pv.addWidget(self._field_label("Varsayılan Okuma Düzeni:"))
        self.panel_reading_order_combo = QComboBox()
        self.panel_reading_order_combo.addItem("RTL — Sağdan Sola (Manga)", "rtl")
        self.panel_reading_order_combo.addItem("LTR — Soldan Sağa (Manhwa)", "ltr")
        self.panel_reading_order_combo.setFixedWidth(280)
        pv.addWidget(self.panel_reading_order_combo)

        self.panel_show_arrows_check = QCheckBox("Okuma sırası oklarını göster")
        self.panel_show_arrows_check.setChecked(True)
        pv.addWidget(self.panel_show_arrows_check)

        v.addWidget(panel_frame)

        # ── Stitch Kalite Ayarları ──────────────────────────────────
        stitch_frame = self._section_frame("Stitch Kalite Varsayılanları")
        sv = QVBoxLayout(stitch_frame)
        sv.setContentsMargins(16, 14, 16, 14)
        sv.setSpacing(12)

        sv.addWidget(self._field_label("Çözünürlük Ölçeği:"))
        self.stitch_scale_combo = QComboBox()
        self.stitch_scale_combo.addItem("Orijinal (100%)", 100)
        self.stitch_scale_combo.addItem("%75", 75)
        self.stitch_scale_combo.addItem("%50", 50)
        self.stitch_scale_combo.setFixedWidth(200)
        sv.addWidget(self.stitch_scale_combo)

        sv.addWidget(self._field_label("Çıktı Formatı:"))
        self.stitch_format_combo = QComboBox()
        self.stitch_format_combo.addItem("PNG (kayıpsız)", "png")
        self.stitch_format_combo.addItem("JPEG q=85", "jpeg85")
        self.stitch_format_combo.addItem("JPEG q=70", "jpeg70")
        self.stitch_format_combo.addItem("JPEG q=50", "jpeg50")
        self.stitch_format_combo.setFixedWidth(200)
        sv.addWidget(self.stitch_format_combo)

        v.addWidget(stitch_frame)

        # ── Dışa Aktarma Varsayılanları ─────────────────────────────
        export_frame = self._section_frame("Dışa Aktarma Varsayılanları")
        ev = QVBoxLayout(export_frame)
        ev.setContentsMargins(16, 14, 16, 14)
        ev.setSpacing(12)

        ev.addWidget(self._field_label("Kaydetme Formatı:"))
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItem("JPEG", "jpg")
        self.export_format_combo.addItem("PNG", "png")
        self.export_format_combo.addItem("WebP", "webp")
        self.export_format_combo.setFixedWidth(160)
        ev.addWidget(self.export_format_combo)

        ev.addWidget(self._field_label("JPEG Kalitesi (1–95):"))
        self.export_jpeg_quality_spin = QSpinBox()
        self.export_jpeg_quality_spin.setRange(1, 95)
        self.export_jpeg_quality_spin.setValue(85)
        self.export_jpeg_quality_spin.setFixedWidth(100)
        ev.addWidget(self.export_jpeg_quality_spin)

        self.export_metadata_check = QCheckBox("panels.json metadata dosyası oluştur")
        self.export_metadata_check.setChecked(True)
        ev.addWidget(self.export_metadata_check)

        v.addWidget(export_frame)

        # ── Tema Seçimi ─────────────────────────────────────────────
        theme_frame = self._section_frame("Tema")
        tv = QVBoxLayout(theme_frame)
        tv.setContentsMargins(16, 14, 16, 14)
        tv.setSpacing(12)

        tv.addWidget(self._field_label("Uygulama Teması:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Tokyo Night (Koyu)", "dark")
        self.theme_combo.addItem("Uzay Mavisi (Space Blue)", "space_blue")
        self.theme_combo.addItem("Açık Tema", "light")
        self.theme_combo.setFixedWidth(240)
        tv.addWidget(self.theme_combo)

        theme_hint = QLabel("Tema değişikliği uygulamayı yeniden başlatmanızı gerektirebilir.")
        theme_hint.setObjectName("cardSubtitle")
        theme_hint.setWordWrap(True)
        tv.addWidget(theme_hint)

        btn_apply_theme = QPushButton("Temayı Uygula")
        btn_apply_theme.setObjectName("secondaryBtn")
        btn_apply_theme.setFixedWidth(140)
        btn_apply_theme.clicked.connect(self._apply_theme)
        tv.addWidget(btn_apply_theme)

        v.addWidget(theme_frame)
        v.addStretch()
        return page

    def _apply_theme(self) -> None:
        """Seçili temayı anında uygular."""
        theme = self.theme_combo.currentData() or "dark"
        from pathlib import Path as _Path
        theme_map = {
            "light": _Path("config/theme_light.qss"),
            "space_blue": _Path("config/theme_space_blue.qss"),
            "dark": _Path("config/theme.qss"),
        }
        theme_file = theme_map.get(theme, _Path("config/theme.qss"))
        if not theme_file.exists():
            theme_file = _Path("config/theme.qss")
        try:
            qss = theme_file.read_text(encoding="utf-8")
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().setStyleSheet(qss)
            # Ayara kaydet
            try:
                data = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
                data.setdefault("app", {})["theme"] = theme
                Path("config/settings.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
            theme_labels = {"light": "Açık", "space_blue": "Uzay Mavisi", "dark": "Tokyo Night"}
            self.ctx.app_state.status_message.emit(
                f"Tema uygulandı: {theme_labels.get(theme, theme)}"
            )
        except Exception as exc:
            QMessageBox.warning(self, "Hata", f"Tema dosyası okunamadı:\n{exc}")

    # ── Hakkinda Sekmesi ───────────────────────────────────────────

    def _make_about_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 32, 32, 32)
        v.setSpacing(10)
        v.setAlignment(Qt.AlignmentFlag.AlignTop)

        items = [
            ("RecapAI",  "color: #7aa2f7; font-size: 26px; font-weight: 700;"),
            ("Sürüm 1.0.0 Beta", "color: #9aa5ce; font-size: 14px;"),
            ("", ""),
            ("Manhwa/manga görsellerinden AI destekli recap videoları üretin.", "color: #c0caf5;"),
            ("Desteklenen Vision API: OpenRouter (Gemini, Claude, GPT-4o, Qwen)", "color: #9aa5ce;"),
            ("Seslendirme: Edge TTS (online), Kokoro TTS (offline)", "color: #9aa5ce;"),
            ("Video: FFmpeg tabanlı render, Ken Burns, geçiş efektleri, altyazı", "color: #9aa5ce;"),
            ("", ""),
            ("Geliştirici: Soldret  |  Lisans: MIT", "color: #565f89; font-size: 12px;"),
            ("© 2024-2025 Soldret. Tüm hakları saklıdır.", "color: #565f89; font-size: 11px;"),
        ]
        for text, style in items:
            lbl = QLabel(text)
            if style:
                lbl.setStyleSheet(style)
            v.addWidget(lbl)

        v.addStretch()
        return page

    # ── Yukleme / Kaydetme ─────────────────────────────────────────

    def _load_current_settings(self) -> None:
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        api = data.get("api", {})
        self.api_key_input.setText(api.get("openrouter_api_key", ""))
        self.base_url_input.setText(api.get("openrouter_base_url", "https://openrouter.ai/api/v1"))

        defaults = data.get("defaults", {})
        self._set_combo_by_data(self.vision_model_combo, defaults.get("vision_model", ""))
        self._set_combo_by_data(self.script_model_combo, defaults.get("script_model", ""))
        self._set_combo_by_data(self.tts_engine_combo,   defaults.get("tts_engine", "edge-tts"))
        self._set_combo_by_data(self.tts_voice_combo,    defaults.get("tts_voice", "tr-TR-AhmetNeural"))
        self._set_combo_by_data(self.kokoro_voice_combo, defaults.get("kokoro_voice", "af_heart"))

        tts = data.get("tts", {})
        if hasattr(self, "tts_speed_spin"):
            self.tts_speed_spin.setValue(float(tts.get("default_speed", 1.0)))
        if hasattr(self, "kokoro_cache_input"):
            self.kokoro_cache_input.setText(tts.get("kokoro_cache_path", ""))
        if hasattr(self, "kokoro_gpu_check"):
            self.kokoro_gpu_check.setChecked(bool(tts.get("kokoro_use_gpu", True)))

        paths = data.get("paths", {})
        self.projects_dir_input.setText(paths.get("projects_dir", "./projects"))
        self.output_dir_input.setText(paths.get("output_dir", "./output"))
        self.logs_dir_input.setText(paths.get("logs_dir", "./logs"))

        # Panel ayarları
        panel = data.get("panel", {})
        if hasattr(self, "panel_min_area_spin"):
            self.panel_min_area_spin.setValue(float(panel.get("min_area_ratio", 0.02)) * 100)
        if hasattr(self, "panel_max_area_spin"):
            self.panel_max_area_spin.setValue(float(panel.get("max_area_ratio", 0.80)) * 100)
        if hasattr(self, "panel_reading_order_combo"):
            self._set_combo_by_data(
                self.panel_reading_order_combo,
                panel.get("default_reading_order", "rtl"),
            )
        if hasattr(self, "panel_show_arrows_check"):
            self.panel_show_arrows_check.setChecked(bool(panel.get("show_order_arrows", True)))

        # Stitch ayarları
        stitch = data.get("stitch", {})
        if hasattr(self, "stitch_scale_combo"):
            scale = stitch.get("scale_percent", 100)
            idx = self.stitch_scale_combo.findData(scale)
            if idx >= 0:
                self.stitch_scale_combo.setCurrentIndex(idx)
        if hasattr(self, "stitch_format_combo"):
            fmt = stitch.get("format", "png")
            q   = stitch.get("jpeg_quality")
            fmt_key = "png" if fmt == "png" or q is None else f"jpeg{q}"
            idx = self.stitch_format_combo.findData(fmt_key)
            if idx >= 0:
                self.stitch_format_combo.setCurrentIndex(idx)

        # Export ayarları
        export = data.get("export", {})
        if hasattr(self, "export_format_combo"):
            self._set_combo_by_data(self.export_format_combo, export.get("format", "jpg"))
        if hasattr(self, "export_jpeg_quality_spin"):
            self.export_jpeg_quality_spin.setValue(int(export.get("jpeg_quality", 85)))
        if hasattr(self, "export_metadata_check"):
            self.export_metadata_check.setChecked(bool(export.get("include_metadata_json", True)))

        # Tema
        app_conf = data.get("app", {})
        if hasattr(self, "theme_combo"):
            self._set_combo_by_data(self.theme_combo, app_conf.get("theme", "dark"))

    def _save_settings(self) -> None:
        try:
            sm = self.ctx.settings_manager

            # Mevcut ayarları al (SettingsManager içindeki)
            data = sm.get_all()

            data.setdefault("api", {})
            new_api_key = self.api_key_input.text().strip()
            data["api"]["openrouter_api_key"] = new_api_key
            data["api"]["openrouter_base_url"] = (
                self.base_url_input.text().strip() or "https://openrouter.ai/api/v1"
            )

            data.setdefault("defaults", {})
            engine_data = self.tts_engine_combo.currentData() or "edge-tts"
            if engine_data == "kokoro_error":
                engine_data = "edge-tts"
            data["defaults"]["vision_model"] = self.vision_model_combo.currentData() or ""
            data["defaults"]["script_model"] = self.script_model_combo.currentData() or ""
            data["defaults"]["tts_engine"]   = engine_data
            data["defaults"]["tts_voice"]    = self.tts_voice_combo.currentData() or ""
            data["defaults"]["kokoro_voice"] = self.kokoro_voice_combo.currentData() or ""

            data.setdefault("tts", {})
            data["tts"]["default_speed"]     = self.tts_speed_spin.value()
            data["tts"]["kokoro_cache_path"] = self.kokoro_cache_input.text().strip()
            data["tts"]["kokoro_use_gpu"]    = self.kokoro_gpu_check.isChecked()

            data.setdefault("paths", {})
            data["paths"]["projects_dir"] = self.projects_dir_input.text().strip() or "./projects"
            data["paths"]["output_dir"]   = self.output_dir_input.text().strip() or "./output"
            data["paths"]["logs_dir"]     = self.logs_dir_input.text().strip() or "./logs"

            # Panel ayarları
            if hasattr(self, "panel_min_area_spin"):
                data.setdefault("panel", {})
                data["panel"]["min_area_ratio"]       = round(self.panel_min_area_spin.value() / 100.0, 4)
                data["panel"]["max_area_ratio"]       = round(self.panel_max_area_spin.value() / 100.0, 4)
                data["panel"]["default_reading_order"] = self.panel_reading_order_combo.currentData() or "rtl"
                data["panel"]["show_order_arrows"]    = self.panel_show_arrows_check.isChecked()

            # Stitch ayarları
            if hasattr(self, "stitch_scale_combo"):
                data.setdefault("stitch", {})
                data["stitch"]["scale_percent"] = self.stitch_scale_combo.currentData() or 100
                fmt_key = self.stitch_format_combo.currentData() or "png"
                if fmt_key == "png":
                    data["stitch"]["format"]       = "png"
                    data["stitch"]["jpeg_quality"] = None
                else:
                    data["stitch"]["format"]       = "jpeg"
                    data["stitch"]["jpeg_quality"] = int(fmt_key.replace("jpeg", ""))

            # Export ayarları
            if hasattr(self, "export_format_combo"):
                data.setdefault("export", {})
                data["export"]["format"]               = self.export_format_combo.currentData() or "jpg"
                data["export"]["jpeg_quality"]         = self.export_jpeg_quality_spin.value()
                data["export"]["include_metadata_json"] = self.export_metadata_check.isChecked()

            # Tema
            if hasattr(self, "theme_combo"):
                data.setdefault("app", {})
                data["app"]["theme"] = self.theme_combo.currentData() or "dark"

            # SettingsManager üzerinden kaydet — tüm sinyaller otomatik yayınlanır.
            # update_from_dict: _settings'i günceller, save() çağırır,
            # api_key_changed sinyalini yayınlar. Hata varsa except bloğu yakalar.
            old_key = sm.get_api_key()
            sm.update_from_dict(data, save=True)

            # api_key_changed update_from_dict içinde zaten yayınlanır;
            # güvenlik için bir kez daha yayınlıyoruz (key değiştiyse).
            if old_key != new_api_key and new_api_key:
                sm.api_key_changed.emit(new_api_key)

            self.ctx.app_state.status_message.emit("Ayarlar kaydedildi.")
            logger.info("Ayarlar kaydedildi. API key uzunluğu: %d", len(new_api_key))
            QMessageBox.information(
                self, "Basarili",
                "Ayarlar kaydedildi.\nTüm modüller yeni ayarları kullanacak."
            )

        except Exception as exc:
            logger.error("Ayarlar kaydedilemedi: %s", exc)
            QMessageBox.critical(self, "Hata", f"Ayarlar kaydedilemedi:\n{exc}")

    # ── Yardimcilar ────────────────────────────────────────────────

    def _section_frame(self, title: str) -> QGroupBox:
        """Başlıklı bölüm kutusu döner. Çağrı noktaları QVBoxLayout(frame) ile layout oluşturur."""
        box = QGroupBox(title)
        box.setObjectName("sectionFrame")
        return box

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("cardSubtitle")
        return lbl

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)