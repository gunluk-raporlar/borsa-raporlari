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

# Ag takilmalarinda sonsuza kadar beklememek icin genel soket zaman asimi.
socket.setdefaulttimeout(30)

# ==== AMD Radeon Developer Cloud API ====
AMD_API_KEY = os.environ.get("AMD_API_KEY", "")
if not AMD_API_KEY:
    raise SystemExit("AMD_API_KEY ortam degiskeni ayarlanmamis!")
AMD_MODEL = os.environ.get("AMD_MODEL", "DeepSeek-V4-Flash")

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

# ---------- HABER AJANI ----------
def news_agent(state: AgentState):
    print("[Haber Ajani] Finans haberleri toplaniyor...", flush=True)
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    toplanan = []
    for ad, url in HABER_KAYNAKLARI.items():
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

# ---------- TEKNİK AJAN (hisse fiyatlari toplu çekim) ----------
def technical_agent(state: AgentState):
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

# ---------- BAS ANALIST (CIO) ----------
def master_cio_agent(state: AgentState):
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

    response = llm_call(prompt)
    return {"final_report": response}

# ---------- OZET AJANI (hafiza indeksleme) ----------
def summary_agent(state: AgentState):
    """Gunun raporunu kisa bir ozete donusturup data/summaries/ altina indeksler.
    Boylece ertesi gunler bu ozetleri okuyarak gecmisi hatirlar."""
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
    ozet = llm_call(prompt)
    save_daily("summaries", bugun, {"ozet": ozet})
    return {}


def llm_call(prompt, max_deneme=3):
    """API cagrisi; hiz siniri (429) olursa bekleyip tekrar dener.
    max_tokens yuksek tutuluyor cunku bu endpoint icin gercek maliyet
    uretilen token sayisina gore hesaplaniyor, ust siniri yuksek tutmanin
    ek bir bedeli yok - sadece yaniti erken kesilmekten koruyor."""
    import openai
    for deneme in range(max_deneme):
        try:
            resp = client.chat.completions.create(
                model=AMD_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=50000,
            )
            secim = resp.choices[0]
            if getattr(secim, "finish_reason", None) == "length":
                print("[Uyari] Yanit token limitine takilip erken kesilmis olabilir.", flush=True)
            return secim.message.content
        except openai.RateLimitError as e:
            bekle = min(10 * (deneme + 1), 30)  # 10sn, 20sn, 30sn
            print(f"[Uyari] API hiz siniri ({type(e).__name__}: {e}). {bekle} sn bekleniyor, tekrar deneniyor ({deneme+1}/{max_deneme})...", flush=True)
            time.sleep(bekle)
        except Exception as e:
            print(f"[Hata] API cagrisi basarisiz ({type(e).__name__}): {e!r}", flush=True)
            time.sleep(10)
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
    print("[Portfoy Ajani] Deneme portfoyu ve kiyaslamalar guncelleniyor...", flush=True)
    import json
    from datetime import datetime as dt

    try:
        import yfinance as yf
    except ModuleNotFoundError:
        yf = None
        print("[Uyari] yfinance kurulu degil; varsayilan USD ve altin degerleri kullanilacak.")

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
            print(f"[Uyari] Kıyaslama kurları çekilemedi, son değerler kullanılacak: {e}")

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
    """Portfoy gecmisini Chart.js ile karsilastirmali cizgi grafigine donusturur.

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
    if p:
        with open("portfolio.html", "w", encoding="utf-8") as f:
            f.write(build_portfolio_html(p))

    print("RAPOR OLUSTURULDU:", date_str)
