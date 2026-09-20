"""
RecapAI - SettingsManager (Singleton).
Tüm uygulama bu sınıftan settings okur/yazar.
Runtime'da değişiklikler anında tüm modüllere yansır.
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

from core.constants import SETTINGS_PATH
from core.secrets import (
    get_openrouter_api_key,
    migrate_settings_secret,
    set_openrouter_api_key,
    strip_secrets_for_disk,
)


class SettingsManager(QObject):
    """Settings yöneticisi. DI ile kullanılır, instance() geriye dönük uyumluluk sağlar."""

    # Sinyaller
    settings_changed = pyqtSignal(dict)   # tüm settings değişti
    api_key_changed  = pyqtSignal(str)    # sadece API key değişti

    _instance: 'SettingsManager | None' = None

    def __init__(self) -> None:
        super().__init__()
        self._settings: Dict = {}
        SettingsManager._instance = self
        self.load()

    @classmethod
    def instance(cls) -> 'SettingsManager':
        """Geriye dönük uyumluluk için global instance döner."""
        if cls._instance is None:
            raise RuntimeError("SettingsManager henüz oluşturulmadı. main.py'den başlatın.")
        return cls._instance

    # ── Disk I/O ───────────────────────────────────────────────────

    def load(self) -> Dict:
        """settings.json'dan belleğe yükle; yeni varsayılan anahtarları birleştir."""
        defaults = self._default_settings()
        try:
            if SETTINGS_PATH.exists():
                disk = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                if not isinstance(disk, dict):
                    disk = {}
                self._settings = migrate_settings_secret(self._deep_merge(defaults, disk))
                logger.info("SettingsManager: ayarlar yüklendi (%s)", SETTINGS_PATH)
            else:
                self._settings = defaults
                self.save()
                logger.info("SettingsManager: varsayılan ayarlar oluşturuldu.")
        except Exception as exc:
            logger.error("SettingsManager yükleme hatası: %s", exc)
            self._settings = defaults
        return self._settings

    def reload(self) -> Dict:
        """Disk'ten yeniden yükle (race-condition guard)."""
        return self.load()

    def save(self) -> bool:
        """Belleği settings.json'a atomik yazar; gizli anahtarlar diske gitmez."""
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                strip_secrets_for_disk(self._settings),
                indent=2,
                ensure_ascii=False,
            )
            tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(SETTINGS_PATH)
            logger.info("SettingsManager: ayarlar kaydedildi.")
            self.settings_changed.emit(copy.deepcopy(self._settings))
            return True
        except Exception as exc:
            logger.error("SettingsManager kaydetme hatası: %s", exc)
            return False

    # ── Okuma / Yazma ──────────────────────────────────────────────

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Nokta-ayrımlı path ile değer oku.
        Örnek: get("api.openrouter_api_key")
        """
        keys = key_path.split(".")
        node = self._settings
        try:
            for k in keys:
                node = node[k]
            if key_path == "api.openrouter_api_key":
                stored = node if isinstance(node, str) else ""
                return get_openrouter_api_key(stored)
            return node
        except (KeyError, TypeError):
            if key_path == "api.openrouter_api_key":
                return get_openrouter_api_key("")
            return default

    def set(self, key_path: str, value: Any, save: bool = True) -> None:
        """
        Nokta-ayrımlı path'e değer yaz.
        save=True ise disk'e de yazar ve sinyalleri yayınlar.
        """
        keys = key_path.split(".")
        node = self._settings

        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]

        old_value = node.get(keys[-1])
        if key_path == "api.openrouter_api_key":
            try:
                set_openrouter_api_key(str(value or ""))
            except Exception as exc:
                logger.error("API anahtarı secrets'e yazılamadı: %s", exc)
            node[keys[-1]] = ""
            if save:
                self.save()
            if str(old_value or "") != str(value or ""):
                self.api_key_changed.emit(str(value or ""))
                logger.info("SettingsManager: API key değişti (sinyal yayınlandı).")
            return

        node[keys[-1]] = value
        if save:
            self.save()

    def get_all(self) -> Dict:
        """Tüm ayarların derin kopyasını döner (shallow copy aliasing'i önler)."""
        return copy.deepcopy(self._settings)

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """
        İki dict'i derinlemesine birleştirir.
        override'daki her iç içe dict, base'deki ilgili dict ile merge edilir;
        üst düzey update() gibi iç dict'leri silmez.
        """
        result = base.copy()
        for key, val in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = SettingsManager._deep_merge(result[key], val)
            else:
                result[key] = val
        return result

    def update_from_dict(self, data: Dict, save: bool = True) -> None:
        """
        Birden fazla ayarı dict olarak güncelle (derin birleştirme).
        Kısmi dict geçirildiğinde mevcut iç içe anahtarlar korunur.
        """
        old_key = self.get_api_key()
        incoming_key = ""
        api = data.get("api") if isinstance(data, dict) else None
        if isinstance(api, dict) and "openrouter_api_key" in api:
            incoming_key = str(api.get("openrouter_api_key") or "").strip()
            api = dict(api)
            api["openrouter_api_key"] = ""
            data = dict(data)
            data["api"] = api
        self._settings = self._deep_merge(self._settings, data)
        if incoming_key:
            try:
                set_openrouter_api_key(incoming_key)
            except Exception as exc:
                logger.error("API anahtarı secrets'e yazılamadı: %s", exc)
        if save:
            self.save()
        new_key = self.get_api_key()
        if old_key != new_key:
            self.api_key_changed.emit(new_key)

    # ── API Key kısayolları ────────────────────────────────────────

    def get_api_key(self) -> str:
        fallback = self.get("api.openrouter_api_key", "")
        fallback = fallback.strip() if isinstance(fallback, str) else ""
        return get_openrouter_api_key(fallback)

    def set_api_key(self, api_key: str) -> None:
        self.set("api.openrouter_api_key", api_key.strip())

    def has_api_key(self) -> bool:
        return len(self.get_api_key()) > 10

    # ── Varsayılan ayarlar ─────────────────────────────────────────

    @staticmethod
    def _default_settings() -> Dict:
        return {
            "app": {
                "name": "RecapAI",
                "version": "1.1.0",
                "language": "tr",
                "log_level": "INFO",
            },
            "api": {
                "openrouter_api_key": "",
                "openrouter_base_url": "https://openrouter.ai/api/v1",
                "elevenlabs_api_key": "",
                "openai_api_key": "",
                # Birincil model başarısız olursa (404/no-endpoints/5xx/429)
                # sırayla denenecek alternatif modeller.
                "vision_fallback_models": ["google/gemini-2.5-flash"],
                "script_fallback_models": ["google/gemini-2.5-flash"],
                "economy_vision_model": "google/gemini-2.5-flash-lite",
                "premium_script_model": "",
            },
            "analysis": {
                "rate_limit_delay": 1.0,
                "skip_low_score_fillers": True,
                "filler_score_threshold": 0.28,
            },
            "paths": {
                "projects_dir": "./projects",
                "output_dir": "./output",
                "logs_dir": "./logs",
            },
            "defaults": {
                "vision_model": "google/gemini-2.5-flash",
                "script_model": "anthropic/claude-sonnet-4",
                "tts_engine": "edge-tts",
                "tts_voice": "tr-TR-AhmetNeural",
            },
            "tts": {
                "default_speed": 1.0,
                "kokoro_cache_path": "",
                "kokoro_use_gpu": True,
                "remove_silence": True,
                "silence_thresh_db": -42.0,
                "min_silence_len_ms": 700,
                "keep_silence_ms": 220,
                "mix_engines": False,
                "narrator_engine": "kokoro",
                "narrator_voice": "am_adam",
                "dialogue_engine": "edge-tts",
                "dialogue_voice": "en-US-AndrewNeural",
            },
            "cache": {
                "global_tts_cache": True,  # Uygulama genelinde tek TTS cache
                "global_cache_dir": "./cache/tts",  # Merkezi cache klasörü
                "tts_cache_max_size_mb": 2048,  # LRU boyut limiti (MB)
            },
        }