import TickerWidget from "../components/TickerWidget";
import SwrProvider from "../components/SwrProvider";
import "./globals.css";

export const metadata = {
  title: "BIST 30 Günlük Raporlar (Yeni)",
  description:
    "BIST 30'un yapay zeka destekli günlük raporları; semantik arama ve izleme panosu ile yeni nesil arayüz.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="tr">
      <body>
        <SwrProvider>
          <TickerWidget />
          <header className="site">
            <div className="kapsayici ic">
              <a className="logo" href="/">
                Borsa Raporları <span>yeni arayüz</span>
              </a>
              <nav className="menu">
                <a href="/">Arşiv</a>
                <a href="/ara">Akıllı Arama</a>
                <a href="/durum">Durum</a>
                <a href="https://borsa-raporlari.pages.dev/">Eski Site</a>
                <a href="https://github.com/gunluk-raporlar/borsa-raporlari">GitHub</a>
              </nav>
            </div>
          </header>
          <main className="kapsayici">{children}</main>
          <footer className="site">
            <div className="kapsayici">
              Veriler Borsa İstanbul gecikmeli fiyatlarla üretilir; hiçbir içerik yatırım tavsiyesi
              değildir. Raporlar GitHub Actions ile otomatik üretilir, bu arayüz Cloudflare
              Pages üzerinde yayınlanır.
            </div>
          </footer>
        </SwrProvider>
      </body>
    </html>
  );
}
