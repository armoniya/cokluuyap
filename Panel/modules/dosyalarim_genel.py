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
        ("yargi_turu_adi", "Yargı Türü", 80),
        ("birimAdi", "Yargı Birimi / Mahkeme", 220),
        ("dosyaNo", "Dosya No", 90),
        ("dosyaTur", "Dosya Türü", 110),
        ("dosyaDurum", "Durum", 70),
        ("acilisTarihi", "Açılış Tarihi", 90),
        ("taraf1", "Taraf 1", 160),
        ("taraf2", "Taraf 2", 160),
        ("taraf3", "Taraf 3", 160),
        ("taraf4", "Taraf 4", 160),
    ]

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.result_q = queue.Queue()
        self.kayitlar = []          # ekrandaki (filtrelenmiş) ham kayıtlar, iid -> index

        self.tur_var = tk.StringVar(value="Tümü")
        self.birim_var = tk.StringVar(value="Tümü")
        self.mahkeme_var = tk.StringVar(value="Tümü")
        self.dosya_tur_var = tk.StringVar(value="Tümü")
        self.durum_var = tk.StringVar(value="Tümü")
        self.tarih_bas_var = tk.StringVar()
        self.tarih_bit_var = tk.StringVar()
        self.taraf_var = tk.StringVar()

        self._tur_kod = {}          # etiket -> kod
        self._birim_kod = {}        # etiket -> kod (seçili türe göre yeniden doldurulur)
        # "Yargı Birimi" mahkeme TÜRÜNÜ süzer (ör. Asliye Hukuk Mahkemesi);
        # "Mahkeme" ise aynı türdeki BELİRLİ mahkemeyi süzer (ör. "ANKARA 4.
        # ASLİYE HUKUK MAHKEMESİ") — kullanıcı bulgusu, 2026-07-12: "yargı
        # türü ve yargı birimi var fakat mahkeme ile filtreleme yok". Yargı
        # Türü/Yargı Birimi seçimine göre kademeli doldurulur.
        self._mahkeme_kod = {}      # etiket (birim adı) -> birimId
        self._dosya_tur_kod = {}    # etiket -> kod (seçili türe göre yeniden doldurulur)
        self._dosya_turleri_tum = []  # filtresiz ("Tümü") tam liste — Temizle bunu geri yükler
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
        self.birim_cb.bind("<<ComboboxSelected>>", self._birim_degisti)
        self.mahkeme_cb = self._combo(ui, "Mahkeme", self.mahkeme_var, ["Tümü"])
        self.mahkeme_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.dosya_tur_cb = self._combo(ui, "Dosya Türü", self.dosya_tur_var, ["Tümü"])
        self.dosya_tur_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.durum_cb = self._combo(ui, "Durum", self.durum_var, ["Tümü"])
        self.durum_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())

        tk.Label(ui, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui, self.tarih_bas_var)
        tk.Label(ui, text="–", bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui, self.tarih_bit_var)

        tk.Label(ui, text="Taraf Adı", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        taraf_e = tk.Entry(ui, textvariable=self.taraf_var, bg="#FFFFFF", fg=C.INK, relief="flat",
                           insertbackground=C.INK, font=self.app.f_body, width=20,
                           highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        taraf_e.pack(side="left", ipady=3)
        taraf_e.bind("<Return>", lambda ev: self._filtrele())

        # ── düğmeler ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self._btn(bar, "Filtrele", self._filtrele, "primary").pack(side="left", ipadx=6)
        self._btn(bar, "Temizle", self._temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.yenile_btn = self._btn(bar, "Yenile (UYAP'tan Güncelle)", self._yenile, "ghost")
        self.yenile_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.yenile_tumu_btn = self._btn(bar, "Tüm Dosyaları Güncelle", self._yenile_tumu, "ghost")
        self.yenile_tumu_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── sonuç tablosu ──
        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(14, 0))
        # xsb ÖNCE pack edilir (side="bottom") ki alt kaydırma çubuğu kendi
        # alanını önce ayırsın; tree_area sonra kalan alanı doldurur (pack
        # sırası önemli — tersi olsaydı expand=True tree_area her yeri kaplar,
        # xsb'ye yer kalmazdı).
        xsb = tk.Scrollbar(card, orient="horizontal")
        xsb.pack(side="bottom", fill="x")
        tree_area = tk.Frame(card, bg=C.CARD)
        tree_area.pack(side="top", fill="both", expand=True)
        cols = [k for k, _l, _w in self.KOLONLAR]
        self.tree = ttk.Treeview(tree_area, columns=cols, show="headings", height=16)
        for k, lbl, w in self.KOLONLAR:
            self.tree.heading(k, text=lbl)
            # stretch=False: kolonlar kendi genişliğinde kalır, taraf
            # kolonları eklenince toplam genişlik kartın görünür alanını
            # aşar — bu YATAY KAYDIRMAYI TETİKLER (kullanıcı bulgusu,
            # 2026-07-12: eskiden yatay kaydırma çubuğu yoktu).
            self.tree.column(k, width=w, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        # Çift tık = "Dosya Görüntüle" düğmesiyle aynı işlem (kullanıcı
        # bulgusu, 2026-07-12: bir dosyaya çift tıklayınca ayrıntı açılsın).
        self.tree.bind("<Double-1>", lambda e: self._dosya_goruntule())
        ysb = tk.Scrollbar(tree_area, orient="vertical", command=self.tree.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        tree_area.grid_rowconfigure(0, weight=1)
        tree_area.grid_columnconfigure(0, weight=1)
        xsb.configure(command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

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
        self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(None, None)))
        self._filtrele_bg({})

    def _alanlari_doldur(self, turler, dosya_turleri, durumlar):
        self._tur_kod = {ad: kod for kod, ad in turler}
        self.tur_cb["values"] = ["Tümü"] + [ad for _k, ad in turler]
        self._dosya_turleri_tum = dosya_turleri   # "Tümü" seçiliyken (filtresiz) tam liste — Temizle bunu geri yükler
        self._dosya_tur_kod = {ad: kod for kod, ad in dosya_turleri}
        self.dosya_tur_cb["values"] = ["Tümü"] + [ad for _k, ad in dosya_turleri]
        self._durum_kod = {ad: kod for kod, ad in durumlar}
        self.durum_cb["values"] = ["Tümü"] + [ad for _k, ad in durumlar]

    def _tur_degisti(self, _event=None):
        self.birim_var.set("Tümü")
        self._birim_kod = {}
        self.birim_cb["values"] = ["Tümü"]
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self.dosya_tur_var.set("Tümü")
        secili = self.tur_var.get()
        kod = self._tur_kod.get(secili)

        def bg():
            try:
                birimler = dosya_core.yargi_birimleri_getir_veya_db(kod, self._log) if kod is not None else []
            except Exception:
                birimler = []
            self.result_q.put(("birimler", birimler))
            try:
                dosya_turleri = dosya_core.dosya_tur_secenekleri(kod)
            except Exception:
                dosya_turleri = []
            self.result_q.put(("dosya_turleri", dosya_turleri))
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(kod, None)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _birim_degisti(self, _event=None):
        """'Yargı Birimi' (mahkeme türü) değişince 'Mahkeme' listesi o türe
        göre daraltılarak yeniden yüklenir (kademeli — bkz. __init__ notu)."""
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        tur_kod = self._tur_kod.get(self.tur_var.get())
        birim_ad = self.birim_var.get()
        birim_kod = self._birim_kod.get(birim_ad) if birim_ad != "Tümü" else None

        def bg():
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(tur_kod, birim_kod)))
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
        mahkeme_ad = self.mahkeme_var.get()
        if mahkeme_ad != "Tümü":
            f["mahkeme_id"] = self._mahkeme_kod.get(mahkeme_ad)
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
        if self.taraf_var.get().strip():
            f["taraf_adi"] = self.taraf_var.get().strip()
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
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self.dosya_tur_var.set("Tümü")
        self._dosya_tur_kod = {ad: kod for kod, ad in self._dosya_turleri_tum}
        self.dosya_tur_cb["values"] = ["Tümü"] + list(self._dosya_tur_kod.keys())
        self.durum_var.set("Tümü")
        self.tarih_bas_var.set("")
        self.tarih_bit_var.set("")
        self.taraf_var.set("")

        def bg():
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(None, None)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _tabloyu_doldur(self, kayitlar):
        self.kayitlar = kayitlar
        self.tree.delete(*self.tree.get_children())
        for i, rec in enumerate(kayitlar):
            vals = [rec.get(k, "") for k, _l, _w in self.KOLONLAR]
            self.tree.insert("", "end", iid=str(i), values=vals)
        self.durum_lbl.config(text=f"{len(kayitlar)} dosya")

    # ─────────────────────────── Yenile (UYAP'tan Güncelle) ───────────────────────────
    def _yenile_baslat(self, **kwargs):
        self.yenile_btn.set_state("disabled")
        self.yenile_tumu_btn.set_state("disabled")
        self.durum_lbl.config(text="UYAP'tan güncelleniyor…")

        def bg():
            try:
                toplam, sonuclar = dosya_core.dosyalarim_yenile(self._log, **kwargs)
                self.result_q.put(("yenilendi", (toplam, sonuclar)))
            except Exception as e:
                self.result_q.put(("yenile_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def _yenile(self):
        """Ekonomik: yalnız seçili Yargı Türü/Birim taranır. Hiçbiri seçili
        değilse (Tümü/Tümü) SenkronKapsami'ye döner — TÜM türleri/birimleri
        taramak için 'Tüm Dosyaları Güncelle' kullanılmalı."""
        kwargs = {}
        tur_ad = self.tur_var.get()
        if tur_ad != "Tümü":
            kwargs["yargi_turu"] = self._tur_kod.get(tur_ad)
            birim_ad = self.birim_var.get()
            if birim_ad != "Tümü":
                kwargs["yargi_birimi_kod"] = self._birim_kod.get(birim_ad)
        self._yenile_baslat(**kwargs)

    def _yenile_tumu(self):
        self._yenile_baslat(tum_turler=True)

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

    def _sessiz_dialog(self, baslik, metin, hata=False):
        """messagebox.showinfo/showerror YERİNE: Windows'ta native mesaj
        kutusu ikonu (bilgi/hata) sistem sesi çalar — kullanıcı bulgusu,
        2026-07-12: çift tıkla sık açılan bu pencere ses çıkarmamalı. Kendi
        Toplevel'imiz Win32 MessageBox API'sini hiç çağırmadığından sessizdir."""
        top = tk.Toplevel(self.app)
        top.title(baslik)
        top.configure(bg=C.CARD)
        top.transient(self.app)
        top.resizable(False, False)

        renk = C.CLAY if hata else C.SAGE_DK
        tk.Label(top, text=baslik, bg=C.CARD, fg=renk, font=self.app.f_card_t,
                 anchor="w", justify="left").pack(anchor="w", padx=18, pady=(16, 6))
        satir_sayisi = metin.count("\n") + 2
        txt = tk.Text(top, bg=C.CARD, fg=C.INK, relief="flat", font=self.app.f_body,
                      wrap="word", width=64, height=min(max(satir_sayisi, 4), 22),
                      padx=4, pady=4, highlightthickness=0, state="normal")
        txt.insert("1.0", metin)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=18)
        self._btn(top, "Tamam", top.destroy, "primary").pack(pady=14)

        top.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - top.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - top.winfo_height()) // 2
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_set()

    def _detay_goster(self, sonuc):
        self.detay_btn.set_state("normal")
        ham, aile, kaydedildi, hata, taraflar = sonuc
        if hata:
            self.durum_lbl.config(text="Dosya ayrıntısı alınamadı")
            self._sessiz_dialog("Dosya Görüntüle", hata, hata=True)
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
        if taraflar:
            satirlar.append("")
            satirlar.append("Taraf Bilgileri:")
            for t in taraflar:
                satir = f"  {t.get('rol', '')}: {t.get('adi', '')}"
                if t.get("vekil"):
                    satir += f" — Vekil: {t['vekil'].strip('[]')}"
                satirlar.append(satir)
        self._sessiz_dialog(baslik, "\n".join(satirlar) +
                            ("\n\n(Yerel veritabanına kaydedildi.)" if kaydedildi else ""))
        if kaydedildi:
            # Kullanıcı bulgusu (2026-07-12): "Dosya Görüntüle" yeni taraf/ayrıntı
            # verisini DB'ye kaydediyordu ama listbox'ı hiç yenilemiyordu —
            # kullanıcı elle "Filtrele"ye basmadan yeni veriyi göremiyordu.
            self._filtrele()

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                # Her mesaj kendi try/except'i içinde işlenir — biri (ör.
                # _detay_goster) beklenmedik bir istisna atarsa bile döngünün
                # sonundaki self.app.after(300, self._poll) HER ZAMAN çalışır.
                # Aksi halde tek bir istisna zamanlayıcıyı kalıcı olarak
                # durdurur ve listbox bir daha HİÇ güncellenmez (kullanıcı
                # bulgusu, 2026-07-12).
                try:
                    if tip == "alanlar":
                        self._alanlari_doldur(*veri)
                    elif tip == "birimler":
                        self._birim_kod = {b.get("ad", b.get("kod", "")): b.get("kod", "") for b in veri}
                        self.birim_cb["values"] = ["Tümü"] + list(self._birim_kod.keys())
                    elif tip == "mahkemeler":
                        self._mahkeme_kod = {m.get("ad", m.get("birimId", "")): m.get("birimId", "") for m in veri}
                        self.mahkeme_cb["values"] = ["Tümü"] + list(self._mahkeme_kod.keys())
                    elif tip == "dosya_turleri":
                        self._dosya_tur_kod = {ad: kod for kod, ad in veri}
                        self.dosya_tur_cb["values"] = ["Tümü"] + list(self._dosya_tur_kod.keys())
                    elif tip == "kayitlar":
                        self._tabloyu_doldur(veri)
                    elif tip == "yenilendi":
                        self.yenile_btn.set_state("normal")
                        self.yenile_tumu_btn.set_state("normal")
                        toplam, sonuclar = veri
                        self.durum_lbl.config(text=f"✔ Güncellendi ({toplam} kayıt, {len(sonuclar)} kapsam).")
                        self._filtrele()
                    elif tip == "yenile_hata":
                        self.yenile_btn.set_state("normal")
                        self.yenile_tumu_btn.set_state("normal")
                        self.durum_lbl.config(text=f"Güncellenemedi: {veri}")
                    elif tip == "detay":
                        self._detay_goster(veri)
                    elif tip == "log":
                        self._append_log(str(veri))
                except Exception as e:
                    self._append_log(f"⚠️ Ekran güncellenemedi ({tip}): {e}")
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
