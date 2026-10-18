"""Dil agaclari arasi yol/slug eslemesi — TEK KAYNAK.

bot.py (_dil_yolu -> dil secici), i18n.py (en-uyarlama + sitemap ince ayari),
muhasebe_diller.py (secici) ve robot.py (secici) ayni tabloyu kullanir; boylece
dil secici hicbir zaman var olmayan bir adrese baglanti uretmez (18.10 kullanici
talebi: /en/muhasebe-terimleri 404'i ve benzerleri).

2026-10-07 kullanici karari: dil agaclarinda URL adlari dile gore degisir
(/en/reports.html gibi). Tablo NAV etiketlerinden turetilmistir; UZUNLUGA GORE
TERSTEN SIRALI olmali (muhasebe-terimleri, terimler'den once).
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

_SLUG_ESLEME = {"en": EN_SLUG_ESLEME}


def slug_degistir(deger, dil):
    """deger icindeki TR slug'larini ilgili dilin adlariyla degistirir.

    Guvenlik katmanlari: kelime siniri (borsa-raporlari domain'i, 'raporlari' gibi
    ek almış sozcukler), Turkce harfle devam, dil oneki (/de/ /ru/ /zh/ baglantilari
    Turkce yapiyi yansitir, dokunulmaz) ve IDEMPOTANSI — hedef slug eski slug'i
    iceriyorsa (robot -> trading-robot) zaten eslenmis deger yeniden eslenmez
    (en/sitemap.xml'deki trading-trading-robot hatasinin kokeni buydu)."""
    tablo = _SLUG_ESLEME.get(dil)
    if not tablo or not deger:
        return deger
    for eski, yeni in tablo:
        on_ek = yeni[:-len(eski)] if yeni.endswith(eski) else ""
        desen = r"(?<![a-z0-9])(?<!/de/)(?<!/ru/)(?<!/zh/)"
        if on_ek:
            desen += "(?<!" + re.escape(on_ek) + ")"
        deger = re.sub(desen + re.escape(eski) + r"(?![a-z0-9çğıöşü])", yeni, deger)
    return deger
