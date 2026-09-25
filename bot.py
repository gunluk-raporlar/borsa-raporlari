import os
import json
import math
import time
import html
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

# Yayin oncesi sirket adi / makro sayi denetimi (bkz. dogrulama.py)
import dogrulama
import makro_veri
import makro_katalog

# Elle eklenen hisse analiz bolumleri: aciklanan bilancolar + degerlendirmeler
# (bkz. hisse_analiz.py; veri: data/hisse-analiz/<KOD>.json)
import hisse_analiz

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

# Dorduncu saglayici: NVIDIA NIM (build.nvidia.com) — bircok model ucretsiz
# kredili; OpenAI uyumlu. Anahtar: https://build.nvidia.com/settings/api-keys
NVID_API_KEY = os.environ.get("NVIDIA_API_KEY", "") or os.environ.get("NIM_API_KEY", "")
NVID_BASE_URL = os.environ.get("NVID_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVID_MODELS = [m.strip() for m in os.environ.get("NVID_MODELS", "").split(",") if m.strip()]

# Besinci saglayici: Z.ai (GLM) — kullanicinin ucretli anahtari; ucretsiz
# saglayicilarin ortak sunuculari sikistiginda kaliteli ve stabil alternativa.
ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")
NVID_MODEL_TERCIH = [
    # Sira CANLI test ile dogrulandi (2026-09-25): once hizli+calisan modeller.
    # Kalite politikasi (2026-09): dusuk parametreli modeller (gemma-4-31b,
    # gpt-oss-20b) uydurma yaptigi icin cikarildi; yalnizca buyuk modeller.
    "nemotron-3-ultra-550b-a55b",     # ~550B MoE  (OK, 0.9 sn)
    "nemotron-3-super-120b-a12b",     # 120B MoE   (OK, 7.5 sn)
    "glm-5.3",                        # OK, 4.7 sn (alt dize eslesmesi glm-5.3-flash'i da yakalar — istenen)
    "kimi-k3",
    "kimi-k2.6",
]  # NOT: "deepseek-v4.1-flash" NIM'de 30 sn timeout veriyor, "llama-3.1-nemotron-70b" 404; cikarildi.

# Model emekleme durumlarina karsi otomatik secim icin tercih siralari
# (icerik eslesmesiyle bulunur; saglayici tam adlandirmayi degistirse de calisir).
# Kalite politikasi (2026-09 kullanici karari): dusuk parametreli modeller
# uydurma yaptigi icin TUM havuzlarda cikarildi; yalnizca buyuk modeller.
GROQ_MODEL_TERCIH = ["gpt-oss-120b", "llama-4-maverick", "llama-3.3-70b-versatile"]
CF_MODEL_TERCIH = ["llama-3.3-70b-instruct-fp8-fast", "llama-4-scout", "llama-3.3-70b-instruct", "llama-3.1-8b-instruct"]
OR_MODEL_TERCIH = [
    "nemotron-3-ultra-550b-a55b",   # ~550B MoE (en buyuk ucretsiz)
    "nemotron-3-super-120b-a12b",   # ~120B MoE
    "nemotron-3.5-lightning",       # 1M baglam
    "glm-5.2",                      # Z.ai GLM-5.2 (ucretsiz)
    "nex-n2.5-pro",
    "inkling",                      # 1M baglam ("inkling-small" suzgecle elenir)
]  # 2026-09: gemma-4-31b/26b, qwen3.8-27b, ling-3.0-flash-fin, inkling-small cikarildi (uydurma).

# Z.ai (GLM) model tercih sirasi — bot.py'deki tum Z.ai cagrilari bunu kullanir.
# YALNIZCA glm-5.3-flash: kullanici karari (2026-09) — buyuk glm-5.3, ZCode'la
# paylasilan haftalik kotayi hizla tukettigi icin kullanilmaz; 4.5/4.7 de
# uydurma yaptigi icin cikarildi. ZAI_MODEL env ile override hala mumkun.
ZAI_MODEL_TERCIH = ["glm-5.3-flash"]

if not AMD_API_KEY and not ALT_API_KEY and not CF_API_KEY and not OR_API_KEY and not NVID_API_KEY:
    raise SystemExit("AMD/ALT/CF/OR/NVIDIA API anahtarlarindan en az biri ayarlanmali!")

# Varsayilan model: 1B parametrelik MiniCPM5-1B karmasik Turkce promptlarda Ingilizce
# ic-konusma uretip talimatlari rapora sicrayabilir ve tekrar dongusune girebilir;
# bu yuzden varsayilan daha guclu bir model. Hafif model gerekirse AMD_MODEL ile secilebilir.
AMD_MODEL = os.environ.get("AMD_MODEL", "DeepSeek-V4-Flash")

# Opsiyonel: virgulle ayrilmis fallback modeller (environment ile kontrol edilebilir)
# GLM-5.3-Flash AMD galerisinde mevcut ve canli testte sorunsuz (2026-09 panel kaydi).
AMD_FALLBACK_MODELS = [m.strip() for m in os.environ.get("AMD_FALLBACK_MODELS", "Qwen3.8-Flash-Next,GLM-5.3-Flash").split(",") if m.strip()]  # MiniCPM5-1B cikarildi: Turkce raporu tasiyamiyor, talimat eko + Ingilizce karistirma yapiyor

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

nvid_client = OpenAI(
    api_key=NVID_API_KEY,
    base_url=NVID_BASE_URL,
    timeout=240.0,
    max_retries=0,
) if NVID_API_KEY else None

zai_client = OpenAI(
    api_key=ZAI_API_KEY,
    base_url="https://api.z.ai/api/paas/v4/",
    timeout=300.0,
    max_retries=1,
) if ZAI_API_KEY else None

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


# Haber RSS kaynaklari (2026-09 genisletildi: 6 -> 12 kaynak)
# Hepsi canli test edildi (HTTP 200 + gecerli RSS). Radyo ve gunluk raporlar
# bu havuzu kullanir; kaynak cesitliligi arttikca konular da cesitlenir.
HABER_KAYNAKLARI = {
    "BloombergHT": "https://www.bloomberght.com/rss",
    "CNN Turk": "https://www.cnnturk.com/feed/rss/ekonomi/news",
    "Haberturk": "https://www.haberturk.com/rss",
    "Ekonomim-Piyasa": "https://www.ekonomim.net/rss/piyasa-10",
    "Ekonomim-Ekonomi": "https://www.ekonomim.net/rss/ekonomi-5",
    "Ekonomim-Sirket": "https://www.ekonomim.net/rss/sirket-12",
    # ---- eklenen kaynaklar ----
    "NTV Ekonomi": "https://www.ntv.com.tr/ekonomi.rss",
    "TRT Haber Ekonomi": "https://www.trthaber.com/ekonomi_articles.rss",
    "AA Ekonomi": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi",
    "Sozcu Ekonomi": "https://www.sozcu.com.tr/rss/ekonomi.xml",
    "Dunya": "https://www.dunya.com/rss",
    "Ekonomist": "https://www.ekonomist.com.tr/rss",
    # ---- 2026-09 genisletme-2: kullanici onerileri + RSS rehberi taramasindan ----
    "Sabah Ekonomi": "https://www.sabah.com.tr/rss/ekonomi.xml",
    "Hurriyet Ekonomi": "https://www.hurriyet.com.tr/rss/ekonomi",
    "CNBC-e": "https://www.cnbce.com/rss",
    "Doviz.com": "https://www.doviz.com/news/rss",
    "NTV Para": "https://www.ntv.com.tr/ntvpara.rss",
    "Sozcu Borsa": "https://www.sozcu.com.tr/feeds-rss-category-borsa",
    "Forbes TR": "https://www.forbes.com.tr/rss",
    "Capital": "https://www.capital.com.tr/rss/all",
    "Mynet Finans": "https://www.mynet.com/rss/publisher-finans.rss",
    "Foreks": "https://www.foreks.com/rss/",
    "Investing TR Piyasa": "https://tr.investing.com/rss/market_overview.rss",
    "Paranin Yonu": "https://www.paraninyonu.com.tr/rss.xml",
}

# Finansla ilgisiz haber basliklarini elemek icin filtre
FINANS_DISI_KELIMELER = [
    "masterchef", "survivor", "on numara", "sayisal loto", "milli piyango",
    "hava durumu", "magazin", "dizi", "burc", "futbol", "mac sonucu",
    # 2026-09 eklentisi: gozlenen gurultu ornekleri (super loto, mac saatleri,
# magazin pozlari, tv yayin akisi) ve Turkce karakterli karsiliklari.
"süper loto", "süperloto", "loto", "iddaa", "bahis", "maç", "maç sonucu",
"hangi kanalda", "canlı izle", "canlı izlenir", "yayın akışı", "tv yayın",
"aile pozu", "mutlu aile", "pozu", "ünlü şarkıcı", "ünlü oyuncu", "oyuncu", "şarkıcı", "sporcu",
"astroloji", "transfer haberi", "iddaa tahmin", "puan durumu",
# gundem/magazin/kaza haberleri borsayi ilgilendirmiyor (kredibilite)
    "cinayet", "olümüne", "ölümüne", "oldur", "öldür", "intihar", "tecavüz",
    "darp", "kavga", "tutukland", "gözaltına", "uyuşturucu", "taciz",
    "babası kendisini", "yardım iste", "bebek", "gelin", "damat", "nişanlı",
    "düğün", "boşandı", "aşk yaşadı", "sevgilisi",
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

# Genel akis veren kaynaklar (magazin/spor/loto tasiyor): bu kaynaklarda baslik
# finans sozluguyle eslesmiyorsa alinmaz.
GENEL_AKIS_KAYNAKLARI = {"Haberturk", "Dunya", "Capital", "Forbes TR", "Ekonomist"}
FINANS_ANAHTARLARI = [
    "borsa", "hisse", "endeks", "faiz", "enflasyon", "dolar", "kur", "tl", "bütçe",
    "cari", "ihracat", "ithalat", "kap ", "bilanço", "kâr", "zarar", "banka", "kredi",
    "tahvil", "altın", "petrol", "ekonomi", "sanayi", "üretim", "büyüme", "istihdam",
    "işsizlik", "vergi", "yatırım", "fon", "merkez bankası", "tcmb", "ihale",
    "temettü", "halka arz", "reel getiri", "portföy", "swap", "rezerv", "maliyet",
]

def news_agent(state: AgentState):
    logger.info("[Haber Ajani] Finans haberleri toplaniyor...")
    print("[Haber Ajani] Finans haberleri toplaniyor...", flush=True)
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    toplanan = []
    gorulen = set()  # ayni haberi birden cok kaynaktan tekrar eklemeyelim

    def _norm(t):
        return re.sub(r"[^a-z0-9çğıöşü]", "", t.lower())

    # Son 4 gunde yayinlanan basliklari tekrar yayinlamayalim: RSS'in ilk
    # maddeleri gunlerce ayni kaliyor ve arsivde ayni haber 10+ kez gorunuyordu.
    onceki = set()
    for _g in load_recent("news", gun=4):
        if _g.get("date") == bugun:
            continue
        for _satir in (_g.get("data") or []):
            _m = re.match(r"^\[[^\]]+\]\s*(.*)$", _satir)
            onceki.add(_norm(_m.group(1) if _m else _satir))

    for ad, url in HABER_KAYNAKLARI.items():
        logger.info("[Haber Ajani] Kaynak: %s", ad)
        print(f"[Haber Ajani] Kaynak: {ad}", flush=True)
        try:
            f = feedparser.parse(url)
            for e in f.entries[:4]:  # kaynak basina 4 (12 kaynak x 4 = ~48 ham baslik)
                baslik = e.title
                if any(k.lower() in baslik.lower() for k in FINANS_DISI_KELIMELER):
                    continue
                # Genel akis kaynaklari (Haberturk genel, Dunya genel vb.) spor/
                # magazin tasiyor: baslik finans sozluguyle eslesmiyorsa alma.
                if ad in GENEL_AKIS_KAYNAKLARI and not any(
                        k in baslik.lower() for k in FINANS_ANAHTARLARI):
                    continue
                anahtar = _norm(baslik)
                if not anahtar or anahtar in gorulen or anahtar in onceki:
                    continue  # mukerrer / bos baslik
                gorulen.add(anahtar)
                toplanan.append(f"[{ad}] {baslik}")
        except Exception:
            continue
        time.sleep(1)
    if toplanan:
        save_daily("news", bugun, toplanan[:28])

    # Gecmis haberleri de ekle (hafiza)
    gecmis = load_recent("news", gun=7)
    gecmis_metin = ""
    if len(gecmis) > 1:
        gecmis_metin = "\n[SON 7 GUNUN HABERLERI]\n"
        for g in gecmis[1:]:
            gecmis_metin += f"-- {g['date']}: " + " | ".join(g['data'][:5]) + "\n"

    if not toplanan and not gecmis:
        return {"news_data": "Haber verisi alinamadi."}
    bugun_metin = "\n".join(toplanan[:28]) if toplanan else "(bugun haber alinamadi)"
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
_BILANCO_KODLARI = {
    # Seri XI No:29 mali tablo kodlari -> kayit anahtarlari
    "1BL": "toplam_varlik",
    "1A": "donen_varlik",
    "1AK": "duran_varlik",
    "2A": "kisa_borc",
    "2B": "uzun_borc",
    "2AA": "fin_borc_kisa",
    "2BA": "fin_borc_uzun",
    "2N": "ozsermaye",
    "2OA": "odenmis_sermaye",
    "3L": "net_kar",
    "3C": "satis",
}


def _df_bilanco_satiri(df, bu_yil):
    """fetch_financials ciktisindan (tek sirket) en guncel dolu donemin
    bilanço/gelir ozetini cikarir. Finansal sirketlerin (banka/faktoring)
    sablonu farkli kod kullandigi icin isim tabanli yedek eslestirme de yapar:
    AKTİF TOPLAMI -> toplam_varlik, ÖZKAY* -> ozsermaye, NET DÖNEM KAR* -> net_kar.
    Donus: ({kalem: TL}, donem) ya da ({}, '')."""
    if df is None or df.empty:
        return {}, ""
    yil_kolonlari = [c for c in df.columns if str(c).startswith(str(bu_yil))]
    if not yil_kolonlari:
        return {}, ""
    kolon_degerler = {}
    for _, r in df.iterrows():
        kod = str(r.get("FINANCIAL_ITEM_CODE") or "")
        ad = str(r.get("FINANCIAL_ITEM_NAME_TR") or "").upper()
        hedef = _BILANCO_KODLARI.get(kod)
        for c in yil_kolonlari:
            try:
                v = r.get(c)
                if v is None:
                    continue
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v == 0:
                continue
            hucre = kolon_degerler.setdefault(c, {})
            if hedef and hedef not in hucre:
                hucre[hedef] = v
            if "toplam_varlik" not in hucre and ("AKTİF TOPLAM" in ad or "TOPLAM VARLIK" in ad):
                hucre["toplam_varlik"] = v
            # Not: 'Özkaynak Yöntemiyle Değerlenen Yatırımlar' (1BD) satiri da
            # 'ÖZKAY' icerir; ozsermaye sanilmasin diye YÖNTEM haric tutulur.
            if "ozsermaye" not in hucre and "ÖZKAY" in ad and "YÖNTEM" not in ad:
                hucre["ozsermaye"] = v
            if "net_kar" not in hucre and "NET DÖNEM KAR" in ad:
                hucre["net_kar"] = v
    if not kolon_degerler:
        return {}, ""
    # veri olan EN GUNCEL donemi sec (gelecek donemler bos olur)
    son_kolon = max(kolon_degerler.keys(), key=lambda c: [int(x) for x in str(c).split("/")])
    kod_deger = kolon_degerler[son_kolon]
    if "toplam_varlik" not in kod_deger and "ozsermaye" not in kod_deger:
        return {}, ""
    # Ozkaynak dogrulamasi: saglayicinin satiri bazen yanlis kalemi getiriyor
    # (KCHOL 2026/6 icin 145,4 mlr; ozdeslik 1.292,0 mlr veriyor ve cok donemli
    # Is Yatirim verisi de 1.292,0 diyor). Ozdeslik varsa o deger kullanilir.
    try:
        _tv = kod_deger.get("toplam_varlik")
        _kb, _ub = kod_deger.get("kisa_borc"), kod_deger.get("uzun_borc")
        if _tv and _kb is not None and _ub is not None:
            _oz = float(_tv) - float(_kb) - float(_ub)
            if _oz > 0:
                _eski = kod_deger.get("ozsermaye")
                if _eski and abs(float(_eski) - _oz) / _oz > 0.10:
                    logger.warning("[Bilanco] ozkaynak saglayicidan %.4g, ozdeslikten %.4g; ozdeslik kullanildi.",
                                   float(_eski), _oz)
                kod_deger["ozsermaye"] = _oz
    except Exception:
        pass
    finansal_borc = kod_deger.pop("fin_borc_kisa", 0.0) + kod_deger.pop("fin_borc_uzun", 0.0)
    if finansal_borc:
        kod_deger["finansal_borc"] = finansal_borc
    return kod_deger, son_kolon


def _bilanco_cek_hisse(hisse, bu_yil):
    """Tek sirket icin mali tablo ceker. Grup 1 (genel sablon) bos donerse
    grup 2 (banka/faktoring sablonu) denenir. Donus: ({kalem: TL}, donem)."""
    from isyatirimhisse import fetch_financials
    for grup in ("1", "2"):
        try:
            df = fetch_financials(symbols=[hisse], start_year=bu_yil, end_year=bu_yil, financial_group=grup)
        except Exception:
            continue
        kay, donem = _df_bilanco_satiri(df, bu_yil)
        if kay:
            return kay, donem
    return {}, ""


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
    bilanco_kayit = {}
    bu_yil = datetime.now(tz).year
    for sira, hisse in enumerate(HISSELER, 1):
        logger.info("[Temel Ajan] %d/%d: %s", sira, len(HISSELER), hisse)
        print(f"[Temel Ajan] {sira}/{len(HISSELER)}: {hisse}", flush=True)
        try:
            fin = fetch_financials(symbols=[hisse], start_year=bu_yil - 1, end_year=bu_yil, financial_group="1")
        except Exception:
            fin = None
        if fin is None or fin.empty:
            # Finansal sirketler (banka/faktoring) genel sablonda bos gelir;
            # banka sablonundan (grup 2) bilanço dene.
            kayit, donem = _bilanco_cek_hisse(hisse, bu_yil)
            if kayit:
                kayit["donem"] = donem
                bilanco_kayit[hisse] = kayit
            time.sleep(4)
            continue
        try:
            # Bilanço/gelir ozetini ayni cekimden ucretsiz cikar (hisse sayfasi karti)
            kayit, donem = _df_bilanco_satiri(fin, bu_yil)
            if kayit:
                kayit["donem"] = donem
                bilanco_kayit[hisse] = kayit
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
    if bilanco_kayit:
        save_daily("bilanco", bugun, bilanco_kayit)
        logger.info("[Temel Ajan] %d sirket icin bilanco ozeti kaydedildi.", len(bilanco_kayit))

    if not ozetler:
        return {"fundamental_data": "Finansal veri alinamadi."}
    return {"fundamental_data": "\n".join(ozetler)}


# ---------- LLM CAGRISI ----------
def _looks_degenerate(metin: str) -> bool:
    # Tablo satiri tekrari: ayni (| ile baslayan) satirdan 3+ kez varsa dongudur
    satirlar = [s.strip() for s in metin.split("\n") if s.strip().startswith("|")]
    if satirlar:
        if Counter(satirlar).most_common(1)[0][1] >= 3:
            return True
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


# ---------- IZLEME: LLM GECIKME METRIKLERI ----------
# Her LLM cagrisinin suresi data/metrics/ altina yazilir (web/durum sayfasi ve
# /api/metrics bu dosyalari okur). Tum adimlar try/except korumalidir; izleme
# rapor uretimini ASLA bozamaz.
METRIK_KAYIT = []


def _metrik_kaydet(islev, sure, ok, model="-"):
    try:
        METRIK_KAYIT.append({
            "islev": islev,
            "model": model,
            "sure_sn": round(float(sure), 3),
            "ok": bool(ok),
            "saat": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).isoformat(timespec="seconds"),
        })
    except Exception:
        pass


def metrik_dosyasi_yaz(etiket="bot"):
    """Toplanan gecikme metriklerini data/metrics/llm-<tarih>.json (gecmis) ve
    data/metrics/latest.json (ozet, izleme sayfasinin okudugu) dosyalarina yazar."""
    try:
        if not METRIK_KAYIT:
            return
        os.makedirs(os.path.join("data", "metrics"), exist_ok=True)
        tarih = datetime.now().strftime("%Y-%m-%d")
        sureler = sorted(k["sure_sn"] for k in METRIK_KAYIT)
        p95 = sureler[int(0.95 * (len(sureler) - 1))] if sureler else 0
        ozet = {
            "adet": len(sureler),
            "ortalama_sn": round(sum(sureler) / len(sureler), 3),
            "p95_sn": p95,
            "maks_sn": round(sureler[-1], 3),
            "basari_orani": round(sum(1 for k in METRIK_KAYIT if k["ok"]) / len(sureler), 3),
            "son_guncelleme": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).isoformat(timespec="seconds"),
        }
        gunluk_yol = os.path.join("data", "metrics", f"llm-{tarih}.json")
        gecmis = []
        if os.path.exists(gunluk_yol):
            try:
                with open(gunluk_yol, encoding="utf-8") as fh:
                    gecmis = json.load(fh).get("cagrilar", [])
            except Exception:
                gecmis = []
        with open(gunluk_yol, "w", encoding="utf-8") as fh:
            json.dump({"tarih": tarih, "etiket": etiket,
                       "cagrilar": gecmis + METRIK_KAYIT, "ozet": ozet},
                      fh, ensure_ascii=False, indent=1)
        latest_yol = os.path.join("data", "metrics", "latest.json")
        latest = {}
        if os.path.exists(latest_yol):
            try:
                with open(latest_yol, encoding="utf-8") as fh:
                    latest = json.load(fh)
            except Exception:
                latest = {}
        latest["llm"] = ozet
        DOGRULAMA_IST["son_guncelleme"] = ozet["son_guncelleme"]
        latest["dogrulama"] = dict(DOGRULAMA_IST)
        latest["guncelleme"] = ozet["son_guncelleme"]
        with open(latest_yol, "w", encoding="utf-8") as fh:
            json.dump(latest, fh, ensure_ascii=False, indent=1)
        logger.info("[Metrik] %d LLM cagrisi olculdu: ort %.1fs, p95 %.1fs (%s)",
                    ozet["adet"], ozet["ortalama_sn"], ozet["p95_sn"], gunluk_yol)
    except Exception:
        logger.exception("[Metrik] LLM metrik dosyalari yazilamadi; uretim etkilenmez.")


def llm_call(prompt, max_deneme=6, fallback_on_fail=True, sirasi=None):
    """_llm_call_ic icin gecikme olcumlu ince sarmalayici (imza ayni kalir)."""
    t0 = time.time()
    sonuc = _llm_call_ic(prompt, max_deneme=max_deneme, fallback_on_fail=fallback_on_fail, sirasi=sirasi)
    _metrik_kaydet("llm_call", time.time() - t0, bool(sonuc))
    return sonuc


def _zai_call(prompt):
    """_zai_call_ic icin gecikme olcumlu ince sarmalayici (imza ayni kalir)."""
    t0 = time.time()
    sonuc = _zai_call_ic(prompt)
    _metrik_kaydet("zai_call", time.time() - t0, sonuc is not None, model="glm")
    return sonuc


def _llm_call_ic(prompt, max_deneme=6, fallback_on_fail=True, sirasi=None):
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

    # Deneme sirasi: AMD (ucretsiz ana) -> ZAI (kullanicinin GLM anahtari;
    # ucretsiz ortak sunucular sikistiginda kaliteli/stabil ikinci sans) ->
    # NVIDIA -> OpenRouter -> Groq. sirasi ile oncelik degistirilebilir.
    sirasi = sirasi or ("AMD", "ZAI", "NVID", "OR", "YEDEK")
    havuzlar = {
        "AMD": (client, AMD_MODEL_LIST or [AMD_MODEL]),
        "ZAI": (zai_client, ZAI_MODEL_TERCIH),
        "NVID": (nvid_client, _havuz_modelleri(nvid_client, NVID_MODELS, NVID_MODEL_TERCIH, "NVIDIA") if nvid_client else NVID_MODELS),
        "YEDEK": (alt_client, _havuz_modelleri(alt_client, ALT_MODELS, GROQ_MODEL_TERCIH, "Groq") if alt_client else ALT_MODELS),
        "OR": (or_client, _havuz_modelleri(or_client, OR_MODELS, OR_MODEL_TERCIH, "OpenRouter",
                                           suzgec=lambda m: m.endswith(":free") and "small" not in m) if or_client else OR_MODELS),
    }
    istekler = []
    kuyruklar = []
    for etiket in sirasi:
        if etiket not in havuzlar:
            # Kalinti/eski etiket (orn. havuzdan cikarilmis saglayici) tum
            # gunluk raporu KeyError ile cokermesin; etiket sessizce atlanir.
            logger.warning("Bilinmeyen LLM saglayici etiketi atlandi: %s", etiket)
            continue
        saglayici, modeller = havuzlar[etiket]
        if saglayici is not None and modeller:
            kuyruklar.append([saglayici, list(modeller), etiket])

    # Saglayicilar araya serpistirilir (round-robin): deneme sirasi AMD, NVID,
    # OR, YEDEK, AMD, NVID, ... seklinde ilerler. Aksi halde ilk saglayicinin
    # tum modelleri deneme butcesini yiyip Groq/OpenRouter hic denenmeden
    # max_deneme tukenir (2026-09-25 kosusunda tam olarak bu oldu: 6 denemenin
    # 6'si da AMD+NVID timeout'una gitti, yanit veren Groq hic denenmedi).
    while kuyruklar:
        kalan = []
        for kuyruk in kuyruklar:
            saglayici, modeller, etiket = kuyruk
            istekler.append((saglayici, modeller.pop(0), etiket))
            if modeller:
                kalan.append(kuyruk)
        kuyruklar = kalan

    if not istekler:
        logger.error("Kullanilabilir LLM saglayicisi yok (AMD_API_KEY / ALT_API_KEY tanimli degil).")
        if fallback_on_fail:
            return "(LLM hizmetine ulaşılamadı — rapor şu an kısmi olarak oluşturuldu veya oluşturulamadı. Daha sonra tekrar deneyin.)"
        raise RuntimeError("Kullanilabilir LLM saglayicisi yok.")

    for deneme in range(1, max_deneme + 1):
        saglayici, model, etiket = istekler[(deneme - 1) % len(istekler)]
        try:
            logger.info("LLM cagrisi: %s model=%s deneme=%d/%d", etiket, model, deneme, max_deneme)

            # Z.ai GLM'de dusunme (thinking) modu kapatilmazsa token akip
            # gecikme/kotu cikti riski artiyor; kalan saglayicilara gonderilmez.
            ek = {"extra_body": {"thinking": {"type": "disabled"}}} if etiket == "ZAI" else {}
            resp = saglayici.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=max_tokens,
                **ek,
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
def _zai_call_ic(prompt):
    """Kullanicinin ZAI_API_KEY secret'iyla GLM'i cagirir; kaliteli/uzun
    gunluk rapor bu saglayicidan yazilir. Anahtar yoksa veya iki model de
    basarisiz olursa None doner (cagiran taraf mevcut llm_call zincirine duser).
    Cikti degenerasyon ve prompt sizmasi acisindan da dogrulanir."""
    anahtar = os.environ.get("ZAI_API_KEY", "")
    if not anahtar:
        return None
    client = OpenAI(api_key=anahtar, base_url="https://api.z.ai/api/paas/v4/",
                    timeout=300.0, max_retries=1)
    modeller = ([os.environ["ZAI_MODEL"]] if os.environ.get("ZAI_MODEL") else []) + ZAI_MODEL_TERCIH
    modeller = list(dict.fromkeys(modeller))  # tekrarlari at
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
                return _tekrar_satirlarini_temizle(icerik)
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


def _tekrar_satirlarini_temizle(metin: str) -> str:
    """LLM'in tablo/liste satirlarini aynen tekrar etme (dongu) egilimine karsi:
    birebir ayni satirlar tek örnege dusurulur. GLM birgun '6. Gunun Onerilen
    Hisseleri' tablosunda ayni satiri 25 kez yazarak 128 satir uretebildi;
    _looks_degenerate tablo-sablonu muafiyeti nedeniyle bunu yakalayamadi.
    Bu fonksiyon yayin oncesi son guvendir."""
    gorulen = set()
    temiz = []
    for satir in metin.split("\n"):
        anahtar = satir.strip()
        if len(anahtar) > 30 and anahtar in gorulen:
            continue  # birebir ayni satir: atla
        gorulen.add(anahtar)
        temiz.append(satir)
    sonuc = "\n".join(temiz)
    if len(sonuc) < len(metin) - 40:
        logger.info("[Temizlik] tekrar eden %d karakter satir dusuruldu", len(metin) - len(sonuc))
    return sonuc


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
        # Modelin tablo/portfoy altina ekledigi "(Not: ...)" dipnotlari
        # (yarim veya tekrarli sablon cumleleri): bilgi tasimadigi icin dusur.
        _nk = re.sub(r"^[\s*\-#>]+", "", s)
        if _nk.startswith("(Not:") and (
            "en güvenli liman" in _nk
            or "satırı bulunmaktadır" in _nk
            or "sinyali veren his" in _nk
            or not _nk.endswith(")")
        ):
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


# ---------- PIYASA VERISI (endeks + kur: rapor acilisi ve pano icin) ----------

_piyasa_onbellek = None  # proses icinde bir kez hesaplanir


# ---------- MAKRO VERI TOPLAYICI (yalnizca akşam snapshot scripti) ----------
# TradingView + TÜİK verilerini data/makro.json'a toplar. Rapor üretimi bu
# fonksiyonu çağırmaz; akşam workflow'u canlı veriyi makro_snapshot.py ile
# data/makro-snapshot.json ve data/makro-gecmis/ altına sabitler.
MAKRO_ULKELER = {"TR": "Türkiye", "US": "ABD", "EU": "Euro Bölgesi"}
_MAKRO_ONBELLEK = None

# ---------- TUIK SDMX (TR resmi öncelikli kaynak / SDMX) ----------
# TradingView ana kaynak kalmaya devam eder; TUIK yalnizca Turkiye
# satirlarini resmi degerle dogrular (fark >0.05 puan ise TUIK kazanir)
# ve TV'nin vermedigi UFE satirini tamamlar. TUIK_API_KEY yoksa veya
# istek hata verirse adim sessizce atlanir, TV verisi oldugu gibi yazilir.
# Kimlik: Keycloak (giris.tuik.gov.tr, client_id=nsi-ws-consumer, 300 sn
# token) + SDMX REST (nsiws.tuik.gov.tr/rest/data/...); kutuphane gerekmez.
_TUIK_TOKEN = {"deger": "", "bitis": 0.0}
TUIK_DATAFLOW = {
    "Enflasyon (yıllık)": "DF_TUFE_SDMX_TT03",  # TUEFE genel, yillik degisim
    "ÜFE (yıllık)": "DF_UFE_SANAYI_V2",          # Toplam UFE (Yİ-ÜFE+YD-ÜFE)
}
# TÜİK veri akışı → resmi snapshot göstergesi. TUIK_API_KEY varsa bu
# kayıtlar TradingView karşılığını ezer; akış/ölçüt bulunamazsa atlanır.
# Seçiciler SDMX boyut adlarındaki İngilizce/Türkçe karşılıklara göre
# uygulanır; hiçbir eşleşme bulunamazsa veri uydurulmaz.
TUIK_SERIE_TANIMLARI = {
    "inflation_yoy": {
        "dataflow": "DF_TUFE_SDMX_TT03", "select": ("total", "annual rate of change"),
        "match": ("total", "annual", "change"),
    },
    "inflation_mom": {
        "dataflow": "DF_TUFE_SDMX_TT03", "select": ("total", "monthly rate of change"),
        "match": ("total", "month", "change"),
    },
    "core_inflation_yoy": {
        "dataflow": "DF_TUFE_SDMX_TT03",
        "select": ("excluding food and energy", "gıda ve enerji dışı"),
        "match_any": ("excluding food and energy", "gıda ve enerji dışı"),
    },
    "producer_prices_yoy": {
        "dataflow": "DF_UFE_SANAYI_V2", "select": ("total", "annual rate of change"),
        "match": ("total", "annual", "change"),
    },
    "unemployment_rate": {
        "dataflow": "DF_ISGUCU_AYLIK_TAMAMLAYICI_GOSTERGE_C",
        "select": ("total", "unemployment rate"),
        "match": ("unemployment", "rate"),
    },
    "participation_rate": {
        "dataflow": "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_C",
        "select": ("total", "labour force participation rate"),
        "match": ("participation", "rate"),
    },
    "industrial_production_yoy": {
        "dataflow": "DF_SANAYI_URETIM_ENDEKS_ANA_C",
        "select": ("total", "annual rate of change"),
        "match": ("total", "annual", "change"),
    },
    "industrial_production_mom": {
        "dataflow": "DF_SANAYI_URETIM_ENDEKS_ANA_C",
        "select": ("total", "monthly rate of change"),
        "match": ("total", "month", "change"),
        # Endeks seviyesi satiri ("... index (2021=100)") ayni donemde
        # % degisim satiriyla yarisiyordu; kesinlikle eleme yapilir.
        "exclude": ("index", "endeks", "=100", "annual rate of change"),
    },
}
_TUIK_YAPI_ONBELLEK = {}
_TUIK_TOPLAM = ("genel", "toplam", "all items", "total")
_TUIK_YILLIK = ("yillik", "annual", "same period", "onceki yil", "yoy")


def _tuik_duz(metin):
    """Aksansiz kucuk harf bicimi ('Yillik'/'yıllık' -> 'yillik' eslesmesi)."""
    import unicodedata
    m = unicodedata.normalize("NFKD", str(metin)).lower().replace("ı", "i")
    return "".join(ch for ch in m if not unicodedata.combining(ch))


def _tuik_token(tazele=False):
    """TUIK_API_KEY ile Keycloak access token alir (300 sn onbellek).

    Anahtar yoksa bos doner (TUIK adimi pas gecilir); `tazele=True` ile
    süresi dolmak uzere olan token yeniden alinir.
    """
    anahtar = os.environ.get("TUIK_API_KEY", "").strip()
    if not anahtar:
        return ""
    simdi = time.time()
    if not tazele and _TUIK_TOKEN["deger"] and simdi < _TUIK_TOKEN["bitis"]:
        return _TUIK_TOKEN["deger"]
    import urllib.request
    import urllib.parse
    veri = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": "nsi-ws-consumer",
        "api_key": anahtar,
    }).encode("utf-8")
    istek = urllib.request.Request(
        "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token",
        data=veri,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(istek, timeout=20) as yanit:
        cevap = json.loads(yanit.read().decode("utf-8"))
    _TUIK_TOKEN["deger"] = cevap["access_token"]
    _TUIK_TOKEN["bitis"] = simdi + max(60, int(cevap.get("expires_in", 300)) - 30)
    return _TUIK_TOKEN["deger"]


def _tuik_yapi(akis_id):
    """Bir dataflow'un SDMX boyut yapısını token ile alır ve cache'ler."""
    global _TUIK_YAPI_ONBELLEK
    if akis_id in _TUIK_YAPI_ONBELLEK:
        return _TUIK_YAPI_ONBELLEK[akis_id]
    token = _tuik_token()
    if not token:
        return None
    import urllib.request
    import urllib.error
    url = (f"https://nsiws.tuik.gov.tr/rest/data/TR,{akis_id},1.0/"
           "?detail=nodata")

    def _cek(jeton):
        istek = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {jeton}",
                          "Accept": "application/json"})
        with urllib.request.urlopen(istek, timeout=60) as yanit:
            return json.loads(yanit.read().decode("utf-8"))

    try:
        try:
            ham = _cek(token)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            ham = _cek(_tuik_token(tazele=True))
        yapi = []
        for tur in ("series", "observation"):
            for d in (ham.get("structure", {}).get("dimensions", {})
                      .get(tur, [])):
                yapi.append({
                    "id": d.get("id", ""), "name": d.get("name", ""),
                    "type": tur, "position": d.get("keyPosition",
                                                     d.get("position", 0)),
                    "values": [
                        {"id": str(v.get("id", "")),
                         "name": str(v.get("name", v.get("id", "")))}
                        for v in d.get("values", [])
                    ],
                })
        yapi.sort(key=lambda d: d["position"])
        if not yapi:
            return None
        _TUIK_YAPI_ONBELLEK[akis_id] = yapi
        return yapi
    except Exception as exc:
        logger.warning("[Makro] TÜİK veri akışı %s alınamadi: %s", akis_id, exc)
        return None


def _tuik_kodlar(dimension, seciciler):
    """Bir SDMX boyutunda seçiciye uyan kodları bulur; uyuşmazsa None."""
    if not seciciler:
        return None
    norm = [_tuik_duz(x) for x in seciciler]
    degerler = [(value, _tuik_duz(f"{value.get('id', '')} "
                                    f"{value.get('name', '')}"))
                for value in dimension.get("values", [])]
    for token in norm:
        exact = [value["id"] for value, metin in degerler
                 if _tuik_duz(value.get("name", "")) == token
                 or _tuik_duz(value.get("id", "")) == token]
        if exact:
            return exact
    eslesen = [value["id"] for value, metin in degerler
               if any(token in metin for token in norm)]
    return eslesen or None


def _tuik_key(yapi, seciciler):
    """Seçicilerden sunucu tarafı SDMX anahtarı üretir."""
    if not yapi:
        return None, False
    series = [d for d in yapi if d.get("type") == "series"]
    parts, eslesme_var = [], False
    for d in series:
        secili = _tuik_kodlar(d, seciciler)
        if secili:
            eslesme_var = True
            parts.append("+".join(secili))
        else:
            parts.append("+".join(v["id"] for v in d.get("values", [])))
    if not series or not eslesme_var:
        return None, False
    return ".".join(parts), True



def _tuik_satirlar(ham):
    """SDMX-JSON veriSetini düz satırlara çevirir."""
    structure = ham.get("structure", {})
    dims = []
    for tur in ("series", "observation"):
        for d in structure.get("dimensions", {}).get(tur, []):
            dims.append({
                "id": d.get("id", ""), "type": tur,
                "position": d.get("keyPosition", d.get("position", 0)),
                "values": {str(i): str(v.get("name", v.get("id", "")))
                           for i, v in enumerate(d.get("values", []))},
            })
    obs_dims = sorted((d for d in dims if d["type"] == "observation"),
                      key=lambda d: d["position"])
    datasets = ham.get("dataSets") or [{}]
    ds = datasets[0] if isinstance(datasets, list) else datasets
    satirlar = []

    def _ekle(series_key, obs_key, value):
        ser_parca = str(series_key).split(":") if series_key is not None else []
        obs_parca = str(obs_key).split(":") if obs_key is not None else []
        row = {}
        for d in dims:
            parca = ser_parca if d["type"] == "series" else obs_parca
            if d["type"] == "series":
                idx_sira = d["position"]
            else:
                idx_sira = next((i for i, x in enumerate(obs_dims)
                                 if x["id"] == d["id"]), -1)
            if idx_sira < 0 or idx_sira >= len(parca):
                continue
            try:
                row[d["id"]] = d["values"].get(str(int(parca[idx_sira])),
                                                  parca[idx_sira])
            except (TypeError, ValueError):
                continue
        try:
            if isinstance(value, list):
                value = value[0] if value else None
            if isinstance(value, dict):
                value = value.get("value")
            row["value"] = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(row["value"]):
            satirlar.append(row)

    for series_key, series_value in (ds.get("series") or {}).items():
        for obs_key, obs_value in (series_value.get("observations") or {}).items():
            _ekle(series_key, obs_key, obs_value)
    for obs_key, obs_value in (ds.get("observations") or {}).items():
        _ekle(None, obs_key, obs_value)
    return satirlar


def _tuik_veri(akis_id, seciciler, end_period=None):
    """Filtreli TÜİK SDMX verisini son gözlemleriyle çeker."""
    yapi = _tuik_yapi(akis_id)
    key, secildi = _tuik_key(yapi, seciciler)
    if not secildi:
        return []
    token = _tuik_token()
    if not token:
        return []
    import urllib.request
    import urllib.error
    import urllib.parse
    query = {"lastNObservations": "3"}
    if end_period:
        query["endPeriod"] = str(end_period)[:10]
    query = urllib.parse.urlencode(query)
    url = (f"https://nsiws.tuik.gov.tr/rest/data/TR,{akis_id},1.0/"
           f"{key}?{query}")

    def _cek(jeton):
        istek = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {jeton}",
                          "Accept": "application/json"})
        with urllib.request.urlopen(istek, timeout=90) as yanit:
            return json.loads(yanit.read().decode("utf-8"))

    try:
        try:
            ham = _cek(token)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            ham = _cek(_tuik_token(tazele=True))
        return _tuik_satirlar(ham)
    except Exception as exc:
        logger.warning("[Makro] TÜİK veri %s alinamadi: %s", akis_id, exc)
        return []


def _tuik_donem(deger):
    metin = str(deger or "")
    if len(metin) >= 7 and metin[4] == "-":
        return metin[:7] + "-01"
    return metin[:10]


def _tuik_seride_guncel(gosterge, as_of=None):
    tanim = TUIK_SERIE_TANIMLARI.get(gosterge)
    if not tanim:
        return None
    satirlar = _tuik_veri(tanim["dataflow"], tanim.get("select", ()),
                           end_period=as_of)
    if not satirlar:
        return None
    aday = []
    for row in satirlar:
        row_text = _tuik_duz(" ".join(str(v) for k, v in row.items()
                                    if k != "value"))
        match = tanim.get("match")
        match_any = tanim.get("match_any")
        if match and not all(_tuik_duz(token) in row_text for token in match):
            continue
        if match_any and not any(_tuik_duz(token) in row_text
                                 for token in match_any):
            continue
        exclude = tanim.get("exclude")
        if exclude and any(_tuik_duz(token) in row_text for token in exclude):
            continue
        donem = _tuik_donem(row.get("TIME_PERIOD"))
        try:
            datetime.strptime(donem, "%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        if as_of and donem[:10] > str(as_of)[:10]:
            continue
        aday.append((donem, float(row["value"]), row))
    if not aday:
        return None
    donem, deger, row = max(aday, key=lambda x: x[0])
    return {"indicator": gosterge,
            "label": makro_katalog.KOD_GOSTERGE[gosterge][1],
            "value": round(deger, 4),
            "unit": makro_katalog.KOD_GOSTERGE[gosterge][2],
            "period": donem, "source_title": tanim["dataflow"],
            "source_period": str(row.get("TIME_PERIOD") or donem),
            "frequency": "TÜİK"}


def _tuik_satir_bul(ham):

    """SDMX-JSON govdesinden genel + yillik en yeni (deger, donem) satirini secer.

    Boyut adlari yanittaki structure uzerinden cozulur; toplam serisi
    (genel/toplam) ve yillik degisim satiri kalip eslesmesiyle filtrelenir.
    Iki filtre birden bulunamazsa ya da secim belirsizse None doner:
    yanlis rakam yazmaktansa TUIK adimi atlanir.
    """
    yapi = (ham.get("structure") or {}).get("dimensions") or {}
    seri_boyut = sorted(yapi.get("series", []),
                        key=lambda d: d.get("keyPosition", d.get("position", 0)))
    goz_boyut = sorted(yapi.get("observation", []),
                       key=lambda d: d.get("keyPosition", d.get("position", 0)))
    donem_pos = next((i for i, d in enumerate(goz_boyut)
                      if d.get("id") == "TIME_PERIOD"), None)
    if donem_pos is None:
        return None

    def _esles(boyut, kaliplar):
        bulunan = []
        for idx, v in enumerate(boyut.get("values", [])):
            metin = _tuik_duz(f"{v.get('id', '')} {v.get('name', '')}")
            if any(k in metin for k in kaliplar):
                bulunan.append((idx, metin))
        return bulunan

    toplam_f = {}   # (kapsam, boyut_pos) -> izinli deger indexleri
    yillik_f = {}
    for kapsam, boyutlar in (("seri", seri_boyut), ("gozlem", goz_boyut)):
        for pos, d in enumerate(boyutlar):
            if kapsam == "gozlem" and d.get("id") == "TIME_PERIOD":
                continue
            t = [i for i, _ in _esles(d, _TUIK_TOPLAM)]
            if t:
                toplam_f[(kapsam, pos)] = t
            y = _esles(d, _TUIK_YILLIK)
            # "Yillik katki (puan)" gibi katki satirlari deger degildir
            oran = [i for i, m in y
                    if "katki" not in m and "contribution" not in m
                    and "puan" not in m]
            if oran:
                yillik_f[(kapsam, pos)] = oran
            elif y:
                yillik_f[(kapsam, pos)] = [i for i, _ in y]
    if not yillik_f:
        logger.debug("[Makro] TÜİK yillik boyutu bulunamadi")
        return None
    seri_sayisi = sum(len(v.get("series", {}) or {})
                      for v in (ham.get("dataSets") or []))
    if not toplam_f and seri_sayisi != 1:
        logger.debug("[Makro] TÜİK toplam serisi secilemedi (%d seri)",
                     seri_sayisi)
        return None

    kisitlar = list(toplam_f.items()) + list(yillik_f.items())
    satirlar = []
    for veri_seti in (ham.get("dataSets") or []):
        for seri_anahtar, seri in (veri_seti.get("series") or {}).items():
            if not seri_boyut:
                seri_idx = []
            else:
                try:
                    seri_idx = [int(p) for p in str(seri_anahtar).split(":")]
                except ValueError:
                    continue
            if any(kapsam == "seri" and pos < len(seri_idx)
                   and seri_idx[pos] not in izinli
                   for (kapsam, pos), izinli in kisitlar):
                continue
            for goz_anahtar, goz_deger in (seri.get("observations") or {}).items():
                try:
                    goz_idx = [int(p) for p in str(goz_anahtar).split(":")]
                except ValueError:
                    continue
                if len(goz_idx) <= donem_pos:
                    continue
                if any(kapsam == "gozlem" and pos < len(goz_idx)
                       and goz_idx[pos] not in izinli
                       for (kapsam, pos), izinli in kisitlar):
                    continue
                try:
                    donem = goz_boyut[donem_pos]["values"][
                        goz_idx[donem_pos]]["id"]
                    deger = float(goz_deger[0])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                satirlar.append((str(donem), deger))
    if not satirlar:
        return None
    en_yeni = max(d for d, _ in satirlar)
    adaylar = {round(v, 4) for d, v in satirlar if d == en_yeni}
    if len(adaylar) != 1:
        logger.debug("[Makro] TÜİK secim belirsiz (%d farkli deger)",
                     len(adaylar))
        return None
    return (next(v for d, v in satirlar if d == en_yeni), en_yeni)


def _tuik_resmi(akis_id, baslangic_ay=14):
    """Bir dataflow icin en guncel genel/yillik (deger, donem) satirini ceker.

    Bos anahtar (tum seriler) + lastNObservations ile sinirli govde alinir;
    401/403'te token bir kez tazelenir. Alinamazsa None.
    """
    import urllib.request
    import urllib.error
    token = _tuik_token()
    if not token:
        return None
    bas = (datetime.now() - timedelta(days=31 * baslangic_ay)).strftime("%Y-%m")
    import urllib.parse
    sorgu = urllib.parse.urlencode({"startPeriod": bas,
                                    "lastNObservations": "3"})
    url = (f"https://nsiws.tuik.gov.tr/rest/data/TR,{akis_id},1.0/"
           f"?{sorgu}")

    def _cek(jeton):
        istek = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {jeton}",
                          "Accept": "application/json"})
        with urllib.request.urlopen(istek, timeout=60) as yanit:
            return json.loads(yanit.read().decode("utf-8"))

    try:
        try:
            ham = _cek(token)
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            ham = _cek(_tuik_token(tazele=True))
    except Exception as e:
        logger.warning("[Makro] TÜİK %s alinamadi: %s", akis_id, e)
        return None
    sonuc = _tuik_satir_bul(ham)
    if sonuc:
        logger.info("[Makro] TÜİK %s: %s (%s)", akis_id,
                    round(sonuc[0], 2), sonuc[1])
    return sonuc


def _tuik_teyit(gostergeler, as_of=None):
    """TÜİK resmi gözlemlerini TR snapshot satırlarına uygular."""
    if not os.environ.get("TUIK_API_KEY", "").strip():
        return False
    katki = False
    for gosterge, tanim in TUIK_SERIE_TANIMLARI.items():
        resmi = _tuik_seride_guncel(gosterge, as_of=as_of)
        if not resmi:
            continue
        ad = resmi["label"]
        mevcut = next((g for g in gostergeler
                       if g.get("ulke") == "Türkiye"
                       and g.get("ad") == ad), None)
        ortak = {
            "deger": resmi["value"], "birim": resmi["unit"],
            "donem": resmi["period"], "tahmin": None, "onceki": None,
            "kaynak_baslik": resmi["source_title"],
            "kaynak_periyot": resmi["source_period"],
            "frekans": resmi["frequency"], "teyit": "TÜİK",
            "kaynak_durumu": "TÜİK resmi verisi",
        }
        if mevcut is None:
            yeni = {"ulke": "Türkiye", "ad": ad, **ortak}
            son_tr = max((i for i, g in enumerate(gostergeler)
                          if g.get("ulke") == "Türkiye"),
                         default=len(gostergeler) - 1)
            gostergeler.insert(son_tr + 1, yeni)
            logger.info("[Makro] TÜİK %s satırı tamamlandı: %s (%s)",
                        gosterge, yeni["deger"], yeni["donem"])
        else:
            fark = abs(float(mevcut.get("deger")) - resmi["value"])
            if fark > 0.0005:
                logger.warning("[Makro] %s farkı: TV %s / TÜİK %s (%s) -> TÜİK",
                               gosterge, mevcut.get("deger"), resmi["value"],
                               resmi["period"])
            mevcut.update(ortak)
        katki = True
    return katki


def _tv_optional_sayi(deger):
    try:
        if deger in (None, ""):
            return None
        return float(deger)
    except (TypeError, ValueError):
        return None


def _tv_birim(gosterge, event):
    """TradingView birimini snapshot'ta anlaşılır Türkçe birime cevirir."""
    katalog_birimi = makro_katalog.KOD_GOSTERGE[gosterge][2]
    if katalog_birimi:
        return katalog_birimi
    kaynak_birimi = str(event.get("unit") or "").strip()
    if gosterge in {"current_account", "trade_balance", "exports", "imports",
                    "fx_reserves", "tourism_revenues"}:
        return {"$": "milyar $", "€": "milyar €",
                "TRY": "milyar TRY"}.get(kaynak_birimi, kaynak_birimi)
    if gosterge == "budget_balance" and kaynak_birimi == "TRY":
        return "milyar TRY"
    return kaynak_birimi


def makro_cek(gun=170, yol="data/makro.json", as_of=None):
    """Ulke bazinda son yayinlanan makro verileri ceker.

    Kaynak: TradingView ekonomik takvimi; TUIK_API_KEY varsa Türkiye için TÜİK
    SDMX veri akışları öncelikli resmi kaynak olarak kullanılır. TÜİK'te
    karşılığı bulunan gösterge, seçilmiş toplam/değişim ölçütü ve veri dönemi
    ile snapshot'a yazılır. TÜİK olmayan/erişilemeyen göstergeler TradingView
    son gözlemiyle devam eder; ölçüt bulunamazsa değer uydurulmaz.
    """
    global _MAKRO_ONBELLEK
    if _MAKRO_ONBELLEK is not None:
        return _MAKRO_ONBELLEK or None
    import urllib.request
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = as_of or datetime.now(tz).strftime("%Y-%m-%d")
    frm = (datetime.strptime(bugun, "%Y-%m-%d") - timedelta(days=gun)).strftime("%Y-%m-%d")
    gostergeler = []
    try:
        for kod, ulke_ad in MAKRO_ULKELER.items():
            url = ("https://economic-calendar.tradingview.com/events"
                   f"?from={frm}T00%3A00%3A00.000Z&to={bugun}T00%3A00%3A00.000Z&countries={kod}")
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0",
                              "Origin": "https://www.tradingview.com"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.load(r)
            olaylar = d.get("result")
            if isinstance(olaylar, dict):
                olaylar = olaylar.get("events", [])
            for gosterge, ad, varsayilan_birim, baslik_listesi, frekans in \
                    makro_katalog.GOSTERGE_TANIMLARI:
                adaylar = [e for e in olaylar
                           if str(e.get("title", "")).strip().lower() in baslik_listesi
                           and str(e.get("date", ""))[:10] <= str(bugun)
                           and e.get("actual") not in (None, "", 0)]
                if not adaylar:
                    continue
                son = max(adaylar, key=lambda e: str(e.get("date", "")))
                deger = _tv_optional_sayi(son.get("actual"))
                if deger is None:
                    continue
                gostergeler.append({
                    "ulke": ulke_ad, "ad": ad, "deger": round(deger, 4),
                    "birim": _tv_birim(gosterge, son), "donem": str(son.get("date", ""))[:10],
                    "tahmin": _tv_optional_sayi(son.get("forecast")),
                    "onceki": _tv_optional_sayi(son.get("previous")),
                    "kaynak_baslik": str(son.get("title", "")),
                    "kaynak_periyot": str(son.get("period") or ""),
                    "frekans": frekans,
                })
    except Exception as e:
        logger.warning("[Makro] ekonomik takvim alinamadi: %s", e)

    # Zorunlu göstergelerde farklı revizyon başlıkları eski bir gözlemi
    # getirebilir. Daha yeni tarihli mevcut gerçekleşmiş gözlem varsa onu koru.
    try:
        with open(yol, encoding="utf-8") as f:
            onceki_veri = json.load(f).get("gostergeler") or []
        for ulke_kodu, gerekli in makro_katalog.REQUIRED.items():
            ulke_ad = MAKRO_ULKELER[ulke_kodu]
            for gosterge in gerekli:
                ad = makro_katalog.KOD_GOSTERGE[gosterge][1]
                mevcut = next((g for g in gostergeler
                              if g.get("ulke") == ulke_ad and g.get("ad") == ad), None)
                onceki = next((g for g in onceki_veri
                              if g.get("ulke") == ulke_ad and g.get("ad") == ad), None)
                if not onceki:
                    continue
                if mevcut is None or str(onceki.get("donem", "")) > str(mevcut.get("donem", "")):
                    if mevcut is not None:
                        gostergeler.remove(mevcut)
                    gostergeler.append(dict(onceki, kaynak_durumu="son gözlem korundu"))
    except (OSError, json.JSONDecodeError):
        pass

    if gostergeler:
        # TÜİK resmi teyidi (yalnızca TR): farkta resmi deger yazilir,
        # eksik UFE satiri tamamlanir; anahtar/hata yoksa TV verisi aynen kalir.
        tuik_ek = ""
        try:
            if _tuik_teyit(gostergeler, as_of=bugun):
                tuik_ek = " + TÜİK SDMX teyitli"
        except Exception as e:
            logger.warning("[Makro] TÜİK teyidi atlandi: %s", e)
        # EVDS (opsiyonel): anahtar/kesif yoksa hicbir sey eklenmez; snapshot
        # TV/TÜİK verisiyle aynen yazilir. Eklenen kayitlar ayni semadan gece.
        try:
            import evds_veri
            evds_sayi = evds_veri.gostergeleri_ekle(gostergeler, bugun)
            if evds_sayi:
                tuik_ek += f" + EVDS({evds_sayi})"
        except Exception as e:
            logger.warning("[Makro] EVDS atlandi: %s", e)
        # Resmi AB/ABD kanallari (Eurostat/ECB, BLS/FRED/BEA): taze olduklari
        # surede TV satirlarinin UZERINE yazar, TV'de olmayan satiri ekler.
        # Anahtar/istek yoksa ya da resmi veri bayatsa TV satiri aynen kalir.
        try:
            import resmi_veri
            resmi_sayi = resmi_veri.gostergeleri_ekle(gostergeler, bugun)
            if resmi_sayi:
                tuik_ek += f" + Resmi AB/ABD({resmi_sayi})"
        except Exception as e:
            logger.warning("[Makro] Resmi AB/ABD adimi atlandi: %s", e)
        govde = {"guncelleme": bugun + " " + datetime.now(tz).strftime("%H:%M"),
                 "kaynak": "TradingView ekonomik takvimi" + tuik_ek,
                 "gostergeler": gostergeler}
        try:
            with open(yol, "w", encoding="utf-8") as f:
                json.dump(govde, f, ensure_ascii=False, indent=1)
        except OSError:
            logger.warning("[Makro] %s yazilamadi", yol)
        canli = dict(govde, _veri_durumu="canli")
        _MAKRO_ONBELLEK = canli
        return canli
    try:  # ag yoksa son bilinen veri
        with open(yol, encoding="utf-8") as f:
            govde = json.load(f)
        if govde.get("gostergeler"):
            govde["_veri_durumu"] = "cache"
            logger.info("[Makro] canli veri yok; cache kullaniliyor (%d gosterge)",
                        len(govde["gostergeler"]))
            _MAKRO_ONBELLEK = govde
            return govde
    except (OSError, json.JSONDecodeError):
        pass
    _MAKRO_ONBELLEK = False
    return None


_MAKRO_SNAPSHOT_ONBELLEK = {}


def makro_snapshot_cek(expected_report_date=None, yenile=False):
    """Uretimde kullanilacak tek makro veri kaynagini dogrular.

    `expected_report_date` verilirse snapshot'in o rapor gunune ait oldugu
    zorunlu kontrol edilir. Ag cagrisi veya canli fallback yapilmaz. Ayni
    surec icinde snapshot nesnesi paylasilir; prompt ve dogrulama ayni veriyi
    kullanir.
    """
    if expected_report_date is None:
        expected_report_date = datetime.now(
            zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    if not yenile and expected_report_date in _MAKRO_SNAPSHOT_ONBELLEK:
        return _MAKRO_SNAPSHOT_ONBELLEK[expected_report_date]
    snapshot = makro_veri.load_for_report(expected_report_date)
    _MAKRO_SNAPSHOT_ONBELLEK[expected_report_date] = snapshot
    return snapshot


def makro_cerceve_metni(expected_report_date=None):
    """Snapshot'i LLM'e kilitli, degistirilemez metin cercevesine cevirir."""
    return makro_veri.frame_text(
        makro_snapshot_cek(expected_report_date=expected_report_date))


MAKRO_AKTARIM_KILAVUZU = (
    "[MAKRO AKTARIM ZINCIRI - UZMAN USLUBU]\n"
    "Makro veriyi hisse/sektor sonucuna baglarken su zinciri kur, atlama:\n"
    "  Enflasyon -> faiz beklentisi -> reel faiz -> kredi buyumesi -> ic talep -> sirket satislari -> marjlar -> degerleme\n"
    "  Banka: politika faizi -> mevduat maliyeti -> kredi fiyatlamasi -> net faiz marji -> takipteki alacaklar -> ozkaynak karliligi\n"
    "  Sanayi: kur -> ithal girdi maliyeti -> ihracat geliri -> brut marj -> isletme sermayesi -> borcluluk\n"
    "Her bolumde uc soruya net cevap ver:\n"
    "  1) Bugun dunden farkli olan ne? 2) Hangi veri bu gorusu destekliyor? 3) Bu gorus hangi kosulda gecersiz olur?\n"
    "Makro veri ile sinyal celisirse bunu ACIKCA yaz (orn. 'teknik sinyal pozitif ama net faiz marji teyidi yok')."
)


# Gunluk rapor ve derin analiz promptlarina ortak gomulen veri-guvenligi
# envanteri: modelin kesinlikle uydurmamasi gereken alanlar ve blogta rakam
# olmadiginda ne yapacagi. 8-21 Eylul 2026 tarihli uydurma endeks seviyeleri
# (3.600-4.400 araligi) prompt'a gercek piyasa verisi girilmedigi icin
# uretilmisti; bu blog o acigi kapatir.
VERI_DAYANAK_ENVANTERI = (
    "[VERI GUVENLIGI - ASLA UYDURMAYACAGIN ALANLAR]\n"
    "Asagidaki alanlarda her rakam yalnizca VERILER bolumundeki karsilik blogdan alinir; "
    "kendi hafizandan, egitim verinden veya tahminle rakam URETME:\n"
    "  1) Endeks seviyeleri ve yuzdeleri (XU030, XU100, dolar bazli getiri), USD/TRY kuru -> [BUGUNUN TARIHI VE PIYASA VERILERI]\n"
    "  2) Hisse fiyatlari, yuzdelik degisimler, direnç/destek ve kanal seviyeleri, osilator degerleri -> teknik tarama ve osilator bloglari\n"
    "  3) Bilanco, kar, satis, rasyo, piyasa degeri -> [TEMEL / FINSANSAL VERILER] (bu blog raporda mevcutsa)\n"
    "  4) Enflasyon, faiz, PMI, istihdam, kur, tahvil faizi gibi makro sayilar -> [MAKRO GEREKLER]\n"
    "  5) Tarih ve gun adi -> yalnizca [BUGUNUN TARIHI VE PIYASA VERILERI]\n"
    "  6) Sirket unvanlari -> yalnizca verideki hisse kodunun yanindaki resmi ad\n"
    "  7) Portfoy agirliklari ve performans rakamlari -> [PORTFOY DURUMU] / portfoy tablosu\n"
    "  8) Gecmis gunlere ait bilgiler -> [HAFIZA] ozetleri; bunlari bugunun verisi gibi sunma.\n"
    "BLOKTA OLMAYAN bir rakami asla tahmin etme, yuvarlayarak tamamlama veya hatirladigin eski bir degerle "
    "ikame etme: o rakami YAZMA. Gerekirse nitel ifade kullan ('veri setinde yer almiyor', 'bugun olculmedi') "
    "ya da o rakami gerektirmeyen bir cumle kur. Bloglar celisirse [BUGUNUN TARIHI VE PIYASA VERILERI] "
    "bloğundaki kesin deger esastir; celiskiyi metinde acikca belirt."
)


# Raporun genel yazim kalitesi: profesyonel bulten / bas analist seviyesi;
# VERI_DAYANAK_ENVANTERI'ndeki veri disiplininin yaninda kullanilir.
PROFESYONEL_YAZIM_KURALLARI = (
    "[PROFESYONEL YAZIM STANDARTI - BAS ANALIST / PORTFOY YONETIMI SEVIYESI]\n"
    "  - Kanit once: her iddia, tavsife ve sayisal atif bir VERI satirina dayanir; dayanagi olmayan "
    "yargi cumlesi kurma ('veride su goruluyor -> su anlamina gelir -> su kosulda gecersiz olur').\n"
    "  - Kesinlik ve abarti dili yasak: 'kesin', 'garanti', 'mutlaka', 'kacinilmaz', 'ucacak', 'dibi gorecek' "
    "yerine olasilik ve kosul dili kullan; firsatlarla riskleri ayni agirlikta yaz.\n"
    "  - Birim ve bicim tutarliligi: her rakam birimiyle yazilsin (TL, bin TL, milyar TL, %, baz puan); "
    "metin boyunca binlik/ayrac bicimi degistirmez (TR raporu: 16.217, %2,4 bicimi).\n"
    "  - Ic tutarliblik: metinde tekrar eden her rakam birebir ayni olmali; bolum sonuclari, tablolar, "
    "portfoy agirliklari, nakit orani ve senaryolar birbirini yalanlamamali.\n"
    "  - Kesin olmayan degerleri 'yaklasik', '~' veya aralikla isaretle; veri setindeki yuvarlamayi ve "
    "ondalık basamak sayisini keyfi degistirme.\n"
    "  - Uslup: sakin, olculu, profesyonel bulten dili; dolgu ve tekrar cumlesi yok; her bolum diger "
    "bolumlerle ayni tonda; okur 'neden boyle dusunuluyor' sorusunun cevabini metinde bulabilmeli.\n"
    "  - Portfoy yonetimi perspektifi: onerileri getiri-risk dengesi ve pozisyon etkisiyle gerekcelendir; "
    "yalnizca yon degil, oneri hangi somut kosulda gecersizlesir de yaz."
)


def makro_metni(veri=None):
    """Snapshot verisini promptlara gomulecek kilitli cerceveye cevirir.

    Eski imza geriye uyumluluk icin korunur; `veri` snapshot semasindaysa
    dogrudan cercevelenir. `veri=None` ise yalnizca akşam snapshot'ini okur.
    """
    if isinstance(veri, dict) and veri.get("schema") == makro_veri.SCHEMA:
        return makro_veri.frame_text(veri)
    if veri is not None:
        satirlar = [f"- {g['ulke']} {g['ad']}: {g['deger']}{g['birim']} "
                    f"({g['donem']})" for g in veri.get("gostergeler", [])]
        return "\n".join(satirlar)
    return makro_cerceve_metni()


def piyasa_verisi():
    """XU030/XU100 son iki kapanisini, USD/TRY'yi ve endeksin dolar bazindaki
    gunluk performansini hesaplar. Rapor prompt'u ile pano ayni kesin rakamlari
    buradan alir; LLM'in tarih/seviye uydurmasi engellenir. Veri alinamazsa
    None doner (cagiran taraf sessizce atlar). borsapy cagrisi yavas oldugundan
    proses basina bir kez hesaplanip onbelleklenir."""
    global _piyasa_onbellek
    if _piyasa_onbellek is not None:
        return _piyasa_onbellek or None
    veri = {}
    try:
        import borsapy as bp
        for kod in ("XU030", "XU100"):
            try:
                ixh = bp.index(kod).history(period="3mo")
                kapanislar = ixh["Close"].astype(float).dropna() if ixh is not None else []
                if len(kapanislar) >= 2:
                    onceki, son = float(kapanislar.iloc[-2]), float(kapanislar.iloc[-1])
                    if onceki > 0:
                        veri[kod] = {"son": son, "onceki": onceki,
                                     "deg": (son / onceki - 1) * 100}
            except Exception as e:
                logger.warning("[Uyari] %s endeks verisi alinamadi: %s", kod, e)
    except Exception as e:
        logger.warning("[Uyari] borsapy endeks verisi alinamadi: %s", e)
    try:
        import yfinance as yf
        df_kur = yf.download("USDTRY=X", period="5d", progress=False)["Close"].ffill().dropna()
        if hasattr(df_kur, "columns"):
            # yfinance >= 0.2 MultiIndex kolon dondurur; tek seride indir.
            df_kur = df_kur.iloc[:, 0]
        if len(df_kur) >= 2:
            onceki, son = float(df_kur.iloc[-2]), float(df_kur.iloc[-1])
            if onceki > 0:
                veri["USDTRY"] = {"son": son, "onceki": onceki,
                                  "deg": (son / onceki - 1) * 100}
    except Exception as e:
        logger.warning("[Uyari] USDTRY verisi alinamadi: %s", e)
    if "XU030" in veri and "USDTRY" in veri:
        # TL getirisi kur hareketiyle duzeltildiginde dolar bazindaki getiri kalir.
        veri["XU030USD"] = {"deg": ((1 + veri["XU030"]["deg"] / 100)
                                    / (1 + veri["USDTRY"]["deg"] / 100) - 1) * 100}
    _piyasa_onbellek = veri if veri else False
    return veri or None


def piyasa_verisi_metni(snapshot=None):
    """Prompt'a piyasa verisini enjekte eder.

    Snapshot verildiyse aynı akşam kaydı kullanılır; verilmezse günlük
    raporların kullandığı canlı BIST/kur özeti üretilir.
    """
    if snapshot is not None:
        satirlar = ["[AKŞAM PİYASA SNAPSHOT — KİLİTLİ]"]
        for r in snapshot.get("piyasa") or []:
            onceki = r.get("previous")
            degisim = r.get("change")
            parcalar = [f"{r['label']}: {_ts(r['value'])}{r.get('unit', '')}",
                        f"önceki: {_ts(onceki)}{r.get('unit', '')}" if onceki is not None else None,
                        f"değişim: {_ty(degisim)}%" if degisim is not None else None,
                        f"kaynak: {r.get('source', '')} ({r.get('period', '')})"]
            satirlar.append("- " + " | ".join(p for p in parcalar if p))
        return "\n".join(satirlar) if len(satirlar) > 1 else ""
    pv = piyasa_verisi()
    if not pv:
        return ""
    bugun = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul"))
    satirlar = [f"Bugunun tarihi: {bugun.day} {_AYLAR[bugun.month - 1]} "
                f"{bugun.year}, {_GUN_ADLARI[bugun.weekday()]}, "
                f"saat {bugun.strftime('%H:%M')} (Turkiye saati)"]
    if "XU030" in pv:
        v = pv["XU030"]
        satirlar.append(f"XU030 (BIST 30): son kapanis {_ts(v['son'])} | "
                        f"onceki kapanis {_ts(v['onceki'])} | gunluk {_ty(v['deg'])}%")
    if "XU100" in pv:
        v = pv["XU100"]
        satirlar.append(f"XU100 (BIST 100): son kapanis {_ts(v['son'])} | "
                        f"onceki kapanis {_ts(v['onceki'])} | gunluk {_ty(v['deg'])}%")
    if "USDTRY" in pv:
        v = pv["USDTRY"]
        satirlar.append(f"USD/TRY: {_ts(v['son'])} | onceki {_ts(v['onceki'])} | "
                        f"gunluk {_ty(v['deg'])}%")
    if "XU030USD" in pv:
        satirlar.append(f"XU030 dolar bazinda gunluk performans: {_ty(pv['XU030USD']['deg'])}% "
                        "(TL getirisi kur hareketine gore duzeltilmis)")
    return "\n".join(satirlar)


def _yf_son_kapanislar(symbol, snapshot_date):
    """Yahoo Finance'de snapshot tarihi ve öncesindeki son iki kapanış."""
    import yfinance as yf
    import pandas as pd
    bitis = pd.Timestamp(snapshot_date) + pd.Timedelta(days=1)
    bas = bitis - pd.Timedelta(days=21)
    df = yf.download(symbol, start=bas.strftime("%Y-%m-%d"),
                     end=bitis.strftime("%Y-%m-%d"), progress=False,
                     auto_adjust=False)
    if df is None or len(df) == 0:
        return []
    close = df["Close"].dropna()
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    rows = [(pd.Timestamp(idx).date().isoformat(), float(v))
            for idx, v in close.items()
            if pd.Timestamp(idx).date() <= pd.Timestamp(snapshot_date).date()]
    return rows[-2:]


def _fred_son_tahvil_getirileri(snapshot_date):
    """FRED DGS2/DGS10 anahtarsız CSV'sinden son iki gözlem."""
    import csv
    import io
    import urllib.request
    import pandas as pd
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2,DGS10"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as yanit:
        tablo = csv.DictReader(io.StringIO(yanit.read().decode("utf-8")))
        satirlar = []
        for row in tablo:
            try:
                tarih = pd.Timestamp(row["observation_date"]).date()
                if tarih > pd.Timestamp(snapshot_date).date():
                    continue
                dgs2 = float(row["DGS2"])
                dgs10 = float(row["DGS10"])
            except (KeyError, TypeError, ValueError):
                continue
            satirlar.append((tarih.isoformat(), dgs2, dgs10))
        return satirlar[-2:]


def _fred_son_kapanislar(seri_ids, snapshot_date):
    """FRED anahtarsız CSV'sinden verilen serilerin son iki ortak gözlemi."""
    import csv
    import io
    import urllib.request
    import pandas as pd
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + ",".join(seri_ids)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as yanit:
        tablo = csv.DictReader(io.StringIO(yanit.read().decode("utf-8")))
        satirlar = []
        for row in tablo:
            try:
                tarih = pd.Timestamp(row["observation_date"]).date()
                if tarih > pd.Timestamp(snapshot_date).date():
                    continue
                degerler = [float(row[s]) for s in seri_ids]
            except (KeyError, TypeError, ValueError):
                continue
            satirlar.append((tarih.isoformat(), *degerler))
        return satirlar[-2:]


def _gram_altin_fiyati(ons_usd, usdtry):
    """Ons altin (USD) + USD/TRY -> TL/gram. 1 troy unce = 31.1034768 gram."""
    return ons_usd * usdtry / 31.1034768


def piyasa_serileri_ce(snapshot_date):
    """Akşam piyasa snapshot'ını gerçekten yayınlanan serilerle üretir."""
    import borsapy as bp
    import pandas as pd
    import logging as _logging
    kayitlar = []
    for symbol, kod, ad, birim, kategori in makro_katalog.PIYASA_SERILERI:
        try:
            satirlar = _yf_son_kapanislar(symbol, snapshot_date)
            if len(satirlar) < 2:
                continue
            (_onceki_tarih, onceki), (son_tarih, son) = satirlar[-2:]
            kayitlar.append({
                "symbol": symbol, "indicator": kod, "label": ad,
                "value": round(son, 6), "previous": round(onceki, 6),
                "change": round((son / onceki - 1) * 100, 4)
                           if onceki else None,
                "unit": birim, "period": son_tarih, "category": kategori,
                "source": "Yahoo Finance", "status": "close",
            })
        except Exception as exc:
            _logging.getLogger("makro-snapshot").warning(
                "Piyasa serisi %s alinamadi: %s", symbol, exc)

    # Altın/TL doğrudan Yahoo sembolü yok; USD/ons x USD/TRY türetilir.
    # (Eski hata: 1000.0 carpani gram fiyatini milyon katina sistiriyordu.)
    try:
        altin = next(r for r in kayitlar if r["indicator"] == "gold_usd")
        usdtry = next(r for r in kayitlar if r["indicator"] == "usdtry")
        gram = _gram_altin_fiyati(altin["value"], usdtry["value"])
        onceki_gram = _gram_altin_fiyati(altin["previous"], usdtry["previous"])
        kayitlar.append({
            "symbol": "GC=F*USDTRY=X", "indicator": "gold_try",
            "label": "Gram Altın (türetilmiş)", "value": round(gram, 4),
            "previous": round(onceki_gram, 4),
            "change": round((gram / onceki_gram - 1) * 100, 4),
            "unit": "TL/gram", "period": altin["period"], "category": "emtia",
            "source": "Yahoo Finance (türetilmiş)", "status": "derived",
        })
    except Exception:
        pass

    try:
        fred = _fred_son_tahvil_getirileri(snapshot_date)
        if len(fred) >= 2:
            (_prev_date, prev2, prev10) = fred[-2]
            last_date, last2, last10 = fred[-1]
            for kod, ad, deger, onceki in (
                ("ust2y", "ABD 2 Yıllık Tahvil", last2, prev2),
                ("ust10y", "ABD 10 Yıllık Tahvil", last10, prev10)):
                kayitlar.append({
                    "symbol": kod.upper(), "indicator": kod, "label": ad,
                    "value": round(deger, 4), "previous": round(onceki, 4),
                    "change": round(deger - onceki, 4), "unit": "%",
                    "period": last_date, "category": "faiz",
                    "source": "FRED", "status": "close",
                })
    except Exception as exc:
        _logging.getLogger("makro-snapshot").warning(
            "FRED tahvil getirileri alinamadi: %s", exc)

    # FRED kredi marjlari (OAS): investment grade + yüksek getirili.
    try:
        marjlar = _fred_son_kapanislar(
            ("BAMLC0A0CM", "BAMLH0A0HYM2"), snapshot_date)
        if len(marjlar) >= 2:
            (_, mprev0, mprev1) = marjlar[-2]
            mdate, m0, m1 = marjlar[-1]
            for kod, ad, deger, onceki in (
                ("corp_oas", "Şirket Tahvili Marjı", m0, mprev0),
                ("hy_oas", "Yüksek Getirili Tahvil Marjı", m1, mprev1),
            ):
                kayitlar.append({
                    "symbol": kod.upper(), "indicator": kod, "label": ad,
                    "value": round(deger, 4), "previous": round(onceki, 4),
                    "change": round(deger - onceki, 4), "unit": "%",
                    "period": mdate, "category": "küresel risk",
                    "source": "FRED", "status": "close",
                })
    except Exception as exc:
        _logging.getLogger("makro-snapshot").warning(
            "FRED kredi marjlari alinamadi: %s", exc)

    for symbol, kod, ad, birim in (
        ("XU030", "bist30", "BIST 30", "puan"),
        ("XU100", "bist100", "BIST 100", "puan"),
    ):
        try:
            seri = bp.index(symbol).history(period="3mo")
            closes = seri["Close"].astype(float).dropna()
            rows = [(pd.Timestamp(idx).date().isoformat(), float(v))
                    for idx, v in closes.items()
                    if pd.Timestamp(idx).date() <= pd.Timestamp(snapshot_date).date()]
            if len(rows) < 2:
                continue
            (_, onceki), (tarih, son) = rows[-2:]
            kayitlar.append({
                "symbol": symbol, "indicator": kod, "label": ad,
                "value": round(son, 4), "previous": round(onceki, 4),
                "change": round((son / onceki - 1) * 100, 4) if onceki else None,
                "unit": birim, "period": tarih, "category": "BIST",
                "source": "Borsapy", "status": "close",
            })
        except Exception as exc:
            _logging.getLogger("makro-snapshot").warning(
                "%s alinamadi: %s", symbol, exc)
    return kayitlar


def master_cio_agent(state: AgentState):
    logger.info("[Bas Analist] Rapor sentezleniyor...")
    print("[Bas Analist] Rapor sentezleniyor...", flush=True)
    gecmis_ozetler = load_recent("summaries", gun=14)
    hafiza_metni = ""
    if gecmis_ozetler:
        hafiza_metni = "\n[GECMIS GUNLERIN ANALIZ OZETLERI - HAFIZA]:\n"
        for g in gecmis_ozetler:
            hafiza_metni += f"-- {g['date']}: {g['data'].get('ozet', '')}\n"

    # Tarih ve endeks seviyeleri kod tarafindan hesaplanir; LLM yalnizca bu
    # kesin rakamlari kullanir. Veri cekilemezse eski "tarih yazma" yasaği
    # devrede kalir (yanlis tarih yayinlama riskine karsi).
    piyasa_blogu = piyasa_verisi_metni()
    piyasa_bolumu = (f"\n[BUGUNUN TARIHI VE PIYASA VERILERI - KESIN RAKAMLAR; "
                     f"tarih, gun adi, seviye ve yuzdeleri YALNIZCA buradan al]:\n"
                     f"{piyasa_blogu}\n") if piyasa_blogu else ""
    if piyasa_blogu:
        tarih_kurallari = (
            '- Rapor doğrudan "## 1." başlığıyla başlayacak; RAPOR ADI, Yayıncı, Konu gibi kimlik satırları EKLEME.\n'
            "- Metinde köşeli parantezli [...] yer tutucu veya iç not kullanma.\n"
            '- Kimlik satırı YAZMA: "Hedge-Fund", "Direktör", "Portföy Yöneticisi", "Analist:", "Yayıncı:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri geçmeyecek.\n'
            '- "## 1." başlığından sonraki İLK cümle tarih ve endeks verisiyle açılır: tarih, gün adı, endeks seviyesi ve yüzdeleri YALNIZCA [BUGUNUN TARIHI VE PIYASA VERILERI] bloğundan AYNEN alınır; kendi hafızandan tarih, gün adı veya rakam ÜRETME (tarih-gün eşleştirmesinde sık hata yapıyorsun). Raporun yazıldığı saat de ilk cümlede geçer (bloktaki "saat HH:MM" değerini aynen kullan). Örnek kalıp: "<tarih>, saat <HH:MM> itibarıyla — BIST 30 (XU030) son kapanış <son kapanış>; bir önceki kapanış <önceki kapanış> (günlük %<değişim>); dolar bazında günlük performans %<değişim>."'
        )
    else:
        tarih_kurallari = (
            '- Rapor doğrudan "## 1." başlığıyla başlayacak; RAPOR ADI, Tarih, Yayıncı, Konu gibi kimlik satırları EKLEME (site şablonu tarihi zaten gösteriyor, yanlış tarihe düşme riski yaratma).\n'
            "- Metinde köşeli parantezli [...] yer tutucu veya iç not kullanma.\n"
            '- Kimlik satırı YAZMA: "Hedge-Fund", "Direktör", "Portföy Yöneticisi", "Analist:", "Yayıncı:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri ve tarih ya da haftanın gün adı raporda GEÇMEYECEK (tarih-gün eşleştirmesinde sık hata yapıyorsun; şablon zaten tarihi gösteriyor).'
        )


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
{piyasa_bolumu}
Raporu kesinlikle profesyonel bir finansal bülten formatında, her başlığı detaylı ve uzun cümlelerle açıklayarak şu alt başlıklar altında oluştur (her başlık "## " ile başlayan markdown başlığı olarak yazılacak):

## 1. Günün Verileri (özet): YALNIZCA rakamlar. Her satır "veri: değer" biçiminde, tek satır olsun: endeks seviyeleri ve günlük değişimler, USD/TRY, gram altın, yükselen/düşen hisse sayısı, sinyal dağılımı. Bu bölümde YORUM, tahmin veya değerlendirme cümlesi YAZMA; yalnızca verilen bloklardaki kesin rakamları listele.
## 2. Bunun Anlamı — Aktarım Zinciri: 1. bölümdeki rakamların nedenini ve piyasaya aktarımını kur. Zinciri şu sırayla ve açık bağlaçlarla yaz: veri → neden → mekanizma → sektör etkisi → hisse etkisi → risk. Örnek biçim: "kur artışı → ithal girdi maliyeti → marj baskısı → iç talep hassasiyeti → şirket bazında farklılaşma". Bu bölümde YENİ RAKAM ÜRETME; yalnızca 1. bölümdeki ve verilen bloklardaki rakamlara atıf yap.
## 3. Haber ve Makroekonomik Değerlendirme: Haber akışının ve makro verilerin BIST 30 şirketlerine yansımaları; enflasyon, kur ve faiz sarmalının yatırımcı psikolojisine etkisi. Her paragrafta önce gözlemi, sonra yorumu yaz.
## 4. Teknik Değerlendirme (Hisse Bazlı): En çok ayrışan, hacim kazanan veya direnç/destek noktalarını test eden lider hisselerin teknik anatomisi. Aşağıdaki SİNYAL TABLOSU verilerini kullan.
## 5. Şirket ve Finansal Değerlendirme: Temel veriler ışığında şirketlerin karlılık, bilanço yapıları ve rasyo bazlı öne çıkan detayları.
## 6. Risk Yönetimi ve Strateji: Kısa vadeli olası aşağı/yukarı yönlü senaryolar ve portföyü koruma kalkanları.
## 7. Önerilen Model Portföy: Raporun SONUNDA, yukarıdaki sinyal ve analizlere DAYANARAK kendinin kurduğu somut bir model portföyü tablosu oluştur. Tablo şu sütunlarla olmalı:

| Hisse | Sektör | Ağırlık (%) | İşlem | Baz Senaryo | İyimser Senaryo | Geçersizlik Koşulu | Gerekçe |
|-------|--------|-------------|-------|-------------|-----------------|--------------------|---------|

Tablo kuralları: En fazla 8 hisse pozisyonu + bir "NAKİT" satırı ekle; ağırlıklar %100'ü tamamlamalı (nakit dahil). Sadece AL/GÜÇLÜ AL sinyali veren ve gerekçesi verilerle desteklenen hisseleri seç; ağırlığı sinyal gücü, Pearson (r) ve kanal konumuna göre belirle. "Giriş bölgesi / hedef / stop" gibi emir dili KULLANMA; bunun yerine koşullu senaryo dili kullan: Baz Senaryo = mevcut veri setinin işaret ettiği en olası patika (örn. "kanal orta bandına doğru toparlanma"), İyimser Senaryo = görünümü güçlendiren somut koşul (örn. "kanal üst bandı üzerinde hacimli kapanış"), Geçersizlik Koşulu = senaryoyu çürüten somut gelişme (örn. "kanal alt bandı altında kapanış"). Anılan seviyeleri SADECE sağlanan gerçek fiyatlardan türet (kanal bantları ve son fiyat baz alın); dışarıdan hiçbir veri ekleme. Her satırın gerekçesi teknik + osilatör gerekçelerini birleştirsin.

Biçim kuralları (zorunlu):
{tarih_kurallari}
- KATMAN AYRIMI (zorunlu): 1. bölüm yalnız VERİdir (yorum yok); 2. bölüm yalnız YORUMdur ve aktarım zincirini (veri → neden → mekanizma → sektör etkisi → hisse etkisi → risk) eksiksiz kurar. Diğer bölümlerde her paragraf önce gözlemi, sonra yorumu yazar.
- TÜM metinde doğru Türkçe karakterler kullan (ç, ğ, ı, i, ö, ş, ü); "sinyal" gibi kelimeleri yanlış yazma ("sinyil" DEĞİL).
- 6. bölümdeki nakit/likidite önerisi ile 7. bölümdeki NAKİT satırının ağırlığı ÇELİŞMEMELİ (örn. "%40 nakit tutun" deyip %0 nakitlik portföy verme).
- Şirket adlarını YALNIZCA verilerde hisse kodunun yanında verilen resmi adla kullan (örn. YKBNK kodunun adı "Yapı Kredi"dir); hiçbir şirket için kendi hafızandan farklı bir isim, kısaltma ya da benzer bir ad yazma.
- Enflasyon gibi makro göstergeleri yalnızca [MAKRO GEREKLER] bölümündeki değerlerle an; kendi genel bilginden sayı yazma.
- Tablo veya portföy bölümlerinden sonra "(Not: ...)" biçiminde dipnot/uyarı cümlesi EKLEME; tekrar eden ya da yarım kalan dipnotları yazma.

{VERI_DAYANAK_ENVANTERI}

{PROFESYONEL_YAZIM_KURALLARI}

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
            f"{s['hisse']} ({HISSE_ADLARI.get(s['hisse'], s['hisse'])}, {s['sektor']}): son {s['son']} TL, gunluk {s['gunluk']:+.2f}%, 60g {s['deg60']:+.1f}%, "
            f"kisa={s['kisa']} orta={s['orta']} uzun={s['uzun']}, WT={s['wt']}, kanal %{s['konum']:.0f}, "
            f"r={s['r']:.2f}, genel={s['genel']}"
            for s in t_satirlar
        )
    if b_satirlar:
        sinyaller += "\n\n[TRADINGVIEW OSILATOR OYLERI - BUGUN]\n" + "\n".join(
            f"{s['hisse']} ({HISSE_ADLARI.get(s['hisse'], s['hisse'])}): oneri={s['oneri']} (al={s['al']}/sat={s['sat']}/notr={s['notr']}), RSI={s['rsi']}, "
            f"MACD={s['macd']}, StochK={s['stoch']}, CCI20={s['cci']}, ADX={s['adx']}"
            for s in b_satirlar
        )
    if sinyaller:
        prompt = prompt.replace("Kurallar: Asla uydurma", sinyaller + "\n\nKurallar: Asla uydurma")

    # Makro gercekler onceki aksam snapshot'indan tek cerceve olarak gelir.
    # Snapshot eksik/gecersizse bu fonksiyon bilerek durur; canli fallback yoktur.
    snapshot = makro_snapshot_cek()
    prompt = prompt.replace(
        "Kurallar: Asla uydurma",
        "[MAKRO GEREKLER]\n" + makro_veri.frame_text(snapshot) + "\n\n"
        + MAKRO_AKTARIM_KILAVUZU + "\n\nKurallar: Asla uydurma")

    # once AMD ana saglayici (DeepSeek-V4-Flash), olmazsa Z.ai (GLM) yedek
    response = None
    try:
        response = llm_call(prompt)
    except Exception as e:
        logger.exception("AMD llm_call failed in master_cio_agent: %s", e)
    # llm_call denemeler tukendiginde raise yerine fallback metni doner; metin
    # "truthy" oldugu icin bu kontrolun altindan gecip Z.ai yedegini atlardik.
    # Fallback metni basarisizlik say, Z.ai'i dene.
    if not response or (isinstance(response, str) and response.startswith("(LLM hizmetine ulaşılamadı")):
        response = _zai_call(prompt)
    if not response:
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
    response = _metin_dogrula_ve_kaydet(
        response, " / gunluk rapor", snapshot=snapshot)
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
    # NVIDIA -> OpenRouter) kullanilir; boylece ana rapor icin AMD'nin
    # gunluk kotasini tuketmez.
    ozet = llm_call(prompt, sirasi=("YEDEK", "NVID", "OR", "AMD"))
    save_daily("summaries", bugun, {"ozet": ozet})
    return {}


# ---------- DENEME PORTFOYU TAKIBI (Kiyaslamali) ----------
PORTFOLYO_DOSYASI = "portfolio.json"
BASLANGIC_SERMAYE = 100000.0

# Portfoy sayfalarindaki ortak aciklama notu: veri kesitini kullaniciya net anlatsin
# ("Guncel" fiyatlar aslinda bir onceki islem gununun kapanisidir; gunluk %0'lik
# hafta sonu/tatil kayitlari bu yuzden normaldir).
PORTFOY_NOTU = ("Portföy hafta içi her sabah otomatik olarak, bir önceki işlem gününün kapanış fiyatlarıyla "
                "güncellenir; hafta sonu ve tatil günlerinde değer değişmez. Canlı fiyatlar için "
                "üstteki fiyat şeridine bakınız. Getiri, başlangıçta eşit dağıtılan 100.000 TL'nin güncel "
                "değerine göre hesaplanır; tablodaki tekil hisse yüzdelerinin basit ortalaması değildir. "
                "Karşılaştırma çizgileri (altın/dolar/mevduat/endeks/enflasyon) da aynı 100.000 TL "
                "tabanına normalize edilmiştir.")


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

    # BIST 100 ve BIST 30 kiyaslama cizgileri: endeksin portfoy baslangicindan
    # bugune getirisini 100.000 TL tabanina normalize eder (borsapy index).
    xu100_degeri = xu030_degeri = None
    try:
        import borsapy as bp

        def _endeks_normalize(kod):
            ixh = bp.index(kod).history(period="3mo")
            if ixh is None or len(ixh) < 2:
                return None
            kapanislar = ixh["Close"].astype(float).dropna()
            kapanislar.index = [str(d)[:10] for d in kapanislar.index]
            taban = kapanislar[kapanislar.index >= p["start_date"]]
            taban_deger = float(taban.iloc[0]) if len(taban) else float(kapanislar.iloc[0])
            return BASLANGIC_SERMAYE * (float(kapanislar.iloc[-1]) / taban_deger) if taban_deger > 0 else None

        xu100_degeri = _endeks_normalize("XU100")
        xu030_degeri = _endeks_normalize("XU030")
    except Exception as e:
        logger.warning("[Uyari] Endeks kiyaslama verisi alinamadi: %s", e)

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
            **({"XU100": round(xu100_degeri, 2)} if xu100_degeri else {}),
            **({"XU030": round(xu030_degeri, 2)} if xu030_degeri else {})
        }
    })
    save_portfolio(p)

    ozet = f"Deneme Portföyü ({bugun}): Toplam {round(toplam,2):.2f} TL (Toplam %{yuzde:+.2f}, Mevduat: {round(deposit_degeri,2):.2f} TL, USD Karşılığı: {round(usd_degeri,2):.2f} TL, Altın Karşılığı: {round(gold_degeri,2):.2f} TL)"
    # Ozet artik final_report'a EKLENMEZ: portfoy verisi kendi sayfasinda
    # (portfolio.html) zaten yayinlaniyor; rapora gomulmesi "[PORTFOY OZETI]"
    # gibi ham pipeline ciktisinin sayfaya sizmasina yol aciyordu.
    return {"portfoy_ozet": ozet}


# ---------- DERIN ANALIZ (Z.ai GLM ile gunluk derin rapor) ----------
def _derin_dongu_var(metin: str) -> bool:
    """Uzun raporlarda paragraf/blok seviyesindeki tekrar dongusunu yakalar.

    _looks_degenerate tablo-satiri ve art arda kisa-parca dongulerine
    odaklidir; derin analizde gorulen bicim baskadir: model ayni fikri
    birkac cumlelik blokla tekrar tekrar yazar (orn. ayni hisse analizi
    7 kez). Bu yuzden burada hem cumle hem kayan-pencere tekrarina bakilir.
    """
    if not metin or len(metin) < 500:
        return False
    duz = re.sub(r"\s+", " ", metin).strip()
    cumleler = [c.strip() for c in re.split(r"(?<=[.!?])\s+", duz) if len(c.strip()) > 80]
    if cumleler and Counter(cumleler).most_common(1)[0][1] >= 3:
        return True
    pencereler = [duz[i:i + 60] for i in range(0, max(1, len(duz) - 60), 60)]
    if pencereler and Counter(pencereler).most_common(1)[0][1] >= 3:
        return True
    return False


def derin_analiz_yap(rapor_state, teknik_satirlar, borsapy_satirlar):
    """Kullanicinin Z.ai anahtariyla (ZAI_API_KEY secret) sayfada yayinlanan
    uzun ve derinlemesine gunluk analizi uretir; sonunda onerilen hisseler
    tablosu (markdown) icerir. Anahtar SADECE sunucuda kalir, sayfaya
    gomulmez. Anahtar yoksa None doner."""
    anahtar = os.environ.get("ZAI_API_KEY", "")
    if not anahtar:
        logger.warning("[Derin Analiz] ZAI_API_KEY tanimli degil; sayfa uretilmeyecek.")
        return None

    model = os.environ.get("ZAI_MODEL") or ZAI_MODEL_TERCIH[0]
    client = OpenAI(api_key=anahtar, base_url="https://api.z.ai/api/paas/v4/",
                    timeout=300.0, max_retries=1)

    teknik_ozet = "\n".join(
        f"{s['hisse']} ({HISSE_ADLARI.get(s['hisse'], s['hisse'])}): son {s['son']} TL, gunluk {s['gunluk']:+.2f}%, 60g {s['deg60']:+.1f}%, "
        f"kisa={s['kisa']} orta={s['orta']} uzun={s['uzun']}, WT={s['wt']}, "
        f"kanal %{s['konum']:.0f}, r={s['r']:.2f}, genel={s['genel']} ({s['sektor']})"
        for s in teknik_satirlar
    )
    borsapy_ozet = "\n".join(
        f"{s['hisse']} ({HISSE_ADLARI.get(s['hisse'], s['hisse'])}): oneri={s['oneri']} (al={s['al']}/sat={s['sat']}/notr={s['notr']}), "
        f"RSI={s['rsi']}, MACD={s['macd']}, StochK={s['stoch']}, CCI20={s['cci']}, ADX={s['adx']}"
        for s in borsapy_satirlar
    )
    portfoy = ""
    p = load_portfolio()
    if p and p.get("history"):
        son = p["history"][-1]
        portfoy = (f"Deneme portfoyu: toplam {son['total']} TL (%{son['pct']:+.2f}), "
                   f"gunluk %{son['daily_pct']:+.2f}, kiyaslamalar: {son.get('benchmarks')}")

    # Prompt ve dogrulama ayni onceki aksam snapshot'ini paylasir.
    snapshot = makro_snapshot_cek()
    makro = makro_veri.frame_text(snapshot)

    # Gunluk raporla ayni kesin tarih/rakam katmani; veri yoksa eski yasak durur.
    piyasa_blogu = piyasa_verisi_metni()
    piyasa_bolumu = (f"\n[BUGUNUN TARIHI VE PIYASA VERILERI - KESIN RAKAMLAR; "
                     f"tarih, gun adi, seviye ve yuzdeleri YALNIZCA buradan al]:\n"
                     f"{piyasa_blogu}\n") if piyasa_blogu else ""
    if piyasa_blogu:
        tarih_kurali = ('Metinde köşeli parantezli [...] yer tutucu kullanma; rapor doğrudan "## 1." başlığıyla başlasın. Kimlik satırı EKLEME: "Hedge-Fund", "Direktör", "Portföy Yöneticisi", "Analist:", "Yayıncı:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri geçmeyecek. "## 1." başlığından sonraki İLK cümle tarih ve endeks verisiyle açılır: tarih, gün adı, seviye ve yüzdeleri YALNIZCA [BUGUNUN TARIHI VE PIYASA VERILERI] bloğundan AYNEN alınır; raporun yazıldığı saat de aynı ilk cümlede geçer (bloktaki "saat HH:MM" değerini aynen kullan); kendi hafızandan tarih, gün adı, saat veya rakam ÜRETME (tarih-gün eşleştirmesinde sık hata yapıyorsun).')
    else:
        tarih_kurali = ('Metinde köşeli parantezli [...] yer tutucu kullanma; rapor doğrudan "## 1." başlığıyla başlasın. Kimlik satırı EKLEME: "Hedge-Fund", "Direktör", "Portföy Yöneticisi", "Analist:", "Yayıncı:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri ve tarih ya da haftanın gün adı raporda GEÇMEYECEK (tarih-gün eşleştirmesinde sık hata yapıyorsun).')

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

| Hisse | Sinyal | Baz Senaryo | İyimser Senaryo | Geçersizlik Koşulu | Gerekçe |
|-------|--------|-------------|-----------------|--------------------|---------|
| ... | ... | ... | ... | ... | ... |

Tabloda SADECE teknik ve osilatör verilerine göre AL/GÜÇLÜ AL sinyali veren hisseleri listeleyip her biri için gerekçe yaz. "Giriş/hedef/stop" emir dili kullanma; koşullu senaryo dili kullan (baz senaryo = en olası patika; iyimser senaryo = görünümü güçlendiren somut koşul; geçersizlik koşulu = senaryoyu çürüten somut gelişme). Rakamları yalnızca verilen fiyatlardan türet, asla dışarıdan veri ekleme. Şirket adlarını YALNIZCA verilerde hisse kodunun yanında verilen resmi adla kullan; hiçbir şirket için kendi hafızandan farklı bir isim yazma. Enflasyon oranını yalnızca [MAKRO GEREKLER] bölümündeki değerle an. {tarih_kurali}

{VERI_DAYANAK_ENVANTERI}

{PROFESYONEL_YAZIM_KURALLARI}

### VERİLER

[TEKNIK TARAMA SINYALLERI]
{teknik_ozet}

[TRADINGVIEW OSILATOR SINYALLERI]
{borsapy_ozet}

[PORTFOY DURUMU]
{portfoy}

[MAKRO GEREKLER]
{makro}
{piyasa_bolumu}

[GUNLUK RAPOR VE HABERLER]
{rapor_state.get('news_data', '')}
{rapor_state.get('tech_data', '')[:3000]}
{rapor_state.get('final_report', '')[:6000]}"""

    son_hata = None
    # 1) AMD ana saglayici (DeepSeek-V4-Flash) + havuz fallback'leri; rate-limit
    #    durumunda llm_call icindeki saglayici/model rotasyonu + Z.ai yedegi devrede.
    try:
        logger.info("[Derin Analiz] AMD cagrisi (llm_call)")
        icerik = llm_call(prompt)
        # llm_call denemeler tukendiginde fallback metni doner; bunu "icerik"
        # sanip sayfaya yazmamak icin basarisizlik sayip GLM yedegine dus.
        if icerik and icerik.strip() and not icerik.startswith("(LLM hizmetine ulaşılamadı"):
            if _derin_dongu_var(icerik):
                son_hata = "tekrar dongusu (AMD)"
                logger.warning("[Derin Analiz] AMD tekrar dongusune girdi; Z.ai yedegine geciliyor.")
            else:
                icerik = rapor_son_islem(icerik)
                return _metin_dogrula_ve_kaydet(
                    icerik, " / derin analiz", snapshot=snapshot)
        else:
            son_hata = "bos yanit (AMD)"
    except Exception as e:
        son_hata = str(e)[:200]
        logger.warning("[Derin Analiz] AMD basarisiz: %s; Z.ai yedegine geciliyor.", son_hata)
    # 2) Yedek: Z.ai GLM (5.3 ailesi once; 4.7 son care)
    denenecekler = [model] + [m for m in ZAI_MODEL_TERCIH if m != model]
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
                if _derin_dongu_var(icerik):
                    son_hata = "tekrar dongusu"
                    logger.warning("[Derin Analiz] %s tekrar dongusune girdi; rapor yayinlanmayip siradaki model deneniyor.", mdl)
                    time.sleep(3)
                    continue
                icerik = rapor_son_islem(icerik)
                return _metin_dogrula_ve_kaydet(
                    icerik, " / derin analiz", snapshot=snapshot)
            son_hata = "bos yanit"
        except Exception as e:
            son_hata = str(e)[:200]
            logger.warning("[Derin Analiz] %s basarisiz: %s", mdl, son_hata)
            time.sleep(3)
    logger.error("[Derin Analiz] tum modeller basarisiz (%s)", son_hata)
    return None


def makro_analiz_yap(yedek_amd=False, snapshot=None):
    """Makroekonomik Degerlendirme sayfasini snapshot cercevesiyle uretir.

    Prompt, HTML tablosu ve deterministik dogrulama ayni onceki aksam
    makro snapshot'ini paylasir. Canli veri cagrisi veya eski snapshot
    fallback'i yoktur.

    Birincil saglayici Z.ai GLM'dir (ZAI_API_KEY). Alinamazsa/basarisizsa
    yedek_omcu olarak AMD DeepSeek-V4-Flash denenir (yedek_amd=True ve
    AMD_API_KEY gercek anahtarsa; makro_analiz.py import sirasinda sahte
    anahtar koydugu icin yedek yalnizca gercek anahtarla acilir). Iki yol da
    metin uretmezse None.
    """
    anahtar = os.environ.get("ZAI_API_KEY", "")
    model = os.environ.get("ZAI_MODEL") or ZAI_MODEL_TERCIH[0]
    glm_istemci = (OpenAI(api_key=anahtar, base_url="https://api.z.ai/api/paas/v4/",
                          timeout=300.0, max_retries=1) if anahtar else None)
    if not anahtar:
        logger.warning("[Makro Analiz] ZAI_API_KEY yok; yedek AMD yolu denenecek (yedek_amd=%s).",
                       yedek_amd)

    # --- Tek kabul edilen kesin veri blogu ---
    snapshot = snapshot or makro_snapshot_cek()
    makro_tablo = makro_veri.frame_text(snapshot)
    piyasa_blogu = piyasa_verisi_metni(snapshot) or ""

    portfoy_ozet = ""
    p = load_portfolio()
    if p and p.get("history"):
        risk = _portfoy_risk(p) or {}
        son = p["history"][-1]
        portfoy_ozet = (f"Deneme portfoyu: toplam {son['total']} TL (%{son['pct']:+.2f}); "
                        f"maksimum dusus %{risk.get('dd', 0):.1f}, yillik volatilite "
                        f"%{risk.get('vol', 0):.1f}; kiyaslamalar: {son.get('benchmarks')}")

    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%d.%m.%Y")

    prompt = f"""Sen makroekonomi alanında uzman, akademik derinlikte ama anlaşılır yazan bağımsız bir analist yapay zekâsısın (gerçek bir kişi veya kurum değilsin; kendini öyle tanıtma). Aşağıdaki KESİN verileri kullanarak Türkiye ve küresel makroekonomik görünümü değerlendiren, DERİNLEMESİNE ve UZUN (en az 1000 kelime) bir "Makroekonomik Değerlendirme" yazısı kaleme al. Yazı Türkçe olacak ve TÜM metinde doğru Türkçe karakterler (ç, ğ, ı, ö, ş, ü) kullanılacak.

Üslup: Bir makroekonomi profesörü gibi yaz — kavramları kısaca açıkla, mekanizmaları (aktarım kanallarını) adım adım kur, tek yönlü kehanet yerine koşullu senaryolar sun. Somut rakam YALNIZCA aşağıdaki veri bloklarından alınır; kendi hafızandan makro sayı (enflasyon, faiz, büyüme, kur, işsizlik vb.) ÜRETME. Veri bloğunda olmayan bir göstergeye ihtiyaç duyarsan sayı vermeden niteliksel konuş.

Yanıtını şu yapıda oluştur (başlıklar aynen bu şekilde, "## " ile):

## 1. Küresel Makroekonomik Görünüm
(ABD ve Euro Bölgesi verileri: enflasyon, politika faizi, büyüme, işsizlik; küresel likidite ve risk iştahına etkileri)

## 2. Türkiye Makroekonomik Görünümü
(TÜİK enflasyon/ÜFE, TCMB politika faizi, büyüme, işsizlik; dezenflasyon sürecinin hangi aşamasında olunduğu)

## 3. İç Talep, Kredi ve Likidite
(Veri bloğunda varsa: mevduat faizi, tüketici kredisi faizi, banka kredileri büyümesi, M3 para arzı; politika faizinden kredi maliyetine ve iç talebe giden kanalı anlat. Veri yoksa bu bölümü niteliksel geç.)

## 4. Küresel Risk İştahı ve Faiz Beklentileri
(Veri bloğunda varsa: VIX, DXY, ABD tahvil getirileri, yatırım-grade ve yüksek getirili kredi marjları, Fed faiz projeksiyonları; risk iştahının BIST'e olası yansımasını bağla. Veri yoksa niteliksel geç.)

## 5. Bütçe ve Dış Denge
(Veri bloğunda varsa: bütçe dengesi, bütçe/GSYH, cari denge, ticaret dengesi, ihracat-ithalat, turizm gelirleri, döviz rezervler; dış finansman kırılganlığını değerlendir. Veri yoksa niteliksel geç.)

## 6. Enerji, Emtia, Döviz ve Tahvil Piyasaları
(Veri bloğunda varsa: Brent/WTI, altın, USD/TRY-EUR/TRY, tahvil getirileri ve gram altın; enerji-fiyat ve kur-enflasyon etkileşimini anlat. Veri yoksa niteliksel geç.)

## 7. Aktarım Mekanizmaları: Makrodan Piyasaya
(Şu zincirleri veriye bağlayarak anlat: politika faizi -> mevduat/kredi maliyeti -> iç talep -> şirket satışları -> marjlar -> değerleme; kur kanalı -> ithal girdi maliyeti -> enflasyon; küresel faiz -> yabancı sermaye akımı -> risk primi)

## 8. BIST 30'a Yansımalar: Sektör Kanalları
(Banka, sanayi, holding, perakende gibi ana sektörlerin makro veriye duyarlılığı; yüksek faiz ortamında kim kazanır kim kaybeder)

## 9. Senaryolar ve İzleme Çerçevesi
(Baz/iyimser/kötümser senaryo; her senaryoyu geçersiz kılacak somut veri gelişmesi; önümüzdeki dönemde izlenecek göstergeler. "Al/sat/giriş/hedef/stop" gibi emir dili KULLANMA.)

## 10. Sonuç ve Değerlendirme

Biçim kuralları (zorunlu):
- Kimlik satırı YAZMA: "Profesör", "Dr.", "Analist:", "Hazırlayan:", "Tarih:" gibi kişi/kurum/unvan ifadeleri geçmeyecek.
- Metinde köşeli parantezli [...] yer tutucu kullanma; yazı doğrudan "## 1." başlığıyla başlasın.
- Bugünün tarihi: {bugun}. Bunun dışında tarih/gün adı üretme.
- Bu bir yatırım tavsiyesi değildir; eğitim ve analiz amaçlıdır — bunu son bölümde kısaca belirt.

### VERİLER (KESİN RAKAMLAR — yalnızca bunları kullan)

[MAKRO GÖSTERGELER — KİLİTLİ ÇERÇEVE]
{makro_tablo}

[PİYASA VERİLERİ]
{piyasa_blogu}

[PORTFÖY RİSK BAĞLAMI]
{portfoy_ozet}
"""

    son_hata = "ZAI anahtari yok" if glm_istemci is None else None

    # 1) AMD ana saglayici (DeepSeek-V4-Flash); Z.ai GLM yedek
    if client is not None:
        try:
            amd_model = AMD_MODEL_LIST[0] if AMD_MODEL_LIST else AMD_MODEL
            logger.info("[Makro Analiz] AMD cagrisi (%s)", amd_model)
            icerik = _llm_call_ic(prompt, max_deneme=4, fallback_on_fail=False,
                                  sirasi=("AMD",))
            if icerik and icerik.strip():
                if _derin_dongu_var(icerik):
                    son_hata = "tekrar dongusu (AMD)"
                    logger.warning("[Makro Analiz] AMD tekrar dongusune girdi; GLM yedegine geciliyor.")
                else:
                    icerik = rapor_son_islem(icerik)
                    return _metin_dogrula_ve_kaydet(
                        icerik, " / makro analiz", snapshot=snapshot)
            else:
                son_hata = "bos yanit (AMD)"
        except Exception as e:
            son_hata = str(e)[:200]
            logger.warning("[Makro Analiz] AMD basarisiz: %s; GLM yedegine geciliyor.", son_hata)

    denenecekler = ([model] + [m for m in ZAI_MODEL_TERCIH if m != model]
                    if glm_istemci is not None else [])
    for deneme, mdl in enumerate(denenecekler):
        try:
            logger.info("[Makro Analiz] GLM cagrisi (%s), deneme %d", mdl, deneme + 1)
            resp = glm_istemci.chat.completions.create(
                model=mdl,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=8000,
                extra_body={"thinking": {"type": "disabled"}},
            )
            icerik = resp.choices[0].message.content or ""
            if icerik.strip():
                if _derin_dongu_var(icerik):
                    son_hata = "tekrar dongusu"
                    logger.warning("[Makro Analiz] %s tekrar dongusune girdi; siradaki model deneniyor.", mdl)
                    time.sleep(3)
                    continue
                icerik = rapor_son_islem(icerik)
                return _metin_dogrula_ve_kaydet(
                    icerik, " / makro analiz", snapshot=snapshot)
            son_hata = "bos yanit"
        except Exception as e:
            son_hata = str(e)[:200]
            logger.warning("[Makro Analiz] %s basarisiz: %s", mdl, son_hata)
            time.sleep(3)
    if son_hata is None:
        son_hata = "glm bos yanit"
    logger.warning("[Makro Analiz] GLM metin uretmedi (%s); yedek denenir: %s",
                   son_hata, "AMD" if yedek_amd else "yedek kapali")

    # --- Yedek: AMD DeepSeek-V4-Flash (llm_call altyapisiyla AMD havuzu) ---
    if yedek_amd:
        if client is None:
            logger.warning("[Makro Analiz] AMD istemcisi yok (AMD_API_KEY); yedek kullanilamadi.")
        else:
            try:
                amd_model = AMD_MODEL_LIST[0] if AMD_MODEL_LIST else AMD_MODEL
                logger.info("[Makro Analiz] AMD yedek cagrisi (%s)", amd_model)
                icerik = _llm_call_ic(prompt, max_deneme=4, fallback_on_fail=False,
                                      sirasi=("AMD",))
                if icerik and icerik.strip():
                    icerik = rapor_son_islem(icerik)
                    return _metin_dogrula_ve_kaydet(
                        icerik, " / makro analiz", snapshot=snapshot)
                son_hata = f"{son_hata} / amd bos yanit"
            except Exception as e:
                son_hata = str(e)[:200]
                logger.warning("[Makro Analiz] AMD yedek basarisiz: %s", son_hata)
    logger.error("[Makro Analiz] metin uretilemedi (GLM/AMD): %s", son_hata)
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
SITE_URL = "https://borsa-raporlari.pages.dev/"




def _tr_bicim(metin):
    """1,234.56 -> 1.234,56 (Turkce sayi bicimi)."""
    return metin.replace(",", "@").replace(".", ",").replace("@", ".")


def _ts(x, hane=2):
    """Turkce binlik ayracli sayi: 1234.5 -> 1.234,50"""
    try:
        return _tr_bicim(f"{x:,.{hane}f}")
    except Exception:
        return str(x)


def _ts0(x):
    return _ts(x, 0)


def _ts1(x):
    return _ts(x, 1)


def _ty(x, hane=2):
    """Isaretli Turkce sayi/yuzde: 1.25 -> +1,25"""
    try:
        return _tr_bicim(f"{x:+,.{hane}f}")
    except Exception:
        return str(x)


def _ty1(x):
    return _ty(x, 1)


def _guzel_url(yol):
    """Cloudflare Pages, .html'li adresleri uzantısız adrese 308 ile
    yonlendirir. Canonical/og:url/sitemap adresleri bu hedefle ayni olsun
    diye yol guzel (uzantisiz) biçime cevrilir: 'hisse/index.html' ->
    'hisse/', 'reports/x.html' -> 'reports/x'."""
    yol = re.sub(r"index\.html$", "", yol or "")
    return re.sub(r"\.html$", "", yol)
SITE_ADI = "BIST 30 Günlük Raporlar"

# ---------- INDEXNOW (Bing/Yandex/Seznam/Naver aninda indeksleme) ----------
# Hesap/anahtar YOK: kok dizine bir anahtar dosyasi koyar, sayfalar degistiginde
# api.indexnow.org'ya ping atariz. Bing, Yandex, Seznam ve Naver bu protokolle
# sayfalari kendi tarayicilarini beklemek yerine dakikalar icinde alir.
# (Google ve Baidu IndexNow kullanmaz; onlar icin dogrulama meta etiketleri
# asagida ENV ile desteklenir.)
INDEXNOW_KEY = "ba8235ea226b9c95831f13f37a9e223f"
INDEXNOW_ANA_SAYFALAR = [
    "index.html", "derin-analiz.html", "makro-analiz.html", "teknik-analiz.html",
    "borsapy-analiz.html", "portfolio.html", "haftasonu.html",
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


# Panelden alinan dogrulama kodlari (tasarim geregi herkese acik degerlerdir;
# ENV ile degistirilebilir: GOOGLE/BING/BAIDU_SITE_DOGRULAMA)
SABIT_DOGRULAMALAR = {
    "google-site-verification": "M6bvKkbe_v88vv_A9tLmapgiWTO6KBafKoHVWX_qwjY",
}


def _dogrulama_etiketleri():
    """Arama motoru sahiplik dogrulama meta etiketleri.
    Sabitler + ENV secret'lari birlesir; ikisi de bossa etiket eklenmez."""
    etiket = ""
    for env_adi, meta_adi in (
        ("GOOGLE_SITE_DOGRULAMA", "google-site-verification"),
        ("BING_SITE_DOGRULAMA", "msvalidate.01"),
        ("BAIDU_SITE_DOGRULAMA", "baidu-site-verification"),
        ("YANDEX_SITE_DOGRULAMA", "yandex-verification"),
    ):
        deger = (os.environ.get(env_adi) or "").strip() or SABIT_DOGRULAMALAR.get(meta_adi, "")
        if deger:
            etiket += f'<meta name="{meta_adi}" content="{deger}">\n'
    return etiket


# ---------- SESLI RAPOR (Edge-TTS, ucretsiz Microsoft sinir sesleri) ----------
# Gunluk rapor her sabah MP3 olarak da okunur (tr-TR sinir sesi, ~48kbps).
# Ayrıca her rapor/derin sayfasinda tarayici sesiyle "Dinle" dugmesi var
# (Web Speech API — ses dosyasi olmasa bile calisir).
TTS_SESI = os.environ.get("TTS_SESI") or "tr-TR-EmelNeural"


# Sesli okumada ticker ve kısaltmaların doğal telaffuzu
HISSE_ADLARI = {
    "AEFES": "Anadolu Efes", "AKBNK": "Akbank", "ASELS": "Aselsan",
    "ASTOR": "Astor Enerji", "BIMAS": "BİM", "DSTKF": "Destek Finans Faktoring",
    "EKGYO": "Emlak Konut GYO", "ENKAI": "Enka İnşaat", "EREGL": "Ereğli Demir Çelik",
    "FROTO": "Ford Otosan", "GARAN": "Garanti BBVA", "GUBRF": "Gübre Fabrikaları",
    "ISCTR": "İş Bankası", "KCHOL": "Koç Holding", "KRDMD": "Kardemir",
    "MGROS": "Migros", "PETKM": "Petkim", "PGSUS": "Pegasus",
    "SAHOL": "Sabancı Holding", "SASA": "Sasa Polyester", "SISE": "Şişecam",
    "TAVHL": "TAV Havalimanları", "TCELL": "Turkcell", "THYAO": "Türk Hava Yolları",
    "TOASO": "Tofaş", "TRALT": "Türk Altın İşletmeleri", "TTKOM": "Türk Telekom",
    "TUPRS": "Tüpraş", "VAKBN": "Vakıfbank", "YKBNK": "Yapı Kredi",
}


def _konusma_metni_normalize(metin):
    """Sesli okuma öncesi kısaltma ve ticker'ları doğal söylenişe çevirir:
    'BIST' -> 'Borsa İstanbul', 'TUPRS' -> 'Tüpraş' vb. (kelime sınırlı)."""
    metin = re.sub(r"\bBIST\b", "Borsa İstanbul", metin)
    for kod, ad in HISSE_ADLARI.items():
        metin = re.sub(r"\b" + re.escape(kod) + r"\b", ad, metin)
    return metin


def _enflasyon_fakt(snapshot=None):
    """Snapshot'taki TR yillik enflasyonunu tek kaynak olarak dondurur."""
    try:
        snapshot = snapshot or makro_snapshot_cek()
        enflasyon, _, _ = makro_veri.rate_maps(snapshot)
        yuzde = float(enflasyon["tr"])
        metin = ("%.2f" % yuzde).rstrip("0").rstrip(".").replace(".", ",")
        rec = next(r for r in snapshot["records"]
                   if r["country_code"] == "TR"
                   and r["indicator"] == "inflation_yoy")
        return {"yuzde": yuzde, "metin": f"%{metin}",
                "donem": rec["period"]}
    except (KeyError, StopIteration, TypeError, ValueError):
        logger.warning("[Dogrulama] snapshot'ta TR enflasyonu yok.")
        return None


# Dogrulama istatistikleri: metrik_dosyasi_yaz bunlari data/metrics/latest.json
# icindeki "dogrulama" anahtarina yazar (izleme sayfasinin gosterdigi).
DOGRULAMA_IST = {"isim": 0, "enflasyon": 0, "toplam_metin": 0, "endeks": 0,
                 "endeks_uyari": 0, "faiz": 0, "makro": 0, "piyasa": 0,
                 "son_guncelleme": ""}


def _faiz_oranlari(snapshot=None):
    """Snapshot'taki ulke bazli politika faizlerini dondurur."""
    snapshot = snapshot or makro_snapshot_cek()
    _, faiz, _ = makro_veri.rate_maps(snapshot)
    return faiz or None


def _enflasyon_oranlari(snapshot=None):
    """Snapshot'taki ulke bazli yillik enflasyonlari dondurur."""
    snapshot = snapshot or makro_snapshot_cek()
    enflasyon, _, _ = makro_veri.rate_maps(snapshot)
    return enflasyon or None


def _metin_dogrula_ve_kaydet(metin, etiket="", snapshot=None):
    """Yayin oncesi deterministik dogrulama; ayni snapshot promptla paylasilir."""
    snapshot = snapshot or makro_snapshot_cek()
    enflasyon_oranlari, faiz_oranlari, gostergeler = makro_veri.rate_maps(snapshot)
    piyasa_serileri = makro_veri.piyasa_map(snapshot)
    enflasyon = _enflasyon_fakt(snapshot)
    try:

        # Endeks seviyesi: XU030 gunluk kapanisi (piyasa_verisi proses icinde
        # bir kez hesaplanir). Rapor metninde bunun disindaki seviyeler
        # (or. 4.200) otomatik olarak dogru degerle degistirilir.
        endeks = None
        try:
            pv = piyasa_verisi() or {}
            if pv.get("XU030", {}).get("son"):
                endeks = float(pv["XU030"]["son"])
        except Exception:
            endeks = None
        sonuc = dogrulama.metin_dogrula(
            metin, HISSE_ADLARI, enflasyon["yuzde"] if enflasyon else None,
            endeks_seviyesi=endeks, faiz_oranlari=faiz_oranlari,
            enflasyon_oranlari=enflasyon_oranlari,
            enflasyon_aylik_oranlari=makro_veri.inflation_mom_map(snapshot),
            makro_gostergeleri=gostergeler,
            piyasa_serileri=piyasa_serileri)
        if (sonuc["isim_duzeltme"] or sonuc["enflasyon_duzeltme"]
                or sonuc["endeks_duzeltme"] or sonuc["faiz_duzeltme"]
                or sonuc["makro_duzeltme"] or sonuc["piyasa_duzeltme"]):
            DOGRULAMA_IST["isim"] += len(sonuc["isim_duzeltme"])
            DOGRULAMA_IST["enflasyon"] += len(sonuc["enflasyon_duzeltme"])
            DOGRULAMA_IST["endeks"] += len(sonuc["endeks_duzeltme"])
            DOGRULAMA_IST["faiz"] += len(sonuc["faiz_duzeltme"])
            DOGRULAMA_IST["makro"] += len(sonuc["makro_duzeltme"])
            DOGRULAMA_IST["piyasa"] += len(sonuc["piyasa_duzeltme"])
            for yanlis, dogru, kod in sonuc["isim_duzeltme"]:
                logger.warning("[Dogrulama%s] sirket adi duzeltildi: '%s' -> '%s (%s)'",
                               etiket, yanlis, dogru, kod)
            for eski, yeni in sonuc["enflasyon_duzeltme"]:
                logger.warning("[Dogrulama%s] enflasyon sayisi duzeltildi: %s -> %s",
                               etiket, eski, yeni)
            for eski, yeni in sonuc["faiz_duzeltme"]:
                logger.warning("[Dogrulama%s] politika faizi duzeltildi: %s -> %s",
                               etiket, eski, yeni)
            for gosterge, eski, yeni in sonuc["makro_duzeltme"]:
                logger.warning("[Dogrulama%s] %s sayisi duzeltildi: %s -> %s",
                               etiket, gosterge, eski, yeni)
            for etiket, eski, yeni in sonuc["piyasa_duzeltme"]:
                logger.warning("[Dogrulama%s] piyasa %s duzeltildi: %s -> %s",
                               etiket, etiket, eski, yeni)
            for eski, yeni in sonuc["endeks_duzeltme"]:
                logger.warning("[Dogrulama%s] endeks seviyesi duzeltildi: %s -> %s",
                               etiket, eski, yeni)
        for uyari in sonuc.get("endeks_uyari", []):
            DOGRULAMA_IST["endeks_uyari"] += 1
            logger.warning("[Dogrulama%s] seviye mantigi: %s", etiket, uyari)
        DOGRULAMA_IST["toplam_metin"] += 1
        return sonuc["metin"]
    except Exception as exc:
        logger.exception("[Dogrulama] beklenmeyen hata; metin yayinlanmadi.")
        raise RuntimeError("deterministik dogrulama basarisiz") from exc


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
    metin = _konusma_metni_normalize(metin)
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
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(165px,1fr)); gap:14px; }
/* genis icerik kartlari icin esit 2 kolon (hisse detay: teknik durum + haberler);
   auto-fill kucuk kart gridi tabloyu dar sutuna sikistirdigi icin ayri sinif gerekli */
.grid-iki { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
/* ---- Gunluk BIST30 panosu (arastirma evi bulteni tarzi) ---- */
.pano { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:0 0 18px; }
.pano-baslik { font-size:15.5px; font-weight:700; margin-bottom:10px; padding-left:10px; border-left:4px solid var(--accent); }
.pano-veri { display:grid; grid-template-columns:repeat(auto-fit, minmax(130px,1fr)); gap:8px; margin-bottom:12px; }
.pano-hucre { background:var(--accent-bg); border:1px solid var(--line); border-radius:10px; padding:8px 12px; }
.pano-etiket { font-size:10.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }
.pano-deger { font-size:17px; font-weight:700; }
.pano-man { display:grid; gap:4px; font-size:13.5px; margin-bottom:12px; }
.pano-tablo { font-size:13px; }
.pano-tablo th, .pano-tablo td { padding:5px 8px; white-space:nowrap; }
.pano-tablo thead th { background:var(--ink); color:#fff; position:sticky; top:0; }
.pano-not { color:var(--muted); font-size:12px; margin:8px 0 0; }
/* 'hidden' ozelligini .grid'in display'i ezmesin diye guaranti */
[hidden] { display:none !important; }
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
.report table { margin:16px 0; border:1px solid var(--line); width:100%; }
.report thead th { background:#0f172a; color:#fff; font-weight:600; font-size:12.5px;
                   text-transform:none; letter-spacing:.3px; padding:11px 14px; text-align:left;
                   border:1px solid var(--line); white-space:nowrap; }
.report tbody td { border:1px solid var(--line); padding:10px 14px; }
.report tbody tr:nth-child(even) td { background:rgba(148,163,184,.10); }
.report tbody tr:hover td { background:var(--accent-bg); }
.report tbody td:first-child { font-weight:600; }
.report hr { border:none; border-top:1px solid var(--line); margin:22px 0; }
.report blockquote { margin:14px 0; padding:10px 16px; border-left:4px solid var(--line); color:var(--muted); }
.badge { display:inline-block; background:var(--accent-bg); color:var(--accent); border:1px solid #99f6e4;
         border-radius:999px; padding:3px 12px; font-size:12.5px; font-weight:600; }
.meta { color:var(--muted); font-size:13.5px; margin:8px 0 22px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.footer { text-align:center; color:var(--muted); font-size:12.5px; padding:26px 20px;
          border-top:1px solid var(--line); background:var(--card); }
.chart { width:100%; height:auto; display:block; }
@media (max-width:640px) {
  .report { padding:18px 16px; }
  table { font-size:13px; }
  th, td { padding:7px 8px; }
  .grid-iki { grid-template-columns:1fr; }
}

/* ---- Ust widget seridi: solda saat + hava durumu, sagda dil baglantilari ---- */
.site-widgets { display:flex; align-items:center; justify-content:space-between;
                gap:8px 18px; flex-wrap:wrap; margin:0 0 12px; }
.site-widgets #live-clock-weather { min-width:0; }
.site-widgets .dil-linkler { margin-left:auto; justify-content:flex-end; }

/* ---- Etkilesimli satir: solda arama (yarim genislik), sagda radyo kutusu ---- */
.interactive-box { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr);
                   gap:10px 18px; align-items:start; margin:0 0 16px; }
.interactive-box > * { min-width:0; }
.arama-kutusu { min-width:0; }
.radyo-mini { display:flex; align-items:center; gap:10px; flex-wrap:wrap; min-width:0; }
.radyo-mini .radyo-uyari { flex-basis:100%; font-size:11.5px; color:var(--muted); line-height:1.35; }

@media (max-width: 768px) {
    .interactive-box {
        grid-template-columns: 1fr !important;
    }
    .site-widgets { align-items:flex-start; }
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
/* sosyal paylasim rozetleri (rapor/hisse basliklarinda) */
.paylas { display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px;
          border:1px solid var(--line); border-radius:999px; font-size:13px; text-decoration:none;
          color:var(--muted); background:transparent; }
.paylas:hover { border-color:var(--accent); color:var(--accent); }

/* ---- Karanlik tema (html.dark) ---- */
html.dark { --ink:#dbe4f0; --muted:#8fa3bd; --line:#273449; --bg:#0b1220; --card:#121c30;
            --pos:#34d399; --neg:#f87171; --accent:#2dd4bf; --accent-bg:#0c2b27; }
html.dark .report tbody tr:nth-child(even) td { background:rgba(148,163,184,.08); }
html.dark .badge { background:#0c2b27; color:#2dd4bf; border-color:#155e56; }
html.dark .ses-btn { background:#121c30; border-color:#155e56; color:#2dd4bf; }
html.dark .footer { background:var(--card); }
/* topbar/ticker/tablo basliklari her temada koyu kalmali (--ink metin rengi
   oldugu icin temayla birlikte acilmamali) */
.topbar, .ticker-bant, .report thead th { background:#0f172a; }
html.dark .ticker-bant, html.dark .topbar { background:#0b1220; border-bottom:1px solid var(--line); }
.theme-btn { background:transparent; color:#cbd5e1; border:1px solid #475569; border-radius:8px;
             padding:4px 10px; font-size:14px; cursor:pointer; }
.theme-btn:hover { color:#fff; border-color:#94a3b8; }

/* ---- Mobil alt navigasyon ---- */
.altbar { display:none; }
@media (max-width: 768px) {
  .altbar { display:flex; position:fixed; bottom:0; left:0; right:0; z-index:60;
            background:#0f172a; padding:6px 4px calc(6px + env(safe-area-inset-bottom));
            justify-content:space-around; border-top:1px solid #1e293b; }
  .altbar a { color:#cbd5e1; text-decoration:none; font-size:11px; text-align:center; flex:1; padding:2px 0; }
  .altbar a .i { display:block; font-size:17px; }
  .altbar a.active { color:#2dd4bf; font-weight:700; }
  body { padding-bottom:66px; }
}

/* ---- Günlük ısı haritası ---- */
.isi-harita { display:grid; grid-template-columns:repeat(auto-fill, minmax(96px,1fr)); gap:8px; }
.isi-hucre { border-radius:10px; padding:10px 6px; text-align:center; color:#fff; }
.isi-hucre b { display:block; font-size:13.5px; letter-spacing:.4px; }
.isi-hucre span { font-size:12px; font-weight:700; }
"""


# ---------- GLM TABANLI SAYFA CEVRISI (Google Translate yerine) ----------
# Siteyi ve raporlari ureten yapay zeka (Z.ai GLM) ayni zamanda sayfayi
# cevirir: Puter.js uzerinden calisir, hicbir hesap/anahtar gerekmez,
# ziyaretci kendi ucretsiz Puter kotasin kullanir. Cince basta olmak
# uzere 5 dil + "Turkce'ye don" secenegi.
# ---------- DIL DEGISTIRICI (gercek dil sayfalari) ----------
# Eski Puter tabanli "anlik ceviri" kutusunun yerini alir: her dil icin
# GERCEK baglanti verir (/en/, /de/, /ru/, /zh/). Ceviri, sayfa uretimi
# sirasinda i18n.py tarafindan yapilir; ziyaretci tarafinda ceviri calismaz.
_DILLER = (
    ("tr", "tr", "Türkçe"),
    ("en", "en", "English"),
    ("de", "de", "Deutsch"),
    ("ru", "ru", "Русский"),
    ("zh", "zh-Hans", "简体中文"),
)
_DIL_STIL = (
    "<style>.dil-linkler{display:flex;gap:10px;align-items:center;font-size:12.5px;flex-wrap:wrap}"
    ".dil-linkler a{color:var(--muted);text-decoration:none;border-bottom:1px solid transparent}"
    ".dil-linkler a:hover{color:var(--accent);border-bottom-color:var(--accent)}"
    ".dil-linkler a.aktif{color:var(--accent);font-weight:600}</style>"
)


def _dil_yolu(yol, dil):
    """Ayni sayfanin ilgili dil surumunun adresi (site .html'siz canonical kullaniyor)."""
    p = (yol or "").lstrip("/")
    if p.endswith("index.html"):
        p = p[: -len("index.html")]
    elif p.endswith(".html"):
        p = p[:-5]
    return f"{SITE_URL}{dil + '/' if dil else ''}{p}"


def _ceviri_widget(kok="", yol=""):
    """Dil degistirici: ayni sayfanin diger dil surumlerine gercek baglanti verir."""
    parcalar = [
        _DIL_STIL,
        '<nav class="dil-linkler" aria-label="Sayfa dili seçin">',
        '<span aria-hidden="true">🌐</span>',
    ]
    for kod, hreflang, ad in _DILLER:
        aktif = ' class="aktif" aria-current="true"' if kod == "tr" else ""
        parcalar.append(
            f'<a href="{_dil_yolu(yol, "" if kod == "tr" else kod)}" '
            f'hreflang="{hreflang}" lang="{hreflang}" data-dil="{kod}"{aktif}>{ad}</a>'
        )
    parcalar.append("</nav>")
    return "\n".join(parcalar)





def _radyo_kutusu(kok=""):
    """Kompakt radyo satiri (2026-09-21): oynat/duraklat + ses ayari + yayin linkleri.
    Arama cubuguyla ayni satirda durur; tam yayin listesi radyo/ sayfasindadir."""
    return f"""
<div class="radyo-mini">
  <button type="button" class="radyo-oynat" id="radyo-btn" aria-label="Oynat / Duraklat">\u25B6</button>
  <audio id="radyo-ses" preload="none"></audio>
  <input type="range" id="radyo-ses-ayar" class="radyo-ses" min="0" max="100" value="80" aria-label="Ses seviyesi" title="Ses">
  <span class="radyo-linkler"><a href="{kok}radyo/index.html">\U0001F4FB T\u00fcm yay\u0131nlar &amp; Podcast sayfas\u0131</a> <span class="ayrac">\u2022</span> <a href="{kok}radyo/podcast.xml">RSS</a></span>
  <span class="radyo-uyari">Yaln\u0131zca dinlemek i\u00e7indir \u2022 Kurgusal yapay zeka sunucular \u2022 Bilgilendirme ama\u00e7l\u0131d\u0131r, yat\u0131r\u0131m tavsiyesi de\u011fildir.</span>
</div>
<script>
(function(){{
  var btn=document.getElementById('radyo-btn'), ses=document.getElementById('radyo-ses'),
      ayar=document.getElementById('radyo-ses-ayar'), kok='{kok}';
  if(!btn||!ses) return;
  if(ayar){{ ses.volume=(parseInt(ayar.value,10)||80)/100; }}
  var hazir=false;
  function hazirla(geri){{
    if(hazir) return geri();
    fetch(kok+'radyo/indeks.json').then(function(r){{return r.json();}}).then(function(d){{
      var l=(d&&d.bolumler)||[];
      if(l.length&&l[0].dosya){{ ses.src=kok+l[0].dosya; }}
      hazir=true; geri();
    }}).catch(function(){{ hazir=true; geri(); }});
  }}
  btn.addEventListener('click',function(){{
    if(ses.paused){{ hazirla(function(){{ var p=ses.play(); if(p&&p.catch){{p.catch(function(){{}});}} }}); }}
    else {{ ses.pause(); }}
  }});
  ses.addEventListener('play',function(){{ btn.textContent='\u275A\u275A'; btn.setAttribute('aria-label','Duraklat'); }});
  ses.addEventListener('pause',function(){{ btn.textContent='\u25B6'; btn.setAttribute('aria-label','Oynat'); }});
  if(ayar){{ ayar.addEventListener('input',function(){{ ses.volume=(parseInt(ayar.value,10)||0)/100; }}); }}
}})();
</script>
"""

_SITE_ARAMA_KUTUSU = """
<div class="arama-kutusu">
    <div style="display: flex; gap: 8px;">
        <input type="text" id="site-arama-giris" placeholder="Hisse, konu veya tarih ara…" style="flex: 1; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; background: var(--card); color: var(--ink);" onkeypress="if(event.key === 'Enter') siteAra();">
        <button onclick="siteAra()" id="site-arama-btn" style="background: #0f766e; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: 600;">Ara</button>
    </div>
    <div id="site-arama-sonuc" style="display: none; margin-top: 10px; border-top: 1px solid var(--line); padding-top: 8px; max-height: 320px; overflow-y: auto;"></div>
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
    """Eski Puter tabanli AI soru kutusu KALDIRILDI (2026-09-21).
    Ziyaretcinin Puter kotasini/oturumunu gerektirdigi icin yayindan cikarildi.
    Ileride sunucu tarafi bir ucnokta (functions/api/...) ile geri gelebilir."""
    return ""


def _ai_panel():
    """AI yanit paneli + Puter cagrisi KALDIRILDI (2026-09-21)."""
    return ""


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
            {"h": s["hisse"], "f": s["son"], "d": s["gunluk"], "s": s.get("genel", "")}
            for s in satirlar
        ],
    }
    with open("ticker.json", "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)


def _kendi_ticker2(kok=""):
    """Ikinci kayan serit: endeksler, altin, doviz ve petrol.

    Veri /api/piyasa ucundan (Cloudflare Pages Function -> TradingView) gelir ve
    hisse seridi gibi 5 dk'da bir tazelenir. Uc erisilemezse gunluk bot kosusunun
    yazdigi piyasa-serit.json yedegine duser."""
    return f"""
<div class="ticker-bant" id="ticker-bant2" data-kok="{kok}" style="margin-bottom:16px">
  <div class="ticker-iz" id="ticker-iz2"><span style="color:#94a3b8">Piyasa verileri yükleniyor...</span></div>
</div>
<script>
(function() {{
  var kutu = document.getElementById('ticker-bant2');
  if (!kutu) return;
  var kok = kutu.dataset.kok || '';
  var iz = document.getElementById('ticker-iz2');

  function bicim(g) {{
    var deger = Number(g.f).toLocaleString('tr-TR', {{minimumFractionDigits: g.o, maximumFractionDigits: g.o}});
    var rozet = (typeof g.d === 'number')
      ? ' <span class="' + (g.d >= 0 ? 'pos' : 'neg') + '">' + (g.d >= 0 ? '▲ +' : '▼ ') +
        Number(g.d).toFixed(2) + '%</span>'
      : '';
    var birim = g.b || g.birim || '';
    return '<span class="ticker-oge"><b>' + g.ad + '</b> ' + deger + (birim ? ' ' + birim : '') + rozet + '</span>';
  }}
  function ciz(veri) {{
    var ogeler = (veri.gostergeler || []).map(bicim).join('');
    if (!ogeler) throw new Error('bos');
    var saat = '<span class="ticker-oge ticker-saat">' + (veri.guncelleme || '') +
               (veri.etiket ? ' · ' + veri.etiket : '') + '</span>';
    iz.innerHTML = ogeler + saat + ogeler + saat;  // sorunsuz dongu icin kopya
  }}
  function yukle(adres) {{
    fetch(adres, {{ cache: 'no-store' }})
      .then(function(r) {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
      .then(ciz)
      .catch(function() {{
        if (adres.indexOf('/api/') !== -1) {{ yukle(kok + 'piyasa-serit.json?t=' + Date.now()); return; }}
        if (iz) iz.innerHTML = '<span style="color:#94a3b8">Piyasa verileri geçici olarak yüklenemedi; kısa süre içinde yeniden denenecek.</span>';
      }});
  }}
  yukle('/api/piyasa?t=' + Date.now());
  setInterval(function() {{ yukle('/api/piyasa?t=' + Date.now()); }}, 5 * 60 * 1000);
}})();
</script>"""


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
  function yukle(adres) {{
    fetch(adres, {{ cache: 'no-store' }})
      .then(function(r) {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
      .then(ciz)
      .catch(function() {{
        if (adres.indexOf('/api/') !== -1) {{ yukle(kok + 'ticker.json?t=' + Date.now()); return; }}
        var iz2 = document.getElementById('ticker-iz');
        if (iz2) iz2.innerHTML = '<span style="color:#94a3b8">Fiyatlar geçici olarak yüklenemedi; kısa süre içinde yeniden denenecek.</span>';
      }});
  }}
  yukle('/api/fiyatlar?t=' + Date.now());
  setInterval(function() {{ yukle('/api/fiyatlar?t=' + Date.now()); }}, 5 * 60 * 1000);
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


def _chart_js_script(grafik):
    """Chart.js yalnizca gercekten grafik cizen sayfalara eklenir; 200 KB'lik
    kutuphaneyi 57 sayfanin tamamina yuklemek Core Web Vitals'i bosuna
    yormaktaydi. Render-blocking olmamasi icin defer ile yuklenir; grafik
    init'leri DOMContentLoaded'a baglanmali."""
    if not grafik:
        return ""
    return '<script defer src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>'


def _sayfa(title, icerik, aktif="raporlar", kok="", aciklama=None, yol=None, ld_ek=None, grafik=False):
    """Tum sayfalar icin ortak iskelet (ust menu + govde + altbilgi).

    aciklama: <meta name="description"> ve og:description icin kisa ozet.
    yol: sayfanin site kokune gore yolu (canonical + og:url icin; or.
    "teknik-analiz.html", "reports/2026-09-08.html", "" = ana sayfa).
    ld_ek: varsa <head>'e eklenen ikinci JSON-LD blogu (or. Article semasi)."""
    a_r = ' class="active"' if aktif == "raporlar" else ""
    a_p = ' class="active"' if aktif == "portfoy" else ""
    a_t = ' class="active"' if aktif == "teknik" else ""
    a_b = ' class="active"' if aktif == "borsapy" else ""
    a_d = ' class="active"' if aktif == "derin" else ""
    a_mk = ' class="active"' if aktif == "makro" else ""
    a_h = ' class="active"' if aktif == "haftasonu" else ""
    a_e = ' class="active"' if aktif == "egitim" else ""
    a_his = ' class="active"' if aktif == "hisseler" else ""
    a_hb = ' class="active"' if aktif == "haberler" else ""
    a_shb = ' class="active"' if aktif == "sirkethaber" else ""
    a_s = ' class="active"' if aktif == "sozluk" else ""
    a_trm = ' class="active"' if aktif == "terimler" else ""
    a_muh = ' class="active"' if aktif == "muhasebe" else ""
    a_k = ' class="active"' if aktif == "karne" else ""
    a_alt_r = ' class="active"' if aktif == "raporlar" else ""
    a_alt_t = ' class="active"' if aktif == "teknik" else ""
    a_alt_his = ' class="active"' if aktif == "hisseler" else ""
    a_alt_p = ' class="active"' if aktif == "portfoy" else ""
    a_alt_hb = ' class="active"' if aktif == "haberler" else ""
    a_tkv = ' class="active"' if aktif == "takvim" else ""
    a_alt_tkv = ' class="active"' if aktif == "takvim" else ""
    tam_url = SITE_URL + _guzel_url(yol.lstrip("/") if yol else "")
    if not aciklama:
        aciklama = "Yapay zeka destekli günlük BIST 30 analizleri: teknik tarama, osilatör sinyalleri, model portföy ve sanal portföy takibi."
    ld_ek_html = f'<script type="application/ld+json">{ld_ek}</script>' if ld_ek else ""
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
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{SITE_ADI} kapak görseli">
<meta property="og:locale" content="tr_TR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{aciklama}">
<meta name="twitter:image" content="{SITE_URL}og-cover.png">
{_dogrulama_etiketleri()}<script type="application/ld+json">{ld_json}</script>{ld_ek_html}
<script>(function(){{try{{var t=localStorage.getItem('tema');if(t==='dark'||(!t&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){{document.documentElement.classList.add('dark');}}}}catch(e){{}}}})();</script>
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link rel="stylesheet" href="{kok}style.css">
{_chart_js_script(grafik)}
</head>
<body>
<header class="topbar"><div class="inner">
<div class="brand-row"><a class="brand" href="{kok}index.html">BIST 30 Günlük Raporlar</a>
<button type="button" class="theme-btn" id="tema-btn" onclick="temaDegistir()" title="Açık/Koyu tema" aria-label="Tema değiştir">🌙</button></div>
<nav><a href="{kok}index.html"{a_r}>Raporlar</a><a href="{kok}hisse/index.html"{a_his}>Hisseler</a><a href="{kok}derin-analiz.html"{a_d}>Derin Analiz</a><a href="{kok}makro-analiz.html"{a_mk}>Makro Analiz</a><a href="{kok}teknik-analiz.html"{a_t}>Teknik Tarama</a><a href="{kok}sinyal-karnesi.html"{a_k}>Sinyal Karnesi</a><a href="{kok}borsapy-analiz.html"{a_b}>Borsapy Sinyal</a><a href="{kok}haberler.html"{a_hb}>Haberler</a><a href="{kok}sirket-haberleri.html"{a_shb}>Şirket Haberleri</a><a href="{kok}portfolio.html"{a_p}>Deneme Portföyü</a><a href="{kok}haftasonu.html"{a_h}>Hafta Sonu</a><a href="{kok}haftasonu-egitimi.html"{a_e}>Borsa Okulu</a><a href="{kok}takvim.html"{a_tkv}>📅 Takvim</a><a href="{kok}sozluk.html"{a_s}>Sözlük</a><a href="{kok}terimler.html"{a_trm}>Terimler</a><a href="{kok}muhasebe-terimleri.html"{a_muh}>Muhasebe Terimleri</a></nav>
</div></header>
{_kendi_ticker(kok)}
{_kendi_ticker2(kok)}

<main class="wrap">
    <!-- Üst Widget Alanı (Canlı Saat, İstanbul Hava Durumu ve GLM Çeviri) -->
    <div class="site-widgets">
        <div id="live-clock-weather" style="display: flex; gap: 15px; align-items: center; flex-wrap: wrap;">
            <span id="current-date-time">⏳ Yükleniyor...</span>
            <span id="istanbul-weather">🌤️ İstanbul Hava Durumu...</span>
        </div>
{_ceviri_widget(kok, yol)}
    </div>

    <!-- Etkileşimli Araçlar (Site İçi Arama ve BIST AI Asistan) -->
    <div class="interactive-box">
{_site_arama_kutusu(kok)}
{_radyo_kutusu(kok)}
    </div>

    <!-- Asıl Sayfa İçeriği -->
    {icerik}
</main>

<nav class="altbar" aria-label="Hızlı menü">
<a href="{kok}index.html"{a_alt_r}><span class="i" aria-hidden="true">📊</span>Raporlar</a>
<a href="{kok}teknik-analiz.html"{a_alt_t}><span class="i" aria-hidden="true">📈</span>Teknik</a>
<a href="{kok}hisse/index.html"{a_alt_his}><span class="i" aria-hidden="true">🏦</span>Hisseler</a>
<a href="{kok}portfolio.html"{a_alt_p}><span class="i" aria-hidden="true">💼</span>Portföy</a>
<a href="{kok}haberler.html"{a_alt_hb}><span class="i" aria-hidden="true">📰</span>Haberler</a>
<a href="{kok}takvim.html"{a_alt_tkv}><span class="i" aria-hidden="true">📅</span>Takvim</a>
</nav>

<script>
function temaDegistir() {{
  var kok = document.documentElement;
  kok.classList.toggle('dark');
  var koyu = kok.classList.contains('dark');
  try {{ localStorage.setItem('tema', koyu ? 'dark' : 'light'); }} catch (e) {{}}
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = koyu ? '☀️' : '🌙';
}}
(function() {{
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = document.documentElement.classList.contains('dark') ? '☀️' : '🌙';
}})();
</script>

<footer class="footer">Burada yer alan bilgi, yorum ve öneriler bilgilendirme amaçlıdır; yatırım danışmanlığı kapsamında değildir, yatırım tavsiyesi değildir. Verilerin doğruluğunun garanti edilmesi mümkün olmayıp içerikten doğabilecek her türlü kararda sorumluluk kullanıcıya aittir.<br>Veri kaynakları: İş Yatırım, RSS haber akışları &bull; Analiz: yapay zeka (çok-ajanlı sistem)<br><a href="{kok}gizlilik.html" style="color:inherit">Gizlilik &amp; KVKK</a></footer>

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


def html_temizle(html_metin):
    """LLM cikisindan gelen HTML'de tehlikeli kirimlari kaldirir (XSS savunmasi).

    Rapor icerigi haber RSS'leri + model cikisindan beslendigi icin, zehirli
    bir haber basliginin modele ettirilip sayfaya <script>/onerror= olarak
    sizmasi teorik olarak mumkundu. Python-markdown ham HTML'i oldugu gibi
    gecirdigi icin burada beyaz-yaklasimli bir son temizlik yapilir:
      - <script>/<iframe>/<object>/<embed>/<style>/<form>/<link>/<meta> bloklari
      - onclick=, onerror= ... gibi tum on* olay nitelikleri
      - href/src icinde javascript: / data: / vbscript: URL'leri
    """
    # Tehlikeli blok etiketleri (icerikleriyle birlikte)
    html_metin = re.sub(r"<\s*(script|iframe|object|embed|style|form|link|meta)\b[^>]*>.*?<\s*/\s*\1\s*>",
                        "", html_metin, flags=re.S | re.I)
    # Kapanmamis/acikta kalan tehlikeli etiketler
    html_metin = re.sub(r"<\s*/?\s*(script|iframe|object|embed|form|meta|link)\b[^>]*>", "", html_metin, flags=re.I)
    # on* olay nitelikleri (onclick, onerror, onload, ...)
    html_metin = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html_metin, flags=re.I)
    # javascript:/data:/vbscript: URL'leri (href/src/action gibi niteliklerde)
    html_metin = re.sub(r"(\s(?:href|src|action|formaction|xlink:href)\s*=\s*[\"']?)\s*(?:javascript|data|vbscript):[^\"'\s>]*",
                        r"\1#", html_metin, flags=re.I)
    return html_metin


def markdown_to_html(metin):
    import re
    # Paragraf icindeki "- " satirlarinin gercek liste olmasi icin bos satir ekle
    metin = re.sub(r"(?<!\n)\n(\s*[-*] )", r"\n\n\1", metin)
    return html_temizle(
        markdown.markdown(metin, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])
    )


def paylas_html(metin, url):
    """Rapor/hisse sayfalarina kucuk sosyal paylasim rozetleri
    (X, Telegram, WhatsApp + baglanti kopyala)."""
    import urllib.parse
    m = urllib.parse.quote_plus(metin)
    u = urllib.parse.quote_plus(url)
    return (f'<span class="paylas-kutu" style="display:inline-flex; gap:5px; align-items:center; margin-left:2px">'
            f'<a class="paylas" href="https://twitter.com/intent/tweet?text={m}&url={u}" target="_blank" rel="noopener" title="X\'te paylaş">𝕏</a>'
            f'<a class="paylas" href="https://t.me/share/url?url={u}&text={m}" target="_blank" rel="noopener" title="Telegram\'da paylaş">✈</a>'
            f'<a class="paylas" href="https://wa.me/?text={m}%20{u}" target="_blank" rel="noopener" title="WhatsApp\'ta paylaş">💬</a>'
            f'<a class="paylas" href="javascript:void(0)" title="Bağlantıyı kopyala" '
            f'onclick="if(navigator.clipboard){{navigator.clipboard.writeText(\'{url}\');this.textContent=\'✓\';}}">🔗</a></span>')


def _pano_html(satirlar, kok=""):
    """Gunluk BIST30 panosu (arastirma evi bulteni tarzi): piyasa verileri
    kutusu + manset satirlari + 30 hisselik gunluk degisim tablosu +
    sektor ortalamalari. Tamamen teknik tarama ve portfoy kur verisinden
    hesaplanir; dis veri/tavsiye icermez."""
    if not satirlar:
        return ""
    sirali = sorted(satirlar, key=lambda s: (s.get("gunluk", 0) or 0), reverse=True)
    gunlukler = [s.get("gunluk", 0) or 0 for s in sirali]
    yukselen = sum(1 for g in gunlukler if g > 0)
    dusen = sum(1 for g in gunlukler if g < 0)
    ort = sum(gunlukler) / len(gunlukler) if gunlukler else 0.0
    en_yuk = sirali[:3]
    en_dus = list(reversed(sirali[-3:]))

    guclu_al = sum(1 for s in satirlar if s.get("genel") == "GÜÇLÜ AL")
    al = sum(1 for s in satirlar if s.get("genel") == "AL")
    sat_s = sum(1 for s in satirlar if s.get("genel") == "SAT")
    guclu_sat = sum(1 for s in satirlar if s.get("genel") == "GÜÇLÜ SAT")
    notr = len(satirlar) - guclu_al - al - sat_s - guclu_sat

    usd = altin = ""
    try:
        p = load_portfolio()
        if p and p.get("history"):
            rates = (p["history"][-1].get("rates") or {})
            if rates.get("USD"):
                usd = f'{_ts(rates["USD"])}'.replace(".", ",")
            if rates.get("GOLD"):
                altin = f'{_ts0(rates["GOLD"])}'
    except Exception:
        pass

    # Gercek endeks seviyeleri + dolar bazindaki performans. "BIST 30 Sepeti"
    # 30 hissenin gunluk ortalamasidir; XU030 resmi endeksten ayridir.
    bugun = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul"))
    bugun_str = (f"{bugun.day} {_AYLAR[bugun.month - 1]} {bugun.year}, "
                 f"{_GUN_ADLARI[bugun.weekday()]}")
    endeks_hucreleri = ""
    pv = piyasa_verisi()
    if pv and "XU030" in pv:
        v = pv["XU030"]
        endeks_hucreleri += (
            f'<div class="pano-hucre"><div class="pano-etiket">XU030 Endeks</div>'
            f'<div class="pano-deger">{_ts(v["son"])} '
            f'<span class="{_renk(v["deg"])}">{_ty(v["deg"])}%</span></div></div>'
            f'<div class="pano-hucre"><div class="pano-etiket">XU030 Önceki Kapanış</div>'
            f'<div class="pano-deger">{_ts(v["onceki"])}</div></div>'
        )
    if pv and "XU100" in pv:
        v = pv["XU100"]
        endeks_hucreleri += (
            f'<div class="pano-hucre"><div class="pano-etiket">XU100</div>'
            f'<div class="pano-deger">{_ts(v["son"])} '
            f'<span class="{_renk(v["deg"])}">{_ty(v["deg"])}%</span></div></div>'
        )
    if pv and "XU030USD" in pv:
        d = pv["XU030USD"]["deg"]
        endeks_hucreleri += (
            f'<div class="pano-hucre"><div class="pano-etiket">XU030 Dolar Bazında</div>'
            f'<div class="pano-deger {_renk(d)}">{_ty(d)}%</div></div>'
        )

    def _fmt(v):
        return f"{_ty(v)}%".replace(".", ",")

    sektor_ort = {}
    for s in satirlar:
        sektor_ort.setdefault(s.get("sektor", "Diğer"), []).append(s.get("gunluk", 0) or 0)
    sektorler = sorted(((k, sum(v) / len(v)) for k, v in sektor_ort.items()),
                       key=lambda kv: kv[1], reverse=True)
    sektor_html = " &bull; ".join(
        f"{k} <span class='{_renk(v)}'>{_fmt(v)}</span>" for k, v in sektorler)

    def _sinyal(genel):
        return ('pos' if genel in ("GÜÇLÜ AL", "AL")
                else 'neg' if genel in ("SAT", "GÜÇLÜ SAT") else '')

    satir_html = "\n".join(
        f"<tr><td>{i}</td>"
        f"<td><a href='{kok}hisse/{s['hisse']}.html'><strong>{s['hisse']}</strong></a></td>"
        f"<td>{_ts(s.get('son', 0))}</td>"
        f"<td class='{_renk(s.get('gunluk', 0))}'><strong>{_fmt(s.get('gunluk', 0) or 0)}</strong></td>"
        f"<td class='{_renk(s.get('deg60', 0))}'>{_ty1((s.get('deg60', 0) or 0))}%</td>"
        f"<td>%{_ts0((s.get('konum', 0) or 0))}</td>"
        f"<td class='{_sinyal(s.get('genel', ''))}'><strong>{s.get('genel', '')}</strong></td></tr>"
        for i, s in enumerate(sirali, 1))

    kapsam = f"{len(sirali)}/{len(HISSELER)}"
    eksik_sembol = sorted({h for h in HISSELER} - {s["hisse"] for s in satirlar})
    gecikmeli = sorted(s["hisse"] for s in satirlar if s.get("stale"))
    uyari_html = ""
    if eksik_sembol:
        uyari_html += (' <strong style="color:#b91c1c">UYARI: ' + str(len(eksik_sembol)) +
                       ' hisse verisi alinamadi: ' + ", ".join(eksik_sembol) + '</strong>')
    if gecikmeli:
        uyari_html += ' <span style="color:#8a6512">Yerel seriden hesaplandi: ' + ", ".join(gecikmeli) + '.</span>'
    return f"""
<div class="pano">
<div class="pano-baslik">📊 Günün Panosu — BIST 30 · {kapsam} hisse · {bugun_str}, {bugun.strftime('%H:%M')} (TSİ)</div>
<div class="pano-veri">
  {endeks_hucreleri}
  <div class="pano-hucre"><div class="pano-etiket">BIST 30 Sepeti</div><div class="pano-deger {_renk(ort)}">{_fmt(ort)}</div></div>
  <div class="pano-hucre"><div class="pano-etiket">Yükselen / Düşen</div><div class="pano-deger">{yukselen} / {dusen}</div></div>
  <div class="pano-hucre"><div class="pano-etiket">$/TL</div><div class="pano-deger">{usd or '—'}</div></div>
  <div class="pano-hucre"><div class="pano-etiket">Gram Altın</div><div class="pano-deger">{(altin + ' TL') if altin else '—'}</div></div>
  <div class="pano-hucre"><div class="pano-etiket">Sinyal (AL / SAT)</div><div class="pano-deger">{guclu_al + al} / {sat_s + guclu_sat}</div></div>
</div>
<div class="pano-man">
<div><span class="pos">▲ En çok yükselenler:</span> {" &bull; ".join(f"<b>{s['hisse']}</b> {_fmt(s.get('gunluk') or 0)}" for s in en_yuk)}</div>
<div><span class="neg">▼ En çok düşenler:</span> {" &bull; ".join(f"<b>{s['hisse']}</b> {_fmt(s.get('gunluk') or 0)}" for s in en_dus)}</div>
<div>📣 Sinyal dağılımı: GÜÇLÜ AL {guclu_al} &middot; AL {al} &middot; NÖTR {notr} &middot; SAT {sat_s} &middot; GÜÇLÜ SAT {guclu_sat}</div>
<div>🏷️ Sektör ortalamaları: {sektor_html}</div>
</div>
<div class="tbl-wrap">
<table class="pano-tablo">
<thead><tr><th>#</th><th>Hisse</th><th>Son (TL)</th><th>Günlük</th><th>60 Gün</th><th>Kanal</th><th>Sinyal</th></tr></thead>
<tbody>
{satir_html}
</tbody>
</table>
</div>
<p class="pano-not">Tablo, site teknik taramasından derlenmiştir; eğitim amaçlıdır, yatırım tavsiyesi değildir.{uyari_html}</p>
</div>"""


def _yazim_ani_str():
    """Raporun yazildigi ani Turkiye saatiyle kisa etiket olarak dondurur."""
    an = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul"))
    return f"Yaz\u0131m saati: {an.strftime('%H:%M')} (TS\u0130)"


def rapor_sayfasi(html_icerik, date_str, baslik="Günlük Piyasa Raporu",
                  alt_baslik="BIST 30 &bull; Yapay zeka destekli günlük analiz",
                  kok_yol=None, aciklama=None, ses_url=None, teknik_satirlar=None,
                  ajanda=None):
    if kok_yol is None:
        kok_yol = f"reports/{date_str}.html"
    if aciklama is None:
        aciklama = (f"{date_str} tarihli BIST 30 {baslik.lower()}: yönetici özeti, haber ve makro "
                    f"değerlendirme, hisse bazlı teknik analiz ve model portföy önerisi.")
    # Ses: kucuk "🔊 Sesli Oku" dugmesi tarayicinin yerlesik sesiyle okur
    # (_sesli_okuma_js); MP3 varsa tek satirlik karmasik olmayan oynatici.
    # Eski kocaman "Raporu Dinle" kutusu kaldirildi (fazla yer kapliyordu).
    ses_bolumu = ""
    if ses_url:
        ses_bolumu = f"""
<div class="card" style="margin:0 0 18px; padding:8px 14px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
  <strong style="font-size:13px; white-space:nowrap">🔊 Sesli bülten</strong>
  <audio controls preload="none" src="{ses_url}" style="flex:1; min-width:220px; height:34px;"></audio>
</div>"""
    kok = "../" if "/" in (kok_yol or "") else ""
    icerik = f"""
<div class="hero">
<h1>{baslik}</h1>
<div class="meta"><span class="badge">{date_str}</span><span class="badge">{_yazim_ani_str()}</span><span>{alt_baslik}</span>
<button type="button" class="ses-btn" id="sesli-okuma-btn" onclick="sesliOkuToggle(this,'rapor-govde')" aria-label="Raporu sesli oku">🔊 Sesli Oku</button>
{paylas_html(baslik + ' ' + date_str, SITE_URL + (kok_yol or f"reports/{date_str}.html"))}</div>
</div>
{_bist30_sepeti_sparkline()}
{ses_bolumu}
{_pano_html(teknik_satirlar, kok)}
{_ajanda_html(ajanda or [], limit=14, kok=kok)}
<article class="report" id="rapor-govde">{html_icerik}</article>
<p style="margin-top:18px"><a href="{kok}index.html">&larr; Tüm raporlara dön</a></p>
{_sesli_okuma_js()}"""
    ld_ek = json.dumps({
        "@context": "https://schema.org", "@type": "Article",
        "headline": f"{baslik} — {date_str}", "datePublished": date_str,
        "dateModified": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).isoformat(timespec="seconds"),
        "inLanguage": "tr", "description": aciklama,
        "author": {"@type": "Organization", "name": SITE_ADI, "url": SITE_URL},
        "publisher": {"@type": "Organization", "name": SITE_ADI, "url": SITE_URL},
        "mainEntityOfPage": SITE_URL + (kok_yol or ""),
    }, ensure_ascii=False)
    return _sayfa(f"{baslik} - {date_str}", icerik, "raporlar", kok=kok,
                  aciklama=aciklama, yol=kok_yol, ld_ek=ld_ek)


def build_html(report, date_str, teknik_satirlar=None, ajanda=None):
    ses_url = None
    if os.path.exists(os.path.join("reports", f"{date_str}.mp3")):
        ses_url = f"../reports/{date_str}.mp3"
    return rapor_sayfasi(markdown_to_html(report), date_str, ses_url=ses_url,
                         teknik_satirlar=teknik_satirlar, ajanda=ajanda)


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
    xu030 = [item.get("XU030") for item in benchmarks]
    xu030_var = all(v is not None for v in xu030) and any(xu030)
    xu030_veri = [round(float(v), 2) for v in xu030] if xu030_var else None

    # Enflasyon kiyas cizgisi: son yayinlanan TUIK yillik TUFE (TradingView
    # ekonomik takvim ucundan cekilir; erisilemezse cache/varsayilan).
    enflasyon_oran, enflasyon_etiket = _enflasyon_yukle()
    try:
        bas_t = datetime.strptime(history[0]["date"], "%Y-%m-%d")
        enflasyon_veri = [
            round(100000 * ((1 + enflasyon_oran) ** ((datetime.strptime(item["date"], "%Y-%m-%d") - bas_t).days / 365)), 2)
            for item in history
        ]
    except Exception:
        enflasyon_veri = None

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
    if xu030_veri:
        datasetler.append({"label": "BIST 30 Endeksi", "data": xu030_veri, "borderColor": "#e11d48", "backgroundColor": "#e11d48", "borderDash": [2, 2]})
    if enflasyon_veri:
        datasetler.append({"label": enflasyon_etiket, "data": enflasyon_veri, "borderColor": "#c026d3", "backgroundColor": "#c026d3", "borderDash": [4, 3]})
    veri = {"labels": etiketler, "datasets": datasetler}
    veri_json = _json.dumps(veri, ensure_ascii=False)

    return f"""
<div class="pgrafik-sarmal">
<div style="position:relative; height:340px; min-width:640px;">
<canvas id="{grafik_id}"></canvas>
</div>
<p class="pgrafik-ipucu">↔ Grafiği sağa-sola kaydırabilirsiniz</p>
</div>
<style>
.pgrafik-sarmal{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.pgrafik-ipucu{{display:none;margin:6px 0 0;color:var(--muted);font-size:12px;text-align:center}}
@media(max-width:700px){{.pgrafik-ipucu{{display:block}}}}
</style>
<script>
document.addEventListener('DOMContentLoaded', function() {{
(function() {{
    var veri = {veri_json};
    var baslangiclar = veri.datasets.map(function(d) {{ return d.data[0]; }});
    var mobil = window.matchMedia('(max-width: 700px)').matches;
    var ctx = document.getElementById('{grafik_id}').getContext('2d');
    new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: veri.labels,
            datasets: veri.datasets.map(function(d) {{
                return Object.assign({{}}, d, {{
                    borderWidth: mobil ? 2 : 3,
                    pointRadius: mobil ? 0 : 2,
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
                legend: {{ position: 'top', labels: {{ boxWidth: mobil ? 10 : 12, padding: mobil ? 6 : 10, font: {{ size: mobil ? 11 : 12, weight: '600' }} }} }},
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
                y: {{ ticks: {{ callback: function(v) {{ return v.toLocaleString('tr-TR') + (mobil ? '' : ' TL'); }} }} }},
                x: {{ ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: mobil ? 6 : 8 }} }}
            }}
        }}
    }});
}})();
}});
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
        f"<tr><td><strong>{h}</strong></td><td>{_ts(p['shares'][h])}</td>"
        f"<td>{_ts(p['initial_prices'].get(h, 0))} TL</td>"
        f"<td>{_ts(son['prices'].get(h, p['initial_prices'].get(h, 0)))} TL</td>"
        f"<td class='{_renk(fark)}'>{_ty(fark)}%</td></tr>"
        for h, fark in satirlar
    )


def _portfoy_risk(p):
    """Gecmis veriden risk/getiri olcutleri: maksimum dusus, volatilite
    (yilliklandirilmis), yilliklandirilmis getiri, kazanan/kaybeden hisse sayisi."""
    hist = [g for g in (p.get("history") or []) if g.get("total")]
    if len(hist) < 3:
        return None
    toplamlar = [float(g["total"]) for g in hist]
    tepe, dd = toplamlar[0], 0.0
    for t in toplamlar:
        tepe = max(tepe, t)
        if tepe > 0:
            dd = min(dd, t / tepe - 1.0)
    gunluk = [float(g.get("daily_pct", 0) or 0) / 100.0 for g in hist[1:]]
    ort = sum(gunluk) / len(gunluk) if gunluk else 0.0
    varyans = (sum((x - ort) ** 2 for x in gunluk) / (len(gunluk) - 1)
               if len(gunluk) > 1 else 0.0)
    vol = (varyans ** 0.5) * (252 ** 0.5) * 100
    gun_sayisi = len(toplamlar) - 1
    yillik = (((toplamlar[-1] / toplamlar[0]) ** (252.0 / gun_sayisi) - 1) * 100
              if gun_sayisi > 0 and toplamlar[0] > 0 else 0.0)
    baslangic = p.get("initial_prices") or {}
    sonuncu = hist[-1].get("prices") or {}
    kazanan = kaybeden = 0
    for kod, ilk in baslangic.items():
        son_f = sonuncu.get(kod)
        if not ilk or son_f is None:
            continue
        if son_f >= ilk:
            kazanan += 1
        else:
            kaybeden += 1
    return {"dd": dd * 100, "vol": vol, "yillik": yillik,
            "kazanan": kazanan, "kaybeden": kaybeden, "gun": gun_sayisi}


def _portfoy_istatistikleri(p):
    son = p["history"][-1]
    risk = _portfoy_risk(p) or {}
    risk_html = ""
    if risk:
        risk_html = (
            f'<div class="stat"><div class="label">Maksimum Düşüş</div>'
            f'<div class="value neg">{_ty(risk["dd"])}%</div></div>'
            f'<div class="stat"><div class="label">Volatilite (yıllık)</div>'
            f'<div class="value">{_ts1(risk["vol"])}%</div></div>'
            f'<div class="stat"><div class="label">Yıllıklandırılmış Getiri</div>'
            f'<div class="value {_renk(risk["yillik"])}">{_ty(risk["yillik"])}%</div>'
            f'<div style="font-size:11px;color:var(--muted)">kısa dönem — ihtiyatlı okunmalı</div></div>'
            f'<div class="stat"><div class="label">Kazanan / Kaybeden</div>'
            f'<div class="value">{risk["kazanan"]} / {risk["kaybeden"]}</div>'
            f'<div style="font-size:11px;color:var(--muted)">{risk["gun"]} işlem günü</div></div>'
        )
    kiyas = ""
    bm = (son.get("benchmarks") or {})
    adlar = {"XU030": "BIST 30", "XU100": "BIST 100", "GOLD": "Gram altın",
             "USD": "Dolar", "DEPOSIT": "Mevduat (faiz)"}
    if bm:
        satir = [f"<tr><td><strong>Bu portföy</strong></td>"
                 f"<td class='{_renk(son['pct'])}'>{_ty(son['pct'])}%</td><td>—</td></tr>"]
        for anahtar in ("XU030", "XU100", "GOLD", "USD", "DEPOSIT"):
            deger = bm.get(anahtar)
            if not deger:
                continue
            yuzde = (float(deger) / float(p["initial_capital"]) - 1) * 100
            fark = son["pct"] - yuzde
            satir.append(
                f"<tr><td>{adlar.get(anahtar, anahtar)} (aynı dönem)</td>"
                f"<td class='{_renk(yuzde)}'>{_ty(yuzde)}%</td>"
                f"<td class='{_renk(fark)}'>{_ty(fark)} puan</td></tr>")
        kiyas = ("<h3 class=\"section-title\">Piyasa Karşılaştırması"
                 "<span style=\"font-size:12px;color:var(--muted);font-weight:400\">"
                 "(aynı 100.000 TL tabanı; son sütun portföyün farkı)</span></h3>"
                 "<div class=\"card\" style=\"padding:8px 24px 16px\"><div class=\"tbl-wrap\">"
                 "<table><tr><th>Karşılaştırma</th><th>Getiri</th><th>Portföy farkı</th></tr>"
                 + "".join(satir) + "</table></div></div>")
    return f"""
<div class="stats">
<div class="stat"><div class="label">Güncel Değer</div><div class="value">{_ts0(son['total'])} TL</div></div>
<div class="stat"><div class="label">Günlük Değişim</div><div class="value {_renk(son['daily_pct'])}">{_ty(son['daily_pct'])}%</div></div>
<div class="stat"><div class="label">Toplam Getiri</div><div class="value {_renk(son['pct'])}">{_ty(son['pct'])}%</div></div>
<div class="stat"><div class="label">Başlangıç</div><div class="value" style="font-size:16px">{p['start_date']}</div></div>
{risk_html}
</div>
{kiyas}
<p style="color:var(--muted); font-size:12px; margin:0 0 18px">Getiri tek başına başarı göstergesi değildir: portföy aynı dönemde BIST 30/100 ve altın/dolar ile karşılaştırılır. Pozitif getiri, endeksin altında kalıyorsa görece zayıftır.</p>"""


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
    # Derin analiz arsiv dosyalari rapor arsivine degil, kendi bolumune gider
    gundem_dosyalari = [fn for fn in (rapor_dosyalari or []) if "-derin-analiz" not in fn]
    derin_dosyalari = [fn for fn in (rapor_dosyalari or []) if "-derin-analiz" in fn]

    arsiv_bolumu = ""
    if gundem_dosyalari:
        GORUNEN_ARŞİV = 3
        kart_liste = [
            f'<a class="rcard" href="reports/{fn}"><span class="date">{_tr_tarih(fn[:-5])}</span>'
            f'<span class="sub">Günlük raporu aç &rarr;</span></a>'
            for fn in gundem_dosyalari
        ]
        gorunen = "".join(kart_liste[:GORUNEN_ARŞİV])
        ekler = ""
        if len(kart_liste) > GORUNEN_ARŞİV:
            kalan = len(kart_liste) - GORUNEN_ARŞİV
            gizli_kartlar = "".join(kart_liste[GORUNEN_ARŞİV:])
            # DIKKAT: buton + gizli grid, gorunen grid'in DIŞINDA kardes
            # element olarak eklenir; grid icine gomülürse iç içe grid
            # dar bir kolona sıkışıp dikey dizilir.
            ekler = f"""
<div style="margin:8px 0 0"><button type="button" onclick="arsivAc(this)" style="background:#fff; border:1px solid #cbd5e1; border-radius:8px; padding:7px 16px; font-size:13.5px; font-weight:600; color:#0f172a; cursor:pointer">Daha fazla göster ({kalan} gün)</button></div>
<div class="grid" id="arsiv-devam" style="display:none; margin-top:10px">{gizli_kartlar}</div>
<script>
function arsivAc(btn) {{
  var devam = document.getElementById('arsiv-devam');
  var kapali = devam.style.display === 'none';
  devam.style.display = kapali ? 'grid' : 'none';
  btn.textContent = kapali ? 'Daha az göster' : 'Daha fazla göster ({kalan} gün)';
}}
</script>"""
        arsiv_bolumu = f"""
<h2 class="section-title">Rapor Arşivi</h2>
<div class="grid">{gorunen}</div>{ekler}"""
    else:
        arsiv_bolumu = """
<h2 class="section-title">Rapor Arşivi</h2>
<p style="color:var(--muted)">Henüz rapor yok.</p>"""

    # Hafta sonu gundemi bolumu (en son hafta sonu sayisi; hafta ici de gosterilir)
    haftasonu_bolumu = ""
    try:
        hs_dosyalar = sorted(f for f in os.listdir("haftasonu") if f.endswith(".html"))
    except OSError:
        hs_dosyalar = []
    if hs_dosyalar:
        son_hs = hs_dosyalar[-1][:-5]
        haftasonu_bolumu = f"""
<h2 class="section-title">Hafta Sonu Gündemi</h2>
<div class="grid"><a class="rcard" href="haftasonu.html"><span class="date">{_tr_tarih(son_hs)}</span>
<span class="sub">Hafta sonu haberlerinden gündem değerlendirmesi & yeni hafta ajandası &rarr;</span></a></div>"""

    # Hafta sonu borsa okulu bolumu: son 4 ders tarih sirali kartlarla.
    # (Eskiden yalnizca en son ders gorunuyordu; digerleri arsivde kayiplasiyordu.)
    egitim_bolumu = ""
    try:
        eg_dosyalar = sorted(f for f in os.listdir("haftasonu-egitimi") if f.endswith(".html"))
    except OSError:
        eg_dosyalar = []
    if eg_dosyalar:
        eg_kartlar = "".join(
            f'<a class="rcard" href="haftasonu-egitimi/{fn}"><span class="date">{_tr_tarih(fn[:-5])}</span>'
            f'<span class="sub">Yapay zeka eğitmenden günün dersi &rarr;</span></a>'
            for fn in reversed(eg_dosyalar[-4:])
        )
        egitim_bolumu = f"""
<h2 class="section-title">Hafta Sonu Borsa Okulu</h2>
<div class="grid">{eg_kartlar}</div>
<p style="margin:10px 0 0"><a href="haftasonu-egitimi.html">Borsa Okulu sayfası &rarr;</a></p>"""

    # Derin analiz bolumu: guncel sayfa + arsivdeki son 3 analiz
    derin_bolumu = ""
    if derin_dosyalari:
        derin_kartlar = "".join(
            f'<a class="rcard" href="reports/{fn}"><span class="date">{_tr_tarih(fn[:-5].replace("-derin-analiz", ""))}</span>'
            f'<span class="sub">O günün derin analizi &rarr;</span></a>'
            for fn in derin_dosyalari[:3]
        )
        derin_bolumu = f"""
<h2 class="section-title">Derin Analiz</h2>
<div class="grid"><a class="rcard" href="derin-analiz.html"><span class="date">Güncel Derin Analiz</span>
<span class="sub">Piyasanın detaylı değerlendirmesi &rarr;</span></a>{derin_kartlar}</div>"""

    teknik_bolumu = ""
    if teknik_oneriler:
        kart = "".join(
            f'<a class="rcard" href="teknik-analiz.html"><span class="date">{s["hisse"]}</span>'
            f'<span class="sub">{s["genel"]} &bull; {_ts(s["son"])} TL &bull; kanal %{_ts0(s["konum"])} &bull; r={_ts(s["r"])}</span></a>'
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
<p>Hafta içi her sabah 08:00'de otomatik üretilen, yapay zeka destekli BIST 30 analizleri ve sanal portföy takibi. (Hafta sonu yayın yok — piyasa kapalı.)</p>
</div>
{arsiv_bolumu}
{derin_bolumu}
{haftasonu_bolumu}
{egitim_bolumu}
{teknik_bolumu}
{portfoy_bolumu}"""
    return _sayfa(
        "BIST 30 Günlük Raporlar", icerik, "raporlar", yol="",
        aciklama="BIST 30'un yapay zeka destekli günlük raporları, teknik tarama ve osilatör sinyalleri, model portföy önerileri ve 100.000 TL'lik sanal deneme portföyünün takibi.",
        grafik=True,
    )


def build_portfolio_html(p):
    son = p["history"][-1]
    ilk_gun = p["history"][0]["date"] if p.get("history") else ""
    grafik = sparkline_svg(p["history"])
    grafik_html = f'<div class="card" style="margin-bottom:22px">{grafik}</div>' if grafik else ""
    gecmis = "".join(
        f"<tr><td>{g['date']}</td><td>{_ts(g['total'])} TL</td>"
        f"<td class='{_renk(g['pct'])}'>{_ty(g['pct'])}%</td>"
        f"<td class='{_renk(g['daily_pct'])}'>{_ty(g['daily_pct'])}%</td></tr>"
        for g in reversed(p["history"])
    )
    icerik = f"""
<div class="hero">
<h1>Deneme Portföyü</h1>
<p>BIST 30 hisselerine eşit dağıtılmış {_ts0(p['initial_capital'])} TL'lik sanal portföy. Alım-satım yapılmaz, sadece takip edilir;
hafta içi her sabah bir önceki işlem gününün kapanış fiyatlarıyla otomatik güncellenir.</p>
</div>
<details class="card" style="margin-bottom:22px">
<summary style="cursor:pointer; font-weight:700">📐 Metodoloji — bu portföy nasıl hesaplanıyor?</summary>
<div style="margin-top:10px; font-size:14px; display:grid; gap:6px">
<p style="margin:0">• {ilk_gun} tarihinde 100.000 TL, BIST 30 hisselerine <strong>eşit dağıtıldı</strong>; adetler fraksiyoneldir, gerçek uygulamada tam lota yuvarlama gerekir.</p>
<p style="margin:0">• Hafta içi her sabah <strong>bir önceki işlem gününün kapanış fiyatlarıyla</strong> güncellenir; tatil günlerinde değer değişmez, hiç alım-satım yapılmaz.</p>
<p style="margin:0">• <strong>Temettü, bedelsiz ve bölünmeler şu an hesaba katılmaz</strong>; bu olayların geçtiği günlerde getiri sapması olabilir.</p>
<p style="margin:0">• Karşılaştırma çizgileri (altın, dolar, mevduat, BIST 100, BIST 30, enflasyon) aynı 100.000 TL tabanına normalize edilir; enflasyon değeri TradingView ekonomik takviminden gelen son TÜİK yıllık okumadır ve grafiğe dönem tarihiyle yansır.</p>
<p style="margin:0">• Toplam getiri, tekil hisse yüzdelerinin ortalaması değil, portföyün toplam değer değişimidir. Sinyal Karnesi sayfasındaki net getiri senaryosu ~%0,10 komisyon+BSMV varsayımı kullanır.</p>
<p style="margin:0; color:var(--muted)">Bu bir sanal deneme portföyüdür; yatırım tavsiyesi değildir. Geçmiş performans gelecekteki sonuçların garantisi değildir.</p>
</div>
</details>
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
        grafik=True,
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


FIYAT_DEPO_DIR = os.path.join(DATA_DIR, "fiyat")
FIYAT_DEPO_SINIR = 420


def fiyat_deposu_oku(hisse):
    """data/fiyat/<HISSE>.json icerigini {tarih: kapanis} olarak doner."""
    try:
        with open(os.path.join(FIYAT_DEPO_DIR, hisse + ".json"), encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def fiyat_deposu_yaz(hisse, seri):
    """{tarih: kapanis} ciftlerini depoya birlestirir; en yeni FIYAT_DEPO_SINIR gunu tutar."""
    try:
        d = fiyat_deposu_oku(hisse)
        for t, k in seri.items():
            try:
                d[str(t)] = round(float(k), 4)
            except (TypeError, ValueError):
                continue
        if len(d) > FIYAT_DEPO_SINIR:
            for t in sorted(d)[:-FIYAT_DEPO_SINIR]:
                d.pop(t, None)
        os.makedirs(FIYAT_DEPO_DIR, exist_ok=True)
        with open(os.path.join(FIYAT_DEPO_DIR, hisse + ".json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, sort_keys=True)
    except Exception:
        logger.warning("[Fiyat Deposu] %s yazilamadi.", hisse)

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
    # semboller icin ek deneme turlari yapilir; aralarda artan bekleme uygulanir.
    for ek_deneme in range(4):
        mevcut = set(df[kod_kolonu].astype(str).str.upper().unique())
        eksikler = [h for h in HISSELER if h not in mevcut]
        if not eksikler:
            break
        logger.warning("[Teknik Tarama] %d hisse eksik, ek deneme %d: %s",
                       len(eksikler), ek_deneme + 1, ", ".join(eksikler))
        print(f"[Teknik Tarama] {len(eksikler)} hisse icin ek deneme ({ek_deneme + 1}): {', '.join(eksikler)}", flush=True)
        try:
            df2 = fetch_stock_data(eksikler, start_date=baslangic, end_date=bitis)
            if df2 is not None and not df2.empty:
                df2.columns = [str(c).upper() for c in df2.columns]
                df = pd.concat([df, df2], ignore_index=True)
        except Exception as e:
            logger.warning("[Teknik Tarama] Ek deneme basarisiz: %s", e)
        time.sleep(min(4 * (ek_deneme + 1), 15))

    # Son care: hala gelmeyen sembolleri tek tek dene (toptan cagride zaman
    # asimina takilan sembol, tek cagride genelde geri doner).
    mevcut = set(df[kod_kolonu].astype(str).str.upper().unique())
    for h in [x for x in HISSELER if x not in mevcut]:
        try:
            dfh = fetch_stock_data([h], start_date=baslangic, end_date=bitis)
            if dfh is not None and not dfh.empty:
                dfh.columns = [str(c).upper() for c in dfh.columns]
                df = pd.concat([df, dfh], ignore_index=True)
                logger.info("[Teknik Tarama] %s tek tek alindi.", h)
        except Exception as e:
            logger.warning("[Teknik Tarama] %s tek tek de alinamadi: %s", h, str(e)[:80])
        time.sleep(2)

    # Fiyat serisi deposu: ham gunluk kapanislar yerelde tutulur
    # (data/fiyat/<HISSE>.json). Boylece kaynak zaman asimina takilsa bile
    # gostergeler yerelden hesaplanabilir; kaynak yalnizca tazeleme icin gerekir.
    tarih_kolonu = next((c for c in ["HGDG_TARIH", "TARIH", "HGDG_TARIH_T", "DATE"] if c in df.columns), None)
    if tarih_kolonu:
        depo_yazilan = 0
        for hisse in HISSELER:
            alt = df[df[kod_kolonu] == hisse][[tarih_kolonu, kapanis_kolonu]].dropna()
            if alt.empty:
                continue
            kayit = {}
            for _, satir in alt.iterrows():
                kayit[str(satir[tarih_kolonu])[:10]] = satir[kapanis_kolonu]
            fiyat_deposu_yaz(hisse, kayit)
            depo_yazilan += 1
        logger.info("[Fiyat Deposu] %d hisse icin kapanis serisi guncellendi.", depo_yazilan)
    else:
        logger.warning("[Fiyat Deposu] tarih kolonu bulunamadi; seri onbellegi atlandi.")

    # Gun ici canli fiyatlari TradingView'den al: isyatirimhisse gun sonu (EOD)
    # veri servis eder, piyasa acikken fiyatlar akmaz. borsapy (TradingView)
    # ~15 dk gecikmeli canli fiyat verir; kapali piyasada iki kaynak esittir
    # (o zaman ekleme yapilmaz ve tablo kapanis verisine doner).
    canli = {}

    # Birincil kaynak: TradingView scanner'a TEK istek (30 hisse ~0.3 sn).
    # Daha once hisse basina birer borsapy fast_info cagrisi yapiliyordu; CI
    # runner'inda cagri basina ~30 sn yavaslanyip 6-16 dk yeriyordu. Scanner
    # 'close' alani oturum acikken son islem fiyatini, kapaliyken kapanisi
    # verir (borsapy/TradingView ayni kaynagi servis eder).
    try:
        import urllib.request
        tv_govde = {"symbols": {"tickers": ["BIST:" + h for h in HISSELER],
                                "query": {"types": []}},
                    "columns": ["name", "close"]}
        tv_istek = urllib.request.Request(
            "https://scanner.tradingview.com/turkey/scan",
            data=json.dumps(tv_govde).encode(),
            headers={"User-Agent": "Mozilla/5.0",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(tv_istek, timeout=25) as r:
            tv_ham = json.loads(r.read().decode())
        for satir in tv_ham.get("data", []):
            d = satir.get("d") or []
            if len(d) >= 2 and d[0] and d[1] and float(d[1]) > 0:
                canli[str(d[0])] = float(d[1])
    except Exception as e:
        logger.warning("[Teknik Tarama] TradingView toplu canli fiyat cekilemedi: %s", str(e)[:120])

    # Scanner'dan gelmeyen semboller icin yedek: borsapy canli fiyat, paralel.
    eksikler = [h for h in HISSELER if h not in canli]
    if eksikler:
        try:
            import borsapy as bp
            from concurrent.futures import ThreadPoolExecutor

            def _canli_fiyat(hisse):
                try:
                    fi = bp.Ticker(hisse).fast_info
                    deger = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
                    if deger and float(deger) > 0:
                        return hisse, float(deger)
                except Exception:
                    pass
                return hisse, None

            with ThreadPoolExecutor(max_workers=8) as fiyat_havuzu:
                for hisse, deger in fiyat_havuzu.map(_canli_fiyat, eksikler):
                    if deger:
                        canli[hisse] = deger
        except ImportError:
            logger.warning("[Teknik Tarama] borsapy yok; gun ici canli fiyat kullanilamayacak.")
    logger.info("[Teknik Tarama] %d hisse icin canli fiyat alindi (TradingView).", len(canli))

    satirlar = []
    atlanan = []
    for sira, hisse in enumerate(HISSELER, 1):
        seri = df[df[kod_kolonu] == hisse][kapanis_kolonu].astype(float).dropna()
        if len(seri) < 145:  # EMA144 anlamlı olsun
            depo = fiyat_deposu_oku(hisse)
            if len(depo) >= 145:
                seri = pd.Series([depo[t] for t in sorted(depo)])
                yerel = True
                logger.warning("[Teknik Tarama] %d/%d %s: kaynaktan veri gelmedi; %d gunluk YEREL seriden hesaplandi.", sira, len(HISSELER), hisse, len(seri))
            else:
                logger.warning("[Teknik Tarama] %d/%d %s: yetersiz gecmis (%d gun), atlandi", sira, len(HISSELER), hisse, len(seri))
                atlanan.append(hisse)
                continue
        else:
            yerel = False
        # Is Yatirim serisinin sonuna canli fiyati ekle (kapanistan farkliyse):
        # boylece EMA/WT/regresyon tum gostergeler gun ici hareketle hesaplanir.
        iy_son = float(seri.iloc[-1])
        tv = canli.get(hisse)
        if (not yerel) and tv and abs(tv - iy_son) > 0.005:
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
            "puan": puansay, "genel": genel, "stale": yerel,
        })

    # Guclu AL'ler one, iclerinde trend gucu (Pearson) yuksek olanlar basta
    satirlar.sort(key=lambda s: (s["puan"], s["r"]), reverse=True)
    save_daily("teknik", bugun, satirlar)
    global _SON_TEKNIK
    _SON_TEKNIK = satirlar
    if atlanan:
        logger.warning("[Teknik Tarama] EVREN EKSIK: %d/%d hisse uretilemedi: %s",
                       len(atlanan), len(HISSELER), ", ".join(atlanan))
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
    # Günlük ısı haritası: değişime göre renk derinliği (yeşil yükselen, kırmızı düşen)
    def _isi_renk(deg):
        # DIKKAT: CSS rgba() ondalik ayirici olarak NOKTA ister. _ts() Turkce
        # bicim (virgul) urettigi icin burada KULLANILMAZ; aksi halde
        # 'rgba(4,120,87,0,77)' gecersiz olur ve hucreler renksiz kalir.
        a = min(0.15 + abs(deg) / 3.0 * 0.80, 0.92)
        return ("4,120,87" if deg >= 0 else "185,28,28") + f",{a:.2f}"

    sirali = sorted(satirlar, key=lambda x: x.get("gunluk", 0), reverse=True)
    isi = "".join(
        f"<div class='isi-hucre' style=\"background:rgba({_isi_renk(s.get('gunluk', 0))})\">"
        f"<b>{s['hisse']}</b><span>{_ty(s.get('gunluk', 0))}%</span></div>"
        for s in sirali
    )
    isi_bolumu = f"""
<h2 class="section-title">Günlük Isı Haritası</h2>
<div class="isi-harita">{isi}</div>
<p style="color:var(--muted); font-size:12px; margin:8px 0 0">Renk koyuluğu değişim büyüklüğünü gösterir — yeşil yükselenler, kırmızı düşenler. Hisse adına tıklayın: detay sayfası (fiyat grafiği, teknik durum, haberler).</p>"""

    satir_html = "".join(
        f"<tr style='cursor:pointer' onclick=\"tvAc('{s['hisse']}')\"><td><a style='color:inherit; text-decoration:none; font-weight:700' href='hisse/{s['hisse']}.html'>{s['hisse']}</a></td>"
        f"<td>{_ts(s['son'])} TL</td>"
        f"<td class='{_renk(s['gunluk'])}'>{_ty(s['gunluk'])}%</td>"
        f"<td class='{_renk(s['deg60'])}'>{_ty1(s['deg60'])}%</td>"
        f"{_teknik_sinyal_hucre(s['kisa'])}{_teknik_sinyal_hucre(s['orta'])}{_teknik_sinyal_hucre(s['uzun'])}"
        f"{_teknik_sinyal_hucre(s['wt'])}"
        f"<td>{_ts0(s['konum'])}%</td><td>{_ts(s['r'])}</td>"
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
gösterebilir. <strong>Mum grafiği için tablodaki bir hisseye tıklayın.</strong> Tüm sinyaller otomatik taramadan üretilir; yatırım tavsiyesi değildir.</p>
<button type="button" class="ses-btn" onclick="sesliOkuToggle(this,'teknik-ses-metin')" aria-label="Teknik tarama özetini sesli oku">🔊 Sesli Özeti Dinle</button>
<div id="teknik-ses-metin" hidden>{_teknik_ses_metni(satirlar)}</div>
</div>
{_sektor_isi_haritasi(satirlar)}
{isi_bolumu}
<div class="card" style="padding:8px 24px 16px">
<div class="tbl-wrap">
<table>
<tr><th>Hisse</th><th>Son</th><th>Gün</th><th>60G %</th><th>Kısa Vade</th><th>Orta Vade</th><th>Uzun Vade</th><th>Wave Trend</th><th>Kanal</th><th>Pearson</th><th>Genel</th></tr>
{satir_html}
</table>
</div>
<p style="margin:12px 0 4px; color:var(--muted); font-size:13px">Bugün {len(satirlar)} hisse tarandı; {guclu} hisse GÜÇLÜ AL sinyalinde. Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</p>
<p style="margin:0 0 6px"><a href="sinyal-karnesi.html">📊 <strong>Sinyal Karnesi</strong> — geçmiş AL sinyalleri 5 işlem günü sonra ne yapmış? &rarr;</a></p>
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
document.addEventListener('DOMContentLoaded', function() {{
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
}});
</script>"""

    guclu = sum(1 for s in satirlar if s["oneri"] == "GÜÇLÜ AL")
    icerik = f"""
<div class="hero">
<h1>Borsapy Sinyalleri</h1>
<p>TradingView teknik analiz göstergelerinin BIST 30 özeti (borsapy kütüphanesiyle çekilir):
toplam osilatör + hareketli ortalama oylarına göre genel öneri, RSI, MACD, Stokastik %K, CCI ve ADX.
Piyasa saatlerinde teknik taramayla birlikte 30 dakikada bir güncellenir.
<strong>Mum grafiği için tablodaki bir hisseye tıklayın.</strong> Sinyaller otomatik taramadan üretilir; yatırım tavsiyesi değildir.</p>
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
        grafik=True,
    )


# ---------- YENI BOLUM GENERATORLERI ----------
# Hisse detay sayfalari, sinyal karnesi (backtest), haber arsivi, sozluk,
# ekonomik takvim rehberi ve radyo podcast RSS'i uretir. Hepsi mevcut
# data/ dosyalarindan calisir; LLM cagrisi gerektirmez.


def style_css_yaz():
    """BASE_CSS'i tek dosyaya yazar; her sayfa <style> gömmeden link verir
    (sayfalar kuculur, tarayici CSS'i cache'ler)."""
    with open("style.css", "w", encoding="utf-8") as f:
        f.write(BASE_CSS)


def piyasa_serit_yaz():
    """Ikinci seridin YEDEK verisi (gunluk kosuda bir kez).

    /api/piyasa erisilemezse istemci bu dosyaya duser. Endeksler ve USD/TRY
    piyasa_verisi()'nden, gram altin _benchmarks_cek()'ten gelir; ons altin,
    doviz caprazlari ve Brent yalnizca canli ucda bulunur (botun kaynagi yok).
    """
    veri = piyasa_verisi() or {}
    gostergeler = []
    for anahtar, ad, birim, o in (("XU030", "BIST 30 (TL)", "", 2),
                                  ("XU100", "BIST 100 (TL)", "", 2)):
        v = veri.get(anahtar)
        if v and v.get("son"):
            gostergeler.append({"k": anahtar, "ad": ad, "f": round(v["son"], 2),
                                "d": round(v.get("deg", 0), 2), "b": birim, "o": o})
    try:
        _usd_yedek, gram, _kaynak = _benchmarks_cek()
    except Exception:
        gram = None
    if gram:
        gostergeler.append({"k": "GRAM", "ad": "Gram Altın", "f": round(gram, 2),
                            "d": None, "b": "TL", "o": 2})
    usd = veri.get("USDTRY")
    if usd and usd.get("son"):
        gostergeler.append({"k": "USDTRY", "ad": "Dolar", "f": round(usd["son"], 4),
                            "d": round(usd.get("deg", 0), 2), "b": "TL", "o": 4})
    # Dolar bazli endeks (XU030 / USDTRY) — TL ile birlikte gosterilir.
    x30 = veri.get("XU030") or {}
    if usd and usd.get("son") and x30.get("son"):
        try:
            gostergeler.append({
                "k": "XU030USD", "ad": "BIST 30 ($)", "b": "$", "o": 2,
                "f": round(x30["son"] / usd["son"], 2),
                "d": round(((1 + x30.get("deg", 0) / 100)
                             / (1 + usd.get("deg", 0) / 100) - 1) * 100, 2),
            })
        except (TypeError, ZeroDivisionError):
            pass
    if not gostergeler:
        logger.info("[Piyasa] serit yedegi icin veri bulunamadi.")
        return False
    govde = {
        "guncelleme": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M"),
        "etiket": "gun sonu / gecikmeli",
        "kaynak": "bot",
        "gostergeler": gostergeler,
    }
    with open("piyasa-serit.json", "w", encoding="utf-8") as f:
        json.dump(govde, f, ensure_ascii=False)
    logger.info("[Piyasa] piyasa-serit.json yazildi (%d gosterge)", len(gostergeler))
    return True


def _fiyat_gecmisi(gun=None):
    """data/prices/*.json -> (tarihler asc, [{HISSE: fiyat}, ...] ayni sirada)"""
    try:
        dosyalar = sorted(f for f in os.listdir("data/prices") if f.endswith(".json"))
    except OSError:
        dosyalar = []
    if gun:
        dosyalar = dosyalar[-gun:]
    tarihler, veriler = [], []
    for d in dosyalar:
        try:
            with open(os.path.join("data/prices", d), encoding="utf-8") as f:
                veriler.append(json.load(f))
            tarihler.append(d[:-5])
        except Exception:
            continue
    return tarihler, veriler


def _mini_sparkline(degerler, w=520, h=90, etiket="fiyat grafiği"):
    """Bagimsiz SVG cizgi grafigi; renk ilk/son degere gore otomatik.
    etiket: ekran okuyucular icin aria-label (kart basligi ayri yazilir)."""
    degerler = [float(v) for v in degerler if v is not None]
    if len(degerler) < 2:
        return ""
    lo, hi = min(degerler), max(degerler)
    aralik = (hi - lo) or 1.0
    adim = w / (len(degerler) - 1)
    noktalar = " ".join(
        f"{round(i * adim, 1)},{round(h - 6 - (v - lo) / aralik * (h - 14), 1)}"
        for i, v in enumerate(degerler)
    )
    renk = "#047857" if degerler[-1] >= degerler[0] else "#b91c1c"
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{etiket}" '
            f'style="max-width:{w}px"> '
            f'<polyline fill="none" stroke="{renk}" stroke-width="2.5" points="{noktalar}" /></svg>')


def _bist30_sepeti_sparkline(gun=30):
    """Son N günün eşit ağırlıklı BIST30 sepeti grafiği (rapor sayfalarının tepesi).
    Eksik verili tarihler atlanır; her hisse ilk GÖRÜLDÜĞÜ fiyata göre normalize edilir."""
    try:
        tarihler, veriler = _fiyat_gecmisi(gun)
        if len(tarihler) < 3:
            return ""
        son_hisseler = list(veriler[-1].keys())
        seri, baz = [], {}
        for t, v in zip(tarihler, veriler):
            toplam, adet = 0.0, 0
            for h in son_hisseler:
                f = v.get(h)
                if not f:
                    continue
                if h not in baz:
                    baz[h] = float(f)
                toplam += float(f) / baz[h]
                adet += 1
            if adet >= 15:  # eksik verili günleri (ör. hafta sonu artığı) atla
                seri.append(round(toplam / adet * 100, 2))
        if len(seri) < 3:
            return ""
        grafik = _mini_sparkline(seri, etiket=f"BIST 30 sepeti fiyat grafiği (son {len(seri)} gün)")
        deg = seri[-1] - seri[0]
        sinif = "pos" if deg >= 0 else "neg"
        return f"""
<div class="card" style="margin:0 0 18px; padding:14px 18px">
<div style="display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:6px">
<strong style="font-size:14px">BIST 30 sepeti — son {len(seri)} gün</strong>
<span class="{sinif}" style="font-weight:700">{deg:+.2f}%</span>
</div>
{grafik}
<div style="color:var(--muted); font-size:12px">30 hissenin eşit ağırlıklı sepeti, ilk görüldüğü fiyata göre normalize edildi.</div>
</div>"""
    except Exception:
        return ""


def _sirket_profilleri():
    """data/sirketler.json -> {KOD: profil}. Dosya yoksa bos sozluk; sayfa
    profilsiz de uretilir."""
    try:
        with open("data/sirketler.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _bilanco_ozeti(kod):
    """Hisse profil kartinin bilanço bolumu. Oncelik: data/bilanco (temel
    ajandin zengin ozeti); yoksa eski data/financials tek satiri; o da
    yoksa '' (kart bilançosuz cikar)."""
    try:
        dosyalar = sorted(f for f in os.listdir("data/bilanco") if f.endswith(".json"))
        if dosyalar:
            tarih = dosyalar[-1][:-5]
            tumu = json.load(open(os.path.join("data/bilanco", dosyalar[-1]), encoding="utf-8"))
            b = tumu.get(kod) or {}
            if b:
                def _mlr(v):
                    if v in (None, "", 0):
                        return "&mdash;"
                    v = float(v)
                    if abs(v) >= 1e9:
                        return f"{_ts1(v / 1e9)}".replace(",", "X").replace(".", ",").replace("X", ".") + " mlr TL"
                    return f"{_ts0(v / 1e6)}".replace(",", ".") + " mln TL"
                toplam_borc = (b.get("kisa_borc") or 0) + (b.get("uzun_borc") or 0)
                nk = b.get("net_kar") or 0
                donem = b.get("donem", "")
                return f"""
<div style="margin:10px 0 0; padding-top:8px; border-top:1px solid var(--line)">
<div style="color:var(--muted); font-size:12px; margin-bottom:2px">Bilanço &mdash; {donem} dönemi ({tarih} verisi)</div>
<table style="font-size:12.5px">
<tr><td>Toplam Varlıklar</td><td><strong>{_mlr(b.get('toplam_varlik'))}</strong></td><td>Toplam Borç</td><td><strong>{_mlr(toplam_borc or None)}</strong></td></tr>
<tr><td>Dönen Varlıklar</td><td>{_mlr(b.get('donen_varlik'))}</td><td>Öz Sermaye</td><td><strong>{_mlr(b.get('ozsermaye'))}</strong></td></tr>
<tr><td>Finansal Borç</td><td>{_mlr(b.get('finansal_borc'))}</td><td>Net Dönem Kârı</td><td class='{_renk(nk)}'><strong>{_mlr(nk or None)}</strong></td></tr>
</table>
</div>"""
    except Exception:
        pass
    try:
        dosyalar = sorted(f for f in os.listdir("data/financials") if f.endswith(".json"))
        if not dosyalar:
            return ""
        tarih = dosyalar[-1][:-5]
        satirlar = json.load(open(os.path.join("data/financials", dosyalar[-1]), encoding="utf-8"))
        satir = next((s for s in satirlar if s.startswith(kod + ":")), "")
        if not satir:
            return ""
        ogeler = []
        for parca in satir.split(":", 1)[1].split(";"):
            parca = parca.strip()
            if "=" not in parca:
                continue
            ad, deger = parca.split("=", 1)
            deger = deger.strip().replace("mlyr", "milyar").replace(".", ",")
            ogeler.append(f"{ad.strip()} <strong>{deger}</strong>")
        if not ogeler:
            return ""
        return (f'<div style="margin:10px 0 0; padding-top:8px; border-top:1px solid var(--line); '
                f'color:var(--muted); font-size:12.5px">Bilançodan ({tarih} verisi): {" &bull; ".join(ogeler)}</div>')
    except Exception:
        return ""


def _sirket_profili_html(kod):
    """Hisse detay sayfasina borsa ekrani tarzi sirket kunyesi karti.
    Bilgi data/sirketler.json'dan gelir (is Yatirim sirket kartlarindan
    derlendi, ay/yil bazli guncellenir); kayit yoksa kart cikmaz."""
    p = _sirket_profilleri().get(kod)
    if not p:
        return ""
    meta = []
    if p.get("kurulus"):
        meta.append(f'<span>Kuruluş: <strong>{p["kurulus"]}</strong></span>')
    if p.get("grup"):
        meta.append(f'<span>Ana ortak: <strong>{p["grup"]}</strong></span>')
    if p.get("sermaye"):
        donem = f' ({p["sermaye_donem"]})' if p.get("sermaye_donem") else ""
        meta.append(f'<span>Ödenmiş sermaye: <strong>{p["sermaye"]}</strong>{donem}</span>')
    web_html = ""
    if p.get("web"):
        web_html = (f'<div style="margin:8px 0 0; font-size:13.5px">🌐 '
                    f'<a href="https://{p["web"]}" target="_blank" rel="noopener">{p["web"]}</a></div>')
    markalar = p.get("markalar") or []
    marka_html = ""
    if markalar:
        rozetler = "".join(f'<span class="badge" style="margin:0 6px 6px 0; font-size:11.5px">{m}</span>' for m in markalar)
        marka_html = (f'<div style="margin:8px 0 0"><div style="color:var(--muted); font-size:12px; '
                      f'margin-bottom:5px">Markalar &amp; iştirakler</div>{rozetler}</div>')
    unvan = f'<div style="font-weight:700; margin-bottom:4px">{p.get("unvan", "")}</div>' if p.get("unvan") else ""
    return f"""
<div class="card" style="margin:0 0 18px">
<h3 style="margin:0 0 6px">🏢 Şirket Profili</h3>
{unvan}
<div class="meta" style="margin:0 0 8px">{''.join(meta)}</div>
<p style="margin:0; font-size:14px">{p.get('faaliyet', '')}</p>
{marka_html}{web_html}{_bilanco_ozeti(kod)}
</div>"""


TEMEL_VERI_YOL = "data/temel-veri.json"
# TradingView scanner alanlari (2026-09 canli test: hepsi dolu donuyor).
# TL bazinda; "ttm" = son 12 ay. Kaynak: scanner.tradingview.com (anahtarsiz).
_TV_ALANLAR = ["name", "close", "market_cap_basic", "ebitda_ttm",
               "total_revenue_ttm", "net_income_ttm", "price_earnings_ttm",
               "price_book_fq", "enterprise_value_ebitda_ttm",
               "enterprise_value_fq", "total_debt_fq", "cash_n_equivalents_fq",
               "gross_profit_ttm", "debt_to_equity_fq", "current_ratio_fq",
               "dividends_yield"]


def temel_veri_cek(iller=None, zaman_asimi=25):
    """30 hissenin FAVÖK/TTM/çarpan verisini tek istekte çekip diske yazar.

    Dönüş: {"guncelleme", "kaynak", "hisseler": {KOD: {...}}} | None
    Ağ hatasında None döner; çağıran taraf önbelleği kullanır.
    """
    import urllib.request
    kodlar = iller or HISSELER
    govde = {"symbols": {"tickers": ["BIST:" + k for k in kodlar],
                          "query": {"types": []}},
             "columns": _TV_ALANLAR}
    try:
        istek = urllib.request.Request(
            "https://scanner.tradingview.com/turkey/scan",
            data=json.dumps(govde).encode(),
            headers={"User-Agent": "Mozilla/5.0",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as r:
            ham = json.loads(r.read().decode())
    except Exception as e:
        logger.warning("[Temel Veri] cekilemedi: %s", str(e)[:120])
        return None
    hisseler = {}
    for satir in ham.get("data", []):
        d = satir.get("d") or []
        if not d or not d[0]:
            continue
        k = str(d[0])
        h = {}
        for ad, deg in zip(_TV_ALANLAR, d):
            if ad != "name":
                h[ad] = deg
        # hesaplanan alanlar
        try:
            if h.get("ebitda_ttm") and h.get("total_revenue_ttm"):
                h["ebitda_marj"] = h["ebitda_ttm"] / h["total_revenue_ttm"] * 100
            if h.get("total_debt_fq") is not None and h.get("cash_n_equivalents_fq") is not None:
                h["net_borc"] = h["total_debt_fq"] - h["cash_n_equivalents_fq"]
        except Exception:
            pass
        hisseler[k] = h
    if not hisseler:
        return None
    govde2 = {"guncelleme": datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M"),
              "kaynak": "TradingView (konsolide, TTM)",
              "hisseler": hisseler}
    try:
        with open(TEMEL_VERI_YOL, "w", encoding="utf-8") as f:
            json.dump(govde2, f, ensure_ascii=False, indent=1)
    except OSError:
        logger.warning("[Temel Veri] yazilamadi")
    logger.info("[Temel Veri] %d hisse icin FAVOK/carpan verisi alindi.", len(hisseler))
    return govde2


def temel_veri():
    """Son kaydedilen temel veri (yoksa {})."""
    try:
        with open(TEMEL_VERI_YOL, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _ondalik(metin):
    """'2.536.000.000 TL' -> 2536000000.0"""
    try:
        t = re.sub(r"[^0-9,\.]", "", str(metin))
        t = t.replace(".", "").replace(",", ".")
        return float(t) if t else None
    except Exception:
        return None


def _para_tl(v):
    """Buyuk TL tutarini okunur yazar: 1.292.000.000.000 -> 1.292,0 mlr TL."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "\u2014"
    if abs(v) >= 1e9:
        return ("%.1f" % (v / 1e9)).replace(".", ",") + " mlr TL"
    if abs(v) >= 1e6:
        return ("%.0f" % (v / 1e6)).replace(",", ".") + " mln TL"
    return "%.0f TL" % v


def _bilanco_kaydi(kod):
    """data/bilanco/<son tarih>.json icinden tek hisse kaydi."""
    try:
        d = sorted(f for f in os.listdir("data/bilanco") if f.endswith(".json"))[-1]
        with open(os.path.join("data/bilanco", d), encoding="utf-8") as f:
            return (json.load(f) or {}).get(kod) or {}
    except Exception:
        return {}


def _fund_kayitlari(kod):
    """data/hisse-analiz/<KOD>.json donem listesi (eskiden yeniye) + kaynak adi.

    Tek kaynak kurali: temel veriler bu dosyadan okunur. Ayni dosya bilanco
    tablosunu da besledigi icin sayfadaki rakamlar birbiriyle celismez.
    """
    try:
        with open(os.path.join("data", "hisse-analiz", kod + ".json"), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return [], ""
    def _sira(x):
        try:
            y, c = str(x.get("donem", "")).split("/")
            return int(y) * 10 + int(c)
        except Exception:
            return 0
    kayitlar = sorted([x for x in (d.get("bilanco") or []) if x.get("donem")], key=_sira)
    return kayitlar, str(d.get("kaynak") or "")


def _ttm(kayitlar, alan):
    """Son 12 ay degeri: son donem + onceki yil tamami - onceki yil ayni donem."""
    if not kayitlar:
        return None
    son = kayitlar[-1]
    try:
        y, c = str(son.get("donem")).split("/")
    except Exception:
        return None
    if c == "12":
        try:
            return float(son.get(alan))
        except (TypeError, ValueError):
            return None
    tam = [k for k in kayitlar if k.get("donem") == "%d/12" % (int(y) - 1)]
    ayni = [k for k in kayitlar if k.get("donem") == "%d/%s" % (int(y) - 1, c)]
    if not (tam and ayni):
        return None
    try:
        return float(son.get(alan)) + float(tam[0].get(alan)) - float(ayni[0].get(alan))
    except (TypeError, ValueError):
        return None


def _degerleme_verisi(kod, son_fiyat):
    """Piyasa degeri, F/K, PD/DD, ROE - TEK kaynaktan (Is Yatirim XI_29).

    Kar ve hasilat TTM (son 12 ay) tabanlidir; donem kari yilliklandirilmaz.
    Boylece sayfadaki elle yazilan degerlendirme metinleriyle ayni sonuc cikar.
    """
    if not son_fiyat:
        return None
    kayitlar, kaynak = _fund_kayitlari(kod)
    if not kayitlar:
        return None
    son = kayitlar[-1]
    prof = _sirket_profilleri().get(kod) or {}
    sermaye = son.get("odenmis_sermaye") or _ondalik(prof.get("sermaye"))
    ozkaynak = son.get("ozsermaye")
    if not (sermaye and ozkaynak):
        return None
    ozkaynak = float(ozkaynak)
    piyasa = float(son_fiyat) * float(sermaye)
    ttm_kar = _ttm(kayitlar, "net_kar")
    ttm_satis = _ttm(kayitlar, "satis")
    fborc = son.get("finansal_borc")
    if fborc is None and son.get("fin_borc_kisa") is not None:
        fborc = (son.get("fin_borc_kisa") or 0) + (son.get("fin_borc_uzun") or 0)
    d = {"donem": son.get("donem"), "kaynak": kaynak, "piyasa_degeri": piyasa,
         "ozkaynak": ozkaynak, "net_kar": son.get("net_kar"), "ttm_kar": ttm_kar,
         "ttm_satis": ttm_satis, "finansal_borc": fborc, "donem_sayisi": len(kayitlar),
         "fk": None, "pddd": None, "roe": None, "borc_ozkaynak": None}
    if ttm_kar and ttm_kar > 0:
        d["fk"] = piyasa / ttm_kar
    if ozkaynak > 0:
        d["pddd"] = piyasa / ozkaynak
        if ttm_kar:
            d["roe"] = ttm_kar / ozkaynak * 100
        if fborc:
            d["borc_ozkaynak"] = float(fborc) / ozkaynak * 100
    return d

def _degerleme_karti(kod, satir, seri=None):
    """Hesaplanmis degerleme/karlilik karti (tek kaynak, TTM tabanli)."""
    v = _degerleme_verisi(kod, satir.get("son"))
    if not v:
        return ('<div class="card"><h3 style="margin:0 0 8px">Değerleme ve Kârlılık</h3>'
                '<p style="margin:0;color:var(--muted);font-size:13px">Bilanço verisi bulunamadı.</p></div>')
    duz = lambda s: ("%.1f" % s).replace(".", ",") if s is not None else "\u2014"
    satirlar = [
        ("Piyasa değeri", _para_tl(v["piyasa_degeri"])),
        ("Özkaynak (%s)" % v["donem"], _para_tl(v["ozkaynak"])),
        ("Net kâr (son 12 ay)", _para_tl(v["ttm_kar"])),
        ("F/K (son 12 ay)", duz(v["fk"])),
        ("PD/DD", ("%.2f" % v["pddd"]).replace(".", ",") if v["pddd"] else "\u2014"),
        ("ROE (son 12 ay)", (("%.1f" % v["roe"]).replace(".", ",") + "%") if v["roe"] else "\u2014"),
        ("Finansal borç / özkaynak", (("%.0f" % v["borc_ozkaynak"]).replace(".", ",") + "%") if v["borc_ozkaynak"] else "\u2014"),
    ]
    getiri = ""
    if seri and len(seri) >= 6 and seri[-6][1]:
        g = (seri[-1][1] / seri[-6][1] - 1) * 100
        getiri = ('<p style="margin:0 0 8px;font-size:13px">Son 5 işlem günü getirisi: '
                  '<strong class="%s">%+.1f%%</strong></p>' % (_renk(g), g))
    govde = "".join("<tr><td>%s</td><td><strong>%s</strong></td></tr>" % (a, b) for a, b in satirlar)
    return ('<div class="card"><h3 style="margin:0 0 8px">Değerleme ve Kârlılık '
            '<span style="font-weight:400;color:var(--muted);font-size:12.5px">'
            '&middot; tek kaynak, son 12 ay</span></h3>' + getiri +
            '<table style="font-size:13.5px">' + govde + '</table>'
            '<p style="margin:8px 0 0;color:var(--muted);font-size:12px">Kaynak: İş Yatırım mali '
            'tabloları (KAP bildirimleri) — sayfadaki bilanço tablosuyla aynı kaynak. Kâr ve '
            'hasılat son 12 ay (TTM) tabanlıdır; özkaynak bilanço özdeşliğinden doğrulanır.</p>' +
            _tv_blogu(kod) + '</div>')

def _tv_blogu(kod):
    """FAVÖK alt bloğu. Yalnızca BAŞKA YERDE OLMAYAN kalemler gösterilir ki
    sayfada aynı metrik iki farklı değerle görünmesin (F/K, PD/DD, hasılat,
    net borç bilinçli olarak dışarıda bırakıldı)."""
    d = (temel_veri().get("hisseler") or {}).get(kod)
    if not d:
        return ""
    duz = lambda x, h=1: ("%.*f" % (h, x)).replace(".", ",")
    satirlar = []
    if d.get("ebitda_ttm") is not None:
        satirlar.append(("FAVÖK (son 12 ay)", _para_tl(d["ebitda_ttm"])))
    if d.get("ebitda_marj") is not None:
        satirlar.append(("FAVÖK marjı", duz(d["ebitda_marj"]) + "%"))
    if d.get("enterprise_value_ebitda_ttm") is not None:
        satirlar.append(("EV / FAVÖK", duz(d["enterprise_value_ebitda_ttm"])))
    if not satirlar:
        return ""
    govde = "".join("<tr><td>%s</td><td><strong>%s</strong></td></tr>" % (a, b) for a, b in satirlar)
    return ('<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">'
            '<div style="font-size:12.5px;color:var(--muted);margin-bottom:6px">FAVÖK · '
            'TradingView konsolide (TTM)</div><table style="font-size:13.5px">' + govde +
            '</table><p style="margin:6px 0 0;color:var(--muted);font-size:11.5px">FAVÖK marjı, '
            'faiz/amortisman/vergi öncesi kârlılıktır; net kâr marjı ile karıştırılmamalıdır.</p></div>')

def _makro_duyarlilik_karti(kod):
    """Sektorun makro rejim duyarliligi (makro_rejim cekirdeginden)."""
    try:
        import makro_rejim
        g = makro_rejim.gostergeleri_yukle()
        if not g:
            return ""
        rejim = makro_rejim.rejim_hesapla(
            g, piyasa=makro_rejim.piyasa_yukle())
        aktarim = makro_rejim.sektor_aktarimi(rejim)
        sektor = _hisse_sektoru(kod)
        d = aktarim.get(sektor)
        if not d:
            return ""
    except Exception:
        return ""
    renk = {"olumlu": "pos", "olumsuz": "neg", "nötr": ""}[d["egilim"]]
    return ('<div class="card"><h3 style="margin:0 0 8px">Makro Duyarlılık '
            '<span style="font-weight:400;color:var(--muted);font-size:12.5px">'
            '&middot; %s sektörü</span></h3>' % sektor +
            '<p style="margin:0 0 8px;font-size:13.5px">Rejim: <strong>%s</strong></p>' % rejim["etiket"] +
            '<table style="font-size:13.5px"><tr><td>Eğilim</td><td><strong class="%s">%s</strong></td></tr>' % (renk, d["egilim"]) +
            '<tr><td>Skor</td><td><strong>%+d</strong></td></tr>' % d["skor"] +
            '<tr><td>Kanallar</td><td>%s</td></tr></table>' % ", ".join(d["kanallar"]) +
            '<p style="margin:8px 0 0;color:var(--muted);font-size:12px">Sektör duyarlılık matrisi x '
            'güncel makro rejim; detay <a href="../makro-analiz.html">Makroekonomik Değerlendirme</a>.</p></div>')

def build_hisse_html(kod, satir, tarihler, veriler, haberler, sirket_haberleri=None):
    seri = [(t, v.get(kod)) for t, v in zip(tarihler, veriler) if v.get(kod)]
    grafik = (_mini_sparkline([f for _, f in seri], etiket=f"{kod} fiyat grafiği (son {len(seri)} gün)")
              if len(seri) >= 2 else "")
    grafik_karti = (
        f"""<div class="card" style="margin-bottom:16px">
<div style="display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:6px">
<strong style="font-size:14px">📊 {kod} fiyat grafiği</strong>
<span style="color:var(--muted); font-size:12px">son {len(seri)} gün &bull; kapanış fiyatları</span>
</div>
{grafik}
<div style="color:var(--muted); font-size:12px">{seri[0][0]} tarihinden bugüne {kod} kapanış fiyatları (TL). Karşılaştırma grafiği değildir.</div>
</div>"""
        if grafik else ""
    )
    degisim = None
    if len(seri) >= 2:
        degisim = (seri[-1][1] / seri[0][1] - 1) * 100
    if sirket_haberleri:
        haber_ogeleri = "".join(
            f"<li style='margin:6px 0'><a href='{html.escape(h['link'], quote=True)}' target='_blank' rel='noopener'>{html.escape(h['baslik'])}</a>"
            f" <span style='color:var(--muted); font-size:12px'>&mdash; {html.escape(h['kaynak'])}"
            + (f" &middot; {datetime.fromtimestamp(h['ts'], zoneinfo.ZoneInfo('Europe/Istanbul')).strftime('%d.%m')}" if h.get("ts") else "")
            + "</span></li>"
            for h in sirket_haberleri[:6])
    else:
        haber_ogeleri = "".join(
            f"<li style='margin:6px 0'>{h.split(']', 1)[-1].strip()}"
            f"{' <span style=&quot;color:var(--muted)&quot;>[' + h[1:].split(']')[0] + ']</span>' if h.startswith('[') and ']' in h else ''}</li>"
            for h in haberler[:12]
        ) or "<li style='color:var(--muted)'>Son 7 günde bu hisseye dair başlık bulunamadı.</li>"
    teknik_ogeler = "".join(
        f"<tr><td>{etiket}</td><td><strong>{deger}</strong></td></tr>"
        for etiket, deger in [
            ("Genel", satir.get("genel", "—")),
            ("Kısa vade (EMA 5-8-13-21)", satir.get("kisa", "—")),
            ("Orta vade (EMA 34-55)", satir.get("orta", "—")),
            ("Uzun vade (EMA 89-144)", satir.get("uzun", "—")),
            ("Wave Trend", satir.get("wt", "—")),
            ("Kanal konumu", f"%{_ts0(satir.get('konum', 0))}"),
            ("Trend gücü (r)", f"{_ts(satir.get('r', 0))}"),
            ("Puan", satir.get("puan", "—")),
        ]
    )
    degisim_html = (
        f"<span class='{_renk(degisim)}' style='font-weight:700'>{degisim:+.1f}% ({seri[0][0]}'ten beri)</span>"
        if degisim is not None else ""
    )
    icerik = f"""
<div class="hero">
<h1>{kod} <span style="font-size:16px; color:var(--muted)">{satir.get('sektor', '')}</span></h1>
<div class="meta"><span class="badge">{satir.get('genel', '—')}</span>
<span><strong>{_ts(satir.get('son', 0))} TL</strong></span>
<span class="{_renk(satir.get('gunluk', 0))}">{_ty(satir.get('gunluk', 0))}% (günlük)</span>
{degisim_html}</div>
</div>
{grafik_karti}
{_sirket_profili_html(kod)}
{hisse_analiz.bilanco_tablosu_html(kod)}
<div class="grid-iki">
{_degerleme_karti(kod, satir, seri)}
{_makro_duyarlilik_karti(kod)}
</div>
<div class="grid-iki">
<div class="card"><h3 style="margin:0 0 8px">Teknik Durum</h3>
<table style="font-size:13.5px">{teknik_ogeler}</table>
<p style="margin:8px 0 0; color:var(--muted); font-size:12px">EMA dizilimi + Wave Trend + 60 günlük regresyon kanalı. Detay: <a href="../teknik-analiz.html">Teknik Tarama</a></p></div>
<div class="card"><h3 style="margin:0 0 8px">Son 7 Gün Haberleri</h3>
<ul style="margin:0; padding-left:18px; font-size:13.5px">{haber_ogeleri}</ul>
<p style="margin:8px 0 0; font-size:12.5px"><a href="../sirket-haberleri.html#H-{kod}">Tüm şirket haberleri &rarr;</a></p></div>
</div>
{hisse_analiz.degerlendirmeler_html(kod)}
<div class="card" style="margin-top:14px">
<h3 style="margin:0 0 8px">{kod} konulu içerikler</h3>
<p style="margin:0"><a href="https://borsa-raporlari-web.pages.dev/ara?q={kod}" target="_blank" rel="noopener">🔍 Semantik aramada "{kod}" geçen tüm rapor bölümleri &rarr;</a></p>
<p style="margin:6px 0 0"><a href="../index.html">Günlük rapor arşivi &rarr;</a></p>
</div>"""
    return _sayfa(
        f"{kod} — Hisse Analizi", icerik, "hisseler", kok="../",
        yol=f"hisse/{kod}.html",
        aciklama=f"{kod} ({satir.get('sektor', 'BIST 30')}) son fiyat, teknik sinyaller, 7 günlük haberleri ve rapor arşivinde geçen değerlendirmeler.",
    )


# ---------- SIRKET HABERLERI (Google News RSS, sirket basina) ----------
_HABER_GURULTU = re.compile(
    r"(günlük teknik analiz|teknik ve osilatör|teknik analiz|destek ve direnç|"
    r"destek-direnç|sinyal listesi|sinyalleri|osilatör|news by matriks|tradingview)",
    re.I)

# Spor gundemi sirket haberlerine siziyor: or. "Tüpraş" aramasi "Beşiktaş
# Tüpraş Stadyumu" haberlerini getiriyor. Basligi spor baglaminda olanlar
# sirket haberi sayilmaz (stadyum isim haklari, mac, derbi vb.).
_SPOR_HABER_DESENI = re.compile(
    r"\b(st(?:ad|at)\w*|be[şs]ikta[şs]\w*|galatasaray\w*|fenerbah[çc]e\w*|trabzonspor\w*|"
    r"ma[çc]\w*|derbi\w*|gol\w*|fikst[üu]r\w*|futbol\w*|trib[üu]n\w*|"
    r"teknik direkt[öo]r\w*|s[üu]per (?:lig|kupas)\w*|d[üu]nya kupas\w*|"
    r"t[üu]rkiye kupas\w*|uefa\w*|uel\b|champions league|europa league|"
    r"penalt[ıi]\w*|hakem\w*|spor toto|play-?off\w*|basket\w*|euroleague|"
    r"eurocup|saha\w*|[şs]ampiyon\w*|forma\w*|antren[öo]r\w*|voleybol\w*|"
    r"g[üu]re[şs]\w*|olimpiyat\w*|turnuva\w*|kadro\w*|hangi kanalda|"
    r"canl[ıi] izle\w*|ma[çc] saat\w*|transfermarkt|\btff\b|\bfifa\b)",
    re.I)


def _spor_haberi_mi(baslik):
    """Baslik spor gundemine mi ait — sirket haberleri icin eleme testi."""
    return bool(_SPOR_HABER_DESENI.search(baslik or ""))



# Hisse kodlarinin gundelik kisaltmalari: basliklarda sirket adi yerine kisaltma
# kullanilabiliyor (or. 'THY ilk 5e girdi'). Tumu KUCUK harfle yazilir; kapi
# kelime siniri ile arar, boylece 'tav' -> 'tavsiye' gibi yanlis eslesme olmaz.
SIRKET_KISALTMALARI = {
    "THYAO": ["thy"],
    "KCHOL": ["koç"],
    "SAHOL": ["sabancı"],
    "ISCTR": ["iş bankası", "işbank"],
    "YKBNK": ["yapı kredi"],
    "AKBNK": ["akbank"],
    "GARAN": ["garanti bankası", "garanti bbva"],
    "VAKBN": ["vakıfbank"],
    "TUPRS": ["tüpraş"],
    "FROTO": ["ford otosan"],
    "TOASO": ["tofaş"],
    "TTKOM": ["türk telekom"],
    "TCELL": ["turkcell"],
    "MGROS": ["migros"],
    "PGSUS": ["pegasus"],
    "ASELS": ["aselsan"],
    "EREGL": ["ereğli demir", "erdeğli"],
    "PETKM": ["petkim"],
    "EKGYO": ["emlak konut"],
    "GUBRF": ["güfre fabrikaları"],
    "KRDMD": ["kardemir"],
    "SISE": ["şişecam"],
    "AEFES": ["anadolu efes"],
    "TRALT": ["türk altın"],
    "TAVHL": ["tav havaliman", "tav airport"],
}

def _sirket_adi_geciyor_mu(baslik, p, kod):
    """Baslikta sirketin adi geciyor mu? Ticker ile arama kisa kodlarin
    siradan kelimelerle cakismasina yol aciyor: or. SISE -> 'yagis ve sise
    dikkat' ya da ALTIN -> emtia haberleri. Bu yuzden baslikta sirket adi
    aranir; ticker yalnizca baslikta BUYUK harfle geciyorsa kabul edilir."""
    baslik = baslik or ""
    adaylar = []
    for anahtar in ("unvan", "sorgu"):
        deger = (p.get(anahtar) or "").strip()
        if deger:
            adaylar.append(deger)
    ilk = (p.get("sorgu") or p.get("unvan") or "").strip().split()
    if len(ilk) >= 2:
        adaylar.append(" ".join(ilk[:2]))
    adaylar += SIRKET_KISALTMALARI.get(kod or "", [])
    for aday in adaylar:
        aday = (aday or "").strip().lower()
        if aday and re.search(r"\b" + re.escape(aday) + r"\b", baslik.lower()):
            return True
    if kod and kod in baslik:  # KAP/bulten basliklari ticker'i buyuk harfle yazar
        return True
    return not adaylar  # profil yoksa eleme yapma

def _sirket_haberleri_cek(profiller):
    """Her BIST30 sirketi icin Google News RSS'ten son 7 gunun haberlerini
    ceker ve data/sirket-haberleri/<tarih>.json'a kaydeder. API anahtari
    gerekmez; bir sirketin akisi basarisizsa digerlerini engellemez."""
    try:
        import feedparser
        import urllib.parse
    except ImportError:
        logger.warning("[Sirket Haberi] feedparser kurulu degil; haber cekilmedi.")
        return []
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    kayitlar = []
    for kod, p in profiller.items():
        sorgu = p.get("sorgu") or p.get("unvan") or kod
        url = (f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(sorgu)}"
               f"+OR+{kod}+when:7d&hl=tr&gl=TR&ceid=TR:tr")
        gorulen = set()
        adet = 0
        try:
            f = feedparser.parse(url)
            for e in f.entries:
                baslik = (e.title or "").strip()
                if not baslik or _HABER_GURULTU.search(baslik):
                    continue
                if _spor_haberi_mi(baslik):
                    continue
                if not _sirket_adi_geciyor_mu(baslik, p, kod):
                    continue
                # 'Baslik - Kaynak' kalibindan kaynagi soy
                kaynak = baslik.rsplit(" - ", 1)[-1].strip()
                temiz = baslik.rsplit(" - ", 1)[0].strip()
                anahtar = re.sub(r"\W+", "", temiz.lower())[:60]
                if not temiz or anahtar in gorulen:
                    continue
                gorulen.add(anahtar)
                ts = int(time.mktime(e.published_parsed)) if hasattr(e, "published_parsed") else 0
                kayitlar.append({"kod": kod, "baslik": temiz, "kaynak": kaynak,
                                 "link": e.link, "ts": ts})
                adet += 1
                if adet >= 6:
                    break
        except Exception as e:
            logger.warning("[Sirket Haberi] %s akisi okunamadi: %s", kod, e)
        time.sleep(0.3)
    save_daily("sirket-haberleri", bugun, kayitlar)
    logger.info("[Sirket Haberi] %d sirket icin %d haber toplandi.",
                len({k['kod'] for k in kayitlar}), len(kayitlar))
    return kayitlar


def _sirket_haberleri_yukle():
    """Son sirket-haberleri kaydini {KOD: [haber, ...]} sozlugune cevirir
    (ts'ye gore yeni once, hisse basina en fazla 6)."""
    try:
        dosyalar = sorted(f for f in os.listdir("data/sirket-haberleri") if f.endswith(".json"))
        kayitlar = json.load(open(os.path.join("data/sirket-haberleri", dosyalar[-1]), encoding="utf-8"))
    except Exception:
        return {}
    harita = {}
    for k in sorted(kayitlar, key=lambda x: x.get("ts", 0), reverse=True):
        if _spor_haberi_mi(k.get("baslik", "")):
            continue
        harita.setdefault(k["kod"], []).append(k)
    return harita


def build_sirket_haberleri_html(haber_map, teknik_satirlar=None):
    """sirket-haberleri.html: 30 sirketin son 7 gun haberleri, sirket basina
    gruplanmis tek sayfa."""
    if teknik_satirlar:
        sira = [s["hisse"] for s in teknik_satirlar]
    else:
        sira = sorted(haber_map.keys())
    bolumler = []
    haberli = 0
    for kod in sira:
        ogeler = haber_map.get(kod, [])
        unvan = _sirket_profilleri().get(kod, {}).get("unvan", "")
        if ogeler:
            haberli += 1
            satirlar = "".join(
                f"<li style='margin:5px 0'><a href='{html.escape(h['link'], quote=True)}' target='_blank' rel='noopener'>{html.escape(h['baslik'])}</a>"
                f" <span style='color:var(--muted); font-size:12px'>— {html.escape(h['kaynak'])}</span></li>"
                for h in ogeler[:5])
            govde = f"<ul style='margin:4px 0 0; padding-left:18px; font-size:13.5px'>{satirlar}</ul>"
        else:
            govde = "<p style='color:var(--muted); font-size:13px; margin:4px 0 0'>Son 7 günde öne çıkan başlık yok.</p>"
        bolumler.append(
            f"<div class='card' id='H-{kod}' style='margin-bottom:12px; padding:12px 18px'>"
            f"<div style='display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:6px'>"
            f"<div><a href='hisse/{kod}.html' style='font-weight:700; font-size:15px'>{kod}</a>"
            f"<span style='color:var(--muted); font-size:12.5px'> · {unvan}</span></div>"
            f"<a href='hisse/{kod}.html' style='font-size:12.5px'>Hisse sayfası &rarr;</a>"
            f"</div>{govde}</div>")
    icerik = f"""
<div class="hero">
<h1>Şirket Haberleri</h1>
<p>BIST 30 şirketlerinin son 7 gündeki şirket özelinde başlıkları; {haberli} şirkette haber bulundu. Kaynaklara tıklayarak orijinal habere gidebilirsiniz.</p>
</div>
{''.join(bolumler)}"""
    return _sayfa("Şirket Haberleri", icerik, "sirkethaber",
                  aciklama="BIST 30 şirketlerinin son 7 gündeki şirket haberleri ve KAP gelişmeleri.",
                  yol="sirket-haberleri.html")


def hisse_sayfalari_yaz(teknik_satirlar):
    """BIST30 hisseleri icin hisse/<KOD>.html + hisse/index.html uretir."""
    if not teknik_satirlar:
        return
    os.makedirs("hisse", exist_ok=True)
    # FAVÖK/çarpan verisi (tek istek, 30 hisse). Hata olursa önceki dosya kullanılır.
    try:
        temel_veri_cek()
    except Exception:
        logger.warning("[Temel Veri] guncellenemedi; onceki veri kullanilacak.")
    tarihler, veriler = _fiyat_gecmisi()
    haber_toplu = []
    try:
        for d in sorted(os.listdir("data/news"))[-7:]:
            with open(os.path.join("data/news", d), encoding="utf-8") as f:
                haber_toplu += json.load(f)
    except Exception:
        pass
    sirket_haber_map = _sirket_haberleri_yukle()
    for s in teknik_satirlar:
        kod = s["hisse"]
        haberler = [h for h in haber_toplu if kod.lower() in h.lower()
                    and not _spor_haberi_mi(h)]
        with open(os.path.join("hisse", f"{kod}.html"), "w", encoding="utf-8") as f:
            f.write(build_hisse_html(kod, s, tarihler, veriler, haberler,
                                     sirket_haberleri=sirket_haber_map.get(kod, [])))
    kartlar = "".join(
        f'<a class="rcard" data-kod="{s["hisse"]}" href="{s["hisse"]}.html"><span class="date">{s["hisse"]}</span>'
        f'<span class="sub">{s.get("genel", "")} &bull; {_ts(s.get("son", 0))} TL</span>'
        f'<span class="sub {_renk(s.get("gunluk", 0))}">{_ty(s.get("gunluk", 0))}%</span></a>'
        for s in teknik_satirlar
    )
    icerik = f"""
<div class="hero">
<h1>BIST 30 Hisseleri</h1>
<p>Her hisse için güncel fiyat, teknik sinyal durumu, fiyat grafiği ve son 7 günün haberleri.</p>
</div>
<div id="isi-haritasi"></div>
<script src="isi-haritasi.js?v=8" defer></script>
<h2 class="section-title">Hisse Kartları</h2>
<div class="grid">{kartlar}</div>"""
    # DİKKAT: bu sayfa hisse/ alt klasorunde — kok="../" olmazsa CSS ve
    # nav linkleri kirilir (stilsiz 'bozuk' sayfa).
    with open(os.path.join("hisse", "index.html"), "w", encoding="utf-8") as f:
        f.write(_sayfa("BIST 30 Hisseleri", icerik, "hisseler", kok="../", yol="hisse/"))
    logger.info("[Hisseler] %d hisse sayfasi uretildi.", len(teknik_satirlar))


def sinyal_karnesi_yaz(teknik_satirlar):
    """data/teknik gecmisindeki AL sinyallerini 5 islem gunu sonraki fiyatlara
    karsi test eder (basit backtest) ve sinyal-karnesi.html uretir."""
    try:
        t_dosyalar = sorted(f for f in os.listdir("data/teknik") if f.endswith(".json"))
    except OSError:
        t_dosyalar = []
    tarihler, veriler = _fiyat_gecmisi()
    kayitlar = []
    for td in t_dosyalar[:-1]:  # son günün 5 gün sonrası henüz yok
        try:
            with open(os.path.join("data/teknik", td), encoding="utf-8") as f:
                gunun_satirlari = json.load(f)
        except Exception:
            continue
        d = td[:-5]
        if d not in tarihler:
            continue
        d_idx = tarihler.index(d)
        sonraki = tarihler[d_idx + 1:]
        if len(sonraki) < 5:
            continue
        cikis_t = sonraki[4]
        cikis_veri = veriler[tarihler.index(cikis_t)]
        giris_veri = veriler[d_idx]
        for s in gunun_satirlari:
            if s.get("genel") != "AL":
                continue
            kod = s.get("hisse")
            giris = giris_veri.get(kod) or s.get("son")
            cikis = cikis_veri.get(kod)
            if not giris or not cikis:
                continue
            kayitlar.append({
                "tarih": d, "hisse": kod, "giris": float(giris), "cikis": float(cikis),
                "cikis_t": cikis_t, "getiri": (float(cikis) / float(giris) - 1) * 100,
            })
    if not kayitlar:
        logger.info("[Karne] backtest icin yeterli gecmis yok; sayfa yazilmadi.")
        return
    ort = sum(k["getiri"] for k in kayitlar) / len(kayitlar)
    isabet = sum(1 for k in kayitlar if k["getiri"] > 0) / len(kayitlar) * 100
    # Masraf senaryosu: giris+cikis komisyon ve BSMV toplam ~%0,10 (varsayim).
    # Not: BIST hisselerinde kazanc stopaji su an yok; masraf buyuk olcude
    # araci kurum komisyonudur. Gercek oran araci kuruma gore degisir.
    ISLEM_MALIYETI = 0.10
    net_ort = ort - ISLEM_MALIYETI
    net_isabet = sum(1 for k in kayitlar if k["getiri"] - ISLEM_MALIYETI > 0) / len(kayitlar) * 100
    en_iyi = sorted(kayitlar, key=lambda k: k["getiri"], reverse=True)[:3]
    en_kotu = sorted(kayitlar, key=lambda k: k["getiri"])[:3]

    def _mini_liste(liste):
        def _tr_tarih(iso):
            parcalar = iso.split("-")
            return ".".join(reversed(parcalar)) if len(parcalar) == 3 else iso

        return "".join(
            f"<div style='margin:8px 0'>"
            f"<div style='display:flex; justify-content:space-between; align-items:baseline; gap:8px'>"
            f"<strong>{k['hisse']}</strong>"
            f"<span class='{_renk(k['getiri'])}' style='font-weight:700; white-space:nowrap'>{k['getiri']:+.2f}%</span></div>"
            f"<div style='color:var(--muted); font-size:12px'>Sinyal: {_tr_tarih(k['tarih'])}</div></div>"
            for k in liste
        )

    tablo = "".join(
        f"<tr><td>{k['tarih']}</td><td><strong>{k['hisse']}</strong></td>"
        f"<td>{_ts(k['giris'])} TL</td><td>{_ts(k['cikis'])} TL</td><td>{k['cikis_t']}</td>"
        f"<td class='{_renk(k['getiri'])}' style='font-weight:700'>{k['getiri']:+.2f}%</td></tr>"
        for k in sorted(kayitlar, key=lambda k: k["tarih"], reverse=True)[:15]
    )
    icerik = f"""
<div class="hero">
<h1>Sinyal Karnesi</h1>
<p>Teknik taramanın verdiği <strong>AL</strong> sinyallerini 5 işlem günü sonra fiyata karşı test ediyoruz.
Sinyal günü kapanışı alıp 5. işlem günü kapanışında satmış olsaydık sonuç: {len(kayitlar)} sinyal,
ortalama <strong class="{_renk(ort)}">{_ty(ort)}%</strong> (brüt), isabet oranı <strong>{_ts0(isabet)}%</strong>.
Komisyon+BSMV masrafı (~%0,10) sonrası: ortalama <strong class="{_renk(net_ort)}">{_ty(net_ort)}%</strong>,
isabet <strong>{_ts0(net_isabet)}%</strong> (varsayımsal masraf senaryosu; gerçek oran aracı kuruma göre değişir).
(Geçmiş performans gelecek getirinin garantisi değildir; yöntem basit tutulmuştur.)</p>
</div>
<div class="grid">
<div class="card"><h3 style="margin:0 0 8px">🏆 En iyi 3</h3>{_mini_liste(en_iyi)}</div>
<div class="card"><h3 style="margin:0 0 8px">📉 En kötü 3</h3>{_mini_liste(en_kotu)}</div>
</div>
<h2 class="section-title">Son 15 Sinyal</h2>
<div class="card" style="padding:8px 24px 16px"><div class="tbl-wrap"><table>
<tr><th>Sinyal Günü</th><th>Hisse</th><th>Giriş</th><th>Çıkış</th><th>Çıkış Günü</th><th>Getiri</th></tr>
{tablo}
</table></div></div>
<p style="color:var(--muted); font-size:12.5px">Yöntem: genel sinyali "AL" olan her hisse, sinyal günü kapanışından
5 işlem günü sonraki kapanışa karşı ölçülür. Sinyaller yatırım tavsiyesi değildir.</p>"""
    with open("sinyal-karnesi.html", "w", encoding="utf-8") as f:
        f.write(_sayfa("Sinyal Karnesi — AL Sinyalleri Backtest", icerik, "karne",
                       yol="sinyal-karnesi.html"))
    logger.info("[Karne] %d AL sinyali test edildi; sayfa uretildi.", len(kayitlar))


def haberler_yaz(gun=14):
    """data/news gecmisinden haber arsivi sayfasi uretir."""
    try:
        dosyalar = sorted(os.listdir("data/news"))[-gun:][::-1]
    except OSError:
        dosyalar = []
    bolumler = []
    toplam = 0
    for d in dosyalar:
        try:
            with open(os.path.join("data/news", d), encoding="utf-8") as f:
                liste = json.load(f)
        except Exception:
            continue
        if not liste:
            continue
        # Gündem/magazin gurultusu render asamasinda da ele (eski veriler de temiz)
        liste = [h for h in liste
                 if not any(k.lower() in h.lower() for k in FINANS_DISI_KELIMELER)]
        if not liste:
            continue
        toplam += len(liste)
        ogeler = "".join(f"<li style='margin:5px 0'>{html.escape(h)}</li>" for h in liste)
        bolumler.append(
            f"<h2 class='section-title'>{_tr_tarih(d[:-5])}</h2>"
            f"<div class='card'><ul style='margin:0; padding-left:20px; font-size:13.5px'>{ogeler}</ul></div>"
        )
    govde = "".join(bolumler) or '<p style="color:var(--muted)">Haber arşivi henüz oluşmadı.</p>'
    icerik = f"""
<div class="hero">
<h1>Haber Arşivi</h1>
<p>RSS akışlarından toplanan günlük BIST ve makro haber başlıkları — son {len(dosyalar)} gün, toplam {toplam} başlık.</p>
</div>
{govde}"""
    with open("haberler.html", "w", encoding="utf-8") as f:
        f.write(_sayfa("Haber Arşivi", icerik, "haberler", yol="haberler.html"))
    logger.info("[Haberler] %d gundelik arsiv yazildi.", len(dosyalar))


def sozluk_yaz():
    """data/sozluk.json iceriginden aramali sozluk sayfasi uretir."""
    try:
        with open("data/sozluk.json", encoding="utf-8") as f:
            terimler = json.load(f)
    except Exception:
        terimler = []
    ogeler = "".join(
        f"<div class='card sozluk-oge' data-terim='{t['terim'].lower()}' style='margin:10px 0'>"
        f"<strong style='color:var(--accent)'>{t['terim']}</strong>"
        f"<p style='margin:6px 0 0; font-size:14px'>{t['aciklama']}</p></div>"
        for t in sorted(terimler, key=lambda x: x["terim"].casefold())
    )
    icerik = f"""
<div class="hero">
<h1>Borsa Sözlüğü</h1>
<p>Borsa Okulu derslerinde geçen temel kavramlar — arayarak süzgeçleyebilirsin.
Ekonomi ve finansın tam sözlüğü için <a href="terimler.html">Terimler ve Tanımlar</a> sayfasına bak.</p>
</div>
<div class="arama-form" style="margin:14px 0">
<input type="text" id="sozluk-ara" placeholder="Terim ara: RSI, temettü, kaldıraç..." onkeyup="sozlukSuz()" style="flex:1; padding:10px 14px; border:1px solid var(--line); border-radius:10px; background:var(--card); color:var(--ink)">
</div>
<div id="sozluk-liste">{ogeler}</div>
<script>
function sozlukSuz() {{
  var q = (document.getElementById('sozluk-ara').value || '').toLowerCase();
  document.querySelectorAll('.sozluk-oge').forEach(function(el) {{
    el.style.display = el.getAttribute('data-terim').indexOf(q) !== -1 ? '' : 'none';
  }});
}}
</script>"""
    with open("sozluk.html", "w", encoding="utf-8") as f:
        f.write(_sayfa("Borsa Sözlüğü", icerik, "sozluk", yol="sozluk.html"))
    logger.info("[Sozluk] %d terim yazildi.", len(terimler))


# ---------- TERIMLER VE TANIMLAR (finetune sozlugunden uretilen tam sozluk) ----------


def _terim_yukle():
    """data/terimler/*.jsonl dosyalarini birlestirip yayina hazir hale getirir.

    Temizlikler: ders kitabi kalintisi '[Bölüm: ...]' etiketlerinin atilmasi,
    satir sonlarinin/bosluklarin duzlestirilmesi, ayni terimin (buyuk/kucuk
    harf farkinca) tek kayitta birlestirilmesi (kaynaklar birlestirilir).
    Donus: terim sozlukleri listesi, Turkce alfabetik sirali."""
    kayitlar = []
    for ad in ("tr_terim_temiz.jsonl", "tr_terim_finans_ek.jsonl"):
        yol = os.path.join("data", "terimler", ad)
        try:
            with open(yol, encoding="utf-8") as f:
                for satir in f:
                    satir = satir.strip()
                    if not satir:
                        continue
                    try:
                        kayitlar.append(json.loads(satir))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    birlesik = {}
    for k in kayitlar:
        terim = re.sub(r"\s+", " ", (k.get("terim_tr") or "").strip())
        tanim = re.sub(r"\s*\[B[oö]l[uü]m:[^\]]*\]", "", k.get("tanim") or "")
        tanim = re.sub(r"\s+", " ", tanim).strip()
        if not terim or len(tanim) < 20:
            continue
        if tanim[:1].islower():
            tanim = tanim[:1].upper() + tanim[1:]
        anahtar = terim.casefold()
        if anahtar in birlesik:
            eski = birlesik[anahtar]
            if k.get("kaynak") and k["kaynak"] not in eski["kaynak"]:
                eski["kaynak"] += " + " + k["kaynak"]
            if len(tanim) > len(eski["tanim"]):
                eski["tanim"] = tanim
            if not eski["en"] and k.get("terim_en"):
                eski["en"] = k["terim_en"].strip()
            continue
        birlesik[anahtar] = {
            "terim": terim,
            "en": (k.get("terim_en") or "").strip(),
            "tanim": tanim,
            "kaynak": (k.get("kaynak") or "").strip(),
        }
    sonuc = sorted(birlesik.values(), key=lambda x: x["terim"].casefold())

    # IFRS sozlugunden cikarilan zenginlestirme (EN tanim + ornek cumle);
    # terim_zenginlestir.py ciktisi. Dosya yoksa sessizce atlanir.
    zengin_yol = os.path.join("data", "terimler", "zengin.jsonl")
    try:
        with open(zengin_yol, encoding="utf-8") as f:
            zenginler = {json.loads(s)["terim"].casefold(): json.loads(s)
                         for s in f if s.strip()}
        for v in sonuc:
            z = zenginler.get(v["terim"].casefold())
            if z:
                v["en_tanim"] = z.get("en_tanim", "")
                v["ornek"] = z.get("ornek", "")
                v["ornek_en"] = z.get("ornek_en", "")
    except OSError:
        pass
    return sonuc


def _ifrs_yukle():
    """data/terimler/muhasebe-terimleri.csv (autoclaw IFRS sozluguki; muhasebenews.com
    kaynagi) okur. Donus: {'en', 'tr', 'en_tanim', 'ornek', 'ornek_en', 'bolum'}
    listesi, EN terime gore alfabetik. Dosya yoksa bos liste doner.

    Ayrica konumsal hizali EN/DE/ZH/RU dosyalari (muhasebe-en.csv, muhasebe-de.csv,
    muhasebe-zh.csv, muhasebe-ru.jsonl; orijinal CSV satir sirasiyla) varsa her
    kayda 'en2' (taze EN tanim), 'de', 'ru', 'zh' tanimlarini ekler."""
    yol = os.path.join("data", "terimler", "muhasebe-terimleri.csv")

    # Konumsal dil dosyalari: orijinal CSV satir sirasiyla hizalidir.
    dil_verisi = {"en2": [], "de": [], "ru": [], "zh": []}
    try:
        import csv as _csv
        with open(os.path.join("data", "terimler", "muhasebe-en.csv"),
                  encoding="utf-8-sig", newline="") as f:
            dil_verisi["en2"] = [re.sub(r"\s+", " ", (r.get("definition") or "").strip())
                                 for r in _csv.DictReader(f)]
    except OSError:
        pass
    try:
        import csv as _csv
        with open(os.path.join("data", "terimler", "muhasebe-de.csv"),
                  encoding="utf-8-sig", newline="") as f:
            dil_verisi["de"] = [re.sub(r"\s+", " ", (r.get("Almanca_Tanim") or "").strip())
                                for r in _csv.DictReader(f)]
    except OSError:
        pass
    try:
        import csv as _csv
        with open(os.path.join("data", "terimler", "muhasebe-zh.csv"),
                  encoding="utf-8-sig", newline="") as f:
            dil_verisi["zh"] = [re.sub(r"\s+", " ", (r.get("定义") or "").strip())
                                for r in _csv.DictReader(f)]
    except OSError:
        pass
    try:
        with open(os.path.join("data", "terimler", "muhasebe-ru.jsonl"),
                  encoding="utf-8") as f:
            ru = [None] * 2000
            for satir in f:
                satir = satir.strip()
                if not satir:
                    continue
                try:
                    r = json.loads(satir)
                    ru[r["i"]] = re.sub(r"\s+", " ", (r.get("def") or "").strip())
                except (json.JSONDecodeError, KeyError):
                    continue
            dil_verisi["ru"] = ru
    except OSError:
        pass

    kayitlar, gorulen = [], set()
    try:
        import csv as _csv
        with open(yol, encoding="utf-8-sig", newline="") as f:
            for satir_no, satir in enumerate(_csv.DictReader(f)):
                en = re.sub(r"\s+", " ", (satir.get("Ingilizce_Terim") or "").strip())
                tr = re.sub(r"\s+", " ", (satir.get("Turkce_Anlami") or "").strip())
                if not en or not tr:
                    continue
                anahtar = en.casefold()
                if anahtar in gorulen:
                    continue
                gorulen.add(anahtar)
                kayit = {
                    "en": en, "tr": tr,
                    "en_tanim": re.sub(r"\s+", " ", (satir.get("Ingilizce_Tanim") or "").strip()),
                    "ornek": re.sub(r"\s+", " ", (satir.get("Ornek_Turkce") or "").strip()),
                    "ornek_en": re.sub(r"\s+", " ", (satir.get("Ornek_Ingilizce") or "").strip()),
                    "bolum": (satir.get("Bolum") or "").strip()[:1].upper(),
                }
                for dil, liste in dil_verisi.items():
                    if satir_no < len(liste) and liste[satir_no]:
                        kayit[dil] = liste[satir_no]
                kayitlar.append(kayit)
    except OSError:
        return []
    return sorted(kayitlar, key=lambda x: x["en"].casefold())


def terimler_yaz():
    """Terimler ve Tanımlar (genel ekonomi) + Muhasebe Terimleri (IFRS): her set
    kendi tek sayfasinda, sozluk.html gibi aramali kart listesi. Bireysel terim
    sayfasi uretilmez; icerik statik oldugundan yalnizca degisen dosyalar yeniden
    yazilir (gunluk commit gurultusu olusmasin)."""
    # Kopyalama caydirici: terim kartlarinda metin secimi kapali; yine de
    # kopyalanan uzun metne otomatik kaynak atfi eklenir. Not: tekniktir ve
    # kararl kullanici/scrapertan korunmaz; amacli aceleci kopyalama engeli.
    koruma = """
<style>.hero, .terim-oge {-webkit-user-select:none; -moz-user-select:none; user-select:none;}</style>
<script>
document.addEventListener('contextmenu', function(e) {
  if (e.target.closest && e.target.closest('.terim-oge')) e.preventDefault();
});
document.addEventListener('copy', function(e) {
  var s = (window.getSelection ? String(window.getSelection()) : '') || '';
  if (s.length > 60 && e.clipboardData) {
    e.clipboardData.setData('text/plain', s +
      '\\n\\nKaynak: BIST 30 Gunluk Raporlar - Terimler ve Tanimlar\\n' + location.href);
    e.preventDefault();
  }
});
</script>"""

    def _yaz(yol, icerik):
        try:
            with open(yol, encoding="utf-8") as f:
                if f.read() == icerik:
                    return False
        except OSError:
            pass
        with open(yol, "w", encoding="utf-8") as f:
            f.write(icerik)
        return True

    def _arama_js(liste_id, input_id):
        """Arama suzgeci: kartlari data-ara'ya gore gizler, bos kalan harf
        basliklarini da saklar."""
        return f"""
<script>
function terimSuz() {{
  var q = (document.getElementById('{input_id}').value || '').toLowerCase();
  document.querySelectorAll('#{liste_id} .terim-oge').forEach(function(el) {{
    el.style.display = el.getAttribute('data-ara').indexOf(q) !== -1 ? '' : 'none';
  }});
  document.querySelectorAll('#{liste_id} h2').forEach(function(h) {{
    var gorunur = 0, kardes = h.nextElementSibling;
    while (kardes && kardes.tagName !== 'H2') {{
      if (kardes.classList && kardes.classList.contains('terim-oge') && kardes.style.display !== 'none') gorunur++;
      kardes = kardes.nextElementSibling;
    }}
    h.style.display = gorunur ? '' : 'none';
  }});
}}
</script>"""

    def _kart(govde, anahtar):
        anahtar = anahtar.casefold().replace("'", "").replace('"', "")
        return f"<div class='card terim-oge' data-ara='{anahtar}' style='margin:10px 0; padding:12px 16px'>{govde}</div>"

    # --- 1) Terimler ve Tanımlar (genel ekonomi sozlugu) ---
    veri = _terim_yukle()
    if veri:
        def _harf_sira(h):
            # Turkce alfabe sirasi (casefold ASCII sort 'ç'yi 'c'den sonra atar)
            sira = "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"
            return (sira.index(h) if h in sira else 99, h)

        harf_gruplari = {}
        for v in veri:
            harf_gruplari.setdefault(v["terim"][:1].upper(), []).append(v)
        kartlar = ""
        for harf in sorted(harf_gruplari, key=_harf_sira):
            kartlar += f"<h2 class='section-title'>{harf}</h2>"
            for v in harf_gruplari[harf]:
                govde = (f"<strong style='color:var(--accent)'>{v['terim']}</strong>"
                         + (f" <span style='color:var(--muted); font-size:13px'>{v['en']}</span>" if v["en"] else "")
                         + f"<p style='margin:6px 0 0; font-size:14px'>{v['tanim']}</p>")
                if v.get("ornek"):
                    govde += (f"<p style='margin:6px 0 0; font-size:13.5px; font-style:italic'>Örnek: {v['ornek']}"
                              + (f" <span style='color:var(--muted)'>({v['ornek_en']})</span>" if v.get("ornek_en") else "")
                              + "</p>")
                if v.get("en_tanim"):
                    govde += (f"<p style='margin:6px 0 0; font-size:13px; color:var(--muted)'>"
                              f"İngilizce tanım: {v['en_tanim']}</p>")
                kartlar += _kart(govde, v["terim"] + " " + v["en"] + " " + v["tanim"])
        icerik = f"""
<div class="hero">
<h1>Terimler ve Tanımlar</h1>
<p>Ekonomi, finans ve borsa terminolojisinin {len(veri)} terimlik referans sözlüğü; tanımlar TCMB,
Rekabet Kurumu, Kalkınma Ajansları ve iktisat sözlükleri gibi resmî kaynaklardan derlenmiştir.
Temel kavramlar için <a href="sozluk.html">Borsa Sözlüğü</a>'ne, muhasebe terminolojisi için
<a href="muhasebe-terimleri.html">Muhasebe Terimleri</a> sayfasına bakabilirsin.</p>
</div>
<div class="arama-form" style="margin:14px 0">
<input type="text" id="terim-ara" placeholder="Terim ara: enflasyon, temettü, zorunlu karşılık..." onkeyup="terimSuz()"
 style="width:100%; padding:10px 14px; border:1px solid var(--line); border-radius:10px; background:var(--card); color:var(--ink)">
</div>
<div id="terim-liste">{kartlar}</div>
<p style="color:var(--muted); font-size:12.5px">Tanımlar ilgili kurumların kamuya açık sözlüklerinden alıntılanmıştır;
yatırım tavsiyesi değildir.</p>
{_arama_js('terim-liste', 'terim-ara')}
{koruma}"""
        _yaz("terimler.html", _sayfa(
            "Terimler ve Tanımlar", icerik, "terimler",
            aciklama=f"{len(veri)} terimlik ekonomi, finans ve borsa terimleri sözlüğü: "
                     "TCMB, Rekabet Kurumu ve yatırım sözlüklerinden kaynaklı tanımlar.",
            yol="terimler.html"))
        logger.info("[Terimler] %d terim tek sayfada yazildi.", len(veri))

    # --- 2) Muhasebe Terimleri (IFRS sozluguki; 5 dilli tanim kutulari) ---
    ifrs = _ifrs_yukle()
    if ifrs:
        # Kaynak verideki bilinen yazim hatalari yalnizca GORUNTULEMEDE duzeltilir.
        _yazim_duzelt = [("A mounts", "Amounts"), ("orbusinesses", "or businesses"),
                         (" ıssues", " issues"), ("ıncome", "income")]

        def _duzelt(metin):
            for eski, yeni_ in _yazim_duzelt:
                metin = metin.replace(eski, yeni_)
            return metin

        bolum_gruplari = {}
        for v in ifrs:
            bolum_gruplari.setdefault(v["bolum"] or "#", []).append(v)
        kartlar = ""
        for bolum in sorted(bolum_gruplari):
            kartlar += f"<h2 class='section-title'>{bolum}</h2>"
            for v in bolum_gruplari[bolum]:
                en_gor = _duzelt(v["en"])
                tanim_en = v.get("en2") or v["en_tanim"]
                govde = (f"<strong style='color:var(--accent)'>{en_gor}</strong>"
                         f" <span style='color:var(--muted); font-size:13px'>{v['tr']}</span>"
                         f"<p style='margin:6px 0 0; font-size:14px'>{tanim_en}</p>")
                if v["ornek"]:
                    govde += (f"<p style='margin:6px 0 0; font-size:13.5px; font-style:italic'>Örnek: {v['ornek']}"
                              + (f" <span style='color:var(--muted)'>({v['ornek_en']})</span>" if v.get("ornek_en") else "")
                              + "</p>")
                # Diger dillerdeki tanimlar (kaynak: denetlenmis autoclaw setleri)
                for dil_kod, dil_etiket in (("de", "DE"), ("ru", "RU"), ("zh", "中文")):
                    if v.get(dil_kod):
                        govde += (f"<p style='margin:6px 0 0; font-size:13px; color:var(--muted)'>"
                                  f"{dil_etiket}: {v[dil_kod]}</p>")
                arama_anahtari = " ".join(v.get(d) or "" for d in ("en", "tr", "en_tanim", "de", "ru", "zh"))
                kartlar += _kart(govde, _duzelt(arama_anahtari))
        icerik = f"""
<div class="hero">
<h1>Muhasebe Terimleri</h1>
<p>{len(ifrs)} terimlik muhasebe ve finansal raporlama sözlüğü; tanımlar İngilizce,
terim karşılıkları ve örnek cümleler Türkçedir; Almanca, Rusça ve Çince tanımlar da
her kartta yer alır. Genel ekonomi terimleri için
<a href="terimler.html">Terimler ve Tanımlar</a> sayfasına bakabilirsin.</p>
</div>
<div class="arama-form" style="margin:14px 0">
<input type="text" id="muhasebe-ara" placeholder="Terim ara: amortisman, şerefiye, goodwill..." onkeyup="terimSuz()"
 style="width:100%; padding:10px 14px; border:1px solid var(--line); border-radius:10px; background:var(--card); color:var(--ink)">
</div>
<div id="muhasebe-liste">{kartlar}</div>
<p style="color:var(--muted); font-size:12.5px">Tanımlar kamuya açık IFRS sözlüğünden alıntılanmıştır;
yatırım tavsiyesi değildir.</p>
{_arama_js('muhasebe-liste', 'muhasebe-ara')}
{koruma}"""
        _yaz("muhasebe-terimleri.html", _sayfa(
            "Muhasebe Terimleri", icerik, "muhasebe",
            aciklama=f"{len(ifrs)} terimlik IFRS muhasebe terimleri sözlüğü: İngilizce tanım, "
                     "Türkçe karşılık ve örnek cümlelerle.",
            yol="muhasebe-terimleri.html"))
        logger.info("[Terimler] %d IFRS terimi tek sayfada yazildi.", len(ifrs))

        # --- 2b) Muhasebe Terimleri: DIL SAYFALARI (ceviri degil, veri tabanli) ---
        # /en /de /ru /zh surumleri kendi dil verisinden uretilir (muhasebe-en.csv,
        # muhasebe-de.csv, muhasebe-zh.csv, muhasebe-ru.jsonl). Makine cevirisi bu
        # sayfalar icin kapali (i18n.py GENERASYON_HARIC); sitemap dil alternatifleri
        # i18n tarafinda eklenir.
        try:
            import muhasebe_diller
            muhasebe_diller.yaz()
            logger.info("[Terimler] Muhasebe dil sayfalari (en/de/ru/zh) guncellendi.")
        except Exception:
            logger.exception("[Terimler] Muhasebe dil sayfalari uretilemedi.")


VARSAYILAN_ENFLASYON = 0.32  # TUIK verisi hic alinamazsa kullanilan yedek


def enflasyon_cek():
    """Son yayinlanan TUIK TUFE yillik degisimini TradingView ekonomik takvim
    ucundan ceker (TR 'Inflation Rate YoY' olayinin 'actual' alani TUIK
    verisidir). data/enflasyon.json'a cache'lenir; erisilemezse onceki cache,
    o da yoksa varsayilan oran kullanilir. Donus: (oran, aciklama) | (None, '')."""
    import urllib.request
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    frm = (datetime.now(tz) - timedelta(days=50)).strftime("%Y-%m-%d")
    url = (f"https://economic-calendar.tradingview.com/events"
           f"?from={frm}T00%3A00%3A00.000Z&to={bugun}T00%3A00%3A00.000Z&countries=TR")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Origin": "https://www.tradingview.com"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.load(r)
        olaylar = d.get("result")
        if isinstance(olaylar, dict):
            olaylar = olaylar.get("events", [])
        adaylar = [e for e in olaylar
                   if "inflation rate yoy" in str(e.get("title", "")).lower()
                   and e.get("actual") not in (None, "", 0)]
        if adaylar:
            son = max(adaylar, key=lambda e: str(e.get("date", "")))
            oran = float(son["actual"]) / 100.0
            kayit = {"oran": round(oran, 4), "donem": str(son.get("date", ""))[:10],
                     "kaynak": "TUIK (TradingView uzerinden)", "guncelleme": bugun}
            with open("data/enflasyon.json", "w", encoding="utf-8") as f:
                json.dump(kayit, f, ensure_ascii=False, indent=1)
            logger.info("[Enflasyon] TUIK yillik TUFE: %%%.2f (%s bilanco donemi)",
                        oran * 100, kayit["donem"])
            return oran, kayit["kaynak"]
    except Exception as e:
        logger.warning("[Enflasyon] TUIK verisi cekilemedi: %s", e)
    return None, ""


def _enflasyon_yukle():
    """Portfoy grafigindeki enflasyon cizgisi icin (oran, etiket). Once cache
    dosyasindaki TUIK verisi, o yoksa varsayilan oran kullanilir."""
    try:
        d = json.load(open("data/enflasyon.json", encoding="utf-8"))
        oran = float(d.get("oran"))
        donem = d.get("donem", "")
        return oran, f"Enflasyon (TÜİK yıllık %{oran * 100:.1f}, {donem})"
    except Exception:
        return VARSAYILAN_ENFLASYON, f"Enflasyon (varsayım %{VARSAYILAN_ENFLASYON * 100:.0f})"


def ekonomik_takvim_cek(gun=14):
    """TradingView'in acik ekonomik takvim ucundan TR/US/EU veri duyurularini
    ceker (onemsizler filtrelenir) ve data/ekonomik-takvim/ altina kaydeder.
    Kaynak erisilemezse bos liste doner; onceki gunun verisi sayfalarda kullanilir."""
    import urllib.request
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    to = (datetime.now(tz) + timedelta(days=gun)).strftime("%Y-%m-%d")
    url = (f"https://economic-calendar.tradingview.com/events"
           f"?from={bugun}T00%3A00%3A00.000Z&to={to}T00%3A00%3A00.000Z&countries=TR,US,EU")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Origin": "https://www.tradingview.com"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.load(r)
    olaylar = d.get("result")
    if isinstance(olaylar, dict):
        olaylar = olaylar.get("events", [])
    secili = []
    for e in olaylar:
        try:
            onem = int(e.get("importance") or 0)
        except (TypeError, ValueError):
            onem = 0
        if onem < 0:
            continue  # otomatik/kucuk veriler: ajandayi sisirmesin
        secili.append({
            "tarih": str(e.get("date", ""))[:10],
            "saat": str(e.get("date", ""))[11:16],
            "ulke": e.get("country", ""),
            "olay": e.get("title", ""),
            "onem": onem,
            "tahmin": e.get("forecast"),
            "gercek": e.get("actual"),
            "onceki": e.get("previous"),
        })
    save_daily("ekonomik-takvim", bugun, secili)
    logger.info("[Takvim] %d olay cekildi (%d gun).", len(secili), gun)
    return secili


# TradingView ekonomik takvimi Ingilizce gelir; yaygin olay adlari icin
# anahtar-kelime sozluguyle yanina Turkçe gloss uretilir. LLM cagrisi yapmaz,
# maliyeti/kotasi yoktur; sozlukte olmayan nadir olaylar Ingilizce kalir.
TAKVIM_TR = [
    ("MBA 30-Year Mortgage Rate", "MBA 30 Yıllık Konut Kredisi Faizi"),
    ("Net Long-term TIC Flows", "Uzun Vadeli Sermaye Girişleri (TIC)"),
    ("Interest Rate Decision", "Faiz Kararı"),
    ("FOMC Economic Projections", "FOMC Ekonomik Projeksiyonları"),
    ("Press Conference", "Basın Toplantısı"),
    ("Initial Jobless Claims", "İlk İşsizlik Başvuruları"),
    ("Continuing Jobless Claims", "Devam Eden İşsizlik Başvuruları"),
    ("Nonfarm Payrolls", "Tarım Dışı İstihdam"),
    ("Average Hourly Earnings", "Ortalama Saatlik Kazançlar"),
    ("Crude Oil Stocks Change", "Ham Petrol Stok Değişimi"),
    ("Crude Oil Stock Change", "Ham Petrol Stok Değişimi"),
    ("Gasoline Stocks Change", "Benzin Stok Değişimi"),
    ("Distillate Stocks Change", "Distilat Stok Değişimi"),
    ("New Car Registrations", "Yeni Otomobil Kayıtları"),
    ("Pending Home Sales", "Bekleyen Konut Satışları"),
    ("Existing Home Sales", "Mevcut Konut Satışları"),
    ("Building Permits", "İnşaat İzinleri"),
    ("Housing Starts", "Konut Başlangıçları"),
    ("Housing Market Index", "Konut Piyasası Endeksi"),
    ("Manufacturing Index", "İmalat Endeksi"),
    ("Manufacturing PMI", "İmalat PMI"),
    ("Services PMI", "Hizmet PMI"),
    ("Composite PMI", "Karma PMI"),
    ("Retail Sales Control Group", "Perakende Satışlar (kontrol grubu)"),
    ("Retail Sales Ex Autos", "Perakende Satışlar (otomobil hariç)"),
    ("Retail Sales", "Perakende Satışlar"),
    ("Import Prices", "İthalat Fiyatları"),
    ("Export Prices", "İhracat Fiyatları"),
    ("Industrial Production", "Sanayi Üretimi"),
    ("Capacity Utilization", "Kapasite Kullanımı"),
    ("Business Inventories", "İşletme Stokları"),
    ("Durable Goods Orders", "Dayanıklı Mal Siparişleri"),
    ("Factory Orders", "Fabrika Siparişleri"),
    ("Wholesale Inventories", "Toptan Satış Stokları"),
    ("Job Openings", "Açık İş Pozisyonları"),
    ("ADP Employment Change", "ADP İstihdam Değişimi"),
    ("Unemployment Rate", "İşsizlik Oranı"),
    ("Consumer Confidence", "Tüketici Güven Endeksi"),
    ("Consumer Sentiment", "Tüketici Duyarlılığı"),
    ("Trade Balance", "Dış Ticaret Dengesi"),
    ("Current Account", "Cari İşlemler Dengesi"),
    ("Core CPI", "Çekirdek TÜFE"),
    ("Core PPI", "Çekirdek ÜFE"),
    ("CPI", "TÜFE"),
    ("PPI", "ÜFE"),
    ("GDP", "GSYH"),
    ("Policy Rate", "Politika Faizi"),
    ("Economic Bulletin", "Ekonomik Bülten"),
    ("President", "Başkanı"),
    ("Speech", "Konuşması"),
    ("Core", "çekirdek"),
    ("Flash", "ön tahmin"),
    ("Prel", "ilk tahmin"),
    ("Final", "son veri"),
    ("MoM", "aylık"),
    ("YoY", "yıllık"),
    ("QoQ", "çeyreklik"),
    ("New Home Sales", "Yeni Konut Satışları"),
    ("Business Confidence", "İşletme Güveni"),
    ("Economic Sentiment", "Ekonomik Duyarlılık"),
    ("Chicago Fed National Activity Index", "Chicago Fed Ulusal Aktivite Endeksi"),
]


def _takvim_tr(olay):
    """Ingilizce olay adini sozlukle Turkcelestirir; sozlukte karsiligi
    yoksa '' dondurur (o zaman Ingilizce ad yanina gloss eklenmez)."""
    if not olay:
        return ""
    tr = olay
    for en, turkce in TAKVIM_TR:
        if en.lower() in tr.lower():
            tr = re.sub(re.escape(en), turkce, tr, flags=re.IGNORECASE)
    return tr if tr.strip().casefold() != olay.strip().casefold() else ""


def _ekonomik_takvim_yukle():
    """Son data/ekonomik-takvim kaydini dondurur (yoksa [])."""
    try:
        dosyalar = sorted(f for f in os.listdir("data/ekonomik-takvim") if f.endswith(".json"))
        return json.load(open(os.path.join("data/ekonomik-takvim", dosyalar[-1]), encoding="utf-8"))
    except Exception:
        return []


def _ajanda_html(olaylar, limit=None, kok=""):
    """Garanti bultenindeki 'Gunluk Ajanda' tablosunun muadili:
    yakin donem ekonomik veri duyurulari (tarih/saat/ulke/olay/tahmin/onceki)."""
    if not olaylar:
        return ""
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    yarin = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")

    def _gun_etiketi(t):
        if t == bugun:
            return "Bugün"
        if t == yarin:
            return "Yarın"
        return t[8:10] + "." + t[5:7]

    def _deger(v):
        if v in (None, "", 0):
            return "&mdash;"
        try:
            return f"{_ts(float(v))}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return str(v)

    secili = olaylar[:limit] if limit else olaylar
    satirlar = []
    son_gun = None
    for e in secili:
        if e.get("tarih") != son_gun:
            satirlar.append(f"<tr><td colspan='5' style='background:var(--accent-bg); font-weight:700; font-size:12px'>{_gun_etiketi(e['tarih'])} ({e['tarih']})</td></tr>")
            son_gun = e["tarih"]
        onem = " 🔴" if e.get("onem", 0) >= 1 else ""
        tr = _takvim_tr(e.get("olay", ""))
        tr_html = f" <span style='color:var(--muted); font-size:11.5px'>&mdash; {tr}</span>" if tr else ""
        satirlar.append(
            f"<tr><td>{e.get('saat', '')}</td><td><strong>{e.get('ulke', '')}</strong>{onem}</td>"
            f"<td>{e.get('olay', '')}{tr_html}</td>"
            f"<td style='text-align:right'>{_deger(e.get('tahmin'))}</td>"
            f"<td style='text-align:right'>{_deger(e.get('onceki'))}</td></tr>")
    return f"""
<div class="pano">
<div class="pano-baslik">📅 Günlük Ajanda — Ekonomik Veri Duyuruları</div>
<div class="tbl-wrap">
<table class="pano-tablo">
<thead><tr><th>Saat</th><th>Ülke</th><th>Olay</th><th style="text-align:right">Tahmin</th><th style="text-align:right">Önceki</th></tr></thead>
<tbody>
{''.join(satirlar)}
</tbody>
</table>
</div>
<p class="pano-not">🔴 = yüksek etkili veri. Kaynak: TradingView ekonomik takvimi; saatler TR zamanıdır. <a href="{kok}takvim.html">Ekonomik Takvim Rehberi &rarr;</a></p>
</div>"""


def takvim_yaz():
    """Ekonomik takvim sayfasi: ustte TradingView akisindan gelen gercek
    yaklasan olaylar, altta kalici rehber (data/takvim.json)."""
    try:
        with open("data/takvim.json", encoding="utf-8") as f:
            ogeler = json.load(f)
    except Exception:
        ogeler = []
    yaklasan = _ajanda_html(_ekonomik_takvim_yukle())
    kartlar = "".join(
        f"<div class='card' style='margin:10px 0'><strong style='color:var(--accent)'>{o['etkinlik']}</strong>"
        f"<div style='color:var(--muted); font-size:13px; margin-top:4px'>{o['periyot']}"
        f"{' &bull; ' + o['not'] if o.get('not') else ''}</div></div>"
        for o in ogeler
    ) or '<p style="color:var(--muted)">Takvim verisi henüz girilmedi.</p>'
    icerik = f"""
<div class="hero">
<h1>Ekonomik Takvim</h1>
<p>Önümüzdeki günlerin önemli ekonomik veri duyuruları ve borsa için kritik düzenli açıklamaların rehberi.</p>
</div>
{yaklasan or '<p style="color:var(--muted)">Yaklaşan olay listesi henüz yüklenmedi.</p>'}
<h2 class="section-title">Düzenli Veri Rehberi</h2>
{kartlar}"""
    with open("takvim.html", "w", encoding="utf-8") as f:
        f.write(_sayfa("Ekonomik Takvim", icerik, "takvim", yol="takvim.html"))
    logger.info("[Takvim] sayfa yazildi (%d rehber ogesi).", len(ogeler))


_RSS_GUNLER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_RSS_AYLAR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def podcast_rss_yaz():
    """radyo/indeks.json -> radyo/podcast.xml (iTunes uyumlu basit RSS 2.0)."""
    try:
        with open("radyo/indeks.json", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return
    ogeler = []
    for b in d.get("bolumler", []):
        try:
            g = datetime.strptime(b.get("tarih", ""), "%Y-%m-%d")
            pub = f"{_RSS_GUNLER[g.weekday()]}, {g.day:02d} {_RSS_AYLAR[g.month - 1]} {g.year} 08:00:00 +0300"
        except Exception:
            pub = "Wed, 01 Jan 2026 08:00:00 +0300"
        url = SITE_URL + b.get("dosya", "")
        ogeler.append(
            f"<item><title>{b.get('baslik', '')}</title><guid isPermaLink=\"false\">{b.get('id', '')}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<enclosure url=\"{url}\" type=\"audio/mpeg\" length=\"0\" />"
            f"<link>{SITE_URL}index.html</link>"
            f"<description>BIST Radyo — kurgusal yapay zeka sunucularla borsa bülteni.</description>"
            f"<itunes:duration>{max(1, round(b.get('sure_sn', 60) / 60))}</itunes:duration></item>"
        )
    # xml-stylesheet PI: tarayicida dogrudan acilinca ham XML agaci yerine
    # podcast.xsl ile bicimlendirilmis sayfa gorunur; podcast uygulamalari PI'yi yok sayar.
    # itunes:image + image: uygulama icinde kanal kapagi gorunsun.
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?xml-stylesheet type="text/xsl" href="podcast.xsl"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>\n'
        f"<title>BIST Radyo — Yapay Zeka Borsa Bülteni</title>\n<link>{SITE_URL}</link>\n"
        "<language>tr</language>\n"
        f"<itunes:image href=\"{SITE_URL}og-cover.png\" />\n"
        f"<image><url>{SITE_URL}og-cover.png</url><title>BIST Radyo — Yapay Zeka Borsa Bülteni</title><link>{SITE_URL}</link></image>\n"
        "<description>Borsa İstanbul'da gün başlangıcı, öğle ve kapanış değerlendirmeleri; "
        "kurgusal yapay zeka sunucular. Yatırım tavsiyesi değildir.</description>\n"
        + "\n".join(ogeler)
        + "\n</channel></rss>"
    )
    with open("radyo/podcast.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    logger.info("[Podcast] %d bolumluk RSS yazildi.", len(ogeler))


def _rss_pubdate(tarih_str):
    """'2026-09-15' -> 'Tue, 15 Sep 2026 08:00:00 +0300' (bozuk tarihte fallback)."""
    try:
        g = datetime.strptime(tarih_str, "%Y-%m-%d")
        return f"{_RSS_GUNLER[g.weekday()]}, {g.day:02d} {_RSS_AYLAR[g.month - 1]} {g.year} 08:00:00 +0300"
    except Exception:
        return "Wed, 01 Jan 2026 08:00:00 +0300"


def rapor_podcast_yaz():
    """reports/*.mp3 -> radyo/podcast-raporlar.xml (Radyo'dan AYRI kategori).
    Gunluk rapor sesli bultenleri; '-derin-analiz.mp3' dosyalari da varsa
    kendi adlarıyla ayni aka girer. Dosya adi kaynaktir: 2026-09-15.mp3 gibi
    tarihten baslik/pubDate uretilir; MP3 yoksa aka oge dusmez."""
    try:
        dosyalar = sorted(f for f in os.listdir("reports") if f.endswith(".mp3"))
    except OSError:
        return
    ogeler = []
    for ad in reversed(dosyalar):  # en yeni once
        tarih = ad[:10]
        tur = "Derin Analiz Sesli Bülteni" if "-derin-analiz" in ad else "Günlük Rapor Sesli Bülteni"
        yol = os.path.join("reports", ad)
        try:
            boyut = os.path.getsize(yol)
        except OSError:
            boyut = 0
        rapor_link = SITE_URL + ("reports/" + ad[:-4] + "-derin-analiz.html" if "-derin-analiz" in ad
                                 else "reports/" + tarih + ".html")
        ogeler.append(
            f"<item><title>{tarih} — {tur}</title><guid isPermaLink=\"false\">rapor-{ad[:-4]}</guid>"
            f"<pubDate>{_rss_pubdate(tarih)}</pubDate>"
            f"<enclosure url=\"{SITE_URL}reports/{ad}\" type=\"audio/mpeg\" length=\"{boyut}\" />"
            f"<link>{rapor_link}</link>"
            f"<description>{tarih} tarihli {tur.lower()} — yapay zeka sesiyle rapor okumasi. Yatirim tavsiyesi degildir.</description>"
            f"</item>"
        )
    # xml-stylesheet PI: podcast.xsl iki akista da calisir (geniktir, kanal
    # basligi/ogeleri stil icinde sabit degil, XML'den okunur).
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?xml-stylesheet type="text/xsl" href="podcast.xsl"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>\n'
        f"<title>BIST Günlük Rapor — Sesli Bülten</title>\n<link>{SITE_URL}</link>\n"
        "<language>tr</language>\n"
        f"<itunes:image href=\"{SITE_URL}og-cover.png\" />\n"
        f"<image><url>{SITE_URL}og-cover.png</url><title>BIST Günlük Rapor — Sesli Bülten</title><link>{SITE_URL}</link></image>\n"
        "<description>Her işlem günü otomatik üretilen BIST 30 günlük raporunun ve derin "
        "analizin yapay zeka sesiyle okumasi. Yatırım tavsiyesi değildir.</description>\n"
        + "\n".join(ogeler)
        + "\n</channel></rss>"
    )
    with open("radyo/podcast-raporlar.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    logger.info("[Podcast] %d bolumluk rapor RSS'i yazildi.", len(ogeler))


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
    sayfalar += [
        {"b": "Hisseler (BIST 30 detay sayfaları)", "u": "hisse/index.html",
         "t": "hisse detay sayfa fiyat teknik sinyal haber grafik sektör " + hisse_listesi},
        {"b": "Şirket Haberleri", "u": "sirket-haberleri.html", "t": "şirket haberleri KAP gelişme açıklama basın son dakika BIST30 şirket özel "},
        {"b": "BIST Radyo (Podcast)", "u": "radyo/index.html",
         "t": "radyo podcast yayın dinle sesli bülten açılış öğle kapanış Ela Mert yapay zeka sunucu RSS abone mp3"},
        {"b": "Sinyal Karnesi (AL sinyalleri backtest)", "u": "sinyal-karnesi.html",
         "t": "sinyal karnesi AL sinyali backtest isabet oranı getiri performans geçmiş teknik tarama sonuç"},
        {"b": "Haber Arşivi", "u": "haberler.html",
         "t": "haber arşivi borsa makro ekonomi başlıklar rss gündem"},
        {"b": "Borsa Sözlüğü", "u": "sozluk.html",
         "t": "sözlük terimler RSI MACD EMA temettü kaldıraç volatilite destek direnç borsa okulu kavramlar"},
        {"b": "Terimler ve Tanımlar", "u": "terimler.html",
         "t": "ekonomi finans terim sözlük tanım makro mikro muhasebe davranışsal finans " +
              " ".join(v["terim"] for v in _terim_yukle())[:18000]},
        {"b": "Muhasebe Terimleri", "u": "muhasebe-terimleri.html",
         "t": "muhasebe IFRS terim sözlük finansal raporlama bilanço amortisman şerefiye goodwill " +
              " ".join(v["en"] + " " + v["tr"] for v in _ifrs_yukle())[:18000]},
        {"b": "Ekonomik Takvim Rehberi", "u": "takvim.html",
         "t": "ekonomik takvim faiz enflasyon FOMC bilanço TCMB TÜİK veri açıklama rehber"},
    ]
    # Muhasebe terimleri DIL SAYFALARI: her biri kendi dil verisiyle indekslenir.
    _ifrs_dil = _ifrs_yukle()
    if _ifrs_dil:
        sayfalar += [
            {"b": "Accounting Terms Glossary (English)", "u": "en/muhasebe-terimleri.html",
             "t": "accounting terms glossary IFRS accounting terminology definition example " +
                  " ".join((v["en"] + " " + (v.get("en2") or v["en_tanim"])) for v in _ifrs_dil)[:18000]},
            {"b": "Buchhaltungslexikon (Deutsch)", "u": "de/muhasebe-terimleri.html",
             "t": "Buchhaltungslexikon Rechnungswesen IFRS Abschreibung Bilanz Definition Beispiel " +
                  " ".join((v["en"] + " " + (v.get("de") or "")) for v in _ifrs_dil)[:18000]},
            {"b": "Словарь бухгалтерских терминов", "u": "ru/muhasebe-terimleri.html",
             "t": "бухгалтерские термины словарь МСФО бухгалтерский учёт определение пример " +
                  " ".join((v.get("ru") or "") for v in _ifrs_dil)[:18000]},
            {"b": "会计术语词典", "u": "zh/muhasebe-terimleri.html",
             "t": "会计术语 词典 IFRS 国际财务报告准则 定义 例句 " +
                  " ".join((v.get("zh") or "") for v in _ifrs_dil)[:18000]},
        ]
    haftasonu_metin = makale_metni("haftasonu.html")
    if haftasonu_metin:
        sayfalar.append({"b": "Hafta Sonu Gündemi (haftalık bülten)", "u": "haftasonu.html",
                         "t": "hafta sonu gündem ekonomi finans emlak ticaret döviz jeopolitik yeni hafta ajandası "
                              + haftasonu_metin[:3200]})
    derin_metin = makale_metni("derin-analiz.html")
    if derin_metin:
        sayfalar.append({"b": "Derin Analiz (günlük derinlemesine inceleme)", "u": "derin-analiz.html",
                         "t": derin_metin[:3500]})
    makro_metin = makale_metni("makro-analiz.html")
    if makro_metin:
        sayfalar.append({"b": "Makroekonomik Değerlendirme (enflasyon, faiz, büyüme)", "u": "makro-analiz.html",
                         "t": makro_metin[:3500]})
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
        ("makro-analiz.html", "weekly"),
        ("teknik-analiz.html", "hourly"),
        ("borsapy-analiz.html", "hourly"),
        ("portfolio.html", "daily"),
        ("haftasonu.html", "weekly"),
        ("sinyal-karnesi.html", "daily"),
        ("haberler.html", "hourly"),
        ("sozluk.html", "weekly"),
        ("terimler.html", "weekly"),
        ("muhasebe-terimleri.html", "weekly"),
        ("takvim.html", "weekly"),
        ("hisse/", "daily"),
        ("sirket-haberleri.html", "daily"),
        ("radyo/index.html", "weekly"),
        ("gizlilik.html", "monthly"),
    ]
    # ozel oncelikler: belirtilmeyenler 0.8 kalir
    oncelik = {"index.html": "1.0", "gizlilik.html": "0.3", "radyo/index.html": "0.7"}
    bugun = datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    url_blokleri = []
    for yol, frekans in statik:
        url_blokleri.append(
            f"  <url><loc>{SITE_URL}{_guzel_url(yol)}</loc><lastmod>{bugun}</lastmod>"
            f"<changefreq>{frekans}</changefreq>"
            f"<priority>{oncelik.get(yol, '0.8')}</priority></url>"
        )
    for fn in rapor_dosyalari:
        try:
            lm = datetime.fromtimestamp(os.path.getmtime(os.path.join("reports", fn))).strftime("%Y-%m-%d")
        except OSError:
            lm = bugun
        url_blokleri.append(
            f"  <url><loc>{SITE_URL}reports/{_guzel_url(fn)}</loc><lastmod>{lm}</lastmod>"
            f"<changefreq>monthly</changefreq><priority>0.6</priority></url>"
        )
    # Hisse detay sayfalari
    try:
        for fn in sorted(os.listdir("hisse")):
            if fn.endswith(".html") and fn != "index.html":
                url_blokleri.append(
                    f"  <url><loc>{SITE_URL}hisse/{_guzel_url(fn)}</loc><lastmod>{bugun}</lastmod>"
                    f"<changefreq>daily</changefreq><priority>0.6</priority></url>"
                )
    except OSError:
        pass
    # Tarihli arsiv sayfalari: hafta sonu gundemi + borsa okulu dersleri.
    # lastmod dosya adindaki tarihten okunur (2026-09-20.html -> 2026-09-20);
    # adinda tarih olmayan dosyalarda bot kosma tarihi kullanilir.
    for klasor in ("haftasonu", "haftasonu-egitimi"):
        try:
            for fn in sorted(os.listdir(klasor)):
                if not fn.endswith(".html"):
                    continue
                stem = fn[:-5]
                lm = stem if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem) else bugun
                url_blokleri.append(
                    f"  <url><loc>{SITE_URL}{klasor}/{_guzel_url(fn)}</loc><lastmod>{lm}</lastmod>"
                    f"<changefreq>monthly</changefreq><priority>0.6</priority></url>"
                )
        except OSError:
            pass
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
    d.text((120, 528), "borsa-raporlari.pages.dev", font=_font(False, 25), fill="#5eead4")
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
    # Uretim baslamadan once onceki aksam snapshot'ini zorunlu dogrula.
    guncel_snapshot = makro_snapshot_cek(expected_report_date=date_str)
    logger.info("Makro snapshot: %s (%d kayit)", guncel_snapshot["snapshot_id"],
                len(guncel_snapshot["records"]))
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

    raporlar = sorted((fn for fn in os.listdir("reports") if fn.endswith(".html")), reverse=True)

    # Ekonomik takvim: TradingView acik ucundan; rapora 'Gunluk Ajanda'
    # bolumu olarak girer ve takvim.html'de yayinlanir.
    ajanda = []
    try:
        ajanda = ekonomik_takvim_cek()
    except Exception:
        logger.exception("[Takvim] cekilemedi; eski veri varsa o kullanilir.")
    if not ajanda:
        ajanda = _ekonomik_takvim_yukle()

    # TUIK verisi akşam snapshot tarafından yazılır; rapor canlı veri çekmez.

    with open(f"reports/{date_str}.html", "w", encoding="utf-8") as f:
        f.write(build_html(report, date_str, teknik_satirlar=_SON_TEKNIK, ajanda=ajanda))

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
            # Guncel sayfa (kokte) + tarihsiz ARŞİV kopyası (reports/ altında;
            # sitemap'e girer ve ana sayfa arşivinde listelenir).
            sayfa = rapor_sayfasi(
                markdown_to_html(derin), date_str,
                baslik="Derin Analiz",
                alt_baslik="BIST 30 &bull; Yapay zeka destekli derinlemesine analiz",
                kok_yol="derin-analiz.html",
                aciklama="BIST 30'un günlük derinlemesine analizi: sektör değerlendirmesi, EMA ve Wave Trend teknik okuma, osilatör-momentum yorumları ve risk senaryoları.",
            )
            arsiv_yolu = f"reports/{date_str}-derin-analiz.html"
            arsiv_sayfasi = rapor_sayfasi(
                markdown_to_html(derin), date_str,
                baslik="Derin Analiz",
                alt_baslik="BIST 30 &bull; Yapay zeka destekli derinlemesine analiz",
                kok_yol=arsiv_yolu,
                aciklama=f"{date_str} tarihli BIST 30 derin analiz raporu.",
            )
            with open("derin-analiz.html", "w", encoding="utf-8") as f:
                f.write(sayfa)
            with open(arsiv_yolu, "w", encoding="utf-8") as f:
                f.write(arsiv_sayfasi)
            print(f"[Derin Analiz] sayfa uretildi (+ arsiv: {arsiv_yolu}).", flush=True)
    except Exception:
        logger.exception("[Derin Analiz] sayfa uretilemedi; diger sayfalar etkilenmez.")

    p = load_portfolio()
    # `raporlar` 7062'de, bugunku rapor(ler) reports/ altina YAZILMADAN once
    # listelenmisti: hem gunluk rapor (7076) hem derin-analiz arsiv kopyasi
    # (7127) sonra olusturuluyor. Tazelenmezse bugunun karti ana sayfa
    # arsivine, sitemap'e, site-arama.json'a ve indexnow ping'ine girmiyordu;
    # arsiv yalnizca siradaki Teknik Tarama run'una (piyasa saati) kadar
    # bayat kaliyordu.
    raporlar = sorted(
        (fn for fn in os.listdir("reports") if fn.endswith(".html")),
        reverse=True)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_index_html(p, raporlar, teknik_oneriler))
    if p and p.get("history"):
        with open("portfolio.html", "w", encoding="utf-8") as f:
            f.write(build_portfolio_html(p))

    # Yeni bolumler: hepsi data/ klasorundeki gecmisle calisir, LLM gerekmez;
    # tek tek try/except ile birinin hatasi digerlerini etkilemez.
    try:
        style_css_yaz()
    except Exception:
        logger.exception("[CSS] style.css yazilamadi; sayfalar etkilenmez.")
    try:
        piyasa_serit_yaz()
    except Exception:
        logger.exception("[Piyasa] serit yedegi yazilamadi.")
    # Makro verisi yalnizca akşam snapshot workflow'unda guncellenir.
    # Sirket haberleri: hisse detay sayfalari + sirket-haberleri.html icin
    # hisse sayfalarindan ONCE cekilir (~40sn; Google News RSS, anahtarsiz).
    sirket_haber_map = {}
    try:
        _sirket_haberleri_cek(_sirket_profilleri())
        sirket_haber_map = _sirket_haberleri_yukle()
    except Exception:
        logger.exception("[Sirket Haberi] cekilemedi; hisse sayfalari eski yontemle uretilir.")

    try:
        hisse_sayfalari_yaz(teknik_satirlar)
    except Exception:
        logger.exception("[Hisseler] sayfalar uretilemedi.")
    try:
        with open("sirket-haberleri.html", "w", encoding="utf-8") as f:
            f.write(build_sirket_haberleri_html(sirket_haber_map, teknik_satirlar))
    except Exception:
        logger.exception("[Sirket Haberi] sayfa uretilemedi.")
    try:
        sinyal_karnesi_yaz(teknik_satirlar)
    except Exception:
        logger.exception("[Karne] uretilemedi.")
    try:
        haberler_yaz()
    except Exception:
        logger.exception("[Haberler] uretilemedi.")
    try:
        sozluk_yaz()
    except Exception:
        logger.exception("[Sozluk] uretilemedi.")
    try:
        terimler_yaz()
    except Exception:
        logger.exception("[Terimler] uretilemedi.")
    try:
        takvim_yaz()
    except Exception:
        logger.exception("[Takvim] uretilemedi.")
    try:
        podcast_rss_yaz()
    except Exception:
        logger.exception("[Podcast] RSS yazilamadi.")
    try:
        rapor_podcast_yaz()
    except Exception:
        logger.exception("[Podcast] rapor RSS'i yazilamadi.")

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

    # NOT (2026-09-21): Cok dilli ceviri bu kosudan CIKARILDI.
    # Gunluk rapor kosusu 100 dk sinirinda ceviri yuzunden iptal olmustu; artik
    # ceviri ayri bir workflow'da (.github/workflows/i18n.yml) calisiyor.

    # IndexNow: degisen sayfalari Bing/Yandex/Seznam/Naver'a aninda bildir.
    # Sitemap'teki TUM URL'ler bildirilir (hisse detay sayfalari, arsivler,
    # sirket haberleri dahil); boylece yeni eklenen her sayfa da otomatik
    # kapsama girer. IndexNow istek basina 10.000 URL kabul eder.
    try:
        sitemap_metni = open("sitemap.xml", encoding="utf-8").read()
        tum_url = re.findall(r"<loc>([^<]+)</loc>", sitemap_metni) or \
            INDEXNOW_ANA_SAYFALAR + [f"reports/{fn}" for fn in raporlar[:5]]
        indexnow_ping(tum_url)
    except Exception:
        logger.exception("[IndexNow] ping atlamasi sorun degil.")

    # Izleme: LLM gecikme metriklerini diske yaz (hata uretimi etkilemez)
    try:
        metrik_dosyasi_yaz()
    except Exception:
        logger.exception("[Metrik] metrik yazimi atlandi.")

    print("RAPOR OLUSTURULDU:", date_str)
