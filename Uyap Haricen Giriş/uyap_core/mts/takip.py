"""
uyap_core.mts.takip — Tek bir MTS takibini canlı oturum üzerinden açma akışı
============================================================================
Kararlı/mts_takip_acan_api.py'deki `takip_ac` mantığı buraya, TARAYICISIZ ve
canlı bağlantı (jobs.JobContext.uyap → uyap_proxy.gw) üzerinde çalışacak şekilde
port edildi. Playwright `page.evaluate(fetch)` → `await ctx.uyap(...)`. İmzalama
F7/win32gui editörü yerine headless `udf_signer.sign_document`.

İki fazlı tasarım (üç onay modunu desteklemek için):
  • prepare(ctx, takip, ...)  → UYAP sorguları + harç hesabı (OKUMA ağırlıklı, taslak
        OLUŞTURMAZ). (ozet, state) döndürür. 'toplu' modda tüm takipler önce hazırlanır.
  • finalize(ctx, takip, state, ...) → taslak oluştur (YAZMA) + UDF indir + imzala +
        evrak gönder. Onaylanan takipler için çağrılır.

Onay akışı orkestrasyonda (job_handlers.coklu_takip_ac): prepare → (gerekiyorsa
ctx.request_approval) → finalize.
"""

import json
import re as _re
import asyncio
import urllib.parse

import httpx

from .models import (
    kalemleri_birlestir, parse_amount, format_date_with_slashes, tr_to_ascii,
)
from .evrak import items_kur, mime_belirle


class DosyaAtla(Exception):
    """Bir takip atlanmalı (kullanıcı 'atla' dedi ya da düzeltilebilir veri sorunu)."""
    pass


# Bağlantı seviyesinde geçici hatalar (kullanıcı bulgusu, 2026-08-11): UYAP'a giden TCP
# bağlantısı istek gönderildikten SONRA, yanıt henüz okunmadan kopabiliyor (ör. ofis-UYAP
# arası ağ sıçraması, ya da yeniden kullanılan keep-alive bağlantının UYAP tarafından tam
# o anda kapatılması) — httpx bunu ReadError/RemoteProtocolError olarak fırlatıyor. SORGU
# uçlarında (prepare() — hiçbir şey OLUŞTURMAZ) 1 kez otomatik tekrar denemek güvenlidir.
_TRANSIENT_ERRORS = (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError,
                     httpx.WriteError, httpx.PoolTimeout)


# ── UYAP istek yardımcıları (ctx.uyap üzerinden) ──────────────────────────────
async def _api_text(ctx, path, payload=None, multipart=False, retry=True):
    """UYAP'a POST atıp yanıt gövdesini metin döndürür (orijinal call_uyap_api eşdeğeri).

    multipart=True: FormData davranışı — dict/list alanlar JSON string'e çevrilip
    (None, deger) form alanı olarak gider; httpx multipart/form-data kurar.

    retry=True (varsayılan): bağlantı, yanıt okunmadan koparsa (bkz. _TRANSIENT_ERRORS)
    kısa bir bekleyişle 1 kez tekrar dener — yalnız durum DEĞİŞTİRMEYEN sorgu uçları için
    güvenlidir. Bir taslak/kayıt OLUŞTURAN çağrılarda (ör. mtsTakipTalebiOlustur_brd.ajx)
    retry=False verilmeli: ilk istek aslında UYAP'a ulaşmış ama yanıtı okurken bağlantı
    koptuysa, tekrar denemek YİNELENEN bir kayıt oluşturabilir."""
    async def _send():
        if multipart:
            files = {
                k: (None, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
                for k, v in (payload or {}).items()
            }
            return await ctx.uyap("POST", path, files=files)
        return await ctx.uyap("POST", path, json=(payload if payload is not None else {}))

    try:
        resp = await _send()
    except _TRANSIENT_ERRORS as e:
        if not retry:
            raise
        ctx.log(f"UYAP bağlantı hatası ({type(e).__name__}: {e or 'mesaj yok'}), "
                f"tekrar deneniyor: {path}")
        await asyncio.sleep(1.5)
        resp = await _send()

    if resp.status_code >= 400:
        govde = (resp.text or "").strip()
        if len(govde) > 500:
            govde = govde[:500] + "…"
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü."
                          + (f" Yanıt gövdesi: {govde}" if govde else ""))
    return resp.text


async def _api_json(ctx, path, payload=None, multipart=False, retry=True):
    return json.loads(await _api_text(ctx, path, payload, multipart, retry))


# ── Alacak kalemi JSON kurucu (orijinal _kalem_dict) ──────────────────────────
def _kalem_dict(ad, tutar_float, faiz_oran="", faiz_tur="", idx=0):
    """Tek bir alacak kalemi JSON'ı kurar. faizBilgileri YALNIZCA Asıl Alacak (value 3)
    için doldurulur; faiz/masraf kalemleri faizBilgileri'ni boş ({}) gönderir."""
    ad_l = (ad or "").lower()
    if "faiz" in ad_l:
        ak_val, ak_display = 6, "Faiz Alacağı"
    elif "masraf" in ad_l:
        ak_val, ak_display = 5, "Masraf Alacağı"
    else:
        ak_val, ak_display = 3, "Asıl Alacağı"

    kalem = {
        "selectedTarafHashKeyList": ["kurum_0", "kisi_0"],
        "temelBilgiler": {
            "tutarTL": tutar_float,
            "alacakTutariTL": tutar_float,
            "selectedParaBirimi": {"tktId": "PRBRMTL", "kod": "TL", "aciklama": "TL-Türk Lirası"},
            "KDV": False,
            "aciklama": ad,
            "selectedAlacakKalemKodu": {"name": ak_display, "value": ak_val}
        },
        "id": idx
    }

    if ak_val == 3:
        faiz_oran_float = 0.0
        try:
            faiz_oran_float = float(str(faiz_oran).replace(",", "."))
        except Exception:
            pass
        faiz_tur_tkt, faiz_tur_desc = "FAIZT00007", "Reeskont Avans"
        if faiz_tur and "yasal" in faiz_tur.lower():
            faiz_tur_tkt, faiz_tur_desc = "FAIZT00001", "Yasal Faiz"
        kalem["faizBilgileri"] = {
            "selectedFaizTuru": {"tktId": faiz_tur_tkt, "kod": faiz_tur_tkt.replace("FAIZT", ""),
                                 "aciklama": faiz_tur_desc, "kodTuru": "FAIZT"},
            "faizOraniKurus": faiz_oran_float,
            "selectedFaizSureTipi": {"id": "2", "adi": "Yıllık"}
        }
    else:
        kalem["faizBilgileri"] = {}
    return kalem


def _es_zamanli_hata_mi(veri):
    """UYAP 'eş zamanlı sorgulama' hatası mı? (PRTL_GNL_10001-61)"""
    if not isinstance(veri, dict):
        return False
    kod = str(veri.get("errorCode", ""))
    mesaj = str(veri.get("error", ""))
    return "10001-61" in kod or "zamanlı" in mesaj.lower()


# ── FAZ 1: PREPARE (sorgular + harç; taslak OLUŞTURMAZ) ───────────────────────
async def prepare(ctx, takip, *, il="İzmir", adliye="İzmir", mahiyet=2007):
    """UYAP sorgularını yapar, harç/masrafı hesaplar. (ozet, state) döndürür.
    state, finalize'ın taslak oluşturması için gereken her şeyi taşır."""
    log = ctx.log
    log(f"[{takip.dosya_no}] API akışı başlıyor (hazırlık).")

    # Avukat & kurum
    await _api_text(ctx, "get_avukat_id.ajx", {})
    kurumlar = await _api_json(ctx, "mtsAvukatYetkiliKurumlar_brd.ajx", {})

    selected_kurum = None
    alacakli_clean = takip.alacakli.upper().replace("İ", "I").replace("ı", "I").strip()
    for k in kurumlar:
        k_name = k.get("kurumAdi", "").upper().replace("İ", "I").replace("ı", "I")
        if alacakli_clean in k_name or k_name in alacakli_clean:
            selected_kurum = k
            break
    if not selected_kurum:
        if len(kurumlar) == 1:
            selected_kurum = kurumlar[0]
        else:
            raise ValueError(f"UYAP yetkili kurum listesinde alacaklı bulunamadı: {takip.alacakli}")
    kisi_kurum_id = selected_kurum.get("kisiKurumId")
    log(f"[{takip.dosya_no}] Alacaklı: {selected_kurum.get('kurumAdi')} (ID: {kisi_kurum_id})")

    # Alacaklı adres & iletişim
    adresler = await _api_json(ctx, "getAdresListesi_brd.ajx",
                               {"kisiKurumId": kisi_kurum_id, "tarafTur": 2})
    selected_address = None
    if adresler:
        selected_address = adresler[0]
        selected_address["isSelected"] = True
    iletisim = await _api_json(ctx, "mtsAlacakliIletisimBilgisi.ajx", {"kisiKurumId": kisi_kurum_id})

    # IBAN (TR'siz, yalnız rakam)
    iban_temiz = _re.sub(r'[^0-9]', '', str(takip.iban))
    iban_details = await _api_json(ctx, "geIbanDetails.ajx",
                                   {"iban": iban_temiz, "isSansurlenecek": False})
    iban_val = iban_details.get("value", {}) if isinstance(iban_details, dict) else {}
    if not iban_val:
        raise ValueError(f"UYAP IBAN detayını döndürmedi (iban: {iban_temiz}).")
    selected_iban = [{
        "bankaAdi": iban_val.get("bankaAdi", ""),
        "ibanNumarasi": iban_val.get("ibanNumarasi", iban_temiz),
        "hesapGenel": iban_val.get("hesapGenel", True),
        "isSelected": True, "id": 0, "ibanTuru": "alacakliIban"
    }]

    # Borçlular (T.C. sorgu + mernis)
    async def _kisi_sorgula(_b, ad, soyad):
        return await _api_json(ctx, "kisiSorgulaWithAdSoyad.ajx",
                               {"tcKimlikNo": _b.kimlik, "ad": (ad or "").upper(),
                                "soyad": (soyad or "").upper()})

    kisi_list = []
    for b in takip.borclular:
        ctx.check_cancel()
        kisi_info = await _kisi_sorgula(b, b.ad, b.soyad)
        if _es_zamanli_hata_mi(kisi_info):
            raise ValueError("Eş zamanlı sorgulama hatası: UYAP aynı anda birden fazla sorguya "
                             f"izin vermiyor. Kısa süre sonra tekrar deneyin. (Borçlu: {b.ad} {b.soyad})")
        if isinstance(kisi_info, dict) and kisi_info.get("success") is False:
            yeni_soyad = (kisi_info.get("soyad") or "").strip()
            if yeni_soyad and yeni_soyad.upper() != (b.soyad or "").upper():
                log(f"[{takip.dosya_no}] Soyadı değişmiş: '{b.soyad}' → '{yeni_soyad}', tekrar sorgulanıyor.")
                kisi_info = await _kisi_sorgula(b, b.ad, yeni_soyad)
                if _es_zamanli_hata_mi(kisi_info):
                    raise ValueError("Eş zamanlı sorgulama hatası (soyad güncellemesi). "
                                     f"Sonra tekrar deneyin. (Borçlu: {b.ad} {yeni_soyad})")
        if (not isinstance(kisi_info, dict) or kisi_info.get("success") is False
                or not kisi_info.get("tcKimlikNo")):
            raise ValueError(f"Borçlu T.C. sorgulaması başarısız: {b.ad} {b.soyad} ({b.kimlik})")

        guncel_soyad = (kisi_info.get("soyadi") or "").strip()
        if guncel_soyad and guncel_soyad.upper() != (b.soyad or "").upper():
            b.soyad = guncel_soyad

        mernis_veri = await _api_json(ctx, "mtsMernisAdresiKontrol_brd.ajx",
                                      {"tcKimlikNo": b.kimlik})
        if _es_zamanli_hata_mi(mernis_veri):
            raise ValueError("Eş zamanlı sorgulama hatası (mernis kontrolünde). "
                             f"Sonra tekrar deneyin. (Borçlu: {b.ad} {b.soyad})")
        if mernis_veri is False or mernis_veri == "false":
            raise ValueError(
                f"MERNİS adres hatası: '{kisi_info.get('adi')} {kisi_info.get('soyadi')}' "
                f"(TC: {b.kimlik}) için MERNİS'te kayıtlı adres yok.")

        kisi_list.append({
            "id": f"kisi_{len(kisi_list)}", "tarafTuru": "KISI", "isVekil": False,
            "mernisAdresiKullan": True,
            "tarafAdi": f"{kisi_info.get('adi')} {kisi_info.get('soyadi')}",
            "temelBilgiler": kisi_info, "adresList": [],
            "rolGirisBilgisi": {"rolID": 22, "rolAdi": "BORÇLU", "sanikStatusu": "E", "davaliDavaciGrubu": "L"},
            "viewId": f"kisi_{len(kisi_list)}"
        })
        log(f"[{takip.dosya_no}] Borçlu bulundu: {kisi_info.get('adi')} {kisi_info.get('soyadi')}")

    # Alacak kalemleri
    birlesik_kalemler = kalemleri_birlestir(takip.alacak_kalemleri)
    alacak_kalemleri = []
    for ak in birlesik_kalemler:
        tutar_float = parse_amount(ak.tutar)
        if tutar_float <= 0:
            continue
        alacak_kalemleri.append(
            _kalem_dict(ak.ad, tutar_float, ak.faiz_oran, ak.faiz_tur, idx=len(alacak_kalemleri)))

    tarih_str = format_date_with_slashes(takip.fatura_tarihi)
    muaccel_str = format_date_with_slashes(takip.odeme_tarihi)
    ilamsiz_tutar_float = parse_amount(takip.ilamsiz_tutar)
    if ilamsiz_tutar_float <= 0:
        ilamsiz_tutar_float = sum(k["temelBilgiler"]["tutarTL"] for k in alacak_kalemleri)

    def _ilamsiz_list_kur():
        return [{
            "id": 0, "no": takip.abone_no or takip.hizmet_abone_no or "",
            "tutar": f"{ilamsiz_tutar_float:.2f} TL", "tutarTL": ilamsiz_tutar_float,
            "tur": "TL", "tarih": tarih_str, "muacceliyetTarihi": muaccel_str,
            "aciklama": takip.aciklama or "",
            "selectedParaBirimi": {"tktId": "PRBRMTL", "kod": "TL", "aciklama": "TL-Türk Lirası"},
            "alacakKalemleri": alacak_kalemleri, "alacakKalemi": ""
        }]

    ilamsiz_list = _ilamsiz_list_kur()

    taraf_list = {
        "kisiList": kisi_list,
        "kurumList": [{
            "id": "kurum_0", "tarafTuru": "KURUM", "isVekil": False, "mernisAdresiKullan": False,
            "temelBilgiler": selected_kurum, "tarafAdi": selected_kurum.get("kurumAdi"),
            "adresList": [selected_address] if selected_address else [],
            "rolGirisBilgisi": {"rolID": 21, "rolAdi": "ALACAKLI", "sanikStatusu": "H", "davaliDavaciGrubu": "N"},
            "selectedIban": selected_iban,
            "currentStatus": {
                "tarafHesapBilgisi": {"selectedHesapBilgileriDVOList": []},
                "vekilHesapBilgisi": {"selectedHesapBilgileriDVOList": selected_iban}
            },
            "alacakliIletisimTelefon": iletisim.get("telefon") or "4441444",
            "alacakliIletisimEPosta": iletisim.get("ePosta") or "iletisim@turktelekom.com.tr",
            "viewId": "kurum_0"
        }]
    }

    # Harç hesapla
    async def _harc_hesapla():
        h = await _api_json(ctx, "mtsHarcList_brd.ajx",
                            {"ilamsizList": ilamsiz_list, "tarafList": taraf_list}, multipart=True)
        if isinstance(h, dict) and "error" in h:
            raise ValueError(f"UYAP harç/masraf hesaplayamadı. Hata: {h.get('error')} (Kod: {h.get('errorCode')})")
        if isinstance(h, list) and not any(it.get("harcMasrafAdi") == "Vekalet Pulu"
                                           for it in h if isinstance(it, dict)):
            try:
                pulu_raw = await _api_text(ctx, "vekaletPuluBedeli.ajx", {})
                pulu_val = float(str(pulu_raw).strip())
                if pulu_val > 0:
                    h.append({"harcMasrafAdi": "Vekalet Pulu", "hesapMiktar": pulu_val})
            except Exception as e:
                log(f"[{takip.dosya_no}] Uyarı: vekalet pulu eklenemedi: {e}")
        return h

    harclar = await _harc_hesapla()

    # Masraf alacağı kalemi (ekran toplamı + 317)
    def _harc_ekran_toplami(h):
        toplam = 0.0
        for it in (h or []):
            if isinstance(it, dict):
                m = it.get("hesapMiktar")
                if isinstance(m, (int, float)):
                    toplam += m
        return toplam

    masraf_ekran = _harc_ekran_toplami(harclar)
    if masraf_ekran > 0:
        masraf_tutar = round(masraf_ekran + 317, 2)
        log(f"[{takip.dosya_no}] Masraf alacağı ekleniyor: {masraf_tutar} TL")
        alacak_kalemleri.append(_kalem_dict("Masraf", masraf_tutar, idx=len(alacak_kalemleri)))
        ilamsiz_list = _ilamsiz_list_kur()
        harclar = await _harc_hesapla()

    # İl & adliye
    iller = await _api_json(ctx, "illeri_getirJSON.ajx", {})
    selected_il = None
    il_clean = il.upper().replace("İ", "I").replace("ı", "I").strip()
    for i_obj in iller:
        i_name = i_obj.get("ad", "").upper().replace("İ", "I").replace("ı", "I")
        if il_clean in i_name or i_name in il_clean:
            selected_il = i_obj
            break
    if not selected_il:
        selected_il = {"il": 35, "ad": "İZMİR"}

    adliyeler = await _api_json(ctx, "icraTakipAdliyeler.ajx", {"ilKodu": selected_il.get("il")})
    selected_adliye = None
    adliye_clean = adliye.upper().replace("İ", "I").replace("ı", "I").strip()
    for a_obj in adliyeler:
        a_name = a_obj.get("adliyeIsmi", "").upper().replace("İ", "I").replace("ı", "I")
        if adliye_clean in a_name or a_name in adliye_clean:
            selected_adliye = a_obj
            break
    if not selected_adliye:
        raise ValueError(f"UYAP Adliye listesinde adliye bulunamadı: {adliye}")

    state = {
        "ilamsiz_list": ilamsiz_list, "taraf_list": taraf_list, "harclar": harclar,
        "selected_il": selected_il, "selected_adliye": selected_adliye, "mahiyet": mahiyet,
    }
    ozet = _ozet_kur(takip, adliye, harclar, alacak_kalemleri)
    return ozet, state


def _ozet_kur(takip, adliye, harclar, alacak_kalemleri):
    """İstemci onay ekranında gösterilecek özet (show_validation_dialog içeriğinin verisi)."""
    toplam = sum(k["temelBilgiler"]["tutarTL"] for k in alacak_kalemleri)
    harc_ozet = []
    for h in (harclar or []):
        if isinstance(h, dict):
            harc_ozet.append({
                "ad": h.get("harcMasrafAdi") or h.get("aciklama") or "Masraf",
                "miktar": h.get("hesapMiktar") or h.get("harcMasrafBedel") or 0.0,
            })
    return {
        "dosya_no": takip.dosya_no,
        "alacakli": takip.alacakli,
        "iban": takip.iban,
        "abone_no": takip.abone_no or takip.hizmet_abone_no,
        "borclular": [{"ad": b.ad, "soyad": b.soyad, "kimlik": b.kimlik} for b in takip.borclular],
        "kalemler": [{"ad": k["temelBilgiler"]["aciklama"], "tutar": k["temelBilgiler"]["tutarTL"]}
                     for k in alacak_kalemleri],
        "toplam": round(toplam, 2),
        "harclar": harc_ozet,
    }


# ── FAZ 2: FINALIZE (taslak + UDF indir + imzala + evrak gönder) ──────────────
async def finalize(ctx, takip, state, *, vekalet=None, dayanak=None):
    """Onaylanan takibi tamamlar. vekalet/dayanak: {"filename":..., "bytes":...} ya da None.
    Dönüş: {"dosya_id":..., "evrak_sonuc":...}."""
    from .. import uyap_proxy, udf_signer
    log = ctx.log

    gw = uyap_proxy.gw
    if gw is None:
        raise RuntimeError("UYAP oturumu hazır değil (gw=None).")

    ilamsiz_list = state["ilamsiz_list"]
    taraf_list = state["taraf_list"]
    harclar = state["harclar"]
    selected_il = state["selected_il"]
    selected_adliye = state["selected_adliye"]
    mahiyet = state.get("mahiyet", 2007)

    # 8) Taslak oluştur
    harc_bilgileri = {"selectedTarafHashKeyList": ["kurum_0"], "selectedTarafHashList": harclar}
    dosya_bilgileri = {
        "dosyaAciklama": takip.aciklama or "",
        "takipIlKodu": selected_il.get("il"),
        "adliye": selected_adliye.get("adliyeBirimID"),
        "mahiyet": mahiyet, "tereke": False,
        "dosyaAciklama_48_4": takip.aciklama or "",
        "mahiyetList": [{"mahiyetId": mahiyet, "kod": "CEPTEL", "takipSekliKod": 7,
                         "mahiyetAdi": "Telefon(Cep)", "gecerlimi": "E", "zorunlu": False,
                         "degistirilemez": False}],
        "dosyaKriterList": [{"kod": "bk", "mahiyetAdi": "B.K. 100.Madde", "zorunlu": True,
                             "degistirilemez": True, "isSelected": True, "disabled": True}],
        "terekemi": "H", "selectedIl": selected_il,
        "selectedBirim": {"birimId": selected_adliye.get("adliyeBirimID"),
                          "birimAd": selected_adliye.get("adliyeIsmi")}
    }
    log(f"[{takip.dosya_no}] UYAP'ta taslak oluşturuluyor...")
    # retry=False: bağlantı yanıt okunurken koparsa, ilk istek UYAP'a ULAŞMIŞ olabilir —
    # otomatik tekrar YİNELENEN bir taslak oluşturabilir (bkz. _api_text docstring).
    taslak = await _api_json(ctx, "mtsTakipTalebiOlustur_brd.ajx", {
        "MTS": "1", "ilamsizList": ilamsiz_list, "tarafList": taraf_list,
        "MTSHarcBilgileri": harc_bilgileri, "MTSDosyaBilgileri": dosya_bilgileri,
    }, multipart=True, retry=False)
    dosya_id = taslak.get("dosyaId")
    if not dosya_id:
        raise ValueError(f"UYAP taslak oluşturamadı. Yanıt: {taslak}")
    if isinstance(dosya_id, str):
        dosya_id = dosya_id.strip().strip('"').strip("'")
    log(f"[{takip.dosya_no}] Taslak oluşturuldu. Dosya ID: {dosya_id}")

    # UDF indir / imzala / evrak gönder (11-13) BURADAN sonra patlarsa, taslak UYAP'ta
    # ZATEN AÇIK (dosya_id var) ve TEKRAR bu iş çalıştırılırsa YENİ bir taslak/mükerrer
    # kayıt oluşur. O yüzden dosya_id'yi hata mesajına GÖM (kullanıcı kaybetmesin, UYAP'ta
    # 'Tamamlanmayan Dosyalar' ekranından bulup elle devam etsin) — bkz. kullanıcı bulgusu
    # 2026-08-11 (evrak gönderirken ReadError, dosya_id log'da görünse de sonuçta hiç
    # dönmüyordu).
    try:
        # 11) UDF taslağını indir (GET) — canlı oturum üzerinden, diske gerek yok
        resp = await ctx.uyap("GET", "mtsTakipTalebiHazirla_brd.avukat",
                              params={"dosyaId": dosya_id}, write=False)
        if resp.status_code >= 400:
            raise ValueError(f"UDF indirilemedi (HTTP {resp.status_code}).")
        if "text/html" in (resp.headers.get("content-type", "").lower()):
            raise ValueError("UDF yerine HTML döndü (oturum/erişim sorunu).")
        udf_bytes = resp.content
        if not udf_bytes:
            raise ValueError("UDF taslağı boş indi.")

        # Güvenli dosya adı (borçlu + abone)
        _b0 = takip.borclular[0] if takip.borclular else None
        _abone = (takip.abone_no or takip.hizmet_abone_no or "").strip()
        if _b0:
            _prefix = f"{_b0.ad}_{_b0.soyad}_{_abone}" if _abone else f"{_b0.ad}_{_b0.soyad}"
        else:
            _prefix = _abone or "UYAP_takip"
        guvenli_isim = tr_to_ascii(_prefix) + ".udf"

        # 12) Headless e-imza (kart PIN/sertifika canlı oturumdan)
        log(f"[{takip.dosya_no}] Takip talebi e-imzalanıyor (headless)...")
        cert_id = getattr(gw, "cert_id", None)
        pin = getattr(getattr(gw, "login_args", None), "pin", None)
        loop = asyncio.get_running_loop()
        signed = await loop.run_in_executor(
            None, udf_signer.sign_document, udf_bytes, guvenli_isim, cert_id, pin)
        log(f"[{takip.dosya_no}] İmzalandı ({len(signed)} bayt).")

        # 13) Evrakları programatik gönder (imzalı UDF + vekalet + dayanak)
        evraklar = [{"tur": "ICR_TAKIP_TLP", "filename": guvenli_isim, "bytes": signed}]
        if vekalet and vekalet.get("bytes"):
            evraklar.append({"tur": "CZM_VEKALETNAME",
                             "filename": vekalet.get("filename") or "vekalet.pdf",
                             "bytes": vekalet["bytes"]})
        else:
            log(f"[{takip.dosya_no}] ⚠️ Vekaletname yok — gönderilmiyor (UYAP reddedebilir).")
        if dayanak and dayanak.get("bytes"):
            evraklar.append({"tur": "MTS_TAKIBIN_DAYANAGI",
                             "filename": dayanak.get("filename") or "dayanak.pdf",
                             "bytes": dayanak["bytes"]})
        else:
            log(f"[{takip.dosya_no}] ⚠️ Dayanak belge yok — gönderilmiyor (UYAP reddedebilir).")

        items_json, alanlar = items_kur(evraklar)
        files = {}
        for (alan, fname), ev in zip(alanlar, evraklar):
            files[alan] = (fname, ev["bytes"], mime_belirle(fname))
        files["items"] = (None, items_json)
        files["dosyaId"] = (None, dosya_id)

        log(f"[{takip.dosya_no}] Evraklar gönderiliyor ({len(alanlar)} adet)...")
        # BİLEREK retry yok: yanıtı okurken bağlantı koparsa evrak UYAP'a ULAŞMIŞ olabilir,
        # körlemesine tekrar denemek MÜKERRER evrak yükleyebilir.
        ev_resp = await ctx.uyap("POST", "davaAcilisEvrakGonderme_brd.ajx", files=files)
        try:
            sonuc = json.loads(ev_resp.text) if ev_resp.text else {}
        except Exception:
            sonuc = {"type": "unknown", "message": ev_resp.text}
        if not isinstance(sonuc, dict) or sonuc.get("type") != "success":
            raise ValueError(f"Evrak gönderme başarısız. UYAP yanıtı: {sonuc}")
    except Exception as e:
        raise RuntimeError(
            f"Taslak ZATEN OLUŞTU (Dosya ID: {dosya_id}) ama evrak gönderilene kadar hata "
            f"oluştu: {type(e).__name__}: {e}. UYAP'ta 'Tamamlanmayan Dosyalar' ekranından bu "
            "dosyayı bulup evrak durumunu kontrol edin/elle tamamlayın — bu takibi TEKRAR "
            "açmayın, mükerrer taslak oluşturur."
        ) from e
    log(f"[{takip.dosya_no}] ✓ Tüm evraklar gönderildi. Dosya AÇIK (henüz ödenmedi).")

    # Ödeme ve tebligat BURADA OTOMATİK yürümez: kullanıcı bunları panelden aç/kapa +
    # onay modu (yok/tek_tek/toplu) ile ayrı ayrı seçer (bkz. job_handlers._coklu_takip_ac,
    # params.odeme_aktif/odeme_onay_modu/tebligat_aktif/tebligat_onay_modu). finalize()
    # yalnızca dosyayı açar; _odeme_yap/_tebligat_gonder o orkestrasyon tarafından,
    # kullanıcının onayından SONRA çağrılır.
    return {"dosya_id": dosya_id, "evrak_sonuc": sonuc}


# ── Harç ödeme (mtsDavaAcilis_brd.ajx) ────────────────────────────────────────
# 2026-08-11 CANLI DENEME #1 ve #2 BAŞARISIZ: yanlış uç nokta kullanılıyordu
# (davaAcilisOdemeIslemleri_brd.ajx, kisiKurumId + harcMasrafList + vakifbankHesapBilgileri
# gibi bir sürü alanla). Bu uç nokta her ikisinde de yalnızca ₺164'lük 'Vekalet Pulu'yu
# işledi, ₺836'lık asıl harcı (MTS Harcı 732 + Vekalet Suret Harcı 104) hiç işlemedi,
# dosya numarasız kaldı — toplam ₺328 gerçek para kaybı.
# DÜZELTME #1 (2026-08-11, ilk capture analizine dayanarak, CANLI DOĞRULANMADAN):
# uç nokta "mtsDavaAcilis.ajx" (brd'siz) olarak yazılmıştı. HARC_ODEME_AKTIF açılıp
# canlı denendiğinde bu isim YANLIŞ çıktı ('hatalı işlem').
# DÜZELTME #2 (2026-08-11, kullanıcının Chrome DevTools ile yakaladığı GERÇEK manuel
# ödeme XHR trafiğiyle doğrulandı — dosya no 2026/899210): UYAP'ın kendi arayüzü
# ödeme için mtsDavaAcilis_BRD.ajx (SONUNDA _brd VAR) uç noktasını kullanıyor, payload
# SADECE
#   {"dosyaId": "<HAM dosyaId, iç içe tırnak karakterleri dahil>", "odemeTipi": "7"}
# Hiçbir harç kalemi/kisiKurumId istemciden gönderilmiyor — sunucu dosyaId'ye bağlı TÜM
# harcı (MTS Harcı + Vekalet Suret Harcı + Vekalet Pulu) kendi hesaplayıp tahsil ediyor.
# Yanıt doğrudan gerçek esas no'yu da içeriyor: {"dosyaNo":"2026/899210","dosyaYil":2026,...}
# — bu, odenmis_dosyalari_bul()'daki ad-soyad eşleştirmesinden daha güvenilir bir kaynak
# olabilir (ayrı bir iyileştirme konusu, bkz. _odeme_yap içindeki not).
# ÖNEMLİ: dosyaId, UYAP'ın döndürdüğü HAM haliyle (iç içe tırnak karakterleri korunarak)
# gönderilmeli; finalize()'daki temizlenmiş (tırnaksız) dosya_id burada tekrar
# tırnaklanıyor (bkz. aşağıdaki _odeme_yap).
# Uç nokta adı artık gerçek trafikle doğrulandı VE 2026-08-11 canlı denemede uçtan
# uca doğrulandı (dosya no 2026/899210 gerçekten açıldı). Bu fonksiyonun ÇAĞRILIP
# ÇAĞRILMAYACAĞI artık burada bir sabitle değil, job_handlers._coklu_takip_ac'a
# geçirilen params.odeme_aktif (panelden kullanıcının aç/kapa yaptığı) ile belirlenir —
# bkz. o dosyadaki orkestrasyon.
async def _odeme_yap(ctx, takip, dosya_id, state):
    """MTS harcını öder (mtsDavaAcilis_brd.ajx). Yanıt JSON'ı doğrudan gerçek esas no'yu
    ("dosyaNo": "2026/899210") ve ödeme sonrası UYAP'ın verdiği YENİ opak dosyaId'yi içerir
    (UYAP dosya durumu değiştikçe token'ı yeniliyor — bkz. yukarıdaki uç nokta notu). Bu
    yanıttan ayrıştırma başarısız olursa (beklenmeyen şekil), eski yönteme —
    odenmis_dosyalari_bul() ile ad-soyad eşleştirmesine— düşülür.
    Dönüş: {"odeme_sonuc": str, "gercek_dosya_no": str|None, "dosya_id": str} — "dosya_id",
    ödeme yanıtı yeni bir token içeriyorsa GÜNCEL değer, yoksa girdi dosya_id'nin aynısıdır."""
    log = ctx.log
    # UYAP dosyaId'leri HAM halinde (iç içe tırnak karakterleri dahil) bekliyor;
    # finalize() dosya_id'yi temizleyip (tırnaksız) sakladığı için burada geri tırnaklanıyor.
    dosya_id_ham = dosya_id if (isinstance(dosya_id, str) and dosya_id.startswith('"')) else f'"{dosya_id}"'
    log(f"[{takip.dosya_no}] Harç ödemesi gönderiliyor (e-Barobirlik kart, mtsDavaAcilis_brd.ajx)...")
    odeme_sonuc = await _api_text(ctx, "mtsDavaAcilis_brd.ajx", {
        "dosyaId": dosya_id_ham, "odemeTipi": "7",
    })
    log(f"[{takip.dosya_no}] ✓ Harç ödemesi tamamlandı.")

    guncel_dosya_id = dosya_id
    gercek_dosya_no = None
    try:
        odeme_json = json.loads(odeme_sonuc)
    except Exception:
        odeme_json = None
    if isinstance(odeme_json, dict):
        dn = str(odeme_json.get("dosyaNo") or "").strip()
        if _ESAS_NO_DESENI.match(dn):
            gercek_dosya_no = dn
        yeni_id = odeme_json.get("dosyaId")
        if isinstance(yeni_id, str) and yeni_id.strip():
            guncel_dosya_id = yeni_id.strip()

    if not gercek_dosya_no:
        try:
            eslesme = await odenmis_dosyalari_bul(ctx, [takip])
            gercek_dosya_no = eslesme.get(str(takip.dosya_no))
        except Exception as e:
            log(f"[{takip.dosya_no}] ⚠️ Ödeme sonrası esas no doğrulanamadı: {e}")
    if gercek_dosya_no:
        log(f"[{takip.dosya_no}] ✓ MTS dosyası açıldı: {gercek_dosya_no}")
    else:
        log(f"[{takip.dosya_no}] ⚠️ Ödeme yapıldı ama esas no 'Tamamlanmayan Dosyalar' "
            "listesinde henüz bulunamadı — UYAP'tan elle doğrulayın.")
    return {"odeme_sonuc": odeme_sonuc, "gercek_dosya_no": gercek_dosya_no, "dosya_id": guncel_dosya_id}


# ── Tebligat gönderme (mtsTarafTebligatList_brd.ajx → mernis adres → mtsTebligatUcreti_brd.ajx
# → MtsTebligatGonder_brd.ajx) ────────────────────────────────────────────────
# 2026-08-11: Kullanıcının Chrome DevTools ile yakaladığı GERÇEK manuel tebligat gönderme
# XHR trafiğine dayanır (dosya no 2026/898949, borçlu AYŞE YUNUSOĞLU, kisiKurumId 85013687,
# ücret 317.20 TL). Akış:
#   1) mtsTarafTebligatList_brd.ajx {"dosyaId": <ham>} → tebligat bekleyen taraf listesi
#      (her taraf: tarafTebligatDVO.hasTebligatEvrak, evrakKisiDVO.guncelKisiKurumId, ...).
#   2) Her taraf için mtsMernisMersisAdresi_brd.ajx {"kisiKurumId": ...} → güncel mernis
#      adresi metni, sonra mtsTebligatAdresGuncelle_brd.ajx {"dosyaId":..., "kisiKurumId":...,
#      "adresTuru":"ADRTR00008"} → tebligat adresini mernise sabitler ({"basarilimi":true}).
#   3) mtsTebligatUcreti_brd.ajx {"dosyaId":..., "tebligatKisiKurumIdList": "[85013687]"}
#      (DİKKAT: liste GERÇEK JSON array DEĞİL, "[id1,id2]" biçiminde bir STRING alan olarak
#      gönderiliyor — dosyaId'deki iç içe tırnaklama gibi UYAP'a özgü bir tuhaflık) → ücret
#      (bare sayı, ör. 317.2, TL).
#   4) MtsTebligatGonder_brd.ajx {"MTSTebligat": 1, "dosyaId":..., "tebligatKisiKurumIdList":
#      <aynı string>, "tebligatUcreti": <ücret>, "odemeTipi": "7"} → gönderim + e-Barobirlik
#      karttan ücret tahsili.
# ÖNEMLİ: dosyaId burada _odeme_yap()'in döndürdüğü, ödeme SONRASI UYAP'ın verdiği GÜNCEL
# token olmalı — taslak oluşturma anındaki eski dosya_id değil (bkz. _odeme_yap notu).
#
# AŞAĞIDAKİ TARAF SEÇİMİ (hasTebligatEvrak) TAM CANLI DOĞRULANMADI: capture'ın son (çok
# büyük) bölümü gönderim SONRASI aynı şemayla ("tarafTebligatDVO": hasTebligatEvrak:true,
# pttDurum:1, aynı kisiKurumId 85013687, gömülü PDF evrak baytları) bir yanıt içeriyor —
# bu, mtsTarafTebligatList_brd.ajx'in gönderim sonrası TEKRAR çağrıldığında hasTebligatEvrak
# alanının true'ya döndüğünü DESTEKLİYOR, ama o bölüm "payload:" diye etiketlenmiş
# ("response:" değil) — muhtemelen kullanıcının capture'ı etiketleme hatası, ama KESİN DEĞİL.
# Yani _tebligat_gonder'daki "hasTebligatEvrak=true olanı atla" filtresi, tekrar
# çalıştırıldığında AYNI TARAFA İKİNCİ KEZ ÜCRET YAZMAYI önlemek için var, ama bu filtrenin
# güvenilirliği CANLI olarak ayrıca doğrulanmalı (ör. bir dosyada tebligat gönder, sonra
# job'ı AYNI dosya_id ile tekrar çalıştır, ikinci seferde 0 taraf/₺0 gönderildiğini gözle).
# Adres adımı (mtsMernisMersisAdresi + mtsTebligatAdresGuncelle) HER taraf için KOŞULSUZ
# çalıştırılıyor (capture'daki tek örnekte zaten aktif adres mernisti; "adres zaten mernisse
# atla" gibi bir kısayol capture'dan doğrulanamadı, bu yüzden eklenmedi — kullanıcı otomatik
# mernis güncellemesini seçti, bkz. proje notları).
# Bu fonksiyonun ÇAĞRILIP ÇAĞRILMAYACAĞI burada bir sabitle değil, job_handlers.
# _coklu_takip_ac'a geçirilen params.tebligat_aktif (panelden kullanıcının aç/kapa
# yaptığı, varsayılan KAPALI) ile belirlenir — bkz. o dosyadaki orkestrasyon. Kapalıyken
# tebligat hiç denenmez (dosya AÇIK ve ÖDENMİŞ kalır, UYAP'tan elle gönderilebilir).
# Kullanıcı ilk kez açmadan önce, yukarıdaki hasTebligatEvrak filtresini tercihen TEK bir
# zaten ödenmiş dosyada mts_tebligat_gonder KURTARMA job'ıyla izole test etmeli.
async def _tebligat_gonder(ctx, takip, dosya_id):
    """Bir MTS dosyasındaki, tebligat evrakı henüz oluşmamış tüm taraflara tebligat
    gönderir (adreslerini önce mernise sabitleyerek). dosya_id, ödeme SONRASI UYAP'ın
    döndürdüğü güncel opak token olmalı (bkz. _odeme_yap).
    Dönüş: {"ucret": float, "kisi_kurum_idler": [...], "sonuc": str} ya da gönderilecek
    taraf yoksa None."""
    log = ctx.log
    dosya_id_ham = dosya_id if (isinstance(dosya_id, str) and dosya_id.startswith('"')) else f'"{dosya_id}"'

    taraf_list = await _api_json(ctx, "mtsTarafTebligatList_brd.ajx", {"dosyaId": dosya_id_ham})
    if not isinstance(taraf_list, list) or not taraf_list:
        log(f"[{takip.dosya_no}] Tebligat: gönderilecek taraf bulunamadı.")
        return None

    kisi_kurum_idler = []
    for t in taraf_list:
        dvo = (t or {}).get("tarafTebligatDVO") or {}
        if dvo.get("hasTebligatEvrak"):
            continue
        kk_id = (dvo.get("evrakKisiDVO") or {}).get("guncelKisiKurumId")
        if kk_id and kk_id not in kisi_kurum_idler:
            kisi_kurum_idler.append(kk_id)
    if not kisi_kurum_idler:
        log(f"[{takip.dosya_no}] Tebligat: tüm taraflara zaten gönderilmiş.")
        return None

    for kk_id in kisi_kurum_idler:
        mernis = await _api_json(ctx, "mtsMernisMersisAdresi_brd.ajx", {"kisiKurumId": kk_id})
        mernis_adres = (mernis or {}).get("veri") if isinstance(mernis, dict) else None
        if not mernis_adres:
            raise ValueError(f"kisiKurumId {kk_id} için MERNİS adresi bulunamadı — "
                              "tebligat adresi belirlenemedi.")
        guncelle = await _api_json(ctx, "mtsTebligatAdresGuncelle_brd.ajx", {
            "dosyaId": dosya_id_ham, "kisiKurumId": kk_id, "adresTuru": "ADRTR00008",
        })
        if not (isinstance(guncelle, dict) and guncelle.get("basarilimi")):
            raise ValueError(f"kisiKurumId {kk_id} için tebligat adresi güncellenemedi: {guncelle}")
        log(f"[{takip.dosya_no}] Tebligat adresi mernise sabitlendi (kisiKurumId {kk_id}): "
            f"{mernis_adres}")

    kisi_kurum_id_list_str = json.dumps(kisi_kurum_idler)

    ucret_raw = await _api_text(ctx, "mtsTebligatUcreti_brd.ajx", {
        "dosyaId": dosya_id_ham, "tebligatKisiKurumIdList": kisi_kurum_id_list_str,
    })
    try:
        ucret = float(str(ucret_raw).strip())
    except Exception:
        raise ValueError(f"Tebligat ücreti okunamadı: {ucret_raw!r}")

    log(f"[{takip.dosya_no}] Tebligat gönderiliyor ({len(kisi_kurum_idler)} taraf, {ucret} TL, "
        "e-Barobirlik kart, MtsTebligatGonder_brd.ajx)...")
    sonuc = await _api_text(ctx, "MtsTebligatGonder_brd.ajx", {
        "MTSTebligat": 1, "dosyaId": dosya_id_ham,
        "tebligatKisiKurumIdList": kisi_kurum_id_list_str,
        "tebligatUcreti": ucret, "odemeTipi": "7",
    })
    log(f"[{takip.dosya_no}] ✓ Tebligat gönderildi ({ucret} TL, {len(kisi_kurum_idler)} taraf).")
    return {"ucret": ucret, "kisi_kurum_idler": kisi_kurum_idler, "sonuc": sonuc}


# ── "Tamamlanmayan Dosyalar" üzerinden ÖDEME DURUMU sorgusu ──────────────────
# YALNIZCA OKUR — hiçbir şey göndermez/ödemez. "Excel'e Aktar" özelliği
# (Panel/modules/mts_takip.py) VE yukarıdaki _odeme_yap() bu fonksiyonla o anki
# durumu okur.
_ESAS_NO_DESENI = _re.compile(r"^\d{4}/\d+$")


async def odenmis_dosyalari_bul(ctx, takipler):
    """UYAP'ın 'Tamamlanmayan Dosyalar' (dosyaTurKod=35) listesini TEK seferde çeker ve
    her takibi borçlu ad soyadıyla eşleştirir — bkz. xml_takip._guncel_dosya_id_bul ile
    AYNI eşleştirme deseni. Eşleşen kayıtta gerçek esas no varsa ("2026/894734" gibi;
    "Ödeme Yapılmadı!" DEĞİL) o no'yu, yoksa None döner.

    Dönüş: {dosya_no(str): gercek_dosya_no(str) | None}."""
    from ..ipotek.takip import _guvenli_liste, _temiz_buyuk

    liste = _guvenli_liste(await _api_json(ctx, "tamamlanmayanDosyalar_brd.ajx", {"dosyaTurKod": 35}))
    sonuc = {}
    for t in takipler:
        hedef_adlar = [_temiz_buyuk(f"{b.ad} {b.soyad}".strip())
                       for b in (t.borclular or []) if (b.ad or b.soyad)]
        hedef_adlar = [ad for ad in hedef_adlar if ad]
        gercek_no = None
        if hedef_adlar:
            adaylar = [k for k in liste
                      if all(ad in _temiz_buyuk(k.get("taraflar") or "") for ad in hedef_adlar)]
            for k in adaylar:
                dn = (k.get("dosyaNo") or "").strip()
                if _ESAS_NO_DESENI.match(dn):
                    gercek_no = dn
                    break
        sonuc[str(t.dosya_no)] = gercek_no
    return sonuc


async def _guncel_dosya_id_bul(ctx, takip):
    """UYAP 'Tamamlanmayan Dosyalar' listesinden, takibin borçlu ad-soyadıyla eşleşen
    dosyanın GÜNCEL (tırnaklar dahil, ham) dosyaID'sini bulur — bkz. xml_takip
    ._guncel_dosya_id_bul ile AYNI, CANLI DOĞRULANMIŞ desen (opak token her yanıtta
    değişebildiği için her kullanımdan önce yeniden sorgulanmalı). Birden fazla veya
    hiç eşleşme yoksa ValueError fırlatır (elle seçim gerekir).

    Bu, ör. mts_tebligat_gonder job'ının kullanıcıdan ham opak token istemeden, yalnızca
    takip.dosya_no/borclular ile çalışabilmesini sağlar."""
    from ..ipotek.takip import _guvenli_liste, _temiz_buyuk

    liste = _guvenli_liste(await _api_json(ctx, "tamamlanmayanDosyalar_brd.ajx", {"dosyaTurKod": 35}))
    if not liste:
        raise ValueError("'Tamamlanmayan Dosyalar' listesi boş.")
    hedef_adlar = [_temiz_buyuk(f"{b.ad} {b.soyad}".strip())
                   for b in (takip.borclular or []) if (b.ad or b.soyad)]
    hedef_adlar = [ad for ad in hedef_adlar if ad]
    if not hedef_adlar:
        raise ValueError(f"[{takip.dosya_no}] Takipte borçlu bilgisi yok — dosya eşleştirilemez.")
    adaylar = [k for k in liste
              if all(ad in _temiz_buyuk(k.get("taraflar") or "") for ad in hedef_adlar)]
    if not adaylar:
        raise ValueError(f"[{takip.dosya_no}] 'Tamamlanmayan Dosyalar' listesinde eşleşen dosya "
                         f"bulunamadı (aranan taraflar: {hedef_adlar}).")
    if len(adaylar) > 1:
        raise ValueError(f"[{takip.dosya_no}] Birden fazla eşleşen dosya bulundu ({len(adaylar)} "
                         "adet) — belirsiz, params.dosya_id ile elle belirtin.")
    dosya_id = adaylar[0].get("dosyaID") or adaylar[0].get("dosyaId")
    if not dosya_id:
        raise ValueError(f"[{takip.dosya_no}] Eşleşen kayıtta dosyaID alanı yok: {adaylar[0]}")
    return dosya_id
