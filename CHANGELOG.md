# Changelog

Bu dosya RecapAI projesindeki önemli değişiklikleri listeler.

## [Yayınlanmamış]

### Eklendi
- **Silence Remover**: Seslendirmedeki (Kokoro/edge-tts) uzun sessiz aralıklar artık
  otomatik olarak kısaltılabiliyor. Ayarlar → Seslendirme sekmesinden
  "Sessiz bölümleri otomatik kırp" seçeneğiyle açılıp kapatılabilir.
  Kelimeler/cümleler arası doğal boşluk (`keep_silence`) korunur, sadece
  gereksiz uzun sessizlikler kısaltılır.

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
