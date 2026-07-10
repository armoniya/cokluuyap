#!/usr/bin/env python3
"""
UYAP P2P Buluşturma (Signaling) Sunucusu
----------------------------------------
Satıcının (sen) internete bakan TEK bileşeni budur ve KASITLI olarak veri taşımaz.
Görevi yalnızca iki tarafı aynı "oda anahtarı" (room key / lisans) üzerinden buluşturup
WebRTC el sıkışmasını (SDP teklif/yanıt) aktarmaktır. UYAP trafiği, müvekkil verisi,
hiçbir kişisel veri buradan GEÇMEZ — o veri ofis ile ev arasında doğrudan (P2P) akar.

Akış:
  * OFIS AJANI ve EV İSTEMCISI aynı oda anahtarıyla bu sunucuya WebSocket açar.
  * İkisi de bağlıyken sunucu EV tarafına {"type":"start"} der.
  * EV bir SDP "offer" üretip yollar -> sunucu OFISE aktarır.
  * OFIS bir SDP "answer" üretip yollar -> sunucu EVE aktarır.
  * Bu noktadan sonra iki taraf DOĞRUDAN birbirine bağlanır; sunucunun işi biter.

aiortc, setLocalDescription sırasında ICE adaylarını topladığı için SDP "tam" gelir;
ayrı aday (trickle ICE) aktarımına gerek yoktur — sunucu sadece offer/answer taşır.

Çalıştırma (ucuz bir VPS yeter, çünkü neredeyse hiç veri taşımaz):
    pip install websockets
    python signaling_server.py --host 0.0.0.0 --port 9000
    # TLS'i önüne Caddy/nginx koyup (wss://) sağlamak en kolayı; ya da
    # --ssl-certfile/--ssl-keyfile ile doğrudan TLS dinle.

İsteğe bağlı lisans/iptal kaldıracı: signaling_config.json içinde
    {"allowed_rooms": ["musteri-1-uzun-rastgele-anahtar", "..."]}
varsa yalnızca listedeki oda anahtarları kabul edilir.

GÜVENLİK (bulgu #4): Hesap deposu BOŞ ve allowlist YOK iken kimliksiz "serbest oda" artık
VARSAYILAN OLARAK REDDEDİLİR. Bu eski/gevşek davranış yalnızca --open bayrağı ya da
UYAP_SIGNALING_OPEN=1 ile (yerel/dev) açılır; üretimde hesap deposu veya allowlist kullanın.
"""

import os
import ssl
import sys
import json
import uuid
import asyncio
import argparse

import websockets

import accounts

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signaling_config.json")

# Hesap deposu (vendor_server ile aynı). Doluysa oda+parola doğrulanır; boşsa eski davranış.
STORE = accounts.AccountStore()

# Oda modeli: TEK ofis + AYNI ANDA N istemci. Her istemciye benzersiz cid atanır;
# offer/answer/relay mesajları cid ile adreslenir (ofis her istemci için ayrı bağlantı tutar).
# room -> {"office": ws|None, "homes": {cid: ws}, "office_meta": ...}
ROOMS = {}
ALLOWED = None  # None => her oda serbest; set => yalnızca bu anahtarlar
# Açık-mod: hesap deposu BOŞ ve allowlist YOK iken kimlik doğrulamasız "serbest oda"a izin
# vermek GÜVENLİ DEĞİLDİR (oda adını tahmin eden herkes office/home olarak katılır). Bu yüzden
# varsayılan KAPALI; yalnızca açıkça istenirse açılır (yerel/dev). Bkz. güvenlik raporu bulgu #4.
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
    if ALLOWED is None:
        print("[*] Oda allowlist'i yok: ortak anahtarı bilen her çift buluşabilir.")
    else:
        print(f"[*] {len(ALLOWED)} oda anahtarı izinli (allowlist aktif).")


async def _safe_send(ws, payload):
    if ws is None:
        return
    try:
        await ws.send(payload if isinstance(payload, str) else json.dumps(payload))
    except Exception:
        pass


async def handler(ws, path=None):
    """websockets sürüm farkları için path opsiyonel (eski sürüm 2 argüman geçer)."""
    role = room = cid = rk = info = None
    try:
        join = json.loads(await ws.recv())
        role = join.get("role")
        # 'room' alanı v2'de KULLANICI ADI taşır (eski tel uyumu için ad değişmedi).
        room = join.get("room")

        # "probe" = SALT kimlik doğrulama (masaüstü/panel girişi); oda durumuna dokunmaz.
        if role not in ("office", "home", "probe") or not room:
            await _safe_send(ws, {"type": "error", "error": "Geçersiz katılım (role/kullanıcı adı)."})
            return

        # rk = iç BULUŞMA anahtarı. Depo doluysa KULLANICI ADI + parola DOĞRULANIR ve buluşma
        # KARARLI office_id ile yapılır; boşsa allowlist/serbest (yerel/dev) — 'room' doğrudan anahtar.
        password = join.get("password") or ""
        if not STORE.is_empty():
            ok, reason, info = STORE.authenticate(room, password)
            if not ok:
                await _safe_send(ws, {"type": "error", "error": reason})
                return
            rk = info["office_id"]
        elif ALLOWED is not None and room not in ALLOWED:
            await _safe_send(ws, {"type": "error", "error": "Tanınmayan kullanıcı adı."})
            return
        elif ALLOWED is None and not OPEN_MODE:
            # Depo boş + allowlist yok + açık-mod kapalı: kimliksiz buluşma reddedilir (güvenli
            # varsayılan). (Allowlist tanımlıysa yukarıda ele alındı; oda izinliyse altta serbest.)
            # Üretimde hesap oluşturun/allowlist verin; yerel/dev için --open.
            await _safe_send(ws, {"type": "error", "error":
                "Sunucu yapılandırılmamış: hesap deposu boş ve oda allowlist'i yok. "
                "Kimliksiz 'serbest oda' yalnızca açık-modda (yerel/dev) çalışır."})
            return
        else:
            rk = room

        if role == "probe":
            # Kimlik doğrulandı; ofis slotuna/istemci listesine kaydolmadan yanıtla ve bitir.
            slot0 = ROOMS.get(rk) or {}
            joined_msg = {"type": "joined", "peer_present": slot0.get("office") is not None}
            if info:
                joined_msg.update({"role": info.get("role"), "office_code": info.get("office_code"),
                                   "office_label": info.get("office_label")})
            await _safe_send(ws, joined_msg)
            return

        slot = ROOMS.setdefault(rk, {"office": None, "homes": {}, "office_meta": None})

        if role == "office":
            slot["office_meta"] = {"local_ips": join.get("local_ips") or [],
                                   "port": join.get("port", 8800),
                                   # LAN-direct bileti (bkz. vendor_server): home, ofis
                                   # proxy'sine Basic-Auth parolası olarak sunar.
                                   "lan_token": join.get("lan_token") or ""}
            # Ofis başına tek bağlantı: eski ofis varsa kapat, yenisiyle değiştir. Bu çoğunlukla
            # MEŞRU yeniden bağlanmadır (ağ koptu), ama açık-modda kimlik doğrulaması olmadığından
            # potansiyel oturum-devralmadır → görünür uyarı bırak (engellemek meşru reconnect'i bozar).
            old = slot.get("office")
            if old is not None:
                if OPEN_MODE and STORE.is_empty():
                    print(f"[!] UYARI: room={room[:8]}… için mevcut OFIS bağlantısı yenisiyle "
                          f"değiştiriliyor (açık-mod, kimlik doğrulaması yok).")
                await old.close()
            slot["office"] = ws
            await _safe_send(ws, {"type": "joined", "peer_present": len(slot["homes"]) > 0})
            # Ofis (yeniden) bağlandı: mevcut tüm istemcilere teklif üretmelerini söyle.
            for hcid, hws in list(slot["homes"].items()):
                start = {"type": "start", "cid": hcid}
                if slot.get("office_meta"):
                    start.update(slot["office_meta"])
                await _safe_send(hws, start)
        else:  # home — her istemciye benzersiz cid
            cid = uuid.uuid4().hex
            slot["homes"][cid] = ws
            joined = {"type": "joined", "cid": cid, "peer_present": slot["office"] is not None}
            if slot.get("office_meta"):
                joined.update(slot["office_meta"])
            await _safe_send(ws, joined)
            if slot["office"] is not None:
                start = {"type": "start", "cid": cid}
                if slot.get("office_meta"):
                    start.update(slot["office_meta"])
                await _safe_send(ws, start)
        print(f"[+] Katıldı: room={room[:8]}… role={role}"
              + (f" cid={cid[:6]}" if cid else f" istemci={len(slot['homes'])}"))

        # Aktarım döngüsü: cid ile adresleyerek doğru tarafa ilet.
        async for msg in ws:
            if role == "home":
                try:
                    m = json.loads(msg)
                except Exception:
                    continue
                m["cid"] = cid  # istemci → ofis: hangi istemci olduğunu imzala
                await _safe_send(slot.get("office"), json.dumps(m))
            else:
                try:
                    target_cid = json.loads(msg).get("cid")
                except Exception:
                    continue
                await _safe_send(slot["homes"].get(target_cid), msg)

    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        print(f"[!] Handler hatası (room={room}): {e}")
    finally:
        if rk in ROOMS:
            slot = ROOMS[rk]
            if role == "office" and slot.get("office") is ws:
                slot["office"] = None
                slot["office_meta"] = None
                for hws in list(slot["homes"].values()):
                    await _safe_send(hws, {"type": "peer_left"})
            elif role == "home" and cid and slot["homes"].get(cid) is ws:
                del slot["homes"][cid]
                await _safe_send(slot.get("office"), {"type": "peer_left", "cid": cid})
            if slot.get("office") is None and not slot["homes"]:
                ROOMS.pop(rk, None)
        print(f"[!] Ayrıldı: room={str(room)[:8]}… role={role}"
              + (f" cid={cid[:6]}" if cid else ""))


async def amain(args):
    global OPEN_MODE
    load_allowed(args.config)
    OPEN_MODE = bool(getattr(args, "open_mode", False)) or _env_open_mode()
    if ALLOWED is None and STORE.is_empty():
        if OPEN_MODE:
            print("[!] AÇIK-MOD: hesap deposu boş + allowlist yok → oda adını bilen herkes "
                  "kimliksiz katılabilir. Yalnızca yerel/dev için; üretimde KAPATIN.")
        else:
            print("[i] Hesap deposu boş + allowlist yok → kimliksiz buluşma REDDEDİLİR "
                  "(güvenli varsayılan). Yerel/dev için --open ya da UYAP_SIGNALING_OPEN=1.")

    ssl_ctx = None
    if args.ssl_certfile and args.ssl_keyfile:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(args.ssl_certfile, args.ssl_keyfile)

    scheme = "wss" if ssl_ctx else "ws"
    try:
        async with websockets.serve(handler, args.host, args.port, ssl=ssl_ctx, max_size=4 * 1024 * 1024):
            print(f"[*] Signaling {scheme}://{args.host}:{args.port} dinliyor (yalnızca SDP taşır, veri YOK).")
            await asyncio.Future()  # sonsuza kadar çalış
    except OSError as e:
        if getattr(e, "winerror", None) == 10048 or getattr(e, "errno", None) in (48, 98, 10048):
            print(f"[!] Port {args.port} zaten kullanımda — büyük ihtimalle BAŞKA bir signaling penceresi açık.")
            print("[!] İki signaling gerekmez: açık olanı kullan, ya da onu kapatıp tekrar başlat.")
        else:
            raise


def main():
    parser = argparse.ArgumentParser(description="UYAP P2P buluşturma (signaling) sunucusu.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--open", dest="open_mode", action="store_true",
                        help="Hesap deposu boş + allowlist yokken kimliksiz 'serbest oda'a izin "
                             "ver (YALNIZCA yerel/dev; üretimde kullanmayın).")
    parser.add_argument("--ssl-certfile", default=None)
    parser.add_argument("--ssl-keyfile", default=None)
    args = parser.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n[!] Signaling kapatıldı.")


if __name__ == "__main__":
    main()
