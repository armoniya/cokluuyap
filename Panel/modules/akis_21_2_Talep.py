# -*- coding: utf-8 -*-
"""
OTURUM AKISI — 21/2 Talep
=========================
Logger'in "Session'u module cevir" ile OTOMATIK uretildi. Yakalanan 27
adimlik oturumu SIRAYLA tekrar GONDERIR. Her istek 127.0.0.1:8800 ofis proxy'sine
POST edilir; ofis (uyap_app.py) onu canli e-imza UYAP oturumuyla iletir — tarayici
GEREKMEZ. "zincir" ile isaretli alanlar bir ONCEKI adimin yanitindan otomatik
beslenir; "girdi" ilk adimin girdileridir; kalani yakalanan sabittir.

Dogrudan calistir:  python <bu_dosya>.py     -> akisi calistirir, son yaniti basar

UYARI: liste yanitlarinda [index] (genelde [0]) varsayimi kullanildi; cok kayitli
       adimlarda gozden gecir.
"""

import json
import urllib.request
import urllib.error

OFFICE_BASE = "http://127.0.0.1:8800"

COMMON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "cache-control": "no-cache",
    "referer": "https://avukat.uyap.gov.tr/",
}

# Ilk adimin skaler girdileri: (python_param, alan, ornek).
PARAMETRELER = [
    # (ilk adımda skaler girdi yok)
]

# Dogrudan calistirinca kullanilacak ornek girdi.
ORNEK_GIRDI = {
    # (girdi yok)
}

_BEKLENEN = {'satir_sayisi': 7}


def _gonder(endpoint, payload, log=None):
    """Tek bir istegi 8800 ofis proxy'sine POST eder; yaniti (dict/list/metin) doner."""
    _govde = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _istek = urllib.request.Request(OFFICE_BASE + "/" + endpoint, data=_govde, method="POST")
    for _ad, _deg in COMMON_HEADERS.items():
        _istek.add_header(_ad, _deg)
    try:
        with urllib.request.urlopen(_istek, timeout=90) as _y:
            _metin = _y.read().decode("utf-8", "replace")
            if log: log(f"{endpoint} -> HTTP {_y.status}")
    except urllib.error.HTTPError as _e:
        if log: log(f"{endpoint} -> HTTP {_e.code}")
        return {"_hata": f"HTTP {_e.code}"}
    except urllib.error.URLError as _e:
        return {"_hata": f"Ofise ulasilamadi ({getattr(_e, 'reason', _e)})."}
    try:
        return json.loads(_metin)
    except Exception:
        return _metin


def calistir(girdi=None, log_fn=None):
    """Yakalanan oturumu sirayla tekrar gonderir. "zincir" alanlar adimlar arasi beslenir.
    Doner: SON adimin ham yaniti."""
    girdi = girdi or {}
    log = log_fn or (lambda *a, **k: None)
    log('Adim 1/27: Dosya Sorgula -> getDosyaAramaParameters.ajx')
    payload_1 = {
    }
    adim1_sonuc = _gonder('getDosyaAramaParameters.ajx', payload_1, log)
    log('Adim 2/27: Dosya Sorgula -> yargiBirimleriSorgula_brd.ajx')
    payload_2 = {
        'yargiTuru': '0',
    }
    adim2_sonuc = _gonder('yargiBirimleriSorgula_brd.ajx', payload_2, log)
    log('Adim 3/27: İcra -> yargiBirimleriSorgula_brd.ajx')
    payload_3 = {
        'yargiTuru': '2',
    }
    adim3_sonuc = _gonder('yargiBirimleriSorgula_brd.ajx', payload_3, log)
    log('Adim 4/27: İCRA DAİRESİ -> avukat_mahkemeleri_sorgula.ajx')
    payload_4 = {
        'yargiTuru': '2',
        'yargiBirimi': '1101',
        'dosyaKapaliMi': False,
    }
    adim4_sonuc = _gonder('avukat_mahkemeleri_sorgula.ajx', payload_4, log)
    log('Adim 5/27: Sorgula -> search_phrase_detayli.ajx')
    payload_5 = {
        'dosyaDurumKod': 0,
        'pageSize': 500,
        'pageNumber': 1,
        'dosyaYil': 2026,
        'dosyaSira': 98353,
        'birimId': '',
        'birimTuru2': '1101',
        'birimTuru3': '2',
    }
    adim5_sonuc = _gonder('search_phrase_detayli.ajx', payload_5, log)
    log('Adim 6/27:  -> dosya_islem_turleri_sorgula_brd.ajx')
    payload_6 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim6_sonuc = _gonder('dosya_islem_turleri_sorgula_brd.ajx', payload_6, log)
    log('Adim 7/27:  -> dosyaAyrintiBilgileri_brd.ajx')
    payload_7 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim7_sonuc = _gonder('dosyaAyrintiBilgileri_brd.ajx', payload_7, log)
    log('Adim 8/27: Dosya Bilgileri\nDosya Hesabı\nTaraf Bilgileri\nEvrak\nSafahat\nEvrak Gönderme\nÖdeme\n -> dosya_borclu_list.ajx')
    payload_8 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim8_sonuc = _gonder('dosya_borclu_list.ajx', payload_8, log)
    log('Adim 9/27: Dosya Bilgileri\nDosya Hesabı\nTaraf Bilgileri\nEvrak\nSafahat\nEvrak Gönderme\nÖdeme\n -> dosyadaUcuncuSahisVekiliMi.ajx')
    payload_9 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim9_sonuc = _gonder('dosyadaUcuncuSahisVekiliMi.ajx', payload_9, log)
    log('Adim 10/27: GÖKMEN SABRİ BOYACIOĞLU - 11852544314 -> dosya_icra_kesinlesme_bilgileri_brd.ajx')
    payload_10 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'kisiKurumId': adim8_sonuc[0]['kisiKurumId'],  # zincir: adim 8 yanitindan
    }
    adim10_sonuc = _gonder('dosya_icra_kesinlesme_bilgileri_brd.ajx', payload_10, log)
    log('Adim 11/27: GÖKMEN SABRİ BOYACIOĞLU - 11852544314 -> getPortalAvukatTalepList.ajx')
    payload_11 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'islemTuru': 'haciz',
    }
    adim11_sonuc = _gonder('getPortalAvukatTalepList.ajx', payload_11, log)
    log('Adim 12/27: Sorgula -> borclu_bilgileri_goruntule_mernis.ajx')
    payload_12 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'kisiKurumId': adim8_sonuc[0]['kisiKurumId'],  # zincir: adim 8 yanitindan
    }
    adim12_sonuc = _gonder('borclu_bilgileri_goruntule_mernis.ajx', payload_12, log)
    log('Adim 13/27: Dosya Bilgileri\nDosya Hesabı\nTaraf Bilgileri\nEvrak\nSafahat\nEvrak Gönderme\nÖdeme\n -> listDosyaEvraklarPageTotal.ajx')
    payload_13 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'pageNumber': 1,
    }
    adim13_sonuc = _gonder('listDosyaEvraklarPageTotal.ajx', payload_13, log)
    log('Adim 14/27: Dosya Bilgileri\nDosya Hesabı\nTaraf Bilgileri\nEvrak\nSafahat\nEvrak Gönderme\nÖdeme\n -> list_dosya_evraklar.ajx')
    payload_14 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'pageNumber': 1,
    }
    adim14_sonuc = _gonder('list_dosya_evraklar.ajx', payload_14, log)
    log('Adim 15/27: Kapalı Tebligat 30/07/2026 -> getDocViewerParameters.ajx')
    payload_15 = {
    }
    adim15_sonuc = _gonder('getDocViewerParameters.ajx', payload_15, log)
    log('Adim 16/27: Kapalı Tebligat 30/07/2026 -> view_document_brd.uyap')
    # (bu adimin cozumlenebilir payload'i yok)
    adim16_sonuc = None
    log('Adim 17/27: Kapalı Tebligat 30/07/2026 -> 6d04b2d2-7019-4362-9613-353e6447a176')
    # (bu adimin cozumlenebilir payload'i yok)
    adim17_sonuc = None
    log('Adim 18/27: Tebligat Talepleri -> getPortalAvukatTalepList.ajx')
    payload_18 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'islemTuru': 'tebligat',
    }
    adim18_sonuc = _gonder('getPortalAvukatTalepList.ajx', payload_18, log)
    log('Adim 19/27: Tebligat Talepleri -> borcluVekilList.ajx')
    payload_19 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'kisiKurumId': adim12_sonuc['borcluBilgileri']['kisiKurumId'],  # zincir: adim 12 yanitindan
    }
    adim19_sonuc = _gonder('borcluVekilList.ajx', payload_19, log)
    log('Adim 20/27: İcra/Ödeme Emrinin Tebliğe Çıkartılması -> getPortalAvukatTalepAdresTuruList_brd.ajx')
    payload_20 = {
    }
    adim20_sonuc = _gonder('getPortalAvukatTalepAdresTuruList_brd.ajx', payload_20, log)
    log('Adim 21/27: İcra/Ödeme Emrinin Tebliğe Çıkartılması -> getPortalAvukatTalepTebligatTuruList_brd.ajx')
    payload_21 = {
    }
    adim21_sonuc = _gonder('getPortalAvukatTalepTebligatTuruList_brd.ajx', payload_21, log)
    log('Adim 22/27: Sorgula -> getIcraTebligatTalebiAdresSorgula.ajx')
    payload_22 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'kisiKurumId': adim12_sonuc['borcluBilgileri']['kisiKurumId'],  # zincir: adim 12 yanitindan
        'adresTuru': 'ADRTR00008',
    }
    adim22_sonuc = _gonder('getIcraTebligatTalebiAdresSorgula.ajx', payload_22, log)
    log('Adim 23/27: Talep Ekle -> odeme_tipleri_sorgula.ajx')
    payload_23 = {
    }
    adim23_sonuc = _gonder('odeme_tipleri_sorgula.ajx', payload_23, log)
    log('Adim 24/27: Talep Evrakı Oluştur -> getIcraTalepEvrakHazirla.uyap')
    payload_24 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
        'filename': adim14_sonuc['tumEvraklar']['2026/98353(İcra Dosyası)'][1]['gonderenDosyaNo'],  # zincir: adim 14 yanitindan
        'kisiKurumId': adim12_sonuc['borcluBilgileri']['kisiKurumId'],  # zincir: adim 12 yanitindan
        'talepBilgileri': '[{"grupKodu":1,"talepKodu":8,"talepAdi":"İcra/Ödeme Emrinin Tebliğe Çıkartılması","talepKisaAdi":"İcra/Ödeme Emrinin Tebliğe Çıkartılması","talepMasrafi":0,"className":"AvukatTalepTebligatOdemeEmriDVO","postaMasrafId":0,"dosyaDurum":"A","tebligatTuruAciklama":"T.K.21/2 Şerhli","tebligatTuru":2,"adresTuru":1,"adresTuruAciklama":"Mernis Adresi","adres":"VAR","masraf":265,"fields":[{"id":"tebligatTuruAciklama","title":"Tebligat Türü"},{"id":"adresTuruAciklama","title":"Adres Türü"},{"id":"adres","title":"Adres"}],"id":0}]',
    }
    adim24_sonuc = _gonder('getIcraTalepEvrakHazirla.uyap', payload_24, log)
    log('Adim 25/27: Talep Evrakı Oluştur -> d0b66981-ea37-4ad3-bea1-a2fd4b225fff')
    # (bu adimin cozumlenebilir payload'i yok)
    adim25_sonuc = None
    log('Adim 26/27: Borçlu\nSorgular\nTalep Gönder\n\n\nAdres Türü\nSorgula\nAdres Bilgisi\nTebligat Türü\nTa -> sign_udf')
    # (bu adimin cozumlenebilir payload'i yok)
    adim26_sonuc = None
    log('Adim 27/27: Evrak Gönder -> avukatIcraTalepEvrakiGonder.ajx')
    # (bu adimin cozumlenebilir payload'i yok)
    adim27_sonuc = None
    return adim27_sonuc


def _kendini_dogrula(log_fn=None):
    """Tum akisi CANLI calistirir; son adimin yanitini yakalamayla kiyaslar."""
    try:
        sonuc = calistir(ORNEK_GIRDI, log_fn)
    except Exception as e:
        return {"ok": False, "mesaj": f"Akis hata verdi: {e}"}
    if isinstance(sonuc, dict) and sonuc.get("_hata"):
        return {"ok": False, "mesaj": sonuc["_hata"]}
    n = len(sonuc) if isinstance(sonuc, (list, dict, str)) else 0
    bekl = _BEKLENEN.get("satir_sayisi")
    if bekl is None:
        return {"ok": True, "mesaj": f"Akis tamamlandi, yanit alindi (uzunluk {n})."}
    return {"ok": (n > 0) if bekl > 0 else True,
            "mesaj": f"Akis tamamlandi: uzunluk {n} (yakalamada {bekl})."}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _sonuc = calistir(ORNEK_GIRDI, log_fn=print)
    print(json.dumps(_sonuc, ensure_ascii=False, indent=2, default=str)
          if isinstance(_sonuc, (dict, list)) else _sonuc)
