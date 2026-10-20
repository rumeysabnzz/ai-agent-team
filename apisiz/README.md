# API'siz çalışma (Claude aboneliği ile)

Bu klasör, BOLBÉ AI ekibinin **Claude API'si kullanılmadan**, Claude aboneliğiyle çalıştırılan sürümüdür. Panel (`bolbe-panel/`) olduğu gibi kalır; bu klasör ona dokunmaz.

| | Panel (`bolbe-panel/`) | API'siz (`apisiz/`) |
|---|---|---|
| Ajanları kim çalıştırır | Railway'deki panel | Claude Code oturumu |
| Claude maliyeti | API kredisi | Aboneliğe dahil, ek ücret yok |
| Virale erişimi | `VIRALE_MCP_URL` + anahtar | Claude'daki ChatPlace bağlantısı |
| Çıktı | Panelin diski | Bu depodaki `apisiz/raporlar/` |
| Canlı izleme | Panelde | Claude uygulamasında oturum kaydı |

## Klasör düzeni
- `marka.md`: marka profili ve rakip listesi. Ajanlar önce bunu okur.
- `urunler.md`: BOLBÉ ve rakip ürün kataloğu (fiyat, hacim, içerik). Her taramada güncellenir.
- `ajanlar/`: takım ve ajan talimatları (panelin `app/team.py` dosyasındaki rollerle aynı).
- `raporlar/<tarih>-<akış>/`: her çalıştırmanın adım adım raporları ve birleşik rapor.

## Nasıl çalışır
1. Claude Code oturumu `ajanlar/rakip-analiz.md` talimatlarını okur.
2. **İçerik Tarayıcı** adımında Virale'den veriyi Claude'daki ChatPlace bağlantısıyla çeker ve `1-icerik-tarayici.md` dosyasını yazar.
3. **Ürün Karşılaştırma Analisti** ayrı bir alt ajan olarak (Sonnet) rakiplerin ürünlerini web aramasıyla BOLBÉ'deki muadilleriyle karşılaştırır (fiyat, ml başı fiyat, içerik, vaat). `2-urun-karsilastirma.md` dosyasını yazar ve `urunler.md` kataloğunu günceller.
4. **Strateji Analisti** ayrı bir alt ajan olarak (Sonnet) önceki raporları okur ve `3-strateji-analisti.md` dosyasını yazar.
5. **Erken Uyarı Ajanı** ayrı bir alt ajan olarak (Haiku) tüm raporları okur ve şu dosyaları yazar:
   - `4-erken-uyari.md`
   - birleşik `haftalik-rakip-raporu.md`
6. Raporlar bu depoya kaydedilir.

## Sunum biçimleri (PDF, Excel, PowerPoint)
Her rapor klasöründeki `sunum/` alt klasöründe raporların PDF (16:9 sunum), Excel ve PowerPoint hâlleri bulunur.
Yeniden üretmek veya yeni raporları çevirmek için depo kökünden:

```sh
pip install -r bolbe-panel/requirements.txt
python apisiz/sunuma_cevir.py pdf xlsx pptx    # docx ve html de verilebilir
```

Dönüştürücü panelle aynıdır (`bolbe-panel/app/export.py`).

## Elle çalıştırma
Bu depo bağlıyken bir Claude Code oturumunda şunu yazın:

> apisiz/ajanlar/rakip-analiz.md talimatlarıyla bu haftanın rakip taramasını API'siz çalıştır; raporları apisiz/raporlar/ altına kaydet, apisiz/sunuma_cevir.py ile PDF, Excel ve PowerPoint hâllerini üret ve main dalına gönder.

## Otomatik çalıştırma (isteğe bağlı)
Claude Code'un zamanlanmış görev özelliğiyle aynı komut her pazartesi 08:00'de kendiliğinden çalıştırılabilir. Bu oturumlar abonelik kullanım hakkınızdan düşer.

## Örnek çalıştırma
- `raporlar/2026-10-18-rakip-taramasi/`: ilk API'siz örnek.
