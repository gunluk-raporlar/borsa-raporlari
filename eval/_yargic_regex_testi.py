# -*- coding: utf-8 -*-
"""dongu.py'deki yargıç puan regex'inin üç yanıt biçimini de yakaladığı testi."""
import json
import re

from dongu import _yargic_metni

pat = r'puan\\?"?\s*:\s*([0-2])'

# 1) düz metin yanıt
j1 = {"result": {"response": '{"puan": 2}'}, "success": True}
# 2) mesaj-nesnesi yanıt (ilk koşudaki hata biçimi)
j2 = {"result": {"response": {"role": "assistant", "content": '{"puan": 1}'}}, "success": True}
# 3) içiçe dict, dump sonrası kaçışlı tırnak
j3 = {"result": {"response": {"puan": 0}}, "success": True}
# 4) bilinmeyen yapı → tüm gövde dump edilir; result içinde puan yoksa None
j4 = {"result": {"usage": {"total_tokens": 30}}, "success": True}

for ad, j, beklenen in [("duz-metin", j1, 2), ("mesaj-nesnesi", j2, 1), ("dump-dict", j3, 0), ("bilinmeyen", j4, None)]:
    metin = _yargic_metni(j)
    m = re.search(pat, metin or "")
    puan = int(m.group(1)) if m else None
    durum = "OK " if puan == beklenen else "HATA"
    print(durum, ad, "→", puan, "| metin ilk 60:", (metin or "")[:60])
