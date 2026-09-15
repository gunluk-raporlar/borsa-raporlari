export const runtime = "edge";

import { sbConf, sbRest } from "../../../lib/data";

export async function POST(req) {
  let q = "";
  try {
    const govde = await req.json();
    q = (govde.q || "").trim();
  } catch {
    // gövde bozuk → q boş kalır
  }
  if (!q) {
    return Response.json({ hata: "Arama metni boş olamaz." }, { status: 400 });
  }
  if (q.length > 500) q = q.slice(0, 500);

  const { CF_ACCOUNT_ID, CF_API_KEY } = process.env;
  if (!CF_ACCOUNT_ID || !CF_API_KEY || !sbConf().url || !sbConf().key) {
    return Response.json(
      { hata: "Arama servisi henüz yapılandırılmadı (SUPABASE/CF değişkenleri eksik)." },
      { status: 503 }
    );
  }

  try {
    const er = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/ai/v1/embeddings`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${CF_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model: process.env.EMBED_MODEL || "@cf/baai/bge-m3",
          input: [q],
        }),
      }
    );
    if (!er.ok) throw new Error(`embedding endpoint ${er.status}`);
    const ej = await er.json();
    const vektor = ej && ej.data && ej.data[0] && ej.data[0].embedding;
    if (!vektor) throw new Error("embedding boş döndü");

    const eslesen = await sbRest("rpc/match_belgeler", {
      method: "POST",
      body: JSON.stringify({
        sorgu_embedding: vektor,
        eslesme_sayisi: 8,
        min_skor: 0.3,
      }),
    });

    const temiz = (eslesen || []).map((s) => ({
      id: s.id,
      kaynak: s.kaynak,
      baslik: s.baslik,
      url: s.url,
      tarih: s.tarih,
      icerik: (s.icerik || "").slice(0, 600),
      benzerlik: s.benzerlik,
    }));
    return Response.json({ sonuc: temiz });
  } catch (e) {
    return Response.json({ hata: e.message || "bilinmeyen hata" }, { status: 502 });
  }
}
