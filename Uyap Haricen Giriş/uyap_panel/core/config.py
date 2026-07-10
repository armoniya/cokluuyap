"""
uyap_panel.core.config — Ayar dosyası, DPAPI şifreleme ve ağ yardımcıları.

uyap_app.py içindeki config/DPAPI/IP/port yardımcılarının TEK noktaya çıkarılmış
hâli. Ayar dosyası, üç ön yüzün (ttk/ttkbootstrap/Django) aynı kayıtlı kullanıcı
bilgisini paylaşması için varsayılan olarak "Uyap Haricen Giriş/uyap_app_config.json"
ile aynıdır.
"""

import os
import sys
import json
import time
import base64
import socket
import subprocess

# .../Uyap Haricen Giriş/uyap_panel/core/config.py → UYAP_DIR = "Uyap Haricen Giriş"
UYAP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# uyap_core'u import edilebilir kıl (ön yüzler farklı çalışma dizininden başlatılabilir).
if UYAP_DIR not in sys.path:
    sys.path.insert(0, UYAP_DIR)

# Mevcut uyap_app.py ile AYNI ayar dosyası — kayıtlı kullanıcı/parola paylaşılır.
CONFIG_PATH = os.path.join(UYAP_DIR, "uyap_app_config.json")

DEFAULT_SERVER_URL = "wss://www.cokluuyap.com/ws"
PORT = 8800
IS_WINDOWS = os.name == "nt"

DEFAULTS = {
    "server_url": DEFAULT_SERVER_URL,
    "username": "",
    "remember": False,
    "password_enc": "",
    "pin_enc": "",
    "cert_id": "",
    "local_port": PORT,
    # Yeni ön yüzlerin hatırladığı takip ayarları (il/adliye/onay modu).
    "il": "İzmir",
    "adliye": "İzmir",
    "onay_modu": "tek_tek",
}


# ── DPAPI ile şifreleme (yalnızca Windows; diğer OS'te düz metne düşer) ──────────
def _dpapi(func, data: bytes) -> bytes:
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
# olarak dosyaya yazılıyordu. Artık:
#   • Windows: DPAPI; başarısız olursa passphrase fallback denenir, o da yoksa sır
#     KAYDEDİLMEZ (boş döner) → düz metne ASLA düşülmez (fail-closed) + kullanıcı uyarılır.
#   • Windows dışı: UYAP_CONFIG_SECRET ile passphrase tabanlı (PBKDF2+Fernet) şifreleme;
#     env yoksa sır KAYDEDİLMEZ + bir kez uyarı.
# Jeton biçimleri: "dpapi:<b64>" | "pbk:<b64salt>:<fernet>" | (eski) düz metin (yalnızca OKUMA).
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
    """UYAP_CONFIG_SECRET varsa PBKDF2+Fernet ile şifreler ('pbk:...'); yoksa/başarısızsa None."""
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
    """Sırrı güvenli biçimde şifreler. ASLA düz metin döndürmez (fail-closed): şifreleme
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
    # Eski sürümden kalan DÜZ METİN sır (geriye dönük OKUMA) — olduğu gibi döndürülür;
    # bir sonraki save_config'te güvenli biçimde yeniden şifrelenecektir.
    return token


# ── Ayar dosyası ────────────────────────────────────────────────────────────────
def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
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


def save_config(cfg: dict) -> None:
    try:
        # Atomik yazım + dar izin: önce 0600 ile geçici dosya, sonra yerine taşı.
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _restrict_perms(tmp)
        os.replace(tmp, CONFIG_PATH)
        _restrict_perms(CONFIG_PATH)
    except Exception as e:
        print(f"[CONFIG] Ayar kaydedilemedi: {e}")


# ── Ağ yardımcıları ──────────────────────────────────────────────────────────────
def detect_ips():
    """Makinenin erişilebilir yerel IPv4 adreslerini döndürür (LAN paylaşımı için)."""
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


def api_base_from_ws(server_url: str) -> str:
    """wss://host/ws → https://host (vendor REST API tabanı)."""
    u = (server_url or "").strip()
    if u.startswith("wss://"):
        u = "https://" + u[len("wss://"):]
    elif u.startswith("ws://"):
        u = "http://" + u[len("ws://"):]
    if u.endswith("/ws"):
        u = u[:-3]
    return u.rstrip("/")


def free_port_if_busy(port=PORT, log=print):
    """Paylaşım/alma öncesi yerel portun boş olduğundan emin ol.

    Önceki oturumdan kalan bir UYAP python süreci portu tutuyorsa, uvicorn
    'WinError 10048' ile bağlanamaz ve paylaşım sessizce çöker. Portu tutan
    ESKİ python sürecini kapatır; python olmayan uygulamaya dokunmaz, uyarır.
    (uyap_app.free_port_if_busy ile aynı davranış.)
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
        return
    except OSError:
        pass
    finally:
        try:
            probe.close()
        except Exception:
            pass

    if os.name != "nt":
        log(f"[SİSTEM] {port} portu meşgul; önceki örneği kapatıp tekrar deneyin.\n")
        return

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

    time.sleep(0.8)
