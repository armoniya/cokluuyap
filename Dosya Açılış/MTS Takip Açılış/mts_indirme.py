# -*- coding: utf-8 -*-
"""
İndirilen UYAP evrağını kaydetme + F7 ile imzalama yardımcısı.
mts_bot.py'den AYRILDI (2026-06-26 modülerleştirme).
"""
import os
import time
import importlib
import pyautogui
import win32gui
import win32con
import win32process
import win32api

from mts_pencere import _pencere_basligi, pencereyi_one_al

INDİRME_KLASORU = os.path.join(os.path.expanduser("~"), "Downloads")

# E-imza kart PIN'i ASLA kaynağa gömülmez. Oturum boyunca bir kez kullanıcıdan alınır.
_OTURUM_PIN = None


def _pin_al():
    """E-imza kart PIN'ini döndürür.

    Öncelik sırası:
      1) Bu oturumda daha önce alındıysa onu kullan (tekrar sormaz).
      2) UYAP_PIN ortam değişkeni tanımlıysa onu kullan (otomatik/headless senaryolar).
      3) Aksi halde gizli (yıldızlı) bir giriş penceresiyle kullanıcıdan iste.
    PIN yalnızca bellekte tutulur, hiçbir yere yazılmaz."""
    global _OTURUM_PIN
    if _OTURUM_PIN:
        return _OTURUM_PIN
    env_pin = os.environ.get("UYAP_PIN")
    if env_pin:
        _OTURUM_PIN = env_pin.strip()
        return _OTURUM_PIN
    try:
        import tkinter as tk
        from tkinter import simpledialog
        kok = tk.Tk()
        kok.withdraw()
        kok.attributes("-topmost", True)
        deger = simpledialog.askstring("E-İmza PIN", "E-imza kart PIN'ini girin:",
                                       show="*", parent=kok)
        kok.destroy()
        if deger:
            _OTURUM_PIN = deger.strip()
    except Exception as e:
        print(f"PIN giriş penceresi açılamadı: {e}")
    return _OTURUM_PIN


def indirmeyi_yakala(download, kayit_adi=None, headless_imza=True):
    """İndirilen dosyayı kaydeder, imzalama programında açar, F7 ile imzalar.

    kayit_adi: None → UYAP'ın önerdiği adı kullan.
               Metin verilirse dosya o adla kaydedilir (uzantı korunur).
               Örn. 'Ali_Veli_123456' → 'Ali_Veli_123456.udf'
    headless_imza: True ise imzalama pencerelerini ekran dışına taşıyarak arka planda imzalar.
    Dönüş: kaydedilen dosyanın tam yolu."""
    import re as _re
    print("İndirme tetiklendi")

    try:
        orijinal = download.suggested_filename or "UYAP_indirme.pdf"
        uzanti = os.path.splitext(orijinal)[1] or ".udf"
        if kayit_adi:
            # Türkçe karakterleri ASCII yapalım ve boşlukları alt çizgi yapıp güvenli isim oluşturalım
            def tr_to_ascii(text):
                tr_map = {
                    'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G',
                    'ı': 'i', 'I': 'I', 'İ': 'I', 'ö': 'o', 'Ö': 'O',
                    'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U', ' ': '_'
                }
                for tr_char, eng_char in tr_map.items():
                    text = text.replace(tr_char, eng_char)
                return _re.sub(r'[^a-zA-Z0-9_]', '', text)

            guvenli = tr_to_ascii(str(kayit_adi)).strip()
            isim = f"{guvenli}{uzanti}" if guvenli else orijinal
        else:
            isim = orijinal
        kayit_yolu = os.path.join(INDİRME_KLASORU, isim)
        download.save_as(kayit_yolu)
        print(f"Kaydedildi: {kayit_yolu}")

        while not os.path.exists(kayit_yolu):
            time.sleep(0.5)
        time.sleep(1)  # Dosyanın tam yazılmasını bekle

        # Chrome'un otomatik olarak indirdiği mükerrer/kopya 'takipTalebi' dosyasını temizleyelim
        try:
            time.sleep(1)
            simdi = time.time()
            for f in os.listdir(INDİRME_KLASORU):
                if f.startswith("takipTalebi") and f.endswith(".udf"):
                    f_path = os.path.join(INDİRME_KLASORU, f)
                    if simdi - os.path.getmtime(f_path) < 15: # Son 15 saniyede indiyse temizle
                        os.remove(f_path)
                        print(f"Chrome otomatik indirmesi temizlendi: {f}")
        except Exception as temizleme_hatasi:
            print(f"Otomatik indirme dosyası temizlenirken hata (önemsiz): {temizleme_hatasi}")

        try:
            chrome_hwnd = win32gui.GetForegroundWindow()
            chrome_pid = win32process.GetWindowThreadProcessId(chrome_hwnd)[1] if chrome_hwnd else 0
            print(f"Tarayıcı penceresi: '{_pencere_basligi(chrome_hwnd)}' ({chrome_hwnd})")

            # Dosya açılmadan ÖNCEKİ durumu kaydet: hem pencereler hem de çalışan
            # süreçler. Editör otomatik açılınca YENİ bir süreç (process) doğar;
            # asıl güvenilir sinyal budur.
            eski_pencereler = set()
            win32gui.EnumWindows(lambda h, _: eski_pencereler.add(h), None)
            try:
                eski_pidler = set(win32process.EnumProcesses())
            except Exception:
                eski_pidler = set()

            # --- İmzalama (UDF editörü) penceresini SONUÇ-ODAKLI bul ---
            # Editör penceresi BAŞLIĞINDA mutlaka editör anahtar kelimesi ya da
            # dosya adı geçer (örn. "Doküman Editörü v5.4.17 - GULBAHAR....udf").
            # Yalnızca böyle pencereleri seçiyoruz; aksi halde yüklenme sırasında
            # açılan BOŞ başlıklı yardımcı pencereler yanlışlıkla seçilip gerçek
            # editör yerine onlar headless'a taşınıyordu (F7 basılamıyordu).
            # Yeni süreç olması sadece bir puan bonusudur, tek başına yetmez.
            editor_kelimeleri = ("udf", "editör", "editor", "doküman", "dokuman", "imza")

            def _editor_bul(zaman_asimi):
                """Editör penceresini arar; başlığı eşleşen pencereyi bulduğu AN
                döner (sonuç-odaklı). Dönüş: (hwnd, baslik) veya (None, None)."""
                bitis = time.time() + zaman_asimi
                while time.time() < bitis:
                    adaylar = []

                    def tara(hwnd, _):
                        if hwnd == chrome_hwnd:
                            return  # Tarayıcı asla seçilmez
                        if not win32gui.IsWindowVisible(hwnd):
                            return
                        baslik = _pencere_basligi(hwnd)
                        bl = baslik.lower()
                        kelime_var = any(k in bl for k in editor_kelimeleri) or isim.lower() in bl
                        if not kelime_var:
                            return  # editör başlığı taşımayan pencereleri ASLA seçme
                        try:
                            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                        except Exception:
                            pid = 0
                        if pid and pid == chrome_pid:
                            return  # Chrome'un kendi pencereleri hariç
                        yeni_surec = bool(pid) and pid not in eski_pidler
                        yeni_pencere = hwnd not in eski_pencereler
                        skor = 2 + (2 if yeni_surec else 0) + (1 if yeni_pencere else 0)
                        adaylar.append((skor, hwnd, baslik))

                    win32gui.EnumWindows(tara, None)
                    if adaylar:
                        adaylar.sort(key=lambda a: a[0], reverse=True)
                        return adaylar[0][1], adaylar[0][2]
                    time.sleep(0.3)
                return None, None

            # 1) Editör zaten kendiliğinden açıldıysa kısa sürede yakalanır. Bu
            #    ortamda genelde açılmadığı için kısa bekliyoruz (boşa beklememek).
            print("Editör penceresi bekleniyor...")
            imzalama_hwnd, imzalama_baslik = _editor_bul(4)

            # 2) Açık editör yoksa bir kez biz açıyoruz, sonra başlığı oturana
            #    kadar bekliyoruz. Tek açma → çift pencere oluşmaz.
            if not imzalama_hwnd:
                print("Editör açık değil, bir kez açılıyor...")
                os.startfile(kayit_yolu)
                imzalama_hwnd, imzalama_baslik = _editor_bul(30)

            if not imzalama_hwnd:
                print("HATA: İmzalama programı bulunamadı.")
                return

            print(f"İmzalama penceresi: '{imzalama_baslik}' ({imzalama_hwnd})")

            # --- Pencereyi GERÇEKTEN öne al ve DOĞRULA ---
            print("İmzalama penceresi öne alınıyor...")
            pencereyi_one_al(imzalama_hwnd)
            time.sleep(2)  # Java editörünün yüklenmesini bekle

            if headless_imza:
                print("Headless mod aktif: İmzalama penceresi ekran dışına taşınıyor...")
                # Move window to -32000, -32000 (off-screen)
                win32gui.SetWindowPos(imzalama_hwnd, 0, -32000, -32000, 0, 0,
                                      win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
                time.sleep(0.5)

            # Yüklenme sonrası bir kez daha öne al (editör açılırken odağı kaçırabilir)
            pencereyi_one_al(imzalama_hwnd)
            time.sleep(0.5)

            onde = win32gui.GetForegroundWindow()
            if onde != imzalama_hwnd:
                print(f"HATA: İmzalama penceresi öne alınamadı. Şu an önde: "
                      f"'{_pencere_basligi(onde)}'. F7'ye BASILMIYOR.")
                pencereyi_one_al(chrome_hwnd)
                return

            print(f"DOĞRULANDI: Aktif pencere '{_pencere_basligi(imzalama_hwnd)}'. F7'ye basılıyor.")

            if not headless_imza:
                # Editörün içerik alanına odak için merkeze fiziksel tık
                try:
                    rect = win32gui.GetWindowRect(imzalama_hwnd)
                    merkez_x = (rect[0] + rect[2]) // 2
                    merkez_y = (rect[1] + rect[3]) // 2
                    pyautogui.click(merkez_x, merkez_y)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"Merkez tıklama başarısız (önemsiz): {e}")

                # Tıklamadan sonra odak hâlâ doğru pencerede mi?
                if win32gui.GetForegroundWindow() != imzalama_hwnd:
                    pencereyi_one_al(imzalama_hwnd)
                    time.sleep(0.4)
            else:
                # Headless iken fiziksel tıklama yapamayız, odaklanması için SetFocus deneyelim
                try:
                    win32gui.SetFocus(imzalama_hwnd)
                except Exception:
                    pass
                time.sleep(0.5)

            print("F7 tuşuna basılıyor (İmzala)...")
            pyautogui.press('f7')
            time.sleep(1.5)

            # --- Şifre/PIN ekranını bekle ---
            print("Şifre/PIN ekranı bekleniyor...")
            sifre_hwnd = None
            baslangic = time.time()
            while time.time() - baslangic < 20:
                fg = win32gui.GetForegroundWindow()
                if fg and fg != imzalama_hwnd and _pencere_basligi(fg).strip():
                    sifre_hwnd = fg
                    print(f"Şifre ekranı açıldı: '{_pencere_basligi(fg)}' ({fg})")
                    if headless_imza:
                        print("Headless mod: Şifre ekranı ekran dışına taşınıyor...")
                        win32gui.SetWindowPos(sifre_hwnd, 0, -32000, -32000, 0, 0,
                                              win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
                        time.sleep(0.2)
                    break
                time.sleep(0.5)

            if not sifre_hwnd:
                print("HATA: F7 basıldı ama şifre ekranı açılmadı!")
                pencereyi_one_al(chrome_hwnd)
                return

            # Şifre ekranını da garantiye al
            pencereyi_one_al(sifre_hwnd)
            time.sleep(0.5)

            _pin = _pin_al()
            if not _pin:
                print("HATA: E-imza PIN'i girilmedi. İmzalama iptal edildi.")
                pencereyi_one_al(chrome_hwnd)
                return
            # PIN penceresi odağı çalmış olabilir; şifre ekranına odağı geri ver.
            pencereyi_one_al(sifre_hwnd)
            time.sleep(0.4)

            print("Şifre yazılıyor...")
            pyautogui.write(_pin, interval=0.12)
            time.sleep(0.8)

            print("Şifre onaylanıyor (Enter)...")
            pyautogui.press('enter')

            print("İmzalama işleminin tamamlanması için bekleniyor (4 sn)...")
            time.sleep(4)

            # --- İmzalama sonrası çıkan bilgi/onay pencerelerini Enter ile geç ---
            for _ in range(3):
                fg = win32gui.GetForegroundWindow()
                if fg and fg not in (imzalama_hwnd, chrome_hwnd) and _pencere_basligi(fg).strip():
                    print(f"Ara pencere onaylanıyor: '{_pencere_basligi(fg)}'")
                    if headless_imza:
                        win32gui.SetWindowPos(fg, 0, -32000, -32000, 0, 0,
                                              win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
                        time.sleep(0.2)
                    pyautogui.press('enter')
                    time.sleep(1)
                else:
                    break

            # --- İmzalama (editör) penceresini kapat ---
            # F7 imzalama işlemini yapıp belgeyi KAYDEDER; Ctrl+S gerekmez.
            # Doğrudan kapatıyoruz; kapanırken bir onay çıkarsa Enter ile geçiyoruz.
            print("İmzalama penceresi kapatılıyor...")
            if win32gui.IsWindow(imzalama_hwnd):
                if pencereyi_one_al(imzalama_hwnd):
                    time.sleep(0.4)
                    pyautogui.hotkey('alt', 'f4')
                    print("Alt+F4 gönderildi.")
                    time.sleep(1)
                    # Kaydettik; yine de bir onay çıkarsa Enter ile geç
                    fg = win32gui.GetForegroundWindow()
                    if (fg and fg != chrome_hwnd and fg != imzalama_hwnd
                            and win32gui.IsWindow(fg) and _pencere_basligi(fg).strip()):
                        if headless_imza:
                            win32gui.SetWindowPos(fg, 0, -32000, -32000, 0, 0,
                                                  win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
                            time.sleep(0.2)
                        pyautogui.press('enter')
                else:
                    print("Uyarı: Editör öne alınamadı, kapatma atlandı.")
            else:
                print("İmzalama penceresi zaten kapanmış.")

            print("DOWNLOAD CALLBACK BİTTİ")

            # Tarayıcıyı tekrar öne al → 'Evrak Ekle' adımına devam
            time.sleep(1)
            if win32gui.IsWindow(chrome_hwnd):
                pencereyi_one_al(chrome_hwnd)

        except Exception as acma_hatasi:
            print(f"Açma hatası: {acma_hatasi}")
            import traceback
            traceback.print_exc()

    except Exception as e:
        print(f"DOWNLOAD CALLBACK HATASI: {e}")
        import traceback
        traceback.print_exc()

    return kayit_yolu


