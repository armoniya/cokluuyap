import json
import os
import time
import threading
import concurrent.futures
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
import re
import pyautogui
import win32gui
import win32con
import win32process
import win32api
import asyncio
import logging

# Silence asyncio "Future exception was never retrieved" warning tracebacks globally
logging.getLogger('asyncio').setLevel(logging.CRITICAL)

# --- CONFIGURATION / YAPILANDIRMA ---
# UYAP API yanıt gövdelerini de loglamak isterseniz True yapabilirsiniz.
# (Gövde okuma işlemi tarayıcı kapatıldığında TargetClosedError uyarılarına sebep olabilir)
LOG_RESPONSE_BODY = True

# Resolve the absolute directory of this script to store log files reliably
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDIRME_KLASORU = os.path.join(os.path.expanduser("~"), "Downloads")


def time_to_seconds(t_str):
    try:
        t_str = t_str.strip().upper().replace(".", ":")
        is_pm = "PM" in t_str
        is_am = "AM" in t_str
        t_str = re.sub(r'[A-Z\s]', '', t_str)
        
        parts = list(map(int, t_str.split(":")))
        if not parts:
            return 0
        
        hours = parts[0]
        minutes = parts[1] if len(parts) > 1 else 0
        seconds = parts[2] if len(parts) > 2 else 0
        
        if is_pm and hours < 12:
            hours += 12
        if is_am and hours == 12:
            hours = 0
            
        return hours * 3600 + minutes * 60 + seconds
    except Exception:
        return 0


def _pencere_basligi(hwnd):
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def pencereyi_one_al(hwnd, deneme=6):
    """
    Bir pencereyi gerçekten öne alır.
    Windows'un 'foreground lock' kısıtlamasını AttachThreadInput + Alt tuşu
    hilesiyle aşar. Pencere gerçekten öne geldiyse True döner.
    """
    for _ in range(deneme):
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            fg = win32gui.GetForegroundWindow()
            cur_thread = win32api.GetCurrentThreadId()
            fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            hedef_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

            # Alt tuşuna basıp bırakmak Windows'un foreground kilidini gevşetir
            try:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            except Exception:
                pass

            attached = []
            try:
                if fg_thread and fg_thread != cur_thread:
                    win32process.AttachThreadInput(cur_thread, fg_thread, True)
                    attached.append(fg_thread)
                if hedef_thread != cur_thread:
                    win32process.AttachThreadInput(cur_thread, hedef_thread, True)
                    attached.append(hedef_thread)

                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            finally:
                for t in attached:
                    try:
                        win32process.AttachThreadInput(cur_thread, t, False)
                    except Exception:
                        pass

            time.sleep(0.4)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception as e:
            print(f"  Öne alma denemesi hata verdi (önemsiz): {e}")
            time.sleep(0.4)

    return win32gui.GetForegroundWindow() == hwnd


def imzalama_ve_acma_islemi(kayit_yolu, orijinal_isim, headless_imza=True):
    """
    Bu fonksiyon tamamen ARKA PLAN THREAD'inde çalışır.
    Herhangi bir Playwright nesnesi kullanmaz (thread-safety için).
    İnen dosyayı varsayılan uygulamada NORMAL (ekranda) açar.
    Bu akışta imzalama YOKTUR; headless_imza parametresi kullanılmaz
    (imza otomasyonu olan mts_takip_acan.indirmeyi_yakala ile karıştırma).
    """
    print(f"⚡ Dosya açılıyor: {kayit_yolu}")
    try:
        # İmza/headless yok: dosyayı sadece varsayılan uygulamada bir kez aç.
        os.startfile(kayit_yolu)
    except Exception as acma_hatasi:
        print(f"❌ Dosya açma hatası: {acma_hatasi}")


def handle_download(download):
    """
    Bu fonksiyon PLAYWRIGHT ANA THREAD'inde çağrılır.
    Dosyayı kaydeder ve ardından imzalama otomasyonunu arka plan thread'inde başlatır.
    """
    print("\n📥 İndirme algılandı, dosya diske kaydediliyor...")
    try:
        orijinal = download.suggested_filename or "UYAP_indirme.pdf"
        kayit_yolu = os.path.join(INDIRME_KLASORU, orijinal)
        
        # Playwright thread'inde kaydetme işlemi güvenlidir
        download.save_as(kayit_yolu)
        print(f"📂 Dosya diske kaydedildi: {kayit_yolu}")
        
        # İmzalama ve açma işlemini arka planda çalıştır (Playwright'ın kilitlenmemesi için)
        threading.Thread(
            target=imzalama_ve_acma_islemi,
            args=(kayit_yolu, orijinal),
            daemon=True
        ).start()
        
    except Exception as e:
        print(f"❌ İndirme yakalama hatası (Ana Thread): {e}")


class UyapSessionLogger:
    def __init__(self):
        self.playwright = None
        self.context = None
        self.page = None
        self._ag_kayit_aktif = False
        self._ag_kayit_yolu = None
        self._ag_response_handler = None
        # Click log dosyasını kesin olarak bu scriptin yanına yazdırıyoruz
        self._click_log_yolu = os.path.join(SCRIPT_DIR, "tiklamalar.log")
        # Ağ trafiğini tıklamaya bağlamak için son tıklama bilgisi
        self._last_click = None        # {"time", "locator", "metin"}
        # Tıklamadan sonra kaç saniyelik trafik "o tıklamanın sonucu" sayılsın
        self._tiklama_pencere_sn = 8.0

    def _istek_govdesi_coz(self, req):
        """
        İstek gövdesini okunabilir biçimde döndürür.
        - Normal text/JSON gövde: req.post_data zaten doludur (bu metoda gelmez).
        - multipart/form-data (dosya yükleme): req.post_data None döner; burada
          req.post_data_buffer'dan ham binary alınıp YAPISI çözülür (alan adları,
          dosya alanı adı + filename + content-type + boyut). Binary içerik atlanır.
        """
        buf = None
        try:
            buf = req.post_data_buffer
        except Exception:
            buf = None
        if not buf:
            return None

        try:
            ctype = req.headers.get("content-type", "")
        except Exception:
            ctype = ""

        if "multipart/form-data" in ctype and "boundary=" in ctype:
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
            return self._multipart_ozetle(buf, boundary)

        # multipart değil ama binary buffer var → metne çevirmeyi dene
        try:
            return buf.decode("utf-8")
        except Exception:
            return f"<binary gövde, {len(buf)} bayt>"

    def _multipart_ozetle(self, buf, boundary):
        """multipart/form-data gövdesini alan alan okunabilir özete çevirir."""
        delim = ("--" + boundary).encode("utf-8")
        parcalar = buf.split(delim)
        satirlar = ["<MULTIPART/FORM-DATA ÇÖZÜMÜ>", f"boundary={boundary}"]
        for p in parcalar:
            p = p.strip(b"\r\n")
            if not p or p == b"--":
                continue
            if b"\r\n\r\n" in p:
                ham_basliklar, govde = p.split(b"\r\n\r\n", 1)
            else:
                ham_basliklar, govde = p, b""
            try:
                basliklar = ham_basliklar.decode("utf-8", "replace")
            except Exception:
                basliklar = "<başlık çözülemedi>"

            name = None
            filename = None
            part_ctype = None
            m = re.search(r'name="([^"]*)"', basliklar)
            if m:
                name = m.group(1)
            m2 = re.search(r'filename="([^"]*)"', basliklar)
            if m2:
                filename = m2.group(1)
            m3 = re.search(r'Content-Type:\s*([^\r\n]+)', basliklar, re.I)
            if m3:
                part_ctype = m3.group(1).strip()

            govde = govde.rstrip(b"\r\n")
            if filename is not None:
                satirlar.append(
                    f"  • ALAN '{name}' = DOSYA filename='{filename}' "
                    f"tip={part_ctype} ({len(govde)} bayt) [binary içerik atlandı]"
                )
            else:
                try:
                    deger = govde.decode("utf-8", "replace")
                except Exception:
                    deger = f"<{len(govde)} bayt>"
                if len(deger) > 500:
                    deger = deger[:500] + "…"
                satirlar.append(f"  • ALAN '{name}' = {deger!r}")
        return "\n".join(satirlar)

    def _yaz_network_log(self, kayit):
        if not self._ag_kayit_yolu:
            return

        def _pretty(deger):
            # JSON string ise güzel biçimlendir, değilse olduğu gibi bırak
            if deger is None:
                return "(yok)"
            if isinstance(deger, str):
                s = deger.strip()
                if s and s[0] in "{[":
                    try:
                        return json.dumps(json.loads(s), ensure_ascii=False, indent=2)
                    except Exception:
                        return deger
                return deger
            return json.dumps(deger, ensure_ascii=False, indent=2)

        tiklama = kayit.get("tetikleyen_tiklama", "")
        locator = kayit.get("tetikleyen_locator", "")

        # --- AYNI ANDA EKRANA YAZDIR ---
        try:
            payload = kayit.get("istek_payload") or "(yok)"
            if isinstance(payload, str) and len(payload) > 300:
                payload = payload[:300] + "…"
            print("\n" + "─" * 70)
            print(f"🌐 TIKLAMA → AĞ  [{kayit['zaman']}]  (tıklama: {tiklama!r})")
            print(f"   {kayit['method']} {kayit['url']}  → {kayit['durum']}")
            print(f"   PAYLOAD : {payload}")
            govde_ozet = kayit.get("yanit") or ""
            if isinstance(govde_ozet, str) and len(govde_ozet) > 300:
                govde_ozet = govde_ozet[:300] + "…"
            print(f"   YANIT   : {govde_ozet}")
            print("─" * 70)
        except Exception:
            pass

        try:
            with open(self._ag_kayit_yolu, "a", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write(f"[{kayit['zaman']}] TETİKLEYEN TIKLAMA: {tiklama!r}\n")
                f.write(f"               LOCATOR: {locator}\n")
                f.write(f"{kayit['method']} {kayit['url']}\n")
                f.write(f"DURUM: {kayit['durum']}\n")
                f.write("\n--- İSTEK HEADERS ---\n")
                f.write(_pretty(kayit["istek_headers"]) + "\n")
                f.write("\n--- İSTEK PAYLOAD ---\n")
                f.write(_pretty(kayit["istek_payload"]) + "\n")
                f.write("\n--- YANIT HEADERS ---\n")
                f.write(_pretty(kayit["yanit_headers"]) + "\n")
                f.write("\n--- YANIT GÖVDESİ ---\n")
                f.write(_pretty(kayit["yanit"]) + "\n\n")
                f.flush()
        except Exception as e:
            print(f"⚠️ Ağ kaydı yazılamadı: {e}")

    def run(self):
        # asyncio check
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
            print("🚀 Playwright başlatılıyor...")
            self.playwright = sync_playwright().start()
            
            # Playwright'ın çalıştığı aktif event loop'u yakalayıp TargetClosedError uyarılarını susturuyoruz
            try:
                loop = asyncio.get_event_loop()
                def custom_handler(loop, context):
                    exception = context.get("exception")
                    if exception and ("TargetClosedError" in str(exception) or 
                                     "Target page, context or browser has been closed" in str(exception) or 
                                     isinstance(exception, asyncio.CancelledError)):
                        return
                    loop.default_exception_handler(context)
                loop.set_exception_handler(custom_handler)
            except Exception:
                pass
            
            # Kalıcı profil ile tarayıcı başlatma
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

            # İlk sekmeyi al
            if len(self.context.pages) > 0:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()

            # --- AĞ TRAFİĞİ LOGLAMAYI EN BAŞTA BAŞLAT ---
            self.ag_kaydini_baslat()

            # --- TIKLAMA LOGGER ALTYAPISININ HAZIRLANMASI ---
            # recordClick fonksiyonunu python tarafında expose edelim (Context seviyesinde)
            def log_click(click_data):
                try:
                    locator = click_data.get("locator", "")
                    metin = click_data.get("metin", "")
                    zaman = click_data.get("zaman", "")

                    # Ağ trafiğini bu tıklamaya bağlamak için sakla
                    self._last_click = {
                        "time": time.time(),
                        "locator": locator,
                        "metin": metin,
                    }

                    # Okunabilir, kopyala-yapıştır Python kodu olarak yaz
                    with open(self._click_log_yolu, "a", encoding="utf-8") as f:
                        f.write(f"# [{zaman}] {click_data.get('etiket','')} -> {metin!r}\n")
                        f.write(locator + "\n")
                        # locator zayıf bir CSS yoluna düştüyse yedek xpath'i not düş
                        if ".get_by_" not in locator and "#" not in locator:
                            f.write(f"#   yedek xpath: {click_data.get('xpath_yedek','')}\n")
                        f.write("\n")
                        f.flush()

                    print(f"🖱️ Tıklama: {metin!r} -> {locator}")

                except Exception as e:
                    print(f"❌ Tıklama loglama hatası: {e}")

            self.context.expose_function("recordClick", log_click)

            # --- UPLOAD (FormData) YAKALAYICI ---
            # Playwright'in ağ katmanı multipart/form-data gövdesini vermez
            # (post_data ve post_data_buffer dosya yüklemede boş döner). Bu yüzden
            # FormData'yı JS tarafında, gönderilmeden hemen önce yakalıyoruz.
            def log_upload(data):
                try:
                    url = data.get("url", "")
                    alanlar = data.get("alanlar", [])
                    zaman = data.get("zaman", "")

                    satirlar = []
                    satirlar.append("=" * 80)
                    satirlar.append(f"[{zaman}] 📤 UPLOAD FORMDATA YAKALANDI (JS hook)")
                    satirlar.append(f"URL: {url}")
                    satirlar.append("--- FORM ALANLARI ---")
                    for a in alanlar:
                        if a.get("tip") == "dosya":
                            satirlar.append(
                                f"  • ALAN '{a.get('ad')}' = DOSYA filename='{a.get('filename')}' "
                                f"tip={a.get('contentType')} ({a.get('boyut')} bayt)"
                            )
                        else:
                            satirlar.append(f"  • ALAN '{a.get('ad')}' = {a.get('deger')!r}")
                    blok = "\n".join(satirlar) + "\n\n"

                    print("\n" + blok)
                    if self._ag_kayit_yolu:
                        with open(self._ag_kayit_yolu, "a", encoding="utf-8") as f:
                            f.write(blok)
                            f.flush()
                except Exception as e:
                    print(f"❌ Upload loglama hatası: {e}")

            self.context.expose_function("recordUpload", log_upload)

            # Tıklamaları dinleyen JavaScript (Immediately Executing)
            click_logger_js = """
            (function() {
                // XPath veya CSS Selector bulucu fonksiyonlar
                function getXPath(el) {
                    if (el.id) {
                        return `//*[@id="${el.id}"]`;
                    }
                    if (el === document.body) {
                        return '/html/body';
                    }
                    let index = 1;
                    let sib = el.previousSibling;
                    while (sib) {
                        if (sib.nodeType === 1 && sib.tagName === el.tagName) {
                            index++;
                        }
                        sib = sib.previousSibling;
                    }
                    const tagName = el.tagName.toLowerCase();
                    const parentXPath = el.parentNode ? getXPath(el.parentNode) : '';
                    return `${parentXPath}/${tagName}[${index}]`;
                }
                
                function getCSSSelector(el) {
                    if (el.id) {
                        return `#${el.id}`;
                    }
                    const path = [];
                    let cur = el;
                    while (cur && cur.nodeType === 1) {
                        let selector = cur.tagName.toLowerCase();
                        if (cur.id) {
                            selector += `#${cur.id}`;
                            path.unshift(selector);
                            break;
                        } else {
                            let sib = cur, nth = 1;
                            while (sib = sib.previousElementSibling) {
                                if (sib.tagName === cur.tagName) nth++;
                            }
                            if (nth > 1) {
                                selector += `:nth-of-type(${nth})`;
                            }
                        }
                        path.unshift(selector);
                        cur = cur.parentNode;
                    }
                    return path.join(' > ');
                }

                function pyStr(s) {
                    // Python string'i için tek tırnakları kaçır
                    return s.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'").trim();
                }

                // Bir elementin KENDİ güçlü kimlik özelliklerinden Python Playwright
                // locator'ı üretir. Yoksa null döner (atadan denenecek).
                function strongLocator(el) {
                    if (!el || el.nodeType !== 1) return null;
                    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test');
                    if (testid) return `page.get_by_test_id('${pyStr(testid)}')`;
                    if (el.id) return `page.locator('#${pyStr(el.id)}')`;
                    const name = el.getAttribute('name');
                    if (name) return `page.locator('${el.tagName.toLowerCase()}[name=\"${pyStr(name)}\"]')`;
                    const ariaLabel = el.getAttribute('aria-label');
                    if (ariaLabel) return `page.get_by_label('${pyStr(ariaLabel)}')`;
                    const placeholder = el.getAttribute('placeholder');
                    if (placeholder) return `page.get_by_placeholder('${pyStr(placeholder)}')`;
                    return null;
                }

                // role + görünür metin tabanlı locator (button/link/menü öğeleri için)
                function roleTextLocator(el, text) {
                    if (!el || el.nodeType !== 1) return null;
                    const tag = el.tagName.toLowerCase();
                    const role = el.getAttribute('role');
                    const cleanText = (text && text.length > 0 && text.length < 80) ? pyStr(text) : '';
                    let pwRole = null;
                    if (tag === 'button' || role === 'button') pwRole = 'button';
                    else if (tag === 'a') pwRole = 'link';
                    else if (role) pwRole = role;
                    if (pwRole && cleanText) return `page.get_by_role('${pwRole}', name='${cleanText}')`;
                    if (cleanText) return `page.get_by_text('${cleanText}')`;
                    return null;
                }

                // Hedef ve tıklanabilir ata arasından en iyi locator'ı seç.
                function bestLocator(target, interactive, text, css) {
                    // 1) Hedefin kendi kimliği (id/name/testid/label/placeholder) en sağlamı
                    return strongLocator(target)
                        || strongLocator(interactive)
                        // 2) role + metin (görünen şeye göre)
                        || roleTextLocator(interactive, text)
                        || roleTextLocator(target, target.innerText ? target.innerText.trim().substring(0,80) : '')
                        // 3) son çare: CSS yolu (xpath değil — okunabilir)
                        || `page.locator('${pyStr(css)}')`;
                }

                function handleElementClick(target) {
                    if (!target) return;
                    
                    let interactive = target;
                    while (interactive && interactive !== document.body) {
                        const tag = interactive.tagName.toLowerCase();
                        if (tag === 'button' || tag === 'a' || interactive.onclick || 
                            interactive.getAttribute('role') === 'button' || 
                            interactive.classList.contains('dx-button') || 
                            interactive.classList.contains('dx-list-item')) {
                            break;
                        }
                        interactive = interactive.parentNode;
                    }
                    if (!interactive || interactive === document.body) {
                        interactive = target;
                    }
                    
                    const text = interactive.innerText ? interactive.innerText.trim().substring(0, 80) : '';
                    const interactiveCss = getCSSSelector(interactive);

                    const now = new Date();
                    const cleanTime = [now.getHours(), now.getMinutes(), now.getSeconds()].map(x => String(x).padStart(2, '0')).join(':');

                    const clickData = {
                        zaman: cleanTime,
                        // ASIL KULLANILACAK ALAN: kopyala-yapıştır Python Playwright satırı
                        locator: bestLocator(target, interactive, text, interactiveCss),
                        etiket: interactive.tagName.toLowerCase(),
                        metin: text,
                        // yedek bilgi (locator çalışmazsa elle bakmak için)
                        xpath_yedek: getXPath(interactive)
                    };
                    
                    // Safe call to recordClick, avoiding cross-origin SecurityErrors
                    let recorded = false;
                    try {
                        if (typeof window.recordClick === 'function') {
                            window.recordClick(clickData);
                            recorded = true;
                        }
                    } catch (e) {}
                    
                    if (!recorded) {
                        try {
                            if (window.top && typeof window.top.recordClick === 'function') {
                                window.top.recordClick(clickData);
                                recorded = true;
                            }
                        } catch (e) {}
                    }
                    
                    if (!recorded) {
                        try {
                            if (window.parent && typeof window.parent.recordClick === 'function') {
                                window.parent.recordClick(clickData);
                                recorded = true;
                            }
                        } catch (e) {}
                    }
                }

                // Ana pencereyi dinle
                window.addEventListener('mousedown', (e) => {
                    if (e.button !== 0) return;
                    handleElementClick(e.target);
                }, true);

                // Dinamik olarak eklenen iframeleri yakalamak için periyodik kontrol
                setInterval(() => {
                    const iframes = document.querySelectorAll('iframe');
                    iframes.forEach(iframe => {
                        try {
                            const doc = iframe.contentDocument || iframe.contentWindow.document;
                            if (doc && !doc._hasClickLogger) {
                                doc._hasClickLogger = true;
                                doc.addEventListener('mousedown', (e) => {
                                    if (e.button !== 0) return;
                                    handleElementClick(e.target);
                                }, true);
                            }
                        } catch (e) {
                            // Cross-origin güvenlik engellerini yoksay (UYAP aynı origin olduğu için engellenmez)
                        }
                    });
                }, 1000);
            })();
            """
            self.context.add_init_script(click_logger_js)

            # FormData gönderilmeden hemen önce XHR ve fetch'i sarmalayıp yakala
            upload_logger_js = """
            (function() {
                function describeFormData(fd, url) {
                    var alanlar = [];
                    try {
                        var it = fd.entries();
                        var step;
                        while (!(step = it.next()).done) {
                            var name = step.value[0];
                            var value = step.value[1];
                            if (value && typeof value === 'object' && 'size' in value && 'name' in value) {
                                // File / Blob
                                alanlar.push({
                                    ad: name, tip: 'dosya',
                                    filename: value.name || '',
                                    boyut: value.size,
                                    contentType: value.type || ''
                                });
                            } else {
                                var v = String(value);
                                if (v.length > 2000) v = v.substring(0, 2000) + '…';
                                alanlar.push({ ad: name, tip: 'metin', deger: v });
                            }
                        }
                    } catch (e) {}

                    var now = new Date();
                    var cleanTime = [now.getHours(), now.getMinutes(), now.getSeconds()]
                        .map(function(x){ return String(x).padStart(2,'0'); }).join(':');
                    var data = { url: url || '', alanlar: alanlar, zaman: cleanTime };

                    try { if (typeof window.recordUpload === 'function') { window.recordUpload(data); return; } } catch(e){}
                    try { if (window.top && typeof window.top.recordUpload === 'function') { window.top.recordUpload(data); return; } } catch(e){}
                    try { if (window.parent && typeof window.parent.recordUpload === 'function') { window.parent.recordUpload(data); return; } } catch(e){}
                }

                // XMLHttpRequest sarmalama
                var origOpen = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(method, url) {
                    try { this.__url = url; } catch(e){}
                    return origOpen.apply(this, arguments);
                };
                var origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.send = function(body) {
                    try {
                        if (body instanceof FormData) {
                            describeFormData(body, this.__url || '');
                        }
                    } catch(e){}
                    return origSend.apply(this, arguments);
                };

                // fetch sarmalama
                if (window.fetch) {
                    var origFetch = window.fetch;
                    window.fetch = function(input, init) {
                        try {
                            if (init && init.body instanceof FormData) {
                                var url = (typeof input === 'string') ? input : (input && input.url) || '';
                                describeFormData(init.body, url);
                            }
                        } catch(e){}
                        return origFetch.apply(this, arguments);
                    };
                }
            })();
            """
            self.context.add_init_script(upload_logger_js)

            # Global indirme yakalama dinleyicisi (Context seviyesinde)
            self.context.on("download", handle_download)

            # Yerel UYAP ağ geçidinden oturumu alıp bağlanıyoruz (SGK sorgu dosyasının yöntemi)
            print("🔑 Yerel UYAP ağ geçidi (127.0.0.1:8800) üzerinden oturum açılıyor...")
            self.page.on("dialog", lambda dialog: dialog.accept())
            
            try:
                self.page.goto("http://127.0.0.1:8800/giris")
                self.page.wait_for_load_state("networkidle")
                print("✅ Oturum başarıyla yüklendi!")
            except Exception as e:
                print(f"❌ Oturum yüklenemedi: {e}")
                print("Lütfen yerel UYAP ağ geçidinin (uyap_app.py) açık ve Paylaş/Al modunda olduğundan emin olun.")
                print("Varsayılan UYAP sayfasına yönlendiriliyor...")
                self.page.goto("https://avukat.uyap.gov.tr/")

            print("\n" + "="*60)
            print("📡 Ağ trafiği kaydı başladı -> ag_kaydi.log")
            print("🖱️ Tıklama kayıtları başladı -> tiklanan_elementler.jsonl")
            print("📂 Tıklamalar ve ağ logları burada: " + SCRIPT_DIR)
            print("📥 İndirilen dosyalar otomatik olarak kaydedilecek ve imzalanacaktır.")
            print("🔔 Tarayıcıyı manuel kullanabilirsiniz. Sonlandırmak için terminalde Ctrl+C yapın.")
            print("="*60 + "\n")

            # Tarayıcıda en az bir sekme açık olduğu sürece bekle.
            # ÖNEMLİ: time.sleep() KULLANMA! Senkron Playwright olayları
            # (response/download/expose_function) yalnızca ana thread bir
            # Playwright API çağrısının içindeyken işlenir. time.sleep() olay
            # döngüsünü pompalamadığından tüm ağ olayları kuyrukta birikip
            # kaybolur. Bunun yerine page.wait_for_timeout() ile bekliyoruz;
            # bu çağrı olay döngüsünü pompalar ve response olayları anında işlenir.
            try:
                while len(self.context.pages) > 0:
                    try:
                        self.context.pages[0].wait_for_timeout(1000)
                    except Exception:
                        # Sayfa kapanmış olabilir; döngü koşulu bir sonraki turda yakalar
                        time.sleep(0.3)
            except KeyboardInterrupt:
                print("\n🔌 Kullanıcı isteği ile kapatılıyor...")
            finally:
                print("🧹 Tarayıcı oturumu ve Playwright kapatılıyor...")
                self.ag_kaydini_durdur()
                try:
                    if self.context:
                        self.context.close()
                except Exception:
                    pass
                try:
                    if self.playwright:
                        self.playwright.stop()
                except Exception:
                    pass
                print("👋 Görüşmek üzere!")

        # Run within the executor if inside asyncio, or normally
        if in_asyncio:
            self._executor.submit(_impl).result()
        else:
            _impl()

    def ag_kaydini_baslat(self, dosya_yolu=None, anahtar_kelimeler=None):
        """Tüm XHR/fetch isteklerini (method, URL, header, payload, yanıt) bir
        JSONL dosyasına kaydeder.
        """
        if dosya_yolu is None:
            dosya_yolu = os.path.join(SCRIPT_DIR, "ag_kaydi.log")
            # Her çalıştığında eski log dosyasını temizle ve yeni dosyayı hemen oluştur
            try:
                with open(dosya_yolu, "w", encoding="utf-8") as f:
                    pass
            except Exception:
                pass
        self._ag_kayit_yolu = dosya_yolu
        self._ag_kayit_aktif = True

        # Tıklamadan bağımsız, kendi kendine (timer ile) tekrarlayan arka plan
        # istekleri — bunları hiçbir zaman kaydetme (gürültü).
        arka_plan_engel = (
            "ws_sorgu_bakiyesi", "ws_gorusme_bakiyesi", "get_kullanici_bildirimleri",
            "kullanici_bilgileri", "menulistesigetir", "manifest.json",
            "heartbeat", "ping", "keepalive",
        )

        def _ilgili(url):
            # URL'den query parametrelerini temizleyip sadece path'e bakalım
            path = url.split("?")[0].lower()
            if any(path.endswith(e) for e in (
                    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                    ".woff", ".woff2", ".ttf", ".ico", ".map")):
                return False
            if any(k in path for k in arka_plan_engel):
                return False
            if anahtar_kelimeler:
                return any(k in url for k in anahtar_kelimeler)
            return True

        def _response_handler(response):
            if not getattr(self, "_ag_kayit_aktif", False):
                return
            try:
                req = response.request
                url = response.url
                if not _ilgili(url):
                    return

                # --- TIKLAMA KAPISI ---
                # Bu istek bir tıklamanın sonucu mu? Son tıklamadan sonra
                # _tiklama_pencere_sn saniye içinde geldiyse evet, değilse
                # (sayfanın kendi arka plan trafiği) kaydetme.
                now = time.time()
                tetikleyen = self._last_click
                if not tetikleyen or (now - tetikleyen["time"]) > self._tiklama_pencere_sn:
                    return

                # Gövdeyi okumayı denemeden önce içerik türünü kontrol ediyoruz
                govde = "<yanıt gövdesi yok veya okunamadı>"
                try:
                    headers = response.headers
                    content_type = headers.get("content-type", "").lower()
                    
                    # Sadece metin/json/xml/javascript vb. ise ve sayfa kapanmadıysa okumaya çalış
                    is_text = any(t in content_type for t in ("text", "json", "javascript", "xml", "html"))
                    
                    if LOG_RESPONSE_BODY and is_text and response.status < 400:
                        # Sayfa veya tarayıcı durumunu kontrol et
                        if self.page and not self.page.is_closed() and self.context:
                            govde = response.text()
                            if isinstance(govde, str) and len(govde) > 50000:
                                govde = govde[:50000] + "...<kırpıldı>"
                except Exception as e:
                    govde = f"<gövde alınamadı: {type(e).__name__}>"

                # İstek gövdesi: normal text/JSON ise post_data doludur; multipart
                # (dosya yükleme) ise None döner → buffer'dan yapısını çöz.
                istek_payload = req.post_data
                if istek_payload is None:
                    istek_payload = self._istek_govdesi_coz(req)

                req_time_str = time.strftime("%H:%M:%S")
                response_data = {
                    "zaman": req_time_str,
                    "tetikleyen_tiklama": tetikleyen.get("metin", ""),
                    "tetikleyen_locator": tetikleyen.get("locator", ""),
                    "method": req.method,
                    "url": url,
                    "istek_headers": dict(req.headers),
                    "istek_payload": istek_payload,
                    "durum": response.status,
                    "yanit_headers": dict(response.headers),
                    "yanit": govde,
                }

                self._yaz_network_log(response_data)

            except Exception as e:
                # Target closed hatalarını sessizce geç ama NameError gibi diğer kod hatalarını yazdır
                err_msg = str(e)
                if not any(x in err_msg for x in ("Target closed", "closed", "CancelledError", "Future")):
                    print(f"⚠️ Yanıt kaydedilirken hata oluştu: {e}")

        self._ag_response_handler = _response_handler
        self.context.on("response", _response_handler)
        print(f"📡 Ağ kaydı başlatıldı -> {dosya_yolu}")
        return dosya_yolu

    def ag_kaydini_durdur(self):
        self._ag_kayit_aktif = False
        try:
            if getattr(self, "_ag_response_handler", None):
                self.context.remove_listener("response", self._ag_response_handler)
        except Exception:
            pass
        if self._ag_kayit_yolu:
            print(f"📡 Ağ kaydı durduruldu -> {self._ag_kayit_yolu}")


if __name__ == "__main__":
    logger = UyapSessionLogger()
    try:
        logger.run()
    except Exception as e:
        print(f"❌ Çalıştırma hatası: {e}")
