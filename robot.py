"""BIST 30 Kağıt-Üstünde İşlem Robotu (paper trading) — bot.py'den TAMAMEN BAĞIMSIZ.

Gerçek para kullanmaz; sinyalleri simüle emirlerle işler, sonucu robot.html'de yayınlar.
Kaldırmak için: robot.py, robot.html, data/robot/, .github/workflows/robot.yml silinir.
Başka hiçbir dosyaya bağımlılığı yoktur.

Calisma duzeni:
  - Piyasa saatlerinde (h.i. 10:00-18:30 TR) her 30 dakikada bir kron kosar.
  - Gostergeler gunun ILK kosusunda cekilen gunluk kapanis serisinden (yerel onbellek)
    hesaplanir; piyasa acikken bugunun barı CANLI fiyattan (TradingView, ~15 dk
    gecikmeli) gecici olarak eklenir ve sinyaller oyle degerlendirilir.
  - Stop-loss / hedef gun icinde canlı fiyattan tetiklenir; AL/GUC'LU AL sinyali de
    gun icinde kesisme olusursa ayni anda isleme donusebilir.
  - Ozkaynak egrisi her kontrolda bir nokta kazanir (gun icinde hareket eder).
  - Ayni fiyatlar tekrar gelirse (kapanis sonrasi tekrarlar, tatil gunleri) islem
    ve nokta eklenmez; ayni hisse ayni gun satusa geri alinmaz.

Katmanlar: VERI -> STRATEJI -> RISK -> OMS -> MUHASEBE -> DASHBOARD (asagida ayri).
"""
import json
import logging
import math
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone as dt_timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Istanbul")
except Exception:  # Windows'ta tzdata yoksa: Türkiye yıl boyu UTC+3'tür
    TZ = dt_timezone(timedelta(hours=3))

import pandas as pd

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("robot")

# ---- SABITLER ----
HISSELER = [
    "AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "DSTKF", "EKGYO", "ENKAI",
    "EREGL", "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD", "MGROS",
    "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO",
    "TOASO", "TRALT", "TTKOM", "TUPRS", "VAKBN", "YKBNK",
]
DURUM_YOL = "data/robot/durum.json"
ISLEM_YOL = "data/robot/islemler.jsonl"
GECMIS_DIR = "data/robot/gecmis"
SAYFA_YOL = "robot.html"

KAPITAL0 = 100_000.0        # sanal başlangıç sermayesi (TL)
POZISYON_ORAN = 0.12        # özkaynağın en fazla %12'si tek hisseye
MAKS_POZISYON = 8           # aynı anda en fazla 8 farklı hisse
STOP_ORAN = -0.07           # maliyetin %7 altında stop-loss
HEDEF_ORAN = 0.15           # maliyetin %15 üstünde kâr alma
GUNLUK_ZARAR_LIMITI = 0.03  # özkaynak dünkü kapanışa göre %3 düşerse yeni alım durur
KOMISYON = 0.0006           # işlem başına %0,06 (BSMV dahil varsayım)
KAYMA = 0.0005              # %0,05 slippage varsayımı
MIN_ISLEM_TL = 2_000.0      # bundan küçük işlem açılmaz
VERI_GUN = 300              # SMA100 + tampon için ~200 işlem günü
OZKAYIT_GUN = 14            # eğride son 14 gün saklanır (ilk nokta hep kalır)

SITE_URL = "https://borsa-raporlari.pages.dev/"


def _simdi():
    return datetime.now(TZ)


def piyasa_acik_mi(simdi=None):
    """BIST sürekli işlem oturumu kabaca 10:00-18:00 TR; tampon için 09:45-18:35."""
    s = simdi or _simdi()
    if s.weekday() >= 5:
        return False
    dk = s.hour * 60 + s.minute
    return 9 * 60 + 45 <= dk <= 18 * 60 + 35


# ---------- 1. VERI KATMANI ----------
def _serileri_duzelt(ham):
    """{hisse: Series} -> {hisse: (tarihler, fiyatlar)}"""
    out = {}
    for h, s in ham.items():
        if s is None or len(s) == 0:
            continue
        s = pd.Series(s).dropna().astype(float)
        s = s[s > 0]
        if s.empty:
            continue
        out[h] = ([str(t)[:10] for t in s.index], [float(v) for v in s.values])
    return out


def veri_cek():
    """Gunluk kapanis serilerini kaynaktan ceker: {hisse: (tarihler, fiyatlar)}."""
    bugun = _simdi()
    bas = (bugun - timedelta(days=VERI_GUN)).strftime("%d-%m-%Y")
    bit = bugun.strftime("%d-%m-%Y")
    ham = {}

    # Birincil kaynak: isyatirimhisse (Is Yatirim EOD) — toplu cagri + eksikler icin ek turlar
    try:
        from isyatirimhisse import fetch_stock_data
        df = fetch_stock_data(HISSELER, start_date=bas, end_date=bit)
        if df is not None and not df.empty:
            df.columns = [str(c).upper() for c in df.columns]
            kod = next((c for c in ["HGDG_HS_KODU", "STOCK_CODE", "SYMBOL"] if c in df.columns), None)
            kapanis = next((c for c in ["HGDG_KAPANIS", "KAPANIS", "CLOSE"] if c in df.columns), None)
            tarih = next((c for c in ["HGDG_TARIH", "TARIH", "DATE"] if c in df.columns), None)
            if kod and kapanis and tarih:
                for h in HISSELER:
                    alt = df[df[kod] == h].dropna(subset=[kapanis])
                    if not alt.empty:
                        seri = alt.set_index(alt[tarih].astype(str).str[:10])[kapanis].astype(float)
                        seri = seri[seri > 0]
                        if not seri.empty:
                            ham[h] = seri
        eksikler = [h for h in HISSELER if h not in ham]
        for i, h in enumerate(eksikler):
            try:
                dfh = fetch_stock_data([h], start_date=bas, end_date=bit)
                if dfh is not None and not dfh.empty:
                    dfh.columns = [str(c).upper() for c in dfh.columns]
                    hk = next((c for c in ["HGDG_HS_KODU", "STOCK_CODE", "SYMBOL"] if c in dfh.columns), None)
                    hka = next((c for c in ["HGDG_KAPANIS", "KAPANIS", "CLOSE"] if c in dfh.columns), None)
                    ht = next((c for c in ["HGDG_TARIH", "TARIH", "DATE"] if c in dfh.columns), None)
                    if hk and hka and ht:
                        alt = dfh[dfh[hk] == h].dropna(subset=[hka])
                        if not alt.empty:
                            ham[h] = alt.set_index(alt[ht].astype(str).str[:10])[hka].astype(float)
            except Exception as e:
                log.warning("[VERI] %s isyatirimhisse'den gelmedi: %s", h, str(e)[:80])
            time.sleep(min(1 + i * 0.2, 3))
    except Exception as e:
        log.warning("[VERI] isyatirimhisse kullanilamadi: %s", str(e)[:120])

    # Yedek kaynak: hala gelmeyen semboller icin yfinance (.IS)
    eksikler = [h for h in HISSELER if h not in ham]
    if eksikler:
        try:
            import yfinance as yf
            for h in eksikler:
                try:
                    d = yf.download(h + ".IS",
                                    start=(bugun - timedelta(days=VERI_GUN)).strftime("%Y-%m-%d"),
                                    progress=False, auto_adjust=True)
                    if d is not None and not d.empty and "Close" in d:
                        ham[h] = d["Close"].iloc[:, 0] if hasattr(d["Close"], "columns") else d["Close"]
                except Exception as e:
                    log.warning("[VERI] %s yfinance'ten de gelmedi: %s", h, str(e)[:80])
                time.sleep(1)
        except ImportError:
            log.warning("[VERI] yfinance kurulu degil; yedek kaynak atlandi.")

    seriler = _serileri_duzelt(ham)
    log.info("[VERI] %d/%d hisse icin kapanis serisi hazir.", len(seriler), len(HISSELER))
    return seriler


def gecmis_oku_ya_da_cek():
    """Gunluk kapanis serilerini GUNLUK yerel onbellekten okur; yoksa ceker ve kaydeder.

    Boylece 30 dakikalik kron kosulari kaynagi her seferinde yormaz; gun icindeki
    kosular onbellek + canli fiyatla calisir. Onbellekler son 3 gun tutulur."""
    bugun = _simdi().strftime("%Y-%m-%d")
    os.makedirs(GECMIS_DIR, exist_ok=True)
    yol = os.path.join(GECMIS_DIR, bugun + ".json")
    if os.path.exists(yol):
        try:
            with open(yol, encoding="utf-8") as f:
                ham = json.load(f)
            log.info("[VERI] Gecmis onbellekten yuklendi (%d hisse, %s).", len(ham), bugun)
            return {h: (v[0], v[1]) for h, v in ham.items()}
        except Exception:
            pass
    seriler = veri_cek()
    if len(seriler) >= 5:
        try:
            with open(yol, "w", encoding="utf-8") as f:
                json.dump({h: [v[0], v[1]] for h, v in seriler.items()}, f, ensure_ascii=False)
        except OSError:
            log.warning("[VERI] Gecmis onbellek yazilamadi: %s", yol)
    # eski onbellekleri temizle (son 3 gun kalsin)
    eski_sinir = (_simdi() - timedelta(days=3)).strftime("%Y-%m-%d")
    for fn in os.listdir(GECMIS_DIR):
        if fn.endswith(".json") and fn[:-5] < eski_sinir:
            try:
                os.remove(os.path.join(GECMIS_DIR, fn))
            except OSError:
                pass
    return seriler


def canli_fiyatlar():
    """TradingView scanner ile 30 hissenin son islem fiyatini TEK istekte alir.

    Oturum acikken son islem fiyati (~15 dk gecikmeli), kapaliyken kapanisi verir.
    Basarisizsa bos sozluk doner; robot o zaman son kapanis verisiyle calisir."""
    try:
        govde = {"symbols": {"tickers": ["BIST:" + h for h in HISSELER], "query": {"types": []}},
                 "columns": ["name", "close"]}
        istek = urllib.request.Request(
            "https://scanner.tradingview.com/turkey/scan",
            data=json.dumps(govde).encode(),
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
        with urllib.request.urlopen(istek, timeout=25) as r:
            ham = json.loads(r.read().decode())
        out = {}
        for satir in ham.get("data", []):
            d = satir.get("d") or []
            if len(d) >= 2 and d[0] and d[1] and float(d[1]) > 0:
                out[str(d[0])] = float(d[1])
        return out
    except Exception as e:
        log.warning("[VERI] TradingView canli fiyat alinamadi: %s", str(e)[:120])
        return {}


def seri_hazirla(seriler, canli, bugun, ekle):
    """Piyasa acikken bugunun barini CANLI fiyattan gecici olarak serilere ekler."""
    if not (ekle and canli):
        return seriler
    out = {}
    for h, (t, f) in seriler.items():
        if h in canli and (not t or t[-1] < bugun):
            out[h] = (t + [bugun], f + [float(canli[h])])
        else:
            out[h] = (t, f)
    return out


# ---------- 2. STRATEJI MOTORU ----------
def _sma(degerler, n):
    if len(degerler) < n:
        return None
    return sum(degerler[-n:]) / n


def _rsi(degerler, n=14):
    """Wilder duzlestirmeli RSI; hesaplanamiyorsa None."""
    if len(degerler) < n + 1:
        return None
    kazanc, kayip = [], []
    for i in range(1, n + 1):
        fark = degerler[i] - degerler[i - 1]
        kazanc.append(max(fark, 0.0))
        kayip.append(max(-fark, 0.0))
    ort_k = sum(kazanc) / n
    ort_kayip = sum(kayip) / n
    for i in range(n + 1, len(degerler)):
        fark = degerler[i] - degerler[i - 1]
        ort_k = (ort_k * (n - 1) + max(fark, 0.0)) / n
        ort_kayip = (ort_kayip * (n - 1) + max(-fark, 0.0)) / n
    if ort_kayip == 0:
        return 100.0
    rs = ort_k / ort_kayip
    return 100.0 - 100.0 / (1.0 + rs)


def strateji_sinyalleri(seriler):
    """Her hisse icin AL/SAT/BEKLE sinyali uretir: {hisse: {...}}.

    AL  : SMA10, SMA30'un UZERINDE (dizilim durumu — taze kesisim sart degil,
          boylece robot duzeltme donemlerinde de pozisyon acar) VE RSI14 45-75
          bandinda VE fiyat SMA100'un uzerinde (trend filtresi).
    SAT : SMA10, SMA30'un altina indi (kesisim olayi) — stop/hedef OMS'te de
          tetiklenir. Piyasa acikken son bar CANLI fiyattan gecici bardir
          (gun ici sinyaller yakalanir)."""
    sinyaller = {}
    for h, (tarihler, fiyatlar) in seriler.items():
        if len(fiyatlar) < 110:
            continue
        f = fiyatlar
        s10 = [_sma(f[:i + 1], 10) for i in range(len(f) - 2, len(f))]
        s30 = [_sma(f[:i + 1], 30) for i in range(len(f) - 2, len(f))]
        sma100 = _sma(f, 100)
        rsi = _rsi(f)
        # Kesisim OLAYI (durum degil): onceki bardaki iliski degisir.
        onceki_ok = s10[0] is not None and s30[0] is not None and s10[1] is not None and s30[1] is not None
        usti_cikti = onceki_ok and s10[1] > s30[1]
        asagi_kesti = onceki_ok and s10[0] >= s30[0] and s10[1] < s30[1]
        sinyal, gerekce = "BEKLE", ""
        if usti_cikti:
            if rsi is None or not (45.0 <= rsi <= 75.0) or f[-1] <= sma100:
                neden = []
                if rsi is not None and rsi > 75:
                    neden.append(f"RSI {rsi:.0f} asiri alim bolgesinde")
                elif rsi is not None and rsi < 45:
                    neden.append(f"RSI {rsi:.0f} zayif")
                if f[-1] <= sma100:
                    neden.append("fiyat SMA100 altinda")
                sinyal, gerekce = "BEKLE", "dizilim AL yonunde ama: " + ", ".join(neden)
            else:
                sinyal = "AL"
                gerekce = f"SMA10 > SMA30 dizilimi · RSI {rsi:.0f} · fiyat SMA100 uzerinde"
        elif asagi_kesti:
            sinyal = "SAT"
            gerekce = "SMA10, SMA30'un asagi kesti"
        sinyaller[h] = {
            "sinyal": sinyal, "gerekce": gerekce,
            "son_fiyat": f[-1], "tarih": tarihler[-1],
            "rsi": round(rsi, 1) if rsi is not None else None,
            "sma100": round(sma100, 2),
        }
    return sinyaller


# ---------- 5. MUHASEBE (durum + append-only islem gunlugu) ----------
def durum_oku():
    try:
        with open(DURUM_YOL, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def durum_yaz(durum):
    os.makedirs(os.path.dirname(DURUM_YOL), exist_ok=True)
    with open(DURUM_YOL, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=2, sort_keys=True)


def islem_logla(kayit):
    os.makedirs(os.path.dirname(ISLEM_YOL), exist_ok=True)
    with open(ISLEM_YOL, "a", encoding="utf-8") as f:
        f.write(json.dumps(kayit, ensure_ascii=False) + "\n")


def islemleri_oku():
    try:
        with open(ISLEM_YOL, encoding="utf-8") as f:
            return [json.loads(satir) for satir in f if satir.strip()]
    except Exception:
        return []


def ozkaynak(durum, fiyatlar):
    """Nakit + acik pozisyonlarin piyasa degeri. Fiyati olmayan pozisyon maliyetle degerlenir."""
    toplam = durum["nakit"]
    for h, p in durum["pozisyonlar"].items():
        toplam += p["lot"] * fiyatlar.get(h, p["maliyet"])
    return toplam


def ozkayit_guncelle(durum, sinyaller):
    """Bu kontrolun ozkaynak degerini egrisine zaman damgali ekler (ayni dakikada uzerine yazar)."""
    fiyatlar = {h: s["son_fiyat"] for h, s in sinyaller.items()}
    deger = round(ozkaynak(durum, fiyatlar), 2)
    simdi = _simdi().strftime("%Y-%m-%dT%H:%M")
    k = durum["ozkayit"]
    if k and k[-1][0] == simdi:
        k[-1][1] = deger
    elif k and k[-1][0] > simdi:
        return
    else:
        k.append([simdi, deger])
    sinir = (_simdi() - timedelta(days=OZKAYIT_GUN)).strftime("%Y-%m-%d")
    durum["ozkayit"] = [k[0]] + [p for p in k[1:] if p[0][:10] >= sinir]


# ---------- 3. RISK + 4. OMS ----------
def risk_ve_oms(durum, sinyaller):
    """Bu kontrolda sinyalleri risk kurallarindan gecirip simule emirlere cevirir.

    - Stop-loss / hedef / kesisim-asagisi cikislari CANLI fiyattan tetiklenir.
    - AL sinyali gun icinde de isleme donusebilir (gecici bar ile).
    - Ayni hisse ayni gun satusa tekrar alinmaz (bugun_satilan listesi).
    - Dunku kapanisa gore gunluk %3 zarar varsa yeni alimlar durur.
    Donus: (emir_sayisi, notlar)."""
    fiyatlar = {h: s["son_fiyat"] for h, s in sinyaller.items()}
    oz = ozkaynak(durum, fiyatlar)
    saat = _simdi().strftime("%H:%M")
    notlar = []
    islem_sayisi = 0

    onceki = float(durum.get("onceki_kapanis_ozkaynak", KAPITAL0))
    duraklatildi = oz < onceki * (1.0 - GUNLUK_ZARAR_LIMITI)
    if duraklatildi and onceki > 0:
        dusus = (1.0 - oz / onceki) * 100.0
        notlar.append(f"Günlük zarar freni: özkaynak dünkü kapanışa göre %{dusus:.1f} düştü — yeni ALIM yapılmıyor.")

    # --- SATISLAR (once satar, nakit acilir): canli fiyatta stop / hedef / kesisim
    for h, p in list(durum["pozisyonlar"].items()):
        s = sinyaller.get(h)
        if s is None:
            continue
        fiyat = s["son_fiyat"]
        stop_tetik = fiyat <= p["stop"]
        hedef_tetik = fiyat >= p["hedef"]
        if s["sinyal"] == "SAT" or stop_tetik or hedef_tetik:
            if stop_tetik and s["sinyal"] != "SAT":
                gerekce = f"stop-loss: {p['stop']:.2f} seviyesi canlı fiyatta delindi (%{STOP_ORAN * 100:.0f} kural)"
            elif hedef_tetik and s["sinyal"] != "SAT":
                gerekce = f"hedef fiyat: {p['hedef']:.2f} seviyesi canlı fiyatta ulaşıldı (+%{HEDEF_ORAN * 100:.0f} kural)"
            else:
                gerekce = s["gerekce"]
            net = fiyat * (1.0 - KAYMA) * (1.0 - KOMISYON)
            tutar = net * p["lot"]
            kz = (net - p["maliyet"]) * p["lot"]
            durum["nakit"] += tutar
            del durum["pozisyonlar"][h]
            durum.setdefault("bugun_satilan", []).append(h)
            islem_sayisi += 1
            durum["gerceklesen_kz"] = durum.get("gerceklesen_kz", 0.0) + kz
            islem_logla({
                "tarih": _simdi().strftime("%Y-%m-%d"), "saat": saat, "hisse": h,
                "yon": "SAT", "lot": p["lot"], "fiyat": round(fiyat, 2),
                "tutar": round(tutar, 2), "kz": round(kz, 2), "gerekce": gerekce,
                "giris_tarihi": p["giris"], "nakit_sonra": round(durum["nakit"], 2),
            })
            log.info("[SAT] %s lot=%d fiyat=%.2f K/Z=%+.2f (%s)", h, p["lot"], fiyat, kz, gerekce)

    # --- ALIMLAR: slot + nakit + boyut + gun ici koruma kurallari
    if not duraklatildi:
        adaylar = sorted(
            [(h, s) for h, s in sinyaller.items()
             if s["sinyal"] == "AL" and h not in durum["pozisyonlar"]
             and h not in durum.get("bugun_alinan", []) and h not in durum.get("bugun_satilan", [])],
            key=lambda x: x[1]["rsi"] if x[1]["rsi"] is not None else 100.0,
        )
        for h, s in adaylar:
            if len(durum["pozisyonlar"]) >= MAKS_POZISYON:
                notlar.append(f"{h}: AL sinyali vardı ama maks. pozisyon sayısına ({MAKS_POZISYON}) ulaşıldı.")
                continue
            fiyat = s["son_fiyat"]
            boyut = min(oz * POZISYON_ORAN, durum["nakit"])
            lot = math.floor(boyut / fiyat)
            if lot < 1 or lot * fiyat < MIN_ISLEM_TL:
                notlar.append(f"{h}: AL sinyali vardı ama nakit/boyut kuralı işlem açmaya yetmedi.")
                continue
            maliyet = fiyat * (1.0 + KAYMA) * (1.0 + KOMISYON)
            toplam = maliyet * lot
            if toplam > durum["nakit"]:
                lot -= 1
                if lot < 1:
                    notlar.append(f"{h}: AL sinyali vardı ama komisyon sonrası nakit yetmedi.")
                    continue
                toplam = maliyet * lot
            durum["nakit"] -= toplam
            durum["pozisyonlar"][h] = {
                "lot": lot, "maliyet": round(maliyet, 4), "giris": _simdi().strftime("%Y-%m-%d"),
                "stop": round(maliyet * (1.0 + STOP_ORAN), 2),
                "hedef": round(maliyet * (1.0 + HEDEF_ORAN), 2),
            }
            durum.setdefault("bugun_alinan", []).append(h)
            islem_sayisi += 1
            islem_logla({
                "tarih": _simdi().strftime("%Y-%m-%d"), "saat": saat, "hisse": h,
                "yon": "AL", "lot": lot, "fiyat": round(fiyat, 2),
                "tutar": round(toplam, 2), "kz": None, "gerekce": s["gerekce"],
                "giris_tarihi": _simdi().strftime("%Y-%m-%d"),
                "nakit_sonra": round(durum["nakit"], 2),
            })
            log.info("[AL] %s lot=%d fiyat=%.2f (~%.0f TL) — %s", h, lot, fiyat, toplam, s["gerekce"])

    return islem_sayisi, notlar


# ---------- 6. DASHBOARD (robot.html) ----------
def _tr(x, hane=2):
    """Turkce sayi bicimi: 1.234,56"""
    return f"{x:,.{hane}f}".replace(",", "\u00A0").replace(".", ",").replace("\u00A0", ".")


def _kisa_zaman(z):
    """'2026-09-28T14:30' -> '28.09 14:30' ; saf tarih ise '28.09.2026'."""
    if len(z) > 10:
        y, a, g = z[:10].split("-")
        return f"{g}.{a} {z[11:16]}"
    y, a, g = z[:10].split("-")
    return f"{g}.{a}.{y}"


def _egri_svg(ozkayit):
    if len(ozkayit) < 2:
        return ("<svg viewBox='0 0 720 160' style='width:100%;height:auto' role='img'>"
                "<text x='360' y='85' text-anchor='middle' fill='#64748b' font-size='13'>"
                "Eğri için ikinci fiyat kontrolü bekleniyor…</text></svg>")
    degerler = [v for _, v in ozkayit]
    mn, mx = min(degerler), max(degerler)
    if mx - mn < 1e-9:
        mx = mn + 1.0
    W, H, P = 720, 160, 14
    def xy(i, v):
        x = P + i * (W - 2 * P) / (len(degerler) - 1)
        y = H - P - (v - mn) * (H - 2 * P) / (mx - mn)
        return f"{x:.1f},{y:.1f}"
    noktalar = " ".join(xy(i, v) for i, v in enumerate(degerler))
    son, ilk = degerler[-1], degerler[0]
    renk = "#047857" if son >= ilk else "#b91c1c"
    return f"""<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto" role="img" aria-label="Özkaynak eğrisi">
<polyline points="{xy(0, ilk)},{xy(len(degerler) - 1, son)}" fill="none" stroke="#94a3b8" stroke-width="1" stroke-dasharray="4 4"/>
<polyline points="{noktalar}" fill="none" stroke="{renk}" stroke-width="2.5" stroke-linejoin="round"/>
<text x="{P}" y="{H - 2}" font-size="10" fill="#64748b">{_kisa_zaman(ozkayit[0][0])}</text>
<text x="{W - P}" y="{H - 2}" font-size="10" fill="#64748b" text-anchor="end">{_kisa_zaman(ozkayit[-1][0])}</text>
<text x="{W - P}" y="{P + 2}" font-size="11" fill="{renk}" text-anchor="end">{_tr(son)} TL</text>
<text x="{P}" y="{P + 2}" font-size="11" fill="#64748b">{_tr(ilk)} TL (başlangıç)</text>
</svg>"""


def _bugun_bolumu(durum):
    """Gunun ozeti: kac kontrol yapildi, bugunku islemler (saat ve gerekcesiyle)."""
    bugun = durum.get("bugun_tarih", _simdi().strftime("%Y-%m-%d"))
    islemler = [i for i in islemleri_oku() if i.get("tarih") == bugun]
    kontrol = durum.get("kontrol_sayisi", 0)
    hucreler = "".join(
        f"<div class='pano-hucre'><div class='pano-etiket'>"
        f"<span class='{'pos' if i['yon'] == 'AL' else 'neg'}'>{i['yon']}</span> · {i['hisse']} · "
        f"{i['lot']} lot · {_tr(i['fiyat'])} ₺ · {i.get('saat', '')}</div>{i['gerekce']}</div>"
        for i in islemler)
    if not hucreler:
        hucreler = "<div class='pano-hucre'>Bugün henüz işlem yok — sinyal bekleniyor.</div>"
    acik_etiket = "🟢 piyasa açık — canlı takip" if piyasa_acik_mi() else "🔴 piyasa kapalı — son kapanış verisi"
    return (f"<h2 class='section-title'>Bugün ({_kisa_zaman(bugun)})</h2>"
            f"<div class='grid' style='gap:10px'>"
            f"<div class='pano-hucre'><div class='pano-etiket'>Fiyat kontrolü</div>"
            f"<span id='bugun-kontrol'>{kontrol}</span> kez · piyasa saatlerinde 30 dakikada bir · {acik_etiket}</div>"
            f"{hucreler}</div>")


def dashboard_yaz(durum, sinyaller, notlar):
    now = _simdi().strftime("%d.%m.%Y %H:%M")
    islemler = islemleri_oku()
    fiyatlar = {h: s["son_fiyat"] for h, s in sinyaller.items()}
    oz = ozkaynak(durum, fiyatlar)
    toplam_kz = oz - KAPITAL0
    getiri = toplam_kz / KAPITAL0 * 100.0
    ger_kz = durum.get("gerceklesen_kz", 0.0)

    satislar = [i for i in islemler if i["yon"] == "SAT"]
    kazananlar = [i for i in satislar if i.get("kz", 0) > 0]
    kazanma_orani = (len(kazananlar) / len(satislar) * 100.0) if satislar else 0.0

    def stat(etiket, deger, cls="", kimlik=""):
        id_html = f" id='{kimlik}'" if kimlik else ""
        return (f"<div class='stat'{id_html} style='background:var(--card);border:1px solid var(--line);"
                f"border-radius:10px;padding:12px 14px'><div class='label'>{etiket}</div>"
                f"<div style='font-size:21px;font-weight:700' class='{cls}'>{deger}</div></div>")

    kpi = "<div class='grid' style='grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:18px 0'>" + "".join([
        stat("Toplam Değer", _tr(oz) + " ₺", "", "kpi-toplam"),
        stat("Toplam Getiri", f"{_tr(getiri)}%", "pos" if toplam_kz >= 0 else "neg", "kpi-getiri"),
        stat("Nakit", _tr(durum["nakit"]) + " ₺", "", "kpi-nakit"),
        stat("Açık Pozisyon", str(len(durum["pozisyonlar"])) + f" / {MAKS_POZISYON}", "", "kpi-poz"),
        stat("Gerçekleşen K/Z", _tr(ger_kz) + " ₺", "pos" if ger_kz >= 0 else "neg", "kpi-kz"),
        stat("Kazanma Oranı", f"{_tr(kazanma_orani, 0)}%", "", "kpi-oran"),
    ]) + "</div>"

    poz_satirlar = ""
    for h, p in sorted(durum["pozisyonlar"].items()):
        fiyat = fiyatlar.get(h, p["maliyet"])
        deger = p["lot"] * fiyat
        kz = (fiyat - p["maliyet"]) * p["lot"]
        kzy = kz / (p["maliyet"] * p["lot"]) * 100.0
        sinif = "pos" if kz >= 0 else "neg"
        poz_satirlar += (f"<tr><td><b>{h}</b></td><td>{p['lot']}</td>"
                         f"<td>{_tr(p['maliyet'])}</td><td>{_tr(fiyat)}</td>"
                         f"<td>{_tr(deger)}</td><td class='{sinif}'>{_tr(kz)}</td>"
                         f"<td class='{sinif}'>{_tr(kzy)}%</td>"
                         f"<td>{_tr(p['stop'])}</td><td>{_tr(p['hedef'])}</td>"
                         f"<td>{p['giris']}</td></tr>")
    pozisyon_bolumu = ("<h2 class='section-title'>Açık Pozisyonlar</h2><div class='card' style='padding:8px 24px 16px' id='sec-poz'>"
                       + ("<div style='overflow-x:auto'><table><tr><th>Hisse</th><th>Lot</th><th>Maliyet</th><th>Son</th><th>Değer ₺</th>"
                          f"<th>K/Z ₺</th><th>K/Z %</th><th>Stop</th><th>Hedef</th><th>Giriş</th></tr>{poz_satirlar}</table></div>"
                          if poz_satirlar else "<p style='color:var(--muted)'>Henüz açık pozisyon yok — robot AL sinyali bekliyor.</p>")
                       + "</div>")

    islem_satirlar = ""
    for i in islemler[-25:][::-1]:
        kz_hucre = "<td>—</td>"
        if i["yon"] == "SAT" and i.get("kz") is not None:
            kz = i["kz"]
            kz_hucre = f"<td class='{'pos' if kz >= 0 else 'neg'}'>{_tr(kz)}</td>"
        islem_satirlar += (f"<tr><td>{i['tarih']} {i.get('saat', '')}</td>"
                           f"<td><b>{i['hisse']}</b></td>"
                           f"<td class='{'pos' if i['yon'] == 'AL' else 'neg'}'>{i['yon']}</td>"
                           f"<td>{i['lot']}</td><td>{_tr(i['fiyat'])}</td><td>{_tr(i['tutar'])}</td>"
                           f"{kz_hucre}<td style='max-width:340px'>{i['gerekce']}</td></tr>")
    islem_bolumu = ("<h2 class='section-title'>Son İşlemler</h2><div class='card' style='padding:8px 24px 16px' id='sec-islem'>"
                    + ("<div style='overflow-x:auto'><table><tr><th>Zaman</th><th>Hisse</th><th>Yön</th><th>Lot</th><th>Fiyat</th>"
                       f"<th>Tutar ₺</th><th>K/Z ₺</th><th>Gerekçe</th></tr>{islem_satirlar}</table></div>"
                       if islem_satirlar else "<p style='color:var(--muted)'>Henüz işlem yok.</p>")
                    + "</div>")

    # Sinyal panosu: son kontroldaki AL/SAT olaylari
    pano = ""
    for h, s in sorted(sinyaller.items()):
        if s["sinyal"] == "BEKLE" or h in durum["pozisyonlar"] and s["sinyal"] != "SAT":
            continue
        if h in durum["pozisyonlar"]:
            durum_notu = "portföyde — satıldı" if s["sinyal"] == "SAT" else "portföyde"
        else:
            durum_notu = "satılacak pozisyon yok" if s["sinyal"] == "SAT" else "alım adayı"
        pano += (f"<div class='pano-hucre'><div class='pano-etiket'>"
                 f"<span class='{'pos' if s['sinyal'] in ('AL',) else 'neg'}'>{s['sinyal']}</span> · {h} · {_tr(s['son_fiyat'])} ₺ · {durum_notu}</div>"
                 f"{s['gerekce']}</div>")
    for n in notlar:
        pano += f"<div class='pano-hucre'>ℹ️ {n}</div>"
    if not pano:
        pano = "<div class='pano-hucre'>Son kontrolde yeni AL/SAT sinyali üretmedi — robot bekliyor.</div>"
    pano_bolumu = ("<h2 class='section-title'>Sinyal Panosu (son kontrol: "
                   + (durum.get("son_bar_tarihi") or "—") + ")</h2><div class='grid' style='gap:10px'>" + pano + "</div>")

    istatistik = (f"<div class='grid-iki'><div class='card'><div class='pano-baslik'>Strateji Kuralları</div>"
                  f"<ul style='margin:0;padding-left:18px;color:var(--muted);font-size:13.5px;line-height:1.7'>"
                  f"<li><b>AL:</b> SMA10, SMA30'un üzerinde dizilim + RSI14 45–75 bandında + fiyat SMA100 üzerinde (taze kesişim beklenmez; dizilim varsa girilir).</li>"
                  f"<li><b>SAT:</b> SMA10, SMA30'u aşağı keser, ya da maliyetin <b>%{abs(int(STOP_ORAN * 100))}</b> altına düşer (stop), "
                  f"ya da <b>%{int(HEDEF_ORAN * 100)}</b> yukarısına çıkar (hedef).</li>"
                  f"<li><b>Boyut:</b> özkaynağın en fazla %{int(POZISYON_ORAN * 100)}'i tek hisseye, en fazla {MAKS_POZISYON} pozisyon.</li>"
                  f"<li><b>Günlük fren:</b> özkaynak dünkü kapanışa göre %{int(GUNLUK_ZARAR_LIMITI * 100)} düşerse yeni alım o gün durdurulur.</li>"
                  f"<li><b>Sürtünme:</b> işlem başına %{_tr(KOMISYON * 100, 2)} komisyon + %{_tr(KAYMA * 100, 2)} kayma varsayılır.</li></ul></div>"
                  f"<div class='card'><div class='pano-baslik'>Performans Özeti</div>"
                  f"<ul style='margin:0;padding-left:18px;color:var(--muted);font-size:13.5px;line-height:1.7'>"
                  f"<li>Başlangıç: {durum['ozkayit'][0][0] if durum['ozkayit'] else '—'} · {_tr(KAPITAL0)} ₺</li>"
                  f"<li>Toplam işlem: {len(islemler)} ({len(satislar)} SAT / {len(islemler) - len(satislar)} AL)</li>"
                  f"<li>Kazanan SAT oranı: {_tr(kazanma_orani, 0)}%</li>"
                  f"<li>Gerçekleşen K/Z: {_tr(ger_kz)} ₺</li>"
                  f"<li>Açık pozisyon K/Z (gerçekleşmemiş): {_tr(toplam_kz - ger_kz)} ₺</li></ul></div></div>")

    icerik = f"""<div class="hero">
<h1>İşlem Robotu <span class="badge">simülasyon</span></h1>
<p>BIST 30 hisseleri üzerinde <b>kağıt-üstünde işlem</b> yapan otomatik robotun canlı defteri.
Robot <b>piyasa saatlerinde 30 dakikada bir</b> canlı fiyattan (TradingView, ~15 dk gecikmeli) kontrol edilir:
stop-loss ve hedef gün içinde tetiklenebilir, kesişim sinyalleri gün içi fiyattan değerlendirilir ve
özkaynak eğrisi her kontrolde bir nokta kazanır. <u>Gerçek para kullanılmaz</u>; tüm emirler gerekçesiyle burada yayımlanır.
<strong>Yatırım tavsiyesi değildir.</strong> Son güncelleme: {now} (İstanbul).</p>
</div>
{kpi}
{_bugun_bolumu(durum)}
<h2 class="section-title">Özkaynak Eğrisi <span style="font-size:13px;color:var(--muted);font-weight:400">— her fiyat kontrolünde bir nokta</span></h2>
<div class="card" id='sec-egri'>{_egri_svg(durum["ozkayit"])}</div>
{pozisyon_bolumu}
{islem_bolumu}
{pano_bolumu}
{istatistik}"""

    html = _iskelet("İşlem Robotu (Simülasyon)", icerik, now)
    with open(SAYFA_YOL, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[DASHBOARD] robot.html yazildi (ozkaynak=%s TL, pozisyon=%d, nokta=%d)",
             _tr(oz), len(durum["pozisyonlar"]), len(durum["ozkayit"]))


# ---------- SITENIN UST GORUNUMU (bot.py _sayfa ciktisinin bagimsiz KOPYASI) ----------
# UYARI: Bilincli tercih — robot bot.py'yi import etmez; site capasi burada
# statik kopya olarak tutulur. bot.py'deki ust menu/seritler degisirse bu
# kopyalar da guncellenmelidir. Kaldirma: bu sablonlar yalnizca robot.html'i
# etkiler, baska hicbir sayfaya dokunmaz.

_SITE_NAV = """<nav><a href="index.html">Raporlar</a><a href="hisse/index.html">Hisseler</a><a href="derin-analiz.html">Derin Analiz</a><a href="makro-analiz.html">Makro Analiz</a><a href="teknik-analiz.html">Teknik Tarama</a><a href="sinyal-karnesi.html">Sinyal Karnesi</a><a href="borsapy-analiz.html">Borsapy Sinyal</a><a href="haberler.html">Haberler</a><a href="sirket-haberleri.html">Şirket Haberleri</a><a href="portfolio.html">Deneme Portföyü</a><a href="haftasonu.html">Hafta Sonu</a><a href="haftasonu-egitimi.html">Borsa Okulu</a><a href="takvim.html">📅 Takvim</a><a href="sozluk.html">Sözlük</a><a href="terimler.html">Terimler</a><a href="muhasebe-terimleri.html">Muhasebe Terimleri</a><a href="robot.html" class="active">İşlem Robotu</a></nav>"""

_SITE_ALTBAR = """<nav class="altbar" aria-label="Hızlı menü">
<a href="index.html"><span class="i" aria-hidden="true">📊</span>Raporlar</a>
<a href="teknik-analiz.html"><span class="i" aria-hidden="true">📈</span>Teknik</a>
<a href="hisse/index.html"><span class="i" aria-hidden="true">🏦</span>Hisseler</a>
<a href="portfolio.html"><span class="i" aria-hidden="true">💼</span>Portföy</a>
<a href="robot.html" class="active"><span class="i" aria-hidden="true">🤖</span>Robot</a>
<a href="haberler.html"><span class="i" aria-hidden="true">📰</span>Haberler</a>
<a href="takvim.html"><span class="i" aria-hidden="true">📅</span>Takvim</a>
</nav>"""

_SITE_SAAT = """    <!-- Üst Widget Alanı (Canlı Saat ve İstanbul Hava Durumu) -->
    <div class="site-widgets">
        <div id="live-clock-weather" style="display: flex; gap: 15px; align-items: center; flex-wrap: wrap;">
            <span id="current-date-time">⏳ Yükleniyor...</span>
            <span id="istanbul-weather">🌤️ İstanbul Hava Durumu...</span>
        </div>
    </div>
<script>
    function updateClock() {{
        const now = new Date();
        const options = {{ timeZone: 'Europe/Istanbul', dateStyle: 'medium', timeStyle: 'medium' }};
        document.getElementById('current-date-time').innerText = '📅 ' + new Intl.DateTimeFormat('tr-TR', options).format(now);
    }}
    setInterval(updateClock, 1000);
    updateClock();

    fetch('https://wttr.in/Istanbul?format=j1')
        .then(response => response.json())
        .then(data => {{
            const current = data.current_condition[0];
            const temp = current.temp_C;
            const desc = current.lang_tr ? current.lang_tr[0].value : current.weatherDesc[0].value;
            document.getElementById('istanbul-weather').innerText = '🌤️ İstanbul: ' + temp + '°C, ' + desc;
        }})
        .catch(err => {{
            document.getElementById('istanbul-weather').innerText = '🌤️ İstanbul: Parçalı Bulutlu';
        }});
</script>"""

_SITE_TICKER1 = """
<div class="ticker-bant" id="ticker-bant" style="margin-bottom:16px">
  <div class="ticker-iz" id="ticker-iz"><span style="color:#94a3b8">Fiyatlar yükleniyor...</span></div>
</div>
<script>
(function() {
  function ciz(veri) {
    var iz = document.getElementById('ticker-iz');
    var ogeler = veri.hisseler.map(function(h) {
      var sinif = h.d >= 0 ? 'pos' : 'neg';
      var ok = h.d >= 0 ? '▲' : '▼';
      return '<span class="ticker-oge"><b>' + h.h + '</b> ' +
             h.f.toLocaleString('tr-TR', {minimumFractionDigits: 2}) + ' TL ' +
             '<span class="' + sinif + '">' + ok + ' ' + (h.d >= 0 ? '+' : '') + h.d.toFixed(2) + '%</span></span>';
    }).join('');
    var etiket = veri.etiket || '~15 dk gecikmeli';
    var saat = '<span class="ticker-oge ticker-saat">' + veri.guncelleme + ' · ' + etiket + '</span>';
    iz.innerHTML = ogeler + saat + ogeler + saat;  // sorunsuz dongu icin kopya
  }
  function yukle(adres) {
    fetch(adres, { cache: 'no-store' })
      .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(ciz)
      .catch(function() {
        if (adres.indexOf('/api/') !== -1) { yukle('ticker.json?t=' + Date.now()); return; }
        var iz2 = document.getElementById('ticker-iz');
        if (iz2) iz2.innerHTML = '<span style="color:#94a3b8">Fiyatlar geçici olarak yüklenemedi; kısa süre içinde yeniden denenecek.</span>';
      });
  }
  yukle('/api/fiyatlar?t=' + Date.now());
  setInterval(function() { yukle('/api/fiyatlar?t=' + Date.now()); }, 5 * 60 * 1000);
})();
</script>"""

_SITE_TICKER2 = """
<div class="ticker-bant" id="ticker-bant2" style="margin-bottom:16px">
  <div class="ticker-iz" id="ticker-iz2"><span style="color:#94a3b8">Piyasa verileri yükleniyor...</span></div>
</div>
<script>
(function() {
  var iz = document.getElementById('ticker-iz2');

  function bicim(g) {
    var deger = Number(g.f).toLocaleString('tr-TR', {minimumFractionDigits: g.o, maximumFractionDigits: g.o});
    var rozet = (typeof g.d === 'number')
      ? ' <span class="' + (g.d >= 0 ? 'pos' : 'neg') + '">' + (g.d >= 0 ? '▲ +' : '▼ ') +
        Number(g.d).toFixed(2) + '%</span>'
      : '';
    var birim = g.b || g.birim || '';
    return '<span class="ticker-oge"><b>' + g.ad + '</b> ' + deger + (birim ? ' ' + birim : '') + rozet + '</span>';
  }
  function ciz(veri) {
    var ogeler = (veri.gostergeler || []).map(bicim).join('');
    if (!ogeler) throw new Error('bos');
    var saat = '<span class="ticker-oge ticker-saat">' + (veri.guncelleme || '') +
               (veri.etiket ? ' · ' + veri.etiket : '') + '</span>';
    iz.innerHTML = ogeler + saat + ogeler + saat;  // sorunsuz dongu icin kopya
  }
  function yukle(adres) {
    fetch(adres, { cache: 'no-store' })
      .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(ciz)
      .catch(function() {
        if (adres.indexOf('/api/') !== -1) { yukle('piyasa-serit.json?t=' + Date.now()); return; }
        if (iz) iz.innerHTML = '<span style="color:#94a3b8">Piyasa verileri geçici olarak yüklenemedi; kısa süre içinde yeniden denenecek.</span>';
      });
  }
  yukle('/api/piyasa?t=' + Date.now());
  setInterval(function() { yukle('/api/piyasa?t=' + Date.now()); }, 5 * 60 * 1000);
})();
</script>"""

_SITE_ARAMA = """
<div class="arama-kutusu">
    <div style="display: flex; gap: 8px;">
        <input type="text" id="site-arama-giris" placeholder="Hisse, konu veya tarih ara…" style="flex: 1; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; background: var(--card); color: var(--ink);" onkeypress="if(event.key === 'Enter') siteAra();">
        <button onclick="siteAra()" id="site-arama-btn" style="background: #0f766e; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: 600;">Ara</button>
    </div>
    <div id="site-arama-sonuc" style="display: none; margin-top: 10px; border-top: 1px solid var(--line); padding-top: 8px; max-height: 320px; overflow-y: auto;"></div>
</div>
<script>
(function() {
  var INDEKS = null;

  function normalize(s) {
    var harita = { 'ı': 'i', 'İ': 'i', 'I': 'i', 'ğ': 'g', 'Ğ': 'g', 'ü': 'u', 'Ü': 'u',
                   'ş': 's', 'Ş': 's', 'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c',
                   'â': 'a', 'î': 'i', 'û': 'u' };
    s = String(s).toLowerCase();
    return s.replace(/[ıİIğĞüÜşŞöÖçÇâîû]/g, function(h) { return harita[h] || h; });
  }

  function indeksYukle() {
    if (INDEKS) return Promise.resolve(INDEKS);
    return fetch('site-arama.json?t=' + Date.now())
      .then(function(r) { return r.json(); })
      .then(function(d) { INDEKS = d; return d; });
  }

  function kacKez(haystack, needle) {
    if (!needle) return 0;
    var sayi = 0, i = 0;
    var h = normalize(haystack), n = normalize(needle);
    while ((i = h.indexOf(n, i)) !== -1) { sayi++; i += n.length; }
    return sayi;
  }

  window.siteAra = function() {
    var giris = document.getElementById('site-arama-giris');
    var panel = document.getElementById('site-arama-sonuc');
    var q = (giris.value || '').trim();
    if (!q) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    panel.innerHTML = '<span style="color:#94a3b8;font-size:13px">Aranıyor...</span>';
    indeksYukle().then(function(indeks) {
      var terimler = q.split(/\\s+/).filter(Boolean);
      var sonuc = (indeks.sayfalar || []).map(function(s) {
        var puan = 0;
        terimler.forEach(function(t) {
          puan += kacKez(s.b, t) * 8 + kacKez(s.t, t);
        });
        return { s: s, puan: puan };
      }).filter(function(x) { return x.puan > 0; })
        .sort(function(a, b) { return b.puan - a.puan; })
        .slice(0, 6);

      var html = '';
      if (!sonuc.length) {
        html += '<div style="color:#64748b;font-size:13px">Site içinde sonuç bulunamadı.</div>';
      } else {
        sonuc.forEach(function(x) {
          var metin = normalize(x.s.t);
          var pos = -1;
          for (var i = 0; i < terimler.length; i++) { var p = metin.indexOf(normalize(terimler[i])); if (p !== -1 && (pos === -1 || p < pos)) pos = p; }
          var kesit = x.s.t;
          if (pos > 60) kesit = '…' + x.s.t.slice(Math.max(0, pos - 40), pos + 90);
          else kesit = x.s.t.slice(0, 130);
          html += '<div style="margin-bottom:8px"><a href="' + x.s.u + '" style="font-weight:600;font-size:13.5px">' + x.s.b + '</a>' +
                  '<div style="color:#64748b;font-size:12.5px">' + kesit.replace(/</g, '&lt;') + '…</div></div>';
        });
      }
      panel.innerHTML = html;
    }).catch(function() {
      panel.innerHTML = '<span style="color:#b91c1c;font-size:13px">Arama indeksi yüklenemedi.</span>';
    });
  };
})();
</script>
"""


_ROBOT_CANLI_JS = """<script>
/* Canlı katman: /api/robot (son commit'lenen durum + canlı fiyat) — üst şerit,
   KPI kartları, pozisyonlar, işlem defteri ve özkaynak eğrisi tarayıcıda
   yeniden render edilir; 19:30/10:30 statik yayını beklemeden güncel kalır. */
(function() {
  var kutu = document.getElementById('robot-canli');
  function tr(x, h) {
    return (x === null || x === undefined) ? '—'
      : Number(x).toLocaleString('tr-TR', {minimumFractionDigits: h || 2, maximumFractionDigits: h || 2});
  }
  function isaret(v) { return v >= 0 ? '+' : ''; }
  function kisaZaman(z) {
    if (!z) return '—';
    if (z.length > 10) return z.slice(8, 10) + '.' + z.slice(5, 7) + ' ' + z.slice(11, 16);
    return z.slice(8, 10) + '.' + z.slice(5, 7) + '.' + z.slice(0, 4);
  }
  function kpiYaz(id, deger, sinif) {
    var el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = deger;
    if (sinif !== undefined) el.className = sinif;
  }
  function cizBar(d) {
    if (!kutu) return;
    var cips = (d.pozisyonlar || []).map(function(p) {
      return "<span class='pano-hucre' style='display:inline-block;padding:4px 10px;margin:2px'><b>" + p.hisse + "</b> "
        + tr(p.son) + " ₺ <span class='" + (p.kzy >= 0 ? 'pos' : 'neg') + "'>"
        + (p.kzy >= 0 ? '+' : '') + tr(p.kzy) + "%</span></span>";
    }).join('');
    if (!cips) cips = "<span style='color:var(--muted)'>açık pozisyon yok — nakit bekleniyor</span>";
    var gun = (d.gunluk_yuzde === null || d.gunluk_yuzde === undefined) ? '' :
      " <span class='" + (d.gunluk_yuzde >= 0 ? 'pos' : 'neg') + "'>(" + (d.gunluk_yuzde >= 0 ? '+' : '') + tr(d.gunluk_yuzde) + "% dünkü kapanışa göre)</span>";
    kutu.innerHTML = "🟢 <b>CANLI</b> · son fiyat " + (d.fiyat_saati || '—') +
      " · Toplam <b style='font-size:17px'>" + tr(d.toplam) + " ₺</b>" + gun + " · " + cips +
      "<div style='color:var(--muted);font-size:11.5px;margin-top:6px'>Fiyatlar ~15 dk gecikmelidir; tüm bölüm 1 dakikada bir tazelenir.</div>";
    kutu.dataset.yuklendi = '1';
  }
  function cizKpi(d) {
    kpiYaz('kpi-toplam', tr(d.toplam) + ' ₺');
    var g = d.toplam_getiri_yuzde || 0;
    kpiYaz('kpi-getiri', isaret(g) + tr(g) + '%', g >= 0 ? 'pos' : 'neg');
    kpiYaz('kpi-nakit', tr(d.nakit) + ' ₺');
    kpiYaz('kpi-poz', (d.pozisyon_sayisi || 0) + ' / 8');
    var kz = d.gerceklesen_kz || 0;
    kpiYaz('kpi-kz', tr(kz) + ' ₺', kz >= 0 ? 'pos' : 'neg');
    kpiYaz('kpi-oran', tr(d.kazanma_orani || 0, 0) + '%');
    var bk = document.getElementById('bugun-kontrol');
    if (bk) bk.innerHTML = d.kontrol_sayisi || 0;
  }
  function cizPoz(d) {
    var hedef = document.getElementById('sec-poz');
    if (!hedef) return;
    var p = d.pozisyonlar || [];
    if (!p.length) { hedef.innerHTML = "<p style='color:var(--muted)'>Henüz açık pozisyon yok — robot AL sinyali bekliyor.</p>"; return; }
    var satir = p.map(function(x) {
      var s = x.kz >= 0 ? 'pos' : 'neg';
      return '<tr><td><b>' + x.hisse + '</b></td><td>' + x.lot + '</td><td>' + tr(x.maliyet) + '</td><td>' + tr(x.son) +
        '</td><td>' + tr(x.son * x.lot) + '</td><td class="' + s + '">' + tr(x.kz) + '</td><td class="' + s + '">' + tr(x.kzy) + '%</td>' +
        '<td>' + tr(x.stop) + '</td><td>' + tr(x.hedef) + '</td><td>' + (x.giris || '—') + '</td></tr>';
    }).join('');
    hedef.innerHTML = "<div style='overflow-x:auto'><table><tr><th>Hisse</th><th>Lot</th><th>Maliyet</th><th>Son</th><th>Değer ₺</th><th>K/Z ₺</th><th>K/Z %</th><th>Stop</th><th>Hedef</th><th>Giriş</th></tr>" + satir + "</table></div>";
  }
  function cizIslem(d) {
    var hedef = document.getElementById('sec-islem');
    if (!hedef) return;
    var l = d.son_islemler || [];
    if (!l.length) { hedef.innerHTML = "<p style='color:var(--muted)'>Henüz işlem yok.</p>"; return; }
    var satir = l.map(function(i) {
      var kzH = '<td>—</td>';
      if (i.yon === 'SAT' && i.kz !== null && i.kz !== undefined) kzH = '<td class="' + (i.kz >= 0 ? 'pos' : 'neg') + '">' + tr(i.kz) + '</td>';
      return '<tr><td>' + (i.tarih || '') + ' ' + (i.saat || '') + '</td><td><b>' + i.hisse + '</b></td>' +
        '<td class="' + (i.yon === 'AL' ? 'pos' : 'neg') + '">' + i.yon + '</td><td>' + i.lot + '</td><td>' + tr(i.fiyat) +
        '</td><td>' + tr(i.tutar) + '</td>' + kzH + '<td style="max-width:340px">' + (i.gerekce || '') + '</td></tr>';
    }).join('');
    hedef.innerHTML = "<div style='overflow-x:auto'><table><tr><th>Zaman</th><th>Hisse</th><th>Yön</th><th>Lot</th><th>Fiyat</th><th>Tutar ₺</th><th>K/Z ₺</th><th>Gerekçe</th></tr>" + satir + "</table></div>";
  }
  function cizEgri(d) {
    var hedef = document.getElementById('sec-egri');
    if (!hedef) return;
    var k = d.ozkayit || [];
    if (k.length < 2) return;
    var vals = k.map(function(p) { return p[1]; });
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    if (mx - mn < 1e-9) mx = mn + 1;
    var W = 720, H = 160, P = 14;
    function xy(i, v) {
      return (P + i * (W - 2 * P) / (vals.length - 1)).toFixed(1) + ',' + (H - P - (v - mn) * (H - 2 * P) / (mx - mn)).toFixed(1);
    }
    var pts = vals.map(function(v, i) { return xy(i, v); }).join(' ');
    var renk = vals[vals.length - 1] >= vals[0] ? '#047857' : '#b91c1c';
    hedef.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto" role="img" aria-label="Özkaynak eğrisi">' +
      '<polyline points="' + xy(0, vals[0]) + ',' + xy(vals.length - 1, vals[vals.length - 1]) + '" fill="none" stroke="#94a3b8" stroke-width="1" stroke-dasharray="4 4"/>' +
      '<polyline points="' + pts + '" fill="none" stroke="' + renk + '" stroke-width="2.5" stroke-linejoin="round"/>' +
      '<text x="' + P + '" y="' + (H - 2) + '" font-size="10" fill="#64748b">' + kisaZaman(k[0][0]) + '</text>' +
      '<text x="' + (W - P) + '" y="' + (H - 2) + '" font-size="10" fill="#64748b" text-anchor="end">' + kisaZaman(k[k.length - 1][0]) + '</text>' +
      '<text x="' + (W - P) + '" y="' + (P + 2) + '" font-size="11" fill="' + renk + '" text-anchor="end">' + tr(vals[vals.length - 1]) + ' TL</text>' +
      '<text x="' + P + '" y="' + (P + 2) + '" font-size="11" fill="#64748b">' + tr(vals[0]) + ' TL (başlangıç)</text></svg>';
  }
  function cizHepsi(d) {
    if (!d || d.hata) {
      if (kutu && !kutu.dataset.yuklendi) kutu.innerHTML = "<span style='color:var(--muted)'>🟠 Canlı özet şu an erişilemiyor — aşağıdaki tablolar son güncellemeye aittir.</span>";
      return;
    }
    cizBar(d); cizKpi(d); cizPoz(d); cizIslem(d); cizEgri(d);
  }
  function yenile() {
    fetch('/api/robot').then(function(r) { return r.json(); }).then(cizHepsi)
      .catch(function() { if (kutu && !kutu.dataset.yuklendi) kutu.innerHTML = "<span style='color:var(--muted)'>🟠 Canlı özet şu an erişilemiyor.</span>"; });
  }
  yenile();
  setInterval(yenile, 60000);
})();
</script>"""


def _iskelet(title, icerik, guncelleme):
    """Sayfa iskeleti — sitenin style.css'ini ve tam capasini kullanan bagimsiz
    KOPYA (bot.py'ye dokunmaz; ust menu, seritler, saat/hava, arama dahil)."""
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="BIST 30 kağıt-üstünde işlem robotunun canlı defteri: piyasa saatlerinde 30 dakikada bir güncellenen simüle AL/SAT emirleri, açık pozisyonlar, gün içi özkaynak eğrisi ve strateji kuralları. Gerçek para kullanılmaz; yatırım tavsiyesi değildir.">
<meta property="og:title" content="{title}">
<meta property="og:description" content="BIST 30 simülasyon işlem robotunun şeffaf defteri: pozisyonlar, işlemler, gün içi özkaynak eğrisi. Yatırım tavsiyesi değildir.">
<meta property="og:type" content="website">
<script>(function(){{try{{var t=localStorage.getItem('tema');if(t==='dark'||(!t&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){{document.documentElement.classList.add('dark');}}}}catch(e){{}}}})();</script>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="topbar"><div class="inner">
<div class="brand-row"><a class="brand" href="index.html">BIST 30 Günlük Raporlar</a>
<button type="button" class="theme-btn" id="tema-btn" onclick="temaDegistir()" title="Açık/Koyu tema" aria-label="Tema değiştir">🌙</button></div>
{_SITE_NAV}
</div></header>
{_SITE_TICKER1}
{_SITE_TICKER2}
<main class="wrap">
<div id="robot-canli" class="card" style="padding:12px 18px;margin:0 0 18px;font-size:14px"><span style="color:var(--muted)">🟡 Canlı özete bağlanıyor…</span></div>
{_SITE_SAAT}
<div class="interactive-box">
{_SITE_ARAMA}
</div>
{icerik}
<p style="color:var(--muted);font-size:12px;margin:26px 0 4px">Bu sayfa bağımsız bir simülasyondur: hiçbir gerçek emir gönderilmez, hiçbir gerçek para risk edilmez.
Veri: İş Yatırım (EOD) + TradingView (gün içi, ~15 dk gecikmeli) · Komisyon ve kayma payı varsayımsaldır.
Son güncelleme: {guncelleme} (İstanbul) · Burada yer alan hiçbir içerik yatırım tavsiyesi değildir.</p>
</main>
{_SITE_ALTBAR}
<footer class="footer">Burada yer alan bilgi, yorum ve öneriler bilgilendirme amaçlıdır; yatırım danışmanlığı kapsamında değildir, yatırım tavsiyesi değildir.
Simülasyon sonuçları geçmiş performansın gelecek getiri göstergesi değildir. Veri kaynakları: İş Yatırım, RSS haber akışları &bull; Analiz: yapay zeka (çok-ajanlı sistem)<br><a href="gizlilik.html" style="color:inherit">Gizlilik &amp; KVKK</a></footer>
<script>
function temaDegistir() {{
  var kok = document.documentElement;
  kok.classList.toggle('dark');
  var koyu = kok.classList.contains('dark');
  try {{ localStorage.setItem('tema', koyu ? 'dark' : 'light'); }} catch (e) {{}}
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = koyu ? '☀️' : '🌙';
}}
(function() {{
  var b = document.getElementById('tema-btn');
  if (b) b.textContent = document.documentElement.classList.contains('dark') ? '☀️' : '🌙';
}})();
</script>
<script>
{_ROBOT_CANLI_JS}
</script>
</body></html>"""


# ---------- ANA AKIS ----------
def main():
    simdi = _simdi()
    acik = piyasa_acik_mi(simdi)
    bugun = simdi.strftime("%Y-%m-%d")
    log.info("[ROBOT] Basliyor %s — piyasa %s.", simdi.strftime("%d.%m %H:%M"), "ACIK (canli kontrol)" if acik else "KAPALI")

    seriler = gecmis_oku_ya_da_cek()
    if len(seriler) < 5:
        log.error("[ROBOT] Yeterli veri gelmedi (%d hisse); bu tur islem yapilmadi.", len(seriler))
        return

    durum = durum_oku()
    if durum is None:
        tarih = max((t[-1] for (t, _) in seriler.values() if t), default=bugun)
        durum = {
            "versiyon": 2, "baslangic": tarih, "kapital0": KAPITAL0,
            "nakit": KAPITAL0, "pozisyonlar": {}, "gerceklesen_kz": 0.0,
            "son_bar_tarihi": None, "ozkayit": [[tarih, KAPITAL0]],
        }
        log.info("[ROBOT] Ilk calistirma: durum baslatildi (son bar %s).", tarih)

    # Gunluk sifirlama: yeni gun -> bugun listeleri sifirlanir, dunku kapanisi referans olur
    if durum.get("bugun_tarih") != bugun:
        onceki = None
        for p in durum["ozkayit"]:
            if p[0][:10] < bugun:
                onceki = p
        durum["bugun_tarih"] = bugun
        durum["bugun_alinan"] = []
        durum["bugun_satilan"] = []
        durum["kontrol_sayisi"] = 0
        durum["onceki_kapanis_ozkaynak"] = onceki[1] if onceki else durum.get("kapital0", KAPITAL0)
    durum["kontrol_sayisi"] = durum.get("kontrol_sayisi", 0) + 1

    canli = canli_fiyatlar()
    if canli:
        log.info("[VERI] %d/%d hisse icin canli fiyat alindi.", len(canli), len(HISSELER))

    # Piyasa acikken bugunun barini canli fiyattan ekle (gun ici kesisim/stop yakalansin)
    kullan = seri_hazirla(seriler, canli, bugun, ekle=acik)
    sinyaller = strateji_sinyalleri(kullan)
    if not sinyaller:
        log.error("[ROBOT] Gosterge hesaplanamadi; cikiliyor.")
        return
    for h, s in sinyaller.items():
        if h in canli:
            s["son_fiyat"] = float(canli[h])
    durum["son_bar_tarihi"] = max(s["tarih"] for s in sinyaller.values())

    # Ayni fiyatlar tekrar geldiyse (kapanis sonrasi kosular, tatil) islem/nokta ekleme
    degisim_yok = bool(canli) and canli == durum.get("son_fiyatlar")
    notlar = []
    if degisim_yok:
        log.info("[ROBOT] Fiyatlar onceki kosuyla ayni; emir ve egri noktasi atlandi.")
    else:
        islem_sayisi, notlar = risk_ve_oms(durum, sinyaller)
        if acik or islem_sayisi:
            ozkayit_guncelle(durum, sinyaller)
        if canli:
            durum["son_fiyatlar"] = canli

    durum_yaz(durum)
    dashboard_yaz(durum, sinyaller, notlar)
    log.info("[ROBOT] Bitti: kontrol #%d, pozisyon=%d, ozkaynak=%s TL.",
             durum["kontrol_sayisi"], len(durum["pozisyonlar"]),
             _tr(ozkaynak(durum, {h: s["son_fiyat"] for h, s in sinyaller.items()})))


if __name__ == "__main__":
    main()
