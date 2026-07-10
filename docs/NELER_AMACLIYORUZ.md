# Neler Amaçlıyoruz? — Hedefler ve Yol Haritası

> Bu dosya geleceğe bakar: **nereye gidiyoruz, neden**.
> Kafan karıştığında buraya dön. Yeni fikir geldiğinde önce buraya yaz, sonra koda dokun.
> Kural: Her fikir ya bir hedefe hizmet eder ya da ayrı bir "sonra" listesine gider.

Tarih: 2026-06-26

---

## Ana Hedef (Tek Cümle)

> Avukatın UYAP'ta tek tek elle yaptığı tekrarlı işleri (sorgu, takip açma, dosya
> indirme, UDF işlemleri) **toplu ve otomatik** yapan, tek tıkla açılan, kurulum
> istemeyen bir masaüstü ürünü çıkarmak.

## 🎯 Ürün Modeli: Uygulama Mağazası (App Store)

> Bu, projenin nihai hedefi ve tüm modülerleştirmenin SEBEBİDİR.

Kullanıcı **çekirdek programı** kurar (boş bir kabuk + mağaza). Sonra:
- Mağazadan **sadece ihtiyacı olan özelliği** seçer (örn. "MTS Takip Açma").
- O özelliğin **ödemesini yapar** ve **yalnızca onu indirir**.
- Kullanmadığı özelliği indirmez, parasını ödemez.
- İstemediğinde özelliği **kaldırır** (ve ödemesi durur).

**Bunun teknik şartı:** Her özellik birbirinden tamamen bağımsız, kendi başına
yüklenip kaldırılabilen bir **eklenti (plugin)** olmalı. Bir özellik kaldırılınca
geri kalan program çalışmaya devam etmeli. İşte bu yüzden 3000 satırlık tek dosya
ölümcül — onu parçalara ayırmadan mağaza modeli imkânsız.

> Detaylı tasarım: [EKLENTI_MIMARISI.md](EKLENTI_MIMARISI.md)

---

## Neden Bu Proje Bitmiyordu? (Dürüst Teşhis)

- Fikirler geldikçe **büyük tek dosyalara** ekleniyordu → dosya şişti, dokunmak korkutucu hale geldi.
- "Ne yaptık / ne yapacağız" yazılı değildi → her oturumda baştan hatırlamaya çalışıyorduk.
- **Çözüm:** (1) Her özellik ayrı dosya. (2) Yazılı harita + hedef listesi (bu klasör).

---

## Yol Haritası — Öncelik Sırası

### Faz 1 — Düzen (şimdi)
- [ ] Dokümantasyon kur (bu klasör) ✅ başladı
- [ ] Büyük monolitleri tek tek modüllere böl (bkz. MODULER_YAPI_STANDARDI.md)
  - [~] `mts_takip_acan.py` (2956 → 725 st.) — BÜYÜK ÖLÇÜDE BÖLÜNDÜ
    - [x] Veri modelleri + yardımcılar → `mts_veri.py` (test edildi)
    - [x] Pencere yönetimi → `mts_pencere.py` (derlendi)
    - [x] XML/Excel dönüşüm → `mts_donusum.py` (test edildi)
    - [x] `indirmeyi_yakala` (indirme + imza) → `mts_indirme.py` (test edildi)
    - [x] `UyapBot` sınıfı (~1600 st.) → `mts_bot.py` (test edildi, api uyumlu)
    - [ ] Akış sınıfları (`KontrolDurumu`, `takip_ac`, istisnalar) → `mts_akis.py` (kalan 725 st.)
  - [ ] `mts_gui_api.py` (1774 st.)
  - [ ] `uyap_app.py` (1865 st.)
  - [ ] `sgk_sorgu_gui.py` (1343 st.)

### Faz 2 — Sağlamlaştırma
- [ ] `Çözülmesi gereken hatalar.md` içindeki hataları kapat
- [ ] Her modül için küçük bir "selftest" (panel.py'deki `--selftest` gibi)

### Faz 3 — Ürünleşme
- [ ] Tek `.exe` / kurulum paketi (kullanıcı python bilmesin)
- [ ] Lisans / hesap akışı (`accounts.py` üzerine)
- [ ] Kullanım kılavuzu (son kullanıcı için)

---

## Tasarım İlkeleri (Değişmez Kurallar)

1. **Arayüz ≠ Mantık.** Görsel kod `*.py`, iş mantığı `*_core.py`. Karıştırma.
2. **Her özellik ayrı dosya.** Yeni fikir = yeni dosya, mevcut dev dosyaya ekleme yok.
3. **MTS ve XML ayrı kalır.** Görsel olarak birleşseler bile teknik mantıkları
   birbirine karışmaz. (memory: "Görsel Birleştirme Kuralı")
4. **Birleştirme sadece görseldir.** Modülleri panele gömerken sadece dış kabuğa
   sar; içerideki çalışan mantığa dokunma.
5. **Kurulum istemez.** DB gömülü, python `.venv` içinde, tek tıkla açılır.

---

## Fikir Bekleme Odası (önce buraya, sonra koda)

> Aklına gelen ama henüz sırası olmayan fikirleri buraya yaz ki kodun içinde kaybolmasın.

- ...
- ...
