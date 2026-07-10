# -*- coding: utf-8 -*-
"""
Modül çekirdeği: UYAP Oturum Kaydı (Logger)
==========================================
ORİJİNAL  Logger/Uyap_session_logger.py  mantığının panel-modülü uyarlamasıdır.
Çalışma tekniği AYNEN korunur: Playwright ile kalıcı bir Chrome açılır, kullanıcı
UYAP'ı normal kullanırken TÜM tıklamalar ve ağ trafiği (XHR/fetch + upload + indirme)
yakalanıp loglanır.

TEK FARK — SGK Sorgu modülüyle aynı bağlantı: tarayıcı kendi e-imza login'ini
yapmaz; PANEL'in zaten açtığı yerel ofis proxy'sine (http://127.0.0.1:8800/giris)
gider. Yani "UYAP Bağlantısı" (Paylaş/Al) açıksa, oturum oradaki canlı e-imza
oturumundan gelir — ayrı giriş gerekmez.

Bu çekirdek SADECE motoru sürer ve thread-safe bir log tamponu tutar; arayüz
(logger.py) bu tamponu yoklayıp (snapshot) sakin bir günlükte gösterir.
"""

import os
import re
import json
import time
import threading
import asyncio
import logging
import urllib.parse

# asyncio "Future exception was never retrieved" izlerini global olarak sustur
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Çıktılar (loglar + kalıcı tarayıcı profili) bu modülün yanındaki sabit klasörde
LOGGER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logger_data")
os.makedirs(LOGGER_DIR, exist_ok=True)
PROFILE_DIR = os.path.join(LOGGER_DIR, "chrome_profile")
INDIRME_KLASORU = os.path.join(os.path.expanduser("~"), "Downloads")

# UYAP API yanıt gövdelerini de loglamak için True (kapatılınca bazı uyarılar azalır)
LOG_RESPONSE_BODY = True

# Panel ile aynı yerel ofis proxy'si (SGK ile birebir aynı bağlantı)
OFFICE_BASE = "http://127.0.0.1:8800"

# Üretilen modüllerin kaydı — panel açılışta okuyup seçilen menü grubuna yerleştirir.
REGISTRY_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "uretilmis_moduller.json")


def registry_oku():
    """Üretilen modül kayıtlarını döner (yoksa/bozuksa boş liste). Dosyası SİLİNMİŞ
    kayıtlar elenir, böylece ölü registry girdisi paneli bozmaz.
    Her kayıt: {key, label, core, grup, endpoint, adim_sayisi?}."""
    try:
        with open(REGISTRY_DOSYA, encoding="utf-8") as f:
            liste = json.load(f)
        if not isinstance(liste, list):
            return []
    except Exception:
        return []
    moddir = os.path.dirname(REGISTRY_DOSYA)
    return [m for m in liste
            if isinstance(m, dict) and m.get("key") and m.get("core")
            and os.path.exists(os.path.join(moddir, m["core"] + ".py"))]


def registry_ekle(kayit):
    """Bir modül kaydını ekler/günceller (aynı key varsa değiştirir)."""
    try:
        with open(REGISTRY_DOSYA, encoding="utf-8") as f:
            liste = json.load(f)
        if not isinstance(liste, list):
            liste = []
    except Exception:
        liste = []
    liste = [m for m in liste if m.get("key") != kayit.get("key")]
    liste.append(kayit)
    with open(REGISTRY_DOSYA, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)


class SessionLogger:
    """Arka planda Playwright oturum kaydını sürer; logları tamponda biriktirir."""

    def __init__(self, base_url=OFFICE_BASE):
        self.base_url = base_url.rstrip("/")

        # --- thread-safe log tamponu (arayüz snapshot ile çeker) ---
        self._logs = []
        # --- yapılandırılmış ağ kayıtları (Endpoint İnceleme sekmesi çeker) ---
        # Her kayıt: {zaman, endpoint, url, method, durum, payload(dict|None),
        #             payload_ham, yanit}. Metne çevrilmeden ham tutulur ki
        #             diff/kova tablosu alan-alan karşılaştırabilsin.
        self._records = []
        self._lock = threading.Lock()
        self._running = False
        self._status = "Hazır"
        self._stop = False
        self._thread = None

        # Playwright nesneleri (worker thread içinde yaşar)
        self.playwright = None
        self.context = None
        self.page = None

        # Ağ/tıklama durumları
        self._ag_kayit_aktif = False
        self._ag_kayit_yolu = os.path.join(LOGGER_DIR, "ag_kaydi.log")
        self._click_log_yolu = os.path.join(LOGGER_DIR, "tiklamalar.log")
        # Yapılandırılmış kayıtların KALICI kopyası (JSONL). Panel kapanıp açılsa da
        # Endpoint İnceleme dolu gelsin diye her kayıt buraya da bir satır yazılır.
        self._kayit_jsonl = os.path.join(LOGGER_DIR, "kayitlar.jsonl")
        self._ag_response_handler = None
        self._last_click = None          # {"time","locator","metin"}
        self._tiklama_pencere_sn = 8.0   # tıklamadan sonra kaç sn'lik trafik o tıklamaya bağlansın

        # Önceki oturumların kayıtlarını diskten geri yükle (varsa)
        self._kayit_yukle()

    def _kayit_yukle(self):
        """Diske (JSONL) yazılmış önceki yapılandırılmış kayıtları belleğe geri alır;
        böylece panel yeniden açılınca Endpoint İnceleme boş gelmez."""
        try:
            if not os.path.exists(self._kayit_jsonl):
                return
            with open(self._kayit_jsonl, encoding="utf-8") as f:
                satirlar = f.readlines()
            kayitlar = []
            for s in satirlar[-1000:]:        # bellek tamponuyla aynı üst sınır
                s = s.strip()
                if not s:
                    continue
                try:
                    kayitlar.append(json.loads(s))
                except Exception:
                    pass
            with self._lock:
                self._records = kayitlar
            if kayitlar:
                self._log(f"🗂️ {len(kayitlar)} önceki kayıt diskten yüklendi "
                          "(Endpoint İnceleme'de görünür).")
        except Exception:
            pass

    # ─────────────────────────── log / snapshot ───────────────────────────
    def _log(self, msg):
        with self._lock:
            self._logs.append(str(msg))
            if len(self._logs) > 5000:          # tamponu sınırla
                self._logs = self._logs[-4000:]

    def snapshot(self, since_log=0):
        """Arayüz için: since_log indeksinden sonraki yeni log satırları + durum."""
        with self._lock:
            return {
                "logs": self._logs[since_log:],
                "log_n": len(self._logs),
                "running": self._running,
                "status": self._status,
            }

    def records_snapshot(self):
        """Endpoint İnceleme sekmesi için: yapılandırılmış kayıtları endpoint'e
        göre gruplar. Döner: {endpoint: [kayit, ...], ...} (giriş sırası korunur)."""
        with self._lock:
            recs = list(self._records)
        gruplar = {}
        for r in recs:
            gruplar.setdefault(r["endpoint"], []).append(r)
        return gruplar

    def records_list(self):
        """Tüm yapılandırılmış kayıtları YAKALAMA SIRASINDA (düz liste) döner.
        Session→modül üreteci sırayı korumak zorunda olduğu için bunu kullanır."""
        with self._lock:
            return list(self._records)

    def records_temizle(self):
        """Yapılandırılmış kayıt tamponunu boşaltır (yeni bir akış kaydetmeden önce);
        kalıcı JSONL kopyasını da siler."""
        with self._lock:
            self._records = []
        try:
            open(self._kayit_jsonl, "w", encoding="utf-8").close()
        except Exception:
            pass

    @staticmethod
    def _payload_to_dict(payload, headers):
        """Ham istek gövdesini diff/kova tablosu için sözlüğe çevirir.
        JSON ve form-urlencoded çözülür; çözülemezse (ör. multipart özeti) None
        döner → tablo o kaydı 'ham' satır olarak gösterir, alanlara bölmez."""
        if not payload or not isinstance(payload, str):
            return None
        s = payload.strip()
        if not s:
            return None
        ctype = ""
        try:
            for k, v in (headers or {}).items():
                if k.lower() == "content-type":
                    ctype = (v or "").lower()
                    break
        except Exception:
            ctype = ""
        # JSON gövdesi
        if s[:1] in "{[" or "json" in ctype:
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    return {str(k): obj[k] for k in obj}
            except Exception:
                pass
        # form-urlencoded (a=1&b=2). Çok satırlı/multipart özetlerini dışla.
        if "=" in s and "\n" not in s:
            try:
                pairs = urllib.parse.parse_qsl(s, keep_blank_values=True)
                if pairs:
                    d = {}
                    for k, v in pairs:      # tekrar eden alanda son değer kazanır
                        d[k] = v
                    return d
            except Exception:
                pass
        return None

    @property
    def running(self):
        return self._running

    @property
    def log_dir(self):
        return LOGGER_DIR

    # ─────────────────────────── başlat / durdur ───────────────────────────
    def start(self):
        if self._running:
            return False
        self._stop = False
        self._running = True
        self._status = "Tarayıcı açılıyor…"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Durdurma bayrağını set eder; worker döngüsü ~1 sn içinde temizleyip kapanır."""
        if not self._running:
            return
        self._stop = True
        self._status = "Durduruluyor…"

    # ─────────────────────────── ağ kaydı yardımcıları ───────────────────────────
    def _istek_govdesi_coz(self, req):
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
        try:
            return buf.decode("utf-8")
        except Exception:
            return f"<binary gövde, {len(buf)} bayt>"

    def _multipart_ozetle(self, buf, boundary):
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
            name = filename = part_ctype = None
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
                    f"tip={part_ctype} ({len(govde)} bayt) [binary içerik atlandı]")
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
        import json

        def _pretty(deger):
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

        # --- yapılandırılmış kayıt (Endpoint İnceleme için; metne çevirmeden) ---
        try:
            url = kayit.get("url", "") or ""
            endpoint = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or url
            rec = {
                "zaman": kayit.get("zaman", ""),
                "endpoint": endpoint,
                "url": url,
                "method": kayit.get("method", ""),
                "durum": kayit.get("durum", ""),
                "tiklama": tiklama,
                "payload": self._payload_to_dict(
                    kayit.get("istek_payload"), kayit.get("istek_headers")),
                "payload_ham": kayit.get("istek_payload"),
                "yanit": kayit.get("yanit"),
            }
            with self._lock:
                self._records.append(rec)
                if len(self._records) > 1000:
                    self._records = self._records[-800:]
            # kalıcı kopya: kayıt başına bir JSONL satırı (panel kapanırsa kaybolmasın)
            try:
                with open(self._kayit_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass
        except Exception:
            pass

        # --- arayüz günlüğüne kısa özet ---
        try:
            payload = kayit.get("istek_payload") or "(yok)"
            if isinstance(payload, str) and len(payload) > 300:
                payload = payload[:300] + "…"
            govde_ozet = kayit.get("yanit") or ""
            if isinstance(govde_ozet, str) and len(govde_ozet) > 300:
                govde_ozet = govde_ozet[:300] + "…"
            self._log("─" * 60)
            self._log(f"🌐 TIKLAMA → AĞ  [{kayit['zaman']}]  (tıklama: {tiklama!r})")
            self._log(f"   {kayit['method']} {kayit['url']}  → {kayit['durum']}")
            self._log(f"   PAYLOAD : {payload}")
            self._log(f"   YANIT   : {govde_ozet}")
        except Exception:
            pass

        # --- tam kayıt diske ---
        try:
            with open(self._ag_kayit_yolu, "a", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write(f"[{kayit['zaman']}] TETİKLEYEN TIKLAMA: {tiklama!r}\n")
                f.write(f"               LOCATOR: {kayit.get('tetikleyen_locator', '')}\n")
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
            self._log(f"⚠️ Ağ kaydı yazılamadı: {e}")

    def _ag_kaydini_baslat(self):
        # Eski log dosyasını temizle ve yeniden oluştur
        try:
            with open(self._ag_kayit_yolu, "w", encoding="utf-8"):
                pass
        except Exception:
            pass
        self._ag_kayit_aktif = True

        # Kendi kendine tekrarlayan arka plan istekleri — gürültü, kaydetme
        arka_plan_engel = (
            "ws_sorgu_bakiyesi", "ws_gorusme_bakiyesi", "get_kullanici_bildirimleri",
            "kullanici_bilgileri", "menulistesigetir", "manifest.json",
            "heartbeat", "ping", "keepalive",
        )

        def _ilgili(url):
            path = url.split("?")[0].lower()
            if any(path.endswith(e) for e in (
                    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                    ".woff", ".woff2", ".ttf", ".ico", ".map")):
                return False
            if any(k in path for k in arka_plan_engel):
                return False
            return True

        def _response_handler(response):
            if not self._ag_kayit_aktif:
                return
            try:
                req = response.request
                url = response.url
                if not _ilgili(url):
                    return

                # TIKLAMA KAPISI: yalnızca bir tıklamadan hemen sonra gelen trafik
                now = time.time()
                tetikleyen = self._last_click
                if not tetikleyen or (now - tetikleyen["time"]) > self._tiklama_pencere_sn:
                    return

                govde = "<yanıt gövdesi yok veya okunamadı>"
                try:
                    headers = response.headers
                    content_type = headers.get("content-type", "").lower()
                    is_text = any(t in content_type for t in
                                  ("text", "json", "javascript", "xml", "html"))
                    if LOG_RESPONSE_BODY and is_text and response.status < 400:
                        if self.page and not self.page.is_closed() and self.context:
                            govde = response.text()
                            if isinstance(govde, str) and len(govde) > 50000:
                                govde = govde[:50000] + "...<kırpıldı>"
                except Exception as e:
                    govde = f"<gövde alınamadı: {type(e).__name__}>"

                istek_payload = req.post_data
                if istek_payload is None:
                    istek_payload = self._istek_govdesi_coz(req)

                self._yaz_network_log({
                    "zaman": time.strftime("%H:%M:%S"),
                    "tetikleyen_tiklama": tetikleyen.get("metin", ""),
                    "tetikleyen_locator": tetikleyen.get("locator", ""),
                    "method": req.method,
                    "url": url,
                    "istek_headers": dict(req.headers),
                    "istek_payload": istek_payload,
                    "durum": response.status,
                    "yanit_headers": dict(response.headers),
                    "yanit": govde,
                })
            except Exception as e:
                err = str(e)
                if not any(x in err for x in ("Target closed", "closed", "CancelledError", "Future")):
                    self._log(f"⚠️ Yanıt kaydedilirken hata: {e}")

        self._ag_response_handler = _response_handler
        self.context.on("response", _response_handler)
        self._log(f"📡 Ağ kaydı başlatıldı → {self._ag_kayit_yolu}")

    def _ag_kaydini_durdur(self):
        self._ag_kayit_aktif = False
        try:
            if self._ag_response_handler:
                self.context.remove_listener("response", self._ag_response_handler)
        except Exception:
            pass

    # ─────────────────────────── tıklama / upload / indirme ───────────────────────────
    def _log_click(self, click_data):
        try:
            locator = click_data.get("locator", "")
            metin = click_data.get("metin", "")
            zaman = click_data.get("zaman", "")
            self._last_click = {"time": time.time(), "locator": locator, "metin": metin}
            with open(self._click_log_yolu, "a", encoding="utf-8") as f:
                f.write(f"# [{zaman}] {click_data.get('etiket', '')} -> {metin!r}\n")
                f.write(locator + "\n")
                if ".get_by_" not in locator and "#" not in locator:
                    f.write(f"#   yedek xpath: {click_data.get('xpath_yedek', '')}\n")
                f.write("\n")
                f.flush()
            self._log(f"🖱️ Tıklama: {metin!r} -> {locator}")
        except Exception as e:
            self._log(f"❌ Tıklama loglama hatası: {e}")

    def _log_upload(self, data):
        try:
            url = data.get("url", "")
            alanlar = data.get("alanlar", [])
            zaman = data.get("zaman", "")
            satirlar = ["=" * 80,
                        f"[{zaman}] 📤 UPLOAD FORMDATA YAKALANDI (JS hook)",
                        f"URL: {url}", "--- FORM ALANLARI ---"]
            for a in alanlar:
                if a.get("tip") == "dosya":
                    satirlar.append(
                        f"  • ALAN '{a.get('ad')}' = DOSYA filename='{a.get('filename')}' "
                        f"tip={a.get('contentType')} ({a.get('boyut')} bayt)")
                else:
                    satirlar.append(f"  • ALAN '{a.get('ad')}' = {a.get('deger')!r}")
            blok = "\n".join(satirlar) + "\n"
            self._log(blok)
            try:
                with open(self._ag_kayit_yolu, "a", encoding="utf-8") as f:
                    f.write(blok + "\n")
                    f.flush()
            except Exception:
                pass
        except Exception as e:
            self._log(f"❌ Upload loglama hatası: {e}")

    # Güvenlik raporu bulgu #10: bu kalıcı Chrome profili yalnızca UYAP'a kilitli değil —
    # kullanıcı pencereyi normal gezinme için de kullanıyor. Bu yüzden "indirme" olayı,
    # ziyaret edilen HERHANGİ bir siteden tetiklenebilir. UYAP'ın gerçekten ürettiği dosyalar
    # bu uzantılardan biridir; bunun dışındaki (ör. .exe/.bat/.js/.vbs/.ps1/.scr/.msi/.jar)
    # bir dosya otomatik açılmaz (yol geçişiyle Downloads dışına yazdırılıp sessizce
    # çalıştırılmasını önler) — yalnızca kaydedilir ve loglanır.
    _GUVENLI_ACMA_UZANTILARI = {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
        ".jpg", ".jpeg", ".png", ".gif", ".tiff", ".zip",
    }

    def _handle_download(self, download):
        self._log("📥 İndirme algılandı, dosya diske kaydediliyor…")
        try:
            orijinal = os.path.basename(download.suggested_filename or "UYAP_indirme.pdf")
            kayit_yolu = os.path.join(INDIRME_KLASORU, orijinal)
            download.save_as(kayit_yolu)
            self._log(f"📂 Dosya kaydedildi: {kayit_yolu}")

            ext = os.path.splitext(orijinal)[1].lower()
            if ext not in self._GUVENLI_ACMA_UZANTILARI:
                self._log(f"⚠️ '{ext}' uzantılı dosya güvenlik nedeniyle otomatik açılmadı "
                          "(UYAP'ın normalde üretmediği bir tür).")
                return

            def _ac():
                try:
                    os.startfile(kayit_yolu)
                except Exception as e:
                    self._log(f"❌ Dosya açma hatası: {e}")

            threading.Thread(target=_ac, daemon=True).start()
        except Exception as e:
            self._log(f"❌ İndirme yakalama hatası: {e}")

    # ─────────────────────────── worker (Playwright) ───────────────────────────
    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self._log(f"❌ Playwright bulunamadı: {e}")
            self._status = "Playwright yok"
            self._running = False
            return

        try:
            self._log("🚀 Playwright başlatılıyor…")
            self.playwright = sync_playwright().start()

            # TargetClosedError uyarılarını sustur
            try:
                loop = asyncio.get_event_loop()

                def custom_handler(loop, context):
                    exc = context.get("exception")
                    if exc and ("TargetClosedError" in str(exc)
                                or "Target page, context or browser has been closed" in str(exc)
                                or isinstance(exc, asyncio.CancelledError)):
                        return
                    loop.default_exception_handler(context)

                loop.set_exception_handler(custom_handler)
            except Exception:
                pass

            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
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
                ],
            )

            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

            # ağ kaydı + tıklama/upload yakalayıcıları
            self._ag_kaydini_baslat()
            self.context.expose_function("recordClick", self._log_click)
            self.context.expose_function("recordUpload", self._log_upload)
            self.context.add_init_script(_CLICK_LOGGER_JS)
            self.context.add_init_script(_UPLOAD_LOGGER_JS)
            self.context.on("download", self._handle_download)

            # PANEL bağlantısı: kendi login'i yok; ofis proxy'sinin /giris'ine git
            self._log(f"🔑 Panel bağlantısı kullanılacak: {self.base_url}/giris "
                      "(UYAP Bağlantısı / Paylaş-Al açık olmalı)")
            self.page.on("dialog", lambda d: d.accept())
            try:
                self.page.goto(f"{self.base_url}/giris")
                self.page.wait_for_load_state("networkidle")
                self._log("✅ Oturum yüklendi! Tarayıcıyı normal kullanın; tıklama ve ağ trafiği kaydediliyor.")
            except Exception as e:
                self._log(f"❌ Oturum yüklenemedi: {e}")
                self._log("Panel'de 'UYAP Bağlantısı' açık ve Paylaş/Al aktif mi? "
                          "Varsayılan UYAP sayfasına geçiliyor…")
                try:
                    self.page.goto("https://avukat.uyap.gov.tr/")
                except Exception:
                    pass

            self._status = "Kaydediyor"
            self._log("📂 Çıktılar: " + LOGGER_DIR)

            # Tarayıcı açık olduğu + durdurulmadığı sürece bekle.
            # ÖNEMLİ: time.sleep() değil wait_for_timeout() — olay döngüsünü pompalar,
            # yoksa response/expose_function olayları işlenmez.
            while not self._stop and len(self.context.pages) > 0:
                try:
                    self.context.pages[0].wait_for_timeout(1000)
                except Exception:
                    time.sleep(0.3)

        except Exception as e:
            self._log(f"❌ Çalıştırma hatası: {e}")
        finally:
            self._log("🧹 Tarayıcı oturumu kapatılıyor…")
            self._ag_kaydini_durdur()
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
            self.context = None
            self.page = None
            self.playwright = None
            self._running = False
            self._status = "Durduruldu"
            self._log("👋 Oturum kaydı sonlandı.")


# ═══════════════════════════ MODÜL TASLAĞI ÜRETECİ ═══════════════════════════
# Endpoint İnceleme sekmesinde onaylanan kova/parametre kararlarından, mevcut
# *_core.py kalıbına uygun bir `taslak_<endpoint>_core.py` iskeleti üretir.
# ÇALIŞAN modüllere dokunmaz; yalnızca yeni bir taslak dosya yazar. Hibrit
# güvence: ham payload örneği dosyanın başına yorum olarak gömülür.

KOVA_SABIT = "sabit"   # 🔒 hep aynı → literal gömülür
KOVA_GIRDI = "girdi"   # ✏️ kullanıcı verir → fonksiyon parametresi
KOVA_IC = "ic"         # ⚙️ değişir ama programın işi → TODO ile bırakılır


def _kimlik(s, varsayilan="alan"):
    """Rastgele bir alan/endpoint adını geçerli bir python tanımlayıcısına çevirir."""
    s = re.sub(r"\.ajx$", "", str(s or ""), flags=re.I)
    s = re.sub(r"[^0-9A-Za-z_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or s[0].isdigit():
        s = (varsayilan + "_" + s).strip("_")
    return s or varsayilan


def _kova_diff_oner(kayitlar):
    """Aynı endpoint'in tekrarlarına bakıp her alan için kova ÖN-önerisi üretir
    (makine tarafı). Değişen alan → girdi, sabit kalan → sabit. ⚙️ (iç/otomatik)
    ayrımını makine yapamaz; onu kullanıcı tabloda işaretler."""
    paydict_list = [k.get("payload") for k in kayitlar if isinstance(k.get("payload"), dict)]
    oneriler = {}
    if not paydict_list:
        return oneriler
    tum_alanlar = []
    for pd in paydict_list:
        for a in pd:
            if a not in tum_alanlar:
                tum_alanlar.append(a)
    for a in tum_alanlar:
        degerler = {repr(pd.get(a)) for pd in paydict_list if a in pd}
        oneriler[a] = KOVA_GIRDI if len(degerler) > 1 else KOVA_SABIT
    return oneriler


def _temsili_payload(kayitlar):
    """Literal değerleri çekmek için en dolu (en çok alanlı) payload'ı seçer."""
    en_iyi = {}
    for k in kayitlar:
        pd = k.get("payload")
        if isinstance(pd, dict) and len(pd) > len(en_iyi):
            en_iyi = pd
    return en_iyi


def _ilk_liste(obj):
    """Bir yanıt nesnesinin içindeki kayıt LİSTESİNİ bulur. Doğrudan liste ise
    onu; sözlük ise içindeki (en uzun) sözlük-listesi alanını döndürür.
    Döner: (liste, erisim_yolu) — erisim_yolu="" doğrudan, "data" → veri['data']."""
    if isinstance(obj, list):
        return obj, ""
    if isinstance(obj, dict):
        en_iyi, yol = [], None
        for k, v in obj.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) > len(en_iyi):
                en_iyi, yol = v, k
        if yol is not None:
            return en_iyi, yol
    return [], None


def yanit_alanlari(kayitlar):
    """Gözlenen yanıttan, modül EKRANI için alan listesini çıkarır (response tarafı).
    Döner: (erisim_yolu, [(alan, ornek_deger), ...]). erisim_yolu None → liste
    bulunamadı (yanıt tek sözlük/başka biçim)."""
    for k in kayitlar:
        y = k.get("yanit")
        obj = y if isinstance(y, (dict, list)) else None
        if obj is None and isinstance(y, str):
            try:
                obj = json.loads(y)
            except Exception:
                obj = None
        if obj is None:
            continue
        liste, yol = _ilk_liste(obj)
        if liste and isinstance(liste[0], dict):
            ornek = liste[0]
            return yol, [(a, ornek.get(a)) for a in ornek.keys()]
        if isinstance(obj, dict):
            return None, [(a, obj.get(a)) for a in obj.keys()]
    return None, []


def _beklenen_satir_say(kayitlar):
    """Yakalanan yanıttan beklenen satır sayısını çıkarır (doğrulama için).
    Liste döndüyse uzunluğu, tek sözlükse 1; çözülemezse None."""
    for k in kayitlar:
        y = k.get("yanit")
        obj = y if isinstance(y, (dict, list)) else None
        if obj is None and isinstance(y, str):
            try:
                obj = json.loads(y)
            except Exception:
                obj = None
        if obj is None:
            continue
        liste, _yol = _ilk_liste(obj)
        if liste:
            return len(liste)
        if isinstance(obj, dict):
            return 1
    return None


def modul_taslagi_uret(endpoint, kayitlar, kovalar, param_adlari=None,
                       kolonlar=None, hedef_dir=None, dosya_adi=None):
    """Onaylanan kararlardan taslak bir *_core.py dosyası yazar; tam yolunu döner.

    endpoint     : "search_phrase_detayli.ajx" gibi
    kayitlar     : o endpoint'e ait yapılandırılmış kayıt listesi (records_snapshot'tan)
    kovalar      : {alan: KOVA_SABIT|KOVA_GIRDI|KOVA_IC}  (istek/payload — kullanıcı onaylı)
    param_adlari : {alan: "python_parametre_adi"}  (girdi alanları için; boşsa türetilir)
    kolonlar     : [(yanit_alani, "Ekran Başlığı"), ...]  (response → modül ekranı;
                   sıralı; boşsa yanıttan otomatik tüm alanlar gösterilir)
    """
    param_adlari = param_adlari or {}
    hedef_dir = hedef_dir or os.path.dirname(os.path.abspath(__file__))
    temsili = _temsili_payload(kayitlar)

    # Alan sırası: tüm kayıtların payload'larındaki ilk-görülme sırası
    alan_sira = []
    for k in kayitlar:
        pd = k.get("payload")
        if isinstance(pd, dict):
            for a in pd:
                if a not in alan_sira:
                    alan_sira.append(a)

    # Girdi alanları → benzersiz parametre adları (imza sırasına göre)
    parametreler, kullanilmis = [], set()
    alan2param = {}
    for a in alan_sira:
        if kovalar.get(a) != KOVA_GIRDI:
            continue
        ad = _kimlik(param_adlari.get(a) or a, "p")
        taban, n = ad, 2
        while ad in kullanilmis:
            ad = f"{taban}_{n}"; n += 1
        kullanilmis.add(ad)
        alan2param[a] = ad
        parametreler.append(ad)

    # payload sözlüğü satırları
    pay_satir = []
    for a in alan_sira:
        kova = kovalar.get(a, KOVA_SABIT)
        if kova == KOVA_GIRDI:
            pay_satir.append(f'        {a!r}: {alan2param[a]},  # ✏️ girdi')
        elif kova == KOVA_IC:
            gozlenen = sorted({repr(k["payload"].get(a)) for k in kayitlar
                               if isinstance(k.get("payload"), dict) and a in k["payload"]})
            ozet = ", ".join(gozlenen[:4]) + ("…" if len(gozlenen) > 4 else "")
            pay_satir.append(
                f'        {a!r}: {temsili.get(a)!r},  # ⚙️ iç/otomatik — TODO: '
                f'programca hesapla (gözlenen: {ozet})')
        else:  # sabit
            pay_satir.append(f'        {a!r}: {temsili.get(a)!r},  # 🔒 sabit')
    pay_blok = "\n".join(pay_satir) if pay_satir else "        # (çözümlenebilir payload yok)"

    imza = ", ".join(parametreler + ["log_fn=None"])
    fn = _kimlik(endpoint, "sorgula")

    # Girdi alanları (✏️) → runner/örnek meta verisi
    param_meta = [(alan2param[a], a, temsili.get(a))
                  for a in alan_sira if kovalar.get(a) == KOVA_GIRDI]
    param_meta_satir = "\n".join(
        f"    ({p!r}, {a!r}, {o!r})," for p, a, o in param_meta
    ) or "    # (girdi alanı yok — tüm alanlar sabit)"
    ornek_satir = "\n".join(f"    {p!r}: {o!r}," for p, _a, o in param_meta) or "    # (girdi yok)"
    cagri_args = "".join(f"girdi.get({p!r}), " for p, _a, _o in param_meta) + "log_fn=log_fn"

    ham_ornek = ""
    for k in kayitlar:
        if k.get("payload_ham"):
            ham = str(k["payload_ham"])
            ham_ornek = ham[:1200] + ("…" if len(ham) > 1200 else "")
            break

    icerik = f'''# -*- coding: utf-8 -*-
"""
AĞ SORGU MODÜLÜ — {endpoint}
{"=" * (15 + len(endpoint))}
Logger'ın yakaladığı tıklamanın AĞ İSTEĞİNİ doğrudan tekrar gönderir. İstek yerel
ofis proxy'sine (127.0.0.1:8800) POST edilir; ofis (uyap_app.py) onu canlı e-imza
UYAP oturumuyla iletir — tarayıcı/Playwright GEREKMEZ. ("UYAP Bağlantısı" /
Paylaş-Al açık olmalı.) İşaretler: 🔒 sabit · ✏️ girdi · ⚙️ iç/otomatik (TODO).

Doğrudan çalıştır:   python <bu_dosya>.py     → örnek girdiyle sorgular, yanıtı basar
İçe aktar:           from ... import {fn}

--- YAKALANAN HAM İSTEK ÖRNEĞİ ---
{ham_ornek or "(yok)"}
"""

import json
import urllib.request
import urllib.error

OFFICE_BASE = "http://127.0.0.1:8800"
ENDPOINT = {endpoint!r}

# İsteğin güvenli başlıkları (oturum/yetki ofiste otomatik eklenir).
COMMON_HEADERS = {{
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "cache-control": "no-cache",
    "referer": "https://avukat.uyap.gov.tr/",
}}

# Doğrudan çalıştırınca kullanılacak yakalanan örnek girdi.
ORNEK_GIRDI = {{
{ornek_satir}
}}

# Arayüz girdi alanları: (python_param, orijinal_alan, yakalanan_örnek).
# Menüdeki "çalıştır-gör" ekranı (runner) bu listeden girdi kutularını kurar.
PARAMETRELER = [
{param_meta_satir}
]


def {fn}({imza}):
    """{endpoint} ağ isteğini 8800 ofis proxy'sine gönderir; yanıtı (JSON çözülürse
    dict/list, yoksa ham metin) döner. Hata olursa {{'_hata': ...}} döner."""
    payload = {{
{pay_blok}
    }}
    govde = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    istek = urllib.request.Request(OFFICE_BASE + "/" + ENDPOINT, data=govde, method="POST")
    for _ad, _deg in COMMON_HEADERS.items():
        istek.add_header(_ad, _deg)
    try:
        with urllib.request.urlopen(istek, timeout=90) as _y:
            _durum = _y.status
            _metin = _y.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as _e:
        if log_fn: log_fn(f"{{ENDPOINT}} -> HTTP {{_e.code}}")
        return {{"_hata": f"HTTP {{_e.code}}", "_govde": _e.read().decode("utf-8", "replace")[:500]}}
    except urllib.error.URLError as _e:
        return {{"_hata": f"Ofise ulasilamadi ({{getattr(_e, 'reason', _e)}}). "
                          "uyap_app.py acik ve Paylas/Al aktif mi?"}}
    if log_fn: log_fn(f"{{ENDPOINT}} -> HTTP {{_durum}}")
    try:
        return json.loads(_metin)
    except Exception:
        return _metin


def calistir(girdi=None, log_fn=None):
    """Genel giriş noktası: runner buraya {{param: değer}} sözlüğü verir; biz onu
    {fn}() imzasına eşleriz. Döner: {fn}() ile aynı (dict/list/metin)."""
    girdi = girdi or {{}}
    return {fn}({cagri_args})


def _kendini_dogrula(log_fn=None):
    """Modülü CANLI çalıştırır: yakalanan tıklamanın ağ isteğini gerçekten gönderir."""
    sonuc = {fn}(**ORNEK_GIRDI, log_fn=log_fn)
    if isinstance(sonuc, dict) and sonuc.get("_hata"):
        return {{"ok": False, "mesaj": sonuc["_hata"]}}
    n = len(sonuc) if isinstance(sonuc, (list, dict, str)) else 0
    return {{"ok": True, "mesaj": f"Canlı istek başarılı, yanıt alındı (uzunluk {{n}})."}}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _sonuc = {fn}(**ORNEK_GIRDI, log_fn=print)
    print(json.dumps(_sonuc, ensure_ascii=False, indent=2, default=str)
          if isinstance(_sonuc, (dict, list)) else _sonuc)
'''

    # Dosya adı: kullanıcı verdiyse onu, yoksa endpoint'ten "sorgu_…" türet. Çakışırsa _2, _3…
    taban = _kimlik(dosya_adi, "modul") if dosya_adi else "sorgu_" + _kimlik(endpoint, "modul")
    aday, n = taban, 1
    while os.path.exists(os.path.join(hedef_dir, aday + ".py")):
        n += 1
        aday = f"{taban}_{n}"
    core_modul = aday
    core_yol = os.path.join(hedef_dir, core_modul + ".py")
    with open(core_yol, "w", encoding="utf-8") as f:
        f.write(icerik)
    return {"key": core_modul, "label": endpoint, "endpoint": endpoint,
            "core": core_modul, "core_yol": core_yol}


# ═══════════════════════════ SESSION (OTURUM AKIŞI) ÜRETECİ ═══════════════════════════
# Tek endpoint yerine, yakalanan TÜM oturumu SIRAYLA çalışan tek bir modüle çevirir.
# Kilit fikir: bir adımın payload değeri daha ÖNCEKİ bir adımın YANITINDA da geçiyorsa,
# onu literal gömmek yerine o yanıttan besler (⛓️ chaining) — böylece akış her
# çalıştığında doğru iç kimlikleri kendisi bulur; ölü bir tekrar olmaz.


def _zincirlenebilir(v):
    """Yalnızca 'anlamlı' skalerleri zincirle (kısa sabitleri/bayrağı atla)."""
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, int):
        return len(str(v)) >= 5
    if isinstance(v, str):
        return len(v) >= 5
    return False


def _zincir_yolu(obj, hedef):
    """obj (parse edilmiş yanıt) içinde hedef skalerin ERİŞİM YOLUNU bulur:
    örn. "[0]['dosyaId']". Bulunmazsa None. Listelerde [index] kullanılır;
    canlıda sıra değişebileceğinden üretilen kodda bu bir varsayımdır."""
    def gez(o, yol):
        if isinstance(o, bool):
            return None
        if isinstance(o, (str, int)) and o == hedef and yol:
            return yol
        if isinstance(o, dict):
            for k, v in o.items():
                r = gez(v, yol + f"[{k!r}]")
                if r is not None:
                    return r
        elif isinstance(o, list):
            for i, v in enumerate(o):
                r = gez(v, yol + f"[{i}]")
                if r is not None:
                    return r
        return None
    return gez(obj, "")


def _parse_yanit(k):
    """Bir kaydın yanıtını parse edilmiş nesneye çevirir (str→json); olmazsa None."""
    y = k.get("yanit")
    if isinstance(y, (dict, list)):
        return y
    if isinstance(y, str):
        try:
            return json.loads(y)
        except Exception:
            return None
    return None


def oturum_akisi_uret(kayitlar, dosya_adi, hedef_dir=None):
    """Yakalanan oturumu (sıralı kayıtlar) TEK bir akış modülüne çevirir.
    `calistir(girdi, log_fn)` adımları sırayla _post eder; ⛓️ alanlar önceki
    adımın yanıtından beslenir. İlk adımın skaler alanları arayüz parametresi olur.
    Döner: {key,label,core,gui,...} (modul_taslagi_uret ile aynı sözleşme)."""
    hedef_dir = hedef_dir or os.path.dirname(os.path.abspath(__file__))
    # Anlamlı adımlar: payload'ı çözülmüş ya da bir yanıtı olanlar
    adimlar = [k for k in kayitlar
               if isinstance(k.get("payload"), dict) or k.get("yanit")]
    if not adimlar:
        raise ValueError("Yakalanan oturumda çözümlenebilir adım yok.")
    objs = [_parse_yanit(k) for k in adimlar]

    # İlk adımın top-level skaler alanları → arayüz parametreleri (giriş noktası)
    param_meta, alan2param = [], {}
    ilk_pay = adimlar[0].get("payload")
    if isinstance(ilk_pay, dict):
        kullanilmis = set()
        for a, v in ilk_pay.items():
            if isinstance(v, (dict, list)):
                continue
            p = _kimlik(a, "p")
            tb, n = p, 2
            while p in kullanilmis:
                p = f"{tb}_{n}"; n += 1
            kullanilmis.add(p)
            alan2param[a] = p
            param_meta.append((p, a, v))

    # ── adım adım gövde + chaining ──
    govde = []
    son_var, son_liste_n = "None", None
    for i, (k, obj) in enumerate(zip(adimlar, objs), start=1):
        ep = k.get("endpoint") or (k.get("url", "").split("?")[0].rsplit("/", 1)[-1])
        tik = k.get("tiklama", "")
        payload = k.get("payload")
        # NOT: log mesajı ASCII '->' kullanır; üretilen modül Türkçe (cp1254) konsola da basabilir.
        govde.append("    log(%r)" % (f"Adim {i}/{len(adimlar)}: {tik} -> {ep}"))
        if isinstance(payload, dict):
            govde.append(f"    payload_{i} = {{")
            for a, v in payload.items():
                ref, kaynak_j = None, None
                if _zincirlenebilir(v):
                    for j in range(i - 1, 0, -1):       # en yakın önceki adımdan başla
                        yp = _zincir_yolu(objs[j - 1], v)
                        if yp is not None:
                            ref, kaynak_j = f"adim{j}_sonuc{yp}", j
                            break
                if ref:
                    govde.append(f"        {a!r}: {ref},  # zincir: adim {kaynak_j} yanitindan")
                elif i == 1 and a in alan2param:
                    govde.append(
                        f"        {a!r}: girdi.get({alan2param[a]!r}, {v!r}),  # girdi")
                else:
                    govde.append(f"        {a!r}: {v!r},")
            govde.append("    }")
            govde.append(f"    adim{i}_sonuc = _gonder({ep!r}, payload_{i}, log)")
        else:
            govde.append("    # (bu adimin cozumlenebilir payload'i yok)")
            govde.append(f"    adim{i}_sonuc = None")
        son_var = f"adim{i}_sonuc"

        # son liste-döndüren adımın uzunluğu (doğrulama beklentisi için)
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            son_liste_n = len(obj)
        elif isinstance(obj, dict):
            liste, _yol = _ilk_liste(obj)
            if liste and isinstance(liste[0], dict):
                son_liste_n = len(liste)

    # Akışın sonucu = SON adımın ham yanıtı (yakalanan tüm istekler sırayla gönderildi)
    govde.append(f"    return {son_var}")

    govde_text = "\n".join(govde)
    param_meta_satir = "\n".join(
        f"    ({p!r}, {a!r}, {ornek!r})," for p, a, ornek in param_meta
    ) or "    # (ilk adımda skaler girdi yok)"
    ornek_satir = "\n".join(f"    {p!r}: {o!r}," for p, _a, o in param_meta) or "    # (girdi yok)"
    beklenen = {"satir_sayisi": son_liste_n}
    baslik = f"OTURUM AKISI — {dosya_adi}"

    icerik = f'''# -*- coding: utf-8 -*-
"""
{baslik}
{"=" * len(baslik)}
Logger'in "Session'u module cevir" ile OTOMATIK uretildi. Yakalanan {len(adimlar)}
adimlik oturumu SIRAYLA tekrar GONDERIR. Her istek 127.0.0.1:8800 ofis proxy'sine
POST edilir; ofis (uyap_app.py) onu canli e-imza UYAP oturumuyla iletir — tarayici
GEREKMEZ. "zincir" ile isaretli alanlar bir ONCEKI adimin yanitindan otomatik
beslenir; "girdi" ilk adimin girdileridir; kalani yakalanan sabittir.

Dogrudan calistir:  python <bu_dosya>.py     -> akisi calistirir, son yaniti basar

UYARI: liste yanitlarinda [index] (genelde [0]) varsayimi kullanildi; cok kayitli
       adimlarda gozden gecir.
"""

import json
import urllib.request
import urllib.error

OFFICE_BASE = "http://127.0.0.1:8800"

COMMON_HEADERS = {{
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "cache-control": "no-cache",
    "referer": "https://avukat.uyap.gov.tr/",
}}

# Ilk adimin skaler girdileri: (python_param, alan, ornek).
PARAMETRELER = [
{param_meta_satir}
]

# Dogrudan calistirinca kullanilacak ornek girdi.
ORNEK_GIRDI = {{
{ornek_satir}
}}

_BEKLENEN = {beklenen!r}


def _gonder(endpoint, payload, log=None):
    """Tek bir istegi 8800 ofis proxy'sine POST eder; yaniti (dict/list/metin) doner."""
    _govde = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _istek = urllib.request.Request(OFFICE_BASE + "/" + endpoint, data=_govde, method="POST")
    for _ad, _deg in COMMON_HEADERS.items():
        _istek.add_header(_ad, _deg)
    try:
        with urllib.request.urlopen(_istek, timeout=90) as _y:
            _metin = _y.read().decode("utf-8", "replace")
            if log: log(f"{{endpoint}} -> HTTP {{_y.status}}")
    except urllib.error.HTTPError as _e:
        if log: log(f"{{endpoint}} -> HTTP {{_e.code}}")
        return {{"_hata": f"HTTP {{_e.code}}"}}
    except urllib.error.URLError as _e:
        return {{"_hata": f"Ofise ulasilamadi ({{getattr(_e, 'reason', _e)}})."}}
    try:
        return json.loads(_metin)
    except Exception:
        return _metin


def calistir(girdi=None, log_fn=None):
    """Yakalanan oturumu sirayla tekrar gonderir. "zincir" alanlar adimlar arasi beslenir.
    Doner: SON adimin ham yaniti."""
    girdi = girdi or {{}}
    log = log_fn or (lambda *a, **k: None)
{govde_text}


def _kendini_dogrula(log_fn=None):
    """Tum akisi CANLI calistirir; son adimin yanitini yakalamayla kiyaslar."""
    try:
        sonuc = calistir(ORNEK_GIRDI, log_fn)
    except Exception as e:
        return {{"ok": False, "mesaj": f"Akis hata verdi: {{e}}"}}
    if isinstance(sonuc, dict) and sonuc.get("_hata"):
        return {{"ok": False, "mesaj": sonuc["_hata"]}}
    n = len(sonuc) if isinstance(sonuc, (list, dict, str)) else 0
    bekl = _BEKLENEN.get("satir_sayisi")
    if bekl is None:
        return {{"ok": True, "mesaj": f"Akis tamamlandi, yanit alindi (uzunluk {{n}})."}}
    return {{"ok": (n > 0) if bekl > 0 else True,
            "mesaj": f"Akis tamamlandi: uzunluk {{n}} (yakalamada {{bekl}})."}}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _sonuc = calistir(ORNEK_GIRDI, log_fn=print)
    print(json.dumps(_sonuc, ensure_ascii=False, indent=2, default=str)
          if isinstance(_sonuc, (dict, list)) else _sonuc)
'''

    # Dosya adı: kullanıcı verdiği ad; çakışırsa _2, _3… Tek dosya (GUI/registry yok).
    taban = _kimlik(dosya_adi, "akis")
    aday, n = taban, 1
    while os.path.exists(os.path.join(hedef_dir, aday + ".py")):
        n += 1
        aday = f"{taban}_{n}"
    core_modul = aday
    core_yol = os.path.join(hedef_dir, core_modul + ".py")
    with open(core_yol, "w", encoding="utf-8") as f:
        f.write(icerik)
    return {"key": core_modul, "label": dosya_adi, "endpoint": "(oturum akışı)",
            "core": core_modul, "core_yol": core_yol, "adim_sayisi": len(adimlar)}


# ─────────────────────────── enjekte edilen JS (orijinalle birebir) ───────────────────────────
_CLICK_LOGGER_JS = r"""
(function() {
    function getXPath(el) {
        if (el.id) { return `//*[@id="${el.id}"]`; }
        if (el === document.body) { return '/html/body'; }
        let index = 1;
        let sib = el.previousSibling;
        while (sib) {
            if (sib.nodeType === 1 && sib.tagName === el.tagName) { index++; }
            sib = sib.previousSibling;
        }
        const tagName = el.tagName.toLowerCase();
        const parentXPath = el.parentNode ? getXPath(el.parentNode) : '';
        return `${parentXPath}/${tagName}[${index}]`;
    }
    function getCSSSelector(el) {
        if (el.id) { return `#${el.id}`; }
        const path = [];
        let cur = el;
        while (cur && cur.nodeType === 1) {
            let selector = cur.tagName.toLowerCase();
            if (cur.id) { selector += `#${cur.id}`; path.unshift(selector); break; }
            else {
                let sib = cur, nth = 1;
                while (sib = sib.previousElementSibling) { if (sib.tagName === cur.tagName) nth++; }
                if (nth > 1) { selector += `:nth-of-type(${nth})`; }
            }
            path.unshift(selector);
            cur = cur.parentNode;
        }
        return path.join(' > ');
    }
    function pyStr(s) { return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").trim(); }
    function strongLocator(el) {
        if (!el || el.nodeType !== 1) return null;
        const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test');
        if (testid) return `page.get_by_test_id('${pyStr(testid)}')`;
        if (el.id) return `page.locator('#${pyStr(el.id)}')`;
        const name = el.getAttribute('name');
        if (name) return `page.locator('${el.tagName.toLowerCase()}[name="${pyStr(name)}"]')`;
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel) return `page.get_by_label('${pyStr(ariaLabel)}')`;
        const placeholder = el.getAttribute('placeholder');
        if (placeholder) return `page.get_by_placeholder('${pyStr(placeholder)}')`;
        return null;
    }
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
    function bestLocator(target, interactive, text, css) {
        return strongLocator(target)
            || strongLocator(interactive)
            || roleTextLocator(interactive, text)
            || roleTextLocator(target, target.innerText ? target.innerText.trim().substring(0,80) : '')
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
                interactive.classList.contains('dx-list-item')) { break; }
            interactive = interactive.parentNode;
        }
        if (!interactive || interactive === document.body) { interactive = target; }
        const text = interactive.innerText ? interactive.innerText.trim().substring(0, 80) : '';
        const interactiveCss = getCSSSelector(interactive);
        const now = new Date();
        const cleanTime = [now.getHours(), now.getMinutes(), now.getSeconds()].map(x => String(x).padStart(2, '0')).join(':');
        const clickData = {
            zaman: cleanTime,
            locator: bestLocator(target, interactive, text, interactiveCss),
            etiket: interactive.tagName.toLowerCase(),
            metin: text,
            xpath_yedek: getXPath(interactive)
        };
        let recorded = false;
        try { if (typeof window.recordClick === 'function') { window.recordClick(clickData); recorded = true; } } catch (e) {}
        if (!recorded) { try { if (window.top && typeof window.top.recordClick === 'function') { window.top.recordClick(clickData); recorded = true; } } catch (e) {} }
        if (!recorded) { try { if (window.parent && typeof window.parent.recordClick === 'function') { window.parent.recordClick(clickData); recorded = true; } } catch (e) {} }
    }
    window.addEventListener('mousedown', (e) => { if (e.button !== 0) return; handleElementClick(e.target); }, true);
    setInterval(() => {
        const iframes = document.querySelectorAll('iframe');
        iframes.forEach(iframe => {
            try {
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (doc && !doc._hasClickLogger) {
                    doc._hasClickLogger = true;
                    doc.addEventListener('mousedown', (e) => { if (e.button !== 0) return; handleElementClick(e.target); }, true);
                }
            } catch (e) {}
        });
    }, 1000);
})();
"""

_UPLOAD_LOGGER_JS = r"""
(function() {
    function describeFormData(fd, url) {
        var alanlar = [];
        try {
            var it = fd.entries(); var step;
            while (!(step = it.next()).done) {
                var name = step.value[0]; var value = step.value[1];
                if (value && typeof value === 'object' && 'size' in value && 'name' in value) {
                    alanlar.push({ ad: name, tip: 'dosya', filename: value.name || '', boyut: value.size, contentType: value.type || '' });
                } else {
                    var v = String(value);
                    if (v.length > 2000) v = v.substring(0, 2000) + '…';
                    alanlar.push({ ad: name, tip: 'metin', deger: v });
                }
            }
        } catch (e) {}
        var now = new Date();
        var cleanTime = [now.getHours(), now.getMinutes(), now.getSeconds()].map(function(x){ return String(x).padStart(2,'0'); }).join(':');
        var data = { url: url || '', alanlar: alanlar, zaman: cleanTime };
        try { if (typeof window.recordUpload === 'function') { window.recordUpload(data); return; } } catch(e){}
        try { if (window.top && typeof window.top.recordUpload === 'function') { window.top.recordUpload(data); return; } } catch(e){}
        try { if (window.parent && typeof window.parent.recordUpload === 'function') { window.parent.recordUpload(data); return; } } catch(e){}
    }
    var origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) { try { this.__url = url; } catch(e){} return origOpen.apply(this, arguments); };
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {
        try { if (body instanceof FormData) { describeFormData(body, this.__url || ''); } } catch(e){}
        return origSend.apply(this, arguments);
    };
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
