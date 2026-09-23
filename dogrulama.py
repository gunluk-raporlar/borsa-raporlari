# -*- coding: utf-8 -*-
"""LLM raporlarinda sirket adi ve makro sayi hatalarini yakalayan deterministik
dogrulama katmani. Yayin oncesi calisir; LLM cagrisi YAPMAZ, agir bagimliligi
yoktur. bot.py tarafindan kullanilir; tek basina da test edilebilir:

    python dogrulama.py

Neden LLM editor degil: sirket adi ve enflasyon degerinin dogrusu sozlukte/
veri dosyasinda hazirdir; ikinci bir model ayni hatayi yapabilir. Kod
kontrolu kesindir, milisaniyeler surer.
"""

import re

# Turkiye alfabesi + sirket adlarinda goren karakterler. Nokta ve cumle
# sonlari bilincli olarak haric: ad, onceki cumleye tasmasin.
_KELIME = r"[0-9A-Za-zçğıöşüÇĞİÖŞÜ&'’\-]"
# Sirket adi en fazla 4 kelime ("Kıymetli Yatırım Menkul Kıymetler" kadar).
_ADI_KALIP = r"((?:" + _KELIME + r"+\s+){0,3}" + _KELIME + r"+)"
# Adin basindaki baglac/zamirleri karsilastirmaya katma ("ve Yapı Kredi" gibi).
_BAGLAC = re.compile(
    r"^(?:(?:ve|veya|ile|ancak|ama|fakat|gibi|için|olan|daha)\s+)+", re.IGNORECASE
)


def _normal(yazi):
    """Karsilastirma oncesi markdown kalintilarini at, kucuk harfe cevir."""
    return re.sub(r"[*_`]+", "", yazi).casefold().strip()


def isim_duzelt(metin, adlar):
    """'Ad (KOD)' eslesmelerini sozlukle dogrular; uymayani resmi adla
    degistirir. Kabul kurali yumusak: dogru adin anlamli (>=3 harf) herhangi
    bir kelimesi yazilan adi iceriyorsa dokunma ("Garanti Bankasi (GARAN)"
    kabul, "Yikbank (YKBNK)" duzeltilir).

    Donus: (yeni_metin, duzeltmeler); duzeltme = (yanlis_ad, dogru_ad, kod).
    """
    if not metin or not adlar:
        return metin, []
    kodlar = sorted(adlar, key=len, reverse=True)
    kalip = re.compile(
        _ADI_KALIP + r"\s*\((" + "|".join(re.escape(k) for k in kodlar) + r")\)"
    )
    duzeltmeler = []

    def _bak(m):
        adi = m.group(1)
        kod = m.group(2)
        dogru = adlar.get(kod)
        if not dogru:
            return m.group(0)
        govde = _BAGLAC.sub("", adi)
        if not govde:
            return m.group(0)
        # Efektif ad: bastaki kucuk harfli cumle sozlerini at, ilk buyuk
        # harfli kelimeden itibaren al ("momentuma Denizbank" -> "Denizbank").
        kelimeler = govde.split()
        bas = 0
        for i, kelime in enumerate(kelimeler):
            if kelime[:1].isupper():
                bas = i
                break
        ad = " ".join(kelimeler[bas:])
        ad_kalip = r"\s+".join(re.escape(k) for k in kelimeler[bas:])
        m2 = re.search(ad_kalip, govde)
        ofset = (len(adi) - len(govde)) + (m2.start() if m2 else 0)
        ad_norm = _normal(ad)
        for kelime in _normal(dogru).split():
            if len(kelime) >= 3 and kelime in ad_norm:
                return m.group(0)  # beklenen ada dair iz var; kabul
        duzeltmeler.append((ad.strip(), dogru, kod))
        # adin basindaki cumle sozleri korunur, yalnizca yanlis ad degisir
        return adi[:ofset] + f"{dogru} ({kod})"

    return kalip.sub(_bak, metin), duzeltmeler


# Enflasyon cumlelerindeki sayilar; "%31,5" ve "31,5%" bicimlerini yakalar.
_SAYI = re.compile(r"%\s*(\d{1,2}(?:[.,]\d{1,2})?)|(?:\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_ENFLASYON_KELIME = re.compile(r"\b(enflasyon|tüfe|tufe)", re.IGNORECASE)  # ek almali kelimeler: "enflasyondaki"...
# "yil sonu enflasyon BEKLENTISI %24" gercek oran degildir; dokunma.
_BEKLENTI = re.compile(r"\b(beklenti|öngörü|ongoru|hedef)", re.IGNORECASE)


def enflasyon_duzelt(metin, oran_yuzde, tolerans=0.25):
    """Enflasyon/TUFE gecen cumlelerde gercek orandan (puan bazinda) sapan
    sayiyi duzeltir. Donus: (yeni_metin, duzeltmeler); duzeltme = (eski, yeni).
    """
    if not metin or oran_yuzde is None:
        return metin, []
    dogru_yazi = ("%{:.1f}".format(oran_yuzde)).replace(".", ",")
    duzeltmeler = []
    cikti = []
    for satir in metin.split("\n"):
        parcalar = re.split(r"(?<=[.!?])\s+", satir)
        yeni_parcalar = []
        for cumle in parcalar:
            if _ENFLASYON_KELIME.search(cumle) and not _BEKLENTI.search(cumle):
                for m in _SAYI.finditer(cumle):
                    yazi = m.group(0)
                    sayi = float(yazi.replace("%", "").replace(",", ".").strip())
                    if abs(sayi - oran_yuzde) > tolerans:
                        dogru = dogru_yazi if yazi.startswith("%") else dogru_yazi[1:] + "%"
                        cumle = cumle[: m.start()] + dogru + cumle[m.end():]
                        duzeltmeler.append((yazi, dogru))
                        break  # cumle basina tek duzeltme yeter
            yeni_parcalar.append(cumle)
        cikti.append(" ".join(yeni_parcalar) if len(parcalar) > 1 else yeni_parcalar[0])
    return "\n".join(cikti), duzeltmeler


# --- Endeks seviyesi denetimi -------------------------------------------------
# Raporlarda gecen "BIST 30 endeksi 4.200-4.250 direnc bandi" gibi ifadeler,
# endeksin gercek seviyesiyle (or. 16.372) karsilastirilir. Sayi, toleransin
# disindaysa DOGRU seviyeyle degistirilir; "destek > direnc" gibi mantik
# hatalari ayrica uyari olarak doner.
_SEVIYE_YAZI = re.compile(
    r"\b(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{5,6}(?:,\d{1,2})?)\b"
)
_ENDEKS_BAGLAM = re.compile(r"(endeks|BIST\s*30|BIST\s*100|XU0?30|XU0?100)", re.I)
_SEVIYE_BAGLAM = re.compile(
    r"(destek|direnç|direnc|seviye|band|bant|kanal|kırıl|kiril|test|geç|gec)", re.I
)
_DESTEK = re.compile(r"destek[a-zçğıöşü]*\s*(?:seviyesi\s*)?[~≈]?\s*([\d.,]+)", re.I)
_DIRENC = re.compile(r"diren[çc][a-zçğıöşü]*\s*(?:seviyesi\s*)?[~≈]?\s*([\d.,]+)", re.I)
# "16.000 destek" ve "destek 16.000" iki yonde de okunur.
_SEVIYE_SOL = re.compile(
    r"([0-9][0-9.,]*)\s*(?:seviyesindeki\s*)?(destek|direnç|direnc)", re.I
)
_SEVIYE_SAG = re.compile(
    r"(destek|diren[çc])[a-zçğıöşü]*\s*(?:seviyesi\s*)?[~≈]?\s*([0-9][0-9.,]*)", re.I
)


def _sayiya(yazi):
    """'16.372,83' -> 16372.83 ; '4.200' -> 4200.0"""
    return float(yazi.replace(".", "").replace(",", "."))


def _binlik(sayi):
    """16372.4 -> '16.372' (kurus gerekmiyorsa tam sayi yazilir)."""
    tam = round(sayi)
    return "{:,}".format(tam).replace(",", ".")


def _sayiya_guvenli(yazi):
    try:
        return _sayiya(yazi)
    except (ValueError, AttributeError):
        return None


def _seviye_etiketi(cumle):
    """Cumledeki (destek, direnc) seviyelerini iki yonde de yakalar.
    Donus: (destek|None, direnc|None)"""
    d = r = None
    for m in _SEVIYE_SOL.finditer(cumle):
        v = _sayiya_guvenli(m.group(1))
        if v is None:
            continue
        if m.group(2).lower().startswith("destek"):
            d = v if d is None else d
        else:
            r = v if r is None else r
    for m in _SEVIYE_SAG.finditer(cumle):
        v = _sayiya_guvenli(m.group(2))
        if v is None:
            continue
        if m.group(1).lower().startswith("destek"):
            d = v if d is None else d
        else:
            r = v if r is None else r
    return d, r


def endeks_seviye_duzelt(metin, endeks_seviyesi, tolerans=0.15):
    """Endeks cumlelerindeki seviyeleri gercek degerle karsilastirir.

    Donus: (yeni_metin, duzeltmeler, uyarilar); duzeltme = (eski, yeni),
    uyari = metin. Yalnizca hem endeks hem seviye baglami gecen CUMLELERDEKI
    sayilar ele alinir; tolerans disindaki sayilar dogru seviyeyle degistirilir.
    """
    if not metin or not endeks_seviyesi:
        return metin, [], []
    dogru = _binlik(endeks_seviyesi)
    duzeltmeler, uyarilar = [], []
    cikti = []
    for satir in metin.split("\n"):
        parcalar = re.split(r"(?<=[.!?])\s+", satir)
        yeni_parcalar = []
        for cumle in parcalar:
            if _ENDEKS_BAGLAM.search(cumle) and _SEVIYE_BAGLAM.search(cumle):
                def _bak(m):
                    try:
                        deger = _sayiya(m.group(1))
                    except ValueError:
                        return m.group(0)
                    if deger <= 0 or abs(deger / endeks_seviyesi - 1) <= tolerans:
                        return m.group(0)
                    duzeltmeler.append((m.group(1), dogru))
                    return dogru
                cumle = _SEVIYE_YAZI.sub(_bak, cumle)
                # "16.372-16.372" gibi tekrarlari tek sayiya indir
                cumle = re.sub(r"(\d{1,3}(?:\.\d{3})+)\s*[-–]\s*\1", r"\1", cumle)
                d, r = _seviye_etiketi(cumle)
                if d is not None and r is not None and d > r:
                    uyarilar.append("destek > direnc: " + cumle.strip()[:120])
            yeni_parcalar.append(cumle)
        cikti.append(" ".join(yeni_parcalar) if len(parcalar) > 1 else yeni_parcalar[0])
    return "\n".join(cikti), duzeltmeler, uyarilar


def metin_dogrula(metin, adlar, enflasyon_yuzde=None, endeks_seviyesi=None):
    """Tum dogrulama zinciri. Donus sozlugu:
    {"metin": ..., "isim_duzeltme": [...], "enflasyon_duzeltme": [...],
     "endeks_duzeltme": [...], "endeks_uyari": [...]}.
    Hicbir durumda istisna yukseltmez; cagiran taraf zaten sarmaladi.
    """
    metin, isim = isim_duzelt(metin, adlar)
    metin, enf = enflasyon_duzelt(metin, enflasyon_yuzde)
    metin, endeks, uyari = endeks_seviye_duzelt(metin, endeks_seviyesi)
    return {"metin": metin, "isim_duzeltme": isim, "enflasyon_duzeltme": enf,
            "endeks_duzeltme": endeks, "endeks_uyari": uyari}


if __name__ == "__main__":
    # Bugunun canli raporunda yakalanan gercek hatalarla kendini test eder.
    ADLAR = {"YKBNK": "Yapı Kredi", "ISCTR": "İş Bankası", "TOASO": "Tofaş",
             "GARAN": "Garanti BBVA", "TUPRS": "Tüpraş"}
    ornek = ("Garanti Bankası (GARAN), Yıkbank (YKBNK), Akbank (AKBNK) ve "
             "Kıymetli Yatırım Menkul Kıymetler (ISCTR) gibi sektörün önemli "
             "oyuncuları GÜÇLÜ SAT vermektedir.")
    sonuc = metin_dogrula(ornek, ADLAR)
    print(sonuc["metin"])
    assert "Yapı Kredi (YKBNK)" in sonuc["metin"], "Yikbank duzelmedi!"
    assert "İş Bankası (ISCTR)" in sonuc["metin"], "ISCTR duzelmedi!"
    assert "Akbank (AKBNK)" in sonuc["metin"], "dogru ad bozuldu!"
    assert "Garanti Bankası (GARAN)" in sonuc["metin"], "kabul edilebilir takma ad bozuldu!"

    ornek2 = "Enflasyon %28,4 seviyesinde yatay seyrediyor."
    sonuc2 = metin_dogrula(ornek2, ADLAR, enflasyon_yuzde=31.51)
    print(sonuc2["metin"])
    assert "%31,5" in sonuc2["metin"], "enflasyon duzelmedi!"

    ornek3 = "Yıl sonu enflasyon beklentisi %24 olarak korunmalı."  # beklenti: dokunma
    sonuc3 = metin_dogrula(ornek3, ADLAR, enflasyon_yuzde=31.51)
    assert "%24" in sonuc3["metin"], "beklenti cumlesine dokunulmamaliydi!"

    ornek4 = "Tosya (TOASO) otomotivde zayıf."  # 1 kelimelik yanlis ad
    sonuc4 = metin_dogrula(ornek4, ADLAR)
    assert "Tofaş (TOASO)" in sonuc4["metin"], "tek kelimelik ad duzelmedi!"

    # --- endeks seviyesi denetimi ---
    ornek5 = ("BIST 30 endeksi, direnç bandı olan 4.200-4.250 seviyesi arasında "
              "konsolide oluyor. Endeksin 4.250 direncini kırması yükseliş sinyali olur.")
    sonuc5 = metin_dogrula(ornek5, ADLAR, endeks_seviyesi=16372.4)
    print(sonuc5["metin"])
    assert "4.200" not in sonuc5["metin"] and "4.250" not in sonuc5["metin"], "hayali seviye kaldi!"
    assert "16.372" in sonuc5["metin"], "dogru seviye yazilmadi!"
    assert len(sonuc5["endeks_duzeltme"]) >= 2, "duzeltmeler kaydedilmedi!"

    ornek6 = "BIST 30 endeksi 16.380 seviyesinde ve 16.400 direnci test ediliyor."  # dogru: dokunma
    sonuc6 = metin_dogrula(ornek6, ADLAR, endeks_seviyesi=16372.4)
    assert sonuc6["endeks_duzeltme"] == [], "tolerans icindeki seviye degistirildi!"

    ornek7 = "Endeks 16.000 destek, 15.000 direnç seviyesi arasında."  # destek > direnc
    sonuc7 = metin_dogrula(ornek7, ADLAR, endeks_seviyesi=16372.4)
    assert sonuc7["endeks_uyari"], "destek>direnc uyarisi uretilmedi!"

    print("dogrulama.py: tum kendini testler gecti.")
