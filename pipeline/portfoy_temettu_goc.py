"""portfolio.json geçmişini temettü nakdiyle deterministik yeniden hesaplar.

Kuru-çalışma varsayılandır; portfolio.json'a yazmak için --uygula kullanılır.
Bot (bot.py) günlük koşuda aynı kurallarla yeni günleri ekler; bu betik yalnızca
GEÇMİŞ kayıtları aynı sözleşmeye taşır (bir kerelik göç, 2026-11-08).

Kanıt zinciri (mimari inceleme, 2026-11-08):
- Her kayıt KENDİ prices anahtarlarıyla toplanır. 2026-10-06 BIST 30 sepet
  değişimi (DSTKF→TRMET) sonrası portfolio.json'daki shares güncellendi; ilk
  21 kayıt (2026-09-06..2026-10-05) DSTKF'lidir ve DSTKF adeti portfolio.json'da
  saklanmaz. DSTKF adeti ilk kaydın fiyatlarından türetilir:
  adet = round(BAŞLANGIÇ/30/fiyat, 2) (bot.py eşit dağıtım formülü, bot.py:3134).
- Temel kimlik denetimi: her kayıt için Σ adet×fiyat, kaydın mevcut "total"
  değeriyle birebir eşleşmeli (temettüsüz taban). Uyuşmayan tek kayıt varsa
  betik veri bozmak yerine durur.
- Sonra: yeni_total = taban + kümülatif net temettü (stopaj %10 sonrası;
  bot.TEMETTU_STOPAJ). pct ve daily_pct yeni toplamlardan yeniden hesaplanır
  (semantik bot.py:3164-3167 ile aynı: ilk kaydın "dün"ü başlangıç sermayesi).
- Bedelsiz/bölünme çarpanı da aynı formülle uygulanır (değer = adet × fiyat ×
  bot._gecmis_carpan); dönem içinde olay olmadığından çarpan 1'dir.
"""
import argparse
import os
import sys

# bot.py importu en az bir LLM anahtarı ister; göç LLM kullanmaz (teknik_tarama
# deseniyle aynı kukla değer).
os.environ.setdefault("AMD_API_KEY", "portfoy-goc")

# betik pipeline/ altından çağrılır; bot.py depo kökündedir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402  (depo kökünden çalıştırılmalı)


def _ana(kok):
    p = bot.load_portfolio() if kok == "." else None
    if p is None:
        import json
        with open(os.path.join(kok, bot.PORTFOLYO_DOSYASI), encoding="utf-8") as f:
            p = json.load(f)
    return p


def main():
    ayirici = argparse.ArgumentParser(description="Portföy geçmişini temettü nakdiyle yeniden hesaplar")
    ayirici.add_argument("--kok", default=".", help="depo kökü (varsayılan: .)")
    ayirici.add_argument("--uygula", action="store_true", help="portfolio.json'a yaz (varsayılan: kuru-çalışma)")
    args = ayirici.parse_args()

    p = _ana(args.kok)
    if not p or not p.get("history"):
        print("HATA: portfolio.json / history bulunamadı.")
        return 2
    # _kurumsal_aksiyonlar/_temettu_nakit data/temettu/seriler için göreli yol
    # kullanır → betiği depo kökünden çalıştırmak şart.
    if not os.path.isdir(os.path.join(args.kok, "data", "temettu", "seriler")):
        print("HATA: data/temettu/seriler yok — depo kökünden çalıştırın.")
        return 2

    baslangic = float(p.get("initial_capital") or bot.BASLANGIC_SERMAYE)
    baslangic_tarih = p["start_date"]
    ilk_fiyatlar = p["history"][0].get("prices") or {}
    era1_adetler = {kod: round(baslangic / len(ilk_fiyatlar) / prc, 2)
                    for kod, prc in ilk_fiyatlar.items() if prc}
    aksiyonlar = bot._kurumsal_aksiyonlar(set(p["shares"].keys()) | set(era1_adetler.keys()))

    onceki_total = baslangic
    uyusmazlik = []
    ozet = []
    eski_son = p["history"][-1]["total"]
    for kayit in p["history"]:
        fiyatlar = kayit.get("prices") or {}
        adetler = {}
        for kod in fiyatlar:
            adet = p["shares"].get(kod) or era1_adetler.get(kod)
            if not adet:
                uyusmazlik.append(f"{kayit['date']}: {kod} için adet çözülemedi")
                continue
            adetler[kod] = adet
        taban = sum(adetler[k] * float(fiyatlar[k]) for k in adetler)
        if abs(taban - float(kayit["total"])) > 0.05:
            uyusmazlik.append(
                f"{kayit['date']}: taban {taban:.2f} != kayıt total {kayit['total']:.2f}")
            continue
        nakit = sum(bot._temettu_nakit(aksiyonlar[k], adetler[k], kayit["date"], baslangic_tarih)
                    for k in adetler)
        carpan_duzeltme = sum(adetler[k] * float(fiyatlar[k])
                              * (bot._gecmis_carpan(aksiyonlar[k], kayit["date"], baslangic_tarih) - 1)
                              for k in adetler)
        yeni_total = round(taban + nakit + carpan_duzeltme, 2)
        yeni_pct = round((yeni_total - baslangic) / baslangic * 100, 2)
        yeni_gunluk = round((yeni_total - onceki_total) / onceki_total * 100, 2) if onceki_total else 0.0
        onceki_total = yeni_total
        ozet.append((kayit["date"], kayit["total"], yeni_total, nakit))
        kayit["total"], kayit["pct"], kayit["daily_pct"] = yeni_total, yeni_pct, yeni_gunluk
        kayit["temettu_nakit"] = round(nakit, 2)

    if uyusmazlik:
        print(f"DOĞRULAMA BAŞARISIZ — {len(uyusmazlik)} kayıt kimlik denetimini geçemedi:")
        for u in uyusmazlik[:10]:
            print(f"  - {u}")
        print("Portfolio.json YAZILMADI. Sepet/adet yapısı değişmiş olabilir; elle inceleyin.")
        return 1

    degisen = [(t, e, y, n) for t, e, y, n in ozet if abs(n) > 0.005]
    print(f"Kimlik denetimi: {len(ozet)}/{len(ozet)} kayıt birebir (temettüsüz taban).")
    print(f"Toplam eski son: {eski_son:.2f} TL → yeni son: {ozet[-1][2]:.2f} TL "
          f"(fark {ozet[-1][2] - eski_son:+.2f} TL).")
    print(f"Temettü nakdi eklenen kayıt: {len(degisen)}")
    for t, e, y, n in degisen:
        print(f"  {t}: {e:.2f} → {y:.2f} TL (nakit {n:+.2f})")

    if args.uygula:
        if args.kok == ".":
            bot.save_portfolio(p)
        else:
            import json
            with open(os.path.join(args.kok, bot.PORTFOLYO_DOSYASI), "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False, indent=2)
        print("UYGULANDI: portfolio.json güncellendi.")
    else:
        print("KURU-ÇALIŞMA: yazılmadı (--uygula ile uygulanır).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
