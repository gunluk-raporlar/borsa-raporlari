export const runtime = "edge";

import { sbConf, sbRest } from "../../../lib/data";

async function aramaYap(soru) {
  let q = (soru || "").trim();
  if (!q) return { status: 400, body: { hata: "Arama metni boş olamaz." } };
  if (q.length > 500) q = q.slice(0, 500);

  const { CF_ACCOUNT_ID, CF_API_KEY } = process.env;
  if (!CF_ACCOUNT_ID || !CF_API_KEY || !sbConf().url || !sbConf().key) {
    return {
      status: 503,
      body: { hata: "Arama servisi henüz yapılandırılmadı (SUPABASE/CF değişkenleri eksik)." },
    };
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
    return { status: 200, body: { sonuc: temiz } };
  } catch (e) {
    return { status: 502, body: { hata: e.message || "bilinmeyen hata" } };
  }
}

export async function POST(req) {
  let q = "";
  try {
    const govde = await req.json();
    q = govde.q || "";
  } catch {
    // gövde bozuk → q boş kalır
  }
  const r = await aramaYap(q);
  return Response.json(r.body, { status: r.status });
}

/** Test ve kolay çağrı için: /api/ara?q=soru */
export async function GET(req) {
  const q = new URL(req.url).searchParams.get("q") || "";
  const r = await aramaYap(q);
  return Response.json(r.body, { status: r.status });
}
