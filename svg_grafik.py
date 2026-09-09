# -*- coding: utf-8 -*-
"""SVG Gorsel Motoru — Borsa Okulu dersleri icin sematik/temsili grafikler.

Deterministik (konu adindan seed) -> dosya/dis servis yok, sayfaya SVG gomulur.
Gorseller EĞİTİM AMAÇLI ŞEMALARDIR (gercek piyasa verisi degil). Mum/osilator
serileri ornek veriden gercek formullerle (RSI/MACD/Stoch/CCI/WT) uretilir.
"""
import math
import random

RENK = {"yesil": "#047857", "kirmizi": "#b91c1c", "teal": "#0d9488", "lacivert": "#0f172a",
        "gri": "#64748b", "acik": "#e2e8f0", "mavi": "#2563eb", "mor": "#7c3aed",
        "turuncu": "#d97706"}


def _seed(konu):
    return random.Random(sum(ord(c) * (i + 3) for i, c in enumerate(konu)) % (2 ** 32))


def _sarmal(w, h, govde, baslik=None, alt="şematik — temsilidir"):
    ek = ""
    if baslik:
        ek += (f'<text x="14" y="26" font-family="Segoe UI,Arial" font-size="15" font-weight="700" '
               f'fill="{RENK["lacivert"]}">{baslik}</text>')
        govde = f'<g transform="translate(0,40)">{govde}</g>'
    if alt:
        ek += (f'<text x="{w - 10}" y="{h - 8}" font-family="Segoe UI,Arial" font-size="11" '
               f'fill="{RENK["gri"]}" text-anchor="end">{alt}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" '
            f'style="width:100%;height:auto;background:#fff;border:1px solid {RENK["acik"]};border-radius:10px;">'
            f'{ek}{govde}</svg>')


def _lejant_cizgi(govde, x, y, ad, renk):
    govde += (f'<line x1="{x}" y1="{y}" x2="{x + 16}" y2="{y}" stroke="{renk}" stroke-width="2.6"/>'
              f'<text x="{x + 22}" y="{y + 4}" font-family="Segoe UI,Arial" font-size="11.5" '
              f'fill="{RENK["lacivert"]}">{ad}</text>')
    return govde


def _seri(konu, n=70, bas=100, oynak=1.6, egim=0.0, ek="x"):
    rnd = _seed(konu + ek)
    degerler = [bas]
    for _ in range(n - 1):
        degerler.append(max(5, degerler[-1] * (1 + egim / n + rnd.gauss(0, oynak) / 100)))
    return degerler


def _ema(vals, p):
    a = 2 / (p + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * a + e * (1 - a)
        out.append(e)
    return out


def _rsi(vals, p=14):
    out, ka, ki = [], None, None
    for i in range(1, len(vals)):
        f = vals[i] - vals[i - 1]
        if i <= p:
            ka = (ka or 0) + max(f, 0)
            ki = (ki or 0) + max(-f, 0)
            out.append(50.0 if i < p else (100 if ki == 0 else 100 - 100 / (1 + (ka / p) / (ki / p))))
        else:
            ka = (ka * (p - 1) + max(f, 0)) / p
            ki = (ki * (p - 1) + max(-f, 0)) / p
            out.append(100 if ki == 0 else 100 - 100 / (1 + ka / ki))
    return [50.0] + out


def _stok(vals, p=14):
    out = []
    for i in range(len(vals)):
        if i < p:
            out.append(50.0)
            continue
        dilim = vals[i - p + 1:i + 1]
        en_d, en_y = min(dilim), max(dilim)
        out.append(100 if en_y == en_d else (vals[i] - en_d) / (en_y - en_d) * 100)
    return out


def _cci(vals, p=20):
    out = []
    for i in range(len(vals)):
        if i < p:
            out.append(0.0)
            continue
        dilim = vals[i - p + 1:i + 1]
        tp = sum(dilim) / p
        sap = sum(abs(v - tp) for v in dilim) / p
        out.append(0.0 if sap == 0 else (vals[i] - tp) / (0.015 * sap))
    return out


def _wt(vals):
    esa = _ema(vals, 10)
    d = _ema([abs(v - e) for v, e in zip(vals, esa)], 10)
    ci = [0.0 if dd == 0 else (v - e) / (0.015 * dd) for v, e, dd in zip(vals, esa, d)]
    wt1 = _ema(ci, 21)
    wt2 = [sum(wt1[max(0, i - 3):i + 1]) / len(wt1[max(0, i - 3):i + 1]) for i in range(len(wt1))]
    return wt1, wt2


def _macd(vals):
    e12 = _ema(vals, 12)
    e26 = _ema(vals, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    return macd, _ema(macd, 9)


def _olcek(veriler, pay=0.08):
    lo = min(min(v) for v in veriler)
    hi = max(max(v) for v in veriler)
    m = (hi - lo) * pay or 1
    return lo - m, hi + m


def _zaman_govde(w, h, seriler, renkler, kalinliklar, bant=None):
    """Ortak zaman serisi cizimi. seriler: liste; bant=(alt,ust,renk,etiket)."""
    x0, y0, x1, y1 = 46, 12, w - 14, h - 26
    govde = ""
    for gx in range(6):
        x = x0 + (x1 - x0) * gx / 6
        govde += f'<line x1="{x:.0f}" y1="{y0}" x2="{x:.0f}" y2="{y1}" stroke="{RENK["acik"]}" stroke-width="1"/>'
    govde += (f'<text x="{x0}" y="{y1 + 15}" font-family="Segoe UI,Arial" font-size="11" '
              f'fill="{RENK["gri"]}">zaman →</text>')
    if bant:
        seriler_ = seriler + [[bant[0]], [bant[1]]]
    else:
        seriler_ = seriler
    alt_v, ust_v = _olcek(seriler_)
    n = max(len(s) for s in seriler)

    def oy(v):
        return y1 - (v - alt_v) / (ust_v - alt_v) * (y1 - y0)

    if bant:
        alt, ust, renk, etiket = bant
        govde += (f'<rect x="{x0}" y="{oy(ust):.1f}" width="{x1 - x0:.0f}" '
                  f'height="{max(0, oy(alt) - oy(ust)):.1f}" fill="{renk}" opacity="0.14"/>')
        for v, c in ((ust, RENK["kirmizi"]), (alt, RENK["yesil"])):
            govde += (f'<line x1="{x0}" y1="{oy(v):.1f}" x2="{x1}" y2="{oy(v):.1f}" stroke="{c}" '
                      f'stroke-width="1.2" stroke-dasharray="6,4" opacity="0.75"/>')
        govde += (f'<text x="{x1 - 4}" y="{oy(ust) - 5:.1f}" font-family="Segoe UI,Arial" font-size="10.5" '
                  f'fill="{RENK["gri"]}" text-anchor="end">{etiket or ""}</text>')
    for s, renk, kalin in zip(seriler, renkler, kalinliklar):
        noktalar = " ".join(f"{x0 + (x1 - x0) * i / max(1, n - 1):.1f},{oy(v):.1f}" for i, v in enumerate(s))
        govde += f'<polyline points="{noktalar}" fill="none" stroke="{renk}" stroke-width="{kalin}" stroke-linejoin="round"/>'
    return govde


# ---------------- gorunumler ----------------

def mum_gorsel(konu):
    rnd = _seed(konu + "-mum")
    w, h = 560, 300
    x0, y0, x1, y1 = 60, 60, 540, 210
    govde = ""
    govde += (f'<text x="{x0}" y="{y0 - 12}" font-family="Segoe UI,Arial" font-size="12" fill="{RENK["gri"]}">'
              f'Gövde = açılış/kapanış • Fitil = en yüksek/en düşük</text>')
    genislik = (x1 - x0) / 16

    def oy(v):
        return y1 - (v - 55) / 180 * (y1 - y0)

    etiketler = {2: "Çekiç", 7: "Boğa formasyonu", 11: "Doji"}
    for i in range(14):
        x = x0 + i * (x1 - x0) / 14 + genislik / 2
        acilis = 120 + rnd.uniform(-15, 15)
        kapanis = acilis + rnd.uniform(-24, 24)
        eny = max(acilis, kapanis) + rnd.uniform(2, 10)
        end = min(acilis, kapanis) - rnd.uniform(2, 10)
        artis = kapanis >= acilis
        renk = RENK["yesil"] if artis else RENK["kirmizi"]
        govde += (f'<line x1="{x:.1f}" y1="{oy(eny):.1f}" x2="{x:.1f}" y2="{oy(end):.1f}" stroke="{renk}" stroke-width="1.4"/>'
                  f'<rect x="{x - genislik / 2:.1f}" y="{oy(max(acilis, kapanis)):.1f}" width="{genislik:.1f}" '
                  f'height="{max(1.5, abs(oy(acilis) - oy(kapanis))):.1f}" rx="1.5" fill="{renk}"/>')
        if i in etiketler:
            govde += (f'<text x="{x:.1f}" y="{oy(eny) - 8:.1f}" font-family="Segoe UI,Arial" font-size="11" '
                      f'font-weight="600" fill="{RENK["lacivert"]}" text-anchor="middle">{etiketler[i]}</text>')
    govde += (f'<rect x="78" y="238" width="16" height="12" rx="3" fill="{RENK["yesil"]}"/>'
              f'<text x="100" y="248" font-family="Segoe UI,Arial" font-size="12" fill="{RENK["lacivert"]}">yükseliş mumu</text>'
              f'<rect x="215" y="238" width="16" height="12" rx="3" fill="{RENK["kirmizi"]}"/>'
              f'<text x="237" y="248" font-family="Segoe UI,Arial" font-size="12" fill="{RENK["lacivert"]}">düşüş mumu</text>')
    return _sarmal(w, h, govde, "Mum Grafiği Anatomisi")


def cizgi_gorsel(konu, seviyeler=None, baslik="Fiyat ve seviyeler", alt_metin=None):
    seri = _seri(konu, n=60, oynak=1.15, egim=0.5)
    govde = _zaman_govde(560, 235, [seri], [RENK["lacivert"]], [2.6])
    if seviyeler:
        yuks = [88, 122, 156]
        for i, (et, v) in enumerate(seviyeler):
            y = yuks[i % len(yuks)]
            govde += (f'<line x1="46" y1="{y}" x2="546" y2="{y}" stroke="{RENK["mavi"]}" stroke-width="1.7" stroke-dasharray="7,4"/>'
                      f'<text x="540" y="{y - 6}" font-family="Segoe UI,Arial" font-size="12" fill="{RENK["mavi"]}" '
                      f'text-anchor="end">{et}</text>')
    return _sarmal(560, 300, govde, baslik, alt=alt_metin or "şematik — temsilidir")


def iki_ma_gorsel(konu):
    seri = _seri(konu, n=70, oynak=1.9, egim=1.2)
    kisa = _ema(seri, 9)
    uzun = _ema(seri, 21)
    govde = _zaman_govde(560, 235, [seri, kisa, uzun], [RENK["lacivert"], RENK["turuncu"], RENK["mavi"]], [2.2, 2.4, 2.4])
    govde = _lejant_cizgi(govde, 60, 18, "Fiyat", RENK["lacivert"])
    govde = _lejant_cizgi(govde, 170, 18, "Hızlı ortalama", RENK["turuncu"])
    govde = _lejant_cizgi(govde, 330, 18, "Yavaş ortalama", RENK["mavi"])
    return _sarmal(560, 300, govde, "Hareketli Ortalamalar — hızlı/yavaş kesişimi")


def _harita(vals, lo, hi):
    span = (hi - lo) or 1
    return [max(0.0, min(100.0, (v - lo) / span * 100.0)) for v in vals]


def osilator_gorsel(konu, tip, bant, baslik, renk):
    seri = _seri(konu, n=80, oynak=1.7, egim=0.25)
    if tip == "rsi":
        veri = _rsi(seri)
    elif tip == "stoch":
        veri = _stok(seri)
    elif tip == "cci":
        veri = _harita(_cci(seri), -160, 160)
    elif tip == "adx":
        egimler = [abs(seri[i] - seri[i - 1]) for i in range(1, len(seri))]
        veri = _harita(_ema([0.0] + egimler, 14), 0, max(egimler) * 1.2 or 1)
    else:
        w1, _w2 = _wt(seri)
        veri = _harita(w1, -90, 90)
    govde = _zaman_govde(560, 235, [veri], [renk], [2.6], bant=bant)
    govde = _lejant_cizgi(govde, 60, 18, "Gösterge", renk)
    govde += (f'<text x="200" y="22" font-family="Segoe UI,Arial" font-size="11.5" fill="{RENK["gri"]}">'
              f'Bant dışı aşırı bölge • içeri dönüş sinyal adayı</text>')
    return _sarmal(560, 300, govde, baslik)


def macd_gorsel(konu):
    seri = _seri(konu, n=90, oynak=1.9, egim=0.5)
    macd, sinyal = _macd(seri)
    # egitim semasi: sifir cizgisi etrafinda salinimi gostermek icin ortala
    ort = sum(macd) / len(macd)
    macd = [v - ort for v in macd]
    sinyal = _ema(macd, 9)
    x0, y0, x1, y1 = 46, 12, 546, 128
    govde = _zaman_govde(560, 150, [seri], [RENK["lacivert"]], [2.2], bant=None)
    govde += f'<g transform="translate(0,160)">'
    lo, hi = min(min(macd), min(sinyal), 0), max(max(macd), max(sinyal), 0)
    m = len(macd)

    def oy(v):
        return y1 - (v - lo) / ((hi - lo) or 1) * (y1 - y0)

    govde += (f'<text x="{x0}" y="{y0 - 5}" font-family="Segoe UI,Arial" font-size="11" fill="{RENK["gri"]}">'
              f'MACD paneli (histogram sıfır çevresinde salınır)</text>')

    for i, v in enumerate(macd):
        x = x0 + (x1 - x0) * i / (m - 1)
        ust, alt = oy(max(v, 0)), oy(min(v, 0))
        govde += (f'<rect x="{x:.1f}" y="{min(ust, alt):.1f}" width="2.4" height="{max(0.8, abs(alt - ust)):.1f}" '
                  f'fill="{RENK["yesil"] if v >= 0 else RENK["kirmizi"]}" opacity="0.85"/>')
    for ser, renk in ((macd, RENK["mavi"]), (sinyal, RENK["turuncu"])):
        noktalar = " ".join(f"{x0 + (x1 - x0) * i / (m - 1):.1f},{oy(v):.1f}" for i, v in enumerate(ser))
        govde += f'<polyline points="{noktalar}" fill="none" stroke="{renk}" stroke-width="2.1"/>'
    sifir = oy(0)
    govde += (f'<line x1="{x0}" y1="{sifir:.1f}" x2="{x1}" y2="{sifir:.1f}" stroke="{RENK["lacivert"]}" '
              f'stroke-width="1.6" stroke-dasharray="5,3"/>')
    govde += (f'<text x="{x0 + 2}" y="{sifir - 4:.1f}" font-family="Segoe UI,Arial" font-size="11" '
              f'font-weight="700" fill="{RENK["lacivert"]}">0</text>')
    govde += f'</g>'
    govde = _lejant_cizgi(govde, 46, 320, "MACD", RENK["mavi"])
    govde = _lejant_cizgi(govde, 130, 320, "Sinyal", RENK["turuncu"])
    govde = _lejant_cizgi(govde, 230, 320, "Histogram", RENK["yesil"])
    return _sarmal(560, 350, govde, "MACD — momentum ve kesişimler")


def kanal_gorsel(konu):
    rnd = _seed(konu + "-kanal")
    n = 60
    seri = []
    v = 100.0
    for _ in range(n):
        v = v + 0.55 + rnd.gauss(0, 2.2)
        seri.append(v)
    xo = (n - 1) / 2
    yo = sum(seri) / n
    egim = sum((i - xo) * (s - yo) for i, s in enumerate(seri)) / (sum((i - xo) ** 2 for i in range(n)) or 1)
    kesim = yo - egim * xo
    merkez = [kesim + egim * i for i in range(n)]
    sap = math.sqrt(sum((s - m) ** 2 for s, m in zip(seri, merkez)) / n)
    govde = _zaman_govde(560, 235, [seri, merkez], [RENK["lacivert"], RENK["teal"]], [2.2, 2.6], bant=None)
    # uyumlu olcekli bant cizgisi: seriyi elle ayni alana yerlestir (govde normalize)
    alt_v, ust_v = _olcek([seri, [m + 2 * sap for m in merkez], [m - 2 * sap for m in merkez]])
    y0, y1 = 12, 209

    def oy2(v):
        return y1 - (v - alt_v) / (ust_v - alt_v) * (y1 - y0)

    x0, x1 = 46, 546
    for cizgi, renk in (([m + 2 * sap for m in merkez], "#94a3b8"), ([m - 2 * sap for m in merkez], "#94a3b8")):
        noktalar = " ".join(f"{x0 + (x1 - x0) * i / (n - 1):.1f},{oy2(v):.1f}" for i, v in enumerate(cizgi))
        govde += f'<polyline points="{noktalar}" fill="none" stroke="{renk}" stroke-width="1.5" stroke-dasharray="6,4"/>'
    govde = _lejant_cizgi(govde, 60, 18, "Fiyat", RENK["lacivert"])
    govde = _lejant_cizgi(govde, 170, 18, "Regresyon (orta)", RENK["teal"])
    govde = _lejant_cizgi(govde, 350, 18, "±2σ bant", "#94a3b8")
    return _sarmal(560, 300, govde, "Regresyon Kanalı — %0 alt bant, %100 üst bant")


def dagilim_gorsel(konu, negatif=False):
    rnd = _seed(konu + ("-neg" if negatif else "-poz"))
    govde = ""
    x0, y0, x1, y1 = 40, 36, 300, 196
    govde += (f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#f8fafc" '
              f'stroke="{RENK["acik"]}" stroke-width="1"/>')
    noktalar = []
    xc = (x1 + x0) / 2
    eg = -0.8 if negatif else 0.8
    for i in range(30):
        x = x0 + 8 + rnd.random() * (x1 - x0 - 16)
        y = (y0 + y1) / 2 - eg * (x - xc) * 0.55 + rnd.gauss(0, 16)
        y = max(y0 + 6, min(y1 - 6, y))
        noktalar.append((x, y))
        govde += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{RENK["teal"]}" opacity="0.85"/>'
    # fit cizgisi
    xs = [p[0] for p in noktalar]
    ys = [p[1] for p in noktalar]
    xo = sum(xs) / len(xs)
    yo = sum(ys) / len(ys)
    eg2 = sum((x - xo) * (y - yo) for x, y in zip(xs, ys)) / (sum((x - xo) ** 2 for x in xs) or 1)
    kes = yo - eg2 * xo
    govde += (f'<line x1="{x0 + 8:.0f}" y1="{kes + eg2 * (x0 + 8):.1f}" x2="{x1 - 8:.0f}" '
              f'y2="{kes + eg2 * (x1 - 8):.1f}" stroke="{RENK["mavi"]}" stroke-width="2.6"/>')
    govde += (f'<text x="{x0}" y="{y1 + 15}" font-family="Segoe UI,Arial" font-size="11" fill="{RENK["gri"]}">varlık A →</text>')
    govde += (f'<text x="{x0}" y="{y0 - 6}" font-family="Segoe UI,Arial" font-size="11" fill="{RENK["gri"]}">varlık B ↓</text>')
    govde += (f'<text x="{x0}" y="{y1 + 33}" font-family="Segoe UI,Arial" font-size="13.5" font-weight="600" fill="{RENK["lacivert"]}">'
              f'r ≈ {"−0,82 — ters yönlü" if negatif else "+0,82 — aynı yönlü"} hareket</text>')
    govde += (f'<text x="{x0}" y="{y1 + 50}" font-family="Segoe UI,Arial" font-size="11.5" fill="{RENK["gri"]}">'
              f'{"Biri düşerken diğeri yükselir — çeşitlendirme için değerli" if negatif else "Fiyat ve gösterge birlikte — trend güvenilir"}</text>')
    return _sarmal(340, 320, govde, "Korelasyon (r)", alt=None)


def hacim_gorsel(konu):
    rnd = _seed(konu + "-hacim")
    x0, y0, x1, y1 = 46, 45, 546, 200
    govde = ""
    govde += (f'<text x="{x0}" y="30" font-family="Segoe UI,Arial" font-size="12.5" fill="{RENK["gri"]}">'
              f'Yüksek bar = yoğun işlem; fiyat hareketini doğrular</text>')
    adet = 36
    for i in range(adet):
        x = x0 + (x1 - x0) * i / adet
        hbar = (0.15 + rnd.random() * 0.85) * (y1 - y0)
        renk = RENK["yesil"] if rnd.random() > 0.45 else RENK["kirmizi"]
        govde += (f'<rect x="{x + 2:.1f}" y="{y1 - hbar:.1f}" width="{(x1 - x0) / adet - 4:.1f}" '
                  f'height="{hbar:.1f}" rx="1" fill="{renk}" opacity="0.8"/>')
    return _sarmal(560, 235, govde, "Hacim")


def buyume_gorsel(konu):
    x = list(range(120))
    lineer = [100 + 0.8 * i for i in x]
    bilesik = [100 * (1.012 ** i) for i in x]
    govde = _zaman_govde(560, 235, [lineer, bilesik], [RENK["gri"], RENK["yesil"]], [2.2, 3])
    govde = _lejant_cizgi(govde, 60, 18, "Doğrusal", RENK["gri"])
    govde = _lejant_cizgi(govde, 170, 18, "Bileşik", RENK["yesil"])
    return _sarmal(560, 300, govde, "Bileşik Getiri — zamanla açılan fark")


def pasta_gorsel(konu, dilimler, baslik):
    cx, cy, r = 140, 120, 90
    toplam = sum(d for _, d in dilimler)
    govde = ""
    aci = -90.0
    renkler = ["#0d9488", "#2563eb", "#d97706", "#7c3aed", "#047857", "#64748b", "#b91c1c", "#ca8a04"]
    for i, (ad, deger) in enumerate(dilimler):
        pay = deger / toplam
        aci2 = aci + 360 * pay
        x1 = cx + r * math.cos(math.radians(aci))
        y1 = cy + r * math.sin(math.radians(aci))
        x2 = cx + r * math.cos(math.radians(aci2))
        y2 = cy + r * math.sin(math.radians(aci2))
        buyuk = 1 if pay > 0.5 else 0
        govde += (f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {buyuk} 1 {x2:.1f} {y2:.1f} Z" '
                  f'fill="{renkler[i % len(renkler)]}" stroke="#fff" stroke-width="2"/>')
        aci = aci2
    ly = 30
    for i, (ad, deger) in enumerate(dilimler):
        govde += (f'<rect x="260" y="{ly}" width="12" height="12" rx="3" fill="{renkler[i % len(renkler)]}"/>'
                  f'<text x="280" y="{ly + 11}" font-family="Segoe UI,Arial" font-size="12.5" '
                  f'fill="{RENK["lacivert"]}">{ad} — %{deger:.0f}</text>')
        ly += 23
    return _sarmal(560, 280, govde, baslik)


def akis_gorsel(konu, adimlar, baslik):
    govde = ""
    kutu_w, kutu_h = 150, 62
    x = 14
    for i, (metin, renk) in enumerate(adimlar):
        y = 70 if i % 2 == 0 else 160
        govde += (f'<rect x="{x}" y="{y}" width="{kutu_w}" height="{kutu_h}" rx="10" fill="{renk}" opacity="0.1" '
                  f'stroke="{renk}" stroke-width="1.7"/>')
        govde += (f'<text x="{x + kutu_w / 2}" y="{y + 36}" font-family="Segoe UI,Arial" font-size="12.5" '
                  f'font-weight="600" fill="{RENK["lacivert"]}" text-anchor="middle">{metin}</text>')
        if i < len(adimlar) - 1:
            y2 = 70 if (i + 1) % 2 == 0 else 160
            govde += (f'<line x1="{x + kutu_w}" y1="{y + kutu_h / 2}" x2="{x + kutu_w + 16}" y2="{y2 + kutu_h / 2}" '
                      f'stroke="{RENK["gri"]}" stroke-width="2" marker-end="url(#ok)"/>')
        x += kutu_w + 60
    govde += ('<defs><marker id="ok" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">'
              f'<path d="M0,0 L9,4.5 L0,9 z" fill="{RENK["gri"]}"/></marker></defs>')
    return _sarmal(560, 250, govde, baslik)


# konu -> gorsel
GORSEL_ESLEME = [
    ("RSI", lambda k: osilator_gorsel(k, "rsi", (30, 70, "#fef2f2", "70 / 30"), "RSI (14) — momentum göstergesi", RENK["mor"])),
    ("Stokastik", lambda k: osilator_gorsel(k, "stoch", (20, 80, "#fef2f2", "80 / 20"), "Stokastik %K — hız ve konum", RENK["mor"])),
    ("CCI", lambda k: osilator_gorsel(k, "cci", (25, 75, "#fef2f2", "+100 / -100"), "CCI (20) — ortalamadan sapma", RENK["mor"])),
    ("ADX", lambda k: osilator_gorsel(k, "adx", (25, 100, "#ecfdf5", "25 eşiği"), "ADX — trend gücü", RENK["yesil"])),
    ("Wave Trend", lambda k: osilator_gorsel(k, "wt", (20, 80, "#fef2f2", "+53 / -53"), "Wave Trend (WT1/WT2)", RENK["teal"])),
    ("MACD", macd_gorsel),
    ("EMA", iki_ma_gorsel),
    ("Mum", mum_gorsel),
    ("Pearson", lambda k: dagilim_gorsel(k, False)),
    ("Regresyon", kanal_gorsel),
    ("Hacim", hacim_gorsel),
    ("Likidite", hacim_gorsel),
    ("Bileşik", buyume_gorsel),
    ("Çeşitlendirme", lambda k: pasta_gorsel(k, [("Hisse", 40), ("Altın", 15), ("Döviz", 10), ("Mevduat", 15), ("Fon", 10), ("Nakit", 10)], "Çeşitlendirme — varlık dağılımı")),
    ("Enflasyon", lambda k: pasta_gorsel(k, [("Mevduat", 25), ("Altın", 25), ("Hisse", 30), ("Döviz", 20)], "Enflasyona karşı varlıklar (kavramsal)")),
]


def gorsel_uret(konu):
    for anahtar, uretici in GORSEL_ESLEME:
        if anahtar in konu:
            try:
                return uretici(konu)
            except Exception:
                return None
    return None
