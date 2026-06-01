"""
RecapAI - FFmpeg yardımcı fonksiyonları.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Bilinen FFmpeg kurulum yerleri (Windows)
_WINDOWS_PATHS = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
]

_cached_path: Optional[str] = None


def check_ffmpeg() -> bool:
    """FFmpeg'in sistemde mevcut olup olmadığını kontrol eder."""
    return get_ffmpeg_path() is not None


def get_ffmpeg_path() -> Optional[str]:
    """FFmpeg yürütülebilir dosyasının tam yolunu döndürür; bulunamazsa None."""
    global _cached_path
    if _cached_path and Path(_cached_path).exists():
        return _cached_path

    # 1) PATH üzerinden ara
    found = shutil.which("ffmpeg")
    if found:
        _cached_path = found
        logger.debug("FFmpeg bulundu (PATH): %s", found)
        return found

    # 2) Bilinen Windows yollarını dene
    if sys.platform == "win32":
        for p in _WINDOWS_PATHS:
            if Path(p).exists():
                _cached_path = p
                logger.debug("FFmpeg bulundu (sabit yol): %s", p)
                return p

    # 3) Ortam değişkeninden al
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and Path(env_path).exists():
        _cached_path = env_path
        logger.debug("FFmpeg bulundu (FFMPEG_PATH env): %s", env_path)
        return env_path

    logger.warning("FFmpeg bulunamadı.")
    return None


def get_ffprobe_path() -> Optional[str]:
    """FFprobe yürütülebilir dosyasının yolunu döndürür."""
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        probe = Path(ffmpeg).parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if probe.exists():
            return str(probe)
    return None


def get_media_duration(file_path: str) -> float:
    """FFprobe ile medya süresini saniye olarak döndürür."""
    probe = get_ffprobe_path()
    if not probe:
        return 0.0
    try:
        result = subprocess.run(
            [
                probe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(result.stdout.strip())
    except Exception as exc:
        logger.warning("FFprobe süre hatası (%s): %s", file_path, exc)
        return 0.0


def run_ffmpeg(
    args: List[str],
    progress_callback: Optional[Callable[[int, str], None]] = None,
    total_duration: float = 0.0,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> int:
    """
    FFmpeg komutunu çalıştırır.

    Args:
        args: ffmpeg'e gönderilecek argüman listesi (ffmpeg yolu dahil değil).
        progress_callback: (percent: int, eta: str) şeklinde çağrılır.
        total_duration: İlerleme hesabı için toplam saniye.
        cancel_check: True döndürürse işlem iptal edilir.

    Returns:
        Çıkış kodu (0 = başarı).
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(
            "FFmpeg bulunamadı. Lütfen FFmpeg'i yükleyin ve PATH'e ekleyin.\n"
            "İndirme: https://ffmpeg.org/download.html"
        )

    cmd = [ffmpeg] + args
    logger.debug("FFmpeg komutu: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stderr_lines: List[str] = []
    duration_re = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
    time_re = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
    speed_re = re.compile(r"speed=\s*([\d.]+)x")

    detected_duration = total_duration

    try:
        for line in process.stderr:
            line = line.strip()
            stderr_lines.append(line)

            if cancel_check and cancel_check():
                process.kill()
                logger.info("FFmpeg işlemi iptal edildi.")
                return -1

            # Toplam süreyi tespit et
            if not detected_duration:
                m = duration_re.search(line)
                if m:
                    h, mn, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                    detected_duration = h * 3600 + mn * 60 + s

            # İlerleme hesapla
            if progress_callback and detected_duration:
                tm = time_re.search(line)
                if tm:
                    h, mn, s = float(tm.group(1)), float(tm.group(2)), float(tm.group(3))
                    current = h * 3600 + mn * 60 + s
                    percent = min(int(current / detected_duration * 100), 99)

                    eta_str = "—"
                    sm = speed_re.search(line)
                    if sm:
                        speed = float(sm.group(1))
                        if speed > 0:
                            remaining = (detected_duration - current) / speed
                            eta_str = _format_eta(remaining)

                    progress_callback(percent, eta_str)

    except Exception as exc:
        logger.error("FFmpeg stderr okuma hatası: %s", exc)

    process.wait()
    ret = process.returncode

    if ret != 0:
        err_tail = "\n".join(stderr_lines[-20:])
        logger.error("FFmpeg hata (kod %d):\n%s", ret, err_tail)
    else:
        if progress_callback:
            progress_callback(100, "0s")
        logger.debug("FFmpeg başarıyla tamamlandı.")

    return ret


def _format_eta(seconds: float) -> str:
    """Saniyeyi okunabilir ETA formatına çevirir."""
    if seconds <= 0:
        return "0s"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def concat_videos(input_paths: List[str], output_path: str, codec: str = "libx264") -> int:
    """
    Birden fazla video dosyasını birleştirir (concat demuxer).
    Tüm giriş dosyaları aynı codec/çözünürlükte olmalı.
    """
    # Geçici concat listesi dosyası
    list_file = Path(output_path).with_suffix(".concat.txt")
    try:
        lines = [f"file '{p}'\n" for p in input_paths]
        list_file.write_text("".join(lines), encoding="utf-8")

        args = [
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output_path),
        ]
        return run_ffmpeg(args)
    finally:
        if list_file.exists():
            list_file.unlink()