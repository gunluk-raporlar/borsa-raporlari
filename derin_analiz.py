"""Derin Analiz sayfasini gunluk bot'tan BAGIMSIZ uretir.

workflow_dispatch ile cagrilir; teknik tarama + borsapy sinyallerini taze
ceker, ZAI_API_KEY secret'iyla GLM'den uzun analizi isteyip derin-analiz.html
yazar. ~3-5 dakika surer (gunluk bot'un ~40 dakikalik LLM beklemelerine
mahkum degildir). ZAI_API_KEY yoksa hicbir sey yazmadan cikar.
"""
import os

os.environ.setdefault("AMD_API_KEY", "derin-analiz")

# --- DERIN ANALIZE OZEL SAGLAYICI AYARLARI (2026-10-21 kullanici kararlari) ---
# Bot.py'ye dokunmadan, import oncesi env ile etkin kilinir (bot bu env'leri
# import aninda okur). Yalnizca bu betigin kosularini etkiler.
# Discovery (DISC) derin analizden TAMAMEN CIKARILDI: kota 429'lari zincirin
# ilk denemesini her kosuda bosa yiyordu. Anahtar env'den dusurulur — bot DISC
# istemcisini hic kurmaz, derin_analiz_yap'in dogrudan yolu da Z.ai'a duser.
os.environ.pop("DISCOVERY_API_KEY", None)
# NVIDIA havuzu yalnizca kullanici secimi iki model: 3 ultra ONCE (13.10'daki
# "ultra once" karar precedansiyla), sonra 3.5 super vl. glm-5.3 /
# nemotron-3-super-120b / kimi-k3/k2.6 "calismiyor" — cikarildi.
os.environ["NVID_MODELS"] = (
    "nvidia/nemotron-3-ultra-550b-a55b,"        # build.nvidia.com katalog kimligi
    "nvidia/nemotron-3.5-super-vl-120b-a12b,"   # 120b tam kimlik
    "nvidia/nemotron-3.5-super-vl"              # yedek kimlik (yanlis olan 404 ile atlanir)
)

import json
import logging
import sys
from datetime import datetime
import zoneinfo

import bot

# --- DERIN ANALIZ SIRASI (2026-10-21 kullanici kararlari) ---
# Agirlikli NVIDIA: NVID (1. anahtar) + NVID2 (2. anahtar, bot.py'ye
# eklendi) ONE — calisan modeller bunlar; sonra ZAI (yenilenen anahtar),
# AMD ve ucretsiz arka saglayicilar. OR (OpenRouter) EN SONA: gunluk
# ucretsiz istek limiti (~50) gunduz doluyor, son care. DISC yok. OR model
# listesi (nemotron ailesi + inkling:free) bot.py OR_MODEL_TERCIH'te.
# max_deneme 9: 9 kuyrugun tamami siraya girebilsin. Enjeksiyon yalnizca bu
# betigin llm_call cagrilari icin gecerli; diger akislar bot'un varsayilan
# sirasini korur.
_orijinal_llm_call = bot.llm_call

def _derin_llm_call(prompt, **kwargs):
    kwargs.setdefault("sirasi", ("NVID", "NVID2", "ZAI", "AMD", "OPENCODE",
                                 "OLLAMA", "GEMINI", "YEDEK", "OR"))
    kwargs.setdefault("max_deneme", 9)
    return _orijinal_llm_call(prompt, **kwargs)

bot.llm_call = _derin_llm_call

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
    baslik_liste = []
    if os.path.exists(haber_yolu):
        with open(haber_yolu, encoding="utf-8") as f:
            baslik_liste = json.load(f)[:20]
        haberler = "\n".join(baslik_liste)
    # Sirket baglam katmani (2026-10-06 RAG): unvan/takma ad eslesmesiyle
    # sirket olaylarini (SPK, divalans, endeks cikisi) modele kesin baglar.
    haberler += bot._sirket_baglam_metni(baslik_liste)

    durum = {
        "news_data": haberler or "(bugun haber cekilmedi)",
        "tech_data": "",
        "final_report": "",
    }

    logger.info("GLM ile derin analiz uretiliyor...")
    analiz = bot.derin_analiz_yap(durum, teknik, borsapy)
    if not analiz:
        logger.error("Derin analiz uretilemedi (model hatasi veya bos yanit).")
        # Yayindaki sayfa korunur ama is KIRMIZI olsun; sessiz yesil kosu
        # uretim olmadigini gizliyor (makro_analiz.py ile ayni duzeltme).
        return 1

    # 2026-10-06 agac yapisi: tam sayfa kendi dizininde yasar —
    # derin-analiz/<tarih>.html (kok derin-analiz.html = hub).
    os.makedirs("derin-analiz", exist_ok=True)
    arsiv_html = bot.rapor_sayfasi(
        bot.markdown_to_html(analiz), date_str,
        baslik="Derin Analiz",
        alt_baslik="BIST 30 &bull; Yapay zeka destekli derinlemesine analiz",
        kok_yol=f"derin-analiz/{date_str}.html",
        donus_yolu="derin-analiz.html", donus_etiket="Tüm derin analizlere dön",
        aciklama=("BIST 30'un günlük derinlemesine analizi: sektör değerlendirmesi, EMA ve "
                  "Wave Trend teknik okuma, osilatör-momentum yorumları ve risk senaryoları."),
    )
    with open(f"derin-analiz/{date_str}.html", "w", encoding="utf-8") as f:
        f.write(arsiv_html)
    bot.build_hub_sayfalari()
    try:
        import kullanim_defteri
        kullanim_defteri.kaydet("derin")
    except Exception:
        logger.exception("[Defter] derin analiz kaydi yazilamadi.")
    try:
        bot.ana_sayfa_tazele()
    except Exception:
        logger.exception("Ana sayfa tazelenemedi; diger ciktilar etkilenmez.")
    try:
        bot.indexnow_ping(["derin-analiz.html", "raporlar.html", "index.html"])
    except Exception:
        logger.exception("IndexNow ping atlanamadi (sorun degil).")
    print(f"DERIN ANALIZ SAYFA URETILDI: {date_str} | {len(analiz)} karakter", flush=True)


if __name__ == "__main__":
    sys.exit(main())
