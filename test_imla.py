# -*- coding: utf-8 -*-
"""editor.imla() ve kalite kapisi gerileme testleri.

Neden var: 2026-11-25'te canli sayfalarda Turkce bozulmalari goruldu —
"%50-65" yerine "50-%65", "15 %" -> "1%5" (tek hane kaymasi), "vb. Portfoy"
gibi kisaltma sonrasi yanlis buyuk harf. Bu testler o hatalarin geri
donmesini engeller; daily.yml icinde BLOKLAYICI calisir.

Kural (kullanici, 2026-11-25): yuzde ARALIGINDA her iki sayi da isaret alir —
"%50-%65". Tek yuzde ("%50-65") ve sonda yuzde ("50-65%") hatalidir.
"""
import unittest

import editor


def _imla(metin):
    return editor.imla(metin)[0]


def _b(metin):
    """Beklenen metne cumle-basi kuralini uygular (imla() ilk harfi buyutur)."""
    return editor._buyuk(metin[0]) + metin[1:] if metin else metin


class _Tabani(unittest.TestCase):
    def esit(self, girdi, beklenen):
        self.assertEqual(_imla(girdi), _b(beklenen))


class TersYuzdeTest(_Tabani):
    """'15 %' -> '%15'. Tek hane kaymasi (kok neden) geri donmemeli."""

    def test_basit(self):
        self.esit("faiz 15 % oldu", "faiz %15 oldu")

    def test_ondalik(self):
        self.esit("günlük 0,49 % düştü", "günlük %0,49 düştü")

    def test_tek_hane_kaymasi_yok(self):
        # Eski desen "15 %" -> "1%5", "0,49 %" -> "0,4%9" uretiyordu.
        cikti = _imla("faiz 15 % oldu")
        self.assertNotIn("1%", cikti)
        self.assertNotIn("%5", cikti)

    def test_iki_ayri_sayi_korunur(self):
        self.esit("5 %3 ayrı sayı", "5 %3 ayrı sayı")


class YuzdeAralikTest(_Tabani):
    """Aralikta HER IKI sayi '%' alir: %50-%65."""

    def test_sonda_yuzde(self):
        self.esit("kredi 50-65% bandı", "kredi %50-%65 bandı")

    def test_sonda_yuzde_bosluklu(self):
        self.esit("Getiri 50-65 % aralığında.", "Getiri %50-%65 aralığında.")

    def test_iki_yaninda_bosluk(self):
        self.esit("Aralık 40 - 60 % arası.", "Aralık %40-%60 arası.")

    def test_tek_yuzde_basta(self):
        self.esit("Beklenti %50-65 bandında.", "Beklenti %50-%65 bandında.")

    def test_tek_yuzde_basta_bosluklu(self):
        self.esit("Beklenti %50 - 65 bandında.", "Beklenti %50-%65 bandında.")

    def test_ondalik_aralik(self):
        self.esit("aylık 1-1,5% aralığı", "aylık %1-%1,5 aralığı")

    def test_zaten_dogru_bicim_korunur(self):
        self.esit("faiz %40-%60 aralığı", "faiz %40-%60 aralığı")

    def test_yuzdesiz_aralik_dokunulmaz(self):
        self.esit("16-20 Ekim tarihleri.", "16-20 Ekim tarihleri.")

    def test_url_kodlamasi_bozulmaz(self):
        self.esit("URL 2026-09-16%20 bozulmaz", "URL 2026-09-16%20 bozulmaz")

    def test_bolunmez_tire_korunur(self):
        # U+2011 ayirici tipografisi sessizce degismemeli.
        self.esit("bant 50\u201165% arası", "bant %50\u2011%65 arası")


class YuzdeSondaTest(_Tabani):
    """'-0,13%' -> '%-0,13'; '+' dusurulur, eksi korunur."""

    def test_eksi(self):
        self.esit("günlük -0,13% değişim", "günlük -%0,13 değişim")

    def test_arti_duser(self):
        self.esit("artış +4,3% oldu", "artış %4,3 oldu")

    def test_parantez_ici(self):
        self.esit("kanal (41%) konum", "kanal (%41) konum")

    def test_yuzde_kelimesi_sonrasi_dokunulmaz(self):
        self.esit("yüzde 5% zaten doğru", "yüzde 5% zaten doğru")


class YuzdeKaymaTest(_Tabani):
    """Kaymis '%' onarimi aralik/tek-sayi kurallarindan ONCE calisir."""

    def test_ondalik_kaymasi(self):
        self.esit("Getiri 43,0%4 oldu.", "Getiri %43,04 oldu.")

    def test_eksi_kaymasi(self):
        self.esit("Düşüş -0,3%8 oldu.", "Düşüş -%0,38 oldu.")


class CumleBasiTest(_Tabani):
    """Kisaltmadan sonra cumle basi sayilmaz; gercek cumle basi buyur."""

    def test_vb_kucuk_kalir(self):
        self.esit("hisse vb. gibi araçlar", "hisse vb. gibi araçlar")

    def test_orn_kucuk_kalir(self):
        self.esit("Örnek örn. bu durum", "Örnek örn. bu durum")

    def test_vs_kucuk_kalir(self):
        self.esit("ayrışma vs. ile birlikte", "ayrışma vs. ile birlikte")

    def test_gercek_cumle_basi_buyur(self):
        self.esit("endeks düştü. alıcı gelmedi.", "endeks düştü. Alıcı gelmedi.")


class SozlukTest(_Tabani):
    """Yazim sozlugu ve kaliplasmis ceviriler."""

    def test_rally_eki(self):
        self.esit("bankacılık odaklı rally'nin sürmesi",
                  "bankacılık odaklı rallinin sürmesi")

    def test_ceviri_kalibi(self):
        self.esit("(relief rally) görülebilir", "(rahatlama rallisi) görülebilir")

    def test_uydurma_sozcuk(self):
        self.esit("korunabilirlik için tutulan oran",
                  "ihtiyat amaçlı tutulan oran")

    def test_isaret_ediyor(self):
        self.esit("zayıfladığını işaret ediyor", "zayıfladığını gösteriyor")

    def test_vb_noktasi(self):
        self.esit("(GARAN, YKBNK vb..) için", "(GARAN, YKBNK vb.) için")


class DokunulmamasiGerekenTest(_Tabani):
    """Veri sozlesmesi ve tablo bicimleri korunur."""

    def test_tablo_satiri(self):
        self.esit("| 3.6% | 2.4% |", "| 3.6% | 2.4% |")

    def test_tablo_ayracli_dizi(self):
        self.esit("U-6 %7,9 tablo değeri", "U-6 %7,9 tablo değeri")


class IsaretYeriTest(_Tabani):
    """Isaret yuzdenin SOLUNDA: "-%15". Iki kural da ayni bicimi uretmeli.

    Architect bulgusu (2026-11-25): _ters_yuzde "-%15", _yuzde_basa "%-15"
    uretiyordu; ayni kural ailesi iki zıt sonuc veriyordu.
    """

    def test_bosluklu_eksi(self):
        self.esit("düşüş -15 % oldu", "düşüş -%15 oldu")

    def test_bitisik_eksi(self):
        self.esit("düşüş -15% oldu", "düşüş -%15 oldu")

    def test_arti_duser(self):
        self.esit("artış +15% oldu", "artış %15 oldu")

    def test_zaten_dogru(self):
        self.esit("düşüş -%15 oldu", "düşüş -%15 oldu")

    def test_eski_bicim_duzeltilir(self):
        self.esit("düşüş %-15 oldu", "düşüş -%15 oldu")

    def test_aralik_bozulmaz(self):
        self.esit("bant %50-%65 arası", "bant %50-%65 arası")


class BinlikAyracTest(_Tabani):
    """Aralik kurali binlik ayracli sayiyi aralik sanmamali.

    Architect bulgusu (2026-11-25): "%5 - 10.000 TL" girdisi
    "%5-%10.000 TL" oluyordu — veri bozulmasi.
    """

    def test_binlik_sag(self):
        self.esit("getiri %5 - 10.000 TL", "getiri %5 - 10.000 TL")

    def test_binlik_sag_ondalik(self):
        self.esit("%5 - 1.234,56 TL", "%5 - 1.234,56 TL")

    def test_puan_araligi(self):
        self.esit("XU030 %2,24 - 15.327 puan", "XU030 %2,24 - 15.327 puan")

    def test_gercek_aralik_hala_calisir(self):
        self.esit("bant %50 - 65 arası", "bant %50-%65 arası")

    def test_binlik_yuzde_sonda(self):
        # 2026-11-25: `(?<![\d.,])` lookbehind'i `234` onundeki `.` yuzunden
        # eslesmiyordu; `+1.234,57%` hic donusturulmuyor ve kalite kapisina
        # takiliyordu (yumusak kapida yayina cikabiliyordu).
        self.esit("getiri +1.234,57% oldu", "getiri %1.234,57 oldu")
        self.esit("getiri -1.234,57% oldu", "getiri -%1.234,57 oldu")
        self.esit("getiri 12.345,67% oldu", "getiri %12.345,67 oldu")

    def test_binlik_yuzde_bosluklu(self):
        self.esit("getiri 1.234,57 % oldu", "getiri %1.234,57 oldu")


class GosterimKorumasiTest(_Tabani):
    """Tek harfli gosterim + '=' cumle basi sayilmaz.

    Architect bulgusu (2026-11-25): "r=0.88" -> "R=0.88" oluyordu; Pearson
    katsayisi gelenekte kucuk yazilir.
    """

    def test_pearson_r(self):
        self.esit("kanalın üst bandında. r=0.88 ile uyumlu.",
                  "kanalın üst bandında. r=0.88 ile uyumlu.")

    def test_p_degeri(self):
        self.esit("düzeltme sonrası. p=0,03 anlamlı.",
                  "düzeltme sonrası. p=0,03 anlamlı.")

    def test_gercek_cumle_basi_hala_buyur(self):
        self.esit("düştü. alıcı gelmedi.", "düştü. Alıcı gelmedi.")


class YuzdeBicimleTest(unittest.TestCase):
    """yuzde_bicimle() TEK giris noktasi — butun bicimler ayni sonucu vermeli.

    Architect bulgusu (2026-11-25): kural editor.imla(), rapor_denetle ve
    pipeline/md_ayna icinde ayri ayri yazilmisti; 13 ortak girdinin 6'sinda
    farkli sonuc veriyordu. Bu test o ayrismayi kilitler.
    """

    # (girdi, beklenen) — hepsi ayni kanonik bicime inmeli
    PROSE = [
        ("artış +%4,09 oldu", "artış %4,09 oldu"),
        ("artış +4,09% oldu", "artış %4,09 oldu"),
        ("düşüş %-4,09 oldu", "düşüş -%4,09 oldu"),
        ("düşüş -4,09% oldu", "düşüş -%4,09 oldu"),
        ("düşüş -4,09 % oldu", "düşüş -%4,09 oldu"),
        ("aralık %50-65 bandı", "aralık %50-%65 bandı"),
        ("aralık 50-65% bandı", "aralık %50-%65 bandı"),
        ("aralık 50-65 % bandı", "aralık %50-%65 bandı"),
        ("aralık %50-%65 bandı", "aralık %50-%65 bandı"),
        ("% -0,06 sapma", "-%0,06 sapma"),
    ]

    def test_prose_kanonik(self):
        for girdi, beklenen in self.PROSE:
            with self.subTest(girdi=girdi):
                self.assertEqual(editor.yuzde_bicimle(girdi)[0], beklenen)

    def test_idempotent(self):
        for girdi, _b in self.PROSE:
            with self.subTest(girdi=girdi):
                bir = editor.yuzde_bicimle(girdi)[0]
                iki = editor.yuzde_bicimle(bir)[0]
                self.assertEqual(bir, iki, "ikinci gecis metni degistirdi")

    def test_tablo_hucre_veri_bicimi_korunur(self):
        # Tablo hucresinde "+5,13%" veri bicimidir; isaret yeri degismez.
        self.assertEqual(
            editor.yuzde_bicimle("+5,13%", tablo_disi=False)[0], "+5,13%")
        # Aralik kurali hucrede de gecerli.
        self.assertEqual(
            editor.yuzde_bicimle("%50-65", tablo_disi=False)[0], "%50-%65")

    def test_imla_ile_ayni_sonuc(self):
        # imla() yuzde bicimi icin ayni fonksiyonu kullanir.
        for girdi, _b in self.PROSE:
            with self.subTest(girdi=girdi):
                self.assertEqual(
                    editor.imla(girdi)[0],
                    editor._buyuk(editor.yuzde_bicimle(girdi)[0][0])
                    + editor.yuzde_bicimle(girdi)[0][1:])


class KaliteKapisiTutarliligiTest(unittest.TestCase):
    """imla() ciktisi kendi kalite kapisini TETIKLEMEMELI.

    Architect bulgusu (2026-11-25): kayma onarimi "43,04%" uretiyordu,
    kalite_tara ise "sayidan sonra %" desenini reddediyordu — duzeltici
    kendi kapisiyla celisiyordu.
    """

    VAKALAR = [
        "faiz 15 % oldu",
        "günlük 0,49 % düştü",
        "kredi 50-65% bandı",
        "Getiri 50-65 % aralığında.",
        "Aralık 40 - 60 % arası.",
        "Beklenti %50-65 bandında.",
        "aylık 1-1,5% aralığı",
        "günlük -0,13% değişim",
        "artış +4,3% oldu",
        "Getiri 43,0%4 oldu.",
        "Düşüş -0,3%8 oldu.",
        "hisse vb. gibi araçlar",
        "endeks düştü. alıcı gelmedi.",
    ]

    def test_imla_ciktisi_temiz(self):
        for girdi in self.VAKALAR:
            with self.subTest(girdi=girdi):
                cikti = _imla(girdi)
                bulgular = editor.kalite_tara(cikti)
                self.assertEqual(
                    bulgular, [],
                    f"imla() ciktisi kalite kapisini tetikledi: {cikti!r} -> "
                    f"{[b[1] for b in bulgular]}")


class I18nSayiCevrimiTest(unittest.TestCase):
    """`i18n.sayilari_cevir()` TR bicimini hedef dile cevirir.

    2026-11-25: TR bicimi `%`-onde olunca `%1.234,57` girdisi
    `1,234%.57` cikiyordu — yuzde isareti sayinin ortasina dusuyordu.
    Sirasi degistirildi: once `%` tasinir, sonra ayirici cevrilir.
    """

    def test_yuzde_onde_cevrilir(self):
        import i18n
        self.assertEqual(i18n.sayilari_cevir("%0,08", "en"), "0.08%")
        self.assertEqual(i18n.sayilari_cevir("-%0,50", "en"), "-0.50%")
        self.assertEqual(i18n.sayilari_cevir("%6,41", "de"), "6.41%")

    def test_binlik_ayrac_korunur(self):
        import i18n
        self.assertEqual(i18n.sayilari_cevir("%1.234,57", "en"), "1,234.57%")
        self.assertEqual(i18n.sayilari_cevir("%12.345,67", "en"), "12,345.67%")

    def test_eski_bicim_de_calisir(self):
        import i18n
        self.assertEqual(i18n.sayilari_cevir("+0,08%", "en"), "+0.08%")
        self.assertEqual(i18n.sayilari_cevir("+1.234,57%", "en"), "+1,234.57%")

    def test_aralik_ve_turkce_dokunulmaz(self):
        import i18n
        self.assertEqual(i18n.sayilari_cevir("%50-%65", "en"), "50%-65%")
        self.assertEqual(i18n.sayilari_cevir("%0,08", "tr"), "%0,08")


if __name__ == "__main__":
    unittest.main(verbosity=2)
