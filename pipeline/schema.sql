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
