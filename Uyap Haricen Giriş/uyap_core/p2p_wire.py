#!/usr/bin/env python3
"""
P2P taşıma yardımcıları (ofis ajanı ve ev istemcisi ortak kullanır)
-------------------------------------------------------------------
Tek bir WebRTC DataChannel üzerinden birden çok HTTP istek/cevabını çoğullamak (mux)
için küçük bir çerçeveleme (framing) protokolü. DataChannel güvenilir + sıralı çalışır
(TCP gibi), bu yüzden tek kanaldan eşzamanlı isteklerin parçalarını `id` ile ayırt
etmek yeterli.

Bir mantıksal mesaj (bir HTTP isteği ya da cevabı) şu JSON çerçevelerine bölünür:

    {"id": "...", "kind": "req"|"res", "seq": k, "total": T,
     "meta": {...}        # yalnızca seq == 0
     "data": "<base64 parça>"}

Gövde base64'lenip CHUNK boyutunda parçalara ayrılır; büyük UDF/PDF indirmeleri bu
sayede belleği şişirmeden ve SCTP'yi zorlamadan akar. Alıcı parçaları `id`'ye göre
biriktirir, hepsi gelince birleştirip (kind, meta, body) döndürür.

Burada UYAP'a özgü hiçbir şey yok; sadece bayt taşır.
"""

import os
import json
import zlib
import struct
import asyncio
from collections import namedtuple

# Ham gövde parçası başına bayt. Çerçeveler artık İKİLİ (base64 yok), bu yüzden bu
# doğrudan tel üstündeki parça boyutu. SCTP/DataChannel güvenli mesaj sınırının altında.
CHUNK = 48 * 1024

# Boştayken bağlantıyı canlı tutmak için kalp atışı (heartbeat) aralığı (saniye).
# Hiç UYAP isteği akmadığında bile istemci bu sıklıkta küçük bir "ping" çerçevesi yollar,
# ofis "pong" ile yanıtlar. Böylece router'ın UDP NAT eşlemesi (tipik 30-120 sn timeout)
# düşmeden önce iki yönde de trafik görür ve kanal boşta kopmaz.
HEARTBEAT_INTERVAL = 15

# Ofis ajanı KONTROL DÜZLEMİ önek'i: bu önekle başlayan HTTP yolları UYAP'a İLETİLMEZ;
# ofiste yerel olarak (iş kuyruğu vb.) işlenir. Hem ofis hem istemci bu yolları ÖNBELLEĞE
# ALMAMALI (durum sorguları her zaman taze dönmeli). Tek yerden tanımlanır ki iki taraf
# da aynı öneki bilsin. UYAP'ın gerçek yollarıyla çakışmaması için bilinçli olarak
# "__uyap_agent__" gibi alışılmadık bir ad seçildi.
AGENT_PREFIX = "__uyap_agent__"

# Her çerçeve = [4 bayt başlık uzunluğu (big-endian)] + [JSON başlık (utf-8)] + [ham parça].
_HEADER_LEN = struct.Struct(">I")

Message = namedtuple("Message", ["id", "kind", "meta", "body"])

# Zaten sıkıştırılmış içerik türleri; bunları tekrar zlib'den geçirmek boşa CPU yakar
# (boyut kazancı yok). Bu türlerde sıkıştırma denemesini atlarız.
_PRECOMPRESSED = (
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
    "font/woff", "font/woff2", "application/font-woff", "application/font-woff2",
    "application/zip", "application/pdf", "application/octet-stream",
    "video/", "audio/",
)


def _is_precompressed(meta):
    """meta içindeki Content-Type zaten sıkıştırılmış bir tür mü?"""
    if not meta:
        return False
    headers = meta.get("headers") or {}
    for k, v in headers.items():
        if k.lower() == "content-type":
            ctype = (v or "").lower()
            return any(p in ctype for p in _PRECOMPRESSED)
    return False


def encode_frames(msg_id, kind, meta, body):
    """Bir mantıksal mesajı DataChannel'a gönderilecek İKİLİ çerçevelere böler.
    Boyut 1024 bayttan büyük ve içerik zaten sıkıştırılmış değilse zlib uygular."""
    compressed = False
    if body and len(body) > 1024 and not _is_precompressed(meta):
        compressed_body = zlib.compress(body, level=1)
        if len(compressed_body) < len(body):
            body = compressed_body
            compressed = True

    body = body or b""
    total = max(1, (len(body) + CHUNK - 1) // CHUNK)
    for seq in range(total):
        chunk = body[seq * CHUNK:(seq + 1) * CHUNK]
        header = {"id": msg_id, "kind": kind, "seq": seq, "total": total}
        if seq == 0:
            if meta is None:
                meta = {}
            meta["_comp"] = compressed
            header["meta"] = meta
        header_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
        yield _HEADER_LEN.pack(len(header_bytes)) + header_bytes + chunk


class Reassembler:
    """Gelen çerçeveleri `id`'ye göre biriktirir; bir mesaj tamamlanınca Message döndürür.
    Parçalar `seq`'e göre saklanıp sırayla birleştirildiği için sırasız (unordered)
    DataChannel teslimi de güvenle desteklenir."""

    def __init__(self):
        self._buf = {}  # id -> {"chunks": {seq: bytes}, "total": T, "meta": ..., "kind": ...}

    def feed(self, frame):
        """Bir ikili çerçeveyi işler. Mesaj tamamlandıysa Message, değilse None döndürür."""
        if isinstance(frame, str):
            frame = frame.encode("utf-8")
        hlen = _HEADER_LEN.unpack(frame[:4])[0]
        header = json.loads(frame[4:4 + hlen].decode("utf-8"))
        chunk = frame[4 + hlen:]
        mid = header["id"]
        entry = self._buf.setdefault(
            mid, {"chunks": {}, "total": header["total"], "meta": None, "kind": header["kind"]}
        )
        entry["chunks"][header["seq"]] = chunk
        if header["seq"] == 0:
            entry["meta"] = header.get("meta")
        if len(entry["chunks"]) >= entry["total"]:
            del self._buf[mid]
            body = b"".join(entry["chunks"][i] for i in range(entry["total"]))

            # Sıkıştırılmış paketi aç
            meta = entry["meta"]
            if meta and meta.get("_comp"):
                body = zlib.decompress(body)

            return Message(mid, entry["kind"], entry["meta"], body)
        return None


async def drain(channel, threshold=1_000_000, poll=0.01):
    """Geri-basınç: DataChannel'ın gönderim tamponu şişmişse boşalana kadar bekle.
    Büyük cevaplarda belleği ve ağı korur."""
    while getattr(channel, "bufferedAmount", 0) > threshold:
        await asyncio.sleep(poll)


async def send_message(channel, msg_id, kind, meta, body):
    """Bir mantıksal mesajı geri-basınca saygı göstererek tüm çerçeveleriyle gönderir."""
    for frame in encode_frames(msg_id, kind, meta, body):
        await drain(channel)
        channel.send(frame)
        # sleep(0) ile event loop'a geçiş izni veririz (ping/keepalive zaman aşımlarını
        # önler) ama Windows'un ~15 ms zamanlayıcı çözünürlüğünü beklemeyiz; sleep(0.001)
        # her çerçevede 10-15 ms'e mal oluyordu. Geri-basınç zaten drain() ile sağlanır.
        await asyncio.sleep(0)


def load_ice_servers():
    """ICE (STUN/TURN) sunucularını ortam değişkeni ya da dosyadan yükler.

    UYAP_ICE ortam değişkeni bir JSON listesi olabilir, ör:
        [{"urls": "stun:stun.l.google.com:19302"},
         {"urls": "turn:turn.example.com:3478", "username": "u", "credential": "p"}]
    Yoksa ice_servers.json dosyasına, o da yoksa public Google STUN'a düşer.

    NOT: TURN yalnızca her iki taraf da simetrik NAT/CGNAT ardındaysa (azınlık)
    gerekir ve WebRTC'de TURN bile yalnızca DTLS ile ŞİFRELİ baytları görür; içeriği
    okuyamaz. İstemezsen TURN ekleme, o nadir vakalar bağlanamaz.
    """
    from aiortc import RTCIceServer

    raw = os.environ.get("UYAP_ICE")
    data = None
    if raw:
        data = json.loads(raw)
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ice_servers.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
    if not data:
        data = [{"urls": "stun:stun.l.google.com:19302"}]

    servers = []
    for s in data:
        servers.append(RTCIceServer(
            urls=s["urls"],
            username=s.get("username"),
            credential=s.get("credential"),
        ))
    return servers


def _signaling_to_ice_url(signaling_url):
    """wss://host/ws  →  https://host/ice  (ws→http, /ws son ekini /ice yapar)."""
    if not signaling_url:
        return None
    u = signaling_url.strip()
    if u.startswith("wss://"):
        u = "https://" + u[len("wss://"):]
    elif u.startswith("ws://"):
        u = "http://" + u[len("ws://"):]
    else:
        return None
    if u.endswith("/ws"):
        u = u[:-len("/ws")] + "/ice"
    else:
        u = u.rstrip("/") + "/ice"
    return u


async def fetch_ice_servers(signaling_url, timeout=15.0):
    """Satıcı sunucusunun /ice ucundan ICE listesini (efemeral TURN dahil) çeker ve
    RTCIceServer listesine çevirir. Ulaşılamazsa yerel load_ice_servers()'a düşer.

    Böylece TURN kimlikleri uygulamaya GÖMÜLMEZ; sunucu zaman sınırlı üretip dağıtır.
    Yerel/localhost signaling'de /ice yoksa sessizce STUN'suz yerel ayara döner."""
    from aiortc import RTCIceServer

    ice_url = _signaling_to_ice_url(signaling_url)
    if ice_url and ("127.0.0.1" not in ice_url and "localhost" not in ice_url):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(ice_url)
                if resp.status_code == 200:
                    data = resp.json().get("iceServers", [])
                    servers = [RTCIceServer(urls=s["urls"], username=s.get("username"),
                                            credential=s.get("credential")) for s in data]
                    if servers:
                        return servers
        except Exception as e:
            print(f"[*] ICE sunucudan çekilemedi ({e}); yerel ayara düşülüyor.")
    return load_ice_servers()
