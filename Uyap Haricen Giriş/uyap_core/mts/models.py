"""
uyap_core.mts.models — MTS veri modelleri + metin/sayı yardımcıları
===================================================================
Kararlı/mts_takip_acan.py'deki modeller ve tarayıcısız yardımcılar buraya taşındı.
Playwright/pandas/win32 BAĞIMLILIĞI YOKTUR (pandas yalnızca parse.py'de, lazy).
"""

import re as _re
import time as _time
from dataclasses import dataclass, field, asdict


# ── Veri modelleri ──────────────────────────────────────────────────────────
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
    aciklama: str = ""        # Talep Açıklaması
    fatura_tarihi: str = ""   # Genel Tarih
    odeme_tarihi: str = ""    # Ödeme Tarihi
    hizmet_abone_no: str = ""  # digerAlacakAciklama'dan çıkarılan abone no
    borclular: list = field(default_factory=list)
    alacak_kalemleri: list = field(default_factory=list)


# ── JSON (iş parametresi) dönüşümleri ─────────────────────────────────────────
# İstemci Excel'i ayrıştırıp Takip listesini dict olarak işe gönderir; ofis tarafı
# işleyici bunları tekrar Takip nesnesine çevirir. dataclasses.asdict iç içe
# dataclass'ları da dict'e çevirir; tersi için aşağıdaki yardımcılar kullanılır.
def takip_from_dict(d):
    """Bir dict'ten Takip nesnesi kurar (borclular/alacak_kalemleri iç içe dict olabilir)."""
    d = dict(d or {})
    borclular = [b if isinstance(b, Borclu) else Borclu(**b) for b in (d.pop("borclular", None) or [])]
    kalemler = [k if isinstance(k, AlacakKalemi) else AlacakKalemi(**k)
                for k in (d.pop("alacak_kalemleri", None) or [])]
    # Bilinmeyen alanları sessizce ele (ileri uyumluluk).
    bilinen = {f for f in Takip.__dataclass_fields__ if f not in ("borclular", "alacak_kalemleri")}
    temiz = {k: v for k, v in d.items() if k in bilinen}
    return Takip(borclular=borclular, alacak_kalemleri=kalemler, **temiz)


def takipler_from_params(takipler):
    """params['takipler'] (dict listesi) → [Takip, ...]."""
    return [t if isinstance(t, Takip) else takip_from_dict(t) for t in (takipler or [])]


def takipler_to_params(takipler):
    """[Takip, ...] → JSON'a uygun dict listesi (istemci tarafı, işe gönderim için)."""
    return [asdict(t) if isinstance(t, Takip) else dict(t) for t in (takipler or [])]


# ── Metin / sayı yardımcıları ─────────────────────────────────────────────────
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


def _tarih(deger):
    """Tarihi ggaayyyy (8 haneli, ayraçsız) formatına çevirir.
    '2026-06-22' -> '22062026', '22.06.2026' -> '22062026', '22062026' -> '22062026'."""
    s = _temiz(deger)
    if not s:
        return s
    s = s.split(" ")[0].split("T")[0]          # olası saat kısmını at
    parcalar = [p for p in _re.split(r"[^0-9]", s) if p]
    if len(parcalar) == 3:
        if len(parcalar[0]) == 4:               # yyyy-mm-dd
            yil, ay, gun = parcalar
        else:                                   # dd-mm-yyyy
            gun, ay, yil = parcalar
        return f"{gun.zfill(2)}{ay.zfill(2)}{yil.zfill(4)}"
    return _re.sub(r"[^0-9]", "", s)             # ayraç yoksa rakamları döndür


def kalemleri_birlestir(kalemler):
    """Alacak adı ve faiz oranı aynı olan kalemleri tek kaleme toplar.

    Aynı (ad, faiz_oran) ikilisine sahip kalemlerin tutarları toplanır; böylece
    örn. tüm 'Asıl Alacak'lar tek bir asıl alacak, tüm aynı oranlı faizler tek
    kalem olur. 'Diğer Asıl Alacak' gibi varyantlar 'Asıl Alacak' sayılır.
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


# ── takip_ac içinde kullanılan yardımcılar (mts_takip_acan_api.py'den) ─────────
def tr_to_ascii(text):
    """Türkçe karakterleri ASCII'ye indirger, dosya adı için güvenli hale getirir."""
    if not text:
        return ""
    tr_map = {
        'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'I': 'I', 'İ': 'I', 'ö': 'o', 'Ö': 'O',
        'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U', ' ': '_'
    }
    for tr_char, eng_char in tr_map.items():
        text = text.replace(tr_char, eng_char)
    return _re.sub(r'[^a-zA-Z0-9_]', '', text)


def format_date_with_slashes(date_str):
    """Tarihi gg/aa/yyyy biçimine çevirir; boşsa bugünü döndürür."""
    if not date_str:
        return _time.strftime("%d/%m/%Y")
    clean_date = _re.sub(r'[^0-9]', '', str(date_str)).strip()
    if len(clean_date) == 8:
        # Yıl ile başlıyorsa (ör. 20260609)
        if clean_date.startswith(("19", "20")) and int(clean_date[4:6]) <= 12 and int(clean_date[6:]) <= 31:
            return f"{clean_date[6:]}/{clean_date[4:6]}/{clean_date[:4]}"
        else:
            return f"{clean_date[:2]}/{clean_date[2:4]}/{clean_date[4:]}"
    elif len(clean_date) == 6:
        return f"{clean_date[:2]}/{clean_date[2:4]}/20{clean_date[4:]}"
    try:
        from datetime import datetime
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(str(date_str).strip(), fmt)
                return dt.strftime("%d/%m/%Y")
            except ValueError:
                pass
    except Exception:
        pass
    return _time.strftime("%d/%m/%Y")


def parse_amount(val):
    """Türk formatlı tutar metnini float'a çevirir ('1.280,78 TL' -> 1280.78)."""
    if not val:
        return 0.0
    val_str = str(val).replace(".", "").replace(",", ".").replace(" TL", "").strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0
