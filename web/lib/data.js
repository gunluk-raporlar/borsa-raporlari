const REPO_RAW = "https://raw.githubusercontent.com/gunluk-raporlar/borsa-raporlari/main";

export function sbConf() {
  return {
    url: process.env.SUPABASE_URL || "",
    key: process.env.SUPABASE_SERVICE_KEY || "",
  };
}

export function sbReady() {
  const c = sbConf();
  return Boolean(c.url && c.key);
}

export async function sbRest(path, init = {}) {
  const c = sbConf();
  const r = await fetch(`${c.url}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: c.key,
      Authorization: `Bearer ${c.key}`,
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}`);
  return r.json();
}

/** Rapor listesi: önce Supabase (embedding indeksi), yoksa sitemap.xml. */
export async function raporListesi(limit = 60) {
  try {
    if (sbReady()) {
      const rows = await sbRest(
        "belgeler?select=tarih,baslik,url&kaynak=eq.gunluk-rapor&parca=eq.0&order=tarih.desc&limit=" + limit
      );
      if (rows.length) {
        return rows.map((r) => ({
          tarih: r.tarih,
          baslik: r.baslik || "Günlük Rapor",
          url: r.url,
        }));
      }
    }
  } catch {
    // DB'ye düşülemedi → sitemap'a geç
  }
  const r = await fetch(`${REPO_RAW}/sitemap.xml`, { next: { revalidate: 600 } });
  if (!r.ok) return [];
  const xml = await r.text();
  const out = [];
  const re = /reports\/(\d{4}-\d{2}-\d{2})\.html<\/loc>/g;
  let m;
  while ((m = re.exec(xml))) {
    const tarih = m[1];
    out.push({
      tarih,
      baslik: `Günlük Rapor — ${tarih}`,
      url: `https://borsa-raporlari.pages.dev/reports/${tarih}.html`,
    });
  }
  return out.sort((a, b) => b.tarih.localeCompare(a.tarih)).slice(0, limit);
}

/** Rapor sayfası içeriği: GitHub raw'dan HTML, script/iframe temizlenmiş. */
export async function raporHtmlGetir(tarih) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(tarih)) return null;
  const r = await fetch(`${REPO_RAW}/reports/${tarih}.html`, { next: { revalidate: 3600 } });
  if (!r.ok) return null;
  let html = await r.text();
  html = html
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<iframe[\s\S]*?<\/iframe>/gi, "")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, "");
  const govde = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  return govde ? govde[1] : html;
}

/** İzleme metrikleri: pipeline ve bot.py'nin yazdığı latest.json */
export async function latestMetrikler() {
  try {
    const r = await fetch(`${REPO_RAW}/data/metrics/latest.json`, { cache: "no-store" });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}
