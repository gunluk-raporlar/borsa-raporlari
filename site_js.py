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
