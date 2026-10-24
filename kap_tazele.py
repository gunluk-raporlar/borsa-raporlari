# -*- coding: utf-8 -*-
"""KAP bildirim akisini resmi MKK API'sinden (KAP VYK REST) tazeleyip
data/kap/kap-akisi.json'u ayni formatta yeniden yazar.

Kullanim:
    python kap_tazele.py --test        # apigwdev (veri 2023 sonuna kadar donuk!)
    python kap_tazele.py --prod        # apigw (uretim gateway yayina alindiginda)
    python kap_tazele.py --prod --geri 4000   # imleci geriye atlayip genis tarama

Kimlik (sir guvenligi): deger bu dosyaya yazilmaz.
    1. KAP_API_KEY ortam degiskeni
    2. ~/.zcode/workspace/default/.kap_api_key (tek satir KEY:SECRET)
    Gateway kimlik yolu (canli testle kesinlesti):
        Authorization: Basic base64(KEY:SECRET)
    PROD'da ek olarak /auth/generateToken ile 24 saatlik Bearer token alinabilir.

Not: TEST ortami verisi 2023 sonuna kadar donuk; gercek bildirimler icin uretim
gateway'i (apigw.mkk.com.tr) gerekir — portalde uretim aboneligi/onayi acildiginda
--prod ile kosulur.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

HOST_TEST = "https://apigwdev.mkk.com.tr/api/vyk"
HOST_PROD = "https://apigw.mkk.com.tr/api/vyk"
ANAHTAR_DOSYA = os.path.expanduser("~/.zcode/workspace/default/.kap_api_key")
CIKTI = os.path.join("data", "kap", "kap-akisi.json")
CIKTI_TEST = os.path.join("data", "kap", "kap-akisi-test.json")
SON_INDEX = os.path.join("data", "kap", "son-index.json")
KODLAR = ["AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "EKGYO", "ENKAI", "EREGL",
          "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD", "MGROS", "PETKM",
          "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO",
          "TRALT", "TRMET", "TTKOM", "TUPRS", "VAKBN", "YKBNK"]
# unvan anahtarlarina gore esleme (bankalarda stockCode virgullu liste; bazi
# sirketler test listesinde hic yok)
UNVAN_ARAMA = {"GARAN": ["TÜRKİYE GARANTİ BANKASI", "GARANTI BANKASI"],
               "ISCTR": ["TÜRKİYE İŞ BANKASI"],
               "VAKBN": ["VAKIFLAR BANKASI"],
               "YKBNK": ["YAPI VE KREDİ BANKASI"],
               "KRDMD": ["KARDEMİR"],
               "TRALT": ["TÜRK ALTIN İŞLETMELERİ"],
               "TRMET": ["TR ANADOLU METAL"]}
LIMIT_HISSE_BASINA = 8
SAFE_SAYFA = 120  # 120*50 = 6000 oge guvenlik siniri


def _kimlik():
    """(key, secret) doner; degerler hicbir yerde yazdirilmaz."""
    v = os.environ.get("KAP_API_KEY")
    if not v:
        try:
            with open(ANAHTAR_DOSYA, encoding="utf-8") as f:
                v = "".join(f.read().split())
        except OSError:
            return None, None
    parca = v.split(":")
    return parca[0], (parca[1] if len(parca) > 1 else "")


def _istek(url, kimlik, secret, token=None):
    if token:
        baslik = {"Authorization": f"Bearer {token}"}
    else:
        birlesik = kimlik + ":" + (secret or "")
        baslik = {"Authorization": "Basic " + base64.b64encode(birlesik.encode()).decode()}
    istek = urllib.request.Request(url, headers={"User-Agent": "borsa-raporlari/1.0", **baslik})
    try:
        with urllib.request.urlopen(istek, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} -> {e.code}: {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"ag hatasi {url}: {e}")


def main():
    tam = "--tam" in sys.argv
    test = "--prod" not in sys.argv
    host = HOST_TEST if test else HOST_PROD
    geri = 4000
    for i, a in enumerate(sys.argv):
        if a == "--geri" and i + 1 < len(sys.argv):
            geri = int(sys.argv[i + 1])
    kimlik, secret = _kimlik()
    if not kimlik:
        raise SystemExit(f"API anahtari yok: KAP_API_KEY ya da {ANAHTAR_DOSYA} (KEY:SECRET).")
    token = None
    if not test:
        tk = _istek(f"{host[:-4]}/auth/generateToken?apiKey={urllib.parse.quote(kimlik)}",
                    kimlik, secret)
        token = tk.get("token")
    print(f"[KAP] ortam: {'TEST' if test else 'PROD'} | host: {host}")
    if test:
        print("[KAP] UYARI: test ortam verisi 2023 sonuna kadar donuk; gercek bildirimler --prod.")

    # 1) members: kod -> id (stockCode virgullu liste + unvan aramasi)
    uyeler = _istek(f"{host}/members", kimlik, secret, token)
    if isinstance(uyeler, dict):
        uyeler = uyeler.get("items") or uyeler.get("data") or uyeler.get("list") or []
    kod_map = {}
    for u in uyeler:
        kodlar_u = [c.strip().upper() for c in (u.get("stockCode") or "").split(",")]
        for k in kodlar_u:
            if k in KODLAR and k not in kod_map:
                kod_map[k] = u.get("id")
        unvan = (u.get("title") or "").upper()
        for k, anahtarlar in UNVAN_ARAMA.items():
            if k not in kod_map and any(a in unvan for a in anahtarlar):
                kod_map[k] = u.get("id")
    eksik = [k for k in KODLAR if k not in kod_map]
    print(f"[KAP] members: {len(kod_map)}/{len(KODLAR)} eslesti"
          + (f" | eksik: {eksik} (prod listesinde tekrar bakilir)" if eksik else ""))
    id_set = {str(v) for v in kod_map.values()}

    # 2) imlec: saklanandan itibaren; tam/ilk kosuda last-geri
    imlec = None
    if not tam:
        try:
            with open(SON_INDEX, encoding="utf-8") as f:
                imlec = json.load(f).get("lastDisclosureIndex")
        except (OSError, ValueError):
            pass
    son = _istek(f"{host}/lastDisclosureIndex", kimlik, secret, token)["lastDisclosureIndex"]
    if not imlec:
        imlec = str(int(son) - geri)
        print(f"[KAP] ilk tarama: imlec {imlec} (son {son}; geri {geri})")

    # 3) genis sayfalama (companyId parametresi en fazla 10 kabul ediyor —
    #    client filtre kullanilir)
    tum_liste = []
    for _ in range(SAFE_SAYFA):
        liste = _istek(f"{host}/disclosures?disclosureIndex={urllib.parse.quote(str(imlec))}",
                       kimlik, secret, token)
        if isinstance(liste, dict):
            liste = liste.get("items") or liste.get("data") or []
        if not liste:
            break
        tum_liste.extend(liste)
        yeni = max(int(x.get("disclosureIndex", 0)) for x in liste)
        if yeni <= int(imlec) or len(liste) < 50:
            break
        imlec = yeni
        if int(imlec) >= int(son):
            break
    print(f"[KAP] taranan oge: {len(tum_liste)}")

    # 4) sirket filtresi (companyId string karsilastirmasi)
    eslesen = [x for x in tum_liste if str(x.get("companyId")) in id_set]
    for x in eslesen:
        x["_kod"] = next(k for k, v in kod_map.items() if str(v) == str(x.get("companyId")))

    # 5) detail: tarih icin — hisse basina son 8
    temiz = {k: [] for k in KODLAR}
    eslesen.sort(key=lambda x: int(x.get("disclosureIndex", 0)), reverse=True)
    sayim = {}
    for x in eslesen:
        kod = x["_kod"]
        if sayim.get(kod, 0) >= LIMIT_HISSE_BASINA:
            continue
        det, h = None, None
        try:
            det = _istek(f"{host}/disclosureDetail/{x['disclosureIndex']}?fileType=data",
                         kimlik, secret, token)
        except RuntimeError as e:
            h = str(e)[:80]
        zaman = (det or {}).get("time") or ""
        temiz[kod].append({
            "kaynak": "KAP",
            "tarih": zaman,
            "baslik": (det or {}).get("subject", {}).get("tr") or x.get("title") or "",
            "url": f"https://www.kap.org.tr/tr/bildirim/{x['disclosureIndex']}",
            "genis": False,
            "index": x["disclosureIndex"],
            **({"hata": h} if h else {}),
        })
        sayim[kod] = sayim.get(kod, 0) + 1

    # 6) artimli kosuda yeni yoksa eskileri koru
    eskiler = {}
    try:
        with open(CIKTI, encoding="utf-8") as f:
            eskiler = json.load(f).get("hisseler", {})
    except (OSError, ValueError):
        pass
    for kod in temiz:
        if len(temiz[kod]) < LIMIT_HISSE_BASINA and eskiler.get(kod):
            var_index = {o.get("index") for o in temiz[kod]}
            temiz[kod].extend(o for o in eskiler[kod] if o.get("index") not in var_index)
        temiz[kod].sort(key=lambda o: str(o.get("index", 0)), reverse=True)

    json.dump({"meta": {"kaynak": "MKK KAP VYK REST API (resmi)", "tarih": str(date.today()),
                        "mod": "TEST" if test else "PROD"},
               "hisseler": temiz},
              open(CIKTI_TEST if test else CIKTI, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"lastDisclosureIndex": son}, open(SON_INDEX, "w", encoding="utf-8"))
    toplam = sum(len(v) for v in temiz.values())
    print(f"[KAP] yazildi: {toplam} oge -> {CIKTI_TEST if test else CIKTI}")
    bos = [k for k, v in temiz.items() if not v]
    if bos:
        print(f"[KAP] bos kalanlar: {bos}")


if __name__ == "__main__":
    main()
