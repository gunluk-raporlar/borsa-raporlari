// /api/asistan-veri — asistanın güncel hisse verisi aracı (Pages Function).
// Kaynaklar raw.githubusercontent üzerinden depodan okunur (functions/api/
// robot.js deseni; GitHub raw'da hız limiti sorunu yok):
//   - data/teknik/<gun>.json  → son fiyat, günlük %, 60g %, EMA/WT sinyalleri
//   - data/fiyat/<KOD>.json   → kapanış serisi → 1 haftalık (5 işlem günü)
//     değişim BURADA hesaplanır; asistan LLM'i sayı HESAPLAMAZ (uydurma-sayı
//     sınıfının asistan tarafındaki karşılığı — architect onayı 2026-11-01).
// Sonuç 10 dk kenar önbelleğinde.

const RAW = "https://raw.githubusercontent.com/gunluk-raporlar/borsa-raporlari/main/data";
const CACHE_TTL = 600;

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

const trSayi = (v, hane = 2) =>
  Number(v).toLocaleString("tr-TR", { minimumFractionDigits: hane, maximumFractionDigits: hane });

const jsonYanit = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": `public, max-age=${CACHE_TTL}`,
  },
});

function istanbulGun(gunAraligi = 8) {
  // Bugünden geriye doğru son `gunAraligi` günün ISO tarihleri (hafta sonu
  // ve gecikmiş üretim günlerini atlamak için aday listesi).
  const gunler = [];
  const bugun = new Date();
  for (let i = 0; i < gunAraligi; i++) {
    const d = new Date(bugun.getTime() - i * 86400000);
    gunler.push(d.toISOString().slice(0, 10));
  }
  return gunler;
}

async function teknikSatiri() {
  for (const gun of istanbulGun()) {
    const r = await fetch(`${RAW}/teknik/${gun}.json`, { signal: AbortSignal.timeout(8000) });
    if (r.ok) {
      const satirlar = await r.json();
      if (Array.isArray(satirlar) && satirlar.length) return satirlar;
    }
  }
  return null;
}

async function fiyatSerisi(kod) {
  const r = await fetch(`${RAW}/fiyat/${kod}.json`, { signal: AbortSignal.timeout(8000) });
  if (!r.ok) return null;
  const seri = await r.json();
  const tarihler = Object.keys(seri).sort();
  if (tarihler.length < 6) return null;
  return tarihler.map((t) => ({ tarih: t, kapanis: Number(seri[t]) }));
}

export async function onRequestGet(context) {
  const kod = (context.params.kod || new URL(context.request.url).searchParams.get("kod") || "")
    .toUpperCase().replace(/[^A-Z]/g, "");
  if (!ADLAR[kod]) return jsonYanit({ hata: "bilinmeyen-kod", kod }, 400);

  const [satirlar, seri] = await Promise.all([teknikSatiri(), fiyatSerisi(kod)]);
  const satir = (satirlar || []).find((s) => s.hisse === kod) || null;
  if (!satir && !seri) return jsonYanit({ hata: "veri-yok", kod }, 404);

  let haftalik = null;
  if (seri && seri.length >= 6) {
    const son = seri[seri.length - 1].kapanis;
    const onceki = seri[seri.length - 6].kapanis;
    if (son && onceki) haftalik = trSayi((son / onceki - 1) * 100);
  }

  return jsonYanit({
    kod,
    ad: ADLAR[kod],
    son: satir ? trSayi(satir.son) : seri ? trSayi(seri[seri.length - 1].kapanis) : null,
    // gunluk ve deg60 teknik dosyasinda ZATEN yüzde birimidir (-1,18 = %−1,18);
    // 100 ile çarpılmaz (canlı testte yakalanan birim hatası, 2026-11-01).
    gunluk: satir ? trSayi(satir.gunluk) : null,
    haftalik,
    deg60: satir ? trSayi(satir.deg60) : null,
    kisa: satir?.kisa ?? null,
    orta: satir?.orta ?? null,
    uzun: satir?.uzun ?? null,
    wt: satir?.wt ?? null,
    wt1: satir?.wt1 != null ? trSayi(satir.wt1) : null,
    konum: satir ? trSayi(satir.konum || 0) : null,
    r: satir ? trSayi(satir.r || 0) : null,
    genel: satir?.genel ?? null,
    veri_gunu: satir ? null : seri?.[seri.length - 1]?.tarih,
  });
}
