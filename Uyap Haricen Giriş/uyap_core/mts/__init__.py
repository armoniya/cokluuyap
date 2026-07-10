"""
uyap_core.mts — MTS takip açma için TARAYICISIZ çekirdek
========================================================
Kararlı/'MTS Takip Açılış' programının (Playwright + e-imza editörü ile çalışan)
mantığının, "güçlü bağlantı" (uyap_proxy.gw canlı oturumu + iş kuyruğu) üzerinden
çalışacak tarayıcısız parçaları.

Bu paketteki her şey GUI/Playwright/win32'den BAĞIMSIZDIR; hem ofiste (iş kuyruğu
işleyicisi, job_handlers.coklu_takip_ac) hem de istemcide (Excel/XML ayrıştırma)
import edilebilir.

Modüller:
  • models — Takip/Borclu/AlacakKalemi veri modelleri + sayısal/metin yardımcıları
             + kalemleri_birlestir + JSON (params) dönüşümleri.
  • parse  — Excel/XML → Takip listesi (pandas gerektirir; istemcide çalışır).
  • evrak  — UYAP evrak gönderme (davaAcilisEvrakGonderme_brd) şablonları + items_kur.
"""

from .models import (
    Borclu, AlacakKalemi, Takip,
    kalemleri_birlestir,
    takip_from_dict, takipler_from_params, takipler_to_params,
    tr_to_ascii, format_date_with_slashes, parse_amount,
)
from .evrak import EVRAK_TURU_SABLON, VARSAYILAN_EVRAK_SIRASI, items_kur, mime_belirle

__all__ = [
    "Borclu", "AlacakKalemi", "Takip",
    "kalemleri_birlestir",
    "takip_from_dict", "takipler_from_params", "takipler_to_params",
    "tr_to_ascii", "format_date_with_slashes", "parse_amount",
    "EVRAK_TURU_SABLON", "VARSAYILAN_EVRAK_SIRASI", "items_kur", "mime_belirle",
]
