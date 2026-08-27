"""
uyap_core.ipotek — İpoteğin Paraya Çevrilmesi Yolu İle İcra Takibi açma için
TARAYICISIZ çekirdek
=============================================================================
uyap_core.mts paketiyle aynı mimari (parse İSTEMCİDE, prepare/finalize canlı
oturumla OFİSTE, is_kuyrugu üzerinden onay akışıyla). Tek fark: burası MTS
(Merkezi Takip Sistemi) değil, standart İcra Takip Açılışı sihirbazının
"İlamlı Takip → İlamların İcrası... → ÖRNEK: 6 İpoteğin Paraya Çevrilmesi"
zincirini kullanır (tevzi numarası alma adımı dahil).

Modüller:
  • parse — Excel (C/D/E/G sütunları) → alacak kalemi listesi (pandas gerekmez,
            openpyxl yeterli; istemcide çalışır).
  • xml_parse — UYAP "exchangeData" XML'i (İLAMLI/İpotek biçimi, <ilam> düğümlü)
            → aynı satır şekli (+ diger_faiz_alacagi/masraf) + dosya/taraf/ilam
            alanları; istemcide çalışır, UYAP'a dokunmaz.
  • takip — prepare (sorgular + harç hesabı, tevzi ALMAZ) / finalize (tevzi +
            UDF indir + e-imza + evrak gönder).
"""

from .parse import excel_kalemlerini_oku, excel_tutar_oku
from .takip import taslak_aciklama_48_4
from .xml_parse import xml_dosyasindan_oku, xml_metninden_oku

__all__ = ["excel_kalemlerini_oku", "excel_tutar_oku", "taslak_aciklama_48_4",
           "xml_dosyasindan_oku", "xml_metninden_oku"]
