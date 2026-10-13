"""NVIDIA MODEL HIZ PROBU (2026-10-13).

SORU: "NVIDIA'in free modellerinden hizli yanit verenleri nasil buluruz?"
CEVAP: Tahmin etmeyelim — /models ucundan listeyi cek, her modele TEK kisa
       istek at, yanit suresini olc, sirala.

Bu arac ceviri kalitesi icin de kontrol eder: hizli model ayni zamanda
CEVIRIYOR mu? Kucuk model hizlidir ama uydurabilir; o yuzden sadece sure degil
gecerli ceviri sayisi da raporlanir.

Calistirma:  python -u nvidia_hiz_testi.py
Ortam:       NVIDIA_API_KEY (ve istege bagli NVIDIA2_API_KEY) tanimli olmali.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import i18n  # noqa: E402

# Ceviri kalitesini olcek gercek turkce finance dizeler.
# Kucuk modellerin uydurma yapip yapmadigini gormek icin sayilar/IBOR korunmali.
ORNEK = [
    "Gunluk BIST 30 raporu: destek ve direnc seviyeleri",
    "BIST 30 endeksi gunu %2,45 oraninda yukselerek 14.200 puandan kapatti",
    "Islem hacmi 12,4 milyar TL olarak gerceklesti",
]

SISTEM = ("You are a professional translator for a Turkish finance website. "
          "The user sends a JSON array of strings. Translate EVERY item into English. "
          "Return ONLY a JSON array of the exact same length — no explanations, "
          "no markdown fences. Every element must be a JSON string.")

TIMEOUT = 90.0
# Ceviri isine yaramayacak kadar buyuk modelleri ele (yavas + parazit).
MIN_MODEL = 8.0          # milyar parametre
# Bunlar dilden cok kod/soz oldugu icin ceviri denemesi anlamli degil.
ELENECEK = ("nemotron-3-nano", "embed", "rerank", "retrieval", "vision",
            "vl", "-vl-", "code", "guard", "clip", "llama-3.2-1b", "llama-3.2-3b")


def modelleri_listele(uc, anahtar):
    istek = urllib.request.Request(
        uc, headers={"Authorization": f"Bearer {anahtar}", "Accept": "application/json"})
    with urllib.request.urlopen(istek, timeout=30) as y:
        return [m.get("id") for m in json.loads(y.read().decode("utf-8")).get("data", [])]


def parametre_sayi(model_id):
    """'nemotron-3-ultra-550b-a55b' -> 550.0 ; 'llama-3.3-70b' -> 70.0"""
    ad = model_id.lower()
    i = ad.rfind("-")
    if i < 0:
        return 0.0
    parca = ad[i + 1:]
    sayi = ""
    for ch in parca:
        if ch.isdigit():
            sayi += ch
        elif sayi and ch == "b":
            break
        elif sayi:
            break
    try:
        return float(sayi) / 1000.0 if sayi else 0.0   # 550b -> 550 ; 70b -> 70
    except ValueError:
        return 0.0


def olc(ad, url, anahtar, model_id):
    bas = time.time()
    try:
        ham = i18n.Cevirmen._llm_istek(url, anahtar, model_id, SISTEM, ORNEK, TIMEOUT)
    except Exception as e:
        return (999.0, 0, str(e)[:70])
    ms = (time.time() - bas) * 1000
    gecerli = sum(1 for k, v in zip(ORNEK, ham) if i18n.ceviri_gecerli_mi(k, v))
    return (ms, gecerli, "")


def bir_anahtari_probe(ad, url, anahtar, limit):
    print(f"\n{'='*78}\n{ad}\n{'='*78}")
    try:
        liste = modelleri_listele(url.replace("/chat/completions", "/models"), anahtar)
    except Exception as e:
        print(f"  model listesi alinamadi: {e}")
        return []
    adaylar = []
    for m in liste:
        ml = m.lower()
        if any(x in ml for x in ELENECEK):
            continue
        b = parametre_sayi(m)
        if b and b < MIN_MODEL:
            continue
        adaylar.append((b, m))
    # Buyukten kucuge: buyuk model = kalite, kucuk model = hiz. Ikisini de olcuyoruz.
    adaylar.sort(key=lambda t: -t[0])
    adaylar = adaylar[:limit]
    print(f"  {len(liste)} model listelendi, {len(adaylar)} aday deneniyor\n")
    print(f"  {'model':52s} {'sure':>9s}  ceviri")
    print(f"  {'-'*52} {'-'*9}  ------")
    sonuclar = []
    for _, m in adaylar:
        ms, gecerli, hata = olc(ad, url, anahtar, m)
        if hata:
            print(f"  {m[:52]:52s} {'HATA':>9s}  {hata}")
            continue
        isaret = "OK" if gecerli == len(ORNEK) else f"{gecerli}/{len(ORNEK)} kotu"
        print(f"  {m[:52]:52s} {ms:8.0f}ms  {isaret}")
        sonuclar.append((ms, m, gecerli))
    return sonuclar


def main():
    print("NVIDIA model hiz probu — her modele tek kisa istek, yanit suresi olculur.")
    print(f"Kontrol dizeleri ({len(ORNEK)} adet), zaman asimi {TIMEOUT:.0f} sn")
    tum = []
    for ad, ortam in (("NVIDIA", "NVIDIA_API_KEY"), ("NVIDIA2", "NVIDIA2_API_KEY")):
        anahtar = os.environ.get(ortam)
        if not anahtar:
            print(f"\n{ad}: {ortam} tanimli degil, atlandi")
            continue
        uc = "https://integrate.api.nvidia.com/v1/chat/completions"
        tum += [(ad, *r) for r in bir_anahtari_probe(ad, uc, anahtar, limit=12)]

    if not tum:
        print("\nHiçbir model yanit vermedi.")
        return 1
    print(f"\n{'='*78}\nHIZ SIRASI (ceviri kalitesiyle birlikte)\n{'='*78}")
    for ad, ms, m, g in sorted(tum, key=lambda t: t[1]):
        kalite = "tam" if g == len(ORNEK) else f"{g}/{len(ORNEK)}"
        print(f"  {ad:9s} {ms:8.0f}ms  kalite={kalite:5s}  {m}")
    print("\nI18N_NVIDIA_MODELS=model1,model2  ile i18n.py'ye sirayi sabitleyebilirsin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())