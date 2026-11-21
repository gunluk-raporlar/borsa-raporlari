"""Site JS'inin TEK KAYNAK modulu (2026-11-17).

site_css.py ile ayni desen: betik burada yasar, sayfa iskeletleri ayni sabiti
kullanir. Iki ayri iskelet var (bot.py `_sayfa` ve robot.py'nin bagimsiz kopyasi);
betigi iki yerde kopyalamak yerine buradan aliyorlar.
"""

# Yatay kaydirilabilir alanlar icin "tut-sur" (drag-to-pan) betigi.
#
# Kullanici bildirimi (2026-11-17): gunluk rapordaki genis tablolar masaustunde
# fare ile kullanissizdi — kaydirma cubugu alanin en altinda oldugu icin once
# asagi inmek, sonra cubugu tutup suruklemek gerekiyordu. Artik alan uzerinde
# basili tutup suruklemek yeterli.
#
# Kapsam sabit bir secici listesi DEGIL: en yakin ust oge, gercekten tasiyorsa
# (scrollWidth > clientWidth) ve overflow-x'i auto/scroll ise hedef sayilir.
# Boylece .tbl-wrap, .report table, .pgrafik-sarmal ve robot sayfasindaki satir
# ici overflow-x:auto kutulari ayni kuralla yakalanir; yeni bir kapsayici
# eklendiginde betigi degistirmek gerekmez.
#
# Dokunmatikte native kaydirma korunur: betik yalnizca fare/kalem isaretcisinde
# devreye girer. Surukleme sonrasi baglanti tiklamasi yutulur (tablo satirlari
# hisse baglantisi; suruklerken yanlislikla gezinme olmasin).
TABLO_KAYDIR_JS = """<script>
(function () {
  var ESIK = 5;
  var k = null, kimlik = null, x0 = 0, y0 = 0, s0 = 0, tasindi = false, yut = false;
  var isaretli = null, bekleyen = null, planlandi = false;
  var onbellek = new WeakMap();

  /* En yakin yatay kaydirilabilir ust oge. Sonuc WeakMap'te tutulur: fare bir
     tablonun uzerinde gezerken her karede yeniden yerlesim olcumu yapilmasin. */
  function kaydirilabilir(el) {
    var yol = [], bulunan = null;
    while (el && el.nodeType === 1 && el !== document.body && el !== document.documentElement) {
      if (onbellek.has(el)) { bulunan = onbellek.get(el); break; }
      yol.push(el);
      if (el.scrollWidth > el.clientWidth + 1) {
        var ox = window.getComputedStyle(el).overflowX;
        if (ox === 'auto' || ox === 'scroll') { bulunan = el; break; }
      }
      el = el.parentElement;
    }
    for (var i = 0; i < yol.length; i++) onbellek.set(yol[i], bulunan);
    return bulunan;
  }

  /* Imlec ipucu: fare kaydirilabilir bir alana girdiginde grab imleci.
     Kare basina en fazla bir tarama yapilir. */
  function imlec(e) {
    bekleyen = e.target;
    if (planlandi) return;
    planlandi = true;
    window.requestAnimationFrame(function () {
      planlandi = false;
      var el = kaydirilabilir(bekleyen);
      if (el === isaretli) return;
      if (isaretli) isaretli.classList.remove('tbl-kaydir');
      isaretli = el;
      if (el) el.classList.add('tbl-kaydir');
    });
  }

  function hareket(e) {
    if (!k || e.pointerId !== kimlik) return;
    var dx = e.clientX - x0, dy = e.clientY - y0;
    if (!tasindi) {
      if (Math.abs(dx) < ESIK) return;
      if (Math.abs(dx) < Math.abs(dy)) { k = null; return; }  /* dikey niyet: sayfa kaydirilsin */
      tasindi = true;
      k.classList.add('tbl-surukle');
      var secim = window.getSelection && window.getSelection();
      if (secim && secim.removeAllRanges) secim.removeAllRanges();
    }
    e.preventDefault();
    k.scrollLeft = s0 - dx;
  }

  function bitir(e) {
    if (e && kimlik !== null && e.pointerId !== kimlik) return;
    document.removeEventListener('pointermove', hareket);
    if (k) {
      k.classList.remove('tbl-surukle');
      if (tasindi) yut = true;
    }
    k = null; kimlik = null; tasindi = false;
  }

  document.addEventListener('pointerdown', function (e) {
    yut = false;
    if (e.pointerType === 'touch' || e.button !== 0) return;
    var t = e.target;
    if (t && t.closest && t.closest('input, select, textarea')) return;
    var hedef = kaydirilabilir(t);
    if (!hedef) return;
    k = hedef; kimlik = e.pointerId;
    x0 = e.clientX; y0 = e.clientY; s0 = hedef.scrollLeft; tasindi = false;
    document.addEventListener('pointermove', hareket, { passive: false });
  });

  document.addEventListener('pointerup', bitir);
  document.addEventListener('pointercancel', bitir);

  /* Surukleme sonrasi baglanti tiklamasi sayilmasin. */
  document.addEventListener('click', function (e) {
    if (!yut) return;
    yut = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  /* Baglantinin native suruklenmesi (hayalet gorsel) tut-sur'u bozmasin. */
  document.addEventListener('dragstart', function (e) {
    if (k) e.preventDefault();
  });

  document.addEventListener('pointerover', imlec);

  /* Yerlesim degisince olcum onbellegi gecersiz. */
  function tazele() { onbellek = new WeakMap(); }
  window.addEventListener('resize', tazele);
  window.addEventListener('load', tazele);
})();
</script>"""


# Kayan serit surucusu. CSS animasyonu translateX(-50%) kullaniyor; yuzde
# cozumu icerik genisligine bagli oldugu icin serit iki durumda bosaliyor ya da
# kayiyor (2026-11-18 telefon gozlemi: 2. serit karardi, icerik sag kenarda
# birikti):
#   1) Icerik bant genisligini doldurmuyor -> her turda bosluk.
#   2) Tarayici yuzdeyi eski/yanlis genislige gore cozuyor -> konum kaymasi.
# Cozum: mesafe piksel olarak hesaplanir, icerik bant + bir periyot dolana kadar
# cogaltilir ve konum her karede acikca yazilir. Yuzde cozumune bagimlilik
# kalmaz; serit hicbir fazda bos kalmaz. Yedek metinde (.ticker-oge yok)
# cogaltma yapilmaz, ayni cumle tekrarlanmasin diye animasyon durdurulur.
TICKER_DONGU_JS = """<script>
(function () {
  var SURE = 150;      // sn: bir periyotluk icerik 150 sn'de akar (eski 75 sn / %50 ile ayni hiz)
  var EN_FAZLA = 12;   // guvenlik: tek seritte en fazla 12 kopya
  var kayitlar = [];
  var kilit = 0;       // kendi yazimlarimiz gozcuya yakalanmasin
  var bantlar = document.querySelectorAll('.ticker-bant');
  if (!bantlar.length) return;

  function kur(bant) {
    var iz = bant.querySelector('.ticker-iz');
    if (!iz) return null;
    kilit++;   // kendi DOM yazimlarimiz gozcuya yakalanmasin
    try {
      // Onceki cogaltmalarimizi at; periyot her zaman sunucudan gelen icerik olsun.
      var ekler = iz.querySelectorAll('.ticker-ek');
      for (var i = 0; i < ekler.length; i++) ekler[i].parentNode.removeChild(ekler[i]);
      var tek = iz.innerHTML;
      if (!iz.textContent || !iz.textContent.replace(/\\s/g, '')) return null;

      iz.style.animation = 'none';
      iz.style.transform = 'none';
      if (!iz.querySelector('.ticker-oge')) return null;  // yedek metin: sabit kalsin
      var periyot = iz.getBoundingClientRect().width;
      if (!periyot) return null;

      var bantW = bant.clientWidth || 1;
      var n = Math.ceil((bantW + periyot) / periyot);
      if (n < 2) n = 2;
      if (n > EN_FAZLA) n = EN_FAZLA;
      var html = tek;
      for (var k = 1; k < n; k++) {
        html += '<span class="ticker-ek" aria-hidden="true" style="display:inline-block">' + tek + '</span>';
      }
      iz.innerHTML = html;

      if (!bant._tickerFare) {
        bant._tickerFare = true;
        bant.addEventListener('mouseenter', function () { bant._tickerDurdu = true; });
        bant.addEventListener('mouseleave', function () { bant._tickerDurdu = false; });
      }
      var x = bant._tickerX || 0;
      return { bant: bant, iz: iz, periyot: periyot, hiz: periyot / SURE, x: x % periyot };
    } finally {
      setTimeout(function () { kilit--; }, 0);
    }
  }

  function kurHepsi() {
    kayitlar = [];
    for (var i = 0; i < bantlar.length; i++) {
      var k = kur(bantlar[i]);
      if (k) kayitlar.push(k);
    }
  }

  var son = 0;
  function adim(t) {
    if (!son) son = t;
    var dt = (t - son) / 1000;
    son = t;
    if (dt > 0.5) dt = 0.5;   // sekme arka plandan donunce sicrama olmasin
    for (var i = 0; i < kayitlar.length; i++) {
      var k = kayitlar[i];
      if (k.bant._tickerDurdu) continue;
      k.x = (k.x + k.hiz * dt) % k.periyot;
      k.bant._tickerX = k.x;
      k.iz.style.transform = 'translate3d(' + (-k.x).toFixed(2) + 'px,0,0)';
    }
    requestAnimationFrame(adim);
  }

  // Guvenlik agi: konum her karede yazildigi icin normalde tetiklenmez. Yine de
  // serit bandi kapatmiyorsa (tarayici kaymasi) yeniden kurulur.
  var sonKur = 0;
  function denetle() {
    for (var i = 0; i < kayitlar.length; i++) {
      var k = kayitlar[i];
      if (k.bant.scrollLeft) k.bant.scrollLeft = 0;
      var r = k.iz.getBoundingClientRect(), rb = k.bant.getBoundingClientRect();
      if (r.right < rb.right - 8 || r.left > rb.left + 8) {
        if (Date.now() - sonKur > 10000) { sonKur = Date.now(); kurHepsi(); }
        return;
      }
    }
  }

  kurHepsi();
  requestAnimationFrame(adim);
  setInterval(denetle, 2000);

  var zaman = null;
  function yeniden() { clearTimeout(zaman); zaman = setTimeout(kurHepsi, 250); }
  window.addEventListener('resize', yeniden);
  window.addEventListener('orientationchange', yeniden);
  window.addEventListener('load', yeniden);

  // Serit icerigi fetch ile sonradan yenileniyor (5 dk). Icerik degisince
  // periyot yeniden olculmeli.
  if (window.MutationObserver) {
    var gozcu = new window.MutationObserver(function () {
      if (kilit) return;
      yeniden();
    });
    var izler = document.querySelectorAll('.ticker-iz');
    for (var j = 0; j < izler.length; j++) gozcu.observe(izler[j], { childList: true });
  }
})();
</script>"""
