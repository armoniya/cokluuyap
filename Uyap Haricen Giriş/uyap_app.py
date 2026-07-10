#!/usr/bin/env python3
"""
UYAP Ağ Geçidi - Birleşik Masaüstü Kontrol Paneli (uyap_app.py)
------------------------------------------------------------
TEK program. İki rolü tek arayüzde birleştirir:

  • PAYLAŞ (Ofis): e-imza ile UYAP'a girer, oturumu canlı tutar ve HEM yerel ağda
    (LAN, 0.0.0.0:8800) HEM dış ağda (bulut signaling üzerinden WebRTC) paylaşır.
    Her iki yol AYNI UYAP oturumunu kullanır (tek e-imza girişi).

  • AL (İstemci): aynı programı başka bir bilgisayarda kullanan kişi, aynı oda anahtarı
    ile bağlantı alır. Ofis aynı yerel ağdaysa doğrudan LAN'dan, değilse dış ağdan
    (WebRTC tüneli) — otomatik seçilir. Tarayıcıda http://127.0.0.1:8800/giris açılır.
"""

import os
import sys
import json
import time
import queue
import base64
import socket
import logging
import subprocess
import argparse
import asyncio
import threading
import webbrowser
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from tkinter import font as tkfont

# Signaling ile iletişim için websockets kitaplığı gerekir
try:
    import websockets
except ImportError:
    websockets = None

from uyap_core import uyap_proxy, office_agent, home_client
# Tek tasarım dili: Panel/theme.py paleti (ılık kâğıt + adaçayı). uyap_theme bu
# paletin uygulamaya taşınmış kopyasıdır — palet değişirse ikisi birlikte güncellenir.
from uyap_theme import C, RoundButton, make_fonts, round_rect


class QueueWriteStream:
    def __init__(self, q):
        self.q = q

    def write(self, message):
        if message:
            self.q.put(message)

    def flush(self):
        pass

    # uvicorn'un log formatlayıcısı sys.stdout.isatty() çağırır; yönlendirilmiş akışta
    # bu metot yoksa "Unable to configure formatter 'default'" hatası verir.
    def isatty(self):
        return False

    def fileno(self):
        raise OSError("QueueWriteStream: fileno yok")


# Kullanıcı bu sabiti derlemeden önce kendi Render alt alan adı (subdomain) ile değiştirebilir.
DEFAULT_SERVER_URL = "wss://www.cokluuyap.com/ws"

# Config dosyası yolu
WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
# Django panelinin (uyap_panel/web) kaynak konumu — yalnızca kaynaktan çalışırken kullanılır
# (bkz start_panel_server); frozen derlemede WORKSPACE_DIR aşağıda LOCALAPPDATA'ya döner ama
# SRC_DIR script'in gerçek konumunu korur.
SRC_DIR = WORKSPACE_DIR
if getattr(sys, "frozen", False):
    # PyInstaller paketi: __file__ paketin içine düşer (güncellemede silinir/taşınır);
    # ayarların kalıcı olması için kullanıcıya özel klasöre yazılır.
    WORKSPACE_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "CokluUyap")
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
    except OSError:
        pass
CONFIG_PATH = os.path.join(WORKSPACE_DIR, "uyap_app_config.json")

LOCAL_PORT = 8800


def free_port_if_busy(port, log=print):
    """Paylaşım/alma başlamadan önce yerel portun (8800) boş olduğundan emin ol.

    Önceki bir oturumdan düzgün kapanmamış bir UYAP süreci portu hâlâ tutuyorsa,
    yeni uvikorn 'WinError 10048 (yalnızca bir kullanıma izin veriliyor)' ile bağlanamaz
    ve paylaşım sessizce çöker; karşı taraf da bu yüzden 'ofis ayrıldı' görür.
    Bu yüzden portu tutan ESKİ python sürecini bulup kapatır, portu serbest bırakırız.
    Alakasız (python olmayan) bir uygulama tutuyorsa ona DOKUNMAYIZ, sadece uyarırız.
    """
    # Port boşsa hemen dön (bağlanabiliyor muyuz?)
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
        return  # port serbest
    except OSError:
        pass  # meşgul — aşağıda temizlenecek
    finally:
        try:
            probe.close()
        except Exception:
            pass

    if os.name != "nt":
        log(f"[SİSTEM] {port} portu meşgul; lütfen önceki örneği kapatıp tekrar deneyin.\n")
        return

    # Portu LISTENING tutan PID'leri netstat ile bul.
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception as e:
        log(f"[SİSTEM] Port kontrolü yapılamadı ({e}); paylaşım yine de denenecek.\n")
        return

    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            pids.add(parts[4])

    for pid in pids:
        try:
            tl = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.lower()
        except Exception:
            tl = ""
        if "python" in tl:
            log(f"[SİSTEM] {port} portunu tutan eski UYAP süreci (PID {pid}) kapatılıyor...\n")
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as e:
                log(f"[SİSTEM] Eski süreç kapatılamadı (PID {pid}): {e}\n")
        else:
            log(f"[SİSTEM] UYARI: {port} portunu python olmayan bir uygulama (PID {pid}) tutuyor; dokunulmadı.\n")

    time.sleep(0.8)  # portun işletim sistemince serbest bırakılması için kısa bekleme

DEFAULTS = {
    "server_url": DEFAULT_SERVER_URL,
    "username": "",
    "remember": True,
    "password_enc": "",
    "pin_enc": "",
    "cert_id": "",
    "local_port": LOCAL_PORT,
    # Kur-unut: bu program müşteriye "ofis sunucusu" olarak dağıtılır; varsayılan
    # davranış girişte paylaşımı kendiliğinden başlatmaktır. Üye olarak kullanan
    # (bağlantı alan) kişi giriş kartındaki işareti kaldırır.
    "auto_share": True,
}

# ── DPAPI İle Şifreleme (Yalnızca Windows) ──
IS_WINDOWS = os.name == 'nt'


def _dpapi(func, data: bytes) -> bytes:
    if not IS_WINDOWS:
        raise NotImplementedError("DPAPI yalnızca Windows üzerinde kullanılabilir.")

    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()

    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ── Sır şifreleme: DPAPI (Windows) + passphrase fallback (her OS), FAIL-CLOSED ──────
# Güvenlik raporu #9: önceden Windows dışında ya da DPAPI başarısızlığında sır DÜZ METİN
# yazılıyordu. Artık düz metne ASLA düşülmez (fail-closed). Bkz. uyap_panel/core/config.py
# (aynı mantığın kanonik kopyası). Jeton: "dpapi:<b64>" | "pbk:<b64salt>:<fernet>" | (eski) düz.
_PASSPHRASE_ENV = "UYAP_CONFIG_SECRET"
_warned = set()


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(f"[GÜVENLİK] {msg}")


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _passphrase_encrypt(plain: str):
    secret = os.environ.get(_PASSPHRASE_ENV, "")
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
        salt = os.urandom(16)
        token = Fernet(_derive_key(secret, salt)).encrypt(plain.encode("utf-8"))
        return "pbk:" + base64.b64encode(salt).decode("ascii") + ":" + token.decode("ascii")
    except Exception as e:
        _warn_once("pbk_enc", f"Passphrase şifreleme başarısız ({e}); sır KAYDEDİLMEDİ.")
        return None


def _passphrase_decrypt(token: str) -> str:
    secret = os.environ.get(_PASSPHRASE_ENV, "")
    if not secret:
        _warn_once("pbk_dec", f"{_PASSPHRASE_ENV} ayarlı değil; kayıtlı sır çözülemiyor "
                              "(yeniden girmeniz gerekebilir).")
        return ""
    try:
        from cryptography.fernet import Fernet
        _, b64salt, ftoken = token.split(":", 2)
        salt = base64.b64decode(b64salt.encode("ascii"))
        return Fernet(_derive_key(secret, salt)).decrypt(ftoken.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def encrypt_secret(plain: str) -> str:
    """Sırrı güvenli biçimde şifreler. ASLA düz metin döndürmez (fail-closed); şifreleme
    yapılamıyorsa boş döner ve kullanıcıyı uyarır (bkz. güvenlik raporu #9)."""
    if not plain:
        return ""
    if IS_WINDOWS:
        try:
            import ctypes
            enc = _dpapi(ctypes.windll.crypt32.CryptProtectData, plain.encode("utf-8"))
            return "dpapi:" + base64.b64encode(enc).decode("ascii")
        except Exception as e:
            _warn_once("dpapi_enc", f"DPAPI şifreleme başarısız ({e}); passphrase fallback denenecek.")
    tok = _passphrase_encrypt(plain)
    if tok is not None:
        return tok
    _warn_once("no_enc", "Sır güvenli şekilde şifrelenemedi -> DİSKE YAZILMADI (fail-closed). "
                         f"Windows dışında saklamak için {_PASSPHRASE_ENV} ortam değişkenini ayarlayın.")
    return ""


def decrypt_secret(token: str) -> str:
    """Şifrelenmiş sırrı çözer; çözülemezse boş döner (sızdırmaz)."""
    if not token:
        return ""
    if token.startswith("dpapi:"):
        if not IS_WINDOWS:
            _warn_once("dpapi_other_os", "Kayıtlı sır Windows DPAPI ile şifrelenmiş; bu OS'te "
                                         "çözülemez (yeniden girmeniz gerekir).")
            return ""
        try:
            import ctypes
            raw = base64.b64decode(token[len("dpapi:"):].encode("ascii"))
            return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, raw).decode("utf-8")
        except Exception:
            return ""
    if token.startswith("pbk:"):
        return _passphrase_decrypt(token)
    # Eski sürümden kalan DÜZ METİN sır (geriye dönük OKUMA).
    return token


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception:
            pass
    # Sunucu adresi önceliği: UYAP_SERVER_URL ortam değişkeni → config dosyası → derlenmiş
    # varsayılan. Env en üstte: alan adı taşımada/dağıtımda dosyayı düzenlemeden geçersiz kılar.
    env_url = os.environ.get("UYAP_SERVER_URL", "").strip()
    if env_url:
        cfg["server_url"] = env_url
    return cfg


def _restrict_perms(path: str) -> None:
    """Ayar dosyasını yalnızca mevcut kullanıcıya kısıtla (sır içerir). POSIX'te 0600;
    Windows'ta os.chmod sessiz no-op olduğundan (bulgu #11) gerçek ACL kısıtlaması icacls
    ile ayrıca uygulanır: kalıtım kesilir, yalnızca mevcut kullanıcıya tam yetki verilir."""
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    if os.name == "nt":
        kullanici = os.environ.get("USERNAME", "").strip()
        if kullanici:
            try:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{kullanici}:(F)"],
                    capture_output=True, timeout=5, check=False,
                )
            except Exception:
                pass


def save_config(cfg):
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _restrict_perms(tmp)
        os.replace(tmp, CONFIG_PATH)
        _restrict_perms(CONFIG_PATH)
    except Exception as e:
        print(f"[GUI] Ayar kaydedilemedi: {e}")


# ── Windows Başlangıç kısayolu (kur-unut) ────────────────────────────────────
# Otomatik paylaşım açıkken paketlenmiş exe kendini Başlangıç'a yazar: bilgisayar
# her açıldığında uygulama kendiliğinden çalışır → otomatik giriş → otomatik paylaşım.
STARTUP_SHORTCUT = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "CokluUyapOfis.lnk")


def sync_startup_shortcut(enable, log=print):
    """Başlangıç kısayolunu kurar/söker. Yalnız paketlenmiş (PyInstaller) exe'de
    çalışır; geliştirme modunda dokunmaz (python.exe'yi Başlangıç'a yazmak yanıltır)."""
    if not IS_WINDOWS or not getattr(sys, "frozen", False):
        return
    try:
        if not enable:
            if os.path.exists(STARTUP_SHORTCUT):
                os.remove(STARTUP_SHORTCUT)
                log("[SİSTEM] Başlangıç kısayolu kaldırıldı.\n")
            return
        target = sys.executable
        ps = ("$w = New-Object -ComObject WScript.Shell; "
              "$s = $w.CreateShortcut('%s'); "
              "$s.TargetPath = '%s'; $s.WorkingDirectory = '%s'; $s.Save()") % (
            STARTUP_SHORTCUT.replace("'", "''"),
            target.replace("'", "''"),
            os.path.dirname(target).replace("'", "''"))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       timeout=30, check=True)
        log("[SİSTEM] Başlangıç kısayolu kuruldu: bilgisayar açılınca paylaşım "
            "kendiliğinden başlar.\n")
    except Exception as e:
        log(f"[SİSTEM] Başlangıç kısayolu ayarlanamadı: {e}\n")


# ── Yerel Django paneli (uyap_panel/web) — uzaktan "Panel'i Aç" için ─────────────────
# Paylaşım açıldığında bu ayrı süreç 127.0.0.1:8000'de başlar; uyap_core.uyap_proxy
# içindeki /__panel__/... öneki bunu UYAP tünelinin ÜZERİNDEN (aynı WebRTC/relay
# bağlantısı, ikinci bir tünel AÇMADAN) ham HTTP proxy'ler. FORCE_SCRIPT_NAME'i bu
# önekle eşleştiriyoruz ki Django'nun ürettiği tüm bağlantılar (statik, yönlendirme,
# {% url %}) zaten doğru önekle gelsin.
# NOT: yalnızca KAYNAKTAN çalışırken etkindir (frozen derlemeye taşınması ayrı bir iş —
# manage.py runserver'ın PyInstaller paketine gömülmesi ayrı test ister).
PANEL_DIR = os.path.join(SRC_DIR, "uyap_panel", "web")
PANEL_SCRIPT_PREFIX = "/__panel__"
panel_process = None


def start_panel_server(log=print):
    global panel_process
    if getattr(sys, "frozen", False):
        return  # paketlenmiş sürümde henüz desteklenmiyor
    if panel_process and panel_process.poll() is None:
        return  # zaten çalışıyor
    manage_py = os.path.join(PANEL_DIR, "manage.py")
    if not os.path.isfile(manage_py):
        log("[SİSTEM] Panel kaynağı bulunamadı, 'Panel'i Aç' devre dışı kalacak.\n")
        return
    # start_sharing 8800 için de aynısını yapar: önceki oturumdan düzgün kapanmamış bir
    # panel süreci varsa temizlenmezse yeni Django örneği sessizce bağlanamaz.
    free_port_if_busy(8000, log)
    env = os.environ.copy()
    env["UYAP_PANEL_SCRIPT_NAME"] = PANEL_SCRIPT_PREFIX
    try:
        panel_process = subprocess.Popen(
            [sys.executable, manage_py, "runserver", "127.0.0.1:8000",
             "--noreload", "--insecure"],
            cwd=PANEL_DIR, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log("[SİSTEM] Panel yerel sunucusu başlatıldı (127.0.0.1:8000).\n")
    except Exception as e:
        log(f"[SİSTEM] Panel başlatılamadı: {e}\n")


def stop_panel_server(log=print):
    global panel_process
    if panel_process and panel_process.poll() is None:
        try:
            panel_process.terminate()
        except Exception as e:
            log(f"[SİSTEM] Panel süreci durdurulurken hata: {e}\n")
    panel_process = None


def detect_ips():
    """Makinenin erişilebilir yerel IPv4 adreslerini tespit eder."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


# ── Giriş Doğrulama Arka Plan İşlemi ──
def verify_credentials_thread(url, username, password, result_queue, office_code=""):
    """Signaling sunucusuna bağlanarak kimlik bilgilerini doğrular. office_code verilirse
    iki-alanlı login: sunucu 'kullanici@ofis_kodu' olarak birleştirir. Sonucu
    (ok, mesaj, info) olarak kuyruğa koyar; info başarıda {role, office_code, office_label}."""
    async def _verify():
        try:
            # Bedava PaaS (Render) uykudayken ilk istek soğuk başlangıçla ~10-30 sn sürebilir;
            # bu yüzden açılış zaman aşımı cömert tutulur (eski 5 sn yanlış "zaman aşımı" verdi).
            async with websockets.connect(url, open_timeout=30.0, ping_timeout=20.0) as ws:
                # role=probe: SALT kimlik doğrulama. 'office' rolü kullanılmaz; o rol gerçek
                # ofis ajanının slotunu ele geçirip tüm bağlı istemcileri düşürüyordu.
                await ws.send(json.dumps({"role": "probe", "room": username,
                                          "office_code": office_code, "password": password}))
                raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "joined":
                    result_queue.put((True, "Başarılı", {
                        "role": msg.get("role", "member"),
                        "office_code": msg.get("office_code", ""),
                        "office_label": msg.get("office_label", ""),
                    }))
                elif mtype == "error":
                    result_queue.put((False, msg.get("error", "Bilinmeyen hata."), {}))
                else:
                    result_queue.put((False, f"Beklenmeyen sunucu yanıtı: {mtype}", {}))
        except asyncio.TimeoutError:
            result_queue.put((False, "Sunucu zaman aşımına uğradı. Sunucu uykudaysa birkaç saniye bekleyip tekrar deneyin.", {}))
        except Exception as e:
            result_queue.put((False, f"Bağlantı başarısız: {str(e)}", {}))

    try:
        asyncio.run(_verify())
    except Exception as e:
        result_queue.put((False, f"Çalışma zamanı hatası: {str(e)}", {}))


class UyapApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.log_queue = queue.Queue()
        self.login_queue = queue.Queue()

        # stdout/stderr yönlendirme (alt katman logları arayüze düşsün)
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        sys.stdout = QueueWriteStream(self.log_queue)
        sys.stderr = QueueWriteStream(self.log_queue)

        self.is_sharing = False
        self.is_receiving = False
        self.login_in_progress = False

        self.username = ""
        self.password = ""
        self.office_code = ""
        self.role = "member"
        self.office_label = ""
        self.server_url = self.cfg.get("server_url", DEFAULT_SERVER_URL)

        self.root.title("Çoklu UYAP - Kontrol Paneli")
        self.root.configure(bg=C.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._login_positioned = False
        self._setting_taskbar_icon = False

        self.fonts = make_fonts()
        self.setup_styles()

        self.container = ttk.Frame(self.root, style='TFrame')
        self.container.pack(fill='both', expand=True)

        self.show_login_screen()
        self.poll_logs()

    def setup_styles(self):
        """Panel/panel.py + Panel/modules ile BİREBİR aynı görsel dil: o kabuk hiçbir
        ttk.Entry/LabelFrame/Checkbutton kullanmaz (düz tk widget + ince kenarlık,
        bkz. modules/baglanti.py, modules/logger.py) — yalnızca Notebook/Treeview/
        Combobox ttk üzerinden gelir ve modules/logger.py'deki 'Logger.*' stil
        adlarıyla BİREBİR aynı değerlerle düzleştirilir."""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background=C.BG, foreground=C.INK, fieldbackground='#FFFFFF')
        style.configure('TFrame', background=C.BG)
        style.configure('TLabel', background=C.BG, foreground=C.INK, font=('Segoe UI', 10))
        # Tablo (Treeview) — modules/logger.py "Logger.Treeview" ile birebir
        style.configure('Custom.Treeview', background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=('Segoe UI', 10))
        style.map('Custom.Treeview', background=[('selected', C.SAGE_TINT)],
                  foreground=[('selected', C.INK)])
        style.configure('Custom.Treeview.Heading', background=C.HEADER, foreground=C.INK_SOFT,
                        relief='flat', font=('Segoe UI Semibold', 9))
        style.map('Custom.Treeview.Heading', background=[('active', C.SAGE_TINT)])
        # Açılır kutu (Combobox) — tek kullanım yeri (sıfırlama politikası)
        style.configure('TCombobox', fieldbackground='#FFFFFF', foreground=C.INK,
                        background=C.CARD, bordercolor=C.LINE, arrowcolor=C.INK_SOFT)

    # ── Panel/modules ile ortak widget yardımcıları (baglanti.py/logger.py'nin
    # düz-tk + ince-kenarlık deseninin birebir kopyası) ──
    def _card(self, parent, col, legend, title):
        """İki sütunlu kart (Paylaş/Al) — modules/baglanti.py:_card ile birebir."""
        holder = tk.Frame(parent, bg=C.BG)
        holder.grid(row=0, column=col, sticky='nsew', padx=(0, 9) if col == 0 else (9, 0))
        card = tk.Frame(holder, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill='both', expand=True)
        inner = tk.Frame(card, bg=C.CARD)
        inner.pack(fill='both', expand=True, padx=20, pady=18)
        tk.Label(inner, text=legend, bg=C.SAGE_TINT, fg=C.SAGE_DK,
                 font=self.fonts["small"], padx=9, pady=2).pack(anchor='w')
        tk.Label(inner, text=title, bg=C.CARD, fg=C.INK,
                 font=self.fonts["card_t"]).pack(anchor='w', pady=(10, 14))
        actions = tk.Frame(inner, bg=C.CARD)
        actions.pack(side='bottom', fill='x')
        body = tk.Frame(inner, bg=C.CARD)
        body.pack(side='top', fill='both', expand=True)
        return body, actions

    def _simple_card(self, parent, title=None, pady=(0, 12)):
        """Tek sütunlu, ince kenarlıklı kart (Kullanıcılar sekmesi bölümleri)."""
        card = tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill='x', pady=pady)
        inner = tk.Frame(card, bg=C.CARD)
        inner.pack(fill='both', expand=True, padx=20, pady=16)
        if title:
            tk.Label(inner, text=title, bg=C.CARD, fg=C.INK,
                     font=self.fonts["card_t"]).pack(anchor='w', pady=(0, 12))
        return inner

    def _section_head(self, parent, title, bg=C.CARD):
        """Kart içi alt bölüm başlığı: ince ayraç + kalın küçük başlık (web'deki
        .section-head/.block-title ile birebir — iç içe kart yerine sade ayraç)."""
        tk.Frame(parent, bg=C.LINE, height=1).pack(fill='x', pady=(14, 10))
        tk.Label(parent, text=title, bg=bg, fg=C.INK,
                 font=self.fonts["card_d"]).pack(anchor='w', pady=(0, 8))

    def _flat_entry(self, parent, var, show=None, width=None, bg=C.CARD):
        """Düz, ince kenarlıklı giriş kutusu — modules/baglanti.py:_entry ile birebir."""
        kw = {"width": width} if width is not None else {}
        e = tk.Entry(parent, textvariable=var, show=(show or ""), bg="#FFFFFF", fg=C.INK,
                     relief="flat", insertbackground=C.INK, font=self.fonts["body"],
                     highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE,
                     disabledbackground=C.BG, disabledforeground=C.INK_FAINT, **kw)
        self._enable_clipboard(e)
        return e

    def _entry(self, parent, label, var, show=None, bg=C.CARD):
        """Etiketli giriş alanı (etiket üstte + düz kutu altta)."""
        tk.Label(parent, text=label, bg=bg, fg=C.INK_SOFT,
                 font=self.fonts["small"]).pack(anchor='w', pady=(0, 4))
        e = self._flat_entry(parent, var, show=show, bg=bg)
        e.pack(fill='x', ipady=6, pady=(0, 13))
        return e

    def _check(self, parent, text, var, bg=C.CARD, command=None, wraplength=300):
        """Düz onay kutusu — panel.py'nin 'Bilgilerimi hatırla' kutusuyla birebir."""
        return tk.Checkbutton(
            parent, text=text, variable=var, bg=bg, fg=C.INK_SOFT,
            activebackground=bg, activeforeground=C.INK_SOFT, selectcolor=bg,
            font=self.fonts["small"], bd=0, highlightthickness=0, wraplength=wraplength,
            anchor='w', justify='left', cursor="hand2", command=command)

    def _enable_clipboard(self, entry):
        """Bir Entry'ye düzen-BAĞIMSIZ Ctrl+C/V/X/A + sağ-tık menüsü (Kes/Kopyala/
        Yapıştır/Tümünü Seç) ekler. Türkçe klavye düzeninde tkinter'ın varsayılan
        kısayolları çalışmaz (Tk keysym'i düzene göre eşler). Bu yüzden Windows sanal
        tuş KODUYLA bağlarız — keycode klavye düzeninden bağımsızdır: V=86, C=67, X=88, A=65."""
        def select_all():
            entry.select_range(0, "end")
            entry.icursor("end")
            return "break"

        def on_ctrl(event):
            kc = event.keycode
            if kc == 86:                      # V → Yapıştır
                entry.event_generate("<<Paste>>"); return "break"
            if kc == 67:                      # C → Kopyala
                entry.event_generate("<<Copy>>"); return "break"
            if kc == 88:                      # X → Kes
                entry.event_generate("<<Cut>>"); return "break"
            if kc == 65:                      # A → Tümünü Seç
                return select_all()
            return None
        entry.bind("<Control-KeyPress>", on_ctrl)

        menu = tk.Menu(entry, tearoff=0)
        menu.add_command(label="Kes", command=lambda: entry.event_generate("<<Cut>>"))
        menu.add_command(label="Kopyala", command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_command(label="Yapıştır", command=lambda: entry.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Tümünü Seç", command=select_all)

        def popup(event):
            entry.focus_set()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        entry.bind("<Button-3>", popup)

    # ── Giriş Ekranı ──
    # panel.py:_show_login ile BİREBİR aynı kart: canvas üzerine çizilmiş gölgeli +
    # yuvarlak köşeli (radius 16) kart, içeriğe göre kendi yüksekliğini ayarlar
    # (PIN satırı gizlenip gösterildiğinde kart da buna göre küçülür/büyür).
    LOGIN_CARD_W = 412
    LOGIN_CARD_MARGIN = 26

    def _build_rounded_card(self, parent, on_resize=None):
        """İçeriğe göre kendini boyutlandıran, gölgeli+yuvarlak köşeli kart. Panel.py'nin
        login kartıyla (round_rect 8/8 gölge + 6/6 gövde, radius 16) birebir aynıdır.
        on_resize(h) verilirse, kart yüksekliği her değiştiğinde (PIN satırı aç/kapa,
        durum metni büyümesi vb.) çağrılır — çerçevesiz giriş penceresini içeriğe göre
        yeniden boyutlandırmak için kullanılır."""
        w = self.LOGIN_CARD_W
        m = self.LOGIN_CARD_MARGIN
        outer = tk.Frame(parent, bg=C.BG)
        canvas = tk.Canvas(outer, bg=C.BG, highlightthickness=0, bd=0, width=w, height=1)
        canvas.pack()
        inner = tk.Frame(canvas, bg=C.CARD)
        win_id = canvas.create_window(m, m, window=inner, anchor='nw', width=w - 2 * m)

        def redraw(_e=None):
            inner.update_idletasks()
            h = inner.winfo_reqheight() + 2 * m
            canvas.configure(height=h)
            canvas.delete("bg")
            round_rect(canvas, 8, 8, w - 8, h - 8, 16, fill=C.SHADOW, outline="", tags="bg")
            round_rect(canvas, 6, 6, w - 10, h - 10, 16, fill=C.CARD, outline=C.CARD_EDGE, tags="bg")
            canvas.tag_lower("bg")
            if on_resize:
                on_resize(h)
        inner.bind("<Configure>", redraw)
        canvas.after(10, redraw)
        return outer, inner

    def show_login_screen(self):
        # Otomatik giriş yalnız uygulamanın İLK açılışında denenir; 'Çıkış Yap' ile
        # bilerek çıkan kullanıcıyı tekrar içeri sokmaz.
        first_show = not getattr(self, "_login_screen_shown", False)
        self._login_screen_shown = True
        for widget in self.container.winfo_children():
            widget.destroy()

        # Giriş ekranı panel.py'deki gibi ÇERÇEVESİZ, yalnızca kart görünen bir
        # pencere olarak gösterilir (masaüstü uygulama şeklinde olağan başlık
        # çubuğu/boş gri alan yerine). Kart yüksekliği içeriğe göre değiştiği için
        # (PIN satırı aç/kapa, durum metni) pencere _on_login_card_resize ile
        # yeniden boyutlanır. minsize SIFIRLANIR: dashboard'un bıraktığı
        # 760x560 tabanı burada aktif kalırsa (ör. çıkış yapıp tekrar giriş
        # ekranına dönünce) kart küçük pencereye asla sığmaz.
        self._login_positioned = False
        self.root.minsize(1, 1)
        self.root.overrideredirect(True)
        try:
            self.root.wm_attributes("-transparentcolor", "#123456")
        except Exception:
            pass
        self.root.configure(bg="#123456")
        try:
            self._set_taskbar_icon()
        except Exception:
            pass

        login_frame = tk.Frame(self.container, bg="#123456")
        login_frame.pack(fill='both', expand=True)

        outer, card = self._build_rounded_card(login_frame, on_resize=self._on_login_card_resize)
        outer.place(relx=0.5, rely=0.5, anchor='center')

        # Sağ üstte kapatma düğmesi (başlık çubuğu olmadığı için gerekli)
        close_btn = tk.Label(card, text="×", bg=C.CARD, fg=C.INK_SOFT,
                             font=tkfont.Font(family="Segoe UI", size=14), cursor="hand2")
        close_btn.place(relx=1.0, rely=0.0, x=0, y=-5, anchor="ne")
        close_btn.bind("<Button-1>", lambda e: self.on_close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=C.CLAY))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=C.INK_SOFT))

        # Marka satırı: yuvarlak rozet + başlık (panel.py'nin "U" rozetiyle birebir)
        top = tk.Frame(card, bg=C.CARD)
        top.pack(anchor='w')
        mk = tk.Canvas(top, width=34, height=34, bg=C.CARD, highlightthickness=0)
        round_rect(mk, 1, 1, 33, 33, 9, fill=C.SAGE, outline="")
        mk.create_text(17, 17, text="Ç", fill="#FFFFFF", font=self.fonts["card_t"])
        mk.pack(side='left')
        tk.Label(top, text="Çoklu UYAP", bg=C.CARD, fg=C.INK,
                 font=self.fonts["card_t"]).pack(side='left', padx=11)
        tk.Label(card, text="Hesabınızla giriş yapın", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["body"]).pack(anchor='w', pady=(12, 20))

        # TEK alanlı giriş: "kullanıcıadı@ofisadı" (ör. ahmet@kemalburo). Eski ayar
        # dosyasında ayrı username+office_code varsa birleştirilip alana yazılır.
        saved_user = (self.cfg.get("username", "") or "").strip()
        saved_office = (self.cfg.get("office_code", "") or "").strip()
        if saved_user and "@" not in saved_user and saved_office:
            saved_user = f"{saved_user}@{saved_office}"
        self.login_var = tk.StringVar(value=saved_user)
        self.login_entry = self._entry(card, "Kullanıcı adı (kullanıcıadı@ofisadı)", self.login_var)

        saved_pass = ""
        if self.cfg.get("remember") and self.cfg.get("password_enc"):
            saved_pass = decrypt_secret(self.cfg.get("password_enc"))
        self.pass_var = tk.StringVar(value=saved_pass)
        self.pass_entry = self._entry(card, "Parola", self.pass_var, show="•")

        self.remember_var = tk.BooleanVar(value=bool(self.cfg.get("remember", True)))
        self.remember_cb = self._check(card, "Bilgilerimi hatırla", self.remember_var)
        self.remember_cb.pack(anchor='w', pady=(0, 6))

        # Kur-unut: işaretliyken giriş, paylaşımı da kendiliğinden başlatır (ofis sunucusu).
        # Bağlantı ALACAK üye işareti kaldırır (uzak üyeler zaten tarayıcıdan giriyor).
        self.auto_share_var = tk.BooleanVar(value=bool(self.cfg.get("auto_share", True)))
        self.auto_share_cb = self._check(
            card, "Bu bilgisayar ofis sunucusu olsun\n(girişte paylaşım kendiliğinden başlar)",
            self.auto_share_var, command=self._toggle_login_pin_row)
        self.auto_share_cb.pack(anchor='w', pady=(0, 6))

        # E-imza PIN'i giriş kartında sorulur ki ilk kurulum TEK ekranda bitsin;
        # yalnız 'ofis sunucusu' işaretliyken görünür. Ayrı bir Frame'e sarılır ki
        # pack/pack_forget kartın geri kalanının sırasını bozmasın.
        self.login_pin_row = tk.Frame(card, bg=C.CARD)
        saved_pin = decrypt_secret(self.cfg.get("pin_enc", "")) if self.cfg.get("pin_enc") else ""
        self.login_pin_var = tk.StringVar(value=saved_pin)
        self.login_pin_entry = self._entry(self.login_pin_row, "E-imza PIN kodu",
                                           self.login_pin_var, show="•")
        self._toggle_login_pin_row()

        self.login_btn = RoundButton(card, "Giriş Yap", command=self.on_login_click,
                                     kind="primary", font=self.fonts["card_t"], height=42)
        self.login_btn.pack(fill='x')

        forgot = tk.Label(card, text="Parolamı unuttum", bg=C.CARD, fg=C.SAGE_DK,
                          cursor="hand2", font=('Segoe UI', 9, 'underline'))
        forgot.pack(pady=(14, 0))
        forgot.bind("<Button-1>", lambda e: self.on_forgot_password())

        self.login_status_lbl = tk.Label(card, text="", bg=C.CARD, fg=C.INK_SOFT,
                                         font=self.fonts["small"], wraplength=320, justify='center')
        self.login_status_lbl.pack(pady=(10, 0))

        # Kur-unut otomatik girişi: kayıtlı kimlik + PIN tamsa ve 'ofis sunucusu'
        # işaretliyse 3 sn sonra kendiliğinden giriş yapılır (giriş, paylaşımı başlatır).
        # Böylece bilgisayar açılışında (Başlangıç kısayolu) hiç tıklama gerekmez.
        if (first_show and self.cfg.get("auto_share", True) and self.cfg.get("remember")
                and "@" in self.login_var.get()
                and self.pass_var.get() and self.login_pin_var.get().strip()):
            self.login_status_lbl.configure(
                text="Otomatik giriş 3 saniye içinde başlıyor… (durdurmak için bir alana tıklayın)",
                fg=C.SAGE_DK)
            self._auto_login_after = self.root.after(3000, self._auto_login_fire)
            for w in (self.login_entry, self.pass_entry, self.login_pin_entry):
                w.bind("<Button-1>", self._cancel_auto_login, add="+")
                w.bind("<Key>", self._cancel_auto_login, add="+")

        # Başlık çubuğu olmadığı için pencere, kartın boş alanlarından tutulup
        # sürüklenebilir olmalı (panel.py'nin _bind_drag_recursive'i ile birebir).
        self._bind_drag_recursive(login_frame)

    def _on_login_card_resize(self, h):
        """Giriş kartı yüksekliği değiştiğinde (PIN satırı, durum metni vb.)
        çerçevesiz pencereyi içeriğe göre yeniden boyutlandırır. İlk gösterimde
        ekranda ortalanır; sonraki boyut değişikliklerinde mevcut konum korunur."""
        w = self.LOGIN_CARD_W
        if not self._login_positioned:
            self._center(w, h)
            self._login_positioned = True
        else:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ── pencereyi ortala (panel.py:_center ile birebir) ──
    def _center(self, w, h):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ── başlıksız pencereyi sürüklemek için (panel.py:_bind_drag_recursive ile birebir) ──
    def _bind_drag_recursive(self, widget):
        try:
            if isinstance(widget, (tk.Entry, RoundButton)):
                return
            if widget.cget("cursor") == "hand2":
                return
        except Exception:
            pass
        widget.bind("<Button-1>", self._on_drag_start, add="+")
        widget.bind("<B1-Motion>", self._on_drag_motion, add="+")
        for child in widget.winfo_children():
            self._bind_drag_recursive(child)

    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event):
        x = self.root.winfo_x() - self._drag_x + event.x
        y = self.root.winfo_y() - self._drag_y + event.y
        self.root.geometry(f"+{x}+{y}")

    # ── görev çubuğu simgesi (panel.py:_set_taskbar_icon ile birebir) ──
    def _set_taskbar_icon(self):
        if not IS_WINDOWS or self._setting_taskbar_icon:
            return
        self._setting_taskbar_icon = True
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if hwnd:
                if hasattr(ctypes.windll.user32, "GetWindowLongPtrW"):
                    get_window_long = ctypes.windll.user32.GetWindowLongPtrW
                    set_window_long = ctypes.windll.user32.SetWindowLongPtrW
                else:
                    get_window_long = ctypes.windll.user32.GetWindowLongW
                    set_window_long = ctypes.windll.user32.SetWindowLongW

                style = get_window_long(hwnd, -20)  # GWL_EXSTYLE
                style = (style & ~0x00000080) | 0x00040000  # ~WS_EX_TOOLWINDOW | WS_EX_APPWINDOW
                set_window_long(hwnd, -20, style)

                self.root.withdraw()
                self.root.deiconify()
        except Exception:
            pass
        finally:
            self._setting_taskbar_icon = False

    def _toggle_login_pin_row(self):
        """Giriş kartındaki PIN satırını 'ofis sunucusu' işaretine göre göster/gizle."""
        if self.auto_share_var.get():
            kw = {}
            try:
                if hasattr(self, "login_btn") and self.login_btn.winfo_exists():
                    kw["before"] = self.login_btn
            except Exception:
                pass
            self.login_pin_row.pack(fill='x', **kw)
        else:
            self.login_pin_row.pack_forget()

    def _auto_login_fire(self):
        self._auto_login_after = None
        if not self.login_in_progress:
            self.on_login_click()

    def _cancel_auto_login(self, _event=None):
        if getattr(self, "_auto_login_after", None):
            self.root.after_cancel(self._auto_login_after)
            self._auto_login_after = None
            self.login_status_lbl.configure(text="Otomatik giriş durduruldu.", foreground=C.INK_SOFT)

    def on_forgot_password(self):
        """Parola sıfırlama talebi: kullanıcıadı@ofisadı ya da e-posta sorar, /api/reset'i
        çağırır. Bağlantı, ofis politikasına göre master'a ya da kullanıcıya e-postalanır."""
        win = tk.Toplevel(self.root)
        win.title("Parola Sıfırlama")
        win.configure(bg=C.CARD)
        win.geometry("420x260")
        win.transient(self.root)
        tk.Label(win, text="Parola Sıfırlama", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.fonts["card_t"]).pack(pady=(14, 6))
        tk.Label(win, text="Kullanıcı adınızı (kullanıcıadı@ofisadı) ya da e-postanızı girin. "
                 "Sıfırlama bağlantısı, ofis ayarına göre size ya da büronuzun master "
                 "kullanıcısına e-posta ile gönderilir.",
                 bg=C.CARD, fg=C.INK, font=self.fonts["body"],
                 wraplength=380, justify='left').pack(pady=(0, 10), padx=16)
        var = tk.StringVar(value=self.login_var.get().strip() if hasattr(self, "login_var") else "")
        ent = self._flat_entry(win, var, width=36)
        ent.pack(pady=4, padx=16, ipady=6, fill='x')
        status = tk.Label(win, text="", bg=C.CARD, fg=C.INK_SOFT,
                          font=self.fonts["small"], wraplength=380, justify='center')
        status.pack(pady=4)

        def send():
            ident = var.get().strip()
            if not ident:
                status.configure(text="Kullanıcı adı veya e-posta girin.", fg=C.CLAY)
                return
            status.configure(text="Gönderiliyor…", fg=C.SAGE_DK)

            def worker():
                data, err = self._office_reset_request(ident)
                def show():
                    if err:
                        status.configure(text=err, fg=C.CLAY)
                    else:
                        extra = " (TEST modu: bağlantı sunucu günlüğüne yazıldı)" if data.get("test_mode") else ""
                        status.configure(text=f"Sıfırlama bağlantısı {data.get('sent_to','')} adresine gönderildi.{extra}",
                                         fg=C.SAGE_DK)
                self.root.after(0, show)
            threading.Thread(target=worker, daemon=True).start()

        btn = RoundButton(win, "Sıfırlama Bağlantısı Gönder", command=send,
                          kind="primary", font=self.fonts["card_d"], height=34)
        btn.configure(bg=C.CARD)
        btn.pack(pady=12, padx=16, fill='x')

    def _office_reset_request(self, identifier, office_code=""):
        """/api/reset'e (kimlik gerektirmez) sıfırlama talebi yollar. (data, error) döndürür.
        identifier 'kullanıcı@ofis' ya da e-postadır; sunucu _login_id ikisini de çözer."""
        body = json.dumps({"identifier": identifier, "office_code": office_code}).encode("utf-8")
        req = urllib.request.Request(self._api_base() + "/api/reset", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            try:
                return None, json.loads(e.read().decode("utf-8")).get("error", str(e))
            except Exception:
                return None, f"Sunucu hatası ({e.code})."
        except urllib.error.URLError as e:
            return None, f"Sunucuya ulaşılamadı: {e.reason}"
        except Exception as e:
            return None, str(e)

    def _parse_login_id(self):
        """Tek alanlı girişi (kullanıcıadı@ofisadı) çözer → (kullanıcı, ofis) ya da
        biçim yanlışsa (None, None). Son '@' esas alınır; sunucu tarafı ofis adını
        zaten slug'lar (büyük/küçük harf, boşluk toleranslı)."""
        raw = self.login_var.get().strip()
        if "@" not in raw:
            return None, None
        user, office = raw.rsplit("@", 1)
        user, office = user.strip(), office.strip()
        if not user or not office:
            return None, None
        return user, office

    def on_login_click(self):
        if websockets is None:
            self.login_status_lbl.configure(text="websockets kitaplığı kurulu değil.", foreground=C.CLAY)
            return
        url = self.server_url
        user, office = self._parse_login_id()
        pw = self.pass_var.get().strip()

        if user is None:
            self.login_status_lbl.configure(
                text="Kullanıcı adını kullanıcıadı@ofisadı biçiminde girin (ör. ahmet@kemalburo).",
                foreground=C.CLAY)
            return
        if not pw:
            self.login_status_lbl.configure(text="Lütfen tüm alanları doldurun.", foreground=C.CLAY)
            return

        self.login_in_progress = True
        self.disable_login_inputs()
        self.login_status_lbl.configure(text="Sunucuya bağlanılıyor, bilgiler doğrulanıyor...", foreground=C.SAGE_DK)

        t = threading.Thread(target=verify_credentials_thread,
                             args=(url, user, pw, self.login_queue, office), daemon=True)
        t.start()
        self.root.after(100, self.check_login_result)

    def check_login_result(self):
        try:
            success, message, info = self.login_queue.get_nowait()
            self.login_in_progress = False
            self.enable_login_inputs()

            if success:
                user, office = self._parse_login_id()
                self.username = user or ""
                self.password = self.pass_var.get().strip()
                self.office_code = office or ""
                self.role = (info or {}).get("role", "member")
                self.office_label = (info or {}).get("office_label", "")
                self.save_login_config()
                login_pin = self.login_pin_var.get().strip()
                self.show_dashboard()
                # Giriş kartında yazılan PIN paylaşım kartına taşınır (aynı PIN'dir);
                # ardından kur-unut kipi paylaşımı kendiliğinden başlatır.
                if login_pin:
                    self.pin_var.set(login_pin)
                self._maybe_auto_share()
            else:
                self.login_status_lbl.configure(text=f"Giriş Başarısız: {message}", foreground=C.CLAY)
        except queue.Empty:
            if self.login_in_progress:
                self.root.after(100, self.check_login_result)

    def disable_login_inputs(self):
        self.login_entry.configure(state='disabled')
        self.pass_entry.configure(state='disabled')
        self.remember_cb.configure(state='disabled')
        self.login_btn.configure(state='disabled')

    def enable_login_inputs(self):
        self.login_entry.configure(state='normal')
        self.pass_entry.configure(state='normal')
        self.remember_cb.configure(state='normal')
        self.login_btn.configure(state='normal')

    def save_login_config(self):
        self.cfg["server_url"] = self.server_url
        self.cfg["office_code"] = self.office_code
        self.cfg["username"] = self.username
        self.cfg["remember"] = self.remember_var.get()
        if self.remember_var.get():
            self.cfg["password_enc"] = encrypt_secret(self.password)
        else:
            self.cfg["password_enc"] = ""
        self.cfg["auto_share"] = bool(self.auto_share_var.get())
        # Giriş kartında PIN yazıldıysa (ofis sunucusu kipi) DPAPI ile saklanır ki
        # sonraki açılışlarda otomatik giriş + otomatik paylaşım tek başına yürüsün.
        pin = self.login_pin_var.get().strip()
        if pin and self.remember_var.get():
            self.cfg["pin_enc"] = encrypt_secret(pin)
        save_config(self.cfg)

    def _maybe_auto_share(self):
        """Kur-unut: 'ofis sunucusu' işaretliyse giriş, paylaşımı da kendiliğinden
        başlatır. Bağlantı mantığı yenilenmez; mevcut start_sharing aynen çağrılır."""
        if not self.cfg.get("auto_share") or self.is_sharing:
            return
        if not self.pin_var.get().strip():
            self.log_queue.put("[SİSTEM] Otomatik paylaşım atlandı: E-imza PIN'i boş. PIN'i girip "
                               "'Bağlantıyı Paylaş'a basın; sonraki girişlerde kendiliğinden başlar.\n")
            return
        self.log_queue.put("[SİSTEM] Ofis sunucusu kipi: paylaşım kendiliğinden başlatılıyor…\n")
        sync_startup_shortcut(True, self.log_queue.put)
        # Panel çizimi otursun diye kısa gecikmeyle; kullanıcı hiçbir şeye basmaz.
        self.root.after(300, self.start_sharing)

    # ── Kontrol Paneli ──
    def show_dashboard(self):
        for widget in self.container.winfo_children():
            widget.destroy()

        # Giriş ekranının çerçevesiz kartından çıkıp olağan (başlık çubuklu)
        # pencereye dönülür — web sitesi temasındaki gibi soldan gezinmeli kabuk.
        self._login_positioned = False
        self.root.overrideredirect(False)
        self.root.configure(bg=C.BG)
        self.root.minsize(760, 560)
        width, height = 900, 640
        self.root.geometry(f"{width}x{height}")
        self._center(width, height)
        try:
            self._set_taskbar_icon()
        except Exception:
            pass

        dash_frame = tk.Frame(self.container, bg=C.BG)
        dash_frame.pack(fill='both', expand=True)

        header_frame = tk.Frame(dash_frame, bg=C.BG)
        header_frame.pack(fill='x', padx=28, pady=(20, 16))
        kimlik = f"{self.username}@{self.office_code}" if self.office_code else self.username
        tk.Label(header_frame, text=f"Kullanıcı: {kimlik}", bg=C.BG, fg=C.INK,
                 font=self.fonts["h1"]).pack(side='left', anchor='center')
        cikis_btn = RoundButton(header_frame, "Çıkış Yap", command=self.on_logout_click,
                                kind="ghost", font=self.fonts["card_d"], height=32)
        cikis_btn.pack(side='right')

        tk.Frame(dash_frame, bg=C.LINE, height=1).pack(fill='x')

        # Gövde: sol gezinme menüsü + içerik (web sitesi temasındaki gibi;
        # sekme yerine soldaki menü ile geçiş yapılır).
        body = tk.Frame(dash_frame, bg=C.BG)
        body.pack(fill='both', expand=True)

        side = tk.Frame(body, bg=C.SIDEBAR, width=190)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        tk.Frame(body, bg=C.LINE, width=1).pack(side='left', fill='y')

        content = tk.Frame(body, bg=C.BG)
        content.pack(side='left', fill='both', expand=True)

        conn_tab = tk.Frame(content, bg=C.BG)
        users_tab = tk.Frame(content, bg=C.BG)
        for f in (conn_tab, users_tab):
            f.place(x=0, y=0, relwidth=1, relheight=1)
        self._dash_frames = {"baglanti": conn_tab, "kullanicilar": users_tab}

        self._nav_buttons = {}
        self._current_nav_key = None
        tk.Frame(side, bg=C.SIDEBAR, height=14).pack(fill='x')
        for key, text in (("baglanti", "Bağlantı"), ("kullanicilar", "Kullanıcılar")):
            self._nav_buttons[key] = self._build_nav_item(side, key, text)

        self.setup_users_tab(users_tab)

        conn_wrap = tk.Frame(conn_tab, bg=C.BG)
        conn_wrap.pack(fill='both', expand=True, padx=14, pady=14)

        cards = tk.Frame(conn_wrap, bg=C.BG)
        cards.pack(fill='x')
        cards.grid_columnconfigure(0, weight=1, uniform="c")
        cards.grid_columnconfigure(1, weight=1, uniform="c")

        share_body, share_actions = self._card(cards, 0, "OFİS", "Bağlantı Paylaş")
        self.setup_share_section(share_body, share_actions)

        recv_body, recv_actions = self._card(cards, 1, "İSTEMCİ", "Bağlantı Al")
        self.setup_receive_section(recv_body, recv_actions)

        log_card = tk.Frame(conn_wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                            highlightthickness=1)
        log_card.pack(fill='both', expand=True, pady=(20, 0))
        log_inner = tk.Frame(log_card, bg=C.CARD)
        log_inner.pack(fill='both', expand=True, padx=16, pady=14)

        log_header = tk.Frame(log_inner, bg=C.CARD)
        log_header.pack(fill='x')
        tk.Label(log_header, text="İşlem Günlüğü", bg=C.CARD, fg=C.INK,
                 font=self.fonts["card_t"]).pack(side='left')
        temizle_btn = RoundButton(log_header, "Temizle", command=self.clear_logs,
                                  kind="ghost", font=self.fonts["small"], height=26, pad=12)
        temizle_btn.pack(side='right')

        self.log_widget = scrolledtext.ScrolledText(
            log_inner, bg="#FBFAF7", fg=C.INK, insertbackground=C.SAGE_DK,
            selectbackground=C.SAGE_TINT, selectforeground=C.INK, font=('Consolas', 10),
            relief='flat', borderwidth=0, highlightthickness=1, highlightbackground=C.CARD_EDGE,
            highlightcolor=C.SAGE)
        self.log_widget.pack(fill='both', expand=True, pady=(10, 0))
        self.log_widget.configure(state='disabled')

        self._select_nav("baglanti")
        self.log_queue.put("[SİSTEM] Bulut hesabı üzerinden giriş yapıldı.\n")

    # ── sol gezinme menüsü (sekme yerine — web sitesi temasıyla birebir) ──
    def _build_nav_item(self, parent, key, text):
        lbl = tk.Label(parent, text=text, bg=C.SIDEBAR, fg=C.INK_SOFT,
                       font=self.fonts["nav"], anchor='w', padx=20, pady=10,
                       cursor="hand2")
        lbl.pack(fill='x')
        lbl.bind("<Button-1>", lambda e, k=key: self._select_nav(k))
        lbl.bind("<Enter>", lambda e, k=key: (lbl.config(bg=C.LINE)
                                              if self._current_nav_key != k else None))
        lbl.bind("<Leave>", lambda e, k=key: (lbl.config(bg=C.SIDEBAR)
                                              if self._current_nav_key != k else None))
        return lbl

    def _select_nav(self, key):
        self._current_nav_key = key
        for k, lbl in self._nav_buttons.items():
            if k == key:
                lbl.config(bg=C.SAGE_TINT, fg=C.INK, font=self.fonts["nav_b"])
            else:
                lbl.config(bg=C.SIDEBAR, fg=C.INK_SOFT, font=self.fonts["nav"])
        frame = self._dash_frames.get(key)
        if frame:
            frame.tkraise()

    def setup_share_section(self, body, actions):
        saved_pin = ""
        if self.cfg.get("pin_enc"):
            saved_pin = decrypt_secret(self.cfg.get("pin_enc"))
        self.pin_var = tk.StringVar(value=saved_pin)
        self.pin_entry = self._entry(body, "E-imza PIN kodu", self.pin_var, show="•")

        self.cert_var = tk.StringVar(value=self.cfg.get("cert_id", ""))
        self.cert_entry = self._entry(body, "Sertifika ID (opsiyonel)", self.cert_var)

        # LAN-direct: işaretliyse proxy (8800) tüm yerel ağa açılır ve aynı ağdaki istemciler
        # sunucuya/WebRTC'ye hiç çıkmadan doğrudan bağlanır. Kapalıyken (varsayılan) 8800
        # yalnızca bu makineden erişilir; istemciler dış-ağ yolunu (P2P/relay) kullanır.
        self.lan_share_var = tk.BooleanVar(value=bool(self.cfg.get("lan_share")))
        self.lan_share_cb = self._check(
            body, "Yerel ağdan doğrudan erişim (aynı ofis ağındaki bilgisayarlar)",
            self.lan_share_var)
        self.lan_share_cb.pack(anchor='w', pady=(0, 6))

        # Kur-unut anahtarı: girişte ve bilgisayar açılışında paylaşım kendiliğinden
        # başlasın (Başlangıç kısayolu da buna göre kurulur/sökülür).
        self.auto_share_dash_var = tk.BooleanVar(value=bool(self.cfg.get("auto_share", True)))
        self.auto_share_dash_cb = self._check(
            body, "Girişte ve bilgisayar açılışında otomatik paylaş",
            self.auto_share_dash_var, command=self.on_auto_share_toggle)
        self.auto_share_dash_cb.pack(anchor='w', pady=(0, 4))

        self.share_btn = RoundButton(actions, "Bağlantıyı Paylaş", command=self.on_share_click,
                                     kind="primary", font=self.fonts["nav_b"], height=38)
        self.share_btn.pack(fill='x', pady=(2, 6))
        # Dağıtan makine de kendi oturumunu kullanabilsin: paylaşım açıkken bu düğme
        # http://127.0.0.1:8800/giris açar (UYAP'a aynı e-imza oturumundan girilir).
        self.share_open_browser_btn = RoundButton(actions, "Tarayıcıyı Aç (Kendim Gir)",
                                                  command=self.on_open_browser_click,
                                                  kind="ghost", font=self.fonts["nav_b"], height=36)
        self.share_open_browser_btn.set_state('disabled')
        self.share_open_browser_btn.pack(fill='x', pady=(0, 6))
        self.share_status_lbl = tk.Label(actions, text="● Paylaşım durduruldu", bg=C.CARD,
                                         fg=C.INK_FAINT, font=self.fonts["small"])
        self.share_status_lbl.pack(anchor='w', pady=(6, 0))

    def setup_receive_section(self, body, actions):
        tk.Label(body, text="Ofise bağlanmak için kullanın; yerel ya da dış ağ otomatik seçilir.",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.fonts["body"],
                 wraplength=300, justify='left').pack(anchor='w', pady=(0, 16))

        self.receive_btn = RoundButton(actions, "Bağlantıyı Al", command=self.on_receive_click,
                                       kind="primary", font=self.fonts["nav_b"], height=38)
        self.receive_btn.pack(fill='x', pady=(2, 6))
        self.open_browser_btn = RoundButton(actions, "Tarayıcıyı Aç", command=self.on_open_browser_click,
                                            kind="ghost", font=self.fonts["nav_b"], height=36)
        self.open_browser_btn.set_state('disabled')
        self.open_browser_btn.pack(fill='x', pady=(0, 6))
        self.receive_status_lbl = tk.Label(actions, text="● Bağlantı yok", bg=C.CARD,
                                           fg=C.INK_FAINT, font=self.fonts["small"])
        self.receive_status_lbl.pack(anchor='w', pady=(6, 0))

    # ── Kullanıcılar Sekmesi (master self-servis: vendor /api/office) ──
    def _api_base(self):
        """server_url (wss://host/ws) → http(s) taban (https://host). API çağrıları için."""
        u = (self.server_url or "").strip()
        if u.startswith("wss://"):
            u = "https://" + u[len("wss://"):]
        elif u.startswith("ws://"):
            u = "http://" + u[len("ws://"):]
        if u.endswith("/ws"):
            u = u[:-3]
        return u.rstrip("/")

    def _office_api(self, action, **params):
        """Senkron JSON API çağrısı (ARKA PLAN thread'inde çağırın). (data, error) döndürür."""
        body = {"username": self.username, "password": self.password, "action": action,
                "office_code": self.office_code}
        body.update(params)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self._api_base() + "/api/office", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error", str(e))
            except Exception:
                msg = f"Sunucu hatası ({e.code})."
            return None, msg
        except urllib.error.URLError as e:
            return None, f"Sunucuya ulaşılamadı: {e.reason}"
        except Exception as e:
            return None, str(e)

    def _run_api(self, action, on_done, **params):
        """API çağrısını arka planda yapıp sonucu UI thread'inde on_done(data, err)'e verir."""
        def worker():
            data, err = self._office_api(action, **params)
            self.root.after(0, lambda: on_done(data, err))
        threading.Thread(target=worker, daemon=True).start()

    def setup_users_tab(self, parent):
        self._master_widgets = []  # yalnızca master kullanabilsin diye etkin/pasif edilenler
        wrap = tk.Frame(parent, bg=C.BG)
        wrap.pack(fill='both', expand=True, padx=14, pady=14)

        self.users_role_lbl = tk.Label(wrap, text="Rol kontrol ediliyor…", bg=C.BG, fg=C.INK_SOFT,
                                       wraplength=760, justify='left', font=self.fonts["small"])
        self.users_role_lbl.pack(anchor='w', pady=(0, 12))

        # ── Kendi parolanı değiştir (HER kullanıcı — üye de master da) ──
        pw_card = self._simple_card(wrap, "Kendi Parolamı Değiştir")
        prow = tk.Frame(pw_card, bg=C.CARD); prow.pack(fill='x')
        tk.Label(prow, text="Yeni parola:", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).pack(side='left', padx=(0, 8))
        self.own_pass_var = tk.StringVar()
        e4 = self._flat_entry(prow, self.own_pass_var, show="•", width=24)
        e4.pack(side='left', padx=(0, 8), ipady=5)
        RoundButton(prow, "Güncelle", command=self.on_change_own_password,
                    kind="primary", font=self.fonts["small"], height=30, pad=12).pack(side='left')

        # ── Ofis yönetimi (YALNIZCA master) ──
        mgmt = self._simple_card(wrap, "Ofis Yönetimi (yalnızca master)", pady=(0, 12))

        list_head = tk.Frame(mgmt, bg=C.CARD)
        list_head.pack(fill='x')
        tk.Label(list_head, text="Kullanıcılar", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).pack(side='left')
        RoundButton(list_head, "Yenile", command=self.refresh_users,
                    kind="ghost", font=self.fonts["small"], height=28, pad=12).pack(side='right')

        cols = ("user", "role", "label", "email", "status")
        self.users_tree = ttk.Treeview(mgmt, columns=cols, show='headings', height=6,
                                       style='Custom.Treeview')
        for c, t, w in (("user", "Kullanıcı", 150), ("role", "Rol", 70),
                        ("label", "Etiket", 150), ("email", "E-posta", 180), ("status", "Durum", 70)):
            self.users_tree.heading(c, text=t)
            self.users_tree.column(c, width=w, anchor='w')
        self.users_tree.pack(fill='x', pady=(8, 8))

        # Seçili kullanıcı eylemleri
        act = tk.Frame(mgmt, bg=C.CARD)
        act.pack(fill='x', pady=(0, 4))
        b_reset = RoundButton(act, "Parola Sıfırla", command=self.on_reset_user,
                              kind="ghost", font=self.fonts["small"], height=28, pad=12)
        b_reset.pack(side='left', padx=(0, 6))
        b_email = RoundButton(act, "E-posta Ata", command=self.on_set_user_email,
                              kind="ghost", font=self.fonts["small"], height=28, pad=12)
        b_email.pack(side='left', padx=(0, 6))
        b_toggle = RoundButton(act, "Pasifleştir / Aktifleştir", command=self.on_toggle_user,
                               kind="ghost", font=self.fonts["small"], height=28, pad=12)
        b_toggle.pack(side='left', padx=(0, 6))
        b_del = RoundButton(act, "Sil", command=self.on_delete_user,
                            kind="stop", font=self.fonts["small"], height=28, pad=12)
        b_del.pack(side='left')
        self._master_widgets += [b_reset, b_email, b_toggle, b_del]

        # Yeni kullanıcı ekleme
        self._section_head(mgmt, "Yeni Üye Ekle")
        row = tk.Frame(mgmt, bg=C.CARD); row.pack(fill='x')
        tk.Label(row, text="Kullanıcı adı:", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).grid(row=0, column=0, sticky='w', padx=2, pady=3)
        self.new_user_var = tk.StringVar()
        e1 = self._flat_entry(row, self.new_user_var, width=20)
        e1.grid(row=0, column=1, padx=2, pady=3, ipady=4)
        tk.Label(row, text="E-posta:", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).grid(row=0, column=2, sticky='w', padx=2, pady=3)
        self.new_email_var = tk.StringVar()
        e_em = self._flat_entry(row, self.new_email_var, width=20)
        e_em.grid(row=0, column=3, padx=2, pady=3, ipady=4)
        tk.Label(row, text="Parola (ops.):", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).grid(row=1, column=0, sticky='w', padx=2, pady=3)
        self.new_pass_var = tk.StringVar()
        e3 = self._flat_entry(row, self.new_pass_var, width=20)
        e3.grid(row=1, column=1, padx=2, pady=3, ipady=4)
        b_add = RoundButton(row, "Ekle", command=self.on_add_user,
                            kind="primary", font=self.fonts["small"], height=28, pad=12)
        b_add.grid(row=1, column=3, sticky='e', padx=2, pady=3)
        self._master_widgets += [e1, e_em, e3, b_add]

        # Ofis ayarları: kendi e-postam + parola sıfırlama politikası
        self._section_head(mgmt, "Ayarlar")
        srow = tk.Frame(mgmt, bg=C.CARD); srow.pack(fill='x')
        tk.Label(srow, text="Kendi e-postam:", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).grid(row=0, column=0, sticky='w', padx=2, pady=3)
        self.own_email_var = tk.StringVar()
        e_oe = self._flat_entry(srow, self.own_email_var, width=26)
        e_oe.grid(row=0, column=1, padx=2, pady=3, ipady=4)
        b_oe = RoundButton(srow, "Kaydet", command=self.on_save_own_email,
                           kind="ghost", font=self.fonts["small"], height=28, pad=12)
        b_oe.grid(row=0, column=2, padx=2, pady=3)
        tk.Label(srow, text="Sıfırlama maili:", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.fonts["small"]).grid(row=1, column=0, sticky='w', padx=2, pady=3)
        self.policy_var = tk.StringVar(value="Bana (master) gelsin")
        self.policy_combo = ttk.Combobox(srow, textvariable=self.policy_var, state='readonly', width=24,
                                         values=["Bana (master) gelsin", "Kullanıcının kendisine gitsin"])
        self.policy_combo.grid(row=1, column=1, padx=2, pady=3)
        b_pol = RoundButton(srow, "Kaydet", command=self.on_save_policy,
                            kind="ghost", font=self.fonts["small"], height=28, pad=12)
        b_pol.grid(row=1, column=2, padx=2, pady=3)
        self._master_widgets += [e_oe, b_oe, self.policy_combo, b_pol]

        self.users_status_lbl = tk.Label(wrap, text="", bg=C.BG, fg=C.INK_SOFT,
                                         font=self.fonts["small"], wraplength=760, justify='left')
        self.users_status_lbl.pack(anchor='w', pady=(4, 0))

        self._set_master_enabled(False)  # rol belli olana kadar yönetim kapalı
        self.refresh_users()  # sekme açılınca otomatik rol/liste yükle

    def _set_master_enabled(self, enabled):
        """Master'a özel yönetim widget'larını topluca etkin/pasif eder."""
        state = 'normal' if enabled else 'disabled'
        for w in getattr(self, "_master_widgets", []):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _set_users_status(self, text, ok=True):
        if hasattr(self, "users_status_lbl") and self.users_status_lbl.winfo_exists():
            self.users_status_lbl.configure(text=text, foreground=C.SAGE_DK if ok else C.CLAY)

    def _selected_username(self):
        sel = self.users_tree.selection()
        if not sel:
            return None
        return self.users_tree.item(sel[0], "values")[0]

    def refresh_users(self):
        self._set_users_status("Yükleniyor…", ok=True)

        def done(data, err):
            if err:
                # "master yetkisi gerekir" = üye hesabı: yönetim kapalı kalsın, kendi parolasını
                # değiştirebilir. Diğer hatalar (ağ vb.) kırmızı gösterilir.
                if "master" in err.lower():
                    self._set_master_enabled(False)
                    self.users_role_lbl.configure(
                        text="Üye hesabı: yalnızca kendi parolanızı değiştirebilirsiniz. "
                             "Üye ekleme/çıkarma yalnızca master (ofis sahibi) hesabında açıktır.",
                        foreground=C.INK_SOFT)
                    self._set_users_status("", ok=True)
                else:
                    self._set_users_status(err, ok=False)
                return
            # Başarılı list → master'ız: yönetimi aç, listeyi + ayarları doldur.
            self._set_master_enabled(True)
            self.users_role_lbl.configure(text="Master (ofis sahibi) hesabı — üyeleri yönetebilirsiniz.",
                                          foreground=C.SAGE_DK)
            for i in self.users_tree.get_children():
                self.users_tree.delete(i)
            for u in data.get("users", []):
                rol = "Master" if u.get("role") == "master" else "Üye"
                durum = "Aktif" if u.get("active", True) else "Pasif"
                self.users_tree.insert("", "end",
                                       values=(u["username"], rol, u.get("label", ""),
                                               u.get("email", ""), durum))
            # Ayarlar: kendi e-postam + sıfırlama politikası
            self.own_email_var.set(data.get("my_email", "") or "")
            self.policy_var.set("Bana (master) gelsin" if data.get("reset_to_master", True)
                                else "Kullanıcının kendisine gitsin")
            lbl = data.get("office_label") or ""
            self._set_users_status(f"Ofis: {lbl} · {len(data.get('users', []))} kullanıcı.", ok=True)
        self._run_api("list", done)

    def on_add_user(self):
        user = self.new_user_var.get().strip()
        if not user:
            self._set_users_status("Yeni kullanıcı adı gerekli.", ok=False)
            return
        email = self.new_email_var.get().strip()
        pw = self.new_pass_var.get().strip()
        self._set_users_status("Ekleniyor…", ok=True)

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self.new_user_var.set("")
            self.new_email_var.set(""); self.new_pass_var.set("")
            self._show_credential("Kullanıcı eklendi", data.get("username"), data.get("password"))
            self.refresh_users()
        self._run_api("add", done, new_username=user, email=email, new_password=pw)

    def on_set_user_email(self):
        target = self._selected_username()
        if not target:
            self._set_users_status("Önce listeden bir kullanıcı seçin.", ok=False)
            return
        sel = self.users_tree.selection()
        cur_email = self.users_tree.item(sel[0], "values")[3]
        win = tk.Toplevel(self.root)
        win.title("E-posta Ata"); win.configure(bg=C.CARD); win.geometry("380x170")
        win.transient(self.root)
        tk.Label(win, text=f"{target} için e-posta", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.fonts["card_t"]).pack(pady=(14, 8))
        var = tk.StringVar(value=cur_email)
        ent = self._flat_entry(win, var, width=34)
        ent.pack(pady=4, padx=16, ipady=6, fill='x')

        def save():
            email = var.get().strip()
            win.destroy()
            self._set_users_status("E-posta kaydediliyor…", ok=True)

            def done(data, err):
                if err:
                    self._set_users_status(err, ok=False)
                    return
                self._set_users_status(f"{target} e-postası güncellendi.", ok=True)
                self.refresh_users()
            self._run_api("set_email", done, target=target, email=email)
        btn = RoundButton(win, "Kaydet", command=save, kind="primary",
                          font=self.fonts["card_d"], height=32)
        btn.configure(bg=C.CARD)
        btn.pack(pady=8)

    def on_save_own_email(self):
        email = self.own_email_var.get().strip()
        self._set_users_status("E-posta kaydediliyor…", ok=True)

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self._set_users_status("Kendi e-postanız güncellendi.", ok=True)
        self._run_api("my_email", done, email=email)

    def on_save_policy(self):
        to_master = "1" if self.policy_var.get().startswith("Bana") else "0"
        self._set_users_status("Politika kaydediliyor…", ok=True)

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self._set_users_status("Parola sıfırlama politikası güncellendi.", ok=True)
        self._run_api("policy", done, reset_to_master=to_master)

    def on_reset_user(self):
        target = self._selected_username()
        if not target:
            self._set_users_status("Önce listeden bir kullanıcı seçin.", ok=False)
            return
        self._set_users_status("Parola sıfırlanıyor…", ok=True)

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self._show_credential("Parola sıfırlandı", data.get("username"), data.get("password"))
        self._run_api("reset", done, target=target)

    def on_toggle_user(self):
        target = self._selected_username()
        if not target:
            self._set_users_status("Önce listeden bir kullanıcı seçin.", ok=False)
            return
        sel = self.users_tree.selection()
        cur_status = self.users_tree.item(sel[0], "values")[4]
        new_active = "0" if cur_status == "Aktif" else "1"

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self._set_users_status(f"{target} durumu güncellendi.", ok=True)
            self.refresh_users()
        self._run_api("toggle", done, target=target, active=new_active)

    def on_delete_user(self):
        target = self._selected_username()
        if not target:
            self._set_users_status("Önce listeden bir kullanıcı seçin.", ok=False)
            return
        if not messagebox.askyesno("Sil", f"'{target}' kullanıcısı silinsin mi?"):
            return

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self._set_users_status(f"{target} silindi.", ok=True)
            self.refresh_users()
        self._run_api("delete", done, target=target)

    def on_change_own_password(self):
        new_pw = self.own_pass_var.get().strip()
        # İstemci tarafı UX ön-kontrolü; asıl politika sunucuda zorlanır (accounts.password_policy_error:
        # en az 8 karakter + büyük/küçük harf, rakam, özel işaret). Sunucudan gelen hata yine gösterilir.
        if len(new_pw) < 8:
            self._set_users_status("Yeni parola en az 8 karakter olmalı "
                                   "(büyük/küçük harf, rakam ve özel işaret içermeli).", ok=False)
            return

        def done(data, err):
            if err:
                self._set_users_status(err, ok=False)
                return
            self.own_pass_var.set("")
            # Yeni parola bu oturumda da kullanılsın (sonraki API çağrıları için).
            self.password = new_pw
            if self.cfg.get("remember"):
                self.cfg["password_enc"] = encrypt_secret(new_pw)
                save_config(self.cfg)
            self._set_users_status("Parolanız güncellendi. (Bu oturumda yeni parola geçerli.)", ok=True)
        self._run_api("passwd", done, new_password=new_pw)

    def _show_credential(self, title, username, password):
        """Üretilen/sıfırlanan parolayı BİR KEZ gösterir (kopyalanabilir diyalog)."""
        if not password:
            self._set_users_status(f"{title}.", ok=True)
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=C.CARD)
        win.geometry("420x200")
        win.transient(self.root)
        tk.Label(win, text=title, bg=C.CARD, fg=C.SAGE_DK,
                 font=self.fonts["card_t"]).pack(pady=(14, 6))
        tk.Label(win, text="Bu parola yalnızca BİR KEZ gösterilir — kullanıcıya iletin:",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.fonts["small"],
                 wraplength=380).pack(pady=(0, 10))
        box = tk.Entry(win, width=40, font=('Consolas', 11), bg="#FFFFFF", fg=C.INK,
                       relief="flat", highlightthickness=1, highlightbackground=C.LINE,
                       highlightcolor=C.SAGE, justify='center')
        box.insert(0, f"Kullanıcı: {username}   Parola: {password}")
        box.configure(state='readonly', readonlybackground="#FFFFFF")
        box.pack(pady=4, padx=16, ipady=6, fill='x')
        self._enable_clipboard(box)
        kopyala = RoundButton(win, "Panoya Kopyala",
                              command=lambda: (self.root.clipboard_clear(),
                                               self.root.clipboard_append(f"{username} / {password}"),
                                               self._set_users_status("Parola panoya kopyalandı.", ok=True)),
                              kind="primary", font=self.fonts["card_d"], height=32)
        kopyala.configure(bg=C.CARD)
        kopyala.pack(pady=12, padx=16, fill='x')
        self._set_users_status(f"{title}: {username}", ok=True)

    # ── Ofis Paylaşım Eylemleri ──
    def on_share_click(self):
        if self.is_sharing:
            self.stop_sharing()
        else:
            self.start_sharing()

    def start_sharing(self):
        pin = self.pin_var.get().strip()
        cert_id = self.cert_var.get().strip()
        port = LOCAL_PORT

        if not pin:
            messagebox.showerror("Hata", "Lütfen E-imza PIN kodunu girin.")
            return

        self.is_sharing = True
        self.disable_share_inputs()
        self.save_dashboard_config()
        # İlk kurulumu elle yapan kullanıcı için de kur-unut tamamlanır: otomatik
        # paylaşım açıksa Başlangıç kısayolu bu noktada kurulur.
        if self.cfg.get("auto_share"):
            sync_startup_shortcut(True, self.log_queue.put)

        # Önceki oturumdan kalan ve 8800'ü hâlâ tutan bir süreç varsa temizle; aksi halde
        # uvicorn bağlanamaz (WinError 10048) ve paylaşım sessizce çöker.
        free_port_if_busy(LOCAL_PORT, self.log_queue.put)

        # LAN-direct tercihi: uyap_proxy._resolve_bind_host bunu okur (kapalıyken 0.0.0.0
        # isteği 127.0.0.1'e düşer), office_agent de yalnızca açıkken LAN IP'lerini + oturumluk
        # LAN biletini ilan eder. Her paylaşımda açıkça yazılır ki eski değer miras kalmasın.
        os.environ["UYAP_LAN_SHARE"] = "1" if self.lan_share_var.get() else "0"

        self.log_queue.put("[SİSTEM] UYAP oturumu kuruluyor; LAN + dış ağ paylaşımı başlatılıyor...\n")

        # Tek event loop, tek UYAP oturumu: run_office hem LAN proxy'yi (0.0.0.0:port) hem
        # bulut signaling/WebRTC'yi aynı oturumu paylaşarak koşturur.
        self.share_loop = asyncio.new_event_loop()
        office_args = argparse.Namespace(
            signaling=self.server_url, room=self.username, office_code=self.office_code,
            password=self.password, pin=pin, cert_id=cert_id or None, no_verify=False, interval=5,
            proxy_port=port, host="0.0.0.0", user=self.username,
        )

        def run_office_thread():
            asyncio.set_event_loop(self.share_loop)
            self.share_task = self.share_loop.create_task(office_agent.run_office(office_args))
            try:
                self.share_loop.run_until_complete(self.share_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.log_queue.put(f"[HATA] Paylaşım durdu: {e}\n")
            finally:
                self.log_queue.put("[SİSTEM] Paylaşım sonlandı.\n")
                self.root.after(0, lambda: self.on_process_exited("office"))
                try:
                    self.share_loop.close()
                except Exception:
                    pass

        self.share_thread = threading.Thread(target=run_office_thread, daemon=True)
        self.share_thread.start()
        start_panel_server(self.log_queue.put)

        self.share_btn.set_text("Paylaşımı Durdur"); self.share_btn.set_kind("stop")
        self.share_open_browser_btn.set_state('normal')
        self.share_status_lbl.configure(text="● Paylaşım Aktif", foreground=C.OK)

        if self.lan_share_var.get():
            addr_lines = [f"http://127.0.0.1:{port}/giris"]
            for ip in detect_ips():
                addr_lines.append(f"http://{ip}:{port}/giris")
            body = "Yerel ağdaki istemciler şu adreslerden bağlanabilir:\n" + "  |  ".join(addr_lines)
        else:
            body = (f"Bu bilgisayardan giriş: http://127.0.0.1:{port}/giris "
                    "(yerel ağdan doğrudan erişim KAPALI; diğer bilgisayarlar dış ağ yoluyla bağlanır).")
        body += f"\nDış ağ: '{self.username}' kullanıcısıyla {self.server_url} üzerinden paylaşılıyor (oda kimliği içeride otomatik döner)."
        self.log_queue.put(f"[SİSTEM] {body}\n")

    def stop_sharing(self, unexpected=False):
        self.is_sharing = False
        stop_panel_server(self.log_queue.put)

        if hasattr(self, "share_loop") and self.share_loop and not self.share_loop.is_closed():
            if hasattr(self, "share_task") and self.share_task:
                try:
                    self.share_loop.call_soon_threadsafe(self.share_task.cancel)
                except Exception as e:
                    self.log_queue.put(f"[SİSTEM] Paylaşım görevi iptal edilirken hata: {e}\n")

        self.share_btn.set_text("Bağlantıyı Paylaş"); self.share_btn.set_kind("primary")
        self.share_open_browser_btn.set_state('disabled')
        self.share_status_lbl.configure(text="● Paylaşım Durduruldu", foreground=C.OFF)
        self.enable_share_inputs()

        if unexpected:
            self.log_queue.put("[SİSTEM] Paylaşım beklenmedik bir hata nedeniyle durduruldu.\n")

    def disable_share_inputs(self):
        self.pin_entry.configure(state='disabled')
        self.cert_entry.configure(state='disabled')
        self.lan_share_cb.configure(state='disabled')

    def enable_share_inputs(self):
        self.pin_entry.configure(state='normal')
        self.cert_entry.configure(state='normal')
        self.lan_share_cb.configure(state='normal')

    # ── İstemci (Alma) Eylemleri ──
    def on_receive_click(self):
        if self.is_receiving:
            self.stop_receiving()
        else:
            self.start_receiving()

    def start_receiving(self):
        port = LOCAL_PORT
        self.is_receiving = True
        self.save_dashboard_config()

        # Önceki oturumdan kalan ve 8800'ü tutan bir süreç varsa temizle (aksi halde
        # yerel sunucu bağlanamaz ve tarayıcı ofise ulaşamaz).
        free_port_if_busy(LOCAL_PORT, self.log_queue.put)

        self.log_queue.put("[SİSTEM] İstemci başlatılıyor (önce yerel ağ denenecek, olmazsa dış ağ)...\n")

        self.receiver_loop = asyncio.new_event_loop()
        receiver_args = argparse.Namespace(
            signaling=self.server_url, room=self.username, office_code=self.office_code,
            password=self.password, host="127.0.0.1", port=port,
        )

        def run_receiver():
            asyncio.set_event_loop(self.receiver_loop)
            self.receiver_task = self.receiver_loop.create_task(home_client.amain(receiver_args))
            try:
                self.receiver_loop.run_until_complete(self.receiver_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.log_queue.put(f"[HATA] İstemci durdu: {e}\n")
            finally:
                self.log_queue.put("[SİSTEM] İstemci sonlandı.\n")
                self.root.after(0, lambda: self.on_process_exited("home_client"))
                try:
                    self.receiver_loop.close()
                except Exception:
                    pass

        self.receiver_thread = threading.Thread(target=run_receiver, daemon=True)
        self.receiver_thread.start()

        self.receive_btn.set_text("Bağlantıyı Kes"); self.receive_btn.set_kind("stop")
        self.open_browser_btn.set_state('normal')
        self.receive_status_lbl.configure(text="● Bağlantı Aktif", foreground=C.OK)

    def stop_receiving(self, unexpected=False):
        self.is_receiving = False

        if hasattr(self, "receiver_loop") and self.receiver_loop and not self.receiver_loop.is_closed():
            if hasattr(self, "receiver_task") and self.receiver_task:
                try:
                    self.receiver_loop.call_soon_threadsafe(self.receiver_task.cancel)
                except Exception as e:
                    self.log_queue.put(f"[SİSTEM] Alıcı görevi iptal edilirken hata: {e}\n")

        self.receive_btn.set_text("Bağlantıyı Al"); self.receive_btn.set_kind("primary")
        self.open_browser_btn.set_state('disabled')
        self.receive_status_lbl.configure(text="● Bağlantı Yok", foreground=C.OFF)

        if unexpected:
            self.log_queue.put("[SİSTEM] İstemci bağlantısı beklenmedik bir hata nedeniyle kesildi.\n")

    def on_open_browser_click(self):
        try:
            webbrowser.open(f"http://127.0.0.1:{LOCAL_PORT}/giris")
        except Exception as e:
            self.log_queue.put(f"[HATA] Tarayıcı açılamadı: {e}\n")

    def save_dashboard_config(self):
        self.cfg["cert_id"] = self.cert_var.get().strip()
        self.cfg["pin_enc"] = encrypt_secret(self.pin_var.get().strip())
        self.cfg["local_port"] = LOCAL_PORT
        self.cfg["lan_share"] = bool(self.lan_share_var.get())
        save_config(self.cfg)

    def on_auto_share_toggle(self):
        """Paylaşım kartındaki kur-unut anahtarı: ayarı kalıcılaştırır, giriş kartındaki
        eş kutuyu senkron tutar ve Başlangıç kısayolunu kurar/söker."""
        self.cfg["auto_share"] = bool(self.auto_share_dash_var.get())
        save_config(self.cfg)
        if hasattr(self, "auto_share_var"):
            self.auto_share_var.set(self.cfg["auto_share"])
        sync_startup_shortcut(self.cfg["auto_share"], self.log_queue.put)
        self.log_queue.put("[SİSTEM] Otomatik paylaşım %s.\n"
                           % ("açık: girişte kendiliğinden başlar" if self.cfg["auto_share"] else "kapalı"))

    def on_process_exited(self, name):
        if name == "office" and self.is_sharing:
            self.stop_sharing(unexpected=True)
        elif name == "home_client" and self.is_receiving:
            self.stop_receiving(unexpected=True)

    # ── Log / Kuyruk Takibi ──
    def poll_logs(self):
        processed = 0
        while processed < 100:
            try:
                msg = self.log_queue.get_nowait()
                self.append_to_log_widget(msg)
                processed += 1
            except queue.Empty:
                break
        self.root.after(100, self.poll_logs)

    def append_to_log_widget(self, text):
        if hasattr(self, 'log_widget') and self.log_widget.winfo_exists():
            self.log_widget.configure(state='normal')
            self.log_widget.insert('end', text)
            self.log_widget.see('end')
            self.log_widget.configure(state='disabled')

    def clear_logs(self):
        if hasattr(self, 'log_widget') and self.log_widget.winfo_exists():
            self.log_widget.configure(state='normal')
            self.log_widget.delete('1.0', 'end')
            self.log_widget.configure(state='disabled')

    # ── Çıkış / Kapatma ──
    def on_logout_click(self):
        if messagebox.askyesno("Çıkış", "Oturumu kapatmak ve çalışan tüm süreçleri durdurmak istediğinize emin misiniz?"):
            self.stop_sharing()
            self.stop_receiving()
            self.username = ""
            self.password = ""
            self.show_login_screen()

    def on_close(self):
        # Paylaşım/alma görevlerini nazikçe iptal etmeyi DENE, sonra pencereyi yık.
        self.is_sharing = False
        self.is_receiving = False
        try:
            self.stop_sharing()
            self.stop_receiving()
        except Exception:
            pass
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        # GARANTİ KAPANIŞ: uvicorn/aiortc/WebRTC arka plan thread'leri (bazıları non-daemon
        # ya da IOCP/medya thread'i) süreci asılı bırakabiliyor; sys.exit() Tk geri-çağrısı
        # içinde SystemExit'i yutulduğu için pencere kapanmıyordu. os._exit tüm thread'leri
        # anında sonlandırır — ayar zaten kaydedildi, temizlenecek kritik bir şey yok.
        os._exit(0)


if __name__ == "__main__":
    # Windows'ta asyncio'nun aiortc/aiohttp/uvicorn ile düzgün çalışması için Proactor döngüsü.
    if IS_WINDOWS:
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

    root = tk.Tk()
    app = UyapApp(root)
    root.mainloop()
