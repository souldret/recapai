"""
RecapAI - Açılış sağlık kontrolü.
Ağ çağrısı yapmaz; API anahtarı, FFmpeg ve TTS paketinin varlığına bakar.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


def startup_checks() -> List[Tuple[str, bool, str]]:
    """(ad, tamam, kısa not) listesi. Eksikler üretimi durdurmaz, yalnızca görünür."""
    checks: List[Tuple[str, bool, str]] = []

    try:
        from core.settings_manager import SettingsManager
        has_key = bool(SettingsManager.instance().has_api_key())
    except Exception as exc:
        logger.debug("API kontrolü okunamadı: %s", exc)
        has_key = False
    checks.append((
        "API",
        has_key,
        "anahtar kayıtlı" if has_key else "Ayarlar'dan OpenRouter anahtarı gerekli",
    ))

    try:
        from core.ffmpeg_helper import check_ffmpeg
        ffmpeg_ok = bool(check_ffmpeg())
    except Exception as exc:
        logger.debug("FFmpeg kontrolü okunamadı: %s", exc)
        ffmpeg_ok = False
    checks.append((
        "FFmpeg",
        ffmpeg_ok,
        "hazır" if ffmpeg_ok else "render için PATH'te yok",
    ))

    edge_ok = False
    try:
        import edge_tts  # noqa: F401
        edge_ok = True
    except Exception as exc:
        logger.debug("edge-tts kontrolü okunamadı: %s", exc)
    checks.append((
        "TTS",
        edge_ok,
        "edge-tts kurulu" if edge_ok else "edge-tts kurulu değil",
    ))
    return checks
