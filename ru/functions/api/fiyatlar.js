// /api/fiyatlar — Canlı BIST30 fiyat özeti (Cloudflare Pages Function).
// Kaynak: TradingView "turkey" tarayıcı uç noktası (sitedeki borsapy
// kütüphanesinin kullandığı kaynak; ~15 dk gecikmeli veri servis eder).
// Kenar önbelleği 9 dk: aynı bölgedeki ziyaretçiler tek üst akış isteğini
// paylaşır; sayfaların 10 dk'lık yenilemeleri hâlâ taze veri bulur.

const BIST30 = ["AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "DSTKF", "EKGYO",
  "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD",
  "MGROS", "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL",
  "THYAO", "TOASO", "TRALT", "TTKOM", "TUPRS", "VAKBN", "YKBNK"];

// Önbellek anahtarı: gerçek bir URL olmak zorunda; istemciye asla dönmez.
const CACHE_KEY = "https://fiyatlar-ic-cache.local/api/fiyatlar";
const CACHE_TTL = 9 * 60; // saniye

const jsonYanit = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": "no-store",
  },
});

async function tradingViewCek() {
  const controller = new AbortController();
  const zamanAsimi = setTimeout(() => controller.abort(), 8000);
  try {
    const r = await fetch("https://scanner.tradingview.com/turkey/scan", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (compatible; borsa-raporlari/1.0)",
        "origin": "https://www.tradingview.com",
        "referer": "https://www.tradingview.com/",
      },
      body: JSON.stringify({
        symbols: { tickers: BIST30.map((h) => "BIST:" + h), query: { types: [] } },
        columns: ["name", "close", "change", "market_cap_basic"],
      }),
      signal: controller.signal,
    });
    if (!r.ok) throw new Error("TradingView HTTP " + r.status);
    const d = await r.json();
    const harita = new Map((d.data || []).map((k) => [k.s.split(":")[1], k.d]));

    const now = new Date();
    // Biçim ICU sürümüne göre değişebilir; parçaları alip kendimiz "GG.AA SS:DD"
    // kurariz (ticker.json'daki "18.09 18:24" biçimiyle ayni).
    const parcalar = Object.fromEntries(new Intl.DateTimeFormat("tr-TR", {
      timeZone: "Europe/Istanbul",
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(now).map((p) => [p.type, p.value]));
    const zaman = parcalar.day + "." + parcalar.month + " " + parcalar.hour + ":" + parcalar.minute;

    const hisseler = [];
    for (const h of BIST30) {
      const dizi = harita.get(h);
      if (!dizi) continue;
      const fiyat = Number(dizi[1]);
      const degisim = Number(dizi[2]);
      const mcap = Number(dizi[3]);
      if (!Number.isFinite(fiyat) || fiyat <= 0) continue;
      hisseler.push({
        h,
        f: Math.round(fiyat * 100) / 100,
        d: Number.isFinite(degisim) ? Math.round(degisim * 100) / 100 : 0,
        m: Number.isFinite(mcap) && mcap > 0 ? Math.round(mcap) : null,
      });
    }
    if (!hisseler.length) throw new Error("TradingView boş yanıt");
    return {
      guncelleme: zaman,
      etiket: "~15 dk gecikmeli canlı",
      kaynak: "TradingView",
      hisseler,
    };
  } finally {
    clearTimeout(zamanAsimi);
  }
}

export async function onRequest() {
  const cache = caches.default;
  try {
    const onbellekte = await cache.match(CACHE_KEY);
    if (onbellekte) {
      // Kenarda saklanan kopya max-age taşır; tarayıcıya no-store verip
      // yenilemelerin daima kenara ulaşmasını garanti ediyoruz.
      const taze = new Response(await onbellekte.clone().text(), onbellekte);
      taze.headers.set("cache-control", "no-store");
      return taze;
    }
  } catch (e) { /* önbellek hatası canlı akışı engellemesin */ }

  try {
    const veri = await tradingViewCek();
    try {
      const kenarKopya = new Response(JSON.stringify(veri), {
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "public, max-age=" + CACHE_TTL,
        },
      });
      await cache.put(CACHE_KEY, kenarKopya);
    } catch (e) { /* önbelleğe yazılamazsa doğrudan servis */ }
    return jsonYanit(veri);
  } catch (e) {
    return jsonYanit(
      { hata: "fiyatlar alınamadı: " + (e && e.message ? e.message : "bilinmiyor") },
      502
    );
  }
}
