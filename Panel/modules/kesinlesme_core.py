# -*- coding: utf-8 -*-
"""
Modül: Kesinleşme Hesabı (Örnek-7, ilamsız, genel haciz yoluyla takip)
========================================================================
Yalnız SAF tarih hesabı — DB/UYAP erişimi YOK, `models.ResmiTatil`den
okunan tatil tarihleri çağıran tarafından `tatiller` parametresiyle verilir.
Diğer takip türleri (ilamlı, kambiyo, rehin vb.) için kurallar HENÜZ
BELİRLENMEDİ (kullanıcı, 2026-08-03) — bu modül yalnız örnek-7/ilamsız/genel
haciz kuralını uygular.

Kural (kullanıcı tarifi, 2026-08-03):
    - Tebligat yapıldığı gün SAYILMAZ; ertesi günden başlayarak 7 gün
      içinde itiraz edilmezse takip kesinleşir.
    - 7. gün resmi/dini tatile ya da hafta sonuna denk gelirse, 7. gün
      tatilin bittiği günden sonraki ilk iş günü olarak kabul edilir
      (yani yalnız SON gün iş günü olacak şekilde kaydırılır — 1. ile
      6. günler arasındaki hafta sonu/tatil günleri süreyi DURDURMAZ,
      normal takvim günü olarak sayılır).
    - 7. günün bittiği günden sonraki ilk iş günü takip kesinleştirilebilir
      (yani itiraz süresi biten günün ERTESİ iş günü, fiilen kesinleşmenin
      işleme alınabileceği gündür).

Bu yorum HENÜZ KULLANICIYA TEYİT ETTİRİLMEDİ — bkz. konuşma. Bu fonksiyonlar
şimdilik yalnız kendi başına test edilmiştir; herhangi bir DB alanını
(kesinlesme_durumu vb.) YAZAN bir akışa bağlanmadan önce kullanıcı onayı
gerekir.
"""

import datetime


def is_business_day(tarih, tatiller=()):
    """`tarih` (datetime.date) hafta sonu (Cumartesi/Pazar) değilse VE
    `tatiller` kümesinde yoksa True. `tatiller`: iterable[datetime.date]
    (bkz. models.ResmiTatil.tarih)."""
    if tarih.weekday() >= 5:  # 5=Cumartesi, 6=Pazar
        return False
    return tarih not in set(tatiller)


def sonraki_is_gunu(tarih, tatiller=()):
    """`tarih` zaten iş günüyse OLDUĞU GİBİ döner; değilse ilk iş gününe
    kadar gün gün ilerletir (tarihin KENDİSİ de aday — bkz. is_business_day)."""
    gun = tarih
    while not is_business_day(gun, tatiller):
        gun += datetime.timedelta(days=1)
    return gun


def ilk_is_gunu_sonrasi(tarih, tatiller=()):
    """`tarih`den SONRAKİ (tarihin kendisi hariç) ilk iş günü."""
    return sonraki_is_gunu(tarih + datetime.timedelta(days=1), tatiller)


def orn7_ilamsiz_genel_haciz_kesinlesme_hesapla(teblig_tarihi, tatiller=()):
    """Örnek-7 (ilamsız, genel haciz yoluyla takip) ödeme emri tebliğ
    tarihinden itiraz süresi + kesinleşme tarihlerini hesaplar.

    `teblig_tarihi`: datetime.date — ödeme emrinin GERÇEKTEN tebliğ edildiği
    (teslim/okundu-sayıldığı) gün.
    `tatiller`: iterable[datetime.date] — bkz. models.ResmiTatil.

    Döner: {
        "itiraz_son_gunu": datetime.date,       # 7. gün (iş günü olacak şekilde kaydırılmış)
        "kesinlesebilir_tarihi": datetime.date,  # itiraz_son_gunu'ndan sonraki ilk iş günü
    }
    """
    gun7_ham = teblig_tarihi + datetime.timedelta(days=7)
    itiraz_son_gunu = sonraki_is_gunu(gun7_ham, tatiller)
    kesinlesebilir_tarihi = ilk_is_gunu_sonrasi(itiraz_son_gunu, tatiller)
    return {
        "itiraz_son_gunu": itiraz_son_gunu,
        "kesinlesebilir_tarihi": kesinlesebilir_tarihi,
    }
