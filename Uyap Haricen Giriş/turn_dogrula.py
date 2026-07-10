# -*- coding: utf-8 -*-
"""TURN kurulumu uçtan uca doğrulama (bkz. docs/TURN_KURULUM.md).

Satıcı sunucusunun /ice ucundan efemeral TURN kimliğini çeker ve YALNIZ TURN
sunucularıyla (STUN'suz) aday toplar: SDP'de "typ relay" adayı görünüyorsa
coturn ayakta, sır doğru ve istemcilerin izlediği yol çalışıyor demektir.

Kullanım (UHG venv'iyle):
    python turn_dogrula.py                       # varsayılan: https://www.cokluuyap.com
    python turn_dogrula.py --server https://baska-adres
"""
import argparse
import asyncio
import sys


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://www.cokluuyap.com",
                    help="satıcı sunucusu kökü (varsayılan: https://www.cokluuyap.com)")
    args = ap.parse_args()

    import httpx
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

    ice_url = args.server.rstrip("/") + "/ice"
    print(f"[*] {ice_url} sorgulanıyor…")
    async with httpx.AsyncClient(timeout=15) as client:
        data = (await client.get(ice_url)).json().get("iceServers", [])

    turn = [s for s in data if s.get("username") and s.get("credential")]
    if not turn:
        print("TURN-HATA: /ice yanıtında kimlikli TURN kaydı yok.")
        print("           Render'da UYAP_TURN_SECRET + UYAP_TURN_URLS tanımlı mı?")
        print(f"           Gelen liste: {data}")
        sys.exit(1)
    print(f"[*] TURN kaydı bulundu: urls={turn[0]['urls']} kullanıcı={turn[0]['username']}")

    # STUN'u BİLEREK dışarıda bırakıyoruz: srflx çıkamayacağı için görülen her
    # relay adayı gerçek bir coturn ALLOCATE başarısıdır.
    servers = [RTCIceServer(urls=s["urls"], username=s["username"],
                            credential=s["credential"]) for s in turn]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
    try:
        pc.createDataChannel("dogrulama")
        offer = await pc.createOffer()
        await asyncio.wait_for(pc.setLocalDescription(offer), timeout=30)  # ICE burada toplanır
        relays = [ln.strip() for ln in pc.localDescription.sdp.splitlines()
                  if " typ relay " in ln + " "]
    finally:
        await pc.close()

    if relays:
        print(f"TURN-OK: relay adayı alındı ({relays[0]})")
    else:
        print("TURN-HATA: kimlik alındı ama relay adayı toplanamadı.")
        print("           VPS firewall'unda UDP/TCP 3478 açık mı? coturn ayakta mı")
        print("           (journalctl -u coturn)? Saatler NTP ile senkron mu?")
        sys.exit(2)


asyncio.run(main())
