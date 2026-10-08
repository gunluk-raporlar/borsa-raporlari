# Ses (TTS) Modelleri — Deneme Notları, Kritik Ayarlar ve Kullanım Planı

*Tarih: 2026-10-07 / 2026-10-08 · Ortak deneme ortamı: GitHub Actions `ubuntu-latest` runner (GPU'suz, 4 çekirdek CPU) · Konu: borsa-raporlari sitesinin radyo/podcast hattı için Türkçe ses motoru seçimi.*

---

## 0. Tek cümlelik özet

| Model | Karar | Rol |
|---|---|---|
| **edge-tts** (Microsoft, ücretsiz uç) | ✅ Ana motor (mevcut) | Ela (EmelNeural) + MERT (AhmetNeural) diyalogu |
| **EMA Lightning** | ✅ Kesinti yedeği (bağlandı, test edildi) | edge-tts düşerse tek sesle yayını kurtarır |
| **Qwen3-TTS-Tiny-TR** | ⭐ Aday — resmi tarifeyle kalite kanıtlandı, kullanıcı beğendi | Faz 1: günde 1 diyalog podcast motoru (tamamen offline) |
| **Trendyol-TTS** | ⏸ Bekletiliyor | CPU'da RTF 0,31× ölçüldü; örnek dinleme kararı verilmedi |
| Perde kaydırma ile "MERT" üretimi | ❌ Elendi | Kullanıcı dinlemesi: "çok kötü" |

---

## 1. Qwen3-TTS-Tiny-TR — en kritik bölüm

**Depo:** https://huggingface.co/erkamk/qwen3-tts-tiny-tr · **Lisans:** Apache 2.0
**Boyut:** ~199M parametre (paketli ~370M; talker + konuşma tokenleştirici) · 12 Hz / 24 kHz
**Taban:** Qwen3-TTS-12Hz-0.6B-Base'in talker-ince-ayarlı küçültülmüş hali (Alania TR sentetik ~1.863 saat, SFT+GRPO)
**Paket:** `pip install qwen-tts==0.1.1` (transformers==4.57.3, accelerate, librosa, torchaudio, soundfile, sox, onnxruntime, einops çeker)
**Demo Space (RESMİ TARİFİN KAYNAĞI):** `spaces/erkamk/qwen3-tts-tiny-tr-demo/raw/main/app.py`

### 1.1 ZORUNLU kritik ayarlar (bunlar olmadan model BOZUK çalışır)

Biz ilk denemede bu ayarlar olmadan çalıştırdık ve üç belirgin arıza aldık:
rastgele **kısalma** (108 karakter → 0,6-3,7 sn ses), **yabancı vurgu**
(kullanıcı: "Türkçe değil"), bozuk/kırık tını. Resmi tarife (`app.py`)
geçince hepsi düzeldi ve kullanıcı onayladı ("sesler gerçekçi olmuş").

```python
AYARLAR = dict(temperature=0.7, top_k=50,
               subtalker_temperature=0.6, subtalker_top_k=50,
               subtalker_dosample=True, repetition_penalty=1.0)

def _norm(metin):
    # EĞİTİM METNİ BÜYÜK I/İ HİÇ GÖRMEMİŞ (app.py açıklaması aynen).
    # "İstanbul" gibi kelimeler aksi halde yabancı vurguyla okunur.
    return metin.replace("İ", "i").replace("I", "ı")

# her CÜMLE ayrı üretilir (model ≤12 sn kliplerle eğitilmiş), cümle başına seed:
for idx, cumle in enumerate(cumleler):
    torch.manual_seed(7 + idx)
    wavs, oran = model.generate_voice_clone(
        text=_norm(cumle), language="Auto",
        voice_clone_prompt=prompt,
        non_streaming_mode=True,
        max_new_tokens=25 + 2 * len(_norm(cumle)),   # Space'in formülü
        **AYARLAR)
    parcalar.append(np.asarray(wavs[0], dtype=np.float32))
    parcalar.append(np.zeros(int(0.2 * oran), dtype=np.float32))  # 0.2 sn sessizlik
ses = np.concatenate(parcalar)
```

### 1.2 Bilinen tuzaklar (hepsi bizim başımıza geldi)

| Tuzak | Belirti | Çözüm |
|---|---|---|
| Örnekleme ayarları verilmemesi | rastgele kısalma/bozukluk | `AYARLAR` sözlüğü ZORUNLU |
| Büyük İ/I göndermek | yabancı vurgu ("Türkçe değil") | `_norm()` İ→i, I→ı |
| 2,8 sn'lik kısa referans | sesler yarı yolda kesilir | referans **5-10 sn temiz konuşma** (9 sn ile test edildi ✓) |
| `language="Turkish"` yazmak | `ValueError` | dil listesinde TR yok; yalnızca `"Auto"` var |
| PyPI torchaudio | `libcudart.so.13` hatası | torchaudio'yu CPU dizininden kur: `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu` |
| Sayıları rakam yazmak | hatalı okuma | kart "sayıları kelime yazın" diyor; radyo hattındaki `_tl_konusma_metni` normalizasyonu bunu zaten yapıyor |

### 1.3 Ses klonlama ve hazır sesler

- Referans: `model.create_voice_clone_prompt(ref_audio=..., x_vector_only_mode=True)`
- **Demo Space'te 4 hazır ses var**: `voices/voice-01.wav` … `voice-04.wav`
  (indirme: `https://huggingface.co/spaces/erkamk/qwen3-tts-tiny-tr-demo/resolve/main/voices/voice-01.wav`)
- Plan: **Ela = voice-01, MERT = voice-02** (iki farklı hazır ses; klonlama ikinci seçenek)

### 1.4 Ölçümler (GPU'suz runner, resmi tarifeyle)

- Model yükleme: ~11-16 sn
- RTF ≈ **0,4×** (140 karakter → 9,6 sn ses için 25,8 sn sentez)
- **10 dk'lık program ≈ 25-30 dk sentez** → iş akışı timeout'u en az 60-90 dk olmalı
- Karşılaştırma: kısalma sorunu olan eski denemelerimizde 1,5-3,7 sn'lik sesler düşüyordu; resmi tarifeyle 4/4 üretim tam boy geldi

### 1.5 Örnek sesler (sitede canlı)

- `radyo/deneme/qwen3-resmi-ses1-1.mp3` … `qwen3-resmi-ses2-2.mp3` (kullanıcı onaylı)
- Kıyas: eski bozuk denemeler `qwen3-deneme-*.mp3`, hızlandırılmışlar `*-hizli-130/160.mp3`

---

## 2. EMA Lightning

**Depo:** https://huggingface.co/canberkkkkkk/ema-lightning · Apache 2.0 · **8,6M parametre** (ema.pt 22,3 MB + decoder.pt 12,1 MB — depoda TEK checkpoint, ek ses yok)
**Paket:** `pip install ema-lightning==1.0.1` (torch, normalizer-tr bağımlı) · Python ≥3.11

```python
from ema_lightning import EMA
tts = EMA()                       # ~3 sn yükleme; ilk sentez ısınma ~14 sn
tts.say(metin, path="cikti.wav")  # speed= parametresi denendi; TypeError'a düşerse speedsiz
```

- **Hız (runner CPU):** ısınma sonrası **9,7-13,4× gerçek zamanlı** → 10 dk program ≈ 60-70 sn
- **Kalite:** WER %0,92 (kart), kullanıcı: "Türkçesi piyasadaki tüm modellerden kaliteli"
- **Kısıt:** tek ses (depoda tek checkpoint doğrulandı — üreticinin videolarındaki erkek sesi yayında yok); perde kaydırma ile erkekleştirme denendi, **kullanıcı elendi**

### Mevcut entegrasyon (radyo yedeği) — CANLI

- `radyo.py → _ema_seslendir()`: edge-tts herhangi bir replikte başarısız olursa aynı replik EMA ile üretilir; model tek instance önbellekte (`_ema_model`)
- Karakter farkı yedekte yalnızca hızla: ELA `speed=1.0`, MERT `speed=0.94` (TypeError'da hızsız)
- `radyo.yml`'ye kurulum adımı: `pip install torch --index-url .../whl/cpu` + `pip install ema-lightning==1.0.1`
- **Kanıt testi:** edge-tts'i zorla patlatan entegrasyon testi yeşil (koşu 37659097949); örnek: `radyo/deneme/ema-yedek-test.mp3`
- Yoklama aracı: `ema_probe.py` + `ema-probe.yml`

---

## 3. Trendyol-TTS (bekletiliyor)

**Depo:** https://huggingface.co/Trendyol/Trendyol-TTS · MIT (ama üst model VoxCPM2 koşulları sorumlulukla) · **2,29B parametre / 4,58 GB** · kart: "araştırma/prototip, üretim servisi değil"

```python
from voxcpm.core import VoxCPM          # pip install voxcpm==2.0.3 (28 bağımlılık)
model = VoxCPM.from_pretrained(hf_model_id="Trendyol/Trendyol-TTS")
cikti = model.generate(text=..., cfg_value=2.0, inference_timesteps=16)  # ndarray döner
```

- **Ölçüm (runner CPU):** yükleme 71,5 sn; 106 karakter → 90 sn sentez / 28,3 sn ses → **RTF 0,31×** (10 dk program ≈ 32 dk)
- Konuşma temposu yavaş; konuşmacı sayısı kartta belirtilmemiş
- Örnek: `radyo/deneme/trendyol-deneme-1.mp3` (dinleme kararı kullanıcıda verilmedi)
- **Bekletme nedeni:** Qwen3 resmi tarife kalitesi beğenilince öncelik kaybetti; Trendyol ciddi CPU maliyetiyle ikinci sırada

---

## 4. edge-tts (üretimdeki mevcut motor)

- `radyo.py → _replik_seslendir()`: ELA=`tr-TR-EmelNeural`, MERT=`tr-TR-AhmetNeural`
- Karakterlendirme: rate/pitch/volume (MERT: +8%/+6Hz; ELA: +3%/-5Hz)
- Sayı/para okunuşu: `_tl_konusma_metni` + `bot._konusma_metni_normalize` (radyo repliklerinde rakamlar kelimeye çevrilir — Qwen3'e geçişte de aynen kullanılacak)
- Risk: gayriresmî Microsoft uç noktası → kesintiye açık; bu yüzden EMA yedeği bağlandı

---

## 5. Reddedilen yaklaşımlar (bir daha denemeyin)

1. **Qwen3 + sentetik (EMA) referans** → bozuk + yabancı vurgu (kullanıcı: "bozuk olmuş aşırı ağır")
2. **Qwen3 atempo 1,3×/1,6× hızlandırma** → tempo düzelir ama vurgu/bozukluk düzelmez; asıl sebep İ→i normalizasyonunun eksikliğiymiş
3. **EMA'yı perde kaydırıp MERT yapmak** (`asetrate*0.82 + atempo`) → kullanıcı: "çok kötü olmuş"
4. **Qwen3'e `language="Turkish"`** → desteklenmiyor, yalnızca `"Auto"`
5. **Trendyol'u günlük otomasyona koymak** → 0,31× RTF mümkün ama kart "üretim servisi değil" diyor; Qwen3 tarife önündeyken gereksiz

---

## 6. Ortam gerçekleri

- **GitHub Actions runner (GPU'suz):** EMA 10-13× RTF, Qwen3 0,4×, Trendyol 0,31× — EMA hariç uzun ses üretimi runner'da "toplu iş" süresi ister
- **Kullanıcının PC'si:** RTX 5070 Ti 16 GB + 64 GB RAM + Ultra 7 270K → lip-sync avatar (Wav2Lip/SadTalker/Hallo sınıfı) ve slayt-video ffmpeg üretimi LOKALDE rahat; PC sürekli açık kalmak zorunda DEĞİL (otomasyon runner'da)
- **Depolama:** radyo arşivi ~15 MB (48 kbps); CF Pages'te yıllarca yer var; ileride R2 (10 GB ücretsiz) opsiyonu

---

## 7. Faz planı (onay bekliyor)

1. **Faz 1 — Günlük Qwen3 podcast'i:** radyo hattına `RADYO_MOTOR=qwen3|edge` seçeneği; Ela=voice-01, MERT=voice-02; İ→i + `_tl_konusma_metni` + cümle-başına üretim + resmi AYARLAR; `radyo.yml` timeout 25→90; günde 1 bölüm (kapanış sonrası); transkript `dogrulama.py` denetiminden geçirilir; kullanım defterine kayıt
2. **Faz 2a — Slayt/grafik videosu:** aynı ses + jenerik + bölüm slaytları + transkript altyazı → 720p H.264 (runner'da otomatik)
3. **Faz 2b — Avatar stüdyo:** ayrı planlar (Ela/MERT lip-sync klipleri) + programatik montaj (JSON kurgu planı, J-cut, geniş plan araları); kullanıcı PC'sinde toplu üretim; önce tek sunucu pilotu

---

## 8. Dosya/iş akışı haritası

| Dosya | İçerik |
|---|---|
| `radyo.py` | radyo üretimi; `_replik_seslendir` (edge→EMA fallback), `_ema_seslendir` |
| `radyo.yml` | radyo iş akışı; EMA kurulum adımı (timeout 25 dk — Faz 1'de 90'a çekilecek) |
| `ema_probe.py` / `ema-probe.yml` | EMA hız/kalite yoklaması + **radyo fallback entegrasyon testi** |
| `qwen3_tts_probe.py` / `tts_kiyas_probe.py` / `tts_resmi_probe.py` | Qwen3 denemeleri (eski → adil kıyas → **resmi tarife**) |
| `.github/workflows/tts-resmi-probe.yml` | resmi tarife koşusu (ses1/ses2 × 2 cümle üretir, repoya işler) |
| `radyo/deneme/` | tüm örnek sesler (sitede: `borsa-raporlari.pages.dev/radyo/deneme/...`) |
| `ops_sync.py` + `borsa-raporlari-ops/kullanim-defteri/` | kullanım defteri (model/token/hata sayısı) private kopya |

## 9. Yeniden üretim komutları (hızlıca)

```bash
# EMA yoklama + radyo fallback testi
gh workflow run ema-probe.yml
# Qwen3 resmi tarife (4 örnek üretir, repoya işler)
gh workflow run tts-resmi-probe.yml
# Trendyol CPU ölçümü (qwen testiyle birlikte)
gh workflow run tts-kiyas-probe.yml
```

---

*Not: Bu dosya `docs/ses-modelleri-notlari.md` olarak ana depoda durur; ayrı sohbetlerde bağlam dosyası olarak kullanılır. Güncellemeler tarihlenecek.*
