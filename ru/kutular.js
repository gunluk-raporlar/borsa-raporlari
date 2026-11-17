/* Ana sayfa BIST 30 kutulari (v1, 2026-11-11 kullanici talebi: "teknik
 * taramadaki isi kutusu gibi ama fiyatli"): kapsam kutucuklarina canli fiyat ve
 * gunluk % yazar, zemin rgba'sini Gunluk Isi Haritasi ile AYNI olcekle gunceller
 * (|degisim| buyudukce koyulastir). Veri: /api/fiyatlar (TradingView, ~15 dk
 * gecikmeli, kenar onbellek 9 dk) — 10 dk'da bir tazeleme. JS calismazsa veya
 * API hata verirse bot'un yazdigi statik kapanis degerleri kalir; hicbir sey
 * bozulmaz (fail-open). Dosya adi bilinclisiz token'sizdir ("hisse" vb. yok):
 * i18n faz-3 slug yazicisi dil agaclarinda src yolunu degistiremez, mutlak
 * /kutular.js her agactan kok dosyaya ulasir.
 *
 * 2026-11-17 (kullanici bildirimi: "renkler degisiyor ama yerler degismiyor"):
 * kutular artik canli veriye gore de SIRALANIR — gunluk degisim yuksekten alcaga,
 * verisi olmayanlar sonda. Statik HTML zaten bu sirada uretiliyor (bot.py
 * _d_anahtar); boylece ilk boyamadan sonra renk ile konum tutarli kalir.
 * Siralama FLIP ile animasyonlanir (once konum olculur, DOM yeniden dizilir,
 * fark transform ile geri alinip sifira gecirilir). Imlec kutularin uzerindeyken
 * siralama ERTELENIR: tam tiklanacak kutunun yerinden kaymasi engellenir.
 * prefers-reduced-motion acikken animasyon atlanir, siralama yine yapilir.
 */
(function () {
  "use strict";

  var LANG = (document.documentElement.lang || "tr").slice(0, 2);
  var kutular = document.querySelectorAll(".hkutu[data-kod]");
  if (!kutular.length) return;
  var izgara = document.querySelector(".hkutu-izgara");

  function bicim(v) {
    return Number(v).toLocaleString(LANG === "tr" ? "tr-TR" : "en-US",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Gunluk Isi Haritasi ile ayni olcek: |degisim| buyudukce zemin koyulastir
  function arka(d) {
    var a = Math.min(0.15 + Math.abs(d) / 3.0 * 0.80, 0.92);
    return (d >= 0 ? "4,120,87" : "185,28,28") + "," + a.toFixed(2);
  }

  // 2026-11-13: metin rengi zemin parlakligina gore (acik temada okunurluk).
  // Esik 0.18 — altinda beyaz, ustunde siyah; karanlik temada CSS beyaza sabitler.
  function metin(d) {
    var a = Math.min(0.15 + Math.abs(d) / 3.0 * 0.80, 0.92);
    var r = d >= 0 ? 4 : 185, g = d >= 0 ? 120 : 28, b = d >= 0 ? 87 : 28;
    function lin(c) {
      c = (c * a + 255 * (1 - a)) / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }
    var L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    return L < 0.18 ? "#fff" : "#000";
  }

  // ---- canli siralama (2026-11-17) ----
  var sonHarita = null;
  var uzerinde = false;   // imlec kutularin uzerinde mi
  var bekleyen = false;   // imlec kalkinca uygulanacak siralama var mi

  function anahtar(el, harita) {
    var s = harita[el.getAttribute("data-kod")];
    return s && typeof s.d === "number" && isFinite(s.d) ? s.d : -Infinity;
  }

  function sirala(harita) {
    if (!izgara) return;
    var mevcut = Array.prototype.slice.call(izgara.children);
    if (mevcut.length < 2) return;

    var sirali = mevcut.slice().sort(function (a, b) {
      var da = anahtar(a, harita), db = anahtar(b, harita);
      if (da === db) return 0;
      return db - da;   // yuksekten alcaga; verisizler (-Infinity) sonda
    });

    var ayni = true, i;
    for (i = 0; i < sirali.length; i++) {
      if (sirali[i] !== mevcut[i]) { ayni = false; break; }
    }
    if (ayni) return;   // sira degismediyse DOM'a hic dokunma

    var azHareket = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // FLIP: once eski konumlar
    var once = null;
    if (!azHareket) {
      once = {};
      for (i = 0; i < mevcut.length; i++) {
        once[mevcut[i].getAttribute("data-kod")] = mevcut[i].getBoundingClientRect();
      }
    }

    for (i = 0; i < sirali.length; i++) izgara.appendChild(sirali[i]);
    if (azHareket || !once) return;

    // yeni konumdan eski konuma isinla, sonra sifira gecir
    var tasinan = [], el, a, b, dx, dy;
    for (i = 0; i < sirali.length; i++) {
      el = sirali[i];
      a = once[el.getAttribute("data-kod")];
      if (!a) continue;
      b = el.getBoundingClientRect();
      dx = a.left - b.left;
      dy = a.top - b.top;
      if (!dx && !dy) continue;
      el.style.transition = "none";
      el.style.transform = "translate(" + dx + "px," + dy + "px)";
      tasinan.push(el);
    }
    if (!tasinan.length) return;

    void izgara.offsetWidth;   // tek yeniden akis: baslangic konumu sabitlensin
    for (i = 0; i < tasinan.length; i++) {
      tasinan[i].style.transition = "transform .5s cubic-bezier(.2,.7,.3,1)";
      tasinan[i].style.transform = "";
    }
  }

  function uygula(liste) {
    var harita = {};
    var i;
    for (i = 0; i < liste.length; i++) harita[liste[i].h] = liste[i];
    kutular.forEach(function (kutu) {
      var s = harita[kutu.getAttribute("data-kod")];
      if (!s || !(s.f > 0)) return;
      var f = kutu.querySelector(".hfiyat"), d = kutu.querySelector(".hdeg");
      if (f) f.textContent = bicim(s.f) + " TL";
      if (d) d.textContent = (s.d >= 0 ? "+" : "") + bicim(s.d) + "%";
      kutu.style.background = "rgba(" + arka(s.d) + ")";
      kutu.style.setProperty("--hk-metin", metin(s.d));
    });
    sonHarita = harita;
    if (uzerinde) { bekleyen = true; return; }   // imlec uzerindeyken yer degistirme
    sirala(harita);
  }

  if (izgara) {
    izgara.addEventListener("pointerenter", function (e) {
      if (e.pointerType === "mouse" || e.pointerType === "pen") uzerinde = true;
    });
    izgara.addEventListener("pointerleave", function (e) {
      if (e.pointerType && e.pointerType !== "mouse" && e.pointerType !== "pen") return;
      uzerinde = false;
      if (bekleyen && sonHarita) { bekleyen = false; sirala(sonHarita); }
    });
  }

  function cek() {
    fetch("/api/fiyatlar?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (v) { uygula((v && v.hisseler) || []); })
      .catch(function () { /* sessiz: statik degerler gecerli kalir */ });
  }

  cek();
  setInterval(cek, 10 * 60 * 1000);
})();
