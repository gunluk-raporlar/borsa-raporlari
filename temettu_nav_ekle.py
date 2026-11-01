# -*- coding: utf-8 -*-
"""Üst menüye "Temettü" bağlantısını ekler (tek seferlik / idempotent).

bot.py her koşuda sayfaları yeniden ürettiği için asıl kaynak bot.py'dir; bu script
hem bot.py şablonunu hem de depodaki üretilmiş HTML dosyalarını günceller.
"""
import glob
import os
import re

KOK = os.path.dirname(os.path.abspath(__file__))
DESEN = re.compile(r'<a href="([^"]*?)takvim(?:\.html)?"([^>]*)>📅 Takvim</a>')
YENI = r'\1<a href="\2temettu.html"{aktif}>💰 Temettü</a>'


def bot_py_guncelle():
    yol = os.path.join(KOK, "bot.py")
    with open(yol, encoding="utf-8") as f:
        s = f.read()
    if "temettu.html" in s:
        print("bot.py: zaten ekli")
        return
    # nav satırındaki takvim bağlantısından sonra ekle
    hedef = '<a href="{kok}takvim.html"{a_tkv}>📅 Takvim</a>'
    if hedef not in s:
        print("bot.py: takvim bağlantısı bulunamadı")
        return
    s = s.replace(hedef, hedef + '<a href="{kok}temettu.html"{a_tm}>💰 Temettü</a>', 1)
    # aktif sınıfı değişkeni
    s = s.replace('    a_tkv = \' class="active"\' if aktif == "takvim" else ""',
                  '    a_tkv = \' class="active"\' if aktif == "takvim" else ""\n'
                  '    a_tm = \' class="active"\' if aktif == "temettu" else ""', 1)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(s)
    print("bot.py: menü güncellendi")


def html_guncelle():
    sayac = 0
    for yol in glob.glob(os.path.join(KOK, "**", "*.html"), recursive=True):
        if "/de/" in yol or "/en/" in yol or "/ru/" in yol or "/zh/" in yol:
            continue
        with open(yol, encoding="utf-8") as f:
            s = f.read()
        if "temettu.html" in s:
            continue
        if not DESEN.search(s):
            continue
        yeni = DESEN.sub(
            lambda m: (m.group(0) + f'<a href="{m.group(1)}temettu">💰 Temettü</a>'), s)
        if yeni != s:
            with open(yol, "w", encoding="utf-8") as f:
                f.write(yeni)
            sayac += 1
    print(f"{sayac} HTML dosyasında menü güncellendi")


if __name__ == "__main__":
    bot_py_guncelle()
    html_guncelle()
