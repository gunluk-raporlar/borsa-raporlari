export const runtime = "edge";

import { sbConf, sbRest } from "../../../lib/data";

// 2026-11-03 iyileştirmesi (mimar onaylı):
// - Ayarlar Supabase "arama_ayar" tablosundan okunur (değerlendirme döngüsü
//   yazar); tablo henüz yoksa kod içi varsayılanlar geçerli (fail-open).
// - Sorgu genişletmesi: şirket kodu ↔ şirket adı haritası (en fazla 2 ekleme).
// - Önce match_belgeler_v2 (belge-başı tekilleştirme + tazelik çarpanı),
//   RPC henüz kurulmadıysa eski match_belgeler'e düşer (her iki yönde güvenli).
// - Yeniden sıralama (bge-reranker-base) VARSAYILAN KAPALI; kendi
//   try/catch'inde — hata vektör sırasını bozmaz (mimar şartı).
// - ham=1 seçeneği genişletme ve rerank olmadan saf vektör araması yapar
//   (değerlendirme döngüsünün A/B kolu); rerank=1 zorunlu yeniden sıralama.

const AYAR_VARSAYILAN = {
  min_skor: 0.3, // RPC taban benzerlik eşiği (otomatik ayar aralığı 0.15-0.45)
  taze_boost: 0.12, // tarihli kaynaklara tazelik çarpanı payı (0-0.15)
  taze_gun: 60, // tazelik sönüm günü aralığı
  rerank_top_k: 0, // 0 = kapalı
  genisletme: 1, // 1 = şirket kodu/adı genişletmesi açık
};

let _ayarOnbellek = { deger: null, zaman: 0 };
const AYAR_TTL = 600000; // 10 dk; hatada son iyi değer (stale-while-error)

async function ayarGetir() {
  const simdi = Date.now();
  if (_ayarOnbellek.deger && simdi - _ayarOnbellek.zaman < AYAR_TTL) return _ayarOnbellek.deger;
  const sonIyi = _ayarOnbellek.deger ? { ..._ayarOnbellek.deger } : null;
  try {
    const satirlar = await sbRest("arama_ayar?select=anahtar,deger");
    const ayar = { ...AYAR_VARSAYILAN };
    for (const s of satirlar || []) {
      if (s.anahtar in ayar && Number.isFinite(Number(s.deger))) ayar[s.anahtar] = Number(s.deger);
    }
    _ayarOnbellek = { deger: ayar, zaman: simdi };
    return ayar;
  } catch {
    return sonIyi || { ...AYAR_VARSAYILAN };
  }
}

// ---------- Şirket kodu / adı sorgu genişletmesi ----------
// [kod, anahtar kelimeler (ASCII-küçük), embedding'e eklenecek metin]

const SIRKETLER = [
  ["GARAN", ["garanti"], "GARAN Garanti Bankası Garanti BBVA"],
  ["AKBNK", ["akbank"], "AKBNK Akbank"],
  ["ISCTR", ["is bankasi", "isbank"], "ISCTR İş Bankası"],
  ["YKBNK", ["yapi kredi", "yapikredi"], "YKBNK Yapı Kredi"],
  ["HALKB", ["halkbank"], "HALKB Halkbank"],
  ["VAKBN", ["vakifbank"], "VAKBN VakıfBank"],
  ["TSKB", ["tskb"], "TSKB Sınai Kalkınma Bankası"],
  ["ASELS", ["aselsan"], "ASELS Aselsan"],
  ["TUPRS", ["tupras"], "TUPRS Tüpraş"],
  ["EREGL", ["eregli", "erdemir"], "EREGL Ereğli Demir Çelik"],
  ["KRDMD", ["kardemir"], "KRDMD Kardemir"],
  ["THYAO", ["thy", "turk hava yollari"], "THYAO THY Türk Hava Yolları"],
  ["PGSUS", ["pegasus"], "PGSUS Pegasus"],
  ["TAVHL", ["tav havalimanlari", "tav"], "TAVHL TAV Havalimanları"],
  ["SISE", ["sisecam"], "SISE Şişecam"],
  ["KOZAL", ["koza altin"], "KOZAL Koza Altın"],
  ["KOZAA", ["koza madencilik"], "KOZAA Koza Madencilik"],
  ["FROTO", ["ford otosan"], "FROTO Ford Otosan"],
  ["TOASO", ["tofas"], "TOASO Tofaş"],
  ["TCELL", ["turkcell"], "TCELL Turkcell"],
  ["TTKOM", ["turk telekom"], "TTKOM Türk Telekom"],
  ["BIMAS", ["bim"], "BIMAS BİM Mağazalar"],
  ["MGROS", ["migros"], "MGROS Migros"],
  ["ARCLK", ["arcelik"], "ARCLK Arçelik"],
  ["EKGYO", ["emlak konut"], "EKGYO Emlak Konut GYO"],
  ["ENKAI", ["enka"], "ENKAI Enka İnşaat"],
  ["SASA", ["sasa"], "SASA Sasa Polyester"],
  ["PETKM", ["petkim"], "PETKM Petkim"],
  ["AEFES", ["anadolu efes"], "AEFES Anadolu Efes"],
  ["CCOLA", ["coca cola"], "CCOLA Coca-Cola İçecek"],
  ["ULKER", ["ulker"], "ULKER Ülker Bisküvi"],
  ["ASTOR", ["astor"], "ASTOR Astor Enerji"],
  ["SAHOL", ["sabanci"], "SAHOL Sabancı Holding"],
  ["KCHOL", ["koc holding"], "KCHOL Koç Holding"],
  ["GUBRF", ["gubre"], "GUBRF Gübre Fabrikaları"],
  ["OYAKC", ["oyak"], "OYAKC Oyak Çimento"],
];

function trKucuk(s) {
  return (s || "")
    .toLowerCase()
    .replace(/ı/g, "i")
    .replace(/İ/g, "i")
    .replace(/I/g, "i")
    .replace(/ç/g, "c")
    .replace(/ğ/g, "g")
    .replace(/ö/g, "o")
    .replace(/ş/g, "s")
    .replace(/ü/g, "u");
}

function kelimeMi(metin, kelime) {
  const kacli = kelime.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp("(^|[^a-z0-9])" + kacli + "([^a-z0-9]|$)").test(metin);
}

function sorguGenislet(soru) {
  const q = trKucuk(soru);
  const eklenen = [];
  for (const [kod, anahtarlar, metin] of SIRKETLER) {
    if (eklenen.length >= 2) break;
    const eslesen = kelimeMi(q, trKucuk(kod)) || anahtarlar.some((a) => kelimeMi(q, a));
    if (eslesen && !kelimeMi(q, trKucuk(metin))) eklenen.push(metin);
  }
  return { metin: eklenen.length ? soru + " " + eklenen.join(" ") : soru, eklenen };
}

// ---------- Yeniden sıralama (varsayılan kapalı; hatada fail-open) ----------

async function yenidenSirala(soru, adaylar, topK) {
  try {
    const r = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${process.env.CF_ACCOUNT_ID}/ai/run/@cf/baai/bge-reranker-base`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${process.env.CF_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: soru,
          contexts: adaylar.map((a) => ({ text: (a.icerik || "").slice(0, 1200) })),
          top_k: topK,
        }),
      }
    );
    if (!r.ok) return null;
    const rj = await r.json();
    const liste = (rj.result && rj.result.response) || rj.response || [];
    return liste
      .filter((x) => Number.isInteger(x.index) && adaylar[x.index])
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .map((x) => ({ ...adaylar[x.index], rerank_skor: x.score || 0 }));
  } catch {
    return null;
  }
}

// ---------- Ana arama ----------

async function aramaYap(soru, secenek = {}) {
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

  const ayar = await ayarGetir();

  let embedMetni = q;
  const genisletilen = [];
  if (!secenek.ham && ayar.genisletme !== 0) {
    const g = sorguGenislet(q);
    embedMetni = g.metin;
    genisletilen.push(...g.eklenen);
  }

  try {
    const er = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/ai/v1/embeddings`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${CF_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model: process.env.EMBED_MODEL || "@cf/baai/bge-m3",
          input: [embedMetni],
        }),
      }
    );
    if (!er.ok) throw new Error(`embedding endpoint ${er.status}`);
    const ej = await er.json();
    const vektor = ej && ej.data && ej.data[0] && ej.data[0].embedding;
    if (!vektor) throw new Error("embedding boş döndü");

    let eslesen = null;
    try {
      eslesen = await sbRest("rpc/match_belgeler_v2", {
        method: "POST",
        body: JSON.stringify({
          sorgu_embedding: vektor,
          eslesme_sayisi: 16,
          min_skor: ayar.min_skor,
          taze_boost: ayar.taze_boost,
          taze_gun: Math.round(ayar.taze_gun),
        }),
      });
    } catch {
      // v2 henüz kurulmadı (Supabase 404) veya geçici hata → eski RPC
      eslesen = await sbRest("rpc/match_belgeler", {
        method: "POST",
        body: JSON.stringify({
          sorgu_embedding: vektor,
          eslesme_sayisi: 16,
          min_skor: ayar.min_skor,
        }),
      });
    }

    // url-başı tekilleştirme (v2 kullanılmadıysa gereklidir)
    const gorulen = new Set();
    let adaylar = (eslesen || []).filter((s) => {
      const u = s.url || "";
      if (gorulen.has(u)) return false;
      gorulen.add(u);
      return true;
    });

    // Yeniden sıralama: ham kolu hiç açmaz; normal kol config'e bakar
    const rerankIstenen = secenek.rerank || (!secenek.ham && (ayar.rerank_top_k || 0) > 0);
    if (rerankIstenen && adaylar.length >= 3) {
      const sirali = await yenidenSirala(q, adaylar, 8);
      if (sirali) adaylar = sirali;
    }
    adaylar = adaylar.slice(0, 8);

    // Gösterim kalibrasyonu: ham cosine'u taban+0.30 bandında 0-100'e ölçekle
    const taban = Math.min(Math.max(ayar.min_skor, 0), 0.6);
    const temiz = adaylar.map((s) => ({
      id: s.id,
      kaynak: s.kaynak,
      baslik: s.baslik,
      url: s.url,
      tarih: s.tarih,
      icerik: (s.icerik || "").slice(0, 600),
      benzerlik: s.benzerlik,
      gosterim: s.rerank_skor
        ? Math.round(100 * Math.min(1, Math.max(0, s.rerank_skor)))
        : Math.round(100 * Math.min(1, Math.max(0, ((s.benzerlik || 0) - taban) / 0.3))),
    }));
    return { status: 200, body: { sonuc: temiz, genisletilen, ayar: { min_skor: ayar.min_skor } } };
  } catch (e) {
    return { status: 502, body: { hata: e.message || "bilinmeyen hata" } };
  }
}

export async function POST(req) {
  let q = "";
  const secenek = {};
  try {
    const govde = await req.json();
    q = govde.q || "";
    if (govde.ham) secenek.ham = 1;
    if (govde.rerank) secenek.rerank = 1;
  } catch {
    // gövde bozuk → q boş kalır
  }
  const r = await aramaYap(q, secenek);
  return Response.json(r.body, { status: r.status });
}

/** Test ve kolay çağrı için: /api/ara?q=soru (+&ham=1 / &rerank=1) */
export async function GET(req) {
  const p = new URL(req.url).searchParams;
  const secenek = {};
  if (p.get("ham")) secenek.ham = 1;
  if (p.get("rerank")) secenek.rerank = 1;
  const r = await aramaYap(p.get("q") || "", secenek);
  return Response.json(r.body, { status: r.status });
}
