import DurumPanel from "../../components/DurumPanel";

export const metadata = { title: "Durum & İzleme | Borsa Raporları" };
export const revalidate = 60;

export default function DurumPage() {
  return (
    <>
      <div className="hero" style={{ marginTop: 20 }}>
        <h1>Sistem Durumu</h1>
        <p>
          Rapor üretim hattının (LLM gecikmesi, embedding, rapor tazeliği) canlı metrikleri.
          Panoların okuduğu veri <code>data/metrics/latest.json</code>; Prometheus formatı için{" "}
          <a style={{ color: "#fff" }} href="/api/metrics">/api/metrics</a> adresi kullanılabilir.
        </p>
      </div>
      <DurumPanel />
    </>
  );
}
