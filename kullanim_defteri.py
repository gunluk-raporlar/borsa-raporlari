"""Kullanim defteri: hangi raporu hangi model yazdi, token, hata sayilari.

Kullanicinin 2026-10-08 istegi: "8 ekim raporunu su model yazdi, su kadar
token tuttu, su kadar hata yaptı" — otomatik Excel.

data/kullanim-defteri.csv  : ana kayit (satir satir, diff edilebilir)
data/kullanim-defteri.xlsx : her kayitta bastan uretilen Excel
                             ('Kayitlar' + 'Ozet' sayfalari)

GIZLILIK NOTU: data/ build.sh beyaz listesinde olmadigindan SITEDE
yayinlanmaz; ancak depo herkese acik oldugundan GitHub arayuzunden
gorulebilir. Tam gizlilik gerekirse sonraki adim: ozel depoya kopyalama
veya Cloudflare R2 (kayit notu, 2026-10-08).

Kaynak veriler: bot._LLM_SON_BASARI (basarili cagri kimligi),
bot._LLM_TOKEN_SAYAC (kosu toplami), bot.DOGRULAMA_IST (dogrulama
duzeltme sayilari).
"""
import csv
import datetime
import os
import zoneinfo

CSV_YOL = os.path.join("data", "kullanim-defteri.csv")
XLSX_YOL = os.path.join("data", "kullanim-defteri.xlsx")
SUTUNLAR = ["tarih", "saat", "rapor_turu", "saglayici", "model", "deneme",
            "prompt_token", "completion_token", "sure_sn", "hata_toplam",
            "hata_detay", "rozet", "yazan_dagilimi"]
_IST_ANAHTARLARI = ("isim", "enflasyon", "toplam_metin", "endeks",
                    "faiz", "makro", "piyasa")


def _ist_ozet(ist):
    """DOGRULAMA_IST sozlugunden (toplam, detay) uretir."""
    if not ist:
        return 0, ""
    toplam = sum(int(ist.get(k) or 0) for k in _IST_ANAHTARLARI)
    parcalar = [f"{k}={ist.get(k)}" for k in _IST_ANAHTARLARI if ist.get(k)]
    uyari = int(ist.get("endeks_uyari") or 0)
    if uyari:
        parcalar.append(f"uyari={uyari}")
    return toplam, ";".join(parcalar)


def kaydet(rapor_turu, rozet=False, csv_yol=CSV_YOL, xlsx_yol=XLSX_YOL,
           bot_modul=None):
    """Basarili rapor uretimini deftere isler; Excel'i yeniden uretir.

    rapor_turu: "gunluk" | "derin" | "makro" | "radyo" ...
    rozet: son-care yayini ise True (dusuk guvenilirlik isareti).
    """
    import sys
    import bot as _b
    b = bot_modul or _b
    # KRITIK (2026-10-09): bot.py script olarak calisirken modul adi
    # "__main__" olur; kaydet'in `import bot`'u IKINCI bir kopya yaratir ve
    # bos sozluk okur (bugunku bos gunluk satirinin kok nedeni). __main__
    # icinde dolu sozluk varsa onu esas al.
    if b is None or not getattr(b, "_LLM_SON_BASARI", None):
        ana = sys.modules.get("__main__")
        if ana is not None and getattr(ana, "_LLM_SON_BASARI", None):
            b = ana
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    simdi = datetime.datetime.now(tz)
    bilgi = dict(getattr(b, "_LLM_SON_BASARI", {}) or {})
    toplam, detay = _ist_ozet(getattr(b, "DOGRULAMA_IST", None))
    satir = {
        "tarih": simdi.strftime("%Y-%m-%d"),
        "saat": simdi.strftime("%H:%M"),
        "rapor_turu": rapor_turu,
        "saglayici": bilgi.get("saglayici", "-"),
        "model": bilgi.get("model", "-"),
        "deneme": bilgi.get("deneme", "-"),
        "prompt_token": bilgi.get("prompt_tokens", "-"),
        "completion_token": bilgi.get("completion_tokens", "-"),
        "sure_sn": bilgi.get("sure_sn", "-"),
        "hata_toplam": toplam,
        "hata_detay": detay,
        "rozet": "evet" if rozet else "hayir",
        "yazan_dagilimi": ", ".join(f"{k2}x{v}" for k2, v in sorted(
            getattr(b, "_LLM_YAZARLAR", {}).items(), key=lambda x: -x[1])),
    }
    os.makedirs(os.path.dirname(csv_yol) or ".", exist_ok=True)
    yeni = not os.path.exists(csv_yol)
    with open(csv_yol, "a", encoding="utf-8-sig", newline="") as fh:
        yazici = csv.DictWriter(fh, fieldnames=SUTUNLAR)
        if yeni:
            yazici.writeheader()
        yazici.writerow(satir)
    _xlsx_yaz(csv_yol, xlsx_yol)
    return satir


def _xlsx_yaz(csv_yol, xlsx_yol):
    """CSV'yi iki sayfali Excel'e dokar (Kayitlar + Ozet)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("[Defter] openpyxl yok; Excel atlandi (pip install openpyxl).")
        return
    with open(csv_yol, encoding="utf-8-sig", newline="") as fh:
        kayitlar = list(csv.DictReader(fh))

    wb = Workbook()
    sayfa = wb.active
    sayfa.title = "Kayitlar"
    sayfa.append(SUTUNLAR)
    for hucre in sayfa[1]:
        hucre.font = Font(bold=True)
    for kayit in kayitlar:
        sayfa.append([kayit[s] for s in SUTUNLAR])
    for i, ad in enumerate(SUTUNLAR, 1):
        sayfa.column_dimensions[get_column_letter(i)].width = \
            max(10, min(30, len(ad) + 8))
    sayfa.freeze_panes = "A2"

    ozet = wb.create_sheet("Ozet")
    ozet_baslik = ["rapor_turu", "adet", "prompt_token",
                   "completion_token", "hata_toplam"]
    ozet.append(ozet_baslik)
    for hucre in ozet[1]:
        hucre.font = Font(bold=True)
    toplamlar = {}
    for kayit in kayitlar:
        tur = toplamlar.setdefault(kayit["rapor_turu"], [0, 0, 0, 0])
        tur[0] += 1
        for j, alan in ((1, "prompt_token"), (2, "completion_token")):
            try:
                tur[j] += int(float(kayit[alan] or 0))
            except ValueError:
                pass
        try:
            tur[3] += int(kayit["hata_toplam"] or 0)
        except ValueError:
            pass
    for tur in sorted(toplamlar):
        ozet.append([tur] + toplamlar[tur])
    for i, ad in enumerate(ozet_baslik, 1):
        ozet.column_dimensions[get_column_letter(i)].width = \
            max(12, len(ad) + 6)

    wb.save(xlsx_yol)
