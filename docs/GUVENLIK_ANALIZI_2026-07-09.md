# Güvenlik Analizi (2. Tur) — UYAP Django / Kararlı Projesi

> Tarih: 2026-07-09
> Kapsam: Tüm proje kodu (statik inceleme, 5 paralel odak: Panel, proxy/office-agent,
> webapp+vendor_deploy+signaling, DB katmanı, secrets/bağımlılık taraması) + üretim alan adına
> (`https://www.cokluuyap.com`) hafif/yıkıcı-olmayan canlı problar (başlık/çerez/CORS/robots.txt/
> yaygın hassas yol kontrolü — brute-force, enjeksiyon denemesi veya Render altyapısına yük
> bindirecek hiçbir işlem yapılmadı).
> Önceki rapor: `guvenlik_analizi.md` (2026-06-29). O rapordaki 13 bulgunun **tamamı bu turda
> yeniden doğrulandı** ve "giderildi" işaretli olanların gerçekten kodda düzeltilmiş durumda
> olduğu teyit edildi (bkz. "Önceki bulgular — yeniden doğrulama" bölümü). Bu belge yalnızca
> **yeni** bulguları içerir.
> Not: Aşağıdaki her bulgu için ilgili dosya doğrudan okunarak teyit edilmiştir; "iddia"
> düzeyinde bırakılmamıştır. Doğrulama derinliği madde başına ayrıca belirtilmiştir.

---

## Özet Tablo

| # | Bulgu | Önem | Konum |
|---|-------|------|-------|
| 1 | `X-Uyap-Local-Token` jeton kontrolü `Origin: null` ile bypass edilebiliyor | **Kritik** | `uyap_core/uyap_proxy.py:1393-1441` |
| 2 | SGK Excel yükleme yolu, dosya adını sanitize etmeden diske yazıyor (path traversal) | **Yüksek** | `uyap_panel/web/panel/views.py:362-367` |
| 3 | `/owner` oturum çerezini imzalayan anahtar, ayarlanmışsa `/admin` parolasına düşüyor | **Yüksek** | `vendor_deploy/vendor_server.py:66,127-128` |
| 4 | Gömülü PostgreSQL tamamen parolasız (`trust`), aynı makinedeki her hesaba açık | **Yüksek** *(bilinçli sadeleştirme, ama sertleştirilmeli)* | `db_baslat.py:98-101` |
| 5 | Parola-sıfırlama/sipariş-teslim linkleri, doğrulanmamış `Host` başlığından kuruluyor | **Orta-Yüksek** | `vendor_deploy/vendor_server.py:120,267-269,348,1459` |
| 6 | WebRTC/relay veri yolu, `require_auth`'un sağladığı hiçbir korumadan geçmiyor | **Orta-Yüksek** *(ciddiyeti signaling sunucusunun oda-kabul mantığına bağlı, o incelenmedi)* | `uyap_core/office_agent.py:289-320,474-486` |
| 7 | Panel'de canlı UYAP parolası, oturum boyunca bellekte düz metin tutuluyor | **Orta** *(paylaşım özelliği için bilinçli tasarım tercihi)* | `Panel/web/server.py:1657` |
| 8 | Herhangi bir oturum açmış panel kullanıcısı, sunucuda yeni Python kodu üretip çalıştırabiliyor | **Orta** | `Panel/modules/logger_core.py` (~772-1201), `Panel/web/server.py:1742,1727-1731` |
| 9 | Panel `/api/login` uçlarında deneme sınırlama/kilit yok | **Orta** | `Panel/web/server.py:1631-1666` |
| 10 | Logger indirilen dosyayı otomatik `os.startfile()` ile çalıştırıyor + adını sanitize etmiyor | **Orta** | `Panel/modules/logger_core.py:528-542` |
| 11 | `SECRET_KEY` dosyasına `chmod 600` çağrısı Windows'ta sessizce hiçbir şey yapmıyor | **Düşük** | `models/uyapdata/settings.py:43`, `uyap_panel/web/uyap_web/settings.py:55` |
| 12 | DB adı ortam değişkeninden f-string ile SQL'e giriyor (parametrize değil) | **Düşük** *(tetiklemek zaten yerel-ele-geçirme gerektirir)* | `db_baslat.py:131`, `sifirla_veritabani.py:131-133` |
| 13 | `usage_logger.py` (gerçek prod giriş noktası) artık var olmayan `vendor_server._admin_ok` fonksiyonunu çağırıyor → `/admin/usage` 500 veriyor | **Düşük** *(fail-closed ama izleme paneli kör)* | `vendor_deploy/usage_logger.py:326,333` |
| 14 | Çıkış yapma / parola değiştirme, önceden verilmiş oturum çerezini iptal etmiyor | **Düşük-Orta** | `vendor_deploy/vendor_server.py:748-749,859-860,1826-1830` |
| 15 | Sitede `Content-Security-Policy` / `Permissions-Policy` başlığı hiç yok | **Düşük-Orta** | canlı, tüm yollar |
| 16 | `robots.txt`, `/admin /owner /reset` gibi hassas yolları herkese açık şekilde listeliyor | **Düşük** (bilgi ifşası) | canlı, `/robots.txt` |
| 17 | `vendor_deploy` çalışma kopyasında commit'lenmiş ama push edilmemiş bir değişiklik var | **Not** (güvenlik açığı değil, operasyonel risk) | `vendor_deploy/webapp/index.html` |

---

## 1. `X-Uyap-Local-Token` kontrolü `Origin: null` ile bypass ediliyor — **Kritik**

**Konum:** `uyap_core/uyap_proxy.py:1393-1441` (doğrudan okunarak teyit edildi).

`require_auth`, aynı makineden (127.0.0.1/::1) gelen isteklerde tam yetki (`GW_USER`) vermeden
önce ya geçerli `X-Uyap-Local-Token` ister ya da `_looks_like_local_browser()` ile "gerçek bir
tarayıcıdan geliyor" onayı arar. Ancak:

- `_looks_like_local_browser` (satır 1399): `Origin` başlığı **her ne olursa olsun** varsa
  (boş string hariç) `True` döner — değeri kontrol edilmez.
- DNS-rebinding koruması (satır 1424): `Origin` başlığı tam olarak `"null"` ise bu korumayı
  **atlar** (`if origin and origin.lower() != "null":`).

Sonuç: `Origin: null` başlığıyla gönderilen bir istek hem DNS-rebinding kontrolünden muaf
tutuluyor hem de "gerçek tarayıcı" testini geçiyor — jeton hiç sunulmadan `GW_USER` (tam UYAP
yetkisi) elde ediliyor. `Origin: null`, tarayıcılarda `file://` sayfalarının veya `sandbox`
attribute'lu iframe'lerin fetch/XHR isteklerinde **doğal olarak** gönderilen bir değerdir —
yani bunu tetiklemek için özel bir araç gerekmez, kurbanın yerel bir HTML dosyası açması ya da
kötü niyetli bir sayfadaki sandbox'lı iframe'in 127.0.0.1:8800'e istek atması yeterlidir. Bu,
yerel-yetki jetonunun var oluş amacını (jetonu okuyamayan bir sürecin tam yetki alamaması)
doğrudan boşa çıkarıyor.

**Öneri:** `_looks_like_local_browser` içinde `Origin` değerinin gerçekten IP/localhost'a
işaret ettiğini kontrol edin (mevcut `_is_ip_or_local` fonksiyonu zaten var, sadece "null"
durumuna da uygulanmalı); DNS-rebinding guard'daki `!= "null"` istisnasını kaldırıp `"null"`
değerini de reddedilecek/yetersiz sayılacak şekilde ele alın.

---

## 2. SGK Excel yükleme — path traversal / rastgele dosya yazma — **Yüksek**

**Konum:** `uyap_panel/web/panel/views.py:362-367` (doğrudan okunarak teyit edildi).

```python
suffix = os.path.splitext(upload.name)[1] or ".xlsx"
tmpdir = tempfile.mkdtemp(prefix="uyap_sgk_")
path = os.path.join(tmpdir, upload.name)
with open(path, "wb") as f:
```

`upload.name`, istemcinin gönderdiği multipart `filename=` değeridir ve `os.path.basename()`
uygulanmadan doğrudan `tmpdir` ile birleştiriliyor. Django'nun `IE_sanitize` fonksiyonu yalnızca
ters-slash (`\`) tabanlı Windows-stili yolları temizler, ileri-slash (`/`) tabanlı `../`
gezinmesini **temizlemez**. Bu proje Windows'ta çalıştığından, Windows dosya API'leri hem `/`
hem `\` ayırıcılarını kabul eder — yani `../../../Users/<kullanıcı>/AppData/Roaming/Microsoft/
Windows/Start Menu/Programs/Startup/evil.xlsx` gibi bir dosya adıyla `tmpdir` dışına, Django
sürecinin yazma yetkisi olan herhangi bir yola dosya yazdırılabilir. Uç nokta `@login_required_api`
ile korunuyor (yani oturum açmış herhangi bir panel kullanıcısı tetikleyebilir); proxy agent
bulgusuna göre `/__panel__/...` yolu hem LAN hem WebRTC/relay üzerinden tünellendiği için bu
yalnızca localhost'tan değil, ofisin paylaştığı uzak üyelerden de erişilebilir olabilir (bu alt
iddia ayrıca doğrulanmadı, olası genişletme olarak not edilmiştir).

**Öneri:** `path = os.path.join(tmpdir, os.path.basename(upload.name))` — proje genelindeki
diğer tüm dosya-yazma uçları (`mts_vekalet`, `mts_dayanak`, `udf_process`, `uretilmis_run`)
zaten bunu yapıyor, sadece bu uç nokta eksik.

---

## 3. `/owner` oturum imza anahtarı, `/admin` parolasına düşebiliyor — **Yüksek**

**Konum:** `vendor_deploy/vendor_server.py:65-66,127-128,623-654` (doğrudan okunarak teyit edildi).

```python
ADMIN_PASSWORD = os.environ.get("UYAP_ADMIN_PASSWORD", "")
...
SESSION_SECRET = (os.environ.get("UYAP_SESSION_SECRET") or ADMIN_PASSWORD
                  or secrets.token_hex(32))
```

`ADMIN_PASSWORD` artık kodda **başka hiçbir yerde** kullanılmıyor (grep ile teyit edildi) —
65-66. satırdaki yorum ("Admin ekranı parolası (HTTP Basic)") güncel değil, eski bir Basic-Auth
mekanizmasından kalma; gerçek `/owner` girişi artık kullanıcı adı+parola+TOTP ile çalışıyor.
Tek canlı etkisi: `UYAP_SESSION_SECRET` ayarlanmamışsa, `UYAP_ADMIN_PASSWORD` (operatör muhtemelen
eski yorum yüzünden hâlâ ayarlıyor olabilir) doğrudan `/owner` ve `/ofis` oturum çerezlerini
imzalayan HMAC anahtarı olur. `/owner` çerezinin yeniden okunması (`_read_owner_session`,
satır 641-654) yalnızca imza+rol+aktiflik kontrolü yapar, **TOTP'yi tekrar sormaz**. Yani bu
anahtar kırılırsa (bir admin parolası, TOTP korumasının aksine, offline brute-force'a açık
olabilir), sahte `owner|utku|<gelecek-exp>|<hmac>` çerezi üretilerek TOTP tamamen atlanabilir.

**Öneri:** `SESSION_SECRET` üretiminde `ADMIN_PASSWORD`'e düşmeyi kaldırın (yalnızca
`UYAP_SESSION_SECRET` veya rastgele üretilen anahtar); Render ortam değişkenlerinde
`UYAP_SESSION_SECRET`'in mutlaka ayarlı olduğunu doğrulayın (aksi halde her deploy'da oturumlar
düşer, ki bu daha küçük bir sorun). Ayrıca `UYAP_ADMIN_PASSWORD`'ün artık hiçbir işlevi
kalmadıysa ortam değişkenlerinden ve koddan tamamen kaldırılması karışıklığı önler.

---

## 4. Gömülü PostgreSQL parolasız (`trust`) — **Yüksek** (bilinçli, sertleştirilmeli)

**Konum:** `db_baslat.py:98-101` (doğrudan okunarak teyit edildi).

```python
"--auth-local=trust", "--auth-host=trust",
```

`DB_USER` varsayılanı `"postgres"` (satır 50) ve hiçbir yerde override edilmiyor. `pg_hba.conf`
tüm yerel-soket ve `127.0.0.1/32`+`::1/128` bağlantılarını parolasız kabul edecek şekilde
yazılıyor. `listen_addresses` hiçbir yerde override edilmediği için derleme-varsayılanı olan
`localhost`'ta kalıyor — yani ağdan değil, **aynı Windows makinesindeki başka her hesap/süreçten**
erişilebilir durumda. Paylaşımlı/çok kullanıcılı bir makinede veya bu makinede çalışan herhangi
bir kötü amaçlı yerel süreç, `psql -h 127.0.0.1 -U postgres -d uyap_icra` ile parolasız tam
superuser erişimi elde eder — TCKN, IBAN, adres, borçlu/alacaklı gibi tüm dosya verisi dahil.

**Öneri:** Kurulumda rastgele bir parola üretip `scram-sha-256` ile `pg_hba.conf`'a yazın (her
kurulum kendi parolasını üretsin, `%LOCALAPPDATA%`'daki diğer sırlar gibi saklansın); en azından
"trust" yerine "peer"/"scram-sha-256 + otomatik parola" tercih edin.

---

## 5. Parola-sıfırlama ve sipariş-teslim linkleri doğrulanmamış `Host` başlığından kuruluyor — **Orta-Yüksek**

**Konum:** `vendor_deploy/vendor_server.py:120,267-269,348,1459` (doğrudan okunarak teyit edildi).

```python
CANONICAL_HOST = (os.environ.get("UYAP_CANONICAL_HOST", "") or "").strip().lower()
...
def _public_base(request):
    scheme = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() or request.scheme
    ...  # request.host kullanılıyor, CANONICAL_HOST ayarlı değilse hiç doğrulanmıyor
```

`UYAP_CANONICAL_HOST` ayarlı değilse (Render `render.yaml`'da `sync:false`/opsiyonel olarak
tanımlı, yani varsayılan olarak boş), `_public_base` isteğin kendi `Host` başlığını **hiç
doğrulamadan** e-postayla gönderilen sıfırlama linkine (satır 348) ve satın-alma-sonrası
kimlik-teslim linkine (satır 1459) gömüyor. Eğer platform (Render/Cloudflare) sahte bir `Host`
başlığını olduğu gibi yansıtıyorsa, saldırgan `/reset` isteğine sahte `Host` koyup kurbana
`https://<saldırgan-alan-adı>/reset?token=...` linkini gönderilmesini sağlayabilir.

**Öneri:** `UYAP_CANONICAL_HOST`'u zorunlu hale getirin (ayarlı değilse e-posta gönderen
uçlarda `_public_base` yerine sabit `https://www.cokluuyap.com` kullanın), opt-in değil
fail-closed davranış tercih edin.

---

## 6. WebRTC/relay veri yolu, `require_auth`'un korumalarından tamamen bağımsız — **Orta-Yüksek**

**Konum:** `uyap_core/office_agent.py:289-320,474-486` + `handle_uyap_request` (206-288),
doğrudan okunarak teyit edildi.

`_wire_datachannel` ve `_serve_relay`, gelen her DataChannel/relay mesajı için doğrudan
`handle_uyap_request`'i çağırıyor — bu fonksiyonun içinde `uyap_proxy.require_auth`'un
sağladığı Basic-Auth/yerel-jeton/Host-Origin kontrollerinin **hiçbiri** yok. Yani
`uyap_proxy.py`'de inşa edilen tüm savunma katmanı yalnızca doğrudan-LAN HTTP yolunu (port 8800)
koruyor; asıl uzak-erişim yolu olan WebRTC/relay için güvenlik tamamen signaling sunucusunun
oda-kabul mantığına devrediliyor (bu dosya kapsamında incelenmedi). Yani signaling
tarafında bir hata/yanlış-yapılandırma varsa, bu dosyada onu yakalayacak ikinci bir savunma
katmanı yok.

**Öneri:** Signaling sunucusunun oda-kabul mantığını ayrı bir turda inceleyin; mümkünse
`handle_uyap_request`'e gelen relay/datachannel mesajlarına da en azından bir oturum/bilet
doğrulaması ekleyin (defense-in-depth), yalnızca signaling'e güvenmeyin.

---

## 7-10, 12-14: Orta/Düşük önem — kısa özet

- **#7 Panel'de parola bellekte düz metin** (`Panel/web/server.py:1657`) — Paylaşım özelliği
  (Al/Paylaş) için bilinçli tasarım tercihi olarak yorumlanmış; yine de bellek-ifşası/crash-dump
  senaryosunda tüm oturum açmış kullanıcıların canlı UYAP parolasını tek noktada toplayan bir
  risk yoğunlaşması oluşturuyor.
- **#8 Serbest kod üretimi/çalıştırma** (`Panel/modules/logger_core.py`) — Herhangi bir oturum
  açmış ofis kullanıcısı (admin ayrımı yok) `/api/logger/generate` ile `Panel/modules/`'a yeni
  `.py` yazdırıp `/api/uretilmis/run` ile çalıştırabiliyor; parametre isimleri güvenli şekilde
  sanitize ediliyor (klasik enjeksiyon değil) ama "her personel girişi = sunucuda yeni kod
  yazıp çalıştırma" yetkisi normal kullanıcı/admin ayrımı olmadan mevcut.
- **#9 `/api/login` deneme sınırlaması yok** (`Panel/web/server.py:1631-1666`) — Varsayılan
  bağlama 127.0.0.1 olduğu için etkisi sınırlı, ama `UYAP_PANEL_LAN=1` açıksa UYAP'a karşı
  sınırsız credential-stuffing proxy'sine dönüşür.
- **#10 Otomatik dosya çalıştırma** (`Panel/modules/logger_core.py:528-542`) — Logger'ın
  yönettiği kalıcı Chrome profili yalnızca `avukat.uyap.gov.tr`'ye kilitli değil; aynı profilde
  ziyaret edilen herhangi bir siteden inen dosya kullanıcı onayı olmadan `os.startfile()` ile
  otomatik açılıyor; ayrıca `download.suggested_filename` diğer tüm dosya-yazma uçlarının aksine
  `os.path.basename()` ile sanitize edilmiyor (Chromium'un kendi sanitizasyonu bunu telafi
  ediyor olabilir, doğrulanmadı).
- **#12 f-string SQL** (`db_baslat.py:131`, `sifirla_veritabani.py:131-133`) — `UYAP_DB_NAME`
  ortam değişkeni parametrize edilmeden SQL'e giriyor; tetiklemek zaten süreç ortam
  değişkenlerini değiştirebilecek düzeyde yerel ele-geçirme gerektirir, düşük gerçek risk ama
  savunma-derinliği eksik.
- **#13 `usage_logger.py`, kaldırılmış `_admin_ok`/`_admin_unauth`'u çağırıyor**
  (`vendor_deploy/usage_logger.py:326,333`) — Gerçek prod giriş noktası (`Dockerfile` CMD)
  `vendor_server.py` değil `usage_logger.py`; admin gate Basic-Auth'tan owner+TOTP'ye taşınırken
  bu dosyadaki referans güncellenmemiş. Sonuç fail-closed (500 hatası, veri sızmıyor) ama
  `/admin/usage` izleme panosu kullanılamaz durumda — anomali tespiti için güvenilen bir araç
  sessizce devre dışı.
- **#14 Oturum iptali yok** (`vendor_deploy/vendor_server.py:748-749,859-860,1826-1830`) —
  Çıkış ve parola değişikliği yalnızca istemci çerezini siliyor; imzalı jeton doğal süresi
  (`SESSION_TTL`, varsayılan 12 saat) dolana kadar geçerli kalıyor.

---

## 11. `chmod 600` Windows'ta sessizce hiçbir şey yapmıyor — **Düşük**

**Konum:** `models/uyapdata/settings.py:43`, `uyap_panel/web/uyap_web/settings.py:55`
(kod içindeki kendi yorumuyla teyit edildi: `# POSIX'te sahip-okuma; Windows'ta yok sayılır`).

`SECRET_KEY`'i `%LOCALAPPDATA%\UyapIcra\*_secret_key` dosyasına yazan kod `os.chmod(path, 0o600)`
çağırıyor ama bu POSIX'e özgü bir çağrı — asıl dağıtım platformu olan Windows'ta etkisi yok.
Koruma tamamen `%LOCALAPPDATA%\UyapIcra` dizininin miras aldığı ACL'ye kalıyor; kod bunu hiç
doğrulamıyor/sertleştirmiyor (`icacls` çağrısı yok).

**Öneri:** Windows'ta `icacls` ile dizine açıkça yalnızca-mevcut-kullanıcı ACL'si uygulayın ya da
en azından bu sınırlamayı yorum satırının ötesinde bir uyarı/log ile belirginleştirin.

---

## Canlı Üretim Problamaları (`https://www.cokluuyap.com`)

Sadece GET/OPTIONS ile, brute-force veya enjeksiyon denemesi yapılmadan:

- **Olumlu:** `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, hassas sayfalarda `Cache-Control: no-store` ve
  `X-Robots-Tag: noindex, nofollow` mevcut. CORS preflight'ta hiçbir
  `Access-Control-Allow-Origin` yansıtılmıyor. `.env`, `.git/config`, `.git/HEAD` gibi yollar
  404. `/admin` gerçekten kullanıcı adı+parola+TOTP isteyen bir giriş formu döndürüyor,
  kimlik doğrulama öncesi hiçbir veri sızdırmıyor.
- **#15 CSP/Permissions-Policy yok** — Sitede hiçbir yolda `Content-Security-Policy` veya
  `Permissions-Policy` başlığı dönmüyor. XSS'e karşı savunma-derinliği eksik (mevcut
  `html.escape()` disiplini birincil savunma, CSP ikincil bir katman olurdu).
- **#16 `robots.txt` hassas yolları listeliyor** — `/admin /owner /reset /ofis /giris
  /satin-al/sonuc` `Disallow` olarak listelenmiş; bu dosya herkese açıktır ve bu yolların
  varlığını/adını saldırganlara doğrudan veriyor. `/ofis` sayfası zaten `X-Robots-Tag: noindex`
  header'ı ile aynı amacı (arama motorlarından gizleme) header üzerinden de sağlıyor —
  bunun robots.txt'de tekrar ilan edilmesine gerek yok.

**Öneri (#15/#16):** Sıkı bir CSP ekleyin (en azından `default-src 'self'`); robots.txt'ten
hassas yol adlarını çıkarıp yalnızca `X-Robots-Tag` header'ına güvenin.

---

## 17. `vendor_deploy` içinde push edilmemiş değişiklik — bulgu değil, operasyonel not

`vendor_deploy/webapp/index.html`'de "Panel'i Aç" (`openPanel()`) özelliği için commit'lenmiş
ama `origin/main`'e push edilmemiş bir değişiklik tespit edildi (`git status`/`git diff
origin/main` ile teyit). Bu projenin daha önce yaşadığı "düzeltme kaynağa gitti ama
vendor_deploy'a push edilmedi, hiç yayına çıkmadı" durumunun tam tersi ama aynı riskli deseni —
burada kaybolmaması için not düşülüyor, karar kullanıcıya bırakılmıştır.

---

## Önceki Bulgular — Yeniden Doğrulama

`guvenlik_analizi.md`'deki (2026-06-29) 13 maddenin tamamı bu turda bağımsız olarak tekrar
kontrol edildi:

- **Gerçekten giderilmiş, kodda teyit edildi:** #2 (Host/Origin guard + yerel-jeton — ancak bu
  turda **yeni bir bypass** bulundu, bkz. bulgu #1 yukarıda), #4 (signaling allowlist), #5
  (IndexedDB cache oturum mührü), #7 (rate limiting/_LoginGuard), #8 (sıfırlama SMTP-bağımlı +
  min parola uzunluğu), #9 (DPAPI/PBKDF2+Fernet fail-closed), #10 (`--no-verify` opt-in), #11
  (`uretilmis_runner` yol sınırlaması), #12 (defusedxml + DTD reddi), gömülü PIN (`092291`)
  artık kaynak kodda yok.
- **Kısmen giderilmiş, aynı durumda:** #13 (`chrome_profile/` cache'leri — `.gitignore` +
  temizleme betiği var, `%LOCALAPPDATA%`'ya taşıma hâlâ ertelenmiş).
- **Hâlâ mevcut/dikkat gerektiren, ama zaten öyle işaretlenmişti:** #3 (Panel'de parola
  bellekte — bu turda #7 olarak tekrar listelendi çünkü hâlâ geçerli), #6 (Django
  DEBUG/SECRET_KEY — bu tur ayrıca doğruladı: artık ikisi de env-gated ve varsayılan güvenli).
- **Eski yedek dosyasındaki gömülü PIN** (`mts_takip_acan.py.bak_uyapbot`) artık dosya sisteminde
  yok (silinmiş) — kapanmış.

Sonuç: önceki turun disiplini gerçek — iddia edilen düzeltmelerin neredeyse tamamı kodda
doğrulanabiliyor. Bu turun yeni bulguları önceki turun kapsamadığı katmanlardan geliyor
(gömülü Postgres, WebRTC/relay veri yolu, vendor SaaS'ın owner/oturum-anahtarı zinciri, canlı
HTTP başlıkları).

---

## Genel Önceliklendirme

1. **Hemen:** Bulgu #1 (Origin: null bypass) — en düşük efor/en yüksek etki, tek fonksiyonluk
   bir düzeltme.
2. **Bu hafta:** #2 (path traversal — tek satır `os.path.basename` düzeltmesi), #3 (SESSION_SECRET
   fallback'i kaldır), #5 (CANONICAL_HOST'u zorunlu yap).
3. **Planlanabilir:** #4 (Postgres'e parola ekleme — kurulum akışına dokunuyor, daha çok efor),
   #6 (signaling'in oda-kabul mantığını ayrıca incele), CSP eklenmesi.
4. **Düşük öncelik / bilgi amaçlı:** geri kalan tüm maddeler.
