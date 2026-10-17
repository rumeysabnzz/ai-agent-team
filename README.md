# BOLBÉ AI Komuta Merkezi

BOLBÉ Skincare'in 4 takım, 12 ajandan oluşan AI pazarlama ekibi ve ekibi canlı izleyip yönettiğiniz web paneli. Railway'de tek servis olarak çalışır. Ajanlar doğrudan Claude API'yi kullanır; Hermes gerekmez.

## Ekip

| Takım | Ajanlar | Düzenli iş |
|-------|---------|------------|
| **1 · Rakip Analiz** | İçerik Tarayıcı → Strateji Analisti → Erken Uyarı Ajanı | Her Pazartesi 08:00 rakip taraması, uyarılar, haftalık rakip raporu |
| **2 · Marka Analiz ve Strateji** | Performans Analisti → Hedef Kitle Analisti → Strateji Plancısı | Her Pazartesi 11:00 haftalık içerik takvimi; her ayın 1'i 09:00 aylık strateji planı |
| **3 · İçerik Üretim** | Copywriter → Görsel Tasarımcı → Carousel Uzmanı | Her Salı 09:00 takvimdeki görsel, story ve carousel'leri üretip onaya gönderir |
| **4 · Video Üretim** | Senaryo Yazarı → Video Üretici → Video Editör | Her Çarşamba 09:00 en az 3 reel + 5 story: senaryo, üretim, kurgu paketi |

Takip edilen rakipler (Ayarlar'dan değiştirilebilir): The Purest Solutions, The Ordinary, Cream Co.

Ajan talimatları `app/team.py` dosyasında. Marka profili ve rakip listesi panelin **Ayarlar** sayfasından düzenlenir ve her çalıştırmada ajanlara verilir.

## Panel

| Sayfa | İçerik |
|-------|--------|
| **Ana ekran** | 12 ajan canlı: çalışan ajan yanar, ne yaptığı altında yazar. Canlı akış, erken uyarılar, son çalıştırmalar, bütçe |
| **Akışlar** | 5 iş akışı: otomatik çalışmayı aç/kapat, not ekleyip hemen çalıştır, tek bir ajana serbest görev ver, çalıştırma geçmişi |
| **Çalıştırma** | Bir çalıştırmanın adım adım canlı zaman çizelgesi, maliyeti ve çıktıları |
| **Onay** | Üretilen görsel, carousel, reel, story ve metinler: önizle, metni kopyala, onayla, revize iste (ajan yeniden üretir), reddet |
| **Raporlar** | Rakip raporları, içerik takvimleri, strateji planları |
| **Galeri / Dosyalar** | Üretilen medya; ekibin kullanacağı veriler (Insights dışa aktarımı, ürün bilgileri, brief) |
| **Ayarlar** | Marka profili, rakipler, bağlantı ve bütçe durumu |

Telefonda ana ekrana eklenebilir (PWA).

## Nasıl çalışır

- Her ajan Claude Opus 5.5 ile kendi araç döngüsünde çalışır (uyarlanabilir düşünme, ajana göre `effort`). Ret durumunda Anthropic'in sunucu tarafı yedek modeli (`fallbacks: "default"`) devreye girer.
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

İsteğe bağlı: `MAX_GENERATIONS_PER_RUN` (12), `MAX_AGENT_TURNS` (30), `MAX_PARALLEL_RUNS` (2), `MAX_UPLOAD_MB` (200), `SESSION_DAYS` (30), `TZ_NAME` (`Europe/Istanbul`), `CLAUDE_MODEL` (`claude-opus-5-5`).

5. **Settings → Networking → Generate Domain** (port 8080).

`VIRALE_MCP_URL` boşsa ajanlar Virale olmadan çalışır: web araması ve yüklenen dosyalarla analiz yaparlar, görsel/video üretemezler ve bunu raporlarında belirtirler.

## Maliyet

- Claude: Opus 5.5 fiyatıyla ($4 / $20 her 1M girdi/çıktı token; web araması $10 / 1000 arama) her çalıştırmanın maliyeti panelde gösterilir ve aylık toplam bütçeyle sınırlanır. Sistem talimatları önbelleğe alınır.
- Virale üretimleri ChatPlace AI bakiyenizden düşer; panel bunları ölçmez, yalnız sayısını sınırlar.

## Yerelde çalıştırma

```sh
pip install -r requirements.txt
DATA_DIR=./data PANEL_PASSWORD=deneme123 SESSION_SECRET=yerel-gizli-anahtar ANTHROPIC_API_KEY=sk-ant-... \
  uvicorn app.main:app --reload --port 8080
```

## Güvenlik

- Tek parolalı giriş; imzalı, `HttpOnly` çerez; 10 dakikada 10 hatalı denemede geçici kilit.
- HTML/SVG dosyaları panelde çalıştırılmaz, indirme olarak verilir.
- Medya yalnız herkese açık `https` adreslerinden indirilir (iç ağ ve özel IP'ler engelli).
- Gerçek şirket verisi bu depoya eklenmez; veriler Railway diskinde durur.
