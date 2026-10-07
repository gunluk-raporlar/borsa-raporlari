"""Qwen3-TTS-Tiny-TR — DEMO SPACE'IN RESMI TARIFIYLE uretim (2026-10-07).

Kaynak: spaces/erkamk/qwen3-tts-tiny-tr-demo/raw/main/app.py.
Ilk iki testimizde eksik olanlar (kalicilik/kalite farkinin olasi nedenleri):
  1. ornekleme ayarlari: temperature=0.7, top_k=50, subtalker_*,
     subtalker_dosample=True, repetition_penalty=1.0
  2. normalize: egitim metni buyuk I/İ icermiyordu -> İ->i, I->ı
  3. non_streaming_mode=True + max_new_tokens = 25 + 2*len(metin)
  4. cumle basina seed ile uretim, parcalar 0.2 sn sessizlikle birlesir
  5. Space'in 4 hazir sesi (voices/voice-01..04.wav) — coklu ses cevabi!
Ciktilar: radyo/deneme/qwen3-resmi-ses<k>-<cumle>.mp3
"""
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

CIKTI = Path("radyo/deneme")
MODEL_ADI = "erkamk/qwen3-tts-tiny-tr"
SESLER = (1, 2)  # Space'in voice-01, voice-02'si
AYARLAR = dict(temperature=0.7, top_k=50, subtalker_temperature=0.6,
               subtalker_top_k=50, subtalker_dosample=True,
               repetition_penalty=1.0)
# Sayilar yaziyla: kart "sayilari kelime yazin" diyor; radyo hattinin
# _tl_konusma_metni normalizasyonu uretimde bunu zaten yapiyor.
CUMLELER = [
    "Borsa İstanbul gününe yükselişle başladı. Bankacılık endeksi öne çıktı, "
    "dolar lirası kırk dokuz virgül on yedi seviyesinde yatay seyrediyor.",
    "Merkez Bankası politika faizini yüzde otuz yedide tuttu; hisse beş günde "
    "yüzde on iki virgül üç yükseldi.",
]


def _norm(metin):
    """Egitim metni buyuk I/İ icermiyordu (Space app.py aciklamasi)."""
    return metin.replace("İ", "i").replace("I", "ı")


def _ses_suresi(yol):
    ffprobe = shutil.which("ffprobe")
    try:
        return float(subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", yol],
            capture_output=True, text=True, timeout=30).stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return None


def _ses_indir(no):
    yol = CIKTI / f"qwen3-ses-{no}.wav"
    url = ("https://huggingface.co/spaces/erkamk/qwen3-tts-tiny-tr-demo/"
           f"resolve/main/voices/voice-0{no}.wav")
    urllib.request.urlretrieve(url, yol)
    return str(yol)


def main():
    import numpy as np
    import torch
    from qwen_tts import Qwen3TTSModel
    import soundfile as sf

    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(MODEL_ADI, device_map="cpu",
                                          dtype=torch.float32)
    torch.set_num_threads(os.cpu_count() or 2)
    print(f"model yukleme: {time.time() - t0:.1f} sn")

    CIKTI.mkdir(parents=True, exist_ok=True)
    for ses_no in SESLER:
        ref = _ses_indir(ses_no)
        prompt = model.create_voice_clone_prompt(ref_audio=ref,
                                                 x_vector_only_mode=True)
        for idx, cumle in enumerate(CUMLELER, 1):
            parca = _norm(cumle)
            t1 = time.time()
            torch.manual_seed(7 + idx)
            wavs, oran = model.generate_voice_clone(
                text=parca, language="Auto", voice_clone_prompt=prompt,
                non_streaming_mode=True,
                max_new_tokens=25 + 2 * len(parca), **AYARLAR)
            sentez = time.time() - t1
            parcalar = [np.asarray(wavs[0], dtype=np.float32),
                        np.zeros(int(0.2 * oran), dtype=np.float32)]
            wav_yolu = CIKTI / f"qwen3-resmi-ses{ses_no}-{idx}.wav"
            sf.write(str(wav_yolu), np.concatenate(parcalar[:-1]), oran)
            ses = _ses_suresi(str(wav_yolu))
            print(f"ses{ses_no}-cumle{idx}: sentez {sentez:.1f} sn | "
                  f"ses {ses and f'{ses:.1f} sn'} | "
                  f"{len(parca)} karakter (resmi tarife)")
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(wav_yolu),
                                "-b:a", "96k",
                                str(wav_yolu).replace(".wav", ".mp3")], check=True)
                wav_yolu.unlink()


if __name__ == "__main__":
    main()
