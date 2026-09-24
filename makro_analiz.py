"""Makroekonomik Degerlendirme sayfasini uretir (makro-analiz.html).

derin_analiz.py desenini izler: gunluk bottan BAGIMSIZ calisir, ZAI_API_KEY
secret'iyla GLM'den uzun makro analizi ister; GLM alinamazsa gercek
AMD_API_KEY ile DeepSeek-V4-Flash yedegine duser. Veri tabani yalnizca
rapor tarihinden bir önceki aksam snapshot'ından gelir; model bu kilitli
cercevedeki rakamlari kullanir. Hiç anahtar yoksa sayfa 404 VERMESIN
diye veri tablosuyla placeholder yazar (var olan iyi sayfayi bozmaz).
Haftada bir (Pazartesi) ve workflow_dispatch ile calisir; veri bir gun onceki
aksam snapshot'indan okunur.
"""
import os

# CI'da secrets AMD_API_KEY gercek anahtari bu satirdan ONCE ortama konmus olur;
# lokal/anahtarsiz ortamda bot import'unun anahtar zorunluluunu karsilamak icin
# sahte bir deger koyariz — yedek yol yalnizca GERCEK anahtar goruldugunde acilir.
_GERCEK_AMD = bool(os.environ.get("AMD_API_KEY", "").strip())
os.environ.setdefault("AMD_API_KEY", "makro-analiz")

import logging
from datetime import datetime
import zoneinfo

import bot
import makro_veri

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("makro-analiz")


def _birlestir(deger, birim):
    if deger is None:
        return "—"
    if birim == "%":
        return f"{deger}%"
    if birim:
        return f"{deger} {birim}"
    return str(deger)


def _veri_tablosu(makro):
    """Genişletilmiş göstergeler, tahmin/önceki ve kaynak bilgisi tablosu."""
    satirlar = "".join(
        "<tr>"
        f"<td>{g['ulke']}</td><td>{g['ad']}</td>"
        f"<td><strong>{_birlestir(g['deger'], g['birim'])}</strong></td>"
        f"<td>{_birlestir(g.get('tahmin'), g['birim'])}</td>"
        f"<td>{_birlestir(g.get('onceki'), g['birim'])}</td>"
        f"<td>{g['donem']}</td><td>{g.get('frekans') or '—'}</td>"
        f"<td>{g.get('kaynak', 'TradingView')}</td>"
        "</tr>"
        for g in makro["gostergeler"]
    )
    return (
        '<h2 class="section-title">Veri Tabanı (analizde kullanılan kilitli göstergeler)</h2>'
        '<div class="card" style="padding:8px 24px 16px"><div class="tbl-wrap"><table>'
        "<tr><th>Kapsam/Ülke</th><th>Gösterge</th><th>Gerçekleşen</th>"
        "<th>Tahmin</th><th>Önceki</th><th>Dönem</th><th>Frekans</th><th>Kaynak</th></tr>"
        f"{satirlar}</table></div>"
        f'<p style="color:var(--muted);font-size:12.5px">Kaynak: {makro.get("kaynak", "")}'
        f' &bull; snapshot: {makro.get("guncelleme", "")}. Analiz metni yalnızca bu '
        "tablodaki rakamlarla üretilir; yoksa sayı yazılmaz ve yayın öncesi "
        "deterministik doğrulamadan geçirilir.</p></div>"
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

    logger.info("Onceki aksam makro snapshot'i yukleniyor...")
    try:
        snapshot = bot.makro_snapshot_cek(expected_report_date=date_str)
        makro = makro_veri.legacy_data(snapshot)
    except makro_veri.SnapshotError as exc:
        logger.error("Geçerli makro snapshot yok: %s", exc)
        return 1
    logger.info("%d makro gosterge hazir (%s).", len(makro["gostergeler"]),
                snapshot["snapshot_id"])

    zai = bool(os.environ.get("ZAI_API_KEY"))
    if not zai and not _GERCEK_AMD:
        logger.warning("ZAI_API_KEY/AMD_API_KEY yok; tam analiz uretilemeyecek.")
        _placeholder_yaz(makro, eksik_anahtar=True)
        return

    logger.info("Makroekonomik degerlendirme uretiliyor (GLM ana, DeepSeek yedek)...")
    analiz = bot.makro_analiz_yap(yedek_amd=_GERCEK_AMD, snapshot=snapshot)
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
