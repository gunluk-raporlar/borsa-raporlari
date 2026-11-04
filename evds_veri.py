# -*- coding: utf-8 -*-
"""TCMB EVDS veri modulu (opsiyonel; anahtar yoksa sessizce atlanir).

`EVDS_API_KEY` ortam degiskeni yoksa ya da istek basarisizsa hicbir veri
EKLEMEZ; makro snapshot'i TV/TÜİK verisiyle aynen calismaya devam eder.
Anahtar oldugunda iki blogu doldurur:

- Makro (TR): mevduat faizi, tuketici kredisi faizi, kredi buyumesi,
  M3 para yillik buyumesi, reel efektif doviz kuru; 2026-11-04'ten itibaren
  enflasyon (yillik, TP.FG.J0 + formulas=3) — taze oldugunda TV satirinin
  UZERINE yazar ("ez"); TUIK SDMX teyitli satiri ezmez.

Dogrulanmis seri kodlari `ELLE_KODLAR` haritasinda elle sabitlidir (24-25
 Eylul 2026 canli denetimle veri + ad birebir kontrol edildi); bu gostergeler
kesif yapmadan dogrudan o kodla cagirilir, boylece arsiv/bayat gruplarin
kalabaliginda yanlis seri secilmez. Haritadaki bir seri EVDS'den kaldirilirsa
veri cekme hatasi loglanir ve o gosterge yalnizca o gun atlanir.

Haritada olmayan hedefler icin seri kodlari `datagroups` + `serieList`
uzerinden kelime eslemesiyle kesfedilir (taze gruplar once taranir; arsiv ve
550 gunden eski grup/seriler ele gelir) ve `data/evds-kodlar.json` dosyasinda
sabitlenir. Kelime eslemesi sonucu belirsizse o gosterge ATLANIR (uygun seri
uydurulmaz) ve adaylar loglanir.

NOT: EVDS'de sabit vadeli (2/10 yillik) TR tahvil getirisi serisi YOKTUR
(25-09-2026 bes uclu denetimle dogrulandi: 4208 pydibs serisi tamami tek tek
ISIN; 14 taze "faiz" grubunda toplu getiri serisi yok) - o yuzden hedeflistede
degildir.

EVDS REST ornegi - parametreler yolun icine yazilir, `?` KULLANILMAZ
(`?`-li istek 400 "Missing parameters" dondurur; 24-09-2026 canli denetimle
dogrulandi): `.../igmevdsms-dis/series=KOD&startDate=gg-aa-yyyy
&endDate=gg-aa-yyyy&type=json&frequency=5&formulas=3` + `key` header.
Anahtarsiz istek HTTP 401 "Invalid API Key" dondurur (olay kayitlanir).
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("evds")

KOK = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"
KOD_DOSYA = Path("data/evds-kodlar.json")

# EVDS frekans kodlari: 1=gunluk, 5=aylik, 6=ceyreklik, 8=yillik.
# formuller: yok/0=seviye, 3=yillik yuzde degisim.
MACRO_HEDEFLERI = (
    {"id": "deposit_rate", "ad": "Mevduat Faizi", "birim": "%",
     "siklik": "aylık", "frekans": 5, "formul": 0,
     "min": 0.0, "max": 150.0, "gun": 400,
     "grup": r"mevduat|deposit", "seri": r"mevduat|deposit",
     "tercih": r"t[u]m|all|ortalama|average", "disi": r"kredi|credit"},
    {"id": "consumer_loan_rate", "ad": "Tüketici Kredisi Faizi", "birim": "%",
     "siklik": "aylık", "frekans": 5, "formul": 0,
     "min": 0.0, "max": 300.0, "gun": 400,
     "grup": r"kredi.*faiz|faiz.*kredi|credit.*rate|loan",
     "seri": r"t[u]ketici|consumer",
     "tercih": r"t[u]ketici|consumer", "disi": r"kart|card|ticari|commercial"},
    {"id": "credit_growth_yoy", "ad": "Kredi Büyümesi (yıllık)", "birim": "%",
     "siklik": "aylık", "frekans": 5, "formul": 3,
     "min": -100.0, "max": 500.0, "gun": 400,
     "grup": r"kredi|credit|loan", "seri": r"6\.\s*toplam|toplam krediler|"
                                           r"total loans|total credit",
     "tercih": None, "disi": r"kart|card|mevduat|deposit"},
    {"id": "m3_yoy", "ad": "M3 Para Arzı (yıllık)", "birim": "%",
     "siklik": "aylık", "frekans": 5, "formul": 3,
     "min": -100.0, "max": 500.0, "gun": 400,
     "grup": r"para arz|money supply|paranin", "seri": r"\bm3\b",
     "tercih": None, "disi": None},
    {"id": "reer", "ad": "Reel Efektif Döviz Kuru", "birim": "endeks",
     "siklik": "aylık", "frekans": 5, "formul": 0,
     "min": 40.0, "max": 250.0, "gun": 400,
     "grup": r"reel efektif|real effective|reer",
     "seri": r"reel efektif|real effective|reer",
     "tercih": r"^t[u]fe bazl", "disi": r"^yi[- ]?ufe|nominal"},
    # 2026-11-04 (kullanıcı kararı: TR'de birincil kaynak TÜİK/TCMB resmi
    # kanallar, TradingView yedek): TÜFE genel endeksi (TÜİK verisi, TCMB
    # EVDS hattı) formulas=3 ile yıllık % değişim döndürür. Kod + formül
    # ikili kanıtla doğrulandı: borsapy README örneğinde TP.FG.J0+yoy_pct
    # Ocak 2026'da 30.65 veriyor (TCMB tablosu 01-2026 = 30.65 birebir);
    # repo içi m3_yoy/credit_growth_yoy aynı deseni kullanıyor.
    # "ez": 1 = taze olduğunda TV satırının ÜZERİNE yaz (resmi_veri.py
    # deseni); TÜİK SDMX teyitli satır ezilmez (TÜİK resmi birincildir).
    {"id": "inflation_yoy", "ad": "Enflasyon (yıllık)", "birim": "%",
     "siklik": "aylık", "frekans": 5, "formul": 3,
     "min": -20.0, "max": 300.0, "gun": 400,
     "grup": r"t[u]ketici fiyat|tufe|consumer price",
     "seri": r"t[u]ketici fiyat endeksi|j0|0\.genel",
     "tercih": r"genel|j0", "disi": r"mevsim|ar[ıi]nd[ıi]r|sezon",
     "ez": 1},
)

# 24-25.09.2026 canli denetimle (evds-probe2/4) verisi + adi birebir
# dogrulanmis seri kodlari. Kesif bu gostergeler icin ATLANIR: eskiden arsiv
# gruplar (END 2002/2013) ya da yanlis anlamli seri secilebiliyordu.
# 'ad'/'grup' yalnizca log/kaynak basligi icindir; EVDS seriyi kaldirirsa
# _veri_cek hatasi goreli loglanir ve o gosterge yalnizca o gun atlanir.
ELLE_KODLAR = {
    "deposit_rate": {"kod": "TP.TRY.MT06",
                     "ad": "Toplam (TL Mevduat, Akım, %)",
                     "grup": "Mevduat Faiz Oranları (Akım)"},
    "consumer_loan_rate": {"kod": "TP.FKF.TRY.15",
                           "ad": "Tüketici Kredisi (TL, Stok, %)",
                           "grup": "Finansman Şirketleri Kredi Faiz Oranları "
                                   "(Stok)"},
    "credit_growth_yoy": {"kod": "TP.KM.B33",
                          "ad": "6.TOPLAM",
                          "grup": "Krediler - Mevduat Bankaları"},
    "m3_yoy": {"kod": "TP.HPBITABLO1.18",
               "ad": "3.M3",
               "grup": "Para Arzı (haftalık)"},
    "reer": {"kod": "TP.RK.T1.Y",
             "ad": "TÜFE Bazlı Reel Efektif Döviz Kuru (2025=100)",
             "grup": "Reel Efektif Döviz Kuru - TÜFE Bazlı"},
    "inflation_yoy": {"kod": "TP.FG.J0",
                      "ad": "Tüketici Fiyat Endeksi (Genel) (2025=100)",
                      "grup": "TÜKETİCİ FİYAT ENDEKSİ (TÜİK)"},
}


class EvdsHata(Exception):
    """EVDS adimi basarisiz (anahtar/kesif/veri). Asla snapshot'i dusurmez."""

    def __init__(self, mesaj, durum="veri"):
        super().__init__(mesaj)
        self.durum = durum  # "anahtar" | "kesif" | "veri"


def anahtar():
    """EVDS API anahtari; yoksa None."""
    return str(os.environ.get("EVDS_API_KEY") or "").strip() or None


def _duz(yazi):
    """Eslesme icin sadelestirilmis yazim: 'yıllık' -> 'yillik'."""
    s = str(yazi).casefold()
    for a, b in (("ı", "i"), ("i̇", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"),
                 ("ö", "o"), ("ç", "c"), ("â", "a"), ("î", "i"), ("û", "u")):
        s = s.replace(a, b)
    return s


def _istek(url, parametreler=None, anahtar_deger=None):
    """EVDS'e GET atar, JSON dondurur. 401/403 -> durum='anahtar'."""
    k = anahtar_deger or anahtar()
    if not k:
        raise EvdsHata("EVDS_API_KEY yok", durum="anahtar")
    # EVDS3 parametreleri '?' ile degil YOLUN ICINE yazar (24-09-2026 canli
    # denetim: '?'-li istek 400 "Missing parameters", '?'-siz istek 200).
    # Ornek: .../datagroups/mode=0&code=&type=json (PyPI 'evds' paketi de
    # boyle cagirir; sadece 'key' header'i gonderilir).
    sorgu = urllib.parse.urlencode(parametreler or {})
    tam = (url + sorgu) if sorgu else url
    req = urllib.request.Request(tam, headers={"key": k})
    try:
        with urllib.request.urlopen(req, timeout=30) as yanit:
            ham = yanit.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise EvdsHata(f"EVDS anahtari gecersiz (HTTP {exc.code})",
                           durum="anahtar") from exc
        # Govde + istek URL'si (anahtar URL'de degil, header'dadir) loglansin:
        # CI'da 400'un sebebi sonraki calismada gorunur olsun.
        try:
            govde = exc.read()[:200].decode("utf-8", "replace").replace("\n", " ")
        except Exception:
            govde = ""
        raise EvdsHata(f"EVDS HTTP {exc.code}: {govde} | istek: {tam}",
                       durum="veri") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EvdsHata(f"EVDS baglanti hatasi: {exc}", durum="veri") from exc
    try:
        return json.loads(ham)
    except ValueError as exc:
        raise EvdsHata("EVDS yaniti JSON degil", durum="veri") from exc


def _kod_onbellegi():
    try:
        with KOD_DOSYA.open(encoding="utf-8") as f:
            veri = json.load(f)
        return veri if isinstance(veri, dict) else {}
    except (OSError, ValueError):
        return {}


def _kod_yaz(veri):
    try:
        KOD_DOSYA.parent.mkdir(parents=True, exist_ok=True)
        with KOD_DOSYA.open("w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=1)
    except OSError as exc:
        logger.warning("[EVDS] kod onbellegi yazilamadi: %s", exc)


def _kayit_adlari(kayit, onek):
    adlar = []
    for alan in (onek + "_ENG", onek + "_TR", onek):
        deger = kayit.get(alan) if isinstance(kayit, dict) else None
        if deger:
            adlar.append(str(deger))
    return adlar


def _esi(adlar, desen):
    return any(re.search(desen, _duz(a)) for a in adlar)


def _grup_listesi():
    veri = _istek(KOK + "datagroups/",
                  {"mode": 0, "code": "", "type": "json"})
    if not isinstance(veri, list):
        raise EvdsHata("EVDS datagroups yaniti liste degil", durum="kesif")
    return [g for g in veri if isinstance(g, dict) and g.get("DATAGROUP_CODE")]


def _seri_listesi(grup_kodu):
    veri = _istek(KOK + "serieList/", {"type": "json", "code": grup_kodu})
    if not isinstance(veri, list):
        return []
    return [s for s in veri if isinstance(s, dict) and s.get("SERIE_CODE")]


def _son_tarih(deger):
    """'dd-mm-yyyy' tarihini date'e cevirir; okunamazsa None."""
    try:
        return datetime.strptime(str(deger or "")[:10], "%d-%m-%Y").date()
    except ValueError:
        return None


def _canli_mi(kayit, azami_gun=550):
    """Arsiv/bayat grup-seri adayini ele; tarihi olmayan kayitlar birakilir.

    EVDS 'datagroups' listesinde arsiv gruplar (END 2002/2013) ve seride
    bitmis seriler ('(Arsiv)', END 2024) taze adaylari kalabaliklastiriyordu
    (25-09-2026 denetim: reer/mevduat kesifi arsiv gruplarda takiliyordu).
    """
    if not isinstance(kayit, dict):
        return False
    metin = _duz(" ".join(_kayit_adlari(kayit, "DATAGROUP_NAME")
                          + _kayit_adlari(kayit, "SERIE_NAME")))
    if "arsiv" in metin or "archive" in metin:
        return False
    son = _son_tarih(kayit.get("END_DATE"))
    if son is None:
        return True
    return (date.today() - son).days <= azami_gun


def _kodu_bul(hedef, gruplar, seri_onbellek=None):
    """Kelime eslemesiyle tek seri kodu kesfeder; belirsizse None."""
    aday_gruplar = [g for g in gruplar
                    if _esi(_kayit_adlari(g, "DATAGROUP_NAME"), hedef["grup"])
                    and _canli_mi(g)]
    # Taze grup once: END_DATE 'dd-mm-yyyy' oldugundan dogru tarihle siralanir
    # (ham/alfabetik sirada 2013'te biten arsiv grubu 2026'dan once geliyordu).
    aday_gruplar.sort(key=lambda g: _son_tarih(g.get("END_DATE")) or date.min,
                      reverse=True)
    belirsiz = []
    for grup in aday_gruplar[:25]:
        grup_kodu = str(grup["DATAGROUP_CODE"])
        if seri_onbellek is not None and grup_kodu in seri_onbellek:
            seriler = seri_onbellek[grup_kodu]
        else:
            try:
                seriler = _seri_listesi(grup_kodu)
            except EvdsHata:
                raise
            if seri_onbellek is not None:
                seri_onbellek[grup_kodu] = seriler
        eslesen = [s for s in seriler
                   if _canli_mi(s)
                   and _esi(_kayit_adlari(s, "SERIE_NAME"), hedef["seri"])
                   and not (hedef.get("disi")
                            and _esi(_kayit_adlari(s, "SERIE_NAME"), hedef["disi"]))]
        if hedef.get("tercih") and len(eslesen) > 1:
            dar = [s for s in eslesen
                   if _esi(_kayit_adlari(s, "SERIE_NAME"), hedef["tercih"])]
            if dar:
                eslesen = dar
        if len(eslesen) == 1:
            secili = eslesen[0]
            return {"kod": str(secili["SERIE_CODE"]),
                    "ad": (_kayit_adlari(secili, "SERIE_NAME") or [""])[0],
                    "grup": (_kayit_adlari(grup, "DATAGROUP_NAME") or [""])[0]}
        belirsiz.extend(eslesen)
    if belirsiz:
        ornekler = [(_kayit_adlari(s, "SERIE_NAME") or ["?"])[0] for s in belirsiz]
        logger.warning("[EVDS] %s icin tek seri secilemedi; kod elle "
                       "sabitlenmeli (adaylar: %s)",
                       hedef["id"], ", ".join(ornekler[:5]))
    else:
        logger.warning("[EVDS] %s icin eslesen seri/grup bulunamadi "
                       "(desen: grup=%r seri=%r)",
                       hedef["id"], hedef["grup"], hedef["seri"])
    return None


def _tarih_yaz(deger):
    """EVDS tarih alanini ('Tarih'/'DATE') 'YYYY-MM-DD'ye cevirir; okunamazsa None.

    Biçimler: gunluk 'gg-aa-yyyy' / 'yyyy-aa-gg', aylik seri 'yyyy-aa'
    (25-09-2026 denetim: frequency=5 yaniti 'Tarih':'2025-8' doner - bu bicim
    islenmezse TUM satirlar duser ve 'gecerli gozlem yok' hatasi uretilir),
    yillik seri 'yyyy'.
    """
    yazi = str(deger or "").strip()
    for bicim, duz in ((r"^\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
                       (r"^\d{4}-\d{2}-\d{2}", "%Y-%m-%d")):
        if re.match(bicim, yazi):
            try:
                return datetime.strptime(yazi[:10], duz).date().isoformat()
            except ValueError:
                return None
    ay = re.match(r"^(\d{4})-(\d{1,2})$", yazi)
    if ay:
        try:
            return date(int(ay.group(1)), int(ay.group(2)), 1).isoformat()
        except ValueError:
            return None
    if re.match(r"^\d{4}$", yazi):
        return f"{yazi}-01-01"
    return None


def _sayi(deger):
    if deger in (None, ""):
        return None
    try:
        return float(deger)
    except (TypeError, ValueError):
        try:
            return float(str(deger).replace(",", "."))
        except (TypeError, ValueError):
            return None


class _Kesif:
    """Seri kodlarini bir kez kesfeder, sonucu onbellege yazar."""

    def __init__(self):
        self.onbellek = _kod_onbellegi()
        self._gruplar = None
        self._seri_onbellek = {}

    def gruplar(self):
        if self._gruplar is None:
            self._gruplar = _grup_listesi()
        return self._gruplar

    def kod(self, hedef):
        elle = ELLE_KODLAR.get(hedef["id"])
        if elle:
            return str(elle["kod"]), str(elle.get("ad") or "")
        kayit = self.onbellek.get(hedef["id"])
        if isinstance(kayit, dict) and str(kayit.get("kod", "")).strip():
            return str(kayit["kod"]), str(kayit.get("ad") or "")
        bulunan = _kodu_bul(hedef, self.gruplar(), self._seri_onbellek)
        if not bulunan:
            raise EvdsHata(f"{hedef['id']}: seri kodu kesfedilemedi",
                           durum="kesif")
        self.onbellek[hedef["id"]] = bulunan
        _kod_yaz(self.onbellek)
        return bulunan["kod"], bulunan["ad"]


def _kolon_eslesir(k, beklenen):
    """'TP_KM_B33' ve formul son ekli 'TP_KM_B33-3' kolonlarini kabul eder.

    EVDS, formulas=3 isteginde kolon adina '-3' ekler; e kabul edilmezse
    dogrulama hep basarisiz olurdu (25-09-2026 denetimde goruldu).
    """
    temiz = str(k).replace(".", "_")
    return temiz in beklenen or temiz.split("-")[0] in beklenen


def _veri_cek(hedef, kod, bitis_tarih):
    """Serinin son iki gozlemini (deger, onceki, donem) dondurur."""
    bitis = datetime.strptime(str(bitis_tarih)[:10], "%Y-%m-%d")
    bas_gun = bitis - timedelta(days=int(hedef["gun"]))
    # Kullanim kilavuzu: istenen frekansin ilk gunu yazilmali (aylikta ayin
    # 1'i); hizalamazsan ilk donem eksik gelir.
    if hedef["frekans"] == 5:
        bas_gun = bas_gun.replace(day=1)
    elif hedef["frekans"] == 6:
        bas_gun = bas_gun.replace(month=((bas_gun.month - 1) // 3) * 3 + 1,
                                  day=1)
    elif hedef["frekans"] == 8:
        bas_gun = bas_gun.replace(month=1, day=1)
    # Bos 'formulas'/'aggregationTypes' de gonderilir (paketle ayni bicim).
    parametreler = {"series": kod, "startDate": bas_gun.strftime("%d-%m-%Y"),
                    "endDate": bitis.strftime("%d-%m-%Y"), "type": "json",
                    "frequency": str(hedef["frekans"]),
                    "formulas": str(hedef.get("formul") or ""),
                    "aggregationTypes": ""}
    veri = _istek(KOK, parametreler)
    items = veri.get("items") if isinstance(veri, dict) else None
    if not isinstance(items, list) or not items:
        raise EvdsHata(f"{hedef['id']}: EVDS gozlem listesi bos")
    # Kolon anahtari anahtar-kelime dogrulamasi: kod sutunu yoksa seri yanlis.
    beklenen = {kod.replace(".", "_"), kod}
    gozlemler = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kolon = next((k for k in it if _kolon_eslesir(k, beklenen)), None)
        if kolon is None:
            continue
        # EVDS3 yanitinda tarih anahtari 'Tarih' (bazen 'DATE').
        tarih = _tarih_yaz(it.get("Tarih") or it.get("DATE"))
        deger = _sayi(it.get(kolon))
        if tarih and deger is not None:
            gozlemler.append((tarih, deger))
    if not gozlemler:
        raise EvdsHata(f"{hedef['id']}: {kod} icin gecerli gozlem yok "
                       "(kolon eslesmedi ya da tum degerler bos)")
    gozlemler.sort()
    donem, deger = gozlemler[-1]
    if not (float(hedef["min"]) <= deger <= float(hedef["max"])):
        raise EvdsHata(f"{hedef['id']}: {deger} degeri aralik disi "
                       f"[{hedef['min']}, {hedef['max']}]")
    onceki = gozlemler[-2][1] if len(gozlemler) > 1 else None
    return deger, onceki, donem


_AY_KISALTMA = {"jan": "01", "feb": "02", "mar": "03", "apr": "04",
                "may": "05", "jun": "06", "jul": "07", "aug": "08",
                "sep": "09", "oct": "10", "nov": "11", "dec": "12",
                "oca": "01", "şub": "02", "sub": "02", "nis": "04",
                "haz": "06", "tem": "07", "ağu": "08", "agu": "08",
                "eyl": "09", "eki": "10", "kas": "11", "ara": "12"}


def _yil_ay(metin):
    """'2026-10-05' / 'Sep. 2026' / 'Eyl. 2026' tarzi donemleri YYYY-AA'ya cevirir."""
    if not metin:
        return ""
    s = str(metin).strip()
    m = re.match(r"^(\d{4})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"^([^\d\s.]{3})[^\s]*\.?\s+(\d{4})$", s)
    if m:
        return f"{m.group(2)}-{_AY_KISALTMA.get(m.group(1).lower()[:3], '')}"
    return ""


def _evds_ez_meli(mevcut, evds_donem):
    """EVDS, mevcut (TV) satiri ezebilir mi? (2026-11-04, resmi_veri deseni)

    Birincil kaynak sirasi: TÜİK SDMX > TCMB EVDS > TradingView.
    - TÜİK teyitli satır ASLA ezilmez.
    - EVDS veri dönemi TV'nin veri döneminden (kaynak_periyot; bozuksa
      donem) GERİDE ise TV taze kalır; eşit/ileri ise EVDS kazanır
      (resmi kanal eşitlikte tutarlılık sağlar).
    """
    if str(mevcut.get("teyit") or "") == "TÜİK":
        return False
    evds_ay = _yil_ay(evds_donem)
    if not evds_ay:
        return False
    veri_ay = (_yil_ay(mevcut.get("kaynak_periyot"))
               or _yil_ay(mevcut.get("donem")))
    if not veri_ay:
        return False
    return evds_ay >= veri_ay


def gostergeleri_ekle(gostergeler, tarih):
    """EVDS makro gostergelerini ekler; 'ez' hedefleri taze olduklarinda
    mevcut (TV) satirin UZERINE yazar. Anahtar/kesif yoksa 0 dondurur."""
    if not anahtar():
        logger.info("[EVDS] EVDS_API_KEY yok; makro EVDS adimlari atlandi.")
        return 0
    kesif = _Kesif()
    eklenen = 0
    for hedef in MACRO_HEDEFLERI:
        mevcut = next((g for g in gostergeler
                       if str(g.get("ulke")) == "Türkiye"
                       and str(g.get("ad")) == hedef["ad"]), None)
        if mevcut is not None and not hedef.get("ez"):
            continue
        try:
            kod, seri_adi = kesif.kod(hedef)
            deger, onceki, donem = _veri_cek(hedef, kod, tarih)
        except EvdsHata as exc:
            logger.warning("[EVDS] %s atlandi: %s", hedef["id"], exc)
            if exc.durum == "anahtar":
                break
            continue
        except Exception as exc:  # pragma: no cover - guvenlik agi
            logger.warning("[EVDS] %s beklenmeyen hata: %s", hedef["id"], exc)
            continue
        eski = mevcut.get("deger") if mevcut is not None else None
        if mevcut is not None:
            if not _evds_ez_meli(mevcut, donem):
                continue
            mevcut.update({
                "deger": round(deger, 4), "birim": hedef["birim"],
                "donem": donem, "frekans": hedef["siklik"],
                "onceki": round(onceki, 4) if onceki is not None else None,
                "kaynak_baslik": f"TCMB EVDS {kod} ({seri_adi})",
                "kaynak_periyot": donem,
                "kaynak_durumu": "TCMB EVDS (akşam snapshot)",
                "teyit": "TCMB EVDS",
            })
            if eski is not None and abs(float(eski) - deger) > 0.0005:
                logger.warning("[EVDS] %s farkı: TV %s / EVDS %s (%s) -> EVDS",
                               hedef["id"], eski, deger, donem)
            eklenen += 1
            continue
        gostergeler.append({
            "ulke": "Türkiye", "ad": hedef["ad"], "deger": round(deger, 4),
            "birim": hedef["birim"], "donem": donem,
            "frekans": hedef["siklik"],
            "onceki": round(onceki, 4) if onceki is not None else None,
            "kaynak_baslik": f"TCMB EVDS {kod} ({seri_adi})",
            "kaynak_periyot": donem,
            "kaynak_durumu": "TCMB EVDS (akşam snapshot)",
        })
        eklenen += 1
    return eklenen
