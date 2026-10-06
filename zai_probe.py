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

# (etiket, tamam_mi, kod/asinin_turu) — sonda net hüküm basmak için.
SONUCLAR = []


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
        SONUCLAR.append((etiket, True, "OK"))
    except urllib.error.HTTPError as e:
        print(f"[{etiket}] HTTP {e.code} | {e.read().decode()[:180]}")
        SONUCLAR.append((etiket, False, e.code))
    except Exception as e:
        print(f"[{etiket}] HATA: {type(e).__name__}: {str(e)[:120]}")
        SONUCLAR.append((etiket, False, type(e).__name__))


if __name__ == "__main__":
    if not os.environ.get("ZAI_API_KEY"):
        raise SystemExit("ZAI_API_KEY yok")
    dene("A: param yok", {})
    dene("B: obj low", {"thinking": {"type": "low"}})
    dene("C: string low", {"thinking": "low"})
    dene("D: reasoning_effort", {"reasoning_effort": "low"})
    dene("E: obj enabled", {"thinking": {"type": "enabled"}})

    # Coding plan ucu: ZCode'un kullandigi haftalik plan kotasi bu uctan sayilir.
    url2 = "https://api.z.ai/api/coding/paas/v4/chat/completions"

    def dene2(etiket, ek_alanlar):
        global url
        eski = url
        url = url2
        try:
            dene(etiket, ek_alanlar)
        finally:
            url = eski

    dene2("F: coding, param yok", {})
    dene2("G: coding, obj low", {"thinking": {"type": "low"}})
    dene("H: std, obj LOW", {"thinking": {"type": "LOW"}})

    # Belgeler (docs.z.ai/devpack/quick-start): Coding Plan ucaklari —
    #   https://api.z.ai/api/coding/paas/v4 -> OpenAI Chat Completions (bot bunu kullanir)
    #   https://api.z.ai/api/v1             -> OpenAI RESPONSES protokolu (farkli istek sekli)
    #   https://api.z.ai/api/anthropic      -> Anthropic protokolu
    # I varyanti, kullanici anahtariyla api/v1 ucunu (Responses sekli) dogrular:
    # adres yanlis degil, protokol farkli — ikisi de ayni abonelikten sayilir.
    print("--- I: api/v1 (Responses protokolu) ---")
    try:
        govde = {"model": "glm-5.3-flash",
                 "input": [{"role": "user", "content": "1+1 kac? sadece sayiyi yaz"}]}
        req = urllib.request.Request("https://api.z.ai/api/v1/responses",
                                     data=json.dumps(govde).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {os.environ['ZAI_API_KEY']}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode())
        print("[I: api/v1 responses] OK | ilk 240 karakter:", json.dumps(d)[:240])
        SONUCLAR.append(("I: api/v1", True, "OK"))
    except urllib.error.HTTPError as e:
        print(f"[I: api/v1 responses] HTTP {e.code} | {e.read().decode()[:180]}")
        SONUCLAR.append(("I: api/v1", False, e.code))
    except Exception as e:
        print(f"[I: api/v1 responses] HATA: {type(e).__name__}: {str(e)[:120]}")
        SONUCLAR.append(("I: api/v1", False, type(e).__name__))

    # Net hüküm: bot (bot.py) yalnızca CODING ucunu kullanır; standart ucun
    # 429 bakiye hatası botu etkilemez (2026-10-06 karışıklık sonrası eklendi).
    coding_sonuc = [(ok, code) for e, ok, code in SONUCLAR if e.startswith(("F:", "G:"))]
    if any(ok for ok, _ in coding_sonuc):
        print("SONUC: Coding paketi CALISIYOR — botun GLM yolu saglikli "
              "(standart uc 429 verebilir; bot onu kullanmiyor).")
    elif coding_sonuc and all(code == 429 for _, code in coding_sonuc):
        print("SONUC: Coding paketi 429 (bakiye/kota) — botun GLM yolu cokmus, "
              "AMD yedegi devreye girecek.")
    else:
        print("SONUC: Coding paketi erisilemez — yukaridaki F/G satirlarina bakin.")
