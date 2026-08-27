# -*- coding: utf-8 -*-
"""
Toplu takip açma sonrası veritabanı kaydı
==========================================
Bir takip UYAP'ta GERÇEKTEN açılıp gerçek esas no ("2026/894734" gibi, bkz.
uyap_core.esas_no) doğrulandıktan SONRA çağrılır. search_phrase_detayli.ajx —
Panel/modules/icra_core.py'nin normal "Dosyalarım" senkronunda ZATEN kullandığı
AYNI, kanıtlı sorgu — ile o dosyanın TAM kapak künyesini yeniden çeker ve
models/icra_models/ingest.py'nin AYNI upsert fonksiyonuna (dosya_kunyesi_kaydet)
verir. Böylece UYAP alan adları (birimId/birimTuru1-3/dosyaTurKod/...) burada
TEKRAR TAHMİN EDİLMEZ — veri modeli değişirse tek yer (ingest.py) güncellenir.

ALACAKLI/BORÇLU (Taraf+DosyaTaraf) da BURADA yazılır — takip verisinden zaten
BİLİNDİĞİ için (XML/İpotek/MTS, ekstra sorgu GEREKMEZ). İlk sürüm bunu "bir
sonraki normal 'Dosyalarım' taraması tamamlar" varsayımıyla atlıyordu — YANLIŞ
çıktı (kullanıcı bulgusu, 2026-08-14): icra_core.py'nin normal senkronunda
ALACAKLI için KANITLI bir UYAP uç noktası hiç YOK (bkz. icra_core.py'deki
"'dosya_alacakli_list.ajx' diye bir UYAP endpoint'i YOK" notu) — yani alacaklı
asla otomatik dolmayacaktı, "İcra Dosyalarım" sütunu SÜRESİZ boş kalırdı.
Taraf upsert deseni Panel/modules/icra_core.save_taraf ile AYNI (tckn/mersis_no
ile TEKİLLEŞTİRİR, aynı kişi tekrar yazılmaz) — o modül tkinter/SGK bağımlılık
zinciri taşıdığından buraya (ofis süreci) İTHAL EDİLMEZ, küçük mantık burada
YİNELENİR (bkz. mts.takip'teki AYNI izolasyon deseni).

Hata (ağ/DB) durumunda BU FONKSİYON SESSİZCE None döner + varsa log() ile
uyarır: takip zaten UYAP'ta AÇIK, veritabanı senkronu başarısız olsa da GERÇEK
dosya kaybolmaz (sonraki normal 'Dosyalarım' taramasında yakalanır).

Django ORM senkron (blocking) olduğundan run_in_executor ile çağrılır — bkz.
xml_takip/ipotek finalize()'daki udf_signer.sign_document ile AYNI desen.
"""
import os
import sys
import asyncio
import json as _json


async def _api_json(ctx, path, payload=None):
    resp = await ctx.uyap("POST", path, json=(payload if payload is not None else {}))
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü.")
    return _json.loads(resp.text)


def _yil_sira(gercek_dosya_no):
    try:
        yil, sira = str(gercek_dosya_no).split("/", 1)
        return int(yil), int(sira)
    except (ValueError, AttributeError):
        return None, None


def _kayit_sec(veri, gercek_dosya_no):
    """search_phrase_detayli.ajx yanıtı (envelope farklı şekillerde olabilir,
    bkz. Panel/modules/icra_core.parse_records ile AYNI savunmacı çözümleme)
    içinden dosyaNo'su TAM eşleşen kaydı döner."""
    kayitlar = veri
    if isinstance(veri, dict):
        for k in ("data", "veri", "list", "rows", "dosyalar"):
            if isinstance(veri.get(k), list):
                kayitlar = veri[k]
                break
        else:
            kayitlar = [veri]
    if isinstance(kayitlar, list) and kayitlar and isinstance(kayitlar[0], list):
        kayitlar = kayitlar[0]
    if not isinstance(kayitlar, list):
        return None
    kayitlar = [k for k in kayitlar if isinstance(k, dict)]
    for k in kayitlar:
        if str(k.get("dosyaNo") or "").strip() == gercek_dosya_no:
            return k
    return kayitlar[0] if len(kayitlar) == 1 else None


def _taraf_upsert(Taraf, info):
    """Panel/modules/icra_core.save_taraf ile AYNI tekilleştirme deseni
    (tckn/mersis_no öncelikli, yoksa tur+ad+soyad / tur+unvan ile ara, hiçbiri
    yoksa yeni oluştur) — o fonksiyon BİREBİR burada yinelenir (import DEĞİL,
    bkz. modül başlığı)."""
    tckn = info.get("tckn")
    mersis_no = info.get("mersis_no")
    defaults = {
        "tur": info.get("tur") or "gercek",
        "ad": info.get("ad") or "", "soyad": info.get("soyad") or "",
        "unvan": info.get("unvan") or "", "vergi_no": info.get("vergi_no") or "",
        "mersis_no": mersis_no,
    }
    taraf = None
    if tckn:
        taraf = Taraf.objects.filter(tckn=tckn).first()
    elif mersis_no:
        taraf = Taraf.objects.filter(mersis_no=mersis_no).first()
    if not taraf:
        if defaults["tur"] == "tuzel" and defaults["unvan"]:
            taraf = Taraf.objects.filter(tur="tuzel", unvan=defaults["unvan"]).first()
        elif defaults["tur"] == "gercek" and defaults["ad"] and defaults["soyad"]:
            taraf = Taraf.objects.filter(tur="gercek", ad=defaults["ad"], soyad=defaults["soyad"]).first()
    if taraf:
        for k, v in defaults.items():
            setattr(taraf, k, v)
        taraf.tckn = tckn or taraf.tckn
        taraf.save()
        return taraf
    if tckn:
        defaults["tckn"] = tckn
    return Taraf.objects.create(**defaults)


def _django_yaz(rec, taraflar):
    here = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.normpath(os.path.join(here, "..", "..", "models"))
    if models_dir not in sys.path:
        sys.path.insert(0, models_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
    import django
    django.setup()
    from django.db import transaction
    from icra_models.ingest import dosya_kunyesi_kaydet
    from icra_models.models import Taraf, DosyaTaraf

    # yargi_turu=2 (İcra) AÇIKÇA verilir — bkz. Panel/modules/dosya_core.YARGI_TURU_ICRA.
    # xml_takip/ipotek/mts'nin AÇTIĞI HER dosya zaten kesinlikle bir icra dosyasıdır,
    # bunda BELİRSİZLİK yok. Bu verilmezse (kullanıcı bulgusu, 2026-08-14) DAHA ÖNCE
    # hiç görülmemiş bir icra dairesi için Birim.yargi_turu NULL kalıyor — Barkod
    # Sorgulama (ve yargi_turu=İcra filtreleyen her ekran) o dosyayı SESSİZCE dışlıyor,
    # yalnızca "Tüm Dosyalarım" (yargi_turu filtresi OPSİYONEL) gösteriyordu.
    dosya_obj, created = dosya_kunyesi_kaydet(rec, yargi_turu=2)

    with transaction.atomic():
        for i, (rol, info) in enumerate(taraflar or []):
            if not (info.get("tckn") or info.get("mersis_no") or info.get("ad") or info.get("unvan")):
                continue
            taraf_obj = _taraf_upsert(Taraf, info)
            DosyaTaraf.objects.update_or_create(
                dosya=dosya_obj, taraf=taraf_obj, rol=rol, defaults={"sira": i})

    return dosya_obj, created


async def kaydet(ctx, gercek_dosya_no, taraflar=None, log=None):
    """Az önce açılan takibi search_phrase_detayli.ajx ile YENİDEN sorgulayıp
    veritabanına (Birim+Dosya) upsert eder, sonra `taraflar` biliniyorsa
    (bkz. çağıran taraflardaki [(rol, info), ...] listesi — takip verisinden
    ZATEN elde, ek sorgu GEREKMEZ) alacaklı/borçlu bağlarını da (Taraf+
    DosyaTaraf) yazar. Bulunamazsa/hata olursa None döner — çağıran taraf
    bunu FATAL saymamalı (takip UYAP'ta zaten açık)."""
    yil, sira = _yil_sira(gercek_dosya_no)
    if yil is None:
        return None

    try:
        payload = {"dosyaYil": yil, "dosyaSira": sira, "dosyaDurumKod": 0,
                  "birimTuru2": "1101", "birimTuru3": "2",
                  "pageSize": 50, "pageNumber": 1}
        veri = await _api_json(ctx, "search_phrase_detayli.ajx", payload)
        rec = _kayit_sec(veri, gercek_dosya_no)
        if not rec:
            if log:
                log(f"⚠️ Veritabanı senkronu: {gercek_dosya_no} search_phrase_detayli'de "
                    "henüz bulunamadı — bir sonraki 'Dosyalarım' taramasında yakalanacak.")
            return None
    except Exception as e:
        if log:
            log(f"⚠️ Veritabanı senkronu: {gercek_dosya_no} sorgusu başarısız ({e}) — dosya "
                "UYAP'ta AÇIK, 'Dosyalarım' bir sonraki taramada yakalayacak.")
        return None

    try:
        loop = asyncio.get_running_loop()
        dosya_obj, created = await loop.run_in_executor(None, _django_yaz, rec, taraflar)
    except Exception as e:
        if log:
            log(f"⚠️ Veritabanına kaydedilemedi ({e}) — dosya UYAP'ta AÇIK, sonraki "
                "'Dosyalarım' taramasında otomatik yakalanır.")
        return None

    if log:
        log(f"✓ Veritabanına {'eklendi' if created else 'güncellendi'}: {gercek_dosya_no}"
            + (f" ({len(taraflar)} taraf)" if taraflar else ""))
    return dosya_obj
