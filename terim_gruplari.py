"""Terim sözlüğü harf-grup tablosu — TEK KAYNAK (2026-10-27 kullanıcı kararı).

bot.py (TR hub + TR grup sayfaları) ve i18n.py (çeviri sonrası dil-yerel
yeniden harflendirme) aynı tabloyu kullanır. Sözlük 1 MB'lık tek sayfa
olmaktan çıkıp harf-grubu sayfalarına bölünür; her dil KENDİ alfabesine göre
harflendirir (ru Kiril, zh pinyin, de ümlüler temel harfe katlanır). Grup URL
adları dil-yereldir; dil_yollari.py tablosundaki "terimler/<tr-slug>" çiftleri
bu slugs'larla uyumlu tutulmalıdır.

Grup aralıkları sabittir (URL stabilitesi): terim dağılımı değişse de URL'ler
değişmez, sayfa içi denge hafif oynar. Hiçbir grubun harf kümesine sığmayan
terimler (Latin akronim, simge vb.) DIGER grubuna düşer; DIGER boşsa sayfa
üretilmez. Örn. ru'da "LIBOR", "SWOT-анализ" gibi Latin başlıklı terimler.

Zenginleşen harfler için aralıklar genişletilmez; denge bozulursa KULLANICI
kararıyla aralık seçimi yeniden yapılır (URL'ler 301'siz değişmez).
"""

import re

DIGER_SLUG = "diger"
DIGER_ETIKET = "Diğer"

# Her grup: (URL slug'ı, görünen etiket, o gruba giren BAŞ HARFLER)
# harfler string'i aynı zamanda grup içi alfabetik sırayı taşır.
_GRUPLAR = {
    "tr": [
        ("a-b", "A–B", "AB"),
        ("c-d", "C–D", "CÇD"),
        ("e-f", "E–F", "EF"),
        ("g-i", "G–İ", "GHIİ"),
        ("j-m", "J–M", "JKLM"),
        ("n-p", "N–P", "NOÖP"),
        ("r-s", "R–S", "RSŞ"),
        ("t-z", "T–Z", "TUÜVXYZ"),
    ],
    "en": [
        ("a-b", "A–B", "AB"),
        ("c-d", "C–D", "CD"),
        ("e-f", "E–F", "EF"),
        ("g-i", "G–I", "GHI"),
        ("j-m", "J–M", "JKLM"),
        ("n-p", "N–P", "NOP"),
        ("q-s", "Q–S", "QRS"),
        ("t-z", "T–Z", "TUVWXYZ"),
    ],
    # de: Ä/Ö/Ü temel harfe katlanır (alman sözlük uygulaması; dağılımda Ä yok,
    # Ö6+Ü32 katlanınca o/r ve u/z gruplarına dengeli yığılıyor).
    "de": [
        ("a-b", "A–B", "AB"),
        ("c-d", "C–D", "CD"),
        ("e-f", "E–F", "EF"),
        ("g-h", "G–H", "GH"),
        ("i-k", "I–K", "IJK"),
        ("l-n", "L–N", "LMN"),
        ("o-r", "O–R", "OQR"),
        ("s-z", "S–Z", "STUVWXYZ"),
    ],
    # ru: Kiril harflendirme; URL slug'ları ASCII transliterasyon
    # (dil_yollari.py ASCII güvenliği kuralı; Ы/Ь/Ъ ile başlayan terim yok).
    "ru": [
        ("a-b", "А–Б", "АБ"),
        ("v-g", "В–Г", "ВГ"),
        ("d-e", "Д–Е", "ДЕ"),
        ("zh-i", "Ж–И", "ЖЗИ"),
        ("y-l", "Й–Л", "ЙКЛ"),
        ("m-n", "М–Н", "МН"),
        ("o-p", "О–П", "ОП"),
        ("r-s", "Р–С", "РС"),
        ("t-kh", "Т–Х", "ТУФХ"),
        ("ts-ya", "Ц–Я", "ЦЧШЩЭЮЯ"),
    ],
    # zh: pinyin baş harfi (pypinyin); pinyin'de V yok. J ve Z çok yoğun.
    "zh": [
        ("a-c", "A–C", "ABC"),
        ("d-f", "D–F", "DEF"),
        ("g-h", "G–H", "GH"),
        ("j-k", "J–K", "JK"),
        ("l-n", "L–N", "LMN"),
        ("o-r", "O–R", "OQR"),
        ("s-w", "S–W", "STUVW"),
        ("x-y", "X–Y", "XY"),
        ("z", "Z", "Z"),
    ],
}


def gruplar(dil):
    """Dilin grup listesi: [{"slug", "etiket", "harfler"}, ...]."""
    return [dict(slug=s, etiket=e, harfler=h) for s, e, h in _GRUPLAR.get(dil, ())]


def grup_sluglari(dil):
    return [s for s, _e, _h in _GRUPLAR.get(dil, ())]


def harf_sirasi(dil):
    """Dilin birleşik baş-harf sırası (grup içi alfabetik sıralama anahtarı)."""
    return "".join(h for _s, _e, h in _GRUPLAR.get(dil, ()))


def grup_etiketi(dil, slug):
    for s, e, _h in _GRUPLAR.get(dil, ()):
        if s == slug:
            return e
    return DIGER_ETIKET if slug == DIGER_SLUG else None


def _zh_harf(terim):
    """pypinyin ile ilk pinyin'in baş harfi; Latin başlayanlar aynen döner.
    pypinyin kurulu değilse None (çağıran fallback'e düşer)."""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return None
    py = lazy_pinyin((terim or "").strip())[:1]
    if py and py[0] and py[0][0].isalpha():
        return py[0][0].upper()
    return None


def terim_bas_harf(dil, terim):
    """Terimin dil kurallarına göre BAŞ HARFİ (temizlenmiş) ya da None.

    Başlardaki tırnak/simge atılır (ru çevirilerinde '"' ile başlayan kirli
    terimler); TR'de 'i'->'İ' üst-harf sorunu düzeltilir; de'de ümlüler
    katlanır; zh'de pinyin baş harfi alınır."""
    t = (terim or "").strip()
    while t and not t[0].isalnum():
        t = t[1:]
    if not t:
        return None
    if dil == "zh":
        p = _zh_harf(t)
        if p:
            return p
    h = t[0]
    if dil == "tr":
        return "İ" if h == "i" else h.upper()
    if dil == "de":
        return {"Ä": "A", "Ö": "O", "Ü": "U"}.get(h.upper(), h.upper())
    return h.upper()


def terim_grup_slug(dil, terim):
    """Terimin dilindeki harf-grup URL slug'ı; sığmıyorsa DIGER_SLUG."""
    h = terim_bas_harf(dil, terim)
    if h:
        for s, _e, harfler in _GRUPLAR.get(dil, ()):
            if h in harfler:
                return s
    return DIGER_SLUG


def harf_grup_slug(dil, harf):
    """Verilen BAŞ HARF'in grubu; sığmıyorsa None."""
    for s, _e, harfler in _GRUPLAR.get(dil, ()):
        if harf in harfler:
            return s
    return None


# -- test ---------------------------------------------------------------
if __name__ == "__main__":
    ornekler = [
        ("tr", "Akreditif"), ("tr", "İhraç"), ("tr", "işlem"), ("tr", "Abuse"),
        ("en", "Letter of Credit"), ("de", "Übernahme"), ("de", "Änderung"),
        ("ru", "LIBOR"), ("ru", "«Ажио»"), ("ru", "Акция"),
        ("zh", "信用证"), ("zh", "LIBOR"), ("zh", "股票"),
    ]
    for d, t in ornekler:
        print(f"{d:3} {t!r:30} -> baş harf {terim_bas_harf(d, t)!r} grup {terim_grup_slug(d, t)!r}")
    for d in _GRUPLAR:
        gs = gruplar(d)
        assert len({g["slug"] for g in gs}) == len(gs), f"{d} slug çakışması"
        tum = harf_sirasi(d)
        assert len(tum) == len(set(tum)), f"{d} harf kümesi çakışması"
    print("tablo sağlam: 5 dil,", {d: len(v) for d, v in _GRUPLAR.items()})
