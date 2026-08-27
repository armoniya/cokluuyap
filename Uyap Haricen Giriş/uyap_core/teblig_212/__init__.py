"""
uyap_core.teblig_212 — T.K.21/2 şerhli yeniden tebliğ talebi GÖNDERİMİ (ücretli,
GERÇEK PARA HARCAR) — TARAYICISIZ çekirdek
=============================================================================
uyap_core.ipotek / uyap_core.mts ile AYNI mimari (prepare canlı oturumla
salt-okunur sorgu yapar; finalize — yalnız kullanıcı onayından SONRA —
UDF indir + e-imza + evrak gönder adımıyla parayı harcar).

Panel/modules/teblig_21_2_core.py (tarama-only, kasıtlı olarak bu pakete HİÇ
dokunmaz) burada YOK — o modül ayrı kalmaya devam eder. Gönderim SADECE
job_handlers.teblig_212_gonder (job_type) üzerinden, her dosya için ayrı
ctx.request_approval onayı alınarak tetiklenir.

Modül:
  • gonder — prepare (dosyaId/kisiKurumId çöz + "zaten gönderilmiş mi" taze
             kontrolü, salt-okunur) / finalize (talep evrakı al + e-imzala +
             avukatIcraTalepEvrakiGonder.ajx'e gönder — ücret burada düşer).
"""

from .gonder import prepare, finalize

__all__ = ["prepare", "finalize"]
