# Modüler Yapı Standardı — Büyük Dosyayı Nasıl Böleriz?

> Amaç: Bir daha asla 3000 satırlık tek dosya olmasın. Her özellik kendi dosyasında,
> arayüzü mantığından ayrı dursun. Bu, projeyi ürünleştirmenin ön şartı.

Tarih: 2026-06-26

---

## Altın Kural: Arayüz / Mantık Ayrımı

`Panel/modules/` bunu **zaten doğru yapıyor** — örnek olarak onu alıyoruz:

```
modules/
  sgk.py        ← ARAYÜZ: sadece görsel (butonlar, tablo, tıklama)
  sgk_core.py   ← MANTIK: arayüzsüz iş (sorgu çalıştırma, veri işleme)
```

Arayüz dosyası mantığı **çağırır**, ama mantık dosyası arayüzü **bilmez**.
Böylece mantığı bozmadan görseli değiştirebilir, ya da aynı mantığı başka yerde
(örn. web panelinde) tekrar kullanabilirsin.

---

## Bir Monoliti Bölme Tarifi (Adım Adım)

> ÖNEMLİ: Bu işi **küçük adımlarla** yap. Her adımdan sonra program hâlâ açılıyor mu
> diye çalıştır. Tek seferde her şeyi bölmeye çalışmak, projeyi yine çıkmaza sokar.

1. **Sınırları çiz.** Dosyayı oku, "burası şu işi yapıyor" diyebileceğin blokları işaretle.
   (örn. mts_takip_acan.py'de: pencere yönetimi / playwright akışı / veri okuma / yazma)
2. **En bağımsız bloğu seç.** Başka koda en az bağlı olan parçayı ilk çıkar.
3. **Yeni dosyaya taşı.** Örn. `mts_pencere.py` aç, o fonksiyonları oraya kopyala.
4. **import ile bağla.** Eski dosyada o fonksiyonları sil, üste `from mts_pencere import ...` ekle.
5. **Çalıştır.** Program açılıyor ve özellik bozulmadıysa → bir sonraki bloğa geç.
6. Dosya yeterince küçülene kadar tekrarla. Hedef: bir dosya **tek bir konuyu** anlatsın.

---

## İsimlendirme Kuralı

| Ne | Nasıl adlandır | Örnek |
|----|----------------|-------|
| Görsel panel | `ozellik.py` | `sgk.py` |
| İş mantığı | `ozellik_core.py` | `sgk_core.py` |
| Yardımcı/küçük parça | açıklayıcı ad | `mts_pencere.py` |
| Ortak araçlar | `_` ile başlat | `_runtime.py` |

---

## Önerilen Bölme Planı (Monolitler İçin)

### `mts_takip_acan.py` (2956 st.) → tahmini bölünme
- `mts_pencere.py` — Windows pencere yönetimi (win32gui/win32con işleri)
- `mts_tarayici.py` — Playwright tarayıcı akışı
- `mts_veri.py` — Veri okuma/eşleştirme
- `mts_takip_acan.py` (kalan) — Akışı yöneten ince çekirdek

### `mts_gui_api.py` (1774 st.)
- Arayüz çizimi ↔ motor çağrıları olarak ikiye ayır (GUI / API katmanı zaten isminde var).

### `uyap_app.py` (1865 st.)
- "PAYLAŞ (Ofis)" ve "AL (İstemci)" rolleri zaten ayrı → iki dosyaya bölünebilir.
- Çoğu mantık zaten `uyap_core/` içinde; `uyap_app.py` ince bir kabuğa indirgenebilir.

### `sgk_sorgu_gui.py` (1343 st.)
- `sgk_sorgu_gui.py` (arayüz) + `sgk_sorgu_core.py` (Excel + sorgu mantığı) ayrımı.
- NOT: `Panel/modules/sgk_core.py` ile mantığı paylaşabilir mi? Önce kontrol et,
  kod tekrarını önle.

> ⚠️ Bu bir öneridir, emir değil. Gerçek sınırlar dosyayı okuyunca netleşir.
> Her bölmeden önce ilgili memory kurallarını (MTS/XML ayrımı) gözet.
