"""
RecapAI - Dışa aktarma fonksiyonları (TXT, SRT).
"""

import logging
import re
from pathlib import Path

from core.models import Chapter

logger = logging.getLogger(__name__)


def audio_export_stem(chapter_name: str, index: int) -> str:
    """Kısa dışa aktarma adı: 'Bölüm 35' + 1 -> '35_001'."""
    name = (chapter_name or "").strip()
    nums = re.findall(r"\d+", name)
    if nums:
        prefix = nums[-1]
    else:
        prefix = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")[:16]
        prefix = prefix or "s"
    return f"{prefix}_{index:03d}"


def _srt_timestamp(seconds: float) -> str:
    """Saniyeyi SRT zaman damgası formatına çevirir: HH:MM:SS,mmm"""
    from core.subtitle_generator import _seconds_to_srt_ts
    return _seconds_to_srt_ts(seconds)


def export_txt(chapter: Chapter, path: str) -> None:
    """
    Bölüm segmentlerini düz metin olarak dışa aktarır.

    Args:
        chapter: Kaynak bölüm.
        path: Hedef dosya yolu.

    Raises:
        ValueError: Segment yoksa.
        IOError: Yazma hatası.
    """
    if not chapter.segments:
        raise ValueError(f"'{chapter.name}' bölümünde script segmenti yok.")

    lines = [f"# {chapter.name}", ""]
    for i, seg in enumerate(chapter.segments):
        if seg.text.strip():
            lines.append(f"[{i + 1}] {seg.text.strip()}")
            lines.append("")

    content = "\n".join(lines)
    try:
        Path(path).write_text(content, encoding="utf-8")
        logger.info("TXT export: %s (%d segment)", path, len(chapter.segments))
    except IOError as exc:
        logger.error("TXT export hatası: %s", exc)
        raise


def export_srt(chapter: Chapter, path: str) -> None:
    """
    Bölüm segmentlerini SRT altyazı formatında dışa aktarır.
    Ses dosyası yoksa süre tahmini kullanır.

    Format:
        1
        00:00:00,000 --> 00:00:05,000
        Metin

    Args:
        chapter: Kaynak bölüm.
        path: Hedef .srt dosya yolu.

    Raises:
        ValueError: Segment yoksa.
        IOError: Yazma hatası.
    """
    if not chapter.segments:
        raise ValueError(f"'{chapter.name}' bölümünde script segmenti yok.")

    blocks = []
    cursor = 0.0
    srt_index = 0

    for seg in chapter.segments:
        text = seg.text.strip()
        if not text:
            continue

        # Süre: ses varsa ses dosyasından al, yoksa tahmin kullan
        duration = seg.duration if seg.duration > 0 else _estimate_duration(text)
        start = cursor
        end = cursor + duration
        cursor = end
        srt_index += 1

        blocks.append(
            f"{srt_index}\n"
            f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
            f"{text}\n"
        )

    content = "\n".join(blocks)
    try:
        Path(path).write_text(content, encoding="utf-8")
        logger.info("SRT export: %s (%d blok)", path, len(blocks))
    except IOError as exc:
        logger.error("SRT export hatası: %s", exc)
        raise


def _estimate_duration(text: str, wpm: int = 150) -> float:
    """Kelime sayısına göre tahmini süre (saniye)."""
    word_count = len(text.split())
    return max(2.0, round((word_count / wpm) * 60, 2))