# -*- coding: utf-8 -*-
"""TCMB EVDS veri modulu (opsiyonel; anahtar yoksa sessizce atlanir).

`EVDS_API_KEY` ortam degiskeni yoksa ya da istek basarisizsa hicbir veri
EKLEMEZ; makro snapshot'i TV/TÜİK verisiyle aynen calismaya devam eder.
Anahtar oldugunda iki blogu doldurur:

- Makro (TR): mevduat faizi, tuketici kredisi faizi, kredi buyumesi,
  M3 para yillik buyumesi, reel efektif doviz kuru.
- Piyasa (TR): 2 ve 10 yillik tahvil getirileri.

Seri kodlari ilk calismada `datagroups` + `serieList` uzerinden kelime
eslemeyle kesfedilir ve `data/evds-kodlar.json` dosyasinda sabitlenir;
sonraki calismalarda dogrudan o kod kullanilir. Kelime eslemesi sonucu
belirsizse o gosterge ATLANIR (uygun seri uydurulmaz) ve adaylar loglanir.

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
from datetime import datetime, timedelta
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
     "grup": r"kredi|credit|loan", "seri": r"verilen krediler|bank credit|"
                                            r"total credit|krediler",
     "tercih": r"bankalara|bankalari|bank", "disi": r"kart|card|mevduat"},
    {"id": "m3_yoy", "ad": "M3 Para Arzı (yıllık)", "birim": "%",
     "siklik": "aylık", "frekans": 5, "formul": 3,
     "min": -100.0, "max": 500.0, "gun": 400,
     "grup": r"para arz|money supply|paranin", "seri": r"\bm3\b",
     "tercih": None, "disi": None},
    {"id": "reer", "ad": "Reel Efektif Döviz Kuru", "birim": "endeks",
     "siklik": "aylık", "frekans": 5, "formul": 0,
     "min": 40.0, "max": 250.0, "gun": 400,
     "grup": r"endeks|doviz|kurlar|index|exchange",
     "seri": r"reel efektif|real effective|reer", "tercih": None,
     "disi": r"nominal|nominal"},
)

PIYASA_HEDEFLERI = (
    {"id": "tr2y", "label": "TR 2 Yıllık Tahvil", "birim": "%",
     "kategori": "faiz", "frekans": 1, "formul": 0,
     "min": 0.0, "max": 150.0, "gun": 40,
     "grup": r"tahvil|getiri|borclanma|bond|yield",
     "seri": r"(?<!\d)2\s*yil|(?<!\d)2\s*year", "tercih": None, "disi": r"enflasyon"},
    {"id": "tr10y", "label": "TR 10 Yıllık Tahvil", "birim": "%",
     "kategori": "faiz", "frekans": 1, "formul": 0,
     "min": 0.0, "max": 150.0, "gun": 40,
     "grup": r"tahvil|getiri|borclanma|bond|yield",
     "seri": r"(?<!\d)10\s*yil|(?<!\d)10\s*year", "tercih": None, "disi": r"enflasyon"},
)


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


def _kodu_bul(hedef, gruplar):
    """Kelime eslemesiyle tek seri kodu kesfeder; belirsizse None."""
    aday_gruplar = [g for g in gruplar if _esi(_kayit_adlari(g, "DATAGROUP_NAME"),
                                               hedef["grup"])]
    belirsiz = []
    for grup in aday_gruplar[:8]:
        try:
            seriler = _seri_listesi(str(grup["DATAGROUP_CODE"]))
        except EvdsHata:
            raise
        eslesen = [s for s in seriler
                   if _esi(_kayit_adlari(s, "SERIE_NAME"), hedef["seri"])
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
    """EVDS tarih alanini ('Tarih'/'DATE') 'YYYY-MM-DD'ye cevirir; okunamazsa None."""
    yazi = str(deger or "").strip()
    for bicim, duz in ((r"^\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
                       (r"^\d{4}-\d{2}-\d{2}", "%Y-%m-%d")):
        if re.match(bicim, yazi):
            try:
                return datetime.strptime(yazi[:10], duz).date().isoformat()
            except ValueError:
                return None
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

    def gruplar(self):
        if self._gruplar is None:
            self._gruplar = _grup_listesi()
        return self._gruplar

    def kod(self, hedef):
        kayit = self.onbellek.get(hedef["id"])
        if isinstance(kayit, dict) and str(kayit.get("kod", "")).strip():
            return str(kayit["kod"]), str(kayit.get("ad") or "")
        bulunan = _kodu_bul(hedef, self.gruplar())
        if not bulunan:
            raise EvdsHata(f"{hedef['id']}: seri kodu kesfedilemedi",
                           durum="kesif")
        self.onbellek[hedef["id"]] = bulunan
        _kod_yaz(self.onbellek)
        return bulunan["kod"], bulunan["ad"]


def _veri_cek(hedef, kod, bitis_tarih):
    """Serinin son iki gozlemini (deger, onceki, donem) dondurur."""
    bitis = datetime.strptime(str(bitis_tarih)[:10], "%Y-%m-%d")
    bas = (bitis - timedelta(days=int(hedef["gun"]))).strftime("%d-%m-%Y")
    # Bos 'formulas'/'aggregationTypes' de gonderilir (paketle ayni bicim).
    parametreler = {"series": kod, "startDate": bas,
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
        kolon = next((k for k in it
                      if str(k).replace(".", "_") in beklenen), None)
        if kolon is None:
            continue
        # EVDS3 yanitinda tarih anahtari 'Tarih' (bazen 'DATE').
        tarih = _tarih_yaz(it.get("Tarih") or it.get("DATE"))
        deger = _sayi(it.get(kolon))
        if tarih and deger is not None:
            gozlemler.append((tarih, deger))
    if not gozlemler:
        raise EvdsHata(f"{hedef['id']}: {kod} icin sonlu gozlem yok "
                       "(sutun/kelime dogrulamasi basarisiz")
    gozlemler.sort()
    donem, deger = gozlemler[-1]
    if not (float(hedef["min"]) <= deger <= float(hedef["max"])):
        raise EvdsHata(f"{hedef['id']}: {deger} degeri aralik disi "
                       f"[{hedef['min']}, {hedef['max']}]")
    onceki = gozlemler[-2][1] if len(gozlemler) > 1 else None
    return deger, onceki, donem


def gostergeleri_ekle(gostergeler, tarih):
    """EVDS makro gostergelerini listeye ekler; anahtar/kesif yoksa 0 dondurur."""
    if not anahtar():
        logger.info("[EVDS] EVDS_API_KEY yok; makro EVDS adimlari atlandi.")
        return 0
    mevcut = {(str(g.get("ulke")), str(g.get("ad"))) for g in gostergeler}
    kesif = _Kesif()
    eklenen = 0
    for hedef in MACRO_HEDEFLERI:
        if ("Türkiye", hedef["ad"]) in mevcut:
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


def piyasa_kayitlari(snapshot_date):
    """EVDS TR tahvil getirileri; anahtar/kesif yoksa bos liste."""
    if not anahtar():
        logger.info("[EVDS] EVDS_API_KEY yok; piyasa EVDS adimlari atlandi.")
        return []
    kesif = _Kesif()
    kayitlar = []
    for hedef in PIYASA_HEDEFLERI:
        try:
            kod, seri_adi = kesif.kod(hedef)
            deger, onceki, donem = _veri_cek(hedef, kod, snapshot_date)
        except EvdsHata as exc:
            logger.warning("[EVDS] %s atlandi: %s", hedef["id"], exc)
            if exc.durum == "anahtar":
                break
            continue
        except Exception as exc:  # pragma: no cover - guvenlik agi
            logger.warning("[EVDS] %s beklenmeyen hata: %s", hedef["id"], exc)
            continue
        kayitlar.append({
            "symbol": f"EVDS:{kod}", "indicator": hedef["id"],
            "label": hedef["label"], "value": round(deger, 4),
            "previous": round(onceki, 4) if onceki is not None else None,
            "change": round(deger - onceki, 4) if onceki is not None else None,
            "unit": hedef["birim"], "period": donem,
            "category": hedef["kategori"],
            "source": f"TCMB EVDS ({seri_adi})", "status": "close",
        })
    return kayitlar
