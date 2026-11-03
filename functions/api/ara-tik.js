// /api/ara-tik — anonim tıklama günlüğü yutağı (Cloudflare Pages Function).
// /ara sayfasındaki sonuç bağlantısına tıklanınca navigator.sendBeacon ile
// çağrılır: {kayit, sira, url}. kayit, ilgili aramanın arama_gunluk.kayit
// kimliğidir → hangi sorguda hangi sıradaki sonucun tıklandığı fine-tune
// verisi olarak birikir (IP/kimlik/oturum YOK — kullanıcı kararı 2026-11-03).
// Yazma işlemi waitUntil ile yanıttan sonra yapılır; istemciyi bekletmez.

const TIK_UST = "https://borsa-raporlari-web.pages.dev/api/ara-tik";
const UUID_DESENI = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function temizle(s, ust) {
  return String(s || "").slice(0, ust);
}

export async function onRequestPost(context) {
  let govde;
  try {
    govde = await context.request.json();
  } catch {
    return new Response(null, { status: 204 });
  }
  const kayit = temizle(govde.kayit, 40);
  const sira = Number(govde.sira);
  const url = temizle(govde.url, 300);
  if (!UUID_DESENI.test(kayit) || !Number.isInteger(sira) || sira < 0 || sira > 15 || !url.startsWith("/")) {
    return new Response(null, { status: 204 });
  }
  try {
    context.waitUntil(
      fetch(TIK_UST, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ kayit, sira, url }),
      }).catch(() => {})
    );
  } catch {
    // tıklama yazılamadı — istemciye yine 204 döner
  }
  return new Response(null, { status: 204 });
}
