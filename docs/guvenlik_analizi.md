# Güvenlik Analizi — UYAP Django / Kararlı Projesi

> Tarih: 2026-06-29
> Kapsam: Proje kodu (`.venv`, `site-packages`, tarayıcı `chrome_profile` cache'leri hariç).
> Not: Bu rapor statik kod incelemesine dayanır; sızma testi (pentest) yapılmamıştır.
> Her bulgu için **konum**, **risk** ve **öneri** verilmiştir.

Bu sistem **tüm yargı sistemine ve müvekkil dosya verisine erişim sağlayan bir e‑imza
oturumunu** taşır/paylaşır. Bu nedenle aşağıdaki bulgular sıradan bir web uygulamasına
göre daha yüksek etkiye sahiptir: ele geçen bir oturum = UYAP'ta avukat adına tam yetki.

---

## Özet Tablo

| # | Bulgu | Önem | Konum |
|---|-------|------|-------|
| 1 | Diske kaydedilmiş canlı UYAP oturum çerezleri (`uyap_session_cookies.json`) | **Kritik** | birden çok dizin |
| 2 | Ofis proxy'sinde localhost = tam yetki (parolasız) + varsayılan `0.0.0.0` bind | **Kritik** *(giderildi: Host/Origin guard + yerel‑yetki jetonu + bind varsayılanı `127.0.0.1`/opt‑in `UYAP_LAN_SHARE`; kalan: aynı‑kullanıcı süreç — OS sınırı)* | `uyap_proxy.py` |
| 3 | Yerel panel sunucusu `0.0.0.0`'a bağlanıyor, CSRF yok, parola bellekte düz | **Yüksek** *(giderildi: bind varsayılanı `127.0.0.1`/opt-in `UYAP_PANEL_LAN` + same-origin CSRF guard + koşullu `Secure` çerez; kalan: parola bellekte (paylaşım için zorunlu) → hız sınırı #7, kalıcı şifreleme #9)* | `Panel/web/server.py` |
| 4 | Signaling "boş depo" modunda her oda serbest — kimlik doğrulaması yok | **Yüksek** *(giderildi: kimliksiz "serbest oda" varsayılan REDDEDİLİR; yalnızca opt-in `--open`/`UYAP_SIGNALING_OPEN=1` (yerel/dev) + ofis-devralma uyarısı; allowlist modu korundu)* | `signaling_server.py`, `vendor_server.py` |
| 5 | Proxy'nin enjekte ettiği JS, login kontrolünü kapatıp veriyi IndexedDB'ye cache'liyor | **Yüksek** *(giderildi: IndexedDB cache artık tarayıcı oturumuna bağlı (sessionStorage SID mührü) → yeni sekme/tarayıcı/sonraki kullanıcıda kalıcı cache otomatik SİLİNİR; logout'ta proaktif temizleme; aynı-oturum hızı korundu; kalan bilinçli: deny-list (allow-list değil), `loginState` her yüklemede yeniden set — gerçek yetki sunucu tarafında)* | `uyap_proxy.py` |
| 6 | Django `DEBUG=True` + sabit/zayıf `SECRET_KEY` | **Orta‑Yüksek** | `settings.py` (×2) |
| 7 | Brute‑force koruması yok; kullanıcı adı sızdırma (enumeration) | **Orta** *(giderildi: IP+kategori bazlı oran sınırlayıcı `_LoginGuard` (üstel backoff + kilit) tüm kimlik uçlarında /ws, /api/office, /ofis/login, /admin, parola‑sıfırlama; `authenticate` tek‑tip mesaj + sabit‑zaman kukla hash; sıfırlama yanıtı tek‑tip)* | `vendor_server.py`, `accounts.py` |
| 8 | Parola sıfırlama jetonu loglara/URL'e düşüyor; min 4 karakter parola | **Orta** *(giderildi: üretimde sıfırlama akışı SMTP'ye bağlı/test-modu opt-in → jeton üretilmez/loglanmaz; `Referrer-Policy: no-referrer`; min parola `UYAP_MIN_PASSWORD_LEN` varsayılan 10, insan-seçimli tüm yollarda)* | `vendor_server.py`, `accounts.py` |
| 9 | DPAPI dışı (Windows harici) ortamda parola/PIN düz metin saklanıyor | **Orta** *(giderildi: fail‑closed — düz metne asla düşmez; Windows dışı `UYAP_CONFIG_SECRET` ile PBKDF2+Fernet; `save_config` atomik + 0600; her iki kopya)* | `core/config.py`, `uyap_app.py` |
| 10 | `--no-verify` ile UYAP TLS doğrulaması kapatılabiliyor | **Düşük‑Orta** *(giderildi: `--no-verify` yalnızca `UYAP_ALLOW_INSECURE_TLS=1` açıkken geçerli, aksi halde yok sayılıp doğrulama açık kalır + belirgin MITM uyarısı; `resolve_verify_ssl`)* | `uyap_proxy.py` vd. |
| 11 | Logger üretilen `*_core.py`'ı import edip çalıştırıyor (yerel RCE yüzeyi) | **Düşük** *(giderildi: import öncesi katı ad deseni + `realpath`/`commonpath` yol sınırı → paket‑kaçışı/yol‑gezinmesi reddedilir)* | `uretilmis_runner.py`, `logger_core.py` |
| 12 | XML ayrıştırma (ElementTree) — entity expansion DoS | **Düşük** *(giderildi: ortak `_guvenli_xml_parse` — defusedxml öncelikli + 20 MB sınırı + DOCTYPE/DTD reddi; iki ayrıştırma noktası)* | `mts/parse.py`, `mts_donusum.py` |
| 13 | Repo içinde `chrome_profile/` cache'leri (hassas veri at‑rest) | **Düşük‑Orta** *(kısmen giderildi: kapsamlı `.gitignore` + `temizle_artefaktlar.py` temizleme betiği; `%LOCALAPPDATA%`'ya taşıma ertelendi)* | birçok dizin |

> **Ek bulgu — Kaynağa gömülü e‑imza PIN'i (giderildi: 2026-06-29).** `uyap_proxy.py`'de
> `configure(... pin="092291" ...)` ve `--pin` argümanının `os.environ.get("UYAP_PIN", "092291")`
> varsayılanı, gerçek e‑imza PIN'ini **kaynak koda gömüyordu** (kuralın ihlali: PIN yalnızca
> `UYAP_PIN`'den gelmeli, kaynağa yazılmamalı). Düzeltme: `configure` varsayılanı `pin=None`,
> `--pin` varsayılanı `os.environ.get("UYAP_PIN")` (literal kaldırıldı), `main()` PIN yokken
> net mesajla `sys.exit(2)` (kardeş `uyap_giris_dışarıdan.py` deseni). GUI/web çağrıları PIN'i
> zaten kullanıcıdan açıkça geçiriyor → etkilenmez. **Kalan:** `Dosya Açılış/MTS Takip Açılış/
> mts_takip_acan.py.bak_uyapbot:227` adlı ESKİ YEDEKTE `pyautogui.write('092291', …)` hâlâ var
> (aktif `mts_takip_acan.py`'de yok); yedek dosyası olduğu için dokunulmadı — silinmesi/temizlenmesi
> kullanıcı onayına bırakıldı.

---

## 1. Diske kaydedilmiş canlı UYAP oturum çerezleri — **Kritik**

**Konum:** `Uyap Haricen Giriş/uyap_session_cookies.json`,
`Uyap Haricen Giriş/uyap_panel/web/uyap_session_cookies.json`,
`Panel/uyap_session_cookies.json` (`save_session_cookies` ile yazılır).

Ofis proxy'si (`uyap_proxy.py` → `save_session_cookies`) UYAP oturum çerezlerini düz JSON
olarak diske yazıyor. Bu çerezler **avukat.uyap.gov.tr için bearer kimlik bilgisidir**:
dosyayı eline geçiren herkes (başka bir uygulama, yedek, senkron, paylaşım) e‑imza
olmadan UYAP oturumunu devralabilir.

**Öneri:**
- Çerezleri diskte tutmaktan kaçının; zorunluysa DPAPI / OS keystore ile şifreleyin ve
  kısa ömürlü tutun, oturum kapanınca silin.
- Bu dosyaları `.gitignore`'a alın ve sürüm geçmişinden temizleyin (eğer git'e girmişse).
- Dosya izinlerini yalnızca kullanıcıya kısıtlayın.

---

## 2. Ofis proxy'sinde localhost tam yetki + varsayılan `0.0.0.0` bind — **Kritik**

**Konum:** `Uyap Haricen Giriş/uyap_core/uyap_proxy.py:1141` (`require_auth`), `main()` `--host` varsayılanı `0.0.0.0`.

```python
client_host = request.client.host if request.client else ""
if client_host in _LOCAL_HOSTS:      # 127.0.0.1 / ::1 / localhost → PAROLASIZ TAM YETKİ
    return GW_USER
```

127.0.0.1'den gelen **her istek** parolasız olarak tam yetkili kabul ediliyor ve doğrudan
UYAP'a iletiliyor. Sonuçlar:
- Aynı makinedeki **herhangi bir kullanıcı/işlem** (kötü amaçlı yazılım, başka uygulama)
  `http://127.0.0.1:8800/...` üzerinden UYAP'ta avukat adına işlem yapabilir.
- Proxy varsayılan olarak `0.0.0.0`'a bağlanıyor. Bir ters‑proxy/yük dengeleyici arkasına
  konursa `request.client.host` çoğunlukla `127.0.0.1` görünür → uzak istemciler de
  **parola kontrolünü atlar**. DNS rebinding ile tarayıcı tabanlı saldırılar da mümkün.
- Tarayıcıdaki herhangi bir web sitesi, `127.0.0.1:8800`'e CSRF/SSRF tarzı istek atarak
  (CORS basit isteklerde) durum değiştiren UYAP çağrıları tetikleyebilir.

**Öneri:**
- Varsayılan bind'i `127.0.0.1` yapın; `0.0.0.0` yalnızca açıkça ve uyarıyla seçilsin.
- Localhost muafiyetini kaldırın ya da en azından paylaşılan bir gizli jeton (header) şartı
  koyun; "aynı makine = güvenli" varsayımı çok‑kullanıcılı/kötücül‑yazılım senaryolarında
  geçersizdir.
- `Origin`/`Host` başlığı doğrulaması ekleyin (DNS rebinding'e karşı), durum değiştiren
  uçlarda CSRF token veya özel başlık (`X-Requested-With` benzeri preflight zorlayan) isteyin.
- Ters‑proxy arkasında `X-Forwarded-For`'a güvenmeyin; gerçek istemci IP'sini doğru çözün.

> **Durum (2026-06-29 güncellemesi — kısmen giderildi):** `require_auth` artık localhost
> muafiyetinden **önce** Host/Origin doğrulaması yapıyor (`uyap_proxy.py`,
> `_hostname_of`/`_is_ip_or_local`). Host ya da Origin başlığı gerçek bir DNS adıysa istek
> **403** ile reddedilir; meşru istemciler her zaman IP‑literali/`localhost` kullandığından
> (ve LAN `home_client` tarayıcının loopback Origin'ini ilettiğinden, `Origin==Host` kuralı
> yerine "DNS‑adı reddi" seçildi) bu, **DNS rebinding ve tarayıcı tabanlı CSRF** vektörlerini
> kapatır. İleri kullanıcı için `UYAP_ALLOWED_HOSTS` env beyaz listesi var.
> **Kalan açık (önceki):** aynı makinede çalışan tarayıcı‑dışı bir süreç (Origin göndermez)
> hâlâ parolasız localhost yolunu kullanabiliyordu; (b) varsayılan bind `0.0.0.0`.

> **Durum (2026-06-29 — gizli‑jeton katmanı eklendi):** Localhost muafiyeti artık
> **koşulsuz değil**. `_init_local_token()` (configure() içinde, hem CLI hem GUI paylaşım
> yolunda) açılışta rastgele bir **yerel‑yetki jetonu** üretir; ortam değişkenine
> (`UYAP_LOCAL_TOKEN`, aynı süreçteki istemciler için) **ve** kullanıcıya özel bir dosyaya
> (`%LOCALAPPDATA%\UyapIcra\gw_local_token`, Win; `~/.config/uyapicra/…`, POSIX — POSIX'te
> `0600`) yazar. `require_auth` localhost dalında:
> 1. geçerli `X-Uyap-Local-Token` başlığı → kabul (yerel Python istemcileri),
> 2. jeton yoksa yalnızca gerçek tarayıcı isteği (`Origin`/`Sec-Fetch-*`/`Referer` yerel ya da
>    `text/html` üst‑düzey gezinme — `_looks_like_local_browser`) → kabul (yerel UI),
> 3. aksi halde **403** (jetonsuz, tarayıcı‑imzasız yerel API çağrısı reddedilir).
>
> İstemci tarafı tek tek değiştirilmedi: ofis, açılışta süreç‑global bir urllib opener
> (`_LocalTokenInjector`) kurar; aynı süreçteki tüm urllib istemcileri (SorguMotoru,
> `is_kuyrugu`, `Mts_evirme`, `logger_core`, `htiyati_Haciz`, …) loopback ofise giderken
> jetonu otomatik taşır. Böylece: jetonu **okuyamayan** bir süreç (farklı kullanıcı / düşük
> bütünlük) artık localhost yolundan tam yetki **alamaz**, ve "soket açabilen her süreç =
> tam yetki" özelliği kalkar.
>
> **Kalan artık risk:** aynı kullanıcı + aynı bütünlük seviyesindeki kötücül bir süreç jeton
> dosyasını/ortam değişkenini okuyabilir ya da tarayıcı başlıklarını (Origin/Sec-Fetch)
> taklit edebilir — bu, loopback HTTP'nin doğasında olan ve ancak OS‑düzeyi yalıtımla çözülen
> bir sınırdır (o seviyedeki saldırgan PIN'i de okuyabilir, GUI'ye enjekte edebilir).
>
> **Durum (2026-06-29 — bind güvenli varsayılana çekildi):** Proxy artık **varsayılan olarak
> yalnızca `127.0.0.1`** dinler. `build_server()` içine eklenen `_resolve_bind_host()` tüm‑arayüz
> adreslerini (`0.0.0.0`/boş/`::`) loopback'e indirger; LAN‑direct paylaşımı (`0.0.0.0`) yalnızca
> **`UYAP_LAN_SHARE=1`** açıkça ayarlandığında açılır. Çağıran belirli bir adres (ör. seçili LAN
> IP'si) verdiyse o bilinçli tercih korunur. Tek nokta `build_server()` olduğundan tüm çağıranlar
> (CLI `main()`, `office_agent.run_office`, GUI paylaşımı) merkezî olarak kapsanır; her çağrı yeri
> ayrıca düzenlenmedi. **Önemli:** dış‑ağ (WebRTC/DataChannel) paylaşımı bu soketi HİÇ kullanmaz —
> istekler ofiste süreç‑içi (`handle_uyap_request`) işlenir — bu yüzden loopback bind uzaktan
> paylaşımı BOZMAZ; yalnızca aynı‑LAN'daki istemcinin doğrudan‑LAN kısayolu kapanır ve relay'e
> düşer. LAN‑direct açıkken de erişim `GW_PASS` Basic‑Auth gerektirir (parola yoksa `require_auth`
> 503 döner), yani parolasız LAN açımı mümkün değildir. Açılışta hangi arayüzde dinlendiği log'a
> yazılır.

---

## 3. Yerel panel sunucusu `0.0.0.0`'a bağlı, CSRF yok, parola bellekte düz — **Yüksek**

**Konum:** `Panel/web/server.py:669` `ThreadingHTTPServer(("0.0.0.0", port), ...)`, `do_POST`.

- Saf stdlib sunucusu `0.0.0.0`'a bağlanıyor ve docstring açıkça "internete tünelle aç"
  diyor. Durum değiştiren tüm uçlar (`/api/conn/*`, `/api/login`) **CSRF korumasız**:
  kimlik çerez tabanlı (`SameSite=Lax`, fakat token yok), POST gövdesi JSON.
  `SameSite=Lax` GET tabanlı CSRF'i azaltır ama tam koruma değildir; ayrıca `Secure`
  bayrağı yok → tünel HTTPS değilse çerez düz akar.
- Oturum sözlüğü `SESSIONS[token] = {"user": user, "pw": pw}` — **UYAP parolası sunucu
  belleğinde düz metin** tutuluyor (paylaş/al için). Bellek dökümü/çökme dökümü ile sızabilir.
- Kimlik doğrulama yalnızca UYAP'a giriş denemesidir; brute‑force/rate‑limit yok.

**Öneri:**
- Varsayılan bind `127.0.0.1`; dışarı açım yalnızca kimlik doğrulamalı bir ters‑proxy +
  HTTPS ile. Çereze `Secure` ekleyin (HTTPS arkasında).
- CSRF token (double‑submit cookie veya senkronizasyon token) ekleyin.
- Parolayı bellekte tutma süresini en aza indirin; mümkünse oturum çerezi yerine
  kısa ömürlü, kapsamı dar bir jeton üretin.
- Giriş denemelerine hız sınırı / gecikme ekleyin.

> **Durum (2026-06-29 — giderildi):** `Panel/web/server.py`
> - **Bind:** Panel artık **varsayılan `127.0.0.1`** dinler (`_panel_bind_host()`); tüm-ağa açım
>   yalnızca açık onayla **`UYAP_PANEL_LAN=1`**. Açılışta hangi arayüzde dinlendiği log'a yazılır
>   (LAN açıksa "ters-proxy + HTTPS arkasından açın, port-forward etmeyin" uyarısı). NOT: paylaşım
>   ofisinin `0.0.0.0` parametresi (`ConnManager.start_share` → `office_agent` → `build_server`)
>   zaten #2'deki `_resolve_bind_host` ile loopback'e indirgeniyor.
> - **CSRF:** Durum değiştiren **tüm** POST uçları (`/api/login`, `/api/logout`, `/api/conn/*`,
>   `/api/udf/*`, `/api/sgk/*`) `do_POST` başında `_csrf_ok()` ile same-origin doğrulamasından
>   geçer: `Sec-Fetch-Site: cross-site` ya da `Host`'tan farklı `Origin` → **403**. Panelin kendi
>   `fetch` çağrıları aynı-origin olduğundan ön yüz DEĞİŞMEDİ; tarayıcı-dışı yerel istemci (Origin
>   yok) CSRF vektörü olmadığından serbest.
> - **Çerez:** `Secure` bayrağı HTTPS arkasında (`UYAP_PANEL_HTTPS=1`) eklenir (düz-HTTP
>   loopback'i bozmamak için koşullu); `HttpOnly; SameSite=Lax` zaten vardı.
> - **Kalan (bilinçli):** UYAP parolası paylaş/al için bellekte tutulmaya devam ediyor (UYAP
>   girişi gerektiriyor; `/api/logout`'ta siliniyor, istemciye gönderilmiyor). Bellekten tamamen
>   kaldırmak paylaşım anında parola sormayı (orijinal akış değişikliği) gerektirir. Giriş hız
>   sınırı bulgu **#7**'de ele alınacak; kalıcı parola şifrelemesi (non-Windows) bulgu **#9**.

---

## 4. Signaling "boş depo" modunda her oda serbest — **Yüksek**

**Konum:** `signaling_server.py:106-116`, `vendor_server.py:250-261`.

Hesap deposu (`STORE`) boşken ve `allowed_rooms` tanımlı değilken **kimlik doğrulaması
yapılmadan** gelen `room` değeri doğrudan buluşma anahtarı olarak kullanılıyor:

```python
if not STORE.is_empty():
    ... authenticate ...
elif ALLOWED is not None and room not in ALLOWED:
    ...
else:
    rk = room        # serbest: oda adını bilen herkes "office" ya da "home" olarak girer
```

Sunucu hesapsız/allowlist'siz deploy edilirse, bir oda adını tahmin/ele geçiren saldırgan
o odaya `office` veya `home` rolüyle katılıp WebRTC el sıkışmasına (SDP) müdahale edebilir
ya da kurbanın eşi gibi davranıp tüneli ele geçirmeye çalışabilir.

**Öneri:**
- Üretimde **her zaman** hesap deposu (kullanıcı+parola) veya allowlist zorunlu olsun;
  "boş = serbest" davranışını yalnızca açık bir `--dev` bayrağına bağlayın, üretimde reddedin.
- Aynı odada ikinci bir `office` bağlantısı geldiğinde eskisini sessizce kapatmak yerine
  doğrulama/uyarı ekleyin (oturum devralma direnci).

> **Durum (2026-06-29 — giderildi).** Hem `signaling_server.py` hem `vendor_server.py`
> (+ birebir kopya `vendor_deploy/vendor_server.py`) güvenli-varsayılana çekildi:
>
> - **Açık-mod opt-in.** Yeni `OPEN_MODE` (global) + `_env_open_mode()`. Karar dalı artık
>   `elif ALLOWED is None and not OPEN_MODE:` ile kimliksiz "serbest oda"yı **VARSAYILAN
>   REDDEDER** (`{"type":"error"}` döner). Eski gevşek davranış yalnızca `--open` bayrağı
>   ya da `UYAP_SIGNALING_OPEN=1/true/yes/on` ile (yerel/dev) açılır. Açılışta hesap deposu
>   boş + allowlist yokken durum açıkça log'lanır (açık → uyarı, kapalı → bilgi).
> - **Allowlist modu korundu.** `ALLOWED` tanımlı ve oda listedeyse açık-mod gerekmeden
>   serbest kalır (allowlist'in kendisi yetkilendirmedir); yalnızca *allowlist'siz* serbest
>   yol kapatıldı. Karar tablosu 7 senaryoyla doğrulandı.
> - **Ofis-devralma uyarısı.** Aynı odada mevcut `office` bağlantısı yenisiyle değiştirilirken
>   (çoğunlukla meşru reconnect) açık-modda görünür `[!] UYARI` log'u bırakılır. Engellemek
>   meşru yeniden bağlanmayı bozacağından bilinçli olarak engellenmedi; kimlik doğrulamalı
>   modda devralma zaten geçerli kimlik bilgisi gerektirir.

---

## 5. Enjekte edilen JS login kontrolünü kapatıyor + veriyi IndexedDB'ye cache'liyor — **Yüksek**

**Konum:** `uyap_proxy.py:430-680` (`_rewrite_body` içine gömülü script).

Proxy, UYAP HTML yanıtlarına bir script enjekte ediyor:
- `localStorage.setItem('loginState', 'true')` — istemci tarafı oturum kontrolünü **kalıcı
  olarak bypass** ediyor.
- `XMLHttpRequest` ve `fetch` sarmalanıp UYAP API yanıtları **IndexedDB'ye (`UYAP_SWR_CACHE`)
  şifresiz** yazılıyor. Bu yanıtlar müvekkil/dosya verisi içerir ve tarayıcıda kalıcıdır;
  ortak/halka açık makinelerde sonraki kullanıcılar erişebilir.
- Cache'lenebilirlik kara listesi (kaydet/sil/imza…) **kelime tabanlı**; kapsanmayan bir
  durum‑değiştiren uç yanlışlıkla cache'lenirse bayat/yanlış veri gösterebilir (hukuki
  bağlamda risklidir).

**Öneri:**
- Hassas yanıtların kalıcı istemci cache'ine yazılmasını engelleyin; gerekiyorsa yalnızca
  oturum süresi boyunca bellek içi (sessionStorage değil, in‑memory) ve oturum bitince temizleyin.
- Kara liste (deny) yerine **beyaz liste (allow)** yaklaşımı kullanın: yalnızca açıkça güvenli,
  salt‑okunur uçlar cache'lensin.
- `loginState` bypass'ının yan etkilerini gözden geçirin; gerçek yetkilendirme her zaman
  sunucu (ofis) tarafında doğrulanmalı (öyle görünüyor, ama istemci kontrolünü kapatmak
  beklenmedik UI durumlarına yol açabilir).

> **Durum (2026-06-29 — giderildi):** Enjekte JS'teki SWR cache artık **kalıcı değil,
> tarayıcı oturumuna bağlı.** `_rewrite_body` içindeki script'e oturum-mührü eklendi:
> - **Oturum SID'i:** `sessionStorage['UYAP_SWR_SID']` (yoksa `crypto.randomUUID` ile üretilir).
>   sessionStorage sekme kapanınca silinir → yeni sekme/tarayıcı/**sonraki kullanıcı** farklı
>   SID üretir.
> - **Otomatik temizleme:** `cacheReady` init'i IndexedDB'deki `__sid__` mührünü mevcut SID ile
>   karşılaştırır; uyuşmazsa cache ÖNCEKİ oturuma aittir → `indexedDB.deleteDatabase` ile
>   **tümü silinir**, taze açılıp yeni mühür basılır. Tüm `dbGet`/`dbSet` bu init'i bekler →
>   sızdıran eski veri hiçbir okumada görünmez. Aynı oturum içi navigasyon/yeniden yükleme
>   hızı (instant render) korunur.
> - **Logout temizliği:** `isLogout(url)` (çıkış/logout) görülürse XHR `send`/`fetch` öncesi
>   `purgeCache()` çağrılır — oturum kapatıp makineyi bırakan kullanıcı için ek koruma.
>
> Böylece raporun "yalnızca oturum süresince, oturum bitince temizle" önerisi karşılandı; ortak/
> halka açık makinede sonraki kullanıcının kalıcı cache'e erişmesi engellendi.
> `INJECT_VERSION` script kaynağının hash'i olduğu için otomatik bump oldu → `office_agent`
> kabuk (HTML) önbelleği kendiliğinden geçersizleşir (elle işlem yok).
>
> **Bilinçli olarak yapılmayan (kalan):**
> - **Allow-list yerine deny-list:** Cache uygunluğu hâlâ kara liste (`isCacheable` blacklist).
>   Beyaz listeye geçmek UYAP'ın salt-okunur uç envanterini gerektirir (elde yok); yanlış
>   daraltma meşru okumaları bozar. Mevcut kara liste (kaydet/sil/imza/ödeme/çıkış…) korundu.
> - **`loginState` bypass:** Kaldırılmadı — her sayfa yüklemesinde zaten yeniden `'true'`
>   set ediliyor ve gerçek yetkilendirme ofis (sunucu) tarafında. Oturum-mührü temizliği
>   IndexedDB veriyi sildiği için asıl sızıntı vektörü (kalıcı müvekkil/dosya verisi) kapandı.

---

## 6. Django `DEBUG=True` + sabit/zayıf `SECRET_KEY` — **Orta‑Yüksek**

**Konum:**
- `Uyap Haricen Giriş/uyap_panel/web/uyap_web/settings.py:24-25` →
  `SECRET_KEY = "uyap-panel-local-only-not-a-secret"`, `DEBUG = True`.
- `models/uyapdata/settings.py:21-22` → `SECRET_KEY` varsayılanı `"dev-insecure-degistir"`,
  `DEBUG = True`.

`DEBUG=True` ile bir istisna oluştuğunda Django, ayarları/ortam değişkenlerini/kod
parçalarını içeren ayrıntılı hata sayfası döndürür. Yerel panel 127.0.0.1'e bağlı olsa da,
yanlışlıkla dışa açılırsa bilgi sızıntısı olur. Sabit `SECRET_KEY` oturum/CSRF imzalarını
tahmin edilebilir kılar.

**Öneri:**
- `DEBUG`'ı ortam değişkeninden okuyun, varsayılanı `False` yapın.
- `SECRET_KEY`'i ortamdan alın; yoksa ilk çalıştırmada rastgele üretip güvenli bir yere
  yazın. Sabit literal kullanmayın.

**Giderildi (2026-06-29):**
- **DEBUG:** İki ayar da artık varsayılan `False`. Yalnızca açık bayrakla açılır —
  panel: `UYAP_PANEL_DEBUG=1`, ORM katmanı: `UYAP_DEBUG=1`.
- **SECRET_KEY:** Sabit literaller (`uyap-panel-local-only-not-a-secret`,
  `dev-insecure-degistir`) kaldırıldı. `_gizli_anahtar_al()` helper'ı: önce ortam değişkeni
  (`UYAP_PANEL_SECRET_KEY` / `UYAP_SECRET_KEY`), yoksa `%LOCALAPPDATA%\UyapIcra` altında
  kalıcı dosyadan okur, o da yoksa `secrets.token_urlsafe(64)` ile üretip yazar (POSIX'te
  `chmod 600`). Diske yazılamazsa oturumluk anahtara düşer.
- **Statik dosya regresyonu:** `DEBUG=False`'ta `runserver` statikleri servis etmediğinden
  `run_web.py` artık `--insecure` ile başlatır → yerel panel arayüzü bozulmaz (yalnızca
  127.0.0.1). Doğrulandı: panel `DEBUG=False`, 86 karakterlik rastgele anahtar, dosya kalıcı.

---

## 7. Brute‑force koruması yok + kullanıcı adı sızdırma — **Orta**

**Konum:** `vendor_server.py` (`/ws`, `/api/office`, `/ofis/login`, `/admin`, `/api/reset`),
`accounts.py:391-412` (`authenticate`), `accounts.py:431` (reset).

- Hiçbir giriş ucu **hız sınırlama / hesap kilitleme** içermiyor; parola brute‑force'a açık.
- `authenticate` ve `request_password_reset`, "Kullanıcı bulunamadı" ile "Parola hatalı"yı
  ayırıyor → **kullanıcı adı enumeration**. Aynı şekilde reset, var olmayan kullanıcıyı
  açıkça belirtiyor.
- `/admin` ve `/ofis/login` denemelerinde de gecikme/limit yok.

**Öneri:**
- IP+kullanıcı bazlı hız sınırlama ve üstel geri çekilme (exponential backoff) ekleyin.
- Giriş ve sıfırlama yanıtlarını **tek tip** yapın ("kullanıcı adı veya parola hatalı" /
  "tanımlıysa bağlantı gönderildi") — varlık bilgisini sızdırmayın.
- Otomatik üretilen parolalar güçlü (`token_urlsafe`), fakat kullanıcı/master'ın
  belirlediği parolalar için minimum güç politikası uygulayın (bkz. #8).

> **Durum (2026-06-30 — giderildi):** Hem `vendor_server.py` hem `accounts.py`
> (+ birebir kopya `vendor_deploy/`) güvenli‑varsayılana çekildi:
>
> - **Oran sınırlama / brute‑force.** Yeni bellek içi `_LoginGuard` (IP+kategori bazlı):
>   kayan 15 dk pencere, **5 başarısızlıktan sonra üstel geri çekilme** (2,4,8…→tavan 5 dk),
>   **10 başarısızlıkta 15 dk tam kilit**, başarılı girişte sayaç sıfırlanır. `_client_ip`
>   PaaS/ters‑proxy ardında `X‑Forwarded‑For`'un ilk adımını kullanır (yalnızca sınırlama için;
>   yetki XFF'e güvenmez). Uygulandığı uçlar: signaling **`/ws`** (kullanıcı+parola doğrulaması),
>   masaüstü **`/api/office`**, tarayıcı **`/ofis/login`**, **`/admin`** (Basic‑Auth; boş/eksik
>   başlık sayılmaz → kilit‑DoS yok, yalnızca yanlış parola sayılır), parola‑sıfırlama
>   (**`/reset`**, **`/api/reset`** — her talep bir maliyet). Aşımda **429 + `Retry‑After`**.
> - **Kullanıcı adı enumeration + timing.** `accounts.authenticate` artık "kullanıcı yok" ile
>   "parola yanlış"ı **ayırt etmez**: tek‑tip `_GENERIC_AUTH_FAIL` ("Kullanıcı adı veya parola
>   hatalı.") döner ve kullanıcı yokken bile **kukla bir kayda karşı PBKDF2 çalıştırır**
>   (`_DUMMY_PW_REC`) → yanıt SÜRESİ de sızdırmaz. Hesap/ofis pasiflik mesajları yalnızca
>   **parola doğrulandıktan sonra** verilir (görmek için zaten geçerli parola gerekir → enumeration
>   değil).
> - **Parola sıfırlama yanıtı tek‑tip.** `_do_reset_request` artık kullanıcı/e‑posta VAR/YOK ya
>   da "alıcı e‑posta tanımsız" farkını **dışarı sızdırmaz**: her durumda tek‑tip `_RESET_GENERIC`
>   ("kayıtlıysa bağlantı gönderildi") döner; maskeli alıcı e‑postası artık DÖNDÜRÜLMEZ; içsel
>   sebep yalnızca sunucu günlüğüne yazılır.
> - **Kalan (bilinçli):** kullanıcı/master'ın belirlediği parolalar için minimum güç politikası
>   bulgu **#8**'de (min‑uzunluk) ele alınır. Sınırlayıcı tek‑süreç bellek içidir; çok‑örnekli
>   (replica) PaaS dağıtımında paylaşımlı bir depo (Redis vb.) gerekir — mevcut tek‑servis
>   modelinde gerekmez.

---

## 8. Parola sıfırlama jetonu loglara/URL'e düşüyor; zayıf parola izni — **Orta**

**Konum:** `vendor_server.py:90-112` (`_send_email` test modu), `:978` (`reset_get` GET token),
`accounts.py:471` (`len(new_password) < 4`).

- SMTP yapılandırılmadığında (`MAIL_ENABLED=False`) sıfırlama bağlantısı **sunucu konsoluna
  yazılıyor** ve `LAST_TEST_MAIL`'de tutuluyor. Logları görebilen herkes geçerli sıfırlama
  bağlantısını (= hesap devralma) elde eder. Üretime yanlışlıkla bu modda çıkılırsa kritik olur.
- Sıfırlama jetonu **GET URL'inde** (`/reset?token=...`) taşınıyor; proxy/erişim logları,
  tarayıcı geçmişi, Referer ile sızabilir.
- Yeni parola için **minimum 4 karakter** — çok zayıf.

**Öneri:**
- Üretimde SMTP zorunlu olsun; yapılandırılmamışsa sıfırlama akışını **devre dışı bırakın**,
  jetonu asla loglamayın.
- Jetonu mümkünse POST gövdesinde işleyin; en azından kullanıldıktan sonra hemen geçersiz
  kılın (zaten tek kullanımlık — iyi) ve logdan maskeleyin.
- Minimum parola uzunluğunu/gücünü artırın (ör. ≥ 10–12 karakter).

> **Durum (2026-06-30 — giderildi):** Hem `vendor_server.py` hem `accounts.py`
> (+ birebir kopya `vendor_deploy/`) güvenli-varsayılana çekildi:
>
> - **Üretimde sıfırlama akışı SMTP'ye bağlı.** Yeni `MAIL_TEST_MODE` (opt-in `UYAP_MAIL_TEST=1`)
>   ve `RESET_ENABLED = MAIL_ENABLED or MAIL_TEST_MODE`. SMTP yapılandırılmamış **ve** test modu
>   kapalıyken `_do_reset_request` jeton **ÜRETMEZ** ve hiçbir şey **loglamaz**; enumeration
>   savunması için yine tek-tip `_RESET_GENERIC` döner. Jeton/bağlantının sunucu konsoluna
>   yazılması artık yalnızca **açık DEV test modunda** olur (üretimde asla). `_send_email` test
>   dalı test modu kapalıyken `False` döner (loglamaz). Talep formu test modu kapalıyken
>   "sıfırlama şu an kullanılamıyor" notu gösterir.
> - **Jeton sızıntısı azaltıldı.** `_ofis_response` artık `Referrer-Policy: no-referrer` ekliyor →
>   `/reset?token=…` URL'i alt-istek/gezinmede **Referer ile sızmaz**; `Cache-Control: no-store`
>   zaten vardı (tarayıcı geçmişi/proxy cache). Jeton hâlâ tek kullanımlık ve kullanıldıktan
>   hemen sonra geçersizleşiyor (mevcut iyi davranış korundu). GET URL'i e-posta bağlantısının
>   doğasında olduğundan kaldırılmadı; asıl parola değişimi POST gövdesinde (gizli alan) yapılır.
> - **Minimum parola gücü.** `accounts.MIN_PASSWORD_LENGTH` (ortamdan `UYAP_MIN_PASSWORD_LEN`,
>   varsayılan **10**, taban 8) + `password_policy_error()`. **İnsanın seçtiği** tüm parolalara
>   uygulanır: `reset_password_with_token`, `create_office` (master), `create_user`,
>   `reset_user_password`, master self-servis `passwd`/`reset`, `/api/office` passwd. Otomatik
>   üretilen parolalar (`generate_password` ≈ 12 karakter, güçlü) **muaftır**. Eski `< 4` literal
>   kontrolleri (×3) kaldırıldı; masaüstü GUI ön-kontrolü (`uyap_app.py`) de 10'a çekildi (asıl
>   zorlama sunucuda).
> - **Kalan (bilinçli):** karmaşıklık (büyük/küçük/rakam/sembol) zorunluluğu eklenmedi —
>   uzunluk tek başına makul; istenirse `password_policy_error` tek noktada genişletilebilir.
>   `LAST_TEST_MAIL` yalnızca DEV test modunda dolar ve hiçbir uca bağlı değildir (dışa açılmaz).

---

## 9. Windows harici ortamda parola/PIN düz metin saklanıyor — **Orta**

**Konum:** `uyap_panel/core/config.py:66-93` (`encrypt_secret`/`decrypt_secret`).

DPAPI yalnızca Windows'ta çalışıyor; `IS_WINDOWS` değilse `encrypt_secret` **düz metni
olduğu gibi** döndürüyor ve `uyap_app_config.json`'a UYAP parolası + e‑imza PIN düz yazılıyor.
DPAPI başarısız olursa da düz metne düşüyor (`except: return plain`).

**Öneri:**
- Windows dışı platformlarda OS keyring (ör. `keyring` kütüphanesi) veya parola tabanlı
  şifreleme kullanın; sessizce düz metne düşmeyin — en azından kullanıcıyı uyarın.
- DPAPI başarısızlığında parolayı kaydetmeyin (fail‑closed).
- Config dosyası izinlerini kısıtlayın.

> **Durum (2026-06-30 — giderildi):** Her İKİ kopya — kanonik `uyap_panel/core/config.py`
> **ve** `uyap_app.py` (Panel bunu kullanır) — düz‑metne‑düşmeyecek biçimde yeniden yazıldı:
>
> - **Fail‑closed.** `encrypt_secret` artık **ASLA** düz metin döndürmez. Windows'ta DPAPI
>   başarısız olursa düz metin yerine passphrase fallback denenir; hiçbiri olmazsa sır
>   **DİSKE YAZILMAZ** (boş döner) ve kullanıcı **uyarılır** (`_warn_once`). Eski
>   `except: return plain` davranışı tamamen kaldırıldı.
> - **Windows dışı şifreleme.** `UYAP_CONFIG_SECRET` ortam değişkeni verilirse PBKDF2‑HMAC‑SHA256
>   (200k iterasyon) ile türetilen anahtardan **Fernet** (cryptography, projede zaten kurulu)
>   ile şifrelenir; jeton `pbk:<b64salt>:<fernet>` biçiminde. Env yoksa sır kaydedilmez +
>   uyarı (sessiz düz‑metin YOK). DPAPI ile şifrelenmiş sır başka bir OS'te açılmaya çalışılırsa
>   boş döner + uyarı (sızdırmaz).
> - **Geriye dönük okuma.** Eski sürümden kalan DÜZ METİN sır okunabilir (decrypt onu olduğu
>   gibi döndürür); bir sonraki kayıtta güvenli biçimde yeniden şifrelenir. Jeton biçimleri:
>   `dpapi:<b64>` | `pbk:<b64salt>:<fernet>` | (eski) düz metin.
> - **Dosya izinleri.** `save_config` artık **atomik** (geçici dosya + `os.replace`) yazıyor ve
>   `_restrict_perms` ile dosyayı **0600**'e (yalnızca kullanıcı) çekiyor (POSIX'te etkili;
>   Windows'ta dosya zaten kullanıcı profilinde).
> - **Doğrulandı:** passphrase round‑trip, Windows DPAPI round‑trip, fail‑closed (env yok → boş),
>   DPAPI‑başka‑OS (boş + uyarı), legacy düz‑metin okuma. Türkçe konsol (cp1254) uyumu için
>   uyarı metinlerinde `→` yerine `->` kullanıldı (UnicodeEncodeError önlendi).
> - **Kalan (bilinçli):** Windows dışında kalıcı saklama kullanıcıdan `UYAP_CONFIG_SECRET`
>   ister (her oturum/ortamda ayarlı olmalı) — OS keyring (`keyring`) kütüphanesi kurulu
>   olmadığından passphrase modeli seçildi; `keyring` eklenirse aynı arayüze takılabilir.

---

## 10. `--no-verify` ile UYAP TLS doğrulaması kapatılabiliyor — **Düşük‑Orta**

**Konum:** `uyap_proxy.py:1311` (`--no-verify`), çağrı zincirinde `verify_ssl=not no_verify`.

Varsayılan kapalı (iyi), ama açıldığında UYAP'a giden bağlantıda sertifika doğrulaması
devre dışı kalır → MITM riski (yargı sistemi trafiği için ciddi). Bayrak kalıcı kullanım
için cazip olabilir.

**Öneri:** Bu bayrağı yalnızca geliştirme için bırakın, kullanıldığında belirgin uyarı basın;
üretim derlemesinde tamamen kaldırmayı değerlendirin.

> **Durum (2026-06-30 — giderildi):** `--no-verify` artık **çift kilitli**:
> - **Açık env onayı şartı.** `uyap_proxy.resolve_verify_ssl(no_verify)` (configure() bunu kullanır;
>   GUI/Panel/office_agent yolları configure üzerinden GEÇER → otomatik kapsanır) `--no-verify`'i
>   yalnızca **`UYAP_ALLOW_INSECURE_TLS=1`** açıkça ayarlıyken kabul eder. Env yoksa bayrak
>   **YOK SAYILIR** ve doğrulama **AÇIK** kalır (fail‑safe). Üretim derlemesi bu env'i ayarlamadığı
>   için bayrak pratikte etkisizdir (raporun "üretimde kaldır" önerisinin pragmatik karşılığı).
> - **Belirgin uyarı.** Hem yok‑sayıldı hem (env'le) devre‑dışı durumunda 72 karakterlik `!` çerçeveli
>   MITM uyarısı basılır. ASCII metin kullanıldı (Türkçe konsol/cp1254 uyumu).
> - **Bağımsız betik de kapsandı.** `uyap_giris_dışarıdan.py` aynı env kapısını kendi `main()`'inde
>   uyguluyor (verify varsayılan `True`, opt‑in ile kapanır).
> - **Doğrulandı:** `no_verify=False→True`; `True`+env yok→`True` (yok sayıldı, uyarı); `True`+env=1→`False` (uyarı).

---

## 11. Logger üretilen `*_core.py`'ı import edip çalıştırıyor — **Düşük (yerel RCE yüzeyi)**

**Konum:** `Panel/modules/uretilmis_runner.py:33` `importlib.import_module(f"modules.{core_modul}")`,
`logger_core.py` (`modul_taslagi_uret`/`oturum_akisi_uret` → `modules/` altına `.py` yazar).

Logger, kaydedilen UYAP oturumundan kod üretip `modules/` altına yazıyor; runner bunları
import ederek (yani **çalıştırarak**) sürüyor. `modules/` dizinine yazabilen bir saldırgan
veya kötü biçimlendirilmiş üretim verisi keyfi kod çalıştırabilir. Etki yerel kullanıcıyla
sınırlı olduğundan önem düşük, ama "mağaza/eklenti" modeli uzaktan indirme'ye evrilirse
(bkz. `magaza_core.py`, App Store hedefi) **tedarik zinciri RCE'sine** yükselir.

**Öneri:**
- Üretilen modül adlarını/yollarını katı doğrulayın (yol gezinmesi, beklenen şablon).
- İleride uzaktan eklenti indirilecekse: imzalı paketler, bütünlük doğrulaması (hash/imza),
  ve mümkünse sandbox/yetki kısıtı şart.

> **Durum (2026-06-30 — giderildi):** `uretilmis_runner.py` artık modülü import etmeden ÖNCE
> **katı doğrulama** yapıyor (`_modul_adi_guvenli_mi`):
> - **Ad deseni.** `^[A-Za-z_][A-Za-z0-9_]*$` — tek parça python tanımlayıcısı; nokta/slash/`..`
>   içeremez → `modules.os` gibi **paket‑kaçışı** ve **yol‑gezinmesi** engellenir.
> - **Yol sınırı.** `os.path.realpath` + `commonpath` ile içe aktarılacak `.py`'nin GERÇEKTEN
>   `modules/` altında ve var olduğu doğrulanır (sembolik bağ/traversal kaçışları kapanır).
> - Geçersiz/bulunamayan ad → import EDİLMEZ; `_hata` ile arayüzde "reddedildi" gösterilir.
>   Logger zaten `_kimlik()` ile güvenli ad üretiyordu; bu, diskteki kayıt defteri kurcalansa
>   bile rastgele kod yürütmeyi önleyen **savunma‑derinliği** katmanıdır.
> - **Doğrulandı:** geçerli+var olan modül kabul; `os`/`sub.evil`/`../etc`/`sorgu/..`/`bad-name`/
>   `..`/`foo.bar`/boş/var‑olmayan tümü **reddedildi**.
> - **Kalan (gelecek, App Store hedefi):** uzaktan eklenti indirme eklenirse imzalı paket +
>   hash/imza bütünlük doğrulaması + sandbox şarttır (bkz. `magaza_core.py`); yerel‑üretim
>   modelinde mevcut ad/yol doğrulaması yeterli.

---

## 12. XML ayrıştırma — entity expansion DoS — **Düşük**

**Konum:** `Uyap Haricen Giriş/uyap_core/mts/parse.py:20`,
`Dosya Açılış/MTS Takip Açılış/mts_donusum.py:26` (`xml.etree.ElementTree`).

Stdlib `ElementTree` varsayılan olarak harici varlıkları (XXE) çözmez, fakat
"billion laughs" türü iç‑varlık genişlemesi DoS'una açık olabilir. Kullanıcı dosyaları
(takip XML'i) ayrıştırıldığı için kötü niyetli/bozuk dosya işleme sürecini kilitleyebilir.

**Öneri:** Güvenilmeyen XML için `defusedxml` kullanın; en azından boyut/derinlik sınırı koyun.

> **Durum (2026-06-30 — giderildi):** Her İKİ ayrıştırma noktası (`uyap_core/mts/parse.py` ve
> `Dosya Açılış/MTS Takip Açılış/mts_donusum.py`) artık doğrudan `ET.parse` yerine ortak
> `_guvenli_xml_parse()` kullanıyor:
> - **defusedxml öncelikli.** Kuruluysa `defusedxml.ElementTree.parse` kullanılır (entity/XXE
>   tamamen kapalı). Kurulu değilse (şu an öyle) fallback devreye girer.
> - **Boyut sınırı.** Dosya > **20 MB** ise ayrıştırılmadan reddedilir (bellek/CPU DoS tavanı).
> - **DOCTYPE/DTD reddi.** Prolog'da `<!DOCTYPE` varsa **reddedilir** → özel iç‑varlık tanımı
>   (billion laughs) ve harici varlık (XXE) zaten tanımlanamaz; meşru UYAP takip XML'inde DOCTYPE
>   bulunmaz, bu yüzden gerçek dosyalar etkilenmez.
> - **Doğrulandı:** normal XML kabul; `<!DOCTYPE … <!ENTITY …>>` içeren "billion laughs" örneği
>   reddedildi.
> - **İsteğe bağlı iyileştirme:** `pip install defusedxml` ile fallback hiç çalışmaz, en sağlam
>   koruma (derinlik dahil) otomatik devreye girer — kod değişikliği gerekmez.

---

## 13. Repo içinde `chrome_profile/` cache'leri ve diğer artefaktlar — **Düşük‑Orta**

**Konum:** `Panel/modules/logger_data/chrome_profile/...`, `Logger/chrome_profile/...`,
`Sorgu/SGK Sorgu/chrome_profile/...`, `Uyap Haricen Giriş/*/static_cache/...`.

Tarayıcı profil/cache dizinleri proje ağacında duruyor. Bunlar UYAP'tan çekilmiş
sayfa/yanıt verisi (muhtemelen kişisel/dosya verisi) ve potansiyel oturum artefaktları
içerebilir. Yedek/paylaşım/sürüm kontrolüne girerse veri sızıntısı olur.

**Öneri:** Bu dizinleri kullanıcı veri klasörüne (`%LOCALAPPDATA%`) taşıyın, `.gitignore`'a
ekleyin, periyodik temizleyin ve paylaşımdan önce silin.

> **Durum (2026-06-30 — kısmen giderildi):**
> - **`.gitignore` eklendi (repo kökü).** Tüm hassas artefakt desenleri sürüm kontrolünden
>   dışlandı: `chrome_profile/`, `uyap_profil/`, `static_cache/`, `Panel/modules/logger_data/`,
>   `uyap_session_cookies.json` (#1), `uyap_app_config.json` (#9), `accounts.json`,
>   `gw_local_token`, üretilen `*_Yatay_Alacak_Listesi.xlsx`, `__pycache__/`, `.venv/` vb.
>   (Repo henüz `git init` edilmemiş; bu önleyici — ilk commit'te hassas veri sızmaz.)
> - **Temizleme betiği eklendi.** `temizle_artefaktlar.py` (repo kökü) tüm hassas dizin/dosyaları
>   bulur; **varsayılan KURU ÇALIŞMA** (yalnızca listeler, ~559 MB / 11 öğe tespit edildi),
>   `--sil` ile (onay isteyerek) siler, `--sil -y` ile sorgusuz. `.venv`/`.git`/`site-packages`
>   atlanır. **Paylaşım/yedek/arşiv öncesi** çalıştırılmalı. (Mevcut profiller SİLİNMEDİ — çalışan
>   oturumları bozmamak için silme kullanıcıya bırakıldı.)
> - **Kalan (bilinçli):** Profillerin `%LOCALAPPDATA%`'ya taşınması YAPILMADI — bu, üç ayrı bot
>   akışının (`mts_bot.py`, `Uyap_session_logger.py`, `logger_core.py`; bazıları cwd‑bağıl
>   `"chrome_profile"` kullanıyor) çalışma mantığına dokunmayı gerektirir ve regresyon riski taşır.
>   Sürüm‑kontrolü ve paylaşım sızıntısı (asıl vektör) `.gitignore` + temizleme betiğiyle kapatıldı;
>   taşıma, ayrı ve dikkatli bir refactor olarak ertelendi.

---

## Genel / Çapraz Kesen Öneriler

1. **"Yerel = güvenli" varsayımını gözden geçirin.** Hem ofis proxy'si hem yerel panel,
   127.0.0.1'e güvenip yetkiyi gevşetiyor. Çok‑kullanıcılı makineler, kötücül yazılım ve
   tarayıcı tabanlı (DNS rebinding/CSRF) saldırılarda bu varsayım kırılır.
2. **Varsayılanları güvenli yapın (secure by default).** `0.0.0.0` bind, `DEBUG=True`,
   sabit `SECRET_KEY`, "boş depo = serbest", min‑4 parola — hepsi varsayılan olarak gevşek.
   Üretim varsayılanları kısıtlayıcı olmalı; gevşetme açık bayrakla yapılmalı.
3. **Hassas sırların disk/log/bellek yaşam döngüsü.** Oturum çerezleri, parola, PIN ve
   sıfırlama jetonları için: şifreli sakla, kısa ömürlü tut, kullanımdan sonra sil, loglama.
4. **Kimlik uçlarına hız sınırı + tek tip yanıt.** Brute‑force ve enumeration'ı engelleyin.
5. **HTTPS zorunluluğu.** Tüm dışa açım yollarında (panel, proxy, vendor) TLS ve `Secure`
   çerez; düz HTTP'de oturum/parola taşımayın.
6. **CSRF/Origin koruması** durum değiştiren tüm yerel API uçlarında.

---

*Bu rapor inceleme anındaki kod durumunu yansıtır. Düzeltmeler sonrası yeniden değerlendirme
ve mümkünse bağımsız bir sızma testi önerilir.*
