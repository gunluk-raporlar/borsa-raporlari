# -*- coding: utf-8 -*-
"""EMA Lightning (HF canberkkkkkk/ema-lightning, Apache 2.0, 8.6M parametre)
tek sesli yerel offline Turkce TTS — TEK KAYNAK.

Kullanicilar (23.10 kararlari): radyo.py Ela sunucusu ve bot.py rapor sesli
bulteni. Kurulum yalnizca ses ureten is akislarinda yapilir (radyo.yml,
daily.yml):
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    pip install ema-lightning==1.0.1
Runner'da ~10x gercek zamanli (yoklama: ema_probe.py). Hata durumunda
exception yukseltir; yedek zincirine karar vermek cagiranin isidir.
"""
import logging
import os
import re

logger = logging.getLogger("ema-tts")

_model = None

# EMA'nin metin on yuzu normalizer-tr "fallback" politikasiyla calisir:
# cozemedigi simgeleri okunus adiyla SOYLER ("kapat parantez", "eğik çizgi",
# "kesme", "nokta işareti") ve tumu BUYUK HARF sozcukleri kisaltma sanip harf
# harf okur ("GÜÇLÜ SAT" -> "ge ü çe le ü se a te"). Asagidaki katman metni
# EMA'ya verilmeden once duzeltir; rapor sesli bulteni ve radyo replikleri
# dahil tum EMA cagrilari icin tek kaynaktan gecerli.
_SINYAL_KUCULT = [
    (re.compile(r"\bGÜÇLÜ AL\b"), "güçlü al"),
    (re.compile(r"\bGÜÇLÜ SAT\b"), "güçlü sat"),
    (re.compile(r"\bDİPTE AL\b"), "dipte al"),
    (re.compile(r"\bTEPEDE SAT\b"), "tepede sat"),
    (re.compile(r"\bNÖTR\b"), "nötr"),
    (re.compile(r"\bAL\b"), "al"),
    (re.compile(r"\bSAT\b"), "sat"),
]
# rakamdan sonra gelen ek kesmesi kaldirilamaz ("20sinde" harf harf okunur);
# boslukla ayrilir ("20 sinde" -> "yirmi sinde"). Harfler arasi kesme
# birlestirilir ("Endeksi'daki" -> "Endeksidaki").
_EK_KESMESI = re.compile(r"(?<=[0-9])'(?=[\wçğıöşüÇĞİÖŞÜ])")
_KESME = re.compile(r"(?<=\w)'(?=\w)")
_DOLAR = re.compile(r"\$\s*([\d.,]+)")
_TEMIZ = [
    (_DOLAR, r"\1 dolar"),
    (re.compile(r"\$"), " dolar "),
    (re.compile(r"€"), " euro "),
    (re.compile(r"&middot;|&bull;|·|•|▪"), ", "),
    (re.compile(r"/"), " "),
    (_EK_KESMESI, " "),
    (_KESME, ""),
    (re.compile(r"[\"“”«»‹›']"), " "),
    (re.compile(r"[()\[\]{}]"), " "),
    (re.compile(r"[<>|*_=~^&]"), " "),
]


def metin_uyarla(metin):
    """EMA on yuzune girmeden once metni EMA'ya uygun hale getirir: sembol
    ve parantezleri temizler, sinyal sozcuklerini kucultur (RSI, ABD gibi
    gercek kisaltmalar bilerek dokunulmaz; harf harf okunmalari dogaldir)."""
    for desen, yerine in _TEMIZ:
        metin = desen.sub(yerine, metin)
    for desen, kucuk in _SINYAL_KUCULT:
        metin = desen.sub(kucuk, metin)
    return re.sub(r"\s+", " ", metin).strip()


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


def seslendir(metin, cikti_yolu, hiz=1.0):
    """metin -> cikti_yolu (mp3, 48k/24kHz). Hiz 1.0 = normal, <1 yavas."""
    metin = metin_uyarla(metin)
    global _model
    try:
        from ema_lightning import EMA
    except ImportError as e:
        raise RuntimeError(
            "EMA kurulu degil (pip install ema-lightning==1.0.1)") from e
    if _model is None:
        logger.info("[EMA] EMA Lightning yukleniyor...")
        _model = EMA()
    wav_yolu = os.path.splitext(cikti_yolu)[0] + ".wav"
    try:
        _model.say(metin, path=wav_yolu, speed=hiz)
    except TypeError:
        # paket surumu speed parametresini desteklemiyorsa hizsiz devam
        _model.say(metin, path=wav_yolu)
    ffmpeg = _ffmpeg_bul()
    if not ffmpeg:
        raise RuntimeError("EMA icin ffmpeg bulunamadi")
    r = os.system(f'"{ffmpeg}" -y -loglevel error -i "{wav_yolu}" '
                  f'-c:a libmp3lame -b:a 48k -ar 24000 "{cikti_yolu}"')
    if r != 0:
        raise RuntimeError(f"EMA wav->mp3 donusumu basarisiz ({r})")
    os.remove(wav_yolu)
    return cikti_yolu
