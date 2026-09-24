# -*- coding: utf-8 -*-
"""Makro rejim cekirdegi (deterministik).

Neden var: makro-analiz.html zaten "aktarim mekanizmalari" ve "BIST 30'a
yansimalar / sektor kanallari" bolumlerini iceriyor; ancak rejim tespiti
yalnizca LLM metninin icinde geciyordu. Ayni veriyle ayni etiketi veren,
kaydedilebilen ve zaman icinde karsilastirilabilen bir katman yoktu.

Bu modul:
  1) data/makro-snapshot.json'daki kilitli gostergelerden kural tabanli rejim etiketi uretir,
  2) sektor aktarim matrisini hesaplar (site SEKTORLER tanimiyla ayni adlar),
  3) her kosuda data/makro-rejim/<tarih>.json olarak kaydeder -> rejim zaman
     serisi boylece birikmeye baslar (dezenflasyon gibi "degisim" temelli
     etiketler ancak seri biriktikce hesaplanabilir).

Hicbir sayfa uretimini etkilemez; saf fonksiyonlar + istege bagli kayit.
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta

MAKRO_YOL = "data/makro-snapshot.json"
REJIM_DIR = "data/makro-rejim"

# --- Sektor duyarliliklari: +2 = kanal yukselince sektor kazanir, -2 = kaybeder.
# Kanallar: faiz (reel faiz seviyesi), ic_talep (buyume), kur (TL degeri),
# emtia (girdi/urun fiyati). Su an yalnizca faiz ve ic_talep hesaplanabiliyor.
SEKTOR_DUYARLILIK = {
    "Bankacılık": {"faiz": 1, "ic_talep": 1, "kur": 0, "emtia": 0},
    "Holding": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": 0},
    "Havacılık & Ulaştırma": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": -1},
    "Otomotiv": {"faiz": -1, "ic_talep": 2, "kur": 1, "emtia": -1},
    "Enerji & Petrokimya": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": 1},
    "Perakende": {"faiz": -1, "ic_talep": 2, "kur": -1, "emtia": -1},
    "Metal & Madencilik": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": 1},
    "Kimya & Gübre": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": -1},
    "Telekom": {"faiz": -1, "ic_talep": 1, "kur": -1, "emtia": 0},
    "Gıda & İçecek": {"faiz": -1, "ic_talep": 1, "kur": -1, "emtia": -1},
    "Cam & Seramik": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": -1},
    "Savunma": {"faiz": -1, "ic_talep": 0, "kur": 1, "emtia": 0},
    "Gayrimenkul": {"faiz": -2, "ic_talep": 1, "kur": 0, "emtia": 0},
    "Finans (Diğer)": {"faiz": -1, "ic_talep": 1, "kur": 0, "emtia": 0},
    "Sanayi (Diğer)": {"faiz": -1, "ic_talep": 1, "kur": 1, "emtia": 1},
}

# Sektor -> hisse eslemesi (bot.SEKTORLER ile ayni; burada kopya tutulur ki
# modul bot'u import etmeden tek basina calissin).
SEKTOR_HISSELER = {
    "Bankacılık": ["AKBNK", "GARAN", "ISCTR", "VAKBN", "YKBNK"],
    "Holding": ["KCHOL", "SAHOL"],
    "Havacılık & Ulaştırma": ["THYAO", "PGSUS", "TAVHL"],
    "Otomotiv": ["FROTO", "TOASO"],
    "Enerji & Petrokimya": ["TUPRS", "PETKM", "ENKAI", "ASTOR"],
    "Perakende": ["BIMAS", "MGROS"],
    "Metal & Madencilik": ["EREGL", "KRDMD"],
    "Kimya & Gübre": ["SASA", "GUBRF"],
    "Telekom": ["TCELL", "TTKOM"],
    "Gıda & İçecek": ["AEFES"],
    "Cam & Seramik": ["SISE"],
    "Savunma": ["ASELS"],
    "Gayrimenkul": ["EKGYO"],
    "Finans (Diğer)": ["DSTKF"],
    "Sanayi (Diğer)": ["TRALT"],
}


# --------------------------------------------------------------------------
def gostergeleri_yukle(yol=MAKRO_YOL):
    """Snapshot -> {(ulke, ad): deger}. Okunamazsa {}."""
    try:
        with io.open(yol, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    out = {}
    if d.get("schema") == "borsa-raporlari/makro-snapshot":
        for g in (d.get("records") or []):
            try:
                out[(str(g.get("country", "")), str(g.get("label", "")))] = float(g.get("value"))
            except (TypeError, ValueError):
                continue
        return out
    for g in (d.get("gostergeler") or []):
        try:
            out[(str(g.get("ulke", "")), str(g.get("ad", "")))] = float(g.get("deger"))
        except (TypeError, ValueError):
            continue
    return out


def piyasa_yukle(yol=MAKRO_YOL):
    """Snapshot piyasa blogu -> {indicator: {value, previous, change, unit}}."""
    try:
        with io.open(yol, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    out = {}
    for p in (d.get("piyasa") or []):
        kod = str(p.get("indicator") or "")
        if not kod:
            continue
        kayit = {}
        for alan in ("value", "previous", "change"):
            try:
                kayit[alan] = None if p.get(alan) is None else float(p.get(alan))
            except (TypeError, ValueError):
                kayit[alan] = None
        kayit["unit"] = str(p.get("unit") or "")
        kayit["label"] = str(p.get("label") or "")
        out[kod] = kayit
    return out


# Alt bilgi sikligi: "Enflasyon" aramasinda yillik kirilim tercih edilir.
_ALT_SIKLIK = ("(aylık)", "(haftalık)", "(çeyreklik)", "(günlük)")


def _bul(g, ulke, ad_parca):
    """(ulke, ad) icinden gosterge degeri.

    Once TAM etiket eslemesi denenir ("Politika faizi"); bulunamazsa parca
    esleyen adaylar siralanir: one-ek eslemesi once, altbilgi siklikli
    kirilim (aylik/ceyreklik) sonra, en kisa aday en son. Boylece snapshot'ta
    once gelen "Enflasyon (aylik)"=0.22 yillik yerine degil deger olarak
    alinmaz; rejim hesabi "Enflasyon (yillik)" ile calisir.
    """
    parca = str(ad_parca).strip().casefold()
    tam, adaylar = None, []
    for (u, a), v in g.items():
        if str(ulke).casefold() not in str(u).casefold():
            continue
        etiket = str(a).strip()
        eslesme = etiket.casefold()
        if eslesme == parca:
            tam = v
            break
        if parca in eslesme:
            adaylar.append((eslesme.startswith(parca),
                            any(m in eslesme for m in _ALT_SIKLIK),
                            len(etiket), v))
    if tam is not None:
        return tam
    if not adaylar:
        return None
    adaylar.sort(key=lambda x: (not x[0], x[1], x[2]))
    return adaylar[0][3]


def rejim_hesapla(g, onceki=None, piyasa=None):
    """Kural tabanli rejim etiketi. `onceki`: daha onceki kayit (trend icin).

    `piyasa`: snapshot piyasa blogu ({indicator: {...}}). Varsa kur/emtia
    sinyalleri ve global risk seviyeleri hesaba katilir; yoksa kanallar None
    kalir (eski davranis).
    """
    enfl = _bul(g, "Türkiye", "Enflasyon")
    faiz = _bul(g, "Türkiye", "Politika faizi")
    buyume = _bul(g, "Türkiye", "Büyüme")
    issizlik = _bul(g, "Türkiye", "İşsizlik")
    cari = _bul(g, "Türkiye", "Cari denge")
    rezerv = _bul(g, "Türkiye", "rezerv")

    p = piyasa or {}

    def _p(kod, alan):
        kayit = p.get(kod) or {}
        deger = kayit.get(alan) if isinstance(kayit, dict) else None
        return deger if isinstance(deger, (int, float)) else None

    usd_deg, brent_deg = _p("usdtry", "change"), _p("brent", "change")
    # kur: +1 = USD/TRY yukseldi (TL zayifliyor); emtia: +1 = Brent artti.
    kur_s = (None if usd_deg is None else
             1 if usd_deg > 0.2 else (-1 if usd_deg < -0.2 else 0))
    emtia_s = (None if brent_deg is None else
               1 if brent_deg > 0.5 else (-1 if brent_deg < -0.5 else 0))
    global_risk = {"vix": _p("vix", "value"), "dxy": _p("dxy", "value"),
                   "ust10y": _p("ust10y", "value"),
                   "brent": _p("brent", "value")}

    reel = round(faiz - enfl, 2) if (faiz is not None and enfl is not None) else None

    if reel is None:
        durus = "bilinmiyor"
    elif reel >= 5:
        durus = "sıkı"
    elif reel >= 0:
        durus = "nötr"
    else:
        durus = "gevşek (negatif reel faiz)"

    if enfl is None:
        seviye = "bilinmiyor"
    elif enfl >= 40:
        seviye = "çok yüksek"
    elif enfl >= 20:
        seviye = "yüksek"
    elif enfl >= 10:
        seviye = "orta"
    else:
        seviye = "düşük"

    # Dezenflasyon yalnizca onceki kayitla karsilastirilarak hesaplanir.
    yon = "seri yeni (kayit birikiyor)"
    if onceki and isinstance(onceki.get("enflasyon"), (int, float)) and enfl is not None:
        fark = round(enfl - onceki["enflasyon"], 2)
        yon = ("dezenflasyon" if fark < -0.05 else
               "yeniden hızlanma" if fark > 0.05 else "yatay")

    if buyume is None:
        talep = "bilinmiyor"
    elif buyume >= 5:
        talep = "güçlü"
    elif buyume >= 2:
        talep = "ılımlı"
    elif buyume >= 0:
        talep = "zayıf"
    else:
        talep = "daralma"

    # Olcum kanallari: verisi snapshot'ta OLAN kanal eksik listesine yazilmaz.
    eksik = []
    if usd_deg is None:
        eksik.append("kur (TL değişimi)")
    if brent_deg is None:
        eksik.append("emtia")
    eksik.append("çekirdek enflasyon")
    eksik.append("enflasyon beklentisi")
    if (_bul(g, "Türkiye", "Kredi Büyümesi") is None
            and _bul(g, "Türkiye", "M3") is None):
        eksik.append("kredi büyümesi / M3")
    if _bul(g, "Türkiye", "Bütçe") is None:
        eksik.append("bütçe")
    if _bul(g, "Türkiye", "Reel Efektif") is None:
        eksik.append("REER")
    if not any(v is not None for v in global_risk.values()):
        eksik.append("global risk (VIX/DXY/UST10Y/Brent)")
    eksik.append("CDS")
    eksik.append("yabancı takas oranı / forward P/E")

    return {
        "tarih": datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d"),
        "olusturma": datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
        "enflasyon": enfl, "politika_faizi": faiz, "reel_faiz": reel,
        "buyume": buyume, "issizlik": issizlik, "cari_denge": cari, "rezerv": rezerv,
        "para_durusu": durus, "enflasyon_seviyesi": seviye, "enflasyon_yonu": yon,
        "ic_talep": talep,
        "piyasa_sinyalleri": {"kur": kur_s, "emtia": emtia_s},
        "global_risk": global_risk,
        "etiket": ("%s enflasyon + %s para politikası (%s puan reel faiz) · %s iç talep"
                   % (seviye, durus, reel if reel is not None else "?", talep)),
        "globall": {
            "ABD": {"enflasyon": _bul(g, "ABD", "Enflasyon"),
                    "faiz": _bul(g, "ABD", "Politika faizi")},
            "Euro Bölgesi": {"enflasyon": _bul(g, "Euro", "Enflasyon"),
                             "faiz": _bul(g, "Euro", "Politika faizi")},
        },
        "eksik_kanallar": eksik,
    }


def sinyaller(rejim):
    """Rejimden sinyal kanallari. Olculemeyen kanal None kalir."""
    f = rejim.get("reel_faiz")
    faiz_sinyal = None if f is None else (1 if f >= 5 else (-1 if f < 0 else 0))
    b = rejim.get("buyume")
    talep_sinyal = None if b is None else (1 if b >= 2 else (-1 if b < 0 else 0))
    ps = rejim.get("piyasa_sinyalleri") or {}
    return {"faiz": faiz_sinyal, "ic_talep": talep_sinyal,
            "kur": ps.get("kur"), "emtia": ps.get("emtia")}


def sektor_aktarimi(rejim):
    """Sektor bazli egilim: {sektor: {skor, egilim, gerekce, kanallar}}."""
    sig = sinyaller(rejim)
    sonuc = {}
    for sektor, duy in SEKTOR_DUYARLILIK.items():
        skor = 0
        kullanilan = []
        for kanal, s in sig.items():
            if s is None:
                continue
            katki = duy.get(kanal, 0) * s
            if katki:
                skor += katki
                kullanilan.append("%s %s" % (kanal, "artis" if s > 0 else "azalis"))
        egilim = "olumlu" if skor > 0 else ("olumsuz" if skor < 0 else "nötr")
        sonuc[sektor] = {"skor": skor, "egilim": egilim,
                         "kanallar": kullanilan or ["ölçülebilir kanal yok"],
                         "hisseler": SEKTOR_HISSELER.get(sektor, [])}
    return dict(sorted(sonuc.items(), key=lambda kv: -kv[1]["skor"]))


def ozet_metin(rejim, aktarim):
    out = []
    out.append("MAKRO REJİM: " + rejim["etiket"])
    out.append("  reel faiz: %s | enflasyon yönü: %s | cari denge: %s | rezerv: %s"
               % (rejim["reel_faiz"], rejim["enflasyon_yonu"], rejim["cari_denge"], rejim["rezerv"]))
    global_s = ", ".join(
        "%s rejim: reel faiz %s" % (u, (round(v["faiz"] - v["enflasyon"], 2)
                                       if v["faiz"] is not None and v["enflasyon"] is not None else "?"))
        for u, v in rejim["globall"].items())
    out.append("  " + global_s)
    out.append("")
    out.append("SEKTÖR AKTARIMI (yalnız ölçülebilen kanallar)")
    for s, d in aktarim.items():
        out.append("  %-22s %-8s skor=%+d  (%s)" % (s, d["egilim"], d["skor"], ", ".join(d["kanallar"])))
    out.append("")
    out.append("ÖLÇÜLEMEYEN KANALLAR: " + ", ".join(rejim["eksik_kanallar"][:5]) + " ...")
    return "\n".join(out)


def kart_html(rejim, aktarim):
    """Sayfaya gomulebilecek rejim karti (self-contained, stil disaridan)."""
    renk = {"olumlu": "#047857", "olumsuz": "#b91c1c", "nötr": "#6b7280"}
    satir = "".join(
        "<tr><td><strong>%s</strong></td><td>%s</td><td style='color:%s;font-weight:600'>%s</td>"
        "<td>%+d</td><td>%s</td></tr>"
        % (s, ", ".join(d["hisseler"]), renk[d["egilim"]], d["egilim"], d["skor"], ", ".join(d["kanallar"]))
        for s, d in aktarim.items())
    return (
        '<div class="card" style="margin-top:14px">'
        '<h3 style="margin:0 0 6px">Makro Rejim Kartı <span style="font-weight:400;color:var(--muted);'
        'font-size:12.5px">&middot; kural tabanlı, veriden hesaplanır</span></h3>'
        '<p style="margin:0 0 10px"><strong>%s</strong></p>'
        '<div class="tbl-wrap"><table style="font-size:13px">'
        '<tr><th>Sektör</th><th>Hisseler</th><th>Eğilim</th><th>Skor</th><th>Kanallar</th></tr>%s</table></div>'
        '<p style="margin:8px 0 0;color:var(--muted);font-size:12px">Ölçülemeyen kanallar: %s</p>'
        '</div>' % (rejim["etiket"], satir, ", ".join(rejim["eksik_kanallar"])))


def kaydet(rejim, dizin=REJIM_DIR):
    """Rejim kaydini data/makro-rejim/<tarih>.json olarak yazar; yolu doner."""
    try:
        os.makedirs(dizin, exist_ok=True)
        yol = os.path.join(dizin, rejim["tarih"] + ".json")
        with io.open(yol, "w", encoding="utf-8") as f:
            json.dump(rejim, f, ensure_ascii=False, indent=1, sort_keys=True)
        return yol
    except Exception:
        return None


def son_kayit(dizin=REJIM_DIR, tarih=None):
    try:
        dosyalar = sorted(f for f in os.listdir(dizin) if f.endswith(".json"))
        if tarih:
            dosyalar = [f for f in dosyalar if f[:-5] < tarih]
        if not dosyalar:
            return None
        with io.open(os.path.join(dizin, dosyalar[-1]), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def uret(yol=MAKRO_YOL, kaydet_mi=True):
    g = gostergeleri_yukle(yol)
    if not g:
        return None
    p = piyasa_yukle(yol)
    bugun = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    rejim = rejim_hesapla(g, onceki=son_kayit(tarih=bugun), piyasa=p)
    aktarim = sektor_aktarimi(rejim)
    if kaydet_mi:
        rejim["kayit_yolu"] = kaydet(rejim)
    return {"rejim": rejim, "aktarim": aktarim}


if __name__ == "__main__":
    sonuc = uret(kaydet_mi="--kaydet" in sys.argv)
    if not sonuc:
        print("makro snapshot okunamadi (data/makro-snapshot.json).")
        sys.exit(1)
    print(ozet_metin(sonuc["rejim"], sonuc["aktarim"]))
    if sonuc["rejim"].get("kayit_yolu"):
        print("\nkaydedildi: " + str(sonuc["rejim"]["kayit_yolu"]))
