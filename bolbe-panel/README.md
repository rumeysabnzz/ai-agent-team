# BOLBÉ AI Komuta Merkezi

BOLBÉ Skincare'in 4 takım, 12 ajandan oluşan AI pazarlama ekibi ve ekibi canlı izleyip yönettiğiniz web paneli. Railway'de tek servis olarak çalışır. Ajanlar doğrudan Claude API'yi kullanır; Hermes gerekmez.

## Ekip

| Takım | Ajanlar | Düzenli iş |
|-------|---------|------------|
| **1 · Rakip Analiz** | İçerik Tarayıcı → Strateji Analisti → Erken Uyarı Ajanı | Her Pazartesi 08:00 rakip taraması, uyarılar, haftalık rakip raporu |
| **2 · Marka Analiz ve Strateji** | Performans Analisti → Hedef Kitle Analisti → Strateji Plancısı | Her Pazartesi 11:00 haftalık içerik takvimi; her ayın 1'i 09:00 aylık strateji planı |
| **3 · İçerik Üretim** | Copywriter → Görsel Tasarımcı → Carousel Uzmanı | Her Salı 09:00 takvimdeki görsel, story ve carousel'leri üretip onaya gönderir |
| **4 · Video Üretim** | Senaryo Yazarı → Video Üretici → Video Editör | Her Çarşamba 09:00 en az 3 reel + 5 story: senaryo, üretim, kurgu paketi |
| **Yönetim** | 4 Takım Yöneticisi → Ana Kontrolcü | Her Cuma 17:00 haftalık yönetim değerlendirmesi ve yönetim raporu |

**Yönetim yapısı:** Her takımın bir yöneticisi var. Yönetici takımının çalışmalarını, hatalarını, maliyetini ve Kurucunun onay/revize/ret notlarını inceler; ajanlarının hafızasına ders yazar, gerekirse web'den kaynaklı bilgi notu ekler ve Ana Kontrolcüye rapor verir. Ana Kontrolcü takımlar arası zinciri ve bütçeyi kontrol eder, "Haftalık Yönetim Raporu"nu kaydeder ve Kurucuya yalnız karar gerektiren konuları iletir.

**Arşiv kullanımı:** Ajanlar her çalışmada şunları görür: Kurucunun yüklediği dosyalar ve notları, kendi son işleri, Kurucunun son talimatları ve son raporlar. `search_archive` aracıyla tüm geçmişte (raporlar, yüklenen dosyalar, geçmiş görev ve çıktılar, onay içerikleri, bilgi bankası) arama yapar. `view_image` ile yüklenen ve üretilen görselleri inceler. Yüklenen PDF, Word (.docx), Excel (.xlsx) ve PowerPoint (.pptx) dosyaları ajanlar için otomatik olarak Markdown metnine çevrilir; Dosyalar sayfasındaki **MD** düğmesi ajanların okuduğu hâli gösterir ve .md olarak indirir. Eski .xls/.doc/.ppt biçimleri yeni biçimde kaydedilip yüklenmelidir. Dosyalar sayfasında yüklenen her dosyaya "ajanlara not" ve hangi takımın kullanacağı eklenebilir.

**Ajan gelişimi:** Her ajanın kalıcı bir hafızası (en fazla 25 ders) ve takımının bilgi bankası var; ajan her çalışmada bunları okur. Dersler dört kaynaktan gelir: Kurucunun onay/revize/ret notları, yöneticinin koçluğu, ajanın kendi değerlendirmesi ve panelden elle eklenenler.

Takip edilen rakipler (Ayarlar'dan değiştirilebilir): The Purest Solutions, The Ordinary, Cream Co.

Ajan talimatları `app/team.py` dosyasında. Marka profili ve rakip listesi panelin **Ayarlar** sayfasından düzenlenir ve her çalıştırmada ajanlara verilir.

## Panel

| Sayfa | İçerik |
|-------|--------|
| **Ana ekran** | 12 ajan canlı: çalışan ajan yanar, ne yaptığı altında yazar. Canlı akış, erken uyarılar, son çalıştırmalar, bütçe |
| **Akışlar** | 5 iş akışı: otomatik çalışmayı aç/kapat, not ekleyip hemen çalıştır, tek bir ajana serbest görev ver, çalıştırma geçmişi |
| **Çalıştırma** | Bir çalıştırmanın adım adım canlı zaman çizelgesi, maliyeti ve çıktıları |
| **Onay** | Üretilen görsel, carousel, reel, story ve metinler: önizle, metni kopyala, onayla, revize iste, reddet. Revize, içeriği üreten ajana gider (video için Video Üretici → Video Editör); kart revizenin durumunu ve yeni sürümü gösterir |
| **İçerik geçmişi** | Onay kartındaki "Geçmiş" veya başlık: ilk çıktı, nasıl başladığı, Kurucu kararları ve tüm revize sürümleri sırayla |
| **Takvim** | Üst çubuktaki ▦ düğmesiyle açılan takvimden veya Takvim sayfasından gün seçilir; bir güne dokununca o gün yapılan çalıştırmalar, raporlar, görsel/videolar, onaylar, uyarılar ve aktivite |
| **Gelişim** | Yönetim yapısı, yönetim raporları, ajan hafızaları (ders ekle/sil) ve bilgi bankası (kaynaklı not ekle/sil) |
| **Raporlar** | Rakip raporları, içerik takvimleri, strateji planları |
| **Galeri / Dosyalar** | Üretilen medya; ekibin kullanacağı veriler (Insights dışa aktarımı, ürün bilgileri, brief) |
| **Ayarlar** | Marka profili, rakipler, bağlantı ve bütçe durumu |

Telefonda ana ekrana eklenebilir (PWA).

## Nasıl çalışır

- Her ajan kendi araç döngüsünde, görevine uygun modelle çalışır (uyarlanabilir düşünme, ajana göre `effort`):
  - **Haiku 5.5** (en ucuz): İçerik Tarayıcı, Erken Uyarı, Performans Analisti, Hedef Kitle Analisti, Görsel Tasarımcı, Carousel Uzmanı, Video Üretici, Video Editör. Veri toplama ve Virale araçlarını kullanma işleri.
  - **Sonnet 5.5**: Strateji Analisti, Strateji Plancısı, Copywriter, Senaryo Yazarı. Analiz ve marka metni.
  - Modeller panelin **Ayarlar → Ajan modelleri** bölümünden ajan başına değiştirilebilir (Opus 5.5 da seçilebilir).
- Sonnet ve Opus'ta ret durumunda Anthropic'in sunucu tarafı yedek modeli (`fallbacks: "default"`) devreye girer; Haiku 5.5'te bu özellik yoktur.
- **Web araması**: Anthropic'in sunucu aracı (`web_search_20260209`).
- **Virale (ChatPlace)**: MCP bağlayıcısıyla. Her ajan yalnız kendi izin listesindeki Virale araçlarını görür (okuma, rakip hesap ekleme, görsel/carousel/video üretimi). Mesaj gönderme, otomasyon, silme gibi araçlar hiçbir ajana açık değildir.
- **Panel araçları**: dosya listeleme/okuma, rapor kaydı, üretilen medyayı galeriye indirme, onaya gönderme, uyarı verme.
- Akıştaki her ajan önceki ajanların çıktısını alır. Olaylar canlı olarak (SSE) panele akar ve diske kaydedilir.
- **Sınırlar**: aylık bütçe (`MONTHLY_BUDGET_USD`) dolunca yeni çalıştırma başlamaz; çalıştırma başına Virale üretim sınırı (`MAX_GENERATIONS_PER_RUN`); ajan başına tur sınırı.

## Railway kurulumu

1. Railway projesi → **+ Create → GitHub Repo** → `ai-agent-team`.
2. Servis → **Settings**: Root Directory `bolbe-panel`, Branch kodun bulunduğu dal.
3. **Volume** ekleyin → Mount path `/data`.
4. **Variables**:

| Değişken | Değer |
|----------|-------|
| `ANTHROPIC_API_KEY` | Claude API anahtarı (console.anthropic.com) |
| `PANEL_PASSWORD` | Panele giriş parolası (en az 12 karakter önerilir) |
| `SESSION_SECRET` | 32+ karakterlik rastgele şifre |
| `VIRALE_MCP_URL` | ChatPlace MCP sunucu adresi |
| `VIRALE_MCP_TOKEN` | ChatPlace MCP erişim anahtarı |
| `MONTHLY_BUDGET_USD` | Aylık Claude harcama sınırı, ör. `50` |
| `PORT` | `8080` |

İsteğe bağlı: `MAX_GENERATIONS_PER_RUN` (12), `MAX_AGENT_TURNS` (30), `MAX_PARALLEL_RUNS` (2), `MAX_UPLOAD_MB` (200), `SESSION_DAYS` (30), `TZ_NAME` (`Europe/Istanbul`).

5. **Settings → Networking → Generate Domain** (port 8080).

`VIRALE_MCP_URL` boşsa ajanlar Virale olmadan çalışır: web araması ve yüklenen dosyalarla analiz yaparlar, görsel/video üretemezler ve bunu raporlarında belirtirler.

## Maliyet

- Claude (1M girdi / çıktı token başına): Haiku 5.5 $0,10 / $0,50 (istem 100K token'ı aşarsa $0,50 / $2,50), Sonnet 5.5 $2 / $10, Opus 5.5 $4 / $20. Web araması $10 / 1000 arama. Her çalıştırmanın maliyeti ajanın modeline göre hesaplanıp panelde gösterilir ve aylık toplam bütçeyle sınırlanır. Sistem talimatları önbelleğe alınır.
- Virale üretimleri ChatPlace AI bakiyenizden düşer; panel bunları ölçmez, yalnız sayısını sınırlar.

## Yerelde çalıştırma

```sh
pip install -r requirements.txt
DATA_DIR=./data PANEL_PASSWORD=deneme123 SESSION_SECRET=yerel-gizli-anahtar ANTHROPIC_API_KEY=sk-ant-... \
  uvicorn app.main:app --reload --port 8080
```

## Güvenlik

- Tek parolalı giriş; imzalı, `HttpOnly` çerez; 10 dakikada 10 hatalı denemede geçici kilit.
- Parola Ayarlar sayfasından değiştirilebilir (diskte PBKDF2 özeti olarak saklanır; diğer cihazlardaki oturumlar kapanır). Unutulursa Railway'de `PANEL_PASSWORD` değiştirilir: değişken değişince paneldeki parola geçersiz olur ve yeni `PANEL_PASSWORD` ile girilir.
- HTML/SVG dosyaları panelde çalıştırılmaz, indirme olarak verilir.
- Medya yalnız herkese açık `https` adreslerinden indirilir (iç ağ ve özel IP'ler engelli).
- Gerçek şirket verisi bu depoya eklenmez; veriler Railway diskinde durur.
