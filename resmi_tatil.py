# -*- coding: utf-8 -*-
"""Türkiye resmi tatil takvimi (2025-2028) — snapshot fallback ve iş günü hesabı için.

Kullanım:
  from resmi_tatil import tatil_mi, is_gunu
  tatil_mi("2026-04-23")  -> True
  is_gunu("2026-04-24")   -> True (Cuma, tatil degil)

Not: Dini bayram tarihleri (Ramazan/Kurban) Hicri takvim kaydigi icin YILLIK
resmi kararnamelerle dogrulanmalidir; listedekiler 2026-2028 icin yayinlanmis
takvimlere gore yazilmistir. Yanlis bir tatil kaydi fallback'i bozmaz:
tarama "dosya varsa al" mantigindadir, tatil etiketi yalnizca log/dokumantasyon
icin kullanilir.
"""
from datetime import date, datetime, timedelta

# Kesin, sabit günler (her yıl tekrar eder): yıl aralığında açılır.
_SABITLER = [
    (1, 1),    # Yılbaşı
    (4, 23),   # Ulusal Egemenlik ve Çocuk Bayramı
    (5, 1),    # Emek ve Dayanışma Günü
    (5, 19),   # Atatürk'ü Anma, Gençlik ve Spor Bayramı
    (7, 15),   # Demokrasi ve Millî Birlik Günü
    (8, 30),   # Zafer Bayramı
    (10, 29),  # Cumhuriyet Bayramı
]

# Dini bayramlar (Ramazan / Kurban) — resmi tatil gün aralıkları.
_DINI = [
    # 2025: Ramazan 30 Mar-1 Nis; Kurban 6-9 Haz
    ("2025-03-30", "2025-04-01"), ("2025-06-06", "2025-06-09"),
    # 2026: Ramazan 19-21 Mar (20 Mart Cuma, 1. gün); Kurban 26-29 May
    # (27 Mayıs Çarşamba, 1. gün); 31 Ağustos Pazartesi muhtemel tatil (30 Ağ. Pazar)
    ("2026-03-19", "2026-03-21"), ("2026-05-26", "2026-05-29"), ("2026-08-31", "2026-08-31"),
    # 2027: Ramazan ~9-11 Mar (10 Mart Çarşamba, 1. gün); Kurban ~16-19 May
    # (17 Mayıs Pazartesi, 1. gün)
    ("2027-03-09", "2027-03-11"), ("2027-05-16", "2027-05-19"),
    # 2028: Ramazan ~26-28 Şub (27 Şubat Pazar, 1. gün); Kurban ~4-7 May
    # (5 Mayıs Cuma, 1. gün) — tahmini, resmi kararnamelerle doğrulanmalı
    ("2028-02-26", "2028-02-28"), ("2028-05-04", "2028-05-07"),
]

_MIN_YIL, _MAX_YIL = 2025, 2028


def _set_uret() -> set:
    gunler = set()
    for yil in range(_MIN_YIL, _MAX_YIL + 1):
        for ay, gun in _SABITLER:
            gunler.add(f"{yil:04d}-{ay:02d}-{gun:02d}")
    for bas, son in _DINI:
        d = datetime.strptime(bas, "%Y-%m-%d").date()
        bitis = datetime.strptime(son, "%Y-%m-%d").date()
        while d <= bitis:
            gunler.add(d.isoformat())
            d += timedelta(days=1)
    return gunler


_TATILLER = _set_uret()


def _normal(tarih) -> str:
    if isinstance(tarih, str):
        return tarih[:10]
    if isinstance(tarih, datetime):
        return tarih.date().isoformat()
    if isinstance(tarih, date):
        return tarih.isoformat()
    raise TypeError(f"desteklenmeyen tarih turu: {type(tarih)}")


def tatil_mi(tarih) -> bool:
    """Verilen gün (str/date/datetime) resmi tatilse True."""
    return _normal(tarih) in _TATILLER


def is_gunu(tarih) -> bool:
    """Is gunu mu: cumartesi/pazar degil VE resmi tatil degil."""
    n = _normal(tarih)
    d = datetime.strptime(n, "%Y-%m-%d").date()
    return d.weekday() < 5 and n not in _TATILLER


if __name__ == "__main__":
    # bilinen tatiller
    assert tatil_mi("2026-04-23") and tatil_mi("2026-01-01")
    assert tatil_mi("2026-03-20") and tatil_mi("2026-05-27")  # dini bayram gunleri
    assert tatil_mi("2027-03-10") and tatil_mi("2028-02-27")
    assert not tatil_mi("2026-10-05")  # pazartesi, tatil degil
    # is gunu mantigi: hafta sonu + tatil haric
    assert not is_gunu("2026-10-03")          # cumartesi
    assert not is_gunu("2026-04-23")          # persembe, tatil
    assert is_gunu("2026-04-24")              # cuma, tatil degil
    assert is_gunu("2026-10-05")              # pazartesi
    # dizi araliklari dogru genisliyor (Ramazan 2026: 19-21 Mart uc gun)
    for g in ("2026-03-19", "2026-03-20", "2026-03-21"):
        assert tatil_mi(g), g
    assert not tatil_mi("2026-03-22")
    print(f"resmi_tatil.py: tum kendini testler gecti ({len(_TATILLER)} tatil gunu, {_MIN_YIL}-{_MAX_YIL})")
