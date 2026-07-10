# -*- coding: utf-8 -*-
"""
OTURUM AKISI — İhtiyati Haciz
=============================
Logger'in "Session'u module cevir" ile OTOMATIK uretildi. Yakalanan 24
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

_BEKLENEN = {'satir_sayisi': 17}


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
    log('Adim 1/24: Dava Açılış İşlemleri -> getNatsParameters.ajx')
    payload_1 = {
    }
    adim1_sonuc = _gonder('getNatsParameters.ajx', payload_1, log)
    log('Adim 2/24: Dava Açılış İşlemleri -> get_avukat_id.ajx')
    payload_2 = {
    }
    adim2_sonuc = _gonder('get_avukat_id.ajx', payload_2, log)
    log('Adim 3/24: Hukuk Dava Aç -> illerIlcelerGetir.ajx')
    payload_3 = {
    }
    adim3_sonuc = _gonder('illerIlcelerGetir.ajx', payload_3, log)
    log('Adim 4/24: İZMİR -> hukuk_tevzi_burolari.ajx')
    payload_4 = {
        'ilKodu': 35,
    }
    adim4_sonuc = _gonder('hukuk_tevzi_burolari.ajx', payload_4, log)
    log('Adim 5/24: İzmir Hukuk Mahkemeleri Tevzi Bürosu -> hukuk_mahkeme_turleri.ajx')
    payload_5 = {
        'birimId': adim4_sonuc[0]['birimId'],  # zincir: adim 4 yanitindan
    }
    adim5_sonuc = _gonder('hukuk_mahkeme_turleri.ajx', payload_5, log)
    log('Adim 6/24: UYAP Avukat Portal\nİçerik Alanına Git\nAna Sayfa\nSık Kullanılanlar\n Dosya Sorgula -> hukukDavaTurleri.ajx')
    # (bu adimin cozumlenebilir payload'i yok)
    adim6_sonuc = None
    log('Adim 7/24: İhtiyati Haciz (Finans) -> hukuk_dava_sebebi.ajx')
    payload_7 = {
        'tevziBuroId': adim4_sonuc[0]['birimId'],  # zincir: adim 4 yanitindan
        'mahkemeTurKodu': '0902',
        'davaTurId': adim6_sonuc[14]['davaTurId'],  # zincir: adim 6 yanitindan
    }
    adim7_sonuc = _gonder('hukuk_dava_sebebi.ajx', payload_7, log)
    log('Adim 8/24: İleri -> hukuk_nafaka_dosyasi_kontrol.ajx')
    payload_8 = {
        'davaTurId': adim6_sonuc[14]['davaTurId'],  # zincir: adim 6 yanitindan
    }
    adim8_sonuc = _gonder('hukuk_nafaka_dosyasi_kontrol.ajx', payload_8, log)
    log('Adim 9/24: İleri -> baro_listesi_sorgula.ajx')
    payload_9 = {
    }
    adim9_sonuc = _gonder('baro_listesi_sorgula.ajx', payload_9, log)
    log('Adim 10/24: İleri -> tarafSifatlari.ajx')
    payload_10 = {
        'kod': {'tablo': 'BLTR2', 'kod': '0902', 'kodAciklama': 'ASLİYE TİCARET MAHKEMESİ'},
    }
    adim10_sonuc = _gonder('tarafSifatlari.ajx', payload_10, log)
    log('Adim 11/24: DAVACI -> get_adres_turleri_by_taraf.ajx')
    payload_11 = {
    }
    adim11_sonuc = _gonder('get_adres_turleri_by_taraf.ajx', payload_11, log)
    log('Adim 12/24: Sorgula -> kurumSorgula.ajx')
    payload_12 = {
        'mersisNo': '0998006967505633',
    }
    adim12_sonuc = _gonder('kurumSorgula.ajx', payload_12, log)
    log('Adim 13/24: DAVALI -> get_adres_turleri_by_taraf.ajx')
    payload_13 = {
    }
    adim13_sonuc = _gonder('get_adres_turleri_by_taraf.ajx', payload_13, log)
    log('Adim 14/24: Sorgula -> kisiSorgula.ajx')
    payload_14 = {
        'tcKimlikNo': '21161038282',
        'tarafSifati': 2,
    }
    adim14_sonuc = _gonder('kisiSorgula.ajx', payload_14, log)
    log('Adim 15/24: DAVALI -> get_adres_turleri_by_taraf.ajx')
    payload_15 = {
    }
    adim15_sonuc = _gonder('get_adres_turleri_by_taraf.ajx', payload_15, log)
    log('Adim 16/24: Sorgula -> kisiSorgula.ajx')
    payload_16 = {
        'tcKimlikNo': '27445828730',
        'tarafSifati': 2,
    }
    adim16_sonuc = _gonder('kisiSorgula.ajx', payload_16, log)
    log('Adim 17/24: Hesapla -> hukukHarcHesaplamaIslemleri.ajx')
    payload_17 = {
        'HarcBilgileri': '{"davaTurId":32256,"davaEsasDegeri":"","faizDegeri":"","tarafSayisi":3,"vekilSayisi":1,"tedbirTalebi":false,"kod":"0902","tablo":"BLTR2","birimId":"3002362","dosyaTurKod":14}',
        'TarafList': '1,1,0,13071619|2,0,0,261937665,FA***,ÖZ*********,MU******,*5/*6/1967,K,1|2,0,0,108946770,Fİ***,BO****,ŞE***,*4/*0/1991,E,1',
        'muvekkil': '0',
        'adliMuzaheret': '',
    }
    adim17_sonuc = _gonder('hukukHarcHesaplamaIslemleri.ajx', payload_17, log)
    log('Adim 18/24: İleri -> isFinansDavasi.ajx')
    payload_18 = {
        'DosyaBilgileri': '{"ilKodu":35,"davaTurId":32256,"tevziBurosuBirimId":"3002362","mahkemeId":"0902","dosyaTurKod":14,"tablo":"BLTR2","kod":"0902","talepBilgileri":"","talepBilgileriText":""}',
        'TarafList': '1,1,0,13071619|2,0,0,261937665,FA***,ÖZ*********,MU******,*5/*6/1967,K,1|2,0,0,108946770,Fİ***,BO****,ŞE***,*4/*0/1991,E,1',
    }
    adim18_sonuc = _gonder('isFinansDavasi.ajx', payload_18, log)
    log('Adim 19/24: Tevzi Numarası Al -> hukuk_dava_tevzi_islemleri.ajx')
    payload_19 = {
        'DosyaBilgileri': '{"ilKodu":35,"davaTurId":32256,"tevziBurosuBirimId":"3002362","mahkemeId":"0902","dosyaTurKod":14,"tablo":"BLTR2","kod":"0902","yevmiyeTarihi":" - ","talepBilgileri":""}',
        'TarafList': '1,1,0,13071619|2,0,0,261937665,FA***,ÖZ*********,MU******,*5/*6/1967,K,1|2,0,0,108946770,Fİ***,BO****,ŞE***,*4/*0/1991,E,1',
        'HarcBilgileri': '{"davaTurId":32256,"davaEsasDegeri":"","faizDegeri":"","tarafSayisi":3,"vekilSayisi":1,"tedbirTalebi":false,"kod":"0902","tablo":"BLTR2","birimId":"3002362","dosyaTurKod":14}',
        'muvekkil': '0',
    }
    adim19_sonuc = _gonder('hukuk_dava_tevzi_islemleri.ajx', payload_19, log)
    log('Adim 20/24: Evrak Gönder -> davaAcilisEvrakGonderme_brd.ajx')
    # (bu adimin cozumlenebilir payload'i yok)
    adim20_sonuc = None
    log('Adim 21/24: Tamam -> odeme_tipleri_sorgula.ajx')
    payload_21 = {
    }
    adim21_sonuc = _gonder('odeme_tipleri_sorgula.ajx', payload_21, log)
    log('Adim 22/24: Tamam -> dosya_harc_masraf_hesabi.ajx')
    payload_22 = {
        'dosyaId': adim19_sonuc['dosyaId'],  # zincir: adim 19 yanitindan
    }
    adim22_sonuc = _gonder('dosya_harc_masraf_hesabi.ajx', payload_22, log)
    log('Adim 23/24: Ödeme Yap -> davaAcilisOdemeIslemleri_brd.ajx')
    payload_23 = {
        'dosyaId': adim19_sonuc['dosyaId'],  # zincir: adim 19 yanitindan
        'odemeTipi': '7',
        'vakifbankHesapBilgileri': 'null',
        'harcMasrafTipi': '',
        'harcMasrafList': '',
        'postaMasraflariList': '',
    }
    adim23_sonuc = _gonder('davaAcilisOdemeIslemleri_brd.ajx', payload_23, log)
    log('Adim 24/24: Dava Aç -> hukuk_dava_acilis_tamamla_brd.ajx')
    payload_24 = {
        'tevziDosyaId': adim19_sonuc['dosyaId'],  # zincir: adim 19 yanitindan
        'birimId': adim22_sonuc[0][0]['hmSahibiBirimID'],  # zincir: adim 22 yanitindan
    }
    adim24_sonuc = _gonder('hukuk_dava_acilis_tamamla_brd.ajx', payload_24, log)
    return adim24_sonuc


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
