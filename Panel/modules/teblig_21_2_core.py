# -*- coding: utf-8 -*-
"""
T.K. 21/2 Şerhli Yeniden Tebliğ Talebi — Uygunluk Tarama (mantık, arayüzsüz)
=============================================================================
Kullanıcının elle yaptığı süreç: bir tebligat "İADE" dönmüşse, borçlunun
MERNİS adresi sorgulanır ve İLK Kapalı Tebligat'ın çıktığı adresle
karşılaştırılır. Adresler EŞLEŞİYORSA (tebligat zaten doğru/güncel mernis
adresine çıkmış ama teslim edilememişse) T.K.21/2 şerhli yeniden tebliğ
talebine UYGUNDUR. Eşleşmiyorsa (mernis'e hiç çıkmamışsa) bu akışın kapsamı
dışında farklı bir yöntem gerekir (kullanıcı kararı, 2026-08-13 — canlı
doğrulandı: dosya 2026/98353'te adresler birebir eşleşiyordu ve kullanıcı
gerçekten 21/2 göndermişti; aynı borçlunun 2026/98345 dosyasında henüz talep
YOKTU).

Bu modül YALNIZCA tarar/raporlar — hiçbir yerde talep GÖNDERMEZ/ÖDEME
YAPMAZ. `getIcraTalepEvrakHazirla.uyap` / `/__uyap_agent__/sign_udf` /
`avukatIcraTalepEvrakiGonder.ajx` endpoint'lerine giden bir kod yolu
KASITLI OLARAK yok — gönderme+ödeme ayrı, sonraki bir onay gerektiren iş.

Akış (her aday için):
  1. Yerel DB'den aday seç: `TebligatBarkod.ptt_durumu == "İADE"`,
     fiziki (elektronik değil), borçlusu TEKİL çözülmüş (bkz.
     barkod_sorgu._borclu_coz_tekil) satırlar.
  2. `barkod_sorgu._dosya_id_coz` ile TAZE dosyaId çözülür (UYAP'ın dosyaId'si
     oturumluk — bkz. models.py Dosya yorumu).
  3. `dosya_borclu_list.ajx` ile TAZE kisiKurumId bulunur (TCKN/unvan ile
     eşleştirilir).
  4. `dosya_core.evrak_listesi_getir` ile evrak listesi çekilir:
     - "Avukat Portal Tebligat Talebi" türünde bir evrak VARSA → talep zaten
       gönderilmiş, dur (bkz. barkod_sorgu.TALEP_EVRAK_TUR — dosya
       açılışındaki otomatik tebligat bu türde DEĞİL).
     - Yoksa: `TebligatBarkod.birim_evrak_no` ile aynı "Kapalı Tebligat"
       evrakı bulunur.
  5. O evrakın PDF'i indirilip (`barkod_sorgu._evrak_pdf_indir`) düz metne
     çevrilir (`barkod_sorgu._pdf_govde_metni`); adres regex'le çıkarılır.
  6. `borclu_bilgileri_goruntule_mernis.ajx` ile MERNİS adresi sorgulanır —
     bu endpoint yerel yetki jetonu istiyor (canlı doğrulandı, 2026-08-13):
     `Uyap Haricen Giriş/uyap_core/uyap_proxy.py:1436-1469`, jeton dosyası
     `%LOCALAPPDATA%\\UyapIcra\\gw_local_token` (aynı Windows kullanıcısı
     okuyabilir — kodun kendi yorumuna göre yerel Python istemcileri için
     TASARLANMIŞ resmi mekanizma).
  7. İki adres normalize edilip (mahalle/sokak eki farkları giderilerek)
     karşılaştırılır, sonuç kategorize edilir.

Çok borçlulu dosyalarda `TebligatBarkod.borclu` boşsa (bkz.
barkod_sorgu._borclu_coz_tekil) o kayıt taramaya hiç girmez — tahmin
YÜRÜTÜLMEZ.
"""
import json
import os
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import icra_core  # noqa: E402  (SorguMotoru, tr_lower)
import dosya_core  # noqa: E402  (_django_hazirla, evrak_listesi_getir, _arka_plan_istek)
import barkod_sorgu as bs  # noqa: E402  (_dosya_id_coz, _evrak_pdf_indir, _pdf_govde_metni, TALEP_EVRAK_TUR)

OFFICE_BASE = "http://127.0.0.1:8800"

_GW_TOKEN_YOLU = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "UyapIcra", "gw_local_token")


def _gw_token():
    """Yerel ofis köprüsünün (127.0.0.1:8800) hassas endpoint'ler için
    istediği yetki jetonu — bkz. modül başlığı. HER ÇAĞRIDA dosyadan taze
    okunur, ÖNBELLEKLENMEZ (kullanıcı bulgusu, 2026-08-14: ofis her
    açılışta yeni bir jeton üretip dosyanın üzerine yazıyor — eskiden bu
    fonksiyon jetonu bir kez okuyup sonsuza dek önbellekte tutuyordu; ofis
    Panel açıkken yeniden başlarsa Panel elindeki eski jetonla "ofis yetki
    hatası (HTTP 403)" alıyordu, kendiliğinden düzelmiyordu — bkz.
    barkod_sorgu._yerel_jetonu_oku, AYNI düzeltme). Dosya yoksa/okunamazsa
    boş döner (çağıran jetonsuz dener, ofis 403 verirse hata olarak yukarı
    yansır — sessizce yutulmaz)."""
    try:
        with open(_GW_TOKEN_YOLU, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _post_yerel_jetonlu(endpoint, payload, log, timeout=90, denemeler=3):
    """127.0.0.1:8800'e POST — `X-Uyap-Local-Token` başlığı AÇIKÇA eklenir.
    `icra_core.SorguMotoru`'nun gönderdiği başlıklarla bazı endpoint'lerde
    (ör. mernis sorgusu) açıklanamayan biçimde tutarsız 403 alındı (canlı
    test, 2026-08-13) — burada belirsiz sezgiye güvenilmez, jeton her zaman
    sunulur."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    token = _gw_token()
    son_hata = None
    for deneme in range(denemeler):
        try:
            req = urllib.request.Request(f"{OFFICE_BASE}/{endpoint}", data=body, method="POST")
            req.add_header("content-type", "application/json")
            req.add_header("accept", "application/json, text/plain, */*")
            req.add_header("referer", "https://avukat.uyap.gov.tr/")
            if token:
                req.add_header("X-Uyap-Local-Token", token)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                metin = r.read().decode("utf-8", "replace")
            try:
                return json.loads(metin)
            except Exception:
                return metin
        except Exception as e:
            son_hata = e
            log(f"  ⚠️ {endpoint} deneme {deneme + 1}/{denemeler}: {e}")
    raise son_hata


# ── Adres çıkarımı (Kapalı Tebligat PDF'i) ───────────────────────────────────
# Canlı doğrulandı (2026-08-13, dosya 2026/98353): PDF gövde metninde adres,
# "TC:<maskeli kimlik>" hemen ardından, "Mühür ve İmza" (ya da form tekrar
# ederse "BU ZARFTA"/"BURADAN KATLAYINIZ") öncesine kadar düz metin olarak
# geçiyor — örn. "Ataşehir Mah. 8213 Sk. No:19 İç Kapı No:3  Çiğli/ İzmir".
_ADRES_BLOK_RE = re.compile(
    r"TC:[\d*]{4,15}\s*(.+?)(?:Mühür ve İmza|BU ZARFTA|BURADAN KATLAYINIZ)", re.DOTALL)
_ADRES_PARCA_RE = re.compile(
    r"(?P<mahalle>.+?)\s+Mah\.\s+(?P<sokak>.+?)\s+No:\s*(?P<disno>\S+)"
    r"(?:\s+İç Kapı No:\s*(?P<icno>\S+))?\s+(?P<ilce>[^/]+?)\s*/\s*(?P<il>.+)$")


def _teblig_adresi_cikar(govde_metni):
    """Kapalı Tebligat PDF gövde metninden adres alanlarını çıkarır — desen
    uymazsa None (tahmin YÜRÜTÜLMEZ, çağıran 'elle inceleyin' der)."""
    m = _ADRES_BLOK_RE.search(govde_metni or "")
    if not m:
        return None
    blok = m.group(1).strip()
    m2 = _ADRES_PARCA_RE.match(blok)
    if not m2:
        return None
    d = m2.groupdict()
    return {"mahalle": d["mahalle"], "sokak": d["sokak"], "disno": d["disno"],
            "icno": d.get("icno") or "", "ilce": d["ilce"], "il": d["il"], "ham": blok}


def _mernis_adres_cikar(mernis_yanit):
    """`borclu_bilgileri_goruntule_mernis.ajx` yanıtından yapısal adresi
    çıkarır (canlı doğrulanmış alan adları — bkz. modül başlığı). Kayıt
    yoksa/şekli beklenmedikse None."""
    try:
        adr = mernis_yanit["sorguSonucDVO"]["mernisAdres"]["adresler"][0]["ilIlceMerkeziAdresi"]
    except Exception:
        return None
    ham = (f"{adr.get('mahalle', '')} {adr.get('csbm', '')} No:{adr.get('disKapiNo', '')} "
           f"İç Kapı No:{adr.get('icKapiNo', '')}  {adr.get('ilce', '')}/ {adr.get('il', '')}")
    return {"mahalle": adr.get("mahalle", ""), "sokak": adr.get("csbm", ""),
            "disno": str(adr.get("disKapiNo", "") or ""), "icno": str(adr.get("icKapiNo", "") or ""),
            "ilce": adr.get("ilce", ""), "il": adr.get("il", ""), "ham": ham}


_EK_SOZLUK = [
    # Desenler, _parca_normalize'ın tr_lower(s) UYGULADIKTAN SONRAKİ hâline göre
    # yazılmalı — kullanıcı bulgusu (2026-08-13): "sokağı"/"bulvarı"/"meydanı"
    # desenleri ğ/ı harflerini LİTERAL taşıyordu; tr_lower bu harfleri ÖNCEDEN
    # g/i'ye çevirdiği için (İ/I/ı→i, ğ→g, bkz. icra_core.tr_lower) bu ekler
    # HİÇBİR ZAMAN eşleşmiyor, tam yazılan ("...Sokağı") adresler kısaltılmış
    # ("...Sk.") karşılıklarıyla eşleşmiyordu — görünürde "aynı adres" iki
    # tarafın "21/2 için uygun" yerine "farklı yöntem gerekli" çıkmasına yol
    # açıyordu.
    (r"\bmahallesi\b", ""), (r"\bmah\.?\b", ""),
    (r"\bsokagi\b", ""), (r"\bsokak\b", ""), (r"\bsk\.?\b", ""),
    (r"\bcaddesi\b", ""), (r"\bcadde\b", ""), (r"\bcd\.?\b", ""),
    (r"\bbulvari\b", ""), (r"\bbulvar\b", ""), (r"\bblv\.?\b", ""),
    (r"\bmeydani\b", ""), (r"\bmeydan\b", ""), (r"\bmey\.?\b", ""),
]


def _parca_normalize(s):
    """Türkçe büyük/küçük harf DUYARSIZ (bkz. icra_core.tr_lower) + mahalle/
    sokak/cadde eki farklarını gideren normalize — tam metin eşleşmesi yerine
    alan alan kıyaslamayı mümkün kılar."""
    s = icra_core.tr_lower(s or "")
    for pat, rep in _EK_SOZLUK:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _adresler_eslesiyor_mu(a, b):
    for k in ("mahalle", "sokak", "disno", "icno", "ilce", "il"):
        if _parca_normalize(a.get(k, "")) != _parca_normalize(b.get(k, "")):
            return False
    return True


def _kisi_kurum_id_bul(borclular_yanit, taraf):
    """Canlı `dosya_borclu_list.ajx` yanıtında yerel `Taraf` kaydına karşılık
    gelen `kisiKurumId`'yi bulur — TCKN varsa TCKN ile, yoksa (tüzel) unvanla
    eşleştirir; tek kayıt varsa güvenle o kabul edilir."""
    if not isinstance(borclular_yanit, list) or not borclular_yanit:
        return None
    if taraf.tckn:
        for b in borclular_yanit:
            k = b.get("kisiTumDVO") or {}
            if str(k.get("tcKimlikNo", "")).strip() == str(taraf.tckn).strip():
                return b.get("kisiKurumId")
    if taraf.unvan:
        for b in borclular_yanit:
            k = b.get("kurumTumDVO") or b.get("kisiTumDVO") or {}
            unvan = k.get("unvan") or k.get("kurumAdi") or ""
            if icra_core.tr_lower(unvan) == icra_core.tr_lower(taraf.unvan):
                return b.get("kisiKurumId")
    if len(borclular_yanit) == 1:
        return borclular_yanit[0].get("kisiKurumId")
    return None


def _adres_metni(d):
    if not d:
        return ""
    return d.get("ham") or (f"{d.get('mahalle', '')} {d.get('sokak', '')} No:{d.get('disno', '')} "
                             f"İç Kapı No:{d.get('icno', '')} {d.get('ilce', '')}/{d.get('il', '')}")


def _aday_kayitlari(secili_kayitlar):
    """Yerel DB'den aday `TebligatBarkod` satırlarını döner — bkz. modül
    başlığı adım 1. `secili_kayitlar` verilirse (birimAdi+dosyaNo çiftleri,
    ör. barkod_sorgu_panel.py'nin Geçmiş tablosu satırları) yalnız o
    dosyalara daraltılır."""
    dosya_core._django_hazirla()
    from icra_models.models import TebligatBarkod
    qs = (TebligatBarkod.objects
          .filter(ptt_durumu="İADE", elektronik_tebligat=False, borclu__isnull=False)
          .select_related("dosya", "dosya__birim", "borclu", "borclu__taraf"))
    kayitlar = list(qs)
    if secili_kayitlar:
        anahtarlar = {(icra_core.tr_lower(r.get("birimAdi", "")), str(r.get("dosyaNo", "")).strip())
                      for r in secili_kayitlar}
        kayitlar = [tb for tb in kayitlar
                    if (icra_core.tr_lower(tb.dosya.birim.ad), tb.dosya.dosya_no) in anahtarlar]
    return kayitlar


# Bu kategorilere ulaşmış bir dosya için sonuç DEĞİŞMEZ (adres kıyası aynı
# sabit PDF/mernis anlık görüntüsüne dayanır) — bkz. iade_tarama önbellek
# kontrolü. "hata …" ve "elle inceleyin …" BİLEREK dışarıda: bunlar geçici/
# çözülebilir durumlar, kullanıcı yeniden denemek isteyebilir.
_DEFINITIF_KATEGORILER = {
    "21/2 için uygun", "farklı yöntem gerekli (mernis'e çıkmamış)", "zaten gönderilmiş",
}


def iade_tarama(secili_kayitlar=None, log_fn=None):
    """Ana giriş noktası. `secili_kayitlar`: None ise yerel DB'deki TÜM
    İADE+fiziki+tek-borçlulu adaylar taranır; liste verilirse (dict'ler,
    en az `birimAdi`/`dosyaNo` alanlarıyla) yalnız o dosyalara daraltılır.
    Döner: list[dict] — her satırda birim, dosyaNo, borclu, evrakTarihi,
    iadeTarihi, tebligAdresi, mernisAdresi, kategori. UYAP'a hiçbir
    yazma/gönderme isteği ATMAZ (bkz. modül başlığı) — yalnız kendi tarama
    sonucunu yerel TebligatBarkod.t212_* alanlarına kaydeder (bkz.
    _sonuc_ekle / son_tarama_sonuclarini_getir), böylece "Son 21/2 Raporu"
    programı yeniden başlatsanız bile kaybolmaz.

    Kullanıcı bulgusu (2026-08-14): "bir dosyada tekrar tekrar mernis
    sorgusu yapmak çok büyük sorun" — DAHA ÖNCE kesin bir sonuca (bkz.
    _DEFINITIF_KATEGORILER) ulaşmış bir aday artık CANLI YENİDEN
    TARANMAZ, önbellekteki (TebligatBarkod.t212_*) sonuç aynen
    döndürülür — o dosya için MERNİS/PDF isteği bir daha ATILMAZ. Yalnız
    hiç taranmamış ya da son denemesi hata/elle-inceleyin ile bitmiş
    adaylar canlı taranır."""
    log = log_fn or (lambda *a, **k: None)
    adaylar = _aday_kayitlari(secili_kayitlar)
    if not adaylar:
        log("Taranacak aday bulunamadı (seçili dosyalarda İADE+fiziki+tek-borçlulu tebligat yok).")
        return []

    from django.utils import timezone

    motor = icra_core.SorguMotoru(log)
    sonuclar = []

    def _sonuc_ekle(tb, satir):
        # Kullanıcı bulgusu (2026-08-14): "son 21/2 raporu silindi, bu
        # bilgiler veritabanına eklenmiyor mu?" — eskiden tarama sonucu
        # yalnız Panel'in belleğinde tutuluyordu. Artık HER taramada
        # (hata/elle-inceleyin dahil, yalnız "uygun" değil) TebligatBarkod'a
        # yazılır — "Son 21/2 Raporu" bundan sonra buradan okunur (bkz.
        # son_tarama_sonuclarini_getir).
        try:
            tb.t212_kategori = satir.get("kategori", "")
            tb.t212_teblig_adresi = satir.get("tebligAdresi", "")
            tb.t212_mernis_adresi = satir.get("mernisAdresi", "")
            tb.t212_tarandi_zamani = timezone.now()
            tb.save(update_fields=["t212_kategori", "t212_teblig_adresi",
                                   "t212_mernis_adresi", "t212_tarandi_zamani"])
        except Exception as e:
            log(f"  ⚠️ Tarama sonucu veritabanına yazılamadı: {e}")
        sonuclar.append(satir)

    for tb in adaylar:
        dosya = tb.dosya
        birim_ad, dosya_no = dosya.birim.ad, dosya.dosya_no
        borclu_ad = str(tb.borclu.taraf)

        if tb.t212_tarandi_zamani and tb.t212_kategori in _DEFINITIF_KATEGORILER:
            log(f"— {birim_ad} {dosya_no} ({borclu_ad}): önbellekten "
                f"({tb.t212_tarandi_zamani:%d.%m.%Y %H:%M}) — MERNİS/PDF sorgusu ATLANDI.")
            sonuclar.append({
                "birim": birim_ad, "dosyaNo": dosya_no, "borclu": borclu_ad,
                "evrakTarihi": tb.evrak_tarihi, "iadeTarihi": tb.son_islem_tarihi,
                "tebligAdresi": tb.t212_teblig_adresi, "mernisAdresi": tb.t212_mernis_adresi,
                "kategori": tb.t212_kategori,
            })
            continue

        satir = {
            "birim": birim_ad, "dosyaNo": dosya_no, "borclu": borclu_ad,
            "evrakTarihi": tb.evrak_tarihi, "iadeTarihi": tb.son_islem_tarihi,
            "tebligAdresi": "", "mernisAdresi": "", "kategori": "",
        }
        log(f"— {birim_ad} {dosya_no} ({borclu_ad}) taranıyor…")
        try:
            rec = bs._dosya_id_coz(motor, dosya.birim.birim_id, dosya.yil, dosya.sira_no, log)
            dosya_id = rec.get("dosyaId") if rec else None
            if not dosya_id:
                satir["kategori"] = "hata — dosya çözülemedi"
                _sonuc_ekle(tb, satir)
                continue

            _status, borclular_yanit = motor._post("dosya_borclu_list.ajx", {"dosyaId": dosya_id})
            kisi_kurum_id = _kisi_kurum_id_bul(borclular_yanit, tb.borclu.taraf)
            if not kisi_kurum_id:
                satir["kategori"] = "hata — borçlu UYAP'ta eşleşmedi"
                _sonuc_ekle(tb, satir)
                continue

            evraklar = dosya_core.evrak_listesi_getir(
                dosya_id, log_fn=log, istek_sarici=dosya_core._arka_plan_istek)
            if any(e.get("tur") == bs.TALEP_EVRAK_TUR for e in evraklar):
                satir["kategori"] = "zaten gönderilmiş"
                _sonuc_ekle(tb, satir)
                continue

            kapali = next((e for e in evraklar if e.get("birimEvrakNo") == tb.birim_evrak_no), None)
            if not kapali:
                satir["kategori"] = "elle inceleyin — kapalı tebligat evrakı bulunamadı"
                _sonuc_ekle(tb, satir)
                continue

            _, pdf_bytes = bs._evrak_pdf_indir(
                motor, dosya_id, kapali.get("evrakId"), log_fn=log,
                istek_sarici=dosya_core._arka_plan_istek)
            govde = bs._pdf_govde_metni(pdf_bytes, log)
            teblig_adres = _teblig_adresi_cikar(govde)

            mernis_yanit = _post_yerel_jetonlu(
                "borclu_bilgileri_goruntule_mernis.ajx",
                {"dosyaId": dosya_id, "kisiKurumId": kisi_kurum_id}, log)
            mernis_adres = _mernis_adres_cikar(mernis_yanit)

            satir["tebligAdresi"] = _adres_metni(teblig_adres) or "(ayrıştırılamadı)"
            satir["mernisAdresi"] = _adres_metni(mernis_adres) or "(alınamadı)"

            if not teblig_adres or not mernis_adres:
                satir["kategori"] = "elle inceleyin — adres ayrıştırılamadı"
            elif _adresler_eslesiyor_mu(teblig_adres, mernis_adres):
                satir["kategori"] = "21/2 için uygun"
            else:
                satir["kategori"] = "farklı yöntem gerekli (mernis'e çıkmamış)"

        except Exception as e:
            satir["kategori"] = f"hata — {e}"
            log(f"  ❌ {e}")
        _sonuc_ekle(tb, satir)

    return sonuclar


def son_tarama_sonuclarini_getir(limit=500):
    """Veritabanına daha önce yazılmış 21/2 tarama sonuçlarını (bkz.
    iade_tarama → _sonuc_ekle) `iade_tarama` ile AYNI satır şeklinde döner —
    "Son 21/2 Raporu" artık Panel belleğine değil buraya bakar, program
    yeniden başlasa bile kaybolmaz. En son taranandan en eskiye sıralı."""
    dosya_core._django_hazirla()
    from icra_models.models import TebligatBarkod
    qs = (TebligatBarkod.objects
          .filter(t212_tarandi_zamani__isnull=False)
          .select_related("dosya", "dosya__birim", "borclu", "borclu__taraf")
          .order_by("-t212_tarandi_zamani")[:limit])
    sonuclar = []
    for tb in qs:
        sonuclar.append({
            "birim": tb.dosya.birim.ad, "dosyaNo": tb.dosya.dosya_no,
            "borclu": str(tb.borclu.taraf) if tb.borclu_id else "",
            "evrakTarihi": tb.evrak_tarihi, "iadeTarihi": tb.son_islem_tarihi,
            "tebligAdresi": tb.t212_teblig_adresi, "mernisAdresi": tb.t212_mernis_adresi,
            "kategori": tb.t212_kategori,
        })
    return sonuclar


def dosyalari_gonderildi_isaretle(kalemler):
    """Az önce GERÇEKTEN gönderilmiş (uyap_core.teblig_212.gonder.finalize
    başarıyla dönmüş — bkz. teblig_21_2_gonder.py) dosyalar için yerel
    `TebligatBarkod.yeniden_teblig_talep_edildi` bayrağını True'ya çevirir.

    Kullanıcı bulgusu (2026-08-14): "gönderdim ama Barkod Sorgu tablosunda
    Yeniden Tebliğ Talebi hâlâ Yok görünüyor, veritabanı güncellenmiyor" —
    bu bayrak yalnızca TAM bir "Kapalı Tebligat" barkod taraması (bkz.
    barkod_sorgu.calistir) sırasında yazılıyordu; tek bir 21/2 gönderiminden
    sonra kimse o taramayı yeniden tetiklemiyordu. Burada CANLI SORGU
    ATILMAZ — finalize()'ın kendi başarı yanıtı zaten kesin kanıttır, yalnız
    yerel DB (ve dolayısıyla ekrandaki sütun) tazelenir.

    `kalemler`: [{"birim":..., "dosyaNo":...}, ...] (job sonucundaki
    "gonderildi" kayıtları). Döner: kaç TebligatBarkod satırının
    güncellendiği (int)."""
    if not kalemler:
        return 0
    dosya_core._django_hazirla()
    from icra_models.models import TebligatBarkod
    anahtarlar = {(icra_core.tr_lower(k.get("birim", "")), str(k.get("dosyaNo", "")).strip())
                  for k in kalemler}
    guncellenen = 0
    for tb in (TebligatBarkod.objects
               .filter(yeniden_teblig_talep_edildi=False)
               .select_related("dosya", "dosya__birim")):
        if (icra_core.tr_lower(tb.dosya.birim.ad), tb.dosya.dosya_no) in anahtarlar:
            tb.yeniden_teblig_talep_edildi = True
            tb.save(update_fields=["yeniden_teblig_talep_edildi"])
            guncellenen += 1
    return guncellenen
