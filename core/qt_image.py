"""NumPy RGB → bağımsız QImage (geçici buffer serbest kalınca çökmesin)."""

from __future__ import annotations

import numpy as np


def numpy_rgb_to_qimage(rgb: np.ndarray):
    from PyQt6.QtGui import QImage

    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    ch = 1 if rgb.ndim == 2 else rgb.shape[2]
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
    return qimg.copy()
