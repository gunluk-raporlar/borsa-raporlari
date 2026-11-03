-- borsa-raporlari | pgvector seması
-- Supabase SQL Editor'e yapıştırılıp bir kez çalıştırılır.

create extension if not exists vector;

-- Rapar parçaları ve embedding vektörleri
create table if not exists belgeler (
  id bigserial primary key,
  kaynak text not null,                -- gunluk-rapor | derin-analiz | haftasonu | egitim
  parca int not null default 0,        -- parça sırası (0 = belgenin başı)
  baslik text,
  url text,
  tarih date,
  icerik text not null,
  hash text not null,                  -- içerik parmak izi (artımlı embedding için)
  embedding vector(1024) not null,     -- bge-m3 = 1024 boyut
  guncelleme timestamptz not null default now(),
  unique (kaynak, url, parca)
);

-- Benzerlik arama indexi (cosine)
create index if not exists belgeler_embedding_idx
  on belgeler using hnsw (embedding vector_cosine_ops);

-- Tarih filtresi için ekstra index
create index if not exists belgeler_tarih_idx on belgeler (tarih desc);

-- Semantik arama fonksiyonu (Next.js /api/ara burayı çağırır)
create or replace function match_belgeler(
  sorgu_embedding vector(1024),
  eslesme_sayisi int default 8,
  min_skor float default 0.30
)
returns table (
  id bigint,
  kaynak text,
  baslik text,
  url text,
  tarih date,
  icerik text,
  benzerlik float
)
language sql stable as $$
  select b.id, b.kaynak, b.baslik, b.url, b.tarih, b.icerik,
         1 - (b.embedding <=> sorgu_embedding) as benzerlik
  from belgeler b
  where 1 - (b.embedding <=> sorgu_embedding) > min_skor
  order by b.embedding <=> sorgu_embedding
  limit eslesme_sayisi;
$$;

-- Güvenlik: anonim erişim kapalı; yalnızca service key (server-side) yazar/okur.
alter table belgeler enable row level security;

-- ============================================================
-- 2026-11-03 — V2 arama + değerlendirme döngüsünün ayar tablosu
-- (Kullanıcı kararı: bu SQL Supabase'de GERÇEKTEN kurulacak; ajan
-- Management API ile çalıştıracak. route.js v2 yoksa eski RPC'ye düşer —
-- sıralama her durumda çalışır.)
-- ============================================================

-- V2: belge-başı tekil sonuç (url başına en iyi parça) + tarihli kaynaklara
-- sönümlü tazelik çarpanı (kök/sözlük sayfaları mtime tarihli olduğundan
-- HARİÇ). benzerlik alanı ham cosine kalır — gösterim kalibrasyonu istemcide
-- taban+0.30 bandıyla yapılır; sıralama SQL içinde ayarlı skorla yapılır.
create or replace function match_belgeler_v2(
  sorgu_embedding vector(1024),
  eslesme_sayisi int default 8,
  min_skor float default 0.30,
  taze_boost float default 0.12,
  taze_gun int default 60
)
returns table (
  id bigint,
  kaynak text,
  baslik text,
  url text,
  tarih date,
  icerik text,
  benzerlik float
)
language sql stable as $$
  with aday as (
    select b.id, b.kaynak, b.baslik, b.url, b.tarih, b.icerik,
           1 - (b.embedding <=> sorgu_embedding) as skor,
           row_number() over (partition by b.url order by b.embedding <=> sorgu_embedding) as sira
    from belgeler b
    where 1 - (b.embedding <=> sorgu_embedding) > min_skor
    order by b.embedding <=> sorgu_embedding
    limit 48
  )
  select a.id, a.kaynak, a.baslik, a.url, a.tarih, a.icerik, a.skor::float as benzerlik
  from aday a
  where a.sira = 1
  order by
    case
      when a.kaynak in ('gunluk-rapor','derin-analiz','makro-analiz','haftasonu','egitim')
           and a.tarih is not null
      then a.skor * (1 + taze_boost * greatest(0, 1 - (current_date - a.tarih)::float / greatest(taze_gun, 1)))
      else a.skor
    end desc
  limit eslesme_sayisi;
$$;

-- Değerlendirme döngüsünün (eval/dongu.py) yazdığı çalışma zamanı ayarları.
-- RLS açık, politika YOK: yalnızca service role yazar/okur.
create table if not exists arama_ayar (
  anahtar text primary key,
  deger float8 not null,
  guncelleme timestamptz not null default now()
);
alter table arama_ayar enable row level security;

-- Başlangıç satırları (route.js varsayılanlarıyla aynı); istenirse boş bırakılır.
insert into arama_ayar (anahtar, deger) values
  ('min_skor', 0.30),
  ('taze_boost', 0.12),
  ('taze_gun', 60),
  ('rerank_top_k', 0),
  ('genisletme', 1)
on conflict (anahtar) do nothing;

-- ============================================================
-- 2026-11-03b — Anonim arama kaydı + tıklama günlüğü (fine-tune verisi)
-- Kullanıcı kararı: ne arandığı, ne döndüğü, benzerlik %'leri ve hangi
-- sonucun tıklandığı kaydedilir; IP/kimlik/oturum YOK — kimlikle
-- bağlanmamış olay kaydı anonim veridir (KVKK kapsamı dışında).
-- akış: vekil her aramaya kayit (uuid) üretir → arama_gunluk'a yazar;
-- /ara sayfasındaki tıklama /api/ara-tik → arama_tik'e yazılır.
-- ============================================================

create table if not exists arama_gunluk (
  kayit uuid primary key,
  terim text not null,
  sonuc jsonb not null default '[]',  -- [{sira, id, url, baslik, kaynak, gosterim, benzerlik}]
  arama_tarihi timestamptz not null default now()
);
alter table arama_gunluk enable row level security;
create index if not exists arama_gunluk_terim_idx on arama_gunluk (terim);

create table if not exists arama_tik (
  id bigserial primary key,
  kayit uuid not null,
  sira int not null,
  url text not null,
  tik_tarihi timestamptz not null default now()
);
alter table arama_tik enable row level security;
create index if not exists arama_tik_kayit_idx on arama_tik (kayit);
