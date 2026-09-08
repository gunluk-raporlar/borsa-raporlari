import os
import json
import time
import socket
import re
from collections import Counter
import pandas as pd
from datetime import datetime, timedelta
import zoneinfo
from openai import OpenAI
from typing import TypedDict
from langgraph.graph import StateGraph, END
import feedparser
import markdown
import logging

# Ag takilmalarinda sonsuza kadar beklememek icin genel soket zaman asimi.
socket.setdefaulttimeout(30)

# ==== LLM SAGLAYICILARI (ana + yedek) ====
# Ana saglayici: AMD Radeon Developer Cloud (Cin'de barindirilir; GitHub
# runner'larindan baglanti arada kopar). Bu yuzden OpenAI-uyumlu bir YEDEK
# saglayici da desteklenir: ALT_API_KEY girilirse AMD'nin tum denemeleri
# tukendiginde (veya hic anahtar verilmazsa) yedek devreye girer.
AMD_API_KEY = os.environ.get("AMD_API_KEY", "")

# Yedek saglayici varsayilani: Groq (ucretsiz katman: ~30 istek/dk, ~1.000 istek/gun;
# model bazli dakikalik token siniri vardir — buyuk promptlar 429 alabilir, bu yuzden
# yedek oncelikli olarak OZET gibi kucuk cagrilara ayrilir, ana rapor AMD'de kalir).
# Ucretsiz anahtar: https://console.groq.com  (secret adi: GROQ_API_KEY veya ALT_API_KEY)
# Alternatif saglayicilar (sadece env ile gec):
#   Google AI Studio: ALT_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  ALT_MODELS=gemini-2.5-flash
#   OpenRouter:       ALT_BASE_URL=https://openrouter.ai/api/v1        ALT_MODELS=deepseek/deepseek-chat-v3.1:free
ALT_API_KEY = os.environ.get("ALT_API_KEY") or os.environ.get("GROQ_API_KEY", "")
ALT_BASE_URL = os.environ.get("ALT_BASE_URL", "https://api.groq.com/openai/v1")
# Bos birakilirsa (varsayilan) saglayicinin /models listesinden otomatik secilir:
# Groq 2026'da llama-3.3-70b-versatile'i emekli ettigi icin sabit model adi
# "model_not_found" hatasi veriyordu. Tercih sirasi asagida tanimlidir.
ALT_MODELS = [m.strip() for m in os.environ.get("ALT_MODELS", "").split(",") if m.strip()]

# Ikinci yedek: Cloudflare Workers AI (ucretsiz katman: gunluk 10.000 neuron;
# Groq'un dar dakikalik token siniri yoktur, bu yuzden buyuk rapor promptu icin
# de uygundur). Anahtar: dash.cloudflare.com -> My Profile -> API Tokens
# (Workers AI izinli olmali). Gerekli secret'lar: CF_API_KEY ve CF_ACCOUNT_ID.
CF_API_KEY = os.environ.get("CF_API_KEY") or os.environ.get("CLOUDFLARE_API_KEY") or os.environ.get("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_MODELS = [m.strip() for m in os.environ.get("CF_MODELS", "").split(",") if m.strip()]

# Model emekleme durumlarina karsi otomatik secim icin tercih siralari
# (icerik eslesmesiyle bulunur; saglayici tam adlandirmayi degistirse de calisir).
GROQ_MODEL_TERCIH = ["gpt-oss-120b", "llama-4-scout", "llama-4-maverick", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
CF_MODEL_TERCIH = ["llama-3.3-70b-instruct-fp8-fast", "llama-4-scout", "llama-3.3-70b-instruct", "llama-3.1-8b-instruct"]

if not AMD_API_KEY and not ALT_API_KEY and not CF_API_KEY:
    raise SystemExit("AMD_API_KEY, ALT_API_KEY veya CF_API_KEY'den en az biri ayarlanmali!")

# Varsayilan model: 1B parametrelik MiniCPM5-1B karmasik Turkce promptlarda Ingilizce
# ic-konusma uretip talimatlari rapora sicrayabilir ve tekrar dongusune girebilir;
# bu yuzden varsayilan daha guclu bir model. Hafif model gerekirse AMD_MODEL ile secilebilir.
AMD_MODEL = os.environ.get("AMD_MODEL", "DeepSeek-V4-Flash")

# Opsiyonel: virgulle ayrilmis fallback modeller (environment ile kontrol edilebilir)
AMD_FALLBACK_MODELS = [m.strip() for m in os.environ.get("AMD_FALLBACK_MODELS", "Qwen3.8-Flash-Next,MiniCPM5-1B").split(",") if m.strip()]

# Eger VLM (vision-language) modellerini explicit olarak kullanmak isterseniz bu environment'i 1 yapin
AMD_INCLUDE_VLM = os.environ.get("AMD_INCLUDE_VLM", "0") == "1"


def _is_vlm_model(name: str) -> bool:
    n = (name or "").lower()
    return any(tok in n for tok in ("vision", "vlm", "image", "visual"))


# Nihai model listesi: once ana model, sonra fallback modeller (VLM'ler opsiyonel olarak haric tutulur)
AMD_MODEL_LIST = [AMD_MODEL] + [m for m in AMD_FALLBACK_MODELS if m != AMD_MODEL]
if not AMD_INCLUDE_VLM:
    AMD_MODEL_LIST = [m for m in AMD_MODEL_LIST if not _is_vlm_model(m)]

client = OpenAI(
    api_key=AMD_API_KEY,
    base_url="https://developer.amd.com.cn/radeon/api/v1",
    timeout=240.0,
    max_retries=0,
) if AMD_API_KEY else None

alt_client = OpenAI(
    api_key=ALT_API_KEY,
    base_url=ALT_BASE_URL,
    timeout=240.0,
    max_retries=0,
) if ALT_API_KEY else None

cf_client = OpenAI(
    api_key=CF_API_KEY,
    base_url=f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1",
    timeout=240.0,
    max_retries=0,
) if CF_API_KEY and CF_ACCOUNT_ID else None

# Takip edilen BIST30 hisseleri (Guncel liste)
HISSELER = [
    "AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "DSTKF", "EKGYO", "ENKAI",
    "EREGL", "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD", "MGROS",
    "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO",
    "TOASO", "TRALT", "TTKOM", "TUPRS", "VAKBN", "YKBNK"
]

# ---- YEREL VERI DEPOSU (hafiza katmani) ----
DATA_DIR = "data"


def save_daily(category, date_str, data):
    """Veriyi data/<category>/<tarih>.json olarak kaydeder."""
    klasor = os.path.join(DATA_DIR, category)
    os.makedirs(klasor, exist_ok=True)
    with open(os.path.join(klasor, f"{date_str}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_recent(category, gun=30):
    """Son N gunun verisini (en yeni once) listeler."""
    klasor = os.path.join(DATA_DIR, category)
    if not os.path.isdir(klasor):
        return []
    dosyalar = sorted([f for f in os.listdir(klasor) if f.endswith(".json")], reverse=True)
    sonuc = []
    for f in dosyalar[:gun]:
        try:
            with open(os.path.join(klasor, f), encoding="utf-8") as fh:
                sonuc.append({"date": f.replace(".json", ""), "data": json.load(fh)})
        except Exception:
            continue
    return sonuc


# Haber RSS kaynaklari
HABER_KAYNAKLARI = {
    "BloombergHT": "https://www.bloomberght.com/rss",
    "CNN Turk": "https://www.cnnturk.com/feed/rss/ekonomi/news",
    "Haberturk": "https://www.haberturk.com/rss",
    "Ekonomim-Piyasa": "https://www.ekonomim.net/rss/piyasa-10",
    "Ekonomim-Ekonomi": "https://www.ekonomim.net/rss/ekonomi-5",
    "Ekonomim-Sirket": "https://www.ekonomim.net/rss/sirket-12",
}

# Finansla ilgisiz haber basliklarini elemek icin filtre
FINANS_DISI_KELIMELER = [
    "masterchef", "survivor", "on numara", "sayisal loto", "milli piyango",
    "hava durumu", "magazin", "dizi", "burc", "futbol", "mac sonucu",
]


class AgentState(TypedDict):
    news_data: str
    tech_data: str
    tech_prices: dict
    fundamental_data: str
    final_report: str


# Configure basic logging
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("bot")


# ---------- HABER AJANI ----------
def news_agent(state: AgentState):
    logger.info("[Haber Ajani] Finans haberleri toplaniyor...")
    print("[Haber Ajani] Finans haberleri toplaniyor...", flush=True)
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    toplanan = []
    for ad, url in HABER_KAYNAKLARI.items():
        logger.info("[Haber Ajani] Kaynak: %s", ad)
        print(f"[Haber Ajani] Kaynak: {ad}", flush=True)
        try:
            f = feedparser.parse(url)
            for e in f.entries[:5]:
                baslik = e.title
                if any(k.lower() in baslik.lower() for k in FINANS_DISI_KELIMELER):
                    continue
                toplanan.append(f"[{ad}] {baslik}")
        except Exception:
            continue
        time.sleep(1)
    if toplanan:
        save_daily("news", bugun, toplanan[:25])

    # Gecmis haberleri de ekle (hafiza)
    gecmis = load_recent("news", gun=7)
    gecmis_metin = ""
    if len(gecmis) > 1:
        gecmis_metin = "\n[SON 7 GUNUN HABERLERI]\n"
        for g in gecmis[1:]:
            gecmis_metin += f"-- {g['date']}: " + " | ".join(g['data'][:5]) + "\n"

    if not toplanan and not gecmis:
        return {"news_data": "Haber verisi alinamadi."}
    bugun_metin = "\n".join(toplanan[:25]) if toplanan else "(bugun haber alinamadi)"
    return {"news_data": bugun_metin + gecmis_metin}


# ---------- TEKNIK AJAN (hisse fiyatlari toplu cekim) ----------
def technical_agent(state: AgentState):
    logger.info("[Teknik Ajan] BIST hisse fiyatlari toplu olarak cekiliyor (Is Yatirim)...")
    print("[Teknik Ajan] BIST hisse fiyatlari toplu olarak cekiliyor (Is Yatirim)...", flush=True)
    try:
        from isyatirimhisse import fetch_stock_data
    except Exception as e:
        return {"tech_data": f"Hisse verisi alinamadi: {e}"}

    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    simdi = datetime.now(tz)
    bugun = simdi.strftime("%Y-%m-%d")
    bitis = simdi.strftime("%d-%m-%Y")
    baslangic = (simdi - timedelta(days=40)).strftime("%d-%m-%Y")

    satirlar = []
    bugun_fiyatlar = {}

    try:
        # Tum hisselerin verisini tek seferde cekiyoruz
        df = fetch_stock_data(HISSELER, start_date=baslangic, end_date=bitis)
        if df is not None and not df.empty:
            df.columns = [str(c).upper() for c in df.columns]

            kod_kolonu = next((col for col in ["HGDG_HS_KODU", "STOCK_CODE", "SYMBOL", "HIZ"] if col in df.columns), None)
            kapanis_kolonu = next((col for col in ["HGDG_KAPANIS", "KAPANIS", "CLOSE"] if col in df.columns), None)

            if kod_kolonu and kapanis_kolonu:
                for hisse in HISSELER:
                    hisse_df = df[df[kod_kolonu] == hisse]
                    if hisse_df.empty:
                        continue

                    son = hisse_df.iloc[-1]
                    fiyat = son.get(kapanis_kolonu)
                    onceki = hisse_df.iloc[-6][kapanis_kolonu] if len(hisse_df) >= 6 else fiyat

                    if fiyat is not None and not pd.isna(fiyat):
                        fiyat_val = float(fiyat)
                        onceki_val = float(onceki) if onceki is not None and not pd.isna(onceki) else fiyat_val
                        degisim = ((fiyat_val - onceki_val) / onceki_val) * 100 if onceki_val > 0 else 0.0
                        satirlar.append(f"{hisse}: {fiyat_val:.2f} TL (5 gunluk %{degisim:+.2f})")
                        bugun_fiyatlar[hisse] = round(fiyat_val, 2)
    except Exception as ex:
        logger.exception("[Teknik Ajan Hatasi] Toplu veri cekilemedi: %s", ex)
        print(f"[Teknik Ajan Hatasi] Toplu veri cekilemedi: {ex}")

    if bugun_fiyatlar:
        save_daily("prices", bugun, bugun_fiyatlar)

    gecmis = load_recent("prices", gun=30)
    gecmis_metin = ""
    if len(gecmis) > 1:
        gecmis_metin = "\n[SON 30 GUNUN FIYAT GECMISI]\n"
        for g in gecmis[1:]:
            ozet = ", ".join(f"{h}={v}" for h, v in list(g['data'].items())[:5])
            gecmis_metin += f"-- {g['date']}: {ozet}\n"

    if not satirlar:
        return {"tech_data": "Hisse verisi alinamadi.", "tech_prices": {}}
    return {"tech_data": "\n".join(satirlar) + gecmis_metin, "tech_prices": bugun_fiyatlar}


# ---------- TEMEL AJAN (finansal tablolar) ----------
def fundamental_agent(state: AgentState):
    logger.info("[Temel Ajan] Sirket finansal verileri cekiliyor...")
    print("[Temel Ajan] Sirket finansal verileri cekiliyor...", flush=True)
    try:
        from isyatirimhisse import fetch_financials
    except Exception as e:
        return {"fundamental_data": f"Finansal veri alinamadi: {e}"}

    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    ozetler = []
    bu_yil = datetime.now(tz).year
    for sira, hisse in enumerate(HISSELER, 1):
        logger.info("[Temel Ajan] %d/%d: %s", sira, len(HISSELER), hisse)
        print(f"[Temel Ajan] {sira}/{len(HISSELER)}: {hisse}", flush=True)
        try:
            fin = fetch_financials(symbols=[hisse], start_year=bu_yil - 1, end_year=bu_yil, financial_group="1")
            if fin is None or fin.empty:
                continue
            # Hasilat ve net donem kari satirlarini bul
            satir = fin[fin["FINANCIAL_ITEM_CODE"].isin(["1A", "3A"])]
            yil_kolonlari = [c for c in fin.columns if str(c).startswith(str(bu_yil))]
            son_kolon = yil_kolonlari[-1] if yil_kolonlari else None
            if son_kolon is None:
                continue
            ozet = f"{hisse}:"
            for _, r in satir.iterrows():
                deger = r.get(son_kolon)
                if pd.notna(deger):
                    ozet += f" {r['FINANCIAL_ITEM_NAME_TR']}={float(deger)/1e9:.2f} mlyr TL;"
            ozetler.append(ozet)
        except Exception:
            pass
        time.sleep(4)

    if ozetler:
        save_daily("financials", bugun, ozetler)

    if not ozetler:
        return {"fundamental_data": "Finansal veri alinamadi."}
    return {"fundamental_data": "\n".join(ozetler)}


# ---------- LLM CAGRISI ----------
def _looks_degenerate(metin: str) -> bool:
    """Modelin tekrar dongusune girdigi yanitlari yakalamak icin basit sezgisel test.

    Kucuk modeller bazen yanitin sonunda ayni kisa parcayi (orn. "18.45, ") yuzlerce
    kez tekrar ederek token limitine kadar takilir; boyle raporlar elenmelidir.
    """
    if not metin or not metin.strip():
        return True
    kuyruk = metin[-4000:]
    # Ayni 3-40 karakterlik parcanin 15'ten fazla kez ART ARDA gelmesi
    if re.search(r"(.{3,40}?)\1{15,}", kuyruk, re.DOTALL):
        return True
    # Ayni uzun satirin 30'dan fazla kez gecmesi
    satirlar = [s.strip() for s in metin.splitlines() if len(s.strip()) >= 8]
    if satirlar:
        if Counter(satirlar).most_common(1)[0][1] > 30:
            return True
    return False


def _contains_prompt_leak(prompt: str, yanit: str) -> bool:
    """Yanitin icinde prompt'un uzun satirlarindan biri BIREBIR geciyorsa True.

    Kucuk modeller bazen verilen talimati ("Raporu kesinlikle profesyonel...")
    yanita kopyalar. Prompt'un 50+ karakterlik tum satirlari kontrol edilir:
    talimat satirlari bu uzunlukta; veri satirlarinin (haber basligi, fiyat
    satiri) raporda birebir tam haliyle tekrarlanasi pratikte imkansizdir.
    """
    if not yanit:
        return False
    talimatlar = [s.strip() for s in prompt.splitlines() if len(s.strip()) >= 50]
    return any(t in yanit for t in talimatlar)


def _dil_karismis(metin: str) -> bool:
    """Turkce beklenen yanitta Ingilizce ic-konusma baskin mi diye bakar.

    Kucuk modeller bazen Turkce talimata ragmen Ingilizce dusunup Ingilizce
    yazar. Ingilizce fonksiyon kelimeleri baskin ve Turkce kelimeler cok azsa
    yanit bozuk sayilir (Ingilizce finans terimleri gecen normal Turkce raporlar
    esikyi gecmez).
    """
    if not metin:
        return False
    ing = len(re.findall(r"\b(the|and|of|to|has|have|with|for|from|this|that|is|are)\b", metin, re.IGNORECASE))
    turkce = len(re.findall(r"\b(ve|ile|olarak|için|göre|daha|çok|ancak|piyasa|rapor|hisse)\b", metin, re.IGNORECASE))
    return ing >= 8 and ing > turkce * 2


def _havuz_modelleri(saglayici, env_listesi, tercih, etiket):
    """Saglayicinin kullanilabilir modellerini belirler.

    env_listesi doluysa (ALT_MODELS / CF_MODELS ile acik verilmisse) oldugu
    kullanilir; bos ise saglayicinin /models listesi sorgulanir ve tercih
    sirasindaki ilk mevcut modeller secilir — boylece saglayici bir modeli
    emekli ettiginde isim degisikligini elle yapmak gerekmez. Liste de
    alinamazsa tercih sirasi dogrudan denenir.
    """
    if env_listesi:
        return env_listesi
    try:
        mevcut = [m.id for m in saglayici.models.list()]
        secilen = [t for t in tercih if any(t in m for m in mevcut)]
        if secilen:
            logger.info("%s icin mevcut modellerden secilenler: %s", etiket, secilen)
            return secilen
        logger.warning("%s /models listesi bos; tercih sirasi dogrudan denenecek.", etiket)
    except Exception as e:
        logger.warning("[Uyari] %s model listesi alinamadi, tercih sirasiyla denenecek: %s", etiket, e)
    return tercih


def llm_call(prompt, max_deneme=6, fallback_on_fail=True, sirasi=None):
    """Daha saglam LLM cagrisi:
    - cok saglayicili: AMD modelleri + (tanimliysa) yedek saglayici modelleri
      sirayla denenir; bir saglayici tukendiginde digerine otomatik gecilir
    - exponential backoff + jitter
    - concurrency/rate-limit durumunda siradaki modele gecis
    - uretilen yanit tekrar dongusu ve prompt sizmasi acisindan dogrulanir; bozuksa
      diger modele gecilir
    - tum denemeler basarisizsa opsiyonel kismi fallback string doner (raise yerine)

    APIConnectionError (GitHub Actions runner'i ile Cin'de barindirilan .com.cn
    endpoint'i arasinda ara sira yasanan gecici baglanti/routing sorunlari) ve
    APITimeoutError icin daha fazla deneme ve daha uzun bekleme suresi kullaniliyor,
    cunku bunlar genelde birkac dakika icinde kendiliginden duzelen gecici sorunlar.

    max_tokens ust siniri yuksek tutuluyor cunku bu endpoint icin gercek maliyet
    uretilen token sayisina gore hesaplaniyor, ust siniri yuksek tutmanin ek bir
    bedeli yok - sadece yaniti erken kesilmekten koruyor. Gerekirse AMD_MAX_TOKENS
    ortam degiskeni ile daraltilabilir.
    """
    import openai
    import random

    max_tokens = int(os.environ.get("AMD_MAX_TOKENS", "8000"))

    # Deneme sirasi: AMD (ana) -> Cloudflare -> Groq. Raporda Cloudflare Groq'dan
    # once gelir cunku Groq'un ucretsiz katmanindaki dar dakikalik token siniri
    # buyuk promptlarda 429 verir; ozette ise Groq oncelidir (kucuk cagri).
    # sirasi parametresiyle oncelik degistirilebilir.
    sirasi = sirasi or ("AMD", "CF", "YEDEK")
    havuzlar = {
        "AMD": (client, AMD_MODEL_LIST or [AMD_MODEL]),
        "CF": (cf_client, _havuz_modelleri(cf_client, CF_MODELS, CF_MODEL_TERCIH, "Cloudflare") if cf_client else CF_MODELS),
        "YEDEK": (alt_client, _havuz_modelleri(alt_client, ALT_MODELS, GROQ_MODEL_TERCIH, "Groq") if alt_client else ALT_MODELS),
    }
    istekler = []
    for etiket in sirasi:
        saglayici, modeller = havuzlar[etiket]
        if saglayici is not None:
            for m in modeller:
                istekler.append((saglayici, m, etiket))

    if not istekler:
        logger.error("Kullanilabilir LLM saglayicisi yok (AMD_API_KEY / ALT_API_KEY tanimli degil).")
        if fallback_on_fail:
            return "(LLM hizmetine ulaşılamadı — rapor şu an kısmi olarak oluşturuldu veya oluşturulamadı. Daha sonra tekrar deneyin.)"
        raise RuntimeError("Kullanilabilir LLM saglayicisi yok.")

    for deneme in range(1, max_deneme + 1):
        saglayici, model, etiket = istekler[(deneme - 1) % len(istekler)]
        try:
            logger.info("LLM cagrisi: %s model=%s deneme=%d/%d", etiket, model, deneme, max_deneme)

            resp = saglayici.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=max_tokens,
            )
            secim = resp.choices[0]
            icerik = secim.message.content or ""

            if _looks_degenerate(icerik) or _contains_prompt_leak(prompt, icerik) or _dil_karismis(icerik):
                sebep = "tekrar dongusu" if _looks_degenerate(icerik) else ("prompt sizmasi" if _contains_prompt_leak(prompt, icerik) else "Ingilizce karisma")
                logger.warning("%s/%s bozuk yanit uretti (%s); siradaki model denenecek.", etiket, model, sebep)
                print(f"[Uyari] {etiket}/{model} bozuk yanit uretti ({sebep}), siradaki model deneniyor ({deneme}/{max_deneme})...", flush=True)
                time.sleep(2)
                continue

            if getattr(secim, "finish_reason", None) == "length":
                logger.warning("Yanit token limitine takilip erken kesilmis olabilir.")
                print("[Uyari] Yanit token limitine takilip erken kesilmis olabilir.", flush=True)
            return icerik

        except openai.RateLimitError as e:
            bekle = min(10 * deneme, 30)  # 10sn, 20sn, 30sn
            print(f"[Uyari] API hiz siniri ({etiket}/{model}, {type(e).__name__}: {e}). {bekle} sn bekleniyor, tekrar deneniyor ({deneme}/{max_deneme})...", flush=True)
            time.sleep(bekle)
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            bekle = min(20 * deneme, 90)  # 20, 40, 60, 80, 90, 90 sn
            sebep = getattr(e, "__cause__", None) or e
            print(f"[Uyari] Baglanti/zaman asimi sorunu ({etiket}/{model}, {type(e).__name__}: {sebep!r}). {bekle} sn bekleniyor, tekrar deneniyor ({deneme}/{max_deneme})...", flush=True)
            time.sleep(bekle)
        except Exception as e:
            emsg = str(e).lower()

            # Concurrency/model-busy tespiti -> yedek saglayici/modellere hizli gec
            if "concurrency" in emsg or "concurrent" in emsg:
                wait = min(10 * deneme, 60)
                logger.info("Model mesgul gorunuyor, %s sn beklenip siradaki saglayici/model denenecek", wait)
                time.sleep(wait + random.uniform(0, 3))
                continue

            # Baglanti/zaman asimi benzeri durumlar
            if "timeout" in emsg or "read timed out" in emsg or "disconnected" in emsg:
                wait = min(15 * deneme, 120)
                logger.warning("Baglanti/zaman asimi sorununa rastlandi: %s. %s sn bekleniyor, deneme %d/%d", e, wait, deneme, max_deneme)
                time.sleep(wait + random.uniform(0, 5))
                continue

            # Rate limit ya da diger server hatalari
            if "rate" in emsg or "429" in emsg or "rate_limit" in emsg:
                wait = min(10 * deneme, 60)
                logger.warning("Rate limit veya 429 alindi: %s. %s sn bekleniyor, deneme %d/%d", e, wait, deneme, max_deneme)
                time.sleep(wait + random.uniform(0, 3))
                continue

            # Bilinmeyen hata, kaydet ve kisa bekle
            logger.exception("Beklenmeyen hata LLM cagrisinda: %s", e)
            time.sleep(min(10 * deneme, 60))

    # Tum denemeler bitti
    logger.error("LLM cagrisi maksimum deneme sayisinda tamamlanamadi.")
    if fallback_on_fail:
        return "(LLM hizmetine ulaşılamadı — rapor şu an kısmi olarak oluşturuldu veya oluşturulamadı. Daha sonra tekrar deneyin.)"
    raise RuntimeError("API cagrisi maksimum deneme sayisinda da tamamlanamadi.")


# ---------- BAS ANALIST (CIO) ----------
def master_cio_agent(state: AgentState):
    logger.info("[Bas Analist] Rapor sentezleniyor...")
    print("[Bas Analist] Rapor sentezleniyor...", flush=True)
    gecmis_ozetler = load_recent("summaries", gun=14)
    hafiza_metni = ""
    if gecmis_ozetler:
        hafiza_metni = "\n[GECMIS GUNLERIN ANALIZ OZETLERI - HAFIZA]:\n"
        for g in gecmis_ozetler:
            hafiza_metni += f"-- {g['date']}: {g['data'].get('ozet', '')}\n"

    prompt = f"""Sen kıdemli bir Hedge-Fund Portföy Yöneticisi ve Araştırma Direktörüsün. Aşağıdaki GERÇEK verileri kullanarak kurumsal yatırımcılara hitap eden, derinlemesine, profesyonel ve uzun bir BIST 30 Yatırım ve Strateji Raporu kaleme al.
Önceki günlere ait analiz özetlerini dikkatle incele; trendin devam edip etmediğini, önceki önerilerin performansını ve piyasa dinamiklerindeki değişimleri eleştirel bir gözle değerlendir.

[GEÇMİŞ GÜNLERİN ANALİZ ÖZETLERİ - HAFIZA]:
{hafiza_metni}

[GÜNÜN HABERLERİ]:
{state['news_data']}

[TEKNİK VERİLER VE HİSSE FİYATLARI]:
{state['tech_data']}

[TEMEL / FİNANSAL VERİLER]:
{state['fundamental_data']}

Raporu kesinlikle profesyonel bir finansal bülten formatında, her başlığı detaylı ve uzun cümlelerle açıklayarak şu alt başlıklar altında oluştur:

1. YÖNETİCİ ÖZETİ VE PİYASA GENEL BAKIŞI: Günün en kritik gelişmeleri, endeksin genel yönü ve fon yönetiminin temel perspektifi.
2. HABER VE MAKROEKONOMİK DEĞERLENDİRME: Akışların BIST 30 şirketlerine yansımaları, enflasyon, kur ve faiz sarmalının yatırımcı psikolojisine etkisi.
3. TEKNİK DEĞERLENDİRME (Hisse Bazlı): En çok ayrışan, hacim kazanan veya direnç/destek noktalarını test eden lider hisselerin teknik anatomisi.
4. ŞİRKET VE FİNANSAL DEĞERLENDİRME: Temel veriler ışığında şirketlerin karlılık, bilanço yapıları ve rasyo bazlı öne çıkan detayları.
5. RİSK YÖNETİMİ VE STRATEJİ: Kısa vadeli olası aşağı/yukarı yönlü senaryolar ve portföyü koruma kalkanları.
6. ÖNERİLEN PORTFÖY VE TAKTİKSEL DAĞILIM: Haftalık ve aylık bazda model portföy için önerilen hisse ağırlıkları ve bu dağılımın gerekçeleri.

Kurallar: Asla uydurma veri veya rakam ekleme, yalnızca sağlanan gerçek verileri ve geçmiş hafızayı baz al. Raporu zengin finansal terimler kullanarak Türkçe kaleme al."""

    try:
        response = llm_call(prompt)
    except Exception as e:
        logger.exception("LLM call failed in master_cio_agent: %s", e)
        response = "(LLM hizmetine ulaşılamadı — rapor şu an kısmi olarak oluşturuldu veya oluşturulamadı. Daha sonra tekrar deneyin.)"

    # Eger llm_call fallback mesaji donduyse, LLM'e ulasilamadi demektir; makul bir ham-rapor uret
    if isinstance(response, str) and response.startswith("(LLM hizmetine ulaşılamadı"):
        logger.warning("LLM'e ulaşılamadi, kısmi ham rapor döndürülüyor.")
        fallback_report = "GUNLUK RAPOR (LLM KULLANILAMADI)\n\n"
        fallback_report += "HABERLER:\n" + state.get('news_data', '') + "\n\n"
        fallback_report += "TEKNIK VE FIYATLAR:\n" + state.get('tech_data', '') + "\n\n"
        fallback_report += "TEMEL/FINANSAL:\n" + state.get('fundamental_data', '') + "\n\n"
        fallback_report += "(LLM'e ulaşılamadığı için rapor otomatik olarak bu ham verilerin birleşimidir.)"
        return {"final_report": fallback_report}

    return {"final_report": response}


# ---------- OZET AJANI (hafiza indeksleme) ----------
def summary_agent(state: AgentState):
    """Gunun raporunu kisa bir ozete donusturup data/summaries/ altina indeksler.
       Boylece ertesi gunler bu ozetleri okuyarak gecmisi hatirlar."""
    logger.info("[Ozet Ajani] Gunun analizi hafizaya indeksleniyor...")
    print("[Ozet Ajani] Gunun analizi hafizaya indeksleniyor...", flush=True)
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    rapor = state.get("final_report", "")
    if not rapor:
        return {}
    prompt = f"""
   Asagidaki gunluk BIST 30 yatirim raporunu, ileride hafiza olarak kullanilmak uzere 5-8 maddelik kisa bir ozete indir.
   Piyasa yonu, one cikan hisseler, portfoy onerisi (yuzdeler) ve temel riskleri mutlaka icersin. Turkce yaz.

   [RAPOR]:
   {rapor[:6000]}
   """
    # Ozet kucuk bir cagri oldugu icin once ucretsiz yedekler (Groq -> Cloudflare)
    # kullanilir; boylece ana rapor icin AMD'nin gunluk kotasini tuketmez.
    ozet = llm_call(prompt, sirasi=("YEDEK", "CF", "AMD"))
    save_daily("summaries", bugun, {"ozet": ozet})
    return {}


# ---------- DENEME PORTFOYU TAKIBI (Kiyaslamali) ----------
PORTFOLYO_DOSYASI = "portfolio.json"
BASLANGIC_SERMAYE = 100000.0


def load_portfolio():
    if os.path.exists(PORTFOLYO_DOSYASI):
        try:
            with open(PORTFOLYO_DOSYASI, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_portfolio(data):
    with open(PORTFOLYO_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _benchmarks_cek():
    """Kiyaslama icin guncel USDTRY ve gram altin fiyatini birden fazla kaynaktan ceker.

    Donus: (usdtry, gram_altin, kaynak_aciklamasi); alinamayan degerler None doner,
    cagiran taraf son bilinen kur ile tamamlar. Actions runner'larinda Yahoo sik
    engellendigi icin tek kaynaga bagli kalmak yerine TCMB (USD) ve gold-api (ons)
    yedekleri kullanilir; hepsi basarisiz olursa portfoydaki son bilinen kur devreye girer.
    """
    usd = None
    gram = None
    kaynaklar = []
    try:
        import yfinance as yf
        # period 5 gunde + ffill/dropna: son gunun verisi henuz olusmamissa (NaN)
        # bir onceki gecerli deger kullanilir.
        df_bench = yf.download(["USDTRY=X", "GC=F"], period="5d", progress=False)["Close"]
        df_bench = df_bench.ffill().dropna(how="any")
        if not df_bench.empty and "USDTRY=X" in df_bench.columns and "GC=F" in df_bench.columns:
            usd = float(df_bench["USDTRY=X"].iloc[-1])
            ons = float(df_bench["GC=F"].iloc[-1])
            gram = (ons * usd) / 31.1035
            kaynaklar.append("yfinance")
    except Exception as e:
        logger.warning("[Uyari] yfinance kurlari cekilemedi: %s", e)

    if usd is None or gram is None:
        import urllib.request
        import xml.etree.ElementTree as ET
        if usd is None:
            try:
                with urllib.request.urlopen("https://www.tcmb.gov.tr/kurlar/today.xml", timeout=20) as r:
                    kok = ET.fromstring(r.read())
                for c in kok.findall("Currency"):
                    if (c.get("Kod") or c.get("CurrencyCode")) == "USD":
                        usd = float(c.findtext("ForexBuying"))
                        break
                if usd:
                    kaynaklar.append("TCMB(USD)")
            except Exception as e:
                logger.warning("[Uyari] TCMB kurlari cekilemedi: %s", e)
        if gram is None and usd:
            try:
                req = urllib.request.Request("https://api.gold-api.com/price/XAU", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    ons = json.load(r).get("price")
                if ons:
                    gram = (float(ons) * usd) / 31.1035
                    kaynaklar.append("gold-api(ons)")
            except Exception as e:
                logger.warning("[Uyari] gold-api cekilemedi: %s", e)
    return usd, gram, ("+".join(kaynaklar) if kaynaklar else "yok")


def portfolio_agent(state: AgentState):
    logger.info("[Portfoy Ajani] Deneme portfoyu ve kiyaslamalar guncelleniyor...")
    print("[Portfoy Ajani] Deneme portfoyu ve kiyaslamalar guncelleniyor...", flush=True)

    fiyatlar = state.get("tech_prices") or {}
    if not fiyatlar:
        return {"final_report": state.get("final_report", "")}

    p = load_portfolio()
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    yillik_faiz = 0.45

    # Guncel USD ve Gram Altin fiyatlarini cok kaynakli cekelim; alinamazsa
    # portfoydaki son bilinen kur (o da yoksa varsayilan) kullanilir.
    guncel_usd, guncel_gold, kur_kaynagi = _benchmarks_cek()
    if guncel_usd is None or guncel_gold is None:
        son_kur = {}
        if p:
            for g in reversed(p.get("history", [])):
                if g.get("rates"):
                    son_kur = g["rates"]
                    break
            if not son_kur:
                son_kur = p.get("initial_benchmarks", {})
        if guncel_usd is None:
            guncel_usd = son_kur.get("USD") or 35.0
        if guncel_gold is None:
            guncel_gold = son_kur.get("GOLD") or 3000.0
        kur_kaynagi += "+son-bilinen"
    print(f"[Bilgi] Benchmark kurlari (kaynak: {kur_kaynagi}): USDTRY={guncel_usd}, gram altin={guncel_gold}", flush=True)

    if p is None:
        # Ilk gun: esit dagilimli portfoy kur ve baslangic kurlarini kaydet
        hisse_adedi = len(fiyatlar)
        pay = BASLANGIC_SERMAYE / hisse_adedi
        p = {
            "start_date": bugun,
            "initial_capital": BASLANGIC_SERMAYE,
            "initial_prices": {h: fiyatlar[h] for h in fiyatlar},
            "shares": {h: round(pay / fiyatlar[h], 2) for h in fiyatlar},
            "initial_benchmarks": {
                "USD": guncel_usd,
                "GOLD": guncel_gold
            },
            "history": [],
        }

    # Ayni gun tekrar calisilirsa son kaydi guncelle (uzerine yaz)
    if p["history"] and p["history"][-1]["date"] == bugun:
        p["history"].pop()

    # Son bilinen fiyatlar: bugun cesitli sebeplerle (kismi API yaniti, tatil gunu vb.)
    # fiyat gelmeyen hisseler 0 sayilip portfoy degeri sahte bir yokusa dusmesin;
    # bu hisseler icin gecmisten son gecerli fiyat kullanilir.
    son_fiyatlar = dict(p.get("initial_prices", {}))
    for g in p.get("history", []):
        son_fiyatlar.update({h: v for h, v in g.get("prices", {}).items()})
    guncel_fiyatlar = {h: fiyatlar.get(h, son_fiyatlar[h]) for h in p["shares"] if son_fiyatlar.get(h)}

    # Bugunku toplam hisse degeri ve yuzde
    toplam = sum(p["shares"][h] * guncel_fiyatlar[h] for h in guncel_fiyatlar)
    yuzde = ((toplam - BASLANGIC_SERMAYE) / BASLANGIC_SERMAYE) * 100

    dun = p["history"][-1]["total"] if p["history"] else BASLANGIC_SERMAYE
    gunluk_yuzde = ((toplam - dun) / dun * 100) if dun else 0.0

    # Mevduat bilesik faiz hesabi
    baslangic_tarihi = datetime.strptime(p["start_date"], "%Y-%m-%d")
    simdiki_tarih = datetime.strptime(bugun, "%Y-%m-%d")
    gecen_gun = max(1, (simdiki_tarih - baslangic_tarihi).days)
    deposit_degeri = BASLANGIC_SERMAYE * ((1 + yillik_faiz / 365) ** gecen_gun)

    # Baslangictaki kur oranlarina gore bugunku USD ve Altin yatiriminin TL karsiligi
    ilk_usd_kuru = p.get("initial_benchmarks", {}).get("USD", guncel_usd)
    ilk_gold_fiyati = p.get("initial_benchmarks", {}).get("GOLD", guncel_gold)

    usd_degeri = BASLANGIC_SERMAYE * (guncel_usd / ilk_usd_kuru)
    gold_degeri = BASLANGIC_SERMAYE * (guncel_gold / ilk_gold_fiyati)

    p["history"].append({
        "date": bugun,
        "total": round(toplam, 2),
        "pct": round(yuzde, 2),
        "daily_pct": round(gunluk_yuzde, 2),
        "prices": guncel_fiyatlar,
        "rates": {"USD": round(float(guncel_usd), 4), "GOLD": round(float(guncel_gold), 4)},
        "benchmarks": {
            "USD": round(usd_degeri, 2),
            "GOLD": round(gold_degeri, 2),
            "DEPOSIT": round(deposit_degeri, 2)
        }
    })
    save_portfolio(p)

    ozet = f"Deneme Portfoyu ({bugun}): Toplam {round(toplam,2):.2f} TL (Toplam %{yuzde:+.2f}, Mevduat: {round(deposit_degeri,2):.2f} TL, USD Karşılığı: {round(usd_degeri,2):.2f} TL, Altın Karşılığı: {round(gold_degeri,2):.2f} TL)"
    return {"final_report": state.get("final_report", "") + "\n\n[PORTFOY OZETI]\n" + ozet}


workflow = StateGraph(AgentState)
workflow.add_node("news", news_agent)
workflow.add_node("technical", technical_agent)
workflow.add_node("fundamental", fundamental_agent)
workflow.add_node("cio", master_cio_agent)
workflow.add_node("summary", summary_agent)
workflow.add_node("portfolio", portfolio_agent)
workflow.set_entry_point("news")
workflow.add_edge("news", "technical")
workflow.add_edge("technical", "fundamental")
workflow.add_edge("fundamental", "cio")
workflow.add_edge("cio", "summary")
workflow.add_edge("summary", "portfolio")
workflow.add_edge("portfolio", END)
app = workflow.compile()

# ---------- ORTAK SITE TASARIMI ----------
BASE_CSS = """
:root { --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f1f5f9; --card:#ffffff;
       --pos:#047857; --neg:#b91c1c; --accent:#0f766e; --accent-bg:#f0fdfa; }
* { box-sizing:border-box; }
body { margin:0; font-family:"Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif;
      background:var(--bg); color:var(--ink); line-height:1.65; }
a { color:var(--accent); }
.topbar { background:var(--ink); }
.topbar .inner { max-width:1080px; margin:0 auto; padding:14px 20px; display:flex;
                 justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }
.topbar .brand { color:#fff; text-decoration:none; font-weight:700; font-size:18px; }
.topbar nav a { color:#cbd5e1; text-decoration:none; margin-left:20px; font-size:14px; }
.topbar nav a:hover, .topbar nav a.active { color:#fff; }
.wrap { max-width:1080px; margin:0 auto; padding:32px 20px 48px; }
.hero { margin-bottom:26px; }
.hero h1 { margin:0 0 6px; font-size:27px; }
.hero p { margin:0; color:var(--muted); font-size:14.5px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px 24px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:14px; margin:0 0 24px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.stat .label { font-size:11.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.6px; }
.stat .value { font-size:20px; font-weight:700; margin-top:3px; }
.pos { color:var(--pos); font-weight:600; }
.neg { color:var(--neg); font-weight:600; }
table { border-collapse:collapse; width:100%; font-size:14.5px; }
th, td { border-bottom:1px solid var(--line); padding:9px 12px; text-align:left; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }
tr:last-child td { border-bottom:none; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(210px,1fr)); gap:14px; }
.rcard { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px;
         text-decoration:none; color:var(--ink); display:block; transition:border-color .15s, box-shadow .15s; }
.rcard:hover { border-color:var(--accent); box-shadow:0 2px 10px rgba(15,23,42,.08); }
.rcard .date { font-size:16.5px; font-weight:700; display:block; }
.rcard .sub { color:var(--muted); font-size:13px; margin-top:4px; display:block; }
.section-title { font-size:19px; margin:34px 0 14px; padding-left:12px; border-left:4px solid var(--accent); }
.report { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:28px 36px; }
.report h1, .report h2, .report h3 { padding-left:12px; border-left:4px solid var(--accent); line-height:1.35; }
.report h1 { font-size:22px; margin:26px 0 10px; }
.report h2 { font-size:19px; margin:26px 0 10px; }
.report h3 { font-size:16.5px; margin:22px 0 8px; }
.report table { margin:14px 0; border:1px solid var(--line); border-radius:8px; }
.report th { background:#f8fafc; }
.report td, .report th { border:1px solid var(--line); }
.report hr { border:none; border-top:1px solid var(--line); margin:22px 0; }
.report blockquote { margin:14px 0; padding:10px 16px; border-left:4px solid var(--line); color:var(--muted); }
.badge { display:inline-block; background:var(--accent-bg); color:var(--accent); border:1px solid #99f6e4;
         border-radius:999px; padding:3px 12px; font-size:12.5px; font-weight:600; }
.meta { color:var(--muted); font-size:13.5px; margin:8px 0 22px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.footer { text-align:center; color:var(--muted); font-size:12.5px; padding:26px 20px;
          border-top:1px solid var(--line); background:#fff; }
.chart { width:100%; height:auto; display:block; }
@media (max-width:640px) {
  .report { padding:18px 16px; }
  table { font-size:13px; }
  th, td { padding:7px 8px; }
}

@media (max-width: 768px) {
    .interactive-box {
        grid-template-columns: 1fr !important;
    }
}
"""


def _sayfa(title, icerik, aktif="raporlar", kok=""):
    """Tum sayfalar icin ortak iskelet (ust menu + govde + altbilgi)."""
    a_r = ' class="active"' if aktif == "raporlar" else ""
    a_p = ' class="active"' if aktif == "portfoy" else ""
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<!-- Google Tag Manager & Analytics -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),j.async=true;j.src='https://www.googletagmanager.com/gtm.js?id=GTM-XXXXXXX';f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','GTM-XXXXXXX');</script>
<style>{BASE_CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
</head>
<body>
<header class="topbar"><div class="inner">
<a class="brand" href="{kok}index.html">BIST 30 Günlük Raporlar</a>
<nav><a href="{kok}index.html"{a_r}>Raporlar</a><a href="{kok}portfolio.html"{a_p}>Deneme Portföyü</a></nav>
</div></header>

<main class="wrap">
    <!-- Üst Widget Alanı (Canlı Saat, İstanbul Hava Durumu ve Google Çeviri) -->
    <div class="site-widgets" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; background: #f8fafc; padding: 10px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; color: #475569; gap: 15px; border: 1px solid #e2e8f0;">
        <div id="live-clock-weather" style="display: flex; gap: 15px; align-items: center; flex-wrap: wrap;">
            <span id="current-date-time">⏳ Yükleniyor...</span>
            <span id="istanbul-weather">🌤️ İstanbul Hava Durumu...</span>
        </div>
        <div id="google_translate_element"></div>
    </div>

    <!-- Etkileşimli Araçlar (Google Arama ve Gemini Sohbet Kutusu) -->
    <div class="interactive-box" style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px;">
        <div style="background: #ffffff; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <form method="get" action="https://www.google.com/search" target="_blank" style="display: flex; gap: 8px;">
                <input type="hidden" name="q" value="site:borsa-raporlari.onrender.com">
                <input type="text" name="q" placeholder="Google ile sitede ara..." style="flex: 1; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px;">
                <button type="submit" style="background: #047857; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: 600;">Ara</button>
            </form>
        </div>
        <div style="background: #ffffff; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0; display: flex; gap: 8px;">
            <input type="text" id="ai-chat-input" placeholder="Gemini'ye borsa hakkında sor..." style="flex: 1; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px;" onkeypress="if(event.key === 'Enter') askGemini();">
            <button onclick="askGemini()" style="background: #2563eb; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: 600;">Sor</button>
        </div>
    </div>

    <!-- Asıl Sayfa İçeriği -->
    {icerik}
</main>

<footer class="footer">Bilgilendirme amacıyla hazırlanmıştır, yatırım tavsiyesi değildir.<br>Veri kaynakları: İş Yatırım, RSS haber akışları &bull; Analiz: yapay zeka (çok-ajanlı sistem)</footer>

<!-- Widget'ları Çalıştıran JavaScript Kodları (Sayfanın en altına eklenir) -->
<script>
    function updateClock() {{
        const now = new Date();
        const options = {{ timeZone: 'Europe/Istanbul', dateStyle: 'medium', timeStyle: 'medium' }};
        document.getElementById('current-date-time').innerText = '📅 ' + new Intl.DateTimeFormat('tr-TR', options).format(now);
    }}
    setInterval(updateClock, 1000);
    updateClock();

    fetch('https://wttr.in/Istanbul?format=j1')
        .then(response => response.json())
        .then(data => {{
            const current = data.current_condition[0];
            const temp = current.temp_C;
            const desc = current.lang_tr ? current.lang_tr[0].value : current.weatherDesc[0].value;
            document.getElementById('istanbul-weather').innerText = `🌤️ İstanbul: ${{temp}}°C, ${{desc}}`;
        }})
        .catch(err => {{
            document.getElementById('istanbul-weather').innerText = '🌤️ İstanbul: Parçalı Bulutlu';
        }});

    function askGemini() {{
        const query = document.getElementById('ai-chat-input').value;
        if(query.trim()) {{
            window.open(`https://gemini.google.com/app?q=${{encodeURIComponent(query)}}`, '_blank');
        }}
    }}
</script>
<script type="text/javascript">
    function googleTranslateElementInit() {{
        new google.translate.TranslateElement({{pageLanguage: 'tr', includedLanguages: 'en,de,fr,ar,ru', layout: google.translate.TranslateElement.InlineLayout.SIMPLE}}, 'google_translate_element');
    }}
</script>
<script type="text/javascript" src="//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
</body></html>"""


def _renk(deger):
    return "pos" if deger >= 0 else "neg"


def markdown_to_html(metin):
    import re
    # Paragraf icindeki "- " satirlarinin gercek liste olmasi icin bos satir ekle
    metin = re.sub(r"(?<!\n)\n(\s*[-*] )", r"\n\n\1", metin)
    return markdown.markdown(metin, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])


def rapor_sayfasi(html_icerik, date_str):
    icerik = f"""
<div class="hero">
<h1>Günlük Piyasa Raporu</h1>
<div class="meta"><span class="badge">{date_str}</span><span>BIST 30 &bull; Yapay zeka destekli günlük analiz</span></div>
</div>
<article class="report">{html_icerik}</article>
<p style="margin-top:18px"><a href="../index.html">&larr; Tüm raporlara dön</a></p>"""
    return _sayfa(f"Piyasa Raporu - {date_str}", icerik, "raporlar", kok="../")


def build_html(report, date_str):
    return rapor_sayfasi(markdown_to_html(report), date_str)


def sparkline_svg(history):
    """Portfoy gecmisini Chart.js ile karsilastirmali cizgi grafige donusturur.

    Onceki elle-cizilen SVG yerine hazir bir grafik kutuphanesi (Chart.js,
    CDN uzerinden _sayfa()'nin <head> kismina ekleniyor) kullaniliyor.
    Bunun getirdigi avantajlar:
      - Excel/Office tarzi duzgun eksen, izgara ve yumusatilmis cizgiler.
      - Dokunmatik ekranlarda (mobil) tooltip native olarak calisiyor;
        Chart.js touchstart/touchmove olaylarini kendisi dinliyor, ozel
        bir tiklama mantigi yazmaya gerek kalmadi.
      - Tooltip icinde hem gunun degeri hem de baslangictan bugune %
        degisim otomatik hesaplanip gosteriliyor.
    """
    if not history:
        return ""

    etiketler = [item["date"] for item in history]
    stocks = [round(float(item.get("total", 0)), 2) for item in history]
    benchmarks = [item.get("benchmarks") or {} for item in history]
    golds = [round(float(item.get("GOLD", stocks[i])), 2) for i, item in enumerate(benchmarks)]
    usds = [round(float(item.get("USD", stocks[i])), 2) for i, item in enumerate(benchmarks)]
    deposits = [round(float(item.get("DEPOSIT", stocks[i])), 2) for i, item in enumerate(benchmarks)]

    import random
    import json as _json
    grafik_id = f"portfoy-grafik-{random.randint(100000, 999999)}"

    veri = {
        "labels": etiketler,
        "datasets": [
            {"label": "Deneme Portföyü (Hisseler)", "data": stocks, "borderColor": "#047857", "backgroundColor": "#047857"},
            {"label": "Altın", "data": golds, "borderColor": "#d97706", "backgroundColor": "#d97706"},
            {"label": "Dolar", "data": usds, "borderColor": "#2563eb", "backgroundColor": "#2563eb"},
            {"label": "Mevduat", "data": deposits, "borderColor": "#94a3b8", "backgroundColor": "#94a3b8", "borderDash": [6, 4]},
        ],
    }
    veri_json = _json.dumps(veri, ensure_ascii=False)

    return f"""
<div style="position:relative; height:340px;">
<canvas id="{grafik_id}"></canvas>
</div>
<script>
(function() {{
    var veri = {veri_json};
    var baslangiclar = veri.datasets.map(function(d) {{ return d.data[0]; }});
    var ctx = document.getElementById('{grafik_id}').getContext('2d');
    new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: veri.labels,
            datasets: veri.datasets.map(function(d) {{
                return Object.assign({{}}, d, {{
                    borderWidth: 3,
                    pointRadius: 2,
                    pointHitRadius: 14,
                    tension: 0.15,
                    fill: false,
                }});
            }})
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {{ mode: 'nearest', intersect: false }},
            plugins: {{
                legend: {{ position: 'top', labels: {{ boxWidth: 12, font: {{ size: 12, weight: '600' }} }} }},
                tooltip: {{
                    callbacks: {{
                        label: function(ctx2) {{
                            var idx = ctx2.datasetIndex;
                            var ilk = baslangiclar[idx];
                            var son = ctx2.parsed.y;
                            var yuzde = ilk ? ((son - ilk) / ilk * 100) : 0;
                            var isaret = yuzde >= 0 ? '+' : '';
                            var sonStr = son.toLocaleString('tr-TR', {{maximumFractionDigits: 2}});
                            return ctx2.dataset.label + ': ' + sonStr + ' TL (' + isaret + yuzde.toFixed(2) + '%)';
                        }}
                    }}
                }}
            }},
            scales: {{
                y: {{ ticks: {{ callback: function(v) {{ return v.toLocaleString('tr-TR') + ' TL'; }} }} }},
                x: {{ ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }} }}
            }}
        }}
    }});
}})();
</script>
"""


def _portfoy_satirlari(p):
    son = p["history"][-1]
    satirlar = []
    for h in p.get("shares", {}):
        ilk = p["initial_prices"].get(h)
        guncel = son["prices"].get(h, ilk)
        if ilk and guncel:
            fark = ((guncel - ilk) / ilk) * 100
            satirlar.append(
                f"<tr><td><strong>{h}</strong></td><td>{p['shares'][h]:,.2f}</td>"
                f"<td>{ilk:.2f} TL</td><td>{guncel:.2f} TL</td>"
                f"<td class='{_renk(fark)}'>{fark:+.2f}%</td></tr>"
            )
    return "".join(satirlar)


def _portfoy_istatistikleri(p):
    son = p["history"][-1]
    return f"""
<div class="stats">
<div class="stat"><div class="label">Güncel Değer</div><div class="value">{son['total']:,.0f} TL</div></div>
<div class="stat"><div class="label">Günlük Değişim</div><div class="value {_renk(son['daily_pct'])}">{son['daily_pct']:+.2f}%</div></div>
<div class="stat"><div class="label">Toplam Getiri</div><div class="value {_renk(son['pct'])}">{son['pct']:+.2f}%</div></div>
<div class="stat"><div class="label">Başlangıç</div><div class="value" style="font-size:16px">{p['start_date']}</div></div>
</div>"""


def build_index_html(p, rapor_dosyalari):
    if rapor_dosyalari:
        kartlar = "".join(
            f'<a class="rcard" href="reports/{fn}"><span class="date">{fn[:-5]}</span>'
            f'<span class="sub">Günlük raporu aç &rarr;</span></a>'
            for fn in rapor_dosyalari
        )
    else:
        kartlar = '<p style="color:var(--muted)">Henüz rapor yok.</p>'

    portfoy_bolumu = ""
    if p and p.get("history"):
        grafik = sparkline_svg(p["history"])
        grafik_html = f'<div class="card" style="margin-bottom:14px">{grafik}</div>' if grafik else ""
        portfoy_bolumu = f"""
<h2 class="section-title">Deneme Portföyü</h2>
{_portfoy_istatistikleri(p)}
{grafik_html}
<div class="card" style="padding:8px 24px 16px">
<table><tr><th>Hisse</th><th>Adet</th><th>İlk Alım</th><th>Güncel</th><th>Getiri</th></tr>{_portfoy_satirlari(p)}</table>
<p style="margin:12px 0 4px"><a href="portfolio.html">Detaylı portföy geçmişi &rarr;</a></p>
</div>"""

    icerik = f"""
<div class="hero">
<h1>BIST 30 Günlük Piyasa Raporları</h1>
<p>Her sabah 08:00'de otomatik üretilen, yapay zeka destekli BIST 30 analizleri ve sanal portföy takibi.</p>
</div>
<h2 class="section-title">Rapor Arşivi</h2>
<div class="grid">{kartlar}</div>
{portfoy_bolumu}"""
    return _sayfa("BIST 30 Günlük Raporlar", icerik, "raporlar")


def build_portfolio_html(p):
    son = p["history"][-1]
    grafik = sparkline_svg(p["history"])
    grafik_html = f'<div class="card" style="margin-bottom:22px">{grafik}</div>' if grafik else ""
    gecmis = "".join(
        f"<tr><td>{g['date']}</td><td>{g['total']:,.2f} TL</td>"
        f"<td class='{_renk(g['pct'])}'>{g['pct']:+.2f}%</td>"
        f"<td class='{_renk(g['daily_pct'])}'>{g['daily_pct']:+.2f}%</td></tr>"
        for g in reversed(p["history"])
    )
    icerik = f"""
<div class="hero">
<h1>Deneme Portföyü</h1>
<p>BIST 30 hisselerine eşit dağıtılmış {p['initial_capital']:,.0f} TL'lik sanal portföy. Alım-satım yapılmaz, sadece takip edilir.</p>
</div>
{_portfoy_istatistikleri(p)}
{grafik_html}
<h2 class="section-title">Hisse Performansı (ilk alım vs güncel)</h2>
<div class="card" style="padding:8px 24px 16px">
<table><tr><th>Hisse</th><th>Adet</th><th>İlk Alım</th><th>Güncel</th><th>Getiri</th></tr>{_portfoy_satirlari(p)}</table>
</div>
<h2 class="section-title">Günlük Geçmiş</h2>
<div class="card" style="padding:8px 24px 16px">
<table><tr><th>Tarih</th><th>Toplam Değer</th><th>Toplam %</th><th>Günlük %</th></tr>{gecmis}</table>
</div>"""
    return _sayfa("Deneme Portföyü", icerik, "portfoy")


if __name__ == "__main__":
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime('%Y-%m-%d')
    result = app.invoke({"news_data": "", "tech_data": "", "tech_prices": {}, "fundamental_data": "", "final_report": ""})
    report = result["final_report"]

    os.makedirs("reports", exist_ok=True)
    with open(f"reports/{date_str}.html", "w", encoding="utf-8") as f:
        f.write(build_html(report, date_str))

    raporlar = sorted((fn for fn in os.listdir("reports") if fn.endswith(".html")), reverse=True)
    p = load_portfolio()
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_index_html(p, raporlar))
    if p and p.get("history"):
        with open("portfolio.html", "w", encoding="utf-8") as f:
            f.write(build_portfolio_html(p))

    print("RAPOR OLUSTURULDU:", date_str)
