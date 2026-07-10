# Belge (Evrak) Önbellekleme Planı — Taslak

Tarih: 2026-07-10
Durum: **PLAN — henüz kod yazılmadı.**

## 0. Önce netleşmesi gereken iki şey

1. **Şu an ayrı bir "belge görüntüleme" uç noktası yok — DOĞRULANDI.**
   `Panel/modules/icra_dosyalarim.py` yalnız kapak künyesini listeliyor;
   `uyap_proxy.py` genel gövde-yeniden-yazan bir vekil; `uyap_core/mts/evrak.py`
   yalnız evrak *yükleme* şablonları içeriyor. `Belgelisteleme.PNG` ekran
   görüntüsü bunu doğruladı: kullanıcının gördüğü "Evrak" sekmesi UYAP'ın kendi
   native ekranı (tünel/proxy üzerinden canlı geziliyor), uygulamanın kendi
   sahip olduğu yapılı bir "evrak listesi / evrak içeriği" çağrısı yok.
   **Sonuç:** bu iş "var olan özelliğin önüne önbellek koymak" değil,
   "evrak-çekme özelliğini önbellekle BİRLİKTE inşa etmek."

   Ekrandan görülen liste alanları: **evrak adı/türü** (örn. "İcra Dairesi Genel
   Yazı", "Tensip zaptı", "Vekaletname"), **tarih** (gg/aa/yyyy), ve bazı
   kayıtlarda **ek sayısı** ("(1 ek)", "(2 ek)" — bir evrak kaydı altında birden
   çok ek dosya olabiliyor, genişletilebilir). Liste ile içerik ayrı çağrılar
   (bir kalem seçilene kadar sağ panel boş) — bu, plandaki "önce ucuz liste,
   sonra talep üzerine içerik indir" akışını doğruluyor. **Ekranda görünür bir
   evrak id'si yok** — gerçek kalıcı kimlik, bu listeyi getiren ağ isteğinin
   (muhtemelen bir `.ajx` uç noktası) JSON/XML gövdesinde olmalı, DevTools ile
   canlı bir oturumda incelenmeden görülemez (bkz. madde 2).

2. **Evrak kimliğinin kalıcılığı — DOĞRULANDI (2026-07-10, canlı test).**
   Chrome üzerinden gerçek bir UYAP oturumunda (127.0.0.1:8800 tüneli, "2026/88516
   İzmir Banka Alacakları İcra Dairesi" dosyası) `list_dosya_evraklar.ajx`
   (POST, gövde: `{dosyaId, pageNumber}`) uç noktası canlı yakalandı. Ofis
   ajanının kendi enjekte ettiği tarayıcı-içi SWR önbelleği (`UYAP_SWR_CACHE`
   IndexedDB — bkz. `[[proxy-swr-cache-oturum-kapsami]]`) devre dışı bırakılarak
   (kayıt silinip) aynı belge için art arda iki bağımsız arka-uç çağrısı yapıldı.
   Sonuç:
   - **`evrakId`, `dosyaId`, `ggEvrakId` HER ÇAĞRIDA DEĞİŞTİ** — aynı belge
     (aynı `birimEvrakNo: 5519483`, aynı "Kapalı Tebligat" kaydı) için iki farklı
     istekte üç alanın tamamı farklı opak string döndü (sabit bir önek + değişen
     kuyruk deseni — şifreli/nonce'lu bir token olduğuna işaret ediyor). Bu,
     `Dosya.dosya_id` için zaten bilinen "oturumluk" örüntüsünün evrak id'si için
     de geçerli olduğunu kanıtlıyor → **Plan A (evrak id'yi kalıcı anahtar olarak
     kullanmak) GEÇERSİZ.**
   - **`birimEvrakNo` (düz tamsayı, örn. `5519483`) HER İKİ ÇAĞRIDA DA AYNI
     KALDI.** Bu, `Dosya`'daki `(birim, yıl, sıra_no)` düz-alan kalıcılık
     örüntüsünün evrak tarafındaki karşılığı — birimin kendi verdiği sıralı
     belge numarası, şifreli bir oturum token'ı değil. **Bu, sha256 içerik
     hash'ine ihtiyaç duymadan kullanılabilecek temiz bir kalıcı anahtar
     adayı.** (bkz. §3, artık "Plan B" yerine tek plan.)
   - Yanıtın gerçek alan adları (varsayım değil, canlı yanıttan): `evrakId`,
     `dosyaId`, `ggEvrakId`, `birimEvrakNo`, `onaylandigiTarih`,
     `gonderenYerKisi`, `gonderenDosyaNo`, `gonderenSayi`,
     `sistemeGonderildigiTarih`, `tur`, `tip`, `aciklama`, `ekEvrakListesi`,
     `isYetkili`.

## 1. Amaç

Kullanıcı bir dosyanın bir evrakını (karar, tebligat, dilekçe, dayanak vb.) daha
önce görüntülemişse, tekrar istediğinde ofis bilgisayarı UYAP'a yeniden tam istek
atmasın; sunucuda/ofiste daha önce inmiş kopya varsa onu göstersin. UYAP'a yalnızca
"bu dosyada yeni evrak var mı" diye ucuz bir liste sorgusu atılsın; yeni bir evrak
varsa sadece o indirilsin. Kazanımlar:

- **Daha az UYAP trafiği** — aynı belge tekrar tekrar tam olarak çekilmez.
- **UYAP hata/kesinti durumunda çalışabilirlik** — UYAP yanıt vermese bile daha
  önce inmiş belge gösterilebilir (bayat olduğu açıkça belirtilerek).
- **Hız** — yerel/merkezi DB'den okuma, UYAP round-trip'inden çok daha hızlı.

## 2. Neden güvenli bir varsayım: evrak içeriği pratikte değişmez

Karar, tebligat, dilekçe gibi evraklar UYAP'a bir kez yüklendikten/oluşturulduktan
sonra içerik olarak değişmez (yeni bir versiyon geldiğinde yeni bir evrak kaydı
açılır, var olanın üstüne yazılmaz). Bu, cache'i **immutable/append-only** bir
model olarak tasarlamayı mümkün kılıyor: bir evrak bir kere indirildiyse süresiz
geçerlidir, periyodik "değişti mi" kontrolüne gerek yoktur — sadece "bu dosyada
daha önce görmediğimiz yeni bir evrak var mı" kontrolü yeterlidir. (İstisna
olabilecek "değişebilir" evrak türleri için §6'da bayrak öneriliyor.)

## 3. Veri modeli (yeni Django tablosu)

`models/icra_models/models.py` içine `Dosya`'ya bağlı yeni bir model:

```python
class Evrak(models.Model):
    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="evraklar")

    # KALICI anahtar — birimin verdiği sıralı belge no'su (düz int, şifreli değil).
    # UYAP'ın döndüğü evrakId/dosyaId/ggEvrakId OTURUMLUKTUR (canlı testte doğrulandı,
    # bkz. §0.2) — sadece o oturumda içerik indirmek için geçici kullanılır, DB'de
    # SAKLANMAZ.
    birim_evrak_no = models.PositiveIntegerField(db_index=True)

    evrak_turu = models.CharField(max_length=64, blank=True)   # "tur", örn. "Kapalı Tebligat"
    evrak_tip = models.CharField(max_length=16, blank=True)    # "tip", örn. "GDN"
    aciklama = models.CharField(max_length=500, blank=True)
    evrak_tarihi = models.DateTimeField(null=True, blank=True)  # "onaylandigiTarih"

    mime_turu = models.CharField(max_length=64, blank=True)
    boyut = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)  # bütünlük doğrulama + dosya adı

    dosya_yolu = models.CharField(max_length=500)  # diskteki cache dosyasının yolu
    degisebilir = models.BooleanField(default=False)  # §6

    indirilme_zamani = models.DateTimeField(auto_now_add=True)
    son_erisim_zamani = models.DateTimeField(auto_now=True)   # LRU temizlik için

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dosya", "birim_evrak_no"], name="uq_dosya_evrak"),
        ]
```

**Kalıcı anahtar:** `(dosya, birim_evrak_no)` — `Dosya`'nın `(birim, yıl, sıra_no)`
düz-alan kalıcılık deseninin evrak karşılığı. `sha256` artık anahtar değil,
yalnızca indirilen içeriğin bütünlük kontrolü ve önbellek dosya adı için tutulur.

**Ek (attachment) desteği:** `Belgelisteleme.PNG`'de görüldüğü gibi bir evrak
kaydı "(1 ek)", "(2 ek)" gibi alt evrak(lar) içerebiliyor (örn. "Vekaletname
03/07/2026 (2 ek)"). Model buna izin vermeli — `Evrak`'a self-FK:
`ust_evrak = models.ForeignKey("self", null=True, blank=True, related_name="ekler",
on_delete=models.CASCADE)`. Ana evrak ile ekleri ayrı `Evrak` satırları olur, aynı
`dosya`'ya bağlı, `ust_evrak` ile gruplanır.

## 4. Depolama: dosya + sadece metadata DB'de

Belgeler (PDF/UDF) DB'de BYTEA olarak değil, dosya sisteminde tutulmalı:

- Yol: `%LOCALAPPDATA%\UyapIcra\evrak_cache\<birim_id>\<yil>-<sira_no>\<sha256[:16]>.<uzanti>`
  (mevcut "Gömülü PostgreSQL" kuralıyla uyumlu: ASCII yol, LOCALAPPDATA altı).
- DB'de yalnız yol + metadata (`Evrak` satırı) tutulur.
- Bu, merkezi Postgres'i şişirmez ve mevcut "SQLite yerel + Postgres merkezi
  senkron mantıksız" önerisiyle çelişmez — çünkü burada senkronize edilen iki
  yazılabilir kopya yok, tek yön var: UYAP → cache.

## 5. Akış

**Dosya evrak listesi açıldığında** (kullanıcı bir dosyanın evrak sekmesine
girdiğinde):
1. Ofis ajanı UYAP'tan yalnızca **ucuz liste çağrısını** yapar (evrak id/tarih/tür/
   boyut listesi — içerik değil).
2. Bu liste, o dosya için DB'deki mevcut `Evrak` kayıtlarıyla karşılaştırılır.
3. Zaten cache'te olanlar "önbellekte" işaretlenir, olmayanlar "yeni" işaretlenir.
   **Otomatik toplu indirme yapılmaz** — kullanıcı hangi evraka tıklarsa yalnız o
   indirilir (disk şişmesin, gereksiz trafik olmasın).

**Kullanıcı bir evraka tıklayıp görüntülemek istediğinde:**
1. Liste çağrısından gelen `birimEvrakNo` ile `(dosya, birim_evrak_no)` üzerinden
   DB'de kayıt aranır. (Listedeki `evrakId`/`dosyaId`/`ggEvrakId` bu adımda henüz
   kullanılmaz — DB aramasında hiç yer almazlar, çünkü oturumluktur.)
2. **Varsa:** dosya diskten okunur, doğrudan gösterilir. UYAP'a hiç gidilmez.
   `son_erisim_zamani` güncellenir.
3. **Yoksa:** o anki liste yanıtındaki (oturumluk) `evrakId`/`ggEvrakId` ile
   UYAP'tan içerik indirilir → sha256 hesaplanır → diske yazılır → `Evrak`
   satırı `birim_evrak_no` ile oluşturulur → kullanıcıya gösterilir. (Bu id'ler
   yalnızca bu tek indirme çağrısında kullanılır, DB'ye yazılmaz — bir sonraki
   erişimde liste tekrar çekildiğinde muhtemelen farklı olacaklardır.)
4. **UYAP hata verirse** (oturum düşmüş, 5xx, timeout) ve o evrak zaten
   cache'teyse: cache'ten göster + üstte "Bu, ⟨tarih⟩ tarihinde inmiş bir
   kopyadır; UYAP şu an yanıt vermiyor" uyarısı. Cache'te yoksa mevcut hata
   davranışı aynen kalır.

## 6. Değişebilir evrak istisnası

Eğer ileride "taslak" veya durumu anlık değişen bir evrak türü ortaya çıkarsa
(örn. bir tebligatın "tebliğ edildi/edilmedi" durumu belge içeriğine gömülüyse),
`degisebilir=True` işaretlenir ve bu türler için her erişimde UYAP'tan hafif bir
"son güncelleme zamanı" kontrolü yapılıp cache'teki tarihle karşılaştırılır;
farklıysa yeniden indirilir. Bu, çoğunluk (immutable) evrak için hiç maliyet
getirmez, sadece azınlık için ekstra bir kontrol adımı ekler.

## 7. Temizlik / disk yönetimi (opsiyonel, sonraki faz)

- KAPALI (durum=1) dosyaların evrakları süresiz saklanabilir (küçük hacim).
- Disk sınırı aşılırsa `son_erisim_zamani`'na göre LRU temizlik.
- Kullanıcıya "önbelleği temizle" düğmesi (ayarlar panelinde).

## 8. Güvenlik notu

Evrak içerikleri kişisel veri (KVKK kapsamı). Cache dosyaları:
- Uygulama dışından erişilemeyecek bir dizinde (mevcut LOCALAPPDATA düzeni zaten
  bunu sağlıyor, iş istasyonu kullanıcı hesabına özel).
- Mevcut auth/CSRF/yerel-yetki-jetonu katmanlarından (bkz. `[[proxy-host-origin-guard]]`
  hafıza notu) geçerek servis edilmeli — cache bir "arka kapı" açmamalı.
- Şifreleme-at-rest bu fazın kapsamı dışında tutulabilir, ama not düşülsün: ileride
  istenirse dosya adı zaten sha256 (içerik tahmin edilemez), asıl risk diske
  fiziksel/başka-hesap erişimi.

## 9. Aşamalı uygulama sırası (öneri)

1. ~~**Faz 0 (ön koşul, doğrulama):** evrak id'sinin kalıcı olup olmadığını
   teyit et.~~ → **TAMAMLANDI (2026-07-10).** Kalıcı anahtar `birim_evrak_no`
   olarak kesinleşti (bkz. §0.2).
2. **Faz 1 (sıradaki adım):** `Evrak` modeli + migration; tekil evrak indirme
   akışına "önce cache'e bak" mantığı; UYAP hata → cache fallback.
3. **Faz 2:** Evrak listesi açıldığında "yeni var mı" karşılaştırması —
   gerçek uç nokta artık biliniyor: `list_dosya_evraklar.ajx` (POST,
   `{dosyaId, pageNumber}`), yanıt `tumEvraklar["<yıl>/<sıra_no>(<tür>)"]`
   altında dizi döner.
4. **Faz 3:** Temizlik/LRU, "önbelleği temizle" arayüzü.
5. **Faz 4 (opsiyonel):** Değişebilir-evrak kontrolü, şifreleme.

---

## Açık sorular (kullanıcıya)

- ~~Belge görüntüleme şu an gerçekten UYAP'ın kendi arayüzü tünel üzerinden mi
  gösteriliyor mu?~~ → **Evet, `Belgelisteleme.PNG` ile doğrulandı.**
- ~~Evrak id'sinin dosya içinde kalıcı olduğunu canlı bir sorgudan teyit
  edebilir misiniz?~~ → **Hayır, kalıcı DEĞİL — Claude in Chrome ile
  2026-07-10'da canlı test edildi ve kesinleşti (bkz. §0.2). Kalıcı anahtar
  olarak `birimEvrakNo` kullanılacak.**

Açık soru kalmadı — plan artık Faz 1'e (kod yazımına) geçmeye hazır. Onay
verirseniz bir sonraki adımda `Evrak` modelini + migration'ı yazabilirim.
