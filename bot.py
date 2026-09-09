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
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_MODELS = [m.strip() for m in os.environ.get("CF_MODELS", "").split(",") if m.strip()]

# Ucuncu yedek: OpenRouter (ucretsiz modeller ":free" ekli olur; fonlansiz
# hesapta ~50 istek/gun). Anahtar: https://openrouter.ai/settings/keys
OR_API_KEY = os.environ.get("OR_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
OR_BASE_URL = os.environ.get("OR_BASE_URL", "https://openrouter.ai/api/v1")
OR_MODELS = [m.strip() for m in os.environ.get("OR_MODELS", "").split(",") if m.strip()]

# Model emekleme durumlarina karsi otomatik secim icin tercih siralari
# (icerik eslesmesiyle bulunur; saglayici tam adlandirmayi degistirse de calisir).
GROQ_MODEL_TERCIH = ["gpt-oss-120b", "llama-4-scout", "llama-4-maverick", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
CF_MODEL_TERCIH = ["llama-3.3-70b-instruct-fp8-fast", "llama-4-scout", "llama-3.3-70b-instruct", "llama-3.1-8b-instruct"]
OR_MODEL_TERCIH = ["nemotron-3-ultra", "nemotron-3.5-lightning", "nemotron-3-super", "gemma-4-31b", "ling-3.0-flash-fin", "inkling"]  # 2026-09 ucretsiz kadro: nemotron3 ailesi + gemma4 + ling-fin

if not AMD_API_KEY and not ALT_API_KEY and not CF_API_KEY and not OR_API_KEY:
    raise SystemExit("AMD_API_KEY, ALT_API_KEY, CF_API_KEY veya OR_API_KEY'den en az biri ayarlanmali!")

# Varsayilan model: 1B parametrelik MiniCPM5-1B karmasik Turkce promptlarda Ingilizce
# ic-konusma uretip talimatlari rapora sicrayabilir ve tekrar dongusune girebilir;
# bu yuzden varsayilan daha guclu bir model. Hafif model gerekirse AMD_MODEL ile secilebilir.
AMD_MODEL = os.environ.get("AMD_MODEL", "DeepSeek-V4-Flash")

# Opsiyonel: virgulle ayrilmis fallback modeller (environment ile kontrol edilebilir)
AMD_FALLBACK_MODELS = [m.strip() for m in os.environ.get("AMD_FALLBACK_MODELS", "Qwen3.8-Flash-Next").split(",") if m.strip()]  # MiniCPM5-1B cikarildi: Turkce raporu tasiyamiyor, talimat eko + Ingilizce karistirma yapiyor

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

or_client = OpenAI(
    api_key=OR_API_KEY,
    base_url=OR_BASE_URL,
    timeout=240.0,
    max_retries=0,
) if OR_API_KEY else None

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
    portfoy_ozet: str


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
    # Tablo sablonlari muaf: modele "bu tabloyu olustur" dedigimiz satirlari
    # modelin geri yazmasi istenen davranistir, sizma degildir.
    talimatlar = [t for t in talimatlar if not t.startswith("|") and not set(t) <= set("|- ")]
    for t in talimatlar:
        if t in yanit:
            return True
        # Birebir eslesme tirnak/kesme ile bozulursa: talimatin ilk 60 karakterlik
        # on eki yanitin acilisinda geciyorsa yine sizma sayilir.
        if len(t) >= 60 and t[:60] in yanit[:500]:
            return True
    return False


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


def _cf_modelleri():
    """Cloudflare Workers AI model listesini resmi endpointten ceker.

    Dikkat: Cloudflare'in OpenAI-uyumlu katmaninda /ai/v1/models YOKTUR;
    dogru liste endpoint'i /ai/models/search'tur. Donen isimler (@cf/... on
    ekli) ayni zamanda cagrida kullanilan tam model kimlikleridir.
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/models/search?per_page=100&task=Text%20Generation",
            headers={"Authorization": f"Bearer {CF_API_KEY}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        return [m["name"] for m in d.get("result", []) if m.get("name")]
    except Exception as e:
        logger.warning("[Uyari] Cloudflare model listesi alinamadi (%s), tercih sirasi kullanilacak: %s", "models/search", e)
        return []


def _cf_havuz():
    """Cloudflare model havuzu: env ile acik liste verilmisse onu kullan;
    yoksa /ai/models/search listesinden tercih sirasina gore sec."""
    if CF_MODELS:
        return CF_MODELS
    mevcut = _cf_modelleri()
    # Tercih SIRASINA gore diz: (models/search listesi gelisiguzel sirali gelir;
    # 70B modellerin 8B'lerin onune gecmesi icin tercih döngusu ile kur)
    secilen = []
    for t in CF_MODEL_TERCIH:
        secilen.extend(m for m in mevcut if t in m)
    secilen = list(dict.fromkeys(secilen))
    if secilen:
        logger.info("Cloudflare icin mevcut modellerden secilenler: %s", secilen)
        return secilen
    return CF_MODEL_TERCIH


def _havuz_modelleri(saglayici, env_listesi, tercih, etiket, suzgec=None):
    """Saglayicinin kullanilabilir modellerini belirler.

    env_listesi doluysa (ALT_MODELS / CF_MODELS / OR_MODELS ile acik
    verilmisse) oldugu kullanilir; bos ise saglayicinin /models listesi
    sorgulanir ve tercih sirasindaki ilk mevcut modeller secilir — boylece
    saglayici bir modeli emekli ettiginde isim degisikligini elle yapmak
    gerekmez. Liste de alinamazsa tercih sirasi dogrudan denenir.
    suzgec: model kimligine uygulanacak ek filtre (orn. OpenRouter'da yalnizca
    ":free" ekli modeller).
    """
    if env_listesi:
        return env_listesi
    try:
        mevcut = [m.id for m in saglayici.models.list()]
        if suzgec:
            mevcut = [m for m in mevcut if suzgec(m)]
        secilen = []
        for t in tercih:
            secilen.extend(m for m in mevcut if t in m)
        # Sirayi koruyarak tekillestir ve TAM kimlikleri dondur — Groq gibi
        # saglayicilarda gercek kimlik "openai/gpt-oss-120b" tarzinda on ekli
        # olabilir; tercih alt dizesini gondermek 404 model_not_found verir.
        secilen = list(dict.fromkeys(secilen))
        if secilen:
            logger.info("%s icin mevcut modellerden secilenler: %s", etiket, secilen)
            return secilen
        logger.warning("%s /models listesi bos veya tercihlerle eslesmedi; tercih sirasi dogrudan denenecek.", etiket)
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
    sirasi = sirasi or ("AMD", "CF", "YEDEK", "OR")
    havuzlar = {
        "AMD": (client, AMD_MODEL_LIST or [AMD_MODEL]),
        "CF": (cf_client, _cf_havuz() if cf_client else CF_MODELS),
        "YEDEK": (alt_client, _havuz_modelleri(alt_client, ALT_MODELS, GROQ_MODEL_TERCIH, "Groq") if alt_client else ALT_MODELS),
        "OR": (or_client, _havuz_modelleri(or_client, OR_MODELS, OR_MODEL_TERCIH, "OpenRouter",
                                           suzgec=lambda m: m.endswith(":free")) if or_client else OR_MODELS),
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
# ---------- Z.AI CAGIRICI (gunluk rapor icin buyuk GLM modeli) ----------
def _zai_call(prompt):
    """Kullanicinin ZAI_API_KEY secret'iyla GLM'i cagirir; kaliteli/uzun
    gunluk rapor bu saglayicidan yazilir. Anahtar yoksa veya iki model de
    basarisiz olursa None doner (cagiran taraf mevcut llm_call zincirine duser).
    Cikti degenerasyon ve prompt sizmasi acisindan da dogrulanir."""
    anahtar = os.environ.get("ZAI_API_KEY", "")
    if not anahtar:
        return None
    client = OpenAI(api_key=anahtar, base_url="https://api.z.ai/api/paas/v4/",
                    timeout=300.0, max_retries=1)
    modeller = [(os.environ.get("ZAI_MODEL") or "glm-4.7-flash"), "glm-4.5-flash"]
    son_hata = None
    for mdl in modeller:
        for deneme in range(2):  # 429/asiri yuk icin ayni modeli bekleyip tekrar dene
            try:
                logger.info("[Z.ai] rapor cagrisi: %s (deneme %d)", mdl, deneme + 1)
                resp = client.chat.completions.create(
                    model=mdl,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=8000,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                icerik = resp.choices[0].message.content or ""
                if not icerik.strip():
                    son_hata = "bos yanit"
                    continue
                if _looks_degenerate(icerik) or _contains_prompt_leak(prompt, icerik) or _dil_karismis(icerik):
                    son_hata = "bozuk yanit (dongu/sizma/dil)"
                    logger.warning("[Z.ai] %s bozuk yanit uretti; siradaki deneniyor.", mdl)
                    break
                logger.info("[Z.ai] rapor alindi (%s): %d karakter", mdl, len(icerik))
                return icerik
            except Exception as e:
                son_hata = str(e)[:200]
                logger.warning("[Z.ai] %s basarisiz: %s", mdl, son_hata)
                if "429" in son_hata or "1305" in son_hata or "overloaded" in son_hata.lower():
                    time.sleep(30)
                    continue
                time.sleep(2)
                break
    logger.warning("[Z.ai] anahtarli cagri basarisiz (%s); yedek zincire dusuluyor.", son_hata)
    return None


# ---------- RAPOR SON ISLEMCISI ----------
# LLM cikisinda tekrar tekrar gorulen sorunlari otomatik duzeltir:
#   1. Rapor basina eklenen "Tarih:/Yayinci:/Konu:" kimlik satirlari (site
#      sablonu tarihi zaten gosteriyor; LLM'nin uydurdugu tarih yanlis olabiliyor).
#   2. Koseli parantezli yer tutucular: [Hedge-Fund Yonetimi], [PORTFOY OZETI]...
#   3. Numarali BUYUK HARF bolum basliklarinin markdown ## basligina cevrilmesi
#      (gunluk rapor sayfalarinda h2 yapisi boyle olusur).
#   4. ASCII'ye dusmus Turkce kelimeler (Ozeti -> Ozeti degil, Özeti) ve
#      sik yazim hatalari (sinyiller -> sinyaller).
_TR_DUZELTME = {
    # bolum basligi kelimeleri (ASCII -> dogru Turkce)
    "Ozeti": "Özeti", "Bakis": "Bakış", "Sektor": "Sektör", "Bazli": "Bazlı",
    "Degerlendirme": "Değerlendirme", "Gorsel": "Görsel", "Osilator": "Osilatör",
    "Okumalari": "Okumaları", "Haritasi": "Haritası", "Gunun": "Günün",
    "Onerilen": "Önerilen", "Giris": "Giriş", "Bolgesi": "Bölgesi",
    "Gerekce": "Gerekçe", "Asiri": "Aşırı", "Alim": "Alım", "Satim": "Satım",
    "Yatirim": "Yatırım", "Portfoy": "Portföy", "Portfoyu": "Portföyü",
    # sik tekrarlayan yazim hatalari (LLM cikisi)
    "sinyiller": "sinyaller", "Sinyiller": "Sinyaller", "sinyil": "sinyal",
    "Ayrisan": "Ayrışan", "ayrisan": "ayrışan", "Nötür": "Nötr",
    "GÜCLÜ": "GÜÇLÜ", "güclü": "güçlü",
}


def _turkce_karakter_duzelt(metin: str) -> str:
    for yanlis, dogru in _TR_DUZELTME.items():
        if yanlis == dogru:
            continue
        metin = re.sub(r"\b" + re.escape(yanlis) + r"\b", dogru, metin)
    return metin


def _tarih_gun_duzelt(metin: str) -> str:
    """Metindeki '7 Eylül 2026 Pazar' gibi TARIH + GUN ADI eslesmelerini denetler;
    gun adi gercek takvimle uyusmuyorsa dogrusuyla degistirir. LLM'ler tarih-gun
    eslemesinde sik hata yapar (orn. 7 Eylül 2026'yı Pazar sanmak); bu fonksiyon
    yayindan once ve gecmis sayfalarin temizliginde guvenle calistirilir."""
    aylar = {a: i for i, a in enumerate(_AYLAR, start=1)}

    def _duzelt(m):
        gun, ay_adi, yil, gun_adi = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        try:
            dogru = _GUN_ADLARI[datetime(yil, aylar[ay_adi], gun).weekday()]
        except ValueError:
            return m.group(0)  # gecersiz tarih (31 Subat vb.) -> dokunma
        if gun_adi == dogru:
            return m.group(0)
        bas = m.group(0)[:m.start(4) - m.start(0)]
        son = m.group(0)[m.end(4) - m.start(0):]
        return bas + dogru + son

    return re.sub(
        r"\b(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)"
        r"\s+(\d{4})\s*,?\s*(Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar)\b",
        _duzelt,
        metin,
    )


def rapor_son_islem(metin: str) -> str:
    """Yayina girmeden once LLM raporunu temizler (bkz. yukaridaki liste)."""
    if not metin:
        return metin
    satirlar = metin.split("\n")
    temiz = []
    for i, satir in enumerate(satirlar):
        s = satir.strip()
        ust = "\n".join(x.strip() for x in satirlar[max(0, i - 2):i])
        # Kimlik satirlari: raporun ilk ~6 satirindaki Tarih/Yayinci/Konu/rapor-adi
        if re.match(r"^(Tarih|Yayıncı|Yayınlayan|Konu|Hazırlayan|Analist)\s*[:：]", s):
            if "YÖNETİCİ" not in ust and "1." not in ust.split("\n")[-1:]:
                temiz.append(None)
                continue
        if re.match(r"^BIST\s*30\s+((HAFTALIK|GÜNLÜK)\s+)?(YATIRIM|STRATEJİ|RAPOR)", s, re.IGNORECASE) and len(s) < 70:
            if not any("##" in t for t in temiz if t):
                temiz.append(None)
                continue
        # Kurum/kisi unvani taklidi iceren bagimsiz satirlar (kişi veya kurum kimligi)
        # Guvenlik: satir noktalama icermeyen kisa bir baslik gorunumunde OLMALI ve
        # bilinen unvan kalibiyla BITMELI (prose cumleler asla silinmez).
        k2 = re.sub(r"^[\s*\-#>]*", "", s).lower().rstrip(".;:,")
        unvan_kaliplari = (
            "hedge-fund portföy yönetimi & araştırma direktörlüğü",
            "hedge-fund portföy yönetimi",
            "hedge-fund araştırma direktörlüğü",
            "kıdemli portföy yöneticisi",
            "portföy yöneticisi ve araştırma direktörü",
            "araştırma direktörlüğü",
        )
        if (
            len(k2) <= 70
            and "." not in k2
            and "," not in k2
            and any(k2.endswith(u) for u in unvan_kaliplari)
        ):
            temiz.append(None)
            continue
        # Koseli parantezli yer tutucu bloklari ([PORTFOY OZETI], [Hedge-Fund ...] satiri vb.)
        if re.match(r"^\[.+\]$", s) or re.match(r"^\[(PORTFOY|PORTFÖY|HEDGE|NOT|KAYNAK)", s, re.IGNORECASE):
            # [...] etiketinin hemen ardindaki ham pipeline satiri da (varsa) birlikte dusur
            j = i + 1
            while j < len(satirlar) and not satirlar[j].strip():
                j += 1
            if j < len(satirlar) and re.match(r"^Deneme\s+Portf[öo]y[üu]?\s*\(20\d\d", satirlar[j].strip()):
                satirlar[j] = ""
            temiz.append(None)
            continue
        temiz.append(satir)
    metin = "\n".join(t for t in temiz if t is not None)
    # Ardik bos satirlari tek bos satira indir
    metin = re.sub(r"\n{3,}", "\n\n", metin)
    # Numarali BUYUK HARF bolumleri (harf icermeyen kucuk harf satirlari) ## basligi yap
    def _baslik_yap(m):
        baslik = m.group(0).strip()
        return "## " + baslik
    metin = re.sub(
        r"^\s*\d+\.\s+[A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9\s&/().,:'-]+$",
        _baslik_yap,
        metin,
        flags=re.MULTILINE,
    )
    metin = _turkce_karakter_duzelt(metin)
    metin = _tarih_gun_duzelt(metin)  # "7 Eylül 2026 Pazar" gibi yanlis gun adlarini duzelt
    return metin.strip()


def master_cio_agent(state: AgentState):
    logger.info("[Bas Analist] Rapor sentezleniyor...")
    print("[Bas Analist] Rapor sentezleniyor...", flush=True)
    gecmis_ozetler = load_recent("summaries", gun=14)
    hafiza_metni = ""
    if gecmis_ozetler:
        hafiza_metni = "\n[GECMIS GUNLERIN ANALIZ OZETLERI - HAFIZA]:\n"
        for g in gecmis_ozetler:
            hafiza_metni += f"-- {g['date']}: {g['data'].get('ozet', '')}\n"

    prompt = f"""Sen Türkiye piyasalarında uzmanlaşmış bağımsız bir finansal analist yapay zekâsısın (gerçek bir kişi veya kurum değilsin; kendini öyle tanıtma). Aşağıdaki GERÇEK verileri kullanarak profesyonel okuyucuya hitap eden, derinlemesine ve uzun bir BIST 30 Yatırım ve Strateji Raporu kaleme al.
Önceki günlere ait analiz özetlerini dikkatle incele; trendin devam edip etmediğini, önceki önerilerin performansını ve piyasa dinamiklerindeki değişimleri eleştirel bir gözle değerlendir.

[GEÇMİŞ GÜNLERİN ANALİZ ÖZETLERİ - HAFIZA]:
{hafiza_metni}

[GÜNÜN HABERLERİ]:
{state['news_data']}

[TEKNİK VERİLER VE HİSSE FİYATLARI]:
{state['tech_data']}

[TEMEL / FİNANSAL VERİLER]:
{state['fundamental_data']}

Raporu kesinlikle profesyonel bir finansal bülten formatında, her başlığı detaylı ve uzun cümlelerle açıklayarak şu alt başlıklar altında oluştur (her başlık "## " ile başlayan markdown başlığı olarak yazılacak):

## 1. Yönetici Özeti ve Piyasa Genel Bakışı: Günün en kritik gelişmeleri, endeksin genel yönü ve fon yönetiminin temel perspektifi.
## 2. Haber ve Makroekonomik Değerlendirme: Akışların BIST 30 şirketlerine yansımaları, enflasyon, kur ve faiz sarmalının yatırımcı psikolojisine etkisi.
## 3. Teknik Değerlendirme (Hisse Bazlı): En çok ayrışan, hacim kazanan veya direnç/destek noktalarını test eden lider hisselerin teknik anatomisi. Aşağıdaki SİNYAL TABLOSU verilerini mutlaka kullan.
## 4. Şirket ve Finansal Değerlendirme: Temel veriler ışığında şirketlerin karlılık, bilanço yapıları ve rasyo bazlı öne çıkan detayları.
## 5. Risk Yönetimi ve Strateji: Kısa vadeli olası aşağı/yukarı yönlü senaryolar ve portföyü koruma kalkanları.
## 6. Önerilen Model Portföy: Raporun SONUNDA, yukarıdaki sinyal ve analizlere DAYANARAK kendinin kurduğu somut bir model portföyü tablosu oluştur. Tablo şu sütunlarla olmalı:

| Hisse | Sektör | Ağırlık (%) | İşlem | Giriş Bölgesi | Hedef-1 | Hedef-2 (3 Ay) | Stop | Gerekçe |
|-------|--------|-------------|-------|---------------|---------|----------------|------|---------|

Tablo kuralları: En fazla 8 hisse pozisyonu + bir "NAKİT" satırı ekle; ağırlıklar %100'ü tamamlamalı (nakit dahil). Sadece AL/GÜÇLÜ AL sinyali veren ve gerekçesi verilerle desteklenen hisseleri seç; ağırlığı sinyal gücü, Pearson (r) ve kanal konumuna göre belirle. Giriş bölgesi, hedefler ve stop seviyelerini SADECE sağlanan gerçek fiyatlardan türet (kanal bantları ve son fiyat baz alın); dışarıdan hiçbir veri ekleme. Her satırın gerekçesi teknik + osilatör gerekçelerini birleştirsin.

Biçim kuralları (zorunlu):
- Rapor doğrudan "## 1." başlığıyla başlayacak; RAPOR ADI, Tarih, Yayıncı, Konu gibi kimlik satırları EKLEME (site şablonu tarihi zaten gösteriyor, yanlış tarihe düşme riski yaratma).
- Metinde köşeli parantezli [...] yer tutucu veya iç not kullanma.
- Kimlik satırı YAZMA: "Hedge-Fund", "Direktör", "Portföy Yöneticisi", "Analist:", "Yayıncı:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri ve tarih ya da haftanın gün adı raporda GEÇMEYECEK (tarih-gün eşleştirmesinde sık hata yapıyorsun; şablon zaten tarihi gösteriyor).
- TÜM metinde doğru Türkçe karakterler kullan (ç, ğ, ı, i, ö, ş, ü); "sinyal" gibi kelimeleri yanlış yazma ("sinyil" DEĞİL).
- 5. bölümdeki nakit/likidite önerisi ile 6. bölümdeki NAKİT satırının ağırlığı ÇELİŞMEMELİ (örn. "%40 nakit tutun" deyip %0 nakitlik portföy verme).

Kurallar: Asla uydurma veri veya rakam ekleme, yalnızca sağlanan gerçek verileri ve geçmiş hafızayı baz al. Raporu zengin finansal terimler kullanarak Türkçe kaleme al."""

    # Gunun sinyal tablolari (master_cio calismadan once taze taramalar alinir)
    try:
        t_satirlar = teknik_tarama_yap()
        b_satirlar = borsapy_analiz_yap()
    except Exception as e:
        logger.warning("[Bas Analist] sinyal taramalari alinamadi: %s", e)
        t_satirlar, b_satirlar = [], []
    sinyaller = ""
    if t_satirlar:
        sinyaller += "\n[TEKNIK TARAMA SINYALLERI - BUGUN]\n" + "\n".join(
            f"{s['hisse']} ({s['sektor']}): son {s['son']} TL, gunluk {s['gunluk']:+.2f}%, 60g {s['deg60']:+.1f}%, "
            f"kisa={s['kisa']} orta={s['orta']} uzun={s['uzun']}, WT={s['wt']}, kanal %{s['konum']:.0f}, "
            f"r={s['r']:.2f}, genel={s['genel']}"
            for s in t_satirlar
        )
    if b_satirlar:
        sinyaller += "\n\n[TRADINGVIEW OSILATOR OYLERI - BUGUN]\n" + "\n".join(
            f"{s['hisse']}: oneri={s['oneri']} (al={s['al']}/sat={s['sat']}/notr={s['notr']}), RSI={s['rsi']}, "
            f"MACD={s['macd']}, StochK={s['stoch']}, CCI20={s['cci']}, ADX={s['adx']}"
            for s in b_satirlar
        )
    if sinyaller:
        prompt = prompt.replace("Kurallar: Asla uydurma", sinyaller + "\n\nKurallar: Asla uydurma")

    # once kullanici Z.ai anahtari (buyuk GLM modeli), olmazsa yedek zincir
    response = _zai_call(prompt)
    if not response:
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

    # Yayin onesi otomatik temizlik: uydurulmus tarih/yayinci satirlari,
    # koseli parantezli yer tutucular, eksik h2 yapisi, ASCII Turkce...
    response = rapor_son_islem(response)
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
    # Ozet kucuk bir cagri oldugu icin once ucretsiz yedekler (Groq ->
    # Cloudflare -> OpenRouter) kullanilir; boylece ana rapor icin AMD'nin
    # gunluk kotasini tuketmez.
    ozet = llm_call(prompt, sirasi=("YEDEK", "CF", "OR", "AMD"))
    save_daily("summaries", bugun, {"ozet": ozet})
    return {}


# ---------- DENEME PORTFOYU TAKIBI (Kiyaslamali) ----------
PORTFOLYO_DOSYASI = "portfolio.json"
BASLANGIC_SERMAYE = 100000.0

# Portfoy sayfalarindaki ortak aciklama notu: veri kesitini kullaniciya net anlatsin
# ("Guncel" fiyatlar aslinda bir onceki islem gununun kapanisidir; gunluk %0'lik
# hafta sonu/tatil kayitlari bu yuzden normaldir).
PORTFOY_NOTU = ("Portföy her sabah otomatik olarak, bir önceki işlem gününün kapanış fiyatlarıyla "
                "güncellenir; hafta sonu ve tatil günlerinde değer değişmez. Canlı fiyatlar için "
                "üstteki fiyat şeridine bakınız.")


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

    # BIST 100 kiyaslama cizgisi: endeksin portfoy baslangicindan bugune
    # getirisini 100.000 TL tabanina normalize eder (borsapy/borsapy index).
    xu100_degeri = None
    try:
        import borsapy as bp
        ix = bp.index("XU100")
        ixh = ix.history(period="3mo")
        if ixh is not None and len(ixh) >= 2:
            kapanislar = ixh["Close"].astype(float).dropna()
            kapanislar.index = [str(d)[:10] for d in kapanislar.index]
            taban = kapanislar[kapanislar.index >= p["start_date"]]
            taban_deger = float(taban.iloc[0]) if len(taban) else float(kapanislar.iloc[0])
            if taban_deger > 0:
                xu100_degeri = BASLANGIC_SERMAYE * (float(kapanislar.iloc[-1]) / taban_deger)
    except Exception as e:
        logger.warning("[Uyari] XU100 kiyaslama verisi alinamadi: %s", e)

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
            "DEPOSIT": round(deposit_degeri, 2),
            **({"XU100": round(xu100_degeri, 2)} if xu100_degeri else {})
        }
    })
    save_portfolio(p)

    ozet = f"Deneme Portföyü ({bugun}): Toplam {round(toplam,2):.2f} TL (Toplam %{yuzde:+.2f}, Mevduat: {round(deposit_degeri,2):.2f} TL, USD Karşılığı: {round(usd_degeri,2):.2f} TL, Altın Karşılığı: {round(gold_degeri,2):.2f} TL)"
    # Ozet artik final_report'a EKLENMEZ: portfoy verisi kendi sayfasinda
    # (portfolio.html) zaten yayinlaniyor; rapora gomulmesi "[PORTFOY OZETI]"
    # gibi ham pipeline ciktisinin sayfaya sizmasina yol aciyordu.
    return {"portfoy_ozet": ozet}


# ---------- DERIN ANALIZ (Z.ai GLM ile gunluk derin rapor) ----------
def derin_analiz_yap(rapor_state, teknik_satirlar, borsapy_satirlar):
    """Kullanicinin Z.ai anahtariyla (ZAI_API_KEY secret) sayfada yayinlanan
    uzun ve derinlemesine gunluk analizi uretir; sonunda onerilen hisseler
    tablosu (markdown) icerir. Anahtar SADECE sunucuda kalir, sayfaya
    gomulmez. Anahtar yoksa None doner."""
    anahtar = os.environ.get("ZAI_API_KEY", "")
    if not anahtar:
        logger.warning("[Derin Analiz] ZAI_API_KEY tanimli degil; sayfa uretilmeyecek.")
        return None

    model = os.environ.get("ZAI_MODEL") or "glm-4.7-flash"
    client = OpenAI(api_key=anahtar, base_url="https://api.z.ai/api/paas/v4/",
                    timeout=300.0, max_retries=1)

    teknik_ozet = "\n".join(
        f"{s['hisse']}: son {s['son']} TL, gunluk {s['gunluk']:+.2f}%, 60g {s['deg60']:+.1f}%, "
        f"kisa={s['kisa']} orta={s['orta']} uzun={s['uzun']}, WT={s['wt']}, "
        f"kanal %{s['konum']:.0f}, r={s['r']:.2f}, genel={s['genel']} ({s['sektor']})"
        for s in teknik_satirlar
    )
    borsapy_ozet = "\n".join(
        f"{s['hisse']}: oneri={s['oneri']} (al={s['al']}/sat={s['sat']}/notr={s['notr']}), "
        f"RSI={s['rsi']}, MACD={s['macd']}, StochK={s['stoch']}, CCI20={s['cci']}, ADX={s['adx']}"
        for s in borsapy_satirlar
    )
    portfoy = ""
    p = load_portfolio()
    if p and p.get("history"):
        son = p["history"][-1]
        portfoy = (f"Deneme portfoyu: toplam {son['total']} TL (%{son['pct']:+.2f}), "
                   f"gunluk %{son['daily_pct']:+.2f}, kiyaslamalar: {son.get('benchmarks')}")

    prompt = f"""Sen Türkiye piyasalarında uzmanlaşmış bağımsız bir finansal analist yapay zekâsısın (gerçek bir kişi veya kurum değilsin; kendini öyle tanıtma). Aşağıdaki BIST 30 verilerini kullanarak profesyonel okuyucuya hitap eden, DERİNLEMESİNE ve UZUN (en az 1200 kelime) bir günlük analiz raporu yaz. Rapor Türkçe olacak ve TÜM metinde doğru Türkçe karakterler (ç, ğ, ı, ö, ş, ü) kullanılacak; "sinyal" gibi kelimeler yanlış yazılmayacak.

Yanıtını şu yapıda oluştur (başlıklar aynen bu şekilde, "## " ile):

## 1. Piyasa Özeti ve Genel Bakış
(Günün genel havası, sektör dönüşümleri, endeks yorumu)

## 2. Sektör Bazlı Değerlendirme
(Isı haritası verilerine ve sinyal yoğunluğuna göre güçlü/zayıf sektörler)

## 3. Teknik Görsel Analiz
(EMA dizilimleri, Wave Trend, regresyon kanalı konumları ve Pearson değerlerinin birlikte yorumu; ayrışan hisseler)

## 4. Osilatör ve Momentum Okumaları
(RSI/MACD/Stochastic/CCI/ADX değerlerinin uyarıları; aşırı alım/satım bölgelerindeki hisseler)

## 5. Risk Haritası ve Senaryolar
(Yukarı/aşağı senaryolar, bölünme stratejileri, dikkat edilecek seviyeler)

## 6. Günün Önerilen Hisseleri
Raporun SONUNDA aşağıdaki başlıklarla tam bir tablo oluştur:

| Hisse | Sinyal | Giriş Bölgesi | Hedef | Stop | Gerekçe |
|-------|--------|---------------|-------|------|---------|
| ... | ... | ... | ... | ... | ... |

Tabloda SADECE teknik ve osilatör verilerine göre AL/GÜÇLÜ AL sinyali veren hisseleri listeleyip her biri için gerekçe yaz. Rakamları yalnızca verilen fiyatlardan türet, asla dışarıdan veri ekleme. Metinde köşeli parantezli [...] yer tutucu kullanma; rapor doğrudan "## 1." başlığıyla başlasın. Kimlik satırı EKLEME: "Hedge-Fund", "Direktör", "Portföy Yöneticisi", "Analist:", "Yayıncı:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri ve tarih ya da haftanın gün adı raporda GEÇMEYECEK (tarih-gün eşleştirmesinde sık hata yapıyorsun).

### VERİLER

[TEKNIK TARAMA SINYALLERI]
{teknik_ozet}

[TRADINGVIEW OSILATOR SINYALLERI]
{borsapy_ozet}

[PORTFOY DURUMU]
{portfoy}

[GUNLUK RAPOR VE HABERLER]
{rapor_state.get('news_data', '')}
{rapor_state.get('tech_data', '')[:3000]}
{rapor_state.get('final_report', '')[:6000]}"""

    son_hata = None
    denenecekler = [model] + (["glm-4.5-flash"] if model != "glm-4.5-flash" else [])
    for deneme, mdl in enumerate(denenecekler):
        try:
            logger.info("[Derin Analiz] GLM cagrisi (%s), deneme %d", mdl, deneme + 1)
            resp = client.chat.completions.create(
                model=mdl,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=8000,
                # GLM'in dusunme modunu kapatmak icin ozel parametre SDK'ya
                # extra_body ile gonderilir; dogrudan kwarg hata verir.
                extra_body={"thinking": {"type": "disabled"}},
            )
            icerik = resp.choices[0].message.content or ""
            if icerik.strip():
                return rapor_son_islem(icerik)
            son_hata = "bos yanit"
        except Exception as e:
            son_hata = str(e)[:200]
            logger.warning("[Derin Analiz] %s basarisiz: %s", mdl, son_hata)
            time.sleep(3)
    logger.error("[Derin Analiz] tum modeller basarisiz (%s)", son_hata)
    return None


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
SITE_URL = "https://borsa-raporlari.onrender.com/"
SITE_ADI = "BIST 30 Günlük Raporlar"

# ---------- INDEXNOW (Bing/Yandex/Seznam/Naver aninda indeksleme) ----------
# Hesap/anahtar YOK: kok dizine bir anahtar dosyasi koyar, sayfalar degistiginde
# api.indexnow.org'ya ping atariz. Bing, Yandex, Seznam ve Naver bu protokolle
# sayfalari kendi tarayicilarini beklemek yerine dakikalar icinde alir.
# (Google ve Baidu IndexNow kullanmaz; onlar icin dogrulama meta etiketleri
# asagida ENV ile desteklenir.)
INDEXNOW_KEY = "ba8235ea226b9c95831f13f37a9e223f"
INDEXNOW_ANA_SAYFALAR = [
    "index.html", "derin-analiz.html", "teknik-analiz.html",
    "borsapy-analiz.html", "portfolio.html",
]


def indexnow_ping(url_yollari):
    """Degisen URL'leri IndexNow'ya bildirir (hata raporu uretimini bozmaz)."""
    try:
        import urllib.request
        veri = json.dumps({
            "host": SITE_URL.split("//", 1)[1].rstrip("/"),
            "key": INDEXNOW_KEY,
            "keyLocation": SITE_URL + INDEXNOW_KEY + ".txt",
            "urlList": [SITE_URL + u for u in url_yollari],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.indexnow.org/indexnow", data=veri,
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=20) as r:
            logger.info("[IndexNow] ping HTTP %s (%d URL)", r.status, len(url_yollari))
    except Exception as e:
        logger.warning("[IndexNow] ping basarisiz (sorun degil): %s", e)


def _dogrulama_etiketleri():
    """Arama motoru sahiplik dogrulama meta etiketleri (ENV ile).
    Google Search Console / Bing Webmaster / Baidu Ziyan panelleri site
    dogrulamasi icin birer kod verir; asagidaki secret'lar doluysa sayfaya
    gumulur. Bos birakilirsa hicbir etiket eklenmez."""
    etiket = ""
    for env_adi, meta_adi in (
        ("GOOGLE_SITE_DOGRULAMA", "google-site-verification"),
        ("BING_SITE_DOGRULAMA", "msvalidate.01"),
        ("BAIDU_SITE_DOGRULAMA", "baidu-site-verification"),
    ):
        deger = (os.environ.get(env_adi) or "").strip()
        if deger:
            etiket += f'<meta name="{meta_adi}" content="{deger}">\n'
    return etiket


# ---------- SESLI RAPOR (Edge-TTS, ucretsiz Microsoft sinir sesleri) ----------
# Gunluk rapor her sabah MP3 olarak da okunur (tr-TR sinir sesi, ~48kbps).
# Ayrıca her rapor/derin sayfasinda tarayici sesiyle "Dinle" dugmesi var
# (Web Speech API — ses dosyasi olmasa bile calisir).
TTS_SESI = os.environ.get("TTS_SESI") or "tr-TR-EmelNeural"


def _ses_metni_hazirla(html):
    """HTML raporu okunabilir saga metne cevirir: tablolar atlanir (sesli
    okumada veri tablosu anlamsizdir), etiketler temizlenir, ~12 bin
    karakterle sinirlanir (~12 dk ses)."""
    metin = re.sub(r"<table.*?</table>", " (Tablo verileri için yazılı rapora bakabilirsiniz.) ", html, flags=re.S)
    metin = re.sub(r"<script.*?</script>", " ", metin, flags=re.S)
    metin = re.sub(r"<style.*?</style>", " ", metin, flags=re.S)
    metin = re.sub(r"<h2[^>]*>", " \n\n ", metin)
    metin = re.sub(r"<[^>]+>", " ", metin)
    metin = metin.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&bull;", ", ")
    # Bolum sonlarini cumle sonuna cevir (ses motoru orada nefes alsin)
    metin = metin.replace("\n\n", ". ")
    metin = re.sub(r"\s+", " ", metin).strip()
    return metin.strip(" .,;:")[:12000]


def rapor_sesi_uret(html, date_str):
    """Raporu Edge-TTS ile reports/{tarih}.mp3 olarak okur. Basarisizlik
    rapor uretimini ASLA etkilemez (False doner)."""
    try:
        import asyncio
        import edge_tts
    except ImportError:
        logger.warning("[TTS] edge-tts kurulu degil; ses uretilmedi.")
        return False
    metin = _ses_metni_hazirla(html)
    if len(metin) < 200:
        logger.info("[TTS] metin cok kisa; ses uretilmedi.")
        return False

    async def _uret():
        ses = edge_tts.Communicate(metin, TTS_SESI, rate="+8%")
        await ses.save(f"reports/{date_str}.mp3")

    try:
        asyncio.run(_uret())
        logger.info("[TTS] reports/%s.mp3 uretildi (%s, %d kr)", date_str, TTS_SESI, len(metin))
        return True
    except Exception as e:
        logger.warning("[TTS] ses uretilemedi: %s", e)
        return False


def _eski_sesleri_temizle(kal=14):
    """Repo sismesin: son N gunun MP3'u kalir, eskiler silinir."""
    try:
        dosyalar = sorted(f for f in os.listdir("reports") if f.endswith(".mp3"))
        for f in dosyalar[:-kal]:
            os.remove(os.path.join("reports", f))
            logger.info("[TTS] eski ses silindi: %s", f)
    except OSError as e:
        logger.warning("[TTS] ses temizligi basarisiz: %s", e)


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

/* ---- Mobil tasima duzeltmeleri ---- */
body { overflow-x: hidden; }
.tv-ticker-bant { max-width: 100%; overflow: hidden; }
.tbl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.tbl-wrap table { min-width: 760px; }   /* genis tablolar kart icinde yatay kayar */
.report table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.ticker-bant { background: var(--ink); overflow: hidden; white-space: nowrap; }
.ticker-iz { display: inline-block; padding: 8px 0; animation: ticker-kaydir 75s linear infinite; }
.ticker-bant:hover .ticker-iz { animation-play-state: paused; }
@keyframes ticker-kaydir { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
.ticker-oge { display: inline-block; margin: 0 16px; color: #cbd5e1; font-size: 13px; }
.ticker-oge b { color: #fff; letter-spacing: .3px; }
.ticker-saat { color: #64748b; font-size: 11.5px; }
@media (max-width: 640px) {
  .topbar .inner { padding: 10px 12px; }
  .topbar nav { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; }
  .topbar nav a { margin-left: 0; font-size: 13px; padding: 4px 0; }
}

/* ---- Sesli okuma butonu ---- */
.ses-btn { background:#fff; color:var(--accent); border:1px solid #99f6e4; border-radius:999px;
           padding:4px 14px; font-size:13px; font-weight:600; cursor:pointer; font-family:inherit; }
.ses-btn:hover { background:var(--accent-bg); }
.ses-btn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
"""


# ---------- GLM TABANLI SAYFA CEVRISI (Google Translate yerine) ----------
# Siteyi ve raporlari ureten yapay zeka (Z.ai GLM) ayni zamanda sayfayi
# cevirir: Puter.js uzerinden calisir, hicbir hesap/anahtar gerekmez,
# ziyaretci kendi ucretsiz Puter kotasin kullanir. Cince basta olmak
# uzere 5 dil + "Turkce'ye don" secenegi.
_CEVIRI_DILLERI = """
<select id="dil-sec" aria-label="Sayfa dili seçin" style="padding: 5px 8px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; background: #fff; color: #334155; cursor: pointer;">
  <option value="">🌐 Dil / 语言</option>
  <option value="zh">🇨🇳 中文（简体）</option>
  <option value="en">🇬🇧 English</option>
  <option value="de">🇩🇪 Deutsch</option>
  <option value="ru">🇷🇺 Русский</option>
  <option value="ar">🇸🇦 العربية</option>
  <option value="tr">🇹🇷 Türkçe (orijinal)</option>
</select>
<span id="ceviri-durum" style="font-size: 12px; color: #64748b;"></span>
<script>
(function() {
  var DIL_ADLARI = { zh: 'Simplified Chinese', en: 'English', de: 'German', ru: 'Russian', ar: 'Arabic' };
  var ORIJINALLER = null;
  var mesgul = false;

  function zamanAsimi(p, ms) {
    return Promise.race([p, new Promise(function(_, rej) { setTimeout(function() { rej(new Error('zaman aşımı')); }, ms); })]);
  }

  function metinDugumleri() {
    var sonuc = [];
    var ana = document.querySelector('main') || document.body;
    var walker = document.createTreeWalker(ana, NodeFilter.SHOW_TEXT, {
      acceptNode: function(n) {
        if (!n.nodeValue || !n.nodeValue.trim() || n.nodeValue.trim().length < 2) return NodeFilter.FILTER_REJECT;
        var p = n.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        var tag = p.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'TEXTAREA' || tag === 'INPUT') return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var n;
    while ((n = walker.nextNode())) sonuc.push(n);
    return sonuc;
  }

  function geriYukle() {
    if (ORIJINALLER) ORIJINALLER.forEach(function(k) { k.node.nodeValue = k.org; });
    document.getElementById('ceviri-durum').innerText = '';
  }

  async function sayfaCevir(dil) {
    var durum = document.getElementById('ceviri-durum');
    if (dil === '' || mesgul) return;
    if (dil === 'tr') { geriYukle(); return; }
    if (typeof puter === 'undefined') {
      durum.innerHTML = '<span style="color:#b91c1c">Çeviri motoru henüz yüklenmedi, birkaç saniye sonra tekrar deneyin.</span>';
      document.getElementById('dil-sec').value = '';
      return;
    }
    mesgul = true;
    try {
      if (ORIJINALLER === null) {
        ORIJINALLER = metinDugumleri().map(function(n) { return { node: n, org: n.nodeValue }; });
      }
      // Metinleri ~1800 karakterlik gruplara böl (istek başına)
      var parcalar = [], suanki = [], uzunluk = 0;
      ORIJINALLER.forEach(function(k) {
        suanki.push(k); uzunluk += k.org.length;
        if (uzunluk >= 1800) { parcalar.push(suanki); suanki = []; uzunluk = 0; }
      });
      if (suanki.length) parcalar.push(suanki);

      var sistem = 'You are a professional translator for a Turkish finance website. The user sends a JSON array of strings. Translate EVERY array item into ' + DIL_ADLARI[dil] + '. Return ONLY a JSON array of the exact same length containing the translations — no explanations, no markdown fences. Keep stock ticker codes (KCHOL, PETKM, THYAO...), numbers, currency amounts, dates and indicator acronyms (RSI, MACD, EMA, ADX, CCI, WT) exactly as they are.';
      for (var i = 0; i < parcalar.length; i++) {
        durum.innerText = '🌐 Çevriliyor... (' + (i + 1) + '/' + parcalar.length + ')';
        var r = await zamanAsimi(puter.ai.chat(
          [ { role: 'system', content: sistem }, { role: 'user', content: JSON.stringify(parcalar[i].map(function(k) { return k.org; })) } ],
          { model: 'z-ai/glm-4.7-flash' }
        ), 45000);
        var yanit = (r && r.message && r.message.content) || '';
        yanit = yanit.replace(/^```(json)?\\s*/i, '').replace(/\\s*```\\s*$/, '').trim();
        var cevrilen = JSON.parse(yanit);
        if (!Array.isArray(cevrilen) || cevrilen.length !== parcalar[i].length) throw new Error('bozuk yanıt');
        parcalar[i].forEach(function(k, idx) { if (typeof cevrilen[idx] === 'string') k.node.nodeValue = cevrilen[idx]; });
      }
      durum.innerText = '✓ ' + DIL_ADLARI[dil] + ' — Türkçe için listeden seçin';
    } catch (e) {
      var hata = (e && e.message) ? e.message : 'bilinmeyen hata';
      durum.innerHTML = '<span style="color:#b91c1c">Çeviri tamamlanamadı (' + hata + ').</span> <span style="color:#475569">Türkçe için listeden seçin. Puter oturum penceresi açıldıysa giriş yapmayı deneyin.</span>';
      document.getElementById('dil-sec').value = '';
    }
    mesgul = false;
  }

  document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('dil-sec').addEventListener('change', function() { sayfaCevir(this.value); });
  });
})();
</script>
"""


def _ceviri_widget(kok=""):
    """Ust widget cubuguna GLM ceviri secicisini koyar (kok parametresi
    ileride gerekirse diye korunur; script zaten kendini baglar)."""
    return _CEVIRI_DILLERI


def _radyo_kutusu(kok=""):
    """Ust widget cubugunda (ceviri secicisinin yaninda) BIST Radyo oynaticisi.

    radyo/indeks.json'dan son yayinlari okur; yayin yoksa kutu gorunmez.
    Yayinlar radyo.yml ile piyasa gunlerinde (acilis/ogle/kapanis) uretilir;
    sunucular kurgusal YAPAY ZEKA karakterleridir."""
    return f"""
<div id="bist-radyo" style="display:none; background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:8px 12px; min-width:250px; max-width:330px;">
  <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
    <strong style="font-size:13px; color:#0f172a;">📻 BIST Radyo</strong>
    <span style="background:#f0fdfa; color:#0f766e; border:1px solid #99f6e4; border-radius:999px; padding:1px 8px; font-size:11px;">AI sunucular</span>
  </div>
  <audio id="radyo-audio" controls preload="none" style="width:100%; height:34px;"></audio>
  <div id="radyo-liste" style="margin-top:6px; font-size:12.5px; color:#475569;"></div>
  <div style="margin-top:4px; color:#94a3b8; font-size:11px;">Kurgusal yapay zeka sunucular • Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</div>
</div>
<script>
(function() {{
  var kok = '{kok}';
  var kutu = document.getElementById('bist-radyo');
  if (!kutu) {{ return; }}
  fetch(kok + 'radyo/indeks.json?t=' + Date.now())
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      var bolumler = (d && d.bolumler) || [];
      if (!bolumler.length) {{ return; }}
      var ses = document.getElementById('radyo-audio');
      var liste = document.getElementById('radyo-liste');
      var secili = 0;
      function yukle(i) {{
        secili = i;
        ses.src = kok + bolumler[i].dosya;
        ses.play().catch(function() {{}});
        liste.innerHTML = bolumler.map(function(b, ix) {{
          var sure = b.sure_sn ? Math.round(b.sure_sn / 60) + ' dk' : '';
          return '<a href="javascript:void(0)" onclick="radyoSec(' + ix + ')" style="display:inline-block; margin:2px 6px 2px 0; ' +
                 (ix === secili ? 'font-weight:700;' : '') + '">' + b.baslik + (sure ? ' (' + sure + ')' : '') + '</a>';
        }}).join('');
      }}
      window.radyoSec = function(i) {{ yukle(i); }};
      yukle(0);
      kutu.style.display = 'block';
    }})
    .catch(function() {{}});
}})();
</script>"""


# ---------- KENDI SITE-ICI ARAMAMIZ (Cloudflare arama widget'i yerine) ----------
# CF "search-modal-snippet" yalnizca yetkilendirilmis domainlerde aciliyordu;
# onrender.com'da izinli olmadigi icin bos beyaz kutu render oluyordu. Bunun
# yerine botun her kosuda urettigi site-arama.json uzerinden tamamen yerel,
# hesapsiz, her domainde calisan bir arama kuruyoruz. Soru gorunumlu
# sorgular icin sonuc panelindeki baglantiyla mevcut Puter asistanina
# (aiSor) kopruleniir.
_SITE_ARAMA_KUTUSU = """
<div style="background: #ffffff; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0;">
    <div style="display: flex; gap: 8px;">
        <input type="text" id="site-arama-giris" placeholder="Sitede ara: hisse, konu, tarih... (ör. PETKM, RSI, portföy)" style="flex: 1; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px;" onkeypress="if(event.key === 'Enter') siteAra();">
        <button onclick="siteAra()" id="site-arama-btn" style="background: #0f766e; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: 600;">Ara</button>
    </div>
    <div id="site-arama-sonuc" style="display: none; margin-top: 10px; border-top: 1px solid #e2e8f0; padding-top: 8px; max-height: 320px; overflow-y: auto;"></div>
</div>
<script>
(function() {
  var KOK = '{KOK}';
  var INDEKS = null;

  function normalize(s) {
    var harita = { 'ı': 'i', 'İ': 'i', 'I': 'i', 'ğ': 'g', 'Ğ': 'g', 'ü': 'u', 'Ü': 'u',
                   'ş': 's', 'Ş': 's', 'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c',
                   'â': 'a', 'î': 'i', 'û': 'u', 'â': 'a' };
    s = String(s).toLowerCase();
    return s.replace(/[ıİIğĞüÜşŞöÖçÇâîû]/g, function(h) { return harita[h] || h; });
  }

  function indeksYukle() {
    if (INDEKS) return Promise.resolve(INDEKS);
    return fetch(KOK + 'site-arama.json?t=' + Date.now())
      .then(function(r) { return r.json(); })
      .then(function(d) { INDEKS = d; return d; });
  }

  function kacKez(haystack, needle) {
    if (!needle) return 0;
    var sayi = 0, i = 0, h = normalize(haystack), n = normalize(needle);
    while ((i = h.indexOf(n, i)) !== -1) { sayi++; i += n.length; }
    return sayi;
  }

  window.siteAra = function() {
    var giris = document.getElementById('site-arama-giris');
    var panel = document.getElementById('site-arama-sonuc');
    var q = (giris.value || '').trim();
    if (!q) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    panel.innerHTML = '<span style="color:#94a3b8;font-size:13px">Aranıyor...</span>';
    indeksYukle().then(function(indeks) {
      var terimler = q.split(/\\s+/).filter(Boolean);
      var sonuc = (indeks.sayfalar || []).map(function(s) {
        var puan = 0;
        terimler.forEach(function(t) {
          puan += kacKez(s.b, t) * 8 + kacKez(s.t, t);
        });
        return { s: s, puan: puan };
      }).filter(function(x) { return x.puan > 0; })
        .sort(function(a, b) { return b.puan - a.puan; })
        .slice(0, 6);

      var html = '';
      var nDil = normalize(q);
      var soruMu = /\\?\\s*$/.test(q) || /^(ne|nasil|neden|kim|hangi|nedir|kac|kac\\.|mi|mı|icin)\\b/i.test(nDil);
      html += '<div style="margin-bottom:8px"><a href="javascript:void(0)" onclick="siteAraAI()" style="font-size:13px">🤖 <b>' + q.replace(/</g, '&lt;') + '</b> sorusunu BIST AI asistanına sor &rarr;</a></div>';
      if (!sonuc.length) {
        html += '<div style="color:#64748b;font-size:13px">Site içinde sonuç bulunamadı. Yukarıdaki bağlantıyla yapay zekâya sorabilirsiniz.</div>';
      } else {
        sonuc.forEach(function(x) {
          var metin = normalize(x.s.t);
          var pos = -1;
          for (var i = 0; i < terimler.length; i++) { var p = metin.indexOf(normalize(terimler[i])); if (p !== -1 && (pos === -1 || p < pos)) pos = p; }
          var kesit = x.s.t;
          if (pos > 60) kesit = '…' + x.s.t.slice(Math.max(0, pos - 40), pos + 90);
          else kesit = x.s.t.slice(0, 130);
          html += '<div style="margin-bottom:8px"><a href="' + KOK + x.s.u + '" style="font-weight:600;font-size:13.5px">' + x.s.b + '</a>' +
                  '<div style="color:#64748b;font-size:12.5px">' + kesit.replace(/</g, '&lt;') + '…</div></div>';
        });
      }
      panel.innerHTML = html;
    }).catch(function() {
      panel.innerHTML = '<span style="color:#b91c1c;font-size:13px">Arama indeksi yüklenemedi.</span>';
    });
  };

  window.siteAraAI = function() {
    var giris = document.getElementById('site-arama-giris');
    var ai = document.getElementById('ai-input');
    if (ai) { ai.value = giris.value; aiSor(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  };
})();
</script>
"""


def _site_arama_kutusu(kok=""):
    return _SITE_ARAMA_KUTUSU.replace("{KOK}", kok)


def _ai_kutu():
    """AI asistan soru kutusu (interactive-box'in sag sutunu). Puter.js
    kullanildigi icin anahtar gerekmez; kutu her zaman aktiftir."""
    return """
        <div style="background: #ffffff; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div style="display: flex; gap: 8px;">
                <input type="text" id="ai-input" placeholder="BIST AI asistanına sorun..." style="flex: 1; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px;" onkeypress="if(event.key === 'Enter') aiSor();">
                <button onclick="aiSor()" id="ai-btn" style="background: #0f766e; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: 600;">Sor</button>
            </div>
            <div style="display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px;">
                <span class="ai-chip" onclick="aiChip(this)">Piyasa özeti ne?</span>
                <span class="ai-chip" onclick="aiChip(this)">RSI ve MACD nedir?</span>
                <span class="ai-chip" onclick="aiChip(this)">Destek ve direnç nedir?</span>
            </div>
        </div>"""


def _ai_panel():
    """AI asistan yanit paneli + Puter.js uzerinden GLM cagrisi.

    Puter.js "kullanici-oder" modeliyle calisir: gelistirici anahtar eklemez
    ve odeme yapmaz; kullanici kendi Puter ucretsiz kotasini kullanir
    (kota dolarsa Puter oturum acma penceresi acar)."""
    return """
<div id="ai-panel" style="display: none; background: #f0fdfa; border: 1px solid #99f6e4; border-radius: 8px; padding: 14px 16px; margin: 0 0 20px; font-size: 14px; line-height: 1.6;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <strong style="color: #0f766e;">🤖 BIST AI Asistan</strong>
        <a href="javascript:void(0)" onclick="document.getElementById('ai-panel').style.display='none'" style="color: #64748b; text-decoration: none; font-size: 18px; line-height: 1;">&times;</a>
    </div>
    <div id="ai-answer"></div>
    <div style="margin-top: 8px; color: #64748b; font-size: 12px;">Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir. Model: GLM-Flash (Z.ai, Puter.js üzerinden — ilk kullanımda Puter oturumu isteyebilir).</div>
</div>
<style>
.ai-chip { background: #f1f5f9; border: 1px solid #e2e8f0; color: #475569; border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.ai-chip:hover { border-color: #0f766e; color: #0f766e; }
</style>
<script src="https://js.puter.com/v2/"></script>
<script>
function aiChip(el) { document.getElementById('ai-input').value = el.textContent; aiSor(); }
function aiZamanAsimi(promise, ms) {
    return Promise.race([
        promise,
        new Promise(function(_, reject) { setTimeout(function() { reject(new Error('yanıt zaman aşımı')); }, ms); })
    ]);
}
async function aiSor() {
    var giris = document.getElementById('ai-input');
    var soru = (giris.value || '').trim();
    if (!soru) return;
    var btn = document.getElementById('ai-btn');
    var cevap = document.getElementById('ai-answer');
    var panel = document.getElementById('ai-panel');
    panel.style.display = 'block';
    cevap.innerHTML = '<em>Yanıt hazırlanıyor... (ilk kullanımda birkaç saniye sürebilir)</em>';
    btn.disabled = true; giris.disabled = true;
    var sistem = 'Sen "BIST 30 Günlük Raporlar" sitesinin Türkçe yapay zeka asistanısın. Görevin: Borsa İstanbul, makroekonomi ve teknik analiz (EMA, RSI, MACD, Wave Trend, destek/direnç vb.) konularında eğitici, kısa ve anlaşılır yanıtlar vermek. Canlı piyasa verine erişimin yok; güncel veriler için kullanıcıyı sitedeki Teknik Tarama, Borsapy Sinyal ve Günlük Rapor sayfalarına yönlendir. Kesin alım-satım tavsiyesi verme; bilgilendir. Yanıtlarını madde işaretleriyle yaz, en fazla 200 kelime tut.';
    var modeller = ['z-ai/glm-4.7-flash', 'infron:z-ai/glm-4.7-flash', 'z-ai/glm-4.5-flash', 'infron:z-ai/glm-4.5-flash'];
    var sonHata = '';
    var zamanasimiSayisi = 0;
    for (var i = 0; i < modeller.length; i++) {
        try {
            var r = await aiZamanAsimi(puter.ai.chat(
                [ { role: 'system', content: sistem }, { role: 'user', content: soru } ],
                { model: modeller[i] }
            ), 20000);
            var metin = (r && r.message && r.message.content) || (typeof r === 'string' ? r : '') || '';
            if (!metin) { sonHata = 'Boş yanıt'; continue; }
            metin = String(metin).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            cevap.innerHTML = metin.replace(/\\n/g, '<br>');
            giris.disabled = false; btn.disabled = false; giris.value = '';
            return;
        } catch (e) {
            sonHata = (e && e.message) ? e.message : 'Bilinmeyen hata';
            if (sonHata.indexOf('zaman aşımı') !== -1) {
                zamanasimiSayisi++;
                // Art arda 2 kez zaman aşımı = Puter erişilemiyor; diger
                // modelleri denemek bekletir, hata mesajina gec.
                if (zamanasimiSayisi >= 2) break;
            }
            if (sonHata.indexOf('auth') !== -1 || sonHata.indexOf('permission') !== -1) break;
        }
    }
    cevap.innerHTML = '<span style="color:#b91c1c">Asistan şu anda yanıt veremedi (' + sonHata + ').</span> ' +
        '<a href="javascript:void(0)" onclick="aiSor()" style="text-decoration:underline">Tekrar dene</a>' +
        '<div style="margin-top:6px;color:#475569;font-size:12.5px">Puter oturum açma penceresi açıldıysa giriş yapmayı deneyin (asistan, ücretsiz Puter kotanızı kullanır). Sorunuz tarayıcı sekmesinde korunuyor.</div>';
    giris.disabled = false; btn.disabled = false;
}
</script>"""


def _piyasa_etiketi():
    """Piyasa acikken '~15 dk gecikmeli' (TradingView canli verisi), kapaliyken
    'gün sonu kapanışı' etiketi dondurur. Boylece gece/gun sonu calisan islerde
    serit "canli/gecikmeli" diye yanlis etiketlenmez."""
    simdi = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul"))
    dakika = simdi.hour * 60 + simdi.minute
    acilis, kapanis = 9 * 60 + 55, 18 * 60 + 10  # BIST seans saatleri (TSİ)
    if simdi.weekday() < 5 and acilis <= dakika <= kapanis:
        return "~15 dk gecikmeli"
    return "gün sonu kapanışı"


def ticker_json_yaz(satirlar, etiket=None):
    """Canli ticker seridi icin ticker.json uretir; tum sayfalar istemci
    tarafinda bu dosyayi cekip seridi render eder (sayfalar statik olsa bile
    JSON her 30 dakikada bir yenilendigi icin veri tazedir).

    etiket: verinin niteligi; None ise _piyasa_etiketi() ile piyasa acik/kapali
    durumuna gore otomatik secilir."""
    if etiket is None:
        etiket = _piyasa_etiketi()
    veri = {
        "guncelleme": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M"),
        "etiket": etiket,
        "hisseler": [
            {"h": s["hisse"], "f": s["son"], "d": s["gunluk"]}
            for s in satirlar
        ],
    }
    with open("ticker.json", "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)


def _kendi_ticker(kok=""):
    """Kendi kayan hisse seridimiz: TradingView'in ucretsiz widget'i BIST
    verisini hic vermedigi icin (isim + kirmizi unlem gorunuyordu) kendi
    verimizle (borsapy/TradingView, ~15 dk gecikmeli) CSS marquee kurduk.
    Veri ticker.json'dan okunur; sayfa acikken 5 dk'da bir tazelenir."""
    return f"""
<div class="ticker-bant" id="ticker-bant" data-kok="{kok}" style="margin-bottom:16px">
  <div class="ticker-iz" id="ticker-iz"><span style="color:#94a3b8">Fiyatlar yükleniyor...</span></div>
</div>
<script>
(function() {{
  var kok = document.getElementById('ticker-bant').dataset.kok || '';
  function ciz(veri) {{
    var iz = document.getElementById('ticker-iz');
    var ogeler = veri.hisseler.map(function(h) {{
      var sinif = h.d >= 0 ? 'pos' : 'neg';
      var ok = h.d >= 0 ? '▲' : '▼';
      return '<span class="ticker-oge"><b>' + h.h + '</b> ' +
             h.f.toLocaleString('tr-TR', {{minimumFractionDigits: 2}}) + ' TL ' +
             '<span class="' + sinif + '">' + ok + ' ' + (h.d >= 0 ? '+' : '') + h.d.toFixed(2) + '%</span></span>';
    }}).join('');
    var etiket = veri.etiket || '~15 dk gecikmeli';
    var saat = '<span class="ticker-oge ticker-saat">' + veri.guncelleme + ' · ' + etiket + '</span>';
    iz.innerHTML = ogeler + saat + ogeler + saat;  // sorunsuz dongu icin kopya
  }}
  function yukle() {{
    fetch(kok + 'ticker.json?t=' + Date.now())
      .then(function(r) {{ return r.json(); }})
      .then(ciz)
      .catch(function() {{}});
  }}
  yukle();
  setInterval(yukle, 5 * 60 * 1000);
}})();  // IIFE: tanimlandigi anda calistir
</script>"""


def _sesli_okuma_js():
    """Tarayicinin yerlesik Web Speech API'si ile 'sesli oku' denetimi.

    Harici servis / API anahtari / kayit yok: tarayici + isletim sisteminin
    yerlesik sesleri kullanilir (Windows: 'Microsoft Tolga' vb. tr-TR ses;
    tr ses yoksa varsayilan ses konusur). Buton uc durumludur:
    okunuyor -> Duraklat, bekliyor -> Devam, durdu -> yeniden baslat."""
    return r"""<script>
(function() {
  if (!('speechSynthesis' in window)) { return; }
  // Chrome bazi sesleri gec yukler; sayfa acilir acilmaz ses listesini isit.
  var sesleriIsit = function() { try { window.speechSynthesis.getVoices(); } catch (e) {} };
  sesleriIsit();
  if (window.speechSynthesis.onvoiceschanged === null) {
    window.speechSynthesis.onvoiceschanged = sesleriIsit;
  }
  window.sesliOkuToggle = function(btn, kaynakId) {
    var S = window.speechSynthesis;
    var durum = btn.getAttribute('data-durum') || 'durdu';
    if (durum === 'okunuyor') { S.pause(); btn.textContent = '\u25b6 Devam'; btn.setAttribute('data-durum', 'bekliyor'); return; }
    if (durum === 'bekliyor') { S.resume(); btn.textContent = '\u23f8 Duraklat'; btn.setAttribute('data-durum', 'okunuyor'); return; }
    S.cancel();
    var kaynak = document.getElementById(kaynakId);
    var metin = kaynak ? (kaynak.textContent || '') : '';
    metin = metin.replace(/\s+/g, ' ').trim();
    if (!metin) { return; }
    var u = new SpeechSynthesisUtterance(metin);
    u.lang = 'tr-TR';
    var sesListe = S.getVoices() || [];
    var trSes = sesListe.filter(function(v) { return v.lang && v.lang.toLowerCase().indexOf('tr') === 0; })[0] || null;
    if (trSes) { u.voice = trSes; }
    u.rate = 1.0;
    u.onend = function() { btn.textContent = '\ud83d\udd0a Sesli Oku'; btn.setAttribute('data-durum', 'durdu'); };
    u.onerror = function() { btn.textContent = '\ud83d\udd0a Sesli Oku'; btn.setAttribute('data-durum', 'durdu'); };
    btn.textContent = '\u23f8 Duraklat';
    btn.setAttribute('data-durum', 'okunuyor');
    S.speak(u);
  };
})();
</script>"""


def _tl_okunus(deger):
    """224.1 -> '224,10'; 2450.0 -> '2.450,00' (Turkce sayi bicimi, sesli
    okumada nokta/virgul karisikligi olmasin)."""
    s = f"{deger:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _teknik_ses_metni(satirlar):
    """Teknik tarama sayfasi icin sesli ozet: 30 satirlik tabloyu tek tek
    okutmak yerine kisa, anlamli bir ozet konusulur."""
    if not satirlar:
        return "Teknik tarama verisi bulunamadı."
    guclu = [s for s in satirlar if s["genel"] == "GÜÇLÜ AL"]
    al = [s for s in satirlar if s["genel"] == "AL"]
    sat = [s for s in satirlar if s["genel"] in ("SAT", "GÜÇLÜ SAT")]
    parcalar = [f"Bugün {len(satirlar)} hisse tarandı."]
    if guclu:
        liste = "; ".join(f"{s['hisse']}, {_tl_okunus(s['son'])} TL" for s in guclu)
        parcalar.append(f"Güçlü al sinyali veren {len(guclu)} hisse: {liste}.")
    if al:
        parcalar.append(f"Al sinyalindeki {len(al)} hisse: " + ", ".join(s["hisse"] for s in al) + ".")
    if sat:
        parcalar.append(f"Sat sinyalindeki {len(sat)} hisse: " + ", ".join(s["hisse"] for s in sat) + ".")
    parcalar.append("Tüm hisselerin sinyal ve göstergeleri tabloda yer alıyor.")
    return " ".join(parcalar)


def _borsapy_ses_metni(satirlar):
    """Borsapy sinyal sayfasi icin sesli ozet: oneri dagilimi + asiri alim/satim
    bolgesindeki hisseler (RSI esikleri)."""
    if not satirlar:
        return "Borsapy sinyal verisi bulunamadı."
    guclu = [s for s in satirlar if s["oneri"] == "GÜÇLÜ AL"]
    sat = [s for s in satirlar if s["oneri"] in ("SAT", "GÜÇLÜ SAT")]
    notr = [s for s in satirlar if s["oneri"] == "NÖTR"]
    asiri_alim = [s for s in satirlar if isinstance(s.get("rsi"), (int, float)) and s["rsi"] >= 70]
    asiri_satim = [s for s in satirlar if isinstance(s.get("rsi"), (int, float)) and s["rsi"] <= 30]
    parcalar = [f"TradingView osilatör oylarına göre {len(satirlar)} hisse değerlendirildi."]
    if guclu:
        parcalar.append(f"Güçlü al sinyali veren {len(guclu)} hisse: " + ", ".join(s["hisse"] for s in guclu) + ".")
    if notr:
        parcalar.append(f"Nötr sinyaldeki hisseler: " + ", ".join(s["hisse"] for s in notr) + ".")
    if sat:
        parcalar.append(f"Sat sinyalindeki hisseler: " + ", ".join(s["hisse"] for s in sat) + ".")
    if asiri_alim:
        parcalar.append("RSI yetmiş üzerinde, aşırı alım bölgesindeki hisseler: " + ", ".join(s["hisse"] for s in asiri_alim) + ".")
    if asiri_satim:
        parcalar.append("RSI otuz altında, aşırı satım bölgesindeki hisseler: " + ", ".join(s["hisse"] for s in asiri_satim) + ".")
    return " ".join(parcalar)


def _sayfa(title, icerik, aktif="raporlar", kok="", aciklama=None, yol=None):
    """Tum sayfalar icin ortak iskelet (ust menu + govde + altbilgi).

    aciklama: <meta name="description"> ve og:description icin kisa ozet.
    yol: sayfanin site kokune gore yolu (canonical + og:url icin; or.
    "teknik-analiz.html", "reports/2026-09-08.html", "" = ana sayfa)."""
    a_r = ' class="active"' if aktif == "raporlar" else ""
    a_p = ' class="active"' if aktif == "portfoy" else ""
    a_t = ' class="active"' if aktif == "teknik" else ""
    a_b = ' class="active"' if aktif == "borsapy" else ""
    a_d = ' class="active"' if aktif == "derin" else ""
    tam_url = SITE_URL + (yol.lstrip("/") if yol else "")
    if not aciklama:
        aciklama = "Yapay zeka destekli günlük BIST 30 analizleri: teknik tarama, osilatör sinyalleri, model portföy ve sanal portföy takibi."
    favicon = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
               "%3Crect width='100' height='100' rx='18' fill='%230f172a'/%3E"
               "%3Ctext y='.9em' x='12' font-size='72'%3E%F0%9F%93%88%3C/text%3E%3C/svg%3E")
    ld_json = json.dumps({
        "@context": "https://schema.org", "@type": "WebPage", "name": title,
        "description": aciklama, "url": tam_url, "inLanguage": "tr",
        "isPartOf": {"@type": "WebSite", "name": SITE_ADI, "url": SITE_URL},
    }, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{aciklama}">
<link rel="canonical" href="{tam_url}">
<link rel="icon" href="{favicon}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_ADI}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{aciklama}">
<meta property="og:url" content="{tam_url}">
<meta property="og:image" content="{SITE_URL}og-cover.png">
<meta property="og:locale" content="tr_TR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{aciklama}">
{_dogrulama_etiketleri()}<script type="application/ld+json">{ld_json}</script>
<style>{BASE_CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
</head>
<body>
<header class="topbar"><div class="inner">
<a class="brand" href="{kok}index.html">BIST 30 Günlük Raporlar</a>
<nav><a href="{kok}index.html"{a_r}>Raporlar</a><a href="{kok}derin-analiz.html"{a_d}>Derin Analiz</a><a href="{kok}teknik-analiz.html"{a_t}>Teknik Tarama</a><a href="{kok}borsapy-analiz.html"{a_b}>Borsapy Sinyal</a><a href="{kok}portfolio.html"{a_p}>Deneme Portföyü</a></nav>
</div></header>
{_kendi_ticker(kok)}

<main class="wrap">
    <!-- Üst Widget Alanı (Canlı Saat, İstanbul Hava Durumu ve GLM Çeviri) -->
    <div class="site-widgets" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; background: #f8fafc; padding: 10px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; color: #475569; gap: 15px; border: 1px solid #e2e8f0;">
        <div id="live-clock-weather" style="display: flex; gap: 15px; align-items: center; flex-wrap: wrap;">
            <span id="current-date-time">⏳ Yükleniyor...</span>
            <span id="istanbul-weather">🌤️ İstanbul Hava Durumu...</span>
        </div>
{_ceviri_widget(kok)}
{_radyo_kutusu(kok)}
    </div>

    <!-- Etkileşimli Araçlar (Site İçi Arama ve BIST AI Asistan) -->
    <div class="interactive-box" style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px;">
{_site_arama_kutusu(kok)}
{_ai_kutu()}
    </div>
{_ai_panel()}

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
</script>
</body></html>"""


def _renk(deger):
    return "pos" if deger >= 0 else "neg"


def markdown_to_html(metin):
    import re
    # Paragraf icindeki "- " satirlarinin gercek liste olmasi icin bos satir ekle
    metin = re.sub(r"(?<!\n)\n(\s*[-*] )", r"\n\n\1", metin)
    return markdown.markdown(metin, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])


def rapor_sayfasi(html_icerik, date_str, baslik="Günlük Piyasa Raporu",
                  alt_baslik="BIST 30 &bull; Yapay zeka destekli günlük analiz",
                  kok_yol=None, aciklama=None, ses_url=None):
    if kok_yol is None:
        kok_yol = f"reports/{date_str}.html"
    if aciklama is None:
        aciklama = (f"{date_str} tarihli BIST 30 {baslik.lower()}: yönetici özeti, haber ve makro "
                    f"değerlendirme, hisse bazlı teknik analiz ve model portföy önerisi.")
    # Sesli rapor: varsa MP3 oynatici + her zaman tarayici sesiyle yedek dugme
    ses_bolumu = """
<div class="card" style="margin:0 0 18px; padding:12px 16px;">
  <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
    <strong style="font-size:13.5px">🔊 Raporu Dinle</strong>
    <button id="tts-btn" onclick="raporDinle()" style="background:#0f766e; color:#fff; border:none; padding:6px 12px; border-radius:4px; cursor:pointer; font-weight:600; font-size:13px;">Tarayıcı sesiyle başlat</button>
    <span id="tts-durum" style="color:#64748b; font-size:12.5px;"></span>
  </div>"""
    if ses_url:
        ses_bolumu += f"""
  <audio controls preload="none" src="{ses_url}" style="width:100%; height:40px; margin-top:10px;"></audio>
  <div style="color:#64748b; font-size:12px; margin-top:4px">Sesli bülten (yapay zekâ sesi) — indirilebilir MP3.</div>"""
    ses_bolumu += """
</div>
<script>
function raporDinle() {
  var durum = document.getElementById('tts-durum');
  var btn = document.getElementById('tts-btn');
  if (!('speechSynthesis' in window)) {
    durum.innerText = 'Tarayıcınız sesli okumayı desteklemiyor.';
    return;
  }
  if (speechSynthesis.speaking || speechSynthesis.pending) {
    speechSynthesis.cancel();
    durum.innerText = '⏹ Durduruldu.';
    btn.innerText = 'Tarayıcı sesiyle başlat';
    return;
  }
  var metin = (document.querySelector('article.report') || document.body).innerText.slice(0, 30000);
  var parcalar = metin.match(/[^.!?,;\\n]+[.!?,;]?\\n?/g) || [metin];
  var konusuldu = 0;
  parcalar.forEach(function(p) {
    p = p.trim();
    if (!p || p.length < 2) return;
    var u = new SpeechSynthesisUtterance(p);
    u.lang = 'tr-TR';
    u.rate = 1.05;
    u.onstart = function() { konusuldu++; durum.innerText = '🔊 Okunuyor... (' + konusuldu + '/' + parcalar.length + ' bölüm)'; };
    speechSynthesis.speak(u);
  });
  btn.innerText = '⏹ Durdur';
  durum.innerText = '🔊 Okunuyor...';
}
</script>"""
    icerik = f"""
<div class="hero">
<h1>{baslik}</h1>
<div class="meta"><span class="badge">{date_str}</span><span>{alt_baslik}</span>
<button type="button" class="ses-btn" id="sesli-okuma-btn" onclick="sesliOkuToggle(this,'rapor-ses-metin')" aria-label="Raporu sesli oku">🔊 Sesli Oku</button></div>
</div>
{ses_bolumu}
<article class="report">{html_icerik}</article>
<p style="margin-top:18px"><a href="../index.html">&larr; Tüm raporlara dön</a></p>"""
    return _sayfa(f"{baslik} - {date_str}", icerik, "raporlar", kok="../",
                  aciklama=aciklama, yol=kok_yol)


def build_html(report, date_str):
    ses_url = None
    if os.path.exists(os.path.join("reports", f"{date_str}.mp3")):
        ses_url = f"../reports/{date_str}.mp3"
    return rapor_sayfasi(markdown_to_html(report), date_str, ses_url=ses_url)


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
    xu100 = [item.get("XU100") for item in benchmarks]
    xu100_var = all(v is not None for v in xu100) and any(xu100)
    xu100_veri = [round(float(v), 2) for v in xu100] if xu100_var else None

    import random
    import json as _json
    grafik_id = f"portfoy-grafik-{random.randint(100000, 999999)}"

    datasetler = [
        {"label": "Deneme Portföyü (Hisseler)", "data": stocks, "borderColor": "#047857", "backgroundColor": "#047857"},
        {"label": "Altın", "data": golds, "borderColor": "#d97706", "backgroundColor": "#d97706"},
        {"label": "Dolar", "data": usds, "borderColor": "#2563eb", "backgroundColor": "#2563eb"},
        {"label": "Mevduat", "data": deposits, "borderColor": "#94a3b8", "backgroundColor": "#94a3b8", "borderDash": [6, 4]},
    ]
    if xu100_veri:
        datasetler.append({"label": "BIST 100 Endeksi", "data": xu100_veri, "borderColor": "#7c3aed", "backgroundColor": "#7c3aed", "borderDash": [2, 2]})
    veri = {"labels": etiketler, "datasets": datasetler}
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


def _portfoy_satirlari(p, limit=None, en_iyi=False):
    """Portfoy tablosu satirlari. en_iyi=True ise getirisi en yuksek
    hisselerden siralar (ana sayfadaki 'en iyi 8' ozeti icin)."""
    son = p["history"][-1]
    satirlar = []
    for h in p.get("shares", {}):
        ilk = p["initial_prices"].get(h)
        guncel = son["prices"].get(h, ilk)
        if ilk and guncel:
            fark = ((guncel - ilk) / ilk) * 100
            satirlar.append((h, fark))
    if en_iyi:
        satirlar.sort(key=lambda x: x[1], reverse=True)
    if limit:
        satirlar = satirlar[:limit]
    return "".join(
        f"<tr><td><strong>{h}</strong></td><td>{p['shares'][h]:,.2f}</td>"
        f"<td>{p['initial_prices'].get(h, 0):.2f} TL</td>"
        f"<td>{son['prices'].get(h, p['initial_prices'].get(h, 0)):.2f} TL</td>"
        f"<td class='{_renk(fark)}'>{fark:+.2f}%</td></tr>"
        for h, fark in satirlar
    )


def _portfoy_istatistikleri(p):
    son = p["history"][-1]
    return f"""
<div class="stats">
<div class="stat"><div class="label">Güncel Değer</div><div class="value">{son['total']:,.0f} TL</div></div>
<div class="stat"><div class="label">Günlük Değişim</div><div class="value {_renk(son['daily_pct'])}">{son['daily_pct']:+.2f}%</div></div>
<div class="stat"><div class="label">Toplam Getiri</div><div class="value {_renk(son['pct'])}">{son['pct']:+.2f}%</div></div>
<div class="stat"><div class="label">Başlangıç</div><div class="value" style="font-size:16px">{p['start_date']}</div></div>
</div>"""


# Turkce tarih bicimi (locale bagimliligi olmadan): "8 Eylul 2026, Sali"
_AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
          "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
_GUN_ADLARI = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _tr_tarih(iso_tarih):
    """'2026-09-08' -> '8 Eylül 2026, Salı' (bozuk formatta None doner)."""
    try:
        d = datetime.strptime(iso_tarih, "%Y-%m-%d")
        return f"{d.day} {_AYLAR[d.month - 1]} {d.year}, {_GUN_ADLARI[d.weekday()]}"
    except (ValueError, TypeError):
        return iso_tarih


def build_index_html(p, rapor_dosyalari, teknik_oneriler=None):
    if rapor_dosyalari:
        kartlar = "".join(
            f'<a class="rcard" href="reports/{fn}"><span class="date">{_tr_tarih(fn[:-5])}</span>'
            f'<span class="sub">Günlük raporu aç &rarr;</span></a>'
            for fn in rapor_dosyalari
        )
    else:
        kartlar = '<p style="color:var(--muted)">Henüz rapor yok.</p>'

    teknik_bolumu = ""
    if teknik_oneriler:
        kart = "".join(
            f'<a class="rcard" href="teknik-analiz.html"><span class="date">{s["hisse"]}</span>'
            f'<span class="sub">{s["genel"]} &bull; {s["son"]:,.2f} TL &bull; kanal %{s["konum"]:.0f} &bull; r={s["r"]:.2f}</span></a>'
            for s in teknik_oneriler
        )
        teknik_bolumu = f"""
<h2 class="section-title">Teknik Taramada Öne Çıkanlar</h2>
<div class="grid">{kart}</div>
<p style="margin:10px 0 0"><a href="teknik-analiz.html">Tüm teknik tarama tablosu &rarr;</a></p>"""

    portfoy_bolumu = ""
    if p and p.get("history"):
        grafik = sparkline_svg(p["history"])
        grafik_html = f'<div class="card" style="margin-bottom:14px">{grafik}</div>' if grafik else ""
        hisse_sayisi = len(p.get("shares", {}))
        gosterilen = min(8, hisse_sayisi)
        portfoy_bolumu = f"""
<h2 class="section-title">Deneme Portföyü</h2>
{_portfoy_istatistikleri(p)}
{grafik_html}
<div class="card" style="padding:8px 24px 16px">
<p style="margin:10px 0 4px; color:var(--muted); font-size:13px">Getirisi en yüksek {gosterilen} hisse:</p>
<div class="tbl-wrap">
<table><tr><th>Hisse</th><th>Adet</th><th>İlk Alım</th><th>Son Fiyat</th><th>Getiri</th></tr>{_portfoy_satirlari(p, limit=8, en_iyi=True)}</table>
</div>
<p style="margin:12px 0 4px"><a href="portfolio.html">Detaylı portföy geçmişi &rarr;</a>
<span style="color:var(--muted); font-size:12.5px">({hisse_sayisi} hissenin tamamı portföy sayfasında)</span></p>
<p style="margin:0 0 6px; color:var(--muted); font-size:12.5px">{PORTFOY_NOTU}</p>
</div>"""

    icerik = f"""
<div class="hero">
<h1>BIST 30 Günlük Piyasa Raporları</h1>
<p>Her sabah 08:00'de otomatik üretilen, yapay zeka destekli BIST 30 analizleri ve sanal portföy takibi.</p>
</div>
<h2 class="section-title">Rapor Arşivi</h2>
<div class="grid">{kartlar}</div>
{teknik_bolumu}
{portfoy_bolumu}"""
    return _sayfa(
        "BIST 30 Günlük Raporlar", icerik, "raporlar", yol="",
        aciklama="BIST 30'un yapay zeka destekli günlük raporları, teknik tarama ve osilatör sinyalleri, model portföy önerileri ve 100.000 TL'lik sanal deneme portföyünün takibi.",
    )


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
<p>BIST 30 hisselerine eşit dağıtılmış {p['initial_capital']:,.0f} TL'lik sanal portföy. Alım-satım yapılmaz, sadece takip edilir;
her sabah bir önceki işlem gününün kapanış fiyatlarıyla otomatik güncellenir.</p>
</div>
{_portfoy_istatistikleri(p)}
{grafik_html}
<h2 class="section-title">Hisse Performansı (ilk alım vs son fiyat)</h2>
<div class="card" style="padding:8px 24px 16px">
<div class="tbl-wrap">
<table><tr><th>Hisse</th><th>Adet</th><th>İlk Alım</th><th>Son Fiyat</th><th>Getiri</th></tr>{_portfoy_satirlari(p)}</table>
</div>
</div>
<h2 class="section-title">Günlük Geçmiş</h2>
<div class="card" style="padding:8px 24px 16px">
<div class="tbl-wrap">
<table><tr><th>Tarih*</th><th>Toplam Değer</th><th>Toplam %</th><th>Günlük %</th></tr>{gecmis}</table>
</div>
</div>
<p style="margin:10px 0 0; color:var(--muted); font-size:12.5px">{PORTFOY_NOTU}<br>
* Tarih sütunu, portföyün güncellendiği günü gösterir; fiyatlar bir önceki işlem gününün kapanışına aittir.
Hafta sonu ve tatil günlerinde değer değişmez.</p>"""
    return _sayfa(
        "Deneme Portföyü", icerik, "portfoy", yol="portfolio.html",
        aciklama="100.000 TL sermayeyle BIST 30 hisselerine eşit dağıtılmış sanal deneme portföyü: günlük değer takibi, hisse performansı ve altın/dolar/mevduat karşılaştırması.",
    )


# ---------- TEKNIK TARAMA (EMA / Wave Trend / Regresyon Kanali) ----------
# kursatsenturk.com Python serisindeki stratejilerin BIST 30 uyarlamasi:
#   - EMA dizilimi sinyalleri: kisa (5-8-13-21), orta (34-55), uzun (89-144)
#   - Wave Trend osilatoru: WT1/WT2 kesisimi, asiri alim/satim (+/-53)
#   - Son 60 gun lineer regresyon kanali + Pearson korelasyonu (trend gucu)
# Tamamen matematikseldir; LLM kullanmaz ve her gun otomatik guncellenir.

# BIST 30 sektor haritasi (isi haritasi icin)
SEKTORLER = {
    "Bankacılık": ["AKBNK", "GARAN", "ISCTR", "VAKBN", "YKBNK"],
    "Holding": ["KCHOL", "SAHOL"],
    "Havacılık & Ulaştırma": ["THYAO", "PGSUS", "TAVHL"],
    "Otomotiv": ["FROTO", "TOASO"],
    "Enerji & Petrokimya": ["TUPRS", "PETKM", "ENKAI", "ASTOR"],
    "Perakende": ["BIMAS", "MGROS"],
    "Metal & Madencilik": ["EREGL", "KRDMD"],
    "Kimya & Gübre": ["SASA", "GUBRF"],
    "Telekom": ["TCELL", "TTKOM"],
    "Gıda & İçecek": ["AEFES"],
    "Cam & Seramik": ["SISE"],
    "Savunma": ["ASELS"],
    "Gayrimenkul": ["EKGYO"],
    "Finans (Diğer)": ["DSTKF"],
    "Sanayi (Diğer)": ["TRALT"],
}


def _hisse_sektoru(hisse):
    for sektor, listeler in SEKTORLER.items():
        if hisse in listeler:
            return sektor
    return "Diğer"

def _ema(seri, periyot):
    return seri.ewm(span=periyot, adjust=False).mean()


def _wave_trend(kapanis):
    """Wave Trend osilatoru: ESA=EMA(k,10), CI=(k-ESA)/(0.015*D), WT1=EMA(CI,21), WT2=SMA(WT1,4)."""
    esa = _ema(kapanis, 10)
    d = (kapanis - esa).abs().ewm(span=10, adjust=False).mean()
    ci = (kapanis - esa) / (0.015 * d)
    wt1 = ci.ewm(span=21, adjust=False).mean()
    wt2 = wt1.rolling(4).mean()
    return wt1, wt2


_SON_TEKNIK = []
_SON_BORSPY = []


def teknik_tarama_yap():
    """BIST 30 icin teknik tarama tablosunu uretir; satir listesi (dict) doner."""
    try:
        from isyatirimhisse import fetch_stock_data
    except Exception as e:
        logger.warning("[Teknik Tarama] isyatirimhisse yok: %s", e)
        return []

    import numpy as np
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    simdi = datetime.now(tz)
    bugun = simdi.strftime("%Y-%m-%d")
    bitis = simdi.strftime("%d-%m-%Y")
    baslangic = (simdi - timedelta(days=300)).strftime("%d-%m-%Y")  # EMA144 + regresyon icin ~200 is gunu

    logger.info("[Teknik Tarama] %d hisse icin 300 gunluk veri cekiliyor...", len(HISSELER))
    print(f"[Teknik Tarama] {len(HISSELER)} hisse icin 300 gunluk veri cekiliyor...", flush=True)
    try:
        df = fetch_stock_data(HISSELER, start_date=baslangic, end_date=bitis)
    except Exception as e:
        logger.warning("[Teknik Tarama] Veri cekilemedi: %s", e)
        return []
    if df is None or df.empty:
        return []

    df.columns = [str(c).upper() for c in df.columns]
    kod_kolonu = next((c for c in ["HGDG_HS_KODU", "STOCK_CODE", "SYMBOL", "HIZ"] if c in df.columns), None)
    kapanis_kolonu = next((c for c in ["HGDG_KAPANIS", "KAPANIS", "CLOSE"] if c in df.columns), None)
    if not kod_kolonu or not kapanis_kolonu:
        return []

    # 30/30 kapsam hedefi: kutuphanenin dahili 10 sn zaman asimina takilan
    # semboller icin iki ek deneme turu yapilir ve sonuclar birlestirilir.
    for ek_deneme in range(2):
        mevcut = set(df[kod_kolonu].astype(str).str.upper().unique())
        eksikler = [h for h in HISSELER if h not in mevcut]
        if not eksikler:
            break
        logger.info("[Teknik Tarama] %d hisse icin ek deneme (%d): %s", len(eksikler), ek_deneme + 1, ", ".join(eksikler))
        print(f"[Teknik Tarama] {len(eksikler)} hisse icin ek deneme: {', '.join(eksikler)}", flush=True)
        try:
            df2 = fetch_stock_data(eksikler, start_date=baslangic, end_date=bitis)
            if df2 is not None and not df2.empty:
                df2.columns = [str(c).upper() for c in df2.columns]
                df = pd.concat([df, df2], ignore_index=True)
        except Exception as e:
            logger.warning("[Teknik Tarama] Ek deneme basarisiz: %s", e)
            time.sleep(3)

    # Gun ici canli fiyatlari TradingView'den al: isyatirimhisse gun sonu (EOD)
    # veri servis eder, piyasa acikken fiyatlar akmaz. borsapy (TradingView)
    # ~15 dk gecikmeli canli fiyat verir; kapali piyasada iki kaynak esittir
    # (o zaman ekleme yapilmaz ve tablo kapanis verisine doner).
    canli = {}
    try:
        import borsapy as bp
        for hisse in HISSELER:
            try:
                fi = bp.Ticker(hisse).fast_info
                deger = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
                if deger and float(deger) > 0:
                    canli[hisse] = float(deger)
            except Exception:
                pass
            time.sleep(0.2)
        logger.info("[Teknik Tarama] %d hisse icin canli fiyat alindi (TradingView).", len(canli))
    except ImportError:
        logger.warning("[Teknik Tarama] borsapy yok; gun ici canli fiyat kullanilamayacak.")

    satirlar = []
    for sira, hisse in enumerate(HISSELER, 1):
        seri = df[df[kod_kolonu] == hisse][kapanis_kolonu].astype(float).dropna()
        if len(seri) < 145:  # EMA144 anlamlı olsun
            logger.info("[Teknik Tarama] %d/%d %s: yetersiz gecmis (%d gun), atlandi", sira, len(HISSELER), hisse, len(seri))
            continue
        # Is Yatirim serisinin sonuna canli fiyati ekle (kapanistan farkliyse):
        # boylece EMA/WT/regresyon tum gostergeler gun ici hareketle hesaplanir.
        iy_son = float(seri.iloc[-1])
        tv = canli.get(hisse)
        if tv and abs(tv - iy_son) > 0.005:
            seri = pd.concat([seri, pd.Series([tv])], ignore_index=True)
        son = float(seri.iloc[-1])
        emalar = {p: _ema(seri, p) for p in (5, 8, 13, 21, 34, 55, 89, 144)}
        e = {p: float(emalar[p].iloc[-1]) for p in emalar}

        # Kisa vade: 5>8>13>21 dizilimi + fiyatin EMA5 ustunde olmasi
        if son > e[5] > e[8] > e[13] > e[21]:
            kisa = "AL"
        elif son < e[5] < e[8] < e[13] < e[21]:
            kisa = "SAT"
        else:
            kisa = "BEKLE"
        # Orta/uzun vade: EMA cifti yonu + fiyatin hizali EMA'nin tarafinda olmasi
        orta = "AL" if (e[34] > e[55] and son > e[34]) else ("SAT" if (e[34] < e[55] and son < e[34]) else "BEKLE")
        uzun = "AL" if (e[89] > e[144] and son > e[89]) else ("SAT" if (e[89] < e[144] and son < e[89]) else "BEKLE")

        wt1, wt2 = _wave_trend(seri)
        w1, w2 = float(wt1.iloc[-1]), float(wt2.iloc[-1])
        if pd.isna(w1) or pd.isna(w2):
            wt_sinyal = "BEKLE"  # duz/yetersiz seride CI 0/0 olabilir
        elif w1 < -53 and w1 > w2:
            wt_sinyal = "DİPTE AL"
        elif w1 > 53 and w1 < w2:
            wt_sinyal = "TEPEDE SAT"
        elif w1 > w2:
            wt_sinyal = "AL"
        else:
            wt_sinyal = "SAT"

        # Son 60 gun lineer regresyon kanali (±2 std) + Pearson
        son60 = seri.iloc[-60:]
        x = np.arange(len(son60))
        egim, kesim = np.polyfit(x, son60.values, 1)
        orta_cizgi = egim * x + kesim
        std = float((son60.values - orta_cizgi).std())
        ust, alt = orta_cizgi[-1] + 2 * std, orta_cizgi[-1] - 2 * std
        konum = ((son - alt) / (ust - alt) * 100) if ust > alt else 50.0
        r = float(np.corrcoef(x, son60.values)[0, 1]) if son60.std() > 0 else 0.0
        deg60 = (son / float(seri.iloc[-61]) - 1) * 100 if len(seri) >= 61 else 0.0
        gunluk = (son / float(seri.iloc[-2]) - 1) * 100 if len(seri) >= 2 and float(seri.iloc[-2]) > 0 else 0.0

        # Genel degerlendirme: yonlu sinyal sayisi (kisa/orta/uzun/WT)
        puansay = sum(1 for s in (kisa, orta, uzun) if s == "AL") + (1 if wt_sinyal in ("AL", "DİPTE AL") else 0)
        if puansay >= 4:
            genel = "GÜÇLÜ AL"
        elif puansay == 3:
            genel = "AL"
        elif puansay == 2:
            genel = "NÖTR"
        elif puansay == 1:
            genel = "SAT"
        else:
            genel = "GÜÇLÜ SAT"

        satirlar.append({
            "hisse": hisse, "son": round(son, 2), "deg60": round(deg60, 2),
            "gunluk": round(gunluk, 2), "sektor": _hisse_sektoru(hisse),
            "kisa": kisa, "orta": orta, "uzun": uzun,
            "wt": wt_sinyal, "wt1": round(w1, 1),
            "konum": round(konum, 0), "r": round(r, 2),
            "puan": puansay, "genel": genel,
        })

    # Guclu AL'ler one, iclerinde trend gucu (Pearson) yuksek olanlar basta
    satirlar.sort(key=lambda s: (s["puan"], s["r"]), reverse=True)
    save_daily("teknik", bugun, satirlar)
    global _SON_TEKNIK
    _SON_TEKNIK = satirlar
    logger.info("[Teknik Tarama] %d hisse tarandi; guclu AL: %d", len(satirlar),
                sum(1 for s in satirlar if s["genel"] in ("GÜÇLÜ AL", "AL")))
    print(f"[Teknik Tarama] {len(satirlar)} hisse tarandi.", flush=True)
    return satirlar


def _teknik_sinyal_hucre(sinyal):
    sinif = "pos" if sinyal in ("AL", "GÜÇLÜ AL", "DİPTE AL") else ("neg" if sinyal in ("SAT", "GÜÇLÜ SAT", "TEPEDE SAT") else "")
    return f"<td class='{sinif}'>{sinyal}</td>"


def _isi_renk(deg):
    """Günlük degisime gore yesil/kirmizi yogunluk rengi (isi haritasi hücre arka plani)."""
    if deg is None:
        return "#f1f5f9"
    yogunluk = min(abs(deg) / 4.0, 1.0)  # +/-4% tam doygunluk
    if deg >= 0:
        return f"rgba(4,120,87,{0.15 + 0.75 * yogunluk:.2f})"
    return f"rgba(185,28,28,{0.15 + 0.75 * yogunluk:.2f})"


def _sektor_isi_haritasi(satirlar):
    """Sektore ortalanmis gunluk degisim ile renkli blok haritasi uretir."""
    sektorler = {}
    for s in satirlar:
        sektorler.setdefault(s.get("sektor", "Diğer"), []).append(s)
    veriler = []
    for sektor, listeler in sektorler.items():
        ortalama = sum(x["gunluk"] for x in listeler) / len(listeler)
        veriler.append((sektor, ortalama, len(listeler)))
    veriler.sort(key=lambda x: x[1], reverse=True)
    hucre = "".join(
        f"<div style='background:{_isi_renk(d)}; border-radius:10px; padding:14px 10px; text-align:center; color:#0f172a;'>"
        f"<div style='font-weight:700; font-size:13px;'>{ad}</div>"
        f"<div style='font-size:17px; font-weight:800; margin-top:4px;'>{d:+.2f}%</div>"
        f"<div style='font-size:11px; opacity:.75;'>{n} hisse</div></div>"
        for ad, d, n in veriler
    )
    return f"""
<h2 class="section-title">Sektörel Isı Haritası <span style="font-size:12px; color:var(--muted); font-weight:400">(günlük değişim ortalaması)</span></h2>
<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(130px,1fr)); gap:10px; margin-bottom:8px;">{hucre}</div>"""


def _tv_modal_js():
    """Tablo satirina tiklaninca acilan TradingView mum grafigi modalı (istemci tarafi)."""
    return """
<div id="tv-modal" style="display:none; position:fixed; inset:0; background:rgba(15,23,42,.65); z-index:999; padding:20px;">
  <div style="background:#fff; max-width:1000px; margin:24px auto; border-radius:12px; padding:12px 16px 16px; max-height:92vh; overflow:auto;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <strong id="tv-baslik" style="font-size:16px;"></strong>
      <a href="javascript:void(0)" onclick="tvKapat()" style="color:#64748b; text-decoration:none; font-size:20px; line-height:1;">&times;</a>
    </div>
    <div id="tv-chart" style="height:520px;"></div>
    <div style="color:#64748b; font-size:12px; margin-top:6px;">Grafik: TradingView (giriş yok, ~15 dk gecikmeli veri). Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</div>
  </div>
</div>
<script>
function tvAc(hisse) {
  var modal = document.getElementById('tv-modal');
  var govde = document.getElementById('tv-chart');
  document.getElementById('tv-baslik').textContent = hisse + ' — Mum Grafiği (TradingView)';
  govde.innerHTML = '';
  var kutu = document.createElement('div');
  kutu.className = 'tradingview-widget-container';
  kutu.style.height = '100%';
  var ic = document.createElement('div');
  ic.className = 'tradingview-widget-container__widget';
  ic.style.height = '100%';
  kutu.appendChild(ic);
  var s = document.createElement('script');
  s.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
  s.async = true;
  s.innerHTML = JSON.stringify({
    symbol: 'BIST:' + hisse, interval: 'D', theme: 'light', style: '1',
    locale: 'tr', autosize: true, hide_side_toolbar: false, allow_symbol_change: false,
    calendar: false, support_host: 'https://www.tradingview.com'
  });
  kutu.appendChild(s);
  govde.appendChild(kutu);
  modal.style.display = 'block';
}
function tvKapat() {
  document.getElementById('tv-modal').style.display = 'none';
  document.getElementById('tv-chart').innerHTML = '';
}
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') tvKapat(); });
</script>"""


def build_teknik_html(satirlar, date_str):
    satir_html = "".join(
        f"<tr style='cursor:pointer' onclick=\"tvAc('{s['hisse']}')\"><td><strong>{s['hisse']}</strong></td>"
        f"<td>{s['son']:,.2f} TL</td>"
        f"<td class='{_renk(s['gunluk'])}'>{s['gunluk']:+.2f}%</td>"
        f"<td class='{_renk(s['deg60'])}'>{s['deg60']:+.1f}%</td>"
        f"{_teknik_sinyal_hucre(s['kisa'])}{_teknik_sinyal_hucre(s['orta'])}{_teknik_sinyal_hucre(s['uzun'])}"
        f"{_teknik_sinyal_hucre(s['wt'])}"
        f"<td>{s['konum']:.0f}%</td><td>{s['r']:.2f}</td>"
        f"{_teknik_sinyal_hucre(s['genel'])}</tr>"
        for s in satirlar
    )
    guclu = sum(1 for s in satirlar if s["genel"] == "GÜÇLÜ AL")
    simdi = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M")
    icerik = f"""
<div class="hero">
<h1>Teknik Tarama</h1>
<p>BIST 30 hisseleri icin otomatik teknik tarama: EMA dizilim sinyalleri (kısa 5-8-13-21, orta 34-55, uzun 89-144),
Wave Trend osilatörü ve 60 günlük regresyon kanalı konumu. Kanal konumu %0=alt bant, %100=üst bant demektir;
%100'ün üzeri fiyatın bandın üstüne çıktığını (aşırı), 0'ın altı bandın altına düştüğünü gösterir.
Pearson (r) son 60 gündeki trendin yönünü/gücünü gösterir (negatif r, fiyatın aşağı yönlü trendini ifade eder;
sinyal yalnızca yön göstergelerine dayanır). <strong>Son güncelleme: {simdi} (İstanbul)</strong> — piyasa
saatlerinde 30 dakikada bir TradingView canlı fiyatlarıyla (~15 dk gecikmeli); piyasa kapalıyken son işlem
gününün kapanış verisiyle güncellenir. Bu sayfa EMA/Wave Trend/regresyon kanalı yöntemine dayanır;
"Borsapy Sinyal" sayfası TradingView osilatör oylarını kullandığı için aynı hissede farklı sinyal
gösterebilir. <strong>Mum grafiği için tablodaki bir hisseye tıklayın.</strong></p>
<button type="button" class="ses-btn" onclick="sesliOkuToggle(this,'teknik-ses-metin')" aria-label="Teknik tarama özetini sesli oku">🔊 Sesli Özeti Dinle</button>
<div id="teknik-ses-metin" hidden>{_teknik_ses_metni(satirlar)}</div>
</div>
{_sektor_isi_haritasi(satirlar)}
<div class="card" style="padding:8px 24px 16px">
<div class="tbl-wrap">
<table>
<tr><th>Hisse</th><th>Son</th><th>Gün</th><th>60G %</th><th>Kısa Vade</th><th>Orta Vade</th><th>Uzun Vade</th><th>Wave Trend</th><th>Kanal</th><th>Pearson</th><th>Genel</th></tr>
{satir_html}
</table>
</div>
<p style="margin:12px 0 4px; color:var(--muted); font-size:13px">Bugün {len(satirlar)} hisse tarandı; {guclu} hisse GÜÇLÜ AL sinyalinde. Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</p>
</div>
{_tv_modal_js()}
{_sesli_okuma_js()}"""
    return _sayfa(
        f"Teknik Tarama - {date_str}", icerik, "teknik", yol="teknik-analiz.html",
        aciklama="BIST 30 teknik tarama tablosu: EMA dizilim sinyalleri, Wave Trend osilatörü, regresyon kanalı konumu ve Pearson korelasyonu. Piyasa saatlerinde 30 dakikada bir güncellenir.",
    )


# ---------- BORSAPY SINYALLERI (TradingView teknik analizi) ----------
def borsapy_analiz_yap():
    """borsapy kutuphanesi uzerinden TradingView'un hisse basina teknik
    analiz ozetini (oneri + osilator degerleri) BIST 30 icin toplar.

    Donus: satir listesi; her satir {hisse, oneri, al/sat/notr sayilari,
    rsi, macd, stoch, cci, adx}. Kismi sonuc dondurulebilir (tek hisse
    hatasi tum taramayi bozmaz)."""
    try:
        import borsapy as bp
    except Exception as e:
        logger.warning("[Borsapy] kutuphane kurulu degil: %s", e)
        return []

    ceviri = {"STRONG_BUY": "GÜÇLÜ AL", "BUY": "AL", "NEUTRAL": "NÖTR",
              "SELL": "SAT", "STRONG_SELL": "GÜÇLÜ SAT"}
    satirlar = []
    for i, hisse in enumerate(HISSELER, 1):
        try:
            s = bp.Ticker(hisse).ta_signals()
            ozet = s.get("summary") or {}
            degerler = (s.get("oscillators") or {}).get("values") or {}
            rec = ozet.get("recommendation", "NEUTRAL")

            def _say(anahtar):
                v = degerler.get(anahtar)
                return round(float(v), 2) if isinstance(v, (int, float)) else None

            satirlar.append({
                "hisse": hisse,
                "oneri": ceviri.get(rec, rec),
                "al": int(ozet.get("buy") or 0),
                "sat": int(ozet.get("sell") or 0),
                "notr": int(ozet.get("neutral") or 0),
                "rsi": _say("RSI"),
                "macd": _say("MACD.macd"),
                "stoch": _say("Stoch.K"),
                "cci": _say("CCI20"),
                "adx": _say("ADX"),
            })
            logger.info("[Borsapy] %d/%d %s: %s", i, len(HISSELER), hisse, satirlar[-1]["oneri"])
        except Exception as e:
            logger.info("[Borsapy] %s alinamadi: %s", hisse, str(e)[:100])
        time.sleep(0.3)  # TradingView rate limit'e karsi

    siralama = {"GÜÇLÜ AL": 0, "AL": 1, "NÖTR": 2, "SAT": 3, "GÜÇLÜ SAT": 4}
    satirlar.sort(key=lambda s: (siralama.get(s["oneri"], 9), -(s["rsi"] or 0)))
    logger.info("[Borsapy] %d hisse tarandi", len(satirlar))
    global _SON_BORSPY
    _SON_BORSPY = satirlar
    return satirlar


def build_borsapy_html(satirlar, date_str):
    import random
    import json as _json

    hucreler = "".join(
        f"<tr style='cursor:pointer' onclick=\"tvAc('{s['hisse']}')\"><td><strong>{s['hisse']}</strong></td>"
        f"{_teknik_sinyal_hucre(s['oneri'])}"
        f"<td><span class='pos'>{s['al']}</span> / <span class='neg'>{s['sat']}</span> / {s['notr']}</td>"
        f"<td>{s['rsi'] if s['rsi'] is not None else '-'}</td>"
        f"<td>{s['macd'] if s['macd'] is not None else '-'}</td>"
        f"<td>{s['stoch'] if s['stoch'] is not None else '-'}</td>"
        f"<td>{s['cci'] if s['cci'] is not None else '-'}</td>"
        f"<td>{s['adx'] if s['adx'] is not None else '-'}</td></tr>"
        for s in satirlar
    )

    # RSI grafigi: >70 asiri alim (kirmizi), <30 asiri satim (yesil)
    rsi_veri = [s for s in satirlar if isinstance(s.get("rsi"), (int, float))]
    grafik = ""
    if rsi_veri:
        veri = {
            "labels": [s["hisse"] for s in rsi_veri],
            "degerler": [s["rsi"] for s in rsi_veri],
        }
        veri_json = _json.dumps(veri, ensure_ascii=False)
        grafik_id = f"borsapy-rsi-{random.randint(100000, 999999)}"
        grafik = f"""
<div class="card" style="margin-bottom:20px">
<h3 style="margin:4px 0 10px">RSI (14) — Aşırı Alım/Satım Haritası</h3>
<div style="position:relative; height:{max(340, 18 * len(rsi_veri))}px">
<canvas id="{grafik_id}"></canvas>
</div>
</div>
<script>
(function() {{
    var veri = {veri_json};
    var renkler = veri.degerler.map(function(v) {{
        if (v >= 70) return '#b91c1c';
        if (v <= 30) return '#047857';
        return '#94a3b8';
    }});
    // 30/70 esik cizgileri + bar uclarina RSI degeri yazan mini eklenti
    var esikVeDegerler = {{
        id: 'esikVeDegerler',
        afterDatasetsDraw: function(chart) {{
            var ctx = chart.ctx, alan = chart.chartArea, xScale = chart.scales.x;
            [70, 30].forEach(function(esik) {{
                var px = xScale.getPixelForValue(esik);
                ctx.save();
                ctx.strokeStyle = esik === 70 ? 'rgba(185,28,28,.5)' : 'rgba(4,120,87,.5)';
                ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5;
                ctx.beginPath(); ctx.moveTo(px, alan.top); ctx.lineTo(px, alan.bottom); ctx.stroke();
                ctx.fillStyle = esik === 70 ? '#b91c1c' : '#047857';
                ctx.font = '600 11px sans-serif';
                var etiket = 'RSI ' + esik + (esik === 70 ? ' (aşırı alım)' : ' (aşırı satım)');
                ctx.fillText(etiket, Math.min(px + 5, alan.right - 110), alan.top + 12);
                ctx.restore();
            }});
            chart.getDatasetMeta(0).data.forEach(function(bar, i) {{
                var v = veri.degerler[i];
                ctx.save();
                ctx.fillStyle = '#334155';
                ctx.font = '10.5px sans-serif';
                ctx.fillText(String(v), Math.min(bar.x + 6, alan.right - 22), bar.y + 3.5);
                ctx.restore();
            }});
        }}
    }};
    new Chart(document.getElementById('{grafik_id}'), {{
        type: 'bar',
        data: {{ labels: veri.labels, datasets: [{{ label: 'RSI (14)', data: veri.degerler, backgroundColor: renkler }}] }},
        options: {{
            indexAxis: 'y',
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: function(c) {{ return 'RSI: ' + c.parsed.x; }} }} }} }},
            scales: {{
                x: {{ min: 0, max: 100,
                     grid: {{ color: '#e2e8f0' }},
                     ticks: {{ stepSize: 10 }} }},
                y: {{ ticks: {{ font: {{ size: 11 }} }} }}
            }}
        }},
        plugins: [esikVeDegerler]
    }});
}})();
</script>"""

    guclu = sum(1 for s in satirlar if s["oneri"] == "GÜÇLÜ AL")
    icerik = f"""
<div class="hero">
<h1>Borsapy Sinyalleri</h1>
<p>TradingView teknik analiz göstergelerinin BIST 30 özeti (borsapy kütüphanesiyle çekilir):
toplam osilatör + hareketli ortalama oylarına göre genel öneri, RSI, MACD, Stokastik %K, CCI ve ADX.
Piyasa saatlerinde teknik taramayla birlikte 30 dakikada bir güncellenir.
<strong>Mum grafiği için tablodaki bir hisseye tıklayın.</strong></p>
<p style="color:var(--muted); font-size:13px; margin-top:8px">Not: Bu sayfadaki oylar TradingView'in osilatör + hareketli ortalama özetidir;
"Teknik Tarama" sayfasındaki EMA/Wave Trend/regresyon kanalı yönteminden bağımsızdır — bu yüzden aynı
hisse için iki sayfa farklı sinyal gösterebilir. RSI ≥70 "aşırı alım" bölgesidir; o bölgedeki GÜÇLÜ AL
etiketleri momentum oylarının çoğunluğunu yansıtır, aşırı alım riskini ortadan kaldırmaz.</p>
<button type="button" class="ses-btn" onclick="sesliOkuToggle(this,'borsapy-ses-metin')" aria-label="Sinyal özetini sesli oku">🔊 Sesli Özeti Dinle</button>
<div id="borsapy-ses-metin" hidden>{_borsapy_ses_metni(satirlar)}</div>
</div>
{grafik}
<div class="card" style="padding:8px 24px 16px">
<div class="tbl-wrap">
<table>
<tr><th>Hisse</th><th>Öneri</th><th>Al/Sat/Nötr</th><th>RSI</th><th>MACD</th><th>Stoch %K</th><th>CCI20</th><th>ADX</th></tr>
{hucreler}
</table>
</div>
<p style="margin:12px 0 4px; color:var(--muted); font-size:13px">{len(satirlar)} hisse sorgulandı; {guclu} hisse GÜÇLÜ AL. RSI &ge;70 aşırı alım, &le;30 aşırı satım bölgesidir. ADX &gt;25 güçlü trend gösterir. Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</p>
</div>
{_tv_modal_js()}
{_sesli_okuma_js()}"""
    return _sayfa(
        f"Borsapy Sinyalleri - {date_str}", icerik, "borsapy", yol="borsapy-analiz.html",
        aciklama="TradingView göstergelerinden BIST 30 osilatör özeti: AL/SAT/NÖTR oyları, RSI, MACD, Stokastik %K, CCI ve ADX değerleri. 30 dakikada bir güncellenir.",
    )


def site_arama_json_yaz(rapor_dosyalari):
    """Site ici arama kutusunun indeksini (site-arama.json) uretir.
    Statik sayfalar anahtar kelime + hisse kodlariyla; rapor ve derin analiz
    sayfalari metinin ilk ~3500 karakteriyle indekslenir. Aramanin kendisi
    tarayici tarafinda calisir — hesap, anahtar veya dis servis gerekmez."""
    tag_temizle = re.compile(r"<[^>]+>")

    def makale_metni(dosya):
        try:
            src = open(dosya, encoding="utf-8").read()
        except OSError:
            return ""
        m = re.search(r'<article class="report"[^>]*>(.*?)</article>', src, re.S)
        govde = m.group(1) if m else src
        govde = re.sub(r"<script.*?</script>", " ", govde, flags=re.S)
        govde = re.sub(r"<style.*?</style>", " ", govde, flags=re.S)
        return tag_temizle.sub(" ", re.sub(r"\s+", " ", govde)).strip()

    hisse_listesi = " ".join(HISSELER)
    sayfalar = [
        {"b": "Ana Sayfa — Rapor Arşivi", "u": "index.html",
         "t": "BIST 30 günlük raporlar arşiv deneme portföy özeti teknik taramada öne çıkanlar günlük değişim " + hisse_listesi},
        {"b": "Teknik Tarama", "u": "teknik-analiz.html",
         "t": "EMA dizilim sinyalleri kısa orta uzun vade Wave Trend WT osilatör regresyon kanalı konumu Pearson korelasyon ısı haritası sektör mum grafiği TradingView " + hisse_listesi + " " + " ".join(SEKTORLER.keys())},
        {"b": "Borsapy Sinyalleri (TradingView)", "u": "borsapy-analiz.html",
         "t": "osilatör oyları al sat nötr güçlü al genel öneri RSI MACD stokastik stoch CCI ADX aşırı alım aşırı satım " + hisse_listesi},
        {"b": "Deneme Portföyü", "u": "portfolio.html",
         "t": "sanal portföy 100.000 TL eşit dağıtılmış hisse performansı günlük geçmiş getiri altın dolar mevduat XU100 benchmark karşılaştırma " + hisse_listesi},
    ]
    derin_metin = makale_metni("derin-analiz.html")
    if derin_metin:
        sayfalar.append({"b": "Derin Analiz (günlük derinlemesine inceleme)", "u": "derin-analiz.html",
                         "t": derin_metin[:3500]})
    for fn in rapor_dosyalari:
        metin = makale_metni(os.path.join("reports", fn))
        if metin:
            sayfalar.append({"b": f"Günlük Rapor — {fn[:-5]}", "u": f"reports/{fn}", "t": metin[:3500]})
    veri = {
        "guncelleme": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M"),
        "sayfalar": sayfalar,
    }
    with open("site-arama.json", "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)
    logger.info("[Arama] site-arama.json yazildi (%d sayfa)", len(sayfalar))


def sitemap_ve_robots_yaz(rapor_dosyalari):
    """Arama motorlari icin robots.txt ve sitemap.xml uretir.
    Ana sayfalarin lastmod'u en son bot kosusunun tarihi olur; raporlarin
    lastmod'u dosyanin son degisiklik zamanindan okunur."""
    statik = [
        ("index.html", "daily"),
        ("derin-analiz.html", "daily"),
        ("teknik-analiz.html", "hourly"),
        ("borsapy-analiz.html", "hourly"),
        ("portfolio.html", "daily"),
    ]
    bugun = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    url_blokleri = []
    for yol, frekans in statik:
        url_blokleri.append(
            f"  <url><loc>{SITE_URL}{yol}</loc><lastmod>{bugun}</lastmod>"
            f"<changefreq>{frekans}</changefreq><priority>{'1.0' if yol == 'index.html' else '0.8'}</priority></url>"
        )
    for fn in rapor_dosyalari:
        try:
            lm = datetime.fromtimestamp(os.path.getmtime(os.path.join("reports", fn))).strftime("%Y-%m-%d")
        except OSError:
            lm = bugun
        url_blokleri.append(
            f"  <url><loc>{SITE_URL}reports/{fn}</loc><lastmod>{lm}</lastmod>"
            f"<changefreq>monthly</changefreq><priority>0.6</priority></url>"
        )
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(url_blokleri) + "\n</urlset>\n")
    with open("robots.txt", "w", encoding="utf-8") as f:
        f.write("User-agent: *\nAllow: /\n\n"
                f"Sitemap: {SITE_URL}sitemap.xml\n")
    logger.info("[SEO] sitemap.xml (%d URL) ve robots.txt yazildi", len(url_blokleri))


def og_cover_png_yaz():
    """og-cover.svg'nin PNG karsiligini uretir (sosyal aglar og:image olarak
    SVG desteklemez; X/LinkedIn/Facebook icin 1200x630 PNG gerekir).

    Cizim deterministiktir (zaman damgasi yok), bu yuzden her kosuda ayni
    dosya cikar ve git diff olusmaz. Pillow kurulu degilse uyarip False doner;
    sayfa uretimini asla engellemez."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("[OG] Pillow kurulu degil; og-cover.png uretilemedi (pip install pillow).")
        return False

    W, H = 1200, 630
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)

    # Dikey degrade arka plan (#0f172a -> #1e293b)
    def _gradyan(y):
        t = y / H
        return tuple(int(a + (b - a) * t) for a, b in zip((15, 23, 42), (30, 41, 59)))

    for y in range(H):
        d.line([(0, y), (W, y)], fill=_gradyan(y))

    def _font(kalin, boy):
        adaylar = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if kalin else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if kalin else "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for p in adaylar:
            try:
                return ImageFont.truetype(p, boy)
            except Exception:
                continue
        return ImageFont.load_default()

    # Yatay kılavuz cizgileri
    for y in (180, 270, 360, 450):
        d.line([(120, y), (1080, y)], fill="#334155", width=1)

    # Yükselen cizgi + uç nokta (SVG ile ayni gorsel dil)
    noktalar = [(120, 430), (250, 400), (380, 415), (510, 340), (640, 355),
                (770, 280), (900, 250), (1030, 175)]
    d.line(noktalar, fill="#2dd4bf", width=10, joint="curve")
    d.ellipse([1016, 161, 1044, 189], fill="#2dd4bf")

    # Metinler
    d.text((120, 78), "BIST 30 Günlük Raporlar", font=_font(True, 58), fill="#ffffff")
    d.text((120, 152), "Yapay zeka destekli piyasa analizi, teknik tarama ve sanal portföy",
           font=_font(False, 26), fill="#94a3b8")
    d.text((120, 528), "borsa-raporlari.onrender.com", font=_font(False, 25), fill="#5eead4")
    uyari = "Bilgilendirme amaçlıdır — yatırım tavsiyesi değildir"
    u_font = _font(False, 22)
    u_genislik = d.textlength(uyari, font=u_font)
    d.text((W - 120 - u_genislik, 532), uyari, font=u_font, fill="#94a3b8")

    img.save("og-cover.png", format="PNG")
    logger.info("[OG] og-cover.png yazildi (%dx%d)", W, H)
    return True


if __name__ == "__main__":
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime('%Y-%m-%d')
    result = app.invoke({"news_data": "", "tech_data": "", "tech_prices": {}, "fundamental_data": "", "final_report": ""})
    report = result["final_report"]

    os.makedirs("reports", exist_ok=True)

    # Sesli bülten: rapor yazilmadan ONCE uretilir; boylece sayfa MP3 oynaticiyi
    # gorebilirim. Basarisiszlik raporu asla engellemez.
    try:
        rapor_sesi_uret(markdown_to_html(report), date_str)
    except Exception:
        logger.exception("[TTS] ses uretimi atlandi; rapor uretimini etkilemez.")
    _eski_sesleri_temizle()

    with open(f"reports/{date_str}.html", "w", encoding="utf-8") as f:
        f.write(build_html(report, date_str))

    raporlar = sorted((fn for fn in os.listdir("reports") if fn.endswith(".html")), reverse=True)

    # Teknik tarama: LLM'den bagimsiz, saf matematik; basarisiz olursa diger
    # sayfalarin uretimini bozmamasi icin ayri try/except icinde.
    teknik_oneriler = []
    teknik_satirlar = _SON_TEKNIK
    try:
        teknik_satirlar = teknik_satirlar or teknik_tarama_yap()
        if teknik_satirlar:
            with open("teknik-analiz.html", "w", encoding="utf-8") as f:
                f.write(build_teknik_html(teknik_satirlar, date_str))
            teknik_oneriler = [s for s in teknik_satirlar if s["genel"] in ("GÜÇLÜ AL", "AL")][:6]
            ticker_json_yaz(teknik_satirlar)
    except Exception:
        logger.exception("[Teknik Tarama] sayfa uretilemedi; rapor uretimini etkilemez.")

    # Borsapy (TradingView) sinyalleri + derin analiz icin toplanir.
    borsapy_satirlar = _SON_BORSPY
    try:
        borsapy_satirlar = borsapy_satirlar or borsapy_analiz_yap()
        if borsapy_satirlar:
            with open("borsapy-analiz.html", "w", encoding="utf-8") as f:
                f.write(build_borsapy_html(borsapy_satirlar, date_str))
    except Exception:
        logger.exception("[Borsapy] sayfa uretilemedi; diger sayfalar etkilenmez.")

    # Derin analiz: kullanicinin Z.ai anahtariyla uzun gunluk rapor;
    # anahtar yoksa atlanir, diger uretimleri etkilemez.
    try:
        derin = derin_analiz_yap(result, teknik_satirlar, borsapy_satirlar)
        if derin:
            with open("derin-analiz.html", "w", encoding="utf-8") as f:
                f.write(rapor_sayfasi(
                    markdown_to_html(derin), date_str,
                    baslik="Derin Analiz",
                    alt_baslik="BIST 30 &bull; Yapay zeka destekli derinlemesine analiz",
                    kok_yol="derin-analiz.html",
                    aciklama="BIST 30'un günlük derinlemesine analizi: sektör değerlendirmesi, EMA ve Wave Trend teknik okuma, osilatör-momentum yorumları ve risk senaryoları.",
                ))
            print("[Derin Analiz] sayfa uretildi.", flush=True)
    except Exception:
        logger.exception("[Derin Analiz] sayfa uretilemedi; diger sayfalar etkilenmez.")

    p = load_portfolio()
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_index_html(p, raporlar, teknik_oneriler))
    if p and p.get("history"):
        with open("portfolio.html", "w", encoding="utf-8") as f:
            f.write(build_portfolio_html(p))

    # SEO: arama motorlari dosyalari her kosuda taze lastmod ile yeniden yazilir
    try:
        sitemap_ve_robots_yaz(raporlar)
    except Exception:
        logger.exception("[SEO] sitemap/robots uretilemedi; rapor uretimini etkilemez.")

    # Sosyal medya paylasim goruntusu (PNG; SVG cogu platformda desteklenmez)
    try:
        og_cover_png_yaz()
    except Exception:
        logger.exception("[OG] og-cover.png uretilemedi; rapor uretimini etkilemez.")

    # Site ici arama indeksi
    try:
        site_arama_json_yaz(raporlar)
    except Exception:
        logger.exception("[Arama] site-arama.json uretilemedi; rapor uretimini etkilemez.")

    # IndexNow: degisen sayfalari Bing/Yandex/Seznam/Naver'a aninda bildir
    try:
        indexnow_ping(INDEXNOW_ANA_SAYFALAR + [f"reports/{fn}" for fn in raporlar[:5]])
    except Exception:
        logger.exception("[IndexNow] ping atlamasi sorun degil.")

    print("RAPOR OLUSTURULDU:", date_str)
