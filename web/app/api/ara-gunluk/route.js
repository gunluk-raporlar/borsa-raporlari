export const runtime = "edge";

import { sbConf, sbRest } from "../../../lib/data";

// POST /api/ara-gunluk — anonim arama kaydı yazımı (yalnız vekil çağırır:
// functions/api/ara.js, waitUntil ile). Gövde: {kayit (uuid), terim, sonuc[]}.
// Kimlik verisi taşımaz; arama_gunluk tablosuna tek satır ekler.

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
  const terim = String(govde.terim || "").slice(0, 200);
  const sonuc = Array.isArray(govde.sonuc) ? govde.sonuc.slice(0, 12) : [];
  if (!UUID_DESENI.test(kayit) || !terim) {
    return Response.json({ hata: "gecersiz-kayit" }, { status: 400 });
  }
  try {
    await sbRest("arama_gunluk", {
      method: "POST",
      body: JSON.stringify([
        {
          kayit,
          terim,
          sonuc: sonuc.map((s, sira) => ({
            sira,
            id: Number(s.id) || null,
            url: String(s.url || "").slice(0, 300),
            baslik: String(s.baslik || "").slice(0, 200),
            kaynak: String(s.kaynak || "").slice(0, 40),
            gosterim: Number.isFinite(Number(s.gosterim)) ? Number(s.gosterim) : null,
            benzerlik: Number.isFinite(Number(s.benzerlik)) ? Number(s.benzerlik) : null,
          })),
        },
      ]),
    });
    return Response.json({ tamam: true });
  } catch (e) {
    return Response.json({ hata: e.message || "yazilamadi" }, { status: 502 });
  }
}
