#!/usr/bin/env bash
# Cloudflare Pages build adimi.
# Amac: yayina YALNIZCA site dosyalarini koymak. Boylece kaynak kod (bot.py, *.py),
# ic veri (data/, pipeline/), arayuz kaynagi (web/) ve log arsivleri (*.zip, joblog.txt)
# pages.dev uzerinden indirilemez hale gelir.
set -e

rm -rf dist
mkdir -p dist

# Sayfa klasorleri (mp3'ler dahil)
# derin-analiz/ + makro-analiz/ (2026-10-07): agac yapisinde tarihli arsiv
# sayfalari bu dizinlerde; listeye alinmazsa hub kartlari 404'e duser.
# terimler/ (2026-10-28): harf-grup sayfalari ayni sekilde listeye alinmali;
# listede yoksa dist'e kopyalanmaz ve tum terim linkleri 404'e duser.
for d in hisse raporlar derin-analiz makro-analiz radyo haftasonu haftasonu-egitimi terimler en de ru zh; do
  if [ -d "$d" ]; then cp -r "$d" dist/; fi
done

# Kok dizindeki site dosyalari
cp *.html *.css *.json *.png *.svg *.xml *.txt _headers _redirects dist/ 2>/dev/null || true

# Site disi metin dosyalari
rm -f dist/joblog.txt dist/requirements.txt

# Dil kopyalarinin style.css'ini kokle esitle (2026-10-11): bot koşusu kök
# style.css'i güncelleyip i18n koşusu dil kopyalarını eski halde birakirsa
# yayindaki dil sayfalari kökle ayni stili paylasir.
for l in en de ru zh; do
  if [ -f "dist/style.css" ] && [ -d "dist/$l" ]; then cp dist/style.css "dist/$l/style.css"; fi
done

# Ic baglanti guzel-URL temizligi (2026-10-22, GSC 'Yonlendirmeli sayfa'):
# dist kopyasindaki tum goreli .html hrefler uzantisiz adrese tasinir; dil
# agaclarinda eski TR-slug'lar yerlesik adlara eslenir. Fail-open: python
# yoksa/basarisizsa build eskisi gibi devam eder.
python3 baglanti_guzellestir.py dist || echo "UYARI: baglanti temizligi atlandi, dist .html linkli yayinlandi"

# --- Yayina girmeyecek kaynak/veri dosyalari kaldirilir (2026-10-22 gizlilik) ---
# Cloudflare Pages build'i GECICI bir klon uzerinde calisir; bu silme yalnizca
# bu build'in yuklenecek agacini etkiler, depoya ve yerel kopyaya dokunmaz.
# Cikis dizini kok ("/") oldugu icin kok agaci da temizlenir; bir gun cikis
# dist'e alinirsa bu blok etkisizlesir ama zarar vermez. IndexNow anahtar
# dosyasi (ba8235...txt) ve arama motoru dogrulama dosyalari BILINCLI KALIR.
# functions/ KALIR: dil agaclarinin 301 middleware'i orada.
rm -f dist/i18n-cache.json dist/i18n-elle.json dist/elle_*.json \
      dist/joblog.txt dist/requirements.txt dist/README.md 2>/dev/null || true
for l in en de ru zh; do
  find "dist/$l" -maxdepth 1 -name 'elle_*.json' -delete 2>/dev/null || true
  find "$l" -maxdepth 1 -name 'elle_*.json' -delete 2>/dev/null || true
done
rm -rf data pipeline web docs logs ajan-uyumlulugu .github __pycache__ kalite
rm -f *.py *.bak *.patch *.zip joblog.txt README.md requirements.txt \
      i18n-cache.json i18n-elle.json elle_*.json 2>/dev/null || true

echo "dist hazir: $(find dist -type f | wc -l) dosya"
