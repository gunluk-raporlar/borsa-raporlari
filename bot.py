import os
import time
import pandas as pd
from datetime import datetime
import zoneinfo
from openai import OpenAI
from typing import TypedDict
from langgraph.graph import StateGraph, END
import feedparser

# ==== AMD Radeon Developer Cloud API ====
AMD_API_KEY = os.environ.get("AMD_API_KEY", "")
if not AMD_API_KEY:
    raise SystemExit("AMD_API_KEY ortam degiskeni ayarlanmamis!")

client = OpenAI(
    api_key=AMD_API_KEY,
    base_url="https://developer.amd.com.cn/radeon/api/v1"
)

# Takip edilen BIST30 hisseleri
HISSELER = ["THYAO", "GARAN", "AKBNK", "EREGL", "KCHOL", "SISE", "BIMAS", "TUPRS", "ASELS", "SAHOL"]

# ---- YEREL VERI DEPOSU (hafiza katmani) ----
DATA_DIR = "data"
import json


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

class AgentState(TypedDict):
    news_data: str
    tech_data: str
    tech_prices: dict
    fundamental_data: str
    final_report: str

# ---------- HABER AJANI ----------
def news_agent(state: AgentState):
    print("[Haber Ajani] Finans haberleri toplaniyor...")
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    toplanan = []
    for ad, url in HABER_KAYNAKLARI.items():
        try:
            f = feedparser.parse(url)
            for e in f.entries[:5]:
                toplanan.append(f"[{ad}] {e.title}")
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

# ---------- TEKNIK AJAN (hisse fiyatlari) ----------
def technical_agent(state: AgentState):
    print("[Teknik Ajan] BIST30 hisse fiyatlari cekiliyor (Is Yatirim)...")
    try:
        from isyatirimhisse import fetch_stock_data
    except Exception as e:
        return {"tech_data": f"Hisse verisi alinamadi: {e}"}

    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    satirlar = []
    bugun_fiyatlar = {}
    for hisse in HISSELER:
        try:
            df = fetch_stock_data(symbols=[hisse], start_date="01-08-2026", end_date="05-09-2026")
            if df is None or df.empty:
                continue
            son = df.iloc[-1]
            fiyat = son.get("HGDG_KAPANIS")
            onceki = df.iloc[-6]["HGDG_KAPANIS"] if len(df) >= 6 else fiyat
            if fiyat and onceki:
                degisim = ((fiyat - onceki) / onceki) * 100
                satirlar.append(f"{hisse}: {fiyat:.2f} TL (5 gunluk %{degisim:+.2f})")
                bugun_fiyatlar[hisse] = round(float(fiyat), 2)
        except Exception:
            pass
        time.sleep(4)  # Is Yatirim sitesini zorlamamak icin

    if bugun_fiyatlar:
        save_daily("prices", bugun, bugun_fiyatlar)

    # Gecmis fiyatlar (aylik teknik analiz icin)
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
    print("[Temel Ajan] Sirket finansal verileri cekiliyor...")
    try:
        from isyatirimhisse import fetch_financials
    except Exception as e:
        return {"fundamental_data": f"Finansal veri alinamadi: {e}"}

    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    ozetler = []
    for hisse in HISSELER[:6]:  # Hiz icin ilk 6 sirket
        try:
            fin = fetch_financials(symbols=[hisse], start_year=2025, end_year=2026, financial_group="1")
            if fin is None or fin.empty:
                continue
            # Hasilat ve net donem kari satirlarini bul
            satir = fin[fin["FINANCIAL_ITEM_CODE"].isin(["1A", "3A"])]
            son_kolon = [c for c in fin.columns if str(c).startswith("2026")][-1] if any(str(c).startswith("2026") for c in fin.columns) else None
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
    print("[Bas Analist] Rapor sentezleniyor (DeepSeek)...")
    prompt = f"""
    Sen kidemli bir Hedge-Fund Portfoy Yoneticisisin. Asagidaki GERCEK verileri kullanarak profesyonel bir BIST 30 Yatirim Raporu yaz.

    [GUNUN HABERLERI]:
    {state['news_data']}

    [TEKNIK VERILER (hisse fiyatlari)]:
    {state['tech_data']}

    [TEMEL/FINANSAL VERILER]:
    {state['fundamental_data']}

    Raporu su basliklarla olustur:
    1. Yonetici Ozeti
    2. Haber ve Makro Degerlendirme
    3. Teknik Degerlendirme (hisse bazli)
    4. Sirket/Finansal Degerlendirme
    5. Risk Yonetimi ve Strateji
    6. ONERILEN PORTFOY: Haftalik ve aylik olarak onerilen hisse dagilimi (yuzde olarak, ornegin THYAO %20, GARAN %15 gibi) ve kisa aciklama.

    Verileri dogrudan kullan, uydurma veri ekleme. Raporu Turkce yaz.
    """
    response = llm_call(prompt)
    return {"final_report": response}


def llm_call(prompt, max_deneme=6):
    """API cagrisi; hiz siniri (429) olursa bekleyip tekrar dener."""
    import openai
    for deneme in range(max_deneme):
        try:
            resp = client.chat.completions.create(
                model="DeepSeek-V4-Flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return resp.choices[0].message.content
        except openai.RateLimitError as e:
            bekle = 20 * (deneme + 1)  # 20sn, 40sn, 60sn... artarak bekle
            print(f"[Uyari] API hiz siniri ({e}). {bekle} sn bekleniyor, tekrar deneniyor ({deneme+1}/{max_deneme})...")
            time.sleep(bekle)
        except Exception as e:
            print(f"[Hata] API cagrisi basarisiz: {e}")
            time.sleep(10)
    raise RuntimeError("API cagrisi maksimum deneme sayisinda da tamamlanamadi.")

# ---------- DENEME PORTFOYU TAKIBI ----------
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
    print("[Portfoy Ajani] Deneme portfoyu guncelleniyor...")
    import json

    # Teknik ajandan gelen fiyatlari kullan (tekrar internetten cekme)
    fiyatlar = state.get("tech_prices") or {}

    if not fiyatlar:
        return {"final_report": state.get("final_report", "")}

    p = load_portfolio()
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")

    if p is None:
        # Ilk gun: esit dagilimli portfoy kur
        hisse_adedi = len(fiyatlar)
        pay = BASLANGIC_SERMAYE / hisse_adedi
        p = {
            "start_date": bugun,
            "initial_capital": BASLANGIC_SERMAYE,
            "initial_prices": {h: fiyatlar[h] for h in fiyatlar},
            "shares": {h: round(pay / fiyatlar[h], 2) for h in fiyatlar},
            "history": [],
        }

    # Bugunku deger ve yuzde
    toplam = sum(p["shares"][h] * fiyatlar[h] for h in p["shares"] if h in fiyatlar)
    yuzde = ((toplam - BASLANGIC_SERMAYE) / BASLANGIC_SERMAYE) * 100

    # Gunluk degisim (dun vs bugun)
    dun = None
    if p["history"]:
        dun = p["history"][-1]["total"]
    gunluk_yuzde = ((toplam - dun) / dun * 100) if dun else 0.0

    p["history"].append({
        "date": bugun,
        "total": round(toplam, 2),
        "pct": round(yuzde, 2),
        "daily_pct": round(gunluk_yuzde, 2),
        "prices": {h: fiyatlar[h] for h in fiyatlar},
    })
    save_portfolio(p)

    # Portfoy ozeti (rapora eklenecek)
    ozet = f"Deneme Portfoyu ({bugun}): Toplam {round(toplam,2):.2f} TL (baslangic {BASLANGIC_SERMAYE:.0f} TL, toplam %{yuzde:+.2f}, gunluk %{gunluk_yuzde:+.2f})"
    return {"final_report": state.get("final_report", "") + "\n\n[PORTFOY OZETI]\n" + ozet}

workflow = StateGraph(AgentState)
workflow.add_node("news", news_agent)
workflow.add_node("technical", technical_agent)
workflow.add_node("fundamental", fundamental_agent)
workflow.add_node("cio", master_cio_agent)
workflow.add_node("portfolio", portfolio_agent)
workflow.set_entry_point("news")
workflow.add_edge("news", "technical")
workflow.add_edge("technical", "fundamental")
workflow.add_edge("fundamental", "cio")
workflow.add_edge("cio", "portfolio")
workflow.add_edge("portfolio", END)
app = workflow.compile()

def build_html(report, date_str):
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Piyasa Raporu - {date_str}</title>
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; background:#fff; color:#111; line-height:1.75; }}
.header {{ border-bottom:1px solid #e5e5e5; padding:18px 24px; }}
.header .inner {{ max-width:760px; margin:0 auto; display:flex; justify-content:space-between; align-items:baseline; }}
.header a {{ color:#111; text-decoration:none; font-size:14px; letter-spacing:.5px; }}
.header .brand {{ font-size:20px; font-weight:bold; }}
.wrap {{ max-width:760px; margin:0 auto; padding:40px 24px; }}
.article h1 {{ font-size:30px; margin:0 0 8px; font-weight:normal; }}
.article .meta {{ color:#777; font-size:13px; border-bottom:1px solid #eee; padding-bottom:18px; margin-bottom:24px; }}
.content {{ font-size:16px; }}
.content h1, .content h2, .content h3 {{ font-weight:normal; }}
.footer {{ text-align:center; color:#999; font-size:12px; padding:30px; border-top:1px solid #eee; }}
</style>
</head>
<body>
<div class="header"><div class="inner"><a class="brand" href="index.html">BIST 30 Piyasa Raporlari</a><a href="index.html">Arsiv</a></div></div>
<div class="wrap">
<div class="article">
<h1>Gunluk Piyasa Raporu</h1>
<div class="meta">{date_str}</div>
<div class="content">{report.replace(chr(10), '<br>')}</div>
</div>
</div>
<div class="footer">Bilgilendirme amaciyla hazirlanmistir, yatirim tavsiyesi degildir.</div>
</body></html>"""

if __name__ == "__main__":
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    date_str = datetime.now(tz).strftime('%Y-%m-%d')
    result = app.invoke({"news_data": "", "tech_data": "", "tech_prices": {}, "fundamental_data": "", "final_report": ""})
    report = result["final_report"]

    os.makedirs("reports", exist_ok=True)
    with open(f"reports/{date_str}.html", "w", encoding="utf-8") as f:
        f.write(build_html(report, date_str))

    files = sorted(os.listdir("reports"), reverse=True)
    items = "".join(
        f'<a class="card" href="reports/{fn}"><span class="date">{fn.replace(".html","")}</span><span class="sub">Gunluk raporu ac &rarr;</span></a>'
        for fn in files if fn.endswith(".html")
    )
    index = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BIST 30 Piyasa Raporlari</title>
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; background:#fff; color:#111; line-height:1.6; }}
.header {{ border-bottom:1px solid #e5e5e5; padding:18px 24px; }}
.header .inner {{ max-width:760px; margin:0 auto; display:flex; justify-content:space-between; align-items:baseline; }}
.header .brand {{ font-size:20px; font-weight:bold; text-decoration:none; color:#111; }}
.header .tag {{ color:#999; font-size:13px; }}
.wrap {{ max-width:760px; margin:0 auto; padding:40px 24px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px,1fr)); gap:20px; }}
.card {{ border:1px solid #e5e5e5; padding:22px; text-decoration:none; color:#111; display:block; transition:.15s; }}
.card:hover {{ border-color:#111; }}
.card .date {{ font-size:18px; font-weight:bold; }}
.card .sub {{ color:#777; font-size:13px; margin-top:6px; }}
.empty {{ color:#999; text-align:center; padding:40px; }}
.footer {{ text-align:center; color:#999; font-size:12px; padding:30px; border-top:1px solid #eee; }}
</style>
</head>
<body>
<div class="header"><div class="inner"><span class="brand">BIST 30 Piyasa Raporlari</span><span class="tag"><a href="portfolio.html" style="color:#777;">Deneme Portfoyu</a></span></div></div>
<div class="wrap">
{"" if items else '<div class="empty">Henuz rapor yok.</div>'}
<div class="grid">
{items}
</div>
</div>
<div class="footer">Bilgilendirme amaciyla hazirlanmistir, yatirim tavsiyesi degildir.</div>
</body></html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(index)

    # ---- Deneme Portfoyu sayfasi ----
    p = load_portfolio()
    if p:
        satirlar = []
        for h in p.get("shares", {}):
            ilk = p["initial_prices"].get(h)
            guncel = p["history"][-1]["prices"].get(h) if p["history"] else ilk
            if ilk and guncel:
                fark = ((guncel - ilk) / ilk) * 100
                satirlar.append(
                    f"<tr><td>{h}</td><td>{ilk:.2f}</td><td>{guncel:.2f}</td>"
                    f"<td style='color:{'#0b6e4f' if fark>=0 else '#b00020'}'>{fark:+.2f}%</td></tr>"
                )
        tablo = "".join(satirlar)
        son = p["history"][-1]
        gecmis = "".join(
            f"<tr><td>{g['date']}</td><td>{g['total']:.2f} TL</td><td>{g['pct']:+.2f}%</td><td>{g['daily_pct']:+.2f}%</td></tr>"
            for g in reversed(p["history"])
        )
        portfolio_html = f"""<!DOCTYPE html>
<html lang="tr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Deneme Portfoyu</title>
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; background:#fff; color:#111; line-height:1.6; }}
.header {{ border-bottom:1px solid #e5e5e5; padding:18px 24px; }}
.header .inner {{ max-width:760px; margin:0 auto; display:flex; justify-content:space-between; align-items:baseline; }}
.header a {{ color:#111; text-decoration:none; }}
.header .brand {{ font-size:20px; font-weight:bold; }}
.wrap {{ max-width:760px; margin:0 auto; padding:40px 24px; }}
h1 {{ font-size:26px; font-weight:normal; }}
table {{ width:100%; border-collapse:collapse; margin-top:20px; }}
th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid #eee; font-size:15px; }}
th {{ color:#777; font-weight:normal; }}
.footer {{ text-align:center; color:#999; font-size:12px; padding:30px; border-top:1px solid #eee; }}
</style>
</head>
<body>
<div class="header"><div class="inner"><a class="brand" href="index.html">BIST 30 Piyasa Raporlari</a><a href="index.html">Raporlar</a></div></div>
<div class="wrap">
<h1>Deneme Portfoyu</h1>
<p>Baslangic: {p['start_date']} | Baslangic Sermayesi: {p['initial_capital']:.0f} TL | Gunluk: {son['daily_pct']:+.2f}% | Toplam: {son['pct']:+.2f}%</p>
<h2>Hisse Performansi (ilk alim vs guncel)</h2>
<table><tr><th>Hisse</th><th>Ilk Alim</th><th>Guncel</th><th>Yuzde</th></tr>{tablo}</table>
<h2>Gunluk Gecmis</h2>
<table><tr><th>Tarih</th><th>Toplam Deger</th><th>Toplam %</th><th>Gunluk %</th></tr>{gecmis}</table>
</div>
<div class="footer">Bilgilendirme amaciyla hazirlanmistir, yatirim tavsiyesi degildir.</div>
</body></html>"""
        with open("portfolio.html", "w", encoding="utf-8") as f:
            f.write(portfolio_html)

    print("RAPOR OLUSTURULDU:", date_str)
