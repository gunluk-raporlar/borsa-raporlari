# -*- coding: utf-8 -*-
"""Hisse sayfalarini mevcut verilerle elle yeniden uretir.

Normalde hisse sayfalari gunluk bot kosusunda uretilir. Bu arac, elle
guncellenen analiz metinlerini (data/hisse-analiz) veya bilanço tablosunu
degistirdikten sonra gunluk kosuyu beklemeden sayfalari tazelemek icindir.

Kullanim:
    python hisse_yenile.py            # en guncel tarihli teknik dosyayla
    python hisse_yenile.py 2026-09-23 # belirli bir teknik dosyasiyla
"""
import json
import os
import sys

# bot.py import edilirken LLM anahtari zorunlu tutuluyor; yerel uretimde
# hicbir LLM cagrisi yapilmaz, bu yuzden sahte bir anahtar yeterli.
os.environ.setdefault("OR_API_KEY", "yerel-yenileme")

import bot  # noqa: E402
import hisse_analiz  # noqa: E402


def _teknik_gun(gun=None):
    dosyalar = sorted(f[:-5] for f in os.listdir("data/teknik") if f.endswith(".json"))
    if not dosyalar:
        raise SystemExit("data/teknik altinda veri yok.")
    if gun and gun not in dosyalar:
        raise SystemExit(f"{gun} icin teknik veri yok. Mevcut: {', '.join(dosyalar[-5:])}")
    return gun or dosyalar[-1]


def main():
    gun = _teknik_gun(sys.argv[1] if len(sys.argv) > 1 else None)
    teknik = json.load(open(f"data/teknik/{gun}.json", encoding="utf-8"))
    kayitlar = list(teknik)
    mevcut = {s["hisse"] for s in kayitlar}

    # Teknik dosyada olmayan hisse (or. yetersiz gecmis nedeniyle atlanan):
    # onceki gunun satirindan alinir, fiyat en guncel kapanisla guncellenir.
    eksikler = [k for k in hisse_analiz._hisseler() if k not in mevcut]
    if eksikler:
        onceki_dosyalar = [f for f in sorted(os.listdir("data/teknik")) if f.endswith(".json")]
        eski_map = {}
        for dosya in reversed(onceki_dosyalar):
            if dosya.startswith(gun):
                continue
            for s in json.load(open(os.path.join("data/teknik", dosya), encoding="utf-8")):
                eski_map.setdefault(s["hisse"], s)
            if all(k in eski_map for k in eksikler):
                break
        try:
            fiyat = json.load(open(f"data/prices/{gun}.json", encoding="utf-8"))
        except OSError:
            fiyat = {}
        for kod in eksikler:
            kayit = dict(eski_map.get(kod) or {})
            if not kayit:
                print(f"[Yenile] {kod}: teknik satir bulunamadi, atlandi.")
                continue
            if kod in fiyat:
                onceki = kayit.get("son")
                kayit["son"] = fiyat[kod]
                if onceki:
                    kayit["gunluk"] = round((fiyat[kod] / onceki - 1) * 100, 2)
            kayitlar.append(kayit)
            print(f"[Yenile] {kod}: onceki teknik satirdan tamamlandi.")

    bot.hisse_sayfalari_yaz(kayitlar)
    print(f"[Yenile] {gun} verisiyle {len(kayitlar)} hisse sayfasi uretildi.")


if __name__ == "__main__":
    main()
