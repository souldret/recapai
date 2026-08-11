"""
RecapAI - TTS Cache sistemi.
Aynı text+voice+params kombinasyonu için ses yeniden üretilmez.
"""

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE_MB = 2048  # Varsayılan cache boyut limiti (2 GB)


class TTSCache:
    """
    TTS çıktılarını önbellekler.
    cache_dir: projects/{proj}/audio/.cache/

    Boyut limitini aşınca en uzun süre kullanılmamış (LRU) girdiler
    otomatik olarak temizlenir.
    """

    def __init__(self, cache_dir: str | Path, max_size_mb: float = DEFAULT_MAX_SIZE_MB) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_mb = max_size_mb
        self._index_path = self.cache_dir / "index.json"
        self._index: dict = self._load_index()
        self._migrate_index()
        logger.debug("TTSCache başlatıldı: %s (limit=%.0fMB)", self.cache_dir, max_size_mb)

    def _migrate_index(self) -> None:
        """Eski index formatını (key -> path) yeni formata (key -> {path, last_used}) taşır."""
        changed = False
        for key, value in list(self._index.items()):
            if isinstance(value, str):
                self._index[key] = {"path": value, "last_used": time.time()}
                changed = True
        if changed:
            self._save_index()

    # ── Index ──────────────────────────────────────────────────────

    def _load_index(self) -> dict:
        try:
            if self._index_path.exists():
                return json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Cache index yüklenemedi: %s", exc)
        return {}

    def _save_index(self) -> None:
        try:
            self._index_path.write_text(
                json.dumps(self._index, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Cache index kaydedilemedi: %s", exc)

    # ── Public API ─────────────────────────────────────────────────

    def get_cache_key(self, text: str, voice: str, params: dict) -> str:
        """text + voice + params kombinasyonundan deterministik hash üretir."""
        payload = json.dumps(
            {"text": text, "voice": voice, "params": params},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> Optional[str]:
        """Cache'de varsa ses dosyasının yolunu döner, yoksa None."""
        entry = self._index.get(key)
        if entry is None:
            return None
        cached_path = Path(entry["path"])
        if cached_path.exists():
            entry["last_used"] = time.time()
            self._save_index()
            logger.debug("Cache hit: %s", key)
            return str(cached_path)
        # Dosya silinmiş, index'ten temizle
        del self._index[key]
        self._save_index()
        return None

    def put(self, key: str, audio_path: str) -> None:
        """Ses dosyasını cache'e kopyalar ve index'e ekler."""
        src = Path(audio_path)
        if not src.exists():
            logger.warning("Cache put: kaynak dosya yok: %s", audio_path)
            return

        suffix = src.suffix or ".mp3"
        dst = self.cache_dir / f"{key}{suffix}"
        try:
            shutil.copy2(src, dst)
            self._index[key] = {"path": str(dst), "last_used": time.time()}
            self._save_index()
            logger.debug("Cache put: %s → %s", key, dst)
            self._evict_lru()
        except Exception as exc:
            logger.warning("Cache put hatası: %s", exc)

    def _evict_lru(self) -> None:
        """Cache boyutu limiti aşarsa en uzun süre kullanılmamış girdileri siler."""
        if self.max_size_mb <= 0:
            return
        try:
            if self.size_mb() <= self.max_size_mb:
                return

            entries = sorted(self._index.items(), key=lambda kv: kv[1].get("last_used", 0))
            for key, entry in entries:
                if self.size_mb() <= self.max_size_mb:
                    break
                path = Path(entry["path"])
                try:
                    path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Cache girdisi silinemedi (%s): %s", path, exc)
                del self._index[key]
                logger.debug("LRU eviction: %s silindi (%s)", key, path)

            self._save_index()
        except Exception as exc:
            logger.warning("LRU eviction hatası: %s", exc)

    def clear(self) -> None:
        """Tüm cache'i temizler (dosya ve alt dizinler dahil)."""
        try:
            for entry in self.cache_dir.iterdir():
                if entry.name == "index.json":
                    continue
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                except Exception as entry_exc:
                    logger.warning("Cache girdisi silinemedi (%s): %s", entry, entry_exc)
            self._index = {}
            self._save_index()
            logger.info("TTS cache temizlendi.")
        except Exception as exc:
            logger.error("Cache temizleme hatası: %s", exc)

    def size_mb(self) -> float:
        """Cache dizininin toplam boyutunu MB cinsinden döner."""
        total = sum(f.stat().st_size for f in self.cache_dir.rglob("*") if f.is_file())
        return total / (1024 * 1024)

    def entry_count(self) -> int:
        """Cache'deki giriş sayısı."""
        return len(self._index)

    @classmethod
    def get_global_cache(cls) -> "TTSCache":
        """
        Uygulama genelinde merkezi TTS cache döner.
        SettingsManager'dan cache dizinini okur.
        """
        try:
            from core.settings_manager import SettingsManager
            sm = SettingsManager.instance()
            if sm.get("cache.global_tts_cache", True):
                cache_dir = sm.get("cache.global_cache_dir", "./cache/tts")
                max_size_mb = sm.get("cache.tts_cache_max_size_mb", DEFAULT_MAX_SIZE_MB)
                from pathlib import Path
                from core.constants import BASE_DIR
                cache_path = BASE_DIR / cache_dir if not Path(cache_dir).is_absolute() else Path(cache_dir)
                return cls(str(cache_path), max_size_mb=max_size_mb)
        except Exception:
            pass
        # Fallback: geçici dizin
        import tempfile
        return cls(str(Path(tempfile.gettempdir()) / "recapai_tts_cache"))