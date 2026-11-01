// Asistan grafiği (LangGraph.js) — yönlendirici → veri toplama → üretim →
// doğrulama döngüsü. Kullanıcı kararı 2026-11-01: LangGraph.js ile gerçek
// düğümlü graf (Cloudflare edge/next-on-pages üzerinde; architect ONAY).
// Kurallar: sayısal doğrulama çift taraflı TR normalizasyonu + ~%0,5
// yuvarlama toleransıyla; en fazla 2 LLM turu (3. tur yerine uyarılı geçiş);
// bağlam boşsa LLM'e hiç girilmez; checkpointer YOK (Workers izolatları
// istekler arası kalıcı değildir — istek başına durumsuz graf).
import { StateGraph, START, END, Annotation } from "@langchain/langgraph";

export const BIST30 = ["AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "TRMET", "EKGYO",
  "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD",
  "MGROS", "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL",
  "THYAO", "TOASO", "TRALT", "TTKOM", "TUPRS", "VAKBN", "YKBNK"];

const ADLAR = {
  AEFES: "Anadolu Efes", AKBNK: "Akbank", ASELS: "Aselsan", ASTOR: "Astor Enerji",
  BIMAS: "BİM", EKGYO: "Emlak Konut", ENKAI: "Enka İnşaat", EREGL: "Erdemir",
  FROTO: "Ford Otosan", GARAN: "Garanti BBVA", GUBRF: "Gübretaş", ISCTR: "İş Bankası",
  KCHOL: "Koç Holding", KRDMD: "Kardemir D", MGROS: "Migros", PETKM: "Petkim",
  PGSUS: "Pegasus", SAHOL: "Sabancı Holding", SASA: "SASA Polyester", SISE: "Şişecam",
  TAVHL: "TAV Havalimanları", TCELL: "Turkcell", THYAO: "Türk Hava Yolları",
  TOASO: "Tofaş", TRALT: "Türk Altın İşletmeleri", TRMET: "TR Anadolu Metal",
  TTKOM: "Türk Telekom", TUPRS: "Tüpraş", VAKBN: "Vakıfbank", YKBNK: "Yapı Kredi",
};

export function trNormalize(metin) {
  return String(metin)
    .replace(/\u00a0/g, " ")
    .replace(/(\d)\.(\d{3})(?!\d)/g, "$1$2") // 1.234 (binlik) → 1234
    .replace(/,(\d)/g, ".$1");                // 12,5 → 12.5
}

export function sayilar(metin) {
  const harita = new Map();
  for (const m of trNormalize(metin).matchAll(/-?\d+(?:\.\d+)?/g)) {
    const v = parseFloat(m[0]);
    if (Number.isFinite(v)) harita.set(v.toFixed(2), v);
  }
  return harita;
}

// Cevaptaki her sayı bağlamda (yuvarlama toleransı ~%0,5) olmalı; olmayanlar
// döner — boş liste = doğrulama geçti.
export function uyusmazlik(cevap, baglam) {
  const c = sayilar(cevap);
  const b = sayilar(baglam);
  const eksik = [];
  for (const [anahtar, v] of c) {
    let var_mi = b.has(anahtar);
    if (!var_mi) {
      for (const w of b.values()) {
        if (w !== 0 && Math.abs(w - v) / Math.abs(w) < 0.005) { var_mi = true; break; }
        if (w === 0 && v === 0) { var_mi = true; break; }
      }
    }
    if (!var_mi) eksik.push(anahtar);
  }
  return eksik;
}

function kodlariBul(soru) {
  const bulunan = new Set();
  for (const kod of BIST30) {
    if (new RegExp(`\\b${kod}\\b`).test(soru)) bulunan.add(kod);
  }
  const kucuk = soru.toLocaleLowerCase("tr");
  for (const [kod, ad] of Object.entries(ADLAR)) {
    if (ad.length >= 5 && kucuk.includes(ad.toLocaleLowerCase("tr"))) bulunan.add(kod);
  }
  return [...bulunan].slice(0, 2);
}

const AsistanDurum = Annotation.Root({
  soru: Annotation(),
  kodlar: Annotation({ reducer: (a, b) => b ?? a ?? [], default: () => [] }),
  bulgular: Annotation({ reducer: (a, b) => b ?? a ?? [], default: () => [] }),
  kaynaklar: Annotation({ reducer: (a, b) => b ?? a ?? [], default: () => [] }),
  cevap: Annotation({ reducer: (a, b) => b ?? a ?? "", default: () => "" }),
  uyari: Annotation({ reducer: (a, b) => b ?? a ?? "", default: () => "" }),
  tur: Annotation({ reducer: (a, b) => b ?? a ?? 0, default: () => 0 }),
  geribildirim: Annotation({ reducer: (a, b) => b ?? a ?? "", default: () => "" }),
  bos: Annotation({ reducer: (a, b) => b ?? a ?? false, default: () => false }),
});

export function grafYap(araclar) {
  const { semanticGetir, stokVerisi, llmCagir } = araclar;
  const MAX_TUR = 2; // toplam LLM üretim turu (architect: 3. tur yerine uyarı)

  async function yonlendir(durum) {
    return { kodlar: kodlariBul(durum.soru) };
  }

  async function veriTopla(durum) {
    const bulgular = [];
    const kaynaklar = [];
    try {
      const belgeler = (await semanticGetir(durum.soru)) || [];
      belgeler.slice(0, 6).forEach((b, i) => {
        bulgular.push(`[K${i + 1}] ${b.tarih || ""} — ${b.baslik || ""} (${b.kaynak || "rapor"}):\n${(b.icerik || "").slice(0, 600)}`);
        kaynaklar.push({ etiket: `K${i + 1}`, baslik: `${b.tarih || ""} — ${b.baslik || ""}`, url: b.url || "" });
      });
    } catch { /* anlamsal arama düşse bile hisse verisi kurtarabilir */ }
    for (const kod of durum.kodlar || []) {
      try {
        const v = await stokVerisi(kod);
        if (!v) continue;
        bulgular.push(
          `[S1] ${v.ad} (${kod}) güncel teknik veri — son fiyat: ${v.son} TL; günlük değişim: ${v.gunluk}%; ` +
          `1 haftalık değişim: ${v.haftalik}%; 60 günlük getiri: ${v.deg60}%; EMA kısa/orta/uzun: ` +
          `${v.kisa}/${v.orta}/${v.uzun}; Wave Trend: ${v.wt}/${v.wt1}; kanal konumu: ${v.konum}%; ` +
          `trend gücü (r): ${v.r}; genel: ${v.genel}`
        );
        kaynaklar.push({ etiket: "S1", baslik: `${kod} güncel teknik verisi`, url: `/hisse/${kod}` });
      } catch { /* tek hisse aracı düşerse kalanla devam */ }
    }
    return { bulgular, kaynaklar, bos: bulgular.length === 0 };
  }

  async function uret(durum) {
    if (durum.bos) {
      return {
        cevap: "Bu soruya elimdeki kaynaklarda (rapor arşivi ve güncel hisse verileri) yeterli içerik bulamadım. " +
          "Farklı bir ifadeyle sorar ya da doğrudan bir hisse kodu yazarsanız (örn. GARAN) tekrar deneyebilirim.",
        uyari: "",
      };
    }
    const baglam = durum.bulgular.join("\n\n");
    const sistem =
      "Sen Borsa Raporları sitesinin (borsa-raporlari.pages.dev) yardımcı asistanısın. " +
      "Kurallar: (1) YALNIZCA sana verilen KAYNAKLAR bloğundaki bilgileri ve sayıları kullan; " +
      "kaynakta olmayan hiçbir sayı, tarih veya iddia yazma. (2) Kaynaklar soruyu karşılamıyorsa " +
      "bunu açıkça söyle. (3) Kaynaklara [K1], [S1] gibi etiketlerle atıf yap. (4) Yatırım tavsiyesi " +
      "VERME; analitik ve nötr dil kullan, sonuna kısa bir 'yatırım tavsiyesi değildir' notu ekle. " +
      "(5) Türkçe, 120-200 kelime, sade ve net.";
    const kullanici =
      `KAYNAKLAR:\n${baglam}\n\nSORU: ${durum.soru}` +
      (durum.geribildirim
        ? `\n\nÖNCEKİ CEVABIN REDDEDİLDİ. Şu sayılar kaynaklarda YOK, çıkar veya kaynakta geçen doğru değerlerle değiştir: ${durum.geribildirim.join(", ")}`
        : "");
    const cevap = await llmCagir([
      { role: "system", content: sistem },
      { role: "user", content: kullanici },
    ]);
    return { cevap, tur: (durum.tur || 0) + 1 };
  }

  async function dogrula(durum) {
    if (durum.bos || !durum.cevap) return { uyari: durum.uyari };
    const eksik = uyusmazlik(durum.cevap, durum.bulgular.join("\n"));
    if (!eksik.length) return { uyari: "" };
    if ((durum.tur || 0) < MAX_TUR) {
      return { geribildirim: eksik, tur: durum.tur };
    }
    return {
      uyari: "⚠️ Cevaptaki bazı sayılar kaynaklarda doğrulanamadı; bilgili okuyun ve kaynak sayfalardan teyit edin.",
    };
  }

  const graf = new StateGraph(AsistanDurum)
    .addNode("yonlendir", yonlendir)
    .addNode("veri_topla", veriTopla)
    .addNode("uret", uret)
    .addNode("dogrula", dogrula)
    .addEdge(START, "yonlendir")
    .addEdge("yonlendir", "veri_topla")
    .addEdge("veri_topla", "uret")
    .addEdge("uret", "dogrula")
    .addConditionalEdges("dogrula", (durum) =>
      durum.geribildirim && (durum.tur || 0) < MAX_TUR ? "revize" : "son",
    { revize: "uret", son: END });

  return graf.compile();
}
