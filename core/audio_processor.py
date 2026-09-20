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


def remove_silence(
    audio_path: str,
    silence_thresh: float = -40.0,
    min_silence_len: int = 400,
    keep_silence: int = 120,
) -> float:
    """
    Ses dosyasındaki (başta, sonda ve konuşma İÇİNDEKİ) sessiz boşlukları
    kısaltır. Tamamen kesmez; her boşluktan `keep_silence` ms kadar bırakır,
    böylece seslendirme doğal durur ama uzun sessiz aralıklar (TTS motorunun
    bıraktığı boşluklar, tekrarlı satır araları vb.) videoyu şişirmez.

    Dosyanın üzerine yazar (in-place). Yeni süreyi (saniye) döner;
    hata durumunda veya değişiklik yapılmadıysa mevcut süreyi döner.

    Args:
        audio_path:      Ses dosyası yolu.
        silence_thresh:  Sessizlik eşiği (dBFS). Bu değerden düşük ses seviyesi
                          "sessiz" sayılır.
        min_silence_len: Bir aralığın "sessizlik" sayılması için gereken
                          minimum süre (ms). Kısa duraklamalar dokunulmadan kalır.
        keep_silence:     Her sessiz aralıktan korunacak süre (ms) — kelimeler
                          birbirine yapışmasın diye küçük bir boşluk bırakılır.
    """
    AudioSegment = _load_pydub()
    try:
        from pydub.silence import detect_nonsilent

        seg = AudioSegment.from_file(audio_path)
        if len(seg) == 0:
            return 0.0

        # Eşik yüksekse (ör. -32) cümle kuyruğu "sessiz" sayılır ve kelime kesilir.
        thresh = min(float(silence_thresh), -38.0)
        min_len = max(int(min_silence_len), 550)
        keep = max(int(keep_silence), 180)
        pad_end = max(keep, 280)

        nonsilent_ranges = detect_nonsilent(
            seg, min_silence_len=min_len, silence_thresh=thresh
        )
        if not nonsilent_ranges:
            # Tamamı sessiz — dokunma, orijinal süreyi döndür.
            return len(seg) / 1000.0

        merged: List[tuple] = []
        for start, end in nonsilent_ranges:
            piece_start = max(0, start - keep)
            piece_end = min(len(seg), end + pad_end)
            if merged and piece_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], piece_end))
            else:
                merged.append((piece_start, piece_end))

        result = seg[merged[0][0]:merged[0][1]]
        for piece_start, piece_end in merged[1:]:
            result += seg[piece_start:piece_end]

        if len(result) >= len(seg) * 0.97:
            return len(seg) / 1000.0

        suffix = Path(audio_path).suffix.lower().lstrip(".")
        fmt = suffix if suffix in ("mp3", "wav", "ogg", "flac") else "mp3"
        result.export(audio_path, format=fmt)
        logger.debug(
            "Sessizlik kaldırıldı: %s (%.2fs -> %.2fs)",
            audio_path, len(seg) / 1000.0, len(result) / 1000.0,
        )
        return len(result) / 1000.0
    except Exception as exc:
        logger.error("remove_silence hatası (%s): %s", audio_path, exc)
        try:
            return get_duration(audio_path)
        except Exception:
            return 0.0


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