# Architect yapısal incelemesi — ajan-okunurluk 7 faz (TRIGGER 3: kilometre taşı)

Tarih: 2026-11-04 (gece) · Tetik: kilometre taşı tamamlandı · Karar: REVISE → P0/P1/P3 giderildi (yukarıdaki kilometre taşı kaydına bakın)

## Architect'in doğrulamaları (kanıt dosya+satır)

- Deploy topolojisi: Pages çıktısı = dist/, Functions = repo kökü functions/ (build.sh içindeki "çıktı kök" yorumu bayat; canlı 404/200 kanıtlarıyla teyit). → dist'te üretilen .md dosyaları ve muhasebe-terimleri/<Harf> yolları yayınlanır; _redirects/middleware (yalnız /de|/ru|/zh önekli) .md'ye dokunmaz.
- build.sh:19 dizin listesinde muhasebe-terimleri var; baglanti_guzellestir.py:63-69 çapaları koruyarak A.html#t3 → A#t3 çevirir; site-arama.json'a dokunmaz.
- daily.yml (04:17 UTC) → embedding.yml (05:35 UTC) sırası güvenli; KOK_KLASORLER göreli yollar CI çalışma diziniyle uyumlu.
- _redirects: muhasebe bölünmesi için yeni kural GEREKMEZ; /en/muhasebe-terimleri.html → /en/accounting-terms.html 301'i bot.py:8767 dil hub üretimi sürdüğü için geçerli; kural sayısı 96/100 limiti altında.
- i18n üç muafiyet noktası kodda: i18n.py:1649-1652, :2043-2045, :2599-2606 (+ tanımlar :92-97).
- Sitemap/list üretimleri: bot.py:9294-9303 (statik liste iç içe üretim), bot.py:9183-9192 (site-arama), bot.py:8135 (/ara bolum render), bot.py:3423-3427 (makro BÖLÜMLEME KURALI), build.sh:19, llms.txt:30,38-46,48-57 — tümü mevcut.
- Fail-open zinciri: embed.py:402-414 ('bolum' kolonu 400'de etiketsiz yeniden yazım); yeni route.js + eski RPC → s.bolum undefined → alan atlanır (route.js:250, ara.js:90, bot.py:8135 koşullu); SQL önce + eski embed → kolon null. İki yön de zararsız.
- docs/ARCHITECTURE.md architect tarafından güncellendi (md_ayna düğümü, embed olay-akışı, Supabase +bolum, deploy topolojisi, bilinen riskler).

## Architect'in bulguları (sıralı)

1. P0: pipeline/schema.sql:163,187 — PostgreSQL'de create or replace mevcut fonksiyonun RETURNS TABLE kolon listesini değiştiremez → SQL Editor'de blok hata verir. Çözüm (uygulandı): iki drop function if exists eklendi (imzalar: match_belgeler(vector(1024), integer, double precision); match_belgeler_v2(..., double precision, double precision, double precision)).
2. P0-ek: hash gömme girdisi üzerinden hesaplandığından, fail-open döneminde yazılan satırlar SQL sonrası artımlı koşuda "değişmedi" sayılır → bolum NULL kalır. Çözüm: SQL uygulandıktan sonra BİR KEZ embedding.yml dispatch mod=backfill.
3. P1: md_ayna.py bağlantı metinlerini kaybediyordu (<a> metni _a_buf'ta kalıyor, _b_buf'a hiç girmiyordu; yerel test: linked td → "|  |"). Çözüm (uygulandı): bağlantı kapanış anında markdown olarak _b_buf'a yazılır; _h_refs mekanizması kaldırıldı. Doğrulama: teknik-analiz.md'de "| [ASELS](hisse/ASELS) | 342,25 TL | ..." satırları.
4. P2 (ops): canlı /api/ara?q=faiz → 502 ust-servis-502 (borsa-raporlari-web.pages.dev arka ucu; muhtemel embedding kotası). Bu işten bağımsız; push sonrası ayrıca doğrulanmalı.
5. P3: llms.txt örnek URL'leri küçük harfliydi (/a…/y); gerçek dosyalar A.html…Y.html, Pages büyük/küçük harfe duyarlı → düzeltildi (/A…/Y).
6. P3: embedding.yml'de concurrency grubu yoktu → eklendi (group: embedding, cancel-in-progress: false; YAML OK).

## Raporun sonuç bölümü (architect'ten aynen)

VERDICT: REVISE — 1. ve 2. maddelerin (schema drop + backfill) push öncesi/sonrası sırasıyla yapılması şart; kod değişikliği yapılmadı (mimar salt-okunur; tek istisna docs/ARCHITECTURE.md).
