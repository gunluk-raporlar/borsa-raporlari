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

    def test_tuik_sanayi_endeks_seviyesi_degil_degisim_secilir(self):
        # Endeks seviyesi satiri ("Monthly Change Index (2021=100)") ayni
        # donemde % degisim satiriyla yarisir; exclude kutusu onu elemeli.
        rows = [
            {"KAPSAM": "Total", "OLCU": "Monthly Change Index (2021=100)",
             "TIME_PERIOD": "2026-08", "value": 113.8051},
            {"KAPSAM": "Total", "OLCU": "Monthly rate of change",
             "TIME_PERIOD": "2026-08", "value": 1.2},
            {"KAPSAM": "Total", "OLCU": "Annual rate of change",
             "TIME_PERIOD": "2026-08", "value": 11.5},
        ]
        with mock.patch.dict(os.environ, {"TUIK_API_KEY": "test-key"}):
            with mock.patch.object(bot, "_tuik_veri", return_value=rows):
                resmi = bot._tuik_seride_guncel(
                    "industrial_production_mom", "2026-09-23")
        self.assertIsNotNone(resmi)
        self.assertEqual(resmi["value"], 1.2)
        self.assertEqual(resmi["period"], "2026-08-01")
        self.assertEqual(resmi["unit"], "%")


class RejimTest(unittest.TestCase):
    """makro_rejim: _bul yillik tercihi (bug 1) + piyasa kanallari (Faz 2)."""

    G = {
        ("Türkiye", "Enflasyon (aylık)"): 0.22,
        ("Türkiye", "Enflasyon (yıllık)"): 33.79,
        ("Türkiye", "Politika faizi"): 37.0,
        ("Türkiye", "Büyüme (çeyreklik)"): 1.1,
        ("Türkiye", "Büyüme (yıllık)"): 2.3,
        ("Türkiye", "İşsizlik oranı"): 8.1,
        ("Türkiye", "Cari denge"): 0.04,
        ("Türkiye", "Bütçe Dengesi"): 12.9,
        ("ABD", "Enflasyon (yıllık)"): 3.4,
        ("ABD", "Politika faizi"): 4.0,
        ("Euro Bölgesi", "Enflasyon (yıllık)"): 3.2,
        ("Euro Bölgesi", "Politika faizi"): 2.65,
    }

    def test_bul_yillik_kirilimi_secer(self):
        import makro_rejim
        # Snapshot sirasinda aylik satir once gelir; yillik secilmeli.
        self.assertEqual(makro_rejim._bul(self.G, "Türkiye", "Enflasyon"), 33.79)
        self.assertEqual(makro_rejim._bul(self.G, "Türkiye", "Büyüme"), 2.3)
        self.assertEqual(makro_rejim._bul(self.G, "Türkiye", "Politika faizi"), 37.0)
        self.assertIsNone(makro_rejim._bul(self.G, "Türkiye", "Sanayi"))
        self.assertEqual(
            makro_rejim._bul({("Türkiye", "Cari denge"): 0.0},
                             "Türkiye", "Cari denge"),
            0.0)

    def test_rejim_reel_faiz_yillik_enflasyonla_hesaplanir(self):
        import makro_rejim
        rejim = makro_rejim.rejim_hesapla(self.G, piyasa={})
        self.assertEqual(rejim["enflasyon"], 33.79)
        self.assertEqual(rejim["buyume"], 2.3)
        self.assertEqual(rejim["reel_faiz"], 3.21)
        self.assertIn("yüksek enflasyon", rejim["etiket"])
        self.assertIn("nötr para politikası", rejim["etiket"])

    def test_piyasa_kanallari_ve_dinamik_eksik_listesi(self):
        import makro_rejim
        piyasa = {"usdtry": {"value": 48.85, "change": 0.35},
                  "brent": {"value": 103.08, "change": 3.86},
                  "vix": {"value": 15.6, "change": 2.63},
                  "dxy": {"value": 101.1, "change": 0.7},
                  "ust10y": {"value": 4.21, "change": 0.05}}
        rejim = makro_rejim.rejim_hesapla(self.G, piyasa=piyasa)
        self.assertEqual(rejim["piyasa_sinyalleri"], {"kur": 1, "emtia": 1})
        self.assertEqual(rejim["global_risk"]["vix"], 15.6)
        for kanal in ("bütçe", "kur (TL değişimi)", "emtia",
                      "global risk (VIX/DXY/UST10Y/Brent)"):
            self.assertNotIn(kanal, rejim["eksik_kanallar"])
        self.assertIn("CDS", rejim["eksik_kanallar"])
        self.assertIn("REER", rejim["eksik_kanallar"])
        self.assertIn("kredi büyümesi / M3", rejim["eksik_kanallar"])
        sinyal = makro_rejim.sinyaller(rejim)
        self.assertEqual((sinyal["kur"], sinyal["emtia"]), (1, 1))
        aktarim = makro_rejim.sektor_aktarimi(rejim)
        self.assertTrue(any("kur" in k
                            for k in aktarim["Perakende"]["kanallar"]))
        # Piyasa blogu yoksa kanallar None; eski davranis korunur.
        rejim2 = makro_rejim.rejim_hesapla(self.G, piyasa={})
        self.assertEqual(rejim2["piyasa_sinyalleri"],
                         {"kur": None, "emtia": None})
        self.assertIn("emtia", rejim2["eksik_kanallar"])


class FrekansVeEtiketTest(unittest.TestCase):
    """dogrulama: binlik sayi, frekans kirilimi, gram altin etiketi."""

    def test_yuzde_degeri_turkce_binlik_ayraci(self):
        self.assertEqual(dogrulama._yuzde_degeri("4.286"), 4286.0)
        self.assertEqual(dogrulama._yuzde_degeri("31,51"), 31.51)
        self.assertEqual(dogrulama._yuzde_degeri("16.372,83"), 16372.83)
        self.assertEqual(dogrulama._yuzde_degeri("3.4"), 3.4)

    def test_aylik_enflasyon_yillik_oranla_karistirilmaz(self):
        metin = "Türkiye'de aylık enflasyon %1,84 olarak hesaplandı."
        aylik = dogrulama.metin_dogrula(
            metin, {}, enflasyon_yuzde=31.51,
            enflasyon_oranlari={"tr": 31.51},
            enflasyon_aylik_oranlari={"tr": 0.22})
        self.assertIn("aylık enflasyon %0,22", aylik["metin"])
        self.assertEqual(len(aylik["enflasyon_duzeltme"]), 1)
        # Aylik kirilim verisi yoksa yillik orani yazmak yerine dokunulmaz.
        verisiz = dogrulama.metin_dogrula(
            metin, {}, enflasyon_yuzde=31.51,
            enflasyon_oranlari={"tr": 31.51})
        self.assertEqual(verisiz["enflasyon_duzeltme"], [])
        yillik = dogrulama.metin_dogrula(
            "Türkiye'de yıllık enflasyon %28,4.", {}, enflasyon_yuzde=31.51,
            enflasyon_oranlari={"tr": 31.51},
            enflasyon_aylik_oranlari={"tr": 0.22})
        self.assertIn("yıllık enflasyon %31,51", yillik["metin"])

    def test_sanayi_uretimi_frekans_kirilimi_ve_dedupe(self):
        gost = {"tr": {"industrial_production_yoy": 4.5,
                       "industrial_production_mom": 1.2,
                       "growth_yoy": 2.3, "growth_qoq": 1.1}}
        aylik, ay_d = dogrulama.makro_gosterge_duzelt(
            "Sanayi üretimi aylık %5,0 arttı.", gost)
        self.assertIn("%1,2", aylik)
        self.assertEqual(len(ay_d), 1)
        yillik, yl_d = dogrulama.makro_gosterge_duzelt(
            "Sanayi üretimi yıllık %5,0 arttı.", gost)
        self.assertIn("%4,5", yillik)
        self.assertEqual(len(yl_d), 1)
        ceyrek, cq_d = dogrulama.makro_gosterge_duzelt(
            "Çeyreklik büyüme %5,0 arttı.", gost)
        self.assertIn("%1,1", ceyrek)
        self.assertEqual(len(cq_d), 1)
        # Ipucu yoksa yillik varsayilir; ayni sayiya ikinci aday uygulanmaz.
        varsayilan, vt_d = dogrulama.makro_gosterge_duzelt(
            "Sanayi üretimi %5,0 arttı.", gost)
        self.assertIn("%4,5", varsayilan)
        self.assertEqual(len(vt_d), 1)

    def test_gram_altin_etiketi_altinla_karismaz(self):
        seriler = [
            {"indicator": "gold_try", "label": "Gram Altın (türetilmiş)",
             "value": 6731.74, "unit": "TL/gram"},
            {"indicator": "gold_usd", "label": "Altın", "value": 4286.30,
             "unit": "USD/ons"},
        ]
        cikti, duzeltme = dogrulama.piyasa_serileri_duzelt(
            "Gram altın 5.000 TL'den, ons altın 4.280 dolardan işlem gördü.",
            seriler)
        self.assertIn("6.731,74", cikti)
        self.assertIn("4.286,3", cikti)
        self.assertEqual(len(duzeltme), 2)
        _, dokunma = dogrulama.piyasa_serileri_duzelt(
            "Gram altın 6.731 TL, ons altın 4.286 dolar.", seriler)
        self.assertEqual(dokunma, [])

    def test_gram_altin_fiyati_formulu(self):
        import bot
        # 4.286 USD/ons x 48,85 TL/USD -> ~6.731 TL/gram (milyon kat degil).
        deger = bot._gram_altin_fiyati(4286.0, 48.85)
        self.assertAlmostEqual(deger, 4286.0 * 48.85 / 31.1034768, places=6)
        self.assertLess(deger, 10000)


class EvdsTest(unittest.TestCase):
    """evds_veri: anahtarsiz atlama, kesif, sutun dogrulamasi."""

    def setUp(self):
        import pathlib
        import tempfile
        import evds_veri
        self.evds = evds_veri
        self._tmp = tempfile.TemporaryDirectory()
        kod_yolu = pathlib.Path(self._tmp.name) / "evds-kodlar.json"
        self._yamalar = [
            mock.patch.dict(os.environ, {"EVDS_API_KEY": "test-anahtar"}),
            mock.patch.object(evds_veri, "KOD_DOSYA", kod_yolu),
            # Kesif testleri elle sabitlenmis kodlarin one cikmasini istemez;
            # pin davranisi ayri testte (test_ele_kodlar...) ele alinir.
            mock.patch.object(evds_veri, "ELLE_KODLAR", {}),
        ]
        for y in self._yamalar:
            y.start()

    def tearDown(self):
        for y in reversed(self._yamalar):
            y.stop()
        self._tmp.cleanup()

    def test_anahtar_yoksa_hicbir_sey_eklenmez(self):
        with mock.patch.dict(os.environ, {"EVDS_API_KEY": ""}):
            liste = []
            self.assertEqual(self.evds.gostergeleri_ekle(liste, "2026-09-24"), 0)
            self.assertEqual(liste, [])

    def test_evds_url_yol_icinde_anhtar_headerda(self):
        # EVDS3 '?'-li URL'ye 400 "Missing parameters" dondurur; parametreler
        # yolun icine, anahtar yalnizca header'a yazilir (24-09-2026 canli
        # denetim kosusuyla dogrulandi - regresyon kilidi).
        yakalanan = {}

        class SahteYanit:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"[]"

        def sahte_urlopen(istek, timeout=None):
            yakalanan["url"] = istek.full_url
            yakalanan["header"] = dict(istek.header_items())
            return SahteYanit()

        with mock.patch.object(self.evds.urllib.request, "urlopen",
                               sahte_urlopen):
            sonuc = self.evds._istek(
                self.evds.KOK + "datagroups/",
                {"mode": 0, "code": "", "type": "json"},
                anahtar_deger="TEST123")
        self.assertEqual(sonuc, [])
        self.assertTrue(
            yakalanan["url"].endswith("/datagroups/mode=0&code=&type=json"),
            yakalanan["url"])
        self.assertNotIn("?", yakalanan["url"])
        self.assertNotIn("TEST123", yakalanan["url"])
        anhtarli = [v for k, v in yakalanan["header"].items()
                    if k.lower() == "key"]
        self.assertEqual(anhtarli, ["TEST123"])

    def test_anahtar_gecersizse_kesif_dahil_atlanir(self):
        def hatali(url, parametreler=None, anahtar_deger=None):
            raise self.evds.EvdsHata("EVDS anahtari gecersiz (HTTP 401)",
                                     durum="anahtar")
        with mock.patch.object(self.evds, "_istek", side_effect=hatali):
            liste = []
            self.assertEqual(self.evds.gostergeleri_ekle(liste, "2026-09-24"), 0)
            self.assertEqual(liste, [])

    def test_kesif_onbellegi_ve_makro_kaydi(self):
        def sahte(url, parametreler=None, anahtar_deger=None):
            if url.endswith("datagroups/"):
                return [{"CATEGORY_ID": 4, "DATAGROUP_CODE": "bie_mevduat",
                         "DATAGROUP_NAME": "Mevduat Faizleri",
                         "DATAGROUP_NAME_ENG": "Deposit Rates"}]
            if url.endswith("serieList/"):
                return [{"SERIE_CODE": "TP.MBM.TUM",
                         "SERIE_NAME": "Tüm Bankalar Mevduat Faizi (Aylık, %)"},
                        {"SERIE_CODE": "TP.MBM.TL",
                         "SERIE_NAME": "TL Vadeli Mevduat Faizi"}]
            if url == self.evds.KOK:
                return {"items": [
                    {"DATE": "01-08-2026 00:00:00", "TP_MBM_TUM": "35.5"},
                    {"DATE": "01-09-2026 00:00:00", "TP_MBM_TUM": "36.25"}]}
            return None

        with mock.patch.object(self.evds, "_istek", side_effect=sahte):
            liste = []
            sayi = self.evds.gostergeleri_ekle(liste, "2026-09-24")
        # Diger EVDS hedefleri bu sahte katalogda eslesmez -> yalniz mevduat.
        self.assertEqual(sayi, 1)
        kayit = liste[0]
        self.assertEqual(kayit["ulke"], "Türkiye")
        self.assertEqual(kayit["ad"], "Mevduat Faizi")
        self.assertEqual(kayit["deger"], 36.25)
        self.assertEqual(kayit["onceki"], 35.5)
        self.assertEqual(kayit["birim"], "%")
        self.assertEqual(kayit["donem"], "2026-09-01")
        self.assertIn("TP.MBM.TUM", kayit["kaynak_baslik"])
        # Kesfedilen kod onbellege yazildi (sonraki calismada dogrudan kullanilir).
        onbellek = self.evds._kod_onbellegi()
        self.assertEqual(onbellek["deposit_rate"]["kod"], "TP.MBM.TUM")

    def test_sutun_anahtari_eslesmezse_uydurma_deger_yazilmaz(self):
        def sahte(url, parametreler=None, anahtar_deger=None):
            if url.endswith("datagroups/"):
                return [{"DATAGROUP_CODE": "bie_mevduat",
                         "DATAGROUP_NAME": "Mevduat Faizleri"}]
            if url.endswith("serieList/"):
                return [{"SERIE_CODE": "TP.MBM.TUM",
                         "SERIE_NAME": "Tüm Bankalar Mevduat Faizi"}]
            if url == self.evds.KOK:
                return {"items": [{"DATE": "01-09-2026 00:00:00",
                                   "YANLIS_KOLON": "36.25"}]}
            return None

        with mock.patch.object(self.evds, "_istek", side_effect=sahte):
            liste = []
            self.assertEqual(self.evds.gostergeleri_ekle(liste, "2026-09-24"), 0)
            self.assertEqual(liste, [])

    def test_tarih_yazi_aylik_ve_yillik_bicimleri(self):
        # Aylik frekansli istek 'Tarih':'2025-8' dondurur; bu bicim
        # islenmezse TUM satirlar duser ve 'gecerli gozlem yok' uretilirdi
        # (25-09-2026 canli denetimde goruldu).
        self.assertEqual(self.evds._tarih_yaz("2025-8"), "2025-08-01")
        self.assertEqual(self.evds._tarih_yaz("2025-10"), "2025-10-01")
        self.assertEqual(self.evds._tarih_yaz("2025"), "2025-01-01")
        self.assertEqual(self.evds._tarih_yaz("01-09-2026"), "2026-09-01")
        self.assertEqual(self.evds._tarih_yaz("24-09-2026 00:00:00"),
                         "2026-09-24")
        self.assertEqual(self.evds._tarih_yaz("2026-09-24"), "2026-09-24")
        self.assertIsNone(self.evds._tarih_yaz("bozuk"))
        self.assertIsNone(self.evds._tarih_yaz(None))

    def test_aylik_istek_hizalanir_ve_formul_son_egli_kolon_kabul(self):
        # Kullanim kilavuzu: frekansin ilk gunu yazilmali (aylikta ayin
        # 1'i); formulas=3 isteginde EVDS kolon adina '-3' ekler.
        yakalanan = {}

        def sahte(url, parametreler=None, anahtar_deger=None):
            yakalanan.update(parametreler or {})
            return {"items": [
                {"Tarih": "2025-8", "TP_KM_B33-3": "51.2"},
                {"Tarih": "2026-9", "TP_KM_B33-3": "61.8"}]}

        hedef = {"id": "credit_growth_yoy", "frekans": 5, "formul": 3,
                 "gun": 400, "min": -100.0, "max": 500.0}
        with mock.patch.object(self.evds, "_istek", side_effect=sahte):
            deger, onceki, donem = self.evds._veri_cek(
                hedef, "TP.KM.B33", "2026-09-24")
        self.assertEqual((deger, onceki, donem), (61.8, 51.2, "2026-09-01"))
        self.assertEqual(yakalanan["startDate"], "01-08-2025")
        self.assertEqual(yakalanan["formulas"], "3")

    def test_ele_kodlar_kesif_cagrisi_yaptirmaz(self):
        # Elle sabitlenmis kod icin ne datagroups ne serieList cagrilir.
        pin = {"reer": {"kod": "TP.RK.T1.Y",
                        "ad": "TUFE Bazli REER", "grup": "g"}}

        def yasak(url, parametreler=None, anahtar_deger=None):
            raise AssertionError("kesif ag cagrisi yapmamali")

        with mock.patch.object(self.evds, "ELLE_KODLAR", pin), \
                mock.patch.object(self.evds, "_istek", side_effect=yasak):
            kesif = self.evds._Kesif()
            kod, ad = kesif.kod({"id": "reer", "grup": r"eslesmez",
                                 "seri": r"eslesmez"})
        self.assertEqual(kod, "TP.RK.T1.Y")
        self.assertEqual(ad, "TUFE Bazli REER")

    def test_kesif_arsiv_grubu_ele_taze_grubu_sec(self):
        # Eskiden '(Arsiv)' adli, eski bitisli gruplar kesifte one
        # cikiyordu; artik arsiv adli ve bayat END_DATE'li gruplar ele gelir.
        def sahte(url, parametreler=None, anahtar_deger=None):
            if url.endswith("datagroups/"):
                return [
                    {"DATAGROUP_CODE": "bie_eski",
                     "DATAGROUP_NAME": "Mevduat Faizleri (Arşiv)",
                     "END_DATE": "01-07-2026"},
                    {"DATAGROUP_CODE": "bie_taze",
                     "DATAGROUP_NAME": "Mevduat Faizleri",
                     "END_DATE": "18-09-2026"}]
            if url.endswith("serieList/"):
                grup = str((parametreler or {}).get("code", ""))
                return [{"SERIE_CODE": "TP." + grup.upper() + ".TUM",
                         "SERIE_NAME": "Tum Mevduat Faizi",
                         "END_DATE": "18-09-2026"}]
            return None

        hedef = {"id": "deposit_rate", "grup": r"mevduat",
                 "seri": r"mevduat faizi", "tercih": None, "disi": None}
        with mock.patch.object(self.evds, "_istek", side_effect=sahte):
            bulunan = self.evds._kodu_bul(hedef, self.evds._grup_listesi())
        self.assertIsNotNone(bulunan)
        self.assertEqual(bulunan["kod"], "TP.BIE_TAZE.TUM")


if __name__ == "__main__":
    unittest.main()
