"""
RecapAI - Altyazı üreteci (SRT ve ASS formatları).
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def _seconds_to_srt_ts(seconds: float) -> str:
    """Saniyeyi SRT zaman damgası formatına çevirir: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _seconds_to_ass_ts(seconds: float) -> str:
    """Saniyeyi ASS zaman damgası formatına çevirir: H:MM:SS.cc"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _estimate_duration(text: str, wpm: int = 150) -> float:
    """Kelime sayısına göre okuma süresi tahmin eder."""
    words = len(text.split())
    return max(1.0, words / wpm * 60)


def generate_srt(chapter, output_path: str) -> str:
    """
    Bölümün segmentlerinden SRT altyazı dosyası üretir.

    Args:
        chapter: Chapter nesnesi (segments listesi içermeli)
        output_path: Çıktı .srt dosyasının yolu

    Returns:
        Oluşturulan dosyanın yolu
    """
    segments = chapter.segments
    if not segments:
        logger.warning("SRT üretimi: segment bulunamadı.")
        Path(output_path).write_text("", encoding="utf-8")
        return output_path

    lines: List[str] = []
    cursor = 0.0
    srt_index = 0

    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue

        # Gerçek ses süresi varsa kullan, yoksa tahmin et
        duration = seg.duration if seg.duration > 0 else _estimate_duration(text)

        start_ts = _seconds_to_srt_ts(cursor)
        end_ts = _seconds_to_srt_ts(cursor + duration)
        srt_index += 1

        lines.append(str(srt_index))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")

        cursor += duration

    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    logger.info("SRT oluşturuldu: %s (%d segment)", output_path, len(segments))
    return output_path


def generate_ass(chapter, output_path: str, style: Optional[Dict[str, Any]] = None) -> str:
    """
    Bölümün segmentlerinden ASS altyazı dosyası üretir.
    ASS formatı daha fazla stillendirme imkanı sunar.

    Args:
        chapter: Chapter nesnesi
        output_path: Çıktı .ass dosyasının yolu
        style: Altyazı stil sözlüğü (font, size, color, stroke_color, stroke_width, position)

    Returns:
        Oluşturulan dosyanın yolu
    """
    if style is None:
        style = {}

    font = style.get("font", "Arial")
    size = style.get("size", 48)
    color_name = style.get("color", "white")
    stroke_color_name = style.get("stroke_color", "black")
    stroke_width = style.get("stroke_width", 2)
    position = style.get("position", "bottom")  # top | middle | bottom

    # Renk adını ASS BGR hex'e çevir (ASS formatı &H00BBGGRR)
    color_map = {
        "white": "&H00FFFFFF",
        "black": "&H00000000",
        "yellow": "&H0000FFFF",
        "red": "&H000000FF",
        "blue": "&H00FF0000",
        "green": "&H0000FF00",
        "cyan": "&H00FFFF00",
        "magenta": "&H00FF00FF",
    }
    def _hex_to_ass(hex_color: str) -> str:
        """#RRGGBB / #AARRGGBB veya renk adını ASS &HAABBGGRR formatına çevirir."""
        if not isinstance(hex_color, str):
            return "&H00FFFFFF"
        hx = hex_color.strip()
        if hx.startswith("#"):
            body = hx[1:]
            try:
                if len(body) == 6:
                    r = int(body[0:2], 16)
                    g = int(body[2:4], 16)
                    b = int(body[4:6], 16)
                    return f"&H00{b:02X}{g:02X}{r:02X}"
                if len(body) == 8:
                    # Qt HexArgb: AARRGGBB
                    a = int(body[0:2], 16)
                    r = int(body[2:4], 16)
                    g = int(body[4:6], 16)
                    b = int(body[6:8], 16)
                    # ASS alpha ters: 00=opak, FF=şeffaf
                    ass_a = 255 - a
                    return f"&H{ass_a:02X}{b:02X}{g:02X}{r:02X}"
            except ValueError:
                pass
        return color_map.get(hx.lower(), "&H00FFFFFF")

    primary_color = _hex_to_ass(color_name)
    outline_color = _hex_to_ass(stroke_color_name)

    # Hizalama: 1=alt sol, 2=alt orta, 3=alt sag, 4=orta sol, 5=orta orta, 6=orta sag
    #            7=ust sol, 8=ust orta, 9=ust sag
    alignment_map = {"bottom": 2, "middle": 5, "top": 8}
    alignment = alignment_map.get(position, 2)

    margin_v = 30

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary_color},&H000000FF,{outline_color},&H00000000,0,0,0,0,100,100,0,0,1,{stroke_width},0,{alignment},30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    segments = chapter.segments
    event_lines: List[str] = []
    cursor = 0.0

    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue

        duration = seg.duration if seg.duration > 0 else _estimate_duration(text)
        start_ts = _seconds_to_ass_ts(cursor)
        end_ts = _seconds_to_ass_ts(cursor + duration)

        # ASS'de satır sonları {\N} ile yapılır
        ass_text = text.replace("\n", r"{\N}")
        event_lines.append(
            f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{ass_text}"
        )
        cursor += duration

    content = header + "\n".join(event_lines)
    Path(output_path).write_text(content, encoding="utf-8")
    logger.info("ASS oluşturuldu: %s (%d segment)", output_path, len(segments))
    return output_path


def generate_srt_from_segments_with_offsets(
    segments: List[Any],
    output_path: str,
    time_offsets: Optional[List[float]] = None,
) -> str:
    """
    Zaman ofsetleri verilen segment listesinden SRT üretir.

    Args:
        segments: SegmentData listesi
        output_path: Çıktı dosyası
        time_offsets: Her segment için başlangıç zamanı (saniye). None ise kümülatif hesaplanır.

    Returns:
        Oluşturulan dosyanın yolu
    """
    lines: List[str] = []
    cursor = 0.0

    for i, seg in enumerate(segments, start=1):
        text = (seg.text or "").strip()
        if not text:
            continue

        if time_offsets and i - 1 < len(time_offsets):
            start = time_offsets[i - 1]
        else:
            start = cursor

        duration = seg.duration if seg.duration > 0 else _estimate_duration(text)
        end = start + duration

        lines.append(str(i))
        lines.append(f"{_seconds_to_srt_ts(start)} --> {_seconds_to_srt_ts(end)}")
        lines.append(text)
        lines.append("")

        cursor = end

    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    return output_path