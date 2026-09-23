// /api/piyasa — Endeks, altın, döviz ve petrol özeti (Cloudflare Pages Function).
// Kaynak: TradingView "symbol" uç noktası (anahtarsız). Sitedeki hisse şeridiyle
// aynı mantık: kenar önbelleği 9 dk, istemci 5 dk'da bir tazeler.
// Semboller doğrulandı: BIST:XU030, BIST:XU100, TVC:GOLD, FX_IDC:USDTRY,
// FX_IDC:EURTRY, FX_IDC:GBPTRY, ICEEUR:BRN1! (Brent vadeli).

const GOSTERGELER = [
  { k: "XU030",  ad: "BIST 30 (TL)",  tv: "BIST:XU030",   b: "",   o: 2 },
  { k: "XU100",  ad: "BIST 100 (TL)", tv: "BIST:XU100",   b: "",   o: 2 },
  { k: "ONS",    ad: "Ons Altın",    tv: "TVC:GOLD",     b: "$",  o: 2 },
  { k: "USDTRY", ad: "Dolar",        tv: "FX_IDC:USDTRY", b: "TL", o: 4 },
  { k: "EURTRY", ad: "Euro",         tv: "FX_IDC:EURTRY", b: "TL", o: 4 },
  { k: "GBPTRY", ad: "Sterlin",      tv: "FX_IDC:GBPTRY", b: "TL", o: 4 },
  { k: "BRENT",  ad: "Brent Petrol", tv: "ICEEUR:BRN1!",  b: "$",  o: 2 },
];

const ONS_GRAM = 31.1034768;      // 1 ons = 31,1034768 gram
const CACHE_KEY = "https://borsa-raporlari.pages.dev/__ic-piyasa-serit";
const CACHE_TTL = 9 * 60;         // saniye (hisse şeridiyle aynı)

const jsonYanit = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": "no-store",
  },
});

async function sembolCek(tv) {
  const controller = new AbortController();
  const zamanAsimi = setTimeout(() => controller.abort(), 8000);
  try {
    const url = "https://scanner.tradingview.com/symbol?symbol=" +
      encodeURIComponent(tv) + "&fields=close,change";
    const r = await fetch(url, {
      headers: {
        "user-agent": "Mozilla/5.0 (compatible; borsa-raporlari/1.0)",
        "origin": "https://www.tradingview.com",
        "referer": "https://www.tradingview.com/",
      },
      signal: controller.signal,
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    const f = Number(d.close);
    const deg = Number(d.change);
    if (!Number.isFinite(f) || f <= 0) throw new Error("geçersiz veri");
    return { f: Math.round(f * 10000) / 10000, d: Number.isFinite(deg) ? Math.round(deg * 100) / 100 : 0 };
  } finally {
    clearTimeout(zamanAsimi);
  }
}

function zamanDamgasi() {
  const parcalar = Object.fromEntries(new Intl.DateTimeFormat("tr-TR", {
    timeZone: "Europe/Istanbul",
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date()).map((p) => [p.type, p.value]));
  return parcalar.day + "." + parcalar.month + " " + parcalar.hour + ":" + parcalar.minute;
}

async function piyasaCek() {
  const ham = await Promise.all(GOSTERGELER.map(async (g) => {
    try {
      const v = await sembolCek(g.tv);
      return { k: g.k, ad: g.ad, b: g.b, o: g.o, f: v.f, d: v.d };
    } catch (e) {
      return null;   // tek gösterge düşerse şeridin tamamı düşmesin
    }
  }));

  const liste = ham.filter(Boolean);
  if (!liste.length) throw new Error("hiçbir gösterge alınamadı");

  // Gram altın = ons altın (USD) / 31,1034768 × USDTRY; günlük değişim iki
  // bileşenin yüzde değişimlerinin toplamına yakınsar.
  const ons = liste.find((g) => g.k === "ONS");
  const usd = liste.find((g) => g.k === "USDTRY");
  if (ons && usd) {
    const gram = (ons.f / ONS_GRAM) * usd.f;
    const konum = liste.findIndex((g) => g.k === "USDTRY");
    liste.splice(konum, 0, {
      k: "GRAM", ad: "Gram Altın", b: "TL", o: 2,
      f: Math.round(gram * 100) / 100,
      d: Math.round((ons.d + usd.d) * 100) / 100,
    });
  }

  // Dolar bazli endeks: XU030 / USDTRY (TradingView'in "BIST-XU030.USD"
  // sayfasindaki mantik; o sembol scanner ucunda tanimli degil, biz hesapliyoruz).
  const xu030 = liste.find((g) => g.k === "XU030");
  if (xu030 && usd) {
    const dolar_endeks = xu030.f / usd.f;
    const degisim = ((1 + xu030.d / 100) / (1 + usd.d / 100) - 1) * 100;
    const konum = liste.findIndex((g) => g.k === "XU100");
    liste.splice(konum + 1, 0, {
      k: "XU030USD", ad: "BIST 30 ($)", b: "$", o: 2,
      f: Math.round(dolar_endeks * 100) / 100,
      d: Math.round(degisim * 100) / 100,
    });
  }

  return {
    guncelleme: zamanDamgasi(),
    etiket: "canlı (TradingView)",
    kaynak: "TradingView",
    gostergeler: liste,
  };
}

export async function onRequest() {
  const cache = caches.default;
  try {
    const onbellekte = await cache.match(CACHE_KEY);
    if (onbellekte) {
      const taze = new Response(await onbellekte.clone().text(), onbellekte);
      taze.headers.set("cache-control", "no-store");
      return taze;
    }
  } catch (e) { /* önbellek hatası canlı akışı engellemesin */ }

  try {
    const veri = await piyasaCek();
    try {
      await cache.put(CACHE_KEY, new Response(JSON.stringify(veri), {
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "public, max-age=" + CACHE_TTL,
        },
      }));
    } catch (e) { /* önbelleğe yazılamazsa doğrudan servis */ }
    return jsonYanit(veri);
  } catch (e) {
    return jsonYanit(
      { hata: "piyasa verisi alınamadı: " + (e && e.message ? e.message : "bilinmiyor") },
      502
    );
  }
}
