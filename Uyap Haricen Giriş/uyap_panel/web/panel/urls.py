from django.urls import path
from . import views

app_name = "panel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("api/reset", views.api_reset, name="api_reset"),

    # ── Bağlantı API'si ──
    path("api/status", views.api_status, name="api_status"),
    path("api/logs", views.api_logs, name="api_logs"),
    path("api/share/start", views.api_share_start, name="api_share_start"),
    path("api/share/stop", views.api_share_stop, name="api_share_stop"),
    path("api/receive/start", views.api_receive_start, name="api_receive_start"),
    path("api/receive/stop", views.api_receive_stop, name="api_receive_stop"),
    path("api/open-browser", views.api_open_browser, name="api_open_browser"),

    # ── Takip API'si (İcra & MTS ortak; kind ile ayrılır) ──
    path("api/takip/parse", views.api_takip_parse, name="api_takip_parse"),
    path("api/takip/start", views.api_takip_start, name="api_takip_start"),
    path("api/takip/status", views.api_takip_status, name="api_takip_status"),
    path("api/takip/cancel", views.api_takip_cancel, name="api_takip_cancel"),
    path("api/takip/approve", views.api_takip_approve, name="api_takip_approve"),

    # ── SGK Sorgu API'si ──
    path("api/sgk/upload", views.api_sgk_upload, name="api_sgk_upload"),
    path("api/sgk/start", views.api_sgk_start, name="api_sgk_start"),
    path("api/sgk/status", views.api_sgk_status, name="api_sgk_status"),
    path("api/sgk/pause", views.api_sgk_pause, name="api_sgk_pause"),
    path("api/sgk/stop", views.api_sgk_stop, name="api_sgk_stop"),
    path("api/sgk/download", views.api_sgk_download, name="api_sgk_download"),
]
