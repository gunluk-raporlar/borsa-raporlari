# -*- coding: utf-8 -*-
"""Akşam makro snapshot sozlesmesi.

Bu modul canli veri CEKMEZ. `makro_cek()` sonucu once makro_snapshot.py
ile burada tanimlanan semaya normalize edilir. Ertesi sabah rapor/analiz,
`data/makro-gecmis/<rapor_tarihi-1>.json` kaydini okur.
"""
from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path

from makro_katalog import GOSTERGE_KOD, KOD_GOSTERGE, PIYASA_KOD, REQUIRED

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Istanbul")
except Exception:  # pragma: no cover
    TZ = None

SCHEMA = "borsa-raporlari/makro-snapshot"
SCHEMA_VERSION = 2
SNAPSHOT_PATH = Path("data/makro-snapshot.json")
HISTORY_DIR = Path("data/makro-gecmis")
LEGACY_INFLATION_PATH = Path("data/enflasyon.json")

ULKE_KOD = {"Türkiye": "TR", "ABD": "US", "Euro Bölgesi": "EU"}


class SnapshotError(ValueError):
    """Snapshot okunamaz, sema gecersiz veya yanlis akşam kaydi."""


def _dt_now():
    return datetime.now(TZ) if TZ else datetime.now().astimezone()


def _parse_dt(deger):
    try:
        dt = datetime.fromisoformat(str(deger))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"gecersiz snapshot zamani: {deger!r}") from exc


def _optional_float(deger):
    if deger in (None, ""):
        return None
    try:
        deger = float(deger)
        return deger if math.isfinite(deger) else None
    except (TypeError, ValueError):
        return None


def normalize(veri, captured_at=None):
    """Canlı takvim + piyasa serilerini akşam snapshot semasına çevirir."""
    if not isinstance(veri, dict) or not isinstance(veri.get("gostergeler"), list):
        raise SnapshotError("canli makro verisi gostergeler listesini icermiyor")
    dt = _parse_dt(captured_at) if captured_at else _dt_now().astimezone(TZ)
    if TZ:
        dt = dt.astimezone(TZ)
    snapshot_date = dt.date().isoformat()
    valid_for = (dt.date() + timedelta(days=1)).isoformat()
    kayitlar = []
    for g in veri["gostergeler"]:
        ulke = str(g.get("ulke", ""))
        ad = str(g.get("ad", ""))
        if ulke not in ULKE_KOD or ad not in GOSTERGE_KOD:
            continue
        try:
            deger = float(g["deger"])
            donem = str(g.get("donem", ""))[:10]
            datetime.strptime(donem, "%Y-%m-%d")
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError(f"gecersiz makro kaydi: {g!r}") from exc
        if not math.isfinite(deger):
            raise SnapshotError(f"sonlu olmayan makro degeri: {deger}")
        kayitlar.append({
            "country_code": ULKE_KOD[ulke], "country": ulke,
            "indicator": GOSTERGE_KOD[ad], "label": ad,
            "value": deger, "unit": str(g.get("birim", "")),
            "period": donem, "status": "actual",
            "forecast": _optional_float(g.get("tahmin")),
            "previous": _optional_float(g.get("onceki")),
            "frequency": str(g.get("frekans") or ""),
            "source_title": str(g.get("kaynak_baslik") or ad),
            "source_period": str(g.get("kaynak_periyot") or ""),
            "provenance": str(g.get("kaynak_durumu") or "canlı akşam toplaması"),
            "confirmed_by": str(g.get("teyit") or ""),
        })
    piyasa = []
    for p in veri.get("piyasa") or []:
        try:
            value = float(p["value"])
            period = str(p["period"])[:10]
            datetime.strptime(period, "%Y-%m-%d")
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError(f"gecersiz piyasa kaydi: {p!r}") from exc
        if not math.isfinite(value):
            raise SnapshotError(f"sonlu olmayan piyasa degeri: {value}")
        item = dict(p)
        item.update({"value": value, "period": period,
                     "previous": _optional_float(p.get("previous")),
                     "change": _optional_float(p.get("change"))})
        piyasa.append(item)
    kayitlar.sort(key=lambda r: (r["country_code"], r["indicator"]))
    piyasa.sort(key=lambda r: r["indicator"])
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "snapshot_id": f"makro-{snapshot_date}-evening",
        "snapshot_date": snapshot_date, "valid_for_date": valid_for,
        "captured_at": dt.isoformat(timespec="seconds"),
        "timezone": "Europe/Istanbul", "source": str(veri.get("kaynak", "")),
        "records": kayitlar, "piyasa": piyasa,
        "coverage": {
            "macro_records": len(kayitlar), "market_records": len(piyasa),
            "available_indicators": sorted({r["indicator"] for r in kayitlar}),
            "available_market_series": sorted({r["indicator"] for r in piyasa}),
        },
    }


def validate(snapshot, expected_report_date=None):
    """Snapshot semasini, zorunlu ulke/gostergeleri ve tarihi dogrular."""
    if not isinstance(snapshot, dict):
        raise SnapshotError("snapshot JSON nesnesi degil")
    if snapshot.get("schema") != SCHEMA or snapshot.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError("snapshot semasi/surumu desteklenmiyor")
    if snapshot.get("timezone") != "Europe/Istanbul":
        raise SnapshotError("snapshot timezone Europe/Istanbul olmali")
    if not str(snapshot.get("source", "")).strip():
        raise SnapshotError("snapshot kaynak alani bos")
    for alan in ("snapshot_id", "snapshot_date", "valid_for_date", "captured_at",
                 "timezone", "source", "records", "piyasa", "coverage"):
        if alan not in snapshot:
            raise SnapshotError(f"snapshot alani eksik: {alan}")
    dt = _parse_dt(snapshot["captured_at"])
    if TZ:
        dt = dt.astimezone(TZ)
    snapshot_date = datetime.strptime(snapshot["snapshot_date"], "%Y-%m-%d").date()
    valid_for = datetime.strptime(snapshot["valid_for_date"], "%Y-%m-%d").date()
    if dt.date() != snapshot_date:
        raise SnapshotError("captured_at ile snapshot_date uyusmuyor")
    if snapshot["snapshot_id"] != f"makro-{snapshot_date}-evening":
        raise SnapshotError("snapshot_id snapshot tarihiyle uyusmuyor")
    if dt.timetz().replace(tzinfo=None) < time(18, 0):
        raise SnapshotError("snapshot akşam 18:00 TSİ sonrasinda alinmali")
    beklenen = snapshot_date + timedelta(days=1)
    if valid_for != beklenen:
        raise SnapshotError("valid_for_date snapshot tarihinin ertesi gunu degil")
    if expected_report_date and snapshot["valid_for_date"] != expected_report_date:
        raise SnapshotError(
            f"beklenen {expected_report_date} icin akşam snapshoti yok; "
            f"bulunan {snapshot['valid_for_date']}")
    kayitlar = snapshot.get("records")
    if not isinstance(kayitlar, list) or not kayitlar:
        raise SnapshotError("snapshot kayitlari bos")
    gorulen = set()
    for r in kayitlar:
        try:
            key = (r["country_code"], r["indicator"])
            value = float(r["value"])
            datetime.strptime(r["period"], "%Y-%m-%d")
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError(f"gecersiz snapshot kaydi: {r!r}") from exc
        if key in gorulen:
            raise SnapshotError(f"tekrarlanan snapshot kaydi: {key}")
        if key[0] not in REQUIRED or key[1] not in GOSTERGE_KOD.values():
            raise SnapshotError(f"bilinmeyen snapshot kaydi: {key}")
        if ULKE_KOD.get(r.get("country")) != key[0] or GOSTERGE_KOD.get(r.get("label")) != key[1]:
            raise SnapshotError(f"ulke/gosterge etiketi kayitla uyusmuyor: {r!r}")
        if r.get("status") != "actual":
            raise SnapshotError(f"kayit actual degil: {key}")
        katalog_birimi = KOD_GOSTERGE[key[1]][2]
        if katalog_birimi and r.get("unit") != katalog_birimi:
            raise SnapshotError(f"gosterge birimi yanlis: {key}")
        for alan in ("forecast", "previous"):
            if r.get(alan) is not None and not math.isfinite(float(r[alan])):
                raise SnapshotError(f"gecersiz {alan} degeri: {key}")
        if not math.isfinite(value):
            raise SnapshotError(f"snapshot degeri sonlu degil: {key}")
        gorulen.add(key)
    for ulke, gerekli in REQUIRED.items():
        eksik = sorted(gerekli - {k[1] for k in gorulen if k[0] == ulke})
        if eksik:
            raise SnapshotError(f"{ulke} zorunlu gostergeleri eksik: {eksik}")

    piyasa = snapshot.get("piyasa")
    if not isinstance(piyasa, list) or len(piyasa) < 5:
        raise SnapshotError("piyasa snapshot'inda en az 5 gercek kayit olmali")
    piyasa_gorulen = set()
    for r in piyasa:
        try:
            kod = r["indicator"]
            value = float(r["value"])
            previous = _optional_float(r.get("previous"))
            change = _optional_float(r.get("change"))
            datetime.strptime(str(r["period"])[:10], "%Y-%m-%d")
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError(f"gecersiz piyasa kaydi: {r!r}") from exc
        if kod not in PIYASA_KOD or kod in piyasa_gorulen:
            raise SnapshotError(f"bilinmeyen/tekrarlanan piyasa kaydi: {r!r}")
        if not math.isfinite(value) or not str(r.get("label", "")).strip():
            raise SnapshotError(f"gecersiz piyasa degeri/etiketi: {r!r}")
        piyasa_gorulen.add(kod)
    coverage = snapshot.get("coverage") or {}
    if (coverage.get("macro_records") != len(snapshot["records"])
            or coverage.get("market_records") != len(piyasa)):
        raise SnapshotError("snapshot coverage sayilari eslesiyor")
    return snapshot


def _atomic_json(yol, veri):
    yol = Path(yol)
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(yol.suffix + ".tmp")
    with gecici.open("w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(gecici, yol)


def write(snapshot):
    """Snapshot'i dogrular, guncel + tarihsel arsiv + legacy TUFE yazar."""
    snapshot = validate(deepcopy(snapshot))
    arsiv = HISTORY_DIR / f"{snapshot['snapshot_date']}.json"
    _atomic_json(arsiv, snapshot)
    _atomic_json(SNAPSHOT_PATH, snapshot)
    tr = next((r for r in snapshot["records"]
               if r["country_code"] == "TR" and r["indicator"] == "inflation_yoy"), None)
    if tr:
        _atomic_json(LEGACY_INFLATION_PATH, {
            "oran": tr["value"] / 100.0, "donem": tr["period"],
            "kaynak": "TUIK (akşam makro snapshot'ı)",
            "guncelleme": snapshot["snapshot_date"],
            "snapshot_id": snapshot["snapshot_id"],
        })
    return SNAPSHOT_PATH, arsiv


def load(path=SNAPSHOT_PATH, expected_report_date=None):
    yol = Path(path)
    try:
        with yol.open(encoding="utf-8") as f:
            snapshot = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"snapshot okunamadi: {yol}") from exc
    return validate(snapshot, expected_report_date=expected_report_date)


def load_for_report(report_date):
    """Rapor tarihine ait onceki aksam arsiv kaydini açar.

    `data/makro-snapshot.json` en son akşamı tutar. Aynı gün 20:30'dan
    sonra elle calisan bir rapor, yeni snapshot'a kaymasin diye daima
    `data/makro-gecmis/<report_date-1>.json` okunur.
    """
    try:
        rapor_tarihi = datetime.strptime(str(report_date), "%Y-%m-%d").date()
    except ValueError as exc:
        raise SnapshotError(f"gecersiz rapor tarihi: {report_date!r}") from exc
    aksam = (rapor_tarihi - timedelta(days=1)).isoformat()
    return load(HISTORY_DIR / f"{aksam}.json", expected_report_date=report_date)


def legacy_data(snapshot):
    """Eski tuketici/moduller icin uyumlu `{gostergeler}` gorunumu."""
    satirlar = [
        {"ulke": r["country"], "ad": r["label"], "deger": r["value"],
         "birim": r["unit"], "donem": r["period"],
         "tahmin": r.get("forecast"), "onceki": r.get("previous"),
         "frekans": r.get("frequency", ""),
         "kaynak": r.get("confirmed_by") or r.get("provenance")
                   or r.get("source_title", "TradingView")}
        for r in snapshot["records"]
    ]
    satirlar.extend(
        {"ulke": "Küresel Piyasa", "ad": r["label"], "deger": r["value"],
         "birim": r.get("unit", ""), "donem": r["period"],
         "tahmin": None, "onceki": r.get("previous"),
         "frekans": "günlük", "kaynak": r.get("source", "")}
        for r in snapshot.get("piyasa") or []
    )
    return {
        "guncelleme": snapshot["snapshot_date"],
        "kaynak": f"{snapshot['source']} | akşam snapshot {snapshot['snapshot_id']}",
        "gostergeler": satirlar,
    }


def frame_text(snapshot):
    """LLM'e kilitli, degistirilemez makro sayi cercevesi uretir."""
    validate(snapshot)
    satirlar = [
        "[MAKRO VERI CERCEVESI - TEK KAYNAK / DEGISTIRILEMEZ]",
        f"Snapshot: {snapshot['snapshot_id']} | alındı: {snapshot['captured_at']}",
        f"Geçerli rapor tarihi: {snapshot['valid_for_date']} | kaynak: {snapshot['source']}",
        f"Kapsam: {len(snapshot['records'])} gerçekleşen makro kaydı, "
        f"{len(snapshot.get('piyasa') or [])} piyasa kaydı.",
        "KURAL: Aşağıdaki değerleri, ülke/gösterge eşleşmelerini, birimleri ve dönemleri "
        "DEĞİŞTİRME. Listedeki başka bir ülkenin değerini bu ülkeye taşıma. "
        "Burada olmayan makro sayı yazma; niteliksel ifade kullan. Tahmin ve önceki "
        "değerleri yalnız kaynakta açıkça verilmişse kullan.",
    ]
    for r in snapshot["records"]:
        ayrinti = [f"gerçekleşen: {r['value']}{r['unit']}"]
        if r.get("forecast") is not None:
            ayrinti.append(f"tahmin: {r['forecast']}{r['unit']}")
        if r.get("previous") is not None:
            ayrinti.append(f"önceki: {r['previous']}{r['unit']}")
        kaynak = r.get("confirmed_by") or r.get("provenance") \
            or r.get("source_title", r["label"])
        satirlar.append(
            f"- [{r['country_code']}] {r['country']} / {r['label']}: "
            f"{' | '.join(ayrinti)} | veri dönemi: {r['period']} | "
            f"frekans: {r.get('frequency') or '-'} | kaynak: {kaynak}")
    if snapshot.get("piyasa"):
        satirlar.append("[AKŞAM PİYASA VERİLERİ — KİLİTLİ]")
        for r in snapshot["piyasa"]:
            degisim = (f" | değişim: {r['change']}%"
                       if r.get("change") is not None else "")
            birim = str(r.get("unit") or "")
            birim_yazi = "" if not birim else ("%" if birim == "%" else " " + birim)
            onceki = (f" | önceki: {r['previous']}{birim_yazi}"
                      if r.get("previous") is not None else "")
            satirlar.append(
                f"- {r['label']}: {r['value']}{birim_yazi}"
                f"{onceki}{degisim} | tarih: {r['period']} | "
                f"kaynak: {r.get('source', '')}")
    return "\n".join(satirlar)


def rate_maps(snapshot):
    """Ulke bazli enflasyon/faiz ve tum gosterge deger sozlukleri."""
    validate(snapshot)
    enflasyon, faiz, gosterge = {}, {}, {}
    for r in snapshot["records"]:
        ulke = r["country_code"].lower()
        gosterge.setdefault(ulke, {})[r["indicator"]] = r["value"]
        if r["indicator"] == "inflation_yoy":
            enflasyon[ulke] = r["value"]
        elif r["indicator"] == "policy_rate":
            faiz[ulke] = r["value"]
    return enflasyon, faiz, gosterge


def inflation_mom_map(snapshot):
    """Ulke bazli AYLIK enflasyon kirilimi (dogrulayicinin frekans ayrimi icin)."""
    validate(snapshot)
    sonuc = {}
    for r in snapshot["records"]:
        if r["indicator"] == "inflation_mom":
            sonuc[r["country_code"].lower()] = r["value"]
    return sonuc


def piyasa_map(snapshot):
    """Snapshot piyasa kayıtlarını doğrulayıcı için listeye çevirir."""
    validate(snapshot)
    return list(snapshot.get("piyasa") or [])
