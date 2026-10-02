"""Ollama Cloud yoklamasi (gecici tani araci — amd_probe.py deseni).

OLLAMA_API_KEY secrets'ta duruyordu ama hictir baglanmamisti (2026-10-02).
/v1/models listesini ceker; buyuk modellerden (kalite politikasina uyan)
ilk 3'une kisa istek atar. Anahtar ASLA yazdirilmaz.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://ollama.com/v1"
TERCIH_ANAHTARLARI = ("glm", "deepseek", "120b", "271b", "671b", "480b", "kimi", "qwen3")


def basliklar():
    return {"Authorization": f"Bearer {os.environ['OLLAMA_API_KEY']}",
            "Content-Type": "application/json", "User-Agent": "ollama-python/1.0"}


def katalog():
    req = urllib.request.Request(BASE + "/models", headers=basliklar())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        veri = d.get("data", [])
        adlar = sorted(m.get("id", "") for m in veri)
        print(f"Katalogda {len(adlar)} model. Tamamı: {adlar}")
        # Buyuk modeller once: tercihe gore sirala
        onerilen = [a for a in adlar if any(k in a.lower() for k in TERCIH_ANAHTARLARI)]
        print("Kalite politikasina uyan adaylar:", onerilen)
        return onerilen[:3]
    except urllib.error.HTTPError as e:
        print(f"Katalog HTTP {e.code}: {e.read().decode()[:200]!r}")
    except Exception as e:
        print("Katalog alinamadi:", type(e).__name__, str(e)[:120])
    return []


def dene(model):
    govde = {"model": model,
             "messages": [{"role": "user", "content": "1+1 kac? sadece sayiyi yaz"}],
             "max_tokens": 64}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(govde).encode(), method="POST",
        headers=basliklar())
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
        dt = time.time() - t0
        secim = (d.get("choices") or [{}])[0]
        icerik = ((secim.get("message") or {}).get("content") or "")[:40]
        print(f"[{model}] OK {dt:.1f}s finish={secim.get('finish_reason')} cevap={icerik!r}")
    except urllib.error.HTTPError as e:
        print(f"[{model}] HTTP {e.code} ({time.time()-t0:.0f}s) {e.read().decode()[:200]!r}")
    except Exception as e:
        print(f"[{model}] HATA {type(e).__name__}: {str(e)[:100]} ({time.time()-t0:.0f}s)")


SORULAR = [
    "Who are you? Which exact model and version are you, and who trained you? One line, no role-play.",
    "你是什么模型？由哪家公司训练？一行回答。",
    "What is your knowledge cutoff date? Answer with the month and year only.",
]

if __name__ == "__main__":
    if not os.environ.get("OLLAMA_API_KEY"):
        raise SystemExit("OLLAMA_API_KEY yok")
    for i, soru in enumerate(SORULAR, 1):
        print(f"--- SORU {i}: {soru[:60]}")
        govde = {"model": "gpt-oss:120b",
                 "messages": [{"role": "user", "content": soru}],
                 "max_tokens": 300}
        req = urllib.request.Request(
            BASE + "/chat/completions", data=json.dumps(govde).encode(), method="POST",
            headers=basliklar())
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode())
            secim = (d.get("choices") or [{}])[0]
            icerik = ((secim.get("message") or {}).get("content") or "")[:400]
            print(f"[gpt-oss:120b] S{i} OK {time.time()-t0:.1f}s")
            print(f"[gpt-oss:120b] CEVAP: {icerik!r}")
        except Exception as e:
            print(f"[gpt-oss:120b] S{i} HATA: {type(e).__name__} {str(e)[:100]}")
