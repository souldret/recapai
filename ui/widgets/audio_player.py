"""
RecapAI - Ses oynatici widget.
Backend sirasi: QMediaPlayer > pygame > (hicbiri).
"""

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSlider, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon

from ui.utils.icons import Icons, ICON_COLOR, ICON_COLOR_MUTED

logger = logging.getLogger(__name__)


def _init_qt_backend():
    """QMediaPlayer + QAudioOutput baslatmayi dene."""
    try:
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
        player = QMediaPlayer()
        # isAvailable() PyQt6'da yok; kaynak yuklemeyi deneyerek kontrol et
        audio_out = QAudioOutput()
        player.setAudioOutput(audio_out)
        audio_out.setVolume(0.8)
        return player, audio_out
    except Exception as e:
        logger.warning("QMediaPlayer baslatılamadi: %s", e)
        return None, None


def _init_pygame_backend():
    """pygame.mixer baslatmayi dene."""
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        logger.info("AudioPlayer: pygame backend aktif")
        return True
    except Exception as e:
        logger.warning("pygame baslatılamadi: %s", e)
        return False


class AudioPlayerWidget(QWidget):
    """
    Kompakt ses oynatici.
    Backend: QMediaPlayer → pygame → none (uyari goster)

    Sinyaller:
        playback_finished   — oynatma bitti
        position_changed(ms) — konum degisti (ms)
    """

    playback_finished = pyqtSignal()
    position_changed  = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        # Backend belirleme
        self._backend = "none"
        self._qt_player = None
        self._qt_audio  = None
        self._pygame_path: Optional[str] = None

        qt_player, qt_audio = _init_qt_backend()
        if qt_player is not None:
            self._qt_player = qt_player
            self._qt_audio  = qt_audio
            self._backend   = "qt"
        elif _init_pygame_backend():
            self._backend = "pygame"
        else:
            logger.warning("AudioPlayer: hicbir backend bulunamadi")

        self._duration_ms: int = 0
        self._seeking     = False
        self._pygame_timer: Optional[QTimer] = None

        self._build_ui()
        self._connect_signals()

        logger.debug("AudioPlayerWidget olusturuldu (backend=%s)", self._backend)

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # Kontrol satiri
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        self._btn_play  = self._icon_btn(Icons.PLAY,  "Oynat",    48)
        self._btn_pause = self._icon_btn(Icons.PAUSE, "Durakla",  48)
        self._btn_stop  = self._icon_btn(Icons.STOP,  "Durdur",   48)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)

        ctrl_row.addWidget(self._btn_play)
        ctrl_row.addWidget(self._btn_pause)
        ctrl_row.addWidget(self._btn_stop)
        ctrl_row.addSpacing(8)

        self._lbl_time = QLabel("00:00 / 00:00")
        self._lbl_time.setObjectName("mutedLabel")
        self._lbl_time.setFixedWidth(110)
        ctrl_row.addWidget(self._lbl_time)
        ctrl_row.addSpacing(8)

        # Volume
        vol_icon = QLabel()
        vol_icon.setPixmap(Icons.pixmap(Icons.VOLUME, size=16, color=ICON_COLOR_MUTED))
        vol_icon.setFixedWidth(20)
        ctrl_row.addWidget(vol_icon)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(80)
        self._vol_slider.setToolTip("Ses seviyesi")
        ctrl_row.addWidget(self._vol_slider)

        # Backend uyarisi
        if self._backend == "none":
            warn_lbl = QLabel("Ses destegi yok")
            warn_lbl.setObjectName("errorLabel")
            ctrl_row.addWidget(warn_lbl)

        ctrl_row.addStretch()
        root.addLayout(ctrl_row)

        # Ilerleme slider'i
        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 1000)
        self._seek_slider.setValue(0)
        self._seek_slider.setToolTip("Konuma git")
        root.addWidget(self._seek_slider)

    def _icon_btn(self, icon_name: str, tooltip: str, size: int) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(size, size)
        btn.setToolTip(tooltip)
        btn.setObjectName("secondaryBtn")
        icon = Icons.get(icon_name)
        if not icon.isNull():
            btn.setIcon(icon)
        else:
            # Fallback metin
            fallback = {"Oynat": "▶", "Durakla": "⏸", "Durdur": "⏹"}
            btn.setText(fallback.get(tooltip, "?"))
            font = QFont()
            font.setPointSize(12)
            btn.setFont(font)
        return btn

    # ── Sinyal baglantilari ───────────────────────────────────────

    def _connect_signals(self) -> None:
        self._btn_play.clicked.connect(self.play)
        self._btn_pause.clicked.connect(self.pause)
        self._btn_stop.clicked.connect(self.stop)

        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        self._seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)

        if self._backend == "qt" and self._qt_player:
            self._qt_player.playbackStateChanged.connect(self._on_state_changed)
            self._qt_player.durationChanged.connect(self._on_duration_changed)
            self._qt_player.positionChanged.connect(self._on_position_changed)
            self._qt_player.mediaStatusChanged.connect(self._on_media_status)

    # ── Public API ────────────────────────────────────────────────

    def load(self, path: str) -> None:
        """Ses dosyasini yukle."""
        self.stop()
        if self._backend == "qt" and self._qt_player:
            self._qt_player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
            self._btn_play.setEnabled(True)
        elif self._backend == "pygame":
            self._pygame_path = str(Path(path).resolve())
            self._btn_play.setEnabled(True)
        logger.debug("AudioPlayer: yuklendi %s (backend=%s)", path, self._backend)

    def play(self) -> None:
        """Oynat / devam et."""
        if self._backend == "qt":
            if self._qt_player and not self._qt_player.source().isEmpty():
                self._qt_player.play()
                self._btn_play.setEnabled(False)
                self._btn_pause.setEnabled(True)
                self._btn_stop.setEnabled(True)
        elif self._backend == "pygame":
            if self._pygame_path:
                try:
                    import pygame
                    pygame.mixer.music.load(self._pygame_path)
                    pygame.mixer.music.play()
                    self._btn_play.setEnabled(False)
                    self._btn_pause.setEnabled(True)
                    self._btn_stop.setEnabled(True)
                    self._start_pygame_timer()
                except Exception as e:
                    logger.error("pygame oynatma hatasi: %s", e)
        else:
            QMessageBox.warning(
                self, "Ses Destegi Yok",
                "Ses oynatma destegi bulunamadi.\n"
                "Cozum: pip install pygame==2.5.2"
            )

    def pause(self) -> None:
        if self._backend == "qt" and self._qt_player:
            self._qt_player.pause()
            self._btn_play.setEnabled(True)
            self._btn_pause.setEnabled(False)
        elif self._backend == "pygame":
            try:
                import pygame
                pygame.mixer.music.pause()
                self._btn_play.setEnabled(True)
                self._btn_pause.setEnabled(False)
            except Exception as e:
                logger.error("pygame pause hatasi: %s", e)

    def stop(self) -> None:
        if self._backend == "qt" and self._qt_player:
            self._qt_player.stop()
        elif self._backend == "pygame":
            try:
                import pygame
                pygame.mixer.music.stop()
                self._stop_pygame_timer()
            except Exception:
                pass

        self._seek_slider.setValue(0)
        self._lbl_time.setText("00:00 / 00:00")
        self._btn_play.setEnabled(True)
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)

    def set_volume(self, value: int) -> None:
        self._vol_slider.setValue(max(0, min(100, value)))

    # ── Volume ────────────────────────────────────────────────────

    def _on_volume_changed(self, value: int) -> None:
        vol = value / 100.0
        if self._backend == "qt" and self._qt_audio:
            self._qt_audio.setVolume(vol)
        elif self._backend == "pygame":
            try:
                import pygame
                pygame.mixer.music.set_volume(vol)
            except Exception:
                pass

    # ── QMediaPlayer sinyalleri ───────────────────────────────────

    def _on_state_changed(self, state) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._btn_play.setEnabled(True)
            self._btn_pause.setEnabled(False)
            self._btn_stop.setEnabled(False)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms
        self._update_time_label(self._qt_player.position() if self._qt_player else 0, duration_ms)

    def _on_position_changed(self, pos_ms: int) -> None:
        if not self._seeking and self._duration_ms > 0:
            ratio = int(pos_ms / self._duration_ms * 1000)
            self._seek_slider.blockSignals(True)
            self._seek_slider.setValue(ratio)
            self._seek_slider.blockSignals(False)
        self._update_time_label(pos_ms, self._duration_ms)
        self.position_changed.emit(pos_ms)

    def _on_media_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop()
            self.playback_finished.emit()

    # ── Pygame timer ──────────────────────────────────────────────

    def _start_pygame_timer(self) -> None:
        if self._pygame_timer is None:
            self._pygame_timer = QTimer(self)
            self._pygame_timer.setInterval(500)
            self._pygame_timer.timeout.connect(self._check_pygame_status)
        self._pygame_timer.start()

    def _stop_pygame_timer(self) -> None:
        if self._pygame_timer:
            self._pygame_timer.stop()

    def _check_pygame_status(self) -> None:
        try:
            import pygame
            if not pygame.mixer.music.get_busy():
                self._stop_pygame_timer()
                self._btn_play.setEnabled(True)
                self._btn_pause.setEnabled(False)
                self._btn_stop.setEnabled(False)
                self.playback_finished.emit()
        except Exception:
            self._stop_pygame_timer()

    # ── Seek ──────────────────────────────────────────────────────

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        if self._backend == "qt" and self._qt_player and self._duration_ms > 0:
            target = int(self._seek_slider.value() / 1000 * self._duration_ms)
            self._qt_player.setPosition(target)
        self._seeking = False

    # ── Yardimcilar ───────────────────────────────────────────────

    def _update_time_label(self, pos_ms: int, duration_ms: int) -> None:
        cur   = self._ms_to_str(pos_ms)
        total = self._ms_to_str(duration_ms)
        self._lbl_time.setText(f"{cur} / {total}")

    @staticmethod
    def _ms_to_str(ms: int) -> str:
        total_sec = max(0, ms) // 1000
        m, s = divmod(total_sec, 60)
        return f"{m:02d}:{s:02d}"
