# -*- coding: utf-8 -*-
"""Yayinlanmis HTML sayfalarinda Turkce kalinti taramasi (CI kapisi).

Neden var: 2026-11-25'te canli sayfalarda "%50-65" yerine "50-%65",
"15 %" -> "1%5" ve "vb. Portfoy" turu hatalar goruldu. editor.imla()
bilinen hatalari duzeltir; bu betik ise YAYINA CIKMIS sayfayi bagimsiz
denetler — duzeltme katmani atlanmis bir uretim yolu varsa yakalar.

Kapsam: <body> icindeki tum metin; script/style/nav/header/footer/svg
cikarilir. Tablo hucreleri AYRICA taranir (2026-11-25 architect bulgusu:
"%54-80" gibi gercek hatalar tablo hucresinde kalmisti) — ancak hucrelerde
"yuzde sayinin basinda" kurali UYGULANMAZ, cunku "+5.13%" bir veri
bicimidir, imla hatasi degil.

Kullanim:
    python sayfa_denetle.py                 # tum dizinler, tolerans 0
    python sayfa_denetle.py --tolerans 3    # en fazla 3 bulguya izin ver
    python sayfa_denetle.py --dizin raporlar,makro-analiz

Cikis kodu: 0 = temiz (veya tolerans icinde), 1 = bulgu var.
"""
import argparse
import glob
import html
import os
import re
import sys

import editor

KOK = os.path.dirname(os.path.abspath(__file__))
DIZINLER = ["derin-analiz", "raporlar", "makro-analiz", "haftasonu",
            "haftasonu-egitimi", "hisse"]

# Tablo hucresinde gecerli olmayan kural: veri bicimi ("+5.13%") imla hatasi
# degildir. Diger yuzde kurallari (aralikta tek '%', kaymis '%') hucrede de
# gecerlidir.
HUCRE_HARIC = "sayının başında"

ATLA = ("script", "style", "nav", "header", "footer", "svg")
# Veri/serit bolgeleri: fiyat seridi ("▼ -0.79%") bir veri bicimidir, imla
# kurali oraya uygulanmaz. Sinif adi bu parcalardan birini iceren alt agac
# tamamen atlanir.
VERI_SINIF = ("ticker", "serit", "strip", "pano", "grafik", "chart",
              "pos", "neg", "degisim", "fiyat")
TAG = re.compile(r"(<[^>]+>)")
SINIF = re.compile(r"""class=["']([^"']*)["']""")


def _metinler(yol: str):
    """(prose, hucre) ikilisi: tablo disi metin ve tablo hucresi metni."""
    with open(yol, encoding="utf-8", errors="replace") as fh:
        s = fh.read()
    m = re.search(r"<body[^>]*>(.*)</body>", s, re.S)
    if not m:
        return "", ""
    parcalar = TAG.split(m.group(1))
    yigin = []          # (etiket, veri_mi)
    tablo = 0
    prose, hucre = [], []
    for p in parcalar:
        if p.startswith("<") and p.endswith(">"):
            mm = re.match(r"</?([a-zA-Z0-9]+)", p)
            if mm:
                ad = mm.group(1).lower()
                kapanis = p.startswith("</")
                if ad == "table":
                    tablo = max(0, tablo - 1) if kapanis else tablo + 1
                if ad in ATLA:
                    if kapanis:
                        if yigin and yigin[-1][0] == ad:
                            yigin.pop()
                    elif not p.rstrip().endswith("/>"):
                        yigin.append((ad, False))
                elif not kapanis and not p.rstrip().endswith("/>"):
                    sm = SINIF.search(p)
                    veri = bool(sm) and any(
                        k in sm.group(1).lower() for k in VERI_SINIF)
                    yigin.append((ad, veri))
                elif kapanis and yigin and yigin[-1][0] == ad:
                    yigin.pop()
            continue
        if not p.strip():
            continue
        if any(v for _a, v in yigin):
            continue
        if any(a in ATLA for a, _v in yigin):
            continue
        (hucre if tablo else prose).append(p)
    return html.unescape("\n".join(prose)), html.unescape("\n".join(hucre))


HARF = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü]")


def _harfli(metin: str) -> str:
    """Yalniz harf iceren satirlari birakir.

    "+1.45%", "-%10,00 ▼", "-19.90%" gibi satirlar veri degeridir; imla
    kurali oraya uygulanmaz. Duz metin satirlari ("Fiyat ... %54-80
    bandinda") korunur.
    """
    return "\n".join(s for s in metin.split("\n") if HARF.search(s))


def bulgular(yol: str):
    prose, hucre = _metinler(yol)
    b = list(editor.kalite_tara(_harfli(prose)))
    b += [x for x in editor.kalite_tara(_harfli(hucre)) if HUCRE_HARIC not in x[1]]
    return b


def tara(dizinler, tolerans=0, ayrinti=3):
    toplam = 0
    sayfa_sayisi = 0
    hatali_sayfa = 0
    for d in dizinler:
        yollar = sorted(glob.glob(os.path.join(KOK, d, "*.html")))
        if not yollar:
            continue
        sayfa_sayisi += len(yollar)
        d_toplam = 0
        for yol in yollar:
            b = bulgular(yol)
            if not b:
                continue
            hatali_sayfa += 1
            d_toplam += len(b)
            print(f"  {os.path.relpath(yol, KOK)}")
            for _kural, aciklama, ornek in b[:ayrinti]:
                print(f"    - {aciklama}: ...{ornek.strip()[:100]}...")
        toplam += d_toplam
        print(f"=== {d}: {len(yollar)} sayfa, {d_toplam} bulgu ===")
    print(f"\nTOPLAM: {sayfa_sayisi} sayfa, {hatali_sayfa} sayfada {toplam} bulgu "
          f"(tolerans {tolerans})")
    return 0 if toplam <= tolerans else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tolerans", type=int, default=0,
                    help="kabul edilen en fazla bulgu sayisi (varsayilan 0)")
    ap.add_argument("--dizin", default="",
                    help="virgulle ayrilmis dizin listesi (varsayilan: hepsi)")
    ap.add_argument("--ayrinti", type=int, default=3,
                    help="sayfa basina gosterilecek bulgu sayisi")
    a = ap.parse_args(argv)
    dizinler = [d.strip() for d in a.dizin.split(",") if d.strip()] or DIZINLER
    return tara(dizinler, a.tolerans, a.ayrinti)


if __name__ == "__main__":
    sys.exit(main())
