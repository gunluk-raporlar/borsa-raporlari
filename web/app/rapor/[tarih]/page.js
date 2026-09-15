import { raporHtmlGetir } from "../../../lib/data";

export const runtime = "edge";
export const revalidate = 3600;

export async function generateMetadata({ params }) {
  const { tarih } = await params;
  return { title: `Günlük Rapor — ${tarih} | Borsa Raporları` };
}

export default async function RaporSayfasi({ params }) {
  const { tarih } = await params;
  const html = await raporHtmlGetir(tarih);
  if (!html) {
    return (
      <div className="bilgi" style={{ marginTop: 20 }}>
        {tarih} tarihli rapor bulunamadı. <a href="/">Arşive dön</a>.
      </div>
    );
  }
  return (
    <>
      <p style={{ marginTop: 18 }}>
        <a href="/">← Arşiv</a> · <a href={`/ara?q=${params.tarih}`}>Bu tarihi arşivde ara</a>
      </p>
      <article className="report-page" dangerouslySetInnerHTML={{ __html: html }} />
    </>
  );
}
