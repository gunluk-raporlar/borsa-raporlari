# -*- coding: utf-8 -*-
"""Hafta Sonu Borsa Okulu — kurgusal YAPAY ZEKA egitmen ile hafta sonu dersleri.

Her Cumartesi ve Pazar bir ders islenir (hafta ici ASLA calismaz):
- MUFREDAT: onceden tanimli konu listesinden hafta/dogun sayisina gore sirayla secilir
  (deterministik -> ayni konu tekrar etmez; 2026-10-24'te 56 konu = 28 haftalik donem).
  Icerik politikasi (kullanici karari): turev/vadeli/kaldiracli islem konulari
  mufredata girmez.
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
    # ---- 2026-10-24 genisletme: Fintables Akademi / SPK SPL konu cervevesi
    # (konu haritasi fikir duzeyinde alindi; icerik ozgun yazilir). Mevcut
    # 26 konunun sirasi korunur (ders_sec deterministik), yeniler sona eklenir.
    ("Bilanço nasıl okunur?", "Finansal Tablolar", "Aktif-pasif dengesi; varlıklar, borçlar ve özkaynak; şirket ne kadar sağlam ayakta duruyor?"),
    ("Gelir tablosu: kâr nasıl oluşur?", "Finansal Tablolar", "Brüt kâr, faaliyet kârı, net kâr basamakları; kârın nereden geldiği (kâr kalitesi)."),
    ("Nakit akım tablosu", "Finansal Tablolar", "İşletme-yatırım-finansman akımları; net kâr ile nakdin neden aynı şey olmadığı."),
    ("F/K çarpanı (P/E)", "Temel Analiz", "Fiyatın hisse başına kâra oranı; yüksek/düşük F/K ne anlatır, ne zaman yanıltır?"),
    ("PD/DD çarpanı (P/B)", "Temel Analiz", "Fiyatın özkaynağa oranı; banka ve sigorta şirketlerinde neden ana ölçüt?"),
    ("ÇFK ve EV/ÇFK", "Temel Analiz", "Kâr yerine nakit üretimi üzerinden değerleme; borçlu şirketlerde daha adil kıyas."),
    ("ROE ve ROA", "Temel Analiz", "Özkaynak ve varlık kârlılığı; sermayenin verimliliği, çok yüksek ROE'nin tuzak tarafı."),
    ("Borçluluk oranları", "Temel Analiz", "Net borç/ÇFK, borç/özkaynak; finansman maliyeti ve kur riski şirket kârına nasıl yansır?"),
    ("Değerleme: çarpan yöntemi", "Temel Analiz", "Sektör kıyaslamasıyla adil fiyat arama; çarpan tuzakları ve sektörel farklılıklar."),
    ("Sermaye artırımı nedir?", "Piyasa Mekanizması", "Bedava/bedelli artırım ve rüçhan hakkı; seyreltme mantığı, hisseye etkisi."),
    ("Emir türleri ve işlem saatleri", "Piyasa Mekanizması", "Piyasa/limit/stop emir farkı; seans yapısı, kolektif işlem penceresi."),
    ("TÜFE ve PPI nasıl okunur?", "Makro", "Tüketici ve üretici enflasyonu; beklentiyle açıklama farkının piyasaya etkisi."),
    ("TCMB kararları nasıl yorumlanır?", "Makro", "Politika faizi, faiz koridoru, rezervler; karar metnindeki ton değişimi."),
    ("Cari açık ve dış borç", "Makro", "Dış dengeler; cari açığın finansmanı, TL ve hisse piyasasına yansıması."),
    ("Büyüme ve işsizlik verileri", "Makro", "Konjonktür göstergeleri; hangi veri hangi piyasayı ne kadar etkiler?"),
    ("Sharpe oranı ve risk-getiri", "Portföy", "Birim riske düşen fazladan getiri; strateji kıyaslamasında neyi anlatır, neyi anlatmaz."),
    ("Kademeli alım (DCA)", "Portföy", "Tek seferde almak mı, zamanda yaymak mı; ortalama maliyet mantığı."),
    ("Davranışsal hatalar: panik ve FOMO", "Portföy", "Kayıptan kaçınma, sürü psikolojisi; kuralın insana üstünlüğü."),
    # ---- 2026-10-24 SPL paralel blok (kullanıcı kararı): eksik SPL dersleri
    # temel duzeyde eklenir; TUREV/VADELI/KALDIRAC konulari MUFREDATA GIREMEZ
    # (kullanici: "borsanin kumar olan tarafi olmasin" — kalici icerik politikasi).
    ("Sermaye Piyasası Kanunu ve SPK", "Mevzuat", "Piyasayı kim düzenler, yatırımcı hangi haklara ve korumalara sahiptir; kanunun temel mantığı."),
    ("Piyasa manipülasyonu ve izinsiz işlem", "Mevzuat", "Manipülatif kalıplar nasıl görünür, yatırımcı buna karşı nasıl korunur; şüpheli hareketi nereye bildirilir."),
    ("Bilgi yükümlülüğü ve KAP", "Mevzuat", "Şirketin kamuya hangi bilgiyi ne zaman açıklaması zorunlu; KAP bildirimlerinin yatırımcı için yeri."),
    ("Yatırımcıyı kim korur: SPK, MKK, TMSF", "Mevzuat", "Kayıt, denetim ve koruma şemsiyesi; aracı kurum batarsa yatırımcıya ne olur?"),
    ("Tahvil ve bono nedir?", "SP Araçları", "Borçlanma araçları: kupon, vade, ihraççı riski; hisseyle yan yana koyunca ne anlatır?"),
    ("Tahvil fiyatı ve faiz ilişkisi", "SP Araçları", "Faiz artınca tahvil neden düşer; basit/efektif getiri farkı, vade uzunluğunun etkisi."),
    ("Yatırım fonu ve ETF", "SP Araçları", "Havuz mantığı; endeks fonu ile aktif yönetim farkı; gider oranının uzun vadeye etkisi."),
    ("Anonim şirket: ortakların hakları", "Ticaret Hukuku", "Genel kurul, yönetim kurulu; oy, rüçhan ve bilgi hakkı; hissedar şirketi nasıl etkiler?"),
    ("MKK ve takas süreci", "Piyasa Mekanizması", "Emirden hesaba: takas, saklama, kaydi sistem; işlem tarihi ile hesap geçişi arasındaki akış."),
    ("Hisse kazançlarının vergilendirilmesi", "Vergi", "Temettü stopajı, BIST pay senedi kazanç istisnası, fon/tahvil getirisinde vergi; net getiriyi kim belirler?"),
    ("Paranın zaman değeri", "Temel Analiz", "Bugünkü ve gelecekteki değer; iskonto mantığı, hisse değerlemesinin matematiği."),
    ("CAPM ve beta", "Temel Analiz", "Sistematik risk, risk primi, beklenen getiri formülü; beta yüksek hisse ne demek?"),
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


def _guncel_veri_ornekleri(konu=""):
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
    # ders grafigiyle ayni GERCEK seri: LLM de uydurma yerine ayni hisseyi anlatsin
    try:
        paket = _gercek_grafik_paketi(None, konu)
        if paket:
            seri30 = paket["seri"][-31:]
            deg = (seri30[-1] / seri30[0] - 1) * 100
            parcalar.append(f"Ders grafiği örnek hissesi {paket['ad']}: son 30 işlem günü {deg:+.1f}%, "
                            f"aralık {min(seri30):.2f}-{max(seri30):.2f} TL; bu seriyi örnekle olarak kullan.")
    except Exception:
        pass
    # gercek yillik enflasyon (TUIK) — makro dersleri icin somut rakam
    try:
        enf = json.load(open(os.path.join(bot.DATA_DIR, "enflasyon.json"), encoding="utf-8"))
        if enf.get("oran"):
            parcalar.append(f"Son açıklanan yıllık enflasyon: %{enf['oran'] * 100:.1f}.")
    except Exception:
        pass
    return " ".join(parcalar)


_PROMPT = """Sen, "BIST 30 Günlük Raporlar" sitesinin Hafta Sonu Borsa Okulu eğitmeni olan kurgusal bir YAPAY ZEKA öğretmenisin (gerçek kişi değilsin; kendini analist/direktör gibi tanıtma). Bugünün ders konusu şu:

KONU: {konu}
KONU TANIMI: {tanim}

Dersi acemi-orta seviye yatırımcıya göre, sıcak ama profesyonel bir öğretmen diliyle, dolu dolu anlat; toplam ~900-1400 kelime. Yapı (markdown, "## " başlıklarla):
## Bugünün Dersi: {konu}
## 1. Nedir? (basit benzetmeyle başla, sonra net ve tam tanım; 150-250 kelime)
## 2. Neden Önemli? (bu bilgi yatırımcıya ne kazandırır, hangi kararları değiştirir)
## 3. Nasıl Okunur / Kullanılır? (adım adım; formülü varsa düz metinle göster — örn. "F/K = Fiyat / Hisse Başına Kâr" — ve her bileşenin nereden okunduğunu söyle)
## 4. Gerçek Örnek ([GÜNCEL VERİ] bölümündeki gerçek şirket ve rakamlarla ADIM ADIM hesap göster: önce formülü yaz, sonra sayıları yerine koy, sonucu yorumla; şirket adı ya da rakam UYDURMA — veri yetersizse genel kuralı anlat)
## 5. Sık Yapılan Hatalar (2-4 madde; her hata için "neden yanlış" cümlesi de ekle)
## 6. Mini Test (2-3 soru; cevapları gizli tutma, sorunun altında ver)
## 7. Özet (3-5 madde)
## 8. Derinleşme (bu konudan sonra öğrenmesi gereken 2-3 kavram; sitedeki Teknik Tarama veya Hisse sayfalarından hangisiyle pratik yapılabilir)

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


def _esit_agirlik_endeks(n=90):
    """data/fiyat'taki tum kagitlarin gundelik getiri ortalamasindan turetilmis
    100 bazli endeks serisi (GERCEK veriden turetir; XU100 yayini yok)."""
    depolar = []
    if os.path.isdir(bot.FIYAT_DEPO_DIR):
        for f in os.listdir(bot.FIYAT_DEPO_DIR):
            if not f.endswith(".json"):
                continue
            d = bot.fiyat_deposu_oku(f[:-5])
            if len(d) >= n // 2:
                depolar.append([v for _, v in sorted(d.items())])
    if len(depolar) < 5:
        return []
    m = min(len(d) for d in depolar)
    seriler = [d[-m:] for d in depolar]
    seviye, out = 100.0, [100.0]
    for i in range(1, m):
        getiriler = [s[i] / s[i - 1] - 1 for s in seriler if s[i - 1]]
        if not getiriler:
            continue
        seviye *= (1 + sum(getiriler) / len(getiriler))
        out.append(seviye)
    return out


def _gercek_grafik_paketi(metin, konu):
    """Ders grafigi icin GERCEK fiyat paketi: {"ad","seri"[,"ad2","seri2"]}.

    Hisse tercihi: ders metninde gecen kod > konu+tarihten deterministik secim
    (her hafta ayni konu ayni hisseyle anlatilir). Endeks dersinde tum
    kagitlardan turetilmis esit agirlikli endeks; korelasyon derslerinde
    ikinci bir gercek seri de verilir. Veri yoksa None -> sematik gorsel.
    """
    kodlar = []
    try:
        _, x_satirlar = excel_tablo.veri_yukle()
        kodlar = [s.get("hisse") for s in x_satirlar if s.get("hisse")]
    except Exception:
        kodlar = []
    if not kodlar and os.path.isdir(bot.FIYAT_DEPO_DIR):
        kodlar = [f[:-5] for f in os.listdir(bot.FIYAT_DEPO_DIR) if f.endswith(".json")]
    kodlar = [k for k in kodlar if k and len(bot.fiyat_deposu_oku(k)) >= 40]
    if not kodlar:
        return None
    ad = _ders_hisse_bul(metin, [{"hisse": k} for k in kodlar])
    if not ad:
        tohum = sum(ord(c) * (i + 5) for i, c in enumerate(konu + _simdi().strftime("%Y-%m-%d")))
        ad = kodlar[tohum % len(kodlar)]
    depo = bot.fiyat_deposu_oku(ad)
    seri = [v for _, v in sorted(depo.items())][-120:]
    if len(seri) < 40:
        return None
    paket = {"ad": ad, "seri": seri}
    if konu.startswith(("Pearson", "Altın, dolar")):
        diger = [k for k in kodlar if k != ad]
        if diger:
            tohum2 = sum(ord(c) for c in ad + konu)
            ad2 = diger[tohum2 % len(diger)]
            depo2 = bot.fiyat_deposu_oku(ad2)
            seri2 = [v for _, v in sorted(depo2.items())][-120:]
            if len(seri2) >= 40:
                paket["ad2"], paket["seri2"] = ad2, seri2
    if konu.startswith("Endeksler"):
        endeks = _esit_agirlik_endeks()
        if endeks:
            paket = {"ad": "BIST 30 kağıtları (eşit ağırlıklı, 100 bazlı)", "seri": endeks}
    return paket


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


def _ders_uret(konu, tanim, veri, kategori=""):
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
                    metin = bot.llm_call(prompt, sirasi=("DISC", "AMD", "CF", "YEDEK", "OR"))
                except Exception as e:
                    logger.warning("LLM ders basarisiz: %s", e)
    if not metin or metin.startswith("(LLM"):
        logger.warning("LLM kullanilamadi; sablon ders uretilecek.")
        kat = f" ({kategori} alanında yer alır)" if kategori else ""
        return (
            f"## Bugünün Dersi: {konu}\n\n"
            f"**Özet:** {tanim}\n\n"
            f"## 1. Nedir?\nBu konu, piyasa dilinin temel yapı taşlarından biridir{kat}; "
            "aşağıdaki açıklama otomatik şablonla hazırlanmıştır (LLM erişilemedi).\n\n"
            "## 2. Neden Önemli?\nPiyasa dilini anlamak, yorumları ve göstergeleri doğru okumayı sağlar.\n\n"
            "## 3. Nasıl Okunur?\nDetaylı anlatım için bir sonraki hafta sonu dersini takip edin; "
            "sitedeki Teknik Tarama sayfasında bu göstergenin canlı değerlerini görebilirsiniz.\n\n"
            "## 4. Gerçek Örnek\n"
            + (veri or "(güncel veri alınamadı)") + "\n\n"
            "## 5. Sık Yapılan Hatalar\n- Göstergeyi tek başına sinyal sanmak.\n- Zaman dilimi belirtmeden yorum yapmak.\n\n"
            "## 6. Mini Test\n1) Bu kavram neyi ölçer?\n2) Hangi veriyle birlikte kullanılmalı?\n\n"
            "## 7. Özet\n- Kavramı tanımlayın, ardından göstergelerle doğrulayın.\n\n"
            "## 8. Derinleşme\n- Konuyu sitedeki Teknik Tarama ve Hisse sayfalarındaki canlı verilerle tekrar edin.\n\n"
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
    veri = _guncel_veri_ornekleri(konu)
    metin = _ders_uret(konu, tanim, veri, kategori)
    metin = bot._tarih_gun_duzelt(metin)
    # Grafik: GERCEK kapanis serisiyle (ders metninde gecen hisse oncelidir)
    paket = _gercek_grafik_paketi(metin, konu)
    gorsel_svg = svg_grafik.gorsel_uret(konu, g=paket)
    logger.info("Ders %d/%d: %s (%s) | grafik verisi: %s", idx + 1, len(MUFREDAT), konu, kategori,
                (paket or {}).get("ad", "şematik"))

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
        if paket:
            fig_alt = (f"Şekil: {konu} — {paket['ad']} gerçek kapanış verisi "
                       f"(son {len(paket['seri'])} işlem günü)")
        else:
            fig_alt = f"Şekil: {konu} — eğitim amaçlı şematik gösterim (gerçek piyasa verisi değildir)"
        figura = (f'<figure style="margin:0 0 18px">{gorsel_svg}'
                  f'<figcaption style="color:var(--muted);font-size:12px;margin-top:6px">{fig_alt}</figcaption></figure>')
    icerik = f"""
<div class="hero">
<h1>Hafta Sonu Borsa Okulu</h1>
<div class="meta"><span class="badge">{tarih}</span><span>Ders {idx + 1}/{len(MUFREDAT)} &bull; {kategori} &bull; Kurgusal yapay zeka eğitmen</span></div>
</div>
<article class="report" id="rapor-ses-metin">{figura}{bot.markdown_to_html(metin)}</article>
<p style="margin:22px 0 0; color:var(--muted); font-size:13px"><strong>Gerçek veriyle pratik:</strong> Aşağıdaki tablo bugünün teknik taramasından gelir — bir hücreye tıklayıp ad/formül çubuğunu görebilir, sütun başlıklarına tıklayarak sıralayabilirsin.</p>
{excel_bolumu}
{onceki}
<p style="margin-top:12px"><a href="../haftasonu-egitimi.html">&larr; Tüm borsa derslerine dön</a></p>"""
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
    # 2026-10-06 kullanici karari: kok haftasonu-egitimi.html artik 'guncel ders'
    # degil, tum derslerin kart listesi (hub). Arsiv yazildiktan SONRA hub
    # yeniden yazilir ki bugunun dersi listede ilk kart olsun.
    bot.build_hub_sayfalari()
    print(f"BORSA OKULU DERSI HAZIR: {tarih} | {konu} | ders {idx + 1}/{len(MUFREDAT)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
