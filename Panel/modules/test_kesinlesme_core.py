# -*- coding: utf-8 -*-
"""kesinlesme_core.py'nin saf tarih hesabı için birim testleri (DB/UYAP
erişimi yok). Çalıştırma: `python -m unittest Panel.modules.test_kesinlesme_core`
ya da bu dosyanın bulunduğu dizinden `python test_kesinlesme_core.py`."""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kesinlesme_core as kc


class OrnekYediKesinlesmeTestleri(unittest.TestCase):
    def test_7_gun_hafta_ici_ise_kaydirilmaz(self):
        # Perşembe tebliğ -> ham 7. gün de Perşembe (iş günü), kaydırma yok.
        teblig = dt.date(2026, 8, 6)
        r = kc.orn7_ilamsiz_genel_haciz_kesinlesme_hesapla(teblig)
        self.assertEqual(r["itiraz_son_gunu"], dt.date(2026, 8, 13))
        self.assertEqual(r["kesinlesebilir_tarihi"], dt.date(2026, 8, 14))

    def test_7_gun_cumartesiye_denk_gelirse_pazartesiye_kayar(self):
        # Cumartesi tebliğ -> ham 7. gün de Cumartesi -> Pazartesi'ye kayar.
        teblig = dt.date(2026, 8, 8)
        self.assertEqual(teblig.strftime("%A"), "Saturday")
        r = kc.orn7_ilamsiz_genel_haciz_kesinlesme_hesapla(teblig)
        self.assertEqual(r["itiraz_son_gunu"], dt.date(2026, 8, 17))   # Pazartesi
        self.assertEqual(r["kesinlesebilir_tarihi"], dt.date(2026, 8, 18))  # Salı

    def test_resmi_tatil_hafta_sonuyla_art_arda_gelirse_ikisini_de_atlar(self):
        # Ham 7. gün Cuma bir resmi tatile denk gelir, hemen ardından hafta
        # sonu (Cts/Paz) gelir -> ilk iş günü Pazartesi'dir.
        teblig = dt.date(2026, 8, 7)  # Cuma
        gun7_ham = teblig + dt.timedelta(days=7)
        self.assertEqual(gun7_ham, dt.date(2026, 8, 14))  # Cuma
        tatiller = {dt.date(2026, 8, 14)}
        r = kc.orn7_ilamsiz_genel_haciz_kesinlesme_hesapla(teblig, tatiller)
        self.assertEqual(r["itiraz_son_gunu"], dt.date(2026, 8, 17))       # Pazartesi
        self.assertEqual(r["kesinlesebilir_tarihi"], dt.date(2026, 8, 18))  # Salı

    def test_is_business_day(self):
        self.assertTrue(kc.is_business_day(dt.date(2026, 8, 6)))   # Perşembe
        self.assertFalse(kc.is_business_day(dt.date(2026, 8, 8)))  # Cumartesi
        self.assertFalse(kc.is_business_day(dt.date(2026, 8, 6), {dt.date(2026, 8, 6)}))


if __name__ == "__main__":
    unittest.main()
