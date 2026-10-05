# -*- coding: utf-8 -*-
"""Yayin oncesi editor katmani: imla/yazim, muğlak-kesin ifadeler ve TTS telaffuz.

Dogrulama zinciri (dogrulama.py) SAYILARI ve ISIMLERI denetler; bu modul DILI
denetler ve zincirin EN BASINDA calisir: ham LLM ciktisi once editor'den gecer
(dil + imla duzelir), dogrulamaya daha steril metin ulasir.

Katmanlar:
  imla(metin)         -> (metin, duzeltmeler)   yazim sozlugu + noktalama
  muglak(metin)       -> (metin, yumusatmalar, uyarilar)
                        kesin yargi dili yumusatilir; emir dili yalniz uyari
  tts_telaffuz(metin) -> metin                 yalnizca SESLI RAPOR girdisinde
                        (XU030/TL/%/binlik sayilarin dogal okunusu)

Tasarim ilkeleri:
- Sifir bagimlilik (std + dogrulama.py gibi proje-ici modul yok).
- Sayilara dokunulmaz (imla rakam-%/rakam-TL komsulugunu atlar); boylece
  dogrulama.py'nin desenleri editor tarafindan bozulamaz.
- Otomatik degisimler deontik cumlelerde (-meli/-mali/gerekir) durur ve
  logs/editor/*.jsonl dosyasina (eski, yeni, baglam, uygulandi) yazilir;
  kayitlar sozluk buyutme kararlari icin kanit havuzudur.
"""
import json
import os
import re
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 1) IMLA: yazim sozlugu + noktalama
# ---------------------------------------------------------------------------

# Bot.py'deki _TR_DUZELTME'nin genisletilmisi; kelime-sinirli uygulanir.
# Anahtar = yanlis form (buyuk/kucuk harf ayri), deger = dogru form.
YAZIM = {
    # ASCII'ye dusmus Turkce kelimeler (bolum basliklari)
    "Ozeti": "Özeti", "Bakis": "Bakış", "Sektor": "Sektör", "Bazli": "Bazlı",
    "Degerlendirme": "Değerlendirme", "Gorsel": "Görsel", "Osilator": "Osilatör",
    "Okumalari": "Okumaları", "Haritasi": "Haritası", "Gunun": "Günün",
    "Onerilen": "Önerilen", "Giris": "Giriş", "Bolgesi": "Bölgesi",
    "Gerekce": "Gerekçe", "Asiri": "Aşırı", "Alim": "Alım", "Satim": "Satım",
    "Yatirim": "Yatırım", "Portfoy": "Portföy", "Portfoyu": "Portföyü",
    "ozeti": "özeti", "bakisi": "bakışı", "sektoru": "sektörü",
    "gore": "göre", "gorece": "görece", "cunku": "çünkü", "Cunku": "Çünkü",
    "olcude": "ölçüde", "olcul": "ölçül", "olcut": "ölçüt",
    "icin": "için", "icinde": "içinde", "icindeki": "içindeki",
    "icin ki": "için ki",
    # sik tekrarlayan yazim hatalari (LLM cikisi)
    "sinyiller": "sinyaller", "Sinyiller": "Sinyaller", "sinyil": "sinyal",
    "Ayrisan": "Ayrışan", "ayrisan": "ayrışan", "Nötür": "Nötr",
    "GÜCLÜ": "GÜÇLÜ", "güclü": "güçlü", "Guclü": "Güçlü", "guclü": "güçlü",
    "GÜclü": "GÜÇLÜ",
    "yanlız": "yalnız", "Yanlız": "Yalnız", "yanlızca": "yalnızca",
    "herkez": "herkes", "Kirilim": "Kırılım", "kirilim": "kırılım",
    "korilasyon": "korelasyon", "korelsyon": "korelasyon",
    "Analizinde": "Analizinde",  # bilinc: dogru kelime, sozlukteki yer tutucu
    "tetikleme": "tetikleme",     # bilinc: dogru kelime
    # kurum/ad bicimleri
    "Borsa istanbul": "Borsa İstanbul", "borsa istanbul": "Borsa İstanbul",
    "Borsa Istanbul": "Borsa İstanbul", "borsa istanbul'un": "Borsa İstanbul'un",
    "tcmb": "TCMB", "Tcmb": "TCMB", "tüik": "TÜİK", "Tüik": "TÜİK",
    "evds": "EVDS", "spk": "SPK", "tufe": "TÜFE", "Tufe": "TÜFE",
}

# Noktalama temizliginde sayi-ondalik virgulu bozmamak icin: virgul/nokta
# sonrasina yalnizca HARF geliyorsa bosluk eklenir.
# DIKKAT: "%" bu sinifta DEGIL — "faizi %37" ifadesindeki bosluk silinirse
# dogrulama desenleri ve okunurluk bozulur (2026-10-05 test bulgusu).
_NOKTALAMA_SONRASI = re.compile(r"([,;:])(?=[A-Za-zÇĞİÖŞÜçğıöşü])")
_NOKTALAMA_ONCESI = re.compile(r"\s+([,.;:!?])")
_CIFT_NOKTALAMA = re.compile(r"(?<=[,;:])(?<!\.\.)\s*[,;:]+\s*")
_EKSIK_NOKTA = re.compile(r"\.(?=[A-Za-zÇĞİÖŞÜçğıöşü])")
_CUMLE_BASI = re.compile(r"(^|[.!?]\s+|\n)([a-zçğıöşü])")
# Markdown yapisal satirlari: baslik, liste, tablo, numarali madde, ayraç
_MD_SATIR = re.compile(r"^\s*(?:#|\||[-*+]\s|\d+[.)]\s|>|---|\*\*)")

# --- Word tarzi yapisal denetimler (deterministik, dusuk yanlis-pozitif) ---
# Yinelenen kelime: "ve ve", "ise ise" — Word'un klasik "double word" kurali.
_YINELENI_KELIME = re.compile(r"\b(\w+)(\s+\1)+\b", re.IGNORECASE)
# Bitisik para birimi: "15TL" -> "15 TL" (USDTRY tek token; desen sayi+bitisik
# kisaltma aradigindan birlesik kodlara dokunulmaz).
_BITISIK_BIRIM = re.compile(r"(\d)(TL|USD|EUR|GBP|TRY)\b")
# "15 %" -> "%15": yalnizca yuzde sonrasi bosluk/noktalama/cumle sonu geldiginde;
# "5 %3" gibi iki ayri sayi yan yanaysa dokunulmaz (veri bozmasini onler).
_TERS_YUZDE = re.compile(r"(\d)\s+%(?=\s|[.,;:!?)]|$)")
# Kisa yazim: "vb" -> "vb."
_VB_KISA = re.compile(r"\bvb(?=[\s.,;:!?)]|$)")

# --- Kodlama/karakter sagligi (Gemini incelemesi sonrasi ekleme) ---
# Bozuk kodlama kalintilari (mojibake): "güìlendiği", "\fcsteyse" gibi —
# i18n-cache temizliginde gercek vakalar gorduk. Otomatik duzeltme yerine
# UYARI uretilir (hangi harfin oldugu bagimli); literal escape kalintilari
# ise guvenle silinir.
_MOJIBAKE_RE = re.compile(
    r"ì|\\fc|\\u00[0-9a-f]{2}|\\x[0-9a-f]{2}"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", re.I)
# Görünmez karakterler: zero-width, soft hyphen, BOM — sessizce silinir;
# NBSP normal bosluk yapilir (gorunmez fark, metni kirletmesin).
_GORUNMEZ_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_NBSP_RE = re.compile(r"\u00a0")
# Tırnak standardizasyonu: curly cift/tek tirnaklar duz forma iner. Türkçe
# ek apostrofu (Türkiye'de) duz ' oldugundan bu donusum guvenlidir.
_TIRNAK_CIFT = re.compile(r"[\u201c\u201d\u201e\u201f]")
_TIRNAK_TEK = re.compile(r"[\u2018\u2019\u201a\u201b]")


def _buyuk(harf: str) -> str:
    """Turkce dogru buyuk harf: i->I degil İ, ı->I."""
    return "İ" if harf == "i" else ("I" if harf == "ı" else harf.upper())


def imla(metin: str) -> tuple:
    """Yazim sozlugu + noktalama temizligi. Sayilara dokunmaz.
    Donus: (yeni_metin, duzeltmeler) — duzeltme = (eski, yeni)."""
    if not metin:
        return metin, []
    duzeltmeler = []

    # 1) yazim sozlugu (kelime sinirli, buyuk/kucuk ayri)
    for yanlis, dogru in YAZIM.items():
        if yanlis == dogru:
            continue
        yeni, n = re.subn(r"\b" + re.escape(yanlis) + r"\b", dogru, metin)
        if n:
            duzeltmeler.append((yanlis, dogru, n))
            metin = yeni

    # 2) noktalama + Word tarzi yapisal duzeltmeler
    temiz = metin
    temiz = _NOKTALAMA_ONCESI.sub(r"\1", temiz)        # "kelime ," -> "kelime,"
    temiz = _NOKTALAMA_SONRASI.sub(r"\1 ", temiz)      # "kelime,kelime" -> ", "
    temiz = re.sub(r"[ \t]{2,}", " ", temiz)           # cift bosluk
    temiz = re.sub(r"(?<!\.)\.\.(?!\.)", ".", temiz)   # ".." -> "." ("..." korunur)
    temiz = re.sub(r",{2,}", ",", temiz)               # ",," -> ","
    temiz = re.sub(r"\s+([)\]])", r"\1", temiz)        # "( metin )" -> "(metin)"
    temiz = re.sub(r"([([]) ", r"\1", temiz)

    # Word tarzi: yinelenen kelime ("ve ve" -> "ve"; ilk form korunur)
    temiz, n = _YINELENI_KELIME.subn(r"\1", temiz)
    if n:
        duzeltmeler.append(("yinelenen-kelime", "tekilleştirildi", n))
    # Word tarzi: bitisik para birimi ("15TL" -> "15 TL")
    temiz, n = _BITISIK_BIRIM.subn(r"\1 \2", temiz)
    if n:
        duzeltmeler.append(("bitişik-birim", "15TL -> 15 TL", n))
    # Word tarzi: ters yüzde ("15 %" -> "%15"; sinirli kosul, "5 %3" korunur)
    temiz, n = _TERS_YUZDE.subn(r"%\1", temiz)
    if n:
        duzeltmeler.append(("ters-yüzde", "15 % -> %15", n))
    # kisa yazim: "vb" -> "vb."
    temiz, n = _VB_KISA.subn("vb.", temiz)
    if n:
        duzeltmeler.append(("vb", "vb.", n))

    # kodlama sagligi: gorunmez karakterler silinir, NBSP normal bosluk olur,
    # curly tirnaklar duzlesir (gorunum degismeden metin standartlasir)
    temiz = _GORUNMEZ_RE.sub("", temiz)
    temiz = _NBSP_RE.sub(" ", temiz)
    temiz = _TIRNAK_CIFT.sub('"', temiz)
    temiz = _TIRNAK_TEK.sub("'", temiz)

    # mojibake (bozuk kodlama) kalintisi: otomatik harita riskli, UYARI uretilir
    # ("güìlendiği", "\fcsteyse" gibi gercek vakalar i18n cache'inde goruldu)
    for m in _MOJIBAKE_RE.finditer(temiz):
        duzeltmeler.append(("mojibake", f"bozuk karakter: {m.group(0)!r}", 1))

    if temiz != metin:
        duzeltmeler.append(("noktalama", "temizlik", 1))
        metin = temiz

    # 3) cumle basi buyuk harf (markdown yapisal satirlari haric)
    satirlar = metin.split("\n")
    for i, satir in enumerate(satirlar):
        if _MD_SATIR.match(satir) or not satir:
            continue
        yeni_satir = _CUMLE_BASI.sub(
            lambda m: m.group(1) + _buyuk(m.group(2)), satir)
        if yeni_satir != satir:
            satirlar[i] = yeni_satir
    metin = "\n".join(satirlar)
    return metin, duzeltmeler


# ---------------------------------------------------------------------------
# 2) MUGLAK/KESIN IFADELER: yumusatma haritasi + deontik koruma
# ---------------------------------------------------------------------------

# Daraltilmis harita: yalnizca analiz-yargi kalip formlari. "kanitlar" (isim)
# ve "kanitlarken" gibi cekimler harictir; "Garanti" (banka) yalnizca fiil
# obeginde degisir. "mutlaka" bilincl YALNIZ UYARI: deontik cumlelerde
# yumusatma anlamini tersine cevirir.
YUMUSAT = [
    ("kanıtlamaktadır", "işaret etmektedir"),
    ("Kanıtlamaktadır", "İşaret etmektedir"),
    ("kanıtlıyor", "işaret ediyor"),
    ("Kanıtlıyor", "İşaret ediyor"),
    ("kanıtlamakta", "işaret etmekte"),
    ("kesinlikle", "büyük olasılıkla"),
    ("Kesinlikle", "Büyük olasılıkla"),
    ("şüphesiz", "beklentiye göre"),
    ("Şüphesiz", "Beklentiye göre"),
    ("kuşkusuz", "beklentiye göre"),
    ("Kuşkusuz", "Beklentiye göre"),
    ("garanti etmektedir", "güçlü biçimde işaret etmektedir"),
    ("garanti ediyor", "güçlü biçimde işaret ediyor"),
    ("garanti eder", "güçlü biçimde işaret eder"),
]
# Yalnizca uyari ureten ifadeler (metin degismez):
UYARI_KALIPLARI = [
    ("mutlaka", "deontik vurgu — Manuel gozden gecirin"),
    ("garantisi", "sahiplik kalibi — Manuel gozden gecirin"),
]
# Deontik cumle korumasi: bu ipuclari varsa o cumlede otomatik yumusatma durur.
# NOT: "mali" (mali tablo) deontik DEGIL, desen onu yakalamaz; "-malı/-meli"
# ekleri basinda kelime gövdesi zorunlu (\w+) tutulur, -dir/-dır cekimi dahil.
_DEONTIK = re.compile(
    r"\b\w+(?:meli|maları|meleri)(?:dır|dir|dur|dür)?\b"
    r"|\b\w+malı(?:dır|dir|dur|dür)?\b"
    r"|gerekir|lazım|gerekmektedir|önerilir|tavsiye edilir", re.I)
# Emir dili taramasi: yatirimciya yonelik dogrudan emir kaliplari (uyari only).
_EMIR_DILI = re.compile(
    r"\b(?:al[ıi]n|sat[ıi]n|hemen al|pozisyon (?:açı?n|al[ıi]n|kapat[ıi]n)|"
    r"stop[- ]?loss (?:koy|koyun|belirle)|hedef(?:e)? sat)", re.I)

_CUMLE_AYIR = re.compile(r"(?<=[.!?])\s+|\n")


def muglak(metin: str) -> tuple:
    """Kesin yargi dilini yumusatir; emir dilini uyari olarak raporlar.
    Donus: (yeni_metin, yumusatmalar, uyarilar).
    yumusatma = (eski, yeni); uyari = (kalip, aciklama, baglam_cumle)."""
    if not metin:
        return metin, [], []
    yumusatmalar = []
    uyarilar = []

    cumleler = _CUMLE_AYIR.split(metin)
    for cumle in cumleler:
        if not cumle.strip():
            continue
        # Word tarzi: eslesmeyen parantez (acma/kapama sayisi farkli)
        if cumle.count("(") != cumle.count(")"):
            uyarilar.append(("parantez dengesi", "açma/kapama sayısı eşit değil", cumle.strip()[:160]))
        # Word tarzi: cok uzun cumle (okunabilirlik) — 40+ kelime
        if len(cumle.split()) >= 40:
            uyarilar.append(("uzun cümle", f"{len(cumle.split())} kelime — bölmeyi düşünün", cumle.strip()[:160]))
        deontik = bool(_DEONTIK.search(cumle))
        for eski, yeni in YUMUSAT:
            if eski not in cumle:
                continue
            if deontik:
                # otomatik degisim durduruldu; kanit olarak loglanir
                uyarilar.append((eski, "deontik cumle — degisim durduruldu", cumle.strip()[:160]))
                continue
            yeni_cumle = cumle.replace(eski, yeni)
            metin = metin.replace(cumle, yeni_cumle, 1)
            cumle = yeni_cumle
            yumusatmalar.append((eski, yeni))
        for kalip, aciklama in UYARI_KALIPLARI:
            if kalip in cumle:
                uyarilar.append((kalip, aciklama, cumle.strip()[:160]))
        for m in _EMIR_DILI.finditer(cumle):
            uyarilar.append((m.group(0), "emir dili", cumle.strip()[:160]))
    return metin, yumusatmalar, uyarilar


# ---------------------------------------------------------------------------
# 3) Yapisal egitim logu (JSONL)
# ---------------------------------------------------------------------------

LOG_KOK = os.environ.get("EDITOR_LOG_KOK", "logs/editor")


def log_kaydi(rapor: str, tur: str, kural: str, eski: str, yeni: str,
              baglam: str, uygulandi: bool, kok: str = ""):
    """Degisimleri logs/editor/YYYY-MM-DD.jsonl olarak append eder.
    Hata uretmez (log yazilamasi yayini asla etkilemez)."""
    try:
        kok = kok or LOG_KOK
        os.makedirs(kok, exist_ok=True)
        kayit = {
            "tarih": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "rapor": rapor, "tur": tur, "kural": kural,
            "eski": eski, "yeni": yeni,
            "baglam": baglam[:200], "uygulandi": bool(uygulandi),
        }
        yol = os.path.join(kok, datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
        with open(yol, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    except Exception:
        pass


def uygula(metin: str, rapor: str = "genel", kok: str = "") -> tuple:
    """Imla + muglak'i uygular ve JSONL loga yazar.
    Donus: (metin, ozet_sozlugu). Zincirin BASINDA cagrilir."""
    if not metin:
        return metin, {"imla": 0, "yumusatma": 0, "uyari": 0}
    metin, imla_d = imla(metin)
    metin, yum, uyari = muglak(metin)
    for eski, yeni, _n in imla_d:
        log_kaydi(rapor, "imla", "yazim", eski, yeni, eski, True, kok)
    for eski, yeni in yum:
        log_kaydi(rapor, "muglak", "yumusatma", eski, yeni, eski, True, kok)
    for kalip, aciklama, baglam in uyari:
        log_kaydi(rapor, "uyari", aciklama, kalip, "", baglam, False, kok)
    ozet = {"imla": sum(n for _, _, n in imla_d), "yumusatma": len(yum), "uyari": len(uyari)}
    return metin, ozet


# ---------------------------------------------------------------------------
# 4) TTS TELAFFUZ (yalnizca sesli rapor girdisi; yayin HTML'ine uygulanmaz)
# ---------------------------------------------------------------------------

_BIRLER = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_ONLAR = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_BASAMAK = ["", " bin", " milyon", " milyar"]


def _uc_hane_oku(n):
    yuz, kalan = divmod(n, 100)
    parcalar = []
    if yuz:
        parcalar.append("yüz" if yuz == 1 else _BIRLER[yuz] + " yüz")
    if kalan >= 10:
        parcalar.append(_ONLAR[kalan // 10])
        kalan %= 10
    if kalan:
        parcalar.append(_BIRLER[kalan])
    return " ".join(parcalar)


def _tam_sayi_oku(n):
    if n == 0:
        return "sıfır"
    gruplar = []
    while n:
        gruplar.append(n % 1000)
        n //= 1000
    parcalar = []
    for i in range(len(gruplar) - 1, -1, -1):
        g = gruplar[i]
        if not g:
            continue
        oku = _uc_hane_oku(g)
        if i == 1 and g == 1:
            oku = "bin"
        parcalar.append(oku + (_BASAMAK[i] if i else ""))
    return " ".join(parcalar)


_TARIH_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")   # gg.aa.yyyy korunur
_TL_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*TL\b")
_BINLIK_RE = re.compile(r"\b(\d{1,3}(?:\.\d{3})+)(,(\d{1,2}))?\b")
_YUZDE_RE = re.compile(r"(eks[iı]\s*)?%(-?\d+(?:,\d+)?)")
_KOD_ACILIM = [
    (r"\bXU030\b", "Borsa İstanbul Otuz Endeksi"),
    (r"\bXU100\b", "Borsa İstanbul Yüz Endeksi"),
    (r"Borsa İstanbul 30\b", "Borsa İstanbul Otuz Endeksi"),
    (r"\bUSD/TRY\b|\bUSDTRY\b", "dolar"),
    (r"\bUSD\b", "dolar"), (r"\bEUR\b", "euro"), (r"\bGBP\b", "sterlin"),
]


def tts_telaffuz(metin: str) -> str:
    """Sesli okuma icin sayi/kod acilimlari. Kural sirasi onemli:
    1) tarihler placeholder ile korunur, 2) TL, 3) yuzde, 4) binlik sayi,
    5) kod acilimlari. Yayin HTML'ine DEGIL, yalnizca TTS girdisine uygulanir."""
    if not metin:
        return metin
    # tarihleri koru ("02.10.2026" -> sayi okunmasin)
    korunan = []
    def _koru(m):
        korunan.append(m.group(0))
        return f"\x00{len(korunan) - 1}\x00"
    metin = _TARIH_RE.sub(_koru, metin)

    # 1) TL: "94.893,52 TL" -> "doksan dort bin ... lira elli iki kurus"
    def _tl(m):
        tam = int(m.group(1).replace(".", ""))
        ana = _tam_sayi_oku(tam)
        if m.group(2):
            k = int(m.group(2))
            return f"{ana} lira {_tam_sayi_oku(k)} kuruş"
        return f"{ana} lira"
    metin = _TL_RE.sub(_tl, metin)

    # 2) yuzde: "%0,71" -> "yuzde 0,71"; "-%3" -> "eksi yuzde 3"
    def _yuzde(m):
        onek = "eksi " if m.group(1) else ""
        return f"{onek}yüzde {m.group(2).replace('-', '')}"
    metin = _YUZDE_RE.sub(_yuzde, metin)

    # 3) binlik sayi (gruplu kisa okunus): "15.327" -> "15 bin 327";
    #    "15.327,05" -> "15 bin 327 virgül sıfır beş"
    def _binlik(m):
        gruplar = m.group(1).split(".")
        ekler = ["", " bin", " milyon", " milyar"]
        n = len(gruplar)
        parcalar = []
        for i, g in enumerate(gruplar):
            sayi = int(g)
            ek = ekler[n - 1 - i]
            if sayi == 1 and ek == " bin":
                parcalar.append("bin")
            else:
                parcalar.append(str(sayi) + ek)
        oku = " ".join(parcalar)
        if m.group(2):
            rakamlar = " ".join(_BIRLER[int(r)] or "sıfır" for r in m.group(3))
            return f"{oku} virgül {rakamlar}"
        return oku
    metin = _BINLIK_RE.sub(_binlik, metin)

    # 4) kod acilimlari
    for desen, acilim in _KOD_ACILIM:
        metin = re.sub(desen, acilim, metin)
    # kalan "TL" kelimeleri
    metin = re.sub(r"\bTL\b", "lira", metin)

    # korunan tarihleri geri koy
    def _geri_koy(m):
        return korunan[int(m.group(1))]
    return re.sub(r"\x00(\d+)\x00", _geri_koy, metin)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # --- imla ---
    m = "borsa istanbul verilerine gore sinyiller guclü; olcul hareket , kesinlikle devam ediyor.."
    yeni, dz = imla(m)
    assert "Borsa İstanbul" in yeni and "sinyaller" in yeni and "güçlü" in yeni, yeni
    assert "ölçül" in yeni, yeni
    assert "%3,2" == imla("%3,2")[0], "sayi bozuldu"
    # rakam-virgul-harf ayrimi
    y, _ = imla("Getiri %5,2 oldu,sonuç olumlu")
    assert "%5,2" in y and "oldu, sonuç" in y, y
    # markdown satiri buyutulmez
    y, _ = imla("## sektor bazli degerlendirme\n|- madde basi |x|")
    assert y.split("\n")[0].startswith("## sektor") or "## " in y.split("\n")[0], y

    # --- muglak ---
    m = "Endeks 15.327 seviyesini kanıtlamaktadır; kesinlikle yükseliş sinyalidir. Stop mesafesi mutlaka korunmalıdır."
    yeni, yum, uy = muglak(m)
    assert "işaret etmektedir" in yeni and "kanıtlamaktadır" not in yeni, yeni
    assert "büyük olasılıkla" in yeni, yeni
    assert "mutlaka" in yeni, "deontik cumlede mutlaka degismemeli"
    assert any(u[0] == "mutlaka" for u in uy), uy
    # deontik koruma
    m2 = "Trend kanıtlamaktadır; bu durum izlenmelidir."
    y2, ym2, u2 = muglak(m2)
    assert "kanıtlamaktadır" in y2, "deontik cumlede degisim yapildi!"
    assert any(u[1].startswith("deontik") for u in u2), u2
    # Garanti bankasi korunur
    m3 = "Garanti BBVA hissesi güçlü sinyal verdi."
    y3, _, _ = muglak(m3)
    assert "Garanti BBVA" in y3, y3

    # --- tts ---
    t = tts_telaffuz("XU030 15.327,05 seviyesinde; %0,71 artış. USD/TRY 49,14 TL. Düzeltme 02.10.2026 tarihli.")
    assert "Borsa İstanbul Otuz Endeksi" in t, t
    assert "yüzde 0,71" in t, t
    assert "lira" in t and "TL" not in t, t
    assert "02.10.2026" in t, "tarih bozuldu!"
    assert "15 bin 327 virgül sıfır beş" in t, t
    t2 = tts_telaffuz("Mevduat faizi %43,8 iken 94.893,52 TL portföy.")
    assert "yüzde 43,8" in t2 and "doksan dört bin" in t2, t2

    # --- uygula (zincir girisi) ---
    m4 = ("borsa istanbul verileri kesinlikle olumlu; endeks kanıtlamaktadır.")
    y4, ozet = uygula(m4, "test")
    assert ozet["imla"] >= 1 and ozet["yumusatma"] >= 1, ozet
    assert "Borsa İstanbul" in y4 and "büyük olasılıkla" in y4, y4

    # --- Word tarzi yapisal denetimler ---
    # yinelenen kelime
    y, _ = imla("Piyasa ve ve teknik görünüm olumlu; ise ise sinyal net.")
    assert "ve teknik" in y and "ise sinyal" in y, y
    # bitisik birim
    y, _ = imla("Hisse 15TL seviyesinde; kur 5USD bazında; portföy 2.3EUR karışık.")
    assert "15 TL" in y and "5 USD" in y and "2.3 EUR" in y, y
    # USDTRY birlesik koduna dokunulmaz
    y, _ = imla("USDTRY kuru 49,14 seviyesinde.")
    assert "USDTRY" in y, y
    # ters yüzde: cumle sonu/bosluk -> duzelt; iki ayri sayi -> dokunma
    y, _ = imla("Getiri 5 % oldu.")
    assert "%5" in y, y
    y, _ = imla("5 %3 oranları karşılaştırıldı.")
    assert "5 %3" in y, f"iki ayri sayi bozuldu: {y}"
    # vb kisa yazim
    y, _ = imla("Altın, döviz vb varlıklar izlenir.")
    assert "vb." in y, y
    # parantez dengesi uyarisi
    _, _, u = muglak("Endeks (destek seviyesinde kaldı. Yeni veri geldi.")
    assert any(k[0] == "parantez dengesi" for k in u), u
    # uzun cumle uyarisi (40+ kelime)
    uzun = " ".join(["kelime"] * 45) + "."
    _, _, u2 = muglak(uzun)
    assert any(k[0] == "uzun cümle" for k in u2), "uzun cumle uyarisi uretilmedi"

    # --- kodlama sagligi (mojibake / gorunmez / tirnak) ---
    # NBSP normallesir, zero-width silinir
    y, _ = imla("Endeks\u00a015.327\u200b seviyesinde.")
    assert "\u00a0" not in y and "\u200b" not in y and "15.327" in y, repr(y)
    # curly tirnaklar duzlesir; Turkce apostrof bozulmaz
    y, _ = imla("\u201cGüçlü sinyal\u201d dedi; Türkiye\u2019de işlem gördü.")
    assert '"Güçlü sinyal"' in y and "Türkiye'de" in y, repr(y)
    # mojibake kalintisi uyarı uretir (otomatik harita yok)
    y, dz = imla("Yükselen ADX trendin güìlendiğini gösterir.")
    assert any(k[0] == "mojibake" for k in dz), dz
    # literal escape kalintisi da yakalanir
    y, dz = imla("ADX yön söylemez. +DI üstteyse alıcı baskısı hakimdir \\fc")
    assert any(k[0] == "mojibake" for k in dz), dz

    print("editor.py: tum kendini testler gecti.")
