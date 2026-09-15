export const runtime = "edge";

import { raporListesi } from "../../../lib/data";

export async function GET() {
  try {
    const raporlar = await raporListesi();
    return Response.json({ raporlar }, { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    return Response.json({ hata: e.message || "liste alınamadı" }, { status: 502 });
  }
}
