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
    var oncekiAy = null, aySayaci = 0;
    var aylikSeri = [], temettuKayit = [];
    var tekSeferlik = (mod === 2 || mod === 3 || mod === 4 || mod === 8);

    for (var i = 0; i < gunler.length; i++) {
      var gun = gunler[i][0], fiyat = gunler[i][1], ay = gun.slice(0, 7);

      if (bedMap[gun]) {
        var r = bedMap[gun];
        var ek = Math.floor(lot * (r - 1));
        lot += ek; bedelsizLot += ek;
      }

      if (temettuAl) {
        while (olayPtr < olaylar.length && olaylar[olayPtr].odeme <= gun) {
          var o = olaylar[olayPtr++];
          var brut = lot * o.dps;
          var net = Math.floor(lot * o.net);
          if (brut <= 0) continue;
          temettuBrut += brut; temettuNet += net; nakit += net;
          var alinabilir = fiyat > 0 ? Math.floor(nakit / fiyat) : 0;
          temettuKayit.push({
            tarih: gun, ex: o.ex, hisseBasi: o.dps, stopaj: o.stopaj, lot: lot,
            brut: brut, net: net, alinan: alinabilir, fiyat: fiyat, toplamLot: lot + alinabilir
          });
          if (alinabilir > 0) {
            lot += alinabilir; temettuLot += alinabilir; nakit -= alinabilir * fiyat;
          }
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
        } else { nakit = butce; }
      }

      if (ayBasi || i === gunler.length - 1) {
        aylikSeri.push({
          tarih: gun, lot: lot, fiyat: fiyat, deger: lot * fiyat, yatirilan: yatirilan,
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
      aylikSeri: aylikSeri, temettuKayit: temettuKayit, gunSayisi: gunler.length
    };
  }

  /* ---------- SVG grafik ---------- */
  function cizGrafik(kap, seriler, secenek) {
    secenek = secenek || {};
    var G = 680, Y = 300, sol = 62, sag = 14, ust = 14, alt = 30;
    var icG = G - sol - sag, icY = Y - ust - alt;
    var tum = [];
    seriler.forEach(function (s) { s.noktalar.forEach(function (p) { if (isFinite(p[1])) tum.push(p[1]); }); });
    if (!tum.length) { kap.innerHTML = '<p class="tm-bos">Grafik için veri yok.</p>'; return; }
    var min = Math.min.apply(null, tum), max = Math.max.apply(null, tum);
    if (min === max) { min = min * 0.95; max = max * 1.05; }
    var pay = (max - min) * 0.08; min -= pay; max += pay;
    if (secenek.sifirdan) min = 0;

    var xMin = seriler[0].noktalar[0][0], xMax = seriler[0].noktalar[seriler[0].noktalar.length - 1][0];
    var tMin = new Date(xMin).getTime(), tMax = new Date(xMax).getTime();
    if (tMax === tMin) tMax = tMin + 864e5;

    function X(t) { return sol + (new Date(t).getTime() - tMin) / (tMax - tMin) * icG; }
    function Yy(v) { return ust + icY - (v - min) / (max - min) * icY; }

    var svg = '<svg viewBox="0 0 ' + G + ' ' + Y + '" role="img" aria-label="' + (secenek.baslik || 'grafik') + '">';
    // ızgara + y ekseni
    for (var g = 0; g <= 4; g++) {
      var deger = min + (max - min) * g / 4, yy = Yy(deger);
      svg += '<line x1="' + sol + '" y1="' + yy.toFixed(1) + '" x2="' + (G - sag) + '" y2="' + yy.toFixed(1) +
        '" stroke="var(--line)" stroke-width="1" stroke-dasharray="' + (g === 0 ? '0' : '3 3') + '"/>';
      svg += '<text x="' + (sol - 8) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end" font-size="10.5" fill="var(--muted)">' +
        (secenek.yFormat ? secenek.yFormat(deger) : TR(deger)) + '</text>';
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
        d2 += (ilk ? 'M' : 'L') + X(p[0]).toFixed(1) + ' ' + Yy(p[1]).toFixed(1);
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
        if (bul) html += '<br>' + s.ad + ': ' + (secenek.ipucuFormat ? secenek.ipucuFormat(bul[1]) : TR(bul[1]));
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

    var html = '<div class="sonuc-ust">' +
      '<div class="sonuc-kart"><div class="l">Lot Sayısı</div><div class="v">' + TR(sonuc.lotSayisi) + '</div>' +
      '<div class="a">' + TR(sonuc.gunSayisi) + ' işlem günü</div></div>' +
      '<div class="sonuc-kart"><div class="l">Ortalama Maliyet</div><div class="v">' + TR(sonuc.ortalamaMaliyet, 2) + ' TL</div>' +
      '<div class="a">lot başına</div></div>' +
      '<div class="sonuc-kart"><div class="l">Güncel Değer</div><div class="v">' + paraYaz(sonuc.guncelDeger, p) + '</div>' +
      '<div class="a">' + TR(sonuc.sonFiyat, 2) + ' TL kapanış</div></div>' +
      '<div class="sonuc-kart"><div class="l">Kâr / Zarar</div><div class="v ' + degisimSinif + '">' +
      (sonuc.karZarar >= 0 ? '+' : '') + paraYaz(sonuc.karZarar, p) + '</div>' +
      '<div class="a ' + degisimSinif + '">' + YUZ(sonuc.getiriYuzde) + '</div></div>' +
      '</div>';

    html += '<div class="tm-seg" id="h-sonuc-para" style="margin:0 0 14px">' +
      ['tl', 'usd', 'altin'].map(function (x) {
        var ad = x === 'tl' ? 'TL' : x === 'usd' ? 'Dolar' : 'Gram Altın';
        return '<button type="button" data-p="' + x + '" aria-pressed="' + (p === x) + '">' + ad + '</button>';
      }).join('') + '</div>';

    html += '<div class="detay-liste">' +
      '<div class="k">Yatırılan toplam</div><div class="v">' + paraYaz(sonuc.yatirilan, p) + '</div>' +
      '<div class="k">Bitiş tarihindeki hisse fiyatı</div><div class="v">' + TR(sonuc.sonFiyat, 2) + ' TL</div>' +
      '<div class="k">Temettüden kazanılan lot</div><div class="v">' + TR(sonuc.temettuLot) + '</div>' +
      '<div class="k">Bedelsizden kazanılan lot</div><div class="v">' + TR(sonuc.bedelsizLot) + '</div>' +
      '<div class="ayrac"></div>' +
      '<div class="k">Toplam temettü (brüt)</div><div class="v">' + paraYaz(sonuc.temettuBrut, p) + '</div>' +
      '<div class="k">Toplam temettü (net, stopaj sonrası)</div><div class="v">' + paraYaz(sonuc.temettuNet, p) + '</div>' +
      '<div class="k">Temettünün maliyete oranı</div><div class="v">' +
      (sonuc.yatirilan > 0 ? TR(sonuc.temettuBrut / sonuc.yatirilan * 100, 1) + '%' : '—') + '</div>' +
      '<div class="ayrac"></div>' +
      '<div class="k">Enflasyon düzeltmeli maliyet</div><div class="v">' + paraYaz(sonuc.enflasyonMaliyet, p) + '</div>' +
      '<div class="k">Enflasyona göre reel getiri</div><div class="v ' + (sonuc.enflasyonReelGetiri >= 0 ? 'pos' : 'neg') + '">' +
      YUZ(sonuc.enflasyonReelGetiri) + '</div>' +
      '</div>';

    html += '<div class="graf-sekmeler" id="h-graf-sekme">' +
      [['Portföy Değeri', 0], ['Hisse Fiyatı', 1], ['Fiyat Ortalamaları', 2], ['Aylık Değişim', 3]].map(function (s) {
        return '<button type="button" data-s="' + s[1] + '" aria-pressed="' + (sekmem === s[1]) + '">' + s[0] + '</button>';
      }).join('') + '</div>' +
      '<div class="graf-kutu" id="h-graf"></div>' +
      '<div class="graf-lejant" id="h-lejant"></div>';

    if (sonuc.temettuKayit.length) {
      html += '<h3 style="font-size:14px;margin:20px 0 8px">Temettü geçmişi</h3><div class="tbl-wrap"><table class="tm-tablo tm-tablo-min">' +
        '<thead><tr><th>Ex-tarih</th><th class="sayi">Hisse başı</th><th class="sayi">Stopaj</th>' +
        '<th class="sayi">Eldeki lot</th><th class="sayi">Net temettü</th><th class="sayi">Alınan lot</th></tr></thead><tbody>' +
        sonuc.temettuKayit.map(function (t) {
          return '<tr><td>' + tarihYaz(t.ex) + '</td>' +
            '<td class="sayi">' + TR(t.hisseBasi, 4) + '</td>' +
            '<td class="sayi">%' + TR(t.stopaj * 100, 0) + '</td>' +
            '<td class="sayi">' + TR(t.lot) + '</td>' +
            '<td class="sayi">' + TR(t.net) + '</td>' +
            '<td class="sayi">' + TR(t.alinan) + '</td></tr>';
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
    var p = para;

    if (sekmem === 0) {
      var deger = seri.map(function (r) { return [r.tarih, paraDeger(r.deger, p)]; });
      var yat = seri.map(function (r) { return [r.tarih, paraDeger(r.yatirilan, p)]; });
      cizGrafik(kap, [
        { ad: 'Portföy', renk: 'var(--accent)', noktalar: deger, dolu: true, kalin: 2.2 },
        { ad: 'Yatırılan', renk: '#94a3b8', noktalar: yat, kesik: true, kalin: 1.6 }
      ], {
        baslik: 'Portföy değeri', sifirdan: true,
        yFormat: function (v) {
          if (p !== 'tl') return TR(v, 0);
          if (Math.abs(v) >= 1e6) return TR(v / 1e6, 1) + 'M';
          if (Math.abs(v) >= 1e3) return TR(v / 1e3, 0) + 'B';
          return TR(v, 0);
        },
        ipucuFormat: function (v) { return p === 'tl' ? TRY(v) : TR(v, 2) + paraEtiket(p); }
      });
      lejant.innerHTML = '<span><i style="border-color:var(--accent)"></i>Portföy değeri</span>' +
        '<span><i style="border-color:#94a3b8"></i>Yatırılan toplam</span>';
    } else if (sekmem === 1) {
      var f = seri.map(function (r) { return [r.tarih, r.fiyat]; });
      cizGrafik(kap, [{ ad: 'Fiyat', renk: 'var(--accent)', noktalar: f, dolu: true, kalin: 2 }],
        { baslik: 'Hisse fiyatı', yFormat: function (v) { return TR(v, 0); }, ipucuFormat: function (v) { return TR(v, 2) + ' TL'; } });
      lejant.innerHTML = '<span><i style="border-color:var(--accent)"></i>Kapanış fiyatı (TL)</span>';
    } else if (sekmem === 2) {
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
    var bas = document.getElementById('h-bas').value;
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
    var bas = document.getElementById('h-bas').value, bit = document.getElementById('h-bit').value;
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
      if (sonuc) { sekmem = 0; para = 'tl'; sonucCiz(); }
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
    secilenFiyatGuncelle();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', baslat);
  else baslat();
})();
