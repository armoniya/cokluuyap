"""
uyap_panel.core.auth — Bulut satıcısına (vendor) karşı kimlik doğrulama + ofis API'si.

  • verify_credentials — signaling sunucusuna (wss .../ws) bağlanıp kullanıcı/parola
        çiftini "probe" rolüyle doğrular (oda durumuna dokunmaz). (Senkron; thread'den çağırın.)
  • office_api         — vendor REST API'si (/api/office): kullanıcı listesi/ekleme/
        sıfırlama/politika vb. (data, error) döndürür.
  • reset_request      — kimlik gerektirmeyen parola sıfırlama talebi (/api/reset).
"""

import json
import asyncio
import urllib.request
import urllib.error

from .config import api_base_from_ws

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None


def verify_credentials(server_url: str, username: str, password: str, office_code: str = ""):
    """(ok: bool, message: str, info: dict) döndürür. Signaling üzerinden 'probe' girişini dener.
    office_code verilirse iki-alanlı login: sunucu 'kullanici@ofis_kodu' olarak birleştirir.
    info: başarıda {role, office_code, office_label} (rol-tabanlı UI için); aksi halde {}."""
    if websockets is None:
        return False, "websockets kitaplığı kurulu değil.", {}

    async def _verify():
        try:
            # Render gibi bedava PaaS uykudaysa ilk istek soğuk başlangıçla uzun sürebilir.
            async with websockets.connect(server_url, open_timeout=30.0, ping_timeout=20.0) as ws:
                # role=probe: SALT kimlik doğrulama. 'office' rolü kullanılmaz; o rol gerçek
                # ofis ajanının slotunu ele geçirip tüm bağlı istemcileri düşürüyordu.
                await ws.send(json.dumps({"role": "probe", "room": username,
                                          "office_code": office_code, "password": password}))
                raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "joined":
                    return True, "Başarılı", {
                        "role": msg.get("role", "member"),
                        "office_code": msg.get("office_code", ""),
                        "office_label": msg.get("office_label", ""),
                    }
                if mtype == "error":
                    return False, msg.get("error", "Bilinmeyen hata."), {}
                return False, f"Beklenmeyen sunucu yanıtı: {mtype}", {}
        except asyncio.TimeoutError:
            return False, "Sunucu zaman aşımına uğradı. Sunucu uykudaysa birkaç saniye sonra tekrar deneyin.", {}
        except Exception as e:
            return False, f"Bağlantı başarısız: {e}", {}

    try:
        return asyncio.run(_verify())
    except Exception as e:
        return False, f"Çalışma zamanı hatası: {e}", {}


def office_api(server_url: str, username: str, password: str, action: str,
               office_code: str = "", **params):
    """vendor /api/office çağrısı (senkron). (data, error) döndürür. office_code verilirse
    sunucu iki-alanlı login'i 'kullanici@ofis_kodu' olarak birleştirir."""
    body = {"username": username, "password": password, "action": action, "office_code": office_code}
    body.update(params)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(api_base_from_ws(server_url) + "/api/office", data=data,
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


def reset_request(server_url: str, identifier: str, office_code: str = ""):
    """vendor /api/reset (kimlik gerektirmez) parola sıfırlama talebi. (data, error).
    office_code verilirse çıplak kullanıcı adıyla birleştirilir (e-posta girildiyse gerekmez)."""
    body = json.dumps({"identifier": identifier, "office_code": office_code}).encode("utf-8")
    req = urllib.request.Request(api_base_from_ws(server_url) + "/api/reset", data=body,
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
