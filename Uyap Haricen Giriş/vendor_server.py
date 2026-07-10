#!/usr/bin/env python3
"""
UYAP Satıcı Sunucusu (vendor_server.py) — TEK parça, bedava PaaS'a deploy edilir
-------------------------------------------------------------------------------
İki işi TEK aiohttp servisinde, TEK portta birleştirir:

  1. `webapp/` statik kabuğunu (index.html + Service Worker + tünel JS) HTTP(S) ile servis
     eder. Tarayıcı bunu bir kez indirir; sonrası Service Worker ile P2P tüneldir.
  2. `/ws` adresinde buluşturma (signaling): ofis ajanı ve tarayıcı aynı oda anahtarıyla
     buluşur, WebRTC el sıkışması (SDP offer/answer) aktarılır.

KASITLI olarak UYAP verisi taşımaz — o veri ofis ile tarayıcı arasında DOĞRUDAN (P2P,
DTLS) akar. Bu sunucu yalnızca statik dosya + SDP taşır. Bu yüzden ucuz/bedava bir kutuda
çalışır ve "satıcı veri yolunda değil" güvencesi korunur.

Neden tek parça: müşterinin/satıcının kendi sunucusu yok; Render/Fly gibi bedava bir PaaS'a
TEK servis olarak deploy edip ÜCRETSIZ HTTPS adı (ör. https://uyap-x.onrender.com) almak en
kolayı. Service Worker HTTPS ister; PaaS bunu hazır verir, alan adı GEREKMEZ.

Çalıştırma (yerel test):
    pip install aiohttp
    python vendor_server.py --host 127.0.0.1 --port 8080
    # Ofis: python office_agent.py --signaling ws://127.0.0.1:8080/ws --room test123
    # Tarayıcı: http://127.0.0.1:8080/?room=test123

Çalıştırma (PaaS/üretim): PORT ortam değişkeni PaaS tarafından verilir; host 0.0.0.0.
    python vendor_server.py            # host=0.0.0.0, port=$PORT
    # Ofis: python office_agent.py --signaling wss://<app>.onrender.com/ws --room <ODA>
    # Müvekkil: https://<app>.onrender.com/?room=<ODA>
"""

import os
import ssl
import sys
import json
import time
import html
import uuid
import hmac
import random
import base64
import asyncio
import hashlib
import secrets
import argparse

from aiohttp import web, WSMsgType

import accounts

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEBAPP_DIR = os.path.join(BASE_DIR, "webapp")
CONFIG_PATH = os.path.join(BASE_DIR, "signaling_config.json")

# Hesap/lisans deposu (benzersiz oda anahtarı + parola doğrulaması). Boşsa eski davranış
# (allowlist / serbest) korunur; hesap oluşturulunca signaling oda+parola DOĞRULAR.
STORE = accounts.AccountStore()

# Admin ekranı parolası (HTTP Basic). Ayarlı değilse /admin kapalıdır.
ADMIN_PASSWORD = os.environ.get("UYAP_ADMIN_PASSWORD", "")

# ── Satış / ödeme (Kalem 4 — İSKELET) ──────────────────────────────────────────────────
# Gerçek ödeme sağlayıcısı (Iyzico/PayTR) henüz BAĞLI DEĞİL. Akış şudur:
#   /satin-al (form) → sipariş "pending"  → (ödeme onayı)  → /odeme/webhook | admin butonu
#   → create_office → giriş bilgileri e-posta ile teslim.
# provider "manual" iken ödeme onayı ADMIN PANELİNDEN elle verilir (havale/EFT vb.). Gerçek
# sağlayıcı eklenince tek yapılacak: _provider_begin()'e yönlendirme + /odeme/webhook'a imza
# doğrulaması koymak; provision mantığı (STORE.create_office) aynen kalır.
PAYMENT_PROVIDER = (os.environ.get("UYAP_PAYMENT_PROVIDER", "manual") or "manual").strip().lower()
# Ödeme sağlayıcısının provision webhook'unu doğrulayan paylaşılan gizli jeton. AYARLI DEĞİLSE
# /odeme/webhook KAPALIDIR (fail-closed) — manuel onay yalnızca admin panelinden yapılır.
PROVISION_TOKEN = os.environ.get("UYAP_PROVISION_TOKEN", "")
# Landing/fiyat sunumu (yalnızca gösterim; asıl fiyatı sağlayıcı belirler).
PLAN_NAME = (os.environ.get("UYAP_PLAN_NAME", "") or "UYAP Uzaktan Erişim — Ofis Lisansı").strip()
PLAN_PRICE = (os.environ.get("UYAP_PLAN_PRICE", "") or "").strip()  # ör. "₺750 / ay"; boşsa iletişim
# Ofis programı indirme adresi. Öncelik: (1) UYAP_DOWNLOAD_URL dış adres, (2) imaja
# gömülü MSI kurulum paketi downloads/CokluUyapKur.msi (Chrome imzasız exe'yi "şüpheli"
# diye engelliyordu; MSI standart kurulum biçimi — bkz. ux md/masaustu_kurulum_ve_arayuz_
# sorunlari.md #1), (3) geçiş için eski tek-exe kurucu, (4) daha eski zip, (5) hiçbiri
# yoksa /indir "hazırlanıyor" der.
DOWNLOAD_URL = (os.environ.get("UYAP_DOWNLOAD_URL", "") or "").strip()
_DL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
DOWNLOAD_FILE_MSI = os.path.join(_DL_DIR, "CokluUyapKur.msi")
DOWNLOAD_FILE = os.path.join(_DL_DIR, "CokluUyapKur.exe")
DOWNLOAD_FILE_ESKI = os.path.join(_DL_DIR, "CokluUyapOfis.zip")


def _download_file():
    """Servis edilecek yerel kurulum dosyası: önce MSI, yoksa tek-exe kurucu, yoksa eski zip."""
    for yol in (DOWNLOAD_FILE_MSI, DOWNLOAD_FILE, DOWNLOAD_FILE_ESKI):
        if os.path.exists(yol):
            return yol
    return ""


def _download_href():
    """/indir düğmesinin hedefi: dış URL varsa o, yoksa gömülü yerel dosya, yoksa boş."""
    if DOWNLOAD_URL:
        return DOWNLOAD_URL
    if _download_file():
        return "/indir/dosya"
    return ""
SALES_ENABLED = (os.environ.get("UYAP_SALES_ENABLED", "1") or "1").strip().lower() in ("1", "true", "yes", "on")
# Provision sonrası düz parola DİSKE YAZILMAZ. Kısa ömürlü, tek-kullanımlık "claim" jetonuyla
# (bellek içi, kalıcı değil — sıfırlama jetonları gibi) bir kez gösterilir + e-posta ile yollanır.
CRED_CLAIMS = {}          # claim_token -> {order_id, username, login, password, office_code, exp}
CRED_CLAIM_TTL = int(os.environ.get("UYAP_CRED_CLAIM_TTL", "3600"))  # 1 saat

# ── Alan adı / TLS (Kalem 5) ───────────────────────────────────────────────────────────
# Özel alan adı GEREKMEZ: *.onrender.com + otomatik TLS yeterlidir. Ama özel alan bağlarsanız:
#  • UYAP_CANONICAL_HOST ayarlıysa GET/HEAD gezinmeleri o host'a 308 ile yönlendirilir (apex→www
#    tekilleştirme + eski *.onrender.com adresini örtük kapatma). Health/ws/api/webhook hariç.
#  • HTTPS algılanınca (X-Forwarded-Proto=https) HSTS + Secure çerez OTOMATİK devreye girer.
CANONICAL_HOST = (os.environ.get("UYAP_CANONICAL_HOST", "") or "").strip().lower()
# HSTS'i kapatmak isterseniz (ör. TLS'i siz sonlandırmıyorsanız) UYAP_HSTS=0 yapın.
HSTS_ENABLED = (os.environ.get("UYAP_HSTS", "1") or "1").strip().lower() in ("1", "true", "yes", "on")

# Master self-servis paneli (/ofis) oturum çerezini imzalamak için gizli anahtar. Sabit bir
# sır ayarlanmazsa sürece özel rastgele bir anahtar üretilir (yeniden başlatınca oturumlar
# düşer — kabul edilebilir). UYAP_SESSION_SECRET verilirse oturumlar deploy'lar arası kalıcıdır.
# NOT: ADMIN_PASSWORD'e ASLA düşülmez — çerez (payload+imza) tarayıcıda açık görülebildiğinden,
# imza anahtarı parola olsaydı geçerli bir çerez ele geçiren biri parolayı çevrimdışı deneme-
# yanılmayla kırmaya çalışabilirdi (HMAC anahtar-kurtarma saldırısı).
SESSION_SECRET = os.environ.get("UYAP_SESSION_SECRET") or secrets.token_hex(32)
SESSION_COOKIE = "uyap_ofis"
SESSION_TTL = int(os.environ.get("UYAP_SESSION_TTL", "43200"))  # 12 saat

# Güvenlik raporu bulgu #6 — savunma-derinliği bileti: WebRTC/relay veri yolu (ofis ajanının
# _wire_datachannel/_serve_relay'i) bugüne kadar bu dosyanın ev→ofis mesaj yönlendirme kodunun
# doğru çalıştığına KÖRLEMESİNE güveniyordu; o yönlendirme kodunda ileride çıkabilecek bir
# regresyon (ör. bir mesajın kimlik-doğrulama damgası basmadan office'e sızması) ofis tarafında
# yakalanamıyordu. NOT: bu, "hesap deposu boşken açık-mod" senaryosunu KAPSAMAZ — STORE boşsa
# hem ofis hem ev girişi zaten aynı şekilde info=None alır (korunacak gerçek veri de yoktur);
# OPEN_MODE zaten yalnızca STORE boşken okunur, gerçek hesap varken hiç devreye girmez. Çözüm:
# ofis, KENDİ girişi gerçek STORE.authenticate() ile doğrulandıysa (yani gerçek hesaplar varsa)
# oda-özel bir anahtar alır; bu anahtarla, her home→office yönlendirilen mesaja kısa ömürlü
# imzalı bir bilet iğnelenir. Ofis, bileti kendi anahtarıyla DOĞRULAYAMADIĞI hiçbir mesajı
# işlemez. Hesap deposu boşken (dev/allowlist/açık-mod) anahtar hiç verilmez — ofis o durumda
# (mevcut davranışı bozmamak için) bilet aramaz.
def _room_ticket_key(rk: str) -> bytes:
    return hmac.new(SESSION_SECRET.encode("utf-8"), f"ticket:{rk}".encode("utf-8"), hashlib.sha256).digest()


def _mint_ticket(rk: str, cid: str, ttl: int = 120):
    """(bilet_hex, bitis_unix) — yalnızca gerçek kimlik doğrulaması geçtiğinde çağrılmalı."""
    exp = int(time.time()) + ttl
    sig = hmac.new(_room_ticket_key(rk), f"{cid}:{exp}".encode("utf-8"), hashlib.sha256).hexdigest()
    return sig, exp

# ── Owner (platform sahibi 'utku') paneli ──────────────────────────────────────────────────
# 'utku' owner hesabı ESKİ Basic-Auth /admin yerine geçer: parola env'den bootstrap edilir
# (UYAP_OWNER_PASSWORD), giriş TOTP 2FA ister, oturum imzalı ÇEREZ ile tutulur. Owner çerezi
# SameSite=Strict'tir (durum-değiştiren /admin uçları için CSRF savunması). Kaynağa parola
# GÖMÜLMEZ; TOTP gizli anahtarı ilk bootstrap'ta ÜRETİLİP bir kez konsola yazılır.
OWNER_COOKIE = "uyap_owner"
OWNER_BOOTSTRAP_PASSWORD = (os.environ.get("UYAP_OWNER_PASSWORD", "") or "").strip()


# ── Kaba-kuvvet (brute-force) / oran sınırlama ─────────────────────────────────────────────
# TÜM kimlik uçları (signaling /ws, /api/office, /ofis/login, /admin, parola sıfırlama) için
# bellek içi, IP+kategori bazlı sınırlayıcı. Tek-süreç aiohttp olduğundan bellek içi sözlük
# yeterli. Kayan pencere içinde başarısızlık sayılır; eşik üstünde ÜSTEL geri çekilme (backoff),
# daha üstte tam KİLİT. Başarılı girişte sayaç sıfırlanır. Bkz. güvenlik raporu bulgu #7.
def _client_ip(request) -> str:
    """İstemci IP'si. PaaS/ters-proxy ardında gerçek IP X-Forwarded-For'un İLK adımındadır;
    yoksa doğrudan bağlantı (request.remote). NOT: yalnızca oran sınırlama içindir — yetki
    kararı XFF'e GÜVENMEZ (bkz. #2). XFF taklidi en kötü ihtimalle saldırganın kendi sayacını
    bölmesine yarar; meşru kullanıcı tek IP'den geldiği için korunur."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.remote or "?"


class _LoginGuard:
    """IP+kategori bazlı başarısızlık sayacı + üstel geri çekilme + tam kilit."""
    WINDOW = 900       # sn: başarısızlıkların sayıldığı kayan pencere (15 dk)
    SOFT = 5           # bu kadar başarısızlıktan SONRA gecikme (backoff) başlar
    HARD = 10          # bu kadar başarısızlıkta tam KİLİT
    LOCK_SECS = 900    # tam kilit süresi (15 dk)
    BACKOFF_MAX = 300  # tek backoff beklemesinin tavanı (5 dk)

    def __init__(self):
        self._fails = {}   # key -> [ts, ...] son başarısızlık zamanları
        self._until = {}   # key -> kilit bitiş ts'i

    def _recent(self, key, now):
        arr = [t for t in self._fails.get(key, ()) if now - t < self.WINDOW]
        if arr:
            self._fails[key] = arr
        else:
            self._fails.pop(key, None)
        return arr

    def blocked_for(self, key) -> int:
        """Şu an reddedilmeli mi? Beklenmesi gereken saniye (0 = izinli)."""
        now = time.time()
        until = self._until.get(key, 0)
        if until > now:
            return int(until - now) + 1
        if until:
            self._until.pop(key, None)
        arr = self._recent(key, now)
        n = len(arr)
        if n >= self.SOFT:
            delay = min(2 ** (n - self.SOFT + 1), self.BACKOFF_MAX)  # 2,4,8,… tavan 5 dk
            wait = arr[-1] + delay - now
            if wait > 0:
                return int(wait) + 1
        return 0

    def record_fail(self, key):
        now = time.time()
        arr = self._recent(key, now)
        arr.append(now)
        self._fails[key] = arr
        if len(arr) >= self.HARD:
            self._until[key] = now + self.LOCK_SECS

    def record_ok(self, key):
        self._fails.pop(key, None)
        self._until.pop(key, None)


GUARD = _LoginGuard()


def _rate_limited(wait: int):
    """429 Too Many Requests (düz metin) — Retry-After ile."""
    return web.Response(status=429, headers={"Retry-After": str(wait), "Cache-Control": "no-store"},
                        text=f"Çok fazla deneme. {wait} sn sonra tekrar deneyin.")

# ── E-posta gönderimi (parola sıfırlama) — ŞİMDİLİK TEST/STUB ──────────────────────────────
# Gönderici (SMTP) hesabı HENÜZ KURULMADI. UYAP_SMTP_* env değişkenleri tam ayarlı DEĞİLSE
# "test modu" çalışır: gerçek mail GÖNDERİLMEZ; içerik sunucu konsoluna yazılır ve LAST_TEST_MAIL'e
# kaydedilir (yerel testte sıfırlama bağlantısını görebilmek için). Gönderici kurulunca env'leri
# doldurmak yeterli; _send_email gerçek gönderime geçer. Bkz. YAPILACAKLAR.md.
SMTP_HOST = os.environ.get("UYAP_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("UYAP_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("UYAP_SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("UYAP_SMTP_PASS", "")
SMTP_FROM = os.environ.get("UYAP_SMTP_FROM", "").strip() or (SMTP_USER or "no-reply@uyap.local")
MAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)
# Sıfırlama jetonunu/bağlantısını sunucu konsoluna YAZMAK yalnızca açık opt-in yerel/dev modunda.
# Üretimde SMTP kurulmamışsa sıfırlama akışı tamamen DEVRE DIŞIdır (jeton üretilmez/loglanmaz);
# bkz. güvenlik raporu #8. Test modu yalnızca yerel geliştirme içindir.
MAIL_TEST_MODE = (os.environ.get("UYAP_MAIL_TEST", "") or "").strip().lower() in ("1", "true", "yes", "on")
# Sıfırlama akışı yalnızca gerçek bir teslim yolu (SMTP) VEYA açık dev test modu varken çalışır.
RESET_ENABLED = MAIL_ENABLED or MAIL_TEST_MODE
LAST_TEST_MAIL = None  # test modunda "gönderilen" son mail (teşhis amaçlı, yalnızca dev)


def _send_email(to, subject, body):
    """E-posta gönderir. SMTP yapılandırılmadıysa (yalnızca açık DEV test modunda) konsola yazar.
    Üretimde test modu KAPALI olduğundan bu dal hiç çalışmaz (çağıran sıfırlamayı zaten engeller)."""
    global LAST_TEST_MAIL
    if not MAIL_ENABLED:
        if not MAIL_TEST_MODE:
            # Güvenlik: jetonu ASLA loglama. Üretimde buraya düşülmemeli (RESET_ENABLED False).
            print("[MAIL] SMTP yapılandırılmamış ve test modu kapalı → mail GÖNDERİLMEDİ.")
            return False
        LAST_TEST_MAIL = {"to": to, "subject": subject, "body": body, "at": int(time.time())}
        print(f"[MAIL-TEST] (DEV) Gönderici kurulmadı; gerçek mail YOK. Alıcı={to} | Konu={subject}\n"
              f"--- mail içeriği (yalnızca yerel test; üretimde GÖRÜNMEZ) ---\n{body}\n--------------------")
        return True
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_FROM, [to], msg.as_string())
        return True
    except Exception as e:
        print(f"[MAIL] Gönderim hatası ({to}): {e}")
        return False


def _public_base(request):
    """Tarayıcının gördüğü origin (PaaS proxy ardında X-Forwarded-Proto dikkate alınır).

    NOT: Host header istemciden gelir ve sahteleştirilebilir (örn. sıfırlama isteği POST'una
    sahte Host ile gelinirse, e-postayla giden bağlantı saldırganın sitesine işaret edebilir —
    middleware'deki kanonik-host yönlendirmesi yalnızca GET/HEAD'i kapsar, POST'u kapsamaz).
    Bu yüzden UYAP_CANONICAL_HOST ayarlıysa DAİMA o kullanılır, Host header'a asla güvenilmez.
    """
    if CANONICAL_HOST:
        return f"https://{CANONICAL_HOST}"
    scheme = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() or request.scheme
    return f"{scheme}://{request.host}"


def _is_https(request):
    """İstek gerçekte TLS üzerinden mi geldi? PaaS ters-proxy ardında X-Forwarded-Proto belirler."""
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return proto == "https" or request.scheme == "https"


@web.middleware
async def _security_mw(request, handler):
    """Güvenlik başlıkları + (opt-in) kanonik host yönlendirmesi (Kalem 5).
    • HTTPS'te HSTS + oturum çerezine Secure. • Her yanıta nosniff / SAMEORIGIN / no-referrer.
    • UYAP_CANONICAL_HOST varsa GET/HEAD gezinmeleri oraya 308; health/ws/api/webhook + yerel
      host'lar HARİÇ (deploy/health-check kırılmasın)."""
    host = (request.host or "").split(":")[0].lower()
    if (CANONICAL_HOST and host and host != CANONICAL_HOST
            and host not in ("127.0.0.1", "localhost")
            and request.method in ("GET", "HEAD")
            and not request.path.startswith(("/ws", "/ice", "/__app__", "/odeme/webhook"))):
        raise web.HTTPPermanentRedirect(f"https://{CANONICAL_HOST}{request.rel_url}")
    resp = await handler(request)
    if getattr(resp, "prepared", False):
        return resp  # ör. WebSocket: başlıklar zaten gönderildi, dokunma
    # Özel/oturumlu sayfalar arama motorlarına kapalı (SEO planı — CANLIYA_HAZIRLIK.md Faz 6).
    if request.path.startswith(("/ofis", "/admin", "/owner", "/reset", "/satin-al/sonuc", "/giris")):
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # webapp same-origin /giris iframe'i güvenli
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    # CSP (bulgu #15): webapp/index.html + admin/ofis/owner sayfalarındaki mevcut inline
    # <style> ve inline script'ler 'unsafe-inline' gerektiriyor (tam kaldırmak ayrı, büyük bir
    # refactor olurdu) — yine de üçüncü taraf script/kaynak yüklemeyi ve clickjacking'i kapatır.
    # connect-src: 'self' (aynı origin fetch/WS) + stun:/turn: (TURN/ICE farklı host'ta olabilir,
    # bkz. TURN kurulum notları) — dış https:// exfiltrasyonu yine engellenir.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' stun: stuns: turn: turns:; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'self'; form-action 'self'")
    resp.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()")
    if _is_https(request):
        if HSTS_ENABLED:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        for morsel in resp.cookies.values():  # handler set_cookie'den SONRA → Secure işaretle
            morsel["secure"] = True
    return resp


def _mask_email(addr):
    addr = addr or ""
    if "@" not in addr:
        return addr
    name, _, dom = addr.partition("@")
    keep = name[:2] if len(name) > 2 else name[:1]
    return f"{keep}***@{dom}"


# Sıfırlama talebinde DAİMA gösterilen tek-tip mesaj: kullanıcı/e-posta var mı YOK mu
# açığa çıkarmaz (enumeration savunması). Maskeli alıcı e-postası artık DÖNDÜRÜLMEZ.
_RESET_GENERIC = ("Eğer bu kullanıcı adı/e-posta sistemde kayıtlıysa, parola sıfırlama "
                  "bağlantısı ilgili e-posta adresine gönderildi (bağlantı 1 saat geçerlidir).")


def _do_reset_request(request, identifier):
    """Sıfırlama talebini işler: alıcıyı çözer, jeton üretir, (stub) mail gönderir.
    (ok: bool, mesaj) döndürür. Enumeration ve kötüye kullanım (mail seli) savunması için:
      • IP bazlı oran sınırı (her talep bir maliyettir),
      • kullanıcı VAR/YOK ya da e-posta tanımlı/değil farkı DIŞARI sızdırılmaz (tek-tip yanıt)."""
    key = f"reset:{_client_ip(request)}"
    wait = GUARD.blocked_for(key)
    if wait:
        return False, f"Çok fazla sıfırlama talebi. {wait} sn sonra tekrar deneyin."
    identifier = (identifier or "").strip()
    if not identifier:
        return False, "Kullanıcı adı veya e-posta gerekli."
    GUARD.record_fail(key)  # her talep bir maliyettir → tekrarlı talepler üstel olarak yavaşlar
    if not RESET_ENABLED:
        # Üretimde SMTP kurulmamış + test modu kapalı → akış DEVRE DIŞI: jeton ÜRETİLMEZ ve
        # ASLA loglanmaz (bkz. #8). Enumeration savunması için yine de tek-tip yanıt döneriz.
        print("[reset] SMTP yapılandırılmamış ve test modu kapalı → sıfırlama talebi YOK SAYILDI "
              "(jeton üretilmedi). Üretim için UYAP_SMTP_* ayarlayın; yerel test için UYAP_MAIL_TEST=1.")
        return True, _RESET_GENERIC
    info, err = STORE.request_password_reset(identifier)
    if err:
        # 'Kullanıcı yok' / 'alıcı e-posta tanımsız' gibi durumlar DIŞARI sızdırılmaz; yalnızca
        # sunucu günlüğüne yazılır. İstemci her hâlükârda tek-tip _RESET_GENERIC görür.
        print(f"[reset] Talep işlenemedi (gizli, dışarı sızdırılmaz): {err}")
    else:
        link = f"{_public_base(request)}/reset?token={info['token']}"
        hedef = info["target_username"]
        body = (f"Merhaba {info['recipient_username']},\n\n"
                f"'{hedef}' kullanıcısı için parola sıfırlama talebi alındı.\n"
                f"Yeni parola belirlemek için aşağıdaki bağlantıyı kullanın (1 saat geçerlidir):\n\n"
                f"{link}\n\n"
                f"Bu talebi siz yapmadıysanız bu e-postayı yok sayın; parolanız değişmez.\n")
        _send_email(info["recipient_email"], "UYAP — Parola Sıfırlama", body)
    return True, _RESET_GENERIC


# ── TURN / ICE ────────────────────────────────────────────────────────────────────────
# CGNAT/simetrik NAT ardındaki (ör. mobil veri) kullanıcılar için TURN gerekir. coturn'ün
# "use-auth-secret" (REST) yöntemiyle EFEMERAL kimlik üretiriz: paylaşılan gizli anahtardan
# (UYAP_TURN_SECRET) zaman sınırlı kullanıcı/parola türetilir; uzun ömürlü sır istemcilere
# gömülmez. UYAP_TURN_URLS virgülle ayrılmış TURN adresleridir, ör:
#   turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp,turns:turn.example.com:5349
def _turn_servers():
    secret = os.environ.get("UYAP_TURN_SECRET")
    urls_raw = os.environ.get("UYAP_TURN_URLS")
    if not secret or not urls_raw:
        return []
    ttl = int(os.environ.get("UYAP_TURN_TTL", "86400"))
    username = f"{int(time.time()) + ttl}:uyap"
    key = hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    credential = base64.b64encode(key).decode("ascii")
    urls = [u.strip() for u in urls_raw.split(",") if u.strip()]
    return [{"urls": urls, "username": username, "credential": credential}]


def build_ice(is_local=False, static_ice=""):
    """İstemcilere verilecek ICE listesini üretir. UYAP_ICE (static_ice) verilmişse onu
    kullanır; yoksa yerelde boş, uzakta STUN + (yapılandırılmışsa) efemeral TURN."""
    if static_ice:
        try:
            return json.loads(static_ice)
        except Exception:
            pass
    if is_local:
        return []
    servers = [{"urls": "stun:stun.l.google.com:19302"}]
    servers.extend(_turn_servers())
    return servers

# URL yolu -> (disk dosyası, content-type). Beyaz liste: dizin gezme (traversal) yok.
ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/__app__/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/__app__/tunnel.js": (os.path.join("js", "tunnel.js"), "application/javascript; charset=utf-8"),
    "/__app__/wire.js": (os.path.join("js", "wire.js"), "application/javascript; charset=utf-8"),
}

# ------------------------------------------------------------------------------------------
# Signaling (buluşturma) — signaling_server.py mantığının aiohttp WebSocket sürümü
# ------------------------------------------------------------------------------------------
# Oda modeli: TEK ofis (e-imza sahibi) + AYNI ANDA N istemci. Bir oda anahtarı = bir ofis
# lisansı; o büronun personeli aynı anahtarla bağlanıp tek UYAP oturumunu paylaşır. Her
# istemciye sunucu benzersiz bir cid atar; offer/answer/relay mesajları cid ile adreslenir
# ki ofis her istemci için ayrı bir WebRTC bağlantısı tutabilsin (birbirini ATMADAN).
ROOMS = {}      # room -> {"office": ws|None, "homes": {cid: ws}, "office_meta": ...}
ALLOWED = None  # None => her oda serbest; set => yalnızca bu anahtarlar
# Açık-mod: hesap deposu BOŞ ve allowlist YOK iken kimlik doğrulamasız "serbest oda"a izin
# vermek GÜVENLİ DEĞİLDİR (oda adını tahmin eden herkes office/home olarak katılır). Varsayılan
# KAPALI; yalnızca açıkça istenirse açılır (yerel/dev). Bkz. güvenlik raporu bulgu #4.
OPEN_MODE = False


def _env_open_mode() -> bool:
    """Kimliksiz 'serbest oda' açık-modu yalnızca AÇIKÇA istenirse: UYAP_SIGNALING_OPEN =
    1/true/yes/on (ya da --open bayrağı). Üretimde KAPALI olmalı — hesap deposu ya da allowlist
    tanımlayın."""
    return (os.environ.get("UYAP_SIGNALING_OPEN", "") or "").strip().lower() in ("1", "true", "yes", "on")


def load_allowed(path=CONFIG_PATH):
    global ALLOWED
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            rooms = cfg.get("allowed_rooms") or []
            ALLOWED = set(rooms) if rooms else None
        except Exception as e:
            print(f"[!] signaling_config.json okunamadı ({e}); tüm odalar serbest.")
            ALLOWED = None
    else:
        ALLOWED = None
    print("[*] Oda allowlist'i yok: ortak anahtarı bilen her çift buluşabilir."
          if ALLOWED is None else f"[*] {len(ALLOWED)} oda anahtarı izinli (allowlist aktif).")


async def _safe_send(ws, payload):
    if ws is None:
        return
    try:
        await ws.send_str(payload if isinstance(payload, str) else json.dumps(payload))
    except Exception:
        pass


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024, heartbeat=30)
    await ws.prepare(request)

    role = room = cid = rk = info = None
    try:
        first = await ws.receive()
        if first.type != WSMsgType.TEXT:
            return ws
        join = json.loads(first.data)
        role = join.get("role")
        # 'room' alanı v2'de KULLANICI ADI taşır (eski tel uyumu için ad değişmedi). İstemciler
        # (ofis ajanı + ev) bu alanda kullanıcı adını gönderir; parola ayrı alanda gelir.
        room = join.get("room")

        # "probe" = SALT kimlik doğrulama (masaüstü/panel girişi). Oda durumuna DOKUNMAZ:
        # eskiden verify 'office' rolüyle katılıyordu ve sunucu gerçek ajanın ws'ini kapatıp
        # tüm istemcilere peer_left yolluyordu — her personel girişi paylaşımı deviriyordu.
        if role not in ("office", "home", "probe") or not room:
            await _safe_send(ws, {"type": "error", "error": "Geçersiz katılım (role/kullanıcı adı)."})
            return ws

        # rk = iç BULUŞMA anahtarı (ROOMS sözlüğü). Hesap deposu doluysa kullanıcı adı + parola
        # DOĞRULANIR ve buluşma KARARLI office_id ile yapılır: kullanıcıya çirkin/sabit bir oda
        # anahtarı gösterilmez, dönen jeton yalnızca savunma/gösterim içindir. Depo boşsa eski
        # dev davranışı: gelen 'room' alanı doğrudan buluşma anahtarıdır (allowlist/serbest).
        password = join.get("password") or ""
        info = None  # kimlik doğrulandıysa {role, office_code, office_label…}; dev/açık-modda None
        if not STORE.is_empty():
            guard_key = f"ws:{_client_ip(request)}"
            wait = GUARD.blocked_for(guard_key)
            if wait:
                await _safe_send(ws, {"type": "error",
                                      "error": f"Çok fazla başarısız deneme. {wait} sn sonra deneyin."})
                return ws
            # İki-alanlı login: istemci ayrı 'office_code' (ofis kodu) gönderebilir; kimlik
            # 'kullanici@ofis_kodu' olarak birleştirilir. Eski tek-alan istemci 'room' alanında
            # zaten tam kimliği gönderir → _login_id onu olduğu gibi geçirir.
            login_id = _login_id(room, join.get("office_code"))
            ok, reason, info = STORE.authenticate(login_id, password)
            if not ok:
                GUARD.record_fail(guard_key)
                await _safe_send(ws, {"type": "error", "error": reason})
                return ws
            GUARD.record_ok(guard_key)
            rk = info["office_id"]
        elif ALLOWED is not None and room not in ALLOWED:
            await _safe_send(ws, {"type": "error", "error": "Tanınmayan kullanıcı adı."})
            return ws
        elif ALLOWED is None and not OPEN_MODE:
            # Depo boş + allowlist yok + açık-mod kapalı: kimliksiz buluşma reddedilir (güvenli
            # varsayılan). (Allowlist tanımlıysa yukarıda ele alındı; oda izinliyse altta serbest.)
            # Üretimde hesap oluşturun/allowlist verin; yerel/dev için --open.
            await _safe_send(ws, {"type": "error", "error":
                "Sunucu yapılandırılmamış: hesap deposu boş ve oda allowlist'i yok. "
                "Kimliksiz 'serbest oda' yalnızca açık-modda (yerel/dev) çalışır."})
            return ws
        else:
            rk = room

        if role == "probe":
            # Kimlik doğrulandı; ofis slotuna/istemci listesine kaydolmadan yanıtla ve bitir.
            # peer_present = ofis ajanı şu an çevrimiçi mi (giriş ekranı isterse gösterir).
            slot0 = ROOMS.get(rk) or {}
            joined_msg = {"type": "joined", "peer_present": slot0.get("office") is not None}
            if info:
                joined_msg.update({"role": info.get("role"), "office_code": info.get("office_code"),
                                   "office_label": info.get("office_label")})
            await _safe_send(ws, joined_msg)
            return ws

        slot = ROOMS.setdefault(rk, {"office": None, "homes": {}, "office_meta": None})

        if role == "office":
            slot["office_meta"] = {"local_ips": join.get("local_ips") or [],
                                   "port": join.get("port", 8800),
                                   # LAN-direct bileti: home bunu ofis proxy'sine Basic-Auth
                                   # parolası olarak sunar (üye parolası ofiste doğrulanamaz).
                                   # Yalnızca bu odanın kimliği doğrulanmış üyelerine gider.
                                   "lan_token": join.get("lan_token") or ""}
            # Çoğunlukla MEŞRU yeniden bağlanma (ağ koptu); ama açık-modda kimlik doğrulaması
            # olmadığından potansiyel oturum-devralma → görünür uyarı (engellemek meşru
            # reconnect'i bozar).
            old = slot.get("office")
            if old is not None:
                if OPEN_MODE and STORE.is_empty():
                    print(f"[!] UYARI: room={str(room)[:8]}… için mevcut OFIS bağlantısı yenisiyle "
                          f"değiştiriliyor (açık-mod, kimlik doğrulaması yok).")
                await old.close()
            slot["office"] = ws
            if info:  # kalıcı iz: bu ofiste bir sunucu ÇALIŞTI (pano "hiç kurulmadı" ayrımı için)
                STORE.mark_agent_seen(rk)
            joined_msg = {"type": "joined", "peer_present": len(slot["homes"]) > 0}
            if info:  # kimlik doğrulanmış oturum → rol + ofis bilgisini istemciye bildir (rol-tabanlı UI)
                joined_msg.update({"role": info.get("role"), "office_code": info.get("office_code"),
                                   "office_label": info.get("office_label"),
                                   # bulgu #6: yalnızca GERÇEK kimlik doğrulaması geçtiyse verilir —
                                   # ofis ajanı bunu alırsa artık bilet doğrulaması ZORUNLU kılar.
                                   "ticket_key": _room_ticket_key(rk).hex()})
            await _safe_send(ws, joined_msg)
            # Ofis (yeniden) bağlandı: mevcut tüm istemcilere teklif üretmelerini söyle.
            for hcid, hws in list(slot["homes"].items()):
                start = {"type": "start", "cid": hcid}
                if slot.get("office_meta"):
                    start.update(slot["office_meta"])
                await _safe_send(hws, start)
        else:  # home
            cid = uuid.uuid4().hex
            slot["homes"][cid] = ws
            joined = {"type": "joined", "cid": cid, "peer_present": slot["office"] is not None}
            if info:  # pano (ana sayfa): rol/ofis etiketi + sunucunun en son ne zaman görüldüğü
                joined.update({"role": info.get("role"), "office_code": info.get("office_code"),
                               "office_label": info.get("office_label"),
                               "agent_seen": STORE.agent_last_seen(rk)})
            if slot.get("office_meta"):
                joined.update(slot["office_meta"])
            await _safe_send(ws, joined)
            if slot["office"] is not None:
                start = {"type": "start", "cid": cid}
                if slot.get("office_meta"):
                    start.update(slot["office_meta"])
                await _safe_send(ws, start)
        print(f"[+] Katıldı: room={str(room)[:8]}… role={role}"
              + (f" cid={cid[:6]}" if cid else f" istemci={len(slot['homes'])}"))

        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
            if msg.type != WSMsgType.TEXT:
                continue
            if role == "home":
                # İstemci → ofis: hangi istemci olduğunu cid ile imzalayıp ofise ilet.
                try:
                    m = json.loads(msg.data)
                except Exception:
                    continue
                if m.get("type") == "status":
                    # Pano durum sorgusu: ofise İLETİLMEZ, sunucu doğrudan yanıtlar
                    # (ofis çevrimdışıyken de çalışmalı — zaten asıl amaç bu).
                    await _safe_send(ws, {"type": "status",
                                          "office_online": slot.get("office") is not None,
                                          "agent_seen": STORE.agent_last_seen(rk) if info else None})
                    continue
                m["cid"] = cid
                if info:  # bulgu #6: gerçek kimlik doğrulamalı odalarda her ofise iletilen
                    # mesaja taze bir bilet iğnelenir; ofis bunu kendi anahtarıyla doğrular.
                    tkt, exp = _mint_ticket(rk, cid)
                    m["ticket"] = tkt
                    m["ticket_exp"] = exp
                await _safe_send(slot.get("office"), json.dumps(m))
            else:
                # Ofis → istemci: ofis mesaja cid'i koyar; doğru istemciye yönlendir.
                try:
                    target_cid = json.loads(msg.data).get("cid")
                except Exception:
                    continue
                await _safe_send(slot["homes"].get(target_cid), msg.data)
    except Exception as e:
        print(f"[!] Signaling handler hatası (room={room}): {e}")
    finally:
        if rk in ROOMS:
            slot = ROOMS[rk]
            if role == "office" and slot.get("office") is ws:
                slot["office"] = None
                slot["office_meta"] = None
                if info:  # "son görülme" ayrılış anına tazelensin (pano bunu gösterir)
                    STORE.mark_agent_seen(rk)
                for hws in list(slot["homes"].values()):
                    await _safe_send(hws, {"type": "peer_left"})
            elif role == "home" and cid and slot["homes"].get(cid) is ws:
                del slot["homes"][cid]
                await _safe_send(slot.get("office"), {"type": "peer_left", "cid": cid})
            if slot.get("office") is None and not slot["homes"]:
                ROOMS.pop(rk, None)
        print(f"[!] Ayrıldı: room={str(room)[:8]}… role={role}"
              + (f" cid={cid[:6]}" if cid else ""))
    return ws


# ------------------------------------------------------------------------------------------
# Admin ekranı (kullanıcı/lisans oluşturma) — HTTP Basic ile korunur
# ------------------------------------------------------------------------------------------
def _make_owner_session(username: str) -> str:
    """Owner (utku) için imzalı, durumsuz oturum jetonu. _make_session ile aynı desen; ayrı
    'owner' etiketi + ayrı çerez (uyap_owner) kullanır ki master oturumuyla karışmasın.
    Epoch: logout'ta (owner_logout) ve parola değişiminde artar (bulgu #14) — bu token'ı o
    ana kadarki epoch'a bağlar, sonradan değişirse önceki token geçersiz olur."""
    exp = int(time.time()) + SESSION_TTL
    epoch = (STORE.users.get(username) or {}).get("sess_epoch", 0)
    payload = f"owner|{username}|{epoch}|{exp}"
    sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode("utf-8")).decode("ascii")


def _read_owner_session(request):
    """Geçerli owner oturum çerezindeki kullanıcı adını döndürür (yoksa None). Hesap hâlâ var,
    'owner' rolünde ve aktif olmalı; token'daki epoch güncel epoch'la eşleşmeli (bulgu #14)."""
    tok = request.cookies.get(OWNER_COOKIE)
    if not tok:
        return None
    try:
        payload = base64.urlsafe_b64decode(tok.encode("ascii")).decode("utf-8")
        tag, username, epoch, exp, sig = payload.split("|", 4)
        if tag != "owner":
            return None
        expected = hmac.new(SESSION_SECRET.encode("utf-8"),
                            f"owner|{username}|{epoch}|{exp}".encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp) < int(time.time()):
            return None
    except Exception:
        return None
    u = STORE.users.get(username)
    if not u or u.get("role") != "owner" or not u.get("active", True):
        return None
    if str(u.get("sess_epoch", 0)) != epoch:
        return None
    return username


def _admin_gate(request):
    """Admin uçları için TEK kapı: owner (utku) OTURUMU şart. Geçerse None döner; aksi halde
    owner giriş sayfasına 302 yönlendirir (GET) ya da POST'u reddeder. Eski Basic-Auth KALDIRILDI;
    yetki artık TOTP 2FA'lı owner girişiyle kurulan imzalı çereze bağlıdır."""
    if _read_owner_session(request):
        return None
    raise web.HTTPFound("/owner/login")


# ── Owner (utku) giriş: kullanıcı + parola + TOTP 2FA ───────────────────────────────────────
def _render_owner_login(msg=None, err=True):
    cls = "msg" if err else "msg ok"
    note = f"<div class='{cls}'>{html.escape(msg)}</div>" if msg else ""
    if STORE.get_owner() is None:
        # Hiç owner yok → yalnızca KURULUM ipucu (sızdıracak kimlik bilgisi yok).
        hint = ("<div class='card'><p style='color:#f59e0b;font-size:13px'>Owner hesabı henüz "
                "kurulmadı. Sunucuyu <code>UYAP_OWNER_PASSWORD</code> ortam değişkeniyle bir kez "
                "başlatın; konsolda gösterilen TOTP anahtarını authenticator uygulamanıza ekleyin. "
                "Anahtar kaybolduysa <code>UYAP_OWNER_TOTP_SECRET</code> ile yenisini atayın.</p></div>")
        return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP — Owner Girişi</title>{_OFIS_CSS}<style>.msg.ok{{background:#064e3b;border:1px solid #22c55e}}</style></head>
<body><div class='wrap'><h1>Owner Girişi</h1>{note}{hint}</div></body></html>"""
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP — Owner Girişi</title>{_OFIS_CSS}<style>.msg.ok{{background:#064e3b;border:1px solid #22c55e}}</style></head><body>
  <div class='wrap'>
    <h1>Owner Girişi</h1>
    {note}
    <div class='card'>
      <p style='color:#94a3b8;font-size:13px'>Platform sahibi (utku) kullanıcı adı, parola ve
      authenticator uygulamanızdaki 6 haneli doğrulama koduyla girin.</p>
      <form method='post' action='/owner/login' autocomplete='off'>
        <input type='text' name='username' value='{html.escape(accounts.OWNER_USERNAME)}' placeholder='Kullanıcı adı' autofocus required>
        <input type='password' name='password' placeholder='Parola' required>
        <input type='text' name='code' placeholder='Doğrulama kodu (6 hane)' inputmode='numeric'
               pattern='[0-9]*' autocomplete='one-time-code' required>
        <button type='submit'>Giriş Yap</button>
      </form>
    </div>
  </div></body></html>"""


def _owner_response(html_text, cookie=None, clear_cookie=False, status=200):
    resp = web.Response(text=html_text, status=status, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    if cookie is not None:
        # SameSite=Strict: durum-değiştiren /admin uçları için CSRF savunması (cross-site POST'ta
        # çerez GÖNDERİLMEZ). Secure bayrağı HTTPS'te _security_mw tarafından eklenir.
        resp.set_cookie(OWNER_COOKIE, cookie, max_age=SESSION_TTL, httponly=True,
                        samesite="Strict", path="/")
    if clear_cookie:
        resp.del_cookie(OWNER_COOKIE, path="/")
    return resp


async def owner_login_get(request):
    if _read_owner_session(request):
        raise web.HTTPFound("/admin")
    return _owner_response(_render_owner_login())


async def owner_login_post(request):
    key = f"owner:{_client_ip(request)}"
    wait = GUARD.blocked_for(key)
    if wait:
        return _owner_response(_render_owner_login(
            msg=f"Çok fazla başarısız deneme. {wait} sn sonra tekrar deneyin."), status=429)
    data = await request.post()
    username = (data.get("username") or accounts.OWNER_USERNAME).strip()
    password = data.get("password") or ""
    code = (data.get("code") or "").strip()
    # 1) Parola (sabit-zaman + dummy hash → enumeration/timing savunması, accounts.authenticate).
    ok, _reason, info = STORE.authenticate(username, password)
    if not ok or not info or info.get("role") != "owner":
        GUARD.record_fail(key)
        return _owner_response(_render_owner_login(msg="Kullanıcı adı, parola veya kod hatalı."),
                               status=401)
    # 2) TOTP (parola doğrulandıktan SONRA → yanlış parolayla TOTP sayaçları yakılmaz).
    if not STORE.verify_owner_totp(username, code):
        GUARD.record_fail(key)
        return _owner_response(_render_owner_login(msg="Doğrulama kodu hatalı ya da süresi doldu."),
                               status=401)
    GUARD.record_ok(key)
    resp = _owner_response(_render_owner_login(msg="Giriş başarılı.", err=False),
                           cookie=_make_owner_session(username))
    resp.headers["Location"] = "/admin"
    resp.set_status(303)  # POST → GET yönlendirme (çerez korunur)
    return resp


async def owner_logout(request):
    # Epoch'u artır: bu token (ve owner'ın başka cihazdaki eşzamanlı oturumları) anında geçersiz
    # olur — logout artık yalnızca istemci çerezini silmekle kalmıyor (bulgu #14).
    username = _read_owner_session(request)
    if username:
        STORE.bump_sess_epoch(username)
    return _owner_response(_render_owner_login(msg="Çıkış yapıldı.", err=False), clear_cookie=True)


def _render_admin(new_account=None, msg=None):
    rows = []
    for a in STORE.listing_offices():
        created = time.strftime("%Y-%m-%d %H:%M", time.localtime(a["created"])) if a["created"] else "-"
        durum = ("<span style='color:#22c55e'>Aktif</span>" if a["active"]
                 else "<span style='color:#ef4444'>Pasif</span>")
        oid = html.escape(a["office_id"])
        master = html.escape(a.get("master_username", "") or "-")
        room = html.escape(a.get("room_key", "") or "-")
        toggle = "revoke" if a["active"] else "activate"
        toggle_lbl = "İptal Et" if a["active"] else "Aktifleştir"
        rows.append(f"""<tr>
          <td><code>{master}</code></td><td>{html.escape(a['label'])}</td>
          <td>{a.get('user_count', 0)}</td>
          <td><code title='İç/dönen jeton; kullanıcıya gösterilmez'>{room}</code></td>
          <td>{durum}</td><td>{created}</td>
          <td class='act'>
            <form method='post' action='/admin/{toggle}'><input type='hidden' name='office_id' value='{oid}'><button>{toggle_lbl}</button></form>
            <form method='post' action='/admin/reset'><input type='hidden' name='office_id' value='{oid}'><button>Master Parola Sıfırla</button></form>
            <form method='post' action='/admin/rotate'><input type='hidden' name='office_id' value='{oid}'><button>Oda Döndür</button></form>
            <form method='post' action='/admin/delete' onsubmit="return confirm('Ofis ve tüm kullanıcıları silinsin mi?')"><input type='hidden' name='office_id' value='{oid}'><button class='danger'>Sil</button></form>
          </td></tr>""")
    table = "\n".join(rows) or "<tr><td colspan='7' style='text-align:center;color:#94a3b8'>Henüz ofis yok.</td></tr>"

    office_options = "\n".join(
        f"<option value='{html.escape(a['office_id'])}'>"
        f"{html.escape(a.get('master_username') or '-')} — {html.escape(a.get('label') or '')}</option>"
        for a in STORE.listing_offices()
    )

    order_rows = []
    for o in STORE.listing_orders():
        if o.get("status") == "provisioned":
            continue  # karşılanan siparişler ofisler tablosunda görünür
        created = time.strftime("%Y-%m-%d %H:%M", time.localtime(o.get("created", 0))) if o.get("created") else "-"
        st = o.get("status", "pending")
        st_color = {"pending": "#eab308", "failed": "#ef4444"}.get(st, "#94a3b8")
        oid = html.escape(o.get("order_id", ""))
        order_rows.append(f"""<tr>
          <td><code>{html.escape(o.get('master_username',''))}</code></td>
          <td>{html.escape(o.get('label',''))}</td>
          <td>{html.escape(o.get('email',''))}</td>
          <td><span style='color:{st_color}'>{html.escape(st)}</span></td>
          <td>{created}</td>
          <td class='act'>
            <form method='post' action='/admin/order-provision'><input type='hidden' name='order_id' value='{oid}'><button>Ödendi → Ofis Oluştur</button></form>
            <form method='post' action='/admin/order-cancel' onsubmit="return confirm('Sipariş silinsin mi?')"><input type='hidden' name='order_id' value='{oid}'><button class='danger'>Sil</button></form>
          </td></tr>""")
    order_table = "\n".join(order_rows) or "<tr><td colspan='6' style='text-align:center;color:#94a3b8'>Bekleyen sipariş yok.</td></tr>"

    banner = ""
    if new_account:
        if new_account.get("password"):
            pw_line = f"<div class='cred'>Parola: <code>{html.escape(new_account['password'])}</code></div>"
        else:  # üyelikte kullanıcı kendi parolasını seçmiş → gösterilecek parola yok
            pw_line = "<div class='cred'>Parola: <i>üyelikte kullanıcının belirlediği parola</i></div>"
        banner = f"""<div class='new'>
          <b>Yeni ofis (lisans) oluşturuldu</b> — bu bilgileri müşteriye verin (parola yalnızca BİR KEZ gösterilir):
          <div class='cred'>Kullanıcı Adı: <code>{html.escape(new_account['username'])}</code></div>
          {pw_line}
        </div>"""
    note = f"<div class='msg'>{html.escape(msg)}</div>" if msg else ""

    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP Lisans Yönetimi</title><style>
  body{{background:#0f172a;color:#f8fafc;font-family:Segoe UI,system-ui,sans-serif;margin:0;padding:24px}}
  h1{{color:#0ea5e9;font-size:20px}} .card{{background:#1e293b;border-radius:10px;padding:18px;margin-bottom:18px;max-width:1000px}}
  input[type=text]{{background:#0f172a;border:1px solid #475569;color:#f8fafc;padding:8px;border-radius:6px;width:260px}}
  button{{background:#0ea5e9;border:0;color:#fff;padding:7px 12px;border-radius:6px;cursor:pointer;font-weight:600;margin:2px}}
  button.danger{{background:#ef4444}} table{{width:100%;border-collapse:collapse;max-width:1000px}}
  th,td{{text-align:left;padding:8px;border-bottom:1px solid #334155;font-size:13px;vertical-align:top}}
  code{{background:#0f172a;padding:2px 6px;border-radius:4px;color:#7dd3fc}}
  .act form{{display:inline}} .new{{background:#064e3b;border:1px solid #22c55e;padding:14px;border-radius:8px;margin-bottom:14px}}
  .cred{{margin-top:6px;font-size:15px}} .msg{{background:#1e3a8a;padding:10px;border-radius:8px;margin-bottom:14px}}
  .top{{display:flex;align-items:center;justify-content:space-between;max-width:1000px}}
  button.ghost{{background:#334155}}
</style></head><body>
  <div class='top'><h1>UYAP Lisans Yönetimi <span style='color:#94a3b8;font-size:13px;font-weight:400'>· owner: <code>{html.escape(accounts.OWNER_USERNAME)}</code></span></h1>
    <form method='post' action='/owner/logout'><button class='ghost'>Çıkış</button></form></div>
  {note}{banner}
  <div class='card'>
    <h3>Yeni Ofis (Lisans) + Master Kullanıcı Oluştur</h3>
    <form method='post' action='/admin/create'>
      <input type='text' name='username' placeholder='Master kullanıcı adı (ör. ahmethukuk)' required>
      <input type='text' name='label' placeholder='Etiket (ör. Ahmet Hukuk Bürosu)' required>
      <input type='text' name='email' placeholder='Master e-posta (parola sıfırlama için)'>
      <input type='text' name='password' placeholder='Parola (boşsa otomatik üretilir)'>
      <button type='submit'>Oluştur</button>
    </form>
    <p style='color:#94a3b8;font-size:12px'>Müşteri uygulamada bu KULLANICI ADI + parolayı girer. Oda kimliği içeride otomatik üretilir, düzensiz aralıklarla DÖNER ve kullanıcıya hiç gösterilmez. Master sonradan kendi alt kullanıcılarını ekleyebilir.</p>
  </div>
  <div class='card'>
    <h3>Ofise Manuel Kullanıcı Ata</h3>
    <form method='post' action='/admin/adduser'>
      <select name='office_id' required style='background:#0f172a;border:1px solid #475569;color:#f8fafc;padding:8px;border-radius:6px'>
        <option value='' disabled selected>Ofis seçin…</option>
        {office_options}
      </select>
      <input type='text' name='username' placeholder='Kullanıcı adı' required>
      <input type='text' name='email' placeholder='E-posta (opsiyonel)'>
      <select name='role' style='background:#0f172a;border:1px solid #475569;color:#f8fafc;padding:8px;border-radius:6px'>
        <option value='member' selected>Üye</option>
        <option value='master'>Master</option>
      </select>
      <input type='text' name='password' placeholder='Parola (boşsa otomatik)'>
      <button type='submit'>Kullanıcı Ekle</button>
    </form>
  </div>
  <div class='card'><h3>Bekleyen Siparişler (satın alma talepleri)</h3>
    <table><thead><tr><th>Kullanıcı</th><th>Etiket</th><th>E-posta</th><th>Durum</th><th>Tarih</th><th>İşlem</th></tr></thead>
    <tbody>{order_table}</tbody></table>
    <p style='color:#94a3b8;font-size:12px'>Ödemesi (havale/EFT vb.) onaylanan talebi "Ödendi → Ofis Oluştur" ile karşılayın; ofis kodu + giriş bilgileri üretilip müşterinin e-postasına gönderilir ve bir kez burada gösterilir.</p>
  </div>
  <div class='card'><h3>Ofisler</h3>
    <table><thead><tr><th>Master Kullanıcı</th><th>Etiket</th><th>Kullanıcı</th><th>Dönen Oda</th><th>Durum</th><th>Oluşturma</th><th>İşlem</th></tr></thead>
    <tbody>{table}</tbody></table>
  </div>
</body></html>"""


def _admin_page(new_account=None, msg=None):
    return web.Response(text=_render_admin(new_account, msg), content_type="text/html",
                        charset="utf-8", headers={"Cache-Control": "no-store"})


async def admin_get(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    return _admin_page()


async def admin_create(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    label = (data.get("label") or "").strip()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    pw = (data.get("password") or "").strip() or None
    if not label or not username:
        return _admin_page(msg="Master kullanıcı adı ve etiket gerekli.")
    try:
        res = STORE.create_office(label, master_username=username, master_password=pw,
                                  master_email=email)
    except accounts.AccountError as e:
        return _admin_page(msg=str(e))
    return _admin_page(new_account={"username": res["master_username"], "password": res["password"]},
                       msg="Ofis ve master kullanıcı oluşturuldu.")


async def admin_adduser(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    office_id = (data.get("office_id") or "").strip()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    role = (data.get("role") or "member").strip()
    pw = (data.get("password") or "").strip() or None
    if not office_id or not username:
        return _admin_page(msg="Ofis ve kullanıcı adı gerekli.")
    try:
        res = STORE.create_user(office_id, username, password=pw, role=role, email=email)
    except accounts.AccountError as e:
        return _admin_page(msg=str(e))
    return _admin_page(new_account={"username": res["username"], "password": res["password"]},
                       msg=f"Kullanıcı ofise eklendi (rol: {res['role']}).")


def _master_of(office_id):
    """Bir ofisin master kullanıcı adını bulur (parola sıfırlama için)."""
    for uname, r in STORE.users.items():
        if r.get("office_id") == office_id and r.get("role") == "master":
            return uname
    return None


async def admin_revoke(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    STORE.set_office_active((data.get("office_id") or "").strip(), False)
    return _admin_page(msg="Ofis lisansı iptal edildi (pasif).")


async def admin_activate(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    STORE.set_office_active((data.get("office_id") or "").strip(), True)
    return _admin_page(msg="Ofis lisansı aktifleştirildi.")


async def admin_reset(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    office_id = (data.get("office_id") or "").strip()
    master = _master_of(office_id)
    if not master:
        return _admin_page(msg="Ofisin master kullanıcısı bulunamadı.")
    new_pw = STORE.reset_user_password(master)
    if new_pw is None:
        return _admin_page(msg="Kullanıcı bulunamadı.")
    return _admin_page(new_account={"username": master, "password": new_pw},
                       msg="Master parolası sıfırlandı.")


async def admin_rotate(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    new_key = STORE.rotate_room_key((data.get("office_id") or "").strip())
    if new_key is None:
        return _admin_page(msg="Ofis bulunamadı.")
    return _admin_page(msg="Oda anahtarı döndürüldü (kullanıcı etkilenmez).")


async def admin_delete(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    STORE.delete_office((data.get("office_id") or "").strip())
    return _admin_page(msg="Ofis ve bağlı kullanıcılar silindi.")


# ------------------------------------------------------------------------------------------
# Satış / ödeme (Kalem 4 — İSKELET): landing → sipariş → provision → giriş bilgisi teslimi
# ------------------------------------------------------------------------------------------
def _prune_claims():
    now = time.time()
    for tok in [t for t, r in CRED_CLAIMS.items() if r.get("exp", 0) < now]:
        CRED_CLAIMS.pop(tok, None)


def _provision_order(order_id, request=None):
    """Ödemesi ONAYLANAN siparişi ofise dönüştürür (idempotent). (ok, res|mesaj) döner.
    res: {order_id, office_code, master_username, master_login, password, claim_token}.
    Parola diske yazılmaz; tek-kullanımlık claim + (varsa) e-posta ile teslim edilir."""
    rec = STORE.get_order(order_id)
    if not rec:
        return False, "Sipariş bulunamadı."
    if rec.get("status") == "provisioned" and rec.get("office_id"):
        return False, "Bu sipariş zaten karşılandı (ofis oluşturuldu)."
    try:
        res = STORE.create_office(
            rec.get("label") or rec.get("master_username"),
            master_username=rec.get("master_username"),
            master_email=rec.get("email", ""),
            master_password_hash=rec.get("password_hash"),  # üyelikte kullanıcı seçtiyse (düz metin YOK)
            phone=rec.get("phone", ""),
            slug=(rec.get("slug") or None),
        )
    except accounts.AccountError as e:
        STORE.update_order(order_id, status="failed", error=str(e))
        return False, str(e)
    STORE.update_order(order_id, status="provisioned", office_id=res["office_id"],
                       office_code=res["office_code"])
    # Kullanıcı üyelikte KENDİ parolasını seçtiyse res["password"] None'dır → asla gösterilmez/yollanmaz.
    user_chose_pw = res["password"] is None
    _prune_claims()
    claim = secrets.token_urlsafe(18)
    CRED_CLAIMS[claim] = {
        "order_id": order_id, "username": res["master_username"], "login": res["master_login"],
        "password": res["password"], "office_code": res["office_code"],
        "exp": time.time() + CRED_CLAIM_TTL,
    }
    email = (rec.get("email") or "").strip()
    if email:
        base = _public_base(request) if request is not None else ""
        pw_line = ("Parola    : (üyelik sırasında belirlediğiniz parola)\n" if user_chose_pw
                   else f"Parola    : {res['password']}\n")
        body = (f"Merhaba,\n\n'{rec.get('label') or res['office_code']}' için UYAP uzaktan erişim "
                f"lisansınız hazır.\n\n"
                f"Ofis kodu : {res['office_code']}\n"
                f"Kullanıcı : {res['master_login']}\n"
                f"{pw_line}\n"
                f"Giriş: {base}/  (Ofis kodu + kullanıcı + parola ile)\n\n"
                f"Güvenliğiniz için parolanızı ilk girişten sonra {base}/ofis panelinden "
                f"değiştirebilirsiniz.\n")
        _send_email(email, "UYAP — Lisansınız hazır", body)
    return True, {"order_id": order_id, "office_code": res["office_code"],
                  "master_username": res["master_username"], "master_login": res["master_login"],
                  "password": res["password"], "claim_token": claim}


# Satış sayfaları da panel web kabuğuyla AYNI tasarım dilini kullanır (açık tema + adaçayı).
_SALES_CSS = """
  :root{color-scheme:light;
    --bg:#F1EEE8;--card:#FCFBF8;--card-edge:#E8E2D7;--shadow:rgba(96,86,66,.14);
    --ink:#43423D;--ink-soft:#8C867B;--ink-faint:#B6AFA2;--line:#E3DDD2;
    --sage:#7C9A7E;--sage-dk:#5E7D63;--sage-tint:#E4EBE0;--clay:#C18A66;--radius:16px}
  body{background:var(--bg);color:var(--ink);
       font-family:"Segoe UI",system-ui,-apple-system,sans-serif;margin:0;padding:24px}
  .wrap{max-width:560px;margin:0 auto}
  h1{color:var(--ink);font-size:24px;font-weight:600} h2{color:var(--sage-dk);font-size:18px;font-weight:600}
  .card{background:var(--card);border:1px solid var(--card-edge);border-radius:var(--radius);
        box-shadow:0 14px 40px var(--shadow);padding:24px 26px;margin:18px 0}
  label{display:block;margin:12px 0 4px;font-size:12px;color:var(--ink-soft)}
  input{width:100%;box-sizing:border-box;background:#fff;border:1px solid var(--line);color:var(--ink);
        padding:10px 12px;border-radius:9px;font-size:14px;outline:none;
        transition:border-color .12s,box-shadow .12s}
  input:focus{border-color:var(--sage);box-shadow:0 0 0 3px var(--sage-tint)}
  button{background:var(--sage);border:0;color:#fff;padding:12px 18px;border-radius:11px;cursor:pointer;
         font-weight:600;font-size:14px;margin-top:16px;width:100%;font-family:inherit;
         box-shadow:0 2px 7px var(--shadow);transition:background .12s,box-shadow .12s}
  button:hover{background:var(--sage-dk);box-shadow:0 4px 11px var(--shadow)}
  a{color:var(--sage-dk)}
  code{background:var(--sage-tint);padding:2px 6px;border-radius:4px;color:var(--sage-dk)}
  .price{font-size:22px;color:var(--sage-dk);font-weight:700} .muted{color:var(--ink-soft);font-size:13px}
  .ok{background:var(--sage-tint);border:1px solid var(--sage);padding:16px;border-radius:11px}
  .err{background:#F7E8DE;border:1px solid var(--clay);color:#9C6B47;padding:12px;border-radius:9px;margin-bottom:12px}
  .cred{font-size:16px;margin-top:8px}
"""


def _sales_shell(title, inner):
    return (f"<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_SALES_CSS}</style></head>"
            f"<body><div class='wrap'>{inner}</div></body></html>")


def _sales_response(title, inner, status=200):
    return web.Response(text=_sales_shell(title, inner), status=status,
                        content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


def _sales_disabled():
    return _sales_response("Kapalı", "<h1>Satış kapalı</h1>"
                           "<p class='muted'>Şu anda çevrimiçi satın alma devre dışı.</p>", status=404)


def _render_satin_al(msg=None):
    price = (f"<div class='price'>{html.escape(PLAN_PRICE)}</div>" if PLAN_PRICE
             else "<div class='muted'>Fiyat için iletişime geçin.</div>")
    err = f"<div class='err'>{html.escape(msg)}</div>" if msg else ""
    return f"""<h1>{html.escape(PLAN_NAME)}</h1>
      <div class='card'>
        {price}
        <p class='muted'>E-imzalı bilgisayarınızdaki UYAP oturumunu, tarayıcıdan güvenli
        (uçtan uca şifreli) bir tünelle uzaktan kullanın. Aşağıdaki formu doldurun; ödeme
        onaylandığında ofis kodunuz ve giriş bilgileriniz e-postanıza gönderilir.</p>
      </div>
      <div class='card'>
        <h2>Satın alma talebi</h2>
        {err}
        <form method='post' action='/satin-al'>
          <label>Büro / etiket <span class='muted'>(boşluksuz; ofis kodunuz bundan türetilir)</span></label>
          <input type='text' name='label' placeholder='ör. ahmethukuk' required
                 autocapitalize='none' autocorrect='off'>
          <label>Kullanıcı adı <span class='muted'>(çıplak ad, '@' olmadan)</span></label>
          <input type='text' name='username' placeholder='ör. ahmet' required
                 autocapitalize='none' autocorrect='off'>
          <label>E-posta <span class='muted'>(giriş bilgileri buraya gönderilir)</span></label>
          <input type='email' name='email' placeholder='ör. ahmet@buro.av.tr' required>
          <button type='submit'>Satın alma talebi gönder</button>
        </form>
        <p class='muted'>Talebinizi gönderince ofis HEMEN oluşmaz; ödeme onaylandıktan sonra
        aktive edilir.</p>
      </div>"""


async def satin_al_get(request):
    if not SALES_ENABLED:
        return _sales_disabled()
    return _sales_response(PLAN_NAME, _render_satin_al())


async def satin_al_post(request):
    if not SALES_ENABLED:
        return _sales_disabled()
    key = f"order:{_client_ip(request)}"
    wait = GUARD.blocked_for(key)
    if wait:
        return _sales_response("Çok fazla talep",
                               f"<div class='err'>Çok fazla talep. {wait} sn sonra tekrar deneyin.</div>",
                               status=429)
    data = await request.post()
    label = (data.get("label") or "").strip()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    if not label or not username or not email:
        return _sales_response(PLAN_NAME, _render_satin_al(msg="Büro, kullanıcı adı ve e-posta gerekli."))
    GUARD.record_fail(key)  # her talep bir maliyettir → spam üstel olarak yavaşlar
    try:
        order = STORE.create_order(label, master_username=username, email=email,
                                   plan=PLAN_NAME, amount=PLAN_PRICE, provider=PAYMENT_PROVIDER)
    except accounts.AccountError as e:
        return _sales_response(PLAN_NAME, _render_satin_al(msg=str(e)))
    # provider "manual": onay admin panelinden verilir. Gerçek sağlayıcı eklendiğinde burada
    # _provider_begin(order, request) çağrılıp kullanıcı ödeme sayfasına yönlendirilecek.
    oid = html.escape(order["order_id"])
    inner = (f"<h1>Talebiniz alındı</h1><div class='card ok'>"
             f"<p>Sipariş numaranız: <code>{oid}</code></p>"
             f"<p>Ödemeniz onaylandığında <b>{html.escape(email)}</b> adresine ofis kodunuz ve "
             f"giriş bilgileriniz gönderilecek.</p></div>"
             f"<p class='muted'>Ödeme/aktivasyon için sizinle iletişime geçilecektir.</p>")
    return _sales_response("Talep alındı", inner)


async def cred_claim_get(request):
    """Provision sonrası giriş bilgilerini TEK KEZ gösterir (kısa ömürlü claim jetonu)."""
    _prune_claims()
    tok = (request.query.get("claim") or "").strip()
    rec = CRED_CLAIMS.pop(tok, None) if tok else None
    if not rec:
        return _sales_response("Bulunamadı",
                               "<h1>Bağlantı geçersiz</h1><p class='muted'>Bu tek-kullanımlık "
                               "bağlantı süresi doldu ya da zaten kullanıldı. Giriş bilgileriniz "
                               "e-postanıza da gönderildi.</p>", status=404)
    pw_html = (f"<div class='cred'>Parola: <code>{html.escape(rec['password'])}</code></div>"
               if rec.get("password") else
               "<div class='cred'>Parola: <i>üyelik sırasında belirlediğiniz parola</i></div>")
    inner = (f"<h1>Lisansınız hazır</h1><div class='card ok'>"
             f"<div class='cred'>Ofis kodu: <code>{html.escape(rec['office_code'])}</code></div>"
             f"<div class='cred'>Kullanıcı: <code>{html.escape(rec['login'])}</code></div>"
             f"{pw_html}"
             f"<p class='muted'>Bu bilgiler yalnızca BİR KEZ gösterilir. Parolanızı ilk girişten "
             f"sonra /ofis panelinden değiştirin.</p></div>"
             f"<p><a href='/'>Girişe git →</a></p>")
    return _sales_response("Lisansınız hazır", inner)


# ── Üye Ol (self-servis üyelik → sipariş) ──────────────────────────────────────────────────
# Tasarım, panel web kabuğuyla (Panel/web/static/style.css) ve açılış ekranıyla
# (webapp/index.html) AYNI dile sadıktır: ılık-nötr açık zemin #F1EEE8, kart #FCFBF8,
# adaçayı vurgu #7C9A7E. Üyelik ödeme onaylı sipariş akışına köprülenir: form ANINDA ofis
# oluşturmaz; kullanıcı ofis adını + PAROLASINI seçer, ödeme/admin onayından sonra ofisi bu
# bilgilerle provision edilir (parola düz metin saklanmaz; hash sipariş kaydında taşınır).
_SITE_CSS = """
  :root{color-scheme:light;
    --bg:#F1EEE8;--card:#FCFBF8;--card-edge:#E8E2D7;--shadow:rgba(96,86,66,.14);
    --ink:#43423D;--ink-soft:#8C867B;--ink-faint:#B6AFA2;--line:#E3DDD2;
    --sage:#7C9A7E;--sage-dk:#5E7D63;--sage-tint:#E4EBE0;--clay:#C18A66;--radius:16px}
  *{box-sizing:border-box}
  body{background:var(--bg);color:var(--ink);
       font-family:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",sans-serif;
       margin:0;padding:24px;min-height:100vh}
  .wrap{max-width:460px;margin:0 auto}
  .brand{text-align:center;margin:8px 0 18px}
  .brand h1{font-size:22px;font-weight:600;margin:0;letter-spacing:-.01em;color:var(--ink)}
  .brand p{color:var(--ink-soft);font-size:13px;line-height:1.55;margin:6px 0 0}
  .card{background:var(--card);border:1px solid var(--card-edge);border-radius:var(--radius);
        box-shadow:0 14px 40px var(--shadow);padding:24px 26px;margin:16px 0}
  h2{font-weight:600;color:var(--ink)}
  label{display:block;margin:12px 0 4px;font-size:12px;color:var(--ink-soft)}
  input{width:100%;background:#fff;border:1px solid var(--line);color:var(--ink);padding:10px 12px;
        border-radius:9px;font-size:14px;outline:none;transition:border-color .12s,box-shadow .12s}
  input::placeholder{color:var(--ink-faint)}
  input:focus{border-color:var(--sage);box-shadow:0 0 0 3px var(--sage-tint)}
  .suffix{display:flex;align-items:center;gap:0} .suffix span{color:var(--ink-soft);padding:0 8px;font-size:14px}
  button{width:100%;background:var(--sage);border:0;color:#fff;padding:12px;border-radius:11px;
         cursor:pointer;font-weight:600;font-size:14px;margin-top:18px;font-family:inherit;
         box-shadow:0 2px 7px var(--shadow);transition:background .12s,box-shadow .12s}
  button:hover{background:var(--sage-dk);box-shadow:0 4px 11px var(--shadow)}
  button:active{transform:translateY(1px)}
  a{color:var(--sage-dk);text-decoration:none} a:hover{color:var(--clay)}
  .muted{color:var(--ink-soft);font-size:12px;line-height:1.5}
  .err{background:#F7E8DE;border:1px solid var(--clay);color:#9C6B47;padding:11px;border-radius:9px;margin-bottom:12px;font-size:13px}
  .ok{background:var(--sage-tint);border:1px solid var(--sage);padding:16px;border-radius:11px}
  code{background:var(--sage-tint);padding:2px 7px;border-radius:5px;color:var(--sage-dk)}
  .foot{text-align:center;margin-top:14px;font-size:13px;color:var(--ink-soft)}
"""


def _site_shell(title, inner):
    return (f"<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_SITE_CSS}</style></head>"
            f"<body><div class='wrap'>{inner}</div></body></html>")


def _site_response(title, inner, status=200):
    return web.Response(text=_site_shell(title, inner), status=status, content_type="text/html",
                        charset="utf-8", headers={"Cache-Control": "no-store",
                                                  "Referrer-Policy": "no-referrer"})


def _render_uye_ol(msg=None, v=None):
    v = v or {}
    err = f"<div class='err'>{html.escape(msg)}</div>" if msg else ""

    def val(k):
        return html.escape(v.get(k, "") or "")

    minlen = accounts.MIN_PASSWORD_LENGTH
    return f"""
      <div class='brand'><h1>UYAP Uzaktan Erişim</h1>
        <p>E-imzalı bilgisayarınızdaki UYAP oturumunu, tarayıcıdan uçtan uca şifreli bir tünelle
        büronuzun her yerinden güvenle kullanın.</p></div>
      <div class='card'>
        <h2 style='margin-top:0;font-size:18px'>Üye Ol</h2>
        {err}
        <form method='post' action='/uye-ol' autocomplete='on'>
          <label>Büro / ofis adı <span class='muted'>(boşluksuz; giriş kodunuz budur)</span></label>
          <input type='text' name='office' value='{val("office")}' placeholder='ör. ahmethukuk'
                 autocapitalize='none' autocorrect='off' pattern='\\S+'
                 title='Büro adında boşluk olamaz' required>
          <label>Kullanıcı adı <span class='muted'>(çıplak ad; giriş: kullanıcı@ofis)</span></label>
          <input type='text' name='username' value='{val("username")}' placeholder='ör. ahmet'
                 autocapitalize='none' autocorrect='off' required>
          <label>E-posta</label>
          <input type='email' name='email' value='{val("email")}' placeholder='ör. ahmet@buro.av.tr' required>
          <label>Telefon</label>
          <input type='tel' name='phone' value='{val("phone")}' placeholder='ör. 0555 111 22 33' required>
          <label>Parola <span class='muted'>({accounts.PASSWORD_POLICY_HINT})</span></label>
          <input type='password' name='password' placeholder='Parola' minlength='{minlen}' required>
          <label>Parola (tekrar)</label>
          <input type='password' name='password2' placeholder='Parolayı tekrar girin' minlength='{minlen}' required>
          <button type='submit'>Üye Ol</button>
        </form>
        <p class='muted' style='margin-top:14px'>Üyelik oluşturulduktan sonra hesabınız ödeme/onay
        sonrası aktifleştirilir; onaylanınca <b>kullanıcı@ofis</b> ve parolanızla giriş yaparsınız.</p>
      </div>
      <div class='foot'>Zaten üye misiniz? <a href='/'>Giriş yap →</a></div>"""


def _render_indir():
    href = _download_href()
    if href:
        btn = (f"<a href='{html.escape(href, quote=True)}' download "
               f"style='display:block;text-align:center;background:var(--sage);color:#fff;"
               f"padding:13px;border-radius:11px;font-weight:600;margin:14px 0'>"
               f"Ofis Programını İndir (Windows kurulum paketi · .msi)</a>")
    else:
        btn = ("<div class='err' style='margin:14px 0'>İndirme paketi şu anda hazırlanıyor; "
               "kısa süre içinde burada yayınlanacak. Sorularınız için bize ulaşın.</div>")
    return f"""
      <div class='brand'><h1>Ofis Programı Kurulumu</h1>
        <p>E-imzanın takılı olduğu <b>ofis bilgisayarına</b> bir kez kurulur; büronuzun geri
        kalanı hiçbir şey kurmadan tarayıcıyla bağlanır.</p></div>
      <div class='card'>
        <h2 style='margin-top:0;font-size:18px'>1. İndir, çift tıkla, bir kez giriş yap — hepsi bu</h2>
        {btn}
        <p class='muted' style='font-size:13px;line-height:1.7'>
          <code>CokluUyapKur.msi</code> standart bir Windows kurulumudur: yönetici izni
          istemez, masaüstüne kısayol koyar ve programı açar. Açılan pencerede
          <b>kullanıcı adınızı (kullanıcıadı@ofisadı), parolanızı ve e-imza PIN'inizi bir
          kez</b> girin: paylaşım kendiliğinden başlar, bilgisayar her açıldığında da
          otomatik kurulur. Menü/ayar gezmek gerekmez.</p>
      </div>
      <div class='card'>
        <h2 style='margin-top:0;font-size:18px'>2. Büronuz bağlansın</h2>
        <p class='muted' style='font-size:13px;line-height:1.7'>
          Personeliniz bu siteye kendi kullanıcı adıyla girer; ana sayfadaki kart bağlantıyı
          kendisi kurar ve <b>"UYAP'ı Aç"</b> düğmesi belirir. Kullanıcı eklemek/yönetmek için
          <a href='/ofis'>Ofis Yönetim Paneli</a>'ni kullanın.</p>
      </div>
      <div class='foot'><a href='/'>← Ana sayfa</a></div>"""


async def indir_get(request):
    return _site_response("Ofis Programını İndir — Çoklu UYAP", _render_indir())


async def indir_dosya(request):
    """İmaja gömülü kurulum dosyasını (tek-exe kurucu; geçişte eski zip) indirir.
    Dış UYAP_DOWNLOAD_URL ayarlıysa oraya yönlendirir (dosyayı iki yerde tutmamak için)."""
    if DOWNLOAD_URL:
        raise web.HTTPFound(DOWNLOAD_URL)
    yol = _download_file()
    if not yol:
        return _site_response("Bulunamadı",
                              "<div class='card'><p class='muted'>İndirme paketi henüz "
                              "yüklenmedi.</p></div>", status=404)
    return web.FileResponse(yol, headers={
        "Content-Disposition": "attachment; filename=%s" % os.path.basename(yol),
        "Cache-Control": "public, max-age=3600"})


async def uye_ol_get(request):
    if not SALES_ENABLED:
        return _site_response("Kapalı", "<div class='brand'><h1>Üyelik kapalı</h1></div>"
                              "<div class='card'><p class='muted'>Şu anda yeni üyelik alınmıyor.</p></div>",
                              status=404)
    return _site_response("Üye Ol — UYAP Uzaktan Erişim", _render_uye_ol())


async def uye_ol_post(request):
    if not SALES_ENABLED:
        return _site_response("Kapalı", "<div class='card'><p class='muted'>Üyelik kapalı.</p></div>",
                              status=404)
    key = f"order:{_client_ip(request)}"
    wait = GUARD.blocked_for(key)
    if wait:
        return _site_response("Çok fazla talep",
                              f"<div class='card'><div class='err'>Çok fazla deneme. {wait} sn "
                              f"sonra tekrar deneyin.</div></div>", status=429)
    data = await request.post()
    v = {k: (data.get(k) or "").strip() for k in ("office", "username", "email", "phone")}
    password = data.get("password") or ""
    password2 = data.get("password2") or ""

    def fail(m):
        return _site_response("Üye Ol", _render_uye_ol(msg=m, v=v), status=400)

    if not (v["office"] and v["username"] and v["email"] and v["phone"]):
        return fail("Tüm alanlar zorunludur.")
    if any(c.isspace() for c in v["office"]):
        return fail("Büro adında boşluk olamaz (ör. 'ahmethukuk').")
    if "@" in v["username"]:
        return fail("Kullanıcı adı '@' içeremez (çıplak ad girin).")
    if password != password2:
        return fail("Parolalar eşleşmiyor.")
    perr = accounts.password_policy_error(password)
    if perr:
        return fail(perr)
    slug = accounts.slugify(v["office"])
    if not slug:
        return fail("Geçerli bir ofis adı girin (en az bir harf/rakam).")
    if STORE.slug_taken(slug):
        return fail("Bu ofis adı zaten alınmış; farklı bir ad deneyin.")
    if accounts.compose_username(slug, v["username"]) in STORE.users:
        return fail("Bu kullanıcı adı bu ofiste dolu; farklı bir ad deneyin.")

    GUARD.record_fail(key)  # her talep bir maliyet → spam üstel yavaşlar
    try:
        order = STORE.create_order(v["office"], master_username=v["username"], email=v["email"],
                                   phone=v["phone"], password=password, slug=slug,
                                   plan=PLAN_NAME, amount=PLAN_PRICE, provider=PAYMENT_PROVIDER)
    except accounts.AccountError as e:
        return fail(str(e))
    inner = (f"<div class='brand'><h1>Üyeliğiniz alındı</h1></div>"
             f"<div class='card ok'>"
             f"<p>Giriş kullanıcı adınız: <code>{html.escape(v['username'])}@{html.escape(slug)}</code></p>"
             f"<p>Sipariş numaranız: <code>{html.escape(order['order_id'])}</code></p>"
             f"<p class='muted' style='margin-top:10px'>Hesabınız ödeme/onay sonrası aktifleştirilecek. "
             f"Onaylanınca <b>{html.escape(v['email'])}</b> adresine bilgilendirme gönderilir; ardından "
             f"seçtiğiniz parolayla giriş yapabilirsiniz.</p></div>"
             f"<div class='card'>"
             f"<h2 style='margin-top:0;font-size:17px'>Sırada ne var?</h2>"
             f"<p class='muted' style='font-size:13px;line-height:1.7'>"
             f"<b>1.</b> Onay beklerken, e-imzanın takılı olduğu <b>ofis bilgisayarına</b> tek seferlik "
             f"ofis programını kurabilirsiniz → <a href='/indir'>ofis programını indir</a>.<br>"
             f"<b>2.</b> Büronuzun diğer personeli hiçbir şey kurmaz; onaydan sonra bu siteye "
             f"kendi kullanıcı adıyla girer.<br>"
             f"<b>3.</b> Kullanıcı eklemek/yönetmek için <a href='/ofis'>Ofis Yönetim Paneli</a>'ni "
             f"kullanırsınız.</p></div>"
             f"<div class='foot'><a href='/'>← Girişe dön</a></div>")
    return _site_response("Üyelik alındı", inner)


def _provision_token_ok(request, data):
    """Webhook'un paylaşılan gizli jetonunu sabit zamanlı doğrular. Jeton başlıkta
    (X-Uyap-Provision-Token) ya da gövdede (token) olabilir."""
    if not PROVISION_TOKEN:
        return False
    supplied = (request.headers.get("X-Uyap-Provision-Token", "")
                or (data.get("token") if isinstance(data, dict) else "") or "")
    return hmac.compare_digest(str(supplied), PROVISION_TOKEN)


async def odeme_webhook(request):
    """Ödeme sağlayıcısının provision webhook'u (makine-makine). Paylaşılan jetonla korunur;
    jeton ayarlı değilse KAPALIDIR (fail-closed). Gövde JSON: {order_id, status|paid, token?}.
    Gerçek Iyzico/PayTR eklendiğinde: burada ayrıca sağlayıcının imzası doğrulanacak."""
    key = f"webhook:{_client_ip(request)}"
    wait = GUARD.blocked_for(key)
    if wait:
        return _rate_limited(wait)
    if not PROVISION_TOKEN:
        return web.json_response({"ok": False, "error": "webhook_disabled"}, status=503)
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    if not _provision_token_ok(request, data):
        GUARD.record_fail(key)
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    GUARD.record_ok(key)
    order_id = (data.get("order_id") or "").strip()
    status = str(data.get("status") or ("paid" if data.get("paid") else "")).strip().lower()
    if not order_id:
        return web.json_response({"ok": False, "error": "order_id_required"}, status=400)
    if status not in ("paid", "success", "completed", "ok"):
        STORE.update_order(order_id, status="failed", provider_status=status or "unknown")
        return web.json_response({"ok": True, "provisioned": False, "status": status or "unknown"})
    ok, res = _provision_order(order_id, request)
    if not ok:
        return web.json_response({"ok": False, "error": res}, status=409)
    return web.json_response({"ok": True, "provisioned": True, "order_id": res["order_id"],
                              "office_code": res["office_code"],
                              "master_username": res["master_username"]})


async def admin_order_provision(request):
    """Admin: 'manual' ödeme onayı — bekleyen siparişi elle karşılar (havale/EFT vb.)."""
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    ok, res = _provision_order((data.get("order_id") or "").strip(), request)
    if not ok:
        return _admin_page(msg=res)
    claim_url = f"{_public_base(request)}/satin-al/sonuc?claim={res['claim_token']}"
    return _admin_page(new_account={"username": res["master_username"], "password": res["password"]},
                       msg=f"Sipariş karşılandı, ofis oluşturuldu. Ofis kodu: {res['office_code']}. "
                           f"Giriş bilgileri (varsa) e-postayla gönderildi; tek-kullanımlık bağlantı: {claim_url}")


async def admin_order_cancel(request):
    resp = _admin_gate(request)
    if resp is not None:
        return resp
    data = await request.post()
    STORE.delete_order((data.get("order_id") or "").strip())
    return _admin_page(msg="Sipariş silindi.")


# ------------------------------------------------------------------------------------------
# Ofis self-servis — MASTER kullanıcının kendi üyelerini ve parolasını yönetmesi
# ------------------------------------------------------------------------------------------
# İki giriş kapısı, TEK yetki/iş mantığı:
#   • /api/office  : JSON API — masaüstü uygulamasının "Kullanıcılar" sekmesi kullanır
#                    (her istekte kullanıcı adı + parola gönderir; oturum tutulmaz).
#   • /ofis        : HTML panel — tarayıcıdan master girer; imzalı çerez ile oturum tutulur.
# Her ikisi de aşağıdaki _office_authorize + _office_action ortak mantığını çağırır; böylece
# yetki kontrolü (master mı? hedef aynı ofiste mi?) tek yerde toplanır.

def _login_id(username, office_code):
    """İki-alanlı login'i tek depo anahtarına indirger. Ofis kodu verilmiş ve kullanıcı adında
    '@' YOKSA birleştirir ('ahmet' + 'kemalburo' → 'ahmet@kemalburo'). '@' zaten varsa (tam
    kimlik girilmiş ya da eski tek-alan istemci) olduğu gibi bırakır → geriye dönük uyum."""
    username = (username or "").strip()
    office_code = (office_code or "").strip()
    if office_code and "@" not in username:
        return accounts.compose_username(office_code, username)
    return username


def _office_authorize(username, password):
    """Kullanıcı adı + parola doğrular ve MASTER yetkisini şart koşar.
    (info, error) döndürür. info: office_id, role, office_label, username…"""
    ok, reason, info = STORE.authenticate(username, password)
    if not ok:
        return None, reason
    if info.get("role") != "master":
        return None, "Bu işlem için master (ofis sahibi) yetkisi gerekir."
    return info, None


def _office_action(info, action, params):
    """Master'ın bir self-servis işlemini yürütür. (result_dict, error) döndürür.
    result_dict gerektiğinde {'username','password'} gibi BİR KEZ gösterilecek bilgi taşır.
    Yetki: hedef kullanıcı master'ın AYNI ofisinde olmalı; master kendini kilitleyemez."""
    office_id = info["office_id"]
    me = info["username"]

    def _same_office(target):
        u = STORE.users.get(target)
        return bool(u) and u.get("office_id") == office_id

    if action == "list":
        office = STORE.get_office(office_id) or {}
        return {"office_label": office.get("label", ""),
                "reset_to_master": office.get("reset_to_master", True),
                "my_email": (STORE.users.get(me, {}) or {}).get("email", ""),
                "users": STORE.listing_users(office_id)}, None

    if action == "add":
        new_user = (params.get("new_username") or "").strip()
        new_pw = (params.get("new_password") or "").strip() or None
        label = (params.get("label") or "").strip()
        email = (params.get("email") or "").strip()
        role = (params.get("role") or "member").strip()
        if role not in ("member", "master"):
            role = "member"
        if not new_user:
            return None, "Yeni kullanıcı adı gerekli."
        try:
            res = STORE.create_user(office_id, new_user, password=new_pw, role=role,
                                    label=label, email=email)
        except accounts.AccountError as e:
            return None, str(e)
        return {"username": res["username"], "password": res["password"], "role": res["role"]}, None

    if action == "set_email":
        target = (params.get("target") or "").strip()
        email = (params.get("email") or "").strip()
        if not _same_office(target):
            return None, "Kullanıcı bu ofiste bulunamadı."
        STORE.set_user_email(target, email)
        return {"username": target, "email": email}, None

    if action == "my_email":
        STORE.set_user_email(me, (params.get("email") or "").strip())
        return {"username": me, "email": (params.get("email") or "").strip()}, None

    if action == "policy":
        to_master = str(params.get("reset_to_master", "")).lower() in ("1", "true", "on", "yes")
        STORE.set_office_reset_policy(office_id, to_master)
        return {"reset_to_master": to_master}, None

    if action == "reset":
        target = (params.get("target") or "").strip()
        new_pw = (params.get("new_password") or "").strip() or None
        if not _same_office(target):
            return None, "Kullanıcı bu ofiste bulunamadı."
        try:
            pw = STORE.reset_user_password(target, password=new_pw)
        except accounts.AccountError as e:
            return None, str(e)
        if pw is None:
            return None, "Kullanıcı bulunamadı."
        return {"username": target, "password": pw}, None

    if action == "passwd":
        new_pw = (params.get("new_password") or "").strip()
        err = accounts.password_policy_error(new_pw)
        if err:
            return None, err
        STORE.reset_user_password(me, password=new_pw)
        return {"username": me, "changed": True}, None

    if action == "toggle":
        target = (params.get("target") or "").strip()
        active = str(params.get("active", "")).lower() in ("1", "true", "on", "yes", "aktif")
        if target == me:
            return None, "Kendinizi pasifleştiremezsiniz."
        if not _same_office(target):
            return None, "Kullanıcı bu ofiste bulunamadı."
        STORE.set_user_active(target, active)
        return {"username": target, "active": active}, None

    if action == "delete":
        target = (params.get("target") or "").strip()
        if target == me:
            return None, "Kendinizi silemezsiniz."
        if not _same_office(target):
            return None, "Kullanıcı bu ofiste bulunamadı."
        try:
            STORE.delete_user(target)
        except accounts.AccountError as e:
            return None, str(e)
        return {"username": target, "deleted": True}, None

    return None, "Bilinmeyen işlem."


async def office_api(request):
    """Masaüstü 'Kullanıcılar' sekmesinin JSON API'si. Gövde: {username, password, action, …}.
    Tarayıcı değil masaüstü çağırdığı için CORS/çerez yok; kimlik her istekte gönderilir."""
    guard_key = f"apioffice:{_client_ip(request)}"
    wait = GUARD.blocked_for(guard_key)
    if wait:
        return web.json_response(
            {"ok": False, "error": f"Çok fazla deneme. {wait} sn sonra tekrar deneyin."},
            status=429, headers={"Retry-After": str(wait), "Cache-Control": "no-store"})
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Geçersiz JSON."}, status=400)
    # İki-alanlı login: ayrı 'office_code' geldiyse kimlikle birleştir (geriye dönük uyumlu).
    username = _login_id(data.get("username"), data.get("office_code"))
    password = data.get("password") or ""
    action = (data.get("action") or "").strip()

    # "passwd" (kendi parolanı değiştir) HER kullanıcıya açıktır (master şartı YOK): kullanıcı
    # mevcut parolasıyla kimliğini kanıtlar, sonra yenisini belirler. Diğer tüm işlemler master ister.
    if action == "passwd":
        ok, reason, _ = STORE.authenticate(username, password)
        if not ok:
            GUARD.record_fail(guard_key)
            return web.json_response({"ok": False, "error": reason}, status=401)
        new_pw = (data.get("new_password") or "").strip()
        err = accounts.password_policy_error(new_pw)
        if err:
            return web.json_response({"ok": False, "error": err}, status=400)
        GUARD.record_ok(guard_key)
        STORE.reset_user_password(username, password=new_pw)
        return web.json_response({"ok": True, "username": username, "changed": True},
                                 headers={"Cache-Control": "no-store"})

    info, err = _office_authorize(username, password)
    if err:
        GUARD.record_fail(guard_key)
        return web.json_response({"ok": False, "error": err}, status=401)
    GUARD.record_ok(guard_key)
    result, err = _office_action(info, action, data)
    if err:
        return web.json_response({"ok": False, "error": err}, status=400)
    out = {"ok": True}
    out.update(result or {})
    return web.json_response(out, headers={"Cache-Control": "no-store"})


# ── /ofis oturum çerezi (imzalı, durumsuz) ────────────────────────────────────────────────
def _make_session(username: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    # Epoch: parola her değiştiğinde (reset_user_password/reset_password_with_token) artar
    # (bulgu #14) — bu token'ı o ana kadarki epoch'a bağlar, sonradan parola değişirse geçersiz olur.
    epoch = (STORE.users.get(username) or {}).get("sess_epoch", 0)
    payload = f"{username}|{epoch}|{exp}"
    sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode("utf-8")).decode("ascii")


def _read_session(request):
    """Geçerli oturum çerezindeki master kullanıcı adını döndürür (yoksa None). Kullanıcı
    hâlâ var, master ve aktif olmalı; token'daki epoch kullanıcının GÜNCEL epoch'uyla eşleşmeli
    (aksi halde parola bu token verildikten sonra değişmiş demektir — bulgu #14)."""
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    try:
        payload = base64.urlsafe_b64decode(tok.encode("ascii")).decode("utf-8")
        username, epoch, exp, sig = payload.rsplit("|", 3)
        expected = hmac.new(SESSION_SECRET.encode("utf-8"),
                            f"{username}|{epoch}|{exp}".encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp) < int(time.time()):
            return None
    except Exception:
        return None
    u = STORE.users.get(username)
    if not u or u.get("role") != "master" or not u.get("active", True):
        return None
    if str(u.get("sess_epoch", 0)) != epoch:
        return None
    return username


def _office_info_for(username):
    """Oturumdaki master için _office_action'ın beklediği info sözlüğünü kurar."""
    u = STORE.users.get(username) or {}
    office = STORE.offices.get(u.get("office_id")) or {}
    return {"username": username, "office_id": u.get("office_id"),
            "role": u.get("role"), "office_label": office.get("label", "")}


def _render_ofis_login(msg=None):
    note = f"<div class='msg'>{html.escape(msg)}</div>" if msg else ""
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP Ofis Paneli — Giriş</title>{_OFIS_CSS}</head><body>
  <div class='wrap'>
    <h1>Ofis Yönetim Paneli</h1>
    {note}
    <div class='card'>
      <p style='color:#94a3b8;font-size:13px'>Master (ofis sahibi) kullanıcı adınız ve parolanızla girin.</p>
      <form method='post' action='/ofis/login'>
        <input type='text' name='username' placeholder='kullanıcıadı@ofisadı (ör. ahmet@kemalburo)' autofocus required>
        <input type='password' name='password' placeholder='Parola' required>
        <button type='submit'>Giriş Yap</button>
      </form>
      <p style='margin-top:10px'><a href='/reset' style='color:#7dd3fc;font-size:13px'>Parolamı unuttum</a></p>
    </div>
  </div></body></html>"""


def _render_ofis_panel(username, new_cred=None, msg=None):
    info = _office_info_for(username)
    office_id = info["office_id"]
    office = STORE.get_office(office_id) or {}
    reset_to_master = office.get("reset_to_master", True)
    my_email = (STORE.users.get(username, {}) or {}).get("email", "")
    users = STORE.listing_users(office_id)
    rows = []
    for u in users:
        durum = ("<span style='color:#22c55e'>Aktif</span>" if u["active"]
                 else "<span style='color:#ef4444'>Pasif</span>")
        rol = "Master" if u["role"] == "master" else "Üye"
        uname = html.escape(u["username"])
        email = html.escape(u.get("email", "") or "")
        is_self = (u["username"] == username)
        # E-posta her satırda düzenlenebilir (master üyenin mailini girebilsin → reset çalışsın).
        email_cell = (f"<form method='post' action='/ofis/setemail' style='display:flex;gap:4px'>"
                      f"<input type='hidden' name='target' value='{uname}'>"
                      f"<input type='text' name='email' value='{email}' placeholder='e-posta' style='width:150px'>"
                      f"<button>Kaydet</button></form>")
        if is_self:
            actions = "<span style='color:#94a3b8;font-size:12px'>(siz)</span>"
        else:
            toggle = "0" if u["active"] else "1"
            toggle_lbl = "Pasifleştir" if u["active"] else "Aktifleştir"
            actions = f"""
              <form method='post' action='/ofis/reset'><input type='hidden' name='target' value='{uname}'><button>Parola Sıfırla</button></form>
              <form method='post' action='/ofis/toggle'><input type='hidden' name='target' value='{uname}'><input type='hidden' name='active' value='{toggle}'><button>{toggle_lbl}</button></form>
              <form method='post' action='/ofis/delete' onsubmit="return confirm('{uname} silinsin mi?')"><input type='hidden' name='target' value='{uname}'><button class='danger'>Sil</button></form>"""
        rows.append(f"""<tr><td><code>{uname}</code></td><td>{rol}</td>
          <td>{html.escape(u['label'])}</td><td>{email_cell}</td><td>{durum}</td>
          <td class='act'>{actions}</td></tr>""")
    table = "\n".join(rows) or "<tr><td colspan='6' style='text-align:center;color:#94a3b8'>Henüz kullanıcı yok.</td></tr>"

    # Politika seçenekleri (master'a / kullanıcıya)
    sel_master = "selected" if reset_to_master else ""
    sel_user = "" if reset_to_master else "selected"

    banner = ""
    if new_cred and new_cred.get("password"):
        banner = f"""<div class='new'><b>Bilgileri kullanıcıya iletin (parola yalnızca BİR KEZ gösterilir):</b>
          <div class='cred'>Kullanıcı Adı: <code>{html.escape(new_cred['username'])}</code></div>
          <div class='cred'>Parola: <code>{html.escape(new_cred['password'])}</code></div></div>"""
    note = f"<div class='msg'>{html.escape(msg)}</div>" if msg else ""
    label = html.escape(info.get("office_label") or "")

    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP Ofis Paneli</title>{_OFIS_CSS}</head><body>
  <div class='wrap'>
    <div class='top'><h1>Ofis Yönetim Paneli</h1>
      <form method='post' action='/ofis/logout'><button class='ghost'>Çıkış</button></form></div>
    <p style='color:#94a3b8;margin-top:0'>Ofis: <b>{label or '-'}</b> · Master: <code>{html.escape(username)}</code></p>
    {note}{banner}
    <div class='card'>
      <h3>Yeni Kullanıcı (Üye) Ekle</h3>
      <form method='post' action='/ofis/add'>
        <input type='text' name='new_username' placeholder='Kullanıcı adı (ör. katip1)' required>
        <input type='text' name='label' placeholder='Etiket (ör. Kâtip Ayşe)'>
        <input type='text' name='email' placeholder='E-posta (parola sıfırlama için)'>
        <input type='text' name='new_password' placeholder='Parola (boşsa otomatik üretilir)'>
        <button type='submit'>Ekle</button>
      </form>
    </div>
    <div class='card'>
      <h3>Ayarlar</h3>
      <form method='post' action='/ofis/myemail' style='margin-bottom:10px'>
        <label style='font-size:13px;color:#94a3b8'>Kendi e-postam (parola sıfırlama için):</label><br>
        <input type='text' name='email' value='{html.escape(my_email)}' placeholder='ornek@eposta.com'>
        <button type='submit'>Kaydet</button>
      </form>
      <form method='post' action='/ofis/policy'>
        <label style='font-size:13px;color:#94a3b8'>Alt kullanıcı parola sıfırlama maili kime gitsin?</label><br>
        <select name='reset_to_master' style='background:#0f172a;border:1px solid #475569;color:#f8fafc;padding:9px;border-radius:6px;margin:3px 4px'>
          <option value='1' {sel_master}>Bana (master) gelsin</option>
          <option value='0' {sel_user}>Kullanıcının kendisine gitsin</option>
        </select>
        <button type='submit'>Politikayı Kaydet</button>
      </form>
    </div>
    <div class='card'>
      <h3>Kendi Parolamı Değiştir</h3>
      <form method='post' action='/ofis/passwd'>
        <input type='password' name='new_password' placeholder='Yeni parola' required>
        <button type='submit'>Parolayı Güncelle</button>
      </form>
    </div>
    <div class='card'><h3>Kullanıcılar</h3>
      <table><thead><tr><th>Kullanıcı</th><th>Rol</th><th>Etiket</th><th>E-posta</th><th>Durum</th><th>İşlem</th></tr></thead>
      <tbody>{table}</tbody></table>
    </div>
  </div></body></html>"""


_OFIS_CSS = """<style>
  body{background:#0f172a;color:#f8fafc;font-family:Segoe UI,system-ui,sans-serif;margin:0;padding:24px}
  .wrap{max-width:920px;margin:0 auto}
  .top{display:flex;align-items:center;justify-content:space-between}
  h1{color:#0ea5e9;font-size:20px} h3{margin-top:0}
  .card{background:#1e293b;border-radius:10px;padding:18px;margin-bottom:16px}
  input{background:#0f172a;border:1px solid #475569;color:#f8fafc;padding:9px;border-radius:6px;width:240px;margin:3px 4px}
  button{background:#0ea5e9;border:0;color:#fff;padding:8px 12px;border-radius:6px;cursor:pointer;font-weight:600;margin:2px}
  button.danger{background:#ef4444} button.ghost{background:#334155}
  table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:8px;border-bottom:1px solid #334155;font-size:13px;vertical-align:top}
  code{background:#0f172a;padding:2px 6px;border-radius:4px;color:#7dd3fc}
  .act form{display:inline} .new{background:#064e3b;border:1px solid #22c55e;padding:14px;border-radius:8px;margin-bottom:14px}
  .cred{margin-top:6px;font-size:15px} .msg{background:#1e3a8a;padding:10px;border-radius:8px;margin-bottom:14px}
</style>"""


def _ofis_response(html_text, cookie=None, clear_cookie=False):
    # Referrer-Policy: no-referrer → sıfırlama sayfası URL'i (/reset?token=…) hiçbir alt-istek
    # ya da gezinmede Referer olarak SIZMAZ (bkz. güvenlik raporu #8).
    resp = web.Response(text=html_text, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    if cookie is not None:
        resp.set_cookie(SESSION_COOKIE, cookie, max_age=SESSION_TTL, httponly=True,
                        samesite="Lax", path="/")
    if clear_cookie:
        resp.del_cookie(SESSION_COOKIE, path="/")
    return resp


async def ofis_get(request):
    username = _read_session(request)
    if not username:
        return _ofis_response(_render_ofis_login())
    return _ofis_response(_render_ofis_panel(username))


async def ofis_login(request):
    key = f"ofis:{_client_ip(request)}"
    wait = GUARD.blocked_for(key)
    if wait:
        return _ofis_response(_render_ofis_login(
            msg=f"Çok fazla başarısız giriş. {wait} sn sonra tekrar deneyin."))
    data = await request.post()
    # İki-alanlı login: Ofis kodu + Kullanıcı → 'kullanici@ofis_kodu'.
    username = _login_id(data.get("username"), data.get("office_code"))
    password = data.get("password") or ""
    info, err = _office_authorize(username, password)
    if err:
        GUARD.record_fail(key)
        return _ofis_response(_render_ofis_login(msg=err))
    GUARD.record_ok(key)
    return _ofis_response(_render_ofis_panel(username, msg="Giriş başarılı."),
                          cookie=_make_session(username))


async def ofis_logout(request):
    # Epoch'u artır: bu token (ve aynı hesabın başka cihazdaki eşzamanlı oturumları) anında
    # geçersiz olur — logout artık yalnızca istemci çerezini silmekle kalmıyor (bulgu #14).
    username = _read_session(request)
    if username:
        STORE.bump_sess_epoch(username)
    return _ofis_response(_render_ofis_login(msg="Çıkış yapıldı."), clear_cookie=True)


async def _ofis_do(request, action):
    """Oturumlu master için bir self-servis işlemini yürütüp paneli yeniden çizer."""
    username = _read_session(request)
    if not username:
        return _ofis_response(_render_ofis_login(msg="Oturum gerekli."))
    data = await request.post()
    info = _office_info_for(username)
    result, err = _office_action(info, action, dict(data))
    if err:
        return _ofis_response(_render_ofis_panel(username, msg=err))
    new_cred = result if (result and result.get("password")) else None
    nice = {"add": "Kullanıcı eklendi.", "reset": "Parola sıfırlandı.",
            "passwd": "Parolanız güncellendi.", "toggle": "Kullanıcı durumu değişti.",
            "delete": "Kullanıcı silindi.", "set_email": "E-posta güncellendi.",
            "my_email": "E-postanız güncellendi.",
            "policy": "Sıfırlama politikası güncellendi."}.get(action, "Tamam.")
    return _ofis_response(_render_ofis_panel(username, new_cred=new_cred, msg=nice))


async def ofis_add(request):     return await _ofis_do(request, "add")
async def ofis_reset(request):   return await _ofis_do(request, "reset")
async def ofis_passwd(request):  return await _ofis_do(request, "passwd")
async def ofis_toggle(request):  return await _ofis_do(request, "toggle")
async def ofis_delete(request):  return await _ofis_do(request, "delete")
async def ofis_setemail(request): return await _ofis_do(request, "set_email")
async def ofis_myemail(request):  return await _ofis_do(request, "my_email")
async def ofis_policy(request):   return await _ofis_do(request, "policy")


# ── Parola sıfırlama (forgot-password): talep + jetonla yeni parola ────────────────────────
# Login bağlamı DIŞINDA çalışan TEK güvenli akış: kullanıcı parolayı değiştirmez; sistem
# kayıtlı e-postaya kısa ömürlü bir bağlantı yollar (ofis politikasına göre master'a ya da
# kullanıcının kendisine). Bağlantı jetonu doğrulanınca yeni parola belirlenir.
def _render_reset_request(msg=None, ok=False):
    cls = "msg ok" if ok else "msg"
    note = f"<div class='{cls}'>{html.escape(msg)}</div>" if msg else ""
    if MAIL_ENABLED:
        test_note = ""
    elif MAIL_TEST_MODE:
        test_note = ("<p style='color:#f59e0b;font-size:12px'>Not: E-posta gönderici kurulmadı "
                     "(DEV TEST modu). Bağlantı yalnızca yerel sunucu günlüğüne yazılır.</p>")
    else:
        test_note = ("<p style='color:#f59e0b;font-size:12px'>Not: Parola sıfırlama şu an "
                     "kullanılamıyor (e-posta gönderimi yapılandırılmamış). Lütfen yöneticinize başvurun.</p>")
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP — Parola Sıfırlama</title>{_OFIS_CSS}
<style>.msg.ok{{background:#064e3b;border:1px solid #22c55e}}</style></head><body>
  <div class='wrap'>
    <h1>Parola Sıfırlama</h1>
    {note}
    <div class='card'>
      <p style='color:#94a3b8;font-size:13px'>Kullanıcı adınızı (kullanıcıadı@ofisadı) ya da e-postanızı girin.
      Ofis ayarına göre sıfırlama bağlantısı size ya da büronuzun master kullanıcısına e-posta ile gönderilir.</p>
      <form method='post' action='/reset'>
        <input type='text' name='identifier' placeholder='kullanıcıadı@ofisadı veya e-posta' autofocus required>
        <button type='submit'>Sıfırlama Bağlantısı Gönder</button>
      </form>
      {test_note}
    </div>
  </div></body></html>"""


def _render_reset_set(token, msg=None):
    note = f"<div class='msg'>{html.escape(msg)}</div>" if msg else ""
    return f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>UYAP — Yeni Parola</title>{_OFIS_CSS}</head><body>
  <div class='wrap'>
    <h1>Yeni Parola Belirle</h1>
    {note}
    <div class='card'>
      <form method='post' action='/reset'>
        <input type='hidden' name='token' value='{html.escape(token)}'>
        <input type='password' name='new_password' placeholder='Yeni parola ({accounts.PASSWORD_POLICY_HINT})' autofocus required minlength='{accounts.MIN_PASSWORD_LENGTH}'>
        <button type='submit'>Parolayı Belirle</button>
      </form>
    </div>
  </div></body></html>"""


async def reset_get(request):
    token = (request.query.get("token") or "").strip()
    if token:
        if STORE.peek_reset_token(token):
            return _ofis_response(_render_reset_set(token))
        return _ofis_response(_render_reset_request(
            msg="Sıfırlama bağlantısı geçersiz ya da süresi dolmuş. Yeniden talep edin.", ok=False))
    return _ofis_response(_render_reset_request())


async def reset_post(request):
    data = await request.post()
    token = (data.get("token") or "").strip()
    if token:  # yeni parola belirleme
        ok, res = STORE.reset_password_with_token(token, data.get("new_password"))
        if ok:
            return _ofis_response(_render_reset_request(
                msg=f"Parola güncellendi ('{res}'). Artık yeni parolanızla girebilirsiniz.", ok=True))
        return _ofis_response(_render_reset_set(token, msg=res))
    # sıfırlama TALEBİ — res zaten tek-tip mesajdır (enumeration savunması). Ofis kodu verildiyse
    # çıplak kullanıcı adıyla birleştirilir; e-posta girildiyse (@ içerir) olduğu gibi kalır.
    ident = _login_id(data.get("identifier"), data.get("office_code"))
    ok, res = _do_reset_request(request, ident)
    return _ofis_response(_render_reset_request(msg=res, ok=ok))


async def api_reset(request):
    """Masaüstü 'Parolamı unuttum' akışı. Gövde: {identifier}. Genel/teşhis edici yanıt döner."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Geçersiz JSON."}, status=400)
    ok, res = _do_reset_request(request, _login_id(data.get("identifier"), data.get("office_code")))
    # res tek-tip mesajdır; alıcı e-posta/varlık bilgisi DÖNDÜRÜLMEZ (enumeration savunması).
    if ok:
        return web.json_response({"ok": True, "message": res, "test_mode": not MAIL_ENABLED})
    return web.json_response({"ok": False, "error": res}, status=429 if "Çok fazla" in res else 400)


# ------------------------------------------------------------------------------------------
# Otomatik oda anahtarı döndürme (rotation) — düzensiz aralıklarla, şeffaf
# ------------------------------------------------------------------------------------------
# Buluşma KARARLI office_id ile yapılır; bu yüzden room_key dönmesi CANLI bağlantıları
# ETKİLEMEZ. Kullanıcı kullanıcı adıyla giriş yaptığı için dönmeyi de hissetmez. Tek faydası:
# sızan/eski bir oda jetonunun ömrünü kısaltmak (savunma). Aralık env ile ayarlanır.
async def _rotation_loop():
    lo = int(os.environ.get("UYAP_ROTATE_MIN", "1800"))   # alt sınır (sn) — vars. 30 dk
    hi = max(lo + 1, int(os.environ.get("UYAP_ROTATE_MAX", "5400")))  # üst sınır — vars. 90 dk
    while True:
        await asyncio.sleep(random.uniform(lo, hi))  # düzensiz/tahmin edilemez aralık
        try:
            n = 0
            for oid in list(STORE.offices.keys()):
                if STORE.rotate_room_key(oid):
                    n += 1
            if n:
                print(f"[*] Otomatik döndürme: {n} ofisin oda anahtarı yenilendi.")
        except Exception as e:
            print(f"[!] Oda döndürme hatası: {e}")


async def _start_rotation(app):
    app["rotation_task"] = asyncio.ensure_future(_rotation_loop())


async def _stop_rotation(app):
    t = app.get("rotation_task")
    if t:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


# ------------------------------------------------------------------------------------------
# Statik webapp + dinamik config
# ------------------------------------------------------------------------------------------
def _bootstrap_owner():
    """UYAP_OWNER_PASSWORD verilmişse 'utku' owner hesabını (yoksa) oluşturur. TOTP gizli anahtarı
    yalnızca İLK oluşturmada ÜRETİLİP bir kez konsola yazılır (operatör authenticator'a ekler).
    Anahtar kaybolduysa/hiç eklenemediyse: UYAP_OWNER_TOTP_SECRET env'ine kendi base32 anahtarınızı
    yazıp yeniden başlatın — açılışta owner'ın TOTP anahtarı bu değere ÇEKİLİR (env'dekiyle aynıysa
    dokunulmaz); aynı anahtarı authenticator uygulamanıza elle girersiniz.
    Kaynağa parola gömülmez; owner yoksa /admin kapalıdır."""
    env_secret = (os.environ.get("UYAP_OWNER_TOTP_SECRET", "") or "").strip().replace(" ", "").upper()
    if OWNER_BOOTSTRAP_PASSWORD:
        try:
            res = STORE.ensure_owner(OWNER_BOOTSTRAP_PASSWORD, totp_secret=env_secret or None)
        except accounts.AccountError as e:
            print(f"[!] Owner bootstrap başarısız: {e}")
            return
        if res.get("created"):
            secret = res["totp_secret"]
            uri = accounts.totp_uri(secret, accounts.OWNER_USERNAME)
            print("\n" + "=" * 72)
            print(f"[+] Owner hesabı oluşturuldu: {accounts.OWNER_USERNAME} (ofis kodu GEREKMEZ).")
            print("    İKİ-ADIMLI DOĞRULAMA (TOTP) — bu anahtarı authenticator uygulamanıza ekleyin.")
            print("    Bu bilgi YALNIZCA ŞİMDİ gösterilir, tekrar gösterilmez:")
            print(f"      TOTP secret : {secret}")
            print(f"      otpauth URI : {uri}")
            print(f"    Giriş: /owner/login  (kullanıcı={accounts.OWNER_USERNAME} + parola + 6 haneli kod)")
            print("=" * 72 + "\n")
            return
        # Owner zaten var: env = kaynak-doğruluk. Parola env'dekinden farklıysa env'dekine EŞİTLE
        # (operatör env'i değiştirdiyse eski parolayla kilitli kalmasın).
        try:
            if STORE.sync_owner_password(accounts.OWNER_USERNAME, OWNER_BOOTSTRAP_PASSWORD):
                print(f"[+] Owner ({accounts.OWNER_USERNAME}) parolası UYAP_OWNER_PASSWORD ile eşitlendi.")
        except accounts.AccountError as e:
            print(f"[!] UYAP_OWNER_PASSWORD mevcut politikayı sağlamıyor, parola GÜNCELLENMEDİ: {e}")
        # Owner pasife düşmüşse env bootstrap onu geri açar (pasif owner panele giremez →
        # kendini düzeltemez; authenticate pasif hesabı da genel hata mesajıyla reddeder).
        if STORE.reactivate_owner(accounts.OWNER_USERNAME):
            print(f"[+] Owner ({accounts.OWNER_USERNAME}) hesabı PASİF idi → yeniden AKTİF edildi.")
    elif STORE.get_owner() is None:
        print("[!] UYAP_OWNER_PASSWORD ayarlı değil ve owner (utku) yok → /admin KAPALI. "
              "Owner oluşturmak için UYAP_OWNER_PASSWORD ile bir kez başlatın.")
        return
    # Env'de TOTP anahtarı verildiyse ve mevcut anahtardan FARKLIYSA uygula (kurtarma).
    owner = STORE.get_owner() or {}
    if env_secret and env_secret != owner.get("totp_secret", ""):
        try:
            STORE.set_owner_totp_secret(accounts.OWNER_USERNAME, env_secret)
        except accounts.AccountError as e:
            print(f"[!] UYAP_OWNER_TOTP_SECRET uygulanamadı: {e}")
            return
        print(f"[+] Owner ({accounts.OWNER_USERNAME}) TOTP anahtarı UYAP_OWNER_TOTP_SECRET ile "
              "GÜNCELLENDİ. Aynı anahtarı authenticator uygulamanıza da ekleyin "
              "(Google Authenticator → 'Kurulum anahtarı gir', zaman tabanlı).")
    else:
        print(f"[*] Owner ({accounts.OWNER_USERNAME}) zaten var → mevcut TOTP anahtarı korundu. "
              "Giriş: /owner/login")
    # Teşhis: aktif anahtarın parmak izi (ilk 4 karakter — anahtarın kendisi LOGLANMAZ).
    # Authenticator'daki kayıtla eşleşmiyorsa kodlar asla tutmaz.
    cur = (STORE.get_owner() or {}).get("totp_secret", "")
    if cur:
        print(f"[*] Aktif owner TOTP anahtarı parmak izi: {cur[:4]}…({len(cur)} karakter).")


def _warn_db_overlap():
    """Hesap deposu (accounts) ile kullanıcının icra DB'sinin (uyap_icra) KARIŞMAMASI gerekir.
    DATABASE_URL yanlışlıkla yerel uyap_icra'ya işaret ediyorsa uyarır (iki-DB ayrımı, plan #5)."""
    dsn = (os.environ.get("DATABASE_URL", "") or "").lower()
    if dsn and "uyap_icra" in dsn:
        print("[!] DİKKAT: DATABASE_URL 'uyap_icra' içeriyor. Hesap deposu (offices/users) ile "
              "kullanıcının icra verisi AYNI veritabanına yazılmamalı — ayrı bir DB kullanın.")


def make_app(args):
    global OPEN_MODE
    _bootstrap_owner()
    _warn_db_overlap()
    load_allowed(args.config)
    OPEN_MODE = bool(getattr(args, "open_mode", False)) or _env_open_mode()
    if ALLOWED is None and STORE.is_empty():
        if OPEN_MODE:
            print("[!] AÇIK-MOD: hesap deposu boş + allowlist yok → oda adını bilen herkes "
                  "kimliksiz katılabilir. Yalnızca yerel/dev için; üretimde KAPATIN.")
        else:
            print("[i] Hesap deposu boş + allowlist yok → kimliksiz buluşma REDDEDİLİR "
                  "(güvenli varsayılan). Yerel/dev için --open ya da UYAP_SIGNALING_OPEN=1.")
    app = web.Application(middlewares=[_security_mw])

    def _is_local(request):
        return (request.host or "").split(":")[0] in ("127.0.0.1", "localhost")

    def config_js(request):
        # signaling: AYNI origin'in /ws'i (tarayıcı location'dan türetir) -> boş bırakıyoruz.
        ice = build_ice(_is_local(request), args.ice)
        cfg = {"signaling": "", "room": args.room, "ice": ice}
        body = "window.UYAP_CONFIG = " + json.dumps(cfg, ensure_ascii=False) + ";\n"
        return web.Response(body=body.encode("utf-8"), content_type="application/javascript",
                            charset="utf-8", headers={"Cache-Control": "no-store"})

    async def ice_endpoint(request):
        # Masaüstü ofis/istemci ICE'ı (efemeral TURN dahil) buradan çeker.
        return web.json_response({"iceServers": build_ice(_is_local(request), args.ice)},
                                 headers={"Cache-Control": "no-store"})

    def serve_file(disk_rel, content_type, sw=False):
        async def handler(_request):
            path = os.path.join(WEBAPP_DIR, disk_rel)
            if not os.path.isfile(path):
                return web.Response(status=404, text="Bulunamadı.")
            with open(path, "rb") as f:
                data = f.read()
            # Uygulama kabuğu (index.html, tunnel.js, wire.js, sw.js) küçük ve sık güncellenir;
            # no-store ile tarayıcı her zaman taze indirir (eski sürüm yapışıp kalmaz). UYAP'ın
            # asıl statik varlıkları zaten SW Cache API'de tutuluyor; bu onları etkilemez.
            headers = {"Cache-Control": "no-store"}
            if sw:
                headers["Service-Worker-Allowed"] = "/"  # SW'nin "/" kapsamı için şart
            ct = content_type.split(";")[0].strip()
            return web.Response(body=data, content_type=ct, charset="utf-8", headers=headers)
        return handler

    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/__app__/config.js", config_js)
    app.router.add_get("/ice", ice_endpoint)
    # Owner (utku) girişi — TOTP 2FA'lı; /admin'i owner oturumu korur.
    app.router.add_get("/owner/login", owner_login_get)
    app.router.add_post("/owner/login", owner_login_post)
    app.router.add_post("/owner/logout", owner_logout)
    # Üye ol (self-servis üyelik → sipariş).
    app.router.add_get("/uye-ol", uye_ol_get)
    app.router.add_post("/uye-ol", uye_ol_post)
    app.router.add_get("/indir", indir_get)          # ofis programı kurulum sayfası
    app.router.add_get("/indir/dosya", indir_dosya)  # gömülü zip indirme
    # Admin (lisans/kullanıcı oluşturma) — owner (utku) oturumu ile korunur.
    app.router.add_get("/admin", admin_get)
    app.router.add_post("/admin/create", admin_create)
    app.router.add_post("/admin/revoke", admin_revoke)
    app.router.add_post("/admin/activate", admin_activate)
    app.router.add_post("/admin/reset", admin_reset)
    app.router.add_post("/admin/rotate", admin_rotate)
    app.router.add_post("/admin/delete", admin_delete)

    # Ofis self-servis: master kendi üyelerini + parolasını yönetir.
    app.router.add_post("/api/office", office_api)   # masaüstü "Kullanıcılar" sekmesi
    app.router.add_get("/ofis", ofis_get)            # tarayıcı paneli (oturumlu)
    app.router.add_post("/ofis/login", ofis_login)
    app.router.add_post("/ofis/logout", ofis_logout)
    app.router.add_post("/ofis/add", ofis_add)
    app.router.add_post("/ofis/reset", ofis_reset)
    app.router.add_post("/ofis/passwd", ofis_passwd)
    app.router.add_post("/ofis/toggle", ofis_toggle)
    app.router.add_post("/ofis/delete", ofis_delete)
    app.router.add_post("/ofis/setemail", ofis_setemail)
    app.router.add_post("/ofis/myemail", ofis_myemail)
    app.router.add_post("/ofis/policy", ofis_policy)
    # Parola sıfırlama (login dışı, e-posta tabanlı) + masaüstü API.
    app.router.add_get("/reset", reset_get)
    app.router.add_post("/reset", reset_post)
    app.router.add_post("/api/reset", api_reset)
    # Admin: ofise manuel kullanıcı atama.
    app.router.add_post("/admin/adduser", admin_adduser)
    # Admin: sipariş (satın alma) onayı/iptali.
    app.router.add_post("/admin/order-provision", admin_order_provision)
    app.router.add_post("/admin/order-cancel", admin_order_cancel)

    # Satış / ödeme (Kalem 4 — iskelet): landing + sipariş + webhook + giriş bilgisi teslimi.
    app.router.add_get("/satin-al", satin_al_get)
    app.router.add_post("/satin-al", satin_al_post)
    app.router.add_get("/satin-al/sonuc", cred_claim_get)
    app.router.add_post("/odeme/webhook", odeme_webhook)

    # Oda anahtarlarını düzensiz (rastgele) aralıklarla otomatik döndüren arka plan görevi.
    app.on_startup.append(_start_rotation)
    app.on_cleanup.append(_stop_rotation)
    for url_path, (disk_rel, ctype) in ROUTES.items():
        app.router.add_get(url_path, serve_file(disk_rel, ctype, sw=url_path.endswith("/sw.js")))

    async def favicon(_request):
        return web.Response(status=204)
    app.router.add_get("/favicon.ico", favicon)

    # ── SEO uçları (CANLIYA_HAZIRLIK.md Faz 6) ──────────────────────────────────────────
    # robots.txt: özel sayfaları taramadan da düşür; sitemap adresini bildir.
    # sitemap.xml: yalnızca herkese açık üç sayfa. Taban adres kanonik host'tan (yoksa
    # istekteki origin'den) türetilir — domain değişirse kod değişmez.
    async def robots_txt(request):
        # Özel sayfaların yolları burada listelenmiyor (bulgu #16): robots.txt HERKESE açık
        # bir dosyadır, Disallow girdileri saldırgana bedava keşif (/admin, /owner vb. var
        # olduğunu) sağlar. Aynı işi (arama motorlarına gösterme) zaten her yanıta eklenen
        # X-Robots-Tag: noindex, nofollow başlığı görüyor (bkz. _security_mw) — bu da index
        # dışı bırakır ama yol listesini yayınlamaz.
        base = f"https://{CANONICAL_HOST}" if CANONICAL_HOST else _public_base(request)
        body = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
        return web.Response(text=body, content_type="text/plain", charset="utf-8")

    async def sitemap_xml(request):
        base = f"https://{CANONICAL_HOST}" if CANONICAL_HOST else _public_base(request)
        urls = "".join(f"<url><loc>{base}{p}</loc></url>"
                       for p in ("/", "/uye-ol", "/satin-al", "/indir"))
        body = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{urls}</urlset>")
        return web.Response(text=body, content_type="application/xml", charset="utf-8")

    app.router.add_get("/robots.txt", robots_txt)
    app.router.add_get("/sitemap.xml", sitemap_xml)
    return app


def main():
    parser = argparse.ArgumentParser(description="UYAP satıcı sunucusu (statik webapp + signaling).")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Dinleme adresi (PaaS: 0.0.0.0; yerel test isterseniz 127.0.0.1).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")),
                        help="Port (PaaS PORT ortam değişkenini otomatik kullanır).")
    parser.add_argument("--room", default=os.environ.get("UYAP_ROOM", ""),
                        help="Varsayılan oda (yerel test kolaylığı). Üretimde URL'de ?room=<ODA>.")
    parser.add_argument("--ice", default=os.environ.get("UYAP_ICE", ""),
                        help="ICE sunucuları JSON listesi (boşsa: yerelde yok, uzakta STUN).")
    parser.add_argument("--config", default=CONFIG_PATH, help="Oda allowlist dosyası.")
    parser.add_argument("--open", dest="open_mode", action="store_true",
                        help="Hesap deposu boş + allowlist yokken kimliksiz 'serbest oda'a izin "
                             "ver (YALNIZCA yerel/dev; üretimde kullanmayın).")
    parser.add_argument("--ssl-certfile", default=None)
    parser.add_argument("--ssl-keyfile", default=None)
    args = parser.parse_args()

    if not os.path.isdir(WEBAPP_DIR):
        print(f"[!] webapp/ klasörü bulunamadı: {WEBAPP_DIR}")
        sys.exit(2)

    ssl_ctx = None
    if args.ssl_certfile and args.ssl_keyfile:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(args.ssl_certfile, args.ssl_keyfile)
    scheme = "https" if ssl_ctx else "http"

    app = make_app(args)
    print(f"[*] Satıcı sunucusu {scheme}://{args.host}:{args.port}/ "
          f"(webapp + /ws signaling tek serviste).")
    print(f"[*] Tarayıcı: {scheme}://{args.host}:{args.port}/?room=<ODA>")
    print(f"[*] Ofis:     office_agent.py --signaling {('wss' if ssl_ctx else 'ws')}://"
          f"{args.host}:{args.port}/ws --room <ODA>")
    if scheme == "http" and args.host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        print("[!] DİKKAT: Service Worker yalnızca HTTPS ya da localhost'ta çalışır.")

    no = len(STORE.offices)
    nu = len(STORE.users)
    print(f"[*] Hesap deposu: {no} ofis / {nu} kullanıcı ({accounts.ACCOUNTS_PATH})."
          + ("" if nu else (" Boş → açık-mod AÇIK (kimliksiz serbest oda)." if OPEN_MODE
                            else " Boş → kimliksiz buluşma REDDEDİLİR (güvenli varsayılan).")))
    if STORE.get_owner() is not None:
        print(f"[*] Owner girişi: {scheme}://{args.host}:{args.port}/owner/login "
              f"(kullanıcı={accounts.OWNER_USERNAME} + parola + TOTP) → /admin.")
    else:
        print("[!] Owner (utku) yok → /admin KAPALI. UYAP_OWNER_PASSWORD ile bir kez başlatın.")
    print(f"[*] Üye ol: {scheme}://{args.host}:{args.port}/uye-ol")
    print(f"[*] Ofis paneli (master): {scheme}://{args.host}:{args.port}/ofis  ·  "
          f"Parola sıfırlama: {scheme}://{args.host}:{args.port}/reset  ·  "
          f"Masaüstü API: {scheme}://{args.host}:{args.port}/api/office")
    print("[*] E-posta: " + ("SMTP yapılandırıldı (gerçek gönderim)." if MAIL_ENABLED
                             else "TEST modu — gönderici kurulmadı, sıfırlama bağlantısı konsola yazılır."))
    if SALES_ENABLED:
        print(f"[*] Satış: {scheme}://{args.host}:{args.port}/satin-al (sağlayıcı={PAYMENT_PROVIDER}). "
              + (f"Webhook /odeme/webhook AÇIK (jeton doğrulamalı)." if PROVISION_TOKEN
                 else "Webhook KAPALI (UYAP_PROVISION_TOKEN yok) → onay yalnızca /admin'den."))
    else:
        print("[!] Satış devre dışı (UYAP_SALES_ENABLED=0).")
    print("[*] Güvenlik başlıkları: nosniff + X-Frame-Options:SAMEORIGIN + Referrer-Policy + CSP + Permissions-Policy açık; "
          + (f"HTTPS'te HSTS {'AÇIK' if HSTS_ENABLED else 'kapalı'} + Secure çerez.")
          + (f" Kanonik host: {CANONICAL_HOST} (GET/HEAD 308 yönlendirme)." if CANONICAL_HOST
             else " Kanonik host ayarlı değil (özel alan adı bağlarsanız UYAP_CANONICAL_HOST)."))
    if _turn_servers():
        print("[*] TURN: efemeral kimlikli TURN etkin (CGNAT/mobil veri desteklenir).")
    else:
        print("[!] TURN yok (yalnızca STUN). CGNAT ardındaki bazı istemciler bağlanamayabilir.")

    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
