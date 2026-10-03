"""Gemini yoklamasi — bizim proje kalite caplariyla.

Test 1 (sayi-sadakati): verilen rakamlarla Turkce piyasa ozeti; uretilen
metindeki sayilar verilenlerle kiyaslanir, disari sayi uydurursa yakalanir.
Test 2 (yapisal): 6 bolumluk mini analiz; bot.py'deki _derin_eksik_mi
mantigiyla ayni bolum-baslik denetimi.
Test 3 (maliyet/limit): usage + finish_reason + gecikme basilir.
Ucretsiz katman ~20 istek/gun (gemini-2.5-flash) — probe 2-4 cagri yeter.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

VERILER = {
    "endeks": "15.218,47", "endeks_degisim": "%2,54",
    "usdtry": "34,20", "altin_usd": "2.450",
    "hacim": "68,4 milyar TL",
}

SAYI_SADAKATI_PROMPT = (
    "Su verileri kullanarak tek paragraflik Turkce BIST 30 piyasa ozeti yaz:\n"
    "Endeks 15.218,47 (gunluk %2,54 artis), USD/TRY 34,20, gram altin karsiligi "
    "2.450 USD seviyesinde, islem hacmi 68,4 milyar TL.\n"
    "Verilen sayilardan baskasini KULLANMA; tam sayilar virgullu Turkce bicimde kalsin."
)

YAPISAL_PROMPT = (
    "Turkiye piyasalari icin kisa bir gun sonu analizi yaz. KESIN su 6 basligi "
    "kullan, her biri '## N.' biciminde ve 2-3 cumle yeterli:\n"
    "## 1. Piyasa Ozeti\n## 2. Sirket Haberleri\n## 3. Teknik Görünüm\n"
    "## 4. Makro Gostergeler\n## 5. Riskler\n## 6. Sonuc\n"
    "Sadece bu basliklar; tablo, giris cumlesi ve kapanis EKLEME. Sayi uydurma; "
    "veri yoksa niteliksel yaz."
)


def sor(soru, max_tokens=700):
    govde = {"model": MODEL,
             "messages": [{"role": "user", "content": soru}],
             "max_tokens": max_tokens, "temperature": 0.3}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(govde).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['GEMINI_API_KEY']}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode())
        dt = time.time() - t0
        secim = (d.get("choices") or [{}])[0]
        icerik = (secim.get("message") or {}).get("content") or ""
        u = d.get("usage", {})
        print(f"  OK {dt:.1f}s finish={secim.get('finish_reason')} "
              f"token: giris={u.get('prompt_tokens')} cikis={u.get('completion_tokens')}")
        return icerik
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} ({time.time()-t0:.1f}s) {e.read().decode()[:200]!r}")
        return None
    except Exception as e:
        print(f"  HATA {type(e).__name__}: {str(e)[:120]}")
        return None


def sayi_denetle(metin):
    """Verilen sayilarin metinde korundugunu, uydurma rakam kalmadigini bakar.

    Izinli parca kumesi = verilen degerlerin icindeki tum sayi parcalari
    (orn. "%2,54" -> "2,54"), boylece modelin ayni sayiyi farkli sonek
    ile yazmasi yanilmiyor.
    """
    sorunlar = []
    for ad, deger in VERILER.items():
        if deger not in metin:
            sorunlar.append(f"{ad}={deger} metinde YOK/bicim bozulmus")
    izinli = set()
    for deger in VERILER.values():
        for m in re.finditer(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?", deger):
            izinli.add(m.group(0))
    izinli |= {"2026"}
    for m in re.finditer(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?", metin):
        parca = m.group(0)
        if parca not in izinli:
            sorunlar.append(f"uydurma olasilikli sayi: {parca!r}")
    return sorunlar


def yapisal_denetle(metin):
    eksik = [n for n in range(1, 7) if not re.search(rf"^##\s*{n}\.", metin, re.M)]
    return [f"## {n}. bolum yok" for n in eksik]


if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY yok")

    print(f"--- TEST 1: sayi-sadakati ({MODEL})")
    c1 = sor(SAYI_SADAKATI_PROMPT, max_tokens=400)
    if c1:
        print("  CEVAP:", c1[:350].replace("\n", " "))
        s = sayi_denetle(c1)
        print("  SAYI DENETIMI:", "TEMIZ" if not s else f"HATA -> {s}")

    print(f"--- TEST 2: yapisal tamlik (6 bolum)")
    c2 = sor(YAPISAL_PROMPT, max_tokens=900)
    if c2:
        print("  CEVAP:", c2[:350].replace("\n", " "))
        s = yapisal_denetle(c2)
        print("  YAPI DENETIMI:", "TAM" if not s else f"EKSIK -> {s}")

    print("--- UYGULAMA NOTU: gpt-oss/GLM kiyasi icin ayni denetimler")
    print("    bot.py'deki uydurma esigi (10+ duzeltme=ret) ve CJK denetimi")
    print("    ile ayni hatta calisir; 20 istek/gun ucretsiz katman sinirli.")
