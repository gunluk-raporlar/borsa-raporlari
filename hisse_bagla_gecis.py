# -*- coding: utf-8 -*-
"""Mevcut rapor sayfalarina hisse baglama katmanini uygular (2026-11-01).

bot.py, raporlari yalnizca URETILDIKLERI gun linkler; katmandan once
uretilmis sayfalar baglantisiz, sonraki duzeltmeler (kod basina tek link ->
tum gecisler) ise yalniz yeni sayfalara dusuyordu. Bu betik her raporun
govdesini okuyup bot._hisse_baglantilari_uygula ile yeniden baglar;
degismeyen dosyalara dokunmaz. Idempotenttir: mevcut <a> icindeki gecisler
atlama kurallarina tabi oldugundan tekrar kosmak zarar vermez.

Kapsam: raporlar/, derin-analiz/, makro-analiz/ (rapor_sayfasi'nin
kapsadigi uc uretici). LLM cagrisi YOK; saf metin isleme.
"""
import os
import re

import bot

KOKLER = ("raporlar", "derin-analiz", "makro-analiz")
GOVDE_RE = re.compile(r'(<article class="report"[^>]*>)(.*?)(</article>)', re.S)


def main():
    degisen, dokunulmayan = 0, 0
    for klasor in KOKLER:
        if not os.path.isdir(klasor):
            continue
        for fn in sorted(os.listdir(klasor)):
            if not fn.endswith(".html"):
                continue
            yol = os.path.join(klasor, fn)
            with open(yol, encoding="utf-8") as f:
                html = f.read()
            m = GOVDE_RE.search(html)
            if not m:
                print(f"[atlandi] {yol}: rapor-govde bulunamadi")
                continue
            yeni_govde = bot._hisse_baglantilari_uygula(m.group(2), kok="../")
            if yeni_govde == m.group(2):
                dokunulmayan += 1
                continue
            with open(yol, "w", encoding="utf-8", newline="\n") as f:
                f.write(html[:m.start(2)] + yeni_govde + html[m.end(2):])
            degisen += 1
    print(f"Sonuc: {degisen} dosyaya baglanti eklendi, {dokunulmayan} dosya zaten guncel.")


if __name__ == "__main__":
    main()
