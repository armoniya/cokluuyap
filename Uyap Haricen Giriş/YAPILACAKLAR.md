# UYAP — Yapılacaklar / Bekleyen İşler

Bu dosya, tamamlanan özelliklerin **eksik kalan / test aşamasında bırakılan** kısımlarını not eder.

## 1. E-posta gönderici (SMTP) kurulumu — ŞU AN TEST MODU ⚠️

Parola sıfırlama (`/reset`, `/api/reset`) akışı **kodda hazır ve çalışıyor**, ancak gerçek e-posta
**gönderilmiyor**. Gönderici hesabı henüz kurulmadı; `vendor_server.py` içindeki `_send_email`
SMTP env değişkenleri yoksa **TEST modu**na düşer: maili göndermez, içeriği (sıfırlama bağlantısı
dahil) sunucu günlüğüne (Render Logs) yazar ve `LAST_TEST_MAIL`'e koyar.

### Gerçek gönderime geçmek için
Bir gönderici e-posta hesabı belirleyip (örn. Gmail "uygulama parolası", ya da SendGrid/Mailgun/
Brevo SMTP), Render → uyap-vendor → **Environment**'a şu değişkenleri ekle:

| Env | Açıklama | Örnek |
|-----|----------|-------|
| `UYAP_SMTP_HOST` | SMTP sunucusu | `smtp.gmail.com` |
| `UYAP_SMTP_PORT` | Port (STARTTLS) | `587` |
| `UYAP_SMTP_USER` | Gönderici kullanıcı/e-posta | `bildirim@buronuz.com` |
| `UYAP_SMTP_PASS` | SMTP parolası / uygulama parolası | `****` |
| `UYAP_SMTP_FROM` | "Kimden" adresi (boşsa USER) | `UYAP <bildirim@buronuz.com>` |

Üçü (`HOST`, `USER`, `PASS`) dolunca `MAIL_ENABLED=True` olur ve `_send_email` gerçek gönderir.
Kod değişikliği GEREKMEZ.

### Gönderici kurulduktan sonra test
1. `/reset` → bir kullanıcı adı/e-posta gir → mail kutusuna bağlantı düşmeli.
2. Masaüstü giriş ekranı → "Parolamı unuttum" → aynı akış.
3. Bağlantı **1 saat** geçerli (token süresi `accounts.request_password_reset` içinde).

## 2. Reset jetonu kalıcılığı (küçük)
Sıfırlama jetonları şu an **bellek içinde** (`AccountStore.reset_tokens`). Sunucu yeniden
başlarsa (deploy/restart) **bekleyen** sıfırlama bağlantıları düşer; kullanıcı tekrar talep eder.
Kısa ömürlü (1 saat) olduğundan kabul edilebilir. İstenirse DB'ye (uyap_kv) taşınabilir.

## 3. Güvenlik incelemesi (ertelendi — kullanıcı kararı)
"Siber güvenlik kısmına sonra bakacağız, çok hassas veri var." Bu turda eklenenler için
ileride gözden geçirilecek başlıklar:
- Sıfırlama talebinde **kullanıcı sayımı (enumeration)**: şu an "kullanıcı bulunamadı" / "e-posta
  tanımlı değil" gibi açık mesajlar dönüyor (büro içi kullanım için kasıtlı, yardımcı olsun diye).
  Halka açık sızdırma kaygısı olursa genel "talep alındıysa mail gönderildi" mesajına çevrilebilir.
- Reset **hız sınırı (rate limit)**: aynı kullanıcıya art arda talebe sınır yok.
- E-posta içeriği düz metin; SPF/DKIM gönderici tarafında ayarlanmalı (spam'e düşmesin).

## 4. UDF e-imza köprüsü (sürükle-bırak) — KARTLA TEST EDİLMELİ ⚠️

Sağ altta HER ZAMAN duran bir alan: kullanıcı `.udf` / `.doc` / `.docx` / `.pdf` dosyasını
sürükler ya da tıklayıp seçer → ofise gider → ofis gerekiyorsa UDF'ye çevirip e-imzayla
imzalar (headless) → imzalı `.udf` geri gelir → UYAP'ta dosya yükleme alanına tıklayınca
otomatik eklenir (DataTransfer). Kod hazır ve mock'la uçtan uca test edildi:
- Sunucu: `uyap_core/udf_signer.py` (`sign_document`: .udf/düz-XML olduğu gibi; word/pdf →
  `udf_converter.py` ile content.xml'e çevir → paketle → imzala). İMZA = **PyKCS11 + `cades.py`**
  (`UDF imzalayan/` klasöründeki KANITLI kod; UYAP CAdES-BES detached CMS). Kart DLL'i otomatik
  bulunur (akisp11.dll/etpkcs11.dll; UYAP_PKCS11_LIB ile geçersiz kılınır). PIN girişten gelir,
  sertifikayı kart seçer. (ArkSigner :5975 yolu BIRAKILDI — profil kabulü belirsizdi.)
  cades zinciri openssl `cms -verify` ile doğrulandı. `jobs.py` → `POST /__uyap_agent__/sign_udf?name=<ad>`
  kontrol-düzlemi ucu (hem WebRTC tüneli hem LAN'da çalışır).
- Dönüştürme bağımlılıkları: `requirements-convert.txt` (python-docx, pypdf, Pillow, PyMuPDF).
  Eski binary `.doc` için ayrıca MS Word + pywin32 gerekir.
- İstemci: `uyap_proxy.py` enjekte JS — sağ altta AÇILIR/KAPANIR (küçük 🖋️ buton → panel)
  sürükle-bırak alanı. Dosya bırakılınca/seçilince ÖNCE "E-imzala / Vazgeç" ONAYI sorulur
  (otomatik imzalama yok). İmzalanınca dosya yükleme alanına tıklayınca `DataTransfer` ile
  otomatik dolar. Onay/sonuç beklerken panel kapanmaz (otomatik indir penceresinde de açık kalır).
  AYRICA UYAP'taki indirme bağlantıları yakalanıp (iframe'den ham fetch) aynı onay akışına
  sokulur → "dosya indiğinde imzalayayım mı?" sorusu.

**ÇÖZÜLEN — "Geçersiz UDF: ZIP açılamadı":** `_diag()` teşhisi gösterdi ki UYAP'ın indirdiği
`.udf` aslında ZIP değil DÜZ `content.xml`'di (ilk baytlar `<?xml`). İstemci/transport bozmuyordu.
`sign_udf_bytes`/`sign_document` artık hem ZIP hem düz-XML gövdeyi kabul ediyor. `_diag()`
hata mesajlarında kalmaya devam ediyor (ileride farklı bir bayt sorunu olursa).

**Doğrulanması gereken (gerçek kart gerekir):** Kartla üretilen imzalı `.udf`'in CANLI UYAP'a
yüklenip kabul edilmesi. İmza motoru artık `UDF imzalayan/`'daki KANITLI PyKCS11+`cades.py`
(kullanıcı onayladı: "gayet güzel imzalanıyordu") ve cades çıktısı openssl `cms -verify` ile
doğrulandı; dolayısıyla yüksek olasılıkla sorunsuz. Kart PIN'i hatalıysa / kart kilitliyse
açık Türkçe hata döner.

## 5. Tamamlananlar (referans)
- Admin panelinden ofise **manuel kullanıcı atama** (`/admin/adduser`) + master e-posta alanı.
- Kullanıcılarda **e-posta** alanı; ofiste **reset_to_master** politikası (master'a / kullanıcıya).
- `/ofis` paneli: üye e-postası, "Ayarlar" (kendi e-postam + politika), e-posta sütunu.
- Masaüstü "Kullanıcılar" sekmesi: e-posta ekleme/atama, ayarlar (kendi e-postam + politika combobox),
  giriş ekranında "Parolamı unuttum".
- `/reset` (talep + jetonla yeni parola) ve `/api/reset` (masaüstü). SW `/reset`'i tünelden muaf.
