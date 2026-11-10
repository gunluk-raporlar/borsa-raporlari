/* Hisse sayfasi canli katman (v3): uc ayri is, tek dosyada.
 *
 * 1) CANLI FIYAT (v1'den): /api/fiyatlar ucundan (TradingView, ~15 dk gecikmeli,
 *    kenar onbellek 9 dk) hero'daki fiyat ve gunluk %'yi tazeler, grafik
 *    serisinin son noktasini guncelleyip grafik.js'i tetikler.
 * 2) CANLI ROZET (v3, 2026-11-11): ticker.json'daki gunluk teknik tarama
 *    sinyalini (s alani: AL/SAT/NÖTR/GÜÇLÜ AL/GÜÇLÜ SAT) hero rozetine yansitir;
 *    dil sayfalarinda rozet sayfanin dilinde guncellenir (ROZET tablosu,
 *    i18n SOZLUK girdileriyle ayni).
 * 3) GUNLUK TEKNIK INCELEME (v3, kullanici karari: "baslik 'Günlük Teknik
 *    <KOD> – <Şirket> İncelemesi', yaninda tarih-saat, piyasa acilmadan
 *    uretilsin ~09:00"): /seri/<KOD>.json (bot'un gunluk 08:30 koşusunun
 *    yazdigi kapanis serisi — BIST 10:00'de acilir) hesaplanir; SMA20/SMA50,
 *    RSI14 ve 30 gunluk dilim konumundan 1-2 cumlelik OLGU ozeti uretilir.
 *    Blok gun boyu SABIT kalir (canli fiyati kullanmaz; gunun karari
 *    acilis-oncesi veriyedir) ve sonunda AL/SAT hukmu TASIMAZ — hukmu resmi
 *    rozet verir, ozet olgulari soyler, boylece ikisi asla celismez.
 *    Dil sayfalarindaki gomulu seri bayat olsa bile inceleme dogru cunku
 *    kaynak /seri/ JSON'u; dil agacina kopyalanmaz, herkes ayni dosyayi ceker.
 *
 * JS calismazsa veya bir uc hata verirse sayfa uretim anindaki statik
 * degerlerle kalir; hicbir sey bozulmaz (her adim fail-open).
 */
(function () {
  "use strict";

  var LANG = (document.documentElement.lang || "tr").slice(0, 2);
  var hero = document.querySelector(".hero");

  /* ---------- ortak ---------- */

  function bicim(v, dil) {
    return Number(v).toLocaleString(dil === "tr" ? "tr-TR" : "en-US",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function sma(d, n) {
    var dilim = d.slice(-n), t = 0, i;
    for (i = 0; i < dilim.length; i++) t += dilim[i];
    return dilim.length ? t / dilim.length : NaN;
  }

  function rsi(d, n) {
    var kazanc = 0, kayip = 0, i, fark;
    if (d.length < n + 1) return NaN;
    for (i = d.length - n; i < d.length; i++) {
      fark = d[i] - d[i - 1];
      if (fark >= 0) kazanc += fark; else kayip -= fark;
    }
    if (kayip === 0) return 100;
    return 100 - 100 / (1 + (kazanc / n) / (kayip / n));
  }

  /* ---------- 1) canli fiyat (hero + grafik serisi) ---------- */

  var serisi = document.getElementById("fiyat-serisi");
  if (serisi) {
    var kod = null;
    try { kod = JSON.parse(serisi.textContent).kod; } catch (e) { kod = null; }
    if (kod) {
      var fiyatEl = document.getElementById("hf-fiyat-sayi");
      var gunlukEl = document.getElementById("hf-gunluk-sayi");
      var gunlukKutu = document.getElementById("hf-gunluk");
      var veriNotu = document.getElementById("hf-veri");
      var idsizKip = false;

      var FIYAT_RE = /^\s*([0-9][0-9.,]*)\s*TL\s*$/;
      var YUZDE_RE = /^(\s*[-+]?\s*[0-9][0-9.,]*)\s*%/;

      (function idsizHero() {
        if (fiyatEl || !hero) return;
        var meta = hero.querySelector(".meta");
        if (!meta) return;
        var spanlar = meta.getElementsByTagName("span");
        var i, j, s, g;
        for (i = 0; i < spanlar.length; i++) {
          s = spanlar[i];
          if (s.id) continue;
          g = s.getElementsByTagName("strong")[0];
          if (!g || !FIYAT_RE.test(g.textContent)) continue;
          fiyatEl = g;
          for (j = i + 1; j < spanlar.length; j++) {
            s = spanlar[j];
            if (s.id) continue;
            if (!/pos|neg/.test(s.className)) continue;
            if (!YUZDE_RE.test(s.textContent)) continue;
            if (/[0-9]{4}/.test(s.textContent)) continue; // donem degisimi span'i
            gunlukEl = s;
            gunlukKutu = s;
            break;
          }
          idsizKip = true;
          break;
        }
      })();

      var canliImi = null;

      function istanbulBugunu() {
        return new Intl.DateTimeFormat("en-CA", {
          timeZone: "Europe/Istanbul", year: "numeric", month: "2-digit", day: "2-digit"
        }).format(new Date());
      }

      function seriGuncelle(fiyat) {
        var veri, t, f, bugun;
        try { veri = JSON.parse(serisi.textContent); } catch (e) { return; }
        t = veri.t || []; f = veri.f || [];
        if (!t.length || !f.length) return;
        bugun = istanbulBugunu();
        if (t[t.length - 1] === bugun) f[f.length - 1] = fiyat;
        else if (t[t.length - 1] < bugun) { t.push(bugun); f.push(fiyat); }
        else return;
        veri.t = t; veri.f = f;
        serisi.textContent = JSON.stringify(veri);
        serisi.setAttribute("data-guncel", "1");
        document.dispatchEvent(new CustomEvent("fiyat-serisi-guncel"));
      }

      function fiyatUygula(satir, zaman) {
        var yuzde = (satir.d >= 0 ? "+" : "") + bicim(satir.d, LANG) + "%";
        if (idsizKip) {
          fiyatEl.textContent = bicim(satir.f, LANG) + " TL";
          if (gunlukEl) {
            gunlukEl.textContent = gunlukEl.textContent.replace(YUZDE_RE, function () { return yuzde; });
            if (gunlukKutu) gunlukKutu.className = satir.d >= 0 ? "pos" : "neg";
          }
        } else if (fiyatEl) {
          fiyatEl.textContent = bicim(satir.f, LANG);
          if (gunlukEl) gunlukEl.textContent = yuzde;
          if (gunlukKutu) gunlukKutu.className = satir.d >= 0 ? "pos" : "neg";
        }
        if (veriNotu && zaman) {
          if (!canliImi) {
            canliImi = document.createElement("span");
            canliImi.style.color = "#047857";
            veriNotu.appendChild(document.createTextNode(" \u00b7 "));
            veriNotu.appendChild(canliImi);
          }
          canliImi.textContent = "\u25CF " + zaman;
        }
        seriGuncelle(satir.f);
      }

      function fiyatCek() {
        fetch("/api/fiyatlar?t=" + Date.now(), { cache: "no-store" })
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
          .then(function (v) {
            var liste = (v && v.hisseler) || [], i, satir = null;
            for (i = 0; i < liste.length; i++) if (liste[i].h === kod) { satir = liste[i]; break; }
            if (satir && satir.f > 0) fiyatUygula(satir, v.guncelleme || "");
          })
          .catch(function () { /* sessiz: statik degerler gecerli kalir */ });
      }
      fiyatCek();
      setInterval(fiyatCek, 10 * 60 * 1000);
    }
  }

  /* ---------- 2) canli rozet (gunluk teknik tarama sinyali) ---------- */

  var ROZET = {
    "GÜÇLÜ AL": { tr: "GÜÇLÜ AL", en: "STRONG BUY", de: "STARKER KAUF", ru: "СИЛЬНАЯ ПОКУПКА", zh: "强力买入" },
    "AL": { tr: "AL", en: "BUY", de: "KAUFEN", ru: "ПОКУПКА", zh: "买入" },
    "NÖTR": { tr: "NÖTR", en: "NEUTRAL", de: "NEUTRAL", ru: "НЕЙТРАЛЬНО", zh: "中性" },
    "TUT": { tr: "TUT", en: "HOLD", de: "HALTEN", ru: "ДЕРЖАТЬ", zh: "持有" },
    "NAKİT": { tr: "NAKİT", en: "CASH", de: "BAR", ru: "НАЛИЧНЫЕ", zh: "现金" },
    "SAT": { tr: "SAT", en: "SELL", de: "VERKAUFEN", ru: "ПРОДАЖА", zh: "卖出" },
    "GÜÇLÜ SAT": { tr: "GÜÇLÜ SAT", en: "STRONG SELL", de: "STARKER VERKAUF", ru: "СИЛЬНАЯ ПРОДАЖА", zh: "强力卖出" }
  };

  function rozetTazele() {
    if (!hero) return;
    var rozetEl = hero.querySelector(".meta .badge");
    if (!rozetEl) return;
    fetch("/ticker.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (v) {
        var liste = (v && v.hisseler) || [], i, satir = null;
        for (i = 0; i < liste.length; i++) if (liste[i].h === KOD_GLOBAL) { satir = liste[i]; break; }
        if (!satir) return;
        var esle = ROZET[satir.s];
        if (esle && esle[LANG]) rozetEl.textContent = esle[LANG];
      })
      .catch(function () { /* sessiz: uretim rozeti gecerli kalir */ });
  }

  /* ---------- 3) gunluk teknik inceleme blogu ---------- */

  var INCELEME_SOZ = {
    tr: { baslik: function (kod, ad) { return "Günlük Teknik " + kod + " – " + ad + " İncelemesi"; },
          kapanis: "son oturum kapanışı",
          uzerinde: "üzerinde", altinda: "altında", karisik: "ortalamalarla karışık",
          rsiAlim: "aşırı alım bölgesinde", rsiSatim: "aşırı satım bölgesinde",
          rsiUst: "nötr bölgenin üst yarısında", rsiAlt: "nötr bölgenin alt yarısında",
          dilimUst: "son 30 günün diliminin üst yarısında", dilimAlt: "son 30 günün diliminin alt yarısında",
          zirveye: "son 30 günün zirvesine %v mesafede", dibe: "son 30 günün dibine %v mesafede",
          kaynak: "Kaynak: piyasa açılışı öncesi kapanış serisi; blok gün boyu sabittir." },
    en: { baslik: function (kod, ad) { return "Daily Technical " + kod + " – " + ad + " Review"; },
          kapanis: "last session close",
          uzerinde: "above", altinda: "below", karisik: "mixed vs averages",
          rsiAlim: "overbought", rsiSatim: "oversold",
          rsiUst: "in the upper half of neutral", rsiAlt: "in the lower half of neutral",
          dilimUst: "in the upper half of the 30-day range", dilimAlt: "in the lower half of the 30-day range",
          zirveye: "%v below the 30-day high", dibe: "%v above the 30-day low",
          kaynak: "Source: pre-market closing series; this block stays fixed for the day." },
    de: { baslik: function (kod, ad) { return "Tägliche technische Analyse " + kod + " – " + ad; },
          kapanis: "Schlusskurs der letzten Handelssession",
          uzerinde: "über", altinda: "unter", karisik: "gemischt zu den Durchschnitten",
          rsiAlim: "im überkauften Bereich", rsiSatim: "im überverkauften Bereich",
          rsiUst: "in der oberen Hälfte des Neutralbereichs", rsiAlt: "in der unteren Hälfte des Neutralbereichs",
          dilimUst: "in der oberen Hälfte des 30-Tage-Bereichs", dilimAlt: "in der unteren Hälfte des 30-Tage-Bereichs",
          zirveye: "%v unter dem 30-Tage-Hoch", dibe: "%v über dem 30-Tage-Tief",
          kaynak: "Quelle: Schlusskursreihe vor Markteröffnung; der Block bleibt den Tag über fest." },
    ru: { baslik: function (kod, ad) { return "Ежедневный технический обзор " + kod + " – " + ad; },
          kapanis: "закрытие последней сессии",
          uzerinde: "выше", altinda: "ниже", karisik: "смешанно к средним",
          rsiAlim: "в зоне перекупленности", rsiSatim: "в зоне перепроданности",
          rsiUst: "в верхней половине нейтральной зоны", rsiAlt: "в нижней половине нейтральной зоны",
          dilimUst: "в верхней половине диапазона 30 дней", dilimAlt: "в нижней половине диапазона 30 дней",
          zirveye: "%v ниже максимума 30 дней", dibe: "%v выше минимума 30 дней",
          kaynak: "Источник: серия закрытий до открытия рынка; блок неизменен в течение дня." },
    zh: { baslik: function (kod, ad) { return kod + " – " + ad + " 每日技术评估"; },
          kapanis: "上一交易日收盘价",
          uzerinde: "高于", altinda: "低于", karisik: "与均线交叉",
          rsiAlim: "处于超买区", rsiSatim: "处于超卖区",
          rsiUst: "处于中性区上半部", rsiAlt: "处于中性区下半部",
          dilimUst: "处于30日区间上半部", dilimAlt: "处于30日区间下半部",
          zirveye: "距30日高点 v%", dibe: "距30日低点 v%",
          kaynak: "来源：开盘前收盘价序列；此板块当日保持不变。" }
  };

  function incelemeCumlesi(s, veri) {
    var f = veri.f, fiyat = f[f.length - 1];
    var s20 = sma(f, 20), s50 = sma(f, 50), r = rsi(f, 14);
    if (!isFinite(s20) || !isFinite(s50) || !isFinite(r)) return null;
    var dilim = f.slice(-30);
    var dip = Math.min.apply(null, dilim), zirve = Math.max.apply(null, dilim);
    var konum = zirve > dip ? (fiyat - dip) / (zirve - dip) : 0.5;
    var smaMetin = (fiyat > s20 && fiyat > s50) ? s.uzerinde :
                   (fiyat < s20 && fiyat < s50) ? s.altinda : s.karisik;
    var rsiMetin = r >= 70 ? s.rsiAlim : r <= 30 ? s.rsiSatim :
                   r >= 50 ? s.rsiUst : s.rsiAlt;
    var konumMetin;
    if (konum >= 0.8) konumMetin = s.zirveye.replace("v", bicim((1 - konum) * 100, LANG));
    else if (konum <= 0.2) konumMetin = s.dibe.replace("v", bicim(konum * 100, LANG));
    else if (konum > 0.5) konumMetin = s.dilimUst;
    else konumMetin = s.dilimAlt;
    var smaDil = LANG === "en" ? "is " + smaMetin + " SMA20 (" + bicim(s20, LANG) + ") and SMA50 (" + bicim(s50, LANG) + ")"
                               : "SMA20 (" + bicim(s20, LANG) + ") ve SMA50 (" + bicim(s50, LANG) + ") " + smaMetin;
    if (LANG === "zh") smaDil = "价格" + smaMetin + " SMA20 (" + bicim(s20, LANG) + ") 与 SMA50 (" + bicim(s50, LANG) + ")";
    if (LANG === "ru") smaDil = "цена " + smaMetin + " SMA20 (" + bicim(s20, LANG) + ") и SMA50 (" + bicim(s50, LANG) + ")";
    if (LANG === "de") smaDil = "der Kurs liegt " + smaMetin + " SMA20 (" + bicim(s20, LANG) + ") und SMA50 (" + bicim(s50, LANG) + ")";
    var rsiDil = LANG === "en" ? "RSI14 at " + bicim(r, LANG) + " (" + rsiMetin + ")"
                               : "RSI14 " + bicim(r, LANG) + " (" + rsiMetin + ")";
    return veri.kod + " " + bicim(fiyat, LANG) + " TL (" + s.kapanis + ") — " + smaDil + "; " + rsiDil + ", " + konumMetin + ".";
  }

  function incelemeKoy(veri) {
    if (!hero || !veri || !veri.kod || !veri.f || veri.f.length < 31) return;
    if (document.getElementById("gunluk-inceleme")) return; // cogaltma
    var s = INCELEME_SOZ[LANG] || INCELEME_SOZ.tr;
    var cumle = incelemeCumlesi(s, veri);
    if (!cumle) return;
    var ad = veri.ad || veri.kod;
    var kart = document.createElement("div");
    kart.className = "card";
    kart.id = "gunluk-inceleme";
    kart.style.margin = "14px 0 18px";
    kart.innerHTML =
      '<p style="font-weight:800; font-size:17px; margin:0 0 2px">' +
      s.baslik(veri.kod, ad) +
      ' <span style="color:var(--muted); font-size:12.5px; font-weight:400">· ' +
      (veri.guncelleme || "") + "</span></p>" +
      '<p style="margin:0; font-size:14.5px">' + cumle + "</p>" +
      '<p style="margin:6px 0 0; color:var(--muted); font-size:11.5px">' + s.kaynak + "</p>";
    hero.insertAdjacentElement("afterend", kart);
  }

  function incelemeCek(kodIstenen) {
    fetch("/seri/" + kodIstenen + ".json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(incelemeKoy)
      .catch(function () { /* sessiz: gunluk uretim yazana dek bloga yer acilmaz */ });
  }

  /* ---------- tetikleyiciler ---------- */

  var KOD_GLOBAL = null;
  try { KOD_GLOBAL = JSON.parse(document.getElementById("fiyat-serisi").textContent).kod; }
  catch (e) {
    var m = (document.querySelector(".hero h1") || { textContent: "" }).textContent.match(/^\s*([A-Z0-9]{3,6})\b/);
    KOD_GLOBAL = m ? m[1] : null;
  }
  if (KOD_GLOBAL) {
    rozetTazele();
    setInterval(rozetTazele, 10 * 60 * 1000);
    incelemeCek(KOD_GLOBAL);
  }
})();
