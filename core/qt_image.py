"""NumPy RGB → bağımsız QImage (geçici buffer serbest kalınca çökmesin)."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def numpy_rgb_to_qimage(rgb: np.ndarray):
    from PyQt6.QtGui import QImage

    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    ch = 1 if rgb.ndim == 2 else rgb.shape[2]
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
    return qimg.copy()


def load_pixmap(path: str | Path):
    """PNG iCCP uyarılarını tetiklemeden QPixmap yükler.

    Scanlation PNG'lerindeki bozuk ICC profilleri Qt'nin libpng'sini
    her thumbnail'de 'known incorrect sRGB profile' basmaya zorlar.
    Pillow RGB'ye çevirip QImage üzerinden yüklemek bunu keser.
    """
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap()
    if not path:
        return pixmap
    src = Path(path)
    if not src.exists() or not src.is_file():
        return pixmap
    try:
        from PIL import Image
        from PIL.ImageQt import ImageQt

        with Image.open(src) as img:
            converted = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            qimg = ImageQt(converted).copy()
        pixmap = QPixmap.fromImage(qimg)
        if not pixmap.isNull():
            return pixmap
    except Exception:
        pass
    pixmap.load(str(src))
    return pixmap
