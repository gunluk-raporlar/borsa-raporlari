"""Z.ai glm-5.3-flash thinking parametresi biçim yoklaması (geçici tanı aracı).

1210 ("cannot be disabled; please use low, high, or max") hatasının hangi
biçimde çözüldüğünü bulur: param yok / obj low / string low / reasoning_effort /
obj enabled. Anahtar ASLA yazdırılmaz; yalnızca durum kodu + yanıt özeti basılır.
"""
import json
import os
import urllib.request
import urllib.error

url = "https://api.z.ai/api/paas/v4/chat/completions"


def dene(etiket, ek_alanlar):
    govde = {"model": "glm-5.3-flash",
             "messages": [{"role": "user", "content": "1+1 kac? sadece sayiyi yaz"}],
             "max_tokens": 512}
    govde.update(ek_alanlar)
    req = urllib.request.Request(url, data=json.dumps(govde).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['ZAI_API_KEY']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode())
        icerik = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
        print(f"[{etiket}] OK | cevap: {str(icerik)[:40]!r} | usage: {d.get('usage')}")
    except urllib.error.HTTPError as e:
        print(f"[{etiket}] HTTP {e.code} | {e.read().decode()[:180]}")
    except Exception as e:
        print(f"[{etiket}] HATA: {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    if not os.environ.get("ZAI_API_KEY"):
        raise SystemExit("ZAI_API_KEY yok")
    dene("A: param yok", {})
    dene("B: obj low", {"thinking": {"type": "low"}})
    dene("C: string low", {"thinking": "low"})
    dene("D: reasoning_effort", {"reasoning_effort": "low"})
    dene("E: obj enabled", {"thinking": {"type": "enabled"}})
