#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
borsa-raporlari · Muhasebe Terimleri Sozlugu — DIL SAYFALARI
============================================================

Neden ayri bir modul?
---------------------
`bot.py` gunluk kosuda `muhasebe-terimleri.html` (TR, 5 dilli tanimli kartlar)
uretir; bu sayfa `terimler.html` gibi TR canonical kalir ve i18n makine
cevirisi kapsamina alinmaz (`i18n.py` GENERASYON_HARIC).

Buna ek olarak her dil icin AYRI ve o dile ait veriyi tasiyan sayfalar gerekir:

  /en/muhasebe-terimleri   Ingilizce terim + tanim + ornek
  /de/muhasebe-terimleri   Ingilizce terim + Almanca karsilik/tanim/ornek
  /ru/muhasebe-terimleri   Rusca terim + tanim + ornek
  /zh/muhasebe-terimleri   Cince terim + tanim + ornek

Bu sayfalar makine cevirisi DEGIL: her biri kaynak veri setindeki ilgili dil
sutunundan uretilir. 1691 terim/dil, alfabetik bolumler + tarayici ici arama.

Kaynak veri (data/terimler/):
  muhasebe-terimleri.csv  TR  (Bolum, Ingilizce_Terim, Turkce_Anlami, Ingilizce_Tanim,
                               Ornek_Ingilizce, Ornek_Turkce)
  muhasebe-en.csv         EN  (section, term, definition, example)
  muhasebe-de.csv         DE  (Bolum, Ingilizce_Terim, Almanca_Anlami, Almanca_Tanim,
                               Ornek_Almanca)
  muhasebe-zh.csv         ZH  (术语, 定义, 例句)
  muhasebe-ru.jsonl       RU  ({i, term, def, ex})

Kullanim:
  python muhasebe_diller.py          # tek basina tum dil sayfalarini yazar
  bot.py icinden: muhasebe_diller.yaz()   (terimler_yaz sonunda cagrilir)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SITE_URL = "https://borsa-raporlari.pages.dev"
SAYFA = "muhasebe-terimleri"
VERI_DIZIN = os.path.join("data", "terimler")

# --------------------------------------------------------------------------
# DIL YAPILANDIRMASI
# --------------------------------------------------------------------------

DIL_CFG = {
    "en": {
        "html_lang": "en", "hreflang": "en", "locale": "en_US", "ad": "English",
        "site_name": "BIST 30 Daily Reports", "brand": "BIST 30 Daily Reports",
        "title": "Accounting Terms Glossary (English)",
        "desc": ("IFRS and accounting terminology: 1691 English terms with definitions and "
                 "usage examples, grouped by letter with instant search."),
        "h1": "Accounting Terms Glossary",
        "lede": ("International financial reporting and accounting terminology — 1691 English "
                 "terms with definitions and real usage examples. Search across the term, "
                 "definition or example instantly."),
        "ara_ph": "Search term, definition or example…",
        "terim_birim": "terms", "bolum_birim": "sections",
        "ornek_label": "Example",
        "tema_title": "Light/dark theme", "tema_aria": "Change theme",
        "dil_aria": "Select page language", "harf_aria": "Jump to letter section",
        "yok": "No term matched your search.",
        "kaynak": "Term list source: muhasebenews.com accounting dictionary (1691 terms).",
        "nav_terimler": "Terms", "nav_muhasebe": "Accounting",
        "footer": ("The information, comments and recommendations contained herein are for "
                   "informational purposes only; they do not constitute investment advisory or "
                   "investment advice. The accuracy of the data cannot be guaranteed and the "
                   "user is responsible for any decisions that may arise from the content.<br>"
                   "Data sources: İş Yatırım, RSS news feeds &bull; Analysis: artificial "
                   "intelligence (multi-agent system)<br>"
                   "<a href=\"gizlilik.html\" style=\"color:inherit\">Privacy &amp; Data "
                   "Protection</a>"),
        "lower": "en",
    },
    "de": {
        "html_lang": "de", "hreflang": "de", "locale": "de_DE", "ad": "Deutsch",
        "site_name": "BIST 30 Tagesberichte", "brand": "BIST 30 Tagesberichte",
        "title": "Buchhaltungslexikon (Englisch – Deutsch)",
        "desc": ("IFRS- und Rechnungswesen-Terminologie: 1691 englische Begriffe mit deutschen "
                 "Entsprechungen, Definitionen und Anwendungsbeispielen — alphabetisch, mit "
                 "Sofortsuche."),
        "h1": "Buchhaltungslexikon",
        "lede": ("Terminologie der internationalen Rechnungslegung — englische Begriffe mit "
                 "deutscher Entsprechung, Definition und Anwendungsbeispiel. Sofortige Suche "
                 "in Begriff, Definition oder Beispiel."),
        "ara_ph": "Begriff, Definition oder Beispiel suchen…",
        "terim_birim": "Begriffe", "bolum_birim": "Abschnitte",
        "ornek_label": "Beispiel",
        "tema_title": "Helles/dunkles Design", "tema_aria": "Design wechseln",
        "dil_aria": "Seitensprache wählen", "harf_aria": "Zum Buchstabenabschnitt springen",
        "yok": "Kein Begriff entspricht Ihrer Suche.",
        "kaynak": "Quelle der Begriffsliste: muhasebenews.com (1691 Begriffe).",
        "nav_terimler": "Begriffe", "nav_muhasebe": "Rechnungswesen",
        "footer": ("Die hier enthaltenen Informationen, Kommentare und Empfehlungen dienen zu "
                   "Informationszwecken; sie stellen keine Anlageberatung dar und sind keine "
                   "Anlageempfehlungen. Die Genauigkeit der Daten kann nicht garantiert werden, "
                   "und die Verantwortung für jede Art von Entscheidung, die aus dem Inhalt "
                   "entstehen könnte, liegt beim Nutzer.<br>Datenquellen: İş Yatırım, "
                   "RSS-Nachrichtenströme &bull; Analyse: künstliche Intelligenz "
                   "(Multi-Agenten-System)<br>"
                   "<a href=\"gizlilik.html\" style=\"color:inherit\">Datenschutz</a>"),
        "lower": "de",
    },
    "ru": {
        "html_lang": "ru", "hreflang": "ru", "locale": "ru_RU", "ad": "Русский",
        "site_name": "Ежедневные отчёты BIST 30", "brand": "Ежедневные отчёты BIST 30",
        "title": "Словарь бухгалтерских терминов (на русском)",
        "desc": ("Терминология МСФО и бухгалтерского учёта: 1691 русский термин с "
                 "определениями и примерами употребления, по разделам алфавита, с "
                 "мгновенным поиском."),
        "h1": "Словарь бухгалтерских терминов",
        "lede": ("Терминология международной финансовой отчётности и бухгалтерского учёта — "
                 "1691 термин с определениями и примерами употребления. Мгновенный поиск по "
                 "термину, определению или примеру."),
        "ara_ph": "Поиск термина, определения или примера…",
        "terim_birim": "терминов", "bolum_birim": "разделов",
        "ornek_label": "Пример",
        "tema_title": "Светлая/тёмная тема", "tema_aria": "Сменить тему",
        "dil_aria": "Выбрать язык страницы", "harf_aria": "Перейти к разделу алфавита",
        "yok": "По вашему запросу терминов не найдено.",
        "kaynak": "Источник списка терминов: muhasebenews.com (1691 термин).",
        "nav_terimler": "Термины", "nav_muhasebe": "Бухучёт",
        "footer": ("Приведённые здесь информация, комментарии и рекомендации носят "
                   "информационный характер; они не являются инвестиционным консультированием "
                   "и не являются инвестиционной рекомендацией. Точность данных не может быть "
                   "гарантирована, и ответственность за любые решения, вытекающие из "
                   "содержания, лежит на пользователе.<br>Источники данных: İş Yatırım, "
                   "RSS-ленты новостей &bull; Анализ: искусственный интеллект (мультиагентная "
                   "система)<br>"
                   "<a href=\"gizlilik.html\" style=\"color:inherit\">Конфиденциальность</a>"),
        "lower": "ru",
    },
    "zh": {
        "html_lang": "zh-Hans", "hreflang": "zh-Hans", "locale": "zh_CN", "ad": "简体中文",
        "site_name": "BIST 30 每日报告", "brand": "BIST 30 每日报告",
        "title": "会计术语词典（中文）",
        "desc": "国际财务报告（IFRS）与会计术语：1691 条中文术语，含定义与例句，支持即时搜索。",
        "h1": "会计术语词典",
        "lede": "国际财务报告与会计术语 —— 1691 条术语，含定义与例句。可在术语、定义或例句中即时搜索。",
        "ara_ph": "搜索术语、定义或例句…",
        "terim_birim": "条术语", "bolum_birim": "个分组",
        "ornek_label": "例句",
        "tema_title": "浅色/深色主题", "tema_aria": "切换主题",
        "dil_aria": "选择页面语言", "harf_aria": "跳转到字母分组",
        "yok": "没有找到与您的搜索匹配的术语。",
        "kaynak": "术语来源：muhasebenews.com（1691 条术语）。",
        "nav_terimler": "术语", "nav_muhasebe": "会计",
        "footer": ("此处所含的信息、评论和建议仅供参考；不属于投资顾问服务，不构成投资建议。无法保证数据的准确性，"
                   "因内容而产生的任何决定，责任均由用户自行承担。<br>数据来源：İş Yatırım、RSS 新闻源 "
                   "&bull; 分析：人工智能（多智能体系统）<br>"
                   "<a href=\"gizlilik.html\" style=\"color:inherit\">隐私与数据保护</a>"),
        "lower": "zh",
    },
}

# Ust menu: (href, tr, en, de, ru, zh) — site menusuyle ayni duzen + Terimler + Muhasebe
MENU = [
    ("index.html",           "Raporlar",         "Reports",          "Berichte",               "Отчёты",             "报告"),
    ("hisse/index.html",     "Hisseler",         "Stocks",           "Aktien",                 "Акции",              "股票"),
    ("derin-analiz.html",    "Derin Analiz",     "Deep Analysis",    "Tiefenanalyse",          "Глубокий анализ",    "深度分析"),
    ("teknik-analiz.html",   "Teknik Tarama",    "Technical Scan",   "Technischer Scan",       "Технический анализ", "技术扫描"),
    ("sinyal-karnesi.html",  "Sinyal Karnesi",   "Signal Scorecard", "Signal-Bilanz",          "Сводка сигналов",    "信号记分卡"),
    ("borsapy-analiz.html",  "Borsapy Sinyal",   "Borsapy Signals",  "Borsapy-Signale",        "Сигналы Borsapy",    "Borsapy 信号"),
    ("haberler.html",        "Haberler",         "News",             "Nachrichten",            "Новости",            "新闻"),
    ("sirket-haberleri.html","Şirket Haberleri", "Company News",     "Unternehmensnachrichten","Новости компаний",   "公司新闻"),
    ("portfolio.html",       "Deneme Portföyü",  "Demo Portfolio",   "Demo-Portfolio",         "Демо-портфель",      "模拟投资组合"),
    ("haftasonu.html",       "Hafta Sonu",       "Weekend",          "Wochenende",             "Выходные",           "周末"),
    ("haftasonu-egitimi.html","Borsa Okulu",     "Market School",    "Börsenschule",           "Школа рынка",        "股市学堂"),
    ("takvim.html",          "📅 Takvim",        "📅 Calendar",      "📅 Kalender",            "📅 Календарь",       "📅 日历"),
    ("sozluk.html",          "Sözlük",           "Glossary",         "Glossar",                "Глоссарий",          "术语表"),
    ("terimler.html",        "Terimler",         "Terms",            "Begriffe",               "Термины",            "术语"),
]
MENU_IDX = {"tr": 0, "en": 1, "de": 2, "ru": 3, "zh": 4}
DILLER = ["tr", "en", "de", "ru", "zh"]
DIL_AD = {"tr": "Türkçe", "en": "English", "de": "Deutsch", "ru": "Русский", "zh": "简体中文"}
DIL_HREFLANG = {"tr": "tr", "en": "en", "de": "de", "ru": "ru", "zh": "zh-Hans"}

# --------------------------------------------------------------------------
# VERI OKUMA
# --------------------------------------------------------------------------


def _yol(ad):
    return os.path.join(VERI_DIZIN, ad)


def _temizle(s):
    return re.sub(r"\s+", " ", (s or "").strip())


# Goruntulemede duzeltilen kaynak yazim hatalari (bot.py ile ayni liste)
YAZIM_DUZELT = [("A mounts", "Amounts"), ("orbusinesses", "or businesses"),
                (" ıssues", " issues"), ("ıncome", "income")]


def _duzelt(m):
    for eski, yeni in YAZIM_DUZELT:
        m = m.replace(eski, yeni)
    return m


def veri_en():
    out = []
    with open(_yol("muhasebe-en.csv"), encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            t = _temizle(r.get("term"))
            if not t:
                continue
            out.append({"sec": (r.get("section") or "").strip(),
                        "term": _duzelt(t), "mean": "",
                        "def": _temizle(r.get("definition")),
                        "ex": _temizle(r.get("example")), "ex2": ""})
    return out


def veri_de():
    out = []
    with open(_yol("muhasebe-de.csv"), encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            t = _temizle(r.get("Ingilizce_Terim"))
            if not t:
                continue
            out.append({"sec": (r.get("Bolum") or "").strip(),
                        "term": _duzelt(t), "mean": _temizle(r.get("Almanca_Anlami")),
                        "def": _temizle(r.get("Almanca_Tanim")),
                        "ex": _temizle(r.get("Ornek_Almanca")), "ex2": ""})
    return out


def veri_zh():
    out = []
    with open(_yol("muhasebe-zh.csv"), encoding="utf-8-sig", newline="") as f:
        okuyucu = csv.reader(f)
        basliklar = next(okuyucu, None)
        for r in okuyucu:
            if len(r) < 3 or not r[0].strip():
                continue
            out.append({"sec": "", "term": _duzelt(_temizle(r[0])), "mean": "",
                        "def": _temizle(r[1]), "ex": _temizle(r[2]), "ex2": ""})
    return out


def veri_ru():
    kayitlar = {}
    with open(_yol("muhasebe-ru.jsonl"), encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                r = json.loads(satir)
            except json.JSONDecodeError:
                continue
            term = _temizle(r.get("term"))
            if not term:
                continue
            kayitlar[int(r.get("i", len(kayitlar)))] = {
                "sec": "", "term": _duzelt(term), "mean": "",
                "def": _temizle(r.get("def")), "ex": _temizle(r.get("ex")), "ex2": ""}
    return [kayitlar[i] for i in sorted(kayitlar)]


VERI_FONK = {"en": veri_en, "de": veri_de, "ru": veri_ru, "zh": veri_zh}

# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def slug(s):
    s = str(s).lower()
    s = re.sub(r"[^0-9a-zа-яёçğıöşü\u4e00-\u9fff]+", "-", s)
    return s.strip("-")[:60] or "terim"


DIL_STIL = ("<style>.dil-linkler{display:flex;gap:10px;align-items:center;font-size:12.5px;"
            "flex-wrap:wrap}.dil-linkler a{color:var(--muted);text-decoration:none;"
            "border-bottom:1px solid transparent}.dil-linkler a:hover{color:var(--accent);"
            "border-bottom-color:var(--accent)}.dil-linkler a.aktif{color:var(--accent);"
            "font-weight:600}</style>")

SAYFA_CSS = """
.sozluk-arac{position:sticky;top:0;z-index:25;background:var(--bg);padding:12px 0 10px;margin:0 0 6px;
  display:flex;gap:12px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.sozluk-arac input{flex:1;min-width:220px;padding:11px 14px;border:1px solid var(--line);border-radius:10px;
  background:var(--card);color:var(--ink);font-size:14.5px}
.sozluk-arac input:focus{outline:2px solid var(--accent);outline-offset:1px}
#sayac{color:var(--muted);font-size:13px;white-space:nowrap;font-variant-numeric:tabular-nums}
.harf-bar{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 20px}
.harf-bar a{display:inline-block;min-width:32px;text-align:center;padding:4px 9px;border:1px solid var(--line);
  border-radius:8px;background:var(--card);color:var(--muted);text-decoration:none;font-size:12.5px;font-weight:700}
.harf-bar a:hover{border-color:var(--accent);color:var(--accent)}
.harf-bolum{margin:0 0 4px}
.harf-baslik{font-size:14px;margin:26px 0 10px;padding:2px 0 2px 10px;border-left:4px solid var(--accent);
  color:var(--muted);text-transform:uppercase;letter-spacing:.1em;scroll-margin-top:70px}
.terim{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:0 0 8px}
.terim:target{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg)}
.terim-ad{margin:0;font-size:15.5px;font-weight:700;line-height:1.45}
.terim-anlam{display:block;font-weight:600;color:var(--accent);font-size:13.5px;margin-top:3px}
.terim-tan{margin:7px 0 0;font-size:14px}
.terim-ornek{margin:6px 0 0;font-size:13px;color:var(--muted)}
.terim-ornek b{color:var(--ink);font-weight:600}
.sozluk-ozet{color:var(--muted);font-size:13px;margin:0 0 10px}
#bos{display:none;color:var(--muted);font-size:14px;padding:14px 0}
.terim, .terim-ornek, .harf-bolum{-webkit-user-select:none; -moz-user-select:none; user-select:none;}
@media (max-width:640px){.harf-bar a{min-width:28px;padding:3px 6px;font-size:12px}}
"""

KORUMA_JS = """
<script>
document.addEventListener('contextmenu', function(e) {
  if (e.target.closest && e.target.closest('.terim')) e.preventDefault();
});
document.addEventListener('copy', function(e) {
  var s = (window.getSelection ? String(window.getSelection()) : '') || '';
  if (s.length > 60 && e.clipboardData) {
    e.clipboardData.setData('text/plain', s +
      '\\n\\nKaynak: BIST 30 Gunluk Raporlar - Muhasebe Terimleri\\n' + location.href);
    e.preventDefault();
  }
});
</script>"""

ISKELET = """<!DOCTYPE html>
<html lang="@@HTML_LANG@@">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>@@TITLE@@</title>
<meta name="description" content="@@DESC@@">
<link rel="canonical" href="@@CANONICAL@@">
@@ALTERNATES@@
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='18' fill='%230f172a'/%3E%3Ctext y='.9em' x='12' font-size='72'%3E%F0%9F%93%88%3C/text%3E%3C/svg%3E">
<meta property="og:type" content="website">
<meta property="og:site_name" content="@@SITE_NAME@@">
<meta property="og:title" content="@@TITLE@@">
<meta property="og:description" content="@@DESC@@">
<meta property="og:url" content="@@CANONICAL@@">
<meta property="og:image" content="@@SITE@@/og-cover.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="@@SITE_NAME@@">
<meta property="og:locale" content="@@LOCALE@@">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="@@TITLE@@">
<meta name="twitter:description" content="@@DESC@@">
<meta name="twitter:image" content="@@SITE@@/og-cover.png">
<script type="application/ld+json">@@JSONLD@@</script>
<script>(function(){try{var t=localStorage.getItem('tema');if(t==='dark'||(!t&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.classList.add('dark');}}catch(e){}})();</script>
<link rel="stylesheet" href="style.css">
<style>@@CSS@@</style>
</head>
<body>
<header class="topbar"><div class="inner">
<div class="brand-row"><a class="brand" href="index.html">@@BRAND@@</a>
<button type="button" class="theme-btn" id="tema-btn" onclick="temaDegistir()" title="@@TEMA_TITLE@@" aria-label="@@TEMA_ARIA@@">🌙</button></div>
<nav>@@NAV@@</nav>
</div></header>

<main class="wrap">
    <div class="site-widgets">
@@DILBLOK@@
    </div>

<div class="hero">
<h1>@@H1@@</h1>
<p>@@LEDE@@</p>
</div>

<div class="stats">
<div class="stat"><div class="label">@@TERIM_LABEL@@</div><div class="value">@@SAYI@@</div></div>
<div class="stat"><div class="label">@@BOLUM_LABEL@@</div><div class="value">@@BOLUM_SAYI@@</div></div>
</div>

<div class="sozluk-arac">
<input type="text" id="terim-ara" placeholder="@@ARA_PH@@" autocomplete="off" aria-label="@@ARA_PH@@">
<span id="sayac"></span>
</div>
@@HARF@@
<div class="sozluk-ozet">@@KAYNAK@@</div>
<div id="liste">
@@LISTE@@
</div>
<div id="bos">@@YOK@@</div>
</main>

<footer class="footer">@@FOOTER@@</footer>

<script>
function temaDegistir() {
  var kok = document.documentElement;
  kok.classList.toggle('dark');
  var koyu = kok.classList.contains('dark');
  try { localStorage.setItem('tema', koyu ? 'dark' : 'light'); } catch (e) {}
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = koyu ? '☀️' : '🌙';
}
(function() {
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = document.documentElement.classList.contains('dark') ? '☀️' : '🌙';
})();

(function() {
  var DIL = "@@LOWER@@";
  var TOPLAM = @@SAYI@@;
  var hepsi = Array.prototype.slice.call(document.querySelectorAll('.terim'));
  var bolumler = Array.prototype.slice.call(document.querySelectorAll('.harf-bolum'));
  var giris = document.getElementById('terim-ara');
  var sayac = document.getElementById('sayac');
  var bos = document.getElementById('bos');
  var terimAd = "@@TERIM_BIRIM@@";

  var harita = { 'ı':'i','İ':'i','I':'i','ğ':'g','Ğ':'g','ü':'u','Ü':'u','ş':'s','Ş':'s',
                 'ö':'o','Ö':'o','ç':'c','Ç':'c','â':'a','î':'i','û':'u',
                 'ё':'е','й':'и','ъ':'','ь':'' };
  function sadeles(s) {
    s = String(s);
    try { s = s.toLocaleLowerCase(DIL === 'tr' ? 'tr-TR' : DIL); }
    catch (e) { s = s.toLowerCase(); }
    return s.replace(/[ıİIğĞüÜşŞöÖçÇâîûёйъь]/g, function(h) { return harita[h] || h; });
  }
  var anahtar = hepsi.map(function(el) { return sadeles(el.textContent); });

  function yaz() {
    var q = sadeles(giris.value.trim());
    var gorunen = 0;
    for (var i = 0; i < hepsi.length; i++) {
      var ok = !q || anahtar[i].indexOf(q) !== -1;
      hepsi[i].style.display = ok ? '' : 'none';
      if (ok) gorunen++;
    }
    bolumler.forEach(function(sec) {
      var gorunur = false;
      var cocuklar = sec.querySelectorAll('.terim');
      for (var j = 0; j < cocuklar.length; j++) {
        if (cocuklar[j].style.display !== 'none') { gorunur = true; break; }
      }
      sec.style.display = gorunur ? '' : 'none';
    });
    bos.style.display = gorunen ? 'none' : 'block';
    sayac.textContent = (q ? gorunen + ' / ' + TOPLAM : TOPLAM) + ' ' + terimAd;
  }
  var zaman = null;
  giris.addEventListener('input', function() {
    if (zaman) clearTimeout(zaman);
    zaman = setTimeout(yaz, 90);
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === '/' && document.activeElement !== giris) { e.preventDefault(); giris.focus(); }
  });
  yaz();
})();
</script>
@@KORUMA@@
</body></html>
"""


def nav_html(dil):
    idx = MENU_IDX[dil]
    parca = ['<a href="%s">%s</a>' % (href, esc(adlar[idx])) for href, *adlar in MENU]
    parca.append('<a href="%s.html" class="active">%s</a>'
                 % (SAYFA, esc(DIL_CFG[dil]["nav_muhasebe"])))
    return "".join(parca)


def dil_blok(dil):
    parca = [DIL_STIL, '<nav class="dil-linkler" aria-label="%s">' % esc(DIL_CFG[dil]["dil_aria"]),
             '<span aria-hidden="true">🌐</span>']
    for d in DILLER:
        yol = ("/%s" % SAYFA) if d == "tr" else ("/%s/%s" % (d, SAYFA))
        aktif = ' class="aktif" aria-current="true"' if d == dil else ""
        parca.append('<a href="%s%s" hreflang="%s" lang="%s" data-dil="%s"%s>%s</a>'
                     % (SITE_URL, yol, DIL_HREFLANG[d], DIL_HREFLANG[d], d, aktif,
                        esc(DIL_AD[d])))
    parca.append("</nav>")
    return "\n".join(parca)


def terim_html(t, idx, dil):
    parca = ['<div class="terim" id="tm-%d" data-slug="%s">' % (idx, esc(slug(t["term"])))]
    parca.append("<h3 class='terim-ad'>%s" % esc(t["term"]))
    if t.get("mean"):
        parca.append("<span class='terim-anlam'>%s</span>" % esc(t["mean"]))
    parca.append("</h3>")
    if t.get("def"):
        parca.append("<p class='terim-tan'>%s</p>" % esc(t["def"]))
    if t.get("ex"):
        parca.append("<p class='terim-ornek'><b>%s:</b> %s</p>"
                     % (esc(DIL_CFG[dil]["ornek_label"]), esc(t["ex"])))
    parca.append("</div>")
    return "".join(parca)


def _sirala_ru(terimler):
    """Rusca terimleri Kiril alfabesine gore grupla + sirala (veri EN terime gore hizali).
    Basdaki tirnak/isaret (ör. «...») siralama ve gruplamada yok sayilir."""
    def anahtar(t):
        return re.sub(r"^[^0-9a-zа-яё]+", "", t["term"].casefold())
    sirali = sorted(terimler, key=anahtar)
    bolumler = []
    for t in sirali:
        sade = anahtar(t)
        harf = (sade[:1] or "#").upper()
        if not bolumler or bolumler[-1][0] != harf:
            bolumler.append((harf, []))
        bolumler[-1][1].append(t)
    return bolumler


def _bolumle(terimler, dil):
    """(bolum_harfi, [terimler]) listesi uretir."""
    if dil == "ru":
        return _sirala_ru(terimler)
    if dil == "zh":
        return [("", terimler)]
    bolumler = []
    for t in terimler:
        harf = (t.get("sec") or "").strip()
        if not bolumler or bolumler[-1][0] != harf:
            bolumler.append((harf, []))
        bolumler[-1][1].append(t)
    return bolumler


def sayfa_uret(dil, terimler):
    cfg = DIL_CFG[dil]
    canonical = SITE_URL + ("/%s" % SAYFA if dil == "tr" else "/%s/%s" % (dil, SAYFA))
    alt = []
    for d in DILLER:
        yol = ("/%s" % SAYFA) if d == "tr" else ("/%s/%s" % (d, SAYFA))
        alt.append('<link rel="alternate" hreflang="%s" href="%s%s">'
                   % (DIL_HREFLANG[d], SITE_URL, yol))
    alt.append('<link rel="alternate" hreflang="x-default" href="%s/%s">' % (SITE_URL, SAYFA))

    jsonld = json.dumps({
        "@context": "https://schema.org", "@type": "DefinedTermSet",
        "name": cfg["title"], "description": cfg["desc"], "url": canonical,
        "inLanguage": cfg["hreflang"],
        "isPartOf": {"@type": "WebSite", "name": cfg["site_name"], "url": SITE_URL + "/"},
        "numberOfItems": len(terimler),
    }, ensure_ascii=False)

    bolumler = _bolumle(terimler, dil)
    idx = 0
    govde = []
    for harf, grup in bolumler:
        govde.append('<section class="harf-bolum" data-bolum="%s">' % esc(harf))
        if harf:
            govde.append('<h2 class="harf-baslik" id="harf-%s">%s</h2>'
                         % (esc(slug(harf)), esc(harf)))
        for t in grup:
            idx += 1
            govde.append(terim_html(t, idx, dil))
        govde.append("</section>")
    liste = "".join(govde)

    if any(h for h, _ in bolumler):
        harfbar = ('<nav class="harf-bar" aria-label="%s">%s</nav>'
                   % (esc(cfg["harf_aria"]),
                      "".join('<a href="#harf-%s">%s</a>' % (esc(slug(h)), esc(h))
                              for h, _ in bolumler if h)))
    else:
        harfbar = ""

    bolum_sayi = len([h for h, _ in bolumler if h]) or 1

    yer = {
        "@@HTML_LANG@@": cfg["html_lang"], "@@TITLE@@": esc(cfg["title"]),
        "@@DESC@@": esc(cfg["desc"]), "@@CANONICAL@@": canonical,
        "@@ALTERNATES@@": "\n".join(alt), "@@SITE_NAME@@": esc(cfg["site_name"]),
        "@@SITE@@": SITE_URL, "@@LOCALE@@": cfg["locale"], "@@JSONLD@@": jsonld,
        "@@CSS@@": SAYFA_CSS, "@@BRAND@@": esc(cfg["brand"]),
        "@@TEMA_TITLE@@": esc(cfg["tema_title"]), "@@TEMA_ARIA@@": esc(cfg["tema_aria"]),
        "@@NAV@@": nav_html(dil), "@@DILBLOK@@": dil_blok(dil),
        "@@H1@@": esc(cfg["h1"]), "@@LEDE@@": esc(cfg["lede"]),
        "@@TERIM_LABEL@@": esc(cfg["terim_birim"]),
        "@@BOLUM_LABEL@@": esc(cfg["bolum_birim"]),
        "@@SAYI@@": str(len(terimler)), "@@BOLUM_SAYI@@": str(bolum_sayi),
        "@@ARA_PH@@": esc(cfg["ara_ph"]), "@@HARF@@": harfbar,
        "@@KAYNAK@@": esc(cfg["kaynak"]), "@@LISTE@@": liste,
        "@@YOK@@": esc(cfg["yok"]), "@@FOOTER@@": cfg["footer"],
        "@@LOWER@@": cfg["lower"], "@@TERIM_BIRIM@@": cfg["terim_birim"],
        "@@KORUMA@@": KORUMA_JS,
    }
    html = ISKELET
    for k, v in yer.items():
        html = html.replace(k, v)
    return html


def _yaz(yol, icerik):
    """Degismediyse yazmaz (gunluk commit gurultusu olusmasin)."""
    try:
        with open(yol, encoding="utf-8") as f:
            if f.read() == icerik:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    with open(yol, "w", encoding="utf-8", newline="\n") as f:
        f.write(icerik)
    return True


def yaz(kok="."):
    """en/de/ru/zh muhasebe terimleri sayfalarini yazar. Donus: yazilan yollar."""
    yazilan = []
    for dil in ("en", "de", "ru", "zh"):
        yol = os.path.join(VERI_DIZIN, "muhasebe-%s.%s" % (dil, "jsonl" if dil == "ru" else "csv"))
        if not os.path.exists(os.path.join(kok, yol)):
            print("[muhasebe_diller] kaynak yok, atlandi: %s" % yol, file=sys.stderr)
            continue
        terimler = VERI_FONK[dil]()
        if not terimler:
            print("[muhasebe_diller] veri bos, atlandi: %s" % dil, file=sys.stderr)
            continue
        html = sayfa_uret(dil, terimler)
        hedef = os.path.join(kok, dil, SAYFA + ".html")
        degisti = _yaz(hedef, html)
        yazilan.append((os.path.join(dil, SAYFA + ".html"), len(terimler), degisti))
        print("[muhasebe_diller] %-3s %5d terim -> %-30s %s"
              % (dil, len(terimler), os.path.join(dil, SAYFA + ".html"),
                 "yazildi" if degisti else "degismedi"))
    return yazilan


def _veri_dizini_ayarla(kok):
    global VERI_DIZIN
    VERI_DIZIN = os.path.join(kok, "data", "terimler")


if __name__ == "__main__":
    kok = os.path.abspath(os.path.dirname(__file__))
    _veri_dizini_ayarla(kok)
    yaz(kok)
