# -*- coding: utf-8 -*-
"""Tek seferlik göç aracı: mevcut veri/HTML'lerden (LLM çağrısı YOK) tüm
statik sayfaları bot.py'nin yeni şablonuyla yeniden üretir.

Kapsam:
  - reports/*.html   : kimlik bloğu (Tarih/Yayıncı/Konu) ve [PORTFOY OZETI]
                       ham verisi temizlenir, h3 bölümler h2'ye yükseltilir,
                       başlıklar doğru Türkçeye çevrilir, SEO kabuğu eklenir.
  - derin-analiz.html: ASCII başlık kelimeleri düzeltilir, SEO kabuğu eklenir.
  - teknik-analiz.html: data/teknik/<son tarih>.json'dan yeniden üretilir.
  - borsapy-analiz.html: mevcut tablodan satırlar ayıklanıp yeniden üretilir.
  - index.html, portfolio.html: portfolio.json'dan yeniden üretilir.
  - sitemap.xml + robots.txt yazılır.

Kullanım:  python migrate_rebuild.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402  (import sırasında LLM çağrısı yapılmaz)

# Türkçe duyarlı küçük/büyük harf tabloları (str.lower/upper 'İ' ve 'I'
# harflerinde İngilizce kuralları uyguladığı için elle eşliyoruz)
TR_KUCUK_MAP = str.maketrans(
    "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZQWX",
    "abcçdefgğhıijklmnoöprsştuüvyzqwx",
)
TR_ILK_HARF = {"a": "A", "b": "B", "c": "C", "ç": "Ç", "d": "D", "e": "E", "f": "F",
               "g": "G", "ğ": "Ğ", "h": "H", "ı": "I", "i": "İ", "j": "J", "k": "K",
               "l": "L", "m": "M", "n": "N", "o": "O", "ö": "Ö", "p": "P", "r": "R",
               "s": "S", "ş": "Ş", "t": "T", "u": "U", "ü": "Ü", "v": "V", "y": "Y",
               "z": "Z"}
BAGIÇLAR = {"ve", "veya", "ile"}


def tr_baslik(metin):
    """'1. YÖNETİCİ ÖZETİ VE PİYASA GENEL BAKIŞI' -> '1. Yönetici Özeti ve Piyasa Genel Bakışı'"""
    kucuk = metin.translate(TR_KUCUK_MAP)

    def kelime(k):
        if k.strip("()„\"'").lower() in BAGIÇLAR or not k:
            return k
        for i, ch in enumerate(k):
            if ch in TR_ILK_HARF:
                return k[:i] + TR_ILK_HARF[ch] + k[i + 1:]
        return k

    return " ".join(kelime(k) for k in kucuk.split(" "))


TAG_TEMIZLE = re.compile(r"<[^>]+>")


def hucre_metin(hucre_html):
    return TAG_TEMIZLE.sub("", hucre_html).strip()


def makale_ayikla(dosya):
    src = open(dosya, encoding="utf-8").read()
    m = re.search(r'<article class="report">(.*?)</article>', src, re.S)
    if not m:
        raise SystemExit(f"HATA: {dosya} içinde article bulunamadı")
    return m.group(1)


def gunluk_rapor_temizle(html):
    """Yayınlanmış günlük rapor article HTML'ini yeni standartlara getirir."""
    # 1) Baştaki kimlik bloğu: <p><strong>BIST 30 YATIRIM...</strong><br/>
    #    <strong>Tarih:</strong> ... <strong>Yayıncı:</strong> ... <strong>Konu:</strong> ...</p> (+ hemen ardındanki <hr/>)
    html = re.sub(
        r"^\s*<p>\s*<strong>\s*BIST\s*30[^<]*</strong>\s*(?:<br\s*/?>\s*<strong>[^<]*</strong>\s*[^<]*)*?</p>\s*(?:<hr\s*/?>\s*)?",
        "",
        html,
        flags=re.S,
    )
    # Güvenlik ağı: Tarih/Yayıncı satırlarını taşıyan tekil paragraflar
    html = re.sub(r"<p>\s*<strong>\s*(?:Tarih|Yayıncı|Yayınlayan|Konu)\s*:\s*</strong>[^<]*</p>\s*", "", html)
    # 2) Sondaki [PORTFOY OZETI] ham veri bloğu
    html = re.sub(
        r"<p>\s*\[PORTF[ÖO]Y\s*Ö?O?ZET[İI]\]\s*(?:<br\s*/?>)?[^<]*(?:<br\s*/?>[^<]*)*</p>\s*$",
        "",
        html,
    )
    # 3) h3 bölüm başlıklarını h2'ye yükselt + Türkçe başlık düzeni
    def h3_to_h2(m):
        return "<h2>" + tr_baslik(m.group(1).strip()) + "</h2>"

    html = re.sub(r"<h3>([^<]*)</h3>", h3_to_h2, html)
    # Zaten h2 olan bölüm başlıklarını da aynı kuralla tazele (yeniden çalıştırmada)
    html = re.sub(r"<h2>(\d+\.[^<]*)</h2>", lambda m: "<h2>" + tr_baslik(m.group(1).strip()) + "</h2>", html)
    # 4) Yayın kimliği/hitap düzeltmeleri
    html = html.replace(
        "Sadece kurumsal yatırımcılara sunulan bir strateji belgesidir.",
        "Genel bilgilendirme amacıyla hazırlanmış bir strateji belgesidir.",
    ).replace("Kurumsal Yatırımcılar,", "Değerli Okur,")
    # 5) Yazım/ASCII Türkçe düzeltmeleri (kelime sınırlı, güvenli)
    html = bot._turkce_karakter_duzelt(html)
    # Ardışık boş satırları toparla
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def derin_temizle(html):
    """Derin analiz article HTML'i: ASCII başlıklar + hitap + yazım düzeltmeleri.
    H2 başlıkları bazen iç içe <strong> içerir; etiketler soyulup düzgün Türkçe
    başlığa çevrilir."""
    html = html.replace("Kurumsal Yatırımcılar,", "Değerli Okur,")
    html = bot._turkce_karakter_duzelt(html)

    def h2_yeniden(m):
        ic = TAG_TEMIZLE.sub("", m.group(1)).strip()
        return "<h2>" + tr_baslik(ic) + "</h2>"

    html = re.sub(r"<h2[^>]*>(.*?)</h2>", h2_yeniden, html, flags=re.S)
    return html.strip()


def borsapy_ayikla(dosya):
    """Mevcut borsapy-analiz.html tablosundan satır sözlüklerini çıkarır."""
    src = open(dosya, encoding="utf-8").read()
    satirlar = []
    for m in re.finditer(r"onclick=\"tvAc\('([A-Z0-9]+)'\)\">(.*?)</tr>", src, re.S):
        hisse, govde = m.group(1), m.group(2)
        hucreler = re.findall(r"<td[^>]*>(.*?)</td>", govde, re.S)
        if len(hucreler) < 8:
            continue
        # Hücre sırası: hisse, öneri, al/sat/nötr oyları, RSI, MACD, Stoch, CCI, ADX
        oneri = hucre_metin(hucreler[1])
        oy = hucre_metin(hucreler[2])  # "20 / 2 / 6"
        oy_m = re.match(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", oy)

        def _f(x):
            x = x.strip().replace(",", ".")
            try:
                return round(float(x), 2)
            except ValueError:
                return None

        satirlar.append({
            "hisse": hisse, "oneri": oneri,
            "al": int(oy_m.group(1)) if oy_m else 0,
            "sat": int(oy_m.group(2)) if oy_m else 0,
            "notr": int(oy_m.group(3)) if oy_m else 0,
            "rsi": _f(hucre_metin(hucreler[3])),
            "macd": _f(hucre_metin(hucreler[4])),
            "stoch": _f(hucre_metin(hucreler[5])),
            "cci": _f(hucre_metin(hucreler[6])),
            "adx": _f(hucre_metin(hucreler[7])),
        })
    return satirlar


def main():
    raporlar = sorted((fn for fn in os.listdir("reports") if fn.endswith(".html")), reverse=True)
    if not raporlar:
        raise SystemExit("reports/ altında HTML yok")
    son_tarih = raporlar[0][:-5]

    # 1) Günlük raporlar
    for fn in raporlar:
        tarih = fn[:-5]
        makale = makale_ayikla(os.path.join("reports", fn))
        temiz = gunluk_rapor_temizle(makale)
        # Kimlik bloğu hâlâ duruyorsa uyar (regex kaçtıysa)
        if "Yayıncı" in temiz[:500] or "[PORTFOY" in temiz:
            print(f"UYARI: {fn} içinde kimlik bloğu kalıntısı olabilir — elle kontrol edin.")
        with open(os.path.join("reports", fn), "w", encoding="utf-8") as f:
            f.write(bot.rapor_sayfasi(temiz, tarih))
        print(f"[OK] reports/{fn} yeniden üretildi")

    # 2) Derin analiz
    derin_yol = "derin-analiz.html"
    if os.path.exists(derin_yol):
        makale = makale_ayikla(derin_yol)
        temiz = derin_temizle(makale)
        with open(derin_yol, "w", encoding="utf-8") as f:
            f.write(bot.rapor_sayfasi(
                temiz, son_tarih,
                baslik="Derin Analiz",
                alt_baslik="BIST 30 &bull; Yapay zeka destekli derinlemesine analiz",
                kok_yol="derin-analiz.html",
                aciklama=("BIST 30'un günlük derinlemesine analizi: sektör değerlendirmesi, EMA ve "
                          "Wave Trend teknik okuma, osilatör-momentum yorumları ve risk senaryoları."),
            ))
        print(f"[OK] {derin_yol} yeniden üretildi")

    # 3) Teknik analiz (kayıtlı tarama verisinden)
    teknik_json = os.path.join("data", "teknik", f"{son_tarih}.json")
    teknik_satirlar = []
    if os.path.exists(teknik_json):
        teknik_satirlar = json.load(open(teknik_json, encoding="utf-8"))
        with open("teknik-analiz.html", "w", encoding="utf-8") as f:
            f.write(bot.build_teknik_html(teknik_satirlar, son_tarih))
        print(f"[OK] teknik-analiz.html yeniden üretildi ({len(teknik_satirlar)} hisse)")
    else:
        print(f"[ATLANDI] {teknik_json} yok; teknik-analiz.html olduğu gibi kaldı")

    # 4) Borsapy (mevcut tablodan ayıkla, yeni kabukla üret)
    borsapy_satirlar = borsapy_ayikla("borsapy-analiz.html")
    if borsapy_satirlar:
        with open("borsapy-analiz.html", "w", encoding="utf-8") as f:
            f.write(bot.build_borsapy_html(borsapy_satirlar, son_tarih))
        print(f"[OK] borsapy-analiz.html yeniden üretildi ({len(borsapy_satirlar)} hisse)")
    else:
        print("[ATLANDI] borsapy tablosu ayıklanamadı")

    # 5) Ana sayfa + portföy
    p = bot.load_portfolio()
    teknik_oneriler = [s for s in teknik_satirlar if s.get("genel") in ("GÜÇLÜ AL", "AL")][:6]
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(bot.build_index_html(p, raporlar, teknik_oneriler))
    print("[OK] index.html yeniden üretildi")
    if p and p.get("history"):
        with open("portfolio.html", "w", encoding="utf-8") as f:
            f.write(bot.build_portfolio_html(p))
        print("[OK] portfolio.html yeniden üretildi")
    if teknik_satirlar:
        bot.ticker_json_yaz(teknik_satirlar)
        print("[OK] ticker.json tazelendi")

    # 6) SEO dosyaları
    bot.sitemap_ve_robots_yaz(raporlar)
    print("[OK] sitemap.xml + robots.txt yazıldı")
    print("\nGÖÇ TAMAMLANDI — tüm sayfalar yeni şablonla üretildi.")


if __name__ == "__main__":
    main()
