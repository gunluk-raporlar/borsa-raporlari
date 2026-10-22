// /ru/* eski Turkce slug'li URL'leri transliterasyon (Kiril degil) adlara 301 (2026-10-22).
// Yalnizca /ru/ onekli isteklerde calisir: kotanin patlama yaricapi ve Functions
// harcamasi dil agaci trafigiyle sinirli kalir (architect duzeltmesi 1).
// Eslenme yoksa next(): statik sunum ve _redirects aynen isler (fail-open).
//
// Kapsam: /ru/raporlar -> /ru/otchety, /ru/hisse/TOASO.html ->
// /ru/aktsii/TOASO.html, /ru/derin-analiz/2026-09-09 -> /ru/glubokiy-analiz/...,
// legacy "<tarih>-derin-analiz.html" eki -> "<tarih>-glubokiy-analiz.html".
const ESLEME = {
  "muhasebe-terimleri": "bukhgalterskie-terminy",
  "sirket-haberleri": "korporativnye-novosti",
  "haftasonu-egitimi": "shkola-rinka",
  "borsapy-analiz": "borsapy-signaly",
  "sinyal-karnesi": "svodka-signalov",
  "teknik-analiz": "tekhnicheskiy-analiz",
  "derin-analiz": "glubokiy-analiz",
  "makro-analiz": "makroanaliz",
  "haftasonu": "vykhodnye",
  "haberler": "novosti",
  "raporlar": "otchety",
  "takvim": "kalendar",
  "sozluk": "glossariy",
  "terimler": "terminy",
  "gizlilik": "konfidencialnost",
  "radyo": "radio",
  "hisse": "aktsii",
  "robot": "torgovyy-robot",
};

function segmentDegistir(seg) {
  const html = seg.endsWith(".html");
  let s = html ? seg.slice(0, -5) : seg;
  let degisti = false;
  for (const [eski, yeni] of Object.entries(ESLEME)) {
    if (s === eski) {
      // tam eslesme her zaman eslenir (ru: robot -> torgovyy-robot)
      s = yeni;
      degisti = true;
      continue;
    }
    // idempotans: hedef ad eskiyi iceriyorsa (torgovyy-robot icindeki "robot")
    // sonek eslemesi atlanir — aksi halde her istekte bir 'torgovyy-' daha eklenir
    if (yeni.endsWith(eski)) continue;
    if (s.endsWith("-" + eski)) {
      s = s.slice(0, s.length - eski.length - 1) + "-" + yeni;
      degisti = true;
    }
  }
  return degisti ? s + (html ? ".html" : "") : seg;
}

export async function onRequest(context) {
  try {
    const url = new URL(context.request.url);
    const seg = url.pathname.split("/");
    if (seg[1] !== "ru") return context.next();
    let degisti = false;
    for (let i = 2; i < seg.length; i++) {
      if (!seg[i]) continue;
      const yeniSeg = segmentDegistir(seg[i]);
      if (yeniSeg !== seg[i]) {
        seg[i] = yeniSeg;
        degisti = true;
      }
    }
    if (degisti) {
      const hedef = new URL(seg.join("/") + url.search, url.origin);
      return Response.redirect(hedef.toString(), 301);
    }
  } catch (hata) {
    // fail-open: yonlendirme hatasi siteyi indirmesin
  }
  return context.next();
}
