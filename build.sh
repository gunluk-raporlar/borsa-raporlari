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
for d in hisse raporlar derin-analiz makro-analiz radyo haftasonu haftasonu-egitimi en de ru zh; do
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

echo "dist hazir: $(find dist -type f | wc -l) dosya"
