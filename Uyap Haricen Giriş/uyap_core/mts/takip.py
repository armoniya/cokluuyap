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

from .models import (
    kalemleri_birlestir, parse_amount, format_date_with_slashes, tr_to_ascii,
)
from .evrak import items_kur, mime_belirle


class DosyaAtla(Exception):
    """Bir takip atlanmalı (kullanıcı 'atla' dedi ya da düzeltilebilir veri sorunu)."""
    pass


# ── UYAP istek yardımcıları (ctx.uyap üzerinden) ──────────────────────────────
async def _api_text(ctx, path, payload=None, multipart=False):
    """UYAP'a POST atıp yanıt gövdesini metin döndürür (orijinal call_uyap_api eşdeğeri).

    multipart=True: FormData davranışı — dict/list alanlar JSON string'e çevrilip
    (None, deger) form alanı olarak gider; httpx multipart/form-data kurar."""
    if multipart:
        files = {
            k: (None, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
            for k, v in (payload or {}).items()
        }
        resp = await ctx.uyap("POST", path, files=files)
    else:
        resp = await ctx.uyap("POST", path, json=(payload if payload is not None else {}))
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü.")
    return resp.text


async def _api_json(ctx, path, payload=None, multipart=False):
    return json.loads(await _api_text(ctx, path, payload, multipart))


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
    taslak = await _api_json(ctx, "mtsTakipTalebiOlustur_brd.ajx", {
        "MTS": "1", "ilamsizList": ilamsiz_list, "tarafList": taraf_list,
        "MTSHarcBilgileri": harc_bilgileri, "MTSDosyaBilgileri": dosya_bilgileri,
    }, multipart=True)
    dosya_id = taslak.get("dosyaId")
    if not dosya_id:
        raise ValueError(f"UYAP taslak oluşturamadı. Yanıt: {taslak}")
    if isinstance(dosya_id, str):
        dosya_id = dosya_id.strip().strip('"').strip("'")
    log(f"[{takip.dosya_no}] Taslak oluşturuldu. Dosya ID: {dosya_id}")

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
    ev_resp = await ctx.uyap("POST", "davaAcilisEvrakGonderme_brd.ajx", files=files)
    try:
        sonuc = json.loads(ev_resp.text) if ev_resp.text else {}
    except Exception:
        sonuc = {"type": "unknown", "message": ev_resp.text}
    if not isinstance(sonuc, dict) or sonuc.get("type") != "success":
        raise ValueError(f"Evrak gönderme başarısız. UYAP yanıtı: {sonuc}")
    log(f"[{takip.dosya_no}] ✓ Tüm evraklar gönderildi. Takip tamamlandı.")

    return {"dosya_id": dosya_id, "evrak_sonuc": sonuc}
