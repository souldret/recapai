"""
RecapAI - Kullanıcı tanımlı render preset'lerinin kaydı/yüklenmesi.

Render sayfasındaki tüm ayarlar (çözünürlük, codec, geçiş efektleri,
altyazı stili, BGM, watermark vb.) tek bir isimle kaydedilip daha sonra
tek tıkla geri yüklenebilir.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from core.constants import CONFIG_DIR

logger = logging.getLogger(__name__)

PRESETS_PATH = CONFIG_DIR / "render_presets.json"


def _load_all() -> Dict[str, Dict[str, Any]]:
    if not PRESETS_PATH.exists():
        return {}
    try:
        return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("render_presets.json okunamadı: %s", exc)
        return {}


def _save_all(presets: Dict[str, Dict[str, Any]]) -> None:
    try:
        PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRESETS_PATH.write_text(
            json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("render_presets.json kaydedilemedi: %s", exc)


def list_presets() -> List[str]:
    """Kayıtlı preset isimlerini alfabetik döner."""
    return sorted(_load_all().keys())


def get_preset(name: str) -> Dict[str, Any]:
    """Adı verilen preset'in ayar sözlüğünü döner (yoksa boş dict)."""
    return _load_all().get(name, {})


def save_preset(name: str, settings: Dict[str, Any]) -> None:
    """Ayarları verilen isimle kaydeder (varsa üzerine yazar)."""
    name = name.strip()
    if not name:
        raise ValueError("Preset adı boş olamaz.")
    presets = _load_all()
    presets[name] = settings
    _save_all(presets)
    logger.info("Render preset kaydedildi: %s", name)


def delete_preset(name: str) -> bool:
    """Preset'i siler. Silinmişse True döner."""
    presets = _load_all()
    if name in presets:
        del presets[name]
        _save_all(presets)
        logger.info("Render preset silindi: %s", name)
        return True
    return False
