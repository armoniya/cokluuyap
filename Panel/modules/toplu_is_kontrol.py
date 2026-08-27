# -*- coding: utf-8 -*-
"""
Toplu İş Kontrolü — paylaşılan duraklat/durdur/karma çekirdeği (GUI + web ortak)
================================================================================
"Toplu SGK Sorgu" (sgk_core.SgkBatch) içindeki KANITLANMIŞ duraklat/durdur
deseni (_paused/_stop bayrakları + _bekle_devam kontrol noktası) burada
genelleştirilir; böylece Dosya Sorgulama, Barkod Sorgulama, Baro Pulu Makbuzu
İndirme gibi tüm toplu (Excel/aralık tarama) işler AYNI mekanizmayı kullanır.

Ayrıca iki yeni ihtiyacı karşılar:
  • KarmaSirasi   — iki (veya daha çok) toplu işi KATI SIRAYLA nöbetleştirir:
                    her işten sadece bir kayıt işlenip sıra diğerine geçer,
                    UYAP'a ASLA eşzamanlı istek gitmez (UYAP zaten eşzamanlı
                    isteği reddediyor — bkz. dosya_core._post_eszamanli_korumali).
  • AktifIsKayitDefteri — bir süreçte (masaüstü YA DA web sunucusu; ikisi ayrı
                    süreç olduğundan her biri kendi tekil kaydını tutar) o an
                    çalışan toplu iş(ler)i izler; yeni bir iş başlatılmak
                    istendiğinde çakışmayı haber verir.

Geriye dönük uyum: tüketici kod `kontrol=None` geçebilir; bu durumda
`nokta()`/`tur_bitti()` çağrıları etkisizdir (iş hiç duraklatılamaz/durdurulamaz
ama eskisi gibi normal çalışır).
"""

import threading
import time


class KarmaSirasi:
    """N adet TopluIsKontrolu'nu katı sırayla (round-robin) nöbetleştirir."""

    def __init__(self):
        self._katilimcilar = []
        self._sira = 0
        self._lock = threading.Lock()

    def katil(self, kontrol):
        with self._lock:
            if kontrol not in self._katilimcilar:
                self._katilimcilar.append(kontrol)

    def cikar(self, kontrol):
        with self._lock:
            if kontrol not in self._katilimcilar:
                return
            i = self._katilimcilar.index(kontrol)
            self._katilimcilar.remove(kontrol)
            if self._katilimcilar and self._sira > i:
                self._sira -= 1
            if self._katilimcilar:
                self._sira %= len(self._katilimcilar)

    def katilimci_sayisi(self):
        with self._lock:
            return len(self._katilimcilar)

    def sira_bende_mi(self, kontrol):
        with self._lock:
            if not self._katilimcilar or kontrol not in self._katilimcilar:
                return True
            return self._katilimcilar[self._sira % len(self._katilimcilar)] is kontrol

    def sirayi_ilerlet(self, kontrol):
        with self._lock:
            if not self._katilimcilar:
                return
            simdiki = self._katilimcilar[self._sira % len(self._katilimcilar)]
            if simdiki is kontrol:
                self._sira = (self._sira + 1) % len(self._katilimcilar)


class TopluIsKontrolu:
    """Tek bir toplu işin duraklat/durdur/karma durumunu tutan kontrol nesnesi.

    Kullanım (tüketici döngü içinde):
        for kayit in kayitlar:
            if kontrol and not kontrol.nokta():
                break
            ... kaydı işle ...
            if kontrol:
                kontrol.tur_bitti()
    """

    def __init__(self, ad="Toplu İş"):
        self.ad = ad
        self._paused = False
        self._stop = False
        self._karma = None
        self._bekleme_araligi = 0.3

    def sifirla(self):
        self._stop = False
        self._paused = False
        self._karma = None

    def duraklat(self):
        self._paused = True

    def devam_et(self):
        self._paused = False

    def toggle_pause(self):
        self._paused = not self._paused
        return self._paused

    def durdur(self):
        self._stop = True
        self._paused = False

    @property
    def paused(self):
        return self._paused

    @property
    def durduruldu(self):
        return self._stop

    def karmaya_kat(self, karma):
        self._karma = karma
        karma.katil(self)

    def karmadan_cik(self):
        if self._karma is not None:
            self._karma.cikar(self)
            self._karma = None

    def nokta(self, durum_fn=None):
        """Kontrol noktası. Duraklatılmışsa veya karma modda sırası
        gelmemişse bekler. Durdurulduysa False, aksi halde True döner."""
        while not self._stop:
            if self._paused:
                if durum_fn:
                    try:
                        durum_fn()
                    except Exception:
                        pass
                time.sleep(self._bekleme_araligi)
                continue
            if self._karma is not None and not self._karma.sira_bende_mi(self):
                time.sleep(min(self._bekleme_araligi, 0.15))
                continue
            return True
        return False

    def tur_bitti(self):
        """Karma modda: bir kayıt bitti, sırayı diğer işe devret."""
        if self._karma is not None:
            self._karma.sirayi_ilerlet(self)


def karmaya_baglan(yeni_kontrol, mevcut_kontroller):
    """`yeni_kontrol`'ü, halen çalışan `mevcut_kontroller` (ad->kontrol dict veya
    liste) ile paylaşılan tek bir KarmaSirasi'na bağlar. Zaten bir karmaya
    bağlı olan mevcut kontroller varsa aynı sıraya katılır (yeni bir tane
    açılmaz), aksi halde yeni bir KarmaSirasi oluşturulur."""
    if hasattr(mevcut_kontroller, "values"):
        mevcut_liste = list(mevcut_kontroller.values())
    else:
        mevcut_liste = list(mevcut_kontroller)

    karma = None
    for k in mevcut_liste:
        if getattr(k, "_karma", None) is not None:
            karma = k._karma
            break
    if karma is None:
        karma = KarmaSirasi()
        for k in mevcut_liste:
            k.karmaya_kat(karma)
    yeni_kontrol.karmaya_kat(karma)
    return karma


class AktifIsKayitDefteri:
    """Bir süreç içinde o an çalışan toplu iş(ler)in kaydı. Masaüstü ve web
    sunucusu ayrı Python süreçleri olduğundan her biri kendi tekil (module-level)
    KAYIT_DEFTERI nesnesini kullanır — bilerek paylaşılmaz."""

    def __init__(self):
        self._is_ler = {}
        self._lock = threading.Lock()

    def basvur(self, ad, kontrol):
        """Çakışma yoksa işi kaydeder ve ("ok", {}) döner. Çakışma varsa
        kaydetmeden ("cakisma", {ad: kontrol, ...}) döner — çağıran taraf
        kullanıcıya sıraya-koy/karma/iptal sorup sonra zorla_ekle çağırmalı."""
        with self._lock:
            if self._is_ler:
                return "cakisma", dict(self._is_ler)
            self._is_ler[ad] = kontrol
            return "ok", {}

    def zorla_ekle(self, ad, kontrol):
        with self._lock:
            self._is_ler[ad] = kontrol

    def sil(self, ad):
        with self._lock:
            self._is_ler.pop(ad, None)

    def anlik(self):
        with self._lock:
            return dict(self._is_ler)

    def bos_mu(self):
        with self._lock:
            return not self._is_ler


KAYIT_DEFTERI = AktifIsKayitDefteri()
