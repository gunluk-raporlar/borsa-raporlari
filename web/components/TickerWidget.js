"use client";

import useSWR from "swr";

const REPO_RAW = "https://raw.githubusercontent.com/gunluk-raporlar/borsa-raporlari/main";

export default function TickerWidget() {
  const { data } = useSWR(`${REPO_RAW}/ticker.json`, (u) => fetch(u).then((r) => r.json()), {
    refreshInterval: 120000,
  });
  if (!data || !data.hisseler) return null;
  return (
    <div className="ticker">
      <div className="ic">
        <span className="etiket">BIST 30 · {data.etiket || ""} · {data.guncelleme || ""}</span>
        {data.hisseler.map((s) => (
          <span key={s.h}>
            <span className="h">{s.h}</span>
            <span>{s.f}</span>{" "}
            <span className={s.d >= 0 ? "yukari" : "asagi"}>
              {s.d >= 0 ? "▲" : "▼"} {Math.abs(s.d).toFixed(2)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
