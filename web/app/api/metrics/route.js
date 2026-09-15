export const runtime = "edge";

import { latestMetrikler } from "../../../lib/data";

function promFormat(veri) {
  const llm = (veri && veri.llm) || {};
  const emb = (veri && veri.embedding) || {};
  const ts = veri && veri.guncelleme ? Date.parse(veri.guncelleme) / 1000 : 0;
  const satirlar = [
    "# HELP borsa_ai_llm_calls_total Son gunluk raporda olculen LLM cagri adedi",
    "# TYPE borsa_ai_llm_calls_total counter",
    `borsa_ai_llm_calls_total ${llm.adet ?? 0}`,
    "# HELP borsa_ai_llm_latency_seconds LLM yanit suresi istatistikleri",
    "# TYPE borsa_ai_llm_latency_seconds gauge",
    `borsa_ai_llm_latency_seconds{stat="avg"} ${llm.ortalama_sn ?? 0}`,
    `borsa_ai_llm_latency_seconds{stat="p95"} ${llm.p95_sn ?? 0}`,
    `borsa_ai_llm_latency_seconds{stat="max"} ${llm.maks_sn ?? 0}`,
    "# HELP borsa_ai_llm_success_ratio LLM cagri basari orani",
    "# TYPE borsa_ai_llm_success_ratio gauge",
    `borsa_ai_llm_success_ratio ${llm.basari_orani ?? 0}`,
    "# HELP borsa_embedding_docs_total Embedding indeksindeki belge sayisi",
    "# TYPE borsa_embedding_docs_total gauge",
    `borsa_embedding_docs_total ${emb.belge_sayisi ?? 0}`,
    "# HELP borsa_embedding_parts_embedded Son calismada embed edilen parca sayisi",
    "# TYPE borsa_embedding_parts_embedded gauge",
    `borsa_embedding_parts_embedded ${emb.embed_edilen_parca ?? 0}`,
    "# HELP borsa_pipeline_last_update_timestamp Son metrik guncellemesi (unix)",
    "# TYPE borsa_pipeline_last_update_timestamp gauge",
    `borsa_pipeline_last_update_timestamp ${ts}`,
  ];
  return satirlar.join("\n") + "\n";
}

export async function GET(req) {
  const format = new URL(req.url).searchParams.get("format");
  const veri = await latestMetrikler();
  if (format === "json") {
    return Response.json(veri || {}, { headers: { "Cache-Control": "no-store" } });
  }
  return new Response(promFormat(veri), {
    headers: { "Content-Type": "text/plain; version=0.0.4; charset=utf-8", "Cache-Control": "no-store" },
  });
}
