#!/usr/bin/env python3
"""IndexNow pingleyici: yeni/guncellenen sayfalari Bing, Yandex ve Seznam'a aninda bildirir.

Kullanim:
  python indexnow_ping.py --before <sha> --after <sha>  # iki commit arasinda degisen sayfalar
  python indexnow_ping.py --all                         # sitemap.xml'deki tum URL'ler
  python indexnow_ping.py ... --dry-run                 # POST atmadan sadece listele

Anahtar: kok dizindeki 7769bc8f58bfd3b7b2094fd91fbfd31d.txt (icerigi anahtarla ayni).
Dosya yenilenirse asagidaki ANAHTAR sabitini de guncelleyin.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SITE = "https://borsa-raporlari.pages.dev"
HOST = "borsa-raporlari.pages.dev"
ENDPOINT = "https://api.indexnow.org/indexnow"
ANAHTAR = "7769bc8f58bfd3b7b2094fd91fbfd31d"
ATLANAN = {"404.html", "onizleme-mobil.html"}  # noindex'li sayfalar


def yol_to_url(yol: str):
    """repo yolunu temiz (uzantisiz) site URL'sine cevirir; atlanacaklar None."""
    yol = yol.replace("\\", "/")
    if yol.startswith("dist/"):  # yerel build ciktisi; garanti altina al
        return None
    if yol in ATLANAN or not yol.endswith(".html"):
        return None
    if yol.endswith("index.html"):
        return SITE + "/" + yol[: -len("index.html")]
    return SITE + "/" + yol[: -len(".html")]


def degisen_url_listesi(before: str, after: str):
    """Iki commit arasinda eklenen/degisen .html dosyalarini temiz URL'lere cevirir."""
    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", before, after, "--", "*.html"]
    sonuc = subprocess.run(cmd, capture_output=True, text=True)
    if sonuc.returncode != 0:
        return None  # sig klon vb. — cagiran sitemap'e dusar
    return [u for u in map(yol_to_url, sonuc.stdout.splitlines()) if u]


def sitemap_url_listesi():
    metin = Path("sitemap.xml").read_text(encoding="utf-8")
    return re.findall(r"<loc>([^<]+)</loc>", metin)


def gonder(url_listesi, dry_run: bool) -> int:
    url_listesi = sorted(set(url_listesi))
    if not url_listesi:
        print("Gonderilecek degisen sayfa yok; IndexNow cagrilmadi.")
        return 0
    print(f"{len(url_listesi)} URL gonderilecek" + (" (dry-run)" if dry_run else "") + ":")
    for u in url_listesi[:20]:
        print("  ", u)
    if len(url_listesi) > 20:
        print(f"   ... ve {len(url_listesi) - 20} tane daha")
    if dry_run:
        return 0
    veri = json.dumps({
        "host": HOST,
        "key": ANAHTAR,
        "keyLocation": f"{SITE}/{ANAHTAR}.txt",
        "urlList": url_listesi,
    }).encode("utf-8")
    istek = urllib.request.Request(
        ENDPOINT, data=veri, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(istek, timeout=30) as cevap:
            print("IndexNow yaniti:", cevap.status, cevap.read(200).decode(errors="replace"))
    except urllib.error.HTTPError as h:
        print(f"IndexNow hatasi: {h.code}", h.read(200).decode(errors="replace"))
        if h.code == 403:
            print("403: anahtar dosyasi sitede bulunamadi — deploy'un bittiginden emin olun.")
        elif h.code == 429:
            print("429: istek limiti asildi; bir sonraki kosuda denenecek.")
        return 1
    except urllib.error.URLError as u:
        print("IndexNow'a ulasilamadi:", u.reason)
        return 1
    return 0


def main():
    p = argparse.ArgumentParser(description="Yeni/guncellenen sayfalari IndexNow'a bildirir")
    p.add_argument("--before", default="", help="onceki commit sha (push event'inden)")
    p.add_argument("--after", default="HEAD", help="yeni commit sha")
    p.add_argument("--all", action="store_true", help="diff yerine sitemap'teki tum URL'leri gonder")
    p.add_argument("--dry-run", action="store_true", help="POST atma, sadece listele")
    a = p.parse_args()

    if a.all:
        url_listesi = sitemap_url_listesi()
    else:
        before = a.before
        if not before or set(before) == {"0"}:  # ilk push / force-push
            before = "HEAD~1"
        url_listesi = degisen_url_listesi(before, a.after)
        if url_listesi is None:
            print("Diff alinamadi (sig klon?) — sitemap fallback.")
            url_listesi = sitemap_url_listesi()
    sys.exit(gonder(url_listesi, a.dry_run))


if __name__ == "__main__":
    main()
