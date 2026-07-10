# -*- coding: utf-8 -*-
"""
UYAP İcra veri katmanı — Django ayarları (yalnızca ORM).
========================================================
Bu proje bir arayüz DEĞİL; UYAP'tan çekilen verilerin kendi PostgreSQL
sunucumuza yazıldığı veri katmanıdır. Yalnızca ORM + migration kullanılır;
URL/şablon/admin yoktur (gerekirse sonra eklenir).

PostgreSQL bağlantısı ortam değişkenlerinden okunur (varsayılanlarla):
    UYAP_DB_NAME      (vars: uyap_icra)
    UYAP_DB_USER      (vars: postgres)
    UYAP_DB_PASSWORD  (vars: boş)
    UYAP_DB_HOST      (vars: 127.0.0.1)
    UYAP_DB_PORT      (vars: 5432)
"""
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


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

# Güvenli varsayılanlar (secure by default) — güvenlik raporu bulgu #6.
# Bu katman yalnızca ORM (URL/şablon/admin yok), ama yine de sabit anahtar
# kullanmaz: ortamdan al, yoksa kalıcı dosyaya rastgele üret+yaz.
def _gizli_anahtar_al() -> str:
    env = os.environ.get("UYAP_SECRET_KEY")
    if env:
        return env.strip()
    kok = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(BASE_DIR)
    gizli_yol = Path(kok) / "UyapIcra" / "icra_secret_key"
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
        gizli_yol.parent.mkdir(parents=True, exist_ok=True)
        gizli_yol.write_text(yeni, encoding="utf-8")
        try:
            os.chmod(gizli_yol, 0o600)
        except Exception:
            pass
        _windows_acl_sinirla(gizli_yol)
    except Exception:
        pass
    return yeni


SECRET_KEY = _gizli_anahtar_al()
# DEBUG yalnızca açık bayrakla; varsayılan False.
DEBUG = os.environ.get("UYAP_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    "icra_models",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("UYAP_DB_NAME", "uyap_icra"),
        "USER": os.environ.get("UYAP_DB_USER", "postgres"),
        "PASSWORD": os.environ.get("UYAP_DB_PASSWORD", ""),
        "HOST": os.environ.get("UYAP_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("UYAP_DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
# Veri deposu yerel (Istanbul) saatlerini olduğu gibi tutar; tz dönüşümü yok.
USE_TZ = False
