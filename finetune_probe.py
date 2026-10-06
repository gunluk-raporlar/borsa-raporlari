"""qwen7b-finmath (lokal Ollama fine-tune) — sitemizin kalite caplariyla.

Test 1: sayi-sadakati (verilen rakamlarla Turkce ozet; uydurma denetimi)
Test 2: 6-bolum yapisal tamlik (derin analiz formati)
Test 3: finans hesap (v10'un egitim alani: NPV)
Model: lokal Ollama http://localhost:11434 (ucretsiz, kotasiz)
"""
import json
import os
import re
import time
import urllib.request

BASE = "http://localhost:11434/v1"
MODEL = os.environ.get("FINETUNE_MODEL", "qwen7b-finmath:latest")

VERILER = {
    "endeks": "15.218,47", "endeks_degisim": "%2,54",
    "usdtry": "48,98", "gram_altin_tl": "6.554,78",
    "hacim": "68,4 milyar TL",
}

SAYI_PROMPT = (
    "Su verileri kullanarak tek paragraflik Turkce BIST 30 piyasa ozeti yaz:\n"
    "Endeks 15.218,47 (gunluk %2,54 artis), USD/TRY 48,98, gram altin "
    "6.554,78 TL seviyesinde, islem hacmi 68,4 milyar TL.\n"
    "Verilen sayilardan baskasini KULLANMA; kendi bilginden kur/fiyat DUZELTME; "
    "tam sayilar virgullu Turkce bicimde kalsin."
)

YAPI_PROMPT = (
    "Turkiye piyasalari icin kisa bir gun sonu analizi yaz. KESIN su 6 basligi "
    "kullan, her biri '## N.' biciminde ve 2-3 cumle yeterli:\n"
    "## 1. Piyasa Ozeti\n## 2. Sirket Haberleri\n## 3. Teknik Görünüm\n"
    "## 4. Makro Gostergeler\n## 5. Riskler\n## 6. Sonuc\n"
    "Sadece bu basliklar; tablo, giris cumlesi ve kapanis EKLEME. Sayi uydurma; "
    "veri yoksa niteliksel yaz."
)

HESAP_PROMPT = (
    "Bugun 1.000 TL yatirim yapiyoruz, 1 yil sonra 1.100 TL alacagiz. "
    "Iskonto orani %8 ise NPV kactir? Adim adim hesapla ve sonucu "
    "'NPV = X TL' biciminde ver."
)
HESAP_DOGRU = 1100 / 1.08 - 1000  # 18.52


def sor(soru, max_tokens=900):
    govde = {"model": MODEL,
             "messages": [{"role": "user", "content": soru}],
             "max_tokens": max_tokens, "temperature": 0.3}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(govde).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode())
        dt = time.time() - t0
        secim = (d.get("choices") or [{}])[0]
        icerik = (secim.get("message") or {}).get("content") or ""
        u = d.get("usage", {})
        print(f"  OK {dt:.1f}s finish={secim.get('finish_reason')} "
              f"token: giris={u.get('prompt_tokens')} cikis={u.get('completion_tokens')}")
        return icerik
    except Exception as e:
        print(f"  HATA {type(e).__name__}: {str(e)[:150]}")
        return None


def sayi_denetle(metin):
    sorunlar = []
    for ad, deger in VERILER.items():
        if deger not in metin:
            sorunlar.append(f"{ad}={deger} YOK")
    izinli = set()
    for deger in VERILER.values():
        for m in re.finditer(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?", deger):
            izinli.add(m.group(0))
    izinli |= {"2026", "30"}
    for m in re.finditer(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?", metin):
        if m.group(0) not in izinli:
            sorunlar.append(f"uydurma olasilikli: {m.group(0)!r}")
    return sorunlar


def yapisal_denetle(metin):
    eksik = [n for n in range(1, 7) if not re.search(rf"^##\s*{n}\.", metin, re.M)]
    return [f"## {n}. bolum yok" for n in eksik]


if __name__ == "__main__":
    print(f"MODEL: {MODEL} (lokal Ollama)\n")

    print("--- TEST 1: sayi-sadakati")
    c1 = sor(SAYI_PROMPT)
    if c1:
        print("  CEVAP:", c1[:300].replace("\n", " "))
        s = sayi_denetle(c1)
        print("  SAYI DENETIMI:", "TEMIZ" if not s else f"HATA -> {s}")

    print("--- TEST 2: yapisal tamlik (6 bolum)")
    c2 = sor(YAPI_PROMPT)
    if c2:
        print("  CEVAP:", c2[:300].replace("\n", " "))
        s = yapisal_denetle(c2)
        print("  YAPI DENETIMI:", "TAM" if not s else f"EKSIK -> {s}")

    print(f"--- TEST 3: NPV hesabi (dogru cevap ≈ {HESAP_DOGRU:.2f} TL)")
    c3 = sor(HESAP_PROMPT)
    if c3:
        print("  CEVAP:", c3[:400].replace("\n", " "))
        m = re.search(r"NPV\s*=?\s*(-?[\d.,]+)", c3)
        if m:
            bulunan = float(m.group(1).replace(".", "").replace(",", "."))
            fark = abs(bulunan - HESAP_DOGRU)
            print(f"  BULUNAN: {bulunan:.2f} | dogru: {HESAP_DOGRU:.2f} | "
                  f"fark: {fark:.2f} -> {'DOGRU' if fark < 1 else 'HATALI'}")
        else:
            print("  'NPV =' satiri bulunamadi")
