# -*- coding: utf-8 -*-
"""
Modül: İcra > Dosyalarım  (Dosya Sorgulama)
===========================================
SAKİN tarzda arayüz; ÇALIŞMA MANTIĞI icra_core üzerinden, suite'in KANITLI
tekniğiyle çalışır (orijinal sgk_sorgu_gui.SorguMotoru._post → 127.0.0.1:8800
ofis proxy'si → canlı e-imza UYAP oturumu). Tarayıcı/Playwright YOK.

Tasarım — TEK alan:
• Sonuç tablosunun en üstünde her kolon için: BAŞLIK (label) + hemen altında o
  kolonun "yazılabilir liste-kutusu" (combobox). Aynı kutu hem sunucu kriteridir
  (Sorgula) hem de dönen sonuçları anında daraltan filtredir. Giriş ve sonuç
  artık iki ayrı yerde değil; tek hizalı tablodadır.
• Durum (Açık/Kapalı) üstte, ayrı ve önemli ilk filtre (dosyaDurumKod).
• Açılış Tarih Aralığı üstte ayrı bir alanda (ileride ayrı işlenecek).
• Ham yanıt / JSON detay paneli YOKTUR.

Alanlar OPSİYONEL, tek tek açılıp kapatılır; tercih kaydedilir
(uyap_app_config.json → "icra_sorgu_alanlari"). UYAP Bağlantısı (Paylaş/Al)
açık değilse sorgu ofis proxy'sine ulaşamaz.
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

from theme import C, RoundButton
from . import icra_core
from . import dosya_core

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402


class IcraDosyalarimPanel:
    # Açılış tarihi alanları kolon/filtre değil; üstte ayrı durur.
    DATE_KEYS = ("dosyaAcilisTarihiStart", "dosyaAcilisTarihiEnd")
    HEAD_H = 56                 # başlık+combobox şeridi yüksekliği (px)

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.engine = icra_core.IcraSorgu(self._log)
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Dosya Sorgulama")

        self.vars = {f["key"]: tk.StringVar() for f in icra_core.FIELDS}
        self.combos = {}           # key -> ttk.Combobox (tablo başlığındaki kutular)
        self._combo_full = {}      # key -> tam değer listesi (oto-tamamlama için)
        self._ac = None            # oto-tamamlama açılır penceresi (Toplevel)
        self._ac_list = None       # içindeki Listbox
        self._ac_key = None        # o an aktif kutu anahtarı
        self.active = self._load_active()
        self.edit_acik = False

        self.durum_var = tk.StringVar(value=icra_core.DURUMLAR[0][0])
        self._durum_kod = {lbl: kod for lbl, kod in icra_core.DURUMLAR}

        # Açık "Taraf ile Ara" alanları (tahminsiz manuel arama).
        self.taraf_tur_var = tk.StringVar(value="gercek")
        self.taraf_vars = {k: tk.StringVar() for k in
                           ("taraf_ad", "taraf_soyad", "taraf_tckn",
                            "taraf_kurum", "taraf_vergi", "taraf_mersis")}

        self.all_records = []
        self._gorunen_kayitlar = []  # tablo satırı (iid) -> ham kayıt eşlemesi ("Dosya Görüntüle")
        self.columns = []          # tablo kolonları = aktif alanlar (tarih hariç)
        self.sort_key = None
        self.sort_reverse = False
        # Son çalıştırılan sorgunun kriterleri — "Dosya Görüntüle" bir kayıt
        # kaydettiğinde (bkz. _detay_goster) tabloyu AYNI kriterlerle DB'den
        # tazelemek için (kullanıcı bulgusu: dosyalarim_genel.py'deki eşi
        # 2026-07-12'de düzeltilmişti, bu ekrana hiç taşınmamıştı).
        self._son_degerler = {}
        self._son_durum_kod = icra_core.DURUM_VARSAYILAN

        self.result_q = queue.Queue()
        self._logbuf = []
        self._loglock = threading.Lock()
        self.running = False

        self._init_style()
        self._build()
        self._poll()
        # İcra daireleri açılır listesini arka planda yükle (GUI'yi bloklamaz).
        threading.Thread(target=self._birim_listesi_yukle_bg, daemon=True).start()
        # Panel açılır açılmaz eldeki DB kayıtlarını göster (UYAP beklemeden).
        threading.Thread(target=self._acilista_db_yukle_bg, daemon=True).start()
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_dongusu(self):
        """20 sn'de bir mevcut filtreyle YEREL veritabanını sessizce yeniden
        okur (UYAP'a GİTMEZ) — kullanıcı isteği (2026-08-14): bir modülde
        yapılan güncelleme diğerinde ELLE Sorgula'ya basmadan görünsün. Canlı
        bir Sorgula SÜRERKEN (self.running) araya girmez ("db_acilis" işleyicisi
        zaten aynı korumayı bir kez daha uygular)."""
        if not self.running:
            values = {k: v.get() for k, v in self.vars.items()}
            durum_kod = self._durum_kod.get(self.durum_var.get(), icra_core.DURUM_VARSAYILAN)
            threading.Thread(target=self._oto_yenile_bg, args=(values, durum_kod), daemon=True).start()
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_bg(self, values, durum_kod):
        try:
            kayitlar = icra_core.db_dosyalari_getir(values, durum_kod)
        except Exception:
            return
        # db_dosyalari_getir HATA durumunda da [] döner (bkz. kendi try/except'i)
        # — burada BOŞ sonucu göndermiyoruz ki geçici bir DB kesintisi ekrandaki
        # DOLU tabloyu SESSİZCE boşaltmasın (kullanıcı bulgusu, 2026-08-14:
        # "var olan veri gitti" — bkz. _birlestir düzeltmesiyle AYNI endişe).
        if kayitlar:
            self.result_q.put(("db_acilis", kayitlar))

    # ─────────────────────────── tercih kalıcı ───────────────────────────
    def _auth(self):
        return getattr(self.app, "_auth", None)

    def _load_active(self):
        auth = self._auth()
        if auth:
            try:
                cfg = auth.load_config()
                kayitli = cfg.get("icra_sorgu_alanlari")
                if isinstance(kayitli, list):
                    gecerli = icra_core.gecerli_alanlar(kayitli)
                    if gecerli:
                        # YENİ eklenen varsayılan sütunlar (ör. kesinlesme_durumu/
                        # tebligat_durumu, 2026-08-14) eski KAYDEDİLMİŞ tercihte
                        # yoksa burada eklenir — yoksa "FIELDS'e ekledim" YETMEZ,
                        # daha önce bu ekranda kolon seçimini özelleştirmiş
                        # kullanıcı yeni varsayılan sütunları HİÇ GÖRMEZ (kayıtlı
                        # tercih eskiyi aynen korur, yeniyi bilmez).
                        for k in icra_core.DEFAULT_ACTIVE:
                            if k not in gecerli:
                                gecerli.append(k)
                        return gecerli
            except Exception:
                pass
        return list(icra_core.DEFAULT_ACTIVE)

    def _save_active(self):
        auth = self._auth()
        if not auth:
            return
        try:
            cfg = auth.load_config()
            cfg["icra_sorgu_alanlari"] = list(self.active)
            auth.save_config(cfg)
        except Exception:
            pass

    def _kolonlar(self):
        """Tablo kolonları = kullanıcının seçtiği alanlar (self.active — "Alanları
        Düzenle" checkbox'larından, bkz. _edit_degisti). ÖNCEDEN burada sabit 7
        kolonluk bir liste vardı (kullanıcı bulgusu, 2026-08-14: "Kesinleşme/
        Tebliğ Durumu'nu İcra Dosyalarım'a eklememişsin" — FIELDS'e eklenmiş
        olmaları YETMİYORDU, çünkü tablo self.active'i HİÇ OKUMUYORDU; "Alanları
        Düzenle" checkbox'ları da bu YÜZDEN görünüşte hiçbir şey değiştirmiyordu)."""
        return [k for k in self.active if k not in self.DATE_KEYS]

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Icra.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.map("Icra.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        dis = tk.Frame(self.parent, bg=C.BG)
        dis.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(dis, bg=C.BG, highlightthickness=0)
        vbar = tk.Scrollbar(dis, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._wrap = tk.Frame(self._canvas, bg=C.BG)
        self._cwin = self._canvas.create_window((0, 0), window=self._wrap, anchor="nw")
        self._son_h = None
        self._wrap.bind("<Configure>", lambda e: self._scroll_guncelle())
        self._canvas.bind("<Configure>", lambda e: (
            self._canvas.itemconfigure(self._cwin, width=e.width), self._scroll_guncelle()))
        self._scroll_wheel_bagla()

        wrap = tk.Frame(self._wrap, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="İcra · Dosyalarım", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="UYAP Dosya Sorgulama (İcra · İcra Dairesi). İstekler yerel "
                            "ofis bağlantısı üzerinden gönderilir; UYAP Bağlantısı açık olmalı.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 14))

        # ── üst kriter çubuğu: Durum + Açılış Tarih Aralığı ──
        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        tk.Label(ui, text="Durum", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        ttk.Combobox(ui, textvariable=self.durum_var, state="readonly",
                     values=[lbl for lbl, _ in icra_core.DURUMLAR],
                     font=self.app.f_body, width=9).pack(side="left", padx=(0, 22))

        tk.Label(ui, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui, "dosyaAcilisTarihiStart")
        tk.Label(ui, text="–", bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui, "dosyaAcilisTarihiEnd")

        # ── ikinci satır: Taraf ile Ara (gerçek kişi / kurum) ──
        tk.Frame(ust, bg=C.LINE, height=1).pack(fill="x", padx=18)
        self._taraf_section(ust)

        # ── düğmeler ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self.sorgula_btn = self._btn(bar, "Sorgula", self.sorgula, "primary")
        self.sorgula_btn.pack(side="left", ipadx=6)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn.set_state("disabled")
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durdur_btn.set_state("disabled")
        self._btn(bar, "Temizle", self.temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.edit_btn = self._btn(bar, "Alanları Düzenle", self.edit_toggle, "ghost")
        self.edit_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT,
                                  font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── alan seçimi (katlanır) ──
        self.edit_frame = tk.Frame(wrap, bg=C.BG)

        # ── sayaç satırı ──
        fb = tk.Frame(wrap, bg=C.BG)
        fb.pack(fill="x", pady=(14, 0))
        tk.Label(fb, text="Sonuçlar", bg=C.BG, fg=C.INK, font=self.app.f_card_t).pack(side="left")
        tk.Label(fb, text="(başlık altındaki kutulara yazarak/seçerek daraltın)", bg=C.BG,
                 fg=C.INK_FAINT, font=self.app.f_small).pack(side="left", padx=(8, 0))
        self.sayac_lbl = tk.Label(fb, text="", bg=C.BG, fg=C.INK_FAINT,
                                  font=self.app.f_small)
        self.sayac_lbl.pack(side="right")

        # ── TEK tablo: başlık şeridi (label+combobox) + veri ──
        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(8, 0))

        self.head_canvas = tk.Canvas(card, bg=C.SIDEBAR, highlightthickness=0,
                                     height=self.HEAD_H)
        self.head_inner = tk.Frame(self.head_canvas, bg=C.SIDEBAR)
        self.head_win = self.head_canvas.create_window((0, 0), window=self.head_inner,
                                                       anchor="nw")
        self.head_canvas.grid(row=0, column=0, sticky="ew")

        self.tree = ttk.Treeview(card, columns=("_ph",), show="", style="Icra.Treeview",
                                 height=14)
        self.tree.grid(row=1, column=0, sticky="nsew")
        ysb = tk.Scrollbar(card, command=self.tree.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        xsb = tk.Scrollbar(card, orient="horizontal", command=self.tree.xview)
        xsb.grid(row=2, column=0, sticky="ew")

        def _xset(first, last):
            xsb.set(first, last)
            try:
                self.head_canvas.xview_moveto(float(first))
            except Exception:
                pass
        self.tree.configure(xscrollcommand=_xset, yscrollcommand=ysb.set)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)

        # ── günlük (yalnızca hata/ilerleme) ──
        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(12, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=4,
                           padx=12, pady=8, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

        self._render_edit()
        self._tabloyu_kur()

    def _tarih_entry(self, parent, key):
        e = tk.Entry(parent, textvariable=self.vars[key], bg="#FFFFFF", fg=C.INK,
                     relief="flat", insertbackground=C.INK, font=self.app.f_body,
                     width=12, highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda ev: self.sorgula())
        return e

    # ─────────────────────────── Taraf ile Ara (tahminsiz) ───────────────────────────
    def _taraf_section(self, parent):
        """Açık taraf arama şeridi: Gerçek Kişi (Ad/Soyad/TCKN) ↔ Kurum
        (Kurum Adı/Vergi/MERSİS). Tür kullanıcı tarafından seçildiği için
        sunucuya tahminsiz, kesin alanlar gider."""
        ti = tk.Frame(parent, bg=C.CARD)
        ti.pack(fill="x", padx=18, pady=12)

        tk.Label(ti, text="Taraf ile Ara", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 10))

        for lbl, val in (("Gerçek Kişi", "gercek"), ("Kurum", "kurum")):
            tk.Radiobutton(ti, text=lbl, value=val, variable=self.taraf_tur_var,
                           bg=C.CARD, fg=C.INK, activebackground=C.CARD,
                           activeforeground=C.INK, selectcolor=C.CARD,
                           font=self.app.f_body, bd=0, highlightthickness=0,
                           cursor="hand2", command=self._taraf_tur_degisti
                           ).pack(side="left", padx=(0, 4))

        # alan grupları — yalnızca seçili tür görünür
        self.taraf_kisi_frame = tk.Frame(ti, bg=C.CARD)
        self.taraf_kurum_frame = tk.Frame(ti, bg=C.CARD)

        self._taraf_alan(self.taraf_kisi_frame, "Ad", "taraf_ad", 14)
        self._taraf_alan(self.taraf_kisi_frame, "Soyad", "taraf_soyad", 14)
        self._taraf_alan(self.taraf_kisi_frame, "TCKN", "taraf_tckn", 12)

        self._taraf_alan(self.taraf_kurum_frame, "Kurum Adı", "taraf_kurum", 26)
        self._taraf_alan(self.taraf_kurum_frame, "Vergi No", "taraf_vergi", 12)
        self._taraf_alan(self.taraf_kurum_frame, "MERSİS", "taraf_mersis", 16)

        self._taraf_tur_degisti()

    def _taraf_alan(self, parent, label, key, width):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(side="left", padx=(0, 4))
        e = tk.Entry(parent, textvariable=self.taraf_vars[key], bg="#FFFFFF", fg=C.INK,
                     relief="flat", insertbackground=C.INK, font=self.app.f_body,
                     width=width, highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3, padx=(0, 12))
        e.bind("<Return>", lambda ev: self.sorgula())
        return e

    def _taraf_tur_degisti(self):
        if self.taraf_tur_var.get() == "kurum":
            self.taraf_kisi_frame.pack_forget()
            self.taraf_kurum_frame.pack(side="left", padx=(8, 0))
        else:
            self.taraf_kurum_frame.pack_forget()
            self.taraf_kisi_frame.pack(side="left", padx=(8, 0))

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind,
                           font=self.app.f_nav_b, height=36)

    # ─────────────────────────── dikey kaydırma ───────────────────────────
    def _scroll_guncelle(self):
        if not self._canvas.winfo_exists():
            return
        ch = self._canvas.winfo_height()
        req = self._wrap.winfo_reqheight()
        h = max(req, ch)
        if h != self._son_h:
            self._son_h = h
            self._canvas.itemconfigure(self._cwin, height=h)
        self._canvas.configure(scrollregion=(0, 0, self._canvas.winfo_width(), h))

    def _scroll_wheel_bagla(self):
        def _wheel(e):
            w = self.parent.winfo_containing(e.x_root, e.y_root)
            while w is not None:
                if w in (getattr(self, "tree", None), getattr(self, "log", None)):
                    return
                w = getattr(w, "master", None)
            self._canvas.yview_scroll(int(-e.delta / 120), "units")
        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all("<MouseWheel>", _wheel))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

    # ─────────────────────────── alan seçimi ───────────────────────────
    def _render_edit(self):
        for w in self.edit_frame.winfo_children():
            w.destroy()
        kart = tk.Frame(self.edit_frame, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        kart.pack(fill="x", pady=(10, 0))
        tk.Label(kart, text="Tablo kolonlarını seç (tercih kaydedilir)", bg=C.CARD,
                 fg=C.INK, font=self.app.f_nav_b).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(kart, text="Açık alanlar tablonun başlık+kutu kolonlarıdır. Kişi ile kurum "
                            "birlikte doldurulursa kurum önceliklidir. (Açılış Tarihi üstte ayrıdır.)",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small).pack(anchor="w",
                 padx=16, pady=(0, 8))
        self.edit_vars = {}
        for grp in icra_core.GROUPS:
            grp_keys = [f for f in icra_core.FIELDS
                        if f["group"] == grp and f["key"] not in self.DATE_KEYS]
            if not grp_keys:
                continue
            satir = tk.Frame(kart, bg=C.CARD)
            satir.pack(fill="x", padx=16, pady=2)
            tk.Label(satir, text=grp.upper(), bg=C.CARD, fg=C.SAGE_DK,
                     font=self.app.f_nav_b, width=8, anchor="w").pack(side="left", padx=(0, 8))
            for f in grp_keys:
                var = tk.BooleanVar(value=(f["key"] in self.active))
                self.edit_vars[f["key"]] = var
                tk.Checkbutton(satir, text=f["label"], variable=var, bg=C.CARD, fg=C.INK,
                               activebackground=C.CARD, activeforeground=C.INK,
                               selectcolor=C.CARD, font=self.app.f_small, bd=0,
                               highlightthickness=0, cursor="hand2",
                               command=self._edit_degisti).pack(side="left", padx=6)
        tk.Frame(kart, bg=C.CARD, height=8).pack()

    def edit_toggle(self):
        self.edit_acik = not self.edit_acik
        if self.edit_acik:
            self.edit_frame.pack(fill="x", after=self._edit_anchor())
            self.edit_btn.set_text("Alanları Gizle")
        else:
            self.edit_frame.pack_forget()
            self.edit_btn.set_text("Alanları Düzenle")

    def _edit_anchor(self):
        # düğme çubuğunun (edit_frame'den bir önceki kardeş) hemen ardına yerleşir
        kardesler = self.edit_frame.master.winfo_children()
        i = kardesler.index(self.edit_frame)
        return kardesler[i - 1]

    def _edit_degisti(self):
        # tarih anahtarları her zaman korunur (üstte ayrı), kolonlar checkbox'tan
        secili = [f["key"] for f in icra_core.FIELDS
                  if f["key"] not in self.DATE_KEYS
                  and self.edit_vars.get(f["key"]) and self.edit_vars[f["key"]].get()]
        tarih = [k for k in self.active if k in self.DATE_KEYS]
        self.active = secili + tarih
        self._save_active()
        self.sort_key = None
        self._tabloyu_kur()

    # ─────────────────────────── tablo (başlık şeridi + veri) ───────────────────────────
    def _tabloyu_kur(self):
        self.columns = self._kolonlar()
        # başlık şeridi yeniden kur
        for w in self.head_inner.winfo_children():
            w.destroy()
        self.combos = {}

        if not self.columns:
            tk.Label(self.head_inner, text="Kolon yok — “Alanları Düzenle” ile alan ekleyin.",
                     bg=C.SIDEBAR, fg=C.INK_FAINT, font=self.app.f_small).grid(
                     row=0, column=0, padx=10, pady=18, sticky="w")
            self.tree["columns"] = ("_ph",)
            self.tree["displaycolumns"] = ("_ph",)
            self.tree.column("_ph", width=400, anchor="w", stretch=True)
            self._tabloyu_doldur()
            self._head_olcule(400)
            return

        total = 0
        for i, k in enumerate(self.columns):
            W = self._kol_genislik(k)
            total += W
            cell = tk.Frame(self.head_inner, bg=C.SIDEBAR, width=W, height=self.HEAD_H)
            cell.grid(row=0, column=i, sticky="nsew")
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)

            ok = ""
            if k == self.sort_key:
                ok = "  ▲" if not self.sort_reverse else "  ▼"
            lbl = tk.Label(cell, text=icra_core.FIELD_BY_KEY[k]["label"] + ok, bg=C.SIDEBAR,
                           fg=C.INK, font=self.app.f_nav_b, anchor="w", cursor="hand2")
            lbl.grid(row=0, column=0, sticky="ew", padx=6, pady=(5, 2))
            lbl.bind("<Button-1>", lambda e, col=k: self._sort_by(col))

            cb = ttk.Combobox(cell, textvariable=self.vars[k], state="normal",
                              font=self.app.f_body, width=4)
            cb.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
            cb.bind("<Return>", lambda ev, col=k: self._combo_return(col))
            cb.bind("<KeyRelease>", lambda ev, col=k: self._oto_tamamla(ev, col))
            cb.bind("<<ComboboxSelected>>", lambda ev: self._canli_filtrele())
            cb.bind("<FocusOut>", lambda ev: self.app.after(150, self._ac_gizle))
            self.combos[k] = cb

        # tree kolonları (sabit genişlik, başlık bandı gizli)
        self.tree["columns"] = self.columns
        self.tree["displaycolumns"] = self.columns
        for k in self.columns:
            self.tree.column(k, width=self._kol_genislik(k), anchor="w", stretch=False)
        self._tabloyu_doldur()
        self._combolari_doldur()
        self._head_olcule(total)

    def _head_olcule(self, total):
        """Başlık şeridini içeriğe göre boyutlandır + yatay kaydırmayı tree ile eşle."""
        self.head_canvas.itemconfigure(self.head_win, width=total, height=self.HEAD_H)
        self.head_canvas.configure(scrollregion=(0, 0, total, self.HEAD_H))

    @staticmethod
    def _kol_genislik(c):
        c = c.lower()
        if c in ("alacakli", "borclu"):
            return 200
        if "kurum" in c or "birim" in c or c in ("adi", "soyadi"):
            return 200
        if c == "dosyatur":
            return 150
        if "tckimlik" in c or "mersis" in c or "vergi" in c:
            return 150
        if c in ("tebligat_durumu", "kesinlesme_durumu"):
            return 200
        return 120

    def _kriterler(self):
        out = {}
        for k in self.columns:
            cb = self.combos.get(k)
            if cb is not None:
                q = icra_core.tr_lower(cb.get().strip())
                if q:
                    out[k] = q
        return out

    def _gorunen(self):
        kayitlar = self.all_records
        kriter = self._kriterler()
        if kriter:
            def uyar(r):
                for k, q in kriter.items():
                    if q not in icra_core.tr_lower(icra_core.kolon_degeri(r, k)):
                        return False
                return True
            kayitlar = [r for r in kayitlar if uyar(r)]
        if self.sort_key:
            # dosyalarim_genel.py'deki AYNI disiplin (2026-08-14, kullanıcı
            # bulgusu: burada tarih sütunları METİN olarak sıralanıp GÜN
            # basamağına göre karışıyordu — bkz. icra_core.tarih_siralama_
            # anahtari). Ayrıca geçersiz/boş değerler HER İKİ yönde de SONA
            # sabitlenir (o ekrandaki AYNI düzeltme, kullanıcı bulgusu
            # 2026-07-13: reverse=True'da eskiden başa zıplıyordu).
            tarih_mi = (icra_core.FIELD_BY_KEY.get(self.sort_key) or {}).get("type") == "date"

            def anahtar_bul(r):
                v = icra_core.kolon_degeri(r, self.sort_key)
                if tarih_mi:
                    k = icra_core.tarih_siralama_anahtari(v)
                    return k
                if not v:
                    return None
                try:
                    return (0, float(v.replace(",", ".")))
                except (ValueError, AttributeError):
                    return (1, v.lower())

            gecerliler, gecersizler = [], []
            for r in kayitlar:
                a = anahtar_bul(r)
                (gecersizler if a is None else gecerliler).append((a, r))
            gecerliler.sort(key=lambda p: p[0], reverse=self.sort_reverse)
            kayitlar = [r for _a, r in gecerliler] + [r for _a, r in gecersizler]
        return kayitlar

    @staticmethod
    def _cell(v):
        t = str(v).replace("\n", " ")
        return (t[:120] + "…") if len(t) > 120 else t

    def _tabloyu_doldur(self):
        self.tree.delete(*self.tree.get_children())
        if not self.columns:
            self.sayac_lbl.config(text="")
            self._gorunen_kayitlar = []
            return
        kayitlar = self._gorunen()
        self._gorunen_kayitlar = kayitlar
        for i, rec in enumerate(kayitlar):
            vals = [self._cell(icra_core.kolon_degeri(rec, c)) for c in self.columns]
            self.tree.insert("", "end", iid=str(i), values=vals)
        toplam = len(self.all_records)
        if not toplam:
            self.sayac_lbl.config(text="")
        elif len(kayitlar) != toplam:
            self.sayac_lbl.config(text=f"{len(kayitlar)} / {toplam} dosya")
        else:
            self.sayac_lbl.config(text=f"{toplam} dosya")

    def _combolari_doldur(self):
        for k in self.columns:
            cb = self.combos.get(k)
            if cb is None:
                continue
            if k == "dosyaYil":
                degerler = [str(y) for y in range(2026, 1987, -1)]
            elif k == "birimAdi":
                # İcra daireleri açılır listesi (UYAP birim listesinden); kullanıcı
                # seçince Sorgula sunucu-taraflı birimId ile arar. Liste henüz
                # yüklenmediyse eldeki sonuçların birimlerine düş.
                degerler = icra_core.birim_adlari()
                if degerler:
                    degerler = sorted(set(degerler), key=lambda s: s.lower())
                else:
                    degerler = sorted({icra_core.kolon_degeri(r, k) for r in self.all_records} - {""},
                                       key=lambda s: s.lower())
            else:
                degerler = sorted({icra_core.kolon_degeri(r, k) for r in self.all_records} - {""},
                                   key=lambda s: s.lower())
            self._combo_full[k] = degerler
            cb["values"] = degerler

    def _birim_listesi_yukle_bg(self):
        """İcra birim listesini arka planda çekip önbelleğe alır, sonra birim
        kutusunu tazeler (GUI'yi bloklamaz). Ofis (8800) kapalıysa YEREL DB'den
        doldurur — böylece birim filtresi çevrimdışı da çalışır."""
        try:
            icra_core.birim_listesi_getir(self._log)
        except Exception:
            pass
        if not icra_core.birim_adlari():     # ofis kapalı/erişilemez → DB yedeği
            try:
                icra_core.birimleri_db_den_yukle()
            except Exception:
                pass
        self.result_q.put(("birim", None))

    def _acilista_db_yukle_bg(self):
        """Panel açılışında eldeki DB kayıtlarını (Açık) arka planda yükleyip gösterir;
        böylece ofis kapalı olsa bile kullanıcı verisini hemen görür."""
        try:
            kayitlar = icra_core.db_dosyalari_getir({}, icra_core.DURUM_VARSAYILAN)
        except Exception:
            kayitlar = []
        if kayitlar:
            self.result_q.put(("db_acilis", kayitlar))

    def _sort_by(self, col):
        if self.sort_key == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = col
            self.sort_reverse = False
        self._tabloyu_kur()

    def _canli_filtrele(self):
        self._tabloyu_doldur()

    def _oto_tamamla(self, event, k):
        """Filtre kutusuna yazdıkça eşleşmeleri CANLI açılır listede gösterir (TR
        harf DUYARSIZ) ve tabloyu süzer. Ok tuşlarıyla listede gezilir, Enter ile
        seçilir, Esc ile kapanır."""
        ks = event.keysym
        if ks in ("Down", "Up"):
            if self._ac_key == k and self._ac_acik():
                self._ac_gez(ks)
            return
        if ks == "Escape":
            self._ac_gizle()
            return
        if ks in ("Return", "Tab", "Left", "Right", "Shift_L", "Shift_R",
                  "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return
        cb = self.combos.get(k)
        if cb is None:
            return
        tam = self._combo_full.get(k, [])
        nq = icra_core.tr_lower(cb.get().strip())
        esles = [v for v in tam if nq in icra_core.tr_lower(v)] if (nq and tam) else []
        cb["values"] = esles if esles else tam
        if nq and esles:
            self._ac_goster(k, esles)
        else:
            self._ac_gizle()
        self._canli_filtrele()

    def _combo_return(self, k):
        """Enter: açılır listede seçili varsa onu al; yoksa sorguyu çalıştır."""
        if self._ac_key == k and self._ac_acik() and self._ac_list.curselection():
            self._ac_onayla()
        else:
            self._ac_gizle()
            self.sorgula()

    # ── oto-tamamlama açılır listesi (yazdıkça eşleşenler) ───────────────────
    def _ac_acik(self):
        try:
            return self._ac is not None and bool(self._ac.winfo_viewable())
        except Exception:
            return False

    def _ac_kur(self):
        if self._ac is not None:
            return
        top = tk.Toplevel(self.app)
        top.withdraw()
        try:
            top.overrideredirect(True)
            top.attributes("-topmost", True)
        except Exception:
            pass
        lb = tk.Listbox(top, activestyle="dotbox", highlightthickness=1,
                        highlightbackground="#cccccc", bd=0, font=self.app.f_body,
                        bg="#FFFFFF", fg=C.INK, selectbackground="#dbe6f3",
                        selectforeground=C.INK, exportselection=False)
        lb.pack(fill="both", expand=True)
        lb.bind("<ButtonRelease-1>", lambda e: self._ac_onayla())
        lb.bind("<Return>", lambda e: self._ac_onayla())
        self._ac, self._ac_list = top, lb

    def _ac_goster(self, k, esles):
        self._ac_kur()
        cb = self.combos.get(k)
        if cb is None or not esles:
            self._ac_gizle()
            return
        self._ac_key = k
        lb = self._ac_list
        lb.delete(0, "end")
        for v in esles[:300]:
            lb.insert("end", v)
        lb["height"] = min(len(esles), 8)
        try:
            self._ac.update_idletasks()
            x = cb.winfo_rootx()
            y = cb.winfo_rooty() + cb.winfo_height()
            w = max(cb.winfo_width(), 200)
            h = self._ac.winfo_reqheight()
            self._ac.geometry(f"{w}x{h}+{x}+{y}")
            self._ac.deiconify()
            self._ac.lift()
        except Exception:
            self._ac_gizle()

    def _ac_gizle(self):
        if self._ac is not None:
            try:
                self._ac.withdraw()
            except Exception:
                pass
        self._ac_key = None

    def _ac_gez(self, yon):
        lb = self._ac_list
        if lb is None or not lb.size():
            return
        n = lb.size()
        cur = lb.curselection()
        i = cur[0] if cur else (-1 if yon == "Down" else 0)
        i = (i + 1) % n if yon == "Down" else (i - 1) % n
        lb.selection_clear(0, "end")
        lb.selection_set(i)
        lb.activate(i)
        lb.see(i)

    def _ac_onayla(self):
        if self._ac_key is None or self._ac_list is None:
            self._ac_gizle()
            return
        sec = self._ac_list.curselection()
        if not sec:
            self._ac_gizle()
            return
        val = self._ac_list.get(sec[0])
        k = self._ac_key
        self.vars[k].set(val)
        self._ac_gizle()
        cb = self.combos.get(k)
        if cb is not None:
            try:
                cb.icursor("end")
                cb.focus_set()
            except Exception:
                pass
        self._canli_filtrele()

    # ─────────────────────────── sorgu ───────────────────────────
    def sorgula(self):
        if self.running:
            return
        values = {k: v.get() for k, v in self.vars.items()}
        values.update({k: v.get() for k, v in self.taraf_vars.items()})
        values["taraf_tur"] = self.taraf_tur_var.get()
        durum_kod = self._durum_kod.get(self.durum_var.get(), icra_core.DURUM_VARSAYILAN)
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._gercek_sorgula(values, durum_kod),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._gercek_sorgula(values, durum_kod)

    def _gercek_sorgula(self, values, durum_kod):
        self._son_degerler, self._son_durum_kod = values, durum_kod
        self.kontrol.sifirla()
        self._set_running(True)
        self.durum_lbl.config(text="Sorgulanıyor…")
        threading.Thread(target=self._ara_bg, args=(values, durum_kod), daemon=True).start()

    def duraklat_toggle(self):
        if not self.running:
            return
        paused = self.kontrol.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self.running:
            self.kontrol.durdur()

    def _set_running(self, running):
        self.running = running
        st = "disabled" if running else "normal"
        self.sorgula_btn.set_state(st)
        self.duraklat_btn.set_state("normal" if running else "disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal" if running else "disabled")
        if not running:
            toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    def _ara_bg(self, values, durum_kod):
        # 1) ÖNCE DB'den göster (UYAP beklemeden) — ofis kapalı olsa bile veri görünür.
        try:
            db_kayitlar = icra_core.db_dosyalari_getir(values, durum_kod)
        except Exception:
            db_kayitlar = []
        if db_kayitlar:
            self.result_q.put(("db", db_kayitlar))
        # 2) SONRA canlı UYAP: yeni/eksik dosyaları ekle (birim+yıl+sıra ile birleştir).
        try:
            kayitlar, _payload, yeni_dosyalar = self.engine.ara(values, durum_kod, kontrol=self.kontrol)
            birlesik = self._birlestir(db_kayitlar, kayitlar)
            self.result_q.put(("ok", (birlesik, yeni_dosyalar)))
        except icra_core.OturumHatasi as e:
            self.result_q.put(("oturum", str(e)))
        except Exception as e:
            # Canlı sorgu başarısız (ör. ofis 8800 kapalı): DB sonuçları ekranda kalsın.
            if db_kayitlar:
                self.result_q.put(("db_son", str(e)))
            else:
                self.result_q.put(("hata", str(e)))

    def _birlestir(self, db_kayitlar, canli_kayitlar):
        """DB + canlı kayıtları birim+dosyaNo (≈ birim+yıl+sıra) ile ALAN ALAN
        birleştirir (TÜM kaydı DEĞİŞTİRMEZ).

        KRİTİK DÜZELTME (kullanıcı bulgusu, 2026-08-14: "Tüm Dosyalarım'da
        güncellenmiş veri varken İcra Dosyalarım'da Sorgula dedim, var olan
        veri gitti"): canlı UYAP'ın search_phrase_detayli.ajx yanıtı yalnız
        KAPAK KÜNYESİ içerir — alacaklı/borçlu HİÇ GELMEZ (bkz. models/
        icra_models/ingest.py modül başlığı). Eski sürüm canlı kaydı OLDUĞU
        GİBİ DB kaydının ÜSTÜNE yazıyordu; bu yüzden DB'de doğru olan
        alacaklı/borçlu EKRANDA boşa dönüyordu (veritabanının KENDİSİ
        ETKİLENMEDİ — yalnız bu ekrandaki birleşik görünüm bozuluyordu).
        Artık canlı bir alan yalnız DOLUYSA DB'deki karşılığının üstüne
        yazılır; canlı boşsa DB'deki değer KORUNUR."""
        def anahtar(r):
            return (str(r.get("birimAdi", "")), str(r.get("dosyaNo", "")))
        harita = {anahtar(r): dict(r) for r in (db_kayitlar or [])}
        for r in (canli_kayitlar or []):
            k = anahtar(r)
            if k in harita:
                birlesik = harita[k]
                for alan, deger in r.items():
                    if deger not in (None, "", []):
                        birlesik[alan] = deger
            else:
                harita[k] = r
        return list(harita.values())

    def _db_goster(self, kayitlar):
        """Canlı sorgu SÜRERKEN DB sonuçlarını anında göster (running korunur)."""
        self.all_records = kayitlar
        self.sort_key = None
        self._tabloyu_kur()
        self.durum_lbl.config(text=f"DB'den {len(kayitlar)} · canlı sorgu sürüyor…")

    def _db_son(self, msg):
        """Canlı sorgu yapılamadı (ofis kapalı) ama DB sonuçları ekranda kalsın."""
        self._set_running(False)
        self.durum_lbl.config(text=f"DB'den {len(self.all_records)} (ofis kapalı/erişilemedi)")
        self._log("ℹ️ Canlı UYAP yapılamadı; yalnız veritabanı gösteriliyor: " + str(msg))

    def _sorgu_bitti(self, tip, veri):
        self._set_running(False)
        if tip == "ok":
            kayitlar, yeni_dosyalar = veri
            self.all_records = kayitlar
            self.sort_key = None
            self.durum_lbl.config(text=f"{len(kayitlar)} sonuç")
            self._tabloyu_kur()
            if yeni_dosyalar:
                count = len(yeni_dosyalar)
                list_str = "\n".join(f"- {item['dosya_no']} ({item['birim_adi']})" for item in yeni_dosyalar)
                self._log(f"ℹ️ Veritabanına {count} yeni dosya eklendi:\n{list_str}")
        elif tip == "oturum":
            self.durum_lbl.config(text="Oturum/bağlantı hatası")
            self._log("⛔ Oturum/yetki hatası: " + veri)
            self._log("   UYAP Bağlantısı modülünden Paylaş/Al ile bağlantıyı başlatın.")
        else:
            self.durum_lbl.config(text="Hata")
            self._log("⚠️ Sorgu hatası: " + veri)
            self._log("   Yerel ofis (127.0.0.1:8800) açık ve UYAP Bağlantısı aktif mi?")

    # ─────────────────────────── Dosya Görüntüle (Dosya Bilgileri ayrıntısı) ──
    def _dosya_goruntule(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden bir dosya seçin.")
            return
        try:
            rec = self._gorunen_kayitlar[int(secim[0])]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satır bulunamadı; listeyi yenileyin.")
            return
        self.detay_btn.set_state("disabled")
        self.durum_lbl.config(text="Dosya ayrıntısı alınıyor…")
        threading.Thread(target=self._dosya_goruntule_bg, args=(rec,), daemon=True).start()

    def _dosya_goruntule_bg(self, rec):
        # Stale-while-revalidate GERİ ALINDI (kullanıcı bulgusu, 2026-07-13):
        # önbellekli 'ham' takibin türü/şekli/yolu için yalnız ÇIPLAK UYAP
        # kodunu içeriyordu ("1"/"0" vb.) — okunabilir '(tahmini)' metni
        # BİLEREK DB'ye yazılmıyor, yalnız CANLI yanıtta üretiliyor; sessiz
        # arka plan tazelemesi bu metni kullanıcıya bir daha HİÇ göstermiyordu
        # — gerçek regresyon. Düzeltme netleşene kadar HER ZAMAN canlı sorgu.
        sonuc = dosya_core.dosya_detay_goster_ve_kaydet(rec, log_fn=self._log)
        # Barkod Sorgu (Kapalı Tebligat — PTT) modülünün DB'ye yazdığı gerçek
        # sonuçlar — dosyalarim_genel.py._dosya_goruntule_bg'deki AYNI çağrı,
        # bu ekranda eksikti (kullanıcı bulgusu, 2026-08-04: "barkod
        # veritabanında olan veri tüm dosyaları sorgulama ekranına
        # gelmiyor"). DOĞAL ANAHTARLA (birim+dosya_no) filtrelenir.
        try:
            barkodlar = dosya_core.tebligat_barkod_gecmisi_listele(
                dosya_id=rec.get("dosyaId"), birim_id=rec.get("birimId"),
                dosya_no=rec.get("dosyaNo"), dosya_tur_kod=rec.get("dosyaTurKod"))
        except Exception:
            barkodlar = []
        self.result_q.put(("detay", (sonuc, barkodlar)))

    def _detay_goster(self, veri):
        from tkinter import messagebox
        self.detay_btn.set_state("normal")
        sonuc, barkodlar = veri
        ham, aile, kaydedildi, hata, taraflar = sonuc
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
        if taraflar:
            satirlar.append("")
            satirlar.append("Taraf Bilgileri:")
            for t in taraflar:
                satir = f"  {t.get('rol', '')}: {t.get('adi', '')}"
                if t.get("vekil"):
                    satir += f" — Vekil: {t['vekil'].strip('[]')}"
                # Kesinleşme/Tebliğ Durumu (kullanıcı bulgusu, 2026-08-04:
                # Barkod Sorgu ile hesaplanan bu veri Dosya Görüntüle'de hiç
                # görünmüyordu) — yalnız borçlu satırlarında dolu olur, bkz.
                # dosya_core._taraflar_kesinlesme_bilgisi_ekle.
                if t.get("kesinlesmeDurumu"):
                    satir += f" — Kesinleşme: {t['kesinlesmeDurumu']}"
                if t.get("tebligatDurumu"):
                    satir += f" — Tebliğ: {t['tebligatDurumu']}"
                satirlar.append(satir)
        # Barkod Sorgu (Kapalı Tebligat — PTT) modülünün DB'ye yazdığı gerçek
        # sonuçlar — dosyalarim_genel.py._barkod_sekmesi'nin bu ekrandaki eşi
        # (kullanıcı bulgusu, 2026-08-04: "barkod veritabanında olan veri tüm
        # dosyaları sorgulama ekranına gelmiyor" — bu, DosyaTaraf.tebligatDurumu
        # enum'undan AYRI bir veri kaynağıdır, o enum hiçbir zaman otomatik
        # doldurulmuyor). En yeniden eskiye.
        if barkodlar:
            satirlar.append("")
            satirlar.append("Barkod / Tebligat Bilgileri:")
            for b in barkodlar:
                satir = (f"  {b.get('evrakAciklama') or '—'} — Barkod: {b.get('barkod') or '—'}"
                          f" — PTT Durumu: {b.get('pttDurumu') or '—'}")
                if b.get("sonIslemTarihi"):
                    satir += f" ({b['sonIslemTarihi']})"
                satir += f" — Tebliğ Mazbatası: {b.get('tebligMazbatasiVar') or '—'}"
                if b.get("kapaliTebligMazbatasiVar") == "Var":
                    satir += ", Kapalı Mazbata: Var"
                if b.get("sorguZamani"):
                    satir += f" — Sorgu: {b['sorguZamani']}"
                satirlar.append(satir)
        messagebox.showinfo(baslik, "\n".join(satirlar) +
                             ("\n\n(Yerel veritabanına kaydedildi.)" if kaydedildi else ""))
        if kaydedildi:
            # Kullanıcı bulgusu (İcra Dosyalarım, dosyalarim_genel.py:1004-1008'deki
            # eşi 2026-07-12'de düzeltilmişti): kayıt DB'ye yazılıyordu ama tablo
            # hiç yenilenmiyordu — kullanıcı elle "Sorgula"ya basmadan yeni veriyi
            # göremiyordu. Canlı UYAP'a GİTMEYEN, yalnız DB'den tazeleyen hafif
            # bir arka plan sorgusu (_ara_bg'nin 1. adımıyla aynı çağrı).
            threading.Thread(target=self._db_yenile_bg, daemon=True).start()

    def _db_yenile_bg(self):
        try:
            kayitlar = icra_core.db_dosyalari_getir(self._son_degerler, self._son_durum_kod)
        except Exception as e:
            self._log(f"⚠️ Liste tazelenemedi: {e}")
            return
        self.result_q.put(("db_yenile", kayitlar))

    def _db_yenile_goster(self, kayitlar):
        self.all_records = kayitlar
        self.sort_key = None
        self._tabloyu_kur()
        self.durum_lbl.config(text=f"Kaydedildi — liste güncellendi ({len(kayitlar)} dosya)")

    def temizle(self):
        for v in self.vars.values():
            v.set("")
        for v in self.taraf_vars.values():
            v.set("")
        self.durum_lbl.config(text="")
        self._tabloyu_doldur()

    # ─────────────────────────── günlük / polling ───────────────────────────
    def _log(self, mesaj):
        with self._loglock:
            self._logbuf.append(str(mesaj))

    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll(self):
        with self._loglock:
            satirlar, self._logbuf = self._logbuf, []
        for ln in satirlar:
            self._append_log(ln)
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                if tip == "birim":
                    self._combolari_doldur()
                elif tip == "db":
                    self._db_goster(veri)
                elif tip == "db_acilis":
                    if not self.running:      # kullanıcı sorgu başlattıysa karışma
                        self.all_records = veri
                        self.sort_key = None
                        self._tabloyu_kur()
                        self.durum_lbl.config(
                            text=f"DB'den {len(veri)} dosya · Sorgula ile UYAP'tan güncelle")
                elif tip == "db_son":
                    self._db_son(veri)
                elif tip == "db_yenile":
                    self._db_yenile_goster(veri)
                elif tip == "detay":
                    self._detay_goster(veri)
                else:
                    self._sorgu_bitti(tip, veri)
        except queue.Empty:
            pass
        self.app.after(200, self._poll)
