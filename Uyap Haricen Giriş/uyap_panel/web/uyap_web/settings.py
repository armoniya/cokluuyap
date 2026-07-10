"""
Django ayarları — UYAP Ağ Geçidi YEREL paneli.

Bu Django YALNIZCA kullanıcının kendi bilgisayarında (127.0.0.1:8000) çalışır ve
uyap_panel.core üzerinden aynı süreçte UYAP bağlantısını yönetir. UYAP oturumu
(e-imza tüneli) ayrı port 8800'de kalır; panel ona dokunmaz, sadece komut verir.

DB GEREKMEZ: oturumlar dosya tabanlıdır (server-side), tek 'migrate' bile gerekmez.
"""

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # .../uyap_panel/web
UYAP_DIR = BASE_DIR.parent.parent                          # .../Uyap Haricen Giriş

# Çekirdek + uyap_core import yolları.
for _p in (str(BASE_DIR), str(UYAP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Güvenli varsayılanlar (secure by default) — güvenlik raporu bulgu #6.
# SECRET_KEY ASLA kaynağa gömülmez: önce ortam değişkeni, yoksa ilk
# çalıştırmada rastgele üretilip kullanıcıya özel bir dosyaya yazılır.
# DEBUG varsayılanı False; yalnızca açık bayrakla (UYAP_PANEL_DEBUG=1) açılır.
# ---------------------------------------------------------------------------
def _env_bayrak(ad: str) -> bool:
    return os.environ.get(ad, "").strip().lower() in ("1", "true", "yes", "on")


def _windows_acl_sinirla(path) -> None:
    """os.chmod Windows'ta sessiz no-op olduğundan (bulgu #11) gerçek ACL kısıtlaması:
    kalıtımı kes, dosyayı yalnızca mevcut kullanıcıya (tam yetki) sınırla."""
    if os.name != "nt":
        return
    kullanici = os.environ.get("USERNAME", "").strip()
    if not kullanici:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{kullanici}:(F)"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass


def _gizli_anahtar_al() -> str:
    """SECRET_KEY döndürür. Öncelik: ortam değişkeni → kalıcı dosya → yeni üret+yaz."""
    env = os.environ.get("UYAP_PANEL_SECRET_KEY")
    if env:
        return env.strip()
    # Kullanıcıya özel, yazılabilir konum (yoksa BASE_DIR'a düşer).
    kok = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(BASE_DIR)
    gizli_dizin = Path(kok) / "UyapIcra"
    gizli_yol = gizli_dizin / "panel_secret_key"
    try:
        if gizli_yol.is_file():
            mevcut = gizli_yol.read_text(encoding="utf-8").strip()
            if mevcut:
                return mevcut
    except Exception:
        pass
    import secrets
    yeni = secrets.token_urlsafe(64)
    try:
        gizli_dizin.mkdir(parents=True, exist_ok=True)
        gizli_yol.write_text(yeni, encoding="utf-8")
        try:
            os.chmod(gizli_yol, 0o600)  # POSIX'te sahip-okuma; Windows'ta yok sayılır.
        except Exception:
            pass
        _windows_acl_sinirla(gizli_yol)
    except Exception:
        # Diske yazılamazsa oturumluk anahtar kullan (yeniden başlatınca değişir).
        pass
    return yeni


SECRET_KEY = _gizli_anahtar_al()
DEBUG = _env_bayrak("UYAP_PANEL_DEBUG")
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# uyap_app.py paylaşım açıkken paneli /__panel__ öneki altında başlatır (bkz
# uyap_core/uyap_proxy.py: PANEL_PREFIX + handle_panel_proxy) — böylece cokluuyap.com
# üzerinden tünelle açılan panel bağlantıları (statik, {% url %}, yönlendirme) doğru
# önekle üretilir. Ortam değişkeni yoksa (bağımsız/yerel geliştirme çalıştırması)
# önek boş kalır, panel her zamanki gibi kök yolda çalışır.
FORCE_SCRIPT_NAME = os.environ.get("UYAP_PANEL_SCRIPT_NAME") or None

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# DB tabanlı yerine dosya tabanlı oturum: 'migrate' gerektirmez, hassas veriyi
# (parola) sunucu tarafında dosyada tutar; çerezde yalnızca oturum kimliği gider.
SESSION_ENGINE = "django.contrib.sessions.backends.file"
SESSION_FILE_PATH = str(BASE_DIR / ".sessions")
os.makedirs(SESSION_FILE_PATH, exist_ok=True)

# MessageMiddleware için gerekli ama DB istemeyen depo.
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

ROOT_URLCONF = "uyap_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "uyap_web.wsgi.application"

# DB kullanılmıyor; yine de Django bir tanım bekleyebilir diye dummy sqlite (dosya
# oluşturulmaz çünkü hiçbir model/migration yok).
DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(BASE_DIR / ".unused.sqlite3")}
}

LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Yerel panel: CSRF çerezi okunabilir olsun (fetch ile X-CSRFToken gönderimi için).
CSRF_COOKIE_HTTPONLY = False
