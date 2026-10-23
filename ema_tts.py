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

logger = logging.getLogger("ema-tts")

_model = None


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
