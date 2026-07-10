"""
panel.store — Oturum başına ayrıştırılmış takip + belge tamponu (RAM içi).

Ayrıştırılmış takip listeleri ve PDF'ler (b64) büyük olabileceğinden oturum
dosyasına değil, süreç-içi bir sözlüğe konur. Yerel tek-kullanıcı panel olduğu
için bu yeterlidir; süreç kapanınca temizlenir.
"""

import threading

_LOCK = threading.Lock()
# session_key -> {"icra": {...}, "mts": {...}}
_STORE = {}
# session_key -> SgkBatch (RAM içi; süreç kapanınca temizlenir)
_SGK = {}


def _bucket(session_key, kind):
    with _LOCK:
        s = _STORE.setdefault(session_key, {})
        return s.setdefault(kind, {"takipler": [], "vekalet": None, "dayanak": None,
                                   "job_id": None})


def get(session_key, kind):
    return _bucket(session_key, kind)


def set_takipler(session_key, kind, takipler):
    b = _bucket(session_key, kind)
    b["takipler"] = takipler


def set_doc(session_key, kind, which, attachment):
    b = _bucket(session_key, kind)
    b[which] = attachment


def set_job(session_key, kind, job_id):
    b = _bucket(session_key, kind)
    b["job_id"] = job_id


def set_sgk(session_key, batch):
    with _LOCK:
        _SGK[session_key] = batch


def get_sgk(session_key):
    with _LOCK:
        return _SGK.get(session_key)


def clear(session_key):
    with _LOCK:
        _STORE.pop(session_key, None)
        b = _SGK.pop(session_key, None)
    if b is not None:
        try:
            b.durdur()
        except Exception:
            pass
