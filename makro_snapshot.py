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
    # GitHub cron hafta sonunda koşuyu saatlerce geciktirebiliyor (3-4 Ekim
    # 2026: aksam slotu yerine 00:04 TSİ). Gece koşuları bir önceki akşamın
    # GECİKMİŞ koşusu sayılır: snapshot tarihi önceki güne alınır ve
    # captured_at o gunun nominal slotuna (18:05 TSİ, 2026-10-06 kullanici
    # karari) normalize edilir; yoksa 18:00 kapisi ve tarih kontrolleri
    # snapshoti reddediyor, ertesi sabah raporu cokuyordu.
    if now.hour < 6:
        now = (now - timedelta(days=1)).replace(hour=18, minute=5,
                                                second=0, microsecond=0)
        logger.warning("Gece yarisi koşusu algılandı; snapshot önceki akşam "
                       "(%s) olarak yazılacak.", now.isoformat(timespec="seconds"))
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
