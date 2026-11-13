/* Ana sayfa BIST 30 kutulari (v1, 2026-11-11 kullanici talebi: "teknik
 * taramadaki isi kutusu gibi ama fiyatli"): kapsam kutucuklarina canli fiyat ve
 * gunluk % yazar, zemin rgba'sini Gunluk Isi Haritasi ile AYNI olcekle gunceller
 * (|degisim| buyudukce koyulastir). Veri: /api/fiyatlar (TradingView, ~15 dk
 * gecikmeli, kenar onbellek 9 dk) — 10 dk'da bir tazeleme. JS calismazsa veya
 * API hata verirse bot'un yazdigi statik kapanis degerleri kalir; hicbir sey
 * bozulmaz (fail-open). Dosya adi bilinclisiz token'sizdir ("hisse" vb. yok):
 * i18n faz-3 slug yazicisi dil agaclarinda src yolunu degistiremez, mutlak
 * /kutular.js her agactan kok dosyaya ulasir.
 */
(function () {
  "use strict";

  var LANG = (document.documentElement.lang || "tr").slice(0, 2);
  var kutular = document.querySelectorAll(".hkutu[data-kod]");
  if (!kutular.length) return;

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
