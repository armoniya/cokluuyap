"""
uyap_core.teblig_212.gonder — T.K.21/2 şerhli yeniden tebliğ talebi:
prepare (salt-okunur) / finalize (ücretli GÖNDERİM) — canlı oturum, TARAYICISIZ.
==============================================================================
İstek zinciri, 2026-08-13 tarihli GERÇEK bir gönderimin ham ağ kaydından
(Panel/modules/logger_data/ag_kaydi.log + kayitlar.jsonl, dosya 2026/98353)
BİREBİR çıkarıldı:

  1. search_phrase_detayli.ajx (mts.sgk.dosya_id_bul) → dosyaId
  2. dosya_borclu_list.ajx (mts.sgk.borclular + borclu_sec) → kisiKurumId
  3. list_dosya_evraklar.ajx → "Avukat Portal Tebligat Talebi" türünde evrak
     VARSA talep ZATEN gönderilmiş demektir — bu kontrol TARAMA anında değil,
     her gönderim denemesinde TAZE yapılır (çift ödemeyi engeller).
  4. getIcraTalepEvrakHazirla.uyap (dosyaId+filename[=dosyaNo, ör. "2026/98353"]
     +kisiKurumId+talepBilgileri JSON) → UYAP SUNUCU TARAFINDA doldurulmuş UDF
     metni döner (istemci hiçbir alan doldurmaz — yakalanan yanıt ile giden
     sign_udf isteğinin gövdesi BİREBİR aynıydı).
  5. Bu UDF, mts/ipotek/xml_takip akışlarıyla AYNI şekilde işleyici içinde
     doğrudan udf_signer.sign_document(...) ile (HTTP sign_udf köprüsü ÜZERİNDEN
     DEĞİL) e-imzalanır.
  6. avukatIcraTalepEvrakiGonder.ajx'e multipart POST (odemeTipi=7,
     e-Barobirlik Kart — bkz. ODEME_TIPI_E_BAROBIRLIK; bu alan İLK
     sürümde atlanmıştı, bkz. altındaki not) — burada gerçek ücret
     (MASRAF_TL) UYAP bakiyesinden düşer. Yanıt {"type":"success",...}
     değilse hata sayılır.

GÜVENİLİRLİK NOTU: "masraf": 265 ve dosyaId'nin adım 4/6'da AYNEN (tırnak
karakterleri dahil) tekrar kullanılması, TEK bir yakalanan örneğe dayanıyor.
İlk canlı testte UYAP'ın gerçekten 265 TL istediği ve dosyaId'nin bu haliyle
kabul edildiği doğrulanmalı — adım 4 beklenmeyen bir yanıt döndürürse (aşağıda
"<template" kontrolü) gönderim adım 6'ya HİÇ İLERLEMEZ, ücret harcanmaz.
"""

import json
import time
import asyncio

from ..mts import sgk

# Avukat portalından atılan HER talep bu türde evrak üretir (dosya açılışındaki
# otomatik "Kapalı Tebligat" bu türde DEĞİL) — bkz. Panel/modules/barkod_sorgu.py
# TALEP_EVRAK_TUR (aynı sabit, iki tarafta da BİREBİR aynı string olmalı).
TALEP_EVRAK_TUR = "Avukat Portal Tebligat Talebi"

TALEP_TUR_KODU = "ICR_AVUKAT_PORTAL_TEBLIGAT_TALEP"
MASRAF_TL = 265

# getPortalAvukatTalepTebligatTuruList_brd.ajx CANLI yanıtından (kullanıcı
# paylaşımı, 2026-08-17) — "tebligatTuru" alanının (bkz. _talep_bilgileri_json)
# kabul ettiği TÜM değerler ve UYAP'ın o an bildirdiği ücretleri. "T.K.21/2
# Şerhli"=2/265 TL bu listeyle BİREBİR örtüşüyor (zaten kodda vardı) — bu,
# "tebligatTuru" alanının gerçekten bu referans listesindeki postaMasraflariId
# olduğunu DOĞRULAR. Ücretler UYAP'ta değişebilir; burada yalnız ön
# bilgilendirme için MASRAF_TL yerine kullanılır, gerçek tutar HER ZAMAN UYAP
# yanıtından/finalize'ın kendi akışından gelir.
TEBLIGAT_TURU_KODLARI = {
    "e_tebligat": {"id": 0, "aciklama": "E-Tebligat", "masraf": 20},
    "normal": {"id": 1, "aciklama": "Normal Tebligat", "masraf": 265},
    "t212": {"id": 2, "aciklama": "T.K.21/2 Şerhli", "masraf": 265},
    "hizli": {"id": 3, "aciklama": "Hızlı Tebligat", "masraf": 530},
    "t212_hizli": {"id": 4, "aciklama": "T.K.21/2 Şerhli Hızlı", "masraf": 530},
    "m35": {"id": 5, "aciklama": "35. Maddeye Göre Tebligat", "masraf": 265},
    "m35_hizli": {"id": 6, "aciklama": "35. Maddeye Göre Hızlı Tebligat", "masraf": 530},
}

# Kullanıcı bulgusu (2026-08-14): ham ağ kaydındaki (ag_kaydi.log satır 1990)
# 'odemeTipi' alanı İLK OKUMADA gözden kaçmış — bu alan olmadan UYAP isteği
# başka bir ödeme yöntemine yönlendiriyor ("Sistemde geçici bakım yapılması
# nedeniyle bu ödeme yöntemi kullanılmamaktadır" hatası). "7" = e-Barobirlik
# Kart (gerçek gönderimde kullanılan, canlı doğrulanmış değer — bkz. ayrıca
# job_handlers.py xml_takip_ac docstring'i: "7 (e-Barobirlik Kart, varsayılan)
# | 4 (Vakıfbank)").
ODEME_TIPI_E_BAROBIRLIK = "7"


async def _post_json(ctx, endpoint, payload):
    resp = await ctx.uyap("POST", endpoint, json=payload)
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{endpoint}' HTTP {resp.status_code} döndürdü.")
    try:
        return json.loads(resp.text)
    except Exception:
        return resp.text


async def _evrak_listesi(ctx, dosya_id):
    """dosya_core.evrak_listesi_getir (Panel tarafı) ile AYNI mantık, ctx.uyap üzerinden."""
    toplam = await _post_json(ctx, "listDosyaEvraklarPageTotal.ajx",
                               {"dosyaId": dosya_id, "pageNumber": 1})
    try:
        toplam_sayfa = max(int(toplam), 1)
    except (TypeError, ValueError):
        toplam_sayfa = 1
    tum = []
    for sayfa in range(1, toplam_sayfa + 1):
        veri = await _post_json(ctx, "list_dosya_evraklar.ajx",
                                 {"dosyaId": dosya_id, "pageNumber": sayfa})
        gruplar = veri.get("tumEvraklar") if isinstance(veri, dict) else None
        if not isinstance(gruplar, dict):
            continue
        for grup in gruplar.values():
            if isinstance(grup, list):
                tum.extend(e for e in grup if isinstance(e, dict))
    return tum


def _talep_bilgileri_json(tebligat_turu_id=2, tebligat_turu_aciklama="T.K.21/2 Şerhli",
                          masraf=MASRAF_TL):
    """ag_kaydi.log / kayitlar.jsonl'dan BİREBİR — dosyaya özgü hiçbir alan
    içermiyor (UYAP dosyaId/kisiKurumId üzerinden kendi tarafında doldurur).
    Varsayılanlar (2/"T.K.21/2 Şerhli") ORİJİNAL, canlı doğrulanmış değerlerdir
    — parametresiz çağrı ESKİ davranışla BİREBİR aynıdır. tebligat_turu_id,
    TEBLIGAT_TURU_KODLARI'ndaki "id" alanına karşılık gelir."""
    return json.dumps([{
        "grupKodu": 1, "talepKodu": 8,
        "talepAdi": "İcra/Ödeme Emrinin Tebliğe Çıkartılması",
        "talepKisaAdi": "İcra/Ödeme Emrinin Tebliğe Çıkartılması",
        "talepMasrafi": 0, "className": "AvukatTalepTebligatOdemeEmriDVO",
        "postaMasrafId": 0, "dosyaDurum": "A",
        "tebligatTuruAciklama": tebligat_turu_aciklama, "tebligatTuru": tebligat_turu_id,
        "adresTuru": 1, "adresTuruAciklama": "Mernis Adresi", "adres": "VAR",
        "masraf": masraf,
        "fields": [{"id": "tebligatTuruAciklama", "title": "Tebligat Türü"},
                   {"id": "adresTuruAciklama", "title": "Adres Türü"},
                   {"id": "adres", "title": "Adres"}],
        "id": 0,
    }], ensure_ascii=False)


async def prepare(ctx, kalem, tebligat_turu_id=2, tebligat_turu_aciklama="T.K.21/2 Şerhli",
                  masraf=MASRAF_TL):
    """SALT-OKUNUR. `kalem`: {"birim":..., "dosyaNo":"2026/98353", "borclu":...}
    (Panel rapor penceresinden — bkz. teblig_21_2_core.iade_tarama sonuç şekli).
    Döner: (ozet, state). ozet["kategori"]=="zaten_gonderilmis" ise finalize
    ÇAĞRILMAMALI (çağıran onay bile istemeden atlamalı — ödeme YAPILMAZ).

    tebligat_turu_id/aciklama/masraf: varsayılan (2/"T.K.21/2 Şerhli"/265),
    ORİJİNAL 21/2 akışıyla BİREBİR aynı — normal_tebligat_gonder (bkz.
    job_handlers.py) bunları "Normal Tebligat" (1) için AÇIKÇA geçirir."""
    dosya_no = str(kalem.get("dosyaNo") or "").strip()
    borclu_adi = str(kalem.get("borclu") or "").strip()
    birim = str(kalem.get("birim") or "").strip()
    if "/" not in dosya_no:
        raise ValueError(f"Geçersiz dosya no: {dosya_no!r} (beklenen biçim: 'YIL/SIRA').")
    yil, sira = dosya_no.split("/", 1)

    dosya_id, _gercek_no = await sgk.dosya_id_bul(ctx, yil.strip(), sira.strip())
    if not dosya_id:
        raise ValueError(f"{dosya_no}: dosya UYAP'ta bulunamadı (birim: {birim}).")

    borclular = await sgk.borclular(ctx, dosya_id)
    if not borclular:
        raise ValueError(f"{dosya_no}: borçlu listesi boş döndü.")
    secilen, eslesti = sgk.borclu_sec(borclular, borclu_adi)
    if not secilen or not secilen.get("kisiKurumId"):
        raise ValueError(f"{dosya_no}: borçlu '{borclu_adi}' UYAP'ta eşleşmedi.")
    if not eslesti:
        ctx.log(f"[{dosya_no}] ⚠️ Borçlu adı tam eşleşmedi, tek borçlu varsayıldı: "
                f"{secilen.get('adi')} {secilen.get('soyadi')}.")

    evraklar = await _evrak_listesi(ctx, dosya_id)
    zaten_var = any(e.get("tur") == TALEP_EVRAK_TUR for e in evraklar)

    borclu_gorunen = f"{secilen.get('adi', '')} {secilen.get('soyadi', '')}".strip() or borclu_adi
    ozet = {
        "dosyaNo": dosya_no,
        "birim": birim,
        "borclu": borclu_gorunen,
        "masraf": masraf,
        "tebligatTuru": tebligat_turu_aciklama,
        "kategori": "zaten_gonderilmis" if zaten_var else "hazir",
    }
    state = {"dosya_id": dosya_id, "dosya_no": dosya_no, "kisi_kurum_id": secilen["kisiKurumId"],
             "tebligat_turu_id": tebligat_turu_id, "tebligat_turu_aciklama": tebligat_turu_aciklama,
             "masraf": masraf}
    return ozet, state


async def finalize(ctx, state):
    """Yalnızca kullanıcı onay ekranını (prepare'ın ozet'i) ONAYLADIKTAN SONRA
    çağrılmalı. Bu fonksiyon GERÇEK PARA HARCAR (state["masraf"], UYAP
    bakiyesinden — prepare()'a hangi tebligat_turu_id/masraf verildiyse state
    onu taşır, finalize kendi başına VARSAYILAN kullanmaz)."""
    from .. import uyap_proxy, udf_signer

    gw = uyap_proxy.gw
    if gw is None:
        raise RuntimeError("UYAP oturumu hazır değil (gw=None).")

    dosya_id = state["dosya_id"]
    dosya_no = state["dosya_no"]
    kisi_kurum_id = state["kisi_kurum_id"]
    tebligat_turu_id = state.get("tebligat_turu_id", 2)
    tebligat_turu_aciklama = state.get("tebligat_turu_aciklama", "T.K.21/2 Şerhli")
    masraf = state.get("masraf", MASRAF_TL)
    talep_bilgileri = _talep_bilgileri_json(tebligat_turu_id, tebligat_turu_aciklama, masraf)

    ctx.log(f"[{dosya_no}] Talep evrakı UYAP'tan isteniyor ({tebligat_turu_aciklama})...")
    resp = await ctx.uyap("POST", "getIcraTalepEvrakHazirla.uyap", json={
        "dosyaId": dosya_id, "filename": dosya_no,
        "kisiKurumId": kisi_kurum_id, "talepBilgileri": talep_bilgileri,
    })
    if resp.status_code >= 400:
        raise ValueError(f"{dosya_no}: talep evrakı hazırlanamadı (HTTP {resp.status_code}).")
    udf_metin = resp.text
    if "<template" not in udf_metin or "<data>" not in udf_metin:
        raise ValueError(f"{dosya_no}: UYAP beklenmeyen bir yanıt döndürdü (UDF şablonu "
                          "değil) — gönderim GÜVENLİK için durduruldu, ücret harcanmadı. "
                          f"Yanıt (ilk 200 karakter): {udf_metin[:200]!r}")
    udf_bytes = udf_metin.encode("utf-8")

    guvenli_isim = f"{dosya_no.replace('/', '_')}_Tebligat_Talebi.udf"
    cert_id = getattr(gw, "cert_id", None)
    pin = getattr(getattr(gw, "login_args", None), "pin", None)
    ctx.log(f"[{dosya_no}] E-imzalanıyor (headless)...")
    loop = asyncio.get_running_loop()
    signed = await loop.run_in_executor(
        None, udf_signer.sign_document, udf_bytes, guvenli_isim, cert_id, pin)
    ctx.log(f"[{dosya_no}] İmzalandı ({len(signed)} bayt).")

    imzali_isim = guvenli_isim[:-4] + "_imzali.udf"
    file_id = 1
    item = {
        "id": int(time.time() * 1000),
        "evrakTuruOptionDVO": {
            "tur": TALEP_TUR_KODU, "label": "Tebligat Talebi", "mandatory": True, "max": 1,
            "ekEvrakMax": 2, "sablonTurKodu": "",
            "acceptTypeList": [".udf", ".UDF"],
            "ekEvrakAcceptTypeList": [".udf", ".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
                                       ".UDF", ".PDF", ".JPG", ".JPEG", ".PNG", ".TIF", ".TIFF"],
        },
        "tur": TALEP_TUR_KODU, "turAciklama": "Tebligat Talebi", "mandatory": True, "file": {},
        "path": f"C:/fakepath/{imzali_isim}", "kullaniciEvrakAciklama": "",
        "parentId": -1, "isVekalet": False, "isVekaletBilgiFormu": False, "fileId": file_id,
    }
    items_json = json.dumps([item], ensure_ascii=False)

    files = {
        f"file_{file_id}": (imzali_isim, signed, "application/octet-stream"),
        "items": (None, items_json),
        "dosyaId": (None, dosya_id),
        "kisiKurumId": (None, str(kisi_kurum_id)),
        "tutar": (None, str(masraf)),
        "talepGrupId": (None, "1"),
        "talepBilgileri": (None, talep_bilgileri),
        "vakifbankHesapBilgileri": (None, "null"),
        "vakifbankOdemeIstekBilgileri": (None, "null"),
        "smsSifre": (None, "null"),
        "odemeTipi": (None, ODEME_TIPI_E_BAROBIRLIK),
    }

    ctx.log(f"[{dosya_no}] Talep gönderiliyor ({tebligat_turu_aciklama}) — {masraf} TL düşülecek...")
    ev_resp = await ctx.uyap("POST", "avukatIcraTalepEvrakiGonder.ajx", files=files)
    try:
        sonuc = json.loads(ev_resp.text) if ev_resp.text else {}
    except Exception:
        sonuc = {"_ham": ev_resp.text}
    if sonuc.get("type") != "success":
        raise ValueError(f"{dosya_no}: gönderim UYAP tarafından reddedildi: {sonuc}")
    ctx.log(f"[{dosya_no}] ✅ Gönderildi. {sonuc.get('message', '')}")
    return {"evrak_sonuc": sonuc}
