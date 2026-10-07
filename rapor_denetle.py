#!/usr/bin/env python3
"""
Makro analiz raporu denetleyici.

Kaynak: Claude denetim taslagi (kullanici iletisi, 2026-10-07); depoya
uyarlandi. Rapor uretildikten SONRA, yayinlanmadan ONCE calisir:
  python rapor_denetle.py makro-analiz/2026-10-07.html [--json]

Cikis kodu: en az bir ERROR varsa 1, yoksa 0.
CI'da su an YALNIZCA raporlamak icin kullanilir (adim `|| echo` ile
non-blocking); esikler birkaq kosu gunlene kadar yayini durdurmaz.

Kurallar: R01-R15 metin/mantik, T01-T07 tablo, N01-N02 sayi kaynakli,
S01 yasakli kalip. ERROR = buyuk olasilikla yanlis, WARN = elle bakilmali,
INFO = bilgi. dogrulama.py (sayi-duzeltici katman) ile birlikte dusunun:
o yayin-ONCESI metni duzeltir, bu uretilmis sayfayi bagimsiz denetler.
"""
import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import date

COUNTRIES = {"Euro Bölgesi", "Türkiye", "ABD", "Küresel Piyasa"}
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
FREQS = {"aylık", "haftalık", "günlük", "üç aylık", "yıllık", "olay"}

# Metinde kalmaması gereken kalıplar (pipeline sızıntıları, bilinen
# yazım/çeviri hataları — 2026-10-07 turunda el ile temizlenmişti; bu
# kural LLM'in ayni hatalari yeniden uretmesini yakalar).
BANNED = [
    (r"veri(de|\s+bloğunda|\s+setinde)?\s+(detay\s+)?yok", "iç işleyiş ifadesi ('veri yok')"),
    (r"\([^)]*\?\)", "parantez içinde kalmış soru işareti"),
    (r"\bKHK\b", "'KHK' bu bağlamda anlamsız"),
    (r"bekenti", "yazım: 'beklenti'"),
    (r"\borayı\b", "yazım: 'oranı'"),
    (r"haftalik", "yazım: 'haftalık'"),
    (r"işlemci endüstri", "çeviri hatası (downstream)"),
    (r"fiyat takibi", "çeviri hatası (pricing power → fiyatlandırma gücü)"),
    (r"finansal kural dönüşü", "çeviri hatası (fiscal rules reform)"),
    (r"%\s?\d+\+\s?bp", "'%100+ bp' yerine '100+ bp'"),
    (r"[Ee]ğim eğrisi", "'getiri eğrisi' denmeli"),
]

findings = []
_seen = set()


def add(sev, rule, msg):
    key = (rule, msg)
    if key in _seen:
        return
    _seen.add(key)
    findings.append({"severity": sev, "rule": rule, "message": msg})


# ---------------------------------------------------------------- yükleme
def load(src):
    if src.startswith("http"):
        raw = urllib.request.urlopen(src, timeout=30).read().decode("utf-8", "replace")
    else:
        with open(src, encoding="utf-8") as f:
            raw = f.read()
    if re.search(r"<html|<td", raw, re.I):
        raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
        raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
        raw = re.sub(r"(?i)</tr>|<br\s*/?>|</p>|</h\d>|</li>", "\n", raw)
        raw = html.unescape(re.sub(r"<[^>]+>", "", raw))
    return raw


def parse_table(text):
    table = {}
    for line in text.splitlines():
        if line.count("|") < 7:
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 8 and c[0] in COUNTRIES:
            table[(c[0], c[1])] = dict(val=c[2].replace("*", ""), fc=c[3], prev=c[4],
                                       period=c[5], freq=c[6], src=c[7])
    return table


def tonum(s):
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace("−", "-"))
    return float(m.group()) if m else None


def pdate(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return date(*map(int, m.groups())) if m else None


def has(s, *pats):
    return all(re.search(p, s, re.I | re.S) for p in pats)


def scan(wins, rule, sev, msg, *pats, cond=True):
    """İlk eşleşen cümle/pencere için bulgu ekler."""
    if not cond:
        return
    for w in wins:
        if has(w, *pats):
            add(sev, rule, f"{msg}\n        > {w[:170]}…")
            return


# ---------------------------------------------------------------- kurallar
def check_text(T, body, wins):
    g = lambda c, n, f="val": tonum(T.get((c, n), {}).get(f))

    # R01: yazılan fark ile tablodan hesaplanan fark
    tufe, ufe = g("Türkiye", "Enflasyon (yıllık)"), g("Türkiye", "ÜFE (yıllık)")
    if tufe is not None and ufe is not None:
        for m in re.finditer(r"(\d+,\d+)\s*puan fark", body):
            claim = float(m.group(1).replace(",", "."))
            if abs(claim - (tufe - ufe)) > 0.05:
                add("ERROR", "R01", f"TÜFE-ÜFE farkı metinde {claim}, tablodan {tufe - ufe:.2f}")

    # R02-R09: yön / mantık kuralları (cümle ve komşu cümle çiftleri)
    scan(wins, "R02", "ERROR",
         "Reel değerlenme cari dengeyi zorlar (ithalat ucuzlar, ihracat pahalılaşır); yön ters olabilir.",
         r"reel (kur )?değerlen", r"cari denge", r"yardımcı|iyileşmesine")
    scan(wins, "R02b", "ERROR",
         "Reel değerlenme ortamında 'ithalat baskılanır' ifadesi tutarsız.",
         r"[Cc]ari denge iyileşir", r"ithalat baskılanır")
    scan(wins, "R03", "ERROR",
         "Pozitif eğimli getiri eğrisi kredi arzını kısıtlamaz (düz/ters eğri kısıtlar).",
         r"pozitif eğim", r"kredi arzını kısıt")
    scan(wins, "R04", "ERROR",
         "'unanchored' = çapasız; 'sabitlen' ile birlikte kullanımı çelişkili.",
         r"unanchored", r"sabitlen")
    pol, pce = g("ABD", "Politika faizi"), g("ABD", "Çekirdek PCE (yıllık)")
    if pol is not None and pce is not None:
        scan(wins, "R05", "ERROR",
             f"ABD reel faizi pozitif (politika {pol} - çekirdek PCE {pce} = {pol - pce:+.1f}); 'negatif' ifadesi veriyle çelişiyor.",
             r"reel faiz", r"negatif", r"altın|ABD", cond=(pol - pce) > 0)
    scan(wins, "R06", "WARN",
         "Aynı yerde hem TL'nin reel değerlenmesi hem kur korumasızlığı/alım gücü erimesi: mantık çelişkisi.",
         r"reel değerlenme", r"korumasızlığ")
    scan(wins, "R07", "WARN",
         "Perakende satış serisi (TÜİK) genelde hacim/reel endekstir; 'nominal' varsayımı ve türetilmiş reel değer doğrulanmalı.",
         r"perakende", r"nominal", r"reel\s*~")
    eu, eu_fc = g("Euro Bölgesi", "Büyüme (çeyreklik)"), g("Euro Bölgesi", "Büyüme (çeyreklik)", "fc")
    if eu is not None and eu_fc is not None:
        scan(wins, "R08", "WARN",
             f"Euro Bölgesi çeyreklik büyüme beklentiyi aştı ({eu} vs {eu_fc}); 'stagnasyon' ifadesini gözden geçirin.",
             r"stagnasyon", r"Euro", cond=eu > eu_fc)
    scan(wins, "R09", "WARN",
         "Aylık TÜFE < aylık ÜFE, 'talep taraflı enflasyon' yorumunu desteklemez (maliyet baskısı/marj sıkışması).",
         r"aylık TÜFE.{0,10}ÜFE.{0,25}altında", r"talep taraf")

    # R10: dönem etiketleri (tablodaki tarih yayın tarihi gibi duruyorsa)
    d = pdate(T.get(("Türkiye", "Cari denge"), {}).get("period"))
    if d and d.day != 1:
        scan(wins, "R10", "ERROR",
             f"Cari denge tablo tarihi {d} (yayın tarihi gibi); metin 'Q3/Eylül verisi' diyorsa dönem hatası var.",
             r"cari", r"Q3|Eylül verisi")
    d = pdate(T.get(("Türkiye", "Bütçe Dengesi"), {}).get("period"))
    if d and d.day != 1:
        ref = MONTHS[(d.month - 2) % 12]
        for w in wins:
            if re.search(r"bütçe", w, re.I):
                mm = re.search(r"(%s) \d{4}" % "|".join(MONTHS), w)
                if mm and mm.group(1) != ref:
                    add("ERROR", "R10b",
                        f"Bütçe yayın tarihi {d} → veri büyük olasılıkla {ref} ayına ait, metin {mm.group(1)} diyor.\n        > {w[:170]}…")
                    break
    months = {(x.year, x.month) for k, v in T.items()
              if "EVDS" in v["src"] and (x := pdate(v["period"]))}
    if len(months) > 1:
        add("WARN", "R11", f"EVDS göstergeleri farklı dönemlerde {sorted(months)}; metindeki tek 'snapshot ayı' ifadesi yanıltıcı olabilir.")

    # R12: senaryo eşiği = mevcut değer (yalnızca >=2 ondalıklı sayılar)
    cur = [tonum(v["val"]) for v in T.values() if tonum(v["val"]) is not None]
    for m in re.finditer(r"(\d+,\d{2})\s*\+|%?(\d+,\d{2})\s+altına\s+gir", body):
        x = float((m.group(1) or m.group(2)).replace(",", "."))
        if any(abs(x - c) < 1e-9 for c in cur):
            add("WARN", "R12", f"Senaryo eşiği {x} mevcut piyasa/veri değeriyle aynı; eşik anlamsız olabilir.")

    # R13: tetikleyici zaten gerçekleşmiş mi? (rezervler)
    # Rapor bu durumu KENDİ not ediyorsa (2026-10-07'deki gibi 'zaten
    # tetiklenmiştir') uyarı üretilmez.
    m = re.search(r"Rezervler haftalık (\d+(?:,\d+)?) mld \$\+ azalırsa", body)
    v, p = g("Türkiye", "Döviz rezervleri"), g("Türkiye", "Döviz rezervleri", "prev")
    if m and v is not None and p is not None and not re.search(r"zaten tetiklen", body):
        thr = float(m.group(1).replace(",", "."))
        if p - v >= thr:
            add("WARN", "R13", f"Rezerv tetikleyicisi (≥{thr} mld $ düşüş) zaten gerçekleşti ({p - v:.3f} mld $); rapor bunu belirtmiyor.")

    # R14: dot plot politika faizinin üstündeyken baz senaryo indirim varsayıyor mu?
    # Baz senaryo SEP tension'ını KENDİ açıklıyorsa (SEP geçiyorsa) uyarı yok.
    dot = g("ABD", "Faiz Projeksiyonu (1. yıl)")
    i = body.find("Baz Senaryo")
    if dot is not None and pol is not None and dot > pol and i >= 0:
        pencere = body[i:i + 900]
        if re.search(r"Fed[^\n]*indirim", pencere) and "SEP" not in pencere:
            add("WARN", "R14", f"Dot plot (1. yıl) {dot} > politika faizi {pol}, ama baz senaryo Fed indirimi varsayıyor; çelişki açıklanmalı.")


def check_table(T):
    g = lambda c, n, f="val": tonum(T.get((c, n), {}).get(f))

    ranges = {("Euro Bölgesi", "Enflasyon Beklentisi (1 yıl)"): (-5, 15),
              ("ABD", "İşsizlik oranı"): (0, 20),
              ("Küresel Piyasa", "VIX"): (5, 90)}
    for k, (lo, hi) in ranges.items():
        x = g(*k)
        if x is not None and not lo <= x <= hi:
            add("ERROR", "T01", f"{k[0]} / {k[1]} = {x}: beklenen aralık dışında ({lo}…{hi}); etiket/birim hatası olabilir.")
    v = T.get(("ABD", "Tüketici Kredisi Değişimi"))
    if v and "%" in v["val"]:
        add("WARN", "T02", "ABD Tüketici Kredisi Değişimi '%' ile verilmiş; bu seri genelde milyar $.")
    brent, wti = g("Küresel Piyasa", "Brent Petrol"), g("Küresel Piyasa", "WTI Petrol")
    if brent and wti and brent - wti > 8:
        add("WARN", "T03", f"Brent-WTI farkı {brent - wti:.1f} $; olağandışı geniş, kaynağı kontrol edin.")
    cpi, pce = g("ABD", "Çekirdek Enflasyon (yıllık)"), g("ABD", "Çekirdek PCE (yıllık)")
    if cpi is not None and pce is not None and pce - cpi > 0.5:
        add("WARN", "T04", f"ABD çekirdek PCE ({pce}) çekirdek CPI'dan ({cpi}) belirgin yüksek; alışılmadık, kaynakları doğrulayın.")
    for k, r in T.items():
        if r["freq"].lower() not in FREQS:
            add("WARN", "T05", f"{k[0]} / {k[1]}: Frekans='{r['freq']}' geçerli değil (sütun kayması?).")
    r = T.get(("Türkiye", "Enflasyon (aylık)"))
    if r and tonum(r["val"]) is not None and tonum(r["val"]) == tonum(r["prev"]):
        add("WARN", "T06", "Türkiye aylık TÜFE: gerçekleşen = önceki; veri tekrarı olabilir.")
    days = {pdate(r["period"]).day == 1 for r in T.values() if pdate(r["period"])}
    if len(days) > 1:
        add("INFO", "T07", "'Dönem' sütunu karışık (bazı satırlar ayın 1'i = referans dönem, bazıları yayın tarihi). Tek bir kural seçin.")


def check_numbers(T, body):
    """Metindeki ondalıklı sayıların tablodan veya kodla türetilmiş değerlerden gelmesini ister."""
    pool = set()
    for r in T.values():
        for f in ("val", "fc", "prev"):
            n = tonum(r[f])
            if n is not None:
                pool.add(abs(n))
    for (c, _), r in T.items():  # günlük yüzde değişimler
        v, p = tonum(r["val"]), tonum(r["prev"])
        if c == "Küresel Piyasa" and v is not None and p:
            pool.add(abs((v - p) / p * 100))
    g = lambda c, n: tonum(T.get((c, n), {}).get("val"))
    tufe, ufe, pol = g("Türkiye", "Enflasyon (yıllık)"), g("Türkiye", "ÜFE (yıllık)"), g("Türkiye", "Politika faizi")
    kredi, tk = g("Türkiye", "Kredi Büyümesi (yıllık)"), g("Türkiye", "Tüketici Kredisi Faizi")
    if None not in (tufe, ufe, pol):
        pool |= {abs(tufe - ufe), abs(pol - tufe), abs(((1 + pol / 100) / (1 + tufe / 100) - 1) * 100)}
    if None not in (tk, pol):
        pool.add(abs(tk - pol))
    if None not in (kredi, tufe):
        pool |= {abs(kredi - tufe), abs(((1 + kredi / 100) / (1 + tufe / 100) - 1) * 100)}
    # ABD reel faiz farkı (R05 ile ayni hesap)
    us_pol, us_pce = g("ABD", "Politika faizi"), g("ABD", "Çekirdek PCE (yıllık)")
    if us_pol is not None and us_pce is not None:
        pool.add(abs(us_pol - us_pce))
    # TR rezerv haftalik degisimi (R13 ile ayni hesap)
    tr_v = tonum(T.get(("Türkiye", "Döviz rezervleri"), {}).get("val"))
    tr_p = tonum(T.get(("Türkiye", "Döviz rezervleri"), {}).get("prev"))
    if tr_v is not None and tr_p is not None:
        pool.add(abs(tr_p - tr_v))

    unknown = []
    for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d+|\d+,\d+)", body):
        s = m.group(1)
        dec = len(s.split(",")[1])
        x = float(s.replace(".", "").replace(",", "."))
        if not any(abs(p - abs(x)) <= 0.5 * 10 ** -dec + 1e-9 for p in pool):
            unknown.append(s)
    if unknown:
        u = sorted(set(unknown), key=unknown.index)
        add("WARN", "N01", "Tabloda/türetilmiş değerlerde karşılığı bulunamayan ondalıklı sayılar "
            f"({len(u)}): {', '.join(u[:20])}{' …' if len(u) > 20 else ''}\n"
            "        (Yuvarlama yerine kesme, tablo dışı sayı veya hesap hatası olabilir.)")

    # Metin 'niteliksel / veri yok' derken sayı veriyorsa
    for s in re.split(r"(?<=[^\d])\.\s+|\n+", body):
        if re.search(r"veri(de|\s+bloğunda)?\s+(detay\s+)?yok|niteliksel", s, re.I) and re.search(r"\d", s) \
                and re.search(r"%\s?\d|~\s?%", s):
            add("WARN", "N02", f"'Veri yok/niteliksel' denirken tablo dışı sayı kullanılmış.\n        > {s[:170]}…")
            break


def check_style(body):
    for pat, msg in BANNED:
        m = re.search(pat, body)
        if m:
            add("WARN", "S01", f"{msg}: '{m.group(0)}'")


# ---------------------------------------------------------------- ana akış
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="dosya yolu veya URL")
    ap.add_argument("--json", action="store_true", help="JSON çıktı")
    a = ap.parse_args()

    text = load(a.source)
    T = parse_table(text)
    if not T:
        print("HATA: veri tablosu ayrıştırılamadı (sütun düzenini kontrol edin).", file=sys.stderr)
        sys.exit(2)
    i = text.find("## Veri Tabanı")
    body = text[:i] if i > 0 else text
    sents = [x.strip() for x in re.split(r"(?<=[^\d])\.\s+|\n+", body) if x.strip()]
    wins = sents + [a_ + " " + b_ for a_, b_ in zip(sents, sents[1:])]

    check_table(T)
    check_text(T, body, wins)
    check_numbers(T, body)
    check_style(body)

    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["rule"]))
    if a.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f"[{f['severity']:<5}] {f['rule']:<4} {f['message']}")
        n = {s: sum(f["severity"] == s for f in findings) for s in order}
        print(f"\n{len(T)} tablo satırı okundu | ERROR={n['ERROR']} WARN={n['WARN']} INFO={n['INFO']}")
    sys.exit(1 if any(f["severity"] == "ERROR" for f in findings) else 0)


if __name__ == "__main__":
    main()
