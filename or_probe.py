"""OpenRouter anahtar yoklamasi (gecici tani araci).

Anahtarin hangi hesaba/etikete ait oldugunu, kullanim sayacini ve gercek
bir free-model cagrisinin sonucunu gosterir. Kullanici panelinde "kullanim
yok" gorurken hattin gercekte kullanildigini kanitlamak icin (2026-10-07:
gunluk makro raporu OR uzerinden yazilmisti — nemotron-3-ultra:free).
Anahtar ASLA yazdirilmaz (yalnizca ilk 8 karakter on eki).
"""
import json
import os
import urllib.request

BASE = "https://openrouter.ai/api/v1"


def _get(yol):
    req = urllib.request.Request(
        BASE + yol,
        headers={"Authorization": f"Bearer {os.environ['OR_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    anahtar = os.environ.get("OR_API_KEY", "")
    if not anahtar:
        raise SystemExit("OR_API_KEY yok")
    print("anahtar on-eki:", anahtar[:8] + "...")

    bilgi = _get("/auth/key").get("data", {})
    print("anahtar etiketi:", bilgi.get("label"))
    print("toplam kullanim: $", bilgi.get("usage"))
    print("harcama limiti:", bilgi.get("limit"))
    print("free-tier anahtar mi:", bilgi.get("is_free_tier"))

    govde = {"model": "nvidia/nemotron-3.5-lightning:free",
             "messages": [{"role": "user",
                           "content": "Yalnizca 'Merhaba' yaz."}],
             "max_tokens": 20}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(govde).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {anahtar}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode())
    icerik = ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    print(f"test cagrisi OK: model={d.get('model')!r} yanit={icerik[:60]!r}")
    print("bu cagri artik panel Activity listesinde gorunmeli.")


if __name__ == "__main__":
    main()
