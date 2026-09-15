"use client";

import { useState } from "react";

export default function SearchBox() {
  const [q, setQ] = useState("");
  const [yukleniyor, setYukleniyor] = useState(false);
  const [hata, setHata] = useState("");
  const [sonuc, setSonuc] = useState(null);

  async function ara(e) {
    e.preventDefault();
    const soru = q.trim();
    if (!soru || yukleniyor) return;
    setYukleniyor(true);
    setHata("");
    try {
      const r = await fetch("/api/ara", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: soru }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.hata || `Sunucu ${r.status} döndü`);
      setSonuc(j.sonuc || []);
    } catch (err) {
      setHata(err.message);
      setSonuc(null);
    } finally {
      setYukleniyor(false);
    }
  }

  return (
    <div>
      <form className="arama-form" onSubmit={ara}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Raporlarda ne aramak istersiniz?"
          aria-label="Arama"
        />
        <button className="btn" disabled={yukleniyor || !q.trim()}>
          {yukleniyor ? "Aranıyor…" : "Ara"}
        </button>
      </form>

      {hata && <div className="hata">Arama başarısız: {hata}</div>}

      {sonuc && sonuc.length === 0 && (
        <div className="bilgi">Bu konuda rapor bulunamadı. Farklı bir ifade deneyin.</div>
      )}

      {sonuc &&
        sonuc.map((s) => (
          <div className="sonuc" key={s.id}>
            <a href={`/rapor/${s.tarih}`}>
              <span className="skor">%{Math.round((s.benzerlik || 0) * 100)} benzerlik</span>
              {" · "}
              <span className="baslik">
                {s.tarih} — {s.baslik} ({s.kaynak})
              </span>
            </a>
            <div className="metin">
              {(s.icerik || "").slice(0, 260)}
              {(s.icerik || "").length > 260 ? "…" : ""}{" "}
              <a href={`/rapor/${s.tarih}`}>rapora git →</a>
            </div>
          </div>
        ))}
    </div>
  );
}
