// /api/ara — Akıllı (anlam-düzeyi) arama vekili (Cloudflare Pages Function).
// Arka uç: borsa-raporlari-web.pages.dev üzerindeki pgvector araması
// (POST /api/ara, {"q": ...} → {"sonuc": [{tarih, baslik, kaynak,
// benzerlik, icerik}]}). Kullanıcı kararı 2026-11-01: sitedeki /ara sayfası
// arka planda bu arama servisine bağlanır; sonuçlar artık arka ucun
// /rapor/<tarih> görüntüleyicisine DEĞİL kendi sayfalarımıza yönlendirilir
// (kaynak: gunluk-rapor → /raporlar/<tarih>, derin-analiz →
// /derin-analiz/<tarih>, makro → /makro-analiz/<tarih>). Arka uç yanıt
// vermezse /ara sayfası yerel dizin aramasına düşer (fail-open).

const UST = "https://borsa-raporlari-web.pages.dev/api/ara";
const CACHE_TTL = 120; // saniye — aynı sorgunun kısa süreli kenar önbelleği

function hedefUrl(s) {
  const kaynak = (s.kaynak || "").toLowerCase();
  const tarih = s.tarih || "";
  if (kaynak.includes("derin")) return `/derin-analiz/${tarih}`;
  if (kaynak.includes("makro")) return `/makro-analiz/${tarih}`;
  return `/raporlar/${tarih}`;
}

const jsonYanit = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": `public, max-age=${CACHE_TTL}`,
  },
});

export async function onRequestPost(context) {
  let q = "";
  try {
    const govde = await context.request.json();
    q = String(govde.q || "").trim();
  } catch {}
  if (!q) return jsonYanit({ hata: "sorgu-bos" }, 400);

  let ust;
  try {
    const denetim = new AbortController();
    const zaman = setTimeout(() => denetim.abort(), 15000);
    ust = await fetch(UST, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ q }),
      signal: denetim.signal,
    });
    clearTimeout(zaman);
  } catch {
    return jsonYanit({ hata: "akilli-ara-ulasilamadi" }, 502);
  }
  if (!ust.ok) return jsonYanit({ hata: `ust-servis-${ust.status}` }, 502);

  let veri;
  try {
    veri = await ust.json();
  } catch {
    return jsonYanit({ hata: "ust-servis-gecersiz-json" }, 502);
  }

  const sonuc = (veri.sonuc || []).map((s) => ({
    tarih: s.tarih,
    baslik: s.baslik,
    kaynak: s.kaynak,
    benzerlik: s.benzerlik,
    icerik: s.icerik,
    url: hedefUrl(s),
  }));
  return jsonYanit({ sonuc });
}
