"""Ana sayfayi (index.html) taze agactan yeniden uretir.

Neden ayri betik: bot.py modul duzeyinde "en az bir LLM anahtari" sarti
koyuyor (bot.py:207). Ana sayfa uretimi hicbir LLM cagrisi yapmadigi halde
bu sart yuzunden `import bot` anahtarsiz ortamda SystemExit veriyordu; yani
yayin akisi (publish.yml) ana sayfayi tazeleyemiyordu. Burada anahtar YOKSA
yer tutucu atanir; gercek anahtar tanimliysa dokunulmaz.

Kullanim:
    python anasayfa_tazele.py
"""
import os
import sys

# bot.py'nin modul duzeyi anahtar kapisi icin yer tutucu. LLM cagrilmaz.
# Bos dize de "tanimsiz" sayilir: aksi halde anahtar bos gecirilen bir akista
# kapi yine SystemExit verirdi.
if not os.environ.get("ALT_API_KEY"):
    os.environ["ALT_API_KEY"] = "anasayfa-tazeleme-llm-kullanmaz"

import bot  # noqa: E402  (anahtar yer tutucusundan SONRA gelmeli)


def main():
    bot.ana_sayfa_tazele()
    print("Ana sayfa tazelendi: index.html", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
