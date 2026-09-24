# -*- coding: utf-8 -*-
"""Akşam makro snapshot ve deterministik doğrulama testleri.

Çalıştırma: python -m unittest -v test_makro_veri.py
"""
import copy
import os
import unittest
from unittest import mock

import unittest

os.environ.setdefault("AMD_API_KEY", "makro-test")

import bot
import dogrulama
import makro_veri


CANLI = {
    "guncelleme": "23.09 20:07",
    "kaynak": "TradingView ekonomik takvimi test",
    "gostergeler": [
        {"ulke": "Türkiye", "ad": "Enflasyon (yıllık)", "deger": 31.51,
         "birim": "%", "donem": "2026-09-03"},
        {"ulke": "Türkiye", "ad": "ÜFE (yıllık)", "deger": 41.2,
         "birim": "%", "donem": "2026-09-03"},
        {"ulke": "Türkiye", "ad": "Politika faizi", "deger": 37.0,
         "birim": "%", "donem": "2026-09-10"},
        {"ulke": "Türkiye", "ad": "İşsizlik oranı", "deger": 8.1,
         "birim": "%", "donem": "2026-08-31"},
        {"ulke": "Türkiye", "ad": "Büyüme (yıllık)", "deger": 2.3,
         "birim": "%", "donem": "2026-08-31"},
        {"ulke": "Türkiye", "ad": "Cari denge", "deger": 0.04,
         "birim": "", "donem": "2026-09-11"},
        {"ulke": "Türkiye", "ad": "Döviz rezervleri", "deger": 68.44,
         "birim": "", "donem": "2026-09-17"},
        {"ulke": "ABD", "ad": "Enflasyon (yıllık)", "deger": 3.4,
         "birim": "%", "donem": "2026-09-11"},
        {"ulke": "ABD", "ad": "Politika faizi", "deger": 4.0,
         "birim": "%", "donem": "2026-09-16"},
        {"ulke": "ABD", "ad": "İşsizlik oranı", "deger": 7.7,
         "birim": "%", "donem": "2026-09-04"},
        {"ulke": "ABD", "ad": "Büyüme (yıllık)", "deger": 1.5,
         "birim": "%", "donem": "2026-08-26"},
        {"ulke": "ABD", "ad": "Cari denge", "deger": -226.8,
         "birim": "", "donem": "2026-06-24"},
        {"ulke": "Euro Bölgesi", "ad": "Enflasyon (yıllık)", "deger": 3.2,
         "birim": "%", "donem": "2026-09-17"},
        {"ulke": "Euro Bölgesi", "ad": "Politika faizi", "deger": 2.65,
         "birim": "%", "donem": "2026-09-10"},
        {"ulke": "Euro Bölgesi", "ad": "İşsizlik oranı", "deger": 6.4,
         "birim": "%", "donem": "2026-09-01"},
        {"ulke": "Euro Bölgesi", "ad": "Büyüme (yıllık)", "deger": 1.2,
         "birim": "%", "donem": "2026-09-07"},
        {"ulke": "Euro Bölgesi", "ad": "Cari denge", "deger": 27.6,
         "birim": "", "donem": "2026-09-18"},
    ],
    "piyasa": [
        {"symbol": "USDTRY=X", "indicator": "usdtry", "label": "USD/TRY",
         "value": 48.85, "previous": 48.84, "change": 0.02, "unit": "TL",
         "period": "2026-09-23", "category": "FX", "source": "Yahoo Finance",
         "status": "close"},
        {"symbol": "^VIX", "indicator": "vix", "label": "VIX",
         "value": 15.6, "previous": 15.2, "change": 2.63, "unit": "indeks",
         "period": "2026-09-23", "category": "küresel risk",
         "source": "Yahoo Finance", "status": "close"},
        {"symbol": "XU030", "indicator": "bist30", "label": "BIST 30",
         "value": 16000.0, "previous": 15900.0, "change": 0.63, "unit": "puan",
         "period": "2026-09-23", "category": "BIST", "source": "Borsapy",
         "status": "close"},
        {"symbol": "DX-Y.NYB", "indicator": "dxy", "label": "DXY",
         "value": 101.1, "previous": 100.4, "change": 0.7, "unit": "indeks",
         "period": "2026-09-23", "category": "küresel risk",
         "source": "Yahoo Finance", "status": "close"},
        {"symbol": "BZ=F", "indicator": "brent", "label": "Brent Petrol",
         "value": 103.08, "previous": 99.25, "change": 3.86, "unit": "USD/varil",
         "period": "2026-09-23", "category": "emtia", "source": "Yahoo Finance",
         "status": "close"},
    ],
}


class MakroSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = makro_veri.normalize(
            CANLI, "2026-09-23T20:30:00+03:00")
        makro_veri.validate(cls.snapshot, "2026-09-24")
        cls.enflasyon, cls.faiz, cls.gosterge = makro_veri.rate_maps(cls.snapshot)

    def test_snapshot_rapor_tarihi_kisiti(self):
        self.assertEqual(self.snapshot["snapshot_id"], "makro-2026-09-23-evening")
        self.assertEqual(self.snapshot["valid_for_date"], "2026-09-24")
        with self.assertRaises(makro_veri.SnapshotError):
            makro_veri.validate(self.snapshot, "2026-09-25")

    def test_ulke_oranlari_birebir_ayri(self):
        self.assertEqual(self.enflasyon,
                         {"tr": 31.51, "us": 3.4, "eu": 3.2})
        self.assertEqual(self.faiz, {"tr": 37.0, "us": 4.0, "eu": 2.65})
        self.assertEqual(self.gosterge["us"]["growth_yoy"], 1.5)
        self.assertEqual(self.gosterge["eu"]["unemployment_rate"], 6.4)

    def test_prompt_cercevesi_kilitli(self):
        cerceve = makro_veri.frame_text(self.snapshot)
        self.assertIn("[US] ABD / Enflasyon (yıllık): gerçekleşen: 3.4%", cerceve)
        self.assertIn("[EU] Euro Bölgesi / Enflasyon (yıllık): gerçekleşen: 3.2%", cerceve)
        self.assertIn("[US] ABD / Politika faizi: gerçekleşen: 4.0%", cerceve)
        self.assertIn("DEĞİŞTİRME", cerceve)

    def test_canli_hata_tek_kaynaktan_duzelir(self):
        metin = (
            "ABD'de yıllık enflasyon %31,5. Federal Reserve politika faizi %37. "
            "Euro Bölgesi yıllık enflasyon %31,5. "
            "Türkiye yıllık enflasyon %31,5.")
        sonuc = dogrulama.metin_dogrula(
            metin, {}, enflasyon_yuzde=31.51,
            enflasyon_oranlari=self.enflasyon,
            faiz_oranlari=self.faiz,
            makro_gostergeleri=self.gosterge)
        self.assertIn("ABD'de yıllık enflasyon %3,4", sonuc["metin"])
        self.assertIn("Federal Reserve politika faizi %4", sonuc["metin"])
        self.assertIn("Euro Bölgesi yıllık enflasyon %3,2", sonuc["metin"])
        self.assertIn("Türkiye yıllık enflasyon %31,51", sonuc["metin"])

    def test_tum_gostergeler_ve_birimler_duzelir(self):
        metin = (
            "ABD yıllık büyüme %9,9 ve işsizlik %8,8. "
            "Euro Bölgesi cari denge %99,0. "
            "Türkiye ÜFE %50,0.")
        sonuc = dogrulama.metin_dogrula(
            metin, {}, enflasyon_yuzde=31.51,
            enflasyon_oranlari=self.enflasyon,
            faiz_oranlari=self.faiz,
            makro_gostergeleri=self.gosterge)
        self.assertIn("büyüme %1,5", sonuc["metin"])
        self.assertIn("işsizlik %7,7", sonuc["metin"])
        self.assertIn("cari denge 27,6", sonuc["metin"])
        self.assertNotIn("cari denge %", sonuc["metin"])
        self.assertIn("ÜFE %41,2", sonuc["metin"])
        self.assertEqual(len(sonuc["makro_duzeltme"]), 4)

    def test_tarih_sayisi_rezerv_sanilmaz(self):
        metin = ("Türkiye döviz rezervleri 23 Eylül 2026 itibarıyla "
                 "60,0 milyar dolardır.")
        sonuc = dogrulama.makro_gosterge_duzelt(metin, self.gosterge)
        self.assertIn("23 Eylül 2026", sonuc[0])
        self.assertIn("68,44 milyar", sonuc[0])
        self.assertEqual(sonuc[1],
                         [("fx_reserves", "60,0", "68,44")])

    def test_piyasa_snapshotsi_promptta_ve_dogrulanir(self):
        cerceve = makro_veri.frame_text(self.snapshot)
        self.assertIn("USD/TRY: 48.85 TL", cerceve)
        self.assertIn("VIX: 15.6 indeks", cerceve)
        metin = "USD/TRY 50,00 TL, VIX 20,0 ve DXY 100."
        sonuc = dogrulama.piyasa_serileri_duzelt(
            metin, makro_veri.piyasa_map(self.snapshot))
        self.assertIn("USD/TRY 48,85 TL", sonuc[0])
        self.assertIn("VIX 15,6", sonuc[0])
        self.assertIn("DXY 101,1", sonuc[0])
        self.assertEqual(len(sonuc[1]), 3)
        karistirilmemis, duzeltme = dogrulama.piyasa_serileri_duzelt(
            "Kontrol altında tutuluyor; BIST 30 endeksi 15.000 puan.",
            makro_veri.piyasa_map(self.snapshot))
        self.assertIn("altında", karistirilmemis)
        self.assertIn("BIST 30 endeksi 15.000", karistirilmemis)
        self.assertEqual(duzeltme, [])

    def test_birimsiz_kayit_yuzde_birimiyle_gecilmez(self):
        bozuk = copy.deepcopy(self.snapshot)
        for rec in bozuk["records"]:
            if rec["indicator"] == "growth_yoy":
                rec["unit"] = "puan"
        with self.assertRaises(makro_veri.SnapshotError):
            makro_veri.validate(bozuk)

    def test_butun_zorunlu_ulke_kayitlari_gerekir(self):
        eksik = copy.deepcopy(self.snapshot)
        eksik["records"] = [r for r in eksik["records"]
                            if not (r["country_code"] == "EU"
                                    and r["indicator"] == "policy_rate")]
        with self.assertRaises(makro_veri.SnapshotError):
            makro_veri.validate(eksik)

    def test_us_unemployment_ve_u6_ayri(self):
        snapshot = bot.makro_snapshot_cek("2026-09-24")
        values = {r["indicator"]: r["value"] for r in snapshot["records"]
                  if r["country_code"] == "US"}
        self.assertEqual(values["unemployment_rate"], 4.1)
        self.assertEqual(values["u6_unemployment"], 7.7)

    def test_bot_snapshot_ve_canli_veri_ayrimi(self):
        snapshot = bot.makro_snapshot_cek("2026-09-24")
        self.assertEqual(snapshot["snapshot_id"], "makro-2026-09-23-evening")
        canli = dict(CANLI, _veri_durumu="canli")
        self.assertEqual(canli["_veri_durumu"], "canli")
        cache = dict(CANLI, _veri_durumu="cache")
        self.assertNotEqual(cache["_veri_durumu"], "canli")
    def test_tuik_sdmx_secici_ve_resmi_kaynak(self):
        yapi = [
            {"id": "GROUP", "name": "Group", "type": "series", "position": 0,
             "values": [{"id": "_T", "name": "Total"}]},
            {"id": "RATE", "name": "Change", "type": "series", "position": 1,
             "values": [{"id": "A", "name": "Annual rate of change"},
                        {"id": "M", "name": "Monthly rate of change"}]},
            {"id": "TIME_PERIOD", "name": "Time", "type": "observation",
             "position": 0, "values": [{"id": "2026-08", "name": "2026-08"}]},
        ]
        key, matched = bot._tuik_key(yapi, ("total", "annual rate of change"))
        self.assertTrue(matched)
        self.assertEqual(key, "_T.A")
        fake = {"structure": {"dimensions": {"series": yapi[:2],
                                             "observation": yapi[2:]}},
                "dataSets": [{"series": {"0:0": {"observations": {
                    "0": [31.51]}}}}]}
        rows = bot._tuik_satirlar(fake)
        self.assertEqual(rows[0]["GROUP"], "Total")
        self.assertEqual(rows[0]["RATE"], "Annual rate of change")
        self.assertEqual(rows[0]["TIME_PERIOD"], "2026-08")
        self.assertEqual(rows[0]["value"], 31.51)
        with mock.patch.dict(os.environ, {"TUIK_API_KEY": "test-key"}):
            with mock.patch.object(bot, "_tuik_veri", return_value=rows):
                resmi = bot._tuik_seride_guncel("inflation_yoy", "2026-09-23")
        self.assertEqual(resmi["value"], 31.51)
        self.assertEqual(resmi["period"], "2026-08-01")

    def test_tuik_resmi_deger_tradingview_satirini_ezer(self):
        gostergeler = [{"ulke": "Türkiye", "ad": "Enflasyon (yıllık)",
                        "deger": 30.0, "birim": "%", "donem": "2026-09-03"}]

        def resmi(gosterge, as_of=None):
            if gosterge == "inflation_yoy":
                return {"label": "Enflasyon (yıllık)", "value": 31.51,
                        "unit": "%", "period": "2026-08-01",
                        "source_title": "DF_TUFE_SDMX_TT03",
                        "source_period": "2026-08", "frequency": "TÜİK"}
            return None

        with mock.patch.dict(os.environ, {"TUIK_API_KEY": "test-key"}):
            with mock.patch.object(bot, "_tuik_seride_guncel", side_effect=resmi):
                self.assertTrue(bot._tuik_teyit(gostergeler, as_of="2026-09-23"))
        self.assertEqual(gostergeler[0]["deger"], 31.51)
        self.assertEqual(gostergeler[0]["donem"], "2026-08-01")
        self.assertEqual(gostergeler[0]["teyit"], "TÜİK")
        self.assertEqual(gostergeler[0]["kaynak_durumu"], "TÜİK resmi verisi")


if __name__ == "__main__":
    unittest.main()
