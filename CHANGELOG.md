# Changelog

Bu dosya RecapAI projesindeki önemli değişiklikleri listeler.

## [Yayınlanmamış]

### Eklendi
- **Silence Remover**: Seslendirmedeki (Kokoro/edge-tts) uzun sessiz aralıklar artık
  otomatik olarak kısaltılabiliyor. Ayarlar → Seslendirme sekmesinden
  "Sessiz bölümleri otomatik kırp" seçeneğiyle açılıp kapatılabilir.
  Kelimeler/cümleler arası doğal boşluk (`keep_silence`) korunur, sadece
  gereksiz uzun sessizlikler kısaltılır.
- **GPU hızlandırmalı render**: Sistemde gerçekten çalışan NVIDIA (NVENC),
  Intel (QuickSync) veya AMD (AMF) GPU encoder'ı varsa Render → Codec
  listesinde otomatik olarak görünür ve seçilebilir. Encoder gerçek bir
  test-encode ile doğrulanır; sistemde kullanılamıyorsa otomatik olarak
  CPU (`libx264`) encoding'e düşülür.
- **TTS cache boyut limiti (LRU)**: Merkezi TTS önbelleği artık sınırsız
  büyümüyor. Ayarlardan (`cache.tts_cache_max_size_mb`, varsayılan 2048 MB)
  belirlenen limit aşıldığında en uzun süre kullanılmamış kayıtlar otomatik
  olarak silinir.
- **OpenRouter model fallback**: Script/analiz üretiminde seçili model
  başarısız olursa (model bulunamadı, kapasite dolu, sunucu hatası vb.)
  ayarlarda tanımlı yedek modele (`api.script_fallback_models`,
  `api.vision_fallback_models`) otomatik geçilir. Kimlik doğrulama/ödeme
  hatalarında (401/402) fallback denenmez, hata doğrudan bildirilir.
- **Render preset kaydet/yükle**: Render sayfasındaki tüm ayarlar (çözünürlük,
  codec, geçişler, altyazı stili, ses, intro/outro/watermark) istenen bir
  isimle kaydedilip toolbar'daki "Benim Preset'lerim" listesinden tek tıkla
  geri yüklenebilir veya silinebilir (`config/render_presets.json`).

### Düzeltildi
- **Render — Kutu geçiş efektleri**: "Kutu İçeri/Dışarı (Yatay/Dikey)" geçiş
  efektleri FFmpeg'in `xfade` filtresinde var olmayan isimlerle (`hboxin`,
  `hboxout`, `vboxin`, `vboxout`) tanımlanmıştı. Bu efektlerden biri seçildiğinde
  (özellikle "Tümünü Seç" ile) render başarısız oluyor veya çıktı bozuk
  oluyordu. Doğru FFmpeg filtre isimleriyle (`horzopen`, `horzclose`,
  `vertopen`, `vertclose`) düzeltildi.
- **Kokoro TTS — GPU/CPU seçimi**: Ayarlardaki "GPU kullan" seçeneği Kokoro
  pipeline'ına hiç iletilmiyordu, motor her zaman kütüphanenin varsayılan
  cihazını kullanıyordu. Artık `KPipeline` oluşturulurken ayarlardan okunan
  `device` (cuda/cpu) parametresi doğru şekilde geçiriliyor.
- **Güvenlik**: `config/settings.json` içinde saklanan OpenRouter API anahtarı,
  dosya `.gitignore`'da olmasına rağmen git geçmişinde hâlâ takip ediliyordu.
  Dosya git takibinden çıkarıldı (`git rm --cached`), yerel dosya korunuyor.

### Doğrulandı
- Görsel hareket (image motion) modlarının tümü (zoom in/out, slide
  top/bottom/left/right, large pan, full pan) tek başına ve tüm geçiş
  efektleriyle birlikte render edildiğinde sorunsuz çalıştığı test edildi.
- Script/AI analiz prompt sistemi (Universal Compression Engine + niş
  modülleri + Bölüm 1 Hook Layer) mevcut `config/prompts.json` yapılandırmasıyla
  gözden geçirildi, ek değişikliğe gerek görülmedi.
- BGM ducking, watermark ve intro/outro birleştirme mantığı render
  pipeline'ında doğru şekilde uygulandığı doğrulandı.
