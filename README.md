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

- Python 3.11+
- PyQt6 6.7.0
- OpenRouter API anahtarı (https://openrouter.ai)

## Özellikler

- 🖼️ Manhwa/manga görsel yükleme ve yönetimi
- 🤖 AI ile otomatik görsel analizi (Gemini, Claude, GPT-4o)
- 📝 Otomatik script oluşturma ve editör
- 🎙️ Türkçe TTS seslendirme (Edge TTS)
- 🎬 Video render ve dışa aktarma
- 🌙 Modern dark tema

## Dizin Yapısı

```
RecapAI/
├── main.py           # Giriş noktası
├── config/           # Ayarlar ve tema
├── core/             # İş mantığı (geliştiriliyor)
├── ui/               # Arayüz bileşenleri
│   ├── main_window.py
│   └── pages/        # Uygulama sayfaları
├── assets/           # İkonlar ve kaynaklar
├── projects/         # Kullanıcı projeleri
└── logs/             # Uygulama logları
```