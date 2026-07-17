"""
RecapAI - Ses işleme yardımcıları.
pydub tabanlı: normalize, trim, merge, convert.
"""

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def _load_pydub():
    """pydub'ı geç yükle; ImportError'u anlamlı mesajla yeniden fırlat."""
    try:
        from pydub import AudioSegment
        return AudioSegment
    except ImportError:
        raise ImportError(
            "pydub kurulu değil. Lütfen 'pip install pydub' çalıştırın. "
            "FFmpeg da sistem PATH'inde olmalıdır."
        )


def get_duration(audio_path: str) -> float:
    """Ses dosyasının süresini saniye cinsinden döner."""
    AudioSegment = _load_pydub()
    try:
        seg = AudioSegment.from_file(audio_path)
        return len(seg) / 1000.0
    except Exception as exc:
        logger.error("get_duration hatası (%s): %s", audio_path, exc)
        return 0.0


def normalize_volume(audio_path: str, target_dbfs: float = -20.0) -> None:
    """
    Ses dosyasını hedef dBFS seviyesine normalize eder.
    Dosyanın üzerine yazar (in-place).
    """
    import math

    AudioSegment = _load_pydub()
    try:
        seg = AudioSegment.from_file(audio_path)
        # Sessiz / boş ses dosyalarında dBFS -inf olur; gain uygulanamaz
        if seg.dBFS == float("-inf") or math.isinf(seg.dBFS) or math.isnan(seg.dBFS):
            logger.warning("normalize_volume atlandı (sessiz dosya): %s", audio_path)
            return
        delta = target_dbfs - seg.dBFS
        normalized = seg.apply_gain(delta)
        suffix = Path(audio_path).suffix.lower().lstrip(".")
        fmt = suffix if suffix in ("mp3", "wav", "ogg", "flac") else "mp3"
        normalized.export(audio_path, format=fmt)
        logger.debug("Normalize edildi: %s (%.1f dBFS)", audio_path, target_dbfs)
    except Exception as exc:
        logger.error("normalize_volume hatası (%s): %s", audio_path, exc)


def trim_silence(audio_path: str, silence_thresh: float = -40.0) -> None:
    """
    Ses dosyasının başındaki ve sonundaki sessizliği keser.
    Dosyanın üzerine yazar (in-place).
    """
    AudioSegment = _load_pydub()
    try:
        from pydub.silence import detect_leading_silence

        seg = AudioSegment.from_file(audio_path)

        # Baştaki sessizlik
        start_trim = detect_leading_silence(seg, silence_threshold=silence_thresh)
        # Sondaki sessizlik (ters çevrip aynı işlemi uygula)
        end_trim = detect_leading_silence(seg.reverse(), silence_threshold=silence_thresh)

        end_pos = len(seg) - end_trim
        if start_trim < end_pos:
            trimmed = seg[start_trim:end_pos]
        else:
            trimmed = seg  # boş çıkmasın

        suffix = Path(audio_path).suffix.lower().lstrip(".")
        fmt = suffix if suffix in ("mp3", "wav", "ogg", "flac") else "mp3"
        trimmed.export(audio_path, format=fmt)
        logger.debug("Sessizlik kesildi: %s", audio_path)
    except Exception as exc:
        logger.error("trim_silence hatası (%s): %s", audio_path, exc)


def merge_audios(
    paths: List[str],
    output_path: str,
    silence_between: float = 0.3,
) -> float:
    """
    Birden fazla ses dosyasını birleştirir.

    Args:
        paths: Birleştirilecek ses dosyalarının yolları.
        output_path: Çıktı dosyası yolu.
        silence_between: Segmentler arasındaki sessizlik (saniye).

    Returns:
        Toplam süre (saniye).
    """
    AudioSegment = _load_pydub()
    try:
        gap = AudioSegment.silent(duration=int(silence_between * 1000))
        combined = AudioSegment.empty()
        for i, p in enumerate(paths):
            if not Path(p).exists():
                logger.warning("merge_audios: dosya yok, atlanıyor: %s", p)
                continue
            seg = AudioSegment.from_file(p)
            if i > 0:
                combined += gap
            combined += seg

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        suffix = Path(output_path).suffix.lower().lstrip(".")
        fmt = suffix if suffix in ("mp3", "wav", "ogg", "flac") else "mp3"
        combined.export(output_path, format=fmt)
        total_sec = len(combined) / 1000.0
        logger.info("merge_audios: %d dosya birleştirildi → %s (%.1fs)", len(paths), output_path, total_sec)
        return total_sec
    except Exception as exc:
        logger.error("merge_audios hatası: %s", exc)
        return 0.0


def convert_to_mp3(wav_path: str, mp3_path: str, bitrate: str = "192k") -> bool:
    """
    WAV dosyasını MP3'e dönüştürür (Kokoro çıktısı için).

    Returns:
        True başarılıysa, False değilse.
    """
    AudioSegment = _load_pydub()
    try:
        seg = AudioSegment.from_wav(wav_path)
        Path(mp3_path).parent.mkdir(parents=True, exist_ok=True)
        seg.export(mp3_path, format="mp3", bitrate=bitrate)
        logger.debug("WAV→MP3: %s → %s", wav_path, mp3_path)
        return True
    except Exception as exc:
        logger.error("convert_to_mp3 hatası (%s): %s", wav_path, exc)
        return False