/* Hisse sayfasi fiyat grafigi: sunucuda cizilen SVG'yi tarayicide yeniden uretir.
 * Aralik dugmeleri (1A/3A/6A/1Y/Tumu), SMA20/50 hesabi ve hover ipucu burada.
 * JS calismazsa sunucudaki statik SVG ve referans degerleri oldugu gibi kalir.
 * Veri: sayfaya gomulu <script type="application/json" id="fiyat-serisi">.
 */
(function () {
  "use strict";

  var kutu = document.getElementById("grafik-kutu");
  var aralikKutu = document.getElementById("grafik-aralik");
  var ipucu = document.getElementById("grafik-ipucu");
  var veriEl = document.getElementById("fiyat-serisi");
  if (!kutu || !aralikKutu || !veriEl) return;

  var veri;
  try { veri = JSON.parse(veriEl.textContent); } catch (e) { return; }
  var T = veri.t || [], F = (veri.f || []).map(Number);
  if (T.length !== F.length || T.length < 2) return;

  var G = 640, H = 280;
  var KENAR = { sol: 54, sag: 14, ust: 18, alt: 26 };
  var PW = G - KENAR.sol - KENAR.sag, PH = H - KENAR.ust - KENAR.alt;
  var SMA_RENK = { 20: "#d97706", 50: "#2563eb" };
  var ARIALIK = [["1A", 30], ["3A", 91], ["6A", 183], ["1Y", 365], ["tumu", 0]];

  var CEVIRI = {
    tr: { tumu: "Tümü", min: "En düşük", max: "En yüksek", gunluk: "günlük",
          gun: "son {n} gün" },
    ru: { tumu: "Всё", min: "Минимум", max: "Максимум", gunluk: "за день",
          gun: "последние {n} дн." },
    zh: { tumu: "全部", min: "最低", max: "最高", gunluk: "日变动",
          gun: "近 {n} 天" },
    en: { tumu: "All", min: "Lowest", max: "Highest", gunluk: "daily",
          gun: "last {n} days" }
  };
  function soz(anahtar) {
    var dil = (document.documentElement.lang || "tr").slice(0, 2);
    return (CEVIRI[dil] || CEVIRI.tr)[anahtar];
  }
  function sayi(v, hane) { return v.toFixed(hane).replace(".", ","); }
  function tarihKisa(t) { return t.slice(8, 10) + "." + t.slice(5, 7); }
  function tarihUzun(t) { return t.slice(8, 10) + "." + t.slice(5, 7) + "." + t.slice(0, 4); }

  function sma(dizi, pencere) {
    var cikti, toplam, i;
    cikti = []; toplam = 0;
    for (i = 0; i < dizi.length; i++) {
      toplam += dizi[i];
      if (i >= pencere) toplam -= dizi[i - pencere];
      cikti.push(i >= pencere - 1 ? toplam / pencere : null);
    }
    return cikti;
  }

  function izgara(lo, hi) {
    var ham, kuv, adim, carpanlar, bas, out, v;
    if (!(hi > lo)) return [];
    ham = (hi - lo) / 4;
    kuv = Math.pow(10, Math.floor(Math.log(ham) / Math.LN10));
    adim = 10 * kuv;
    carpanlar = [1, 2, 2.5, 5, 10];
    for (v = 0; v < carpanlar.length; v++) {
      if (carpanlar[v] * kuv >= ham) { adim = carpanlar[v] * kuv; break; }
    }
    bas = Math.ceil(lo / adim) * adim;
    out = [];
    for (v = bas; v <= hi + 1e-9; v += adim) out.push(Number(v.toPrecision(10)));
    return out;
  }

  function svgEl() { return document.getElementById("grafik-svg"); }

  var durum = { t: T, f: F, adimX: PW / (T.length - 1), kaydir: 0 };

  function pencereVerisi(gun) {
    var kes, i;
    if (!gun) return { t: T.slice(), f: F.slice() };
    kes = new Date(T[T.length - 1]).getTime() - gun * 864e5;
    i = 0;
    while (i < T.length && new Date(T[i]).getTime() < kes) i++;
    if (T.length - i < 2) i = Math.max(0, T.length - 2);
    return { t: T.slice(i), f: F.slice(i) };
  }

  function lejantYaz(smalar) {
    var lejantEl = kutu.parentElement ? kutu.parentElement.querySelector(".grafik-lejant") : null;
    if (!lejantEl) return;
    lejantEl.innerHTML = Object.keys(smalar).map(Number).sort(function (a, b) { return a - b; })
      .map(function (per) {
        var s = smalar[per];
        return '<span><i class="' + (per === 20 ? "lj-duz" : "") + '" style="color:' +
          SMA_RENK[per] + '"></i>SMA' + per + " (" + sayi(s[s.length - 1], 2) + " TL)</span>";
      }).join("");
  }

  function donemYaz(deg) {
    var el = document.getElementById("grafik-donem");
    if (!el) return;
    el.className = deg >= 0 ? "pos" : "neg";
    el.textContent = sayi(deg, 1) + "%";
  }

  function gunYaz(adet) {
    var el = document.getElementById("grafik-gun");
    if (el) el.textContent = soz("gun").replace("{n}", adet);
  }

  function ciz(gun) {
    var p, t, f, n, smalar, lo, hi, tampon, adimX, renk, deg;
    var b, j, i, v, s, bas, ps, q, iMin, iMax, ciftler, c, x, y, ex, ey, qSon, iso;
    p = pencereVerisi(gun);
    t = p.t; f = p.f; n = f.length;
    smalar = {};
    [20, 50].forEach(function (per) { if (n >= per + 1) smalar[per] = sma(f, per); });
    lo = Math.min.apply(null, f); hi = Math.max.apply(null, f);
    Object.keys(smalar).forEach(function (k) {
      smalar[k].forEach(function (val) {
        if (val !== null) { if (val < lo) lo = val; if (val > hi) hi = val; }
      });
    });
    tampon = hi > lo ? (hi - lo) * 0.06 : Math.max(Math.abs(hi) * 0.02, 0.5);
    lo -= tampon; hi += tampon;
    adimX = PW / (n - 1);
    durum = { t: t, f: f, adimX: adimX, kaydir: T.length - n, lo: lo, hi: hi };

    function xy(idx, val) {
      return [KENAR.sol + idx * adimX, KENAR.ust + (1 - (val - lo) / (hi - lo)) * PH];
    }
    function noktalar(dizi) {
      return dizi.map(function (val, idx) {
        q = xy(idx, val);
        return q[0].toFixed(1) + "," + q[1].toFixed(1);
      }).join(" ");
    }

    renk = f[n - 1] >= f[0] ? "#047857" : "#b91c1c";
    deg = (f[n - 1] / f[0] - 1) * 100;
    b = ['<defs><linearGradient id="jsglg" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + renk + '" stop-opacity=".14"/>' +
      '<stop offset="1" stop-color="' + renk + '" stop-opacity="0"/></linearGradient></defs>'];

    izgara(lo, hi).forEach(function (deger) {
      y = xy(0, deger)[1].toFixed(1);
      b.push('<line x1="' + KENAR.sol + '" y1="' + y + '" x2="' + (G - KENAR.sag) + '" y2="' + y +
        '" stroke="var(--line)" stroke-width="1"/>');
      b.push('<text x="' + (KENAR.sol - 7) + '" y="' + (Number(y) + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="10.5" fill="var(--muted)">' + sayi(deger, 2) + "</text>");
    });
    for (j = 0; j < 5; j++) {
      i = Math.round(j * (n - 1) / 4);
      x = Math.min(Math.max(xy(i, lo)[0], KENAR.sol + 16), G - KENAR.sag - 16).toFixed(1);
      b.push('<text x="' + x + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10.5" ' +
        'fill="var(--muted)">' + tarihKisa(t[i]) + "</text>");
    }
    y = xy(0, f[0])[1].toFixed(1);
    b.push('<line x1="' + KENAR.sol + '" y1="' + y + '" x2="' + (G - KENAR.sag) + '" y2="' + y +
      '" stroke="var(--muted)" stroke-width="1" stroke-dasharray="2 4"/>');
    b.push('<polygon fill="url(#jsglg)" points="' + noktalar(f) + " " +
      (KENAR.sol + (n - 1) * adimX).toFixed(1) + "," + (KENAR.ust + PH).toFixed(1) + " " +
      KENAR.sol + "," + (KENAR.ust + PH).toFixed(1) + '"/>');
    b.push('<polyline fill="none" stroke="' + renk + '" stroke-width="2" points="' + noktalar(f) + '"/>');
    Object.keys(smalar).map(Number).sort(function (a, b2) { return a - b2; }).forEach(function (per) {
      s = smalar[per];
      bas = 0;
      while (bas < n && s[bas] === null) bas++;
      ps = [];
      for (i = bas; i < n; i++) {
        q = xy(i, s[i]);
        ps.push(q[0].toFixed(1) + "," + q[1].toFixed(1));
      }
      b.push('<polyline fill="none" stroke="' + SMA_RENK[per] + '" stroke-width="1.6" ' +
        'stroke-dasharray="5 4" points="' + ps.join(" ") + '"/>');
    });

    iMin = 0; iMax = 0;
    for (i = 1; i < n; i++) {
      if (f[i] < f[iMin]) iMin = i;
      if (f[i] > f[iMax]) iMax = i;
    }
    if (f[iMin] !== f[iMax]) {
      ciftler = [[iMin, soz("min"), 16], [iMax, soz("max"), -12]];
      for (c = 0; c < ciftler.length; c++) {
        i = ciftler[c][0];
        q = xy(i, f[i]);
        x = Math.min(Math.max(q[0], KENAR.sol + 56), G - KENAR.sag - 56).toFixed(1);
        y = Math.min(Math.max(q[1] + ciftler[c][2], KENAR.ust + 4), H - KENAR.alt - 4).toFixed(1);
        b.push('<circle cx="' + q[0].toFixed(1) + '" cy="' + q[1].toFixed(1) + '" r="3" fill="' + renk + '"/>');
        b.push('<text x="' + x + '" y="' + y + '" text-anchor="middle" font-size="10.5" ' +
          'font-weight="700" fill="var(--muted)">' + ciftler[c][1] + " " + sayi(f[i], 2) +
          " TL · " + tarihKisa(t[i]) + "</text>");
      }
    }
    qSon = xy(n - 1, f[n - 1]);
    b.push('<circle cx="' + qSon[0].toFixed(1) + '" cy="' + qSon[1].toFixed(1) + '" r="3.5" fill="' + renk + '"/>');
    b.push('<text x="' + (qSon[0] - 7).toFixed(1) + '" y="' + (qSon[1] - 8).toFixed(1) +
      '" text-anchor="end" font-size="11.5" font-weight="700" fill="' + renk + '">' +
      sayi(f[n - 1], 2) + " TL</text>");
    b.push('<line id="jsimlec" x1="0" y1="' + KENAR.ust + '" x2="0" y2="' + (KENAR.ust + PH) +
      '" stroke="var(--muted)" stroke-width="1" visibility="hidden"/>');
    b.push('<circle id="jsimlecnokta" r="4" fill="var(--ink)" visibility="hidden"/>');

    svgEl().outerHTML = '<svg viewBox="0 0 ' + G + " " + H + '" id="grafik-svg" class="chart" ' +
      'preserveAspectRatio="xMidYMid meet" role="img" aria-label="' + t[0] + "-" + t[n - 1] +
      " arasi " + n + ' gunluk kapanis fiyati grafigi" style="width:100%;height:auto;display:block">' +
      b.join("") + "</svg>";
    lejantYaz(smalar);
    donemYaz(deg);
    gunYaz(n);
  }

  // --- hover: dikimlec + nokta + ipucu kutusu ---
  function gizle() {
    var cizgi = document.getElementById("jsimlec");
    var nokta = document.getElementById("jsimlecnokta");
    if (cizgi) cizgi.setAttribute("visibility", "hidden");
    if (nokta) nokta.setAttribute("visibility", "hidden");
    if (ipucu) ipucu.style.opacity = "0";
  }

  function hareket(e) {
    var svg = svgEl(), rect, oran, kutuRect, yOran, vx, i, gi, onceki, deg, q, sol, ust;
    if (!svg) return;
    rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    oran = G / rect.width;
    kutuRect = kutu.getBoundingClientRect();
    yOran = rect.height / H;
    vx = (e.clientX - rect.left) * oran;
    i = Math.round((vx - KENAR.sol) / durum.adimX);
    i = Math.max(0, Math.min(durum.f.length - 1, i));
    gi = durum.kaydir + i;
    onceki = gi > 0 ? F[gi - 1] : null;
    deg = onceki ? (durum.f[i] / onceki - 1) * 100 : null;
    q = [KENAR.sol + i * durum.adimX, KENAR.ust + (1 - (durum.f[i] - durum.lo) / (durum.hi - durum.lo)) * PH];
    var cizgi = document.getElementById("jsimlec");
    var nokta = document.getElementById("jsimlecnokta");
    if (cizgi) {
      cizgi.setAttribute("x1", q[0].toFixed(1));
      cizgi.setAttribute("x2", q[0].toFixed(1));
      cizgi.setAttribute("visibility", "visible");
    }
    if (nokta) {
      nokta.setAttribute("cx", q[0].toFixed(1));
      nokta.setAttribute("cy", q[1].toFixed(1));
      nokta.setAttribute("visibility", "visible");
    }
    if (ipucu) {
      ipucu.innerHTML = "<b>" + sayi(durum.f[i], 2) + " TL</b><br>" + tarihUzun(durum.t[i]) +
        (deg === null ? "" : " · <span style='color:" + (deg >= 0 ? "#4ade80" : "#f87171") + "'>" +
          (deg >= 0 ? "+" : "") + sayi(deg, 2) + "% " + soz("gunluk") + "</span>");
      sol = q[0] / oran;
      ust = q[1] * yOran;
      ipucu.style.transform = ust < 70 ? "translate(-50%, 18px)" : "translate(-50%, -112%)";
      ipucu.style.left = Math.max(60, Math.min(kutuRect.width - 60, sol)).toFixed(0) + "px";
      ipucu.style.top = ust.toFixed(0) + "px";
      ipucu.style.opacity = "1";
    }
  }

  kutu.addEventListener("pointermove", hareket);
  kutu.addEventListener("pointerleave", gizle);

  var secili = "tumu";
  ARIALIK.forEach(function (cift) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = cift[0] === "tumu" ? soz("tumu") : cift[0];
    if (cift[0] === secili) btn.setAttribute("aria-pressed", "true");
    btn.addEventListener("click", function () {
      secili = cift[0];
      Array.prototype.forEach.call(aralikKutu.children, function (dugme) {
        dugme.setAttribute("aria-pressed", dugme === btn ? "true" : "false");
      });
      ciz(cift[1]);
      gizle();
    });
    aralikKutu.appendChild(btn);
  });

  ciz(0);
})();
