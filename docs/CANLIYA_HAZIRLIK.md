# Canlıya Hazırlık — Kendi Domain'imizle Tek Adresten Yayın

> Tarih: 2026-07-02
> Amaç: GoDaddy'den alınacak **`cokluuyap.com`** ile Render üzerindeki
> `vendor_server`'ı **tek bir resmî adresten** yayınlamak.
> Üyelik/owner/DB hazırlığı için bkz. [YAYIN_HAZIRLIK.md](YAYIN_HAZIRLIK.md);
> deploy temelleri için `Uyap Haricen Giriş/DEPLOY_VENDOR.md`.

Seçilen alan adı: **`cokluuyap.com`** (apex, kanonik) + **`www.cokluuyap.com`** (308 → apex).
Domain "çoklu uyap" aramasıyla birebir eşleşiyor (exact-match) — SEO fazı: Faz 6.

---

## Faz 0 — Alan adı: `cokluuyap.com` (GoDaddy)

**Karar verildi:** `cokluuyap.com`. "Çoklu UYAP" ana arama kalıbıyla birebir
eşleşen exact-match domain — rakipler (multiuyap.com, uyapplus.com,
uyapgiris.com, uyapcozumleri.com) bu kalıbı ancak içerikle yakalıyor.

- [x] `cokluuyap.com` SATIN ALINDI (2026-07-02). GoDaddy'nin ücretli eklentilerini (website
  builder, e-posta paketi vb.) kaldır; **WHOIS gizliliği** GoDaddy'de ücretsiz, açık kalsın.
- [ ] **Auto-renew'u hemen aç** (exact-match domain düşerse rakip kapar).
- [ ] Varsa bütçe: `cokluuyap.com.tr`'yi de savunma amaçlı al — GoDaddy `.tr`
  satmaz, TRABIS'e bağlı Türk operatörden alınır (isimtescil, natro, METUnic);
  sadece 301/308 yönlendirme olarak kullanılır.
- [ ] DNS yönetimi GoDaddy panelinde kalacak (nameserver taşımaya gerek yok).

**Kanonik adres:** apex (`cokluuyap.com`); `www` ona 308 ile akar
(`UYAP_CANONICAL_HOST` bunu sunucu tarafında otomatik yapar, aşağıda).

---

## Faz 1 — Render tarafını üretime hazırlama

- [ ] **Plan yükseltme (önemli):** kullanıcı kararı (2026-07-02): şimdilik `free`
  kalıyor (test/kurulum dönemi). **GERÇEK müşteri almadan önce** `render.yaml`'da
  `plan: starter` yap + panelden onayla — free uyur, müşteri ilk istekte ~50 sn bekler.
  (Env placeholder'ları `UYAP_OWNER_PASSWORD` + `UYAP_SESSION_SECRET` eklendi.)
- [ ] **Kalıcı hesap deposu:** `DATABASE_URL` (Neon/Supabase PostgreSQL) mutlaka
  tanımlı olsun — free disk'te `accounts.json` her deploy'da silinir.
  Bu DB, gömülü `uyap_icra` DB'sinden **ayrıdır**, asla aynı olmamalı
  (bkz. YAYIN_HAZIRLIK.md §1).
- [ ] **Tek deploy kaynağı:** yayına giden tek parça `Uyap Haricen Giriş/vendor_deploy/`
  klasörüdür (kendi git deposu var). Akış hep aynı kalsın:
  1. Değişiklik ana kopyada yapılır (`vendor_server.py`, `accounts.py`, `webapp/`),
  2. `cp` ile `vendor_deploy/` altına **birebir** kopyalanır,
  3. `vendor_deploy` içinden commit + push → Render `autoDeploy: true` ile kendisi çeker.
- [ ] Deploy sonrası health check yeşil mi bakın: `https://<app>.onrender.com/__app__/config.js`

---

## Faz 2 — Domain'i Render'a bağlama (DNS + TLS)

1. **Render panel:** servis → *Settings → Custom Domains → Add Domain* →
   `cokluuyap.com` ve `www.cokluuyap.com` ikisini de ekleyin.
   Render her biri için gereken DNS kaydını ekranda gösterir.
2. **GoDaddy DNS panelinde** (My Products → domain → DNS):

   | Tip | Host | Değer | Not |
   |-----|------|-------|-----|
   | A | `@` | Render'ın gösterdiği IP (genelde `216.24.57.1`) | apex için; GoDaddy apex'te CNAME/ALIAS desteklemez, A kaydı şart |
   | CNAME | `www` | `<app>.onrender.com` | Render'ın verdiği hedefi aynen yazın |

   GoDaddy'nin hazır gelen "Parked" A kaydını ve `_domainconnect` dışındaki
   gereksiz kayıtları silin.
3. **Doğrulama + TLS:** Render kayıtları görünce alanı doğrular ve Let's Encrypt
   sertifikasını **otomatik** üretir/yeniler — elle sertifika işi yok.
   DNS yayılımı GoDaddy'de genelde dakikalar sürer (TTL 600 seçin).
   > **2026-07-02 YAPILDI:** DNS + Custom Domains + TLS tamam. Render **www'yi
   > birincil** seçti (apex → www'ye 301 atıyor); kanonik adres bu yüzden
   > **`www.cokluuyap.com`** olarak benimsendi (plan apex idi — sapma bilinçli).
4. **Tekilleştirme:** Render env'e ekleyin:
   ```
   UYAP_CANONICAL_HOST=www.cokluuyap.com
   ```
   Sunucu tüm GET/HEAD gezinmelerini bu adrese 308 ile yönlendirir; böylece
   `www.` ve eski `*.onrender.com` adresleri kendiliğinden tek adrese toplanır.
   (Health-check, `/ws`, `/ice`, `/odeme/webhook` muaftır — deploy kırılmaz.)
5. Test: `https://cokluuyap.com`, `https://www.cokluuyap.com` ve
   `https://<app>.onrender.com` üçü de sonunda kanonik adreste açılmalı; kilit
   (TLS) geçerli, HSTS başlığı geliyor olmalı.

---

## Faz 3 — Kod/istemci tarafında adres güncellemeleri

Masaüstü/ofis tarafı varsayılan olarak eski onrender adresine bakıyor; domain
bağlanınca bunlar güncellenmeli:

- [x] `uyap_panel/core/config.py:28` + `uyap_app.py:64` + `uyap_app_config.json` →
  `DEFAULT_SERVER_URL = "wss://www.cokluuyap.com/ws"` (2026-07-02 yapıldı;
  `UYAP_SERVER_URL` env ile hâlâ ezilebilir). DİKKAT: apex 301 attığı için
  wss adresi **www** olmalı.
- [ ] Ofis ajanı başlatma komutları/kısayolları:
  `--signaling wss://www.cokluuyap.com/ws`
- [x] README/DEPLOY belgelerindeki örnek adresler (`DEPLOY_VENDOR.md`,
  `vendor_deploy/README.md`) www.cokluuyap.com'a çevrildi;
  index.html canonical/OG/JSON-LD de www (2026-07-02).
- [ ] Not: eski `*.onrender.com` adresi kapanmaz; `UYAP_CANONICAL_HOST` sayesinde
  eski linkler yeni adrese kendiliğinden yönlenir. Yine de mevcut ofis ajanları
  `/ws`'e onrender üzerinden bağlanmaya devam edebilir (WS muaf) — acele
  kesinti olmaz, kontrollü geçiş yapılır.

---

## Faz 4 — Ortam değişkenleri (Render panelden, repoya YAZILMAZ)

| Değişken | Durum | Not |
|----------|-------|-----|
| `UYAP_CANONICAL_HOST` | **yeni — zorunlu** | `cokluuyap.com` |
| `DATABASE_URL` | zorunlu | Neon/Supabase; hesap deposu |
| `UYAP_OWNER_PASSWORD` | zorunlu (ilk açılış) | utku bootstrap; TOTP anahtarı konsola bir kez yazılır |
| `UYAP_SESSION_SECRET` | zorunlu | deploy'lar arası oturum kalıcılığı |
| `UYAP_SMTP_HOST/USER/PASS` | önerilir | parola sıfırlama + lisans teslimi e-postaları |
| `UYAP_PROVISION_TOKEN` | ödeme entegrasyonunda | boşsa webhook kapalı (güvenli varsayılan) |
| `UYAP_PLAN_NAME`, `UYAP_PLAN_PRICE` | opsiyonel | landing'de fiyat/başlık |
| `UYAP_TURN_SECRET`, `UYAP_TURN_URLS` | sonraya | CGNAT/mobil veri müşterisi çıkarsa — kurulum: `docs/TURN_KURULUM.md` |

---

## Faz 5 — Yayın günü kontrol listesi

1. [ ] `sifirla_veritabani.py --sil -y` ile temiz başlangıç (test verisi kalmasın).
2. [ ] Render'da env değişkenlerini gir → deploy → konsoldaki **TOTP anahtarını
   authenticator'a ekle** (bir kez yazılır, kaçırma!).
3. [ ] Uçtan uca duman testi (hepsi `https://cokluuyap.com` üzerinden):
   - [ ] Açılış sayfası yeni tasarımla geliyor (panel paleti).
   - [ ] `/uye-ol` → test üyeliği → `/owner/login` (utku+TOTP) → `/admin` →
     "Ödendi → Ofis Oluştur" → `kullanici@ofis` ile giriş.
   - [ ] Ofis ajanı `wss://cokluuyap.com/ws`'e bağlanıyor, tarayıcıdan tünel açılıyor.
   - [ ] `/satin-al` ve claim sayfası yeni temayla açılıyor
     (CSS Python sabitinde — bu deploy'la birlikte zaten yansır).
4. [ ] `signaling_config.json` allowlist'i güncel mi (oda anahtarı = lisans)?
5. [ ] Eski onrender linki kanonik adrese 308 atıyor mu?

---

## Faz 6 — SEO (rakip taraması 2026-07-02'ye göre)

Rakipler: SHA Yazılım (multiuyap.com — "Uyap Server", "Uyap Kâtibim"),
UYAP Plus (uyapplus.com), uyapgiris.com (Chrome eklentisi), uyapcozumleri.com.
Hepsi aynı anahtar kelime kalıbını kullanıyor; biz de aynı aramaları hedefleyeceğiz
ama **spam backlink taktiklerine girmeden** (Çakmak Su örneği gibi alakasız
sitelerde içerik çoğaltma — Google bunu cezalandırabilir, uzak dur).

### Hedef anahtar kelimeler (Google SERP taraması 2026-07-02 ile doğrulandı)

**1. Ana kalıp (domain'imiz birebir karşılıyor):**
"çoklu uyap giriş programı", "uyap çoklu giriş", "uyap çoklu oturum (açma)",
"çoklu uyap kullanımı", "uyap birden fazla giriş/oturum", "uyap toplu giriş
programı", "uyap multi login" (SHA bu İngilizce kalıbı da hedefliyor).

**2. Hata aramaları (en değerli trafik — sorun yaşayan avukat o an müşteridir):**
- "UYAP 5 aktif oturum" hatası / "aktif 5 oturum hatası" (iki kelime sırası da aranıyor)
- "eş zamanlı sorgu hatası" / birebir hata metni: **"eş zamanlı olarak birden
  fazla sorgulama yapamazsınız"** (SSS'de tırnak içinde aynen geçir — SERP'te
  bu tam cümleyle neredeyse rakipsiz sonuç var, sadece SHA çıkıyor)
- "oturum limiti aşıldı"
- "UYAP başka bilgisayarda oturum açıldı" (sistem mesajının kendisi)
- "9. madde e-imza kilitlenmesi", "e-imza banlanması", "uyap 3 saat kilitlenme"
  (SSS'de mekanizmayı doğru anlat: farklı IP bloklarından eşzamanlı giriş →
  3 saat blok; tek e-imza+tek çıkış noktası bunu yapısal olarak önler)

**3. Ürün kategorisi adları (rakip ürün adı = kategori adı olmuş):**
"uyap server (programı)", "uyap toplu sorgu programı", "uyap sorgu programı",
"uyap yazılım çözümleri". "multiuyap"/"uyap katibim" rakip markaları — meta'ya
yazma, ama kıyas/alternatif içerik sayfası ("uyap server alternatifi") olur.

**4. Toplu işlem aramaları (ürünümüzde karşılığı VAR — MTS/XML/toplu sorgu):**
"uyap toplu takip açma", "MTS takip açılışı", "mts toplu takip açma programı",
"XML'den icra takibi açma", "uyap toplu dosya sorgulama", "SGK/MERNİS/EGM toplu
sorgu". Bu küme ikinci bir landing/özellik sayfası hak ediyor.

**5. Uzaktan çalışma açısı (rakiplerin zayıf olduğu boşluk):**
"uyap uzaktan erişim", "evden uyap girişi", "e-imza ofiste uzaktan çalışma",
"adliyeden uyap erişimi", "tablet/telefondan uyap". Ürünümüz tarayıcı tabanlı
(P2P tünel) — rakipler kurulum isteyen masaüstü programlar; bu farkı işle.

**6. Maliyet açısı (2024-07'den beri geçerli):** UYAP'ta aynı dosyada günde
5'ten fazla sorgu, sorgu başına 3 TL ücretli (Adalet Bakanlığı İİDB duyurusu).
Bizim SWR önbelleği tekrar sorguyu azaltıyor → "uyap sorgulama ücreti"
aramasına "önbellek sayesinde mükerrer sorgu ücreti ödemeyin" içeriği yazılır.

### Rakip fiyat istihbaratı (konumlandırma için)
- UYAP Plus: "5.700 TL'den başlayan fiyatlarla" (yıllık).
- SHA/Uyap Server: fiyat gizli, 1-99 kullanıcı dinamik tablo + **3 gün ücretsiz
  deneme** (deneme teklifi dönüşümde işe yarıyor — bizde de düşünülmeli).
- `UYAP_PLAN_PRICE` env'ini buna göre konumlandır.

### Rakip taktik istihbaratı
- SHA tek firma, **çoklu exact-match domain** stratejisi kullanıyor:
  multiuyap.com + uyapserver.com + uyapkatibim.com (her ürün-kelimeye ayrı site).
  cokluuyap.com bu oyunda en değerli kalıbı kapatıyor.
- Görünürlüklerinin çoğu **advertorial/basın bülteni ağı**: esgazete, nedenhaber,
  haberbodrum, ufukgazetesi, hukukihaber, parakazanmarehberi, hatta cakmaksu
  (su firması!). Aynı metni onlarca siteye basıyorlar. hukukihaber.net gibi
  gerçek hukuk medyasında tanıtım yazısı meşru ve değerli; alakasız sitelere
  kopya metin basmaksa riskli — yapma.
- Soru-cevap/forumlarda görünürler: myicra.com/forum, avukatlarasor.net —
  buralarda gerçek kullanıcı diliyle (satış dili değil) var olmak değerli.

### On-page (webapp/index.html — açılış sayfası)
- [x] `<title>`: "Çoklu UYAP Giriş Programı — 5 Aktif Oturum Sorununa Çözüm |
  cokluuyap.com"; `<meta name="description">` "5 aktif oturum" + "tek e-imza ile
  birden fazla kullanıcı" içeriyor. *(2026-07-02 yapıldı)*
- [x] `<h1>` hero başlığı "Çoklu UYAP Giriş Programı" oldu; bağlantı ekranı
  başlığı da "Çoklu UYAP". *(yapıldı)*
- [x] Open Graph + `lang="tr"` + canonical eklendi. *(yapıldı)*
- [x] SSS bölümü eklendi: hero kartında 5 kapalı `<details>` (5 aktif oturum,
  eş zamanlı sorgu — hata metni birebir tırnak içinde —, 9. madde, kurulum, P2P
  gizlilik) + head'de aynı içerikle `FAQPage` JSON-LD ve `SoftwareApplication`
  şeması. Overlay'e `overflow-y:auto` eklendi (kart uzayınca kaydırılır). *(yapıldı)*
- [x] İçerik dürüst: "tek e-imza üzerinden ekip erişimi" anlatılıyor, kural
  aşma/atlatma vaadi yok. *(yapıldı)*

### Teknik SEO (vendor_server tarafı)
- [x] `robots.txt` servis ediliyor: `/ofis /admin /owner /reset /satin-al/sonuc
  /giris` → Disallow + Sitemap satırı (taban adres `UYAP_CANONICAL_HOST`'tan,
  yoksa istek origin'inden). *(2026-07-02 yapıldı)*
- [x] `sitemap.xml`: `/`, `/uye-ol`, `/satin-al`. *(yapıldı)*
- [x] Özel sayfalara `X-Robots-Tag: noindex, nofollow` (_security_mw içinde:
  /ofis /admin /owner /reset /satin-al/sonuc /giris). *(yapıldı)*
- [x] sw.js allowlist'e `/robots.txt` + `/sitemap.xml` eklendi; vendor_deploy
  kopyaları (vendor_server.py, index.html, sw.js) cp ile birebir senkronlandı,
  yerel duman testi (8137) geçti. *(yapıldı)*
- [ ] Yayın sonrası: **Google Search Console** + Bing Webmaster'a domain'i ekle,
  sitemap gönder, "5 aktif oturum" sorgusunda konum takibi yap.

### İçerik (yayından sonra, aceleye gerek yok)
- [ ] Hata-odaklı 3-5 kısa makale/SSS sayfası (yukarıdaki hata aramaları birebir
  başlık olsun). Rakiplerin sayfaları şablon-kopya; özgün, gerçekten açıklayan
  içerik öne geçer.
- [ ] Baro/hukuk topluluklarında doğal tanıtım; satın alınmış alakasız backlink YOK.

---

## Faz 7 — Yayın sonrası

- [ ] **Uptime izleme:** UptimeRobot/Better Stack ile `https://cokluuyap.com/__app__/config.js`
  5 dk'da bir kontrol (ücretsiz katman yeter).
- [ ] **DB yedeği:** Neon/Supabase otomatik yedekleri açık mı kontrol et;
  ayda bir `uyap_kv` içeriğini elle dışa aktar.
- [ ] **Alan adı otomatik yenileme:** GoDaddy'de auto-renew açık + ödeme kartı
  güncel (alan adı düşerse her şey durur).
- [ ] E-posta gönderimi domain'den yapılacaksa SPF/DKIM kayıtlarını DNS'e ekle
  (SMTP sağlayıcısı değerleri verir); yoksa mailler spam'e düşer.
- [ ] İleride: TURN sunucusu (CGNAT müşterileri; adım adım rehber + doğrulama
  betiği hazır: `docs/TURN_KURULUM.md` + `turn_dogrula.py`), status sayfası,
  `.com.tr` yönlendirmesi.

---

## Sıra özeti

**cokluuyap.com'u al → Render Starter + env'ler → DNS bağla → `UYAP_CANONICAL_HOST` →
kod içindeki varsayılan adresleri güncelle → vendor_deploy senkron + push →
DB sıfırla + owner bootstrap → duman testi → SEO (title/meta/SSS/robots/Search Console) → izleme.**
Faz 2'ye kadar hiçbir kod değişikliği gerekmez; Faz 3'teki iki `DEFAULT_SERVER_URL`
düzeltmesi domain kesinleşince tek seferde yapılır.
