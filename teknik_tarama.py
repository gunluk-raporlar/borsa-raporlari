"""Teknik taramayi gunluk bot'tan BAGIMSIZ calistirir.

GitHub Actions kronu tarafindan piyasa saatlerinde (hafta ici 10:00-18:30 TR)
30 dakikada bir cagrilir; LLM, haber, portfoy veya rapor uretimi YOKTUR.
Sadece teknik-analiz.html ve anasayfadaki 'One Cikanlar' bolumu guncellenir.
Kron adi saglamasi gerektirmedigi icin hicbir secret'a ihtiyaci yoktur.
"""
import os

# bot.py import ederken en az bir LLM anahtari istiyor; tarama LLM kullanmadigi
# icin anahtar yoksa kukla deger gecilir (bot'un tarama/dis HTML fonksiyonlari
# anahtara dokunmaz).
os.environ.setdefault("AMD_API_KEY", "teknik-tarama")

import logging
from datetime import datetime
import zoneinfo

import bot

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("teknik-tarama")


def _yaz_degistiyse(yol, icerik):
    """Dosyayi yalniz icerik gercekten degistiyse yazar; True/False doner.
    (IndexNow'ya ayni URL'yi bos yere tekrar tekrar pinglememek icin.)"""
    try:
        with open(yol, encoding="utf-8") as f:
            if f.read() == icerik:
                return False
    except OSError:
        pass
    with open(yol, "w", encoding="utf-8") as f:
        f.write(icerik)
    return True


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime("%Y-%m-%d")

    satirlar = bot.teknik_tarama_yap()
    if not satirlar:
        logger.warning("Tarama veri alamadi; sayfalar degistirilmedi.")
        return

    degisti = _yaz_degistiyse("teknik-analiz.html", bot.build_teknik_html(satirlar, date_str))
    bot.ticker_json_yaz(satirlar)

    # Borsapy (TradingView) sinyal sayfasi: hatasi teknik sayfayi etkilemesin.
    try:
        bs_satirlar = bot.borsapy_analiz_yap()
        if bs_satirlar:
            if _yaz_degistiyse("borsapy-analiz.html", bot.build_borsapy_html(bs_satirlar, date_str)):
                degisti = True
    except Exception:
        logger.exception("borsapy-analiz.html uretilemedi; teknik sayfa etkilenmez.")

    raporlar = sorted((fn for fn in os.listdir("reports") if fn.endswith(".html")), reverse=True)
    p = bot.load_portfolio()
    oneriler = [s for s in satirlar if s["genel"] in ("GÜÇLÜ AL", "AL")][:6]
    if _yaz_degistiyse("index.html", bot.build_index_html(p, raporlar, oneriler)):
        degisti = True

    if degisti:
        try:
            bot.indexnow_ping(["teknik-analiz.html", "borsapy-analiz.html", "index.html"])
        except Exception:
            logger.exception("IndexNow ping atlanamadi (sorun degil).")

    guclu = sum(1 for s in satirlar if s["genel"] == "GÜÇLÜ AL")
    print(f"TEKNIK TARAMA GUNCELLENDI: {date_str} | {len(satirlar)} hisse | {guclu} GÜÇLÜ AL", flush=True)


if __name__ == "__main__":
    main()
