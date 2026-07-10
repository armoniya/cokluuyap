# Çoklu Yargı Türü — Seçici Senkronizasyon Planı — Taslak

Tarih: 2026-07-10
Durum: **PLAN — henüz kod yazılmadı.**

## 0. İstek

Kullanıcı hangi yargı türü + yargı birimi (mahkeme türü) kombinasyonlarının
senkronize edileceğine kendi karar verebilsin (örn. yalnız Ceza-Ağır Ceza,
Hukuk-Asliye Ticaret, İcra-Banka Alacakları İcra Dairesi). Program ilk
bağlantıda Yargı Türü/Birimi/Mahkeme referans verisini ve tarafları çeksin.
Dosyalar açılış tarihi + dosya türü (Esas, Değişik İş, Tereke, Talimat vb.)
dahil bu değerlere göre listelensin ve filtrelenebilsin. "Dosya Görüntüle"
tıklandığında "Dosya Bilgileri" sekmesindeki tablo verileri de kaydedilsin.

Bu, mevcut `icra_core.py`'nin **yalnızca İcra'ya sabitlenmiş** olmasından
(`birimTuru2="1101"`, `birimTuru3="2"` hardcoded — bkz. madde 2) çok daha
geniş bir kapsam: Belge Önbellek planından (Faz 1 tamamlandı) ayrı, paralel
bir iş.

## 1. Canlı doğrulanan gerçek veri (Chrome, 2026-07-10, aynı teknik: SWR
IndexedDB cache okuma — `[[proxy-swr-cache-oturum-kapsami]]`)

### 1.1 Yargı Türü kodları (`yargiTuru` — sabit, küçük liste)

`yargiBirimleriSorgula_brd.ajx`'i besleyen dropdown'da **hiçbir arka-uç
çağrısı olmadan** 8 seçenek listeleniyor — bu, Yargı Türü'nün ön yüzde SABİT
bir enum olduğunu gösteriyor (Birim/Mahkeme gibi büyüyen bir referans tablosu
DEĞİL). Her seçenek seçildiğinde tetiklenen `yargiBirimleriSorgula_brd.ajx`
isteğinin gövdesinden (`{"yargiTuru": "<kod>"}`) kodlar doğrulandı:

| kod | Yargı Türü | Yargı Birimi alt-listesi var mı |
|---|---|---|
| 0  | Ceza | Evet |
| 1  | Hukuk | Evet |
| 2  | İcra | Evet (tek: İCRA DAİRESİ) |
| 3  | Cbs | Hayır (bunun yerine "İl" alanı) |
| 6  | İdari Yargı | Evet |
| 11 | Satış Memurluğu | Evet (tek: SATIŞ MEMURLUĞU) |
| 25 | Arabuluculuk | Evet |
| 26 | Tazminat Komisyonu Başkanlığı | Evet (tek: TAZMINAT KOMISYONU) |

**Sonuç:** `Dosya`/arama katmanında Yargı Türü basit bir
`models.IntegerChoices` olarak modellenmeli — "ilk bağlantıda çek" listesi
BUNA gerek yok (statik sabit).

### 1.2 Yargı Birimi kodları (`yargiBirimleriSorgula_brd.ajx` yanıtı — GERÇEK
büyüyen referans veri, "ilk bağlantıda çek" burada anlamlı)

Yanıt şekli: `[{"altSistKodu": -1, "tablo": "<kod>", "kod": "<AD>"}, ...]`.
`tablo` alanı, aramada `birimTuru2` parametresi olarak kullanılan koddur.
Örnekler (tam liste canlı çekilip cache'lenmeli, burada sadece doğrulananlar):

- Hukuk (1): `0920` Asliye Hukuk, **`0902` Asliye Ticaret Mahkemesi**, `0926`
  Aile, `0904` Sulh Hukuk, `0925` İcra Hukuk, `0908` İş Mahkemesi, vb.
- Ceza (0): `0921` Asliye Ceza, `0901` Ağır Ceza, `0931` Sulh Ceza
  Hakimliği, vb.
- İcra (2): `1101` İcra Dairesi (tek).
- İdari Yargı (6): `0917` Bölge İdare, `0919` Vergi, `0918` İdare Mahkemesi.
- Arabuluculuk (25): `6701` Arabuluculuk Merkezi, `6702` Arabuluculuk Daire
  Başkanlığı.

### 1.3 Mahkeme listesi — zaten bilinen uç nokta, GENELLENDİ

`avukat_mahkemeleri_sorgula.ajx` (POST `{yargiTuru, yargiBirimi, dosyaKapaliMi}`)
zaten `icra_core.birim_listesi_getir`'de kullanılıyordu (yalnız icra için,
sabit `yargiTuru=2, yargiBirimi=1101`). Canlı testte AYNI uç nokta
`{"yargiTuru":"1","yargiBirimi":"0902", ...}` ile İzmir/Adana Asliye Ticaret
Mahkemeleri listesini döndürdü (`birimAdi`, `birimId`). **Bu uç nokta zaten
yargı türü/birimi bağımsız, genel amaçlı** — icra_core'daki mevcut
`birim_listesi_getir` fonksiyonu neredeyse birebir yeniden kullanılabilir,
sadece `yargiTuru`/`yargiBirimi` parametreleri sabit yerine değişken olmalı.

### 1.4 Dosya arama uç noktası — AYNI uç nokta, GENELLENDİ (kritik bulgu)

`search_phrase_detayli.ajx` (icra_core.py'de yalnız icra için kullanıldığı
sanılan uç nokta) **Hukuk için de aynı şekilde çalışıyor**, sadece
`birimTuru2`/`birimTuru3` değerleri değişiyor:

```
İcra:  {"dosyaDurumKod":0,"pageSize":500,"pageNumber":1,"birimId":"","birimTuru2":"1101","birimTuru3":"2"}
Hukuk: {"dosyaDurumKod":0,"pageSize":500,"pageNumber":1,"birimId":"1034210","birimTuru2":"0902","birimTuru3":"1"}
```

`birimTuru2` = Yargı Birimi kodu (§1.2 `tablo`), `birimTuru3` = Yargı Türü
kodu (§1.1). **Bu, `icra_core.py`'nin İcra'ya özgü olmadığını, sadece iki
sabitin (`BIRIM_TURU2`, `BIRIM_TURU3`) hardcoded olduğunu kanıtlıyor** — alt
yapı (sayfalama, taraf varyantları, `X-Uyap-Read` başlığı, DB fallback) genel.

### 1.5 Yanıt kaydının gerçek alanları (`search_phrase_detayli.ajx`, Hukuk
örneği — kritik bulgu: **`dosyaDurumKod`/`dosyaTurKod` ikili DEĞİL**)

```json
{"dosyaId":"...", "dosyaNo":"2026/1755", "dosyaDurumKod":7, "dosyaDurum":"Karara Çıkmış",
 "dosyaTurKod":14, "dosyaTur":"Hukuk Değişik İş Dosyası",
 "dosyaAcilisTarihi":{"date":{"year":2026,"month":7,"day":6},"time":{...}},
 "birimAdi":"İzmir 6. Asliye Ticaret Mahkemesi","birimId":"1034210",
 "birimTuru1":"09","birimTuru2":"0902","birimTuru3":"0992",
 "isDavaDosyasiAcilmisMi":false, "isSorusturmaDosyasiIncelemeTalebiKabulEdilmis":false, "isNew":true}
```

Aynı sorguda gözlenen `dosyaDurumKod` değerleri: **`0`=Açık, `7`=Karara
Çıkmış, `29`=İstinafta** (yalnız 51 kayıtlık tek bir mahkeme örnekleminden —
kesin liste değil, bkz. §5 açık sorular). `dosyaTurKod` değerleri: **`14`=
Hukuk Değişik İş Dosyası, `15`=Hukuk Dava Dosyası**.

**Mevcut `models/icra_models/ingest.py` (`dosya_kunyesi_kaydet`) zaten bu ham
UYAP kodlarını OLDUĞU GİBİ `durum_kod`/`tur_kod`'a yazıyor** (`Dosya.Durum`/
`Dosya.Tur` enum'larından geçirmeden). Yani **kırılma riski YOK** — DB'de
zaten gerçek UYAP kodları duruyor; tek eksik olan `Dosya.Durum`/`Dosya.Tur`
`IntegerChoices` etiketlerinin bu gerçek değerleri (0/1/7/14/15/29/...) içerecek
şekilde genişletilmesi (yalnızca `get_..._display()` okunabilirliği için,
veri katmanında DEĞİŞİKLİK gerekmiyor).

### 1.6 `Birim.turu1/turu2/turu3` — anlam çakışması ÇÖZÜLDÜ (kod okunarak,
tahmin edilmeden)

`ingest.py`'deki mevcut kod, `Birim.turu1/turu2/turu3` alanlarını YANIT
KAYDININ kendi `birimTuru1/2/3` alanlarından dolduruyor (örn. icra için
`"11"/"1101"/"1199"`) — bunlar birimin KENDİ sınıflandırma kodları, arama
sorgusundaki `birimTuru3` (yargı türü, örn. `"2"`) ile **AYNI ALAN ADINI
paylaşan ama anlamca FARKLI bir değer**. Kanıt: Hukuk örneğinde sorgu
`birimTuru3="1"` gönderildi ama dönen kaydın kendi `birimTuru3` alanı
`"0992"` idi — ikisi uyuşmuyor çünkü ayrı kavramlar.

**Sonuç:** Mevcut `Birim.turu2` alanı zaten "Yargı Birimi" (mahkeme türü)
kodu ile aynı değeri taşıyor (icra için `1101`, hukuk/asliye ticaret için
`0902`) — bu alan YENİDEN KULLANILABİLİR, değiştirilmesine gerek yok. Ama
**"Yargı Türü" (0/1/2/3/6/11/25/26) için `Birim` üzerinde hiçbir alan yok** —
arama/filtreleme için YENİ bir `yargi_turu` alanı eklenmeli (§2).

## 2. Şema değişiklikleri (öneri — kod yazılmadı, tartışmaya açık)

### 2.1 `Birim`'e yeni alan

```python
yargi_turu = models.PositiveSmallIntegerField(
    "Yargı Türü Kodu", null=True, blank=True, db_index=True)  # §1.1 kodu
```

`turu1/turu2/turu3` AYNEN kalır (zaten doğru anlamı taşıyor, §1.6).

### 2.2 `Dosya.Durum` / `Dosya.Tur` enum'larının genişletilmesi

Şu an: `Durum = {ACIK:0, KAPALI:1}`, `Tur = {ESAS:0, TALIMAT:1}` — yalnız
icra'ya yetiyordu. Gerçek gözlenen değerler çok daha geniş (§1.5). **Veri
katmanında kırılma yok** (§1.5) — yalnızca `IntegerChoices` listesine gerçek
kodlar eklenmeli. **Ama tam liste henüz bilinmiyor** (yalnız 3 durum + 2 tür
kodu canlı doğrulandı) — bkz. §5.

### 2.3 Yeni referans tabloları (Yargı Birimi + Mahkeme önbelleği)

```python
class YargiBirimi(models.Model):
    """§1.2 — avukat_mahkemeleri_sorgula.ajx için gereken (yargiTuru, tablo) çifti."""
    yargi_turu = models.PositiveSmallIntegerField(db_index=True)
    kod = models.CharField(max_length=8)          # "tablo", örn. "0902"
    ad = models.CharField(max_length=120)          # "kod" (UYAP'ın adlandırması ters), örn. "ASLİYE TİCARET MAHKEMESİ"
    class Meta:
        constraints = [models.UniqueConstraint(fields=["yargi_turu", "kod"], name="uq_yargi_birimi")]
```

`Birim` (mahkeme/daire) modeli zaten var (§1.6) — `yargi_turu` eklenmesi
yeterli, ayrı bir "Mahkeme" tablosuna gerek yok.

### 2.4 Senkron kapsamı — kullanıcı tercihi (YENİ)

```python
class SenkronKapsami(models.Model):
    """Kullanıcının senkronize edilmesini istediği (yargı türü, yargı birimi
    türü) kombinasyonu. Boşsa (hiç kayıt yok) hiçbir şey otomatik senkron
    edilmez — kullanıcı en az bir kombinasyon eklemeli."""
    yargi_turu = models.PositiveSmallIntegerField()
    yargi_birimi_kod = models.CharField(max_length=8, blank=True)  # boş = türün tamamı
    aktif = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["yargi_turu", "yargi_birimi_kod"], name="uq_senkron_kapsami")]
```

### 2.5 "Dosya Bilgileri" sekmesi verisi — İKİ FARKLI ŞEKİL (icra vs dava)

`dosyaAyrintiBilgileri_brd.ajx` yanıtı dosya ailesine göre TAMAMEN farklı
alanlar döndürüyor (§1 canlı test, önceki oturumdan):
- İcra/takip: `takibinTuru`, `takibinSekli`, `takibinYolu`,
  `alacakKalemToplamTutar`, `vekaletUcreti`, `tahsilHarci`, ...
- Hukuk/dava: `davaAcilisTuru`, `davaTurleriStr`, `ilgiliDavaListesiStr`,
  `durusmaTarihi`, `basvuruyaBirakilmaTarihiStr`, ...

Tek bir düz tabloya zorlamak yanlış olur (çoğu alan diğer ailede hep NULL
kalır). **KARARLAŞTIRILDI (kullanıcı onayı, 2026-07-10): aile başına ayrı
model**, `Dosya`'ya `OneToOneField(related_name=...)`:

```python
class IcraTakipDetay(models.Model):
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="icra_detay")
    takibin_turu = models.CharField(max_length=8, blank=True)
    takibin_turu_aciklama = models.CharField(max_length=120, blank=True)
    takibin_sekli = models.CharField(max_length=8, blank=True)
    takibin_sekli_aciklama = models.CharField(max_length=255, blank=True)
    takibin_yolu = models.CharField(max_length=8, blank=True)
    takibin_yolu_aciklama = models.CharField(max_length=120, blank=True)
    alacak_kalemi_toplam = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    alacak_kalemi_faiz = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    takip_sonrasi_masraf = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vekalet_ucreti = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tahsil_harci = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    yapilmis_tahsilat = models.DecimalField(max_digits=14, decimal_places=2, default=0)


class HukukDavaDetay(models.Model):
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="hukuk_detay")
    dava_acilis_turu = models.CharField(max_length=120, blank=True)
    dava_turleri = models.CharField(max_length=500, blank=True)
    ilgili_dosya_listesi = models.CharField(max_length=500, blank=True)
    ilgili_dava_listesi = models.CharField(max_length=500, blank=True)
    ilgili_seri_dava_listesi = models.CharField(max_length=500, blank=True)
    birlesen_dosya_listesi = models.CharField(max_length=500, blank=True)
    durusma_tarihi = models.DateTimeField(null=True, blank=True)
    basvuruya_birakilma_tarihi = models.DateTimeField(null=True, blank=True)
```

Yeni yargı türleri (Ceza, İdari Yargı, ...) canlı test edildikçe kendi
`*Detay` modelleri aynı desenle eklenecek (bkz. §5 açık sorular — henüz
canlı görülmedi, alan uydurulmayacak).

## 3. Akış (öneri)

1. **İlk bağlantıda (bootstrap):** Yargı Birimi listesi her yargı türü için
   çekilip `YargiBirimi` tablosuna yazılır (küçük, sabit sıklıkla değişen
   veri — periyodik yenileme yeterli, her oturumda değil).
2. **Kullanıcı senkron kapsamını seçer:** Ayarlar ekranında (yeni) yargı
   türü + yargı birimi çoklu seçim listesi; seçilenler `SenkronKapsami`'ye
   yazılır.
3. **Arama/senkron:** `icra_core.py`'nin genellenmiş hâli (bkz. §4), aktif
   her `SenkronKapsami` satırı için `search_phrase_detayli.ajx`'i kendi
   `birimTuru2`/`birimTuru3` değerleriyle çağırır.
4. **Filtre UI:** Panel'deki dosya tablosu Yargı Türü, Yargı Birimi, Dosya
   Türü, Dosya Durumu, Açılış Tarihi aralığına göre filtrelenebilir hâle
   gelir (şu an yalnız birim adı/yıl/no/tür serbest metin filtresi var).
5. **"Dosya Görüntüle" tıklanınca:** `dosyaAyrintiBilgileri_brd.ajx` çağrılır,
   §2.5'teki JSON alanına yazılır.
6. **Taraflar:** Mevcut `Taraf`/`DosyaTaraf` altyapısı zaten yargı türünden
   bağımsız tasarlanmış (rol=alacaklı/borçlu) — yalnız İcra'ya özel
   `dosya_borclu_list.ajx` çağrısının Hukuk için karşılığı (Taraf Bilgileri
   sekmesi) henüz doğrulanmadı (bkz. §5).

## 4. Mimari karar — KARARLAŞTIRILDI (kullanıcı onayı, 2026-07-10): **(B) Paralel modül**

`icra_core.py` üretimde kanıtlanmış, özenle yazılmış bir modül (sayfalama,
taraf varyant denemeleri, `X-Uyap-Read` adalet başlığı, DB fallback — hepsi
"neden böyle" yorumlarıyla belgeli). Bunu yerinde genelleştirmek (sabitleri
parametreye çevirmek) İcra akışını regresyona sokma riski taşır. İki seçenek:

- **(A) Yerinde genelleştir:** `BIRIM_TURU2`/`BIRIM_TURU3` sabitlerini
  fonksiyon parametresi yap, `icra_core.py` hem icra hem diğer türler için
  kullanılsın.
- **(B) Paralel modül:** Yeni `dosya_core.py` ortak mantığı taşısın,
  `icra_core.py` onun üstüne icra'ya özel sabit `BIRIM_TURU2/3`'lü ince bir
  sarmalayıcı (wrapper) olarak kalsın — İcra akışı hiç dokunulmamış olur.

Kullanıcının "çalışan sistem bir köşede kalsın, belki iki farklı ürün"
tercihiyle (bu oturumun başındaki git yedekleme kararı) tutarlı: yeni
`dosya_core.py` ortak mantığı taşıyacak, `icra_core.py` DOKUNULMADAN kalıp
üzerine ince bir sarmalayıcı (veya paralel, ayrı çağrılan bir modül) olacak.

## 5. Açık sorular / eksik doğrulamalar

- `dosyaDurumKod`/`dosyaTurKod` tam listesi bilinmiyor — yalnız tek bir
  mahkemenin (İzmir 6. Asliye Ticaret) 51 kaydından örneklendi. "Tereke"
  kullanıcı tarafından adı verildi ama CANLI GÖRÜLMEDİ — kodu
  UYDURULMAYACAK, gerçek bir tereke dosyası bulunup doğrulanmalı.
- Ceza/İdari Yargı/diğer yargı türleri için `search_phrase_detayli.ajx`
  yanıtının Hukuk ile aynı şekilde çalıştığı VARSAYILIYOR (mantıken aynı uç
  nokta, aynı birimTuru2/3 deseni) ama henüz her tür için ayrı ayrı canlı
  test edilmedi.
- Hukuk (ve diğer türler) için "Taraf Bilgileri" sekmesinin arkasındaki uç
  nokta henüz yakalanmadı (icra'nın `dosya_borclu_list.ajx`'inin karşılığı).
- `dosyaAyrintiBilgileri_brd.ajx`'in Ceza/İdari Yargı/vb. için üçüncü bir
  şekli olup olmadığı bilinmiyor.

## 6. Kararlar

1. ~~§4 mimari kararı~~ → **(B) paralel modül** (kullanıcı onayı, 2026-07-10).
2. ~~§2.5 "Dosya Bilgileri" şeması~~ → **aile başına ayrı model**
   (`IcraTakipDetay`, `HukukDavaDetay`, ...) (kullanıcı onayı, 2026-07-10).
3. **Sıralama kararı:** §5'teki eksik doğrulamalar (Ceza/İdari Yargı için
   search_phrase_detayli şekli, Hukuk taraf uç noktası, tereke kodu) her biri
   AYRI bir canlı-test gerektiriyor ve İcra+Hukuk'tan bağımsız. Projenin
   şimdiye dek işleyen deseniyle tutarlı olarak: **Faz 1 (şema: `Birim.
   yargi_turu`, `YargiBirimi`, `SenkronKapsami`, genişletilmiş `Durum`/`Tur`
   choices, `IcraTakipDetay`+`HukukDavaDetay`) şimdi, elimizdeki DOĞRULANMIŞ
   İcra+Hukuk verisiyle yazılıyor.** Ceza/İdari Yargı/Satış Memurluğu/vb. ve
   taraf/tereke uç noktaları, o yargı türleri fiilen kullanılmaya
   başlandığında (ya da ayrı bir doğrulama oturumunda) kendi `*Detay`
   modelleriyle eklenecek — şimdiden alan uydurulmayacak.

## 7. İlerleme

- **Faz 1 (şema) — TAMAMLANDI** (commit `ff14cdc`): `Birim.yargi_turu`,
  `YargiBirimi`, `SenkronKapsami`, genişletilmiş `Dosya.Durum`/`Tur` choices,
  `IcraTakipDetay`, `HukukDavaDetay`. `manage.py check` temiz.
- **Faz 2 (sorgu motoru) — TAMAMLANDI** (commit `b9d87b1`): yeni
  `Panel/modules/dosya_core.py` — `yargi_birimleri_getir`/`_bootstrap`
  (`yargiBirimleriSorgula_brd.ajx` → `YargiBirimi` upsert), genellenmiş
  `birim_listesi_getir_genel`/`build_payload_genel` (§1.3/§1.4'teki
  `birimTuru2`/`birimTuru3` artık parametre), `SenkronKapsami`'ye göre
  çoklu-kapsam döngüsü kuran `DosyaSorgu.calistir`. Ortak aktarım/ayrıştırma
  mantığı (`SorguMotoru`, `parse_records`, `taraf_variantlari`,
  `kolon_degeri`, `save_taraf`, ...) `icra_core.py`'den İTHAL EDİLDİ,
  kopyalanmadı — mimari karar (§4) gereği `icra_core.py`'ye HİÇ dokunulmadı
  (yalnız okundu). `ingest.py`'deki `dosya_kunyesi_kaydet` artık opsiyonel
  `yargi_turu` parametresi alıyor (None ise eski davranış aynen korunur).
  **Sınırlama:** bu makinede PostgreSQL ikilikleri kurulu değil; doğrulama
  yalnızca `manage.py check` + kod incelemesiyle yapıldı, `YargiBirimi` upsert
  ve `DosyaSorgu.calistir` akışı CANLI DB'ye karşı henüz TEST EDİLMEDİ.
- **Faz 3 (çift arayüz — masaüstü Tkinter + web) — TAMAMLANDI.** Kullanıcı
  isteği (2026-07-10): "hem Django hem ofisteki tkinter arayüzünde bu
  listeleme ve indirme ayarı olsun." Önce mimari araştırıldı (bkz. altı) ve
  iki açık soru kullanıcıya soruldu/karara bağlandı, SONRA kod yazıldı:
  - **Mimari (araştırmayla doğrulandı, tartışmasız):** `Panel/panel.py`
    (Tkinter) ve `Panel/web/server.py` (çıplak `http.server`, Django DEĞİL)
    AYNI `models/icra_models` Django ORM'ini, AYNI DB'yi, proses-içi
    `import` ile paylaşıyor — aralarında HTTP API YOK. Yani `dosya_core.py`
    tek bir "headless motor" olarak HER İKİ arayüzden de doğrudan
    çağrılabiliyor; ayrı bir backend katmanı gerekmedi.
  - **"Listeleme" ile "indirme" AYNI kapsam mı?** Evet — doğrulandı
    (`icra_dosyalarim.py:551-559,713-729`): dosya listesi önce YEREL DB'den
    gösterilir (`db_dosyalari_getir`), sonra canlı UYAP'tan güncellenir.
    Yani DB'ye ne girerse (= `SenkronKapsami` neyi kapsıyorsa) listede o
    görünür — ayrı bir "listeleme kapsamı" kavramına gerek yok, TEK ayar
    ekranı hem taramayı hem dolayısıyla listelemeyi kapsıyor.
  - **"Tüm tür" (yargı birimi belirtmeden, örn. tüm Hukuk mahkemeleri)
    seçilebilir mi? KARARLAŞTIRILDI (kullanıcı onayı, 2026-07-10): EVET,
    olsun.** Bu nedenle Faz 2'deki yarım/atlanan "hepsi" desteği
    TAMAMLANDI: `DosyaSorgu._gorevleri_genislet` artık boş
    `yargi_birimi_kod`'u önce DB önbelleğinden (`yargi_birimleri_db_den_yukle`),
    yoksa canlı `yargi_birimleri_getir` ile genişletiyor; genişletilemezse
    (Yargı Birimi listesi de yoksa) o kapsam log ile bildirilip atlanıyor —
    artık sessizce atlamıyor.
  - **Yeni `dosya_core.py` fonksiyonları:** `yargi_birimleri_db_den_yukle`
    (ağa gitmeden DB önbelleği okur), `senkron_kapsami_durumu_getir` (ayar
    ekranının mevcut işaretli durumu için TÜM kayıtları — aktif/pasif —
    döner), `senkron_kapsami_kaydet` (replace-all semantik: listede
    olmayanlar SİLİNMEZ, yalnız `aktif=False` yapılır).
  - **Tkinter:** `Panel/modules/senkron_kapsami.py` (yeni,
    `SenkronKapsamiPanel`) — `ayarlar.py`/`icra_dosyalarim.py` desenleri
    (kart+checkbox, `queue`+`threading`+`app.after` polling). `panel.py`
    `CEKIRDEK_NAV`'a eklendi (her zaman görünür, "Ayarlar" gibi çekirdek —
    Mağaza'dan satın alınan bir özellik değil).
  - **Web:** `Panel/web/server.py`'ye `_dosya` modül global'i +
    `senkron_kapsami_durumu`/`senkron_kapsami_yenile`/`senkron_kapsami_kaydet_web`
    + `GET/POST /api/senkron-kapsami` + `GET /api/senkron-kapsami/yenile`
    (tek bir türün listesini canlı yenilemek için, ayrı uç — 8 türü her
    sayfa açılışında canlı çekmek yavaş olurdu). `static/senkron_kapsami.js`
    (yeni) + `index.html`'e `data-panel="senkron_kapsami"` bölümü +
    `app.js` CORE listesine eklendi. Bir türün listesini yenilemek yalnız O
    KARTI günceller (tam grid yeniden çizilmiyor) — kullanıcının diğer
    kartlardaki kaydedilmemiş işaretlemeleri kaybolmasın diye.
  - **Doğrulama:** `py_compile` + `node --check` (JS) + izole fonksiyon
    testleri (DB yoksa zarifçe `[]`/hata mesajı dönüyor) temiz. **DİKKAT:**
    `server._load_auth()` test amacıyla ASLA çağrılmasın — içindeki
    `boot_autoconnect()` auto_connect ayarı kayıtlıysa GERÇEK e-imza UYAP
    girişini tetikler (bkz. bellek: server-load-auth-canli-giris-tuzagi).
    Canlı DB'ye karşı tam uçtan uca test (Postgres bu makinede kurulu değil)
    hâlâ YAPILMADI.

- **Faz 4 (arka plan senkron zamanlayıcısı) — TAMAMLANDI.** Kullanıcı isteği
  (2026-07-10): kalan işler arasından en önemlisinden başlanması istendi —
  ayar ekranı (Faz 3) tek başına işe yaramaz, onu fiilen ÇALIŞTIRAN bir
  döngü olmadan `SenkronKapsami` yalnızca kayıtlı bir tercih olarak kalırdı.
  `dosya_core.py`'ye `senkron_zamanlayici_baslat(interval_saniye=None,
  log_fn=None)`/`senkron_zamanlayici_durdur()` eklendi: daemon thread, her
  `VARSAYILAN_ARALIK_SANIYE` (1800 sn = 30 dk) aralıkla `DosyaSorgu(log_fn).
  calistir()` çağırır; süreç başına yalnız bir kez gerçekten başlar (ikinci
  çağrı çalışan thread'i döner, no-op). UYAP oturumu/proxy ya da DB o an
  erişilemezse tur sessizce loglanıp bir sonraki aralıkta yeniden denenir —
  `_load_auth()`/`boot_autoconnect()` gibi YENİ bir oturum AÇMAZ, yalnızca
  zaten var olan bağlantıyı (127.0.0.1:8800) kullanır, dolayısıyla o riski
  TAŞIMAZ (bkz. bellek: server-load-auth-canli-giris-tuzagi).
  - **Web:** `Panel/web/server.py`'nin tek `_load_auth()` çağrısı (süreç
    başına bir kez, `main()` içinde arka plan thread'inde) `_dosya` başarıyla
    yüklendiğinde `senkron_zamanlayici_baslat`'ı da çağırır.
  - **Tkinter:** `Panel/panel.py`'nin `__init__`'i, `_load_auth` thread'ini
    başlattığı yerin hemen yanında (aynı `--selftest` korumasıyla)
    `dosya_core.senkron_zamanlayici_baslat`'ı çağırır; log mesajları
    `self.log_queue`'ya yazılır (mevcut "Bağlantı" modülüyle aynı thread-safe
    günlük kanalı).
  - **Bilinen sınırlama (kabul edildi, tasarım gereği):** Aynı makinede hem
    ofis ajanı (web, kur-unut) hem Tkinter paneli AYNI ANDA açıksa, iki AYRI
    süreç kendi zamanlayıcısını bağımsız çalıştırır — aynı DB'ye/proxy'ye
    çakışan taramalar olabilir. Bu, mevcut "adil sıralama" (X-Uyap-Read)
    tasarımıyla tutarlı (karşılıklı dışlama değil, adalet); ayrı bir
    kilitleme mekanizması eklenmedi çünkü upsert semantiği (`dosya_kunyesi_
    kaydet`) zaten tekrarlı yazımlara karşı güvenli.
  - **Doğrulama:** izole testte (`server._load_auth()`/`boot_autoconnect()`
    hiç ÇAĞRILMADAN, doğrudan `dosya_core.senkron_zamanlayici_baslat(interval_
    saniye=1, log_fn=...)`) zamanlayıcı başladı, bir tur çalıştı (DB bu
    makinede kurulu olmadığı için beklenen "connection refused" hatasını
    zarifçe yakalayıp logladı), `senkron_zamanlayici_durdur()` ile temiz
    şekilde durdu (thread `is_alive() == False`). Canlı UYAP oturumu +
    PostgreSQL ile uçtan uca test hâlâ YAPILMADI (bilinen sınırlama).

- **Faz 5 ("Dosya Görüntüle" — Dosya Bilgileri ayrıntısı) — KISMEN
  TAMAMLANDI.** Kullanıcının sıralı isteğindeki ikinci madde. İstek şekli
  TAHMİN DEĞİL — `Panel/modules/Vekalet_Sunma.py`'deki CANLI YAKALANMIŞ akışın
  7. adımından (`dosyaAyrintiBilgileri_brd.ajx`, payload `{"dosyaId": "..."}`)
  AYNEN alındı. Yanıt şeklinin YALNIZ bir kısmı canlı doğrulanmıştı (§1.5/§2.5
  — prose özet, ham JSON dökümü değil); repo genelinde (`Vekalet_Sunma.py`,
  `Mts_evirme.py`, `htiyati_Haciz.py`, plan dosyası, models.py) arandı, yanıtın
  TAM ham JSON dökümü hiçbir yerde bulunamadı. **Bu yüzden ingest yalnızca
  önceden doğrulanmış anahtarları yazar**, geri kalanı UYDURULMAZ:
  - `dosya_core.py`: `dosya_ayrinti_getir(dosya_id, log_fn)` (transport),
    `dosya_ayrinti_kaydet(dosya, ham, log_fn)` (aile — `dosya.birim.
    yargi_turu`'na göre — `IcraTakipDetay`/`HukukDavaDetay`'a yazar, doğrulanan
    alanlar: İcra için `takibinTuru/Sekli/Yolu`, `alacakKalemToplamTutar`,
    `vekaletUcreti`, `tahsilHarci`; Hukuk için `davaAcilisTuru`,
    `davaTurleriStr`, `ilgiliDavaListesiStr`, `durusmaTarihi` — ki bu sonuncusu
    `ingest._tarih` ile ayrıştırılır, `basvuruyaBirakilmaTarihiStr` biçimi
    doğrulanmadığı için kaydedilmez, yalnız loglanır), `dosya_detay_goster_ve_
    kaydet(rec, log_fn)` (tek giriş noktası: `rec`'ten TAZE dosyaId'yi alır,
    ayrıntıyı çeker, `Dosya`'yı doğal anahtarıyla — birim+yıl+sıra+tür —
    bulur, kaydeder). Ham yanıt HER ZAMAN log_fn'e yazılır (teşhis) ki canlı
    doğrulama yapıldığında eksik alanların gerçek adları loglardan okunabilsin.
  - **Önemli mimari not (Dosya.dosya_id OTURUMLUK):** `models.py`'nin kendi
    docstring'i `Dosya.dosya_id`'nin UYAP oturumuna göre değişebildiğini
    söylüyor — bu yüzden `dosya_detay_goster_ve_kaydet` DB'den eski bir
    dosya_id OKUMAZ, çağıranın o ANKİ arama sonucundan (`rec["dosyaId"]`) TAZE
    değeri vermesini ister; DB önbelleğinden gelen (henüz bu oturumda
    sorgulanmamış) kayıtlarda dosyaId boş/eski olabilir, bu durumda açık bir
    hata mesajıyla (listeyi yenileyin) geri döner.
  - **Tkinter:** `icra_dosyalarim.py`'ye "Dosya Görüntüle" düğmesi eklendi;
    tabloya satır seçilip tıklanınca (queue+thread deseniyle, masaüstünün
    tüm diğer akışlarıyla AYNI) ayrıntı çekilir, `messagebox` ile gösterilir.
  - **Web:** `icra.js`'e "Dosya Görüntüle" düğmesi + satır seçme (tıklama)
    eklendi; `server.py`'ye `IcraJob.records` (ham kayıtları AYNI sırada
    saklar — önceden yalnız serileştirilmiş `rows` tutuluyordu) +
    `icra_detay()` + `POST /api/icra/detay` eklendi.
  - **Kapsam:** yalnızca İcra Dosyalarım ekranına eklendi (halihazırda tek
    çalışan dosya listesi ekranı budur — diğer yargı türleri için henüz genel
    bir "Dosyalarım" tarama ekranı yok, bkz. altındaki "Kalan").
  - **Doğrulama:** `py_compile`+`node --check` temiz; izole testte `dosya_id`
    boş/erişilemez durumlar için hata mesajları doğru döndü. Canlı UYAP +
    PostgreSQL ile uçtan uca test hâlâ YAPILMADI (bilinen sınırlama).
  - **Kabul edilmiş bilinmeyen (doğrulanmadı, uydurulmadı):** Arka plan
    zamanlayıcısı (Faz 4) her 30 dakikada `Dosya.dosya_id`'yi YENİDEN yazabilir
    (`dosya_kunyesi_kaydet` upsert eder). Kullanıcı ekranda ESKİ bir
    `all_records` görüntüsüyle "Dosya Görüntüle"ye tıklarsa gönderilen dosyaId,
    zamanlayıcının o an DB'ye yazdığı YENİ değerden farklı olabilir. UYAP'ın
    `dosyaAyrintiBilgileri_brd.ajx`'i böyle "eski ama var olan" bir dosyaId'ye
    nasıl tepki verdiği (açıkça hata mı verir, yoksa başka bir dosyanın
    ayrıntısını mı döner) repo genelinde ARANDI (`Vekalet_Sunma.py`,
    `Mts_evirme.py`) ama hiçbir yerde bulunamadı — CANLI DOĞRULANMADI. Bu
    yüzden UYDURULMADI/varsayılmadı; ileride canlı test edilmeli.

- **Faz 6 (genel çoklu-yargı-türü "Dosyalarım" ekranı + filtre UI) —
  TAMAMLANDI.** Kullanıcının sıralı isteğindeki üçüncü madde; kullanıcıya
  soruldu ("filtre UI aslında yeni bir genel ekran gerektiriyor, nasıl
  ilerleyeyim?") ve "genel ekranı yap" onayı alındı. **Mimari karar (§4
  precedentiyle tutarlı):** `icra_dosyalarim.py`'ye KESİNLİKLE dokunulmadı
  (kanıtlı modül); yeni, AYRI bir ekran (`dosyalarim_genel.py` / web
  `dosyalarim_genel.js`) `dosya_core.py`'yi doğrudan kullanır.
  - **Tasarım farkı (İcra Dosyalarım'dan bilinçli sapma):** bu ekran CANLI
    UYAP sorgusu YAPMAZ, yalnız yerel DB'yi okur/filtreler (yargı
    türü/birimi/dosya türü/durum/açılış tarihi aralığı) — çünkü artık
    `SenkronKapsami` (Faz 3) + arka plan zamanlayıcısı (Faz 4) veriyi zaten
    sürekli DB'ye indiriyor; birden çok yargı türü/birimi için AYRICA canlı
    sorgu mantığı yazmak `DosyaSorgu.calistir`'i tekrarlamak olurdu. "Yenile"
    düğmesi kullanıcı isteğiyle HEMEN `DosyaSorgu.calistir`'i (=
    `dosya_core.dosyalarim_yenile`) tetikler, DB'yi tazeler.
  - **`dosya_core.py` eklentileri:** `dosya_tur_secenekleri`/`dosya_durum_
    secenekleri` (Dosya.Tur/Durum choices'ı UI'a sunar), `dosyalarim_db_
    listele(filtreler)` (yargi_turu/yargi_birimi_kod/tur_kod/durum_kod/tarih
    aralığına göre DB sorgusu, 2000 kayıtla sınırlı), `dosyalarim_yenile`
    (ince sarmalayıcı — `DosyaSorgu.calistir`).
  - **Tkinter:** `Panel/modules/dosyalarim_genel.py` (yeni,
    `DosyalarimGenelPanel`) — filtre çubuğu (4 combobox + tarih aralığı),
    Treeview sonuç tablosu, "Yenile"/"Dosya Görüntüle" düğmeleri (aynı
    queue+thread+`app.after` deseni). `panel.py` `CEKIRDEK_NAV`'a
    "Uygulama Mağazası" ile "Senkron Kapsamı" arasına eklendi (çekirdek,
    her zaman görünür).
  - **Web:** `index.html`'e `data-panel="dosyalarim_genel"` bölümü + `app.js`
    CORE listesine eklendi; `static/dosyalarim_genel.js` (yeni) aynı
    filtre+tablo+Yenile+Dosya Görüntüle akışını uygular. `server.py`'ye
    `dosyalarim_fields/birimler/list/detay/yenile_baslat/yenile_durum` +
    `GET/POST /api/dosyalarim/*` uçları eklendi ("Yenile" `icra_search`'teki
    job-tabanlı arka plan ilerleme desenini izler — `DOSYALARIM_JOBS`).
  - **"Dosya Görüntüle" bu ekranda:** satırın DB'den okunan `dosyaId`'si
    doğrudan kullanılır (İcra Dosyalarım'daki gibi HAM bir arama sonucundan
    değil) — bu yüzden Faz 5'in "kabul edilmiş bilinmeyen" notu (dosyaId
    OTURUMLUK, DB'den okunanı eski olabilir) BURADA DA geçerlidir; kullanıcı
    önce "Yenile" ile tazelemeli.
  - **Doğrulama:** `py_compile`+`node --check`+HTML section denge kontrolü
    temiz; izole testte (`server._dosya` doğrudan atanarak, `_load_auth()`
    ÇAĞRILMADAN) `fields`/`birimler`/`list`/`detay`/`yenile_baslat`/`yenile_
    durum` hepsi DB'siz ortamda beklenen zarif hata mesajlarını döndü,
    "yenile" job'ı 2 saniyede DB-erişilemez hatasıyla temiz bitti (asılı
    kalmadı). Canlı UYAP + PostgreSQL ile uçtan uca test hâlâ YAPILMADI.

- **Doğrulama düzeltmesi (2026-07-10, `/verify` sonrası) — TAMAMLANDI**
  (commit `c8315ca`): `/verify` sırasında canlı ölçüldü — `models/uyapdata/
  settings.py`'de `connect_timeout` ayarlanmadığı için gerçek `.venv`
  sürücüsüyle (psycopg v3) DB erişilemezken sorgular ~130 SANİYE donuyordu
  (sistem python'daki psycopg2 ile aynı senaryo ~2 sn'de hızlı hata veriyordu
  — fark sürücü, DB durumu değil). Bu, Faz 2'den beri `dosya_core.py`'nin
  TÜM DB çağrılarını (zamanlayıcı, Dosyalarım ekranları, Senkron Kapsamı)
  etkiliyordu; Faz 4/6 bunu YENİ, kullanıcının doğrudan tıkladığı yerlerde
  (Filtrele/Yenile düğmeleri, zamanlayıcı döngüsü) görünür kıldığı için
  düzeltildi. `DATABASES["default"]["OPTIONS"]["connect_timeout"] = 3`
  eklendi — doğrulandı: aynı DB-erişilemez senaryo artık 130s yerine 3.46s.

- **Faz 8 — Hukuk (ve genel) Taraf Bilgileri uç noktası — TAMAMLANDI**
  (2026-07-10): canlı doğrulandı — klasik `avukat.uyap.gov.tr`'ye (kendi
  sistemimiz değil, doğrudan e-imza) Chrome ile giriş yapılıp bir Hukuk dava
  dosyasının "Taraf Bilgileri" sekmesi açılarak ağ trafiği (fetch/XHR hook)
  yakalandı. Uç nokta: **`dosya_taraf_bilgileri_brd.ajx`** (POST, gövde
  `{"dosyaId": "<taze dosyaId>"}` — `dosyaAyrintiBilgileri_brd.ajx` ile AYNI
  gövde şekli). Yanıt: `list[{"adi": str, "rol": "Davacı"|"Davalı", "vekil":
  "[AD SOYAD]" (vekil yoksa anahtar HİÇ YOK), "kisiKurum": "Kişi"|"Kurum"}]`
  — TCKN/MERSİS/vergi no VERMEZ. Yalnız Hukuk canlı test edildi; diğer yargı
  türlerinde aynı uç nokta varsayılıyor (dosya_islem_turleri_sorgula_brd.ajx
  yanıtındaki ortak "taraf_bilgileri" modülünden), AYRI doğrulanmadı — hata
  durumunda sessizce boş liste döner, UI'ı bozmaz.
  Kod: `dosya_core.py`'ye `dosya_taraf_getir`/`_taraf_bilgisi_ayristir`/
  `_vekil_kaydet`/`dosya_taraf_kaydet` eklendi; `dosya_detay_goster_ve_kaydet`
  artık 5'li tuple döner (`..., taraflar`) — ayrıntı + taraf TEK çağrıda
  gelir. `models.DosyaTaraf.Rol`'e `DAVACI`/`DAVALI` eklendi (migration 0005).
  Her iki UI (`icra_dosyalarim.py`, `dosyalarim_genel.py`, `icra.js`,
  `dosyalarim_genel.js`, web `server.py`'nin `icra_detay`/`dosyalarim_detay`)
  "Dosya Görüntüle" çıktısına Taraf Bilgileri listesini ekleyecek şekilde
  güncellendi.

  **İcra gate'i KALDIRILDI — canlı doğrulandı (2026-07-11):** önceki
  sürümde `aile == "icra"` olduğunda yeni uç nokta HİÇ ÇAĞRILMIYORDU (bkz.
  altında eski gerekçe). Bu kez `avukat.uyap.gov.tr`'ye client-side routing
  ile (soldaki menüden tıklayarak — doğrudan URL girişi `/dosya-sorgulama`
  sunucudan 404 alıp SPA'yı "Sorgulanıyor…" durumunda sonsuza kadar
  kilitliyor, bu KOD HATASI değil UYAP'ın kendi SPA'sının bir hydration
  kusuru) girilip gerçek bir İcra dosyası (İzmir Banka Alacakları İcra
  Dairesi 2026/89122) açıldı, hem "Taraf Bilgileri" hem "Borçlu Bilgileri"
  sekmeleri fetch/XHR hook'uyla yakalandı. Üç zincir halkası da CANLI
  doğrulandı (varsayım değil):
  1. Rol string'leri (`"Alacaklı"`/`"Borçlu"`) `tr_lower()` ile mevcut
     `Rol.ALACAKLI`/`Rol.BORCLU` koduna (`"alacakli"`/`"borclu"`) BİREBİR
     eşleşiyor.
  2. Aynı kişi için `dosya_taraf_bilgileri_brd.ajx` (`adi`: "FİKRİ BOZKUŞ")
     ve icra_core.py'nin kendi `dosya_borclu_list.ajx` akışı (`adi`:
     "FİKRİ", `soyadi`: "BOZKUŞ", `tcKimlikNo`: "27445828730") harf harf
     AYNI ad/soyad'ı veriyor — `save_taraf`'ın TCKN'siz (ad,soyad) fallback
     eşleşmesi icra_core'un TCKN'li kaydettiği AYNI Taraf satırına düşüyor.
  3. Her iki `DosyaTaraf.objects.update_or_create` çağrısı da aynı
     `(dosya, taraf, rol)` anahtarını kullanıyor (icra_core.py:752-754 ile
     dosya_core.py karşılaştırıldı) — bu yüzden borçlu satırı YİNELENMEZ,
     yalnız güncellenir (`tckn`'e hiç dokunulmaz, çünkü `save_taraf`'ın
     `defaults` sözlüğü tckn içermez).
  Ayrıca not: icra_core.py yalnız `rol="borclu"` yazıyor (`rol="alacakli"`
  hiç yazmıyor) — gate kaldırılınca İcra dosyalarına Alacaklı satırı (vekil
  bilgisiyle birlikte) İLK KEZ ekleniyor; bu saf bir veri zenginleştirme,
  çakışma değil.

  Doğrulama: gerçek `.venv` altında geçici SQLite ile GERÇEK Django ORM
  üzerinden uçtan uca senaryo çalıştırıldı — icra_core'un önceden
  oluşturduğu TCKN'li Borçlu kaydı (1 Taraf, 1 DosyaTaraf) simüle edilip
  canlı yakalanan tam JSON ile `dosya_taraf_kaydet` çağrıldı: sonuç 2
  Taraf/2 DosyaTaraf — borçlu satırı YİNELENMEDİ (tckn korundu, sira
  güncellendi), alacaklı satırı vekille birlikte YENİ eklendi. Tüm
  assertion'lar geçti. Ayrıca önceki (Hukuk) doğrulaması geçerliliğini
  korur: `panel.py --selftest` + web sunucusu (güvenli harness,
  `_load_auth()` ÇAĞRILMADAN) ile `/api/dosyalarim/detay` uçtan uca test
  edildi. Canlı UYAP + gerçek PostgreSQL ile TAM uçtan uca (bu ortamda
  embedded Postgres binary'leri mevcut değil) hâlâ ayrı doğrulanmadı.

  **Diğer yargı türlerinin canlı taraması (2026-07-11):** kullanıcı isteği
  üzerine tüm Yargı Türü seçeneklerinde ("her yargı birimini de dene")
  gerçek dosyalar açılıp Taraf Bilgileri yakalandı:
  - **İdari Yargı** (İDARE MAHKEMESİ, 2026/1003): rol = `Davacı`/`Davalı`
    — Hukuk ile AYNI, ek kod GEREKMEDİ.
  - **Ceza** (ASLİYE CEZA MAHKEMESİ, 2026/391): rol = `Sanık`/`Müşteki` —
    YENİ değerler, `Rol`'e eklendi.
  - **Satış Memurluğu** (SATIŞ MEMURLUĞU/Tereke, 2026/5): rol içinde
    `Alacaklı`'nın yanında `Üçüncü Şahıs`/`Hissedar`/`Tasfiye Memuru` de
    var — YENİ.
  - **Arabuluculuk** (ARABULUCULUK MERKEZİ, 2026/4855): rol =
    `Başvurucu`/`Diğer Taraf` — YENİ.
  - **Cbs** (2026-07-11, ikinci deneme): önceki oturumda "Sorgula" butonu
    kalıcı "Sorgulanıyor…" durumunda kilitlenmişti (muhtemelen o anki İl/
    Yargı Birimi kombinasyonuna özgü geçici bir durum); kullanıcının uyarısı
    üzerine tekrar denendiğinde İzmir + İzmir Cumhuriyet Başsavcılığı
    kombinasyonuyla sorgu ÇALIŞTI ve gerçek Cbs Soruşturma Dosyaları listelendi
    (örn. 2026/88535, 2026/68375, 2026/143181...). Ancak dosya detayı
    modalında Hukuk/İcra/Ceza'daki gibi bir "Taraf Bilgileri" sekmesi/kartı
    HİÇ YOK — yalnızca Evrak, Kişisel Notlarım, Ödeme kartları var (hem
    normal hem tam ekran görünümde doğrulandı). Kesin kanıt için
    `dosya_taraf_bilgileri_brd.ajx` gerçek bir Cbs `dosyaId`'siyle DOĞRUDAN
    çağrıldı: sunucu boş liste DEĞİL, açık bir yetki hatası döndürdü —
    `{"errorCode":"PRTL_GNL_1-1","error":"Bu dosya üzerinde ilgili işlemi
    yapma yetkiniz bulunmamaktadır."}`. Yani Cbs (soruşturma) dosyalarında
    taraf bilgisi UYAP tarafında avukata KASITLI OLARAK kapalı (muhtemelen
    soruşturma gizliliği) — UI kısıtlaması değil, sunucu-taraflı yetki
    kısıtlaması. `dosya_taraf_getir` (`dosya_core.py`) bu durumu zaten güvenle
    ele alıyor: yanıt `list` değilse (`isinstance(veri, list)` False) `[]`
    döner, hiçbir hata/kayıp oluşmaz — kod değişikliği GEREKMEDİ.
  - **Tazminat Komisyonu Başkanlığı**: CANLI DOĞRULANAMADI. Gerçek yanıt
    `[[],0]` geldi (bu kullanıcının bu türde dosyası yok — kod sorunu değil).

  **Bulunan gerçek hata — `rol` alanı çok kısaydı:** yeni rol string'lerinin
  `tr_lower()` çıktıları mevcut `max_length=10`'u aşıyordu: `"Üçüncü Şahıs"`
  → `"ucuncu sahis"` (12), `"Tasfiye Memuru"` → `"tasfiye memuru"` (14),
  `"Diğer Taraf"` → `"diger taraf"` (11). Bu, Faz 8'in İcra gate'i
  kaldırıldığından beri (commit `f19709c`) HALİHAZIRDA canlıda mevcut bir
  hataydı: `dosya_taraf_kaydet` `transaction.atomic()` içinde çalıştığından,
  gerçek PostgreSQL'de bu uzunluktaki BİR satır bile tüm o dosyanın taraf
  toplu yazımını (sessizce, `except Exception` yakalıyor) geri alırdı —
  Ceza/Satış Memurluğu/Arabuluculuk dosyalarında hiçbir taraf kaydedilmezdi.
  Düzeltme: `DosyaTaraf.Rol`'e 7 yeni değer eklendi (`SANIK`, `MUSTEKI`,
  `UCUNCU_SAHIS`, `HISSEDAR`, `TASFIYE_MEMURU`, `BASVURUCU`, `DIGER_TARAF`),
  `rol` alanı `max_length=10`→`20` genişletildi (canlı doğrulanan en uzun
  değer 14 — Cbs/Tazminat Komisyonu canlı doğrulanamadığından 6 karakterlik
  pay bırakıldı, TAHMİN olarak işaretlenir). Migration `0006_alter_
  dosyataraf_rol`. Geçici SQLite ile gerçek ORM üzerinden doğrulandı: 7 yeni
  kod da (en uzunu dahil) doğru kaydedildi, `get_rol_display()` doğru
  Türkçe etiketi döndürdü. Commit: bkz. altında.

- **Kalan (henüz yapılmadı):** Dosya Bilgileri ayrıntısının UYDURULMAYAN
  kalan alanları (faiz/masraf ayrıntısı, ilgili/seri/birleşen dosya
  listeleri, başvuruya bırakılma tarihi — canlı JSON dökümü yapıldığında
  tamamlanacak). Taraf Bilgileri uç noktasının Tazminat Komisyonu
  Başkanlığı'nda canlı doğrulanması (bu kullanıcının o türde dosyası
  olmadığı için şu an mümkün değil — yeni bir dosya açıldığında tekrar
  denenebilir). Cbs artık KESİN: sunucu yetki hatası döndürüyor, taraf verisi
  hiç gelmiyor — `max_length=20`'nin Cbs için TAHMİN olma durumu ortadan
  kalktı (Cbs zaten hiç rol değeri üretmeyecek); yalnız Tazminat Komisyonu
  için hâlâ doğrulanmamış bir pay olarak kalıyor. Gerçek PostgreSQL ile uçtan uca DB yazma testi (bu
  geliştirme ortamında embedded Postgres yok). Ayrıca ileride istenirse:
  zamanlayıcı aralığının (şu an sabit 30 dk) bir ayar ekranından değiştirilebilir hâle getirilmesi,
  "stale dosyaId" davranışının canlı doğrulanması (bkz. Faz 5 "kabul
  edilmiş bilinmeyen").
