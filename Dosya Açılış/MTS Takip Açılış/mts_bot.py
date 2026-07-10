# -*- coding: utf-8 -*-
"""
MTS UYAP Botu (Playwright otomasyonu) — UyapBot sınıfı.
mts_takip_acan.py'den AYRILDI (2026-06-26). İndirme/imza: mts_indirme.
"""
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

from mts_pencere import _pencere_basligi, pencereyi_one_al
from mts_veri import _tutar_to_float
from mts_indirme import indirmeyi_yakala

INDİRME_KLASORU = os.path.join(os.path.expanduser("~"), "Downloads")


class UyapBot:

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.default_timeout = 15000
        self.optional_timeout = 2000
        self._executor = None      # asyncio ortamında playwright thread'i
        self._pw_thread_id = None  # o thread'in kimliği

    

    def _run(self, func):
        try:
            return func()
        except Exception as e:
            import traceback
            traceback.print_exc()

            print("PAGE CLOSED:", self.page.is_closed() if hasattr(self, "page") else "page yok")

            raise e

    def safe_action(self, selector, action="click", value=None, timeout=None):
        def _impl():
            if self.page is None:
                print("Hata: Sayfa nesnesi hazir degil. Once bot.oturumla_baglan() calistir.")
                return False
            _timeout = timeout or self.default_timeout
            try:
                locator = self.page.locator(selector)
                locator.wait_for(state="visible", timeout=_timeout)
                if action == "click":
                    locator.click(timeout=_timeout)
                elif action == "fill":
                    locator.fill(value)
                elif action == "type":
                    locator.type(value, delay=5)
                return True
            except PlaywrightTimeoutError:
                return False
        return self._run(_impl)

    def oturumla_baglan(self):
        # asyncio döngüsü içinde miyiz kontrol et
        import asyncio
        try:
            asyncio.get_running_loop()
            in_asyncio = True
        except RuntimeError:
            in_asyncio = False

        if in_asyncio:
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            self._pw_thread_id = self._executor.submit(threading.get_ident).result()

        def _impl():
            ag_dosya_yolu = r"C:\Users\KalkanHukuk\Desktop\UYAPDjango\uyap_session.json"
            
            self.playwright = sync_playwright().start()
            
            # 1. SADECE TEK BİR TARAYICI BAŞLATIYORUZ (Kalıcı Profil)
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir="chrome_profile",
                headless=False,
                channel="chrome",
                accept_downloads=True,
                no_viewport=True,
                args=[
                    "--start-maximized",
                    "--disable-features=DownloadBubble",
                    "--disable-features=DownloadBubbleV2",
                    "--disable-prompt-on-repost",
                    "--disable-features=AccessibilityObjectModel",
                    "--disable-features=CaretBrowsing",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-notifications",
                    "--disable-infobars",
                    "--disable-translate",
                    "--disable-save-password-bubble",
                    "--disable-default-apps",
                    "--disable-popup-blocking",
                ]
            )

            # 2. YENİ SEKME AÇMAK YERİNE, VAR OLAN İLK SEKME KULLANILIYOR
            if len(self.context.pages) > 0:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()

            # 3. OTURUM VERİLERİNİ YÜKLEME
            with open(ag_dosya_yolu, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            self.context.add_cookies(session_data["cookies"])
            
            self.page.on("dialog", lambda dialog: dialog.accept())
            self.page.goto("https://avukat.uyap.gov.tr/")

            local_storage = session_data["local_storage"]
            self.page.evaluate(f"(data) => {{ for (const key in data) {{ window.localStorage.setItem(key, data[key]); }} }}", local_storage)

            self.page.reload()
            self.page.wait_for_load_state("networkidle")
            print("Oturum yüklendi.")

            # Ağ kaydı VARSAYILAN OLARAK AÇIK — tüm XHR/fetch trafiğini
            # (uç/payload/yanıt) kaydeder. Kapatmak için: UYAP_AG_KAYDI=0
            if os.environ.get("UYAP_AG_KAYDI", "1") != "0":
                self.ag_kaydini_baslat()

        self._run(_impl)

    def ag_kaydini_baslat(self, dosya_yolu=None, anahtar_kelimeler=None):
        """Tüm XHR/fetch isteklerini (method, URL, header, payload, yanıt) bir
        JSONL dosyasına kaydeder. Amaç: UYAP'ın hangi uca hangi veriyi
        gönderip ne aldığını çıkarıp, yavaş Playwright UI yerine doğrudan
        API (self.context.request) çağrılarına geçebilmek.

        Kullanım: kaydı başlat → akışı bir kez (elle veya otomatik) yap →
        ag_kaydini_durdur(). Üretilen .jsonl dosyası endpoint haritasıdır.

        anahtar_kelimeler: yalnızca URL'inde bunlardan biri geçen istekleri
                           kaydet (None ise tüm xhr/fetch)."""
        import json as _json
        if dosya_yolu is None:
            dosya_yolu = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"ag_kaydi_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
        self._ag_kayit_yolu = dosya_yolu
        self._ag_kayit_aktif = True

        def _ilgili(url):
            if any(url.lower().endswith(e) for e in (
                    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                    ".woff", ".woff2", ".ttf", ".ico", ".map")):
                return False
            if anahtar_kelimeler:
                return any(k in url for k in anahtar_kelimeler)
            return True

        def _yaz(kayit):
            try:
                with open(dosya_yolu, "a", encoding="utf-8") as f:
                    f.write(_json.dumps(kayit, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"Ağ kaydı yazılamadı: {e}")

        def _response_handler(response):
            if not getattr(self, "_ag_kayit_aktif", False):
                return
            try:
                req = response.request
                if req.resource_type not in ("xhr", "fetch"):
                    return
                url = response.url
                if not _ilgili(url):
                    return
                try:
                    govde = response.text()
                    if isinstance(govde, str) and len(govde) > 50000:
                        govde = govde[:50000] + "...<kırpıldı>"
                except Exception:
                    govde = "<yanıt gövdesi alınamadı>"
                _yaz({
                    "zaman": time.strftime("%H:%M:%S"),
                    "method": req.method,
                    "url": url,
                    "istek_headers": dict(req.headers),
                    "istek_payload": req.post_data,
                    "durum": response.status,
                    "yanit_headers": dict(response.headers),
                    "yanit": govde,
                })
            except Exception as e:
                print(f"Ağ kaydı (response) hatası: {e}")

        self._ag_response_handler = _response_handler
        self.context.on("response", _response_handler)
        print(f"📡 Ağ kaydı başladı → {dosya_yolu}")
        return dosya_yolu

    def ag_kaydini_durdur(self):
        """Ağ kaydını durdurur ve dinleyiciyi kaldırır."""
        self._ag_kayit_aktif = False
        try:
            if getattr(self, "_ag_response_handler", None):
                self.context.remove_listener("response", self._ag_response_handler)
        except Exception:
            pass
        print(f"📡 Ağ kaydı durduruldu → {getattr(self, '_ag_kayit_yolu', '?')}")

    def dava_acilis_islemleri(self):
        time.sleep(1)
        menu_selector = "span.SidebarLabel span.me-2:has-text('Dava Açılış İşlemleri')"
        self.safe_action(menu_selector, action="click")

    def MTS_takip_acilis(self):
        time.sleep(0.5)
        mts_selector = "span.menu-text:has-text('MTS Takip Açılış')"
        self.safe_action(mts_selector, action="click")

    def il_sec_izmir(self, il="İzmir"):
        def _impl():
            print(f"İl seçiliyor: {il}")
            il_kutusu = "#il-mts"
            if self.safe_action(il_kutusu, action="click"):
                time.sleep(0.5)
                self.page.keyboard.type(il, delay=5)
                time.sleep(0.5)
                self.page.keyboard.press("Tab")
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
        self._run(_impl)

    # YENİ EKLENEN MODÜL
    def MTS_adliye_sec(self, adliye="İzmir"):
        """MTS Adliye kutusuna tıklar, ili yazar ve onaylar."""
        def _impl():
            print(f"Adliye seçiliyor: {adliye}")
            adliye_kutusu = "#adliye-mts"
            if self.safe_action(adliye_kutusu, action="click"):
                time.sleep(0.5)
                self.page.keyboard.type(adliye, delay=5)
                time.sleep(0.5)
                self.page.keyboard.press("Tab")
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
                print("Adliye seçimi tamamlandı.")
            else:
                print("Hata: Adliye kutusu bulunamadı.")
        self._run(_impl)


        
    def talep_aciklamasini_temizle_ve_gir(self, metin=""):
        """Talep açıklaması kutusunu temizler, ardından (metin verilmişse) type ile yazar."""
        def _impl():
            print("Talep açıklaması kutusu temizleniyor...")
            selector = "#talepAciklama-txt"
            if not self.safe_action(selector, action="click"):
                print("Hata: Talep açıklaması kutusu bulunamadı.")
                return
            time.sleep(0.3)
            self.page.keyboard.press("Control+A")
            time.sleep(0.2)
            self.page.keyboard.press("Backspace")
            time.sleep(0.2)
            print("Temizleme tamamlandı.")

            if metin and str(metin).strip():
                hedef = str(metin).strip()
                print(f"Talep açıklaması yazılıyor: {hedef[:60]}...")
                self.page.keyboard.type(hedef, delay=5)
                self.page.keyboard.press("Tab")
                print("Talep açıklaması yazıldı.")
            time.sleep(0.5)
        self._run(_impl)

    def mts_mahiyet_secen(self):
        """Mahiyet bilgilerini girer ve İleri butonuna tıklar."""
        def _impl():
            time.sleep(0.5)
            print("Mahiyet seçiliyor: Telefon(Cep)")
            input_selector = "#mahiyetBilgileri"
            next_button_selector = "#nextBtn"
            if self.safe_action(input_selector, action="click"):
                self.page.keyboard.type("Telefon(Cep)", delay=5)
                time.sleep(0.5)
                self.page.keyboard.press("ArrowDown")
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
                print("Mahiyet girişi tamamlandı.")
                print("İleri butonuna tıklanıyor...")
                if self.safe_action(next_button_selector, action="click"):
                    print("İleri butonuna tıklandı.")
                else:
                    print("Hata: İleri butonu bulunamadı.")
            else:
                print("Hata: Mahiyet kutusu bulunamadı.")
            time.sleep(0.5)
        self._run(_impl)
    def alacakli_secimi(self, metin: str = "tt mobi"):
        def _impl():
            print("Alacaklı seçimi süreci başlatılıyor...")
            taraf_sifati_selector = "#tarafSifati"
            if self.safe_action(taraf_sifati_selector, action="click"):
                time.sleep(0.5)
            alacakli_turu_selector = "div.dx-list-item-content:has-text('ALACAKLI')"
            if self.safe_action(alacakli_turu_selector, action="click"):
                time.sleep(0.5)
                kurum_lookup_selector = "#yetkili-kurumlar"
                if self.safe_action(kurum_lookup_selector, action="click"):
                    time.sleep(0.2)
                    # UYAP kurum araması uzun metinde her harfte yeniden
                    # tetiklenip yavaşlıyor; ayırt edici kısa bir önek yeterli.
                    # (Avukatın yetkili kurum listesi kısadır, önek tek sonucu
                    # filtreler.) Boşluksuz ilk ~12 karakteri arama metni yap.
                    arama_metni = (metin or "").strip()
                    if len(arama_metni) > 12:
                        arama_metni = arama_metni[:12]
                    print(f"Kurum adı giriliyor (arama öneki): {arama_metni}")

                    self.page.keyboard.type(arama_metni, delay=0)
                    # Sonuç listesi backend'den asenkron geldiği için sabit
                    # bekleme yerine açılır listede bir öğe görünene kadar bekle;
                    # aksi halde ArrowDown/Enter boş listeye basıp seçim yapamıyor.
                    secenek = self.page.locator(
                        ".dx-popup-content .dx-list-item, "
                        ".dx-overlay-content .dx-list-item").first
                    try:
                        secenek.wait_for(state="visible", timeout=8000)
                    except Exception:
                        print("UYARI: Alacaklı kurum sonuç listesi görünmedi "
                              "(yine de seçim deneniyor).")
                    self.page.keyboard.press("ArrowDown")
                    self.page.keyboard.press("Enter")
                    print("Alacaklı kurum seçimi tamamlandı.")
                else:
                    print("Hata: Yetkili kurumlar kutusu bulunamadı.")
            else:
                print("Hata: Alacaklı türü seçeneği bulunamadı.")
        self._run(_impl)

    def iban_bilgileri_tikla(self):
        def _impl():
            time.sleep(0.5)
            locator = self.page.locator(".hedef-accordion--title", has_text="Iban Bilgileri")
            locator.wait_for(state="visible")
            locator.click()
        self._run(_impl)
        
    def alacakli_iban_sec(self):
        def _impl():
            time.sleep(0.5)
            locator = self.page.locator(".dx-item.dx-radiobutton", has_text="Alacaklı İban")
            locator.wait_for(state="visible")
            locator.click()
        self._run(_impl)

    def vekil_iban_sec(self):
        def _impl():
            time.sleep(0.5)
            locator = self.page.locator(".dx-item.dx-radiobutton", has_text="Vekil İban")
            locator.wait_for(state="visible")
            locator.click()
        self._run(_impl)

    def vakifbank_iban_alani_tikla(self):
        time.sleep(0.5)
        """Vakıfbank IBAN giriş alanına tıklar."""
        print("Vakıfbank IBAN alanına tıklanıyor...")
        selector = "#vakıfbankIbanNo-txt"
        if self.safe_action(selector, action="click"):
            print("IBAN alanına odaklanıldı.")
        else:
            print("Hata: IBAN giriş alanı bulunamadı.")
        


    def vakifbank_iban_doldur(self, iban: str = "TR370001500158007308941128"):
        """Vakıfbank IBAN alanına tıklar ve IBAN'ı yapıştırır."""
        print(f"IBAN yapıştırılıyor: {iban}")
        selector = "#vakıfbankIbanNo-txt"
        time.sleep(0.5)

        # safe_action içinde zaten 'fill' tanımlı olduğu için doğrudan kullanıyoruz
        if self.safe_action(selector, action="fill", value=iban):
            print("IBAN başarıyla yapıştırıldı.")
            # Yapıştırdıktan sonra sitenin algılaması için bir Tab veya Enter gerekirse:
            # self.page.keyboard.press("Tab") 
        else:
            print("Hata: IBAN kutusu bulunamadı.")

    def vakifbank_iban_ekle_guncelle_tikla(self):
        """Playwright'ın önerdiği en garantici yöntemle butona tıklar."""
        def _impl():
            print("Ekle/Güncelle butonu aranıyor (Role-based)...")
            try:
                btn = self.page.get_by_role("button", name="Ekle/Güncelle").last
                btn.wait_for(state="visible", timeout=5000)
                btn.click()
                print("Ekle/Güncelle butonuna başarıyla tıklandı.")
            except Exception as e:
                print(f"Hata: Buton bulunamadı veya tıklanamadı. Detay: {e}")
                print("Alternatif yöntem deneniyor...")
                self.page.locator("span.dx-button-text", has_text="Ekle/Güncelle").last.click(force=True)
        self._run(_impl)

    def taraf_ekle(self):
        def _impl():
            self.page.wait_for_selector("#taraf-ekle-mts", state="visible", timeout=5000)
            self.page.click("#taraf-ekle-mts")
            print("Başarılı: Butona tıklandı.")
        self._run(_impl)

    def borclu_secimi(self):
        def _impl():
            print("Borçlu seçimi süreci başlatılıyor...")
            borclu_taraf_sifati_selector = "#tarafSifati"
            if self.safe_action(borclu_taraf_sifati_selector, action="click"):
                time.sleep(0.5)
            else:
                print("Taraf Sıfatı Kutusu Seçilirken Hata")
            borclu_turu_selector = "div.dx-list-item-content:has-text('BORÇLU')"
            if self.safe_action(borclu_turu_selector, action="click"):
                time.sleep(0.5)
                selector = ".dx-item:has-text('Kişi')"
                if self.safe_action(selector, action="click"):
                    print("Başarılı: 'Kişi' seçildi.")
                else:
                    try:
                        self.page.get_by_text("Kişi", exact=True).click()
                        print("Başarılı: Metin üzerinden 'Kişi' seçildi.")
                    except Exception as e:
                        print(f"Hata: 'Kişi' seçeneği bulunamadı. {e}")
            else:
                print("Taraf Sıfatı Kutusu Seçilirken Hata")
            time.sleep(0.5)
            print("Borçlu seçimi tamamlandı.")
        self._run(_impl)
    def _temizle_ve_yaz(self, selector, deger):
        """Metin alanını ÖNCE temizler (önceki borçludan kalan değeri siler),
        SONRA klavyeden yazar gibi yeni değeri girer. Alanlar borçlular arasında
        temizlenmezse 'BARANBARAN' gibi üst üste binmeler oluşuyordu."""
        def _impl():
            try:
                alan = self.page.locator(selector)
                alan.wait_for(state="visible", timeout=self.default_timeout)
                alan.click()
                time.sleep(0.2)
                try:
                    alan.fill("")                       # mevcut değeri sil
                except Exception:
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Delete")
                time.sleep(0.15)
                alan.type(str(deger).strip(), delay=8)
                return True
            except Exception as e:
                print(f"Hata: '{selector}' alanı doldurulamadı. {e}")
                return False
        return self._run(_impl)

    def borclu_tckn_ekle(self, tc_no):
        time.sleep(1)
        """TC Kimlik numarasını (alanı temizleyerek) klavyeden yazar gibi girer."""
        if self._temizle_ve_yaz("#tcKimlikNo-txt", tc_no):
            print(f"TC girildi: {tc_no}")
            return True
        print("Hata: TC giriş alanı bulunamadı.")
        return False

    def borclu_ad_ekle(self, ad):
        """Adı (alanı temizleyerek) klavyeden yazar gibi girer."""
        if self._temizle_ve_yaz("#adi-txt", ad):
            print(f"Ad başarıyla girildi: {ad}")
            return True
        print("Hata: Ad alanı (#adi-txt) bulunamadı.")
        return False

    def borclu_soyad_ekle(self, soyad):
        """Soyadı (alanı temizleyerek) klavyeden yazar gibi girer."""
        if self._temizle_ve_yaz("#soyadi-txt", soyad):
            print(f"Soyad başarıyla girildi: {soyad}")
            return True
        print("Hata: Soyad alanı (#soyadi-txt) bulunamadı.")
        return False
            
    def borclu_sorgula_buton_tikla(self):
        if self.safe_action("#sorgula-mts", action="click"):
            print("Borçlu sorgula butonuna tıklandı.")

    def adres_bilgileri_tikla(self):
        """'Adres Bilgileri' akordeon başlığına tıklar.

        Birden çok 'Adres Bilgileri' eşleşmesi olabildiğinden .first kullanılır
        (aksi halde strict-mode hatası tüm dosyayı düşürüyordu). Eleman görünür
        alana kaydırılır — borçlular biriktikçe sayfa uzayıp başlık görüşten
        çıkabiliyor."""
        def _impl():
            print("'Adres Bilgileri' açılıyor...")
            sel = "div.hedef-accordion--title:has-text('Adres Bilgileri')"
            try:
                bsl = self.page.locator(sel).first
                bsl.wait_for(state="visible", timeout=5000)
                bsl.scroll_into_view_if_needed(timeout=2000)
                bsl.click()
                print("Başarılı: Adres Bilgileri bölümüne tıklandı.")
                return True
            except Exception:
                try:
                    alt = self.page.get_by_text("Adres Bilgileri", exact=True).first
                    alt.scroll_into_view_if_needed(timeout=2000)
                    alt.click()
                    print("Başarılı: Metin yoluyla tıklandı.")
                    return True
                except Exception as e:
                    print(f"Hata: Adres Bilgileri butonu bulunamadı. {e}")
                    return False
        return self._run(_impl)

    def mernis_kullan_checkbox_tikla(self, index=0):
        """'Mernis adresini kullan' checkbox'ına tıklar.

        Yalnızca GÖRÜNÜR checkbox'lar arasından seçer — kapalı akordeondaki gizli
        kutuları ya da eklenmiş taraf kartlarındaki kutuları yanlışlıkla seçmemek
        için. index<0 → görünür son checkbox (aktif formun mernis kutusu)."""
        def _impl():
            try:
                kutular = self.page.locator(".dx-checkbox-icon:visible")
                try:
                    adet = kutular.count()
                except Exception:
                    adet = 0
                if adet == 0:
                    print("Hata: Görünür checkbox bulunamadı.")
                    return False
                target = kutular.last if index < 0 else kutular.nth(index)
                print(f"Checkbox seçiliyor (görünür={adet}, index={index}).")
                target.scroll_into_view_if_needed(timeout=2000)
                target.wait_for(state="visible", timeout=5000)
                target.click()
                print("Checkbox başarıyla tıklandı.")
                return True
            except Exception as e:
                print(f"Hata: Checkbox tıklanamadı. Detay: {e}")
                return False
        return self._run(_impl)

    def adres_mernis_kullan(self):
        """'Adres Bilgileri' akordeonunu GEREKİYORSA açar ve 'Mernis adresini
        kullan' checkbox'ını işaretler.

        Akordeonun açık/kapalı durumu borçlular arasında korunduğundan körlemesine
        tıklamak (toggle) bazen onu KAPATIP 'Lütfen adres ekleyiniz' hatasına yol
        açıyordu. Bunun yerine: mernis checkbox'ı GÖRÜNÜR olana kadar akordeonu
        açmayı dener (açıksa hiç dokunmaz), sonra kutuyu işaretler. İdempotent."""
        def _impl():
            baslik = "div.hedef-accordion--title:has-text('Adres Bilgileri')"
            cb_sel = ".dx-checkbox-icon:visible"
            acildi = False
            for _ in range(3):
                # Mernis kutusu zaten görünüyorsa akordeon açıktır → dokunma.
                if self.page.locator(cb_sel).count() > 0:
                    acildi = True
                    break
                # Görünmüyorsa akordeonu aç.
                try:
                    b = self.page.locator(baslik).first
                    b.wait_for(state="visible", timeout=5000)
                    b.scroll_into_view_if_needed(timeout=2000)
                    b.click()
                    print("'Adres Bilgileri' akordeonu açıldı.")
                except Exception as e:
                    print(f"Adres Bilgileri akordeonu tıklanamadı: {e}")
                time.sleep(0.6)
            if not acildi and self.page.locator(cb_sel).count() == 0:
                print("Hata: Mernis checkbox görünür değil — adres eklenemedi.")
                return False
            try:
                kutu = self.page.locator(cb_sel).last
                kutu.scroll_into_view_if_needed(timeout=2000)
                kutu.click()
                print("'Mernis adresini kullan' işaretlendi.")
                time.sleep(0.3)
                return True
            except Exception as e:
                print(f"Hata: Mernis checkbox işaretlenemedi: {e}")
                return False
        return self._run(_impl)

    def taraf_ekle_butonuna_tikla(self):
        """'Taraf Ekle' butonuna tıklar (ID: #taraf-ekle-mts)."""
        selector = "#taraf-ekle-mts"
        print("Taraf Ekle butonuna tıklanıyor...")
        
        if self.safe_action(selector, action="click"):
            print("Başarıyla 'Taraf Ekle' butonuna tıklandı.")
            return True
        else:
            try:
                # Alternatif olarak doğrudan locator ile tıklama denemesi
                self.page.locator(selector).click()
                print("Başarıyla locator üzerinden 'Taraf Ekle' tıklandı.")
                return True
            except Exception as e:
                print(f"Hata: Taraf Ekle butonu tıklanamadı. Detay: {e}")
                return False

    def taraf_giris_ilerle_buton_tikla(self):
        selector = ".dx-button:has-text('İleri')"
        
        if self.safe_action(selector, action="click"):
            print("Başarılı: 'İleri' butonuna tıklandı.")

    def ilamsiz_ekle_buton(self):
        selector = ".dx-button:has-text('İlamsız Ekle')"
        
        if self.safe_action(selector, action="click"):
            print("Başarılı: 'İlamsız' butonuna tıklandı.")
    def ilamsiz_abone_musteri_no_yaz(self, metin):
        selector = "#aboneNo-txt"
        print(f"Abone No giriliyor: {metin}")
        
        if self.safe_action(selector, action="click"):
            if self.safe_action(selector, action="type", value=str(metin)):
                print("Abone No başarıyla yazıldı.")
                return True
        
        print("Hata: Abone No alanı bulunamadı veya yazılamadı.")
        return False
    def ilamsiz_tutar_alan_doldur(self, deger):
        """role='spinbutton' olan sayısal alana tıklar ve değeri yazar."""
        selector = "input[role='spinbutton']"
        print(f"Sayısal alana değer giriliyor: {deger}")
        
        # Alana odaklanmak için önce tıkla, ardından type ile yaz
        if self.safe_action(selector, action="click"):
            if self.safe_action(selector, action="type", value=str(deger)):
                print("Sayısal değer başarıyla yazıldı.")
                return True
                
        print("Hata: Sayısal giriş alanı bulunamadı veya yazılamadı.")
        return False
    
    def ilamsiz_fatura_tarih_gir(self, tarih_metni):
        """Fatura Tarihi alanındaki görünür input'a tıklar ve tarihi yazar."""
        # Gizli input'u eleyip sadece sınıfı 'dx-texteditor-input' olanı seçiyoruz
        selector = "#fatura-tarihi input.dx-texteditor-input"
        print(f"Fatura Tarihi giriliyor: {tarih_metni}")
        
        try:
            self.page.locator(selector).click()
            self.page.locator(selector).fill("")
            self.page.locator(selector).type(str(tarih_metni), delay=5)
            print("Fatura tarihi başarıyla girildi.")
            time.sleep(1)
            self.page.keyboard.press("Enter")
            return True
        except Exception as e:
            print(f"Hata: Fatura tarihi alanı bulunamadı. {e}")
            return False
    def ilamsiz_odeme_tarihi_gir_ve_enter(self, tarih="22062026"):
        """Ödeme Tarihi alanına tıklar, tarihi yazar ve Enter'a basar."""
        # Görünür olan metin giriş alanını hedefliyoruz
        selector = "#odeme-tarihi input.dx-texteditor-input"
        print(f"Ödeme Tarihi giriliyor: {tarih}")
        
        try:
            # 1. Alana tıkla ve odaklan
            self.page.locator(selector).click()
            # 2. Varsa eski içeriği temizle
            self.page.locator(selector).fill("")
            # 3. Tarihi karakter karakter yaz
            self.page.locator(selector).type(str(tarih), delay=5)
            
            # 4. Sistemin veriyi işlemesi için 1 saniye bekle
            self.page.wait_for_timeout(1000)
            
            # 5. Enter tuşuna basarak onayla
            self.page.keyboard.press("Enter")
            
            print("Ödeme tarihi başarıyla girildi ve Enter'a basıldı.")
            return True
        except Exception as e:
            print(f"Hata: Ödeme tarihi işlemi başarısız. {e}")
            return False
        
    def ilamsiz_aciklama_gir(self, metin="Açıklama"):
        """Açıklama alanına tıklar ve belirtilen metni yazar."""
        # ID altındaki textarea elemanını hedefliyoruz
        selector = "#aciklama-ilamsiz-mts textarea.dx-texteditor-input"
        metin = "" if metin is None else str(metin)
        if not metin.strip():
            print("Bilgi: Açıklama (H sütunu) boş — yazma atlandı.")
            return True
        print(f"Açıklama giriliyor: {metin}")

        try:
            alan = self.page.locator(selector)
            alan.wait_for(state="visible", timeout=self.default_timeout)
            hedef = metin.strip()

            # Önce fill ile dene (en güvenilir); tutmazsa klavye type ile tekrar.
            for yontem in ("fill", "type"):
                alan.click()
                alan.focus()
                time.sleep(0.15)
                #if yontem == "fill":
                    #alan.fill(metin)
                #else:
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Backspace")
                self.page.keyboard.type(metin, delay=4)
                self.page.keyboard.press("Tab")

                try:
                    yazilan = alan.input_value().strip()
                except Exception:
                    yazilan = ""

                if yazilan == hedef:
                    print(f"Açıklama başarıyla yazıldı ({yontem}).")
                    return True

                print(f"Uyarı: Açıklama yazımı tutmadı ({yontem}). Okunan='{yazilan}'")
                time.sleep(0.25)

            print("Hata: Açıklama alanına metin yazılamadı (fill ve type denendi).")
            return False
        except Exception as e:
            print(f"Hata: Açıklama yazılamadı. {e}")
            return False
    def ilamsiz_ekle_butonuna_tikla(self):
        """'Ekle' butonuna tıklar (ID: #ekle-btn-mts)."""
        selector = "#ekle-btn-mts"
        print("Ekle butonuna tıklanıyor...")
        if self.safe_action(selector, action="click"):
            print("Başarıyla 'Ekle' butonuna tıklandı.")
            return True
            time.sleep(3)
        return False
    def ilamsiz_ileri_butonuna_tikla(self):
        time.sleep(2)
        """İleri butonuna tıklar."""
        selector = "#nextBtn"

        print("İleri butonuna tıklanıyor...")

        try:
            if self.page.is_closed():
                print("Hata: Sayfa kapanmış.")
                return False

            buton = self.page.locator(selector)

            buton.wait_for(
                state="visible",
                timeout=5000
            )

            buton.click()

            print("Başarıyla İleri butonuna tıklandı.")
            return True

        except Exception as e:
            print(f"Hata: İleri butonu bulunamadı veya tıklanamadı. Detay: {e}")
            return False
    def alacak_kalemi_ekle_butonuna_tikla(self):
        """'Alacak Kalemi Ekle' butonuna aria-label üzerinden tıklar."""
        selector = 'div[aria-label="Alacak Kalemi Ekle"]'
        print("Alacak Kalemi Ekle butonuna tıklanıyor...")
        
        try:
            # Butonun görünür olmasını bekle ve tıkla
            self.page.locator(selector).wait_for(state="visible", timeout=5000)
            self.page.locator(selector).click()
            print("Başarıyla 'Alacak Kalemi Ekle' butonuna tıklandı.")
            return True
        except Exception as e:
            print(f"Hata: 'Alacak Kalemi Ekle' butonuna tıklanamadı. Detay: {e}")
            return False
    def alacak_turu_ac(self):

        """'Alacak Türü' açılır kutusuna tıklar ve seçenek listesini açar."""
        selector = "#alacakTuru"
        print("Alacak Türü alanı açılıyor...")
        
        try:
            # Elemanın görünür olmasını bekle ve tıkla
            self.page.locator(selector).wait_for(state="visible", timeout=5000)
            self.page.locator(selector).click()
            print("Başarıyla 'Alacak Türü' alanına tıklandı.")
            return True
        except Exception as e:
            print(f"Hata: 'Alacak Türü' alanı bulunamadı veya tıklanamadı. Detay: {e}")
            return False
    def alacak_turu_asil_alacagi_sec(self):
        """Açılır menüden 'Asıl Alacağı' seçeneğini bulur ve seçer."""
        print("Açılır menüden 'Asıl Alacağı' seçeneği seçiliyor...")
        
        try:
            # HTML'deki role="option" ve "Asıl Alacağı" metnini hedefliyoruz
            secenek = self.page.get_by_role("option", name="Asıl Alacağı")
            
            # Menünün yüklenmesi için görünür olmasını bekle ve tıkla
            secenek.wait_for(state="visible", timeout=3000)
            secenek.click()
            
            print("Başarıyla 'Asıl Alacağı' seçeneği seçildi.")
            return True
        except Exception as e:
            print(f"Hata: 'Asıl Alacağı' seçeneği seçilemedi. Detay: {e}")
            return False
    def alacak_aciklamasi_gir(self, aciklama="test alacağı"):
        """'Alacak Açıklama' alanına tıklar ve belirtilen açıklamayı yazar."""
        print(f"Alacak açıklaması giriliyor: {aciklama}")
        
        try:
            # aria-label="Alacak Açıklama" olan metin alanını hedefliyoruz
            alan = self.page.get_by_role("textbox", name="Alacak Açıklama")
            
            # 1. Alana tıkla ve odaklan
            alan.click()
            # 2. Varsa içindeki eski metni temizle
            alan.fill("")
            # 3. Yeni açıklamayı güvenli bir şekilde yaz
            alan.type(aciklama, delay=5)
            
            print("Alacak açıklaması başarıyla girildi.")
            return True
        except Exception as e:
            print(f"Hata: Alacak açıklaması girilemedi. Detay: {e}")
            return False
    def alacak_tutar_gir(self, miktar="1280,78"):
        """'Tutar' alanına tıklar, temizler ve kuruşlu miktarı yazar."""
        print(f"Tutar giriliyor: {miktar}")
        
        try:
            # aria-label="Tutar" olan sayı kutusunu tam isabet hedefliyoruz
            alan = self.page.get_by_role("spinbutton", name="Tutar")
            
            # 1. Alana odaklanmak için tıkla
            alan.click()
            # 2. Kutuda varsayılan veya eski bir değer varsa temizle
            alan.fill("")
            # 3. Kuruşlu tutarı güvenli bir gecikmeyle yazdır
            alan.type(str(miktar), delay=5)
            
            print("Tutar başarıyla girildi.")
            return True
        except Exception as e:
            print(f"Hata: Tutar alanı doldurulamadı. Detay: {e}")
            return False
    def alacak_faiz_turu_diger_sec(self):
        """'Faiz Türü' alanına tıklar, 0.5 saniye bekler ve 'Diğer' seçeneğini seçer."""
        combobox_selector = "#faizTuru"
        print("Faiz Türü açılır kutusuna tıklanıyor...")
        
        try:
            # 1. Faiz Türü kutusunun görünür olmasını bekle ve tıkla
            self.page.locator(combobox_selector).wait_for(state="visible", timeout=5000)
            self.page.locator(combobox_selector).click()
            
            # 2. İstendiği gibi tam 0.5 saniye (500 milisaniye) bekle
            print("0.5 saniye bekleniyor...")
            self.page.wait_for_timeout(500)
            
            # 3. Liste açıldığında 'Diğer' seçeneğine tıkla
            print("'Diğer' seçeneği seçiliyor...")
            secenek = self.page.get_by_role("option", name="Diğer")
            secenek.wait_for(state="visible", timeout=3000)
            secenek.click()
            
            print("Başarıyla 'Diğer' faiz türü seçildi.")
            return True
        except Exception as e:
            print(f"Hata: Faiz türü adımları tamamlanamadı. Detay: {e}")
            return False
    def alacak_faiz_turu_reeskont_sec(self):
        """'Faiz Türü' alanına tıklar, 0.5 saniye bekler ve 'Reeskont' seçeneğini seçer."""
        combobox_selector = "#faizTuru"
        print("Faiz Türü açılır kutusuna tıklanıyor...")
        
        try:
            # 1. Faiz Türü kutusunun görünür olmasını bekle ve tıkla
            self.page.locator(combobox_selector).wait_for(state="visible", timeout=5000)
            self.page.locator(combobox_selector).click()
            
            # 2. İstendiği gibi tam 0.5 saniye (500 milisaniye) bekle
            print("0.5 saniye bekleniyor...")
            self.page.wait_for_timeout(500)
            
            # 3. Liste açıldığında 'Reeskont' seçeneğine tıkla
            print("'Reeskont' seçeneği seçiliyor...")
            secenek = self.page.get_by_role("option", name="Reeskont")
            secenek.wait_for(state="visible", timeout=3000)
            secenek.click()
            
            print("Başarıyla 'Reeskont' faiz türü seçildi.")
            return True
        except Exception as e:
            print(f"Hata: Faiz türü adımları tamamlanamadı. Detay: {e}")
            return False
    def alacak_faiz_turu_sec(self, tur_adi: str = "Diğer"):
        """'Faiz Türü' açılır kutusunu açar ve verilen türü seçer.
        tur_adi = XML'deki faizTipKodAciklama (örn 'Diğer', 'Reeskont Avans').
        Önce birebir eşleşme, bulunamazsa içeren eşleşme denenir."""
        combobox_selector = "#faizTuru"
        tur_adi = (tur_adi or "").strip() or "Diğer"
        print(f"Faiz türü seçiliyor: {tur_adi}")

        def _impl():
            try:
                self.page.locator(combobox_selector).wait_for(state="visible", timeout=5000)
                self.page.locator(combobox_selector).click()
                self.page.wait_for_timeout(500)

                secenek = self.page.get_by_role("option", name=tur_adi, exact=True)
                try:
                    secenek.wait_for(state="visible", timeout=2000)
                except Exception:
                    # Birebir bulunamazsa içeren eşleşmeye düş
                    secenek = self.page.get_by_role("option", name=tur_adi).first
                    secenek.wait_for(state="visible", timeout=3000)
                secenek.click()
                print(f"Başarıyla '{tur_adi}' faiz türü seçildi.")
                return True
            except Exception as e:
                print(f"Hata: '{tur_adi}' faiz türü seçilemedi. Detay: {e}")
                return False
        return self._run(_impl)
    def alacak_faiz_orani_gir(self, oran="19,20"):
        """'Faiz Oranı' alanına tıklar, temizler ve belirtilen oranı yazar.

        NOT: 'Reeskont Avans' / 'Yasal' gibi faiz türlerinde UYAP oranı kendisi
        doldurur ve alanı kilitler. Bu durumda alan düzenlenebilir olmadığından
        yazma atlanır (aksi halde tıklama/yazma takılır)."""
        print(f"Faiz Oranı giriliyor: {oran}")

        try:
            # aria-label="Faiz Oranı" olan sayı kutusunu hedefliyoruz
            alan = self.page.get_by_role("spinbutton", name="Faiz Oranı")
            alan.wait_for(state="visible", timeout=4000)

            # Alan sistemce kilitlenmişse (otomatik dolu) yazma — atla
            try:
                if not alan.is_editable():
                    print("Bilgi: Faiz oranı alanı düzenlenemiyor (otomatik dolu) — yazma atlandı.")
                    return True
            except Exception:
                pass

            # 1. Alana odaklanmak için tıkla
            alan.click()
            # 2. Kutuda varsayılan veya eski bir değer varsa temizle
            alan.fill("")
            # 3. Oranı güvenli bir gecikmeyle yazdır
            alan.type(str(oran), delay=5)

            print("Faiz oranı başarıyla girildi.")
            return True
        except Exception as e:
            print(f"Hata: Faiz oranı alanı doldurulamadı (atlanıyor). Detay: {e}")
            return False
    def alacak_faiz_sure_tipi_yillik_sec(self):
        """'Faiz Süre Tipi' alanına tıklar, 0.5 saniye bekler ve 'Yıllık' seçeneğini seçer."""
        combobox_selector = "#faizSureTipi"
        print("Faiz Süre Tipi açılır kutusuna tıklanıyor...")
        
        try:
            # 1. Faiz Süre Tipi kutusunun görünür olmasını bekle ve tıkla
            self.page.locator(combobox_selector).wait_for(state="visible", timeout=5000)
            time.sleep(0.7)
            self.page.locator(combobox_selector).click()
            
            # 2. İstendiği gibi tam 0.5 saniye (500 milisaniye) bekle
            print("0.5 saniye bekleniyor...")
            self.page.wait_for_timeout(500)
            
            # 3. Liste açıldığında 'Yıllık' seçeneğine tıkla
            print("'Yıllık' seçeneği seçiliyor...")
            secenek = self.page.get_by_role("option", name="Yıllık")
            secenek.wait_for(state="visible", timeout=3000)
            secenek.click()
            
            print("Başarıyla 'Yıllık' faiz süre tipi seçildi.")
            return True
        except Exception as e:
            print(f"Hata: Faiz süre tipi adımları tamamlanamadı. Detay: {e}")
            return False
    def alacak_ekle_mts_butonuna_tikla(self):
        """Görseldeki id='ekle-mts' olan Ekle butonuna tıklar."""
        selector = "#ekle-mts"
        print("Görseldeki Ekle (#ekle-mts) butonuna tıklanıyor...")
        
        # safe_action kendi içinde beklemeyi ve tıklamayı yapıyor
        if self.safe_action(selector, action="click"):
            print("Başarılı: 'Ekle' butonuna tıklandı.")
            return True
        else:
            # Alternatif olarak direkt Playwright locator ile zorla tıklama denemesi (Fallback)
            def _impl():
                try:
                    print("Normal tıklama başarısız, alternatif (force) yöntem deneniyor...")
                    self.page.locator(selector).click(force=True, timeout=3000)
                    print("Başarılı: Alternatif yöntemle 'Ekle' butonuna tıklandı.")
                    return True
                except Exception as e:
                    print(f"Hata: 'Ekle' butonu kesinlikle tıklanamadı. Detay: {e}")
                    return False
            return self._run(_impl)
        time.sleep(1)
        try:
            self.page.get_by_role("button", name="Tamam").click()
            print("Tamam butonuna tıklandı.")
            return True
        except Exception as e:
            print(f"Tamam butonuna tıklanamadı: {e}")
            return False
    def ekle_tamam_butonuna_tikla(self):
        """SweetAlert2 Tamam butonuna tıklar."""
        try:
            buton = self.page.locator("button.swal2-confirm", has_text="Tamam")
            buton.wait_for(state="visible", timeout=5000)
            buton.click()
            print("Tamam butonuna başarıyla tıklandı.")
            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"Tamam butonuna tıklanamadı: {e}")
            return False

    def swal_tamam_varsa_kapat(self, timeout=2000):
        """SweetAlert2 'Tamam' uyarısı VARSA kapatır; yoksa sessizce geçer.
        Uyarının her zaman çıkmadığı (opsiyonel) adımlarda kullanılır; uyarı
        çıkmazsa hata basmaz, akışı bloklamaz."""
        try:
            buton = self.page.locator("button.swal2-confirm", has_text="Tamam")
            buton.wait_for(state="visible", timeout=timeout)
            buton.click()
            print("SweetAlert 'Tamam' uyarısı kapatıldı.")
            time.sleep(0.5)
            return True
        except Exception:
            print("Bilgi: Kapatılacak 'Tamam' uyarısı çıkmadı, devam ediliyor.")
            return False

    def borclu_sorgu_hatasi(self, timeout=2500):
        """Borçlu 'Sorgula' sonrası UYAP uyarı popup'ı (mernis eşleşmedi /
        vefat / mernis adresi bulunamadı / soyadı değişikliği vb.) çıktıysa
        mesajını döndürür ve popup'ı kapatır. Uyarı çıkmazsa None döner.

        SweetAlert2 yapısı:
          - Uyarı ikonu : div.swal2-icon.swal2-warning (veya .swal2-error)
          - Mesaj       : #swal2-html-container
          - Onay butonu : button.swal2-confirm ('Tamam')"""
        def _impl():
            try:
                ikon = self.page.locator(
                    "div.swal2-icon.swal2-warning, div.swal2-icon.swal2-error")
                ikon.first.wait_for(state="visible", timeout=timeout)
            except Exception:
                return None                      # uyarı yok → sorgu başarılı
            mesaj = ""
            try:
                mesaj = self.page.locator(
                    "#swal2-html-container").inner_text(timeout=1500).strip()
            except Exception:
                pass
            # Popup'ı kapat (Tamam) — ekranı temizlemeye hazır hale getir
            try:
                btn = self.page.locator("button.swal2-confirm", has_text="Tamam")
                btn.click(timeout=2000)
                time.sleep(0.4)
            except Exception:
                pass
            return mesaj or "UYAP borçlu sorgu uyarısı"
        return self._run(_impl)

    def acik_uyari_mesaji(self, timeout=2500):
        """Görünür bir SweetAlert2 UYARI/HATA popup'ı varsa mesajını döndürür
        ama KAPATMAZ (kullanıcı tarayıcıda görüp elle müdahale edebilsin).
        Yoksa None. Başarı/'Tamam' bilgi popup'larını tetiklememek için yalnızca
        swal2-warning / swal2-error ikonlu popup'lara bakar."""
        def _impl():
            try:
                ikon = self.page.locator(
                    "div.swal2-icon.swal2-warning, div.swal2-icon.swal2-error")
                ikon.first.wait_for(state="visible", timeout=timeout)
            except Exception:
                return None
            for sel in ("#swal2-html-container", "#swal2-title"):
                try:
                    t = self.page.locator(sel).inner_text(timeout=800).strip()
                    if t:
                        return t
                except Exception:
                    pass
            return "UYAP uyarısı"
        return self._run(_impl)

    def taraflari_temizle(self, max_silme=30):
        """'Taraflar' listesindeki tüm tarafları (alacaklı + borçlular) siler.
        Hatalı dosyadan sonra ekranı bir sonraki takibe hazır hale getirmek için
        kullanılır. Sil butonu: kart sağ üstündeki kırmızı (danger) 'x' ikonu."""
        def _impl():
            sil_sec = ("div.dx-button-danger[title='sil'], "
                       "div.dx-button-danger[aria-label='fe icon-x']")
            silinen = 0
            for _ in range(max_silme):
                sil_btn = self.page.locator(sil_sec)
                try:
                    if sil_btn.count() == 0:
                        break
                    sil_btn.first.click(timeout=3000)
                    silinen += 1
                    time.sleep(0.4)
                    # Silme onayı (Evet/Tamam) çıkarsa kapat
                    try:
                        onay = self.page.locator("button.swal2-confirm")
                        onay.wait_for(state="visible", timeout=1200)
                        onay.click()
                        time.sleep(0.3)
                    except Exception:
                        pass
                except Exception:
                    break
            print(f"Ekran temizlendi: {silinen} taraf silindi.")
            return silinen
        return self._run(_impl)

    def taraf_sayisi(self):
        """'Taraflar' listesine eklenmiş taraf (alacaklı + borçlular) sayısını
        döndürür. Her tarafın kartında sağ üstte bir 'sil' (x) butonu var;
        bunları sayar. Hata olursa -1 döner (doğrulama 'bilinmiyor' sayar)."""
        def _impl():
            try:
                sil = self.page.locator(
                    "div.dx-button-danger[title='sil'], "
                    "div.dx-button-danger[aria-label='fe icon-x']")
                return sil.count()
            except Exception:
                return -1
        return self._run(_impl)

    def sayfada_metin_var_mi(self, metin):
        """Sayfa gövdesinin görünür metninde verilen ifade geçiyor mu?
        (Taraf kimlik no / alacaklı adı gibi değerleri doğrulamak için.)"""
        def tr_lower(s):
            if not s:
                return ""
            return s.replace("İ", "i").replace("I", "ı").lower()

        def _impl():
            try:
                govde = self.page.locator("body").inner_text(timeout=3000)
                target = tr_lower(metin)
                source = tr_lower(govde)
                return (target in source) if target else False
            except Exception:
                return False
        return self._run(_impl)

    def mevcut_asama_tespit(self):
        """Oturumun MTS akışında ŞU AN hangi aşamada olduğunu sayfadaki görünür
        işaretçilerden tahmin eder. (anahtar, insan_okunur_etiket) döndürür.

        Akışı yeniden başlatmaz; yalnızca hata/duraklama sonrası kullanıcıya
        'neredesin' bilgisi vermek içindir. Sayfa gövdesini bir kez okuyup, en
        İLERİ aşamadan en erkene doğru kontrol eder; ilk eşleşeni döndürür."""
        def _impl():
            def tr_lower(s):
                if not s:
                    return ""
                return s.replace("İ", "i").replace("I", "ı").lower()
            try:
                govde = tr_lower(self.page.locator("body").inner_text(timeout=3000))
            except Exception:
                govde = ""

            def metin_var(m):
                t = tr_lower(m)
                return bool(t) and t in govde

            def gorunur(selector):
                try:
                    loc = self.page.locator(selector)
                    return loc.count() > 0 and loc.first.is_visible()
                except Exception:
                    return False

            def rol_gorunur(rol, ad):
                try:
                    return self.page.get_by_role(rol, name=ad).first.is_visible()
                except Exception:
                    return False

            # En İLERİ aşamadan en erkene doğru — ilk eşleşme kazanır.
            if (rol_gorunur("combobox", "Evrak Türü")
                    or metin_var("Evrak Ekle") or metin_var("Belge Ekle")):
                return ("evrak", "Evrak yükleme (takip talebi / dayanak / vekalet)")
            if metin_var("veri girişini onaylıyorum") or metin_var("Takip Talebi Oluştur"):
                return ("onay_imza", "Onay / e-imza (takip talebi oluşturma)")
            if metin_var("Harç") and metin_var("Masraf"):
                return ("harc_masraf", "Harç / masraf özeti")
            if gorunur('div[aria-label="Alacak Kalemi Ekle"]') or metin_var("Alacak Kalemi"):
                return ("alacak_kalemleri", "Alacak kalemleri girişi")
            if gorunur("#aboneNo-txt") or metin_var("İlamsız Ekle"):
                return ("ilamsiz", "İlamsız alacak bilgileri (abone / tutar / tarih)")
            if (gorunur("#yetkili-kurumlar") or gorunur("#tarafSifati")
                    or gorunur("#taraf-ekle-mts")):
                return ("taraflar", "Taraf girişi (alacaklı / borçlu / IBAN)")
            if metin_var("Mahiyet") or metin_var("Adliye"):
                return ("acilis", "Açılış (il / adliye / mahiyet)")
            return ("bilinmiyor", "Aşama tespit edilemedi — sayfayı elle kontrol edin")
        try:
            return self._run(_impl)
        except Exception:
            return ("bilinmiyor", "Aşama tespit edilemedi")

    def alacak_turu_masraf_alacagi_sec(self):
        """Açılır menüden 'Asıl Alacağı' seçeneğini bulur ve seçer."""
        print("Açılır menüden 'Asıl Alacağı' seçeneği seçiliyor...")
        
        try:
            # HTML'deki role="option" ve "Asıl Alacağı" metnini hedefliyoruz
            secenek = self.page.get_by_role("option", name="Masraf Alacağı")
            
            # Menünün yüklenmesi için görünür olmasını bekle ve tıkla
            secenek.wait_for(state="visible", timeout=3000)
            secenek.click()
            
            print("Başarıyla 'Masraf Alacağı' seçeneği seçildi.")
            return True
        except Exception as e:
            print(f"Hata: 'Masraf Alacağı' seçeneği seçilemedi. Detay: {e}")
            return False
    def alacak_turu_faiz_alacagi_sec(self):
        """Açılır menüden 'Faiz Alacağı' seçeneğini bulur ve seçer."""
        print("Açılır menüden 'Faiz Alacağı' seçeneği seçiliyor...")
        
        try:
            # HTML'deki role="option" ve "Faiz Alacağı" metnini hedefliyoruz
            secenek = self.page.get_by_role("option", name="Faiz Alacağı")
            
            # Menünün yüklenmesi için görünür olmasını bekle ve tıkla
            secenek.wait_for(state="visible", timeout=3000)
            secenek.click()
            
            print("Başarıyla 'Faiz Alacağı' seçeneği seçildi.")
            return True
        except Exception as e:
            print(f"Hata: 'Faiz Alacağı' seçeneği seçilemedi. Detay: {e}")
            return False
    def masraf_toplam_tutar_al(self):
        """'Harç / Masraf Bilgileri' kartındaki 'Toplam Tutar:' değerini okur.
        Örn HTML: <div class="hedef-card ...">...Harç / Masraf Bilgileri...
                    <div class="fw-bold">Toplam Tutar:  1000,00 </div></div>

        Sayfada birden fazla 'Toplam Tutar' olduğundan (ör. başka bir kartta 0,00)
        okuma DOĞRUDAN Harç/Masraf kartıyla sınırlanır. Bulunamazsa yedek olarak
        sıfır-OLMAYAN ilk 'Toplam Tutar' alınır. Dönüş: '1000,00' gibi metin/None."""
        def _impl():
            try:
                import re
                desen = re.compile(
                    r"Toplam\s*Tutar\s*:?\s*([\d\.]*\d(?:,\d+)?)", re.IGNORECASE)

                def _ayikla(metin):
                    m = desen.search(metin or "")
                    return m.group(1).strip() if m else None

                # Harç toplamı sayfa açılınca kısa süre 0,00 görünüp sonra
                # hesaplanıyor; bu yüzden SIFIR-OLMAYAN değer görene kadar
                # ~10 sn pollarız. (Gerçekten harç yoksa süre sonunda None döner.)
                for _ in range(20):
                    cerceveler = [self.page] + list(self.page.frames)
                    for cf in cerceveler:
                        # 1) DOĞRU HEDEF: 'Harç'/'Masraf' başlıklı kartın içindeki
                        #    Toplam Tutar (başka kartlardaki 0,00'ı kapmaz).
                        try:
                            kart = cf.locator("div.hedef-card").filter(
                                has_text="Harç")
                            tt = kart.locator("div.fw-bold",
                                              has_text="Toplam Tutar")
                            for i in range(tt.count()):
                                tutar = _ayikla(tt.nth(i).inner_text())
                                if tutar and _tutar_to_float(tutar) > 0:
                                    print(f"Toplam Tutar okundu "
                                          f"(Harç/Masraf kartı): {tutar}")
                                    return tutar
                        except Exception:
                            pass
                        # 2) YEDEK: genel fw-bold 'Toplam Tutar' — yine yalnızca
                        #    sıfır-OLMAYAN değeri kabul et (0,00 ya yanlış kart ya
                        #    da henüz hesaplanmamış demektir).
                        try:
                            el = cf.locator("div.fw-bold:visible",
                                            has_text="Toplam Tutar")
                            for i in range(el.count()):
                                tutar = _ayikla(el.nth(i).inner_text())
                                if tutar and _tutar_to_float(tutar) > 0:
                                    print(f"Toplam Tutar okundu (yedek): {tutar}")
                                    return tutar
                        except Exception:
                            pass
                    self.page.wait_for_timeout(500)

                print("UYARI: Sıfır-olmayan 'Harç/Masraf Toplam Tutar' bulunamadı.")
                return None
            except Exception as e:
                print(f"Toplam Tutar okunamadı: {e}")
                return None
        return self._run(_impl)

    def geri_butonuna_tikla(self):
        """Veri girişi onay sayfasından bir önceki (alacak kalemleri) sayfaya
        dönmek için 'çift sol ok' (fa-angle-double-left) butonuna tıklar."""
        def _impl():
            try:
                ikon = self.page.locator("i.fa-angle-double-left").first
                ikon.wait_for(state="visible", timeout=5000)
                # İkonun kendisi yerine tıklanabilir atasına (buton) tıkla
                ikon.click()
                print("Geri (<<) butonuna tıklandı.")
                time.sleep(1)
                return True
            except Exception as e:
                print(f"Geri butonuna tıklanamadı: {e}")
                return False
        return self._run(_impl)

    def verigirisi_onayliyorum_checkbox_tikla(self, index=0):
        """DevExtreme checkbox simgesine tıklar."""
        try:
            checkbox = self.page.locator(".dx-checkbox-icon").nth(index)
            checkbox.wait_for(state="visible", timeout=5000)
            checkbox.click()
            print(f"{index}. checkbox tıklandı.")
            return True
        except Exception as e:
            print(f"Checkbox tıklanamadı: {e}")
            return False
    def takip_talebi_olustur_butonuna_tikla(self):
        """Takip Talebi Oluştur butonuna tıklar."""
        try:
            buton = self.page.locator(".dx-button", has_text="Takip Talebi Oluştur")
            buton.wait_for(state="visible", timeout=50)
            buton.click()
            print("Takip Talebi Oluştur butonuna tıklandı.")
            return True
        except Exception as e:
            print(f"Butona tıklanamadı: {e}")
            return False
    def takip_talebi_olustur_tamam_butonuna_tikla(self):
        """SweetAlert2 Tamam butonuna tıklar."""
        try:
            buton = self.page.locator("button.swal2-confirm", has_text="Tamam")
            buton.wait_for(state="visible", timeout=50)
            buton.click()
            print("SweetAlert Tamam butonuna tıklandı.")
            return True
        except Exception as e:
            print(f"Tamam butonu tıklanamadı: {e}")
            return False
    def takip_talebi_olustur_tikla(self, dosya_adi_prefix=None):
        """Takip Talebi Oluştur butonuna tıklar, indirmeyi bekler ve dosya yolunu döner.

        dosya_adi_prefix: None → UYAP'ın önerdiği adı kullan.
                          Metin → borçlu+abone kombinasyonundan oluşan benzersiz ad."""
        def _impl():
            try:
                buton = self.page.locator("div.button-evrak-indir", has_text="Takip Talebi Oluştur")
                buton.wait_for(state="visible", timeout=50)
                print("İndirme bekleniyor...")
                with self.page.expect_download(timeout=600) as download_info:
                    buton.click()
                download = download_info.value
                kayit_yolu = indirmeyi_yakala(download, kayit_adi=dosya_adi_prefix)
                return kayit_yolu
            except Exception as e:
                print(f"Takip Talebi Oluştur butonu tıklanamadı: {e}")
                return None
        return self._run(_impl)
    def evrak_turu_takip_talebi_sec(self):
        """Evrak Türü combobox'ını açar ve 'Takip Talebi' seçeneğini seçer."""
        def _impl():
            print("Evrak Türü alanı açılıyor...")
            # Görsel 1: aria-label="Evrak Türü" olan combobox'ı bulup tıklıyoruz
            combo = self.page.get_by_role("combobox", name="Evrak Türü")
            combo.wait_for(state="visible", timeout=500)
            combo.click()
            
            time.sleep(0.5)
            
            print("'Takip Talebi' seçeneği seçiliyor...")
            # Görsel 2: dx-list içindeki 'Takip Talebi' metnine tıklıyoruz
            secenek = self.page.locator(".dx-list-item-content", has_text="Takip Talebi")
            secenek.wait_for(state="visible", timeout=5000)
            secenek.click()
            print("Evrak türü başarıyla seçildi.")
        self._run(_impl)

    def imzali_dosya_yukle(self, dosya_yolu):
        """'Dosya Seç' butonunu tetikler, dosyayı seçer, ardından yükleme butonuna tıklar.
        UDF için 'Evrak Yükle', PDF için 'Belge Yükle' / 'Yükle' adlarını dener."""
        def _impl():
            uzanti = os.path.splitext(dosya_yolu)[1].lower()
            print(f"Dosya seçiliyor ({uzanti}): {dosya_yolu}")
            try:
                with self.page.expect_file_chooser() as fc_info:
                    self.page.get_by_role("button", name="Dosya Seç").click()
                fc_info.value.set_files(dosya_yolu)
                print("Dosya seçildi, yükleme butonu aranıyor...")
            except Exception as e:
                print(f"Dosya seçilirken hata: {e}")
                return False

            time.sleep(1.5)

            # Olası yükleme butonu adları (UDF ve PDF için farklı olabilir)
            aday_adlar = ["Evrak Yükle", "Belge Yükle", "Yükle"]
            for ad in aday_adlar:
                try:
                    btn = self.page.get_by_role("button", name=ad, exact=True)
                    btn.wait_for(state="visible", timeout=400)
                    btn.click()
                    print(f"'{ad}' butonuna tıklandı.")
                    time.sleep(1)
                    return True
                except Exception:
                    pass
                # dx-button has_text ile de dene
                try:
                    btn = self.page.locator(".dx-button", has_text=ad).first
                    btn.wait_for(state="visible", timeout=200)
                    btn.click()
                    print(f"'{ad}' butonuna tıklandı (dx-button locator).")
                    time.sleep(1)
                    return True
                except Exception:
                    pass

            print("UYARI: Yükleme butonu bulunamadı (Evrak Yükle / Belge Yükle / Yükle). "
                  "Sayfa kaydedilerek devam ediliyor...")
            # Son çare: sayfanın mevcut butonlarını logla
            try:
                butonlar = self.page.locator("button, .dx-button").all()
                isimler = [b.inner_text() for b in butonlar[:20]]
                print(f"Sayfadaki butonlar: {isimler}")
            except Exception:
                pass
            return False

        self._run(_impl)
    
    def yuklu_belgeyi_listeden_sec(self):
        """Evrak yükleme panelindeki listede yeni yüklenen dosyanın satırını seçer.

        UYAP bazen dosyayı otomatik seçmez; 'Evrak Ekle' butonunu aktifleştirmek
        için satıra tıklamak gerekebilir. İlk görünür data satırını seçer."""
        def _impl():
            # 1) Yükleme tamamlanana kadar bekle (Sadece görünür durumdaki panelleri bekle)
            try:
                for lp_sel in (".dx-loadpanel-content:visible", ".dx-loadindicator:visible", ".dx-loadpanel:visible"):
                    lp = self.page.locator(lp_sel)
                    if lp.count() > 0:
                        print(f"Yükleme göstergesi algılandı ({lp_sel}), kaybolması bekleniyor...")
                        lp.last.wait_for(state="hidden", timeout=120)
                        time.sleep(0.3)
            except Exception as e:
                print(f"Yükleme panelinin kaybolması beklenirken hata/timeout: {e}")

            # 2) Aday seçiciler (Öncelikli olarak checkbox'lar, sonra satırların kendisi - Hızlı geçmesi için :visible eklendi)
            adaylar = [
                "tr.dx-row.dx-data-row td.dx-command-select .dx-checkbox:visible",
                "tr.dx-row.dx-data-row .dx-select-checkbox:visible",
                "tr.dx-row.dx-data-row .dx-checkbox-icon:visible",
                ".dx-datagrid-rowsview tr.dx-row .dx-select-checkbox:visible",
                ".dx-datagrid-rowsview tr.dx-row .dx-checkbox-icon:visible",
                ".dx-select-checkbox:visible",
                ".dx-checkbox-icon:visible",
                "tr.dx-row.dx-data-row:visible",
                ".dx-datagrid-rowsview tr.dx-row:visible",
                "tr[role='row']:visible",
                ".dx-list-item:visible",
            ]
            for sel in adaylar:
                try:
                    locator = self.page.locator(sel)
                    if locator.count() == 0:
                        continue
                    
                    satir = locator.first
                    satir.wait_for(state="visible", timeout=150)
                    
                    # Zaten seçili olup olmadığını kontrol et (tekrar tıklayıp seçimi kaldırmamak için)
                    zaten_secili = False
                    try:
                        sinif = satir.get_attribute("class") or ""
                        ebeveyn_sinif = ""
                        try:
                            ebeveyn_sinif = satir.locator("xpath=./ancestor::tr").first.get_attribute("class") or ""
                        except Exception:
                            pass
                        
                        if "dx-selection" in sinif or "dx-checkbox-checked" in sinif or "dx-selection" in ebeveyn_sinif:
                            zaten_secili = True
                    except Exception:
                        pass
                    
                    if zaten_secili:
                        print(f"Belge zaten seçili görünüyor ({sel}), tekrar tıklanmıyor.")
                        return True
                        
                    satir.scroll_into_view_if_needed(timeout=1000)
                    satir.click(timeout=1500)
                    print(f"Belge listesinden satır/kutu seçildi ({sel}).")
                    time.sleep(0.2)
                    return True
                except Exception:
                    continue
            print("UYARI: Belge listesinde seçilecek satır/kutu bulunamadı — devam ediliyor.")
            return False
        return self._run(_impl)

    def evrak_ekle_butonuna_tikla(self):
        """'Evrak Ekle' veya 'Belge Ekle' butonuna tıklar.

        Sayfada birden fazla 'Evrak Ekle' butonu olabilir (biri paneli açan,
        biri pasif/disabled olan). Bu yüzden GÖRÜNÜR ve ETKİN (disabled olmayan)
        ilk butonu seçeriz; tek match'te strict-mode ihlali yaşanmaz.
        Ayrıca bazı ekranlarda butonun adı 'Belge Ekle' olabilir."""
        def _impl():
            try:
                for metin in ("Evrak Ekle", "Belge Ekle"):
                    buton = self.page.locator(".dx-button:visible", has_text=metin)
                    try:
                        buton.first.wait_for(state="visible", timeout=4000)
                    except Exception:
                        continue
                    adet = buton.count()
                    for i in range(adet):
                        el = buton.nth(i)
                        try:
                            sinif = el.get_attribute("class") or ""
                            if "dx-state-disabled" in sinif:
                                continue
                            el.scroll_into_view_if_needed(timeout=2000)
                            el.click()
                            print(f"'{metin}' butonuna tıklandı ({i + 1}/{adet}).")
                            time.sleep(1)
                            return True
                        except Exception:
                            continue
                # Hiçbiri etkin görünmediyse ilk görünür butonu zorla dene
                for metin in ("Evrak Ekle", "Belge Ekle"):
                    try:
                        self.page.locator(".dx-button:visible", has_text=metin).first.click()
                        print(f"'{metin}' butonuna tıklandı (fallback).")
                        time.sleep(1)
                        return True
                    except Exception:
                        continue
                print("HATA: 'Evrak Ekle' / 'Belge Ekle' butonu bulunamadı.")
                return False
            except Exception as e:
                print(f"Evrak/Belge Ekle butonuna tıklanamadı: {e}")
                return False
        return self._run(_impl)

    def evrak_turu_takibin_dayanagi_sec(self):
        """Evrak Türü combobox'ından 'Takibin Dayanağı' seçer."""
        def _impl():
            combo = self.page.get_by_role("combobox", name="Evrak Türü")
            combo.wait_for(state="visible", timeout=5000)
            combo.click()
            time.sleep(0.5)
            self.page.get_by_text("Takibin Dayanağı", exact=True).click()
            print("Evrak türü: Takibin Dayanağı seçildi.")
            time.sleep(0.5)
            # NOT: 'Evrak Ekle' butonuna burada DEĞİL, dosya yüklendikten sonra
            # takip_ac akışında tıklanır (yükleme öncesi tıklama erken kalıyordu).
            return True
        return self._run(_impl)
    def evrak_turu_vekaletname_sec(self):
        """Evrak Türü combobox'ından 'Vekaletname' seçer."""
        def _impl():
            combo = self.page.get_by_role("combobox", name="Evrak Türü")
            combo.wait_for(state="visible", timeout=5000)
            combo.click()
            time.sleep(0.5)
            self.page.get_by_text("Vekaletname", exact=True).click()
            print("Vekaletname seçildi.")
            time.sleep(0.5)
            # NOT: 'Evrak Ekle' butonuna burada DEĞİL, dosya yüklendikten sonra
            # takip_ac akışında tıklanır (yükleme öncesi tıklama erken kalıyordu).
            return True
        return self._run(_impl)

    def evrak_gonder_butonuna_tikla(self):
        """'Evrak Gönder' butonuna tıklar."""
        def _impl():
            btn = self.page.get_by_role("button", name="Evrak Gönder", exact=True)
            btn.wait_for(state="visible", timeout=10000)
            btn.click()
            print("Evrak Gönder butonuna tıklandı.")
            return True
        return self._run(_impl)
    def inspector_ac(self):
        def _impl():
            print("\n" + "="*50)
            print("PLAYWRIGHT INSPECTOR AÇILIYOR")
            print("'Pick locator' butonuna basıp elementin üzerine gelin.")
            print("Çıkmak için Inspector'ı kapatın veya 'Resume' butonuna basın.")
            print("="*50)
            self.page.pause()
        self._run(_impl)

    def sayfa_html_kaydet(self, dosya_adi="sayfa_snapshot.html"):
        def _impl():
            html = self.page.content()
            with open(dosya_adi, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Sayfa HTML kaydedildi: {dosya_adi}")
        self._run(_impl)
   
    
