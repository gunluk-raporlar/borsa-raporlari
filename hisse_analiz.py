# -*- coding: utf-8 -*-
"""Elle eklenen hisse analiz bolumleri: aciklanan bilancolar + degerlendirmeler.

Hisse sayfalarinda (hisse/<KOD>.html) bot.py'nin otomatik urettigi icerigin
yanina iki elle surulen bolum eklenir:

  1) "Aciklanan Bilancolar" tablosu — data/hisse-analiz/<KOD>.json icindeki
     "bilanco" listesinden. Rakamlar Is Yatirim mali tablolarindan
     `--bilanco-cek` ile yeniden cekilebilir (metinlere dokunulmaz).
  2) "Degerlendirmeler" — teknik analiz, makroekonomik degerlendirme ve
     bilanco degerlendirmesi metinleri. Bu metinler bilincli olarak ELLE
     yazilir/guncellenir; gunluk bot kosulari onlara dokunmaz.

Veri dosyasi bicimi (data/hisse-analiz/ASELS.json):
{
  "kod": "ASELS",
  "guncelleme": "2026-09-23",            # degerlendirme metinlerinin tarihi
  "bilanco_guncelleme": "2026-09-23",    # tablonun cekim tarihi
  "kaynak": "Is Yatirim mali tablolari (KAP bildirimleri esas alinir)",
  "tip": "sinai",                        # "sinai" ya da "banka"
  "bilanco": [{"donem": "2026/6", "satis": 88494252000.0, ...}, ...],
  "teknik_analiz": "...",
  "makro_degerlendirme": "...",
  "bilanco_degerlendirmesi": "..."
}

Kullanim:
    python hisse_analiz.py --kontrol            # kapsam ve tutarlilik denetimi
    python hisse_analiz.py --bilanco-cek        # tum hisselerin tablosunu yenile
    python hisse_analiz.py --bilanco-cek ASELS  # yalnizca secili hisseler

bot.py: build_hisse_html() bu modulun urettigi HTML'i sayfaya ekler; veri
dosyasi yoksa bolumler sessizce atlanir (sayfa bozulmaz).
"""

import json
import html as _html
import os
import re
import time
from datetime import datetime

KLASOR = os.path.join("data", "hisse-analiz")

# XI_29 (genel) mali tablo kodlari -> kayit anahtarlari
_SINAI_KOD = {
    "1A": "donen_varlik", "1AK": "duran_varlik", "1BL": "toplam_varlik",
    "2A": "kisa_borc", "2B": "uzun_borc", "2AA": "fin_borc_kisa",
    "2BA": "fin_borc_uzun", "2N": "ozsermaye", "2OA": "odenmis_sermaye",
    "3C": "satis", "3L": "net_kar",
}

# XI_29_2 (banka) mali tablo kodlari -> kayit anahtarlari
_BANKA_KOD = {
    "1Z": "toplam_varlik", "2O": "ozsermaye", "2OA": "odenmis_sermaye",
    "3C": "satis", "3Z": "net_kar",
}

_KALEM_SIRA = ("satis", "net_kar", "toplam_varlik", "donen_varlik",
               "duran_varlik", "kisa_borc", "uzun_borc", "finansal_borc",
               "ozsermaye", "odenmis_sermaye")


def _hisseler():
    """bot.py icindeki HISSELER listesini (bot'u import etmeden) okur."""
    try:
        with open("bot.py", encoding="utf-8") as f:
            kaynak = f.read()
        m = re.search(r"HISSELER\s*=\s*\[(.*?)\]", kaynak, re.S)
        if m:
            return re.findall(r"\"([A-Z0-9]{3,6})\"", m.group(1))
    except OSError:
        pass
    return []


def _dosya(kod):
    return os.path.join(KLASOR, kod + ".json")


def yukle(kod):
    """Hisse analiz kaydini okur; yoksa None."""
    try:
        with open(_dosya(kod), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def kaydet(veri):
    os.makedirs(KLASOR, exist_ok=True)
    with open(_dosya(veri["kod"]), "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)


def _tr(v, ondalik=1):
    """Sayiyi Turkce bicime cevirir: 1234567.8 -> '1.234.567,8'."""
    if v is None:
        return "&mdash;"
    s = f"{float(v):,.{ondalik}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _mlr(v, ondalik=1):
    """TL degerini milyar (mlr) TL metnine cevirir."""
    if v in (None, ""):
        return "&mdash;"
    try:
        return _tr(float(v) / 1e9, ondalik)
    except (TypeError, ValueError):
        return "&mdash;"


def _oran(pay, payda):
    try:
        return float(pay) / float(payda)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _donem_sirala(d):
    try:
        yil, ceyrek = str(d).split("/")
        return (int(yil), int(ceyrek))
    except (ValueError, AttributeError):
        return (0, 0)



def _hucre_yaz(donemler, df, satir, hedefler):
    """Satirin donem kolonlarindaki dolu degerlerini hedef kalemlere yazar."""
    for kolon in df.columns:
        if not str(kolon)[:1].isdigit():
            continue
        try:
            v = float(satir.get(kolon))
        except (TypeError, ValueError):
            continue
        if v == 0 or v != v:  # sifir ve NaN atlanir
            continue
        hucre = donemler.setdefault(str(kolon), {})
        for hedef in hedefler:
            if hedef not in hucre:
                hucre[hedef] = v


def _tablo_cikar(df, tip):
    """fetch_financials ciktisini {donem: {kalem: TL}} sozlugune cevirir.

    Iki gecisli eslestirme: once sablon KOD eslesmeleri (XI_29 / XI_29_2),
    sonra isim tabanli yedekler. Kod eslesmesi her zaman onceliklidir:
    ornek — 2OCF 'Dönem Net Kar/Zararı' (ozkaynak icindeki ana ortaklik payi)
    satiri isimce once gelse de asil '3L DÖNEM KARI (ZARARI)' satiri kazanir.
    """
    kalem = _SINAI_KOD if tip == "sinai" else _BANKA_KOD
    donemler = {}
    if df is None or df.empty:
        return donemler
    satirlar = []
    for _, r in df.iterrows():
        satirlar.append((str(r.get("FINANCIAL_ITEM_CODE") or "").upper(),
                         str(r.get("FINANCIAL_ITEM_NAME_TR") or "").upper(), r))
    # 1. gecis: sablon kodlari
    for kod, _ad, r in satirlar:
        hedef = kalem.get(kod)
        if hedef:
            _hucre_yaz(donemler, df, r, [hedef])
    # 2. gecis: isim tabanli yedekler (kodla dolmayan kalemleri tamamlar)
    for _kod, ad, r in satirlar:
        hedefler = []
        if "NET FAİZ GELİRİ" in ad or "NET FAİZ GELIRI" in ad or "SATIŞ GELİRLERİ" in ad:
            hedefler.append("satis")
        if ad.startswith("DÖNEM KAR") or ad.startswith("NET DÖNEM KAR"):
            hedefler.append("net_kar")
        if "AKTİF TOPLAM" in ad or "TOPLAM VARLIK" in ad:
            hedefler.append("toplam_varlik")
        # Not: 'Özkaynak Yöntemiyle Değerlenen Yatırımlar' (1BD) satiri da
        # 'ÖZKAYNAK' icerir; ozsermaye sanilmasin diye YÖNTEM haric tutulur.
        if "ÖZKAYNAK" in ad and "YÖNTEM" not in ad:
            hedefler.append("ozsermaye")
        if hedefler:
            _hucre_yaz(donemler, df, r, hedefler)
    return donemler


def _listeye_cevir(ham, tip):
    """{donem: {kalem: TL}} -> sirali bilanco listesi (JSON'da saklanan bicim)."""
    liste = []
    for donem in sorted(ham, key=_donem_sirala):
        h = ham[donem]
        if not (h.get("toplam_varlik") or h.get("ozsermaye")):
            continue
        kayit = {"donem": donem}
        for k in _KALEM_SIRA:
            if k in ("finansal_borc", "fin_borc_kisa", "fin_borc_uzun"):
                continue
            if h.get(k):
                kayit[k] = round(float(h[k]), 2)
        finansal_borc = (h.get("fin_borc_kisa") or 0) + (h.get("fin_borc_uzun") or 0)
        if finansal_borc and tip == "sinai":
            kayit["finansal_borc"] = round(float(finansal_borc), 2)
        liste.append(kayit)
    return liste


def _cek_tek(kod, baslangic, bitis):
    """Tek hisse icin mali tablo ceker. Once genel sablon (grup 1), bulunamazsa
    banka/faktoring sablonu (grup 2) denenir. Donus: (bilanco_listesi, tip)."""
    from isyatirimhisse import fetch_financials

    for tip, grup in (("sinai", "1"), ("banka", "2")):
        for _deneme in (1, 2):
            try:
                df = fetch_financials(symbols=[kod], start_year=baslangic,
                                      end_year=bitis, financial_group=grup)
            except Exception:
                df = None
            if df is None or df.empty:
                time.sleep(2)
                continue
            ham = _tablo_cikar(df, tip)
            if any(h.get("toplam_varlik") and h.get("ozsermaye") for h in ham.values()):
                liste = _listeye_cevir(ham, tip)
                if liste:
                    return liste, tip
            time.sleep(2)
    return [], ""


def bilanco_cek(kodlar=None, baslangic=2024):
    """Is Yatirim'dan mali tablolari cekip data/hisse-analiz/<KOD>.json
    dosyalarindaki 'bilanco' bolumunu yeniler. Metinler korunur."""
    if kodlar is None:
        kodlar = _hisseler()
    bitis = datetime.now().year
    ozet = {"tamam": [], "bos": []}
    for sira, kod in enumerate(kodlar, 1):
        liste, tip = _cek_tek(kod, baslangic, bitis)
        veri = yukle(kod) or {
            "kod": kod, "teknik_analiz": "", "makro_degerlendirme": "",
            "bilanco_degerlendirmesi": "",
        }
        veri["kod"] = kod
        if liste:
            veri["bilanco"] = liste
            veri["tip"] = tip
            veri["kaynak"] = "İş Yatırım mali tabloları (KAP bildirimleri esas alınır)"
            veri["bilanco_guncelleme"] = datetime.now().strftime("%Y-%m-%d")
            kaydet(veri)
            ozet["tamam"].append(kod)
            print(f"[Bilanço] {sira}/{len(kodlar)} {kod}: {tip}, {len(liste)} dönem", flush=True)
        else:
            kaydet(veri)
            ozet["bos"].append(kod)
            print(f"[Bilanço] {sira}/{len(kodlar)} {kod}: veri bulunamadı", flush=True)
    print(f"\nTamamlanan: {len(ozet['tamam'])} | veri bulunamayan: {len(ozet['bos'])}")
    if ozet["bos"]:
        print("Bulunamayanlar:", ", ".join(ozet["bos"]))


def _yoy_ek(satir, kalem, harita):
    """Ayni donemin bir onceki yilina gore degisim rozeti (yoksa '')."""
    if not satir.get(kalem):
        return ""
    yil, ceyrek = _donem_sirala(satir["donem"])
    onceki = harita.get(f"{yil - 1}/{ceyrek}")
    if not onceki or not onceki.get(kalem):
        return ""
    oran = _oran(satir[kalem], onceki[kalem])
    if oran is None:
        return ""
    yuzde = (oran - 1) * 100
    isaret = "+" if yuzde >= 0 else ""
    return (f' <span style="color:var(--muted); font-weight:400; font-size:11px">'
            f"({isaret}{_tr(yuzde)}% YoY)</span>")


def bilanco_tablosu_html(kod):
    """hisse/<KOD>.html icin 'Aciklanan Bilancolar' karti (yoksa '')."""
    veri = yukle(kod)
    if not veri:
        return ""
    satirlar = [s for s in (veri.get("bilanco") or []) if s.get("donem")]
    if not satirlar:
        return ""
    banka = veri.get("tip") == "banka"
    satirlar.sort(key=lambda s: _donem_sirala(s.get("donem")), reverse=True)
    harita = {s["donem"]: s for s in satirlar}
    guncelleme = veri.get("bilanco_guncelleme") or ""
    guncelleme_bit = f" &middot; son çekim: {guncelleme}" if guncelleme else ""

    satis_ad = "Net Faiz Geliri" if banka else "Satış Gelirleri"
    varlik_ad = "Toplam Aktifler" if banka else "Toplam Varlıklar"
    basliklar = ["Dönem", f"{satis_ad} (mlr TL)", "Net Kâr (mlr TL)", "Kâr Marjı",
                 f"{varlik_ad} (mlr TL)", "Özkaynaklar (mlr TL)"]
    if not banka:
        basliklar += ["Fin. Borç (mlr TL)", "Fin. Borç / Özkaynak"]

    govde = []
    for s in satirlar:
        nk = s.get("net_kar")
        nk_sinif = ' class="neg"' if isinstance(nk, (int, float)) and nk < 0 else ""
        marj = _oran(nk, s.get("satis"))
        kaldirac = _oran(s.get("finansal_borc"), s.get("ozsermaye"))
        hucreler = [
            f'<td><strong>{s["donem"]}</strong></td>',
            f"<td>{_mlr(s.get('satis'))}{_yoy_ek(s, 'satis', harita)}</td>",
            f"<td{nk_sinif}>{_mlr(nk)}{_yoy_ek(s, 'net_kar', harita)}</td>",
            f"<td>{('%' + _tr(marj * 100)) if marj is not None else '&mdash;'}</td>",
            f"<td>{_mlr(s.get('toplam_varlik'))}</td>",
            f"<td>{_mlr(s.get('ozsermaye'))}</td>",
        ]
        if not banka:
            hucreler.append(f"<td>{_mlr(s.get('finansal_borc'))}</td>")
            hucreler.append(f"<td>{('%' + _tr(kaldirac * 100)) if kaldirac is not None else '&mdash;'}</td>")
        govde.append("<tr>" + "".join(hucreler) + "</tr>")

    tablo = ('<div class="tbl-wrap"><table style="font-size:12.5px"><tr>'
             + "".join(f"<th>{b}</th>" for b in basliklar) + "</tr>"
             + "".join(govde) + "</table></div>")
    return f"""
<div class="card" style="margin-top:14px">
<h3 style="margin:0 0 4px">📋 Açıklanan Bilançolar <span style="font-weight:400; color:var(--muted); font-size:12.5px">&middot; elle eklenen mali tablo özeti{guncelleme_bit}</span></h3>
{tablo}
<p style="margin:8px 0 0; color:var(--muted); font-size:12px">Gelir tablosu kalemleri ({satis_ad.lower()} ve net kâr) yıl başından itibaren kümülatiftir; bilanço kalemleri dönem sonu bakiyesidir. Kaynak: {veri.get('kaynak', 'İş Yatırım mali tabloları')}.</p>
</div>"""


def _metin_html(metin):
    """Serbest metni paragraflara bolup HTML'e cevirir (satir sonu = paragraf)."""
    parcalar = [p.strip() for p in str(metin or "").split("\n") if p.strip()]
    return "".join(f'<p style="margin:6px 0 0; font-size:14px">{_html.escape(p)}</p>' for p in parcalar)


def degerlendirmeler_html(kod):
    """hisse/<KOD>.html icin elle yazilan degerlendirme karti (yoksa '')."""
    veri = yukle(kod)
    if not veri:
        return ""
    bolumler = [
        ("📈 Teknik Analiz", veri.get("teknik_analiz")),
        ("🌍 Makroekonomik Değerlendirme", veri.get("makro_degerlendirme")),
        ("🧾 Bilanço Değerlendirmesi", veri.get("bilanco_degerlendirmesi")),
    ]
    bolumler = [(b, m) for b, m in bolumler if str(m or "").strip()]
    if not bolumler:
        return ""
    govde = []
    for baslik, metin in bolumler:
        govde.append(f'<h4 style="margin:12px 0 0; font-size:14.5px">{baslik}</h4>{_metin_html(metin)}')
    tarih = veri.get("guncelleme") or ""
    tarih_bit = ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", tarih):
        y, a, g = tarih.split("-")
        tarih_bit = f" &middot; son güncelleme: {g}.{a}.{y}"
    return f"""
<div class="card" style="margin-top:14px">
<h3 style="margin:0 0 2px">📝 Değerlendirmeler <span style="font-weight:400; color:var(--muted); font-size:12.5px">&middot; elle hazırlanır{tarih_bit}</span></h3>
{''.join(govde)}
<p style="margin:10px 0 0; color:var(--muted); font-size:12px">Bu bölüm elle güncellenir; bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</p>
</div>"""

    return ozet


def kontrol(kodlar=None):
    """Kapsam/tutarlilik denetimi: eksik dosya, eksik metin, supheli sayilar."""
    if kodlar is None:
        kodlar = _hisseler()
    eksik_dosya, eksik_metin, sorunlar, bilgi = [], [], [], []
    for kod in kodlar:
        veri = yukle(kod)
        if not veri:
            eksik_dosya.append(kod)
            continue
        for alan in ("teknik_analiz", "makro_degerlendirme", "bilanco_degerlendirmesi"):
            if len(str(veri.get(alan) or "").strip()) < 80:
                eksik_metin.append(f"{kod}:{alan}")
        bilanco = veri.get("bilanco") or []
        if not bilanco:
            # Kaynakta veri olmayan hisseler (or. DSTKF) bilgi notu olarak gecer.
            bilgi.append(kod)
        elif len(bilanco) < 4:
            sorunlar.append(f"{kod}: bilanço dönemi az ({len(bilanco)})")
        for s in bilanco:
            if not s.get("donem"):
                sorunlar.append(f"{kod}: dönemsiz kayıt")
                continue
            for kalem in ("satis", "net_kar", "toplam_varlik", "ozsermaye"):
                v = s.get(kalem)
                if v is not None and not isinstance(v, (int, float)):
                    sorunlar.append(f"{kod}: {s['donem']} {kalem} sayı değil")
    print(f"Hisse sayısı: {len(kodlar)}")
    print(f"Dosya eksik: {len(eksik_dosya)}" + (f" -> {', '.join(eksik_dosya)}" if eksik_dosya else ""))
    print(f"Metin eksik/eşik altı: {len(eksik_metin)}" + (f" -> {', '.join(eksik_metin)}" if eksik_metin else ""))
    print(f"Bilanço verisi olmayan (bilgi): {len(bilgi)}" + (f" -> {', '.join(bilgi)}" if bilgi else ""))
    print(f"Veri sorunu: {len(sorunlar)}" + (" -> " + "; ".join(sorunlar[:15]) if sorunlar else ""))
    return not (eksik_dosya or eksik_metin or sorunlar)


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Elle eklenen hisse analiz bölümleri (bilanço + değerlendirme).")
    ap.add_argument("--kontrol", action="store_true", help="kapsam ve tutarlılık denetimi")
    ap.add_argument("--bilanco-cek", nargs="*", metavar="KOD", default=None,
                    help="İş Yatırım'dan mali tabloları çekip data/hisse-analiz dosyalarını yeniler")
    ap.add_argument("--baslangic-yili", type=int, default=2024,
                    help="mali tablo çekiminde ilk yıl (varsayılan 2024)")
    args = ap.parse_args()
    if args.bilanco_cek is not None:
        kodlar = [k.upper() for k in args.bilanco_cek] or None
        bilanco_cek(kodlar=kodlar, baslangic=args.baslangic_yili)
        return
    if args.kontrol:
        ok = kontrol()
        raise SystemExit(0 if ok else 1)
    ap.print_help()


if __name__ == "__main__":
    main()

