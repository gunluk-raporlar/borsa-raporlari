"use client";

import { useState } from "react";

const GORUNEN = 3;
const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];

function tarihYazi(t) {
  try {
    const d = new Date(`${t}T09:00:00`);
    return `${d.getDate()} ${AYLAR[d.getMonth()]} ${d.getFullYear()}, ${GUNLER[d.getDay()]}`;
  } catch {
    return t;
  }
}

function Kart({ r }) {
  return (
    <a className="rcard" href={`/rapor/${r.tarih}`}>
      <span className="tarih">{tarihYazi(r.tarih)}</span>
      <span className="alt">{r.baslik} · raporu aç →</span>
    </a>
  );
}

export default function ArsivListesi({ raporlar }) {
  const [acik, setAcik] = useState(false);
  if (!raporlar || raporlar.length === 0) {
    return <div className="bilgi">Henüz rapor yayınlanmadı.</div>;
  }
  const gorunen = raporlar.slice(0, GORUNEN);
  const gizli = raporlar.slice(GORUNEN);
  return (
    <>
      <div className="grid">
        {gorunen.map((r) => <Kart key={r.tarih} r={r} />)}
      </div>
      {gizli.length > 0 && (
        <>
          <div style={{ margin: "8px 0 0" }}>
            <button
              type="button"
              onClick={() => setAcik(!acik)}
              style={{
                background: "#fff", border: "1px solid #cbd5e1", borderRadius: 8,
                padding: "7px 16px", fontSize: "13.5px", fontWeight: 600,
                color: "#0f172a", cursor: "pointer",
              }}
            >
              {acik ? "Daha az göster" : `Daha fazla göster (${gizli.length} gün)`}
            </button>
          </div>
          {acik && (
            <div className="grid" style={{ marginTop: 10 }}>
              {gizli.map((r) => <Kart key={r.tarih} r={r} />)}
            </div>
          )}
        </>
      )}
    </>
  );
}
