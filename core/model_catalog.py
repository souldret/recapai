"""
RecapAI - OpenRouter model kataloğu yardımcıları.
config/models.json üzerinden vision/script modellerini yükler ve
combo etiketlerini (fiyat, öneri rozetleri) üretir.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MODELS_PATH = Path(__file__).resolve().parent.parent / "config" / "models.json"

# Öneri rozetleri (UI etiketleri)
BADGE_LABELS = {
    "best_value": "Fiyat/Performans",
    "budget": "Bütçe",
    "performance": "Performans",
    "free": "Ücretsiz",
    "recommended": "Önerilen",
}


def load_catalog() -> Dict[str, Any]:
    try:
        return json.loads(_MODELS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("models.json yüklenemedi: %s", exc)
        return {}


def get_models(category: str) -> List[Dict[str, Any]]:
    """category: 'vision_models' | 'script_models'"""
    data = load_catalog()
    models = data.get(category, [])
    return models if isinstance(models, list) else []


def format_model_label(model: Dict[str, Any], *, show_cost: bool = True) -> str:
    """
    Combo kutusu için okunabilir etiket.
    Örnek: "Gemini 2.5 Flash  ·  Fiyat/Performans  [$]"
    """
    name = model.get("name") or model.get("id") or "?"
    parts = [name]

    badges: List[str] = []
    # Çoklu rozet desteği
    rec = model.get("recommendation") or model.get("badge")
    if isinstance(rec, str) and rec:
        badges.append(BADGE_LABELS.get(rec, rec))
    elif isinstance(rec, list):
        for r in rec:
            badges.append(BADGE_LABELS.get(r, str(r)))

    if model.get("recommended") and "best_value" not in (rec if isinstance(rec, list) else [rec]):
        # Eski alan: recommended=true → Önerilen (best_value yoksa)
        if not rec:
            badges.append(BADGE_LABELS["recommended"])

    # tags alanı da desteklenir
    for tag in model.get("tags") or []:
        label = BADGE_LABELS.get(tag, None)
        if label and label not in badges:
            badges.append(label)

    if badges:
        parts.append(" · ".join(badges))

    if show_cost:
        cost = model.get("cost") or ""
        if cost:
            parts.append(f"[{cost}]")

    return "  ".join(parts)


def default_model_id(category: str) -> Optional[str]:
    """Önerilen (recommended veya best_value) model id'si."""
    models = get_models(category)
    for m in models:
        rec = m.get("recommendation")
        if m.get("recommended") or rec == "best_value" or (
            isinstance(rec, list) and "best_value" in rec
        ):
            return m.get("id")
    return models[0]["id"] if models else None


def model_tooltip(model: Dict[str, Any]) -> str:
    """Combo tooltip metni."""
    bits = []
    if model.get("description"):
        bits.append(model["description"])
    cost = model.get("cost")
    if cost:
        bits.append(f"Maliyet: {cost}")
    if model.get("max_tokens"):
        bits.append(f"Max tokens: {model['max_tokens']}")
    rec = model.get("recommendation")
    if rec:
        if isinstance(rec, list):
            bits.append("Rozet: " + ", ".join(BADGE_LABELS.get(r, r) for r in rec))
        else:
            bits.append("Rozet: " + BADGE_LABELS.get(rec, str(rec)))
    return " | ".join(bits)
