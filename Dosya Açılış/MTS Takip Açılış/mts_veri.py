# -*- coding: utf-8 -*-
"""
MTS Veri Modelleri ve Yardımcılar
=================================
mts_takip_acan.py'den AYRILDI (2026-06-26 modülerleştirme).

Burada SADECE saf veri tutar ve metin/sayı yardımcıları bulunur:
playwright, win32 veya UYAP'a HİÇBİR bağımlılığı yoktur. Bu yüzden
güvenle başka yerlerden (mts_takip_acan, mts_takip_acan_api, gui) import
edilebilir.

İçerik:
  - Veri modelleri: Borclu, AlacakKalemi, Takip
  - Metin/sayı yardımcıları: _temiz, _virgullu, _tutar_to_float,
    _float_to_tutar, _ad_kanonik, _tarih
  - İş mantığı yardımcısı: kalemleri_birlestir
"""

from dataclasses import dataclass, field
import re


@dataclass
class Borclu:
    ad: str = ""
    soyad: str = ""
    kimlik: str = ""


@dataclass
class AlacakKalemi:
    ad: str = ""
    tutar: str = ""
    faiz_oran: str = ""
    faiz_tur: str = ""   # XML faizTipKodAciklama (örn 'Diğer', 'Reeskont Avans')


@dataclass
class Takip:
    dosya_no: str = ""
    alacakli: str = ""
    iban: str = ""
    abone_no: str = ""
    ilamsiz_tutar: str = ""
    aciklama: str = ""        # H — Talep Açıklaması
    fatura_tarihi: str = ""   # I — Genel Tarih
    odeme_tarihi: str = ""    # J — Ödeme Tarihi
    hizmet_abone_no: str = ""  # digerAlacakAciklama'dan çıkarılan abone no (PDF eşleştirme)
    borclular: list = field(default_factory=list)
    alacak_kalemleri: list = field(default_factory=list)


def _temiz(deger):
    """Hücre değerini güvenli stringe çevirir, NaN/None -> ''."""
    if deger is None:
        return ""
    s = str(deger).strip()
    if s.lower() in ("nan", "none"):
        return ""
    return s


def _virgullu(deger):
    """Tutar/faiz oranını Türk formatına çevirir (1280.78 -> 1280,78)."""
    s = _temiz(deger)
    if not s:
        return s
    if s.endswith(".0"):          # Excel float kuyruğu '.0' at
        s = s[:-2]
    if "," in s:                  # zaten virgüllü ise dokunma (örn '10163,64')
        return s
    return s.replace(".", ",")


def _tutar_to_float(deger):
    """Türk formatlı tutarı float'a çevirir. '1.280,78' / '1280,78' / '1280.78' -> 1280.78"""
    s = _temiz(deger).replace(" ", "")
    if not s:
        return 0.0
    if "," in s:                      # Türk formatı: '.' binlik, ',' ondalık
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _float_to_tutar(deger):
    """float'ı Türk formatlı kuruşlu metne çevirir. 1280.78 -> '1280,78'"""
    return f"{deger:.2f}".replace(".", ",")


def _ad_kanonik(ad):
    """Kalem adını gruplama için normalize eder.

    'Diğer Asıl Alacak' vb. varyantlar 'Asıl Alacak' ile aynı kabul edilir;
    böylece aynı faiz oranına sahip tüm asıl alacaklar tek kaleme toplanır."""
    s = (ad or "").strip()
    dusuk = s.lower()
    if "asıl alacak" in dusuk or "asil alacak" in dusuk:
        return "Asıl Alacak"
    return s


def kalemleri_birlestir(kalemler):
    """Alacak adı ve faiz oranı aynı olan kalemleri tek kaleme toplar.

    Aynı (ad, faiz_oran) ikilisine sahip kalemlerin tutarları toplanır; böylece
    örn. tüm 'Asıl Alacak'lar tek bir asıl alacak, tüm aynı oranlı
    'Geçmiş gün faizi'leri tek bir faiz kalemi olarak girilir. 'Diğer Asıl Alacak'
    gibi varyantlar da 'Asıl Alacak' sayılarak aynı orana göre birleştirilir.
    Giriş sırası korunur."""
    gruplar = {}
    sira = []
    for k in kalemler:
        ad_kanonik = _ad_kanonik(k.ad)
        anahtar = (ad_kanonik.lower(),
                   (k.faiz_oran or "").strip().replace(".", ","))
        if anahtar not in gruplar:
            gruplar[anahtar] = AlacakKalemi(
                ad=ad_kanonik, tutar="0", faiz_oran=k.faiz_oran, faiz_tur=k.faiz_tur)
            sira.append(anahtar)
        mevcut = gruplar[anahtar]
        mevcut.tutar = _float_to_tutar(
            _tutar_to_float(mevcut.tutar) + _tutar_to_float(k.tutar))
    return [gruplar[a] for a in sira]


def _tarih(deger):
    """Tarihi ggaayyyy (8 haneli, ayraçsız) formatına çevirir.
    '2026-06-22' -> '22062026', '22.06.2026' -> '22062026', '22062026' -> '22062026'."""
    s = _temiz(deger)
    if not s:
        return s
    s = s.split(" ")[0].split("T")[0]          # olası saat kısmını at
    parcalar = [p for p in re.split(r"[^0-9]", s) if p]
    if len(parcalar) == 3:
        if len(parcalar[0]) == 4:               # yyyy-mm-dd
            yil, ay, gun = parcalar
        else:                                   # dd-mm-yyyy
            gun, ay, yil = parcalar
        return f"{gun.zfill(2)}{ay.zfill(2)}{yil.zfill(4)}"
    return re.sub(r"[^0-9]", "", s)             # ayraç yoksa rakamları döndür
