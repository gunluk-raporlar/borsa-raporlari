"""EMA Lightning (Turkce TTS) yoklamasi — Actions runner uyumluluk testi.

Kaynak: https://huggingface.co/canberkkkkkk/ema-lightning (Apache 2.0,
8.6M parametre, yalnizca Turkce, CPU ~6x gercek zamanli - model karti).

GPU'suz ubuntu runner'da olculenler: model yukleme suresi, cumle basina
sentez hizi (karakter/sn) ve ses suresi (ffprobe ile gercek zaman carpani).
Ornek sesler radyo/deneme/ altina yazilir; is akisi repoya isler (kullanici
dinleyebilir) ve artifact olarak da yukler. Radyo hattinin (radyo.py)
edge-tts yedegi adayidir; bu betik yalnizca olcum/deneme yapar.
"""
import shutil
import subprocess
import time
from pathlib import Path

CIKTI = Path("radyo/deneme")
# Radyo cumlelerine benzeyen 3 ornek: sayili, yuzdeli, para birimli
# (radyo.py zaten 224,10 TL -> "yuzde/yirmi dort virgul on" normalizasyonu
# yaptigi icin burada da dogal okunus test ediliyor).
CUMLELER = [
    "Merhaba, Borsa İstanbul gününe yükselişle başladı.",
    "BIST 30 endeksi günü yüzde 1,2 kazançla 12.374 puandan kapattı; dolar TL 49,17 seviyesinde yatay seyretti.",
    "Merkez Bankası politika faizini yüzde 37'de tuttu; 224,10 TL fiyatlanan hisse beş günde yüzde 12,3 yükseldi.",
]


def _ses_suresi(yol):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        cikti = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", yol],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return float(cikti)
    except (ValueError, subprocess.SubprocessError):
        return None


def main():
    from ema_lightning import EMA

    t0 = time.time()
    tts = EMA()
    yukleme = time.time() - t0
    print(f"model yukleme: {yukleme:.1f} sn")

    CIKTI.mkdir(parents=True, exist_ok=True)
    toplam_karakter = toplam_sentez = toplam_ses = 0.0
    for i, cumle in enumerate(CUMLELER, 1):
        yol = str(CIKTI / f"ema-deneme-{i}.wav")
        t1 = time.time()
        tts.say(cumle, path=yol)
        sentez = time.time() - t1
        ses = _ses_suresi(yol)
        carpan = (ses / sentez) if ses else None
        print(f"cumle {i}: sentez {sentez:.1f} sn | {len(cumle)} karakter | "
              f"ses {ses and f'{ses:.1f} sn'} | "
              f"gercek-zaman carpani {carpan and f'{carpan:.1f}x'}")
        toplam_karakter += len(cumle)
        toplam_sentez += sentez
        toplam_ses += ses or 0.0

    print(f"TOPLAM: {toplam_karakter} karakter, sentez {toplam_sentez:.1f} sn, "
          f"ses {toplam_ses:.1f} sn -> "
          f"{toplam_karakter / max(toplam_sentez, 0.01):.0f} karakter/sn, "
          f"gercek-zaman carpani {toplam_ses / max(toplam_sentez, 0.01):.1f}x")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        for i in range(1, len(CUMLELER) + 1):
            wav = CIKTI / f"ema-deneme-{i}.wav"
            mp3 = CIKTI / f"ema-deneme-{i}.mp3"
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(wav),
                            "-b:a", "96k", str(mp3)], check=True)
            wav.unlink()
        print("mp3 donusumu tamam (96 kbps).")
    else:
        print("ffmpeg yok; wav dosyalari birakildi.")


if __name__ == "__main__":
    main()
