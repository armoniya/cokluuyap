# -*- coding: utf-8 -*-
"""
Modül: Hukuk Dosyalarım
========================
`dosyalarim_genel.py`'nin ("Dosyalarım (Tümü)") Hukuk'a (yargı türü=1) SABİT
kılınmış, sadeleştirilmiş bir eşi — `icra_dosyalarim.py`'nin İcra'ya paralel
ayrı modülü gibi. Çalışma mantığı TAMAMEN `dosya_core.py`'dedir (bu modül
yalnız arayüz); DB'yi okur (`dosyalarim_db_listele`), canlı UYAP sorgusu
YALNIZ "Yenile" ile (`dosyalarim_yenile`, arka plan zamanlayıcısıyla AYNI
mantık) tetiklenir. İcra'ya özgü alanlar (Alacaklı/Borçlu, Kesinleşme/Tebliğ
Durumu, Barkod Sorgu) BİLEREK YOK — kullanıcı isteği (2026-08-27): yalnız
Mahkeme/Dosya No/Dosya Türü/Durum/Açılış Tarihi/Davacı/Davalı.

"Seçilenlerin Tüm Belgelerini İndir" (2026-08-27, kullanıcı isteği — Faz 2):
`evrak_indirici.calistir`'e sarmalayıcı. Seçili her dosya için kendi alt
klasörüne TÜM evrakları orijinal formatında indirir + tek bir birleştirilmiş
PDF üretir (bkz. evrak_indirici.py modül başlığı — canlı doğrulanmış format
davranışı). Yalnız masaüstünde: bu, kullanıcının KENDİ dosya sistemine yazan
yerel bir işlem — web sürümü sunucunun disk'ine yazardı, bu yüzden BİLEREK
yalnız burada."""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

import openpyxl

from theme import C, RoundButton
from . import dosya_core

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesnesini görmesi için şart (bkz.
# dosyalarim_genel.py başlığındaki AYNI gerekçe): bu iki modül HER YERDE
# bare import edilir, aksi halde `modules.toplu_is_kontrol` ile
# `toplu_is_kontrol` sys.modules'te AYRI kopyalar olur.
_HERE_TIK = os.path.dirname(os.path.abspath(__file__))
if _HERE_TIK not in sys.path:
    sys.path.insert(0, _HERE_TIK)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402

from . import evrak_indirici

_LOG_DOSYA_YOLU = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "UyapIcra",
    "hukuk_dosyalarim.log")


class _EvrakListesiPaneli:
    """"Dosya Görüntüle" penceresindeki "Evraklar" sekmesi (kullanıcı isteği,
    2026-08-27: "bu evrakları o pencerede görüntüleyebileceğim ve checkbox
    ile indirip indirmemeye karar vereceğim bir hale getir"). Dosyanın TÜM
    evrak listesini gösterir, her satırda tık-ile-değişen bir ☐/☑ işareti
    taşır (ttk.Treeview'ın kendi checkbox'ı YOK, bu yüzden metin karakteriyle
    simüle edilir). "Görüntüle" seçili TEK satırı OS'un varsayılan
    programıyla açar (`evrak_indirici.evrak_onizle`); "Seçilenleri İndir ve
    Birleştir" işaretli TÜM satırları bir klasöre indirip tek PDF'te
    birleştirir (`evrak_indirici.evrak_kumesini_indir_ve_birlestir`).

    Kendi bağımsız kuyruğu/`after` döngüsünü tutar (ana panelin
    `result_q`/`_poll`'unu PAYLAŞMAZ) — aynı anda birden çok "Dosya
    Görüntüle" penceresi açık kalabildiğinden (bkz. HukukDosyalarimPanel
    başlığı), mesajları doğru pencereye yönlendirmenin en basit yolu budur."""

    def __init__(self, parent, top, app, rec, log_fn):
        self.top = top
        self.app = app
        self.rec = rec
        self.dosya_id = rec.get("dosyaId")
        self.log_fn = log_fn
        self.q = queue.Queue()
        self.evraklar = []
        self.secili = set()
        self._build(parent)
        threading.Thread(target=self._yukle_bg, daemon=True).start()
        self.top.after(200, self._poll)

    def _build(self, parent):
        bar = tk.Frame(parent, bg=C.CARD)
        bar.pack(fill="x", padx=12, pady=(10, 4))
        RoundButton(bar, "Tümünü Seç", command=self._tumunu_sec, kind="ghost",
                    font=self.app.f_nav_b, height=30).pack(side="left")
        RoundButton(bar, "Tümünü Kaldır", command=self._tumunu_kaldir, kind="ghost",
                    font=self.app.f_nav_b, height=30).pack(side="left", padx=(6, 0))
        self.goruntule_btn = RoundButton(bar, "Görüntüle", command=self._goruntule, kind="ghost",
                                          font=self.app.f_nav_b, height=30)
        self.goruntule_btn.pack(side="left", padx=(14, 0))
        self.indir_btn = RoundButton(bar, "Seçilenleri İndir ve Birleştir", command=self._indir_birlestir,
                                      kind="primary", font=self.app.f_nav_b, height=30)
        self.indir_btn.pack(side="left", padx=(6, 0))

        self.durum = tk.Label(parent, text="Evrak listesi yükleniyor…", bg=C.CARD, fg=C.INK_SOFT,
                               font=self.app.f_small)
        self.durum.pack(anchor="w", padx=12, pady=(0, 4))

        cols = ("sec", "tur", "no", "tarih")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=10)
        self.tree.heading("sec", text="")
        self.tree.heading("tur", text="Tür")
        self.tree.heading("no", text="Evrak No")
        self.tree.heading("tarih", text="Onay Tarihi")
        self.tree.column("sec", width=32, anchor="center", stretch=False)
        self.tree.column("tur", width=340, anchor="w", stretch=True)
        self.tree.column("no", width=90, anchor="w", stretch=False)
        self.tree.column("tarih", width=110, anchor="w", stretch=False)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.tree.bind("<Button-1>", self._tiklandi)

    def _yukle_bg(self):
        if not self.dosya_id:
            self.q.put(("hata", "dosyaId yok — önce 'Genel Bilgiler' sekmesinin yüklenmesi gerekiyor."))
            return
        try:
            ham = dosya_core.evrak_listesi_getir(self.dosya_id, log_fn=self.log_fn)
            evraklar = evrak_indirici._evrak_listesi_duzlestir(ham)
        except Exception as e:
            self.q.put(("hata", str(e)))
            return
        self.q.put(("yuklendi", evraklar))

    def _doldur(self, evraklar):
        self.evraklar = evraklar
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(evraklar):
            self.tree.insert("", "end", iid=str(i),
                              values=("☐", e.get("tur", ""), e.get("birimEvrakNo", ""),
                                      e.get("onaylandigiTarih", "")))
        self.durum.config(text=f"{len(evraklar)} evrak — işaretlemek için ☐ kutusuna tıklayın.")

    def _tiklandi(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        kolon = self.tree.identify_column(event.x)
        if kolon != "#1":  # yalnız 'sec' sütununa tıklama işaretler; diğerleri normal satır seçimi
            return
        idx = int(iid)
        evrak_id = self.evraklar[idx].get("evrakId")
        if evrak_id in self.secili:
            self.secili.discard(evrak_id)
            self.tree.set(iid, "sec", "☐")
        else:
            self.secili.add(evrak_id)
            self.tree.set(iid, "sec", "☑")

    def _tumunu_sec(self):
        for i, e in enumerate(self.evraklar):
            self.secili.add(e.get("evrakId"))
            self.tree.set(str(i), "sec", "☑")

    def _tumunu_kaldir(self):
        self.secili.clear()
        for i in range(len(self.evraklar)):
            self.tree.set(str(i), "sec", "☐")

    def _goruntule(self):
        secim = self.tree.selection()
        if not secim:
            self.durum.config(text="Önce listeden bir satır seçin (satıra tıklayın).")
            return
        try:
            evrak = self.evraklar[int(secim[0])]
        except (ValueError, IndexError):
            return
        self.goruntule_btn.set_state("disabled")
        self.durum.config(text=f"'{evrak.get('tur', '')}' açılıyor…")
        threading.Thread(target=self._goruntule_bg, args=(evrak,), daemon=True).start()

    def _goruntule_bg(self, evrak):
        try:
            evrak_indirici.evrak_onizle(self.dosya_id, evrak, self.log_fn)
            self.q.put(("goruntulendi", None))
        except Exception as e:
            self.q.put(("goruntule_hata", str(e)))

    def _indir_birlestir(self):
        if not self.secili:
            self.durum.config(text="Önce en az bir evrak işaretleyin (☐ kutusuna tıklayın).")
            return
        hedef = filedialog.askdirectory(title="Belgelerin indirileceği klasörü seçin")
        if not hedef:
            return
        secilenler = [e for e in self.evraklar if e.get("evrakId") in self.secili]
        self.indir_btn.set_state("disabled")
        self.durum.config(text=f"{len(secilenler)} evrak indiriliyor…")
        threading.Thread(target=self._indir_bg, args=(secilenler, hedef), daemon=True).start()

    def _indir_bg(self, secilenler, hedef_kok):
        try:
            birim_adi = self.rec.get("birimAdi", "")
            dosya_no = self.rec.get("dosyaNo", "")
            klasor_adi = f"{evrak_indirici._guvenli_ad(birim_adi)} {evrak_indirici._guvenli_ad(dosya_no)}"
            klasor = os.path.join(hedef_kok, klasor_adi)
            indirilen, toplam, birlesik = evrak_indirici.evrak_kumesini_indir_ve_birlestir(
                self.dosya_id, secilenler, klasor, klasor_adi, self.log_fn)
            self.q.put(("indirildi", (indirilen, toplam, klasor)))
        except Exception as e:
            self.q.put(("indirme_hata", str(e)))

    def _poll(self):
        try:
            while True:
                tip, veri = self.q.get_nowait()
                if tip == "yuklendi":
                    self._doldur(veri)
                elif tip == "hata":
                    self.durum.config(text=f"Evrak listesi alınamadı: {veri}")
                elif tip == "goruntulendi":
                    self.goruntule_btn.set_state("normal")
                    self.durum.config(text="Açıldı.")
                elif tip == "goruntule_hata":
                    self.goruntule_btn.set_state("normal")
                    self.durum.config(text=f"Görüntülenemedi: {veri}")
                elif tip == "indirildi":
                    self.indir_btn.set_state("normal")
                    ind, top, klasor = veri
                    self.durum.config(text=f"✔ {ind}/{top} evrak indirildi — {klasor}")
                elif tip == "indirme_hata":
                    self.indir_btn.set_state("normal")
                    self.durum.config(text=f"İndirilemedi: {veri}")
        except queue.Empty:
            pass
        try:
            if self.top.winfo_exists():
                self.top.after(300, self._poll)
        except Exception:
            pass


class HukukDosyalarimPanel:
    YARGI_TURU = dosya_core.YARGI_TURU_HUKUK

    KOLONLAR = [
        ("birimAdi", "Mahkeme", 260),
        ("dosyaNo", "Dosya No", 100),
        ("dosyaTur", "Dosya Türü", 160),
        ("dosyaDurum", "Durum", 80),
        ("acilisTarihi", "Açılış Tarihi", 100),
        ("davaci", "Davacı", 220),
        ("davali", "Davalı", 220),
    ]
    HEAD_H = 52

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Hukuk Dosyalarım Yenile")
        self.belge_kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Hukuk Dosyalarım Belge İndir")
        self._aktif_kontrol = None
        self.result_q = queue.Queue()
        self.kayitlar_ham = []
        self.kayitlar = []
        self.col_vars = {k: tk.StringVar() for k, _l, _w in self.KOLONLAR}
        self.col_combos = {}
        self.col_labels = {}
        self.sort_key = None
        self.sort_reverse = False

        self.birim_var = tk.StringVar(value="Tümü")
        self.mahkeme_var = tk.StringVar(value="Tümü")
        self.dosya_tur_var = tk.StringVar(value="Tümü")
        self.durum_var = tk.StringVar(value="Tümü")
        self.tarih_bas_var = tk.StringVar()
        self.tarih_bit_var = tk.StringVar()
        self.taraf_var = tk.StringVar()

        self._birim_kod = {}
        self._mahkeme_kod = {}
        self._dosya_tur_kod = {}
        self._durum_kod = {}

        self._init_style()
        self._build()
        threading.Thread(target=self._alanlar_yukle_bg, daemon=True).start()
        self.app.after(200, self._poll)
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_dongusu(self):
        try:
            mesgul = getattr(self.yenile_btn, "_state", "normal") == "disabled"
            if not mesgul and self.parent.winfo_exists():
                threading.Thread(target=self._oto_yenile_bg,
                                 args=(self._filtreleri_topla(),), daemon=True).start()
        except Exception:
            pass
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_bg(self, filtreler):
        try:
            kayitlar = dosya_core.dosyalarim_db_listele(filtreler)
        except Exception:
            return
        if kayitlar:
            self.result_q.put(("kayitlar", kayitlar))

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="Hukuk Dosyalarım", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Senkron Kapsamı'nda seçilen Hukuk dosyaları. Bu liste yerel "
                 "veritabanından gelir; UYAP'tan tazelemek için “Yenile”'yi kullanın.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=760,
                 justify="left").pack(anchor="w", pady=(6, 14))

        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        self.birim_cb = self._combo(ui, "Yargı Birimi", self.birim_var, ["Tümü"])
        self.birim_cb.bind("<<ComboboxSelected>>", self._birim_degisti)
        self.mahkeme_cb = self._combo(ui, "Mahkeme", self.mahkeme_var, ["Tümü"])
        self.mahkeme_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.dosya_tur_cb = self._combo(ui, "Dosya Türü", self.dosya_tur_var, ["Tümü"])
        self.dosya_tur_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.durum_cb = self._combo(ui, "Durum", self.durum_var, ["Tümü"])
        self.durum_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())

        ui_tarih = tk.Frame(ust, bg=C.CARD)
        ui_tarih.pack(fill="x", padx=18, pady=(0, 12))

        tk.Label(ui_tarih, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui_tarih, self.tarih_bas_var)
        tk.Label(ui_tarih, text="–", bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui_tarih, self.tarih_bit_var)

        tk.Label(ui_tarih, text="Taraf Adı (Davacı/Davalı)", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        taraf_e = tk.Entry(ui_tarih, textvariable=self.taraf_var, bg="#FFFFFF", fg=C.INK, relief="flat",
                           insertbackground=C.INK, font=self.app.f_body, width=24,
                           highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        taraf_e.pack(side="left", ipady=3)
        taraf_e.bind("<Return>", lambda ev: self._filtrele())

        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self._btn(bar, "Filtrele", self._filtrele, "primary").pack(side="left", ipadx=6)
        self._btn(bar, "Temizle", self._temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.yenile_btn = self._btn(bar, "Yenile (UYAP'tan Güncelle)", self._yenile, "ghost")
        self.yenile_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn.set_state("disabled")
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durdur_btn.set_state("disabled")
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.excel_btn = self._btn(bar, "Excel'e Aktar", self._excel_aktar, "ghost")
        self.excel_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.belge_indir_btn = self._btn(bar, "Seçilenlerin Tüm Belgelerini İndir",
                                          self._secilenlerin_belgelerini_indir, "ghost")
        self.belge_indir_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(14, 0))

        self.head_canvas = tk.Canvas(card, bg=C.SIDEBAR, highlightthickness=0, height=self.HEAD_H)
        self.head_inner = tk.Frame(self.head_canvas, bg=C.SIDEBAR)
        self.head_win = self.head_canvas.create_window((0, 0), window=self.head_inner, anchor="nw")
        self.head_canvas.grid(row=0, column=0, sticky="ew")

        cols = [k for k, _l, _w in self.KOLONLAR]
        self.tree = ttk.Treeview(card, columns=cols, show="", height=16)
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda e: self._dosya_goruntule())
        ysb = tk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        xsb = tk.Scrollbar(card, orient="horizontal")
        xsb.grid(row=2, column=0, sticky="ew")

        def _xset(first, last):
            xsb.set(first, last)
            try:
                self.head_canvas.xview_moveto(float(first))
            except Exception:
                pass
        xsb.configure(command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=_xset)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        self._basligi_kur()

        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(10, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=3,
                           padx=12, pady=6, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        self._btn(lbox, "Günlüğü Aç", self._log_dosyasini_ac, "ghost").pack(
            side="right", padx=8, pady=6, ipadx=2)

    def _combo(self, parent, label, var, values):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", values=values,
                          font=self.app.f_body, width=18)
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

    # ─────────────────────────── sütun başlığı: canlı filtre + sıralama ───
    def _basligi_kur(self):
        for w in self.head_inner.winfo_children():
            w.destroy()
        self.col_combos = {}
        self.col_labels = {}
        total = 0
        for i, (k, lbl, w) in enumerate(self.KOLONLAR):
            total += w
            cell = tk.Frame(self.head_inner, bg=C.SIDEBAR, width=w, height=self.HEAD_H)
            cell.grid(row=0, column=i, sticky="nsew")
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)

            baslik = tk.Label(cell, text=lbl, bg=C.SIDEBAR, fg=C.INK,
                              font=self.app.f_nav_b, anchor="w", cursor="hand2")
            baslik.grid(row=0, column=0, sticky="ew", padx=6, pady=(5, 2))
            baslik.bind("<Button-1>", lambda e, col=k: self._sirala(col))
            self.col_labels[k] = (baslik, lbl)

            cb = ttk.Combobox(cell, textvariable=self.col_vars[k], state="normal",
                              font=self.app.f_body, width=4)
            cb.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
            cb.bind("<KeyRelease>", lambda ev: self._yerel_uygula())
            cb.bind("<<ComboboxSelected>>", lambda ev: self._yerel_uygula())
            cb.bind("<Return>", lambda ev: self._yerel_uygula())
            self.col_combos[k] = cb

            self.tree.column(k, width=w, anchor="w", stretch=False)
        self._sirala_etiket_guncelle()
        self.head_canvas.itemconfigure(self.head_win, width=total, height=self.HEAD_H)
        self.head_canvas.configure(scrollregion=(0, 0, total, self.HEAD_H))

    def _yerel_filtreleri_sifirla(self):
        for var in self.col_vars.values():
            var.set("")
        self.sort_key = None
        self.sort_reverse = False
        self._sirala_etiket_guncelle()

    def _sirala_etiket_guncelle(self):
        for k, (widget, taban) in self.col_labels.items():
            ok = ""
            if k == self.sort_key:
                ok = "  ▲" if not self.sort_reverse else "  ▼"
            widget.config(text=taban + ok)

    def _sirala(self, col):
        if self.sort_key == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = col
            self.sort_reverse = False
        self._sirala_etiket_guncelle()
        self._yerel_uygula()

    def _combolari_doldur(self):
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is None:
                continue
            degerler = sorted({str(r.get(k, "") or "") for r in self.kayitlar_ham} - {""},
                               key=lambda s: dosya_core.tr_lower(s))
            cb["values"] = degerler

    def _yerel_kriterler(self):
        out = {}
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is not None:
                q = dosya_core.tr_lower(cb.get().strip())
                if q:
                    out[k] = q
        return out

    def _yerel_uygula(self):
        kayitlar = self.kayitlar_ham
        kriter = self._yerel_kriterler()
        if kriter:
            def uyar(r):
                for k, q in kriter.items():
                    if q not in dosya_core.tr_lower(str(r.get(k, "") or "")):
                        return False
                return True
            kayitlar = [r for r in kayitlar if uyar(r)]
        if self.sort_key:
            col = self.sort_key
            if col == "acilisTarihi":
                import re
                _re = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")

                def gecerli_mi(r):
                    m = _re.match(str(r.get(col, "") or "").strip())
                    return f"{m.group(3)}{m.group(2)}{m.group(1)}" if m else None
            else:
                def gecerli_mi(r):
                    v = str(r.get(col, "") or "").strip()
                    if not v:
                        return None
                    try:
                        return (0, float(v.replace(",", ".")))
                    except ValueError:
                        return (1, dosya_core.tr_lower(v))
            gecerliler, gecersizler = [], []
            for r in kayitlar:
                a = gecerli_mi(r)
                (gecersizler if a is None else gecerliler).append((a, r))
            gecerliler.sort(key=lambda p: p[0], reverse=self.sort_reverse)
            kayitlar = [r for _a, r in gecerliler] + [r for _a, r in gecersizler]

        self.kayitlar = kayitlar
        self.tree.delete(*self.tree.get_children())
        for i, rec in enumerate(kayitlar):
            vals = [rec.get(k, "") for k, _l, _w in self.KOLONLAR]
            self.tree.insert("", "end", iid=str(i), values=vals)
        toplam = len(self.kayitlar_ham)
        if not toplam:
            self.durum_lbl.config(text="")
        elif len(kayitlar) != toplam:
            self.durum_lbl.config(text=f"{len(kayitlar)} / {toplam} dosya")
        else:
            self.durum_lbl.config(text=f"{toplam} dosya")

    # ─────────────────────────── günlük ───────────────────────────
    def _log(self, mesaj):
        self.result_q.put(("log", str(mesaj)))

    def _append_log(self, text):
        self._log_dosyaya_yaz(text)
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    @staticmethod
    def _log_dosyaya_yaz(text):
        try:
            os.makedirs(os.path.dirname(_LOG_DOSYA_YOLU), exist_ok=True)
            with open(_LOG_DOSYA_YOLU, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except Exception:
            pass

    def _log_dosyasini_ac(self):
        try:
            os.makedirs(os.path.dirname(_LOG_DOSYA_YOLU), exist_ok=True)
            if not os.path.exists(_LOG_DOSYA_YOLU):
                open(_LOG_DOSYA_YOLU, "a", encoding="utf-8").close()
            os.startfile(_LOG_DOSYA_YOLU)
        except Exception as e:
            self._append_log(f"⚠️ Log dosyası açılamadı: {e}")

    # ─────────────────────────── veri yükleme ───────────────────────────
    def _alanlar_yukle_bg(self):
        try:
            birimler = dosya_core.yargi_birimleri_getir_veya_db(self.YARGI_TURU, self._log)
            dosya_turleri = dosya_core.dosya_tur_secenekleri(self.YARGI_TURU)
            durumlar = dosya_core.dosya_durum_secenekleri()
            mahkemeler = dosya_core.mahkeme_secenekleri(self.YARGI_TURU, None)
        except Exception as e:
            self._log(f"⚠️ Alanlar yüklenemedi: {e}")
            return
        self.result_q.put(("alanlar", (birimler, dosya_turleri, durumlar)))
        self.result_q.put(("mahkemeler", mahkemeler))
        self._filtrele_bg({"yargi_turu": self.YARGI_TURU})

    def _alanlari_doldur(self, birimler, dosya_turleri, durumlar):
        self._birim_kod = {b.get("ad", b.get("kod", "")): b.get("kod", "") for b in birimler}
        self.birim_cb["values"] = ["Tümü"] + list(self._birim_kod.keys())
        self._dosya_tur_kod = {ad: kod for kod, ad in dosya_turleri}
        self.dosya_tur_cb["values"] = ["Tümü"] + [ad for _k, ad in dosya_turleri]
        self._durum_kod = {ad: kod for kod, ad in durumlar}
        self.durum_cb["values"] = ["Tümü"] + [ad for _k, ad in durumlar]

    def _birim_degisti(self, _event=None):
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self._yerel_filtreleri_sifirla()
        birim_ad = self.birim_var.get()
        birim_kod = self._birim_kod.get(birim_ad) if birim_ad != "Tümü" else None

        def bg():
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(self.YARGI_TURU, birim_kod)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _filtreleri_topla(self):
        f = {"yargi_turu": self.YARGI_TURU}
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
        self.birim_var.set("Tümü")
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self.dosya_tur_var.set("Tümü")
        self.durum_var.set("Tümü")
        self.tarih_bas_var.set("")
        self.tarih_bit_var.set("")
        self.taraf_var.set("")
        self._yerel_filtreleri_sifirla()

        def bg():
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(self.YARGI_TURU, None)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _tabloyu_doldur(self, kayitlar):
        self.kayitlar_ham = kayitlar
        self._combolari_doldur()
        self._yerel_uygula()

    # ─────────────────────────── Yenile (UYAP'tan Güncelle) ───────────────
    def _yenile_baslat(self, **kwargs):
        self.kontrol.sifirla()
        self._aktif_kontrol = self.kontrol
        self.yenile_btn.set_state("disabled")
        self.belge_indir_btn.set_state("disabled")
        self.duraklat_btn.set_state("normal")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal")
        self.durum_lbl.config(text="UYAP'tan güncelleniyor…")

        def bg():
            try:
                toplam, sonuclar = dosya_core.dosyalarim_yenile(self._log, kontrol=self.kontrol, **kwargs)
                self.result_q.put(("yenilendi", (toplam, sonuclar)))
            except Exception as e:
                self.result_q.put(("yenile_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def _yenile_cakisma_kontrolu(self, kwargs):
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._yenile_baslat(**kwargs),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._yenile_baslat(**kwargs)

    def _yenile(self):
        """Ekonomik: yalnız seçili Yargı Birimi taranır (Tümü ise Hukuk'un
        Senkron Kapsamı'ndaki TÜM birimleri — bkz. dosya_core.dosyalarim_yenile
        yargi_turu tek başına verilince yaptığı genişletme)."""
        kwargs = {"yargi_turu": self.YARGI_TURU}
        birim_ad = self.birim_var.get()
        if birim_ad != "Tümü":
            kwargs["yargi_birimi_kod"] = self._birim_kod.get(birim_ad)
        self._yenile_cakisma_kontrolu(kwargs)

    def duraklat_toggle(self):
        if not self._aktif_kontrol:
            return
        paused = self._aktif_kontrol.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self._aktif_kontrol:
            self._aktif_kontrol.durdur()

    def _yenile_bitti_ui(self):
        self._aktif_kontrol = None
        self.yenile_btn.set_state("normal")
        self.belge_indir_btn.set_state("normal")
        self.duraklat_btn.set_state("disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("disabled")
        toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    # ─────────────────────────── Seçilenlerin Tüm Belgelerini İndir ───────
    def _secilenlerin_belgelerini_indir(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden en az bir dosya seçin (Ctrl/Shift ile çoklu seçim).")
            return
        try:
            secili_kayitlar = [self.kayitlar[int(iid)] for iid in secim]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        hedef = filedialog.askdirectory(title="Belgelerin indirileceği klasörü seçin")
        if not hedef:
            return
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.belge_kontrol.ad, self.belge_kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.belge_kontrol.ad, self.belge_kontrol,
                lambda: self._belge_indir_baslat(secili_kayitlar, hedef),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._belge_indir_baslat(secili_kayitlar, hedef)

    def _belge_indir_baslat(self, kayitlar, hedef):
        self.belge_kontrol.sifirla()
        self._aktif_kontrol = self.belge_kontrol
        self.yenile_btn.set_state("disabled")
        self.belge_indir_btn.set_state("disabled")
        self.duraklat_btn.set_state("normal")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal")
        self.durum_lbl.config(text=f"{len(kayitlar)} dosya için belgeler indiriliyor…")
        self._log(f"▶ {len(kayitlar)} seçili dosya için 'Tüm Belgeleri İndir' başlıyor… (klasör: {hedef})")

        def bg():
            try:
                sonuc = evrak_indirici.calistir(kayitlar, hedef, self._log, kontrol=self.belge_kontrol)
                self.result_q.put(("belge_indir_bitti", sonuc))
            except Exception as e:
                self.result_q.put(("belge_indir_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def _belge_indir_bitti_ui(self):
        self._aktif_kontrol = None
        self.yenile_btn.set_state("normal")
        self.belge_indir_btn.set_state("normal")
        self.duraklat_btn.set_state("disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("disabled")
        toplu_is_kontrol.KAYIT_DEFTERI.sil(self.belge_kontrol.ad)

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
        self.result_q.put(("detay", (rec, sonuc)))

    # ─────────────────────────── Excel'e Aktar ───────────────────────────
    def _excel_aktar(self):
        if not self.kayitlar:
            self.durum_lbl.config(text="Aktarılacak kayıt yok — önce filtreleyin.")
            return
        varsayilan_ad = f"HukukDosyalarim_{time.strftime('%Y%m%d_%H%M')}.xlsx"
        yol = filedialog.asksaveasfilename(
            title="Excel'e Aktar", defaultextension=".xlsx",
            initialfile=varsayilan_ad, filetypes=[("Excel", "*.xlsx")])
        if not yol:
            return
        self.excel_btn.set_state("disabled")
        self.durum_lbl.config(text="Excel'e yazılıyor…")
        threading.Thread(target=self._excel_aktar_bg, args=(yol, list(self.kayitlar)), daemon=True).start()

    def _excel_aktar_bg(self, yol, kayitlar):
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Hukuk Dosyalarım"
            ws.append([lbl for _k, lbl, _w in self.KOLONLAR])
            for rec in kayitlar:
                ws.append([rec.get(k, "") for k, _l, _w in self.KOLONLAR])
            for i, (_k, _l, w) in enumerate(self.KOLONLAR, start=1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(10, min(w // 6, 45))
            wb.save(yol)
        except PermissionError:
            self.result_q.put(("excel_hata", "Dosya başka bir programda (ör. Excel'de) açık olabilir, kapatıp tekrar deneyin."))
            return
        except Exception as e:
            self.result_q.put(("excel_hata", str(e)))
            return
        self.result_q.put(("excel_tamam", (yol, len(kayitlar))))

    def _excel_aktarildi(self, veri):
        yol, adet = veri
        self.excel_btn.set_state("normal")
        self.durum_lbl.config(text=f"✔ {adet} kayıt Excel'e aktarıldı: {os.path.basename(yol)}")
        try:
            os.startfile(yol)
        except Exception:
            pass

    def _sessiz_dialog(self, baslik, metin, hata=False):
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

    def _detay_goster(self, veri):
        rec, sonuc = veri
        self.detay_btn.set_state("normal")
        ham, aile, kaydedildi, hata, taraflar = sonuc
        if hata:
            self.durum_lbl.config(text="Dosya ayrıntısı alınamadı")
            self._sessiz_dialog("Dosya Görüntüle", hata, hata=True)
            return
        self.durum_lbl.config(text="Dosya ayrıntısı kaydedildi")
        baslik = f"Dosya Bilgileri — {rec.get('dosyaNo', '') or ''}".rstrip(" —")
        self._detay_pencere(baslik, rec, ham, aile, taraflar, kaydedildi)
        if kaydedildi:
            self._filtrele()

    def _alan_satirlari(self, parent, alanlar, sutun_sayisi=2):
        for g in range(sutun_sayisi):
            parent.columnconfigure(g * 2 + 1, weight=1)
        for i, (lbl, val) in enumerate(alanlar):
            row, grup = divmod(i, sutun_sayisi)
            col = grup * 2
            tk.Label(parent, text=lbl, bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_small,
                     anchor="w").grid(row=row, column=col, sticky="w",
                                      padx=(18 if col == 0 else 24, 8), pady=4)
            tk.Label(parent, text=str(val) if val else "—", bg=C.CARD, fg=C.INK,
                     font=self.app.f_body, anchor="w", justify="left",
                     wraplength=240).grid(row=row, column=col + 1, sticky="w",
                                          padx=(0, 18), pady=4)

    def _ozet_sekmesi(self, parent, rec, ham, aile):
        alanlar = [
            ("Mahkeme", rec.get("birimAdi", "")),
            ("Dosya No", rec.get("dosyaNo", "")),
            ("Dosya Türü", rec.get("dosyaTur", "")),
            ("Durum", rec.get("dosyaDurum", "")),
            ("Açılış Tarihi", rec.get("acilisTarihi", "")),
        ]
        if aile == "hukuk":
            alanlar += [
                ("Dava Açılış Türü", ham.get("davaAcilisTuru", "")),
                ("Dava Türleri", ham.get("davaTurleriStr", "")),
                ("İlgili Dava Listesi", ham.get("ilgiliDavaListesiStr", "")),
                ("Duruşma Tarihi", ham.get("durusmaTarihi", "")),
            ]
        else:
            alanlar.append(("Not", "Bu dosya için ek ayrıntı görüntüleme desteklenmiyor."))
        self._alan_satirlari(parent, alanlar)

    def _taraflar_sekmesi(self, parent, taraflar):
        parent.columnconfigure(1, weight=1)
        for c, baslik in enumerate(("Rol", "Ad / Unvan", "Vekil")):
            tk.Label(parent, text=baslik, bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_nav_b,
                     anchor="w").grid(row=0, column=c, sticky="w",
                                      padx=(18 if c == 0 else 0, 18 if c == 2 else 10),
                                      pady=(12, 4))
        for i, t in enumerate(taraflar, start=1):
            tk.Label(parent, text=t.get("rol", "") or "—", bg=C.CARD, fg=C.INK,
                     font=self.app.f_body, anchor="w").grid(row=i, column=0, sticky="w",
                                                            padx=(18, 10), pady=3)
            for c, val in ((1, t.get("adi", "")), (2, (t.get("vekil") or "").strip("[]"))):
                tk.Label(parent, text=val or "—", bg=C.CARD, fg=C.INK, font=self.app.f_body,
                         anchor="w", justify="left", wraplength=260).grid(
                    row=i, column=c, sticky="w", padx=(0, 18 if c == 2 else 10), pady=3)

    _detay_pencere_sayaci = 0

    def _detay_pencere(self, baslik, rec, ham, aile, taraflar, kaydedildi):
        top = tk.Toplevel(self.app)
        top.title(baslik)
        top.configure(bg=C.CARD)
        top.geometry("780x580")
        top.minsize(600, 420)
        top.resizable(True, True)

        tk.Label(top, text=baslik, bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_card_t,
                 anchor="w", justify="left").pack(anchor="w", padx=18, pady=(16, 10))

        if (ham or {}).get("_onbellekten"):
            tk.Label(top, text="⚠️ Canlı sorgu şu an başarısız — daha önce kaydedilmiş veri gösteriliyor (güncel olmayabilir).",
                     bg=C.CARD, fg=C.CLAY, font=self.app.f_small, anchor="w",
                     justify="left", wraplength=660).pack(anchor="w", padx=18, pady=(0, 6))

        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=18)

        tab_ozet = tk.Frame(nb, bg=C.CARD)
        nb.add(tab_ozet, text="Genel Bilgiler")
        self._ozet_sekmesi(tab_ozet, rec, ham, aile)

        if taraflar:
            tab_taraf = tk.Frame(nb, bg=C.CARD)
            nb.add(tab_taraf, text="Taraflar")
            self._taraflar_sekmesi(tab_taraf, taraflar)

        if rec.get("dosyaId"):
            tab_evrak = tk.Frame(nb, bg=C.CARD)
            nb.add(tab_evrak, text="Evraklar")
            _EvrakListesiPaneli(tab_evrak, top, self.app, rec, self._log)

        alt = tk.Frame(top, bg=C.CARD)
        alt.pack(fill="x", padx=18, pady=(10, 16))
        if kaydedildi:
            tk.Label(alt, text="Yerel veritabanına kaydedildi.", bg=C.CARD, fg=C.INK_FAINT,
                     font=self.app.f_small).pack(side="left")
        self._btn(alt, "Kapat", top.destroy, "primary").pack(side="right")

        top.update_idletasks()
        kaydirma = (HukukDosyalarimPanel._detay_pencere_sayaci % 8) * 28
        HukukDosyalarimPanel._detay_pencere_sayaci += 1
        x = self.app.winfo_rootx() + 40 + kaydirma
        y = self.app.winfo_rooty() + 40 + kaydirma
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.focus_set()

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                try:
                    if tip == "alanlar":
                        self._alanlari_doldur(*veri)
                    elif tip == "mahkemeler":
                        self._mahkeme_kod = {m.get("ad", m.get("birimId", "")): m.get("birimId", "") for m in veri}
                        self.mahkeme_cb["values"] = ["Tümü"] + list(self._mahkeme_kod.keys())
                    elif tip == "kayitlar":
                        self._tabloyu_doldur(veri)
                    elif tip == "yenilendi":
                        self._yenile_bitti_ui()
                        toplam, sonuclar = veri
                        self.durum_lbl.config(text=f"✔ Güncellendi ({toplam} kayıt, {len(sonuclar)} kapsam).")
                        self._filtrele()
                    elif tip == "yenile_hata":
                        self._yenile_bitti_ui()
                        self.durum_lbl.config(text=f"Güncellenemedi: {veri}")
                    elif tip == "detay":
                        self._detay_goster(veri)
                    elif tip == "belge_indir_bitti":
                        self._belge_indir_bitti_ui()
                        basarili = sum(1 for s in veri if str(s.get("Durum", "")).startswith("✅"))
                        self.durum_lbl.config(text=f"✔ Belge indirme tamamlandı ({basarili}/{len(veri)} dosya).")
                    elif tip == "belge_indir_hata":
                        self._belge_indir_bitti_ui()
                        self.durum_lbl.config(text=f"Belge indirme başarısız: {veri}")
                    elif tip == "excel_tamam":
                        self._excel_aktarildi(veri)
                    elif tip == "excel_hata":
                        self.excel_btn.set_state("normal")
                        self.durum_lbl.config(text=f"Excel'e aktarılamadı: {veri}")
                    elif tip == "log":
                        self._append_log(str(veri))
                except Exception as e:
                    self._append_log(f"⚠️ Ekran güncellenemedi ({tip}): {e}")
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
