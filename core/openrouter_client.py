"""
RecapAI - OpenRouter API istemcisi (Singleton).
404 hata yönetimi, model doğrulama, hesap bilgisi desteği.
SettingsManager ile entegre: API key runtime'da güncellenir.
"""

import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from PIL import Image

from core.constants import OPENROUTER_BASE_URL, MODELS_PATH

logger = logging.getLogger(__name__)
MAX_IMAGE_PX = 1024
RETRY_COUNT = 3
RETRY_BASE_DELAY = 1.0    # saniye


def _message_text(msg: Any) -> str:
    """OpenRouter/Gemini message.content + reasoning alanlarini metne cevirir."""
    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg
    if not isinstance(msg, dict):
        return str(msg)
    chunks: List[str] = []

    def _take(val: Any) -> None:
        if not val:
            return
        if isinstance(val, str):
            chunks.append(val)
        elif isinstance(val, list):
            for part in val:
                if isinstance(part, str):
                    chunks.append(part)
                elif isinstance(part, dict):
                    chunks.append(str(part.get("text") or part.get("content") or ""))

    _take(msg.get("content"))
    _take(msg.get("reasoning"))
    _take(msg.get("reasoning_content"))
    _take(msg.get("reasoning_details"))
    return "\n".join(c for c in chunks if c).strip()


# ── İstisna ────────────────────────────────────────────────────────────────────

class OpenRouterError(Exception):
    """OpenRouter API hatası."""

    def __init__(self, message: str, status_code: int = 0,
                 response_data: Optional[dict] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


# ── İstemci ────────────────────────────────────────────────────────────────────

class OpenRouterClient:
    """
    OpenRouter API ile iletişim kurar.
    DI ile kullanılır; instance() geriye dönük uyumluluk sağlar.
    API key her istekte SettingsManager'dan okunur.

    Kullanım:
        client = OpenRouterClient.instance()
        result = client.vision_analyze("google/gemini-flash-1.5", "img.jpg", "...")
    """

    _instance: 'OpenRouterClient | None' = None

    def __init__(self, settings_manager, api_key: Optional[str] = None) -> None:
        self._settings_manager = settings_manager
        self._explicit_key: Optional[str] = api_key
        self._base_url = OPENROUTER_BASE_URL
        self._total_tokens = 0
        self._usage_events: List[Dict[str, Any]] = []
        self._session = requests.Session()
        self._api_models_cache: Optional[List[Dict]] = None
        OpenRouterClient._instance = self

        if self._settings_manager:
            try:
                self._settings_manager.api_key_changed.connect(self._on_api_key_changed)
                logger.info("OpenRouterClient: SettingsManager sinyaline bağlandı.")
            except Exception as exc:
                logger.warning("OpenRouterClient: SettingsManager bağlantısı başarısız: %s", exc)

    @classmethod
    def instance(cls) -> 'OpenRouterClient':
        """Geriye dönük uyumluluk için global instance döner."""
        if cls._instance is None:
            raise RuntimeError("OpenRouterClient henüz oluşturulmadı. main.py'den başlatın.")
        return cls._instance

    # ── Setup ──────────────────────────────────────────────────────

    def _on_api_key_changed(self, new_key: str) -> None:
        """SettingsManager'dan API key değişim sinyali geldi."""
        logger.info(
            "OpenRouterClient: API key güncellendi (uzunluk: %d).", len(new_key)
        )
        self._explicit_key = None   # artık settings'ten okusun
        self._api_models_cache = None  # cache'i temizle

    def _current_api_key(self) -> str:
        """
        Güncel API key'i döner.
        Öncelik: explicit_key (geriye dönük uyumluluk) > SettingsManager > disk.
        """
        if self._explicit_key:
            return self._explicit_key.strip()

        # SettingsManager üzerinden oku
        if self._settings_manager:
            return self._settings_manager.get_api_key()
        return ""

    def _current_base_url(self) -> str:
        if self._settings_manager:
            return self._settings_manager.get("api.openrouter_base_url", OPENROUTER_BASE_URL)
        return OPENROUTER_BASE_URL

    def _headers(self) -> Dict[str, str]:
        api_key = self._current_api_key()
        if not api_key:
            raise OpenRouterError(
                "API anahtarı ayarlanmamış. Ayarlar > API Ayarları bölümünden girin."
            )
        return {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://recapai.local",
            "X-Title": "RecapAI",
            "Content-Type": "application/json",
        }

    def _get_headers(self) -> Dict[str, str]:
        """Authorization gerektirmeyen GET istekleri için de header döner."""
        api_key = self._current_api_key()
        return {
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "HTTP-Referer": "https://recapai.local",
            "X-Title": "RecapAI",
        }

    def update_api_key(self, api_key: str) -> None:
        """API anahtarını çalışma zamanında günceller (geriye dönük uyumluluk)."""
        self._explicit_key = api_key.strip() if api_key else None
        # Cache'i temizle; yeni key farklı erişim haklarına sahip olabilir
        self._api_models_cache = None
        logger.info("OpenRouterClient.update_api_key: key güncellendi.")

    # ── Core Request ───────────────────────────────────────────────

    def _io_guard(self):
        """Paralel vision çağrıları aynı Session'ı paylaşmasın diye kilit."""
        lock = getattr(self, "_io_lock", None)
        if lock is None:
            import threading
            lock = threading.Lock()
            self._io_lock = lock
        return lock

    def _post(self, endpoint: str, payload: Dict) -> Dict:
        """Retry logic ile POST isteği gönderir."""
        with self._io_guard():
            return self._post_body(endpoint, payload)

    def _post_body(self, endpoint: str, payload: Dict) -> Dict:
        """Retry logic ile POST isteği gönderir."""
        base = self._current_base_url()
        url = f"{base}/{endpoint.lstrip('/')}"
        logger.debug(
            "_post: model=%s api_key_len=%d",
            payload.get("model", "?"),
            len(self._current_api_key()),
        )
        last_exc: Exception = OpenRouterError("Bilinmeyen hata")

        for attempt in range(RETRY_COUNT):
            try:
                resp = self._session.post(
                    url, headers=self._headers(),
                    json=payload, timeout=60,
                )

                if resp.status_code == 404:
                    err_data = self._safe_json(resp)
                    err_msg = self._extract_error_message(err_data)
                    model = payload.get("model", "bilinmeyen")
                    alt = self._suggest_alternative(model)
                    detail = (
                        f"Model '{model}' bulunamadı veya bu hesapla erişilemiyor.\n\n"
                        f"Olası nedenler:\n"
                        f"  • Model ID yanlış veya artık mevcut değil\n"
                        f"  • Hesabınızda yeterli kredi yok\n"
                        f"  • ':free' suffix'i bu model için geçersiz\n\n"
                        f"Önerilen alternatif: {alt}\n\n"
                        f"API yanıtı: {err_msg}"
                    )
                    logger.error("404 — model: %s | yanıt: %s", model, err_msg)
                    raise OpenRouterError(detail, status_code=404, response_data=err_data)

                if resp.status_code == 401:
                    model = payload.get("model", "")
                    provider = model.split("/")[0] if "/" in str(model) else str(model)
                    raise OpenRouterError(
                        f"Model '{model}' bu hesapla açılamadı (401).\n\n"
                        "Bu çoğu zaman API anahtarının tamamen yanlış olduğu anlamına gelmez.\n"
                        "Görsel analiz (Gemini) çalışıyorsa anahtar geçerlidir.\n\n"
                        "Sık neden:\n"
                        f"  • {provider.title()} modelleri OpenRouter gizlilik/data-policy onayı ister\n"
                        "    https://openrouter.ai/settings/privacy\n"
                        "  • Bu modele kredi/erişim yok\n\n"
                        "Çözüm: Script sayfasında Gemini 2.5 Flash seçin, ya da "
                        "OpenRouter Privacy ayarından Anthropic'i açın.",
                        status_code=401,
                        response_data={"model": model},
                    )

                if resp.status_code == 402:
                    raise OpenRouterError(
                        "Yetersiz kredi. OpenRouter hesabınıza kredi yükleyin.\n"
                        "https://openrouter.ai/credits",
                        status_code=402,
                    )

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = RETRY_BASE_DELAY * (2 ** attempt)
                    try:
                        if retry_after:
                            wait = max(wait, float(retry_after))
                    except (TypeError, ValueError):
                        pass
                    last_exc = OpenRouterError(
                        "Rate limit (429). Lütfen biraz bekleyip tekrar deneyin.",
                        status_code=429,
                    )
                    logger.warning("Rate limit, %.1f sn bekleniyor (deneme %d)…", wait, attempt + 1)
                    if attempt < RETRY_COUNT - 1:
                        time.sleep(wait)
                        continue
                    raise last_exc

                if resp.status_code >= 500:
                    if attempt < RETRY_COUNT - 1:
                        wait = RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "Server hatası %d, %.1f sn bekleniyor (deneme %d)…",
                            resp.status_code, wait, attempt + 1,
                        )
                        time.sleep(wait)
                        continue

                if resp.status_code >= 400:
                    err_data = self._safe_json(resp)
                    err_msg = self._extract_error_message(err_data)
                    raise OpenRouterError(err_msg, resp.status_code, err_data)

                data = resp.json()

                # Yanıtta gömülü hata var mı?
                if "error" in data:
                    err_msg = self._extract_error_message(data)
                    model = payload.get("model", "bilinmeyen")
                    # "No endpoints found" — model bu hesapta erişilemez
                    if "No endpoints found" in err_msg or "no endpoints" in err_msg.lower():
                        alt = self._suggest_alternative(model)
                        detail = (
                            f"Model '{model}' için endpoint bulunamadı.\n\n"
                            f"Olası nedenler:\n"
                            f"  • Model artık OpenRouter'da mevcut değil\n"
                            f"  • Hesabınızda bu modele erişim yok\n"
                            f"  • Model ID yanlış yazılmış\n\n"
                            f"Önerilen alternatif: {alt}\n\n"
                            f"Script sayfasından farklı bir model seçip tekrar deneyin."
                        )
                        raise OpenRouterError(detail, status_code=404, response_data=data)
                    raise OpenRouterError(f"API hatası: {err_msg}", response_data=data)

                self._track_tokens(data)
                return data

            except OpenRouterError:
                raise
            except requests.exceptions.Timeout:
                last_exc = OpenRouterError("İstek zaman aşımı (timeout).")
                logger.warning("Timeout, deneme %d/%d", attempt + 1, RETRY_COUNT)
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
            except requests.RequestException as exc:
                last_exc = OpenRouterError(f"Bağlantı hatası: {exc}")
                logger.warning(
                    "İstek hatası (deneme %d): %s — %.1f sn bekleniyor",
                    attempt + 1, exc, RETRY_BASE_DELAY * (2 ** attempt),
                )
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))

        raise last_exc

    def _post_with_fallback(
        self,
        endpoint: str,
        payload: Dict,
        fallback_models: Optional[List[str]] = None,
    ) -> Dict:
        """
        `_post` ile aynı işi yapar; ancak birincil model kalıcı olarak
        başarısız olursa (404 / "no endpoints" / rate-limit veya sunucu
        hatasının tüm denemeler tükendikten sonra sürmesi) `fallback_models`
        listesindeki modellere sırayla geçer.

        401 çoğu zaman anahtarın tamamen geçersiz olduğu anlamına gelmez:
        Anthropic/OpenAI modelleri gizlilik onayı veya model erişimi ister.
        Gemini gibi yedek modeller aynı anahtarla çalışabilir.
        402 (yetersiz kredi) hesap seviyesidir; fallback denenmez.
        """
        models_to_try = [payload["model"]] + [m for m in (fallback_models or []) if m != payload["model"]]
        last_error: Optional[OpenRouterError] = None
        auth_failures = 0

        for i, model in enumerate(models_to_try):
            attempt_payload = dict(payload, model=model)
            try:
                data = self._post(endpoint, attempt_payload)
                if i > 0:
                    logger.info("Model fallback başarılı: '%s' kullanıldı.", model)
                return data
            except OpenRouterError as exc:
                last_error = exc
                if exc.status_code == 402:
                    raise
                if exc.status_code == 401:
                    auth_failures += 1
                if i < len(models_to_try) - 1:
                    logger.warning(
                        "Model '%s' başarısız (%s), fallback deneniyor: '%s'",
                        model, exc.status_code or exc, models_to_try[i + 1],
                    )
                    continue
                if auth_failures == len(models_to_try):
                    raise OpenRouterError(
                        "API key geçersiz (401). Tüm modeller reddetti.\n"
                        "Ayarlar > API'den sk-or-v1-... anahtarını kaydedin.\n"
                        "https://openrouter.ai/keys",
                        status_code=401,
                    ) from exc
                raise

        raise last_error or OpenRouterError("Tüm modeller başarısız oldu.")

    def _safe_json(self, response: requests.Response) -> dict:
        """Response'u güvenli şekilde dict'e çevirir."""
        try:
            return response.json()
        except Exception:
            return {"raw_text": response.text[:500]}

    def _extract_error_message(self, data: dict) -> str:
        """OpenRouter hata yanıtından okunabilir mesaj çıkarır."""
        if not data:
            return "Bilinmeyen hata"
        if "error" in data:
            error = data["error"]
            if isinstance(error, dict):
                return error.get("message", str(error))
            return str(error)
        if "message" in data:
            return data["message"]
        if "raw_text" in data:
            return data["raw_text"][:300]
        return json.dumps(data)[:300]

    def _suggest_alternative(self, model_id: str) -> str:
        """Bulunamayan model için alternatif önerir."""
        # Önce yerel config'den bak
        try:
            data = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
            all_models = data.get("vision_models", []) + data.get("script_models", [])
            if "/" in model_id:
                provider = model_id.split("/")[0]
                for m in all_models:
                    if m["id"].startswith(f"{provider}/") and m["id"] != model_id:
                        return m["id"]
        except Exception:
            pass

        # Genel fallback
        return "google/gemini-2.5-flash"

    def _track_tokens(self, data: Dict) -> None:
        usage = data.get("usage", {}) or {}
        total = usage.get("total_tokens", 0)
        if total:
            self._total_tokens += total
            logger.debug("Token kullanımı: +%d (toplam: %d)", total, self._total_tokens)
        if usage:
            self._usage_events.append({
                "model": data.get("model") or "",
                "usage": usage,
            })
            if len(self._usage_events) > 400:
                self._usage_events = self._usage_events[-200:]

    # ── Chat Completion ────────────────────────────────────────────

    def chat_completion(
        self,
        model: str,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        fallback_models: Optional[List[str]] = None,
    ) -> Dict:
        """
        Standart chat tamamlama isteği gönderir.

        Args:
            fallback_models: Birincil model başarısız olursa sırayla
                denenecek alternatif model ID'leri.

        Returns:
            {"content": str, "model": str, "usage": dict}
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._post_with_fallback("chat/completions", payload, fallback_models)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = _message_text(msg) if isinstance(msg, dict) else _message_text(choice)
        return {
            "content": content or "",
            "model": data.get("model", model),
            "usage": data.get("usage", {}),
        }

    # ── Vision Analyze ─────────────────────────────────────────────

    def vision_analyze(
        self,
        model: str,
        image_path: str,
        prompt: str,
        fallback_models: Optional[List[str]] = None,
        max_tokens: int = 1024,
        max_image_px: Optional[int] = None,
        image_bytes: Optional[bytes] = None,
    ) -> Dict:
        """
        Görseli base64 encode edip vision modeline gönderir.

        Args:
            model: Kullanılacak vision modeli ID.
            image_path: Görsel dosya yolu.
            prompt: Analiz promptu.
            fallback_models: Birincil model başarısız olursa sırayla
                denenecek alternatif vision model ID'leri.

        Returns:
            {"content": str, "model": str, "usage": dict}
        """
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
        else:
            if not image_path:
                raise OpenRouterError("Vision icin gorsel yolu veya bayt gerekli.")
            b64 = self._encode_image(image_path, max_px=max_image_px)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ]
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        used_reasoning = False
        if "gemini" in (model or "").lower() or "thinking" in (model or "").lower():
            payload["reasoning"] = {"exclude": True}
            used_reasoning = True
        try:
            data = self._post_with_fallback("chat/completions", payload, fallback_models)
        except OpenRouterError as exc:
            if not used_reasoning or exc.status_code in (401, 402):
                raise
            payload.pop("reasoning", None)
            logger.info("Vision reasoning parametresi kaldırılıp yeniden deneniyor.")
            data = self._post_with_fallback("chat/completions", payload, fallback_models)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = _message_text(msg)
        if not content:
            content = _message_text(choice)
        if not content:
            logger.warning(
                "Vision yanit metni bos. keys=%s msg_keys=%s usage=%s",
                list(data.keys()), list(msg.keys()) if isinstance(msg, dict) else type(msg),
                data.get("usage"),
            )
        return {
            "content": content,
            "model": data.get("model", model),
            "usage": data.get("usage", {}),
        }

    def _encode_image(self, path: str, max_px: Optional[int] = None) -> str:
        """Görseli yükler, max_px'e küçültür, JPEG base64 döndürür."""
        limit = MAX_IMAGE_PX if max_px is None else max(256, int(max_px))
        try:
            with Image.open(path) as img:
                # RGBA/P → RGB dönüşümü
                if img.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                if max(img.size) > limit:
                    img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                    logger.debug("Görsel küçültüldü: %s → %s", path, img.size)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as exc:
            raise OpenRouterError(f"Görsel encode hatası ({path}): {exc}") from exc

    # ── Streaming ──────────────────────────────────────────────────

    def chat_stream(self, model: str, messages: List[Dict], max_tokens: int = 4000) -> Generator[str, None, None]:
        """
        SSE stream üzerinden metin chunk'ları yield eder.
        """
        if not self._current_api_key():
            raise OpenRouterError("API anahtarı ayarlanmamış.")

        url = f"{self._current_base_url()}/chat/completions"
        payload = {"model": model, "messages": messages, "stream": True, "max_tokens": max_tokens}

        with self._session.post(url, headers=self._headers(), json=payload,
                                stream=True, timeout=120) as resp:
            if resp.status_code >= 400:
                err_data = self._safe_json(resp)
                err_msg = self._extract_error_message(err_data)
                raise OpenRouterError(err_msg, resp.status_code, err_data)
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    text = text[6:]
                if text == "[DONE]":
                    break
                try:
                    chunk = json.loads(text)
                    # Stream içinde gömülü hata (ör. "No endpoints found")
                    if "error" in chunk:
                        err_msg = self._extract_error_message(chunk)
                        if "No endpoints found" in err_msg or "no endpoints" in err_msg.lower():
                            alt = self._suggest_alternative(model)
                            raise OpenRouterError(
                                f"{err_msg}\n\nÖnerilen alternatif: {alt}\n"
                                f"Script sayfasından farklı bir model seçip tekrar deneyin.",
                                response_data=chunk,
                            )
                        raise OpenRouterError(f"Stream hatası: {err_msg}", response_data=chunk)
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except OpenRouterError:
                    raise
                except (json.JSONDecodeError, KeyError):
                    continue

    # ── Models ─────────────────────────────────────────────────────

    def list_models(self, force_refresh: bool = False) -> List[Dict]:
        """
        OpenRouter API'den erişilebilir model listesini getirir.
        Cache'lenmiş sonuçları döner; force_refresh=True ile yeniler.
        Hata durumunda config/models.json'dan döner.
        """
        if self._api_models_cache is not None and not force_refresh:
            return self._api_models_cache

        if not self._current_api_key():
            return self._local_models()

        try:
            resp = self._session.get(
                f"{self._current_base_url()}/models",
                headers=self._get_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                self._api_models_cache = models
                logger.info("OpenRouter: %d model yüklendi.", len(models))
                return models
            else:
                logger.warning("Model listesi alınamadı: HTTP %d", resp.status_code)
        except Exception as exc:
            logger.error("Model listesi isteği başarısız: %s", exc)

        return self._local_models()

    def _local_models(self) -> List[Dict]:
        """config/models.json'dan yerel model listesini döner."""
        try:
            data = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
            return data.get("vision_models", []) + data.get("script_models", [])
        except Exception as exc:
            logger.warning("Yerel model listesi yüklenemedi: %s", exc)
            return []

    def validate_model(self, model_id: str) -> bool:
        """
        Modelin OpenRouter'da mevcut olup olmadığını kontrol eder.
        Model listesi alınamazsa True döner (şüphe avantajı).
        """
        models = self.list_models()
        if not models:
            return True
        model_ids = {m.get("id") for m in models}
        return model_id in model_ids

    # ── Test ───────────────────────────────────────────────────────

    def test_connection(self) -> Tuple[bool, str]:
        """
        Anahtarı gerçek bir chat isteğiyle doğrular.
        GET /models herkese açıktır; 200 dönmesi key'in geçerli olduğu anlamına gelmez.
        """
        if not self._current_api_key():
            return False, "API anahtarı tanımlı değil"

        try:
            resp = self._session.post(
                f"{self._current_base_url()}/chat/completions",
                headers=self._headers(),
                json={
                    "model": "google/gemini-2.5-flash",
                    "messages": [{"role": "user", "content": "Reply with OK"}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
                timeout=20,
            )
            if resp.status_code == 200:
                return True, "Anahtar geçerli (Gemini chat OK)"
            if resp.status_code == 401:
                return False, (
                    "Anahtar Gemini chat'i reddetti (401). "
                    "Yeni sk-or-v1-... key alın: https://openrouter.ai/keys"
                )
            if resp.status_code == 402:
                return False, "Yetersiz kredi. https://openrouter.ai/credits"
            err = self._extract_error_message(self._safe_json(resp))
            return False, f"HTTP {resp.status_code}: {err}"
        except requests.exceptions.ConnectionError:
            return False, "İnternet bağlantısı yok"
        except requests.exceptions.Timeout:
            return False, "Zaman aşımı (timeout)"
        except Exception as exc:
            return False, f"Hata: {exc}"

    # ── Account Info ───────────────────────────────────────────────

    def get_account_info(self) -> Optional[Dict]:
        """
        Hesap bilgilerini ve kalan krediyi döner.

        Returns:
            {"label": str, "usage": float, "limit": float|None, ...}
            Hata durumunda None.
        """
        if not self._current_api_key():
            return None

        try:
            resp = self._session.get(
                f"{self._current_base_url()}/auth/key",
                headers=self._get_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {})
            logger.warning("Hesap bilgisi alınamadı: HTTP %d", resp.status_code)
        except Exception as exc:
            logger.error("Hesap bilgisi isteği başarısız: %s", exc)
        return None

    # ── Stats ──────────────────────────────────────────────────────

    @property
    def total_tokens_used(self) -> int:
        """Bu oturumda kullanılan toplam token sayısı."""
        return self._total_tokens

    def usage_events(self) -> List[Dict[str, Any]]:
        return list(self._usage_events)

    def session_cost_usd(self) -> float:
        from core.costing import session_cost
        return float(session_cost(self._usage_events).get("usd") or 0.0)