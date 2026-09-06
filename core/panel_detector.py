"""
RecapAI - Panel Dedektörü
Gutter (ara boşluk) ızgarası + kontur yedek. Tam bölüm sayfalarını panellere ayırır.
"""

import logging
import warnings
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import cv2
import numpy as np
from PIL import Image as PILImage

PILImage.MAX_IMAGE_PIXELS = None
warnings.filterwarnings("ignore", category=PILImage.DecompressionBombWarning)

logger = logging.getLogger(__name__)

PanelBox = Tuple[int, int, int, int]


class PanelDetector:
    """
    Manga/manhwa/webtoon sayfasından panelleri tespit eder.

    1) Kenar rengine göre gutter (beyaz/siyah ara boşluk) ızgarası
    2) Gutter yoksa ölçekli adaptif eşik + kontur
    3) NMS, inset, okuma sırası
    """

    def __init__(
        self,
        min_area_ratio: float = 0.015,
        max_area_ratio: float = 0.92,
        row_threshold_px: int = 0,
    ) -> None:
        self.min_area_ratio = min_area_ratio
        self.max_area_ratio = max_area_ratio
        self.row_threshold_px = row_threshold_px

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        block = int(max(15, min(51, min(w, h) * 0.018)))
        if block % 2 == 0:
            block += 1
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            block, 4,
        )
        k = max(3, int(min(w, h) * 0.004))
        if k % 2 == 0:
            k += 1
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
        binary = cv2.dilate(binary, kernel, iterations=1)
        return binary

    def filter_contours(self, contours: List, image_area: int) -> List[PanelBox]:
        min_area = image_area * self.min_area_ratio
        max_area = image_area * self.max_area_ratio
        valid: List[PanelBox] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < min_area or area > max_area:
                continue
            aspect = w / max(h, 1)
            if aspect < 0.08 or aspect > 14.0:
                continue
            c_area = cv2.contourArea(contour)
            if area == 0 or c_area / area < 0.35:
                continue
            valid.append((int(x), int(y), int(w), int(h)))
        return valid

    def _row_threshold(self, image_h: int) -> int:
        if self.row_threshold_px > 0:
            return self.row_threshold_px
        return max(40, int(image_h * 0.035))

    def _cluster_rows(
        self, boxes: List[PanelBox], image_h: int
    ) -> Dict[int, List[Tuple[PanelBox, Tuple[int, int]]]]:
        thresh = self._row_threshold(image_h)
        centers = [(x + w // 2, y + h // 2) for x, y, w, h in boxes]
        rows: Dict[int, List] = {}
        for box, center in zip(boxes, centers):
            cy = center[1]
            matched_key = None
            min_dist = float("inf")
            for key in rows:
                d = abs(cy - key)
                if d < min_dist and d < thresh:
                    min_dist = d
                    matched_key = key
            if matched_key is None:
                matched_key = cy
                rows[matched_key] = []
            rows[matched_key].append((box, center))
        return rows

    def sort_panels(self, boxes: List[PanelBox], image_h: int = 2000) -> List[PanelBox]:
        if not boxes:
            return []
        rows = self._cluster_rows(boxes, image_h)
        sorted_boxes: List[PanelBox] = []
        for row_y in sorted(rows.keys()):
            row_items = rows[row_y]
            row_items.sort(key=lambda item: item[1][0], reverse=True)
            for box, _ in row_items:
                sorted_boxes.append(box)
        return sorted_boxes

    def sort_panels_ltr(self, boxes: List[PanelBox], image_h: int = 2000) -> List[PanelBox]:
        if not boxes:
            return []
        rows = self._cluster_rows(boxes, image_h)
        sorted_boxes: List[PanelBox] = []
        for row_y in sorted(rows.keys()):
            row_items = rows[row_y]
            row_items.sort(key=lambda item: item[1][0])
            for box, _ in row_items:
                sorted_boxes.append(box)
        return sorted_boxes

    def detect(
        self,
        image: np.ndarray,
        reading_order: str = "rtl",
    ) -> List[PanelBox]:
        h, w = image.shape[:2]
        if h <= 0 or w <= 0:
            return []
        tall = h / max(w, 1) >= 2.2
        if tall:
            boxes = detect_strip_panels(image)
        else:
            work, scale = _detection_scale(image, max_side=2200)
            boxes = detect_page_panels(
                work,
                min_area_ratio=min(self.min_area_ratio, 0.012),
                max_area_ratio=max(self.max_area_ratio, 0.94),
            )
            if len(boxes) < 2:
                extra = detect_content_panels(
                    work,
                    min_area_ratio=min(self.min_area_ratio, 0.008),
                    max_area_ratio=self.max_area_ratio,
                )
                if len(extra) > len(boxes):
                    boxes = extra
            if scale != 1.0:
                boxes = [
                    (
                        int(round(x / scale)), int(round(y / scale)),
                        int(round(bw / scale)), int(round(bh / scale)),
                    )
                    for x, y, bw, bh in boxes
                ]
        boxes = [_clamp_box(b, w, h) for b in boxes]
        boxes = nms_boxes(boxes, iou_thresh=0.5)
        boxes = merge_contained(boxes)
        min_area = w * max(48, int(h * 0.008 if tall else w * 0.04))
        boxes = [b for b in boxes if _box_area(b) >= min_area]
        if not boxes:
            boxes = [(0, 0, w, h)]
        inset = max(1, int(min(w, 400) * 0.004))
        boxes = [_inset_box(b, w, h, inset) for b in boxes]
        if reading_order == "ltr":
            return self.sort_panels_ltr(boxes, h)
        return self.sort_panels(boxes, h)

    def _detect_contours(self, image: np.ndarray) -> List[PanelBox]:
        binary = self.preprocess(image)
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        image_area = image.shape[0] * image.shape[1]
        return self.filter_contours(list(contours), image_area)

    def detect_from_path(
        self,
        image_path: str | Path,
        reading_order: str = "rtl",
    ) -> List[PanelBox]:
        image_path = Path(image_path)
        try:
            pil_img = PILImage.open(image_path).convert("RGB")
            image = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            pil_img.close()
        except Exception as exc:
            raise ValueError(f"Görüntü okunamadı: {image_path} — {exc}") from exc
        return self.detect(image, reading_order=reading_order)

    @staticmethod
    def crop_panel(image: np.ndarray, box: PanelBox) -> np.ndarray:
        x, y, w, h = box
        ih, iw = image.shape[:2]
        x = max(0, x)
        y = max(0, y)
        w = min(w, iw - x)
        h = min(h, ih - y)
        return image[y: y + h, x: x + w]

    @staticmethod
    def crop_panels(image: np.ndarray, boxes: List[PanelBox]) -> List[np.ndarray]:
        return [PanelDetector.crop_panel(image, box) for box in boxes]

    @staticmethod
    def save_panels(
        panels: List[np.ndarray],
        output_dir: str | Path,
        prefix: str = "panel",
        ext: str = ".jpg",
        jpeg_quality: int = 95,
    ) -> List[str]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: List[str] = []
        ext = ext if ext.startswith(".") else f".{ext}"
        for i, panel in enumerate(panels):
            if panel is None or panel.size == 0:
                continue
            filename = f"{prefix}_{i + 1:03d}{ext}"
            filepath = output_dir / filename
            rgb = cv2.cvtColor(panel, cv2.COLOR_BGR2RGB)
            im = PILImage.fromarray(rgb)
            if ext.lower() in (".jpg", ".jpeg"):
                im.save(str(filepath), quality=jpeg_quality, optimize=True)
            elif ext.lower() == ".png":
                im.save(str(filepath), optimize=True)
            elif ext.lower() == ".webp":
                im.save(str(filepath), quality=jpeg_quality, method=6)
            else:
                im.save(str(filepath))
            paths.append(str(filepath))
        return paths

    @staticmethod
    def draw_debug(
        image: np.ndarray,
        boxes: List[PanelBox],
        color: Tuple[int, int, int] = (0, 255, 0),
        thickness: int = 2,
    ) -> np.ndarray:
        debug = image.copy()
        for i, (x, y, w, h) in enumerate(boxes):
            cv2.rectangle(debug, (x, y), (x + w, y + h), color, thickness)
            label = str(i + 1)
            font = cv2.FONT_HERSHEY_SIMPLEX
            ts = cv2.getTextSize(label, font, 1.0, 2)[0]
            cv2.rectangle(debug, (x, y - 30), (x + ts[0] + 4, y), color, -1)
            cv2.putText(debug, label, (x + 2, y - 5), font, 1.0, (0, 0, 0), 2)
        return debug


def _clamp_box(box: PanelBox, img_w: int, img_h: int) -> PanelBox:
    x, y, bw, bh = box
    x = max(0, min(x, img_w - 2))
    y = max(0, min(y, img_h - 2))
    bw = max(2, min(bw, img_w - x))
    bh = max(2, min(bh, img_h - y))
    return (x, y, bw, bh)


def _detection_scale(image: np.ndarray, max_side: int = 1600) -> Tuple[np.ndarray, float]:
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return image, 1.0
    scale = max_side / float(longest)
    nw = max(32, int(w * scale))
    nh = max(32, int(h * scale))
    small = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_AREA)
    return small, scale


def _box_area(box: PanelBox) -> int:
    return max(0, box[2]) * max(0, box[3])


def _inset_box(box: PanelBox, img_w: int, img_h: int, inset: int) -> PanelBox:
    x, y, w, h = box
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    x = max(0, x + inset)
    y = max(0, y + inset)
    x2 = min(img_w, x2 - inset)
    y2 = min(img_h, y2 - inset)
    return (x, y, max(2, x2 - x), max(2, y2 - y))


def _iou(a: PanelBox, b: PanelBox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = _box_area(a) + _box_area(b) - inter
    return inter / union if union else 0.0


def nms_boxes(boxes: List[PanelBox], iou_thresh: float = 0.55) -> List[PanelBox]:
    if len(boxes) <= 1:
        return boxes
    ordered = sorted(boxes, key=_box_area, reverse=True)
    keep: List[PanelBox] = []
    for box in ordered:
        if all(_iou(box, k) < iou_thresh for k in keep):
            keep.append(box)
    return keep


def merge_contained(boxes: List[PanelBox], cover: float = 0.88) -> List[PanelBox]:
    if len(boxes) <= 1:
        return boxes
    drop = set()
    for i, a in enumerate(boxes):
        for j, b in enumerate(boxes):
            if i == j or j in drop:
                continue
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            x1 = max(ax, bx)
            y1 = max(ay, by)
            x2 = min(ax + aw, bx + bw)
            y2 = min(ay + ah, by + bh)
            inter = max(0, x2 - x1) * max(0, y2 - y1)
            if _box_area(a) > 0 and inter / _box_area(a) >= cover and _box_area(b) > _box_area(a):
                drop.add(i)
                break
    return [b for i, b in enumerate(boxes) if i not in drop]


def _gutter_mask(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    border = np.concatenate([
        gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]
    ])
    border_med = float(np.median(border))
    light = border_med >= 140
    if light:
        mask = gray >= 232
    else:
        mask = gray <= 28
    min_run = max(4, int(min(w, h) * 0.006))
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, w // 40), 1))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(9, h // 40)))
    mask_u8 = mask.astype(np.uint8) * 255
    mask_h = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel_h)
    mask_v = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel_v)
    combined = cv2.bitwise_or(mask_h, mask_v)
    k = max(3, min_run)
    combined = cv2.dilate(
        combined,
        cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)),
        iterations=1,
    )
    return combined


def _split_axis(
    occupancy: np.ndarray,
    min_gap: int,
    min_content: int,
    trim_edges: bool = True,
) -> List[Tuple[int, int]]:
    n = occupancy.size
    gaps: List[Tuple[int, int]] = []
    i = 0
    while i < n:
        if occupancy[i] == 0:
            start = i
            while i < n and occupancy[i] == 0:
                i += 1
            if i - start >= min_gap:
                gaps.append((start, i))
        else:
            i += 1
    cuts = [0]
    for gs, ge in gaps:
        if gs <= 2:
            if not trim_edges:
                cuts.append(ge)
            continue
        if ge >= n - 2:
            if not trim_edges:
                cuts.append(gs)
            continue
        cuts.append((gs + ge) // 2)
    cuts.append(n)
    cuts = sorted(set(int(c) for c in cuts if 0 <= c <= n))
    spans: List[Tuple[int, int]] = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a >= min_content:
            spans.append((a, b))
    return spans if spans else [(0, n)]


def _boxes_from_region(
    content: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    min_gap_x: int,
    min_w: int,
    min_area: float,
    max_area: float,
) -> List[PanelBox]:
    region = content[y0:y1, x0:x1]
    if region.size == 0:
        return []
    col_occ = (region.sum(axis=0) > (y1 - y0) * 0.08).astype(np.uint8)
    x_spans = _split_axis(col_occ, min_gap_x, min_w)
    boxes: List[PanelBox] = []
    for sx0, sx1 in x_spans:
        cell = region[:, sx0:sx1]
        if cell.size == 0 or float(cell.mean()) < 0.12:
            continue
        ys = np.where(cell.any(axis=1))[0]
        xs = np.where(cell.any(axis=0))[0]
        if ys.size == 0 or xs.size == 0:
            continue
        bx = x0 + sx0 + int(xs[0])
        by = y0 + int(ys[0])
        bw = int(xs[-1]) - int(xs[0]) + 1
        bh = int(ys[-1]) - int(ys[0]) + 1
        area = bw * bh
        if area < min_area or area > max_area:
            continue
        boxes.append((bx, by, bw, bh))
    return boxes


def detect_strip_panels(image: np.ndarray) -> List[PanelBox]:
    """Uzun webtoon/manhwa şeridini yatay gutter'lardan panellere böler."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    row_mean = gray.mean(axis=1)
    row_std = gray.std(axis=1)
    border = float(np.median(np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])))
    light = border >= 128
    if light:
        is_gap = (row_mean >= 232) & (row_std < 22)
    else:
        is_gap = (row_mean <= 22) & (row_std < 22)

    min_gap = max(6, int(h * 0.0025), int(w * 0.012))
    min_h = max(36, int(w * 0.12))
    spans = _split_axis((~is_gap).astype(np.uint8), min_gap, min_h, trim_edges=False)
    boxes: List[PanelBox] = []
    for y0, y1 in spans:
        band = gray[y0:y1, :]
        if band.size == 0:
            continue
        col_std = band.std(axis=0)
        xs = np.where(col_std > 6)[0]
        if xs.size == 0:
            x0, x1 = 0, w
        else:
            x0, x1 = int(xs[0]), int(xs[-1]) + 1
        pad = max(2, w // 200)
        x0 = max(0, x0 - pad)
        x1 = min(w, x1 + pad)
        if y1 - y0 >= min_h and x1 - x0 >= max(24, w // 8):
            boxes.append((x0, y0, x1 - x0, y1 - y0))
    return boxes


def detect_page_panels(
    image: np.ndarray,
    min_area_ratio: float = 0.012,
    max_area_ratio: float = 0.94,
) -> List[PanelBox]:
    """Manga/manhwa sayfası: satır projeksiyonu, sonra her satırda kolon."""
    boxes = detect_gutter_panels(image, min_area_ratio, max_area_ratio)
    if len(boxes) >= 2:
        return boxes
    return detect_content_panels(image, min_area_ratio, max_area_ratio)


def detect_content_panels(
    image: np.ndarray,
    min_area_ratio: float = 0.015,
    max_area_ratio: float = 0.92,
) -> List[PanelBox]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    image_area = h * w
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    light_gutter = float(np.median(border)) >= 128
    if light_gutter:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        binary[gray >= 240] = 0
    else:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binary[gray <= 18] = 0

    kx = max(3, int(w * 0.012))
    ky = max(3, int(h * 0.010))
    if kx % 2 == 0:
        kx += 1
    if ky % 2 == 0:
        ky += 1
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
    content = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_k, iterations=1)
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, kx // 2), max(3, ky // 2)))
    content = cv2.morphologyEx(content, cv2.MORPH_OPEN, open_k, iterations=1)

    num, labels, stats, _ = cv2.connectedComponentsWithStats(content, connectivity=8)
    min_area = max(80.0, image_area * min(min_area_ratio, 0.006))
    max_area = image_area * max_area_ratio
    boxes: List[PanelBox] = []
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if area < min_area or bw * bh > max_area:
            continue
        aspect = bw / max(bh, 1)
        if aspect < 0.06 or aspect > 16.0:
            continue
        pad = 2
        boxes.append((
            max(0, x - pad),
            max(0, y - pad),
            min(w - max(0, x - pad), bw + 2 * pad),
            min(h - max(0, y - pad), bh + 2 * pad),
        ))

    if len(boxes) < 2:
        gutter_boxes = detect_gutter_panels(
            image, min_area_ratio=min_area_ratio, max_area_ratio=max_area_ratio,
        )
        if len(gutter_boxes) > len(boxes):
            return gutter_boxes
    return boxes


def detect_gutter_panels(
    image: np.ndarray,
    min_area_ratio: float = 0.015,
    max_area_ratio: float = 0.92,
) -> List[PanelBox]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    image_area = h * w
    gutter = _gutter_mask(gray)
    content = (gutter == 0).astype(np.uint8)
    row_occ = (content.sum(axis=1) > w * 0.04).astype(np.uint8)
    min_gap_x = max(6, int(w * 0.012))
    min_gap_y = max(6, int(h * 0.010))
    min_w = max(24, int(w * 0.08))
    min_h = max(24, int(h * 0.06))
    min_area = image_area * min_area_ratio
    max_area = image_area * max_area_ratio
    y_spans = _split_axis(row_occ, min_gap_y, min_h)
    boxes: List[PanelBox] = []
    for y0, y1 in y_spans:
        boxes.extend(_boxes_from_region(
            content, 0, y0, w, y1, min_gap_x, min_w, min_area, max_area,
        ))
    if len(y_spans) == 1 and len(boxes) <= 1:
        return []
    return boxes


def detect_horizontal_cuts(
    image: np.ndarray,
    threshold: int = 250,
    min_consecutive: int = 5,
    dark_threshold: int = 10,
) -> List[int]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    row_means = gray.mean(axis=1)
    row_std = gray.std(axis=1)
    is_cut = ((row_means >= threshold) | (row_means <= dark_threshold)) & (row_std < 18)
    min_run = max(min_consecutive, int(h * 0.004), 8)
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
                if band_end - band_start + 1 >= min_run:
                    cut_points.append((band_start + band_end) // 2)
                in_band = False
    if in_band:
        if h - band_start >= min_run:
            cut_points.append((band_start + h - 1) // 2)
    return cut_points


def split_by_horizontal_cuts(
    image: np.ndarray,
    cut_y_list: List[int],
    min_segment_height: int = 50,
) -> List[np.ndarray]:
    h = image.shape[0]
    boundaries = [0] + sorted(cut_y_list) + [h]
    segments: List[np.ndarray] = []
    for i in range(len(boundaries) - 1):
        y_start = boundaries[i]
        y_end = boundaries[i + 1]
        if y_end - y_start >= min_segment_height:
            segments.append(image[y_start:y_end, :])
    return segments


def stitch_images_vertical(
    image_paths: List[str | Path],
    output_path: str | Path,
    gap: int = 0,
    gap_color: Tuple[int, int, int] = (255, 255, 255),
) -> str:
    if not image_paths:
        raise ValueError("Birleştirilecek görsel listesi boş.")

    target_width: Optional[int] = None
    valid_paths: List[Path] = []
    for p in image_paths:
        p = Path(p)
        try:
            with PILImage.open(p) as probe:
                ww = probe.size[0]
            if target_width is None or ww < target_width:
                target_width = ww
            valid_paths.append(p)
        except Exception as exc:
            logger.warning("Stitch: görsel okunamadı, atlandı: %s — %s", p, exc)

    if not valid_paths or target_width is None:
        raise ValueError("Hiçbir görsel okunamadı.")

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

    bg_rgb = (gap_color[2], gap_color[1], gap_color[0])
    canvas = PILImage.new("RGB", (target_width, total_height), bg_rgb)
    y_offset = 0
    for i, (p, hh) in enumerate(zip(valid_paths, heights)):
        img = PILImage.open(p).convert("RGB")
        if img.size[0] != target_width:
            img = img.resize((target_width, hh), PILImage.LANCZOS)
        canvas.paste(img, (0, y_offset))
        img.close()
        y_offset += hh
        if gap > 0 and i < len(valid_paths) - 1:
            y_offset += gap

    output_path = Path(output_path)
    if output_path.suffix.lower() in (".jpg", ".jpeg") and total_height > 65000:
        output_path = output_path.with_suffix(".png")
        logger.info("Stitch yüksekliği JPEG limitini aşıyor (%d px) — PNG olarak kaydediliyor.", total_height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path))
    canvas.close()
    logger.info("Stitch tamamlandı: %s (%dx%d)", output_path, target_width, total_height)
    return str(output_path)
