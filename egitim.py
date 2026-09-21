# -*- coding: utf-8 -*-
"""Hafta Sonu Borsa Okulu — kurgusal YAPAY ZEKA egitmen ile hafta sonu dersleri.

Her Cumartesi ve Pazar bir ders islenir (hafta ici ASLA calismaz):
- MUFREDAT: onceden tanimli konu listesinden hafta/dogun sayisina gore sirayla secilir
  (deterministik -> ayni konu tekrar etmez, 26 konu = 13 haftalik donem).
- ICERIK: buyuk LLM (Z.ai GLM, yoksa sablon) konuyu ogretir: nedir, neden onemli,
  nasil okunur, gercek BIST verisiyle ornek, sik hatalar, mini test, ozet.
- KAYNAK: guncel data/teknik ve fiyat verisinden somut ornek rakamlar prompta verilir.
- YAYIN: haftasonu-egitimi.html (guncel ders) + arsiv haftasonu-egitimi/<tarih>.html.
- KIMLIK: Egitmen kurgusal bir yapay zekadir; gercek kisi/kurum degildir, unvan taklidi yok.
- UYARI: egitim amaclidir, yatirim tavsiyesi degildir.

Test icin: TARIH_SIM=YYYY-MM-DD ve SKIP_LLM=1 ortam degiskenleri.
"""
import os
import re
import sys
import json
import glob
import logging
from datetime import datetime, timedelta
import zoneinfo

os.environ.setdefault("AMD_API_KEY", "egitim")

import bot  # noqa: E402
import svg_grafik  # noqa: E402
import excel_tablo  # noqa: E402  Excel gorunumlu gerccek teknik tarama tablosu

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("egitim")

EGITIM_DIR = "egitim"
ARSIV_DIR = "haftasonu-egitimi"  # sayfa koku: haftasonu-egitimi.html
BASLANGIC_HAFTASI = "2026-09-12"  # ilk Cumartesi

# Muftedat: (konu, kategori, tek cumlelik tarif -> sablon/context icin)
MUFREDAT = [
    ("RSI (Göreceli Güç Endeksi)", "Osilatör", "0-100 arası momentum göstergesi; 70 üstü aşırı alım, 30 altı aşırı satım."),
    ("MACD nedir, nasıl okunur?", "Osilatör", "İki hareketli ortalamanın farkından türetilen trend-momentum göstergesi; sinyal çizgisi kesişimleri."),
    ("Stokastik %K", "Osilatör", "Fiyatın son dönem aralığındaki konumunu gösteren osilatör; 80/20 eşikleri."),
    ("CCI (Emtia Kanal Endeksi)", "Osilatör", "Fiyatın istatistiksel ortalamasından sapmasını ölçer; +100/-100 bölgeleri."),
    ("ADX ve trend gücü", "Trend", "Trendin yönünü değil GÜCÜNÜ ölçer; 25 üstü güçlü trend."),
    ("EMA ile SMA farkı", "Hareketli Ortalama", "Üstel ortalama eski fiyatlara daha az ağırlık verir, fiyata daha hızlı tepki verir."),
    ("Wave Trend osilatörü", "Osilatör", "Dalga temelli osilatör; WT1/WT2 kesişimi ve +53/-53 aşırı bölgeleri."),
    ("Pearson korelasyonu (r)", "İstatistik", "İki serinin birlikte hareketini -1 ile +1 arasında ölçer; r=0,82 ne anlatır?"),
    ("Regresyon kanalı", "Teknik Analiz", "Son 60 günün lineer trendi etrafına çizilen istatistiksel bant; %0 alt, %100 üst."),
    ("Destek ve direnç seviyeleri", "Teknik Analiz", "Fiyatın geçmişte durduğu/döndüğü fiyat bölgeleri; kırılım mantığı."),
    ("Trend çizgisi nasıl çizilir?", "Teknik Analiz", "Dip ve tepeleri birleştiren doğrular; yükselen/alçalan/yanlı trend."),
    ("Mum grafiği okuma", "Grafik", "Açılış-kapanış-en yüksek-en düşük; yeşil/kırmızı mum ve formasyonlar."),
    ("Hacim analizi", "Grafik", "Hacim fiyat hareketini doğrular mı? Yükselişte artan hacim ne anlama gelir?"),
    ("Tavan ve taban fiyat marjı", "Piyasa Mekanizması", "BIST'te günlük fiyat hareketi %10 ile sınırlıdır; tavan/taban ne zaman olur?"),
    ("Açığa satış nedir?", "Piyasa Mekanizması", "Önce satıp sonra geri alma; BIST'te koşulları ve riskleri."),
    ("Yukarı Adım Kuralı (uptick)", "Piyasa Mekanizması", "Düşüş dönemlerinde açığa satışı sınırlayan kural; piyasayı nasıl etkiler?"),
    ("Endeksler: BIST 30, BIST 100, XU100", "Piyasa Mekanizması", "Endeks nedir, ağırlıklandırma nasıl yapılır, hangi endeks neyi anlatır?"),
    ("Portföy çeşitlendirme", "Portföy", "Sepet mantığı; korelasyonu düşük varlıklarla riski azaltma."),
    ("Stop-loss ve risk yönetimi", "Portföy", "Kaybı sınırlamak: pozisyon boyutu, stop seviyesi, risk/getiri oranı."),
    ("Bileşik getiri", "Portföy", "Getirinin üstüne getiri; uzun vadede neden bu kadar güçlü?"),
    ("Enflasyon ve reel getiri", "Makro", "Nominal getiri eksi enflasyon; %28,4 enflasyonda %2 kazanç ne demek?"),
    ("Faiz ve hisse ilişkisi", "Makro", "Faiz artarsa hisse ne olur? İskonto oranı ve alternatif maliyet."),
    ("Altın, dolar ve hisse korelasyonu", "Makro", "Güvenli liman mantığı; risk iştahı açılıp kapanınca neye para akar?"),
    ("Temettü nedir?", "Şirket", "Kâr dağıtımı; temettü verimi ve 'ex-tarih' mantığı."),
    ("Halka arz nedir?", "Şirket", "Şirketin borsaya açılması; talep toplama, tavan serisi ve riskler."),
    ("Likidite nedir?", "Piyasa Mekanizması", "Bir varlığı fiyatı bozmadan alıp satabilme kolaylığı; işlem hacmiyle ilişkisi."),
]

DUMMY_ANAHTARLAR = {"", "teknik-tarama", "derin-analiz", "radyo", "migrate-rebuild",
                    "radyo-test", "test", "x", "haftasonu", "egitim", "dummy-ayri-test"}


def _simdi():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    sim = os.environ.get("TARIH_SIM", "")
    if sim:
        try:
            return datetime.strptime(sim, "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            pass
    return datetime.now(tz)


def ders_sec(simdi):
    """Tarihe gore muftedat indeksi: baslangic Cumartesi'sinden itibaren
    her hafta 2 ders (Cmt=0, Paz=1)."""
    epoch = datetime.strptime(BASLANGIC_HAFTASI, "%Y-%m-%d").replace(tzinfo=simdi.tzinfo)
    gun = (simdi.date() - epoch.date()).days
    if gun < 0:
        return 0, MUFREDAT[0]
    hafta = gun // 7
    hafta_sonu_gunu = simdi.weekday()  # 5=Cumartesi, 6=Pazar
    gun_no = 0 if hafta_sonu_gunu == 5 else (1 if hafta_sonu_gunu == 6 else 0)
    idx = (hafta * 2 + gun_no) % len(MUFREDAT)
    return idx, MUFREDAT[idx]


def _guncel_veri_ornekleri():
    """Derse somut ornek verebilmek icin guncel tarama/portfoy verisinden kisa ozet."""
    parcalar = []
    t_dosya = None
    klasor = os.path.join(bot.DATA_DIR, "teknik")
    if os.path.isdir(klasor):
        dosyalar = sorted(f for f in os.listdir(klasor) if f.endswith(".json"))
        t_dosya = dosyalar[-1] if dosyalar else None
    if t_dosya:
        try:
            satirlar = json.load(open(os.path.join(klasor, t_dosya), encoding="utf-8"))
            if satirlar:
                guclu = [s for s in satirlar if s.get("genel") == "GÜÇLÜ AL"][:3]
                hareketli = sorted(satirlar, key=lambda s: abs(s.get("gunluk", 0) or 0), reverse=True)[:3]
                if guclu:
                    parcalar.append("Güçlü al sinyali örnekleri: " + "; ".join(
                        f"{s['hisse']} son {bot._tl_okunus(s['son'])} TL, günlük {s.get('gunluk', 0):+.2f}%, "
                        f"kanal %{s.get('konum', 0):.0f}, r={s.get('r', 0):.2f}"
                        for s in guclu) + ".")
                if hareketli:
                    parcalar.append("En hareketli hisseler: " + "; ".join(
                        f"{s['hisse']} son {bot._tl_okunus(s['son'])} TL, günlük {s.get('gunluk', 0):+.2f}%, "
                        f"kanal %{s.get('konum', 0):.0f}, r={s.get('r', 0):.2f}"
                        for s in hareketli) + ".")
        except Exception:
            pass
    return " ".join(parcalar)


_PROMPT = """Sen, "BIST 30 Günlük Raporlar" sitesinin Hafta Sonu Borsa Okulu eğitmeni olan kurgusal bir YAPAY ZEKA öğretmenisin (gerçek kişi değilsin; kendini analist/direktör gibi tanıtma). Bugünün ders konusu şu:

KONU: {konu}
KONU TANIMI: {tanim}

Dersi acemi-orta seviye yatırımcıya göre, sıcak ve net bir öğretmen diliyle anlat. Yapı (markdown, "## " başlıklarla):
## Bugünün Dersi: {konu}
## 1. Nedir? (basit benzetmeyle başla, sonra net tanım)
## 2. Neden Önemli? (bu bilgi yatırımcıya ne kazandırır)
## 3. Nasıl Okunur / Kullanılır? (adım adım; eğer göstergeyse hangi değer ne anlama gelir)
## 4. Gerçek Örnek (mümkünse [GÜNCEL VERİ] bölümündeki gerçek rakamları kullan; somut ol)
## 5. Sık Yapılan Hatalar (2-4 madde)
## 6. Mini Test (2-3 soru; cevapları gizli tutma, sorunun altında ver)
## 7. Özet (3-5 madde)

Kurallar:
- YALNIZCA genel geçer eğitim bilgisi + [GÜNCEL VERİ] bölümündeki veriyi kullan; dışarıdan rakam/veri uydurma.
- Rakam geçirirken "… TL", "%…" biçiminde yaz; seslendirme otomatik çevirir.
- Kimlik satırı yazma (Tarih:, Yayıncı:, Analist: vb. YOK). Tarih/gün adı yazma.
- Kesin "şu hisseyi al" yönlendirmesi yapma; eğitim tonu.
- Doğru Türkçe karakterler; kısa ve akılda kalıcı cümleler.
- Sayfa sonuna: "Bu ders eğitim amaçlıdır, yatırım tavsiyesi değildir."

[GÜNCEL VERİ]
{veri}"""


def _ders_hisse_bul(metin, satirlar):
    """Ders metninde gecen ilk BIST kodunu dondurur (tablo onsecimi icin).
    Ders TTKOM'u ornek olarak anlattıysa tablo o hucreyi acilirken secili gosterir."""
    if not metin or not satirlar:
        return None
    en_iyi, en_konum = None, None
    for s in satirlar:
        kod = s.get("hisse")
        if not kod:
            continue
        m = re.search(rf"\b{re.escape(kod)}\b", metin)
        if m and (en_konum is None or m.start() < en_konum):
            en_iyi, en_konum = kod, m.start()
    return en_iyi


def _excel_veri_bolumu(metin=None):
    """data/teknik'teki en guncel taramayi Excel gorunumlu tablo olarak dondurur.
    Veri yoksa bos string doner (sayfa tablosuz yayinlanir)."""
    try:
        x_tarih, x_satirlar = excel_tablo.veri_yukle()
        if not x_satirlar:
            return ""
        on_hisse = _ders_hisse_bul(metin, x_satirlar)
        if not on_hisse:
            on_hisse = max(x_satirlar, key=lambda s: abs(s.get("gunluk", 0) or 0)).get("hisse")
        return excel_tablo.excel_tablo_html(
            x_satirlar, x_tarih,
            baslik="TEKNIK-TARAMA.XLSX",
            preselect_hisse=on_hisse, preselect_alan="gunluk")
    except Exception as e:
        logger.warning("Excel tablo uretilemedi (sayfa tablosuz devam ediyor): %s", e)
        return ""


def _ders_uret(konu, tanim, veri):
    prompt = _PROMPT.format(konu=konu, tanim=tanim, veri=veri or "(guncel veri alinamadi)")
    metin = None
    if not os.environ.get("SKIP_LLM") == "1":
        if os.environ.get("ZAI_API_KEY") and os.environ["ZAI_API_KEY"] not in DUMMY_ANAHTARLAR:
            try:
                metin = bot._zai_call(prompt)
            except Exception as e:
                logger.warning("Z.ai ders denemesi basarisiz: %s", e)
        if not metin or metin.startswith("(LLM"):
            keys = {k: os.environ.get(k, "") for k in ("AMD_API_KEY", "CF_API_KEY", "ALT_API_KEY", "OR_API_KEY")}
            if any(v and v not in DUMMY_ANAHTARLAR for v in keys.values()):
                try:
                    metin = bot.llm_call(prompt, sirasi=("AMD", "CF", "YEDEK", "OR"))
                except Exception as e:
                    logger.warning("LLM ders basarisiz: %s", e)
    if not metin or metin.startswith("(LLM"):
        logger.warning("LLM kullanilamadi; sablon ders uretilecek.")
        return (
            f"## Bugünün Dersi: {konu}\n\n"
            f"**Özet:** {tanim}\n\n"
            "## 1. Nedir?\nBu konu teknik analizde sıkça kullanılan temel kavramlardan biridir; "
            "aşağıdaki açıklama otomatik şablonla hazırlanmıştır (LLM erişilemedi).\n\n"
            "## 2. Neden Önemli?\nPiyasa dilini anlamak, yorumları ve göstergeleri doğru okumayı sağlar.\n\n"
            "## 3. Nasıl Okunur?\nDetaylı anlatım için bir sonraki hafta sonu dersini takip edin; "
            "sitedeki Teknik Tarama sayfasında bu göstergenin canlı değerlerini görebilirsiniz.\n\n"
            "## 4. Gerçek Örnek\n"
            + (veri or "(güncel veri alınamadı)") + "\n\n"
            "## 5. Sık Yapılan Hatalar\n- Göstergeyi tek başına sinyal sanmak.\n- Zaman dilimi belirtmeden yorum yapmak.\n\n"
            "## 6. Mini Test\n1) Bu kavram neyi ölçer?\n2) Hangi veriyle birlikte kullanılmalı?\n\n"
            "## 7. Özet\n- Kavramı tanımlayın, ardından göstergelerle doğrulayın.\n\n"
            "_Bu ders eğitim amaçlıdır, yatırım tavsiyesi değildir._"
        )
    return bot.rapor_son_islem(metin)


def main():
    simdi = _simdi()
    tarih = simdi.strftime("%Y-%m-%d")
    if simdi.weekday() not in (5, 6):
        print("HAFTA ICI: borsa okulu dersi uretilmedi (sadece Cumartesi/Pazar islenir).", flush=True)
        return 0

    idx, (konu, kategori, tanim) = ders_sec(simdi)
    veri = _guncel_veri_ornekleri()
    gorsel_svg = svg_grafik.gorsel_uret(konu)
    logger.info("Ders %d/%d: %s (%s)", idx + 1, len(MUFREDAT), konu, kategori)
    metin = _ders_uret(konu, tanim, veri)
    metin = bot._tarih_gun_duzelt(metin)

    # Dersin altina: guncel taramanin Excel gorunumlu gerccek veri tablosu
    excel_bolumu = _excel_veri_bolumu(metin)

    # Onceki dersler listesi
    onceki = ""
    if os.path.isdir(ARSIV_DIR):
        liste = sorted((f for f in os.listdir(ARSIV_DIR) if f.endswith(".html")), reverse=True)
        liste = [f for f in liste if f != f"{tarih}.html"]
        if liste:
            baglar = " ".join(
                f'<a href="{ARSIV_DIR}/{f}" style="margin:0 10px 0 0">{bot._tr_tarih(f[:-5])}</a>'
                for f in liste[:6])
            onceki = f'<p style="margin-top:16px; color:var(--muted); font-size:13px"><strong>Önceki dersler:</strong> {baglar}</p>'

    figura = ""
    if gorsel_svg:
        figura = (f'<figure style="margin:0 0 18px">{gorsel_svg}'
                  f'<figcaption style="color:var(--muted);font-size:12px;margin-top:6px">Şekil: {konu} — eğitim amaçlı şematik gösterim (gerçek piyasa verisi değildir)</figcaption></figure>')
    icerik = f"""
<div class="hero">
<h1>Hafta Sonu Borsa Okulu</h1>
<div class="meta"><span class="badge">{tarih}</span><span>Ders {idx + 1}/{len(MUFREDAT)} &bull; {kategori} &bull; Kurgusal yapay zeka eğitmen</span></div>
</div>
<article class="report" id="rapor-ses-metin">{figura}{bot.markdown_to_html(metin)}</article>
<p style="margin:22px 0 0; color:var(--muted); font-size:13px"><strong>Gerçek veriyle pratik:</strong> Aşağıdaki tablo bugünün teknik taramasından gelir — bir hücreye tıklayıp ad/formül çubuğunu görebilir, sütun başlıklarına tıklayarak sıralayabilirsin.</p>
{excel_bolumu}
{onceki}
<p style="margin-top:12px"><a href="index.html">&larr; Ana sayfaya dön</a></p>"""
    html = bot._sayfa(
        f"Hafta Sonu Borsa Okulu - {tarih}", icerik, aktif="egitim", yol="haftasonu-egitimi.html",
        aciklama="Hafta sonu borsa eğitimi: yapay zeka eğitmen her Cumartesi/Pazar bir terim, gösterge veya grafik konusunu gerçek BIST verisiyle anlatır.",
    )
    with open("haftasonu-egitimi.html", "w", encoding="utf-8") as f:
        f.write(html)
    # Tarihli arsiv sayfasi KOK DIZIN sayfasinin kopyasi degil: kok="../"
    # ile uretilir (CSS/menu linkleri alt klasorde calisir) ve canonical
    # kendi URL'ini gosterir (aksi halde arama motorlari arsivi indekslemez).
    os.makedirs(ARSIV_DIR, exist_ok=True)
    alt_html = bot._sayfa(
        f"Hafta Sonu Borsa Okulu - {tarih}", icerik, aktif="egitim", yol=f"haftasonu-egitimi/{tarih}.html",
        kok="../",
        aciklama="Hafta sonu borsa eğitimi: yapay zeka eğitmen her Cumartesi/Pazar bir terim, gösterge veya grafik konusunu gerçek BIST verisiyle anlatır.",
    )
    with open(os.path.join(ARSIV_DIR, f"{tarih}.html"), "w", encoding="utf-8") as f:
        f.write(alt_html)
    print(f"BORSA OKULU DERSI HAZIR: {tarih} | {konu} | ders {idx + 1}/{len(MUFREDAT)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
