"""
uyap_panel.core — Arayüzden bağımsız servis katmanı.

İçerik:
  • config      — ayar dosyası (yükle/kaydet) + DPAPI ile parola/PIN şifreleme,
                  yerel IP tespiti, meşgul portu serbest bırakma.
  • auth        — bulut satıcısına (vendor) karşı kimlik doğrulama + ofis API'si.
  • connection  — ConnectionManager: PAYLAŞ (office_agent) ve AL (home_client)
                  yaşam döngüsü; asyncio olay döngüsünü ayrı bir iş parçacığında
                  yönetir; çıktı günlüğünü tampona yazar.
  • takip       — TakipService: dosya ayrıştırma (MTS/İcra) + yerel proxy (8800)
                  üzerinden iş kuyruğuna iş gönderme / durum sorgulama / onay.

uyap_app.py'deki dağınık mantık BURADA tek yerde toplanır; ttkbootstrap GUI ve
Django ön yüzleri yalnızca bu katmanı çağırır (kod tekrarı yok).
"""

from . import config, auth, connection, takip, sgk

# İcra iş türünü ("icra_takip_ac") kuyruğa kaydet. uyap_core importu config'te
# sys.path ayarlandıktan sonra güvenle yapılır; backend yoksa kayıt yine yapılır,
# iş çalışınca net "backend eklenmedi" mesajı döner.
try:
    from . import icra_jobs  # noqa: F401  (yan etki: @job_type kaydı)
except Exception as _e:  # pragma: no cover
    import sys as _sys
    print(f"[uyap_panel] İcra iş türü kaydı atlandı: {_e}", file=_sys.stderr)
from .config import (
    PORT, DEFAULT_SERVER_URL, load_config, save_config,
    encrypt_secret, decrypt_secret, detect_ips, api_base_from_ws,
)
from .connection import ConnectionManager, get_manager
from .takip import TakipService
from .auth import verify_credentials, office_api, reset_request
from .sgk import SgkBatch, SORGULAR, KOLONLAR, ANAHTARLAR

__all__ = [
    "config", "auth", "connection", "takip", "sgk",
    "PORT", "DEFAULT_SERVER_URL", "load_config", "save_config",
    "encrypt_secret", "decrypt_secret", "detect_ips", "api_base_from_ws",
    "ConnectionManager", "get_manager", "TakipService",
    "verify_credentials", "office_api", "reset_request",
    "SgkBatch", "SORGULAR", "KOLONLAR", "ANAHTARLAR",
]
