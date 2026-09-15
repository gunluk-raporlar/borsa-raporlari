"use client";

import useSWR from "swr";

const fetcher = (u) => fetch(u).then((r) => r.json());

function Olc({ ad, deger, detay }) {
  return (
    <div className="olc">
      <div className="ad">{ad}</div>
      <div className="deger">{deger}</div>
      {detay && <div className="detay">{detay}</div>}
    </div>
  );
}

export default function DurumPanel() {
  const { data, error, isLoading } = useSWR("/api/metrics?format=json", fetcher, {
    refreshInterval: 60000,
  });

  if (isLoading) return <div className="bilgi" style={{ marginTop: 16 }}>Metrikler yükleniyor…</div>;
  if (error) return <div className="hata" style={{ marginTop: 16 }}>Metrikler alınamadı: {String(error)}</div>;
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="bilgi" style={{ marginTop: 16 }}>
        Henüz metrik üretilmedi. İlk embedding çalışması ve günlük rapordan sonra burada veri
        görünecek.
      </div>
    );
  }

  const llm = data.llm || {};
  const emb = data.embedding || {};
  const promLink = "/api/metrics";

  return (
    <>
      <div className="durum-grid">
        <Olc
          ad="LLM çağrısı (son rapor)"
          deger={llm.adet ?? "—"}
          detay={`ortalama ${llm.ortalama_sn ?? "—"} sn · p95 ${llm.p95_sn ?? "—"} sn`}
        />
        <Olc
          ad="LLM gecikmesi (p95)"
          deger={llm.p95_sn != null ? `${llm.p95_sn} sn` : "—"}
          detay={`başarı oranı ${llm.basari_orani != null ? `%${Math.round(llm.basari_orani * 100)}` : "—"}`}
        />
        <Olc
          ad="Embedding: belge / parça"
          deger={emb.belge_sayisi != null ? `${emb.belge_sayisi} / ${emb.embed_edilen_parca ?? "—"}` : "—"}
          detay={emb.sure_sn != null ? `son çalışma ${emb.sure_sn} sn` : "henüz çalışmadı"}
        />
        <Olc
          ad="Son güncelleme"
          deger={(data.guncelleme || "—").replace("T", " ").slice(0, 16)}
          detay="Europe/Istanbul"
        />
      </div>

      <div className="kart">
        <h2>Prometheus formatı</h2>
        <p className="kucuk">
          Kendi Prometheus/Grafana kurulumunuz bu adresi doğrudan scrape edebilir:{" "}
          <a href={promLink}>{promLink}</a>
        </p>
        <PromOnizleme />
      </div>
    </>
  );
}

function PromOnizleme() {
  const { data } = useSWR("/api/metrics", (u) => fetch(u).then((r) => r.text()), {
    refreshInterval: 120000,
  });
  return <pre className="prom">{data || "yükleniyor…"}</pre>;
}
