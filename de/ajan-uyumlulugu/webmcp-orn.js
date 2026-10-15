/* ajan-uyumlulugu/webmcp-orn.js — HAZIR, SİTEYE BAĞLI DEĞİL
 *
 * WebMCP (https://github.com/webmachinelearning/webmcp) araç kaydı:
 * site araması, ticker verisi ve günlük rapor listesini ajanlara sunar.
 * Desteklemeyen tarayıcıda sessizce no-op olur (feature detection).
 *
 * Bağlama: bot.py header şablonuna
 *   <script src="{kok}webmcp-orn.js" defer></script>
 * Chrome kalıcı sürüm için header'a Origin Trial token meta'sı gerekir
 * (bkz. OKU.md). ChatGPT Desktop token istemez.
 */
(function () {
  "use strict";
  var mc = (typeof document !== "undefined" && document.modelContext)
        || (typeof navigator !== "undefined" && navigator.modelContext);
  if (!mc || typeof mc.registerTool !== "function") { return; }

  function normalize(s) {
    var harita = { "\u0131": "i", "\u0130": "i", "I": "i", "\u011f": "g", "\u011e": "g",
                   "\u00fc": "u", "\u00dc": "u", "\u015f": "s", "\u015e": "s",
                   "\u00f6": "o", "\u00d6": "o", "\u00e7": "c", "\u00c7": "c" };
    s = String(s).toLowerCase().replace(/\u0307/g, "");
    return s.replace(/[\u0131\u0130I\u011f\u011e\u00fc\u00dc\u015f\u015e\u00f6\u00d6\u00e7\u00c7]/g,
                     function (h) { return harita[h] || h; });
  }

  // 1) Site aramasi — ajan icin yapilandirilmis sonuc dondurur (DOM scraping yerine)
  mc.registerTool({
    name: "site_ara",
    description: "borsa-raporlari.com.tr sitesinde hisse kodu, sirket, konu veya tarih arar. Baslik + URL + alinti listesi dondurur.",
    inputSchema: {
      type: "object",
      properties: { terim: { type: "string", description: "Aranacak hisse kodu, sirket adi, konu veya tarih" } },
      required: ["terim"]
    },
    execute: async function (args) {
      var terim = String((args && args.terim) || "").trim();
      if (!terim) { return { hata: "terim bos" }; }
      var r = await fetch("/site-arama.json?t=" + Date.now(), { cache: "no-store" });
      var d = await r.json();
      var n = normalize(terim);
      var sonuc = (d.sayfalar || []).map(function (s) {
        var puan = 8 * (normalize(s.b).split(n).length - 1)
                 + (normalize(s.t).split(n).length - 1);
        return { baslik: s.b, url: s.u, alinti: s.t.slice(0, 160), puan: puan };
      }).filter(function (x) { return x.puan > 0; })
        .sort(function (a, b) { return b.puan - a.puan; })
        .slice(0, 6)
        .map(function (x) { return { baslik: x.baslik, url: x.url, alinti: x.alinti }; });
      return { guncelleme: d.guncelleme, sonucSayisi: sonuc.length, sonuclar: sonuc };
    }
  });

  // 2) Ticker verisi — kayan seridin arkasindaki sayilar (DOM'da gorunmez, JSON'da var)
  mc.registerTool({
    name: "ticker_getir",
    description: "BIST 30 hisselerinin son fiyatlari ve gunluk degisim yuzdeleri (gecikmeli veri).",
    inputSchema: { type: "object", properties: {} },
    execute: async function () {
      var r = await fetch("/ticker.json?t=" + Date.now(), { cache: "no-store" });
      var d = await r.json();
      return {
        guncelleme: d.guncelleme,
        etiket: d.etiket,
        hisseler: (d.hisseler || []).map(function (h) {
          return { kod: h.h, fiyatTL: h.f, degisimYuzde: h.d, sinyal: h.s || null };
        })
      };
    }
  });

  // 3) Gunluk rapor listesi — tarih sirali son raporlar
  mc.registerTool({
    name: "gunluk_raporlar",
    description: "borsa-raporlari.com.tr gunluk piyasa raporlarinin en yenilerini listeler.",
    inputSchema: {
      type: "object",
      properties: { adet: { type: "number", description: "Kac rapor donsun (varsayilan 5)" } }
    },
    execute: async function (args) {
      var adet = Math.min(Number((args && args.adet) || 5) || 5, 20);
      var r = await fetch("/site-arama.json?t=" + Date.now(), { cache: "no-store" });
      var d = await r.json();
      var raporlar = (d.sayfalar || [])
        .filter(function (s) { return s.u.indexOf("raporlar/") === 0; })
        .slice(0, adet)
        .map(function (s) { return { baslik: s.b, url: s.u, ozet: s.t.slice(0, 200) }; });
      return { raporlar: raporlar };
    }
  });
})();
