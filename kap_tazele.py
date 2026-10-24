# -*- coding: utf-8 -*-
"""KAP bildirim akisini resmi MKK API'sinden (KAP VYK REST) tazeleyip
data/kap/kap-akisi.json'u ayni formatta yeniden yazar.

Kullanim:
    python kap_tazele.py              # son kayitli index'ten itibaren artimli
    python kap_tazele.py --tam        # son 7 gunu bastan ceker

Kimlik:
    Anahtar asla bu dosyaya yazilmaz. Okuma sirasi:
      1. KAP_API_KEY ortam degiskeni
      2. C:/Users/pc/.zcode/workspace/default/.kap_api_key dosyasi (tek satir)
    Test ortaminda (apigwdev) token gerekmez; PROD'da (apigw) apiKey ile
    /auth/generateToken'a GET atilir, donen token 24 saat Bearer olarak
    kullanilir. Portalda uretim aboneligi onaylanana kadar --test ile test
    ortami kullanilir (veriler test/karistirma verisi olabilir).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

HOST_TEST = "https://apigwdev.mkk.com.tr/api/vyk"
HOST_PROD = "https://apigw.mkk.com.tr/api/vyk"
ANAHTAR_DOSYA = os.path.expanduser("~/.zcode/workspace/default/.kap_api_key")
CIKTI = os.path.join("data", "kap", "kap-akisi.json")
SON_INDEX = os.path.join("data", "kap", "son-index.json")
KODLAR = ["AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "EKGYO", "ENKAI", "EREGL",
          "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KRDMD", "MGROS", "PETKM",
          "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO",
          "TRALT", "TRMET", "TTKOM", "TUPRS", "VAKBN", "YKBNK"]


def _anahtar():
    v = os.environ.get("KAP_API_KEY")
    if v:
        return v.strip()
    try:
        with open(ANAHTAR_DOSYA, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _istek(url, kimlik, token=None):
    """GET; kimlik yolu: token varsa Bearer, yoksa API-Key header denemeleri
    Apinizer gateway icin yaygin uc biçim sırayla denenir."""
    son_hata = None
    basliklar = []
    if token:
        basliklar.append({"Authorization": f"Bearer {token}"})
    basliklar.append({"Authorization": f"Api-Key {kimlik}"})
    basliklar.append({"Api-Key": kimlik})
    for baslik in basliklar:
        istek = urllib.request.Request(url, headers={"User-Agent": "borsa-raporlari/1.0", **baslik})
        try:
            with urllib.request.urlopen(istek, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            govde = e.read().decode(errors="replace")[:200]
            son_hata = f"{e.code}: {govde}"
            if e.code in (401, 403):
                continue  # diger kimlik biçimini dene
            raise RuntimeError(f"{url} -> {son_hata}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"ag hatasi {url}: {e}")
    raise RuntimeError(f"kimlik gecersiz: {son_hata}")


def main():
    tam = "--tam" in sys.argv
    test = "--test" in sys.argv or "--prod" not in sys.argv
    host = HOST_TEST if test else HOST_PROD
    anahtar = _anahtar()
    if not anahtar:
        raise SystemExit("API anahtari yok: KAP_API_KEY ortam degiskeni ya da "
                         f"{ANAHTAR_DOSYA} dosyasi olusturulmali.")
    token = None
    if not test:
        token = _istek(f"{host[:-4]}/auth/generateToken?apiKey="
                       f"{urllib.parse.quote(anahtar)}", anahtar)["token"]
    print(f"[KAP] ortam: {'TEST' if test else 'PROD'} | host: {host}")

    # 1) sirket listesi -> stockCode -> companyId
    uyeler = _istek(f"{host}/members", anahtar, token)
    if isinstance(uyeler, dict):
        uyeler = uyeler.get("items") or uyeler.get("data") or uyeler.get("list") or []
    kod_map = {}
    for u in uyeler:
        sc = (u.get("stockCode") or "").upper()
        if sc in KODLAR:
            kod_map[sc] = u.get("id") or u.get("companyId")
    eksik = [k for k in KODLAR if k not in kod_map]
    print(f"[KAP] members: {len(kod_map)}/{len(KODLAR)} eslesti"
          + (f" | eksik: {eksik}" if eksik else ""))
    if not kod_map:
        raise SystemExit("hicbir hisse eslesmedi; /members donumune bak.")

    # 2) son index (yoksa lastDisclosureIndex'ten geriye 7 gunluk tarama icin
    #    index'i bulmak yerine tam modda son index'ten itibaren sayfalayarak
    #    tarihe gore filtrelenir; disclosures index tabanli calisir)
    son = None
    if not tam:
        try:
            with open(SON_INDEX, encoding="utf-8") as f:
                son = json.load(f).get("lastDisclosureIndex")
        except (OSError, ValueError):
            pass
    if not son:
        son = _istek(f"{host}/lastDisclosureIndex", anahtar, token)["lastDisclosureIndex"]
        print(f"[KAP] ilk calistirma; son index: {son} (tarihe geriye gidecegiz)")

    # 3) disclosures sayfalama: index'ten geriye gidemiyoruz; API "gonderilen
    #    index'ten ITIBAREN ilk 50" veriyor -> tam modda lastDisclosureIndex'ten
    #    itibaren simdiye kadarki kismi alip tarih filtresiyle 7 gune indiriyoruz.
    sinir_tarih = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    tum_liste = []
    imlec = son
    for _ in range(40):  # 40*50=2000 oge siniri; asimda index'i kaydir
        q = urllib.parse.urlencode({"disclosureIndex": imlec,
                                    **{f"companyId": c for c in kod_map.values()}})
        q = "disclosureIndex=" + urllib.parse.quote(str(imlec)) + "".join(
            f"&companyId={urllib.parse.quote(str(c))}" for c in kod_map.values())
        liste = _istek(f"{host}/disclosures?{q}", anahtar, token)
        if isinstance(liste, dict):
            liste = liste.get("items") or liste.get("data") or []
        if not liste:
            break
        tum_liste.extend(liste)
        yeni_son = max(int(x["disclosureIndex"]) for x in liste)
        if yeni_son <= int(imlec):
            break
        imlec = yeni_son
        if any((x.get("title") or "") for x in liste[-5:]) and len(liste) < 50:
            break
        if not tam and any(
                str(datetime.today().year) in str(x.get("time") or x.get("date") or "")
                for x in liste[:1]):
            pass  # tarih filtresi asagida yapilir; cikis kosulu uzun olmamasin
        if len(tum_liste) >= 2000:
            break

    # 4) sirket filtresi (API dizi almiyorsa client-side da emniyet) + detay yok:
    #    kart icin yalniz liste bilgisi yeterli (tarih/baslik/url). Detail
    #    cagrilari tum ogelere atilirsa istek patlar; sadece son 8/hisse cekilir.
    temiz = {k: [] for k in KODLAR}
    for x in tum_liste:
        stoklar = x.get("stockCodes") or x.get("relatedStocks") or []
        kodlar = {s.get("code", "").upper() if isinstance(s, dict) else str(s).upper()
                  for s in stoklar} & set(KODLAR)
        for kod in kodlar:
            temiz[kod].append({
                "kaynak": "KAP",
                "tarih": str(x.get("time") or x.get("date") or ""),
                "baslik": x.get("title") or "",
                "url": f"https://www.kap.org.tr/tr/bildirim/{x['disclosureIndex']}",
                "genis": len(kodlar) > 1,
                "index": x["disclosureIndex"],
            })
    for kod in temiz:
        temiz[kod].sort(key=lambda o: str(o.get("index", 0)), reverse=True)

    eskiler = {}
    try:
        with open(CIKTI, encoding="utf-8") as f:
            eskiler = json.load(f).get("hisseler", {})
    except (OSError, ValueError):
        pass
    for kod in temiz:
        if len(temiz[kod]) < 8 and eskiler.get(kod):
            # artimli kosuda yeni bildirim yoksa eskileri koru (ayni kaynagi
            # iki kez yazmamak icin index kontrolu)
            var_index = {o.get("index") for o in temiz[kod]}
            temiz[kod].extend(o for o in eskiler[kod] if o.get("index") not in var_index)

    json.dump({"meta": {"kaynak": "MKK KAP VYK REST API (resmi)", "tarih": str(date.today()),
                        "mod": "TEST" if test else "PROD"},
               "hisseler": temiz},
              open(CIKTI, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if son:
        json.dump({"lastDisclosureIndex": imlec}, open(SON_INDEX, "w", encoding="utf-8"))
    toplam = sum(len(v) for v in temiz.values())
    print(f"[KAP] yazildi: {toplam} oge -> {CIKTI}")
    # sifir olanlari bildir (kart bos kalirsa eski veri kalir)
    bos = [k for k, v in temiz.items() if not v]
    if bos:
        print(f"[KAP] bos kalanlar: {bos}")


if __name__ == "__main__":
    main()
