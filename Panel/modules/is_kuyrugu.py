# -*- coding: utf-8 -*-
"""
İş Kuyruğu İstemcisi — ofis (güçlü bağlantı) üzerindeki uzun işleri sürer
=========================================================================
Panel modülleri (ör. MTS çoklu takip açma) için, ofis ajanının iş kuyruğu
kontrol-düzlemine konuşan ince bir HTTP istemcisi. Taşıma TAM OLARAK panelin
diğer modüllerinin kullandığıyla aynıdır (bkz. SGK Sorgu/SorguMotoru._post):
tüm istekler yerel `127.0.0.1:8800`'e gider; orası

  • PAYLAŞAN isek kendi ofis proxy'miz (uyap_proxy),
  • LOKAL/UZAK ALAN isek yerel `home_client` (tüneli ofise taşır)

olur. Her üç durumda da adres aynıdır ve localhost istekleri parolasız kabul
edilir (uyap_proxy.require_auth → 127.0.0.1 muaf). Böylece bu istemci, bağlantı
hangi moddaysa o bağlantı üzerinden çalışır; ayrı bir oturum/Playwright YOKTUR.

Kontrol düzlemi sözleşmesi (uyap_core/jobs.py):
    POST /__uyap_agent__/jobs              {"type","params"} → 201 {id, job}
    GET  /__uyap_agent__/jobs/<id>         → {job}  (logs/progress/result/pending_approval)
    POST /__uyap_agent__/jobs/<id>/cancel  → {job}
    POST /__uyap_agent__/jobs/<id>/approve {"decision","selection",...} → {job}
"""

import os
import json
import urllib.request
import urllib.error

OFFICE_BASE = "http://127.0.0.1:8800"
JOBS_URL = f"{OFFICE_BASE}/__uyap_agent__/jobs"

_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-cache",
}


class OfisUlasilamiyor(Exception):
    """Yerel ofis bağlantısına (127.0.0.1:8800) ulaşılamadı — UYAP Bağlantısı
    (Paylaş/Al) henüz başlatılmamış olabilir."""


def _yerel_jeton():
    """uyap_core.uyap_proxy.require_auth()'un localhost'tan gelen TARAYICI-DIŞI isteklerde
    aradığı 'X-Uyap-Local-Token' değerini okur (kullanıcı bulgusu 2026-08-11: bu modül
    jetonu hiç göndermiyordu, ofis ağ geçidiyle AYRI süreçte çalıştığı için otomatik
    urllib opener enjeksiyonundan (bkz. uyap_proxy._install_local_token_opener)
    yararlanamıyor — o yüzden AYNI kullanıcıya özel jeton DOSYASINI burada da okuyup
    isteğe kendimiz ekliyoruz; yol biçimi uyap_proxy._local_token_file() ile AYNI olmalı."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "UyapIcra", "gw_local_token")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = os.path.join(base, "uyapicra", "gw_local_token")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _istek(method, url, govde=None, timeout=30):
    data = json.dumps(govde, ensure_ascii=False).encode("utf-8") if govde is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in _HEADERS.items():
        req.add_header(k, v)
    jeton = _yerel_jeton()
    if jeton:
        req.add_header("X-Uyap-Local-Token", jeton)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            metin = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        try:
            detay = e.read().decode("utf-8", "replace")
        except Exception:
            detay = str(e)
        try:
            return e.code, json.loads(detay)
        except Exception:
            return e.code, {"error": detay}
    except urllib.error.URLError as e:
        raise OfisUlasilamiyor(
            "Yerel ofis bağlantısına (127.0.0.1:8800) ulaşılamadı. UYAP Bağlantısı "
            f"(Paylaş/Al) açık mı? Ayrıntı: {getattr(e, 'reason', e)}")
    try:
        return status, json.loads(metin)
    except Exception:
        return status, {"raw": metin}


def is_baslat(jtype, params, timeout=30):
    """Yeni iş başlatır; iş kaydını (dict: id/status/...) döndürür."""
    status, veri = _istek("POST", JOBS_URL, {"type": jtype, "params": params}, timeout)
    if status != 201:
        raise RuntimeError(veri.get("error") or f"İş başlatılamadı (HTTP {status}).")
    return veri.get("job") or {"id": veri.get("id")}


def is_durum(job_id, timeout=30):
    """İşin tam kaydını (logs/progress/result/pending_approval dâhil) döndürür."""
    status, veri = _istek("GET", f"{JOBS_URL}/{job_id}", None, timeout)
    if status != 200:
        raise RuntimeError(veri.get("error") or f"İş durumu alınamadı (HTTP {status}).")
    return veri.get("job") or {}


def is_liste(timeout=30):
    """TÜM işleri (özet, loglar HARİÇ) döndürür — kullanıcı bulgusu (2026-08-14):
    aynı dosya için art arda birden çok gönderim işi tetiklenmiş (pencere
    kaybolunca kullanıcı butona tekrar tekrar basmış), hepsi sırada/onay
    beklemede birikmişti. Yeni bir iş başlatmadan ÖNCE aynı türden bekleyen
    bir iş olup olmadığını kontrol etmek için kullanılır."""
    status, veri = _istek("GET", JOBS_URL, None, timeout)
    if status != 200:
        raise RuntimeError(veri.get("error") or f"İş listesi alınamadı (HTTP {status}).")
    return veri.get("jobs") or []


def is_iptal(job_id, timeout=30):
    status, veri = _istek("POST", f"{JOBS_URL}/{job_id}/cancel", {}, timeout)
    return veri.get("job") or {}


def is_onayla(job_id, karar, timeout=30):
    """Onay yanıtı gönderir. karar: {"decision":"approve"|"skip"|"cancel", "selection":[...]}."""
    status, veri = _istek("POST", f"{JOBS_URL}/{job_id}/approve", karar, timeout)
    if status != 200:
        raise RuntimeError(veri.get("error") or f"Onay gönderilemedi (HTTP {status}).")
    return veri.get("job") or {}


def ofis_erisilebilir(timeout=4):
    """Ofis kontrol düzlemine (iş kuyruğu listesi) hafif bir GET atar; (ok, mesaj) döner.
    UYAP'a hiç gitmez — yalnızca yerel bağlantının ayakta olup olmadığını söyler."""
    try:
        status, veri = _istek("GET", JOBS_URL, None, timeout)
    except OfisUlasilamiyor as e:
        return False, str(e)
    if status == 200:
        turler = veri.get("types") or []
        return True, ("coklu_takip_ac" in turler) and "hazır" or "bağlı (iş türü bulunamadı)"
    return False, veri.get("error") or f"Beklenmeyen yanıt (HTTP {status})."
