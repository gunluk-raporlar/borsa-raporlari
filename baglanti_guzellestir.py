"""Ic baglantilari guzel-URL (uzantisiz) bicimine cevirir.

Neden: Cloudflare Pages, .html isteklerini 308 ile uzantısız adrese
yonlendirir; sitede ~29.600 ic href hala .html biciminde oldugu icin Google
Search Console "Yonlendirmeli sayfa" sinifi surekli besleniyor (2026-10-22
GSC raporu: 24 sayfa). Bu modul yalnizca GORELI ic href degerlerine dokunur:

  "raporlar.html"        -> "raporlar"
  "../index.html"        -> "../"       (dizin bicimi)
  "hisse/index.html"     -> "hisse/"
  "index.html"           -> "./"
  "/index.html"          -> "/"        (404.html'in kok-mutlak baglantilari)

Sorgu ("?") ve capa ("#") son ekleri korunur; harici (http/https/protokol-
relatif), semali (mailto/tel/javascript/data) ve bos degerler ile zaten
temiz degerler aynen birakilir. Cift tirnak VE tek tirnak işlenir (bot'un
haber karti ureticileri tek tirnakli href uretiyordu).

Dil agaclarinda (en/de/ru/zh) ayrica dil_yollari.slug_degistir uygulanir:
eski TR-slug'li baglantilar dogrudan yerlesik adlara tasınır (en/hisse/X ->
en/stocks/X); 301 zinciri ve fail-open middleware bagimliligi azalir.

Calistirilma noktalari (iki katman, 2026-10-22):
  1. publish.yml  -> repo agaci uzerinde, bos yayin commit'inden once
     (deploy su an kok dizininden yapildigi icin her yayini temiz gider).
  2. build.sh     -> dist/ kopyasi uzerinde (Cloudflare Pages her build'de;
     output dizini dashboard'ta dist'e alininca tek garanti noktasi olur).

site-arama.json bilincli HARIC: i18n.py'nin en-arama varlik filtresi "u"
degerini dosya yolu olarak kullaniyor; temiz deger filtreyi bosaltir.
Arama JS'i .html'li "u" degeriyle 308'e duser, zararsiz.

Idempotenttir: temiz degerler yeniden islemede degismez.
Kullanim: python baglanti_guzellestir.py [kok]   (varsayilan ".")
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dil_yollari import slug_degistir, SLUG_DILLER
except ImportError:  # bagimsiz kullanimda slug eslemesi olmadan da calisir
    def slug_degistir(deger, dil):
        return deger
    SLUG_DILLER = ()

# Dokunulmayacak deger onekleri: harici, protokol-relatif, capa, semali.
ATLA_ONEK = ("http://", "https://", "//", "#", "mailto:", "tel:", "ftp:",
             "ws:", "wss:", "javascript:", "data:", "sms:")

# Yurunmeyecek dizinler (site HTML'i icermeyen kaynak/veri/arac agaclari).
HARIC_DIZIN = {".git", "dist", "__pycache__", "web", "data", "docs",
               "functions", "pipeline", "logs", "node_modules",
               "ajan-uyumlulugu", ".github", "ceviri-kuyrugu", "kalite"}

HREF_RE = re.compile(r"""(href\s*=\s*)(["'])([^"'\s>]+)\2""", re.IGNORECASE)


def _deger_temizle(deger, dil):
    """Tek bir href degerini donusturur; dokunulmayacaksa aynen dondurur."""
    # Sorgu/capa ayir: donusum yalniz yol kismina uygulanir.
    kesme = len(deger)
    for i, ch in enumerate(deger):
        if ch in "?#":
            kesme = i
            break
    yol, son = deger[:kesme], deger[kesme:]
    if not yol or yol.startswith(ATLA_ONEK):
        return deger
    if yol.endswith("index.html"):
        # Dizin bicimi: "hisse/index.html" -> "hisse/", "index.html" -> "./"
        yol = yol[:-len("index.html")] or "./"
    elif yol.endswith(".html"):
        yol = yol[:-len(".html")]
    else:
        return deger  # zaten temiz (veya baska uzanti); capayla oldugu gibi
    if dil:
        yol = slug_degistir(yol, dil)
    if "//" in yol:  # kazara cift egik cizgiyi toparla (basindaki // zaten atlandi)
        yol = re.sub(r"(?<!:)/{2,}", "/", yol)
    return yol + son


def temizle(kok="."):
    """kok altindaki tum *.html dosyalarinin ic baglantilarini guzellestirir.

    Donus: {"dosya": degisen dosya sayisi, "baglanti": cevrilen href sayisi}
    """
    istat = {"dosya": 0, "baglanti": 0}
    for kok_dizin, dizinler, dosyalar in os.walk(kok):
        dizinler[:] = [d for d in dizinler if d not in HARIC_DIZIN]
        for ad in dosyalar:
            if not ad.endswith(".html"):
                continue
            tam = os.path.join(kok_dizin, ad)
            rel = os.path.relpath(tam, kok).replace("\\", "/")
            parcalar = rel.split("/")
            dil = parcalar[0] if len(parcalar) > 1 and parcalar[0] in SLUG_DILLER else None
            try:
                with open(tam, encoding="utf-8", newline="") as f:
                    icerik = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            sayac = {"n": 0}

            def _degistir(m):
                yeni = _deger_temizle(m.group(3), dil)
                if yeni != m.group(3):
                    sayac["n"] += 1
                    return m.group(1) + m.group(2) + yeni + m.group(2)
                return m.group(0)

            yeni_icerik = HREF_RE.sub(_degistir, icerik)
            if sayac["n"] and yeni_icerik != icerik:
                try:
                    with open(tam, "w", encoding="utf-8", newline="") as f:
                        f.write(yeni_icerik)
                except OSError:
                    continue
                istat["dosya"] += 1
                istat["baglanti"] += sayac["n"]
    return istat


if __name__ == "__main__":
    kok = sys.argv[1] if len(sys.argv) > 1 else "."
    s = temizle(kok)
    print("[Baglanti] %d dosyada %d ic baglanti guzel-URL'ye cevrildi (kok=%s)."
          % (s["dosya"], s["baglanti"], kok))
