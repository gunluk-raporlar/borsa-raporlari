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
    "jev-1.13-free",
    "jev-1.13",
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
             "messages": [{"role": "user", "content": SORU}],
             "max_tokens": 500}
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
        icerik = ((secim.get("message") or {}).get("content") or "")
        print(f"[{model}] OK {dt:.1f}s finish={secim.get('finish_reason')} "
              f"alan-model={d.get('model')!r} usage={d.get('usage')}")
        print(f"[{model}] CEVAP: {icerik[:400]!r}")
    except urllib.error.HTTPError as e:
        print(f"[{model}] HTTP {e.code} ({time.time()-t0:.0f}s) {e.read().decode()[:160]!r}")
    except Exception as e:
        print(f"[{model}] HATA {type(e).__name__}: {str(e)[:100]} ({time.time()-t0:.0f}s)")


SORULAR = [
    "Does this market condition suggest bullish sentiment? Index up 1.2% at 9850, USD/TRY stable at 34.20. Answer yes or no.",
]

if __name__ == "__main__":
    if not os.environ.get("OPENCODE_API_KEY"):
        raise SystemExit("OPENCODE_API_KEY yok")
    for i, soru in enumerate(SORULAR, 1):
        print(f"--- SORU {i}: {soru[:60]}")
        SORU = soru
        for m in [m for m in MODELLER if m in ("space-bunny-free", "fledge-alpha-free")]:
            dene(m)
    # Parmak izi kiyasi: gercek GLM ayni sorulari
    if os.environ.get("ZAI_API_KEY"):
        print("=== GLM KARSILASTIRMA (z.ai glm-5.3-flash) ===")
        import urllib.request as u2
        for i, soru in enumerate(SORULAR[:3], 1):
            govde = {"model": "glm-5.3-flash",
                     "messages": [{"role": "user", "content": soru}],
                     "max_tokens": 300}
            req = u2.Request("https://api.z.ai/api/coding/paas/v4/chat/completions",
                             data=json.dumps(govde).encode(), method="POST",
                             headers={"Authorization": f"Bearer {os.environ['ZAI_API_KEY']}",
                                      "Content-Type": "application/json"})
            try:
                with u2.urlopen(req, timeout=120) as r:
                    d = json.loads(r.read().decode())
                secim = (d.get("choices") or [{}])[0]
                icerik = ((secim.get("message") or {}).get("content") or "")[:400]
                print(f"[glm-5.3-flash] S{i} alan-model={d.get('model')!r}")
                print(f"[glm-5.3-flash] CEVAP: {icerik!r}")
            except Exception as e:
                print(f"[glm-5.3-flash] S{i} HATA: {type(e).__name__} {str(e)[:100]}")
