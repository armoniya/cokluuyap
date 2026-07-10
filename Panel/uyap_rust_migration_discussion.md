# UYAP Django Projesi Rust Dönüşüm ve Optimizasyon Tartışmaları

Bu doküman, projeyi Rust diline taşıma fizibilitesi, RAM/Hız darboğazları, veritabanı mimarileri ve UYAP entegrasyonu optimizasyonları üzerine yaptığımız tüm teknik görüşmeleri içermektedir.

---

## 1. Rust ile Yeniden Yazmak Mantıklı mı? (Hız Açısından)

### Mevcut Mimari Analizi
Projeniz; gömülü bir PostgreSQL veritabanı (`db_baslat.py`), Django veritabanı şeması (`models/`), Tkinter tabanlı bir masaüstü arayüzü (`panel.py`), WebRTC tabanlı P2P tünelleme (`uyap_app.py`) ve akıllı kart entegrasyonlu UDF e-imza imzalayıcı (`uyap_proxy.py`, `cades.py`) bileşenlerinden oluşmaktadır.

### Hız Darboğazları Nelerdir?
Sadece kullanıcı deneyimindeki işlem hızı için Rust'a geçmek **beklenen büyük hızlanmayı getirmeyecektir.** Çünkü projedeki ana yavaşlık unsurları CPU hızına değil, dış faktörlere bağlıdır:
1. **UYAP Sunucu Gecikmesi (Network I/O Bound):** İsteklerin hızı Ankara'daki UYAP sunucularının yanıt süresiyle ve internet hattıyla sınırlıdır.
2. **Akıllı Kart Hızı (Hardware Bound):** E-imza şifreleme işlemi fiziksel akıllı kartın donanımı içinde gerçekleşir. Kartın işlemcisi sabittir ve 1-3 saniye sürer.
3. **Legacy Word Çevirileri (COM Bound):** Eski `.doc` dosyalarını `.docx` yaparken arka planda MS Word'ün çağrılması Windows COM nesnelerine bağlıdır.

### Rust'ın Getireceği Gerçek Avantajlar
* **Kolay Dağıtım:** Python kurulumu ve paket bağımlılıkları olmadan, tek bir `.exe` dosyası ile dağıtım.
* **Çok Düşük Kaynak Tüketimi:** RAM kullanımını 10-20 kat azaltma (~30-50 MB RAM).
* **Modern Görsel Arayüz:** Tauri kullanarak HTML/CSS/JS tabanlı, GPU destekli akıcı bir UI.

---

## 2. RAM Tüketim Analizi (Neden ~400 MB Kaplıyor?)

Masaüstü bilgisayarlarında RAM'i şişiren 4 ana unsur tespit edilmiştir:
1. **Gömülü PostgreSQL (~100-150 MB):** Arka planda çalışan birden fazla yardımcı sunucu süreci.
2. **Django Framework (~80-100 MB):** Büyük web sunucu yapılandırması ve modellerin belleğe yüklenme maliyeti.
3. **Çift Python Çalışma Zamanı (Yinelenen ~60'ar MB):** Görsel arayüz (`panel.py`) ve tünel proxy servisinin (`uyap_app.py`) ayrı süreçlerde çalışması sebebiyle iki adet Python yorumlayıcısının (VM) RAM'e yüklenmesi.
4. **Yerel Kütüphaneler (`aiortc`, `cryptography` vb. - ~80 MB):** C tabanlı ağır uzantıların yüklenmesi.

---

## 3. İlişkisel Veritabanı ve Mimari Kararlar

Alacaklı $\rightarrow$ Borçlu $\rightarrow$ İcra Dosyası gibi birbirine zincirle bağlı karmaşık veritabanı şemalarında iki farklı senaryo değerlendirilmiştir:

### SQLite ile İlişkisel Veri Tutulabilir mi?
**Evet.** SQLite, PostgreSQL gibi tam ilişkisel (RDBMS) bir veritabanıdır. Foreign Key (Yabancı Anahtar) kısıtlamalarını, cascading (zincirleme silme) işlemlerini, Join ve transaction yapılarını eksiksiz destekler. Milyonlarca satırlık yerel verileri sıfır sunucu yüküyle çok hızlı işleyebilir.

### Ortak Ağ (Centralized DB) vs Yerel Ağ (Local DB)
1. **Senaryo A (Ortak Veritabanı - PostgreSQL Şart):** Ofisteki birden fazla bilgisayar aynı anda tek bir ortak veritabanına veri yazıp okuyacaksa PostgreSQL zorunludur (SQLite ağ üzerinden eşzamanlı yazmaya uygun değildir). 
   * *Optimizasyon:* PostgreSQL sunucusu **sadece tek bir merkez makinede** kurulur. Diğer kullanıcıların bilgisayarlarında sadece istemci çalışır. Böylece kullanıcı bilgisayarlarında PostgreSQL kaynaklı RAM kullanımı **0 MB** olur.
2. **Senaryo B (Yerel Veritabanı - SQLite En İyisi):** Her kullanıcı sadece kendi bilgisayarındaki verileri yönetecekse, SQLite hız, RAM tasarrufu ve sıfır kurulum maliyeti için en iyi alternatiftir.

---

## 4. ORM (İlişkisel Haritalama) Karşılaştırması

```
+------------------+-------------------------+--------------------+-----------------------------+
|    ORM Adı       |  Dil / Alt Yapı         |  Yaklaşık RAM Yükü |   Kullanım Amacı / Desen    |
+------------------+-------------------------+--------------------+-----------------------------+
|  Django ORM      |  Python (Ağır)          |    ~80-100 MB      | Web framework, Active Record|
|  SQLAlchemy      |  Python (Orta/Güçlü)    |    ~25-35 MB       | Esnek/Detaylı, Data Mapper  |
|  Peewee ORM      |  Python (Hafif)         |    ~5-10 MB        | Basit/Hızlı, Active Record  |
|  SeaORM          |  Rust (Ultra Hafif)     |    ~1-2 MB         | Asenkron/Güvenli, SeaORM    |
+------------------+-------------------------+--------------------+-----------------------------+
```

### SQLAlchemy'nin Çalışma Prensibi
SQLAlchemy, Django'dan farklı olarak **Data Mapper** deseniyle çalışır. Tablolarınız ve Python sınıflarınız bağımsızdır. `Session` nesnesi aracılığıyla veritabanı işlemlerini koordine eder. Zincirli ilişkileri çekmek için tek bir sorguda SQL JOIN oluşturan `joinedload` (Eager Loading) gibi gelişmiş teknikleri barındırır. Django ORM'e göre oldukça hafiftir.

---

## 5. UYAP İstek ve Sorgu Optimizasyon Önerileri

Ağ iletişiminde maksimum hızlanma için uygulanması gereken stratejiler:
1. **TCP/TLS Bağlantı Havuzu (Connection Pooling):** Her istekte yeni bağlantı kurup TLS el sıkışmasıyla (~300ms) vakit kaybetmemek için istemci nesnelerini (Python `httpx.AsyncClient` veya Rust `reqwest::Client`) singleton olarak tek bir kez oluşturup yeniden kullanın.
2. **HTTP/2 Desteği:** UYAP sunucularında HTTP/2 etkindir. Aynı bağlantı üzerinden çok sayıda asenkron isteğin eşzamanlı iletilebilmesi (multiplexing) için istemci ayarlarında HTTP/2'yi aktif edin.
3. **Canlı Tutma (Heartbeat):** Oturum çerezinin (`JSESSIONID`) düşmesini engellemek için arka planda her 3-4 dakikada bir UYAP'a hafif sorgular göndererek oturumu canlı tutun.
4. **Hızlı XML/HTML Ayrıştırma (Parsing):** UYAP'tan gelen büyük XML/HTML yanıtları işlerken standart Python kütüphaneleri yerine C-tabanlı **`lxml`** kullanın. Rust tarafında ise zero-copy prensipli **`quick-xml`** ve **`scraper`** kütüphaneleri hızı 20-30 kat artıracaktır.
5. **Akıllı Kart Belleği:** Sertifika bilgilerini kart takıldığında sadece bir kez okuyup önbelleğe alın, imzalama dışındaki her işlemde karta fiziksel sorgu atmayın.

---

*Tüm bu optimizasyonların teknik kodlama yönergelerini içeren bağımsız markdown dosyamıza buradan ulaşabilirsiniz:*  
👉 [uyap_optimization_recommendations.md](file:///C:/Users/KalkanHukuk/.gemini/antigravity-cli/brain/2412dbe2-9b88-454f-903c-b60c04b96959/uyap_optimization_recommendations.md)
