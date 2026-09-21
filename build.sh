#!/usr/bin/env bash
# Cloudflare Pages build adimi.
# Amac: yayina YALNIZCA site dosyalarini koymak. Boylece kaynak kod (bot.py, *.py),
# ic veri (data/, pipeline/), arayuz kaynagi (web/) ve log arsivleri (*.zip, joblog.txt)
# pages.dev uzerinden indirilemez hale gelir.
set -e

rm -rf dist
mkdir -p dist

# Sayfa klasorleri (mp3'ler dahil)
for d in hisse reports radyo haftasonu haftasonu-egitimi en de ru zh; do
  if [ -d "$d" ]; then cp -r "$d" dist/; fi
done

# Kok dizindeki site dosyalari
cp *.html *.css *.json *.png *.svg *.xml *.txt _headers dist/ 2>/dev/null || true

# Site disi metin dosyalari
rm -f dist/joblog.txt dist/requirements.txt

echo "dist hazir: $(find dist -type f | wc -l) dosya"
