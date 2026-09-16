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


def metin_dogrula(metin, adlar, enflasyon_yuzde=None):
    """Tum dogrulama zinciri. Donus sozlugu:
    {"metin": ..., "isim_duzeltme": [...], "enflasyon_duzeltme": [...]}.
    Hicbir durumda istisna yukseltmez; cagiran taraf zaten sarmaladi.
    """
    metin, isim = isim_duzelt(metin, adlar)
    metin, enf = enflasyon_duzelt(metin, enflasyon_yuzde)
    return {"metin": metin, "isim_duzeltme": isim, "enflasyon_duzeltme": enf}


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

    print("dogrulama.py: tum kendini testler gecti.")
