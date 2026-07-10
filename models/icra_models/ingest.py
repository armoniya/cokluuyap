# -*- coding: utf-8 -*-
"""
UYAP yanıtı → PostgreSQL (kapak künyesi)
========================================
'search_phrase_detayli' yanıtındaki her kaydı (Dosya kapak künyesi) Birim + Dosya
olarak upsert eder. Tekil anahtar UYAP'ın 'dosyaId' değeridir; tekrar çalıştırmak
güvenlidir (var olanı günceller, yoksa ekler).

Örnek kayıt (kullanıcının verdiği yanıttan):
  {"dosyaId":"...","dosyaNo":"2025/237","dosyaDurumKod":0,"dosyaDurum":"Açık",
   "dosyaTurKod":1,"dosyaTur":"Talimat Dosyası",
   "dosyaAcilisTarihi":{"date":{"year":2025,"month":9,"day":11},
                        "time":{"hour":17,"minute":3,"second":9,"nano":0}},
   "birimAdi":"Sinanpaşa İcra Dairesi","birimId":"1000428",
   "birimTuru1":"11","birimTuru2":"1101","birimTuru3":"1199", ...}
"""
from datetime import datetime

from django.db import transaction

from .models import Birim, Dosya


def _tarih(d):
    """{"date":{...},"time":{...}} → naive datetime (yerel). Olmazsa None."""
    if not isinstance(d, dict):
        return None
    tarih = d.get("date") or {}
    saat = d.get("time") or {}
    try:
        return datetime(
            int(tarih["year"]), int(tarih["month"]), int(tarih["day"]),
            int(saat.get("hour", 0) or 0), int(saat.get("minute", 0) or 0),
            int(saat.get("second", 0) or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _yil_sira(dosya_no):
    """'2025/237' → (2025, 237). Ayrıştırılamazsa (0, 0)."""
    try:
        yil, sira = str(dosya_no).split("/", 1)
        return int(yil), int(sira)
    except (ValueError, AttributeError):
        return 0, 0


@transaction.atomic
def dosya_kunyesi_kaydet(rec):
    """Tek bir UYAP kaydını (kapak künyesi) upsert eder. (Dosya, created) döner."""
    birim, _ = Birim.objects.update_or_create(
        birim_id=str(rec.get("birimId", "")),
        defaults={
            "ad": rec.get("birimAdi", "") or "",
            "turu1": str(rec.get("birimTuru1", "") or ""),
            "turu2": str(rec.get("birimTuru2", "") or ""),
            "turu3": str(rec.get("birimTuru3", "") or ""),
        },
    )
    yil, sira = _yil_sira(rec.get("dosyaNo"))
    tur_kod = int(rec.get("dosyaTurKod", 0) or 0)
    # KALICI tekil anahtar = (birim, yıl, sıra, tür_kod) [uq_dosya_kunye]. dosyaId
    # OTURUMA GÖRE DEĞİŞTİĞİ için onunla upsert edilirse aynı dosya yeni id ile
    # gelince "duplicate key ... uq_dosya_kunye" hatası verir. Bu yüzden DOĞAL
    # ANAHTARLA upsert edip dosya_id'yi (ve diğer alanları) güncelliyoruz.
    dosya, created = Dosya.objects.update_or_create(
        birim=birim,
        yil=yil,
        sira_no=sira,
        tur_kod=tur_kod,
        defaults={
            "dosya_id": str(rec.get("dosyaId", "") or ""),
            "dosya_no": rec.get("dosyaNo", "") or "",
            "durum_kod": int(rec.get("dosyaDurumKod", 0) or 0),
            "durum": rec.get("dosyaDurum", "") or "",
            "tur": rec.get("dosyaTur", "") or "",
            "acilis_tarihi": _tarih(rec.get("dosyaAcilisTarihi")),
            "is_dava_dosyasi_acilmis": bool(rec.get("isDavaDosyasiAcilmisMi", False)),
        },
    )
    return dosya, created


def kapak_kunyelerini_kaydet(records):
    """Kayıt listesini upsert eder. (yeni_sayisi, guncellenen_sayisi) döner."""
    yeni = guncel = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        _, created = dosya_kunyesi_kaydet(rec)
        if created:
            yeni += 1
        else:
            guncel += 1
    return yeni, guncel
