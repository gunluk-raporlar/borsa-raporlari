# -*- coding: utf-8 -*-
"""GIZLI kalite-yargici: uretilen derin/makro analizleri rubrikle puanlar.

Kurallar (2026-10-19, kullanici karari):
- Siteye/akisa DOKUNMAZ: ciktilar kalite/ altinda; kalite/ .gitignore'da VE
  build.sh kopya setinde olmadigindan siteye cikmaz.
- Kalicilik: private ops deposuna (OPS_REPO_TOKEN) cek/ite senkronu —
  derin-analiz/makro-analiz koşuları boş runner'da baslar; skorlar.jsonl
  once ops deposundan geri cekilir, puanlama sonrası itilir. Token yoksa
  tekil mod (yalnız yerel yazım, geçmiş karşılaştırması boş kalır).
- Uretici sayaçlarını kirletmez: bot._LLM_SON_BASARI / _LLM_TOKEN_SAYAC /
  _LLM_KOSU_TOKEN / _LLM_YAZARLAR icerik-kopya yedeklenip geri yuklenir
  (kullanim_defteri kayıtları bozulmaz). Yargıcı kayitlardan SONRA koşulur;
  bu restore yalnızca savunma katmanıdır.
- Yargic rotasyonu DISC'siz: DISC = uretici (deepseek-v4-flash); kendi
  cıktısını yargılamaması için (oz-yargı biasi) yargic sirasi AMD→ZAI→NVID→OR.
- Fail-open: her hata log'a düşer; uretim asla etkilenmez.
- Regresyon mantigi: yargıc puanı kosudan kosuya ±1-2 oynar; tek kosu altına
  düşmek alarm degil, "son 2 koşu da 7-koşu temelinin 1.0 puan altında" alarm.
  Temel yalnız AYNI prompt_versiyonu + AYNI yargıc modelden kurulur
  (prompt/model degisimi kalite dususu sanılmasın).
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("kalite-yargic")

KALITE_DIR = Path("kalite")
SKORLAR_YOLU = KALITE_DIR / "skorlar.jsonl"
RAPORLAR_DIR = KALITE_DIR / "raporlar"
OPS_REPO = os.environ.get("OPS_REPO", "gunluk-raporlar/borsa-raporlari-ops")

PROMPT_VERSIYONU = "2026-10-19-v1"
YARGIC_SIRASI = ("AMD", "ZAI", "NVID", "OR")   # DISC = uretici; yargicta YOK
MAX_DENEME = 3
METIN_SINIR = 24000
REGRESYON_TEMEL = 7      # temel: son 7 kosu (ayni prompt_versiyonu + yargıc)
REGRESYON_FARK = 1.0
REGRESYON_ART_ARD = 2    # arka arkaya 2 kosu altinda → alarm

RUBRIK = (
    "- veri_dayanak: her sayı/iddia rapora verilen veriye dayanıyor mu; "
    "desteksiz/uydurma rakam veya kurum-kişi iddiası var mı\n"
    "- derinlik: yüzeysel haber tekrarı mı, yoksa neden-sonuç ve piyasa "
    "mekanizması anlatıyor mu\n"
    "- tutarliilik: iç çelişki yok; aynı gösterge farklı bölümlerde aynı\n"
    "- eyleme_donusluk: yatırımcı için net çıkarım/senaryo/izlenecek varlık "
    "veriyor mu\n"
    "- dil_kalitesi: Türkçe doğruluğu, akıcılık, bölüm yapısı"
)

_GERKEKLI_JSON_ANAHTARLARI = {"skorlar", "genel", "zayiflar", "ozet"}

# bot modulune GEÇ referans: testler buraya yalancı modül takabilir;
# üretimde ilk kullanımda gerçek bot yüklenir (modül açılışında değil —
# kalite_yargic bot importu anahtar zorunluluğu olan bir modüldür).
bot_modul = None


def _bot_yukle():
    global bot_modul
    if bot_modul is None:
        import bot
        bot_modul = bot
    return bot_modul


def _json_ayikla(yanit):
    """Model yanıtından JSON'u gevşekce ayıklar (fence, on/sonda metin)."""
    metin = (yanit or "").strip()
    if "```" in metin:
        parcalar = metin.split("```")
        for parca in parcalar:
            p = parca.strip()
            if p.startswith("json"):
                p = p[4:]
            if p.startswith("{"):
                metin = p
                break
    bas = metin.find("{")
    son = metin.rfind("}")
    if bas < 0 or son <= bas:
        raise ValueError("JSON bulosu yok")
    veri = json.loads(metin[bas:son + 1])
    if not isinstance(veri, dict) or not _GERKEKLI_JSON_ANAHTARLARI.issubset(veri):
        raise ValueError("JSON sema eksik")
    if not isinstance(veri.get("skorlar"), dict):
        raise ValueError("skorlar dict degil")
    return veri


def _json_dogrulama(yanit):
    """llm_call dogrulama callable'i: JSON bozuksa siradaki modele gecer."""
    try:
        _json_ayikla(yanit)
        return True
    except Exception:
        return False


def _yargic_promptu(tur, metin):
    return (
        "Sen bağımsız bir finansal-analiz kalite denetçisisin; raporu yazan "
        "modeli bilmiyorsun ve onun tarafını tutmuyorsun. Aşağıda bir "
        f"{tur} analiz raporu metni veriliyor. Raporu şu beş kriterde "
        "0-10 puanla (10 = kusursuz; 6 = kabul eşiği):\n"
        f"{RUBRIK}\n\n"
        "Sıkı ol: uydurma rakam, verisiz güçlü iddia veya iç çelişki "
        "görürsen puanı kır; övgü için puan verme. Zayıflıkları SOMUT ve "
        "kısa maddeler halinde yaz (en fazla 5 madde).\n\n"
        "YALNIZCA şu JSON'u döndür, başka hiçbir metin yazma:\n"
        '{"skorlar":{"veri_dayanak":0-10,"derinlik":0-10,"tutarliilik":0-10,'
        '"eyleme_donusluk":0-10,"dil_kalitesi":0-10},'
        '"genel":0-10,"zayiflar":["..."],"ozet":"1-2 cumle"}\n\n'
        "<<<RAPOR\n" + metin[:METIN_SINIR] + "\nRAPOR>>>"
    )


def _sayac_yedekle_ve_geri_yukle():
    """Yargıc llm_call'i bot'un defter sayaçlarını kirletmesin (içerik kopya).

    Mutasyonlar yerinde (update/+=) oldugundan modulu yeniden baglamak yetmez;
    icerik kopyasiyla geri yuklemek gerekir. Tum mutasyon noktaları cagri
    anında bot modulunun global adlarını arar — bot uzerinden calisir.
    """
    bot = _bot_yukle()

    sinif = type("_Yedek", (), {})

    def yedekle():
        for ad in ("_LLM_SON_BASARI", "_LLM_TOKEN_SAYAC", "_LLM_YAZARLAR",
                   "_LLM_KOSU_TOKEN"):
            setattr(sinif, ad, dict(getattr(bot, ad)))
        sinif._etkili = True

    def geri_yukle():
        if not getattr(sinif, "_etkili", False):
            return
        for ad in ("_LLM_SON_BASARI", "_LLM_TOKEN_SAYAC", "_LLM_YAZARLAR",
                   "_LLM_KOSU_TOKEN"):
            getattr(bot, ad).clear()
            getattr(bot, ad).update(getattr(sinif, ad))

    return yedekle, geri_yukle


def _uretici_bilgisi():
    """Yargictan ONCE okunur: raporu hangi model/yazici yazdi (bot._LLM_*)."""
    bot = _bot_yukle()

    son = dict(getattr(bot, "_LLM_SON_BASARI", {}))
    yazan = dict(getattr(bot, "_LLM_YAZARLAR", {}))
    return {"son": son, "yazan_dagilimi": yazan}


def _ops_klonla(token):
    """Private ops deposunu depth-1 klonlar; basarisizlikta None."""
    git = shutil.which("git")
    tmp = tempfile.mkdtemp(prefix="kalite-ops-")
    url = f"https://x-access-token:{token}@github.com/{OPS_REPO}.git"
    r = subprocess.run([git, "clone", "--depth", "1", url, tmp],
                       capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning("[Kalite] ops deposu klonlanamadi: %s",
                       (r.stderr or r.stdout)[-200:])
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    return tmp


def _ops_cek(tmp):
    """Klondaki kalite/ gecmisini yerel kalite/ altina geri yukler."""
    kaynak = Path(tmp) / "kalite"
    if not (kaynak / "skorlar.jsonl").exists():
        logger.info("[Kalite] ops deposunda gecmis yok — ilk koşu.")
        return
    KALITE_DIR.mkdir(parents=True, exist_ok=True)
    hedef = KALITE_DIR / "skorlar.jsonl"
    if hedef.exists():
        _jsonl_birlestir(hedef, kaynak / "skorlar.jsonl")
    else:
        shutil.copy2(kaynak / "skorlar.jsonl", hedef)
    rapor_kaynak = kaynak / "raporlar"
    if rapor_kaynak.exists():
        for f in rapor_kaynak.glob("*.md"):
            hedef_d = RAPORLAR_DIR / f.name
            if not hedef_d.exists():
                hedef_d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, hedef_d)
    logger.info("[Kalite] gecmis ops deposundan geri yuklendi (%d satır).",
                sum(1 for _ in open(hedef, encoding="utf-8")))


def _jsonl_birlestir(yerel, repo):
    """Iki skorlar.jsonl'yi (tarih,tur,saat) anahtariyla birlestirir."""
    satirlar, anahtarlar = [], set()
    for yol in (repo, yerel):
        if not yol.exists():
            continue
        for satir in yol.read_text(encoding="utf-8").splitlines():
            satir = satir.strip()
            if not satir:
                continue
            try:
                veri = json.loads(satir)
            except Exception:
                continue
            k = (veri.get("tarih"), veri.get("tur"), veri.get("saat"))
            if k in anahtarlar:
                continue
            anahtarlar.add(k)
            satirlar.append(satir)
    satirlar.sort()
    yerel.write_text("\n".join(satirlar) + ("\n" if satirlar else ""),
                     encoding="utf-8")


def _ops_ite(tmp):
    """Yerel kalite/ icerigini klona kopyalayip private depoya iter."""
    git = shutil.which("git")
    hedef = Path(tmp) / "kalite"
    hedef.mkdir(parents=True, exist_ok=True)
    shutil.copy2(KALITE_DIR / "skorlar.jsonl", hedef / "skorlar.jsonl")
    if RAPORLAR_DIR.exists():
        (hedef / "raporlar").mkdir(parents=True, exist_ok=True)
        for f in RAPORLAR_DIR.glob("*.md"):
            shutil.copy2(f, hedef / "raporlar" / f.name)
    if subprocess.run([git, "add", "-A"], cwd=tmp).returncode != 0:
        return False
    durum = subprocess.run([git, "status", "--porcelain"], cwd=tmp,
                           capture_output=True, text=True)
    if not durum.stdout.strip():
        logger.info("[Kalite] ops deposunda degisiklik yok — push atlandi.")
        return True
    for cmd in ([git, "-c", "user.name=borsa-bot",
                 "-c", "user.email=borsa-bot@users.noreply.github.com",
                 "commit", "-m", "Kalite defteri guncelleme"],
                [git, "push", "origin", "HEAD"]):
        if subprocess.run(cmd, cwd=tmp, capture_output=True, text=True).returncode != 0:
            logger.warning("[Kalite] ops push adimi basarisiz: %s", cmd[-1])
            return False
    logger.info("[Kalite] skorlar private ops deposuna islendi.")
    return True


def _gecmis_satirlari(tur, yargic_model):
    """Aynı tur + prompt_versiyonu + yargıc modelden geçmiş satırlar."""
    if not SKORLAR_YOLU.exists():
        return []
    uygun = []
    for satir in SKORLAR_YOLU.read_text(encoding="utf-8").splitlines():
        try:
            veri = json.loads(satir)
        except Exception:
            continue
        if (veri.get("tur") == tur
                and veri.get("prompt_versiyonu") == PROMPT_VERSIYONU
                and veri.get("yargic_model") == yargic_model
                and veri.get("genel") is not None):
            uygun.append(veri)
    return uygun


def _regresyon(yeni_genel, gecmis):
    """Son 2 koşu da temelin 1.0 altindaysa True (gurultuye dayanikli)."""
    if len(gecmis) < REGRESYON_TEMEL:
        return False
    temel = [v["genel"] for v in gecmis[-REGRESYON_TEMEL:]]
    esik = sum(temel) / len(temel) - REGRESYON_FARK
    son = gecmis[-1]["genel"]
    if yeni_genel < esik and son < esik:
        return True
    return False


def _rapor_yaz(tur, tarih, skor, zayiflar, ozet, uretici, yargic_bilgi,
               gecmis, regresyon_alarm):
    """Tam yargic raporunu kalite/raporlar/<tur>-<tarih>.md olarak yazar."""
    RAPORLAR_DIR.mkdir(parents=True, exist_ok=True)
    yolu = RAPORLAR_DIR / f"{tur}-{tarih}.md"
    egilim = [v.get("genel") for v in gecmis[-REGRESYON_TEMEL:]]
    satirlar = [
        f"# Kalite yargıç raporu — {tur} / {tarih}",
        "",
        f"- Genel skor: **{skor}** (rubrik v{PROMPT_VERSIYONU})",
        f"- Yargıç: {yargic_bilgi.get('model', '?')} "
        f"({yargic_bilgi.get('saglayici', '?')})",
        f"- Üretici: {json.dumps(uretici.get('son') or {}, ensure_ascii=False)}",
        f"- Regresyon alarmı: {'EVET' if regresyon_alarm else 'hayır'}",
        f"- Önceki {len(egilim)} koşunun genel skoru: {egilim}",
        "",
        "## Zayıflıklar",
    ]
    satirlar += [f"- {z}" for z in zayiflar] or ["- (yok)"]
    satirlar += ["", "## Yargıç özeti", ozet or "", ""]
    yolu.write_text("\n".join(satirlar), encoding="utf-8")
    return yolu


def degerlendir(tur, tarih, metin):
    """Uretimden SONRA cagrilir: puanlar, kalite/ altina yazar, ops'a ittirir.

    Fail-open: her istisna cagiran tarafa dusmez (kanca zaten try/except'li)
    ve uretime dokunmaz. Metin yoksa sessizce cikar.
    """
    if not metin or not str(metin).strip():
        logger.info("[Kalite] rapor metni yok — yargıcı atlandı.")
        return None

    uretici = _uretici_bilgisi()   # yargic sayaçları ezip kaybetmeden ONCE
    token = os.environ.get("OPS_REPO_TOKEN", "").strip()
    tmp = _ops_klonla(token) if token else None
    if token and not tmp:
        logger.warning("[Kalite] ops cekme basarisiz — tekil moda düşüldü.")
    elif not token:
        logger.info("[Kalite] OPS_REPO_TOKEN yok — tekil mod (geçmiş çekilmez).")
    try:
        if tmp:
            _ops_cek(tmp)

        yedekle, geri_yukle = _sayac_yedekle_ve_geri_yukle()
        yedekle()
        try:
            bot = _bot_yukle()
            yanit = bot.llm_call(
                _yargic_promptu(tur, str(metin)),
                max_deneme=MAX_DENEME,
                fallback_on_fail=True,
                sirasi=YARGIC_SIRASI,
                dogrulama=_json_dogrulama,
            )
            yargic_bilgi = dict(getattr(bot, "_LLM_SON_BASARI", {}))
        finally:
            geri_yukle()

        if not yanit:
            logger.warning("[Kalite] yargic yanit vermedi — kayit yazilmadi.")
            return None
        try:
            veri = _json_ayikla(yanit)
        except Exception:
            logger.warning("[Kalite] yargic JSON'u bozuk — kayit yazilmadi: %s",
                           (yanit or "")[:120])
            return None

        skorlar = {k: round(float(v), 1)
                   for k, v in veri["skorlar"].items()
                   if isinstance(v, (int, float))}
        genel = round(float(veri["genel"]), 1) if isinstance(
            veri.get("genel"), (int, float)) else (
            round(sum(skorlar.values()) / len(skorlar), 1) if skorlar else None)
        zayiflar = [str(z) for z in (veri.get("zayiflar") or [])][:8]
        ozet = str(veri.get("ozet") or "")[:400]

        gecmis = _gecmis_satirlari(tur, yargic_bilgi.get("model"))
        alarm = genel is not None and _regresyon(genel, gecmis)

        tz = ZoneInfo("Europe/Istanbul")
        simdi = datetime.now(tz)
        satir = {
            "tarih": tarih, "saat": simdi.strftime("%H:%M"), "tur": tur,
            "prompt_versiyonu": PROMPT_VERSIYONU,
            "uretici": uretici, "yargic_model": yargic_bilgi.get("model"),
            "skorlar": skorlar, "genel": genel,
            "zayiflar": zayiflar, "ozet": ozet,
            "regresyon": alarm,
        }
        KALITE_DIR.mkdir(parents=True, exist_ok=True)
        with open(SKORLAR_YOLU, "a", encoding="utf-8") as f:
            f.write(json.dumps(satir, ensure_ascii=False) + "\n")

        yolu = _rapor_yaz(tur, tarih, genel, zayiflar, ozet, uretici,
                          yargic_bilgi, gecmis, alarm)
        logger.info("[Kalite] %s/%s puanlandı: genel=%.1f%s | rapor: %s",
                    tur, tarih, genel if genel is not None else -1,
                    " REGRESYON ALARMI" if alarm else "", yolu)

        if tmp and _ops_ite(tmp):
            pass
        return satir
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------ birim test (ag cagrisiz) ------------------------

def _test():
    """--test: stub LLM ile JSON ayristirma + sayaç restore + defter yazimi."""
    import importlib
    import types

    ky = importlib.import_module("kalite_yargic")

    bot_yalani = types.ModuleType("bot")
    bot_yalani._LLM_SON_BASARI = {"saglayici": "AMD", "model": "DeepSeek-V4-Flash",
                                  "deneme": 2}
    bot_yalani._LLM_TOKEN_SAYAC = {"in": 1234, "out": 5678}
    bot_yalani._LLM_YAZARLAR = {"AMD/DeepSeek-V4-Flash": 9}
    bot_yalani._LLM_KOSU_TOKEN = {"in": 9000, "out": 11000}
    j = 0

    def sahte_llm_call(prompt, **kw):
        nonlocal j
        j += 1
        kw.get("dogrulama")
        if j == 1:
            # ilk deneme bozuk JSON donsun (dogrulama callback'i test edilsin)
            assert kw.get("dogrulama")("garanti-benek") is False
            return ('```json\n{"skorlar":{"veri_dayanak":7,"derinlik":6,'
                    '"tutarliilik":8,"eyleme_donusluk":6,"dil_kalitesi":9},'
                    '"genel":7.2,"zayiflar":["Faiz yolunda verisiz iddia"],'
                    '"ozet":"Orta-iyi analiz."}\n```')
        return ('{"skorlar":{"veri_dayanak":6,"derinlik":6,"tutarliilik":7,'
                '"eyleme_donusluk":5,"dil_kalitesi":8},"genel":6.4,'
                '"zayiflar":["Derinlik siniri"],"ozet":"Gecmis koşuya karsi hafif dusus."}')

    bot_yalani.llm_call = sahte_llm_call
    ky.bot_modul = bot_yalani   # yargıcı bu yalancı bot modülüne bağla

    eski_dir, eski_skor = ky.KALITE_DIR, ky.SKORLAR_YOLU
    ky.KALITE_DIR = Path("kalite-test")
    ky.SKORLAR_YOLU = ky.KALITE_DIR / "skorlar.jsonl"
    eski_rapor = ky.RAPORLAR_DIR
    ky.RAPORLAR_DIR = ky.KALITE_DIR / "raporlar"
    try:
        before = {ad: dict(getattr(bot_yalani, ad)) for ad in
                  ("_LLM_SON_BASARI", "_LLM_TOKEN_SAYAC", "_LLM_YAZARLAR",
                   "_LLM_KOSU_TOKEN")}
        satir = ky.degerlendir("test", "2000-01-01", "Test rapor metni.")
        assert satir and satir["genel"] == 7.2, satir
        after = {ad: dict(getattr(bot_yalani, ad)) for ad in before}
        assert before == after, f"sayaç restore BOZUK: {before} != {after}"
        assert (ky.KALITE_DIR / "raporlar" / "test-2000-01-01.md").exists()
        # ikinci koşu: gecmis satirla birlestirme + dogrulama yolu
        satir2 = ky.degerlendir("test", "2000-01-02", "Ikinci test metni.")
        assert satir2 and satir2["genel"] == 6.4
        satir_sayisi = len(ky.SKORLAR_YOLU.read_text(encoding="utf-8").strip().splitlines())
        assert satir_sayisi == 2, satir_sayisi
        print("KALITE_YARGIC TEST TAMAM: JSON ayristirma, sayaç restore, "
              "defter + rapor yazimi OK (2 satir).")
    finally:
        shutil.rmtree(ky.KALITE_DIR, ignore_errors=True)
        ky.KALITE_DIR, ky.SKORLAR_YOLU, ky.RAPORLAR_DIR = (
            eski_dir, eski_skor, eski_rapor)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_test() if "--test" in sys.argv else 0)
