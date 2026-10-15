# ajan-uyumlulugu/ — HAZIR PAKET (siteye BAĞLI DEĞİL)

Bu klasör **yayına çıkmaz** (build.sh yalnızca kök dosyaları ve listeli dizinleri
kopyalar; bu klasör listede yok). Ajan-uyumluluğunun 2. ve 3. katmanının
hazır hali burada bekler; bağlama kararı verilince aşağıdaki adımlar uygulanır.

## 1. WebMCP araçları (`webmcp-orn.js`)

Site araması, ticker verisi ve son raporları ajanlara sunan kayıt bloğu.
Tarayıcı desteklemiyorsa sessizce no-op olur — siteye zararı yok.

**Bağlama adımları:**
1. `webmcp-orn.js`'i depo köküne kopyala (build.sh `*.js`... kökte js kopyalanmaz;
   ya `dist/` adımına ekleyin ya da bot.py'deki header şablonuna inline gömün).
2. Chrome'un kalıcı sürümü için **Origin Trial token'ı** alın:
   https://developer.chrome.com/docs/ai/webmcp sayfasındaki trial bağlantısından
   token üretip header şablonuna `<meta http-equiv="origin-trial" content="TOKEN">`
   ekleyin. ChatGPT Desktop ve deneme bayraklı Chrome'lar token istemez.
3. Test: Chrome'da `about:flags#enable-webmcp-testing` açın; ajan panelden
   "site_ara TOASO" çağrısını deneyin.

## 2. llms.txt güncellemesi (`llms-yeni.txt`)

Mevcut llms.txt'ye "Makine-okunur veri uç noktaları" bölümü eklenmiş hali.
**Bağlama:** içeriğini kök `llms.txt`'ye kopyalayın (üzerine yazın). Ajanlar
veri dosyalarının yerini (ticker.json, site-arama.json, piyasa-serit.json)
buradan öğrenir.

## Ne zaman bağlanır?

Chrome Origin Trial kalıcı sürüme çıktığında veya ajan trafiği ölçülebilir
hale geldiğinde. 1. katman (HTML'e pişirme) zaten yayında; bu paket onun
üstüne ince katmandır.
