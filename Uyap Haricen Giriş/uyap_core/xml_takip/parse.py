# -*- coding: utf-8 -*-
"""
uyap_core.xml_takip.parse — UYAP "exchangeData" XML'ini (İcra Takip Açılış - XML,
Kota Tipi: Banka Dosyası) ayrıştırır
====================================================================================
Örnek (kullanıcının canlı ihracından, 2026-07-28): <exchangeData><dosyalar><dosya .../>
...</dosyalar></exchangeData>. Bir XML BİRDEN FAZLA <dosya> (= birden fazla takip)
içerebilir — MTS'nin Excel'i gibi, her <dosya> tek bir icra takibi açma isteğidir.

Öznitelik adları UYAP'ın kendi alan adlandırmasıyla (alacakKalemKod, faizTipKod,
mahiyetKodu, takipTuru/Yolu/Sekli...) BİREBİR örtüşüyor — bu değerler
uyap_core.xml_takip.takip.prepare() içinde canlı referans uç noktalarıyla
(icra_takip_turu.ajx, icraTakipAlacakGirisBilgileri.ajx, mtsDosyaKriterleri_brd.ajx…)
doğrulanır; burada SADECE ham XML'i Python nesnelerine çevirir, UYAP'a dokunmaz.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


# ── Veri modelleri ──────────────────────────────────────────────────────────
@dataclass
class VekilBilgisi:
    tckn: str = ""
    ad: str = ""
    soyad: str = ""
    baro_no: str = ""
    buro_adi: str = ""
    vergi_no: str = ""


@dataclass
class AdresBilgisi:
    """<taraf>/<kisiKurumBilgileri>/<adres> — UYAP'a AYNEN gönderilir (canlı
    doğrulanan gerçek bir kayıtta bu şekilde: yalnızca XML'de var olan alanlar
    doludur, kalanı null gider — kurum sorgusundan ZENGİNLEŞTİRİLMEZ)."""
    adres: str = ""
    adres_turu: str = ""
    il: str = None
    ilce: str = None
    il_kodu: str = None
    ilce_kodu: str = None
    posta_kodu: str = None


@dataclass
class TarafBilgisi:
    id: str
    tur: str                # "KURUM" | "KISI"
    ad: str
    rol: str                # "ALACAKLI" | "BORÇLU" (XML metni)
    rol_id: int
    tckn: str = ""
    mersis_no: str = ""
    vergi_no: str = ""
    iban: str = ""
    adres: AdresBilgisi = None
    baba_adi: str = None
    ana_adi: str = None
    dogum_tarihi: str = None
    cinsiyet: str = None
    kisi_adi: str = None      # yalnız KISI: kisiTumBilgileri/@adi (ad'ı ayrıştırmadan)
    kisi_soyadi: str = None   # yalnız KISI: kisiTumBilgileri/@soyadi


@dataclass
class FaizBilgisi:
    tip_kod: str = ""
    tip_aciklama: str = ""
    oran: float = 0.0
    baslangic_tarihi: str = ""
    sure_tip: str = "2"      # UYAP referans listesinde "2" = Yıllık


@dataclass
class AlacakKalemi:
    id: str
    kod: int                 # alacakKalemKod (icraTakipAlacakGirisBilgileri.ajx ile doğrulanır)
    kod_turu: int
    ad: str                  # alacakKalemAdi
    kod_aciklama: str        # alacakKalemKodAciklama
    tutar: float
    ilk_tutar: float
    faiz: FaizBilgisi = None
    taraf_idler: list = field(default_factory=list)


@dataclass
class DigerAlacak:
    id: str
    tutar: float
    tarih: str
    odeme_tarihi: str
    aciklama: str             # digerAlacakAciklama
    kalemler: list = field(default_factory=list)   # [AlacakKalemi, ...]


@dataclass
class Dosya:
    dosya_turu: int
    takip_turu: int
    takip_yolu: int
    takip_sekli: int
    bk84_madde: bool
    bsmv: bool
    kkdf: bool
    talep_edilen_hak: str      # alacaklininTalepEttigiHak — "1/4" açıklamasının kaynağı
    aciklama_48_9: str         # aciklama48e9 — takip yolu açıklaması ("HACİZ" vb.)
    dosya_tipi: str
    mahiyet_kodu: int
    dosya_belirleyicisi: str   # XML'in kendi referansı (dosyaId DEĞİL — UYAP'tan gelmez)
    vekil: VekilBilgisi = None
    taraflar: list = field(default_factory=list)     # [TarafBilgisi, ...]
    alacaklar: list = field(default_factory=list)    # [DigerAlacak, ...]


# ── yardımcılar ──────────────────────────────────────────────────────────────
def _s(el, ad, varsayilan=""):
    return (el.get(ad) if el is not None else None) or varsayilan


def _bool(el, ad):
    """UYAP'ın E/H (Evet/Hayır) bayraklarını Python bool'a çevirir."""
    return _s(el, ad).strip().upper() == "E"


def _int(el, ad, varsayilan=0):
    try:
        return int(str(_s(el, ad, varsayilan)).strip())
    except (TypeError, ValueError):
        return varsayilan


def _float(el, ad, varsayilan=0.0):
    try:
        return float(str(_s(el, ad, varsayilan)).strip())
    except (TypeError, ValueError):
        return varsayilan


def _vekil_oku(vekil_kisi_el):
    if vekil_kisi_el is None:
        return None
    vekil_el = vekil_kisi_el.find("vekil")
    kisi_el = vekil_kisi_el.find("kisiTumBilgileri")
    return VekilBilgisi(
        tckn=_s(kisi_el, "tcKimlikNo"),
        ad=_s(kisi_el, "adi"),
        soyad=_s(kisi_el, "soyadi"),
        baro_no=_s(vekil_el, "baroNo"),
        buro_adi=_s(vekil_el, "avukatlikBuroAdi"),
        vergi_no=_s(vekil_el, "vergiNo"),
    )


def _adres_oku(adres_el):
    if adres_el is None:
        return None
    return AdresBilgisi(
        adres=_s(adres_el, "adres"), adres_turu=_s(adres_el, "adresTuru"),
        il=adres_el.get("il"), ilce=adres_el.get("ilce"),
        il_kodu=adres_el.get("ilKodu"), ilce_kodu=adres_el.get("ilceKodu"),
        posta_kodu=adres_el.get("postaKodu"),
    )


def _taraf_oku(taraf_el):
    kkb = taraf_el.find("kisiKurumBilgileri")
    if kkb is None:
        raise ValueError(f"taraf id={taraf_el.get('id')!r}: kisiKurumBilgileri eksik.")
    kurum_el = kkb.find("kurum")
    kisi_el = kkb.find("kisiTumBilgileri")
    rol_el = taraf_el.find("rolTur")
    if rol_el is None:
        raise ValueError(f"taraf id={taraf_el.get('id')!r}: rolTur eksik.")
    iban_el = taraf_el.find("iban")
    baba_adi = ana_adi = dogum_tarihi = cinsiyet = kisi_adi = kisi_soyadi = None

    if kurum_el is not None:
        tur = "KURUM"
        mersis_no = _s(kurum_el, "mersisNo")
        vergi_no = _s(kurum_el, "vergiNo")
        tckn = ""
    elif kisi_el is not None:
        tur = "KISI"
        mersis_no = ""
        vergi_no = ""
        tckn = _s(kisi_el, "tcKimlikNo")
        baba_adi = kisi_el.get("babaAdi")
        ana_adi = kisi_el.get("anaAdi")
        dogum_tarihi = kisi_el.get("dogumTarihi") or kisi_el.get("dogumTarihiStr")
        cinsiyet = kisi_el.get("cinsiyeti")
        kisi_adi = kisi_el.get("adi")
        kisi_soyadi = kisi_el.get("soyadi")
    else:
        raise ValueError(f"taraf id={taraf_el.get('id')!r}: ne kurum ne kişi bilgisi var.")

    return TarafBilgisi(
        id=taraf_el.get("id"), tur=tur, ad=_s(kkb, "ad"),
        rol=_s(rol_el, "Rol"), rol_id=_int(rol_el, "rolID"),
        tckn=tckn, mersis_no=mersis_no, vergi_no=vergi_no,
        iban=_s(iban_el, "no"), adres=_adres_oku(kkb.find("adres")),
        baba_adi=baba_adi, ana_adi=ana_adi, dogum_tarihi=dogum_tarihi, cinsiyet=cinsiyet,
        kisi_adi=kisi_adi, kisi_soyadi=kisi_soyadi,
    )


def _faiz_oku(faiz_el):
    if faiz_el is None:
        return None
    return FaizBilgisi(
        tip_kod=_s(faiz_el, "faizTipKod"), tip_aciklama=_s(faiz_el, "faizTipKodAciklama"),
        oran=_float(faiz_el, "faizOran"), baslangic_tarihi=_s(faiz_el, "baslangicTarihi"),
        sure_tip=_s(faiz_el, "faizSureTip", "2"),
    )


def _alacak_kalemi_oku(ak_el):
    taraf_idler = [r.get("id") for r in ak_el.findall("ref") if r.get("to") == "taraf" and r.get("id")]
    tutar = _float(ak_el, "alacakKalemTutar")
    return AlacakKalemi(
        id=ak_el.get("id"), kod=_int(ak_el, "alacakKalemKod"), kod_turu=_int(ak_el, "alacakKalemKodTuru"),
        ad=_s(ak_el, "alacakKalemAdi"), kod_aciklama=_s(ak_el, "alacakKalemKodAciklama"),
        tutar=tutar, ilk_tutar=_float(ak_el, "alacakKalemIlkTutar", tutar),
        faiz=_faiz_oku(ak_el.find("faiz")), taraf_idler=taraf_idler,
    )


def _diger_alacak_oku(da_el):
    kalemler = [_alacak_kalemi_oku(ak) for ak in da_el.findall("alacakKalemi")]
    return DigerAlacak(
        id=da_el.get("id"), tutar=_float(da_el, "tutar"), tarih=_s(da_el, "tarih"),
        odeme_tarihi=_s(da_el, "odemeTarihi"), aciklama=_s(da_el, "digerAlacakAciklama"),
        kalemler=kalemler,
    )


def _dosya_oku(dosya_el):
    taraflar = [_taraf_oku(t) for t in dosya_el.findall("taraf")]
    if not taraflar:
        raise ValueError(f"dosya {dosya_el.get('dosyaBelirleyicisi')!r}: hiç taraf yok.")
    alacaklar = [_diger_alacak_oku(da) for da in dosya_el.findall("digerAlacak")]
    if not alacaklar:
        raise ValueError(f"dosya {dosya_el.get('dosyaBelirleyicisi')!r}: hiç alacak (digerAlacak) yok.")

    return Dosya(
        dosya_turu=_int(dosya_el, "dosyaTuru"), takip_turu=_int(dosya_el, "takipTuru"),
        takip_yolu=_int(dosya_el, "takipYolu"), takip_sekli=_int(dosya_el, "takipSekli"),
        bk84_madde=_bool(dosya_el, "BK84MaddeUygulansin"), bsmv=_bool(dosya_el, "BSMVUygulansin"),
        kkdf=_bool(dosya_el, "KKDFUygulansin"),
        talep_edilen_hak=_s(dosya_el, "alacaklininTalepEttigiHak"),
        aciklama_48_9=_s(dosya_el, "aciklama48e9"), dosya_tipi=_s(dosya_el, "dosyaTipi"),
        mahiyet_kodu=_int(dosya_el, "mahiyetKodu"),
        dosya_belirleyicisi=_s(dosya_el, "dosyaBelirleyicisi"),
        vekil=_vekil_oku(dosya_el.find("VekilKisi")),
        taraflar=taraflar, alacaklar=alacaklar,
    )


# ── genel API ─────────────────────────────────────────────────────────────────
def xml_metninden_oku(xml_icerik):
    """xml_icerik: str veya bytes. Dönüş: [Dosya, ...] (XML'deki her <dosya> için biri).
    Bozuk/eksik XML'de ValueError fırlatır (kullanıcı dosyayı seçtiğinde hemen görsün)."""
    if isinstance(xml_icerik, bytes):
        try:
            xml_icerik = xml_icerik.decode("utf-8")
        except UnicodeDecodeError:
            xml_icerik = xml_icerik.decode("utf-8-sig")
    try:
        kok = ET.fromstring(xml_icerik)
    except ET.ParseError as e:
        raise ValueError(f"XML ayrıştırılamadı: {e}")

    dosyalar_el = kok.find("dosyalar")
    if dosyalar_el is None:
        raise ValueError("XML 'exchangeData/dosyalar' düğümünü içermiyor — beklenen UYAP "
                         "değişim formatı (exchangeData) değil.")
    dosyalar = [_dosya_oku(d) for d in dosyalar_el.findall("dosya")]
    if not dosyalar:
        raise ValueError("XML içinde hiç <dosya> (takip) bulunamadı.")
    return dosyalar


def xml_dosyasindan_oku(yol):
    with open(yol, "rb") as f:
        return xml_metninden_oku(f.read())


def dosya_ozet(dosya):
    """GUI önizlemesi / onay ekranı için XML'den okunan tek bir Dosya'nın özeti
    (CANLI UYAP doğrulaması İÇERMEZ — bkz. takip.prepare() doğrulanmış özet için)."""
    alacaklilar = [t for t in dosya.taraflar if t.rol_id == 21 or t.rol.upper() == "ALACAKLI"]
    borclular = [t for t in dosya.taraflar if t.rol_id == 22 or "BORÇLU" in t.rol.upper()]
    kalemler = []
    toplam = 0.0
    for da in dosya.alacaklar:
        for k in da.kalemler:
            kalemler.append({
                "ad": k.kod_aciklama or k.ad, "tutar": k.tutar,
                "faiz_orani": k.faiz.oran if k.faiz else None,
            })
            toplam += k.tutar
    return {
        "dosya_belirleyicisi": dosya.dosya_belirleyicisi,
        "mahiyet_kodu": dosya.mahiyet_kodu,
        "alacaklilar": [{"ad": t.ad, "mersis_no": t.mersis_no, "tckn": t.tckn, "iban": t.iban}
                        for t in alacaklilar],
        "borclular": [{"ad": t.ad, "tckn": t.tckn} for t in borclular],
        "kalemler": kalemler,
        "toplam": round(toplam, 2),
        "vekil": {"ad": dosya.vekil.ad, "soyad": dosya.vekil.soyad, "baro_no": dosya.vekil.baro_no}
                 if dosya.vekil else None,
    }
