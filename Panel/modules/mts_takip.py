# -*- coding: utf-8 -*-
"""
MTS Takip Açma — Gömülü Panel (güçlü bağlantı / iş kuyruğu sürümü)
=================================================================
Bu modül, `Dosya Açılış/MTS Takip Açılış/mts_gui_api.py`'nin ARAYÜZ DÜZENİNİ panel
temasıyla yeniden kurar; ama UYAP'a Playwright/kendi Chrome'u ile DEĞİL, panelin
mevcut bağlantısı (ofis ajanı, `127.0.0.1:8800` iş kuyruğu) üzerinden konuşur.

Mimari ([[gorsel-birlestirme-kurali]] + [[adil-siralama-okuma-onceligi]]):
  • Excel/XML ayrıştırma İSTEMCİDE yapılır (uyap_core.mts.parse — tarayıcısız, pandas).
  • Açma işi `coklu_takip_ac` iş türüyle ofise gönderilir; ofis prepare→(onay)→finalize
    (taslak + UDF + e-imza + evrak) adımlarını CANLI oturumla yürütür (uyap_core.mts.takip).
  • İlerleme/günlük/onay/iptal iş kuyruğu HTTP sözleşmesiyle (is_kuyrugu) sürülür.
  • Bağlantı PAYLAŞAN/LOKAL-ALAN/UZAK-ALAN fark etmeksizin aynı yerel adrestir; bu modül
    "hangi moddaysa o bağlantı" üzerinden çalışır — ayrı oturum dosyası/Playwright YOKTUR.

Eklenti: mağazadan 'mts_takip_acma' etkin değilse menüde görünmez; kaldırılınca bu panel
menüden çıkar, orijinal dosyalar ve ofis kodu olduğu gibi durur.
"""

import os
import sys
import base64
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from theme import C, RoundButton
from . import is_kuyrugu
from . import takip_sonuc_raporu


# ── uyap_core (ofis çekirdeği) importu için yol ekle ─────────────────────────
def _uyap_core_ekle():
    """`Uyap Haricen Giriş` klasörünü sys.path'e ekler (uyap_core orada)."""
    here = os.path.dirname(os.path.abspath(__file__))
    kok = os.path.dirname(os.path.dirname(here))          # .../Kararlı
    uhg = os.path.join(kok, "Uyap Haricen Giriş")
    if uhg not in sys.path:
        sys.path.insert(0, uhg)
    return uhg


# Mod etiketi -> iş kuyruğu onay_modu
MODLAR = [
    ("yok",     "⚡  Otomatik",     "Seçili takipleri sırayla, kesintisiz açar."),
    ("tek_tek", "👁  Tek Tek Onay", "Her takip hazırlanınca bilgileri gösterir; onaylarsanız açar."),
    ("toplu",   "🗂  Toplu Onay",   "Önce hepsini hazırlar; tek listede gözden geçirip seçtiklerinizi açar."),
]


class MtsTakipPanel:
    BASLIK = "MTS Takip Açma"

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app

        # ── Durum ──
        self.kaynak_yolu = None
        self.takipler = []              # uyap_core.mts.Takip listesi
        self.alacaklilar = []           # benzersiz alacaklı adları (sıralı)
        self.vekalet_map = {}           # {alacakli: dosya_yolu}
        self.dayanak_map = {}           # {dosya_no: dosya_yolu}
        self.secili = set()             # açılacak takiplerin dosya_no'ları
        # Ödeme/tebligat, takip-açma modundan (tam otomatik/toplu/tek tek) BAĞIMSIZ,
        # HER DOSYA için ayrı ayrı işaretlenir (bkz. kullanıcı talebi 2026-08-11) — Bekleyen
        # listesindeki 💳/📨 kutularıyla. Varsayılan: ödeme işaretli (canlı doğrulandı),
        # tebligat işaretsiz (mükerrer ücretlendirme filtresi izole test bekliyor).
        self.odeme_secili = set()       # ödeme yapılacak dosya_no'lar
        self.tebligat_secili = set()    # tebligat gönderilecek dosya_no'lar
        self.durum = {}                 # dosya_no -> bekleyen/aktif/tamam/hata/atlandi
        self.hata_mesaj = {}            # dosya_no -> hata nedeni
        self.secili_dosya_no = None
        self._bekleyen_sira_yon = None  # None | "artan" | "azalan" — "Takip" başlığı tıklamasıyla değişir

        self.job_id = None
        self._calisiyor = False
        self._poll_after = None
        self._excel_job_id = None
        self._excel_calisiyor = False
        self._tebligat_job_id = None
        self._tebligat_calisiyor = False
        self._tebligat_log_sayac = 0
        self._log_sayac = 0             # işlenen log satırı sayısı (artımlı)
        self._onay_aktif = False        # şu an bir onay isteği ekranda mı
        self._son_sonuc = {}            # son biten işin job.result'ı (Excel'e Aktar için)

        self._mod_var = tk.StringVar(value="yok")
        self._il_var = tk.StringVar(value="İzmir")
        self._adliye_var = tk.StringVar(value="İzmir")
        # Takip AÇILDIKTAN SONRAKİ iki ayrı aşama (ödeme/tebligat) — HANGİ dosyalarda
        # yapılacağı Bekleyen listesindeki 💳/📨 kutularıyla HER DOSYA için ayrı seçilir
        # (self.odeme_secili/self.tebligat_secili); burdaki iki mod, işaretli dosyalar için
        # onayın NASIL alınacağını belirler (bkz. job_handlers._coklu_takip_ac).
        self._odeme_mod_var = tk.StringVar(value="yok")
        self._tebligat_mod_var = tk.StringVar(value="yok")

        self._init_style()
        self._build()
        self._baglanti_kontrol()

    # ─────────────────────────────────────────────────────── stil
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Mts.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=25, font=self.app.f_body)
        style.configure("Mts.Treeview.Heading", background=C.SIDEBAR, foreground=C.INK,
                        relief="flat", font=self.app.f_nav_b, padding=5)
        style.map("Mts.Treeview", background=[("selected", C.SAGE_TINT)],
                  foreground=[("selected", C.INK)])
        style.configure("Mts.Horizontal.TProgressbar", troughcolor=C.SIDEBAR,
                        background=C.SAGE, borderwidth=0)

    # ─────────────────────────────────────────────────────── arayüz
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True)

        # Kaydırılabilir gövde (içerik uzun)
        canvas = tk.Canvas(wrap, bg=C.BG, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        vsb = tk.Scrollbar(wrap, command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=vsb.set)
        body = tk.Frame(canvas, bg=C.BG)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        ic = tk.Frame(body, bg=C.BG)
        ic.pack(fill="both", expand=True, padx=40, pady=(30, 24))

        # Başlık
        tk.Label(ic, text=self.BASLIK, bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(ic, text="Merkezi Takip Sistemi üzerinden toplu icra takibi açılışı — "
                          "panelin UYAP bağlantısı üzerinden (tarayıcısız).",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 0))
        self.baglanti_lbl = tk.Label(ic, text="● Bağlantı kontrol ediliyor…", bg=C.BG,
                                     fg=C.INK_FAINT, font=self.app.f_small)
        self.baglanti_lbl.pack(anchor="w", pady=(4, 0))
        tk.Frame(ic, bg=C.LINE, height=1).pack(fill="x", pady=(16, 0))

        self._kaynak_bar_kur(ic)
        self._ayar_bar_kur(ic)
        self._onay_bar_kur(ic)
        self._sutunlar_kur(ic)
        self._detay_kur(ic)
        self._log_kur(ic)

        self._listeleri_ciz()
        self._butonlar_guncelle()

    # ── kaynak (XML/Excel) seçim barı ──
    def _kaynak_bar_kur(self, parent):
        bar = tk.Frame(parent, bg=C.BG)
        bar.pack(fill="x", pady=(16, 0))
        self.sec_btn = RoundButton(bar, "📂  XML / Excel Seç", command=self._kaynak_sec,
                                   kind="primary", font=self.app.f_nav_b, height=36)
        self.sec_btn.pack(side="left", ipadx=6)
        self.kaldir_btn = RoundButton(bar, "Kaldır", command=self._kaynak_kaldir,
                                      kind="ghost", font=self.app.f_nav_b, height=36)
        self.kaldir_btn.pack(side="left", padx=(8, 0))
        self.kaynak_lbl = tk.Label(bar, text="Henüz dosya seçilmedi.", bg=C.BG,
                                   fg=C.INK_SOFT, font=self.app.f_body)
        self.kaynak_lbl.pack(side="left", padx=14)
        self.ozet_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.SAGE_DK,
                                 font=self.app.f_nav_b)
        self.ozet_lbl.pack(side="right")

    # ── mod + il/adliye + dayanak + başlat/durdur ──
    def _ayar_bar_kur(self, parent):
        kart = tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)

        # Mod
        tk.Label(ic, text="ÇALIŞMA MODU", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")
        mod_satir = tk.Frame(ic, bg=C.CARD)
        mod_satir.pack(fill="x", pady=(4, 10))
        for deger, baslik, aciklama in MODLAR:
            rb = tk.Radiobutton(mod_satir, text=baslik, variable=self._mod_var,
                                value=deger, bg=C.CARD, fg=C.INK, selectcolor=C.CARD,
                                activebackground=C.CARD, activeforeground=C.INK,
                                font=self.app.f_body, bd=0, highlightthickness=0,
                                cursor="hand2")
            rb.pack(side="left", padx=(0, 18))

        # Ödeme & Tebligat (takip AÇILDIKTAN SONRA, ayrı onay turlarıyla). HANGİ dosyalarda
        # yapılacağı — çalışma modundan (tam otomatik/toplu/tek tek) BAĞIMSIZ olarak — sağdaki
        # Bekleyen listesinde HER DOSYA için ayrı 💳/📨 kutusuyla işaretlenir; buradaki iki mod
        # yalnız işaretlenen dosyalarda onayın NASIL alınacağını belirler. Reddedilen/işaretsiz
        # dosya bir önceki durumda (açık / ödenmiş) kalır, UYAP'tan elle ilerletilebilir.
        tk.Label(ic, text="ÖDEME VE TEBLİGAT ONAY BİÇİMİ", bg=C.CARD,
                 fg=C.INK_SOFT, font=self.app.f_nav_b).pack(anchor="w", pady=(6, 0))
        tk.Label(ic, text="Hangi dosyalarda yapılacağını Bekleyen listesindeki 💳/📨 "
                          "kutularıyla işaretleyin — buradaki seçim yalnız NASIL onaylanacağını belirler.",
                bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small,
                wraplength=520, justify="left").pack(anchor="w")
        ot_satir = tk.Frame(ic, bg=C.CARD)
        ot_satir.pack(fill="x", pady=(4, 10))

        odeme_col = tk.Frame(ot_satir, bg=C.CARD)
        odeme_col.pack(side="left", padx=(0, 32), anchor="n")
        tk.Label(odeme_col, text="💳 Ödeme", bg=C.CARD, fg=C.INK,
                font=self.app.f_body).pack(anchor="w")
        for deger, etiket in (("yok", "Onaysız (işaretliyse hemen öde)"), ("tek_tek", "Her dosyada onayla"),
                             ("toplu", "Toplu önizle, tek onay")):
            tk.Radiobutton(odeme_col, text=etiket, variable=self._odeme_mod_var, value=deger,
                          bg=C.CARD, fg=C.INK_SOFT, selectcolor=C.CARD, activebackground=C.CARD,
                          activeforeground=C.INK, font=self.app.f_small, bd=0,
                          highlightthickness=0, cursor="hand2").pack(anchor="w", padx=(18, 0))

        tebligat_col = tk.Frame(ot_satir, bg=C.CARD)
        tebligat_col.pack(side="left", anchor="n")
        tk.Label(tebligat_col, text="📨 Tebligat", bg=C.CARD, fg=C.INK,
                font=self.app.f_body).pack(anchor="w")
        for deger, etiket in (("yok", "Onaysız (işaretliyse hemen gönder)"), ("tek_tek", "Her dosyada onayla"),
                             ("toplu", "Toplu önizle, tek onay")):
            tk.Radiobutton(tebligat_col, text=etiket, variable=self._tebligat_mod_var, value=deger,
                          bg=C.CARD, fg=C.INK_SOFT, selectcolor=C.CARD, activebackground=C.CARD,
                          activeforeground=C.INK, font=self.app.f_small, bd=0,
                          highlightthickness=0, cursor="hand2").pack(anchor="w", padx=(18, 0))

        # İl / Adliye + dayanak
        alt = tk.Frame(ic, bg=C.CARD)
        alt.pack(fill="x")
        tk.Label(alt, text="İl", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(side="left")
        tk.Entry(alt, textvariable=self._il_var, width=12, bg="#FFFFFF", fg=C.INK,
                 relief="flat", font=self.app.f_body, highlightthickness=1,
                 highlightbackground=C.LINE, highlightcolor=C.SAGE).pack(
            side="left", padx=(6, 16), ipady=3)
        tk.Label(alt, text="Adliye", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(side="left")
        tk.Entry(alt, textvariable=self._adliye_var, width=12, bg="#FFFFFF", fg=C.INK,
                 relief="flat", font=self.app.f_body, highlightthickness=1,
                 highlightbackground=C.LINE, highlightcolor=C.SAGE).pack(
            side="left", padx=(6, 16), ipady=3)
        self.dayanak_btn = RoundButton(alt, "📁  Dayanak PDF Klasörü", command=self._dayanak_klasor,
                                       kind="ghost", font=self.app.f_nav_b, height=32)
        self.dayanak_btn.pack(side="left")
        self.dayanak_lbl = tk.Label(alt, text="", bg=C.CARD, fg=C.INK_FAINT,
                                    font=self.app.f_small)
        self.dayanak_lbl.pack(side="left", padx=(8, 0))

        # Başlat / Durdur
        eylem = tk.Frame(ic, bg=C.CARD)
        eylem.pack(fill="x", pady=(12, 0))
        self.baslat_btn = RoundButton(eylem, "▶  Seçili Takipleri Aç", command=self._baslat,
                                      kind="primary", font=self.app.f_nav_b, height=38)
        self.baslat_btn.pack(side="left", ipadx=8)
        self.durdur_btn = RoundButton(eylem, "⏹  Durdur", command=self._durdur,
                                      kind="stop", font=self.app.f_nav_b, height=38)
        self.durdur_btn.pack(side="left", padx=(8, 0))
        self.durum_lbl = tk.Label(eylem, text="", bg=C.CARD, fg=C.INK_SOFT,
                                  font=self.app.f_small)
        self.durum_lbl.pack(side="left", padx=14)
        self.ilerleme = ttk.Progressbar(eylem, mode="determinate",
                                        style="Mts.Horizontal.TProgressbar", length=200)
        self.ilerleme.pack(side="right")

    # ── onay çubuğu (tek_tek / toplu) ──
    def _onay_bar_kur(self, parent):
        self.onay_bar = tk.Frame(parent, bg=C.SAGE_TINT, highlightbackground=C.SAGE,
                                 highlightthickness=1)
        ic = tk.Frame(self.onay_bar, bg=C.SAGE_TINT)
        ic.pack(fill="x", padx=14, pady=10)
        self.onay_mesaj = tk.Label(ic, text="", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                                   font=self.app.f_nav_b, justify="left", anchor="w",
                                   wraplength=620)
        self.onay_mesaj.pack(side="left", fill="x", expand=True)
        self.onay_btn_cer = tk.Frame(ic, bg=C.SAGE_TINT)
        self.onay_btn_cer.pack(side="right")
        self._onay_parent = parent
        # başlangıçta gizli (pack edilmez)

    # ── üç sütun: alacaklılar | bekleyen | açılan ──
    def _sutunlar_kur(self, parent):
        sut = tk.Frame(parent, bg=C.BG)
        sut.pack(fill="x", pady=(14, 0))
        sut.columnconfigure(0, weight=3, uniform="s")
        sut.columnconfigure(1, weight=4, uniform="s")
        sut.columnconfigure(2, weight=4, uniform="s")

        # Alacaklılar & Vekaletler
        k0 = self._kart(sut)
        k0.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tk.Label(k0, text="🧾  Alacaklılar & Vekaletler", bg=C.CARD, fg=C.INK,
                 font=self.app.f_nav_b).pack(anchor="w", padx=12, pady=(12, 0))
        tk.Label(k0, text="Çift tıkla → vekalet ata.", bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_small).pack(anchor="w", padx=12)
        cer0 = tk.Frame(k0, bg=C.CARD)
        cer0.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.alacakli_tv = ttk.Treeview(cer0, columns=("ad", "sayi", "vek", "day"),
                                        show="headings", height=7, selectmode="browse",
                                        style="Mts.Treeview")
        for s, b, g in (("ad", "Alacaklı", 130), ("sayi", "Tk", 34),
                        ("vek", "Vekalet", 110), ("day", "Dayanak", 100)):
            self.alacakli_tv.heading(s, text=b)
            self.alacakli_tv.column(s, width=g, anchor="w")
        self.alacakli_tv.tag_configure("var", foreground=C.SAGE_DK)
        self.alacakli_tv.tag_configure("yok", foreground=C.INK_FAINT)
        self.alacakli_tv.pack(side="left", fill="both", expand=True)
        sb0 = tk.Scrollbar(cer0, command=self.alacakli_tv.yview)
        sb0.pack(side="right", fill="y")
        self.alacakli_tv.configure(yscrollcommand=sb0.set)
        self.alacakli_tv.bind("<Double-1>", lambda e: self._vekalet_ata())

        # Bekleyen
        k1 = self._kart(sut)
        k1.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.bekleyen_baslik = tk.Label(k1, text="⏳  Bekleyen (0)", bg=C.CARD, fg=C.INK,
                                       font=self.app.f_nav_b)
        self.bekleyen_baslik.pack(anchor="w", padx=12, pady=(12, 0))
        secbar = tk.Frame(k1, bg=C.CARD)
        secbar.pack(fill="x", padx=12, pady=(4, 0))
        RoundButton(secbar, "☑ Hepsi", command=self._hepsini_sec, kind="ghost",
                    font=self.app.f_small, height=28).pack(side="left")
        RoundButton(secbar, "☐ Hiçbiri", command=self._hicbiri, kind="ghost",
                    font=self.app.f_small, height=28).pack(side="left", padx=(6, 0))
        # 💳/📨 sütunlarını hızlı toplu işaretlemek için — satır satır tıklamak yerine.
        odetebbar = tk.Frame(k1, bg=C.CARD)
        odetebbar.pack(fill="x", padx=12, pady=(4, 0))
        RoundButton(odetebbar, "💳 Tümü", command=lambda: self._odeme_teb_toplu("odeme", True),
                    kind="ghost", font=self.app.f_small, height=26).pack(side="left")
        RoundButton(odetebbar, "💳 Hiçbiri", command=lambda: self._odeme_teb_toplu("odeme", False),
                    kind="ghost", font=self.app.f_small, height=26).pack(side="left", padx=(4, 10))
        RoundButton(odetebbar, "📨 Tümü", command=lambda: self._odeme_teb_toplu("tebligat", True),
                    kind="ghost", font=self.app.f_small, height=26).pack(side="left")
        RoundButton(odetebbar, "📨 Hiçbiri", command=lambda: self._odeme_teb_toplu("tebligat", False),
                    kind="ghost", font=self.app.f_small, height=26).pack(side="left", padx=(4, 0))
        cer1 = tk.Frame(k1, bg=C.CARD)
        cer1.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.bekleyen_tv = ttk.Treeview(cer1, columns=("sec", "ode", "teb", "ad"), show="headings",
                                       height=7, selectmode="browse", style="Mts.Treeview")
        self.bekleyen_tv.heading("sec", text="Aç")
        self.bekleyen_tv.heading("ode", text="💳")
        self.bekleyen_tv.heading("teb", text="📨")
        self.bekleyen_tv.heading("ad", text="Takip", command=self._bekleyen_basligi_tikla)
        self.bekleyen_tv.column("sec", width=32, anchor="center", stretch=False)
        self.bekleyen_tv.column("ode", width=32, anchor="center", stretch=False)
        self.bekleyen_tv.column("teb", width=32, anchor="center", stretch=False)
        self.bekleyen_tv.column("ad", width=220, anchor="w")
        self.bekleyen_tv.tag_configure("aktif", foreground=C.CLAY)
        self.bekleyen_tv.pack(side="left", fill="both", expand=True)
        sb1 = tk.Scrollbar(cer1, command=self.bekleyen_tv.yview)
        sb1.pack(side="right", fill="y")
        self.bekleyen_tv.configure(yscrollcommand=sb1.set)
        self.bekleyen_tv.bind("<Button-1>", self._bekleyen_tik)
        self.bekleyen_tv.bind("<<TreeviewSelect>>", self._bekleyen_secildi)

        # Açılan
        k2 = self._kart(sut)
        k2.grid(row=0, column=2, sticky="nsew")
        self.acilan_baslik = tk.Label(k2, text="✓  Açılan (0)", bg=C.CARD, fg=C.INK,
                                      font=self.app.f_nav_b)
        self.acilan_baslik.pack(anchor="w", padx=12, pady=(12, 0))
        tk.Label(k2, text="Açıldıkça/atlandıkça buraya geçer.", bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_small).pack(anchor="w", padx=12)
        acbar = tk.Frame(k2, bg=C.CARD)
        acbar.pack(fill="x", padx=12, pady=(4, 0))
        self.excel_btn = RoundButton(
            acbar, "📊 Ödenenleri Excel'e Aktar", command=self._excel_aktar,
            kind="ghost", font=self.app.f_small, height=28)
        self.excel_btn.pack(side="left")
        self.tebligat_btn = RoundButton(
            acbar, "📨 Tebligat Gönder (Test)", command=self._tebligat_gonder_test,
            kind="ghost", font=self.app.f_small, height=28)
        self.tebligat_btn.pack(side="left", padx=(6, 0))
        cer2 = tk.Frame(k2, bg=C.CARD)
        cer2.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.acilan_tv = ttk.Treeview(cer2, columns=("durum", "ad"), show="headings",
                                     height=7, selectmode="browse", style="Mts.Treeview")
        self.acilan_tv.heading("durum", text="Durum")
        self.acilan_tv.heading("ad", text="Takip")
        self.acilan_tv.column("durum", width=70, anchor="w")
        self.acilan_tv.column("ad", width=220, anchor="w")
        self.acilan_tv.tag_configure("tamam", foreground=C.SAGE_DK)
        self.acilan_tv.tag_configure("hata", foreground=C.CLAY)
        self.acilan_tv.tag_configure("atlandi", foreground=C.INK_FAINT)
        self.acilan_tv.pack(side="left", fill="both", expand=True)
        sb2 = tk.Scrollbar(cer2, command=self.acilan_tv.yview)
        sb2.pack(side="right", fill="y")
        self.acilan_tv.configure(yscrollcommand=sb2.set)
        self.acilan_tv.bind("<<TreeviewSelect>>", self._acilan_secildi)

    def _kart(self, parent):
        return tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)

    # ── detay (borçlular + kalemler) ──
    def _detay_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="both", expand=True, padx=14, pady=12)
        bas = tk.Frame(ic, bg=C.CARD)
        bas.pack(fill="x")
        self.detay_baslik = tk.Label(bas, text="Takip Ayrıntısı", bg=C.CARD, fg=C.INK,
                                     font=self.app.f_card_t)
        self.detay_baslik.pack(side="left")
        self.detay_rozet = tk.Label(bas, text="", bg=C.CARD, fg=C.INK_SOFT,
                                    font=self.app.f_nav_b)
        self.detay_rozet.pack(side="right")
        self.detay_bilgi = tk.Label(ic, text="Bir takip seçin.", bg=C.CARD, fg=C.INK,
                                    font=self.app.f_body, justify="left", anchor="w",
                                    wraplength=1100)
        self.detay_bilgi.pack(fill="x", pady=(8, 8))

        tab = tk.Frame(ic, bg=C.CARD)
        tab.pack(fill="both", expand=True)
        tab.columnconfigure(0, weight=1, uniform="t")
        tab.columnconfigure(1, weight=1, uniform="t")
        tk.Label(tab, text="Borçlular", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).grid(row=0, column=0, sticky="w")
        tk.Label(tab, text="Alacak Kalemleri", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).grid(row=0, column=1, sticky="w", padx=(6, 0))
        bc = tk.Frame(tab, bg=C.CARD)
        bc.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(4, 0))
        self.borclu_tv = ttk.Treeview(bc, columns=("ad", "soyad", "kimlik"),
                                     show="headings", height=4, style="Mts.Treeview")
        for s, b, g in (("ad", "Ad", 120), ("soyad", "Soyad", 120), ("kimlik", "Kimlik/Vergi", 140)):
            self.borclu_tv.heading(s, text=b)
            self.borclu_tv.column(s, width=g, anchor="w")
        self.borclu_tv.pack(fill="both", expand=True)
        kc = tk.Frame(tab, bg=C.CARD)
        kc.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(4, 0))
        self.kalem_tv = ttk.Treeview(kc, columns=("ad", "tutar", "oran", "tur"),
                                    show="headings", height=4, style="Mts.Treeview")
        for s, b, g, a in (("ad", "Alacak Adı", 170, "w"), ("tutar", "Tutar", 90, "e"),
                           ("oran", "Faiz Oranı", 80, "e"), ("tur", "Faiz Türü", 110, "w")):
            self.kalem_tv.heading(s, text=b)
            self.kalem_tv.column(s, width=g, anchor=a)
        self.kalem_tv.pack(fill="both", expand=True)
        tab.rowconfigure(1, weight=1)

    # ── günlük ──
    def _log_kur(self, parent):
        kart = tk.Frame(parent, bg="#FBFAF7", highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        kart.pack(fill="x", pady=(14, 0))
        bas = tk.Frame(kart, bg="#FBFAF7")
        bas.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(bas, text="📜  Günlük", bg="#FBFAF7", fg=C.INK,
                 font=self.app.f_nav_b).pack(side="left")
        RoundButton(bas, "Temizle", command=self._log_temizle, kind="ghost",
                    font=self.app.f_small, height=28).pack(side="right")
        RoundButton(bas, "📊  Bu Çalışmanın Sonucunu Excel'e Aktar",
                    command=self._sonuclari_excel_aktar, kind="ghost",
                    font=self.app.f_small, height=28).pack(side="right", padx=(0, 8))
        cer = tk.Frame(kart, bg="#FBFAF7")
        cer.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.log = tk.Text(cer, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=8, padx=4, pady=2,
                           state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(cer, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)

    # ─────────────────────────────────────────────────────── yardımcılar
    def _log_yaz(self, metin):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", str(metin).rstrip("\n") + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _log_temizle(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _sonuclari_excel_aktar(self):
        sonuc = self._son_sonuc or {}

        def _borclu(s):
            t = self._takip_bul(s.get("dosya_no"))
            return self._borclu_metni(t) if t else ""

        kolonlar = [
            ("Dosya No", "dosya_no"),
            ("Borçlu", _borclu),
            ("Durum", "durum"),
            ("Esas No", "gercek_dosya_no"),
            ("Dosya ID", "dosya_id"),
            ("Hata", lambda s: s.get("mesaj") or s.get("odeme_hata") or s.get("tebligat_hata") or ""),
        ]
        takip_sonuc_raporu.sonuclari_excel_yaz(
            self._log_yaz, sonuc.get("sonuclar") or [], kolonlar,
            "mts_takip_sonuclari.xlsx", sheet_title="MTS Takip Sonuçları")

    def _durum_yaz(self, metin):
        if self.durum_lbl.winfo_exists():
            self.durum_lbl.config(text=metin)

    def _borclu_metni(self, t, kisa=False):
        adlar = [f"{b.ad} {b.soyad}".strip() or (b.kimlik or "?") for b in t.borclular]
        if not adlar:
            return "(borçlu yok)"
        if kisa and len(adlar) > 2:
            return f"{adlar[0]}, {adlar[1]} +{len(adlar) - 2}"
        return ", ".join(adlar)

    def _takip_bul(self, dosya_no):
        for t in self.takipler:
            if str(t.dosya_no) == str(dosya_no):
                return t
        return None

    # ─────────────────────────────────────────────────────── bağlantı kontrol
    def _baglanti_kontrol(self):
        def isi():
            try:
                ok, mesaj = is_kuyrugu.ofis_erisilebilir()
            except Exception as e:
                ok, mesaj = False, str(e)
            self.app.after(0, lambda: self._baglanti_yaz(ok, mesaj))
        threading.Thread(target=isi, daemon=True).start()

    def _baglanti_yaz(self, ok, mesaj):
        if not self.baglanti_lbl.winfo_exists():
            return
        if ok:
            self.baglanti_lbl.config(text="● Ofis bağlantısı hazır (iş kuyruğu erişilebilir).",
                                     fg=C.SAGE_DK)
        else:
            self.baglanti_lbl.config(
                text="● Ofis bağlantısı yok — UYAP Bağlantısı (Paylaş/Al) başlatın.",
                fg=C.CLAY)

    # ─────────────────────────────────────────────────────── kaynak seç/kaldır
    def _kaynak_sec(self):
        if self._calisiyor:
            return
        yol = filedialog.askopenfilename(
            title="MTS XML veya Excel seçin",
            filetypes=[("XML / Excel", "*.xml *.xlsx *.xls"), ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.kaynak_lbl.config(text="Ayrıştırılıyor…")
        self.sec_btn.set_state("disabled")

        def isi():
            try:
                _uyap_core_ekle()
                from uyap_core.mts import parse as core_parse
                takipler = core_parse.kaynak_to_takipler(yol)
                self.app.after(0, lambda: self._kaynak_yuklendi(yol, takipler))
            except Exception as e:
                self.app.after(0, lambda e=e: self._kaynak_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _kaynak_yuklendi(self, yol, takipler):
        self.sec_btn.set_state("normal")
        if not takipler:
            self.kaynak_lbl.config(text="Dosyada açılacak takip bulunamadı.")
            return
        self.kaynak_yolu = yol
        self.takipler = takipler
        # benzersiz alacaklılar (sıralı)
        gor = []
        for t in takipler:
            if t.alacakli and t.alacakli not in gor:
                gor.append(t.alacakli)
        self.alacaklilar = gor
        self.durum = {str(t.dosya_no): "bekleyen" for t in takipler}
        self.hata_mesaj = {}
        self.secili = set(str(t.dosya_no) for t in takipler)   # varsayılan: hepsi seçili
        # Ödeme varsayılan İŞARETLİ (canlı doğrulandı), tebligat varsayılan İŞARETSİZ (bkz.
        # __init__ notu) — kullanıcı Bekleyen listesinden dosya bazında değiştirebilir.
        self.odeme_secili = set(str(t.dosya_no) for t in takipler)
        self.tebligat_secili = set()
        self.kaynak_lbl.config(text=os.path.basename(yol))
        self.ozet_lbl.config(text=f"{len(takipler)} takip · {len(gor)} alacaklı")
        self._log_yaz(f"✓ {len(takipler)} takip ayrıştırıldı ({os.path.basename(yol)}).")
        self._listeleri_ciz()
        self._butonlar_guncelle()

    def _kaynak_hata(self, e):
        self.sec_btn.set_state("normal")
        self.kaynak_lbl.config(text="Ayrıştırma hatası.")
        self._log_yaz(f"❌ Ayrıştırma hatası: {e}")
        messagebox.showerror("Ayrıştırma hatası", str(e))

    def _kaynak_kaldir(self):
        if self._calisiyor:
            return
        self.kaynak_yolu = None
        self.takipler = []
        self.alacaklilar = []
        self.vekalet_map = {}
        self.dayanak_map = {}
        self.secili = set()
        self.odeme_secili = set()
        self.tebligat_secili = set()
        self.durum = {}
        self.hata_mesaj = {}
        self.kaynak_lbl.config(text="Henüz dosya seçilmedi.")
        self.ozet_lbl.config(text="")
        self.dayanak_lbl.config(text="")
        self._listeleri_ciz()
        self._detay_goster(None)
        self._butonlar_guncelle()

    # ─────────────────────────────────────────────────────── vekalet / dayanak
    def _vekalet_ata(self):
        if self._calisiyor:
            return
        sec = self.alacakli_tv.selection()
        if not sec:
            return
        idx = self.alacakli_tv.index(sec[0])
        if idx >= len(self.alacaklilar):
            return
        alacakli = self.alacaklilar[idx]
        yol = filedialog.askopenfilename(
            title=f"{alacakli} için vekalet seçin",
            filetypes=[("Vekalet/İmzalı", "*.udf *.pdf *.xml"), ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.vekalet_map[alacakli] = yol
        self._log_yaz(f"📎 {alacakli} → {os.path.basename(yol)}")
        self._alacakli_ciz()

    def _dayanak_klasor(self):
        if self._calisiyor or not self.takipler:
            if not self.takipler:
                messagebox.showinfo("Dayanak", "Önce bir XML/Excel seçin.")
            return
        klasor = filedialog.askdirectory(title="Dayanak PDF'lerinin bulunduğu klasör")
        if not klasor:
            return
        self.dayanak_btn.set_state("disabled")
        self.dayanak_lbl.config(text="PDF'ler taranıyor…")
        self._log_yaz(f"📁 Dayanak taranıyor: {os.path.basename(klasor)} …")
        takipler = list(self.takipler)

        def isi():
            try:
                eslesme = self._pdf_dayanak_tara(klasor, takipler)
                self.app.after(0, lambda: self._dayanak_bitti(klasor, eslesme))
            except Exception as e:
                self.app.after(0, lambda e=e: self._dayanak_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    @staticmethod
    def _pdf_metin(pdf_yolu):
        """PDF metnini çıkarır. Önce PyMuPDF (fitz), olmazsa pypdf kullanır —
        ikisi de panel venv'inde mevcuttur (pdfplumber gerekmez)."""
        try:
            import fitz  # PyMuPDF
            with fitz.open(pdf_yolu) as d:
                return "".join(pg.get_text() or "" for pg in d)
        except ImportError:
            pass
        from pypdf import PdfReader
        r = PdfReader(pdf_yolu)
        return "".join((s.extract_text() or "") for s in r.pages)

    def _pdf_dayanak_tara(self, klasor, takipler):
        """Orijinal mts_takip_acan.pdf_dayanak_tara ile aynı çift doğrulama:
        (1) hizmet_abone_no PDF metninde geçer  VE
        (2) borçlu isim parçalarından en az biri dosya adında veya PDF metninde geçer.
        {dosya_no(str): tam_yol} döner."""
        # Eşleştirme anahtarı: (dosya_no, abone_no, isim_parcalari)
        anahtarli = []
        for t in takipler:
            abone = (t.hizmet_abone_no or "").strip()
            if not abone:
                continue
            isim_parcalari = []
            for b in t.borclular:
                for kelime in (b.ad, b.soyad):
                    k = (kelime or "").strip().upper()
                    if k and len(k) > 1:
                        isim_parcalari.append(k)
            anahtarli.append((str(t.dosya_no), abone, isim_parcalari))

        if not anahtarli:
            self.app.after(0, lambda: self._log_yaz(
                "ℹ Hiçbir takipte hizmet abone no yok; PDF eşleştirme atlandı."))
            return {}

        pdf_listesi = [os.path.join(klasor, f) for f in os.listdir(klasor)
                       if f.lower().endswith(".pdf")]
        self.app.after(0, lambda n=len(pdf_listesi), k=len(anahtarli): self._log_yaz(
            f"   {n} PDF, {k} takip taranıyor…"))

        sonuc = {}
        for pdf_yolu in pdf_listesi:
            dosya_adi_buyuk = os.path.basename(pdf_yolu).upper()
            try:
                metin = self._pdf_metin(pdf_yolu)
                metin_buyuk = metin.upper()
                for dosya_no, abone_no, isim_parcalari in anahtarli:
                    if dosya_no in sonuc:
                        continue
                    if abone_no not in metin_buyuk and abone_no not in metin:
                        continue
                    isim_eslesti = any(
                        p in dosya_adi_buyuk or p in metin_buyuk
                        for p in isim_parcalari) if isim_parcalari else True
                    if isim_eslesti:
                        sonuc[dosya_no] = pdf_yolu
                    # orijinaldeki gibi break YOK: bir PDF birden çok takibe uyabilir
            except Exception as e:
                self.app.after(0, lambda f=os.path.basename(pdf_yolu), e=e:
                               self._log_yaz(f"   ⚠ PDF okunamadı: {f}: {e}"))
        return sonuc

    def _dayanak_bitti(self, klasor, eslesme):
        self.dayanak_btn.set_state("normal")
        self.dayanak_map = eslesme
        n = len(self.dayanak_map)
        self.dayanak_lbl.config(text=f"{n}/{len(self.takipler)} eşleşti")
        self._log_yaz(f"📁 Dayanak: {n}/{len(self.takipler)} takip eşleşti "
                      f"({os.path.basename(klasor)}).")
        self._alacakli_ciz()

    def _dayanak_hata(self, e):
        self.dayanak_btn.set_state("normal")
        self.dayanak_lbl.config(text="Tarama hatası.")
        if isinstance(e, ImportError):
            self._log_yaz("❌ PDF okuyucu yok (PyMuPDF/pypdf): pip install pymupdf")
            messagebox.showerror("Dayanak", "PDF okuyucu bulunamadı.\n\npip install pymupdf")
        else:
            self._log_yaz(f"❌ Dayanak tarama hatası: {e}")
            messagebox.showerror("Dayanak tarama hatası", str(e))

    # ─────────────────────────────────────────────────────── liste çizimleri
    def _listeleri_ciz(self):
        self._alacakli_ciz()
        self._bekleyen_ciz()
        self._acilan_ciz()

    def _alacakli_ciz(self):
        self.alacakli_tv.delete(*self.alacakli_tv.get_children())
        sayim = {}
        for t in self.takipler:
            sayim[t.alacakli] = sayim.get(t.alacakli, 0) + 1
        for alacakli in self.alacaklilar:
            yol = self.vekalet_map.get(alacakli)
            vek = ("✓ " + os.path.basename(yol)) if yol else "— seçilmedi"
            day_say = sum(1 for t in self.takipler
                          if t.alacakli == alacakli and str(t.dosya_no) in self.dayanak_map)
            day = f"✓ {day_say}" if day_say else "—"
            self.alacakli_tv.insert("", "end", values=(alacakli, sayim.get(alacakli, 0), vek, day),
                                    tags=("var" if yol else "yok",))

    def _bekleyen_ciz(self):
        self.bekleyen_tv.delete(*self.bekleyen_tv.get_children())
        bekleyen = [t for t in self.takipler
                    if self.durum.get(str(t.dosya_no), "bekleyen") in ("bekleyen", "aktif")]
        if self._bekleyen_sira_yon:
            bekleyen.sort(key=lambda t: self._borclu_metni(t, kisa=True).lower(),
                          reverse=(self._bekleyen_sira_yon == "azalan"))
        for t in bekleyen:
            dn = str(t.dosya_no)
            isaret = "☑" if dn in self.secili else "☐"
            ode_isaret = "☑" if dn in self.odeme_secili else "☐"
            teb_isaret = "☑" if dn in self.tebligat_secili else "☐"
            tag = "aktif" if self.durum.get(dn) == "aktif" else ""
            abone = (t.hizmet_abone_no or "").strip()
            abone_kismi = f" (Abone: {abone})" if abone else ""
            etiket = f" {self._borclu_metni(t, kisa=True)}{abone_kismi}  ·  Dosya {dn}"
            self.bekleyen_tv.insert("", "end", iid=dn, values=(isaret, ode_isaret, teb_isaret, etiket),
                                    tags=(tag,) if tag else ())
        sec_say = sum(1 for t in bekleyen if str(t.dosya_no) in self.secili)
        self.bekleyen_baslik.config(text=f"⏳  Bekleyen ({len(bekleyen)}) — ☑ {sec_say}")
        ok = {"artan": " ▲", "azalan": " ▼"}.get(self._bekleyen_sira_yon, "")
        self.bekleyen_tv.heading("ad", text="Takip" + ok)

    def _acilan_ciz(self):
        self.acilan_tv.delete(*self.acilan_tv.get_children())
        rozet = {"tamam": "✓ Açıldı", "hata": "✗ Hata", "atlandi": "⤼ Atlandı"}
        n_tamam = n_hata = 0
        for t in self.takipler:
            dn = str(t.dosya_no)
            d = self.durum.get(dn)
            if d not in ("tamam", "hata", "atlandi"):
                continue
            if d == "tamam":
                n_tamam += 1
            elif d == "hata":
                n_hata += 1
            etiket = f"{self._borclu_metni(t, kisa=True)} · Dosya {dn}"
            self.acilan_tv.insert("", "end", iid="a_" + dn,
                                  values=(rozet.get(d, d), etiket), tags=(d,))
        bas = f"✓  Açılan ({n_tamam})"
        if n_hata:
            bas += f" — ⚠ {n_hata} hata"
        self.acilan_baslik.config(text=bas)

    # ─────────────────────────────────────────────────────── seçim olayları
    def _bekleyen_tik(self, event):
        if self._calisiyor:
            return
        row = self.bekleyen_tv.identify_row(event.y)
        if not row:
            return
        kolon = self.bekleyen_tv.identify_column(event.x)
        hedef = {"#1": self.secili, "#2": self.odeme_secili, "#3": self.tebligat_secili}.get(kolon)
        if hedef is None:
            return
        if row in hedef:
            hedef.discard(row)
        else:
            hedef.add(row)
        self._bekleyen_ciz()
        self._butonlar_guncelle()
        return "break"

    def _odeme_teb_toplu(self, hangi, isaretle):
        """💳/📨 sütununu bekleyen listedeki TÜM dosyalar için tek seferde işaretler/kaldırır."""
        if self._calisiyor:
            return
        hedef = self.odeme_secili if hangi == "odeme" else self.tebligat_secili
        bekleyen_no = [str(t.dosya_no) for t in self.takipler
                      if self.durum.get(str(t.dosya_no), "bekleyen") in ("bekleyen", "aktif")]
        if isaretle:
            hedef.update(bekleyen_no)
        else:
            hedef.difference_update(bekleyen_no)
        self._bekleyen_ciz()

    def _bekleyen_secildi(self, _e=None):
        sec = self.bekleyen_tv.selection()
        if sec:
            self.secili_dosya_no = sec[0]
            self._detay_goster(sec[0])

    def _acilan_secildi(self, _e=None):
        sec = self.acilan_tv.selection()
        if sec:
            dn = sec[0][2:]   # "a_" önekini at
            self.secili_dosya_no = dn
            self._detay_goster(dn)

    def _hepsini_sec(self):
        if self._calisiyor:
            return
        self.secili = set(str(t.dosya_no) for t in self.takipler
                          if self.durum.get(str(t.dosya_no), "bekleyen") in ("bekleyen", "aktif"))
        self._bekleyen_ciz()
        self._butonlar_guncelle()

    def _hicbiri(self):
        if self._calisiyor:
            return
        self.secili.clear()
        self._bekleyen_ciz()
        self._butonlar_guncelle()

    def _bekleyen_basligi_tikla(self):
        """'Takip' başlığına tıklanınca alfabetik sıralamayı A-Z / Z-A arasında değiştirir."""
        self._bekleyen_sira_yon = "azalan" if self._bekleyen_sira_yon == "artan" else "artan"
        self._bekleyen_ciz()

    # ─────────────────────────────────────────────────────── detay
    def _detay_goster(self, dosya_no):
        self.borclu_tv.delete(*self.borclu_tv.get_children())
        self.kalem_tv.delete(*self.kalem_tv.get_children())
        t = self._takip_bul(dosya_no) if dosya_no else None
        if t is None:
            self.detay_baslik.config(text="Takip Ayrıntısı")
            self.detay_rozet.config(text="")
            self.detay_bilgi.config(text="Bir takip seçin.")
            return
        dn = str(t.dosya_no)
        rozet = {"bekleyen": ("⏳ Bekliyor", C.INK_SOFT), "aktif": ("▶ Açılıyor", C.CLAY),
                 "tamam": ("✓ Açıldı", C.SAGE_DK), "hata": ("✗ Hata", C.CLAY),
                 "atlandi": ("⤼ Atlandı", C.INK_FAINT)}.get(
            self.durum.get(dn, "bekleyen"), ("", C.INK_SOFT))
        self.detay_baslik.config(text=f"Takip Ayrıntısı — Dosya {dn}")
        self.detay_rozet.config(text=rozet[0], fg=rozet[1])
        vk = self.vekalet_map.get(t.alacakli)
        vk_m = ("📎 " + os.path.basename(vk)) if vk else "📎 vekalet seçilmedi"
        bilgi = (f"Borçlu(lar):  {self._borclu_metni(t)}\n"
                 f"Alacaklı:  {t.alacakli}        Vekalet:  {vk_m}\n"
                 f"IBAN:  {t.iban or '-'}        Abone No:  {t.abone_no or t.hizmet_abone_no or '-'}        "
                 f"İlamsız Tutar:  {t.ilamsiz_tutar or '-'}\n"
                 f"Talep:  {t.aciklama or '-'}")
        if self.durum.get(dn) == "hata" and self.hata_mesaj.get(dn):
            bilgi += f"\n\n⚠ Hata nedeni:  {self.hata_mesaj[dn]}"
        self.detay_bilgi.config(text=bilgi)
        for b in t.borclular:
            self.borclu_tv.insert("", "end", values=(b.ad, b.soyad, b.kimlik))
        for k in t.alacak_kalemleri:
            self.kalem_tv.insert("", "end", values=(k.ad, k.tutar, k.faiz_oran, k.faiz_tur))

    # ─────────────────────────────────────────────────────── buton durumları
    def _butonlar_guncelle(self):
        var = bool(self.takipler)
        sec_var = bool(self.secili)
        if self._calisiyor:
            self.baslat_btn.set_state("disabled")
            self.durdur_btn.set_state("normal")
            self.sec_btn.set_state("disabled")
            self.kaldir_btn.set_state("disabled")
        else:
            self.baslat_btn.set_state("normal" if (var and sec_var) else "disabled")
            self.durdur_btn.set_state("disabled")
            self.sec_btn.set_state("normal")
            self.kaldir_btn.set_state("normal")
        self.excel_btn.set_state(
            "normal" if (var and not self._calisiyor and not self._excel_calisiyor) else "disabled")
        self.tebligat_btn.set_state(
            "normal" if (var and not self._calisiyor and not self._tebligat_calisiyor) else "disabled")

    # ─────────────────────────────────────────────────────── ödenenleri Excel'e aktar
    def _excel_aktar(self):
        """UYAP'ın 'Tamamlanmayan Dosyalar' ekranını sorgulayıp (yalnız okuma —
        hiçbir ödeme/gönderim yapılmaz), o anda gerçek esas no almış (yani UYAP'ta
        ödemesi tamamlanmış) takipleri Ad Soyad / Ürün No / Dosya No sütunlarıyla
        Excel'e yazar. MTS akışında harç ödemesi otomatik olmadığından ödeme UYAP'ta
        elle yapılmalı; bu buton yalnızca SONUCU raporlar."""
        if self._calisiyor or self._excel_calisiyor:
            return
        if not self.takipler:
            messagebox.showinfo("Excel'e Aktar", "Önce bir XML/Excel seçin.")
            return

        self._excel_calisiyor = True
        self._butonlar_guncelle()
        self._durum_yaz("Ödeme durumu UYAP'tan sorgulanıyor…")
        self._log_yaz("\n📊 Ödenenleri Excel'e Aktar: UYAP 'Tamamlanmayan Dosyalar' "
                      "listesi sorgulanıyor…")
        takipler = list(self.takipler)

        def isi():
            try:
                _uyap_core_ekle()
                from uyap_core.mts.models import takipler_to_params
                params = {"takipler": takipler_to_params(takipler)}
                job = is_kuyrugu.is_baslat("mts_odenmis_dosyalari_bul", params)
                self.app.after(0, lambda: self._excel_is_basladi(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._excel_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _excel_hata(self, e):
        self._excel_calisiyor = False
        self._butonlar_guncelle()
        self._durum_yaz("")
        self._log_yaz(f"❌ Excel'e aktarma başlatılamadı: {e}")
        messagebox.showerror("Excel'e Aktar", str(e))

    def _excel_is_basladi(self, job):
        self._excel_job_id = job.get("id")
        self._excel_poll()

    def _excel_poll(self):
        if not self._excel_job_id:
            return
        jid = self._excel_job_id

        def isi():
            try:
                job = is_kuyrugu.is_durum(jid)
                self.app.after(0, lambda: self._excel_poll_isle(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._excel_poll_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _excel_poll_hata(self, e):
        self._log_yaz(f"⚠️ Excel sorgusu — durum alınamadı: {e}")
        if self._excel_calisiyor:
            self.app.after(2000, self._excel_poll)

    def _excel_poll_isle(self, job):
        if job.get("status") in ("done", "error", "cancelled"):
            self._excel_bitti(job)
            return
        if self._excel_calisiyor:
            self.app.after(900, self._excel_poll)

    def _excel_bitti(self, job):
        self._excel_calisiyor = False
        self._excel_job_id = None
        self._butonlar_guncelle()
        self._durum_yaz("")

        if job.get("status") != "done":
            self._log_yaz(f"❌ Excel sorgusu başarısız: {job.get('error') or 'bilinmeyen hata'}")
            messagebox.showerror("Excel'e Aktar", job.get("error") or "Sorgu başarısız.")
            return

        sonuc = job.get("result") or {}
        satirlar = []
        for s in (sonuc.get("sonuclar") or []):
            gercek_no = s.get("gercek_dosya_no")
            if not gercek_no:
                continue
            t = self._takip_bul(s.get("dosya_no"))
            if t is None:
                continue
            urun_no = t.abone_no or t.hizmet_abone_no or "-"
            satirlar.append((self._borclu_metni(t), urun_no, gercek_no))

        self._log_yaz(f"📊 {len(satirlar)}/{len(self.takipler)} takip ödenmiş bulundu "
                      "(gerçek esas no atanmış).")
        if not satirlar:
            messagebox.showinfo(
                "Excel'e Aktar",
                "Ödemesi tamamlanmış (gerçek esas no atanmış) takip bulunamadı.\n\n"
                "MTS'te harç ödemesi otomatik yapılmıyor — UYAP'ta 'Tamamlanmayan "
                "Dosyalar' ekranından elle ödedikten sonra tekrar deneyin.")
            return

        yol = filedialog.asksaveasfilename(
            title="Excel'i kaydet", defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")], initialfile="mts_odenen_takipler.xlsx")
        if not yol:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Ödenen Takipler"
            ws.append(["Ad Soyad", "Ürün Numarası", "Dosya Numarası"])
            for satir in satirlar:
                ws.append(list(satir))
            for col, genislik in zip("ABC", (32, 20, 18)):
                ws.column_dimensions[col].width = genislik
            wb.save(yol)
        except Exception as e:
            self._log_yaz(f"❌ Excel yazılamadı: {e}")
            messagebox.showerror("Excel'e Aktar", str(e))
            return
        self._log_yaz(f"✓ Excel kaydedildi: {yol}")
        messagebox.showinfo("Excel'e Aktar", f"{len(satirlar)} kayıt yazıldı:\n{yol}")

    # ─────────────────────────────────────────────────────── tebligat gönder (test/kurtarma)
    def _tebligat_gonder_test(self):
        """Sağdaki 'Açılan' listesinden seçili TEK dosyaya, mts_tebligat_gonder işiyle
        (job_handlers.py) tebligat gönderir. TEBLIGAT_GONDER_AKTIF bayrağını etkilemez —
        yalnız izole test/kurtarma amaçlıdır. Dosyanın harcının UYAP'ta zaten ÖDENMİŞ
        olması gerekir; job dosya_id'yi borçlu ad-soyadıyla kendisi bulur."""
        if self._calisiyor or self._excel_calisiyor or self._tebligat_calisiyor:
            return
        dn = self.secili_dosya_no
        t = self._takip_bul(dn) if dn else None
        if t is None:
            messagebox.showinfo("Tebligat Gönder (Test)",
                                "Önce sağdaki 'Açılan' listesinden, harcı UYAP'ta zaten "
                                "ödenmiş bir dosya seçin.")
            return
        borclu = self._borclu_metni(t)
        if not messagebox.askyesno(
                "Tebligat Gönder (Test)",
                f"'{borclu}' borçlusuna ait dosyaya GERÇEK tebligat gönderilecek ve "
                "e-Barobirlik karttan GERÇEK ücret (~317 TL) tahsil edilecek.\n\n"
                "Bu dosyanın harcının UYAP'ta ÖDENMİŞ olduğundan emin misiniz?\n\n"
                "Devam edilsin mi?"):
            return

        self._tebligat_calisiyor = True
        self._tebligat_log_sayac = 0
        self._butonlar_guncelle()
        self._log_yaz(f"\n📨 Tebligat Gönder (Test): [{dn}] {borclu} için deneniyor...")

        def isi():
            try:
                _uyap_core_ekle()
                from uyap_core.mts.models import takipler_to_params
                params = {"takip": takipler_to_params([t])[0]}
                job = is_kuyrugu.is_baslat("mts_tebligat_gonder", params)
                self.app.after(0, lambda: self._tebligat_is_basladi(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._tebligat_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _tebligat_hata(self, e):
        self._tebligat_calisiyor = False
        self._butonlar_guncelle()
        self._log_yaz(f"❌ Tebligat işi başlatılamadı: {e}")
        messagebox.showerror("Tebligat Gönder (Test)", str(e))

    def _tebligat_is_basladi(self, job):
        self._tebligat_job_id = job.get("id")
        self._log_yaz(f"   job id: {self._tebligat_job_id}")
        self._tebligat_poll()

    def _tebligat_poll(self):
        if not self._tebligat_job_id:
            return
        jid = self._tebligat_job_id

        def isi():
            try:
                job = is_kuyrugu.is_durum(jid)
                self.app.after(0, lambda: self._tebligat_poll_isle(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._tebligat_poll_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _tebligat_poll_hata(self, e):
        self._log_yaz(f"⚠️ Tebligat işi — durum alınamadı: {e}")
        if self._tebligat_calisiyor:
            self.app.after(2000, self._tebligat_poll)

    def _tebligat_poll_isle(self, job):
        for satir in (job.get("logs") or [])[self._tebligat_log_sayac:]:
            self._log_yaz(f"   {satir.get('line', satir)}")
        self._tebligat_log_sayac = len(job.get("logs") or [])
        if job.get("status") in ("done", "error", "cancelled"):
            self._tebligat_bitti(job)
            return
        if self._tebligat_calisiyor:
            self.app.after(900, self._tebligat_poll)

    def _tebligat_bitti(self, job):
        self._tebligat_calisiyor = False
        self._tebligat_job_id = None
        self._butonlar_guncelle()
        if job.get("status") != "done":
            self._log_yaz(f"❌ Tebligat gönderimi başarısız: {job.get('error') or 'bilinmeyen hata'}")
            messagebox.showerror("Tebligat Gönder (Test)", job.get("error") or "İş başarısız.")
            return
        sonuc = job.get("result") or {}
        self._log_yaz(f"✓ Tebligat sonucu: {sonuc}")
        messagebox.showinfo("Tebligat Gönder (Test)", f"Tamamlandı:\n\n{sonuc}")

    # ─────────────────────────────────────────────────────── belge → base64
    def _belge_b64(self, yol):
        with open(yol, "rb") as f:
            return {"filename": os.path.basename(yol),
                    "b64": base64.b64encode(f.read()).decode("ascii")}

    # ─────────────────────────────────────────────────────── başlat
    def _baslat(self):
        if self._calisiyor:
            return
        if not self.takipler:
            messagebox.showwarning("Eksik veri", "Önce bir XML/Excel seçin.")
            return
        secili_takipler = [t for t in self.takipler if str(t.dosya_no) in self.secili
                           and self.durum.get(str(t.dosya_no)) in ("bekleyen", None)]
        if not secili_takipler:
            messagebox.showwarning("Takip seçilmedi",
                                   "Açılacak takip işaretlenmedi. Listeden ☑ ile seçin "
                                   "veya 'Hepsi' butonunu kullanın.")
            return

        # Vekaleti olmayan alacaklılar için uyar (yine de devam edilebilir)
        vekaletsiz = sorted({t.alacakli for t in secili_takipler
                             if not self.vekalet_map.get(t.alacakli)})
        if vekaletsiz:
            if not messagebox.askyesno(
                    "Vekalet eksik",
                    "Şu alacaklılar için vekalet seçilmedi:\n\n" + ", ".join(vekaletsiz) +
                    "\n\nBu takiplerde vekaletname adımı atlanacak. Devam edilsin mi?"):
                return

        il = self._il_var.get().strip() or "İzmir"
        adliye = self._adliye_var.get().strip() or "İzmir"
        onay_modu = self._mod_var.get()
        odeme_onay_modu = self._odeme_mod_var.get()
        tebligat_onay_modu = self._tebligat_mod_var.get()
        # Ödeme/tebligat HER DOSYA için ayrı: Bekleyen listesindeki 💳/📨 kutularıyla
        # işaretlenmiş dosya_no'ların haritası (bkz. __init__ notu) — bu koşuda işlenecek
        # takiplerle sınırlanır (henüz açılmayacak bir dosyanın işareti burada anlamsız).
        odeme_yap = {str(t.dosya_no): str(t.dosya_no) in self.odeme_secili for t in secili_takipler}
        tebligat_gonder = {str(t.dosya_no): str(t.dosya_no) in self.tebligat_secili
                           for t in secili_takipler}

        # Belgeleri base64'le (vekalet alacaklıya, dayanak dosya_no'ya göre)
        try:
            vekalet = {}
            for t in secili_takipler:
                yol = self.vekalet_map.get(t.alacakli)
                if yol and t.alacakli not in vekalet:
                    vekalet[t.alacakli] = self._belge_b64(yol)
            dayanak = {}
            for t in secili_takipler:
                yol = self.dayanak_map.get(str(t.dosya_no))
                if yol:
                    dayanak[str(t.dosya_no)] = self._belge_b64(yol)
        except Exception as e:
            messagebox.showerror("Belge okunamadı", str(e))
            return

        _uyap_core_ekle()
        from uyap_core.mts.models import takipler_to_params
        params = {
            "takipler": takipler_to_params(secili_takipler),
            "il": il, "adliye": adliye, "onay_modu": onay_modu,
            "odeme_yap": odeme_yap, "odeme_onay_modu": odeme_onay_modu,
            "tebligat_gonder": tebligat_gonder, "tebligat_onay_modu": tebligat_onay_modu,
            "vekalet": vekalet, "dayanak": dayanak,
        }

        self._calisiyor = True
        self._log_sayac = 0
        mod_ad = dict((d, b) for d, b, _ in MODLAR).get(onay_modu, onay_modu)
        self._durum_yaz("İş gönderiliyor…")
        self._log_yaz(f"\n=== MTS ÇOKLU TAKİP AÇMA — {mod_ad} ({len(secili_takipler)} takip) ===")
        self._butonlar_guncelle()
        self.ilerleme.configure(maximum=len(secili_takipler), value=0)

        def isi():
            try:
                job = is_kuyrugu.is_baslat("coklu_takip_ac", params)
                self.app.after(0, lambda: self._is_basladi(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._is_baslatilamadi(e))
        threading.Thread(target=isi, daemon=True).start()

    def _is_baslatilamadi(self, e):
        self._calisiyor = False
        self._durum_yaz("Başlatılamadı.")
        self._log_yaz(f"❌ İş başlatılamadı: {e}")
        messagebox.showerror("Başlatılamadı", str(e))
        self._butonlar_guncelle()

    def _is_basladi(self, job):
        self.job_id = job.get("id")
        self._durum_yaz("Çalışıyor…")
        self._log_yaz(f"İş kuyruğuna alındı (id={self.job_id}).")
        self._poll()

    # ─────────────────────────────────────────────────────── poll
    def _poll(self):
        if not self.job_id:
            return

        def isi():
            try:
                job = is_kuyrugu.is_durum(self.job_id)
                self.app.after(0, lambda: self._poll_isle(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._poll_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _poll_hata(self, e):
        self._log_yaz(f"⚠️ Durum alınamadı: {e}")
        # bağlantı geçici kopmuş olabilir; bir kez daha dene
        if self._calisiyor:
            self._poll_after = self.app.after(2000, self._poll)

    def _poll_isle(self, job):
        # Yeni log satırları
        loglar = job.get("logs") or []
        for ln in loglar[self._log_sayac:]:
            self._log_yaz(ln.get("line", ""))
        self._log_sayac = len(loglar)

        # İlerleme
        prog = job.get("progress") or {}
        if prog.get("total"):
            self.ilerleme.configure(maximum=prog["total"], value=prog.get("done", 0))
        if prog.get("message"):
            self._durum_yaz(prog["message"])

        # Artımlı sonuçlar (varsa) durum haritasına işle
        sonuc = job.get("result") or {}
        for s in (sonuc.get("sonuclar") or []):
            dn = str(s.get("dosya_no"))
            d = {"tamam": "tamam", "hata": "hata", "atlandı": "atlandi",
                 "atlandi": "atlandi"}.get(s.get("durum"), None)
            if d:
                self.durum[dn] = d
                if s.get("mesaj"):
                    self.hata_mesaj[dn] = s["mesaj"]

        status = job.get("status")

        # Onay isteği
        if status == "awaiting_approval" and not self._onay_aktif:
            self._onay_goster(job.get("pending_approval") or {})

        self._bekleyen_ciz()
        self._acilan_ciz()

        if status in ("done", "error", "cancelled"):
            self._is_bitti(job)
            return
        if self._calisiyor:
            self._poll_after = self.app.after(900, self._poll)

    def _is_bitti(self, job):
        self._calisiyor = False
        self.job_id = None
        self._onay_gizle()
        status = job.get("status")
        sonuc = job.get("result") or {}
        self._son_sonuc = sonuc
        if status == "done":
            self._durum_yaz(
                f"Bitti: {sonuc.get('basari', 0)} tamam, {sonuc.get('atlanan', 0)} atlandı, "
                f"{sonuc.get('hata', 0)} hata.")
            self._log_yaz("✓ İş tamamlandı.")
        elif status == "cancelled":
            self._durum_yaz("Durduruldu.")
            self._log_yaz("⏹ İş durduruldu.")
        else:
            self._durum_yaz("Hata.")
            self._log_yaz(f"❌ İş hatası: {job.get('error') or 'bilinmeyen'}")
        self._butonlar_guncelle()

    # ─────────────────────────────────────────────────────── durdur
    def _durdur(self):
        if not self.job_id:
            return
        self._durum_yaz("Durduruluyor…")
        jid = self.job_id

        def isi():
            try:
                is_kuyrugu.is_iptal(jid)
            except Exception:
                pass
        threading.Thread(target=isi, daemon=True).start()

    # ─────────────────────────────────────────────────────── onay çubuğu
    def _onay_goster(self, pending):
        self._onay_aktif = True
        mod = pending.get("mod")
        for w in self.onay_btn_cer.winfo_children():
            w.destroy()
        if mod == "tek_tek":
            ozet = pending.get("takip") or {}
            self.onay_mesaj.config(text=self._ozet_metni(ozet) +
                                   "\nBu takip açılsın mı?")
            self._onay_btn("✓ Onayla", lambda: self._onay_ver({"decision": "approve"}), "primary")
            self._onay_btn("⤼ Atla", lambda: self._onay_ver({"decision": "skip"}), "ghost")
            self._onay_btn("⏹ Durdur", lambda: self._onay_ver({"decision": "cancel"}), "stop")
        elif mod == "toplu":
            takipler = pending.get("takipler") or []
            # Toplu onayda seçim = bekleyen listede ☑ işaretli dosya_no'lar.
            self.onay_mesaj.config(
                text=f"{len(takipler)} takip hazırlandı. Bekleyen listede ☑ işaretlediklerinizi "
                     "açmak için Onayla'ya basın.")
            self._onay_btn("✓ Seçilenleri Aç",
                           lambda: self._onay_ver({"selection": list(self.secili)}), "primary")
            self._onay_btn("⏹ Durdur", lambda: self._onay_ver({"decision": "cancel"}), "stop")
        elif mod == "odeme_tek_tek":
            kalem = pending.get("kalem") or {}
            self.onay_mesaj.config(text=self._harc_metni(kalem) + "\nBu dosyanın harcı ödensin mi?")
            self._onay_btn("✓ Öde", lambda: self._onay_ver({"decision": "approve"}), "primary")
            self._onay_btn("⤼ Bu Dosyayı Atla", lambda: self._onay_ver({"decision": "skip"}), "ghost")
            self._onay_btn("⏹ Kalanları Durdur", lambda: self._onay_ver({"decision": "cancel"}), "stop")
        elif mod == "odeme_toplu":
            kalemler = pending.get("kalemler") or []
            toplam = sum((k.get("toplam_harc") or 0) for k in kalemler)
            self.onay_mesaj.config(
                text=f"{len(kalemler)} dosya ödeme onayı bekliyor (toplam ~{toplam:.2f} TL). "
                     "Aşağıdan seçin.")
            self._onay_btn("✓ Detaylı Seç…",
                           lambda: self._toplu_kalem_dialog("Ödeme Onayı", kalemler, self._harc_satiri),
                           "primary")
            self._onay_btn("⏭ Hiçbirini Ödeme", lambda: self._onay_ver({"selection": []}), "ghost")
            self._onay_btn("⏹ Kalanları Durdur", lambda: self._onay_ver({"decision": "cancel"}), "stop")
        elif mod == "tebligat_tek_tek":
            kalem = pending.get("kalem") or {}
            self.onay_mesaj.config(text=self._tebligat_metni(kalem) + "\nBu dosyaya tebligat gönderilsin mi?")
            self._onay_btn("✓ Gönder", lambda: self._onay_ver({"decision": "approve"}), "primary")
            self._onay_btn("⤼ Bu Dosyayı Atla", lambda: self._onay_ver({"decision": "skip"}), "ghost")
            self._onay_btn("⏹ Kalanları Durdur", lambda: self._onay_ver({"decision": "cancel"}), "stop")
        elif mod == "tebligat_toplu":
            kalemler = pending.get("kalemler") or []
            self.onay_mesaj.config(
                text=f"{len(kalemler)} dosya tebligat onayı bekliyor. Aşağıdan seçin.")
            self._onay_btn("✓ Detaylı Seç…",
                           lambda: self._toplu_kalem_dialog("Tebligat Onayı", kalemler, self._tebligat_satiri),
                           "primary")
            self._onay_btn("⏭ Hiçbirine Gönderme", lambda: self._onay_ver({"selection": []}), "ghost")
            self._onay_btn("⏹ Kalanları Durdur", lambda: self._onay_ver({"decision": "cancel"}), "stop")
        else:
            # Bilinmeyen mod: körlemesine onaylamak yerine işi GÜVENLİ tarafta durdur —
            # tanınmayan bir onay turunu sessizce approve etmek (ör. para harcayan bir
            # aşamada) kullanıcının hiç görmediği bir işlemi onaylamış olur.
            self._log_yaz(f"⚠️ Bilinmeyen onay türü ({mod}) — güvenlik için iş durduruluyor.")
            self._onay_ver({"decision": "cancel"})
            return
        try:
            self.onay_bar.pack(fill="x", pady=(14, 0))
        except Exception:
            pass

    def _ozet_metni(self, ozet):
        borc = ", ".join(f"{b.get('ad','')} {b.get('soyad','')}".strip()
                         for b in (ozet.get("borclular") or [])) or "-"
        harc = "; ".join(f"{h.get('ad')}: {h.get('miktar')}" for h in (ozet.get("harclar") or []))
        return (f"Dosya {ozet.get('dosya_no')} · {ozet.get('alacakli','')}\n"
                f"Borçlu: {borc}  ·  Toplam: {ozet.get('toplam','-')} TL"
                + (f"\nMasraf: {harc}" if harc else ""))

    def _harc_metni(self, kalem):
        harc = "; ".join(f"{h.get('ad')}: {h.get('miktar')} TL" for h in (kalem.get("harclar") or []))
        return (f"Dosya {kalem.get('dosya_no')} · {kalem.get('alacakli','')}\n"
                f"Harç: {harc or '-'}  ·  Toplam: {kalem.get('toplam_harc', 0)} TL")

    def _harc_satiri(self, kalem):
        return (f"Dosya {kalem.get('dosya_no')} · {kalem.get('alacakli','')} · "
                f"Harç toplamı {kalem.get('toplam_harc', 0)} TL")

    def _tebligat_metni(self, kalem):
        borc = ", ".join(f"{b.get('ad','')} {b.get('soyad','')}".strip()
                         for b in (kalem.get("borclular") or [])) or "-"
        return f"Dosya {kalem.get('dosya_no')} · {kalem.get('alacakli','')}\nTaraflar: {borc}"

    def _tebligat_satiri(self, kalem):
        return (f"Dosya {kalem.get('dosya_no')} · {kalem.get('alacakli','')} · "
                f"{len(kalem.get('borclular') or [])} taraf")

    def _toplu_kalem_dialog(self, baslik, kalemler, satir_fn):
        """Ödeme/tebligat toplu onayı: her kalem için ayrı ☑ ile seçim yapılan küçük pencere.
        Reddedilen kalem yalnız BU AŞAMAYI atlar — bir önceki aşamada zaten gerçekleşmiş
        (dosya açık / ödenmiş) hiçbir şey geri alınmaz."""
        win = tk.Toplevel(self.parent)
        win.title(baslik)
        win.configure(bg=C.BG)
        win.geometry("520x480")
        win.transient(self.parent)
        tk.Label(win, text=f"{baslik} — {len(kalemler)} dosya", bg=C.BG, fg=C.INK,
                font=self.app.f_nav_b).pack(anchor="w", padx=14, pady=(14, 6))

        govde = tk.Frame(win, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        govde.pack(fill="both", expand=True, padx=14)
        canvas = tk.Canvas(govde, bg=C.CARD, highlightthickness=0)
        sb = ttk.Scrollbar(govde, orient="vertical", command=canvas.yview)
        ic = tk.Frame(canvas, bg=C.CARD)
        ic.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=ic, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        secimler = {}
        for k in kalemler:
            dn = str(k.get("dosya_no"))
            var = tk.BooleanVar(value=True)
            secimler[dn] = var
            tk.Checkbutton(ic, text=satir_fn(k), variable=var, bg=C.CARD, fg=C.INK,
                          selectcolor=C.CARD, activebackground=C.CARD, activeforeground=C.INK,
                          font=self.app.f_body, bd=0, highlightthickness=0,
                          cursor="hand2", anchor="w", justify="left").pack(anchor="w", fill="x", pady=1)

        alt = tk.Frame(win, bg=C.BG)
        alt.pack(fill="x", padx=14, pady=10)

        def gonder():
            secili = [dn for dn, v in secimler.items() if v.get()]
            win.destroy()
            self._onay_ver({"selection": secili})

        RoundButton(alt, "✓ Seçilenlerle Devam Et", command=gonder, kind="primary",
                   font=self.app.f_nav_b, height=34).pack(side="right")
        RoundButton(alt, "Vazgeç (tümü işaretli kalsın)", command=win.destroy, kind="ghost",
                   font=self.app.f_nav_b, height=34).pack(side="right", padx=(0, 8))

    def _onay_btn(self, metin, komut, kind):
        b = RoundButton(self.onay_btn_cer, metin, command=komut, kind=kind,
                        font=self.app.f_small, height=30)
        b.pack(side="left", padx=4)
        return b

    def _onay_ver(self, karar):
        self._onay_gizle()
        jid = self.job_id
        if not jid:
            return
        self._durum_yaz("Onay gönderiliyor…")

        def isi():
            try:
                is_kuyrugu.is_onayla(jid, karar)
            except Exception as e:
                self.app.after(0, lambda e=e: self._log_yaz(f"⚠️ Onay gönderilemedi: {e}"))
        threading.Thread(target=isi, daemon=True).start()

    def _onay_gizle(self):
        self._onay_aktif = False
        try:
            self.onay_bar.pack_forget()
        except Exception:
            pass
