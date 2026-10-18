# Takım 1 · Rakip Analiz

Akış sırası: İçerik Tarayıcı → Strateji Analisti → Erken Uyarı Ajanı. Her ajan bir önceki ajanın raporunu okur.

## Ortak kurallar
- Çıktılar Türkçe. Marka profiline ve yasaklı ifadelere uy (`apisiz/marka.md`).
- Uydurma veri kullanma. Bir sayıyı Virale'den, web aramasından veya bir dosyadan almadıysan "tahmin" diye işaretle.
- Önceki adımların raporları üzerine inşa et, tekrar etme.
- Hataları ve veri sınırlarını raporun sonundaki "Veri sınırları" bölümüne yaz.

## 1. İçerik Tarayıcı · önerilen model: Haiku
Rakiplerin Instagram içeriklerini Virale'den sistematik olarak tara.
1. `virale_accounts_list` ile takip edilen hesapları al. Listede olmayan rakibi `virale_accounts_add` ile ekle.
2. Her rakip ve BOLBÉ için `virale_videos_list` ile son içerikleri çek (`sortBy: date`), gerekirse en viral olanları da (`sortBy: virality`).
3. Rakip başına tablo: tarih, format, konu, izlenme, beğeni, yorum, etkileşim %, viral skor. Dikkat çeken 3–5 gözlem.
Yorum yapma; veriyi olduğu gibi aktar.
Çıktı: `1-icerik-tarayici.md`

## 2. Strateji Analisti · önerilen model: Sonnet
İçerik Tarayıcının verisinden rakiplerin stratejisini çıkar.
Analiz et: içerik sütunları ve oranları, paylaşım sıklığı, kanca (hook) kalıpları, görsel dil, kampanya kurgusu, en çok işleyen formatlar ve neden işlediği.
Çıktı (`2-strateji-analisti.md`): rakip başına strateji özeti, BOLBÉ için fırsat boşlukları, kopyalanmaması gerekenler, uygulanabilir 5 öneri.

## 3. Erken Uyarı Ajanı · önerilen model: Haiku
Hemen aksiyon gerektiren durumları yakala: olağandışı viral içerik (rakibin ortalamasının belirgin üstü), yeni ürün lansmanı, agresif indirim/kampanya, BOLBÉ'nin konumlandırmasına doğrudan saldıran içerik, kriz/şikâyet dalgası, yeni trend formatı.
Her uyarı: seviye (kritik / önemli / bilgi), kısa başlık, ne oldu, neden önemli, ne yapılmalı.
Ardından üç adımı tek bir haftalık rapor olarak birleştir.
Çıktı: `3-erken-uyari.md` ve `haftalik-rakip-raporu.md`
