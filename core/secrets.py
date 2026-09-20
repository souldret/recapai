"""
RecapAI - API anahtarı saklama.

Anahtar settings.json'a yazılmaz. Öncelik:
  1) OPENROUTER_API_KEY ortam değişkeni
  2) config/secrets.json (gitignored)
  3) Eski settings.json kaydı (bir kez secrets'e taşınır)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from core.constants import CONFIG_DIR

logger = logging.getLogger(__name__)

SECRETS_PATH = CONFIG_DIR / "secrets.json"
ENV_KEY = "OPENROUTER_API_KEY"


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _read_secrets() -> Dict[str, Any]:
    if not SECRETS_PATH.exists():
        return {}
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("secrets.json okunamadı: %s", exc)
        return {}


def load_dotenv() -> None:
    """Proje kökündeki .env dosyasını (varsa) ortama yükler."""
    env_path = CONFIG_DIR.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception as exc:
        logger.debug(".env yüklenemedi: %s", exc)


def get_openrouter_api_key(settings_fallback: str = "") -> str:
    load_dotenv()
    env = (os.environ.get(ENV_KEY) or "").strip()
    if env:
        return env
    stored = str(_read_secrets().get("openrouter_api_key") or "").strip()
    if stored:
        return stored
    return (settings_fallback or "").strip()


def set_openrouter_api_key(api_key: str) -> None:
    """Kullanıcı girdisini secrets.json'a yazar. Ortam değişkenini ezmez."""
    key = (api_key or "").strip()
    data = _read_secrets()
    data["openrouter_api_key"] = key
    try:
        _atomic_write(SECRETS_PATH, json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.error("API anahtarı kaydedilemedi: %s", exc)
        raise


def migrate_settings_secret(settings: Dict[str, Any]) -> Dict[str, Any]:
    """settings.json içindeki eski anahtarı secrets'e taşıyıp bellek kopyasından siler."""
    api = settings.get("api")
    if not isinstance(api, dict):
        return settings
    legacy = str(api.get("openrouter_api_key") or "").strip()
    if legacy and not str(_read_secrets().get("openrouter_api_key") or "").strip():
        try:
            set_openrouter_api_key(legacy)
            logger.info("OpenRouter anahtarı settings.json'dan secrets.json'a taşındı.")
        except Exception as exc:
            logger.warning("Anahtar taşınamadı, settings içinde bırakıldı: %s", exc)
            return settings
    api["openrouter_api_key"] = ""
    return settings


def strip_secrets_for_disk(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Disk'e yazılacak kopyadan gizli alanları çıkarır."""
    import copy
    out = copy.deepcopy(settings)
    api = out.get("api")
    if isinstance(api, dict):
        api["openrouter_api_key"] = ""
        api["elevenlabs_api_key"] = ""
        api["openai_api_key"] = ""
    return out
