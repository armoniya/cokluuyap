# Proje Haritası — UYAP Çalışma Paneli

> Bu dosyanın amacı: "Bu klasörde ne nerede?" sorusuna 1 dakikada cevap vermek.
> Yeni bir fikir geldiğinde önce buraya bakıp **hangi parçaya** dokunacağını gör.

Tarih: 2026-06-26

---

## 1. Büyük Resim

Bu proje **tek bir program değil**, birbirinden yarı-bağımsız birkaç alt-uygulamanın
bir arada durduğu bir "monorepo"dur. Hepsini birbirine bağlayan ortak çatı:

- **Tek giriş kapısı:** `UYAP_Panel.bat` → `Panel/panel.py` (görsel kabuk).
- **Tek e-imza oturumu:** `Uyap Haricen Giriş/` UYAP'a bir kez girer, herkes onu kullanır.
- **Gömülü veritabanı:** `db_baslat.py` açılışta PostgreSQL'i otomatik ayağa kaldırır.

```
Kullanıcı çift tıklar
        │
   UYAP_Panel.bat
        │
   Panel/panel.py  ← GÖRSEL KABUK (sol menü + içerik + durum çubuğu)
        │
        ├── modules/baglanti.py      → "Uyap Haricen Giriş" oturumuna bağlanır (8800)
        ├── modules/udf.py           → UDF Converter'ı içine gömer
        ├── modules/sgk.py           → SGK Sorgu'yu içine gömer
        ├── modules/icra_dosyalarim.py → İcra dosyaları listeleme/sorgu
        ├── modules/xml_takip.py     → İcra/MTS takip açma
        ├── modules/logger.py        → Oturum loglayıcı
        └── modules/ayarlar.py       → Ayarlar
```

---

## 2. Klasör Klasör Ne Var?

### `Panel/`  ← ANA UYGULAMA (örnek alınması gereken mimari)
Tkinter masaüstü kabuğu. **Doğru yapılmış modüler örnek burası.**
- `panel.py` — Sadece görsel kabuk. İş mantığı YOK. (~1400 satır, ama büyük kısmı arayüz)
- `theme.py` — Renkler, butonlar, ortak görsel bileşenler.
- `modules/` — Her özellik ayrı dosya:
  - `*.py` (örn. `sgk.py`) — Görsel panel (arayüz).
  - `*_core.py` (örn. `sgk_core.py`) — O özelliğin iş mantığı (arayüzsüz).
  - Bu **arayüz / mantık ayrımı** projenin altın kuralıdır.
- `web/` — Panel'in web sunucu tarafı (port 8000).

### `Uyap Haricen Giriş/`  ← UYAP AĞ GEÇİDİ (kalp)
E-imza ile UYAP'a girer, oturumu canlı tutar, LAN (8800) + dış ağ (WebRTC) paylaşır.
- `uyap_app.py` — Birleşik masaüstü kontrol paneli (~1865 satır, **monolit**).
- `uyap_core/` — **İyi modülerleştirilmiş** çekirdek:
  - `uyap_proxy.py` — UYAP'a giden isteklerin vekili.
  - `office_agent.py` — Ofis (paylaşan) tarafı.
  - `home_client.py` — Uzaktan (alan) tarafı.
  - `jobs.py`, `job_handlers.py` — İş kuyruğu.
  - `udf_converter.py`, `udf_signer.py`, `cades.py`, `akis_pkcs11.py` — UDF & imza.
  - `mts/` — MTS'e özel işler (takip.py, sgk.py).
- `uyap_panel/` — Django tabanlı web paneli (ayrı bir ön yüz).
- `vendor_server.py` / `vendor_deploy/` — Bulut signaling sunucusu (WebRTC eşleştirme).
- `accounts.py` — Hesap/lisans.

### `Dosya Açılış/`  ← TOPLU DOSYA AÇMA
İki **ayrı** teknik (memory kuralı: MTS ve XML birbirine karışmaz):
- `MTS Takip Açılış/` — Playwright + masaüstü otomasyonu ile MTS takibi açar.
  - `mts_takip_acan.py` (~725 satır, bölündü) — kalan: akış/döngü (`takip_ac`, `KontrolDurumu`, `pdf_dayanak_tara`, `kaynaktan_takipler`).
    Çıkan modüller: `mts_veri.py`, `mts_pencere.py`, `mts_donusum.py`, `mts_indirme.py`, `mts_bot.py` (`UyapBot`).
  - `mts_gui_api.py` (~1774 satır, **monolit**) — GUI ↔ motor köprüsü.
  - `mts_takip_acan_api.py`, `mts_evrak_yukle.py`.
- `İcra Takip Açılış - XML/` — XML tabanlı icra takibi açma.
  - `Uyap_Xml_Takip_Açan.py`.

### `Sorgu/`  ← TOPLU SORGULAMA
- `SGK Sorgu/sgk_sorgu_gui.py` (~1343 satır, **monolit**) — Excel'den toplu SGK sorgusu.

### `UDF Converter GUI/`  ← UDF ↔ PDF DÖNÜŞTÜRÜCÜ
- `app.py` — Arayüz. `converter.py` / `signer.py` / `verify.py` / `cades.py` — mantık.
  (Burası da arayüz/mantık ayrımına sahip, iyi durumda.)

### `Logger/`
- `Uyap_session_logger.py` — UYAP oturumunu izleyip kaydeden loglayıcı.

### `models/`
- Django modelleri + `manage.py`. Veritabanı şeması burada.

### Kök dosyalar
- `db_baslat.py` — Gömülü PostgreSQL'i kurar/başlatır (Panel açılışta çağırır).
- `_pg_setup/` — PostgreSQL kurulum yardımcıları.
- `UYAP_Panel.bat` — Tek tıkla başlatıcı (.venv python'ı bulur).

---

## 3. Port Haritası (memory ile uyumlu)

| Port | Kim | Ne için |
|------|-----|---------|
| 8000 | Panel web | Panel'in kendi ön yüzü |
| 8800 | Uyap Haricen Giriş | Paylaşılan UYAP oturumu (LAN) |
| (bulut) | vendor_server | WebRTC signaling (dış ağ) |

---

## 4. Sağlık Durumu — Neresi Modüler, Neresi Değil?

| Parça | Durum | Not |
|-------|-------|-----|
| `Panel/` | ✅ İyi | İzlenecek örnek mimari |
| `Uyap Haricen Giriş/uyap_core/` | ✅ İyi | Çekirdek bölünmüş |
| `UDF Converter GUI/` | ✅ İdare eder | Arayüz/mantık ayrı |
| `uyap_app.py` | ⚠️ Monolit | 1865 satır — bölünebilir |
| `sgk_sorgu_gui.py` | ⚠️ Monolit | 1343 satır — bölünebilir |
| `mts_takip_acan.py` | 🟢 Bölündü | 2956 → 725 st. — `mts_veri.py` + `mts_pencere.py` + `mts_donusum.py` + `mts_indirme.py` + `mts_bot.py`(`UyapBot`) çıkarıldı; kalan: akış/döngü (sıradaki adım `mts_akis.py`) |
| `mts_gui_api.py` | 🔴 Monolit | 1774 satır |

> Detaylı plan için: [MODULER_YAPI_STANDARDI.md](MODULER_YAPI_STANDARDI.md)
