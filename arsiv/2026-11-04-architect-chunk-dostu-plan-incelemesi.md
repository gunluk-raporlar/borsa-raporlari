# Architect incelemesi — chunk-dostu dokümantasyon planı (TRIGGER 1: plan kontrol)

Tarih: 2026-11-04 01:27 TSS (terminalden okundu) · Tetik: plan öncesi (architect-on-call kural 1) · Karar: REVISE

Görev: Siteyi Tom Johnson "Chunking your content for agent readability" (idratherbewriting.com, 2026-10-31) ölçütlerine göre ajan-okunur yapma planının denetimi. Amaç Google sıralaması değil; ajan/RAG erişimi ve sitenin kendi anlam araması.

## Architect ölçümleri (kanıt — benim denetimimde olmayanlar)

- terimler.html 2026-10-27 kararından beri hub; harf-grup sayfaları var (bot.py:8440-8446; build.sh terimler/ dağıtır). Grup sayfalarında h2 MEVCUT (terimler/a-b.html: h2 A+B; terimler/t-z.html: 7 h2); terimSuz() JS'i h2 bekliyor (bot.py:8424-8431). → Planın "terimler'e h2 ver" maddesi kısmen yapılmış; kalan iş terim kartı strong→h3.
- muhasebe-terimleri.html: 2,16 MB, ~210.000 kelime, 18 h2 (harf başına), 1695 terim kartı (div+strong), sıfır tablo. pipeline/embed.py indeksindeki 568 parça (~%27, backfill dry-run: toplam 2074 parça / 130 belge) BU TEK SAYFADAN. Sitenin bir numaralı parça-okunabilirlik ve indeks-hakimiyet sorunu.
- Sayı düzeltmeleri: sozluk.html ~1873 kelime (benim sayım 1031 — yöntem farkı), terimler hub ~7087.

## Architect'in soru bazlı yanıtları (özet)

(a) div→heading görsel gerileme GERÇEK: site_css.py BASE_CSS'te genel h2/h3 stili yok (yalnız .report h1/h2/h3 + .section-title, satır 72-77); sozluk/terimler/muhasebe/raporlar-hub .report kabuğunu kullanmıyor (_sayfa, bot.py:4671). Sahte-başlık div'leri heading'e çevrilirse tarayıcı varsayılanı görünür. Çözüm: stiller tek yerden BASE_CSS'e. İkinci tuzak: sozluk JS süzgeci yalnız .sozluk-oge'yi çevirir (bot.py:8212-8217); harf-h2 eklenirse süzgeç boş-başlık düzeltmesi de yapmalı (jsdom spot-check).
(b) .md aynaları şimdi DEĞİL, P1+P7 sonrası: bugünkü HTML'den çevrilirse sahte başlıklar markdown'da paragrafa düşer. Asıl değer gerekçesi: embed.py metin çıkarıcı tablo hücre sınırlarını yok ediyor (embed.py:125-147) — 106 satırlık makro tablosu chunk'ta kolonsuz düz metin; .md boru tablosu kolon semantiğini tek koruyan artefakt. Uygulama: derleme zamanında (build.sh *.md kopyalamıyor; ajan-uyumlulugu açıkça siliniyor — kural gerekli). Kapsam: ~10 hub sayfası + tarihli raporlar. ?format=md CF Function gereksiz (önbelleksiz, CPU harcar).
(c) Tablolar: 106 satır makro tablosu bölünsün — _veri_tablosu zaten ulke alanı taşıyor (makro_analiz.py:42-54); ülke bazlı 4-6 alt tablo insan için de kazanç. 31 satırlık BIST tabloları TEK kalsın + kolonları isimlendiren giriş + <caption>; sinyal-tipi bölme iki kitle için de paralel-deneyim bedeli. teknik-analiz div-ısı haritası metne düşerken tüm kolon semantiğini kaybediyor → gerçek tabloya çevir (veya görsel + erişilebilir tablo).
(d) embed.py başlık-farkında chunking: kalite pozitif, maliyet önemsiz (mevcut --backfill yolu embed.py:195-215, 380-402; ~130 batch; Supabase şema değişikliği yalnız bolum kolonu). KUPLAJ UYARISI: /api/ara çalışanı icerik alanını arayüzde gösterir (functions/api/ara.js:85-104); önek icerik'e gömülürse arayüze gürültü sızar. Doğru desen: önek yalnız embedding GİRDİSİNE; saklanan icerik temiz; bağlam ayrı bolum kolonuna (tek ALTER + ara.js select).
(e) i18n riski DÜŞÜK: i18n metin düğümü toplayıp DOM'u koruyor (i18n.py:1257-1290, 1455-1475); çeviri cache'li (i18n.py:420-426) — maliyet yalnız yeni başlık dizeleri. Çapa 1: terimler_gruplari_yeniden_dagit terim kartı DOM biçimine bağlı — div→h3'te varsayımları güncellenmeli. Çapa 2: muhasebe bölünürse yeni dosyalar i18n rglob ile otomatik alınır (i18n.py:1512-1536); id-çapa disiplini korunmalı (bot.py:8443-8445).

## Diğer bulgular

- Arşiv raporları üreticiyle düzelmez (bir kez yazılır); çare üretim prompt'una bölümleme şartı (gelecek) + chunker (arşiv).
- embed.py KAYNAKLAR/KOK_SAYFALAR (embed.py:37-60) hisse sayfalarını ve teknik/borsapy hub'larını içermiyor — isteğe bağlı kapsam genişletme.
- ajan-uyumlulugu/llms-yeni.txt: 26 URL'den 1'i karşılıksız (…/reports eski slug; 301 ile çalışır ama canonical /raporlar yazılmalı).
- WebMCP erteleme OKU.md'deki kararla tutarlı. İncelenen dosyalarda sır yok; embed.py anahtarları ortam değişkeninden okuyor (embed.py:367-372).

## Sıralı sorunlar

1. YÜKSEK: denetim bayat — muhasebe-terimleri bölünmesi plana eklenmeli; terimler maddesi daraltılmalı.
2. ORTA-YÜKSEK: P7 önek/bolum kuplajı (arayüz gürültüsü).
3. ORTA: BASE_CSS başlık stilleri + JS süzgeç başlık uyumu açık madde.
4. ORTA: P2 kapsam — arşiv (prompt+chunker) ile gelecek rapor ayrımı.
5. DÜŞÜK-ORTA: ısı haritası div→tablo.
6. DÜŞÜK: .md sıralaması (P1+P7 sonrası) + build.sh/_headers kuralları + /reports düzeltmesi.
7. BİLGİ: yeniden indeks maliyeti önemsiz; taban çizgisi yeniden alınmalı.

## Önerilen sıra

1. Taban çizgisi denetimi (terimler/ alt sayfaları + muhasebe-terimleri dahil).
2. Faz 1a: muhasebe-terimleri bölünmesi (terimler kalıbı: hub + harf sayfaları, id-çapa, i18n yeniden-harflendirme kontrolü).
3. BASE_CSS başlık stilleri; JS süzgeçlere başlık uyumu.
4. embed.py: çıkarıcıya başlık işareti, heading-sınırlı bölme, embedding girdisine önek; icerik temiz + bolum kolonu; tek --backfill.
5. makro tablosu ulke bazlı bölme; BIST 31'likler tek tablo+giriş+caption; ısı haritası tabloya.
6. Üretim prompt'larına bölümleme şartı (gelecek raporlar); arşive dokunma.
7. En son: .md üretici + build.sh kuralı; llms-yeni /reports→/raporlar düzeltmesiyle llms.txt'ye bağlama. Her fazdan sonra aynı denetim betiği + 4 dil kontrolü; yayın yalnız kullanıcı onayıyla.

VERDICT: REVISE
