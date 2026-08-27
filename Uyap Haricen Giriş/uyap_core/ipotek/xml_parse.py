# -*- coding: utf-8 -*-
"""
uyap_core.ipotek.xml_parse — UYAP "exchangeData" XML'ini (İLAMLI/İpotek biçimi,
<ilam> düğümlü) İpotek Takip Açma paneli alanlarına ayrıştırır (tarayıcısız,
UYAP'a DOKUNMAZ — bkz. uyap_core.xml_takip.parse ile aynı iş bölümü felsefesi).
================================================================================
Zarf, uyap_core.xml_takip.parse'ın okuduğu <exchangeData><dosyalar><dosya>...
zarfıyla AYNI — ama İLAMSIZ (<digerAlacak>) değil İLAMLI biçim: dosya başına TEK
bir <ilam> (tapu/ipotek ilamı) taşır, alacak kalemleri doğrudan <ilam> altında
<alacakKalemi> olarak listelenir. 2026-08-12 tarihli örnek ihraçtan (kullanıcının
verdiği "Burak Şentürk.xml") yazıldı.

KULLANICI KARARI (2026-08-12): Excel akışının aksine (bkz. ipotek.parse — satır
başına yalnızca D/E/G toplanır), bu modül XML'deki HER <alacakKalemi>'ni AYRI AYRI,
BİRLEŞTİRMEDEN aktarır — "her asıl alacağı, faizi, bsmv'yi ve masrafı ayrı ayrı al"
talimatı gereği. Yani üretilen "kalemler" listesindeki her satır, XML'deki TEK bir
<alacakKalemi>'nin karşılığıdır (yalnızca o satırın türüne ait alan doludur, diğerleri
0'dır) — ipotek.takip.prepare() zaten her satırı BAĞIMSIZ olarak işler (bkz. o
modüldeki alacak_kalemleri döngüsü), bu yüzden birleştirmeye HİÇ gerek yok.

Kalem türü SINIFLANDIRMASI (aşağıdaki sırayla, ilk eşleşen kazanır):
  1) alacakKalemKodAciklama == "Asıl Alacak"  → "asil_alacak" (bir <faiz> çocuğu
     varsa oranı okunur; örnek XML'de HER "Asıl Alacak" kaleminde <faiz> var).
  2) alacakKalemKodAciklama == "BSMV"          → "bsmv" (DİKKAT: örnek XML'de kod=7168
     ("Asıl Alacak" kodu) ama kodAciklama="BSMV" olan 3 kalem VAR — bankanın ihraç
     aracının bir tuhaflığı gibi görünüyor; kod yerine kodAciklama METNİNE göre
     sınıflandırmak bu kalemleri doğru şekilde BSMV'ye yönlendiriyor, "Asıl Alacak"a
     karıştırmıyor).
  3) alacakKalemAdi == "Geçmiş Gün Faizi"      → "gecmis_gun_faizi"
  4) alacakKalemKod == "6" veya ad == "Diğer Faiz Alacağı" → "diger_faiz_alacagi"
  5) alacakKalemKod == "9728" veya ad üstte "MASRAF"       → "masraf"
  6) hiçbiri değilse → ValueError (BİLİNMEYEN tür sessizce atlanmaz/varsayılan bir
     türe düşürülmez — kullanıcı elle kontrol etsin).
Örnek XML'de bu 6 kural TÜM 16 alacakKalemi'ni istisnasız kapsıyor (kalemlerin
toplamı == ilamın TÜM alacakKalemi tutarlarının toplamı, kayıpsız) — ama BAŞKA bir
XML'de tanınmayan bir tür çıkarsa akış DURUR, veri sessizce kaybolmaz.

Taraf eşlemesi: her <alacakKalemi>'nin kendi <ref to="taraf" id="..."/> listesi VAR
ama bu modül onu OKUMAZ/kullanmaz — ipotek.takip.prepare() zaten HER kalemi TÜM
taraflara (alacaklı + tüm borçlular) uyguluyor (bkz. o modüldeki taraf_index).
Örnek XML'de zaten HER alacakKalemi'nin ref listesi identik (tüm taraflar) — bu
modül bunu DOĞRULAR (_taraf_reflerini_kontrol_et) ve eğer bir kalemin ref listesi
diğerlerinden FARKLIYSA (yani XML taraf bazında farklı kalem dağıtıyorsa) ValueError
fırlatır — bu panel o senaryoyu (henüz) desteklemiyor, sessizce yanlış tarafa
yazmaktansa durmayı tercih eder.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class XmlTaraf:
    id: str
    tur: str          # "KURUM" | "KISI"
    ad: str
    rol: str
    rol_id: int
    tckn: str = ""
    mersis_no: str = ""
    iban: str = ""


@dataclass
class XmlIpotekVerisi:
    dosya_belirleyicisi: str
    takip_turu: int
    takip_yolu: int
    takip_sekli: int
    ilam_turu_kodu: int          # ilam/@ilamKurumTip — icra_takip_ilam_turleri.ajx
                                  # "value" ADAYI (GUI'de canlı listede varsa seçilir,
                                  # yoksa isim eşleşmesine düşülür — CANLI DOĞRULANMADI)
    aciklama_48_9: str
    ipotek_rehin_aciklama: str   # dosya/@merhununNeOldugu (boş olabilir)
    tapu_muduru_adi: str         # ilam/@ilamKurumAd
    ilam_tarihi: str             # gg/aa/yyyy (XML'deki biçimde, olduğu gibi)
    yevmiye_yil: str
    yevmiye_sira: str
    alacakli: XmlTaraf
    borclular: list              # [XmlTaraf, ...]
    kalemler: list                # ipotek.parse.excel_kalemlerini_oku ile AYNI
                                   # anahtarlar + diger_faiz_alacagi/masraf; HER satır
                                   # yalnızca KENDİ türüne ait alanı doludur (birleştirilmemiş)
    xml_toplam_tutar: float       # ilamdaki TÜM alacakKalemi'lerin toplamı (çapraz kontrol)
    kalemler_toplam_tutar: float  # kalemler listesindeki toplam (xml_toplam_tutar'a EŞİT olmalı)
    uyarilar: list = field(default_factory=list)


# ── ufak yardımcılar (uyap_core.xml_takip.parse ile aynı desen) ──────────────
def _s(el, ad, varsayilan=""):
    return (el.get(ad) if el is not None else None) or varsayilan


def _f(el, ad, varsayilan=0.0):
    try:
        return float(str(_s(el, ad, varsayilan)).strip())
    except (TypeError, ValueError):
        return varsayilan


def _i(el, ad, varsayilan=0):
    try:
        return int(str(_s(el, ad, varsayilan)).strip())
    except (TypeError, ValueError):
        return varsayilan


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

    if kurum_el is not None:
        tur, mersis_no, tckn = "KURUM", _s(kurum_el, "mersisNo"), ""
    elif kisi_el is not None:
        tur, mersis_no, tckn = "KISI", "", _s(kisi_el, "tcKimlikNo")
    else:
        raise ValueError(f"taraf id={taraf_el.get('id')!r}: ne kurum ne kişi bilgisi var.")

    return XmlTaraf(
        id=taraf_el.get("id"), tur=tur, ad=_s(kkb, "ad"),
        rol=_s(rol_el, "Rol"), rol_id=_i(rol_el, "rolID"),
        tckn=tckn, mersis_no=mersis_no, iban=_s(iban_el, "no"),
    )


def _kalem_turu_belirle(ak_el):
    """bkz. modül başlığındaki sınıflandırma kuralları. Tanınmazsa None döner —
    çağıran taraf ValueError fırlatır (sessizce atlanmaz)."""
    kod = _s(ak_el, "alacakKalemKod")
    kod_aciklama = _s(ak_el, "alacakKalemKodAciklama")
    ad = _s(ak_el, "alacakKalemAdi")
    if kod_aciklama == "Asıl Alacak":
        return "asil_alacak"
    if kod_aciklama == "BSMV":
        return "bsmv"
    if ad == "Geçmiş Gün Faizi":
        return "gecmis_gun_faizi"
    if kod == "6" or ad == "Diğer Faiz Alacağı":
        return "diger_faiz_alacagi"
    if kod == "9728" or ad.strip().upper() == "MASRAF":
        return "masraf"
    return None


def _taraf_reflerini_kontrol_et(tumu):
    """Her <alacakKalemi>'nin <ref to="taraf"> listesinin AYNI olduğunu doğrular —
    bkz. modül başlığı. Farklıysa ValueError (bu panel taraf bazlı kalem dağıtımını
    henüz desteklemiyor)."""
    ilk_ref_seti = None
    for ak in tumu:
        ref_seti = frozenset(r.get("id") for r in ak.findall("ref")
                              if r.get("to") == "taraf" and r.get("id"))
        if not ref_seti:
            raise ValueError(f"alacakKalemi id={ak.get('id')!r}: hiç taraf referansı (ref) yok.")
        if ilk_ref_seti is None:
            ilk_ref_seti = ref_seti
        elif ref_seti != ilk_ref_seti:
            raise ValueError(
                f"alacakKalemi id={ak.get('id')!r} FARKLI bir taraf kümesine ({sorted(ref_seti)}) "
                f"referans veriyor (diğerleri {sorted(ilk_ref_seti)}) — bu panel yalnızca TÜM "
                "kalemlerin AYNI taraf kümesine (alacaklı + tüm borçlular) uygulandığı XML'leri "
                "destekliyor; bu dosyayı UYAP ekranından elle açın.")


def _kalemleri_ayikla(ilam_el):
    tumu = ilam_el.findall("alacakKalemi")
    if not tumu:
        raise ValueError("<ilam> içinde hiç <alacakKalemi> yok.")
    _taraf_reflerini_kontrol_et(tumu)

    kalemler = []
    xml_toplam = 0.0
    for ak in tumu:
        tutar = _f(ak, "alacakKalemTutar")
        xml_toplam += tutar
        tur = _kalem_turu_belirle(ak)
        if tur is None:
            raise ValueError(
                f"XML'de TANINMAYAN alacak kalemi türü: id={ak.get('id')!r}, "
                f"ad={_s(ak, 'alacakKalemAdi')!r}, "
                f"kodAciklama={_s(ak, 'alacakKalemKodAciklama')!r}, kod={_s(ak, 'alacakKalemKod')!r} "
                f"(tutar={tutar:.2f} TL) — bu panel bu türü bilmiyor, otomatik doldurma "
                "DURDURULDU. Bu kalemi elle kontrol edip UYAP ekranından girin.")
        faiz_el = ak.find("faiz")
        faiz_orani = _f(faiz_el, "faizOran") if (tur == "asil_alacak" and faiz_el is not None) else 0.0
        satir = {"satir": len(kalemler) + 1, "faiz_orani": 0.0, "asil_alacak": 0.0,
                 "gecmis_gun_faizi": 0.0, "bsmv": 0.0, "diger_faiz_alacagi": 0.0, "masraf": 0.0}
        satir[tur] = round(tutar, 2)
        if tur == "asil_alacak":
            satir["faiz_orani"] = faiz_orani
        kalemler.append(satir)

    kalemler_toplam = round(sum(
        k["asil_alacak"] + k["gecmis_gun_faizi"] + k["bsmv"] + k["diger_faiz_alacagi"] + k["masraf"]
        for k in kalemler), 2)
    return kalemler, round(xml_toplam, 2), kalemler_toplam


def _dosya_oku(dosya_el):
    uyarilar = []
    takip_turu = _i(dosya_el, "takipTuru")
    takip_yolu = _i(dosya_el, "takipYolu")
    takip_sekli = _i(dosya_el, "takipSekli")
    aciklama_48_9 = _s(dosya_el, "aciklama48e9")
    ipotek_rehin_aciklama = _s(dosya_el, "merhununNeOldugu").strip()
    if not ipotek_rehin_aciklama:
        uyarilar.append(
            "İpotek/Rehin Açıklaması XML'de boş (merhununNeOldugu) — bu alan UYAP için "
            "ZORUNLU, göndermeden önce MUTLAKA elle doldurun.")
    dosya_belirleyicisi = _s(dosya_el, "dosyaBelirleyicisi")

    taraflar = [_taraf_oku(t) for t in dosya_el.findall("taraf")]
    if not taraflar:
        raise ValueError(f"dosya {dosya_belirleyicisi!r}: hiç taraf yok.")
    alacaklilar = [t for t in taraflar if t.rol_id == 21 or t.rol.upper() == "ALACAKLI"]
    borclular = [t for t in taraflar if t.rol_id == 22 or "BORÇLU" in t.rol.upper()]
    if len(alacaklilar) != 1:
        raise ValueError(
            f"dosya {dosya_belirleyicisi!r}: XML'de {len(alacaklilar)} ALACAKLI taraf bulundu — "
            "bu panel yalnızca TEK (kurum) alacaklılı ipotek takibini destekliyor.")
    alacakli = alacaklilar[0]
    if alacakli.tur != "KURUM" or not alacakli.mersis_no:
        raise ValueError(
            f"dosya {dosya_belirleyicisi!r}: alacaklı ('{alacakli.ad}') bir KURUM değil ya da "
            "Mersis No taşımıyor — bu panel yalnızca kurum (Mersis no'lu) alacaklıyı destekliyor.")
    if not borclular:
        raise ValueError(f"dosya {dosya_belirleyicisi!r}: BORÇLU taraf bulunamadı.")
    for b in borclular:
        if b.tur != "KISI" or not b.tckn:
            raise ValueError(
                f"dosya {dosya_belirleyicisi!r}: borçlu '{b.ad}' için TCKN XML'de yok ya da "
                "KİŞİ değil — elle girin.")

    ilam_el = dosya_el.find("ilam")
    if ilam_el is None:
        raise ValueError(
            f"dosya {dosya_belirleyicisi!r}: <ilam> düğümü yok — bu, İLAMLI/İpotek biçiminde "
            "bir dosya değil (İLAMSIZ/Banka Dosyası XML'i olabilir; o biçim için "
            "uyap_core.xml_takip kullanılmalı).")
    if len(dosya_el.findall("ilam")) > 1:
        uyarilar.append(
            "XML'de BİRDEN FAZLA <ilam> var — yalnızca İLKİ kullanıldı, diğerleri YOK SAYILDI. "
            "Bu dosyayı elle kontrol edin.")

    tapu_muduru_adi = _s(ilam_el, "ilamKurumAd")
    ilam_tarihi = _s(ilam_el, "ilamTarihi")
    yevmiye_yil = _s(ilam_el, "ilamKararNoYil")
    yevmiye_sira = _s(ilam_el, "ilamKararSira")
    ilam_turu_kodu = _i(ilam_el, "ilamKurumTip")

    kalemler, xml_toplam, kalemler_toplam = _kalemleri_ayikla(ilam_el)

    return XmlIpotekVerisi(
        dosya_belirleyicisi=dosya_belirleyicisi, takip_turu=takip_turu, takip_yolu=takip_yolu,
        takip_sekli=takip_sekli, ilam_turu_kodu=ilam_turu_kodu, aciklama_48_9=aciklama_48_9,
        ipotek_rehin_aciklama=ipotek_rehin_aciklama, tapu_muduru_adi=tapu_muduru_adi,
        ilam_tarihi=ilam_tarihi, yevmiye_yil=yevmiye_yil, yevmiye_sira=yevmiye_sira,
        alacakli=alacakli, borclular=borclular, kalemler=kalemler,
        xml_toplam_tutar=xml_toplam, kalemler_toplam_tutar=kalemler_toplam, uyarilar=uyarilar,
    )


# ── genel API (uyap_core.xml_takip.parse ile aynı imza deseni) ───────────────
def xml_metninden_oku(xml_icerik):
    """xml_icerik: str veya bytes. Dönüş: [XmlIpotekVerisi, ...] (XML'deki her
    <dosya> için biri). Bozuk/eksik XML'de ya da tanınmayan bir alacak kalemi
    türünde ValueError fırlatır."""
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
