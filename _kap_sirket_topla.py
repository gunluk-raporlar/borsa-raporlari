# -*- coding: utf-8 -*-
"""kap-akisi.json'u tamamen resmi KAP kayitlarindan yeniden kurar.

Kaynaklar (ikisi de SSR, curl ile erisilir):
1. Kuresel sayfa: /tr/bildirim-sorgu-sonuc?srcbar=Y&cmp=Y&cat=6&slf=ALL
   (~son 6 gun / 2000 kayit; iliskili sirket kayitlari dahil: DKB, SPK listeleri)
2. Sirket sayfasi: /tr/bildirim-sorgu-sonuc?srcbar=Y&cmp=Y&cat=3&m=<mkkMemberOid>
   (gecerli ay, sirketin tum bildirimleri)

OID bulunmasi: GET /tr/api/member/filter/<KOD> -> [{"mkkMemberOid","title",...}];
adaylar arasinda "A.S." ile biten sirket adi tercih edilir.

Fintables kokenli ogeler tamamen atilir; her kod icin ogeler disclosureIndex
ile tekillestirilir, tarih sirasiyla en fazla 15 oge tutulur.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

KAVANOZ = "_kap_cerez.txt"
HAM_KURESEL = "_kap_kuresel_fresh.html"
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
DOSYA = "data/kap/kap-akisi.json"
OID_DOSYA = "data/kap/kap-oidler.json"


def curl(url, hedef, ek=[], t=60):
    # Cerez dosyasi depoda tutulmuyor (yerelde elle olusturulmustu); yoksa
    # -b eklenmez — KAP cerez istemiyor, istekler cerezsiz de 200 donuyor.
    cerez = ["-b", KAVANOZ] if os.path.exists(KAVANOZ) else []
    # KAP zaman zaman "Empty reply from server" (curl 52) donduruyor; 14.11
    # kosusunda tek bir hisse yuzunden TUM betik dusuyordu. 3 deneme + artan
    # bekleme: gunluk CI isi tek bir gecici hatayla veri tazelemeden kalmasin.
    son_hata = ""
    for deneme in range(3):
        r = subprocess.run(["curl", "-sS", "--max-time", str(t)] + cerez
                           + ek + [url, "-o", hedef], capture_output=True, text=True)
        if r.returncode == 0:
            return
        son_hata = r.stderr.strip()
        if deneme < 2:
            time.sleep(2 * (deneme + 1))
    raise RuntimeError(f"curl basarisiz {url}: {son_hata}")


def kayitlari_ayristir(dosya):
    t = open(dosya, encoding="utf-8", errors="replace").read()
    u = t.replace('\\"', '"')
    dec = json.JSONDecoder()
    out = {}
    for m in re.finditer(r'\{"disclosureBasic":', u):
        try:
            obj, _ = dec.raw_decode(u, m.start())
            k = obj["disclosureBasic"]
            out[k["disclosureIndex"]] = k
        except Exception:
            continue
    return out


def oid_bul(kod):
    """Once dogrulanmis esleme dosyasina bakar (data/kap/kap-oidler.json).

    KAP'in /api/member/filter/<KOD> ucu ayni onekli birden fazla sirket
    donduruyor (GARAN -> 8 aday: Garanti Faktoring, Garanti Portfoy, ...).
    "A.S. ile biten ilk aday" sezgisi bu yuzden yanlis sirketi seciyordu;
    GARAN bu yuzden 0 bildirimle kaliyordu. Esleme dosyasi 30 kodun tamami
    icin KAP sayfasindan dogrulandi, bu yuzden birincil kaynak o.
    """
    try:
        esleme = json.load(open(OID_DOSYA, encoding="utf-8"))["sirketler"]
        oid = (esleme.get(kod) or {}).get("mkkMemberOid")
        if oid:
            return oid
    except Exception:
        pass
    curl(f"https://www.kap.org.tr/tr/api/member/filter/{kod}", "_kap_oid.json")
    adaylar = json.load(open("_kap_oid.json", encoding="utf-8"))
    if not adaylar:
        return None
    sirket = next((a for a in adaylar
                   if a.get("title", "").rstrip().endswith(("A.Ş.", "A.S."))), adaylar[0])
    return sirket["mkkMemberOid"]


def _kodlari_ayir(deger):
    """KAP 'stockCode' alani tek kod degil, virgulle ayrilmis liste olabiliyor
    (GARAN kayitlarinda 'GARAN, TGB'). Eski kod `stockCode == kod` diye TAM
    esitlik aradigi icin bu kayitlarin hepsi eleniyordu: GARAN'in 04.11-09.11
    arasindaki 5 bildirimi (Paylara Genel Alimlara Iliskin Bildirim vb.)
    sayfaya hic dusmuyordu."""
    return [x.strip() for x in (deger or "").split(",") if x.strip()]


def ogeye_cevir(k):
    g, a, y, s, dk = (int(x) for x in re.match(
        r"(\d{1,2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})", k["publishDate"]).groups())
    iliskili = [x.strip() for x in (k.get("relatedStocks") or "").split(",")
                if x.strip()]
    return {
        "kaynak": "KAP",
        "tarih": f"{g} {AYLAR[a - 1]} {s:02d}:{dk:02d}",
        "baslik": k["title"].strip(),
        "url": f"https://www.kap.org.tr/tr/Bildirim/{k['disclosureIndex']}",
        "genis": len(iliskili) > 1,
        "_sirala": (y, a, g, s, dk),
    }


def main():
    curl("https://www.kap.org.tr/tr/bildirim-sorgu", "_kap_on.html")
    curl("https://www.kap.org.tr/tr/bildirim-sorgu-sonuc?srcbar=Y&cmp=Y"
         "&cat=6&slf=ALL", HAM_KURESEL, t=90)
    kuresel = kayitlari_ayristir(HAM_KURESEL)
    if len(kuresel) < 500:
        print(f"[KAP] Kuresel sayfa yetersiz ({len(kuresel)}); islem iptal.")
        sys.exit(1)
    print(f"[KAP] Kuresel sayfa: {len(kuresel)} kayit")

    veri = json.load(open(DOSYA, encoding="utf-8"))
    hisseler = veri["hisseler"]
    yeni_hisseler = {}
    for kod in hisseler:
        oid = oid_bul(kod)
        birlesik = dict(kuresel)
        if oid:
            sirket_url = ("https://www.kap.org.tr/tr/bildirim-sorgu-sonuc"
                          "?srcbar=Y&cmp=Y&cat=3&m=" + oid)
            try:
                curl(sirket_url, "_kap_sirket.html", t=60)
                birlesik.update(kayitlari_ayristir("_kap_sirket.html"))
            except Exception as e:
                # Tek hissenin sayfasi alinamazsa TUM kosu dusmesin: o hissenin
                # onceki kayitlari korunur, digerleri tazelenir.
                print(f"[KAP] {kod}: sirket sayfasi alinamadi ({e}); eski kayitlar korunuyor")
                yeni_hisseler[kod] = hisseler[kod]
                time.sleep(0.3)
                continue
        else:
            print(f"[KAP] {kod}: OID bulunamadi, yalnizca kuresel sayfa kullanilacak")
        uyusan = [k for k in birlesik.values()
                  if kod in _kodlari_ayir(k.get("stockCode"))
                  or kod in _kodlari_ayir(k.get("relatedStocks"))]
        ogeler = sorted((ogeye_cevir(k) for k in uyusan),
                        key=lambda o: o["_sirala"], reverse=True)[:15]
        for o in ogeler:
            o.pop("_sirala", None)
        yeni_hisseler[kod] = ogeler
        time.sleep(0.3)

    veri["hisseler"] = yeni_hisseler
    veri["meta"] = {
        "kaynak": ("KAP bildirim-sorgu-sonuc (www.kap.org.tr resmi; kuresel sayfa"
                   " ~son 6 gun + sirket sayfalari gecerli ay)"),
        "tarih": datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y"),
        "not": "Akis sayfasi basina en fazla 15 oge; tum url'ler resmi kap.org.tr",
    }
    json.dump(veri, open(DOSYA, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    toplam = sum(len(v) for v in yeni_hisseler.values())
    bos = [k for k, v in yeni_hisseler.items() if not v]
    print(f"[KAP] Yeniden kurulum: {toplam} oge (30 kod); bos kod: {bos or 'yok'}")


if __name__ == "__main__":
    main()
