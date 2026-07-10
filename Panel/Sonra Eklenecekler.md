# UYAP & Çoklu UYAP — Sonra Eklenecekler ve Kararlar

Bu döküman, panel geliştirme yol haritasını ve UX/UI tarafında alınan son kararları içerir.

---

## A. Son Alınan UX/UI Kararları (Öncelikli)

### 1. `cokluuyap.com` Giriş Ekranı Sadeleştirmesi
* Giriş ekranındaki 3 veri giriş alanı (Ofis Kodu, Kullanıcı Adı, Şifre) yerine **2 alanlı tasarım** kullanılacaktır.
* Kullanıcılar doğrudan tek bir alana **`kullaniciadi@ofisadı`** yazarak ve şifrelerini girerek giriş yapacaktır (Ofis adı `@` işaretinden sonraki kısımdan otomatik çözümlenecektir).

### 2. Giriş Sonrası Akıllı Pano (Dashboard) Tasarımı
* Giriş yapıldığında dönen yükleme simgeli ekran tamamen kaldırılacaktır. Yerine ana sayfa panosu yüklenecektir.
* **Bağlantı Yoksa:** Panoda *"aktif/paylaşılan bağlantı yok, bağlantı kurmak için tıklayın"* ibaresi yer alacaktır.
* **Bağlantı Kurma Yetkisi:** Bağlantı kurma (paylaşma) yetkisi sadece `master` (ofis sahibi) kullanıcıya özel olmayacak, tüm kullanıcılar (üyeler dahil) bu işlemi görebilecek/tetikleyebilecektir.

### 3. Akıllı Ajan (Server) Durum Kontrolü ve Yönlendirmeler
Eğer sistemde aktif bağlantı yoksa, sistem arka planda ofisin geçmiş durumunu ve yerel cihazı kontrol ederek şu yönlendirmeleri yapacaktır:
* **Senaryo 1: Ajan daha önce hiç çalıştırılmamışsa:**
  * Ekranda *"bağlantı kurup dağıtmak için programı indirin"* uyarısı ve indirme butonu yer alacaktır.
* **Senaryo 2: Ajan daha önce çalışmış ama şu an çevrimdışıysa:**
  * Ekranda **"Server'ı Bağlat"** butonu bulunacaktır (Server'ın tekrar bağlantı kurmayı denemesini tetikler).
  * Yeniden bağlanma başarısız olursa, hatanın *"İnternet veya elektrik kesintisi"* nedeniyle olduğu arayüzde belirtilecektir.
  * Bu ekranda ayrıca yerel makinedeki program varlığı (ping ile) taranacaktır:
    * Program yüklü değilse: Programı indirme seçeneği gösterilecektir.
    * Program yüklü/çalışıyorsa: Doğrudan yerel bağlanma ekranı/akışı sunulacaktır.

### 4. Master Kullanıcı Arayüzü Güncellemesi
* Master (ofis sahibi) kullanıcının alt kullanıcı ekleme/çıkarma yetkilerini yönetebileceği menü doğrudan **ana menüye (main menu)** eklenecektir.

### 5. Uygulama Mağazası Varsayılan Durumu (App Store)
* Uygulama mağazasındaki tüm isteğe bağlı modüller ve eklenti öğeleri varsayılan olarak **kapalı (disabled/off)** gelecektir. Kullanıcı talep ederse aktif edecektir.

---

## B. Planlanan Diğer Özellikler (Yol Haritası)

1) e-tebligat menüsü eklenecek.
2) tahsilat-reddiyat modülü eklenecek. Alacaklı ve borçlu bazlı tahsilat reddiyat raporlanacak.
3) Ödeme işlemlerinden veri alıp alacaklı filtresiyle masraf raporlama yapılacak.
4) Ayarlara toplu veri güncelleme saati eklenecek.
5) Dosya aktif/pasif özelliği (Yapılandırma, işlem, arama).
6) Dosya Arama kısmında yıl girdikten sonra otomatik olarak bir sonraki kısıma tıklanması sağlanacak.
7) Udf dönüştürücüde sürükle-bırak özelliği eklenecek.

---
*İmzalamadan sonra pencere küçülmüyor hatası düzeltilecek.*