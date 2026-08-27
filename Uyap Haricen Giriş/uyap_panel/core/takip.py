"""
uyap_panel.core.takip — Takip açılış servisi (İcra XML + MTS).

İki ayrı akış, tek servis:
  • kind="mts"   → uyap_core.mts ile ayrıştırma + "coklu_takip_ac" işi (HAZIR backend).
  • kind="icra"  → uyap_core.icra ile ayrıştırma + "icra_takip_ac" işi
                   (İcra backend uzantı noktası: uyap_panel.core.icra_jobs).

Dosya İSTEMCİDE ayrıştırılır (pandas), takipler params olarak işe gönderilir; iş
ofisteki canlı UYAP oturumu üzerinden yerel proxy (127.0.0.1:8800) ile çalışır.
Tüm ağ çağrıları senkrondur — bir iş parçacığından çağırın.
"""

import os
import json
import base64
import urllib.request
import urllib.error

from .config import PORT, local_gateway_token

JOB_TYPE = {"mts": "coklu_takip_ac", "icra": "icra_takip_ac"}


class TakipService:
    """Yerel proxy üzerinden iş kuyruğuna konuşan ince servis.

    username/password, ofis proxy'sinin Basic-Auth'u için gerekir (home_client'te
    zararsız). port = yerel proxy portu (varsayılan 8800).
    """

    def __init__(self, username, password, port=PORT):
        self.username = username
        self.password = password
        self.port = port

    # ── Dosya ayrıştırma (istemcide) ──
    @staticmethod
    def parse_source(path, kind="mts"):
        """Excel/XML → (takipler_params: list, error: str|None)."""
        try:
            if kind == "mts":
                from uyap_core.mts.parse import kaynak_to_takipler
                from uyap_core.mts.models import takipler_to_params
                return takipler_to_params(kaynak_to_takipler(path)), None
            elif kind == "icra":
                # İcra ayrıştırıcı uzantı noktası: uyap_core.icra.parse (henüz yoksa
                # MTS ayrıştırıcıyla aynı XML şemasını dener — birçok alan ortaktır).
                try:
                    from uyap_core.icra.parse import kaynak_to_takipler as icra_parse
                    from uyap_core.icra.models import takipler_to_params as icra_params
                    return icra_params(icra_parse(path)), None
                except ImportError:
                    from uyap_core.mts.parse import kaynak_to_takipler
                    from uyap_core.mts.models import takipler_to_params
                    return takipler_to_params(kaynak_to_takipler(path)), None
            else:
                return None, f"Bilinmeyen takip türü: {kind}"
        except Exception as e:
            return None, str(e)

    @staticmethod
    def file_to_b64(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    @staticmethod
    def attachment(path):
        return {"filename": os.path.basename(path), "b64": TakipService.file_to_b64(path)}

    # ── Yerel proxy (iş kuyruğu) çağrıları ──
    def _request(self, method, path, body=None):
        url = f"http://127.0.0.1:{self.port}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        tok = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", "Basic " + tok)
        local_tok = local_gateway_token()
        if local_tok:
            req.add_header("X-Uyap-Local-Token", local_tok)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = r.read().decode("utf-8")
                return (json.loads(txt) if txt else {}), None
        except urllib.error.HTTPError as e:
            try:
                return None, json.loads(e.read().decode("utf-8")).get("error", str(e))
            except Exception:
                return None, f"Sunucu hatası ({e.code})."
        except Exception as e:
            return None, f"Yerel UYAP bağlantısına ulaşılamadı (önce 'Paylaş' veya 'Al'): {e}"

    # ── İş yaşam döngüsü ──
    def build_params(self, takipler, *, il, adliye, onay_modu, odeme_aktif=None,
                     odeme_onay_modu=None, tebligat_aktif=None, tebligat_onay_modu=None,
                     vekalet=None, dayanak=None):
        """coklu_takip_ac / icra_takip_ac için ortak params sözlüğü.

        odeme_aktif/odeme_onay_modu/tebligat_aktif/tebligat_onay_modu: yalnız MTS
        (coklu_takip_ac) kullanır — bkz. job_handlers.py. icra_takip_ac bunları görmez
        (None ise hiç eklenmez), o yüzden icra tarafını etkilemez."""
        params = {"takipler": takipler, "il": il, "adliye": adliye, "onay_modu": onay_modu}
        if odeme_aktif is not None:
            params["odeme_aktif"] = odeme_aktif
        if odeme_onay_modu is not None:
            params["odeme_onay_modu"] = odeme_onay_modu
        if tebligat_aktif is not None:
            params["tebligat_aktif"] = tebligat_aktif
        if tebligat_onay_modu is not None:
            params["tebligat_onay_modu"] = tebligat_onay_modu
        if vekalet:
            params["vekalet"] = {t.get("alacakli", ""): vekalet for t in takipler}
        if dayanak:
            params["dayanak"] = {str(t.get("dosya_no", "")): dayanak for t in takipler}
        return params

    def submit(self, kind, params):
        """(job_id, error). kind: 'mts' | 'icra'."""
        jtype = JOB_TYPE.get(kind)
        if not jtype:
            return None, f"Bilinmeyen takip türü: {kind}"
        data, err = self._request("POST", "__uyap_agent__/jobs", {"type": jtype, "params": params})
        if err:
            return None, err
        return (data or {}).get("id"), None

    def get(self, job_id):
        """(job: dict, error)."""
        data, err = self._request("GET", f"__uyap_agent__/jobs/{job_id}")
        if err:
            return None, err
        return (data or {}).get("job") or {}, None

    def cancel(self, job_id):
        return self._request("POST", f"__uyap_agent__/jobs/{job_id}/cancel")

    def approve(self, job_id, decision, selection=None):
        body = {"decision": decision}
        if selection is not None:
            body["selection"] = selection
        return self._request("POST", f"__uyap_agent__/jobs/{job_id}/approve", body)
