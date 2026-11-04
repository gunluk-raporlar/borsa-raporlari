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
# muhasebe-terimleri/ (2026-11-04): IFRS sozlugunun harf-grup sayfari —
# terimler ile ayni nedenle listede olmak zorunda.
# temettu-veri/ (2026-11-01): temettu.html'in tarayicida okudugu JSON verileri.
for d in hisse raporlar derin-analiz makro-analiz radyo haftasonu haftasonu-egitimi terimler muhasebe-terimleri temettu-veri en de ru zh; do
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

# Dil secici baglantilari (2026-10-30): bolum index'leri (hisse/, radyo/ ...)
# ve tarihli arsiv sayfalari icin hedef cozumleyici guncellendi; dist'te daha
# once uretilmis eski menuler yayina cikmadan tazelenir. Fail-open.
python3 dil_baglantilari_tazele.py dist || echo "UYARI: dil menusu tazeleme atlandi"

# Ic baglanti guzel-URL temizligi (2026-10-22, GSC 'Yonlendirmeli sayfa'):
# dist kopyasindaki tum goreli .html hrefler uzantisiz adrese tasinir; dil
# agaclarinda eski TR-slug'lar yerlesik adlara eslenir. Fail-open: python
# yoksa/basarisizsa build eskisi gibi devam eder.
python3 baglanti_guzellestir.py dist || echo "UYARI: baglanti temizligi atlandi, dist .html linkli yayinlandi"

# Markdown aynalari (2026-11-04, ajan-okunurluk): secili sayfalarin .md kopyasi
# — ajan "<sayfa>.md" yazarak tablo/baslik korunmus surumu ceker. Sayfa listesi
# pipeline/md_ayna.py icinde. Fail-open: python yoksa site .md'siz yayinlanir.
python3 pipeline/md_ayna.py dist || echo "UYARI: md aynalari atlandi"

# Hreflang kumeleri (2026-11-04, cokdilli SEO): baglanti_guzellestir'den SONRA
# kosar; karşılık .html dist'te VARSA <head>'e rel=alternate hreflang kümesi
# ekler (güzel-URL, uzantısız). Zaten kümesi olan sayfa atlanır (idempotent).
# Eşleme tabloları pipeline/md_ayna.py KOK_ESLE/KLASOR_ESLE ile aynıdır.
python3 hreflang_ekle.py dist || echo "UYARI: hreflang atlandi"

# raporlar.xml (2026-11-05, ajan beslemesi): dist/raporlar/ altindaki .md
# raporlardan RSS 2.0 beslemesi; item linkleri dogrudan .md adresleridir.
# Tarih front matter'dan, yoksa dosya adindan okunur; en yeni 50 item,
# tarihe gore tersten. Fail-open: besleme uretilemezse build surer.
python3 - <<'PY' || echo "UYARI: raporlar.xml uretilemedi"
import os
import re
from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

SITE = "https://borsa-raporlari.pages.dev"
klasor = os.path.join("dist", "raporlar")
ogeler = []
for ad in (os.listdir(klasor) if os.path.isdir(klasor) else []):
    if not ad.endswith(".md"):
        continue
    with open(os.path.join(klasor, ad), encoding="utf-8", errors="replace") as fh:
        bas = fh.read(4096)
    baslik = re.search(r'^title:\s*"?([^"\r\n]+)"?\s*$', bas, re.M)
    tarih = re.search(r'^date:\s*"?(\d{4})-(\d{2})-(\d{2})', bas, re.M)
    if not tarih:
        tarih = re.search(r'(\d{4})-(\d{2})-(\d{2})', ad)
    if not tarih:
        continue                  # tarihsiz dosya (or. index.md) beslemeye girmez
    y, a, g = (int(t) for t in tarih.groups())
    ogeler.append({
        "tarih": (y, a, g),
        "baslik": baslik.group(1).strip() if baslik else ad[:-3],
        "link": SITE + "/raporlar/" + ad,
    })
ogeler.sort(key=lambda o: o["tarih"], reverse=True)
kalemler = []
for o in ogeler[:50]:
    yayin = format_datetime(datetime(*o["tarih"], tzinfo=ZoneInfo("Europe/Istanbul")))
    kalemler.append(
        "    <item>\n"
        "      <title>%s</title>\n"
        "      <link>%s</link>\n"
        '      <guid isPermaLink="true">%s</guid>\n'
        "      <pubDate>%s</pubDate>\n"
        "    </item>" % (escape(o["baslik"]), o["link"], o["link"], yayin))
rss = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
       '<channel>\n'
       '<title>Borsa Raporları — Günlük Piyasa Raporları</title>\n'
       '<link>' + SITE + "/raporlar</link>\n"
       '<description>Yapay zeka destekli günlük BIST 30 piyasa raporları: '
       'günün özeti, endeks durumu, teknik tarama ve osilatör sinyalleri '
       '(LLM/ajanlar için Markdown sürümleri).</description>\n'
       '<language>tr</language>\n'
       '<atom:link href="' + SITE + '/raporlar.xml" rel="self" '
       'type="application/rss+xml"/>\n'
       + "\n".join(kalemler) + "\n"
       "</channel>\n</rss>\n")
with open(os.path.join("dist", "raporlar.xml"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(rss)
print("[raporlar.xml] %d item yazildi" % len(kalemler))
PY
echo "raporlar.xml: $(wc -c < dist/raporlar.xml 2>/dev/null || echo 0) bayt, $(grep -c '<item>' dist/raporlar.xml 2>/dev/null || echo 0) item"

# MD link denetimi (2026-11-05): dist'teki .md aynalarindaki
# https://borsa-raporlari.pages.dev/... baglantilari karsiliksiz mi diye
# denetler; hedef .md'sizlestirilir, dist'te <yol>.html YA DA <yol>.md
# aranir. Kirik link build'i BLOKLAMAZ: yalniz uyarı basar, her kosulda
# exit 0. Fail-open: python yoksa denetim atlanir.
python3 - <<'PY' || echo "UYARI: md link denetimi atlandi"
import os
import re

SITE = "https://borsa-raporlari.pages.dev"
kok = "dist"
toplam = 0
kirik = []
for kkd, _alt, adlar in os.walk(kok):
    for a in adlar:
        if not a.endswith(".md"):
            continue
        md = os.path.join(kkd, a)
        with open(md, encoding="utf-8", errors="replace") as fh:
            icerik = fh.read()
        for hedef in re.findall(r"\]\(" + re.escape(SITE) + r"/([^)\s]*)\)", icerik):
            toplam += 1
            yol = hedef.split("#", 1)[0].rstrip("/")
            if yol.endswith(".md"):
                yol = yol[:-3]
            if not yol:
                continue                  # SITE kökü -> dist/index.html mevcut
            for aday in (yol + ".html", yol + ".md"):
                if os.path.isfile(os.path.join(kok, aday)):
                    break
            else:
                kirik.append((os.path.relpath(md, kok).replace("\\", "/"), hedef))
if kirik:
    for md, hedef in kirik:
        print("::warning::md kirik link: %s -> %s" % (md, hedef))
print("[md-link] %s (%d link)" % ("tum linkler OK" if not kirik
                                 else "%d kirik" % len(kirik), toplam))
PY

# llms-full.txt (2026-11-04, ajan beslemesi): dist'teki TUM .md aynalarinin
# (TR kök + en/de/ru/zh ağaçları) yol sırasıyla birlesik tek dosyasi; her
# dosya "## <yol>" başlığıyla ayrılır. llms.txt (kısa indeks) DOKUNULMAZ.
python3 - <<'PY' || echo "UYARI: llms-full.txt uretilemedi"
import os
kok = "dist"
mdler = []
for kkd, _alt, adlar in os.walk(kok):
    for a in adlar:
        if a.endswith(".md"):
            mdler.append(os.path.relpath(os.path.join(kkd, a), kok).replace("\\", "/"))
mdler.sort()
bolumler = []
for r in mdler:
    with open(os.path.join(kok, r), encoding="utf-8", errors="replace") as fh:
        bolumler.append("## %s\n\n%s" % (r, fh.read().strip()))
with open(os.path.join(kok, "llms-full.txt"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n\n".join(bolumler) + "\n")
print("[llms-full] %d md birlestirildi" % len(mdler))
PY
echo "llms-full.txt: $(wc -c < dist/llms-full.txt 2>/dev/null || echo 0) bayt, $(wc -l < dist/llms-full.txt 2>/dev/null || echo 0) satir"

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
