"""
RecapAI - YOLO (yerel) + Vision API panel tespiti.
OpenCV kural motorunun üzerine isteğe bağlı modeller.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from core.constants import BASE_DIR

logger = logging.getLogger(__name__)

PanelBox = Tuple[int, int, int, int]

YOLO_WEIGHTS = BASE_DIR / "models" / "yolov8s-worldv2.pt"
YOLO_CLASSES = ["comic panel", "manga panel", "manhwa panel", "webtoon panel"]

# OpenRouter yaklaşık USD / 1M token (2026). Görsel ~1 sayfa ≈ 800 girdi + 300 çıktı.
VISION_RATES = {
    "google/gemini-2.5-flash": {"in": 0.15, "out": 0.60, "label": "Gemini 2.5 Flash"},
    "google/gemini-2.5-flash-lite": {"in": 0.05, "out": 0.20, "label": "Gemini 2.5 Flash Lite"},
    "google/gemini-2.5-pro": {"in": 1.25, "out": 10.00, "label": "Gemini 2.5 Pro"},
    "google/gemini-2.0-flash": {"in": 0.10, "out": 0.40, "label": "Gemini 2.0 Flash"},
    "openai/gpt-4o-mini": {"in": 0.15, "out": 0.60, "label": "GPT-4o Mini"},
    "openai/gpt-4o": {"in": 2.50, "out": 10.00, "label": "GPT-4o"},
    "openai/gpt-4.1-mini": {"in": 0.40, "out": 1.60, "label": "GPT-4.1 Mini"},
    "openai/gpt-4.1": {"in": 2.00, "out": 8.00, "label": "GPT-4.1"},
    "anthropic/claude-sonnet-4": {"in": 3.00, "out": 15.00, "label": "Claude Sonnet 4"},
}

TOKENS_IN_PER_PAGE = 900
TOKENS_OUT_PER_PAGE = 350

VISION_PROMPT = """You detect comic/manga/manhwa/webtoon PANEL FRAMES on this image.
Return every story panel as a box. Do NOT box speech bubbles, SFX, or characters.

Return ONLY JSON (no markdown):
{"panels":[{"x":0,"y":0,"w":100,"h":100}]}

x,y,w,h are PERCENT of the full image (0-100). x,y = top-left.
Order: top-to-bottom, then left-to-right (manhwa) unless the page is Japanese manga (right-to-left in each row).
Include every panel, even small ones. Cover the whole strip/page.
"""

_yolo_model = None
_yolo_failed = False


def yolo_available() -> bool:
    try:
        import ultralytics  # noqa: F401
        return True
    except Exception:
        return False


def yolo_status() -> str:
    if _yolo_failed:
        return "yuklenemedi"
    if not yolo_available():
        return "ultralytics yok — .venv\\Scripts\\python.exe -m pip install ultralytics"
    if YOLO_WEIGHTS.exists():
        return f"hazir ({YOLO_WEIGHTS.name})"
    return "ilk calistirmada model inecek (~15MB)"


def _load_yolo():
    global _yolo_model, _yolo_failed
    if _yolo_model is not None:
        return _yolo_model
    if _yolo_failed:
        return None
    try:
        from ultralytics import YOLO
        YOLO_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
        src = str(YOLO_WEIGHTS) if YOLO_WEIGHTS.exists() else "yolov8s-worldv2.pt"
        model = YOLO(src)
        try:
            model.set_classes(YOLO_CLASSES)
        except Exception:
            pass
        if not YOLO_WEIGHTS.exists():
            try:
                imported = Path(getattr(model, "ckpt_path", "") or "")
                if imported.exists():
                    import shutil
                    shutil.copy2(imported, YOLO_WEIGHTS)
            except Exception:
                pass
        _yolo_model = model
        logger.info("YOLO-World panel modeli yuklendi.")
        return model
    except Exception as exc:
        _yolo_failed = True
        logger.warning("YOLO yuklenemedi: %s", exc)
        return None


def detect_yolo_panels(
    image: np.ndarray,
    conf: float = 0.18,
    iou: float = 0.45,
) -> List[PanelBox]:
    model = _load_yolo()
    if model is None:
        return []
    h, w = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    try:
        results = model.predict(rgb, conf=conf, iou=iou, verbose=False, imgsz=1280)
    except Exception as exc:
        logger.warning("YOLO predict hatasi: %s", exc)
        return []
    boxes: List[PanelBox] = []
    for r in results:
        if r.boxes is None:
            continue
        for b in r.boxes:
            xyxy = b.xyxy[0].tolist()
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(x1 + 2, min(x2, w))
            y2 = max(y1 + 2, min(y2, h))
            boxes.append((x1, y1, x2 - x1, y2 - y1))
    return boxes


def estimate_vision_cost(model: str, pages: int = 1) -> dict:
    pages = max(1, int(pages))
    rates = VISION_RATES.get(model) or {"in": 0.50, "out": 2.00, "label": model}
    usd = pages * (
        (TOKENS_IN_PER_PAGE / 1_000_000.0) * rates["in"]
        + (TOKENS_OUT_PER_PAGE / 1_000_000.0) * rates["out"]
    )
    usd = max(usd, 0.0001 * pages)
    return {
        "model": model,
        "label": rates.get("label", model),
        "pages": pages,
        "usd": round(usd, 4),
        "usd_low": round(usd * 0.6, 4),
        "usd_high": round(usd * 1.8, 4),
        "note": (
            f"~{pages} sayfa × {rates.get('label', model)}. "
            f"Tahmini ${usd * 0.6:.4f}–${usd * 1.8:.4f} "
            f"(OpenRouter; gerçek fatura token'a göre değişir)."
        ),
    }


def _item_to_xywh(it) -> Optional[Tuple[float, float, float, float]]:
    if isinstance(it, (list, tuple)) and len(it) >= 4:
        a, b, c, d = float(it[0]), float(it[1]), float(it[2]), float(it[3])
        if c > a and d > b:
            return a, b, c - a, d - b
        return a, b, c, d
    if not isinstance(it, dict):
        return None
    if {"x", "y", "w", "h"} <= set(it):
        return float(it["x"]), float(it["y"]), float(it["w"]), float(it["h"])
    bbox = it.get("bbox") or it.get("box") or it.get("rect")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return _item_to_xywh(bbox)
    keys = {k.lower(): k for k in it}
    def _g(*names):
        for n in names:
            k = keys.get(n)
            if k is not None:
                return float(it[k])
        return None
    x1 = _g("x1", "xmin", "left", "x_min")
    y1 = _g("y1", "ymin", "top", "y_min")
    x2 = _g("x2", "xmax", "right", "x_max")
    y2 = _g("y2", "ymax", "bottom", "y_max")
    if None not in (x1, y1, x2, y2):
        return x1, y1, x2 - x1, y2 - y1
    return None


def parse_vision_boxes(text: str, img_w: int, img_h: int) -> List[PanelBox]:
    raw = (text or "").strip()
    if not raw:
        return []
    raw = raw.replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{[\s\S]*\}", raw)
    blob = match.group(0) if match else raw
    if not match:
        arr = re.search(r"\[[\s\S]*\]", raw)
        if arr:
            blob = arr.group(0)
    data = None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("panels") or data.get("boxes") or data.get("regions") or data.get("detections") or []
        if not items:
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], (dict, list)):
                    items = v
                    break
        if not items and _item_to_xywh(data):
            items = [data]
    if not items:
        for m in re.finditer(
            r'\{\s*"x"\s*:\s*([-\d.]+)\s*,\s*"y"\s*:\s*([-\d.]+)\s*,\s*"w"\s*:\s*([-\d.]+)\s*,\s*"h"\s*:\s*([-\d.]+)',
            text or "",
        ):
            items.append({"x": m.group(1), "y": m.group(2), "w": m.group(3), "h": m.group(4)})
    if not isinstance(items, list):
        return []
    boxes: List[PanelBox] = []
    for it in items:
        xywh = _item_to_xywh(it)
        if not xywh:
            continue
        x, y, bw, bh = xywh
        if max(abs(x), abs(y), abs(bw), abs(bh)) <= 1.5:
            x, y, bw, bh = x * 100.0, y * 100.0, bw * 100.0, bh * 100.0
        if max(abs(x), abs(y), abs(bw), abs(bh)) <= 100.5:
            x = x / 100.0 * img_w
            y = y / 100.0 * img_h
            bw = bw / 100.0 * img_w
            bh = bh / 100.0 * img_h
        ix, iy = int(round(x)), int(round(y))
        iw, ih = int(round(bw)), int(round(bh))
        if iw < 8 or ih < 8:
            continue
        boxes.append((ix, iy, iw, ih))
    return boxes


def detect_hybrid(
    image_path: str,
    reading_order: str = "ltr",
    min_area_ratio: float = 0.015,
    max_area_ratio: float = 0.92,
    use_yolo: bool = True,
    use_vision: bool = False,
    vision_model: Optional[str] = None,
    progress=None,
) -> Tuple[List[PanelBox], str]:
    """YOLO → OpenCV → isteğe bağlı Vision. (boxes, engine_used)"""
    from core.panel_detector import PanelDetector, nms_boxes

    def _p(msg: str) -> None:
        if progress:
            progress(msg)

    detector = PanelDetector(min_area_ratio=min_area_ratio, max_area_ratio=max_area_ratio)
    boxes: List[PanelBox] = []
    used = "none"

    if use_vision:
        _p("Vision API panelleri isteniyor...")
        try:
            v_boxes = detect_vision_panels(image_path, model=vision_model)
            from PIL import Image as PILImage
            with PILImage.open(image_path) as im:
                vw, vh = im.size
            if v_boxes:
                boxes = detector.sort_reading_order(v_boxes, vh, vw, reading_order)
                used = "vision"
                logger.info("Vision %d panel dondurdu.", len(boxes))
                return boxes, used
            logger.warning("Vision JSON parse bos; OpenCV/YOLO yedek.")
        except Exception as exc:
            logger.warning("Vision panel hatasi: %s", exc)

    _p("OpenCV taranıyor...")
    cv_boxes = detector.detect_from_path(image_path, reading_order=reading_order)
    used = "opencv"
    boxes = cv_boxes

    if use_yolo and yolo_available():
        _p("YOLO panelleri aranıyor (ilk seferde model inebilir)...")
        try:
            pil_img = __import__("PIL.Image", fromlist=["Image"]).open(image_path).convert("RGB")
            bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            pil_img.close()
            yolo_boxes = detect_yolo_panels(bgr)
            if len(yolo_boxes) >= 2:
                h, w = bgr.shape[:2]
                yolo_boxes = detector.sort_reading_order(yolo_boxes, h, w, reading_order)
                if len(yolo_boxes) >= len(cv_boxes):
                    boxes = yolo_boxes
                    used = "yolo"
                else:
                    boxes = detector.sort_reading_order(
                        nms_boxes(yolo_boxes + cv_boxes, iou_thresh=0.5), h, w, reading_order
                    )
                    used = "yolo+opencv"
        except Exception as exc:
            logger.warning("YOLO atlandi: %s", exc)

    return boxes, used


def detect_vision_panels(
    image_path: str,
    model: Optional[str] = None,
) -> List[PanelBox]:
    from core.openrouter_client import OpenRouterClient
    from core.settings_manager import SettingsManager
    from PIL import Image as PILImage

    settings = SettingsManager.instance()
    model = model or settings.get("defaults.vision_model", "google/gemini-2.5-flash")
    with PILImage.open(image_path) as im:
        img_w, img_h = im.size
    client = OpenRouterClient.instance()
    fallback = settings.get("api.vision_fallback_models", []) or []
    result = client.vision_analyze(
        model,
        image_path,
        VISION_PROMPT,
        fallback,
        max_tokens=4096,
        max_image_px=2048,
    )
    content = result.get("content") or ""
    if not content.strip():
        logger.warning("Vision bos yanit (model=%s usage=%s)", result.get("model"), result.get("usage"))
    else:
        logger.debug("Vision ham yanit (%d kr): %s", len(content), content[:800])
    boxes = parse_vision_boxes(content, img_w, img_h)
    if not boxes:
        logger.warning(
            "Vision JSON parse bos (model=%s, %d kr). Ornek: %s",
            result.get("model", model), len(content), content[:500],
        )
    logger.info("Vision panel: %d kutu (model=%s)", len(boxes), result.get("model", model))
    return boxes
