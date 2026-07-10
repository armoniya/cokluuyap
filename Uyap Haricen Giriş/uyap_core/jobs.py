#!/usr/bin/env python3
"""
UYAP İş Kuyruğu (job queue) — ofis tarafı çekirdeği
---------------------------------------------------
Tek bir HTTP istek/cevabıyla bitmeyen, uzun süren / toplu UYAP işlerini (çoklu takip açma,
dava açma, dosya sorgulayıp indirme vb.) arka planda yürütür; ilerleme, iptal ve hata
raporlamayı tek tipte sunar. GUI (büyük proje) bunları SADECE HTTP ile sürer; bu yüzden
home_client'a HİÇ dokunmaya gerek yoktur — istek mevcut tünelden ofise gelir, office_agent
kontrol-düzlemi yollarını UYAP'a iletmek yerine buraya yönlendirir.

Kontrol düzlemi (hepsi proxy origin'ine, yani tünelin diğer ucuna gider):

    POST /__uyap_agent__/jobs              {"type": "...", "params": {...}}  → 201 {id, job}
    GET  /__uyap_agent__/jobs              → {jobs:[...özet...], types:[...]}
    GET  /__uyap_agent__/jobs/<id>         → {job}  (loglar dahil tam kayıt)
    POST /__uyap_agent__/jobs/<id>/cancel  → {job}  (işbirlikçi iptal işareti)

Tasarım notları:
  • İş türleri EKLENTİ gibidir: job_handlers.py içinde @job_type("ad") ile kaydedilir.
    Bu modül SADECE altyapıdır; UYAP'a özgü iş mantığı job_handlers.py'dedir.
  • Her işleyici async'tir ve bir JobContext alır: ctx.uyap(...) ile UYAP'a konuşur (yazma
    işlemleri paylaşılan UYAP_WRITE_LOCK'tan geçer), ctx.progress/ctx.log ile durum bildirir,
    ctx.check_cancel() ile iptal noktası koyar.
  • Kalıcılık YOK (şimdilik): işler bellekte tutulur, ajan yeniden başlayınca sıfırlanır.
    Gerekirse ileride JobManager'a disk kalıcılığı eklenebilir (bkz. TODO).
  • İndirilen dosyalar: işleyici sonucunda küçük meta döndürür; büyük binary'nin alan tarafa
    aktarımı ayrı bir "dosya transfer" mesaj tipiyle eklenecek (bkz. TODO).
"""

import os
import time
import json
import uuid
import asyncio
import traceback
import urllib.parse

from . import uyap_proxy
from ._runtime import batch_write, WRITE_METHODS
from .p2p_wire import AGENT_PREFIX

# Kontrol düzlemi yol öneki (tam): AGENT_PREFIX + "/jobs".
JOBS_PREFIX = AGENT_PREFIX + "/jobs"

# UDF e-imza köprüsü: istemci ham `.udf` POST eder, ofis kartla imzalayıp imzalı `.udf`
# (binary) döndürür. JSON iş kuyruğundan ayrı, ikili gövdeli tek-atımlık bir uçtur.
SIGN_UDF_PATH = AGENT_PREFIX + "/sign_udf"

# İş durumları.
QUEUED = "queued"        # sıraya alındı, henüz başlamadı
RUNNING = "running"      # işleniyor
AWAITING_APPROVAL = "awaiting_approval"  # istemci onayı bekleniyor (kullanıcı ekranında)
DONE = "done"           # başarıyla bitti
ERROR = "error"         # hata ile bitti
CANCELLED = "cancelled"  # iptal edildi

# Kayıtlı iş türleri: ad -> async işleyici(ctx). job_handlers.py @job_type ile doldurur.
_HANDLERS = {}


def job_type(name):
    """İşleyiciyi bir iş türü adına bağlayan dekoratör.

        @job_type("dosya_sorgula")
        async def _handler(ctx): ...
    """
    def deco(fn):
        _HANDLERS[name] = fn
        return fn
    return deco


def registered_types():
    return sorted(_HANDLERS)


class JobCancelled(Exception):
    """İşleyici, ctx.check_cancel() ile iptal isteğini gördüğünde bunu fırlatır."""
    pass


class Job:
    """Tek bir işin durumu. JSON'a çevrilebilir alanlar GUI'ye olduğu gibi gider."""

    def __init__(self, jtype, params):
        self.id = uuid.uuid4().hex
        self.type = jtype
        self.params = params or {}
        self.status = QUEUED
        self.progress = {"done": 0, "total": None, "message": ""}
        self.result = None
        self.error = None
        self.logs = []          # [{ts, line}]
        self.created = time.time()
        self.updated = self.created
        self._cancel = False    # işbirlikçi iptal işareti
        self._task = None       # asyncio.Task
        # ── İstemci onayı (awaiting_approval) ──
        # İşleyici ctx.request_approval(payload) çağırınca pending_approval doldurulur ve
        # iş, istemci POST .../approve yollayana kadar _approval_event üzerinde bekler.
        self.pending_approval = None      # istemciye gösterilecek özet (JSON)
        self._approval_event = asyncio.Event()
        self._approval_response = None    # istemcinin kararı: {decision, selection, ...}

    def touch(self):
        self.updated = time.time()

    def to_dict(self, with_logs=True):
        d = {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "params": self.params,
            "pending_approval": self.pending_approval,
            "created": self.created,
            "updated": self.updated,
        }
        if with_logs:
            d["logs"] = self.logs
        return d


class JobContext:
    """İşleyiciye verilen yardımcı: UYAP erişimi + ilerleme/iptal raporlama."""

    def __init__(self, job):
        self.job = job
        self.params = job.params

    # ── İlerleme / log / iptal ────────────────────────────────────────────────
    def check_cancel(self):
        """İşleyici, güvenli noktalarda bunu çağırmalı; iptal istendiyse JobCancelled fırlatır."""
        if self.job._cancel:
            raise JobCancelled()

    def log(self, line):
        self.job.logs.append({"ts": time.time(), "line": str(line)})
        # Belleği sınırla: son 500 satırı tut.
        if len(self.job.logs) > 500:
            del self.job.logs[:-500]
        self.job.touch()

    def progress(self, done=None, total=None, message=None):
        if done is not None:
            self.job.progress["done"] = done
        if total is not None:
            self.job.progress["total"] = total
        if message is not None:
            self.job.progress["message"] = message
        self.job.touch()

    # ── İstemci onayı (kullanıcı ekranında) ────────────────────────────────────
    async def request_approval(self, payload):
        """İşi DURAKLATIP istemciden onay ister. payload, istemciye gösterilecek özettir
        (ör. takip bilgileri + harçlar). İstemci POST .../jobs/<id>/approve ile yanıt verince
        kararı (dict: {decision, selection, ...}) döndürür.

        decision == "cancel" ise işi iptal eder (JobCancelled). "approve"/"skip"/seçim
        bilgisi olduğu gibi işleyiciye döner. Üç onay modunda da (yok/tek_tek/toplu) bu
        tek mekanizma kullanılır; 'yok' modunda işleyici bunu hiç çağırmaz."""
        job = self.job
        job.pending_approval = payload
        job._approval_response = None
        job._approval_event.clear()
        job.status = AWAITING_APPROVAL
        job.touch()
        await job._approval_event.wait()
        # Uyandık: iptal mi edildi yoksa karar mı geldi?
        self.check_cancel()                       # _cancel ise JobCancelled
        resp = job._approval_response or {}
        job.pending_approval = None
        job._approval_response = None
        job.status = RUNNING
        job.touch()
        if resp.get("decision") == "cancel":
            job._cancel = True
            raise JobCancelled()
        return resp

    # ── UYAP erişimi ──────────────────────────────────────────────────────────
    async def uyap(self, method, path, *, params=None, json=None, data=None,
                   content=None, files=None, headers=None, write=None, relogin=True):
        """UYAP'a tek bir istek atar ve httpx.Response döndürür. Anlık proxy istekleriyle
        AYNI kimlikli oturumu (uyap_proxy.gw) ve AYNI yazma kilidini paylaşır.

        files: multipart/form-data için httpx 'files' sözlüğü. Düz metin alanları
               (None, "deger") tuple'ı ile, dosyalar (adi, baytlar, mime) ile verilir
               (bkz. uyap_core.mts.evrak.items_kur ile üretilen alanlar).
        write: None ise metoda göre otomatik belirlenir (POST/PUT/PATCH/DELETE → yazma).
               Yazma işlemleri DÜŞÜK öncelikli batch_write() geçidinden geçer: her adımdan
               önce bekleyen bireysel (anlık) kullanıcı isteğine yol verir, sonra devam eder.
        relogin: UYAP "giriş duvarı" döndürürse bir kez yeniden giriş yapıp tekrar dener.
        """
        gw = uyap_proxy.gw
        if gw is None:
            raise RuntimeError("UYAP oturumu hazır değil (gw=None). Ofis ajanı giriş yapmadan iş çalıştırılamaz.")
        if write is None:
            write = method.upper() in WRITE_METHODS

        url = f"{uyap_proxy.UYAP_BASE}/{path.lstrip('/')}"
        hdrs = {"Referer": f"{uyap_proxy.UYAP_BASE}/"}
        if headers:
            hdrs.update(headers)

        async def _do():
            resp = await gw.client.request(
                method, url, params=params, json=json, data=data,
                content=content, files=files, headers=hdrs)
            if relogin and uyap_proxy._looks_like_login_wall(resp):
                await gw.relogin()
                resp = await gw.client.request(
                    method, url, params=params, json=json, data=data,
                    content=content, files=files, headers=hdrs)
            return resp

        gw.touch()
        if write:
            async with batch_write():
                return await _do()
        return await _do()


class JobManager:
    """Bellek-içi iş kayıt defteri + zamanlayıcı. Eşzamanlı çalışan iş sayısını bir
    semafor ile sınırlar (UYAP'ı boğmamak için). Yazma çakışmaları zaten UYAP_WRITE_LOCK
    ile korunur; semafor ek olarak toplam yükü dengeler."""

    def __init__(self, max_concurrent=2):
        self.jobs = {}          # id -> Job
        self._order = []        # gönderim sırasıyla id listesi
        self._sem = asyncio.Semaphore(max_concurrent)

    def submit(self, jtype, params):
        if jtype not in _HANDLERS:
            raise KeyError(jtype)
        job = Job(jtype, params)
        self.jobs[job.id] = job
        self._order.append(job.id)
        job._task = asyncio.ensure_future(self._run(job))
        return job

    async def _run(self, job):
        async with self._sem:
            if job._cancel:
                job.status = CANCELLED
                job.touch()
                return
            job.status = RUNNING
            job.touch()
            ctx = JobContext(job)
            handler = _HANDLERS[job.type]
            try:
                job.result = await handler(ctx)
                job.status = DONE if not job._cancel else CANCELLED
            except JobCancelled:
                job.status = CANCELLED
                ctx.log("İş iptal edildi.")
            except asyncio.CancelledError:
                job.status = CANCELLED
                raise
            except Exception as e:
                job.status = ERROR
                job.error = f"{type(e).__name__}: {e}"
                ctx.log("HATA:\n" + traceback.format_exc())
            finally:
                job.touch()

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job._cancel = True          # işbirlikçi: işleyici check_cancel() ile görecek
        if job.status == QUEUED and job._task is not None:
            # Henüz başlamadıysa görevi de iptal et (semaforu beklerken).
            job._task.cancel()
        # Onay beklerken iptal edildiyse bekleyen işi uyandır (request_approval içinde
        # check_cancel JobCancelled fırlatacak).
        job._approval_event.set()
        job.touch()
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)

    def listing(self):
        return [self.jobs[i].to_dict(with_logs=False) for i in self._order if i in self.jobs]


# Süreç başına tek yönetici.
MANAGER = JobManager()

_JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}


def _resp(status, obj):
    return status, dict(_JSON_HEADERS), json.dumps(obj, ensure_ascii=False).encode("utf-8")


def is_control_path(path):
    """Verilen HTTP yolu ofis kontrol düzlemine mi ait? (UYAP'a İLETİLMEZ.)"""
    p = (path or "").lstrip("/")
    return (p == JOBS_PREFIX or p.startswith(JOBS_PREFIX + "/")
            or p == SIGN_UDF_PATH)


async def _handle_sign_udf(method, query, body):
    """POST /__uyap_agent__/sign_udf?name=<dosyaadı> — yüklenen belgeyi (UDF/Word/PDF)
    gerekiyorsa UDF'ye çevirip ofisteki e-imzayla imzalar; imzalı `.udf`'i binary döndürür.
    PIN/sertifika aktif UYAP oturumundan gelir (headless, popup yok)."""
    if method != "POST":
        return _resp(405, {"error": "Yöntem desteklenmiyor (POST bekleniyor)."})
    if not body:
        return _resp(400, {"error": "Boş gövde: imzalanacak belge gönderilmedi."})

    gw = uyap_proxy.gw
    if gw is None:
        return _resp(503, {"error": "UYAP oturumu hazır değil (ofis giriş yapmadı)."})
    cert_id = getattr(gw, "cert_id", None)
    pin = getattr(getattr(gw, "login_args", None), "pin", None)

    # Dosya adı/uzantı query'den gelir (Türkçe karakterler için yüzde-kodlu olabilir).
    qs = urllib.parse.parse_qs(query or "")
    filename = urllib.parse.unquote((qs.get("name") or [""])[0]) or ".udf"
    base = os.path.splitext(os.path.basename(filename))[0] or "belge"
    # Content-Disposition başlığı ASCII olmalı; Türkçe karakterleri at.
    out_name = (base.encode("ascii", "ignore").decode() or "belge") + "_imzali.udf"

    from . import udf_signer
    try:
        loop = asyncio.get_running_loop()
        signed = await loop.run_in_executor(
            None, udf_signer.sign_document, body, filename, cert_id, pin)
    except udf_signer.SignError as e:
        return _resp(400, {"error": str(e)})
    except Exception as e:
        return _resp(502, {"error": f"İmzalama hatası: {type(e).__name__}: {e}"})

    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": 'attachment; filename="%s"' % out_name,
        "Cache-Control": "no-store",
    }
    return 200, headers, signed


async def handle_control(method, path, query, body):
    """Kontrol-düzlemi HTTP isteğini işler; (status, headers, body) döndürür (proxy formatı)."""
    method = (method or "GET").upper()
    p = (path or "").lstrip("/")

    # UDF e-imza köprüsü iş kuyruğundan ayrı (ikili gövde): en başta ele al.
    if p == SIGN_UDF_PATH:
        return await _handle_sign_udf(method, query, body)

    rest = p[len(JOBS_PREFIX):].lstrip("/")   # "" | "<id>" | "<id>/cancel"

    try:
        if rest == "":
            if method == "GET":
                return _resp(200, {"jobs": MANAGER.listing(), "types": registered_types()})
            if method == "POST":
                try:
                    payload = json.loads(body or b"{}")
                except Exception:
                    return _resp(400, {"error": "Geçersiz JSON gövdesi."})
                jtype = payload.get("type")
                jparams = payload.get("params") or {}
                if jtype not in _HANDLERS:
                    return _resp(400, {"error": f"Bilinmeyen iş türü: {jtype!r}",
                                       "types": registered_types()})
                job = MANAGER.submit(jtype, jparams)
                return _resp(201, {"id": job.id, "job": job.to_dict()})
            return _resp(405, {"error": "Yöntem desteklenmiyor."})

        parts = rest.split("/")
        job_id = parts[0]
        action = parts[1] if len(parts) > 1 else None
        job = MANAGER.get(job_id)
        if job is None:
            return _resp(404, {"error": "İş bulunamadı."})

        if action is None and method == "GET":
            return _resp(200, {"job": job.to_dict()})
        if action == "cancel" and method == "POST":
            MANAGER.cancel(job_id)
            return _resp(200, {"job": job.to_dict()})
        if action == "approve" and method == "POST":
            # İstemci onay yanıtı: {"decision": "approve"|"skip"|"cancel", "selection": [...]}
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                return _resp(400, {"error": "Geçersiz JSON gövdesi."})
            job._approval_response = payload if isinstance(payload, dict) else {"decision": "approve"}
            job._approval_event.set()    # bekleyen request_approval'ı uyandır
            return _resp(200, {"job": job.to_dict()})
        return _resp(405, {"error": "Yöntem/eylem desteklenmiyor."})

    except Exception as e:
        return _resp(500, {"error": f"{type(e).__name__}: {e}"})


# İş türü işleyicilerini kaydet (job_type dekoratörü yukarıda tanımlı olduğundan güvenli).
from . import job_handlers  # noqa: E402,F401
