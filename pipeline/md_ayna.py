# -*- coding: utf-8 -*-
"""HTML -> Markdown ayna uretici (LLM/ajan okumasi icin "yalniz asil icerik").

2026-11-04 ikinci revizyon (dis incelemeler: Claude, ChatGPT, Hy4):
- Bozukluk kok-nedeni tek yerde: HTML dugum arasindaki bosluk bilgisi
  artik her parcayla tasinir (once/sonu). Tablo hucresi "".join ile
  birlestigi icin "50,02(onceki", paragraf " ".join ile birlestigi icin
  "] 'un" / "( [PGSUS]" gibi yapay bosluklar olusuyordu; ikisi de
  akilli birlestirmeyle duzeltildi (Turkce ek kesmesi, noktalama,
  parantez-acilisi, em-dash ve emoji sinirlari dahil).
- pano-veri (10 KPI karti) ve pano-man (yuksekler/dusenler, sinyal
  dagilimi, sektor ortalamalari) artik ATLANMAZ — makine-okunur en yogun
  veri md'de olmali (Hy4 1. bulgu). pano-hucre "- Etiket: Deger"
  maddesine map'lenir.
- pano-baslik ("📊 Gunun Panosu", "📅 Gunluk Ajanda") "###" basliga
  map'lenir; insan sayfasinda gorunum degismez.
- Rapor paragraflarindaki kalin-altbaslik + <br> kalibi (§4) "####"
  basliga doner; paragrafa yapismaz.
- h2/h3'ten hemen sonra AYNI metni tekrar eden baslik/strong-paragraf
  atlanir (6. Risk tekrari ve benzerleri — uretim tarafindan gelmeye
  devam edebilecegi icin jenerik).
- Ic linkler artik .md kopyalara gider (ajan md zincirinde kalir);
  dil kopyalari (en/de/ru/zh) .md'siz oldugundan onlara dokunulmaz.
- H1 ve front matter title tarihli olur (# Gunluk Piyasa Raporu —
  4 Kasim 2026); hero-meta satiri (badge yigini) atlanir.
Asistan kartlari, ticker, ses butonlari vb. gurultu atlanmaya devam
eder (embed.py _MetinCikarici ile ayni kume mantigi).

2026-11-04 üçüncü revizyon (dört dış denetim + kullanıcı istekleri):
- pano-man satırları (▲/▼/📣/🏷️) madde olur; listeden sonra boş satırsız
  gelen div metni lazy-continuation ile maddeye yapışamaz.
- sepet-grafik kartı md'ye gelir ("BIST 30 sepeti — son 19 gün: -%15,01").
- Başlık seviyeleri kırpılır (# sonrası ### → ##; yukarı çekilmez).
- Tablolar sütun-hizalı doldurulur; başlıksız tablonun ilk satırı iki kez
  basılmaz; takvim grup başlığındaki çift tarih tekilleşir.
- Klasör-kök ("../hisse/") ve sayfaya-göreli ("ISCTR") iç linkler doğru
  .md'ye çözülür; parçalı hedefler (#) HTML'e bırakılır.
- Front matter: updated/symbols/data; dil kolu chrome'u EN (en) +
  machine_translated; üstte tek satırlık tüketim notu.
- İşaretli yüzde biçimleri tek biçime çekilir; ajanda ondalıkları TR'ye
  çevrilir (yalnız tam 2 kesirli hane); açılış tırnağı önceki söze
  yapışmaz.
"""
import os
import re
import sys
from datetime import date, timedelta
from html.parser import HTMLParser

ATLANAN = {"script", "style", "noscript", "svg", "head", "nav", "header",
           "footer", "aside", "button", "form", "select", "option",
           "label", "template"}
# 2026-11-04: "pano-veri" ve "pano-man" atlama listesinden CIKARILDI
# (veri kaybi — Hy4 bulgusu); hero-meta eklendi. 2026-11-04 (üçüncü
# revizyon): "sepet-grafik" de çıkarıldı — kartın "BIST 30 sepeti — son 19
# gün -%15,01" metni md'ye akmalıydı (dört denetimin ortak bulgusu); svg
# zaten ATLANAN'da.
ATLA_SINIF = ("hesap", "asistan", "site-widgets", "live-clock",
              "interactive-box", "arama-kutusu", "radyo",
              "ticker", "paylas", "sesli-bulten", "hero-meta")

# Kök sayfalar (uzantısız adlar; .html okunur, .md yazılır)
KOK_SAYFALAR = [
    "index", "raporlar", "derin-analiz", "makro-analiz", "teknik-analiz",
    "borsapy-analiz", "sinyal-karnesi", "portfoy", "haftasonu",
    "haftasonu-egitimi", "haberler", "sirket-haberleri", "takvim",
    "sozluk", "terimler", "muhasebe-terimleri", "temettu",
]
# Klasör ağaçları (içindeki tüm .html'ler; .md kopyası aynı yere yazılır)
KLASORLER = [
    "raporlar", "derin-analiz", "makro-analiz", "haftasonu",
    "haftasonu-egitimi", "hisse", "terimler", "muhasebe-terimleri",
]

SITE_URL = "https://borsa-raporlari.pages.dev"

# --- Çok dilli kol (2026-11-04, architect S9 ölçek sınırı: tarihli
# klasörlerde yalnız SON 30 GÜN) ---
# SABİT eşleme tabloları: TR adı → dil adı. hreflang_ekle.py (depo kökü)
# AYNI adları kullanır; iki script bilinçli bağımsızdır (import yok) —
# bir tarafı değiştirirsen öbürünü de değiştir. Adlar dil klasörlerinin
# ls çıktısıyla doğrulanmıştır (en/de/ru/zh; olmayan sayfa fail-open atlanır).
DILLER = ("en", "de", "ru", "zh")
KOK_ESLE = {
    "en": {"index": "index", "raporlar": "reports", "derin-analiz": "deep-analysis",
           "makro-analiz": "macro-analysis", "teknik-analiz": "technical-scan",
           "sinyal-karnesi": "signal-scorecard", "portfoy": "portfolio",
           "haftasonu": "weekend", "haberler": "news", "takvim": "calendar",
           "sozluk": "glossary", "terimler": "terms",
           "muhasebe-terimleri": "accounting-terms", "hisse": "stocks"},
    "de": {"index": "index", "raporlar": "berichte", "derin-analiz": "tiefenanalyse",
           "makro-analiz": "makroanalyse", "teknik-analiz": "technischer-scan",
           "sinyal-karnesi": "signal-bilanz", "portfoy": "portfolio",
           "haftasonu": "wochenende", "haberler": "nachrichten", "takvim": "kalender",
           "sozluk": "glossar", "terimler": "begriffe",
           "muhasebe-terimleri": "rechnungslegungsbegriffe", "hisse": "aktien"},
    "ru": {"index": "index", "raporlar": "otchety", "derin-analiz": "glubokiy-analiz",
           "makro-analiz": "makroanaliz", "teknik-analiz": "tekhnicheskiy-analiz",
           "sinyal-karnesi": "svodka-signalov", "portfoy": "portfel",
           "haftasonu": "vykhodnye", "haberler": "novosti", "takvim": "kalendar",
           "sozluk": "glossariy", "terimler": "terminy",
           "muhasebe-terimleri": "bukhgalterskie-terminy", "hisse": "aktsii"},
    "zh": {"index": "index", "raporlar": "baogao", "derin-analiz": "shendu-fenxi",
           "makro-analiz": "hongguan-fenxi", "teknik-analiz": "jishu-saomiao",
           "sinyal-karnesi": "xinhao-jifenka", "portfoy": "touzizuhe",
           "haftasonu": "zhoumo", "haberler": "xinwen", "takvim": "rili",
           "sozluk": "cidian", "terimler": "shuyu",
           "muhasebe-terimleri": "kuaiji-shuyu", "hisse": "gupiao"},
}
KLASOR_ESLE = {
    "en": {"raporlar": "reports", "derin-analiz": "deep-analysis",
           "makro-analiz": "macro-analysis", "haftasonu": "weekend",
           "hisse": "stocks", "terimler": "terms"},
    "de": {"raporlar": "berichte", "derin-analiz": "tiefenanalyse",
           "makro-analiz": "makroanalyse", "haftasonu": "wochenende",
           "hisse": "aktien", "terimler": "begriffe"},
    "ru": {"raporlar": "otchety", "derin-analiz": "glubokiy-analiz",
           "makro-analiz": "makroanaliz", "haftasonu": "vykhodnye",
           "hisse": "aktsii", "terimler": "terminy"},
    "zh": {"raporlar": "baogao", "derin-analiz": "shendu-fenxi",
           "makro-analiz": "hongguan-fenxi", "haftasonu": "zhoumo",
           "hisse": "gupiao", "terimler": "shuyu"},
}
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
# Akıllı birleştirme kümleri (2026-11-04 kok-neden fix'i)
SON_EK = ",;:.!?)]}>'’\"…»"      # oncesine bosluk KOYULMAZ (Turkce ek dahil)
DASH_BAS = "—–-"                  # oncesine bosluk konur (tiipografi)
ONEK = "([{"                      # SONRASINA bosluk konmaz


class _MdCikarici(HTMLParser):
    def __init__(self, kok=None, dil="tr", planli=None, sayfa_rel=None):
        super().__init__(convert_charrefs=True)
        # kok: dist kökü (dil kolunda .md varlığı kontrolü için); dil: sayfanın
        # dili — "tr" = TR kolu (davranış değişmez), "en/de/ru/zh" = dil kolu.
        # planli: bu koşuda ÜRETİLECEK dil .md'lerinin rel kümesi — link
        # çevirisi diske yazım SIRASINDAN bağımsız olsun diye dosya varlığına
        # ek olarak buna da bakılır (2026-11-04).
        # sayfa_rel: kaynağın dist'e göreli uzantısız yolu — sayfaya-göreli
        # çıplak href'ler ("ISCTR") bu bağlamla çözülür (2026-11-04).
        self.kok = kok or ""
        self.dil = dil
        self.sayfa_rel = (sayfa_rel or "").replace("\\", "/").strip("/")
        self.planli = planli or set()
        self.satirlar = []
        self.atla = 0
        self.baslik = ""
        self._h1 = False
        self.ana = 0
        self.ozel_turu = None
        self.ozel_derinlik = 0
        self._b_sev = 0
        self._b_buf = []          # (metin, once_bosluk, sonu_bosluk) uclu
        self._a_href = None
        self._a_buf = []
        self._hucreler = []
        self._tablo_satirlari = []
        self._liste_turu = None
        self._td = 0
        self._tablo_baslik = None
        self._son_hucre_etiketi = ""
        self._bekleyen = False    # salt-bosluk dugumunden sonra: bosluk var
        # pano karta map (2026-11-04)
        self._etiket_modu = False
        self._hucre_ici = False
        self._k_etiket = ""
        self._div_baslik = False
        # kalin-altbaslik + <br> -> #### (2026-11-04)
        self._kalin_basi = False
        self._kalin_lead = None
        # baslik-alti ayni-metin tekrar dedupe (2026-11-04)
        self._son_baslik = ""

    def _arayuz_mi(self, attrs):
        for k, v in attrs:
            if k in ("class", "id") and v and any(p in v for p in ATLA_SINIF):
                return True
        return False

    def _lead_geri_koy(self):
        """strong kapanip <br> gelmeden metin devam ettiyse lead'i normal
        akisa geri koyar (#### olmadan)."""
        if self._kalin_lead is not None:
            lead = self._kalin_lead
            self._kalin_lead = None
            if lead:
                self._b_buf.insert(0, (lead, False, True))

    def handle_starttag(self, tag, attrs):
        if self.ozel_turu is not None:
            if tag == self.ozel_turu:
                self.ozel_derinlik += 1
            return
        oz = dict(attrs)
        cls = oz.get("class") or ""
        if tag == "main":
            self.ana += 1
        elif tag in ATLANAN:
            self.atla += 1
            return
        elif tag == "h1":
            self._h1 = True
        elif tag in ("h2", "h3", "h4", "h5", "h6"):
            self._b_sev = int(tag[1])
            self._b_buf = []
            return
        elif tag == "a":
            self._a_href = oz.get("href") or ""
            self._a_buf = []
            return
        elif tag == "br":
            if not self._td and self._kalin_lead is not None:
                lead = self._kalin_lead
                self._kalin_lead = None
                if lead:
                    self.satirlar.append("")
                    self.satirlar.append("#### " + lead)
                    self.satirlar.append("")
            self._bekleyen = True
            return
        elif tag == "strong":
            if not self._b_buf and not self._td:
                self._kalin_basi = True
        elif tag in ("td", "th"):
            self._td += 1
            self._son_hucre_etiketi = tag
            return
        elif tag == "li":
            self._liste_turu = self._liste_turu or "-"
        elif tag == "div":
            if "pano-baslik" in cls:
                self._div_baslik = True
                self._b_buf = []
            elif "pano-hucre" in cls:
                self._hucre_ici = True
                self._b_buf = []
            elif "pano-etiket" in cls:
                self._etiket_modu = True
                self._k_etiket = ""
            elif "pano-deger" in cls:
                self._etiket_modu = False
        if self._arayuz_mi(attrs):
            self.ozel_turu = tag
            self.ozel_derinlik = 1

    def handle_endtag(self, tag):
        if self.ozel_turu is not None:
            if tag == self.ozel_turu:
                self.ozel_derinlik -= 1
                if self.ozel_derinlik <= 0:
                    self.ozel_turu = None
            return
        if tag in ATLANAN and self.atla:
            self.atla -= 1
        elif tag == "h1":
            self._h1 = False
        elif tag in ("h2", "h3", "h4", "h5", "h6"):
            metin = self._satir_duz(self._zengin_metin(self._b_buf))
            if metin and metin != self._son_baslik:
                self.satirlar.append("")
                self.satirlar.append("#" * self._b_sev + " " + metin)
                self.satirlar.append("")  # baslik sonrasi bos satir
                self._son_baslik = metin
            self._b_sev = 0
            self._b_buf = []
        elif tag == "strong":
            if self._kalin_basi:
                self._kalin_lead = self._satir_duz(self._zengin_metin(self._b_buf))
                self._b_buf = []
                self._kalin_basi = False
        elif tag == "a":
            # baglanti kapanis aninda markdown olarak metin akisina yazilir;
            # once/sonra bosluk bilgisi akılli birlestirmede kullanilir.
            metin = " ".join(x[0] for x in self._a_buf if x[0].strip())
            href = self._a_href or ""
            once = self._bekleyen
            self._bekleyen = False
            if metin and href:
                self._b_buf.append(("[%s](%s)" % (metin.replace("[", "(").replace("]", ")"),
                                                  self._mutlak(href)), once, False))
            elif metin:
                self._b_buf.append((metin, once, False))
            self._a_href = None
            self._a_buf = []
        elif tag in ("p", "div"):
            if tag == "div" and self._div_baslik:
                metin = self._satir_duz(self._zengin_metin(self._b_buf))
                if metin:
                    self.satirlar.append("")
                    self.satirlar.append("### " + metin)
                    self.satirlar.append("")
                self._div_baslik = False
                self._b_buf = []
            elif tag == "div" and self._etiket_modu:
                self._etiket_modu = False  # etiket kutusu bitti; hucre suruyor
            elif tag == "div" and self._hucre_ici:
                etiket = self._k_etiket.strip(" :")
                metin = self._satir_duz(self._zengin_metin(self._b_buf))
                satir = (f"- {etiket}: {metin}" if etiket and metin else metin)
                if satir:
                    self.satirlar.append(satir)
                self._hucre_ici = False
                self._k_etiket = ""
                self._b_buf = []
            elif self._td:
                # hücre içindeyiz: metin satırlara değil, hücre birikimine akar
                pass
            else:
                self._lead_geri_koy()
                metin = self._satir_duz(self._zengin_metin(self._b_buf))
                # baslik-alti AYNI metin tekrarini at (or. "6. Risk ..." strong-p)
                if metin and metin != self._son_baslik:
                    # 2026-11-04 (Hy4 P0-1): "- " maddesinden sonra boş satırsız
                    # gelen div metni lazy-continuation ile maddeye yapışıyor.
                    # Pano-man satırları (▲/▼/📣/🏷️) maddenin devamı olarak
                    # maddeye çevrilir; diğer metinlerden önce boş satır konur.
                    son_dolu = next((s for s in reversed(self.satirlar)
                                     if s.strip()), "")
                    if son_dolu.startswith("- "):
                        if metin[0] in "▲▼📣🏷":
                            metin = "- " + metin
                        else:
                            self.satirlar.append("")
                    self.satirlar.append(self._link_listesi(metin))
                self._b_buf = []
        elif tag in ("td", "th"):
            self._td = max(0, self._td - 1)
            self._hucreler.append(self._satir_duz(self._zengin_metin(self._b_buf)))
            self._b_buf = []
        elif tag == "tr":
            if self._hucreler:
                # ilk th-satiri tablo basligi (thead); segment yaziminda
                # her blokta tekrar basilir (takvim grubu bolunmesi icin)
                if self._tablo_baslik is None and self._son_hucre_etiketi == "th":
                    self._tablo_baslik = list(self._hucreler)
                else:
                    self._tablo_satirlari.append(self._hucreler)
                self._hucreler = []
        elif tag == "table":
            self._tablo_yaz()
        elif tag in ("ul", "ol"):
            self._liste_turu = None
        elif tag == "li":
            self._lead_geri_koy()
            metin = self._satir_duz(self._zengin_metin(self._b_buf))
            if metin:
                self.satirlar.append("- " + self._link_listesi(metin).removeprefix("- "))
            self._b_buf = []
        elif tag == "main" and self.ana:
            self.ana -= 1

    def handle_data(self, data):
        if self.atla or self.ozel_turu is not None:
            return
        if self._etiket_modu:
            self._k_etiket += data
            return
        onlu = bool(data) and data[0].isspace()
        sonu = bool(data) and data[-1].isspace()
        metin = data.strip()
        if not metin:
            self._bekleyen = True   # salt-bosluk dugumu: sonraki parca bosluklu
            return
        once = onlu or self._bekleyen
        self._bekleyen = False
        if self._h1 and not self.baslik:
            self.baslik = metin
            return  # h1 metni govdeye ikinci kez yazilmaz (2026-11-04 canli test bulgusu)
        if self._kalin_lead is not None:
            self._lead_geri_koy()
        if self._a_href is not None:
            self._a_buf.append((metin, once, sonu))
            return
        self._b_buf.append((metin, once, sonu))

    # --- yardımcılar ---
    def _zengin_metin(self, buf):
        """(metin, once, sonu) uclulerini HTML'deki gercek bosluk bilgisine
        sadik kalarak birlestirir; Turkce ek kesmesi ve noktalama yapisik
        kalir, em-dash ve emoji sinirlarinda bosluk korunur (2026-11-04)."""
        cikti = ""
        son_sonu = False
        for metin, once, sonu in buf:
            if not metin:
                continue
            if not cikti:
                cikti = metin
            elif metin[0] in SON_EK and not (
                    # 2026-11-04 (GLM): düz tırnak hem açılış hem kapanış
                    # işaretidir; arkası harf gelen tırnak AÇILIŞTIR — önceki
                    # söze yapıştırılmaz ([VAKBN](…)"GÜÇLÜ SAT" hatası).
                    # Kesme işaretine (') dokunulmaz: Türkçe ek yapışması doğrudur.
                    metin[0] == '"' and len(metin) > 1 and metin[1] not in SON_EK):
                cikti += metin
            elif metin[0] in DASH_BAS or son_sonu or once:
                cikti += " " + metin
            elif cikti[-1] in ",;:.!?" and metin[0] not in SON_EK:
                cikti += " " + metin  # noktalama sonrasi gelen link: bosluk sart
            elif cikti[-1] in ONEK:
                cikti += metin
            else:
                cikti += metin  # HTML'de bosluk yoktu: yapisk
            son_sonu = sonu
        return cikti

    @staticmethod
    def _satir_duz(metin):
        return re.sub(r"\s+", " ", metin).strip()

    @staticmethod
    def _link_listesi(metin):
        """Yapisik ardısık linkler ("...(url)[...") HTML'de tek div'e
        yigilmis liste demektir; her linki ayrı maddeye boler (2026-11-04
        Hy4 ④ — rapor arsivi aylari tek satira basiliyordu)."""
        if metin.count(")[") >= 2:
            return "- " + metin.replace(")[", ")\n- [")
        return metin

    def _mutlak(self, href):
        """Goreli linkleri site kokune cevirir. TR kolu: ic linkler .md kopyaya
        yonlendirilir (ajan md zincirinde kalir, 2026-11-04 Hy4 ④); dil
        kopyalarina (en/de/ru/zh — .md'siz, 404) DOKUNULMAZ (92/92 kurali
        aynen korunur). Dil kolu (2026-11-04): yalniz AYNI dil agacinda .md
        karsiligi dist'te VARSA .md'ye yonlendirir; yoksa dokunmaz (guzel-URL
        HTML'e birakir) — fail-open."""
        if href.startswith(("http://", "https://", "#", "mailto:", "tel:")):
            return href
        temiz = re.sub(r"^(\.\./)+", "", href).lstrip("/").rstrip("/")
        if temiz.endswith(".html"):
            temiz = temiz[:-5]
        if self.dil != "tr":
            temiz = temiz.strip("/")
            if temiz.startswith(self.dil + "/"):
                temiz = temiz[len(self.dil) + 1:]
            if temiz.endswith(".md"):
                temiz = temiz[:-3]
            if temiz in ("", ".", "index"):
                if (self.dil + "/index" in self.planli or
                        os.path.isfile(os.path.join(self.kok, self.dil, "index.md"))):
                    return SITE_URL + "/" + self.dil + "/index.md"
                return SITE_URL + "/" + self.dil      # href="/" ana sayfa
            aday = temiz
            if "/" in temiz:
                ilk, kalan = temiz.split("/", 1)
                es = KLASOR_ESLE.get(self.dil, {}).get(ilk)
                if es:                                 # eski TR-slug'li link
                    aday = es + "/" + kalan
            else:
                # 2026-11-04 (Qwen ①): klasör-kök bağlantısı ("../stocks/")
                # ağacın index.md'ine gider; sayfaya-göreli çıplak yol
                # ("PGSUS" en/stocks sayfasında) sayfanın klasörüyle çözülür.
                if self.kok and os.path.isfile(
                        os.path.join(self.kok, self.dil, temiz, "index.html")):
                    if self.dil + "/" + temiz + "/index" in self.planli:
                        return SITE_URL + "/" + self.dil + "/" + temiz + "/index.md"
                if self.kok and "/" in self.sayfa_rel:
                    sayfa_klasor = self.sayfa_rel.split("/")[1]
                    if (sayfa_klasor in KLASOR_ESLE.get(self.dil, {}).values()
                            and temiz not in KOK_ESLE.get(self.dil, {}).values()):
                        aday2 = sayfa_klasor + "/" + temiz
                        if os.path.isfile(os.path.join(
                                self.kok, self.dil, aday2 + ".html")):
                            return SITE_URL + "/" + self.dil + "/" + aday2 + ".md"
                aday = KOK_ESLE.get(self.dil, {}).get(temiz, temiz)
            if (aday in self.planli or
                    os.path.isfile(os.path.join(self.kok, self.dil, aday + ".md"))):
                return SITE_URL + "/" + self.dil + "/" + aday + ".md"
            return SITE_URL + "/" + self.dil + "/" + temiz  # .md yok: HTML'e
        if "#" in temiz:
            # 2026-11-04: parçalı hedef (#bolum) .md kopyada yok — HTML'e
            # bırakılır (fail-open, architect P2).
            return SITE_URL + "/" + temiz
        if not temiz:
            return SITE_URL + "/index.md"        # href="/" ana sayfa
        if temiz.endswith(".md"):
            return SITE_URL + "/" + temiz
        ilk = temiz.split("/")[0]
        if ilk in ("en", "de", "ru", "zh"):
            return SITE_URL + "/" + temiz
        # 2026-11-04 (Qwen ①): klasör-kök bağlantısı ("../hisse/", "/hisse")
        # — .md karşılığı ağacın index.md'idir ("/hisse.md" 404'tür); hub'ı
        # kök sayfa olan ağaçlarda (terimler, raporlar) kök .md kullanılır.
        if temiz in KLASORLER and self.kok:
            if os.path.isfile(os.path.join(self.kok, temiz, "index.html")):
                return SITE_URL + "/" + temiz + "/index.md"
            if temiz in KOK_SAYFALAR:
                return SITE_URL + "/" + temiz + ".md"
        # 2026-11-04 (Qwen ①): sayfaya-göreli çıplak yol ("ISCTR" hisse
        # sayfasında /ISCTR değil /hisse/ISCTR'e çözülür; html varlığı
        # esas alınır — md aynı koşuda üretilir, sıra fark etmez).
        if ("/" not in temiz and self.kok and self.sayfa_rel
                and "/" in self.sayfa_rel):
            klasor = self.sayfa_rel.split("/")[0]
            if (klasor in KLASORLER and temiz not in KOK_SAYFALAR
                    and temiz not in KLASORLER):
                aday = klasor + "/" + temiz
                if os.path.isfile(os.path.join(self.kok, aday + ".html")):
                    return SITE_URL + "/" + aday + ".md"
        if ilk in KOK_SAYFALAR or ilk in KLASORLER:
            return SITE_URL + "/" + temiz + ".md"
        return SITE_URL + "/" + temiz            # .md'siz bilinmeyen yol: HTML'e

    def _tablo_yaz(self):
        if not self._tablo_satirlari:
            return
        baslik = self._tablo_baslik
        self.satirlar.append("")  # tablo oncesi bos satir: markdown render sartı

        # 2026-11-04 (Qwen ②): başlıksız tabloda ilk satır hem başlık hem
        # gövde olarak basılıyordu ("ust = baslik or satirlar[0]" + tüm
        # satırların basılması) — ilk satır artık yalnız başlık. 2026-11-04
        # (kullanıcı): hücreler sütun genişliğine doldurulur; ham metin
        # görüntüleyicide dikey çizgiler hizalı akar. Dolgulu satır 250
        # karakteri aşarsa tablo dolgusuz basılır (fail-open).
        def _esc(s):
            return s.replace("|", "\\|")

        ajanda_mi = (bool(baslik) and any("Beklenen" in c for c in baslik)
                     and any("Önceki" in c for c in baslik))

        def _ondalik(h):
            # 2026-11-04 (GLM/Muse): ajanda/takvim tablolarında ABD ondalığı
            # TR'ye çevrilir; "tam 2 kesirli hane" kuralı binlik grupları
            # ("13.754", "5,091.2") ve tarihleri dışarıda bırakır. Yalnız
            # ajanda tablolarına uygulanır — bilanço metinleri korunur.
            return re.sub(r"(?<![\d.,])([+\-−]?\d+)\.(\d{2})(?!\d)", r"\1,\2", h)

        # 2026-11-04 (Hy4 takvim P0'i): tek-hucreli satirlar colspan'li GRUP
        # basligidir — tabloyu bolup "#### baslik" olarak akisa yazilir.
        # 2026-11-04 (Qwen ③): grup başlığındaki çift tarih tekrarı
        # ("04.11 (X) (X)") tekilleştirilir.
        bloklar = []
        segment = []
        for s in self._tablo_satirlari:
            if len(s) == 1 and (baslik and len(baslik) > 1):
                if segment:
                    bloklar.append(("tablo", baslik, segment))
                    segment = []
                if s[0]:
                    metin = re.sub(r"\s+(\([^)]+\))\s+\1$", r" \1", s[0])
                    bloklar.append(("grup", metin, None))
                continue
            segment.append(s)
        if segment:
            bloklar.append(("tablo", baslik, segment))

        for tur, a, b in bloklar:
            if tur == "grup":
                self.satirlar.append("")
                self.satirlar.append("#### " + a)
                self.satirlar.append("")
                continue
            ust, satirlar = a, b
            if baslik is not None:
                ust_ham, govde_ham = ust, satirlar
            else:
                ust_ham, govde_ham = satirlar[0], satirlar[1:]
            genis = max([len(r) for r in satirlar] + [len(ust_ham)])
            ust_e = [_esc(_ondalik(c)) for c in ust_ham] + [""] * (genis - len(ust_ham))
            rows_e = [[_esc(_ondalik(c)) for c in s] + [""] * (genis - len(s))
                      for s in govde_ham]
            gen = [max([len(ust_e[j])] + [len(r[j]) for r in rows_e])
                   for j in range(genis)]
            dolgu = 3 * genis + sum(gen) <= 250

            def _satir(hucreler):
                if dolgu:
                    hucreler = [c.ljust(gen[j]) for j, c in enumerate(hucreler)]
                return "| " + " | ".join(hucreler) + " |"

            self.satirlar.append(_satir(ust_e))
            self.satirlar.append(_satir(["---"] * genis))
            for r in rows_e:
                self.satirlar.append(_satir(r))
            self.satirlar.append("")
        self.satirlar.append("")
        self._tablo_satirlari = []
        self._hucreler = []
        self._tablo_baslik = None
        self._son_hucre_etiketi = ""


def _baslik_kirp(govde):
    """Başlık seviyelerindeki atlamaları aşağı kırp (# sonrası ### → ##);
    asla yukarı çekmez. Sarmalayıcının yazdığı H1 (seviye 1) referanstır
    (2026-11-04, Hy4/Qwen başlık hiyerarşisi bulgusu)."""
    satirlar = govde.split("\n")
    onceki = 1
    for i, s in enumerate(satirlar):
        m = re.match(r"^(#{2,6}) ", s)
        if m:
            sev = min(len(m.group(1)), onceki + 1)
            if sev != len(m.group(1)):
                satirlar[i] = "#" * sev + s[len(m.group(1)):]
            onceki = sev
    return "\n".join(satirlar)


def html_md(yol, rel=None, dil="tr", kok=None, planli=None):
    """HTML dosyasını Markdown'a çevirir; front matter + yasal kapanışlı
    md metni döner. rel: dist köküne göreli uzantısız yol (canonical ve
    sayfaya-göreli link çözümü için). dil: front matter'daki language
    alanı ("tr" = TR kolu, davranış eski); kok: dist kökü; planli: dil
    kolunda bu koşuda üretilecek .md rel kümesi."""
    with open(yol, encoding="utf-8", errors="replace") as fh:
        ham = fh.read()
    rel_temiz = (rel or os.path.basename(yol)).replace("\\", "/")
    if rel_temiz.endswith(".html"):
        rel_temiz = rel_temiz[:-5]
    c = _MdCikarici(kok=kok, dil=dil, planli=planli, sayfa_rel=rel_temiz)
    c.yalniz_ana = "<main" in ham
    c.feed(ham)
    baslik = c.baslik
    if not baslik:
        m = re.search(r"<title>(.*?)</title>", ham, re.S | re.I)
        baslik = re.sub(r"\s+", " ", m.group(1)).strip() if m else os.path.basename(yol)
    govde = "\n".join(c.satirlar)
    govde = re.sub(r"\n{3,}", "\n\n", govde).strip()
    if not govde:
        return ""
    # 2026-11-04: başlık kırpma + işaretli yüzde biçimi normalize
    # ("+%4,09" ve "+4,09%" karışıklığı — çoğunluk biçimi kazanır;
    # işaretsiz "%X,Y" biçimlerine dokunulmaz).
    govde = _baslik_kirp(govde)
    govde = re.sub(r"([+\-−])%(\d[\d,]*)", r"\1\2%", govde)
    govde = re.sub(r"%([+\-−])(\d[\d,]*)", r"\1\2%", govde)
    # 2026-11-04 (GLM): eski artifact'lerdeki sesli-bülten kartı bugünkü
    # "card" sınıflıydı — oyuncusuz kalan "🔊 Sesli bülten" etiketi satırı
    # md'yi kirletir; yeni üretimde kart zaten ATLA_SINIF'ta.
    govde = "\n".join(l for l in govde.split("\n") if l.strip() != "🔊 Sesli bülten")
    # 2026-11-04 (Qwen/Muse): canonical gerçek insan URL'idir — kök "/"
    # ("/index" 308'e düşer), klasör hub'ı "/hisse/" biçiminde.
    if rel_temiz == "index":
        canli = SITE_URL + "/"
    elif rel_temiz.endswith("/index"):
        canli = SITE_URL + "/" + rel_temiz[:-6] + "/"
    else:
        canli = SITE_URL + "/" + rel_temiz
    if rel_temiz == "index" or rel_temiz.endswith("/index"):
        md_url = SITE_URL + "/" + rel_temiz + ".md"
    else:
        md_url = canli + ".md"
    m_t = re.search(r"\d{4}-\d{2}-\d{2}", rel_temiz)
    # 2026-11-04 (Hy4 ③): H1 ve title tarihli olur — dosya tek basina
    # okundugunda hangi rapor oldugu ilk satirdan belli olsun.
    baslik_md = baslik
    if m_t:
        y, ay, g = m_t.group(0).split("-")
        if dil == "tr":
            baslik_md = f"{baslik} — {int(g)} {AYLAR[int(ay) - 1]} {y}"
        else:
            # dil sayfasında Türkçe ay adı karışmasın: ISO tarih
            baslik_md = f"{baslik} — {m_t.group(0)}"
    govde = f"# {baslik_md}\n\n{govde}\n"
    # YAML front matter + yasal kapanış — .md dosyası tek başına paylaşıldığında
    # bağlam, kaynak ve uyarı kaybolmasın (2026-11-04). Dil kolu için EN
    # chrome'u yalnız en'e verilir (de/ru/zh çevirisi uydurulmaz — TR kalır,
    # machine_translated bayrağı dürüstlük işareti); kapanıştaki URL < >
    # autolink olur — italik içindeki çıplak URL tıklanmazdı (2026-11-04).
    if dil == "en":
        gen_by = "Artificial intelligence (multi-agent system)"
        feragat = "Not investment advice."
        not_satiri = ("> This file is a raw Markdown copy for LLM/agent "
                      "consumption; human version: <" + canli + ">")
        kapanis = ("\n---\n\n_Note: This content was produced by the site's "
                   "automation with AI (multi-agent system); educational "
                   "purposes only, not investment advice. Live version: <"
                   + canli + ">_\n")
    else:
        gen_by = "Yapay zeka (çok ajanlı sistem)"
        feragat = "Yatırım tavsiyesi değildir."
        not_satiri = ("> Bu dosya LLM/ajan tüketimi için ham Markdown "
                      "kopyasıdır; insan sürümü: <" + canli + ">")
        kapanis = ("\n---\n\n_Bilgilendirme: Bu içerik site otomasyonu tarafından "
                   "yapay zeka (çok ajanlı sistem) ile üretilmiştir; eğitim amaçlıdır, "
                   "yatırım tavsiyesi değildir. Canlı sürüm: <" + canli + ">_\n")
    m_dm = re.search(r'"dateModified":\s*"([^"]+)"', ham)
    semboller = {k for k in re.findall(
        r"/(?:hisse|stocks|aktien|aktsii|gupiao)/([A-Z][A-Z0-9]{1,5})(?=[/\"'.?#)])",
        ham) if k != "index"}
    if dil == "tr" and "/" in rel_temiz and rel_temiz.split("/")[0] == "hisse":
        semboller.add(rel_temiz.split("/")[1])
    semboller = sorted(semboller)[:40]
    fm = ["---", 'title: "%s"' % baslik_md.replace('"', "'")]
    if m_t:
        fm.append("date: " + m_t.group(0))
    fm += ['canonical: "' + canli + '"',
           'markdown: "' + md_url + '"',
           "language: " + dil,
           'generated_by: "' + gen_by + '"',
           'disclaimer: "' + feragat + '"']
    if m_dm:
        fm.append('updated: "' + m_dm.group(1) + '"')
    if semboller:
        fm.append("symbols: [" + ", ".join(semboller) + "]")
    fm.append('data: "' + SITE_URL + '/ticker.json"')
    if dil != "tr":
        fm.append("machine_translated: true")
    fm.append("---")
    return "\n".join(fm) + "\n\n" + not_satiri + "\n\n" + govde + kapanis


def _yaz(yol, icerik):
    try:
        with open(yol, encoding="utf-8") as fh:
            if fh.read() == icerik:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    with open(yol, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(icerik)
    return True


def main():
    kok = sys.argv[1] if len(sys.argv) > 1 else "dist"
    yazilan = 0
    # --- TR kolu (davranış aynen korunur) ---
    # Kök sayfalar
    for ad in KOK_SAYFALAR:
        yol = os.path.join(kok, ad + ".html")
        if not os.path.isfile(yol):
            continue
        md = html_md(yol, rel=ad, kok=kok)
        if md and _yaz(os.path.join(kok, ad + ".md"), md):
            yazilan += 1
    # Klasör ağaçları
    for klasor in KLASORLER:
        dizin = os.path.join(kok, klasor)
        if not os.path.isdir(dizin):
            continue
        for ad in sorted(os.listdir(dizin)):
            if not ad.endswith(".html"):
                continue
            yol = os.path.join(dizin, ad)
            md = html_md(yol, rel=klasor + "/" + ad[:-5], kok=kok)
            if md and _yaz(os.path.join(dizin, ad[:-5] + ".md"), md):
                yazilan += 1
    # --- Dil kolu (2026-11-04): sabit eşleme + tarihli klasörlerde SON 30 GÜN
    # (architect S9 ölçek sınırı; bugün terminal saatinden alınır). Olmayan
    # sayfa/klasör sessizce atlanır (fail-open). ---
    sinir = (date.today() - timedelta(days=30)).isoformat()
    # 1. geçiş: bu koşuda üretilecek dil .md'lerinin rel kümesi — link
    # çevirisi yazım sırasından bağımsız olsun diye önceden hesaplanır.
    planli = set()
    for dil in DILLER:
        if not os.path.isdir(os.path.join(kok, dil)):
            continue
        for tr_ad, dil_ad in KOK_ESLE.get(dil, {}).items():
            if os.path.isfile(os.path.join(kok, dil, dil_ad + ".html")):
                planli.add(dil + "/" + dil_ad)
        for tr_klasor, dil_klasor in KLASOR_ESLE.get(dil, {}).items():
            dizin = os.path.join(kok, dil, dil_klasor)
            if not os.path.isdir(dizin):
                continue
            for ad in os.listdir(dizin):
                if not ad.endswith(".html"):
                    continue
                m_t = re.match(r"^(\d{4}-\d{2}-\d{2})\.html$", ad)
                if m_t and m_t.group(1) < sinir:
                    continue
                planli.add(dil + "/" + dil_klasor + "/" + ad[:-5])
    # 2. geçiş: üretim
    for dil in DILLER:
        dil_kok = os.path.join(kok, dil)
        if not os.path.isdir(dil_kok):
            continue
        for tr_ad, dil_ad in sorted(KOK_ESLE.get(dil, {}).items()):
            yol = os.path.join(dil_kok, dil_ad + ".html")
            if not os.path.isfile(yol):
                continue
            md = html_md(yol, rel=dil + "/" + dil_ad, dil=dil, kok=kok,
                         planli=planli)
            if md and _yaz(os.path.join(dil_kok, dil_ad + ".md"), md):
                yazilan += 1
        for tr_klasor, dil_klasor in sorted(KLASOR_ESLE.get(dil, {}).items()):
            dizin = os.path.join(dil_kok, dil_klasor)
            if not os.path.isdir(dizin):
                continue
            for ad in sorted(os.listdir(dizin)):
                if not ad.endswith(".html"):
                    continue
                m_t = re.match(r"^(\d{4}-\d{2}-\d{2})\.html$", ad)
                if m_t and m_t.group(1) < sinir:
                    continue   # son 30 günün dışındaki tarihli arşiv sayfası
                yol = os.path.join(dizin, ad)
                md = html_md(yol, rel=dil + "/" + dil_klasor + "/" + ad[:-5],
                             dil=dil, kok=kok, planli=planli)
                if md and _yaz(os.path.join(dizin, ad[:-5] + ".md"), md):
                    yazilan += 1
    print(f"[md-ayna] {yazilan} Markdown aynası üretildi ({kok}).")


if __name__ == "__main__":
    main()
