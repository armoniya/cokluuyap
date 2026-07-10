# -*- coding: utf-8 -*-
"""
MTS XML / Excel Dönüşümü
========================
mts_takip_acan.py'den AYRILDI (2026-06-26 modülerleştirme).

UYAP XML'i ile ara Excel arasındaki dönüşüm. Veri modellerini mts_veri'den
kullanır; playwright/win32/UYAP oturumu bağımlılığı YOKTUR.

İçerik:
  - xml_to_excel(xml_yolu)       -> XML'i ayrıştırıp .xlsx üretir
  - excel_to_takipler(excel_yolu) -> Excel'i okuyup Takip nesneleri döndürür
"""

from mts_veri import (
    Borclu, AlacakKalemi, Takip,
    _temiz, _virgullu, _tutar_to_float, _float_to_tutar,
    _ad_kanonik, kalemleri_birlestir, _tarih,
)

# Güvenilmeyen kullanıcı XML'i için üst sınır (DoS savunması, güvenlik raporu #12).
_XML_AZAMI_BAYT = 20 * 1024 * 1024  # 20 MB


def _guvenli_xml_parse(xml_yolu):
    """XML'i entity-expansion DoS'una ("billion laughs") ve XXE'ye karşı güvenli ayrıştırır.
    `defusedxml` varsa onu kullanır; yoksa boyut sınırı + DOCTYPE/DTD reddi uygular (DTD yoksa
    özel iç-varlık tanımı da olamaz → billion laughs/XXE etkisiz). Bkz. güvenlik raporu #12."""
    import os
    boyut = os.path.getsize(xml_yolu)
    if boyut > _XML_AZAMI_BAYT:
        raise ValueError(f"XML dosyası çok büyük ({boyut} bayt > {_XML_AZAMI_BAYT}); işlenmedi.")
    try:
        from defusedxml.ElementTree import parse as _safe_parse
        return _safe_parse(xml_yolu)
    except ImportError:
        pass
    with open(xml_yolu, "rb") as f:
        bas = f.read(65536)
    if b"<!DOCTYPE" in bas.upper():
        raise ValueError("XML DOCTYPE/DTD içeriyor; güvenlik gereği işlenmedi "
                         "(entity-expansion/XXE riski).")
    import xml.etree.ElementTree as ET
    return ET.parse(xml_yolu)


def xml_to_excel(xml_yolu):
    """UYAP XML'ini ayrıştırıp 'Yatay Alacak Listesi' Excel'i üretir.
    Her alacak kalemi için Ad / Tutar / Faiz_Oran / Faiz_Tur sütunlarını yazar.
    Üretilen .xlsx yolunu döndürür (xml ile aynı klasöre)."""
    import pandas as pd

    tree = _guvenli_xml_parse(xml_yolu)
    xml_root = tree.getroot()

    tum_satirlar = []
    dosya_sayac = 0

    for dosya in xml_root.findall(".//dosya"):
        dosya_sayac += 1
        talep_aciklama = dosya.get("alacaklininTalepEttigiHak", "")

        # --- Alacaklı (ad + IBAN) ---
        su_anki_alacakli = "Bulunamadı"
        su_anki_iban = ""
        taraflar = dosya.findall(".//taraf")
        for taraf in taraflar:
            rol_node = taraf.find("rolTur")
            if rol_node is not None and "ALACAKLI" in rol_node.get("Rol", "").upper():
                kkb = taraf.find("kisiKurumBilgileri")
                if kkb is not None:
                    su_anki_alacakli = kkb.get("ad", "")
                iban_node = taraf.find("iban")
                if iban_node is not None:
                    su_anki_iban = iban_node.get("no", "")
                    break

        # --- Borçlular (dikey: her biri ayrı satır olacak) ---
        bu_dosyadaki_borclular = []
        for taraf in taraflar:
            rol_node = taraf.find("rolTur")
            if rol_node is not None and "BORÇLU" in rol_node.get("Rol", "").upper():
                kkb = taraf.find("kisiKurumBilgileri")
                if kkb is not None:
                    tb = kkb.find("kisiTumBilgileri")
                    b_detay = {
                        "ad": tb.get("adi", "") if tb is not None else "",
                        "soyad": tb.get("soyadi", "") if tb is not None else "",
                        "tckn": tb.get("tcKimlikNo", "") if tb is not None else "",
                        "vkn": tb.get("vergiNo", "") if tb is not None else "",
                        "kurum_ad": kkb.get("ad", ""),
                    }
                    if b_detay["tckn"] and not b_detay["vkn"]:
                        b_detay["final_id"], b_detay["tip"] = b_detay["tckn"], "Gerçek Kişi"
                    else:
                        b_detay["final_id"], b_detay["tip"] = b_detay["vkn"], "Kurum"
                        if not b_detay["ad"]:
                            b_detay["ad"] = b_detay["kurum_ad"]
                    bu_dosyadaki_borclular.append(b_detay)

        # --- Alacak kalemleri ve tarihler (yatay) ---
        yatay_alacak_verisi = {}
        diger_alacak = dosya.find(".//digerAlacak")
        if diger_alacak is not None:
            yatay_alacak_verisi["Genel Tarih"] = diger_alacak.get("tarih", "")
            yatay_alacak_verisi["Ödeme Tarihi"] = diger_alacak.get("odemeTarihi", "")
            yatay_alacak_verisi["Abonelik Numarası"] = diger_alacak.get("alacakNo", "")
            yatay_alacak_verisi["Alacak Kalem Tutar ilamsiz"] = diger_alacak.get("tutar", "")
            # digerAlacakAciklama içinden "XXXXXXXXXX Numarali Hizmet Aboneligi" kalıbını çıkar
            import re as _re
            _aciklama_raw = diger_alacak.get("digerAlacakAciklama", "")
            _m = _re.search(r'(\d{8,})\s+Numaral[iı]\s+Hizmet\s+Abone', _aciklama_raw, _re.IGNORECASE)
            yatay_alacak_verisi["Hizmet Abone No"] = _m.group(1) if _m else ""
        else:
            yatay_alacak_verisi["Genel Tarih"] = ""
            yatay_alacak_verisi["Ödeme Tarihi"] = ""
            yatay_alacak_verisi["Hizmet Abone No"] = ""

        alacak_kalemleri = dosya.findall(".//alacakKalemi")
        for idx, ak in enumerate(alacak_kalemleri, 1):
            f_node = ak.find("faiz")
            yatay_alacak_verisi[f"Alacak_{idx}_Ad"] = ak.get("alacakKalemKodAciklama", "")
            yatay_alacak_verisi[f"Alacak_{idx}_Tutar"] = ak.get("alacakKalemTutar", "0").replace(",", ".")
            yatay_alacak_verisi[f"Alacak_{idx}_Faiz_Oran"] = f_node.get("faizOran", "0").replace(",", ".") if f_node is not None else "0"
            yatay_alacak_verisi[f"Alacak_{idx}_Faiz_Tur"] = f_node.get("faizTipKodAciklama", "") if f_node is not None else ""

        # --- Birleştir: her borçlu için bir satır ---
        for i, b in enumerate(bu_dosyadaki_borclular):
            satir = {
                "Dosya No": dosya_sayac,
                "Alacaklı": su_anki_alacakli,
                "IBAN": su_anki_iban,
                "Borçlu Ad": b["ad"],
                "Borçlu Soyad": b["soyad"],
                "Borçlu Kimlik/Vergi No": b["final_id"],
                "Kişi Tipi": b["tip"],
                "Talep Açıklaması": talep_aciklama if i == 0 else "",
            }
            # Alacak kalemleri / tarih bilgileri SADECE ilk borçlu satırında
            if i == 0:
                satir.update(yatay_alacak_verisi)
            tum_satirlar.append(satir)

    if not tum_satirlar:
        raise ValueError("XML'de işlenecek veri bulunamadı.")

    df = pd.DataFrame(tum_satirlar)
    sabit_sutunlar = ["Dosya No", "Alacaklı", "IBAN", "Borçlu Ad", "Borçlu Soyad",
                      "Borçlu Kimlik/Vergi No", "Kişi Tipi", "Talep Açıklaması",
                      "Genel Tarih", "Ödeme Tarihi"]
    # Bazı dosyalarda hiç bulunmayan sabit sütunlar olabilir; eksikleri boş ekle
    # (büyük/değişken XML'lerde KeyError ile çökmeyi önler).
    for c in sabit_sutunlar:
        if c not in df.columns:
            df[c] = ""
    diger_sutunlar = [c for c in df.columns if c not in sabit_sutunlar]
    df = df[sabit_sutunlar + diger_sutunlar]

    kayit_yolu = xml_yolu.replace(".xml", "_Yatay_Alacak_Listesi.xlsx")
    df.to_excel(kayit_yolu, index=False)
    print(f"Excel oluşturuldu: {kayit_yolu}")
    return kayit_yolu


def excel_to_takipler(excel_yolu):
    """Üretilen Excel'i okur, 'Dosya No' (A sütunu) bazında gruplayıp
    her grup için bir Takip nesnesi döndürür."""
    import pandas as pd

    df = pd.read_excel(excel_yolu, dtype=str)
    df = df.fillna("")

    takipler = []
    for dosya_no, grup in df.groupby("Dosya No", sort=False):
        ilk = grup.iloc[0]

        # --- Borçlular (her satır bir borçlu) ---
        borclular = []
        for _, satir in grup.iterrows():
            kimlik = _temiz(satir.get("Borçlu Kimlik/Vergi No", ""))
            if kimlik.endswith(".0"):
                kimlik = kimlik[:-2]
            ad = _temiz(satir.get("Borçlu Ad", ""))
            soyad = _temiz(satir.get("Borçlu Soyad", ""))
            if not (kimlik or ad):
                continue
            borclular.append(Borclu(ad=ad, soyad=soyad, kimlik=kimlik))

        # --- Alacak kalemleri (yalnızca ilk satırda dolu; Ad boşalınca dur) ---
        kalemler = []
        idx = 1
        while f"Alacak_{idx}_Ad" in df.columns:
            ad = _temiz(ilk.get(f"Alacak_{idx}_Ad", ""))
            if not ad:
                break
            kalemler.append(AlacakKalemi(
                ad=ad,
                tutar=_virgullu(ilk.get(f"Alacak_{idx}_Tutar", "")),
                faiz_oran=_virgullu(ilk.get(f"Alacak_{idx}_Faiz_Oran", "")),
                faiz_tur=_temiz(ilk.get(f"Alacak_{idx}_Faiz_Tur", "")),
            ))
            idx += 1

        takipler.append(Takip(
            dosya_no=_temiz(dosya_no),
            alacakli=_temiz(ilk.get("Alacaklı", "")),
            iban=_temiz(ilk.get("IBAN", "")),
            abone_no=_temiz(ilk.get("Abonelik Numarası", "")),
            ilamsiz_tutar=_virgullu(ilk.get("Alacak Kalem Tutar ilamsiz", "")),
            aciklama=_temiz(ilk.get("Talep Açıklaması", "")),
            fatura_tarihi=_tarih(ilk.get("Genel Tarih", "")),
            odeme_tarihi=_tarih(ilk.get("Ödeme Tarihi", "")),
            hizmet_abone_no=_temiz(ilk.get("Hizmet Abone No", "")),
            borclular=borclular,
            alacak_kalemleri=kalemler,
        ))
    return takipler
