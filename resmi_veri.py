# -*- coding: utf-8 -*-
"""Resmi veri kanallari: AB (Eurostat/ECB) ve ABD (BLS/FRED/BEA).

Aksam makro snapshot'inin TR disindaki resmi katmani. Bu modul uc is yapar:

1. Zorunlu/yardimci gostergelerin resmi degerlerini ceker.
2. Taze olduklari surede TradingView satirlarinin UZERINE yazar (TV satiri
   yoksa resmi satiri listeye ekler). Boylece `frame_text` ile `rate_maps`
   ayni snapshot nesnesinden beslenir ve resmi deger otomatik olarak cerceveyi
   de dogrulamayi da belirler.
3. Ayrica `data/resmi-veri.json` + `data/resmi-gecmis/<tarih>.json` olarak
   AYRI bir resmi veri tabanina kaydeder (EU + AB tum satirlari, TR'de
   yalnizca TUİK/EVDS onayli satirlar).

Tasarim kurallari:
  * Her hedef bagimsizdir. Anahtar yoksa, ag/HTTP hatasi olursa ya da deger
    aralik disinda ise yalnizca o hedef atlanir; snapshot yazilmaya devam eder.
  * Tazelik kapagi: resmi veri, veri donemi TV satirinin VERI doneminden
    (yayin tarihi degil) geride degilse ve ornek gunune gore bayatlamamis
    ise TV satirini ezer. Boylece bayat Eurostat kumesi taze TV satirini
    ezemez.
  * API anahtarlari hicbir log/mesaj/yaziya yazilmaz; URL'ler de yazilmaz
    (BLS/FRED/BEA anahtarlari sorgu govdesinde/yolunda tasinabilir).
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from makro_katalog import KOD_GOSTERGE

logger = logging.getLogger("resmi-veri")

MAGAZA_YOL = Path("data/resmi-veri.json")
ARSIV_DIZIN = Path("data/resmi-gecmis")

# Makro katalog/ulke adlarindan snapshot country_code karsiligi.
ULKE_KOD = {"Türkiye": "TR", "ABD": "US", "Euro Bölgesi": "EU"}

EUROSTAT_KOK = ("https://ec.europa.eu/eurostat/api/dissemination/"
                "statistics/1.0/data/")
ECB_KOK = "https://data-api.ecb.europa.eu/service/data/"
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
BEA_KOK = "https://apps.bea.gov/api/data/"

# BEA ITA cari denge indikator kodu. Degeri yoksa hedef kesif asamasinda
# sayilir ve sessizce atlanir; kod saglik kontrolu (resmi-check.yml) kesfi ile
# buraya yazilir ya da BEA_CARI_INDICATOR ortam degiskeniyle verilir.
CARI_GOSTERGE = ""

# Birim gosterge bayat sayilir (ornek gunune gore, gun). TV satiri esittir
# YAYIN tarihi, resmi veri esittir VERI donemi; bu yuzden ikinci kontrol
# (_taze_mi icinde) TV'nin kaynak_periyot'uyla yapilir.
AZAMI_GUN = {"günlük": 15, "haftalık": 30, "olay": 45,
             "aylık": 120, "üç aylık": 240, "yıllık": 500}

AY_KISA = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

MAGAZA_ALANLARI = ("country_code", "country", "indicator", "label", "value",
                   "unit", "period", "frequency", "forecast", "previous",
                   "source_title", "source_period", "provenance",
                   "confirmed_by")

# Gecici sunucu yogunlugunda tekrar denenecek HTTP kodlari (BLS 503 vb.).
TEKRAR_KODLARI = (429, 500, 502, 503, 504)
TEKRAR_BEKLEME = 3.0  # testler 0'a cekebilir

# Varsayilan istek basligi. Bazi AB uclari (Eurostat) kendini tanimayan
# Python-urllib UA'siyla gelen istegi govde yazmadan kapatir; o zaman hata
# "Remote end closed connection without response" olur ve durum="ag".
# Cagiran kendi basligini gecerirse onunki kazanir.
VARSAYILAN_BASLIK = {"User-Agent": "borsa-raporlari/1.0 (resmi-veri)"}

_ONBELLEK = {}    # pin anahari -> [(donem, ham deger)]
_PIN_HATA = {}    # pin anahari -> ResmiHata (ayni calismada tekrar deneme yok)


class ResmiHata(RuntimeError):
    """Resmi kanal istegi/cevabi gecerli degil; o hedef atlanir."""

    def __init__(self, mesaj, durum="veri"):
        super().__init__(mesaj)
        self.durum = durum


def sifirla():
    """Onbellek ve hata defterini temizler (testler/tekrar deneme icin)."""
    _ONBELLEK.clear()
    _PIN_HATA.clear()


def _temizle(metin, anahtar=None):
    """Anahtari hicbir ciktiya yazmamak icin govde/urduden temizler."""
    metin = metin if isinstance(metin, str) else str(metin)
    k = str(anahtar or "")
    if len(k) >= 6:
        metin = metin.replace(k, "***")
    return metin


def _govde_kis(metin, anahtar=None):
    """Yanit govdesini kisaltir; HTML hata sayfalari logu kirletmesin."""
    metin = _temizle(metin, anahtar)
    metin = re.sub(r"\s+", " ", metin).strip()
    if metin[:15].lower().startswith(("<!doctype", "<html")):
        return "HTML yanit (sunucu mesgul/engelli olabilir)"
    return metin[:160]


def _istek(url, anahtar=None, veri=None, basliklar=None, etiket="resmi",
           timeout=25, tekrar=1):
    """HTTP istegi; govde metni dondurur. URL ve anahtar hic loglanmaz."""
    nihai = dict(VARSAYILAN_BASLIK)
    nihai.update(basliklar or {})
    for deneme in range(tekrar + 1):
        req = urllib.request.Request(url, data=veri, headers=nihai)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as yanit:
                ham = yanit.read()
        except urllib.error.HTTPError as exc:
            if exc.code in TEKRAR_KODLARI and deneme < tekrar:
                time.sleep(TEKRAR_BEKLEME)
                continue
            try:
                govde = exc.read()[:400].decode("utf-8", "replace")
            except Exception:  # pragma: no cover - guvenlik agi
                govde = ""
            durum = "anahtar" if exc.code in (401, 403) else "veri"
            raise ResmiHata(f"{etiket} HTTP {exc.code}: "
                            f"{_govde_kis(govde, anahtar)}",
                            durum=durum) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if deneme < tekrar:
                time.sleep(TEKRAR_BEKLEME)
                continue
            sebep = getattr(exc, "reason", exc)
            raise ResmiHata(f"{etiket} istek hatasi: "
                            f"{_govde_kis(sebep, anahtar)}",
                            durum="ag") from None
        metin = ham.decode("utf-8", "replace") if isinstance(ham, bytes) \
            else str(ham)
        return _temizle(metin, anahtar)
    raise ResmiHata(f"{etiket} istek yapilamadi", durum="ag")  # pragma: no cover


# ---------------------------------------------------------------- donemler


def _ay_sonu(yil, ay):
    return date(yil, ay, calendar.monthrange(yil, ay)[1])


def _ay_ekle(tarih, adet):
    yil = tarih.year + (tarih.month - 1 + adet) // 12
    ay = (tarih.month - 1 + adet) % 12 + 1
    return date(yil, ay, min(tarih.day, calendar.monthrange(yil, ay)[1]))


def _ceyrek_donemi(tarih):
    """FRED/BLS ceyrek-basi tarihini ceyrek sonuna cevirir (veri donemi)."""
    return _ay_sonu(tarih.year, ((tarih.month - 1) // 3 + 1) * 3)


def _donem_cevir(kod, frekans):
    """Kaynak donem kodunu snapshot `period` bicimine (YYYY-AA-GG) cevirir.

    * ``2026-Q2`` / ``Q2 2026`` -> ceyrek sonu (2026-06-30)
    * ``2026-07`` / ``2026-7``   -> ayin ilki  (2026-07-01)
    * ``2026``                   -> yilin ilki (2026-01-01)
    * ``2026-04-01``             -> aynen; uctest aylik seri icin ceyrek sonu
    """
    metin = str(kod or "").strip()
    if not metin:
        return None
    m = re.fullmatch(r"(\d{4})-Q([1-4])", metin, re.IGNORECASE)
    if m:
        return _ay_sonu(int(m.group(1)), int(m.group(2)) * 3)
    m = re.fullmatch(r"[Qq]([1-4])\s+(\d{4})", metin)
    if m:
        return _ay_sonu(int(m.group(2)), int(m.group(1)) * 3)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", metin)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    if re.fullmatch(r"\d{4}", metin):
        return date(int(metin), 1, 1)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", metin)
    if m:
        try:
            tarih = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return _ceyrek_donemi(tarih) if frekans == "üç aylık" else tarih
    return None


def _tv_veri_donemi(donem, kaynak_periyot):
    """TV satirinin VERI donemini bulur (donem = yayin tarihidir).

    ``kaynak_periyot`` TV'nin veri donemi etiketidir: "Aug", "Q2", "Sep/12",
    "2025" ya da bos. Cevrilemiyorsa None doner ve tazelik kapagi yalnizca
    ornek yasina bakar.
    """
    metin = str(kaynak_periyot or "").strip()
    if not metin:
        return None
    yayin = _donem_cevir(donem, "aylık")
    if yayin is None:
        return None
    yil = yayin.year
    m = re.fullmatch(r"(?:(\d{4})-)?Q([1-4])", metin, re.IGNORECASE)
    if m:
        hedef_yil = int(m.group(1)) if m.group(1) else yil
        ay = int(m.group(2)) * 3
        if ay > yayin.month:
            hedef_yil -= 1
        return _ay_sonu(hedef_yil, ay)
    if re.fullmatch(r"\d{4}", metin):
        return date(int(metin), 1, 1)
    parca = [p.strip() for p in metin.split("/")]
    ay = AY_KISA.get(parca[0].lower()[:3])
    if not ay:
        return None
    if ay > yayin.month:
        yil -= 1
    gun = 1
    if len(parca) > 1 and parca[1].isdigit() and 1 <= int(parca[1]) <= 31:
        gun = int(parca[1])
    return date(yil, ay, min(gun, calendar.monthrange(yil, ay)[1]))


def _taze_mi(resmi_donem, tv_donem, tv_periyot, tarih, frekans):
    """Resmi veri TV satirini ezme hakkini kazaniyor mu?"""
    try:
        rd = datetime.strptime(str(resmi_donem)[:10], "%Y-%m-%d").date()
        hd = datetime.strptime(str(tarih)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    if rd > hd:  # gelecege ait donem guvenilir degil
        return False
    if (hd - rd).days > AZAMI_GUN.get(frekans, 120):
        return False
    tvp = _tv_veri_donemi(tv_donem, tv_periyot)
    if tvp is not None and rd < tvp:
        return False
    return True


# ------------------------------------------------------------------ seriler


def _uret(gozlemler, hesap):
    """[(donem, deger)] artan seriyi istenen olcumden yeni seriye cevirir."""
    if not gozlemler:
        return []
    gozlemler = sorted(gozlemler, key=lambda x: x[0])
    if hesap == "son":
        return list(gozlemler)
    if hesap == "fark":
        return [(gozlemler[i][0], gozlemler[i][1] - gozlemler[i - 1][1])
                for i in range(1, len(gozlemler))]
    if hesap == "aylik":
        cikti = []
        for i in range(1, len(gozlemler)):
            d, v = gozlemler[i]
            o_d, o = gozlemler[i - 1]
            if o_d != _ay_ekle(d, -1) or not o:
                continue
            cikti.append((d, (v / o - 1) * 100))
        return cikti
    if hesap == "yillik":
        esleme = dict(gozlemler)
        cikti = []
        for d, v in gozlemler:
            o = esleme.get(_ay_ekle(d, -12))
            if o is None or not o:
                continue
            cikti.append((d, (v / o - 1) * 100))
        return cikti
    raise ResmiHata(f"bilinmeyen olcum: {hesap}")


def _olcekla(deger, olcek):
    if not olcek:
        return deger
    if olcek == "milyar":
        while abs(deger) > 10000:  # BEA milyon/milyar/dolar birim belirsizligi
            deger /= 1000.0
        return deger
    return deger * float(olcek)


def _jsonstat(cevap, sabit, frekans):
    """Eurostat JSON-stat yanitini [(donem, deger)] serisine cevirir."""
    ids = cevap.get("id") or []
    boyutlar = cevap.get("size") or []
    if not ids or len(ids) != len(boyutlar):
        raise ResmiHata("JSON-stat yapisi beklenen gibi degil")
    dims = cevap.get("dimension") or {}
    kolon = []
    zaman_i = None
    for i, dim in enumerate(ids):
        kategori = dims.get(dim) or {}
        indeks = _kat_index(kategori.get("category") or {})
        if not indeks:
            raise ResmiHata(f"JSON-stat boyutu bos: {dim}")
        if dim == "time":
            zaman_i = i
            sirali = sorted(indeks.items(), key=lambda kv: kv[1])
            kolon.append(sirali)
            continue
        kod = sabit.get(dim)
        if kod is None:
            if len(indeks) == 1:  # tek kategorili boyut (freq vb.)
                kod = next(iter(indeks))
            else:
                # Filtre unutulmussa yanlis seri secilirdi; yuzukurugu.
                raise ResmiHata(f"JSON-stat boyutu filtresiz: {dim}")
        if kod not in indeks:
            raise ResmiHata(f"JSON-stat degeri yok: {dim}={kod}")
        kolon.append([(kod, indeks[kod])])
    if zaman_i is None:
        raise ResmiHata("JSON-stat 'time' boyutu yok")
    adim = [1] * len(boyutlar)
    for i in range(len(boyutlar) - 2, -1, -1):
        adim[i] = adim[i + 1] * boyutlar[i + 1]
    degerler = cevap.get("value")
    gozlemler = []
    for kod, k_pos in kolon[zaman_i]:
        duz = k_pos * adim[zaman_i]
        for i, kayit in enumerate(kolon):
            if i == zaman_i:
                continue
            duz += kayit[0][1] * adim[i]
        if isinstance(degerler, dict):
            # Eurostat JSON-object anahtarlari metindir; bazi ureticiler
            # tam sayi anahtar uretir, ikisi de kabul edilir.
            ham = degerler.get(str(duz))
            if ham is None:
                ham = degerler.get(duz)
        elif isinstance(degerler, list):
            ham = degerler[duz] if duz < len(degerler) else None
        else:
            ham = None
        if ham in (None, "", "."):
            continue
        try:
            sayi = float(ham)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(sayi):
            continue
        tarih = _donem_cevir(kod, frekans)
        if tarih:
            gozlemler.append((tarih, sayi))
    if not gozlemler:
        raise ResmiHata("JSON-stat serisi bos (tum gozlemler bos)")
    return gozlemler


def _kat_index(kategori):
    indeks = kategori.get("index")
    if isinstance(indeks, dict):
        return {str(k): v for k, v in indeks.items()}
    if isinstance(indeks, list):
        return {str(k): i for i, k in enumerate(indeks)}
    return {}


def _eurostat(veri_kumesi, parametreler, frekans):
    sorgu = urllib.parse.urlencode(parametreler or {})
    url = EUROSTAT_KOK + veri_kumesi + ("?" + sorgu if sorgu else "")
    ham = _istek(url, etiket=f"Eurostat {veri_kumesi}")
    if not ham.strip():
        raise ResmiHata(f"Eurostat {veri_kumesi}: yanit bos")
    try:
        cevap = json.loads(ham)
    except ValueError as exc:
        raise ResmiHata(f"Eurostat {veri_kumesi}: JSON degil") from None
    if not isinstance(cevap, dict):
        raise ResmiHata(f"Eurostat {veri_kumesi}: beklenmeyen yanit")
    return _jsonstat(cevap, parametreler or {}, frekans)


def _ecb(seri, frekans):
    url = f"{ECB_KOK}{seri}?format=csvdata&lastNObs=40"
    ham = _istek(url, etiket=f"ECB {seri}")
    satirlar = list(csv.reader(io.StringIO(ham)))
    if len(satirlar) < 2:
        raise ResmiHata(f"ECB {seri}: CSV satiri yok")
    baslik = [str(h).strip() for h in satirlar[0]]
    alt = [h.lower() for h in baslik]
    try:
        i_t, i_v = alt.index("time_period"), alt.index("obs_value")
    except ValueError:
        if len(baslik) < 2:
            raise ResmiHata(f"ECB {seri}: kolonlar taninamadi")
        i_t, i_v = len(baslik) - 2, len(baslik) - 1
    gozlemler = []
    for s in satirlar[1:]:
        if len(s) <= max(i_t, i_v):
            continue
        tarih = _donem_cevir(s[i_t], frekans)
        try:
            deger = float(s[i_v])
        except ValueError:
            continue
        if tarih and math.isfinite(deger):
            gozlemler.append((tarih, deger))
    if not gozlemler:
        raise ResmiHata(f"ECB {seri}: gecerli gozlem yok")
    return gozlemler


def _bls_donemi(yil, donem):
    try:
        y = int(yil)
    except (TypeError, ValueError):
        return None
    m = re.fullmatch(r"M(\d{2})", str(donem or ""))
    if m:
        ay = int(m.group(1))
        return date(y, ay, 1) if 1 <= ay <= 12 else None
    q = re.fullmatch(r"Q(\d)", str(donem or ""))
    if q and 1 <= int(q.group(1)) <= 4:
        return _ay_sonu(y, int(q.group(1)) * 3)
    return None


def _bls_ayikla(govde):
    """BLS yanitini ``{seri: [(donem, ham deger)]}`` haritasina cevirir."""
    if not govde.strip():
        raise ResmiHata("BLS yanit bos")
    try:
        veri = json.loads(govde)
    except ValueError:
        raise ResmiHata("BLS JSON degil") from None
    sonuclar = veri.get("Results") or {}
    # v1 yaniti kucuk harf 'series', v2 yaniti 'Series' dondurur.
    seriler = sonuclar.get("Series") or sonuclar.get("series") or []
    if not seriler:
        raise ResmiHata("BLS yaniti seri icermedi: "
                        f"{veri.get('status') or ''} "
                        f"{veri.get('message') or ''}".strip())
    harita = {}
    for s in seriler:
        gozlemler = []
        for it in s.get("data") or []:
            tarih_d = _bls_donemi(it.get("year"), it.get("period"))
            if tarih_d is None:
                continue
            try:
                deger = float(str(it.get("value")).replace(",", ""))
            except (TypeError, ValueError):
                continue  # '-' (eksik gozlem) ve bos degerler
            if math.isfinite(deger):
                gozlemler.append((tarih_d, deger))
        harita[str(s.get("seriesID") or "")] = gozlemler
    return harita


# BLS kotu gun/limit mesajlari: seri seri denemek de ayni hatayi verir ve kotu
# gunde 25 istek/gunluk kotayi gereksiz yere bitirir; ilk anda vazgecilir.
KOTA_IFADELERI = ("threshold", "quota", "rate limit", "too many",
                  "requests allocated", "maximum number", "daily limit")


def _limit_mi(metin):
    metin = str(metin).lower()
    return any(ifade in metin for ifade in KOTA_IFADELERI)


def _bls_toplu(seriler, yil_bas, yil_son):
    """BLS serilerini mumkun oldugunca TEK istekte ceker (limit/503 korumasi).

    Anahtarli istek (Actions) tek POST ile v2'ye gider; anahtar yoksa ya da v2
    basarisizsa v1'in de destekledigi tek POST'a, o da basarisizsa seri seri
    GET'e inilir. BLS anahtarsiz kotu gunde ~25 istek/gun sinirlar; toplu
    istek bu yuzden zorunludur. Kota/mesgul hatasinda seri seri denenmez.
    """
    kayit = {"seriesid": list(seriler), "startyear": str(yil_bas),
             "endyear": str(yil_son)}
    basliklar = {"Content-Type": "application/json"}
    anahtar = os.environ.get("BLS_API_KEY", "").strip()
    if anahtar:
        govde = json.dumps({**kayit, "registrationkey": anahtar}).encode("utf-8")
        try:
            ham = _istek(BLS_V2, anahtar=anahtar, veri=govde,
                         basliklar=basliklar, etiket="BLS")
            return _bls_ayikla(ham)
        except ResmiHata as exc:
            if _limit_mi(exc):
                raise
            logger.info("[Resmi] BLS v2 atlandi (%s); v1'e donuldu", exc)
    try:
        ham = _istek(BLS_V1, veri=json.dumps(kayit).encode("utf-8"),
                     basliklar=basliklar, etiket="BLS")
        return _bls_ayikla(ham)
    except ResmiHata as exc:
        if _limit_mi(exc):
            raise
        logger.warning("[Resmi] BLS toplu istek atlandi (%s); seri seri "
                       "denenir", exc)
    harita = {}
    for seri in seriler:
        try:
            ham = _istek(f"{BLS_V1}{seri}?startyear={yil_bas}&endyear={yil_son}",
                         etiket=f"BLS {seri}")
            harita.update(_bls_ayikla(ham))
        except ResmiHata as exc:
            logger.warning("[Resmi] BLS %s atlandi: %s", seri, exc)
    if not harita:
        raise ResmiHata("BLS hicbir seri alinamadi", durum="veri")
    return harita


def _bls_doldur(pins, tarih):
    """Verilen BLS pin'lerini tek istekte ceker; onbellege/hata defterine yazar."""
    tarih_d = datetime.strptime(str(tarih)[:10], "%Y-%m-%d").date()
    yil_son = tarih_d.year
    yil_bas = max(yil_son - max(1, max(-(-p["geriye_gun"] // 366)
                                       for p in pins)), yil_son - 9)
    try:
        harita = _bls_toplu(list(dict.fromkeys(p["seri"] for p in pins)),
                            yil_bas, yil_son)
    except ResmiHata as exc:
        for p in pins:
            _PIN_HATA[_pin_anahari(p, tarih)] = exc
        return
    for p in pins:
        anahar = _pin_anahari(p, tarih)
        gozlemler = harita.get(p["seri"]) or []
        if gozlemler:
            _ONBELLEK[anahar] = gozlemler
        else:
            _PIN_HATA[anahar] = ResmiHata(
                f"BLS {p['seri']}: yanit serisi bos", durum="veri")


def _fred_api(seri, baslangic, anahtar, frekans):
    sorgu = urllib.parse.urlencode({"series_id": seri, "file_type": "json",
                                    "api_key": anahtar,
                                    "observation_start": baslangic})
    ham = _istek(f"{FRED_API}?{sorgu}", anahtar=anahtar, etiket=f"FRED {seri}")
    if not ham.strip():
        raise ResmiHata(f"FRED {seri}: yanit bos")
    try:
        veri = json.loads(ham)
    except ValueError:
        raise ResmiHata(f"FRED {seri}: JSON degil") from None
    gozlemler = []
    for it in veri.get("observations") or []:
        tarih = _donem_cevir(it.get("date"), frekans)
        try:
            deger = float(it.get("value"))
        except (TypeError, ValueError):
            continue  # '.' bos gozlem
        if tarih and math.isfinite(deger):
            gozlemler.append((tarih, deger))
    if not gozlemler:
        raise ResmiHata(f"FRED {seri}: gecerli gozlem yok")
    return gozlemler


def _fred_csv(seri, baslangic, frekans):
    url = f"{FRED_CSV}?id={seri}" + (f"&cosd={baslangic}" if baslangic else "")
    ham = _istek(url, etiket=f"FRED {seri}")
    if not ham.strip():
        raise ResmiHata(f"FRED {seri}: yanit bos")
    okuyucu = csv.reader(io.StringIO(ham))
    try:
        baslik = next(okuyucu)
    except StopIteration:
        raise ResmiHata(f"FRED {seri}: CSV bos") from None
    if len(baslik) < 2:
        raise ResmiHata(f"FRED {seri}: kolonlar taninamadi")
    gozlemler = []
    for satir in okuyucu:
        if len(satir) < 2:
            continue
        tarih = _donem_cevir(satir[0], frekans)
        try:
            deger = float(satir[1])
        except ValueError:
            continue
        if tarih and math.isfinite(deger):
            gozlemler.append((tarih, deger))
    if not gozlemler:
        raise ResmiHata(f"FRED {seri}: gecerli gozlem yok")
    return gozlemler


def _fred(seri, tarih, geriye_gun, frekans):
    baslangic = (tarih - timedelta(days=geriye_gun)).isoformat()
    anahtar = os.environ.get("FRED_API_KEY", "").strip()
    if anahtar:
        try:
            return _fred_api(seri, baslangic, anahtar, frekans)
        except ResmiHata as exc:
            logger.info("[Resmi] FRED API atlandi (%s); fredgraph CSV kullanildi",
                        exc)
    return _fred_csv(seri, baslangic, frekans)


def _bea(pin, tarih):
    anahtar = os.environ.get("BEA_API_KEY", "").strip()
    if not anahtar:
        raise ResmiHata("BEA_API_KEY yok; satir TV'de kalir", durum="anahtar")
    kod = CARI_GOSTERGE or os.environ.get("BEA_CARI_INDICATOR", "").strip()
    if not kod:
        raise ResmiHata("BEA cari denge indikator kodu kesif bekliyor",
                        durum="kesif")
    sorgu = urllib.parse.urlencode({
        "UserID": anahtar, "method": "GetData",
        "datasetname": pin.get("veri_seti", "ITA"), "Indicator": kod,
        "Year": "last", "ResultFormat": "JSON"})
    ham = _istek(f"{BEA_KOK}?{sorgu}", anahtar=anahtar,
                 etiket="BEA " + pin.get("veri_seti", "ITA"))
    if not ham.strip():
        raise ResmiHata("BEA yanit bos (anahtar/erisim yok olabilir)",
                        durum="bos")
    try:
        veri = json.loads(ham)
    except ValueError:
        raise ResmiHata("BEA JSON degil") from None
    ham_veri = ((veri.get("BEAAPI") or {}).get("Results") or {}).get("Data")
    if not isinstance(ham_veri, list) or not ham_veri:
        hata = ((veri.get("BEAAPI") or {}).get("Error") or {}).get("APIErrorDescription") or ""
        raise ResmiHata(f"BEA veri yok: {hata}", durum="veri")
    gozlemler = []
    for it in ham_veri:
        tarih_d = _donem_cevir(it.get("TimePeriod"), pin["frekans"])
        try:
            deger = float(str(it.get("DataValue")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if tarih_d and math.isfinite(deger):
            gozlemler.append((tarih_d, deger))
    if not gozlemler:
        raise ResmiHata("BEA gecerli gozlem yok")
    return gozlemler


def bea_gostergeleri():
    """BEA ITA indikator listesini dondurur (saglik kontrolu kesfi).

    Klasore gore cevap ``BEAAPI.Results.ParamValue[]`` icinde ``Key`` +
    ``Desc`` tasiyor (Nisan 2026 kilavuzu s.11-12); anahtar/alan adi
    yanlisysa liste bos doner ve kesif sessizce kaybolur, bu yuzden bos
    yanit hata olarak yukseltilir.
    """
    anahtar = os.environ.get("BEA_API_KEY", "").strip()
    if not anahtar:
        raise ResmiHata("BEA_API_KEY yok", durum="anahtar")
    sorgu = urllib.parse.urlencode({
        "UserID": anahtar, "method": "GetParameterValues",
        "datasetname": "ITA", "ParameterName": "Indicator",
        "ResultFormat": "JSON"})
    ham = _istek(f"{BEA_KOK}?{sorgu}", anahtar=anahtar,
                 etiket="BEA ITA kesfi")
    if not ham.strip():
        raise ResmiHata("BEA kesif yaniti bos", durum="bos")
    try:
        veri = json.loads(ham)
    except ValueError:
        raise ResmiHata("BEA kesif JSON degil") from None
    bea = veri.get("BEAAPI") or {}
    hata = str((bea.get("Error") or {}).get("APIErrorDescription") or "")
    sonuclar = bea.get("Results") or {}
    degerler = sonuclar.get("ParamValue") or sonuclar.get("ParameterValues") or []
    if not degerler:
        raise ResmiHata(f"BEA kesif bos: {hata or 'ParamValue yok'}",
                        durum="veri" if hata else "bos")
    return [{"kod": str(d.get("Key") or ""),
             "ad": str(d.get("Desc") or d.get("Text") or "")}
            for d in degerler if isinstance(d, dict)]


# -------------------------------------------------------------------- pinler

EU_PINLER = [
    {"gosterge": "inflation_yoy", "ulke": "Euro Bölgesi", "kaynak": "Eurostat",
     "tip": "eurostat", "veri_kumesi": "prc_hicp_minr",
     "parametreler": {"geo": "EA21", "coicop18": "TOTAL", "unit": "RCH_A"},
     "kaynak_baslik": "Eurostat EA21 HICP (yıllık)",
     "hesap": "son", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 400, "min": -5.0, "max": 100.0},
    {"gosterge": "inflation_mom", "ulke": "Euro Bölgesi", "kaynak": "Eurostat",
     "tip": "eurostat", "veri_kumesi": "prc_hicp_minr",
     "parametreler": {"geo": "EA21", "coicop18": "TOTAL", "unit": "RCH_M"},
     "kaynak_baslik": "Eurostat EA21 HICP (aylık)",
     "hesap": "son", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 400, "min": -10.0, "max": 100.0},
    {"gosterge": "unemployment_rate", "ulke": "Euro Bölgesi",
     "kaynak": "Eurostat", "tip": "eurostat", "veri_kumesi": "une_rt_m",
     "parametreler": {"geo": "EA21", "sex": "T", "age": "TOTAL",
                      "unit": "PC_ACT", "s_adj": "SA"},
     "kaynak_baslik": "Eurostat EA21 işsizlik oranı",
     "hesap": "son", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 400, "min": 0.0, "max": 30.0},
    {"gosterge": "growth_yoy", "ulke": "Euro Bölgesi", "kaynak": "Eurostat",
     "tip": "eurostat", "veri_kumesi": "namq_10_gdp",
     "parametreler": {"geo": "EA21", "na_item": "B1GQ", "unit": "CLV_PCH_SM",
                      "s_adj": "NSA"},
     "kaynak_baslik": "Eurostat EA21 GSYH (yıllık)",
     "hesap": "son", "frekans": "üç aylık", "ondalik": 1,
     "geriye_gun": 700, "min": -20.0, "max": 30.0},
    {"gosterge": "growth_qoq", "ulke": "Euro Bölgesi", "kaynak": "Eurostat",
     "tip": "eurostat", "veri_kumesi": "namq_10_gdp",
     "parametreler": {"geo": "EA21", "na_item": "B1GQ", "unit": "CLV_PCH_PRE",
                      "s_adj": "SCA"},
     "kaynak_baslik": "Eurostat EA21 GSYH (çeyreklik)",
     "hesap": "son", "frekans": "üç aylık", "ondalik": 1,
     "geriye_gun": 700, "min": -20.0, "max": 30.0},
    {"gosterge": "policy_rate", "ulke": "Euro Bölgesi", "kaynak": "ECB",
     "tip": "ecb", "seri": "FM/D.U2.EUR.4F.KR.MRR_FR.LEV",
     "kaynak_baslik": "ECB MRO ana refinansman faizi",
     "hesap": "son", "frekans": "olay", "ondalik": 2,
     "geriye_gun": 60, "min": 0.0, "max": 100.0},
    {"gosterge": "current_account", "ulke": "Euro Bölgesi",
     "kaynak": "Eurostat", "tip": "eurostat", "veri_kumesi": "ei_bpm6ca_m",
     "parametreler": {"geo": "EA21", "bop_item": "CA", "currency": "MIO_EUR",
                      "sector10": "S1", "sectpart": "S1", "stk_flow": "BAL",
                      "partner": "EXT_EA21", "s_adj": "NSA"},
     "kaynak_baslik": "Eurostat EA21 cari denge (aylık)",
     "hesap": "son", "frekans": "aylık", "birim": "milyar €",
     "olcek": 0.001, "ondalik": 1,
     "geriye_gun": 400, "min": -500.0, "max": 500.0},
]

ABD_PINLER = [
    # --- BLS (government payroll / CPI / issizlik) ---
    {"gosterge": "inflation_yoy", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "CUUR0000SA0",
     "kaynak_baslik": "BLS CPI-U (yıllık)",
     "hesap": "yillik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 100.0},
    # BLS baslikli aylik artis MEVSIMSEL DUZELTILMIS tabanli aciklanir
    # (CUSR = SA: 0.396 -> 0.4 = TV); CUUR (NSA) 0.318 -> 0.3 olurdu.
    {"gosterge": "inflation_mom", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "CUSR0000SA0",
     "kaynak_baslik": "BLS CPI-U (aylık, SA)",
     "hesap": "aylik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 10.0},
    {"gosterge": "core_inflation_yoy", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "CUUR0000SA0L1E",
     "kaynak_baslik": "BLS CPI-U çekirdek (yıllık)",
     "hesap": "yillik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 100.0},
    {"gosterge": "unemployment_rate", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "LNS14000000",
     "kaynak_baslik": "BLS işsizlik oranı (SA)",
     "hesap": "son", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 400, "min": 0.0, "max": 30.0},
    {"gosterge": "u6_unemployment", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "LNS13327709",
     "kaynak_baslik": "BLS U-6 genişletilmiş işsizlik",
     "hesap": "son", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 400, "min": 0.0, "max": 40.0},
    {"gosterge": "participation_rate", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "LNS11300000",
     "kaynak_baslik": "BLS iş gücü katılım oranı (SA)",
     "hesap": "son", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 400, "min": 50.0, "max": 80.0},
    {"gosterge": "nonfarm_payroll", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "CES0000000001",
     "kaynak_baslik": "BLS tarım dışı istihdam değişimi (SA)",
     "hesap": "fark", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -2000.0, "max": 2000.0},
    {"gosterge": "wage_growth_yoy", "ulke": "ABD", "kaynak": "BLS",
     "tip": "bls", "seri": "CES0500000003",
     "kaynak_baslik": "BLS saatlik ücret artışı (yıllık, SA)",
     "hesap": "yillik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 30.0},
    # --- FRED (Fed karari, GSYH, PCE, haftalik issizlik basvurulari) ---
    {"gosterge": "policy_rate", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "DFEDTARU",
     "kaynak_baslik": "FRED Fed fon ust siniri",
     "hesap": "son", "frekans": "olay", "ondalik": 2,
     "geriye_gun": 60, "min": 0.0, "max": 100.0},
    {"gosterge": "growth_yoy", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "GDPC1",
     "kaynak_baslik": "FRED reel GSYH (yıllık)",
     "hesap": "yillik", "frekans": "üç aylık", "ondalik": 1,
     "geriye_gun": 900, "min": -20.0, "max": 30.0},
    {"gosterge": "growth_qoq", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "A191RL1Q225SBEA",
     "kaynak_baslik": "FRED reel GSYH (çeyreklik, yıllıklandırılmış)",
     "hesap": "son", "frekans": "üç aylık", "ondalik": 1,
     "geriye_gun": 400, "min": -20.0, "max": 30.0},
    {"gosterge": "pce_inflation_yoy", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "PCEPI",
     "kaynak_baslik": "FRED PCE fiyat endeksi (yıllık)",
     "hesap": "yillik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 100.0},
    {"gosterge": "pce_inflation_mom", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "PCEPI",
     "kaynak_baslik": "FRED PCE fiyat endeksi (aylık)",
     "hesap": "aylik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 10.0},
    {"gosterge": "core_pce_yoy", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "PCEPILFE",
     "kaynak_baslik": "FRED çekirdek PCE (yıllık)",
     "hesap": "yillik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 100.0},
    {"gosterge": "core_pce_mom", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "PCEPILFE",
     "kaynak_baslik": "FRED çekirdek PCE (aylık)",
     "hesap": "aylik", "frekans": "aylık", "ondalik": 1,
     "geriye_gun": 600, "min": -10.0, "max": 10.0},
    {"gosterge": "initial_jobless_claims", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "ICSA",
     "kaynak_baslik": "FRED haftalık ilk işsizlik başvurusu (SA)",
     "hesap": "son", "frekans": "haftalık", "ondalik": 1, "olcek": 0.001,
     "geriye_gun": 120, "min": 0.0, "max": 4000.0},
    {"gosterge": "continuing_jobless_claims", "ulke": "ABD", "kaynak": "FRED",
     "tip": "fred", "seri": "CCSA",
     "kaynak_baslik": "FRED haftalık devam eden başvurular (SA)",
     "hesap": "son", "frekans": "haftalık", "ondalik": 1, "olcek": 0.001,
     "geriye_gun": 120, "min": 0.0, "max": 4000.0},
    # --- BEA (cari denge; indikator kodu kesif beklerken atlanir) ---
    {"gosterge": "current_account", "ulke": "ABD", "kaynak": "BEA",
     "tip": "bea", "veri_seti": "ITA",
     "kaynak_baslik": "BEA ITA cari denge",
     "hesap": "son", "frekans": "üç aylık", "birim": "milyar $",
     "olcek": "milyar", "ondalik": 1,
     "geriye_gun": 400, "min": -3000.0, "max": 1000.0},
]

PINLER = EU_PINLER + ABD_PINLER


def _birim(pin):
    """Katalog birimi bos degilse o gecerlidir (validate kurali)."""
    return KOD_GOSTERGE[pin["gosterge"]][2] or pin.get("birim") or ""


def _gosterge_adi(gosterge):
    return KOD_GOSTERGE[gosterge][1]


def _pin_anahari(pin, tarih):
    """Ayni veri kumesini farkli parametrelerle sorgulayan pin'ler ayri olmali.

    `prc_hicp_minr` yillik + aylik, `namq_10_gdp` yillik + ceyreklik olarak
    ayni kumeyi cagirir; anahtar yalnizca kume adini tasisa yanlis seri
    onbellekten doner (25-09-2026 denetiminde goruldu).
    """
    return (pin["tip"],
            pin.get("seri") or pin.get("veri_kumesi") or pin.get("veri_seti", ""),
            repr(pin.get("parametreler")), pin.get("hesap"),
            pin.get("olcek"), str(tarih)[:10])


def _pin_cek(pin, tarih_d):
    """Ham ``[(donem, deger)]`` serisini tek basina kaynaktan ceker."""
    if pin["tip"] == "eurostat":
        return _eurostat(pin["veri_kumesi"], pin["parametreler"],
                         pin["frekans"])
    if pin["tip"] == "ecb":
        return _ecb(pin["seri"], pin["frekans"])
    if pin["tip"] == "fred":
        return _fred(pin["seri"], tarih_d, pin["geriye_gun"], pin["frekans"])
    if pin["tip"] == "bea":
        return _bea(pin, tarih_d)
    if pin["tip"] == "bls":
        # BLS normalde toplu (_bls_doldur) doldurulur; yalnizca disaridan
        # gelen tek bir pin icin yedek tek istek.
        yil_son = tarih_d.year
        yil_bas = max(yil_son - max(1, -(-pin["geriye_gun"] // 366)),
                      yil_son - 9)
        return _bls_toplu([pin["seri"]], yil_bas, yil_son).get(pin["seri"]) or []
    raise ResmiHata(f"bilinmeyen kaynak tipi: {pin['tip']}")


def _pin_coz(pin, tarih):
    """Pin icin (deger, onceki, donem) uretir; hata durumunda ResmiHata.

    Ag/cevap hatalari `_PIN_HATA` defterine yazilir; ayni calismada ayni hedef\n    tekrar tekrar denemez (BLS limiti ve gece kosusuna karsi koruma).
    """
    tarih_d = datetime.strptime(str(tarih)[:10], "%Y-%m-%d").date()
    anahar = _pin_anahari(pin, tarih)
    if anahar in _PIN_HATA:
        raise _PIN_HATA[anahar]
    if pin["tip"] == "bls" and anahar not in _ONBELLEK:
        akraba = [p for p in PINLER if p["tip"] == "bls"]
        if not any(p is pin for p in akraba):
            akraba = akraba + [pin]
        _bls_doldur(akraba, tarih)
        if anahar in _PIN_HATA:
            raise _PIN_HATA[anahar]
    gozlemler = _ONBELLEK.get(anahar)
    if gozlemler is None:
        try:
            gozlemler = _pin_cek(pin, tarih_d)
        except ResmiHata as exc:
            _PIN_HATA[anahar] = exc
            raise
        _ONBELLEK[anahar] = gozlemler
    olcekli = [(d, _olcekla(v, pin.get("olcek"))) for d, v in gozlemler]
    seri = _uret(olcekli, pin["hesap"])
    if not seri:
        raise ResmiHata(f"{pin['gosterge']}: turev serisi bos")
    donem, deger = seri[-1]
    onceki = seri[-2][1] if len(seri) > 1 else None
    ondalik = pin.get("ondalik", 1)
    deger = round(deger, ondalik)
    if onceki is not None:
        onceki = round(onceki, ondalik)
    if not (float(pin["min"]) <= deger <= float(pin["max"])):
        raise ResmiHata(f"{deger} degeri aralik disi "
                        f"[{pin['min']}, {pin['max']}]")
    return deger, onceki, donem.isoformat()


def pin_kontrol(tarih=None):
    """Her pinin canli sonucunu dondurur (saglik kontrolu / yerel denetim).

    Sunucunun istegi aniden kapattigi **tekil** bir ag hatasi (``durum="ag"``)
    icin pin bir kez daha denenir: gece kozesinde tek bir paket kaybi saglik
    kontrolunu kirmiziya cevirmesin. Kalici kesinti yine ``ag`` olarak doner
    (ikiden fazla deneme YOK), 401/403 gibi kalici hatalar da tekrarlanmaz
    (BLS kotasi bosuna yanmasin).
    """
    tarih = tarih or date.today().isoformat()
    sonuclar = []
    for pin in PINLER:
        kayit = {"gosterge": pin["gosterge"], "ulke": pin["ulke"],
                 "kaynak": pin["kaynak"], "seri": pin.get("seri")
                 or pin.get("veri_kumesi") or pin.get("veri_seti", ""),
                 "durum": "ok", "birim": _birim(pin)}
        for deneme in range(2):
            try:
                deger, onceki, donem = _pin_coz(pin, tarih)
                kayit.update({"durum": "ok", "deger": deger,
                              "onceki": onceki, "donem": donem})
                kayit.pop("hata", None)   # onceden yazilmis hata metni kalmasin
                break
            except ResmiHata as exc:
                kayit.update({"durum": exc.durum, "hata": str(exc)})
                if deneme or exc.durum != "ag":
                    break
                # Tekil ag patlamasi: defteri bosalt ve bir kez daha dene.
                _PIN_HATA.pop(_pin_anahari(pin, tarih), None)
                kayit["tekrar"] = True
                time.sleep(TEKRAR_BEKLEME)
            except Exception as exc:  # pragma: no cover - guvenlik agi
                kayit.update({"durum": "hata", "hata": str(exc)})
                break
        sonuclar.append(kayit)
    return sonuclar


# --------------------------------------------------- satir guncelleme/kayit


def _satir_bul(gostergeler, ulke, ad):
    return next((g for g in gostergeler
                 if str(g.get("ulke")) == ulke and str(g.get("ad")) == ad),
                None)


def _satir_ekle(gostergeler, ulke, ad, ortak):
    """Ayni ulke blogunun sonuna resmi satir ekler (TUik deseniyle)."""
    yeni = {"ulke": ulke, "ad": ad, **ortak}
    son_i = max((i for i, g in enumerate(gostergeler)
                 if str(g.get("ulke")) == ulke),
                default=len(gostergeler) - 1)
    gostergeler.insert(son_i + 1, yeni)
    return yeni


def gostergeleri_ekle(gostergeler, tarih):
    """Resmi AB/ABD satirlarini listeye yazar; islenen satir sayisini dondurur.

    TV satiri taze ise **uzerine** yazilir, yoksa yeni satir eklenir. Tazelik
    kapagi gecmezse TV satiri aynen kalir ve yalnizca bilgi logu yazilir.
    """
    islenen = 0
    for pin in PINLER:
        ad = _gosterge_adi(pin["gosterge"])
        try:
            deger, onceki, donem = _pin_coz(pin, tarih)
        except ResmiHata as exc:
            seviye = (logger.info if exc.durum in ("anahtar", "kesif")
                      else logger.warning)
            seviye("[Resmi] %s atlandi [%s]: %s", pin["gosterge"],
                   exc.durum, exc)
            continue
        except Exception as exc:  # pragma: no cover - guvenlik agi
            logger.warning("[Resmi] %s beklenmeyen hata: %s",
                           pin["gosterge"], exc)
            continue
        satir = _satir_bul(gostergeler, pin["ulke"], ad)
        if not _taze_mi(donem,
                        satir.get("donem") if satir else None,
                        satir.get("kaynak_periyot") if satir else None,
                        tarih, pin["frekans"]):
            logger.info("[Resmi] %s resmi donem %s taze degil; %s satiri "
                        "korundu (TV %s)", pin["gosterge"], donem,
                        pin["ulke"], satir.get("donem") if satir else "-")
            continue
        tahmin = satir.get("tahmin") if satir else None
        ortak = {
            "deger": deger, "birim": _birim(pin), "donem": donem,
            "tahmin": tahmin, "onceki": onceki,
            "kaynak_baslik": pin["kaynak_baslik"], "kaynak_periyot": donem,
            "frekans": pin["frekans"], "teyit": pin["kaynak"],
            "kaynak_durumu": f"{pin['kaynak']} resmi verisi",
        }
        if satir is None:
            _satir_ekle(gostergeler, pin["ulke"], ad, ortak)
            logger.info("[Resmi] %s satiri eklendi: %s (%s)",
                        pin["gosterge"], deger, donem)
        else:
            onceki_deger = satir.get("deger")
            satir.update(ortak)
            if onceki_deger != deger:
                logger.info("[Resmi] %s %s: %s -> %s (%s)",
                            pin["gosterge"], pin["ulke"], onceki_deger,
                            deger, donem)
        islenen += 1
    if islenen:
        logger.info("[Resmi] %d resmi satir islendi", islenen)
    return islenen


def _resmi_satir(kayit):
    """Kayit resmi bir kanaldan mi geliyor (TUİK/EVDS/AB-ABD kanallari)?"""
    if str(kayit.get("confirmed_by") or "").strip():
        return True
    provenans = str(kayit.get("provenance") or "")
    return any(ad in provenans for ad in ("EVDS", "Eurostat", "ECB", "BLS",
                                          "FRED", "BEA", "TÜİK"))


def magaza_yaz(snapshot):
    """Snapshot'taki resmi AB/ABD + TR onayli satirlari ayri store'a yazar.

    * EU/ABD: tum satirlar (kaynak_turu alani hangi kanaldan geldigini soyler).
    * TR: yalnizca TUİK/EVDS onayli satirlar.
    """
    kayitlar = []
    for r in snapshot["records"]:
        kod = r.get("country_code")
        if kod in ("EU", "US"):
            pass
        elif kod == "TR" and _resmi_satir(r):
            pass
        else:
            continue
        kayit = {alan: r.get(alan) for alan in MAGAZA_ALANLARI}
        kayit["kaynak_turu"] = ("resmi kanal" if _resmi_satir(r)
                                else "TV ekonomik takvimi")
        kayitlar.append(kayit)
    kaynaklar = sorted({str(r.get("confirmed_by") or "").strip()
                        for r in kayitlar if str(r.get("confirmed_by") or "").strip()})
    govde = {
        "schema": "borsa-raporlari/resmi-veri",
        "schema_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
        "guncelleme": snapshot["snapshot_date"],
        "olusturma": snapshot["captured_at"],
        "kaynaklar": (kaynaklar or []) + ["TradingView"],
        "kayit_sayisi": len(kayitlar),
        "gostergeler": kayitlar,
    }
    arsiv = ARSIV_DIZIN / f"{snapshot['snapshot_date']}.json"
    _yaz(arsiv, govde)
    _yaz(MAGAZA_YOL, govde)
    logger.info("[Resmi] magaza yazildi: %s (+%s) %d kayit",
                MAGAZA_YOL, arsiv, len(kayitlar))
    return MAGAZA_YOL, arsiv


def _yaz(yol, veri):
    yol = Path(yol)
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(yol.suffix + ".tmp")
    with gecici.open("w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(gecici, yol)
