// Eski Render adresini Cloudflare Pages adresine 301 ile yonlendirir.
// Tum yollar ve sorgu parametreleri korunur; Google Adres Degisikligi
// aracinin zorunlu kilmarks oldugu 301 durum kodunu dondurur.
const http = require('http');

const HEDEF = 'https://borsa-raporlari.pages.dev';
const PORT = process.env.PORT || 3000;

http
  .createServer((istek, yanit) => {
    yanit.writeHead(301, { Location: HEDEF + istek.url });
    yanit.end();
  })
  .listen(PORT, () => {
    console.log(`Yonlendirme servisi ${PORT} portunda calisiyor -> ${HEDEF}`);
  });
