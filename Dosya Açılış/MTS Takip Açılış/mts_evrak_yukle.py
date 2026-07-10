"""
mts_evrak_yukle.py
==================
UYAP MTS takip açılışında son adım olan EVRAK GÖNDERME (davaAcilisEvrakGonderme_brd.ajx)
isteğini dinamik olarak kurup gönderen yardımcı modül.

ag_kaydi.log'dan çözülen gerçek istek yapısı:
  POST https://avukat.uyap.gov.tr/davaAcilisEvrakGonderme_brd.ajx
  multipart/form-data alanları:
    file_1, file_2, ...  -> dosya içerikleri (imzalı UDF + ekler)
    items                -> her evrakın metadata'sı (tür/fileId/path...) JSON dizisi
    dosyaId              -> mtsTakipTalebiOlustur_brd.ajx yanıtından gelen takip id

items içindeki her öğenin "fileId" alanı, "file_<N>" alanı ile eşleşir
(fileId:1 -> file_1, fileId:2 -> file_2 ...).

Bu modül Playwright'in APIRequestContext'i (context.request) üzerinden, oturum
çerezleriyle gerçek isteği kurar. Böylece tarayıcıda manuel dosya seçmeye gerek
kalmadan, dinamik olarak seçilen belgeler yüklenebilir.
"""

import os
import json
import base64


# --- EVRAK TÜRÜ ŞABLONLARI ---
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

URL = "https://avukat.uyap.gov.tr/davaAcilisEvrakGonderme_brd.ajx"


def _mime_belirle(yol):
    uzanti = os.path.splitext(yol)[1].lower()
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
    """
    evraklar: [{"tur": "ICR_TAKIP_TLP", "yol": "C:/.../Takip_Talebi.udf"}, ...]
    sırayla fileId 1,2,3... atanır ve UYAP'ın beklediği items JSON dizisini kurar.
    Geriye (items_json_str, [(alan_adi, yol), ...]) döner.
    """
    import time as _t
    items = []
    dosya_alanlari = []
    for i, ev in enumerate(evraklar, 1):
        tur = ev["tur"]
        yol = ev["yol"]
        if tur not in EVRAK_TURU_SABLON:
            raise ValueError(f"Bilinmeyen evrak türü: {tur}. "
                             f"Geçerli türler: {list(EVRAK_TURU_SABLON)}")
        sablon = EVRAK_TURU_SABLON[tur]
        dosya_adi = os.path.basename(yol)
        items.append({
            # id: tarayıcıda Date.now() + offset; backend için benzersiz olması yeterli
            "id": int(_t.time() * 1000) + i,
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
        dosya_alanlari.append((f"file_{i}", yol))

    return json.dumps(items, ensure_ascii=False), dosya_alanlari


def evrak_gonder(context, dosya_id, evraklar, dogrula=True):
    """
    UYAP'a evrakları gönderir.

    context   : Playwright BrowserContext (oturum çerezleri burada).
    dosya_id  : mtsTakipTalebiOlustur_brd.ajx yanıtından gelen dosyaId (tırnaklı string).
    evraklar  : [{"tur": "ICR_TAKIP_TLP", "yol": "C:/.../Takip_Talebi.udf"}, ...]
                Sıra önemli (file_1, file_2, ...). En az imzalı Takip Talebi UDF olmalı.
    dogrula   : True ise her dosyanın diskte var olduğunu kontrol eder.

    Geriye UYAP yanıt sözlüğünü döndürür: {"type": "success"/"error", "message": ...}
    """
    if isinstance(dosya_id, str):
        dosya_id = dosya_id.strip().strip('"').strip("'")

    if dogrula:
        for ev in evraklar:
            if not os.path.exists(ev["yol"]):
                raise FileNotFoundError(f"Evrak bulunamadı: {ev['yol']}")

    items_json, dosya_alanlari = items_kur(evraklar)

    # Playwright multipart: dosyalar için {name, mimeType, buffer}; metin için düz string
    multipart = {}
    for alan_adi, yol in dosya_alanlari:
        with open(yol, "rb") as f:
            icerik = f.read()
        multipart[alan_adi] = {
            "name": os.path.basename(yol),
            "mimeType": _mime_belirle(yol),
            "buffer": icerik,
        }
    multipart["items"] = items_json
    multipart["dosyaId"] = dosya_id

    print(f"📤 Evrak gönderiliyor ({len(dosya_alanlari)} dosya) -> {URL}")
    for alan_adi, yol in dosya_alanlari:
        print(f"   {alan_adi}: {os.path.basename(yol)} ({os.path.getsize(yol)} bayt)")

    response = context.request.post(URL, multipart=multipart)

    durum = response.status
    try:
        govde = response.text()
    except Exception:
        govde = ""

    try:
        sonuc = json.loads(govde) if govde else {}
    except Exception:
        sonuc = {"type": "unknown", "message": govde}

    print(f"🟢 DURUM: {durum}  | YANIT: {sonuc}")
    return sonuc


def evrak_gonder_page(page, dosya_id, evraklar, dogrula=True):
    """
    evrak_gonder ile AYNI işi yapar; ancak isteği sayfanın KENDİ fetch'i üzerinden
    (page.evaluate ile) gönderir. Böylece tarayıcı sayfa bağlamı (referer, origin,
    oturum durumu) tam korunur — UYAP'ın evrak gönderme akışı bu bağlamı bekler.
    mts_takip_acan_api.py gibi page üzerinden çalışan akışlar için bunu kullanın.

    page      : Playwright Page (UYAP sayfası açık ve oturum aktif olmalı).
    dosya_id  : mtsTakipTalebiOlustur_brd.ajx yanıtından gelen dosyaId.
    evraklar  : [{"tur": "ICR_TAKIP_TLP", "yol": "..."}, ...]  (sıra: file_1, file_2...).
    """
    if isinstance(dosya_id, str):
        dosya_id = dosya_id.strip().strip('"').strip("'")

    if dogrula:
        for ev in evraklar:
            if not os.path.exists(ev["yol"]):
                raise FileNotFoundError(f"Evrak bulunamadı: {ev['yol']}")

    items_json, dosya_alanlari = items_kur(evraklar)

    # Dosyaları base64 ile sayfa tarafına taşıyıp orada Blob/FormData kurarız.
    dosyalar = []
    for alan_adi, yol in dosya_alanlari:
        with open(yol, "rb") as f:
            icerik = f.read()
        dosyalar.append({
            "alan": alan_adi,
            "filename": os.path.basename(yol),
            "mime": _mime_belirle(yol),
            "base64": base64.b64encode(icerik).decode("ascii"),
        })

    print(f"📤 Evrak gönderiliyor (sayfa fetch, {len(dosyalar)} dosya) -> {URL}")
    for d in dosyalar:
        print(f"   {d['alan']}: {d['filename']}")

    js = """
    async (args) => {
        const { url, dosyaId, items, dosyalar } = args;
        const fd = new FormData();
        for (const d of dosyalar) {
            const bin = atob(d.base64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const blob = new Blob([bytes], { type: d.mime });
            fd.append(d.alan, blob, d.filename);
        }
        fd.append('items', items);
        fd.append('dosyaId', dosyaId);
        const r = await fetch(url, { method: 'POST', body: fd });
        const text = await r.text();
        return { status: r.status, body: text };
    }
    """
    sonuc = page.evaluate(js, {
        "url": URL,
        "dosyaId": dosya_id,
        "items": items_json,
        "dosyalar": dosyalar,
    })

    durum = sonuc.get("status")
    govde = sonuc.get("body", "")
    try:
        parsed = json.loads(govde) if govde else {}
    except Exception:
        parsed = {"type": "unknown", "message": govde}
    print(f"🟢 DURUM: {durum} | YANIT: {parsed}")
    return parsed


if __name__ == "__main__":
    # Bağımsız test/örnek kullanım. Gerçek dosyaId ve dosya yollarını doldurun.
    print("Bu bir modüldür; içe aktarıp evrak_gonder(context, dosya_id, evraklar) çağırın.")
    print("Örnek evraklar listesi:")
    ornek = [
        {"tur": "ICR_TAKIP_TLP", "yol": r"C:\...\Takip_Talebi.udf"},
        {"tur": "CZM_VEKALETNAME", "yol": r"C:\...\vekalet.pdf"},
        {"tur": "MTS_TAKIBIN_DAYANAGI", "yol": r"C:\...\mts_belgesi.pdf"},
    ]
    items_json, alanlar = items_kur(ornek)
    print("\nÜretilen items JSON:")
    print(json.dumps(json.loads(items_json), ensure_ascii=False, indent=2))
    print("\nDosya alanları:", alanlar)
