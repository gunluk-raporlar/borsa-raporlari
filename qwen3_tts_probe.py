"""Qwen3-TTS-Tiny-TR yoklamasi — Actions runner uyumluluk testi.

Kaynak: https://huggingface.co/erkamk/qwen3-tts-tiny-tr (Apache 2.0,
~370M paketli parametre, Turkce TTS + SES KLONLAMA; radyo hattinin
iki-sunucu ihtiyacina EMA Lightning'in tek-ses kisitina alternatif).

GPU'suz runner'da olculenler: model yukleme, cumle basina sentez hizi,
ses suresi (ffprobe) ile gercek zaman carpani. Referans ses olarak EMA
Lightning yoklamasinin ciktisi kullanilir (radyo/deneme/ema-deneme-1.mp3;
yok -> ffmpeg ile yeniden uretilemezse yoklama referanssiz atlanir).
Ornek sesler radyo/deneme/qwen3-*.mp3 olarak yazilir; is akisi repoya
isler ve artifact yukler. Karsilastirma tabani: ema_probe.py.
"""
import shutil
import subprocess
import time
from pathlib import Path

MODEL_ADI = "erkamk/qwen3-tts-tiny-tr"
REF_KAYNAK = Path("radyo/deneme/ema-deneme-1.mp3")
REF_WAV = Path("radyo/deneme/qwen3-ref.wav")
CIKTI = Path("radyo/deneme")
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


def _mp3_den_wav():
    """EMA ornek mp3'unu referans wav'a cevirir (klonlama girdisi)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    if not REF_KAYNAK.exists():
        print(f"referans bulunamadi: {REF_KAYNAK} — klonlama testi atlanir")
        return False
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(REF_KAYNAK),
                    "-ar", "24000", "-ac", "1", str(REF_WAV)], check=True)
    return True


def main():
    import torch
    from qwen_tts import Qwen3TTSModel

    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(MODEL_ADI, device_map="cpu",
                                          dtype=torch.float32)
    print(f"model yukleme: {time.time() - t0:.1f} sn")

    CIKTI.mkdir(parents=True, exist_ok=True)
    ses_var = _mp3_den_wav()
    prompt = None
    if ses_var:
        t1 = time.time()
        prompt = model.create_voice_clone_prompt(ref_audio=str(REF_WAV),
                                                 x_vector_only_mode=True)
        print(f"ses klonlama promptu: {time.time() - t1:.1f} sn "
              f"(referans: EMA ornegi)")

    toplam_karakter = toplam_sentez = toplam_ses = 0.0
    for i, cumle in enumerate(CUMLELER, 1):
        yol = str(CIKTI / f"qwen3-deneme-{i}.wav")
        t1 = time.time()
        # Ilk kosuda (2026-10-07) kisa sesler dustu (108 karakter -> 0,6 sn):
        # karttaki "son kelimeler dusuyor" sorunu. Dil listesinde Turkce YOK
        # (yalnizca 'auto' — ValueError kanitlandi); max_new_tokens yukseltme
        # denenir, desteklenmiyorsa parametresiz devam edilir.
        wavs, oran = None, None
        for ekstra in ({"max_new_tokens": 1000}, {}):
            try:
                wavs, oran = model.generate_voice_clone(
                    text=cumle, language="Auto",
                    voice_clone_prompt=prompt, **ekstra)
                print(f"cumle {i}: ekstra={bool(ekstra)}")
                break
            except (TypeError, ValueError):
                continue
        if wavs is None:
            raise SystemExit("generate_voice_clone hicbir ayarla calismadi.")
        sentez = time.time() - t1
        with open(yol, "wb") as f:
            import soundfile as sf
            sf.write(f, wavs[0], oran)
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
            wav = CIKTI / f"qwen3-deneme-{i}.wav"
            mp3 = CIKTI / f"qwen3-deneme-{i}.mp3"
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(wav),
                            "-b:a", "96k", str(mp3)], check=True)
            wav.unlink()
        print("mp3 donusumu tamam (96 kbps).")


if __name__ == "__main__":
    main()
