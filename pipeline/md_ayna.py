# -*- coding: utf-8 -*-
"""Markdown aynaları (2026-11-04, Faz 7 — ajan-okunurluk).

Ne yapar?
  dist ağacındaki seçili HTML sayfaların .md kopyasını üretir: ajanlar
  "<sayfa-adı>.md" yazarak tabloları boru formatında, başlıkları korunmuş,
  gezinme kabuğu arındırılmış sürümü çeker (idratherbewriting.com kalıbı:
  "Markdown versions of all pages are available by appending .md").

Neden derleme anında?
  Başlıklar ve tablolar zaten düzgün üretilen HTML'den çevrilir; arama
  motoru/ajan için ayrı bir servis katmanı (?format=md Function'ı) gerekmez,
  CDN önbelleği aynen çalışır.

Kullanım:
  python3 pipeline/md_ayna.py dist        # build.sh'ten: dist ağacında üret
  python3 pipeline/md_ayna.py .           # yerel deneme (repo kökünde)

Atlanan öğeler: script/style/nav/header/footer/aside/form + hesaplayıcı/
asistan kartları (embed.py _MetinCikarici ile aynı küme — tek davranış).
"""
import os
import re
import sys
from html.parser import HTMLParser

ATLANAN = {"script", "style", "noscript", "svg", "head", "nav", "header",
           "footer", "aside", "button", "form", "select", "option",
           "label", "template"}
ATLA_SINIF = ("hesap", "asistan", "site-widgets", "live-clock",
              "interactive-box", "arama-kutusu", "radyo",
              # 2026-11-04 (kullanıcı kararı): .md kopyalar "yalnız asıl
              # içerik" modunda — fiyat şeritleri (bant + bant2 kopyası),
              # paylaşım butonları (javascript:void(0) dahil), günün
              # panosu ve sesli bülten ajani için saf gürültü; HTML
              # sayfalara dokunulmaz.
              "ticker", "paylas", "pano", "sesli-bulten")

# Kök sayfalar (uzantısız adlar; .html okunur, .md yazılır)
KOK_SAYFALAR = [
    "index", "raporlar", "derin-analiz", "makro-analiz", "teknik-analiz",
    "borsapy-analiz", "sinyal-karnesi", "portfolio", "haftasonu",
    "haftasonu-egitimi", "haberler", "sirket-haberleri", "takvim",
    "sozluk", "terimler", "muhasebe-terimleri", "temettu",
]
# Klasör ağaçları (içindeki tüm .html'ler; .md kopyası aynı yere yazılır)
KLASORLER = [
    "raporlar", "derin-analiz", "makro-analiz", "haftasonu",
    "haftasonu-egitimi", "hisse", "terimler", "muhasebe-terimleri",
]


class _MdCikarici(HTMLParser):
    def __init__(self):
        super().__init__()
        self.satirlar = []
        self.atla = 0
        self.baslik = ""
        self._h1 = False
        self.ana = 0
        self.ozel_turu = None
        self.ozel_derinlik = 0
        self._b_sev = 0
        self._b_buf = []
        self._a_href = None
        self._a_buf = []
        self._hucreler = []
        self._tablo_satirlari = []
        self._liste_turu = None
        self._td = 0

    def _arayuz_mi(self, attrs):
        for k, v in attrs:
            if k in ("class", "id") and v and any(p in v for p in ATLA_SINIF):
                return True
        return False

    def handle_starttag(self, tag, attrs):
        if self.ozel_turu is not None:
            if tag == self.ozel_turu:
                self.ozel_derinlik += 1
            return
        oz = dict(attrs)
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
        elif tag in ("td", "th"):
            self._td += 1
            return
        elif tag == "li":
            self._liste_turu = self._liste_turu or "-"
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
            metin = self._satir_duz("#" * self._b_sev + " " +
                                    self._zengin_metin(self._b_buf))
            if metin.strip("# "):
                self.satirlar.append("")
                self.satirlar.append(metin)
            self._b_sev = 0
            self._b_buf = []
        elif tag == "a":
            # 2026-11-04 architect P1 duzeltmesi: baglanti metni _a_buf'ta
            # kalip _b_buf'a hic girmedigi icin tablo hucreleri/paragraflar
            # bos kaliyordu. Baglanti kapanis aninda markdown olarak dogrudan
            # metin akisina yazilir.
            metin = " ".join(x for x in self._a_buf if x.strip())
            href = self._a_href or ""
            if metin and href:
                self._b_buf.append("[%s](%s)" % (metin.replace("[", "(").replace("]", ")"), href))
            elif metin:
                self._b_buf.append(metin)
            self._a_href = None
            self._a_buf = []
        elif tag in ("p", "div"):
            if self._td:
                # hücre içindeyiz: metin satırlara değil, hücre birikimine akar
                pass
            else:
                metin = self._satir_duz(self._zengin_metin(self._b_buf))
                if metin:
                    self.satirlar.append(metin)
            self._b_buf = []
        elif tag in ("td", "th"):
            self._td = max(0, self._td - 1)
            self._hucreler.append(" ".join("".join(self._b_buf).split()))
            self._b_buf = []
        elif tag == "tr":
            if self._hucreler:
                self._tablo_satirlari.append(self._hucreler)
                self._hucreler = []
        elif tag == "table":
            self._tablo_yaz()
        elif tag in ("ul", "ol"):
            self._liste_turu = None
        elif tag == "li":
            metin = self._satir_duz(self._zengin_metin(self._b_buf))
            if metin:
                self.satirlar.append("- " + metin)
            self._b_buf = []
        elif tag == "main" and self.ana:
            self.ana -= 1

    def handle_data(self, data):
        if self.atla or self.ozel_turu is not None:
            return
        metin = data.strip()
        if not metin:
            return
        if self._h1 and not self.baslik:
            self.baslik = metin
        if self._a_href is not None:
            self._a_buf.append(metin)
            return
        self._b_buf.append(metin)

    # --- yardımcılar ---
    @staticmethod
    def _zengin_metin(buf):
        """_b_buf parçalarını sırayla birleştirir (markdown bağlantıları
        kapanış anında zaten _b_buf'a yazılmıştır)."""
        return " ".join(x.strip() for x in buf if x.strip())

    @staticmethod
    def _satir_duz(metin):
        return re.sub(r"\s+", " ", metin).strip()

    def _tablo_yaz(self):
        if not self._tablo_satirlari:
            return
        genis = max(len(r) for r in self._tablo_satirlari)
        self.satirlar.append("")
        for i, satir in enumerate(self._tablo_satirlari):
            hucreler = [c.replace("|", "\\|") for c in satir] + [""] * (genis - len(satir))
            self.satirlar.append("| " + " | ".join(hucreler) + " |")
            if i == 0:
                self.satirlar.append("|" + " --- |" * genis)
        self.satirlar.append("")
        self._tablo_satirlari = []
        self._hucreler = []


def html_md(yol):
    """HTML dosyasını Markdown'a çevirir; (baslik, md_metni) döner."""
    with open(yol, encoding="utf-8", errors="replace") as fh:
        ham = fh.read()
    c = _MdCikarici()
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
    return f"# {baslik}\n\n{govde}\n"


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
    # Kök sayfalar
    for ad in KOK_SAYFALAR:
        yol = os.path.join(kok, ad + ".html")
        if not os.path.isfile(yol):
            continue
        md = html_md(yol)
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
            md = html_md(yol)
            if md and _yaz(os.path.join(dizin, ad[:-5] + ".md"), md):
                yazilan += 1
    print(f"[md-ayna] {yazilan} Markdown aynası üretildi ({kok}).")


if __name__ == "__main__":
    main()
