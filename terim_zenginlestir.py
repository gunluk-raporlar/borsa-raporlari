# -*- coding: utf-8 -*-
"""IFRS muhasebe sozlugunden (autoclaw CSV) terim zenginlestirmesi cikarir:
EN tanim + TR ornek cumle + EN ornek cumle.

Kaynak: autoclaw workspace — muhasebe-sozlugu/muhasebe-terimleri.csv
(1691 kayit; alanlar: Bolum, Ingilizce_Terim, Turkce_Anlami, Ingilizce_Tanim,
Ornek_Ingilizce, Ornek_Turkce; kaynak: muhasebenews.com IFRS sozluguki).

Cikti: data/terimler/zengin.jsonl — {"terim": <sozlukteki TR terim>,
"en_tanim": ..., "ornek": ..., "ornek_en": ...}. terimler_yaz() bu dosyayi
varsa sayfalara ekler.

Kullanim: python terim_zenginlestir.py [csv yolu]
"""
import csv
import json
import os
import re
import sys

VARSAYILAN_KAYNAK = os.path.join(
    os.path.expanduser("~"), ".openclaw-autoclaw", "workspace", "muhasebe-sozlugu",
    "muhasebe-terimleri.csv")


def _temiz(metin):
    return re.sub(r"\s+", " ", metin or "").strip()


def kaynaktan_terimler(yol):
    """CSV kayitlarini sozluk bicimine cevirir."""
    terimler = []
    with open(yol, encoding="utf-8-sig", newline="") as f:
        for satir in csv.DictReader(f):
            terimler.append({
                "en_ad": _temiz(satir.get("Ingilizce_Terim")),
                "tr_ad": _temiz(satir.get("Turkce_Anlami")),
                "en_tanim": _temiz(satir.get("Ingilice_Tanim")
                                   or satir.get("Ingilizce_Tanim")),
                "ornek": _temiz(satir.get("Ornek_Turkce")),
                "ornek_en": _temiz(satir.get("Ornek_Ingilizce")),
            })
    return terimler


def main():
    kaynak = sys.argv[1] if len(sys.argv) > 1 else VARSAYILAN_KAYNAK
    terimler = kaynaktan_terimler(kaynak)
    print(f"Kaynak: {len(terimler)} IFRS terimi okundu.")

    # Sozlukteki terimlerle esle: once TR ad, bulunamazsa terim_en alanina gore.
    import bot  # _terim_yukle sozlugu ayni temizlikle verir
    sozluk = bot._terim_yukle()
    tr_indeks = {v["terim"].casefold(): v["terim"] for v in sozluk}
    en_indeks = {v["en"].casefold(): v["terim"] for v in sozluk if v["en"]}

    zengin, gorulen = [], set()
    istatistik = {"tr": 0, "en": 0, "atlandi": 0}
    for t in terimler:
        tr_es = tr_indeks.get(t["tr_ad"].casefold())
        en_es = en_indeks.get(t["en_ad"].strip("[]").casefold())
        hedef = tr_es or en_es
        if not hedef:
            istatistik["atlandi"] += 1
            continue
        if hedef in gorulen:
            continue
        gorulen.add(hedef)
        istatistik["tr" if tr_es else "en"] += 1
        zengin.append({
            "terim": hedef,
            "en_tanim": t["en_tanim"],
            "ornek": t["ornek"],
            "ornek_en": t["ornek_en"],
        })

    with open(os.path.join("data", "terimler", "zengin.jsonl"), "w", encoding="utf-8") as f:
        for z in zengin:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")
    print(f"Eslesen terim: {len(zengin)} (TR adindan {istatistik['tr']}, EN addan {istatistik['en']}; "
          f"{istatistik['atlandi']} sozlukte karsiligi yok).")
    print("data/terimler/zengin.jsonl yazildi.")


if __name__ == "__main__":
    main()
