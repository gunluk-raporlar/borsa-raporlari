// /api/asistan — LangGraph.js destekli soru-cevap asistanı (edge route).
// Graf: web/lib/asistan-graf.js (yönlendirici → veri toplama → üretim →
// doğrulama döngüsü). Araçlar: pgvector anlam araması (Supabase
// match_belgeler + Workers AI embedding; /api/ara ile aynı hat) ve ana
// sitedeki /api/asistan-veri güncel hisse verisi (teknik satırı + 1 haftalık
// değişim). LLM: OpenRouter (OPENROUTER_API_KEY runtime secret — web-deploy
// secret-put adımı; build env'e KONMAZ, architect notu 2026-11-01).
export const runtime = "edge";

import { sbConf, sbRest } from "../../../lib/data";
import { grafYap } from "../../../lib/asistan-graf";

const SITE = process.env.SITE_URL || "https://borsa-raporlari.pages.dev";

async function semanticGetir(soru) {
  const { CF_ACCOUNT_ID, CF_API_KEY } = process.env;
  if (!CF_ACCOUNT_ID || !CF_API_KEY || !sbConf().url || !sbConf().key) return [];
  const er = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/ai/v1/embeddings`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${CF_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: process.env.EMBED_MODEL || "@cf/baai/bge-m3", input: [soru] }),
      signal: AbortSignal.timeout(10000),
    }
  );
  if (!er.ok) throw new Error(`embedding ${er.status}`);
  const ej = await er.json();
  const vektor = ej?.data?.[0]?.embedding;
  if (!vektor) throw new Error("embedding boş");
  return sbRest("rpc/match_belgeler", {
    method: "POST",
    body: JSON.stringify({ sorgu_embedding: vektor, eslesme_sayisi: 6, min_skor: 0.3 }),
  });
}

async function stokVerisi(kod) {
  const r = await fetch(`${SITE}/api/asistan-veri?kod=${encodeURIComponent(kod)}`, {
    signal: AbortSignal.timeout(8000),
  });
  if (!r.ok) return null;
  return r.json();
}

async function llmCagir(messages) {
  const key = process.env.OPENROUTER_API_KEY;
  if (!key) throw new Error("OPENROUTER_API_KEY yok");
  const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: JSON.stringify({
      model: process.env.ASISTAN_MODEL || "deepseek/deepseek-chat-v3.1:free",
      messages,
      temperature: 0.2,
    }),
    signal: AbortSignal.timeout(40000),
  });
  if (!r.ok) throw new Error(`LLM ${r.status}`);
  const j = await r.json();
  return j?.choices?.[0]?.message?.content || "";
}

async function asistanCalistir(soru) {
  let q = (soru || "").trim();
  if (!q) return { status: 400, body: { hata: "Soru boş olamaz." } };
  if (q.length > 500) q = q.slice(0, 500);

  if (!process.env.OPENROUTER_API_KEY) {
    return { status: 503, body: { hata: "Asistan henüz yapılandırılmadı (LLM anahtarı eksik)." } };
  }

  const graf = grafYap({ semanticGetir, stokVerisi, llmCagir });
  try {
    const sonuc = await graf.invoke({ soru: q }, { recursionLimit: 25 });
    return {
      status: 200,
      body: {
        cevap: sonuc.cevap || "",
        kaynaklar: sonuc.kaynaklar || [],
        uyari: sonuc.uyari || "",
        tur: sonuc.tur || 0,
      },
    };
  } catch (e) {
    return { status: 502, body: { hata: e.message || "asistan hatası" } };
  }
}

export async function POST(req) {
  let soru = "";
  try {
    const govde = await req.json();
    soru = govde.soru || "";
  } catch {
    // gövde bozuk → boş soru
  }
  const r = await asistanCalistir(soru);
  return Response.json(r.body, { status: r.status });
}

/** Test ve kolay çağrı: /api/asistan?q=soru */
export async function GET(req) {
  const soru = new URL(req.url).searchParams.get("q") || "";
  const r = await asistanCalistir(soru);
  return Response.json(r.body, { status: r.status });
}
