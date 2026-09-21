#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
borsa-raporlari · cok dilli katman (i18n)
=========================================

Amac: Turkce siteyi HIC DEGISTIRMEDEN /en/ /de/ /ru/ /zh/ alt dizinlerinde
gercek (statik, indekslenebilir, hreflang'li) dil sayfalari uretmek.

TASARIM KURALI
--------------
Bu betik Turkce sayfalari ASLA yazmaz. Yalnizca {dil}/... altina yeni dosya uretir.
Calismanin basinda ve sonunda tum Turkce .html dosyalarinin sha256'si alinir;
degisiklik varsa calisma HATA ile durur (--zorla ile gecilebilir).

KULLANIM
--------
  # 1) Yapisal pilot (ceviri motoru yok, mekanik isaret koyar):
  python i18n.py --diller en --sayfalar index.html --provider mock

  # 2) Gercek uretim (mevcut LLM anahtarlariyla):
  python i18n.py --diller en,de,ru,zh --provider llm

  # 3) DeepL Free (500.000 karakter/ay):
  python i18n.py --diller en,de,ru,zh --provider deepl

  # 4) Uretilenleri dogrula (kirik ic baglanti + hreflang simetrisi):
  python i18n.py --diller en,de,ru,zh --dogrula

  # 5) Sitemap'e dil alternatiflerini ekle (opt-in, Turkce kayitlar korunur):
  python i18n.py --diller en,de,ru,zh --sitemap

  # 6) Uretilen dil dizinlerini sil:
  python i18n.py --temizle

ANAHTARLAR (env)
----------------
  OPENROUTER_API_KEY | GROQ_API_KEY  -> --provider llm
  DEEPL_API_KEY                      -> --provider deepl  (free: <key>:fx)
"""

from __future__ import annotations

import argparse
import hashlib
import json

try:  # istege bagli: bozuk JSON'lari kurtarir (pip install json-repair)
    from json_repair import repair_json as _json_repair_kurtar
except Exception:  # kurulu degilse kendi toleransli ayristiricimiz devrede
    _json_repair_kurtar = None
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# ----------------------------------------------------------------------------
# 1) DIL TANIMLARI
# ----------------------------------------------------------------------------

SITE_URL = "https://borsa-raporlari.pages.dev/"

DILLER = {
    "tr": {"lang": "tr", "locale": "tr_TR", "hreflang": "tr", "ad": "Türkçe"},
    "en": {"lang": "en", "locale": "en_US", "hreflang": "en", "ad": "English"},
    "de": {"lang": "de", "locale": "de_DE", "hreflang": "de", "ad": "Deutsch"},
    "ru": {"lang": "ru", "locale": "ru_RU", "hreflang": "ru", "ad": "Русский"},
    "zh": {"lang": "zh-Hans", "locale": "zh_CN", "hreflang": "zh-Hans", "ad": "简体中文"},
}

# Sayfa uretimi disinda tutulacak dosyalar (dogrulama dosyalari vb.)
HARIC_DOSYALAR = {"404.html", "onizleme-mobil.html"}
# Arama motoru dogrulama dosyalari (google/yandex/bing/baidu...) ve gecici onizleme sayfalari cevrilmez
HARIC_DESEN_RE = re.compile(r"^(google|yandex|baidu|bing|naver|indexnow|site-?verification|onizleme-)", re.I)

# ----------------------------------------------------------------------------
# 2) ARAYUZ SOZLUGU (elle yazilmis; makine cevirisine birakilmaz)
#    Anahtar = Turkce kaynak metin (bastaki emoji/isaret ayiklandiktan sonraki hali)
# ----------------------------------------------------------------------------

SOZLUK = {
    # --- ust menu ---
    "Raporlar": {"en": "Reports", "de": "Berichte", "ru": "Отчёты", "zh": "报告"},
    "Hisseler": {"en": "Stocks", "de": "Aktien", "ru": "Акции", "zh": "股票"},
    "Derin Analiz": {"en": "Deep Analysis", "de": "Tiefenanalyse", "ru": "Глубокий анализ", "zh": "深度分析"},
    "Teknik Tarama": {"en": "Technical Scan", "de": "Technischer Scan", "ru": "Технический анализ", "zh": "技术扫描"},
    "Sinyal Karnesi": {"en": "Signal Scorecard", "de": "Signal-Bilanz", "ru": "Сводка сигналов", "zh": "信号记分卡"},
    "Borsapy Sinyal": {"en": "Borsapy Signals", "de": "Borsapy-Signale", "ru": "Сигналы Borsapy", "zh": "Borsapy 信号"},
    "Haberler": {"en": "News", "de": "Nachrichten", "ru": "Новости", "zh": "新闻"},
    "Şirket Haberleri": {"en": "Company News", "de": "Unternehmensnachrichten", "ru": "Новости компаний", "zh": "公司新闻"},
    "Deneme Portföyü": {"en": "Demo Portfolio", "de": "Demo-Portfolio", "ru": "Демо-портфель", "zh": "模拟投资组合"},
    "Hafta Sonu": {"en": "Weekend", "de": "Wochenende", "ru": "Выходные", "zh": "周末"},
    "Borsa Okulu": {"en": "Market School", "de": "Börsenschule", "ru": "Школа рынка", "zh": "股市学堂"},
    "Takvim": {"en": "Calendar", "de": "Kalender", "ru": "Календарь", "zh": "日历"},
    "Sözlük": {"en": "Glossary", "de": "Glossar", "ru": "Глоссарий", "zh": "术语表"},
    # --- mobil alt menu ---
    "Teknik": {"en": "Technical", "de": "Technik", "ru": "Технический", "zh": "技术"},
    "Portföy": {"en": "Portfolio", "de": "Portfolio", "ru": "Портфель", "zh": "投资组合"},
    # --- basliklar / bolum adlari ---
    "BIST 30 Günlük Piyasa Raporları": {
        "en": "BIST 30 Daily Market Reports",
        "de": "BIST 30 Tagesmarktberichte",
        "ru": "Ежедневные отчёты по рынку BIST 30",
        "zh": "BIST 30 每日市场报告",
    },
    "Rapor Arşivi": {"en": "Report Archive", "de": "Berichtsarchiv", "ru": "Архив отчётов", "zh": "报告存档"},
    "Güncel Derin Analiz": {"en": "Latest Deep Analysis", "de": "Aktuelle Tiefenanalyse", "ru": "Актуальный глубокий анализ", "zh": "最新深度分析"},
    "Hafta Sonu Gündemi": {"en": "Weekend Agenda", "de": "Wochenendagenda", "ru": "Повестка выходных", "zh": "周末议程"},
    "Hafta Sonu Borsa Okulu": {"en": "Weekend Market School", "de": "Wochenend-Börsenschule", "ru": "Школа рынка выходного дня", "zh": "周末股市学堂"},
    "Teknik Taramada Öne Çıkanlar": {"en": "Technical Scan Highlights", "de": "Highlights des technischen Scans", "ru": "Лидеры технического анализа", "zh": "技术扫描亮点"},
    "Hisse Kartları": {"en": "Stock Cards", "de": "Aktienkarten", "ru": "Карточки акций", "zh": "股票卡片"},
    "Canlı Piyasa Isı Haritası": {"en": "Live Market Heatmap", "de": "Live-Markt-Heatmap", "ru": "Живая карта рынка", "zh": "实时市场热力图"},
    # --- buton / baglanti metinleri ---
    "Günlük raporu aç": {"en": "Open daily report", "de": "Tagesbericht öffnen", "ru": "Открыть дневной отчёт", "zh": "打开每日报告"},
    "Tüm teknik tarama tablosu": {"en": "Full technical scan table", "de": "Vollständige Scan-Tabelle", "ru": "Полная таблица сканирования", "zh": "完整技术扫描表"},
    "Detaylı portföy geçmişi": {"en": "Detailed portfolio history", "de": "Detaillierte Portfolio-Historie", "ru": "Подробная история портфеля", "zh": "详细投资组合历史"},
    "Borsa Okulu sayfası": {"en": "Market School page", "de": "Seite der Börsenschule", "ru": "Страница школы рынка", "zh": "股市学堂页面"},
    "Daha fazla göster": {"en": "Show more", "de": "Mehr anzeigen", "ru": "Показать больше", "zh": "显示更多"},
    "Yenile": {"en": "Refresh", "de": "Aktualisieren", "ru": "Обновить", "zh": "刷新"},
    "Şimdi yenile": {"en": "Refresh now", "de": "Jetzt aktualisieren", "ru": "Обновить сейчас", "zh": "立即刷新"},
    "Ara": {"en": "Search", "de": "Suchen", "ru": "Поиск", "zh": "搜索"},
    "Sor": {"en": "Ask", "de": "Fragen", "ru": "Спросить", "zh": "提问"},
    "Fiyatlar yükleniyor...": {"en": "Loading prices…", "de": "Preise werden geladen…", "ru": "Загрузка цен…", "zh": "正在加载价格…"},
    "Sayfa dili seçin": {"en": "Select page language", "de": "Seitensprache wählen", "ru": "Выберите язык страницы", "zh": "选择页面语言"},
    "Hızlı menü": {"en": "Quick menu", "de": "Schnellmenü", "ru": "Быстрое меню", "zh": "快捷菜单"},
    "Sitede ara: hisse, konu, tarih... (ör. PETKM, RSI, portföy)": {
        "en": "Search the site: stock, topic, date… (e.g. PETKM, RSI, portfolio)",
        "de": "Website durchsuchen: Aktie, Thema, Datum… (z. B. PETKM, RSI, Portfolio)",
        "ru": "Поиск по сайту: акция, тема, дата… (напр. PETKM, RSI, портфель)",
        "zh": "站内搜索：股票、主题、日期…（例如 PETKM、RSI、投资组合）",
    },
    "BIST AI asistanına sorun...": {
        "en": "Ask the BIST AI assistant…",
        "de": "Fragen Sie den BIST-KI-Assistenten…",
        "ru": "Спросите ИИ-ассистента BIST…",
        "zh": "询问 BIST AI 助手…",
    },
    "BIST 30 Günlük Raporlar": {
        "en": "BIST 30 Daily Reports",
        "de": "BIST 30 Tagesberichte",
        "ru": "Ежедневные отчёты BIST 30",
        "zh": "BIST 30 每日报告",
    },
    "Tema değiştir": {"en": "Change theme", "de": "Design wechseln", "ru": "Сменить тему", "zh": "切换主题"},
    "Açık/Koyu tema": {"en": "Light/dark theme", "de": "Helles/dunkles Design", "ru": "Светлая/тёмная тема", "zh": "浅色/深色主题"},
    "Gizlilik & KVKK": {"en": "Privacy & Data Protection", "de": "Datenschutz", "ru": "Конфиденциальность", "zh": "隐私与数据保护"},
    "Güncel Değer": {"en": "Current value", "de": "Aktueller Wert", "ru": "Текущая стоимость", "zh": "当前价值"},
    "Günlük Değişim": {"en": "Daily change", "de": "Tagesänderung", "ru": "Дневное изменение", "zh": "日变化"},
    "Toplam Getiri": {"en": "Total return", "de": "Gesamtrendite", "ru": "Общая доходность", "zh": "总收益率"},
    "Başlangıç": {"en": "Start", "de": "Start", "ru": "Старт", "zh": "开始"},
    "Hisse": {"en": "Stock", "de": "Aktie", "ru": "Акция", "zh": "股票"},
    "Adet": {"en": "Qty", "de": "Stück", "ru": "Кол-во", "zh": "数量"},
    "İlk Alım": {"en": "Entry price", "de": "Einstiegspreis", "ru": "Цена входа", "zh": "买入价"},
    "Son Fiyat": {"en": "Last price", "de": "Letzter Preis", "ru": "Последняя цена", "zh": "最新价"},
    "Getiri": {"en": "Return", "de": "Rendite", "ru": "Доходность", "zh": "收益率"},
    "Destek": {"en": "Support", "de": "Unterstützung", "ru": "Поддержка", "zh": "支撑"},
    "Direnç": {"en": "Resistance", "de": "Widerstand", "ru": "Сопротивление", "zh": "阻力"},
    "Sinyal": {"en": "Signal", "de": "Signal", "ru": "Сигнал", "zh": "信号"},
    # --- hukuki uyari (elle sabitlenir; makineye birakilmaz) ---
    "Burada yer alan bilgi, yorum ve öneriler bilgilendirme amaçlıdır; yatırım danışmanlığı kapsamında değildir, yatırım tavsiyesi değildir.": {
        "en": "The information, comments and suggestions here are for informational purposes only; they do not constitute investment advisory services or investment advice.",
        "de": "Die hier enthaltenen Informationen, Kommentare und Empfehlungen dienen ausschließlich Informationszwecken; sie stellen keine Anlageberatung und keine Anlageempfehlung dar.",
        "ru": "Приведённая здесь информация, комментарии и рекомендации носят исключительно информационный характер; они не являются инвестиционным консультированием или инвестиционной рекомендацией.",
        "zh": "本页所载信息、评论与建议仅供信息参考；不构成投资顾问服务或投资建议。",
    },
}

# Cevrilmeyecek metinler (ticker, sayi, kisaltma)
TICKER_RE = re.compile(r"^[A-ZÇĞİÖŞÜ0-9]{2,6}$")
SAYI_RE = re.compile(r"^[\s\d\.,%+\-–—()\[\]/|·•:;<>₺$€]*$")
KISALTMA_RE = re.compile(r"^(RSI|MACD|EMA|SMA|ADX|CCI|WT|BIST|TL|USD|EUR)[\s\d%\.\-]*$", re.I)

# Sayi bicimi: Turkce (1.234,56 / +2,55%) -> hedef dil (1,234.56 / +2.55%)
SAYI_BICIM_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+")


def sayilari_cevir(metin: str, dil: str) -> str:
    """Site 21.09.2026'dan beri tum sayilari Turkce bicimde yaziyor (1.234,56 / +2,55%).
    Turkce disi dillerde bu bicim yanlistir; ayiricilar yer degistirir."""
    if dil == "tr" or not metin:
        return metin

    def degistir(m):
        s = m.group(0)
        if "." in s and "," in s:
            return s.replace(".", "\u0000").replace(",", ".").replace("\u0000", ",")
        if "," in s:
            return s.replace(",", ".")      # 21,44 -> 21.44
        return s.replace(".", ",")          # 98.908 -> 98,908

    metin = SAYI_BICIM_RE.sub(degistir, metin)
    return re.sub(r"%(\d)", r"\1%", metin)  # %21 -> 21%


# Tercume edilecek ozellikler
OZELLIK_ANAHTARLARI = ("title", "alt", "placeholder", "aria-label", "content", "data-aciklama")
# content="..." yalnizca bu meta/etiketlerde cevrilir
ICERIK_META_RE = re.compile(r'<(?:meta)\b[^>]*\b(?:name|property)\s*=\s*"(description|og:description|og:title|og:image:alt|twitter:description|twitter:title|keywords)"[^>]*>', re.I)

# ----------------------------------------------------------------------------
# 3) CEVIRI SAGLAYICILARI
# ----------------------------------------------------------------------------


class Cevirmen:
    """Onbellek + saglayici zinciri (llm -> deepl -> mock)."""

    def __init__(self, provider: str = "mock", onbellek_yolu: str = "i18n-cache.json"):
        self.provider = provider
        # Onbellek yalnizca gercek saglayicilarda kullanilir; mock/off ciktilari
        # gercek onbellege sizmamali (test isaretleri uretime karismasin).
        self.gecerli_onbellek = provider in ("llm", "deepl")
        self.onbellek_yolu = Path(onbellek_yolu if self.gecerli_onbellek
                                  else f"i18n-cache-{provider}.json")
        self.onbellek = {}
        if self.gecerli_onbellek and self.onbellek_yolu.exists():
            try:
                self.onbellek = json.loads(self.onbellek_yolu.read_text(encoding="utf-8"))
            except Exception:
                self.onbellek = {}
        # --- koruma mekanizmalari (2026-09-21: kosu timeout'undan sonra eklendi) ---
        self.baslangic = time.time()
        self.sure_siniri = float(os.environ.get("I18N_SURE_SINIRI_DK", "20")) * 60
        self.istek_timeout = float(os.environ.get("I18N_TIMEOUT", "30"))
        self.devre_disi: set[str] = set()      # kalici hata alan saglayicilar (401/402/403)
        self.hata_sayaci: dict[str, int] = {}  # gecici hatalar (timeout/429/5xx)
        self.atlanan_parca = 0
        self.sure_doldu = False
        self.yeni = 0
        self.onbellekten = 0
        self.sozlukten = 0

    # -- onbellek --------------------------------------------------------
    @staticmethod
    def _anahtar(dil: str, metin: str) -> str:
        return f"{dil}:{hashlib.sha1(metin.encode('utf-8')).hexdigest()[:20]}"

    def kaydet(self):
        if self.gecerli_onbellek:
            self.onbellek_yolu.write_text(
                json.dumps(self.onbellek, ensure_ascii=False, indent=0, sort_keys=True),
                encoding="utf-8",
            )

    # -- ana giris -------------------------------------------------------
    def cevir_liste(self, metinler: list[str], dil: str) -> list[str]:
        """Metin listesini cevirir; sozluk > onbellek > saglayici sirasi."""
        sonuc: list[str] = []
        bekleyen: list[tuple[int, str]] = []

        for i, m in enumerate(metinler):
            sonuc.append(m)  # yer tutucu
            saf, _onek = metni_ayikla(m)
            if not cevrilecek_mi(saf):
                continue
            if saf in SOZLUK and dil in SOZLUK[saf]:
                sonuc[i] = m.replace(saf, SOZLUK[saf][dil])
                self.sozlukten += 1
                continue
            k = self._anahtar(dil, saf)
            # onbellekteki deger kaynakla AYNI ise gecersiz say (zehirli kayitlari etkisiz kilar)
            if k in self.onbellek and self.onbellek[k] and self.onbellek[k].strip() != saf.strip():
                _saf, onek = metni_ayikla(m)
                sonuc[i] = (onek + self.onbellek[k]) if onek else self.onbellek[k]
                self.onbellekten += 1
                continue
            if k in self.onbellek and self.onbellek[k] and len(saf) <= 60:
                # kisa dizelerde (sayi/ticker/kod/tablo satiri) birebir ayni ceviri gecerlidir
                _saf, onek = metni_ayikla(m)
                sonuc[i] = (onek + self.onbellek[k]) if onek else self.onbellek[k]
                self.onbellekten += 1
                continue
            bekleyen.append((i, saf))

        if bekleyen:
            parcalar = [s for _, s in bekleyen]
            ham = self._saglayici(parcalar, dil)
            for (i, saf), cev in zip(bekleyen, ham):
                # Ceviri yoksa saglayici BOS doner ("").
                # Ama ceviri kaynakla birebir ayni olabilir: sayilar, ticker'lar, kodlar
                # ("BIST 30", "414,75 TL", "RSI"), tablo satirlari. Bunlar GECERLI ceviridir;
                # onbellege yazilmazsa her kosuda bosuna yeniden denenir.
                if not cev or not isinstance(cev, str):
                    self.atlanan_parca += 1
                    continue
                if cev.strip() == saf.strip() and len(saf) > 60:
                    # Uzun metinde birebir ayni cikti suphelidir (model kopyalamis olabilir):
                    # onbellege yazma, sonraki kosuda yeniden dene.
                    self.atlanan_parca += 1
                    continue
                self.onbellek[self._anahtar(dil, saf)] = cev
                self.yeni += 1
                # metnin basindaki emoji/isaret korunur
                _saf, onek = metni_ayikla(metinler[i])
                sonuc[i] = (onek + cev) if onek else cev
        if dil != "tr":
            sonuc = [sayilari_cevir(s, dil) for s in sonuc]
        return sonuc

    # -- saglayicilar ----------------------------------------------------
    def _saglayici(self, parcalar: list[str], dil: str) -> list[str]:
        if self.provider == "mock":
            return [f"[{DILLER[dil]['ad']}] {p}" for p in parcalar]
        if self.provider == "off":
            return list(parcalar)  # gercek passthrough: hicbir sey eklenmez
        if self.provider == "deepl":
            try:
                return self._deepl(parcalar, dil)
            except urllib.error.HTTPError as h:
                # 456 = kota bitti / duzeltilemez hata: kosuyu cokertmesin, bos donup devam etsin
                try:
                    govde = h.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    govde = ""
                print(f"[i18n] DeepL hatasi HTTP {h.code} :: {govde}", file=sys.stderr)
                if h.code in (456, 403, 401, 429):
                    self.devre_disi.add("deepl")
                return [""] * len(parcalar)
            except Exception as h:
                print(f"[i18n] DeepL hatasi: {h}", file=sys.stderr)
                return [""] * len(parcalar)
        return self._llm(parcalar, dil)

    def _uclari_kur(self) -> list:
        """Saglayici + model listesi (oncelik sirasiyla). Test modu da bunu kullanir."""
        uclar = []
        # 1) Z.AI (GLM) — kullanicinin kayitli anahtari; bot.py ile ayni uc ve modeller
        zai_anahtar = os.environ.get("ZAI_API_KEY")
        if zai_anahtar:
            # Not: glm-4.5-flash testte ~1.1 sn; glm-4.7-flash dalgali (1.4-25 sn).
            # ZAI_MODELS (virgullu) ile birden fazla model denenebilir; test modu bunlari sirayla olcer.
            ham = os.environ.get("ZAI_MODELS") or os.environ.get("ZAI_MODEL") or "glm-4.5-flash"
            zai_modeller = [m.strip() for m in ham.split(",") if m.strip()] or ["glm-4.5-flash"]
            zai_modeller.append("glm-4.7-flash")
            zai_modeller = list(dict.fromkeys(zai_modeller))   # tekrarlari at
            uclar.append(("https://api.z.ai/api/paas/v4/chat/completions", zai_anahtar, zai_modeller))
        # 2) AMD Radeon Developer Cloud (bot.py'nin ana saglayicisi)
        amd_anahtar = os.environ.get("AMD_API_KEY")
        if amd_anahtar:
            modeller = [os.environ.get("AMD_MODEL", "DeepSeek-V4-Flash")]
            modeller += [m.strip() for m in os.environ.get("AMD_FALLBACK_MODELS", "Qwen3.8-Flash-Next").split(",") if m.strip()]
            uclar.append(("https://developer.amd.com.cn/radeon/api/v1/chat/completions", amd_anahtar, modeller))
        alt_anahtar = os.environ.get("ALT_API_KEY") or os.environ.get("GROQ_API_KEY")
        if alt_anahtar:
            alt_url = os.environ.get("ALT_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/") + "/chat/completions"
            alt_modeller = [m.strip() for m in os.environ.get("ALT_MODELS", "llama-3.3-70b-versatile").split(",") if m.strip()]
            uclar.append((alt_url, alt_anahtar, alt_modeller))
        or_anahtar = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OR_API_KEY")
        if or_anahtar:
            or_url = os.environ.get("OR_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
            varsayilan_or = "meta-llama/llama-3.3-70b-instruct:free,deepseek/deepseek-chat-v3.1:free"
            or_modeller = [m.strip() for m in
                           os.environ.get("OR_MODELS", os.environ.get("OR_MODEL", varsayilan_or)).split(",")
                           if m.strip()]
            uclar.append((or_url, or_anahtar, or_modeller))
        return uclar

    def _llm(self, parcalar: list[str], dil: str) -> list[str]:
        hedef = {
            "en": "English", "de": "German", "ru": "Russian", "zh": "Simplified Chinese",
        }[dil]
        sistem = (
            "You are a professional translator for a Turkish finance website. "
            "The user sends a JSON array of strings. Translate EVERY item into " + hedef + ". "
            "Return ONLY a JSON array of the exact same length — no explanations, no markdown fences. "
            "Every element must be a JSON string: escape any double quote as \\\" and never put a raw line break inside a string. "
            "Keep stock tickers (KCHOL, PETKM, THYAO...), numbers, currency amounts, dates and indicator "
            "acronyms (RSI, MACD, EMA, ADX, CCI, WT) exactly as they are."
        )
        uclar = self._uclari_kur()
        if not uclar:
            raise SystemExit(
                "[i18n] HATA: --provider llm icin AMD_API_KEY / ALT_API_KEY / OPENROUTER_API_KEY'den en az biri gerekli."
            )

        cikti: list[str] = []
        PENCERE = int(os.environ.get("I18N_PENCERE", "10"))  # istek basina dize sayisi (kucuk parti = daha az JSON bozulmasi)
        dil_hedefi = {"en": "EN-GB", "de": "DE", "ru": "RU", "zh": "ZH"}[dil]
        for bas in range(0, len(parcalar), PENCERE):
            # sure butcesi: asilirsa kalanlari cevirmeden don (site uretimi asla bloke olmasin)
            if time.time() - self.baslangic > self.sure_siniri:
                self.sure_doldu = True
                self.atlanan_parca += len(parcalar) - bas
                print(f"[i18n] sure butcesi doldu ({self.sure_siniri/60:.0f} dk); kalan {len(parcalar)-bas} parca sonraki kosuya birakildi", file=sys.stderr)
                break
            dilim = parcalar[bas:bas + PENCERE]
            if bas % (PENCERE * 10) == 0:
                print(f"[i18n] {dil}: {bas}/{len(parcalar)} dize (atlanan: {self.atlanan_parca})", flush=True)
            son = None
            son_uc = None   # hangi saglayici cevirdi (gecikme ona gore)
            for url, anahtar, modeller in uclar:
                if url in self.devre_disi:
                    continue
                for model in modeller:
                    try:
                        aday = self._llm_istek_dayanikli(url, anahtar, model, sistem, dilim, self.istek_timeout)
                        if aday and len(aday) == len(dilim):
                            son = aday
                            son_uc = url
                            break
                    except urllib.error.HTTPError as hata:
                        kod = hata.code
                        # hata govdesini de yaz: 403/402'nin gercek sebebi (anahtar mi, model mi, bolge mi) gorunsun
                        try:
                            govde_hata = hata.read().decode("utf-8", errors="replace")[:200]
                        except Exception:
                            govde_hata = ""
                        print(f"[i18n] LLM hatasi ({model}): HTTP {kod} :: {govde_hata}", file=sys.stderr)
                        if kod in (401, 402, 403, 404):      # kalici: anahtar/kota/yetki
                            self.devre_disi.add(url)
                            print(f"[i18n] saglayici devre disi: {url} (HTTP {kod})", file=sys.stderr)
                            break
                        if kod == 429:                        # hiz siniri: bekle, bir kez daha dene
                            # 429 = gecici yogunluk. Saglayiciyi devre disi BIRAKMA, sadece bekle.
                            bekle = min(float(hata.headers.get("Retry-After") or 6), 30)
                            print(f"[i18n] 429 hiz siniri ({model}); {bekle:.0f} sn bekleniyor", file=sys.stderr)
                            time.sleep(bekle)
                            try:
                                aday = self._llm_istek_dayanikli(url, anahtar, model, sistem, dilim, self.istek_timeout)
                                if aday and len(aday) == len(dilim):
                                    son = aday
                                    son_uc = url
                                    break
                            except Exception:
                                pass
                            son = None
                            continue
                        self.hata_sayaci[url] = self.hata_sayaci.get(url, 0) + 1
                        if self.hata_sayaci[url] >= 6:
                            self.devre_disi.add(url)
                            print(f"[i18n] saglayici devre disi (3 hata): {url}", file=sys.stderr)
                            break
                        son = None
                    except Exception as hata:
                        print(f"[i18n] LLM hatasi ({model}): {hata}", file=sys.stderr)
                        self.hata_sayaci[url] = self.hata_sayaci.get(url, 0) + 1
                        if self.hata_sayaci[url] >= 6:
                            self.devre_disi.add(url)
                            print(f"[i18n] saglayici devre disi (3 hata): {url}", file=sys.stderr)
                            break
                        son = None
                if son:
                    break
            # son yedek: DeepL (DEEPL_API_KEY varsa) — LLM'ler dusunce tek kurtaricimiz
            if not son and (os.environ.get("DEEPL_API_KEY") or os.environ.get("DEEPL_KEY")):
                try:
                    aday = self._deepl(dilim, dil, hedef=dil_hedefi)
                    if aday and len(aday) == len(dilim):
                        son = aday
                        son_uc = "deepl"
                        print("[i18n] DeepL yedeginden cevrildi", file=sys.stderr)
                except Exception as hata:
                    print(f"[i18n] DeepL hatasi: {hata}", file=sys.stderr)
            if not son:
                self.atlanan_parca += len(dilim)
            # DIKKAT: cevrilemeyen parca icin kaynak metin yerine BOS dondurulur.
            # (Aksi halde cagiran taraf bunu "cevrildi" sanip onbellege yazar ve bir daha denemez.)
            cikti.extend(son if son else [""] * len(dilim))
            # hiz sinirina karsi ara: Z.AI hizli ve limiti yuksek -> kisa; AMD 20/dk -> uzun
            if bas + PENCERE < len(parcalar):
                if son_uc and "api.z.ai" in son_uc:
                    aralik = float(os.environ.get("I18N_ARALIK_ZAI", "0.4"))
                elif son_uc == "deepl":
                    aralik = 0.1
                else:
                    aralik = float(os.environ.get("I18N_ARALIK", "1.0"))
                if aralik > 0:
                    time.sleep(aralik)
        return cikti

    @staticmethod
    def _llm_istek(url, anahtar, model, sistem, dilim, timeout=30, usage_kutusu=None):
        istek_govdesi = {
            "model": model,
            "messages": [
                {"role": "system", "content": sistem},
                {"role": "user", "content": json.dumps(dilim, ensure_ascii=False)},
            ],
            "temperature": 0.2,
        }
        # Z.AI: glm-5.3 ailesi dusunmeyi kapatmiyor (kod 1210) -> alani hic gondermeyelim.
        # Digerlerinde ceviri icin dusunme kapali (hiz + token tasarrufu).
        if "z.ai" in url and not model.startswith("glm-5.3"):
            istek_govdesi["thinking"] = {"type": "disabled"}
        govde = json.dumps(istek_govdesi).encode("utf-8")
        istek = urllib.request.Request(url, data=govde, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {anahtar}",
        })
        with urllib.request.urlopen(istek, timeout=timeout) as yanit:
            veri = json.loads(yanit.read().decode("utf-8"))
        if usage_kutusu is not None and isinstance(veri.get("usage"), dict):
            usage_kutusu.append(veri["usage"])
        icerik = veri["choices"][0]["message"]["content"].strip()
        sonuc = Cevirmen._json_ayikla(icerik, len(dilim))
        if not isinstance(sonuc, list) or len(sonuc) != len(dilim):
            raise ValueError("LLM ciktisi ayristirilamadi")
        return [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in sonuc]

    @staticmethod
    def _json_ayikla(icerik: str, beklenen: int):
        """LLM ciktisindan ceviri listesini dayanikli sekilde cikarir (yaygin JSON bozulmalarini onarir)."""
        metin = icerik.strip()
        metin = re.sub(r"^```(?:json)?\s*", "", metin)
        metin = re.sub(r"\s*```$", "", metin)
        adaylar = [metin]
        try:
            i, j = metin.index("["), metin.rindex("]")
            adaylar.append(metin[i:j + 1])
        except Exception:
            pass
        for aday in adaylar:
            try:  # strict=False: dize icindeki ham satir sonu / kontrol karakterine izin ver
                d = json.loads(aday, strict=False)
                if isinstance(d, list) and len(d) == beklenen:
                    return d
            except Exception:
                pass
            try:  # sondaki fazla virgulleri temizle
                d = json.loads(re.sub(r",\s*([\]\}])", r"\1", aday), strict=False)
                if isinstance(d, list) and len(d) == beklenen:
                    return d
            except Exception:
                pass
        if _json_repair_kurtar is not None:  # eksik virgul / kacissiz tirnak kurtarici
            try:
                d = _json_repair_kurtar(metin, return_objects=True)
                if isinstance(d, list) and len(d) == beklenen:
                    return [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in d]
            except Exception:
                pass
        satirlar = []  # son care: satir tabanli ('1. ceviri' / '"ceviri",')
        for s in metin.splitlines():
            s = s.strip().rstrip(",")
            if not s or s in ("[", "]"):
                continue
            m = re.match(r'^(?:\d+\s*[\.\)]\s*)?"?(.*?)"?$', s, re.S)
            if m and m.group(1).strip():
                satirlar.append(m.group(1))
        return satirlar if len(satirlar) == beklenen else None

    @classmethod
    def _llm_istek_dayanikli(cls, url, anahtar, model, sistem, dilim, timeout=30, usage_kutusu=None):
        """Parti bozulursa ikiye bolup yeniden dener; kucuk partilerde JSON bozulmasi cogu kez kaybolur."""
        try:
            return cls._llm_istek(url, anahtar, model, sistem, dilim, timeout, usage_kutusu)
        except (json.JSONDecodeError, ValueError):
            if len(dilim) <= 1:
                raise
            orta = len(dilim) // 2
            a = cls._llm_istek_dayanikli(url, anahtar, model, sistem, dilim[:orta], timeout, usage_kutusu)
            b = cls._llm_istek_dayanikli(url, anahtar, model, sistem, dilim[orta:], timeout, usage_kutusu)
            if a and b and len(a) == orta and len(b) == len(dilim) - orta:
                return list(a) + list(b)
            raise

    @staticmethod
    def _deepl(parcalar: list[str], dil: str, hedef: str | None = None) -> list[str]:
        anahtar = os.environ.get("DEEPL_API_KEY") or os.environ.get("DEEPL_KEY")
        if not anahtar:
            raise SystemExit("[i18n] HATA: DeepL icin DEEPL_API_KEY gerekli.")
        hedef = hedef or {"en": "EN-GB", "de": "DE", "ru": "RU", "zh": "ZH"}[dil]
        # Free anahtarlar (:fx) -> api-free; Pro anahtarlar -> api.deepl.com
        # (Pro'ya gecersen DEEPL_BASE_URL=https://api.deepl.com ayarlaman yeterli)
        taban = os.environ.get("DEEPL_BASE_URL", "https://api-free.deepl.com").rstrip("/")
        govde = "&".join([f"text={urllib.parse.quote(p)}" for p in parcalar])
        govde += f"&target_lang={hedef}&source_lang=TR&preserve_formatting=1"
        istek = urllib.request.Request(taban + "/v2/translate", data=govde.encode("utf-8"), headers={
            "Authorization": f"DeepL-Auth-Key {anahtar}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(istek, timeout=float(os.environ.get("I18N_TIMEOUT", "60"))) as yanit:
            veri = json.loads(yanit.read().decode("utf-8"))
        return [x.get("text", "") for x in veri.get("translations", [])]


def metni_ayikla(metin: str) -> tuple[str, str]:
    """'📅 Takvim' -> ('Takvim', '📅 '): bastaki emoji/isaretleri ayirir."""
    m = re.match(r"^([^\wÇĞİÖŞÜçğıöşü]*)(.*)$", metin, re.S)
    if m:
        return m.group(2), metni_boslukla(m.group(1))
    return metin, ""


def metni_boslukla(onek: str) -> str:
    return (onek + " ") if onek and not onek.endswith((" ", "\u00a0")) else onek


# Ticker / kur / gosterge gibi sayisal metinler ceviriye GONDERILMEZ.
# (Sayi bicimi ayrica sayilari_cevir ile duzeltilir; fiyatin LLM'e gitmesi risklidir.)
BIRIMLER = ("TL", "TRY", "USD", "EUR", "RSI", "MACD", "EMA", "SMA", "ADX", "CCI", "WT")


def sayisal_mi(metin: str) -> bool:
    temiz = re.sub(r"[\d\.,%+\-–—()\[\]/|:;·•₺$€°\s]", "", metin)
    if not temiz:
        return True
    return len(temiz) <= 4 and temiz.upper() in BIRIMLER


def cevrilecek_mi(metin: str) -> bool:
    m = metin.strip()
    if len(m) < 2:
        return False
    if SAYI_RE.match(m) or TICKER_RE.match(m) or KISALTMA_RE.match(m):
        return False
    if sayisal_mi(m):        # fiyat, yuzde, oran, "TL" iceren sayisal metin
        return False
    # en az bir harf olmali
    return bool(re.search(r"[A-Za-zÇĞİÖŞÜçğıöşüА-Яа-я]", m))


# ----------------------------------------------------------------------------
# 4) HTML DONUSTURUCU
# ----------------------------------------------------------------------------


class MetinToplayici(HTMLParser):
    """Sayfayi tarar, cevrilecek metin/ozellik parcalarini toplar ve
    yerlerine ⟦n⟧ yer tutucusu koyar."""

    def __init__(self, dil: str = "tr"):
        super().__init__(convert_charrefs=True)
        self.dil = dil
        self.parcalar: list[dict] = []
        self.cikti: list[str] = []
        self.korunan = 0  # script/style derinligi
        self._ic_meta = False

    def _kaydet(self, metin: str, tur: str) -> int:
        self.parcalar.append({"kaynak": metin, "tur": tur})
        return len(self.parcalar) - 1

    # -- etiketler --
    def handle_starttag(self, tag, attrs):
        ham = self.get_starttag_text() or f"<{tag}>"
        if tag in ("script", "style"):
            self.korunan += 1
            self.cikti.append(ham)
            return
        kapanis = ham.endswith("/>")
        govde = ham[:-2] if kapanis else ham[:-1]

        icerik_izi = ICERIK_META_RE.match(govde + ">") is not None

        def degistir(m):
            ad, deger = m.group(1), m.group(2)
            if ad == "content" and not icerik_izi:
                return m.group(0)
            saf, onek = metni_ayikla(deger)
            if not cevrilecek_mi(saf):
                # ceviriye gitmiyor ama sayi bicimi yine de duzeltilir (fiyat, yuzde…)
                return f'{ad}="{sayilari_cevir(deger, self.dil)}"'
            i = self._kaydet(deger, "ozellik")
            return f'{ad}="⟦{i}⟧"'

        desen = r'(?<![\w-])(' + "|".join(re.escape(a) for a in OZELLIK_ANAHTARLARI) + r')="([^"]*)"'
        yeni = re.sub(desen, degistir, govde)
        self.cikti.append(yeni + ("/>" if kapanis else ">"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.korunan:
            self.korunan -= 1
        self.cikti.append(f"</{tag}>")

    def handle_data(self, veri):
        if self.korunan:
            self.cikti.append(veri)  # script/style -> dokunma
            return
        saf, _onek = metni_ayikla(veri)
        if not cevrilecek_mi(saf):
            # ceviriye gitmeyen sayisal metinlerde de sayi bicimi duzeltilir
            self.cikti.append(_kacir(sayilari_cevir(veri, self.dil)))
            return
        i = self._kaydet(veri, "metin")
        self.cikti.append(f"⟦{i}⟧")

    def handle_comment(self, veri):
        self.cikti.append(f"<!--{veri}-->")

    def handle_decl(self, decl):
        self.cikti.append(f"<!{decl}>")

    def handle_pi(self, veri):
        self.cikti.append(f"<?{veri}>")

    def bilinmeyen(self, veri):
        self.cikti.append(veri)


def _kacir(metin: str) -> str:
    return metin.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


YER_TUTUCU_RE = re.compile(r"⟦(\d+)⟧")


DIL_SW_RE = re.compile(r'<nav class="dil-linkler".*?</nav>', re.S)
DIL_SW_TOKEN = "<!--@@DIL_SW@@-->"

# Eski (Puter tabanli) anlik ceviri kutusu: sablon guncellenmeden uretilmis sayfalarda
# bu blok bulunursa gercek dil baglantilarina cevrilir (gecis kolayligi).
ESKI_WIDGET_RE = re.compile(
    r'<select id="dil-sec".*?</select>\s*<span id="ceviri-durum".*?</span>\s*<script>.*?</script>',
    re.S,
)
DIL_STIL = (
    "<style>.dil-linkler{display:flex;gap:10px;align-items:center;font-size:12.5px;flex-wrap:wrap}"
    ".dil-linkler a{color:var(--muted);text-decoration:none;border-bottom:1px solid transparent}"
    ".dil-linkler a:hover{color:var(--accent);border-bottom-color:var(--accent)}"
    ".dil-linkler a.aktif{color:var(--accent);font-weight:600}</style>"
)
DIL_ADLARI = (
    ("tr", "tr", "Türkçe"),
    ("en", "en", "English"),
    ("de", "de", "Deutsch"),
    ("ru", "ru", "Русский"),
    ("zh", "zh-Hans", "简体中文"),
)


def dil_switcher_html(rel: Path, aktif: str) -> str:
    """Dil degistirici blogu: ayni sayfanin her dildeki surumune gercek baglanti."""
    y = seo_yol(rel)
    parcalar = [DIL_STIL, '<nav class="dil-linkler" aria-label="Sayfa dili seçin">',
                '<span aria-hidden="true">🌐</span>']
    for kod, hl, ad in DIL_ADLARI:
        url = f"{SITE_URL}{kod + '/' if kod != 'tr' else ''}{y}"
        a = ' class="aktif" aria-current="true"' if kod == aktif else ""
        parcalar.append(f'<a href="{url}" hreflang="{hl}" lang="{hl}" data-dil="{kod}"{a}>{ad}</a>')
    parcalar.append("</nav>")
    return "\n".join(parcalar)


def dil_sw_aktif(sw: str, dil: str) -> str:
    """Dil degistiricisinde aktif dili isaretler (baglantilar her dilde aynidir)."""
    sw = sw.replace(' class="aktif" aria-current="true"', "")
    return sw.replace(f'data-dil="{dil}"', f'data-dil="{dil}" class="aktif" aria-current="true"')


def sayfayi_cevir(ham_html: str, dil: str, cevirmen: Cevirmen, diller: list[str] | None = None,
                  rel: Path | None = None) -> str:
    # (-1) eski widget varsa gercek dil baglantilarina cevir
    if rel is not None and ESKI_WIDGET_RE.search(ham_html):
        ham_html = ESKI_WIDGET_RE.sub(lambda _m: dil_switcher_html(rel, dil), ham_html, count=1)

    # (0) dil degistiriciyi dokunulmaz bolgeye al: baglantilari mutlak ve TUM dillerde
    #     ayni olmali; ayrica ceviriye de girmemeli (dil adlari kendi dilinde yazilir).
    sw_eslesme = DIL_SW_RE.search(ham_html)
    sw_blok = sw_eslesme.group(0) if sw_eslesme else None
    if sw_blok:
        ham_html = ham_html.replace(sw_blok, DIL_SW_TOKEN, 1)

    # (a) mutlak SITE_URL baglantilari dil agacina tasinir (og-cover paylasilan varlik kalir)
    govde = re.sub(
        re.escape(SITE_URL) + r"(?!og-cover)",
        SITE_URL + dil + "/",
        ham_html,
    )
    # (b) head alanlari
    d = DILLER[dil]
    govde = re.sub(r'(<html[^>]*\blang=")[^"]*(")', lambda m: m.group(1) + d["lang"] + m.group(2), govde, count=1)
    govde = re.sub(r'(<meta property="og:locale" content=")[^"]*(")', lambda m: m.group(1) + d["locale"] + m.group(2), govde, count=1)
    govde = re.sub(r'("inLanguage"\s*:\s*")[^"]*(")', lambda m: m.group(1) + d["lang"].split("-")[0] + m.group(2), govde)

    # (c) metinleri topla
    p = MetinToplayici(dil)
    p.feed(govde)
    p.close()
    sablon = "".join(p.cikti)

    # (d) cevir
    kaynaklar = [x["kaynak"] for x in p.parcalar]
    ceviriler = cevirmen.cevir_liste(kaynaklar, dil) if kaynaklar else []

    def yerlestir(m):
        i = int(m.group(1))
        if i >= len(ceviriler):
            return m.group(0)
        deger = ceviriler[i]
        return _kacir(deger) if p.parcalar[i]["tur"] == "metin" else deger.replace('"', "&quot;")

    sablon = YER_TUTUCU_RE.sub(yerlestir, sablon)

    # (e) hreflang blogu (canonical'dan hemen sonra, bir kez)
    sablon = hreflang_ekle(sablon, dil, diller)

    # (f) dil degistiriciyi yerine geri koy (aktif dil isaretli)
    if sw_blok is not None:
        sablon = sablon.replace(DIL_SW_TOKEN, dil_sw_aktif(sw_blok, dil), 1)
    return sablon


def hreflang_ekle(html: str, dil: str, diller: list[str] | None = None) -> str:
    """Yalnizca gercekten uretilmis diller + tr listelenir (olu hreflang uretmemek icin)."""
    kume = ["tr"] + [d for d in (diller or list(DILLER.keys())) if d != "tr"]
    satirlar = []
    for kod in kume:
        meta = DILLER[kod]
        satirlar.append(f'<link rel="alternate" hreflang="{meta["hreflang"]}" href="@@URL_{kod}@@">')
    satirlar.append('<link rel="alternate" hreflang="x-default" href="@@URL_tr@@">')
    blok = "\n".join(satirlar)
    if 'hreflang="x-default"' in html:
        return html
    return re.sub(r'(<link rel="canonical"[^>]*>)', lambda m: m.group(1) + "\n" + blok, html, count=1)


# ----------------------------------------------------------------------------
# 5) SAYFA KESFI + YAZIM
# ----------------------------------------------------------------------------

DIL_DIZINLERI = set(DILLER.keys())


def turkce_sayfalar(kok: Path) -> list[Path]:
    bulunan: list[Path] = []
    for dosya in sorted(kok.rglob("*.html")):
        rel = dosya.relative_to(kok)
        parcalar = rel.parts
        if parcalar[0] in DIL_DIZINLERI:      # uretilmis dil dizinleri
            continue
        if dosya.name in HARIC_DOSYALAR or HARIC_DESEN_RE.match(dosya.name):
            continue
        if any(x in ("web", "node_modules", "functions", ".git") for x in parcalar):
            continue
        try:  # dogrulama dosyalari gibi <html> icermeyen parcalari atla
            if "<html" not in dosya.read_text(encoding="utf-8", errors="replace")[:4000].lower():
                continue
        except OSError:
            continue
        bulunan.append(rel)
    return bulunan


def tr_hashleri(kok: Path) -> dict[str, str]:
    return {str(r): hashlib.sha256((kok / r).read_bytes()).hexdigest() for r in turkce_sayfalar(kok)}


# Dil sayfalarina KOPYALANMAYACAK klasorler (site bunlari cagirmiyor; kopyalanirsa
# data/ + functions/ her dil dizininde 150+ gereksiz dosya olusturuyordu).
KOPYALAMA_DISLA = {"data", "pipeline", "web", "functions", ".github", ".git"}


# Dil sayfalarindan kok varliklara (css/js/json/png) kopyalanacak dosya turleri
KOPYALA_EKLERI = {".css", ".js", ".json", ".png", ".svg", ".webmanifest", ".ico", ".xml"}
CEVRILMEYEN_MEDYA = {".mp3", ".mp4", ".webm", ".ogg"}


def varliklari_kopyala(kok: Path, dil: str) -> int:
    """Kok dizindeki statik varliklari dil dizinine kopyalar (medya ve site disi klasorler haric)."""
    hedef_kok = kok / dil
    sayi = 0
    # onceden yanlislikla kopyalanmis klasorleri temizle (or. data/ ve functions/)
    for ad in KOPYALAMA_DISLA:
        fazlalik = hedef_kok / ad
        if fazlalik.exists():
            shutil.rmtree(fazlalik)
    for dosya in kok.rglob("*"):
        if not dosya.is_file():
            continue
        rel = dosya.relative_to(kok)
        if rel.parts[0] in DIL_DIZINLERI or rel.parts[0] in KOPYALAMA_DISLA:
            continue
        if dosya.suffix.lower() in CEVRILMEYEN_MEDYA or dosya.suffix.lower() == ".html":
            continue
        if dosya.suffix.lower() not in KOPYALA_EKLERI:
            continue
        hedef = hedef_kok / rel
        hedef.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dosya, hedef)
        sayi += 1
    return sayi


def esdegerlik_denetle(kok: Path, diller: list[str]) -> dict:
    """TR sayfa ile dil sayfasi arasinda YAPISAL esdegerlik denetimi.

    Amac: "dil sayfasi Turkce sayfanin birebir kopyasi, sadece metni cevrilmis" iddiasini
    olcmek. CSS bloklari, JS bloklari ve class kumesi ayni olmali; bagil varliklar yerinde olmali.
    Bilincli farklar: dil degistirici blogu ve eski Puter ceviri bloku.
    """
    rapor = {"karsilastirilan_sayfa": 0, "css_farki": [], "js_farki": [], "sinif_farki": [],
             "eksik_varlik": []}
    style_re = re.compile(r"<style\b.*?</style>", re.S | re.I)
    script_re = re.compile(r"<script\b.*?</script>", re.S | re.I)
    for dil in diller:
        for rel in turkce_sayfalar(kok):
            dosya = kok / dil / rel
            if not dosya.exists():
                continue
            tr = (kok / rel).read_text(encoding="utf-8", errors="replace")
            d = dosya.read_text(encoding="utf-8", errors="replace")
            rapor["karsilastirilan_sayfa"] += 1

            tr_css = [b for b in style_re.findall(tr) if "dil-linkler" not in b]
            d_css = [b for b in style_re.findall(d) if "dil-linkler" not in b]
            if tr_css != d_css:
                rapor["css_farki"].append(f"{dil}/{rel}")

            tr_js = [b for b in script_re.findall(tr) if "dil-sec" not in b and "ld+json" not in b]
            d_js = [b for b in script_re.findall(d) if "ld+json" not in b]
            if tr_js != d_js:
                rapor["js_farki"].append(f"{dil}/{rel}")

            tr_sinif = set(re.findall(r'class="([^"]+)"', tr))
            d_sinif = set(re.findall(r'class="([^"]+)"', d))
            # dil degistirici blogunun kendi siniflari (bilincli fark)
            for s in ("dil-linkler", "aktif"):
                tr_sinif.discard(s)
                d_sinif.discard(s)
            if tr_sinif != d_sinif:
                rapor["sinif_farki"].append(
                    f"{dil}/{rel} (+{sorted(d_sinif - tr_sinif)[:3]} / -{sorted(tr_sinif - d_sinif)[:3]})"
                )

            # bagil varliklar: script/style icerigi haric (JS sablonlari sahte eslesme uretir)
            govde = script_re.sub("", d)
            govde = re.sub(r"<style\b.*?</style>", "", govde, flags=re.S | re.I)
            for h in re.findall(r'(?:href|src)="([^"]+)"', govde):
                if h.startswith(("http", "//", "#", "mailto", "data:", "javascript:")):
                    continue
                hedef = dosya.parent / h.split("#")[0].split("?")[0]
                if not hedef.exists():
                    rapor["eksik_varlik"].append(f"{dil}/{rel} -> {h}")
    return rapor


def sitemap_dogrula(kok: Path, diller: list[str]) -> dict:
    """Sitemap tam mi? Her sayfa kayitli mi, kayitli her adres gercek mi?

    Amac: 'bilinmeyen yol kalmasin' — sitemap'teki her <loc> var olan bir dosyaya
    karsilik gelmeli, her HTML sayfa da (TR + dil) sitemap'te bulunmali.
    """
    from collections import Counter

    yol = kok / "sitemap.xml"
    if not yol.exists():
        return {"durum": "sitemap.xml yok"}
    xml = yol.read_text(encoding="utf-8")
    loclar = re.findall(r"<loc>([^<]+)</loc>", xml)
    kayitli = set(loclar)

    def dosya_var(u: str) -> bool:
        if SITE_URL not in u:
            return True
        kismi = u.split(SITE_URL, 1)[1].split("#")[0].split("?")[0]
        adaylar = [kismi, kismi + ".html", kismi + "index.html"] if kismi else ["index.html"]
        return any((kok / a).exists() for a in adaylar)

    yok = [u for u in loclar if not dosya_var(u)]
    tekrar = [u for u, n in Counter(loclar).items() if n > 1]

    eksik_kayit, dil_eksik = [], []
    for rel in turkce_sayfalar(kok):
        if SITE_URL + seo_yol(rel) not in kayitli and SITE_URL + rel.as_posix() not in kayitli:
            eksik_kayit.append(seo_yol(rel) or "/")
        for dil in diller:
            if f"{SITE_URL}{dil}/{seo_yol(rel)}" not in kayitli:
                dil_eksik.append(f"{dil}/{seo_yol(rel)}")

    return {
        "sitemap_loc": len(loclar),
        "benzersiz_loc": len(kayitli),
        "dosyasi_olmayan_loc": yok[:20],
        "tekrarlanan_loc": tekrar[:10],
        "sitemap_te_olmayan_turkce_sayfa": eksik_kayit[:20],
        "sitemap_te_olmayan_dil_sayfasi": dil_eksik[:20],
        "eksik_sayilar": {"dosyasi_olmayan": len(yok), "tekrar": len(tekrar),
                          "turkce_eksik": len(eksik_kayit), "dil_eksik": len(dil_eksik)},
        "hreflang_alternatif_satiri": len(re.findall(r'hreflang="', xml)),
    }


def _modelleri_al(url: str, anahtar: str, timeout: float = 30) -> list:
    """Saglayicinin /models ucundan erisilebilir model kimliklerini ceker."""
    istek = urllib.request.Request(url, headers={"Authorization": f"Bearer {anahtar}"})
    with urllib.request.urlopen(istek, timeout=timeout) as yanit:
        veri = json.loads(yanit.read().decode("utf-8"))
    ham = veri.get("data") or veri.get("models") or []
    cikti = []
    for m in ham:
        if isinstance(m, dict):
            kim = m.get("id") or m.get("model") or m.get("name")
            if kim:
                cikti.append(str(kim))
        elif isinstance(m, str):
            cikti.append(m)
    return cikti


def saglayici_test() -> int:
    """Her saglayiciye ve modeline TEK kisa istek atar; durum kodunu ve hata mesajini yazar.

    Amac: "hangi anahtar/hangi model su an calisiyor?" sorusunu kota yakmadan yanitlamak.
    Hicbir dosya yazmaz, onbellek kullanmaz.
    """
    c = Cevirmen(provider="llm")
    uclar = c._uclari_kur()
    if not uclar:
        print("Hic saglayici yapilandirilmamis (env bos).")
        return 2

    def ev_ad(url):
        if "amd" in url:
            return "AMD"
        if "z.ai" in url:
            return "Z.AI"
        if "groq" in url:
            return "Groq/ALT"
        return "OpenRouter"

    # 0) Her saglayicinin GERCEK model listesi (destekliyorsa)
    deepl_anahtar = os.environ.get("DEEPL_API_KEY") or os.environ.get("DEEPL_KEY")
    if deepl_anahtar:
        # Anahtarin hangi plana ait oldugunu teshis et: ucretsiz anahtarlar ':fx' ile biter.
        k = deepl_anahtar.strip()
        taban_deepl = os.environ.get("DEEPL_BASE_URL", "https://api-free.deepl.com").rstrip("/")
        print(f"--- DeepL anahtar teshisi: uzunluk={len(k)} son3='...{k[-3:]}' fx_eki={k.endswith(':fx')} ---")
        for uc in ("https://api-free.deepl.com", "https://api.deepl.com"):
            try:
                istek = urllib.request.Request(uc + "/v2/usage", headers={"Authorization": f"DeepL-Auth-Key {k}"})
                with urllib.request.urlopen(istek, timeout=30) as y:
                    u = json.loads(y.read().decode("utf-8"))
                print(f"    {uc}/v2/usage: OK :: {json.dumps(u, ensure_ascii=False)[:200]}")
            except urllib.error.HTTPError as h:
                try:
                    govde = h.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    govde = ""
                print(f"    {uc}/v2/usage: HTTP {h.code} :: {govde}")
            except Exception as h:
                print(f"    {uc}/v2/usage: {h}")
        # DeepL'in destekledigi dil kodlarini sor: hedef kodlarimiz (EN-GB/DE/RU/ZH) gecerli mi?
        for tip in ("target", "source"):
            try:
                istek = urllib.request.Request(f"{taban_deepl}/v2/languages?type={tip}",
                                               headers={"Authorization": f"DeepL-Auth-Key {k}"})
                with urllib.request.urlopen(istek, timeout=30) as y:
                    dil_listesi = json.loads(y.read().decode("utf-8"))
                kodlar = [d.get("language") for d in dil_listesi]
                print(f"    {tip} diller ({len(kodlar)}): {kodlar[:40]}")
                if tip == "target":
                    for kod in ("EN-GB", "DE", "RU", "ZH", "ZH-HANS"):
                        print(f"        {kod}: {'VAR' if kod in kodlar else 'YOK'}")
            except Exception as h:
                print(f"    {tip} diller: alinamadi ({h})")
        bas = time.time()
        try:
            cev = Cevirmen._deepl(["BIST 30 gunluk rapor: destek ve direnc"], "en")
            print(f"    ceviri testi: OK ({(time.time()-bas)*1000:.0f} ms) -> {str(cev[0])[:60]}")
        except Exception as h:
            print(f"    ceviri testi: HATA ({h})")

    for url, anahtar, _m in uclar:
        ev = ev_ad(url)
        if "groq" in url:
            print(f"--- {ev}: model listesi sorgulanmiyor (403/engelli)")
            continue
        models_url = url.replace("/chat/completions", "/models")
        try:
            liste = _modelleri_al(models_url, anahtar)
            ilgi = [m for m in liste if any(k in m.lower() for k in ("glm", "deepseek", "qwen", "flash"))]
            goster = ilgi if ilgi else liste
            print(f"--- {ev}: {len(liste)} model erisilebilir ---")
            for m in goster[:45]:
                print("    ", m)
            if len(goster) > 45:
                print(f"     ... (+{len(goster) - 45} model daha)")
        except urllib.error.HTTPError as h:
            print(f"--- {ev}: model listesi alinamadi (HTTP {h.code})")
        except Exception as h:
            print(f"--- {ev}: model listesi alinamadi ({h})")

    sistem = "Cevirmen. Sana verilen JSON dizisini ayni uzunlukta cevrilmis JSON dizisi olarak dondur."
    dilim = ["BIST 30 gunluk rapor: destek ve direnc seviyeleri"]
    for url, anahtar, modeller in uclar:
        ev = ev_ad(url)
        print(f"--- {ev} ({url.split('/')[2]}) ---")
        for model in modeller:
            baslangic = time.time()
            kutu = []
            try:
                sonuc = Cevirmen._llm_istek(url, anahtar, model, sistem, dilim, 30, kutu)
                sure = (time.time() - baslangic) * 1000
                ok = isinstance(sonuc, list) and len(sonuc) == 1
                tok = ""
                if kutu:
                    u = kutu[-1]
                    tok = (f" | token: giris={u.get('prompt_tokens')} cikis={u.get('completion_tokens')} "
                           f"toplam={u.get('total_tokens')}")
                print(f"   {model}: OK ({sure:.0f} ms){tok} -> {str(sonuc[0])[:60] if ok else sonuc}")
            except urllib.error.HTTPError as h:
                sure = (time.time() - baslangic) * 1000
                try:
                    govde = h.read().decode("utf-8", errors="replace")[:160]
                except Exception:
                    govde = ""
                print(f"   {model}: HTTP {h.code} ({sure:.0f} ms) :: {govde}")
            except Exception as h:
                sure = (time.time() - baslangic) * 1000
                print(f"   {model}: HATA ({sure:.0f} ms) :: {h}")
    return 0


def dil_agac_kumesi(kok: Path, hedef_liste) -> set[str]:
    """Dil agacinda bulunacak TUM dosyalarin (sayfa + kopyalanan varlik) kumesi.

    Onarim karari dosya sisteminin o anki haline DEGIL, bu kumeye bakar:
    sayfalar sirayla yazildigi icin 'henuz yazilmamis dosya' yanlis onarim uretirdi.
    """
    kume = {str(Path(r)) for r in hedef_liste}
    for dosya in kok.rglob("*"):
        if not dosya.is_file():
            continue
        rel = dosya.relative_to(kok)
        if rel.parts[0] in DIL_DIZINLERI or rel.parts[0] in (".git", "web", "node_modules"):
            continue
        if dosya.suffix.lower() in CEVRILMEYEN_MEDYA or dosya.suffix.lower() == ".html":
            continue
        if dosya.suffix.lower() not in KOPYALA_EKLERI:
            continue
        kume.add(str(rel))
    return kume


def medya_kumesi(kok: Path) -> set[str]:
    """Dil agacina KOPYALANMAYAN medya dosyalarinin Turkce yollari (or. mp3)."""
    kume = set()
    for dosya in kok.rglob("*"):
        if not dosya.is_file():
            continue
        rel = dosya.relative_to(kok)
        if rel.parts[0] in DIL_DIZINLERI or rel.parts[0] in (".git", "web", "node_modules"):
            continue
        if dosya.suffix.lower() in CEVRILMEYEN_MEDYA:
            kume.add(str(rel))
    return kume


def baglantilari_onar(html: str, rel: Path, agac: set[str], medya: set[str] | None = None) -> tuple[str, int, int]:
    """Dil agacinda cozulemeyen bagil baglantilari onarir.

    1) '../' oneki eksik olan baglantilar bir ust dizine tasinir
       (or. haftasonu/*.html -> 'index.html'; bu Turkce tarafta da kirik).
    2) Dil agacina kopyalanmayan medya (mp3) Turkce mutlak adrese cevrilir.
    """
    medya = medya or set()
    sayac = {"n": 0, "medya": 0}

    def coz(hedef: str) -> str:
        y = os.path.normpath(os.path.join(os.path.dirname(str(rel)), hedef))
        return str(Path(y))

    def onar(m):
        nitelik, hedef = m.group(1), m.group(2)
        if not hedef or hedef.startswith(("http", "//", "#", "mailto:", "data:", "javascript:")):
            return m.group(0)
        parcalar = hedef.split("#")[0].split("?")
        sorgu = ("?" + parcalar[1]) if len(parcalar) > 1 else ""
        saf = parcalar[0]
        if not saf:
            return m.group(0)
        if coz(saf) in agac:
            return m.group(0)
        if coz("../" + saf) in agac:
            sayac["n"] += 1
            return f'{nitelik}="../{hedef}"'
        if coz(saf) in medya:
            sayac["medya"] += 1
            return f'{nitelik}="{SITE_URL}{coz(saf).replace(chr(92), "/")}{sorgu}"'
        return m.group(0)

    return re.sub(r'(href|src)="([^"]+)"', onar, html), sayac["n"], sayac["medya"]


def seo_yol(rel: Path) -> str:
    """Sayfanin site kokune gore SEO yolu: site artik .html'siz canonical kullaniyor.

    index.html            -> ''
    hisse/index.html      -> 'hisse/'
    teknik-analiz.html    -> 'teknik-analiz'
    """
    p = rel.as_posix()
    if p.endswith("index.html"):
        return p[: -len("index.html")]
    if p.endswith(".html"):
        return p[: -5]
    return p


def seo_url_formu(html: str) -> str:
    """canonical / og:url / hreflang / JSON-LD url adreslerini .html'siz forma cevirir
    (Turkce sayfalarin 21.09.2026'dan beri kullandigi bicim)."""

    def temizle(u: str) -> str:
        if u.endswith("/index.html"):
            u = u[: -len("index.html")]
        elif u.endswith("index.html"):
            u = u[: -len("index.html")] + "/"
        return re.sub(r"\.html(?=$|[#?])", "", u)

    desenler = (
        r'(<link rel="canonical" href=")([^"]+)(")',
        r'(<meta property="og:url" content=")([^"]+)(")',
        r'(<link rel="alternate" hreflang="[^"]+" href=")([^"]+)(")',
        r'("url"\s*:\s*")([^"]+)(")',
    )
    for d in desenler:
        html = re.sub(d, lambda m: m.group(1) + temizle(m.group(2)) + m.group(3), html)
    return html


def dil_sayfalari_yaz(kok: Path, diller: list[str], sayfa_listesi: list[Path] | None,
                      cevirmen: Cevirmen, onarim: bool = True) -> dict:
    hedef_liste = sayfa_listesi or turkce_sayfalar(kok)
    agac = dil_agac_kumesi(kok, hedef_liste)
    medya = medya_kumesi(kok)
    ozet = {"sayfa": 0, "dil": {}, "aktarilan_metin": 0, "onarilan_baglanti": 0, "medya_baglantisi": 0}

    # En az cevrilmis dil once islenir: sure butcesi dolarsa tum diller dengeli ilerler.
    def _kapsam(d: str) -> int:
        return sum(1 for k in getattr(cevirmen, "onbellek", {}) if str(k).startswith(d + ":"))

    try:
        diller = sorted(diller, key=_kapsam)
        print("[i18n] dil sirasi (en az cevrilmis once): " +
              ", ".join(f"{d}={_kapsam(d)}" for d in diller), flush=True)
    except Exception:
        pass

    for dil in diller:
        n = 0
        print(f"[i18n] --- {dil}: {len(hedef_liste)} sayfa cevirilecek ---", flush=True)
        for rel in hedef_liste:
            kaynak = kok / rel
            ham = kaynak.read_text(encoding="utf-8", errors="replace")
            yeni = sayfayi_cevir(ham, dil, cevirmen, diller, rel)
            # hreflang yer tutuculari: her sayfanin kendi yoluna gore doldurulur
            for kod in DILLER:
                yeni = yeni.replace("@@URL_" + kod + "@@", f"{SITE_URL}{kod + '/' if kod != 'tr' else ''}{rel.as_posix()}")
            yeni = seo_url_formu(yeni)
            if onarim:
                yeni, onarilan, medya_n = baglantilari_onar(yeni, rel, agac, medya)
                ozet["onarilan_baglanti"] += onarilan
                ozet["medya_baglantisi"] += medya_n
            hedef = kok / dil / rel
            hedef.parent.mkdir(parents=True, exist_ok=True)
            hedef.write_text(yeni, encoding="utf-8")
            n += 1
            if n % 20 == 0:
                print(f"[i18n] {dil}: {n}/{len(hedef_liste)} sayfa yazildi", flush=True)
        ozet["dil"][dil] = n
        ozet["sayfa"] += n
    return ozet


# ----------------------------------------------------------------------------
# 6) SITEMAP ALTERNATIFLERI (opt-in)
# ----------------------------------------------------------------------------


def _etiket(blok: str, ad: str) -> str | None:
    m = re.search(rf"<{ad}>([^<]+)</{ad}>", blok)
    return m.group(1).strip() if m else None


def _url_bloku(loc: str, attr: dict, alternatifler: list[str]) -> str:
    parcalar = [f"  <url>", f"    <loc>{loc}</loc>"]
    if attr.get("lastmod"):
        parcalar.append(f"    <lastmod>{attr['lastmod']}</lastmod>")
    if attr.get("changefreq"):
        parcalar.append(f"    <changefreq>{attr['changefreq']}</changefreq>")
    if attr.get("priority"):
        parcalar.append(f"    <priority>{attr['priority']}</priority>")
    parcalar.extend(alternatifler)
    parcalar.append("  </url>")
    return "\n".join(parcalar)


def sitemap_guncelle(kok: Path, diller: list[str]) -> dict:
    """Sitemap'i "her sayfa kayitli olsun" ilkesine gore yeniden kurar.

    - Mevcut Turkce kayitlarin lastmod/changefreq/priority degerleri korunur.
    - Sitemap'te OLMAYAN Turkce sayfalar eklenir (or. haftasonu-egitimi.html).
    - Her kayit icin hreflang alternatifleri + her dil icin ayri <url> blogu yazilir.
    - Islem idempotent: tekrar calistirildiginda ciktii ayni kalir.
    """
    yol = kok / "sitemap.xml"
    if not yol.exists():
        return {"durum": "sitemap.xml yok"}
    xml = yol.read_text(encoding="utf-8")

    # 1) mevcut kayitlari oku
    tr_kayitlar: dict[str, dict] = {}
    dis_bloklar: list[str] = []
    for blok in re.findall(r"<url>.*?</url>", xml, re.S):
        loc = _etiket(blok, "loc")
        if not loc:
            continue
        attr = {k: _etiket(blok, k) for k in ("lastmod", "changefreq", "priority")}
        if SITE_URL in loc:
            kismi = loc.split(SITE_URL, 1)[1]
            if kismi.split("/")[0] in DILLER:
                continue  # dil kaydi; yeniden uretilecek
            tr_kayitlar[kismi] = attr
        else:
            dis_bloklar.append(blok)

    # 2) eksik Turkce sayfalari ekle
    son_lastmod = max((a.get("lastmod") or "" for a in tr_kayitlar.values()), default="") \
        or time.strftime("%Y-%m-%d")
    eklenen: list[str] = []
    for rel in turkce_sayfalar(kok):
        sy = seo_yol(rel)
        if sy not in tr_kayitlar:
            tr_kayitlar[sy] = {"lastmod": son_lastmod, "changefreq": "weekly", "priority": "0.6"}
            eklenen.append(sy or "/")

    # 3) yeniden yaz
    bloklar: list[str] = []
    dil_sayisi = 0
    for sy in sorted(tr_kayitlar):
        attr = tr_kayitlar[sy]
        alternatifler = [
            f'    <xhtml:link rel="alternate" hreflang="{DILLER[k]["hreflang"]}" '
            f'href="{SITE_URL}{k + "/" if k != "tr" else ""}{sy}"/>'
            for k in ["tr"] + diller
        ]
        alternatifler.append(
            f'    <xhtml:link rel="alternate" hreflang="x-default" href="{SITE_URL}{sy}"/>'
        )
        bloklar.append(_url_bloku(f"{SITE_URL}{sy}", attr, alternatifler))
        for d in diller:
            bloklar.append(_url_bloku(f"{SITE_URL}{d}/{sy}", attr, alternatifler))
            dil_sayisi += 1
    bloklar.extend("  " + b.strip() for b in dis_bloklar)

    yeni = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(bloklar)
        + "\n</urlset>\n"
    )
    yol.write_text(yeni, encoding="utf-8")
    return {
        "durum": "guncellendi",
        "turkce_url": len(tr_kayitlar),
        "dil_url": dil_sayisi,
        "toplam_loc": len(tr_kayitlar) * (1 + len(diller)) + len(dis_bloklar),
        "eklenen_turkce": eklenen,
    }


# ----------------------------------------------------------------------------
# 7) DOGRULAMA
# ----------------------------------------------------------------------------


def dogrula(kok: Path, diller: list[str]) -> dict:
    rapor = {"kirik_baglanti": [], "hreflang_eksik": [], "canonical_hatali": [], "canonical_baska_sayfa": [], "uretilmemis": [], "kontrol": 0}
    tr_sayfalar = {str(r): r for r in turkce_sayfalar(kok)}
    for dil in diller:
        for rel in tr_sayfalar.values():
            dosya = kok / dil / rel
            if not dosya.exists():
                rapor["uretilmemis"].append(f"{dil}/{rel}")
                continue
            html = dosya.read_text(encoding="utf-8", errors="replace")
            rapor["kontrol"] += 1
            if 'hreflang="x-default"' not in html or f'hreflang="{DILLER[dil]["hreflang"]}"' not in html:
                rapor["hreflang_eksik"].append(f"{dil}/{rel}")
            beklenen = f'{SITE_URL}{dil}/{seo_yol(rel)}'
            kok_url = f'{SITE_URL}{dil}/'
            mc = re.search(r'rel="canonical" href="([^"]+)"', html)
            if not mc:
                rapor["canonical_hatali"].append(f"{dil}/{rel}: canonical etiketi yok")
            elif not mc.group(1).startswith(kok_url):
                rapor["canonical_hatali"].append(f"{dil}/{rel} -> {mc.group(1)}")
            elif mc.group(1) not in (beklenen, kok_url):
                # Tur tarafinda bilincli olarak hub sayfaya isaret ediyor olabilir
                rapor["canonical_baska_sayfa"].append(f"{dil}/{rel} -> {mc.group(1)}")
            # ic baglantilar (script/style icerigi haric — JS icindeki "href" metinleri sahte eslesme uretir)
            govde = re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)
            govde = re.sub(r"<style\b.*?</style>", "", govde, flags=re.S | re.I)
            for href in re.findall(r'(?:href|src)="([^"]+)"', govde):
                if href.startswith(("//", "mailto:", "data:", "#", "javascript:")):
                    continue
                if href.startswith(SITE_URL):  # site mutlak adresi -> uretilen dosyaya karsilik gelmeli
                    kismi = href[len(SITE_URL):].split("#")[0].split("?")[0]
                    adaylar = [kismi, kismi + ".html", kismi + "index.html"]
                    if not any((kok / a).exists() for a in adaylar if a):
                        rapor["kirik_baglanti"].append(f"{dil}/{rel} -> {href}")
                    continue
                if href.startswith("http"):
                    continue
                hedef = (dosya.parent / href.split("#")[0].split("?")[0]).resolve()
                if not hedef.exists() and href.split("#")[0].split("?")[0]:
                    rapor["kirik_baglanti"].append(f"{dil}/{rel} -> {href}")
    return rapor


# ----------------------------------------------------------------------------
# 8) ANA AKIS
# ----------------------------------------------------------------------------


def uretim_calistir(kok: str | Path = ".", diller=("en", "de", "ru", "zh"), provider: str = "llm",
                    sitemap: bool = True, onarim: bool = True) -> dict:
    """bot.py icinden tek satirda cagrilacak giris noktasi.

    Turkce sayfalar yazilmaz; yalnizca {dil}/... altina uretilir. Uretim sonunda
    Turkce dosyalarin sha256'si tekrar kontrol edilir; degismisse hata yukseltilir.
    """
    kok = Path(kok).resolve()
    diller = list(diller)
    onceki = tr_hashleri(kok)
    cevirmen = Cevirmen(provider=provider, onbellek_yolu=str(kok / "i18n-cache.json"))
    kopya = 0
    for d in diller:  # varliklar once kopyalanir: onarim karari bu kumeye dayanir
        kopya += varliklari_kopyala(kok, d)
    ozet = dil_sayfalari_yaz(kok, diller, None, cevirmen, onarim=onarim)
    cevirmen.kaydet()
    sm = sitemap_guncelle(kok, diller) if sitemap else None

    sonraki = tr_hashleri(kok)
    degisen = [k for k in onceki if onceki[k] != sonraki.get(k)]
    if degisen:
        raise RuntimeError("[i18n] Turkce sayfalar degisti: " + ", ".join(degisen[:5]))

    return {
        "diller": ozet["dil"],
        "sayfa": ozet["sayfa"],
        "kopyalanan_varlik": kopya,
        "onarilan_baglanti": ozet["onarilan_baglanti"],
        "medya_baglantisi": ozet["medya_baglantisi"],
        "yeni_ceviri": cevirmen.yeni,
        "onbellekten": cevirmen.onbellekten,
        "sozlukten": cevirmen.sozlukten,
        "devre_disi_saglayici": sorted(cevirmen.devre_disi),
        "cevrilmeyen_parca": cevirmen.atlanan_parca,
        "sure_doldu": cevirmen.sure_doldu,
        "sitemap": sm,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="borsa-raporlari cok dilli katman")
    ap.add_argument("--kok", default=".", help="depo koku (varsayilan: .)")
    ap.add_argument("--diller", default="en,de,ru,zh")
    ap.add_argument("--provider", default="mock", choices=["mock", "llm", "deepl", "off"])
    ap.add_argument("--sayfalar", default="", help="virgulle ayrilmis sayfa listesi (varsayilan: hepsi)")
    ap.add_argument("--sitemap", action="store_true", help="sitemap.xml'e dil alternatiflerini ekle")
    ap.add_argument("--dogrula", action="store_true", help="sadece dogrula, yazma")
    ap.add_argument("--esdeger", action="store_true",
                    help="TR sayfa ile dil sayfasi yapisal olarak ayni mi (CSS/JS/class)")
    ap.add_argument("--sitemap-dogrula", action="store_true",
                    help="sitemap tam mi: her sayfa kayitli mi, her kayit gercek mi")
    ap.add_argument("--saglayici-test", action="store_true",
                    help="her saglayiciya/modeline tek istek atip durumunu gosterir")
    ap.add_argument("--temizle", action="store_true", help="uretilmis dil dizinlerini sil")
    ap.add_argument("--zorla", action="store_true", help="Turkce degisiklik korumasini atla")
    ap.add_argument("--varliklari-kopyala", action="store_true", default=True,
                    help="css/js/json gibi kok varliklari dil dizinlerine kopyala (varsayilan: acik)")
    args = ap.parse_args()

    kok = Path(args.kok).resolve()
    diller = [d.strip() for d in args.diller.split(",") if d.strip()]

    if args.temizle:
        for d in diller:
            hedef = kok / d
            if hedef.exists():
                shutil.rmtree(hedef)
                print(f"[i18n] silindi: {d}/")
        return 0

    tr_once = tr_hashleri(kok)

    if args.dogrula:
        rapor = dogrula(kok, diller)
        print(json.dumps(rapor, ensure_ascii=False, indent=2))
        return 0 if not (rapor["kirik_baglanti"] or rapor["hreflang_eksik"] or rapor["canonical_hatali"]) else 1

    if args.esdeger:
        rapor = esdegerlik_denetle(kok, diller)
        print(json.dumps(rapor, ensure_ascii=False, indent=2))
        temiz = not (rapor["css_farki"] or rapor["js_farki"] or rapor["sinif_farki"] or rapor["eksik_varlik"])
        return 0 if temiz else 1

    if args.saglayici_test:
        return saglayici_test()

    if args.sitemap_dogrula:
        rapor = sitemap_dogrula(kok, diller)
        print(json.dumps(rapor, ensure_ascii=False, indent=2))
        eksik = rapor.get("eksik_sayilar", {})
        return 0 if not any(eksik.values()) else 1

    sayfa_listesi = None
    if args.sayfalar:
        sayfa_listesi = [Path(s.strip()) for s in args.sayfalar.split(",") if s.strip()]

    cevirmen = Cevirmen(provider=args.provider, onbellek_yolu=str(kok / "i18n-cache.json"))

    kopya = 0
    if args.varliklari_kopyala and not sayfa_listesi:
        for d in diller:  # varliklar once kopyalanir (onarim karari bu kumeye dayanir)
            kopya += varliklari_kopyala(kok, d)

    ozet = dil_sayfalari_yaz(kok, diller, sayfa_listesi, cevirmen)

    cevirmen.kaydet()

    if args.sitemap:
        print("[i18n] sitemap:", json.dumps(sitemap_guncelle(kok, diller), ensure_ascii=False))

    # --- Turkce koruma kontrolu ---
    tr_sonra = tr_hashleri(kok)
    degisen = [k for k in tr_once if tr_once[k] != tr_sonra.get(k)]
    yeni_tr = [k for k in tr_sonra if k not in tr_once]
    print(json.dumps({
        "diller": ozet["dil"],
        "devre_disi_saglayici": sorted(cevirmen.devre_disi),
        "cevrilmeyen_parca": cevirmen.atlanan_parca,
        "sure_doldu": cevirmen.sure_doldu,
        "toplam_sayfa": ozet["sayfa"],
        "kopyalanan_varlik": kopya,
        "onarilan_baglanti": ozet["onarilan_baglanti"],
        "medya_baglantisi": ozet["medya_baglantisi"],
        "sozlukten": cevirmen.sozlukten,
        "onbellekten": cevirmen.onbellekten,
        "yeni_ceviri": cevirmen.yeni,
        "turkce_degisen": degisen,
        "turkce_yeni_dosya": yeni_tr,
    }, ensure_ascii=False, indent=2))

    if degisen and not args.zorla:
        print("[i18n] DURDU: Turkce sayfalar degismis! Bu bir hatadir.", file=sys.stderr)
        return 2
    print("[i18n] OK — Turkce sayfalar degismedi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
