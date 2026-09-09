# -*- coding: utf-8 -*-
"""BIST Radyo: iki kurgusal YAPAY ZEKA sunucunun (Ela & Mert) piyasa sohbeti.

radyo.yml (GitHub Actions) tarafindan piyasa gunlerinde TSI ~10:10 / 13:30 / 18:05
cagrilir. Akis:
  1. GUNUN VERISI: data/news, data/teknik (son tarama), portfolio.json, data/prices
  2. SOHRET: LLM (varsa Z.ai, degilse bot.llm_call zinciri) iki sunucu arasinda
     dogal bir sohbet yazar; LLM erisilemezse veriden sablon sohbet uretilir.
  3. SESLENDIRME: edge-tts (Microsoft tr-TR neural sesler) — Ela = EmelNeural
     (kadin), Mert = AhmetNeural (erkek). Her replik ayri sentezlenir, ffmpeg ile
     kisa sessizliklerle tek MP3'te birlestirilir.
  4. YAYIN: radyo/<tarih>-<bolum>.mp3 yazilir, radyo/indeks.json guncellenir,
     7 gunden eski yayinlar temizlenir (repo sismesin).

ONEMLI NOT: Ela ve Mert gercek kisi degil, kurgusal YAPAY ZEKA sunuculardir.
Icerik bilgilendirme amaclidir, yatirim tavsiyesi degildir.
"""
import os
import re
import sys
import json
import glob
import shutil
import asyncio
import logging
from datetime import datetime, timedelta
import zoneinfo

# bot.py import ederken en az bir LLM anahtari ister; burada anahtar yoksa
# sablon sohbete dusulur, o yuzden kukla deger gecilir.
os.environ.setdefault("AMD_API_KEY", "radyo")

import bot  # noqa: E402

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger("radyo")

RADYO_DIR = "radyo"
TMP_DIR = os.path.join(RADYO_DIR, "_tmp")

SESLER = {"ELA": "tr-TR-EmelNeural", "MERT": "tr-TR-AhmetNeural"}

BOLUM_ADLARI = {  # (saat araligi TSI) -> ad
    "acilis": "Açılış Yayını",
    "ogle": "Öğle Yayını",
    "kapanis": "Kapanış Yayını",
}


def _bolum_belirle():
    """Simdiki saate gore bolum adi; env ile ezilebilir (BOLUM=acilis|ogle|kapanis)."""
    env = os.environ.get("BOLUM", "").strip().lower()
    if env in BOLUM_ADLARI:
        return env
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    dakika = datetime.now(tz).hour * 60 + datetime.now(tz).minute
    if dakika < 12 * 60:
        return "acilis"
    if dakika < 16 * 60:
        return "ogle"
    return "kapanis"


# ---------- Turkce sayi okunusu (sesli okumada 224,10 TL -> lira/kurus) ----------
_BIRLER = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_ONLAR = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_BASAMAK = ["", "bin", "milyon", "milyar"]


def _uc_hane_oku(n):
    yuz, kalan = divmod(n, 100)
    parcalar = []
    if yuz:
        parcalar.append("yüz" if yuz == 1 else _BIRLER[yuz] + " yüz")
    if kalan >= 10:
        parcalar.append(_ONLAR[kalan // 10])
        kalan = kalan % 10
    if kalan:
        parcalar.append(_BIRLER[kalan])
    return " ".join(parcalar)


def _tam_sayi_oku(n):
    if n == 0:
        return "sıfır"
    gruplar = []
    while n:
        gruplar.append(n % 1000)
        n //= 1000
    parcalar = []
    for i in range(len(gruplar) - 1, -1, -1):
        g = gruplar[i]
        if not g:
            continue
        oku = _uc_hane_oku(g)
        if i == 1 and g == 1:
            oku = "bin"  # 1.000 -> "bin", "bir bin" degil
        parcalar.append(oku + (" " + _BASAMAK[i] if i else ""))
    return " ".join(parcalar)


def _tl_konusma_metni(metin):
    """Metindeki '224,10 TL' / '2.450,00 TL' kalıplarını dogal konusmaya cevirir:
    '224 lira 10 kuruş', '2 bin 450 lira'. (LLM ciktisi ve sablon icin ortak.)"""

    def _cevir(m):
        tam = m.group(1).replace(".", "")
        kurus = int(m.group(2) or 0)
        tam_sayi = int(tam)
        ana = _tam_sayi_oku(tam_sayi)
        if kurus:
            k = _tam_sayi_oku(kurus)
            return f"{ana} lira {k} kuruş"
        return f"{ana} lira"

    return re.sub(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*TL\b", _cevir, metin)


# ---------- Veri toplama ----------
def _son_dosya(kategori):
    klasor = os.path.join(bot.DATA_DIR, kategori)
    if not os.path.isdir(klasor):
        return None
    dosyalar = sorted(f for f in os.listdir(klasor) if f.endswith(".json"))
    return dosyalar[-1] if dosyalar else None


def _gecmis_yayin_metni(bugun, limit=5, max_karakter=2200):
    """Eski yayin transkriptlerini hafiza olarak okur (data/radyo/*.json).
    Boylece sunucular onceki yayinlarda soylediklerine atif yapabilir:
    'Dün kapanışta temkinli olalım demiştik...' gibi dogal sureklilik olusur.
    En yeni 5 yayin alinir; cok uzun olmamasi icin karakter siniri vardir."""
    klasor = os.path.join(bot.DATA_DIR, "radyo")
    if not os.path.isdir(klasor):
        return ""
    dosyalar = sorted(f for f in os.listdir(klasor) if f.endswith(".json"))
    parcalar = []
    toplam = 0
    for f in reversed(dosyalar):
        if f.startswith(bugun):
            continue  # bugunun kendi yayini henuz hafiza degil
        try:
            d = json.load(open(os.path.join(klasor, f), encoding="utf-8"))
        except Exception:
            continue
        satirlar = d.get("satirlar") or []
        if not satirlar:
            continue
        blok = f"-- {d.get('tarih', f[:-5])} ({d.get('bolum', '')}): " + " ".join(satirlar[:14])
        if len(blok) > 1600:
            blok = blok[:1600] + "..."
        parcalar.append(blok)
        toplam += len(blok)
        if len(parcalar) >= limit or toplam > max_karakter:
            break
    return "\n".join(parcalar) if parcalar else ""


def _transkript_kaydet(veri, satirlar):
    """Uretilen sohbeti data/radyo/<tarih>-<bolum>.json olarak saklar.
    Bu dosyalar repoda kalici hafizadir: hem sonraki yayinlar icin (sureklilik)
    hem de ileride video uretimi (altyazi/goruntu senkronu) icin kullanilabilir."""
    klasor = os.path.join(bot.DATA_DIR, "radyo")
    os.makedirs(klasor, exist_ok=True)
    tarih = veri.get("tarih") or veri.get("bugun") or datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    yol = os.path.join(klasor, f"{tarih}-{veri['bolum']}.json")
    kayit = {
        "tarih": tarih,
        "bolum": veri["bolum"],
        "guncelleme": veri.get("guncelleme", ""),
        "satirlar": satirlar,
    }
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False, indent=1)
    return yol


def _gunun_verisi():
    """Sohbetin dayanacagi veri ozetini toplar (LLM promptuna ve sablona ortak)."""
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    bugun = datetime.now(tz).strftime("%Y-%m-%d")

    # Haberler (en guncel kayit; bugunku yoksa en yakin)
    haberler = []
    h_dosya = _son_dosya("news")
    if h_dosya:
        try:
            ham = json.load(open(os.path.join(bot.DATA_DIR, "news", h_dosya), encoding="utf-8"))[:6]
            # kayit icindeki "[KaynakAdi] baslik" kalibini konusma icin soy
            haberler = [re.sub(r"^\[[^\]]*\]\s*", "", h).strip() for h in ham]
        except Exception:
            pass

    # Teknik tarama (en son kayit)
    tarama = []
    t_dosya = _son_dosya("teknik")
    if t_dosya:
        try:
            tarama = json.load(open(os.path.join(bot.DATA_DIR, "teknik", t_dosya), encoding="utf-8"))
        except Exception:
            pass
    guclu = [s for s in tarama if s.get("genel") == "GÜÇLÜ AL"][:5]
    en_hareketli = sorted(tarama, key=lambda s: abs(s.get("gunluk", 0) or 0), reverse=True)[:3]

    # Portfoy + kiyaslamalar
    portfoy = bot.load_portfolio()
    pf_ozet = ""
    if portfoy and portfoy.get("history"):
        son = portfoy["history"][-1]
        b = son.get("benchmarks") or {}
        pf_ozet = (f"Portföy toplam değeri {bot._tl_okunus(son['total'])} TL "
                   f"(günlük {son['daily_pct']:+.2f}%, toplam {son['pct']:+.2f}%). "
                   f"Karşılaştırmalar: altın {bot._tl_okunus(b.get('GOLD', 0))} TL, "
                   f"dolar {bot._tl_okunus(b.get('USD', 0))} TL, "
                   f"mevduat {bot._tl_okunus(b.get('DEPOSIT', 0))} TL.")
    # Onceki yayinlarin transkriptleri (hafiza / sureklilik)
    gecmis = _gecmis_yayin_metni(bugun)
    return {
        "bugun": bugun,
        "guncelleme": datetime.now(tz).strftime("%d.%m %H:%M"),
        "haberler": haberler,
        "guclu": guclu,
        "en_hareketli": en_hareketli,
        "pf_ozet": pf_ozet,
        "gecmis": gecmis,
        "tarama_sayisi": len(tarama),
    }


# ---------- Sohbet metni ----------
_PROMPT_SABLON = """Sen bir ekonomi radyosu sohbet yazari yapay zekasısın. BIST Radyo için iki kurgusal sunucunun (Ela ve Mert) DOĞAL bir piyasa programını yaz. İkisi de GERÇEK KİŞİ DEĞİL, yapay zeka sunuculardır; kimlik/unvan taklidi yapma (analist, direktör vb. demeyin). Program; birbirine laf atan, soru soran, aynı fikirde olmayabilen, yorum yapan ve senaryolu beklentisini paylaşan iki sunucunun sohbeti gibi olmalı — tebliğ/duyuru değil, sohbet.

KARAKTERLER (kurgusal, abartısız):
- ELA: makro tarafı ağır basar; sakin, meraklı, soru sorar, rakamların ardındaki hikayeyi arar.
- MERT: piyasa/teknik tarafı ağır basar; biraz daha atak ve esprili, sezgiyle konuşur, Ela'nın fikrine bazen katılır bazen nazikçe karşı çıkar.

FORMAT:
- Her satır TAM OLARAK "ELA: " veya "MERT: " ile başlar, karşılıklı diyalog halinde ilerler.
- Toplam 18-24 replik. Replikler KISA ve konuşma dilinde: 1-3 cümle; soru, tepki, onay, karşı çıkma, espri serpiştir. Madde işareti/tablo/başlık KULLANMA.
- Bazı replikler birbirine bağlansın (Mert'in sorusuna Ela cevap versin), böylece kopuk monolog yerine gerçek bir sohbet olsun.

AKIŞ (bölüme göre esnet):
1) Selamlama + günün havası (hissiyat, ilk izlenim)
2) Gündem: haberleri kendi cümleleriyle YORUMLAYIN (sadece okumayın): bu ne anlama gelir, kime ne yarar/zarar
3) Öne çıkan hisseler ve hareketler: neden hareket etmiş olabileceğine dair sohbet/yorum
4) Deneme portföyü + altın/dolar karşılaştırması üzerine yorum
5) TAHMİN BÖLÜMÜ: Mert ve Ela günün geri kalanı/yarın için SENARYOLU beklentilerini söyler ("bence...", "eğer ... olursa ...", "benim okumam şöyle..."); kesin iddia değil, kişisel AI yorumu olduğu hissettirilsin.
6) Kapanış: kısa toparlama + bir kez "Bu yayın bilgilendirme amaçlıdır, yatırım tavsiyesi değildir; tahminler yapay zekanın görüşüdür."

HAFIZA (çok önemli): [GEÇMİŞ YAYINLAR] bölümündeki eski yayınlardan bir-iki tanesine doğal biçimde atıf yapın ("Dün kapanışta... demiştik, bugün ... görüyoruz" gibi). Geçmişte söylediklerinizle çelişiyorsanız bunu açıkça söyleyin ("o zaman temkinliydik, haklı çıktık" ya da "yanılmışız, nedenini konuşalım").

SINIRLAR:
- Somut fiyatlar ve veriler YALNIZCA sağlanan bölümlerden alınır; dışarıdan uydurma rakam ekleme. Rakamları metinde "224,10 TL" biçiminde yaz (seslendirme otomatik çevirir).
- Tahminler senaryolu ve görüş niteliğinde olsun; "kesin" ifadelerden ve doğrudan al-sat yönlendirmesinden kaçının.
- Türkçe karakterlere dikkat.

[GEÇMİŞ YAYINLAR]
{gecmis}

[GÜNÜN HABERLERİ]
{haberler}

[TEKNİK TARAMA - ÖNE ÇIKANLAR]
{guclu}

[EN HAREKETLİ HİSSELER]
{hareketli}

[PORTFÖY]
{pf}"""


def _sablon_sohbet(v):
    """LLM erisilemezse veriden dogal gorunen bir diyalog kurar."""
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    gun = datetime.now(tz).strftime("%A").lower()
    gunler = {"monday": "Pazartesi", "tuesday": "Salı", "wednesday": "Çarşamba",
              "thursday": "Perşembe", "friday": "Cuma"}
    gun_adi = gunler.get(gun, "")
    satirlar = [
        f"ELA: Merhaba, BIST Radyo'ya hoş geldiniz! Bugün {gun_adi}, piyasalardaki gelişmeleri birlikte değerlendireceğiz.",
        f"MERT: Merhaba Ela! Bugün {v['tarama_sayisi']} BIST 30 hissesi tarandı. Önce gündeme bakalım: {v['haberler'][0] if v['haberler'] else 'yurt içinde önemli bir veri akışı yok'}.",
    ]
    if v["haberler"]:
        for h in v["haberler"][1:3]:
            satirlar.append(f"ELA: Bir de şu başlık dikkat çekiyor: {h}.")
    if v["guclu"]:
        satirlar.append("MERT: Teknik tarafta güçlü al sinyali veren hisselere bakalım.")
        for s in v["guclu"]:
            satirlar.append(
                f"ELA: {s['hisse']}, {bot._tl_okunus(s['son'])} TL'den işlem görüyor ve günlük "
                f"{s['gunluk']:+.2f} yüzde hareket etmiş durumda."
            )
    if v["en_hareketli"]:
        satirlar.append("MERT: Peki en çok hareket edenler kimler?")
        for s in v["en_hareketli"][:2]:
            yon = "yükseliş" if (s.get("gunluk") or 0) >= 0 else "düşüş"
            satirlar.append(f"ELA: {s['hisse']} tarafında {yon} var; günlük değişim {abs(s['gunluk'] or 0):.2f} yüzde.")
    if v["pf_ozet"]:
        satirlar.append(f"MERT: Deneme portföyüne bakacak olursak: {v['pf_ozet']}")
    satirlar.append("ELA: Benim okumam şöyle: gün içinde seçici alımlar sürebilir ama oynaklık yüksek, bu yüzden temkinli kalmakta fayda var.")
    satirlar.append("MERT: Katılıyorum; bence günün kalanında piyasa dengelenmeye çalışacak. Tabii bu benim tahminim, yatırım tavsiyesi değil.")
    satirlar.append("ELA: Özetle piyasada seçici ve temkinli bir hava hâkim; gelişmeleri takip etmeye devam edeceğiz.")
    satirlar.append("MERT: Unutmayın, bu yayın bilgilendirme amaçlıdır, yatırım tavsiyesi değildir. Hoşça kalın!")
    return satirlar


def _sohbet_uret(v):
    """Once LLM dener; sonuc bozuk/ulasilamazsa sablona dusulur."""
    prompt = _PROMPT_SABLON.format(
        gecmis=v.get("gecmis") or "(henüz eski yayın yok)",
        haberler="\n".join("- " + h for h in v["haberler"]) or "(bugun haber alinamadi)",
        guclu="\n".join(
            f"{s['hisse']} ({s.get('sektor', '')}): {bot._tl_okunus(s['son'])} TL, gunluk {s['gunluk']:+.2f}%"
            for s in v["guclu"]) or "(guclu al sinyali yok)",
        hareketli="\n".join(
            f"{s['hisse']}: gunluk {s['gunluk']:+.2f}%"
            for s in v["en_hareketli"]) or "(veri yok)",
        pf=v["pf_ozet"] or "(portfoy verisi yok)",
    )
    metin = None
    if os.environ.get("ZAI_API_KEY"):
        try:
            metin = bot._zai_call(prompt)
        except Exception as e:
            logger.warning("Z.ai sohbet denemesi basarisiz: %s", e)
    if not metin or metin.startswith("(LLM"):
        try:
            metin = bot.llm_call(prompt, sirasi=("AMD", "CF", "YEDEK", "OR"))
        except Exception as e:
            logger.warning("LLM sohbet basarisiz: %s", e)
            metin = None
    if not metin or metin.startswith("(LLM"):
        logger.warning("LLM kullanilamadi; veri tabanli sablon sohbet kullanilacak.")
        return _sablon_sohbet(v)

    satirlar = []
    for satir in metin.splitlines():
        s = satir.strip()
        if not s:
            continue
        m = re.match(r"^(ELA|MERT)\s*[:：]\s*(.+)$", s, re.IGNORECASE | re.DOTALL)
        if not m:
            continue  # LLM kurallara uymayan satiri at
        konusan = m.group(1).upper()
        icerik = re.sub(r"\s+", " ", m.group(2)).strip()
        if icerik and not icerik.lower().startswith(("ela:", "mert:")):
            satirlar.append(f"{konusan}: {icerik}")
    # Program uzunlugu: en az 12 replik (yoksa sablona), en fazla 26 (ses dosyasi sismesin)
    if len(satirlar) < 12:
        logger.warning("LLM sohbeti yetersiz (%d replik); sablon kullanilacak.", len(satirlar))
        return _sablon_sohbet(v)
    if len(satirlar) > 26:
        satirlar = satirlar[:26]
    return satirlar


# ---------- Seslendirme ----------
def _ffmpeg_bul():
    import shutil as _sh
    ff = _sh.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


async def _replik_seslendir(replik, konusan, cikti_yolu):
    from edge_tts import Communicate
    ses = SESLER.get(konusan, SESLER["ELA"])
    metin = _tl_konusma_metni(replik)
    # cok uzun replikleri edge-tts sinirina takilmamak icin oldugu gibi gonder
    com = Communicate(metin, ses, rate="+0%")
    await com.save(cikti_yolu)
    return cikti_yolu


def _birlestir(parcalar, sessizlik, cikti_yolu, ffmpeg):
    """Parcalari (mp3) kisa sessizliklerle tek mp3 yapar: ffmpeg concat filtresi."""
    girdiler = []
    for p in parcalar:
        girdiler.append(p)
        girdiler.append(sessizlik)
    if not girdiler:
        return False
    # gecici wav'lara cevir (farkli parametreleri sorunsuz birlestirmek icin)
    tmp_wavs = []
    for i, p in enumerate(girdiler):
        w = os.path.join(TMP_DIR, f"p{i:03d}.wav")
        r = os.system(f'"{ffmpeg}" -y -loglevel error -i "{p}" -ar 24000 -ac 1 "{w}"')
        if r != 0:
            return False
        tmp_wavs.append(w)
    liste = os.path.join(TMP_DIR, "liste.txt")
    with open(liste, "w", encoding="utf-8") as f:
        for w in tmp_wavs:
            f.write(f"file '{os.path.abspath(w)}'\n")
    r = os.system(
        f'"{ffmpeg}" -y -loglevel error -f concat -safe 0 -i "{liste}" '
        f'-c:a libmp3lame -b:a 48k -ar 24000 -ac 1 "{cikti_yolu}"'
    )
    return r == 0


def _sessizlik_mp3(ffmpeg):
    y = os.path.join(TMP_DIR, "sus.mp3")
    r = os.system(f'"{ffmpeg}" -y -loglevel error -f lavfi -i anullsrc=r=24000:cl=mono -t 0.4 -c:a libmp3lame -b:a 48k "{y}"')
    return y if r == 0 else None


def _sure_sn(dosya):
    try:
        import subprocess
        ff = _ffmpeg_bul()
        if not ff:
            return 0
        r = subprocess.run([ff, "-i", dosya], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(float(m.group(3)))
    except Exception:
        pass
    return 0


def main():
    tz = zoneinfo.ZoneInfo("Europe/Istanbul")
    simdi = datetime.now(tz)
    tarih = simdi.strftime("%Y-%m-%d")
    bolum = _bolum_belirle()
    bolum_adi = BOLUM_ADLARI[bolum]
    os.makedirs(RADYO_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    ffmpeg = _ffmpeg_bul()
    if not ffmpeg:
        logger.error("ffmpeg bulunamadi; yayin uretilemedi.")
        return 1
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        logger.error("edge-tts kurulu degil (pip install edge-tts).")
        return 1

    veri = _gunun_verisi()
    veri["bolum"] = bolum
    satirlar = _sohbet_uret(veri)
    logger.info("Sohbet hazir: %d replik (%s - %s)", len(satirlar), tarih, bolum_adi)

    # Transkripti hafizaya kaydet (data/radyo/): gelecek yayinlar icin sureklilik
    # + ileride video uretimi icin altyazi temeli. Seslendirme oncesi kaydedilir;
    # boylece ses hata verse bile sohbet kaybolmaz.
    try:
        tr_yol = _transkript_kaydet(veri, satirlar)
        logger.info("Transkript kaydedildi: %s", tr_yol)
    except Exception as e:
        logger.warning("Transkript kaydedilemedi: %s", e)

    parcalar = []
    try:
        for i, satir in enumerate(satirlar):
            m = re.match(r"^(ELA|MERT)\s*[:：]\s*(.+)$", satir, re.IGNORECASE | re.DOTALL)
            if not m:
                continue
            konusan = m.group(1).upper()
            icerik = re.sub(r"\s+", " ", m.group(2)).strip()
            cikti = os.path.join(TMP_DIR, f"replik{i:03d}.mp3")
            asyncio.run(_replik_seslendir(icerik, konusan, cikti))
            parcalar.append(cikti)
            logger.info("Replik %d/%d seslendirildi (%s)", i + 1, len(satirlar), konusan)
    except Exception as e:
        logger.exception("Seslendirme hatasi: %s", e)
        return 1

    if not parcalar:
        logger.error("Seslendirilecek replik yok.")
        return 1

    sus = _sessizlik_mp3(ffmpeg)
    if sus:
        parcalar = [p for x in parcalar for p in (x, sus)]
    dosya_ad = f"{tarih}-{bolum}.mp3"
    cikti = os.path.join(RADYO_DIR, dosya_ad)
    if not _birlestir(parcalar, sus or parcalar[-1], cikti, ffmpeg):
        logger.error("MP3 birlestirme basarisiz.")
        return 1

    sure = _sure_sn(cikti)
    logger.info("Yayin hazir: %s (%.1f dk)", cikti, sure / 60)

    # indeks.json guncelle
    indeks_yolu = os.path.join(RADYO_DIR, "indeks.json")
    indeks = {"guncelleme": simdi.strftime("%d.%m %H:%M"), "bolumler": []}
    if os.path.exists(indeks_yolu):
        try:
            indeks = json.load(open(indeks_yolu, encoding="utf-8"))
        except Exception:
            indeks = {"guncelleme": "", "bolumler": []}
    indeks["bolumler"] = [
        b for b in indeks.get("bolumler", [])
        if b.get("dosya") != f"radyo/{dosya_ad}"
    ]
    indeks["bolumler"].insert(0, {
        "id": dosya_ad[:-4], "tarih": tarih, "bolum": bolum,
        "baslik": f"{tarih} — {bolum_adi}", "dosya": f"radyo/{dosya_ad}", "sure_sn": sure,
    })
    indeks["bolumler"] = sorted(indeks["bolumler"], key=lambda b: b.get("id", ""), reverse=True)[:20]
    indeks["guncelleme"] = simdi.strftime("%d.%m %H:%M")  # her kosuda taze saat
    with open(indeks_yolu, "w", encoding="utf-8") as f:
        json.dump(indeks, f, ensure_ascii=False, indent=1)

    # 3 gunden eski yayinlari temizle (repo sismesin; gerektiginde artirilabilir)
    sinir = (simdi - timedelta(days=3)).strftime("%Y-%m-%d")
    for eski in glob.glob(os.path.join(RADYO_DIR, "*.mp3")):
        ad = os.path.basename(eski)[:10]
        if ad < sinir:
            os.remove(eski)
            logger.info("Eski yayin silindi: %s", eski)
    # gecici dosyalari temizle
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"BIST RADYO YAYINI HAZIR: {dosya_ad} | {len(satirlar)} replik | {sure // 60} dk {sure % 60} sn", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
