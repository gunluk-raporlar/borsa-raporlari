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


# Enflasyon cumlelerindeki yuzdeler. Yuzde iki konumda da olabilir; en az bir
# tanesi yuzde isareti icermelidir. Uc haneli degerleri de kapsar.
_SAYI = re.compile(
    r"(?:%\s*\d{1,3}(?:[.,]\d{1,3})?|\b\d{1,3}(?:[.,]\d{1,3})?\s*%)",
    re.IGNORECASE,
)
_ENFLASYON_KELIME = re.compile(r"\b(enflasyon|tüfe|tufe)", re.IGNORECASE)
_ENFLASYON_KELIME_DOKU = re.compile(
    r"\b(enflasyon|tüfe|tufe|üfe|ufe)", re.IGNORECASE
)
# Ayni cumlede baska gostergeye ait yuzde varsa onu enflasyon sanmamak icin.
_DIGER_GOSTERGE = re.compile(
    r"\b(politika faiz[a-zçğıöşü]*|faiz[a-zçğıöşü]*|büyüme[a-zçğıöşü]*|"
    r"işsizlik[a-zçğıöşü]*|cari denge|döviz rezerv[a-zçğıöşü]*)\b",
    re.IGNORECASE,
)
# "yil sonu enflasyon BEKLENTISI %24" gercek oran degildir. Cumle genelinde
# beklenti kelimesi varsa tum cumleyi atmak yerine, ilgili yuzdeyi atla.
_BEKLENTI = re.compile(
    r"\b(?:beklenti|öngörü|onguru|hedef)[a-zçğıöşü]*\b", re.IGNORECASE
)
# Veri tabanindaki ulke oranlarini cumledeki ulke ipucuna gore sec.
_ULKE_FED = re.compile(
    r"\b(?:ABD|Amerika(?:n)?|US|U\.S\.)\b", re.IGNORECASE
)
_ULKE_EU = re.compile(
    r"\b(?:Euro(?:\s?Bölgesi)|Avrupa|ECB|Eurozone|Euro area)\b",
    re.IGNORECASE,
)
# NOT 1: Bu desene "Bölgede" gibi tek-kelime alternatifleri KONULMAZ; eski
# surumdeki "|Bölgede" alternatifi "reel faizlerin pozitif bölgede olması"
# gibi turkiye cumlelerini Euro baglamina sokup TR enflasyon/faizini EU
# degeriyle denetleyerek hatali raporun (2 Ekim 2026: "%3,2 enflasyon,
# %2,65 politika faizi") yayinlanmasina yol acmisti.
# NOT 2: "Euro" TEK BASINA da baglam sayilmaz: "euro bazinda/cinsinden getiri"
# ifadeleri yaygindir ve TR cumlelerini EU degeriyle denetletip dogru TR
# degerini EU oraniyla degistirtiyor (dogrulanmis saldiri vektoru, 2026-10-05).
# Merkez bankasi adlari, karsilastirma yapan cumlede de sahibi gosterir.
# Turkiye baglami: en yakin ulke kelimesi mantiginda ABD/Avrupa ile yarisir
# (2026-10-07). "TCMB" hem kurum hem ulke ipucu olarak tr verir.
_ULKE_TR = re.compile(
    r"\b(?:Türkiye|Turkiye|Türkiye Cumhuriyet Merkez Bankası|TCMB)\b",
    re.IGNORECASE,
)
_MERKEZ_FED = re.compile(r"\b(?:Federal Reserve|Fed)\b", re.IGNORECASE)
_MERKEZ_EU = re.compile(
    r"\b(?:Avrupa Merkez Bankası|ECB|European Central Bank|EZB)\b", re.IGNORECASE
)
# Veri tabaninda olmayan enflasyon kırılımlarını genel TUIK oranina zorlama.
# Bir cumlede bu kırılımlardan biri varsa tum cumleyi temkinli olarak atla.
_ALT_ENFLASYON = re.compile(
    r"\b(çekirdek|enerji|gıda|hizmet|üretici|tüketici)\b", re.IGNORECASE
)


def _kelime_mesafesi(cumle, pos, desen):
    """Sayı ile gösterge kelimesi arasındaki yönlü karakter mesafesi.

    Sayının hem solunda hem sağında gösterge kelimesi varsa mutlak başlangıç
    mesafesi yanlış eşleşme yapabilir. Bu yüzden ilgili kenara olan mesafe
    ölçülür: `%9,9 ve işsizlik %8,8` içinde işsizlik için `%8,8` seçilir.
    """
    en_yakin = None
    for k in desen.finditer(cumle):
        if k.end() <= pos:
            d = pos - k.end()
        elif k.start() >= pos:
            d = k.start() - pos
        else:
            d = 0
        if en_yakin is None or d < en_yakin:
            en_yakin = d
    return en_yakin


def _yuzde_degeri(yazi):
    """'31,51' -> 31.51 ; Turkce binlik ayracli '4.286' -> 4286.0.

    Turkce raporlarda NOKTA her zaman binlik ayracligidir, ondalik VIRGULDUR.
    Nokta-tam-3-hane kalibi ("16.372", "1.234.567") binlik sayilir; kalan
    durumda virgul ondaliga cevrilir ("3.4" -> 3.4 sayilmaz, "3,4" -> 3.4).
    """
    metin = yazi.replace("%", "").strip()
    try:
        if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", metin):
            return float(metin.replace(".", "").replace(",", "."))
        return float(metin.replace(",", "."))
    except ValueError:
        return None


def _enflasyon_yuzde_temizle(yazi, dogru):
    """Mevcut yuzde bicimini koruyarak dogru degeri yazar."""
    return "%" + dogru if yazi.lstrip().startswith("%") else dogru + "%"


def _ulke_anahtari(cumle, sayi_pos):
    """Yuzdenin ulke ipucunu tr/us/eu olarak dondurur; belirsizse None.

    Once sayidan once gelen acik merkez bankasi adi, sonra sayidan once gelen
    en yakin ulke adi kullanilir. Boylece "ECB ... ABD'deki gibi" karsilastirmasi
    Euro kipinda, "ABD enflasyonu %3,4, Euro enflasyonu %3,2" ise iki ayri
    ulke olarak cozulur.
    """
    merkezler = []
    for desen, anahtar in ((_MERKEZ_EU, "eu"), (_MERKEZ_FED, "us")):
        for m in desen.finditer(cumle, 0, sayi_pos):
            merkezler.append((sayi_pos - m.start(), anahtar))
    if merkezler:
        return min(merkezler)[1]

    onceki = []
    for desen, anahtar in ((_ULKE_FED, "us"), (_ULKE_EU, "eu"),
                           (_ULKE_TR, "tr")):
        for m in desen.finditer(cumle, 0, sayi_pos):
            onceki.append((m.start(), anahtar))
    if onceki:
        # 2026-10-07: TR deseni eklendi — "ABD enflasyonu %3,4, Türkiye
        # enflasyonu %28,4" cumlesinde Türkiye sayısı ABD oranına
        # değiştiriliyordu (en yakın önceki ülke kelimesi kazanır).
        return max(onceki)[1]

    sonraki = set()
    for desen, anahtar in ((_ULKE_FED, "us"), (_ULKE_EU, "eu"),
                           (_ULKE_TR, "tr")):
        if desen.search(cumle, sayi_pos):
            sonraki.add(anahtar)
    if len(sonraki) == 1:
        return next(iter(sonraki))
    if len(sonraki) > 1:
        return None
    return "tr"


def enflasyon_duzelt(metin, oran_yuzde, tolerans=0.005, oranlar=None,
                     oranlar_aylik=None, izinli=None):
    """Enflasyon/TUFE yuzdelerini ulke ve gosterge baglamindan duzeltir.

    `oranlar` = {"tr": .., "us": .., "eu": ..}. Verilmedigi ulke icin
    o cumledeki orana dokunulmaz. Ayni cumlede birden fazla ulke orani
    varsa her ulkenin enflasyona en yakin yuzdesi ayri ayri denetlenir.
    `oranlar_aylik` snapshot'taki ENFLASYON (aylik) kirilimidir; cumledeki
    en yakin frekans ipucu "aylik" ise yillik orana degil bu deger gore
    denetlenir (yoksa o yuzdeye dokunulmaz).
    """
    oranlar = dict(oranlar or {})
    if oran_yuzde is not None and "tr" not in oranlar:
        oranlar["tr"] = oran_yuzde
    if not metin or not oranlar:
        return metin, []

    duzeltmeler = []
    cikti = []
    for satir in metin.split("\n"):
        parcalar = re.split(r"(?<=[.!?])\s+", satir)
        yeni_parcalar = []
        for cumle in parcalar:
            if _ALT_ENFLASYON.search(cumle):
                yeni_parcalar.append(cumle)
                continue
            en_iyi = {}
            for m in _SAYI.finditer(cumle):
                deger = _yuzde_degeri(m.group(0))
                if deger is None:
                    continue
                d_enf = _kelime_mesafesi(cumle, m.start(), _ENFLASYON_KELIME)
                if d_enf is None:
                    continue
                # Beklenti/hedef veya baska gostergeye daha yakin yuzdeyi alma.
                d_bek = _kelime_mesafesi(cumle, m.start(), _BEKLENTI)
                d_diger = _kelime_mesafesi(cumle, m.start(), _DIGER_GOSTERGE)
                if ((d_bek is not None and d_bek < d_enf)
                        or (d_diger is not None and d_diger < d_enf)):
                    continue
                anahtar = _ulke_anahtari(cumle, m.start())
                if anahtar is None:
                    continue
                hedef = oranlar.get(anahtar)
                # "Aylik enflasyon %1,84" yillik orana gore degil snapshot'taki
                # aylik kirilima gore denetlenir; aylik deger yoksa dokunulmaz.
                if _siklik_bul(cumle, m.start()) == "aylık":
                    hedef = (oranlar_aylik or {}).get(anahtar)
                if hedef is None or abs(deger - hedef) <= tolerans:
                    continue
                # GERCEK DEGER KORUMASI: sayi snapshot'in baska bir satirinda
                # gercekse (or. ABD enflasyonu %3,4) dokunulmaz (2026-10-07).
                if _gercek_mi(deger, izinli):
                    continue
                # Ayni ulke icin yalnizca enflasyona en yakin yuzdeyi duzelt.
                if anahtar not in en_iyi or d_enf < en_iyi[anahtar][0]:
                    en_iyi[anahtar] = (d_enf, m, hedef)

            for _, m, hedef in sorted(en_iyi.values(), key=lambda x: x[1].start(),
                                      reverse=True):
                yazi = m.group(0)
                dogru_yazi = ("%.3f" % hedef).rstrip("0").rstrip(".").replace(".", ",")
                dogru = _enflasyon_yuzde_temizle(yazi, dogru_yazi)
                cumle = cumle[:m.start()] + dogru + cumle[m.end():]
                duzeltmeler.append((yazi, dogru))
            yeni_parcalar.append(cumle)
        cikti.append(" ".join(yeni_parcalar) if len(parcalar) > 1 else yeni_parcalar[0])
    return "\n".join(cikti), duzeltmeler


# --- Politika faizi denetimi ---------------------------------------------------
# "TCMB politika faizi %43" gibi cumlelerdeki yanlis oran akşam makro
# snapshot'indaki GERCEK oranla karsilastirilir; tolerans disindaysa
# duzeltilir. Coklu-yuzde cumlelerde ("enflasyon %31,5 iken faiz %45") hangi
# sayinin hangi konuya ait oldugunu KELIME MESAFESI belirler; boylece iki
# denetim birbirinin cumlesine karismaz. Beklenti/hedef yuzdesi aday
# elemesinde atlanir; ayni cumledeki gercek faiz yine duzeltilebilir.
_FAIZ_KELIME = re.compile(
    r"\b(politika faiz[a-zçğıöşü]*|faiz[a-zçğıöşü]*|TCMB|Fed|"
    r"merkez bankas[a-zçğıöşü]*)\b",
    re.IGNORECASE)
# "Merkez bankasi %2 hedefi" ifadesinde hedefi faiz sanmamak icin
# yalnizca fiili faiz sahibi ifadeleri kullanilir.
_FAIZ_SAHIP_KELIME = re.compile(
    r"\b(politika faiz[a-zçğıöşü]*|faiz[a-zçğıöşü]*|TCMB|Fed)\b",
    re.IGNORECASE)
# Politika faizi OLMAYAN faiz turleri: bu adlar yakin oldukca o yuzde
# politika faiziyle kiyaslanmaz (mevduat faizinin gercek degerini bozmamak icin).
_FAIZ_TURU = re.compile(
    r"\b(mevduat|kredi|reel|takas|tabela|gecelik|repo|ihraç)\s+(?:faiz|oran)", re.I)
# Genel Avrupa ipucu, "Avrupa faizi" gibi yalnizca bolgesel ifadeler icin.
_ULKE_BAGLAM = re.compile(r"\b(avrupa|euro bölgesi|ECB)\b", re.IGNORECASE)


def faiz_duzelt(metin, oranlar, tolerans=0.005, izinli=None):
    """Politika faizi cumlelerinde gercek orandan sapan sayiyi duzeltir.

    oranlar: {"tr": 37.0, "us": 4.0, "eu": 2.65} biciminde ulke bazli oranlar
    (None/eksik ulke o denetimi devredisi birakir). Donus: (yeni_metin, duzeltmeler).
    """
    if not metin or not oranlar:
        return metin, []
    duzeltmeler = []
    cikti = []
    for satir in metin.split("\n"):
        parcalar = re.split(r"(?<=[.!?])\s+", satir)
        yeni_parcalar = []
        for cumle in parcalar:
            if _FAIZ_KELIME.search(cumle) and _SAYI.search(cumle):
                # Kurum konumlari: coklu kurumlu cumlelerde her sayi KENDI en
                # yakin kurumuyla denetlenir (2026-10-07: "Fed faizi %5, ECB
                # politika faizi %4" cumlesinde ECB sayisi Fed havuzuyla
                # eslesip duzeltilmeden kaliyordu).
                kurumlar = []
                for m2 in _MERKEZ_FED.finditer(cumle):
                    kurumlar.append((m2.start(), "us"))
                for m2 in _MERKEZ_EU.finditer(cumle):
                    kurumlar.append((m2.start(), "eu"))
                for m2 in _MERKEZ_TCMB.finditer(cumle):
                    kurumlar.append((m2.start(), "tr"))
                coklu = len({k for _, k in kurumlar}) > 1

                if _MERKEZ_FED.search(cumle):
                    oran = oranlar.get("us")
                elif _MERKEZ_EU.search(cumle):
                    oran = oranlar.get("eu")
                elif _MERKEZ_TCMB.search(cumle):
                    oran = oranlar.get("tr")
                elif _ULKE_FED.search(cumle):
                    oran = oranlar.get("us")
                elif _ULKE_EU.search(cumle):
                    oran = oranlar.get("eu")
                elif _ULKE_BAGLAM.search(cumle) and not re.search(
                        r"TCMB|türkiye|merkez bankası", cumle, re.IGNORECASE):
                    oran = oranlar.get("eu")  # yalnizca avrupa/ipucu varsa
                else:
                    oran = oranlar.get("tr")
                if oran is None:
                    yeni_parcalar.append(cumle)
                    continue

                def _faiz_duzelt_bir(m, oran_deger):
                    yazi = m.group(0)
                    try:
                        sayi = float(yazi.replace("%", "").replace(",", ".").strip())
                    except ValueError:
                        return None
                    if sayi is None or abs(sayi - oran_deger) <= tolerans:
                        return None
                    if _gercek_mi(sayi, izinli):
                        return None
                    dy = ("%.2f" % oran_deger).rstrip("0").rstrip(".").replace(".", ",")
                    dogru = "%" + dy if yazi.startswith("%") else dy + "%"
                    return (m, dogru, yazi)

                if coklu:
                    adaylar = []
                    for m in _SAYI.finditer(cumle):
                        ef = _kelime_mesafesi(cumle, m.start(), _ENFLASYON_KELIME_DOKU)
                        ff = _kelime_mesafesi(cumle, m.start(), _FAIZ_SAHIP_KELIME)
                        fb = _kelime_mesafesi(cumle, m.start(), _BEKLENTI)
                        if ff is None:
                            ff = _kelime_mesafesi(cumle, m.start(), _FAIZ_KELIME)
                            if fb is not None and (ff is None or fb <= ff + 5):
                                continue
                        if ef is not None and (ff is None or ef < ff):
                            continue  # bu yuzde enflasyona ait
                        if ff is None:
                            continue  # faiz kelimesiyle arada iliski yok
                        if fb is not None and fb <= ff:
                            continue  # bu yuzde faiz beklentisi/hedefi
                        sol = cumle[max(0, m.start() - 30):m.start()]
                        if _FAIZ_TURU.search(sol):
                            continue
                        oncekiler = [(p, k) for p, k in kurumlar if p < m.start()]
                        if oncekiler:
                            o2 = oranlar.get(max(oncekiler)[1])
                            if o2 is not None:
                                oran_deger = o2
                        else:
                            oran_deger = oran
                        r = _faiz_duzelt_bir(m, oran_deger)
                        if r:
                            adaylar.append(r)
                    # Sagdan sola uygula (konum kaymasini onler).
                    for m, dogru, yazi in sorted(
                            adaylar, key=lambda x: x[0].start(), reverse=True):
                        cumle = cumle[: m.start()] + dogru + cumle[m.end():]
                        duzeltmeler.append((yazi, dogru))
                else:
                    dogru_yazi = ("%.2f" % oran).rstrip("0").rstrip(".").replace(".", ",")
                    # Enflasyona daha yakin yuzdeler enflasyon denetimine aittir;
                    # faize en yakin adayi sec.
                    aday = None
                    for m in _SAYI.finditer(cumle):
                        ef = _kelime_mesafesi(cumle, m.start(), _ENFLASYON_KELIME_DOKU)
                        ff = _kelime_mesafesi(cumle, m.start(), _FAIZ_SAHIP_KELIME)
                        fb = _kelime_mesafesi(cumle, m.start(), _BEKLENTI)
                        if ff is None:
                            ff = _kelime_mesafesi(cumle, m.start(), _FAIZ_KELIME)
                            # "Merkez bankasi %2 hedefi" cumlesinde genel kurum
                            # adini faiz sahibi sayma.
                            if fb is not None and (ff is None or fb <= ff + 5):
                                continue
                        if ef is not None and (ff is None or ef < ff):
                            continue  # bu yuzde enflasyona ait
                        if ff is None:
                            continue  # faiz kelimesiyle arada iliski yok
                        if fb is not None and fb <= ff:
                            continue  # bu yuzde faiz beklentisi/hedefi
                        # Bileşik faiz türü (mevduat/kredi/reel faizi...) politika
                        # faizi değildir: sayının solundaki yakın bölümde tür adı
                        # varsa bu yüzde politika faiziyle denetlenmez — aksi halde
                        # DOĞRU mevduat faizi (%43,8) politika faiziyle (%37)
                        # değiştiriliyordu (dogrulanmis saldiri vektoru, 2026-10-05).
                        sol = cumle[max(0, m.start() - 30):m.start()]
                        if _FAIZ_TURU.search(sol):
                            continue
                        aday = m
                        break
                    if aday is not None:
                        r = _faiz_duzelt_bir(aday, oran)
                        if r:
                            m, dogru, yazi = r
                            cumle = cumle[: m.start()] + dogru + cumle[m.end():]
                            duzeltmeler.append((yazi, dogru))
            yeni_parcalar.append(cumle)
        cikti.append(" ".join(yeni_parcalar) if len(parcalar) > 1 else yeni_parcalar[0])
    return "\n".join(cikti), duzeltmeler


# Frekans ipuclari: "aylik enflasyon %1,84" sayisi yillik orana gore
# DENETLENMEMELI; aday seciminde en yakin ipucu sayinin hangi kirilima
# (aylik/yillik/ceyreklik) ait oldugunu belirler.
_SIKLIK_AYLIK = re.compile(r"\b(?:aylık|aylik|monthly)\b", re.IGNORECASE)
_SIKLIK_YILLIK = re.compile(r"\b(?:yıllık|yillik|annual)\b", re.IGNORECASE)
_SIKLIK_CEYREKLIK = re.compile(r"\b(?:çeyreklik|ceyreklik|quarterly)\b", re.IGNORECASE)
_SIKLIK_DESENLERI = (("aylık", _SIKLIK_AYLIK), ("yıllık", _SIKLIK_YILLIK),
                     ("çeyreklik", _SIKLIK_CEYREKLIK))


def _siklik_bul(cumle, pos):
    """Sayiya en yakin frekans ipucunu ('aylik'/'yillik'/'ceyreklik') dondurur."""
    adaylar = []
    for etiket, desen in _SIKLIK_DESENLERI:
        d = _kelime_mesafesi(cumle, pos, desen)
        if d is not None:
            adaylar.append((d, etiket))
    if not adaylar:
        return None
    return min(adaylar)[1]


def _gosterge_sikligi(kod):
    """Gosterge kodundan beklenen frekans (_yoy->yillik, _mom->aylik...)."""
    if kod.endswith("_yoy"):
        return "yıllık"
    if kod.endswith("_mom"):
        return "aylık"
    if kod.endswith("_qoq"):
        return "çeyreklik"
    return None


# Snapshot'taki kalan makro gostergeleri icin genel deterministik denetim.
# "U-6"/"u-6"/"U6" yazimlari genel issizlik denetiminden ayrilir (2026-10-07:
# kucuk harfli "u-6" cumlesi genel oranla degistiriliyordu).
_U6_ETIKET = re.compile(r"\bU-?6\b", re.IGNORECASE)

_GOSTERGE_KELIMELER = {
    "producer_prices_yoy": re.compile(r"\b(?:ÜFE|üretici fiyat)\w*", re.IGNORECASE),
    "core_inflation_yoy": re.compile(r"\bçekirdek\s+enflasyon\w*", re.IGNORECASE),
    "pce_inflation_yoy": re.compile(r"\byıllık\s+(?:PCE enflasyon|PCE fiyat endeksi)\w*", re.IGNORECASE),
    "pce_inflation_mom": re.compile(r"\baylık\s+(?:PCE enflasyon|PCE fiyat endeksi)\w*", re.IGNORECASE),
    "core_pce_yoy": re.compile(r"\bçekirdek\s+yıllık\s+PCE\w*", re.IGNORECASE),
    "core_pce_mom": re.compile(r"\bçekirdek\s+aylık\s+PCE\w*", re.IGNORECASE),
    "inflation_expectation_1y": re.compile(r"\b1 yıllık\s+enflasyon beklentisi\w*", re.IGNORECASE),
    "inflation_expectation_5y": re.compile(r"\b5 yıllık\s+enflasyon beklentisi\w*", re.IGNORECASE),
    "growth_yoy": re.compile(r"\b(?:yıllık büyüme|GSYH büyümesi|büyüme)\w*", re.IGNORECASE),
    "growth_qoq": re.compile(r"\bçeyreklik\s+büyüme\w*", re.IGNORECASE),
    "unemployment_rate": re.compile(r"\bişsizlik(?:\s+oranı)?\w*", re.IGNORECASE),
    "u6_unemployment": re.compile(r"\bU-6\s+işsizlik\w*", re.IGNORECASE),
    "participation_rate": re.compile(r"\b(?:ekonomik katılım|katılım oranı)\w*", re.IGNORECASE),
    "nonfarm_payroll": re.compile(r"\b(?:NFP|işsizlik dışı istihdam)\w*", re.IGNORECASE),
    "wage_growth_yoy": re.compile(r"\b(?:yıllık ücret artışı|ücret artışı)\w*", re.IGNORECASE),
    "wage_growth_mom": re.compile(r"\b(?:aylık ücret artışı|ücret artışı)\w*", re.IGNORECASE),
    "industrial_production_yoy": re.compile(r"\b(?:yıllık sanayi üretimi|sanayi üretimi)\w*", re.IGNORECASE),
    "industrial_production_mom": re.compile(r"\b(?:aylık sanayi üretimi|sanayi üretimi)\w*", re.IGNORECASE),
    "retail_sales_yoy": re.compile(r"\b(?:yıllık perakende satış|perakende satış)\w*", re.IGNORECASE),
    "retail_sales_mom": re.compile(r"\b(?:aylık perakende satış|perakende satış)\w*", re.IGNORECASE),
    "pmi_manufacturing": re.compile(r"\b(?:imalat PMI|üretim PMI)\w*", re.IGNORECASE),
    "pmi_services": re.compile(r"\b(?:hizmetler PMI|hizmet PMI)\w*", re.IGNORECASE),
    "pmi_composite": re.compile(r"\b(?:bileşik PMI|composite PMI)\w*", re.IGNORECASE),
    "consumer_confidence": re.compile(r"\btüketici güven\w*", re.IGNORECASE),
    "business_confidence": re.compile(r"\biş güven\w*", re.IGNORECASE),
    "economic_sentiment": re.compile(r"\b(?:ekonomik beklenti endeksi|ekonomik güven)\w*", re.IGNORECASE),
    "current_account": re.compile(r"\bcari\s+(?:denge|işlem dengesi)\w*", re.IGNORECASE),
    "trade_balance": re.compile(r"\bticaret dengesi\w*", re.IGNORECASE),
    "exports": re.compile(r"\b(?:mal ihracatı|ihracat)\w*", re.IGNORECASE),
    "imports": re.compile(r"\b(?:mal ithalatı|ithalat)\w*", re.IGNORECASE),
    "budget_balance": re.compile(r"\bbütçe dengesi\w*", re.IGNORECASE),
    "budget_gdp": re.compile(r"\bbütçe\s*/\s*GSYH\w*", re.IGNORECASE),
    "fx_reserves": re.compile(r"\b(?:döviz\s+)?rezerv\w*", re.IGNORECASE),
    "m3_yoy": re.compile(r"\bM3\s+para arzı\w*", re.IGNORECASE),
    "consumer_credit": re.compile(r"\btüketici kredisi(?! faizi)\w*", re.IGNORECASE),
    "initial_jobless_claims": re.compile(r"\bilk işsizlik başvurusu\w*", re.IGNORECASE),
    "continuing_jobless_claims": re.compile(r"\bdevam eden işsizlik başvurusu\w*", re.IGNORECASE),
    "job_openings": re.compile(r"\b(?:JOLTs|açık iş sayısı)\w*", re.IGNORECASE),
    "new_home_sales": re.compile(r"\byeni konut satış\w*", re.IGNORECASE),
    "durable_goods": re.compile(r"\bdayanıklı mal sipariş\w*", re.IGNORECASE),
    # Yeni makro bloklari (TV/EVDS): her biri kendi kirilim etiketiyle.
    "producer_prices_mom": re.compile(
        r"\baylık\s+(?:ÜFE|üretici fiyat)\w*|\bÜFE\s*\(aylık\)\w*", re.IGNORECASE),
    "tourism_revenues": re.compile(r"\bturizm\s+gelir\w*", re.IGNORECASE),
    "tourist_arrivals_yoy": re.compile(
        r"\bturist(?:lerin)?\s+(?:sayısı|girişi|varışı)\w*", re.IGNORECASE),
    "capacity_utilization": re.compile(r"\bkapasite\s+kullanım\w*", re.IGNORECASE),
    "debt_gdp": re.compile(r"\bborç\s*/\s*GSYH\w*", re.IGNORECASE),
    "loans_companies_yoy": re.compile(r"\bşirketlere\s+krediler?\w*", re.IGNORECASE),
    "loans_households_yoy": re.compile(r"\bhanehalkına\s+krediler?\w*", re.IGNORECASE),
    "consumer_spending_qoq": re.compile(r"\btüketici\s+harcamalar\w*", re.IGNORECASE),
    "rate_projection_1y": re.compile(
        r"\bfaiz\s+projeksiyonu\w*\s*\(?\s*1\b|\b(?:1\.\s*yıl|ilk\s+yıl)",
        re.IGNORECASE),
    "rate_projection_2y": re.compile(
        r"\bfaiz\s+projeksiyonu\w*\s*\(?\s*2\b|\b(?:2\.\s*yıl|ikinci\s+yıl)",
        re.IGNORECASE),
    "deposit_rate": re.compile(r"\bmevduat\s+faizi\w*", re.IGNORECASE),
    "consumer_loan_rate": re.compile(r"\btüketici\s+kredisi\s+faizi\w*", re.IGNORECASE),
    "credit_growth_yoy": re.compile(r"\bkredi\s+büyümesi\w*|\bbanka\s+kredileri\w*",
                                    re.IGNORECASE),
    "reer": re.compile(r"\breel\s+efektif\s+döviz\s+kuru\w*", re.IGNORECASE),
}
_SAYI_YUZDELI = re.compile(
    r"(?<![\w.])(?:%\s*-?\d{1,3}(?:[.,]\d{1,3})?"
    r"|-?\d{1,3}(?:[.,]\d{1,3})?\s*%)(?!\w)"
)
_SAYI_ONLIKLI = re.compile(
    r"(?<![\w.])(?:%\s*-?\d+[.,]\d+|-?\d+[.,]\d+\s*%?)(?!\w)"
)


def _sayi_bicimle(deger, yazi, birim):
    """Kaynak ondalik duzenini koruyarak snapshot degerini yazar.

    Kaynak binlik ayracli Turkce duzende ("4.286", "6.731,74") cikti da
    binlik nokta + ondalik virgul ile yazilir; boylece "4.200" gibi bir
    seviye "4200" formatina dusmez.
    """
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", yazi.strip()):
        tam, _, kir = ("%.2f" % deger).partition(".")
        sayi = "{:,}".format(int(tam)).replace(",", ".")
        kir = kir.rstrip("0")
        if kir:
            sayi += "," + kir
    elif deger == int(deger):
        sayi = str(int(deger))
    else:
        sayi = ("%.3f" % deger).rstrip("0").rstrip(".")
        if "." in yazi and "," not in yazi:
            sayi = sayi.replace(".", ".")
        else:
            sayi = sayi.replace(".", ",")
    yuzde = birim == "%"
    if yazi.lstrip().startswith("%"):
        return ("%" + sayi) if yuzde else sayi
    if yazi.endswith("%"):
        return (sayi + "%") if yuzde else sayi
    return ("%" + sayi) if yuzde else sayi


def _gosterge_hedef(gostergeler, ulke, gosterge):
    """Gosterge degerini okur; bilinen anahtar alias'larini destekler
    (2026-10-07, ChatGPT incelemesi): tourism_revenues/tourism_revenue
    tekil-cogul karisikligi ayni sayiyi hic duzeltmeden geciriyordu."""
    tablo = gostergeler.get(ulke) or {}
    hedef = tablo.get(gosterge)
    if hedef is None and gosterge == "tourism_revenues":
        hedef = tablo.get("tourism_revenue")
    return hedef


def makro_gosterge_duzelt(metin, gostergeler, tolerans=0.005, izinli=None):
    """Snapshot'taki diger gercek gostergeleri ulke/indikator baglaminda duzeltir."""
    if not metin or not gostergeler:
        return metin, []
    duzeltmeler, cikti = [], []
    for satir in metin.split("\n"):
        parcalar = re.split(r"(?<=[.!?])\s+", satir)
        yeni_parcalar = []
        for cumle in parcalar:
            if _BEKLENTI.search(cumle):
                yeni_parcalar.append(cumle)
                continue
            duzeltilecek = []
            yuzdeli_gostergeler = {
                "producer_prices_yoy", "producer_prices_mom",
                "core_inflation_yoy",
                "pce_inflation_yoy", "pce_inflation_mom", "core_pce_yoy",
                "core_pce_mom", "inflation_expectation_1y",
                "inflation_expectation_5y", "growth_yoy", "growth_qoq",
                "unemployment_rate", "u6_unemployment", "participation_rate",
                "wage_growth_yoy", "wage_growth_mom",
                "industrial_production_yoy", "industrial_production_mom",
                "retail_sales_yoy", "retail_sales_mom", "budget_gdp",
                "m3_yoy", "consumer_credit", "durable_goods",
                "tourist_arrivals_yoy", "capacity_utilization", "debt_gdp",
                "loans_companies_yoy", "loans_households_yoy",
                "consumer_spending_qoq", "rate_projection_1y",
                "rate_projection_2y", "deposit_rate", "consumer_loan_rate",
                "credit_growth_yoy",
            }
            for gosterge, desen in _GOSTERGE_KELIMELER.items():
                if not desen.search(cumle):
                    continue
                if gosterge == "unemployment_rate" and _U6_ETIKET.search(cumle):
                    continue
                adaylar = []
                sayi_deseni = (_SAYI_YUZDELI if gosterge in yuzdeli_gostergeler
                               else _SAYI_ONLIKLI)
                for sayi_m in sayi_deseni.finditer(cumle):
                    deger = _yuzde_degeri(sayi_m.group(0))
                    if deger is None:
                        continue
                    # GERCEK DEGER KORUMASI (2026-10-07): sayi baska bir
                    # gostergedeki gercek degerse (or. turizm 15,9, issizlik
                    # %7,7) anahtar-kelime yanilgisiyla degistirilmez.
                    if _gercek_mi(deger, izinli):
                        continue
                    d_ind = _kelime_mesafesi(cumle, sayi_m.start(), desen)
                    if d_ind is None or d_ind > 50:
                        continue
                    # Frekans ayrimi: "aylik ... %x" sayisi yillik gostergeye
                    # (ve tersi) yazilmaz; ipucu yoksa gosterge kirilimi esas.
                    sikil = _gosterge_sikligi(gosterge)
                    if sikil and _siklik_bul(cumle, sayi_m.start()) not in (None, sikil):
                        continue
                    ulke = _ulke_anahtari(cumle, sayi_m.start())
                    hedef = _gosterge_hedef(gostergeler, ulke, gosterge) if ulke else None
                    if hedef is not None:
                        adaylar.append((d_ind, sayi_m, hedef, deger))
                if adaylar:
                    yakin, sayi_m, hedef, deger = min(
                        adaylar, key=lambda x: x[0])
                    if abs(deger - hedef) > tolerans:
                        # deger tuplende tasinir: ayni sayiya bakan gostergeler
                        # arasinda secim sozluk sirasina degil, anahtar-kelime
                        # yakinligina (esitse deger yakinligina) gore yapilir
                        # (2026-10-07: turizm 15,9 ihracatla degistiriliyordu).
                        duzeltilecek.append((gosterge, yakin, sayi_m, hedef, deger))
            # Ayni sayi birden fazla gosterge adayinda gorundugunde eski
            # uygulama ayni konuma iki kez yaziyordu (metin kaymasi); yalnizca
            # ilk aday uygulanir (sozluk sirasi yillik tercih eder).
            gorulen, tek_aday = set(), []
            for aday in sorted(duzeltilecek,
                               key=lambda x: (x[2].start(), x[1],
                                              abs(x[4] - x[3]))):
                if aday[2].start() in gorulen:
                    continue
                gorulen.add(aday[2].start())
                tek_aday.append(aday)
            duzeltilecek = tek_aday
            for gosterge, _, m, hedef, _deger in sorted(
                    duzeltilecek, key=lambda x: x[2].start(), reverse=True):
                yazi = m.group(0)
                birim = "%" if gosterge in yuzdeli_gostergeler else ""
                dogru = _sayi_bicimle(hedef, yazi, birim)
                cumle = cumle[:m.start()] + dogru + cumle[m.end():]
                duzeltmeler.append((gosterge, yazi, dogru))
            yeni_parcalar.append(cumle)
        cikti.append(" ".join(yeni_parcalar) if len(parcalar) > 1 else yeni_parcalar[0])
    return "\n".join(cikti), duzeltmeler


_SAYI_MARKET = re.compile(r"(?<![\w.])-?\d{1,8}(?:[.,]\d+)?(?![\w])")


def _etiket_cekirdegi(etiket):
    """'Gram Altın (türetilmiş)' -> 'Gram Altın' (raporlar parantezi yazmaz)."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", etiket).strip()


def piyasa_serileri_duzelt(metin, seriler, tolerans=0.005, izinli=None):
    """Gece piyasa snapshot'ındaki değerleri metinde deterministik düzeltir.

    Etiketler en uzun cekirdekten eslesir; bir serinin etiketi diger bir
    serinin etiketi icinde geciyorsa ("Altin" icinde "Gram altin") o bulus
    atlanir, boylece gram altin sayisi ons altinla karismaz. Yuvarlama
    toleransi buyuk degerlerde oransal olarak genisler (4.286 vs 4286,30).
    """
    if not metin or not seriler:
        return metin, []
    aday_seriler = [r for r in seriler
                    if r.get("indicator") not in {"bist30", "bist100"}
                    and str(r.get("label", "")).strip()]
    if not aday_seriler:
        return metin, []
    sirali = sorted(
        aday_seriler,
        key=lambda r: len(_etiket_cekirdegi(str(r.get("label", "")))),
        reverse=True)
    duzeltmeler, cikti = [], []
    for satir in metin.split("\n"):
        cumleler = re.split(r"(?<=[.!?])\s+", satir)
        yeni = []
        for cumle in cumleler:
            # Cumledeki tum etiket buluslari bir kez bulunur (kapsama kontrolu).
            buluslar = {}
            for r in sirali:
                ck = _etiket_cekirdegi(str(r.get("label", "")))
                if ck not in buluslar:
                    buluslar[ck] = list(re.finditer(
                        r"(?<!\w)" + re.escape(ck) + r"(?!\w)", cumle,
                        re.IGNORECASE))
            adaylar, kapali = [], []
            for r in sirali:
                ck = _etiket_cekirdegi(str(r.get("label", "")))
                for etiket_m in buluslar[ck]:
                    # Daha uzun bir etiketin icindeyse bu bulus diger serinin.
                    if any(
                        len(dk) > len(ck)
                        and d2.start() <= etiket_m.start()
                        and etiket_m.end() <= d2.end()
                        for dk, lst in buluslar.items() if dk != ck
                        for d2 in lst
                    ):
                        continue
                    sonra = cumle[etiket_m.end():]
                    ayrac = re.match(
                        r"\s*(?:endeksi|değeri|seviyesi|fiyatı|kapanışı|getirisi|"
                        r"endekse|gramı|at|level|price)?\s*(?:[:=-]\s*|[ \t]+)?",
                        sonra, re.IGNORECASE)
                    sayi_bas = etiket_m.end() + (ayrac.end() if ayrac else 0)
                    sayi_m = _SAYI_MARKET.match(cumle, sayi_bas)
                    if not sayi_m:
                        continue
                    # Ayni sayi zaten baska bir serinin adayi tarafindan alindi.
                    if any(s < sayi_m.end() and sayi_m.start() < e
                           for s, e in kapali):
                        continue
                    deger = _yuzde_degeri(sayi_m.group(0))
                    if deger is None:
                        continue
                    kapali.append((sayi_m.start(), sayi_m.end()))
                    hedef = float(r["value"])
                    if (abs(deger - hedef) > max(tolerans, abs(hedef) * 0.0005)
                            and not _gercek_mi(deger, izinli)):
                        adaylar.append((sayi_m, hedef, r.get("unit", ""),
                                        str(r.get("label", ""))))
            for m, hedef, birim, etiket in sorted(
                    adaylar, key=lambda x: x[0].start(), reverse=True):
                yazi = m.group(0)
                dogru = _sayi_bicimle(hedef, yazi, birim)
                cumle = cumle[:m.start()] + dogru + cumle[m.end():]
                duzeltmeler.append((etiket, yazi, dogru))
            yeni.append(cumle)
        cikti.append(" ".join(yeni) if len(yeni) > 1 else yeni[0])
    return "\n".join(cikti), duzeltmeler


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


# TCMB kurum adi: coklu-kurum cumlelerinde sayiya tr oranini esler.
_MERKEZ_TCMB = re.compile(
    r"\b(?:TCMB|Türkiye Cumhuriyet Merkez Bankası)\b", re.IGNORECASE)
# Yabanci endeks adi gecen cumleler BIST seviyesiyle denetlenmez; aksi halde
# "DXY endeksi 102.500 seviyesine cikti" ifadesindeki 102.500, XU030 degeriyle
# (15.327) degistiriliyordu (dogrulanmis saldiri vektoru, 2026-10-05).
_YABANCI_ENDEKS = re.compile(
    r"\b(DXY|S\s*&\s*P|SP\s*500|Nasdaq|Dow(?:\s+Jones)?|DAX|FTSE|Nikkei|"
    r"Euro Stoxx|CAC\s*40|MSCI|Brent|Gold|Gold Spot|XAU|Alt[ıi]n)", re.I)


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


def endeks_seviye_duzelt(metin, endeks_seviyesi, tolerans=0.15,
                         endeks_seviyeleri=None, izinli=None):
    """Endeks cumlelerindeki seviyeleri gercek degerle karsilastirir.

    Donus: (yeni_metin, duzeltmeler, uyarilar); duzeltme = (eski, yeni),
    uyari = metin. Yalnizca hem endeks hem seviye baglami gecen CUMLELERDEKI
    sayilar ele alinir; tolerans disindaki sayilar dogru seviyeyle degistirilir.

    `endeks_seviyeleri` = {"30": 15327.05, "100": 16450.2} verilirse cumlede
    hangi endeks etiketi (BIST 30/XU030 vs BIST 100/XU100) geciyorsa o
    endeksin gercek seviyesiyle denetlenir. Tek endeksle karsilastirma yapan
    surum, modelin "BIST 100 = XU030 degeri" kopyalamasini goremezdi
    (2 Ekim 2026 raporundaki canli hata): iki endeks ayni sayiyi yazdiginda
    hatali deger dogru saniliyordu. Cumlede iki endeks birden geciyorsa
    (karsilastirma cumlesi) o sayi atlanir; etiket yoksa XU030 varsayilir.
    """
    if not metin:
        return metin, [], []
    haritalar = {}
    if endeks_seviyesi:
        haritalar["30"] = endeks_seviyesi
    for k, v in (endeks_seviyeleri or {}).items():
        if v:
            haritalar[k] = v
    if not haritalar:
        return metin, [], []
    varsayilan = haritalar.get("30")
    duzeltmeler, uyarilar = [], []
    cikti = []
    for satir in metin.split("\n"):
        parcalar = re.split(r"(?<=[.!?])\s+", satir)
        yeni_parcalar = []
        for cumle in parcalar:
            if _ENDEKS_BAGLAM.search(cumle) and _SEVIYE_BAGLAM.search(cumle):
                yabanci = _YABANCI_ENDEKS.search(cumle)
                etiket100 = re.search(r"(BIST\s*100|XU0?100)", cumle, re.I)
                etiket30 = re.search(r"(BIST\s*30|XU0?30)", cumle, re.I)
                per_number = bool(yabanci)
                if per_number:
                    # Yabanci endeks gecen cumlede sayi-basina etiket esleme
                    # (2026-10-07): sayidan onceki en yakin etiket kurumu
                    # belirler; acik BIST/XU etiketi YOKSA sayi ATLANIR —
                    # varsayilana (XU030) dusmez, aksi halde "DXY endeksi
                    # 102.500" ifadesi 16.372'ye "duzeltilirdi" (5 Ekim vektoru).
                    etiketler = []
                    for m2 in re.finditer(r"(BIST\s*100|XU0?100)", cumle, re.I):
                        etiketler.append((m2.start(), "100"))
                    for m2 in re.finditer(r"(BIST\s*30|XU0?30)", cumle, re.I):
                        etiketler.append((m2.start(), "30"))
                    for m2 in _YABANCI_ENDEKS.finditer(cumle):
                        etiketler.append((m2.start(), None))
                    etiketler.sort()
                    hedef_seviye = True  # sayi-basina mod
                elif etiket100 and etiket30:
                    hedef_seviye = None      # karsilastirma cumlesi: belirsiz
                elif etiket100:
                    hedef_seviye = haritalar.get("100", varsayilan)
                elif etiket30:
                    hedef_seviye = haritalar.get("30", varsayilan)
                else:
                    hedef_seviye = varsayilan
                if hedef_seviye:
                    if per_number:
                        dogru_varsayilan = None
                    else:
                        dogru_varsayilan = _binlik(hedef_seviye)

                    def _bak(m):
                        if per_number:
                            oncekiler = [(p, k) for p, k in etiketler
                                         if p < m.start()]
                            if not oncekiler:
                                return m.group(0)   # acik etiket yok -> atla
                            k2 = max(oncekiler)[1]
                            if k2 is None:
                                return m.group(0)   # yabanci endekse ait -> atla
                            hedef = haritalar.get(k2, varsayilan)
                            if not hedef:
                                return m.group(0)
                            dogru = _binlik(hedef)
                        else:
                            hedef = hedef_seviye
                            dogru = dogru_varsayilan
                        try:
                            deger = _sayiya(m.group(1))
                        except ValueError:
                            return m.group(0)
                        if deger <= 0:
                            return m.group(0)
                        # GERCEK DEGER KORUMASI (2026-10-07): sayi snapshot'taki
                        # herhangi bir gercek degerse dokunulmaz.
                        if _gercek_mi(deger, izinli, tolerans=0.0005):
                            return m.group(0)
                        # Kopya saptamasi (tolerans denetiminden ONCE): sayi
                        # haritadaki BASKA bir endeksin gercek degerine yakinsa
                        # ve hedef endeksten acik sekilde sapiyorsa model o
                        # endeksten kopyalamistir ("BIST 100 = BIST 30
                        # seviyesi", 2026-10-02 canli hata; %15 tolerans
                        # icinde kaldigi icin baska turlu gorunmez).
                        for diger_k, diger_v in haritalar.items():
                            if (diger_v and abs(deger / diger_v - 1) <= 0.005
                                    and abs(deger / hedef - 1) > 0.01):
                                duzeltmeler.append((m.group(1), dogru))
                                return dogru
                        if abs(deger / hedef - 1) <= tolerans:
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


def _gercek_mi(deger, izinli, tolerans=0.005):
    """Sayi, snapshot'in herhangi bir gercek degerine yaklasik eslesiyor mu?

    Bagil tolerans (%0,5): 61,6~61,563, 26,0~25,98, 29,7~29,73 gibi yuvarlama
    biçimleri korunur; 3,4 (ABD enflasyonu) gibi baska bir gercek deger de
    korunur — dogrulayici gercek degerleri BIRBIRIYLE degistirmemeli
    (2026-10-07: canli yayinda dogrulayicinin kendisi %3,4 -> %29,73 yazmisti).
    """
    if not izinli:
        return False
    for g in izinli:
        fark = abs(deger - g)
        if fark <= tolerans or fark <= tolerans * abs(g):
            return True
    return False


def metin_dogrula(metin, adlar, enflasyon_yuzde=None, endeks_seviyesi=None,
                   faiz_oranlari=None, enflasyon_oranlari=None,
                   makro_gostergeleri=None, piyasa_serileri=None,
                   enflasyon_aylik_oranlari=None, endeks_seviyeleri=None,
                   izinli_degerler=None):
    """Tum dogrulama zinciri. Donus sozlugu:
    {"metin": ..., "isim_duzeltme": [...], "enflasyon_duzeltme": [...],
     "endeks_duzeltme": [...], "endeks_uyari": [...], "faiz_duzeltme": [...],
     "makro_duzeltme": [...], "piyasa_duzeltme": [...]}.
    `makro_gostergeleri`: makro snapshot'inin ulke/indikator deger sozlugu.
    `endeks_seviyeleri`: {"30": XU030 seviyesi, "100": XU100 seviyesi} —
    BIST 100 cumleleri gercek XU100 seviyesiyle denetlenir.
    Hicbir durumda istisna yukseltmez; cagiran taraf zaten sarmaladi.
    """
    metin, isim = isim_duzelt(metin, adlar)
    metin, enf = enflasyon_duzelt(metin, enflasyon_yuzde, oranlar=enflasyon_oranlari,
                                  oranlar_aylik=enflasyon_aylik_oranlari,
                                  izinli=izinli_degerler)
    metin, faiz = faiz_duzelt(metin, faiz_oranlari, izinli=izinli_degerler)
    metin, makro = makro_gosterge_duzelt(metin, makro_gostergeleri,
                                         izinli=izinli_degerler)
    metin, piyasa = piyasa_serileri_duzelt(metin, piyasa_serileri,
                                           izinli=izinli_degerler)
    metin, endeks, uyari = endeks_seviye_duzelt(metin, endeks_seviyesi,
                                                endeks_seviyeleri=endeks_seviyeleri,
                                                izinli=izinli_degerler)
    return {"metin": metin, "isim_duzeltme": isim, "enflasyon_duzeltme": enf,
            "endeks_duzeltme": endeks, "endeks_uyari": uyari,
            "faiz_duzeltme": faiz, "makro_duzeltme": makro,
            "piyasa_duzeltme": piyasa}


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

    # Politika faizi: yanlis oran duzeltilir, dogru/abarti oranlara dokunulmaz,
    # enflasyon+faiz karisik cumlelerde iki denetim birbirine karismaz.
    ORAN = {"tr": 37.0, "us": 4.0, "eu": 2.65}
    faiz_ornek = ("TCMB politika faizi %43 seviyesinde tutuldu. "
                  "Fed faizi %5 düzeyinde. "
                  "Enflasyon %31,5 iken politika faizi %37 olarak açıklandı. "
                  "Yeni yıl enflasyon beklentisi %24.")
    s = faiz_duzelt(faiz_ornek, ORAN)
    print(s[0])
    assert "%37" in s[0] and ("%43" not in s[0]), "politika faizi duzeltilmedi!"
    assert "%5" not in s[0] and "%4" in s[0], "Fed faizi duzeltilmedi!"
    assert "%31,5" in s[0], "enflasyon yuzdesi faiz denetimince bozuldu!"
    assert "%24" in s[0], "beklenti cumlesine dokunuldu!"
    assert len(s[1]) == 2, f"beklenen 2 duzeltme, gelen {len(s[1])}"

    # Ayni cumlede merkez bankasi hedefi ve gercek Federal Reserve faizi vardir;
    # yalnizca gercek faiz duzeltilir.
    hedefli_faiz = ("Merkez bankasının %2 hedefi varken Federal Reserve "
                    "politika faizi %37 seviyesindedir.")
    hf = faiz_duzelt(hedefli_faiz, ORAN)
    assert "%2" in hf[0] and "%4" in hf[0] and "%37" not in hf[0], "hedef/faiz ayrimi basarisiz!"

    # Ayni durum ayri cumlelerde oldugunda da hedef oranı korunur.
    ayri_hedefli_faiz = (
        "Merkez bankasının %2 hedefi hâlâ uzak. "
        "Bu bağlamda Federal Reserve politika faizi %37 seviyesindedir.")
    ahf = faiz_duzelt(ayri_hedefli_faiz, ORAN)
    assert "%2" in ahf[0] and "%4" in ahf[0] and "%37" not in ahf[0], "ayri hedef/faiz ayrimi basarisiz!"
    print("dogrulama.py: tum kendini testler gecti.")

    ornek2 = "Enflasyon %28,4 seviyesinde yatay seyrediyor."
    sonuc2 = metin_dogrula(ornek2, ADLAR, enflasyon_yuzde=31.51,
                           enflasyon_oranlari={"tr": 31.51})
    print(sonuc2["metin"])
    assert "%31,51" in sonuc2["metin"], "enflasyon duzelmedi!"

    # Ulke baglami: ABD/Euro enflasyonu Turkiye oraniyla degistirilmemeli.
    ENF_ORAN = {"tr": 31.51, "us": 3.4, "eu": 3.2}
    ulke_ornek = ("ABD'de yıllık enflasyon %3,3 seviyesinde. "
                  "Euro Bölgesi'nde yıllık enflasyon %3,2 olarak açıklandı. "
                  "Türkiye'de yıllık enflasyon %28,4 seviyesinde.")
    su = metin_dogrula(ulke_ornek, ADLAR, enflasyon_yuzde=31.51,
                       enflasyon_oranlari=ENF_ORAN)
    print(su["metin"])
    assert "%3,4" in su["metin"], "ABD enflasyonu duzeltilmedi!"
    assert "%3,2" in su["metin"], "Euro enflasyonu bozuldu!"
    assert "%31,51" in su["metin"], "Turkiye enflasyonu duzeltilmedi!"
    print("uluslararasi enflasyon baglami testi gecti.")

    # Canli sayfadaki hatanin birebir regresyonu: iki yabanci ulke orani
    # yanlis olarak Turkiye oranina cevrilmemeli.
    canli_hata = ("ABD'deki yıllık enflasyon oranı %31,5 seviyesinde. "
                  "Avrupa Merkez Bankası (ECB) yıllık enflasyon oranını %31,5 "
                  "olarak açıkladı.")
    sh = metin_dogrula(canli_hata, ADLAR, enflasyon_yuzde=31.51,
                       enflasyon_oranlari=ENF_ORAN)
    assert "%3,4" in sh["metin"] and "%3,2" in sh["metin"], "canli hata duzelmedi!"
    assert len(sh["enflasyon_duzeltme"]) == 2, "iki yanlis oran kaydedilmedi!"

    # Karsilastirma yapan cumlede ECB sahibi olmali; "ABD'deki gibi" ifadesi
    # Euro oranini ABD oranina cevirmemeli.
    karsilastirma = ("ECB, enflasyonu ABD'deki gibi kontrol altına alamadı; "
                     "yıllık enflasyon %31,5 olarak gerçekleşti.")
    sk = metin_dogrula(karsilastirma, ADLAR, enflasyon_yuzde=31.51,
                       enflasyon_oranlari=ENF_ORAN)
    assert "%3,2" in sk["metin"] and "%3,4" not in sk["metin"], "ECB baglami bozuk!"

    # Ayni cumlede iki ulke varsa her oran kendi ulkesine gore duzeltilir.
    iki_ulke = "ABD enflasyonu %31,5, Euro Bölgesi enflasyonu %31,5."
    si = metin_dogrula(iki_ulke, ADLAR, enflasyon_yuzde=31.51,
                       enflasyon_oranlari=ENF_ORAN)
    assert si["metin"] == "ABD enflasyonu %3,4, Euro Bölgesi enflasyonu %3,2.", si["metin"]

    # Beklenti/hedef orani, alt enflasyon ve baska gosterge oranlari genel
    # (TUIK) oraniyla degistirilmemeli.
    korumalar = (
        "Yıl sonu enflasyon beklentisi %24 olarak korunmalı. "
        "Enerji enflasyonu %25 seviyesinde. "
        "Enflasyon %28,4. Büyüme %2,3.")
    sp = metin_dogrula(korumalar, ADLAR, enflasyon_yuzde=31.51,
                       enflasyon_oranlari=ENF_ORAN)
    assert "%24" in sp["metin"] and "%25" in sp["metin"], "beklenti/alt enflasyon bozuldu!"
    assert "%31,51" in sp["metin"] and "%2,3" in sp["metin"], "gosterge karisimi bozuldu!"

    ornek3 = "Yıl sonu enflasyon beklentisi %24 olarak korunmalı."  # geriye uyumluluk
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

    # --- Turkce binlik ayracli sayi okumasi ---
    assert _yuzde_degeri("4.286") == 4286.0, "binlik ayracli sayi okunamadi!"
    assert _yuzde_degeri("31,51") == 31.51, "ondalik virgul bozuldu!"
    assert _yuzde_degeri("16.372,83") == 16372.83, "binlik+ondalik okunamadi!"
    assert _yuzde_degeri("3.4") == 3.4, "noktali ondalik bozuldu!"

    # --- Gram altin etiketi "Altin" ile karismaz; dogru deger yazilir ---
    seriler = [
        {"indicator": "gold_try", "label": "Gram Altın (türetilmiş)",
         "value": 6731.74, "unit": "TL/gram"},
        {"indicator": "gold_usd", "label": "Altın", "value": 4286.30,
         "unit": "USD/ons"},
    ]
    gram, gram_d = piyasa_serileri_duzelt(
        "Gram altın 5.000 TL'den, ons altın 4.280 dolardan işlem gördü.",
        seriler)
    assert "6.731,74" in gram, f"gram altin duzeltilmedi: {gram}"
    assert "4.286,3" in gram, f"ons altin duzeltilmedi: {gram}"
    assert len(gram_d) == 2, f"beklenen 2 piyasa duzeltmesi: {gram_d}"
    _, dokunma_d = piyasa_serileri_duzelt(
        "Gram altın 6.731 TL, ons altın 4.286 dolar.", seriler)
    assert dokunma_d == [], f"dogru degere dokunuldu: {dokunma_d}"

    # --- Frekans ayrimi: aylik/yillik/ceyreklik kendi gostergesine yazilir ---
    GOST = {"tr": {"industrial_production_yoy": 4.5,
                   "industrial_production_mom": 1.2,
                   "growth_yoy": 2.3, "growth_qoq": 1.1}}
    ay1, ay1d = makro_gosterge_duzelt("Sanayi üretimi aylık %5,0 arttı.", GOST)
    assert "%1,2" in ay1 and len(ay1d) == 1, f"aylik sanayi ayrimi: {ay1} {ay1d}"
    yl1, yl1d = makro_gosterge_duzelt("Sanayi üretimi yıllık %5,0 arttı.", GOST)
    assert "%4,5" in yl1 and len(yl1d) == 1, f"yillik sanayi ayrimi: {yl1} {yl1d}"
    cq1, cq1d = makro_gosterge_duzelt("Çeyreklik büyüme %5,0 arttı.", GOST)
    assert "%1,1" in cq1 and len(cq1d) == 1, f"ceyreklik buyume ayrimi: {cq1} {cq1d}"
    vt1, vt1d = makro_gosterge_duzelt("Sanayi üretimi %5,0 arttı.", GOST)
    # Ipucu yoksa yillik varsayilir; ayni sayiya IKINCI bir aday uygulanmaz.
    assert "%4,5" in vt1 and len(vt1d) == 1, f"varsayilan yillik/dedupe: {vt1} {vt1d}"

    # --- Enflasyon frekans kirilimi ---
    AYLIK, YILLIK = {"tr": 0.22}, {"tr": 31.51}
    e1, e1d = enflasyon_duzelt("Aylık enflasyon %1,84 olarak açıklandı.",
                               31.51, oranlar=YILLIK, oranlar_aylik=AYLIK)
    assert "%0,22" in e1 and len(e1d) == 1, f"aylik enflasyon kirilimi: {e1}"
    e2, e2d = enflasyon_duzelt("Aylık enflasyon %1,84 olarak açıklandı.",
                               31.51, oranlar=YILLIK)
    assert "%1,84" in e2 and e2d == [], f"aylik veri yokken dokunuldu: {e2}"
    e3, e3d = enflasyon_duzelt("Yıllık enflasyon %28,4 seviyesinde.",
                               31.51, oranlar=YILLIK, oranlar_aylik=AYLIK)
    assert "%31,51" in e3 and len(e3d) == 1, f"yillik enflasyon kirilimi: {e3}"

    # --- 2026-10-02 canli hata regresyonlari (2 Ekim raporu yayindi) ---
    # 1) "pozitif bolgede" kelimesi cumleyi Euro baglamina sokuyordu; TR
    #    enflasyonu %3,2 olarak ve TR faizi %2,65 (EU degeri) olarak kaldi.
    CANLI = {"tr": 31.51, "us": 3.4, "eu": 3.8}
    CANLI_FAIZ = {"tr": 37.0, "us": 4.0, "eu": 2.65}
    canli = ("Türkiye'de yıllık enflasyon %3,2 seviyesinde (Eylül verisi) seyrederken "
             "politika faizi %2,65'da sabit tutulmakta, reel faizlerin hâlâ pozitif "
             "bölgede olmasına rağmen kredi büyümesi yıllık %1,2 seviyelerinde...")
    sc = metin_dogrula(canli, {}, 31.51, faiz_oranlari=CANLI_FAIZ,
                       enflasyon_oranlari=CANLI)
    assert "%31,51" in sc["metin"], f"canli enflasyon duzelmedi: {sc['metin'][:120]}"
    assert "%37" in sc["metin"], f"canli politika faizi duzelmedi: {sc['metin'][:160]}"
    assert "Euro Bölgesi" not in sc["metin"] or "%2,65" in sc["metin"], "EU orani bozuldu"
    # 2) BIST 100 = BIST 30 kopyasi: %15 tolerans icinde kalsa da yakalanmali.
    se = endeks_seviye_duzelt(
        "BIST 100 endeksi 15.327 seviyesinde, kazançla işlem görmekteyken.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert se[1] and "16.450" in se[0], f"BIST100 kopyasi yakalanmadi: {se[0]}"
    # dogru XU100 degerine dokunulmaz
    sd = endeks_seviye_duzelt(
        "BIST 100 endeksi 16.480 seviyesinde seyrediyor.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert sd[1] == [], "dogru XU100 degeri bozuldu"
    # karsilastirma cumlesinde iki endeks degerine dokunulmaz
    sk = endeks_seviye_duzelt(
        "BIST 100 (16.450) ile BIST 30 (15.327) birlikte yukseldi.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert sk[1] == [], "karsilastirma cumlesi bozuldu"

    # --- Kelime-oyunu saldiri vektorleri (2026-10-05, dogrulanmis) ---
    # 1) "euro bazindaki" cumleyi Euro baglamina sokup TR enflasyonunu EU
    #    degeriyle degistirtiyordu.
    s1 = metin_dogrula(
        "Endeksin euro bazındaki performansı %0,48 oldu; yıllık enflasyon %28,4 seviyesinde.",
        {}, 31.51, enflasyon_oranlari=CANLI)
    assert "%31,51" in s1["metin"] and "%3,8" not in s1["metin"], \
        f"euro-bazindaki saldirisi acik: {s1['metin']}"
    # 2) Gercek mevduat faizi politika faiziyle degistiriliyordu.
    sf, sdf = faiz_duzelt(
        "Mevduat faizi %43,8 seviyesindeyken politika faizi sabit kaldi.", CANLI_FAIZ)
    assert "%43,8" in sf and sdf == [], f"mevduat faizi bozuldu: {sf}"
    sp, spd = faiz_duzelt("TCMB politika faizi %2,65'da sabit tutulmakta.", CANLI_FAIZ)
    assert "%37" in sp and spd, "gercek politika faizi hatasi gorunmez oldu!"
    # 3) Yabanci endeks seviyesi BIST degeriyle degistiriliyordu.
    sy = endeks_seviye_duzelt(
        "DXY endeksi 102.500 seviyesine cikti; BIST uygulamasi izlendi.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert sy[1] == [] and "102.500" in sy[0], f"DXY bozuldu: {sy[0]}"

    # --- GERCEK DEGER KORUMASI (2026-10-07, canli STORM kosusu vakalari) ---
    # Dogrulayici, snapshot'ta VAR olan bir degeri baska bir gercek degerle
    # "duzeltiyordu": %3,4 (ABD enflasyonu) -> %29,73 (TR enflasyonu) yazmisti.
    IZINLI = [3.4, 29.73, 1.84, 37.0, 4.0, 2.65, 64.74, 43.795, 7.7, 15.9,
              25.98, 36.3222, 61.563, 27.38, 8.2, 3.8, 2.2, 5.4, 1.2, -0.3234]
    # 1) ABD bolumunde DOGRU %3,4 yazilmisti; %29,73 ile degistiriliyordu.
    sa = metin_dogrula(
        "ABD ekonomisi manşet enflasyon %3,4 seviyesinde; politika faizi %4,0 seviyesinde.",
        ADLAR, enflasyon_yuzde=29.73,
        enflasyon_oranlari={"tr": 29.73, "us": 3.4},
        faiz_oranlari={"tr": 37.0, "us": 4.0},
        izinli_degerler=IZINLI)
    assert "%3,4" in sa["metin"], f"gercek ABD enflasyonu bozuldu: {sa['metin']}"
    assert "%29,73" not in sa["metin"], f"TR degeri ABD cumlesine yazildi: {sa['metin']}"
    # 2) Tuketici kredisi faizi %64,74 politika faizi havuzuyla degistiriliyordu.
    sk2 = faiz_duzelt("Tuketici kredisi faizi %64,74 seviyesine cikti.",
                      {"tr": 37.0, "us": 4.0, "eu": 2.65}, izinli=IZINLI)
    assert "%64,74" in sk2[0] and sk2[1] == [], f"kredi faizi bozuldu: {sk2}"
    # 3) Issizlik orani %7,7 sanayi uretimiyle (-0,323) degistiriliyordu.
    sg = makro_gosterge_duzelt(
        "Turkiye'de issizlik orani %7,7 seviyesinde kaldi.",
        {"tr": {"unemployment_rate": 7.7, "industrial_production_yoy": -0.3234}},
        izinli=IZINLI)
    assert "%7,7" in sg[0] and sg[1] == [], f"issizlik orani bozuldu: {sg}"
    # 4) Turizm gelirleri 15,9 ihracatla (25,98) degistiriliyordu; cumlede
    # ihracat kelimesi gectigi icin exports desenine yakaliyordu.
    sg2 = makro_gosterge_duzelt(
        "Ihracat artisiyla turizm gelirleri 15,9 milyar dolara ulasti.",
        {"tr": {"exports": 25.98, "tourism_revenues": 15.9}},
        izinli=IZINLI)
    assert "15,9" in sg2[0] and sg2[1] == [], f"turizm geliri bozuldu: {sg2}"
    # 5) ECB faizi %2,65 "buyume" kelimesi yuzunden buyume degeriyle (1,2)
    # degistiriliyordu.
    sg3 = makro_gosterge_duzelt(
        "ECB politika faizi %2,65 ile buyume dengesini ariyor.",
        {"eu": {"growth_yoy": 1.2}},
        izinli=IZINLI)
    assert "%2,65" in sg3[0] and sg3[1] == [], f"ECB faizi bozuldu: {sg3}"
    # 6) UYDURMA hala duzeltilir: evrende olmayan %55,1 TR oraniyla degisir.
    su2 = metin_dogrula("Turkiye'de yillik enflasyon %55,1 seviyesinde.",
                        ADLAR, enflasyon_yuzde=29.73,
                        enflasyon_oranlari={"tr": 29.73},
                        izinli_degerler=IZINLI)
    assert "%29,73" in su2["metin"], "uydurma artik duzeltilmiyor!"
    # 7) Yuvarlama korunur: %29,7, gercek %29,73'e dokunulmadan kalir.
    sy2 = metin_dogrula("Turkiye'de yillik enflasyon %29,7 seviyesinde.",
                        ADLAR, enflasyon_yuzde=29.73,
                        enflasyon_oranlari={"tr": 29.73},
                        izinli_degerler=IZINLI)
    assert "%29,7 seviyesinde" in sy2["metin"], f"yuvarlama bozuldu: {sy2['metin']}"

    # --- AYNI SAYIYA BIRDEN FAZLA GOSTERGE: sozluk sirasi degil yakınlık
    # kazansin (2026-10-07, Replit incelemesi). "Turizm gelirleri" sayiya en
    # yakin kelime; exports degeriyle degistirilmesi hataydi.
    g1 = makro_gosterge_duzelt(
        "Ihracat artisiyla turizm gelirleri 14,2 milyar dolara ulasti.",
        {"tr": {"exports": 25.98, "tourism_revenues": 15.9}}, izinli=IZINLI)
    assert g1[1] and g1[1][0][0] == "tourism_revenues" and "15,9" in g1[0],         f"yakinlik secimi calismadi: {g1}"
    # Gercek deger korunur kurali ayni vaka: 15,9 zaten gercekse hic dokunma.
    g2 = makro_gosterge_duzelt(
        "Ihracat artisiyla turizm gelirleri 15,9 milyar dolara ulasti.",
        {"tr": {"exports": 25.98, "tourism_revenues": 15.9}}, izinli=IZINLI)
    assert g2[1] == [] and "15,9" in g2[0], f"gercek deger bozuldu: {g2}"

    # --- U-6 kontrolu buyuk/kucuk harf duyarsiz (Replit incelemesi):
    # kucuk harfli "u-6" cumlesi genel issizlik oraniyla degistiriliyordu.
    U6 = {"us": {"unemployment_rate": 4.2, "u6_unemployment": 7.6}}
    u1 = makro_gosterge_duzelt(
        "ABD'de u-6 işsizlik oranı %7,6 seviyesinde kaldi.", U6)
    assert u1[1] == [] and "%7,6" in u1[0], f"u-6 gercek degeri bozuldu: {u1}"
    # Uydurma u-6 degeri dogru gostergese (u6) duzeltilir, genel orana degil.
    u2 = makro_gosterge_duzelt(
        "ABD'de u-6 işsizlik oranı %8,1 seviyesine cikti.", U6, izinli=[4.2])
    assert u2[1] and u2[1][0][0] == "u6_unemployment" and "7,6" in u2[0],         f"u-6 uydurmasi yanlis gostergese gitti: {u2}"

    # --- COKLU-ULKE / COKLU-KURUM CUMLELERI (2026-10-07, ChatGPT stres testi) ---
    # 1) "ABD enflasyonu %3,4, Türkiye enflasyonu %28,4": TR sayisi ABD
    #    oranina degistiriliyordu (_ulke_anahtari'da TR deseni yoktu).
    sm1 = metin_dogrula(
        "ABD enflasyonu %3,4, Türkiye enflasyonu %28,4 seviyesinde.",
        ADLAR, enflasyon_yuzde=29.73,
        enflasyon_oranlari={"tr": 29.73, "us": 3.4},
        izinli_degerler=[3.4, 29.73])
    assert "%3,4," in sm1["metin"] and "Türkiye enflasyonu %29,73" in sm1["metin"],         f"TR sayisi yanlis ulkeye gitti: {sm1['metin']}"
    # 2) "Fed faizi %5, ECB politika faizi %4,5": ECB sayisi Fed havuzuyla
    #    eslesiyordu; artik her sayi kendi en yakin kurumuyla denetlenir.
    sf2 = faiz_duzelt(
        "Fed faizi %5, ECB politika faizi %4,5.",
        {"tr": 37.0, "us": 4.0, "eu": 2.65}, izinli=[4.0, 2.65])
    assert "%4," in sf2[0] and "2,65" in sf2[0] and "%4,5" not in sf2[0],         f"coklu kurum eslemesi calismadi: {sf2}"
    # 3) Yabanci endeksli cumlede BIST sayisi artik denetlenir, yabanci
    #    sayi dokunulmaz kalir; etiketsiz sayi yine atlanir.
    se1 = endeks_seviye_duzelt(
        "BIST 30 12000 seviyesinde kapandi, DXY 102.500 seviyesinde.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert "15.327" in se1[0] and "102.500" in se1[0], f"DXY/BIST karisti: {se1}"
    se2 = endeks_seviye_duzelt(
        "DXY endeksi 102.500 seviyesine cikti.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert se2[1] == [] and "102.500" in se2[0], f"etiketsiz sayi bozuldu: {se2}"

    # --- ChatGPT yeniden-yazim incelemesinden alinan ek regresyonlar ---
    # Anahtar alias: cogul (tourism_revenues) deseni tekil anahtarli veriyle
    # de calismali; aksi halde ayni sayi hic duzeltilmeden geciyordu.
    tg1 = makro_gosterge_duzelt(
        "Turizm gelirleri 20,0 milyar dolar oldu.",
        {"tr": {"tourism_revenue": 15.9}})
    assert "15,9" in tg1[0] and tg1[1], f"tekil anahtar alias calismadi: {tg1}"
    # Fed+ECB ayni cumlede, izinli'siz: her sayi kendi kurumuna duzeltilir.
    fe = faiz_duzelt(
        "Fed faizi %5, ECB politika faizi %4.",
        {"tr": 37.0, "us": 4.0, "eu": 2.65})
    assert fe[0] == "Fed faizi %4, ECB politika faizi %2,65." and len(fe[1]) == 2, fe
    # ABD+TR ayni cumlede, izinli'siz (uydurma TR sayisi TR oranina gider).
    tu = enflasyon_duzelt(
        "ABD enflasyonu %3,4, Türkiye enflasyonu %28,4.",
        31.51, oranlar={"tr": 31.51, "us": 3.4, "eu": 3.8})
    assert tu[0] == "ABD enflasyonu %3,4, Türkiye enflasyonu %31,51.", tu
    # Yabanci endeksli cumlede BIST denetlenir, yabanci sayi korunur.
    bd = endeks_seviye_duzelt(
        "BIST 30 endeksi 10.000 direnc, DXY endeksi 102.500 direnc.",
        15327.05, endeks_seviyeleri={"30": 15327.05, "100": 16450.0})
    assert "10.000" not in bd[0] and "15.327" in bd[0] and "102.500" in bd[0] and bd[1], bd

    print("dogrulama.py: tum kendini testler gecti.")
