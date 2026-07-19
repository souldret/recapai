"""
RecapAI - Ses önizleme dialog'u.
Seçili ses ile test cümlesi sentezler ve oynatır.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QProgressBar, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize

from ui.widgets.audio_player import AudioPlayerWidget
from ui.utils.icons import Icons

logger = logging.getLogger(__name__)

# Test cümleleri
PREVIEW_TEXT = {
    "tr": "Merhaba, ben senin manhwa anlatıcınım. Hikayemize başlayalım.",
    "en": "Hello, I am your manhwa narrator. Let's begin our story.",
    "ja": "こんにちは、私はあなたのマンワナレーターです。",
    "es": "Hola, soy tu narrador de manhwa. Comencemos nuestra historia.",
    "fr": "Bonjour, je suis votre narrateur manhwa. Commençons notre histoire.",
    "de": "Hallo, ich bin dein Manhwa-Erzähler. Lass uns unsere Geschichte beginnen.",
}


class _SynthWorker(QThread):
    """Arka planda önizleme sentezi yapar."""
    done  = pyqtSignal(str)   # output_path
    error = pyqtSignal(str)

    def __init__(
        self,
        engine_name: str,
        voice_id: str,
        text: str,
        out_path: str,
        params: Optional[dict] = None,
    ):
        super().__init__()
        self.engine_name = engine_name
        self.voice_id    = voice_id
        self.text        = text
        self.out_path    = out_path
        self.params      = params or {}

    def run(self) -> None:
        try:
            from core.tts_engine import TTSManager
            engine = TTSManager.get_instance().get_engine(self.engine_name)
            engine.synthesize(
                text=self.text,
                voice=self.voice_id,
                output_path=self.out_path,
                **self.params,
            )
            self.done.emit(self.out_path)
        except Exception as exc:
            logger.error("Önizleme sentez hatası: %s", exc)
            self.error.emit(str(exc))


class VoicePreviewDialog(QDialog):
    """
    Ses önizleme dialog'u.
    voice_selected sinyali: seçilen voice_id'yi üst widget'a gönderir.
    """

    voice_selected = pyqtSignal(str)   # voice_id

    def __init__(
        self,
        engine_name: str,
        voices: list,
        current_voice: Optional[str] = None,
        params: Optional[dict] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ses Önizleme")
        self.setMinimumWidth(520)
        self.setModal(True)

        self._engine_name = engine_name
        self._voices      = voices
        self._current_voice = current_voice
        self._params = params or {}
        self._worker: Optional[_SynthWorker] = None
        self._tmp_path: Optional[str] = None

        self._build_ui()
        self._populate_voices()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Başlık
        title = QLabel("Ses Önizleme")
        title.setObjectName("headingLabel")
        root.addWidget(title)

        # Ses seçici
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Ses:"))
        self._voice_combo = QComboBox()
        self._voice_combo.setMinimumWidth(280)
        voice_row.addWidget(self._voice_combo, 1)
        root.addLayout(voice_row)

        # Test metni bilgisi
        info = QLabel("Test cümlesi sese göre otomatik seçilir (TR/EN/JP...).")
        info.setObjectName("mutedLabel")
        root.addWidget(info)

        # Progress / durum
        self._lbl_status = QLabel("Ses seçip 'Önizle' butonuna basın.")
        self._lbl_status.setObjectName("mutedLabel")
        root.addWidget(self._lbl_status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        self._progress.setFixedHeight(6)
        root.addWidget(self._progress)

        # Audio player
        self._player = AudioPlayerWidget()
        root.addWidget(self._player)

        # Alt butonlar
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("separator")
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        self._btn_preview = QPushButton("  Önizle")
        self._btn_preview.setIcon(Icons.get(Icons.PLAY))
        self._btn_preview.setIconSize(QSize(16, 16))
        self._btn_preview.setFixedWidth(120)
        self._btn_preview.clicked.connect(self._do_preview)
        btn_row.addWidget(self._btn_preview)

        btn_row.addStretch()

        self._btn_select = QPushButton("  Bu Sesi Seç")
        self._btn_select.setIcon(Icons.get(Icons.CHECK))
        self._btn_select.setIconSize(QSize(16, 16))
        self._btn_select.setFixedWidth(140)
        self._btn_select.clicked.connect(self._select_voice)
        btn_row.addWidget(self._btn_select)

        btn_cancel = QPushButton("İptal")
        btn_cancel.setObjectName("secondaryBtn")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        root.addLayout(btn_row)

    def _populate_voices(self) -> None:
        for v in self._voices:
            self._voice_combo.addItem(v["name"], v["id"])

        if self._current_voice:
            idx = self._voice_combo.findData(self._current_voice)
            if idx >= 0:
                self._voice_combo.setCurrentIndex(idx)

    # ── Mantık ────────────────────────────────────────────────────

    def _get_preview_text(self, voice_id: str) -> str:
        """Voice'a göre test metni seç."""
        # Dil tespiti: voice id veya engine'den
        first = voice_id[0] if voice_id else "a"
        lang_map = {
            "a": "en", "b": "en",
            "j": "ja", "e": "es", "f": "fr",
            "h": "en", "i": "en",
            "p": "es", "z": "en",
        }
        # Edge-TTS: "tr-TR-Ahmet..." → tr
        if voice_id.startswith("tr-"):
            return PREVIEW_TEXT["tr"]
        if voice_id.startswith("en-"):
            return PREVIEW_TEXT["en"]
        if voice_id.startswith("ja-") or first == "j":
            return PREVIEW_TEXT["ja"]
        if first == "e":
            return PREVIEW_TEXT["es"]
        if first == "f":
            return PREVIEW_TEXT["fr"]
        return PREVIEW_TEXT["en"]

    def _do_preview(self) -> None:
        """Önizleme sesini sentezle ve oynat."""
        voice_id = self._voice_combo.currentData()
        if not voice_id:
            return

        if self._worker and self._worker.isRunning():
            return

        self._player.stop()
        self._btn_preview.setEnabled(False)
        self._progress.setVisible(True)
        self._lbl_status.setText("Sentezleniyor...")

        text = self._get_preview_text(voice_id)

        # Geçici dosya
        tmp = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False, prefix="recap_preview_"
        )
        self._tmp_path = tmp.name
        tmp.close()

        self._worker = _SynthWorker(
            engine_name=self._engine_name,
            voice_id=voice_id,
            text=text,
            out_path=self._tmp_path,
            params=self._params,
        )
        self._worker.done.connect(self._on_synth_done)
        self._worker.error.connect(self._on_synth_error)
        self._worker.start()

    def _on_synth_done(self, path: str) -> None:
        self._progress.setVisible(False)
        self._btn_preview.setEnabled(True)
        self._lbl_status.setText("<span style='color:#22c55e;'><b>Hazır</b> — oynatılıyor...</span>")
        self._player.load(path)
        self._player.play()

    def _on_synth_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_preview.setEnabled(True)
        self._lbl_status.setText(f"<span style='color:#ef4444;'><b>Hata:</b> {msg[:80]}</span>")
        self._lbl_status.setStyleSheet("color: #f7768e;")

    def _select_voice(self) -> None:
        voice_id = self._voice_combo.currentData()
        if voice_id:
            self.voice_selected.emit(voice_id)
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        # Geçici dosyayı temizle
        if self._tmp_path:
            try:
                Path(self._tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
        super().closeEvent(event)