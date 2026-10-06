"""sirketler.json 'temel' listelerini yerel mali verilerden uretir.

Kaynaklar (hepsi repoda, ag yok):
  - data/hisse-analiz/<KOD>.json  -> Is Yatirim cevrelik mali tablolar (10 donem)
  - data/temel-veri.json          -> TradingView TTM alanlari (nakit, cari oran, EV/FAVOK ...)
  - data/sirketler.json           -> mevcut profiller (TRMET'in elle girilen Midas
                                     notlari gibi) korunur; 'temel' yoksa/saftar ise
                                     uretilen maddeler yazilir.

Kullanim: python temel_ozet_uret.py [--force]
  --force: mevcut 'temel' listesi olsa bile uzerine yazar.
  Cikti tarih damgalidir; veriler guncellendikce tekrar calistirilabilir.
"""
import json
import os
import sys

TARIH = "06.10.2026"


def _yukle(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _sira(x):
    try:
        y, c = str(x.get("donem", "")).split("/")
        return int(y) * 10 + int(c)
    except Exception:
        return 0


def _yoy(kayitlar, alan):
    """Son donem ayni cevrek gecen yil karsilastirmasi (%) — veri yoksa None."""
    if not kayitlar:
        return None
    son = kayitlar[-1]
    y, c = str(son.get("donem")).split("/")
    gecmis = [k for k in kayitlar if k.get("donem") == f"{int(y) - 1}/{c}"]
    if not gecmis:
        return None
    yeni, eski = son.get(alan), gecmis[0].get(alan)
    try:
        yeni, eski = float(yeni), float(eski)
    except (TypeError, ValueError):
        return None
    if not eski:
        return None
    return (yeni / eski - 1) * 100


def _yorum_cari(x):
    return "güçlü likidite" if x > 2 else "dengeli likidite" if x >= 1 else "kısa vadeli yükümlülük baskısı"


def _yorum_yoy(x, birim=""):
    if x is None:
        return None
    ok = ("artış" if x > 0.5 else "yok denecek düzeyde" if x > -0.5 else "daralma")
    return f"YoY %{x:+.1f}{birim} ({ok})"


def uret(kod, temel_veri):
    tv = (temel_veri.get("hisseler") or {}).get(kod) or {}
    d = _yukle(os.path.join("data", "hisse-analiz", kod + ".json")) or {}
    kayitlar = sorted([x for x in (d.get("bilanco") or []) if x.get("donem")], key=_sira)
    if not kayitlar or not tv:
        return None
    son = kayitlar[-1]
    f = lambda x: f"{x:,.0f}".replace(",", ".") if x is not None else "—"
    y1 = lambda x: f"{x:.1f}".replace(".", ",") if x is not None else "—"
    madde = []

    oz = son.get("ozsermaye")
    satis_son = son.get("satis")
    try:
        satis_son = float(satis_son)
    except (TypeError, ValueError):
        satis_son = None

    # 1) Kârlılık + büyüme (çeyreklik YoY)
    if satis_son:
        marj = (float(son.get("net_kar") or 0) / satis_son) * 100
        marj_yorum = ("yüksek" if marj > 20 else "normal" if marj >= 5 else "ince" if marj > 0 else "negatif")
        madde.append(f"Son çeyrek ({son.get('donem')}): satış {f(satis_son)} TL, "
                     f"net kâr marjı %{y1(marj)} ({marj_yorum}).")
    for alan, ad in (("satis", "Satış"), ("net_kar", "Net kâr")):
        y = _yoy(kayitlar, alan)
        if y is not None:
            madde.append(f"{ad} büyümesi: {_yorum_yoy(y)}.")
    oz_yoy = None
    if oz:
        eski = [k for k in kayitlar[:-1] if k.get("ozsermaye")]
        if eski:
            try:
                oz_yoy = (float(oz) / float(eski[-1]["ozsermaye"]) - 1) * 100
                madde.append(f"Özkaynak büyümesi: {_yorum_yoy(oz_yoy)}.")
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    # 2) Verimlilik: aktif devir hizi (TTM satis / toplam varlik)
    try:
        toplam_varlik = float(son.get("toplam_varlik"))
        ttm_satis = 0.0
        for k in kayitlar[-4:]:
            ttm_satis += float(k.get("satis") or 0)
        if toplam_varlik > 0 and ttm_satis > 0:
            devir = ttm_satis / toplam_varlik
            yorum = "verimli varlık kullanımı" if devir > 0.5 else "orta varlık kullanımı" if devir > 0.2 else "ağır varlık tabanı (sermaye yoğun sektör normu olabilir)"
            madde.append(f"Aktif devir hızı {y1(devir)} (TTM satış / toplam varlık) — {yorum}.")
    except (TypeError, ValueError):
        pass

    # 3) Borcluluk yapis
    try:
        oz = float(oz)
        fin = float(son.get("finansal_borc") or 0)
        kisa = float(son.get("kisa_borc") or 0)
        uzun = float(son.get("uzun_borc") or 0)
        tv_borc = tv.get("total_debt_fq")
        tv_nakit = tv.get("cash_n_equivalents_fq")
        if oz > 0:
            oran = fin / oz * 100
            yorum = ("düşük kaldıraç" if oran < 20 else "makul kaldıraç" if oran < 60 else "yüksek kaldıraç")
            madde.append(f"Finansal borç / özkaynak %{y1(oran)} ({yorum}); "
                         f"toplam borç (kısa+uzun) / özkaynak %{y1((kisa + uzun) / oz * 100)}.")
        if tv_borc is not None and tv_nakit is not None:
            net = float(tv_borc) - float(tv_nakit)
            if net <= 0:
                madde.append(f"Net finansal borç YOK — nakit, brüt borçtan {f(-net)} TL fazla.")
            else:
                madde.append(f"Net finansal borç {f(net)} TL (TradingView bilanso kesiti).")
    except (TypeError, ValueError):
        pass

    # 4) Likidite (Is Yatirim donen varlik / kisa borc + TV cari oran)
    try:
        cari = float(son.get("donen_varlik")) / float(son.get("kisa_borc"))
        madde.append(f"Cari oran (dönen varlık / kısa borç) {y1(cari)} — {_yorum_cari(cari)}.")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    tv_cari = tv.get("current_ratio_fq")
    if tv_cari:
        madde.append(f"Cari oran (TradingView bilanso kesiti) {y1(tv_cari)} — {_yorum_cari(float(tv_cari))}.")

    # 5) Carpanlar + temettu (TradingView TTM)
    carpanlar = []
    if tv.get("price_earnings_ttm"):
        carpanlar.append(f"F/K {y1(tv['price_earnings_ttm'])}")
    if tv.get("price_book_fq"):
        carpanlar.append(f"PD/DD {y1(tv['price_book_fq'])}")
    if tv.get("enterprise_value_ebitda_ttm"):
        carpanlar.append(f"EV/FAVÖK {y1(tv['enterprise_value_ebitda_ttm'])}")
    tem = tv.get("dividends_yield")
    if tem:
        carpanlar.append(f"temettü verimi %{y1(float(tem))}")
    if carpanlar:
        madde.append("Çarpanlar: " + " · ".join(carpanlar) + ".")

    if not madde:
        return None
    madde.append(f"Kaynak: İş Yatırım çeyreklik mali tablolar + TradingView TTM ({TARIH}); "
                 "yorumlar sabit eşik kurallarıyla üretilmiştir, yatırım tavsiyesi değildir.")
    return madde


def main():
    force = "--force" in sys.argv
    temel_veri = _yukle("data/temel-veri.json") or {}
    sirketler = _yukle("data/sirketler.json") or {}
    yazilan = atlanan = 0
    for kod in sorted(sirketler):
        mevcut = sirketler[kod].get("temel") or []
        if mevcut and not force and any("Midas" in m for m in mevcut):
            # Elle girilen saglayici notlar (orn. TRMET Midas) korunur.
            atlanan += 1
            continue
        madde = uret(kod, temel_veri)
        if madde:
            sirketler[kod]["temel"] = madde
            yazilan += 1
        else:
            atlanan += 1
    with open("data/sirketler.json", "w", encoding="utf-8") as f_:
        json.dump(sirketler, f_, ensure_ascii=False, indent=2)
    print(f"Temel analiz ozetleri: {yazilan} hisseye yazildi, {atlanan} atlandi (korunan/veri yok).")


if __name__ == "__main__":
    main()
