// /api/robot — İşlem Robotu canlı özeti (Cloudflare Pages Function).
// Kaynaklar:
//   - Robot durumu: raw.githubusercontent'tan SON commit'lenmiş durum
//     (robot gün içinde [CI Skip] etiketiyle de commit eder; Pages build
//     tetiklemez ama bu uç nokta en güncel durumu her zaman okur).
//   - Fiyatlar: TradingView "turkey" tarayıcı uç noktası — fiyatlar.js ile
//     aynı kaynak (~15 dk gecikmeli).
// Sonuç 45 sn kenar önbelleğinde tutulur; robot.html her 60 sn'de tazeler.

const RAW = "https://raw.githubusercontent.com/gunluk-raporlar/borsa-raporlari/main/data/robot";
const CACHE_KEY = "https://borsa-raporlari.pages.dev/__ic-robot";
const CACHE_TTL = 45; // saniye

const BIST30 = ["AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "DSTKF", "EKGYO",
  "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD",
  "MGROS", "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL",
  "THYAO", "TOASO", "TRALT", "TTKOM", "TUPRS", "VAKBN", "YKBNK"];

const jsonYanit = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": `public, max-age=${CACHE_TTL}`,
  },
});

async function jsonCek(url) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), 8000);
  try {
    const r = await fetch(url, { signal: c.signal });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

async function metinCek(url) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), 8000);
  try {
    const r = await fetch(url, { signal: c.signal });
    if (!r.ok) return null;
    return await r.text();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

async function scannerCek() {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), 8000);
  try {
    const r = await fetch("https://scanner.tradingview.com/turkey/scan", {
      method: "POST",
      headers: { "content-type": "application/json", "user-agent": "Mozilla/5.0" },
      body: JSON.stringify({
        symbols: { tickers: BIST30.map((h) => "BIST:" + h), query: { types: [] } },
        columns: ["name", "close"],
      }),
      signal: c.signal,
    });
    const ham = await r.json();
    const out = {};
    for (const satir of ham.data || []) {
      const d = satir.d || [];
      if (d.length >= 2 && d[0] && d[1] && +d[1] > 0) out[String(d[0])] = +d[1];
    }
    return out;
  } catch {
    return {};
  } finally {
    clearTimeout(t);
  }
}

export async function onRequestGet(context) {
  const onbellek = await caches.default.match(CACHE_KEY);
  if (onbellek) return onbellek;

  let yanit;
  try {
    const [durum, islemlerMetin, canli] = await Promise.all([
      jsonCek(RAW + "/durum.json"),
      metinCek(RAW + "/islemler.jsonl"),
      scannerCek(),
    ]);

    if (!durum) {
      yanit = jsonYanit({ hata: "robot durumu okunamadi" }, 503);
    } else {
      const pozisyonlar = Object.entries(durum.pozisyonlar || {})
        .map(([h, p]) => {
          const son = canli[h] || p.maliyet;
          const kz = (son - p.maliyet) * p.lot;
          const taban = p.maliyet * p.lot;
          return {
            hisse: h, lot: p.lot, maliyet: p.maliyet, son,
            kz: Math.round(kz * 100) / 100,
            kzy: taban ? Math.round((kz / taban) * 10000) / 100 : 0,
            stop: p.stop, hedef: p.hedef, giris: p.giris || "",
          };
        })
        .sort((a, b) => a.hisse.localeCompare(b.hisse));

      const toplam = (durum.nakit || 0) + pozisyonlar.reduce((s, p) => s + p.son * p.lot, 0);
      const kapital0 = typeof durum.kapital0 === "number" ? durum.kapital0 : 100000;
      const onceki = typeof durum.onceki_kapanis_ozkaynak === "number" ? durum.onceki_kapanis_ozkaynak : null;

      let sonIslemler = [];
      if (islemlerMetin) {
        sonIslemler = islemlerMetin.trim().split("\n").slice(-25).reverse()
          .map((s) => { try { return JSON.parse(s); } catch { return null; } })
          .filter(Boolean);
      }
      const satislar = sonIslemler.filter((i) => i.yon === "SAT");
      const kazananlar = satislar.filter((i) => i.kz > 0);

      yanit = jsonYanit({
        zaman: new Date().toISOString(),
        fiyat_saati: new Date().toLocaleTimeString("tr-TR", { timeZone: "Europe/Istanbul", hour: "2-digit", minute: "2-digit" }),
        toplam: Math.round(toplam * 100) / 100,
        toplam_getiri_yuzde: Math.round((toplam / kapital0 - 1) * 10000) / 100,
        gunluk_yuzde: onceki ? Math.round((toplam / onceki - 1) * 10000) / 100 : null,
        nakit: durum.nakit || 0,
        gerceklesen_kz: durum.gerceklesen_kz || 0,
        toplam_kesinti: durum.toplam_kesinti || 0,
        pozisyon_sayisi: pozisyonlar.length,
        kontrol_sayisi: durum.kontrol_sayisi || 0,
        bugun_tarih: durum.bugun_tarih || "",
        kazanma_orani: satislar.length ? Math.round(kazananlar.length / satislar.length * 1000) / 10 : 0,
        pozisyonlar,
        son_islemler: sonIslemler,
        ozkayit: durum.ozkayit || [],
      });
    }
  } catch (e) {
    yanit = jsonYanit({ hata: String(e).slice(0, 120) }, 503);
  }

  context.waitUntil(caches.default.put(CACHE_KEY, yanit.clone()));
  return yanit;
}
