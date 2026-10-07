"""Adil kiyas yoklamasi (2026-10-07 kullanici itirazi: "sayfadaki ornekler
baya iyi Turkce konusuyordu").

Ilk Qwen3 testimiz klonlama yolunu SENTETIK EMA referansiyla kullanmisti;
kart "gercek sesli referans" oneriyor. Bu betik:
  A) Qwen3-TTS-Tiny-TR'yi GERCEK kalitede ses referansiyla (edge-tts
     AhmetNeural ~10 sn klibi) yeniden dener;
  B) Trendyol-TTS'i (2.29B, VoxCPM runtime) CPU'da tek cumleyle hiz
     olcumune sokar (sayfa ornekleri guzel ama runner'da pratik mi?).
Ciktilar radyo/deneme/ altina yazilir; is akisi repoya isler.
"""
import shutil
import subprocess
import time
from pathlib import Path

CIKTI = Path("radyo/deneme")
CUMLELER = [
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


def _gercek_ref_uret():
    """edge-tts AhmetNeural ile ~10 sn'lik gercek-kalite referans klibi."""
    import asyncio
    from edge_tts import Communicate
    metin = ("Borsa İstanbul gününe yükselişle başladı. Bankacılık endeksi "
             "öne çıktı, dolar TL 49,17 seviyesinde yatay seyrediyor. "
             "Merkez Bankası politika faizini yüzde 37'de tuttu.")
    mp3 = CIKTI / "qwen3-ref-gercek.mp3"
    asyncio.run(Communicate(metin, "tr-TR-AhmetNeural").save(str(mp3)))
    wav = CIKTI / "qwen3-ref-gercek.wav"
    subprocess.run([shutil.which("ffmpeg"), "-y", "-v", "error",
                    "-i", str(mp3), "-ar", "24000", "-ac", "1", str(wav)],
                   check=True)
    print(f"gercek referans: {wav} ({_ses_suresi(str(wav)) and f'{_ses_suresi(str(wav)):.1f} sn'})")
    return str(wav)


def qwen3_test():
    import torch
    from qwen_tts import Qwen3TTSModel
    import soundfile as sf

    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained("erkamk/qwen3-tts-tiny-tr",
                                          device_map="cpu",
                                          dtype=torch.float32)
    print(f"qwen3 yukleme: {time.time() - t0:.1f} sn")
    ref = _gercek_ref_uret()
    prompt = model.create_voice_clone_prompt(ref_audio=ref,
                                             x_vector_only_mode=True)
    for i, cumle in enumerate(CUMLELER, 1):
        t1 = time.time()
        wavs, oran = model.generate_voice_clone(
            text=cumle, language="Auto",
            voice_clone_prompt=prompt, max_new_tokens=1000)
        sentez = time.time() - t1
        yol = CIKTI / f"qwen3-gercek-ref-{i}.wav"
        sf.write(str(yol), wavs[0], oran)
        ses = _ses_suresi(str(yol))
        print(f"qwen3 cumle {i}: sentez {sentez:.1f} sn | ses {ses:.1f} sn | "
              f"RTF {ses / sentez:.2f}x" if ses else f"qwen3 cumle {i}: {sentez:.1f} sn")


def trendyol_test():
    from voxcpm.core import VoxCPM

    t0 = time.time()
    model = VoxCPM.from_pretrained(hf_model_id="Trendyol/Trendyol-TTS")
    print(f"trendyol yukleme: {time.time() - t0:.1f} sn")
    cumle = CUMLELER[0]
    t1 = time.time()
    cikti = model.generate(text=cumle, cfg_value=2.0, inference_timesteps=16)
    sentez = time.time() - t1
    print(f"trendyol cikti tipi: {type(cikti).__name__}")
    import soundfile as sf
    import numpy as np
    wavs = oran = None
    if isinstance(cikti, tuple) and len(cikti) == 2:
        wavs, oran = cikti
    elif isinstance(cikti, dict):
        wavs = cikti.get("wavs") or cikti.get("wav")
        oran = cikti.get("rate") or cikti.get("sampling_rate") or 16000
    else:
        dizi = np.asarray(cikti)
        if dizi.dtype in (np.float32, np.float64) and dizi.ndim == 1:
            wavs, oran = [dizi], 16000
    if wavs is None:
        print(f"trendyol cikti cozulemedi: {repr(cikti)[:200]}")
        return
    yol = CIKTI / "trendyol-deneme-1.wav"
    sf.write(str(yol), wavs[0], oran)
    ses = _ses_suresi(str(yol))
    print(f"trendyol cumle: sentez {sentez:.1f} sn | {len(cumle)} karakter | "
          f"ses {ses and f'{ses:.1f} sn'} | "
          f"RTF {(ses / sentez) if ses else '?'} (CPU)")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(yol),
                        "-b:a", "96k", str(yol).replace(".wav", ".mp3")],
                       check=True)
        yol.unlink()


if __name__ == "__main__":
    print("=== A) Qwen3-TTS-Tiny-TR (gercek ses referansiyla) ===")
    qwen3_test()
    print("=== B) Trendyol-TTS (CPU hiz olcumu) ===")
    trendyol_test()
    print("KIYAS TAMAM")
