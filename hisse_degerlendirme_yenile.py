# -*- coding: utf-8 -*-
"""Hisse 'Değerlendirmeler' bölümlerini güncel verilerle yeniden üretir.

data/hisse-analiz/<KOD>.json içindeki üç metin (teknik_analiz,
makro_degerlendirme, bilanco_degerlendirmesi) elle hazırlanır ve zamanla
bayatlar (2026-11-01 itibarıyla 30 hissenin tamamı 23.09'da kalmıştı —
kullanıcı bildirimi). Bu araç haftalık workflow'dan çağrılır:

  1) hisse_analiz.bilanco_cek() ile İş Yatırım'dan mali tabloları tazeler
     (fail-open: çekim başarısızsa mevcut tabloyla devam eder);
  2) teknik_analiz ve makro_degerlendirme metinlerini HER koşuda güncel
     verilerle yeniden yazar (bot.llm_call);
  3) bilanco_degerlendirmesi'ni yalnız YENİ DÖNEM geldiğinde yeniler —
     şirket bilanço açıklamaları duyurulan takvimde gelir, haftalık takvimle
     değil yeni dönemle tetiklenir (kullanıcı kararı 2026-11-01); 100 günden
     eski metinler yine de tazelenir (bayatlık sigortası);
  4) temettü/bölünme/birleşme gibi sermaye kararları bu sürümde takip
     EDİLMEZ — KAP akışından takvim üretimi ayrı bir iş (bkz. DURUM).

Kullanım:
    python hisse_degerlendirme_yenile.py                 # 30 hissenin tamamı
    python hisse_degerlendirme_yenile.py --kodlar ASELS,THYAO
    python hisse_degerlendirme_yenile.py --bilanco-atla  # İş Yatırım çekimi yok
    python hisse_degerlendirme_yenile.py --dry           # LLM çağrısı yok
"""
import argparse
import datetime
import json
import os
import sys
import zoneinfo

os.environ.setdefault("OR_API_KEY", "yerel-yenileme")  # import kapısı için

import bot  # noqa: E402  (llm_call)
import hisse_analiz  # noqa: E402  (bilanco_cek)

KLASOR = os.path.join("data", "hisse-analiz")
SIMDI = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul"))
BUGUN = SIMDI.date().isoformat()
BAYATLIK_SIGORTASI_GUN = 100

MIN_UZUNLUK = 350  # karakter — üç bölümün her biri için alt sınır


def _tr_sayi(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _yuzde(x):
    return f"%{_tr_sayi(x)}"


def _teknik_ozeti(satir):
    if not satir:
        return "(teknik verisi yok)"
    return (
        f"Son fiyat: {_tr_sayi(satir.get('son') or 0)} TL; günlük değişim: "
        f"{_yuzde(satir.get('gunluk') or 0)}; 60 günlük getiri: "
        f"{_yuzde(satir.get('deg60') or 0)}; EMA dizilimi kısa/orta/uzun: "
        f"{satir.get('kisa')}/{satir.get('orta')}/{satir.get('uzun')}; Wave Trend: "
        f"{satir.get('wt')}/{satir.get('wt1')}; kanal konumu: "
        f"{_yuzde(satir.get('konum') or 0)}; trend gücü (r): {satir.get('r')}; "
        f"genel puan/özet: {satir.get('puan')} / {satir.get('genel')}"
    )
    # NOT: gunluk ve deg60 teknik dosyasında ZATEN yüzde birimidir (-1,18 =
    # %−1,18); 100 ile çarpılmaz (2026-11-01 canlı testte yakalanan birim hatası).


def _makro_ozeti():
    try:
        m = json.load(open("data/makro.json", encoding="utf-8"))
    except Exception:
        return "(makro verisi yok)"
    satirlar = []
    for g in m.get("gostergeler", []):
        if g.get("ulke") != "Türkiye":
            continue
        deger = g.get("deger")
        if deger is None:
            continue
        satir = f"{g.get('ad')}: {_tr_sayi(deger)}{g.get('birim') or ''}"
        if g.get("onceki") is not None:
            satir += f" (önceki: {_tr_sayi(g['onceki'])}{g.get('birim') or ''})"
        satirlar.append(satir)
    return "; ".join(satirlar[:10]) or "(makro verisi yok)"


def _bilanco_ozeti(tablo):
    if not tablo:
        return "(bilanço tablosu yok)", None, False
    son = tablo[-1]
    donem = son.get("donem")
    satirlar = []
    for d in tablo[-4:]:
        parcalar = [f"dönem {d.get('donem')}"]
        if d.get("satis"):
            parcalar.append(f"satış {_tr_sayi(d['satis'] / 1e9)} mlr TL")
        if d.get("net_kar"):
            parcalar.append(f"net kâr {_tr_sayi(d['net_kar'] / 1e9)} mlr TL")
        if d.get("ozsermaye"):
            parcalar.append(f"özkaynak {_tr_sayi(d['ozsermaye'] / 1e9)} mlr TL")
        satirlar.append(" · ".join(parcalar))
    # Hesaplanan oranlar LLM'e hazır verilir; model sayı HESAPLEMEZ, yalnız
    # verilen oranları metne işler (uydurma sayı sınıfının önlenmesi).
    yillik = ceyrek = None
    try:
        yil, cev = donem.split("/")
        ayni = next((d for d in tablo if d.get("donem") == f"{int(yil) - 1}/{cev}"), None)
        if ayni and ayni.get("net_kar") and son.get("net_kar"):
            yillik = (son["net_kar"] / ayni["net_kar"] - 1) * 100
    except (ValueError, TypeError):
        pass
    if len(tablo) >= 2:
        onceki = tablo[-2]
        if onceki.get("net_kar") and son.get("net_kar"):
            ceyrek = (son["net_kar"] / onceki["net_kar"] - 1) * 100
    if yillik is not None:
        satirlar.append(f"son dönem net kâr YILLIK değişim: {_yuzde(yillik)}")
    if ceyrek is not None:
        satirlar.append(f"son dönem net kâr ÇEYREK değişim: {_yuzde(ceyrek)}")
    return "\n".join(satirlar), yillik, False


def _prompt(kod, ad, tip, teknik, makro, bilanco_metni, yillik):
    return f"""Aşağıdaki GÜNCEL VERİLERLE {ad} ({kod}, sektör: {tip}) için hisse sayfasının "Değerlendirmeler" bölümünün üç parçasını Türkçe yeniden yaz.

KURALLAR (ihlal edilen yanıt reddedilir):
- YALNIZCA aşağıda verilen sayıları kullan; hiçbir sayıyı dışarıdan ekleme, hesaplama yapma, yuvarlama dışında değiştirme.
- Sayı biçimi: Türkçe (ondalık virgül), yüzdeler % işaretiyle; para birimi TL.
- Her bölüm 90-160 kelime; bugünkü durumu anlatır, geçmiş hikâye anlatmaz.
- Yatırım tavsiyesi YOK; nötr analitik dil ("AL/SAT önerisi" değil, "teknik görünüm" dili).
- Bilanço parçasında yalnız son dönemleri ve aşağıda hesaplanmış değişim oranlarını kullan.

GÜNCEL VERİLER
--- Teknik ---
{teknik}

--- Makro (Türkiye) ---
{makro}

--- Mali tablo (son dönemler) ---
{bilanco_metni}

ÇIKTI: YALNIZCA şu anahtarları taşıyan geçerli bir JSON nesnesi, başka hiçbir metin yok:
{{"teknik_analiz": "...", "makro_degerlendirme": "...", "bilanco_degerlendirmesi": "..."}}"""


def _dogrula(icerik):
    """llm_call doğrulama callback'i: JSON parse edilemeyen veya kısa/eksik
    bölümlü yanıtı reddeder (True = kabul)."""
    try:
        metin = icerik.strip()
        if metin.startswith("```"):
            metin = metin.split("```")[1].lstrip("json").strip()
        veri = json.loads(metin)
        anahtarlar = ("teknik_analiz", "makro_degerlendirme", "bilanco_degerlendirmesi")
        if any(not str(veri.get(a) or "").strip() for a in anahtarlar):
            return False
        if any(len(str(veri[a])) < MIN_UZUNLUK for a in anahtarlar):
            return False
        return True
    except Exception:
        return False


def _parse(icerik):
    metin = icerik.strip()
    if metin.startswith("```"):
        metin = metin.split("```")[1].lstrip("json").strip()
    return json.loads(metin)


def _donem(dosya_tarih):
    try:
        return (SIMDI.date() - datetime.date.fromisoformat(dosya_tarih)).days
    except (ValueError, TypeError):
        return 9999


def main():
    ap = argparse.ArgumentParser(description="Hisse değerlendirme metinlerini güncel verilerle yeniler.")
    ap.add_argument("--kodlar", default=None, help="virgülle ayrılmış kod listesi (boşsa 30 hisse)")
    ap.add_argument("--bilanco-atla", action="store_true", help="İş Yatırım mali tablo çekimini atla")
    ap.add_argument("--dry", action="store_true", help="LLM çağrısı yok; yalnız toplanan veriyi özetler")
    args = ap.parse_args()

    teknik_gun = None
    for f in sorted(os.listdir("data/teknik")):
        if f.endswith(".json"):
            teknik_gun = f[:-5]
    if not teknik_gun:
        raise SystemExit("data/teknik altında veri yok.")
    teknik_satirlar = {s["hisse"]: s for s in json.load(open(f"data/teknik/{teknik_gun}.json", encoding="utf-8"))}

    kodlar = ([k.strip().upper() for k in args.kodlar.split(",") if k.strip()]
              if args.kodlar else sorted(s[:-5] for s in os.listdir(KLASOR) if s.endswith(".json")))
    # BIST30'dan çıkarılmış hissenin (or. DSTKF) bayat dosyasını yenileme —
    # yalnız güncel endeks bileşenleri işlenir.
    kodlar = [k for k in kodlar if k in bot.HISSE_ADLARI]

    # İş Yatırım çekiminden ÖNCE her dosyanın son bilanço dönemi anlık
    # görüntülenir; çekimden sonra dönemi değişen hissede yeni şirket
    # açıklaması gelmiş demektir → bilanco_degerlendirmesi tazelenir.
    onceki_donemler = {}
    for kod in kodlar:
        try:
            d = json.load(open(os.path.join(KLASOR, f"{kod}.json"), encoding="utf-8"))
            onceki_donemler[kod] = (d.get("bilanco") or [{}])[-1].get("donem")
        except Exception:
            onceki_donemler[kod] = None

    if not args.bilanco_atla:
        try:
            hisse_analiz.bilanco_cek()
        except Exception as e:
            print(f"[bilanco-cek] başarısız, mevcut tabloyla devam: {e}")

    makro = _makro_ozeti()
    ozet = {"yazilan": [], "bilanco-atlandi": [], "hata": []}
    for kod in kodlar:
        yol = os.path.join(KLASOR, f"{kod}.json")
        if not os.path.isfile(yol):
            ozet["hata"].append(f"{kod}: dosya yok")
            continue
        veri = json.load(open(yol, encoding="utf-8"))
        ad = bot.HISSE_ADLARI.get(kod, kod)
        teknik = _teknik_ozeti(teknik_satirlar.get(kod))
        bilanco_metni, yillik, _ = _bilanco_ozeti(veri.get("bilanco") or [])

        # Yeni dönem = çekim sonrası son dönem, çekim öncesi anlık görüntüden
        # farklı → şirket yeni mali tablo açıklamış demektir.
        yeni_donem = bool(veri.get("bilanco")) and (
            onceki_donemler.get(kod) != (veri.get("bilanco") or [{}])[-1].get("donem")
        )
        bilanco_metni_yazilsin = yeni_donem or _donem(veri.get("guncelleme")) > BAYATLIK_SIGORTASI_GUN
        if args.kodlar:
            # elle alt küme istendiyse bilanço metni de tazelenir (kullanıcı davranışı)
            bilanco_metni_yazilsin = True

        if args.dry:
            print(f"[dry] {kod}: teknik={len(teknik)} kar, makro={len(makro)} kar, "
                  f"bilanco_yenilenecek={bilanco_metni_yazilsin} (yillik={yillik})")
            continue

        prompt = _prompt(kod, ad, veri.get("tip") or "şirket", teknik, makro, bilanco_metni, yillik)
        yanit = bot.llm_call(prompt, max_deneme=4, dogrulama=_dogrula)
        if not yanit:
            ozet["hata"].append(f"{kod}: LLM yanıt yok")
            continue
        try:
            yeni = _parse(yanit)
        except Exception:
            ozet["hata"].append(f"{kod}: JSON parse")
            continue

        veri["teknik_analiz"] = yeni["teknik_analiz"].strip()
        veri["makro_degerlendirme"] = yeni["makro_degerlendirme"].strip()
        if bilanco_metni_yazilsin:
            veri["bilanco_degerlendirmesi"] = yeni["bilanco_degerlendirmesi"].strip()
            veri["bilanco_guncelleme"] = BUGUN
        else:
            ozet["bilanco-atlandi"].append(kod)
        veri["guncelleme"] = BUGUN
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=1)
        ozet["yazilan"].append(kod)
        print(f"[ok] {kod} (bilanço metni: {'yazildi' if bilanco_metni_yazilsin else 'korundu'})")

    print(f"Özet: yazilan={len(ozet['yazilan'])} bilanco-korundu={len(ozet['bilanco-atlandi'])} hata={len(ozet['hata'])}")
    if ozet["hata"]:
        print("Hatalar:", "; ".join(ozet["hata"]))
    if not args.dry and ozet["yazilan"]:
        # Sayfalar güncel verilerle yeniden üretilir; temiz-URL temizliği ve
        # commit iş akışta yapılır.
        import subprocess
        subprocess.run([sys.executable, "hisse_yenile.py"], check=False)


if __name__ == "__main__":
    main()
