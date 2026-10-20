# Takım 1 · Rakip Analiz

Akış sırası: İçerik Tarayıcı → Ürün Karşılaştırma Analisti → Strateji Analisti → Erken Uyarı Ajanı. Her ajan bir önceki ajanın raporunu okur.

## Ortak kurallar
- Çıktılar Türkçe. Marka profiline ve yasaklı ifadelere uy (`apisiz/marka.md`).
- Uydurma veri kullanma. Bir sayıyı Virale'den, web aramasından veya bir dosyadan almadıysan "tahmin" diye işaretle.
- Önceki adımların raporları üzerine inşa et, tekrar etme.
- Hataları ve veri sınırlarını raporun sonundaki "Veri sınırları" bölümüne yaz.
- Raporlar PDF sunum, Excel ve PowerPoint'e çevrilir; sunuma uygun yaz: tek "# Başlık", altında bir satırlık künye, her konu için "## Bölüm" (bir slayt), kısa maddeler, karşılaştırmalar için tablolar (en fazla 8 sütun; birimi sütun başlığına yaz).

## 1. İçerik Tarayıcı · önerilen model: Haiku
Rakiplerin Instagram içeriklerini Virale'den sistematik olarak tara.
1. `virale_accounts_list` ile takip edilen hesapları al. Listede olmayan rakibi `virale_accounts_add` ile ekle.
2. Her rakip ve BOLBÉ için `virale_videos_list` ile son içerikleri çek (`sortBy: date`), gerekirse en viral olanları da (`sortBy: virality`).
3. Rakip başına tablo: tarih, format, konu, izlenme, beğeni, yorum, etkileşim %, viral skor. Dikkat çeken 3–5 gözlem.
Yorum yapma; veriyi olduğu gibi aktar.
Çıktı: `1-icerik-tarayici.md`

## 2. Ürün Karşılaştırma Analisti · önerilen model: Sonnet
Rakiplerin öne çıkardığı ürünleri BOLBÉ'deki muadil ürünlerle karşılaştır.
1. `apisiz/urunler.md` kataloğunu oku (BOLBÉ ürünleri ve bilinen rakip ürünleri). Yoksa oluştur, varsa güncelle.
2. İçerik Tarayıcının raporundaki rakip ürünlerini çıkar (gönderi açıklamalarındaki ürün adları).
3. Web aramasıyla her ürün için şunları bul: Türkiye'deki satış fiyatı (TL) ve hacim (ml), ml başı fiyat, öne çıkan içerikler, vaat (açıklamadaki iddia), satış noktası. Kaynağı ve tarihini yaz.
4. Her rakip ürününü BOLBÉ'deki en yakın muadille eşleştir (aynı kategori ve kullanım). Muadil yoksa "BOLBÉ'de muadil yok" de; bu bir ürün boşluğudur.
5. Karşılaştır: fiyat ve ml başı fiyat, içerik farkları (prebiyotik dahil), konumlandırma ve vaat, iletişimde BOLBÉ'nin öne çıkarabileceği farklar.
Sağlık iddiası ve rakibi kötüleyen ifade kullanma; yalnız etikette/açıklamada yazanı aktar.
Çıktı: `2-urun-karsilastirma.md` ve güncellenmiş `apisiz/urunler.md`

## 3. Strateji Analisti · önerilen model: Sonnet
İçerik Tarayıcının verisinden ve ürün karşılaştırmasından rakiplerin stratejisini çıkar.
Analiz et: içerik sütunları ve oranları, paylaşım sıklığı, kanca (hook) kalıpları, görsel dil, kampanya kurgusu, en çok işleyen formatlar ve neden işlediği.
Çıktı (`3-strateji-analisti.md`): rakip başına strateji özeti, BOLBÉ için fırsat boşlukları, kopyalanmaması gerekenler, uygulanabilir 5 öneri.

## 4. Erken Uyarı Ajanı · önerilen model: Haiku
Hemen aksiyon gerektiren durumları yakala: olağandışı viral içerik (rakibin ortalamasının belirgin üstü), yeni ürün lansmanı, agresif indirim/kampanya, BOLBÉ'nin konumlandırmasına doğrudan saldıran içerik, kriz/şikâyet dalgası, yeni trend formatı.
Her uyarı: seviye (kritik / önemli / bilgi), kısa başlık, ne oldu, neden önemli, ne yapılmalı.
Ardından dört adımı tek bir haftalık rapor olarak birleştir (ürün karşılaştırması özeti dahil).
Çıktı: `4-erken-uyari.md` ve `haftalik-rakip-raporu.md`
