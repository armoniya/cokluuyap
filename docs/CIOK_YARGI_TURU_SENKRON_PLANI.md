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
kalır). Öneri: `Dosya`'ya JSON alan (`ayrinti_json = models.JSONField(default=dict)`)
+ yalnız FİLTRELEMEDE kullanılacak ortak alanlar (`durusma_tarihi` gibi) ayrı
sütun. **Bu, açık bir tasarım kararı — kullanıcı onayı gerekir** (bkz. §6).

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

## 4. Mimari karar — AÇIK SORU (kullanıcıya, kod yazmadan önce)

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
tercihiyle (bu oturumun başındaki git yedekleme kararı) tutarlı olan **(B)**
daha güvenli görünüyor, ama karar kullanıcıya bırakılmalı.

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

## 6. Kullanıcıya sorular

1. §4 mimari kararı: **(A) yerinde genelleştir** mi, **(B) paralel modül**
   mü?
2. §2.5 "Dosya Bilgileri" verisi için JSON alan yaklaşımı kabul edilebilir
   mi, yoksa aile başına ayrı model mi tercih edilir (örn. `IcraTakipDetay`,
   `HukukDavaDetay`)?
3. §5'teki eksik doğrulamalar için: kalan yargı türlerini (Ceza, İdari Yargı,
   vb.) ve taraf/tereke uç noktalarını tek tek canlı test etmeye devam
   edelim mi (yine Chrome ile), yoksa yeterli veri toplandı, Faz 1'e (şema +
   bootstrap) geçelim mi?
