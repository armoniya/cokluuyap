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

import os
import sys
import threading

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


def _django_hazirla():
    """models/ dizinini path'e ekler ve Django'yu kurar (bir kez, önbellekli
    olmadan — çağıran zaten kendi önbelleğini tutuyorsa gerek yok)."""
    mdir = os.path.normpath(os.path.join(_HERE, "..", "..", "models"))
    if mdir not in sys.path:
        sys.path.insert(0, mdir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
    import django
    django.setup()


def yargi_birimleri_getir(yargi_turu, log_fn=None):
    """Bir yargı türü için Yargı Birimi (mahkeme türü) listesini UYAP'tan
    çeker (`yargiBirimleriSorgula_brd.ajx`, payload {"yargiTuru": kod}) ve
    `YargiBirimi` tablosuna upsert eder. Döner: list[{"kod","ad"}].

    Ağ hatasında ya da DB erişilemezse [] döner (çağıran eldeki DB önbelleğini
    `yargi_birimleri_db_den_yukle` ile ayrıca okuyabilir)."""
    motor = SorguMotoru(log_fn or (lambda *a, **k: None))
    try:
        _status, veri = motor._post(YARGI_BIRIMI_ENDPOINT, {"yargiTuru": str(yargi_turu)})
    except Exception:
        return []
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


def birim_listesi_getir_genel(yargi_turu, yargi_birimi_kod, log_fn=None):
    """`icra_core.birim_listesi_getir`'in genellenmiş hâli — sabit
    yargiTuru=2/yargiBirimi=1101 yerine parametre alır. Önbelleksiz (her
    çağrıda UYAP'a gider); çağıran isterse kendi önbelleğini tutar. Döner:
    list[{"birimAdi","birimId"}]."""
    motor = SorguMotoru(log_fn or (lambda *a, **k: None))
    payload = {"yargiTuru": str(yargi_turu), "yargiBirimi": yargi_birimi_kod, "dosyaKapaliMi": False}
    try:
        _status, veri = motor._post(BIRIM_LISTE_ENDPOINT, payload)
    except Exception:
        return []
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


class DosyaSorgu:
    """Çoklu yargı türü/birimi dosya sorgusu (headless). `SenkronKapsami`'de
    işaretli her (yargı türü, yargı birimi) kombinasyonu için
    `search_phrase_detayli.ajx`'i kendi birimTuru2/3 değerleriyle çağırır.
    Aktarım ve taraf/kayıt işleme mantığı `icra_core`'dan İTHAL EDİLİR."""

    def __init__(self, log_fn=None):
        self.log_fn = log_fn or (lambda m: None)

    def _bir_kapsami_ara(self, motor, yargi_turu, yargi_birimi_kod, values, durum_kod):
        """Tek bir (yargi_turu, yargi_birimi_kod) için sayfalanmış arama.
        Döner: list[dict] (ham UYAP kayıtları, dosyaId ile bu kapsam içinde
        tekilleştirilmiş)."""
        tum = []
        gorulen = set()
        variantlar = taraf_variantlari(values) or [None]
        for variant in variantlar:
            sayfa = 1
            while True:
                payload = build_payload_genel(values, yargi_turu, yargi_birimi_kod, durum_kod, variant)
                payload["pageNumber"] = sayfa
                _status, veri = motor._post(ENDPOINT, payload)
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
                    birimler = yargi_birimleri_getir(yargi_turu, self.log_fn)
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

    def calistir(self, values=None, durum_kod=0):
        """`SenkronKapsami`'deki her aktif kapsamı sırayla arar, kaydeder.
        Döner: (kayit_sayisi, kapsam_sonuclari:list[dict]). `values` boşsa
        (varsayılan) kapsam-genişleticiler dışında ekstra filtre uygulanmaz."""
        values = values or {}
        motor = SorguMotoru(self.log_fn)
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
        sonuclar = []
        for yargi_turu, yargi_birimi_kod, ad in gorevler:
            self.log_fn(f"… sorgulanıyor: {ad} / {yargi_birimi_kod}")
            try:
                kayitlar = self._bir_kapsami_ara(motor, yargi_turu, yargi_birimi_kod, values, durum_kod)
            except OturumHatasi:
                raise
            except Exception as e:
                self.log_fn(f"⚠️ {ad}/{yargi_birimi_kod} sorgusu başarısız: {e}")
                continue

            kaydedilen = 0
            if db_available:
                for rec in kayitlar:
                    if not rec.get("dosyaId"):
                        continue
                    try:
                        dosya_kunyesi_kaydet(rec, yargi_turu=yargi_turu)
                        kaydedilen += 1
                    except Exception as e:
                        self.log_fn(f"⚠️ Kayıt atlandı ({rec.get('dosyaNo','')}): {e}")

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


def dosya_ayrinti_getir(dosya_id, log_fn=None):
    """`dosyaAyrintiBilgileri_brd.ajx` çağrısı. `dosya_id`, çağrıldığı ANKİ
    arama oturumunun TAZE 'dosyaId' değeri olmalı (bkz. models.Dosya
    docstring'i — dosyaId oturumluktur, DB'den eski bir değer okunup buraya
    verilmemeli). Ağ hatasında ya da beklenmeyen yanıt şeklinde {} döner."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        _status, veri = motor._post(DOSYA_AYRINTI_ENDPOINT, {"dosyaId": str(dosya_id)})
    except Exception as e:
        log(f"⚠️ Dosya ayrıntısı alınamadı: {e}")
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


def dosya_taraf_getir(dosya_id, log_fn=None):
    """`dosya_taraf_bilgileri_brd.ajx` çağrısı. `dosya_id` TAZE olmalı (bkz.
    `dosya_ayrinti_getir` docstring'i — aynı oturumluk kısıt). Ağ hatasında
    ya da beklenmeyen yanıt şeklinde [] döner."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        _status, veri = motor._post(TARAF_BILGILERI_ENDPOINT, {"dosyaId": str(dosya_id)})
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


def _vekil_kaydet(vekil_ham, Vekil, log_fn=None):
    """`item['vekil']` (ör. "[HAKAN TOLUNAY BURHAN]") ayrıştırıp dedup ederek
    `Vekil` nesnesi döner; yoksa None. TCKN/baro yok — yalnız ad+soyad
    eşleşmesiyle dedup edilir (bu uç nokta başka kimlik vermiyor)."""
    log = log_fn or (lambda m: None)
    metin = str(vekil_ham or "").strip()
    if metin.startswith("[") and metin.endswith("]"):
        metin = metin[1:-1].strip()
    if not metin:
        return None
    isimler = [p.strip() for p in metin.split(",") if p.strip()]
    if len(isimler) > 1:
        log(f"… birden fazla vekil bulundu {isimler}, yalnız ilki kaydediliyor "
            "(DosyaTaraf.vekil tekil alan, çoklu-vekil durumu canlı doğrulanmadı).")
    isim = isimler[0] if isimler else metin
    parcalar = isim.rsplit(None, 1)
    ad, soyad = (parcalar[0], parcalar[1]) if len(parcalar) == 2 else (isim, "")
    if not ad:
        return None
    vekil = Vekil.objects.filter(ad=ad, soyad=soyad).first()
    return vekil or Vekil.objects.create(ad=ad, soyad=soyad)


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
            taraf_obj = save_taraf(info, Taraf)
            vekil_obj = _vekil_kaydet(item.get("vekil"), Vekil, log)
            rol_kod = tr_lower(item.get("rol", "")) or "diger"
            DosyaTaraf.objects.update_or_create(
                dosya=dosya, taraf=taraf_obj, rol=rol_kod,
                defaults={"sira": idx, "vekil": vekil_obj})
            sayac += 1
    return sayac


def dosya_detay_goster_ve_kaydet(rec, log_fn=None):
    """Tek giriş noktası (UI'ların çağıracağı): ham arama kaydından (`rec` —
    `search_phrase_detayli.ajx` satırı, TAZE 'dosyaId' içermeli) ayrıntı VE
    taraf bilgilerini çeker, ilgili `Dosya`'yı doğal anahtarıyla
    (birim+yıl+sıra+tür) bulur ve kaydeder. Döner: (ham:dict, aile:str|None,
    kaydedildi:bool, hata:str|None, taraflar:list) — `hata` doluysa
    `ham`/`aile` boş/None olabilir, UI kullanıcıya `hata`yı göstermeli.
    `taraflar`, `dosya_taraf_getir`'in ham (UYAP şekilli) listesidir; ayrıntı
    başarısız olsa bile boş liste olarak döner (asla None)."""
    log = log_fn or (lambda m: None)
    dosya_id = str((rec or {}).get("dosyaId", "") or "")
    if not dosya_id:
        return {}, None, False, ("Bu kayıtta 'dosyaId' yok (DB önbelleğinden "
                                  "geliyor olabilir) — listeyi yenileyip tekrar deneyin."), []
    ham = dosya_ayrinti_getir(dosya_id, log)
    if not ham:
        return {}, None, False, "Dosya ayrıntısı alınamadı (bağlantı/oturum sorunu olabilir).", []
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
        return ham, None, False, (f"Dosya yerel veritabanında bulunamadı (önce "
                                   f"Sorgula/senkron ile kaydedilmeli olabilir): {e}"), []
    aile, kaydedildi = dosya_ayrinti_kaydet(dosya, ham, log)
    taraflar = dosya_taraf_getir(dosya_id, log)
    if taraflar:
        try:
            dosya_taraf_kaydet(dosya, taraflar, log)
        except Exception as e:
            log(f"⚠️ Taraf bilgileri kaydedilemedi: {e}")
    return ham, aile, kaydedildi, None, taraflar


# ── Genel "Dosyalarım" (çoklu yargı türü) — DB tarama + filtre ──────────────
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 "Kalan" — filtre UI)
# İcra Dosyalarım'ın aksine bu ekran CANLI UYAP sorgusu yapmaz (yalnız DB
# okur) — SenkronKapsami'nin (Faz 3/4) zaten doldurduğu veriyi yargı
# türü/birimi/dosya türü/durum/tarih aralığına göre tarar. Tazelemek isteyen
# kullanıcı "Yenile" ile `dosyalarim_yenile` (= DosyaSorgu.calistir, arka plan
# zamanlayıcısıyla AYNI mantık, yalnız HEMEN) çağırır, sonra DB yeniden okunur.
def dosya_tur_secenekleri():
    """`Dosya.Tur` (models.py) seçeneklerini döner: list[(kod, ad)]."""
    _django_hazirla()
    from icra_models.models import Dosya
    return [(v, l) for v, l in Dosya.Tur.choices]


def dosya_durum_secenekleri():
    """`Dosya.Durum` (models.py) seçeneklerini döner: list[(kod, ad)]."""
    _django_hazirla()
    from icra_models.models import Dosya
    return [(v, l) for v, l in Dosya.Durum.choices]


def dosyalarim_db_listele(filtreler=None):
    """Yerel DB'deki `Dosya` kayıtlarını (SenkronKapsami'nin doldurduğu)
    isteğe bağlı filtrelerle döner. `filtreler`: {"yargi_turu": int|None,
    "yargi_birimi_kod": str|None, "tur_kod": int|None, "durum_kod": int|None,
    "tarih_baslangic": "GG.AA.YYYY"|None, "tarih_bitis": "GG.AA.YYYY"|None}.
    DB erişilemezse [] döner. Her kayıt "dosyaId" içerir ama bu DB'den okunan
    OLABİLİR eski bir değer (bkz. plan §7 Faz 5 "kabul edilmiş bilinmeyen") —
    'Dosya Görüntüle' güncel olmayabileceğini varsayıp hata durumunda
    kullanıcıyı 'Yenile'ye yönlendirmelidir."""
    filtreler = filtreler or {}
    try:
        _django_hazirla()
        from icra_models.models import Dosya
        qs = Dosya.objects.select_related("birim").all()
        if filtreler.get("yargi_turu") not in (None, ""):
            qs = qs.filter(birim__yargi_turu=int(filtreler["yargi_turu"]))
        if filtreler.get("yargi_birimi_kod"):
            qs = qs.filter(birim__turu2=filtreler["yargi_birimi_kod"])
        if filtreler.get("tur_kod") not in (None, ""):
            qs = qs.filter(tur_kod=int(filtreler["tur_kod"]))
        if filtreler.get("durum_kod") not in (None, ""):
            qs = qs.filter(durum_kod=int(filtreler["durum_kod"]))
        from datetime import datetime
        if filtreler.get("tarih_baslangic"):
            try:
                qs = qs.filter(acilis_tarihi__gte=datetime.strptime(
                    filtreler["tarih_baslangic"], "%d.%m.%Y"))
            except ValueError:
                pass
        if filtreler.get("tarih_bitis"):
            try:
                qs = qs.filter(acilis_tarihi__lte=datetime.strptime(
                    filtreler["tarih_bitis"], "%d.%m.%Y"))
            except ValueError:
                pass
        qs = qs.order_by("-acilis_tarihi")[:2000]
        out = []
        for d in qs:
            out.append({
                "dosyaId": d.dosya_id, "birimId": d.birim.birim_id,
                "yargi_turu": d.birim.yargi_turu,
                "yargi_turu_adi": YARGI_TURU_ADI.get(d.birim.yargi_turu, ""),
                "birimAdi": d.birim.ad, "dosyaNo": d.dosya_no,
                "dosyaTurKod": d.tur_kod, "dosyaTur": d.tur,
                "dosyaDurumKod": d.durum_kod, "dosyaDurum": d.durum,
                "acilisTarihi": d.acilis_tarihi.strftime("%d.%m.%Y") if d.acilis_tarihi else "",
            })
        return out
    except Exception:
        return []


def dosyalarim_yenile(log_fn=None):
    """'Yenile' düğmesi: aktif `SenkronKapsami`'ye göre CANLI UYAP taraması
    yapar (`DosyaSorgu.calistir` — arka plan zamanlayıcısıyla AYNI mantık,
    kullanıcı isteğiyle HEMEN). Döner: (toplam, sonuclar) — bkz. calistir."""
    return DosyaSorgu(log_fn).calistir()


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
    dönüp bir sonraki aralığı bekler; bu normaldir, hata değildir."""
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
                toplam, sonuclar = DosyaSorgu(log).calistir()
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
