# -*- coding: utf-8 -*-
"""
Toplu İş Çakışma Diyaloğu — masaüstü
=====================================
Bir toplu iş (Dosya Sorgulama/Barkod Sorgulama/Toplu SGK/Baro Pulu Makbuzu vb.)
başlatılmak istendiğinde `toplu_is_kontrol.KAYIT_DEFTERI`'nde zaten çalışan
başka bir toplu iş varsa kullanıcıya üç seçenek sunar:
  • Sıraya Koy      — yürüyen iş bitince bu iş otomatik başlar
  • Karma Çalıştır  — iki iş KATI SIRAYLA nöbetleşir (UYAP'a asla eşzamanlı
                       istek gitmez — bkz. toplu_is_kontrol.KarmaSirasi)
  • İptal           — hiçbir şey yapılmaz

Motivasyon (kullanıcı bulgusu): "Dalgınlıkla iki işi birden yaptırayım dedim
program kafayı yedi" — UYAP zaten eşzamanlı isteği reddediyor
(dosya_core._post_eszamanli_korumali), bu yüzden gerçek paralel ÇALIŞTIRMA
seçeneği YOKTUR, yalnızca sıraya koyma ve katı nöbetleşme sunulur.
"""

import os
import sys
import tkinter as tk

# Bu dosyanın kendi klasörü (Panel/modules) — kardeş `toplu_is_kontrol`in bare
# içe aktarımı için sys.path'e eklenir; import SIRASI ne olursa olsun güvenli
# olsun diye (bkz. dosya_core.py'deki aynı desen) HER modül bunu KENDİSİ yapar.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from theme import C, RoundButton
from toplu_is_kontrol import KAYIT_DEFTERI, karmaya_baglan  # noqa: E402


def cakisma_sor(parent, calisan_adlari):
    """Modal diyalog gösterir; "sira" | "karma" | "iptal" döner (pencere
    kapanana kadar bekler — messagebox.askyesno gibi senkron)."""
    ad_metni = ", ".join(calisan_adlari) if calisan_adlari else "bilinmeyen bir iş"
    sonuc = {"deger": "iptal"}

    top = tk.Toplevel(parent)
    top.title("Yürümekte olan bir toplu iş var")
    top.configure(bg=C.BG)
    top.resizable(False, False)
    try:
        top.transient(parent)
        top.attributes("-topmost", True)
    except Exception:
        pass

    tk.Label(top, text=f"Şu anda çalışan bir toplu iş var: {ad_metni}",
             bg=C.BG, fg=C.INK, font=("Segoe UI", 11, "bold"),
             wraplength=380, justify="left").pack(padx=20, pady=(20, 6), anchor="w")
    tk.Label(top, text="Bu yeni işi nasıl başlatalım?\n\n"
                       "• Sıraya Koy — diğer iş bitince otomatik başlar.\n"
                       "• Karma Çalıştır — iki iş katı sırayla nöbetleşir "
                       "(bir kayıt o işten, bir kayıt bu işten); UYAP'a asla "
                       "eşzamanlı istek gitmez.",
             bg=C.BG, fg=C.INK_SOFT, font=("Segoe UI", 9),
             wraplength=380, justify="left").pack(padx=20, pady=(0, 16), anchor="w")

    btns = tk.Frame(top, bg=C.BG)
    btns.pack(padx=20, pady=(0, 20), fill="x")

    def sec(deger):
        sonuc["deger"] = deger
        top.destroy()

    RoundButton(btns, "Sıraya Koy", command=lambda: sec("sira"),
                kind="primary", height=34).pack(side="left", padx=(0, 8))
    RoundButton(btns, "Karma Çalıştır", command=lambda: sec("karma"),
                kind="ghost", height=34).pack(side="left", padx=(0, 8))
    RoundButton(btns, "İptal", command=lambda: sec("iptal"),
                kind="ghost", height=34).pack(side="left")

    top.protocol("WM_DELETE_WINDOW", lambda: sec("iptal"))
    top.update_idletasks()
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        tw, th = top.winfo_reqwidth(), top.winfo_reqheight()
        top.geometry(f"+{px + max(0, (pw - tw) // 2)}+{py + max(0, (ph - th) // 2)}")
    except Exception:
        pass
    top.grab_set()
    parent.wait_window(top)
    return sonuc["deger"]


def basvur_ile_cakisma_akisi(parent, ad, kontrol):
    """`KAYIT_DEFTERI.basvur` çağırır; çakışma varsa kullanıcıya sorar. Döner:
      "baslat" — hemen başla (çakışma yoktu YA DA kullanıcı 'Karma' seçti;
                 her iki durumda da kayıt defterine ZATEN eklenmiştir)
      "sirada" — kullanıcı 'Sıraya Koy' seçti; çağıran taraf
                 `sira_bekle_ve_baslat` ile boşalmayı beklemeli
      "iptal"  — kullanıcı vazgeçti, hiçbir şey yapma"""
    durum, mevcut = KAYIT_DEFTERI.basvur(ad, kontrol)
    if durum == "ok":
        return "baslat"
    secim = cakisma_sor(parent, list(mevcut.keys()))
    if secim == "karma":
        karmaya_baglan(kontrol, mevcut)
        KAYIT_DEFTERI.zorla_ekle(ad, kontrol)
        return "baslat"
    if secim == "sira":
        return "sirada"
    return "iptal"


def sira_bekle_ve_baslat(app, ad, kontrol, baslat_fn, durum_fn=None, interval_ms=1000):
    """Tkinter ana döngüsünü BLOKLAMADAN (app.after ile tekrarlanan kontrol)
    kayıt defteri boşalana kadar bekler, sonra kaydı alıp `baslat_fn()` çağırır.
    `durum_fn(metin)` her turda çağrılır (ör. bir etikete durum yazmak için)."""
    def tur():
        if KAYIT_DEFTERI.bos_mu():
            KAYIT_DEFTERI.zorla_ekle(ad, kontrol)
            baslat_fn()
            return
        if durum_fn:
            durum_fn("Sırada bekliyor… (yürüyen iş bitince otomatik başlayacak)")
        app.after(interval_ms, tur)
    tur()
