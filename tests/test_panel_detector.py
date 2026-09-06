"""PanelDetector gutter / NMS / sıralama testleri."""

import numpy as np

from core.panel_detector import (
    PanelDetector,
    nms_boxes,
    merge_contained,
    detect_gutter_panels,
    detect_content_panels,
    detect_horizontal_cuts,
)


def _page(w=800, h=1200, panels=None, bg=255):
    img = np.full((h, w, 3), bg, dtype=np.uint8)
    for x, y, pw, ph in panels or []:
        img[y:y + ph, x:x + pw] = (40, 40, 40)
        img[y + 8:y + ph - 8, x + 8:x + pw - 8] = (90, 90, 110)
    return img


class TestNmsAndMerge:
    def test_nms_drops_heavy_overlap(self):
        a = (10, 10, 100, 100)
        b = (15, 15, 100, 100)
        kept = nms_boxes([a, b], iou_thresh=0.5)
        assert len(kept) == 1

    def test_merge_contained_drops_inner(self):
        outer = (0, 0, 200, 200)
        inner = (20, 20, 50, 50)
        out = merge_contained([outer, inner])
        assert outer in out
        assert inner not in out


class TestGutterDetect:
    def test_content_detect_two_side(self):
        img = _page(panels=[
            (40, 40, 340, 500),
            (420, 40, 340, 500),
        ])
        boxes = detect_content_panels(img, min_area_ratio=0.02, max_area_ratio=0.9)
        assert len(boxes) >= 2

    def test_two_vertical_panels(self):
        img = _page(panels=[
            (40, 40, 340, 500),
            (420, 40, 340, 500),
        ])
        boxes = detect_gutter_panels(img, min_area_ratio=0.02, max_area_ratio=0.9)
        assert len(boxes) >= 2

    def test_two_stacked_panels(self):
        img = _page(panels=[
            (40, 40, 720, 500),
            (40, 620, 720, 500),
        ])
        det = PanelDetector(min_area_ratio=0.02, max_area_ratio=0.92)
        boxes = det.detect(img, reading_order="ltr")
        assert len(boxes) >= 2
        ys = [b[1] for b in boxes]
        assert ys == sorted(ys)

    def test_rtl_right_first(self):
        img = _page(panels=[
            (40, 80, 300, 400),
            (420, 80, 300, 400),
        ])
        det = PanelDetector(min_area_ratio=0.02)
        boxes = det.detect(img, reading_order="rtl")
        assert len(boxes) >= 2
        assert boxes[0][0] > boxes[1][0]

    def test_grid_four_panels(self):
        img = _page(panels=[
            (40, 40, 340, 500),
            (420, 40, 340, 500),
            (40, 620, 340, 500),
            (420, 620, 340, 500),
        ])
        det = PanelDetector(min_area_ratio=0.02, max_area_ratio=0.92)
        boxes = det.detect(img, reading_order="ltr")
        assert len(boxes) == 4

    def test_stacked_not_split_by_phantom_columns(self):
        img = _page(panels=[
            (40, 40, 720, 500),
            (40, 620, 720, 500),
        ])
        boxes = detect_gutter_panels(img, min_area_ratio=0.02, max_area_ratio=0.9)
        assert len(boxes) == 2

    def test_empty_page_returns_one(self):
        img = np.full((400, 300, 3), 245, dtype=np.uint8)
        det = PanelDetector()
        boxes = det.detect(img, reading_order="ltr")
        assert len(boxes) >= 1


class TestHorizontalCuts:
    def test_white_band_cut(self):
        img = np.full((600, 200, 3), 30, dtype=np.uint8)
        img[280:320, :] = 255
        cuts = detect_horizontal_cuts(img, threshold=250, min_consecutive=8, dark_threshold=10)
        assert cuts
        assert 270 <= cuts[0] <= 330
