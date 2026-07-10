import json
import os
import time
import threading
import concurrent.futures
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
import importlib
import pyautogui
import win32gui
import win32con
import win32process
import win32api
INDİRME_KLASORU = os.path.join(os.path.expanduser("~"), "Downloads")


# Pencere yönetimi mts_pencere.py'ye taşındı (2026-06-26).
# Geriye dönük uyum: pencereyi_one_al'ı mts_takip_acan_api.py de buradan import ediyor.
from mts_pencere import _pencere_basligi, pencereyi_one_al


# indirmeyi_yakala -> mts_indirme.py, UyapBot -> mts_bot.py taşındı (2026-06-26).
# Geriye dönük uyum: mts_takip_acan_api.py bu isimleri hâlâ buradan import ediyor.
from mts_indirme import indirmeyi_yakala
from mts_bot import UyapBot


# ============================================================
#  EXCEL OKUMA + TAKİP DÖNGÜSÜ
# ============================================================
# Veri modelleri ve saf yardımcılar mts_veri.py'ye taşındı (2026-06-26).
# Geriye dönük uyum: aşağıdaki isimler eskiden bu dosyada tanımlıydı,
# mts_takip_acan_api.py vb. hâlâ bunları buradan import ediyor.
from mts_veri import (
    Borclu, AlacakKalemi, Takip,
    _temiz, _virgullu, _tutar_to_float, _float_to_tutar,
    _ad_kanonik, kalemleri_birlestir, _tarih,
)


# XML/Excel dönüşümü mts_donusum.py'ye taşındı (2026-06-26).
from mts_donusum import xml_to_excel, excel_to_takipler


class TakipDurduruldu(Exception):
    """Kullanıcı 'Durdur' butonuna bastığında akışı sonlandırmak için fırlatılır."""
    pass


class DosyaAtla(Exception):
    """Borçlu eklenirken UYAP hatası (mernis eşleşmedi / vefat / adres yok vb.)
    çıkıp kullanıcı 'bu dosyayı atla' dediğinde fırlatılır. Çağıran döngü,
    tarafları temizleyip bir sonraki dosyaya geçer."""
    pass


class KontrolDurumu:
    """GUI'den otomasyon akışını DURAKLATMA / DEVAM / DURDURMA kontrolü.

    - duraklat()  : bir sonraki kontrol noktasında akış bekler.
    - devam_et()  : bekleyen akışı sürdürür.
    - durdur()    : bir sonraki kontrol noktasında TakipDurduruldu fırlatır.
    - bekle_manuel_cozum() : kullanıcı 'Sorunu Çözdüm' deyene kadar bloklar.
    - manuel_cozuldu()     : bekleyen akışı serbest bırakır.

    Otomasyon kodu, eski `input(...)` duraklama noktalarında `kontrol_noktasi()`
    çağırır; böylece arayüzdeki butonlar akışı yönetebilir."""

    def __init__(self, log_fonksiyonu=None):
        self._devam = threading.Event()
        self._devam.set()            # set = çalışıyor, clear = duraklatıldı
        self._durdur = threading.Event()
        self._log = log_fonksiyonu or (lambda m: print(m))
        # Manuel çözüm bekleme mekanizması
        self._manuel_bekle = threading.Event()
        self._manuel_bekle.set()     # set = bekleme yok
        self._manuel_karar = None    # "devam" / "atla" / "_DURDUR_"

    def duraklat(self):
        if not self._devam.is_set():
            return
        self._devam.clear()
        self._log("⏸  Duraklatıldı — 'Devam Et' ile sürdürebilirsiniz.")

    def devam_et(self):
        if self._devam.is_set():
            return
        self._devam.set()
        self._log("▶  Devam ediliyor...")

    def durdur(self):
        self._durdur.set()
        self._devam.set()            # duraklamış akışı serbest bırak ki çıkabilsin
        self._manuel_bekle.set()     # manuel beklemeyi de serbest bırak
        self._log("⏹  Durdurma istendi — mevcut adım bitince duracak.")

    @property
    def durduruldu_mu(self):
        return self._durdur.is_set()

    @property
    def duraklatildi_mi(self):
        return not self._devam.is_set()

    @property
    def manuel_beklemede_mi(self):
        return not self._manuel_bekle.is_set()

    def bekle_manuel_cozum(self):
        """GUI 'Sorunu Çözdüm' butonuna basılana (ya da Durdur'a) kadar bloklar.
        Dönüş değeri: 'devam', 'atla' veya '_DURDUR_'."""
        self._manuel_karar = None
        self._manuel_bekle.clear()   # bloklamaya başla
        self._log("⚠  Program takıldı — 'Sorunu Çözdüm' veya 'Dosyayı Atla' seçin.")
        self._manuel_bekle.wait()    # GUI serbest bırakana kadar bekle
        karar = self._manuel_karar or "devam"
        if karar == "_DURDUR_" or self._durdur.is_set():
            raise TakipDurduruldu()
        return karar

    def manuel_cozuldu(self, karar="devam"):
        """GUI tarafından çağrılır. 'devam', 'atla' veya '_DURDUR_' kabul eder."""
        self._manuel_karar = karar
        self._manuel_bekle.set()

    def kontrol_noktasi(self):
        """Adımlar arasında çağrılır. Duraklatıldıysa bekler, durdurulduysa fırlatır."""
        if self._durdur.is_set():
            raise TakipDurduruldu()
        self._devam.wait()           # duraklatıldıysa burada bloklar
        if self._durdur.is_set():
            raise TakipDurduruldu()


def _son_durum_dogrula(bot, takip):
    """İmzadan ÖNCE ekrandaki veriyi kaynak Takip ile karşılaştırır.
    Bulunan tutarsızlıkların listesini döndürür (boşsa veri tutarlı).

    Kimlik no UYAP sayfasında maskelenmiş olabilir (123456*****).
    Bu durumda kimlik bulunamazsa borçlunun adı/soyadıyla ikinci kontrol
    yapılır; ad/soyad görünüyorsa hata değil uyarı verilir."""
    sorunlar = []
    for b in takip.borclular:
        if b.kimlik and not bot.sayfada_metin_var_mi(b.kimlik):
            # Kimlik ekranda yok — maskeli olabilir; isimle doğrula
            isim_gorunuyor = (
                (b.ad and bot.sayfada_metin_var_mi(b.ad)) or
                (b.soyad and bot.sayfada_metin_var_mi(b.soyad))
            )
            if isim_gorunuyor:
                print(f"    ⚠ Uyarı: {b.ad} {b.soyad} kimliği ({b.kimlik}) "
                      f"ekranda görünmüyor (maskelenmiş olabilir); "
                      f"isim bulundu → devam ediliyor.")
            else:
                sorunlar.append(
                    f"Borçlu ekranda bulunamadı: {b.ad} {b.soyad} ({b.kimlik}).")
    # Alacaklı adının ayırt edici ilk kelimesi görünüyor mu? (yumuşak kontrol)
    alacakli_ilk = (takip.alacakli or "").strip().split(" ")[0]
    if len(alacakli_ilk) >= 3 and not bot.sayfada_metin_var_mi(alacakli_ilk):
        sorunlar.append(f"Alacaklı ekranda görünmüyor olabilir: {takip.alacakli}.")
    return sorunlar


def takip_ac(bot, takip, kontrol=None, vekalet_map=None, il="İzmir", adliye="İzmir",
             onay=None, dayanak_map=None, veri_girisi_onay=None,
             borclu_hata_onay=None, dogrulama_onay=None, manuel_mudahale_onay=None,
             genel_hata_onay=None):
    """Tek bir takibi (aynı Dosya No'lu tüm satırları) uçtan uca açar.

    kontrol          : KontrolDurumu — duraklat/devam/durdur denetimi (None ise kesintisiz).
    vekalet_map      : {alacaklı_adı: vekalet_dosya_yolu}
    il / adliye      : Takibin açılacağı il ve adliye (arayüzden gelir).
    onay             : callable(adim_adi) -> bool  ("Tam Kontrol" modu).
    veri_girisi_onay : callable() -> bool  ("Kontrol" modu). Veri girişi tamamlandıktan
                       sonra kullanıcı formu kontrol edip onaylar; False → durdurulur.
    borclu_hata_onay : callable(dosya_no, borclu_ad, mesaj) -> "atla"/None. Borçlu
                       sorgusunda UYAP hatası çıktığında çağrılır; akış durur ve
                       kullanıcı karar verir. 'Durdur' seçilirse callback içinde
                       TakipDurduruldu fırlatılır; aksi halde dosya atlanır.
                       None ise (konsol modu) hata anında dosya otomatik atlanır.
    dogrulama_onay   : callable(dosya_no, rapor) -> "atla"/"devam". İmza öncesi
                       son-durum doğrulamasında tutarsızlık bulunursa çağrılır.
                       None ise (konsol modu) yanlış takip imzalanmasın diye
                       dosya otomatik atlanır.
    manuel_mudahale_onay : callable(dosya_no, adim, mesaj) -> "tekrar"/"manuel".
                       Onay/imza adımında UYAP uyarı popup'ı çıkınca çağrılır;
                       akış DURUR, kullanıcı tarayıcıda elle düzeltir. 'tekrar' →
                       adım yeniden denenir, 'manuel' → kalanı kullanıcı bitirir
                       (sonraki dosya), 'Durdur' → TakipDurduruldu. None ise
                       (konsol modu) uyarı anında dosya otomatik atlanır.
    genel_hata_onay  : callable(dosya_no, adim, mesaj) -> "devam"/"atla". Herhangi
                       bir adımda beklenmedik hata oluştuğunda çağrılır; akış
                       duraklar ve kullanıcı 'Sorunu Çözdüm, Devam Et' veya
                       'Bu Dosyayı Atla' seçebilir. None ise hata anında dosya
                       otomatik atlanır."""
    if kontrol is None:
        kontrol = KontrolDurumu()
    vekalet_map = vekalet_map or {}
    dayanak_map = dayanak_map or {}

    def _adim(ad):
        """Duraklat/durdur kontrolü + (Tam Kontrol modunda) adım onayı."""
        kontrol.kontrol_noktasi()
        if onay is not None and not onay(ad):
            raise TakipDurduruldu()

    def _guvenli_adim(ad, fonksiyon, *args, **kwargs):
        """Bir adımı güvenli şekilde çalıştırır. Hata oluşursa kullanıcıya
        'Sorunu Çözdüm, Devam Et' / 'Dosyayı Atla' seçeneği sunar (genel_hata_onay
        callback'i varsa); yoksa hatayı yukarı fırlatır (DosyaAtla)."""
        max_deneme = 5
        for deneme in range(1, max_deneme + 1):
            try:
                return fonksiyon(*args, **kwargs)
            except (TakipDurduruldu, DosyaAtla):
                raise   # Bu özel istisnalar doğrudan yukarı git
            except Exception as hata:
                import traceback
                hata_metni = str(hata)
                tb_metni = traceback.format_exc()
                print(f"    ⚠ Adım hatası ('{ad}', deneme {deneme}/{max_deneme}): {hata_metni}")
                print(tb_metni)
                kontrol.kontrol_noktasi()
                if genel_hata_onay is not None:
                    karar = genel_hata_onay(
                        takip.dosya_no, ad,
                        f"{hata_metni}\n\nSorunu tarayıcıda çözdükten sonra "
                        "'Sorunu Çözdüm, Devam Et' butonuna basın."
                    )
                    if karar == "devam":
                        print(f"    🔄 Kullanıcı sorunu çözdü — adım yeniden deneniyor ({deneme+1}/{max_deneme})...")
                        continue
                    elif karar == "atla":
                        raise DosyaAtla(f"Kullanıcı adımı atlattı: {ad} — {hata_metni}")
                    else:
                        # "_DURDUR_" veya beklenmedik değer
                        raise TakipDurduruldu()
                else:
                    # Konsol/otomatik mod: hatayı yukarı fırlat
                    raise DosyaAtla(f"Adım hatası: {ad} — {hata_metni}")

    print(f"\n--- Takip açılıyor (Dosya No: {takip.dosya_no}) ---")
    print(f"    İl: {il} | Adliye: {adliye}")
    print(f"    Alacaklı: {takip.alacakli} | Borçlu: {len(takip.borclular)} | "
          f"Alacak kalemi: {len(takip.alacak_kalemleri)}")

    # 1) Açılış
    _adim("Açılış işlemleri (il / adliye / mahiyet)")
    bot.dava_acilis_islemleri()
    bot.MTS_takip_acilis()
    bot.il_sec_izmir(il)
    time.sleep(3)
    bot.MTS_adliye_sec(adliye)
    bot.talep_aciklamasini_temizle_ve_gir(takip.aciklama)
    bot.mts_mahiyet_secen()

    # 2) Alacaklı (B) + IBAN (C) — takip başına bir kez
    _adim("Alacaklı ve IBAN bilgileri")
    bot.alacakli_secimi(takip.alacakli)
    bot.iban_bilgileri_tikla()
    bot.alacakli_iban_sec()
    bot.vakifbank_iban_alani_tikla()
    bot.vakifbank_iban_doldur(takip.iban)
    bot.vakifbank_iban_ekle_guncelle_tikla()
    time.sleep(1)
    bot.taraf_ekle_butonuna_tikla()   # Alacaklıyı taraf olarak ekle
    kontrol.kontrol_noktasi()

    # 3) Borçlular (D, E, F) — her borçlu için taraf ekle + rol seçimi tekrar
    _adim("Borçluların eklenmesi")
    toplam_borclu = len(takip.borclular)
    print(f"    >>> Eklenecek borçlu sayısı: {toplam_borclu}")
    # Doğrulama temel çizgisi: alacaklı eklendikten sonraki taraf sayısı.
    # Delta tabanlı kontrol (mutlak sayı yerine artış) — sayfadaki olası sabit
    # 'Temizle' vb. danger butonlarına karşı dayanıklı.
    baslangic_taraf = bot.taraf_sayisi()
    onceki_taraf = baslangic_taraf
    for sira, b in enumerate(takip.borclular, 1):
        print(f"    --- Borçlu {sira}/{toplam_borclu}: "
              f"{b.ad} {b.soyad} ({b.kimlik}) ekleniyor ---")
        # Her borçlu için aynı yol: taraf kaydedilince yeni boş taraf formu
        # (#tarafSifati) kendiliğinden açılır — borçlu 1 de bu sayede ekstra
        # tıklama olmadan ekleniyor. (Eskiden sira>1'de taraf_ekle() çağrılıp
        # '#taraf-ekle-mts' bekleniyordu; oysa o buton kaydetten hemen sonra
        # görünmez olduğundan wait_for_selector timeout'a düşüp tüm dosyayı
        # atlatıyordu — kaldırıldı.)
        bot.borclu_secimi()
        bot.borclu_tckn_ekle(" " + b.kimlik)   # F — başında boşluk zorunlu
        bot.borclu_ad_ekle(b.ad)               # D
        bot.borclu_soyad_ekle(b.soyad)         # E
        bot.borclu_sorgula_buton_tikla()

        # --- UYAP borçlu sorgu hatası kontrolü ---
        # Sorgu sonrası 'mernis eşleşmedi / vefat / adres yok' gibi bir uyarı
        # popup'ı çıkarsa körlemesine devam edip veriyi kaydırmamak için DUR.
        hata = bot.borclu_sorgu_hatasi()
        if hata:
            print(f"    !!! UYAP borçlu sorgu hatası ({b.ad} {b.soyad}): {hata}")
            kontrol.kontrol_noktasi()           # duraklat/durdur denetimi
            if borclu_hata_onay is not None:
                # 'Durdur' seçilirse callback içinde TakipDurduruldu fırlatılır;
                # döndüyse kullanıcı 'atla' demiştir.
                borclu_hata_onay(takip.dosya_no, f"{b.ad} {b.soyad}".strip(), hata)
            raise DosyaAtla(hata)

        # Adres Bilgileri akordeonunu (kapalıysa) aç + 'Mernis adresini kullan'
        # işaretle. İdempotent: akordeon zaten açıksa kapatmaz (eskiden körlemesine
        # toggle 'Lütfen adres ekleyiniz' hatasına yol açıyordu).
        bot.adres_mernis_kullan()
        bot.taraf_ekle_butonuna_tikla()
        time.sleep(1)

        # --- DOĞRULAMA: borçlu gerçekten taraf listesine eklendi mi? ---
        # Taraf sayısı artmadıysa (ör. adres/checkbox adımı atlandı, mernis
        # eşleşmedi vb.) körlemesine devam ETME — dur ve kullanıcıya sor.
        yeni_taraf = bot.taraf_sayisi()
        # SERT KAPI: taraf sayısı artmadıysa borçlu eklenmemiştir (adres/checkbox
        # adımı atlanmış vb.) — körlemesine devam etme.
        if yeni_taraf >= 0 and onceki_taraf >= 0 and yeni_taraf <= onceki_taraf:
            neden = (f"Borçlu taraf listesine eklenemedi "
                     f"(taraf sayısı {onceki_taraf}→{yeni_taraf}).")
            print(f"    !!! DOĞRULAMA HATASI ({b.ad} {b.soyad}): {neden}")
            kontrol.kontrol_noktasi()
            if borclu_hata_onay is not None:
                borclu_hata_onay(takip.dosya_no, f"{b.ad} {b.soyad}".strip(), neden)
            raise DosyaAtla(neden)
        # YUMUŞAK kontrol: kimlik ekranda görünüyor mu? (maskeleme olursa diye
        # sadece uyarı; toplu kimlik doğrulaması imza öncesinde tekrar yapılır.)
        if b.kimlik and not bot.sayfada_metin_var_mi(b.kimlik):
            print(f"    ⚠ Uyarı: Borçlu kimliği ({b.kimlik}) ekranda görünmüyor.")
        print(f"    ✓ Borçlu {sira} eklendi (taraf sayısı={yeni_taraf}).")
        onceki_taraf = yeni_taraf
        kontrol.kontrol_noktasi()

    # Döngü sonu doğrulaması (delta): eklenen taraf sayısı = borçlu sayısı olmalı.
    gercek_taraf = bot.taraf_sayisi()
    eklenen = gercek_taraf - baslangic_taraf
    if (gercek_taraf >= 0 and baslangic_taraf >= 0 and eklenen != toplam_borclu):
        neden = (f"Eklenen borçlu sayısı tutmuyor: {eklenen} eklendi, "
                 f"beklenen {toplam_borclu} "
                 f"(taraf {baslangic_taraf}→{gercek_taraf}).")
        print(f"    !!! DOĞRULAMA HATASI: {neden}")
        kontrol.kontrol_noktasi()
        if borclu_hata_onay is not None:
            borclu_hata_onay(takip.dosya_no, "(taraf doğrulama)", neden)
        raise DosyaAtla(neden)
    print(f"    >>> {toplam_borclu} borçlu eklendi (taraf sayısı={gercek_taraf}), "
          f"taraf girişinden ileri geçiliyor.")
    bot.taraf_giris_ilerle_buton_tikla()
    kontrol.kontrol_noktasi()

    # 4) İlamsız: abone no (K) + tutar (L) — takip başına bir kez
    _adim("İlamsız tutar / abone bilgileri")
    bot.ilamsiz_ekle_buton()
    bot.ilamsiz_abone_musteri_no_yaz(takip.abone_no)
    bot.ilamsiz_tutar_alan_doldur(takip.ilamsiz_tutar)
    bot.ilamsiz_fatura_tarih_gir(takip.fatura_tarihi)      # I — Genel Tarih
    bot.ilamsiz_odeme_tarihi_gir_ve_enter(takip.odeme_tarihi)  # J — Ödeme Tarihi
    bot.ilamsiz_aciklama_gir(takip.aciklama)              # H — Talep Açıklaması
    bot.ilamsiz_ekle_butonuna_tikla()
    kontrol.kontrol_noktasi()

    # 5) Alacak kalemleri (M,N,O üçlüleri) — her kalem için
    # Adı ve faiz oranı aynı olan kalemleri tek kaleme toplayıp öyle gir.
    _adim("Alacak kalemlerinin eklenmesi")
    birlesik_kalemler = kalemleri_birlestir(takip.alacak_kalemleri)
    print(f"    >>> {len(takip.alacak_kalemleri)} kalem -> "
          f"{len(birlesik_kalemler)} birleşik kaleme indirgendi.")
    for k in birlesik_kalemler:
        # Tutarı 0 veya boş olan kalemleri atla
        _tutar_s = (k.tutar or "").strip().replace(",", ".").replace(" ", "")
        try:
            _tutar_f = float(_tutar_s) if _tutar_s else 0.0
        except ValueError:
            _tutar_f = 0.0
        if _tutar_f == 0.0:
            print(f"Bilgi: '{k.ad}' kalemi tutar=0 — atlandı.")
            continue
        bot.alacak_kalemi_ekle_butonuna_tikla()
        bot.alacak_turu_ac()
        ad_kucuk = k.ad.lower()
        if "masraf" in ad_kucuk:
            bot.alacak_turu_masraf_alacagi_sec()
        elif "faiz" in ad_kucuk:
            bot.alacak_turu_faiz_alacagi_sec()
        else:
            bot.alacak_turu_asil_alacagi_sec()
        bot.alacak_aciklamasi_gir(k.ad)        # M (alacak adı = açıklama)
        bot.alacak_tutar_gir(k.tutar)          # N

        # --- Faiz türü ---
        # "Geçmiş gün faizi" kalemlerinde faiz türü olarak Reeskont Avans seçilir
        # (UYAP oranı otomatik doldurur; biz 0 yazmaya çalışmayız).
        faiz_tur = (k.faiz_tur or "").strip()
        karsilastir = (faiz_tur + " " + k.ad).lower()
        gecmis_gun = ("geçmiş gün" in karsilastir or "gecmis gun" in karsilastir
                      or "geçmis gün" in karsilastir)
        if gecmis_gun:
            print("Bilgi: 'Geçmiş gün faizi' → faiz türü Reeskont Avans seçiliyor.")
            faiz_tur = "Reeskont Avans"
        bot.alacak_faiz_turu_sec(faiz_tur)     # faizTipKodAciklama

        # --- Faiz oranı (O) ---
        # 0 / boş oranlarda (reeskont, geçmiş gün vb.) yazma — alan kilitli veya
        # 0 reddediliyor; oranı UYAP otomatik dolduruyor.
        oran = (k.faiz_oran or "").strip().replace(".", ",")
        sifir_mi = oran in ("", "0", "0,", "0,0", "0,00", "0,000")
        if gecmis_gun or sifir_mi:
            print(f"Bilgi: Faiz oranı yazılmıyor (oran='{k.faiz_oran}', "
                  f"otomatik dolacak).")
        else:
            bot.alacak_faiz_orani_gir(k.faiz_oran)  # O
        bot.alacak_faiz_sure_tipi_yillik_sec()
        bot.alacak_ekle_mts_butonuna_tikla()
        bot.ekle_tamam_butonuna_tikla()
        kontrol.kontrol_noktasi()

    # 5b) Masraf alacağı — onay sayfasındaki güncel 'Toplam Tutar' kadar
    # masraf alacağı kalemi ekle. Tutar günden güne değişebildiği için sabit
    # değer kullanmak yerine ekrandan okunur.
    _adim("Masraf alacağı (Toplam Tutar) eklenmesi")
    bot.ilamsiz_ileri_butonuna_tikla()        # onay/özet sayfasına geç
    time.sleep(1)
    masraf_tutar = bot.masraf_toplam_tutar_al()
    if masraf_tutar and _tutar_to_float(masraf_tutar) > 0:
        # Sayfadan okunan harç/masraf toplamına sabit 317 TL eklenir
        # (örn. 1000 -> 1317). UYAP'a girilecek masraf alacağı bu tutardır.
        sayfa_tutar = _tutar_to_float(masraf_tutar)
        masraf_tutar = _float_to_tutar(sayfa_tutar + 317)
        print(f"    >>> Masraf alacağı ekleniyor: {masraf_tutar} "
              f"(sayfa tutarı {_float_to_tutar(sayfa_tutar)} + 317)")
        bot.geri_butonuna_tikla()             # alacak kalemleri sayfasına dön
        time.sleep(1)
        bot.alacak_kalemi_ekle_butonuna_tikla()
        bot.alacak_turu_ac()
        bot.alacak_turu_masraf_alacagi_sec()
        bot.alacak_aciklamasi_gir("Masraf")
        bot.alacak_tutar_gir(masraf_tutar)
        bot.alacak_faiz_turu_reeskont_sec()
        bot.alacak_faiz_sure_tipi_yillik_sec()
        bot.alacak_ekle_mts_butonuna_tikla()
        bot.ekle_tamam_butonuna_tikla()
        kontrol.kontrol_noktasi()
        bot.ilamsiz_ileri_butonuna_tikla()    # yeniden onay sayfasına ilerle
        time.sleep(1)
    else:
        print("Bilgi: Toplam Tutar okunamadı/0 — masraf alacağı eklenmedi.")

    # 6) Onay + takip talebi + e-imza (F7)
    _adim("Takip talebi oluşturma + e-imza")

    # --- İMZA ÖNCESİ SON-DURUM DOĞRULAMASI ---
    # Ekrandaki veri kaynak Takip ile tutmuyorsa YANLIŞ takip imzalanmasın.
    sorunlar = _son_durum_dogrula(bot, takip)
    if sorunlar:
        rapor = "İmza öncesi doğrulama uyarısı:\n- " + "\n- ".join(sorunlar)
        print(f"    !!! {rapor}")
        kontrol.kontrol_noktasi()
        if dogrulama_onay is not None:
            # 'Durdur' → callback TakipDurduruldu fırlatır; 'yine de imzala' →
            # döner ve akış devam eder; 'atla' → DosyaAtla.
            karar = dogrulama_onay(takip.dosya_no, rapor)
            if karar == "atla":
                raise DosyaAtla(rapor)
            print("    ⚠ Kullanıcı uyarıya rağmen imzalamayı onayladı.")
        else:
            # Otomatik mod: yanlış takibi imzalamaktansa dosyayı atla.
            raise DosyaAtla(rapor)
    else:
        print("    ✓ İmza öncesi doğrulama: ekrandaki veri kaynakla tutarlı.")

    # Kontrol modu: form dolu, kullanıcı kontrol edip onaylayana kadar bekle
    if veri_girisi_onay is not None:
        if not veri_girisi_onay():
            raise TakipDurduruldu()

    # 'Veri girişini onaylıyorum' — UYAP burada son doğrulamayı yapar; 'mersis
    # bulunamadı' gibi bir uyarı çıkabilir. Bu noktada körlemesine devam etmek
    # yerine DUR ve kullanıcıya manuel müdahale imkânı ver (düzelt → tekrar dene /
    # kalanı manuel bitir / durdur).
    while True:
        bot.verigirisi_onayliyorum_checkbox_tikla(index=0)
        time.sleep(1)
        uyari = bot.acik_uyari_mesaji()        # uyarı/hata popup'ı (kapatmaz)
        if not uyari:
            break                              # sorun yok → imza akışına devam
        print(f"    !!! UYAP uyarısı (veri girişi onayı): {uyari}")
        kontrol.kontrol_noktasi()
        if manuel_mudahale_onay is None:
            # Konsol/otomatik mod: uyarıyı kapat ve dosyayı atla.
            bot.swal_tamam_varsa_kapat()
            raise DosyaAtla(uyari)
        karar = manuel_mudahale_onay(takip.dosya_no, "veri girişi onayı", uyari)
        if karar == "tekrar":
            print("    🔄 Kullanıcı düzeltti — onay adımı yeniden deneniyor.")
            continue
        if karar == "atla":
            raise DosyaAtla(uyari)
        # "manuel": kullanıcı kalan adımları (imza/yükleme) elle tamamlayacak.
        print("    ➡ Kullanıcı kalanı manuel tamamlayacak; sonraki dosyaya geçiliyor.")
        return

    # Onay sonrası SweetAlert2 'Tamam' uyarısı bazen çıkar bazen çıkmaz.
    # Çıkarsa kapat, çıkmazsa hata basmadan devam et (akışı bloklamasın).
    time.sleep(1)
    bot.swal_tamam_varsa_kapat()
    time.sleep(1)
    bot.takip_talebi_olustur_butonuna_tikla()
    time.sleep(2)
    bot.takip_talebi_olustur_tamam_butonuna_tikla()
    time.sleep(2)
    kontrol.kontrol_noktasi()
    # Dosyayı indir, aç, F7 ile imzala, şifre + Enter, kapat, yolunu al
    # Dosya adı benzersiz olsun: ilk borçlunun adı-soyadı + abone no
    _b0 = takip.borclular[0] if takip.borclular else None
    _abone = (takip.abone_no or takip.hizmet_abone_no or "").strip()
    if _b0:
        _prefix = f"{_b0.ad}_{_b0.soyad}_{_abone}" if _abone else f"{_b0.ad}_{_b0.soyad}"
    else:
        _prefix = _abone or None
    imzalanan_dosya = bot.takip_talebi_olustur_tikla(dosya_adi_prefix=_prefix)
    if imzalanan_dosya:
        time.sleep(0.5)
        bot.evrak_turu_takip_talebi_sec()
        time.sleep(0.3)
        bot.imzali_dosya_yukle(imzalanan_dosya)
        time.sleep(0.3)
        bot.yuklu_belgeyi_listeden_sec()
        time.sleep(0.2)

    time.sleep(0.3)
    bot.evrak_ekle_butonuna_tikla()

    # 7) Dayanak belge — bu takip için eşleştirilmiş PDF varsa yükle
    dayanak_yolu = dayanak_map.get(takip.dosya_no)
    if dayanak_yolu and os.path.exists(dayanak_yolu):
        print(f"Dayanak belge yükleniyor (Dosya {takip.dosya_no}): {dayanak_yolu}")
        _adim("Dayanak belge yüklenmesi")
        bot.evrak_turu_takibin_dayanagi_sec()
        time.sleep(0.5)
        bot.imzali_dosya_yukle(dayanak_yolu)
        time.sleep(0.3)
        # Yüklenen belgeyi listede seç — UYAP bazen otomatik seçmiyor
        bot.yuklu_belgeyi_listeden_sec()
        time.sleep(0.2)
        bot.evrak_ekle_butonuna_tikla()
        print("Dayanak belge evrakı eklendi.")
    elif dayanak_yolu:
        print(f"UYARI: Dayanak belge dosyası bulunamadı, atlanıyor: {dayanak_yolu}")
    else:
        print(f"Bilgi: Dosya {takip.dosya_no} için dayanak belge eşleşmesi yok, atlanıyor.")

    # 8) Vekaletname — bu takibin alacaklısı için seçilmiş vekalet varsa yükle
    vekalet_yolu = vekalet_map.get(takip.alacakli)
    if vekalet_yolu and os.path.exists(vekalet_yolu):
        print(f"Vekaletname yükleniyor ({takip.alacakli}): {vekalet_yolu}")
        _adim("Vekaletname yüklenmesi")
        bot.evrak_turu_vekaletname_sec()
        time.sleep(0.5)
        bot.imzali_dosya_yukle(vekalet_yolu)
        time.sleep(0.3)
        bot.yuklu_belgeyi_listeden_sec()
        time.sleep(0.2)
        bot.evrak_ekle_butonuna_tikla()
        print("Vekaletname evrakı eklendi.")
    elif vekalet_yolu:
        print(f"UYARI: Vekalet dosyası bulunamadı, atlanıyor: {vekalet_yolu}")
    else:
        print(f"Bilgi: '{takip.alacakli}' için vekalet seçilmemiş, atlanıyor.")

    print(f"--- Takip tamamlandı (Dosya No: {takip.dosya_no}) ---")


def pdf_dayanak_tara(klasor_yolu, takipler):
    """Klasördeki PDF'leri tarayıp takiplere göre dayanak belge eşleştirir.

    Çift doğrulama:
      1. PDF dosya adı içinde borçlunun adı/soyadı GEÇİYOR mu?
      2. PDF metni içinde hizmet_abone_no GEÇİYOR mu?
    Her iki koşul da sağlanmalıdır. {dosya_no: pdf_tam_yolu} döner."""
    import re

    try:
        import pdfplumber
    except ImportError:
        print("HATA: pdfplumber kurulu değil. Kurmak için: pip install pdfplumber")
        return {}

    if not os.path.isdir(klasor_yolu):
        print(f"HATA: PDF klasörü bulunamadı: {klasor_yolu}")
        return {}

    # Eşleştirme anahtarı: (dosya_no, hizmet_abone_no, borçlu isim parçaları)
    anahtarli = []
    for t in takipler:
        if not t.hizmet_abone_no:
            continue
        isim_parcalari = []
        for b in t.borclular:
            for kelime in (b.ad, b.soyad):
                k = kelime.strip().upper()
                if k and len(k) > 1:
                    isim_parcalari.append(k)
        anahtarli.append((t.dosya_no, t.hizmet_abone_no, isim_parcalari))

    if not anahtarli:
        print("Bilgi: Hiçbir takipte hizmet_abone_no yok; PDF eşleştirme atlandı.")
        return {}

    pdf_listesi = [os.path.join(klasor_yolu, f)
                   for f in os.listdir(klasor_yolu)
                   if f.lower().endswith(".pdf")]
    print(f"PDF tarama başlıyor: {len(pdf_listesi)} PDF, {len(anahtarli)} anahtar")

    sonuc = {}
    for pdf_yolu in pdf_listesi:
        dosya_adi_buyuk = os.path.basename(pdf_yolu).upper()
        try:
            with pdfplumber.open(pdf_yolu) as pdf:
                metin = ""
                for sayfa in pdf.pages:
                    metin += (sayfa.extract_text() or "")
            metin_buyuk = metin.upper()

            for dosya_no, abone_no, isim_parcalari in anahtarli:
                if dosya_no in sonuc:
                    continue  # zaten eşleşti

                # Koşul 1: Abone no PDF metninde geçiyor mu?
                if abone_no not in metin_buyuk and abone_no not in metin:
                    continue

                # Koşul 2: Borçlu isim parçalarından en az biri dosya adında
                #           VEYA PDF metninde geçiyor mu?
                isim_eslesti = any(
                    p in dosya_adi_buyuk or p in metin_buyuk
                    for p in isim_parcalari
                ) if isim_parcalari else True  # İsim bilgisi yoksa sadece no yeterli

                if isim_eslesti:
                    sonuc[dosya_no] = pdf_yolu
                    print(f"  Eşleşme: Dosya {dosya_no} ← "
                          f"{os.path.basename(pdf_yolu)} "
                          f"(no={abone_no}, isim OK)")
                else:
                    print(f"  Zayıf eşleşme ATLANDI: {os.path.basename(pdf_yolu)} "
                          f"no={abone_no} bulundu ama isim doğrulanamadı.")
        except Exception as e:
            print(f"  PDF okunamadı: {os.path.basename(pdf_yolu)}: {e}")

    eslesmeyen = [dn for dn, _, _ in anahtarli if dn not in sonuc]
    if eslesmeyen:
        print(f"  Eşleşmeyen takipler: {eslesmeyen}")
    print(f"PDF tarama bitti: {len(sonuc)} eşleşme.")
    return sonuc


def kaynaktan_takipler(kaynak):
    """XML veya Excel yolundan Takip listesi üretir.
    XML ise önce 'Yatay Alacak Listesi' Excel'i oluşturur, sonra okur."""
    if kaynak.lower().endswith(".xml"):
        excel_yolu = xml_to_excel(kaynak)
    else:
        excel_yolu = kaynak
    return excel_to_takipler(excel_yolu)


def main(kaynak=None):
    """UYAP XML veya hazır Excel alır; XML ise önce Excel üretir, sonra
    Excel'deki her Dosya No için bir takip açar."""
    if not kaynak:
        import tkinter as tk
        from tkinter import filedialog
        kok = tk.Tk()
        kok.withdraw()
        kaynak = filedialog.askopenfilename(
            title="UYAP XML veya Excel seçin",
            filetypes=[("XML / Excel", "*.xml *.xlsx"),
                       ("XML", "*.xml"),
                       ("Excel", "*.xlsx")],
        )
        kok.destroy()
    if not kaynak:
        print("Dosya seçilmedi, çıkılıyor.")
        return

    # XML seçildiyse önce Excel üret; Excel ise doğrudan kullan
    if kaynak.lower().endswith(".xml"):
        excel_yolu = xml_to_excel(kaynak)
    else:
        excel_yolu = kaynak

    takipler = excel_to_takipler(excel_yolu)
    print(f"{len(takipler)} takip bulundu: {[t.dosya_no for t in takipler]}")

    bot = UyapBot()
    bot.oturumla_baglan()

    for i, takip in enumerate(takipler, 1):
        print(f"\n========== {i}/{len(takipler)} ==========")
        try:
            takip_ac(bot, takip)
        except DosyaAtla as e:
            # Borçlu sorgu hatası — ekranı temizleyip sonraki dosyaya geç
            print(f"⤼ Dosya No {takip.dosya_no} atlandı (borçlu hatası): {e}")
            try:
                bot.taraflari_temizle()
            except Exception as ce:
                print(f"Temizleme sırasında hata (yok sayıldı): {ce}")
            continue
        except Exception as e:
            print(f"!!! HATA — Dosya No {takip.dosya_no} atlanıyor: {e}")
            continue

    print("\nTüm takipler tamamlandı.")
    input("Kapatmak için Enter'a basın...")


# --- ÇALIŞTIRMA ---
# Varsayılan olarak modern kontrol arayüzünü (mts_gui) başlatır.
# Eski konsol akışını çalıştırmak isterseniz:  python mts_takip_acan.py --konsol
if __name__ == "__main__":
    import sys
    if "--konsol" in sys.argv:
        main()
    else:
        import mts_gui
        mts_gui.main()