// /api/asistan — AI asistan proxy'si (Pages Function, ana site).
// Grafiği çalıştıran uç nokta yeni arayüz projesinde (borsa-raporlari-web
// /api/asistan, LangGraph.js); ana site aynı-köken tek kapı olarak hizmet
// verir — ikinci host adı ziyaretçiye sızdırılmaz (architect notu 2026-11-01:
// hata mesajlarında da host geçmez, upstream status aynen geçirilir,
// no-store). LLM + doğrulama döngüsü 10-30 sn sürebilir; 55 sn üst sınır.

const UST = "https://borsa-raporlari-web.pages.dev/api/asistan";

const jsonYanit = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  },
});

async function gonder(context, soru) {
  let ust;
  try {
    ust = await fetch(UST, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ soru: soru || "" }),
      signal: AbortSignal.timeout(55000),
    });
  } catch {
    return jsonYanit({ hata: "Asistan şu anda yanıt vermedi; biraz sonra deneyin." }, 502);
  }
  let veri;
  try {
    veri = await ust.json();
  } catch {
    return jsonYanit({ hata: "Asistan yanıtı okunamadı; biraz sonra deneyin." }, 502);
  }
  return jsonYanit(veri, ust.status);
}

export async function onRequestPost(context) {
  let soru = "";
  try {
    const govde = await context.request.json();
    soru = govde.soru || "";
  } catch {
    // gövde bozuk → boş soru, üst uç 400 döner
  }
  return gonder(context, soru);
}

/** Kolay test: /api/asistan?q=soru */
export async function onRequestGet(context) {
  const q = new URL(context.request.url).searchParams.get("q") || "";
  return gonder(context, q);
}
