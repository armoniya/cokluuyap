# -*- coding: utf-8 -*-
"""
OTURUM AKISI — XML TAKİP AÇILIŞ 2807
====================================
Logger'in "Session'u module cevir" ile OTOMATIK uretildi. Yakalanan 11
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
    log('Adim 1/11: Dava Açılış İşlemleri -> get_avukat_id.ajx')
    payload_1 = {
    }
    adim1_sonuc = _gonder('get_avukat_id.ajx', payload_1, log)
    log('Adim 2/11: İcra Takip Açılış - XML -> illerIlcelerGetir.ajx')
    payload_2 = {
    }
    adim2_sonuc = _gonder('illerIlcelerGetir.ajx', payload_2, log)
    log('Adim 3/11: İZMİR -> icraTakipAdliyeler.ajx')
    payload_3 = {
        'ilKodu': 35,
    }
    adim3_sonuc = _gonder('icraTakipAdliyeler.ajx', payload_3, log)
    log('Adim 4/11: İzmir Adliye -> tevziSiraTipleri.ajx')
    payload_4 = {
        'birimId': adim3_sonuc[6]['adliyeBirimID'],  # zincir: adim 3 yanitindan
    }
    adim4_sonuc = _gonder('tevziSiraTipleri.ajx', payload_4, log)
    log('Adim 5/11: İcra Takip Başlat -> icra_takip_tevzi_islemleri.ajx')
    # (bu adimin cozumlenebilir payload'i yok)
    adim5_sonuc = None
    log('Adim 6/11: İcra Takip Tamamlanmayan Dosyalar -> tamamlanmayanDosyalar_brd.ajx')
    payload_6 = {
        'dosyaTurKod': 35,
    }
    adim6_sonuc = _gonder('tamamlanmayanDosyalar_brd.ajx', payload_6, log)
    log('Adim 7/11: Takip Talebini İndir -> icraTakipTalebiIndir.uyap')
    # (bu adimin cozumlenebilir payload'i yok)
    adim7_sonuc = None
    log('Adim 8/11: Takip Talebini İndir -> 40e3e3dc-38a8-4924-bb5a-c5c16dc0d0f9')
    # (bu adimin cozumlenebilir payload'i yok)
    adim8_sonuc = None
    log('Adim 9/11: Takip Talebi -> sign_udf')
    # (bu adimin cozumlenebilir payload'i yok)
    adim9_sonuc = None
    log('Adim 10/11: Evrak Gönder -> davaAcilisEvrakGonderme_brd.ajx')
    # (bu adimin cozumlenebilir payload'i yok)
    adim10_sonuc = None
    log('Adim 11/11: Evrak Gönder -> tamamlanmayanDosyalar_brd.ajx')
    payload_11 = {
        'dosyaTurKod': 35,
    }
    adim11_sonuc = _gonder('tamamlanmayanDosyalar_brd.ajx', payload_11, log)
    return adim11_sonuc


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
