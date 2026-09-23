"""Makroekonomik Degerlendirme sayfasini uretir (makro-analiz.html).

derin_analiz.py desenini izler: gunluk bottan BAGIMSIZ calisir, ZAI_API_KEY
secret'iyla GLM'den uzun makro analizi ister. Veri tabani bot.makro_cek()
(TradingView ekonomik takvimi -> data/makro.json) uzerinden beslenir; model
yalnizca bu kesin rakamlari kullanir. ZAI_API_KEY yoksa sayfa 404 VERMESIN
diye veri tablosuyla placeholder yazar (var olan iyi sayfayi bozmaz).
Haftada bir (Pazartesi) ve workflow_dispatch ile calisir; makro veri
gunluk degismez.
"""
import os

os.environ.setdefault("AMD_API_KEY", "makro-analiz")

import logging
from datetime import datetime
import zoneinfo

import bot

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("makro-analiz")


def _veri_tablosu(makro):
    """Gosterge tablosu HTML'i (sayfanin alt bolumu; her uretimde guncel)."""
    satirlar = "".join(
        f"<tr><td>{g['ulke']}</td><td>{g['ad']}</td>"
        f"<td><strong>{g['deger']}{g['birim']}</strong></td><td>{g['donem']}</td></tr>"
        for g in makro["gostergeler"]
    )
    return (
        '<h2 class="section-title">Veri Tabanı (bu analizde kullanılan kesin rakamlar)</h2>'
        '<div class="card" style="padding:8px 24px 16px"><div class="tbl-wrap"><table>'
        "<tr><th>Ülke</th><th>Gösterge</th><th>Son Değer</th><th>Dönem</th></tr>"
        f"{satirlar}</table></div>"
        f'<p style="color:var(--muted);font-size:12.5px">Kaynak: {makro.get("kaynak", "")}'
        f' &bull; güncelleme: {makro.get("guncelleme", "")}. Analiz metni yalnızca bu '
        "tablodaki rakamlarla üretilir ve yayın öncesi doğrulama katmanından geçer.</p></div>"
    )


def _placeholder_yaz(makro, eksik_anahtar):
    """Tam analiz yokken sayfanin 404 VERMEMESI icin veri tablosuyla basit sayfa yazar.

    Haftalik workflow basariyla calistiginda tam analiz bu sayfanin yerine yazar;
    amac yalnizca nav/sitemap/IndexNow linkini yasli tutmamak.
    """
    if os.path.exists("makro-analiz.html") and eksik_anahtar:
        # Anahtar gecici olarak yoksa yayindaki iyi sayfayi bozma.
        logger.info("Mevcut makro-analiz.html korunuyor.")
        return
    if os.path.exists("makro-analiz.html") and not eksik_anahtar:
        logger.info("Model hatasi; yayindaki makro-analiz.html korunuyor.")
        return
    tarih = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y")
    metin = (f"## Makroekonomik Değerlendirme\n\n"
             f"Bu sayfa, {tarih} itibarıyla güncellenen makro veri tabanını "
             "yayınlar. Derinlemesine makroekonomik değerlendirme, haftalık "
             "otomatik analiz çalışmasında bu sayfada yayınlanacaktır.\n\n"
             "Aşağıdaki tablo, analizde kullanılan kesin göstergelerin "
             "güncel değerleridir.")
    sarmal = dict(
        baslik="Makroekonomik Değerlendirme",
        alt_baslik="Türkiye ve küresel makro görünüm &bull; Yapay zeka destekli derinlemesine analiz",
        kok_yol="makro-analiz.html",
        aciklama=("Türkiye ve küresel makroekonomik görünümün derinlemesine değerlendirmesi: "
                  "enflasyon, politika faizi, büyüme, cari denge, aktarım mekanizmaları "
                  "ve BIST 30'a sektör kanallarıyla yansımalar."),
    )
    html = bot.rapor_sayfasi(
        bot.markdown_to_html(metin) + _veri_tablosu(makro),
        datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d"),
        **sarmal)
    with open("makro-analiz.html", "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Placeholder makro-analiz.html yazildi (veri tablosuyla).")


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime("%Y-%m-%d")

    logger.info("Makro veri tabani guncelleniyor...")
    makro = bot.makro_cek()
    if not makro or not makro.get("gostergeler"):
        logger.warning("Makro veri alinamadi; cikiliyor.")
        return
    logger.info("%d makro gosterge hazir.", len(makro["gostergeler"]))

    if not os.environ.get("ZAI_API_KEY"):
        logger.warning("ZAI_API_KEY tanimli degil; tam analiz uretilemeyecek.")
        _placeholder_yaz(makro, eksik_anahtar=True)
        return

    logger.info("GLM ile makroekonomik degerlendirme uretiliyor...")
    analiz = bot.makro_analiz_yap()
    if not analiz:
        logger.warning("Makro analiz uretilemedi; mevcut sayfa korunuyor/placeholder.")
        _placeholder_yaz(makro, eksik_anahtar=False)
        return

    # Veri tablosu bolumu: metnin altina her zaman guncel gosterge tablosu
    veri_bolumu = _veri_tablosu(makro)

    sarmal = dict(
        baslik="Makroekonomik Değerlendirme",
        alt_baslik="Türkiye ve küresel makro görünüm &bull; Yapay zeka destekli derinlemesine analiz",
        kok_yol="makro-analiz.html",
        aciklama=("Türkiye ve küresel makroekonomik görünümün derinlemesine değerlendirmesi: "
                  "enflasyon, politika faizi, büyüme, cari denge, aktarım mekanizmaları "
                  "ve BIST 30'a sektör kanallarıyla yansımalar."),
    )
    html = bot.rapor_sayfasi(
        bot.markdown_to_html(analiz) + veri_bolumu, date_str, **sarmal)
    with open("makro-analiz.html", "w", encoding="utf-8") as f:
        f.write(html)
    try:
        bot.indexnow_ping(["makro-analiz.html", "index.html"])
    except Exception:
        logger.exception("IndexNow ping atlandi (sorun degil).")
    print(f"MAKRO ANALIZ SAYFA URETILDI: {date_str} | {len(analiz)} karakter", flush=True)


if __name__ == "__main__":
    main()
