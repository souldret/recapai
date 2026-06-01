"""
RecapAI - Panel Dedektörü
manga-panel-detector algoritması temel alınarak RecapAI'ye entegre edilmiştir.
OpenCV ile adaptif ikili eşikleme + morfolojik işlemler kullanır.
"""

import logging
import os
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Panel koordinatı tipi: (x, y, w, h)
PanelBox = Tuple[int, int, int, int]


class PanelDetector:
    """
    Manga/webtoon sayfa görselinden panelleri tespit eden sınıf.

    Algoritma:
        1. Gri tonlamaya çevirme
        2. Gauss uyarlamalı ikili eşikleme
        3. Morfolojik kapama + genişletme (kopuk çerçeveleri birleştirme)
        4. Kontur tespiti (RETR_EXTERNAL)
        5. Alan + en-boy oranı + dikdörtgensellik filtresi
        6. Sıralama: yukarıdan aşağıya, aynı satırda sağdan sola (Japon manga düzeni)
    """

    def __init__(
        self,
        min_area_ratio: float = 0.02,
        max_area_ratio: float = 0.80,
        row_threshold_px: int = 50,
    ) -> None:
        """
        Args:
            min_area_ratio: Geçerli panel için minimum alan oranı (0.0–1.0).
            max_area_ratio: Geçerli panel için maksimum alan oranı (0.0–1.0).
            row_threshold_px: Aynı satır sayılmak için maksimum Y mesafesi (piksel).
        """
        self.min_area_ratio = min_area_ratio
        self.max_area_ratio = max_area_ratio
        self.row_threshold_px = row_threshold_px

    # ── Ön İşleme ──────────────────────────────────────────────────────────────

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        """Görüntüyü binary hale getirir (morfolojik işlemlerle)."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            11, 2,
        )

        # Kopuk çerçeveleri kapat
        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_close, iterations=2)

        # Genişlet — çerçeve bağlantısını güçlendir
        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        binary = cv2.dilate(binary, kernel_dilate, iterations=1)

        return binary

    # ── Kontur Filtreleme ──────────────────────────────────────────────────────

    def filter_contours(
        self, contours: List, image_area: int
    ) -> List[PanelBox]:
        """Alan, en-boy oranı ve dikdörtgensellik filtresi uygular."""
        min_area = image_area * self.min_area_ratio
        max_area = image_area * self.max_area_ratio
        valid: List[PanelBox] = []

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)

            if area < min_area or area > max_area:
                continue

            aspect = w / h
            if aspect < 0.1 or aspect > 10.0:
                continue

            # Dikdörtgensellik: kontur alanı / bounding box alanı
            rect_area = w * h
            if rect_area == 0 or area / rect_area < 0.5:
                continue

            valid.append((x, y, w, h))

        return valid

    # ── Sıralama ───────────────────────────────────────────────────────────────

    def sort_panels(self, boxes: List[PanelBox]) -> List[PanelBox]:
        """
        Manga okuma sırası: yukarıdan aşağıya, aynı satırda sağdan sola.
        Webtoon için yalnızca yukarıdan aşağıya kullanılabilir.
        """
        if not boxes:
            return []

        centers = [(x + w // 2, y + h // 2) for x, y, w, h in boxes]
        rows: Dict[int, List] = {}

        for box, center in zip(boxes, centers):
            cy = center[1]
            matched_key = None
            min_dist = float("inf")
            for key in rows:
                d = abs(cy - key)
                if d < min_dist and d < self.row_threshold_px:
                    min_dist = d
                    matched_key = key
            if matched_key is None:
                matched_key = cy
                rows[matched_key] = []
            rows[matched_key].append((box, center))

        sorted_boxes: List[PanelBox] = []
        for row_y in sorted(rows.keys()):
            row_items = rows[row_y]
            # Sağdan sola sırala (Japon manga düzeni)
            row_items.sort(key=lambda item: item[1][0], reverse=True)
            for box, _ in row_items:
                sorted_boxes.append(box)

        return sorted_boxes

    def sort_panels_ltr(self, boxes: List[PanelBox]) -> List[PanelBox]:
        """
        Soldan sağa, yukarıdan aşağıya sıralama (webtoon / manhwa için).
        """
        if not boxes:
            return []

        centers = [(x + w // 2, y + h // 2) for x, y, w, h in boxes]
        rows: Dict[int, List] = {}

        for box, center in zip(boxes, centers):
            cy = center[1]
            matched_key = None
            min_dist = float("inf")
            for key in rows:
                d = abs(cy - key)
                if d < min_dist and d < self.row_threshold_px:
                    min_dist = d
                    matched_key = key
            if matched_key is None:
                matched_key = cy
                rows[matched_key] = []
            rows[matched_key].append((box, center))

        sorted_boxes: List[PanelBox] = []
        for row_y in sorted(rows.keys()):
            row_items = rows[row_y]
            # Soldan sağa sırala
            row_items.sort(key=lambda item: item[1][0])
            for box, _ in row_items:
                sorted_boxes.append(box)

        return sorted_boxes

    # ── Ana Tespit Fonksiyonu ──────────────────────────────────────────────────

    def detect(
        self,
        image: np.ndarray,
        reading_order: str = "rtl",
    ) -> List[PanelBox]:
        """
        Görseldeki panelleri tespit eder.

        Args:
            image: BGR numpy dizisi.
            reading_order: "rtl" (manga, sağdan sola) veya "ltr" (webtoon, soldan sağa).

        Returns:
            Sıralanmış panel bounding box listesi [(x, y, w, h), ...].
        """
        binary = self.preprocess(image)
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        image_area = image.shape[0] * image.shape[1]
        valid_boxes = self.filter_contours(list(contours), image_area)

        if reading_order == "ltr":
            return self.sort_panels_ltr(valid_boxes)
        return self.sort_panels(valid_boxes)

    def detect_from_path(
        self,
        image_path: str | Path,
        reading_order: str = "rtl",
    ) -> List[PanelBox]:
        """
        Dosya yolundan görüntü okuyarak panelleri tespit eder.
        WebP dahil tüm formatları destekler.
        """
        image_path = Path(image_path)
        # Pillow kullan: OpenCV cv2.imread ~65535 px yükseklik sınırı var
        # Büyük webtoon stitched görselleri için gereklidir
        from PIL import Image as PILImage
        try:
            pil_img = PILImage.open(image_path).convert("RGB")
            image = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            pil_img.close()
        except Exception as exc:
            raise ValueError(f"Görüntü okunamadı: {image_path} — {exc}") from exc

        return self.detect(image, reading_order=reading_order)

    # ── Panel Kırpma ───────────────────────────────────────────────────────────

    @staticmethod
    def crop_panel(image: np.ndarray, box: PanelBox) -> np.ndarray:
        """Verilen bounding box'a göre paneli kırpar."""
        x, y, w, h = box
        return image[y: y + h, x: x + w]

    @staticmethod
    def crop_panels(
        image: np.ndarray, boxes: List[PanelBox]
    ) -> List[np.ndarray]:
        """Tüm panelleri kırpıp liste olarak döndürür."""
        return [PanelDetector.crop_panel(image, box) for box in boxes]

    # ── Kaydetme ───────────────────────────────────────────────────────────────

    @staticmethod
    def save_panels(
        panels: List[np.ndarray],
        output_dir: str | Path,
        prefix: str = "panel",
        ext: str = ".jpg",
    ) -> List[str]:
        """
        Panelleri dosyaya kaydeder.

        Returns:
            Kaydedilen dosya yollarının listesi.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: List[str] = []
        for i, panel in enumerate(panels):
            filename = f"{prefix}_{i + 1:03d}{ext}"
            filepath = output_dir / filename
            cv2.imwrite(str(filepath), panel)
            paths.append(str(filepath))
        return paths

    # ── Debug Görseli ──────────────────────────────────────────────────────────

    @staticmethod
    def draw_debug(
        image: np.ndarray,
        boxes: List[PanelBox],
        color: Tuple[int, int, int] = (0, 255, 0),
        thickness: int = 2,
    ) -> np.ndarray:
        """Panel bounding box'larını çizilmiş debug görselini döndürür."""
        debug = image.copy()
        for i, (x, y, w, h) in enumerate(boxes):
            cv2.rectangle(debug, (x, y), (x + w, y + h), color, thickness)
            label = str(i + 1)
            font = cv2.FONT_HERSHEY_SIMPLEX
            ts = cv2.getTextSize(label, font, 1.0, 2)[0]
            cv2.rectangle(debug, (x, y - 30), (x + ts[0] + 4, y), color, -1)
            cv2.putText(debug, label, (x + 2, y - 5), font, 1.0, (0, 0, 0), 2)
        return debug


# ── Yatay Çizgi Tespiti (Webtoon Kesim Noktaları) ────────────────────────────

def detect_horizontal_cuts(
    image: np.ndarray,
    threshold: int = 250,
    min_consecutive: int = 5,
    dark_threshold: int = 10,
) -> List[int]:
    """
    Webtoon görselinde tamamen beyaz veya tamamen siyah yatay çizgileri tespit eder.
    Bu çizgiler doğal kesim noktaları olarak kullanılabilir.

    Args:
        image: BGR numpy dizisi.
        threshold: Bir satırın "beyaz" sayılması için minimum ortalama parlaklık (0–255).
        min_consecutive: Kesim noktası sayılmak için minimum ardışık satır sayısı.
        dark_threshold: Bir satırın "siyah" sayılması için maksimum ortalama parlaklık.

    Returns:
        Y koordinatlarının listesi (her biri bir kesim bandının ortası).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # Her satırın ortalamasını hesapla
    row_means = gray.mean(axis=1)   # shape: (h,)

    # Beyaz (>= threshold) veya siyah (<= dark_threshold) satırları işaretle
    is_cut = (row_means >= threshold) | (row_means <= dark_threshold)

    cut_points: List[int] = []
    in_band = False
    band_start = 0

    for y in range(h):
        if is_cut[y]:
            if not in_band:
                in_band = True
                band_start = y
        else:
            if in_band:
                band_end = y - 1
                band_len = band_end - band_start + 1
                if band_len >= min_consecutive:
                    cut_points.append((band_start + band_end) // 2)
                in_band = False

    # Son band
    if in_band:
        band_len = h - band_start
        if band_len >= min_consecutive:
            cut_points.append((band_start + h - 1) // 2)

    return cut_points


def split_by_horizontal_cuts(
    image: np.ndarray,
    cut_y_list: List[int],
    min_segment_height: int = 50,
) -> List[np.ndarray]:
    """
    Tespit edilen kesim noktalarına göre görüntüyü yatay parçalara böler.

    Args:
        image: BGR numpy dizisi.
        cut_y_list: Kesim noktalarının Y koordinatları.
        min_segment_height: Minimum segment yüksekliği (küçükleri atlar).

    Returns:
        Segment görüntülerinin listesi.
    """
    h = image.shape[0]
    boundaries = [0] + sorted(cut_y_list) + [h]
    segments: List[np.ndarray] = []

    for i in range(len(boundaries) - 1):
        y_start = boundaries[i]
        y_end   = boundaries[i + 1]
        if y_end - y_start >= min_segment_height:
            segments.append(image[y_start:y_end, :])

    return segments


# ── Webtoon Stitch ─────────────────────────────────────────────────────────────

def stitch_images_vertical(
    image_paths: List[str | Path],
    output_path: str | Path,
    gap: int = 0,
    gap_color: Tuple[int, int, int] = (255, 255, 255),
) -> str:
    """
    Birden fazla görüntüyü dikey olarak birleştirir (webtoon için).
    Pillow tabanlı uygulama — OpenCV'nin ~65535 px yükseklik sınırı yoktur.

    Args:
        image_paths: Birleştirilecek görsel dosya yolları (sırayla).
        output_path: Çıktı dosyası yolu (.png önerilir; JPEG 65535 px sınırına sahiptir).
        gap: Görseller arası boşluk (piksel).
        gap_color: Boşluk rengi (BGR tuple — OpenCV convention ile uyumlu).

    Returns:
        Kaydedilen dosyanın yolu (str).

    Raises:
        ValueError: Görsel listesi boşsa veya hiç görsel okunamazsa.
    """
    from PIL import Image as PILImage

    if not image_paths:
        raise ValueError("Birleştirilecek görsel listesi boş.")

    # ── 1. Geçiş: minimum genişliği bul (bellek açısından verimli) ──
    target_width: Optional[int] = None
    valid_paths: List[Path] = []
    for p in image_paths:
        p = Path(p)
        try:
            with PILImage.open(p) as probe:
                w = probe.size[0]
            if target_width is None or w < target_width:
                target_width = w
            valid_paths.append(p)
        except Exception as exc:
            logger.warning("Stitch: görsel okunamadı, atlandı: %s — %s", p, exc)

    if not valid_paths or target_width is None:
        raise ValueError("Hiçbir görsel okunamadı.")

    # ── 2. Geçiş: toplam yüksekliği hesapla ──
    total_height = 0
    heights: List[int] = []
    for p in valid_paths:
        with PILImage.open(p) as probe:
            orig_w, orig_h = probe.size
        if orig_w != target_width:
            scaled_h = int(orig_h * target_width / orig_w)
        else:
            scaled_h = orig_h
        heights.append(scaled_h)
        total_height += scaled_h
    if gap > 0:
        total_height += gap * (len(valid_paths) - 1)

    # ── 3. Birleştir: önceden ayrılmış tuval üzerine yapıştır ──
    # gap_color BGR → RGB dönüşümü (Pillow RGB kullanır)
    bg_rgb = (gap_color[2], gap_color[1], gap_color[0])
    canvas = PILImage.new("RGB", (target_width, total_height), bg_rgb)
    y_offset = 0
    for i, (p, h) in enumerate(zip(valid_paths, heights)):
        img = PILImage.open(p).convert("RGB")
        if img.size[0] != target_width:
            img = img.resize((target_width, h), PILImage.LANCZOS)
        canvas.paste(img, (0, y_offset))
        img.close()
        y_offset += h
        if gap > 0 and i < len(valid_paths) - 1:
            y_offset += gap

    # ── 4. Kaydet — PNG zorunlu değil ama JPEG için yükseklik uyarısı ──
    output_path = Path(output_path)
    # JPEG maksimum 65535 px — büyük stitch için otomatik PNG'ye geç
    if output_path.suffix.lower() in (".jpg", ".jpeg") and total_height > 65000:
        output_path = output_path.with_suffix(".png")
        logger.info("Stitch yüksekliği JPEG limitini aşıyor (%d px) — PNG olarak kaydediliyor.", total_height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path))
    canvas.close()
    logger.info("Stitch tamamlandı: %s (%dx%d)", output_path, target_width, total_height)
    return str(output_path)