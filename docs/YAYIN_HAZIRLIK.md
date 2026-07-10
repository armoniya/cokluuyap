# Yayın Hazırlığı — Website, Üyelik, Owner (utku) ve DB Sıfırlama

> Tarih: 2026-07-02
> Kapsam: `Uyap Haricen Giriş/vendor_server.py`, `accounts.py`, `webapp/index.html`,
> kök `sifirla_veritabani.py`. Değişiklikler `vendor_deploy/` altına birebir kopyalandı.

## 1. İki veritabanı AYRIDIR (karışmaz)

| DB | Ne tutar | Nerede |
|----|----------|--------|
| **Hesap deposu (bizim)** | ofisler + kullanıcılar + siparişler + owner `utku` | `accounts.json` **veya** `DATABASE_URL` PostgreSQL `uyap_kv[k='accounts']` |
| **İcra verisi (kullanıcının)** | Django modelleri (icra dosyaları vb.) | gömülü PostgreSQL `uyap_icra` @ `%LOCALAPPDATA%\UyapIcra` |

İkisi hiçbir tabloyu paylaşmaz. `DATABASE_URL` **asla** `uyap_icra`'ya işaret etmemeli;
`vendor_server` açılışta bunu tespit ederse uyarır (`_warn_db_overlap`).

## 2. Website + Üyelik akışı

- **Tanıtım (hero):** `/` (webapp `index.html`) artık ürünü anlatan bir açılış + **Giriş Yap**
  ve **Üye Ol** butonlarıyla açılır. Giriş Yap mevcut login formunu açar (tünel/SW mantığı
  DEĞİŞMEDİ); Üye Ol `/uye-ol`'a gider.
- **Üye Ol (`/uye-ol`):** ofis adı, kullanıcı adı (çıplak), e-posta, telefon, parola (+tekrar).
  Tasarım giriş ekranıyla aynı dile sadıktır (`#0b0d12`/`#5b8cff`).
- **Model:** ödeme onaylı (mevcut sipariş akışı). Form ANINDA ofis oluşturmaz → **bekleyen
  sipariş** yaratır. Kullanıcının seçtiği parola **hash'lenerek** siparişte taşınır (düz metin
  ASLA saklanmaz); telefon da taşınır. Ödeme/onay sonrası owner panelinden
  **"Ödendi → Ofis Oluştur"** ile ofis, kullanıcının kendi parolasıyla provision edilir.
- Giriş: `kullanici@ofis_kodu` + parola (`/` üzerinden Giriş Yap).

## 3. Owner hesabı `utku` (admin yerine) + TOTP 2FA

Eski Basic-Auth `/admin` (UYAP_ADMIN_PASSWORD) **kaldırıldı**. Artık:

- `utku` bir **owner** rolündedir; ofis kodu GEREKMEZ, tüm platform yetkisine sahiptir,
  tünele katılmaz (silinemez/pasifleştirilemez).
- **Bootstrap:** sunucuyu `UYAP_OWNER_PASSWORD=...` ile bir kez başlatın. Owner yoksa oluşturulur
  ve **TOTP gizli anahtarı + otpauth URI konsola bir kez** yazılır → authenticator uygulamanıza
  (Google/Microsoft Authenticator, Authy…) ekleyin. Parola kaynağa GÖMÜLMEZ, düz saklanmaz.
- **Giriş:** `/owner/login` → kullanıcı (`utku`) + parola + 6 haneli TOTP kodu. Başarılıda
  imzalı `uyap_owner` çerezi (SameSite=Strict → CSRF savunması) kurulur; `/admin` açılır.
- **Güvenlik:** rate-limit (`_LoginGuard`), sabit-zaman + dummy-hash (enumeration/timing),
  TOTP `compare_digest` + ±1 pencere + **replay reddi** (`totp_last`).

### Ortam değişkenleri
| Değişken | Açıklama |
|----------|----------|
| `UYAP_OWNER_PASSWORD` | utku bootstrap parolası (zorunlu; yoksa `/admin` kapalı) |
| `UYAP_OWNER_USERNAME` | owner adı (varsayılan `utku`) |
| `UYAP_MIN_PASSWORD_LEN` | insan-seçimli parola min uzunluk (vars. 10) |
| `UYAP_SESSION_SECRET` | oturum imzası (deploy'lar arası kalıcılık) |
| `DATABASE_URL` | hesap deposu PostgreSQL (yoksa dosya) |
| ~~`UYAP_ADMIN_PASSWORD`~~ | **kaldırıldı** (yalnızca eski oturum-sırrı fallback'ı) |

## 4. Veritabanı sıfırlama — `sifirla_veritabani.py` (kök)

```
"Uyap Haricen Giriş\.venv\Scripts\python.exe" sifirla_veritabani.py            # KURU çalışma
"Uyap Haricen Giriş\.venv\Scripts\python.exe" sifirla_veritabani.py --sil -y   # sıfırla
   --sadece-hesap | --sadece-icra | --flush   (icra'yı DROP yerine manage.py flush ile)
```
- Hesap deposu: `accounts.json` (+ vendor_deploy) sil; `DATABASE_URL` varsa `uyap_kv` satırını sil.
- İcra: `uyap_icra` DROP + yeniden oluştur + migrate (varsayılan) ya da `--flush`.
- **.venv ile çalıştırın** (migrate Django + psycopg2 ister). Sıfırlama owner `utku`'yu da siler
  → sunucuyu tekrar `UYAP_OWNER_PASSWORD` ile başlatıp yeni TOTP anahtarını ekleyin.

## 5. Yayın öncesi kontrol listesi
1. `sifirla_veritabani.py --sil -y` (temiz başlangıç). ✔ (2026-07-02 çalıştırıldı)
2. Sunucuyu `UYAP_OWNER_PASSWORD` + (üretimde) `DATABASE_URL`, `UYAP_SESSION_SECRET`,
   `UYAP_SMTP_*` ile başlatın; konsoldaki TOTP anahtarını authenticator'a ekleyin.
3. `/owner/login` → utku ile gir → `/admin`. `/uye-ol` ile test üyeliği → provision → giriş.
