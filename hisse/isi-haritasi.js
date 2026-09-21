/*
 * BIST30 Canlı Isı Haritası — kendi kendine yeten (self-contained) widget.
 *
 * Veri: /api/fiyatlar (Cloudflare Pages Function → TradingView, kenar
 * önbelleği ~9 dk). Sayfa açık kaldığı sürece her 10 dakikada bir otomatik
 * yenilenir; API erişilemezse gun sonu ticker.json ile gösterim sürer.
 *
 * Kare boyutu piyasa değerine (piyasa değeri yoksa eşit), renk önceki
 * kapanışa göre gün içi değişime göre boyanır (±%5 doygunluk).
 *
 * Kullanım: sayfada <div id="isi-haritasi"></div> + bu dosyayı defer ile yükle.
 */
(function () {
  "use strict";

  var PERIYOT = 10 * 60 * 1000; // 10 dk otomatik yenileme

  // Bu script nereden yüklendiyse kök orasıdır (hisse/ alt klasörü).
  var KOK = "../";
  try {
    var src = (document.currentScript && document.currentScript.src) || "";
    if (src) KOK = src.replace(/isi-haritasi\.js.*$/, "");
  } catch (e) {}

  var CSS = [
    ".isi-kutu{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:26px 0 8px}",
    ".isi-baslik-satir{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px}",
    ".isi-baslik{margin:0;font-size:19px;padding-left:12px;border-left:4px solid var(--accent);line-height:1.35}",
    ".isi-sag{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--muted);flex-wrap:wrap}",
    "#isi-yenile{background:var(--accent-bg);border:1px solid var(--line);color:var(--accent);border-radius:8px;padding:4px 10px;cursor:pointer;font-size:12.5px;font-weight:600}",
    "#isi-yenile:hover{border-color:var(--accent)}",
    "#isi-yenile:disabled{opacity:.55;cursor:wait}",
    ".isi-legend{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--muted);margin:0 0 8px}",
    ".isi-legend-bar{flex:1;max-width:240px;height:8px;border-radius:4px;border:1px solid var(--line);",
    "background:linear-gradient(90deg,#7f1d1d,#dc2626,#fecaca,#f8fafc,#d1fae5,#34d399,#047857)}",
    ".isi-treemap{position:relative;width:100%;height:clamp(340px,52vw,470px);border-radius:10px;overflow:hidden;background:var(--bg);border:1px solid var(--line)}",
    ".isi-kare{position:absolute;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;padding:3px 7px;text-decoration:none;overflow:hidden;white-space:nowrap;box-shadow:inset 0 0 0 1px rgba(255,255,255,.22);transition:filter .12s}",
    ".isi-kare:hover{filter:brightness(1.09);z-index:2;box-shadow:inset 0 0 0 2px rgba(255,255,255,.6)}",
    ".isi-kod{font-weight:700;letter-spacing:.3px;line-height:1.15}",
    ".isi-yuzde{line-height:1.2;opacity:.95}",
    ".isi-fiyat{line-height:1.2;opacity:.88;font-size:.85em}",
    ".isi-not{color:var(--muted);font-size:12px;margin-top:8px}",
    ".isi-hata{color:var(--neg)}"
  ].join("\n");

  var durumEl = null, treemapEl = null, mesgul = false, sonZaman = 0, sonVeri = null;

  function kur() {
    var hedef = document.getElementById("isi-haritasi");
    if (!hedef) return;
    var stil = document.createElement("style");
    stil.textContent = CSS;
    document.head.appendChild(stil);
    hedef.innerHTML =
      '<div class="isi-kutu">' +
        '<div class="isi-baslik-satir">' +
          '<h2 class="isi-baslik">📊 Canlı Piyasa Isı Haritası</h2>' +
          '<div class="isi-sag">' +
            '<span id="isi-durum">Yükleniyor…</span>' +
            '<button type="button" id="isi-yenile" title="Şimdi yenile">⟳ Yenile</button>' +
          '</div>' +
        '</div>' +
        '<div class="isi-legend"><span>−%5 ve altı</span><div class="isi-legend-bar"></div><span>+%5 ve üstü</span></div>' +
        '<div class="isi-treemap" id="isi-treemap"></div>' +
        '<div class="isi-not">Kare boyutu piyasa değerini, renk önceki kapanışa göre gün içi değişimi gösterir. ' +
        'Her 10 dakikada bir otomatik güncellenir · Kaynak: TradingView (~15 dk gecikmeli) · ' +
        'Hisse detay sayfası için kareye tıklayın. Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.</div>' +
      '</div>';
    durumEl = document.getElementById("isi-durum");
    treemapEl = document.getElementById("isi-treemap");
    document.getElementById("isi-yenile").addEventListener("click", function () { tazele(); });
    tazele();
    setInterval(function () { tazele(); }, PERIYOT);
    // Arka planda kalan sekme geri geldiğinde 10 dk dolduysa hemen tazele.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && sonZaman && Date.now() - sonZaman >= PERIYOT) tazele();
    });
    // Pencere boyutu değişince treemapi yeniden yerleştir (yeni veri çekmeden).
    var boyutZamanAsimi = null;
    window.addEventListener("resize", function () {
      if (!sonVeri || !treemapEl) return;
      if (boyutZamanAsimi) clearTimeout(boyutZamanAsimi);
      boyutZamanAsimi = setTimeout(function () { ciz(sonVeri); }, 200);
    });
  }

  function durum(metin, hataMi) {
    if (durumEl) {
      durumEl.textContent = metin;
      durumEl.className = hataMi ? "isi-hata" : "";
    }
  }

  function apiCek() {
    return fetch("/api/fiyatlar?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (v) {
        if (!v || !Array.isArray(v.hisseler) || !v.hisseler.length) throw new Error("boş veri");
        return v;
      });
  }

  function tickerCek() {
    return fetch(KOK + "ticker.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (v) {
        if (!v || !Array.isArray(v.hisseler) || !v.hisseler.length) throw new Error("boş veri");
        return v;
      });
  }

  function tazele() {
    if (mesgul || !treemapEl) return;
    mesgul = true;
    var btn = document.getElementById("isi-yenile");
    if (btn) btn.disabled = true;
    if (sonZaman) durum("Güncelleniyor…");
    apiCek()
      .then(function (v) {
        ciz(v, "canlı");
        kartGuncelle(v);
        durum("Son güncelleme: " + (v.guncelleme || "--:--") + " · " + (v.etiket || "canlı"));
      })
      .catch(function () {
        // API yoksa (ör. Functions henüz deploy edilmedi) günlük veriyle göster.
        tickerCek()
          .then(function (v) {
            ciz(v, "gunluk");
            durum("Günlük kapanış verisi (" + (v.guncelleme || "") + ") · canlı veri şu anda alınamıyor", true);
          })
          .catch(function () {
            durum("Veri alınamadı — birkaç dakika içinde yeniden denenecek", true);
          });
      })
      .then(function () {
        mesgul = false;
        if (btn) btn.disabled = false;
      });
  }

  /* ---------- squarified treemap ---------- */

  // items: [{a: px2, h: veri}]; dikdörtgeni en kare görünümlü satırlara böler.
  function yerlesim(items, x, y, w, h, out) {
    if (!items.length || w < 1 || h < 1) return;
    if (items.length === 1) {
      out.push({ v: items[0].h, x: x, y: y, w: w, h: h });
      return;
    }
    var toplam = 0, i;
    for (i = 0; i < items.length; i++) toplam += items[i].a;
    var dikey = w >= h;          // dikeyse sıra sol kolona yerleşir
    var kisa = dikey ? h : w;    // sıranın boyunca uzanacağı kenar

    var sira = [items[0]];
    var siraToplam = items[0].a;
    var enKotu = enKotuOran(sira, siraToplam, kisa);
    var n = 1;
    while (n < items.length) {
      var aday = sira.concat([items[n]]);
      var adayToplam = siraToplam + items[n].a;
      var oran = enKotuOran(aday, adayToplam, kisa);
      if (oran <= enKotu) { sira = aday; siraToplam = adayToplam; enKotu = oran; n++; }
      else break;
    }

    var kalinlik = Math.min(siraToplam / kisa, dikey ? w : h);
    var konum = 0;
    for (i = 0; i < sira.length; i++) {
      var uzunluk = sira[i].a / (siraToplam / kisa);
      if (dikey) out.push({ v: sira[i].h, x: x, y: y + konum, w: kalinlik, h: uzunluk });
      else out.push({ v: sira[i].h, x: x + konum, y: y, w: uzunluk, h: kalinlik });
      konum += uzunluk;
    }
    var kalan = items.slice(sira.length);
    if (dikey) yerlesim(kalan, x + kalinlik, y, w - kalinlik, h, out);
    else yerlesim(kalan, x, y + kalinlik, w, h - kalinlik, out);
  }

  function enKotuOran(sira, siraToplam, kisa) {
    var kalinlik = siraToplam / kisa;
    var enKotu = 0;
    for (var i = 0; i < sira.length; i++) {
      var uzunluk = sira[i].a / kalinlik;
      var r = Math.max(kalinlik / uzunluk, uzunluk / kalinlik);
      if (r > enKotu) enKotu = r;
    }
    return enKotu;
  }

  /* ---------- render ---------- */

  function renk(d) {
    var t = Math.min(Math.abs(d) / 5, 1); // ±%5'te doygun
    var acik = d >= 0 ? [209, 250, 229] : [254, 202, 202];
    var koyu = d >= 0 ? [4, 120, 87] : [185, 28, 28];
    var c = acik.map(function (l, i) { return Math.round(l + (koyu[i] - l) * t); });
    return { bg: "rgb(" + c.join(",") + ")", fg: t >= 0.5 ? "#fff" : "#0f172a" };
  }

  function sayi(f, sonek) {
    return f.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + sonek;
  }

  // hisse/index.html'deki kartların fiyat + % bilgilerini ısı haritasının
  // kullandığı aynı canlı veriyle güncelle (sinyal metni sabah analizinden kalır)
  function kartGuncelle(veri) {
    var kartlar = document.querySelectorAll(".rcard[data-kod]");
    if (!kartlar.length) return;
    var harita = {};
    (veri.hisseler || []).forEach(function (h) { harita[h.h] = h; });
    Array.prototype.forEach.call(kartlar, function (kart) {
      var h = harita[kart.getAttribute("data-kod")];
      if (!h || typeof h.f !== "number") return;
      var alt = kart.querySelectorAll(".sub");
      if (alt.length < 3) return;
      var sinyal = (alt[1].textContent.split("•")[0] || "").trim();
      alt[1].textContent = sinyal + " • " +
        h.f.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " TL";
      alt[2].textContent = (h.d > 0 ? "+" : h.d < 0 ? "-" : "") +
        Math.abs(h.d).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%";
      alt[2].className = "sub " + (h.d >= 0 ? "pos" : "neg");
    });
  }
  function yuzde(d) {
    return (d > 0 ? "+" : d < 0 ? "-" : "") + sayi(Math.abs(d), "%");
  }

  function ciz(veri, tip) {
    sonZaman = Date.now();
    sonVeri = veri;
    var hisseler = veri.hisseler.filter(function (h) { return typeof h.f === "number" && h.f > 0; });
    if (!hisseler.length) return;
    var mcapVar = hisseler.some(function (h) { return typeof h.m === "number" && h.m > 0; });

    var items = hisseler.map(function (h) {
      // Piyasa degerini 0.6. kuvvetle olcekle: dev hisseler haritayi tumuyle
      // isgal etmesin, kucuk hisselerin etiketi okunur kalsin.
      return { a: (mcapVar && typeof h.m === "number" && h.m > 0) ? Math.pow(h.m, 0.6) : 1, h: h };
    });
    if (mcapVar) items.sort(function (a, b) { return b.a - a.a; });
    else items.sort(function (a, b) { return a.h.h < b.h.h ? -1 : 1; });

    var toplam = 0;
    items.forEach(function (it) { toplam += it.a; });
    var W = treemapEl.clientWidth || 600, H = treemapEl.clientHeight || 400;
    var olcek = (W * H) / toplam;
    items.forEach(function (it) { it.a *= olcek; });

    var kutular = [];
    yerlesim(items, 0, 0, W, H, kutular);

    var html = "";
    kutular.forEach(function (k) {
      var h = k.v;
      var r = renk(h.d);
      var mini = k.w < 46 || k.h < 34;
      var fiyatGoster = k.w >= 108 && k.h >= 82;
      var yuzdeGoster = !mini;
      var baslik = h.h + " · " + sayi(h.f, " TL") + " · " + yuzde(h.d) + " (önceki kapanışa göre)";
      html +=
        '<a class="isi-kare" href="' + h.h + '.html" style="left:' + k.x.toFixed(1) + "px;top:" + k.y.toFixed(1) +
        "px;width:" + k.w.toFixed(1) + "px;height:" + k.h.toFixed(1) + "px;background:" + r.bg + ";color:" + r.fg +
        (mini ? ";font-size:10.5px" : "") + '" title="' + baslik.replace(/"/g, "&quot;") + '">' +
        '<span class="isi-kod">' + h.h + "</span>" +
        (yuzdeGoster ? '<span class="isi-yuzde">' + yuzde(h.d) + "</span>" : "") +
        (fiyatGoster ? '<span class="isi-fiyat">' + sayi(h.f, " TL") + "</span>" : "") +
        "</a>";
    });
    treemapEl.innerHTML = html;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", kur);
  } else {
    kur();
  }
})();
