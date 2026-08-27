#!/usr/bin/env python3
"""
UYAP Ofis Ajanı (paylaşan taraf)
--------------------------------
Ofis bilgisayarında, e-imza kartının takılı olduğu makinede çalışır. TEK UYAP oturumunu
kurar/canlı tutar ve onu İKİ yoldan paylaşır:

  1. YEREL AĞ (LAN): uyap_proxy'nin FastAPI sunucusunu 0.0.0.0:<port>'ta açar. Aynı ağdaki
     bir istemci doğrudan bu adrese bağlanır (en hızlı yol, tünel yok).

  2. DIŞ AĞ (WebRTC): satıcının signaling sunucusuna DIŞARI doğru bir WebSocket açar
     (NAT/firewall sorunsuz geçer; port yönlendirme YOK). Ev istemcisinin WebRTC teklifini
     yanıtlar; kurulan DataChannel üzerinden gelen HTTP isteklerini UYAP'a uygular.

Her iki yol da AYNI `uyap_proxy.gw` oturumunu paylaşır: UYAP tek oturum/tek IP görür,
"başka oturum aktif" uyarısına düşmez. UYAP trafiği signaling'den GEÇMEZ; veri ofis ile
istemci arasında doğrudan akar. Satıcı veri yolunda değildir.

Çalıştırma (ofis):
    pip install aiortc websockets   # httpx/fastapi/uvicorn zaten kurulu olmalı
    set UYAP_PIN=...                 # ya da --pin
    python office_agent.py --signaling wss://senin-sunucun:9000/ws --room <ODA> --cert-id <id>
"""

import os
import sys
import time
import json
import base64
import socket
import hashlib
import hmac
import secrets
import asyncio
import argparse

import httpx
import websockets
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription

from . import uyap_proxy
from . import jobs
from ._runtime import interactive_write, WRITE_METHODS as _WRITE_METHODS
from .p2p_wire import Reassembler, send_message, load_ice_servers, fetch_ice_servers, AGENT_PREFIX

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _build_forward_headers(headers: dict) -> dict:
    """uyap_proxy._build_forward_headers'ın dict sürümü (elimizde FastAPI Request yok)."""
    out = {k: v for k, v in (headers or {}).items() if k.lower() not in uyap_proxy.DROP_REQUEST}
    out["Host"] = uyap_proxy.UYAP_HOST
    # Accept-Encoding set ETMİYORUZ: httpx varsayılanıyla UYAP cevabı sıkıştırılmış iner,
    # resp.content çözülmüş gelir; UYAP→ofis transferi hızlanır, yeniden yazım yine düz metinde.
    return out


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_cache")


def should_cache(method: str, path: str) -> bool:
    if method.upper() != "GET":
        return False
    # İş-kuyruğu kontrol yolları ASLA önbelleğe alınmaz (durum sorguları taze dönmeli).
    if path.lstrip("/").startswith(AGENT_PREFIX):
        return False
    path_lower = path.lower()
    if "/static/" in path_lower or "static/" in path_lower:
        return True
    if any(path_lower.endswith(ext) for ext in [".js", ".css", ".woff2", ".woff", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".gif"]):
        return True
    # HTML shell paths (root, no extension, or .html/.jsp)
    if path == "" or path_lower in ("index.html", "index.jsp", "giris"):
        return True
    # If the last segment of the path doesn't contain a dot, it's a SPA page route
    last_segment = path_lower.split("/")[-1]
    if last_segment and "." not in last_segment:
        return True
    return False


def get_cached_response(path: str, proxy_base: str, query: str = ""):
    if not os.path.exists(CACHE_DIR):
        return None
    h = _cache_key(path, proxy_base, query)
    cache_path = os.path.join(CACHE_DIR, h)
    meta_path = cache_path + ".meta"
    if os.path.exists(cache_path) and os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            with open(cache_path, "rb") as f:
                body = f.read()
            return meta["status"], meta["headers"], body
        except Exception:
            pass
    return None


def save_response_to_cache(path: str, proxy_base: str, status: int, headers: dict, body: bytes, query: str = ""):
    if status != 200:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = _cache_key(path, proxy_base, query)
    cache_path = os.path.join(CACHE_DIR, h)
    meta_path = cache_path + ".meta"
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"status": status, "headers": headers}, f, ensure_ascii=False)
        with open(cache_path, "wb") as f:
            f.write(body)
    except Exception:
        pass


# ── Paylaşılan önbellekte tazelik (stale-while-revalidate) ───────────────────────────
# Kabuk (arayüz HTML'i) tazelik süresini geçince hâlâ ANINDA verilir ama arkada yeniden
# çekilip güncellenir. Böylece ofisteki ortak önbellek tüm istemcilere hem hızlı hem
# güncel kalır. Statik varlıklar (js/css/resim) nadiren değişir; onlar tazelenmez.
SHELL_TTL = 300  # saniye

REFRESHING = set()  # arkada tazelenmekte olan path'ler (çift tazelemeyi önler)


def _is_shell(path: str) -> bool:
    p = path.lower()
    last = p.split("/")[-1]
    return path == "" or p in ("index.html", "index.jsp", "giris") or (bool(last) and "." not in last)


def _cache_key(path: str, proxy_base: str, query: str) -> str:
    """Önbellek dosya adı (md5). KABUKLARA enjekte-script sürümü eklenir: enjekte JS
    değişince anahtar değişir → eski kabuk otomatik geçersiz olur, uzak istemciler bayat
    enjeksiyon almaz. Statik varlıklara (js/css/resim) eklenmez; enjeksiyon almazlar,
    gereksiz yere yeniden indirilmesinler."""
    key = f"{path}?{query}::{proxy_base}"
    if _is_shell(path):
        key += "::iv=" + getattr(uyap_proxy, "INJECT_VERSION", "")
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _cache_age(path: str, proxy_base: str, query: str):
    """Önbellekteki kaydın yaşını (saniye) döndürür; kayıt yoksa None."""
    h = _cache_key(path, proxy_base, query)
    try:
        return time.time() - os.path.getmtime(os.path.join(CACHE_DIR, h))
    except OSError:
        return None


async def _fetch_uyap(method, path, query, body, fwd_headers, proxy_base, store):
    """UYAP'a tek isteği uygular (login duvarı görülürse bir kez yeniden giriş + tekrar),
    cevabı yeniden yazar ve istenirse paylaşılan önbelleğe yazar. (status, headers, body)."""
    gw = uyap_proxy.gw
    target = f"{uyap_proxy.UYAP_BASE}/{path}"
    if query:
        target += "?" + query

    async def forward():
        return await gw.client.request(method, target, content=body, headers=fwd_headers)

    resp = await forward()
    # "İletirken oturumu kontrol et": UYAP login duvarı döndüyse kilitli yeniden giriş.
    if uyap_proxy._looks_like_login_wall(resp):
        await gw.relogin()
        resp = await forward()

    # Enjekte script'i de GÖM: (a) localStorage loginState=true ile UYAP SPA'sının istemci
    # tarafı giriş kontrolünü atlatır, (b) IndexedDB SWR ile anında render.
    out_body = uyap_proxy._rewrite_body(
        resp.headers.get("content-type", ""), resp.content, proxy_base, inject=True)
    out_headers = uyap_proxy._build_response_headers(resp, proxy_base)
    if store and resp.status_code == 200:
        save_response_to_cache(path, proxy_base, resp.status_code, out_headers, out_body, query)
    return resp.status_code, out_headers, out_body


async def _background_refresh(method, path, query, body, fwd_headers, proxy_base):
    """Bayatlamış kabuğu arkada sessizce tazeler (istemciyi bekletmez)."""
    if path in REFRESHING:
        return
    REFRESHING.add(path)
    try:
        await _fetch_uyap(method, path, query, body, fwd_headers, proxy_base, store=True)
    except Exception:
        pass
    finally:
        REFRESHING.discard(path)


ACTIVE_DOWNLOADS = {}  # path -> asyncio.Event

# Çok-istemcili modda UYAP'a giden DEĞİŞTİREN istekleri (POST/PUT/PATCH/DELETE) sıraya alır:
# iki kullanıcının form gönderimi / çok adımlı sihirbaz işlemleri birbirine girip UYAP'ın
# sunucu-tarafı oturum durumunu bozmasın diye tek tek (FIFO) işlenir. asyncio.Lock bekleyenleri
# geliş sırasında uyandırır. GET'ler (okuma/gezinme/statik) KİLİTLENMEZ: tarayıcı bunları zaten
# paralel ister ve UYAP tek oturumda eşzamanlı okumayı normal karşılar; aksi halde tek
# kullanıcının sayfası bile gereksiz yavaşlardı.
# NOT: _WRITE_METHODS ve UYAP_WRITE_LOCK artık uyap_core._runtime'dan gelir; iş kuyruğu
# (jobs.py) ile AYNI kilidi paylaşırlar (anlık istek + toplu iş aynı oturumu bozmasın).


async def handle_uyap_request(meta: dict, body: bytes):
    """Bir HTTP isteğini UYAP'a uygular; (status, headers, body) döndürür.
    Önbellek ofiste tutulur ve TÜM istemciler tarafından PAYLAŞILIR: bir istemcinin
    çektiği kabuğu diğerleri UYAP'a hiç gitmeden alır. Eşzamanlı ilk istekler tek sefer
    UYAP'a gider (request-collapsing); tazeliğini yitiren kabuk arkada tazelenir (SWR)."""
    path = (meta.get("path") or "").lstrip("/")

    # PANEL: yerel Django paneli (uyap_panel/web, ayrı süreç, 127.0.0.1:8000) — UYAP
    # oturumu/önbelleği/enjeksiyonuyla hiç ilgisi yok, en başta ayrılır.
    if uyap_proxy.is_panel_path(path):
        return await uyap_proxy.handle_panel_proxy(
            meta.get("method", "GET"), path, meta.get("query") or "", body, meta.get("headers"))

    # KONTROL DÜZLEMİ: iş-kuyruğu yolları UYAP'a İLETİLMEZ; ofiste yerel işlenir. UYAP
    # oturumu (gw) gerektirmeden de yanıtlanabilir (ör. iş listeleme), bu yüzden en başta.
    if jobs.is_control_path(path):
        return await jobs.handle_control(
            meta.get("method", "GET"), path, meta.get("query", ""), body)

    gw = uyap_proxy.gw
    gw.touch()

    query = meta.get("query") or ""
    method = meta.get("method", "GET")
    proxy_base = meta.get("proxy_base") or ""
    fwd_headers = _build_forward_headers(meta.get("headers"))

    event = None
    cacheable = should_cache(method, path)
    if cacheable:
        # Request collapsing: aynı path için eşzamanlı istekler ilkini bekler.
        while path in ACTIVE_DOWNLOADS:
            await ACTIVE_DOWNLOADS[path].wait()

        cached = get_cached_response(path, proxy_base, query)
        if cached is not None:
            status, headers, cached_body = cached

            # 304 Not Modified kontrolü
            req_headers = meta.get("headers", {})
            if_modified_since = req_headers.get("If-Modified-Since") or req_headers.get("if-modified-since")
            if_none_match = req_headers.get("If-None-Match") or req_headers.get("if-none-match")
            cached_last_modified = headers.get("last-modified") or headers.get("Last-Modified")
            cached_etag = headers.get("etag") or headers.get("ETag")
            use_304 = False
            if if_none_match and cached_etag and if_none_match.strip() == cached_etag.strip():
                use_304 = True
            elif if_modified_since and cached_last_modified and if_modified_since.strip() == cached_last_modified.strip():
                use_304 = True
            if use_304:
                return 304, headers, b""

            # SWR: kabuk tazeliğini yitirdiyse ANINDA ver, arkada güncelle. Paylaşılan
            # önbellek böylece hem hızlı (tüm istemcilere ortak) hem güncel kalır.
            if _is_shell(path):
                age = _cache_age(path, proxy_base, query)
                if age is not None and age > SHELL_TTL:
                    asyncio.ensure_future(
                        _background_refresh(method, path, query, body, fwd_headers, proxy_base))
            return status, headers, cached_body

        event = asyncio.Event()
        ACTIVE_DOWNLOADS[path] = event

    try:
        # Değiştiren işlemler (POST/PUT/PATCH/DELETE — durum DEĞİŞTİRMEYEN "okuma-ipuçlu"
        # sorgular DAHİL, bkz. _runtime modül docstring'i: UYAP eşzamanlı HİÇBİR isteği
        # kabul etmiyor) tek tek, sırayla UYAP'a gider. Bireysel (anlık) istek olduğu için
        # YÜKSEK öncelikli geçitten geçer: arka plandaki toplu iş, bu istek bitene kadar
        # sıradaki adımını bekler. Yalnız gerçek GET'ler (statik/gezinme) kilitlenmez.
        if method.upper() in _WRITE_METHODS:
            async with interactive_write():
                return await _fetch_uyap(method, path, query, body, fwd_headers, proxy_base, store=cacheable)
        return await _fetch_uyap(method, path, query, body, fwd_headers, proxy_base, store=cacheable)
    except httpx.HTTPError as e:
        return 502, {"Content-Type": "text/plain; charset=utf-8"}, f"UYAP'a ulaşılamadı: {e}".encode("utf-8")
    finally:
        if event:
            ACTIVE_DOWNLOADS.pop(path, None)
            event.set()


def _wire_datachannel(channel):
    """Gelen DataChannel'a istek işleyiciyi bağlar."""
    reasm = Reassembler()

    async def _serve(msg):
        try:
            status, headers, body = await handle_uyap_request(msg.meta, msg.body)
        except Exception as e:
            print(f"[!] Istek isleme hatasi: {e}")
            status = 502
            headers = {"Content-Type": "text/plain; charset=utf-8"}
            body = f"Ofis ajani hatasi: {e}".encode("utf-8")

        try:
            await send_message(channel, msg.id, "res", {"status": status, "headers": headers}, body)
        except Exception as e:
            print(f"[!] Istek cevabi tünelden gonderilemedi: {e}")

    @channel.on("message")
    def on_message(message):
        try:
            msg = reasm.feed(message)
        except Exception as e:
            print(f"[!] Çerçeve çözme hatası: {e}")
            return
        if not msg:
            return
        if msg.kind == "ping":
            # İstemcinin boşta canlılık sinyali; pong ile yanıtla (NAT eşlemesini sıcak tutar).
            asyncio.ensure_future(send_message(channel, msg.id, "pong", {}, b""))
        elif msg.kind == "req":
            asyncio.ensure_future(_serve(msg))

    print("[+] DataChannel açıldı; istekler UYAP'a iletilecek.")


class WSRelaySender:
    """Ofis signaling WebSocket'ini bir DataChannel gibi gösterir: p2p_wire ikili
    çerçevelerini base64'leyip {"type":"relay","b64":...} TEXT olarak telefona iletir.
    Tek yazıcı görev sırayı, bufferedAmount geri-basıncı korur (send_message/drain
    değişmeden çalışır). cid, çok-istemcili modda cevabın doğru telefona dönmesini sağlar."""
    def __init__(self, ws, cid=None):
        self.ws = ws
        self.cid = cid
        self.bufferedAmount = 0
        self._q = asyncio.Queue()
        self._closed = False
        self._task = asyncio.ensure_future(self._writer())

    def send(self, frame):
        self.bufferedAmount += len(frame)
        self._q.put_nowait(frame)

    async def _writer(self):
        while True:
            frame = await self._q.get()
            if frame is None:
                break
            try:
                payload = {"type": "relay", "b64": base64.b64encode(frame).decode("ascii")}
                if self.cid is not None:
                    payload["cid"] = self.cid
                await self.ws.send(json.dumps(payload))
            except Exception as e:
                print(f"[!] relay gönderim hatası: {e}")
            finally:
                self.bufferedAmount -= len(frame)

    def close(self):
        if not self._closed:
            self._closed = True
            self._q.put_nowait(None)


class MultiAnswerer:
    """Oda başına AYNI ANDA birden çok istemciyi yönetir: her istemci (cid) için ayrı bir
    RTCPeerConnection tutar. Hepsi AYNI uyap_proxy.gw oturumunu paylaşır (tek e-imza); bir
    istemcinin bağlanması/kopması diğerlerini ETKİLEMEZ."""

    def __init__(self, ice_servers):
        self.ice_servers = ice_servers
        self.pcs = {}  # cid -> RTCPeerConnection

    async def close_one(self, cid):
        pc = self.pcs.pop(cid, None)
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass

    async def close_all(self):
        for cid in list(self.pcs):
            await self.close_one(cid)

    async def on_offer(self, ws, cid, sdp, sdptype):
        """Bir istemciden (cid) teklif geldi: o istemcinin eski bağlantısı varsa yenile,
        diğer istemcilere dokunma; taze bir yanıt üretip cid ile geri yolla."""
        await self.close_one(cid)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=self.ice_servers))
        self.pcs[cid] = pc

        @pc.on("datachannel")
        def on_datachannel(channel):
            _wire_datachannel(channel)

        @pc.on("connectionstatechange")
        async def on_state():
            print(f"[*] P2P[{cid[:6]}] durumu: {pc.connectionState}")
            if pc.connectionState in ("failed", "closed", "disconnected"):
                if self.pcs.get(cid) is pc:
                    self.pcs.pop(cid, None)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdptype))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)  # ICE toplanmasını bekler; SDP "tam" olur
        await ws.send(json.dumps({
            "type": "answer",
            "cid": cid,
            "sdp": pc.localDescription.sdp,
            "sdptype": pc.localDescription.type,
        }))
        print(f"[*] [{cid[:6]}] yanıt (answer) gönderildi; P2P bağlantı kuruluyor…")


def detect_local_ips():
    """Bu makinenin yerel ağdaki IPv4 adreslerini tespit eder (LAN keşfi için)."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


async def run_signaling(args, ice_servers):
    """Signaling'e bağlı kal; kopunca yeniden dene. Gelen teklifleri yanıtla.
    JOIN mesajında yerel IP'leri ve portu ilan eder; signaling bunları eve aktarır,
    ev de aynı ağdaysa doğrudan LAN'a bağlanır."""
    session = MultiAnswerer(ice_servers)
    backoff = 1
    while True:
        try:
            async with websockets.connect(
                args.signaling, max_size=4 * 1024 * 1024,
                ping_interval=20, ping_timeout=60, close_timeout=10,
            ) as ws:
                # LAN-direct kapalıysa (lan_token yok) IP ilan edilmez: istemci ulaşamayacağı
                # adresleri problamasın, doğrudan tünele geçsin. Açıksa bilet de gönderilir;
                # sunucu office_meta ile birlikte eve iletir (home_client Basic-Auth'ta kullanır).
                lan_token = getattr(args, "lan_token", None) or ""
                join_msg = {
                    "role": "office",
                    "room": args.room,
                    "office_code": getattr(args, "office_code", "") or "",
                    "password": getattr(args, "password", None) or "",
                    "local_ips": detect_local_ips() if lan_token else [],
                    "port": getattr(args, "proxy_port", 8800),
                    "lan_token": lan_token,
                }
                await ws.send(json.dumps(join_msg))
                print(f"[+] Signaling'e bağlandı: {args.signaling} (oda hazır, istemci bekleniyor).")
                backoff = 1

                # bulgu #6 (savunma-derinliği bileti): signaling, BU ofisin girişi gerçek
                # STORE.authenticate() ile doğrulandıysa (yani gerçek hesaplar varsa) 'joined'
                # mesajında bir oda anahtarı verir. Anahtar geldiyse her home→ofis yönlendirilen
                # mesajın taşıdığı bilet ZORUNLU doğrulanır — bu, vendor_server.py'nin mesaj
                # yönlendirme kodunda İLERİDE çıkabilecek bir regresyona karşı ikinci bir
                # savunma katmanıdır (hesap deposu boşken açık-mod senaryosunu kapsamaz: o
                # durumda zaten OPEN_MODE devreye girmeden önce koruyacak gerçek veri de yoktur).
                # Anahtar hiç gelmediyse (dev/allowlist/açık-mod) eski davranış korunur, bilet
                # aranmaz.
                room_ticket_key = None

                def _ticket_ok(msg):
                    if room_ticket_key is None:
                        return True
                    cid_ = msg.get("cid")
                    tkt = msg.get("ticket")
                    exp = msg.get("ticket_exp")
                    if not cid_ or not tkt or not exp:
                        return False
                    try:
                        if int(exp) < int(time.time()):
                            return False
                        expected = hmac.new(room_ticket_key, f"{cid_}:{exp}".encode("utf-8"),
                                            hashlib.sha256).hexdigest()
                        return hmac.compare_digest(tkt, expected)
                    except Exception:
                        return False

                # Çok-istemcili WS-relay (telefon) durumu: her istemci (cid) için ayrı
                # gönderici + yeniden birleştirici. P2P (DataChannel) yolu buradan GEÇMEZ.
                relay_senders = {}   # cid -> WSRelaySender
                relay_reasms = {}    # cid -> Reassembler

                def _drop_relay(cid):
                    s = relay_senders.pop(cid, None)
                    if s is not None:
                        s.close()
                    relay_reasms.pop(cid, None)

                async def _serve_relay(sender, rmsg):
                    try:
                        status, headers, body = await handle_uyap_request(rmsg.meta, rmsg.body)
                    except Exception as e:
                        print(f"[!] Relay istek isleme hatasi: {e}")
                        status = 502
                        headers = {"Content-Type": "text/plain; charset=utf-8"}
                        body = f"Ofis ajani hatasi: {e}".encode("utf-8")
                    try:
                        await send_message(sender, rmsg.id, "res",
                                           {"status": status, "headers": headers}, body)
                    except Exception as e:
                        print(f"[!] Relay cevabi gonderilemedi: {e}")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        mtype = msg.get("type")
                        cid = msg.get("cid")
                        if mtype == "joined":
                            tk = msg.get("ticket_key")
                            room_ticket_key = bytes.fromhex(tk) if tk else None
                            if room_ticket_key is not None:
                                print("[*] Bulgu #6 savunma-derinliği: bilet doğrulaması AKTİF "
                                      "(signaling gerçek kimlik doğrulaması bildirdi).")
                        elif mtype in ("offer", "relay_hello", "relay") and not _ticket_ok(msg):
                            print(f"[!] Bilet doğrulaması BAŞARISIZ [{str(cid)[:6]}] — istek "
                                  f"reddedildi (signaling'in oda-kabul kararına güvenilmiyor).")
                        elif mtype == "offer":
                            await session.on_offer(ws, cid, msg["sdp"], msg.get("sdptype", "offer"))
                        elif mtype == "relay_hello":
                            _drop_relay(cid)
                            relay_reasms[cid] = Reassembler()
                            relay_senders[cid] = WSRelaySender(ws, cid)
                            await ws.send(json.dumps({"type": "relay_ready", "cid": cid}))
                            print(f"[+] WS-relay modu [{str(cid)[:6]}]: telefon tüneli kuruldu.")
                        elif mtype == "relay":
                            if cid not in relay_reasms:
                                relay_reasms[cid] = Reassembler()
                            if cid not in relay_senders:
                                relay_senders[cid] = WSRelaySender(ws, cid)
                            rmsg = relay_reasms[cid].feed(base64.b64decode(msg["b64"]))
                            if rmsg and rmsg.kind == "ping":
                                asyncio.ensure_future(
                                    send_message(relay_senders[cid], rmsg.id, "pong", {}, b""))
                            elif rmsg and rmsg.kind == "req":
                                asyncio.ensure_future(_serve_relay(relay_senders[cid], rmsg))
                        elif mtype == "peer_left":
                            if cid is not None:
                                print(f"[*] İstemci ayrıldı [{str(cid)[:6]}]; bağlantısı kapatılıyor.")
                                await session.close_one(cid)
                                _drop_relay(cid)
                            else:
                                # cid yoksa hepsini kapat (beklenmez ama güvenli).
                                await session.close_all()
                                for c in list(relay_senders):
                                    _drop_relay(c)
                        elif mtype == "error":
                            print(f"[!] Signaling reddetti: {msg.get('error')}")
                            return
                        # start/answer ofis için anlamsız, yok sayılır ('joined' artık yukarıda işlenir)
                    except Exception as e:
                        # Tek bir bozuk mesaj signaling bağlantısını düşürmesin.
                        print(f"[!] Signaling mesaj hatası ({e}); bağlantı korunuyor.")
        except asyncio.CancelledError:
            await session.close_all()
            raise
        except Exception as e:
            print(f"[!] Signaling bağlantısı koptu ({e}); {backoff}s sonra yeniden denenecek.")
            # NOT: P2P bağlantıları (DataChannel) signaling'den bağımsız yaşar; signaling
            # kopunca onları YIKMAYIZ, yalnızca yeniden bağlanıp yeni istemcileri karşılarız.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def run_office(args):
    """Tek UYAP oturumunu kurup hem LAN (FastAPI :port) hem dış ağ (WebRTC) üzerinden
    paylaşır. Her iki yol da AYNI uyap_proxy.gw oturumunu kullanır (tek e-imza girişi)."""
    # Oturumu kurulmadıysa VEYA önceki paylaşımdan kalma httpx client kapandıysa (yeniden)
    # kur. GUI'de "Paylaş → Durdur → tekrar Paylaş" yapınca lifespan kapanışı gw.client'i
    # aclose() ediyordu; eski kod gw'yi None saymadığı için ikinci paylaşım KAPALI client'la
    # çöküyordu. Client kapalıysa taze bir gw kurarız (parola args'tan zaten geliyor).
    gw = uyap_proxy.gw
    if gw is None or getattr(gw, "client", None) is None or gw.client.is_closed:
        user = getattr(args, "user", None) or args.room
        uyap_proxy.configure(
            host=args.host, port=args.proxy_port, pin=args.pin, cert_id=args.cert_id,
            interval=args.interval, no_verify=args.no_verify,
            user=user, password=getattr(args, "password", None),
        )
    uyap_proxy.IDLE_INTERVAL = args.interval

    # LAN-direct: proxy GERÇEKTEN yerel ağa açılıyor mu? (UYAP_LAN_SHARE kapalıyken
    # _resolve_bind_host 0.0.0.0'ı 127.0.0.1'e düşürür.) Kapalıyken yerel IP İLAN EDİLMEZ —
    # eskiden yine de ediliyordu ve istemciler ulaşılamayan adresleri boşuna probluyordu.
    # Açıkken oturumluk bir LAN bileti üretilir: üye parolaları satıcı sunucusunda durduğu
    # için proxy onları doğrulayamaz; signaling'e girebilen (kimliği doğrulanmış) üyeye
    # dağıtılan bu bilet, LAN Basic-Auth parolası olarak kabul edilir (uyap_proxy.require_auth).
    bind_host = uyap_proxy._resolve_bind_host(getattr(args, "host", "0.0.0.0"))
    lan_token = None
    if bind_host not in ("127.0.0.1", "::1", "localhost"):
        lan_token = secrets.token_urlsafe(24)
    args.lan_token = lan_token
    uyap_proxy.GW_LAN_TICKET = lan_token

    # ICE'ı satıcı sunucusundan çek (efemeral TURN dahil); ulaşılamazsa yerel ayara düşer.
    if args.signaling and ("127.0.0.1" in args.signaling or "localhost" in args.signaling):
        ice_servers = []
    else:
        ice_servers = await fetch_ice_servers(args.signaling)

    # LAN sunucusu: uyap_proxy FastAPI uygulamasını bu döngüde koştur. Lifespan ilk girişi
    # yapar ve keepalive döngüsünü başlatır; böylece ayrıca giriş/keepalive yazmaya gerek yok.
    server = uyap_proxy.build_server(host=args.host, port=args.proxy_port, in_thread=True)

    tasks = [
        asyncio.create_task(server.serve()),
        asyncio.create_task(run_signaling(args, ice_servers)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        server.should_exit = True
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# Geriye dönük uyumluluk: eski çağrılar amain'i kullanıyordu.
amain = run_office


def main():
    parser = argparse.ArgumentParser(description="UYAP ofis ajanı (LAN + WebRTC paylaşım).")
    parser.add_argument("--signaling", default=os.environ.get("UYAP_SIGNALING"),
                        help="Signaling sunucusu, ör. wss://senin-sunucun:9000/ws")
    parser.add_argument("--room", default=os.environ.get("UYAP_ROOM"),
                        help="Oda/lisans anahtarı (ofis ve istemci aynı olmalı)")
    parser.add_argument("--password", default=os.environ.get("UYAP_PASSWORD"),
                        help="Paylaşım parolası (LAN Basic-Auth + signaling için)")
    parser.add_argument("--pin", default=os.environ.get("UYAP_PIN"),
                        help="E-imza PIN (ya da UYAP_PIN ortam değişkeni)")
    parser.add_argument("--cert-id", default=os.environ.get("UYAP_CERT_ID"),
                        help="Sertifika ID (gözetimsiz yeniden giriş için)")
    parser.add_argument("--no-verify", action="store_true", help="UYAP'a karşı SSL doğrulamasını atla")
    parser.add_argument("--interval", type=int, default=5, help="Boşta oturum probe aralığı (sn)")
    parser.add_argument("--proxy-port", type=int, default=8800, help="Yerel LAN portu (varsayılan 8800)")
    parser.add_argument("--host", default="0.0.0.0", help="LAN dinleme adresi (varsayılan 0.0.0.0)")
    parser.add_argument("--user", default=os.environ.get("GW_USER"),
                        help="LAN Basic-Auth kullanıcı adı (varsayılan: oda anahtarı)")
    args = parser.parse_args()

    if not args.signaling or not args.room:
        print("[!] --signaling ve --room zorunlu (ya da UYAP_SIGNALING / UYAP_ROOM).")
        sys.exit(2)
    if not args.pin:
        print("[!] UYARI: PIN verilmedi (--pin / UYAP_PIN). Giriş başarısız olabilir.")

    try:
        asyncio.run(run_office(args))
    except KeyboardInterrupt:
        print("\n[!] Ofis ajanı kapatıldı.")


if __name__ == "__main__":
    main()
