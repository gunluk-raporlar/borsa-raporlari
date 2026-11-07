# -*- coding: utf-8 -*-
"""dist HTML'lerine ajan-okunurluk ekleri koyar (hreflang + .md linkleri).

Üç BAĞIMSIZ adım uygular; her biri KENDİ idempotent işaretine bakar, birinin
eklenmiş olması ötekini engellemez (2026-11-05 öncesi tek işaretli kontrol,
hreflang kümesi eklenmiş sayfada md link adımlarını da atlıyordu):

  1) HEAD MD LINK  — işaret: 'type="text/markdown"'. Karşılık .md dosyası
     dist'te VARSA <head>'e <link rel="alternate" type="text/markdown">
     satırı ekler (yerleşim: </title> baştan bar; yoksa meta description,
     o da yoksa <head> açılışı).
  2) FOOTER MD SATIRI — işaret: 'LLM/ajanlar için Markdown sürümü'.
     Karşılık .md VARSA </footer> kapanışından hemen önceye görünür md
     satırı ekler; </footer> yoksa atlanır (fail-open).
  3) HREFLANG      — işaret: 'rel="alternate" hreflang='. TR <-> en/de/ru/zh
     karşılıklarını SABİT eşleme tablolarıyla bulur; karşılık .html dosyası
     dist'te VARSA <head>'e rel="alternate" hreflang link kümesi ekler.
     URL'ler uzantısızdır (güzel-URL; baglanti_guzellestir.py ile aynı
     biçim — bu script ondan SONRA koşar). Karşılığı olmayan sayfaya
     hreflang EKLENMEZ (404'e işaret yasağı). Tarihli sayfalarda karşılık
     AYNI tarihte aranır; en sürümü yoksa x-default TR'ye düşer.

Eşleme tabloları pipeline/md_ayna.py (KOK_ESLE/KLASOR_ESLE) ile AYNI adları
kullanır; iki script bilinçli bağımsızdır (import yok, tablo tekrarı kabul
edilmiştir) — bir tarafı değiştirirsen öbürünü de değiştir.

Kullanım: python hreflang_ekle.py dist
"""
import os
import re
import sys

SITE = "https://borsa-raporlari.pages.dev"
DILLER = ("en", "de", "ru", "zh")

# Bu işaretler bot.py ile senkron — değiştirirsen öbürünü de değiştir.
# (HEAD MD LINK bot.py'deki md_ek satırının, FOOTER MD bot.py'deki md_footer
# bloğunun birebir çıktısıdır; HREFLANG kümesini yalnız bu script üretir.)
MD_HEAD_ISARET = 'type="text/markdown"'
MD_FOOTER_ISARET = "LLM/ajanlar için Markdown sürümü"
HREFLANG_ISARET = 'rel="alternate" hreflang='

# hreflang değerleri: tablo anahtarı "zh", ama sitenin MEVCUT kümeleri
# (i18n hattının dil sayfalarına yazdığı) Çince için "zh-Hans" kullanır —
# karşılıklı kümelerin değeri tutarlı olsun diye aynı değer basılır.
HF_DEGER = {"tr": "tr", "en": "en", "de": "de", "ru": "ru", "zh": "zh-Hans"}

# TR adı -> dil adı (ana sayfalar). pipeline/md_ayna.py KOK_ESLE ile aynı.
KOK_ESLE = {
    "en": {"index": "index", "raporlar": "reports", "derin-analiz": "deep-analysis",
           "makro-analiz": "macro-analysis", "teknik-analiz": "technical-scan",
           "sinyal-karnesi": "signal-scorecard", "portfoy": "portfolio",
           "haftasonu": "weekend", "haberler": "news", "takvim": "calendar",
           "sozluk": "glossary", "terimler": "terms",
           "muhasebe-terimleri": "accounting-terms", "hisse": "stocks"},
    "de": {"index": "index", "raporlar": "berichte", "derin-analiz": "tiefenanalyse",
           "makro-analiz": "makroanalyse", "teknik-analiz": "technischer-scan",
           "sinyal-karnesi": "signal-bilanz", "portfoy": "portfolio",
           "haftasonu": "wochenende", "haberler": "nachrichten", "takvim": "kalender",
           "sozluk": "glossar", "terimler": "begriffe",
           "muhasebe-terimleri": "rechnungslegungsbegriffe", "hisse": "aktien"},
    "ru": {"index": "index", "raporlar": "otchety", "derin-analiz": "glubokiy-analiz",
           "makro-analiz": "makroanaliz", "teknik-analiz": "tekhnicheskiy-analiz",
           "sinyal-karnesi": "svodka-signalov", "portfoy": "portfel",
           "haftasonu": "vykhodnye", "haberler": "novosti", "takvim": "kalendar",
           "sozluk": "glossariy", "terimler": "terminy",
           "muhasebe-terimleri": "bukhgalterskie-terminy", "hisse": "aktsii"},
    "zh": {"index": "index", "raporlar": "baogao", "derin-analiz": "shendu-fenxi",
           "makro-analiz": "hongguan-fenxi", "teknik-analiz": "jishu-saomiao",
           "sinyal-karnesi": "xinhao-jifenka", "portfoy": "touzizuhe",
           "haftasonu": "zhoumo", "haberler": "xinwen", "takvim": "rili",
           "sozluk": "cidian", "terimler": "shuyu",
           "muhasebe-terimleri": "kuaiji-shuyu", "hisse": "gupiao"},
}
# TR klasör -> dil klasör (tarihli arşivler + hisse/terimler ağaçları).
KLASOR_ESLE = {
    "en": {"raporlar": "reports", "derin-analiz": "deep-analysis",
           "makro-analiz": "macro-analysis", "haftasonu": "weekend",
           "hisse": "stocks", "terimler": "terms"},
    "de": {"raporlar": "berichte", "derin-analiz": "tiefenanalyse",
           "makro-analiz": "makroanalyse", "haftasonu": "wochenende",
           "hisse": "aktien", "terimler": "begriffe"},
    "ru": {"raporlar": "otchety", "derin-analiz": "glubokiy-analiz",
           "makro-analiz": "makroanaliz", "haftasonu": "vykhodnye",
           "hisse": "aktsii", "terimler": "terminy"},
    "zh": {"raporlar": "baogao", "derin-analiz": "shendu-fenxi",
           "makro-analiz": "hongguan-fenxi", "haftasonu": "zhoumo",
           "hisse": "gupiao", "terimler": "shuyu"},
}

_TERS_KOK = {d: {v: k for k, v in t.items()} for d, t in KOK_ESLE.items()}
_TERS_KLASOR = {d: {v: k for k, v in t.items()} for d, t in KLASOR_ESLE.items()}


def _ileri(tr_rel, dil):
    """TR rel yolu (uzantısız) -> dil rel adayı veya None."""
    parcalar = tr_rel.split("/")
    if len(parcalar) == 1:
        ad = KOK_ESLE.get(dil, {}).get(parcalar[0])
        return (dil + "/" + ad) if ad else None
    ilk, kalan = parcalar[0], "/".join(parcalar[1:])
    klasor = KLASOR_ESLE.get(dil, {}).get(ilk)
    if klasor:
        return dil + "/" + klasor + "/" + kalan
    return None


def _ters(dil_rel):
    """Dil rel yolu -> TR rel adayı veya None."""
    dil, _, kalan = dil_rel.partition("/")
    if dil not in DILLER or not kalan:
        return None
    parcalar = kalan.split("/")
    if len(parcalar) == 1:
        return _TERS_KOK.get(dil, {}).get(parcalar[0])
    klasor = _TERS_KLASOR.get(dil, {}).get(parcalar[0])
    if klasor:
        return klasor + "/" + "/".join(parcalar[1:])
    return None


def _kume(rel, dist):
    """rel için (dil -> rel) hreflang kümesi; yalnız dist'te .html olarak
    VAR olan diller girer, <2 girdi ise küme kurulmaz (tek dillik sayfaya
    hreflang anlamsızdır)."""
    if rel.split("/")[0] in DILLER:
        tr = _ters(rel)
        if not tr:
            return None
    else:
        tr = rel
    aday = {"tr": tr}
    for dil in DILLER:
        k = _ileri(tr, dil)
        if k:
            aday[dil] = k
    mevcut = {d: r for d, r in aday.items()
              if os.path.isfile(os.path.join(dist, r + ".html"))}
    if "tr" not in mevcut:
        # TR karşılığı yok (ör. harf-grup adları diller arasında farklıdır):
        # küme kurulmaz — x-default'ın 404'e işaret etmesi yasak.
        return None
    if len(mevcut) < 2:
        return None
    return mevcut


def _url(rel):
    """rel -> güzel-URL. Ana sayfalar sitenin mevcut küme kuralına uyar:
    kök "/" , dil ana sayfaları "/<dil>/" (ör. en/index -> /en/)."""
    if rel == "index":
        return SITE + "/"
    if rel.endswith("/index") and rel.split("/")[0] in DILLER:
        return SITE + "/" + rel.split("/")[0] + "/"
    return SITE + "/" + rel


def _md_href(rel):
    """rel -> .md aynasının tam adresi (bot.py md_href biçimiyle aynı:
    kök sayfa /index.md, ötekiler /<rel>.md)."""
    if rel == "index":
        return SITE + "/index.md"
    return SITE + "/" + rel + ".md"


def _md_head(ham, rel, dist):
    """1. adım HEAD MD LINK: karşılık .md dist'te VARSA ve sayfada işaret
    yoksa <head>'e type="text/markdown" alternate linkini ekler."""
    if MD_HEAD_ISARET in ham:
        return ham, False           # zaten eklenmiş: idempotent atla
    if not os.path.isfile(os.path.join(dist, rel + ".md")):
        return ham, False           # karşılık .md yok: ekleme yapılmaz
    m = re.search(r"</title>", ham, re.I)
    if not m:
        m = re.search(r'<meta name="description"[^>]*>', ham, re.I)
    if not m:
        m = re.search(r"<head[^>]*>", ham, re.I)
    if not m:
        return ham, False
    satir = ('\n<link rel="alternate" type="text/markdown" href="'
             + _md_href(rel) + '">')
    return ham[:m.end()] + satir + ham[m.end():], True


def _md_footer(ham, rel, dist):
    """2. adım FOOTER MD SATIRI: karşılık .md dist'te VARSA ve sayfada
    işaret yoksa </footer> kapanışının hemen öncesine görünür md satırını
    ekler; </footer> yoksa atlanır (fail-open)."""
    if MD_FOOTER_ISARET in ham:
        return ham, False           # zaten eklenmiş: idempotent atla
    if not os.path.isfile(os.path.join(dist, rel + ".md")):
        return ham, False           # karşılık .md yok: ekleme yapılmaz
    m = re.search(r"</footer>", ham, re.I)
    if not m:
        return ham, False
    tam_md = _md_href(rel)
    blok = ('<br><span style="font-size:11.5px">📄 LLM/ajanlar için Markdown sürümü: '
            '<a href="' + tam_md + '" style="color:inherit">'
            + tam_md.replace("https://", "") + "</a></span>")
    return ham[:m.start()] + blok + ham[m.start():], True


def _hreflang(ham, rel, dist):
    """3. adım HREFLANG: küme kurulabiliyorsa <head>'e ekler."""
    if HREFLANG_ISARET in ham:
        return ham, False           # zaten eklenmiş: idempotent atla
    kume = _kume(rel, dist)
    if not kume:
        return ham, False
    satirlar = []
    for dil in ("tr",) + DILLER:
        if dil in kume:
            satirlar.append('<link rel="alternate" hreflang="%s" href="%s"/>'
                            % (HF_DEGER[dil], _url(kume[dil])))
    x_varsayilan = "en" if "en" in kume else "tr"
    satirlar.append('<link rel="alternate" hreflang="x-default" href="%s"/>'
                    % _url(kume[x_varsayilan]))
    blok = "\n" + "\n".join(satirlar)
    # yerleşim: bot.py <head> şablonunda </title> baştan bardır; </title>
    # yoksa meta description, o da yoksa <head> açılışının ardına konur.
    m = re.search(r"</title>", ham, re.I)
    if not m:
        m = re.search(r'<meta name="description"[^>]*>', ham, re.I)
    if not m:
        m = re.search(r"<head[^>]*>", ham, re.I)
    if not m:
        return ham, False
    return ham[:m.end()] + blok + ham[m.end():], True


def _sayfa_isle(yol, rel, dist):
    """Üç bağımsız adımı sırayla uygular; (head, footer, hreflang) değişim
    üçlüsü döner. Değişiklik olduysa dosyaya bir kez yazılır."""
    with open(yol, encoding="utf-8", errors="replace") as fh:
        ham = fh.read()
    ham, d_head = _md_head(ham, rel, dist)
    ham, d_footer = _md_footer(ham, rel, dist)
    ham, d_hreflang = _hreflang(ham, rel, dist)
    if d_head or d_footer or d_hreflang:
        with open(yol, "w", encoding="utf-8", newline="") as fh:
            fh.write(ham)
    return d_head, d_footer, d_hreflang


def main():
    dist = sys.argv[1] if len(sys.argv) > 1 else "dist"
    head = footer = hreflang = atlanan = 0
    for kokd, _alt, dosyalar in os.walk(dist):
        for ad in dosyalar:
            if not ad.endswith(".html"):
                continue
            yol = os.path.join(kokd, ad)
            rel = os.path.relpath(yol, dist).replace("\\", "/")[:-5]
            try:
                d_head, d_footer, d_hreflang = _sayfa_isle(yol, rel, dist)
                head += d_head
                footer += d_footer
                hreflang += d_hreflang
                if not (d_head or d_footer or d_hreflang):
                    atlanan += 1
            except Exception as e:
                atlanan += 1
                print(f"[uyari] {rel}: {e}")
    print(f"[md-gorsel] head={head} footer={footer} hreflang={hreflang}, "
          f"atlanan={atlanan} ({dist}).")


if __name__ == "__main__":
    main()
