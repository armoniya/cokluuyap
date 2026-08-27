# -*- coding: utf-8 -*-
"""
Ortak "gerçek esas no" sorgusu
===============================
xml_takip/ipotek/mts akışlarının HER BİRİ "tevzi" adımından sonra yalnızca
UYAP'ın oturumluk/opak dosyaId'sini bilir — kullanıcının UYAP ekranında (ör.
"Tamamlanmayan Dosyalar" veya harç ödemesi sonrası) gördüğü KALICI esas no
("2026/894734" gibi) ayrı bir adımda doğrulanır.

Bu modül mts.takip.odenmis_dosyalari_bul ile AYNI, CANLI DOĞRULANMIŞ eşleştirme
desenini (tamamlanmayanDosyalar_brd.ajx + taraf adlarıyla eşleştirme) genel bir
fonksiyona çıkarır — kullanıcı bulgusu (2026-08-14): xml_takip/ipotek akışları bu
numarayı HİÇ yakalamıyordu, yalnızca dosyaId (opak token) logluyordu.

mts.takip kendi (canlı doğrulanmış) sürümünü DEĞİŞTİRİLMEDEN korur — buradaki
fonksiyon yalnızca job_handlers.py'nin xml_takip/ipotek akışları için EK bir
doğrulama adımı olarak kullanılır.
"""
import re as _re
import json as _json

_ESAS_NO_DESENI = _re.compile(r"^\d{4}/\d+$")


async def _api_json(ctx, path, payload=None):
    resp = await ctx.uyap("POST", path, json=(payload if payload is not None else {}))
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü.")
    return _json.loads(resp.text)


async def bul(ctx, hedef_adlar, dosya_tur_kod=35):
    """hedef_adlar: eşleştirilecek taraf ad(soyad)/unvan metinleri (ör.
    ["AYŞE YUNUSOĞLU"] ya da ["ABC TİCARET LTD ŞTİ"]) — 'Tamamlanmayan
    Dosyalar' kaydındaki 'taraflar' serbest metninde HEPSİ geçen dosya aranır
    (büyük/küçük harf ve TR karakter farkına duyarsız alt-dize eşleşmesi).

    Eşleşen kayıtta gerçek esas no varsa ("2026/894734" gibi; "Ödeme
    Yapılmadı!" gibi yer tutucu DEĞİL) o no'yu, yoksa None döner. Birden fazla
    dosya eşleşirse (ör. aynı borçluya karşı başka açık dosyalar) İLK gerçek
    esas no'lu olanı döner — çağıran taraf (job_handlers.py) belirsizlik
    durumunda bunu loglar."""
    from .ipotek.takip import _guvenli_liste, _temiz_buyuk

    hedef_adlar = [_temiz_buyuk(a) for a in (hedef_adlar or []) if a]
    hedef_adlar = [a for a in hedef_adlar if a]
    if not hedef_adlar:
        return None

    liste = _guvenli_liste(await _api_json(ctx, "tamamlanmayanDosyalar_brd.ajx",
                                           {"dosyaTurKod": dosya_tur_kod}))
    adaylar = [k for k in liste
              if all(ad in _temiz_buyuk(k.get("taraflar") or "") for ad in hedef_adlar)]
    for k in adaylar:
        dn = (k.get("dosyaNo") or "").strip()
        if _ESAS_NO_DESENI.match(dn):
            return dn
    return None
