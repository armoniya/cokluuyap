# Neler Yaptık? — Tamamlanan İşler

> Bu dosya geçmişe bakar: bugüne kadar **ne kuruldu, ne çalışıyor**.
> Yeni biri (ya da 3 ay sonraki sen) projeye baktığında "buraya kadar gelmişiz" desin diye.

Tarih: 2026-06-26

---

## Çekirdek Altyapı

- ✅ **Tek tıkla başlatma** — `UYAP_Panel.bat` projeyi `.venv` python'ı ile açar.
- ✅ **Gömülü PostgreSQL** — `db_baslat.py` açılışta veritabanını otomatik kurar,
  başlatır ve migrate eder. Kullanıcı hiçbir şey kurmaz. (`%LOCALAPPDATA%\UyapIcra`)
- ✅ **Tek e-imza oturumu** — `Uyap Haricen Giriş` bir kez UYAP'a girer; LAN (8800)
  ve dış ağ (WebRTC) üzerinden aynı oturum paylaşılır.

## Panel (Ana Arayüz)

- ✅ **Görsel kabuk** — Sol menü + içerik alanı + durum çubuğu (`panel.py`).
- ✅ **Modüler panel sistemi** — Her özellik `modules/` altında ayrı dosya, arayüz
  (`*.py`) ve mantık (`*_core.py`) birbirinden ayrı.
- ✅ Bağlanma, UDF, SGK, İcra Dosyalarım, Logger, Ayarlar panelleri gömülü.

## Özellik Modülleri

- ✅ **UDF ↔ PDF dönüştürücü** — E-imzalı (CAdES / PKCS#11 akıllı kart) dönüştürme.
- ✅ **SGK Toplu Sorgu** — Excel'den okuyup her borçlu için 7 SGK sorgusu (Kamu/SSK/
  Bağkur, çalışan/emekli + iş yeri), sonuçları `*_yapilanlar.xlsx` dosyalarına yazma.
- ✅ **İcra Dosyalarım** — UYAP'tan dosya arama/listeleme (birim+yıl+sıra kimliği).
- ✅ **MTS Takip Açma** — Playwright + masaüstü otomasyonu ile toplu takip açma.
- ✅ **İcra XML Takip Açma** — XML tabanlı toplu takip açma (MTS'ten ayrı teknik).
- ✅ **Oturum Loglayıcı** — UYAP oturum trafiğini kaydetme.

## Ağ / Paylaşım

- ✅ **LAN paylaşımı** — Ofis bilgisayarı oturumu 0.0.0.0:8800'de paylaşır.
- ✅ **Dış ağ paylaşımı** — Bulut signaling + WebRTC tüneli ile uzaktan erişim.
- ✅ **Adil sıralama / okuma önceliği** — Okuma istekleri yazma kilidini atlar
  (`X-Uyap-Read` başlığı), ofis yazarken bile sorgular akar.

---

## Bilinen Açık Konular

> Detay için bu mevcut dosyalara bak:
> - `Panel/Çözülmesi gereken hatalar.md`
> - `Panel/Sonra Eklenecekler.md`
> - `Uyap Haricen Giriş/YAPILACAKLAR.md`
