# -*- coding: utf-8 -*-
"""Temettü veri çekici — borsapy (temettü/bedelsiz/fiyat) + Yahoo (USD/TRY, altın).

Çıktı:
  data/temettu/seriler/<KOD>.json  → günlük düzeltilmemiş fiyat + temettü + bedelsiz
  data/temettu/kur.json            → günlük USD/TRY ve gram altın
  data/temettu/ozet.json           → takvim + verim tablosu (temettu.py üretir)

Kullanım: python temettu_cek.py [--sinir N]
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

KOK = os.path.dirname(os.path.abspath(__file__))
VERI = os.path.join(KOK, "data", "temettu")
SERI = os.path.join(VERI, "seriler")

TICKERS = ["TOASO", "TTKOM", "ENKAI", "ASELS", "FROTO", "MGROS", "ASTOR", "VAKBN",
           "TUPRS", "YKBNK", "ISCTR", "GARAN", "PETKM", "BIMAS", "TAVHL", "EKGYO",
           "KCHOL", "KRDMD", "AKBNK", "SAHOL", "EREGL", "TRMET", "SISE", "GUBRF",
           "AEFES", "SASA", "TCELL", "TRALT", "THYAO", "PGSUS"]

BASLANGIC = "2008-01-01"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _yahoo(sym, p1=1199145600, p2=None):
    """Yahoo chart API'den günlük seri (timestamp, close)."""
    if p2 is None:
        p2 = int(datetime.now(timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1={p1}&period2={p2}&interval=1d")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    res = d["chart"]["result"][0]
    ts = res.get("timestamp") or []
    cl = res["indicators"]["quote"][0]["close"]
    out = []
    for i, t in enumerate(ts):
        if i < len(cl) and cl[i] is not None:
            out.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), float(cl[i])))
    return out


def kur_cek():
    """USD/TRY ve gram altın (GC=F USD/ons ÷ 31.1035 × USDTRY)."""
    os.makedirs(VERI, exist_ok=True)
    usd = dict(_yahoo("TRY=X"))
    ons = dict(_yahoo("GC=F"))
    ortak = sorted(set(usd) & set(ons))
    seri = []
    son_usd, son_ons = None, None
    for g in sorted(set(usd) | set(ons)):
        son_usd = usd.get(g, son_usd)
        son_ons = ons.get(g, son_ons)
        if son_usd and son_ons:
            seri.append([g, round(son_usd, 4), round(son_ons / 31.1035 * son_usd, 4)])
    json.dump({"guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M"), "seri": seri},
              open(os.path.join(VERI, "kur.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[kur] {len(seri)} gün · son USD/TRY={seri[-1][1]} gram altın={seri[-1][2]}")
    return len(seri)


def hisse_cek(kod):
    import borsapy as bp
    t = bp.Ticker(kod)

    h = t.history(start=BASLANGIC)
    if h is None or len(h) == 0:
        raise RuntimeError("fiyat serisi boş")

    # Bedelsiz / sermaye artırımı oranları (yüzde → çarpan)
    bedelsiz = []
    try:
        sp = t.splits
        if sp is not None and len(sp):
            for idx, row in sp.iterrows():
                yuzde = 0.0
                for col in ("BonusFromCapital", "BonusFromDividend", "RightsIssue"):
                    if col in sp.columns and row[col] == row[col]:
                        yuzde += float(row[col] or 0)
                if yuzde > 0:
                    bedelsiz.append([str(idx)[:10], round(1 + yuzde / 100.0, 6)])
    except Exception as e:
        print(f"  ! {kod} bedelsiz: {e}")

    bedelsiz.sort()

    # Temettü (hisse başına TL, düzeltilmemiş)
    temettu = []
    try:
        dv = t.dividends
        if dv is not None and len(dv):
            for idx, row in dv.iterrows():
                tut = float(row.iloc[0]) if hasattr(row, "iloc") else float(row)
                if tut > 0:
                    temettu.append([str(idx)[:10], round(tut, 4)])
    except Exception as e:
        print(f"  ! {kod} temettü: {e}")
    temettu.sort()

    # Fiyat serisi: borsapy düzeltilmiş kapanış verir → düzeltilmemişe çevir
    # düzeltilmemiş(t) = düzeltilmiş(t) × Π(sonraki bedelsiz çarpanları)
    fiyat = []
    for idx, row in h.iterrows():
        g = str(idx)[:10]
        p = float(row["Close"])
        if p <= 0:
            continue
        carpan = 1.0
        for bd, r in bedelsiz:
            if bd > g:
                carpan *= r
        fiyat.append([g, round(p * carpan, 4)])

    kayit = {
        "kod": kod,
        "guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fiyat": fiyat,
        "temettu": temettu,
        "bedelsiz": bedelsiz,
    }
    os.makedirs(SERI, exist_ok=True)
    with open(os.path.join(SERI, kod + ".json"), "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False, separators=(",", ":"))
    return len(fiyat), len(temettu), len(bedelsiz)


def main():
    sinir = None
    if "--sinir" in sys.argv:
        sinir = int(sys.argv[sys.argv.index("--sinir") + 1])
    os.makedirs(SERI, exist_ok=True)
    kur_cek()
    liste = TICKERS[:sinir] if sinir else TICKERS
    hata = []
    for i, kod in enumerate(liste, 1):
        t0 = time.time()
        try:
            n, nd, nb = hisse_cek(kod)
            print(f"[{i:2d}/{len(liste)}] {kod:6s} fiyat={n:5d} temettü={nd:3d} bedelsiz={nb:2d} "
                  f"({time.time()-t0:.1f}s)", flush=True)
        except Exception as e:
            hata.append(kod)
            print(f"[{i:2d}/{len(liste)}] {kod:6s} HATA: {e}", flush=True)
        time.sleep(0.2)
    print("bitti. hata:", hata or "yok")


if __name__ == "__main__":
    main()
