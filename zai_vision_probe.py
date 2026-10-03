"""GLM-5.3-flash VISION yoklamasi: arayuzde 'vision' yaziyor — dogru mu?

ates_kadin pasaportu (bakir kirmizi sacli kadin) gonderilir; modelin
cevabinda kirmizi saç/kadin gecmiyorsa goruntuyu GORMUYOR demektir.
Hem coding ucu hem standart uc denenir. Anahtar yazdirilmaz.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

SORU = ("Bu goruntude ne goruyorsun? Kisinin sac rengini ve turunu tek "
        "cumlede acikla; goruntu goremiyorsan bunu acikca soyle.")
IMG = r"C:/Users/pc/dizi-stil/kimlikler/ates_kadin.jpg"

UCULAR = [
    ("coding", "https://api.z.ai/api/coding/paas/v4/chat/completions"),
    ("standart", "https://api.z.ai/api/paas/v4/chat/completions"),
]


def dene(etiket, url):
    b64 = base64.b64encode(open(IMG, "rb").read()).decode()
    govde = {
        "model": "glm-5.3-flash",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": SORU},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        "max_tokens": 300,
    }
    req = urllib.request.Request(
        url, data=json.dumps(govde).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['ZAI_API_KEY']}",
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode())
        secim = (d.get("choices") or [{}])[0]
        icerik = ((secim.get("message") or {}).get("content") or "")[:400]
        print(f"[{etiket}] OK {time.time()-t0:.1f}s CEVAP: {icerik!r}")
        kirmizi = any(k in icerik.lower() for k in ("kırmızı", "kizil", "red", "bakır", "copper"))
        print(f"[{etiket}] SAÇ RENGİ GÖRDÜ MÜ: {'EVET' if kirmizi else 'HAYIR'}")
    except urllib.error.HTTPError as e:
        print(f"[{etiket}] HTTP {e.code} ({time.time()-t0:.1f}s) {e.read().decode()[:200]!r}")
    except Exception as e:
        print(f"[{etiket}] HATA {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    if not os.environ.get("ZAI_API_KEY"):
        raise SystemExit("ZAI_API_KEY yok")
    if not os.path.exists(IMG):
        raise SystemExit(f"test goruntusu yok: {IMG}")
    for etiket, url in UCULAR:
        dene(etiket, url)
