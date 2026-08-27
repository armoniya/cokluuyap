"""
uyap_core.ipotek.parse — Excel'den alacak kalemi listesi çıkarır (tarayıcısız)
================================================================================
Kolon eşlemesi (kullanıcıyla netleştirildi, 1. satır başlık — veri 2. satırdan
başlar):
    C = faiz oranı (%)         → yalnızca D'ye uygulanır
    D = asıl alacak tutarı     → faiz türü "Diğer", süre tipi "Yıllık", oran=C
    E = geçmiş gün faizi tutarı → faiz UYGULANMAZ (zaten hesaplı tutar)
    G = BSMV tutarı            → faiz UYGULANMAZ
    H = masraf                 → KULLANILMIYOR (kullanıcı kararıyla atlandı)

Bir satırda D/E/G'den biri boş veya <=0 ise o kalem ATLANIR (sıfır tutarlı
alacak kalemi UYAP'a gönderilmez); satırın tamamı boşsa (C/D/E/G hepsi boş)
satır tablo sonu sayılır.
"""

import openpyxl


def excel_tutar_oku(deger):
    """Bir hücreden tutar/oran okur.

    Hücre zaten sayısal ise (openpyxl'in normal davranışı, hücre "Sayı" biçimli
    ise) DOĞRUDAN kullanılır — hiçbir string ayrıştırma yapılmaz, bu yüzden
    nokta/virgül karışıklığı riski YOKTUR. Hücre metin ise TR ("123.050,77")
    veya EN ("123,050.77") biçimini son ayraca (virgül mü nokta mı ondalık)
    bakarak sezer."""
    if deger is None:
        return 0.0
    if isinstance(deger, (int, float)):
        return float(deger)
    s = str(deger).strip().replace("TL", "").replace("%", "").strip()
    if not s:
        return 0.0
    son_nokta = s.rfind(".")
    son_virgul = s.rfind(",")
    if son_nokta == -1 and son_virgul == -1:
        return float(s)
    if son_virgul > son_nokta:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def excel_kalemlerini_oku(yol):
    """Excel dosyasını okur, satır satır alacak kalemi grupları döndürür.

    Dönüş: [{"faiz_orani": float, "asil_alacak": float,
             "gecmis_gun_faizi": float, "bsmv": float, "satir": int}, ...]
    (yalnızca en az bir tutarı >0 olan satırlar; hepsi boşsa tablo bitmiş sayılır.)
    """
    wb = openpyxl.load_workbook(yol, data_only=True)
    ws = wb.worksheets[0]

    satirlar = []
    for row in ws.iter_rows(min_row=2):
        # C=3, D=4, E=5, G=7 (1-indeksli sütun)
        c_deger = row[2].value if len(row) > 2 else None
        d_deger = row[3].value if len(row) > 3 else None
        e_deger = row[4].value if len(row) > 4 else None
        g_deger = row[6].value if len(row) > 6 else None

        if c_deger is None and d_deger is None and e_deger is None and g_deger is None:
            break  # tablo sonu

        faiz_orani = excel_tutar_oku(c_deger)
        asil_alacak = excel_tutar_oku(d_deger)
        gecmis_gun_faizi = excel_tutar_oku(e_deger)
        bsmv = excel_tutar_oku(g_deger)

        if asil_alacak <= 0 and gecmis_gun_faizi <= 0 and bsmv <= 0:
            continue  # bu satırda gönderilecek hiçbir kalem yok

        satirlar.append({
            "satir": row[0].row,
            "faiz_orani": faiz_orani,
            "asil_alacak": asil_alacak,
            "gecmis_gun_faizi": gecmis_gun_faizi,
            "bsmv": bsmv,
        })

    return satirlar
