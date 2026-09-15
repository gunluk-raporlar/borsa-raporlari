import { raporListesi } from "../lib/data";

export const revalidate = 300;

function tarihYazi(t) {
  try {
    return new Date(`${t}T09:00:00`).toLocaleDateString("tr-TR", {
      day: "numeric",
      month: "long",
      year: "numeric",
      weekday: "long",
    });
  } catch {
    return t;
  }
}

export default async function Home() {
  let raporlar = [];
  let hata = false;
  try {
    raporlar = await raporListesi();
  } catch {
    hata = true;
  }

  return (
    <>
      <div className="hero">
        <h1>BIST 30 Günlük Piyasa Raporları</h1>
        <p>
          Hafta içi her sabah otomatik üretilen yapay zeka destekli analizler. Bu yeni arayüz;
          raporları ISR ile önbellekler, pgvector ile raporların <i>anlam düzeyinde</i> aramasını
          yapar.
        </p>
      </div>

      <div className="kart">
        <h2>Raporları anlamsal arayın</h2>
        <p className="kucuk">
          "faiz kararı sonrası bankalar ne demişti?" gibi doğal dilde sorular sorun — arama,
          embedding benzerliği ile rapor arşivinde dolaşır.
        </p>
        <p>
          <a className="btn" href="/ara" style={{ textDecoration: "none", display: "inline-block" }}>
            Akıllı Aramayı Aç →
          </a>
        </p>
      </div>

      <h2 className="section-title" style={{ marginTop: 26 }}>Rapor Arşivi</h2>
      {hata && <div className="bilgi">Rapor listesi şu anda alınamadı; birkaç dakika sonra tekrar deneyin.</div>}
      {!hata && raporlar.length === 0 && <div className="bilgi">Henüz rapor yayınlanmadı.</div>}
      <div className="grid">
        {raporlar.map((r) => (
          <a key={r.tarih} className="rcard" href={`/rapor/${r.tarih}`}>
            <span className="tarih">{tarihYazi(r.tarih)}</span>
            <span className="alt">{r.baslik} · raporu aç →</span>
          </a>
        ))}
      </div>
    </>
  );
}
