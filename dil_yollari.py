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
    giden yol (/en/ /de/ /ru/ /zh/, kendisi haric) dokunulmaz — ve IDEMPOTANSI —
    hedef slug eski slug'i iceriyorsa (robot -> trading-robot) zaten eslenmis
    deger yeniden eslenmez (en/sitemap.xml'deki trading-trading-robot hatasinin
    kokeni buydu)."""
    tablo = _SLUG_ESLEME.get(dil)
    if not tablo or not deger:
        return deger
    # baska dil onekleri altindaki slug'lara dokunma (kendi onekimiz serbest)
    koruma = "".join(f"(?<!/{x}/)" for x in SLUG_DILLER if x != dil)
    for eski, yeni in tablo:
        on_ek = yeni[:-len(eski)] if yeni.endswith(eski) else ""
        desen = r"(?<![a-z0-9])" + koruma
        if on_ek:
            desen += "(?<!" + re.escape(on_ek) + ")"
        deger = re.sub(desen + re.escape(eski) + r"(?![a-z0-9çğıöşü])", yeni, deger)
    return deger
