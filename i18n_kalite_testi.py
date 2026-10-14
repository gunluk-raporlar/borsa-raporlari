"""CANLI KALITE TESTI â€” Actions'ta calisir, gercek API cevabini gosterir.

Canli kosum sonucu: yeni_ceviri=12, reddedilen_ceviri=224.
API cevap veriyor ama neredeyse hepsi reddediliyor. Iki olasilik:
  (a) model Turkce donduruyor (kimlik ceviri)
  (b) model bos/bozuk JSON donduruyor
Bu test ikisini AYIRIR ve gercek metinleri loga basar.
"""
import os
import sys
import json
import time
import urllib.error
import urllib.request

import i18n  # noqa: E402

# Gunluk raporda GERCEKTEN bulunan somut dizeler
ORNEK = [
    "Gunluk BIST 30 raporu: destek ve direnc seviyeleri",
    "Haftalik teknik ozet: RSI 68, MACD donusum sinyali verdi",
    "BIST 30 endeksi gunu %2,45 oraninda yukselerek 14.200 puandan kapatti",
    "Islem hacmi 12,4 milyar TL olarak gerceklesti",
]

c = i18n.Cevirmen(provider="llm")
uclar = c._uclari_kur()
print(f"Zincir: {[u[0] for u in uclar]}\n")

# Her ucu TEK TEK dene; ham cevabi yaz (zincir/kalite filtresi YOK)
sistem = ("You are a professional translator for a Turkish finance website. "
          "The user sends a JSON array of strings. Translate EVERY item into English. "
          "Return ONLY a JSON array of the exact same length â€” no explanations, "
          "no markdown fences. Every element must be a JSON string.")

for ev, url, anahtar, modeller in uclar:
    if ev.startswith(("OpenRouter", "OrcaRouter", "Gemini", "Groq")):
        continue          # yogunlukta; gormek icin once hizli/ana uc'lara bak
    for model in modeller:
        z = c.yavas_timeout if ev in c.yavas_uc else (c.orta_timeout if ev in c.orta_uc else c.hizli_timeout)
        bas = time.time()
        try:
            ham = i18n.Cevirmen._llm_istek(url, anahtar, model, sistem, ORNEK, z)
            ms = (time.time() - bas) * 1000
            gecerli = sum(1 for k, v in zip(ORNEK, ham)
                          if i18n.ceviri_gecerli_mi(k, v))
            kimlik = sum(1 for k, v in zip(ORNEK, ham) if str(v).strip() == k.strip())
            bos = sum(1 for v in ham if not str(v).strip())
            print(f"{ev}/{model}: OK {ms:.0f} ms | gecerli={gecerli}/4 "
                  f"kimlik={kimlik} bos={bos}")
            for k, v in zip(ORNEK, ham):
                ok = i18n.ceviri_gecerli_mi(k, v)
                print(f"    [{'OK ' if ok else 'RED'}] {str(v)[:78]!r}")
        except Exception as e:
            ms = (time.time() - bas) * 1000
            print(f"{ev}/{model}: HATA {ms:.0f} ms :: {str(e)[:110]}")
        break      # her ucte sadece ilk model
    print()

# DeepL iki anahtarla: kotasi ve gercek ceviri kalitesi
print("\n--- DeepL ---")
for _ad, _ortam in (("DEEPL", "DEEPL_API_KEY"), ("DEEPL2", "DEEPL2_API_KEY"), ("DEEPL3", "DEEPL3_API_KEY")):
    _k = os.environ.get(_ortam)
    if not _k:
        print(f"{_ad}: {_ortam} tanimli degil")
        continue
    for uc in ("https://api-free.deepl.com", "https://api.deepl.com"):
        try:
            rq = urllib.request.Request(uc + "/v2/usage",
                                        headers={"Authorization": f"DeepL-Auth-Key {_k}"})
            with urllib.request.urlopen(rq, timeout=20) as y:
                u = json.loads(y.read().decode("utf-8"))
            print(f"{_ad} {uc}/v2/usage: OK :: {u}")
            break
        except urllib.error.HTTPError as h:
            print(f"{_ad} {uc}/v2/usage: HTTP {h.code}")
        except Exception as e:
            print(f"{_ad} {uc}/v2/usage: {e}")
    b = time.time()
    try:
        cv = i18n.Cevirmen._deepl(ORNEK, "en", anahtar=_k)
        ms = (time.time() - b) * 1000
        g = sum(1 for s_, v in zip(ORNEK, cv) if i18n.ceviri_gecerli_mi(s_, v))
        print(f"{_ad} ceviri: OK ({ms:.0f} ms) gecerli={g}/{len(ORNEK)}")
        for v in cv:
            print(f"    {str(v)[:74]!r}")
    except urllib.error.HTTPError as h:
        print(f"{_ad} ceviri: HTTP {h.code} ({time.time()-b:.1f} sn)")
    except Exception as e:
        print(f"{_ad} ceviri: HATA {str(e)[:70]}")

# Cloudflare Workers AI: ucretsiz, Actions'tan erisilebilir, DeepSeek-V4-Flash var.
print("\n--- Cloudflare Workers AI ---")
_cf_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
_cf_tok = os.environ.get("CLOUDFLARE_API_TOKEN")
if not (_cf_id and _cf_tok):
    print("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN tanimli degil")
else:
    # IKI UC DENENIR: yerel REST (/ai/run/<model>) ve OpenAI uyumlu (/ai/v1).
    # 403 -> anahtar yetkisi eksik;  404 -> adres yanlis.
    # DENENENLER: hesapta 23-25 Eylul'de KULLANILMIS modeller (Workers Free
    # planda musait olanlar). deepseek-v4-flash / glm-5.3-flash Free planda
    # YOK: "not available on the Workers Free plan" hatasi donuyor.
    # Cloudflare ajaninin Free planda calistigi bildirdigi modeller.
    # glm-4.7-flash: 100+ dil, 131K baglam. nemotron-3-120b: NVIDIA'nin MoE modeli.
    _MODELLER = ["@cf/zai-org/glm-4.7-flash",
                 "@cf/nvidia/nemotron-3-120b-a12b",
                 "@cf/openai/gpt-oss-120b",
                 "@cf/google/gemma-4-26b-a4b-it"]
    print("\n  -- ULC 1: OpenAI uyumlu (/ai/v1/chat/completions) --")
    for _m in _MODELLER:
        b = time.time()
        try:
            ham = i18n.Cevirmen._llm_istek(
                f"https://api.cloudflare.com/client/v4/accounts/{_cf_id}/ai/v1/chat/completions",
                _cf_tok, _m,
                "You are a professional translator for a Turkish finance website. "
                "The user sends a JSON array of strings. Translate EVERY item into English. "
                "Return ONLY a JSON array of the exact same length - no explanations, "
                "no markdown fences. Every element must be a JSON string.",
                ORNEK, 120)
            ms = (time.time() - b) * 1000
            g = sum(1 for k2, v in zip(ORNEK, ham) if i18n.ceviri_gecerli_mi(k2, v))
            print(f"{_m}: OK {ms:.0f} ms | gecerli={g}/{len(ORNEK)}")
            for v in ham:
                print(f"    {str(v)[:72]!r}")
        except urllib.error.HTTPError as h:
            print(f"{_m}: HTTP {h.code} ({(time.time()-b)*1000:.0f} ms)")
        except Exception as e:
            print(f"{_m}: HATA {str(e)[:70]}")

    print("\n  -- ULC 2: yerel REST (/ai/run/<model>) --")
    _S = ("You are a professional translator for a Turkish finance website. "
          "The user sends a JSON array of strings. Translate EVERY item into English. "
          "Return ONLY a JSON array of the same length, no markdown fences.")
    for _m in _MODELLER[:3]:
        _u = (f"https://api.cloudflare.com/client/v4/accounts/{_cf_id}/ai/run/{_m}")
        b = time.time()
        try:
            _rq = urllib.request.Request(_u, data=json.dumps({
                "messages": [{"role": "system", "content": _S},
                             {"role": "user", "content": json.dumps(ORNEK, ensure_ascii=False)}]
            }).encode("utf-8"), headers={
                "Authorization": f"Bearer {_cf_tok}", "Content-Type": "application/json"})
            with urllib.request.urlopen(_rq, timeout=120) as _y:
                _d = json.loads(_y.read().decode())
            _c = _d.get("result", {}).get("response", "")
            print(f"{_m}: OK {(time.time()-b)*1000:.0f} ms -> {str(_c)[:90]!r}")
        except urllib.error.HTTPError as _h:
            _g = ""
            try:
                _g = _h.read().decode("utf-8", errors="replace")[:160]
            except Exception:
                pass
            print(f"{_m}: HTTP {_h.code} {(time.time()-b)*1000:.0f} ms :: {_g}")
        except Exception as _e:
            print(f"{_m}: HATA {str(_e)[:70]}")

print("SONUC Yorumu:")
print("  gecerli=4  -> saglayici sorunsuz")
print("  kimlik>0   -> model Turkce donduruyor (kimlik ceviri)")
print("  bos>0      -> model bos/bozuk JSON donduruyor")
