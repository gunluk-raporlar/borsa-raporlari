"""Dil agaclari arasi yol/slug eslemesi — TEK KAYNAK.

bot.py (_dil_yolu -> dil secici), i18n.py (dil-uyarlama + sitemap ince ayari),
muhasebe_diller.py (secici) ve robot.py (secici) ayni tabloyu kullanir; boylece
dil secici hicbir zaman var olmayan bir adrese baglanti uretmez (18.10 kullanici
talebi: /en/muhasebe-terimleri 404'i ve benzerleri).

2026-10-07 kullanici karari: dil agaclarinda URL adlari dile gore degisir
(/en/reports.html gibi). Tablo NAV etiketlerinden turetilmistir; UZUNLUGA GORE
TERSTEN SIRALI olmali (muhasebe-terimleri, terimler'den once).

2026-10-22 kullanici karari: ayni yerellestirme de/ru/zh agaclarina da uygulanir.
Yazim bicimi kullanici secimidir (mekanik SOZLUK turevi degildir): de=Almanca
(ASCII guvenli yazim: ae/oe/ue), ru=transliterasyon (Kiril URL degil), zh=pinyin
(Baidu/Google uygulamasinda onerilen Latin yazim). Tum hedefler ASCII'dir.
"""

import re
from pathlib import Path

EN_SLUG_ESLEME = [
    ("muhasebe-terimleri", "accounting-terms"),
    ("sirket-haberleri", "company-news"),
    ("haftasonu-egitimi", "market-school"),
    ("borsapy-analiz", "borsapy-signals"),
    ("sinyal-karnesi", "signal-scorecard"),
    ("teknik-analiz", "technical-scan"),
    ("derin-analiz", "deep-analysis"),
    ("makro-analiz", "macro-analysis"),
    ("haftasonu", "weekend"),
    ("haberler", "news"),
    ("raporlar", "reports"),
    ("takvim", "calendar"),
    ("sozluk", "glossary"),
    ("terimler", "terms"),
    ("gizlilik", "privacy"),
    ("radyo", "radio"),
    ("hisse", "stocks"),
    ("robot", "trading-robot"),
]

# de: Almanca slug'lar (SOZLUK basliklarindan; ASCII yazim ae/oe/ue)
DE_SLUG_ESLEME = [
    ("muhasebe-terimleri", "rechnungslegungsbegriffe"),
    ("sirket-haberleri", "unternehmensnachrichten"),
    ("haftasonu-egitimi", "boersenschule"),
    ("borsapy-analiz", "borsapy-signale"),
    ("sinyal-karnesi", "signal-bilanz"),
    ("teknik-analiz", "technischer-scan"),
    ("derin-analiz", "tiefenanalyse"),
    ("makro-analiz", "makroanalyse"),
    ("haftasonu", "wochenende"),
    ("haberler", "nachrichten"),
    ("raporlar", "berichte"),
    ("takvim", "kalender"),
    ("sozluk", "glossar"),
    ("terimler", "begriffe"),
    ("gizlilik", "datenschutz"),
    ("radyo", "radio"),
    ("hisse", "aktien"),
    ("robot", "handelsroboter"),
]

# ru: transliterasyon (Kiril URL degil — ASCII mimari guvenligi; kullanici karari)
RU_SLUG_ESLEME = [
    ("muhasebe-terimleri", "bukhgalterskie-terminy"),
    ("sirket-haberleri", "korporativnye-novosti"),
    ("haftasonu-egitimi", "shkola-rinka"),
    ("borsapy-analiz", "borsapy-signaly"),
    ("sinyal-karnesi", "svodka-signalov"),
    ("teknik-analiz", "tekhnicheskiy-analiz"),
    ("derin-analiz", "glubokiy-analiz"),
    ("makro-analiz", "makroanaliz"),
    ("haftasonu", "vykhodnye"),
    ("haberler", "novosti"),
    ("raporlar", "otchety"),
    ("takvim", "kalendar"),
    ("sozluk", "glossariy"),
    ("terimler", "terminy"),
    ("gizlilik", "konfidencialnost"),
    ("radyo", "radio"),
    ("hisse", "aktsii"),
    ("robot", "torgovyy-robot"),
]

# zh: pinyin (kullanici karari; Baidu/Google uygulamasinda onerilen Latin yazim)
ZH_SLUG_ESLEME = [
    ("muhasebe-terimleri", "kuaiji-shuyu"),
    ("sirket-haberleri", "gongsi-xinwen"),
    ("haftasonu-egitimi", "gushi-xuetang"),
    ("borsapy-analiz", "borsapy-xinhao"),
    ("sinyal-karnesi", "xinhao-jifenka"),
    ("teknik-analiz", "jishu-saomiao"),
    ("derin-analiz", "shendu-fenxi"),
    ("makro-analiz", "hongguan-fenxi"),
    ("haftasonu", "zhoumo"),
    ("haberler", "xinwen"),
    ("raporlar", "baogao"),
    ("takvim", "rili"),
    ("sozluk", "cidian"),
    ("terimler", "shuyu"),
    ("gizlilik", "yinsi"),
    ("radyo", "guangbo"),
    ("hisse", "gupiao"),
    ("robot", "jiaoyi-jiqiren"),
]

_SLUG_ESLEME = {
    "en": EN_SLUG_ESLEME,
    "de": DE_SLUG_ESLEME,
    "ru": RU_SLUG_ESLEME,
    "zh": ZH_SLUG_ESLEME,
}

# "UZUNLUGA GORE TERSTEN SIRALI" sarti sozde degil, koda bagli (2026-10-22
# architect onerisi): uzun eski once islenir, kisa eski icine giremez.
for _tablo in (EN_SLUG_ESLEME, DE_SLUG_ESLEME, RU_SLUG_ESLEME, ZH_SLUG_ESLEME):
    _tablo.sort(key=lambda cift: len(cift[0]), reverse=True)

# KALICI KATMAN (2026-10-22 kullanici karari): tablolardaki hedef slug'lar
# (trading-robot, torgovyy-robot, handelsroboter...) yerlesmistir; HICBIR dil
# gecisi, baska bir dilin yerlestirdigi slug'in eski parcasina bir daha
# dokunamaz. Boylece zincirleme gecis (orn. once en sonra de) "trading-
# handelsroboter" tarzi bozuk adres uretmez — kalici adlar degismez.
_ON_EKLER = {}
for _tablo in _SLUG_ESLEME.values():
    for _eski, _yeni in _tablo:
        if _yeni.endswith(_eski):
            _ON_EKLER.setdefault(_eski, set()).add(_yeni[:-len(_eski)])

SLUG_DILLER = tuple(_SLUG_ESLEME)

# Hizli eleme desenleri (i18n dosya taramasi "bu dosyada esleme var mi?" icin)
SLUG_TANILAR = {
    dil: re.compile("|".join(re.escape(e) for e, _ in tablo))
    for dil, tablo in _SLUG_ESLEME.items()
}


def slug_degistir(deger, dil):
    """deger icindeki TR slug'larini ilgili dilin adlariyla degistirir.

    Guvenlik katmanlari: kelime siniri (borsa-raporlari domain'i, 'raporlari' gibi
    ek almış sozcukler), Turkce harfle devam, dil oneki — BASKA bir dil agacina
    giden yol (/en/ /de/ /ru/ /zh/, kendisi haric) dokunulmaz — IDEMPOTANSI —
    hedef slug eski slug'i iceriyorsa zaten eslenmis deger yeniden eslenmez —
    ve KALICI KATMAN: herhangi bir dilin hedefi eski slug'la bitiyorsa o onek
    TUM gecislerde korumadir (trading-robot / torgovyy-robot ... baska dilde
    robot'a eslenmez)."""
    tablo = _SLUG_ESLEME.get(dil)
    if not tablo or not deger:
        return deger
    # baska dil onekleri altindaki slug'lara dokunma (kendi onekimiz serbest)
    koruma = "".join(f"(?<!/{x}/)" for x in SLUG_DILLER if x != dil)
    for eski, yeni in tablo:
        desen = r"(?<![a-z0-9])" + koruma
        for on_ek in sorted(_ON_EKLER.get(eski, ())):
            desen += "(?<!" + re.escape(on_ek) + ")"
        deger = re.sub(desen + re.escape(eski) + r"(?![a-z0-9çğıöşü])", yeni, deger)
    return deger


# ---------------------------------------------------------------------------
# Sayfa hedefi cozumleme (dil secici + menu tazeleme)
# ---------------------------------------------------------------------------
# 30.10.2026 kullanici bildirimi: /hisse/ sayfasinda dil secilince ana sayfa
# aciliyordu. Kok neden: bolum index'leri (hisse/index.html, radyo/index.html)
# icin hedef "{dil}/{yol}.html" diye araniyor, "{dil}/{yol}/index.html" hic
# denenmiyordu; hedef bulunamayinca dil ana sayfasina dusuluyordu. Asagidaki
# cozumleyici hem .html hem de /index.html hedefini bilir; boylece dil secici
# ayni sayfanin (varsa) cevirisine, yoksa ait oldugu bolumun hub'ina gider.

_TERS_SLUG = None


def tr_yolu(rel):
    """Cevrilmis dosya yolunun TR karsiligini turetir.

    en/stocks/GARAN.html -> hisse/GARAN; de/begriffe/c-d.html -> terimler/c-d;
    de/index.html -> ''; hisse/index.html -> hisse.
    """
    global _TERS_SLUG
    if _TERS_SLUG is None:
        _TERS_SLUG = {d: {yeni: eski for eski, yeni in t}
                      for d, t in _SLUG_ESLEME.items()}
    parcalar = list(Path(rel).parts)
    geri = {}
    if parcalar and parcalar[0] in _SLUG_ESLEME:
        geri = _TERS_SLUG[parcalar[0]]
        parcalar = parcalar[1:]
    yollar = []
    for p in parcalar:
        if p.endswith(".html"):
            p = p[:-5]
        yollar.append(geri.get(p, p))
    if yollar and yollar[-1] == "index":
        yollar = yollar[:-1]
    return "/".join(yollar)


def _dil_slug(tr_yol, dil):
    p = (tr_yol or "").strip("/")
    return slug_degistir(p, dil) if dil else p


def hedef_dosya(tr_yol, dil, kok=".", dizin=False):
    """Ayni sayfanin dil agacindaki dosyasini bulur (yoksa None).

    tr_yol uzantisiz ve bas/son slash'siz olmali. dizin=True ise once
    "{dil}/{p}/index.html", sonra "{dil}/{p}.html" denenir; boylece
    'hisse/' gibi bolum index'leri dogru cozulur.
    """
    kok = Path(kok)
    p = _dil_slug(tr_yol, dil)
    onek = kok / dil if dil else kok
    if not p:
        adaylar = [onek / "index.html"]
    elif dizin:
        adaylar = [onek / p / "index.html", onek / f"{p}.html"]
    else:
        adaylar = [onek / f"{p}.html", onek / p / "index.html"]
    for aday in adaylar:
        if aday.is_file():
            return aday
    return None


def hedef_url(tr_yol, dil, kok=".", site_url="", dizin=False):
    """Dil secici icin ayni sayfanin hedef URL'sini uretir.

    Ayni sayfa o dilde yoksa once bolum hub'ina ({dil}/{bolum} veya
    {dil}/{bolum}/), o da yoksa dil ana sayfasina duser. Boylece secici
    hicbir zaman 404 uretmez; bolum index'leri de ana sayfaya dusmez.
    """
    kok = Path(kok)
    taban = (site_url or "").rstrip("/") + "/"
    hedef = hedef_dosya(tr_yol, dil, kok, dizin=dizin)
    if hedef is not None:
        rel = hedef.relative_to(kok).as_posix()
        if dil and rel.startswith(dil + "/"):
            rel = rel[len(dil) + 1:]
        if rel.endswith("index.html"):
            rel = rel[: -len("index.html")]
        elif rel.endswith(".html"):
            rel = rel[:-5]
        return f"{taban}{dil + '/' if dil else ''}{rel}"
    # Bolum hub'i: makale cevrilmemisse listeye, ana sayfaya degil.
    p = _dil_slug(tr_yol, dil)
    bolum = p.split("/")[0] if p else ""
    if bolum:
        for aday, sonek in ((f"{bolum}.html", ""), (f"{bolum}/index.html", "/")):
            yol = (kok / dil / aday) if dil else (kok / aday)
            if yol.is_file():
                return f"{taban}{dil + '/' if dil else ''}{bolum}{sonek}"
    return f"{taban}{dil + '/' if dil else ''}"


_DIL_NAV_RE = re.compile(
    r'<nav\b[^>]*\bclass="[^"]*\bdil-linkler\b[^"]*"[^>]*>.*?</nav>',
    re.S | re.I,
)
_DIL_A_RE = re.compile(r"<a\b[^>]*>", re.I)
_DIL_HREF_RE = re.compile(r'\bhref="([^"]*)"', re.I)
_DIL_DATA_RE = re.compile(r'\bdata-dil="(tr|en|de|ru|zh)"', re.I)


def menu_tazele(html, rel, kok=".", site_url=""):
    """Sayfadaki dil menusunun href'lerini guncel hedeflerle tazeler.

    Yalnizca <nav class="dil-linkler"> icindeki <a> etiketlerinin href'i
    degisir; aria-label, dil adlari ve aktif sinifi korunur. (html, degisim)
    dondurur. Boylece eski kosularda uretilmis sayfalar da onarilir.
    """
    m = _DIL_NAV_RE.search(html)
    if not m:
        return html, 0
    nav = m.group(0)
    tr_rel = tr_yolu(rel)
    sayi = 0

    def _a_degistir(mo):
        nonlocal sayi
        etiket = mo.group(0)
        dm = _DIL_DATA_RE.search(etiket)
        hm = _DIL_HREF_RE.search(etiket)
        if not dm or not hm:
            return etiket
        dil = dm.group(1).lower()
        yeni = hedef_url(tr_rel, "" if dil == "tr" else dil, kok, site_url)
        if hm.group(1) == yeni:
            return etiket
        sayi += 1
        return etiket[:hm.start(1)] + yeni + etiket[hm.end(1):]

    yeni_nav = _DIL_A_RE.sub(_a_degistir, nav)
    if not sayi:
        return html, 0
    return html[:m.start()] + yeni_nav + html[m.end():], sayi
