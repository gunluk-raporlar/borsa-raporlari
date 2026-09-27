"""BIST 30 Kağıt-Üstünde İşlem Robotu (paper trading) — bot.py'den TAMAMEN BAĞIMSIZ.

Gerçek para kullanmaz; sinyalleri simüle emirlerle işler, sonucu robot.html'de yayınlar.
Kaldırmak için: robot.py, robot.html, data/robot/, .github/workflows/robot.yml silinir.
Başka hiçbir dosyaya bağımlılığı yoktur (ortak CSS/linkler haricinde bot.py'den
hiçbir fonksiyon çağrılmaz).

Katmanlar (her biri ayrı fonksiyon grubu):
  1. VERI     : isyatirimhisse 300 günlük EOD kapanış; yfinance yedek kaynak
  2. STRATEJI : SMA10/SMA30 kesişimi + RSI14 filtresi + SMA100 trend filtresi
  3. RISK     : pozisyon boyutu, maks. pozisyon, stop-loss/hedef, günlük zarar limiti
  4. OMS      : simüle fill (slippage + komisyon), gün bazlı idempotency
  5. MUHASEBE : data/robot/durum.json (durum) + data/robot/islemler.jsonl (append-only)
  6. DASHBOARD: robot.html (site stilini style.css'ten alır, kendi iskeletini basar)

GitHub Actions kronu hafta içi 19:15 (TR) civarı, kapanış barı oluşmuşken çalışır.
"""
import json
import logging
import math
import os
import time
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
SAYFA_YOL = "robot.html"

KAPITAL0 = 100_000.0        # sanal başlangıç sermayesi (TL)
POZISYON_ORAN = 0.12        # özkaynağın en fazla %12'si tek hisseye
MAKS_POZISYON = 8           # aynı anda en fazla 8 farklı hisse
STOP_ORAN = -0.07           # maliyetin %7 altında stop-loss
HEDEF_ORAN = 0.15           # maliyetin %15 üstünde kâr alma
GUNLUK_ZARAR_LIMITI = 0.03  # özkaynak bir günde %3 düşerse yeni alım durur
KOMISYON = 0.0006           # işlem başına %0,06 (BSMV dahil varsayım)
KAYMA = 0.0005              # %0,05 slippage varsayımı (kapanıştan işlem)
MIN_ISLEM_TL = 2_000.0      # bundan küçük işlem açılmaz
VERI_GUN = 300              # SMA100 + tampon için ~200 işlem günü

SITE_URL = "https://borsa-raporlari.pages.dev/"


# ---------- 1. VERI KATMANI ----------
def _serileri_duzelt(ham):
    """{hisse: Series(tarih->kapanis)} sozlugunu {hisse: (tarihler, fiyatlar)} bicimine getirir."""
    out = {}
    for h, s in ham.items():
        if s is None or len(s) == 0:
            continue
        s = pd.Series(s).dropna().astype(float)
        s = s[s > 0]
        if s.empty:
            continue
        tarihler = [str(t)[:10] for t in s.index]
        out[h] = (tarihler, [float(v) for v in s.values])
    return out


def veri_cek():
    """BIST 30 icin gunluk kapanis serilerini doner: {hisse: (tarihler, fiyatlar)}."""
    bugun = datetime.now(TZ)
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
        # kutuphanenin zaman asimina takilan semboller icin tek tek ek deneme
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


# ---------- 2. STRATEJI MOTORU ----------
def _sma(degerler, n):
    if len(degerler) < n:
        return None
    return sum(degerler[-n:]) / n


def _rsi(degerler, n=14):
    """Wilder duzlestirmeli RSI; hesaplanamıyorsa None."""
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

    AL  : son barda SMA10, SMA30'u yukari kesti  VE RSI14 45-75 arasinda
          VE fiyat SMA100'un uzerinde (trend filtresi).
    SAT : son barda SMA10, SMA30'u asagi kesti (stop/hedef OMS katmaninda da tetiklenir).
    """
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
        # Golden cross : onceki bar s10<=s30 iken son bar s10>s30
        # Death  cross : onceki bar s10>=s30 iken son bar s10<s30
        # (Onceki bar kosulu olmadan 'SMA10 zaten asagida' durumu her gun
        # SAT uretir ve sinyal gunlugu gurultuye bogulur.)
        onceki_ok = s10[0] is not None and s30[0] is not None and s10[1] is not None and s30[1] is not None
        alt_kesti = onceki_ok and s10[0] <= s30[0]
        usti_cikti = onceki_ok and s10[1] > s30[1]
        asagi_kesti = onceki_ok and s10[0] >= s30[0] and s10[1] < s30[1]
        sinyal, gerekce = "BEKLE", ""
        if alt_kesti and usti_cikti:
            if rsi is None or not (45.0 <= rsi <= 75.0) or f[-1] <= sma100:
                neden = []
                if rsi is not None and rsi > 75:
                    neden.append(f"RSI {rsi:.0f} asiri alim bolgesinde")
                elif rsi is not None and rsi < 45:
                    neden.append(f"RSI {rsi:.0f} zayif")
                if f[-1] <= sma100:
                    neden.append("fiyat SMA100 altinda")
                sinyal, gerekce = "BEKLE", "kesisim var ama: " + ", ".join(neden)
            else:
                sinyal = "AL"
                gerekce = f"SMA10, SMA30'u yukari kesti · RSI {rsi:.0f} · fiyat SMA100 uzerinde"
        elif asagi_kesti:
            sinyal = "SAT"
            gerekce = "SMA10, SMA30'u asagi kesti"
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


# ---------- 3. RISK + 4. OMS ----------
def risk_ve_oms(durum, sinyaller):
    """Yeni barda sinyalleri risk kurallarindan gecirip simule emirlere cevirir.

    Donus: (yeni_islem_sayisi, notlar) — notlar dashboard'da sinyal panosunu besler.
    """
    fiyatlar = {h: s["son_fiyat"] for h, s in sinyaller.items()}
    tarih = max(s["tarih"] for s in sinyaller.values())

    # Idempotency: ayni bar bir kez islenir (tekrar calistirmalar yeni emir uretmez)
    if durum.get("son_bar_tarihi") == tarih:
        return 0, ["Bu bar onceki kosuda islendi; yeni emir acilmadi."]

    onceki_ozkaynak = durum["ozkayit"][-1][1] if durum["ozkayit"] else KAPITAL0
    oz = ozkaynak(durum, fiyatlar)
    notlar = []
    islem_sayisi = 0

    # --- RISK: gunluk zarar limiti — yeni alimlar duraklatilir (satislar her zaman serbest)
    duraklatildi = oz < onceki_ozkaynak * (1.0 - GUNLUK_ZARAR_LIMITI)
    if duraklatildi:
        dusus = (1.0 - oz / onceki_ozkaynak) * 100.0
        notlar.append(f"Günlük zarar limiti: özkaynak {dusus:.1f}% düştü — bugün yeni ALIM yapılmadı.")

    # --- SATISLAR (once satar, nakit acilir): kesisim asagisi / stop / hedef
    for h, p in list(durum["pozisyonlar"].items()):
        s = sinyaller.get(h)
        if s is None:
            continue
        fiyat = s["son_fiyat"]
        stop_tetik = fiyat <= p["stop"]
        hedef_tetik = fiyat >= p["hedef"]
        if s["sinyal"] == "SAT" or stop_tetik or hedef_tetik:
            if stop_tetik and s["sinyal"] != "SAT":
                gerekce = f"stop-loss: {p['stop']:.2f} seviyesine dokundu (%{STOP_ORAN * 100:.0f} kural)"
            elif hedef_tetik and s["sinyal"] != "SAT":
                gerekce = f"hedef fiyat: {p['hedef']:.2f} seviyesine ulasti (+%{HEDEF_ORAN * 100:.0f} kural)"
            else:
                gerekce = s["gerekce"]
            net = fiyat * (1.0 - KAYMA) * (1.0 - KOMISYON)
            tutar = net * p["lot"]
            kz = (net - p["maliyet"]) * p["lot"]
            durum["nakit"] += tutar
            del durum["pozisyonlar"][h]
            islem_sayisi += 1
            durum["gerceklesen_kz"] = durum.get("gerceklesen_kz", 0.0) + kz
            islem_logla({
                "tarih": tarih, "hisse": h, "yon": "SAT", "lot": p["lot"],
                "fiyat": round(fiyat, 2), "tutar": round(tutar, 2),
                "kz": round(kz, 2), "gerekce": gerekce,
                "giris_tarihi": p["giris"], "nakit_sonra": round(durum["nakit"], 2),
            })
            log.info("[SAT] %s lot=%d fiyat=%.2f K/Z=%+.2f (%s)", h, p["lot"], fiyat, kz, gerekce)

    # --- ALIMLAR: slot + nakit + boyut kurallari; adaylar en dusuk RSI'dan (asiri isinmamis) siralanir
    if not duraklatildi:
        adaylar = sorted(
            [(h, s) for h, s in sinyaller.items()
             if s["sinyal"] == "AL" and h not in durum["pozisyonlar"]],
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
            brut = lot * fiyat
            maliyet = fiyat * (1.0 + KAYMA) * (1.0 + KOMISYON)
            toplam = maliyet * lot
            if toplam > durum["nakit"]:
                lot -= 1
                if lot < 1:
                    notlar.append(f"{h}: AL sinyali vardı ama komisyon sonrası nakit yetmedi.")
                    continue
                maliyet = fiyat * (1.0 + KAYMA) * (1.0 + KOMISYON)
                toplam = maliyet * lot
                brut = lot * fiyat
            durum["nakit"] -= toplam
            durum["pozisyonlar"][h] = {
                "lot": lot, "maliyet": round(maliyet, 4), "giris": tarih,
                "stop": round(maliyet * (1.0 + STOP_ORAN), 2),
                "hedef": round(maliyet * (1.0 + HEDEF_ORAN), 2),
            }
            islem_sayisi += 1
            islem_logla({
                "tarih": tarih, "hisse": h, "yon": "AL", "lot": lot,
                "fiyat": round(fiyat, 2), "tutar": round(toplam, 2),
                "kz": None, "gerekce": s["gerekce"],
                "giris_tarihi": tarih, "nakit_sonra": round(durum["nakit"], 2),
            })
            log.info("[AL] %s lot=%d fiyat=%.2f (~%.0f TL) — %s", h, lot, fiyat, toplam, s["gerekce"])

    durum["son_bar_tarihi"] = tarih
    return islem_sayisi, notlar


def ozkayit_guncelle(durum, sinyaller):
    """Bugunun (son barin) ozkaynak degerini egrisine yazar; ayni tarihte uzerine yazar."""
    fiyatlar = {h: s["son_fiyat"] for h, s in sinyaller.items()}
    if not sinyaller:
        return
    tarih = max(s["tarih"] for s in sinyaller.values())
    deger = round(ozkaynak(durum, fiyatlar), 2)
    if durum["ozkayit"] and durum["ozkayit"][-1][0] == tarih:
        durum["ozkayit"][-1][1] = deger
    elif durum["ozkayit"] and durum["ozkayit"][-1][0] > tarih:
        return  # eski tarihli veri; egriyi geriye sarma
    else:
        durum["ozkayit"].append([tarih, deger])


# ---------- 6. DASHBOARD (robot.html) ----------
def _tr(x, hane=2):
    """Turkce sayi bicimi: 1.234,56"""
    return f"{x:,.{hane}f}".replace(",", "\u00A0").replace(".", ",").replace("\u00A0", ".")


def _egri_svg(ozkayit):
    if len(ozkayit) < 2:
        return ("<svg viewBox='0 0 720 160' style='width:100%;height:auto' role='img'>"
                "<text x='360' y='85' text-anchor='middle' fill='#64748b' font-size='13'>"
                "Eğri için ikinci işlem günü bekleniyor…</text></svg>")
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
    son = degerler[-1]
    ilk = degerler[0]
    renk = "#047857" if son >= ilk else "#b91c1c"
    return f"""<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto" role="img" aria-label="Özkaynak eğrisi">
<polyline points="{xy(0, ilk)},{xy(len(degerler) - 1, son)}" fill="none" stroke="#94a3b8" stroke-width="1" stroke-dasharray="4 4"/>
<polyline points="{noktalar}" fill="none" stroke="{renk}" stroke-width="2.5" stroke-linejoin="round"/>
<text x="{P}" y="{H - 2}" font-size="10" fill="#64748b">{ozkayit[0][0]}</text>
<text x="{W - P}" y="{H - 2}" font-size="10" fill="#64748b" text-anchor="end">{ozkayit[-1][0]}</text>
<text x="{W - P}" y="{P + 2}" font-size="11" fill="{renk}" text-anchor="end">{_tr(son)} TL</text>
<text x="{P}" y="{P + 2}" font-size="11" fill="#64748b">{_tr(ilk)} TL (başlangıç)</text>
</svg>"""


def dashboard_yaz(durum, sinyaller, notlar):
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
    islemler = islemleri_oku()
    fiyatlar = {h: s["son_fiyat"] for h, s in sinyaller.items()}
    oz = ozkaynak(durum, fiyatlar)
    toplam_kz = oz - KAPITAL0
    getiri = toplam_kz / KAPITAL0 * 100.0
    ger_kz = durum.get("gerceklesen_kz", 0.0)

    satislar = [i for i in islemler if i["yon"] == "SAT"]
    kazananlar = [i for i in satislar if i.get("kz", 0) > 0]
    kazanma_orani = (len(kazananlar) / len(satislar) * 100.0) if satislar else 0.0

    # KPI kartlari
    def stat(etiket, deger, cls=""):
        return (f"<div class='stat' style='background:var(--card);border:1px solid var(--line);"
                f"border-radius:10px;padding:12px 14px'><div class='label'>{etiket}</div>"
                f"<div style='font-size:21px;font-weight:700' class='{cls}'>{deger}</div></div>")

    kpi = "<div class='grid' style='grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:18px 0'>" + "".join([
        stat("Toplam Değer", _tr(oz) + " ₺"),
        stat("Toplam Getiri", f"{_tr(getiri)}%", "pos" if toplam_kz >= 0 else "neg"),
        stat("Nakit", _tr(durum["nakit"]) + " ₺"),
        stat("Açık Pozisyon", str(len(durum["pozisyonlar"])) + f" / {MAKS_POZISYON}"),
        stat("Gerçekleşen K/Z", _tr(ger_kz) + " ₺", "pos" if ger_kz >= 0 else "neg"),
        stat("Kazanma Oranı", f"{_tr(kazanma_orani, 0)}%"),
    ]) + "</div>"

    # Pozisyon tablosu
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
    pozisyon_bolumu = ("""<h2 class="section-title">Açık Pozisyonlar</h2><div class="card" style="padding:8px 24px 16px">
<div style="overflow-x:auto"><table><tr><th>Hisse</th><th>Lot</th><th>Maliyet</th><th>Son</th><th>Değer ₺</th>
<th>K/Z ₺</th><th>K/Z %</th><th>Stop</th><th>Hedef</th><th>Giriş</th></tr>""" + poz_satirlar +
                       ("""</table></div>""" if poz_satirlar else "</table></div><p style='color:var(--muted)'>Henüz açık pozisyon yok — robot AL sinyali bekliyor.</p>") + "</div>")

    # Son islemler
    islem_satirlar = ""
    for i in islemler[-25:][::-1]:
        if i["yon"] == "SAT":
            kz = i.get("kz")
            kz_hucre = (f"<td class='{'pos' if kz >= 0 else 'neg'}'>{_tr(kz)}</td>" if kz is not None else "<td>—</td>")
        else:
            kz_hucre = "<td>—</td>"
        islem_satirlar += (f"<tr><td>{i['tarih']}</td>"
                           f"<td><b>{i['hisse']}</b></td>"
                           f"<td class='{'pos' if i['yon'] == 'AL' else 'neg'}'>{i['yon']}</td>"
                           f"<td>{i['lot']}</td><td>{_tr(i['fiyat'])}</td><td>{_tr(i['tutar'])}</td>"
                           f"{kz_hucre}<td style='max-width:340px'>{i['gerekce']}</td></tr>")
    islem_bolumu = ("""<h2 class="section-title">Son İşlemler</h2><div class="card" style="padding:8px 24px 16px">
<div style="overflow-x:auto"><table><tr><th>Tarih</th><th>Hisse</th><th>Yön</th><th>Lot</th><th>Fiyat</th>
<th>Tutar ₺</th><th>K/Z ₺</th><th>Gerekçe</th></tr>""" + islem_satirlar +
                    ("</table></div>" if islem_satirlar else "</table></div><p style='color:var(--muted)'>Henüz işlem yok.</p>") + "</div>")

    # Sinyal panosu: bugun ne oldu, ne bekliyor
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
    if notlar:
        for n in notlar:
            pano += f"<div class='pano-hucre'>ℹ️ {n}</div>"
    if not pano:
        pano = "<div class='pano-hucre'>Bugün yeni AL/SAT sinyali üretmedi — robot bekliyor.</div>"
    pano_bolumu = ("<h2 class='section-title'>Sinyal Panosu (son bar: "
                   + (durum.get("son_bar_tarihi") or "—") + ")</h2><div class='grid' style='gap:10px'>" + pano + "</div>")

    # Strateji kurallari + istatistik
    istatistik = (f"<div class='grid-iki'><div class='card'><div class='pano-baslik'>Strateji Kuralları</div>"
                  f"<ul style='margin:0;padding-left:18px;color:var(--muted);font-size:13.5px;line-height:1.7'>"
                  f"<li><b>AL:</b> SMA10, SMA30'u yukarı keser + RSI14 45–75 bandında + fiyat SMA100 üzerinde.</li>"
                  f"<li><b>SAT:</b> SMA10, SMA30'u aşağı keser, ya da maliyetin <b>%{abs(int(STOP_ORAN * 100))}</b> altına düşer (stop), "
                  f"ya da <b>%{int(HEDEF_ORAN * 100)}</b> yukarısına çıkar (hedef).</li>"
                  f"<li><b>Boyut:</b> özkaynağın en fazla %{int(POZISYON_ORAN * 100)}'i tek hisseye, en fazla {MAKS_POZISYON} pozisyon.</li>"
                  f"<li><b>Günlük fren:</b> özkaynak bir günde %{int(GUNLUK_ZARAR_LIMITI * 100)} düşerse yeni alım o gün durdurulur.</li>"
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
<p>BIST 30 hisseleri üzerinde <b>kağıt-üstünde işlem</b> (paper trading) yapan otomatik robotun canlı defteri:
stratejisi kendi ürettiği AL/SAT sinyallerini <u>gerçek para kullanmadan</u>, kapanış fiyatlarından
komisyon ve kayma payıyla simüle eder; tüm emirler, pozisyonlar ve özkaynak eğrisi burada şeffaf biçimde yayımlanır.
<strong>Yatırım tavsiyesi değildir.</strong> Son güncelleme: {now} (İstanbul).</p>
</div>
{kpi}
<h2 class="section-title">Özkaynak Eğrisi</h2>
<div class="card">{_egri_svg(durum["ozkayit"])}</div>
{pozisyon_bolumu}
{islem_bolumu}
{pano_bolumu}
{istatistik}"""

    # Basit rol: aktif menü vurgusu sabit 'robot'
    html = _iskelet("İşlem Robotu (Simülasyon)", icerik, now)
    with open(SAYFA_YOL, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[DASHBOARD] robot.html yazildi (ozkaynak=%s TL, pozisyon=%d)", _tr(oz), len(durum["pozisyonlar"]))


def _iskelet(title, icerik, guncelleme):
    """Sayfa iskeleti — sitenin style.css'ini kullanan BAGIMSIZ kopya (bot.py'ye dokunmaz).

    KALDIRMA NOTU: bu fonksiyon tek basina robot.html'i uretir; baska sayfayi etkilemez."""
    nav = """<nav><a href="index.html">Raporlar</a><a href="teknik-analiz.html">Teknik Tarama</a><a href="sinyal-karnesi.html">Sinyal Karnesi</a><a href="portfolio.html">Deneme Portföyü</a><a href="robot.html" class="active">İşlem Robotu</a></nav>"""
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="BIST 30 kağıt-üstünde işlem robotunun canlı defteri: simüle AL/SAT emirleri, açık pozisyonlar, özkaynak eğrisi ve strateji kuralları. Gerçek para kullanılmaz; yatırım tavsiyesi değildir.">
<meta property="og:title" content="{title}">
<meta property="og:description" content="BIST 30 simülasyon işlem robotunun şeffaf defteri: pozisyonlar, işlemler, özkaynak eğrisi. Yatırım tavsiyesi değildir.">
<meta property="og:type" content="website">
<script>(function(){{try{{var t=localStorage.getItem('tema');if(t==='dark'||(!t&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){{document.documentElement.classList.add('dark');}}}}catch(e){{}}}})();</script>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="topbar"><div class="inner">
<div class="brand-row"><a class="brand" href="index.html">BIST 30 Günlük Raporlar</a>
<button type="button" class="theme-btn" id="tema-btn" onclick="temaDegistir()" title="Açık/Koyu tema" aria-label="Tema değiştir">🌙</button></div>
{nav}
</div></header>
<main class="wrap">
{icerik}
<p style="color:var(--muted);font-size:12px;margin:26px 0 4px">Bu sayfa bağımsız bir simülasyondur: hiçbir gerçek emir gönderilmez, hiçbir gerçek para risk edilmez.
Veri: İş Yatırım (EOD) / Yahoo Finance yedek · Komisyon ve kayma payı varsayımsaldır.
Son güncelleme: {guncelleme} (İstanbul) · Burada yer alan hiçbir içerik yatırım tavsiyesi değildir.</p>
</main>
<footer class="footer">Burada yer alan bilgi, yorum ve öneriler bilgilendirme amaçlıdır; yatırım danışmanlığı kapsamında değildir, yatırım tavsiyesi değildir.
Simülasyon sonuçları geçmiş performansın gelecek getiri göstergesi değildir. <a href="gizlilik.html" style="color:inherit">Gizlilik &amp; KVKK</a></footer>
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
</body></html>"""


# ---------- ANA AKIS ----------
def main():
    simdi = datetime.now(TZ)
    log.info("[ROBOT] Basliyor (%s) — paper trading, hicbir gercek emir gonderilmez.", simdi.strftime("%Y-%m-%d %H:%M"))

    seriler = veri_cek()
    if len(seriler) < 5:
        log.error("[ROBOT] Yeterli veri gelmedi (%d hisse); bu tur islem yapilmadi.", len(seriler))
        return

    sinyaller = strateji_sinyalleri(seriler)
    if not sinyaller:
        log.error("[ROBOT] Hicbir hisse icin gosterge hesaplanamadi; cikiliyor.")
        return

    durum = durum_oku()
    if durum is None:
        tarih = max(s["tarih"] for s in sinyaller.values())
        durum = {
            "versiyon": 1, "baslangic": tarih, "kapital0": KAPITAL0,
            "nakit": KAPITAL0, "pozisyonlar": {},
            "gerceklesen_kz": 0.0, "son_bar_tarihi": None,
            "ozkayit": [[tarih, KAPITAL0]],
        }
        log.info("[ROBOT] Ilk calistirma: durum baslatildi (baslangic bari %s).", tarih)

    # Bir onceki barin ozkaynagiyla kiyaslama yapabilmek icin: risk katmani
    # gunluk zarar limitini ozkayit'taki son degere gore denetler.
    islem_sayisi, notlar = risk_ve_oms(durum, sinyaller)
    ozkayit_guncelle(durum, sinyaller)
    durum_yaz(durum)
    dashboard_yaz(durum, sinyaller, notlar)

    log.info("[ROBOT] Bitti: %d emir islendi, ozkaynak=%s TL, pozisyon=%d.",
             islem_sayisi, _tr(ozkaynak(durum, {h: s["son_fiyat"] for h, s in sinyaller.items()})),
             len(durum["pozisyonlar"]))


if __name__ == "__main__":
    main()
