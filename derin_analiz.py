"""Derin Analiz sayfasini gunluk bot'tan BAGIMSIZ uretir.

workflow_dispatch ile cagrilir; teknik tarama + borsapy sinyallerini taze
ceker, ZAI_API_KEY secret'iyla GLM'den uzun analizi isteyip derin-analiz.html
yazar. ~3-5 dakika surer (gunluk bot'un ~40 dakikalik LLM beklemelerine
mahkum degildir). ZAI_API_KEY yoksa hicbir sey yazmadan cikar.
"""
import os

os.environ.setdefault("AMD_API_KEY", "derin-analiz")

import json
import logging
from datetime import datetime
import zoneinfo

import bot

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("derin-analiz")


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime("%Y-%m-%d")

    if not os.environ.get("ZAI_API_KEY"):
        logger.warning("ZAI_API_KEY tanimli degil; derin analiz uretilemez.")
        return

    logger.info("Teknik tarama aliniyor...")
    teknik = bot.teknik_tarama_yap()
    logger.info("Borsapy sinyalleri aliniyor...")
    borsapy = bot.borsapy_analiz_yap()
    if not teknik or not borsapy:
        logger.warning("Veri alinamadi (teknik=%d, borsapy=%d); cikiliyor.", len(teknik), len(borsapy))
        return

    # Bugunun haber basliklari (varsa) prompta eklenir
    haber_yolu = os.path.join(bot.DATA_DIR, "news", f"{date_str}.json")
    haberler = ""
    if os.path.exists(haber_yolu):
        with open(haber_yolu, encoding="utf-8") as f:
            haberler = "\n".join(json.load(f)[:20])

    durum = {
        "news_data": haberler or "(bugun haber cekilmedi)",
        "tech_data": "",
        "final_report": "",
    }

    logger.info("GLM ile derin analiz uretiliyor...")
    analiz = bot.derin_analiz_yap(durum, teknik, borsapy)
    if not analiz:
        logger.error("Derin analiz uretilemedi (model hatasi veya bos yanit).")
        return

    with open("derin-analiz.html", "w", encoding="utf-8") as f:
        f.write(bot.rapor_sayfasi(bot.markdown_to_html(analiz), date_str + " — Derin Analiz"))
    print(f"DERIN ANALIZ SAYFA URETILDI: {date_str} | {len(analiz)} karakter", flush=True)


if __name__ == "__main__":
    main()
