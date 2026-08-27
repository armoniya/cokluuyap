# Borçlu Kartı — Plan — Taslak

Tarih: 2026-07-13
Durum: **PLAN — henüz kod yazılmadı.**

## 0. İstek

İleride İcra kartı (dosya görünümü) için değişiklik yapılacak; bunun bir
parçası olarak bir **borçlu kartı** eklenecek — bir borçlunun (`Taraf`,
`DosyaTaraf.rol=BORCLU`) tüm dosyalarda görünen künye bilgilerini tek bir
kartta toplayan bir görünüm. Bu doküman, o kart yazılmaya başlanmadan önce
uyulması gereken TEK kritik kuralı sabitler: **künyenin her öğesi her
senkronda körlemesine güncellenmemeli.**

## 1. Neden bu kural gerekli — mevcut kodda kanıtlanmış risk

`save_taraf()` (`Panel/modules/icra_core.py:554` — **dokunulmaz**, bkz. §4)
şu an her taraf senkronunda TÜM alanları koşulsuz eziyor:

```python
defaults = {
    "tur": tur, "ad": ad or "", "soyad": soyad or "", "unvan": unvan or "",
    "vergi_no": vergi_no or "", "mersis_no": mersis_no,
}
...
if taraf:
    for k, v in defaults.items():
        setattr(taraf, k, v)   # ← YENİ DEĞER BOŞ OLSA BİLE ESKİYİ EZER
    taraf.save()
```

Ve besleyici (`Panel/modules/dosya_core.py:_taraf_bilgisi_ayristir`,
`dosya_taraf_getir`'in ham yanıtından üretiyor) şu an **TCKN/vergi
no/mersis no'yu hiçbir zaman doldurmuyor** — sabit `None`/`""` döndürüyor,
çünkü `dosya_taraf_bilgileri_brd.ajx` uç noktası bu alanları vermiyor:

```python
return {"tur": "gercek", "ad": ad, "soyad": soyad, "unvan": "",
        "tckn": None, "vergi_no": "", "mersis_no": None}
```

Sonuç: TCKN/vergi no/adres/IBAN gibi alanlar **borçlu kartı özelinde**
başka bir kaynaktan (ayrı bir sorgu, kullanıcının elle girişi, doğrulanmış
bir başka uç nokta) bir kez doldurulsa bile, dosya listesi normal şekilde
senkronize olduğu her seferinde `save_taraf()` bu dolu değerin üzerine
BOŞ yazıp SİLER. Bu tam olarak [[proxy-swr-cache-oturum-kapsami]] ve
eşzamanlı-hata çalışmasında (2026-07-13) sabitlenen "UYDURULMAZ / var olan
doğru veri asla hata veya eksik yanıtla ezilmez" disiplininin bir başka
görünümü — burada "hata" değil ama sonucu aynı: **eksik/boş bir yanıt,
zaten doğru olan bir alanı sessizce siliyor.**

Hassas veri (TCKN, adres, IBAN, telefon) söz konusu olduğunda bu risk kabul
edilemez — kullanıcı bunu açıkça belirtti: "ileride hassas veriler bu
dosyalarda kalacak, bunu istemiyorum."

## 2. Kural (borçlu kartı yazılırken uyulacak)

**Bir alan yalnız YENİ DEĞER DOLU olduğunda güncellenir. Yeni değer boşsa
o alana hiç dokunulmaz — eski (varsa) değer olduğu gibi kalır.**

Yani şu an `save_taraf`'ın yaptığı "tüm `defaults`'u koşulsuz `setattr`"
yerine, borçlu kartına özel yazma yolu şöyle davranmalı (taslak sözde-kod):

```python
for alan, yeni_deger in defaults.items():
    if yeni_deger in (None, ""):
        continue  # boş yeni değer, dolu eskiyi ASLA ezmez
    setattr(taraf, alan, yeni_deger)
taraf.save()
```

Ek olarak:

- **Kaynak önceliği** tanımlanmalı: birden fazla uç nokta/akış aynı alanı
  besleyebiliyorsa (ör. TCKN hem dosya-taraf akışından hem ayrı bir
  doğrulanmış sorgudan gelebilir), hangi kaynağın "daha güvenilir" sayılıp
  diğerini ezebileceği açıkça karar verilmeli — sessiz/rastgele öncelik
  OLMAZ.
- **UYDURULMAZ disiplini burada da geçerli**: bir alanın gerçek UYAP
  anahtar adı/eşleşmesi canlı doğrulanmadan borçlu kartına "kesin veri"
  gibi yazılmaz (bkz. [[icra-kunye-penceresi-ve-takibin-turu-tahmini]] —
  aynı disiplinin daha önceki bir uygulaması, "(tahmini)" etiketiyle
  ekranda gösterip DB'ye yazmama).
- Bu "boş ezmez" kuralı yalnız borçlu kartına özel yeni yazma yolu için
  geçerli olacak; `save_taraf()`'ın kendisi **değiştirilmeyecek** (bkz.
  §4) — mevcut dosya-taraf senkron akışı bugünkü davranışıyla kalır, yeni
  kart kendi sarmalayıcısını kullanır.

## 3. Künye kapsamı (mevcut `Taraf` modeli alanları — `models/icra_models/models.py:92`)

Gerçek kişi: `ad`, `soyad`, `tckn`.
Tüzel kişi: `unvan`, `mersis_no`, `vergi_no`, `e_tebligat_adresi`, `kep_adresi`.
Ortak: `adres`, `iban`.

Bu doküman hangi alanların borçlu kartında GÖSTERİLECEĞİNE karar vermiyor —
yalnız yazma kuralını sabitliyor. Gösterim kapsamı, kart tasarımı
başladığında ayrıca netleştirilecek.

### 3.1 Doldurulması gereken kimlik alanları (kullanıcı isteği, 2026-07-13)

`tckn`, `vergi_no`, `mersis_no` — model'de zaten VAR ama şu an mevcut
dosya-taraf senkron akışında (`_taraf_bilgisi_ayristir`, §1) HİÇBİRİ
doldurulmuyor, sabit `None`/`""` dönüyor. Bunların gerçek verilerle
doldurulması, borçlu kartı için gelecekteki bir iş kalemidir.

**DETSİS No (Dernekler Bilgi Sistemi No) — model'de HENÜZ YOK, yeni alan +
migration gerekir.** Tüzel kişi tarafın türüne göre (şirket → MERSİS,
dernek → DETSİS) hangi kimlik numarasının geçerli olduğu ayrışır; `Taraf`
modeline `detsis_no` (mersis_no ile aynı desende: `CharField(blank=True,
null=True, unique=True)`) eklenmeli.

Hangi UYAP uç noktasının bu dört alanı (tckn/vergi_no/mersis_no/detsis_no)
sağladığı HENÜZ canlı doğrulanmadı — UYDURULMAZ disiplini gereği burada
varsayılan bir uç nokta/alan adı belirtilmiyor; kaynak canlı tespit
edilmeden kod yazılmayacak. Doldurulduklarında §2'deki "boş ezmez" kuralı
bunlar için de geçerli: bir kere gerçek bir kaynaktan dolan tckn/vergi
no/mersis no/detsis no, sonraki normal dosya-taraf senkronunda (ki o akış
bunları hâlâ boş döndürüyor) ASLA silinmemeli.

## 4. Kısıt — `icra_core.py` dokunulmaz

Standing mimari karar (2026-07-10, `docs/CIOK_YARGI_TURU_SENKRON_PLANI.md`
§4 — "(B) Paralel modül"): `Panel/modules/icra_core.py` **asla
düzenlenmez**, yalnız `import icra_core as _ic` ile kullanılır. Bu yüzden
§2'deki "boş ezmez" davranışı `save_taraf()`'ın İÇİNE yazılamaz — borçlu
kartı için ya `dosya_core.py` içinde yeni, ayrı bir kaydetme fonksiyonu
(`borclu_karti_kaydet` gibi) ya da `icra_models` tarafında bir yardımcı
fonksiyon olarak eklenmeli; `save_taraf()`'ın kendisi ve onu çağıran mevcut
dosya-taraf senkron akışı olduğu gibi kalır.

## 5. Açık sorular (kart yazılmaya başlanmadan önce karara bağlanacak)

- Borçlu kartı hangi ekranda açılacak (Dosya Görüntüle içinden mi, ayrı bir
  "Borçlularım" listesi mi)? Masaüstü+web parite kuralı geçerli
  ([[gorsel-birlestirme-kurali]]).
- Aynı borçlu birden fazla dosyada geçiyorsa (zaten `Taraf` TCKN/mersis_no
  ile TEKİL — `unique=True`) kart bu dosyaların hangi alt kümesini
  gösterecek?
- TCKN/vergi no/mersis no/DETSİS no/adres/IBAN gibi hassas ve kimlik
  alanları hangi kaynaktan (hangi .ajx uç noktası veya kullanıcı girişi)
  doldurulacak — bu uç nokta/akış henüz canlı doğrulanmadı (bkz. §3.1).
- `detsis_no` alanı için migration ne zaman yazılacak — kaynak/uç nokta
  netleşmeden şema kilitlenmesin diye, gerçek veri akışı doğrulanana kadar
  ertelenebilir.
- Kaynak önceliği (bkz. §2) somut olarak nasıl kodlanacak — alan başına mı,
  kaynak başına mı?

Bu sorular yanıtlanmadan borçlu kartı kaydetme mantığı yazılmaya
başlanmamalı; bu doküman yalnız §2'deki "boş ezmez" kuralını önceden
sabitlemek için var.
