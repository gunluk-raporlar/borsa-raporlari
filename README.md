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
