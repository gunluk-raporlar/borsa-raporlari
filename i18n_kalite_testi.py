"""CANLI KALITE TESTI â€” Actions'ta calisir, gercek API cevabini gosterir.

Canli kosum sonucu: yeni_ceviri=12, reddedilen_ceviri=224.
API cevap veriyor ama neredeyse hepsi reddediliyor. Iki olasilik:
  (a) model Turkce donduruyor (kimlik ceviri)
  (b) model bos/bozuk JSON donduruyor
Bu test ikisini AYIRIR ve gercek metinleri loga basar.
"""
import glob
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

# --- GERCEK SAYFA DIZELERI PROBU (2026-10-15) ---
# Kalite testinin 4 ornek dizesi tum saglayicilarda GECERLI donerken gercek kosu
# ~%95 sessiz red yiyor (atlanan artiyor, hata satiri yok). Ornek dizeler temiz
# secilmisti; burada gercek sayfalardan toplanan BEKLEYEN dizelerle ayni saglayici
# A/B test edilir (dusunme-kapama acik/kapali) ve red gerekcesi ham ciktiyla gosterilir.
print("\n--- Gercek sayfa dizeleri probu (Discovery/deepseek-v4-flash-0731, hedef=de) ---")
try:
    _aday_sayfalar = (["index.html", "raporlar.html", "derin-analiz.html"]
                      + sorted(glob.glob("raporlar/*.html"))[:3]
                      + sorted(glob.glob("hisse/*.html"))[:3])
    _gercek: list[str] = []
    for _syf in _aday_sayfalar:
        if len(_gercek) >= 24 or not os.path.exists(_syf):
            continue
        try:
            _p = i18n.MetinToplayici(dil="de")
            _p.feed(open(_syf, encoding="utf-8").read())
            _p.close()
        except Exception:
            continue
        for _kaynak in (x["kaynak"] for x in _p.parcalar):
            _saf, _onek = i18n.metni_ayikla(_kaynak)
            if not i18n.cevrilecek_mi(_saf) or _saf in i18n.SOZLUK:
                continue
            if i18n.Cevirmen._anahtar("de", _saf) in c.onbellek:
                continue      # onbellekte: kosuda saglayiciya gitmez, proba da girmesin
            if len(_saf) < 8:
                continue
            _gercek.append(_saf)
            if len(_gercek) >= 24:
                break
    print(f"gercek bekleyen dize toplandi: {len(_gercek)}")
    _disc_url = (os.environ.get("DISCOVERY_BASE_URL",
                 "https://discovery-api.intern-ai.org.cn/v1").rstrip("/")
                 + "/chat/completions")
    _disc_k = os.environ.get("DISCOVERY_API_KEY")
    _sistem_de = ("You are a professional translator for a Turkish finance website. "
                  "The user sends a JSON array of strings. Translate EVERY item into German. "
                  "Return ONLY a JSON array of the exact same length — no explanations, "
                  "no markdown fences. Every element must be a JSON string.")
    if _disc_k and len(_gercek) >= 6:
        _yarim = len(_gercek) // 2
        for _etiket, _stil, _grup in (("dusunme-kapama ACIK", None, _gercek[:_yarim]),
                                      ("dusunme-kapama KAPALI", "yok", _gercek[_yarim:])):
            i18n._disc_calisan_stil = None
            i18n._disc_reddedilen_stiller.clear()
            if _stil:
                os.environ["DISCOVERY_DUSUNME_STIL"] = _stil
            else:
                os.environ.pop("DISCOVERY_DUSUNME_STIL", None)
            try:
                _t = time.time()
                _ham = i18n.Cevirmen._llm_istek_dayanikli(
                    _disc_url, _disc_k, "deepseek-v4-flash-0731", _sistem_de, _grup, 60)
                print(f"[{_etiket}] istek OK ({time.time() - _t:.1f} sn, {len(_ham)}/{len(_grup)} dize)")
                _sayim: dict[str, int] = {}
                for _s, _c in zip(_grup, _ham):
                    if not isinstance(_c, str) or not _c.strip():
                        _d = "BOS"
                    elif _c.strip() == _s.strip():
                        _d = "KIMLIK"
                    elif i18n.TURKCE_OZEL_RE.search(_c) and not any(a in _c for a in i18n.OZEL_ADLAR):
                        _d = "TURKCE-KARAKTER"
                    else:
                        _d = "GECERLI"
                    _sayim[_d] = _sayim.get(_d, 0) + 1
                    print(f"    [{_d}] {_s[:46]!r} -> {_c[:64]!r}")
                print(f"    ozet: {_sayim}")
            except Exception as _h:
                print(f"[{_etiket}] istek HATA: {str(_h)[:140]}")
        os.environ.pop("DISCOVERY_DUSUNME_STIL", None)
    else:
        print("DISCOVERY_API_KEY yok veya yeterli dize toplanamadi; gercek-dize probu atlandi")
except Exception as _probe_h:
    print(f"gercek-dize probu hata: {_probe_h}")

print("SONUC Yorumu:")
print("  gecerli=4  -> saglayici sorunsuz")
print("  kimlik>0   -> model Turkce donduruyor (kimlik ceviri)")
print("  bos>0      -> model bos/bozuk JSON donduruyor")
