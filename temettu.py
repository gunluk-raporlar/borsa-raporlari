# -*- coding: utf-8 -*-
"""Temettü sayfası üreteci.

Girdi : data/temettu/seriler/<KOD>.json (temettu_cek.py üretir) + data/temettu/kur.json
Çıktı : temettu-veri/ozet.json, temettu-veri/<KOD>.json, temettu-veri/kur.json  (yayına giden veri)
        temettu.html                                                           (sayfa)

Kullanım:
    python temettu_cek.py      # veriyi tazele (borsapy + Yahoo)
    python temettu.py          # özet + veri dosyaları + sayfa
"""
import json
import os
import statistics
from datetime import date, datetime, timedelta

os.environ.setdefault("AMD_API_KEY", "temettu")

try:  # CI'da bot.py ve bağımlılıkları kurulu; yerelde yoksa canlı iskeletten türetilir
    import bot  # noqa: E402
    _BOT = bot
except Exception as _hata:  # pragma: no cover
    print(f"[bilgi] bot.py yüklenemedi ({_hata}); sayfa iskeleti takvim.html'den alınacak")
    _BOT = None

KOK = os.path.dirname(os.path.abspath(__file__))
VERI = os.path.join(KOK, "data", "temettu")
SERI = os.path.join(VERI, "seriler")
YAYIN = os.path.join(KOK, "temettu-veri")

# Asgari ücret (net, TL/ay) — "Asgari Ücret ile Alım" modu için yaklaşık tablo.
# Kaynak: yıl içinde yürürlükte olan net asgari ücret; Temmuz zammı olan yıllarda
# yıl sonu değeri kullanılır. Kesin tutar için resmî kaynağa bakınız.
ASGARI_UCRET = {
    "2010": 577, "2011": 629, "2012": 673, "2013": 773, "2014": 846, "2015": 949,
    "2016": 1300, "2017": 1404, "2018": 1603, "2019": 2020, "2020": 2324,
    "2021": 2826, "2022": 5500, "2023": 11402, "2024": 17002, "2025": 22104,
    "2026": 22104,
}

AY_AD = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
         "Eylül", "Ekim", "Kasım", "Aralık"]


def _oku(kod):
    with open(os.path.join(SERI, kod + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _tum_seriler():
    if not os.path.isdir(SERI):
        raise SystemExit("Veri yok. Önce: python temettu_cek.py")
    out = {}
    for dosya in sorted(os.listdir(SERI)):
        if not dosya.endswith(".json"):
            continue
        kod = dosya[:-5]
        try:
            out[kod] = _oku(kod)
        except Exception as e:
            print(f"! {kod} okunamadi: {e}")
    return out


def _fiyat_bul(seri, tarih):
    """tarih veya öncesindeki son kapanış."""
    son = None
    for g, f in seri:
        if g <= tarih:
            son = f
        else:
            break
    return son


def _tr(n, o=2):
    s = f"{n:,.{o}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def takvim_olustur(seriler):
    """Geçmiş temettü olayları + gelecek 12 ay için desenden türetilmiş tahminler."""
    olaylar = []
    bugun = date.today()
    for kod, s in seriler.items():
        for tarih, tutar in s.get("temettu", []):
            fiyat = _fiyat_bul(s["fiyat"], tarih)
            if not fiyat:
                continue
            olaylar.append({
                "tarih": tarih, "kod": kod, "tutar": tutar, "fiyat": fiyat,
                "verim": round(tutar / fiyat * 100, 2), "tahmin": False,
            })

    # --- tahmin: son 5 yılda aynı ayda en az 2 kez ödeme yapan hisseler
    tahminler = []
    for kod, s in seriler.items():
        kayit = [t for t in s.get("temettu", []) if t[0] >= str(bugun.year - 5)]
        if len(kayit) < 2:
            continue
        aylar = {}
        for tarih, tutar in kayit:
            aylar.setdefault(int(tarih[5:7]), []).append((tarih, tutar))
        for ay, liste in aylar.items():
            if len(liste) < 2:
                continue
            gunler = sorted(int(t[0][8:10]) for t in liste)
            tahmini_gun = int(statistics.median(gunler))
            son = liste[-1][0]
            yil = int(son[:4]) + 1
            try:
                t = date(yil, ay, min(tahmini_gun, 28))
            except ValueError:
                continue
            while t <= bugun:
                try:
                    t = date(t.year + 1, ay, min(tahmini_gun, 28))
                except ValueError:
                    break
            if (t - bugun).days > 365:
                continue
            son_fiyat = s["fiyat"][-1][1] if s.get("fiyat") else None
            tahminler.append({
                "tarih": t.isoformat(), "kod": kod,
                "tutar": liste[-1][1], "fiyat": son_fiyat,
                "verim": round(liste[-1][1] / son_fiyat * 100, 2) if son_fiyat else None,
                "tahmin": True,
            })
    olaylar.extend(tahminler)
    olaylar.sort(key=lambda o: (o["tarih"], o["kod"]))
    return olaylar


def _bedelsiz_carpan(bedelsiz, tarih):
    """tarihten SONRA yapılan bedelsizlerin çarpanı (fiyat/temettüyü bugünkü pay
    başına çevirmek için). Örn. 2025'te %900 bedelsiz varsa 2024 temettüsü 10'a bölünür."""
    c = 1.0
    for g, r in bedelsiz:
        if g > tarih:
            c *= r
    return c


def verim_satirlari(seriler):
    bugun = date.today()
    satirlar = []
    for kod, s in seriler.items():
        temettu = s.get("temettu", [])
        bedelsiz = s.get("bedelsiz", [])
        fiyat_seri = s.get("fiyat", [])
        if not fiyat_seri:
            continue
        fiyat = fiyat_seri[-1][1]
        son = temettu[-1] if temettu else None

        # Tüm temettüleri bugünkü pay başına çevir (bedelsiz düzeltmesi)
        duz = [(g, t / _bedelsiz_carpan(bedelsiz, g)) for g, t in temettu]
        son_tutar = duz[-1][1] if duz else None

        # Son 12 ay: son ödeme tarihinden geriye 365 gün (temettü ödemeyen dönemde
        # verim sıfıra düşmesin; "son ödeme dönemi" verimi gösterilir).
        on_iki = 0.0
        if son:
            bitis = date.fromisoformat(son[0])
            bas = (bitis - timedelta(days=365)).isoformat()
            on_iki = sum(t for g, t in duz if bas <= g <= son[0])

        # 5 yıl ortalama verim: her yılın toplam temettüsü / o yıl sonu fiyatı
        yillik = {}
        for g, t in duz:
            yillik[g[:4]] = yillik.get(g[:4], 0) + t
        verimler = []
        for y in sorted(yillik)[-5:]:
            f = _fiyat_bul(fiyat_seri, f"{y}-12-31") or fiyat
            if f:
                verimler.append(yillik[y] / f * 100)

        # kesintisiz yıl: son yıldan geriye doğru temettü verilen yıl sayısı.
        # Fiyat serimizin başladığı yıldan öncesi sayılmaz (veri yok, kanıt yok).
        ilk_yil = int(fiyat_seri[0][0][:4])
        kesinti = 0
        yil = bugun.year
        while yil >= ilk_yil:
            if str(yil) in yillik:
                kesinti += 1
            elif str(yil - 1) in yillik and kesinti == 0:
                yil -= 1
                continue
            else:
                break
            yil -= 1

        on_yil = sum(t for g, t in duz if g >= f"{bugun.year - 10}-01-01")

        satirlar.append({
            "kod": kod,
            "sonTutar": son_tutar,
            "sonTarih": son[0] if son else None,
            "fiyat": fiyat,
            "verim": round(on_iki / fiyat * 100, 2) if on_iki else 0.0,
            "onIkiAy": on_iki,
            "besYilVerim": round(statistics.mean(verimler), 2) if verimler else 0.0,
            "yilSayisi": kesinti,
            "toplamOnYil": on_yil,
        })
    satirlar.sort(key=lambda r: r["verim"], reverse=True)
    return satirlar


def yayin_verisi(seriler, takvim, satirlar, kur):
    os.makedirs(YAYIN, exist_ok=True)

    # kur: sadece gereken aralık, 4 hane
    kur_kisa = [[g, round(u, 4), round(a, 2)] for g, u, a in kur["seri"]]
    with open(os.path.join(YAYIN, "kur.json"), "w", encoding="utf-8") as f:
        json.dump({"guncelleme": kur.get("guncelleme", ""), "seri": kur_kisa},
                  f, ensure_ascii=False, separators=(",", ":"))

    for kod, s in seriler.items():
        kayit = {
            "kod": kod,
            "fiyat": [[g, round(p, 2)] for g, p in s["fiyat"]],
            "temettu": s.get("temettu", []),
            "bedelsiz": s.get("bedelsiz", []),
        }
        with open(os.path.join(YAYIN, kod + ".json"), "w", encoding="utf-8") as f:
            json.dump(kayit, f, ensure_ascii=False, separators=(",", ":"))

    ozet = {
        "guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "takvim": takvim,
        "verim": satirlar,
        "asgariUcret": ASGARI_UCRET,
        "hisseler": [{"kod": r["kod"], "fiyat": r["fiyat"], "verim": r["verim"]} for r in satirlar],
    }
    with open(os.path.join(YAYIN, "ozet.json"), "w", encoding="utf-8") as f:
        json.dump(ozet, f, ensure_ascii=False, separators=(",", ":"))
    return ozet


def _takvim_html(takvim):
    bugun = date.today().isoformat()
    gruplar = {}
    for o in takvim:
        if o["tahmin"]:
            continue
        gruplar.setdefault(o["tarih"][:7], []).append(o)
    aylar = sorted(gruplar, reverse=True)[:14]
    parcalar = []
    for ay in aylar:
        y, m = ay.split("-")
        parcalar.append(f'<div class="tm-ay"><h3>{AY_AD[int(m)-1]} {y}</h3>')
        for o in sorted(gruplar[ay], key=lambda x: (x["tarih"], x["kod"])):
            gun = int(o["tarih"][8:10])
            g = date.fromisoformat(o["tarih"])
            gun_adi = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][g.weekday()]
            parcalar.append(
                f'<div class="tm-olay"><span class="t">{gun:02d} {gun_adi}</span>'
                f'<span class="k">{o["kod"]}</span>'
                f'<span class="v">{_tr(o["tutar"], 4)} TL</span>'
                f'<span class="f">{_tr(o["fiyat"], 2)} TL</span>'
                f'<span class="verim">%{_tr(o["verim"], 2)}</span></div>'
            )
        parcalar.append("</div>")
    return "".join(parcalar) if parcalar else '<p class="tm-bos">Kayıt yok.</p>'


def _verim_html(satirlar):
    parcalar = []
    for r in satirlar:
        if not r["sonTutar"]:
            parcalar.append(
                f'<tr data-kod="{r["kod"]}" data-sonTutar="0" data-sonTarih="" data-fiyat="{r["fiyat"]}" '
                f'data-verim="0" data-onIkiAy="0" data-besYilVerim="0" data-yilSayisi="0" data-toplamOnYil="0">'
                f'<td><b>{r["kod"]}</b></td><td class="sayi">—</td><td>—</td>'
                f'<td class="sayi">{_tr(r["fiyat"], 2)}</td><td class="sayi">—</td><td class="sayi">—</td>'
                f'<td class="sayi">—</td><td class="sayi">0</td><td class="sayi">—</td></tr>'
            )
            continue
        g = date.fromisoformat(r["sonTarih"])
        parcalar.append(
            f'<tr data-kod="{r["kod"]}" data-sonTutar="{r["sonTutar"]}" data-sonTarih="{r["sonTarih"]}" '
            f'data-fiyat="{r["fiyat"]}" data-verim="{r["verim"]}" data-onIkiAy="{r["onIkiAy"]}" '
            f'data-besYilVerim="{r["besYilVerim"]}" data-yilSayisi="{r["yilSayisi"]}" data-toplamOnYil="{r["toplamOnYil"]}">'
            f'<td><b>{r["kod"]}</b></td>'
            f'<td class="sayi">{_tr(r["sonTutar"], 4)}</td>'
            f'<td>{g.strftime("%d.%m.%Y")}</td>'
            f'<td class="sayi">{_tr(r["fiyat"], 2)}</td>'
            f'<td class="sayi"><b>%{_tr(r["verim"], 2)}</b></td>'
            f'<td class="sayi">{_tr(r["onIkiAy"], 2)}</td>'
            f'<td class="sayi">%{_tr(r["besYilVerim"], 2)}</td>'
            f'<td class="sayi">{r["yilSayisi"]}</td>'
            f'<td class="sayi">{_tr(r["toplamOnYil"], 2)}</td></tr>'
        )
    return "".join(parcalar)


def _yedek_sayfa(baslik, icerik, aciklama, ld_ek=None):
    """bot.py yoksa: canlı sitedeki takvim.html iskeletini alıp gövdeyi değiştirir.
    Böylece menü, tema, ticker ve alt menü canlı siteyle birebir aynı kalır."""
    import re
    with open(os.path.join(KOK, "takvim.html"), encoding="utf-8") as f:
        s = f.read()

    site = "https://borsa-raporlari.pages.dev"
    s = re.sub(r"<title>.*?</title>", lambda m: f"<title>{baslik}</title>", s, count=1, flags=re.S)
    s = re.sub(r'<meta name="description" content="[^"]*">',
               lambda m: f'<meta name="description" content="{aciklama}">', s, count=1)
    s = re.sub(r'<link rel="canonical" href="[^"]*">',
               lambda m: f'<link rel="canonical" href="{site}/temettu">', s, count=1)
    for prop in ("og:title", "twitter:title"):
        s = re.sub(rf'<meta property="{prop}" content="[^"]*">',
                   lambda m: f'<meta property="{prop}" content="{baslik}">', s, count=1)
    for prop in ("og:description", "twitter:description"):
        s = re.sub(rf'<meta property="{prop}" content="[^"]*">',
                   lambda m: f'<meta property="{prop}" content="{aciklama}">', s, count=1)
    s = re.sub(r'<meta name="twitter:title" content="[^"]*">',
               lambda m: f'<meta name="twitter:title" content="{baslik}">', s, count=1)
    s = re.sub(r'<meta name="twitter:description" content="[^"]*">',
               lambda m: f'<meta name="twitter:description" content="{aciklama}">', s, count=1)
    s = re.sub(r'<meta property="og:url" content="[^"]*">',
               lambda m: f'<meta property="og:url" content="{site}/temettu">', s, count=1)

    ld_json = json.dumps({
        "@context": "https://schema.org", "@type": "WebPage", "name": baslik,
        "description": aciklama, "url": site + "/temettu", "inLanguage": "tr",
        "isPartOf": {"@type": "WebSite", "name": "BIST 30 Günlük Raporlar", "url": site + "/"},
    }, ensure_ascii=False)
    ld_ek_html = f'<script type="application/ld+json">{ld_ek}</script>' if ld_ek else ""
    s = re.sub(r'<script type="application/ld\+json">.*?</script>',
               lambda m: f'<script type="application/ld+json">{ld_json}</script>{ld_ek_html}',
               s, count=1, flags=re.S)

    s = s.replace('<a href="temettu">💰 Temettü</a>', '<a href="temettu" class="active">💰 Temettü</a>')
    s = s.replace('<a href="takvim" class="active">📅 Takvim</a>', '<a href="takvim">📅 Takvim</a>')
    # Widget'ları (saat/hava/arama/radyo) koru: yalnızca içerik bloğunu değiştir.
    if "<!-- Asıl Sayfa İçeriği -->" in s:
        s = re.sub(r'<!-- Asıl Sayfa İçeriği -->.*?(</main>)',
                   lambda m: "<!-- Asıl Sayfa İçeriği -->\n" + icerik + "\n" + m.group(1),
                   s, count=1, flags=re.S)
    else:
        s = re.sub(r'(<main class="wrap">).*?(</main>)',
                   lambda m: m.group(1) + "\n" + icerik + "\n" + m.group(2), s, count=1, flags=re.S)
    return s


def _sayfa(baslik, icerik, aciklama, ld_ek=None):
    if _BOT is not None:
        return _BOT._sayfa(baslik, icerik, "temettu", yol="temettu.html",
                           aciklama=aciklama, ld_ek=ld_ek)
    return _yedek_sayfa(baslik, icerik, aciklama, ld_ek)


def sayfa_uret(ozet, seriler):
    with open(os.path.join(KOK, "temettu-kaynak", "govde.html"), encoding="utf-8") as f:
        govde = f.read()
    with open(os.path.join(KOK, "temettu-kaynak", "hesap.js"), encoding="utf-8") as f:
        js = f.read()

    takvim = ozet["takvim"]
    satirlar = ozet["verim"]
    bugun = date.today()

    bu_yil = [o for o in takvim if not o["tahmin"] and o["tarih"][:4] == str(bugun.year)]
    odeyen = len({o["kod"] for o in bu_yil})
    verimli = [r for r in satirlar if r["verim"] > 0]
    ort_verim = sum(r["verim"] for r in verimli) / len(verimli) if verimli else 0
    en_iyi = verimli[0] if verimli else None
    sonraki = next((o for o in takvim if o["tahmin"] and o["tarih"] >= bugun.isoformat()), None)

    # hisse seçenekleri (verim sırasına göre)
    secenekler = "".join(f'<option value="{r["kod"]}">{r["kod"]}</option>' for r in satirlar)

    # tarih sınırları
    tum_tarihler = [g for s in seriler.values() for g, _ in s["fiyat"]]
    min_tarih = min(tum_tarihler) if tum_tarihler else "2010-01-01"
    max_tarih = max(tum_tarihler) if tum_tarihler else bugun.isoformat()
    bas_default = f"{max_tarih[:4]}-01-02" if max_tarih[:4] != "2010" else min_tarih

    try:
        with open(os.path.join(KOK, "data", "enflasyon.json"), encoding="utf-8") as f:
            enf = round(float(json.load(f)["oran"]) * 100, 1)
    except Exception:
        enf = 30.0

    yerine = {
        "{{TAKVIM}}": _takvim_html(takvim),
        "{{VERIM}}": _verim_html(satirlar),
        "{{HISSE_SEC}}": secenekler,
        "{{MIN_TARIH}}": min_tarih,
        "{{MAX_TARIH}}": max_tarih,
        "{{BAS_DEFAULT}}": bas_default,
        "{{VERI_ARALIGI}}": (f"{min_tarih[8:10]}.{min_tarih[5:7]}.{min_tarih[:4]} – "
                             f"{max_tarih[8:10]}.{max_tarih[5:7]}.{max_tarih[:4]}"),
        "{{ENF_ORAN}}": str(enf),
        "{{GUNCELLEME}}": ozet["guncelleme"],
        "{{S_BURSYIL}}": f"{odeyen} / {len(seriler)}",
        "{{S_BURSYIL_ADET}}": f"{bugun.year}",
        "{{S_VERIM}}": f"%{_tr(ort_verim, 2)}",
        "{{S_VERIM_ADET}}": str(len(verimli)),
        "{{S_VEREN}}": f"%{_tr(en_iyi['verim'], 2)}" if en_iyi else "—",
        "{{S_VEREN_AD}}": en_iyi["kod"] if en_iyi else "—",
        "{{S_SONRAKI}}": (date.fromisoformat(sonraki["tarih"]).strftime("%d %b") if sonraki else "—"),
        "{{S_SONRAKI_AD}}": (sonraki["kod"] + " · tahmini" if sonraki else "tahmin yok"),
        "{{YIL_CHIP}}": "",
    }
    for k, v in yerine.items():
        govde = govde.replace(k, v)

    # stil bloğu gövdenin başında gelir; hesaplayıcı JS'i sayfa sonuna eklenir
    icerik = govde + "\n<script>\n" + js + "\n</script>"

    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "Temettü aldığımda hisse fiyatı neden düşüyor?",
             "acceptedAnswer": {"@type": "Answer", "text": "Ex-tarihte şirketin kasasından nakit çıktığı için fiyat temettü tutarı kadar düzeltilir. Bu bir kayıp değildir: düşen fiyat ve alınan nakit toplamı önceki değeri verir."}},
            {"@type": "Question", "name": "Temettü verimi nasıl hesaplanır?",
             "acceptedAnswer": {"@type": "Answer", "text": "Hisse başına yıllık brüt temettü tutarının hisse fiyatına bölünmesiyle bulunur. Örneğin 100 TL fiyatlı hisse yılda 8 TL temettü dağıtıyorsa verim %8'dir."}},
            {"@type": "Question", "name": "BIST'te temettü stopajı ne kadar?",
             "acceptedAnswer": {"@type": "Answer", "text": "Kâr payı dağıtımında stopaj kaynakta kesilir: 2020 öncesi %15, 2020 ile 21 Aralık 2024 arasındaki ödemelerde %10, 22 Aralık 2024 ve sonrasındaki ödemelerde yeniden %15 (9286 sayılı Cumhurbaşkanı Kararı)."}},
            {"@type": "Question", "name": "Temettü ile lot alımı nedir?",
             "acceptedAnswer": {"@type": "Answer", "text": "Dağıtılan net temettünün aynı gün fiyatından yeniden hisse alımında kullanılmasıdır. Hesaplayıcı bu tercihi gün gün simüle eder."}}
        ]
    }, ensure_ascii=False)

    html = _sayfa(
        "Temettü Merkezi — Takvim, Verim ve Hesaplayıcı",
        icerik,
        aciklama=("BIST 30 temettü takvimi, hisse başına kâr payı geçmişi, temettü verimi tablosu ve "
                  "geçmişe dönük temettü getirisi hesaplayıcısı: düzenli alım, temettü ile lot alımı, "
                  "bedelsiz sermaye artırımı ve stopaj dahil."),
        ld_ek=ld,
    )
    with open(os.path.join(KOK, "temettu.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"temettu.html yazildi ({len(html)/1024:.0f} KB) · {len(takvim)} takvim kaydi · {len(satirlar)} hisse")


def main():
    seriler = _tum_seriler()
    with open(os.path.join(VERI, "kur.json"), encoding="utf-8") as f:
        kur = json.load(f)
    takvim = takvim_olustur(seriler)
    satirlar = verim_satirlari(seriler)
    ozet = yayin_verisi(seriler, takvim, satirlar, kur)
    sayfa_uret(ozet, seriler)


if __name__ == "__main__":
    main()
