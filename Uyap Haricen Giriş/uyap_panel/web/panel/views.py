"""
panel.views — UYAP Ağ Geçidi yerel paneli (Django) görünümleri.

Sayfalar:  /login, /logout, /  (dashboard, sekmeli)
API (JSON, POST/GET):
  Bağlantı : /api/status /api/logs /api/share/* /api/receive/* /api/open-browser
  Takip    : /api/takip/{parse,start,status,cancel,approve}

Tüm UYAP işleri ortak çekirdek (uyap_panel.core) üzerinden yürür; bu modül yalnızca
HTTP ↔ çekirdek köprüsüdür. Düşük katman bloklayan çağrılar Django'nun çok-iş-parçacıklı
geliştirme sunucusunda güvenle çalışır (her istek kendi thread'inde).
"""

import os
import json
import tempfile
import functools

from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_GET

from uyap_panel.core import (
    load_config, save_config, decrypt_secret, encrypt_secret,
    verify_credentials, reset_request, get_manager, TakipService, PORT, DEFAULT_SERVER_URL,
    SgkBatch, SORGULAR, ANAHTARLAR,
)
from . import store

manager = get_manager()


# ── yardımcılar ───────────────────────────────────────────────────────────────
def _server_url():
    return load_config().get("server_url", DEFAULT_SERVER_URL)


def _creds(request):
    return request.session.get("username"), request.session.get("password")


def _ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _service(request):
    user, pw = _creds(request)
    return TakipService(user, pw, PORT)


def login_required_api(view):
    @functools.wraps(view)
    def wrapper(request, *a, **k):
        if not request.session.get("username"):
            return JsonResponse({"error": "Oturum yok. Lütfen giriş yapın."}, status=401)
        return view(request, *a, **k)
    return wrapper


def login_required_page(view):
    @functools.wraps(view)
    def wrapper(request, *a, **k):
        if not request.session.get("username"):
            return redirect(reverse("panel:login"))
        return view(request, *a, **k)
    return wrapper


def _body(request):
    try:
        return json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        return {}


# ── kimlik ──────────────────────────────────────────────────────────────────────
def login_view(request):
    if request.session.get("username"):
        return redirect(reverse("panel:dashboard"))

    cfg = load_config()
    ctx = {"username": cfg.get("username", ""), "office_code": cfg.get("office_code", ""),
           "error": "", "remember": cfg.get("remember", False)}

    if request.method == "POST":
        office = request.POST.get("office_code", "").strip()
        user = request.POST.get("username", "").strip()
        pw = request.POST.get("password", "").strip()
        remember = request.POST.get("remember") == "on"
        if not office or not user or not pw:
            ctx.update(username=user, office_code=office, error="Lütfen tüm alanları doldurun.")
            return render(request, "panel/login.html", ctx)

        ok, msg, info = verify_credentials(_server_url(), user, pw, office_code=office)
        if not ok:
            ctx.update(username=user, office_code=office,
                       error=f"Giriş başarısız: {msg}", remember=remember)
            return render(request, "panel/login.html", ctx)

        # Oturuma çıplak ad + ofis kodu + rol; tünel başlatma sunucuda 'ad@ofis_kodu' birleştirir.
        request.session["username"] = user
        request.session["password"] = pw
        request.session["office_code"] = office
        request.session["role"] = info.get("role", "member")
        cfg["username"] = user
        cfg["office_code"] = office
        cfg["remember"] = remember
        cfg["password_enc"] = encrypt_secret(pw) if remember else ""
        save_config(cfg)
        return redirect(reverse("panel:dashboard"))

    return render(request, "panel/login.html", ctx)


@require_POST
def api_reset(request):
    """Parola sıfırlama talebi (kimlik gerektirmez). app.py on_forgot karşılığı."""
    body = _body(request)
    ident = (body.get("identifier") or "").strip()
    if not ident:
        return JsonResponse({"error": "Kullanıcı adı veya e-posta girin."}, status=400)
    data, err = reset_request(_server_url(), ident, office_code=(body.get("office_code") or "").strip())
    if err:
        return JsonResponse({"error": err}, status=400)
    return JsonResponse({"ok": True, "sent_to": data.get("sent_to", ""),
                         "test_mode": bool(data.get("test_mode"))})


def logout_view(request):
    manager.shutdown()
    if request.session.session_key:
        store.clear(request.session.session_key)
    request.session.flush()
    return redirect(reverse("panel:login"))


@ensure_csrf_cookie
@login_required_page
def dashboard(request):
    cfg = load_config()
    ctx = {
        "username": request.session.get("username"),
        "pin": decrypt_secret(cfg.get("pin_enc", "")),
        "cert_id": cfg.get("cert_id", ""),
        "il": cfg.get("il", "İzmir"),
        "adliye": cfg.get("adliye", "İzmir"),
        "onay_modu": cfg.get("onay_modu", "tek_tek"),
        "giris_url": manager.giris_url(PORT),
        "port": PORT,
        "sgk_sorgular": [{"baslik": b, "anahtar": a} for b, _, a in SORGULAR],
        "sgk_kolonlar": ["No", "Ad Soyad", "Birim", "Dosya No"] + [s[0] for s in SORGULAR],
    }
    return render(request, "panel/dashboard.html", ctx)


# ── bağlantı API'si ──────────────────────────────────────────────────────────────
@require_GET
@login_required_api
def api_status(request):
    return JsonResponse(manager.status())


@require_GET
@login_required_api
def api_logs(request):
    try:
        since = int(request.GET.get("since", 0))
    except ValueError:
        since = 0
    total, lines = manager.logs_since(since)
    return JsonResponse({"total": total, "lines": lines})


@require_POST
@login_required_api
def api_share_start(request):
    data = _body(request)
    pin = (data.get("pin") or "").strip()
    cert_id = (data.get("cert_id") or "").strip()
    user, pw = _creds(request)
    # PIN / sertifika ID'sini (paylaşılan) yapılandırmaya kaydet.
    cfg = load_config()
    cfg["pin_enc"] = encrypt_secret(pin)
    cfg["cert_id"] = cert_id
    save_config(cfg)
    ok, msg = manager.start_sharing(server_url=_server_url(), username=user, password=pw,
                                    pin=pin, cert_id=cert_id or None,
                                    office_code=request.session.get("office_code", ""))
    return JsonResponse({"ok": ok, "message": msg, "status": manager.status()},
                        status=200 if ok else 400)


@require_POST
@login_required_api
def api_share_stop(request):
    manager.stop_sharing()
    return JsonResponse({"ok": True, "status": manager.status()})


@require_POST
@login_required_api
def api_receive_start(request):
    user, pw = _creds(request)
    ok, msg = manager.start_receiving(server_url=_server_url(), username=user, password=pw,
                                      office_code=request.session.get("office_code", ""))
    return JsonResponse({"ok": ok, "message": msg, "status": manager.status()},
                        status=200 if ok else 400)


@require_POST
@login_required_api
def api_receive_stop(request):
    manager.stop_receiving()
    return JsonResponse({"ok": True, "status": manager.status()})


@require_POST
@login_required_api
def api_open_browser(request):
    ok, info = manager.open_browser(PORT)
    return JsonResponse({"ok": ok, "url": info})


# ── takip API'si ──────────────────────────────────────────────────────────────────
def _valid_kind(kind):
    return kind in ("icra", "mts")


@require_POST
@login_required_api
def api_takip_parse(request):
    """multipart: file=<xml/xlsx>, kind=icra|mts, doc=(opsiyonel: vekalet/dayanak)."""
    kind = request.POST.get("kind", "")
    if not _valid_kind(kind):
        return JsonResponse({"error": "Geçersiz takip türü."}, status=400)
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"error": "Dosya yok."}, status=400)

    skey = _ensure_session_key(request)
    doc = request.POST.get("doc", "")   # "" → kaynak; "vekalet"/"dayanak" → belge

    suffix = os.path.splitext(upload.name)[1] or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.close()

        if doc in ("vekalet", "dayanak"):
            att = {"filename": upload.name, "b64": TakipService.file_to_b64(tmp.name)}
            store.set_doc(skey, kind, doc, att)
            return JsonResponse({"ok": True, "doc": doc, "filename": upload.name})

        takipler, err = TakipService.parse_source(tmp.name, kind=kind)
        if err:
            return JsonResponse({"error": f"Dosya ayrıştırılamadı: {err}"}, status=400)
        store.set_takipler(skey, kind, takipler)
        onek = [str(t.get("dosya_no")) for t in takipler[:20]]
        return JsonResponse({"ok": True, "count": len(takipler), "filename": upload.name,
                             "dosya_nolar": onek, "fazla": len(takipler) > 20})
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@require_POST
@login_required_api
def api_takip_start(request):
    data = _body(request)
    kind = data.get("kind", "")
    if not _valid_kind(kind):
        return JsonResponse({"error": "Geçersiz takip türü."}, status=400)
    if not manager.is_connected():
        return JsonResponse({"error": "Önce 'Bağlantı' sekmesinden Paylaş ya da Al ile "
                                      "bağlantıyı başlatın."}, status=400)

    skey = _ensure_session_key(request)
    bucket = store.get(skey, kind)
    takipler = bucket.get("takipler") or []
    if not takipler:
        return JsonResponse({"error": "Önce bir XML/Excel kaynağı seçin."}, status=400)

    il = (data.get("il") or "İzmir").strip()
    adliye = (data.get("adliye") or "İzmir").strip()
    onay_modu = data.get("onay_modu") or "tek_tek"

    cfg = load_config()
    cfg.update(il=il, adliye=adliye, onay_modu=onay_modu)
    save_config(cfg)

    svc = _service(request)
    params = svc.build_params(takipler, il=il, adliye=adliye, onay_modu=onay_modu,
                              vekalet=bucket.get("vekalet"), dayanak=bucket.get("dayanak"))
    job_id, err = svc.submit(kind, params)
    if err:
        return JsonResponse({"error": err}, status=400)
    store.set_job(skey, kind, job_id)
    return JsonResponse({"ok": True, "job_id": job_id, "count": len(takipler)})


@require_GET
@login_required_api
def api_takip_status(request):
    job_id = request.GET.get("job_id")
    if not job_id:
        return JsonResponse({"error": "job_id gerekli."}, status=400)
    job, err = _service(request).get(job_id)
    if err:
        return JsonResponse({"error": err}, status=400)
    return JsonResponse({"job": job})


@require_POST
@login_required_api
def api_takip_cancel(request):
    data = _body(request)
    job_id = data.get("job_id")
    if not job_id:
        return JsonResponse({"error": "job_id gerekli."}, status=400)
    _service(request).cancel(job_id)
    return JsonResponse({"ok": True})


@require_POST
@login_required_api
def api_takip_approve(request):
    data = _body(request)
    job_id = data.get("job_id")
    decision = data.get("decision")
    selection = data.get("selection")
    if not job_id or not decision:
        return JsonResponse({"error": "job_id ve decision gerekli."}, status=400)
    _service(request).approve(job_id, decision, selection)
    return JsonResponse({"ok": True})


# ── SGK Sorgu API'si ──────────────────────────────────────────────────────────────
@require_POST
@login_required_api
def api_sgk_upload(request):
    """multipart: file=<xlsx>. Excel'i kalıcı bir geçici yola yazar, SgkBatch kurar."""
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"error": "Dosya yok."}, status=400)
    skey = _ensure_session_key(request)

    # Önceki batch'i (çalışıyorsa) durdur ve değiştir.
    eski = store.get_sgk(skey)
    if eski is not None:
        try:
            eski.durdur()
        except Exception:
            pass

    suffix = os.path.splitext(upload.name)[1] or ".xlsx"
    tmpdir = tempfile.mkdtemp(prefix="uyap_sgk_")
    # upload.name istemciden gelir (multipart); "../" ile tmpdir dışına yazılmasın diye
    # her iki ayırıcı da normalize edilip yalnız dosya adı kısmı kullanılır.
    safe_name = os.path.basename(upload.name.replace("\\", "/")) or ("dosya" + suffix)
    path = os.path.join(tmpdir, safe_name)
    with open(path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)
    try:
        batch = SgkBatch(path, port=PORT)
        rows = batch.yukle()
    except Exception as e:
        return JsonResponse({"error": f"Excel açılamadı: {e}"}, status=400)
    store.set_sgk(skey, batch)
    return JsonResponse({"ok": True, "filename": upload.name, "rows": rows,
                         "kolonlar": ["No", "Ad Soyad", "Birim", "Dosya No"]
                                     + [s[0] for s in SORGULAR]})


@require_POST
@login_required_api
def api_sgk_start(request):
    data = _body(request)
    mode = data.get("mode") or "normal"
    secili = data.get("secili") or []
    skey = _ensure_session_key(request)
    batch = store.get_sgk(skey)
    if batch is None:
        return JsonResponse({"error": "Önce bir Excel dosyası yükleyin."}, status=400)
    if not manager.is_connected():
        return JsonResponse({"error": "Önce 'UYAP Bağlantısı'ndan Paylaş ya da Al ile "
                                      "bağlantıyı başlatın."}, status=400)
    secili = [a for a in secili if a in ANAHTARLAR]
    if not secili:
        return JsonResponse({"error": "En az bir sorgu sütunu seçin."}, status=400)
    if mode == "retry" and batch.hata_sayisi(set(secili)) == 0:
        return JsonResponse({"error": "Tekrar denenecek hatalı hücre yok."}, status=400)
    if not batch.basla(secili, mode=mode):
        return JsonResponse({"error": "Sorgu başlatılamadı (zaten çalışıyor olabilir)."}, status=400)
    return JsonResponse({"ok": True})


@require_GET
@login_required_api
def api_sgk_status(request):
    skey = _ensure_session_key(request)
    batch = store.get_sgk(skey)
    if batch is None:
        return JsonResponse({"error": "Yüklü dosya yok."}, status=400)
    try:
        since_seq = int(request.GET.get("seq", 0))
    except ValueError:
        since_seq = 0
    try:
        since_log = int(request.GET.get("log", 0))
    except ValueError:
        since_log = 0
    return JsonResponse(batch.snapshot(since_seq=since_seq, since_log=since_log))


@require_POST
@login_required_api
def api_sgk_pause(request):
    data = _body(request)
    skey = _ensure_session_key(request)
    batch = store.get_sgk(skey)
    if batch is None:
        return JsonResponse({"error": "Yüklü dosya yok."}, status=400)
    if data.get("paused"):
        batch.duraklat()
    else:
        batch.devam()
    return JsonResponse({"ok": True, "paused": batch.paused})


@require_POST
@login_required_api
def api_sgk_stop(request):
    skey = _ensure_session_key(request)
    batch = store.get_sgk(skey)
    if batch is not None:
        batch.durdur()
    return JsonResponse({"ok": True})


@require_GET
@login_required_api
def api_sgk_download(request):
    skey = _ensure_session_key(request)
    batch = store.get_sgk(skey)
    if batch is None:
        raise Http404("Yüklü dosya yok.")
    which = request.GET.get("which", "tam")
    path = batch.ozet_yolu if which == "ozet" else batch.excel_yolu
    if not path or not os.path.exists(path):
        raise Http404("Çıktı dosyası henüz yok.")
    return FileResponse(open(path, "rb"), as_attachment=True,
                        filename=os.path.basename(path))
