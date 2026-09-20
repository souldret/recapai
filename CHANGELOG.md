# Changelog

Bu dosya RecapAI projesindeki önemli değişiklikleri listeler.

## [Yayınlanmamış]

### Değiştirildi
- Script artık **beat başına tek voiceover** üretir; diğer paneller kısa sessiz B-roll olarak videoda kalır. Analiz dump'ı ile doldurulmaz.
- Script sayfasındaki **Stil** seçicisi kaldırıldı; anlatım otomatik Manhwa Fresh YouTube recap stiline kilitlendi.
- Script Üret butonu birincil (indigo) stil aldı; kart editörü daha yüksek.
- Script ayar çubuğu tek sıkışık satırdan 4 satıra ayrıldı (bölüm/model, niş/süre, kancalar, aksiyonlar).

### Düzeltildi
- Orta/uzun script artık beat bütçesini doldurmak için kısa VO'yu modele yeniden yazdırır (~6 dk hedef).
- Silence remover cümle kuyruğunu kesmiyor (daha düşük eşik, daha uzun keep).
- Kokoro `am_fable` HuggingFace'de yok; `am_fenrir`'e yönlendirildi. Geçersiz sesler listeden çıktı.
- Sayfa başlığı (`PageHeader`) sabit 70px tavanı kaldırıldı; butonlar kesilmiyor.
- Analiz Başlat birincil indigo buton oldu.
- ASS altyazı satır sonu kaçışı `\N` (çok satırlı altyazı bozulmuyordu).
- Ana sayfa Türkçe karakterler: "Aç", "bölüm", "görsel".
- Sidebar sürüm etiketi v1.1.0 ile ayarlarla hizalandı.
- Light temada `ghostButton` stili eksikti.

### Eklendi
- Panel tespiti: YOLO-World (yerel) + OpenCV + isteğe bağlı Vision API (çalıştırmadan önce USD tahmini).
- Büyük stitch görsellerinde Pillow DecompressionBomb uyarısı kapatıldı.
- Render: görselin arkasına hafif drop shadow (yalnız letterbox boşluğu varken; tam kadrajda gölge yok).
- Blur arka plan daha güçlü (`boxblur` 20→32, vinyette 25→36).
- Manhwa Fresh: YouTube tutma katmanı (açılış kancası, orta rehook, son açık uç, stakes).
- Render look: hafif kontrast/doygunluk, unsharp, gerçek vinyet (vignette_blur/cinematic/gradient).
- Encode: CRF 18, medium preset, YouTube bitrate 12M, `+faststart`.

### Düzeltildi
- Manga görsellerinde silme/yeniden sıralama sonrası sayaç ve toplu panel listesi artık güncellenir.
- Ken Burns zoom artık 2x tuvalden kırpmıyor; önce tam kadraj letterbox, sonra tek ölçekle merkeze yakınlaşıyor (sağa/sola kayma yok).
- Tema yükleme artık çalışma dizinine değil `core.constants` yollarına bakıyor.
- README Python sürümü, TTS, dizin yapısı ve test komutu güncellendi.
- `pytest` `requirements.txt` içine eklendi.

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

### Değiştirildi
- **Görsel hareket (image motion) sadeleştirildi**: Render → Görsel Hareketi
  bölümündeki Slide (Top/Bottom/Left/Right), Large Pan ve Full Pan modları
  kaldırıldı; bu karmaşık overlay/split tabanlı filtre zincirleri render'ı
  yavaşlatıp bazı kombinasyonlarda bozuk çıktı üretiyordu. Artık yalnızca
  FFmpeg `zoompan` filtresine dayanan **Zoom In / Zoom Out** Ken Burns efekti
  destekleniyor; sahne geçişleri için mevcut `xfade` geçiş efektleri listesi
  (fade, wipe, slide, cover, diagonal vb.) kullanılmaya devam ediyor.

### Düzeltildi
- **Render %5'te donma (kritik)**: Arka plan efektlerinde (`blur`,
  `vignette_blur`, `cinematic`, `gradient_tb/lr`) kullanılan `boxblur=40:40`,
  `boxblur=30:30`, `boxblur=50:50` gibi filtre parametreleri hatalıydı —
  FFmpeg'de `boxblur`'un 2. parametresi "power" (filtrenin kaç kez üst üste
  uygulanacağı) anlamına gelir; radius ile aynı büyük değer verilince tek bir
  4 saniyelik klip ~44 saniyeye kadar sürüyor, çok segmentli render bu yüzden
  ilerleme çubuğunda "%5'te donmuş" gibi görünüyordu. Doğru küçük power
  değerleriyle (`20:2`, `15:2`, `25:2`) düzeltildi; blur içeren klipler artık
  ~3 saniyenin altında tamamlanıyor.
- **GPU render tespiti çalışmıyordu**: GPU encoder testi (`h264_nvenc`,
  `h264_qsv`, `h264_amf`) 64×64 piksellik bir test görüntüsü kullanıyordu.
  NVENC bu boyutu desteklemediği için ("Frame Dimension less than the minimum
  supported value") gerçekte çalışan bir GPU bile "kullanılamıyor" olarak
  raporlanıyor, Render → Codec listesinde hiç görünmüyordu. Test görüntüsü
  320×240'a çıkarılarak düzeltildi. Ayrıca Render → Codec bölümüne, GPU
  hızlandırmanın bu sistemde kullanılabilir olup olmadığını ve hangi
  encoder(lar)ın bulunduğunu gösteren bir durum bilgisi etiketi eklendi.
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
- Zoom In / Zoom Out Ken Burns modları, arka plan efektleriyle (blur,
  vignette_blur, cinematic, gradient_tb/lr) birlikte ve `compose_chapter`
  üzerinden tam bir render akışıyla test edildi; donma/performans sorunu
  gözlenmedi.
- GPU encoder tespiti (`get_available_gpu_encoders`) düzeltme sonrası
  NVENC'i doğru şekilde bulduğu doğrulandı.
- Script/AI analiz prompt sistemi (Universal Compression Engine + niş
  modülleri + Bölüm 1 Hook Layer) mevcut `config/prompts.json` yapılandırmasıyla
  gözden geçirildi, ek değişikliğe gerek görülmedi.
- BGM ducking, watermark ve intro/outro birleştirme mantığı render
  pipeline'ında doğru şekilde uygulandığı doğrulandı.
