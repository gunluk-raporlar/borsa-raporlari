# -*- coding: utf-8 -*-
"""Elle yazilmis terim cevirilerini i18n-elle.json'a isler.

Dosya bicimi (tek dil): {terim: "ceviri"} — dil dosya adindan okunur:
  elle_en_p2.json -> dil=en, elle_ru_p7.json -> dil=ru vb.

Terim adi _terim_yukle()'deki kayitla eslestirilir; kaynak anahtar, i18n.py'in
kullandigi bicimle BIREBIR ayni uretilir: sha1(tanim.encode('utf-8')).hexdigest()[:20].
Boylece ceviri kuyrugu bu dizelere hic dokunmaz (elle > onbellek > saglayici).

Kullanim: python elle_ekle.py elle_en_p2.json [elle_de_p2.json ...]
"""
import hashlib
import json
import os
import re
import shutil
import sys

ELLE = "i18n-elle.json"


def _anahtar(dil, metin):
    return f"{dil}:{hashlib.sha1(metin.encode('utf-8')).hexdigest()[:20]}"


def _dosya_dili(yol):
    m = re.search(r"elle_([a-z]{2})_", os.path.basename(yol))
    if not m or m.group(1) not in ("en", "de", "ru", "zh"):
        raise SystemExit(f"Dil dosya adinda okunamadi (elle_<dil>_...): {yol}")
    return m.group(1)


def main():
    import bot
    sozluk = {v["terim"]: v["tanim"] for v in bot._terim_yukle()}
    elle = {}
    try:
        elle = json.load(open(ELLE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if os.path.exists(ELLE):
        shutil.copy(ELLE, ELLE + ".yedek")

    toplam, hata = 0, []
    for yol in sys.argv[1:]:
        dil = _dosya_dili(yol)
        parti = json.load(open(yol, encoding="utf-8"))
        eklenti = 0
        for terim, ceviri in parti.items():
            kaynak = sozluk.get(terim)
            if kaynak is None:
                if terim not in hata:
                    hata.append(terim)
                continue
            if not ceviri:
                continue
            k = _anahtar(dil, kaynak)
            if elle.get(k) != ceviri:
                elle[k] = ceviri
                eklenti += 1
        toplam += eklenti
        print(f"{yol} ({dil}): {len(parti)} terim, {eklenti} yeni anahtar.")
    with open(ELLE, "w", encoding="utf-8") as f:
        json.dump(elle, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"Toplam {toplam} ceviri anahtari islendi (toplam elle kaydi: {len(elle)}).")
    if hata:
        print("Sozlukte bulunamayan terimler:", "; ".join(hata[:10]))


if __name__ == "__main__":
    main()
