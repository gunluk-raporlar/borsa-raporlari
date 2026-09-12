# -*- coding: utf-8 -*-
"""Excel gorunumlu teknik tarama tablosu — Borsa Okulu ders sayfalari icin.

Amac: dersin "Gerçek Örnek" bölümüne, gerçek data/teknik taramasından üretilen,
gerçek bir Excel çalışma sayfası görünümünde tablo gömmek.

Kullanım:
    tarih, satirlar = excel_tablo.veri_yukle()           # data/teknik/<tarih>.json
    html = excel_tablo.excel_tablo_html(satirlar, tarih)  # self-contained HTML

- Dış bağımlılık yok: CSS + JS tabloyla birlikte gömülür (arşiv sayfalarında da çalışır).
- Sıralama: sütun başlığına tıkla. Hücre seçince ad kutusu + formül çubuğu güncellenir.
- Kanal % sütununda Excel tarzı veri çubuğu (data bar), sinyalde koşullu biçimlendirme.
"""
import os
import json
import glob
import html

# --- Biçimlendirme yardımcıları -------------------------------------------

def _tr_sayi(x, hane=2):
    """1234.5 -> '1.234,50' (Türkçe binlik . , ondalık)"""
    s = f"{x:,.{hane}f}"
    return s.replace(",", "@").replace(".", ",").replace("@", ".")


def _fmt_tl(x):
    return _tr_sayi(x, 2)


def _fmt_pct(x, hane=2, isaret=True):
    """9.94 -> '+%9,94' ; -0.78 -> '-%0,78'"""
    isaretli = isaret and abs(x) > 1e-9
    isaret_s = "+" if (isaretli and x > 0) else ("" if x >= 0 else "-")
    return f"{isaret_s}%{_tr_sayi(abs(x), hane)}"


def _fmt_r(x):
    return ("+" if x > 0 else "") + _tr_sayi(x, 2)


SINYAL_SINIF = {"GÜÇLÜ AL": "gua", "AL": "al", "NÖTR": "notr", "SAT": "sat", "GÜÇLÜ SAT": "gus"}
SINYAL_SIRA = {"GÜÇLÜ SAT": 0, "SAT": 1, "NÖTR": 2, "AL": 3, "GÜÇLÜ AL": 4}


def veri_yukle(klasor=None):
    """data/teknik altındaki en güncel taramayı döndürür: (tarih, satirlar)."""
    if klasor is None:
        klasor = os.path.join("data", "teknik")
    dosyalar = sorted(glob.glob(os.path.join(klasor, "*.json")))
    if not dosyalar:
        return None, []
    son = dosyalar[-1]
    tarih = os.path.splitext(os.path.basename(son))[0]
    try:
        with open(son, encoding="utf-8") as f:
            satirlar = json.load(f)
    except Exception:
        satirlar = []
    return tarih, satirlar if isinstance(satirlar, list) else []


# --- CSS (bir kez gömülür, .xlsx-box kapsamlı) -----------------------------

_CSS = """
.xlsx-box{--x-ink:#0f172a;--x-muted:#64748b;--x-line:#d7dde6;--x-line2:#e6ebf2;
  --x-head:#0f172a;--x-accent:#0f766e;--x-pos:#047857;--x-neg:#b91c1c;
  --x-gutter:#eef2f7;--x-sel:#0e9f6e;
  background:#fff;border:1px solid var(--x-line);border-radius:10px;overflow:hidden;
  box-shadow:0 1px 2px rgba(15,23,42,.06);margin:18px 0 22px;font-size:13px;color:var(--x-ink);}
.xlsx-titlebar{display:flex;align-items:center;gap:10px;padding:8px 12px;
  background:linear-gradient(180deg,#10715f,#0d5f51);color:#fff;}
.xlsx-titlebar .xlsx-dosya{font-weight:700;font-size:13.5px;letter-spacing:.2px;}
.xlsx-titlebar .xlsx-rota{font-size:11.5px;opacity:.85;}
.xlsx-titlebar .xlsx-tarihbadge{margin-left:auto;background:rgba(255,255,255,.16);
  border:1px solid rgba(255,255,255,.35);border-radius:999px;padding:2px 10px;font-size:11.5px;font-weight:600;}
.xlsx-toolbar{display:flex;align-items:stretch;border-bottom:1px solid var(--x-line);background:#f8fafc;}
.xlsx-adkutu{min-width:64px;padding:6px 10px;border-right:1px solid var(--x-line);
  font-weight:700;font-size:12.5px;text-align:center;background:#fff;color:var(--x-ink);}
.xlsx-fxetiket{padding:6px 10px;border-right:1px solid var(--x-line);color:var(--x-muted);
  font-style:italic;font-family:Georgia,serif;font-size:13px;display:flex;align-items:center;}
.xlsx-fxdeger{padding:6px 12px;font-size:12.5px;color:var(--x-ink);display:flex;align-items:center;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.xlsx-sheet{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.xlsx-box table{display:table;width:100%;min-width:780px;border-collapse:separate;border-spacing:0;
  margin:0;font-size:13px;line-height:1.45;}
.xlsx-box table caption{display:none;}
.xlsx-box th,.xlsx-box td{border-bottom:1px solid var(--x-line2);border-right:1px solid var(--x-line2);
  padding:7px 10px;text-align:left;font-weight:400;text-transform:none;letter-spacing:0;background:#fff;}
.xlsx-box th:last-child,.xlsx-box td:last-child{border-right:none;}
.xlsx-box tbody tr:last-child td{border-bottom:none;}
.xlsx-harfler th{background:var(--x-gutter);color:#475569;font-size:11px;font-weight:600;
  text-align:center;padding:3px 10px;border-bottom:1px solid var(--x-line);}
.xlsx-gutter{background:var(--x-gutter)!important;color:#64748b;font-size:11px;text-align:center!important;
  font-weight:600!important;width:34px;min-width:34px;}
.xlsx-baslik th{background:var(--x-head);color:#fff;font-size:12px;font-weight:600;
  white-space:nowrap;cursor:pointer;user-select:none;}
.xlsx-baslik th:hover{background:#1e293b;}
.xlsx-baslik th.xlsx-sirali{background:#134e4a;box-shadow:inset 0 -2px 0 #2dd4bf;}
.xlsx-ok{font-size:9px;margin-left:5px;opacity:.9;}
.xlsx-box td.num,.xlsx-box th.num{text-align:right;font-variant-numeric:tabular-nums;}
.xlsx-satirno{position:relative;}
.xlsx-box td{position:relative;}
.xlsx-databar{position:absolute;left:0;top:0;bottom:0;background:rgba(15,118,110,.13);
  border-right:2px solid rgba(15,118,110,.4);}
.xlsx-barval{position:relative;}
.posx{color:var(--x-pos);font-weight:600;}
.negx{color:var(--x-neg);font-weight:600;}
.ntrx{color:#475569;}
.xlsx-badge{display:inline-block;border-radius:999px;padding:1px 9px;font-size:11px;font-weight:700;white-space:nowrap;}
.xlsx-badge.gua{background:#d1fae5;color:#065f46;border:1px solid #6ee7b7;}
.xlsx-badge.al{background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;}
.xlsx-badge.notr{background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;}
.xlsx-badge.sat{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;}
.xlsx-badge.gus{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;}
.xlsx-secili{outline:2px solid var(--x-sel);outline-offset:-2px;background:#f0fdfa!important;
  box-shadow:inset 0 0 0 1px var(--x-sel);}
.xlsx-secili.xlsx-gutter{background:#e2e8f0!important;}
.xlsx-tabs{display:flex;gap:2px;align-items:flex-end;border-top:1px solid var(--x-line);background:#f1f5f9;padding:0 8px;}
.xlsx-tab{background:#fff;border:1px solid var(--x-line);border-bottom:none;border-radius:6px 6px 0 0;
  margin:6px 2px 0;padding:4px 14px;font-size:12px;font-weight:700;color:var(--x-accent);
  box-shadow:0 -1px 0 rgba(15,23,42,.04) inset;}
.xlsx-tab.pasif{background:#e8edf3;color:#64748b;font-weight:500;}
.xlsx-status{display:flex;flex-wrap:wrap;gap:4px 16px;padding:7px 14px;background:var(--x-head);
  color:#cbd5e1;font-size:11.5px;}
.xlsx-status b{color:#fff;font-weight:600;}
.xlsx-not{padding:6px 14px 9px;color:var(--x-muted);font-size:11.5px;background:#fbfcfe;border-top:1px dashed var(--x-line2);}
@media (max-width:640px){
  .xlsx-box{font-size:12px;}
  .xlsx-box th,.xlsx-box td{padding:5px 7px;}
  .xlsx-fxetiket{display:none;}
}
"""

# --- JS (sıralama + hücre seçimi / formül çubuğu) --------------------------

_JS = """
(function(){
  var kutu = document.getElementById('__ID__');
  if(!kutu || kutu.getAttribute('data-xlsx-init')) return;
  kutu.setAttribute('data-xlsx-init','1');
  var adkutu = kutu.querySelector('.xlsx-adkutu');
  var fx = kutu.querySelector('.xlsx-fxdeger');
  function sec(td){
    var eski = kutu.querySelectorAll('.xlsx-secili');
    for(var i=0;i<eski.length;i++) eski[i].classList.remove('xlsx-secili');
    td.classList.add('xlsx-secili');
    adkutu.textContent = td.getAttribute('data-ref')||'';
    fx.textContent = td.getAttribute('data-fx')||'';
  }
  kutu.addEventListener('click', function(ev){
    var td = ev.target && ev.target.closest ? ev.target.closest('td[data-ref]') : null;
    if(td) sec(td);
  });
  var ilk = kutu.querySelector('td[data-preselect]') || kutu.querySelector('td[data-ref]');
  if(ilk) sec(ilk);
  var basliklar = kutu.querySelectorAll('.xlsx-baslik th[data-sort]');
  for(var i=0;i<basliklar.length;i++){
    basliklar[i].addEventListener('click', function(){
      var th = this;
      var key = th.getAttribute('data-sort');
      var yon = th.getAttribute('data-yon')==='asc' ? 'desc' : 'asc';
      for(var j=0;j<basliklar.length;j++){
        basliklar[j].removeAttribute('data-yon');
        basliklar[j].classList.remove('xlsx-sirali');
        var ok = basliklar[j].querySelector('.xlsx-ok');
        if(ok) ok.textContent='';
      }
      th.setAttribute('data-yon', yon);
      th.classList.add('xlsx-sirali');
      var ok = th.querySelector('.xlsx-ok');
      if(ok) ok.textContent = yon==='asc' ? '\\u25B2' : '\\u25BC';
      var govde = kutu.querySelector('tbody.xlsx-data');
      var satirlar = Array.prototype.slice.call(govde.querySelectorAll('tr'));
      satirlar.sort(function(a,b){
        var va=a.getAttribute('data-'+key), vb=b.getAttribute('data-'+key);
        var na=parseFloat(va), nb=parseFloat(vb);
        var r = (!isNaN(na)&&!isNaN(nb)) ? (na-nb) : String(va).localeCompare(String(vb),'tr');
        return yon==='asc' ? r : -r;
      });
      for(var k=0;k<satirlar.length;k++){
        govde.appendChild(satirlar[k]);
        var no = satirlar[k].querySelector('.xlsx-satirno');
        if(no) no.textContent = String(k+2);
      }
    });
  }
})();
"""


def _hucre(harf, satir_no, hisse, alan_adi, deger_html, deger_metin, deger_sort, sinif="", preselect=False):
    ref = f"{harf}{satir_no}"
    fx = f"={ref}  →  {hisse} • {alan_adi} = {deger_metin}"
    pre = " data-preselect=\"1\"" if preselect else ""
    cls = f" class=\"{sinif}\"" if sinif else ""
    return (f"<td{cls} data-ref=\"{ref}\" data-fx=\"{html.escape(fx)}\" "
            f"data-{_sort_anahtar(alan_adi)}=\"{deger_sort}\"{pre}>{deger_html}</td>")


_SORT_KEY = {"Son (TL)": "son", "Günlük %": "gunluk", "60 Gün %": "deg60",
             "Kanal %": "konum", "Korelasyon r": "r", "Sinyal": "genel"}


def _sort_anahtar(alan_adi):
    return _SORT_KEY.get(alan_adi, "hisse")


def excel_tablo_html(satirlar, tarih, tablo_id="xlsx-egitim",
                     baslik="TEKNIK-TARAMA.XLSX", kaynak_yolu=None,
                     preselect_hisse=None, preselect_alan="gunluk"):
    """Gerçek tarama satırlarından Excel görünümlü, kendi başına çalışır HTML döndürür."""
    if not satirlar:
        return ""
    if kaynak_yolu is None:
        kaynak_yolu = f"data/teknik/{tarih}.json"

    satirlar = sorted(satirlar, key=lambda s: abs(s.get("gunluk", 0) or 0), reverse=True)

    # durum çubuğu istatistikleri
    gunlukler = [s.get("gunluk", 0) or 0 for s in satirlar]
    ort = sum(gunlukler) / len(gunlukler) if gunlukler else 0
    en_h = max(satirlar, key=lambda s: abs(s.get("gunluk", 0) or 0))

    HARM = ["A", "B", "C", "D", "E", "F", "G"]
    BASLIKLAR = ["Hisse", "Son (TL)", "Günlük %", "60 Gün %", "Kanal %", "Korelasyon r", "Sinyal"]

    # preselect hedefi
    pre_ref_satir = 0
    for i, s in enumerate(satirlar):
        if preselect_hisse and s.get("hisse") == preselect_hisse:
            pre_ref_satir = i
            break

    g = []
    g.append(f"<style>{_CSS}</style>")
    g.append(f"<div class=\"xlsx-box\" id=\"{tablo_id}\" role=\"group\" aria-label=\"Gerçek teknik tarama verisi, Excel görünümü\">")
    # başlık çubuğu
    g.append("<div class=\"xlsx-titlebar\">"
             f"<span class=\"xlsx-dosya\">📊 {html.escape(baslik)}</span>"
             "<span class=\"xlsx-rota\">gerçek BIST teknik taraması</span>"
             f"<span class=\"xlsx-tarihbadge\">{tarih}</span></div>")
    # formül çubuğu
    g.append("<div class=\"xlsx-toolbar\">"
             "<div class=\"xlsx-adkutu\">—</div>"
             "<div class=\"xlsx-fxetiket\">fx</div>"
             "<div class=\"xlsx-fxdeger\">Bir hücreye tıklayın…</div></div>")
    # tablo
    g.append("<div class=\"xlsx-sheet\"><table>")
    g.append("<thead>")
    g.append("<tr class=\"xlsx-harfler\" aria-hidden=\"true\"><th class=\"xlsx-gutter\"></th>"
             + "".join(f"<th>{h}</th>" for h in HARM) + "</tr>")
    g.append("<tr class=\"xlsx-baslik\"><th class=\"xlsx-gutter\">1</th>"
             + "".join(
                 f"<th data-sort=\"{_sort_anahtar(b)}\" data-yon=\"\">{b}<span class=\"xlsx-ok\"></span></th>"
                 for b in BASLIKLAR)
             + "</tr></thead>")
    g.append("<tbody class=\"xlsx-data\">")

    for i, s in enumerate(satirlar):
        no = i + 2
        hisse = s.get("hisse", "?")
        son = s.get("son", 0) or 0
        gunluk = s.get("gunluk", 0) or 0
        deg60 = s.get("deg60", 0) or 0
        konum = s.get("konum", 0) or 0
        r = s.get("r", 0) or 0
        genel = (s.get("genel", "NÖTR") or "NÖTR").upper()

        son_s = _fmt_tl(son)
        gunluk_s = _fmt_pct(gunluk)
        gunluk_ok = "▲" if gunluk > 0 else ("▼" if gunluk < 0 else "•")
        deg60_s = _fmt_pct(deg60)
        konum_s = _fmt_pct(konum, 0, isaret=False)
        r_s = _fmt_r(r)
        genel_gorsel = html.escape(genel)
        sira = SINYAL_SIRA.get(genel, 2)
        sinif = SINYAL_SINIF.get(genel, "notr")

        yon_sinif = "posx" if gunluk > 0 else ("negx" if gunluk < 0 else "ntrx")
        yon60_sinif = "posx" if deg60 > 0 else ("negx" if deg60 < 0 else "ntrx")

        bar_p = max(0.0, min(konum, 150.0)) / 150.0 * 100.0

        hucreler = []
        hucreler.append(_hucre("A", no, hisse, "Hisse", f"<b>{hisse}</b>", hisse, hisse))
        hucreler.append(_hucre("B", no, hisse, "Son (TL)", son_s, son_s, son, "num",
                               preselect=(pre_ref_satir == i and preselect_alan == "son")))
        hucreler.append(_hucre("C", no, hisse, "Günlük %",
                               f"<span class=\"{yon_sinif}\">{gunluk_s} {gunluk_ok}</span>",
                               gunluk_s, gunluk, "num",
                               preselect=(pre_ref_satir == i and preselect_alan == "gunluk")))
        hucreler.append(_hucre("D", no, hisse, "60 Gün %",
                               f"<span class=\"{yon60_sinif}\">{deg60_s}</span>",
                               deg60_s, deg60, "num"))
        hucreler.append(_hucre("E", no, hisse, "Kanal %",
                               f"<span class=\"xlsx-databar\" style=\"width:{bar_p:.1f}%\"></span>"
                               f"<span class=\"xlsx-barval\">{konum_s}</span>",
                               konum_s, konum, "num"))
        hucreler.append(_hucre("F", no, hisse, "Korelasyon r", r_s, r_s, r, "num ntrx"))
        hucreler.append(_hucre("G", no, hisse, "Sinyal",
                               f"<span class=\"xlsx-badge {sinif}\">{genel_gorsel}</span>",
                               genel, sira))

        tr = (f"<tr data-hisse=\"{hisse}\" data-son=\"{son}\" data-gunluk=\"{gunluk}\" "
              f"data-deg60=\"{deg60}\" data-konum=\"{konum}\" data-r=\"{r}\" data-genel=\"{sira}\">"
              f"<td class=\"xlsx-gutter xlsx-satirno\" aria-hidden=\"true\">{no}</td>"
              + "".join(hucreler) + "</tr>")
        g.append(tr)

    g.append("</tbody></table></div>")

    # sayfa sekmeleri + durum çubuğu
    g.append("<div class=\"xlsx-tabs\">"
             "<span class=\"xlsx-tab\">Teknik Tarama</span>"
             f"<span class=\"xlsx-tab pasif\">Kaynak: {html.escape(kaynak_yolu)}</span></div>")
    g.append("<div class=\"xlsx-status\">"
             f"<span><b>{len(satirlar)}</b> kayıt</span>"
             "<span><b>7</b> sütun</span>"
             f"<span>Ort. günlük: <b>{_fmt_pct(ort)}</b></span>"
             f"<span>En hareketli: <b>{en_h.get('hisse','—')} {_fmt_pct(en_h.get('gunluk',0) or 0)}</b></span>"
             "<span>Sıralamak için sütun başlığına tıklayın</span></div>")
    g.append("<div class=\"xlsx-not\">Hücre değerleri "
             f"{tarih} tarihli gerçek teknik taramasından alınmıştır (BIST 30). "
             "Kanal %: fiyatın 60 günlük aralıktaki konumu • r: Pearson korelasyonu. "
             "Eğitim amaçlıdır, yatırım tavsiyesi değildir.</div>")

    g.append("</div>")
    g.append(f"<script>{_JS.replace('__ID__', tablo_id)}</script>")
    return "".join(g)
