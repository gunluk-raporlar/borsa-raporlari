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
    fundamental_data: str
    final_report: str

# ---------- HABER AJANI ----------
def news_agent(state: AgentState):
    print("[Haber Ajani] Finans haberleri toplaniyor...")
    toplanan = []
    for ad, url in HABER_KAYNAKLARI.items():
        try:
            f = feedparser.parse(url)
            for e in f.entries[:5]:
                toplanan.append(f"[{ad}] {e.title}")
        except Exception:
            continue
        time.sleep(1)
    if not toplanan:
        return {"news_data": "Haber verisi alinamadi."}
    return {"news_data": "\n".join(toplanan[:25])}

# ---------- TEKNIK AJAN (hisse fiyatlari) ----------
def technical_agent(state: AgentState):
    print("[Teknik Ajan] BIST30 hisse fiyatlari cekiliyor (Is Yatirim)...")
    try:
        from isyatirimhisse import fetch_stock_data
    except Exception as e:
        return {"tech_data": f"Hisse verisi alinamadi: {e}"}

    satirlar = []
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
        except Exception:
            pass
        time.sleep(4)  # Is Yatirim sitesini zorlamamak icin

    if not satirlar:
        return {"tech_data": "Hisse verisi alinamadi."}
    return {"tech_data": "\n".join(satirlar)}

# ---------- TEMEL AJAN (finansal tablolar) ----------
def fundamental_agent(state: AgentState):
    print("[Temel Ajan] Sirket finansal verileri cekiliyor...")
    try:
        from isyatirimhisse import fetch_financials
    except Exception as e:
        return {"fundamental_data": f"Finansal veri alinamadi: {e}"}

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

    Verileri dogrudan kullan, uydurma veri ekleme. Raporu Turkce yaz.
    """
    response = client.chat.completions.create(
        model="DeepSeek-V4-Flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return {"final_report": response.choices[0].message.content}

workflow = StateGraph(AgentState)
workflow.add_node("news", news_agent)
workflow.add_node("technical", technical_agent)
workflow.add_node("fundamental", fundamental_agent)
workflow.add_node("cio", master_cio_agent)
workflow.set_entry_point("news")
workflow.add_edge("news", "technical")
workflow.add_edge("technical", "fundamental")
workflow.add_edge("fundamental", "cio")
workflow.add_edge("cio", END)
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
    result = app.invoke({"news_data": "", "tech_data": "", "fundamental_data": "", "final_report": ""})
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
<div class="header"><div class="inner"><span class="brand">BIST 30 Piyasa Raporlari</span><span class="tag">Gunluk analiz arsivi</span></div></div>
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

    print("RAPOR OLUSTURULDU:", date_str)
