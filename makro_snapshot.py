# -*- coding: utf-8 -*-
"""Canli makro verisini akşam snapshot semasina yazar.

Bu script akşam workflow'u tarafindan calistirilir. Rapor/analiz uretimi bu
scripti calistirmaz; ertesi sabah data/makro-gecmis/<rapor_tarihi-1>.json
kaydini okur.
"""
import os

# bot.py import sirasinda en az bir saglayici anahtari istiyor; burada LLM cagrisi
# yapilmayacagi icin yerel bir sentinel yeterlidir.
os.environ.setdefault("AMD_API_KEY", "makro-snapshot")

from datetime import datetime, timedelta
import logging
import zoneinfo

import bot
import makro_veri
import makro_rejim
import resmi_veri

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("makro-snapshot")


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    now = datetime.now(tz)
    # Veri gunu gece yarisi degil, BIST acilisinda baslar (10:00 TSİ; 2026-10-06
    # kullanici karari). Bu yuzden 00:00-09:59 arasindaki gecikmis kosular bir
    # onceki gunun aksamina DAHILDIR: snapshot tarihi onceki gune alinir ve
    # captured_at nominal slota (18:05 TSİ) normalize edilir. Oncesi 06:00
    # idi; 3-4 Ekim 2026'da GitHub cron koşuyu 00:04'e sarkitip 18:00 kapisi
    # reddediyor, ertesi sabah raporu cokuyordu. 10:00-17:59 arasi kosular
    # BIST icinde oldugundan aksine dahil edilmez: kapı reddeder (gun ici
    # verisinin aksam snapshot'ina karismasi istenmez).
    if now.hour < 10:
        now = (now - timedelta(days=1)).replace(hour=18, minute=5,
                                                second=0, microsecond=0)
        logger.warning("Veri günü dışı (gece/öğleden önce) koşusu algılandı; "
                       "snapshot önceki akşam (%s) olarak yazılacak.",
                       now.isoformat(timespec="seconds"))
    logger.info("Canli makro verisi cekiliyor...")
    canli = bot.makro_cek()
    if not canli or not canli.get("gostergeler"):
        logger.error("Canli makro verisi alinamadi; snapshot yazilmadi.")
        return 1
    if canli.get("_veri_durumu") != "canli":
        logger.error("Makro verisi cache'ten geldi; yeni aksam snapshot yazilmadi.")
        return 1
    canli = dict(canli)
    canli.pop("_veri_durumu", None)
    piyasa = bot.piyasa_serileri_ce(now.date().isoformat())
    canli["piyasa"] = piyasa
    canli["kaynak"] = (str(canli.get("kaynak", ""))
                       + " | Yahoo Finance/FRED/Borsapy piyasa serileri")
    logger.info("%d piyasa serisi snapshot'a eklendi.", len(piyasa))
    snapshot = makro_veri.normalize(canli, captured_at=now.isoformat(timespec="seconds"))
    makro_veri.validate(snapshot)
    guncel, arsiv = makro_veri.write(snapshot)
    # Resmi veri tabanı (EU + ABD tum satirlari, TR'de TUİK/EVDS onaylilari):
    # snapshot'tan uretilir; yazamazsa aksam snapshot'i etkilenmez.
    try:
        resmi_magaza, resmi_arsiv = resmi_veri.magaza_yaz(snapshot)
        resmi_durum = f"{resmi_magaza} + {resmi_arsiv}"
    except Exception as e:
        logger.warning("Resmi veri magazasi yazilamadi: %s", e)
        resmi_durum = "yazilamadi"
    rejim = makro_rejim.uret(kaydet_mi=True)
    rejim_yolu = ((rejim or {}).get("rejim") or {}).get("kayit_yolu", "yazilamadi")
    print(
        f"MAKRO SNAPSHOT YAZILDI: {snapshot['snapshot_id']} | "
        f"{len(snapshot['records'])} kayit | {guncel} + {arsiv} | rejim: {rejim_yolu}"
        f" | resmi: {resmi_durum}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
