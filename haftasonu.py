# -*- coding: utf-8 -*-
"""Hafta Sonu Gündem Ajanı — sadece hafta sonları çalışır.

Cumartesi/Pazar günlerinde ekonomi, finans, emlak, ticaret, para/döviz ve
jeopolitik haberlerini geniş bir kaynak havuzundan toplar; LLM (yoksa şablon)
bunları "hafta sonu gündem değerlendirmesi + yeni hafta ajandası" olarak yazar;
haftasonu.html ve arşiv (haftasonu/<tarih>.html) üretir. Böylece yeni haftaya
başlamadan önce ziyaretçi genel bir gündemle hazırlanır.

- Zamanlama: hafta sonu (Pazar akşamı cron). Hafta içi çalıştırılırsa FORCE=1
  olmadıkça hiçbir şey yapmaz (hafta içi gündemi günlük rapor zaten kapsar).
- İçerik: bilgilendirme amaçlıdır, yatırım tavsiyesi değildir; kaynaklar listelenir.
"""
import os
import re
import sys
import json
import time
import glob
import logging
from datetime import datetime
import zoneinfo

os.environ.setdefault("AMD_API_KEY", "haftasonu")

import bot  # noqa: E402

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("haftasonu")

DUMMY_ANAHTARLAR = {"", "teknik-tarama", "derin-analiz", "radyo", "migrate-rebuild",
                    "radyo-test", "test", "x", "haftasonu", "dummy-ayri-test"}

# Kategori -> [(kaynak adi, rss url)]. TR finans kaynaklari bot.py'den miras,
# uzerine hafta sonuna ozel tema + kuresel akislar eklenir.
_EKSTRA_KATEGORILER = {
    "Emlak": [
        ("GoogleNews-Emlak", "https://news.google.com/rss/search?q=emlak%20konut%20piyasas%C4%B1&hl=tr&gl=TR&ceid=TR:tr"),
        ("Sozcu Emlak", "https://www.sozcu.com.tr/feeds-rss-category-emlak"),
        ("Isindetayi Gayrimenkul", "https://www.isindetayi.com/rss/gayrimenkul"),
    ],
    "Para & Döviz": [
        ("GoogleNews-Doviz", "https://news.google.com/rss/search?q=d%C3%B6viz%20kur%20piyasas%C4%B1%20TL&hl=tr&gl=TR&ceid=TR:tr"),
        ("Doviz.com", "https://www.doviz.com/news/rss"),
        ("Investing TR Forex", "https://tr.investing.com/rss/forex.rss"),
        ("Paranin Yonu", "https://www.paraninyonu.com.tr/rss.xml"),
        ("Sozcu Emtia", "https://www.sozcu.com.tr/feeds-rss-category-emtia"),
        ("Investing TR Emtia", "https://tr.investing.com/rss/commodities.rss"),
    ],
    "Ticaret & Dış Ticaret": [
        ("GoogleNews-Ticaret", "https://news.google.com/rss/search?q=ihracat%20ithalat%20ticaret&hl=tr&gl=TR&ceid=TR:tr"),
    ],
    "Jeopolitik": [
        ("GoogleNews-Jeopolitik", "https://news.google.com/rss/search?q=jeopolitik%20riskler%20petrol&hl=tr&gl=TR&ceid=TR:tr"),
        ("AA Guncel", "https://www.aa.com.tr/tr/rss/default?cat=guncel"),
        ("DW Turkce", "https://rss.dw.com/rdf/rss-tur-all"),
        ("Hurriyet Dunya", "https://www.hurriyet.com.tr/rss/dunya"),
        ("Sabah Dunya", "https://www.sabah.com.tr/rss/dunya.xml"),
    ],
    "Küresel Ekonomi": [
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("GoogleNews-Dunya", "https://news.google.com/rss/search?q=world%20economy%20markets&hl=en&gl=US&ceid=US:en"),
    ],
}

_EXTRA_FILTRE = ["spor", "futbol", "magazin", "dizi", "yemek tarifi", "hava durumu",
                 "survivor", "masterchef", "milli piyango", "sayısal loto"]


def kategoriler():
    """Kategori -> kaynak listesi (TR finans + ekstralar)."""
    k = {}
    tr_liste = [(ad, url) for ad, url in bot.HABER_KAYNAKLARI.items()]
    tr_liste.append(("Onedio Ekonomi", "https://onedio.com/Publisher/publisher-ekonomi.rss"))
    k["Ekonomi & Finans (Türkiye)"] = tr_liste
    k.update(_EKSTRA_KATEGORILER)
    return k


def haberleri_topla():
    """Kategorilere ayrilmis baslik listesi toplar; data/weekend/<tarih>.json kaydeder."""
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    gorulen = set()

    def norm(t):
        return re.sub(r"[^a-z0-9çğıöşü]", "", t.lower())

    def temiz(baslik):
        for k in _EXTRA_FILTRE + bot.FINANS_DISI_KELIMELER:
            if k.lower() in baslik.lower():
                return None
        return re.sub(r"^\[[^\]]*\]\s*", "", baslik).strip()

    sonuc = {}
    for kategori, kaynaklar in kategoriler().items():
        liste = []
        for ad, url in kaynaklar:
            try:
                f = __import__("feedparser").parse(url)
                for e in f.entries[:6]:
                    b = temiz(e.title)
                    if not b:
                        continue
                    anahtar = norm(b)
                    if anahtar in gorulen:
                        continue
                    gorulen.add(anahtar)
                    liste.append(f"[{ad}] {b}")
            except Exception:
                continue
            time.sleep(0.2)
        sonuc[kategori] = liste[:18]

    klasor = os.path.join(bot.DATA_DIR, "weekend")
    os.makedirs(klasor, exist_ok=True)
    kayit = {"tarih": bugun, "kategoriler": sonuc}
    with open(os.path.join(klasor, f"{bugun}.json"), "w", encoding="utf-8") as fh:
        json.dump(kayit, fh, ensure_ascii=False, indent=1)
    toplam = sum(len(v) for v in sonuc.values())
    logger.info("Hafta sonu haberleri toplandi: %d baslik (%d kategori)", toplam, len(sonuc))
    return sonuc


_PROMPT = """Sen bir ekonomi gündemi editörü yapay zekasısın. Aşağıda HAFTA SONU boyunca ekonomi, finans, emlak, ticaret, para/döviz ve jeopolitik alanlarında toplanmış gerçek haber başlıkları var. Bunları "Hafta Sonu Gündem" raporuna dönüştür: genel bir değerlendirme + yeni haftaya başlamadan önce izlenmesi gereken başlıklar.

Kurallar:
- Rapor Türkçe; "## " ile başlayan markdown başlıkları kullan.
- Yapı (aynı sırayla):
## 1. Hafta Sonu Gündem Özeti  (3-5 maddelik genel tablo: bu hafta sonu öne çıkan temalar ne, haftaya nasıl giriyoruz)
## 2. Ekonomi & Finans (Türkiye)
## 3. Para & Döviz
## 4. Emlak
## 5. Ticaret & Dış Ticaret
## 6. Jeopolitik & Küresel Ekonomi
## 7. Yeni Hafta Ajandası  (önümüzdeki hafta hangi başlıklar/gelişmeler izlenecek — madde madde)
- Her bölümde haberleri madde olarak değil, KISA YORUMLU anlat (2-4 cümle/bölüm); gerektiğinde başlıktaki kaynağı parantez içinde an ("[BBC Business]" gibi). Uzun liste kopyalama.
- Bir kategoride kayda değer haber yoksa "Bu hafta sonu ... alanında kayda değer bir gelişme öne çıkmadı." yaz.
- YALNIZCA verilen başlıklardan hareket et; dışarıdan veri/rakam ekleme, uydurma. Rakam geçiyorsa başlıktaki haliyle yaz.
- Tarih, yayıncı, "Analist:", kurum/unvan kimlik satırı YAZMA. Haftanın gün adını yazma.
- Kesin al-sat/emlak yatırımı yönlendirmesi yapma; bilgilendirme tonu.
- Son paragraf: "Bu içerik yapay zeka ile hafta sonu haberlerinden derlenmiştir; bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."

[HABER BAŞLIKLARI]
{haberler}"""


def _gundem_uret(kategoriler):
    """LLM dener; erisilemezse sablon dondurur."""
    blok = ""
    for kat, liste in kategoriler.items():
        blok += f"\n### {kat}\n" + ("\n".join("- " + b for b in liste) if liste else "(haber yok)")
    prompt = _PROMPT.format(haberler=blok[:9000])
    metin = None
    if not os.environ.get("SKIP_LLM") == "1":
        if os.environ.get("ZAI_API_KEY") and os.environ["ZAI_API_KEY"] not in DUMMY_ANAHTARLAR:
            try:
                metin = bot._zai_call(prompt)
            except Exception as e:
                logger.warning("Z.ai gundem denemesi basarisiz: %s", e)
        if not metin or metin.startswith("(LLM"):
            keys = {k: os.environ.get(k, "") for k in ("AMD_API_KEY", "CF_API_KEY", "ALT_API_KEY", "OR_API_KEY")}
            if any(v and v not in DUMMY_ANAHTARLAR for v in keys.values()):
                try:
                    metin = bot.llm_call(prompt, sirasi=("AMD", "CF", "YEDEK", "OR"))
                except Exception as e:
                    logger.warning("LLM gundem basarisiz: %s", e)
    if not metin or metin.startswith("(LLM"):
        logger.warning("LLM kullanilamadi; sablon gundem uretilecek.")
        satirlar = ["## 1. Hafta Sonu Gündem Özeti",
                    "Bu bölüm yapay zeka yerine otomatik şablonla hazırlandı; aşağıda hafta sonu boyunca öne çıkan başlıklar kaynaklarıyla listeleniyor.",
                    "## 2. Ekonomi & Finans (Türkiye)"]
        for kat in ("Ekonomi & Finans (Türkiye)", "Para & Döviz", "Emlak", "Ticaret & Dış Ticaret", "Jeopolitik", "Küresel Ekonomi"):
            liste = kategoriler.get(kat) or []
            satirlar.append(f"### {kat}")
            satirlar.extend("- " + b for b in liste[:12]) if liste else satirlar.append("(kayda değer haber yok)")
        satirlar.append("## 7. Yeni Hafta Ajandası")
        satirlar.append("- Hafta içi günlük raporlar, teknik tarama ve BIST Radyo ile takibe devam edilecek.")
        satirlar.append("- Yukarıdaki başlıklardaki gelişmelerin piyasalara yansıması izlenecek.")
        satirlar.append("_Bu içerik yapay zeka ile hafta sonu haberlerinden derlenmiştir; bilgilendirme amaçlıdır, yatırım tavsiyesi değildir._")
        return "\n\n".join(satirlar)
    return bot.rapor_son_islem(metin)


def _kaynak_dipnotu(kategoriler):
    satirlar = ["**Haber kaynakları:**"]
    for kat, liste in kategoriler.items():
        if liste:
            adlar = sorted({b.split("]")[0][1:] for b in liste if b.startswith("[")})
            satirlar.append(f"- {kat}: {', '.join(adlar)}")
    return "\n".join(satirlar)


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    simdi = datetime.now(tz)
    tarih = simdi.strftime("%Y-%m-%d")
    if simdi.weekday() < 5:
        # Hafta ici ASLA calismaz (workflow_dispatch ile elle tetiklense bile):
        # hafta ici gundemi gunluk rapor kapsar, hafta sonu sayisi hafta icinde yayinlanamaz.
        print("HAFTA ICI: hafta sonu gundemi uretilmedi (sadece Cumartesi/Pazar calisir).", flush=True)
        return 0

    kategoriler = haberleri_topla()
    metin = _gundem_uret(kategoriler)
    metin = bot._tarih_gun_duzelt(metin)
    gvde = bot.markdown_to_html(metin) + "\n" + bot.markdown_to_html(_kaynak_dipnotu(kategoriler))

    icerik = f"""
<div class="hero">
<h1>Hafta Sonu Gündemi</h1>
<div class="meta"><span class="badge">{tarih}</span><span>Yapay zeka destekli hafta sonu gündem değerlendirmesi — yeni haftaya hazırlık</span></div>
</div>
<article class="report" id="rapor-ses-metin">{gvde}</article>
<p style="margin-top:18px"><a href="index.html">&larr; Ana sayfaya dön</a></p>"""
    html = bot._sayfa(
        f"Hafta Sonu Gündemi - {tarih}", icerik, aktif="haftasonu", yol="haftasonu.html",
        aciklama="Hafta sonu ekonomi, finans, emlak, ticaret, para/döviz ve jeopolitik haberlerinin yapay zeka ile derlenmiş gündem değerlendirmesi ve yeni hafta ajandası.",
    )
    with open("haftasonu.html", "w", encoding="utf-8") as f:
        f.write(html)
    arsiv_dir = "haftasonu"
    os.makedirs(arsiv_dir, exist_ok=True)
    with open(os.path.join(arsiv_dir, f"{tarih}.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HAFTA SONU GUNDEMI HAZIR: {tarih} | {sum(len(v) for v in kategoriler.values())} baslik", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
