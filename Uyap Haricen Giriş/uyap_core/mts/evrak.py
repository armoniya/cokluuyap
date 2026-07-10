"""
uyap_core.mts.evrak — UYAP MTS evrak gönderme şablonları (tarayıcısız)
======================================================================
Kararlı/mts_evrak_yukle.py'deki evrak metadata şablonları ve `items` JSON kurucusu.
Burada SADECE şablon + items_kur var; gerçek HTTP gönderimi iş kuyruğu işleyicisinde
ctx.uyap(..., files=...) ile (uyap_proxy.gw oturumu üzerinden) yapılır.

Orijinalden fark: dosyalar artık diskten yol ile DEĞİL, bellekteki baytlarla
gönderildiği için items_kur dosya YOLU yerine dosya ADI (filename) alır.
"""

import os
import json
import time as _time


URL_PATH = "davaAcilisEvrakGonderme_brd.ajx"   # uyap_proxy.UYAP_BASE'e göre görece yol

# ag_kaydi.log'daki gerçek "items" yapısından alınan, türe özgü evrakTuruOptionDVO
# tanımları. UYAP backend'i bu metadata'yı doğruladığı için birebir korunur.
EVRAK_TURU_SABLON = {
    "ICR_TAKIP_TLP": {
        "tur": "ICR_TAKIP_TLP",
        "label": "Takip Talebi",
        "mandatory": True,
        "max": 1,
        "ekEvrakMax": 5,
        "sablonTurKodu": "",
        "acceptTypeList": [".udf", ".UDF"],
        "ekEvrakAcceptTypeList": [".udf", ".pdf", ".jpg", ".png", ".jpeg", ".image/jpg",
                                  ".image/jpeg", ".tif", ".tiff", ".UDF", ".JPG", ".JPEG",
                                  ".PNG", ".PDF", ".TIF", ".TIFF"],
    },
    "CZM_VEKALETNAME": {
        "tur": "CZM_VEKALETNAME",
        "label": "Vekaletname",
        "mandatory": True,
        "max": 1,
        "ekEvrakMax": 5,
        "sablonTurKodu": "",
        "acceptTypeList": [".udf", ".pdf", ".jpg", ".png", ".jpeg", ".image/jpg",
                           ".image/jpeg", ".tif", ".tiff", ".UDF", ".PDF", ".JPG",
                           ".JPEG", ".PNG", ".TIF", ".TIFF"],
        "ekEvrakAcceptTypeList": [".udf", ".pdf", ".jpg", ".png", ".jpeg", ".image/jpg",
                                  ".image/jpeg", ".tif", ".tiff", ".UDF", ".JPG", ".JPEG",
                                  ".PNG", ".PDF", ".TIF", ".TIFF"],
    },
    "MTS_TAKIBIN_DAYANAGI": {
        "tur": "MTS_TAKIBIN_DAYANAGI",
        "label": "Takibin Dayanağı",
        "mandatory": True,
        "max": 1,
        "ekEvrakMax": 5,
        "sablonTurKodu": "",
        "acceptTypeList": [".udf", ".pdf", ".jpg", ".png", ".jpeg", ".image/jpg",
                           ".image/jpeg", ".tif", ".tiff", ".UDF", ".PDF", ".JPG",
                           ".JPEG", ".PNG", ".TIF", ".TIFF"],
        "ekEvrakAcceptTypeList": [".udf", ".pdf", ".jpg", ".png", ".jpeg", ".image/jpg",
                                  ".image/jpeg", ".tif", ".tiff", ".UDF", ".JPG", ".JPEG",
                                  ".PNG", ".PDF", ".TIF", ".TIFF"],
    },
}

# Standart MTS takip açılışı evrak sırası (zorunlu üç evrak)
VARSAYILAN_EVRAK_SIRASI = ["ICR_TAKIP_TLP", "CZM_VEKALETNAME", "MTS_TAKIBIN_DAYANAGI"]


def mime_belirle(filename):
    uzanti = os.path.splitext(filename or "")[1].lower()
    return {
        ".pdf": "application/pdf",
        ".udf": "application/octet-stream",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(uzanti, "application/octet-stream")


def items_kur(evraklar):
    """UYAP'ın beklediği `items` JSON dizisini kurar.

    evraklar: [{"tur": "ICR_TAKIP_TLP", "filename": "Takip_Talebi.udf"}, ...]
              (sıra önemli: fileId 1,2,3... → file_1, file_2, file_3).
    Dönüş: (items_json_str, [(alan_adi, filename), ...]).
    """
    items = []
    dosya_alanlari = []
    for i, ev in enumerate(evraklar, 1):
        tur = ev["tur"]
        dosya_adi = ev.get("filename") or os.path.basename(ev.get("yol", "")) or f"evrak_{i}"
        if tur not in EVRAK_TURU_SABLON:
            raise ValueError(f"Bilinmeyen evrak türü: {tur}. "
                             f"Geçerli türler: {list(EVRAK_TURU_SABLON)}")
        sablon = EVRAK_TURU_SABLON[tur]
        items.append({
            "id": int(_time.time() * 1000) + i,
            "evrakTuruOptionDVO": sablon,
            "tur": tur,
            "turAciklama": sablon["label"],
            "mandatory": sablon["mandatory"],
            "file": {},
            "path": f"C:/fakepath/{dosya_adi}",
            "kullaniciEvrakAciklama": "",
            "parentId": -1,
            "isVekalet": False,
            "isVekaletBilgiFormu": False,
            "fileId": i,
        })
        dosya_alanlari.append((f"file_{i}", dosya_adi))
    return json.dumps(items, ensure_ascii=False), dosya_alanlari
