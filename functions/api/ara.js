// /api/ara — Akıllı (anlam-düzeyi) arama vekili (Cloudflare Pages Function).
// Arka uç: borsa-raporlari-web.pages.dev üzerindeki pgvector araması
// (POST /api/ara, {"q": ...} → {"sonuc": [{tarih, baslik, kaynak,
// benzerlik, icerik}]}). Kullanıcı kararı 2026-11-01: sitedeki /ara sayfası
// arka planda bu arama servisine bağlanır; sonuçlar artık arka ucun
// /rapor/<tarih> görüntüleyicisine DEĞİL kendi sayfalarımıza yönlendirilir
// (kaynak: gunluk-rapor → /raporlar/<tarih>, derin-analiz →
// /derin-analiz/<tarih>, makro → /makro-analiz/<tarih>). Arka uç yanıt
// vermezse /ara sayfası yerel dizin aramasına düşer (fail-open).
// 2026-11-02: GET destegi eklendi — baslik hizli arama kutusu (her sayfada)
// ayni vekile ?q= ile baglandi; POST davranisi birebir korundu.

const UST = "https://borsa-raporlari-web.pages.dev/api/ara";
const KOK = "https://borsa-raporlari.pages.dev";
const GUNLUK_UST = "https://borsa-raporlari-web.pages.dev/api/ara-gunluk";
const CACHE_TTL = 120; // saniye — aynı sorgunun kısa süreli kenar önbelleği

// 2026-11-03 mimar notu: arka uç adresi zaten SITE_URL tabanlı üretip
// döndürüyor (pipeline/embed.py); kök/adresi çevirip geçir — yedek harita
// yalnız url alanı eksikse çalışır, yeni kaynaklar otomatik kapsanır.
function hedefUrl(s) {
  if (s.url && s.url.startsWith(KOK + "/")) return s.url.slice(KOK.length);
  const kaynak = (s.kaynak || "").toLowerCase();
  const tarih = s.tarih || "";
  if (kaynak.includes("derin")) return `/derin-analiz/${tarih}`;
  if (kaynak.includes("makro")) return `/makro-analiz/${tarih}`;
  if (kaynak === "egitim") return `/haftasonu-egitimi/${tarih}.html`;
  if (kaynak === "haftasonu") return `/haftasonu/${tarih}.html`;
  if (kaynak === "sozluk") return "/sozluk.html";
  if (kaynak === "terimler") return "/terimler.html";
  if (kaynak === "muhasebe-terimleri") return "/muhasebe-terimleri.html";
  if (kaynak === "temettu") return "/temettu.html";
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

async function akilliAra(q, secenek, waitUntil) {
  if (!q) return jsonYanit({ hata: "sorgu-bos" }, 400);
  const govde = { q };
  // Değerlendirme döngüsünün A/B kolları: ham = genişletme+rerank yok,
  // rerank = genişletme açık + zorunlu yeniden sıralama (arka uç karar verir).
  if (secenek && secenek.ham) govde.ham = 1;
  if (secenek && secenek.rerank) govde.rerank = 1;

  let ust;
  try {
    const denetim = new AbortController();
    const zaman = setTimeout(() => denetim.abort(), 15000);
    ust = await fetch(UST, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(govde),
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

  // Anonim arama kaydı: her aramaya kayit (uuid) verilir; sorgu + dönen
  // sonuçlar + %'ler arama_gunluk'a yazılır (IP/kimlik/oturum YOK — kullanıcı
  // kararı 2026-11-03). waitUntil ile yanıtı beklemeden yazılır; hata aramayı
  // etkilemez. Tıklamalar bu kayit kimliğine bağlanır (/api/ara-tik).
  const kayit = crypto.randomUUID();
  const sonuc = (veri.sonuc || []).map((s, sira) => ({
    sira,
    id: s.id,
    tarih: s.tarih,
    baslik: s.baslik,
    kaynak: s.kaynak,
    benzerlik: s.benzerlik,
    gosterim: s.gosterim,
    icerik: s.icerik,
    url: hedefUrl(s),
  }));
  const genisletilen = veri.genisletilen || [];

  // ham/rerank kolları değerlendirme trafiğidir — günlüğe yazılmaz
  if (waitUntil && !(secenek.ham || secenek.rerank)) {
    try {
      waitUntil(
        fetch(GUNLUK_UST, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            kayit,
            terim: q.slice(0, 200),
            sonuc: sonuc.map(({ icerik, ...gerisi }) => gerisi),
          }),
        }).catch(() => {})
      );
    } catch {
      // günlük yazılamadı — arama etkilenmez
    }
  }

  return jsonYanit({ sonuc, kayit, genisletilen, ayar: veri.ayar || null });
}

export async function onRequestPost(context) {
  let q = "", secenek = {};
  try {
    const govde = await context.request.json();
    q = String(govde.q || "").trim();
    if (govde.ham) secenek.ham = 1;
    if (govde.rerank) secenek.rerank = 1;
  } catch {}
  return akilliAra(q, secenek, context.waitUntil);
}

export async function onRequestGet(context) {
  const p = new URL(context.request.url).searchParams;
  const q = String(p.get("q") || "").trim();
  const secenek = {};
  if (p.get("ham")) secenek.ham = 1;
  if (p.get("rerank")) secenek.rerank = 1;
  return akilliAra(q, secenek, context.waitUntil);
}
