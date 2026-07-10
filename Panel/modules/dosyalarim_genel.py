# -*- coding: utf-8 -*-
"""
Modül: Dosyalarım (Tümü)
=========================
Yalnız İcra'ya değil, `SenkronKapsami`'nin (bkz. senkron_kapsami.py) kapsadığı
TÜM yargı türü/birimindeki dosyaları tek ekranda yargı türü/birimi/dosya
türü/durum/açılış tarihi aralığına göre filtreleyerek listeler (bkz.
docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 Faz 6). `icra_dosyalarim.py`'nin
AKSİNE bu ekran canlı UYAP sorgusu YAPMAZ — yalnız yerel DB'yi okur
(`dosya_core.dosyalarim_db_listele`); "Yenile" düğmesi arka planda
`DosyaSorgu.calistir`'i (arka plan zamanlayıcısıyla AYNI mantık) HEMEN
çalıştırıp DB'yi tazeler. Çalışma mantığı TAMAMEN `dosya_core.py`'dedir;
`icra_dosyalarim.py`'ye KESİNLİKLE dokunulmaz (bkz. plan §4 kararı — kanıtlı
modülü değiştirme, paralel yaz).
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from theme import C, RoundButton
from . import dosya_core


class DosyalarimGenelPanel:
    KOLONLAR = [
        ("yargi_turu_adi", "Yargı Türü", 110),
        ("birimAdi", "Yargı Birimi / Mahkeme", 220),
        ("dosyaNo", "Dosya No", 90),
        ("dosyaTur", "Dosya Türü", 160),
        ("dosyaDurum", "Durum", 100),
        ("acilisTarihi", "Açılış Tarihi", 100),
    ]

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.result_q = queue.Queue()
        self.kayitlar = []          # ekrandaki (filtrelenmiş) ham kayıtlar, iid -> index

        self.tur_var = tk.StringVar(value="Tümü")
        self.birim_var = tk.StringVar(value="Tümü")
        self.dosya_tur_var = tk.StringVar(value="Tümü")
        self.durum_var = tk.StringVar(value="Tümü")
        self.tarih_bas_var = tk.StringVar()
        self.tarih_bit_var = tk.StringVar()

        self._tur_kod = {}          # etiket -> kod
        self._birim_kod = {}        # etiket -> kod (seçili türe göre yeniden doldurulur)
        self._dosya_tur_kod = {}
        self._durum_kod = {}

        self._build()
        threading.Thread(target=self._alanlar_yukle_bg, daemon=True).start()
        self.app.after(200, self._poll)

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="Dosyalarım (Tümü)", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Senkron Kapsamı'nda seçilen tüm yargı türü/birimlerindeki "
                 "dosyalar. Bu liste yerel veritabanından gelir; UYAP'tan tazelemek "
                 "için “Yenile”'yi kullanın.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=760,
                 justify="left").pack(anchor="w", pady=(6, 14))

        # ── filtre çubuğu ──
        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        self.tur_cb = self._combo(ui, "Yargı Türü", self.tur_var, ["Tümü"])
        self.tur_cb.bind("<<ComboboxSelected>>", self._tur_degisti)
        self.birim_cb = self._combo(ui, "Yargı Birimi", self.birim_var, ["Tümü"])
        self.birim_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.dosya_tur_cb = self._combo(ui, "Dosya Türü", self.dosya_tur_var, ["Tümü"])
        self.dosya_tur_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.durum_cb = self._combo(ui, "Durum", self.durum_var, ["Tümü"])
        self.durum_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())

        tk.Label(ui, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui, self.tarih_bas_var)
        tk.Label(ui, text="–", bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui, self.tarih_bit_var)

        # ── düğmeler ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self._btn(bar, "Filtrele", self._filtrele, "primary").pack(side="left", ipadx=6)
        self._btn(bar, "Temizle", self._temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.yenile_btn = self._btn(bar, "Yenile (UYAP'tan Güncelle)", self._yenile, "ghost")
        self.yenile_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── sonuç tablosu ──
        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(14, 0))
        cols = [k for k, _l, _w in self.KOLONLAR]
        self.tree = ttk.Treeview(card, columns=cols, show="headings", height=16)
        for k, lbl, w in self.KOLONLAR:
            self.tree.heading(k, text=lbl)
            self.tree.column(k, width=w, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        ysb = tk.Scrollbar(card, command=self.tree.yview)
        ysb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=ysb.set)

        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(10, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=3,
                           padx=12, pady=6, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)

    def _combo(self, parent, label, var, values):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", values=values,
                          font=self.app.f_body, width=16)
        cb.pack(side="left", padx=(0, 18))
        return cb

    def _tarih_entry(self, parent, var):
        e = tk.Entry(parent, textvariable=var, bg="#FFFFFF", fg=C.INK, relief="flat",
                    insertbackground=C.INK, font=self.app.f_body, width=11,
                    highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda ev: self._filtrele())
        return e

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind, font=self.app.f_nav_b, height=36)

    # ─────────────────────────── günlük ───────────────────────────
    def _log(self, mesaj):
        self.result_q.put(("log", str(mesaj)))

    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ─────────────────────────── veri yükleme ───────────────────────────
    def _alanlar_yukle_bg(self):
        try:
            turler = dosya_core.YARGI_TURLERI
            dosya_turleri = dosya_core.dosya_tur_secenekleri()
            durumlar = dosya_core.dosya_durum_secenekleri()
        except Exception as e:
            self._log(f"⚠️ Alanlar yüklenemedi: {e}")
            return
        self.result_q.put(("alanlar", (turler, dosya_turleri, durumlar)))
        self._filtrele_bg({})

    def _alanlari_doldur(self, turler, dosya_turleri, durumlar):
        self._tur_kod = {ad: kod for kod, ad in turler}
        self.tur_cb["values"] = ["Tümü"] + [ad for _k, ad in turler]
        self._dosya_tur_kod = {ad: kod for kod, ad in dosya_turleri}
        self.dosya_tur_cb["values"] = ["Tümü"] + [ad for _k, ad in dosya_turleri]
        self._durum_kod = {ad: kod for kod, ad in durumlar}
        self.durum_cb["values"] = ["Tümü"] + [ad for _k, ad in durumlar]

    def _tur_degisti(self, _event=None):
        self.birim_var.set("Tümü")
        self._birim_kod = {}
        self.birim_cb["values"] = ["Tümü"]
        secili = self.tur_var.get()
        kod = self._tur_kod.get(secili)
        if kod is not None:
            def bg():
                try:
                    birimler = dosya_core.yargi_birimleri_db_den_yukle(kod)
                except Exception:
                    birimler = []
                self.result_q.put(("birimler", birimler))
            threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _filtreleri_topla(self):
        f = {}
        tur_ad = self.tur_var.get()
        if tur_ad != "Tümü":
            f["yargi_turu"] = self._tur_kod.get(tur_ad)
        birim_ad = self.birim_var.get()
        if birim_ad != "Tümü":
            f["yargi_birimi_kod"] = self._birim_kod.get(birim_ad)
        dt_ad = self.dosya_tur_var.get()
        if dt_ad != "Tümü":
            f["tur_kod"] = self._dosya_tur_kod.get(dt_ad)
        durum_ad = self.durum_var.get()
        if durum_ad != "Tümü":
            f["durum_kod"] = self._durum_kod.get(durum_ad)
        if self.tarih_bas_var.get().strip():
            f["tarih_baslangic"] = self.tarih_bas_var.get().strip()
        if self.tarih_bit_var.get().strip():
            f["tarih_bitis"] = self.tarih_bit_var.get().strip()
        return f

    def _filtrele(self):
        self.durum_lbl.config(text="Yükleniyor…")
        self._filtrele_bg(self._filtreleri_topla())

    def _filtrele_bg(self, filtreler):
        def bg():
            try:
                kayitlar = dosya_core.dosyalarim_db_listele(filtreler)
            except Exception as e:
                self._log(f"⚠️ Liste okunamadı: {e}")
                kayitlar = []
            self.result_q.put(("kayitlar", kayitlar))
        threading.Thread(target=bg, daemon=True).start()

    def _temizle(self):
        self.tur_var.set("Tümü")
        self.birim_var.set("Tümü")
        self.birim_cb["values"] = ["Tümü"]
        self.dosya_tur_var.set("Tümü")
        self.durum_var.set("Tümü")
        self.tarih_bas_var.set("")
        self.tarih_bit_var.set("")
        self._filtrele()

    def _tabloyu_doldur(self, kayitlar):
        self.kayitlar = kayitlar
        self.tree.delete(*self.tree.get_children())
        for i, rec in enumerate(kayitlar):
            vals = [rec.get(k, "") for k, _l, _w in self.KOLONLAR]
            self.tree.insert("", "end", iid=str(i), values=vals)
        self.durum_lbl.config(text=f"{len(kayitlar)} dosya")

    # ─────────────────────────── Yenile (UYAP'tan Güncelle) ───────────────────────────
    def _yenile(self):
        self.yenile_btn.set_state("disabled")
        self.durum_lbl.config(text="UYAP'tan güncelleniyor…")

        def bg():
            try:
                toplam, sonuclar = dosya_core.dosyalarim_yenile(self._log)
                self.result_q.put(("yenilendi", (toplam, sonuclar)))
            except Exception as e:
                self.result_q.put(("yenile_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    # ─────────────────────────── Dosya Görüntüle ───────────────────────────
    def _dosya_goruntule(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden bir dosya seçin.")
            return
        try:
            rec = self.kayitlar[int(secim[0])]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satır bulunamadı; listeyi yenileyin.")
            return
        self.detay_btn.set_state("disabled")
        self.durum_lbl.config(text="Dosya ayrıntısı alınıyor…")
        threading.Thread(target=self._dosya_goruntule_bg, args=(rec,), daemon=True).start()

    def _dosya_goruntule_bg(self, rec):
        sonuc = dosya_core.dosya_detay_goster_ve_kaydet(rec, log_fn=self._log)
        self.result_q.put(("detay", sonuc))

    def _detay_goster(self, sonuc):
        from tkinter import messagebox
        self.detay_btn.set_state("normal")
        ham, aile, kaydedildi, hata = sonuc
        if hata:
            self.durum_lbl.config(text="Dosya ayrıntısı alınamadı")
            messagebox.showerror("Dosya Görüntüle", hata)
            return
        self.durum_lbl.config(text="Dosya ayrıntısı kaydedildi")
        if aile == "icra":
            satirlar = [
                f"Takibin Türü: {ham.get('takibinTuru', '') or '—'}",
                f"Takibin Şekli: {ham.get('takibinSekli', '') or '—'}",
                f"Takibin Yolu: {ham.get('takibinYolu', '') or '—'}",
                f"Alacak Kalemi Toplam: {ham.get('alacakKalemToplamTutar', '') or '—'}",
                f"Vekalet Ücreti: {ham.get('vekaletUcreti', '') or '—'}",
                f"Tahsil Harcı: {ham.get('tahsilHarci', '') or '—'}",
            ]
            baslik = "Dosya Bilgileri — İcra Takip"
        elif aile == "hukuk":
            satirlar = [
                f"Dava Açılış Türü: {ham.get('davaAcilisTuru', '') or '—'}",
                f"Dava Türleri: {ham.get('davaTurleriStr', '') or '—'}",
                f"İlgili Dava Listesi: {ham.get('ilgiliDavaListesiStr', '') or '—'}",
                f"Duruşma Tarihi: {ham.get('durusmaTarihi', '') or '—'}",
            ]
            baslik = "Dosya Bilgileri — Hukuk Dava"
        else:
            satirlar = ["Bu yargı türü için henüz ayrıntı görüntüleme desteklenmiyor."]
            baslik = "Dosya Bilgileri"
        messagebox.showinfo(baslik, "\n".join(satirlar) +
                             ("\n\n(Yerel veritabanına kaydedildi.)" if kaydedildi else ""))

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                if tip == "alanlar":
                    self._alanlari_doldur(*veri)
                elif tip == "birimler":
                    self._birim_kod = {b.get("ad", b.get("kod", "")): b.get("kod", "") for b in veri}
                    self.birim_cb["values"] = ["Tümü"] + list(self._birim_kod.keys())
                elif tip == "kayitlar":
                    self._tabloyu_doldur(veri)
                elif tip == "yenilendi":
                    self.yenile_btn.set_state("normal")
                    toplam, sonuclar = veri
                    self.durum_lbl.config(text=f"✔ Güncellendi ({toplam} kayıt, {len(sonuclar)} kapsam).")
                    self._filtrele()
                elif tip == "yenile_hata":
                    self.yenile_btn.set_state("normal")
                    self.durum_lbl.config(text=f"Güncellenemedi: {veri}")
                elif tip == "detay":
                    self._detay_goster(veri)
                elif tip == "log":
                    self._append_log(str(veri))
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
