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

print("SONUC Yorumu:")
print("  gecerli=4  -> saglayici sorunsuz")
print("  kimlik>0   -> model Turkce donduruyor (kimlik ceviri)")
print("  bos>0      -> model bos/bozuk JSON donduruyor")
