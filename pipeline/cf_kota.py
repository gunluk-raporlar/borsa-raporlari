"""Workers AI günlük neuron tüketimini GraphQL Analytics'ten okur.

Dataset: aiInferenceAdaptiveGroups (hesap düzeyi Workers AI çıkarım kayıtları,
sum.neurons alanı — gömme + yeniden sıralama + metin üretimi TÜM modellerin
toplamı, kota sayacının kendisi budur). Ücretsiz plan 10.000 neuron/gün;
sayaç 00:00 UTC'de (03:00 TSİ) sıfırlanır.

Her hata adımında (None, açıklama) döner — çağıran taraf fail-open karar
verir; bu modül görevi asla bloklamaz.

Kullanıcı kararı 2026-11-05: eval yargıcı öncesi kota kontrolü ve günlük
kota notu. Gerekçe: 2026-11-03'te aynı gün 6 eval koşusu kotayı tüketip
sitenin anlam aramasını reset'e kadar 429'a düşürmüştü (commit 9eab270be).
"""
import os
from datetime import datetime, timezone

# 10k kotanın 1k altı güvenlik payı (arama gömmeleri + reranker yaşar).
GUNLUK_LIMIT = int(os.environ.get("EVAL_KOTA_LIMIT", "9000"))
# Yargıç koşusunun ölçülmüş maliyeti ~2.9k neuron (2026-11-03: 6 koşu ≈ 17.5k)
# + pay. Ne olur ne olmaz payı: boş kota bile aramaları korur.
KOSU_MALIYETI = int(os.environ.get("EVAL_KOTA_MALIYET", "4000"))


def bugun_neuron():
    """Bugün (UTC günü) harcanan neuron toplamı → (değer, açıklama).

    Değer None ise okuma başarısızdır; açıklama nedeni taşır. Boş gün 0.0
    döner (satır yoksa kota kullanılmamış demektir).
    """
    hesap = os.environ.get("CF_ACCOUNT_ID", "")
    anahtar = os.environ.get("CF_API_KEY", "")
    if not (hesap and anahtar):
        return None, "CF secret yok"
    bas = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sorgu = {
        "query": (
            'query { viewer { accounts(filter: {accountTag: "%s"}) { '
            "aiInferenceAdaptiveGroups(limit: 1, "
            'filter: {datetime_geq: "%s"}) { sum { neurons } } } } }'
            % (hesap, bas.strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
    }
    try:
        import requests

        r = requests.post(
            "https://api.cloudflare.com/client/v4/graphql",
            headers={
                "Authorization": f"Bearer {anahtar}",
                "Content-Type": "application/json",
            },
            json=sorgu,
            timeout=30,
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        veri = r.json()
        hatalar = veri.get("errors") or []
        if hatalar:
            return None, ("graphql: " + str(hatalar[0].get("message", "")))[:120]
        gruplar = (
            veri.get("data", {})
            .get("viewer", {})
            .get("accounts", [{}])[0]
            .get("aiInferenceAdaptiveGroups", [])
        )
        if not gruplar:
            return 0.0, "ok"
        return float(gruplar[0]["sum"]["neurons"]), "ok"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:120]
