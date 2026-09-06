"""
RecapAI - Panel İşleme Worker'ları
Stitch ve panel tespiti işlemlerini arka planda yürütür.
"""

import logging
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

# Panel koordinatı tipi: (x, y, w, h)
PanelBox = Tuple[int, int, int, int]


class ImageLoadWorker(QThread):
    """
    Görüntüyü arka planda yükler (UI thread'ini bloke etmez).
    Büyük manga sayfaları veya stitched webtoon görselleri için kullanılır.
    """

    finished = pyqtSignal(str, object)   # (image_path, np.ndarray)
    error = pyqtSignal(str)              # hata mesajı

    def __init__(self, image_path: str, parent=None) -> None:
        super().__init__(parent)
        self._path = image_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            import logging as _logging
            import cv2
            import numpy as np
            from pathlib import Path
            from PIL import Image as PILImage
            import warnings
            PILImage.MAX_IMAGE_PIXELS = None
            warnings.filterwarnings("ignore", category=PILImage.DecompressionBombWarning)
            _logging.getLogger("PIL").setLevel(_logging.WARNING)

            p = Path(self._path)
            # Pillow kullan: OpenCV'nin ~65535 px yükseklik sınırı yoktur
            pil_img = PILImage.open(p).convert("RGB")
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            pil_img.close()

            if not self._cancelled:
                self.finished.emit(self._path, img)
        except Exception as exc:
            logger.exception("ImageLoadWorker hatası")
            if not self._cancelled:
                self.error.emit(str(exc))


class StitchWorker(QThread):
    """
    Webtoon: bölüm görsellerini dikey olarak birleştirir.
    """

    progress = pyqtSignal(str)          # durum mesajı
    finished = pyqtSignal(str)          # çıktı dosya yolu
    error = pyqtSignal(str)             # hata mesajı

    def __init__(
        self,
        image_paths: List[str],
        output_path: str,
        gap: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = image_paths
        self._output = output_path
        self._gap = gap
        self._cancelled = False

    def cancel(self) -> None:
        """Worker'ı iptal işaretler (stitch atomic olduğundan en kısa sürede durur)."""
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            from core.panel_detector import stitch_images_vertical
            self.progress.emit(f"{len(self._paths)} görsel birleştiriliyor...")
            out = stitch_images_vertical(self._paths, self._output, gap=self._gap)
            if not self._cancelled:
                self.finished.emit(out)
        except Exception as exc:
            logger.exception("StitchWorker hatası")
            if not self._cancelled:
                self.error.emit(str(exc))


class PanelDetectWorker(QThread):
    """
    Tek bir görsel üzerinde panel tespiti yapar.
    Manga (rtl) veya webtoon (ltr) okuma düzenini destekler.
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(list)         # List[PanelBox]
    error = pyqtSignal(str)

    def __init__(
        self,
        image_path: str,
        reading_order: str = "rtl",
        min_area_ratio: float = 0.02,
        max_area_ratio: float = 0.80,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = image_path
        self._order = reading_order
        self._min_area = min_area_ratio
        self._max_area = max_area_ratio
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            from core.panel_detector import PanelDetector
            self.progress.emit("Panel tespiti başlatıldı...")
            detector = PanelDetector(
                min_area_ratio=self._min_area,
                max_area_ratio=self._max_area,
            )
            boxes = detector.detect_from_path(self._path, reading_order=self._order)
            if self._cancelled:
                return
            self.progress.emit(f"{len(boxes)} panel tespit edildi.")
            self.finished.emit(boxes)
        except Exception as exc:
            logger.exception("PanelDetectWorker hatası")
            if not self._cancelled:
                self.error.emit(str(exc))


class PanelBatchDetectWorker(QThread):
    """
    Birden fazla görsel üzerinde sırayla panel tespiti yapar.
    Manga modunda "Tüm Sayfalarda Tespit Et" için kullanılır.

    Sinyaller:
        page_done(page_index, image_path, boxes)  — her sayfa tamamlandığında
        progress(done, total, message)             — ilerleme bilgisi
        finished(results)                          — tüm sayfalar bitti
        error(message)                             — hata
    """

    page_done  = pyqtSignal(int, str, list)   # (page_index, image_path, boxes)
    progress   = pyqtSignal(int, int, str)    # (done, total, message)
    finished   = pyqtSignal(dict)             # {image_path: [boxes...]}
    error      = pyqtSignal(str)

    def __init__(
        self,
        image_paths: List[str],
        reading_order: str = "rtl",
        min_area_ratio: float = 0.02,
        max_area_ratio: float = 0.80,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths       = image_paths
        self._order       = reading_order
        self._min_area    = min_area_ratio
        self._max_area    = max_area_ratio
        self._cancelled   = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from core.panel_detector import PanelDetector
        detector = PanelDetector(
            min_area_ratio=self._min_area,
            max_area_ratio=self._max_area,
        )
        results: Dict[str, List[PanelBox]] = {}
        total = len(self._paths)

        for i, path in enumerate(self._paths):
            if self._cancelled:
                return
            try:
                self.progress.emit(i, total, f"İşleniyor: {Path(path).name}  ({i+1}/{total})")
                boxes = detector.detect_from_path(path, reading_order=self._order)
                results[path] = boxes
                if not self._cancelled:
                    self.page_done.emit(i, path, boxes)
            except Exception as exc:
                logger.warning("Batch detect sayfa hatası %s: %s", path, exc)
                results[path] = []
                if not self._cancelled:
                    self.page_done.emit(i, path, [])

        if not self._cancelled:
            self.progress.emit(total, total, f"Tamamlandı — {total} sayfa işlendi.")
            self.finished.emit(results)


class StitchQualityWorker(QThread):
    """
    Webtoon stitch işlemini kalite/çözünürlük parametreleriyle yapar.

    Parametreler:
        scale_percent: 100 = orijinal, 75 = %75, 50 = %50
        jpeg_quality:  0–95; None ise PNG olarak kaydeder
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(str)   # çıktı yolu
    error    = pyqtSignal(str)

    def __init__(
        self,
        image_paths: List[str],
        output_path: str,
        scale_percent: int = 100,
        jpeg_quality: Optional[int] = None,
        gap: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths         = image_paths
        self._output        = output_path
        self._scale         = scale_percent
        self._jpeg_quality  = jpeg_quality
        self._gap           = gap
        self._cancelled     = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            from PIL import Image as PILImage
            from pathlib import Path as _Path
            import warnings
            PILImage.MAX_IMAGE_PIXELS = None
            warnings.filterwarnings("ignore", category=PILImage.DecompressionBombWarning)

            self.progress.emit(f"{len(self._paths)} görsel birleştiriliyor (kalite: {self._scale}%)...")

            # Minimum genişliği bul
            target_width: Optional[int] = None
            valid_paths: List[_Path] = []
            for p in self._paths:
                pp = _Path(p)
                try:
                    with PILImage.open(pp) as probe:
                        w = probe.size[0]
                    if target_width is None or w < target_width:
                        target_width = w
                    valid_paths.append(pp)
                except Exception as exc:
                    logger.warning("Stitch Quality: görsel atlandı %s: %s", p, exc)

            if not valid_paths or target_width is None:
                self.error.emit("Hiçbir görsel okunamadı.")
                return

            # Ölçekleme uygula
            if self._scale != 100:
                target_width = max(1, int(target_width * self._scale / 100))

            # Toplam yüksekliği hesapla
            total_height = 0
            heights: List[int] = []
            for pp in valid_paths:
                with PILImage.open(pp) as probe:
                    orig_w, orig_h = probe.size
                scaled_h = max(1, int(orig_h * target_width / orig_w))
                heights.append(scaled_h)
                total_height += scaled_h
            if self._gap > 0:
                total_height += self._gap * (len(valid_paths) - 1)

            # Tuval oluştur ve yapıştır
            canvas = PILImage.new("RGB", (target_width, total_height), (255, 255, 255))
            y_offset = 0
            for i, (pp, h) in enumerate(zip(valid_paths, heights)):
                if self._cancelled:
                    canvas.close()
                    return
                img = PILImage.open(pp).convert("RGB")
                img = img.resize((target_width, h), PILImage.LANCZOS)
                canvas.paste(img, (0, y_offset))
                img.close()
                y_offset += h
                if self._gap > 0 and i < len(valid_paths) - 1:
                    y_offset += self._gap
                self.progress.emit(f"Birleştiriliyor: {i+1}/{len(valid_paths)}...")

            # Kaydet
            out_path = _Path(self._output)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            if self._jpeg_quality is not None:
                # JPEG — yükseklik sınırı kontrolü
                if total_height > 65000:
                    out_path = out_path.with_suffix(".png")
                    canvas.save(str(out_path), "PNG")
                else:
                    out_path = out_path.with_suffix(".jpg")
                    canvas.save(str(out_path), "JPEG", quality=self._jpeg_quality, optimize=True)
            else:
                out_path = out_path.with_suffix(".png")
                canvas.save(str(out_path), "PNG")

            canvas.close()
            if not self._cancelled:
                self.finished.emit(str(out_path))
        except Exception as exc:
            logger.exception("StitchQualityWorker hatası")
            if not self._cancelled:
                self.error.emit(str(exc))


class ZipExportWorker(QThread):
    """
    Seçili bölümlerin panellerini bolum_01/panel_001.jpg yapısında ZIP'e aktarır.

    Sinyaller:
        progress(done, total, message)
        finished(zip_path)
        error(message)
    """

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(
        self,
        export_items: List[Dict[str, Any]],
        # Her öğe: {"chapter_name": str, "panels": [(image_path, boxes), ...]}
        output_zip: str,
        image_format: str = "jpg",
        jpeg_quality: int = 85,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._items       = export_items
        self._zip_path    = output_zip
        self._format      = image_format.lower().lstrip(".")
        self._quality     = jpeg_quality
        self._cancelled   = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        import zipfile
        import io
        from PIL import Image as PILImage

        try:
            # Format doğrulama: her item {"chapter_name": str, "panels": [(path, boxes)...]}
            for item in self._items:
                if "chapter_name" not in item or "panels" not in item:
                    self.error.emit(
                        "ZipExportWorker: geçersiz export_items formatı. "
                        "Her öğe 'chapter_name' ve 'panels' anahtarı içermelidir."
                    )
                    return
                for entry in item["panels"]:
                    if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
                        self.error.emit(
                            "ZipExportWorker: 'panels' listesi (image_path, boxes) "
                            "tuple çiftlerinden oluşmalıdır."
                        )
                        return

            total_panels = sum(
                sum(len(boxes) for _, boxes in item["panels"])
                for item in self._items
            )
            done = 0

            with zipfile.ZipFile(self._zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for ch_idx, item in enumerate(self._items):
                    if self._cancelled:
                        return
                    ch_name   = item["chapter_name"]
                    ch_folder = f"bolum_{ch_idx + 1:02d}"
                    panel_num = 1

                    for img_path, boxes in item["panels"]:
                        if self._cancelled:
                            return
                        try:
                            src = PILImage.open(img_path).convert("RGB")
                            import numpy as np
                            import cv2
                            img_arr = cv2.cvtColor(np.array(src), cv2.COLOR_RGB2BGR)
                            src.close()

                            for x, y, w, h in boxes:
                                if self._cancelled:
                                    return
                                crop = img_arr[y: y + h, x: x + w]

                                buf = io.BytesIO()
                                crop_pil = PILImage.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                                ext = self._format
                                if ext in ("jpg", "jpeg"):
                                    crop_pil.save(buf, "JPEG", quality=self._quality, optimize=True)
                                    ext = "jpg"
                                elif ext == "webp":
                                    crop_pil.save(buf, "WEBP", quality=self._quality)
                                else:
                                    crop_pil.save(buf, "PNG")
                                    ext = "png"
                                crop_pil.close()

                                arcname = f"{ch_folder}/panel_{panel_num:03d}.{ext}"
                                zf.writestr(arcname, buf.getvalue())
                                panel_num += 1
                                done += 1
                                self.progress.emit(done, total_panels,
                                                   f"{ch_name} — panel {panel_num-1}")
                        except Exception as exc:
                            logger.warning("ZIP export panel hatası %s: %s", img_path, exc)

            if not self._cancelled:
                self.finished.emit(self._zip_path)

        except Exception as exc:
            logger.exception("ZipExportWorker hatası")
            if not self._cancelled:
                self.error.emit(str(exc))

