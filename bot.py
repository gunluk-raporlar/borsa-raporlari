import os
import json
import time
import pandas as pd
from datetime import datetime, timedelta
import zoneinfo
from openai import OpenAI
from typing import TypedDict
from langgraph.graph import StateGraph, END
import feedparser
import markdown

# ==== AMD Radeon Developer Cloud API ====
AMD_API_KEY = os.environ.get("AMD_API_KEY", "")
if not AMD_API_KEY:
    raise SystemExit("AMD_API_KEY ortam degiskeni ayarlanmamis!")

client = OpenAI(
    api_key=AMD_API_KEY,
    base_url="https://developer.amd.com.cn/radeon/api/v1"
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
    print("[Haber Ajani] Finans haberleri toplaniyor...")
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")
    toplanan = []
    for ad, url in HABER_KAYNAKLARI.items():
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

# ---------- TEKNIK AJAN (hisse fiyatlari) ----------
def technical_agent(state: AgentState):
    print("[Teknik Ajan] BIST30 hisse fiyatlari cekiliyor (Is Yatirim)...")
    try:
        from isyatirimhisse import fetch_stock_data
    except Exception as e:
        return {"tech_data": f"Hisse verisi alinamadi: {e}"}

    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    simdi = datetime.now(tz)
    bugun = simdi.strftime("%Y-%m-%d")
    # Son ~40 gunluk veri cek (5 gunluk degisim hesabi icin yeterli)
    bitis = simdi.strftime("%d-%m-%Y")
    baslangic = (simdi - timedelta(days=40)).strftime("%d-%m-%Y")
    satirlar = []
    bugun_fiyatlar = {}
    for hisse in HISSELER:
        try:
            df = fetch_stock_data(symbols=[hisse], start_date=baslangic, end_date=bitis)
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
    bu_yil = datetime.now(tz).year
    for hisse in HISSELER:
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
    print("[Bas Analist] Rapor sentezleniyor (DeepSeek)...")

    # Hafiza: onceki gunlerin analiz ozetleri
    gecmis_ozetler = load_recent("summaries", gun=14)
    hafiza_metni = ""
    if gecmis_ozetler:
        hafiza_metni = "\n[GECMIS GUNLERIN ANALIZ OZETLERI - HAFIZA]:\n"
        for g in gecmis_ozetler:
            hafiza_metni += f"-- {g['date']}: {g['data'].get('ozet', '')}\n"

    prompt = f"""
    Sen kidemli bir Hedge-Fund Portfoy Yoneticisisin. Asagidaki GERCEK verileri kullanarak profesyonel bir BIST 30 Yatirim Raporu yaz.
    Onceki gunlere ait analiz ozetlerini de dikkate al; trend devam ediyor mu, onceki oneriler nasil performans gosterdi degerlendir.
{hafiza_metni}
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


# ---------- OZET AJANI (hafiza indeksleme) ----------
def summary_agent(state: AgentState):
    """Gunun raporunu kisa bir ozete donusturup data/summaries/ altina indeksler.
    Boylece ertesi gunler bu ozetleri okuyarak gecmisi hatirlar."""
    print("[Ozet Ajani] Gunun analizi hafizaya indeksleniyor...")
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

    # Gunluk degisim (dun vs bugun) - ayni gun tekrar calisirsa son kayit guncellenir
    if p["history"] and p["history"][-1]["date"] == bugun:
        p["history"].pop()
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
<style>{BASE_CSS}</style>
</head>
<body>
<header class="topbar"><div class="inner">
<a class="brand" href="{kok}index.html">BIST 30 Günlük Raporlar</a>
<nav><a href="{kok}index.html"{a_r}>Raporlar</a><a href="{kok}portfolio.html"{a_p}>Deneme Portföyü</a></nav>
</div></header>
<main class="wrap">
{icerik}
</main>
<footer class="footer">Bilgilendirme amacıyla hazırlanmıştır, yatırım tavsiyesi değildir.<br>Veri kaynakları: İş Yatırım, RSS haber akışları &bull; Analiz: yapay zeka (çok-ajanlı sistem)</footer>
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


def sparkline_svg(degerler, genislik=760, yukseklik=200):
    """Portfoy gecmisinden basit SVG cizgi grafigi uretir."""
    if len(degerler) < 2:
        return ""
    mn, mx = min(degerler), max(degerler)
    fark = (mx - mn) or 1.0
    sol, sag, ust, alt = 10, 14, 16, 28
    iy = yukseklik - ust - alt
    adim = (genislik - sol - sag) / (len(degerler) - 1)
    pts = [(sol + i * adim, ust + (mx - v) / fark * iy) for i, v in enumerate(degerler)]
    cizgi = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    sx, sy = pts[-1]
    renk = "#047857" if degerler[-1] >= degerler[0] else "#b91c1c"
    return f"""<svg class="chart" viewBox="0 0 {genislik} {yukseklik}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Portfoy performans grafigi">
<polyline points="{cizgi}" fill="none" stroke="{renk}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
<circle cx="{sx:.1f}" cy="{sy:.1f}" r="4.5" fill="{renk}"/>
<text x="{sol}" y="{yukseklik - 8}" font-size="11" fill="#64748b">Min: {mn:,.0f} TL</text>
<text x="{genislik - sag}" y="{yukseklik - 8}" font-size="11" fill="#64748b" text-anchor="end">Maks: {mx:,.0f} TL</text>
</svg>"""


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
        grafik = sparkline_svg([g["total"] for g in p["history"]])
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
    grafik = sparkline_svg([g["total"] for g in p["history"]])
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
<p>10 BIST 30 hissesine eşit dağıtılmış {p['initial_capital']:,.0f} TL'lik sanal portföy. Alım-satım yapılmaz, sadece takip edilir.</p>
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
