# -*- coding: utf-8 -*-
"""
Çoklu Yargı Türü — headless sorgu motoru (GUI + web ortak)
===========================================================
`icra_core.py`'nin kanıtlanmış İcra akışına PARALEL, genel amaçlı bir modül
(bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §4 — mimari karar B: paralel
modül). `icra_core.py` KASTEN dokunulmadan kalır; bu dosya ondan yalnızca
zaten yargı-türünden bağımsız olan yardımcıları İTHAL EDER (aktarım motoru,
taraf varyant çözümü, kayıt ayrıştırma, kolon değeri okuma) — aynı mantığı
ikinci kez yazmaz. Yeni olan tek şey: `birimTuru2`/`birimTuru3`'ün sabit
yerine PARAMETRE olması, Yargı Birimi bootstrap çekimi ve `SenkronKapsami`
tablosuna göre çoklu-kapsam sorgu döngüsü.

Canlı doğrulanan veri (§1, plan dosyası) — TAHMİN YOK:
  Yargı Türü (yargiTuru, sabit enum): 0 Ceza, 1 Hukuk, 2 İcra, 3 Cbs,
    6 İdari Yargı, 11 Satış Memurluğu, 25 Arabuluculuk, 26 Tazminat Komisyonu.
  Yargı Birimi ('yargiBirimleriSorgula_brd.ajx' yanıtı): {"tablo": kod, "kod": ad}
    — 'tablo' = search_phrase_detayli.ajx'teki 'birimTuru2'.
  search_phrase_detayli.ajx: aynı uç nokta, yalnız birimTuru2 (yargı birimi
    kodu) ve birimTuru3 (yargı türü kodu, STR) değişir.
"""

import datetime
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import icra_core as _ic  # noqa: E402  (paralel modül — icra_core DOKUNULMAZ, yalnız içe aktarılır)

# ── icra_core'dan aynen alınan, zaten yargı-türünden bağımsız yardımcılar ────
SorguMotoru = _ic.SorguMotoru
OturumHatasi = _ic.OturumHatasi
tr_lower = _ic.tr_lower
taraf_variantlari = _ic.taraf_variantlari
build_taraf_variants = _ic.build_taraf_variants
parse_records = _ic.parse_records
kolon_degeri = _ic.kolon_degeri
parse_taraf_from_uyap = _ic.parse_taraf_from_uyap
save_taraf = _ic.save_taraf

ENDPOINT = _ic.ENDPOINT                        # "search_phrase_detayli.ajx"
PAGE_SIZE = _ic.PAGE_SIZE
BIRIM_LISTE_ENDPOINT = _ic.BIRIM_LISTE_ENDPOINT  # "avukat_mahkemeleri_sorgula.ajx"
YARGI_BIRIMI_ENDPOINT = "yargiBirimleriSorgula_brd.ajx"

# Cbs (yargı türü 3) — CANLI DOĞRULANDI (Chrome ağ yakalama, 2026-07-14,
# kullanıcı isteğiyle: giriş yapılmış avukat.uyap.gov.tr'de Detaylı Arama'da
# Cbs seçilip İl/Yargı Birimi doldurularak). Diğer türlerden üç noktada
# FARKLI:
#  1. `YARGI_BIRIMI_ENDPOINT` yargiTuru=3 için HER ZAMAN [] döner (plan
#     dosyası §1.1'de zaten şüphelenilmişti — "Yargı Birimi alt-listesi yok,
#     bunun yerine İl alanı"). Gerçek birim (savcılık) listesi İL BAZINDA
#     ayrı bir uç noktadan gelir.
#  2. `illeri_getirJSON.ajx` (boş gövde `{}`) TÜM illeri döner:
#     [{"il": <plaka kodu>, "ad": "İSTANBUL", ...}, ...] (81 il).
#  3. Her il için `CBS_BIRIM_ENDPOINT` {"ilKodu": <il>} o ildeki Cumhuriyet
#     Başsavcılıklarını döner: [{"birimAdi","birimId"}, ...] — `birimId`
#     DOĞRUDAN arama isteğinin `birimTuru2` alanında kullanılır (diğer
#     türlerdeki gibi ayrı bir 'birim TÜRÜ' kodu yok, savcılık kendi başına
#     hem tür hem birim).
#  4. Dosya arama isteği `search_phrase_detayli.ajx` YERİNE
#     `CBS_ARAMA_ENDPOINT`'e gider — payload/yanıt ŞEKLİ AYNI
#     ({"dosyaDurumKod","pageSize","pageNumber","birimId","birimTuru2",
#     "birimTuru3"} → [kayıtlar, toplam]), `build_payload_genel`/
#     `parse_records` DEĞİŞİKLİK GEREKTİRMEDİ — canlı doğrulanan gerçek bir
#     kayıt: {"dosyaId":"...","dosyaNo":"2026/88535","dosyaDurumKod":0,
#     "dosyaTurKod":16,"dosyaTur":"CBS Sorusturma Dosyası",...} (models.py
#     Dosya.Tur'a CBS_SORUSTURMA=16 eklendi).
YARGI_TURU_CBS = 3
CBS_ILLER_ENDPOINT = "illeri_getirJSON.ajx"
CBS_BIRIM_ENDPOINT = "cbs_birim_sorgula.ajx"
CBS_ARAMA_ENDPOINT = "avukat_dosya_sorgula_cbs_brd.ajx"

# Yargı Türü — sabit enum (§1.1, ön yüz dropdown'da arka-uç çağrısı olmadan
# listeleniyor; büyümez, bu yüzden DB'de referans tablosu YOK, burada sabit).
YARGI_TURLERI = [
    (0, "Ceza"),
    (1, "Hukuk"),
    (2, "İcra"),
    (3, "Cbs"),
    (6, "İdari Yargı"),
    (11, "Satış Memurluğu"),
    (25, "Arabuluculuk"),
    (26, "Tazminat Komisyonu Başkanlığı"),
]
YARGI_TURU_ADI = dict(YARGI_TURLERI)
YARGI_TURU_ICRA = 2  # bkz. YARGI_TURLERI — _taraflar_sutunlari önceliklendirmesi yalnız İcra'da
YARGI_TURU_HUKUK = 1  # bkz. YARGI_TURLERI — hukuk_dosyalarim.py'nin sabit kapsamı


def _kendi_vekil_adlari():
    """Ayarlar ekranında girilen, KULLANICININ KENDİ vekil ad(lar)ı —
    virgülle ayrılmış serbest metin (ör. "Utku Alpaslan, Ayşe Yılmaz"),
    UYAP taraf yanıtındaki 'vekil' adıyla eşleştirmek için `tr_lower` ile
    normalize edilmiş küme olarak döner. Ortak ayar dosyasını (uyap_app_config.
    json) DOĞRUDAN `uyap_panel.core.config`'ten okur — `uyap_app` modülünü
    İTHAL ETMEZ (bkz. bellek: server._load_auth() canlı-giriş tuzağı; o modül
    yüklendiğinde yan etkili otomatik bağlantı tetikleyebilir, bu saf ayar
    okuyucusu tetiklemez). Ayar hiç girilmemişse boş küme döner — 'bizim
    taraf' tespiti o zaman sessizce devre dışı kalır (uydurma yok)."""
    try:
        uhg_dir = os.path.normpath(os.path.join(_HERE, "..", "..", "Uyap Haricen Giriş"))
        if uhg_dir not in sys.path:
            sys.path.insert(0, uhg_dir)
        from uyap_panel.core.config import load_config
        ham = (load_config().get("kendi_vekil_adlari", "") or "").strip()
    except Exception:
        return set()
    return {tr_lower(p.strip()) for p in ham.split(",") if p.strip()}


def bu_makine_istemci_mi():
    """Bu makine ofisin UYAP oturumunu TUTAN makine değil, ona bağlanan bir
    İSTEMCİ mi? (`config["auto_connect"] == "receive"`, bkz. `ayarlar.py`
    AUTO_SECENEKLER — "Bağlantıyı Al (İstemci)".) `_kendi_vekil_adlari` ile
    AYNI güvenli desen: ortak config dosyasını `uyap_panel.core.config`'ten
    DOĞRUDAN okur, `uyap_app` modülünü İTHAL ETMEZ (bkz. bellek:
    server._load_auth() canlı-giriş tuzağı) — böylece panel.py'nin
    `_load_auth`'unun ~5 sn'lik arka plan yüklemesini BEKLEMEDEN, açılışın
    en başında (DB/senkron başlatılmadan ÖNCE) güvenle çağrılabilir.

    Kullanıcı bulgusu (2026-07-13): aynı ofisin birden fazla çalışanı kendi
    makinesinde bu programı çalıştırırsa, HER makine kendi gömülü
    veritabanını kurup HER biri ayrıca UYAP'ı tarıyordu — hem gereksiz veri
    tekrarı hem de AYNI paylaşılan UYAP oturumuna (bkz. baglanti.py ofis/
    istemci tüneli) gereksiz yük. Yalnız 'sunucu' (auto_connect="share" veya
    "off" — bkz. dosya_core.py modül üstü Açık Sınır notu) DB/senkron
    çalıştırmalı; 'istemci' (auto_connect="receive") çalıştırmamalı, veriyi
    sunucunun web panelinden görüntülemeli. Config okunamazsa/ayar hiç
    girilmemişse False (bugünkü varsayılan davranış BOZULMAZ)."""
    try:
        uhg_dir = os.path.normpath(os.path.join(_HERE, "..", "..", "Uyap Haricen Giriş"))
        if uhg_dir not in sys.path:
            sys.path.insert(0, uhg_dir)
        from uyap_panel.core.config import load_config
        return (load_config().get("auto_connect", "off") or "off") == "receive"
    except Exception:
        return False


def _django_hazirla():
    """models/ dizinini path'e ekler ve Django'yu kurar (bir kez, önbellekli
    olmadan — çağıran zaten kendi önbelleğini tutuyorsa gerek yok)."""
    mdir = os.path.normpath(os.path.join(_HERE, "..", "..", "models"))
    if mdir not in sys.path:
        sys.path.insert(0, mdir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
    import django
    django.setup()


def _cbs_birimleri_getir(motor, log_fn=None, istek_sarici=None):
    """Cbs için Yargı Birimi (savcılık) listesini İL BAZINDA toplar (bkz.
    CBS_* sabitleri üstündeki canlı doğrulama notu): önce `illeri_getirJSON.
    ajx` ile TÜM illeri, sonra HER il için `CBS_BIRIM_ENDPOINT` ile o ildeki
    savcılıkları çeker. 81 ayrı istek gerektirdiğinden diğer türlerden
    YAVAŞTIR — yalnız bootstrap/Yenile'de çalışır, sonuç `YargiBirimi`'ye
    önbelleklenir. Bir ilin isteği başarısız olursa yalnız o il atlanır, tur
    devam eder. `istek_sarici`: bkz. `yargi_birimleri_getir` docstring'i —
    bireysel/arka-plan öncelik geçidinden GEÇMEDEN atılan istekler UYAP'ın
    eşzamanlı reddiyle çakışabiliyordu (kullanıcı bulgusu, 2026-07-14).
    Döner: list[{"kod","ad"}] (kod=savcılığın birimId'si)."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    iller = _post_eszamanli_korumali(sarici, motor, CBS_ILLER_ENDPOINT, {}, log)
    if not isinstance(iller, list):
        if iller is None:
            log("⚠️ Cbs İl listesi alınamadı (eşzamanlı red).")
        return []
    temiz = []
    for il in iller:
        il_kodu = il.get("il") if isinstance(il, dict) else None
        if il_kodu is None:
            continue
        birimler = _post_eszamanli_korumali(sarici, motor, CBS_BIRIM_ENDPOINT, {"ilKodu": il_kodu}, log)
        if not isinstance(birimler, list):
            continue
        for b in birimler:
            if isinstance(b, dict) and b.get("birimId"):
                temiz.append({"kod": str(b["birimId"]), "ad": str(b.get("birimAdi", "") or "")})
    return temiz


def yargi_birimleri_getir(yargi_turu, log_fn=None, istek_sarici=None):
    """Bir yargı türü için Yargı Birimi (mahkeme türü) listesini UYAP'tan
    çeker (`yargiBirimleriSorgula_brd.ajx`, payload {"yargiTuru": kod}) ve
    `YargiBirimi` tablosuna upsert eder. Döner: list[{"kod","ad"}]. Cbs
    (yargı türü 3) İSTİSNA: bu uç nokta o tür için HER ZAMAN [] döndüğünden
    (canlı doğrulandı) `_cbs_birimleri_getir` ile İl-bazlı toplanır.

    `istek_sarici`: hangi öncelik geçidinden (`_bireysel_istek`/
    `_arka_plan_istek`) geçileceği — VARSAYILAN `_bireysel_istek`. Bu modüldeki
    TÜM UYAP çağrıları aynı `_istek_kilidi`'ni paylaşmalı; bu fonksiyon
    ESKİDEN çıplak `motor._post` çağırıyordu (gate'ten TAMAMEN muaf), bu da
    arka plan senkron turunun (`_gorevleri_genislet`) kullanıcının o anki
    'Dosya Görüntüle' tıklamasıyla AYNI ANDA UYAP'a gitmesine, dolayısıyla
    programın YENİ AÇILDIĞI anda bile ilk tıklamada 'eşzamanlı' reddi
    almasına yol açıyordu (kullanıcı bulgusu, 2026-07-14 — log boşken bile
    oluyordu). Ağ hatasında ya da DB erişilemezse [] döner (çağıran eldeki DB
    önbelleğini `yargi_birimleri_db_den_yukle` ile ayrıca okuyabilir)."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    motor = SorguMotoru(log)
    if yargi_turu == YARGI_TURU_CBS:
        temiz = _cbs_birimleri_getir(motor, log_fn, sarici)
    else:
        veri = _post_eszamanli_korumali(sarici, motor, YARGI_BIRIMI_ENDPOINT,
                                         {"yargiTuru": str(yargi_turu)}, log)
        liste = veri if isinstance(veri, list) else []
        temiz = [{"kod": str(b.get("tablo", "") or ""), "ad": str(b.get("kod", "") or "")}
                 for b in liste if isinstance(b, dict) and b.get("tablo")]
    if temiz:
        try:
            _django_hazirla()
            from icra_models.models import YargiBirimi
            for b in temiz:
                YargiBirimi.objects.update_or_create(
                    yargi_turu=yargi_turu, kod=b["kod"], defaults={"ad": b["ad"]})
        except Exception as e:
            (log_fn or (lambda *a, **k: None))(f"⚠️ YargiBirimi kaydı atlandı: {e}")
    return temiz


def yargi_birimleri_bootstrap(log_fn=None):
    """İlk bağlantıda: TÜM yargı türleri için Yargı Birimi listesini çeker.
    Küçük, seyrek değişen veri — periyodik (ör. günde bir) yeniden çağrılması
    yeterlidir. Döner: {yargi_turu: [{"kod","ad"}, ...]}."""
    log = log_fn or (lambda *a, **k: None)
    sonuc = {}
    for kod, ad in YARGI_TURLERI:
        log(f"… Yargı Birimi listesi çekiliyor: {ad}")
        sonuc[kod] = yargi_birimleri_getir(kod, log_fn)
    return sonuc


def yargi_birimleri_db_den_yukle(yargi_turu=None):
    """`YargiBirimi` tablosundan (önceden `yargi_birimleri_getir`/`_bootstrap`
    ile doldurulmuş) kayıtları AĞA GİTMEDEN okur — ayar ekranının seçenek
    listesini hızlı açması için. yargi_turu None ise tüm türler. DB
    erişilemezse [] döner. Döner: list[{"yargi_turu","kod","ad"}]."""
    try:
        _django_hazirla()
        from icra_models.models import YargiBirimi
        qs = YargiBirimi.objects.all()
        if yargi_turu is not None:
            qs = qs.filter(yargi_turu=yargi_turu)
        return [{"yargi_turu": b.yargi_turu, "kod": b.kod, "ad": b.ad} for b in qs]
    except Exception:
        return []


def yargi_birimleri_getir_veya_db(yargi_turu, log_fn=None):
    """`yargi_birimleri_db_den_yukle`'nin sonucu boşsa (o yargı türü için DB
    önbelleği — Senkron Kapsamı'nda '↻ Listeyi UYAP'tan Getir/Yenile' hiç
    tıklanmamışsa — hiç doldurulmamışsa) canlı UYAP'tan çeker
    (`yargi_birimleri_getir`, DB'ye de yazar). "Dosyalarım (Tümü)" ekranının
    Yargı Birimi filtresi, YALNIZ önceden yenilenmiş türler için değil, HER
    yargı türü seçiminde dolu gelsin diye (bkz. kullanıcı bulgusu: Hukuk
    seçilince Yargı Birimi listesi boş geliyordu). Döner: list[{"kod","ad"}]."""
    birimler = yargi_birimleri_db_den_yukle(yargi_turu)
    if birimler:
        return birimler
    try:
        return yargi_birimleri_getir(yargi_turu, log_fn)
    except Exception:
        return []


def senkron_kapsami_durumu_getir():
    """Ayar ekranının mevcut işaretli durumunu göstermesi için TÜM
    `SenkronKapsami` kayıtlarını (aktif/pasif fark etmeksizin) döner.
    Döner: list[{"yargi_turu","yargi_birimi_kod","aktif"}]."""
    _django_hazirla()
    from icra_models.models import SenkronKapsami
    return [{"yargi_turu": s.yargi_turu, "yargi_birimi_kod": s.yargi_birimi_kod, "aktif": s.aktif}
            for s in SenkronKapsami.objects.all()]


def senkron_kapsami_kaydet(secimler):
    """Ayar ekranından gelen TAM seçim listesini kaydeder (replace-all
    semantik): `secimler` = list[(yargi_turu:int, yargi_birimi_kod:str)],
    burada yargi_birimi_kod="" o yargı türünün TAMAMI demektir (§2.4, kullanıcı
    onayı 2026-07-10 — 'tüm tür' seçeneği de var). Listede OLMAYAN var olan
    kayıtlar SİLİNMEZ, yalnızca aktif=False yapılır (geçmiş/log amaçlı iz
    korunur); listede olanlar upsert edilip aktif=True yapılır."""
    _django_hazirla()
    from icra_models.models import SenkronKapsami
    from django.db import transaction as _tx
    istenen = {(int(t), (k or "")) for t, k in secimler}
    with _tx.atomic():
        for s in SenkronKapsami.objects.all():
            aktif_olmali = (s.yargi_turu, s.yargi_birimi_kod) in istenen
            if s.aktif != aktif_olmali:
                s.aktif = aktif_olmali
                s.save(update_fields=["aktif"])
        for (t, k) in istenen:
            SenkronKapsami.objects.update_or_create(
                yargi_turu=t, yargi_birimi_kod=k, defaults={"aktif": True})


def birim_listesi_getir_genel(yargi_turu, yargi_birimi_kod, log_fn=None, istek_sarici=None):
    """`icra_core.birim_listesi_getir`'in genellenmiş hâli — sabit
    yargiTuru=2/yargiBirimi=1101 yerine parametre alır. Önbelleksiz (her
    çağrıda UYAP'a gider); çağıran isterse kendi önbelleğini tutar.
    `istek_sarici`: bkz. `yargi_birimleri_getir` docstring'i — aynı gerekçeyle
    (2026-07-14) artık öncelik geçidinden (varsayılan `_bireysel_istek`) geçer.
    Döner: list[{"birimAdi","birimId"}]."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    payload = {"yargiTuru": str(yargi_turu), "yargiBirimi": yargi_birimi_kod, "dosyaKapaliMi": False}
    veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor, BIRIM_LISTE_ENDPOINT, payload, log)
    liste = veri if isinstance(veri, list) else []
    return [{"birimAdi": b.get("birimAdi", "") or "", "birimId": str(b.get("birimId", "") or "")}
            for b in liste if isinstance(b, dict) and b.get("birimId")]


def build_payload_genel(values, yargi_turu, yargi_birimi_kod, durum_kod=0, taraf_variant=None, birim_id=None):
    """`icra_core.build_payload`'ın genellenmiş hâli: `birimTuru2`/`birimTuru3`
    sabit değil, verilen yargı birimi/türüne göre kurulur. `values` aynı
    `icra_core.FIELDS` sözleşmesini kullanır (dosyaYil, dosyaNo, tarih
    aralığı); `birim_id` doğrudan verilirse birim adı çözümü atlanır."""
    payload = {
        "dosyaDurumKod": durum_kod,
        "pageSize": PAGE_SIZE,
        "pageNumber": 1,
        "birimTuru2": yargi_birimi_kod,
        "birimTuru3": str(yargi_turu),
    }

    yil_val = (values.get("dosyaYil") or "").strip()
    if yil_val:
        try:
            payload["dosyaYil"] = int(yil_val)
        except ValueError:
            pass

    no_val = (values.get("dosyaNo") or "").strip()
    if no_val:
        try:
            if "/" in no_val:
                _, sira = no_val.split("/", 1)
                payload["dosyaSira"] = int(sira)
            else:
                payload["dosyaSira"] = int(no_val)
        except ValueError:
            pass

    if birim_id:
        payload["birimId"] = birim_id
    elif (values.get("birimAdi") or "").strip().isdigit():
        payload["birimId"] = (values.get("birimAdi") or "").strip()

    start_val = (values.get("dosyaAcilisTarihiStart") or "").strip()
    if start_val:
        payload["dosyaAcilisTarihiStart"] = start_val
    end_val = (values.get("dosyaAcilisTarihiEnd") or "").strip()
    if end_val:
        payload["dosyaAcilisTarihiEnd"] = end_val

    if taraf_variant:
        payload.update(taraf_variant)

    return payload


def senkron_kapsamlari_getir():
    """Aktif `SenkronKapsami` satırlarını HAM hâliyle döndürür: list[(yargi_turu,
    yargi_birimi_kod)]. yargi_birimi_kod boş STRING olabilir ("tüm tür" seçimi,
    §2.4) — bu fonksiyon genişletme YAPMAZ, ham kaydı döner. Genişletme
    (`YargiBirimi`'den o türün tüm kodlarını türetme) `DosyaSorgu.calistir`'in
    işidir (bkz. altındaki `_gorevleri_genislet`)."""
    _django_hazirla()
    from icra_models.models import SenkronKapsami
    return [(s.yargi_turu, s.yargi_birimi_kod) for s in SenkronKapsami.objects.filter(aktif=True)]


_DURUM_KODLARI_TUMU = (0, 1)  # Açık, Kapalı — bkz. DosyaSorgu.calistir docstring


# ── Bireysel-öncelikli istek geçidi (kullanıcı bulgusu, 2026-07-13: "programımız
# açıldığında güncelleme yapıyor, ben Dosya Görüntüle dediğimde eşzamanlı hatası
# alıyoruz") ──────────────────────────────────────────────────────────────────
# UYAP tek oturumda aynı anda İKİNCİ bir isteği REDDEDİYOR (bkz. uyap_core/
# _runtime.py'deki AYNI desen — "Eş zamanlı olarak birden fazla sorgulama
# yapamazsınız!"). Ofis tarafındaki proxy (127.0.0.1:8800) kendi süreci için bunu
# zaten sıraya alıyor, AMA Panel'in KENDİ arka plan zamanlayıcısı
# (`senkron_zamanlayici_baslat`) ile kullanıcının o anki tıklaması (Dosya
# Görüntüle/Yenile) AYNI süreçte iki ayrı thread'den bağımsız HTTP istekleri
# başlatıyordu — proxy'nin sırası doğru olsa bile, kaynakta (Panel) hangisinin
# ÖNCE gideceğini belirleyen bir şey yoktu. Çözüm: `_runtime.py`'deki
# interactive_write()/batch_write() ADİL DAĞITIM desenini (asyncio yerine
# threading ile) burada tekrarlıyoruz — kullanıcının bireysel isteği YÜKSEK
# öncelikli, arka plan senkronu her tek UYAP isteğinden ÖNCE bireysel bekleyen
# var mı diye kontrol edip varsa yol verir.
_istek_kilidi = threading.Lock()
_bekleyen_bireysel = 0
_bireysel_sayac_kilidi = threading.Lock()
_bireysel_yok = threading.Event()
_bireysel_yok.set()  # başlangıçta bekleyen bireysel istek yok

# ── İstekler arası taban aralık (kullanıcı bulgusu, 2026-07-13: `_istek_kilidi`
# TÜM UYAP isteklerini bu süreç içinde katı biçimde sıraya alıyor — iki istek
# ASLA aynı anda uçmuyor — ama "eşzamanlı" reddi YİNE de çıkabiliyordu, ör.
# `dosya_detay_goster_ve_kaydet`'in ardışık iki çağrısı: dosya_ayrinti_getir
# bitip yanıt döner dönmez (aradaki tek şey bir DB kaydı, milisaniyeler) hemen
# dosya_taraf_getir başlıyordu. Demek ki UYAP'ın REDDİ yalnız "bizim tarafımızda
# çakışma" değil — UYAP'ın kendi oturum kilidini HTTP yanıtını gönderdikten
# SONRA bırakması da biraz zaman alıyor olmalı (bkz. _post_eszamanli_korumali
# üzerindeki 'birkaç saniye sonra kendiliğinden geçer' notu — bu zaten bilinen
# bir gecikmeydi, ama önceden yalnız BAŞARISIZLIK SONRASI bekleniyordu, önceden
# değil). Çözüm: kilidi tutarken bir önceki isteğin BİTİŞİNDEN bu yana en az
# `_MIN_ISTEK_ARALIGI` saniye geçmediyse yenisini başlatmadan önce bekle —
# böylece ilk denemede reddedilme ihtimali baştan azalır; `_post_eszamanli_
# korumali`'nin tekrar-deneme mekanizması yine de bir GÜVENLİK AĞI olarak kalır
# (tamamen dışarıdan — başka ofis/sekme — gelen çakışmalar için).
# NOT: bu bekleme yalnız `_bireysel_istek` (kullanıcının tıklaması) tarafında
# UYGULANIR — `_arka_plan_istek` (toplu tur) BEKLEMEZ, yalnız bitiş zamanını
# KAYDEDER. Toplu tur binlerce ardışık istek atabiliyor (bkz. taraf_da_cek'in
# 2026-07-12 hız düzeltmesi, hemen yukarıda) — her adıma sabit 1sn eklemek o
# düzeltmeyi geri alırdı. Tespit edilen somut çakışma (dosya_ayrinti_getir →
# dosya_taraf_getir) HER ZAMAN _bireysel_istek üzerinden gider, bu yüzden
# bekleme yalnız orada yeterli.
_MIN_ISTEK_ARALIGI = 1.0
_son_istek_bitis = 0.0
_son_istek_kilidi = threading.Lock()


def _aralik_bekle():
    with _son_istek_kilidi:
        bekle = _son_istek_bitis + _MIN_ISTEK_ARALIGI - time.monotonic()
    if bekle > 0:
        time.sleep(bekle)


def _aralik_kaydet():
    global _son_istek_bitis
    with _son_istek_kilidi:
        _son_istek_bitis = time.monotonic()


@contextmanager
def _bireysel_istek():
    """Kullanıcının o anki tıklamasının (Dosya Görüntüle, Yenile) UYAP isteği
    için yüksek öncelikli geçit — arka plan senkronu buna yol verir."""
    global _bekleyen_bireysel
    with _bireysel_sayac_kilidi:
        _bekleyen_bireysel += 1
        _bireysel_yok.clear()
    try:
        with _istek_kilidi:
            _aralik_bekle()
            try:
                yield
            finally:
                _aralik_kaydet()
    finally:
        with _bireysel_sayac_kilidi:
            _bekleyen_bireysel -= 1
            if _bekleyen_bireysel <= 0:
                _bekleyen_bireysel = 0
                _bireysel_yok.set()


@contextmanager
def _arka_plan_istek():
    """Otomatik senkron turunun HER TEK UYAP isteği için düşük öncelikli geçit —
    kilidi almadan önce bekleyen bireysel istek kalmayana kadar bekler; böylece
    kullanıcının tıklaması sayfa/birim sınırında (saniyeler içinde) araya
    girebilir, saatlerce süren tam turun bitmesini beklemez. `_aralik_bekle()`
    BİLEREK çağrılmaz (bkz. `_MIN_ISTEK_ARALIGI` yorumu) — yalnız bitiş zamanı
    kaydedilir ki ardından gelecek bir _bireysel_istek doğru referans alsın."""
    while _bekleyen_bireysel > 0:
        _bireysel_yok.wait()
    with _istek_kilidi:
        try:
            yield
        finally:
            _aralik_kaydet()


# ── UYAP'ın "eşzamanlı" reddinin GERÇEK bir hata olarak tanınması ────────────
# (kullanıcı bulgusu, 2026-07-13: yukarıdaki öncelik geçidi YARIŞI ortadan
# kaldırır AMA UYAP'a bizim dışımızdan — ör. ayrı bir tarayıcı sekmesi/başka bir
# ofis bilgisayarı — aynı anda başka bir istek giderse UYAP yine de "Eş zamanlı
# olarak birden fazla sorgulama yapamazsınız!" ile reddedebilir; bu durumda
# `SorguMotoru._post` (bkz. Sorgu/SGK Sorgu/sgk_sorgu_gui.py — orijinal,
# değiştirilmez) BİR İSTİSNA FIRLATMAZ, red metnini normal 200 yanıt gibi
# döner. Bu metnin ham .ajx gövdesinde TAM OLARAK hangi şekilde geleceği (düz
# metin mi, HTML mi, yoksa UYAP'ın kendi JSON hata zarfı içinde mi) CANLI
# DOĞRULANMADI — yalnız bir TARAYICI SAYFASI görünümü yakalanabildi (bkz. Panel/
# Eşzamanlı hata metni.txt). Bu YÜZDEN biçimden bağımsız, imza metnine dayalı
# bir tarama yapılır: veri dict/list/str hangi şekilde gelirse gelsin, İÇİNDE
# bu imza varsa bu bir HATADIR, gerçek dosya verisi DEĞİLDİR — asla DB'ye
# yazılmaz, aksi halde gerçek/eski veri boş/yanlış değerle EZİLEBİLİR.
_ESZAMANLI_IMZA = "birden fazla sorgulama yapamaz"


def _yanit_hata_zarfi_mi(veri):
    """`veri` UYAP'ın GENEL hata zarfı mı (ör. {'errorCode': 'PRTL_GNL_10001-4',
    'error': 'Doğrulama hatası...'})? — kullanıcı bulgusu, 2026-07-14: bu zarf
    dict OLDUĞU için `dosya_ayrinti_getir` onu 'veri geldi' sanıp doğrudan
    `dosya_ayrinti_kaydet`'e veriyordu; zarftaki alanlar (errorCode/error) gerçek
    dosya alanlarıyla (takibinTuru vb.) eşleşmediğinden `update_or_create`
    HEPSİNİ boş/sıfır değerle YAZIYORDU — DAHA ÖNCE DOĞRU KAYDEDİLMİŞ takibin
    türü/şekli/yolu bu şekilde SESSİZCE SİLİNMİŞ oluyordu. Gerçek başarılı
    yanıtlarda (canlı doğrulanan örnekler) 'errorCode' anahtarı HİÇ görülmedi —
    bu yüzden onun varlığı güvenilir bir hata imzası sayılır."""
    return isinstance(veri, dict) and "errorCode" in veri


def _yanit_eszamanli_hata_mi(veri, derinlik=0):
    """`veri` (motor._post'un döndürdüğü ham/parse edilmiş yanıt) içinde UYAP'ın
    eşzamanlı-red imzası var mı? bkz. modül düzeyi açıklama (_ESZAMANLI_IMZA)."""
    if derinlik > 4:
        return False
    if isinstance(veri, str):
        return _ESZAMANLI_IMZA in veri.lower()
    if isinstance(veri, dict):
        return any(_yanit_eszamanli_hata_mi(v, derinlik + 1) for v in veri.values())
    if isinstance(veri, (list, tuple)):
        return any(_yanit_eszamanli_hata_mi(v, derinlik + 1) for v in veri)
    return False


def _post_eszamanli_korumali(istek_sarici, motor, endpoint, payload, log_fn,
                              tekrar=4, bekleme_saniye=3, **post_kwargs):
    """`istek_sarici()` geçidi ALTINDA `motor._post`'u çağırır; yanıt UYAP'ın
    "eşzamanlı" reddiyse (bkz. _yanit_eszamanli_hata_mi) bunu GERÇEK veri
    SANMAZ — artan aralıklarla (3sn, 6sn, 9sn, 12sn…) tekrar dener (bu bir
    YARIŞ DURUMUDUR, genelde birkaç saniye sonra kendiliğinden geçer), tüm
    denemeler tükenirse None döner. Çağıran None'ı 'veri yok' ile AYNI ele
    almalı (boş/eski kayıt DB'ye asla bu redle EZİLMEMELİ).
    `tekrar=4` (kullanıcı bulgusu, 2026-07-13: 'Yenile' sırasında UYAP önce
    502/'ulaşılamadı' verip ardından ~13sn boyunca 'eşzamanlı' reddetmeye
    devam etti — eski tekrar=2/sabit 3sn toplam bekleme bu tür uzun süren
    UYAP-taraflı tıkanmayı aşamıyordu; artan bekleme ile toplam tolerans
    3+6+9+12=30sn'ye çıkarıldı)."""
    log = log_fn or (lambda *a, **k: None)
    for deneme in range(tekrar + 1):
        with istek_sarici():
            _status, veri = motor._post(endpoint, payload, **post_kwargs)
        if not _yanit_eszamanli_hata_mi(veri):
            return veri
        if deneme < tekrar:
            bekle = bekleme_saniye * (deneme + 1)
            log(f"⚠️ UYAP 'eşzamanlı sorgulama' isteği reddetti ({endpoint}) — "
                f"bu GERÇEK veri değil, {bekle}sn sonra tekrar denenecek "
                f"({deneme + 1}/{tekrar}).")
            time.sleep(bekle)
        else:
            log(f"⚠️ UYAP 'eşzamanlı sorgulama' isteği reddetti ({endpoint}) — "
                f"{tekrar} deneme sonunda hâlâ reddediliyor, bu tur atlanıyor "
                "(DB'ye YAZILMAYACAK, eski/doğru veri korunacak).")
    return None


# ── Evrak (belge) listesi + içerik indirme ───────────────────────────────────
# bkz. docs/BELGE_ONBELLEK_PLANI.md §0.2 — canlı yakalamada `evrakId`/`ggEvrakId`
# VE bir evrak kaydının KENDİ İÇİNDEKİ 'dosyaId' alanı OTURUMLUK/rotating çıktı
# (aynı belge için ardışık iki istekte üçü de farklıydı). Kalıcı anahtar
# `birimEvrakNo`. `view_document_brd.uyap`'a giden dosyaId İSE arama adımından
# (search_phrase_detayli.ajx) ÇÖZÜLEN dosyaId olmalı — evrak kaydının kendi
# 'dosyaId' alanı DEĞİL; 2026-07-23 canlı yakalamada AYNI yanıt içinde bile
# ikisi FARKLI çıktı (ör. "...RmReb5gihS4K9jNV6m19j" vs "...8a6DCpZ7Yi7x16v5v+FOlT").
# Bu ayrım gözden kaçırılırsa indirme oturum/yetki hatasıyla başarısız olur.
EVRAK_LISTESI_ENDPOINT = "list_dosya_evraklar.ajx"
EVRAK_LISTESI_TOPLAM_ENDPOINT = "listDosyaEvraklarPageTotal.ajx"
DOC_VIEWER_HAZIRLIK_ENDPOINT = "getDocViewerParameters.ajx"
EVRAK_ICERIK_YOLU = "view_document_brd.uyap"


def evrak_listesi_getir(dosya_id, log_fn=None, istek_sarici=None):
    """`dosya_id` (arama adımından ÇÖZÜLEN dosyaId) için TÜM sayfalardaki evrak
    kayıtlarını (dict: evrakId, birimEvrakNo, tur, aciklama, onaylandigiTarih,
    ...) düz bir listede döner. Bir sayfa UYAP hata verirse o sayfa atlanır."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    motor = SorguMotoru(log)

    toplam = _post_eszamanli_korumali(sarici, motor, EVRAK_LISTESI_TOPLAM_ENDPOINT,
                                       {"dosyaId": dosya_id, "pageNumber": 1}, log)
    try:
        toplam_sayfa = max(int(toplam), 1)
    except (TypeError, ValueError):
        toplam_sayfa = 1

    tum = []
    for sayfa in range(1, toplam_sayfa + 1):
        veri = _post_eszamanli_korumali(sarici, motor, EVRAK_LISTESI_ENDPOINT,
                                         {"dosyaId": dosya_id, "pageNumber": sayfa}, log)
        gruplar = veri.get("tumEvraklar") if isinstance(veri, dict) else None
        if not isinstance(gruplar, dict):
            continue
        for grup in gruplar.values():
            if isinstance(grup, list):
                tum.extend(e for e in grup if isinstance(e, dict))
    return tum


def evrak_html_indir(dosya_id, evrak_id, log_fn=None, istek_sarici=None, timeout=90):
    """Bir evrakın ham HTML içeriğini `view_document_brd.uyap`'tan GET ile indirir.
    `dosya_id` ARAMA ADIMINDAN ÇÖZÜLEN dosyaId olmalı (bkz. modül başlığındaki
    not — evrak kaydının kendi 'dosyaId' alanı DEĞİL). UYAP'ın gerçek akışında
    bu isteğin hemen öncesinde `getDocViewerParameters.ajx` boş gövdeyle
    çağrılıyor (canlı yakalamada görüldü); aynı sıra burada da izlenir."""
    import urllib.parse
    import urllib.request

    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    motor = SorguMotoru(log)

    _post_eszamanli_korumali(sarici, motor, DOC_VIEWER_HAZIRLIK_ENDPOINT, {}, log)

    qs = urllib.parse.urlencode({"evrakId": evrak_id, "dosyaId": dosya_id})
    url = f"{motor.base}/{EVRAK_ICERIK_YOLU}?{qs}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "*/*")
    req.add_header("referer", f"{motor.base}/dosya-sorgulama")

    with sarici():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            data = r.read()
    if status >= 400:
        raise RuntimeError(f"Evrak indirilemedi (HTTP {status})")
    return data.decode("utf-8", "replace")


class DosyaSorgu:
    """Çoklu yargı türü/birimi dosya sorgusu (headless). `SenkronKapsami`'de
    işaretli her (yargı türü, yargı birimi) kombinasyonu için
    `search_phrase_detayli.ajx`'i kendi birimTuru2/3 değerleriyle çağırır.
    Aktarım ve taraf/kayıt işleme mantığı `icra_core`'dan İTHAL EDİLİR."""

    def __init__(self, log_fn=None, istek_sarici=None):
        self.log_fn = log_fn or (lambda m: None)
        # Varsayılan YÜKSEK öncelik (_bireysel_istek): mevcut tüm çağıranlar
        # (Yenile düğmesi, dosyalarim_yenile) davranışını DEĞİŞTİRMEDEN otomatik
        # olarak arka plan senkronuna karşı öncelik kazanır. Yalnız arka plan
        # zamanlayıcısı (`senkron_zamanlayici_baslat`) düşük öncelikli
        # `_arka_plan_istek`'i AÇIKÇA verir — bkz. _bireysel_istek/_arka_plan_istek
        # docstring'i.
        self._istek_sarici = istek_sarici or _bireysel_istek

    def _bir_kapsami_ara(self, motor, yargi_turu, yargi_birimi_kod, values, durum_kod):
        """Tek bir (yargi_turu, yargi_birimi_kod) için sayfalanmış arama.
        Döner: list[dict] (ham UYAP kayıtları, dosyaId ile bu kapsam içinde
        tekilleştirilmiş)."""
        tum = []
        gorulen = set()
        endpoint = CBS_ARAMA_ENDPOINT if yargi_turu == YARGI_TURU_CBS else ENDPOINT
        variantlar = taraf_variantlari(values) or [None]
        for variant in variantlar:
            sayfa = 1
            while True:
                payload = build_payload_genel(values, yargi_turu, yargi_birimi_kod, durum_kod, variant)
                payload["pageNumber"] = sayfa
                veri = _post_eszamanli_korumali(self._istek_sarici, motor, endpoint, payload, self.log_fn)
                kayitlar = parse_records(veri)
                for rec in kayitlar:
                    did = rec.get("dosyaId")
                    if did and did in gorulen:
                        continue
                    if did:
                        gorulen.add(did)
                    tum.append(rec)
                if len(kayitlar) < PAGE_SIZE:
                    break
                sayfa += 1
                if sayfa > 200:
                    self.log_fn(f"⚠️ ({YARGI_TURU_ADI.get(yargi_turu, yargi_turu)}/{yargi_birimi_kod}) "
                                "sayfa sınırına (200) ulaşıldı; bazı kayıtlar alınmamış olabilir.")
                    break
        return tum

    def _gorevleri_genislet(self, kapsamlar):
        """Ham `SenkronKapsami` satırlarını (yargi_turu, yargi_birimi_kod) iş
        listesine çevirir. yargi_birimi_kod="" ("tüm tür" seçimi, §2.4) ise
        `YargiBirimi`'den o türün TÜM kayıtlı kodları genişletilir (önce DB
        önbelleği, boşsa canlı `yargi_birimleri_getir` denenir). Genişletilemezse
        (Yargı Birimi listesi de boşsa) o kapsam log ile bildirilip atlanır.
        Döner: list[(yargi_turu, yargi_birimi_kod, yargi_turu_adi)]."""
        gorevler = []
        for yargi_turu, yargi_birimi_kod in kapsamlar:
            ad = YARGI_TURU_ADI.get(yargi_turu, str(yargi_turu))
            if yargi_birimi_kod:
                gorevler.append((yargi_turu, yargi_birimi_kod, ad))
                continue
            birimler = yargi_birimleri_db_den_yukle(yargi_turu)
            if not birimler:
                self.log_fn(f"… '{ad}' (tüm tür) için Yargı Birimi listesi DB'de yok, canlı çekiliyor…")
                try:
                    birimler = yargi_birimleri_getir(yargi_turu, self.log_fn, self._istek_sarici)
                except Exception as e:
                    self.log_fn(f"⚠️ '{ad}' (tüm tür) genişletilemedi: {e}")
                    birimler = []
            if not birimler:
                self.log_fn(f"⚠️ '{ad}' (tüm tür) genişletilemedi — Yargı Birimi listesi boş/erişilemez, bu kapsam atlandı.")
                continue
            for b in birimler:
                if b.get("kod"):
                    gorevler.append((yargi_turu, b["kod"], f"{ad}/{b.get('ad', b['kod'])}"))
        return gorevler

    def calistir(self, values=None, durum_kod=None, tum_turler=False, tek_kapsam=None, taraf_da_cek=False,
                 kontrol=None):
        """`SenkronKapsami`'deki her aktif kapsamı sırayla arar, kaydeder.
        Döner: (kayit_sayisi, kapsam_sonuclari:list[dict]). `values` boşsa
        (varsayılan) kapsam-genişleticiler dışında ekstra filtre uygulanmaz.
        `tek_kapsam=(yargi_turu, yargi_birimi_kod)` verilirse SADECE o (tür,
        birim) taranır (`yargi_birimi_kod=""` ise o türün TÜM birimleri —
        bkz. `_gorevleri_genislet`) — 'Dosyalarım (Tümü)' ekranının EKONOMİK
        Yenile'si için (kullanıcı bulgusu: eskiden Yenile seçili filtreden
        bağımsız HER ZAMAN tüm türleri/birimleri tarıyordu, pahalıydı).
        `tum_turler=True` ise `SenkronKapsami`'ye HİÇ BAKMADAN her yargı
        türünü ('Tümü' birim genişletmesiyle) tarar — 'Tüm Dosyaları
        Güncelle' ayrı düğmesi için. `tek_kapsam`, `tum_turler`'den ÖNCELİKLİDİR.
        `taraf_da_cek=True` ise her kayıt için AYRICA taraf bilgisi de çekilip
        kaydedilir (kapsamdaki dosya sayısı kadar EK canlı UYAP isteği —
        kullanıcı bulgusu, 2026-07-12: 'Dosyalarım (Tümü)' ekranının Taraf Adı
        filtresi anlamlı olsun diye eklendi). VARSAYILAN False: arka plan
        zamanlayıcısı (`senkron_zamanlayici_baslat`) bu parametreyi VERMEZ —
        30 dakikada bir sessizce çalışan otomatik tur eskisi gibi yalnız ucuz
        kapak künyesini çeker; taraf çekimi yalnız kullanıcının doğrudan
        tetiklediği 'Yenile'/'Tüm Dosyaları Güncelle'de (bkz. `dosyalarim_yenile`)
        açılır — sessiz arka plan turunun maliyetini artırmamak için.

        `durum_kod`: VARSAYILAN `None` — her kapsam HEM Açık (0) HEM Kapalı
        (1) `dosyaDurumKod`'uyla ayrı ayrı taranır (bkz. `_DURUM_KODLARI_TUMU`).
        Açık/Kapalı ikili kodu `icra_core.DURUMLAR`'dan CANLI DOĞRULANMIŞTIR
        (yalnız İcra için); `search_phrase_detayli.ajx` TÜM yargı türlerinde
        AYNI uç nokta/payload şeklini paylaştığından (§1.4 plan dosyası) aynı
        ikili kod diğer türlere de uygulanır — bu genelleme türe özel CANLI
        doğrulanmadı (TAHMİN olarak işaretlenir). Eskiden BU PARAMETRE HİÇBİR
        çağıran tarafından verilmiyordu ve varsayılan `0` (yalnız Açık) idi:
        yani 'Dosyalarım (Tümü)' ekranı hiçbir yargı türünde KAPALI dosyayı
        HİÇ çekmiyordu (kullanıcı bulgusu, 2026-07-14: özellikle Arabuluculuk'ta
        fark edildi, ama kapsam TÜM türleri etkiliyordu). `durum_kod` açıkça
        bir tamsayı verilirse (ör. eski/özel bir çağrı) eskisi gibi TEK durum
        taranır."""
        values = values or {}
        durum_kodlari = list(_DURUM_KODLARI_TUMU) if durum_kod is None else [durum_kod]
        motor = SorguMotoru(self.log_fn)
        if tek_kapsam is not None:
            kapsamlar = [tek_kapsam]
        elif tum_turler:
            kapsamlar = [(kod, "") for kod, _ad in YARGI_TURLERI]
        else:
            try:
                kapsamlar = senkron_kapsamlari_getir()
            except Exception as e:
                raise RuntimeError(f"SenkronKapsami okunamadı (DB erişilemez olabilir): {e}")
            if not kapsamlar:
                self.log_fn("SenkronKapsami boş — hiçbir yargı türü/birimi seçilmemiş, senkron yapılacak bir şey yok.")
                return 0, []

        gorevler = self._gorevleri_genislet(kapsamlar)
        if not gorevler:
            self.log_fn("Genişletme sonrası sorgulanacak hiçbir (yargı türü, yargı birimi) kalmadı.")
            return 0, []

        try:
            _django_hazirla()
            from icra_models.ingest import dosya_kunyesi_kaydet
            db_available = True
        except Exception as e:
            self.log_fn(f"⚠️ Yerel veritabanı aktif değil: {e}")
            db_available = False

        toplam = 0
        taraf_cekilen = 0
        sonuclar = []
        for yargi_turu, yargi_birimi_kod, ad in gorevler:
            if kontrol:
                kontrol.tur_bitti()
                if not kontrol.nokta():
                    self.log_fn("⏹ Durduruldu.")
                    break
            kayitlar = []
            gorulen_did = set()
            basarisiz = False
            for dk in durum_kodlari:
                durum_adi = {0: "Açık", 1: "Kapalı"}.get(dk, str(dk))
                self.log_fn(f"… sorgulanıyor: {ad} / {yargi_birimi_kod} ({durum_adi})")
                try:
                    parca = self._bir_kapsami_ara(motor, yargi_turu, yargi_birimi_kod, values, dk)
                except OturumHatasi:
                    raise
                except Exception as e:
                    self.log_fn(f"⚠️ {ad}/{yargi_birimi_kod} ({durum_adi}) sorgusu başarısız: {e}")
                    basarisiz = True
                    continue
                for rec in parca:
                    did = rec.get("dosyaId")
                    if did and did in gorulen_did:
                        continue
                    if did:
                        gorulen_did.add(did)
                    kayitlar.append(rec)
            if basarisiz and not kayitlar:
                continue

            kaydedilen = 0
            if db_available:
                for rec in kayitlar:
                    if kontrol:
                        kontrol.tur_bitti()
                        if not kontrol.nokta():
                            self.log_fn("⏹ Durduruldu.")
                            break
                    dosya_id = rec.get("dosyaId")
                    if not dosya_id:
                        continue
                    try:
                        dosya, _created = dosya_kunyesi_kaydet(rec, yargi_turu=yargi_turu)
                        kaydedilen += 1
                    except Exception as e:
                        self.log_fn(f"⚠️ Kayıt atlandı ({rec.get('dosyaNo','')}): {e}")
                        continue
                    if not taraf_da_cek:
                        continue
                    # ZATEN taraf verisi olan kayıt atlanır (kullanıcı bulgusu,
                    # 2026-07-13: taraf_da_cek ilk eklendiğinde HER kayıt için
                    # koşulsuz ek istek atılıyordu — büyük kapsamlarda (binlerce
                    # dosya) bu, tek çalıştırmayı pratikte bitmeyecek kadar
                    # yavaşlatıp 'Yenile' düğmesini uzun süre devre dışı bırakıyor,
                    # kullanıcıya "bir kere çalıştı, sonra tıklanmıyor" gibi
                    # görünüyordu. Bu atlama sayesinde TEKRAR eden turlar yalnız
                    # YENİ/taraf'ı henüz çekilmemiş dosyalar için istek atar.
                    # Kimlikli (isim/unvan dolu) taraf kaydı VARSA atla —
                    # yalnız BOŞ/hayalet kayıt (bkz. dosya_taraf_kaydet'teki
                    # "Kimliksiz kayıt KORUMASI") "taraf var" SAYILMAZ (canlı
                    # DB denetimi, 2026-08-13: 953 dosyada boş hayalet taraf
                    # kaydı bulunmuştu; eski `exists()` kontrolü bu dosyalar
                    # için toplu sorguda taraf bilgisini SONSUZA DEK bir daha
                    # hiç çekmiyordu — kullanıcı bulgusu: "Tüm Dosyalarım'dan
                    # sorgula dedim, veritabanını güncelledim ama taraf
                    # bilgileri hâlâ çıkmıyor").
                    if dosya.taraf_baglari.exclude(
                            taraf__ad="", taraf__soyad="", taraf__unvan="").exists():
                        continue
                    # Yalnız `taraf_da_cek=True` iken (bkz. docstring): kapsamdaki
                    # YENİ dosya sayısı kadar EK canlı UYAP isteği. Bir kaydın
                    # taraf çekimi başarısız olursa yalnız o kayıt atlanır, tur
                    # devam eder.
                    try:
                        # KISA timeout/tek deneme (bkz. dosya_taraf_getir
                        # docstring): taraf sayısı çok olan dosyalarda UYAP
                        # yanıtı 90s×3 varsayılanla ~4.5dk sürebiliyor —
                        # kullanıcı bulgusu (2026-07-12): 'Yenile' tam da
                        # böyle 'kalabalık' bir dosyada takılıp kalıyordu.
                        # Burada başarısız/yavaş bir dosya HIZLI atlanır,
                        # tur devam eder; tek dosya ayrıntısını açan kullanıcı
                        # (dosya_detay_goster_ve_kaydet) hâlâ tam sabırla bekler.
                        taraflar = dosya_taraf_getir(dosya_id, self.log_fn, timeout=20, denemeler=1,
                                                      istek_sarici=self._istek_sarici)
                        if taraflar:
                            dosya_taraf_kaydet(dosya, taraflar, self.log_fn)
                    except Exception as e:
                        self.log_fn(f"⚠️ Taraf bilgisi atlandı ({rec.get('dosyaNo','')}): {e}")
                    taraf_cekilen += 1
                    if taraf_cekilen % 50 == 0:
                        # İlerleme günlüğü — bu satır olmadan uzun bir tur
                        # sessizce çalışır ve kullanıcıya "takıldı" gibi görünür
                        # (kullanıcı bulgusu, 2026-07-13).
                        self.log_fn(f"… taraf bilgisi: {taraf_cekilen} yeni dosya işlendi ({ad})")

            toplam += len(kayitlar)
            sonuclar.append({
                "yargi_turu": yargi_turu, "yargi_turu_adi": ad,
                "yargi_birimi_kod": yargi_birimi_kod,
                "bulunan": len(kayitlar), "kaydedilen": kaydedilen,
            })
        return toplam, sonuclar


# ── "Dosya Görüntüle" — Dosya Bilgileri sekmesi ayrıntısı ────────────────────
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §2.5/§7 "Kalan")
# İstek şekli TAHMİN DEĞİL: Panel/modules/Vekalet_Sunma.py'deki CANLI YAKALANMIŞ
# akışın 7. adımından alındı — {"dosyaId": "..."} tek alanlı payload.
# Yanıt şeklinin YALNIZ bir kısmı canlı doğrulandı (plan §1.5/§2.5, önceki
# oturumdan — prose özet, ham JSON dökümü değil). Bu yüzden ingest fonksiyonu
# YALNIZ doğrulanan anahtarları yazar; geri kalanı (ör. faiz/masraf ayrıntısı,
# ilgili/seri/birleşen dosya listeleri, başvuruya bırakılma tarihi) UYDURULMAZ,
# model varsayılanında bırakılır. Ham yanıt log_fn'e yazılır ki canlı doğrulama
# yapıldığında gerçek anahtar adları buradan (loglardan) okunabilsin.
DOSYA_AYRINTI_ENDPOINT = "dosyaAyrintiBilgileri_brd.ajx"


def dosya_ayrinti_getir(dosya_id, log_fn=None, istek_sarici=None):
    """`dosyaAyrintiBilgileri_brd.ajx` çağrısı. `dosya_id`, çağrıldığı ANKİ
    arama oturumunun TAZE 'dosyaId' değeri olmalı (bkz. models.Dosya
    docstring'i — dosyaId oturumluktur, DB'den eski bir değer okunup buraya
    verilmemeli). `istek_sarici`: VARSAYILAN `_bireysel_istek` (kullanıcının
    doğrudan tıklaması) — SWR arka plan tazelemesi gibi düşük öncelikli
    çağıranlar `_arka_plan_istek` vermeli (bkz. `dosya_detay_goster_ve_kaydet`)
    ki kullanıcının bir SONRAKİ gerçek tıklamasının önüne geçmesin. Ağ
    hatasında ya da beklenmeyen yanıt şeklinde {} döner."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        # Öncelik geçidi (bkz. istek_sarici) + UYAP'ın "eşzamanlı" reddini
        # GERÇEK veri sanmayan koruma (bkz. _post_eszamanli_korumali) —
        # kullanıcı bulgusu, 2026-07-13.
        veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor,
                                         DOSYA_AYRINTI_ENDPOINT, {"dosyaId": str(dosya_id)}, log)
    except Exception as e:
        log(f"⚠️ Dosya ayrıntısı alınamadı: {e}")
        return {}
    if _yanit_hata_zarfi_mi(veri):
        log(f"⚠️ UYAP hata zarfı döndürdü (gerçek veri değil): {veri} — "
            "bu tur DB'ye YAZILMAYACAK, eski/doğru veri korunacak.")
        return {}
    return veri if isinstance(veri, dict) else {}


def _decimal_veya_sifir(deger):
    from decimal import Decimal, InvalidOperation
    try:
        if deger in (None, ""):
            return Decimal("0")
        return Decimal(str(deger))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _acik_metin_bul(ham, kod_alan):
    """`ham` (dosyaAyrintiBilgileri_brd.ajx yanıtı) içinde `kod_alan` (ör.
    'takibinTuru') ile İLGİLİ, kod DEĞİL okunabilir METİN taşıyan bir kardeş
    alan arar. UYARI: bu uç nokta için açıklama alanının GERÇEK adı henüz
    canlı doğrulanmadı (bkz. plan §2.5) — dönen eşleşme bir TAHMİNDİR (ad
    benzerliğine dayanır, MTS modülündeki '<alan>Aciklama'/'<alan>Adi'
    deseninden esinlenir — bkz. uyap_core/mts/parse.py — ama bu, FARKLI bir
    UYAP alt sisteminin XML şeması, bu .ajx uç noktasının kanıtı DEĞİLDİR).
    Bu YÜZDEN çağıran kod bu değeri asla DB'ye yazmamalı, yalnız ekranda
    '(tahmini)' etiketiyle göstermelidir — kesin/doğrulanmış veriymiş gibi
    sunulursa kullanıcı yanlış bilgiye güvenebilir. Bulunamazsa ("", "")
    döner. İkinci eleman hangi anahtarın eşleştiğidir — log_fn'e yazılır ki
    bir canlı test sırasında gerçek anahtar adı buradan teyit/düzeltilebilsin."""
    kod_deger = str(ham.get(kod_alan, "") or "").strip()
    kok = kod_alan.lower()
    en_iyi_anahtar, en_iyi_deger = "", ""
    for anahtar, deger in ham.items():
        if anahtar == kod_alan or not isinstance(deger, str):
            continue
        deger = deger.strip()
        if not deger or deger == kod_deger or deger.isdigit() or kok not in anahtar.lower():
            continue
        if len(deger) > len(en_iyi_deger):
            en_iyi_anahtar, en_iyi_deger = anahtar, deger
    return en_iyi_deger, en_iyi_anahtar


def dosya_ayrinti_kaydet(dosya, ham, log_fn=None):
    """Bir `Dosya` (Django nesnesi) için `dosya_ayrinti_getir`'in ham yanıtını
    ailesine (İcra/Hukuk) göre `IcraTakipDetay`/`HukukDavaDetay`'a yazar.
    Aile, kaydın kendi alanından DEĞİL `dosya.birim.yargi_turu`'ndan belirlenir
    (Dosya'da yargi_turu alanı YOK, §1.6). Diğer yargı türleri için henüz
    *Detay modeli yok (plan §5) — o durumda hiçbir şey yazılmaz, yalnız
    loglanır. Döner: ("icra"|"hukuk"|None, kaydedildi:bool)."""
    log = log_fn or (lambda m: None)
    if not isinstance(ham, dict) or not ham:
        log("⚠️ Dosya ayrıntısı boş/geçersiz yanıt — kaydedilmedi.")
        return None, False
    log(f"… ham dosya ayrıntısı (teşhis/canlı doğrulama için): {ham}")

    _django_hazirla()
    from icra_models.models import IcraTakipDetay, HukukDavaDetay
    from icra_models.ingest import _tarih

    yargi_turu = dosya.birim.yargi_turu
    if yargi_turu == 2:  # İcra
        turu_metin, turu_k = _acik_metin_bul(ham, "takibinTuru")
        sekli_metin, sekli_k = _acik_metin_bul(ham, "takibinSekli")
        yolu_metin, yolu_k = _acik_metin_bul(ham, "takibinYolu")
        for etiket, anahtar, metin in (("takibinTuru", turu_k, turu_metin),
                                        ("takibinSekli", sekli_k, sekli_metin),
                                        ("takibinYolu", yolu_k, yolu_metin)):
            if metin:
                log(f"… '{etiket}' için TAHMİNİ açıklama '{anahtar}' alanından "
                    f"bulundu: {metin!r} (canlı doğrulanmadı — DB'ye yazılmadı, "
                    "yalnız ekranda '(tahmini)' etiketiyle gösterilecek).")
        # takibin_turu/sekli/yolu_aciklama alanları BİLEREK 'defaults'a
        # eklenmiyor: gerçek UYAP anahtar adı canlı doğrulanana kadar DB'ye
        # tahmini metin yazılmaz (bkz. _acik_metin_bul docstring'i) — yalnız
        # kod alanları güncellenir, açıklama alanları dokunulmadan kalır.
        IcraTakipDetay.objects.update_or_create(
            dosya=dosya,
            defaults={
                "takibin_turu": str(ham.get("takibinTuru", "") or ""),
                "takibin_sekli": str(ham.get("takibinSekli", "") or ""),
                "takibin_yolu": str(ham.get("takibinYolu", "") or ""),
                "alacak_kalemi_toplam": _decimal_veya_sifir(ham.get("alacakKalemToplamTutar")),
                "vekalet_ucreti": _decimal_veya_sifir(ham.get("vekaletUcreti")),
                "tahsil_harci": _decimal_veya_sifir(ham.get("tahsilHarci")),
            },
        )
        # UI (masaüstü+web) 'ham'ı doğrudan kullanır; kod yerine TAHMİNİ metni
        # gösterebilsin diye senkron anahtarlar eklenir, '(tahmini)' etiketiyle
        # açıkça işaretlenir — bulunamadıysa koda düşer (etiketsiz).
        ham["takibinTuru_metin"] = f"{turu_metin} (tahmini)" if turu_metin else str(ham.get("takibinTuru", "") or "")
        ham["takibinSekli_metin"] = f"{sekli_metin} (tahmini)" if sekli_metin else str(ham.get("takibinSekli", "") or "")
        ham["takibinYolu_metin"] = f"{yolu_metin} (tahmini)" if yolu_metin else str(ham.get("takibinYolu", "") or "")
        return "icra", True
    if yargi_turu == 1:  # Hukuk
        HukukDavaDetay.objects.update_or_create(
            dosya=dosya,
            defaults={
                "dava_acilis_turu": str(ham.get("davaAcilisTuru", "") or ""),
                "dava_turleri": str(ham.get("davaTurleriStr", "") or ""),
                "ilgili_dava_listesi": str(ham.get("ilgiliDavaListesiStr", "") or ""),
                "durusma_tarihi": _tarih(ham.get("durusmaTarihi")),
            },
        )
        if ham.get("basvuruyaBirakilmaTarihiStr"):
            log("… 'basvuruyaBirakilmaTarihiStr' biçimi henüz canlı doğrulanmadı, "
                f"kaydedilmedi (ham değer: {ham.get('basvuruyaBirakilmaTarihiStr')!r}).")
        return "hukuk", True
    log(f"⚠️ Yargı türü {yargi_turu} için henüz ayrı bir *Detay modeli yok "
        "(bkz. plan §5) — bu dosya için ayrıntı kaydedilmedi.")
    return None, False


# ── Taraf Bilgileri (tüm yargı türleri) ──────────────────────────────────
# Canlı doğrulandı (2026-07-10, Hukuk dava dosyası, klasik avukat.uyap.gov.tr
# üzerinden Chrome ağ trafiği yakalanarak — bkz.
# docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §8). Yanıt şekli:
#   [{"adi": str, "rol": "Davacı"|"Davalı", "vekil": "[AD SOYAD]" (opsiyonel —
#     vekil yoksa anahtar HİÇ yok), "kisiKurum": "Kişi"|"Kurum"}, ...]
# TCKN/MERSİS/vergi no VERMEZ — save_taraf bu yüzden ad/unvan eşleşmesine
# düşer (icra_core.save_taraf zaten bunu destekliyor, tckn'i defaults'ta
# EZMEZ). Yalnız Hukuk canlı test edildi; sekmenin diğer yargı türlerinde de
# aynı uç noktayı kullandığı `dosya_islem_turleri_sorgula_brd.ajx` yanıtındaki
# ortak modül listesinden (taraf_bilgileri) çıkarılıyor — ayrı ayrı
# doğrulanmadı, bu yüzden hata durumunda sessizce [] döner (varsayım UI'ı
# bozmaz, yalnız o dosya için taraf boş kalır).
TARAF_BILGILERI_ENDPOINT = "dosya_taraf_bilgileri_brd.ajx"


def dosya_taraf_getir(dosya_id, log_fn=None, timeout=90, denemeler=3, istek_sarici=None):
    """`dosya_taraf_bilgileri_brd.ajx` çağrısı. `dosya_id` TAZE olmalı (bkz.
    `dosya_ayrinti_getir` docstring'i — aynı oturumluk kısıt). Ağ hatasında
    ya da beklenmeyen yanıt şeklinde [] döner. `timeout`/`denemeler`
    VARSAYILANI (90s×3 ≈ 4.5dk) kullanıcının TEK dosya için beklediği
    'Dosya Görüntüle' akışına uygundur; taraf sayısı ÇOK olan dosyalarda
    UYAP'ın yanıt süresi de uzuyor — toplu 'Yenile' turunda (bkz.
    `DosyaSorgu.calistir`) bu yüzden DAHA KISA değerler verilir, aksi halde
    tek 'kalabalık' dosya turun tamamını dakikalarca kilitleyip kullanıcıya
    'takıldı' izlenimi veriyordu (kullanıcı bulgusu, 2026-07-12). `istek_sarici`:
    bkz. `dosya_ayrinti_getir` docstring'i — VARSAYILAN `_bireysel_istek`."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        # bkz. dosya_ayrinti_getir'deki AYNI gerekçe — bu da öncelik geçidi
        # (istek_sarici) + eşzamanlı-red koruması.
        veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor,
                                         TARAF_BILGILERI_ENDPOINT, {"dosyaId": str(dosya_id)}, log,
                                         timeout=timeout, denemeler=denemeler)
    except Exception as e:
        log(f"⚠️ Taraf bilgileri alınamadı: {e}")
        return []
    return veri if isinstance(veri, list) else []


def _taraf_bilgisi_ayristir(item):
    """`dosya_taraf_getir` satırını `save_taraf`'ın beklediği sözlüğe çevirir.
    Ad/soyad ayrımı, kod tabanında zaten kullanılan sezgiyle yapılır (son
    kelime soyad — bkz. icra_core.parse_taraf_from_uyap'taki unvan bölme)."""
    adi = str(item.get("adi", "") or "").strip()
    if tr_lower(item.get("kisiKurum", "")) == "kurum":
        return {"tur": "tuzel", "unvan": adi, "ad": "", "soyad": "",
                "tckn": None, "vergi_no": "", "mersis_no": None}
    parcalar = adi.rsplit(None, 1)
    ad, soyad = (parcalar[0], parcalar[1]) if len(parcalar) == 2 else (adi, "")
    return {"tur": "gercek", "ad": ad, "soyad": soyad, "unvan": "",
            "tckn": None, "vergi_no": "", "mersis_no": None}


def _vekiller_kaydet(vekil_ham, Vekil, log_fn=None):
    """`item['vekil']` (ör. "[HAKAN TOLUNAY BURHAN]" ya da birden fazla vekil
    olduğunda "[AD1 SOYAD1, AD2 SOYAD2]") ayrıştırıp dedup ederek `Vekil`
    nesnelerinin LİSTESİNİ döner (kullanıcı bulgusu, 2026-07-11: eskiden yalnız
    ilki kaydedilip diğerleri atılıyordu — `DosyaTaraf.vekiller` artık M2M).
    TCKN/baro yok — yalnız ad+soyad eşleşmesiyle dedup edilir (bu uç nokta
    başka kimlik vermiyor)."""
    log = log_fn or (lambda m: None)
    metin = str(vekil_ham or "").strip()
    if metin.startswith("[") and metin.endswith("]"):
        metin = metin[1:-1].strip()
    if not metin:
        return []
    isimler = [p.strip() for p in metin.split(",") if p.strip()]
    sonuc = []
    for isim in isimler:
        parcalar = isim.rsplit(None, 1)
        ad, soyad = (parcalar[0], parcalar[1]) if len(parcalar) == 2 else (isim, "")
        if not ad:
            continue
        vekil = Vekil.objects.filter(ad=ad, soyad=soyad).first()
        sonuc.append(vekil or Vekil.objects.create(ad=ad, soyad=soyad))
    return sonuc


def dosya_taraf_kaydet(dosya, ham_liste, log_fn=None):
    """`dosya_taraf_getir`'in ham yanıtını `DosyaTaraf`'a yazar. Rol, Türkçe
    metinden `tr_lower` ile koda çevrilir (Davacı→davaci, Davalı→davali,
    Alacaklı→alacakli, Borçlu→borclu — bkz. models.DosyaTaraf.Rol). Döner:
    kaydedilen_sayisi:int."""
    log = log_fn or (lambda m: None)
    if not isinstance(ham_liste, list) or not ham_liste:
        return 0
    _django_hazirla()
    import django
    from icra_models.models import Taraf, Vekil, DosyaTaraf
    sayac = 0
    with django.db.transaction.atomic():
        for idx, item in enumerate(ham_liste):
            if not isinstance(item, dict):
                continue
            info = _taraf_bilgisi_ayristir(item)
            # Kimliksiz kayıt KORUMASI (canlı DB denetimi, 2026-08-13): UYAP'ın
            # taraf_bilgileri yanıtı bazı istisnai dosya türlerinde (Merkezi
            # Takip Sistemi / Abonelik Sözleşmeleri İcra Daireleri gibi —
            # görülen dosya.tur_kod: 35, 294) ad/soyad/tckn/unvan/mersis'i TAMAMEN
            # boş bir satır döndürüyor. save_taraf bunu hiçbir önceki kayıtla
            # eşleştiremediğinden (dedup anahtarı yok) her çağrıda YENİ bir
            # boş Taraf+DosyaTaraf satırı oluşuyordu — 953 dosyada, çoğunda
            # zaten adı dolu gerçek bir taraf VARKEN, sırf tekrar tekrar
            # "Dosya Görüntüle" açıldıkça biriken 1799 hayalet kayıt tespit
            # edildi. Kimliği olmayan satır hiçbir zaman kaydedilmez.
            if not (info.get("tckn") or info.get("mersis_no")
                    or (info.get("unvan") or "").strip()
                    or (info.get("ad") or "").strip() or (info.get("soyad") or "").strip()):
                log(f"  ⚠️ Kimliksiz taraf kaydı atlandı (rol={item.get('rol', '')!r}).")
                continue
            taraf_obj = save_taraf(info, Taraf)
            vekil_objs = _vekiller_kaydet(item.get("vekil"), Vekil, log)
            rol_kod = tr_lower(item.get("rol", "")) or "diger"
            dt, _ = DosyaTaraf.objects.update_or_create(
                dosya=dosya, taraf=taraf_obj, rol=rol_kod,
                defaults={"sira": idx})
            dt.vekiller.set(vekil_objs)
            sayac += 1
    return sayac


# ── Canlı sorgu başarısız olursa DB'ye düş (kullanıcı bulgusu, 2026-07-14:
# "eşzamanlı" reddi/ağ hatası yüzünden dosya_ayrinti_getir ya da
# dosya_taraf_getir boş dönünce ekran daha önce BAŞARIYLA kaydedilmiş veriyi
# de yok sayıp kullanıcıya çıplak hata gösteriyordu — zaten işlenmiş veri
# ASLA sessizce kaybolmamalı). Bu iki fonksiyon `IcraTakipDetay`/
# `HukukDavaDetay`/`DosyaTaraf`'tan `ham`/`taraflar` ile AYNI şekle sahip bir
# sözlük/liste kurar; `_ozet_sekmesi`/`_taraflar_sekmesi` (UI) hangi kaynaktan
# geldiğini bilmeden aynen render eder.
def _ham_db_den_olustur(dosya):
    """Kayıtlı `IcraTakipDetay`/`HukukDavaDetay`'dan `dosya_ayrinti_getir`
    şeklinde bir 'ham' sözlüğü kurar. '_metin' (tahmini açıklama) alanları
    BİLEREK YOK — o yalnız CANLI yanıtta üretilir, DB'de tutulmaz (bkz.
    `_acik_metin_bul` docstring'i); bu yüzden düşme (fallback) yolunda
    kullanıcı ham kodu görür, tahmini açıklamayı değil — yine de HİÇBİR ŞEY
    görmemekten iyidir."""
    yargi_turu = dosya.birim.yargi_turu
    if yargi_turu == 2:
        try:
            d = dosya.icra_detay
        except Exception:
            return {}
        return {
            "takibinTuru": d.takibin_turu,
            "takibinSekli": d.takibin_sekli,
            "takibinYolu": d.takibin_yolu,
            "alacakKalemToplamTutar": str(d.alacak_kalemi_toplam),
            "vekaletUcreti": str(d.vekalet_ucreti),
            "tahsilHarci": str(d.tahsil_harci),
        }
    if yargi_turu == 1:
        try:
            d = dosya.hukuk_detay
        except Exception:
            return {}
        return {
            "davaAcilisTuru": d.dava_acilis_turu,
            "davaTurleriStr": d.dava_turleri,
            "ilgiliDavaListesiStr": d.ilgili_dava_listesi,
            "durusmaTarihi": d.durusma_tarihi.isoformat() if d.durusma_tarihi else "",
        }
    return {}


def _taraflar_db_den_olustur(dosya):
    """Kayıtlı `DosyaTaraf`lardan `dosya_taraf_getir` şeklinde (adi/rol/vekil)
    bir liste kurar."""
    sonuc = []
    for dt in dosya.taraf_baglari.select_related("taraf").prefetch_related("vekiller").all():
        vekiller = ", ".join(f"{v.ad} {v.soyad}".strip() for v in dt.vekiller.all())
        sonuc.append({
            "rol": dt.get_rol_display(),
            "adi": str(dt.taraf),
            "vekil": f"[{vekiller}]" if vekiller else "",
        })
    return sonuc


def _taraflar_kesinlesme_bilgisi_ekle(dosya, taraflar):
    """`taraflar` (dosya_taraf_getir/_taraflar_db_den_olustur şekli: rol/adi/
    vekil) listesine DB'deki `DosyaTaraf.kesinlesme_durumu`/`tebligat_durumu`
    (yalnız borçlularda anlamlı) bilgisini ekler — kullanıcı isteği
    (2026-08-04): "Dosyaya tıkladığımızda açılan pencerede tebligat ve
    kesinleşme durumunu da görmek istiyorum". Bu iki alan UYAP'tan ASLA
    gelmez (yalnız yerelde hesaplanır, bkz. kesinlesme_durumlarini_guncelle) —
    bu yüzden canlı taraf yanıtında hiç yok, DB'ye AYRICA bakılır. Rol+ad ile
    eşleştirilir (tr_lower, TCKN'siz — save_taraf'ın kendi eşleştirmesiyle AYNI
    disiplin). `taraflar` YERİNDE değiştirilir, ayrıca döner."""
    try:
        borclular = {
            tr_lower(str(dt.taraf)): dt
            for dt in dosya.taraf_baglari.select_related("taraf").filter(rol="borclu")
        }
    except Exception:
        return taraflar
    for t in taraflar:
        dt = borclular.get(tr_lower(t.get("adi", "")))
        t["kesinlesmeDurumu"] = dt.get_kesinlesme_durumu_display() if dt else ""
        t["tebligatDurumu"] = dt.get_tebligat_durumu_display() if dt else ""
    return taraflar


def dosya_detay_goster_ve_kaydet(rec, log_fn=None, istek_sarici=None):
    """Tek giriş noktası (UI'ların çağıracağı): ham arama kaydından (`rec` —
    `search_phrase_detayli.ajx` satırı, TAZE 'dosyaId' içermeli) ayrıntı VE
    taraf bilgilerini çeker, ilgili `Dosya`'yı doğal anahtarıyla
    (birim+yıl+sıra+tür) bulur ve kaydeder. Döner: (ham:dict, aile:str|None,
    kaydedildi:bool, hata:str|None, taraflar:list) — `hata` doluysa
    `ham`/`aile` boş/None olabilir, UI kullanıcıya `hata`yı göstermeli.
    `taraflar`, `dosya_taraf_getir`'in ham (UYAP şekilli) listesidir; ayrıntı
    başarısız olsa bile boş liste olarak döner (asla None). `istek_sarici`:
    bkz. `dosya_ayrinti_getir` docstring'i — VARSAYILAN `_bireysel_istek`
    (kullanıcının doğrudan tıklaması); düşük öncelikli arka plan çağıranları
    `_arka_plan_istek` vermeli (bkz. `DosyaSorgu`).

    CANLI SORGU BAŞARISIZ OLURSA (bkz. modül düzeyi not, _ham_db_den_olustur):
    daha önce kaydedilmiş DB verisi varsa ONA düşülür — kullanıcı asla
    'zaten işlediğimiz veri' yüzünden boş ekran görmez. Bu durumda dönen
    `ham` sözlüğünde `_onbellekten` anahtarı True olur (UI bunu bir uyarı
    şeridi göstermek için kullanabilir); `kaydedildi` False kalır (yeni bir
    şey YAZILMADI, yalnız gösterildi)."""
    log = log_fn or (lambda m: None)
    dosya_id = str((rec or {}).get("dosyaId", "") or "")
    if not dosya_id:
        return {}, None, False, ("Bu kayıtta 'dosyaId' yok (DB önbelleğinden "
                                  "geliyor olabilir) — listeyi yenileyip tekrar deneyin."), []

    # Dosya kaydı YALNIZ `rec`'ten (birim/yıl/sıra/tür) bulunur — `ham`'a
    # bağımlı DEĞİL, bu yüzden canlı sorgu tamamen başarısız olsa bile önce
    # bu arama yapılabilir (fallback'in ön koşulu).
    dosya = None
    try:
        _django_hazirla()
        from icra_models.models import Dosya
        from icra_models.ingest import _yil_sira
        birim_id = str((rec or {}).get("birimId", "") or "")
        yil, sira = _yil_sira((rec or {}).get("dosyaNo"))
        tur_kod = int((rec or {}).get("dosyaTurKod", 0) or 0)
        dosya = Dosya.objects.select_related("birim").get(
            birim__birim_id=birim_id, yil=yil, sira_no=sira, tur_kod=tur_kod)
    except Exception as e:
        log(f"… dosya yerel veritabanında bulunamadı (fallback devre dışı kalacak): {e}")
        dosya = None

    ham = dosya_ayrinti_getir(dosya_id, log, istek_sarici)
    canli_basarisiz = not ham
    if canli_basarisiz and dosya is not None:
        onbellek = _ham_db_den_olustur(dosya)
        if onbellek:
            log("⚠️ Canlı dosya ayrıntısı alınamadı — yerel veritabanındaki "
                "önceki kayıt gösteriliyor (bu tur DB'ye yeni bir şey yazılmadı).")
            ham = onbellek
            ham["_onbellekten"] = True

    if not ham:
        if dosya is None:
            return {}, None, False, "Dosya ayrıntısı alınamadı (bağlantı/oturum sorunu olabilir).", []
        return {}, None, False, ("Dosya ayrıntısı alınamadı ve yerel veritabanında da "
                                  "önbelleğe alınmış bir kayıt yok (henüz hiç görüntülenmemiş olabilir)."), []
    if dosya is None:
        return ham, None, False, ("Dosya yerel veritabanında bulunamadı (önce "
                                   "Sorgula/senkron ile kaydedilmeli olabilir)."), []

    if canli_basarisiz:
        aile = {2: "icra", 1: "hukuk"}.get(dosya.birim.yargi_turu)
        kaydedildi = False
    else:
        aile, kaydedildi = dosya_ayrinti_kaydet(dosya, ham, log)
    # İcra'da bu uç nokta CANLI DOĞRULANDI (2026-07-11, İzmir Banka Alacakları
    # İcra Dairesi 2026/89122 dosyası, avukat.uyap.gov.tr Taraf Bilgileri
    # sekmesi): rol string'leri "Alacaklı"/"Borçlu" — tr_lower() ile icra_core.
    # py'nin DosyaTaraf.Rol seçenekleriyle ("alacakli"/"borclu") BİREBİR
    # eşleşiyor. Ayrıca 'dosya_borclu_list.ajx' (icra_core.py'nin kendi
    # akışı) ile karşılaştırıldı: aynı kişi için adi/soyadi alanları harf
    # harf aynı ("FİKRİ"/"BOZKUŞ") ve her iki update_or_create çağrısı da
    # aynı (dosya, taraf, rol) anahtarını kullanıyor — bu yüzden save_taraf'ın
    # TCKN'siz (ad,soyad) eşleşmesi icra_core'un TCKN'li kaydettiği AYNI Taraf
    # satırına düşüyor (mevcut tckn'ye dokunulmuyor, sira/vekil güncelleniyor).
    # icra_core.py yalnız rol="borclu" yazıyor; bu akış ayrıca "alacakli"
    # satırlarını da (ilk kez) ekliyor — DosyaTaraf.objects.update_or_create
    # hiçbir zaman silme yapmadığından en kötü ihtimalde kozmetik bir
    # yinelenen satır oluşur, mevcut kanıtlı veri kaybolmaz.
    taraflar = dosya_taraf_getir(dosya_id, log, istek_sarici=istek_sarici)
    if taraflar:
        log(f"… {len(taraflar)} taraf canlı olarak alındı: "
            + ", ".join(f"{t.get('rol', '')}:{t.get('adi', '')}" for t in taraflar))
        try:
            dosya_taraf_kaydet(dosya, taraflar, log)
        except Exception as e:
            log(f"⚠️ Taraf bilgileri kaydedilemedi: {e}")
    elif dosya.taraf_baglari.exists():
        log("⚠️ Canlı taraf bilgisi alınamadı (boş yanıt) — yerel veritabanındaki önceki taraflar gösteriliyor.")
        taraflar = _taraflar_db_den_olustur(dosya)
    else:
        log("… canlı taraf sorgusu boş döndü ve yerel veritabanında da önceki taraf kaydı yok "
            "(Taraflar sekmesi bu yüzden hiç görünmeyecek).")
    if taraflar:
        taraflar = _taraflar_kesinlesme_bilgisi_ekle(dosya, taraflar)
    return ham, aile, kaydedildi, None, taraflar


# Stale-while-revalidate ("Dosya Görüntüle" için önbellek+arka plan tazeleme)
# GERİ ALINDI (kullanıcı bulgusu, 2026-07-13): önbellekli 'ham' takibin türü/
# şekli/yolu için yalnız ÇIPLAK UYAP kodunu ("1"/"0" vb.) içerebiliyordu —
# okunabilir '(tahmini)' açıklama metni BİLEREK DB'ye yazılmıyor (bkz.
# dosya_ayrinti_kaydet, UYDURULMAZ disiplini), yalnız CANLI yanıtta
# üretiliyor. Sessiz arka plan tazelemesi bu metni kullanıcıya bir daha HİÇ
# göstermiyordu — gerçek bir regresyondu. "Dosya Görüntüle" bu yüzden HER
# ZAMAN canlı sorguya düşer (bkz. dosya_detay_goster_ve_kaydet) — yalnız
# `dosyalarim_db_listele` (liste ekranı, zaten UYAP'a hiç gitmiyordu) hızlı
# kalır.


# ── Genel "Dosyalarım" (çoklu yargı türü) — DB tarama + filtre ──────────────
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 "Kalan" — filtre UI)
# İcra Dosyalarım'ın aksine bu ekran CANLI UYAP sorgusu yapmaz (yalnız DB
# okur) — SenkronKapsami'nin (Faz 3/4) zaten doldurduğu veriyi yargı
# türü/birimi/dosya türü/durum/tarih aralığına göre tarar. Tazelemek isteyen
# kullanıcı "Yenile" ile `dosyalarim_yenile` (= DosyaSorgu.calistir, arka plan
# zamanlayıcısıyla AYNI mantık, yalnız HEMEN) çağırır, sonra DB yeniden okunur.
def dosya_tur_secenekleri(yargi_turu=None):
    """Dosya Türü seçeneklerini döner: list[(kod, ad)].
    `Dosya.Tur` (models.py) sabit enum'u yalnız canlı doğrulanan birkaç değeri
    kapsar (0/1=Esas/Talimat → İcra'ya özgü, 14/15=Hukuk Değişik İş/Hukuk Dava
    → Hukuk'a özgü) ve yargı türüne göre AYRIM YAPMAZ — bu yüzden hangi Yargı
    Türü seçilirse seçilsin aynı 4 seçenek listeleniyordu (kullanıcı bulgusu,
    2026-07-11). Burada onun yerine yerel DB'deki GERÇEK kayıtlardan, verilen
    `yargi_turu`ya ait DISTINCT (tur_kod, tur) çiftleri okunur — `tur` UYAP'ın
    kendi serbest-metin etiketidir, `durum`/`durum_kod` ile aynı desen (bkz.
    models.py Dosya.durum yorumu). `yargi_turu=None` ise tüm DB taranır.
    O tür için DB'de hiç kayıt yoksa (henüz senkron edilmemiş) statik
    `Dosya.Tur` listesine düşülür — uydurma değil, en azından bilinen
    kodları göstermek için."""
    _django_hazirla()
    from icra_models.models import Dosya
    qs = Dosya.objects.all()
    if yargi_turu not in (None, ""):
        qs = qs.filter(birim__yargi_turu=int(yargi_turu))
    satirlar = (qs.exclude(tur="")
                  .values_list("tur_kod", "tur").distinct().order_by("tur_kod"))
    secenekler = {}
    for kod, ad in satirlar:
        secenekler.setdefault(kod, ad)
    if secenekler:
        return sorted(secenekler.items())
    return [(v, l) for v, l in Dosya.Tur.choices]


def dosya_durum_secenekleri():
    """`Dosya.Durum` (models.py) seçeneklerini döner: list[(kod, ad)]."""
    _django_hazirla()
    from icra_models.models import Dosya
    return [(v, l) for v, l in Dosya.Durum.choices]


TARAF_SUTUN_SAYISI = 4
# Sabit görüntü sütunu sayısı — kullanıcı bulgusu (2026-07-12): taraflar TEK
# bir metin sütununda BİRLEŞTİRİLMEMELİ, her taraf kendi sütununda ayrı ayrı
# görünmeli ki taraf adına göre filtreleme anlamlı olsun. Canlı DB'de şimdiye
# dek gözlenen azami taraf sayısı 3'tür; pay bırakmak için 4 sütun ayrıldı.
# Bundan fazlası olursa (ör. çok taraflı CBS/arabuluculuk dosyası) taşan
# taraflar SON sütuna ("; " ile) eklenir — hiçbiri sessizce atılmaz. Alacaklı/
# Borçlu bu 4 sütuna hiç girmez (kendi ayrı "alacakli"/"borclu" sütunlarında
# gösteriliyorlar, bkz. `_taraflar_sutunlari` içindeki filtre) — bütçe yalnız
# kalan (üçüncü şahıs/vekil dışı taraf) sayısına göre işler.


def _taraflar_sutunlari(dosya):
    """Bir `Dosya`nın `taraf_baglari`sından (prefetch edilmiş olmalı)
    `TARAF_SUTUN_SAYISI` uzunluğunda bir liste üretir; her eleman "Rol: Ad
    Soyad" biçiminde TEK bir tarafı temsil eder (boş kalan slotlar ""). Rol
    etiketi `DosyaTaraf.Rol` seçeneklerinden (bilinenler); listede olmayan
    roller (ör. arabuluculuk/CBS'e özgü "talep eden", "katilan" — kullanıcı
    bulgusu, 2026-07-12: yargı türüne göre rol kümesi değişiyor) ham rol
    metninin ilk harfi büyütülerek gösterilir — uydurma değil, DB'deki gerçek
    metnin biçimi.

    İcra dosyalarında (`YARGI_TURU_ICRA`) taraf sayısı sütun bütçesini
    aşıyorsa (ör. onlarca borçlulu konsolide dosya) ham `sira` yerine
    ÖNCELİK sırası kullanılır: (Ayarlar'da kendi vekil adı tanımlıysa) vekili
    olduğumuz taraf önce gelir — kullanıcı bulgusu, 2026-07-12: kalabalık
    İcra dosyasında önemli taraflar rastgele 'taşan' sütuna gömülüp fiilen
    görünmez oluyordu. Alacaklı/Borçlu bu önceliklendirmeye artık dahil
    DEĞİL çünkü `baglar`'dan zaten çıkarılıyorlar (kendi ayrı sütunlarında
    gösteriliyorlar, bkz. aşağıdaki filtre). Diğer yargı türlerinde ve az
    tarafli dosyalarda davranış DEĞİŞMEZ (ham sıra)."""
    from icra_models.models import DosyaTaraf
    rol_etiket = dict(DosyaTaraf.Rol.choices)
    baglar = list(dosya.taraf_baglari.all())
    # Alacaklı/Borçlu artık `dosyalarim_db_listele`'de kendi ayrı sütunlarında
    # ("alacakli"/"borclu") gösteriliyor — burada TEKRAR göstermemek için bu
    # iki rol çıkarılır (kullanıcı bulgusu, 2026-07-13: "Alacaklı: X" hem
    # kendi sütununda hem taraf1'de aynı anda görünüyordu, "bütünlük yok"
    # şikayetinin asıl nedeni buydu). İcra dışı yargı türlerinde bu roller
    # hiç oluşmadığından bu satır etkisizdir (no-op).
    baglar = [dt for dt in baglar if dt.rol not in ("alacakli", "borclu", "davaci", "davali")]
    parcalar = [f"{rol_etiket.get(dt.rol, dt.rol.title())}: {dt.taraf}" for dt in baglar]

    sirali = parcalar
    if dosya.birim.yargi_turu == YARGI_TURU_ICRA and len(baglar) > TARAF_SUTUN_SAYISI:
        kendi = _kendi_vekil_adlari()
        bizim_idx = None
        if kendi:
            for i, dt in enumerate(baglar):
                if any(tr_lower(f"{v.ad} {v.soyad}".strip()) in kendi for v in dt.vekiller.all()):
                    bizim_idx = i
                    break

        if bizim_idx is not None:
            kalanlar = [i for i in range(len(parcalar)) if i != bizim_idx]
            sirali = [parcalar[bizim_idx]] + [parcalar[i] for i in kalanlar]

    sutunlar = sirali[:TARAF_SUTUN_SAYISI] + [""] * max(0, TARAF_SUTUN_SAYISI - len(sirali))
    if len(sirali) > TARAF_SUTUN_SAYISI:
        sutunlar[TARAF_SUTUN_SAYISI - 1] = "; ".join(sirali[TARAF_SUTUN_SAYISI - 1:])
    return sutunlar


def mahkeme_secenekleri(yargi_turu=None, yargi_birimi_kod=None):
    """BELİRLİ mahkeme/icra dairesi (`Birim`) seçeneklerini yerel DB'den döner:
    list[{"birimId","ad"}]. 'Yargı Birimi' filtresi mahkeme TÜRÜNÜ süzer (ör.
    'Asliye Hukuk Mahkemesi' — bkz. `yargi_birimleri_getir_veya_db`); bu ise
    aynı türdeki BELİRLİ mahkemeyi süzer (ör. 'ANKARA 4. ASLİYE HUKUK
    MAHKEMESİ') — kullanıcı bulgusu, 2026-07-12: 'yargı türü ve yargı birimi
    var fakat mahkeme ile filtreleme yok'. `yargi_turu`/`yargi_birimi_kod`
    verilirse Birim.yargi_turu/turu2'ye göre daraltılır (Yargı Türü/Yargı
    Birimi seçimine göre kademeli doldurulması için — ekranın diğer
    dropdown'larıyla AYNI desen). DB erişilemezse [] döner."""
    try:
        _django_hazirla()
        from icra_models.models import Birim
        qs = Birim.objects.all()
        if yargi_turu not in (None, ""):
            qs = qs.filter(yargi_turu=int(yargi_turu))
        if yargi_birimi_kod:
            qs = qs.filter(turu2=yargi_birimi_kod)
        return [{"birimId": b.birim_id, "ad": b.ad} for b in qs.order_by("ad")]
    except Exception:
        return []


def taraf_adi_secenekleri(rol, yargi_turu=None):
    """Belirli bir rol (`DosyaTaraf.Rol` kodu, ör. "alacakli"/"borclu") için
    DB'deki BENZERSİZ taraf görünen adlarını (`str(Taraf)`) döner — Alacaklı/
    Borçlu arama kutusunun açılır listesini doldurmak için (kullanıcı isteği:
    serbest metnin yanında var olan isimler de seçilebilsin). `yargi_turu`
    verilirse yalnız o türdeki dosyaların tarafları (bu liste şu an yalnız
    İcra ekranında kullanılıyor). Türkçe harf sırasına göre sıralanır
    (`tr_lower` — bkz. dosyalarim_db_listele'deki aynı gerekçe). DB
    erişilemezse [] döner."""
    try:
        _django_hazirla()
        from icra_models.models import DosyaTaraf
        qs = DosyaTaraf.objects.filter(rol=rol).select_related("taraf")
        if yargi_turu is not None:
            qs = qs.filter(dosya__birim__yargi_turu=int(yargi_turu))
        isimler = {str(dt.taraf) for dt in qs if dt.taraf_id}
        return sorted((i for i in isimler if i), key=tr_lower)
    except Exception:
        return []


def _taraf_isim_eslesir(taraf, aranan_lower):
    """Bir `Taraf`ın ad/soyad/unvanının `aranan_lower` (zaten `tr_lower`
    ile küçültülmüş) metni içerip içermediğini denetler. `dosyalarim_db_
    listele`'nin taraf_adi/alacakli_adi/borclu_adi filtreleri için Python'da
    çalışır — DB (gömülü PostgreSQL, `initdb --locale=C`, bkz. db_baslat.py)
    `icontains`'i Postgres'in yerel `ILIKE`'ına çevirir; `C` yerelinde ILIKE
    yalnız ASCII harfleri büyük/küçük eşler ('İ/I/ı/Ş/ş/Ğ/ğ/Ü/ü/Ö/ö/Ç/ç'
    içeren adlarda DB'de birebir var olan bir isim girilse bile eşleşme
    BULAMIYORDU — kullanıcı bulgusu, 2026-07-12; canlı veriyle DOĞRULANDI:
    gerçek bir borçlunun kendi soyadını küçük harfle arattığında eski
    icontains 0 sonuç, bu fonksiyon 3/3 doğru sonuç verdi). `tr_lower` bu
    harfleri katlayarak karşılaştırdığından büyük/küçük harf hiçbir zaman
    zorunlu değildir."""
    if not aranan_lower:
        return True
    adaylar = (taraf.ad, taraf.soyad, taraf.unvan, f"{taraf.ad} {taraf.soyad}".strip())
    return any(aranan_lower in tr_lower(a) for a in adaylar if a)


def dosyalarim_db_listele(filtreler=None):
    """Yerel DB'deki `Dosya` kayıtlarını (SenkronKapsami'nin doldurduğu)
    isteğe bağlı filtrelerle döner. `filtreler`: {"yargi_turu": int|None,
    "yargi_birimi_kod": str|None, "mahkeme_id": str|None (bkz.
    `mahkeme_secenekleri` — BELİRLİ mahkeme/icra dairesi, `Birim.birim_id`),
    "tur_kod": int|None, "durum_kod": int|None,
    "tarih_baslangic": "GG.AA.YYYY"|None, "tarih_bitis": "GG.AA.YYYY"|None,
    "taraf_adi": str|None (rol'den BAĞIMSIZ, Taraf.ad/soyad/unvan'da Türkçe-
    duyarlı alt-metin araması — Hukuk/Ceza gibi davacı/davalı/sanık rolleri
    için), "alacakli_adi": str|None, "borclu_adi": str|None (rol="alacakli"/
    "borclu" ile SINIRLI aynı arama — İcra dosyalarında belirli tarafa göre
    arama; kullanıcı bulgusu, 2026-07-12: yalnız genel 'Taraf Adı' vardı,
    İcra'da alacaklı/borçlu AYRI ARANAMIYORDU. İkisi birden verilirse AYRI
    taraf_baglari eşleşmeleri aranır (aynı satırda olmaları ŞART DEĞİL) —
    "bu alacaklı VE bu borçlunun birlikte geçtiği dosya" sorgusunu doğal
    olarak destekler. Arama büyük/küçük harf VE Türkçe harf (İ/I/ı/Ş/Ğ/Ü/Ö/Ç)
    duyarsızdır — bkz. `_taraf_isim_eslesir`, kullanıcı bulgusu 2026-07-12:
    DB'de birebir var olan bir ad girilse bile eski SQL icontains eşleşme
    bulamıyordu).}.
    DB erişilemezse [] döner. Her kayıt "dosyaId" içerir ama bu DB'den okunan
    OLABİLİR eski bir değer (bkz. plan §7 Faz 5 "kabul edilmiş bilinmeyen") —
    'Dosya Görüntüle' güncel olmayabileceğini varsayıp hata durumunda
    kullanıcıyı 'Yenile'ye yönlendirmelidir. Her kayıtta ayrıca "taraf1".."taraf{N}"
    (N=`TARAF_SUTUN_SAYISI`, bkz. `_taraflar_sutunlari`) alanları bulunur — her
    biri TEK bir tarafı temsil eder (birleştirilmiş özet metin DEĞİL, kullanıcı
    bulgusu 2026-07-12). Taraf bilgisi yalnız UYAP'tan taraf ayrıntısı çekilmiş
    dosyalarda dolu olur (bkz. `DosyaSorgu.calistir` — artık bulk 'Yenile'
    sırasında da çekilir), henüz çekilmemişse boş kalır (uydurma yok). Ayrıca
    "alacakli"/"borclu" alanları bulunur: rol="alacakli"/"borclu" olan TÜM
    tarafların virgülle birleştirilmiş adları (İcra Dosyalarım'daki
    `icra_core.db_dosyalari_getir` ile AYNI türetme, kullanıcı bulgusu
    2026-07-13: taraf1..4 genel sütunlarında gömülüydü ama tek bakışta
    ayırt edilemiyordu); İcra dışındaki yargı türlerinde bu rol'ler hiç
    oluşmadığından boş kalır. Ayrıca "kesinlesme_durumu": rol="borclu" olan
    DosyaTaraf satırlarının (dolu olanların) görüntü metinleri virgülle
    birleştirilmiş hâli (kullanıcı isteği, 2026-08-03) — kurallar henüz tam
    belirlenmedi (bkz. models.py DosyaTaraf.KesinlesmeDurumu), bu yüzden çoğu
    dosyada boş görünebilir. Ayrıca "tebligat_durumu": DosyaTaraf enum'undan
    DEĞİL (o hiç otomatik yazılmaz), dosya başına EN SON `TebligatBarkod`
    sonucundan (PTT durumu + tarih) türetilir — bkz.
    `_dosya_barkod_ozetlerini_ekle` (kullanıcı bulgusu, 2026-08-04, İKİNCİ
    tur: ilk fix yanlış kaynağı bağlamıştı, sütun de yanlışlıkla
    kaldırılmıştı)."""
    filtreler = filtreler or {}
    try:
        _django_hazirla()
        from django.db.models import Prefetch
        from icra_models.models import Dosya, DosyaTaraf
        qs = Dosya.objects.select_related("birim").all()
        if filtreler.get("yargi_turu") not in (None, ""):
            qs = qs.filter(birim__yargi_turu=int(filtreler["yargi_turu"]))
        if filtreler.get("yargi_birimi_kod"):
            qs = qs.filter(birim__turu2=filtreler["yargi_birimi_kod"])
        if filtreler.get("mahkeme_id"):
            qs = qs.filter(birim__birim_id=filtreler["mahkeme_id"])
        if filtreler.get("tur_kod") not in (None, ""):
            qs = qs.filter(tur_kod=int(filtreler["tur_kod"]))
        if filtreler.get("durum_kod") not in (None, ""):
            qs = qs.filter(durum_kod=int(filtreler["durum_kod"]))
        from datetime import datetime, timedelta
        if filtreler.get("tarih_baslangic"):
            try:
                qs = qs.filter(acilis_tarihi__gte=datetime.strptime(
                    filtreler["tarih_baslangic"], "%d.%m.%Y"))
            except ValueError:
                pass
        if filtreler.get("tarih_bitis"):
            # `acilis_tarihi` saat bilgisi de taşıyan bir DateTimeField —
            # `__lte <bitiş günü> 00:00:00` yazılırsa bitiş gününün kendisinde
            # (saat 00:00 dışında) açılmış HER dosya filtreden dışarıda
            # kalıyordu (kullanıcı bulgusu, 2026-08-04: "açılış tarihi
            # yazıyor ama aralığı filtrelediğimde çıkmıyor"). Bunun yerine
            # bitiş gününün TAMAMINI kapsamak için bir SONRAKİ günün
            # başlangıcından KESİN ÖNCESİ (`__lt`) kullanılır.
            try:
                bitis = datetime.strptime(filtreler["tarih_bitis"], "%d.%m.%Y") + timedelta(days=1)
                qs = qs.filter(acilis_tarihi__lt=bitis)
            except ValueError:
                pass
        # taraf_adi/alacakli_adi/borclu_adi: DB icontains YAZILMAZ — gömülü
        # PostgreSQL `C` yereliyle kurulur (initdb --locale=C, db_baslat.py),
        # bu yerelde Django'nun icontains'i çevirdiği Postgres `ILIKE` yalnız
        # ASCII harfleri büyük/küçük eşler; Türkçe harfli adlarda (İ/I/ı/Ş/ş/
        # Ğ/ğ/Ü/ü/Ö/ö/Ç/ç) DB'de birebir var olan bir isim girilse bile hiç
        # eşleşme BULAMIYORDU (kullanıcı bulgusu, 2026-07-12).
        # Bunun yerine aşağıda, prefetch edilmiş taraf_baglari üzerinde Python'da
        # `_taraf_isim_eslesir` (tr_lower tabanlı) ile filtrelenir — rol ve isim
        # koşulu doğal olarak AYNI DosyaTaraf satırına uygulanır (çok-değerli
        # ilişki join-bölünmesi tuzağı da böylece hiç oluşmaz).
        qs = qs.prefetch_related(
            Prefetch("taraf_baglari",
                     queryset=DosyaTaraf.objects.select_related("taraf").order_by("sira")),
            "taraf_baglari__vekiller",
        ).order_by("-acilis_tarihi")
        taraf_adi_l = tr_lower(filtreler.get("taraf_adi") or "")
        alacakli_l = tr_lower(filtreler.get("alacakli_adi") or "")
        borclu_l = tr_lower(filtreler.get("borclu_adi") or "")
        out = []
        for d in qs:
            baglar = list(d.taraf_baglari.all())
            if taraf_adi_l and not any(_taraf_isim_eslesir(dt.taraf, taraf_adi_l) for dt in baglar):
                continue
            if alacakli_l and not any(dt.rol == "alacakli" and _taraf_isim_eslesir(dt.taraf, alacakli_l) for dt in baglar):
                continue
            if borclu_l and not any(dt.rol == "borclu" and _taraf_isim_eslesir(dt.taraf, borclu_l) for dt in baglar):
                continue
            rec = {
                "dosyaId": d.dosya_id, "birimId": d.birim.birim_id,
                "yargi_turu": d.birim.yargi_turu,
                "yargi_turu_adi": YARGI_TURU_ADI.get(d.birim.yargi_turu, ""),
                "birimAdi": d.birim.ad, "dosyaNo": d.dosya_no,
                "dosyaTurKod": d.tur_kod, "dosyaTur": d.tur,
                "dosyaDurumKod": d.durum_kod, "dosyaDurum": d.durum,
                "acilisTarihi": d.acilis_tarihi.strftime("%d.%m.%Y") if d.acilis_tarihi else "",
                # İcra dosyalarında rol="alacakli"/"borclu" olan tarafların
                # adları — icra_core.db_dosyalari_getir'deki AYNI türetme
                # (bkz. o dosyanın 213-221. satırları); diğer yargı türlerinde
                # bu rol'ler hiç oluşmadığından boş kalır (uydurma yok).
                # İcra Dosyalarım'daki gibi kendi başına sütun olsun diye
                # eklendi — kullanıcı bulgusu, 2026-07-13: taraf1..4 genel
                # sütunlarında Alacaklı/Borçlu gömülü ama tek bakışta
                # ayırt edilemiyordu ("bütünlük yok").
                "alacakli": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "alacakli"),
                "borclu": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "borclu"),
                # Hukuk Dosyalarım (hukuk_dosyalarim.py) için aynı türetme —
                # rol="davaci"/"davali" olmayan yargı türlerinde boş kalır.
                "davaci": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "davaci"),
                "davali": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "davali"),
                # DosyaTaraf.kesinlesme_durumu borçlu bazlı (kullanıcı isteği,
                # 2026-08-03) — aynı dosyada birden fazla borçlu varsa her
                # birinin AYRI değeri olabilir, bu yüzden "alacakli"/"borclu"
                # ile AYNI virgülle-birleştirme deseni kullanılır. Kurallar
                # henüz tam belirlenmediğinden çoğu dosyada boş görünebilir,
                # bu UYDURMA değil beklenen durumdur.
                "kesinlesme_durumu": ", ".join(
                    dt.get_kesinlesme_durumu_display() for dt in baglar
                    if dt.rol == "borclu" and dt.kesinlesme_durumu),
            }
            for i, deger in enumerate(_taraflar_sutunlari(d), start=1):
                rec[f"taraf{i}"] = deger
            out.append(rec)
        # "tebligat_durumu" sütunu (kullanıcı bulgusu, 2026-08-04, İKİNCİ tur:
        # ilk fix YANLIŞ kaynağa — hiç yazılmayan DosyaTaraf.tebligat_durumu
        # enum'una — bağlanmıştı, kullanıcı gerçek barkod verisini istiyordu).
        # Gerçek kaynak `TebligatBarkod` (barkod_sorgu.py'nin yazdığı) — bkz.
        # `_dosya_barkod_ozetlerini_ekle`.
        _dosya_barkod_ozetlerini_ekle(out)
        return out
    except Exception:
        return []


_EVRAK_TARIHI_RE_LISTE = re.compile(r"^(\d{2})[./](\d{2})[./](\d{4})")


def _evrak_tarihi_siralama_anahtari(tarih):
    """barkod_sorgu_panel._evrak_tarihi_anahtari İLE AYNI mantık (o dosyaya
    bağımlılık YOK — dosya_core Tkinter'sız, web sunucusundan da çağrılır):
    'GG/AA/YYYY' ya da 'GG.AA.YYYY' -> sıralanabilir 'YYYYAAGG'. Ayrıştırılamazsa
    boş dize (en eski sayılır)."""
    m = _EVRAK_TARIHI_RE_LISTE.match(str(tarih or "").strip())
    if not m:
        return ""
    gg, aa, yyyy = m.groups()
    return f"{yyyy}{aa}{gg}"


def _dosya_barkod_ozetlerini_ekle(kayitlar):
    """`kayitlar` (dosyalarim_db_listele'nin ürettiği, her biri "dosyaId"
    içeren dosya listesi) içindeki her kayda dosya başına EN SON
    `TebligatBarkod` sonucunu "tebligat_durumu" alanı olarak ekler (kullanıcı
    bulgusu, 2026-08-04, İKİNCİ tur: "hala veritabanında olan tebliğ ...
    durumunu göremiyorum" — Tebliğ Durumu sütunu KALDIRILMIŞTI, kullanıcı geri
    istedi, bu kez GERÇEK barkod verisiyle). "En son" seçimi
    `barkod_sorgu_panel._barkod_ozetini_birlestir` İLE AYNI disiplini kullanır
    (CANLI DB'de doğrulanmış bulgu, 2026-08-03: `sorgu_zamani`'ye göre "ilk
    kayıt en güncel" varsayımı YANLIŞ çıkmıştı — toplu taramada evraklar UYAP'
    tan kronolojik olmayan sırayla gelip DB'ye yazılabiliyor; bunun yerine
    evrak TARİHİ en yeni olan kayıt seçilir). `kayitlar` YERİNDE değiştirilir,
    ayrıca döner."""
    if not kayitlar:
        return kayitlar
    try:
        gecmis = tebligat_barkod_gecmisi_listele()
    except Exception:
        gecmis = []
    ozet = {}
    for g in gecmis:
        did = g.get("dosyaId")
        if did is None:
            continue
        mevcut = ozet.get(did)
        if mevcut is None or (_evrak_tarihi_siralama_anahtari(g.get("evrakTarihi"))
                               > _evrak_tarihi_siralama_anahtari(mevcut.get("evrakTarihi"))):
            ozet[did] = g
    for r in kayitlar:
        g = ozet.get(r.get("dosyaId"))
        if not g:
            r["tebligat_durumu"] = ""
            continue
        metin = g.get("pttDurumu") or ""
        if g.get("sonIslemTarihi"):
            metin = f"{metin} ({g['sonIslemTarihi']})" if metin else g["sonIslemTarihi"]
        r["tebligat_durumu"] = metin
    return kayitlar


# ── Tebligat Barkod Sorgu geçmişi (barkod_sorgu.py'nin yazdığı TebligatBarkod) ──
# "Dosyalarım (Tümü)" ile AYNI kalıcı DB'den okur, AYNI desen (server-side
# kaba filtre yok — tüm kayıtlar döner, ekran kendi içinde sütun-başlığı
# canlı filtre/sıralama uygular, bkz. dosyalarim_genel.py `_yerel_uygula`).
TEBLIGAT_BARKOD_LIMIT = 3000  # tek seferde çekilecek azami satır — makul üst sınır


def tebligat_barkod_gecmisi_listele(limit=TEBLIGAT_BARKOD_LIMIT, dosya_id=None,
                                     birim_id=None, dosya_no=None, dosya_tur_kod=None):
    """`barkod_sorgu.py`'nin (Kapalı Tebligat Barkod Sorgu) yazdığı
    `TebligatBarkod` kayıtlarını, bağlı `Dosya`/`Birim` bilgisiyle birlikte
    en yeniden eskiye döner: list[dict]. DB erişilemezse [] döner. Her kayıt
    dosyalarim_genel'deki KOLONLAR ile aynı ruhta düz sözlük alanları taşır
    (ekran kendi filtre/sıralamasını yerelde uygular).

    `birim_id`+`dosya_no` verilirse DOĞAL ANAHTARLA (birim, yıl, sıra —
    gerekirse `dosya_tur_kod` ile de) filtrelenir — TERCİH EDİLEN yol, çünkü
    `dosya_id` (UYAP'ın 'dosyaId'si) OTURUMA GÖRE DEĞİŞİR (bkz.
    icra_models.ingest.dosya_kunyesi_kaydet yorumu): "Dosyalarım (Tümü)"
    ekranı DB'den okuduğu ESKİ bir dosyaId'yi bellekte tutarken, ARADAN
    başka bir akış (ör. Barkod Sorgu) aynı dosyayı YENİ bir oturumla
    çözüp `Dosya.dosya_id`'yi güncelleyebilir — bu durumda eski dosyaId'yle
    filtrelemek YANLIŞLIKLA BOŞ SONUÇ döner (kullanıcı bulgusu, 2026-08-03:
    barkod sorgulanıp DB'ye kaydedildiği hâlde "Dosya Görüntüle" barkodu
    hiç göstermiyordu). `dosya_id` yalnız `birim_id`/`dosya_no` verilmediğinde
    (veya doğal anahtar çözülemediğinde) GERİYE DÖNÜK uyumluluk için kullanılır.
    "Dosyalarım (Tümü)" ekranındaki "Dosya Görüntüle" penceresinin
    Barkod/Tebligat sekmesi bunu kullanır (bkz. dosyalarim_genel.py)."""
    try:
        _django_hazirla()
        from icra_models.models import TebligatBarkod
        from icra_models.ingest import _yil_sira
        qs = TebligatBarkod.objects.select_related("dosya", "dosya__birim")
        dogal_anahtar_uygulandi = False
        if birim_id and dosya_no:
            yil, sira = _yil_sira(dosya_no)
            if yil:
                qs = qs.filter(dosya__birim__birim_id=str(birim_id),
                                dosya__yil=yil, dosya__sira_no=sira)
                if dosya_tur_kod not in (None, ""):
                    qs = qs.filter(dosya__tur_kod=int(dosya_tur_kod))
                dogal_anahtar_uygulandi = True
        if not dogal_anahtar_uygulandi and dosya_id is not None:
            qs = qs.filter(dosya__dosya_id=dosya_id)
        qs = qs.order_by("-sorgu_zamani")[:limit]
        out = []
        for t in qs:
            d = t.dosya
            out.append({
                "birimAdi": d.birim.ad, "dosyaNo": d.dosya_no,
                "evrakAciklama": t.evrak_aciklama, "evrakTarihi": t.evrak_tarihi,
                "barkod": t.barkod, "tebligatTuru": t.tebligat_turu,
                "elektronikTebligat": "Evet" if t.elektronik_tebligat else "Hayır",
                "pttDurumu": t.ptt_durumu, "sonIslemTarihi": t.son_islem_tarihi,
                "tebligMazbatasiVar": "Var" if t.tebligat_mazbatasi_var else "Yok",
                "tebligMazbatasiAciklama": t.tebligat_mazbatasi_aciklama,
                "kapaliTebligMazbatasiVar": "Var" if t.kapali_tebligat_mazbatasi_var else "Yok",
                "yenidenTebligTalebi": "Var" if t.yeniden_teblig_talep_edildi else "Yok",
                "sorguZamani": t.sorgu_zamani.strftime("%d.%m.%Y %H:%M") if t.sorgu_zamani else "",
                "dosyaId": d.dosya_id, "birimId": d.birim.birim_id,
            })
        return out
    except Exception:
        return []


def dosyalarim_yenile(log_fn=None, yargi_turu=None, yargi_birimi_kod=None, tum_turler=False, kontrol=None):
    """'Yenile' düğmesi: CANLI UYAP taraması yapar (`DosyaSorgu.calistir`,
    kullanıcı isteğiyle HEMEN). `yargi_turu` verilirse SADECE o tür taranır
    (`yargi_birimi_kod` de verilirse SADECE o birim) — EKONOMİK varsayılan
    (kullanıcı bulgusu: eski davranış seçili filtreden bağımsız HER ZAMAN tüm
    türleri/birimleri tarıyordu, pahalıydı). `tum_turler=True` ('Tüm
    Dosyaları Güncelle' ayrı düğmesi) ESKİ davranış: SenkronKapsami'ye
    bakmadan her yargı türünü/birimini tarar (bkz. calistir docstring).
    İkisi de verilmezse `SenkronKapsami`'ye döner (arka plan senkronuyla aynı
    kapsam). Arka plan zamanlayıcısı bu fonksiyonu ÇAĞIRMAZ,
    `DosyaSorgu(...).calistir()`'i doğrudan (tum_turler=False, taraf_da_cek=False
    ile) çağırır — bkz. `senkron_zamanlayici_baslat`. Bu fonksiyon (kullanıcının
    doğrudan tetiklediği 'Yenile'/'Tüm Dosyaları Güncelle') `taraf_da_cek=True`
    verir — sessiz arka plan turunun aksine burada ek istek maliyeti kullanıcının
    o anki bilinçli eylemine bağlı. Döner: (toplam, sonuclar).

    Tarama bittikten sonra `kesinlesme_durumlarini_guncelle` de çağrılır
    (kullanıcı bulgusu, 2026-08-04: "UYAP'tan güncelle dediğimde kesinleşme
    sütunu hala boş") — bu, YENİ bir UYAP isteği atmaz, yalnız daha önce
    Barkod Sorgu ile toplanmış yerel TebligatBarkod verisinden (varsa) o anki
    tarihe göre kesinleşme durumunu yeniden hesaplar (bkz. o fonksiyonun
    docstring'i — özellikle "kesinleşme bekliyor" -> "kesinleşti" geçişi
    yalnız GÜNÜN geçmesiyle tetiklenebildiği için Yenile'nin her çağrısında
    tazelenmesi gerekir). DİKKAT: 'Yenile' BİLEREK barkod sorgusu ÇALIŞTIRMAZ
    (kullanıcı kararı, 2026-08-04: "toplu yenile yaptığımda barkod sorgusu
    yapmasın, yenile SADECE dosya listesini çeksin" — daha önce burada
    otomatik bir Barkod Sorgu tetikleyicisi denenmiş, kullanıcı isteğiyle
    KALDIRILMIŞTIR) — barkod/tebliğ verisi yalnız kullanıcı Barkod Sorgu
    ekranından TOPLU olarak açıkça çalıştırdığında toplanır; burada yalnız
    o ÖNCEDEN toplanmış yerel veri okunur."""
    sorgu = DosyaSorgu(log_fn)
    # DİKKAT: `if yargi_turu:` YAZILMAZ — Ceza'nın kodu 0'dır (models.py Birim
    # yorumu: "0=Ceza,1=Hukuk,2=İcra,...") ve Python'da 0 falsy'dir; `if
    # yargi_turu:` Ceza seçiliyken bu dalı atlayıp yanlışlıkla tüm türleri
    # tarayan alt dala düşürürdü (kullanıcı bulgusu, 2026-07-13'te fark edilen
    # potansiyel hata — `is not None` KESİN 0 dahil tüm kodları kapsar).
    if yargi_turu is not None:
        sonuc = sorgu.calistir(tek_kapsam=(yargi_turu, yargi_birimi_kod or ""), taraf_da_cek=True,
                                kontrol=kontrol)
    else:
        sonuc = sorgu.calistir(tum_turler=tum_turler, taraf_da_cek=True, kontrol=kontrol)
    try:
        kesinlesme_durumlarini_guncelle(log_fn=log_fn)
    except Exception as e:
        (log_fn or (lambda m: None))(f"⚠️ Kesinleşme durumu güncellenemedi: {e}")
    return sonuc


# ── Kesinleşme durumu hesabı (bkz. kesinlesme_core.py) ───────────────────────
# Kullanıcı kararı (AskUserQuestion, 2026-08-04): tek bilinen kural (Örnek-7,
# ilamsız, genel haciz yoluyla takip — 7 günlük itiraz süresi) TÜM "Ödeme
# Emri" tebligatı olan borçlulara uygulanır. UYAP'ta takip türünü (ilamsız/
# kambiyo/ilamlı) güvenilir şekilde ayırt eden canlı doğrulanmış bir alan YOK
# (bkz. IcraTakipDetay.takibin_turu yorumu — kod saklanır ama metin eşlemesi
# "(tahmini)"dir, DB'ye yazılmaz) — kambiyo senedi ile takipte de aynı "Ödeme
# Emri" evrak adı geçebilir ama itiraz süresi 7 gün DEĞİLDİR (5 gündür);
# kullanıcı bu riski bilerek kabul etti. itiraz_edildi_mi (elle işaretlenir)
# hesaplamaya HER ZAMAN önceliklidir.
_TEBLIG_TARIHI_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")


def _teblig_tarihini_coz(deger):
    """barkod_sorgu.py'nin ürettiği 'GG/AA/YYYY[ SS:DD]' biçimindeki Son
    İşlem Tarihi'ni datetime.date'e çevirir; ayrıştırılamazsa None."""
    m = _TEBLIG_TARIHI_RE.match(str(deger or "").strip())
    if not m:
        return None
    gg, aa, yyyy = m.groups()
    try:
        return datetime.date(int(yyyy), int(aa), int(gg))
    except ValueError:
        return None


def _tebligat_gerceklesti_mi(barkod_kaydi):
    """Bu TebligatBarkod satırı GERÇEKTEN teslim/okundu-sayılmış mı —
    "Dağıtımda"/"İade" gibi ara durumlarda kesinleşme süresi BAŞLAMAZ.
    Elektronik tebligatta `son_islem_tarihi` yalnız Kapalı E-Tebliğ Mazbatası
    çözüldüğünde dolar (bkz. barkod_sorgu._e_teblig_haritasini_olustur) — bu
    yüzden dolu olması yeterlidir. Fiziki PTT'de `ptt_durumu` metninde
    "teslim edildi" aranır (canlı doğrulanan tek kesin terim, bkz.
    barkod_sorgu._ptt_durum_ve_tarih)."""
    if barkod_kaydi.elektronik_tebligat:
        return bool(barkod_kaydi.son_islem_tarihi)
    return "teslim edildi" in tr_lower(barkod_kaydi.ptt_durumu or "")


def _borclu_kesinlesme_hesapla(dt, tatiller, bugun, TebligatBarkod, DosyaTarafCls, kc):
    """Tek bir borçlu `DosyaTaraf` satırı için kesinlesme_durumu hesaplar.
    Döner: yeni durum kodu (KesinlesmeDurumu.choices) ya da None (henüz
    hesaplanacak bir şey yok — ör. hiç 'Ödeme Emri' sorgusu yapılmamış veya
    tebligat hâlâ dağıtımda/iade — bu durumda MEVCUT değer dokunulmadan
    kalır)."""
    if dt.itiraz_edildi_mi:
        return DosyaTarafCls.KesinlesmeDurumu.ITIRAZ
    en_yeni_tarih = None
    for k in TebligatBarkod.objects.filter(borclu=dt, tebligat_turu="Ödeme Emri"):
        if not _tebligat_gerceklesti_mi(k):
            continue
        t = _teblig_tarihini_coz(k.son_islem_tarihi)
        if t and (en_yeni_tarih is None or t > en_yeni_tarih):
            en_yeni_tarih = t
    if en_yeni_tarih is None:
        return None
    sonuc = kc.orn7_ilamsiz_genel_haciz_kesinlesme_hesapla(en_yeni_tarih, tatiller)
    if bugun >= sonuc["kesinlesebilir_tarihi"]:
        return DosyaTarafCls.KesinlesmeDurumu.KESINLESTI
    return DosyaTarafCls.KesinlesmeDurumu.KESINLESME_BEKLIYOR


def _tebligat_barkod_borclu_geriye_donuk_doldur(DosyaTaraf, TebligatBarkod, dosya_obj, log):
    """`TebligatBarkod.borclu` alanı (ve bunu dolduran barkod_sorgu._borclu_coz_tekil)
    bu FK eklenmeden ÖNCE çalışan barkod sorgularının satırlarında NULL kalmıştı
    (kullanıcı bulgusu, 2026-08-04: "barkod bilgisini zaten çekmiştim, kesinleşme
    neden boş" — veri DB'de VARDI, yalnız borçluya bağlanmamıştı). Aynı kural
    (`_borclu_coz_tekil` ile AYNI): yalnız dosyada TEK borçlu varsa geriye dönük
    bağlanır, çok borçlulu dosyada dokunulmaz. Döner: doldurulan kayıt sayısı."""
    qs = TebligatBarkod.objects.filter(borclu__isnull=True).select_related("dosya")
    if dosya_obj is not None:
        qs = qs.filter(dosya=dosya_obj)
    doldurulan = 0
    for tb in qs:
        borclular = list(DosyaTaraf.objects.filter(dosya=tb.dosya, rol="borclu"))
        if len(borclular) == 1:
            tb.borclu = borclular[0]
            tb.save(update_fields=["borclu"])
            doldurulan += 1
    if doldurulan:
        log(f"… geriye dönük borçlu eşlemesi: {doldurulan} barkod kaydı bağlandı.")
    return doldurulan


def kesinlesme_durumlarini_guncelle(dosya_obj=None, log_fn=None):
    """rol=borclu `DosyaTaraf` satırları için kesinlesme_durumu'nu YEREL
    veritabanındaki `TebligatBarkod` kayıtlarından hesaplar ve yazar — UYAP'a
    YENİ istek ATMAZ (bkz. `_borclu_kesinlesme_hesapla`). Önce
    `_tebligat_barkod_borclu_geriye_donuk_doldur` ile ESKİ (borclu FK'sinden
    önce yazılmış) barkod kayıtlarını da bağlar, bu yüzden daha önce çekilmiş
    barkod verisi de hesaba dahil olur. `dosya_obj` verilirse yalnız o
    dosyanın borçluları güncellenir (barkod_sorgu.py bir dosyayı işleyip
    bitirdiğinde çağırır); verilmezse (Yenile akışı) TebligatBarkod'u olan
    TÜM borçlular taranır. Döner: güncellenen satır sayısı; DB/modül
    hazırlanamazsa (ör. PostgreSQL kapalı) sessizce 0."""
    log = log_fn or (lambda *a, **k: None)
    try:
        _django_hazirla()
        from icra_models.models import DosyaTaraf, TebligatBarkod, ResmiTatil
        import kesinlesme_core as kc
    except Exception as e:
        log(f"⚠️ Kesinleşme hesabı için DB/modül hazırlanamadı: {e}")
        return 0

    _tebligat_barkod_borclu_geriye_donuk_doldur(DosyaTaraf, TebligatBarkod, dosya_obj, log)

    tatiller = set(ResmiTatil.objects.values_list("tarih", flat=True))
    bugun = datetime.date.today()

    qs = DosyaTaraf.objects.filter(rol="borclu")
    if dosya_obj is not None:
        qs = qs.filter(dosya=dosya_obj)
    else:
        qs = qs.filter(dosya__tebligat_barkodlari__isnull=False).distinct()

    guncellenen = 0
    for dt in qs:
        yeni = _borclu_kesinlesme_hesapla(dt, tatiller, bugun, TebligatBarkod, DosyaTaraf, kc)
        if yeni is not None and yeni != dt.kesinlesme_durumu:
            dt.kesinlesme_durumu = yeni
            dt.save(update_fields=["kesinlesme_durumu"])
            guncellenen += 1
    if guncellenen:
        log(f"… kesinleşme durumu güncellendi: {guncellenen} borçlu satırı.")
    return guncellenen


# ── Arka plan zamanlayıcı — hem Tkinter hem web SÜREÇ BAŞINA bir kez çağırır ──
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 "Kalan" — otomatik senkron döngüsü)
# Tek ortak uygulama burada: iki arayüz de kendi sürecinde bu fonksiyonu bir kez
# çağırır, döngü mantığı İKİ KEZ YAZILMAZ. UYAP oturumu/proxy (127.0.0.1:8800)
# veya DB o an erişilemezse bir tur sessizce loglanıp bir sonraki aralıkta
# yeniden denenir — _load_auth()/boot_autoconnect() gibi YENİ bir oturum AÇMAZ,
# yalnızca zaten var olan bağlantıyı kullanır (bkz. bellek:
# server-load-auth-canli-giris-tuzagi — bu fonksiyon o riski TAŞIMAZ).
VARSAYILAN_ARALIK_SANIYE = 1800  # 30 dakika

_zamanlayici_thread = None
_zamanlayici_dur = None


def senkron_zamanlayici_baslat(interval_saniye=None, log_fn=None):
    """Arka planda periyodik `DosyaSorgu.calistir()` döngüsü başlatır (daemon
    thread). Süreç başına yalnız bir kez gerçekten başlar; art arda çağrılar
    (ör. iki panel aynı süreçte dosya_core'u kullanıyorsa) no-op'tur — çalışan
    thread'i döner. `SenkronKapsami` boşsa `calistir()` zaten hızlıca 0,[]
    dönüp bir sonraki aralığı bekler; bu normaldir, hata değildir.

    İSTEMCİ modunda (bkz. `bu_makine_istemci_mi`) HİÇ başlamaz — bu makine
    ofisin UYAP oturumunu tutan sunucu değil, o yüzden kendi başına UYAP'ı
    taramamalı (kullanıcı bulgusu, 2026-07-13). Hem `panel.py`'nin hem
    `web/server.py`'nin çağrı noktası bu TEK fonksiyonu kullandığından bu
    kontrol ikisini birden kapsar."""
    if bu_makine_istemci_mi():
        (log_fn or (lambda m: None))(
            "… bu makine 'İstemci' modunda (Ayarlar > Bağlantı) — yerel "
            "veritabanı/arka plan senkronu başlatılmadı, dosyalar ofis "
            "sunucusunun web panelinden görüntülenmeli.")
        return None
    global _zamanlayici_thread, _zamanlayici_dur
    if _zamanlayici_thread is not None and _zamanlayici_thread.is_alive():
        return _zamanlayici_thread
    aralik = interval_saniye or VARSAYILAN_ARALIK_SANIYE
    log = log_fn or (lambda m: None)
    durdur = threading.Event()
    _zamanlayici_dur = durdur

    def _dongu():
        log(f"… otomatik senkron zamanlayıcı başladı ({aralik} sn aralıkla).")
        while not durdur.is_set():
            try:
                # Düşük öncelikli geçit: kullanıcının o anki tıklaması (Dosya
                # Görüntüle/Yenile) HER TEK UYAP isteğinden önce araya girebilsin
                # diye — bkz. _arka_plan_istek/_bireysel_istek docstring'i.
                toplam, sonuclar = DosyaSorgu(log, istek_sarici=_arka_plan_istek).calistir()
                if sonuclar:
                    log(f"✔ Otomatik senkron turu: {toplam} kayıt tarandı "
                        f"({len(sonuclar)} kapsam).")
            except Exception as e:
                log(f"⚠️ Otomatik senkron turu başarısız (bir sonraki aralıkta "
                    f"yeniden denenecek): {e}")
            durdur.wait(aralik)
        log("… otomatik senkron zamanlayıcı durduruldu.")

    _zamanlayici_thread = threading.Thread(target=_dongu, daemon=True)
    _zamanlayici_thread.start()
    return _zamanlayici_thread


def senkron_zamanlayici_durdur():
    """Çalışan zamanlayıcıyı (varsa) bir sonraki uyanışında nazikçe durdurur."""
    if _zamanlayici_dur is not None:
        _zamanlayici_dur.set()
