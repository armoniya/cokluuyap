# Güvenlik Giderme — Devam İş Listesi (#7–#13)

> Bu dosya, `docs/GUVENLIK_ANALIZI.md` raporundaki **kalan** bulguları yeni bir
> konuşma penceresinden sırayla ilerletmek için hazırlanmıştır. Her bulgu
> **tek tek** ele alınır, kullanıcı onayıyla bir sonrakine geçilir.

## Çalışma kuralları (DEĞİŞMEZ)
- **Sırayla** ilerle; her bulgu bitince kullanıcı onayı bekle.
- Tüm iletişim ve kod yorumları **Türkçe**.
- **Görsel Birleştirme Kuralı:** modül birleştirirken yalnızca görsel sarmala;
  mantığa/tekniğe dokunma, MTS/XML ayrı kalır.
- PIN'ler kullanıcı girişinden veya `UYAP_PIN` ortam değişkeninden gelir; **asla
  kaynağa gömülmez**. Projede canlı tam yetkili UYAP e‑imza oturumu var → kimlik/oturum
  yüksek değerli.
- `sgk_sorgu_gui.py` ve benzeri motorlar **AYNEN değiştirilmez** orijinaller.
- Orijinal akışlara mimari/mantık/UX değişikliğini tek taraflı yapma.
- **Secure by default:** gevşetme açık bayrakla; varsayılan kısıtlayıcı.

## Tamamlananlar
- #1–#6 giderildi. En son **#6** (Django `DEBUG`/`SECRET_KEY`) bitti — bkz. rapor
  "Giderildi (2026-06-29)" blokları ve hafıza dosyaları.

---

## #7 — Brute‑force koruması yok + kullanıcı adı sızdırma · **Orta** · ⬜ BEKLİYOR
**Konum:** `vendor_server.py` (`/ws`, `/api/office`, `/ofis/login`, `/admin`, `/api/reset`),
`accounts.py:391-412` (`authenticate`), `accounts.py:431` (reset).

**Sorun:**
- Giriş uçlarında hız sınırlama / hesap kilitleme yok → parola brute‑force'a açık.
- `authenticate` ve `request_password_reset` "Kullanıcı bulunamadı" ↔ "Parola hatalı"yı
  ayırıyor → kullanıcı adı enumeration. Reset, var olmayan kullanıcıyı açıkça belirtiyor.
- `/admin` ve `/ofis/login` denemelerinde gecikme/limit yok.

**Yapılacak:**
- IP+kullanıcı bazlı hız sınırlama + üstel geri çekilme (exponential backoff).
- Giriş ve sıfırlama yanıtlarını **tek tip** yap ("kullanıcı adı veya parola hatalı" /
  "tanımlıysa bağlantı gönderildi") — varlık bilgisi sızdırma.
- Otomatik üretilen parolalar güçlü (`token_urlsafe`); kullanıcı/master parolaları için
  minimum güç politikası (bkz. #8).

---

## #8 — Sıfırlama jetonu log/URL'e düşüyor; zayıf parola izni · **Orta** · ⬜ BEKLİYOR
**Konum:** `vendor_server.py:90-112` (`_send_email` test modu), `:978` (`reset_get` GET token),
`accounts.py:471` (`len(new_password) < 4`).

**Sorun:**
- SMTP yoksa (`MAIL_ENABLED=False`) sıfırlama bağlantısı **konsola yazılıyor** + `LAST_TEST_MAIL`'de
  tutuluyor → logu gören hesap devralır. Yanlışlıkla üretimde kritik.
- Jeton **GET URL'inde** (`/reset?token=...`) → proxy/erişim logu, tarayıcı geçmişi, Referer'la sızar.
- Yeni parola minimum **4 karakter** — çok zayıf.

**Yapılacak:**
- Üretimde SMTP zorunlu; yapılandırılmamışsa sıfırlama akışını devre dışı bırak, jetonu loglama.
- Jetonu POST gövdesinde işle; kullanımdan sonra hemen geçersiz kıl (zaten tek kullanımlık),
  logda maskeley/maskele.
- Minimum parola uzunluğunu ≥ 10–12 karaktere çıkar.

---

## #9 — Windows harici ortamda parola/PIN düz metin saklanıyor · **Orta** · ⬜ BEKLİYOR
**Konum:** `uyap_panel/core/config.py:66-93` (`encrypt_secret`/`decrypt_secret`).

**Sorun:** DPAPI yalnızca Windows. `IS_WINDOWS` değilse `encrypt_secret` düz metni olduğu gibi
döndürüyor → `uyap_app_config.json`'a UYAP parolası + e‑imza PIN düz yazılıyor. DPAPI hatasında
da düz metne düşüyor (`except: return plain`).

**Yapılacak:**
- Windows dışında OS keyring (`keyring`) veya parola tabanlı şifreleme; sessizce düz metne düşme,
  en azından kullanıcıyı uyar.
- DPAPI başarısızlığında parolayı **kaydetme** (fail‑closed).
- Config dosyası izinlerini kısıtla.

---

## #10 — `--no-verify` ile UYAP TLS doğrulaması kapatılabiliyor · **Düşük‑Orta** · ⬜ BEKLİYOR
**Konum:** `uyap_proxy.py:1311` (`--no-verify`), zincirde `verify_ssl=not no_verify`.

**Sorun:** Varsayılan kapalı (iyi) ama açıkken UYAP'a sertifika doğrulaması devre dışı → MITM
(yargı trafiği için ciddi). Kalıcı kullanım cazip.

**Yapılacak:** Yalnızca geliştirme için bırak, kullanıldığında belirgin uyarı bas; üretim
derlemesinde tamamen kaldırmayı değerlendir.

---

## #11 — Logger üretilen `*_core.py`'ı import edip çalıştırıyor · **Düşük (yerel RCE)** · ⬜ BEKLİYOR
**Konum:** `Panel/modules/uretilmis_runner.py:33` `importlib.import_module(f"modules.{core_modul}")`,
`logger_core.py` (`modul_taslagi_uret`/`oturum_akisi_uret` → `modules/` altına `.py` yazar).

**Sorun:** Logger, kayıtlı oturumdan kod üretip `modules/` altına yazıyor; runner import ederek
**çalıştırıyor**. `modules/`'a yazabilen saldırgan/bozuk üretim verisi keyfi kod çalıştırır. Etki
yerel; "mağaza/eklenti" uzaktan indirmeye evrilirse **tedarik zinciri RCE'sine** yükselir
(bkz. App Store hedefi, `magaza_core.py`).

**Yapılacak:**
- Üretilen modül adlarını/yollarını katı doğrula (yol gezinmesi, beklenen şablon).
- Uzaktan eklenti gelecekse: imzalı paket, bütünlük (hash/imza), mümkünse sandbox/yetki kısıtı.

---

## #12 — XML ayrıştırma entity expansion DoS · **Düşük** · ⬜ BEKLİYOR
**Konum:** `Uyap Haricen Giriş/uyap_core/mts/parse.py:20`,
`Dosya Açılış/MTS Takip Açılış/mts_donusum.py:26` (`xml.etree.ElementTree`).

**Sorun:** Stdlib `ElementTree` XXE çözmez ama "billion laughs" iç‑varlık genişlemesi DoS'una
açık olabilir. Kullanıcı XML'i (takip dosyası) işlendiği için kötü dosya süreci kilitleyebilir.

**Yapılacak:** Güvenilmeyen XML için `defusedxml`; en azından boyut/derinlik sınırı.
**NOT:** MTS/XML mantığına dokunma kuralı gereği yalnızca ayrıştırıcı sertleştir, akışı bozma.

---

## #13 — Repo içinde `chrome_profile/` cache'leri ve artefaktlar · **Düşük‑Orta** · ⬜ BEKLİYOR
**Konum:** `Panel/modules/logger_data/chrome_profile/...`, `Logger/chrome_profile/...`,
`Sorgu/SGK Sorgu/chrome_profile/...`, `Uyap Haricen Giriş/*/static_cache/...`.

**Sorun:** Tarayıcı profil/cache dizinleri proje ağacında. UYAP'tan çekilmiş sayfa/yanıt verisi
(kişisel/dosya verisi) + oturum artefaktı içerebilir. Yedek/paylaşım/sürüm kontrolüne girerse
veri sızıntısı.

**Yapılacak:** Bu dizinleri `%LOCALAPPDATA%`'ya taşı, `.gitignore`'a ekle, periyodik temizle,
paylaşımdan önce sil.

---

## Her bulgu bitince yapılacak rutin
1. `docs/GUVENLIK_ANALIZI.md` → ilgili satırı "giderildi" işaretle + "**Giderildi (TARİH):**" bloğu ekle.
2. Bu dosyada (`GUVENLIK_GIDERME_DEVAM.md`) ilgili başlığın ⬜ → ✅ yap.
3. Hafıza dosyası oluştur (`memory/`), `MEMORY.md` indeksine satır ekle.
4. Mümkünse sözdizimi/çalışma doğrulaması yap, sonra kullanıcıya raporla ve onay bekle.
