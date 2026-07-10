# Eklenti (Plugin) ve Uygulama Mağazası Mimarisi

> Hedef: Kullanıcı çekirdek programı kurar; her özelliği **mağazadan ayrı ayrı**
> satın alıp indirir, istemediğinde kaldırır. Özellikler birbirinden bağımsızdır.

Tarih: 2026-06-26 · Durum: TASARIM (henüz uygulanmadı)

---

## 1. Neden Bu Mimari?

Mağaza modelinin tek şartı var: **her özellik tek başına kurulup kaldırılabilmeli.**
Bir özellik silinince program çökmemeli; kurulunca program değişmeden tanımalı.
Bu yüzden:

- ❌ 3000 satırlık tek dosya → bir özelliği ayıramazsın, satamazsın.
- ✅ Her özellik = kendi klasöründe, standart arayüzlü, bağımsız bir paket.

> Modülerleştirme (büyük dosyaları bölme) bu hedefin ön hazırlığıdır.

---

## 1.5. İKİ SEVİYE: App ≠ İç Modül (KARIŞTIRMA)

Bu projede **iki ayrı bölünme** var. Aynı şey değiller:

| | **App (satılır eklenti)** | **İç modül / çekirdek özelliği (satılmaz)** |
|---|---|---|
| Nedir? | Kullanıcının fark ettiği, tek başına işe yarayan özellik | Appların ortak kullandığı altyapı parçası |
| Örnek | MTS Takip Açma · SGK Sorgu · İcra XML Takip · UDF Dönüştürücü | indirmeyi yakalama · pencere yönetimi · veri modelleri · UYAP oturumu · e-imza · tema |
| Mağazada? | ✅ Ayrı satılır / kurulur / kaldırılır | ❌ Satılmaz; app'in içinde ya da çekirdekte gelir |
| Dosya | `eklentiler/<ad>/` klasörü + manifest.json | Sadece bir `.py` modülü (örn. `mts_indirme.py`) |

**Kritik:** Şu an yaptığımız `.py`'ye bölme (bakım kolaylığı) ile app sınırı (mağaza)
**aynı şey değil**. Bir app birden çok iç modülden oluşur.

- `mts_takip_acan.py` → bölündüğünde çıkan `mts_veri.py`, `mts_pencere.py`,
  `mts_donusum.py`, `mts_indirme.py`, `mts_akis.py`, `UyapBot` **hiçbiri ayrı app değildir.**
  Hepsi tek bir app'in ("MTS Takip Açma") iç parçalarıdır.
- Bir iç modül birden çok app tarafından kullanılıyorsa (örn. indirmeyi yakalama,
  pencere yönetimi, UYAP oturumu) → o **çekirdeğe** ait olur, app'e değil.
- Bir iç modül sadece tek app'e özelse (örn. `mts_donusum.py` yalnızca MTS) →
  o app'in klasörü içinde kalır.

> Soru sırası: önce "bu kullanıcının satın alacağı bir şey mi?" (app) → değilse
> "kaç app kullanıyor?" (1 ise app-içi, çok ise çekirdek).

---

## 2. Mevcut Temel (Sıfırdan Başlamıyoruz)

Zaten elimizde eklenti altyapısının çekirdeği var:

- `Panel/modules/uretilmis_runner.py` — Bir modülü **dinamik** yükler:
  `importlib.import_module(f"modules.{core_modul}")`, sonra `core.PARAMETRELER` ile
  girdi kutularını kurar ve `core.calistir(girdi, log)` ile çalıştırır.
  → Yani **standart arayüzlü** (`PARAMETRELER` + `calistir`) her modül, ekstra GUI
  yazmadan çalışıyor. Bu, bir eklenti sözleşmesinin (contract) ta kendisi.
- `Panel/modules/uretilmis_moduller.json` — Üretilmiş modüllerin **kaydı** (registry).
  → Mağaza katalogunun/kurulu eklenti listesinin temeli.
- `Panel/panel.py` — Sol menüyü modüllerden kuran kabuk. Menü, kurulu eklentilere
  göre dinamik üretilebilir.

> Sonuç: "App Store"u sıfırdan icat etmiyoruz; bu üç parçayı genelleştiriyoruz.

---

## 3. Bir Eklenti Neye Benzer? (Hedef Yapı)

Her özellik tek bir klasör olur. Örnek:

```
eklentiler/
  mts_takip_acma/             ← BİR APP (mağazada satılır)
    manifest.json             ← kimlik, sürüm, fiyat, bağımlılıklar
    core.py                   ← MANTIK: PARAMETRELER + calistir()  (arayüzsüz)
    panel.py                  ← (isteğe bağlı) özel arayüz; yoksa UretilmisRunner kullanılır
    requirements.txt          ← bu eklentinin python bağımlılıkları (playwright vb.)
    ic/                       ← bu app'e ÖZEL iç modüller (ayrı app DEĞİL)
      mts_veri.py
      mts_donusum.py
      uyap_bot.py             ← (UyapBot bölündüğünde)
```

Ortak iç modüller (birden çok app kullanıyorsa) **app'in içinde değil, çekirdekte**:

```
cekirdek/
  indirme.py                  ← indirmeyi yakalama (tüm program kullanır)
  pencere.py                  ← pencere yönetimi
  uyap_oturum.py              ← UYAP e-imza oturumu
  tema.py · runner.py
```

### manifest.json (sözleşme)
```json
{
  "ad": "MTS Takip Açma",
  "kimlik": "mts_takip_acma",
  "surum": "1.0.0",
  "fiyat": { "tip": "aylik", "tutar": 0 },
  "giris": "core.py",
  "menu_grubu": "Dosya Açılış",
  "baglimoduller": ["uyap_oturum"]
}
```

### Eklenti sözleşmesi (her core.py sağlamalı)
- `PARAMETRELER` — girdi alanlarının tanımı (UretilmisRunner bunu okur).
- `calistir(girdi, log)` — işi yapan tek giriş noktası.
- (Opsiyonel) `kur()` / `kaldir()` — kurulum/temizlik kancaları.

---

## 4. Çekirdek Programın Görevleri

| Görev | Nasıl |
|-------|-------|
| Mağaza kataloğu | Sunucudan eklenti listesi + fiyat çek |
| Satın alma / lisans | `accounts.py` üzerine; eklenti kimliği başına yetki |
| İndirme | Eklenti klasörünü `eklentiler/` altına aç |
| Yükleme | `manifest.json` oku → menüye ekle → `importlib` ile core'u tanı |
| Kaldırma | Klasörü sil → menüden çıkar → lisansı bırak |
| İzolasyon | Bir eklenti çökse de çekirdek ayakta kalsın (try/except sınırları) |

---

## 5. Yol Haritası (Mağazaya Giden Adımlar)

1. **[şimdi] Modülerleştirme** — Büyük dosyaları bağımsız parçalara böl.
   Her özelliğin mantığı arayüzünden ayrılana kadar mağaza mümkün değil.
2. **Eklenti sözleşmesini netleştir** — `PARAMETRELER` + `calistir` standardını
   `uretilmis_runner` üzerinden tüm özelliklere yay.
3. **manifest.json kur** — Her özelliğe kimlik/sürüm/fiyat ekle.
4. **Yerel eklenti yükleyici** — `eklentiler/` klasörünü tarayıp menüyü dinamik kur.
5. **Lisans kapısı** — `accounts.py` ile "bu kullanıcı bu eklentiye sahip mi?".
6. **Mağaza ön yüzü + sunucu** — İndir/satın al/kaldır akışı (en son).

> İlke: Önce **yerelde** "kur/kaldır/çalıştır" mükemmel çalışsın; ödeme/sunucu en sona.

---

## 6. Bu Mimariyi Bozmayan Kurallar

- Her özellik **kendi klasöründe**; başka eklentinin dosyasına dokunmaz.
- Ortak kod (UYAP oturumu, tema, runner) **çekirdekte**, eklentide değil.
- Eklenti **çekirdeği import edebilir**, çekirdek eklentiyi sadece sözleşmeyle tanır.
- MTS/XML gibi teknik olarak ayrı işler **ayrı eklenti** olur (memory kuralı korunur).
