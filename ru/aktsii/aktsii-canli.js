/* Hisse sayfasi canli fiyat: /api/fiyatlar ucundan (TradingView, ~15 dk gecikmeli,
 * kenar onbellek 9 dk) bu sayfanin hissesini bulur; hero'daki fiyat ve gunluk %
 * sayilarini tazeler, grafik serisinin son noktasini canli fiyata cekeker ve
 * grafik.js'i yeniden cizim icin tetikler. JS calismazsa veya API hata verirse
 * sayfa uretim anindaki statik degerlerle kalir; hicbir sey bozulmaz.
 * 2026-11-09 (kullanici talebi): hisse sayfasinda fiyat ve grafik canli olmaliydi;
 * sayfalar gunluk uretiliyor, gun ici tazelik /api/fiyatlar'da bekliyordu.
 */
(function () {
  "use strict";

  var serisi = document.getElementById("fiyat-serisi");
  var fiyatEl = document.getElementById("hf-fiyat-sayi");
  var gunlukEl = document.getElementById("hf-gunluk-sayi");
  var gunlukKutu = document.getElementById("hf-gunluk");
  var veriNotu = document.getElementById("hf-veri");
  if (!serisi || !fiyatEl) return;

  var kod;
  try { kod = JSON.parse(serisi.textContent).kod; } catch (e) { return; }
  if (!kod) return;

  var canliImi = null;

  function sayi(v) {
    return Number(v).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function istanbulBugunu() {
    // ISO "YYYY-MM-DD", Istanbul gunuyle (seri tarihleri BIST gunleridir)
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Istanbul", year: "numeric", month: "2-digit", day: "2-digit"
    }).format(new Date());
  }

  // Serinin son noktasi bugunse canli fiyatla DEGISTIRILIR (uretim anlik
  // fotografdi); seri bugunden eskiyse bugun yeni nokta olarak EKLENIR.
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

  function uygula(satir, zaman) {
    fiyatEl.textContent = sayi(satir.f);
    if (gunlukEl && typeof satir.d === "number") {
      gunlukEl.textContent = (satir.d >= 0 ? "+" : "") + sayi(satir.d) + "%";
      if (gunlukKutu) gunlukKutu.className = satir.d >= 0 ? "pos" : "neg";
    }
    if (veriNotu && zaman) {
      // Tek bir yesil nokta-damga; her yenilemede cogalmaz, yalnizca saati gunceller.
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

  function cek() {
    fetch("/api/fiyatlar?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (v) {
        var liste = (v && v.hisseler) || [], i, satir = null;
        for (i = 0; i < liste.length; i++) if (liste[i].h === kod) { satir = liste[i]; break; }
        if (satir && satir.f > 0) uygula(satir, v.guncelleme || "");
      })
      .catch(function () { /* sessiz: statik degerler gecerli kalir */ });
  }

  cek();
  setInterval(cek, 10 * 60 * 1000); // kenar onbellek 9 dk; 10 dk'da bir tazele
})();
