#!/usr/bin/env python3
"""Yayin öncesi veri dogrulama kapisi (yalnızca stdlib; bloklayıcı).

publish.yml "Site yayini" adımında, commit/push'tan ÖNCE çalışır: makine-okunur
JSON'ların yapısı bozuksa yayın durur — hatalı veri Cloudflare build kotası
harcamaz ve siteye çıkmaz. Bloklayıcı hatalar exit 1 verir; tazelik/şüphe
uyarıları yalnızca log'a yazılır (yayını durdurmaz).

Kapsam: ticker.json, piyasa-serit.json, portfolio.json, sinyal-karnesi.json (varsa).
Kullanım: python3 pipeline/veri_dogrula.py [kok_dizin]   (varsayılan: .)
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

hatalar = []
uyarilar = []

# sinyal değer sözlüğü (bot.py teknik tarama çıktısı); bilinmeyen değer hata
# değil uyarıdır — borsapy/TradingView yeni bir etiket eklerse yayın durmasın.
SINYALLER = {"GÜÇLÜ AL", "AL", "NÖTR", "SAT", "GÜÇLÜ SAT"}


def _oku(kok, ad):
    yol = Path(kok) / ad
    if not yol.exists():
        hatalar.append(f"{ad}: dosya yok")
        return None
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        hatalar.append(f"{ad}: JSON okunamadı/bozuk: {e}")
        return None


def _sonlu(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _iso_yas_gun(deger):
    """ISO zaman damgasının kaç gün önce olduğunu döndürür; ayrışamazsa None."""
    try:
        t = datetime.fromisoformat(str(deger).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def ticker_dogrula(kok):
    veri = _oku(kok, "ticker.json")
    if veri is None:
        return
    if not isinstance(veri.get("guncelleme"), str) or not veri["guncelleme"].strip():
        hatalar.append("ticker.json: 'guncelleme' alanı yok/boş")
    if veri.get("guncelleme_iso") is None:
        uyarilar.append("ticker.json: 'guncelleme_iso' yok (ajan tazelik kontrolü zayıflar)")
    hisseler = veri.get("hisseler")
    if not isinstance(hisseler, list) or not hisseler:
        hatalar.append("ticker.json: 'hisseler' listesi yok/boş")
        return
    n = len(hisseler)
    if n < 15:
        hatalar.append(f"ticker.json: yalnızca {n} hisse — evren bozulmuş olabilir (beklenen 30)")
    elif n != 30:
        uyarilar.append(f"ticker.json: {n} hisse (beklenen 30) — BIST 30 değişimi ise llms.txt'i güncelleyin")
    for i, kayit in enumerate(hisseler):
        kod = kayit.get("h") if isinstance(kayit, dict) else None
        if not isinstance(kod, str) or not kod.strip():
            hatalar.append(f"ticker.json.hisseler[{i}]: 'h' (kod) yok/boş")
            continue
        if not _sonlu(kayit.get("f")) or kayit["f"] <= 0:
            hatalar.append(f"ticker.json[{kod}]: 'f' sıfırdan büyük sonlu sayı olmalı ({kayit.get('f')!r})")
        if not _sonlu(kayit.get("d")):
            hatalar.append(f"ticker.json[{kod}]: 'd' (günlük %) sonlu sayı olmalı ({kayit.get('d')!r})")
        sinyal = kayit.get("s")
        if not isinstance(sinyal, str) or not sinyal.strip():
            hatalar.append(f"ticker.json[{kod}]: 's' (sinyal) yok/boş")
        elif sinyal not in SINYALLER:
            uyarilar.append(f"ticker.json[{kod}]: bilinmeyen sinyal değeri {sinyal!r} (sözlüğü güncelleyin)")


def serit_dogrula(kok):
    veri = _oku(kok, "piyasa-serit.json")
    if veri is None:
        return
    if not isinstance(veri.get("guncelleme"), str) or not veri["guncelleme"].strip():
        hatalar.append("piyasa-serit.json: 'guncelleme' alanı yok/boş")
    gostergeler = veri.get("gostergeler")
    if not isinstance(gostergeler, list) or not gostergeler:
        hatalar.append("piyasa-serit.json: 'gostergeler' listesi yok/boş")
        return
    for i, g in enumerate(gostergeler):
        if not isinstance(g, dict):
            hatalar.append(f"piyasa-serit.json.gostergeler[{i}]: nesne değil")
            continue
        if not isinstance(g.get("k"), str) or not g["k"].strip():
            hatalar.append(f"piyasa-serit.json.gostergeler[{i}]: 'k' (simge) yok/boş")
        if not _sonlu(g.get("f")):
            hatalar.append(f"piyasa-serit.json[{g.get('k')}]: 'f' sonlu sayı olmalı ({g.get('f')!r})")
        if g.get("d") is not None and not _sonlu(g.get("d")):
            hatalar.append(f"piyasa-serit.json[{g.get('k')}]: 'd' sonlu sayı veya null olmalı")
    yas = _iso_yas_gun(veri.get("guncelleme_iso"))
    if yas is None and veri.get("guncelleme_iso"):
        uyarilar.append("piyasa-serit.json: 'guncelleme_iso' ISO değil")
    elif yas is not None and yas > 4:
        uyarilar.append(f"piyasa-serit.json: yedek veri {yas:.0f} gündür değişmedi (tatil mi?)")


def portfoy_dogrula(kok):
    veri = _oku(kok, "portfolio.json")
    if veri is None:
        return
    shares = veri.get("shares")
    if not isinstance(shares, dict) or not shares:
        hatalar.append("portfolio.json: 'shares' yok/boş")
    gecmis = veri.get("history")
    if not isinstance(gecmis, list) or not gecmis:
        hatalar.append("portfolio.json: 'history' yok/boş")
        return
    onceki = None
    for i, kayit in enumerate(gecmis):
        tarih = kayit.get("date")
        if not isinstance(tarih, str) or len(tarih) != 10:
            hatalar.append(f"portfolio.json.history[{i}]: 'date' yok/bozuk ({tarih!r})")
        total = kayit.get("total")
        if not _sonlu(total) or total <= 0:
            hatalar.append(f"portfolio.json.history[{tarih}]: 'total' pozitif sonlu sayı olmalı ({total!r})")
            continue
        if onceki is not None and total < onceki * 0.5:
            uyarilar.append(f"portfolio.json.history[{tarih}]: toplam bir günde yarıya düştü "
                            f"({onceki:.0f} → {total:.0f}) — sepet değişimi/kısmi veri olabilir")
        onceki = total
    son = gecmis[-1]
    if not isinstance(son.get("prices"), dict) or not son["prices"]:
        hatalar.append("portfolio.json: son kayıtta 'prices' yok/boş")


def karne_dogrula(kok):
    yol = Path(kok) / "sinyal-karnesi.json"
    if not yol.exists():
        return
    veri = _oku(kok, "sinyal-karnesi.json")
    if veri is None:
        return
    ozet = veri.get("ozet")
    kayitlar = veri.get("kayitlar")
    if not isinstance(ozet, dict) or not isinstance(kayitlar, list):
        hatalar.append("sinyal-karnesi.json: 'ozet' nesnesi / 'kayitlar' listesi yok")
        return
    if ozet.get("n") != len(kayitlar):
        hatalar.append(f"sinyal-karnesi.json: ozet.n={ozet.get('n')} != kayitlar={len(kayitlar)}")
    if veri.get("yontem_surumu") is None:
        uyarilar.append("sinyal-karnesi.json: 'yontem_surumu' yok")
    for i, kayit in enumerate(kayitlar):
        if not isinstance(kayit, dict) or not kayit.get("tarih") or not kayit.get("hisse"):
            hatalar.append(f"sinyal-karnesi.json.kayitlar[{i}]: tarih/hisse eksik")
            break
        if not _sonlu(kayit.get("getiri")):
            hatalar.append(f"sinyal-karnesi.json.kayitlar[{i}]: getiri sonlu sayı olmalı")
            break


def main():
    kok = sys.argv[1] if len(sys.argv) > 1 else "."
    ticker_dogrula(kok)
    serit_dogrula(kok)
    portfoy_dogrula(kok)
    karne_dogrula(kok)
    if uyarilar:
        print("UYARILAR:")
        for u in uyarilar:
            print(f"  ~ {u}")
    if hatalar:
        print("DOĞRULAMA BAŞARISIZ (yayın durduruldu):")
        for h in hatalar:
            print(f"  - {h}")
        return 1
    print("DOĞRULAMA TAMAM: ticker.json, piyasa-serit.json, portfolio.json"
          + (", sinyal-karnesi.json" if (Path(kok) / "sinyal-karnesi.json").exists() else "")
          + " yapısı geçerli.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
