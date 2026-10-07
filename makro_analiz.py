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
import sys
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
    """Tam analiz yokken kok makro-analiz.html'i hub olarak tazeler (2026-10-06).

    Hub (tum makro raporlarin kart listesi) kok sayfanin yeni rolu; tam analiz
    uretilse de üretilmese de kok bu listeden yeniden yazilir. Eski davranis
    (kok'e tam sayfa/placeholder yazmak) kaldirildi: kok artik her zaman hub.
    """
    bot.build_hub_sayfalari()
    import glob as _glob
    logger.info("kok makro-analiz.html hub olarak tazelendi (arsiv: %d).",
                len(_glob.glob("makro-analiz/????-??-??.html")))


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime("%Y-%m-%d")

    # Tazelik kapisi (2026-10-06): Sali-Cuma guvenlik kosulari icin — arsivde
    # 48 saatten taze makro raporu varsa uretim kesilir (1 dk'da cikar).
    # MAKRO_FORCE=1 ile kapı atlanir (zorla yenileme).
    if os.environ.get("MAKRO_FORCE") != "1":
        import glob as _glob
        arsivler = sorted(_glob.glob("makro-analiz/????-??-??.html"))
        if arsivler:
            son_t = os.path.basename(arsivler[-1])[:10]
            try:
                son_d = datetime.strptime(son_t, "%Y-%m-%d").date()
                fark = (datetime.now(tz).date() - son_d).days
                if fark < 2:
                    logger.info("Makro analiz %s tarihli (%d gun) — 48 saat taze, "
                                "uretim atlandi (guvenlik kosusu).", son_t, fark)
                    return 0
            except ValueError:
                pass

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
        return 1

    logger.info("Makroekonomik degerlendirme uretiliyor (GLM ana, DeepSeek yedek)...")
    analiz = bot.makro_analiz_yap(yedek_amd=_GERCEK_AMD, snapshot=snapshot)
    if not analiz:
        logger.warning("Makro analiz uretilemedi; mevcut sayfa korunuyor/placeholder.")
        _placeholder_yaz(makro, eksik_anahtar=False)
        # Yayindaki sayfa korunur ama is KIRMIZI olsun: sessiz basari
        # 2026-09-29'da uretim olmadigini Actions uzerinden gormeyi engelledi.
        return 1

    # Veri tablosu bolumu: metnin altina her zaman guncel gosterge tablosu
    veri_bolumu = _veri_tablosu(makro)

    # 2026-10-06 agac yapisi: tam sayfa kendi dizininde yasar —
    # makro-analiz/<tarih>.html (kok makro-analiz.html = hub).
    os.makedirs("makro-analiz", exist_ok=True)
    arsiv_yolu = f"makro-analiz/{date_str}.html"
    arsiv_sayfa = bot.rapor_sayfasi(
        bot.markdown_to_html(analiz) + veri_bolumu, date_str,
        baslik="Makroekonomik Değerlendirme",
        alt_baslik="Türkiye ve küresel makro görünüm &bull; Yapay zeka destekli derinlemesine analiz",
        kok_yol=arsiv_yolu,
        aciklama=f"{bot._tr_tarih(date_str)} tarihli haftalik makro analiz raporu.",
    )
    with open(arsiv_yolu, "w", encoding="utf-8") as f:
        f.write(arsiv_sayfa)
    bot.build_hub_sayfalari()
    try:
        bot.indexnow_ping(["makro-analiz.html", "raporlar.html", "index.html"])
    except Exception:
        logger.exception("IndexNow ping atlandi (sorun degil).")
    print(f"MAKRO ANALIZ SAYFA URETILDI: {date_str} | {len(analiz)} karakter", flush=True)


if __name__ == "__main__":
    sys.exit(main())
