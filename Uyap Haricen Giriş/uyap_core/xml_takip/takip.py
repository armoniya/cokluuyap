# -*- coding: utf-8 -*-
"""
uyap_core.xml_takip.takip — "İcra Takip Açılış - XML" canlı oturum üzerinden açma akışı
— TARAYICISIZ (Kota Tipi: bkz. KOTA_TIPI_DESTEKLENEN — "banka" varsayılan, "gayrimenkul"
de destekleniyor; "avukat"/"kurum"/"takip_tipi" HENÜZ canlı doğrulanmadığı için prepare()
tarafından reddedilir)
====================================================================================
2026-07-28 tarihli GERÇEK bir dosya açılışının canlı kaydından (Panel/modules/
logger_data/kayitlar.jsonl, "XML TAKİP AÇILIŞ 2807" oturumu — dosya GERÇEKTEN
açıldı, dosyaId döndü, evrak "Evrak, tevzi dosyasına gönderildi." ile başarıyla
gönderildi) BİREBİR çıkarıldı. Önceki sürümün IlamsizList tahmini YANLIŞTI —
gerçek kayıt bambaşka (çok daha sade) bir zarf gösterdi; bu sürüm o zarfı harfiyen
uygular. AYRICA DOĞRULANDI: bu modülün _taraf_dict/_alacak_kalemi_dict/_ilamsiz_
item_dict/_icra_dosya_bilgileri çıktısı, örnek bir test XML'inden OFFLINE
üretilip yakalanan gerçek multipart gövdesiyle alan alan (JSON eşitliğiyle)
karşılaştırıldı — TarafList/IlamsizList/IcraDosyaBilgileri BİREBİR eşleşiyor
(canlı tevzi çağrısı TEKRAR YAPILMADI — aynı dosyayı ikinci kez açmamak için
yalnızca payload inşası test edildi, gerçek POST'un kabul edileceği %100
garanti değil, ama zarf üretimi artık doğrulanmış kaynak veriyle bit-bit aynı).
UYARI: yalnızca BU XML şekli (tek digerAlacak, kişi+kurum taraf, faizli tek
asıl alacak kalemi) için doğrulandı — farklı birleşimlerde (ör. iki alacaklı,
ilamsizTipi başka bir değer) canlı test edilmeden GÜVENİLMEMELİDİR.

ANAHTAR BULGULAR (2026-07-28 canlı kayıttan):
  1) icra_takip_tevzi_islemleri.ajx JSON GÖVDESİ DEĞİL, multipart/form-data ile
     çağrılıyor (IcraDosyaBilgileri/TarafList/IlamsizList/IlamliList/TahsilatList
     HER BİRİ ayrı bir form alanı, JSON metni olarak) + ekstra bir "IslemTuru":
     "topluTakip" alanı. Önceki sürüm bunu JSON gövdesi sanıyordu — İLK HATANIN
     kök nedeni buydu.
  2) Bu akışta ayrı bir "harç önizleme" adımı (icra_harc_hesaplama_islemleri.ajx)
     YOK — gerçek kayıtta il/adliye/tevziSiraTipleri'nden SONRA doğrudan tevzi
     çağrılıyor. UYAP'ın kendi XML ekranı da harcı kullanıcıya ÖNCEDEN GÖSTERMİYOR
     (yalnızca dosya açıldıktan SONRA "Ödeme Yap" ekranında görünüyor). Bu yüzden
     prepare() artık harç hesaplamaya ÇALIŞMAZ; onay ekranı yalnızca XML'in kendi
     beyan ettiği tutarları gösterir.
  3) TarafList/IlamsizList içeriği CANLI kurumSorgula/kisiSorgula sonucuyla
     ZENGİNLEŞTİRİLMİYOR — tarayıcı XML dosyasını YEREL olarak ayrıştırıp DOĞRUDAN
     bu minimal alanları gönderiyor (ör. kurum için sadece kurumAdi/vergiNo/
     mersisNo; taraf adresBilgisi'si canlı sorgudan DEĞİL, XML'in kendi <adres>
     öğesinden). prepare() yine de kurumSorgula/kisiSorgula/mernis sorgularını
     BAĞIMSIZ bir GÜVENLİK KONTROLÜ olarak çalıştırır (XML'deki yazım hatalarını
     yakalamak için) ama SONUÇLARINI gönderilen veriye KARIŞTIRMAZ.
  4) faizOraniKurus tam sayı değil, oranın YÜZDE'sinin (ondalık kısmının) STRING
     hali: 66.30 -> faizOraniTL=66, faizOraniKurus="30" (yani (oran-tam)*100,
     *10 DEĞİL — önceki sürümün _oran_tl_kurus'u ipotek'ten miras kalan YANLIŞ
     bir varsayımdı, bkz. _oran_yuzde_kurus).
  5) dosyaId/dosyaID alanları HER yanıtta (tevzi / tamamlanmayanDosyalar) FARKLI
     bir opak token — kalıcı bir veritabanı ID'si DEĞİL, her seferinde YENİDEN
     üretiliyor gibi görünüyor (aynı dosya için art arda üç farklı token
     gözlemlendi). Bu yüzden finalize() tevzi yanıtındaki dosyaId'yi SAKLAMAZ;
     her sonraki adımdan (indirme, evrak gönder, ödeme) hemen ÖNCE
     tamamlanmayanDosyalar_brd.ajx'ten TARAFLAR METNİYLE eşleşen dosyayı YENİDEN
     bulur ve dosyaID'yi TIRNAK İŞARETLERİ DAHİL (asla strip edilmeden) kullanır
     — çünkü indirme URL'sinde gerçek kayıtta tırnaklar url-encode edilmiş
     (%22...%22) olarak AYNEN gönderilmişti.
  6) finalize()'daki UDF indir / e-imza / evrak gönder adımları BİREBİR canlı
     kayıttan ("Evrak, tevzi dosyasına gönderildi." başarı yanıtı alındı).
  7) HARÇ ÖDEME adımları (odeme_tipleri_sorgula, dosya_harc_masraf_hesabi,
     davaAcilisOdemeIslemleri_brd) AYRI, daha önceki bir kayıttan (33 adımlık
     oturum) alındı — bu üçü de gerçek payload'la yakalandı ama kisiKurumId'nin
     dosya_harc_masraf_hesabi yanıtından hangi yoldan okunacağı ([2][1].
     kisiKurumBilgileriDVO.kisiKurumId) TEK örnekten endeks varsayımıyla çıkarıldı
     — savunmacı okunur, bulunamazsa dosya AÇIK ama ÖDENMEMİŞ kalır (evrak zaten
     gönderilmiş olduğundan dosya kaybolmaz, 'Tamamlanmayan Dosyalar' ekranından
     elle ödenir).
  8) KİŞİ tarafta (borçlu) MERNİS ADRESİ SORUNU (kullanıcı bulgusu, 2026-07-29):
     ilk canlı denemede (bulgu 3'teki "XML'den birebir" kural GEREĞİ) borçlunun
     adresBilgisi'si XML'in kendi <adres> öğesinden (genelde "Mernis Adresi"
     YER TUTUCU metni, gerçek sokak/il/ilçe DEĞİL) kopyalanıyor ve
     "mernisAdresiKullan": false gönderiliyordu — UYAP bu anlamsız metni adres
     olarak kabul etmeyip tebligatı dosyada KAYITLI ESKİ bir adrese düşürüyordu.
     Düzeltme: KİŞİ taraf için artık "mernisAdresiKullan": true verilir ve
     adresBilgisi HİÇ GÖNDERİLMEZ — uyap_core.ipotek.takip'in AYNI endpoint
     (icra_takip_tevzi_islemleri.ajx) için CANLI DOĞRULANMIŞ kisi_taraf deseniyle
     BİREBİR. prepare() artık mernis kontrolü false dönerse (kişinin mernis
     kaydı YOKSA) sessizce uyarı vermek YERİNE DURUR (ValueError) — çünkü artık
     UYAP'ın mernis adresini bulabileceğine GÜVENİYORUZ. Bu satır KÜÇÜK ama
     GÜVENİLİRLİK BAKIMINDAN kanıtlı (aynı endpoint, ipotek'ten) — yine de bu
     spesifik Banka-Dosyası kota'sıyla BİRLİKTE canlı doğrulanmadı; ilk
     denemede UYAP ekranından (Dosya Bilgileri > Taraflar) borçlunun adresinin
     doğru göründüğünü MUTLAKA teyit edin.
"""

import re as _re
import json
import asyncio
from datetime import datetime

from ..ipotek.takip import _il_bul, _adliye_bul, _degerle_bul, _guvenli_liste, _temiz_buyuk


async def _api_text(ctx, path, payload=None):
    resp = await ctx.uyap("POST", path, json=(payload if payload is not None else {}))
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü.")
    return resp.text


async def _api_json(ctx, path, payload=None):
    return json.loads(await _api_text(ctx, path, payload))


async def _api_multipart_json(ctx, path, alanlar):
    """alanlar: {ad: değer}. dict/list değerler JSON metnine çevrilip (None, metin)
    form alanı olarak; str değerler AYNEN gönderilir (ör. IslemTuru). Yanıtı
    JSON'a çevirip döner — bkz. modül başlığı bulgu (1): bu endpoint XML/Banka
    Dosyası akışında JSON gövde DEĞİL, multipart/form-data bekliyor."""
    files = {
        k: (None, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
        for k, v in alanlar.items()
    }
    resp = await ctx.uyap("POST", path, files=files)
    if resp.status_code >= 400:
        raise ValueError(f"UYAP '{path}' HTTP {resp.status_code} döndürdü.")
    return json.loads(resp.text)


IL_VARSAYILAN = "İzmir"
ADLIYE_VARSAYILAN = "İzmir"
ODEME_TIPI_VARSAYILAN = "7"   # canlı yakalanan kayıtta kullanılan (bkz. odeme_tipleri_sorgula.ajx -> "7":"e-barobirlik kart")

# 2026-08-19: Kota/Daire Tevzi Tipi seçimi — UYAP'ın "Takip Açılış - XML" ekranındaki
# "Kota / Daire Tevzi Tipi / Takip Tipleri" alanının 5 seçeneğinden yalnız "banka" ve
# "gayrimenkul" bu şekilde (True/False bayrak çifti) CANLI DOĞRULANDI (bkz. modül başlığı
# bulgu (1) — icra_takip_tevzi_islemleri.ajx gövdesi, 2026-07-28 kaydı). "gayrimenkul" ise
# bayrağın SİMETRİK ters çevrilmesiyle (Banka=False, Gayrimenkul=True) çıkarıldı — kendi
# canlı kaydı YOK, ilk kullanımda UYAP ekranından (Dosya Bilgileri) doğru kota'ya
# düştüğünü MUTLAKA teyit edin. "avukat"/"kurum"/"takip_tipi" (Tahliye, Nafaka vb. —
# tevziSiraTakipTip kodu gerektiriyor, boolean bayrak DEĞİL) için IcraDosyaBilgileri
# gövdesinin doğru şekli HİÇ yakalanmadı — bu yüzden BİLEREK desteklenmiyor (bkz. prepare()).
KOTA_TIPI_VARSAYILAN = "banka"
_KOTA_TIPI_BAYRAK = {
    "banka":       {"dosyaTevziTipiBanka": True,  "dosyaTevziTipiGayrimenkul": False},
    "gayrimenkul": {"dosyaTevziTipiBanka": False, "dosyaTevziTipiGayrimenkul": True},
}
KOTA_TIPI_DESTEKLENEN = frozenset(_KOTA_TIPI_BAYRAK)
KOTA_TIPI_DESTEKSIZ = frozenset({"avukat", "kurum", "takip_tipi"})

# 2026-08-06: HARÇ ÖDEMESİ ARTIK AKTİF — kisiKurumId ALACAKLI'ya (tarafRolu==21)
# göre seçiliyor. Önceki sürüm dosya_harc_masraf_hesabi.ajx yanıtındaki listede
# alacaklı/borçlu ayrımı yapmadan İLK bulunan kaydı alıyordu (bkz. finalize()
# bulgu 7); canlı bir dosyada listede borçlu ÖNCE geldiği için yanlışlıkla
# borçlunun kisiKurumId'si seçiliyordu. Aynı dosyada manuel "Ödeme Yap"
# denendiğinde UYAP'ın kendi arayüzünün davaAcilisOdemeIslemleri_brd.ajx'e
# gönderdiği gerçek payload'da ALACAKLI'nın kisiKurumId'si çıktı — bu, XML'deki
# rolID="21" Rol="ALACAKLI" ile ve uyap_core.ipotek.takip'teki rolID 21=ALACAKLI/
# 22=BORÇLU kuralıyla birebir örtüşüyor. Birden fazla ALACAKLI olan bir dosya
# henüz canlı doğrulanmadı — o durumda ilk bulunan alacaklı seçilir.
HARC_ODEME_AKTIF = True
ALACAKLI_TARAF_ROLU = 21


def _maskeli_mi(ad):
    """UYAP bazı canlı kisiSorgula yanıtlarında kişi adını gizlilik amacıyla
    kısmen '*' ile maskeler (ör. 'YUSUF' -> 'YU***') — 2026-07-28 canlı testinde
    görüldü. Maskeli bir ad, XML'deki AÇIK adla asla harfiyen eşleşmez; bu
    yüzden ad-uyuşmazlığı uyarısı maskeli yanıtlarda ANLAMSIZ olur ve HER
    çalıştırmada yanlışlıkla tetiklenir — bu durumda karşılaştırma atlanır."""
    return "*" in (ad or "")


def _oran_yuzde_kurus(oran):
    """66.30 -> (66, '30'). bkz. modül başlığı bulgu (4) — canlı kayıttan."""
    tam = int(oran)
    kurus = round((oran - tam) * 100)
    return tam, str(kurus)


def _adres_dict(adres):
    if adres is None:
        return {"adres": "", "adresTuru": "", "il": None, "ilce": None,
                "ilKodu": None, "ilceKodu": None, "postaKodu": None}
    return {"adres": adres.adres, "adresTuru": adres.adres_turu, "il": adres.il,
            "ilce": adres.ilce, "ilKodu": adres.il_kodu, "ilceKodu": adres.ilce_kodu,
            "postaKodu": adres.posta_kodu}


def _taraf_dict(taraf):
    """CANLI DOĞRULANAN minimal şekil (bkz. modül başlığı bulgu (3)) — XML
    içeriğinden DOĞRUDAN kurulur, hiçbir alan canlı sorgudan zenginleştirilmez.

    KİŞİ taraf İSTİSNASI (kullanıcı bulgusu, 2026-07-29): ilk canlı denemede
    (bir test dosyasında) borçlunun adresBilgisi'si XML'in KENDİ <adres>
    öğesinden BİREBİR kopyalanmıştı — ama o öğe gerçek bir sokak/il/ilçe adresi
    DEĞİL, banka ihraç yazılımının bıraktığı "Mernis Adresi" YER TUTUCU
    metniydi ("adresTuruAciklama": "Yurt İçi İkametgah Adresi" — MERSİS/gerçek
    mernis sorgusu DEĞİL). "mernisAdresiKullan": false ile birlikte gönderilince
    UYAP bu anlamsız metni ADRES OLARAK KABUL ETMİYOR, tebligatı dosyada zaten
    kayıtlı ESKİ bir adrese düşürüyor — kullanıcının bildirdiği tam olay budur.
    mtsMernisAdresiKontrol_brd.ajx SADECE true/false döner (canlı doğrulandı,
    gerçek adres metni İÇERMEZ) — yani istemci tarafında "doğru" bir adres
    METNİ kurmanın yolu YOK. Çözüm: uyap_core.ipotek.takip'in AYNI endpoint
    (icra_takip_tevzi_islemleri.ajx) için CANLI DOĞRULANMIŞ deseni — KİŞİ
    tarafta "mernisAdresiKullan": true VERİLİR ve adresBilgisi HİÇ GÖNDERİLMEZ;
    UYAP tevzi anında kişinin GÜNCEL mernis adresini SUNUCU TARAFINDA kendisi
    çeker (tıpkı manuel ekrandaki "Mernis Adresini Sorgula/Seç" düğmesinin
    yaptığı gibi). Bu değişiklik AYNI endpoint için ipotek'ten miras kanıtla
    yüksek güvenilir, ama bu XML/Banka-Dosyası kota'sıyla BİRLİKTE canlı
    doğrulanmadı — ilk denemede borçlunun adresinin doğru göründüğünü UYAP
    ekranından (Dosya Bilgileri > Taraflar) MUTLAKA teyit edin."""
    if taraf.tur == "KURUM":
        temel = {"kurumAdi": taraf.ad, "vergiNo": taraf.vergi_no, "mersisNo": taraf.mersis_no}
        hesap = {"ibanNumarasi": taraf.iban} if taraf.iban else {}
        d = {
            "id": taraf.id, "temelBilgileri": temel, "adresBilgisi": _adres_dict(taraf.adres),
            "hesapBilgisi": hesap, "tarafTuru": taraf.tur, "mernisAdresiKullan": False,
            "tarafSifati": {"rolID": str(taraf.rol_id), "rolAdi": taraf.rol},
        }
    else:
        temel = {"kisiKurumID": "", "tcKimlikNo": taraf.tckn, "adi": taraf.kisi_adi or taraf.ad,
                 "soyadi": taraf.kisi_soyadi or "", "babaAdi": taraf.baba_adi, "anaAdi": taraf.ana_adi,
                 "dogumTarihiStr": taraf.dogum_tarihi, "cinsiyeti": taraf.cinsiyet}
        d = {
            "id": taraf.id, "temelBilgileri": temel,
            "hesapBilgisi": {}, "tarafTuru": taraf.tur, "mernisAdresiKullan": True,
            "tarafSifati": {"rolID": str(taraf.rol_id), "rolAdi": taraf.rol},
        }
    return d


def _alacak_kalemi_dict(ak, taraf_idx_map):
    taraf_index = [taraf_idx_map[t] for t in ak.taraf_idler if t in taraf_idx_map]
    kalem = {
        "selectedTarafList": ",".join(str(i) for i in taraf_index),
        "temelBilgileri": {
            "alacakTutari": f"{ak.tutar:.2f}", "alacakTutariTL": "PRBRMTL",
            "selectedParaBirimi": "PRBRMTL", "selectedParaBirimiAciklama": "TL - Türk Lirası",
            "KDV": "", "aciklama": ak.kod_aciklama or ak.ad, "detayliAciklama": "",
            "selectedTarihTuru": "",
            "selectedAlacakKalemKodu": {
                "alacakKalemKodAciklama": ak.ad, "alacakKalemKod": str(ak.kod)},
        },
    }
    if ak.faiz:
        tam, kurus = _oran_yuzde_kurus(ak.faiz.oran or 0.0)
        kalem["faizBilgileri"] = {
            "selectedFaizTuru": {"tktId": ak.faiz.tip_kod, "aciklama": ""},
            "faizOraniTL": tam, "faizOraniKurus": kurus,
            "selectedFaizSureTipi": ak.faiz.sure_tip or "2", "selectedFaizSureTipiAciklama": "Diğer",
            "baslangicTarihi": ak.faiz.baslangic_tarihi,
        }
    else:
        kalem["faizBilgileri"] = {}
    return kalem


def _ilamsiz_item_dict(da, taraf_idx_map, idx):
    return {
        "id": idx, "ilamsizTipi": "diger", "alacakNo": "",
        "meblagi": f"{da.tutar:.2f}", "meblagTuru": "PRBRMTL", "meblagTuruAciklama": "TL - Türk Lirası",
        "aciklama": da.aciklama or "", "alacakTarihi": "",
        "alacakKalemleri": [_alacak_kalemi_dict(ak, taraf_idx_map) for ak in da.kalemler],
    }


def _icra_dosya_bilgileri(state):
    """CANLI DOĞRULANAN minimal şekil (bkz. modül başlığı bulgu (1)). Kota tipi bayrakları
    artık state["kota_tipi"]'ye göre seçilir (bkz. _KOTA_TIPI_BAYRAK) — prepare() zaten
    yalnız KOTA_TIPI_DESTEKLENEN içindeki değerlere izin verdiği için burada tekrar
    doğrulanmıyor."""
    kriter_kodlari = [k["kod"] for k in state["dosya_kriterleri"]]
    bayraklar = _KOTA_TIPI_BAYRAK[state["kota_tipi"]]
    return {
        "kotaKullanimSekli": 0,
        "adliyeBirimId": state["adliye_birim_id"],
        "dosyaKriterList": ",".join(kriter_kodlari) + ("," if kriter_kodlari else ""),
        **bayraklar,
        "mahiyetId": str(state["mahiyet_kodu"]),
        "takipTuru": str(state["takip_turu"]), "takipYolu": str(state["takip_yolu"]),
        "takipSekli": str(state["takip_sekli"]),
        "dosyaAciklama_48_4": state["aciklama_48_4"], "dosyaAciklama_48_9": state["aciklama_48_9"],
    }


# ── FAZ 1: PREPARE (referans doğrulama + bağımsız güvenlik kontrolü) ──────────
async def prepare(ctx, dosya, *, il=IL_VARSAYILAN, adliye=ADLIYE_VARSAYILAN,
                  il_kodu=None, adliye_birim_id=None, kota_tipi=KOTA_TIPI_VARSAYILAN):
    """dosya: uyap_core.xml_takip.parse.Dosya (XML'den zaten ayrıştırılmış, TEK takip).
    Dönüş: (ozet, state). Tevzi almaz, dosya AÇMAZ. ÖNEMLİ: bu akışta UYAP harcı
    ÖNCEDEN göstermiyor (bkz. modül başlığı bulgu (2)) — ozet'teki tutarlar XML'in
    KENDİ beyanıdır, UYAP'ça henüz hesaplanmamıştır.

    il_kodu/adliye_birim_id: Panel'in canlı İl/Adliye dropdown'larından GELİYORSA
    (kullanıcı zaten UYAP'ın kendi listesinden seçti) doğrudan buradan verilir — bu
    durumda illerIlcelerGetir.ajx/icraTakipAdliyeler.ajx'teki isimle-bul (fuzzy)
    eşleştirmesi ATLANIR (belirsiz/yanlış adliyeye düşme riskini azaltır). Yalnız
    il/adliye (isim) verilirse eski isimle-arama davranışına geri düşülür.

    kota_tipi: "banka" | "gayrimenkul" (bkz. KOTA_TIPI_DESTEKLENEN) — diğerleri
    (KOTA_TIPI_DESTEKSIZ) CANLI DOĞRULANMADIĞI için burada BİLEREK reddedilir."""
    log = ctx.log

    if kota_tipi not in KOTA_TIPI_DESTEKLENEN:
        if kota_tipi in KOTA_TIPI_DESTEKSIZ:
            raise ValueError(
                f"Kota tipi '{kota_tipi}' için UYAP'a gönderilecek gövde şekli HENÜZ CANLI "
                "DOĞRULANMADI (yalnız 'banka' ve 'gayrimenkul' destekleniyor). Bu takibi UYAP'ın "
                "kendi 'Takip Açılış - XML' ekranından elle açın, ya da önce Logger ile bu kota "
                "tipi için bir oturum yakalayıp uyap_core.xml_takip.takip'i güncelleyin.")
        raise ValueError(f"Bilinmeyen kota tipi: {kota_tipi!r}")

    if il_kodu is not None and adliye_birim_id is not None:
        selected_il = {"ad": il, "il": il_kodu}
        selected_adliye = {"adliyeIsmi": adliye, "adliyeBirimID": adliye_birim_id}
    else:
        log("İl/ilçe listesi alınıyor...")
        iller = await _api_json(ctx, "illerIlcelerGetir.ajx", {})
        selected_il = _il_bul(iller, il)
        if not selected_il:
            raise ValueError(f"İl bulunamadı: {il}")

        log(f"{selected_il.get('ad')} adliyeleri alınıyor...")
        adliyeler = await _api_json(ctx, "icraTakipAdliyeler.ajx", {"ilKodu": selected_il.get("il")})
        selected_adliye = _adliye_bul(adliyeler, adliye)
        if not selected_adliye:
            raise ValueError(f"Adliye bulunamadı: {adliye}")
        adliye_birim_id = selected_adliye.get("adliyeBirimID")

    turu_listesi = await _api_json(ctx, "icra_takip_turu.ajx", {})
    turu_item = _degerle_bul(turu_listesi, dosya.takip_turu)
    if turu_item is None and _guvenli_liste(turu_listesi):
        raise ValueError(f"XML'deki takipTuru={dosya.takip_turu} UYAP referans listesinde yok: {turu_listesi}")
    takip_turu_text = turu_item.get("name") if turu_item else "İlamsız Takip"

    acilabilir = await _api_json(ctx, "icraTakipDosyaAcilabilirMi.ajx", {"birimId": adliye_birim_id})
    if not (isinstance(acilabilir, dict) and str(acilabilir.get("message")).lower() == "true"):
        raise ValueError(f"UYAP bu adliyede dosya açılamayacağını bildirdi: {acilabilir}")
    await _api_json(ctx, "tevziSiraTipleri.ajx", {"birimId": adliye_birim_id})

    yol_listesi = await _api_json(ctx, "icra_takip_yolu.ajx", {"takipTuru": dosya.takip_turu})
    takip_yolu_item = _degerle_bul(yol_listesi, dosya.takip_yolu)
    if not takip_yolu_item:
        raise ValueError(f"XML'deki takipYolu={dosya.takip_yolu} UYAP referans listesinde yok: {yol_listesi}")

    sekli_listesi = await _api_json(ctx, "icra_takip_sekli.ajx",
                                    {"takipTuru": dosya.takip_turu, "takipYolu": dosya.takip_yolu})
    takip_sekli_item = _degerle_bul(sekli_listesi, dosya.takip_sekli)
    if not takip_sekli_item:
        raise ValueError(f"XML'deki takipSekli={dosya.takip_sekli} UYAP referans listesinde yok: {sekli_listesi}")

    mahiyet_listesi = await _api_json(ctx, "icra_takip_mahiyetleri.ajx", {
        "takipTuru": dosya.takip_turu, "takipYolu": dosya.takip_yolu, "takipSekli": dosya.takip_sekli})
    mahiyet_item = _degerle_bul(mahiyet_listesi, dosya.mahiyet_kodu)
    if not mahiyet_item:
        raise ValueError(f"XML'deki mahiyetKodu={dosya.mahiyet_kodu} UYAP referans listesinde yok: {mahiyet_listesi}")

    kriter_listesi = await _api_json(ctx, "mtsDosyaKriterleri_brd.ajx", {})
    kriterler = []
    for kod, secili in (("bk", True), ("bsmv", dosya.bsmv), ("kkdf", dosya.kkdf)):
        it = next((k for k in _guvenli_liste(kriter_listesi) if k.get("kod") == kod), None)
        if it and (secili or it.get("zorunlu")):
            kriterler.append(it)

    alacak_giris = await _api_json(ctx, "icraTakipAlacakGirisBilgileri.ajx", {"takipTuru": dosya.takip_turu})
    kalem_kodlari = _guvenli_liste(alacak_giris[0]) if isinstance(alacak_giris, list) and alacak_giris else []
    faiz_turleri = _guvenli_liste(alacak_giris[1]) if isinstance(alacak_giris, list) and len(alacak_giris) > 1 else []
    kalem_kod_degerleri = {k.get("value") for k in kalem_kodlari}
    faiz_kod_degerleri = {f.get("value") for f in faiz_turleri}
    for da in dosya.alacaklar:
        for ak in da.kalemler:
            if kalem_kodlari and ak.kod not in kalem_kod_degerleri:
                raise ValueError(f"XML'deki alacakKalemKod={ak.kod} ('{ak.ad}') UYAP referans "
                                 f"listesinde yok — beklenmeyen kalem türü.")
            if ak.faiz and faiz_turleri and ak.faiz.tip_kod not in faiz_kod_degerleri:
                raise ValueError(f"XML'deki faizTipKod={ak.faiz.tip_kod!r} UYAP referans listesinde yok.")

    # ── Bağımsız güvenlik kontrolü (bkz. modül başlığı bulgu (3)): SADECE
    # doğrulama/uyarı amaçlı — sonucu gönderilecek veriye KARIŞTIRILMAZ. ──
    for taraf in dosya.taraflar:
        ctx.check_cancel()
        if taraf.tur == "KURUM":
            if not taraf.mersis_no:
                raise ValueError(f"Taraf '{taraf.ad}' (KURUM) için Mersis No XML'de yok.")
            log(f"Mersis no ile kontrol ediliyor: {taraf.mersis_no} ({taraf.ad})")
            try:
                kurumlar = await _api_json(ctx, "kurumSorgula.ajx", {"mersisNo": taraf.mersis_no})
                if not isinstance(kurumlar, list) or not kurumlar:
                    raise ValueError("kurum bulunamadı")
                kurum_adi = kurumlar[0].get("kurumAdi")
                if _temiz_buyuk(kurum_adi) != _temiz_buyuk(taraf.ad):
                    log(f"⚠️ Uyarı: XML'deki ad ('{taraf.ad}') UYAP kaydından ('{kurum_adi}') farklı.")
                else:
                    log(f"✓ Kurum doğrulandı: {kurum_adi}")
            except Exception as e:
                log(f"⚠️ Kurum güvenlik kontrolü yapılamadı ({taraf.mersis_no}): {e} — "
                   "yine de XML'deki bilgiyle devam ediliyor.")
        else:
            if not taraf.tckn:
                raise ValueError(f"Taraf '{taraf.ad}' (KİŞİ) için TCKN XML'de yok.")
            log(f"TCKN ile kontrol ediliyor: {taraf.tckn} ({taraf.ad})")
            try:
                kisi = await _api_json(ctx, "kisiSorgula.ajx",
                                       {"tcKimlikNo": taraf.tckn, "tarafSifati": taraf.rol_id})
                if not isinstance(kisi, dict) or not kisi.get("tcKimlikNo"):
                    raise ValueError(f"T.C. sorgulaması başarısız — UYAP yanıtı: {kisi}")
                guncel_ad = f"{kisi.get('adi','')} {kisi.get('soyadi','')}".strip()
                if _maskeli_mi(guncel_ad):
                    log(f"ℹ️ UYAP bu kişinin adını gizlilik nedeniyle maskeledi ('{guncel_ad}') — "
                       "ad karşılaştırması atlanıyor, TCKN yeterli kabul edildi.")
                elif _temiz_buyuk(guncel_ad) != _temiz_buyuk(taraf.ad):
                    log(f"⚠️ Uyarı: XML'deki ad ('{taraf.ad}') UYAP kaydından ('{guncel_ad}') farklı.")
                else:
                    log(f"✓ Kişi doğrulandı: {guncel_ad}")
                mernis = await _api_json(ctx, "mtsMernisAdresiKontrol_brd.ajx", {"tcKimlikNo": taraf.tckn})
                if mernis is False or mernis == "false":
                    # ARTIK ZORUNLU kontrol (kullanıcı bulgusu, 2026-07-29): taraf
                    # dict'i tevzi anında UYAP'ın kendi mernis adresini çekmesine
                    # GÜVENİYOR (mernisAdresiKullan=true, bkz. _taraf_dict) — mernis
                    # adresi yoksa UYAP'ın NEREYE tebligat çıkaracağı belirsiz/eski
                    # bir adrese düşebilir; bu yüzden burada DURULUR.
                    raise ValueError(
                        f"MERNİS'te kayıtlı adres yok: {taraf.tckn} ({taraf.ad}) — bu taraf için "
                        "UYAP tebligat adresini otomatik çözemez. Devam etmeden önce UYAP'ta bu "
                        "kişinin adresini elle kontrol edin.")
            except ValueError:
                raise
            except Exception as e:
                log(f"⚠️ Kişi güvenlik kontrolü yapılamadı ({taraf.tckn}): {e} — "
                   "yine de XML'deki bilgiyle devam ediliyor.")

    taraf_list = [_taraf_dict(t) for t in dosya.taraflar]
    taraf_idx_map = {t.id: i for i, t in enumerate(dosya.taraflar)}
    ilamsiz_list = [_ilamsiz_item_dict(da, taraf_idx_map, i) for i, da in enumerate(dosya.alacaklar)]

    state = {
        "dosya": dosya, "taraf_list": taraf_list, "ilamsiz_list": ilamsiz_list,
        "selected_il": selected_il, "selected_adliye": selected_adliye, "adliye_birim_id": adliye_birim_id,
        "kota_tipi": kota_tipi,
        "takip_turu": dosya.takip_turu, "takip_turu_text": takip_turu_text,
        "takip_yolu": dosya.takip_yolu, "takip_sekli": dosya.takip_sekli,
        "mahiyet_kodu": dosya.mahiyet_kodu, "mahiyet_text": mahiyet_item.get("name"),
        "dosya_kriterleri": kriterler,
        "aciklama_48_4": dosya.talep_edilen_hak, "aciklama_48_9": dosya.aciklama_48_9,
    }

    ozet = _ozet_kur(dosya, mahiyet_item.get("name"))
    return ozet, state


def _ozet_kur(dosya, mahiyet_text):
    """ÖNEMLİ: bu tutarlar XML'in KENDİ beyanıdır — UYAP tevzi adımından önce
    harç hesaplamıyor (bkz. modül başlığı bulgu (2)), yani burada bir UYAP
    harç önizlemesi YOKTUR. Kullanıcı yalnızca alacak kalemlerini kontrol eder."""
    kalemler = []
    toplam = 0.0
    for da in dosya.alacaklar:
        for k in da.kalemler:
            kalemler.append({"ad": k.kod_aciklama or k.ad, "tutar": k.tutar,
                             "faiz_orani": k.faiz.oran if k.faiz else None})
            toplam += k.tutar
    return {
        "dosya_belirleyicisi": dosya.dosya_belirleyicisi, "mahiyet": mahiyet_text,
        "taraflar": [{"ad": t.ad, "rol": t.rol} for t in dosya.taraflar],
        "kalemler": kalemler, "toplam_alacak": round(toplam, 2),
        "harclar": [], "harc_toplam": None,
        "harc_notu": "UYAP bu akışta harcı ÖNCEDEN göstermiyor — tutar tevzi "
                    "sonrası 'Ödeme Yap' adımında belirlenecek.",
    }


# ── "Tamamlanmayan Dosyalar" üzerinden GÜNCEL dosyaID bul ─────────────────────
def _dosya_tarih_anahtari(kayit):
    for alan in ("sonIslemTarihi", "dosyaAcilisTarihi"):
        s = kayit.get(alan)
        if s:
            try:
                return datetime.strptime(s, "%b %d, %Y %I:%M:%S %p")
            except Exception:
                pass
    return datetime.min


async def _guncel_dosya_id_bul(ctx, dosya):
    """bkz. modül başlığı bulgu (5): dosyaId her yanıtta farklı bir opak token —
    bu yüzden HER kullanımdan önce burada TARAFLAR METNİYLE eşleşen kaydı yeniden
    bulur ve dosyaID'yi TIRNAKLAR DAHİL, hiç değiştirmeden döner."""
    liste = _guvenli_liste(await _api_json(ctx, "tamamlanmayanDosyalar_brd.ajx", {"dosyaTurKod": 35}))
    if not liste:
        raise ValueError("'Tamamlanmayan Dosyalar' listesi boş — az önce açılan dosya bulunamadı. "
                         "UYAP'ta ELLE kontrol edin.")
    hedef_adlar = [_temiz_buyuk(t.ad) for t in dosya.taraflar]
    adaylar = [k for k in liste
              if all(ad in _temiz_buyuk(k.get("taraflar") or "") for ad in hedef_adlar)]
    if not adaylar:
        raise ValueError(f"Az önce açılan dosya 'Tamamlanmayan Dosyalar' listesinde bulunamadı "
                         f"(aranan taraflar: {hedef_adlar}) — UYAP'ta ELLE tamamlayın.")
    adaylar.sort(key=_dosya_tarih_anahtari, reverse=True)
    return adaylar[0]["dosyaID"]


# ── FAZ 2: FINALIZE (tevzi + UDF indir + e-imza + evrak gönder + harç ödeme) ──
async def finalize(ctx, state, *, vekalet=None, dayanak=None, odeme_tipi=ODEME_TIPI_VARSAYILAN):
    """Yalnızca kullanıcı onay ekranındaki özeti onayladıktan SONRA çağrılmalı.
    vekalet/dayanak: {"filename":..., "bytes":...} ya da None (dayanak opsiyonel).
    Tevzi adımı GERÇEK bir dosya açar."""
    from .. import uyap_proxy, udf_signer
    log = ctx.log
    dosya = state["dosya"]

    gw = uyap_proxy.gw
    if gw is None:
        raise RuntimeError("UYAP oturumu hazır değil (gw=None).")

    log("Tevzi numarası alınıyor — bu adımdan sonra dosya UYAP'ta GERÇEKTEN açılmış olacak...")
    tevzi_sonuc = await _api_multipart_json(ctx, "icra_takip_tevzi_islemleri.ajx", {
        "IcraDosyaBilgileri": _icra_dosya_bilgileri(state),
        "TarafList": state["taraf_list"], "IlamsizList": state["ilamsiz_list"],
        "IlamliList": [], "TahsilatList": [], "IslemTuru": "topluTakip",
    })
    if not isinstance(tevzi_sonuc, dict) or not tevzi_sonuc.get("dosyaId"):
        raise ValueError(f"UYAP tevzi numarası döndürmedi (dosya AÇILMADI). Ham yanıt: {tevzi_sonuc}")
    log(f"Tevzi tamamlandı ({tevzi_sonuc.get('birimAdi', '')}).")

    log("Az önce açılan dosya 'Tamamlanmayan Dosyalar' listesinden bulunuyor...")
    dosya_id = await _guncel_dosya_id_bul(ctx, dosya)

    resp = await ctx.uyap("GET", "icraTakipTalebiIndir.uyap", params={"dosyaId": dosya_id}, write=False)
    if resp.status_code >= 400:
        raise ValueError(f"Takip talebi indirilemedi (HTTP {resp.status_code}). Dosya UYAP'ta açık "
                         "kaldı — 'Tamamlanmayan Dosyalar' ekranından elle devam edin.")
    udf_bytes = resp.content
    if not udf_bytes:
        raise ValueError("Takip talebi taslağı boş indi.")

    from ..mts.models import tr_to_ascii
    guvenli_isim = tr_to_ascii(dosya.dosya_belirleyicisi or "Takip_Talebi") + ".udf"
    log("Takip talebi e-imzalanıyor (headless)...")
    cert_id = getattr(gw, "cert_id", None)
    pin = getattr(getattr(gw, "login_args", None), "pin", None)
    loop = asyncio.get_running_loop()
    signed = await loop.run_in_executor(None, udf_signer.sign_document, udf_bytes, guvenli_isim, cert_id, pin)
    log(f"İmzalandı ({len(signed)} bayt).")

    from ..mts.evrak import items_kur, mime_belirle
    evraklar = [{"tur": "ICR_TAKIP_TLP", "filename": guvenli_isim, "bytes": signed}]
    if vekalet and vekalet.get("bytes"):
        evraklar.append({"tur": "CZM_VEKALETNAME", "filename": vekalet.get("filename") or "vekalet.pdf",
                         "bytes": vekalet["bytes"]})
    else:
        log("⚠️ Vekaletname yok — gönderilmiyor (UYAP reddedebilir).")
    if dayanak and dayanak.get("bytes"):
        evraklar.append({"tur": "MTS_TAKIBIN_DAYANAGI", "filename": dayanak.get("filename") or "dayanak.pdf",
                         "bytes": dayanak["bytes"]})

    items_json, alanlar = items_kur(evraklar)
    files = {}
    for (alan, fname), ev in zip(alanlar, evraklar):
        files[alan] = (fname, ev["bytes"], mime_belirle(fname))
    files["items"] = (None, items_json)
    files["dosyaId"] = (None, dosya_id)

    log(f"Evraklar gönderiliyor ({len(alanlar)} adet)...")
    ev_resp = await ctx.uyap("POST", "davaAcilisEvrakGonderme_brd.ajx", files=files)
    try:
        evrak_sonuc = json.loads(ev_resp.text) if ev_resp.text else {}
    except Exception:
        evrak_sonuc = {"type": "unknown", "message": ev_resp.text}
    if not isinstance(evrak_sonuc, dict) or evrak_sonuc.get("type") != "success":
        raise ValueError(f"Evrak gönderme başarısız. UYAP yanıtı: {evrak_sonuc} — "
                         "'Tamamlanmayan Dosyalar' ekranından elle devam edin.")
    log(f"✓ Evrak gönderildi: {evrak_sonuc.get('message')}")

    # ── Harç ödeme — bkz. modül başlığı bulgu (7). Evrak gönderme BAŞARILI
    # olduysa dosya zaten açık; ödeme başarısız olursa dosya kaybolmaz, elle
    # ödenebilir. Token yine rotatif olabileceğinden dosyaId'yi YENİDEN bulur. ──
    odeme_sonuc = None
    if not HARC_ODEME_AKTIF:
        log("⏸ Harç ödemesi PASİF (ödeyen taraf seçimi düzeltilene kadar) — dosya AÇIK, "
            "evrak gönderildi, ÖDENMEDİ. 'Tamamlanmayan Dosyalar' ekranından elle ödeyin.")
        return {"dosya_id": dosya_id, "evrak_sonuc": evrak_sonuc, "odeme_sonuc": odeme_sonuc}
    try:
        odeme_dosya_id = await _guncel_dosya_id_bul(ctx, dosya)
        await _api_json(ctx, "odeme_tipleri_sorgula.ajx", {})
        harc_masraf = await _api_json(ctx, "dosya_harc_masraf_hesabi.ajx",
                                      {"dosyaId": odeme_dosya_id, "dosyaTurKod": 35})
        # Ödemeyi ALACAKLI'nın (tarafRolu==21) kisiKurumId'siyle gönder — bkz.
        # HARC_ODEME_AKTIF üstündeki not: eski "ilk bulunanı al" mantığı borçlu
        # önce gelirse yanlış tarafa ödeme yapabiliyordu (2026-08-06 canlı bulgu,
        # UYAP'ın kendi arayüzünün gönderdiği payload'la doğrulandı).
        kisi_kurum_id = None
        ilk_bulunan_kisi_kurum_id = None
        if isinstance(harc_masraf, list):
            for parca in harc_masraf:
                if isinstance(parca, list):
                    for it in parca:
                        if isinstance(it, dict) and isinstance(it.get("kisiKurumBilgileriDVO"), dict):
                            aday_id = it["kisiKurumBilgileriDVO"].get("kisiKurumId")
                            if ilk_bulunan_kisi_kurum_id is None:
                                ilk_bulunan_kisi_kurum_id = aday_id
                            if it.get("tarafRolu") == ALACAKLI_TARAF_ROLU:
                                kisi_kurum_id = aday_id
                                break
                if kisi_kurum_id:
                    break
        if not kisi_kurum_id:
            if ilk_bulunan_kisi_kurum_id is None:
                raise ValueError(f"dosya_harc_masraf_hesabi.ajx yanıtında kisiKurumId bulunamadı: {harc_masraf}")
            log(f"⚠️ Yanıtta tarafRolu={ALACAKLI_TARAF_ROLU} (ALACAKLI) bulunamadı — "
                f"ilk bulunan kisiKurumId ({ilk_bulunan_kisi_kurum_id}) kullanılıyor. "
                "Ödemeden sonra doğru tarafa gittiğini UYAP'tan MANUEL doğrulayın.")
            kisi_kurum_id = ilk_bulunan_kisi_kurum_id

        log(f"Harç ödemesi gönderiliyor (ödeme tipi: {odeme_tipi})...")
        odeme_sonuc = await _api_json(ctx, "davaAcilisOdemeIslemleri_brd.ajx", {
            "dosyaId": odeme_dosya_id, "odemeTipi": odeme_tipi, "kisiKurumId": kisi_kurum_id,
            "vakifbankHesapBilgileri": "null", "harcMasrafTipi": "",
            "harcMasrafList": "", "postaMasraflariList": "",
        })
        log("✓ Harç ödemesi tamamlandı.")
    except Exception as e:
        log(f"⚠️ Harç ödemesi YAPILAMADI (dosya AÇIK kaldı, evrak gönderildi): {e} — "
           "'Tamamlanmayan Dosyalar' ekranından elle ödeyin.")

    return {"dosya_id": dosya_id, "evrak_sonuc": evrak_sonuc, "odeme_sonuc": odeme_sonuc}
