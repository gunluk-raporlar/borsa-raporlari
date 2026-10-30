#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dil secici baglantilarini son dosya durumuna gore tazeler.

Kullanim:
    python dil_baglantilari_tazele.py .            # yerinde duzelt
    python dil_baglantilari_tazele.py dist         # yayin agacinda duzelt
    python dil_baglantilari_tazele.py . --denetle  # yazmadan raporla (CI kapisi)

30.10.2026 kullanici bildirimi: /hisse/ sayfasinda dil secilince ana sayfa
aciliyordu. Kok neden bolum index'lerinin ({dil}/{yol}/index.html) hedef
cozumleyicisinde taninmamasiydi. Bu betik mevcut sayfalari da onarir; boylece
yeniden uretilmesini beklemeden yayindaki menu duzelir.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dil_yollari import menu_tazele  # noqa: E402

SITE_URL = "https://borsa-raporlari.pages.dev/"
ATLA = {".git", "node_modules", "__pycache__", "web", "functions", "data",
        "docs", "ajan-uyumlulugu"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Dil secici baglantilarini tazele")
    ap.add_argument("kok", nargs="?", default=".", help="Site kok dizini")
    ap.add_argument("--site-url", default=SITE_URL)
    ap.add_argument("--denetle", action="store_true",
                    help="Yazmadan yalnizca degisecek dosyalari raporla")
    args = ap.parse_args()

    kok = Path(args.kok).resolve()
    if not kok.is_dir():
        print(f"HATA: dizin yok: {kok}", file=sys.stderr)
        return 2

    dosya_sayisi = baglanti_sayisi = 0
    for dosya in sorted(kok.rglob("*.html")):
        rel = dosya.relative_to(kok)
        if any(p in ATLA for p in rel.parts[:-1]):
            continue
        try:
            html = dosya.read_text(encoding="utf-8")
        except OSError:
            continue
        yeni, n = menu_tazele(html, rel, kok, args.site_url)
        if not n:
            continue
        dosya_sayisi += 1
        baglanti_sayisi += n
        print(f"{'DEGISECEK' if args.denetle else 'DUZELTILDI'}: {rel} ({n} baglanti)")
        if not args.denetle:
            with open(dosya, "w", encoding="utf-8", newline="\n") as f:
                f.write(yeni)

    print(f"Toplam: {dosya_sayisi} sayfa, {baglanti_sayisi} dil baglantisi"
          + (" (denetim; yazilmadi)" if args.denetle else ""))
    return 1 if (args.denetle and dosya_sayisi) else 0


if __name__ == "__main__":
    sys.exit(main())
