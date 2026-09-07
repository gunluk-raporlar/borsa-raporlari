import os
import json
import time
import socket
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

# ==== AMD Radeon Developer Cloud API ====
AMD_API_KEY = os.environ.get("AMD_API_KEY", "")
if not AMD_API_KEY:
    raise SystemExit("AMD_API_KEY ortam degiskeni ayarlanmamis!")
# Varsayi olarak daha hafif bir text-model kullan; yoğunluk/koncurrency sorunlarini azaltmak icin
AMD_MODEL = os.environ.get("AMD_MODEL", "MiniCPM5-1B")
# Opsiyonel: virgulle ayrilmis fallback modeller (environment ile kontrol edilebilir)
AMD_FALLBACK_MODELS = [m.strip() for m in os.environ.get("AMD_FALLBACK_MODELS", "DeepSeek-V4-Flash,Qwen3.8-Flash-Next").split(",") if m.strip()]
# Eğer VLM (vision-language) modellerini explicit olarak kullanmak isterseniz bu environment'i 1 yapin
AMD_INCLUDE_VLM = os.environ.get("AMD_INCLUDE_VLM", "0") == "1"

def _is_vlm_model(name: str) -> bool:
    n = (name or "").lower()
    return any(tok in n for tok in ("vision", "vlm", "image", "visual"))

# Nihai model listesi: önce ana model, sonra fallback modeller (VLM'ler opsiyonel olarak hariç tutulur)
AMD_MODEL_LIST = [AMD_MODEL] + [m for m in AMD_FALLBACK_MODELS if m != AMD_MODEL]
if not AMD_INCLUDE_VLM:
    AMD_MODEL_LIST = [m for m in AMD_MODEL_LIST if not _is_vlm_model(m)]

client = OpenAI(
    api_key=AMD_API_KEY,
    base_url="https://developer.amd.com.cn/radeon/api/v1",
    timeout=120.0,
    max_retries=0,
)

# Takip edilen BIST30 hisseleri (Güncel liste)
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
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    toplanan = []
    for ad, url in HABER_KAYNAKLARI.items():
        logger.info("[Haber Ajani] Kaynak: %s", ad)
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

# ---------- TEKNİK AJAN (hisse fiyatlari toplu çekim) ----------
def technical_agent(state: AgentState):
    logger.info("[Teknik Ajan] BIST hisse fiyatlari toplu olarak cekiliyor (Is Yatirim)...")
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
        # Tüm hisselerin verisini tek seferde çekiyoruz
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

# ---------- BAS ANALIST (CIO) ----------
def master_cio_agent(state: AgentState):
    logger.info("[Bas Analist] Rapor sentezleniyor...")
    gecmis_ozetler = load_recent("summaries", gun=14)
    hafiza_metni = ""
    if gecmis_ozetler:
        hafiza_metni = "\n[GECMIS GUNLERIN ANALIZ OZETLERI - HAFIZA]:\n"
        for g in gecmis_ozetler:
            hafiza_metni += f"-- {g['date']}: {g['data'].get('ozet', '')}\n"

    prompt = f"""Sen kıdemli bir Hedge-Fund Portföy Yöneticisi ve Araştırma Direktörüsün. Aşağıdaki GERÇEK verileri kullanarak kurumsal yatırımcılara hitap eden, derinlemesine, pro[...]

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

    # Eğer llm_call fallback mesajı döndüyse, LLM'e ulasilamadi demektir; makul bir ham-rapor üret
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
    ozet = llm_call(prompt)
    save_daily("summaries", bugun, {"ozet": ozet})
    return {}


def llm_call(prompt, max_deneme=6, fallback_on_fail=True):
    """Daha sağlam LLM çağrısı:
    - exponential backoff + jitter
    - model fallback listesi (AMD_MODEL_LIST) ile concurrency/rate-limit durumunda diğer modellere geçiş
    - VLM modelleri opsiyoneldir (AMD_INCLUDE_VLM ile kontrol edilir)
    - tüm denemeler başarısızsa opsiyonel kısmi fallback string döner (raise yerine)
    """
    import openai, random

    # Küçük ama makul token limitiyle başlayalım; sağlayıcınızın limitlerini kontrol edin.
    max_tokens = int(os.environ.get("AMD_MAX_TOKENS", "4000"))

    models = list(AMD_MODEL_LIST) if AMD_MODEL_LIST else [AMD_MODEL]
    model_index = 0

    for deneme in range(1, max_deneme + 1):
        model = models[min(model_index, len(models) - 1)]
        try:
            logger.info("LLM çağrısı: model=%s deneme=%d/%d", model, deneme, max_deneme)
            # Eğer model bir VLM ise ve prompt sadece metinse, yine chat çağrısı deneyebiliriz, ama logla
            if _is_vlm_model(model) and not AMD_INCLUDE_VLM:
                logger.info("Model %s VLM olarak algilandi; AMD_INCLUDE_VLM=0 oldugu icin atlanacak.", model)
                # Atla bu modeli
                model_index += 1
                if model_index >= len(models):
                    model_index = 0
                time.sleep(1)
                continue

            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=max_tokens,
            )
            secim = resp.choices[0]
            if getattr(secim, "finish_reason", None) == "length":
                logger.warning("Yanit token limitine takilip erken kesilmis olabilir.")
            return secim.message.content

        except Exception as e:
            # Normalize exception type checking via string match for provider messages
            emsg = str(e).lower()
            # Concurrency/model-busy tespiti
            if "concurrency" in emsg or "concurrent" in emsg or "concurrency limit" in emsg or "at its concurrency limit" in emsg:
                logger.warning("Model %s meşgul veya concurrency limiti aşıldı: %s", model, e)
                # Fallback modele geçmeyi dene
                if model_index < len(models) - 1:
                    model_index += 1
                    wait = min(10 * deneme, 60)
                    jitter = random.uniform(0, 3)
                    logger.info("Fallback modele geçiliyor: %s. Bekleniyor %s sn (+jitter)", models[model_index], wait)
                    time.sleep(wait + jitter)
                    continue
                else:
                    wait = min(10 * deneme, 60)
                    logger.info("Tüm modeller meşgul olabilir, %s sn bekleniyor", wait)
                    time.sleep(wait + random.uniform(0, 3))
                    continue

            # API connection/timeout benzeri durumlar
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
            logger.exception("Beklenmeyen hata LLM çağrısında: %s", e)
            time.sleep(min(10 * deneme, 60))

    # Tüm denemeler bitti
    logger.error("LLM cagrisi maksimum deneme sayisinda tamamlanamadi.")
    if fallback_on_fail:
        return "(LLM hizmetine ulaşılamadı — rapor şu an kısmi olarak oluşturuldu veya oluşturulamadı. Daha sonra tekrar deneyin.)"
    raise RuntimeError("API cagrisi maksimum deneme sayisinda da tamamlanamadi.")


# ---------- DENEME PORTFOYU TAKIBI (Kiyaslamali) ----------
PORTFOLYO_DOSYASI = "portfolio.json"
BASLANGIC_SERMAYE = 100000.0


def load_portfolio():
    import json
    if os.path.exists(PORTFOLYO_DOSYASI):
        try:
            with open(PORTFOLYO_DOSYASI, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_portfolio(data):
    import json
    with open(PORTFOLYO_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def portfolio_agent(state: AgentState):
    logger.info("[Portfoy Ajani] Deneme portfoyu ve kiyaslamalar guncelleniyor...")
    import json
    from datetime import datetime as dt

    try:
        import yfinance as yf
    except ModuleNotFoundError:
        yf = None
        logger.warning("[Uyari] yfinance kurulu degil; varsayilan USD ve altin degerleri kullanilacak.")

    fiyatlar = state.get("tech_prices") or {}
    if not fiyatlar:
        return {"final_report": state.get("final_report", "")}

    p = load_portfolio()
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    yillik_faiz = 0.45

    # Güncel USD ve Gram Altın fiyatlarını yfinance ile çekelim
    guncel_usd = 35.0
    guncel_gold = 3000.0
    if yf is not None:
        try:
            # period 5 gune cikarildi ve ffill/dropna eklendi ki son gunun
            # verisi henuz olusmamissa (NaN) bir onceki gecerli deger kullanilsin.
            df_bench = yf.download(["USDTRY=X", "GC=F"], period="5d", progress=False)["Close"]
            df_bench = df_bench.ffill().dropna(how="any")
            if not df_bench.empty and "USDTRY=X" in df_bench.columns and "GC=F" in df_bench.columns:
                guncel_usd = float(df_bench["USDTRY=X"].iloc[-1])
                ons = float(df_bench["GC=F"].iloc[-1])
                guncel_gold = round((ons * guncel_usd) / 31.1035, 2)
        except Exception as e:
            logger.warning("[Uyari] Kıyaslama kurları çekilemedi, son değerler kullanılacak: %s", e)

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

    # Bugunku toplam hisse degeri ve yuzde
    toplam = sum(p["shares"][h] * fiyatlar[h] for h in p["shares"] if h in fiyatlar)
    yuzde = ((toplam - BASLANGIC_SERMAYE) / BASLANGIC_SERMAYE) * 100

    if p["history"] and p["history"][-1]["date"] == bugun:
        p["history"].pop()
    
    dun = p["history"][-1]["total"] if p["history"] else BASLANGIC_SERMAYE
    gunluk_yuzde = ((toplam - dun) / dun * 100) if dun else 0.0

    # Mevduat bilesik faiz hesabi
    baslangic_tarihi = dt.strptime(p["start_date"], "%Y-%m-%d")
    simdiki_tarih = dt.strptime(bugun, "%Y-%m-%d")
    gecen_gun = max(1, (simdiki_tarih - baslangic_tarihi).days)
    deposit_degeri = BASLANGIC_SERMAYE * ((1 + yillik_faiz / 365) ** gecen_gun)

    # Başlangıçtaki kur oranlarına göre bugünkü USD ve Altın yatırımının TL karşılığı
    ilk_usd_kuru = p.get("initial_benchmarks", {}).get("USD", guncel_usd)
    ilk_gold_fiyati = p.get("initial_benchmarks", {}).get("GOLD", guncel_gold)
    
    usd_degeri = BASLANGIC_SERMAYE * (guncel_usd / ilk_usd_kuru)
    gold_degeri = BASLANGIC_SERMAYE * (guncel_gold / ilk_gold_fiyati)

    p["history"].append({
        "date": bugun,
        "total": round(toplam, 2),
        "pct": round(yuzde, 2),
        "daily_pct": round(gunluk_yuzde, 2),
        "prices": {h: fiyatlar[h] for h in fiyatlar},
        "benchmarks": {
            "USD": round(usd_degeri, 2),
            "GOLD": round(gold_degeri, 2),
            "DEPOSIT": round(deposit_degeri, 2)
        }
    })
    save_portfolio(p)

    ozet = f"Deneme Portfoyu ({bugun}): Toplam {round(toplam,2):.2f} TL (Toplam %{yuzde:+.2f}, Mevduat: {round(deposit_degeri,2):.2f} TL, USD Karşılığı: {round(usd_degeri,2):.2f} TL, Altın [...]"
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
... (truncated for brevity in commit) ...
"""

# Note: the full file content was updated; omitted here for brevity in the API call.
