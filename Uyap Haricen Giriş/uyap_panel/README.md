# UYAP Ağ Geçidi — Arayüz Katmanı (`uyap_panel`)

Mevcut `uyap_app.py` (tkinter/ttk) ile **aynı çekirdeği** kullanan iki yeni ön yüz:

| Sürüm | Çalıştırma | Nerede açılır |
|------|-----------|----------------|
| **ttkbootstrap GUI** (masaüstü) | `.venv\Scripts\python.exe uyap_panel\run_gui.py` | Ayrı pencere |
| **Django web** (yerel panel) | `.venv\Scripts\python.exe uyap_panel\web\run_web.py` | http://127.0.0.1:**8000** |

> Tüm komutlar **`Uyap Haricen Giriş`** klasöründen, sanal ortam (`.venv`) ile çalıştırılır.

## Portlar — önemli
- **8000** → kontrol paneli arayüzü (yalnızca Django sürümü).
- **8800** → UYAP oturumu (e-imza tüneli). Bu port **dokunulmadan** kalır; "Tarayıcıyı Aç"
  düğmesi yine `http://127.0.0.1:8800/giris` açar. 8800 boş olmalı ki tünel bağlanabilsin.

## Mimari
```
uyap_panel/
  core/                 ← UI-bağımsız servis katmanı (her iki ön yüz bunu çağırır)
    config.py           ayar dosyası + DPAPI şifreleme + IP/port yardımcıları
    auth.py             vendor giriş doğrulama (wss) + /api/office + /api/reset
    connection.py       ConnectionManager: PAYLAŞ (office_agent) / AL (home_client)
                        yaşam döngüsü; ayrı thread + asyncio; çıktı günlüğü tamponu
    takip.py            TakipService: XML/Excel ayrıştır + iş kuyruğuna gönder/sorgula/onayla
    icra_jobs.py        "icra_takip_ac" iş türü (İcra backend uzantı noktası)
  gui/app.py            ttkbootstrap masaüstü paneli
  web/                  Django projesi (uyap_web) + panel uygulaması
  run_gui.py            GUI başlatıcı
  web/run_web.py        Django başlatıcı (--noreload, tek süreç)
```

Çekirdek, `uyap_core` paketini (UYAP iş mantığı) sarmalar. UI katmanında **iş mantığı yoktur**.

## Özellikler (her iki sürümde)
- **Giriş** — bulut hesabı (kullanıcı adı/parola) ile, `vendor-uyap.onrender.com` üzerinden doğrulama.
- **Bağlantı**
  - *Paylaş (Ofis)*: e-imza PIN + sertifika ID ile UYAP oturumu kurar; LAN + dış ağ paylaşır.
  - *Al (İstemci)*: paylaşılan oturuma bağlanır (önce LAN, olmazsa WebRTC).
  - *Tarayıcıyı Aç*: dağıtılan/alınan oturumu kendi tarayıcısında açar (`:8800/giris`).
  - Canlı işlem günlüğü.
- **İcra Takip (XML)** — klasik İcra Takip Talebi; `icra_takip_ac` işi.
- **MTS Takip** — Merkezi Takip Sistemi; mevcut `coklu_takip_ac` işi (hazır backend).

Her iki takip akışı da: dosya **bu bilgisayarda** ayrıştırılır → canlı UYAP oturumu üzerinden
açılır → onay (Onaysız / Her takipte / Toplu önizle) bu ekranda alınır.

## İcra backend durumu
MTS akışı (`uyap_core.mts`) **hazırdır**. İcra için arayüz, iş kuyruğu, onay ve ilerleme
**tamamen kuruludur**; yalnızca UYAP klasik-icra uçları henüz yazılmadı. Eklendiğinde:

```
uyap_core/icra/takip.py   → async def prepare(ctx, takip, *, il, adliye)
                             async def finalize(ctx, takip, state, *, vekalet, dayanak)
uyap_core/icra/models.py  → (opsiyonel) icra'ya özel takip modeli; yoksa mts modeli kullanılır
```
yazılınca İcra sekmesi otomatik çalışır. Backend yokken iş, net bir
"İcra backend'i henüz eklenmedi" mesajıyla biter (arayüz akışı yine de test edilebilir).

## İlk kurulum
```
.venv\Scripts\python.exe -m pip install ttkbootstrap django pandas openpyxl
```
Django için **veritabanı/migration gerekmez** (oturumlar dosya tabanlı: `web/.sessions/`).

## Notlar
- Kayıtlı kullanıcı/parola, `uyap_app.py` ile **aynı** `uyap_app_config.json` dosyasında
  tutulur; üç arayüz aynı ayarı paylaşır (parola DPAPI ile şifreli).
- Django paneli yereldir; dışa açmayın (e-imza/smartcard sadece yerel makinede çalışır).
