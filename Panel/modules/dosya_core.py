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
    """Aktif `SenkronKapsami` satırlarını döndürür: list[(yargi_turu,
    yargi_birimi_kod)]. yargi_birimi_kod boşsa o yargı türünün TAMAMI demektir
    — bu durumda önce `YargiBirimi`'den o türün kayıtlı tüm kodları genişletilir
    (bootstrap'ın önceden çalışmış olması gerekir; yoksa o tür atlanır ve
    log_fn ile bildirilir — çağıran `DosyaSorgu.calistir` bunu yapar)."""
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

        try:
            _django_hazirla()
            from icra_models.ingest import dosya_kunyesi_kaydet
            db_available = True
        except Exception as e:
            self.log_fn(f"⚠️ Yerel veritabanı aktif değil: {e}")
            db_available = False

        toplam = 0
        sonuclar = []
        for yargi_turu, yargi_birimi_kod in kapsamlar:
            ad = YARGI_TURU_ADI.get(yargi_turu, str(yargi_turu))
            if not yargi_birimi_kod:
                self.log_fn(f"⚠️ '{ad}' için yargı birimi belirtilmemiş (tüm birimler) — "
                             "henüz desteklenmiyor, bu kapsam atlandı. Belirli bir yargı "
                             "birimi seçin.")
                continue
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
