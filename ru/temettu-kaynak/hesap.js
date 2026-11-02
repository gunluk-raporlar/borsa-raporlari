/* ================= Temettü sayfası — hesaplayıcı + arayüz ================= */
(function () {
  'use strict';

  /* ---------- yardımcılar ---------- */
  var TR = function (n, d) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toLocaleString('tr-TR', {
      minimumFractionDigits: d === undefined ? 0 : d,
      maximumFractionDigits: d === undefined ? 0 : d
    });
  };
  var TRY = function (n) { return TR(n, 0) + ' TL'; };
  var YUZ = function (n) { return (n >= 0 ? '+' : '') + TR(n, 2) + '%'; };
  var AY = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
  var GUN = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'];

  function tarihYaz(iso) {
    var p = iso.split('-');
    return parseInt(p[2], 10) + ' ' + AY[parseInt(p[1], 10) - 1] + ' ' + p[0];
  }

  /* ---------- hesaplama motoru (temettucu.com modeli) ---------- */
  function isGunuArti(iso, n) {
    var d = new Date(iso + 'T00:00:00Z'), kalan = n;
    while (kalan > 0) {
      d.setUTCDate(d.getUTCDate() + 1);
      var w = d.getUTCDay();
      if (w !== 0 && w !== 6) kalan--;
    }
    return d.toISOString().slice(0, 10);
  }
  function kurBul(kur, tarih, tur) {
    if (!kur || !kur.length) return null;
    var lo = 0, hi = kur.length - 1, son = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (kur[mid][0] <= tarih) { son = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (son < 0) return null;
    return tur === 'usd' ? kur[son][1] : kur[son][2];
  }

  var STOPAJ_ESKI = 0.15, STOPAJ_YENI = 0.10, STOPAJ_GECIS = '2020-01-01';

  function hesapla(cfg) {
    var seri = cfg.seri, temettu = cfg.temettu || [], bedelsiz = cfg.bedelsiz || [], kur = cfg.kur || [];
    var mod = cfg.mod, tutar = cfg.tutar, alinanLot = cfg.alinanLot, oran = cfg.oran || 1;
    var temettuAl = cfg.temettuAl, enflasyonArtis = cfg.enflasyonArtis, yillikEnf = cfg.yillikEnf || 0;
    var asgariUcret = cfg.asgariUcret || null;
    var baslangic = cfg.baslangic, bitis = cfg.bitis;

    var gunler = seri.filter(function (r) { return r[0] >= baslangic && r[0] <= bitis; });
    if (!gunler.length) return null;

    var olaylar = [];
    for (var e = 0; e < temettu.length; e++) {
      var ex = temettu[e][0], dps = temettu[e][1];
      if (ex < baslangic || ex > bitis) continue;
      var odeme = isGunuArti(ex, 2);
      if (odeme > bitis) continue;
      var stopaj = odeme < STOPAJ_GECIS ? STOPAJ_ESKI : STOPAJ_YENI;
      olaylar.push({ ex: ex, odeme: odeme, dps: dps, net: dps * (1 - stopaj), stopaj: stopaj });
    }
    olaylar.sort(function (a, b) { return a.odeme < b.odeme ? -1 : a.odeme > b.odeme ? 1 : 0; });
    var olayPtr = 0;

    var bedMap = {};
    for (var b = 0; b < bedelsiz.length; b++) {
      if (bedelsiz[b][0] >= baslangic && bedelsiz[b][0] <= bitis) bedMap[bedelsiz[b][0]] = bedelsiz[b][1];
    }

    var lot = 0, nakit = 0, yatirilan = 0;
    var temettuBrut = 0, temettuNet = 0, temettuLot = 0, bedelsizLot = 0, sermayeLot = 0;
    var yatUsd = 0, yatAlt = 0, tBrutUsd = 0, tBrutAlt = 0;
    var hypBrut = 0, hypUsd = 0, hypAlt = 0;
    var oncekiAy = null, aySayaci = 0;
    var aylikSeri = [], olayKayit = [];
    var tekSeferlik = (mod === 2 || mod === 3 || mod === 4 || mod === 8);

    for (var i = 0; i < gunler.length; i++) {
      var gun = gunler[i][0], fiyat = gunler[i][1], ay = gun.slice(0, 7);

      if (bedMap[gun]) {
        var r = bedMap[gun];
        var ek = Math.floor(lot * (r - 1));
        lot += ek; bedelsizLot += ek;
        olayKayit.push({ tarih: gun, tip: 'Sermaye Artırımı', ek: ek, toplamLot: lot });
      }

      while (olayPtr < olaylar.length && olaylar[olayPtr].odeme <= gun) {
        var o = olaylar[olayPtr++];
        var brut = lot * o.dps;
        var net = Math.floor(lot * o.net);
        if (brut <= 0) continue;
        var ku = kurBul(kur, gun, 'usd'), ka = kurBul(kur, gun, 'altin');
        hypBrut += brut;
        if (ku) hypUsd += brut / ku;
        if (ka) hypAlt += brut / ka;
        if (temettuAl) {
          temettuBrut += brut; temettuNet += net; nakit += net;
          if (ku) tBrutUsd += brut / ku;
          if (ka) tBrutAlt += brut / ka;
        }
        var alinabilir = (temettuAl && fiyat > 0) ? Math.floor(nakit / fiyat) : 0;
        olayKayit.push({
          tarih: gun, tip: 'Temettü', ex: o.ex, hisseBasi: o.dps, stopaj: o.stopaj, lot: lot,
          brut: brut, net: net, alinan: alinabilir, fiyat: fiyat,
          toplamLot: lot + alinabilir, usd: ku, altin: ka
        });
        if (alinabilir > 0) {
          lot += alinabilir; temettuLot += alinabilir; nakit -= alinabilir * fiyat;
        }
      }

      var ayBasi = ay !== oncekiAy;
      if (ayBasi) { oncekiAy = ay; aySayaci++; }

      var alimTl = 0, katsayi = 1;
      if (enflasyonArtis && !tekSeferlik) katsayi = Math.pow(1 + yillikEnf, Math.floor((aySayaci - 1) / 12));

      if (mod === 0) alimTl = alinanLot * fiyat;
      else if (mod === 1) { if (ayBasi) alimTl = alinanLot * fiyat; }
      else if (mod === 2) { if (i === 0) alimTl = alinanLot * fiyat; }
      else if (mod === 3) { if (i === 0) alimTl = tutar; }
      else if (mod === 4) { if (i === 0) { var k4 = kurBul(kur, gun, 'usd'); if (k4) alimTl = tutar * k4; } }
      else if (mod === 8) { if (i === 0) { var k8 = kurBul(kur, gun, 'altin'); if (k8) alimTl = tutar * k8; } }
      else if (mod === 5) { if (ayBasi) alimTl = tutar * katsayi; }
      else if (mod === 6) { if (ayBasi) { var k6 = kurBul(kur, gun, 'usd'); if (k6) alimTl = tutar * k6 * katsayi; } }
      else if (mod === 9) { if (ayBasi) { var k9 = kurBul(kur, gun, 'altin'); if (k9) alimTl = tutar * k9 * katsayi; } }
      else if (mod === 7) {
        if (ayBasi && asgariUcret) {
          var taban = asgariUcret[gun.slice(0, 4)];
          if (taban) alimTl = taban * oran * katsayi;
        }
      }

      if (alimTl > 0 && fiyat > 0) {
        var butce = alimTl + nakit;
        var adet = Math.floor(butce / fiyat);
        if (adet > 0) {
          lot += adet;
          var harcanan = adet * fiyat;
          nakit = butce - harcanan;
          yatirilan += harcanan; sermayeLot += adet;
          var ku2 = kurBul(kur, gun, 'usd'), ka2 = kurBul(kur, gun, 'altin');
          if (ku2) yatUsd += harcanan / ku2;
          if (ka2) yatAlt += harcanan / ka2;
        } else { nakit = butce; }
      }

      if (ayBasi || i === gunler.length - 1) {
        aylikSeri.push({
          tarih: gun, lot: lot, fiyat: fiyat, deger: lot * fiyat, yatirilan: yatirilan,
          cumBrut: temettuBrut, cumUsd: tBrutUsd, cumAlt: tBrutAlt,
          cumHBrut: hypBrut, cumHUsd: hypUsd, cumHAlt: hypAlt,
          usd: kurBul(kur, gun, 'usd'), altin: kurBul(kur, gun, 'altin')
        });
      }
    }

    var son = gunler[gunler.length - 1];
    var sonFiyat = son[1], guncelDeger = lot * sonFiyat;
    var sonUsd = kurBul(kur, son[0], 'usd'), sonAltin = kurBul(kur, son[0], 'altin');
    var yil = Math.max(1 / 12, (new Date(bitis) - new Date(baslangic)) / (365.25 * 864e5));
    var enfMaliyet = yatirilan * Math.pow(1 + yillikEnf, yil);

    return {
      lotSayisi: lot, yatirilan: yatirilan, guncelDeger: guncelDeger,
      karZarar: guncelDeger - yatirilan,
      getiriYuzde: yatirilan > 0 ? (guncelDeger / yatirilan - 1) * 100 : 0,
      ortalamaMaliyet: lot > 0 ? yatirilan / lot : 0,
      sonFiyat: sonFiyat, sonTarih: son[0],
      temettuBrut: temettuBrut, temettuNet: temettuNet, temettuLot: temettuLot,
      bedelsizLot: bedelsizLot, sermayeLot: sermayeLot, nakit: nakit,
      enflasyonMaliyet: enfMaliyet,
      enflasyonReelGetiri: yatirilan > 0 ? (guncelDeger / enfMaliyet - 1) * 100 : 0,
      usd: { yatirilan: sonUsd ? yatirilan / sonUsd : null, guncel: sonUsd ? guncelDeger / sonUsd : null, kur: sonUsd },
      altin: { yatirilan: sonAltin ? yatirilan / sonAltin : null, guncel: sonAltin ? guncelDeger / sonAltin : null, kur: sonAltin },
      yatUsd: yatUsd, yatAlt: yatAlt, tBrutUsd: tBrutUsd, tBrutAlt: tBrutAlt,
      olayKayit: olayKayit, aylikSeri: aylikSeri, gunSayisi: gunler.length
    };
  }

  /* ---------- SVG grafik ---------- */
  function cizGrafik(kap, seriler, secenek) {
    secenek = secenek || {};
    var eksen2 = seriler.some(function (s) { return s.eksen === 2; });
    var G = 680, Y = 300, sol = 62, sag = eksen2 ? 48 : 14, ust = 14, alt = 30;
    var icG = G - sol - sag, icY = Y - ust - alt;
    function topla(ikinci) {
      var t = [];
      seriler.forEach(function (s) {
        if ((s.eksen === 2) === ikinci) s.noktalar.forEach(function (p) { if (isFinite(p[1])) t.push(p[1]); });
      });
      return t;
    }
    var tum = topla(false), tum2 = eksen2 ? topla(true) : [];
    if (!tum.length && !tum2.length) { kap.innerHTML = '<p class="tm-bos">Grafik için veri yok.</p>'; return; }
    function olcek(liste) {
      var min = Math.min.apply(null, liste), max = Math.max.apply(null, liste);
      if (min === max) { min = min * 0.95; max = max * 1.05; }
      var pay = (max - min) * 0.08; min -= pay; max += pay;
      if (secenek.sifirdan) min = 0;
      return { min: min, max: max };
    }
    var m1 = olcek(tum.length ? tum : tum2), min = m1.min, max = m1.max;
    var m2 = eksen2 && tum2.length ? olcek(tum2) : null;

    var xMin = seriler[0].noktalar[0][0], xMax = seriler[0].noktalar[seriler[0].noktalar.length - 1][0];
    var tMin = new Date(xMin).getTime(), tMax = new Date(xMax).getTime();
    if (tMax === tMin) tMax = tMin + 864e5;

    function X(t) { return sol + (new Date(t).getTime() - tMin) / (tMax - tMin) * icG; }
    function Yy(v, eksen) {
      if (eksen === 2 && m2) return ust + icY - (v - m2.min) / (m2.max - m2.min) * icY;
      return ust + icY - (v - min) / (max - min) * icY;
    }

    var svg = '<svg viewBox="0 0 ' + G + ' ' + Y + '" role="img" aria-label="' + (secenek.baslik || 'grafik') + '">';
    // ızgara + y ekseni
    for (var g = 0; g <= 4; g++) {
      var deger = min + (max - min) * g / 4, yy = Yy(deger);
      svg += '<line x1="' + sol + '" y1="' + yy.toFixed(1) + '" x2="' + (G - sag) + '" y2="' + yy.toFixed(1) +
        '" stroke="var(--line)" stroke-width="1" stroke-dasharray="' + (g === 0 ? '0' : '3 3') + '"/>';
      svg += '<text x="' + (sol - 8) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end" font-size="10.5" fill="var(--muted)">' +
        (secenek.yFormat ? secenek.yFormat(deger) : TR(deger)) + '</text>';
    }
    if (m2) {
      for (var d2 = 0; d2 <= 4; d2++) {
        var deger2 = m2.min + (m2.max - m2.min) * d2 / 4;
        svg += '<text x="' + (G - sag + 6) + '" y="' + (Yy(deger2, 2) + 4).toFixed(1) +
          '" font-size="10.5" fill="#b45309">' + (secenek.y2Format ? secenek.y2Format(deger2) : TR(deger2, 1)) + '</text>';
      }
      svg += '<text x="' + (G - 4) + '" y="' + (ust - 2) + '" text-anchor="end" font-size="9.5" fill="#b45309">Gram Altın</text>';
    }
    // x ekseni etiketleri
    var adet = 5;
    for (var i = 0; i <= adet; i++) {
      var t = tMin + (tMax - tMin) * i / adet;
      var d = new Date(t), et = d.getFullYear() + (i === adet || i === 0 ? '' : '');
      var xx = sol + icG * i / adet;
      svg += '<text x="' + xx.toFixed(1) + '" y="' + (Y - 8) + '" text-anchor="middle" font-size="10.5" fill="var(--muted)">' +
        (secenek.xFormat ? secenek.xFormat(d) : et) + '</text>';
    }
    // seriler
    seriler.forEach(function (s) {
      var d2 = '', ilk = true;
      s.noktalar.forEach(function (p) {
        if (!isFinite(p[1])) return;
        d2 += (ilk ? 'M' : 'L') + X(p[0]).toFixed(1) + ' ' + Yy(p[1], s.eksen).toFixed(1);
        ilk = false;
      });
      if (s.dolu) {
        var alan = d2 + 'L' + X(s.noktalar[s.noktalar.length - 1][0]).toFixed(1) + ' ' + (ust + icY) +
          'L' + X(s.noktalar[0][0]).toFixed(1) + ' ' + (ust + icY) + 'Z';
        svg += '<path d="' + alan + '" fill="' + s.renk + '" opacity="0.10"/>';
      }
      svg += '<path d="' + d2 + '" fill="none" stroke="' + s.renk + '" stroke-width="' + (s.kalin || 2) +
        '" stroke-linejoin="round"' + (s.kesik ? ' stroke-dasharray="5 4"' : '') + '/>';
    });
    svg += '<line class="imlec" x1="0" y1="' + ust + '" x2="0" y2="' + (ust + icY) + '" stroke="var(--muted)" stroke-width="1" opacity="0"/>';
    svg += '<rect x="' + sol + '" y="' + ust + '" width="' + icG + '" height="' + icY + '" fill="transparent" class="yakala"/>';
    svg += '</svg>';
    kap.innerHTML = svg + '<div class="graf-ipucu"></div>';

    // hover
    var svgEl = kap.querySelector('svg'), imlec = kap.querySelector('.imlec'), ipucu = kap.querySelector('.graf-ipucu');
    var yakala = kap.querySelector('.yakala');
    function hareket(ev) {
      var kutu = svgEl.getBoundingClientRect();
      var oran = (ev.clientX - kutu.left) / kutu.width * G;
      if (oran < sol || oran > G - sag) { imlec.setAttribute('opacity', '0'); ipucu.style.opacity = 0; return; }
      var t = tMin + (oran - sol) / icG * (tMax - tMin);
      var en = seriler[0].noktalar, secili = en[0];
      for (var j = 0; j < en.length; j++) {
        if (new Date(en[j][0]).getTime() <= t) secili = en[j]; else break;
      }
      imlec.setAttribute('x1', X(secili[0]).toFixed(1));
      imlec.setAttribute('x2', X(secili[0]).toFixed(1));
      imlec.setAttribute('opacity', '0.5');
      var html = '<b>' + tarihYaz(secili[0]) + '</b>';
      seriler.forEach(function (s) {
        var bul = null;
        for (var m = 0; m < s.noktalar.length; m++) if (s.noktalar[m][0] === secili[0]) { bul = s.noktalar[m]; break; }
        if (bul) html += '<br>' + s.ad + ': ' + (s.birim !== undefined
          ? TR(bul[1], s.ondalik === undefined ? 0 : s.ondalik) + s.birim
          : (secenek.ipucuFormat ? secenek.ipucuFormat(bul[1]) : TR(bul[1])));
      });
      ipucu.innerHTML = html;
      ipucu.style.left = (X(secili[0]) / G * 100) + '%';
      ipucu.style.top = (Yy(secili[1]) / Y * 100) + '%';
      ipucu.style.opacity = 1;
    }
    yakala.addEventListener('mousemove', hareket);
    yakala.addEventListener('touchmove', function (ev) { if (ev.touches[0]) hareket(ev.touches[0]); }, { passive: true });
    yakala.addEventListener('mouseleave', function () { imlec.setAttribute('opacity', '0'); ipucu.style.opacity = 0; });
  }

  /* ---------- veri yükleme ---------- */
  var onbellek = {};
  function seriYukle(kod) {
    if (onbellek[kod]) return Promise.resolve(onbellek[kod]);
    return fetch('temettu-veri/' + kod + '.json', { cache: 'force-cache' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) { onbellek[kod] = d; return d; });
  }

  var OZET = null, KUR = null, secilenKod = null, sonuc = null, para = 'tl', sekmem = 0;

  function paraDeger(tl, p) {
    if (p === 'tl' || !sonuc) return tl;
    var k = p === 'usd' ? sonuc.usd.kur : sonuc.altin.kur;
    return k ? tl / k : null;
  }
  function paraEtiket(p) { return p === 'usd' ? '$' : p === 'altin' ? ' gr' : ' TL'; }
  function paraYaz(tl, p, ondalik) {
    var v = paraDeger(tl, p);
    if (v === null) return '—';
    if (p === 'tl') return TR(v, ondalik === undefined ? 0 : ondalik) + ' TL';
    return TR(v, ondalik === undefined ? 2 : ondalik) + paraEtiket(p);
  }

  /* ---------- sonuç çizimi ---------- */
  function sonucCiz() {
    if (!sonuc) return;
    var kap = document.getElementById('h-sonuc');
    var p = para;
    var degisimSinif = sonuc.karZarar >= 0 ? 'pos' : 'neg';

    var eUsd = sonuc.usd.kur, eAlt = sonuc.altin.kur;
    function birimYaz(v, c, o) {
      if (v === null || v === undefined || isNaN(v)) return '—';
      return TR(v, o === undefined ? 0 : o) + (c === 'usd' ? ' $' : c === 'altin' ? ' gr' : ' TL');
    }
    function digerIkili(tl, sec) {
      if (!eUsd || !eAlt) return TR(sonuc.sonFiyat, 2) + ' TL kapanış';
      var l = [];
      if (sec !== 'tl') l.push(birimYaz(tl, 'tl', 0));
      if (sec !== 'usd') l.push(birimYaz(tl / eUsd, 'usd', 0));
      if (sec !== 'altin') l.push(birimYaz(tl / eAlt, 'altin', 1));
      return l.join(' · ');
    }
    var html = '<div class="sonuc-ust">' +
      '<div class="sonuc-kart"><div class="l">Güncel Değer</div><div class="v">' + paraYaz(sonuc.guncelDeger, p) + '</div>' +
      '<div class="a">' + digerIkili(sonuc.guncelDeger, p) + '</div></div>' +
      '<div class="sonuc-kart"><div class="l">Yatırılan Toplam</div><div class="v">' + paraYaz(sonuc.yatirilan, p) + '</div>' +
      '<div class="a">' + digerIkili(sonuc.yatirilan, p) + '</div></div>' +
      '<div class="sonuc-kart"><div class="l">Kâr / Zarar</div><div class="v ' + degisimSinif + '">' +
      (sonuc.karZarar >= 0 ? '+' : '') + paraYaz(sonuc.karZarar, p) + '</div>' +
      '<div class="a ' + degisimSinif + '">' + YUZ(sonuc.getiriYuzde) + ' · enflasyona göre ' + YUZ(sonuc.enflasyonReelGetiri) + '</div></div>' +
      '<div class="sonuc-kart"><div class="l">Toplam Lot</div><div class="v">' + TR(sonuc.lotSayisi) + '</div>' +
      '<div class="a">' + TR(sonuc.gunSayisi) + ' işlem günü · temettüden ' + TR(sonuc.temettuLot) + '</div></div>' +
      '</div>';

    html += '<div class="tm-seg" id="h-sonuc-para" style="margin:0 0 14px">' +
      ['tl', 'usd', 'altin'].map(function (x) {
        var ad = x === 'tl' ? 'TL' : x === 'usd' ? 'Dolar' : 'Gram Altın';
        return '<button type="button" data-p="' + x + '" aria-pressed="' + (p === x) + '">' + ad + '</button>';
      }).join('') + '</div>';

    function ucSatir(k, t, u, a) {
      return '<div class="k">' + k + '</div><div class="v">' + t + '</div><div class="v">' + u + '</div><div class="v">' + a + '</div>';
    }
    html += '<div class="detay-liste detay-uc">' +
      ucSatir('', '<b>TL</b>', '<b>Dolar ($)</b>', '<b>Gram Altın</b>') +
      ucSatir('Yatırılan toplam tutar', TR(sonuc.yatirilan, 0),
        eUsd ? TR(sonuc.yatUsd, 0) : '—', eAlt ? TR(sonuc.yatAlt, 0) + ' gr' : '—') +
      ucSatir('Yatırılan tutarın enflasyonla bugünkü değeri', TR(sonuc.enflasyonMaliyet, 0),
        eUsd ? TR(sonuc.enflasyonMaliyet / eUsd, 0) : '—', '—') +
      ucSatir('Lot başına ortalama maliyet',
        sonuc.lotSayisi ? TR(sonuc.yatirilan / sonuc.lotSayisi, 2) : '—',
        eUsd && sonuc.lotSayisi ? TR(sonuc.yatUsd / sonuc.lotSayisi, 2) : '—',
        eAlt && sonuc.lotSayisi ? TR(sonuc.yatAlt / sonuc.lotSayisi, 4) + ' gr' : '—') +
      ucSatir('Bitiş tarihindeki güncel değer', TR(sonuc.guncelDeger, 0),
        eUsd ? TR(sonuc.usd.guncel, 0) : '—', eAlt ? TR(sonuc.altin.guncel, 1) + ' gr' : '—') +
      ucSatir('Bitişe kadarki toplam temettü (brüt)',
        TR(sonuc.temettuBrut, 0),
        sonuc.temettuBrut ? (eUsd ? TR(sonuc.tBrutUsd, 0) : '—') : TR(0, 0),
        sonuc.temettuBrut ? (eAlt ? TR(sonuc.tBrutAlt, 1) + ' gr' : '—') : TR(0, 0) + ' gr') +
      ucSatir('Bitiş tarihindeki hisse fiyatı', TR(sonuc.sonFiyat, 2),
        eUsd ? TR(sonuc.sonFiyat / eUsd, 2) : '—', eAlt ? TR(sonuc.sonFiyat / eAlt, 4) + ' gr' : '—') +
      '<div class="ayrac" style="grid-column:1/-1"></div>' +
      ucSatir('Toplam temettü (net, stopaj sonrası)', paraYaz(sonuc.temettuNet, 'tl'), '—', '—') +
      ucSatir('Enflasyona göre reel getiri',
        '<span class="' + (sonuc.enflasyonReelGetiri >= 0 ? 'pos' : 'neg') + '">' + YUZ(sonuc.enflasyonReelGetiri) + '</span>', '—', '—') +
      '</div>';

    var kaynaklar = [
      { ad: 'Satın Alınan', v: sonuc.sermayeLot, renk: '#0ea5e9' },
      { ad: 'Bedelsizden', v: sonuc.bedelsizLot, renk: '#8b5cf6' },
      { ad: 'Temettüden', v: sonuc.temettuLot, renk: '#f59e0b' }
    ].filter(function (x) { return x.v > 0; });
    if (kaynaklar.length && sonuc.lotSayisi > 0) {
      var aci = -Math.PI / 2, donut = '';
      if (kaynaklar.length === 1) {
        donut = '<circle cx="60" cy="60" r="50" fill="' + kaynaklar[0].renk + '"/>';
      } else {
        kaynaklar.forEach(function (x) {
          var dilim = x.v / sonuc.lotSayisi * Math.PI * 2, R = 50, M = 60;
          var x1 = (M + R * Math.cos(aci)).toFixed(2), y1 = (M + R * Math.sin(aci)).toFixed(2);
          aci += dilim;
          var x2 = (M + R * Math.cos(aci)).toFixed(2), y2 = (M + R * Math.sin(aci)).toFixed(2);
          donut += '<path d="M ' + M + ' ' + M + ' L ' + x1 + ' ' + y1 + ' A ' + R + ' ' + R +
            ' 0 ' + (dilim > Math.PI ? 1 : 0) + ' 1 ' + x2 + ' ' + y2 + ' Z" fill="' + x.renk +
            '" stroke="var(--card)" stroke-width="1"/>';
        });
      }
      html += '<div class="donut-kap"><svg viewBox="0 0 120 120" width="120" height="120" role="img" aria-label="Lot kaynakları">' + donut +
        '<circle cx="60" cy="60" r="28" fill="var(--card)"/>' +
        '<text x="60" y="58" text-anchor="middle" font-size="13" font-weight="700" fill="var(--ink)">' + TR(sonuc.lotSayisi) + '</text>' +
        '<text x="60" y="72" text-anchor="middle" font-size="8" fill="var(--muted)">toplam lot</text></svg>' +
        '<div class="donut-lejant">' + kaynaklar.map(function (x) {
          return '<span><i style="background:' + x.renk + '"></i>' + x.ad + ': <b>' + TR(x.v) + '</b> (%' +
            TR(x.v / sonuc.lotSayisi * 100, 1) + ')</span>';
        }).join('') + '</div></div>';
    }

    html += '<div class="graf-sekmeler" id="h-graf-sekme">' +
      [['Temettü Getirisi', 0], ['Lot Sayısı', 1], ['Portföy Değeri', 2], ['Hisse Fiyatı', 3], ['Fiyat Ortalamaları', 4], ['Aylık Değişim', 5]].map(function (s) {
        return '<button type="button" data-s="' + s[1] + '" aria-pressed="' + (sekmem === s[1]) + '">' + s[0] + '</button>';
      }).join('') + '</div>' +
      '<div class="graf-kutu" id="h-graf"></div>' +
      '<div class="graf-lejant" id="h-lejant"></div>';

    if (sonuc.olayKayit.length) {
      html += '<h3 style="font-size:14px;margin:20px 0 8px">Temettü ve sermaye artırımı geçmişi</h3>' +
        '<div class="tbl-wrap"><table class="tm-tablo tm-tablo-min">' +
        '<thead><tr><th>Tarih</th><th>Tip</th><th class="sayi">Hisse başı</th><th class="sayi">TL</th>' +
        '<th class="sayi">Dolar ($)</th><th class="sayi">Altın (gr)</th>' +
        '<th class="sayi">Kazanılan lot</th><th class="sayi">Toplam lot</th></tr></thead><tbody>' +
        sonuc.olayKayit.map(function (t) {
          if (t.tip === 'Sermaye Artırımı') {
            return '<tr><td>' + tarihYaz(t.tarih) + '</td><td>Sermaye Artırımı</td>' +
              '<td class="sayi">—</td><td class="sayi">—</td><td class="sayi">—</td><td class="sayi">—</td>' +
              '<td class="sayi">' + TR(t.ek) + '</td><td class="sayi">' + TR(t.toplamLot) + '</td></tr>';
          }
          return '<tr><td>' + tarihYaz(t.tarih) + '</td><td>Temettü</td>' +
            '<td class="sayi">' + TR(t.hisseBasi, 4) + '</td>' +
            '<td class="sayi">' + TR(t.brut, 0) + '</td>' +
            '<td class="sayi">' + (t.usd ? TR(t.brut / t.usd, 0) : '—') + '</td>' +
            '<td class="sayi">' + (t.altin ? TR(t.brut / t.altin, 1) : '—') + '</td>' +
            '<td class="sayi">' + (t.alinan ? TR(t.alinan) : '—') + '</td>' +
            '<td class="sayi">' + TR(t.toplamLot) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }

    html += '<p class="tm-not">Fiyatlar düzeltilmemiş günlük kapanıştır; komisyon ve BSMV hesaba katılmaz. ' +
      'Temettü, ex-tarihten 2 iş günü sonra ödenir ve stopaj (%15 → %10, 2020) düşülerek lota çevrilir.</p>';

    kap.innerHTML = html;
    baglantilar();
    grafikCiz();
  }

  function grafikCiz() {
    if (!sonuc) return;
    var kap = document.getElementById('h-graf'), lejant = document.getElementById('h-lejant');
    var seri = sonuc.aylikSeri;
    if (!seri.length) return;

    if (sekmem === 0) {
      cizGrafik(kap, [
        { ad: 'Temettü Getirisi TL', renk: '#1d4ed8', noktalar: seri.map(function (r) { return [r.tarih, r.cumHBrut]; }), kalin: 2, birim: ' TL' },
        { ad: 'Temettü Getirisi Dolar', renk: '#15803d', noktalar: seri.map(function (r) { return [r.tarih, r.cumHUsd]; }), kalin: 1.8, birim: ' $' },
        { ad: 'Temettü Getirisi Gram Altın', renk: '#d97706', noktalar: seri.map(function (r) { return [r.tarih, r.cumHAlt]; }), kalin: 1.8, eksen: 2, birim: ' gr', ondalik: 2 }
      ], { baslik: 'Temettü getirisi (kümülatif, brüt — temettü ile lot alımı kapalıyken bile olası ödemeler gösterilir)' });
      lejant.innerHTML = '<span><i style="border-color:#1d4ed8"></i>Temettü Getirisi TL</span>' +
        '<span><i style="border-color:#15803d"></i>Temettü Getirisi Dolar</span>' +
        '<span><i style="border-color:#d97706"></i>Temettü Getirisi Gram Altın (sağ eksen)</span>';
    } else if (sekmem === 1) {
      cizGrafik(kap, [
        { ad: 'Biriktirilen Lot Sayısı', renk: '#dc2626', noktalar: seri.map(function (r) { return [r.tarih, r.lot]; }), dolu: true, kalin: 2, birim: ' lot' }
      ], { baslik: 'Lot sayısı', sifirdan: true });
      lejant.innerHTML = '<span><i style="border-color:#dc2626"></i>Biriktirilen Lot Sayısı</span>';
    } else if (sekmem === 2) {
      cizGrafik(kap, [
        { ad: 'Portföy Değeri TL', renk: '#1d4ed8', noktalar: seri.map(function (r) { return [r.tarih, r.deger]; }), dolu: true, kalin: 2, birim: ' TL' },
        { ad: 'Portföy Değeri Dolar', renk: '#15803d', noktalar: seri.map(function (r) { return [r.tarih, r.usd ? r.deger / r.usd : null]; }), kalin: 1.8, birim: ' $', ondalik: 0 },
        { ad: 'Portföy Değeri Gram Altın', renk: '#d97706', noktalar: seri.map(function (r) { return [r.tarih, r.altin ? r.deger / r.altin : null]; }), kalin: 1.8, eksen: 2, birim: ' gr', ondalik: 1 },
        { ad: 'Yatırılan Toplam', renk: '#94a3b8', noktalar: seri.map(function (r) { return [r.tarih, r.yatirilan]; }), kesik: true, kalin: 1.5, birim: ' TL' }
      ], { baslik: 'Portföy değeri' });
      lejant.innerHTML = '<span><i style="border-color:#1d4ed8"></i>Portföy Değeri TL</span>' +
        '<span><i style="border-color:#15803d"></i>Dolar</span>' +
        '<span><i style="border-color:#d97706"></i>Gram Altın (sağ eksen)</span>' +
        '<span><i style="border-color:#94a3b8"></i>Yatırılan Toplam</span>';
    } else if (sekmem === 3) {
      cizGrafik(kap, [
        { ad: 'Hisse Fiyatı TL', renk: '#1d4ed8', noktalar: seri.map(function (r) { return [r.tarih, r.fiyat]; }), dolu: true, kalin: 2, birim: ' TL', ondalik: 2 },
        { ad: 'Hisse Fiyatı Dolar', renk: '#15803d', noktalar: seri.map(function (r) { return [r.tarih, r.usd ? r.fiyat / r.usd : null]; }), kalin: 1.8, birim: ' $', ondalik: 2 },
        { ad: 'Hisse Fiyatı Gram Altın', renk: '#d97706', noktalar: seri.map(function (r) { return [r.tarih, r.altin ? r.fiyat / r.altin : null]; }), kalin: 1.8, eksen: 2, birim: ' gr', ondalik: 3 }
      ], { baslik: 'Hisse fiyatı (düzeltilmemiş kapanış)' });
      lejant.innerHTML = '<span><i style="border-color:#1d4ed8"></i>Hisse Fiyatı TL</span>' +
        '<span><i style="border-color:#15803d"></i>Dolar</span>' +
        '<span><i style="border-color:#d97706"></i>Gram Altın (sağ eksen)</span>';
    } else if (sekmem === 4) {
      function ort(seri, n) {
        var out = [], top = 0;
        for (var i = 0; i < seri.length; i++) {
          top += seri[i].fiyat;
          if (i >= n) top -= seri[i - n].fiyat;
          out.push([seri[i].tarih, i >= n - 1 ? top / n : null]);
        }
        return out;
      }
      cizGrafik(kap, [
        { ad: 'Fiyat', renk: 'var(--accent)', noktalar: seri.map(function (r) { return [r.tarih, r.fiyat]; }), kalin: 1.6 },
        { ad: '50 ay', renk: '#f59e0b', noktalar: ort(seri, 50), kalin: 1.8 },
        { ad: '200 ay', renk: '#8b5cf6', noktalar: ort(seri, 200), kalin: 1.8 }
      ], { baslik: 'Fiyat ortalamaları', yFormat: function (v) { return TR(v, 0); }, ipucuFormat: function (v) { return TR(v, 2) + ' TL'; } });
      lejant.innerHTML = '<span><i style="border-color:var(--accent)"></i>Fiyat</span>' +
        '<span><i style="border-color:#f59e0b"></i>50 ay ort.</span><span><i style="border-color:#8b5cf6"></i>200 ay ort.</span>';
    } else {
      var aylik = [];
      for (var i = 1; i < seri.length; i++) {
        var onceki = seri[i - 1].fiyat, simdi = seri[i].fiyat;
        aylik.push([seri[i].tarih, (simdi / onceki - 1) * 100]);
      }
      cizGrafik(kap, [{ ad: 'Aylık değişim', renk: '#0ea5e9', noktalar: aylik, kalin: 1.5 }],
        { baslik: 'Aylık değişim', yFormat: function (v) { return TR(v, 0) + '%'; }, ipucuFormat: function (v) { return YUZ(v); } });
      lejant.innerHTML = '<span><i style="border-color:#0ea5e9"></i>Aylık fiyat değişimi (%)</span>';
    }
  }

  /* ---------- form ---------- */
  function secilenFiyatGuncelle() {
    var kod = document.getElementById('h-hisse').value;
    var basIn = document.getElementById('h-bas');
    var bas = basIn.dataset.iso || basIn.value;
    var el = document.getElementById('h-secilen-fiyat');
    if (!kod || !bas) return;
    seriYukle(kod).then(function (d) {
      var f = null;
      for (var i = 0; i < d.fiyat.length; i++) { if (d.fiyat[i][0] <= bas) f = d.fiyat[i][1]; else break; }
      el.innerHTML = 'Seçilen tarihteki hisse fiyatı: <b>' + (f ? TR(f, 2) + ' TL' : '—') + '</b>';
    }).catch(function () { el.textContent = 'Seçilen tarihteki hisse fiyatı: —'; });
  }

  function modGuncelle() {
    var mod = parseInt(document.getElementById('h-mod').value, 10);
    var lotModu = (mod === 0 || mod === 1 || mod === 2);
    document.getElementById('h-lot-alan').hidden = !lotModu;
    document.getElementById('h-tutar-alan').hidden = lotModu || mod === 7;
    document.getElementById('h-oran-alan').hidden = mod !== 7;
    var et = document.getElementById('h-tutar-etiket');
    if (mod === 6 || mod === 4) et.textContent = 'Alım Tutarı (Dolar)';
    else if (mod === 9 || mod === 8) et.textContent = 'Altın Miktarı (gram)';
    else et.textContent = 'Alım Tutarı (TL)';
  }

  function baglantilar() {
    var seg = document.getElementById('h-sonuc-para');
    if (seg) seg.addEventListener('click', function (ev) {
      var b = ev.target.closest('button'); if (!b) return;
      para = b.dataset.p; sonucCiz();
    });
    var sek = document.getElementById('h-graf-sekme');
    if (sek) sek.addEventListener('click', function (ev) {
      var b = ev.target.closest('button'); if (!b) return;
      sekmem = parseInt(b.dataset.s, 10);
      Array.prototype.forEach.call(sek.children, function (c) { c.setAttribute('aria-pressed', c === b); });
      grafikCiz();
    });
  }

  function hesaplaTikla() {
    var kod = document.getElementById('h-hisse').value;
    var bIn = document.getElementById('h-bas'), sIn = document.getElementById('h-bit');
    var bas = bIn.dataset.iso || bIn.value, bit = sIn.dataset.iso || sIn.value;
    var durum = document.getElementById('h-durum');
    if (!kod || !bas || !bit) { durum.textContent = 'Hisse ve tarih aralığı seçin.'; return; }
    if (bas >= bit) { durum.textContent = 'Başlangıç tarihi bitişten önce olmalı.'; return; }
    durum.textContent = 'Hesaplanıyor…';
    document.getElementById('h-hesapla').disabled = true;

    seriYukle(kod).then(function (d) {
      var mod = parseInt(document.getElementById('h-mod').value, 10);
      sonuc = hesapla({
        seri: d.fiyat, temettu: d.temettu, bedelsiz: d.bedelsiz, kur: (KUR && KUR.seri) || [],
        baslangic: bas, bitis: bit, mod: mod,
        tutar: parseFloat(document.getElementById('h-tutar').value) || 0,
        alinanLot: parseFloat(document.getElementById('h-lot').value) || 1,
        oran: parseFloat(document.getElementById('h-oran').value) || 1,
        temettuAl: document.getElementById('h-temettu').checked,
        enflasyonArtis: document.getElementById('h-enf').checked,
        yillikEnf: (parseFloat(document.getElementById('h-enf-oran').value) || 0) / 100,
        asgariUcret: (OZET && OZET.asgariUcret) || null
      });
      durum.textContent = sonuc ? '' : 'Bu aralıkta veri yok.';
      if (sonuc) { para = 'tl'; sekmem = 0; sonucCiz(); }
      document.getElementById('h-hesapla').disabled = false;
    }).catch(function (e) {
      durum.textContent = 'Veri yüklenemedi (' + e.message + ').';
      document.getElementById('h-hesapla').disabled = false;
    });
  }

  /* ---------- takvim + verim tablosu ---------- */
  function takvimCiz(yil) {
    var kap = document.getElementById('takvim-liste');
    var olaylar = OZET.takvim.filter(function (o) { return yil === 'tumu' || o.tarih.slice(0, 4) === yil; });
    if (!olaylar.length) { kap.innerHTML = '<p class="tm-bos">Bu yıl için kayıt yok.</p>'; return; }
    var gruplar = {};
    olaylar.forEach(function (o) {
      var ay = o.tarih.slice(0, 7);
      (gruplar[ay] = gruplar[ay] || []).push(o);
    });
    var html = '';
    Object.keys(gruplar).sort().forEach(function (ay) {
      var p = ay.split('-');
      html += '<div class="tm-ay"><h3>' + AY[parseInt(p[1], 10) - 1] + ' ' + p[0] + '</h3>';
      gruplar[ay].forEach(function (o) {
        var d = new Date(o.tarih + 'T00:00:00');
        html += '<div class="tm-olay' + (o.tahmin ? ' tm-tahmin' : '') + '">' +
          '<span class="t">' + parseInt(o.tarih.slice(8, 10), 10) + ' ' + GUN[d.getDay()].slice(0, 3) + '</span>' +
          '<span class="k">' + o.kod + (o.tahmin ? '<span class="tm-etiket">tahmini</span>' : '') + '</span>' +
          '<span class="v">' + TR(o.tutar, 4) + ' TL</span>' +
          '<span class="f">' + (o.fiyat ? TR(o.fiyat, 2) + ' TL' : '—') + '</span>' +
          '<span class="verim">' + (o.verim ? '%' + TR(o.verim, 2) : '') + '</span>' +
          '</div>';
      });
      html += '</div>';
    });
    kap.innerHTML = html;
  }

  function verimSirala(alan, yon) {
    var tb = document.querySelector('#verim-tablo tbody');
    var satirlar = Array.prototype.slice.call(tb.children);
    satirlar.sort(function (a, b) {
      var x = a.dataset[alan] || '', y = b.dataset[alan] || '';
      var nx = parseFloat(x), ny = parseFloat(y);
      var kars = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y), 'tr');
      return yon === 'azalan' ? -kars : kars;
    });
    satirlar.forEach(function (s) { tb.appendChild(s); });
  }

  /* ---------- tarih seçici (yıl -> ay paneli) ---------- */
  var AY_KISA = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
  function trTarih(iso) {
    var p = iso.split('-');
    return p[2] + '.' + p[1] + '.' + p[0];
  }
  function panelKapat() {
    Array.prototype.forEach.call(document.querySelectorAll('.tarih-panel'), function (p) { p.hidden = true; });
  }
  function aySec(input, yil, aa) {
    var iso;
    if (input.id === 'h-bit') {
      iso = (input.dataset.max && input.dataset.max.slice(0, 7) === yil + '-' + aa)
        ? input.dataset.max
        : yil + '-' + aa + '-' + new Date(+yil, +aa, 0).getDate();
    } else {
      iso = (input.dataset.min && input.dataset.min.slice(0, 7) === yil + '-' + aa)
        ? input.dataset.min
        : yil + '-' + aa + '-01';
    }
    input.dataset.iso = iso;
    input.value = trTarih(iso);
    panelKapat();
    secilenFiyatGuncelle();
  }
  function aylariGoster(input, panel, yil) {
    var seciliIso = input.dataset.iso || input.value;
    panel.innerHTML = '<div class="tp-baslik">' + yil + ' · ay seçin</div>';
    panel.style.gridTemplateColumns = 'repeat(3,1fr)';
    for (var a = 1; a <= 12; a++) {
      var aa = (a < 10 ? '0' : '') + a;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tp-btn' + (seciliIso.slice(0, 7) === yil + '-' + aa ? ' secili' : '');
      b.textContent = AY_KISA[a - 1];
      b.addEventListener('click', (function (yy, mm) {
        return function () { aySec(input, yy, mm); };
      })(yil, aa));
      panel.appendChild(b);
    }
  }
  function panelAc(input) {
    var panel = input.parentElement.querySelector('.tarih-panel');
    if (!panel) return;
    var acikMi = !panel.hidden;
    panelKapat();
    if (acikMi) return;
    var minYil = +(input.dataset.min || '2008-01-01').slice(0, 4);
    var maxYil = +(input.dataset.max || '2026-12-31').slice(0, 4);
    var yil = +(input.dataset.iso || input.value).slice(0, 4);
    panel.innerHTML = '<div class="tp-baslik">Yıl seçin</div>';
    panel.style.gridTemplateColumns = 'repeat(4,1fr)';
    for (var y = minYil; y <= maxYil; y++) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tp-btn' + (y === yil ? ' secili' : '');
      b.textContent = y;
      b.addEventListener('click', (function (yy) {
        return function () { aylariGoster(input, panel, yy); };
      })(y));
      panel.appendChild(b);
    }
    panel.hidden = false;
  }
  function tarihSeciciKur() {
    ['h-bas', 'h-bit'].forEach(function (id) {
      var input = document.getElementById(id);
      if (!input) return;
      if (input.value && input.value.indexOf('-') === 4) input.dataset.iso = input.value;
      if (input.dataset.iso) input.value = trTarih(input.dataset.iso);
      input.addEventListener('click', function () { panelAc(input); });
    });
    // Panel icindeki tiklamalar belgeye yayilmasin: yil butonu innerHTML yenilemesiyle
    // DOM'dan koparilinca kopuk hedefin closest(.tarih-alan)'i bos doner ve panel
    // haksiz yere kapanir (yil secilince ay listesi gorunmuyordu).
    Array.prototype.forEach.call(document.querySelectorAll('.tarih-panel'), function (p) {
      p.addEventListener('click', function (ev) { ev.stopPropagation(); });
    });
    document.addEventListener('click', function (ev) {
      if (!ev.target.closest('.tarih-alan')) panelKapat();
    });
  }

  /* ---------- başlat ---------- */
  function baslat() {
    fetch('temettu-veri/ozet.json').then(function (r) { return r.json(); }).then(function (d) {
      OZET = d;
      var chipKap = document.getElementById('takvim-yillar');
      var yillar = Array.from(new Set(d.takvim.map(function (o) { return o.tarih.slice(0, 4); }))).sort().reverse();
      var buYil = String(new Date().getFullYear());
      var secili = yillar.indexOf(buYil) >= 0 ? buYil : yillar[0];
      chipKap.innerHTML = yillar.map(function (y) {
        return '<button type="button" class="tm-chip" data-y="' + y + '" aria-pressed="' + (y === secili) + '">' + y + '</button>';
      }).join('') + '<button type="button" class="tm-chip" data-y="tumu" aria-pressed="false">Tümü</button>';
      chipKap.addEventListener('click', function (ev) {
        var b = ev.target.closest('button'); if (!b) return;
        Array.prototype.forEach.call(chipKap.children, function (c) { c.setAttribute('aria-pressed', c === b); });
        takvimCiz(b.dataset.y);
      });
      takvimCiz(secili);

      document.querySelectorAll('#verim-tablo th[data-sirala]').forEach(function (th) {
        th.addEventListener('click', function () {
          var yon = th.dataset.yon === 'azalan' ? 'artan' : 'azalan';
          th.dataset.yon = yon;
          verimSirala(th.dataset.sirala, yon);
        });
      });
    }).catch(function () {
      document.getElementById('takvim-liste').innerHTML = '<p class="tm-bos">Takvim verisi yüklenemedi.</p>';
    });

    fetch('temettu-veri/kur.json').then(function (r) { return r.json(); }).then(function (d) { KUR = d; }).catch(function () { });

    document.getElementById('h-hesapla').addEventListener('click', hesaplaTikla);
    document.getElementById('h-mod').addEventListener('change', modGuncelle);
    document.getElementById('h-hisse').addEventListener('change', secilenFiyatGuncelle);
    document.getElementById('h-bas').addEventListener('change', secilenFiyatGuncelle);
    document.getElementById('h-enf').addEventListener('change', function () {
      document.getElementById('h-enf-alan').hidden = !this.checked;
    });
    modGuncelle();
    tarihSeciciKur();
    secilenFiyatGuncelle();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', baslat);
  else baslat();
})();
