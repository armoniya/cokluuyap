# -*- coding: utf-8 -*-
"""
UYAP Çalışma Paneli — web sunucusu (kabuk)
==========================================
Saf stdlib HTTP sunucusu (ek bağımlılık yok). Yalnızca:
  • statik kabuğu sunar (login.html / index.html / static/*)
  • giriş kapısı: oturum yoksa /login'e yönlendirir
  • POST /api/login → ORİJİNAL "Uyap Haricen Giriş/uyap_app.py" içindeki
    verify_credentials_thread çağrılır (doğrulama mantığı YENİDEN YAZILMAZ)

Çalıştırma:        python server.py            (varsayılan 127.0.0.1:8090 — yalnızca yerel)
Port değiştir:     python server.py 9000
İnternetten erişim: Varsayılan olarak yalnızca 127.0.0.1 dinlenir. Tüm ağa açmak için
                   UYAP_PANEL_LAN=1 (0.0.0.0); dışarı açım kimlik doğrulamalı ters-proxy +
                   HTTPS arkasından yapılmalı (HTTPS arkasında UYAP_PANEL_HTTPS=1 → Secure çerez).

Güvenlik: durum değiştiren tüm POST uçları same-origin/CSRF kontrolünden geçer (_csrf_ok).
"""

import os
import sys
import json
import time
import queue
import base64
import asyncio
import secrets
import argparse
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie

# Türkçe Windows konsolu (cp1254) bazı karakterleri (→, …) kodlayamaz; güvene al.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # PyInstaller paketi: statik kabuk (index/login/static) exe verilerinin içinde
    # "Panel/web" altında taşınır (bkz. ofis_ajani.spec datas).
    HERE = os.path.join(getattr(sys, "_MEIPASS", HERE), "Panel", "web")
STATIC_DIR = os.path.join(HERE, "static")
# Orijinal login kodunun bulunduğu klasör
UHG = os.path.normpath(os.path.join(HERE, "..", "..", "Uyap Haricen Giriş"))
# Modül çekirdekleri (GUI ile AYNI orijinal mantığı sarmalar): Panel/modules
MODULES_DIR = os.path.normpath(os.path.join(HERE, "..", "modules"))
for _p in (UHG, MODULES_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Uygulama Mağazası çekirdeği — masaüstüyle AYNI katalog + AYNI sahiplik dosyası
# (%LOCALAPPDATA%\UyapIcra\sahip_olunan.json). magaza_core sunum-bağımsızdır
# (tkinter/theme import etmez), bu yüzden headless sunucuda doğrudan kullanılır.
# Böylece bir özellik web'den ya da masaüstünden etkinleştirilse de ikisi de görür.
try:
    import magaza_core
except Exception as _e:   # pragma: no cover
    magaza_core = None
    print("[web] magaza_core yuklenemedi:", _e)

_auth = None          # uyap_app modülü (arka planda yüklenir)
_auth_err = None
# token -> {"user","pw"}. NOT: "pw" (UYAP parolası) office_agent paylaş/al için sunucu
# belleğinde tutulur — UYAP girişi parolayı gerektirir; istemciye ASLA gönderilmez ve
# /api/logout'ta silinir. Bellekten tamamen kaldırmak paylaşım anında parola sormayı
# (orijinal akış değişikliği) gerektirir; kalıcı saklama DPAPI ile şifrelidir (bkz. #9).
SESSIONS = {}


# Güvenlik raporu bulgu #9 — /api/login'de deneme sınırlaması yoktu. Panel varsayılan olarak
# yalnızca 127.0.0.1'e bağlanır (etkisi sınırlı) ama UYAP_PANEL_LAN=1 açıldığında bu uç,
# sınırsız deneme hakkı olan bir UYAP credential-stuffing proxy'sine dönüşebiliyordu. vendor_server.py
# (Uyap Haricen Giriş) içindeki _LoginGuard ile AYNI IP+üstel-geri-çekilme+tam-kilit mantığı,
# tek-süreç olduğu için bellek-içi sözlükle burada da uygulanır.
class _LoginGuard:
    """IP bazlı başarısızlık sayacı + üstel geri çekilme + tam kilit."""
    WINDOW = 900       # sn: başarısızlıkların sayıldığı kayan pencere (15 dk)
    SOFT = 5           # bu kadar başarısızlıktan SONRA gecikme (backoff) başlar
    HARD = 10          # bu kadar başarısızlıkta tam KİLİT
    LOCK_SECS = 900    # tam kilit süresi (15 dk)
    BACKOFF_MAX = 300  # tek backoff beklemesinin tavanı (5 dk)

    def __init__(self):
        self._fails = {}   # key -> [ts, ...] son başarısızlık zamanları
        self._until = {}   # key -> kilit bitiş ts'i

    def _recent(self, key, now):
        arr = [t for t in self._fails.get(key, ()) if now - t < self.WINDOW]
        if arr:
            self._fails[key] = arr
        else:
            self._fails.pop(key, None)
        return arr

    def blocked_for(self, key) -> int:
        """Şu an reddedilmeli mi? Beklenmesi gereken saniye (0 = izinli)."""
        now = time.time()
        until = self._until.get(key, 0)
        if until > now:
            return int(until - now) + 1
        if until:
            self._until.pop(key, None)
        arr = self._recent(key, now)
        n = len(arr)
        if n >= self.SOFT:
            delay = min(2 ** (n - self.SOFT + 1), self.BACKOFF_MAX)  # 2,4,8,… tavan 5 dk
            wait = arr[-1] + delay - now
            if wait > 0:
                return int(wait) + 1
        return 0

    def record_fail(self, key):
        now = time.time()
        arr = self._recent(key, now)
        arr.append(now)
        self._fails[key] = arr
        if len(arr) >= self.HARD:
            self._until[key] = now + self.LOCK_SECS

    def record_ok(self, key):
        self._fails.pop(key, None)
        self._until.pop(key, None)


LOGIN_GUARD = _LoginGuard()


def _cookie_attrs():
    """Oturum çerezi öznitelikleri. `Secure` yalnızca panel HTTPS arkasındaysa eklenir
    (UYAP_PANEL_HTTPS=1) — aksi halde düz-HTTP loopback'te çerez hiç gönderilmez ve giriş bozulur."""
    s = "; HttpOnly; SameSite=Lax"
    if (os.environ.get("UYAP_PANEL_HTTPS", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        s += "; Secure"
    return s

# Modül çekirdekleri (GUI ile birebir aynı): UDF dönüştürücü + SGK sorgu
_udf = None
_udf_err = None
_sgk = None
_sgk_err = None
SGK_BATCHES = {}      # token -> {"batch": SgkBatch, "dir": tempdir, "path": yüklenen}
# İcra Dosyalarım çekirdeği (GUI ile aynı icra_core mantığını sarmalar)
_icra = None
_icra_err = None
ICRA_JOBS = {}        # token -> IcraJob (oturum başına sorgu durumu)
# Çoklu Yargı Türü — Senkron Kapsamı çekirdeği (GUI ile aynı dosya_core mantığını
# sarmalar; bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7/§8).
_dosya = None
_dosya_err = None
# Masaüstü IcraDosyalarimPanel ile AYNI sabit 7 kolon (icra_core._kolonlar eşi).
ICRA_COLUMNS = ['alacakli', 'borclu', 'birimAdi', 'dosyaYil', 'dosyaNo', 'dosyaTur', 'acilisTarihi']

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}


def _load_auth():
    """Orijinal login modülünü (uyap_app) ve modül çekirdeklerini arka planda yükle."""
    global _auth, _auth_err, _udf, _udf_err, _sgk, _sgk_err, _icra, _icra_err, _dosya, _dosya_err
    try:
        import uyap_app
        _auth = uyap_app
        print("[web] giris modulu hazir (uyap_app)")
    except Exception as e:
        _auth_err = str(e)
        print("[web] giris modulu yuklenemedi:", e)
    # UDF + SGK çekirdekleri (GUI ile aynı orijinal mantığı sarmalar)
    try:
        import udf_core
        _udf = udf_core
        print("[web] UDF modulu hazir")
    except Exception as e:
        _udf_err = str(e)
        print("[web] UDF modulu yuklenemedi:", e)
    try:
        import sgk_core
        _sgk = sgk_core
        print("[web] SGK modulu hazir")
    except Exception as e:
        _sgk_err = str(e)
        print("[web] SGK modulu yuklenemedi:", e)
    try:
        import icra_core
        _icra = icra_core
        print("[web] Icra Dosyalarim modulu hazir")
    except Exception as e:
        _icra_err = str(e)
        print("[web] Icra Dosyalarim modulu yuklenemedi:", e)
    try:
        import dosya_core
        _dosya = dosya_core
        print("[web] Senkron Kapsami modulu hazir")
    except Exception as e:
        _dosya_err = str(e)
        print("[web] Senkron Kapsami modulu yuklenemedi:", e)
    if _dosya is not None:
        try:
            _dosya.senkron_zamanlayici_baslat(
                log_fn=lambda m: print("[senkron]", m))
        except Exception as e:
            print("[web] Senkron zamanlayici baslatilamadi:", e)
    # Kayıtlı kimlik + PIN varsa paylaşım/alım açılışta kendiliğinden başlar
    # (masaüstü uygulama ya da panel girişi GEREKMEZ — "kur-unut" ofis ajanı).
    boot_autoconnect()


def verify(username, password, office_code=""):
    """ORİJİNAL verify_credentials_thread'i çağırır → (ok, mesaj, role).
    office_code verilirse iki-alanlı login: vendor 'kullanici@ofis_kodu' olarak birleştirir
    (masaüstü uyap_app ve web sitesiyle aynı model).

    NOT: verify_credentials_thread kuyruğa (ok, mesaj, info) ÜÇLÜ demet koyar (masaüstü
    uyap_app.py satır 615 ile aynı sözleşme); info başarıda {role, office_code, office_label}
    taşır. Güvenlik raporu bulgu #8: role artık burada da çözülüp döndürülüyor — çağıran,
    yönetici-only işlemleri (ör. Logger modül üretimi) 'master' rolüyle sınırlayabilsin diye."""
    if _auth is None:
        return False, (_auth_err or "Giriş modülü henüz hazır değil, birkaç saniye sonra deneyin."), ""
    # Sunucu adresi env (UYAP_SERVER_URL) → config → derlenmiş varsayılan önceliğiyle;
    # masaüstüyle aynı load_config mantığını kullanır (dağıtımda dosya düzenlemeden geçersiz kılınır).
    try:
        url = _auth.load_config().get("server_url", _auth.DEFAULT_SERVER_URL)
    except Exception:
        url = _auth.DEFAULT_SERVER_URL
    q = queue.Queue()
    # verify_credentials_thread bloklar (içinde asyncio.run); sonucu kuyruğa koyar.
    _auth.verify_credentials_thread(url, username, password, q, office_code)
    try:
        result = q.get_nowait()
    except queue.Empty:
        return False, "Sunucudan yanıt alınamadı.", ""
    ok = bool(result[0])
    msg = result[1] if len(result) > 1 else ""
    info = result[2] if len(result) > 2 and isinstance(result[2], dict) else {}
    role = info.get("role", "") if ok else ""
    return ok, msg, role


def remember_config(username, password, remember, office_code=""):
    """Kullanıcı adı/parolayı orijinal config + DPAPI ile sakla (isteğe bağlı).
    office_code masaüstüyle AYNI anahtara (cfg['office_code']) yazılır — iki ön yüz
    aynı kaydı paylaşır, boot_autoconnect da buradan okur."""
    if _auth is None:
        return
    try:
        cfg = _auth.load_config()
        cfg["server_url"] = _auth.DEFAULT_SERVER_URL
        cfg["office_code"] = office_code
        cfg["username"] = username
        cfg["remember"] = bool(remember)
        cfg["password_enc"] = _auth.encrypt_secret(password) if remember else ""
        _auth.save_config(cfg)
    except Exception:
        pass


def remember_pin(pin, cert_id=""):
    """E-imza PIN'ini (ve varsa sertifika kimliğini) DPAPI ile ortak config'e yazar.
    Açılışta otomatik paylaşım (boot_autoconnect) ve girişte otomatik paylaşım
    (maybe_autoconnect) bu kayıtla, PIN'i yeniden sormadan çalışır."""
    if _auth is None:
        return
    try:
        cfg = _auth.load_config()
        cfg["pin_enc"] = _auth.encrypt_secret(pin)
        if cert_id:
            cfg["cert_id"] = cert_id
        _auth.save_config(cfg)
    except Exception:
        pass


class _LogStream:
    """office_agent/home_client stdout çıktısını yakalar (hem konsola hem LOG'a)."""
    def __init__(self, orig):
        self._orig = orig

    def write(self, s):
        if s:
            for ln in s.splitlines():
                if ln.strip():
                    ConnManager.LOG.append(ln)
            try:
                self._orig.write(s)
            except Exception:
                pass

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("no fileno")


class ConnManager:
    """UYAP Bağlantısı (Paylaş/Al) — GUI'deki sürücü kalıbının sunucu tarafı eşi.
    ORİJİNAL office_agent.run_office / home_client.amain fonksiyonlarını çağırır."""
    LOG = __import__("collections").deque(maxlen=600)
    sharing = False
    receiving = False
    _share_loop = _share_task = _recv_loop = _recv_task = None
    _capturing = False

    @classmethod
    def _capture(cls):
        if not cls._capturing:
            sys.stdout = _LogStream(sys.stdout)
            sys.stderr = _LogStream(sys.stderr)
            cls._capturing = True

    @classmethod
    def log(cls, text):
        for ln in str(text).splitlines():
            if ln.strip():
                cls.LOG.append(ln)

    @classmethod
    def start_share(cls, user, pw, pin, cert_id, office_code=""):
        if cls.sharing:
            return False, "Paylaşım zaten aktif."
        if _auth is None:
            return False, (_auth_err or "Giriş modülü hazır değil.")
        if not pin:
            return False, "E-imza PIN kodu gerekli."
        cls._capture()
        port = getattr(_auth, "LOCAL_PORT", 8800)
        _auth.free_port_if_busy(port, cls.log)
        cls.log("[SİSTEM] UYAP oturumu kuruluyor; LAN + dış ağ paylaşımı başlatılıyor...")
        cls.sharing = True
        cls._share_loop = asyncio.new_event_loop()
        args = argparse.Namespace(
            signaling=_auth.DEFAULT_SERVER_URL, room=user, office_code=office_code,
            password=pw, pin=pin, cert_id=cert_id or None, no_verify=False, interval=5,
            proxy_port=port, host="0.0.0.0", user=user,
        )

        def run():
            asyncio.set_event_loop(cls._share_loop)
            cls._share_task = cls._share_loop.create_task(_auth.office_agent.run_office(args))
            try:
                cls._share_loop.run_until_complete(cls._share_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                cls.log(f"[HATA] Paylaşım durdu: {e}")
            finally:
                cls.log("[SİSTEM] Paylaşım sonlandı.")
                cls.sharing = False
                try:
                    cls._share_loop.close()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()
        return True, "ok"

    @classmethod
    def stop_share(cls):
        cls.sharing = False
        if cls._share_loop and not cls._share_loop.is_closed() and cls._share_task:
            try:
                cls._share_loop.call_soon_threadsafe(cls._share_task.cancel)
            except Exception:
                pass
        cls.log("[SİSTEM] Paylaşım durduruldu.")
        return True, "ok"

    @classmethod
    def start_receive(cls, user, pw, office_code=""):
        if cls.receiving:
            return False, "Bağlantı zaten aktif."
        if _auth is None:
            return False, (_auth_err or "Giriş modülü hazır değil.")
        cls._capture()
        port = getattr(_auth, "LOCAL_PORT", 8800)
        _auth.free_port_if_busy(port, cls.log)
        cls.log("[SİSTEM] İstemci başlatılıyor (önce yerel ağ, olmazsa dış ağ)...")
        cls.receiving = True
        cls._recv_loop = asyncio.new_event_loop()
        args = argparse.Namespace(
            signaling=_auth.DEFAULT_SERVER_URL, room=user, office_code=office_code,
            password=pw, host="127.0.0.1", port=port,
        )

        def run():
            asyncio.set_event_loop(cls._recv_loop)
            cls._recv_task = cls._recv_loop.create_task(_auth.home_client.amain(args))
            try:
                cls._recv_loop.run_until_complete(cls._recv_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                cls.log(f"[HATA] İstemci durdu: {e}")
            finally:
                cls.log("[SİSTEM] İstemci sonlandı.")
                cls.receiving = False
                try:
                    cls._recv_loop.close()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()
        return True, "ok"

    @classmethod
    def stop_receive(cls):
        cls.receiving = False
        if cls._recv_loop and not cls._recv_loop.is_closed() and cls._recv_task:
            try:
                cls._recv_loop.call_soon_threadsafe(cls._recv_task.cancel)
            except Exception:
                pass
        cls.log("[SİSTEM] Bağlantı kesildi.")
        return True, "ok"

    @classmethod
    def status(cls, since=0):
        logs = list(cls.LOG)
        return {
            "sharing": cls.sharing, "receiving": cls.receiving,
            "n": len(logs), "logs": logs[since:] if since < len(logs) else [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# UDF Dönüştürücü — orijinal converter/signer/verify (udf_core ile, GUI ile aynı)
# ─────────────────────────────────────────────────────────────────────────────
def udf_dlls(deep=False):
    if _udf is None:
        return []
    try:
        return _udf.find_dlls_deep() if deep else _udf.find_common_dlls()
    except Exception:
        return []


def udf_process(data):
    """Yüklenen belgeyi UDF'ye dönüştürür (gerekirse e-imzalar). Döner: JSON sözlüğü."""
    if _udf is None:
        return {"ok": False, "logs": [_udf_err or "UDF modülü hazır değil."]}
    filename = (data.get("filename") or "belge").strip()
    mode = data.get("mode") or "convert"
    try:
        raw = base64.b64decode(data.get("data_b64") or "")
    except Exception:
        return {"ok": False, "logs": ["Dosya verisi çözülemedi."]}
    if not raw:
        return {"ok": False, "logs": ["Boş dosya."]}
    ext = os.path.splitext(filename)[1].lower() or ".docx"
    base = os.path.splitext(os.path.basename(filename))[0] or "belge"
    out_name = base + ".udf"
    tmpdir = tempfile.mkdtemp(prefix="uyap_udf_")
    in_path = os.path.join(tmpdir, "girdi" + ext)
    out_path = os.path.join(tmpdir, out_name)
    try:
        with open(in_path, "wb") as f:
            f.write(raw)
        if mode == "sign":
            dll = (data.get("dll") or "").strip()
            pin = (data.get("pin") or "").strip()
            if not dll or not pin:
                return {"ok": False, "logs": ["İmzalama için DLL yolu ve PIN gerekli."]}
            success, vlogs = _udf.convert_and_sign(in_path, out_path, dll, pin)
            logs = (["İmzalı UDF üretildi."] if success
                    else ["UDF üretildi fakat doğrulama uyarısı var."]) + list(vlogs)
        else:
            _udf.convert_only(in_path, out_path)
            logs = ["İmzasız UDF üretildi."]
        with open(out_path, "rb") as f:
            out_b64 = base64.b64encode(f.read()).decode("ascii")
        return {"ok": True, "logs": logs, "filename": out_name, "data_b64": out_b64}
    except Exception as e:
        return {"ok": False, "logs": [f"Hata: {e}"]}
    finally:
        for p in (in_path, out_path):
            try:
                os.remove(p)
            except Exception:
                pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass


def udf_sign_existing(data):
    """Yüklenen hazır UDF'i OLDUĞU GİBİ (dönüştürmeden) e-imzalar. Döner: JSON sözlüğü."""
    if _udf is None:
        return {"ok": False, "logs": [_udf_err or "UDF modülü hazır değil."]}
    filename = (data.get("filename") or "belge.udf").strip()
    if not filename.lower().endswith(".udf"):
        return {"ok": False, "logs": ["Yalnızca .udf dosyası imzalanabilir."]}
    dll = (data.get("dll") or "").strip()
    pin = (data.get("pin") or "").strip()
    if not dll or not pin:
        return {"ok": False, "logs": ["İmzalama için DLL yolu ve PIN gerekli."]}
    try:
        raw = base64.b64decode(data.get("data_b64") or "")
    except Exception:
        return {"ok": False, "logs": ["Dosya verisi çözülemedi."]}
    if not raw:
        return {"ok": False, "logs": ["Boş dosya."]}
    base = os.path.splitext(os.path.basename(filename))[0] or "belge"
    out_name = base + "_imzali.udf"
    tmpdir = tempfile.mkdtemp(prefix="uyap_udf_")
    in_path = os.path.join(tmpdir, "girdi.udf")
    out_path = os.path.join(tmpdir, out_name)
    try:
        with open(in_path, "wb") as f:
            f.write(raw)
        success, vlogs = _udf.sign_existing(in_path, out_path, dll, pin)
        logs = (["İmzalı UDF üretildi."] if success
                else ["UDF imzalandı fakat doğrulama uyarısı var."]) + list(vlogs)
        with open(out_path, "rb") as f:
            out_b64 = base64.b64encode(f.read()).decode("ascii")
        return {"ok": True, "logs": logs, "filename": out_name, "data_b64": out_b64}
    except Exception as e:
        return {"ok": False, "logs": [f"Hata: {e}"]}
    finally:
        for p in (in_path, out_path):
            try:
                os.remove(p)
            except Exception:
                pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Uygulama Mağazası — masaüstüyle AYNI çekirdek (magaza_core). Katalog + sahiplik
# tek doğruluk kaynağıdır; web yalnızca onu sunum için JSON'a çevirir.
# ─────────────────────────────────────────────────────────────────────────────
def store_catalog():
    """Katalog + sahip olunan app kimlikleri. Sol menü ve mağaza kartları bundan
    kurulur. accent = renk İSMİ ("sage"/"clay"/"blue"/"gold") → CSS değişkeni."""
    if magaza_core is None:
        return {"catalog": [], "owned": []}
    try:
        owned = sorted(magaza_core.sahip_olunanlar())
        cat = []
        for app in magaza_core.katalog():
            cat.append({
                "kimlik": app.get("kimlik"),
                "ad": app.get("ad"),
                "aciklama": app.get("aciklama", ""),
                "fiyat": app.get("fiyat", ""),
                "menu_yolu": app.get("menu_yolu", []),
                "yapraklar": app.get("yapraklar", []),
                "uretilmis": bool(app.get("uretilmis")),
            })
        return {"catalog": cat, "owned": owned}
    except Exception as e:
        return {"catalog": [], "owned": [], "err": str(e)}


def store_toggle(data):
    """Bir app'i etkinleştirir/kaldırır (yerel sahiplik deposunu yazar). Masaüstü
    bir sonraki menü tazelemesinde aynı değişikliği görür (paylaşılan JSON)."""
    if magaza_core is None:
        return {"ok": False, "msg": "Mağaza çekirdeği hazır değil."}
    kimlik = (data.get("kimlik") or "").strip()
    if not kimlik:
        return {"ok": False, "msg": "Geçersiz uygulama kimliği."}
    enable = bool(data.get("enable"))
    try:
        if enable:
            magaza_core.sahip_ekle(kimlik)
        else:
            magaza_core.sahip_cikar(kimlik)
        return {"ok": True, "owned": sorted(magaza_core.sahip_olunanlar())}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Ayarlar — masaüstüyle AYNI tercih deposu (uyap_app.load_config/save_config;
# DPAPI'li ortak uyap_app_config.json). Web yeni bir depo KURMAZ; aynı dosyayı
# paylaşır → masaüstünde değişen ayar web'de de görünür (ve tersi).
# Şimdilik tek ayar: açılışta otomatik UYAP bağlantısı.
#   auto_connect = "off" | "share" | "receive"   (masaüstü modules/ayarlar.py eşi)
# ─────────────────────────────────────────────────────────────────────────────
AUTO_MODES = ("off", "share", "receive")
AUTO_DEFAULT = "off"


def get_settings():
    """Mevcut tercihler. Giriş modülü hazır değilse varsayılanlar döner."""
    mode = AUTO_DEFAULT
    if _auth is not None:
        try:
            cfg = _auth.load_config()
            v = cfg.get("auto_connect", AUTO_DEFAULT)
            if v in AUTO_MODES:
                mode = v
        except Exception:
            pass
    return {"auto_connect": mode}


def set_settings(data):
    """auto_connect tercihini ortak config'e yazar (DPAPI altyapısı korunur)."""
    if _auth is None:
        return {"ok": False, "msg": (_auth_err or "Giriş modülü henüz hazır değil.")}
    mode = (data.get("auto_connect") or "").strip()
    if mode not in AUTO_MODES:
        return {"ok": False, "msg": "Geçersiz otomatik bağlantı kipi."}
    try:
        cfg = _auth.load_config()
        cfg["auto_connect"] = mode
        _auth.save_config(cfg)
        return {"ok": True, "auto_connect": mode}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def maybe_autoconnect(user, pw, office_code=""):
    """Giriş sonrası, ayar 'share'/'receive' ise UYAP bağlantısını otomatik başlatır
    (masaüstü panel._maybe_autoconnect eşi). Bağlantı mantığı yine ConnManager
    (orijinal office_agent/home_client) üzerinden çağrılır; yeni mantık yoktur.
    'share' için kayıtlı (DPAPI) E-imza PIN'i gerekir — yoksa atlanır ve loglanır."""
    if _auth is None:
        return
    try:
        cfg = _auth.load_config()
    except Exception:
        return
    mode = cfg.get("auto_connect", AUTO_DEFAULT)
    if mode not in ("share", "receive"):
        return
    try:
        if mode == "receive" and not ConnManager.receiving:
            ConnManager.log("[SİSTEM] Otomatik bağlantı: ofise bağlanılıyor…")
            ConnManager.start_receive(user, pw, office_code)
        elif mode == "share" and not ConnManager.sharing:
            pin = ""
            try:
                if cfg.get("pin_enc"):
                    pin = _auth.decrypt_secret(cfg["pin_enc"]) or ""
            except Exception:
                pin = ""
            if not pin:
                ConnManager.log("[SİSTEM] Otomatik paylaşım atlandı: kayıtlı E-imza PIN'i yok "
                                "(UYAP Bağlantısı ekranında PIN'i bir kez kaydedin).")
                return
            ConnManager.log("[SİSTEM] Otomatik bağlantı: paylaşım başlatılıyor…")
            ConnManager.start_share(user, pw, pin, (cfg.get("cert_id") or "").strip(), office_code)
    except Exception as e:
        ConnManager.log(f"[HATA] Otomatik bağlantı başlatılamadı: {e}")


def boot_autoconnect():
    """Sunucu AÇILIŞINDA otomatik bağlantı — panel girişi bile gerekmez (kur-unut ajan).
    Koşullar: auto_connect ayarı 'share'/'receive', girişte 'Beni hatırla' ile kaydedilmiş
    parola (DPAPI); 'share' ayrıca kayıtlı E-imza PIN'i ister (maybe_autoconnect denetler).
    Böylece bilgisayar açılıp server.py çalıştığında ofis paylaşımı kendiliğinden kurulur;
    masaüstü uygulamaya ya da panele girmeye gerek kalmaz."""
    if _auth is None:
        return
    try:
        cfg = _auth.load_config()
        if cfg.get("auto_connect", AUTO_DEFAULT) not in ("share", "receive"):
            return
        user = (cfg.get("username") or "").strip()
        pw = ""
        try:
            if cfg.get("password_enc"):
                pw = _auth.decrypt_secret(cfg["password_enc"]) or ""
        except Exception:
            pw = ""
        if not user or not pw:
            ConnManager.log("[SİSTEM] Açılışta otomatik bağlantı atlandı: kayıtlı kullanıcı/parola "
                            "yok (panel girişinde 'Beni hatırla' işaretleyin).")
            return
        ConnManager.log("[SİSTEM] Açılışta otomatik bağlantı deneniyor…")
        maybe_autoconnect(user, pw, (cfg.get("office_code") or "").strip())
    except Exception as e:
        ConnManager.log(f"[HATA] Açılışta otomatik bağlantı: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SGK Sorgu — orijinal SorguMotoru + özetleyiciler (sgk_core ile, GUI ile aynı)
# Her oturum (token) kendi SgkBatch'ine sahiptir.
# ─────────────────────────────────────────────────────────────────────────────
def sgk_upload(token, data):
    if _sgk is None:
        return {"ok": False, "msg": _sgk_err or "SGK modülü hazır değil."}
    cur = SGK_BATCHES.get(token)
    if cur and cur["batch"].running:
        return {"ok": False, "msg": "Önce çalışan sorguyu durdurun."}
    filename = (data.get("filename") or "liste.xlsx").strip()
    try:
        raw = base64.b64decode(data.get("data_b64") or "")
    except Exception:
        return {"ok": False, "msg": "Dosya verisi çözülemedi."}
    if not raw:
        return {"ok": False, "msg": "Boş dosya."}
    safe = os.path.basename(filename) or "liste.xlsx"
    if not safe.lower().endswith((".xlsx", ".xlsm")):
        safe += ".xlsx"
    tmpdir = tempfile.mkdtemp(prefix="uyap_sgk_")
    path = os.path.join(tmpdir, safe)
    try:
        with open(path, "wb") as f:
            f.write(raw)
        batch = _sgk.SgkBatch()
        n = batch.load(path)
    except Exception as e:
        return {"ok": False, "msg": f"Excel açılamadı: {e}"}
    SGK_BATCHES[token] = {"batch": batch, "dir": tmpdir, "path": path}
    return {"ok": True, "n": n, "columns": batch.kolonlar,
            "queries": [{"key": k, "label": l} for k, l in _sgk.SORGU_BASLIKLARI]}


def sgk_batch(token):
    cur = SGK_BATCHES.get(token)
    return cur["batch"] if cur else None


# ─────────────────────────────────────────────────────────────────────────────
# İcra Dosyalarım — masaüstü IcraDosyalarimPanel'in sunucu-tarafı eşi. Sorgu/iş
# mantığı icra_core'dadır (orijinal SorguMotoru._post → 127.0.0.1:8800 ofis →
# canlı e-imza UYAP). Burada YALNIZCA sunum köprüsü vardır: oturum başına bir iş
# parçacığı + durum; UYAP'a giden teknik AYNEN korunur. Her oturum (token) kendi
# IcraJob'una sahiptir (SGK_BATCHES kalıbı).
# ─────────────────────────────────────────────────────────────────────────────
def icra_fields():
    """Kolon başlıkları + durum (Açık/Kapalı) seçenekleri. Web arayüzü tabloyu
    bundan kurar. icra_core.FIELD_BY_KEY / DURUMLAR tek doğruluk kaynağıdır."""
    if _icra is None:
        return {"ready": False, "err": _icra_err}
    return {
        "ready": True,
        "columns": [{"key": c, "label": _icra.FIELD_BY_KEY[c]["label"]} for c in ICRA_COLUMNS],
        "durumlar": [{"label": l, "kod": k} for l, k in _icra.DURUMLAR],
        "durum_default": _icra.DURUM_VARSAYILAN,
    }


def icra_units():
    """İcra daireleri açılır listesi (masaüstü _birim_listesi_yukle_bg eşi):
    önce UYAP'tan (ofis açıksa), olmazsa yerel DB'den. Ağ yoksa eldeki önbellek."""
    if _icra is None:
        return []
    try:
        _icra.birim_listesi_getir(lambda *a, **k: None)
    except Exception:
        pass
    if not _icra.birim_adlari():
        try:
            _icra.birimleri_db_den_yukle()
        except Exception:
            pass
    try:
        return sorted(set(_icra.birim_adlari()), key=lambda s: s.lower())
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Senkron Kapsamı — masaüstü Panel/modules/senkron_kapsami.py ile AYNI dosya_core
# çekirdeğini ve AYNI SenkronKapsami/YargiBirimi tablolarını paylaşır (bkz.
# docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7/§8). Ağa gitmeden DB önbelleğini
# okur; canlı yenileme AYRI bir uç noktadan tetiklenir (8 yargı türünün tümünü
# her GET'te canlı çekmek sayfa açılışını yavaşlatır).
# ─────────────────────────────────────────────────────────────────────────────
def senkron_kapsami_durumu():
    """Ayar ekranı için tam anlık görüntü: yargı türleri sabit listesi + DB'deki
    Yargı Birimi önbelleği (yargi_turu -> liste) + mevcut SenkronKapsami seçimi."""
    if _dosya is None:
        return {"ready": False, "err": _dosya_err}
    turler = [{"kod": k, "ad": a} for k, a in _dosya.YARGI_TURLERI]
    birimler = {}
    for kod, _ad in _dosya.YARGI_TURLERI:
        try:
            birimler[str(kod)] = _dosya.yargi_birimleri_db_den_yukle(kod)
        except Exception:
            birimler[str(kod)] = []
    try:
        secili = _dosya.senkron_kapsami_durumu_getir()
    except Exception:
        secili = []
    return {"ready": True, "turler": turler, "birimler": birimler, "secili": secili}


def senkron_kapsami_yenile(yargi_turu):
    """Bir yargı türünün Yargı Birimi listesini canlı UYAP'tan çeker (DB'ye
    upsert eder) — masaüstü '↻ Listeyi UYAP'tan Getir/Yenile' eşi."""
    if _dosya is None:
        return {"ok": False, "msg": _dosya_err or "Senkron Kapsamı modülü hazır değil."}
    try:
        birimler = _dosya.yargi_birimleri_getir(yargi_turu, lambda *a, **k: None)
        return {"ok": True, "birimler": birimler}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def senkron_kapsami_kaydet_web(secimler):
    """Ayar ekranından gelen [[yargi_turu, yargi_birimi_kod], ...] listesini
    kaydeder (dosya_core.senkron_kapsami_kaydet — replace-all semantik: listede
    olmayan var olan kayıtlar silinmez, yalnız aktif=False yapılır)."""
    if _dosya is None:
        return {"ok": False, "msg": _dosya_err or "Senkron Kapsamı modülü hazır değil."}
    try:
        temiz = [(int(t), str(k or "")) for t, k in (secimler or [])]
        _dosya.senkron_kapsami_kaydet(temiz)
        return {"ok": True, "kayit": len(temiz)}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Dosyalarım (Tümü) — masaüstü Panel/modules/dosyalarim_genel.py ile AYNI
# dosya_core çekirdeğini paylaşır (bkz. plan §7 Faz 6). İcra Dosyalarım'ın
# aksine CANLI UYAP sorgusu yapmaz (yalnız DB okur); tazelemek isteyen
# kullanıcı "Yenile"yi tetikler (arka planda DosyaSorgu.calistir), job-tabanlı
# ilerleme icra_search/IcraJob deseniyle AYNI.
# ─────────────────────────────────────────────────────────────────────────────
DOSYALARIM_JOBS = {}   # token -> {"running", "logs", "sonuc"}


def dosyalarim_fields():
    if _dosya is None:
        return {"ready": False, "err": _dosya_err}
    return {
        "ready": True,
        "yargi_turleri": [{"kod": k, "ad": a} for k, a in _dosya.YARGI_TURLERI],
        "dosya_turleri": [{"kod": k, "ad": a} for k, a in _dosya.dosya_tur_secenekleri()],
        "durumlar": [{"kod": k, "ad": a} for k, a in _dosya.dosya_durum_secenekleri()],
    }


def dosyalarim_birimler(yargi_turu):
    if _dosya is None:
        return {"birimler": []}
    try:
        return {"birimler": _dosya.yargi_birimleri_db_den_yukle(int(yargi_turu))}
    except Exception:
        return {"birimler": []}


def dosyalarim_list(data):
    if _dosya is None:
        return {"ok": False, "msg": _dosya_err or "Senkron Kapsamı modülü hazır değil.", "kayitlar": []}
    filtreler = {k: (data.get(k) or None) for k in (
        "yargi_turu", "yargi_birimi_kod", "tur_kod", "durum_kod",
        "tarih_baslangic", "tarih_bitis")}
    try:
        return {"ok": True, "kayitlar": _dosya.dosyalarim_db_listele(filtreler)}
    except Exception as e:
        return {"ok": False, "msg": str(e), "kayitlar": []}


def dosyalarim_detay(data):
    if _dosya is None:
        return {"ok": False, "msg": _dosya_err or "Senkron Kapsamı modülü hazır değil."}
    rec = {
        "dosyaId": data.get("dosyaId", ""), "birimId": data.get("birimId", ""),
        "dosyaNo": data.get("dosyaNo", ""), "dosyaTurKod": data.get("dosyaTurKod", 0),
    }
    ham, aile, kaydedildi, hata, taraflar = _dosya.dosya_detay_goster_ve_kaydet(rec, lambda m: None)
    if hata:
        return {"ok": False, "msg": hata}
    return {"ok": True, "aile": aile, "kaydedildi": kaydedildi, "ham": ham, "taraflar": taraflar}


def dosyalarim_yenile_baslat(token):
    if _dosya is None:
        return {"ok": False, "msg": _dosya_err or "Senkron Kapsamı modülü hazır değil."}
    onceki = DOSYALARIM_JOBS.get(token)
    if onceki and onceki["running"]:
        return {"ok": False, "msg": "Önceki yenileme sürüyor."}
    job = DOSYALARIM_JOBS[token] = {"running": True, "logs": [], "sonuc": None}

    def _log(m):
        job["logs"].append(str(m))

    def _run():
        try:
            toplam, sonuclar = _dosya.dosyalarim_yenile(_log)
            job["sonuc"] = {"toplam": toplam, "sonuclar": sonuclar}
        except Exception as e:
            job["sonuc"] = {"hata": str(e)}
        finally:
            job["running"] = False
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


def dosyalarim_yenile_durum(token, since_log):
    job = DOSYALARIM_JOBS.get(token)
    if job is None:
        return {"loaded": False}
    return {
        "loaded": True, "running": job["running"],
        "logs": job["logs"][since_log:], "log_n": len(job["logs"]),
        "sonuc": job["sonuc"] if not job["running"] else None,
    }


def _icra_birlestir(db_kayitlar, canli):
    """DB + canlı kayıtları birim+dosyaNo ile birleştirir (masaüstü _birlestir eşi);
    canlı taze olduğu için onu tercih eder, canlıda olmayan DB dosyalarını korur."""
    def anahtar(r):
        return (str(r.get("birimAdi", "")), str(r.get("dosyaNo", "")))
    harita = {anahtar(r): r for r in (db_kayitlar or [])}
    for r in (canli or []):
        harita[anahtar(r)] = r
    return list(harita.values())


class IcraJob:
    """Oturum başına İcra Dosyalarım sorgusu. Masaüstü panelin _ara_bg akışını
    bire bir izler: ÖNCE DB'den göster (ofis kapalı olsa bile veri görünür), SONRA
    canlı UYAP ile birleştir. Hiçbir yeni iş mantığı yok; icra_core çağrılır."""

    def __init__(self):
        self.lock = threading.Lock()
        self.logs = []
        self.rows = []        # [[kolon değerleri...], ...] (sunum için serileştirilmiş)
        self.records = []     # AYNI sırada ham UYAP kayıtları (dosyaId dahil) — "Dosya Görüntüle"
        self.rev = 0          # rows her değiştiğinde artar (istemci tazeler)
        self.running = False
        self.status = ""
        self.yeni = []        # DB'ye yeni eklenen dosyalar (bir kez bildirilir)
        self.error = ""

    def log(self, m):
        with self.lock:
            for ln in str(m).splitlines():
                if ln.strip():
                    self.logs.append(ln)

    def _set_rows(self, records):
        with self.lock:
            self.rows = [[_icra.kolon_degeri(r, c) for c in ICRA_COLUMNS] for r in records]
            self.records = list(records)
            self.rev += 1

    def snapshot(self, since_log, since_rev):
        with self.lock:
            out = {
                "loaded": True, "running": self.running, "status": self.status,
                "log_n": len(self.logs), "rev": self.rev, "columns": ICRA_COLUMNS,
                "logs": self.logs[since_log:] if since_log < len(self.logs) else [],
            }
            if since_rev != self.rev:
                out["rows"] = self.rows
            if self.yeni:
                out["yeni"] = self.yeni
                self.yeni = []      # yalnız bir kez bildir
            if self.error:
                out["error"] = self.error
            return out

    def start(self, values, durum_kod):
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.status = "Sorgulanıyor…"
            self.error = ""
        threading.Thread(target=self._run, args=(values, durum_kod), daemon=True).start()
        return True

    def _run(self, values, durum_kod):
        try:
            # 1) ÖNCE DB'den göster (UYAP beklemeden) — ofis kapalı olsa bile veri görünür.
            try:
                db_kayitlar = _icra.db_dosyalari_getir(values, durum_kod)
            except Exception:
                db_kayitlar = []
            if db_kayitlar:
                self._set_rows(db_kayitlar)
                with self.lock:
                    self.status = "DB'den %d · canlı sorgu sürüyor…" % len(db_kayitlar)
            # 2) SONRA canlı UYAP: yeni/eksik dosyaları ekle (birim+yıl+sıra ile birleştir).
            engine = _icra.IcraSorgu(self.log)
            try:
                kayitlar, _payload, yeni = engine.ara(values, durum_kod)
                birlesik = _icra_birlestir(db_kayitlar, kayitlar)
                self._set_rows(birlesik)
                with self.lock:
                    self.status = "%d sonuç" % len(birlesik)
                    self.yeni = [{"dosya_no": y.get("dosya_no", ""),
                                  "birim_adi": y.get("birim_adi", "")} for y in (yeni or [])]
            except _icra.OturumHatasi as e:
                with self.lock:
                    self.status = "Oturum/bağlantı hatası"
                    self.error = str(e)
                self.log("⛔ Oturum/yetki hatası: " + str(e))
                self.log("   UYAP Bağlantısı modülünden Paylaş/Al ile bağlantıyı başlatın.")
            except Exception as e:
                # Canlı sorgu başarısız (ör. ofis 8800 kapalı): DB sonuçları ekranda kalsın.
                if db_kayitlar:
                    with self.lock:
                        self.status = "DB'den %d (ofis kapalı/erişilemedi)" % len(db_kayitlar)
                    self.log("ℹ️ Canlı UYAP yapılamadı; yalnız veritabanı gösteriliyor: " + str(e))
                else:
                    with self.lock:
                        self.status = "Hata"
                        self.error = str(e)
                    self.log("⚠️ Sorgu hatası: " + str(e))
                    self.log("   Yerel ofis (127.0.0.1:8800) açık ve UYAP Bağlantısı aktif mi?")
        finally:
            with self.lock:
                self.running = False


def icra_job(token, create=False):
    job = ICRA_JOBS.get(token)
    if job is None and create:
        job = ICRA_JOBS[token] = IcraJob()
    return job


def icra_search(token, data):
    """Sorguyu başlatır (masaüstü 'Sorgula' eşi). values = kolon kutuları + Taraf
    ile Ara alanları + tarih aralığı; durum_kod = Açık/Kapalı."""
    if _icra is None:
        return {"ok": False, "msg": _icra_err or "İcra modülü hazır değil."}
    values = {}
    for f in _icra.FIELDS:
        values[f["key"]] = (data.get(f["key"]) or "")
    for k in ("taraf_ad", "taraf_soyad", "taraf_tckn",
              "taraf_kurum", "taraf_vergi", "taraf_mersis", "taraf_tur"):
        values[k] = (data.get(k) or "")
    try:
        durum_kod = int(data.get("durum_kod", _icra.DURUM_VARSAYILAN))
    except (TypeError, ValueError):
        durum_kod = _icra.DURUM_VARSAYILAN
    job = icra_job(token, create=True)
    if not job.start(values, durum_kod):
        return {"ok": False, "msg": "Önceki sorgu sürüyor."}
    return {"ok": True}


def icra_detay(token, data):
    """"Dosya Görüntüle" (masaüstü IcraDosyalarimPanel._dosya_goruntule eşi):
    `job.records[index]`'teki (aynı sorgudan TAZE 'dosyaId' içeren) ham kaydı
    `dosya_core.dosya_detay_goster_ve_kaydet`'e verir. `index`, istemcinin
    (icra.js) tıkladığı satırın HAM (filtrelenmemiş) sırasıdır."""
    job = ICRA_JOBS.get(token)
    if job is None:
        return {"ok": False, "msg": "Önce bir sorgu çalıştırın."}
    try:
        idx = int(data.get("index"))
        with job.lock:
            rec = job.records[idx]
    except (TypeError, ValueError, IndexError):
        return {"ok": False, "msg": "Seçili satır bulunamadı; listeyi yenileyin."}
    if _dosya is None:
        return {"ok": False, "msg": _dosya_err or "Senkron Kapsamı modülü hazır değil."}
    ham, aile, kaydedildi, hata, taraflar = _dosya.dosya_detay_goster_ve_kaydet(rec, job.log)
    if hata:
        return {"ok": False, "msg": hata}
    return {"ok": True, "aile": aile, "kaydedildi": kaydedildi, "ham": ham, "taraflar": taraflar}


# ─────────────────────────────────────────────────────────────────────────────
# MTS Takip Açma — masaüstü MtsTakipPanel'in sunucu-tarafı eşi. İŞ MANTIĞI yine
# orijinaldedir: kaynak (XML/Excel) ayrıştırma uyap_core.mts.parse ile, açma işi
# is_kuyrugu üzerinden ofise (127.0.0.1:8800 iş kuyruğu) gönderilir; ofis
# prepare→(onay)→finalize'ı CANLI oturumla yürütür. Burada YALNIZCA sunum köprüsü
# + oturum başına durum (ayrıştırılan takipler, vekalet/dayanak dosyaları, job_id).
# Tarayıcı/Playwright YOK; yeni iş mantığı YOK. ([[gorsel-birlestirme-kurali]])
# ─────────────────────────────────────────────────────────────────────────────
MTS_SESSIONS = {}     # token -> MtsSession


class MtsSession:
    def __init__(self):
        self.takipler = []     # uyap_core.mts.models.Takip listesi
        self.vekalet = {}      # alacakli -> {"filename","b64"}
        self.dayanak = {}      # dosya_no -> {"filename","b64"}
        self.job_id = None


def mts_session(token, create=False):
    s = MTS_SESSIONS.get(token)
    if s is None and create:
        s = MTS_SESSIONS[token] = MtsSession()
    return s


def mts_conn():
    """Ofis (iş kuyruğu) erişilebilir mi? Masaüstü _baglanti_kontrol eşi (UYAP'a gitmez)."""
    try:
        import is_kuyrugu
        ok, msg = is_kuyrugu.ofis_erisilebilir()
        return {"ok": ok, "msg": msg}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def mts_parse(token, data):
    """Yüklenen XML/Excel'i istemci-tarafı (sunucu-tarafı) uyap_core.mts.parse ile
    ayrıştırır; takipleri oturuma alır ve serileştirip döndürür. Masaüstü
    _kaynak_sec → kaynak_to_takipler eşi."""
    filename = (data.get("filename") or "kaynak").strip()
    try:
        raw = base64.b64decode(data.get("data_b64") or "")
    except Exception:
        return {"ok": False, "msg": "Dosya verisi çözülemedi."}
    if not raw:
        return {"ok": False, "msg": "Boş dosya."}
    ext = os.path.splitext(filename)[1].lower() or ".xml"
    tmpdir = tempfile.mkdtemp(prefix="uyap_mts_")
    path = os.path.join(tmpdir, "kaynak" + ext)
    try:
        with open(path, "wb") as f:
            f.write(raw)
        from uyap_core.mts import parse as mts_parse_mod
        takipler = mts_parse_mod.kaynak_to_takipler(path)
    except Exception as e:
        return {"ok": False, "msg": "Ayrıştırma hatası: %s" % e}
    finally:
        for p in (path,):
            try:
                os.remove(p)
            except Exception:
                pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass
    if not takipler:
        return {"ok": False, "msg": "Dosyada açılacak takip bulunamadı."}
    s = mts_session(token, create=True)
    s.takipler = takipler
    s.vekalet = {}
    s.dayanak = {}
    s.job_id = None
    from uyap_core.mts import models as mts_models
    ser = mts_models.takipler_to_params(takipler)
    alacaklilar = []
    for t in takipler:
        if t.alacakli and t.alacakli not in alacaklilar:
            alacaklilar.append(t.alacakli)
    return {"ok": True, "takipler": ser, "alacaklilar": alacaklilar,
            "filename": os.path.basename(filename)}


def mts_clear(token):
    s = mts_session(token)
    if s:
        s.takipler = []
        s.vekalet = {}
        s.dayanak = {}
        s.job_id = None
    return {"ok": True}


def mts_vekalet(token, data):
    """Bir alacaklıya vekalet dosyası bağlar (masaüstü _vekalet_ata eşi)."""
    s = mts_session(token)
    if not s or not s.takipler:
        return {"ok": False, "msg": "Önce bir XML/Excel seçin."}
    alacakli = (data.get("alacakli") or "").strip()
    if not alacakli:
        return {"ok": False, "msg": "Alacaklı belirtilmedi."}
    b64 = data.get("data_b64") or ""
    if not b64:
        return {"ok": False, "msg": "Boş dosya."}
    filename = os.path.basename((data.get("filename") or "vekalet").strip())
    s.vekalet[alacakli] = {"filename": filename, "b64": b64}
    return {"ok": True, "alacakli": alacakli, "filename": filename}


def _mts_pdf_metin(path):
    """PDF metnini çıkarır (masaüstü _pdf_metin eşi): önce PyMuPDF, olmazsa pypdf."""
    try:
        import fitz
        with fitz.open(path) as d:
            return "".join(pg.get_text() or "" for pg in d)
    except ImportError:
        pass
    from pypdf import PdfReader
    r = PdfReader(path)
    return "".join((s.extract_text() or "") for s in r.pages)


def mts_dayanak(token, data):
    """Yüklenen PDF'leri masaüstü _pdf_dayanak_tara ile AYNI çift doğrulamayla
    (abone no PDF metninde + borçlu ismi dosya adı/metinde) takiplere eşler."""
    s = mts_session(token)
    if not s or not s.takipler:
        return {"ok": False, "msg": "Önce bir XML/Excel seçin."}
    anahtarli = []
    for t in s.takipler:
        abone = (t.hizmet_abone_no or "").strip()
        if not abone:
            continue
        isim_parcalari = []
        for b in t.borclular:
            for kelime in (b.ad, b.soyad):
                k = (kelime or "").strip().upper()
                if k and len(k) > 1:
                    isim_parcalari.append(k)
        anahtarli.append((str(t.dosya_no), abone, isim_parcalari))
    if not anahtarli:
        return {"ok": True, "matched": {}, "total": len(s.takipler),
                "msg": "Hiçbir takipte hizmet abone no yok; PDF eşleştirme atlandı."}
    files = data.get("files") or []
    tmpdir = tempfile.mkdtemp(prefix="uyap_mts_day_")
    sonuc = {}      # dosya_no -> {"filename","b64"}
    try:
        for fobj in files:
            fn = os.path.basename((fobj.get("filename") or "").strip())
            if not fn.lower().endswith(".pdf"):
                continue
            b64 = fobj.get("data_b64") or ""
            try:
                raw = base64.b64decode(b64)
            except Exception:
                continue
            path = os.path.join(tmpdir, fn)
            try:
                with open(path, "wb") as f:
                    f.write(raw)
                metin = _mts_pdf_metin(path)
                metin_buyuk = metin.upper()
                dosya_adi_buyuk = fn.upper()
                for dosya_no, abone_no, isim_parcalari in anahtarli:
                    if dosya_no in sonuc:
                        continue
                    if abone_no not in metin_buyuk and abone_no not in metin:
                        continue
                    isim_eslesti = any(
                        p in dosya_adi_buyuk or p in metin_buyuk
                        for p in isim_parcalari) if isim_parcalari else True
                    if isim_eslesti:
                        sonuc[dosya_no] = {"filename": fn, "b64": b64}
            except Exception:
                continue
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass
    finally:
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass
    s.dayanak = sonuc
    matched = {dn: v["filename"] for dn, v in sonuc.items()}
    return {"ok": True, "matched": matched, "total": len(s.takipler)}


def mts_start(token, data):
    """Seçili takipleri 'coklu_takip_ac' işiyle ofise gönderir (masaüstü _baslat eşi)."""
    try:
        import is_kuyrugu
    except Exception as e:
        return {"ok": False, "msg": "İş kuyruğu istemcisi yüklenemedi: %s" % e}
    s = mts_session(token)
    if not s or not s.takipler:
        return {"ok": False, "msg": "Önce bir XML/Excel seçin."}
    secili = set(str(x) for x in (data.get("secili") or []))
    secili_takipler = [t for t in s.takipler if str(t.dosya_no) in secili]
    if not secili_takipler:
        return {"ok": False, "msg": "Açılacak takip seçilmedi."}
    il = (data.get("il") or "İzmir").strip() or "İzmir"
    adliye = (data.get("adliye") or "İzmir").strip() or "İzmir"
    onay_modu = (data.get("onay_modu") or "yok").strip()
    from uyap_core.mts import models as mts_models
    vekalet = {}
    for t in secili_takipler:
        v = s.vekalet.get(t.alacakli)
        if v and t.alacakli not in vekalet:
            vekalet[t.alacakli] = {"filename": v["filename"], "b64": v["b64"]}
    dayanak = {}
    for t in secili_takipler:
        d = s.dayanak.get(str(t.dosya_no))
        if d:
            dayanak[str(t.dosya_no)] = {"filename": d["filename"], "b64": d["b64"]}
    params = {
        "takipler": mts_models.takipler_to_params(secili_takipler),
        "il": il, "adliye": adliye, "onay_modu": onay_modu,
        "vekalet": vekalet, "dayanak": dayanak,
    }
    try:
        job = is_kuyrugu.is_baslat("coklu_takip_ac", params)
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    s.job_id = job.get("id")
    return {"ok": True, "job_id": s.job_id, "n": len(secili_takipler)}


def mts_status(token):
    """Aktif işin tam kaydını döndürür (logs/progress/result/pending_approval)."""
    s = mts_session(token)
    jid = s.job_id if s else None
    if not jid:
        return {"loaded": False}
    try:
        import is_kuyrugu
        job = is_kuyrugu.is_durum(jid)
    except Exception as e:
        return {"loaded": True, "error": str(e)}
    return {"loaded": True, "job": job}


def mts_approve(token, data):
    s = mts_session(token)
    if not s or not s.job_id:
        return {"ok": False, "msg": "Aktif iş yok."}
    try:
        import is_kuyrugu
        job = is_kuyrugu.is_onayla(s.job_id, data.get("karar") or {})
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True, "job": job}


def mts_cancel(token):
    s = mts_session(token)
    if not s or not s.job_id:
        return {"ok": False}
    try:
        import is_kuyrugu
        is_kuyrugu.is_iptal(s.job_id)
    except Exception:
        pass
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# UYAP Oturum Kaydı (Logger) — masaüstü LoggerPanel'in sunucu-tarafı eşi. İŞ
# MANTIĞI orijinal logger_core.SessionLogger'dadır (tarayıcı 8800 ofis proxy'sine
# bağlanır, trafiği yakalar; yakalanan çağrılardan *_core.py modülü üretilir).
# Yakalama TEK yerel makineyi (panel makinesini) izlediği için TEK paylaşılan
# motor kullanılır (masaüstüyle aynı tekil davranış). Burada yalnızca sunum +
# HTTP köprüsü vardır; yeni iş mantığı YOK. ([[gorsel-birlestirme-kurali]])
# ─────────────────────────────────────────────────────────────────────────────
_logger = None
_logger_err = None


def _logger_engine():
    global _logger, _logger_err
    if _logger is not None:
        return _logger
    try:
        import logger_core
        _logger = logger_core.SessionLogger(base_url=logger_core.OFFICE_BASE)
    except Exception as e:
        _logger_err = str(e)
        _logger = None
    return _logger


def _logger_kisa(v, n=20):
    """Masaüstü logger._kisa eşi: hücre değerini tek satıra indirip kısaltır."""
    s = "" if v is None else str(v)
    s = s.replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def logger_status(since_log):
    eng = _logger_engine()
    if eng is None:
        return {"running": False, "status": _logger_err or "Logger hazır değil.",
                "logs": [], "log_n": since_log}
    snap = eng.snapshot(since_log)
    try:
        snap["log_dir"] = eng.log_dir
    except Exception:
        snap["log_dir"] = ""
    return snap


def logger_start():
    eng = _logger_engine()
    if eng is None:
        return {"ok": False, "msg": _logger_err or "Logger hazır değil."}
    return {"ok": True, "started": eng.start()}


def logger_stop():
    eng = _logger_engine()
    if eng is None:
        return {"ok": False}
    eng.stop()
    return {"ok": True}


def logger_clear_records():
    eng = _logger_engine()
    if eng is None:
        return {"ok": False}
    eng.records_temizle()
    return {"ok": True}


def logger_endpoints():
    """Endpoint İnceleme verisi: her endpoint için İSTEK alanları (öneri kova +
    örnek değerler + parametre önerisi) ve YANIT alanları (örnek). Kararları
    istemci verir; üretim logger_generate'te yapılır. Masaüstü _tablo_doldur eşi."""
    import logger_core
    eng = _logger_engine()
    if eng is None:
        return {"endpoints": []}
    gruplar = eng.records_snapshot()
    out = []
    for ep, kayitlar in gruplar.items():
        pay_recs = [k for k in kayitlar if isinstance(k.get("payload"), dict)]
        alanlar = []
        for k in pay_recs:
            for a in k["payload"]:
                if a not in alanlar:
                    alanlar.append(a)
        oneri = logger_core._kova_diff_oner(kayitlar)
        gosterilecek = pay_recs[:5]
        istek = []
        for a in alanlar:
            istek.append({
                "alan": a,
                "oneri_kova": oneri.get(a, logger_core.KOVA_SABIT),
                "param_oneri": logger_core._kimlik(a, "p"),
                "degerler": [_logger_kisa(r["payload"].get(a)) for r in gosterilecek],
            })
        _yol, alan_ornek = logger_core.yanit_alanlari(kayitlar)
        yanit = [{"alan": a, "ornek": _logger_kisa(ornek, 30)} for a, ornek in alan_ornek]
        out.append({"endpoint": ep, "count": len(kayitlar),
                    "cagri_basliklari": ["Çağrı %d" % (i + 1) for i in range(len(gosterilecek))],
                    "istek": istek, "yanit": yanit})
    return {"endpoints": out, "kova": {"sabit": logger_core.KOVA_SABIT,
            "girdi": logger_core.KOVA_GIRDI, "ic": logger_core.KOVA_IC}}


def logger_generate(data):
    """Tek endpoint'ten (kararlarla) ya da tüm oturumdan modül üretir, registry'ye
    yazar ve CANLI doğrular. Masaüstü _uret / _session_uret / _kaydet_ve_dogrula eşi."""
    import logger_core
    import importlib
    eng = _logger_engine()
    if eng is None:
        return {"ok": False, "msg": _logger_err or "Logger hazır değil."}
    ad = (data.get("ad") or "").strip()
    grup = (data.get("grup") or "Üretilen Modüller").strip()
    if not ad:
        return {"ok": False, "msg": "Modül adı gerekli."}
    try:
        if data.get("session"):
            kayitlar = eng.records_list()
            if not kayitlar:
                return {"ok": False, "msg": "Önce Başlat'a basıp UYAP'ta birkaç işlem yapın."}
            sonuc = logger_core.oturum_akisi_uret(kayitlar, ad)
            endpoint = "(oturum akışı)"
        else:
            ep = (data.get("endpoint") or "").strip()
            if not ep:
                return {"ok": False, "msg": "Endpoint seçilmedi."}
            kayitlar = eng.records_snapshot().get(ep, [])
            if not kayitlar:
                return {"ok": False, "msg": "Bu endpoint için kayıt yok."}
            kovalar = dict(data.get("kovalar") or {})
            param = dict(data.get("param") or {})
            kolonlar = [(k[0], k[1]) for k in (data.get("kolonlar") or [])
                        if isinstance(k, (list, tuple)) and len(k) == 2]
            sonuc = logger_core.modul_taslagi_uret(ep, kayitlar, kovalar, param,
                                                   kolonlar, dosya_adi=ad)
            endpoint = ep
    except Exception as e:
        return {"ok": False, "msg": "Üretim hatası: %s" % e}
    kayit = {"key": sonuc["key"], "label": ad, "core": sonuc["core"],
             "grup": grup, "endpoint": endpoint}
    if sonuc.get("adim_sayisi"):
        kayit["adim_sayisi"] = sonuc["adim_sayisi"]
    try:
        logger_core.registry_ekle(kayit)
    except Exception as e:
        return {"ok": False, "msg": "Registry'ye yazılamadı: %s" % e}
    # Canlı doğrula: taze yazılan core'u import edip _kendini_dogrula() oynatır.
    dogrula = {"ok": False, "mesaj": ""}
    try:
        mod = importlib.import_module(sonuc["core"])
        importlib.reload(mod)
        dogrula = mod._kendini_dogrula(lambda *a, **k: None) or dogrula
    except Exception as e:
        dogrula = {"ok": False, "mesaj": "Doğrulama çalıştırılamadı: %s" % e}
    return {"ok": True, "core": sonuc["core"], "grup": grup,
            "adim_sayisi": sonuc.get("adim_sayisi"),
            "dogrula": {"ok": bool(dogrula.get("ok")), "mesaj": dogrula.get("mesaj", "")}}


# ─────────────────────────────────────────────────────────────────────────────
# Üretilen Modül Çalıştırıcı (Runner) — masaüstü UretilmisRunner'ın sunucu-tarafı
# eşi. Logger'ın ürettiği herhangi bir ağ-sorgu *_core.py modülünü sürer:
# core.PARAMETRELER'den girdi şeması okunur, core.calistir(girdi, log) ile istek
# 8800 ofis proxy'sine gider, yanıt (liste→tablo / diğer→JSON) döndürülür. Her
# üretilen modül için AYRI arayüz YOK — tek bileşen. Modül adı KATI desenle ve
# gerçek dosya varlığıyla doğrulanır (masaüstü _modul_adi_guvenli_mi eşi; #11).
# ─────────────────────────────────────────────────────────────────────────────
def _uretilmis_guvenli(ad):
    import re
    if not ad or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", ad):
        return False
    hedef = os.path.realpath(os.path.join(MODULES_DIR, ad + ".py"))
    try:
        return (os.path.commonpath([os.path.realpath(MODULES_DIR), hedef])
                == os.path.realpath(MODULES_DIR)) and os.path.isfile(hedef)
    except (ValueError, OSError):
        return False


def _uretilmis_core_bul(key):
    """Sol-menü yaprak anahtarını (key) core modül adına çözer (registry üzerinden)."""
    try:
        import logger_core
        for m in logger_core.registry_oku():
            if m.get("key") == key:
                return m.get("core"), m.get("label", key)
    except Exception:
        pass
    return None, None


def _uretilmis_import(core):
    import importlib
    if not _uretilmis_guvenli(core):
        return None, "Güvenli olmayan ya da bulunamayan modül adı: %r" % core
    try:
        return importlib.import_module(core), None
    except Exception as e:
        return None, str(e)


def _uretilmis_cell(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def _uretilmis_sonuc(sonuc):
    """Sonucu sunum için serileştirir (masaüstü _goster_sonuc eşi): liste-sözlük →
    tablo (kolon+satır), diğer → JSON/metin."""
    if isinstance(sonuc, dict) and sonuc.get("_hata"):
        return {"ok": False, "msg": str(sonuc.get("_hata"))}
    if isinstance(sonuc, list) and sonuc and all(isinstance(x, dict) for x in sonuc):
        kolonlar = []
        for satir in sonuc:
            for k in satir.keys():
                if k not in kolonlar:
                    kolonlar.append(k)
        rows = [[_uretilmis_cell(satir.get(k)) for k in kolonlar] for satir in sonuc]
        return {"ok": True, "type": "table", "columns": kolonlar, "rows": rows, "n": len(sonuc)}
    if isinstance(sonuc, (dict, list)):
        return {"ok": True, "type": "text",
                "text": json.dumps(sonuc, ensure_ascii=False, indent=2, default=str)}
    return {"ok": True, "type": "text", "text": str(sonuc)}


def uretilmis_spec(key):
    core, label = _uretilmis_core_bul(key)
    if not core:
        return {"ok": False, "msg": "Üretilmiş modül bulunamadı: %s" % key}
    mod, err = _uretilmis_import(core)
    if mod is None:
        return {"ok": False, "msg": "Modül yüklenemedi: %s" % err, "core": core}
    parametreler = []
    for p in (getattr(mod, "PARAMETRELER", []) or []):
        try:
            param, etiket, ornek = p
        except Exception:
            continue
        parametreler.append([param, str(etiket), None if ornek is None else str(ornek)])
    excel = getattr(mod, "EXCEL_GIRDI", None)
    excel_out = None
    if isinstance(excel, dict):
        excel_out = {"etiket": str(excel.get("etiket", "Dosya")),
                     "fonksiyon": excel.get("fonksiyon", "excel_isle")}
    return {"ok": True, "core": core, "baslik": label,
            "parametreler": parametreler, "excel_girdi": excel_out}


def uretilmis_run(data):
    key = (data.get("key") or "").strip()
    core, label = _uretilmis_core_bul(key)
    if not core:
        return {"ok": False, "msg": "Üretilmiş modül bulunamadı: %s" % key}
    mod, err = _uretilmis_import(core)
    if mod is None:
        return {"ok": False, "msg": "Modül yüklenemedi: %s" % err}
    logs = []

    def logfn(*a):
        logs.append(" ".join(str(x) for x in a))

    fobj = data.get("file")
    try:
        if fobj and isinstance(getattr(mod, "EXCEL_GIRDI", None), dict):
            excel = mod.EXCEL_GIRDI
            fn = getattr(mod, excel.get("fonksiyon", "excel_isle"), None)
            if fn is None:
                return {"ok": False, "msg": "Modülde toplu-girdi fonksiyonu bulunamadı."}
            filename = os.path.basename((fobj.get("filename") or "girdi").strip()) or "girdi"
            try:
                raw = base64.b64decode(fobj.get("data_b64") or "")
            except Exception:
                return {"ok": False, "msg": "Dosya verisi çözülemedi."}
            tmpdir = tempfile.mkdtemp(prefix="uyap_uret_")
            path = os.path.join(tmpdir, filename)
            try:
                with open(path, "wb") as f:
                    f.write(raw)
                sonuc = fn(path, log_fn=logfn)
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass
                try:
                    os.rmdir(tmpdir)
                except Exception:
                    pass
        else:
            sonuc = mod.calistir(dict(data.get("girdi") or {}), logfn)
    except Exception as e:
        return {"ok": False, "msg": str(e), "logs": logs}
    out = _uretilmis_sonuc(sonuc)
    out["logs"] = logs
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "UyapPanel/1.0"

    # ── yardımcılar ──
    def _csrf_ok(self):
        """CSRF koruması (durum değiştiren istekler için): çapraz-site KANITI varsa reddet.
        Panelin kendi fetch çağrıları aynı-origin'dir (`Sec-Fetch-Site: same-origin`,
        `Origin` = adres çubuğu). Başka bir sitenin tarayıcıdan tetiklediği sahte istek
        yabancı `Origin` / `cross-site` taşır → engellenir. Tarayıcı-dışı yerel istemci
        (Origin/Sec-Fetch göndermez) CSRF vektörü değildir, serbest bırakılır."""
        sfs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if sfs and sfs not in ("same-origin", "same-site", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower() != "null":
            try:
                o_hostport = urlparse(origin).netloc.lower()
            except Exception:
                return False
            if o_hostport != (self.headers.get("Host") or "").lower():
                return False
        return True

    def _session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        tok = cookie["session"].value if "session" in cookie else None
        return SESSIONS.get(tok)

    def _user(self):
        s = self._session()
        return s.get("user") if s else None

    def _is_master(self):
        """Güvenlik raporu bulgu #8: ofisin BİRİNCİL (master) hesabı mı — yoksa sonradan
        eklenen ek personel (member) hesabı mı? role yalnızca dev/açık-mod gibi hesapsız
        girişlerde boş gelir (vendor tarafında zaten izin verilen, hesap gerektirmeyen
        durum) — o durumda kısıtlama uygulanmaz, mevcut davranış korunur."""
        s = self._session()
        if not s:
            return False
        role = s.get("role", "")
        return role in ("", "master")

    def _token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["session"].value if "session" in cookie else None

    def _body(self):
        try:
            ln = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(ln) or b"{}") if ln else {}
        except Exception:
            return {}

    def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, cookie=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), cookie=cookie)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _serve_file(self, name):
        path = os.path.normpath(os.path.join(HERE, name))
        if not path.startswith(HERE) or not os.path.isfile(path):
            self._send(404, "Bulunamadı", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            self._send(200, f.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    # ── GET ──
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if self._user():
                self._serve_file("index.html")
            else:
                self._redirect("login")
        elif path in ("/login", "/login.html"):
            self._serve_file("login.html")
        elif path.startswith("/static/"):
            self._serve_file(path.lstrip("/"))
        elif path == "/api/me":
            self._json(200, {"user": self._user()})
        elif path == "/api/store/catalog":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, store_catalog())
        elif path == "/api/settings":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, get_settings())
        elif path == "/api/conn/status":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            try:
                since = int(urlparse(self.path).query.split("since=")[-1]) \
                    if "since=" in self.path else 0
            except Exception:
                since = 0
            self._json(200, ConnManager.status(since))
        elif path == "/api/udf/dlls":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            deep = q.get("deep", ["0"])[0] == "1"
            self._json(200, {"dlls": udf_dlls(deep), "ready": _udf is not None,
                             "err": _udf_err})
        elif path == "/api/uretilmis/spec":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            self._json(200, uretilmis_spec((q.get("key", [""])[0] or "").strip()))
        elif path == "/api/logger/status":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                since_log = int(q.get("log", ["0"])[0])
            except Exception:
                since_log = 0
            self._json(200, logger_status(since_log))
        elif path == "/api/logger/endpoints":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, logger_endpoints())
        elif path == "/api/mts/conn":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, mts_conn())
        elif path == "/api/mts/status":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, mts_status(self._token()))
        elif path == "/api/icra/fields":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, icra_fields())
        elif path == "/api/icra/units":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, {"units": icra_units()})
        elif path == "/api/senkron-kapsami":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, senkron_kapsami_durumu())
        elif path == "/api/senkron-kapsami/yenile":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                yargi_turu = int(q.get("yargi_turu", ["-1"])[0])
            except Exception:
                yargi_turu = -1
            self._json(200, senkron_kapsami_yenile(yargi_turu))
        elif path == "/api/dosyalarim/fields":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            self._json(200, dosyalarim_fields())
        elif path == "/api/dosyalarim/birimler":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                yargi_turu = int(q.get("yargi_turu", ["-1"])[0])
            except Exception:
                yargi_turu = -1
            self._json(200, dosyalarim_birimler(yargi_turu))
        elif path == "/api/dosyalarim/yenile-durum":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                since_log = int(q.get("log", ["0"])[0])
            except Exception:
                since_log = 0
            self._json(200, dosyalarim_yenile_durum(self._token(), since_log))
        elif path == "/api/icra/status":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            job = icra_job(self._token())
            if not job:
                self._json(200, {"loaded": False})
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                since_log = int(q.get("log", ["0"])[0])
                since_rev = int(q.get("rev", ["0"])[0])
            except Exception:
                since_log = since_rev = 0
            self._json(200, job.snapshot(since_log, since_rev))
        elif path == "/api/sgk/status":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            batch = sgk_batch(self._token())
            if not batch:
                self._json(200, {"loaded": False})
                return
            try:
                since_log = int(q.get("log", ["0"])[0])
                since_rev = int(q.get("rev", ["0"])[0])
            except Exception:
                since_log = since_rev = 0
            snap = batch.snapshot(since_log, since_rev)
            snap["loaded"] = True
            self._json(200, snap)
        elif path == "/api/sgk/cell":
            if not self._user():
                self._json(401, {"error": "oturum yok"})
                return
            q = parse_qs(urlparse(self.path).query)
            batch = sgk_batch(self._token())
            if not batch:
                self._json(200, {"value": ""})
                return
            try:
                r = int(q.get("r", ["0"])[0])
                c = int(q.get("c", ["0"])[0])
            except Exception:
                r = c = 0
            self._json(200, {"value": batch.hucre_tam_deger(r, c)})
        elif path == "/api/sgk/download":
            if not self._user():
                self._send(401, "oturum yok", "text/plain; charset=utf-8")
                return
            batch = sgk_batch(self._token())
            if not batch or not batch.excel_yolu or not os.path.isfile(batch.excel_yolu):
                self._send(404, "Henüz çıktı yok", "text/plain; charset=utf-8")
                return
            with open(batch.excel_yolu, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(batch.excel_yolu))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(404, "Bulunamadı", "text/plain; charset=utf-8")

    # ── POST ──
    def do_POST(self):
        path = urlparse(self.path).path
        # Tüm durum değiştiren uçlar (login/logout/conn/udf/sgk) CSRF korumalıdır.
        if not self._csrf_ok():
            self._json(403, {"ok": False, "msg": "Çapraz-site istek reddedildi (CSRF)."})
            return
        if path == "/api/login":
            # Bulgu #9: IP başına deneme sınırlaması. Varsayılan bağlamada (127.0.0.1) etkisi
            # sınırlı ama UYAP_PANEL_LAN=1 açıksa bu uç, kilit olmadan UYAP'a karşı sınırsız
            # credential-stuffing proxy'sine dönüşebiliyordu.
            guard_key = self.client_address[0] if self.client_address else "?"
            wait = LOGIN_GUARD.blocked_for(guard_key)
            if wait:
                self._json(429, {"ok": False,
                                 "msg": "Çok fazla başarısız deneme. %d sn sonra tekrar deneyin." % wait})
                return
            try:
                ln = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(ln) or b"{}")
            except Exception:
                self._json(400, {"ok": False, "msg": "Geçersiz istek."})
                return
            office = (data.get("office_code") or "").strip()
            user = (data.get("username") or "").strip()
            pw = (data.get("password") or "").strip()
            remember = bool(data.get("remember"))
            if not user or not pw:
                self._json(200, {"ok": False, "msg": "Kullanıcı adı ve parola gerekli."})
                return
            # İki-alanlı login (site/masaüstü ile aynı): ofis kodu ayrı gelir, vendor
            # 'kullanici@ofis' olarak birleştirir. Ofis kodu yalnızca kullanıcı adı
            # zaten tam biçimdeyse ("ad@ofis") boş bırakılabilir.
            if not office and "@" not in user:
                self._json(200, {"ok": False, "msg": "Ofis kodu gerekli (büronuzu tanımlayan "
                                 "kod; ör. kullanıcı 'veli', ofis 'kalkan')."})
                return
            ok, msg, role = verify(user, pw, office)
            if ok:
                LOGIN_GUARD.record_ok(guard_key)
                token = secrets.token_urlsafe(24)
                # Parola, paylaş/al (office_agent) için sunucu belleğinde tutulur;
                # istemciye ASLA gönderilmez.
                SESSIONS[token] = {"user": user, "pw": pw, "office": office, "role": role}
                remember_config(user, pw, remember, office)
                # Ayar 'share'/'receive' ise UYAP bağlantısını açılışta otomatik kur
                # (masaüstü _maybe_autoconnect eşi). Bloklamadan arka planda.
                threading.Thread(target=maybe_autoconnect, args=(user, pw, office),
                                 daemon=True).start()
                ck = "session=%s; Path=/%s" % (token, _cookie_attrs())
                self._json(200, {"ok": True}, cookie=ck)
            else:
                LOGIN_GUARD.record_fail(guard_key)
                self._json(200, {"ok": False, "msg": msg})
        elif path == "/api/logout":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            tok = cookie["session"].value if "session" in cookie else None
            SESSIONS.pop(tok, None)
            self._json(200, {"ok": True}, cookie="session=; Path=/; Max-Age=0")
        elif path == "/api/store/toggle":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, store_toggle(self._body()))
        elif path == "/api/settings":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, set_settings(self._body()))
        elif path == "/api/senkron-kapsami":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            body = self._body()
            self._json(200, senkron_kapsami_kaydet_web(body.get("secimler") or []))
        elif path.startswith("/api/conn/"):
            sess = self._session()
            if not sess:
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            try:
                ln = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(ln) or b"{}") if ln else {}
            except Exception:
                data = {}
            user, pw = sess.get("user"), sess.get("pw")
            office = sess.get("office", "")
            if path == "/api/conn/share":
                _pin = (data.get("pin") or "").strip()
                _cert = (data.get("cert_id") or "").strip()
                ok, msg = ConnManager.start_share(user, pw, _pin, _cert, office)
                # 'PIN'i kaydet' işaretliyse DPAPI ile sakla → açılışta otomatik paylaşım
                # (boot_autoconnect) bir daha PIN sormadan çalışır.
                if ok and data.get("save_pin"):
                    remember_pin(_pin, _cert)
            elif path == "/api/conn/share/stop":
                ok, msg = ConnManager.stop_share()
            elif path == "/api/conn/receive":
                ok, msg = ConnManager.start_receive(user, pw, office)
            elif path == "/api/conn/receive/stop":
                ok, msg = ConnManager.stop_receive()
            else:
                self._send(404, "Bulunamadı", "text/plain; charset=utf-8")
                return
            self._json(200, {"ok": ok, "msg": msg})
        elif path == "/api/udf/convert":
            if not self._user():
                self._json(401, {"ok": False, "logs": ["oturum yok"]})
                return
            self._json(200, udf_process(self._body()))
        elif path == "/api/udf/sign-existing":
            if not self._user():
                self._json(401, {"ok": False, "logs": ["oturum yok"]})
                return
            self._json(200, udf_sign_existing(self._body()))
        elif path == "/api/icra/search":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, icra_search(self._token(), self._body()))
        elif path == "/api/icra/detay":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, icra_detay(self._token(), self._body()))
        elif path == "/api/dosyalarim/list":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, dosyalarim_list(self._body()))
        elif path == "/api/dosyalarim/detay":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, dosyalarim_detay(self._body()))
        elif path == "/api/dosyalarim/yenile":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, dosyalarim_yenile_baslat(self._token()))
        elif path == "/api/uretilmis/run":
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            self._json(200, uretilmis_run(self._body()))
        elif path.startswith("/api/logger/"):
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            if path == "/api/logger/start":
                self._json(200, logger_start())
            elif path == "/api/logger/stop":
                self._json(200, logger_stop())
            elif path == "/api/logger/clear-records":
                self._json(200, logger_clear_records())
            elif path == "/api/logger/generate":
                # Güvenlik raporu bulgu #8: bu uç sunucuda YENİ .py modülü yazar — herhangi
                # bir personel (member) değil, yalnızca ofisin master hesabı çağırabilir.
                # /api/uretilmis/run KASITLI olarak kısıtlanmadı: personel, master'ın önceden
                # ürettiği/onayladığı modülleri günlük iş akışında çalıştırmaya devam eder.
                if not self._is_master():
                    self._json(403, {"ok": False,
                                     "msg": "Bu işlem yalnızca ofis yöneticisi (master) hesabıyla yapılabilir."})
                    return
                self._json(200, logger_generate(self._body()))
            else:
                self._send(404, "Bulunamadı", "text/plain; charset=utf-8")
        elif path.startswith("/api/mts/"):
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            token = self._token()
            if path == "/api/mts/parse":
                self._json(200, mts_parse(token, self._body()))
            elif path == "/api/mts/clear":
                self._json(200, mts_clear(token))
            elif path == "/api/mts/vekalet":
                self._json(200, mts_vekalet(token, self._body()))
            elif path == "/api/mts/dayanak":
                self._json(200, mts_dayanak(token, self._body()))
            elif path == "/api/mts/start":
                self._json(200, mts_start(token, self._body()))
            elif path == "/api/mts/approve":
                self._json(200, mts_approve(token, self._body()))
            elif path == "/api/mts/cancel":
                self._json(200, mts_cancel(token))
            else:
                self._send(404, "Bulunamadı", "text/plain; charset=utf-8")
        elif path.startswith("/api/sgk/"):
            if not self._user():
                self._json(401, {"ok": False, "msg": "oturum yok"})
                return
            token = self._token()
            if path == "/api/sgk/upload":
                self._json(200, sgk_upload(token, self._body()))
                return
            batch = sgk_batch(token)
            if not batch:
                self._json(200, {"ok": False, "msg": "Önce bir Excel yükleyin."})
                return
            if path == "/api/sgk/start":
                data = self._body()
                secili = data.get("selected") or None
                ok = batch.start(data.get("mode") or "normal", secili)
                self._json(200, {"ok": ok})
            elif path == "/api/sgk/stop":
                batch.stop()
                self._json(200, {"ok": True})
            elif path == "/api/sgk/pause":
                paused = batch.toggle_pause()
                self._json(200, {"ok": True, "paused": paused})
            else:
                self._send(404, "Bulunamadı", "text/plain; charset=utf-8")
        else:
            self._send(404, "Bulunamadı", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # sessiz


def _panel_bind_host():
    """Panel varsayılan olarak yalnızca `127.0.0.1` dinler. Tüm ağa açım (LAN/tünel)
    yalnızca açık onayla: UYAP_PANEL_LAN = 1/true/yes/on. Dışarı açım yine de
    kimlik doğrulamalı bir ters-proxy + HTTPS arkasından yapılmalıdır."""
    if (os.environ.get("UYAP_PANEL_LAN", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        return "0.0.0.0"
    return "127.0.0.1"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    # Windows'ta aiortc/uvicorn (office_agent/home_client) için Proactor döngüsü.
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass
    threading.Thread(target=_load_auth, daemon=True).start()
    host = _panel_bind_host()
    srv = ThreadingHTTPServer((host, port), Handler)
    print("UYAP Calisma Paneli (web) -> http://127.0.0.1:%d  (giris: /login)" % port)
    if host == "0.0.0.0":
        print("[!] Panel TUM AGA acik (%s:%d); yalnizca kimlik dogrulamali ters-proxy + "
              "HTTPS arkasindan disari acin. Internete dogrudan port-forward ETMEYIN." % (host, port))
    else:
        print("[i] Panel yalnizca yerel arayuzde (%s:%d) dinliyor; disari acmak icin "
              "UYAP_PANEL_LAN=1 (tercihen ters-proxy + HTTPS ile)." % (host, port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatılıyor…")
        srv.shutdown()


if __name__ == "__main__":
    main()
