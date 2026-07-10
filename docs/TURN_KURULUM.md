# TURN Sunucusu Kurulumu (coturn)

Son açık bulgu: CGNAT/simetrik NAT ardındaki kullanıcılar (tipik olarak **mobil veri**,
bazı fiber operatörleri) STUN ile P2P kuramaz. Bugün bu kullanıcılar WS-relay
fallback'iyle bağlanıyor (çalışıyor ama tüm trafik Render üzerinden akıyor);
TURN kurulunca bu vakalar da gerçek WebRTC'ye döner, Render bant genişliği rahatlar.

**Kod tarafında yapılacak İŞ YOK** — her şey hazır ve 2026-07-07'de doğrulandı:

- `vendor_server.py` → `_turn_servers()`: `UYAP_TURN_SECRET` + `UYAP_TURN_URLS`
  env'leri varsa `/ice` yanıtına **efemeral** kimlik ekler (coturn `use-auth-secret`
  REST biçimi: kullanıcı adı `sonkullanma:uyap`, parola `base64(HMAC-SHA1(secret, kullanıcıadı))`).
  Uzun ömürlü sır istemcilere asla gitmez.
- Tarayıcı: `config_js` → `window.UYAP_CONFIG.ice` → `tunnel.js` `RTCPeerConnection({iceServers: ICE})`.
- Masaüstü/exe: `home_client` ve `office_agent` her bağlantıda `p2p_wire.fetch_ice_servers()`
  ile `/ice`'tan çeker (`username`/`credential` alanları `RTCIceServer`'a aynen geçer).

Yani kurulum = **(A) bir VPS'e coturn kur + (B) Render'a iki env değişkeni ekle.**
İstemcilere hiçbir şey dağıtılmaz; env eklenince mevcut exe ve web anında TURN kullanır.

---

## Neden Render'da olmaz?

TURN ağırlıklı olarak **UDP** ister ve sabit genel IP + geniş port aralığı gerekir.
Render web servisleri yalnız HTTP(S) taşır. En ucuz çözüm küçük bir VPS:
Hetzner CX22 (~4 €/ay) veya DigitalOcean (~6 $/ay), Ubuntu 24.04. Trafik yükü
düşüktür (yalnız CGNAT'lı azınlık relay'e düşer, o da DTLS-şifreli bayt — TURN içeriği okuyamaz).

---

## A) VPS tarafı

### 1. DNS (GoDaddy)

`cokluuyap.com` DNS'ine A kaydı ekle:

```
turn.cokluuyap.com  A  <VPS_IP>     TTL 1 saat
```

### 2. coturn kurulumu (Ubuntu)

```bash
sudo apt update && sudo apt install -y coturn
sudo sed -i 's/^#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
```

### 3. Sır üret (iki tarafta AYNI kullanılacak)

```bash
openssl rand -hex 32
# çıktıyı not al: hem turnserver.conf'a hem Render UYAP_TURN_SECRET'a girecek
```

### 4. `/etc/turnserver.conf`

Dosyanın tamamını şununla değiştir (`<SIR>` ve sertifika yollarını doldur):

```ini
listening-port=3478
tls-listening-port=5349
fingerprint

# Efemeral kimlik (vendor_server._turn_servers ile aynı yöntem)
use-auth-secret
static-auth-secret=<SIR>
realm=turn.cokluuyap.com

# Relay port aralığı
min-port=49152
max-port=65535

# TLS (turns: için; 5. adımdaki certbot sertifikası)
cert=/etc/letsencrypt/live/turn.cokluuyap.com/fullchain.pem
pkey=/etc/letsencrypt/live/turn.cokluuyap.com/privkey.pem

# Güvenlik: relay'in iç ağlara/loopback'e paket atmasını engelle
no-loopback-peers
no-multicast-peers
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=100.64.0.0-100.127.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
no-cli

# Kota (kötüye kullanım freni; gerekirse artır)
user-quota=12
total-quota=1200
```

### 5. TLS sertifikası (turns: — kurumsal ağlarda UDP/3478 kapalıysa tek çıkış yolu)

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d turn.cokluuyap.com
# yenileme kancası: coturn'un yeni sertifikayı görmesi için
echo -e '#!/bin/sh\nsystemctl restart coturn' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/coturn
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/coturn
# coturn'un sertifikayı okuyabilmesi gerekir (Ubuntu'da coturn root düşürür):
sudo chmod 755 /etc/letsencrypt/live /etc/letsencrypt/archive
```

### 6. Güvenlik duvarı + başlat

```bash
sudo ufw allow 22/tcp
sudo ufw allow 3478/udp && sudo ufw allow 3478/tcp
sudo ufw allow 5349/tcp
sudo ufw allow 49152:65535/udp
sudo ufw enable
sudo systemctl enable --now coturn
sudo journalctl -u coturn -n 20   # "listener opened" satırlarını gör
```

VPS sağlayıcının kendi güvenlik duvarı/cloud firewall'u varsa aynı portları orada da aç.

---

## B) Render tarafı

uyap-vendor servisinin Environment ayarlarına ekle (env değişikliği otomatik restart eder):

| Değişken | Değer |
|---|---|
| `UYAP_TURN_SECRET` | 3. adımdaki `<SIR>` (turnserver.conf'takiyle AYNI) |
| `UYAP_TURN_URLS` | `turn:turn.cokluuyap.com:3478?transport=udp,turn:turn.cokluuyap.com:3478?transport=tcp,turns:turn.cokluuyap.com:5349?transport=tcp` |
| `UYAP_TURN_TTL` | (opsiyonel) kimlik ömrü sn, varsayılan 86400 |

**URL sırası önemli:** aiortc (masaüstü/exe istemcileri) listedeki yalnızca **İLK**
`turn:` URL'sini kullanır (`rtcicetransport.connection_kwargs`: "only a single TURN
server is supported"); tarayıcı ise hepsini paralel dener. Bu yüzden
`UYAP_TURN_URLS`'te **UDP ilk sırada** kalmalı — TCP/TLS varyantları tarayıcının
kısıtlı ağlardaki (UDP engelli kurum ağı) yedeğidir.

---

## C) Doğrulama

1. **`https://www.cokluuyap.com/ice`** yanıtında artık `username`/`credential` içeren
   `turn:` kaydı görünmeli (STUN'un yanında).
2. Windows'ta, UHG venv'iyle uçtan uca test — `/ice`'tan efemeral kimliği çekip
   coturn'den GERÇEK relay adayı alır (istemcilerin izlediği yolun aynısı):

   ```powershell
   & ".\Uyap Haricen Giriş\.venv\Scripts\python.exe" ".\Uyap Haricen Giriş\turn_dogrula.py"
   # beklenen: "TURN-OK: relay adayı alındı (…)"
   ```

   Betiğin iki yolu da 2026-07-07'de sınandı: TURN'süz prod'a karşı doğru teşhis
   ("kayıt yok → env eksik"), yerel mini-TURN taklidine karşı gerçek allocate
   el sıkışmasıyla "TURN-OK".
3. Canlı sağlama: telefonu **mobil veriye** al (Wi-Fi kapalı), cokluuyap.com'dan
   bağlan — tunnel.js sekme başlığında aktif aday tipini gösterir; CGNAT'taysa
   `relay` görünmesi TURN'ün devrede olduğunu kanıtlar.
4. VPS'te canlı izleme: `sudo journalctl -u coturn -f` — bağlantı sırasında
   "ALLOCATE" satırları düşer.

Sorun çıkarsa: saat kayması efemeral kimliği bozar (`timedatectl` ile VPS'te NTP
açık olsun); 401 hatası = iki taraftaki sır farklı; hiç aday yoksa firewall'da
UDP 3478 kapalıdır.
