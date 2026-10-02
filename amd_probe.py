"""AMD model yoklamasi (gecici tani araci — zai_probe.py deseni).

Onerilen 3 yeni model + mevcut havuz referanslarina TEKER TEKER birer
kisa istek atar: durum + gecikme + finish_reason + yanit ilk satiri.
Anahtar ASLA yazdirilmaz. once /models listesi de basilir (katalog
adlari zamanla degisebildigi icin tam kimlik buradan dogrulanir).
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://developer.amd.com.cn/radeon/api/v1"
# Ilk ikisi mevcut havuz (referans), sonraki ucusu kullanici onerisi
MODELLER = [
    "DeepSeek-V4-Flash",
    "GLM-5.3-Flash",
    "DeepSeek-V4.1-Flash",
    "DeepSeek-V4-Flash-Vision-Exp",
    "MiMo-V2.6-Flash",
]


def katalog():
    req = urllib.request.Request(BASE + "/models",
                                 headers={"Authorization": f"Bearer {os.environ['AMD_API_KEY']}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        adlar = sorted(m.get("id", "") for m in d.get("data", []))
        ilgi = [a for a in adlar if any(k in a.lower() for k in
                ("deepseek", "mimo", "glm", "qwen"))]
        print(f"Katalogda {len(adlar)} model; ilgilenenler: {ilgi}")
    except Exception as e:
        print("Katalog alinamadi:", type(e).__name__, str(e)[:120])


def dene(model):
    govde = {"model": model,
             "messages": [{"role": "user", "content": "1+1 kac? sadece sayiyi yaz"}],
             "max_tokens": 32}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(govde).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['AMD_API_KEY']}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            d = json.loads(r.read().decode())
        dt = time.time() - t0
        secim = (d.get("choices") or [{}])[0]
        icerik = ((secim.get("message") or {}).get("content") or "")[:40]
        print(f"[{model}] OK {dt:.1f}s finish={secim.get('finish_reason')} cevap={icerik!r}")
    except urllib.error.HTTPError as e:
        print(f"[{model}] HTTP {e.code} ({time.time()-t0:.0f}s) {e.read().decode()[:140]!r}")
    except Exception as e:
        print(f"[{model}] HATA {type(e).__name__}: {str(e)[:100]} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    if not os.environ.get("AMD_API_KEY"):
        raise SystemExit("AMD_API_KEY yok")
    katalog()
    for m in MODELLER:
        dene(m)
