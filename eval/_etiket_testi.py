# -*- coding: utf-8 -*-
"""yaz() düğümünün etiketler.jsonl kalıcılığını sahte veriyle sınar.

DATA_KOK geçici klasöre yönlendirilir; gerçek data/eval dokunulmaz.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dongu

gecici = tempfile.mkdtemp(prefix="etiket-testi-")
dongu.DATA_KOK = gecici

durum = {
    "kuru": False,
    "sorgular": ["garanti bankası haftalık değerlendirme"],
    "kol_sonuc": {"normal": {"garanti bankası haftalık değerlendirme": [
        {"baslik": "Günlük Piyasa Raporu", "kaynak": "gunluk-rapor",
         "tarih": "2026-11-02", "icerik": "GARAN Garanti BBVA analiz metni..."},
        {"baslik": "Muhasebe Terimleri", "kaynak": "muhasebe-terimleri",
         "tarih": "2026-11-02", "icerik": "Guarantee amount Garanti tutarı..."},
    ]}, "ham": {}, "rerank": {}},
    "puanlar": {"normal": {"garanti bankası haftalık değerlendirme": {0: 2, 1: 1}},
                "ham": {}, "rerank": {}},
    "metrikler": {"normal": {"ndcg": None, "mrr": None},
                  "ham": {"ndcg": None, "mrr": None},
                  "rerank": {"ndcg": None, "mrr": None}},
    "bos": {"normal": 0, "ham": 0, "rerank": 0},
    "yargic_hata": 0, "yargic_yok": False,
    "karar": "olcum", "degisen": None, "gecmis": [], "mevcut_ayar": {},
}

dongu.yaz(durum)

yol = os.path.join(gecici, "etiketler.jsonl")
satirlar = [json.loads(s) for s in open(yol, encoding="utf-8")]
assert len(satirlar) == 2, f"beklenen 2 satır, gelen {len(satirlar)}"
assert satirlar[0]["puan"] == 2 and satirlar[0]["sira"] == 0
assert satirlar[1]["puan"] == 1 and "icerik" in satirlar[1]
assert "ip" not in satirlar[0] and "oturum" not in satirlar[0]
print("OK etiketler.jsonl:", len(satirlar), "satır; alanlar:", sorted(satirlar[0].keys()))
