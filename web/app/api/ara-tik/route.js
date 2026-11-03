export const runtime = "edge";

import { sbConf, sbRest } from "../../../lib/data";

// POST /api/ara-tik — anonim tıklama kaydı yazımı (yalnız vekil çağırır:
// functions/api/ara-tik.js). Gövde: {kayit (uuid), sira, url} — arama_tik
// tablosuna tek satır ekler. Doğruluk sinyali: hangi sorguda hangi sıradaki
// sonuç tıklandı.

const UUID_DESENI = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(req) {
  if (!sbConf().url || !sbConf().key) {
    return Response.json({ hata: "supabase-yok" }, { status: 503 });
  }
  let govde;
  try {
    govde = await req.json();
  } catch {
    return Response.json({ hata: "govde-bozuk" }, { status: 400 });
  }
  const kayit = String(govde.kayit || "").slice(0, 40);
  const sira = Number(govde.sira);
  const url = String(govde.url || "").slice(0, 300);
  if (!UUID_DESENI.test(kayit) || !Number.isInteger(sira) || sira < 0 || sira > 15 || !url.startsWith("/")) {
    return Response.json({ hata: "gecersiz-kayit" }, { status: 400 });
  }
  try {
    await sbRest("arama_tik", {
      method: "POST",
      body: JSON.stringify([{ kayit, sira, url }]),
    });
    return Response.json({ tamam: true });
  } catch (e) {
    return Response.json({ hata: e.message || "yazilamadi" }, { status: 502 });
  }
}
