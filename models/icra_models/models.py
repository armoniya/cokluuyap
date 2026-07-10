# -*- coding: utf-8 -*-
"""
İcra veri modeli — kapak künyesi + taraf zinciri (rol bazlı, tekil taraf)
=========================================================================
Zincir (kullanıcının istediği sorgular):
  • Bir alacaklının TÜM borçluları  → alacaklı tarafın bulunduğu dosyalardaki
    borçlu taraflar.
  • Bir borçlunun TÜM dosyaları       → borçlu tarafın dosya bağları.
Bu, ayrı bir 'Alacaklı→Borçlu→Dosya' FK zinciri yerine TEK 'Taraf' tablosu
(gerçek/tüzel, TCKN ya da MERSIS/Vergi ile tekil) + 'DosyaTaraf' bağ tablosu
(rol = alacaklı/borçlu, vekil) ile sağlanır; aynı kişi tekrar yazılmaz.

Şimdilik UYAP 'search_phrase_detayli' yanıtı yalnızca KAPAK KÜNYESİNİ verir
(Dosya + Birim). Taraf/Vekil verisi dosya detay sorgusundan gelince doldurulur;
modeller şimdiden hazır.
"""
from django.db import models


class Birim(models.Model):
    """İcra dairesi / birim / mahkeme — kapak künyesinin birim kısmı.
    turu1/turu2/turu3, UYAP yanıt kaydının KENDİ 'birimTuru1/2/3' alanlarıdır
    (örn. icra için "11"/"1101"/"1199") — bunlar arama sorgusunda gönderilen
    'yargiTuru' parametresiyle AYNI ANLAMA GELMEZ (bkz.
    docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.6, ingest.py okunarak
    doğrulandı). Yargı türü (Ceza/Hukuk/İcra/... — search_phrase_detayli.ajx
    payload'ındaki 'birimTuru3') bu yüzden AYRI bir alanla tutulur."""
    birim_id = models.CharField("UYAP Birim No", max_length=32, unique=True)
    ad = models.CharField("Birim Adı", max_length=255)
    turu1 = models.CharField("birimTuru1", max_length=8, blank=True)
    turu2 = models.CharField("birimTuru2", max_length=8, blank=True)   # 1101 = İcra Dairesi
    turu3 = models.CharField("birimTuru3", max_length=8, blank=True)
    yargi_turu = models.PositiveSmallIntegerField(
        "Yargı Türü Kodu", null=True, blank=True, db_index=True)   # 0=Ceza,1=Hukuk,2=İcra,...
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Birim"
        verbose_name_plural = "Birimler"
        ordering = ["ad"]

    def __str__(self):
        return self.ad


class YargiBirimi(models.Model):
    """Yargı Birimi (mahkeme türü) referans listesi — yargiBirimleriSorgula_brd.ajx
    yanıtından (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.2). 'kod' alanı
    UYAP'ın 'tablo' alanıdır (arama sorgusunda birimTuru2 olarak gönderilir),
    'ad' ise UYAP'ın (ters adlandırılmış) 'kod' alanıdır (görünen isim)."""
    yargi_turu = models.PositiveSmallIntegerField("Yargı Türü Kodu", db_index=True)
    kod = models.CharField("Birim Türü Kodu (tablo)", max_length=8)
    ad = models.CharField("Birim Türü Adı", max_length=120)
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Yargı Birimi"
        verbose_name_plural = "Yargı Birimleri"
        ordering = ["yargi_turu", "ad"]
        constraints = [
            models.UniqueConstraint(fields=["yargi_turu", "kod"], name="uq_yargi_birimi"),
        ]

    def __str__(self):
        return f"{self.ad} ({self.kod})"


class SenkronKapsami(models.Model):
    """Kullanıcının senkronize edilmesini istediği (yargı türü, yargı birimi
    türü) kombinasyonu. Hiç kayıt yoksa hiçbir şey otomatik senkron edilmez —
    kullanıcı en az bir kombinasyon eklemeli. yargi_birimi_kod boşsa o yargı
    türünün TAMAMI kapsam dahilindedir."""
    yargi_turu = models.PositiveSmallIntegerField("Yargı Türü Kodu")
    yargi_birimi_kod = models.CharField("Yargı Birimi Kodu (tablo)", max_length=8, blank=True)
    aktif = models.BooleanField(default=True)
    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Senkron Kapsamı"
        verbose_name_plural = "Senkron Kapsamları"
        constraints = [
            models.UniqueConstraint(fields=["yargi_turu", "yargi_birimi_kod"],
                                    name="uq_senkron_kapsami"),
        ]

    def __str__(self):
        return f"Yargı Türü {self.yargi_turu} · {self.yargi_birimi_kod or 'tümü'}"


class Taraf(models.Model):
    """Alacaklı veya borçlu olabilen kişi/kurum. Kimlikle TEKİLdir; aynı kişi
    farklı dosyalarda tekrar yazılmaz (dosyalara DosyaTaraf ile bağlanır)."""

    class Tur(models.TextChoices):
        GERCEK = "gercek", "Gerçek Kişi"
        TUZEL = "tuzel", "Tüzel Kişi"

    tur = models.CharField(max_length=8, choices=Tur.choices)

    # — gerçek kişi —
    ad = models.CharField(max_length=120, blank=True)
    soyad = models.CharField(max_length=120, blank=True)
    tckn = models.CharField("TCKN", max_length=11, blank=True, null=True, unique=True)

    # — tüzel kişi —
    unvan = models.CharField("Unvan / Şirket Adı", max_length=400, blank=True)
    mersis_no = models.CharField("MERSIS No", max_length=20, blank=True, null=True, unique=True)
    vergi_no = models.CharField("Vergi No", max_length=15, blank=True, db_index=True)
    e_tebligat_adresi = models.CharField("E-Tebligat Adresi", max_length=255, blank=True)
    kep_adresi = models.CharField("KEP Adresi", max_length=255, blank=True)

    # — ortak —
    adres = models.TextField(blank=True)
    iban = models.CharField(max_length=34, blank=True)

    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Taraf"
        verbose_name_plural = "Taraflar"

    def __str__(self):
        if self.tur == self.Tur.TUZEL:
            return self.unvan or self.mersis_no or self.vergi_no or f"Taraf #{self.pk}"
        return (f"{self.ad} {self.soyad}".strip()
                or self.tckn or f"Taraf #{self.pk}")


class Vekil(models.Model):
    """Bir tarafın vekili (avukat). Tarafa DosyaTaraf üzerinden bağlanır."""
    ad = models.CharField(max_length=120)
    soyad = models.CharField(max_length=120, blank=True)
    tckn = models.CharField("TCKN", max_length=11, blank=True, null=True, unique=True)
    baro_sicil = models.CharField("Baro Sicil No", max_length=40, blank=True)
    adres = models.TextField(blank=True)
    iban = models.CharField(max_length=34, blank=True)
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vekil"
        verbose_name_plural = "Vekiller"

    def __str__(self):
        return f"{self.ad} {self.soyad}".strip() or f"Vekil #{self.pk}"


class Dosya(models.Model):
    """İcra dosyası kapak künyesi: İcra Dairesi + Yıl + Dosya No, durum, tür,
    açılış tarihi. KALICI tekil anahtar = (birim, yıl, sıra_no, tür_kod)
    [uq_dosya_kunye]. UYAP'ın 'dosyaId' değeri OTURUMLUKtur (her sorguda değişebilir),
    kimlik DEĞİLDİR; yalnız o oturumda taraf/detay çekmek için saklanır."""

    class Durum(models.IntegerChoices):
        """UYAP'ın kendi 'dosyaDurumKod' değerleri — OLDUĞU GİBİ saklanır
        (bkz. ingest.py, choices yalnız görüntüleme içindir). Kod anlamı
        yargı türüne göre değişebilir (bkz.
        docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §5) — burada yalnız canlı
        doğrulanan değerler var, tam liste değildir."""
        ACIK = 0, "Açık"
        KAPALI = 1, "Kapalı"
        KARARA_CIKMIS = 7, "Karara Çıkmış"
        ISTINAFTA = 29, "İstinafta"

    class Tur(models.IntegerChoices):
        """UYAP'ın kendi 'dosyaTurKod' değerleri — OLDUĞU GİBİ saklanır (bkz.
        ingest.py). Yalnız canlı doğrulanan değerler; tam liste değildir
        (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §5)."""
        ESAS = 0, "Esas"
        TALIMAT = 1, "Talimat"
        HUKUK_DEGISIK_IS = 14, "Hukuk Değişik İş Dosyası"
        HUKUK_DAVA = 15, "Hukuk Dava Dosyası"

    dosya_id = models.CharField("UYAP Dosya Kimliği (oturumluk)", max_length=255,
                                blank=True, db_index=True)
    birim = models.ForeignKey(Birim, on_delete=models.PROTECT, related_name="dosyalar")

    yil = models.PositiveIntegerField("Dosya Yılı")
    sira_no = models.PositiveIntegerField("Dosya No")
    dosya_no = models.CharField("Dosya No (ham)", max_length=20)   # "2025/237"

    durum_kod = models.IntegerField(choices=Durum.choices, default=Durum.ACIK)
    durum = models.CharField(max_length=20, blank=True)
    tur_kod = models.IntegerField(choices=Tur.choices, default=Tur.ESAS)
    tur = models.CharField(max_length=40, blank=True)

    acilis_tarihi = models.DateTimeField("Açılış Tarihi", null=True, blank=True)
    is_dava_dosyasi_acilmis = models.BooleanField(default=False)

    taraflar = models.ManyToManyField(Taraf, through="DosyaTaraf", related_name="dosyalar")

    son_senkron = models.DateTimeField(auto_now=True)
    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dosya"
        verbose_name_plural = "Dosyalar"
        ordering = ["-acilis_tarihi"]
        constraints = [
            models.UniqueConstraint(fields=["birim", "yil", "sira_no", "tur_kod"],
                                    name="uq_dosya_kunye"),
        ]

    def __str__(self):
        return f"{self.birim.ad} {self.yil}/{self.sira_no} ({self.get_tur_kod_display()})"


class DosyaTaraf(models.Model):
    """Dosya ↔ Taraf bağı: rol (alacaklı/borçlu) ve (varsa) vekil. Zincirin
    kalbi burasıdır."""

    class Rol(models.TextChoices):
        ALACAKLI = "alacakli", "Alacaklı"
        BORCLU = "borclu", "Borçlu"

    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="taraf_baglari")
    taraf = models.ForeignKey(Taraf, on_delete=models.PROTECT, related_name="dosya_baglari")
    rol = models.CharField(max_length=10, choices=Rol.choices)
    vekil = models.ForeignKey(Vekil, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="temsil_baglari")
    sira = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Dosya Tarafı"
        verbose_name_plural = "Dosya Tarafları"
        ordering = ["dosya", "rol", "sira"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "taraf", "rol"],
                                    name="uq_dosya_taraf_rol"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.get_rol_display()}: {self.taraf}"


class IcraTakipDetay(models.Model):
    """İcra/takip dosyasına özgü ayrıntı — dosyaAyrintiBilgileri_brd.ajx
    yanıtından (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.5/§2.5). Hukuk
    dava dosyalarında bu alanlar hiç YOK — bu yüzden ayrı bir model (ortak
    tek tabloda hep NULL kalacak alanlar yerine)."""
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="icra_detay")

    takibin_turu = models.CharField(max_length=8, blank=True)
    takibin_turu_aciklama = models.CharField(max_length=120, blank=True)
    takibin_sekli = models.CharField(max_length=8, blank=True)
    takibin_sekli_aciklama = models.CharField(max_length=255, blank=True)
    takibin_yolu = models.CharField(max_length=8, blank=True)
    takibin_yolu_aciklama = models.CharField(max_length=120, blank=True)

    alacak_kalemi_toplam = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    alacak_kalemi_faiz = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    takip_sonrasi_masraf = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vekalet_ucreti = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tahsil_harci = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    yapilmis_tahsilat = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "İcra Takip Detayı"
        verbose_name_plural = "İcra Takip Detayları"

    def __str__(self):
        return f"{self.dosya} · Takip Detayı"


class HukukDavaDetay(models.Model):
    """Hukuk dava/değişik iş dosyasına özgü ayrıntı —
    dosyaAyrintiBilgileri_brd.ajx yanıtından (bkz.
    docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.5/§2.5). İcra dosyalarında bu
    alanlar hiç YOK — bu yüzden ayrı bir model."""
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="hukuk_detay")

    dava_acilis_turu = models.CharField(max_length=120, blank=True)
    dava_turleri = models.CharField(max_length=500, blank=True)
    ilgili_dosya_listesi = models.CharField(max_length=500, blank=True)
    ilgili_dava_listesi = models.CharField(max_length=500, blank=True)
    ilgili_seri_dava_listesi = models.CharField(max_length=500, blank=True)
    birlesen_dosya_listesi = models.CharField(max_length=500, blank=True)
    durusma_tarihi = models.DateTimeField(null=True, blank=True)
    basvuruya_birakilma_tarihi = models.DateTimeField(null=True, blank=True)

    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hukuk Dava Detayı"
        verbose_name_plural = "Hukuk Dava Detayları"

    def __str__(self):
        return f"{self.dosya} · Dava Detayı"


class Evrak(models.Model):
    """Bir dosyaya ait evrak (karar, tebligat, dilekçe, dayanak vb.) önbellek
    kaydı. KALICI anahtar = (dosya, birim_evrak_no) [uq_dosya_evrak].
    UYAP'ın döndüğü evrakId/dosyaId/ggEvrakId OTURUMLUKTUR (canlı testte
    doğrulandı, bkz. docs/BELGE_ONBELLEK_PLANI.md §0.2) — yalnız o oturumda
    içerik indirmek için geçici kullanılır, burada SAKLANMAZ."""

    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="evraklar")
    ust_evrak = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                                  related_name="ekler")

    birim_evrak_no = models.PositiveIntegerField("Birim Evrak No", db_index=True)

    evrak_turu = models.CharField("Evrak Türü", max_length=64, blank=True)
    evrak_tip = models.CharField("Evrak Tipi", max_length=16, blank=True)
    aciklama = models.CharField("Açıklama", max_length=500, blank=True)
    evrak_tarihi = models.DateTimeField("Evrak Tarihi", null=True, blank=True)

    mime_turu = models.CharField("MIME Türü", max_length=64, blank=True)
    boyut = models.PositiveIntegerField("Boyut (bayt)", default=0)
    sha256 = models.CharField("SHA-256", max_length=64, db_index=True)

    dosya_yolu = models.CharField("Diskteki Dosya Yolu", max_length=500)
    degisebilir = models.BooleanField("Değişebilir Evrak", default=False)

    indirilme_zamani = models.DateTimeField(auto_now_add=True)
    son_erisim_zamani = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Evrak"
        verbose_name_plural = "Evraklar"
        ordering = ["dosya", "birim_evrak_no"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "birim_evrak_no"], name="uq_dosya_evrak"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.birim_evrak_no}"
