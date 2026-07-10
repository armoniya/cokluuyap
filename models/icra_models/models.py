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
    """İcra dairesi / birim — kapak künyesinin 'İcra Dairesi' parçası."""
    birim_id = models.CharField("UYAP Birim No", max_length=32, unique=True)
    ad = models.CharField("Birim Adı", max_length=255)
    turu1 = models.CharField("birimTuru1", max_length=8, blank=True)
    turu2 = models.CharField("birimTuru2", max_length=8, blank=True)   # 1101 = İcra Dairesi
    turu3 = models.CharField("birimTuru3", max_length=8, blank=True)
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Birim"
        verbose_name_plural = "Birimler"
        ordering = ["ad"]

    def __str__(self):
        return self.ad


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
        ACIK = 0, "Açık"
        KAPALI = 1, "Kapalı"

    class Tur(models.IntegerChoices):
        ESAS = 0, "Esas"
        TALIMAT = 1, "Talimat"

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
