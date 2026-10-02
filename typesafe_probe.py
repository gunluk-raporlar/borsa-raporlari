"""Typesafe Jev (System One) yoklamasi — gercel haber basliklariyla.

bot.py'nin keyword tabanli haber eleme yerine Jev noul/choice kapisi
oneriliyor; bu probe gercek gunun basliklarini API'ye gonderip
- gecikme, token kullanimi
- "BIST 30 / makro etkiler mi?" noul degeri
- duygu choice etiketi (olumlu/olumsuz/notr)
olculerini toplar. Anahtar ASLA yazdirilmaz.
"""
import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
# Haberin bulundugu gun (CI'da repo checkout'u bugunun dosyasini icerir)
HABER_GUNU = os.environ.get("HABER_GUNU", "2026-10-02")
SECILEN = int(os.environ.get("HABER_ADET", "8"))


def sor(baslik):
    govde = {
        "state": baslik,
        "model": "jev-latest",
        "questions": {
            "bist30_etkiler": {
                "type": "noul",
                "instructions": ("Bu haber BIST 30 borsasindaki bir sirketi veya "
                                 "Turkiye makro ekonomik gorunumunu (faiz, "
                                 "enflasyon, kur, buyume, enerji) dogrudan "
                                 "etkileyebilir nitelikte"),
            },
            "duygu": {
                "type": "choice",
                "instructions": "Haberin ilgili piyasaya duygu yonelimini siniflandir",
                "criteria": {
                    "olumlu": "Sirket kazanc, sozlesme, yatirim veya makro iyilesme sinyali",
                    "olumsuz": "Kayip, ceza, gerileme veya makro bozulma sinyali",
                    "notr": "Duygu yonu belirgin degil veya yalnizca bilgilendirici",
                },
            },
        },
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(govde).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        dt = time.time() - t0
        a = d.get("answers", {})
        etk = a.get("bist30_etkiler", {})
        duy = a.get("duygu", {})
        print(f"  {dt*1000:.0f} ms | etkiler={etk.get('noul')} "
              f"| duygu={duy.get('choice')} (guven={duy.get('confidence')}) "
              f"| token={d.get('usage', {}).get('input_tokens')}")
        print(f"  BASLIK: {baslik[:100]}")
        return True
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} ({time.time()-t0:.1f}s) {e.read().decode()[:160]!r}")
        print(f"  BASLIK: {baslik[:100]}")
        return False
    except Exception as e:
        print(f"  HATA {type(e).__name__}: {str(e)[:120]}")
        return False


if __name__ == "__main__":
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY yok")
    yol = os.path.join("data", "news", f"{HABER_GUNU}.json")
    haberler = json.load(open(yol, encoding="utf-8"))
    sec = haberler[:SECILEN]
    print(f"{len(sec)}/{len(haberler)} gercel baslik Jev'ye gonderiliyor "
          f"({HABER_GUNU})...\n")
    ok = sum(bool(sor(h)) for h in sec)
    print(f"\nSonuc: {ok}/{len(sec)} basarili")
