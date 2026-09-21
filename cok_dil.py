"""Cok dilli sayfa uretimi (en / de / zh).

bot.py __main__ icinden guvenli hook ile cagrilir; bot.py'yi import etmez,
gerekli her seyi (rapor metni, teknik satirlar, LLM cagrisi) parametreyle alir.
Bu moduldeki hicbir hata Turkce sitenin uretimini etkileyemez; yalnizca
en/ de/ zh/ klasorleri altina yazar ve mevcut dosyalara dokunmaz.

Kapsam (v1):
  {dil}/index.html        - dile ozel giris sayfasi + son rapor listesi
  {dil}/reports/TARIH.html - gunluk raporun LLM cevirisi (yalnizca metin;
                             tablolar/sayilar asla degistirilmez)
  {dil}/hisse/index.html  - BIST 30 teknik tablo (etiketler sozlukten;
                             hisse detay linkleri Turkce sayfaya gider)

SEO: her sayfada canonical + hreflang (tr + mevcut dil kardesleri + x-default).
Sitemap: sitemap_satirlari() bot.py'nin sitemap fonksiyonundan cagrilir.
"""

import os
import re
import json
from datetime import datetime
import zoneinfo

import markdown

SITE_URL = "https://borsa-raporlari.pages.dev/"
TZ = zoneinfo.ZoneInfo("Europe/Istanbul")
DILLER = ("en", "de", "zh")

_FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
            "%3Crect width='100' height='100' rx='18' fill='%230f172a'/%3E"
            "%3Ctext y='.9em' x='12' font-size='72'%3E%F0%9F%93%88%3C/text%3E%3C/svg%3E")

# ---------------------------------------------------------------------------
# Dil sozlukleri (etiketler elle yazildi; yalnizca rapor METNI LLM ile cevrilir)
# ---------------------------------------------------------------------------
DIL = {
    "en": {
        "ad": "English", "html_lang": "en", "og_locale": "en_US", "onluk": ".",
        "site_adi": "BIST 30 Daily Reports",
        "nav_ana": "Home", "nav_raporlar": "Reports", "nav_hisseler": "Stocks",
        "ana_baslik": "BIST 30 Daily Reports — AI-Powered Turkish Stock Market Analysis",
        "ana_tanim": "AI-powered daily BIST 30 (Borsa Istanbul) analysis in English: technical screening, oscillator signals, model portfolio and virtual portfolio tracking — generated every market day by a multi-agent AI system.",
        "hero_baslik": "AI-Powered BIST 30 Daily Analysis",
        "hero_alt": "Borsa Istanbul's BIST 30 index, covered daily by a multi-agent AI system: market report, technical screening and oscillator signals.",
        "son_raporlar": "Latest reports",
        "rapor_link": "Market report — {t}",
        "paket_basligi": "Today's BIST 30 snapshot",
        "yukselenler": "Top gainers", "dusenler": "Top losers",
        "hisse_git": "Full BIST 30 technical table",
        "hisse_baslik": "BIST 30 Stocks — Technical Signals",
        "hisse_tanim": "Daily technical signals for BIST 30 stocks: trend score, daily and 60-day performance, and overall signal.",
        "hisse_not": "Detailed per-stock pages are currently available in Turkish; the table below is the English overview.",
        "th": ["Ticker", "Last", "Day %", "60-day %", "Signal"],
        "rapor_baslik": "Market Report",
        "rapor_alt": "AI-powered daily BIST 30 analysis",
        "rapor_tanim": "AI-powered BIST 30 daily market report for {t}: executive summary, macro and news review, stock-level technical reading and model portfolio view.",
        "geri": "&larr; Back to all reports",
        "tr_git": "This site in Turkish",
        "footer": "All information, commentary and suggestions on this page are for informational purposes only and do not constitute investment advisory services or investment advice. Accuracy of the data cannot be guaranteed; responsibility for any decision taken rests with the user.",
    },
    "de": {
        "ad": "Deutsch", "html_lang": "de", "og_locale": "de_DE", "onluk": ",",
        "site_adi": "BIST 30 Tagesberichte",
        "nav_ana": "Start", "nav_raporlar": "Berichte", "nav_hisseler": "Aktien",
        "ana_baslik": "BIST 30 Tagesberichte — KI-gestützte Analyse des türkischen Aktienmarkts",
        "ana_tanim": "KI-gestützte tägliche BIST-30-Analysen (Börse Istanbul) auf Deutsch: technisches Screening, Oszillatorsignale, Modellportfolio und virtuelle Portfolio-Verfolgung — an jedem Handelstag von einem Multi-Agenten-KI-System erstellt.",
        "hero_baslik": "KI-gestützte BIST-30-Tagesanalyse",
        "hero_alt": "Der BIST-30-Index der Börse Istanbul — täglich analysiert von einem Multi-Agenten-KI-System: Marktbericht, technisches Screening und Oszillatorsignale.",
        "son_raporlar": "Neueste Berichte",
        "rapor_link": "Marktbericht — {t}",
        "paket_basligi": "Heutige BIST-30-Kurzübersicht",
        "yukselenler": "Top-Gewinner", "dusenler": "Top-Verlierer",
        "hisse_git": "Vollständige BIST-30-Techniktabelle",
        "hisse_baslik": "BIST-30-Aktien — Technische Signale",
        "hisse_tanim": "Tägliche technische Signale für BIST-30-Aktien: Trendscore, Tages- und 60-Tage-Performance sowie Gesamtsignal.",
        "hisse_not": "Detaillierte Aktienseiten sind derzeit nur auf Türkisch verfügbar; die folgende Tabelle ist die deutsche Übersicht.",
        "th": ["Ticker", "Letzter", "Tag %", "60-Tage %", "Signal"],
        "rapor_baslik": "Marktbericht",
        "rapor_alt": "KI-gestützte tägliche BIST-30-Analyse",
        "rapor_tanim": "KI-gestützter BIST-30-Tagesmarktbericht vom {t}: Zusammenfassung, Makro- und Nachrichtenreview, technische Einzeltitel-Lektüre und Modellportfolio.",
        "geri": "&larr; Zurück zu allen Berichten",
        "tr_git": "Diese Seite auf Türkisch",
        "footer": "Alle auf dieser Seite enthaltenen Informationen, Kommentare und Vorschläge dienen ausschließlich der Information und stellen keine Anlageberatung oder Anlageempfehlung dar. Für die Richtigkeit der Daten kann keine Garantie übernommen werden; die Verantwortung für jede getroffene Entscheidung liegt beim Nutzer.",
    },
    "zh": {
        "ad": "中文", "html_lang": "zh", "og_locale": "zh_CN", "onluk": ".",
        "site_adi": "BIST 30 每日报告",
        "nav_ana": "首页", "nav_raporlar": "报告", "nav_hisseler": "股票",
        "ana_baslik": "BIST 30 每日报告 — AI 驱动的土耳其股市分析",
        "ana_tanim": "人工智能驱动的 BIST 30（伊斯坦布尔证券交易所）每日分析：技术筛选、振荡器信号、模型投资组合与虚拟组合跟踪——每个交易日由多智能体 AI 系统生成。",
        "hero_baslik": "AI 驱动的 BIST 30 每日分析",
        "hero_alt": "伊斯坦布尔证券交易所 BIST 30 指数，由多智能体 AI 系统每日解读：市场报告、技术筛选与振荡器信号。",
        "son_raporlar": "最新报告",
        "rapor_link": "市场报告 — {t}",
        "paket_basligi": "今日 BIST 30 速览",
        "yukselenler": "涨幅榜", "dusenler": "跌幅榜",
        "hisse_git": "完整 BIST 30 技术表",
        "hisse_baslik": "BIST 30 股票 — 技术信号",
        "hisse_tanim": "BIST 30 股票每日技术信号：趋势评分、当日与 60 天表现以及综合信号。",
        "hisse_not": "个股详情页目前仅提供土耳其语版本；下表为中文概览。",
        "th": ["代码", "最新价", "日涨跌 %", "60 天 %", "信号"],
        "rapor_baslik": "市场报告",
        "rapor_alt": "AI 驱动的 BIST 30 每日分析",
        "rapor_tanim": "{t} 的 AI 驱动 BIST 30 每日市场报告：执行摘要、宏观与新闻回顾、个股技术解读与模型投资组合观点。",
        "geri": "&larr; 返回全部报告",
        "tr_git": "本站的土耳其语版本",
        "footer": "本页面所载信息、评论和建议仅供参考，不构成投资咨询服务或投资建议。数据的准确性无法保证，因内容做出任何决策的责任由用户自行承担。",
    },
}

SINYAL = {
    "en": {"GÜÇLÜ AL": "Strong Buy", "AL": "Buy", "NÖTR": "Neutral", "SAT": "Sell", "GÜÇLÜ SAT": "Strong Sell"},
    "de": {"GÜÇLÜ AL": "Starker Kauf", "AL": "Kaufen", "NÖTR": "Neutral", "SAT": "Verkaufen", "GÜÇLÜ SAT": "Starker Verkauf"},
    "zh": {"GÜÇLÜ AL": "强力买入", "AL": "买入", "NÖTR": "中性", "SAT": "卖出", "GÜÇLÜ SAT": "强力卖出"},
}

SEKTOR = {
    "Bankacılık": ("Banking", "Banken", "银行"),
    "Holding": ("Holding", "Holdings", "控股集团"),
    "Havacılık & Ulaştırma": ("Aviation & Transport", "Luftfahrt & Verkehr", "航空与运输"),
    "Otomotiv": ("Automotive", "Automobil", "汽车"),
    "Enerji & Petrokimya": ("Energy & Petrochemicals", "Energie & Petrochemie", "能源与石化"),
    "Perakende": ("Retail", "Einzelhandel", "零售"),
    "Metal & Madencilik": ("Metals & Mining", "Metalle & Bergbau", "金属与矿业"),
    "Kimya & Gübre": ("Chemicals & Fertilizers", "Chemie & Düngemittel", "化工与化肥"),
    "Telekom": ("Telecom", "Telekom", "电信"),
    "Gıda & İçecek": ("Food & Beverage", "Lebensmittel & Getränke", "食品饮料"),
    "Cam & Seramik": ("Glass & Ceramics", "Glas & Keramik", "玻璃陶瓷"),
    "Savunma": ("Defense", "Verteidigung", "国防"),
    "Gayrimenkul": ("Real Estate", "Immobilien", "房地产"),
    "Finans (Diğer)": ("Other Financials", "Sonstige Finanzwerte", "其他金融"),
    "Sanayi (Diğer)": ("Other Industrials", "Sonstige Industrie", "其他工业"),
    "Diğer": ("Other", "Sonstige", "其他"),
}

_CEVIRI_PROMPT = (
    "Translate the following Turkish stock-market report into {ad}.\n"
    "Rules:\n"
    "- Keep every number, ticker symbol (e.g. AEFES, THYAO), currency amount and "
    "percentage exactly as in the original.\n"
    "- Preserve the Markdown structure (headings, tables, lists, bold) exactly.\n"
    "- Output ONLY the translated Markdown text, with no introduction or notes.\n\n"
    "--- REPORT START ---\n{metin}\n--- REPORT END ---"
)


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------
def _sayi(v, onluk):
    """Sayiyi dilin ondalik ayracina gore bicimlendirir (12,345.67 <-> 12.345,67)."""
    s = f"{v:,.2f}"
    if onluk == ",":
        s = s.replace(",", "@").replace(".", ",").replace("@", ".")
    return s


def _yuzde(v, onluk):
    s = f"{v:+.2f}%"
    if onluk == ",":
        s = s.replace(".", ",")
    return s


def _sinyal_hucre(genel, dil):
    sinyal = SINYAL[dil].get(genel, genel)
    cls = "pos" if genel in ("GÜÇLÜ AL", "AL") else "neg" if genel in ("SAT", "GÜÇLÜ SAT") else ""
    return f"<td class='{cls}'><strong>{sinyal}</strong></td>" if cls else f"<td>{sinyal}</td>"


def _hreflang_etiketleri(tr_yol, mevcut):
    """tr_yol: Turkce sayfanin site kokune gore yolu (or. reports/2026-09-21.html).
    mevcut: alternatif dil sayfasinin gercekten var oldugu dil kodlari."""
    satir = [f'<link rel="alternate" hreflang="tr" href="{SITE_URL}{tr_yol}">']
    for d in DILLER:
        if d in mevcut:
            satir.append(f'<link rel="alternate" hreflang="{d}" href="{SITE_URL}{d}/{tr_yol}">')
    satir.append(f'<link rel="alternate" hreflang="x-default" href="{SITE_URL}{tr_yol}">')
    return "\n".join(satir)


def _sayfa(dil, title, tanim, icerik, kok, tr_yol, mevcut):
    """Dil sayfalari icin sadelestirilmis iskelet. bot.py'nin _sayfa()'sindan
    bagimsizdir; Turkce sayfalara dokunmadan ayni style.css'i kullanir."""
    d = DIL[dil]
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "WebPage", "name": title,
        "description": tanim, "url": SITE_URL + tr_yol, "inLanguage": dil,
    }, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="{d['html_lang']}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{tanim}">
<link rel="canonical" href="{SITE_URL}{tr_yol}">
{_hreflang_etiketleri(tr_yol, mevcut)}
<link rel="icon" href="{_FAVICON}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{d['site_adi']}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{tanim}">
<meta property="og:url" content="{SITE_URL}{tr_yol}">
<meta property="og:locale" content="{d['og_locale']}">
<script type="application/ld+json">{ld}</script>
<script>(function(){{try{{var t=localStorage.getItem('tema');if(t==='dark'||(!t&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){{document.documentElement.classList.add('dark');}}}}catch(e){{}}}})();</script>
<link rel="stylesheet" href="{kok}style.css">
</head>
<body>
<header class="topbar"><div class="inner">
<a class="brand" href="{kok}index.html">{d['site_adi']}</a>
<nav><a href="{kok}index.html">{d['nav_ana']}</a><a href="{kok}hisse/index.html">{d['nav_hisseler']}</a><a href="{kok}../index.html">{d['tr_git']}</a></nav>
<button type="button" class="theme-btn" id="tema-btn" onclick="temaDegistir()" title="Theme" aria-label="Theme">🌙</button>
</div></header>
<main class="wrap">
{icerik}
</main>
<script>
function temaDegistir() {{
  var r = document.documentElement;
  r.classList.toggle('dark');
  var koyu = r.classList.contains('dark');
  try {{ localStorage.setItem('tema', koyu ? 'dark' : 'light'); }} catch (e) {{}}
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = koyu ? '☀️' : '🌙';
}}
(function() {{
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = document.documentElement.classList.contains('dark') ? '☀️' : '🌙';
}})();
</script>
<footer class="footer">{d['footer']}<br>
<a href="{kok}../gizlilik.html" style="color:inherit">Gizlilik &amp; KVKK</a></footer>
</body></html>"""


def _tablo_html(satirlar, dil, kok_hisse):
    """BIST 30 teknik tablosunu dil etiketleriyle uretir (veri asla cevrilmez)."""
    d = DIL[dil]
    th = "".join(f"<th>{h}</th>" for h in d["th"])
    tr_html = []
    for s in satirlar:
        g = s.get("gunluk", 0) or 0
        d60 = s.get("deg60", 0) or 0
        g_cls = "pos" if g > 0 else "neg" if g < 0 else ""
        d60_cls = "pos" if d60 > 0 else "neg" if d60 < 0 else ""
        tr_html.append(
            f"<tr><td><a href='{kok_hisse}{s['hisse']}.html'><strong>{s['hisse']}</strong></a></td>"
            f"<td>{_sayi(s.get('son', 0) or 0, d['onluk'])}</td>"
            f"<td class='{g_cls}'>{_yuzde(g, d['onluk'])}</td>"
            f"<td class='{d60_cls}'>{_yuzde(d60, d['onluk'])}</td>"
            f"{_sinyal_hucre(s.get('genel', ''), dil)}</tr>")
    return (f"<table><thead><tr>{th}</tr></thead><tbody>"
            + "\n".join(tr_html) + "</tbody></table>")


def _rapor_cevir(report, dil, llm, log):
    """Gunluk rapor metnini LLM ile cevirir; basarisizsa None doner.
    Tablolar ve sayilar prompt ile degistirilmez diye kisitlanir."""
    try:
        sonuc = llm(_CEVIRI_PROMPT.format(ad=DIL[dil]["ad"], metin=report))
        if not sonuc or not sonuc.strip():
            return None
        # llm_call'in fallback'leri bazen aciklama ekleyebilir; rapor imzasi yoksa kabul etme
        if re.search(r"#{1,3} ", sonuc) is None:
            log(f"[CokDil] {dil}: ceviri markdown basligi icermiyor, atlandi.")
            return None
        return sonuc.strip()
    except Exception as e:
        log(f"[CokDil] {dil} ceviri hatasi: {e}")
        return None


def _mevcut_diller(tr_yol):
    """Bir Turkce sayfanin hangi dil kopyalari diskte var?"""
    return [d for d in DILLER if os.path.isfile(os.path.join(d, tr_yol))]


# ---------------------------------------------------------------------------
# Sayfa uretimleri
# ---------------------------------------------------------------------------
def _rapor_sayfasi_yaz(dil, date_str, report_md):
    d = DIL[dil]
    kok = "../"
    html_govde = markdown.markdown(report_md, extensions=["tables", "fenced_code"])
    tanim = d["rapor_tanim"].format(t=date_str)
    tr_yol = f"reports/{date_str}.html"
    mevcut = ["tr"] + _mevcut_diller(tr_yol)
    icerik = f"""
<div class="hero">
<h1>{d['rapor_baslik']} — {date_str}</h1>
<div class="meta"><span class="badge">{date_str}</span><span>{d['rapor_alt']}</span></div>
</div>
<article class="report">{html_govde}</article>
<p style="margin-top:18px"><a href="index.html">{d['geri']}</a></p>"""
    yol = f"{dil}/{tr_yol}"
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(_sayfa(dil, f"{d['rapor_baslik']} — {date_str} | {d['site_adi']}", tanim,
                       icerik, kok, tr_yol, mevcut))


def _hisse_index_yaz(dil, teknik_satirlar):
    if not teknik_satirlar:
        return
    d = DIL[dil]
    tr_yol = "hisse/index.html"
    mevcut = ["tr"] + _mevcut_diller(tr_yol)
    icerik = f"""
<div class="hero">
<h1>{d['hisse_baslik']}</h1>
<div class="meta"><span>{d['hisse_not']}</span></div>
</div>
<div class="card">{_tablo_html(teknik_satirlar, dil, "../../hisse/")}</div>"""
    yol = f"{dil}/{tr_yol}"
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(_sayfa(dil, f"{d['hisse_baslik']} | {d['site_adi']}", d["hisse_tanim"],
                       icerik, "", tr_yol, mevcut))


def _ana_sayfa_yaz(dil, teknik_satirlar):
    d = DIL[dil]
    os.makedirs(dil, exist_ok=True)
    # Son rapor arsivi: dilin kendi reports/ klasorunden okunur
    rapor_html = ""
    klasor = os.path.join(dil, "reports")
    if os.path.isdir(klasor):
        dosyalar = sorted((fn for fn in os.listdir(klasor) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", fn)),
                          reverse=True)[:20]
        if dosyalar:
            li = "\n".join(
                f"<li><a href='reports/{fn}'>{d['rapor_link'].format(t=fn[:-5])}</a></li>"
                for fn in dosyalar)
            rapor_html = f"<div class='card'><h2>{d['son_raporlar']}</h2><ul>{li}</ul></div>"
    # Gunluk snapshot: en iyi 3 artan / 3 dusen (saf veri, ceviri gerekmez)
    snapshot = ""
    if teknik_satirlar:
        sirali = sorted(teknik_satirlar, key=lambda s: (s.get("gunluk", 0) or 0), reverse=True)
        ust = sirali[:3]
        alt = list(reversed(sirali[-3:]))

        def _satir(l, liste):
            h = "".join(
                f"<li>{s['hisse']} <span class='{'pos' if (s.get('gunluk') or 0) > 0 else 'neg'}'>"
                f"{_yuzde(s.get('gunluk', 0) or 0, d['onluk'])}</span></li>" for s in liste)
            return f"<div style='flex:1'><strong>{l}</strong><ul>{h}</ul></div>"
        snapshot = (f"<div class='card'><h2>{d['paket_basligi']}</h2>"
                    f"<div style='display:flex;gap:30px;flex-wrap:wrap'>"
                    f"{_satir(d['yukselenler'], ust)}{_satir(d['dusenler'], alt)}"
                    f"</div><p style='margin-top:12px'><a href='hisse/index.html'>{d['hisse_git']}</a></p></div>")
    icerik = f"""
<div class="hero">
<h1>{d['hero_baslik']}</h1>
<p>{d['hero_alt']}</p>
</div>
{snapshot}
{rapor_html}"""
    with open(f"{dil}/index.html", "w", encoding="utf-8") as f:
        f.write(_sayfa(dil, d["ana_baslik"], d["ana_tanim"], icerik, "", "index.html", DILLER))


# ---------------------------------------------------------------------------
# Giris noktalari (bot.py'den cagrilir)
# ---------------------------------------------------------------------------
def uret(report, date_str, teknik_satirlar, llm=None, log=None):
    """Gunluk pipeline'in sonunda cagrilir. Hicbir sekilde exception yukseltmez;
    Turkce uretim bu noktada zaten tamamlanmis olur."""
    log = log or (lambda *a, **k: None)
    try:
        cevrilmis = {}
        for dil in DILLER:
            if report and llm:
                metin = _rapor_cevir(report, dil, llm, log)
                if metin:
                    cevrilmis[dil] = metin
        for dil, metin in cevrilmis.items():
            try:
                _rapor_sayfasi_yaz(dil, date_str, metin)
            except Exception as e:
                log(f"[CokDil] {dil} rapor sayfasi: {e}")
        for dil in DILLER:
            try:
                _hisse_index_yaz(dil, teknik_satirlar)
            except Exception as e:
                log(f"[CokDil] {dil} hisse sayfasi: {e}")
            try:
                _ana_sayfa_yaz(dil, teknik_satirlar)
            except Exception as e:
                log(f"[CokDil] {dil} ana sayfa: {e}")
        print(f"[CokDil] {len(cevrilmis)} dilde rapor + landing/hisse sayfalari islendi.", flush=True)
    except Exception as e:
        log(f"[CokDil] uretim hatasi: {e}")


def sitemap_satirlari():
    """Dil sayfalarindan sitemap <url> satirlari uretir; bot.py'nin sitemap
    fonksiyonuna eklenir. Dil klasoru yoksa bos doner (bot.py etkilenmez)."""
    satirlar = []
    for dil in DILLER:
        if not os.path.isdir(dil):
            continue
        # Landing
        if os.path.isfile(os.path.join(dil, "index.html")):
            satirlar.append(f"  <url><loc>{SITE_URL}{dil}/index.html</loc>"
                            f"<changefreq>daily</changefreq><priority>0.8</priority></url>")
        # Gunluk rapor arsivi
        klasor = os.path.join(dil, "reports")
        if os.path.isdir(klasor):
            for fn in sorted(os.listdir(klasor)):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", fn):
                    satirlar.append(
                        f"  <url><loc>{SITE_URL}{dil}/reports/{fn}</loc>"
                        f"<lastmod>{fn[:-5]}</lastmod>"
                        f"<changefreq>monthly</changefreq><priority>0.6</priority></url>")
        # Hisse listesi
        if os.path.isfile(os.path.join(dil, "hisse", "index.html")):
            satirlar.append(f"  <url><loc>{SITE_URL}{dil}/hisse/index.html</loc>"
                            f"<changefreq>daily</changefreq><priority>0.8</priority></url>")
    return satirlar
