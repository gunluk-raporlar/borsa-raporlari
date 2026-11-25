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
    # 25.10 denetim bulgulari: LLM cikisinda tekrar gorulen hatalar
    "bekentisi": "beklentisi", "Bekentisi": "Beklentisi",
    "bekentisinin": "beklentisinin", "Bekentisinin": "Beklentisinin",
    "gelişmektekte": "gelişmekte olan", "Gelişmektekte": "Gelişmekte olan",
    "gelişmektek": "gelişmekte olan", "Gelişmektek": "Gelişmekte olan",
    "içsal": "içsel",
    "depremasyon": "değer kaybı", "Depremasyon": "Değer kaybı",
    "fiskalsal": "mali", "liraizasyon": "liralaşma",
    "emta": "emtia", "Emta": "Emtia",
    "sağılayıcı": "sağlayıcı", "bilanso": "bilanço",
    "ylık": "yıllık", "stoç": "stok", "Stoç": "Stok",
    "hissedede": "hissede", "ödüsüz": "risksiz",
    "dib yapma": "dip yapma",
    # 21.11 denetim bulgulari: canli sayfalarda gorulen cumle bozulmalari
    "med ve trend": "trend",
    "cevabını adım öğreneceğiz": "cevabını adım adım öğreneceğiz",
    "Cevabını adım öğreneceğiz": "Cevabını adım adım öğreneceğiz",
    "pay oranlarıyla oranında": "pay oranları oranında",
    "Pay oranlarıyla oranında": "Pay oranları oranında",
    "endeksi yeniyken iyi": "endeksi yenerken iyi",
    "altında gramın": "altının gram fiyatı",
    "Altında gramın": "Altının gram fiyatı",
    "CEO: AHMET OLMUŞTUR": "Ahmet Olmuştur",
    # 25.11 denetim bulgulari: 24 Kasim derin analizinde yayina giren hatalar.
    # Her satir canli sayfada gorulmus somut bir hatadir; sozluk buyudukce
    # ayni hata sinifi tekrar yayina giremez.
    "elimmizde": "elimizde", "Elimmizde": "Elimizde",
    "sinyol": "sinyal", "Sinyol": "Sinyal",
    "teknikta": "teknikte", "Teknikta": "Teknikte",
    "bantına": "bandına", "Bantına": "Bandına",
    "dışardaki": "dışarıdaki", "Dışardaki": "Dışarıdaki",
    "ağırlıkta hisseler": "ağırlıklı hisseler",
    "Ağırlıkta hisseler": "Ağırlıklı hisseler",
    "aşağıladığını": "aşağı çektiğini", "Aşağıladığını": "Aşağı çektiğini",
    "aşağıladı": "aşağı çekti", "Aşağıladı": "Aşağı çekti",
    "korunabilirlik için tutulan": "ihtiyat amaçlı tutulan",
    "Korunabilirlik için tutulan": "İhtiyat amaçlı tutulan",
    "korunabilirlik": "ihtiyat", "Korunabilirlik": "İhtiyat",
    "kisa": "kısa", "Kisa": "Kısa",
    "yüzde puan": "puan", "Yüzde puan": "Puan",
    # Ingilizce karsiligi hazir terimlerin ham birakilmasi (2026-11-24)
    "magnet": "hedef", "Magnet": "Hedef",
    "futures": "vadeli işlem", "Futures": "Vadeli işlem",
    # "ralli" Turkce sozlukte yerlesik; ek alirken apostrof KULLANILMAZ
    # ("rallinin", "rallisi"). Bu satirlar asagidaki "rally" girdisinden
    # ONCE gelmeli, yoksa "rally'nin" -> "ralli'nin" gibi yanlis bicim cikar.
    # Ingilizce tamlamalar once gelir; yoksa yalniz "rally" cevrilip
    # "(relief ralli)" gibi yari Turkce bicim kalir.
    "bear market rally": "ayı piyasası rallisi",
    "bearish rally": "ayı rallisi",
    "rate cut rally": "faiz indirimi rallisi",
    "relief rally": "rahatlama rallisi",
    "narrow rally": "dar ralli",
    "seçici rally": "seçici ralli",
    "rally'nin": "rallinin", "Rally'nin": "Rallinin",
    "rally'ye": "ralliye", "Rally'ye": "Ralliye",
    "rally'yi": "ralliyi", "Rally'yi": "Ralliyi",
    "rally'de": "rallide", "Rally'de": "Rallide",
    "rally'si": "rallisi", "Rally'si": "Rallisi",
    "rally": "ralli", "Rally": "Ralli",
    # Cift ceviri onarimi: '"falling knife" (dusen bicak)' -> '"dusen bicak"'
    '"falling knife" (düşen bıçak)': '"düşen bıçak"',
    '"Falling Knife" (Düşen Bıçak)': '"Düşen Bıçak"',
    "Falling Knife": "Düşen Bıçak", "falling knife": "düşen bıçak",
    "mean reversion": "ortalamaya dönüş", "Mean reversion": "Ortalamaya dönüş",
    "bir birikim (consolidation)": "bir konsolidasyon (yatay birikim)",
    "consolidation": "konsolidasyon", "Consolidation": "Konsolidasyon",
    "Bölünme Stratejisi": "Dağıtım Stratejisi",
    "bölünme stratejisi": "dağıtım stratejisi",
    # 25.11 toplu tarama: kurala sigmayan, tek tek gorulen uzun kuyruk.
    "kar realizasyonu": "kâr realizasyonu",
    "Kar realizasyonu": "Kâr realizasyonu",
    "ons başı": "ons başına", "Ons başı": "Ons başına",
    "disenflasyon": "dezenflasyon", "Disenflasyon": "Dezenflasyon",
    "regression": "regresyon", "Regression": "Regresyon",
    "doviz": "döviz", "Doviz": "Döviz",
    "kananın": "kanalın", "Kananın": "Kanalın",
    "yatan trend": "yatay trend", "Yatan trend": "Yatay trend",
    "trendingin": "trendin", "Trendingin": "Trendin",
    "seyir defterini sürdürüyor": "seyretmeyi sürdürüyor",
    "Seyir defterini sürdürüyor": "Seyretmeyi sürdürüyor",
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
# Kisaltmadan sonra cumle basi SAYILMAZ: "vb. gibi", "orn. bu", "vs. ile".
# Bu kume olmadan _CUMLE_BASI "vb. Gibi" uretiyordu (2026-11-25 architect
# bulgusu; canli sayfalarda "vb. Portfoy", "vs. Temel" olarak goruldu).
_KISALTMA = {
    "vb", "örn", "bkz", "yy", "vs", "dr", "prof", "no",
    "m.ö", "m.s", "sf", "age", "çev",
}
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
# 25.11 DUZELTME: eski desen tek haneyi yakaliyordu ("15 %" -> "1%5",
# "0,49 %" -> "0,4%9"). Canli sayfalardaki bozuk yuzdelerin kok nedeni buydu;
# sayinin TAMAMI ve ondalik kismi desene dahil edildi.
_TERS_YUZDE = re.compile(r"(?<![\d.,])([+-]?)(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s+%(?=\s|[.,;:!?)]|$)")


def _ters_yuzde(m) -> str:
    """'15 %' -> '%15'. Arti isareti dusurulur (varsayilan pozitif), eksi korunur."""
    isaret = "" if m.group(1) == "+" else m.group(1)
    return isaret + "%" + m.group(2)


def _yuzde_basa(m) -> str:
    """'-0,13%' -> '-%0,13'. Isaret yuzdenin SOLUNDA (tek bicim).

    2026-11-25: _ters_yuzde() ile ayni bicimi uretir; korpusta baskin yazim
    bu (HEAD: -%X 2770, %-X 646). "yüzde 5%" kalibi korunur.
    """
    onceki = m.string[max(0, m.start() - 7):m.start()]
    if onceki.rstrip().endswith("yüzde"):
        return m.group(0)          # "yüzde 5%" zaten dogru bicim
    sayi = m.group(1)
    if sayi.startswith("+"):
        sayi = sayi[1:]
    if sayi.startswith("-"):
        return "-%" + sayi[1:]
    return "%" + sayi
# Kisa yazim: "vb" -> "vb." (zaten noktaliysa dokunma; "vb.." uretmesin)
_VB_KISA = re.compile(r"\bvb(?!\.)(?=[\s.,;:!?)]|$)")
# 25.10 denetim: LLM bazi yuzdeleri kaydirik yaziyor ('-0,38%' -> '-0,3%8',
# '43,04%' -> '43,0%4', '%24,6' -> '%24,%6'). Rakamlar DEGISTIRILMEZ; yalnizca
# yanlis yere dusen '%' sayinin sonuna tasinir (dogrulama desenlerini bozmaz).
_YUZDE_KAYMA1 = re.compile(r"(\d),(\d)%(\d)(?!\d)")
_YUZDE_KAYMA2 = re.compile(r"(\d),%(?=\d)")
# 25.11 denetim: yuzde isareti sayidan SONRA yazilmis ("-0,13%", "+4,3%").
# TR raporunda '%' sayinin basindadir; isaret korunur, '+' dusurulur.
# SINIRLAR (gerileme testi 2026-11-25): (a) '%' sayiya BITISIK olmali —
# "U-6 %7,9" gibi tablo ayracli diziler eslesmesin; (b) '%'den sonra rakam
# gelmemeli; (c) NOKTAli ondalik ("3.6%") hic ele alinmaz — veri tablolarinda
# bicim sozlesmesi farkli, cevirmek veriyi bozar; (d) tablo satirlarina
# uygulanmaz (bkz. _satir_disi).
_YUZDE_SONRA = re.compile(r"(?<![\w.,%])([+-]?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?))%(?!\d)")
# 2026-11-25: isaret yeri tek bicime indirilir — "-%15". Korpusta baskin
# yazim bu (HEAD: -%X 2770, %-X 646); _ters_yuzde() ve _yuzde_basa() de
# ayni bicimi uretir. "%50-%65" araligina dokunmaz (orada '%' tireye bitisik
# degil).
_YUZDE_EKSI = re.compile(r"%-(\d)")
# 2026-11-25: "+%4,09" bicimi hic ele alinmiyordu (architect bulgusu).
# Arti isareti varsayilan pozitiftir, dusurulur.
_YUZDE_ARTI = re.compile(r"\+%(\d)")
# 25.11 denetim: ARALIK ifadesinde yuzde sonda ("50-65%"). Tek sayi kurali
# (_YUZDE_SONRA) bunu "50-%65" yapiyordu — yanlis. Dogrusu "%50-%65":
# aralikta HER IKI sayi da yuzde isareti alir (kullanici duzeltmesi 25.11).
# (?!\d) korumasi URL kodlamasi icin: "2026-09-16%20" bozulmaz.
_YUZDE_ARALIK = re.compile(
    r"(?<![\w.,%])(%?)(\d+(?:,\d+)?)[ \t]*([-\u2011\u2013])[ \t]*"
    r"(\d+(?:,\d+)?)(?!\.\d{3})([ \t]*)(%?)(?![\d%])")
# Ayni kuralin ikinci yonu ("%50-65" -> "%50-%65") ve bosluklu yazim
# ("50-65 %", "40 - 60 %") ayni desende toplandi; yuzde hic yoksa dokunulmaz.


def _yuzde_aralik(m) -> str:
    """Aralikta her iki sayiya '%' koyar; AYIRICI KORUNUR.

    Sayfalarda aralik tiresi cogu yerde bolunmez tire (U+2011). Duz '-' ile
    degistirmek sessiz bir tipografi gerilemesi olurdu (2026-11-25).
    Yuzde isareti hic yoksa (tarih araligi, "16-20 Ekim") dokunulmaz.
    """
    if not (m.group(1) or m.group(6)):
        return m.group(0)
    kuyruk = "" if m.group(6) else m.group(5)
    return "%" + m.group(2) + m.group(3) + "%" + m.group(4) + kuyruk
# 25.11 denetim: "stop-zarar (mental)" ceviri kalibi -> "zihinsel stop".
_STOP_ZARAR = re.compile(r"stop[- ]zarar\s*\((?:mental|zihinsel)\)", re.I)
# 25.11 denetim: "işaret etmek" yanlis kullanimi. "X olduğunu işaret ediyor"
# Ingilizce "indicates" kalibidir; TR karsiligi "gösteriyor".
_ISARET_EDIYOR = re.compile(
    r"(\w+(?:dığını|diğini|duğunu|düğünü|tığını|tiğini|tuğunu|tüğünü))"
    r"\s+işaret ediyor\b")
_ISARET_ETTIGINI = re.compile(r"\bişaret ettiğini gösteriyor\b")
# 25.11 denetim: XU030 "otuz" diye okunur; eki -u'dur ("XU030'u").
# Yalnizca YANLIS bicim eslesir; dogru bicim sayilmaz (log gurultusu olmasin).
_ENDEKS_EKI = re.compile(r"\bXU030'ü\b")
# 25.11 denetim: "ağırlıkta hisseler" / "ağırlık hisseler" -> "ağırlıklı
# hisseler" (cekimli haller dahil). Kelime sonu \b ile sinirlanamadigi icin
# ayri desen.
_AGIRLIKTA = re.compile(r"\bağırlık(?:ta)?\s+(hisse)")
# 25.11 denetim: "% -0,06" -> "%-0,06" (isaretle yuzde arasinda bosluk).
# Yalnizca BOSLUK (satir sonu degil) ve yalnizca isaretli sayi: "%\n2026"
# gibi tablo gecisleri bozulmasin (2026-11-25 gerileme testi).
_YUZDE_BOSLUK = re.compile(r"%[ \t]+([-+]\d)")
# 25.11 denetim: ASCII ok ("%62 -> %70") yerine gercek ok isareti.
_OK_ISARETI = re.compile(r"[ \t]*->[ \t]*")
# 25.11 denetim: Turkce terim zaten yaziliyken parantezli Ingilizce karsiligi
# cevrilirse tekrar olusur ("ortalama dönüş (ortalamaya dönüş)"). Bu durumda
# parantez SILINIR; ceviri yalnizca Turkce karsiligi olmayan yerlerde yapilir.
_MEAN_REVERSION_TEK = re.compile(
    r"\b(ortalama dönüş|ortalamaya dönüş|ortalama dönüşün|ortalamaya dönüşün|"
    r"ortalama dönüşe|ortalamaya dönüşe)"
    r"\s*\((?:mean[ -]reversion)\)", re.I)
# 25.10 denetim: LLM ara sira baska alfabeden sozcuk birakiyor (Korece
# '심리적 지원', '경쟁'). Turkce raporda bu alfabeler meşru degildir; koşu
# sessizce SOKULUR ve kayda gecer (ceviri hattinda KULLANILMAZ — o hatta
# Cince/Rusca meşrudur).
_YABANCI_YAZI_RE = re.compile(
    r"[\uac00-\ud7af\u3130-\u318f"   # Hangul
    r"\u4e00-\u9fff\u3040-\u30ff"    # CJK + Kana
    r"\u0900-\u097f\u0600-\u06ff\u0590-\u05ff"  # Devanagari, Arapça, İbranice
    r"]+")

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


def _cumle_basi(m) -> str:
    """Cumle basi buyuk harf; ama kisaltmadan ve gosterimden sonra dokunmaz.

    "vb. gibi" -> "vb. Gibi" hatasini onler. Onceki sozcuk noktali bir
    kisaltmaysa (vb., orn., vs., bkz. ...) cumle bitmemistir.
    2026-11-25: tek harfli gosterim + "=" de korunur — "r=0.88" Pearson
    katsayisidir, gelenekte kucuk yazilir; "R=0.88" gosterim hatasiydi.
    """
    onceki = m.string[:m.start(2)]
    son = re.search(r"(\S+)\s*$", onceki)
    if son and son.group(1).lower().rstrip(".") in _KISALTMA:
        return m.group(0)
    if m.string[m.start(2) + 1:m.start(2) + 2] == "=":
        return m.group(0)
    return m.group(1) + _buyuk(m.group(2))


def _satir_disi(desen, yeni, metin: str) -> tuple:
    """Deseni yalnizca tablo satirlari DISINDA uygular.

    Markdown tablo satiri '|' ile baslar; hucrelerde sayi/% bicimi veri
    sozlesmesidir, imla kurali oraya uygulanmaz (2026-11-25 gerileme testi:
    "U-6 %7,9" -> "U-%67,9" bozulmasi bu yuzden olmustu).
    Donus: (yeni_metin, degisiklik_sayisi).
    """
    satirlar = metin.split("\n")
    toplam = 0
    for i, satir in enumerate(satirlar):
        if satir.lstrip().startswith("|"):
            continue
        yeni_satir, n = desen.subn(yeni, satir)
        if n:
            satirlar[i] = yeni_satir
            toplam += n
    return "\n".join(satirlar), toplam


def yuzde_bicimle(metin: str, tablo_disi: bool = True) -> tuple:
    """Yuzde bicimini TR konvansiyonuna getirir — TEK giris noktasi.

    Kural: '%' sayinin basinda ("%50", "-%15"); ARALIKTA her iki sayi da
    isaret alir ("%50-%65"). '+' dusurulur, ayirici tire (U+2011) korunur,
    binlik ayracli sayi aralik sanilmaz ("%5 - 10.000 TL" dokunulmaz).

    2026-11-25 (architect bulgusu): ayni kural editor.imla(), rapor_denetle
    ve pipeline/md_ayna icinde ayri ayri yazilmisti ve 13 ortak girdinin
    6'sinda farkli sonuc veriyordu. Artik tek uygulama; digerleri bunu
    cagirmali.

    Donus: (yeni_metin, degisiklik_sayisi).
    """
    toplam = 0
    # Kurallar birbirinin ciktisini besliyor ("% -0,06" -> BOSLUK -> "%-0,06"
    # -> EKSI -> "-%0,06"). Tek gecis yetmedigi icin sabitlenene kadar
    # tekrarlanir; boylece fonksiyon kendi kendinin sabit noktasidir.
    for _ in range(3):
        yeni, n = _yuzde_bicimle_gecis(metin, tablo_disi)
        toplam += n
        if yeni == metin:
            break
        metin = yeni
    return metin, toplam


def _yuzde_bicimle_gecis(metin: str, tablo_disi: bool) -> tuple:
    """yuzde_bicimle()'nin tek gecisi — disaridan cagrilmaz."""
    toplam = 0
    metin, n = _YUZDE_KAYMA1.subn(r"\1,\2\3%", metin)
    toplam += n
    metin, n = _YUZDE_KAYMA2.subn(r"\1,", metin)
    toplam += n
    metin, n = _satir_disi(_YUZDE_ARALIK, _yuzde_aralik, metin)
    toplam += n
    metin, n = _TERS_YUZDE.subn(_ters_yuzde, metin)
    toplam += n
    if not tablo_disi:
        # Tablo hucresi: yalniz kelime ve aralik kurallari. Hucredeki
        # "+5,13%" bir VERI bicimidir; isaret yerini degistirmek veri
        # sozlesmesini bozar (2026-11-25).
        return metin, toplam
    metin, n = _satir_disi(_YUZDE_SONRA, _yuzde_basa, metin)
    toplam += n
    metin, n = _satir_disi(_YUZDE_EKSI, r"-%\1", metin)
    toplam += n
    metin, n = _YUZDE_ARTI.subn(r"%\1", metin)
    toplam += n
    metin, n = _YUZDE_BOSLUK.subn(r"%\1", metin)
    toplam += n
    return metin, toplam


# ---------------------------------------------------------------------------
# 2b) LaTeX KALINTISI: ham matematik isaretlerini okunur duz metne cevir
# ---------------------------------------------------------------------------
# 2026-11-26: haftasonu-egitimi/2026-10-25 ve 2026-11-07 sayfalarinda LLM
# cikisi ham LaTeX olarak yayina girdi ("\frac{\sum_{i=1}^{N} P_i}{\text{Baz
# Değeri}} \times 100"). Markdown ters boluleri kacisladi; okuyucu sayfada
# "\text{...}" ve "\frac{...}" harflerini gordu. Site matematik motoru
# yuklemiyor (KaTeX/MathJax yok) ve sitedeki yerlesik gelenek duz metin
# ("Formül: Gelecek Değer = Anapara × (1 + Dönemsel Getiri)^Dönem Sayısı"),
# bu yuzden kalinti okunur duz metne cevrilir.
_LATEX_SEMBOL = {
    "times": "×", "approx": "≈", "cdot": "·", "pm": "±", "div": "÷",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "infty": "∞", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "mu": "μ",
    "sigma": "σ", "pi": "π", "left": "", "right": "",
}
# Argumani duz metin olarak gecirilen komutlar ("\text{Kapanış}" -> "Kapanış")
_LATEX_METIN_KOMUT = {"text", "mathrm", "mathbf", "mathit", "operatorname",
                      "textrm", "textbf", "textit"}
_LATEX_KOMUT = re.compile(r"\\[a-zA-Z]+")
# Gosterim matematigi sinirlayicilari: \[ ... \], \( ... \), $$ ... $$
_LATEX_GOSTERIM = re.compile(r"\\\[(.*?)\\\]|\\\((.*?)\\\)|\$\$(.*?)\$\$", re.S)
# HTML'e donusmus gosterim blogu: "\[ ... \]" -> "[<br /> ... <br />]"
_LATEX_HTML_BLOK = re.compile(
    r"\[\s*(?:<br\s*/?>\s*)?(.*?)(?:\s*<br\s*/?>\s*)?\]", re.S)
# Blok icinde <br> disinda etiket varsa dokunulmaz (duz metin blogu degil)
_LATEX_BLOK_YABANCI = re.compile(r"<(?!/?br\b)")


def _latex_grup(s: str, i: int) -> tuple:
    """i, '{' konumunda. Dengeli grubu dondurur: (icerik, sonraki_indeks)."""
    derinlik = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            derinlik += 1
        elif s[j] == "}":
            derinlik -= 1
            if derinlik == 0:
                return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


def _latex_metin(s: str) -> str:
    """LaTeX parcasini okunur duz metne cevirir (matematik motoru yok)."""
    cikti = []
    i = 0
    while i < len(s):
        if s[i] != "\\":
            if s[i] not in "{}":
                cikti.append(s[i])
            i += 1
            continue
        m = _LATEX_KOMUT.match(s, i)
        if not m:
            # \{ \} \% \& \_ \$ \# ve bosluk komutlari
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in "{}%&_$#":
                cikti.append(nxt)
                i += 2
            elif nxt in " ,;:!" or nxt == "\\":
                cikti.append(" ")
                i += 2
            else:
                i += 1
            continue
        ad = m.group(0)[1:]
        i = m.end()
        if ad in _LATEX_METIN_KOMUT:
            if i < len(s) and s[i] == "{":
                ic, i = _latex_grup(s, i)
                cikti.append(_latex_metin(ic))
            continue
        if ad == "frac":
            if i < len(s) and s[i] == "{":
                ust, i = _latex_grup(s, i)
                alt = ""
                if i < len(s) and s[i] == "{":
                    alt, i = _latex_grup(s, i)
                cikti.append(f"({_latex_metin(ust)}) / ({_latex_metin(alt)})")
            continue
        if ad == "sqrt":
            if i < len(s) and s[i] == "{":
                ic, i = _latex_grup(s, i)
                cikti.append(f"√({_latex_metin(ic)})")
            continue
        if ad == "sum":
            alt = ust = ""
            if i < len(s) and s[i] == "_":
                i += 1
                if i < len(s) and s[i] == "{":
                    alt, i = _latex_grup(s, i)
            if i < len(s) and s[i] == "^":
                i += 1
                if i < len(s) and s[i] == "{":
                    ust, i = _latex_grup(s, i)
            cikti.append(f"Σ({alt}..{ust})" if alt and ust else "Σ")
            continue
        if ad in _LATEX_SEMBOL:
            cikti.append(_LATEX_SEMBOL[ad])
            continue
        if len(ad) == 1:
            # "\n", "\t" gibi kacis dizileri LaTeX degildir; ters bolu korunur
            cikti.append("\\" + ad)
            continue
        # Bilinmeyen komut: adi korunur (sessiz veri kaybi olmasin)
        cikti.append(ad)
    return re.sub(r"[ \t]+", " ", "".join(cikti)).strip()


def latex_temizle(metin: str) -> tuple:
    """Ham LaTeX kalintilarini okunur duz metne cevirir.

    script/style ve kod bloklari korunur: oradaki ters bolu LaTeX degildir
    (sayfa ici arama betiginde "\\u0307", "\\s+" gibi diziler var).
    Yalnizca metin dugumleri islenir; HTML etiketleri oldugu gibi kalir.
    Donus: (yeni_metin, cevrilen_komut_sayisi).
    """
    if not metin:
        return metin, 0
    if "\\" not in metin and "$$" not in metin:
        return metin, 0
    korunan = []

    def _koru(m):
        korunan.append(m.group(0))
        return f"\x00{len(korunan) - 1}\x00"

    calisma = re.sub(r"(?is)<(script|style)\b.*?</\1>", _koru, metin)
    calisma = re.sub(r"(?ms)^[ \t]*```.*?^[ \t]*```", _koru, calisma)
    calisma = re.sub(r"`[^`\n]*`", _koru, calisma)
    n = len(_LATEX_KOMUT.findall(calisma))
    if not n and not _LATEX_GOSTERIM.search(calisma):
        return metin, 0

    def _gosterim(m):
        ic = next(g for g in m.groups() if g is not None)
        return _latex_metin(ic)

    calisma = _LATEX_GOSTERIM.sub(_gosterim, calisma)

    def _html_blok(m):
        ic = m.group(1)
        if not _LATEX_KOMUT.search(ic) or len(ic) > 600:
            return m.group(0)
        if _LATEX_BLOK_YABANCI.search(ic):
            return m.group(0)
        return _latex_metin(ic)

    calisma = _LATEX_HTML_BLOK.sub(_html_blok, calisma)

    # Kalan komutlar: yalnizca metin dugumlerinde (etiketler korunur)
    parcalar = re.split(r"(<[^>]*>)", calisma)
    for idx, parca in enumerate(parcalar):
        if parca.startswith("<") and parca.endswith(">"):
            continue
        if _LATEX_KOMUT.search(parca):
            parcalar[idx] = _latex_metin(parca)
    calisma = "".join(parcalar)

    for i, parca in enumerate(korunan):
        calisma = calisma.replace(f"\x00{i}\x00", parca)
    return calisma, n or (0 if calisma == metin else 1)


def _sinirli_desen(yanlis: str) -> str:
    """Sozluk anahtarini kelime sinirli desene cevirir.

    Sinir yalnizca kenar karakteri HARF/RAKAM ise konur. Kosulsuz "\\b"
    kullanildiginda tirnak veya parantezle baslayan/biten anahtarlar hic
    eslesmiyordu: '"falling knife" (dusen bicak)' ve 'bir birikim
    (consolidation)' girdileri bu yuzden olu durumdaydi (2026-11-25).
    """
    sol = r"\b" if re.match(r"\w", yanlis) else ""
    sag = r"\b" if re.search(r"\w$", yanlis) else ""
    return sol + re.escape(yanlis) + sag


def imla(metin: str) -> tuple:
    """Yazim sozlugu + noktalama temizligi. Sayilara dokunmaz.
    Donus: (yeni_metin, duzeltmeler) — duzeltme = (eski, yeni)."""
    if not metin:
        return metin, []
    duzeltmeler = []

    # 0) cift ceviri onarimi — sozlukten ONCE calismali, yoksa "mean reversion"
    #    cevrilip tekrar uretilir.
    metin, n = _MEAN_REVERSION_TEK.subn(r"\1", metin)
    if n:
        duzeltmeler.append(("mean-reversion-tekrar", "parantezli karşılık silindi", n))

    # 1) yazim sozlugu (kelime sinirli, buyuk/kucuk ayri)
    for yanlis, dogru in YAZIM.items():
        if yanlis == dogru:
            continue
        yeni, n = re.subn(_sinirli_desen(yanlis), dogru, metin)
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
    # yuzde bicimi: TEK giris noktasi (yuzde_bicimle). Kayma onarimi, aralik,
    # ters yuzde, sonda yuzde ve isaret yeri ayni fonksiyonda ve DOGRU sirada.
    temiz, n = yuzde_bicimle(temiz)
    if n:
        duzeltmeler.append(("yüzde-biçimi", "TR yüzde konvansiyonu", n))
    # kisa yazim: "vb" -> "vb."
    temiz, n = _VB_KISA.subn("vb.", temiz)
    if n:
        duzeltmeler.append(("vb", "vb.", n))
    # 25.11: ceviri kalibi ve yanlis fiil kullanimi
    temiz, n = _STOP_ZARAR.subn("zihinsel stop", temiz)
    if n:
        duzeltmeler.append(("stop-zarar", "zihinsel stop", n))
    temiz, n = _ISARET_EDIYOR.subn(r"\1 gösteriyor", temiz)
    if n:
        duzeltmeler.append(("işaret-ediyor", "gösteriyor", n))
    temiz, n = _ISARET_ETTIGINI.subn("gösterdiğini gösteriyor", temiz)
    if n:
        duzeltmeler.append(("işaret-ettiğini", "gösterdiğini", n))
    temiz, n = _ENDEKS_EKI.subn("XU030'u", temiz)
    if n:
        duzeltmeler.append(("endeks-eki", "XU030'u", n))
    temiz, n = _AGIRLIKTA.subn(r"ağırlıklı \1", temiz)
    if n:
        duzeltmeler.append(("ağırlıkta", "ağırlıklı", n))
    temiz, n = _OK_ISARETI.subn(" → ", temiz)
    if n:
        duzeltmeler.append(("ok-işareti", "-> -> →", n))
    # yabanci yazi kaliintisi: baska alfabeden run'lar silinir, artan cift
    # bosluklar toplanir (Korece '경쟁' vakasi — 25.10 denetim)
    temiz, n = _YABANCI_YAZI_RE.subn(" ", temiz)
    if n:
        temiz = re.sub(r" {2,}", " ", temiz)
        duzeltmeler.append(("yabancı-yazı", "baska alfabeden kalinti silindi", n))

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
        yeni_satir = _CUMLE_BASI.sub(_cumle_basi, satir)
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
        return metin, {"imla": 0, "yumusatma": 0, "uyari": 0, "latex": 0}
    # 2026-11-26: ham LaTeX kalintisi imla'dan ONCE temizlenir; aksi halde
    # "\text{...}" icindeki kelimeler sozluk taramasina takilir.
    metin, latex_n = latex_temizle(metin)
    metin, imla_d = imla(metin)
    metin, yum, uyari = muglak(metin)
    for eski, yeni, _n in imla_d:
        log_kaydi(rapor, "imla", "yazim", eski, yeni, eski, True, kok)
    for eski, yeni in yum:
        log_kaydi(rapor, "muglak", "yumusatma", eski, yeni, eski, True, kok)
    for kalip, aciklama, baglam in uyari:
        log_kaydi(rapor, "uyari", aciklama, kalip, "", baglam, False, kok)
    ozet = {"imla": sum(n for _, _, n in imla_d), "yumusatma": len(yum),
            "uyari": len(uyari), "latex": latex_n}
    return metin, ozet


# ---------------------------------------------------------------------------
# 3b) KALITE TARAMASI (yayin kapisi)
# ---------------------------------------------------------------------------
# `imla()` bilinen hatalari duzeltir; bu tarama DUZELTMEDEN SONRA kalanlari
# bulur ve cagiran taraf (bot.py) esigi asarsa yaniti REDDEDER. Amac: yeni bir
# model yeni bir hata uretirse sayfaya ulasmasin, siradaki model densin.
#
# Desenler YUKSEK KESINLIKLI tutulur: Turkce bir raporda mesru kullanimi
# olmayan bicimler. Supheli ama mesru olabilecek kaliplar (orn. parantezle
# verilen Ingilizce terim) buraya GIRMEZ — yanlis pozitif yayini bloke eder.
KALITE_KALIPLARI = [
    (re.compile(r"\bkisa\b"), "yazım: 'kısa'"),
    (re.compile(r"\bsinyol\b"), "yazım: 'sinyal'"),
    (re.compile(r"\belimmizde\b"), "yazım: 'elimizde'"),
    (re.compile(r"\bteknikta\b"), "yazım: 'teknikte'"),
    (re.compile(r"\bbantına\b"), "yazım: 'bandına'"),
    (re.compile(r"\bdışardaki\b"), "yazım: 'dışarıdaki'"),
    (re.compile(r"\bağırlık(?:ta)?\s+hisse"), "yazım: 'ağırlıklı hisse'"),
    (re.compile(r"\bkorunabilirlik\b"), "uydurma sözcük: 'ihtiyat'"),
    (re.compile(r"\başağıladı"), "anlam hatası: 'aşağı çekti'"),
    (re.compile(r"\byüzde puan\b"), "yazım: 'puan'"),
    (re.compile(r"\bXU030'ü\b"), "ek uyumu: \"XU030'u\""),
    (re.compile(r"\bvb\.\."), "noktalama: 'vb.'"),
    # Yuzde bicimi yalnizca tablo DISI satirlarda aranir: tablo hucresinde
    # "3.6%" veri sozlesmesidir, imla kurali oraya uygulanmaz.
    # (?![-+±]?\d) sarti: "XU030 %2,24" ve "XU100 %-13,3" gibi DOGRU
    # bicimleri yakalamasin; isaretli sayi da yuzdeden SONRA gelir.
    (re.compile(r"\d\s*%(?![-+±]?\d)"), "yüzde biçimi: '%' sayının başında", True),
    # '%' iki rakamin ARASINA dusmus ("29,73%4", "43,79%5") — her zaman hata.
    (re.compile(r"\d%\d"), "yüzde biçimi: '%' rakamların arasında", True),
    # ARALIKTA tek yuzde: "%50-65" ve "50-65%" hatali, dogrusu "%50-%65"
    # (kullanici kurali 2026-11-25). Iki desen de yalnizca TEK yuzde tasiyan
    # bicimi yakalar; "%50-%65" her ikisine de uymaz.
    (re.compile(r"\d[ \t]*[-‑–][ \t]*\d+[ \t]*%"), "yüzde biçimi: aralıkta tek '%' (sonda)", True),
    (re.compile(r"%\d+[ \t]*[-‑–][ \t]*\d+(?![ \t]*%)"), "yüzde biçimi: aralıkta tek '%' (başta)", True),
    (re.compile(r"\bmagnet\b", re.I), "çeviri: 'hedef'"),
    (re.compile(r"\brally\b", re.I), "çeviri: 'ralli'"),
    (re.compile(r"\bFalling Knife\b", re.I), "çeviri: 'Düşen Bıçak'"),
    (re.compile(r"\bstop[- ]zarar\b", re.I), "çeviri: 'zihinsel stop'"),
    (re.compile(r"\bBölünme Stratejisi\b"), "çeviri: 'Dağıtım Stratejisi'"),
    # 2026-11-25: yabanci dil kalintisi. Canli sayfada "principalement"
    # (Fransizca) yayina girmisti; sozluk bunu yakalayamiyordu. Liste dar
    # tutuldu: yalnizca Turkce duz metinde mesru OLMAYAN baglaclar.
    (re.compile(r"\b(?:principalement|notamment|également|egalement|cependant|"
                r"toutefois|néanmoins|neanmoins|davantage|actuellement)\b", re.I),
     "yabancı sözcük: Fransızca kalıntı"),
    (re.compile(r"\b(?:therefore|however|moreover|furthermore|nevertheless|"
                r"whereas|thereby|hence|thus)\b", re.I),
     "yabancı sözcük: İngilizce bağlaç kalıntısı"),
    (re.compile(r"\w+(?:dığını|diğini|duğunu|düğünü|tığını|tiğini|tuğunu|tüğünü)"
                r"\s+işaret ediyor\b"), "kullanım: 'gösteriyor'"),
    # ASCII'ye dusmus Turkce: bu sozcukler Turkce metinde mesru degildir.
    # DIKKAT: yalnizca Turkce karsiligi OLMAYAN bicimler; "bekleniyor",
    # "hisselerin", "endekste", "nedeniyle" gibi dogru sozcukler GIRMEZ.
    (re.compile(r"\b(?:icin|gore|cunku|oldugunu|oldugu|olmadigi|degil|yuzde|"
                r"artis|dusus|yukselis|uzerinde|altinda|uzerine|altina|donem|"
                r"donemde|gosteriyor|sektor|sektorde)\b"),
     "ASCII Türkçe: eksik harf"),
    # 2026-11-26: ham LaTeX kalintisi. Site matematik motoru yuklemiyor
    # (KaTeX/MathJax yok); "\frac{...}", "\text{...}", "\times" gibi diziler
    # sayfada harf olarak gorunur. haftasonu-egitimi/2026-10-25 ve 2026-11-07
    # sayfalari boyle bozulmustu. Iki harften uzun komutlar aranir: "\n",
    # "\t", "\s" gibi tek harfli kacis dizileri LaTeX degildir.
    (re.compile(r"\\[a-zA-Z]{2,}"), "LaTeX kalıntısı: düz metne çevrilmeli"),
]
# Esik: bu sayidan fazla BULGU varsa yanit yayina alinmaz.
KALITE_ESIK = 2


def kalite_tara(metin: str) -> list:
    """Duzeltme sonrasi kalan Turkce hatalarini tarar.

    Donus: [(kural, aciklama, ornek)] — bos liste metin temiz demektir.
    Yalnizca desen eslesmesi aranir; dil modeli cagrilmaz, ag yok.
    Ucuncu elemani True olan kurallar tablo satirlarinda aranmaz.
    """
    if not metin:
        return []
    bulgular = []
    for kural in KALITE_KALIPLARI:
        desen, aciklama = kural[0], kural[1]
        tablo_haric = len(kural) > 2 and kural[2]
        kaynak = metin
        if tablo_haric:
            kaynak = "\n".join(s for s in metin.split("\n")
                               if not s.lstrip().startswith("|"))
        m = desen.search(kaynak)
        if m:
            bas = max(0, m.start() - 40)
            bulgular.append((desen.pattern, aciklama, kaynak[bas:m.end() + 40]))
    return bulgular


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
    # 2026-11-25 (architect bulgusu): "python -O editor.py" assert'leri
    # soydugu icin hicbir test kosmadan "tum kendini testler gecti" basiyordu.
    # Yanlis yesil yerine acik hata. Kalici testler: test_imla.py.
    if not __debug__:
        raise SystemExit(
            "editor.py self-test: -O ile assert'ler kaldirilir, test kosmaz. "
            "Kalici testler icin: python -m unittest test_imla -v")
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
