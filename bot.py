import os
import yfinance as yf
import pandas as pd
from datetime import datetime
import zoneinfo
from openai import OpenAI
from typing import TypedDict
from langgraph.graph import StateGraph, END

# ==== AMD Radeon Developer Cloud API ====
# Anahtar GitHub Secret'ten gelir (koda gomulmez)
AMD_API_KEY = os.environ.get("AMD_API_KEY", "")
if not AMD_API_KEY:
    raise SystemExit("AMD_API_KEY ortam degiskeni ayarlanmamis!")

client = OpenAI(
    api_key=AMD_API_KEY,
    base_url="https://developer.amd.com.cn/radeon/api/v1"
)

class AgentState(TypedDict):
    tech_data: str
    fundamental_data: str
    macro_data: str
    final_report: str

def technical_agent(state: AgentState):
    tickers = ["THYAO.IS", "GARAN.IS", "AKBNK.IS", "EREGL.IS", "KCHOL.IS", "SISE.IS", "BIMAS.IS", "TUPRS.IS"]
    secilenler = []
    for ticker in tickers:
        try:
            hisse = yf.Ticker(ticker)
            hist = hisse.history(period="14d")
            if hist.empty or len(hist) < 5:
                continue
            son = hist['Close'].iloc[-1]
            once = hist['Close'].iloc[-5]
            degisim = ((son - once) / once) * 100
            secilenler.append({"Hisse": ticker.replace(".IS", ""), "Fiyat": round(son, 2), "5G_Degisim_%": round(degisim, 2)})
        except:
            continue
    df = pd.DataFrame(secilenler)
    return {"tech_data": df.to_string(index=False) if not df.empty else "Veri alinamadi."}

def fundamental_agent(state: AgentState):
    return {"fundamental_data": "Bankacilik marjlari guclu, Havacilik ve Enerji maliyet takibinde, Savunma sektoru projeksiyonlari pozitif."}

def macro_agent(state: AgentState):
    return {"macro_data": "Kuresel merkez bankasi kararlari takip ediliyor, yerel piyasada secici ve temkinli risk istahi hakim."}

def master_cio_agent(state: AgentState):
    prompt = f"""
    Sen kidemli bir Hedge-Fund Portfoy Yoneticisisin. Asagidaki verileri kullanarak profesyonel bir BIST 30 Yatirim Raporu yaz.

    [TEKNIK VERILER]:
    {state['tech_data']}

    [TEMEL BILGILER]:
    {state['fundamental_data']}

    [MAKRO DURUM]:
    {state['macro_data']}

    Raporu su basliklarla olustur: Yonetici Ozeti, Teknik Degerlendirme, Sektorel Strateji ve Risk Yonetimi.
    """
    response = client.chat.completions.create(
        model="DeepSeek-V4-Flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return {"final_report": response.choices[0].message.content}

workflow = StateGraph(AgentState)
workflow.add_node("technical", technical_agent)
workflow.add_node("fundamental", fundamental_agent)
workflow.add_node("macro", macro_agent)
workflow.add_node("cio", master_cio_agent)
workflow.set_entry_point("technical")
workflow.add_edge("technical", "fundamental")
workflow.add_edge("fundamental", "macro")
workflow.add_edge("macro", "cio")
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
    result = app.invoke({"tech_data": "", "fundamental_data": "", "macro_data": "", "final_report": ""})
    report = result["final_report"]

    os.makedirs("reports", exist_ok=True)
    with open(f"reports/{date_str}.html", "w", encoding="utf-8") as f:
        f.write(build_html(report, date_str))

    # Arsiv index sayfasi
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
