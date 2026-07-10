# UYAP Uzaktan Erişim — P2P (WebRTC) Mimarisi

Amaç: **evdeki** bilgisayardan, **ofisteki** e-imzayla UYAP'a girmiş programa komut göndermek.
Üç kesin kısıt korunur:

1. Son kullanıcıya Tailscale/VPN **kurdurulmaz**.
2. Modemde **port yönlendirme yok**.
3. **Müşteri verisi satıcının sunucusundan geçmez.**

Veri yolu:

```
[Ev tarayıcı] → 127.0.0.1:8800 (home_client) ──DataChannel (DTLS, doğrudan P2P)──→ (office_agent) → UYAP
                          │                                                              │
                          └────────── sadece SDP el sıkışması (signaling) ──────────────┘
                                       (satıcının minik sunucusu — VERİ YOK)
```

## Bileşenler

| Dosya | Nerede çalışır | Görevi |
|------|----------------|--------|
| `signaling_server.py` | Satıcının (senin) ucuz VPS'in | Oda anahtarıyla ofis↔ev eşler, SDP teklif/yanıt aktarır. **UYAP verisi geçmez.** |
| `office_agent.py` | Ofis (e-imza kartı burada) | `uyap_proxy.GatewaySession` ile UYAP'a girer; P2P kanaldan gelen istekleri UYAP'a uygular. |
| `home_client.py` | Ev | `127.0.0.1:8800`'de yerel HTTP sunar; tarayıcı isteklerini P2P kanaldan ofise tüneller. |
| `p2p_wire.py` | (ortak) | DataChannel çerçeveleme (büyük dosya parçalama) + ICE yükleyici. |

## Kurulum

```bash
# Signaling (VPS):
pip install websockets

# Ofis:
pip install aiortc websockets httpx fastapi uvicorn

# Ev:
pip install aiortc aiohttp websockets
```

## Çalıştırma

Önce her müşteri için uzun, rastgele bir **oda anahtarı** üret (lisans gibi davranır):

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

1) Satıcı — signaling (bir kez, TLS'i Caddy/nginx ile `wss://` yap):
```bash
python signaling_server.py --host 0.0.0.0 --port 9000
```

2) Ofis:
```bash
set UYAP_PIN=......
python office_agent.py --signaling wss://senin-sunucun:9000 --room <ODA_ANAHTARI> --cert-id <id>
```

3) Ev:
```bash
python home_client.py --signaling wss://senin-sunucun:9000 --room <ODA_ANAHTARI>
# sonra tarayıcı: http://127.0.0.1:8800/giris
```

## Güvenlik / KVKK notları

- UYAP oturum çerezleri **eve hiç gelmez**; ofis tarafında server-side kalır (uyap_proxy davranışı korunur).
- Yerel sunucu **127.0.0.1**'e bağlı: yalnızca ev makinesindeki tarayıcı erişir.
- Signaling yalnızca bağlantı kurulum meta'sını (SDP/ICE adresleri) görür; dava/müvekkil verisi görmez.
- **TURN yedeği (opsiyonel):** iki taraf da simetrik NAT/CGNAT ardındaysa doğrudan delik açılamaz.
  O nadir durumda `ice_servers.json` ya da `UYAP_ICE` ile TURN tanımlanır; WebRTC'de TURN bile yalnızca
  **DTLS ile şifreli** baytları taşır, içeriği okuyamaz. İstemezsen TURN ekleme.
- Oda anahtarını iptal etmek için signaling'de `signaling_config.json` → `allowed_rooms` allowlist'i kullan.

## ICE / TURN örneği (`ice_servers.json`, opsiyonel)

```json
[
  {"urls": "stun:stun.l.google.com:19302"},
  {"urls": "turn:turn.senin-sunucun:3478", "username": "kullanici", "credential": "parola"}
]
```

> Üretimde STUN'u da kendin barındırmak istersen `coturn` hem STUN hem TURN verir.

## Açık uçlar (ürünleştirme)

- ~~E-imza PIN'i kodda sabit kalmamalı~~ ✅ Tamam: PIN artık yalnızca `UYAP_PIN` ortam
  değişkeni / `--pin` argümanı / GUI ayarından gelir; kaynakta hiçbir yerde yazılı değil.
- Ofis ajanı + ev istemcisi için tek tıklık paketleme (tray app / .exe).
- Oda anahtarı dağıtımı + lisans yönetimi.
