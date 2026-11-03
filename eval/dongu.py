# -*- coding: utf-8 -*-
"""Arama kalitesi değerlendirme döngüsü (LangGraph StateGraph).

Ne yapar?
  Kullanıcının GERÇEK arama zincirini (borsa-raporlari.pages.dev/api/ara
  vekilini) ölçer: tohum sorgu setini üç kolda çalıştırır, sonuçları bir LLM
  yargıçla (Workers AI) 0-2 puanla puanlar, nDCG@8 + MRR hesaplar, geçmiş
  koşularla karşılaştırır ve SADECE güvenli parametreleri arama_ayar
  tablosuna yazar. Kod değişikliği ASLA otomatik uygulanmaz; rapor/öneri
  olarak kalır.

Kollar (aynı sorgu, aynı koşu — A/B karışımı yok):
  normal  : yayındaki davranış (arama_ayar'daki genişletme/rerank ayarı)
  ham     : genişletme + rerank YOK, saf vektör araması (?ham=1)
  rerank  : genişletme açık + zorunlu yeniden sıralama (?rerank=1)

Otomatik ayar disiplini (mimar şartları, 2026-11-03):
  - Bir koşuda EN FAZLA BİR parametre değişir.
  - Değişiklik için iki ARDIŞIK koşuda aynı yönde kanıt gerekir (histerezis).
  - min_skor ∈ [0.15, 0.45], taze_boost ∈ [0, 0.15] aralıkları dışına çıkılmaz.
  - Her yazma rapora "eski → yeni" olarak kaydedilir.

Kullanım:
  python eval/dongu.py            # tam koşu (ağ + yargıç + rapor + ayar yazma)
  python eval/dongu.py --kuru     # ağ YOK: sahte veriyle graf + metrik doğrulaması

Ortam değişkenleri:
  CF_ACCOUNT_ID, CF_API_KEY           yargıç modeli (Workers AI; mevcut secret)
  SUPABASE_URL, SUPABASE_SERVICE_KEY  arama_ayar yazımı (mevcut secret)
  ARA_URL                             varsayılan https://borsa-raporlari.pages.dev/api/ara
"""
import argparse
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, TypedDict

import requests

# ---------- Sabitler ----------

ARA_URL = os.environ.get("ARA_URL", "https://borsa-raporlari.pages.dev/api/ara")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_KEY = os.environ.get("CF_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
YARGIC_MODEL = os.environ.get("YARGIC_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")

KOL_KOLLAR = ["normal", "ham", "rerank"]
NDCG_ESIK = 0.50   # bu değerin altı "kötü koşu" sayılır (rapor işareti)
FARK_ESIK = 0.03   # A/B kol farkı kanıt eşiği
ARALIK = {"min_skor": (0.15, 0.45), "taze_boost": (0.0, 0.15)}
AYAR_YAZILABILIR = {"min_skor", "taze_boost", "rerank_top_k", "genisletme"}

DOSYA_KOK = os.path.dirname(os.path.abspath(__file__))
DATA_KOK = os.path.join(DOSYA_KOK, "..", "data", "eval")
GECMIS_YOL = os.path.join(DATA_KOK, "gecmis.json")


def _bugun():
    return datetime.now().strftime("%Y-%m-%d")


# ---------- Yardımcılar ----------

def vekil_ara(sorgu, kol, zaman_asimi=25):
    """Vekili GET ile çağırır; sonuç listesini sadeleştirip döndürür."""
    param = []
    if kol == "ham":
        param.append("ham=1")
    if kol == "rerank":
        param.append("rerank=1")
    url = f"{ARA_URL}?q={requests.utils.quote(sorgu)}" + ("&" + "&".join(param) if param else "")
    r = requests.get(url, timeout=zaman_asimi)
    r.raise_for_status()
    sonuc = r.json().get("sonuc") or []
    return [
        {
            "baslik": s.get("baslik") or "",
            "kaynak": s.get("kaynak") or "",
            "tarih": s.get("tarih") or "",
            "icerik": (s.get("icerik") or "")[:400],
        }
        for s in sonuc[:8]
    ]


def cf_yargic(sorgu, icerik):
    """Workers AI yargıcı: sonucun sorgu ile ilgisini 0/1/2 puanlar."""
    prompt = (
        "Sen bir arama kalitesi değerlendiricisisin. Kullanıcının arama sorgusu:\n"
        f'"{sorgu}"\n\nBir arama sonucu parçası:\n"{icerik[:380]}"\n\n'
        "Bu sonucun sorguya ilgisi var mı? Yalnızca şu biçimde JSON döndür: "
        '{"puan": 0} (0 = ilgisiz, 1 = kısmen ilgili, 2 = doğrudan ilgili)'
    )
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{YARGIC_MODEL}",
        headers={"Authorization": f"Bearer {CF_API_KEY}"},
        json={"messages": [{"role": "user", "content": prompt}],
              "max_tokens": 40, "temperature": 0},
        timeout=60,
    )
    r.raise_for_status()
    j = r.json()
    metin = (j.get("result") or {}).get("response") if isinstance(j.get("result"), dict) else j.get("response")
    m = re.search(r'"puan"\s*:\s*([0-2])', metin or "")
    return int(m.group(1)) if m else None


def ndcg8(puanlar):
    """puanlar: sıralı sonuç puanları (0/1/2). Kazanç {0:0, 1:1, 2:3}."""
    kazanc = {0: 0.0, 1: 1.0, 2: 3.0}
    d = sum(kazanc.get(p, 0.0) / math.log2(i + 2) for i, p in enumerate(puanlar))
    ideal = sorted(puanlar, reverse=True)
    idcg = sum(kazanc.get(p, 0.0) / math.log2(i + 2) for i, p in enumerate(ideal))
    return d / idcg if idcg > 0 else None


def mrr(puanlar):
    for i, p in enumerate(puanlar):
        if p == 2:
            return 1.0 / (i + 1)
    return 0.0 if puanlar and all(p is not None for p in puanlar) else None


def _puan_temiz(liste):
    return [p if p is not None else 0 for p in liste]


def _ik_ardisik(gecmis, kosul):
    """Son iki koşunun İKİSİ de koşulu sağlıyor mu (geçmiş, bu koşu hariç)."""
    if len(gecmis) < 2:
        return False
    return kosul(gecmis[-2]) and kosul(gecmis[-1])


def _sinirli(anahtar, deger):
    alt, ust = ARALIK.get(anahtar, (-1e9, 1e9))
    return round(min(ust, max(alt, deger)), 4)


# ---------- LangGraph düğümleri (saf fonksiyon; durum dict) ----------

def sorgu_sec(durum):
    with open(os.path.join(DOSYA_KOK, "sorgular.json"), encoding="utf-8") as fh:
        seti = json.load(fh)
    return {"sorgular": [s["sorgu"] for s in seti["sorgular"]],
            "ipuclari": {s["sorgu"]: s.get("ipucu") for s in seti["sorgular"]}}


def arama(durum):
    if durum.get("kuru"):
        return _kuru_arama(durum)
    kol_sonuc = {kol: {} for kol in KOL_KOLLAR}
    bos = {kol: 0 for kol in KOL_KOLLAR}
    for sorgu in durum["sorgular"]:
        for kol in KOL_KOLLAR:
            try:
                liste = vekil_ara(sorgu, kol)
            except Exception as e:
                print(f"  [hata] arama {kol}/{sorgu}: {type(e).__name__}: {e}")
                liste = []
            kol_sonuc[kol][sorgu] = liste
            if not liste:
                bos[kol] += 1
    return {"kol_sonuc": kol_sonuc, "bos": bos}


def yargic(durum):
    if durum.get("kuru"):
        return _kuru_yargic(durum)
    if not (CF_ACCOUNT_ID and CF_API_KEY):
        print("[uyarı] CF anahtarları yok — yargıç atlandı, puanlama yapılamaz.")
        return {"yargic_yok": True, "yargic_hata": 0, "puanlar": {kol: {} for kol in KOL_KOLLAR}}
    puanlar = {kol: {} for kol in KOL_KOLLAR}
    isler = []
    for kol in KOL_KOLLAR:
        for sorgu, liste in durum["kol_sonuc"][kol].items():
            for sira, s in enumerate(liste):
                isler.append((kol, sorgu, sira, s))
    print(f"  yargıç: {len(isler)} parça puanlanıyor ({YARGIC_MODEL})")

    def puanla(is_):
        kol, sorgu, sira, s = is_
        try:
            return kol, sorgu, sira, cf_yargic(sorgu, s["icerik"])
        except Exception as e:
            print(f"  [hata] yargıç {kol}/{sorgu}/{sira}: {type(e).__name__}")
            return kol, sorgu, sira, None

    with ThreadPoolExecutor(max_workers=4) as havuz:
        for kol, sorgu, sira, puan in havuz.map(puanla, isler):
            puanlar[kol].setdefault(sorgu, {})[sira] = puan
    hatali = sum(1 for kol in KOL_KOLLAR for d in puanlar[kol].values() for p in d.values() if p is None)
    return {"puanlar": puanlar, "yargic_hata": hatali, "yargic_yok": False}


def metrik(durum):
    metrikler = {}
    for kol in KOL_KOLLAR:
        nd_list, mrr_list = [], []
        for sorgu in durum["sorgular"]:
            adet = len(durum["kol_sonuc"][kol].get(sorgu) or [])
            if not adet:
                continue
            d = durum.get("puanlar", {}).get(kol, {}).get(sorgu, {})
            sirali = _puan_temiz([d.get(i) for i in range(adet)])
            n = ndcg8(sirali)
            m = mrr(sirali)
            if n is not None:
                nd_list.append(n)
            if m is not None:
                mrr_list.append(m)
        metrikler[kol] = {
            "ndcg": round(sum(nd_list) / len(nd_list), 4) if nd_list else None,
            "mrr": round(sum(mrr_list) / len(mrr_list), 4) if mrr_list else None,
        }
    return {"metrikler": metrikler}


def karar(durum):
    gecmis = []
    if os.path.exists(GECMIS_YOL):
        try:
            with open(GECMIS_YOL, encoding="utf-8") as fh:
                gecmis = json.load(fh).get("kosular", [])
        except Exception:
            gecmis = []
    m = durum["metrikler"]
    karar, degisen = "olcum", None
    n = lambda kol: m.get(kol, {}).get("ndcg")

    if m.get("normal", {}).get("ndcg") is None or durum.get("kuru"):
        karar = "olcum"
    elif len(gecmis) < 2:
        karar = "baseline"
    elif durum["bos"]["normal"] > 0 and _ik_ardisik(gecmis, lambda k: k.get("bos_normal", 0) > 0):
        # 1. öncelik: boş dönen sorgu varsa eşiği düşür
        gecerli = gecmis[-1].get("ayarlar", {}).get("min_skor", 0.30)
        yeni = _sinirli("min_skor", gecerli - 0.05)
        if yeni != _sinirli("min_skor", gecerli):
            karar, degisen = "ayar", {"anahtar": "min_skor", "eski": gecerli, "yeni": yeni}
    elif n("ham") is not None and n("normal") is not None:
        # 2/3. öncelik: kol karşılaştırmaları (aynı koşuda A/B)
        fark_ham = n("ham") - n("normal")
        fark_rerank = (n("rerank") - n("normal")) if n("rerank") is not None else 0.0
        onceki = gecmis[-1].get("ayarlar", {})
        if fark_ham >= FARK_ESIK and _ik_ardisik(
                gecmis, lambda k: (k.get("ndcg_ham") or 0) - (k.get("ndcg_normal") or 0) >= FARK_ESIK):
            if onceki.get("genisletme", 1) == 1:
                karar, degisen = "ayar", {"anahtar": "genisletme", "eski": 1, "yeni": 0}
        elif fark_ham <= -FARK_ESIK and _ik_ardisik(
                gecmis, lambda k: (k.get("ndcg_ham") or 0) - (k.get("ndcg_normal") or 0) <= -FARK_ESIK):
            if onceki.get("genisletme", 1) == 0:
                karar, degisen = "ayar", {"anahtar": "genisletme", "eski": 0, "yeni": 1}
        elif fark_rerank >= FARK_ESIK and _ik_ardisik(
                gecmis, lambda k: (k.get("ndcg_rerank") or 0) - (k.get("ndcg_normal") or 0) >= FARK_ESIK):
            if onceki.get("rerank_top_k", 0) == 0:
                karar, degisen = "ayar", {"anahtar": "rerank_top_k", "eski": 0, "yeni": 8}
        elif fark_rerank <= -FARK_ESIK and _ik_ardisik(
                gecmis, lambda k: (k.get("ndcg_rerank") or 0) - (k.get("ndcg_normal") or 0) <= -FARK_ESIK):
            if onceki.get("rerank_top_k", 0) == 8:
                karar, degisen = "ayar", {"anahtar": "rerank_top_k", "eski": 8, "yeni": 0}

    mevcut_ayar = gecmis[-1].get("ayarlar", {}) if gecmis else {}
    return {"karar": karar, "degisen": degisen, "gecmis": gecmis, "mevcut_ayar": mevcut_ayar}


def yaz(durum):
    if durum.get("kuru"):
        print("KURU KOŞU — dosya yazılmadı, ayar değiştirilmedi.")
        return {}
    os.makedirs(DATA_KOK, exist_ok=True)
    tarih = _bugun()
    simdi = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+03:00")
    kayit = {"tarih": tarih, "sorgu_sayisi": len(durum["sorgular"]),
             "metrikler": durum["metrikler"], "bos": durum["bos"],
             "yargic_hata": durum.get("yargic_hata", 0),
             "yargic_yok": durum.get("yargic_yok", False),
             "karar": durum["karar"], "degisen": durum["degisen"]}
    with open(os.path.join(DATA_KOK, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(kayit, fh, ensure_ascii=False, indent=1)

    # Ayar yazımı: yalnız güvenli anahtarlar; secret yoksa yalnız öneri kalır
    yazildi = False
    d = durum.get("degisen")
    if durum["karar"] == "ayar" and d and d["anahtar"] in AYAR_YAZILABILIR:
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/arama_ayar",
                    params={"on_conflict": "anahtar"},
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                             "Content-Type": "application/json",
                             "Prefer": "resolution=merge-duplicates"},
                    json=[{"anahtar": d["anahtar"], "deger": d["yeni"], "guncelleme": simdi}],
                    timeout=30,
                )
                r.raise_for_status()
                yazildi = True
                print(f"[ayar] {d['anahtar']}: {d['eski']} → {d['yeni']} (arama_ayar'a yazıldı)")
            except Exception as e:
                print(f"[hata] arama_ayar yazılamadı: {type(e).__name__}: {e}")
        else:
            print(f"[uyarı] SUPABASE secret yok — öneri raporda kaldı: "
                  f"{d['anahtar']} {d['eski']} → {d['yeni']}")

    # Geçmiş zinciri (son 30 koşu)
    ayarlar = dict(durum.get("mevcut_ayar") or {})
    if durum["karar"] == "ayar" and d:
        ayarlar[d["anahtar"]] = d["yeni"]
    kosu = {
        "tarih": tarih,
        "ndcg_normal": durum["metrikler"]["normal"]["ndcg"],
        "ndcg_ham": durum["metrikler"]["ham"]["ndcg"],
        "ndcg_rerank": durum["metrikler"]["rerank"]["ndcg"],
        "mrr_normal": durum["metrikler"]["normal"]["mrr"],
        "bos_normal": durum["bos"]["normal"],
        "karar": durum["karar"],
        "ayarlar": ayarlar,
    }
    with open(GECMIS_YOL, "w", encoding="utf-8") as fh:
        json.dump({"kosular": (durum["gecmis"] + [kosu])[-30:]}, fh, ensure_ascii=False, indent=1)

    # İnsan-okur rapor
    m = durum["metrikler"]
    satirlar = [
        f"# Arama değerlendirme raporu — {tarih}",
        "",
        f"- Sorgu sayısı: {len(durum['sorgular'])} · Yargıç hatası: {durum.get('yargic_hata', 0)}"
        + (" · YARGIÇ YOK (CF anahtarları eksik)" if durum.get("yargic_yok") else ""),
        f"- normal nDCG@8: {m['normal']['ndcg']} · MRR: {m['normal']['mrr']} · boş sorgu: {durum['bos']['normal']}",
        f"- ham    nDCG@8: {m['ham']['ndcg']} · MRR: {m['ham']['mrr']} · boş sorgu: {durum['bos']['ham']}",
        f"- rerank nDCG@8: {m['rerank']['ndcg']} · MRR: {m['rerank']['mrr']} · boş sorgu: {durum['bos']['rerank']}",
        f"- Karar: **{durum['karar']}**"
        + (f" · {d['anahtar']}: {d['eski']} → {d['yeni']}"
           + (" (arama_ayar'a yazıldı)" if yazildi else " (YAZILAMADI)") if d else ""),
        "",
        "Kod değişikliği önerileri bu raporda kalır; otomatik uygulanmaz.",
    ]
    with open(os.path.join(DATA_KOK, f"rapor-{tarih}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(satirlar) + "\n")
    print(f"[rapor] data/eval/rapor-{tarih}.md yazıldı; karar={durum['karar']}")
    return {"yazildi": yazildi}


# ---------- Kuru mod kaynakları (ağ yok) ----------

def _kuru_arama(durum):
    kol_sonuc, bos = {kol: {} for kol in KOL_KOLLAR}, {kol: 0 for kol in KOL_KOLLAR}
    for sorgu in durum["sorgular"]:
        for kol in KOL_KOLLAR:
            adet = 8 if kol != "ham" else 7  # boş sonuç senaryosunu da tetikle
            kol_sonuc[kol][sorgu] = [
                {"baslik": f"Sahte sonuç {i}", "kaynak": "gunluk-rapor", "tarih": "2026-11-02",
                 "icerik": f"{sorgu} ile ilgili sahte içerik GARAN RSI temettü"}
                for i in range(adet)
            ]
    return {"kol_sonuc": kol_sonuc, "bos": bos}


def _kuru_yargic(durum):
    puanlar = {kol: {} for kol in KOL_KOLLAR}
    for kol in KOL_KOLLAR:
        for sorgu, liste in durum["kol_sonuc"][kol].items():
            # normal/rerank kolu ilk 2 sonuçta tam ilgi; ham kolu biraz zayıf
            puanlar[kol][sorgu] = {i: (2 if i < 2 else (1 if i < 5 else 0))
                                   for i in range(len(liste))}
    return {"puanlar": puanlar, "yargic_hata": 0, "yargic_yok": False}


# ---------- Graf kurulumu ----------

class Durum(TypedDict, total=False):
    """StateGraph şeması: düğüm çıktıları anahtar-anahtar birleşir."""
    kuru: bool
    sorgular: list
    ipuclari: dict
    kol_sonuc: dict
    bos: dict
    puanlar: dict
    yargic_yok: bool
    yargic_hata: int
    metrikler: dict
    karar: str
    degisen: dict
    gecmis: list
    mevcut_ayar: dict


def graf_kur():
    from langgraph.graph import END, StateGraph

    builder = StateGraph(Durum)
    builder.add_node("sorgu_sec", sorgu_sec)
    builder.add_node("arama", arama)
    builder.add_node("yargic", yargic)
    builder.add_node("metrik", metrik)
    builder.add_node("karar", karar)
    builder.add_node("yaz", yaz)
    builder.set_entry_point("sorgu_sec")
    builder.add_edge("sorgu_sec", "arama")
    builder.add_edge("arama", "yargic")
    builder.add_edge("yargic", "metrik")
    builder.add_edge("metrik", "karar")
    builder.add_edge("karar", "yaz")
    builder.add_edge("yaz", END)
    return builder.compile()


def main():
    ayrik = argparse.ArgumentParser(description="Arama kalitesi değerlendirme döngüsü")
    ayrik.add_argument("--kuru", action="store_true",
                       help="ağ yok: sahte veriyle graf doğrulaması (dosya yazılmaz)")
    arg = ayrik.parse_args()
    t0 = time.time()

    app = graf_kur()
    son: Dict[str, Any] = app.invoke({"kuru": bool(arg.kuru)},
                                     config={"recursion_limit": 50})
    for kol in KOL_KOLLAR:
        m = son["metrikler"][kol]
        print(f"  {kol}: nDCG@8={m['ndcg']} MRR={m['mrr']}")
    print(f"Tamamlandı {round(time.time() - t0, 1)} sn; karar={son['karar']}")


if __name__ == "__main__":
    main()
