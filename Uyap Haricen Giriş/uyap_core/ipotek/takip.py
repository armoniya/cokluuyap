"""
uyap_core.ipotek.takip — İpotek (İlamlı, İpoteğin Paraya Çevrilmesi) icra takibi
açma akışı — canlı oturum üzerinden, TARAYICISIZ.
================================================================================
2026-07-28 tarihli gerçek (canlı, e-imzalı) bir dosya açılışının kaydından
(Panel/modules/logger_data/kayitlar.jsonl) çıkarılan istek/yanıt çiftlerine göre
yazıldı — bkz. uyap_core.mts.takip (aynı mimari: prepare = sorgular + harç
hesabı; finalize = tevzi + UDF indir + e-imza + evrak gönder).

GÜVENİLİRLİK NOTU: prepare() içindeki TÜM adımlar (illerIlcelerGetir ...
icra_takip_tahsilat_nedenleri) gerçek yakalanan istek/yanıt çiftleriyle
doğrulanmıştır. finalize() içindeki tevzi adımı da doğrulanmıştır (aynı
kayıttan). Evrak gönderme adımının TAM "items" yapısı — 2026-07-28 ag_kaydi.log
ham ağ kaydında (JS FormData hook) bulundu ve BİREBİR uygulandı: UDF
(ICR_TAKIP_TLP) + Vekaletname (CZM_VEKALETNAME, ZORUNLU) + dayanak belge(ler)i
(MTS_TAKIBIN_DAYANAGI — bu kod "MTS" adını taşısa da gerçek kayıtta normal
(MTS-olmayan) İcra Takip Açılışında da aynen kullanılmış). Birden fazla dayanak
belgesinde gerçek kayıtta ikinci evrakın "label"ı "Takibin Dayanağı - Ek Evrak"
idi; burada tüm dayanak evrakları aynı paylaşılan şablonla ("Takibin Dayanağı")
gönderiliyor — bu yalnızca görüntü metni farkı, "tur" alanı (asıl doğrulanan
şey) aynı olduğundan düşük riskli bir sadeleştirme kabul edildi.

"dosyaAciklama_48_4" alanı UYAP arayüzünde AYRI bir giriş kutusu değildir
(kullanıcı geri bildirdi) — bu yüzden burada kullanıcıdan ALINMAZ, alacak
kalemlerinden (asıl alacak tutarları faiz oranına göre gruplanıp) otomatik
üretilir; bkz. _dosya_aciklama_uret().
"""

import json
import re as _re
import asyncio
import base64

from ..mts.models import format_date_with_slashes


def _belge_coz(d):
    """params üzerinden gelen {"filename":..., "b64":...} sözlüğünü
    {"filename":..., "bytes":...} haline çevirir (HTTP/JSON üzerinden bayt
    gönderilemediği için GUI base64 kodlar). Geçersizse None döner."""
    if not isinstance(d, dict) or not d.get("b64"):
        return None
    try:
        return {"filename": d.get("filename"), "bytes": base64.b64decode(d["b64"])}
    except Exception:
        return None


async def _api_text(ctx, path, payload=None, multipart=False):
    if multipart:
        files = {
            k: (None, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
            for k, v in (payload or {}).items()
        }
        resp = await ctx.uyap("POST", path, files=files)
    else:
        resp = await ctx.uyap("POST", path, json=(payload if payload is not None else {}))
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü.")
    return resp.text


async def _api_json(ctx, path, payload=None, multipart=False):
    return json.loads(await _api_text(ctx, path, payload, multipart))


IL_VARSAYILAN = "İzmir"
ADLIYE_VARSAYILAN = "İzmir"

# icraTakipAlacakGirisBilgileri.ajx referans listesinden canlı doğrulandı (2026-07-28).
# Masraf (Excel'in H sütunu) Excel akışında kullanıcı kararıyla dışarıda bırakıldı —
# bu karar yalnızca Excel akışını bağlar (bkz. Panel/modules/potek_takip.py docstring).
ALACAK_KALEM_BILGI = {
    "asil_alacak": {"kod": 7172, "kod_aciklama": "Asıl Alacak", "aciklama": "Asıl Alacak"},
    "gecmis_gun_faizi": {"kod": 7182, "kod_aciklama": "Geçmiş Gün Faizi", "aciklama": "Faiz"},
    "bsmv": {"kod": 7178, "kod_aciklama": "BSMV", "aciklama": "BSMV"},
}

# "diger_faiz_alacagi" / "masraf": XML akışında (uyap_core.ipotek.xml_parse) ortaya
# çıkabilen, ama YUKARIDAKİ üç türün aksine CANLI DOĞRULANMAMIŞ kalem türleri — kod
# numaraları burada HARDCODE EDİLMEZ (yanlış kod göndermemek için), her prepare()
# çağrısında icraTakipAlacakGirisBilgileri.ajx referans listesinden İSİM eşleşmesiyle
# BULUNUR (bkz. _referans_kalem_bul). Bulunamazsa ValueError fırlatılır — tutar
# sessizce atlanmaz/göz ardı edilmez (2026-08-12, kullanıcı kararı: "her asıl alacağı,
# faizi, bsmv'yi ve masrafı ayrı ayrı al").
_REFERANS_ARANACAK_ADLAR = {
    "diger_faiz_alacagi": ["Faiz Alacağı", "Diğer Faiz"],
    "masraf": ["Masraf"],
}


def _temiz_buyuk(s):
    return (s or "").upper().replace("İ", "I").replace("ı", "I").strip()


def _guvenli_liste(v):
    """UYAP bazen liste beklenen bir uç noktada hata nesnesi ({"type":"error",...})
    döndürüyor (bkz. getAdresListesi_brd.ajx canlı testinde görülen KeyError).
    Liste değilse boş liste döndürür — çağıran taraf "bulunamadı" olarak ele alır."""
    return v if isinstance(v, list) else []


def _il_bul(iller, il_adi):
    hedef = _temiz_buyuk(il_adi)
    for i in _guvenli_liste(iller):
        if hedef in _temiz_buyuk(i.get("ad")) or _temiz_buyuk(i.get("ad")) in hedef:
            return i
    return None


def _adliye_bul(adliyeler, adliye_adi):
    hedef = _temiz_buyuk(adliye_adi)
    for a in _guvenli_liste(adliyeler):
        if hedef in _temiz_buyuk(a.get("adliyeIsmi")):
            return a
    return None


def _isimle_bul(liste, alt_metin):
    hedef = _temiz_buyuk(alt_metin)
    for it in _guvenli_liste(liste):
        if hedef in _temiz_buyuk(it.get("name")):
            return it
    return None


def _degerle_bul(liste, deger):
    for it in _guvenli_liste(liste):
        if it.get("value") == deger:
            return it
    return None


def _oran_tl_kurus(oran):
    """UYAP'ın faiz oranı giriş alanı tam kısmı/ondalık basamağı ayrı tutuyor
    (ör. 54.6 -> '54' TL kısmı, '6' kuruş kısmı — yakalanan veriden çıkarıldı,
    yalnızca TEK ondalık basamak destekliyor gibi görünüyor)."""
    tam = int(oran)
    kurus = round((oran - tam) * 10)
    return str(tam), (0 if kurus == 0 else str(kurus))


def _referans_kalem_bul(kalem_kodlari, tur):
    """icraTakipAlacakGirisBilgileri.ajx yanıtından (canlı) `tur` (ör. "masraf") için
    isim eşleşmesiyle kod arar — bkz. _REFERANS_ARANACAK_ADLAR. Bulamazsa None döner
    (çağıran taraf ValueError fırlatır); UYDURMA kod DÖNMEZ."""
    for aday_ad in _REFERANS_ARANACAK_ADLAR.get(tur, []):
        it = _isimle_bul(kalem_kodlari, aday_ad)
        if it:
            return {"kod": it.get("value"), "kod_aciklama": it.get("name"), "aciklama": it.get("name")}
    return None


def _kalem_dict(tur, tutar, taraf_index, faiz_orani=None, idx=0, kalem_bilgi_map=None):
    bilgi = (kalem_bilgi_map or ALACAK_KALEM_BILGI)[tur]
    kalem = {
        "selectedTarafHashKeyList": taraf_index,
        "selectedTarafList": ",".join(str(i) for i in taraf_index),
        "temelBilgileri": {
            "alacakTutariTL": tutar, "alacakTutari": tutar,
            "selectedParaBirimi": "PRBRMTL",
            "selectedParaBirimiAciklama": "TL-Türk Lirası",
            "selectedParaBirimiKod": "TL-Türk Lirası",
            "KDV": False,
            "aciklama": bilgi["aciklama"],
            "selectedAlacakKalemKodu": {
                "alacakKalemKodAciklama": bilgi["kod_aciklama"], "alacakKalemKod": bilgi["kod"]},
        },
        "id": idx,
    }
    if tur == "asil_alacak":
        tl, kurus = _oran_tl_kurus(faiz_orani or 0.0)
        kalem["faizBilgileri"] = {
            "selectedFaizTuru": {"tktId": "FAIZT00003", "kod": "00003", "aciklama": "Diğer", "kodTuru": "FAIZT"},
            "faizOraniTL": tl, "faizOrani": faiz_orani, "faizOraniKurus": kurus,
            "selectedFaizSureTipi": "2", "selectedFaizSureTipiAdi": "Yıllık",
        }
    else:
        kalem["faizBilgileri"] = {}
    return kalem


def _tl_format(tutar):
    """1234.5 -> '1.234,50' (TR biçimi: nokta binlik, virgül ondalık)."""
    return f"{tutar:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


_DOSYA_ACIKLAMA_KUYRUK = (
    " oranından az olmamak üzere artan cari faiz oranında temerrüt faizi,  (faiz oranlarının "
    "artması halinde artan oran üzerinden fazin hesaplanması kaydıyla) faizin %5'i oranında "
    "BSMV'si, icra giderleri, avukatlı ücreti ile birlikte tahsili, Borçlar Kanununun 100. "
    "maddesi uyarınca yapılacak kısmi ödemelerin öncelikle, faiz ve giderlere mahsubuyla "
    "tahsili ve borçlar kanunun 586. maddesindeki haklarımız saklı kalarak ve ............. "
    "İcra Müdürlüğünün .........E sayılı dosyası ile tahsilde tekerrür etmemek üzere "
    "ipoteğin satılarak paraya çevrilmesi istemidir."
)


def _dosya_aciklama_uret(alacak_kalemleri):
    """dosyaAciklama_48_4 metnini otomatik üretir — UYAP arayüzünde bu AYRI bir giriş
    kutusu DEĞİLDİR (2026-07-28 canlı testinde doğrulandı); asıl alacak tutarlarını
    faiz oranına göre gruplayıp yakalanan gerçek metin şablonuyla birleştirir."""
    gruplar = {}
    sira = []
    for k in alacak_kalemleri:
        fb = k.get("faizBilgileri") or {}
        oran = fb.get("faizOrani")
        if oran is None:
            continue  # yalnızca asıl alacak (faiz uygulanan) kalemler gruplanır
        oran = round(float(oran), 2)
        gruplar[oran] = gruplar.get(oran, 0.0) + k["temelBilgileri"]["alacakTutariTL"]
        if oran not in sira:
            sira.append(oran)
    # NOT: yakalanan gerçek metinde tutar TR biçiminde (virgül ondalık) ama YÜZDE
    # noktayla yazılmış ("%54.60") — iki farklı biçim aynı cümlede karışık, birebir
    # korundu.
    parcalar = [f"{_tl_format(gruplar[o])}TL için Yıllık (365 Gün) %{o:.2f} Faiz"
               for o in sira]
    on_metin = ("Toplam alacağın fazlaya ilişkin haklarımız ve varsa diğer teminatlardan doğan "
               "talep haklarımız saklı kalmak ve aynı alacak için olandiğer dosyalarda tahsilde "
               "tekerrür olmamak kaydıyla, takip tarihinden itibaren asıl alacak için "
               "hesaplanacak bakiye temerrüt faizi talep hakkı saklı tutularak")
    return on_metin + " " + "; ".join(parcalar) + _DOSYA_ACIKLAMA_KUYRUK


def taslak_aciklama_48_4(kalemler_excel):
    """GUI'nin "1/4 Açıklaması" alanı için TASLAK metin üretir — excel_kalemlerini_oku()
    çıktısını (ham satırlar) alır, kullanıcı bu taslağı onaylamadan önce DÜZENLEYEBİLİR."""
    gecici_kalemler = []
    for satir in (kalemler_excel or []):
        if satir.get("asil_alacak", 0) > 0:
            gecici_kalemler.append(_kalem_dict(
                "asil_alacak", round(satir["asil_alacak"], 2), [0],
                faiz_orani=satir.get("faiz_orani", 0.0), idx=len(gecici_kalemler)))
    return _dosya_aciklama_uret(gecici_kalemler)


def _icra_dosya_bilgileri(state, kota_kullanim_sekli):
    """Yakalanan iki çağrıda (harç hesaplama / tevzi) kotaKullanimSekli farklıydı
    (3 / 0) — geri kalan alanlar aynıydı; bu iki çağrının her biri kendi değerini
    verir, geri kalanı state'ten aynen alınır.

    Kota Tipi: kullanıcı UYAP'ta Avukat/Kurum/Banka/Gayrimenkul/Takip seçeneklerini
    görüyor; bu modül SADECE Gayrimenkul'ü destekler (İpotek takibi zorunlu kılıyor).
    Diğer üç seçeneğin (Avukat/Kurum/Takip) JSON karşılığı bilinmiyor, bu yüzden
    uygulanmadı — bkz. panel'deki "Kota Tipi: Gayrimenkul (sabit)" göstergesi."""
    kriterler = [{"kod": "bk", "mahiyetAdi": "B.K. 100.Madde", "zorunlu": True, "degistirilemez": True},
                {"kod": "bsmv", "mahiyetAdi": "BSMV", "zorunlu": False, "degistirilemez": False}]
    return {
        "selectedIl": state["selected_il"],
        "kotaKullanimSekliText": "Gayrimenkul Dosyası",
        "kotaKullanimSekli": kota_kullanim_sekli,
        "selectedAdliye": state["selected_adliye"],
        "adliyeBirimId": state["adliye_birim_id"], "adliyeIsmi": state["selected_adliye"].get("adliyeIsmi"),
        "selectedTakipTuru": {"name": state["takip_turu_text"], "value": state["takip_turu"]},
        "takipTuru": state["takip_turu"], "takipTuruText": state["takip_turu_text"],
        "selectedTakipSekli": state["takip_sekli_item"], "takipSekli": state["takip_sekli"],
        "takipSekliText": state["takip_sekli_text"],
        "selectedTakipYolu": state["takip_yolu_item"], "takipYolu": state["takip_yolu"],
        "takipYoluText": state["takip_yolu_item"].get("name"),
        "dosyaTevziTipiBanka": False, "dosyaTevziTipiGayrimenkul": True,
        "dosyaAciklama_48_4": state["aciklama_48_4"],
        "dosyaAciklama_48_9": state["aciklama_48_9"],
        **({"dosyaAciklama_48_5": state["aciklama_48_5"]} if state.get("aciklama_48_5") else {}),
        "ipotekRehinAciklama": state["ipotek_rehin_aciklama"],
        "selectedDosyaKriterleri": kriterler,
        "dosyaKriterList": ",".join(k["kod"] for k in kriterler),
        "dosyaKriterTextList": ",".join(k["mahiyetAdi"] for k in kriterler),
        "showHacizTahliyeValue": False, "hacizOnayValue": False, "tahliyeOnayValue": False,
    }


# ── FAZ 1: PREPARE (sorgular + harç hesabı; TEVZİ ALMAZ, dosya açmaz) ─────────
async def prepare(ctx, params):
    """params (bkz. Panel/modules/potek_takip.py):
      mersis_no, tckn_list:[str,...], iban,
      il="İzmir", adliye="İzmir",
      tapu_muduru_adi, ilam_tarihi (gg/aa/yyyy veya gg.aa.yyyy — otomatik normalize edilir),
      yevmiye_yil, yevmiye_sira, ipotek_rehin_aciklama,
      aciklama_48_4  (ZORUNLU — "1/4": alacağın TL tutarı + faiz açıklaması; GUI
                      _dosya_aciklama_uret() ile taslak önerir, kullanıcı düzenler)
      aciklama_48_9  ("1/9": takip yolu açıklaması, boşsa "İpoteğin Paraya Çevrilmesi")
      aciklama_48_5  ("1/5": üçüncü şahıs rehin/ipotek bilgisi — OPSİYONEL, genelde boş)
      takip_turu, takip_yolu, takip_sekli, ilam_turu (int, GUI'nin canlı dropdown'larından)
      vekalet: {"filename":..., "bytes":...}                (ZORUNLU — CZM_VEKALETNAME)
      dayanak_listesi: [{"filename":..., "bytes":...}, ...]  (ZORUNLU, en az 1 — MTS_TAKIBIN_DAYANAGI)
      kalemler: [{"faiz_orani","asil_alacak","gecmis_gun_faizi","bsmv"}, ...]  (excel_kalemlerini_oku çıktısı)

    NOT: "dosyaAciklama_48_5" alan adı yakalanan oturumda hiç görünmedi (o davada
    üçüncü şahıs rehni yoktu, alan muhtemelen boş olduğu için gönderilmedi) —
    "48_4"/"48_9" ile aynı adlandırma örüntüsünden ÇIKARILDI, canlı doğrulanmadı.

    Dönüş: (ozet, state). ozet, onay ekranında kullanıcıya gösterilir."""
    log = ctx.log
    p = params

    mersis_no = _re.sub(r"[^0-9]", "", str(p.get("mersis_no") or ""))
    tckn_list = [_re.sub(r"[^0-9]", "", str(t)) for t in (p.get("tckn_list") or []) if str(t).strip()]
    iban_temiz = _re.sub(r"[^0-9]", "", str(p.get("iban") or ""))
    il = p.get("il") or IL_VARSAYILAN
    adliye = p.get("adliye") or ADLIYE_VARSAYILAN
    kalemler_excel = p.get("kalemler") or []

    if not mersis_no:
        raise ValueError("Alacaklı Mersis numarası girilmedi.")
    if not tckn_list:
        raise ValueError("En az bir borçlu TCKN'si girilmedi.")
    if not iban_temiz:
        raise ValueError("Alacaklı IBAN'ı girilmedi.")
    if not kalemler_excel:
        raise ValueError("Excel'den okunan alacak kalemi bulunamadı.")
    if not str(p.get("ilam_tarihi") or "").strip():
        raise ValueError("İlam/Tescil Tarihi girilmedi (UYAP bunu boş kabul etmiyor).")
    ilam_tarihi = format_date_with_slashes(p.get("ilam_tarihi"))
    if not str(p.get("tapu_muduru_adi") or "").strip():
        raise ValueError("Tapu Müdürlüğü Adı girilmedi.")
    if not str(p.get("ipotek_rehin_aciklama") or "").strip():
        raise ValueError("İpotek/Rehin Açıklaması girilmedi.")
    aciklama_48_4 = str(p.get("aciklama_48_4") or "").strip()
    if not aciklama_48_4:
        raise ValueError("1/4 Açıklaması girilmedi.")
    aciklama_48_9 = str(p.get("aciklama_48_9") or "").strip() or "İpoteğin Paraya Çevrilmesi"
    aciklama_48_5 = str(p.get("aciklama_48_5") or "").strip()  # opsiyonel (üçüncü şahıs rehni yoksa boş)
    vekalet = _belge_coz(p.get("vekalet"))
    if not vekalet:
        raise ValueError("Vekaletname yüklenmedi — UYAP bu evrakı zorunlu istiyor.")
    dayanak_listesi = [d for d in (_belge_coz(d) for d in (p.get("dayanak_listesi") or [])) if d]
    if not dayanak_listesi:
        raise ValueError("Takibin dayanağı belgesi (tapu senedi/ipotek belgesi) yüklenmedi.")

    log("İl/ilçe listesi alınıyor...")
    iller = await _api_json(ctx, "illerIlcelerGetir.ajx", {})
    selected_il = _il_bul(iller, il)
    if not selected_il:
        raise ValueError(f"İl bulunamadı: {il}")

    turu_listesi = await _api_json(ctx, "icra_takip_turu.ajx", {})
    await _api_json(ctx, "mtsDosyaKriterleri_brd.ajx", {})

    log(f"{selected_il.get('ad')} adliyeleri alınıyor...")
    adliyeler = await _api_json(ctx, "icraTakipAdliyeler.ajx", {"ilKodu": selected_il.get("il")})
    selected_adliye = _adliye_bul(adliyeler, adliye)
    if not selected_adliye:
        raise ValueError(f"Adliye bulunamadı: {adliye}")
    adliye_birim_id = selected_adliye.get("adliyeBirimID")

    acilabilir = await _api_json(ctx, "icraTakipDosyaAcilabilirMi.ajx", {"birimId": adliye_birim_id})
    if not (isinstance(acilabilir, dict) and str(acilabilir.get("message")).lower() == "true"):
        raise ValueError(f"UYAP bu adliyede dosya açılamayacağını bildirdi: {acilabilir}")

    await _api_json(ctx, "tevziSiraTipleri.ajx", {"birimId": adliye_birim_id})

    # Takip türü/yolu/şekli — varsayılan İpotek kombinasyonu, ama GUI'deki canlı
    # dropdown'lardan farklı bir seçim geldiyse ONA uyulur (kullanıcı isteğiyle
    # görünür/seçilebilir yapıldı). DİKKAT: aşağıdaki ilamli_list alanları
    # (dosyaAciklama_48_9, ipotekRehinAciklama, dosyaKriterList="bk" vb.) yine
    # de İpotek takibine özgü sabit kalır — başka bir takip şekli seçilirse bu
    # alanlar UYAP'ça reddedilebilir; bu modül temelde İpotek Takip Açma'dır.
    takip_turu = p.get("takip_turu")
    takip_turu = int(takip_turu) if takip_turu is not None else 0
    turu_item = _degerle_bul(turu_listesi, takip_turu)
    if turu_item is None and _guvenli_liste(turu_listesi):
        raise ValueError(f"UYAP takip türü listesinde value={takip_turu} yok.")
    takip_turu_text = turu_item.get("name") if turu_item else "İlamlı Takip"

    yol_listesi = await _api_json(ctx, "icra_takip_yolu.ajx", {"takipTuru": takip_turu})
    takip_yolu_param = p.get("takip_yolu")
    if takip_yolu_param is not None:
        takip_yolu_item = _degerle_bul(yol_listesi, int(takip_yolu_param))
    else:
        takip_yolu_item = _isimle_bul(yol_listesi, "Para ve Teminat Verilmesi Hakkındaki")
    if not takip_yolu_item:
        raise ValueError(f"UYAP takip yolu listesinde seçenek bulunamadı (seçim: {takip_yolu_param}).")
    takip_yolu = takip_yolu_item["value"]

    sekli_listesi = await _api_json(ctx, "icra_takip_sekli.ajx",
                                    {"takipTuru": takip_turu, "takipYolu": takip_yolu})
    takip_sekli_param = p.get("takip_sekli")
    if takip_sekli_param is not None:
        takip_sekli_item = _degerle_bul(sekli_listesi, int(takip_sekli_param))
    else:
        takip_sekli_item = _isimle_bul(sekli_listesi, "İpoteğin Paraya Çevrilmesi")
    if not takip_sekli_item:
        raise ValueError(f"UYAP takip şekli listesinde seçenek bulunamadı (seçim: {takip_sekli_param}).")
    takip_sekli = takip_sekli_item["value"]

    await _api_json(ctx, "icra_takip_mahiyetleri.ajx",
                    {"takipTuru": takip_turu, "takipYolu": takip_yolu, "takipSekli": takip_sekli})
    await _api_json(ctx, "baro_listesi_sorgula.ajx", {})
    await _api_json(ctx, "icraTarafRolTurleri.ajx", {})
    await _api_json(ctx, "get_adres_turleri_by_taraf.ajx", {})

    # ── Alacaklı (kurum) — Mersis no ile ──
    log(f"Mersis no ile alacaklı sorgulanıyor: {mersis_no}")
    kurumlar = await _api_json(ctx, "kurumSorgula.ajx", {"mersisNo": mersis_no})
    if not isinstance(kurumlar, list) or not kurumlar:
        raise ValueError(f"Mersis numarasıyla kurum bulunamadı: {mersis_no} (UYAP yanıtı: {kurumlar})")
    kurum = kurumlar[0]
    kisi_kurum_id = kurum.get("kisiKurumID")
    log(f"Alacaklı: {kurum.get('kurumAdi')}")

    log("Mersis adresi sorgulanıyor...")
    adresler = await _api_json(ctx, "getAdresListesi_brd.ajx",
                               {"kisiKurumId": kisi_kurum_id, "tarafTur": 2})
    if not isinstance(adresler, list):
        log(f"Uyarı: alacaklı için adres listesi dönmedi (UYAP yanıtı: {adresler}); "
           "adressiz devam ediliyor.")
        adresler = []
    mersis_adresi = next((a for a in adresler if a.get("adresTuru") == "ADRTR00011"),
                        (adresler[0] if adresler else None))
    if mersis_adresi:
        mersis_adresi = dict(mersis_adresi, isSelected=True)
    elif adresler == []:
        log("Uyarı: alacaklı için hiçbir adres bulunamadı — adresList boş gönderilecek.")

    try:
        await _api_json(ctx, "getIbanListesi.ajx", {"kisiKurumId": kisi_kurum_id, "tarafTur": 1})
    except Exception:
        pass  # kurumun kayıtlı ibanı olmayabilir — aşağıda kullanıcının girdiği iban ayrıca doğrulanıyor

    iban_detay = await _api_json(ctx, "geIbanDetails.ajx",
                                 {"iban": iban_temiz, "isSansurlenecek": False})
    iban_deger = iban_detay.get("value", {}) if isinstance(iban_detay, dict) else {}
    if not iban_deger:
        raise ValueError(f"UYAP IBAN detayını döndürmedi (iban: {iban_temiz}). IBAN'ı kontrol edin.")
    iban_numarasi = iban_deger.get("ibanNumarasi") or f"TR{iban_temiz}"

    kurum_taraf = {
        "id": "ms_kurum_0",
        "tarafSifati": {"rolID": 21, "rolAdi": "ALACAKLI", "sanikStatusu": "H", "davaliDavaciGrubu": "N"},
        "sorguTuru": 0, "tarafTuru": "KURUM", "isVekil": True,
        "temelBilgileri": kurum, "tarafAdi": kurum.get("kurumAdi"),
        "sucBilgisi": [], "tazminatBilgisi": [],
        "adresList": [mersis_adresi] if mersis_adresi else [],
        "adresBilgisi": mersis_adresi,
        "mernisAdresiKullan": False, "eTebligatAdresiKullan": False,
        "isVekilIban": False,
    }
    kurum_taraf["iban"] = {
        "bankaAdi": iban_deger.get("bankaAdi", ""), "iban": iban_numarasi,
        "ibanNumarasi": iban_numarasi, "hesapGenel": iban_deger.get("hesapGenel", True),
        "isSelected": True, "id": 0, "isVekilIban": False,
    }
    kurum_taraf["hesapBilgisi"] = kurum_taraf["iban"]

    # ── Borçlu(lar) — TCKN ile (birden fazla olabilir) ──
    kisi_taraflari = []
    for i, tckn in enumerate(tckn_list):
        ctx.check_cancel()
        log(f"Borçlu sorgulanıyor: {tckn}")
        kisi = await _api_json(ctx, "kisiSorgula.ajx", {"tcKimlikNo": tckn, "tarafSifati": 22})
        if not isinstance(kisi, dict) or not kisi.get("tcKimlikNo"):
            raise ValueError(f"Borçlu T.C. sorgulaması başarısız: {tckn} — UYAP yanıtı: {kisi}")
        try:
            await _api_json(ctx, "eTebligatSorgula.ajx", {"tcKimlikNo": tckn})
        except Exception as e:
            log(f"Uyarı: e-Tebligat sorgusu başarısız (önemsiz, devam ediliyor): {e}")
        mernis = await _api_json(ctx, "mtsMernisAdresiKontrol_brd.ajx", {"tcKimlikNo": tckn})
        if mernis is False or mernis == "false":
            raise ValueError(
                f"MERNİS'te kayıtlı adres yok: {tckn} ({kisi.get('adi')} {kisi.get('soyadi')})")
        kisi_taraflari.append({
            "id": f"ms_kisi_{i}",
            "tarafSifati": {"rolID": 22, "rolAdi": "BORÇLU VE MÜFLİS",
                            "sanikStatusu": "E", "davaliDavaciGrubu": "L"},
            "sorguTuru": 0, "tarafTuru": "KISI", "isVekil": False,
            "tarafAdi": f"{kisi.get('adi','')} {kisi.get('soyadi','')}".strip(),
            "temelBilgileri": kisi, "sucBilgisi": [], "tazminatBilgisi": [],
            "mernisAdresiKullan": True, "eTebligatAdresiKullan": False,
        })
        log(f"Borçlu bulundu: {kisi.get('adi')} {kisi.get('soyadi')}")

    taraf_list = [kurum_taraf] + kisi_taraflari
    taraf_index = list(range(len(taraf_list)))

    ilam_turleri = await _api_json(ctx, "icra_takip_ilam_turleri.ajx", {})
    ilam_turu_param = p.get("ilam_turu")
    if ilam_turu_param is not None:
        ilam_turu_item = _degerle_bul(ilam_turleri, int(ilam_turu_param))
    else:
        ilam_turu_item = _isimle_bul(ilam_turleri, "Diğer")
    if not ilam_turu_item:
        raise ValueError(f"UYAP ilam türü listesinde seçenek bulunamadı (seçim: {ilam_turu_param}).")
    ilam_turu = ilam_turu_item["value"]
    ilam_turu_text = ilam_turu_item.get("name")

    await _api_json(ctx, "faizTurleri.ajx", {})
    await _api_json(ctx, "get_para_birimleriJSON.ajx", {})
    alacak_giris = await _api_json(ctx, "icraTakipAlacakGirisBilgileri.ajx", {"takipTuru": takip_turu})
    kalem_kodlari = _guvenli_liste(alacak_giris[0]) if isinstance(alacak_giris, list) and alacak_giris else []
    await _api_json(ctx, "icra_takip_ilam_dosya_turu.ajx", {"ilamTuru": ilam_turu})

    # "diger_faiz_alacagi"/"masraf" satırlarda VARSA (bkz. uyap_core.ipotek.xml_parse —
    # Excel akışı bu iki alanı hiç doldurmaz) kodlarını CANLI referanstan bul; bulunamazsa
    # DUR (tutarı sessizce atlama) — bkz. ALACAK_KALEM_BILGI üstündeki not.
    kalem_bilgi_map = dict(ALACAK_KALEM_BILGI)
    for tur in ("diger_faiz_alacagi", "masraf"):
        if any(satir.get(tur, 0) > 0 for satir in kalemler_excel):
            bilgi = _referans_kalem_bul(kalem_kodlari, tur)
            if not bilgi:
                raise ValueError(
                    f"UYAP referans listesinde (icraTakipAlacakGirisBilgileri.ajx) '{tur}' için "
                    f"aranan adlardan ({_REFERANS_ARANACAK_ADLAR[tur]}) hiçbiri bulunamadı — bu "
                    "tutarlar GÖNDERİLEMEZ, otomatik uydurma kod kullanılmaz. XML'den gelen bu "
                    "kalemleri UYAP ekranından elle girin.")
            kalem_bilgi_map[tur] = bilgi

    # ── Alacak kalemleri — satırlardan (Excel: yalnızca D/E/G; XML: her XML alacak
    # kalemi kendi satırında, birleştirilmeden — bkz. uyap_core.ipotek.xml_parse) ──
    alacak_kalemleri = []
    for satir in kalemler_excel:
        if satir.get("asil_alacak", 0) > 0:
            alacak_kalemleri.append(_kalem_dict(
                "asil_alacak", round(satir["asil_alacak"], 2), taraf_index,
                faiz_orani=satir.get("faiz_orani", 0.0), idx=len(alacak_kalemleri),
                kalem_bilgi_map=kalem_bilgi_map))
        if satir.get("gecmis_gun_faizi", 0) > 0:
            alacak_kalemleri.append(_kalem_dict(
                "gecmis_gun_faizi", round(satir["gecmis_gun_faizi"], 2), taraf_index,
                idx=len(alacak_kalemleri), kalem_bilgi_map=kalem_bilgi_map))
        if satir.get("bsmv", 0) > 0:
            alacak_kalemleri.append(_kalem_dict(
                "bsmv", round(satir["bsmv"], 2), taraf_index, idx=len(alacak_kalemleri),
                kalem_bilgi_map=kalem_bilgi_map))
        if satir.get("diger_faiz_alacagi", 0) > 0:
            alacak_kalemleri.append(_kalem_dict(
                "diger_faiz_alacagi", round(satir["diger_faiz_alacagi"], 2), taraf_index,
                idx=len(alacak_kalemleri), kalem_bilgi_map=kalem_bilgi_map))
        if satir.get("masraf", 0) > 0:
            alacak_kalemleri.append(_kalem_dict(
                "masraf", round(satir["masraf"], 2), taraf_index, idx=len(alacak_kalemleri),
                kalem_bilgi_map=kalem_bilgi_map))

    if not alacak_kalemleri:
        raise ValueError("Hiçbir pozitif tutarlı alacak kalemi okunamadı.")

    ilamli_list = [{
        "id": "ms_ilam_0", "ilamTuru": ilam_turu, "ilamTuruText": ilam_turu_text,
        "dosyaNo": "undefined/undefined",
        "yevmiyeYil": p.get("yevmiye_yil") or "",
        "yevmiyeSira": p.get("yevmiye_sira") or "",
        "ilamTarihi": ilam_tarihi,
        "ilamliKurumAdi": p.get("tapu_muduru_adi") or "",
        "ilamliAciklama": p.get("ipotek_rehin_aciklama") or "",
        "alacakKalemleri": alacak_kalemleri,
    }]

    state = {
        "taraf_list": taraf_list, "ilamli_list": ilamli_list,
        "selected_il": selected_il, "selected_adliye": selected_adliye,
        "adliye_birim_id": adliye_birim_id,
        "takip_turu": takip_turu, "takip_turu_text": takip_turu_text,
        "takip_yolu": takip_yolu, "takip_yolu_item": takip_yolu_item,
        "takip_sekli": takip_sekli, "takip_sekli_item": takip_sekli_item,
        "takip_sekli_text": takip_sekli_item.get("name"),
        "aciklama_48_4": aciklama_48_4, "aciklama_48_9": aciklama_48_9,
        "aciklama_48_5": aciklama_48_5,
        "ipotek_rehin_aciklama": p.get("ipotek_rehin_aciklama") or "",
        "vekalet": vekalet, "dayanak_listesi": dayanak_listesi,
    }

    log("Harç hesaplanıyor...")
    harc_sonuc = await _api_json(ctx, "icra_harc_hesaplama_islemleri.ajx", {
        "IcraDosyaBilgileri": json.dumps(_icra_dosya_bilgileri(state, kota_kullanim_sekli=3),
                                        ensure_ascii=False),
        "TarafList": json.dumps(taraf_list, ensure_ascii=False),
        "IlamliList": json.dumps(ilamli_list, ensure_ascii=False),
        "IlamsizList": [],
        "TahsilatList": [],
    })
    if not isinstance(harc_sonuc, list) or len(harc_sonuc) < 2:
        raise ValueError(f"UYAP harç hesaplayamadı. Yanıt: {harc_sonuc}")
    harc_listesi, harc_toplam = harc_sonuc[0], harc_sonuc[1]

    await _api_json(ctx, "icra_takip_tahsilat_nedenleri.ajx", {})

    ozet = _ozet_kur(kurum, kisi_taraflari, iban_numarasi, alacak_kalemleri, harc_listesi, harc_toplam)
    return ozet, state


def _ozet_kur(kurum, kisi_taraflari, iban_numarasi, alacak_kalemleri, harc_listesi, harc_toplam):
    """Onay ekranında gösterilecek özet — alacak kalemi TUTARLARINI tek tek
    listeler (ondalık ayraç karışıklığını kullanıcı burada YAKALASIN diye)."""
    kalemler_ozet = []
    for k in alacak_kalemleri:
        tb = k["temelBilgileri"]
        satir = {"ad": tb["aciklama"], "tutar": tb["alacakTutariTL"]}
        if k.get("faizBilgileri"):
            satir["faiz_orani"] = k["faizBilgileri"].get("faizOrani")
        kalemler_ozet.append(satir)
    toplam_alacak = round(sum(k["tutar"] for k in kalemler_ozet), 2)
    harc_ozet = [{"ad": h.get("harcMasrafAdi") or "Masraf", "miktar": h.get("hesapMiktar", 0.0)}
                 for h in (harc_listesi or []) if isinstance(h, dict)]
    return {
        "alacakli": kurum.get("kurumAdi"),
        "mersis_no": kurum.get("mersisNo"),
        "iban": iban_numarasi,
        "borclular": [{"tckn": b["temelBilgileri"].get("tcKimlikNo"),
                       "ad": b["temelBilgileri"].get("adi"), "soyad": b["temelBilgileri"].get("soyadi")}
                      for b in kisi_taraflari],
        "kalemler": kalemler_ozet,
        "toplam_alacak": toplam_alacak,
        "harclar": harc_ozet,
        "harc_toplam": harc_toplam,
    }


# ── FAZ 2: FINALIZE (tevzi numarası al + UDF indir + e-imza + evrak gönder) ──
async def finalize(ctx, state):
    """Yalnızca kullanıcı onay ekranındaki özeti (alacak kalemi tutarları dahil)
    onayladıktan SONRA çağrılmalı. Tevzi adımı GERÇEK bir dosya açar (dosya bu
    noktadan sonra UYAP'ta "tamamlanmayan dosyalar" listesinde görünür)."""
    from .. import uyap_proxy, udf_signer
    log = ctx.log

    gw = uyap_proxy.gw
    if gw is None:
        raise RuntimeError("UYAP oturumu hazır değil (gw=None).")

    taraf_list = state["taraf_list"]
    ilamli_list = state["ilamli_list"]

    log("Tevzi numarası alınıyor — bu adımdan sonra dosya UYAP'ta GERÇEKTEN açılmış olacak...")
    tevzi_sonuc = await _api_json(ctx, "icra_takip_tevzi_islemleri.ajx", {
        "IcraDosyaBilgileri": json.dumps(_icra_dosya_bilgileri(state, kota_kullanim_sekli=0),
                                        ensure_ascii=False),
        "TarafList": json.dumps(taraf_list, ensure_ascii=False),
        "IlamliList": json.dumps(ilamli_list, ensure_ascii=False),
        "IlamsizList": [],
        "TahsilatList": [],
    })
    dosya_id = tevzi_sonuc.get("dosyaId") if isinstance(tevzi_sonuc, dict) else None
    if not dosya_id:
        raise ValueError(f"UYAP tevzi numarası döndürmedi. Yanıt: {tevzi_sonuc}")
    if isinstance(dosya_id, str):
        dosya_id = dosya_id.strip().strip('"').strip("'")
    log(f"Tevzi tamamlandı. Dosya ID: {dosya_id}")

    await _api_json(ctx, "tamamlanmayanDosyalar_brd.ajx", {"dosyaTurKod": 35})

    # UYARI: bu iki adım (UDF indirme yolu + evrak "items" meta verisi) canlı
    # doğrulanmadı — bkz. modül başlığı. Hata olursa dosya "tamamlanmayan
    # dosyalar" ekranından elle tamamlanabilir, kaybolmaz.
    resp = await ctx.uyap("GET", "icraTakipTalebiIndir.uyap", params={"dosyaId": dosya_id}, write=False)
    if resp.status_code >= 400:
        raise ValueError(f"Takip talebi indirilemedi (HTTP {resp.status_code}). "
                         f"Dosya UYAP'ta açık kaldı (ID: {dosya_id}) — "
                         "'Tamamlanmayan Dosyalar' ekranından elle devam edin.")
    udf_bytes = resp.content
    if not udf_bytes:
        raise ValueError(f"Takip talebi taslağı boş indi (Dosya ID: {dosya_id}).")

    guvenli_isim = "Takip_Talebi.udf"
    log("Takip talebi e-imzalanıyor (headless)...")
    cert_id = getattr(gw, "cert_id", None)
    pin = getattr(getattr(gw, "login_args", None), "pin", None)
    loop = asyncio.get_running_loop()
    signed = await loop.run_in_executor(
        None, udf_signer.sign_document, udf_bytes, guvenli_isim, cert_id, pin)
    log(f"İmzalandı ({len(signed)} bayt).")

    # Evrak sırası 2026-07-28 ham ağ kaydından (ag_kaydi.log) BİREBİR: UDF + Vekaletname
    # (zorunlu) + dayanak belge(ler)i (zorunlu, en az 1). Bkz. modül başlığı notu.
    from ..mts.evrak import items_kur, mime_belirle
    vekalet = state["vekalet"]
    dayanak_listesi = state["dayanak_listesi"]
    evraklar = [{"tur": "ICR_TAKIP_TLP", "filename": guvenli_isim, "bytes": signed}]
    evraklar.append({"tur": "CZM_VEKALETNAME",
                     "filename": vekalet.get("filename") or "vekalet.pdf",
                     "bytes": vekalet["bytes"]})
    for d in dayanak_listesi:
        evraklar.append({"tur": "MTS_TAKIBIN_DAYANAGI",
                         "filename": d.get("filename") or "dayanak.pdf",
                         "bytes": d["bytes"]})

    items_json, alanlar = items_kur(evraklar)
    files = {}
    for (alan, fname), ev in zip(alanlar, evraklar):
        files[alan] = (fname, ev["bytes"], mime_belirle(fname))
    files["items"] = (None, items_json)
    files["dosyaId"] = (None, dosya_id)

    log(f"Evrak gönderiliyor ({len(alanlar)} adet)...")
    ev_resp = await ctx.uyap("POST", "davaAcilisEvrakGonderme_brd.ajx", files=files)
    try:
        sonuc = json.loads(ev_resp.text) if ev_resp.text else {}
    except Exception:
        sonuc = {"type": "unknown", "message": ev_resp.text}
    if not isinstance(sonuc, dict) or sonuc.get("type") != "success":
        raise ValueError(f"Evrak gönderme başarısız (Dosya ID: {dosya_id}). UYAP yanıtı: {sonuc} — "
                         "'Tamamlanmayan Dosyalar' ekranından elle devam edin.")
    log(f"✓ Evrak gönderildi: {sonuc.get('message')}")

    await _api_json(ctx, "tamamlanmayanDosyalar_brd.ajx", {"dosyaTurKod": 35})
    return {"dosya_id": dosya_id, "evrak_sonuc": sonuc}
