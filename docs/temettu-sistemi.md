# Temettü sayfası — sistem notu ve temettucu.com incelemesi

Bu belge iki şeyi kaydeder: (1) `temettucu.com` hesaplayıcısının nasıl çalıştığına dair
tersine mühendislik bulguları, (2) bu bulgularla kurduğumuz kendi sistemimizin yapısı.

---

## 1. temettucu.com nasıl kurulmuş?

### 1.1 Ön yüz

| Katman | Ne kullanılmış |
|---|---|
| Framework | React 17 (Create React App, `webpackJsonp` chunk yapısı) |
| UI | MUI v5 (DataGrid, Select, TextField) + W3.CSS yardımcı sınıfları |
| Grafik | Chart.js 3 (çizgi grafik, ikinci Y ekseni "Gram Altın" için) |
| Ölçüm | Google Analytics 4 + AdSense |
| Dağıtım | Statik SPA (`/static/js/main.*.chunk.js`), tüm mantık tarayıcıda değil, sunucuda |

Tek sayfa uygulaması; hesaplama tarayıcıda **yapılmıyor**. Form verisi `multipart/form-data`
olarak PHP ucuna gönderiliyor, dönen JSON ekrana basılıyor.

### 1.2 Arka uç uçları

Tümü `https://temettucu.com/php-backhend/` altında:

| Uç | Yöntem | Gönderilen | Dönen |
|---|---|---|---|
| `getStocks.php` | GET | — | Tüm BIST kodları (düz dizi). Tarayıcıda 24 saat `localStorage`'da cache'lenir |
| `index.php` | POST | `hisse, startDate (dd-mm-yyyy), endDate, alimModu, alinanLot, yatirilacakPara, yatirilacakOran, temettuChecked, ucretArtis` | `sonuc`, `hisseMiktari`, `hisseDegerleri`, `resultTemettuJson`, `resultTemettu` |
| `getPrice.php` | POST | `hisse, startDate` | Seçilen tarihteki fiyat: `secilenTl`, `secilenUsd` |
| `calculateCurrentPrice.php` | POST | `startDate (mm-yyyy), yatirilacakPara` | O tarihe kadar yatırılan tutarın TL/USD karşılığı |
| `getAvarages.php` | GET | — | Hisse başına 50/200 günlük ortalama fiyat (TL, USD, altın) |
| `getPriceChange.php` | GET | — | Günlük/haftalık/aylık fiyat değişimi |
| `logMovement.php` | POST | `movement` | Kullanıcı hareketi kaydı (analitik) |

Yani sistem: **PHP + MySQL (fiyat/temettü/bedelsiz tabloları) + React ön yüz**. Hesaplama
sunucuda, dönen seri tarayıcıda grafiğe çevriliyor. Sunucu tarafında günlük fiyat, USD/TRY,
gram altın, temettü ve bedelsiz tabloları tutuluyor; `index.php` bu tabloları gün gün
işleyip sonucu üretiyor.

### 1.3 Alım modları

Ön yüzdeki `alimModu` sabitleri:

| Kod | Mod | Grup |
|---|---|---|
| 0 | Günlük Lot Alım | Düzenli |
| 1 | Aylık Lot Alım | Düzenli |
| 5 | Aylık TL ile Alım | Düzenli |
| 6 | Aylık Dolar ile Alım | Düzenli |
| 9 | Aylık Altın ile Alım | Düzenli |
| 7 | Asgari Ücret ile Alım | Düzenli |
| 2 | Tek Seferde Lot Alım | Tek seferde |
| 3 | Tek Seferde TL ile Alım | Tek seferde |
| 4 | Tek Seferde Dolar ile Alım | Tek seferde |
| 8 | Tek Seferde Altın ile Alım | Tek seferde |

`ucretArtis` = "Alımları enflasyon oranında yıllık arttır". Açıkken aylık katkı her yıl
enflasyon kadar büyütülüyor (TUPRS 2010→2026, aylık 50.000 TL: kapalıyken maliyet
9.999.997 TL, açıkken 51.228.428 TL — yani katkılar yıllık ~%30 bileşik artırılmış).

### 1.4 Hesaplama modeli (doğrulanmış)

Canlı uca kontrollü istekler atıp sonuçları kendi simülasyonumuzla karşılaştırarak
çözülen davranışlar:

1. **Alım** seçilen günün kapanışından, **tam lot** (`floor(para / fiyat)`).
2. **Temettü ödeme tarihi** = ex-tarih + **2 iş günü** (hafta sonu atlanır).
   Örn. ex 2012-04-06 (Cuma) → ödeme 2012-04-10 (Salı).
3. **Stopaj** ödeme tarihine göre: 2020 öncesi **%15**, 2020 ve sonrası **%10**.
   (2010 olayında 2,50 TL × 0,85 = 2,125 → 1.639 lot × 2,125 = 3.482 TL ile birebir uyuştu.)
4. **Net temettü tam TL'ye yuvarlanır**, sonra lota çevrilir: `floor(net / fiyat)`.
   Bu yuvarlama her olayda ~1 lot fark üretiyor; birebir eşleşmenin anahtarı buydu.
5. **Bedelsiz**: lot sayısı oran kadar çoğalır (TUPRS 2023-04-04 %600 → ×7).
   Fiyat serisi düzeltilmemiş tutulur; düzeltme lot tarafında yapılır.
6. **Kalan nakit** sonraki alıma devreder (mod 5/6/9'da).
7. Dönen `hisseMiktari` **yıllık**, `hisseDegerleri` **aylık** snapshot; `resultTemettuJson`
   her temettü olayını (tarih, hisse başı, o günkü lot, alınan lot, toplam lot) taşır.

### 1.5 Doğrulama tablosu

TUPRS, 2010-03-09 → 2026-10-30:

| Senaryo | temettucu.com | Bizim motor | Sapma |
|---|---|---|---|
| Tek seferde 1.000 lot | 7.000 lot | 7.000 lot | %0,00 |
| Tek seferde 50.000 TL, temettü yok | 11.473 lot | 11.473 lot | %0,00 |
| Aylık 50.000 TL, temettü yok | 847.546 lot | 848.022 lot | %0,06 |
| Aylık 50.000 TL, temettü var | 1.725.299 lot | 1.726.983 lot | %0,10 |
| Tek seferde 50.000 TL, temettü var | 30.886 lot | 30.958 lot | %0,23 |

Kalan fark, kapanış fiyatlarının kaynağından (bizim seri borsapy/BIST, onlarınki kendi
veritabanı) ve lot yuvarlama sırasından geliyor. Model birebir aynı.

---

## 2. Bizim kurduğumuz sistem

### 2.1 Dosyalar

| Yol | İş |
|---|---|
| `temettu_cek.py` | Veri çekici. `borsapy` ile temettü (nominal TL), bedelsiz oranı ve günlük kapanış; Yahoo ile USD/TRY ve ons altın. `data/temettu/` altına yazar |
| `temettu.py` | Üretici. Takvim + verim tablosunu hesaplar, `temettu-veri/` (yayına giden JSON) ve `temettu.html` üretir |
| `temettu-kaynak/govde.html` | Sayfa gövdesi (stil + bölümler, `{{...}}` yer tutucuları) |
| `temettu-kaynak/hesap.js` | Hesaplama motoru + arayüz (tarayıcıda, sunucu gerekmez) |
| `temettu_nav_ekle.py` | Üst menüye "💰 Temettü" bağlantısı ekler (bot.py + üretilmiş HTML'ler) |
| `temettu-veri/ozet.json` | Takvim + verim tablosu + asgari ücret tablosu (küçük, sayfa açılışında yüklenir) |
| `temettu-veri/<KOD>.json` | Hisse başına günlük fiyat + temettü + bedelsiz (seçilince yüklenir) |
| `temettu-veri/kur.json` | Günlük USD/TRY ve gram altın |

### 2.2 Veri akışı

```
borsapy (temettü, bedelsiz, kapanış)  ─┐
Yahoo (USD/TRY, GC=F ons altın)       ─┼─▶ temettu_cek.py ─▶ data/temettu/
                                       │
                       temettu.py ─────┴─▶ temettu-veri/*.json  (tarayıcıya)
                                       └─▶ temettu.html        (statik sayfa)
```

Hesaplama **tarayıcıda** yapılır: sayfa açılırken yalnızca `ozet.json` (≈30 KB) iner;
kullanıcı hisse seçip HESAPLA'ya bastığında o hissenin serisi (~40–100 KB) çekilir.
Sunucu, PHP veya veritabanı gerekmez — Cloudflare Pages'in statik yapısına uyar.

### 2.3 Fiyat serisinin düzeltilmemiş hâle getirilmesi

`borsapy.history()` **bedelsiz düzeltmeli** kapanış verir, `dividends` ise **nominal**
tutar verir. İkisini karıştırmamak için:

```
düzeltilmemiş(t) = düzeltilmiş(t) × Π (t'den sonraki bedelsiz çarpanları)
```

Doğrulama: TUPRS 2010-03-09 → 30,50 TL (temettucu.com'un gösterdiği fiyatla aynı),
FROTO 2025-05-06 → 877,50 / 2025-05-07 → 87,25 (%900 bedelsiz, tam 10 kat).

### 2.4 Verim tablosunda bedelsiz düzeltmesi

Geçmiş bir temettüyü **bugünkü** fiyata bölmek yanlış sonuç verir (FROTO %40,9 gibi).
Bu yüzden tabloda her temettü, sonrasındaki bedelsizlerin çarpanına bölünüp bugünkü pay
başına çevriliyor: FROTO %17,4. Takvimde ise tutarlar **o günkü** nominal değerinde ve
verim o günkü fiyata göre hesaplanıyor (tarihsel kayıt doğru kalsın diye).

### 2.5 Bilinen sınırlar

- **Tahmini takvim**: gelecek ex-tarihler geçmiş desenden (son 5 yılda aynı ay ≥2 ödeme)
  türetilir, `TAHMİNİ` etiketiyle gösterilir. Kesin tarih KAP bildirimiyle netleşir.
- **Asgari ücret tablosu** yaklaşıktır (`temettu.py` içinde `ASGARI_UCRET`).
- **Komisyon/BSMV** hesaba katılmaz; fiyatlar kapanış fiyatıdır.
- **Temettü kapsamı** BIST 30 ile sınırlı (sitenin geri kalanıyla aynı evren).
- `borsapy` temettü listesi 1999'a kadar gidiyor; "kesintisiz yıl" sayacı fiyat serimizin
  başladığı yıldan (2008) öncesini saymaz.

---

## 3. Komutlar

```bash
python temettu_cek.py            # veriyi tazele (~2 dk, 30 hisse)
python temettu.py                # ozet + veri dosyalari + temettu.html
python temettu_nav_ekle.py       # menüyü bir kez güncelle (idempotent)
```

CI'da sıra: `temettu_cek.py` → `temettu.py`. `bot.py` kuruluysa sayfa iskeleti
`bot._sayfa()` ile üretilir; yerelde bağımlılıklar yoksa `takvim.html` iskeletinden
türetilir (menü/tema birebir aynı kalır).

`build.sh` `temettu-veri/` klasörünü yayına kopyalar; `temettu-kaynak/` yayına girmez.
