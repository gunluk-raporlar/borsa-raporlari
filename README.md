# Borsa Raporları — Platform

BIST 30 günlük piyasa raporlarını üreten otomasyonun iki katmanı:

1. **Üretim (mevcut, değişmedi):** Python scriptleri GitHub Actions cron ile çalışır,
   rapor HTML'lerini repoya commitler; Cloudflare Pages **borsa-raporlari.pages.dev**
   adresinde yayınlar.
2. **Yeni platform (bu çalışmada eklendi):** pgvector ile **semantik arama**, Next.js
   **ISR + SWR** arayüzü ve **izleme metrikleri**. Hepsi GitHub Actions + ücretsiz
   bulut servisleriyle çalışır, sunucu/Docker gerektirmez.

```
GitHub Actions (cron) ──▶ bot.py rapor üretir ──▶ reports/*.html ──▶ Cloudflare Pages (eski site)
        │                                        │
        └─▶ pipeline/embed.py (async batch)      └─▶ web/ (Next.js: ISR + SWR arayüzü)
                │  Cloudflare Workers AI bge-m3          ├─ /            rapor arşivi (ISR 5 dk)
                ▼                                        ├─ /rapor/[tarih]  rapor sayfası (ISR 1 sa)
          Supabase pgvector  ◀──────────────────────────  └─ /ara         semantik arama (RPC benzerlik)
                                          /api/ara sorgu embedding + match_belgeler()
          data/metrics/*.json  ◀──  /durum izleme sayfası + /api/metrics (Prometheus formatı)
```

## Bileşenler

| Yol | Ne işe yarar |
|---|---|
| `bot.py` ve diğer scriptler | Rapor üretimi (mevcut; artık LLM gecikme metrikleri de yazıyor) |
| `pipeline/schema.sql` | Supabase'de bir kez çalıştırılacak pgvector şeması |
| `pipeline/embed.py` | Raporları metne çıkarıp parçalar, **async batch** embedding yapar, hash'e göre **artımlı** upsert eder |
| `web/` | Next.js 15 + SWR arayüzü (ISR, semantik arama, durum sayfası) |
| `.github/workflows/embedding.yml` | Gecelik embedding işi (hafta içi 08:35 TR + manuel) |
| `.github/workflows/web-deploy.yml` | web/'i ayrı bir Pages projesine deploy eder: **borsa-raporlari-web.pages.dev** |
| `data/metrics/latest.json` | İzleme sayfasının okuduğu özet metrikler |
| `hisse_analiz.py` | Elle eklenen hisse bölümleri: açıklanan bilanço tablosu (İş Yatırım'dan çekilir) + değerlendirme metinleri (veri: `data/hisse-analiz/<KOD>.json`) |
| `hisse_yenile.py` | Bilanço/metin güncellendikten sonra hisse sayfalarını CI beklemeden yerelde yeniden üretir |

## Akşam makro snapshot akışı

Makro sayıları artık doğrudan canlı kaynaktan rapora karıştırılmaz:

1. `.github/workflows/makro-snapshot.yml` her gün **20:30 TSİ**'de canlı veriyi çeker.
2. `makro_snapshot.py`, semayı doğrulayıp `data/makro-snapshot.json` ve
   `data/makro-gecmis/YYYY-MM-DD.json` dosyalarına yazar. Cache'e düşerse yeni
   akşam snapshot'ı üretilmez.
3. D+1 günkü günlük rapor, derin analiz ve haftalık makro analizi yalnızca
   `data/makro-gecmis/D.json` kaydını okur. Böylece aynı akşam 20:30'dan sonra
   elle çalıştırılan raporlar da veri kaymasına uğramaz.
4. Prompt, HTML veri tablosu, `makro_rejim.py` ve `dogrulama.py` aynı snapshot
   TÜİK SDMX'ten alınan Türkiye verileri resmi kaynak olarak işaretlenir.
   `TUIK_API_KEY` GitHub Secret olarak tanımlanmalıdır; anahtar repoya yazılmaz.
   TÜİK akışları: TÜFE (`DF_TUFE_SDMX_TT03`), toplam ÜFE
   (`DF_UFE_SANAYI_V2`), sanayi üretim endeksi, aylık temel/tamamlayıcı işgücü.
   SDMX seçicisi bulunamayan veya veri dönemi belirsiz akışlar atlanır; değer
   uydurulmaz.
5. TCMB EVDS opsiyoneldir: `EVDS_API_KEY` GitHub Secret olarak tanımlanır
   (repoya yazılmaz). Anahtar yoksa/401 gelirse hiçbir şey eklenmez, snapshot
   TV/TÜİK verisiyle yazılır. Anahtar varsa TR mevduat faizi, tüketici kredisi
   faizi, kredi büyümesi, M3 (yıllık) ve reel efektif kuru EVDS'ten gelir.
   Doğrulanmış seri kodları `evds_veri.ELLE_KODLAR` haritasında elle sabittir
   (24-25.09.2026 canlı denetim — arşiv/bayat gruplarda yanlış seri seçimini
   engeller); haritada olmayan hedefler `datagroups` + `serieList` kelime
   eşlemesiyle keşfedilir ve `data/evds-kodlar.json` içinde sabitlenir
   (workflow bu dosyayı commit eder). Eşleşme birden çok adaya düşerse o
   gösterge atlanır ve adaylar loglanır — kaynağı doğrulanamayan sayı rapora
   girmez. EVDS'de sabit vadeli 2/10 yıllık TR tahvil getirisi serisi
   bulunmadığından (09.2026 uç denetim: 4208 pydibs serisi tek tek ISIN)
   bu hedefler listede yoktur.
6. Doğrulama; ülke/gösterge eşleşmesine göre enflasyon, politika faizi, ÜFE,
   büyüme, işsizlik, cari denge ve rezerv değerlerini deterministik kontrol eder.

Snapshot şeması v2 ortak `makro_katalog.py` kataloğunu kullanır. Katalog 57 makro
göstergeyi kapsar: çekirdek enflasyon/PCE, PMI, üretim, perakende satış, ücret,
güven, bütçe, ticaret, M3 ve kredi gibi kaynağı doğrulanmış seriler; genişletilmiş
bloklarla (iç talep/kredi, küresel risk/faiz projeksiyonları, bütçe/dış denge,
turizm/kapasite kullanımı) ve EVDS akıllı göstergeleri (mevduat faizi, tüketici
kredisi faizi, kredi büyümesi, M3 yıllık, reel efektif kuru — TV'de karşılığı
olmayanlar). ABD'de başlıksız işsizlik oranı ile U-6 farklı kodludur. CDS veya
doğrudan yatırım için güvenilir/erişilebilir yayımlanmış veri yoksa sayı
uydurulmaz.

Aynı snapshot 15 piyasa serisi de taşır: USD/TRY, EUR/TRY, altın, gram altın
(türetilmiş), WTI, Brent, DXY, VIX, ABD 3/5/30 yıllık tahviller, FRED DGS2/DGS10
ve Borsapy BIST 30/BIST 100. Opsiyonel olarak FRED kredi marjları (investment
grade OAS, yüksek getirili OAS) eklenir. Her kayıt kapanış, önceki değer, değişim, tarih ve kaynağını taşır.
Yayınlanmayan seri snapshot'a girmez.

```bash
python makro_snapshot.py
python -m unittest -v test_makro_veri.py
python dogrulama.py
```

## Elle güncellenen hisse analizleri (bilanço + değerlendirme)

`hisse/<KOD>.html` sayfalarında iki bölüm **elle** yönetilir; günlük bot koşuları
bu içeriğe dokunmaz, yalnızca veri dosyasından sayfaya geri yazar:

1. **Açıklanan Bilançolar** — `data/hisse-analiz/<KOD>.json` içindeki `bilanco`
   listesinden üretilen tablo (satış/gelir, net kâr, marj, aktif, özkaynak,
   finansal borç; yıllık değişimler otomatik hesaplanır). Rakamlar İş Yatırım
   mali tablolarından çekilir:

   ```bash
   python hisse_analiz.py --bilanco-cek              # 30 hissenin tablosunu yenile
   python hisse_analiz.py --bilanco-cek ASELS AKBNK  # yalnızca seçili hisseler
   ```

2. **Değerlendirmeler** — teknik analiz, makroekonomik değerlendirme ve bilanço
   değerlendirmesi metinleri; aynı JSON dosyasının `teknik_analiz`,
   `makro_degerlendirme`, `bilanco_degerlendirmesi` alanlarına **elle** yazılır.

Denetim ve yayın:

```bash
python hisse_analiz.py --kontrol   # 30 hisse kapsam/tutarlılık denetimi
python hisse_yenile.py             # hisse sayfalarını yerelde yeniden üret (CI beklemeden)
```

Notlar:
- Metin boşsa ilgili bölüm sayfada sessizce gizlenir; bozuk/yarım bölüm oluşmaz.
- Bankalar (AKBNK, GARAN, ISCTR, VAKBN, YKBNK) banka mali tablo şablonundan
  çekilir; tabloda "Net Faiz Geliri / Toplam Aktifler" başlıkları kullanılır.
- DSTKF için kaynakta mali tablo verisi bulunmadığından tablo eklenmez; durum
  `--kontrol` çıktısında bilgi satırı olarak raporlanır.
- Gelir tablosu kalemleri (satış, net kâr) yıl başından itibaren kümülatiftir;
  bilanço kalemleri dönem sonu bakiyesidir.

## Kurulum secret'ları

| Secret | Zorunlu mu? | Ne için |
|---|---|---|
| `SUPABASE_URL` | Arama/embedding için | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Arama/embedding için | Supabase → Project Settings → API → **service_role** anahtarı |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Zaten mevcut | Embedding modeli (Workers AI bge-m3) + Pages deploy |

Secret'lar girilmeden hiçbir şey bozulmaz: embedding workflow'u sessizce atlar,
arama API'si "henüz yapılandırılmadı" yanıtı verir.

### Supabase adımları (5 dakika)
1. [supabase.com](https://supabase.com) → ücretsiz proje aç.
2. **SQL Editor** → `pipeline/schema.sql` içeriğini yapıştır → **Run**.
3. **Project Settings → API** → Project URL ve `service_role` anahtarını kopyala.
4. GitHub secret olarak ekle:
   ```bash
   gh secret set SUPABASE_URL -b"https://xxxx.supabase.co" -R gunluk-raporlar/borsa-raporlari
   gh secret set SUPABASE_SERVICE_KEY -b"<service-key>" -R gunluk-raporlar/borsa-raporlari
   ```
5. İlk doldurma (tüm arşiv): GitHub → Actions → **Embedding (pgvector)** → Run workflow → `mod: backfill`.

## Lokal geliştirme

```bash
# Arayüz
cd web && npm install && npm run dev        # http://localhost:3000
npm run build                               # prod derleme kontrolü
npm run pages:build                         # Cloudflare build'i (Windows'ta symlink
                                            # adımı atlanabilir; CI'da tam çalışır)

# Embedding pipeline
python pipeline/embed.py --dry-run          # ağ çağrısı yok, planı yazar
python pipeline/embed.py --backfill         # tüm arşivi embed eder (secret ister)
```

## İzleme (monitoring)

- **`/durum`** sayfası: LLM gecikmesi (ortalama/p95/başarı oranı), embedding istatistikleri,
  son güncelleme zamanı — 60 saniyede bir SWR ile yenilenir.
- **`/api/metrics`**: Prometheus metin formatı (`borsa_ai_llm_latency_seconds{stat="p95"}`,
  `borsa_embedding_docs_total`, …). Kendi Prometheus/Grafana sunucunuz bu adresi doğrudan
  scrape edebilir; scrape config örneği:

  ```yaml
  scrape_configs:
    - job_name: borsa-raporlari
      metrics_path: /api/metrics
      scrape_interval: 60s
      static_configs:
        - targets: ["borsa-raporlari-web.pages.dev"]
          scheme: https
  ```

- Metrik kaynağı: `bot.py` içindeki korumalı enstrümantasyon (`llm_call`/`_zai_call`
  süreleri) ve `pipeline/embed.py` çıktıları → `data/metrics/*.json` (repoda geçmiş tutulur).

> Not: Celery/Redis bu mimaride bulunmadığı için "Celery lag" ve "Redis hit rate"
> metrikleri bilinçli olarak yoktur — iş çizelgeleme GitHub Actions cron, önbellek ise
> Cloudflare CDN + ISR'dir. İleride Celery/Redis'e geçilirse `/api/metrics` uç noktası
> aynen kullanılmaya devam edebilir.

## Deploy zinciri

- **Eski statik site** (`borsa-raporlari.pages.dev`): Cloudflare Pages git entegrasyonu,
  repo kökündeki HTML'leri yayınlar — bu platform çalışması köke dokunmadığı için
  etkilenmez.
- **Yeni arayüz**: `web/**` değişince `Web (Next.js) Deploy` workflow'u build alıp
  `borsa-raporlari-web.pages.dev`'e deploy eder (wrangler + mevcut CF secret'ları).

Yasal uyarı: sitedeki hiçbir içerik yatırım tavsiyesi değildir.

## Muhasebe Terimleri sözlüğü (çok dilli)

- TR: `muhasebe-terimleri.html` — `bot.py` üretir (İngilizce terim + Türkçe karşılık/tanım/örnek,
  her kartta EN/DE/RU/ZH tanım kutuları). Tek sayfa; makine çevirisi kapsamı dışındadır.
- EN/DE/RU/ZH: `{en,de,ru,zh}/muhasebe-terimleri.html` — **her biri kendi dil verisinden**
  üretilir (çeviri değil): `muhasebe-en.csv` (EN), `muhasebe-de.csv` (DE),
  `muhasebe-zh.csv` (ZH), `muhasebe-ru.jsonl` (RU). 1691 terim/dil, alfabetik bölümler +
  tarayıcı içi arama.
- Üretici: `muhasebe_diller.py` (`tek başına: python muhasebe_diller.py`); `bot.py` içindeki
  `terimler_yaz()` günlük koşuda otomatik çağırır, böylece sayfalar veriyle senkron kalır.
- `i18n.py`: bu sayfa `GENERASYON_HARIC` ile makine çevirisinden muaf; ancak
  `DIL_SAYFASI_VERIDEN` sayesinde sitemap'e dil alternatifleri ve ayrı `<url>` kayıtları
  eklenir (sayfalar gerçekten var).
