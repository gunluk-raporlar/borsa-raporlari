// /zh/* eski Turkce slug'li URL'leri pinyin adlara 301 (2026-10-22).
// Yalnizca /zh/ onekli isteklerde calisir: kotanin patlama yaricapi ve Functions
// harcamasi dil agaci trafigiyle sinirli kalir (architect duzeltmesi 1).
// Eslenme yoksa next(): statik sunum ve _redirects aynen isler (fail-open).
//
// Kapsam: /zh/raporlar -> /zh/baogao, /zh/hisse/TOASO.html ->
// /zh/gupiao/TOASO.html, /zh/derin-analiz/2026-09-09 -> /zh/shendu-fenxi/...,
// legacy "<tarih>-derin-analiz.html" eki -> "<tarih>-shendu-fenxi.html".
const ESLEME = {
  "muhasebe-terimleri": "kuaiji-shuyu",
  "sirket-haberleri": "gongsi-xinwen",
  "haftasonu-egitimi": "gushi-xuetang",
  "borsapy-analiz": "borsapy-xinhao",
  "sinyal-karnesi": "xinhao-jifenka",
  "teknik-analiz": "jishu-saomiao",
  "derin-analiz": "shendu-fenxi",
  "makro-analiz": "hongguan-fenxi",
  "haftasonu": "zhoumo",
  "haberler": "xinwen",
  "raporlar": "baogao",
  "takvim": "rili",
  "sozluk": "cidian",
  "terimler": "shuyu",
  "gizlilik": "yinsi",
  "radyo": "guangbo",
  "hisse": "gupiao",
  "robot": "jiaoyi-jiqiren",
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
    if (seg[1] !== "zh") return context.next();
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
