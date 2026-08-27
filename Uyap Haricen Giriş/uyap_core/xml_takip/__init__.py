# -*- coding: utf-8 -*-
"""
uyap_core.xml_takip — "İcra Takip Açılış - XML" (Banka Dosyası) için TARAYICISIZ çekirdek
============================================================================================
uyap_core.ipotek/mts paketleriyle aynı mimari (parse İSTEMCİDE, prepare/finalize canlı
oturumla OFİSTE, is_kuyrugu üzerinden onay akışıyla). Fark: taraf/alacak verisi Excel'den
DEĞİL, UYAP'ın kendi "exchangeData" değişim formatındaki bir XML dosyasından gelir — bir
XML BİRDEN FAZLA <dosya> (takip) içerebilir (bkz. parse.py).

Modüller:
  • parse — exchangeData XML → Dosya/Taraf/AlacakKalemi dataclass listesi (stdlib
            xml.etree, bağımlılık yok; istemcide çalışır).
  • takip — prepare (referans doğrulama + canlı taraf sorgusu + harç hesabı, tevzi
            ALMAZ) / finalize (tevzi + UDF indir + e-imza + evrak gönder + harç ödeme).
"""

from .parse import xml_metninden_oku, xml_dosyasindan_oku, dosya_ozet

__all__ = ["xml_metninden_oku", "xml_dosyasindan_oku", "dosya_ozet"]
