"""
RecapAI - TTS Engine katmanı.
EdgeTTSEngine (online, Microsoft), KokoroTTSEngine (offline, local), TTSManager.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.text_utils import normalize_caps

logger = logging.getLogger(__name__)


# ── Temel Soyut Sınıf ─────────────────────────────────────────────────────────

class TTSEngine(ABC):
    """Tüm TTS motorları için ortak arayüz."""

    @abstractmethod
    def list_voices(self) -> List[Dict]:
        """Kullanılabilir seslerin listesini döner."""

    @abstractmethod
    def synthesize(self, text: str, voice: str, output_path: str, **kwargs) -> float:
        """
        Metni sese çevirir.
        Returns: Ses süresi (saniye). Hata durumunda 0.0.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Engine kullanılabilir mi?"""

    def get_error(self) -> Optional[str]:
        """Varsa hata mesajını döner."""
        return None


# ── Edge TTS ──────────────────────────────────────────────────────────────────

class EdgeTTSEngine(TTSEngine):
    """
    Microsoft Edge TTS — Online, ücretsiz, çoklu dil.
    Gereksinim: edge-tts>=6.1.12
    """

    VOICE_LANG_FILTER = ("tr-TR", "en-US", "en-GB")

    RECOMMENDED = {
        "tr-TR-AhmetNeural": "Ahmet (TR, Erkek, Dramatik)",
        "tr-TR-EmelNeural":  "Emel (TR, Kadin, Yumusak)",
        "en-US-GuyNeural":   "Guy (EN-US, Erkek)",
        "en-US-JennyNeural": "Jenny (EN-US, Kadin)",
        "en-US-AndrewMultilingualNeural": "Andrew Multilingual (EN-US, Erkek)",
        "en-GB-RyanNeural":  "Ryan (EN-GB, Erkek)",
        "en-GB-SoniaNeural": "Sonia (EN-GB, Kadin)",
    }

    def __init__(self) -> None:
        self._cached_voices: Optional[List[Dict]] = None

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def list_voices(self) -> List[Dict]:
        if self._cached_voices is not None:
            return self._cached_voices
        if not self.is_available():
            logger.warning("edge-tts kurulu degil.")
            return []
        try:
            import edge_tts

            async def _fetch():
                return await edge_tts.list_voices()

            raw = asyncio.run(_fetch())
            voices = []
            for v in raw:
                locale = v.get("Locale", "")
                if not any(locale.startswith(f) for f in self.VOICE_LANG_FILTER):
                    continue
                short_name = v.get("ShortName", "")
                gender_raw = v.get("Gender", "Male")
                gender = "Erkek" if "Male" in gender_raw else "Kadin"
                if short_name in self.RECOMMENDED:
                    display = self.RECOMMENDED[short_name]
                else:
                    friendly = v.get("FriendlyName", short_name)
                    display = f"{friendly} ({locale}, {gender})"
                voices.append({
                    "id":       short_name,
                    "name":     display,
                    "language": locale,
                    "gender":   gender,
                    "engine":   "edge-tts",
                })
            voices.sort(key=lambda x: (0 if x["id"] in self.RECOMMENDED else 1, x["language"], x["name"]))
            self._cached_voices = voices
            logger.info("Edge-TTS: %d ses yuklendi.", len(voices))
            return voices
        except Exception as exc:
            logger.error("Edge-TTS ses listesi alinamadi: %s", exc)
            return self._fallback_voices()

    def _fallback_voices(self) -> List[Dict]:
        return [
            {"id": "tr-TR-AhmetNeural",  "name": "Ahmet (TR, Erkek, Dramatik)",      "language": "tr-TR", "gender": "Erkek", "engine": "edge-tts"},
            {"id": "tr-TR-EmelNeural",   "name": "Emel (TR, Kadin, Yumusak)",         "language": "tr-TR", "gender": "Kadin", "engine": "edge-tts"},
            {"id": "en-US-GuyNeural",    "name": "Guy (EN-US, Erkek)",                "language": "en-US", "gender": "Erkek", "engine": "edge-tts"},
            {"id": "en-US-JennyNeural",  "name": "Jenny (EN-US, Kadin)",              "language": "en-US", "gender": "Kadin", "engine": "edge-tts"},
            {"id": "en-US-AndrewMultilingualNeural", "name": "Andrew Multilingual (EN-US, Erkek)", "language": "en-US", "gender": "Erkek", "engine": "edge-tts"},
            {"id": "en-GB-RyanNeural",   "name": "Ryan (EN-GB, Erkek)",               "language": "en-GB", "gender": "Erkek", "engine": "edge-tts"},
            {"id": "en-GB-SoniaNeural",  "name": "Sonia (EN-GB, Kadin)",              "language": "en-GB", "gender": "Kadin", "engine": "edge-tts"},
        ]

    @staticmethod
    def _fix_rate(rate: str) -> str:
        """Edge-TTS rate formatını garantiye al: '+0%' gibi."""
        rate = str(rate).strip()
        if not rate.endswith("%"):
            rate = rate + "%"
        if not rate.startswith(("+", "-")):
            rate = "+" + rate
        return rate

    @staticmethod
    def _fix_pitch(pitch: str) -> str:
        """Edge-TTS pitch formatını garantiye al: '+0Hz' gibi."""
        pitch = str(pitch).strip()
        if not pitch.endswith("Hz"):
            pitch = pitch + "Hz"
        if not pitch.startswith(("+", "-")):
            pitch = "+" + pitch
        return pitch

    @staticmethod
    def _fix_volume(volume: str) -> str:
        """Edge-TTS volume formatını garantiye al: '+0%' gibi."""
        volume = str(volume).strip()
        if not volume.endswith("%"):
            volume = volume + "%"
        if not volume.startswith(("+", "-")):
            volume = "+" + volume
        return volume

    async def _synthesize_async(self, text: str, voice: str, output_path: str,
                                rate: str, pitch: str, volume: str) -> None:
        """Async synthesis — edge-tts>=7.0.0 uyumlu."""
        import edge_tts
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )
        await communicate.save(output_path)

    def synthesize(self, text: str, voice: str, output_path: str,
                   rate: str = "+0%", pitch: str = "+0Hz", volume: str = "+0%", **kwargs) -> float:
        """Synthesize with retry (403 hatasına karşı 3 deneme)."""
        import time

        if not self.is_available():
            raise RuntimeError("edge-tts kurulu degil. 'pip install --upgrade edge-tts' calistirin.")

        text = normalize_caps(text)
        rate   = self._fix_rate(rate)
        pitch  = self._fix_pitch(pitch)
        volume = self._fix_volume(volume)

        max_retries = 3
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                # Windows QThread uyumlu event loop
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        self._synthesize_async(text, voice, output_path, rate, pitch, volume)
                    )
                    loop.close()
                except RuntimeError:
                    asyncio.run(
                        self._synthesize_async(text, voice, output_path, rate, pitch, volume)
                    )

                # Dosya kontrolü
                p = Path(output_path)
                if not p.exists() or p.stat().st_size == 0:
                    raise RuntimeError("Cikti dosyasi bos veya olusturulamadi.")

                from core.audio_processor import get_duration
                duration = get_duration(output_path)
                logger.debug("Edge-TTS: '%s...' -> %s (%.1fs)", text[:30], output_path, duration)
                return duration

            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                logger.warning(
                    "Edge-TTS deneme %d/%d basarisiz: %s",
                    attempt + 1, max_retries, err_str,
                )
                if "403" in err_str:
                    wait = 3 + attempt * 2   # 3s, 5s, 7s
                    logger.info("403 hatasi — %ds bekleniyor...", wait)
                    time.sleep(wait)
                else:
                    time.sleep(1)

        raise RuntimeError(
            f"Edge-TTS {max_retries} denemede basarisiz: {last_error}\n\n"
            "Cozum onerileri:\n"
            "1. edge-tts paketini guncelleyin: pip install --upgrade edge-tts\n"
            "2. Internet baglantisinizi kontrol edin\n"
            "3. VPN/Proxy kullaniyorsaniz kapatin\n"
            "4. Birkas dakika bekleyip tekrar deneyin"
        )


# ── Kokoro TTS ────────────────────────────────────────────────────────────────

class KokoroTTSEngine(TTSEngine):
    """
    Kokoro TTS — Offline, local, yüksek kalite.
    Gereksinim: kokoro>=0.7.16, torch>=2.0.0, soundfile, numpy
    NOT: Türkçe desteklenmez.

    Singleton pattern: bagimlillik kontrolu sadece BIR kez yapilir,
    tekrar eden import/instantiation log spam olusturmaz.
    """

    # ── Singleton state ───────────────────────────────────────────
    _instance: Optional["KokoroTTSEngine"] = None
    _checked: bool = False
    _cls_available: bool = False
    _cls_error: Optional[str] = None

    SUPPORTED_LANGS: Dict[str, str] = {
        "a": "American English", "b": "British English", "e": "Spanish",
        "f": "French", "h": "Hindi", "i": "Italian", "j": "Japanese",
        "p": "Brazilian Portuguese", "z": "Mandarin Chinese",
    }

    VOICE_CATALOG: Dict[str, List[Dict]] = {
        "a": [
            # American English — Manhwa recap için önerilen sesler
            {"id": "af_heart",    "name": "Heart (Female, Warm)",           "gender": "F"},
            {"id": "af_bella",    "name": "Bella (Female, Professional)",    "gender": "F"},
            {"id": "af_nicole",   "name": "Nicole (Female, Casual)",         "gender": "F"},
            {"id": "af_sarah",    "name": "Sarah (Female, Narrator)",        "gender": "F"},
            {"id": "af_sky",      "name": "Sky (Female, Bright)",            "gender": "F"},
            {"id": "am_adam",     "name": "Adam (Male, Dramatic)",           "gender": "M"},
            {"id": "am_michael",  "name": "Michael (Male, Deep)",            "gender": "M"},
            {"id": "am_fenrir",   "name": "Fenrir (Male, Epic)",             "gender": "M"},
            {"id": "am_liam",     "name": "Liam (Male, Confident)",          "gender": "M"},
            {"id": "am_puck",     "name": "Puck (Male, Playful)",            "gender": "M"},
            {"id": "am_santa",    "name": "Santa (Male, Warm)",              "gender": "M"},
        ],
        "b": [
            # British English
            {"id": "bf_emma",     "name": "Emma (Female, British)",          "gender": "F"},
            {"id": "bf_isabella", "name": "Isabella (Female, British)",      "gender": "F"},
            {"id": "bf_alice",    "name": "Alice (Female, British, Soft)",   "gender": "F"},
            {"id": "bf_lily",     "name": "Lily (Female, British, Bright)",  "gender": "F"},
            {"id": "bm_george",   "name": "George (Male, British)",          "gender": "M"},
            {"id": "bm_lewis",    "name": "Lewis (Male, British)",           "gender": "M"},
            {"id": "bm_daniel",   "name": "Daniel (Male, British, Deep)",    "gender": "M"},
        ],
        "e": [
            # Spanish
            {"id": "ef_dora",     "name": "Dora (Female, Spanish)",          "gender": "F"},
            {"id": "em_alex",     "name": "Alex (Male, Spanish)",            "gender": "M"},
            {"id": "em_santa",    "name": "Santa (Male, Spanish, Warm)",     "gender": "M"},
        ],
        "f": [
            # French
            {"id": "ff_siwis",    "name": "Siwis (Female, French)",          "gender": "F"},
        ],
        "h": [
            # Hindi
            {"id": "hf_alpha",    "name": "Alpha (Female, Hindi)",           "gender": "F"},
            {"id": "hm_omega",    "name": "Omega (Male, Hindi)",             "gender": "M"},
        ],
        "i": [
            # Italian
            {"id": "if_sara",     "name": "Sara (Female, Italian)",          "gender": "F"},
            {"id": "im_nicola",   "name": "Nicola (Male, Italian)",          "gender": "M"},
        ],
        "j": [
            # Japanese
            {"id": "jf_alpha",        "name": "Alpha (Female, Japanese)",        "gender": "F"},
            {"id": "jf_gongitsune",   "name": "Gongitsune (Female, Japanese)",   "gender": "F"},
            {"id": "jf_nezuko",       "name": "Nezuko (Female, Japanese, Soft)", "gender": "F"},
            {"id": "jf_tebukuro",     "name": "Tebukuro (Female, Japanese)",     "gender": "F"},
            {"id": "jm_kumo",         "name": "Kumo (Male, Japanese)",           "gender": "M"},
            {"id": "jm_daichi",       "name": "Daichi (Male, Japanese, Deep)",   "gender": "M"},
        ],
        "p": [
            # Portuguese (Brazilian)
            {"id": "pf_dora",     "name": "Dora (Female, Portuguese)",       "gender": "F"},
            {"id": "pm_alex",     "name": "Alex (Male, Portuguese)",         "gender": "M"},
            {"id": "pm_santa",    "name": "Santa (Male, Portuguese, Warm)",  "gender": "M"},
        ],
        "z": [
            # Mandarin Chinese
            {"id": "zf_xiaobei",  "name": "Xiaobei (Female, Mandarin)",      "gender": "F"},
            {"id": "zf_xiaoni",   "name": "Xiaoni (Female, Mandarin, Soft)", "gender": "F"},
            {"id": "zm_yunjian",  "name": "Yunjian (Male, Mandarin)",        "gender": "M"},
            {"id": "zm_yunxi",    "name": "Yunxi (Male, Mandarin, Clear)",   "gender": "M"},
        ],
    }

    def __new__(cls) -> "KokoroTTSEngine":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # __init__ her cagrildiginda calisiyor; sadece ilk seferde kontrol et
        if KokoroTTSEngine._checked:
            return
        KokoroTTSEngine._checked = True
        self._pipelines: Dict[str, Any] = {}
        self._device: Optional[str] = None
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Kokoro bagimliliklarini kontrol et — SADECE BIR KEZ calisir."""
        try:
            import torch
            logger.debug("Torch version: %s", torch.__version__)

            _test = torch.tensor([1.0])
            del _test

            from kokoro import KPipeline  # noqa: F401

            KokoroTTSEngine._cls_available = True
            logger.info("Kokoro TTS: Hazir")

        except OSError as e:
            KokoroTTSEngine._cls_error = (
                "Torch DLL hatasi. Cozum:\n"
                "1. Visual C++ Redistributable kurun\n"
                "   (aka 'vc_redist.x64.exe' — Microsoft sitesinden)\n"
                "2. Torch'u yeniden kurun:\n"
                "   pip uninstall torch\n"
                "   pip install torch==2.3.1 --index-url https://download.pytorch.org/whl/cpu\n"
                "3. Ya da calistirin: python tools/install_dependencies.py"
            )
            logger.error("Kokoro torch DLL hatasi (tek sefer): %s", e)

        except ImportError as e:
            pkg = str(e).replace("No module named ", "").strip("'")
            KokoroTTSEngine._cls_error = (
                f"Eksik paket: {pkg}\n"
                "Cozum: pip install kokoro torch soundfile numpy"
            )
            logger.error("Kokoro import hatasi (tek sefer): %s", e)

        except Exception as e:
            KokoroTTSEngine._cls_error = f"Kokoro yuklenemedi: {e}"
            logger.error("Kokoro hatasi (tek sefer): %s", e)

    def is_available(self) -> bool:
        return KokoroTTSEngine._cls_available

    def get_error(self) -> Optional[str]:
        return KokoroTTSEngine._cls_error

    def _get_device(self) -> str:
        if self._device is None:
            try:
                import torch
                cuda_ok = torch.cuda.is_available()
            except (ImportError, OSError, Exception):
                cuda_ok = False

            use_gpu_setting = True
            try:
                from core.settings_manager import SettingsManager
                use_gpu_setting = bool(SettingsManager.instance().get("tts.kokoro_use_gpu", True))
            except Exception:
                pass

            self._device = "cuda" if (cuda_ok and use_gpu_setting) else "cpu"
        return self._device

    def _get_pipeline(self, lang_code: str):
        if lang_code not in self._pipelines:
            from kokoro import KPipeline
            device = self._get_device()
            logger.info("Kokoro pipeline olusturuluyor: lang_code='%s' device='%s'", lang_code, device)
            self._pipelines[lang_code] = KPipeline(lang_code=lang_code, device=device)
            logger.info("Kokoro pipeline hazir: lang_code='%s'", lang_code)
        return self._pipelines[lang_code]

    def list_voices(self) -> List[Dict]:
        result = []
        for lang_code, voices in self.VOICE_CATALOG.items():
            lang_name = self.SUPPORTED_LANGS.get(lang_code, lang_code)
            for v in voices:
                result.append({"id": v["id"], "name": v["name"], "language": lang_name,
                                "lang_code": lang_code, "gender": v["gender"], "engine": "kokoro"})
        return result

    @classmethod
    def _resolve_voice(cls, voice: str) -> str:
        """Eski/geçersiz ses id'lerini HuggingFace'de olanlara çevirir."""
        aliases = {
            "am_fable": "am_fenrir",
            "am_onyx": "am_fenrir",
            "am_echo": "am_liam",
            "af_alloy": "af_heart",
            "af_nova": "af_sky",
            "bm_fable": "bm_george",
        }
        resolved = aliases.get((voice or "").strip(), voice)
        known = {v["id"] for voices in cls.VOICE_CATALOG.values() for v in voices}
        if resolved not in known:
            logger.warning("Kokoro ses '%s' yok, am_fenrir kullanılıyor.", voice)
            return "am_fenrir"
        if resolved != voice:
            logger.info("Kokoro ses '%s' → '%s' (HuggingFace'de yok).", voice, resolved)
        return resolved

    @staticmethod
    def _coerce_speed(speed=None, **kwargs) -> float:
        """
        Hız parametresini float'a çevirir.
        Kabul: speed=1.2, speed='1.2', rate='+20%' (Edge formatı yedek).
        """
        val = speed
        if val is None:
            val = kwargs.get("speed", None)
        if val is None and "rate" in kwargs:
            # Edge-TTS rate: '+20%' → 1.20
            rate = str(kwargs.get("rate", "+0%")).strip().replace("%", "")
            try:
                val = 1.0 + (float(rate) / 100.0)
            except (TypeError, ValueError):
                val = 1.0
        try:
            val = float(val)
        except (TypeError, ValueError):
            val = 1.0
        # Kokoro pratik aralık
        if val < 0.5:
            val = 0.5
        if val > 2.0:
            val = 2.0
        return val

    @staticmethod
    def _chunk_to_numpy(audio, np):
        """Kokoro Result/tensor/ndarray ses parçasını numpy float32'ye çevirir."""
        if audio is None:
            return None
        # torch tensor
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        elif hasattr(audio, "cpu") and hasattr(audio, "numpy"):
            audio = audio.cpu().numpy()
        arr = np.asarray(audio, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        if arr.size == 0:
            return None
        return arr

    def synthesize(self, text: str, voice: str, output_path: str, speed: float = 1.0, **kwargs) -> float:
        if not KokoroTTSEngine._cls_available:
            error = KokoroTTSEngine._cls_error or "Kokoro kullanamıyor."
            raise RuntimeError(f"Kokoro TTS kullanamıyor:\n{error}")

        import numpy as np
        import soundfile as sf

        text = normalize_caps(text)
        speed = self._coerce_speed(speed, **kwargs)
        voice = self._resolve_voice(voice)
        lang_code = voice[0] if voice else "a"
        if lang_code not in self.SUPPORTED_LANGS:
            logger.warning("Bilinmeyen lang_code '%s', 'a' kullaniliyor.", lang_code)
            lang_code = "a"

        pipeline = self._get_pipeline(lang_code)
        audio_chunks = []
        try:
            logger.info("Kokoro synthesize: voice=%s speed=%.2f text_len=%d", voice, speed, len(text))
            generator = pipeline(text=text, voice=voice, speed=float(speed))
            for item in generator:
                # Result.audio veya (gs, ps, audio) uyumu
                if hasattr(item, "audio"):
                    audio = item.audio
                elif isinstance(item, (tuple, list)) and len(item) >= 3:
                    audio = item[2]
                else:
                    audio = item
                arr = self._chunk_to_numpy(audio, np)
                if arr is not None:
                    audio_chunks.append(arr)
        except Exception as exc:
            raise RuntimeError(f"Kokoro sentez hatasi: {exc}") from exc

        if not audio_chunks:
            raise RuntimeError("Kokoro hic ses uretmedi.")

        full_audio = np.concatenate(audio_chunks)
        sample_rate = 24000
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        wav_path = output_path.replace(".mp3", ".wav") if output_path.endswith(".mp3") else output_path + ".wav"
        sf.write(wav_path, full_audio, sample_rate)

        if output_path.endswith(".mp3"):
            from core.audio_processor import convert_to_mp3
            ok = convert_to_mp3(wav_path, output_path)
            if ok:
                Path(wav_path).unlink(missing_ok=True)
            else:
                shutil.move(wav_path, output_path)
        else:
            shutil.move(wav_path, output_path)

        from core.audio_processor import get_duration
        duration = get_duration(output_path)
        logger.info(
            "Kokoro: speed=%.2f '%s...' -> %s (%.1fs)",
            speed, text[:30], output_path, duration,
        )
        return duration

    def download_model(self, progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        if not KokoroTTSEngine._cls_available:
            return False
        try:
            if progress_callback:
                progress_callback("Model indiriliyor (~350MB), lutfen bekleyin...")
            self._get_pipeline("a")
            if progress_callback:
                progress_callback("Model basariyla indirildi.")
            return True
        except Exception as exc:
            logger.error("Model indirme hatasi: %s", exc)
            if progress_callback:
                progress_callback(f"Hata: {exc}")
            return False

    def is_model_downloaded(self) -> bool:
        try:
            hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
            if not hf_cache.exists():
                return False
            for folder in hf_cache.iterdir():
                if "kokoro" in folder.name.lower():
                    return True
            return False
        except Exception:
            return False


# ── TTS Manager ───────────────────────────────────────────────────────────────

class TTSManager:
    """Tüm TTS engine'leri yöneten merkezi sınıf. Singleton."""

    _instance: Optional["TTSManager"] = None

    def __init__(self) -> None:
        self.engines: Dict[str, TTSEngine] = {
            "edge-tts": EdgeTTSEngine(),
            "kokoro":   KokoroTTSEngine(),
        }
        logger.debug("TTSManager baslatildi.")

    @classmethod
    def get_instance(cls) -> "TTSManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_engine(self, name: str) -> TTSEngine:
        engine = self.engines.get(name)
        if engine is None:
            logger.warning("Bilinmeyen engine '%s', edge-tts kullaniliyor.", name)
            engine = self.engines["edge-tts"]
        return engine

    def get_available_engines(self) -> List[Dict]:
        kokoro_engine = self.engines["kokoro"]
        kokoro_available = kokoro_engine.is_available()
        kokoro_error = kokoro_engine.get_error() if not kokoro_available else None

        return [
            {
                "id": "edge-tts",
                "name": "Edge-TTS (Online, Ucretsiz)",
                "available": self.engines["edge-tts"].is_available(),
                "description": "Microsoft Azure tabanlı, 400+ ses, çok dilli. İnternet gerektirir.",
                "error": None,
            },
            {
                "id": "kokoro",
                "name": "Kokoro TTS (Offline, Yuksek Kalite)",
                "available": kokoro_available,
                "description": "Açık kaynak, local model, GPU/CPU. İnternet gerektirmez. (~350MB model)",
                "error": kokoro_error,
            },
        ]

    def synthesize_chapter(self, chapter, engine_name: str, voice: str,
                           progress_callback: Optional[Callable[[int, int, str], None]] = None,
                           cache=None, **kwargs) -> None:
        engine = self.get_engine(engine_name)
        segments = chapter.segments
        total = len(segments)
        audio_dir = kwargs.pop("audio_dir", Path("projects") / "audio" / chapter.id)
        audio_dir = Path(audio_dir)
        audio_dir.mkdir(parents=True, exist_ok=True)

        for i, segment in enumerate(segments):
            if not segment.text.strip():
                continue
            # Normalize önce yapılır — cache key ve synthesis hep normalize metin üzerinden
            tts_text = normalize_caps(segment.text)
            cache_params = {k: v for k, v in kwargs.items()}
            cache_params["voice"] = voice
            cache_params["engine"] = engine_name
            cache_key = None
            if cache is not None:
                cache_key = cache.get_cache_key(tts_text, voice, cache_params)
                cached = cache.get(cache_key)
                if cached:
                    segment.audio_path = cached
                    from core.audio_processor import get_duration
                    segment.duration = get_duration(cached)
                    if progress_callback:
                        progress_callback(i + 1, total, f"[Cache] Segment {i+1}/{total}")
                    continue
            output_path = str(audio_dir / f"segment_{i:04d}.mp3")
            try:
                if progress_callback:
                    progress_callback(i + 1, total, f"Sentezleniyor: Segment {i+1}/{total}")
                duration = engine.synthesize(text=tts_text, voice=voice, output_path=output_path, **kwargs)
                segment.audio_path = output_path
                segment.duration = duration
                if cache is not None and cache_key:
                    cache.put(cache_key, output_path)
            except Exception as exc:
                logger.error("Segment %d seslendirme hatasi: %s", i, exc)
                segment.audio_path = None
                segment.duration = 0.0
                raise RuntimeError(
                    f"Segment {i + 1} seslendirilemedi: {exc}"
                ) from exc

        spoken = [s for s in segments if (s.text or "").strip()]
        missing = [s for s in spoken if not s.audio_path]
        if missing:
            raise RuntimeError(
                f"{len(missing)}/{len(spoken)} konuşma satırı seslendirilemedi."
            )

        if progress_callback:
            progress_callback(total, total, "Tum segmentler tamamlandi.")

    def detect_language(self, text: str) -> str:
        try:
            from langdetect import detect
            return detect(text)
        except Exception:
            tr_chars = set("çğışöüÇĞİŞÖÜ")
            if any(c in tr_chars for c in text):
                return "tr"
            return "en"

    def recommend_engine(self, text: str) -> str:
        lang = self.detect_language(text)
        kokoro_engine = self.engines["kokoro"]
        if lang == "tr":
            return "edge-tts"
        if kokoro_engine.is_available():
            return "kokoro"
        return "edge-tts"