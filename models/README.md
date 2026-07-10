# UYAP İcra — Veri Katmanı (Django ORM → PostgreSQL)

UYAP'tan çekilen icra verisini kendi PostgreSQL sunucumuza yazan katman.
Bu bir arayüz değildir; yalnızca **ORM + migration**.

> Not: Django app adı boşluk içeremez. Bu yüzden importlanabilir app **`icra_models`**
> klasöründedir; senin oluşturduğun boş `icra models` klasörü silinebilir.

## Yapı
```
models/
  manage.py              # Django yönetim aracı
  uyapdata/settings.py   # ayarlar (PostgreSQL bağlantısı UYAP_DB_* ortam değişkenlerinden)
  icra_models/
    models.py            # Birim, Taraf, Vekil, Dosya, DosyaTaraf
    ingest.py            # UYAP yanıtı -> upsert (kapak künyesi)
    migrations/0001_initial.py
```

## Veri modeli (rol bazlı, tekil taraf)
- **Birim** — İcra dairesi (birimId tekil).
- **Taraf** — gerçek/tüzel kişi; TCKN ya da MERSIS ile **tekil** (tekrar yazılmaz).
- **Vekil** — tarafın avukatı.
- **Dosya** — kapak künyesi: Birim + Yıl + Dosya No, durum (Açık/Kapalı), tür
  (Esas/Talimat), açılış tarihi. UYAP `dosyaId` tekil upsert anahtarı.
- **DosyaTaraf** — Dosya↔Taraf bağı: **rol = alacaklı/borçlu**, vekil. Zincir burada:
  - bir alacaklının tüm borçluları, bir borçlunun tüm dosyaları bu bağdan sorgulanır.

Şimdilik UYAP `search_phrase_detayli` yanıtı yalnızca **kapak künyesini** verir
(Dosya + Birim). Taraf/Vekil, dosya detay sorgusundan gelince doldurulacak.

## Kurulum / çalıştırma (venv ile, bu klasörden)
```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:UYAP_DB_NAME = "uyap_icra"; $env:UYAP_DB_USER = "postgres"
$env:UYAP_DB_PASSWORD = "..."; $env:UYAP_DB_HOST = "127.0.0.1"; $env:UYAP_DB_PORT = "5432"
python manage.py migrate
```
PostgreSQL'de önce veritabanı açılmalı:  `CREATE DATABASE uyap_icra;`

## Kullanım (kayıt yazma)
```python
from icra_models.ingest import kapak_kunyelerini_kaydet
yeni, guncel = kapak_kunyelerini_kaydet(records)   # records = yanıt veri[0]
```
