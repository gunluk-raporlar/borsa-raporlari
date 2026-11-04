# -*- coding: utf-8 -*-
"""Borsa raporlarını embedding'leyip Supabase (pgvector) veritabanına yazar.

Ne yapar?
  1. raporlar/, haftasonu/, haftasonu-egitimi/ altındaki HTML raporları metne
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
    ("gunluk-rapor", "raporlar", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
    ("derin-analiz", "derin-analiz", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
    # makro-analiz başlangıçta unutulmuştu (kullanıcı bulgusu 2026-11-01:
    # akıllı aramada makro sonuçları yok); backfill ile tüm arşiv işlenir.
    ("makro-analiz", "makro-analiz", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
    ("haftasonu", "haftasonu", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
    ("egitim", "haftasonu-egitimi", re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")),
]

# Kök dizindeki tarihsiz sözlük sayfaları (kullanıcı kararı 2026-11-01:
# sözlükler pgvector'a eklensin). Adlarında tarih olmadığından tarih = dosya
# mtime'ı alınır ve 10 günlük tazelik süzgecine takılmazlar; içerik
# değişmedikçe hash aynı kaldığından embedding maliyeti çıkmaz.
# 2026-11-02 (kullanıcı kararı): temettü merkezi de anlam aramasında çıksın —
# takvim/verim içerikleri sunucuda üretilip HTML'e yazılır (metne çıkar);
# hesaplayıcı istemci JS olduğundan indekse girmez.
KOK_SAYFALAR = [
    ("sozluk", "sozluk.html"),
    ("terimler", "terimler.html"),
    ("muhasebe-terimleri", "muhasebe-terimleri.html"),  # hub (terim adı indisi)
    ("temettu", "temettu.html"),
]

# Tarihsiz harf-grup klasörleri (2026-11-04 Faz 1a/6): muhasebe-terimleri/
# 2,16 MB'lık tek sayfadan harf-grup sayfalarına bölündü; indeksin ~%27'si tek
# sayfadan geliyordu. Tarih = dosya mtime'ı (KOK_SAYFALAR kuralı).
# 2026-11-04 (kullanıcı kararı): hisse detay sayfaları da anlam aramasına
# girdi — "kullanıcı önce hisseyi merak eder" (site-arama.json'da zaten
# vardı; pgvector'da yoktu). Maliyet notu: hisse sayfaları CI'da her gün
# yeniden üretildiğinden fiyat/teknik/haber parçaları GÜNLÜK yeniden embed
# edilir (~150 parça/gün tahmini); şirket profili/tanıtım gibi sabit parçalar
# hash değişmediği için yeniden embed edilmez.
KOK_KLASORLER = [
    ("muhasebe-terimleri-grup", "muhasebe-terimleri"),
    ("hisse-detay", "hisse"),
]
SITE_BASE = os.environ.get("SITE_URL", "https://borsa-raporlari.pages.dev").rstrip("/")
PARCA_BOYUTU = 1400


# ---------- HTML → metin ----------

class _MetinCikarici(HTMLParser):
    # 2026-11-03 mimar notu: gezinme/uygulama kabuğu embedding parçalarına
    # karışmasın — head/nav/header/footer vb. atlanır; sınıf/id'sinde "hesap"
    # veya "asistan" geçen öğeler (hesaplayıcı, asistan kartı) özel atlanır;
    # sayfada <main> varsa yalnız main içinden metin alınır.
    # 2026-11-04 (Faz 6, ajan-okunurluk): olay akışı (seviye, metin) üretir —
    # h2/h3 ayrı olay olarak gelir (başlık-sınırı bölme için), td/th sınırı
    # " | " olarak korunur (tablo kolon anlamı artık düz metne ezilmez).
    ATLANAN = {"script", "style", "noscript", "svg", "head", "nav", "header",
               "footer", "aside", "button", "form", "select", "option",
               "label", "template"}
    ATLA_SINIF = ("hesap", "asistan", "site-widgets", "live-clock",
                  "interactive-box", "arama-kutusu", "radyo")

    def __init__(self, yalniz_ana=False):
        super().__init__()
        self.olaylar = []      # (seviye, metin); seviye 0=metin, 2=h2, 3=h3
        self.atla = 0
        self.baslik = ""
        self._h1 = False
        self._b_sev = 0        # aktif h2/h3 birikimi (0 = başlık dışında)
        self._b_buf = []
        self.yalniz_ana = yalniz_ana
        self.ana = 0
        self.ozel_turu = None
        self.ozel_derinlik = 0

    def _arayuz_mi(self, attrs):
        for k, v in attrs:
            if k in ("class", "id") and v and any(p in v for p in self.ATLA_SINIF):
                return True
        return False

    def handle_starttag(self, tag, attrs):
        if self.ozel_turu is not None:
            if tag == self.ozel_turu:
                self.ozel_derinlik += 1
            return
        if tag == "main":
            self.ana += 1
        elif tag in self.ATLANAN:
            self.atla += 1
            return
        elif tag == "h1":
            self._h1 = True
        elif tag in ("h2", "h3"):
            self._b_sev = int(tag[1])
            self._b_buf = []
            return
        if self._arayuz_mi(attrs):
            self.ozel_turu = tag
            self.ozel_derinlik = 1

    def handle_endtag(self, tag):
        if self.ozel_turu is not None:
            if tag == self.ozel_turu:
                self.ozel_derinlik -= 1
                if self.ozel_derinlik <= 0:
                    self.ozel_turu = None
            return
        if tag in self.ATLANAN and self.atla:
            self.atla -= 1
        elif tag == "h1":
            self._h1 = False
        elif tag in ("h2", "h3"):
            if self._b_sev:
                metin = " ".join(x.strip() for x in self._b_buf if x.strip())
                if metin:
                    self.olaylar.append((self._b_sev, metin))
                self._b_sev = 0
                self._b_buf = []
            return
        elif tag in ("td", "th"):
            self.olaylar.append((0, " | "))
        elif tag == "tr":
            self.olaylar.append((0, "\u0001SATIR\u0001"))
        elif tag == "main" and self.ana:
            self.ana -= 1

    def handle_data(self, data):
        if self.atla or self.ozel_turu is not None:
            return
        if self.yalniz_ana and self.ana == 0:
            return
        metin = data.strip()
        if not metin:
            return
        if self._h1 and not self.baslik:
            self.baslik = metin
        if self._b_sev:
            self._b_buf.append(metin)
            return
        self.olaylar.append((0, metin))


def html_metni(yol):
    with open(yol, encoding="utf-8", errors="replace") as fh:
        ham = fh.read()
    c = _MetinCikarici(yalniz_ana="<main" in ham)
    c.feed(ham)
    baslik = c.baslik
    if not baslik:
        m = re.search(r"<title>(.*?)</title>", ham, re.S | re.I)
        baslik = re.sub(r"\s+", " ", m.group(1)).strip() if m else os.path.basename(yol)
    return baslik, c.olaylar


def _satir_bol(metin, boyut):
    """Uzun metin satırını cümle sınırlarına saygılı ≤boyut parçalara böler;
    aşırı uzun cümleleri sert böler (eski parcala davranışı, satır bazında)."""
    sonuclar, suanki = [], ""
    for cumle in re.split(r"(?<=[.!?…])\s+", metin):
        cumle = cumle.strip()
        if not cumle:
            continue
        if len(cumle) > boyut:
            if suanki:
                sonuclar.append(suanki)
                suanki = ""
            for i in range(0, len(cumle), boyut):
                sonuclar.append(cumle[i:i + boyut])
            continue
        if len(suanki) + len(cumle) + 1 > boyut:
            sonuclar.append(suanki)
            suanki = cumle
        else:
            suanki = f"{suanki} {cumle}".strip()
    if suanki:
        sonuclar.append(suanki)
    return sonuclar


def bolumle(olaylar, sayfa_basligi="", boyut=PARCA_BOYUTU):
    """Olay akışını (seviye, metin) chunk'lara böler (2026-11-04 Faz 6):
    - h2/h3'te KESİN bölme: her başlık yeni parça açar ve parçanın ilk satırı
      başlık olur — başlıksız, bağlamsız parça sorunu (Johnson; Anthropic
      Contextual Retrieval) kaynağında çözülür.
    - Aynı kesitte cümle sınırlarına saygılı ~boyut paketleme.
    Dönüş: (bolum_etiketi, temiz_parca) listesi. Etiket YALNIZCA gömme
    girdisine eklenir; saklanan 'icerik' temiz kalır — /api/ara arayüzüne
    bağlam gürültüsü sızmaz (2026-11-04 mimar notu)."""
    def _etiket():
        p = []
        if sayfa_basligi:
            p.append(f"Sayfa: {sayfa_basligi}")
        if h2:
            p.append(f"Bölüm: {h2}")
        if h3:
            p.append(f"Alt bölüm: {h3}")
        return " > ".join(p)

    parcalar = []
    h2 = h3 = ""
    suanki = []

    def _kapat():
        if not suanki:
            return
        metin = "\n".join(suanki)
        metin = metin.replace("\n | \n", " | ")            # hücre sınırları
        metin = metin.replace("\u0001SATIR\u0001\n", "\n")  # tablo satır sonu
        metin = metin.replace("\u0001SATIR\u0001", "")      # parça sonuna kalan işaretçi
        metin = re.sub(r" \|(?=\n|$)", "", metin)           # satır sonu artık '|'
        metin = re.sub(r"[ \t]+", " ", metin)
        metin = re.sub(r"\n{3,}", "\n\n", metin).strip()
        if metin:
            parcalar.append((_etiket(), metin))
        suanki.clear()

    for sev, metin in olaylar:
        if sev == 2:
            _kapat()
            h2, h3 = metin, ""
            suanki.append(metin)
        elif sev == 3:
            _kapat()
            h3 = metin
            suanki.append(metin)
        else:
            # Yapısal ayırıcılar (' | ' hücre sınırı, SATIR işaretçisi) satır
            # bölücünden GEÇMEZ: _satir_bol strip()lediği için boşlukları
            # yutup '|' yapıyordu (2026-11-04 hata ayıklama bulgusu).
            if metin in (" | ", "\u0001SATIR\u0001"):
                suanki.append(metin)
                continue
            for satir in _satir_bol(metin, boyut):
                if sum(len(s) + 1 for s in suanki) + len(satir) + 1 > boyut:
                    _kapat()
                suanki.append(satir)
    _kapat()
    return parcalar


def _belge_ekle(belgeler, kaynak, yol, tarih, url):
    try:
        baslik, olaylar = html_metni(yol)
    except Exception as e:
        print(f"  [atla] {yol}: {e}")
        return
    parcalar = bolumle(olaylar, sayfa_basligi=baslik)
    if sum(len(m) for _, m in parcalar) < 400:
        return
    for i, (etiket, parca) in enumerate(parcalar):
        girdi = f"{etiket}\n{parca}" if etiket else parca
        belgeler.append({
            "kaynak": kaynak,
            "url": url,
            "parca": i,
            "baslik": baslik,
            "bolum": etiket,
            "tarih": tarih,
            "icerik": parca,
            "girdi": girdi,
            "hash": hashlib.sha256(girdi.encode("utf-8")).hexdigest()[:16],
        })


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
            _belge_ekle(belgeler, kaynak, os.path.join(klasor, ad), tarih,
                        f"{SITE_BASE}/{klasor}/{ad}")
    for kaynak, ad in KOK_SAYFALAR:
        if not os.path.isfile(ad):
            continue
        tarih = datetime.fromtimestamp(os.path.getmtime(ad)).strftime("%Y-%m-%d")
        _belge_ekle(belgeler, kaynak, ad, tarih, f"{SITE_BASE}/{ad}")
    for kaynak, klasor in KOK_KLASORLER:
        if not os.path.isdir(klasor):
            continue
        for ad in sorted(os.listdir(klasor)):
            if not ad.endswith(".html"):
                continue
            yol = os.path.join(klasor, ad)
            tarih = datetime.fromtimestamp(os.path.getmtime(yol)).strftime("%Y-%m-%d")
            _belge_ekle(belgeler, kaynak, yol, tarih, f"{SITE_BASE}/{klasor}/{ad}")
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


# PostgREST'e giden satırlar beyaz listeyle sınırlı: _belge_ekle satırlarda
# gömme girdisini de taşıyor ('girdi', hash için) — o kolon DB'de olmadığından
# PostgREST tüm grubu 400 ile reddediyordu (2026-11-04 backfill koşusu; hata
# gövdesi 'girdi'yi sayıyordu, 'bolum' arayan fail-open koşulu yakalayamadı).
_DB_KOLONLARI = ("kaynak", "url", "parca", "baslik", "bolum", "tarih", "icerik", "hash", "embedding")


def sb_upsert(base, anahtar, satirlar):
    import requests

    for i in range(0, len(satirlar), 50):
        grup = [{k: s[k] for k in _DB_KOLONLARI} for s in satirlar[i:i + 50]]
        r = requests.post(
            f"{base}/rest/v1/belgeler",
            params={"on_conflict": "kaynak,url,parca"},
            headers={**_sb_basliklar(anahtar), "Prefer": "resolution=merge-duplicates"},
            json=grup,
            timeout=120,
        )
        if r.status_code == 400 and "bolum" in r.text:
            # 'bolum' kolonu henüz yok (schema.sql 2026-11-04 bloğu uygulanmamış):
            # etiket olmadan yaz — zincir kırılmaz, bağlam yine de gömme
            # girdisinde var (fail-open; 2026-11-04 mimar notu).
            print("[uyari] 'bolum' kolonu yok; bu tur etiketler saklanmadan yazılıyor "
                  "(pipeline/schema.sql içindeki 2026-11-04 bloğunu uygulayın).")
            r = requests.post(
                f"{base}/rest/v1/belgeler",
                params={"on_conflict": "kaynak,url,parca"},
                headers={**_sb_basliklar(anahtar), "Prefer": "resolution=merge-duplicates"},
                json=[{k: v for k, v in s.items() if k in _DB_KOLONLARI and k != "bolum"} for s in grup],
                timeout=120,
            )
        if not r.ok:
            print(f"[upsert-hata] HTTP {r.status_code}: {r.text[:400]}")
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
        vektorler = asyncio.run(embed_et([b["girdi"] for b in yeni], model, batch, concurrency))
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
