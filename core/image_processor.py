"""
RecapAI - Görsel işleme yardımcıları.
"""

import logging
import re
from pathlib import Path
from typing import List, Dict, Optional

from PIL import Image
import warnings

# Pillow varsayılanı ~89.5 MP; manhwa stitch için yükselt ama sınırsız bırakma.
Image.MAX_IMAGE_PIXELS = 178_956_970 * 2
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
THUMBNAIL_SIZE = (250, 350)


# ── Dosya Listeleme ────────────────────────────────────────────────────────────

def natural_key(text: str) -> List:
    """Doğal sıralama için sayı parçalarını ayırır."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def load_images_from_folder(folder: str | Path) -> List[str]:
    """
    Klasördeki desteklenen görselleri natural sort ile döndürür.

    Args:
        folder: Taranacak klasör yolu.

    Returns:
        Sıralı dosya yolları listesi.
    """
    folder = Path(folder)
    if not folder.is_dir():
        logger.warning("Geçersiz klasör: %s", folder)
        return []

    files = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_FORMATS
    ]
    files.sort(key=lambda f: natural_key(f.name))
    logger.debug("%d görsel bulundu: %s", len(files), folder)
    return [str(f) for f in files]


# ── Thumbnail ──────────────────────────────────────────────────────────────────

def create_thumbnail(
    path: str | Path,
    output_path: str | Path,
    size: int = 250,
) -> bool:
    """
    Görselin thumbnail'ini oluşturur (aspect ratio korunur).

    Args:
        path: Kaynak görsel yolu.
        output_path: Kaydedilecek thumbnail yolu.
        size: Uzun kenarın maksimum piksel boyutu.

    Returns:
        Başarılıysa True.
    """
    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(path) as img:
            img = img.convert("RGB")
            thumb_size = (size, int(size * 1.4))
            img.thumbnail(thumb_size, Image.Resampling.LANCZOS)
            img.save(output_path, "JPEG", quality=85, optimize=True)

        logger.debug("Thumbnail oluşturuldu: %s", output_path)
        return True
    except Exception as exc:
        logger.error("Thumbnail hatası (%s): %s", path, exc)
        return False


def create_thumbnail_batch(
    paths: List[str],
    output_dir: str | Path,
    size: int = 250,
) -> Dict[str, Optional[str]]:
    """
    Birden fazla görsel için thumbnail oluşturur.

    Returns:
        {kaynak_yol: thumbnail_yol | None}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[str, Optional[str]] = {}

    for src in paths:
        src_path = Path(src)
        thumb_path = output_dir / f"{src_path.stem}_thumb.jpg"
        success = create_thumbnail(src_path, thumb_path, size)
        results[src] = str(thumb_path) if success else None

    return results


# ── Görsel Bilgisi ─────────────────────────────────────────────────────────────

def get_image_info(path: str | Path) -> Dict:
    """
    Görsel hakkında temel meta bilgi döndürür.

    Returns:
        {"width": int, "height": int, "size_kb": float,
         "format": str, "filename": str}
    """
    path = Path(path)
    try:
        stat_size = path.stat().st_size / 1024
        with Image.open(path) as img:
            return {
                "width": img.width,
                "height": img.height,
                "size_kb": round(stat_size, 1),
                "format": img.format or path.suffix.upper().lstrip("."),
                "filename": path.name,
            }
    except Exception as exc:
        logger.error("Görsel bilgisi alınamadı (%s): %s", path, exc)
        return {
            "width": 0, "height": 0,
            "size_kb": 0.0, "format": "?",
            "filename": path.name,
        }


def is_valid_image(path: str | Path) -> bool:
    """Dosyanın geçerli bir görsel olup olmadığını kontrol eder."""
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False