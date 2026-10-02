"""OpenCode Zen yoklamasi (gecici tani araci — amd_probe.py deseni).

Anahtar /zen/v1/models listesini ve 1-2 ucretsiz modele tek istek atar;
durum + gecikme + yanit ilk satiri basar. Anahtar ASLA yazdirilmaz.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://opencode.ai/zen/v1"
# Ucretsiz katman (docs 2026-10-02); jev-1.13-free ozel /systemone ucugu
# kullandigi icin listede yok.
MODELLER = [
    "mimo-v2.6-flash-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "big-pickle",
    "space-bunny-free",
    "longcat-2.5-preview-free",
]


def katalog():
    req = urllib.request.Request(BASE + "/models",
                                 headers={"Authorization": f"Bearer {os.environ['OPENCODE_API_KEY']}", "User-Agent": "opencode/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        veri = d.get("data", d if isinstance(d, list) else [])
        adlar = sorted(m.get("id", "") if isinstance(m, dict) else str(m) for m in veri)
        ucretsiz = [a for a in adlar if "free" in a or a in ("big-pickle", "space-bunny")]
        print(f"Katalogda {len(adlar)} model; ucretsiz adaylar: {ucretsiz}")
    except urllib.error.HTTPError as e:
        print(f"Katalog HTTP {e.code}: {e.read().decode()[:160]!r}")
    except Exception as e:
        print("Katalog alinamadi:", type(e).__name__, str(e)[:120])


def dene(model):
    govde = {"model": model,
             "messages": [{"role": "user", "content": "1+1 kac? sadece sayiyi yaz"}],
             "max_tokens": 64}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(govde).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['OPENCODE_API_KEY']}",
                 "Content-Type": "application/json", "User-Agent": "opencode/1.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            d = json.loads(r.read().decode())
        dt = time.time() - t0
        secim = (d.get("choices") or [{}])[0]
        icerik = ((secim.get("message") or {}).get("content") or "")[:40]
        print(f"[{model}] OK {dt:.1f}s finish={secim.get('finish_reason')} cevap={icerik!r}")
    except urllib.error.HTTPError as e:
        print(f"[{model}] HTTP {e.code} ({time.time()-t0:.0f}s) {e.read().decode()[:160]!r}")
    except Exception as e:
        print(f"[{model}] HATA {type(e).__name__}: {str(e)[:100]} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    if not os.environ.get("OPENCODE_API_KEY"):
        raise SystemExit("OPENCODE_API_KEY yok")
    katalog()
    for m in MODELLER:
        dene(m)
