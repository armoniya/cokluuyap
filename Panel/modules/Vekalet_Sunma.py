# -*- coding: utf-8 -*-
"""
OTURUM AKISI — Vekalet Sunma
============================
Logger'in "Session'u module cevir" ile OTOMATIK uretildi. Yakalanan 12
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

_BEKLENEN = {'satir_sayisi': 1}


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
    log('Adim 1/12: Dosya Sorgula -> getDosyaAramaParameters.ajx')
    payload_1 = {
    }
    adim1_sonuc = _gonder('getDosyaAramaParameters.ajx', payload_1, log)
    log('Adim 2/12: Dosya Sorgula -> yargiBirimleriSorgula_brd.ajx')
    payload_2 = {
        'yargiTuru': '0',
    }
    adim2_sonuc = _gonder('yargiBirimleriSorgula_brd.ajx', payload_2, log)
    log('Adim 3/12: İcra -> yargiBirimleriSorgula_brd.ajx')
    payload_3 = {
        'yargiTuru': '2',
    }
    adim3_sonuc = _gonder('yargiBirimleriSorgula_brd.ajx', payload_3, log)
    log('Adim 4/12: İCRA DAİRESİ -> avukat_mahkemeleri_sorgula.ajx')
    payload_4 = {
        'yargiTuru': '2',
        'yargiBirimi': '1101',
        'dosyaKapaliMi': False,
    }
    adim4_sonuc = _gonder('avukat_mahkemeleri_sorgula.ajx', payload_4, log)
    log('Adim 5/12: Sorgula -> search_phrase_detayli.ajx')
    payload_5 = {
        'dosyaDurumKod': 0,
        'pageSize': 500,
        'pageNumber': 1,
        'dosyaYil': 2018,
        'dosyaSira': 2326,
        'birimId': '',
        'birimTuru2': '1101',
        'birimTuru3': '2',
    }
    adim5_sonuc = _gonder('search_phrase_detayli.ajx', payload_5, log)
    log('Adim 6/12:  -> dosya_islem_turleri_sorgula_brd.ajx')
    payload_6 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim6_sonuc = _gonder('dosya_islem_turleri_sorgula_brd.ajx', payload_6, log)
    log('Adim 7/12:  -> dosyaAyrintiBilgileri_brd.ajx')
    payload_7 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim7_sonuc = _gonder('dosyaAyrintiBilgileri_brd.ajx', payload_7, log)
    log('Adim 8/12: Dosya Bilgileri\nDosya Hesabı\nTaraf Bilgileri\nEvrak\nSafahat\nEvrak Gönderme\nÖdeme\n -> dosya_gonderilecek_evrak_listesi_brd.ajx')
    payload_8 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim8_sonuc = _gonder('dosya_gonderilecek_evrak_listesi_brd.ajx', payload_8, log)
    log('Adim 9/12: Dosya Bilgileri\nDosya Hesabı\nTaraf Bilgileri\nEvrak\nSafahat\nEvrak Gönderme\nÖdeme\n -> odeme_alinacak_evrak_listesi.ajx')
    payload_9 = {
        'dosyaId': adim5_sonuc[0][0]['dosyaId'],  # zincir: adim 5 yanitindan
    }
    adim9_sonuc = _gonder('odeme_alinacak_evrak_listesi.ajx', payload_9, log)
    log('Adim 10/12: Evrak Ekle -> odeme_tipleri_sorgula.ajx')
    payload_10 = {
    }
    adim10_sonuc = _gonder('odeme_tipleri_sorgula.ajx', payload_10, log)
    log('Adim 11/12: Evrak Ekle -> posta_masraflari.ajx')
    payload_11 = {
        'dosyaTurKod': 35,
        'birimId': adim5_sonuc[0][0]['birimId'],  # zincir: adim 5 yanitindan
    }
    adim11_sonuc = _gonder('posta_masraflari.ajx', payload_11, log)
    log('Adim 12/12: Evrak Gönder -> evrakGonder_brd.ajx')
    # (bu adimin cozumlenebilir payload'i yok)
    adim12_sonuc = None
    return adim12_sonuc


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
