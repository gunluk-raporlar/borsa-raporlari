# -*- coding: utf-8 -*-
"""Borsa raporlarını embedding'leyip Supabase (pgvector) veritabanına yazar.

Ne yapar?
  1. reports/, haftasonu/, haftasonu-egitimi/ altındaki HTML raporları metne
     çıkarır, cümle sınırlarına dikkat ederek ~1400 karakterlik parçalara böler.
  2. Cloudflare Workers AI'nin OpenAI-uyumlu embeddings endpoint'i (bge-m3)
     üzerinden parçaları ASYNC + BATCH embedding'ler (asyncio + AsyncOpenAI;
     429/ağ hatasında exponential backoff).
  3. Parçaları hash karşılaştırmasıyla ARTIMLI upsert eder: içeriği
     değişmeyen parçalar yeniden embed edilmez, böylece günlük maliyet ~0.
     Belge küçuldüyse artık olmayan parçaların satırları silinir.

Kullanım:
  python pipeline/embed.py              # son 10 günün raporları (artımlı)
  python pipeline/embed.py --backfill   # tüm arşiv
  python pipeline/embed.py --dry-run    # ağ çağrısı YOK, planı özetler

Ortam değişkenleri:
  CF_ACCOUNT_ID, CF_API_KEY            embedding sağlayıcısı (Workers AI)
  SUPABASE_URL, SUPABASE_SERVICE_KEY   veritabanı (REST, sunucu gerektirmez)
  EMBED_MODEL=@cf/baai/bge-m3  EMBED_BATCH=16  EMBED_CONCURRENCY=3

Secret'lar tanımlı değilse script uyarı verip 0 ile çıkar: CI zincirini
kırmadan, secret'lar girildiği gün otomatik devreye girer.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser

KAYNAKLAR = [
    # (kaynak adı, klasör, dosya adı deseni)
    ("gunluk-rapor", "reports", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
    ("derin-analiz", "reports", re.compile(r"^\d{4}-\d{2}-\d{2}-derin-analiz\.html$")),
    ("haftasonu", "haftasonu", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
    ("egitim", "haftasonu-egitimi", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
]
SITE_BASE = os.environ.get("SITE_URL", "https://borsa-raporlari.pages.dev").rstrip("/")
PARCA_BOYUTU = 1400


# ---------- HTML → metin ----------

class _MetinCikarici(HTMLParser):
    ATLANAN = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parcalar = []
        self.atla = 0
        self.baslik = ""
        self._h1 = False

    def handle_starttag(self, tag, attrs):
        if tag in self.ATLANAN:
            self.atla += 1
        elif tag == "h1":
            self._h1 = True

    def handle_endtag(self, tag):
        if tag in self.ATLANAN and self.atla:
            self.atla -= 1
        elif tag == "h1":
            self._h1 = False

    def handle_data(self, data):
        if self.atla:
            return
        metin = data.strip()
        if not metin:
            return
        if self._h1 and not self.baslik:
            self.baslik = metin
        self.parcalar.append(metin)


def html_metni(yol):
    with open(yol, encoding="utf-8", errors="replace") as fh:
        ham = fh.read()
    c = _MetinCikarici()
    c.feed(ham)
    baslik = c.baslik
    if not baslik:
        m = re.search(r"<title>(.*?)</title>", ham, re.S | re.I)
        baslik = re.sub(r"\s+", " ", m.group(1)).strip() if m else os.path.basename(yol)
    return baslik, "\n".join(c.parcalar)


def parcala(metin, boyut=PARCA_BOYUTU):
    """Cümle sınırlarında böl; aşırı uzun cümleleri sert böl."""
    cumleler = re.split(r"(?<=[.!?…])\s+|\n+", metin)
    parcalar, suanki = [], ""
    for cumle in cumleler:
        cumle = cumle.strip()
        if not cumle:
            continue
        if len(cumle) > boyut:
            if suanki:
                parcalar.append(suanki)
                suanki = ""
            for i in range(0, len(cumle), boyut):
                parcalar.append(cumle[i:i + boyut])
            continue
        if len(suanki) + len(cumle) + 1 > boyut:
            parcalar.append(suanki)
            suanki = cumle
        else:
            suanki = f"{suanki} {cumle}".strip()
    if suanki:
        parcalar.append(suanki)
    return parcalar


def belgeleri_topla(backfill=False):
    """(kaynak, klasör, desen) üçlülerine göre HTML'leri parçalar."""
    sinir = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    belgeler = []
    for kaynak, klasor, desen in KAYNAKLAR:
        if not os.path.isdir(klasor):
            continue
        for ad in sorted(os.listdir(klasor)):
            if not desen.match(ad):
                continue
            tarih = ad[:10]
            if not backfill and tarih < sinir:
                continue
            yol = os.path.join(klasor, ad)
            try:
                baslik, metin = html_metni(yol)
            except Exception as e:
                print(f"  [atla] {yol}: {e}")
                continue
            if len(metin) < 400:
                continue
            url = f"{SITE_BASE}/{klasor}/{ad}"
            for i, parca in enumerate(parcala(metin)):
                belgeler.append({
                    "kaynak": kaynak,
                    "url": url,
                    "parca": i,
                    "baslik": baslik,
                    "tarih": tarih,
                    "icerik": parca,
                    "hash": hashlib.sha256(parca.encode("utf-8")).hexdigest()[:16],
                })
    return belgeler


# ---------- Embedding (Workers AI, async batch) ----------

async def embed_et(parcalar, model, batch, concurrency):
    from openai import APIError, AsyncOpenAI, RateLimitError

    acct = os.environ["CF_ACCOUNT_ID"]
    anahtar = os.environ["CF_API_KEY"]
    istemci = AsyncOpenAI(
        api_key=anahtar,
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1",
        timeout=120.0,
        max_retries=2,
    )
    sem = asyncio.Semaphore(concurrency)
    vektorler = [None] * len(parcalar)

    async def grup_embed(baslangic):
        grup = parcalar[baslangic:baslangic + batch]
        async with sem:
            for deneme in range(5):
                try:
                    resp = await istemci.embeddings.create(model=model, input=grup)
                    for j, d in enumerate(resp.data):
                        vektorler[baslangic + j] = d.embedding
                    return
                except (RateLimitError, APIError) as e:
                    bekle = min(2 ** deneme * 5, 60)
                    print(f"  [retry] embedding deneme {deneme + 1}/5: {type(e).__name__} → {bekle}s bekleniyor")
                    await asyncio.sleep(bekle)
            raise RuntimeError("embedding 5 denemede başarısız oldu")

    await asyncio.gather(*[grup_embed(i) for i in range(0, len(parcalar), batch)])
    eksik = [i for i, v in enumerate(vektorler) if not v]
    if eksik:
        raise RuntimeError(f"{len(eksik)} parçanın embedding'i boş döndü")
    return vektorler


# ---------- Supabase REST ----------

def _sb_basliklar(anahtar):
    return {
        "apikey": anahtar,
        "Authorization": f"Bearer {anahtar}",
        "Content-Type": "application/json",
    }


def sb_mevcut_hashler(base, anahtar, url_listesi):
    import requests

    hashler = {}
    for i in range(0, len(url_listesi), 50):
        grup = url_listesi[i:i + 50]
        in_f = ",".join(f'"{u}"' for u in grup)
        r = requests.get(
            f"{base}/rest/v1/belgeler",
            params={"select": "url,parca,hash", "url": f"in.({in_f})"},
            headers={**_sb_basliklar(anahtar), "Range": "0-99999"},
            timeout=60,
        )
        r.raise_for_status()
        for satir in r.json():
            hashler[(satir["url"], satir["parca"])] = satir["hash"]
    return hashler


def sb_upsert(base, anahtar, satirlar):
    import requests

    for i in range(0, len(satirlar), 50):
        r = requests.post(
            f"{base}/rest/v1/belgeler",
            params={"on_conflict": "kaynak,url,parca"},
            headers={**_sb_basliklar(anahtar), "Prefer": "resolution=merge-duplicates"},
            json=satirlar[i:i + 50],
            timeout=120,
        )
        r.raise_for_status()


def sb_fazla_parca_sil(base, anahtar, url, son_parca):
    """Belge küçuldüyse artık var olmayan parça satırlarını siler."""
    import requests

    r = requests.delete(
        f"{base}/rest/v1/belgeler",
        params={"url": f"eq.{url}", "parca": f"gte.{son_parca}"},
        headers=_sb_basliklar(anahtar),
        timeout=60,
    )
    r.raise_for_status()


# ---------- Metrik ----------

def metrik_yaz(belge_sayisi, embed_edilen, sure_sn):
    """data/metrics/latest.json'a embedding bölümünü yazar (izleme sayfası okur)."""
    try:
        os.makedirs(os.path.join("data", "metrics"), exist_ok=True)
        simdi = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+03:00")
        bolum = {
            "belge_sayisi": belge_sayisi,
            "embed_edilen_parca": embed_edilen,
            "sure_sn": round(sure_sn, 1),
            "son_guncelleme": simdi,
        }
        latest_yol = os.path.join("data", "metrics", "latest.json")
        latest = {}
        if os.path.exists(latest_yol):
            try:
                with open(latest_yol, encoding="utf-8") as fh:
                    latest = json.load(fh)
            except Exception:
                latest = {}
        latest["embedding"] = bolum
        latest["guncelleme"] = simdi
        with open(latest_yol, "w", encoding="utf-8") as fh:
            json.dump(latest, fh, ensure_ascii=False, indent=1)
        tarih = datetime.now().strftime("%Y-%m-%d")
        with open(os.path.join("data", "metrics", f"embedding-{tarih}.json"), "w", encoding="utf-8") as fh:
            json.dump({"tarih": tarih, **bolum}, fh, ensure_ascii=False, indent=1)
        print(f"[metrik] latest.json güncellendi: {bolum}")
    except Exception as e:
        print(f"[uyarı] metrik yazılamadı (çalışışı etkilemez): {e}")


# ---------- Ana akış ----------

def main():
    backfill = "--backfill" in sys.argv
    dry = "--dry-run" in sys.argv
    t0 = time.time()

    belgeler = belgeleri_topla(backfill=backfill)
    belge_adet = len({b["url"] for b in belgeler})
    print(f"{belge_adet} belgeden {len(belgeler)} parça çıkarıldı"
          f"{' (backfill: tüm arşiv)' if backfill else ' (son 10 gün)'}.")
    if not belgeler:
        print("[atlandı] embed edilecek rapor yok.")
        return

    if dry:
        for url in sorted({b["url"] for b in belgeler}):
            adet = sum(1 for b in belgeler if b["url"] == url)
            print(f"  {adet:>3} parça  {url.rsplit('/', 1)[-1]}")
        print("dry-run: ağ çağrısı yapılmadı, çıkılıyor.")
        return

    eksik = [v for v in ("CF_API_KEY", "CF_ACCOUNT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_KEY")
             if not os.environ.get(v)]
    if eksik:
        print(f"[atlandı] Eksik secret'lar: {', '.join(eksik)}. "
              "GitHub secret'ları girildiğinde bu workflow otomatik çalışacak.")
        return

    model = os.environ.get("EMBED_MODEL", "@cf/baai/bge-m3")
    batch = int(os.environ.get("EMBED_BATCH", "16"))
    concurrency = int(os.environ.get("EMBED_CONCURRENCY", "3"))
    sb_base = os.environ["SUPABASE_URL"].rstrip("/")
    sb_key = os.environ["SUPABASE_SERVICE_KEY"]

    url_listesi = sorted({b["url"] for b in belgeler})
    mevcut = sb_mevcut_hashler(sb_base, sb_key, url_listesi)
    yeni = [b for b in belgeler if mevcut.get((b["url"], b["parca"])) != b["hash"]]
    print(f"{len(yeni)} parça embed edilecek ({len(belgeler) - len(yeni)} parça değişmedi, atlanıyor).")

    if yeni:
        print(f"Embedding başlıyor: model={model}, batch={batch}, eşzamanlılık={concurrency}")
        vektorler = asyncio.run(embed_et([b["icerik"] for b in yeni], model, batch, concurrency))
        satirlar = []
        for b, v in zip(yeni, vektorler):
            satirlar.append({**b, "embedding": v})
        sb_upsert(sb_base, sb_key, satirlar)
        print(f"{len(satirlar)} parça Supabase'e yazıldı.")

        # Küçülen belgelerde fazladan kalan eski parçaları temizle
        parca_adet = {}
        for b in belgeler:
            parca_adet[b["url"]] = max(parca_adet.get(b["url"], 0), b["parca"] + 1)
        for url in url_listesi:
            mevcut_adet = max([p for (u, p) in mevcut if u == url], default=-1) + 1
            if mevcut_adet > parca_adet.get(url, 0):
                sb_fazla_parca_sil(sb_base, sb_key, url, parca_adet.get(url, 0))
                print(f"  [temizlik] {url.rsplit('/', 1)[-1]}: {mevcut_adet - parca_adet.get(url, 0)} eski parça silindi")

    metrik_yaz(belge_adet, len(yeni), time.time() - t0)
    print(f"Tamamlandı {round(time.time() - t0, 1)} saniyede.")


if __name__ == "__main__":
    main()
