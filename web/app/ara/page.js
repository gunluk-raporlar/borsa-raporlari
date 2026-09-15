import SearchBox from "../../components/SearchBox";

export const metadata = { title: "Akıllı Arama | Borsa Raporları" };

export default function AraPage() {
  return (
    <>
      <div className="hero" style={{ marginTop: 20 }}>
        <h1>Akıllı Arama</h1>
        <p>
          Rapor arşivi embedding vektörleri (pgvector) üzerinden anlam düzeyinde aranır — anahtar
          kelime değil, <i>anlam</i> eşleşmesi.
        </p>
      </div>
      <SearchBox />
      <div className="kart">
        <p className="kucuk">
          Örnek sorular: "TCMB faiz kararı sonrası banka yorumları", "RSI aşırı alım sinyali veren
          hisseler hangileri?", "hafta sonu gündeminde jeopolitik neler vardı?"
        </p>
      </div>
    </>
  );
}
