# 🎬 RecapAI - Manhwa Recap Generator

Manhwa/manga görsellerinden AI destekli recap videoları üreten masaüstü uygulaması.

## Kurulum

```bash
# 1. Sanal ortam oluştur
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 2. Bağımlılıkları yükle
pip install -r requirements.txt

# 3. Ortam değişkenlerini ayarla
copy .env.example .env
# .env dosyasını açıp OPENROUTER_API_KEY değerini girin

# 4. Uygulamayı başlat
python main.py
```

## Gereksinimler

- Python 3.10–3.12 (3.13 önerilmez; Torch/Kokoro henüz tam uyumlu değil)
- PyQt6 6.7.0
- OpenRouter API anahtarı (https://openrouter.ai) — `.env` veya Ayarlar ekranı; `settings.json`'a yazılmaz
- FFmpeg (video render)

Opsiyonel yerel TTS (Kokoro): `python tools/install_dependencies.py`

Test: `pytest tests/ -q` (GitHub Actions: Python 3.10–3.12 + FFmpeg)

Windows paketi (PyInstaller): `tools\build_windows.bat` → `dist\RecapAI\RecapAI.exe`

## Özellikler

- Manhwa/manga görsel yükleme, panel tespiti
- AI görsel analizi (OpenRouter: Gemini, Claude, GPT-4o)
- Niş modüllü script üretimi (power fantasy, romance, dark action, comedy)
- Edge TTS + opsiyonel Kokoro
- FFmpeg render (Ken Burns, xfade, altyazı, BGM, watermark)
- YouTube 1080p / Shorts 9:16 / 4K look preset'leri, gece render kuyruğu
- Karakter kadrosu (seri bible), last-time köprüsü, A/B kanca, lint düzeltme döngüsü

## Dizin Yapısı

```
RecapAI/
├── main.py           # Giriş noktası
├── config/           # Ayarlar, promptlar, QSS temalar
├── core/             # İş mantığı
├── ui/               # PyQt sayfaları ve worker'lar
├── tests/            # pytest
├── tools/            # Torch/CUDA kurulum
├── projects/         # Kullanıcı projeleri (gitignored)
└── logs/             # Uygulama logları (gitignored)
```