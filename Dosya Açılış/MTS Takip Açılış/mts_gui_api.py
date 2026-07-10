# -*- coding: utf-8 -*-
"""
MTS Takip Açan — Modern Kontrol Paneli
======================================

Tasarım:
  - Solda koyu bir kenar çubuğu: mod seçimi, il/adliye, başlat/duraklat/durdur,
    ilerleme ve durum.
  - Sağda üç sütun: Alacaklılar & Vekaletler · Bekleyen Takipler · Açılan Takipler.
  - Altta geniş detay paneli (borçlular + alacak kalemleri) ve canlı günlük.

Modlar:
  - Otomatik     : tüm takipleri sırayla, kesintisiz açar.
  - Kontrol      : her takipten ÖNCE bilgileri gösterir, kullanıcı onaylarsa açar.
  - Tam Kontrol  : takip açılışının her aşamasında ayrı ayrı onay (Evet/Hayır) ister.

Çalıştırma:  python mts_gui.py
"""

import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import mts_takip_acan_api as mts


# ----------------------------------------------------------------------
#  Renk paleti — modern, düz (flat) tema
# ----------------------------------------------------------------------
RENK = {
    "arka":        "#0b1220",   # içerik arka planı (sol tarafla bütünleşik koyu)
    "kart":        "#1e293b",   # kart yüzeyi (slate-800)
    "kart2":       "#172033",   # başlık/şerit zemini
    "girdi":       "#0f172a",   # tablo / listbox / giriş zemini
    "kenar":       "#334155",   # kenarlık (slate-700)
    "yazi":        "#e2e8f0",   # birincil yazı (açık)
    "soluk":       "#94a3b8",   # ikincil yazı
    "soluk2":      "#64748b",

    "yan":         "#0f172a",   # kenar çubuğu (slate-900)
    "yan2":        "#1e293b",   # kenar çubuğu kart (slate-800)
    "yan_yazi":    "#e2e8f0",
    "yan_soluk":   "#94a3b8",

    "ana":         "#6366f1",   # indigo
    "ana_koyu":    "#4f46e5",
    "ana_acik":    "#a5b4fc",   # koyu zeminde açık indigo yazı

    "yesil":       "#10b981",
    "yesil_koyu":  "#34d399",   # koyu zeminde okunaklı yeşil
    "yesil_acik":  "#064e3b",

    "turuncu":     "#f59e0b",
    "turuncu_koyu":"#fbbf24",   # koyu zeminde okunaklı turuncu
    "sari_acik":   "#3f3b1e",   # koyu zeminde aktif satır vurgusu

    "kirmizi":     "#ef4444",
    "kirmizi_koyu":"#f87171",   # koyu zeminde okunaklı kırmızı
    "kirmizi_acik":"#7f1d1d",

    "log_arka":    "#060b14",
    "log_yazi":    "#cbd5e1",
}

VEKALET_TIPLERI = [
    ("Vekalet / İmzalı evrak", "*.udf *.pdf *.xml"),
    ("UDF", "*.udf"),
    ("PDF", "*.pdf"),
    ("Tüm dosyalar", "*.*"),
]

MODLAR = [
    ("otomatik", "⚡  Otomatik",
     "Tüm takipleri sırayla, kesintisiz açar."),
    ("kontrol", "👁  Kontrol",
     "Her takipten önce bilgileri gösterir; onaylarsanız açar."),
    ("tam", "🔒  Tam Kontrol",
     "Açılışın her aşamasında ayrı onay ister."),
]


# ----------------------------------------------------------------------
#  stdout → arayüz günlüğü (hem konsola hem log kutusuna yazar)
# ----------------------------------------------------------------------
class StdoutYonlendirici:
    def __init__(self, kuyruk, gercek_stdout):
        self._kuyruk = kuyruk
        self._gercek = gercek_stdout

    def write(self, metin):
        try:
            if self._gercek:
                self._gercek.write(metin)
        except Exception:
            pass
        if metin:
            self._kuyruk.put(metin)

    def flush(self):
        try:
            if self._gercek:
                self._gercek.flush()
        except Exception:
            pass


class MtsGuiApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MTS XML Takip Açılışı (API Hızlı Mod)")
        self.minsize(1120, 740)
        self.configure(bg=RENK["arka"])

        # --- Durum ---
        self.kaynak_yolu = None
        self.takipler = []                 # mts.Takip listesi
        self.alacaklilar = []              # benzersiz alacaklı adları (sıralı)
        self.vekalet_map = {}              # {alacaklı: dosya_yolu}
        self.dayanak_map = {}              # {dosya_no: pdf_yolu} — otomatik PDF eşleşmesi

        self.bekleyen = []                 # bekleyen takip index'leri (sıralı)
        self.acilan = []                   # açılan takip index'leri (sıralı)
        self.takip_durum = {}              # idx -> bekleyen/aktif/tamam/hata/atlandi
        self.hata_mesajlari = {}           # idx -> hatalı dosyanın neden mesajı (guide)
        self.secili_takipler = set()       # checkbox ile açılması seçilen takip index'leri
        self.aktif_index = None
        self.secili_index = None

        self._bekleyen_pos = []            # listbox pozisyonu -> takip index
        self._acilan_pos = []
        self._alacakli_pos = []            # treeview iid -> alacaklı adı

        self.mod_var = tk.StringVar(value="otomatik")
        self.kontrol = None                # mts.KontrolDurumu (çalışırken)
        self.worker = None
        self._calisiyor = False

        # --- Onay (Kontrol/Tam Kontrol) altyapısı: pencere içi, bloklamasız ---
        self._onay_olay = threading.Event()
        self._onay_sonuc = None
        self._onay_bekliyor = False

        # --- Manuel çözüm bekleme (genel hata durumunda 'Sorunu Çözdüm' butonu) ---
        self._manuel_beklemede = False

        # --- Log altyapısı ---
        self._log_kuyrugu = queue.Queue()
        self._gercek_stdout = sys.stdout
        sys.stdout = StdoutYonlendirici(self._log_kuyrugu, self._gercek_stdout)

        self._stilleri_kur()
        self._arayuzu_kur()
        self._butonlari_guncelle()
        self._detay_goster(None)

        self.after(100, self._log_kuyrugunu_isle)
        self.protocol("WM_DELETE_WINDOW", self._kapat)
        self.after(0, self._one_getir)

    # ------------------------------------------------------------------
    def _one_getir(self):
        self.update_idletasks()
        g, y = 1240, 820
        ekran_g = self.winfo_screenwidth()
        ekran_y = self.winfo_screenheight()
        x = max(0, (ekran_g - g) // 2)
        ny = max(0, (ekran_y - y) // 2 - 20)
        self.geometry(f"{g}x{y}+{x}+{ny}")
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(500, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ------------------------------------------------------------------
    #  Stil
    # ------------------------------------------------------------------
    def _stilleri_kur(self):
        stil = ttk.Style(self)
        try:
            stil.theme_use("clam")
        except Exception:
            pass

        stil.configure("TFrame", background=RENK["arka"])
        stil.configure("Kart.TFrame", background=RENK["kart"])
        stil.configure("Yan.TFrame", background=RENK["yan"])
        stil.configure("Yan2.TFrame", background=RENK["yan2"])

        stil.configure("TLabel", background=RENK["arka"],
                       foreground=RENK["yazi"], font=("Segoe UI", 10))
        stil.configure("Kart.TLabel", background=RENK["kart"],
                       foreground=RENK["yazi"], font=("Segoe UI", 10))
        stil.configure("KartBaslik.TLabel", background=RENK["kart"],
                       foreground=RENK["yazi"], font=("Segoe UI Semibold", 11))
        stil.configure("Soluk.TLabel", background=RENK["kart"],
                       foreground=RENK["soluk"], font=("Segoe UI", 9))
        stil.configure("Deger.TLabel", background=RENK["kart"],
                       foreground=RENK["yazi"], font=("Segoe UI Semibold", 10))

        # Kenar çubuğu etiketleri
        stil.configure("YanBaslik.TLabel", background=RENK["yan"],
                       foreground="#ffffff", font=("Segoe UI Semibold", 15))
        stil.configure("Yan.TLabel", background=RENK["yan"],
                       foreground=RENK["yan_soluk"], font=("Segoe UI Semibold", 9))
        stil.configure("Yan2.TLabel", background=RENK["yan2"],
                       foreground=RENK["yan_yazi"], font=("Segoe UI", 10))

        stil.configure("TEntry", fieldbackground=RENK["girdi"], padding=5,
                       foreground=RENK["yazi"], bordercolor=RENK["kenar"])

        stil.configure("Treeview", background=RENK["girdi"],
                       fieldbackground=RENK["girdi"],
                       foreground=RENK["yazi"], rowheight=26, font=("Segoe UI", 10),
                       borderwidth=0, relief="flat")
        stil.configure("Treeview.Heading", font=("Segoe UI Semibold", 9),
                       background=RENK["kart2"], foreground=RENK["soluk"],
                       relief="flat", borderwidth=0, padding=(6, 5))
        stil.map("Treeview", background=[("selected", RENK["ana"])],
                 foreground=[("selected", "#ffffff")])
        stil.map("Treeview.Heading",
                 background=[("active", RENK["kenar"])],
                 foreground=[("active", RENK["yazi"])])

        # --- Modern scrollbar (sağ taraftaki gri ttk scrollbar'ları giderir) ---
        for yon, to, tn in (("Vertical", "Vertical.Scrollbar.trough",
                              "Vertical.Scrollbar.thumb"),
                             ("Horizontal", "Horizontal.Scrollbar.trough",
                              "Horizontal.Scrollbar.thumb")):
            try:
                stil.layout(f"{yon}.TScrollbar", [
                    (to, {"sticky": "nswe",
                          "children": [(tn, {"expand": "1", "sticky": "nswe"})]})])
            except Exception:
                pass
        stil.configure("Vertical.TScrollbar", gripcount=0, borderwidth=0,
                       relief="flat", troughcolor=RENK["kart"],
                       background=RENK["kenar"], darkcolor=RENK["kart"],
                       lightcolor=RENK["kart"], bordercolor=RENK["kart"],
                       arrowcolor=RENK["kart"])
        stil.configure("Horizontal.TScrollbar", gripcount=0, borderwidth=0,
                       relief="flat", troughcolor=RENK["kart"],
                       background=RENK["kenar"], darkcolor=RENK["kart"],
                       lightcolor=RENK["kart"], bordercolor=RENK["kart"],
                       arrowcolor=RENK["kart"])
        stil.map("Vertical.TScrollbar",
                 background=[("active", RENK["soluk2"]),
                             ("!active", RENK["kenar"])])
        stil.map("Horizontal.TScrollbar",
                 background=[("active", RENK["soluk2"]),
                             ("!active", RENK["kenar"])])

        stil.configure("Ana.Horizontal.TProgressbar",
                       troughcolor=RENK["yan2"], background=RENK["yesil"],
                       thickness=10, borderwidth=0)

    def _tk_buton(self, parent, metin, komut, ana_renk, koyu_renk, fg="white",
                  genislik=14, kucuk=False, bg_parent=None):
        b = tk.Button(parent, bg=ana_renk, fg=fg, activebackground=koyu_renk,
                      activeforeground=fg, relief="flat", bd=0,
                      font=("Segoe UI Semibold", 9 if kucuk else 10),
                      width=genislik, cursor="hand2",
                      highlightthickness=0,
                      padx=8 if kucuk else 10, pady=5 if kucuk else 9)

        def _normalle():
            # Modal pencere (filedialog/messagebox) sonrası <Leave> gelmediğinde
            # buton 'koyu' renge takılı kalmasın diye komut bitince rengi sıfırla.
            if str(b["state"]) != "disabled":
                b.configure(bg=b._ana_renk)

        def _sarili():
            try:
                if komut:
                    komut()
            finally:
                b.after(30, _normalle)

        b.configure(text=metin, command=_sarili)

        def _gir(_e):
            if str(b["state"]) != "disabled":
                b.configure(bg=koyu_renk)

        def _cik(_e):
            if str(b["state"]) != "disabled":
                b.configure(bg=ana_renk)

        b.bind("<Enter>", _gir)
        b.bind("<Leave>", _cik)
        b._ana_renk = ana_renk
        b._fg = fg
        return b

    def _kart(self, parent, dolgu=14):
        f = tk.Frame(parent, bg=RENK["kart"], highlightbackground=RENK["kenar"],
                     highlightthickness=1, bd=0)
        ic = tk.Frame(f, bg=RENK["kart"])
        ic.pack(fill="both", expand=True, padx=dolgu, pady=dolgu)
        f.ic = ic
        return f

    # ------------------------------------------------------------------
    #  Arayüz iskeleti
    # ------------------------------------------------------------------
    def _arayuzu_kur(self):
        govde = tk.Frame(self, bg=RENK["arka"])
        govde.pack(fill="both", expand=True)

        self._kenar_cubugu_kur(govde)

        # İçerik bölgesi
        icerik = tk.Frame(govde, bg=RENK["arka"])
        icerik.pack(side="left", fill="both", expand=True, padx=16, pady=16)

        self._kaynak_barini_kur(icerik)

        # Onay çubuğu (Kontrol / Tam Kontrol modunda pencere içinde belirir)
        self._onay_bar_kur(icerik)

        # Üç sütun: alacaklılar | bekleyen | açılan
        sutunlar = tk.Frame(icerik, bg=RENK["arka"])
        sutunlar.pack(fill="x", pady=(12, 0))
        sutunlar.columnconfigure(0, weight=3, uniform="s")
        sutunlar.columnconfigure(1, weight=4, uniform="s")
        sutunlar.columnconfigure(2, weight=4, uniform="s")
        self.sutunlar_cer = sutunlar
        self._alacakli_kart_kur(sutunlar)
        self._bekleyen_kart_kur(sutunlar)
        self._acilan_kart_kur(sutunlar)

        # Detay
        self._detay_kart_kur(icerik)

        # Günlük
        self._log_kart_kur(icerik)

    # ------------------------------------------------------------------
    #  Kenar çubuğu (sol, koyu)
    # ------------------------------------------------------------------
    def _kenar_cubugu_kur(self, parent):
        yan = tk.Frame(parent, bg=RENK["yan"], width=288)
        yan.pack(side="left", fill="y")
        yan.pack_propagate(False)

        pad = 22

        # Logo + başlık
        bas = tk.Frame(yan, bg=RENK["yan"])
        bas.pack(fill="x", padx=pad, pady=(24, 6))
        rozet = tk.Label(bas, text="MTS", bg=RENK["ana"], fg="white",
                         font=("Segoe UI Semibold", 13), padx=10, pady=4)
        rozet.pack(side="left")
        tk.Label(bas, text="  Takip Açan", bg=RENK["yan"], fg="white",
                 font=("Segoe UI Semibold", 15)).pack(side="left")
        tk.Label(yan, text="UYAP otomasyon paneli", bg=RENK["yan"],
                 fg=RENK["yan_soluk"], font=("Segoe UI", 9)).pack(
            anchor="w", padx=pad)

        # --- MOD ---
        tk.Label(yan, text="ÇALIŞMA MODU", bg=RENK["yan"], fg=RENK["yan_soluk"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=pad,
                                                     pady=(24, 8))
        self._mod_kartlari = {}
        for deger, baslik, aciklama in MODLAR:
            self._mod_karti_ekle(yan, pad, deger, baslik, aciklama)

        # --- İl / Adliye ---
        tk.Label(yan, text="İL  /  ADLİYE", bg=RENK["yan"], fg=RENK["yan_soluk"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=pad,
                                                     pady=(22, 8))
        ia = tk.Frame(yan, bg=RENK["yan"])
        ia.pack(fill="x", padx=pad)
        self.il_var = tk.StringVar(value="İzmir")
        self.adliye_var = tk.StringVar(value="İzmir")
        for etiket, var in (("İl", self.il_var), ("Adliye", self.adliye_var)):
            satir = tk.Frame(ia, bg=RENK["yan"])
            satir.pack(fill="x", pady=3)
            tk.Label(satir, text=etiket, bg=RENK["yan"], fg=RENK["yan_soluk"],
                     font=("Segoe UI", 9), width=6, anchor="w").pack(side="left")
            e = tk.Entry(satir, textvariable=var, bg=RENK["yan2"],
                         fg="white", insertbackground="white", relief="flat",
                         font=("Segoe UI", 10), highlightthickness=1,
                         highlightbackground="#334155",
                         highlightcolor=RENK["ana"])
            e.pack(side="left", fill="x", expand=True, ipady=4)

        # --- Eylemler ---
        eylem = tk.Frame(yan, bg=RENK["yan"])
        eylem.pack(fill="x", padx=pad, pady=(26, 4))
        self.baslat_btn = self._tk_buton(eylem, "▶   Başlat", self._baslat,
                                         RENK["yesil"], RENK["yesil_koyu"],
                                         genislik=22)
        self.baslat_btn.pack(fill="x", pady=(0, 8))
        alt = tk.Frame(eylem, bg=RENK["yan"])
        alt.pack(fill="x")
        self.duraklat_btn = self._tk_buton(alt, "⏸  Duraklat",
                                           self._duraklat_devam, RENK["turuncu"],
                                           RENK["turuncu_koyu"], genislik=10)
        self.duraklat_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.durdur_btn = self._tk_buton(alt, "⏹  Durdur", self._durdur,
                                         RENK["kirmizi"], RENK["kirmizi_koyu"],
                                         genislik=10)
        self.durdur_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # --- Sorunu Çözdüm butonu (otomasyon takılınca belirir) ---
        self._cozum_cerceve = tk.Frame(eylem, bg=RENK["yan"])
        self._cozum_cerceve.pack(fill="x", pady=(8, 0))
        self.cozum_btn = self._tk_buton(
            self._cozum_cerceve, "✅  Sorunu Çözdüm, Devam Et",
            lambda: self._manuel_cozuldu("devam"),
            "#16a34a", "#15803d", genislik=22)
        self.cozum_btn.pack(fill="x", pady=(0, 4))
        self.atla_cozum_btn = self._tk_buton(
            self._cozum_cerceve, "↼  Bu Dosyayı Atla",
            lambda: self._manuel_cozuldu("atla"),
            RENK["turuncu"], RENK["turuncu_koyu"], genislik=22)
        self.atla_cozum_btn.pack(fill="x")
        self._cozum_cerceve.pack_forget()   # başlangıçta gizli

        # --- Dayanak PDF tarama ---
        tk.Label(yan, text="DAYANAK BELGELER", bg=RENK["yan"], fg=RENK["yan_soluk"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=pad,
                                                     pady=(20, 6))
        self.dayanak_btn = self._tk_buton(
            yan, "📁  PDF Klasörü Tara", self._dayanak_klasoru_sec,
            RENK["kart2"], RENK["kenar"], fg=RENK["yazi"], genislik=22)
        self.dayanak_btn.pack(fill="x", padx=pad)
        self.dayanak_etiketi = tk.Label(
            yan, text="Klasör seçilmedi.", bg=RENK["yan"],
            fg=RENK["yan_soluk"], font=("Segoe UI", 8),
            wraplength=240, justify="left")
        self.dayanak_etiketi.pack(anchor="w", padx=pad, pady=(4, 0))

        # --- İlerleme + durum (alta yasla) ---
        alt_blok = tk.Frame(yan, bg=RENK["yan"])
        alt_blok.pack(side="bottom", fill="x", padx=pad, pady=(0, 22))
        self.ilerleme_etiketi = tk.Label(alt_blok, text="0 / 0",
                                         bg=RENK["yan"], fg="white",
                                         font=("Segoe UI Semibold", 11))
        self.ilerleme_etiketi.pack(anchor="w", pady=(0, 4))
        self.ilerleme = ttk.Progressbar(alt_blok, mode="determinate",
                                        style="Ana.Horizontal.TProgressbar")
        self.ilerleme.pack(fill="x")
        self.durum_etiketi = tk.Label(alt_blok, text="●  Hazır",
                                      bg=RENK["yan"], fg=RENK["yan_soluk"],
                                      font=("Segoe UI Semibold", 10))
        self.durum_etiketi.pack(anchor="w", pady=(12, 0))

    def _mod_karti_ekle(self, yan, pad, deger, baslik, aciklama):
        kart = tk.Frame(yan, bg=RENK["yan2"], highlightthickness=2,
                        highlightbackground=RENK["yan2"], cursor="hand2")
        kart.pack(fill="x", padx=pad, pady=4)
        ust = tk.Frame(kart, bg=RENK["yan2"])
        ust.pack(fill="x", padx=12, pady=(8, 0))
        rb = tk.Radiobutton(ust, variable=self.mod_var, value=deger,
                            bg=RENK["yan2"], activebackground=RENK["yan2"],
                            selectcolor=RENK["yan2"], fg="white",
                            highlightthickness=0, bd=0, takefocus=0,
                            command=self._mod_degisti)
        rb.pack(side="left")
        tk.Label(ust, text=baslik, bg=RENK["yan2"], fg="white",
                 font=("Segoe UI Semibold", 10)).pack(side="left", padx=(2, 0))
        ac = tk.Label(kart, text=aciklama, bg=RENK["yan2"], fg=RENK["yan_soluk"],
                      font=("Segoe UI", 8), justify="left", wraplength=210)
        ac.pack(anchor="w", padx=12, pady=(0, 8))

        def _sec(_e=None):
            self.mod_var.set(deger)
            self._mod_degisti()
        for w in (kart, ust, ac):
            w.bind("<Button-1>", _sec)
        self._mod_kartlari[deger] = kart

    def _mod_degisti(self):
        secili = self.mod_var.get()
        for deger, kart in self._mod_kartlari.items():
            if deger == secili:
                kart.configure(highlightbackground=RENK["ana"], bg=RENK["yan2"])
            else:
                kart.configure(highlightbackground=RENK["yan2"])

    # ------------------------------------------------------------------
    #  Kaynak (XML/Excel) barı
    # ------------------------------------------------------------------
    def _kaynak_barini_kur(self, parent):
        kart = self._kart(parent, dolgu=12)
        kart.pack(fill="x")
        ic = kart.ic
        self.sec_btn = self._tk_buton(ic, "📂  Dosya Seç / Değiştir",
                                      self._kaynak_sec, RENK["ana"],
                                      RENK["ana_koyu"], genislik=20)
        self.sec_btn.pack(side="left")
        self.kaldir_btn = self._tk_buton(ic, "✕  XML Kaldır",
                                         self._kaynak_kaldir, RENK["kirmizi"],
                                         RENK["kirmizi_koyu"], genislik=13)
        self.kaldir_btn.pack(side="left", padx=(8, 0))
        self.kaynak_etiketi = tk.Label(ic, text="Henüz XML / Excel seçilmedi.",
                                       bg=RENK["kart"], fg=RENK["soluk"],
                                       font=("Segoe UI", 10))
        self.kaynak_etiketi.pack(side="left", padx=14)
        self.ozet_etiketi = tk.Label(ic, text="", bg=RENK["kart"],
                                     fg=RENK["ana_acik"],
                                     font=("Segoe UI Semibold", 10))
        self.ozet_etiketi.pack(side="right")

    # ------------------------------------------------------------------
    #  Onay çubuğu (Kontrol / Tam Kontrol modu) — pencere içi, bloklamasız
    # ------------------------------------------------------------------
    def _onay_bar_kur(self, parent):
        self.onay_bar = tk.Frame(parent, bg=RENK["ana"], highlightthickness=0)
        ic = tk.Frame(self.onay_bar, bg=RENK["ana"])
        ic.pack(fill="x", padx=14, pady=10)
        self.onay_mesaj = tk.Label(ic, text="", bg=RENK["ana"], fg="white",
                                   font=("Segoe UI Semibold", 11),
                                   justify="left", anchor="w")
        self.onay_mesaj.pack(side="left", fill="x", expand=True)
        self.onay_btn_cer = tk.Frame(ic, bg=RENK["ana"])
        self.onay_btn_cer.pack(side="right")
        self.onay_parent = parent
        # Başlangıçta gizli

    def _onay_bar_goster(self, mesaj, secenekler):
        self.onay_mesaj.configure(text=mesaj)
        for w in self.onay_btn_cer.winfo_children():
            w.destroy()
        for etiket, deger, ana, koyu in secenekler:
            b = self._tk_buton(self.onay_btn_cer, etiket,
                               lambda d=deger: self._onay_ver(d), ana, koyu,
                               genislik=16, kucuk=True)
            b.pack(side="left", padx=4)
        try:
            self.onay_bar.pack(fill="x", pady=(12, 0), before=self.sutunlar_cer)
        except Exception:
            self.onay_bar.pack(fill="x", pady=(12, 0))

    def _onay_bar_gizle(self):
        self.onay_bar.pack_forget()

    def _onay_ver(self, deger):
        self._onay_sonuc = deger
        self._onay_bekliyor = False
        self._onay_olay.set()

    def _onay_iste(self, mesaj, secenekler):
        """Worker thread'inden çağrılır. Pencere içi onay çubuğunu gösterir ve
        kullanıcı bir düğmeye basana (ya da Durdur'a) kadar BLOKLAMADAN bekler.
        Ana döngü serbest kaldığı için Duraklat/Durdur çalışmaya devam eder.

        secenekler: [(etiket, deger, ana_renk, koyu_renk), ...]
        Dönüş: seçilen deger.  Durdurulduysa mts.TakipDurduruldu fırlatır."""
        if self.kontrol and self.kontrol.durduruldu_mu:
            raise mts.TakipDurduruldu()
        self._onay_sonuc = None
        self._onay_bekliyor = True
        self._onay_olay.clear()
        self.after(0, lambda: self._onay_bar_goster(mesaj, secenekler))
        self._onay_olay.wait()
        self.after(0, self._onay_bar_gizle)
        if self._onay_sonuc == "_DURDUR_" or (self.kontrol and
                                              self.kontrol.durduruldu_mu):
            raise mts.TakipDurduruldu()
        return self._onay_sonuc

    # ------------------------------------------------------------------
    #  Alacaklılar & Vekaletler kartı
    # ------------------------------------------------------------------
    def _alacakli_kart_kur(self, parent):
        kart = self._kart(parent, dolgu=12)
        kart.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ic = kart.ic
        tk.Label(ic, text="🧾  Alacaklılar & Vekaletler", bg=RENK["kart"],
                 fg=RENK["yazi"], font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(ic, text="Her alacaklıya ayrı vekalet bağlayın.",
                 bg=RENK["kart"], fg=RENK["soluk"],
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))

        cer = tk.Frame(ic, bg=RENK["kenar"], bd=0, highlightthickness=1,
                       highlightbackground=RENK["kenar"])
        cer.pack(fill="both", expand=True)
        self.alacakli_tablo = ttk.Treeview(
            cer, columns=("alacakli", "sayi", "vekalet", "dayanak"), show="headings",
            height=7, selectmode="browse")
        self.alacakli_tablo.heading("alacakli", text="Alacaklı")
        self.alacakli_tablo.heading("sayi", text="Takip")
        self.alacakli_tablo.heading("vekalet", text="Vekalet")
        self.alacakli_tablo.heading("dayanak", text="Dayanak PDF")
        self.alacakli_tablo.column("alacakli", width=130, anchor="w")
        self.alacakli_tablo.column("sayi", width=40, anchor="center")
        self.alacakli_tablo.column("vekalet", width=110, anchor="w")
        self.alacakli_tablo.column("dayanak", width=110, anchor="w")
        self.alacakli_tablo.tag_configure("var", foreground=RENK["yesil_koyu"])
        self.alacakli_tablo.tag_configure("yok", foreground=RENK["soluk"])
        kaydir = ttk.Scrollbar(cer, command=self.alacakli_tablo.yview)
        self.alacakli_tablo.configure(yscrollcommand=kaydir.set)
        kaydir.pack(side="right", fill="y")
        self.alacakli_tablo.pack(side="left", fill="both", expand=True)
        self.alacakli_tablo.bind("<Double-1>", lambda e: self._vekalet_ata_secili())

        btn_satir = tk.Frame(ic, bg=RENK["kart"])
        btn_satir.pack(fill="x", pady=(8, 0))
        self.vekalet_btn = self._tk_buton(
            btn_satir, "📎  Vekalet Ata", self._vekalet_ata_secili,
            RENK["ana"], RENK["ana_koyu"], genislik=16, kucuk=True)
        self.vekalet_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.dayanak_onizle_btn = self._tk_buton(
            btn_satir, "🔍  Dayanak Onayla", self._dayanak_onizle_penceresi,
            RENK["turuncu"], RENK["turuncu_koyu"], genislik=16, kucuk=True)
        self.dayanak_onizle_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ------------------------------------------------------------------
    #  Bekleyen / Açılan listbox kartları
    # ------------------------------------------------------------------
    def _liste_kutusu(self, parent, secbg):
        ic = tk.Frame(parent, bg=RENK["kenar"], highlightthickness=1,
                      highlightbackground=RENK["kenar"], bd=0)
        ic.pack(fill="both", expand=True, pady=(6, 0))
        lb = tk.Listbox(ic, bg=RENK["girdi"], fg=RENK["yazi"],
                        selectbackground=secbg, selectforeground="#ffffff",
                        relief="flat", bd=0, highlightthickness=0,
                        activestyle="none", font=("Segoe UI", 10),
                        height=7)
        kaydir = ttk.Scrollbar(ic, command=lb.yview)
        lb.configure(yscrollcommand=kaydir.set)
        kaydir.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        return lb

    def _bekleyen_kart_kur(self, parent):
        kart = self._kart(parent, dolgu=12)
        kart.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        ic = kart.ic
        self.bekleyen_baslik = tk.Label(ic, text="⏳  Bekleyen Takipler (0)",
                                        bg=RENK["kart"], fg=RENK["yazi"],
                                        font=("Segoe UI Semibold", 11))
        self.bekleyen_baslik.pack(anchor="w")
        tk.Label(ic, text="☑ işaretli takipler açılır. Kutuya tıklayarak seçin.",
                 bg=RENK["kart"], fg=RENK["soluk"],
                 font=("Segoe UI", 8)).pack(anchor="w")

        # Hepsini seç / kaldır
        sec_satir = tk.Frame(ic, bg=RENK["kart"])
        sec_satir.pack(fill="x", pady=(4, 0))
        self.hepsi_sec_btn = self._tk_buton(
            sec_satir, "☑  Hepsini Seç", self._hepsini_sec,
            RENK["kart2"], RENK["kenar"], fg=RENK["yazi"], genislik=14, kucuk=True)
        self.hepsi_sec_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.hepsi_kaldir_btn = self._tk_buton(
            sec_satir, "☐  Hepsini Kaldır", self._hepsini_kaldir,
            RENK["kart2"], RENK["kenar"], fg=RENK["yazi"], genislik=14, kucuk=True)
        self.hepsi_kaldir_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        cer = tk.Frame(ic, bg=RENK["kenar"], highlightthickness=1,
                       highlightbackground=RENK["kenar"], bd=0)
        cer.pack(fill="both", expand=True, pady=(6, 0))
        self.bekleyen_tv = ttk.Treeview(
            cer, columns=("sec", "ad"), show="headings", height=7,
            selectmode="browse")
        self.bekleyen_tv.heading("sec", text="✓")
        self.bekleyen_tv.heading("ad", text="Takip")
        self.bekleyen_tv.column("sec", width=34, anchor="center", stretch=False)
        self.bekleyen_tv.column("ad", width=240, anchor="w")
        self.bekleyen_tv.tag_configure("aktif", foreground=RENK["turuncu_koyu"])
        self.bekleyen_tv.tag_configure("atlandi", foreground=RENK["soluk2"])
        self.bekleyen_tv.tag_configure("normal", foreground=RENK["yazi"])
        kaydir = ttk.Scrollbar(cer, command=self.bekleyen_tv.yview)
        self.bekleyen_tv.configure(yscrollcommand=kaydir.set)
        kaydir.pack(side="right", fill="y")
        self.bekleyen_tv.pack(side="left", fill="both", expand=True)
        self.bekleyen_tv.bind("<Button-1>", self._bekleyen_tiklandi)
        self.bekleyen_tv.bind("<<TreeviewSelect>>", self._bekleyen_secildi)

    def _acilan_kart_kur(self, parent):
        kart = self._kart(parent, dolgu=12)
        kart.grid(row=0, column=2, sticky="nsew")
        ic = kart.ic
        self.acilan_baslik = tk.Label(ic, text="✓  Açılan Takipler (0)",
                                      bg=RENK["kart"], fg=RENK["yazi"],
                                      font=("Segoe UI Semibold", 11))
        self.acilan_baslik.pack(anchor="w")
        tk.Label(ic, text="Açıldıkça buraya geçer.", bg=RENK["kart"],
                 fg=RENK["soluk"], font=("Segoe UI", 8)).pack(anchor="w")
        self.acilan_lb = self._liste_kutusu(ic, RENK["yesil"])
        self.acilan_lb.bind("<<ListboxSelect>>", self._acilan_secildi)

        # Hatalı (turuncu) bir dosya seçilince beliren 'Tekrar Dene' butonu.
        self.tekrar_btn = self._tk_buton(
            ic, "🔄  Tekrar Dene", self._acilan_tekrar_dene,
            RENK["turuncu"], RENK["turuncu_koyu"], genislik=20, kucuk=True)
        self.tekrar_btn.pack(fill="x", pady=(6, 0))
        self._buton_durum(self.tekrar_btn, False)   # başlangıçta pasif

    # ------------------------------------------------------------------
    #  Detay paneli (geniş)
    # ------------------------------------------------------------------
    def _detay_kart_kur(self, parent):
        kart = self._kart(parent, dolgu=14)
        kart.pack(fill="both", expand=True, pady=(12, 0))
        ic = kart.ic

        bas = tk.Frame(ic, bg=RENK["kart"])
        bas.pack(fill="x")
        self.detay_baslik = tk.Label(bas, text="Takip Ayrıntısı", bg=RENK["kart"],
                                     fg=RENK["yazi"],
                                     font=("Segoe UI Semibold", 12))
        self.detay_baslik.pack(side="left")
        self.detay_rozet = tk.Label(bas, text="", bg=RENK["kart"],
                                    fg=RENK["soluk"],
                                    font=("Segoe UI Semibold", 10))
        self.detay_rozet.pack(side="right")

        self.bilgi_etiketi = tk.Label(ic, text="Bir takip seçin.", bg=RENK["kart"],
                                      fg=RENK["yazi"], font=("Segoe UI", 10),
                                      justify="left", anchor="w",
                                      wraplength=1180)
        self.bilgi_etiketi.pack(fill="x", pady=(8, 8))

        tablolar = tk.Frame(ic, bg=RENK["kart"])
        tablolar.pack(fill="both", expand=True)
        tablolar.columnconfigure(0, weight=1, uniform="t")
        tablolar.columnconfigure(1, weight=1, uniform="t")
        tablolar.rowconfigure(1, weight=1)

        tk.Label(tablolar, text="Borçlular", bg=RENK["kart"], fg=RENK["soluk"],
                 font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w",
                                                      padx=(0, 6))
        tk.Label(tablolar, text="Alacak Kalemleri", bg=RENK["kart"],
                 fg=RENK["soluk"], font=("Segoe UI Semibold", 10)).grid(
            row=0, column=1, sticky="w", padx=(6, 0))

        b_cer = tk.Frame(tablolar, bg=RENK["kenar"], highlightthickness=1,
                         highlightbackground=RENK["kenar"])
        b_cer.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(4, 0))
        self.borclu_tablo = ttk.Treeview(
            b_cer, columns=("ad", "soyad", "kimlik"), show="headings", height=5)
        for s, baslik, gen, hiza in (("ad", "Ad", 130, "w"),
                                     ("soyad", "Soyad", 130, "w"),
                                     ("kimlik", "Kimlik / Vergi No", 150, "w")):
            self.borclu_tablo.heading(s, text=baslik)
            self.borclu_tablo.column(s, width=gen, anchor=hiza)
        b_kaydir = ttk.Scrollbar(b_cer, command=self.borclu_tablo.yview)
        self.borclu_tablo.configure(yscrollcommand=b_kaydir.set)
        b_kaydir.pack(side="right", fill="y")
        self.borclu_tablo.pack(side="left", fill="both", expand=True)

        k_cer = tk.Frame(tablolar, bg=RENK["kenar"], highlightthickness=1,
                         highlightbackground=RENK["kenar"])
        k_cer.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(4, 0))
        self.kalem_tablo = ttk.Treeview(
            k_cer, columns=("ad", "tutar", "oran", "tur"), show="headings",
            height=5)
        for s, baslik, gen, hiza in (("ad", "Alacak Adı", 180, "w"),
                                     ("tutar", "Tutar", 90, "e"),
                                     ("oran", "Faiz Oranı", 90, "e"),
                                     ("tur", "Faiz Türü", 120, "w")):
            self.kalem_tablo.heading(s, text=baslik)
            self.kalem_tablo.column(s, width=gen, anchor=hiza)
        k_kaydir = ttk.Scrollbar(k_cer, command=self.kalem_tablo.yview)
        self.kalem_tablo.configure(yscrollcommand=k_kaydir.set)
        k_kaydir.pack(side="right", fill="y")
        self.kalem_tablo.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------------
    #  Günlük kartı
    # ------------------------------------------------------------------
    def _log_kart_kur(self, parent):
        kart = self._kart(parent, dolgu=12)
        kart.pack(fill="x", pady=(12, 0))
        ic = kart.ic
        bas = tk.Frame(ic, bg=RENK["kart"])
        bas.pack(fill="x")
        tk.Label(bas, text="📜  Günlük", bg=RENK["kart"], fg=RENK["yazi"],
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        self._tk_buton(bas, "Temizle", self._log_temizle, RENK["soluk"],
                       "#475569", genislik=10, kucuk=True).pack(side="right")

        cer = tk.Frame(ic, bg=RENK["kenar"], highlightthickness=1,
                       highlightbackground=RENK["kenar"])
        cer.pack(fill="both", expand=True, pady=(8, 0))
        self.log_kutusu = tk.Text(cer, height=6, bg=RENK["log_arka"],
                                  fg=RENK["log_yazi"], insertbackground="white",
                                  relief="flat", wrap="word", state="disabled",
                                  font=("Consolas", 9), padx=10, pady=8,
                                  highlightthickness=0)
        kaydir = ttk.Scrollbar(cer, command=self.log_kutusu.yview)
        self.log_kutusu.configure(yscrollcommand=kaydir.set)
        kaydir.pack(side="right", fill="y")
        self.log_kutusu.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------------
    #  Yardımcı: takibin borçlu adlarını metne çevir
    # ------------------------------------------------------------------
    def _borclu_metni(self, takip, kisa=False):
        adlar = []
        for b in takip.borclular:
            ad = f"{b.ad} {b.soyad}".strip() or (b.kimlik or "?")
            adlar.append(ad)
        if not adlar:
            return "(borçlu yok)"
        if kisa and len(adlar) > 2:
            return f"{adlar[0]}, {adlar[1]} +{len(adlar) - 2}"
        return ", ".join(adlar)

    # ------------------------------------------------------------------
    #  Listbox etiket biçimleme + çizim
    # ------------------------------------------------------------------
    def _etiket(self, idx, acilan=False):
        t = self.takipler[idx]
        borclular = self._borclu_metni(t, kisa=True)
        if acilan:
            durum = self.takip_durum.get(idx)
            isaret = "✓" if durum == "tamam" else ("✗" if durum == "hata" else "–")
            return f"  {isaret}  {borclular}  ·  Dosya {t.dosya_no}"
        durum = self.takip_durum.get(idx)
        nokta = "▶" if durum == "aktif" else "•"
        vekalet = "📎" if self.vekalet_map.get(t.alacakli) else "  "
        return f" {nokta} {vekalet} {borclular}  ·  Dosya {t.dosya_no}"

    def _listeleri_ciz(self):
        # Bekleyen (onay kutulu tablo)
        for satir in self.bekleyen_tv.get_children():
            self.bekleyen_tv.delete(satir)
        self._bekleyen_pos = list(self.bekleyen)
        for idx in self._bekleyen_pos:
            durum = self.takip_durum.get(idx)
            isaret = "☑" if idx in self.secili_takipler else "☐"
            tag = "aktif" if durum == "aktif" else (
                "atlandi" if durum == "atlandi" else "normal")
            self.bekleyen_tv.insert("", "end", iid=str(idx),
                                    values=(isaret, self._etiket(idx)),
                                    tags=(tag,))
        # Açılan
        self.acilan_lb.delete(0, "end")
        self._acilan_pos = list(self.acilan)
        hata_sayisi = 0
        for pos, idx in enumerate(self._acilan_pos):
            self.acilan_lb.insert("end", self._etiket(idx, acilan=True))
            if self.takip_durum.get(idx) == "hata":
                hata_sayisi += 1
                self.acilan_lb.itemconfig(pos, fg=RENK["turuncu_koyu"])
            else:
                self.acilan_lb.itemconfig(pos, fg=RENK["yesil_koyu"])

        secili_sayi = sum(1 for i in self.bekleyen if i in self.secili_takipler)
        self.bekleyen_baslik.configure(
            text=f"⏳  Bekleyen Takipler ({len(self.bekleyen)})  —  ☑ {secili_sayi} seçili")
        if hata_sayisi:
            self.acilan_baslik.configure(
                text=f"✓  Açılan Takipler ({len(self.acilan)})  —  ⚠ {hata_sayisi} hatalı")
        else:
            self.acilan_baslik.configure(
                text=f"✓  Açılan Takipler ({len(self.acilan)})")

        # Aktif takibi bekleyen tabloda seçili göster
        if self.aktif_index is not None and self.aktif_index in self._bekleyen_pos:
            iid = str(self.aktif_index)
            self.bekleyen_tv.selection_set(iid)
            self.bekleyen_tv.see(iid)

    # ------------------------------------------------------------------
    #  Alacaklı tablosu çizimi
    # ------------------------------------------------------------------
    def _alacakli_tablosu_ciz(self):
        for satir in self.alacakli_tablo.get_children():
            self.alacakli_tablo.delete(satir)
        self._alacakli_pos = []
        sayim = {}
        for t in self.takipler:
            sayim[t.alacakli] = sayim.get(t.alacakli, 0) + 1
        for alacakli in self.alacaklilar:
            yol = self.vekalet_map.get(alacakli)
            if yol:
                vk_metin = "✓ " + os.path.basename(yol)
                etiket = "var"
            else:
                vk_metin = "— seçilmedi"
                etiket = "yok"
            # Dayanak: bu alacaklıya ait kaç takipte dayanak eşleşmesi var?
            dayanak_sayi = sum(
                1 for t in self.takipler
                if t.alacakli == alacakli and t.dosya_no in self.dayanak_map)
            takip_sayi = sayim.get(alacakli, 0)
            if dayanak_sayi > 0:
                dayanak_metin = f"✓ {dayanak_sayi}/{takip_sayi} eşleşti"
            else:
                dayanak_metin = "— eşleşme yok"
            self.alacakli_tablo.insert(
                "", "end",
                values=(alacakli, takip_sayi, vk_metin, dayanak_metin),
                tags=(etiket,))
            self._alacakli_pos.append(alacakli)

    # ------------------------------------------------------------------
    #  Liste seçim olayları
    # ------------------------------------------------------------------
    def _bekleyen_secildi(self, _e=None):
        sec = self.bekleyen_tv.selection()
        if sec:
            self.secili_index = int(sec[0])
            self._detay_goster(self.secili_index)

    def _bekleyen_tiklandi(self, event):
        """Onay kutusu (✓) sütununa tıklanınca takibin seçimini değiştirir."""
        if self._calisiyor:        # otomasyon sürerken seçim kilitli
            return
        row = self.bekleyen_tv.identify_row(event.y)
        if not row:
            return
        sutun = self.bekleyen_tv.identify_column(event.x)
        if sutun != "#1":          # yalnızca '✓' sütunu toggle eder
            return
        idx = int(row)
        if idx in self.secili_takipler:
            self.secili_takipler.discard(idx)
        else:
            self.secili_takipler.add(idx)
        self._listeleri_ciz()
        self._butonlari_guncelle()
        return "break"             # satır seçimi/detay tetiklenmesin

    def _hepsini_sec(self):
        self.secili_takipler = set(self.bekleyen)
        self._listeleri_ciz()
        self._butonlari_guncelle()

    def _hepsini_kaldir(self):
        self.secili_takipler.clear()
        self._listeleri_ciz()
        self._butonlari_guncelle()

    def _acilan_secildi(self, _e=None):
        sec = self.acilan_lb.curselection()
        if sec and sec[0] < len(self._acilan_pos):
            self.secili_index = self._acilan_pos[sec[0]]
            self._detay_goster(self.secili_index)
        self._tekrar_butonu_guncelle()

    def _tekrar_butonu_guncelle(self):
        """'Tekrar Dene' yalnızca boştayken ve hatalı bir dosya seçiliyken etkin."""
        sec = self.acilan_lb.curselection()
        etkin = (not self._calisiyor and bool(sec)
                 and sec[0] < len(self._acilan_pos)
                 and self.takip_durum.get(self._acilan_pos[sec[0]]) == "hata")
        self._buton_durum(self.tekrar_btn, etkin)

    def _acilan_tekrar_dene(self):
        """Seçili hatalı dosyayı bekleyen kuyruğa geri alıp tek başına yeniden çalıştırır."""
        if self._calisiyor:
            messagebox.showinfo(
                "Çalışıyor",
                "Otomasyon çalışırken tekrar denenemez. Önce bitmesini bekleyin.")
            return
        sec = self.acilan_lb.curselection()
        if not sec or sec[0] >= len(self._acilan_pos):
            return
        idx = self._acilan_pos[sec[0]]
        if self.takip_durum.get(idx) != "hata":
            messagebox.showinfo(
                "Tekrar Dene",
                "Yalnızca hatalı (turuncu) dosyalar yeniden denenebilir.")
            return
        # Dosyayı açılanlardan çıkar, bekleyen kuyruğa al ve SADECE onu seç.
        if idx in self.acilan:
            self.acilan.remove(idx)
        self.takip_durum.pop(idx, None)          # 'bekliyor' durumuna döner
        self.hata_mesajlari.pop(idx, None)       # eski hata nedenini temizle
        if idx not in self.bekleyen:
            self.bekleyen.append(idx)
        self.secili_takipler = {idx}             # yalnızca bu dosya çalışsın
        self._listeleri_ciz()
        self._ilerleme_guncelle()
        self._log(f"\n🔄 Dosya {self.takipler[idx].dosya_no} yeniden deneniyor...\n")
        self._baslat()

    # ------------------------------------------------------------------
    #  Detay gösterimi
    # ------------------------------------------------------------------
    def _detay_goster(self, idx):
        for satir in self.borclu_tablo.get_children():
            self.borclu_tablo.delete(satir)
        for satir in self.kalem_tablo.get_children():
            self.kalem_tablo.delete(satir)

        if idx is None or idx >= len(self.takipler):
            self.detay_baslik.configure(text="Takip Ayrıntısı")
            self.detay_rozet.configure(text="")
            self.bilgi_etiketi.configure(text="Bir takip seçin.")
            return

        t = self.takipler[idx]
        durum_metni = {"bekleyen": ("⏳ Bekliyor", RENK["soluk"]),
                       "aktif": ("▶ Açılıyor", RENK["turuncu_koyu"]),
                       "tamam": ("✓ Açıldı", RENK["yesil_koyu"]),
                       "hata": ("✗ Hata", RENK["kirmizi_koyu"]),
                       "atlandi": ("⤼ Atlandı", RENK["soluk"])}.get(
            self.takip_durum.get(idx, "bekleyen"), ("", RENK["soluk"]))
        self.detay_baslik.configure(text=f"Takip Ayrıntısı — Dosya {t.dosya_no}")
        self.detay_rozet.configure(text=durum_metni[0], fg=durum_metni[1])

        vk = self.vekalet_map.get(t.alacakli)
        vk_metin = ("📎 " + os.path.basename(vk)) if vk else "📎 vekalet seçilmedi"
        bilgi = (f"Borçlu(lar):  {self._borclu_metni(t)}\n"
                 f"Alacaklı:  {t.alacakli}        Vekalet:  {vk_metin}\n"
                 f"IBAN:  {t.iban or '-'}        Abone No:  {t.abone_no or '-'}        "
                 f"İlamsız Tutar:  {t.ilamsiz_tutar or '-'}\n"
                 f"Genel Tarih:  {t.fatura_tarihi or '-'}        "
                 f"Ödeme Tarihi:  {t.odeme_tarihi or '-'}\n"
                 f"Talep Açıklaması:  {t.aciklama or '-'}")
        # Hatalı dosyada nedenini (guide) göster
        if self.takip_durum.get(idx) == "hata" and self.hata_mesajlari.get(idx):
            bilgi += f"\n\n⚠ Hata nedeni:  {self.hata_mesajlari[idx]}"
        self.bilgi_etiketi.configure(text=bilgi)

        for b in t.borclular:
            self.borclu_tablo.insert("", "end", values=(b.ad, b.soyad, b.kimlik))
        for k in t.alacak_kalemleri:
            self.kalem_tablo.insert("", "end",
                                    values=(k.ad, k.tutar, k.faiz_oran, k.faiz_tur))

    def _dayanak_klasoru_sec(self):
        """PDF klasörü seçer, tarar ve dayanak_map'i günceller."""
        if self._calisiyor:
            messagebox.showwarning("Çalışıyor",
                                   "Otomasyon sürerken dayanak taranlamaz.")
            return
        if not self.takipler:
            messagebox.showinfo("Dayanak",
                                "Önce bir XML / Excel seçin.")
            return
        klasor = filedialog.askdirectory(title="Dayanak PDF'lerinin bulunduğu klasörü seçin")
        if not klasor:
            return
        self._buton_durum(self.dayanak_btn, False)
        self._log(f"\nPDF tarama başlıyor: {klasor}\n")
        self.dayanak_etiketi.configure(text="Taranıyor...")
        threading.Thread(
            target=self._dayanak_tara_worker,
            args=(klasor,), daemon=True).start()

    def _dayanak_tara_worker(self, klasor):
        try:
            sonuc = mts.pdf_dayanak_tara(klasor, self.takipler)
        except Exception as e:
            import traceback
            self.after(0, lambda e=e: (
                self._log(f"Dayanak tarama hatası: {e}\n"),
                self.dayanak_etiketi.configure(text="Hata!"),
                self._butonlari_guncelle()
            ))
            return
        self.after(0, lambda s=sonuc, k=klasor: self._dayanak_tara_tamam(s, k))

    def _dayanak_tara_tamam(self, sonuc, klasor):
        self.dayanak_map = sonuc
        eslesme = len(sonuc)
        toplam = len(self.takipler)
        self.dayanak_etiketi.configure(
            text=f"{eslesme}/{toplam} eşleşme • {os.path.basename(klasor)}")
        self._log(f"{eslesme}/{toplam} takip için dayanak PDF eşleştirildi.\n")
        self._alacakli_tablosu_ciz()
        self._butonlari_guncelle()

    def _dayanak_onizle_penceresi(self):
        """Eşleşen dayanak PDF'leri listeler; kullanıcı her birini açıp
        onaylayabilir ya da eşleşmeyi reddedebilir."""
        if self._calisiyor:
            messagebox.showwarning("Çalışıyor",
                                   "Otomasyon sürerken dayanak değiştirilemez.")
            return
        if not self.dayanak_map:
            messagebox.showinfo("Dayanak Onayla",
                                "Henüz eşleşmiş dayanak PDF yok.\n"
                                "Önce 'PDF Klasörü Tara' butonunu kullanın.")
            return

        pen = tk.Toplevel(self)
        pen.title("Dayanak PDF Onayla / Reddet")
        pen.configure(bg=RENK["arka"])
        pen.resizable(True, True)
        pen.minsize(700, 400)
        pen.grab_set()

        # Başlık
        tk.Label(pen, text="Eşleşen Dayanak Belgeler",
                 bg=RENK["arka"], fg="white",
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=18, pady=(14, 4))
        tk.Label(pen,
                 text="Her satır için PDF'i açıp kontrol edin, ardından Onayla veya Reddet seçin.",
                 bg=RENK["arka"], fg=RENK["soluk"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 10))

        # Liste + scrollbar
        cer = tk.Frame(pen, bg=RENK["arka"])
        cer.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        yer = ttk.Treeview(
            cer,
            columns=("dosya", "borclu", "pdf", "durum"),
            show="headings", height=12, selectmode="browse")
        yer.heading("dosya", text="Dosya No")
        yer.heading("borclu", text="Borçlu")
        yer.heading("pdf", text="Eşleşen PDF")
        yer.heading("durum", text="Durum")
        yer.column("dosya", width=70, anchor="center")
        yer.column("borclu", width=180, anchor="w")
        yer.column("pdf", width=280, anchor="w")
        yer.column("durum", width=100, anchor="center")
        yer.tag_configure("onaylandi", foreground=RENK["yesil_koyu"])
        yer.tag_configure("reddedildi", foreground=RENK["kirmizi_koyu"])
        yer.tag_configure("bekliyor", foreground=RENK["turuncu_koyu"])
        kb = ttk.Scrollbar(cer, command=yer.yview)
        yer.configure(yscrollcommand=kb.set)
        kb.pack(side="right", fill="y")
        yer.pack(side="left", fill="both", expand=True)

        # Durum takibi: {dosya_no: True/False/None}
        durumlar = {}
        for dosya_no, pdf_yolu in self.dayanak_map.items():
            takip = next((t for t in self.takipler if t.dosya_no == dosya_no), None)
            borclu = ""
            if takip:
                borclu = ", ".join(
                    f"{b.ad} {b.soyad}".strip() for b in takip.borclular[:2])
                if len(takip.borclular) > 2:
                    borclu += f" +{len(takip.borclular)-2}"
            durumlar[dosya_no] = None
            yer.insert("", "end",
                       iid=dosya_no,
                       values=(dosya_no, borclu,
                               os.path.basename(pdf_yolu), "⏳ Bekliyor"),
                       tags=("bekliyor",))

        # Seçili satırdaki PDF yolunu günceller (hem map hem treeview)
        def _satir_guncelle(dn, yeni_yol):
            self.dayanak_map[dn] = yeni_yol
            vals = list(yer.item(dn, "values"))
            vals[2] = os.path.basename(yeni_yol)
            yer.item(dn, values=vals)

        # Buton fonksiyonları
        def _satir_pdf_ac():
            sec = yer.selection()
            if not sec:
                messagebox.showinfo("Seçim", "Önce bir satır seçin.", parent=pen)
                return
            dn = sec[0]
            yol = self.dayanak_map.get(dn, "")
            if yol and os.path.exists(yol):
                os.startfile(yol)
            else:
                messagebox.showerror("Bulunamadı", f"Dosya bulunamadı:\n{yol}",
                                     parent=pen)

        def _gozat():
            """Otomatik eşleşme yanlışsa kullanıcı doğru PDF'i seçer."""
            sec = yer.selection()
            if not sec:
                messagebox.showinfo("Seçim", "Önce bir satır seçin.", parent=pen)
                return
            dn = sec[0]
            mevcut = self.dayanak_map.get(dn, "")
            baslangic_klasor = os.path.dirname(mevcut) if mevcut else os.path.expanduser("~")
            yeni_yol = filedialog.askopenfilename(
                title=f"Dosya {dn} için doğru dayanak PDF'i seçin",
                initialdir=baslangic_klasor,
                filetypes=[("PDF", "*.pdf"), ("Tüm dosyalar", "*.*")],
                parent=pen)
            if yeni_yol:
                _satir_guncelle(dn, yeni_yol)
                # Yeni seçilen PDF'i hemen aç
                os.startfile(yeni_yol)

        def _onayla():
            sec = yer.selection()
            if not sec:
                messagebox.showinfo("Seçim", "Önce bir satır seçin.", parent=pen)
                return
            dn = sec[0]
            durumlar[dn] = True
            vals = list(yer.item(dn, "values"))
            vals[3] = "✓ Onaylandı"
            yer.item(dn, values=vals, tags=("onaylandi",))

        def _reddet():
            sec = yer.selection()
            if not sec:
                messagebox.showinfo("Seçim", "Önce bir satır seçin.", parent=pen)
                return
            dn = sec[0]
            durumlar[dn] = False
            vals = list(yer.item(dn, "values"))
            vals[3] = "✗ Reddedildi"
            yer.item(dn, values=vals, tags=("reddedildi",))

        def _tumu_onayla():
            for dn in durumlar:
                durumlar[dn] = True
                vals = list(yer.item(dn, "values"))
                vals[3] = "✓ Onaylandı"
                yer.item(dn, values=vals, tags=("onaylandi",))

        def _kaydet_kapat():
            yeni_map = {}
            for dn, onay in durumlar.items():
                if onay is True:
                    yeni_map[dn] = self.dayanak_map[dn]
                elif onay is None:
                    if messagebox.askyesno(
                            "Karar verilmedi",
                            f"Dosya {dn} için karar verilmedi.\n"
                            "Bu eşleşme ONAYLANSIN mı?",
                            parent=pen):
                        yeni_map[dn] = self.dayanak_map[dn]
            self.dayanak_map = yeni_map
            self._alacakli_tablosu_ciz()
            eslesme = len(self.dayanak_map)
            self._log(f"Dayanak onayı tamamlandı: {eslesme} eşleşme onaylandı.\n")
            pen.destroy()

        # Çift tıklama → PDF aç
        yer.bind("<Double-1>", lambda _e: _satir_pdf_ac())

        # ---- Üst buton satırı: aç / gözat ----
        ust = tk.Frame(pen, bg=RENK["arka"])
        ust.pack(fill="x", padx=18, pady=(0, 4))
        self._tk_buton(ust, "📄  PDF'i Aç", _satir_pdf_ac,
                       RENK["kart2"], RENK["kenar"], fg=RENK["yazi"],
                       genislik=16, kucuk=True).pack(side="left", padx=(0, 6))
        self._tk_buton(ust, "📂  Doğru PDF'i Seç (Gözat)", _gozat,
                       RENK["kart2"], RENK["kenar"], fg=RENK["turuncu_koyu"],
                       genislik=24, kucuk=True).pack(side="left")

        # ---- Alt buton satırı: onayla / reddet / kaydet ----
        alt = tk.Frame(pen, bg=RENK["arka"])
        alt.pack(fill="x", padx=18, pady=(4, 14))
        self._tk_buton(alt, "✓  Onayla", _onayla,
                       RENK["yesil"], RENK["yesil_koyu"],
                       genislik=12, kucuk=True).pack(side="left", padx=(0, 4))
        self._tk_buton(alt, "✗  Reddet", _reddet,
                       RENK["kirmizi"], RENK["kirmizi_koyu"],
                       genislik=12, kucuk=True).pack(side="left", padx=(0, 10))
        self._tk_buton(alt, "✓✓  Tümünü Onayla", _tumu_onayla,
                       RENK["ana"], RENK["ana_koyu"],
                       genislik=16, kucuk=True).pack(side="left", padx=(0, 4))
        self._tk_buton(alt, "💾  Kaydet ve Kapat", _kaydet_kapat,
                       RENK["yesil"], RENK["yesil_koyu"],
                       genislik=18, kucuk=True).pack(side="right")

        pen.wait_window()

    # ------------------------------------------------------------------
    #  Vekalet seçimi (alacaklı tablosundaki seçili alacaklı için)
    # ------------------------------------------------------------------
    def _vekalet_ata_secili(self):
        if self._calisiyor:
            messagebox.showwarning("Çalışıyor",
                                   "Otomasyon sürerken vekalet değiştirilemez.")
            return
        sec = self.alacakli_tablo.selection()
        if not sec:
            messagebox.showinfo("Vekalet",
                                "Önce listeden bir alacaklı seçin.")
            return
        pos = self.alacakli_tablo.index(sec[0])
        if pos >= len(self._alacakli_pos):
            return
        alacakli = self._alacakli_pos[pos]
        yol = filedialog.askopenfilename(
            title=f"Vekalet dosyası seç — {alacakli}",
            filetypes=VEKALET_TIPLERI)
        if not yol:
            return
        self.vekalet_map[alacakli] = yol
        self._log(f"Vekalet atandı → {alacakli}: {os.path.basename(yol)}\n")
        self._alacakli_tablosu_ciz()
        self._listeleri_ciz()
        if self.secili_index is not None:
            self._detay_goster(self.secili_index)

    # ------------------------------------------------------------------
    #  Kaynak (XML/Excel) seçimi  — her zaman değiştirilebilir
    # ------------------------------------------------------------------
    def _kaynak_sec(self):
        if self._calisiyor:
            messagebox.showwarning("Çalışıyor",
                                   "Otomasyon sürerken kaynak değiştirilemez.")
            return
        if self.takipler and self.acilan:
            if not messagebox.askyesno(
                    "Dosya Değiştir",
                    "Yeni bir dosya seçilirse mevcut liste ve ilerleme sıfırlanır.\n"
                    "Devam edilsin mi?"):
                return
        yol = filedialog.askopenfilename(
            title="UYAP XML veya Excel seçin",
            filetypes=[("XML / Excel", "*.xml *.xlsx"),
                       ("XML", "*.xml"), ("Excel", "*.xlsx"),
                       ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.kaynak_yolu = yol
        self.kaynak_etiketi.configure(text=os.path.basename(yol),
                                      fg=RENK["yazi"])
        self._durum("Dosya okunuyor...", RENK["turuncu"])
        self._log(f"\nKaynak seçildi: {yol}\n")
        self._buton_durum(self.sec_btn, False)
        threading.Thread(target=self._kaynak_oku_worker,
                         args=(yol,), daemon=True).start()

    def _kaynak_kaldir(self):
        if self._calisiyor:
            messagebox.showwarning("Çalışıyor",
                                   "Otomasyon sürerken dosya kaldırılamaz.")
            return
        if not self.takipler:
            return
        if not messagebox.askyesno(
                "XML Kaldır",
                "Yüklü dosya ve tüm liste sıfırlansın mı?\n"
                "(Atadığınız vekaletler de temizlenir.)"):
            return
        self.kaynak_yolu = None
        self.takipler = []
        self.alacaklilar = []
        self.vekalet_map = {}
        self.dayanak_map = {}
        self.bekleyen = []
        self.acilan = []
        self.takip_durum = {}
        self.hata_mesajlari = {}
        self.secili_takipler = set()
        self.aktif_index = None
        self.secili_index = None

        self.kaynak_etiketi.configure(text="Henüz XML / Excel seçilmedi.",
                                      fg=RENK["soluk"])
        self.ozet_etiketi.configure(text="")
        self._alacakli_tablosu_ciz()
        self._listeleri_ciz()
        self._detay_goster(None)
        self._ilerleme_guncelle()
        self._durum("Hazır", RENK["yan_soluk"])
        self._log("Dosya kaldırıldı. Yeni bir XML / Excel seçebilirsiniz.\n")
        self._butonlari_guncelle()

    def _kaynak_oku_worker(self, yol):
        try:
            takipler = mts.kaynaktan_takipler(yol)
        except Exception as e:
            import traceback
            hata_metni = traceback.format_exc()
            self.after(0, lambda e=e, tb=hata_metni: self._okuma_hatasi(e, tb))
            return
        self.after(0, lambda: self._okuma_tamam(takipler))

    def _okuma_hatasi(self, e, tb=""):
        self._durum("Okuma hatası", RENK["kirmizi"])
        self._log(f"HATA: Kaynak okunamadı: {e}\n{tb}\n")
        self._buton_durum(self.sec_btn, True)
        self._butonlari_guncelle()
        messagebox.showerror("Okuma Hatası", f"Dosya okunamadı:\n{e}")

    def _okuma_tamam(self, takipler):
        # try/finally: çizim sırasında bir hata olsa bile butonlar yeniden
        # etkinleşsin — aksi halde "Dosya Seç" gri kalıp yeni XML yüklenemez.
        try:
            self.takipler = takipler
            gorulmus = []
            for t in takipler:
                if t.alacakli and t.alacakli not in gorulmus:
                    gorulmus.append(t.alacakli)
            self.alacaklilar = gorulmus
            # Hâlâ var olan alacaklıların vekaletini koru
            self.vekalet_map = {a: y for a, y in self.vekalet_map.items()
                                if a in gorulmus}

            self.bekleyen = list(range(len(takipler)))
            self.acilan = []
            self.takip_durum = {i: "bekleyen" for i in range(len(takipler))}
            self.hata_mesajlari = {}
            self.secili_takipler = set(range(len(takipler)))   # başlangıçta hepsi seçili
            self.aktif_index = None
            self.secili_index = 0 if takipler else None

            self._alacakli_tablosu_ciz()
            self._listeleri_ciz()
            self._detay_goster(self.secili_index)
            self._ilerleme_guncelle()
            self.ozet_etiketi.configure(
                text=f"{len(takipler)} takip · {len(gorulmus)} alacaklı")
            if takipler:
                self._durum(f"{len(takipler)} takip hazır", RENK["yesil"])
            else:
                self._durum("Dosyada takip bulunamadı", RENK["turuncu"])
            self._log(f"{len(takipler)} takip bulundu. "
                      f"Alacaklılar: {', '.join(gorulmus) or '-'}\n")
        except Exception as e:
            import traceback
            self._durum("Çizim hatası", RENK["kirmizi"])
            self._log(f"HATA (okuma_tamam): {e}\n{traceback.format_exc()}\n")
        finally:
            self._buton_durum(self.sec_btn, True)
            self._butonlari_guncelle()

    # ------------------------------------------------------------------
    #  Çalıştırma kontrolü
    # ------------------------------------------------------------------
    def _baslat(self):
        if self._calisiyor or not self.takipler:
            if not self.takipler:
                messagebox.showwarning("Eksik veri", "Önce bir XML / Excel seçin.")
            return
        if not self.bekleyen:
            messagebox.showinfo("Bitti", "Açılacak bekleyen takip kalmadı.")
            return
        # Yalnızca checkbox ile işaretli (açılacak) bekleyen takipler
        acilacak = [i for i in self.bekleyen if i in self.secili_takipler]
        if not acilacak:
            messagebox.showwarning(
                "Takip seçilmedi",
                "Açılacak takip işaretlenmedi.\n"
                "Listeden en az bir takibi ☑ ile seçin "
                "veya 'Hepsini Seç' butonunu kullanın.")
            return
        il = self.il_var.get().strip() or "İzmir"
        adliye = self.adliye_var.get().strip() or "İzmir"
        mod = self.mod_var.get()

        # Açılacak (işaretli) takiplerde vekaleti olmayan alacaklılar
        vekaletsiz = [self.takipler[i].alacakli for i in acilacak
                      if not self.vekalet_map.get(self.takipler[i].alacakli)]
        if vekaletsiz:
            benzersiz = ", ".join(dict.fromkeys(vekaletsiz))
            if not messagebox.askyesno(
                    "Vekalet eksik",
                    f"Şu alacaklılar için vekalet seçilmedi:\n\n{benzersiz}\n\n"
                    "Bu takiplerde vekaletname adımı atlanacak. Devam edilsin mi?"):
                return

        self.kontrol = mts.KontrolDurumu(log_fonksiyonu=lambda m: print(m))
        self._calisiyor = True
        self._manuel_beklemede = False
        self._butonlari_guncelle()
        mod_ad = dict((d, b) for d, b, _ in MODLAR).get(mod, mod)
        self._durum("Çalışıyor...", RENK["yesil"])
        self._log(f"\n=== OTOMASYON BAŞLATILIYOR — {mod_ad} ===\n")
        self.worker = threading.Thread(
            target=self._calistir_worker, args=(il, adliye, mod), daemon=True)
        self.worker.start()

    def _calistir_worker(self, il, adliye, mod):
        try:
            bot = mts.UyapBot()
            print("Tarayıcı oturumuna bağlanılıyor...")
            bot.oturumla_baglan()

            # Tam Kontrol: her aşamadan önce pencere içi onay çubuğu göster
            def _adim_onayi(ad):
                return self._onay_iste(
                    f"AŞAMA ONAYI  ·  {ad}   —   uygulansın mı?",
                    [("✓  Onayla", True, RENK["yesil"], RENK["yesil_koyu"]),
                     ("⏹  Durdur", "_DURDUR_", RENK["kirmizi"],
                      RENK["kirmizi_koyu"])])
            onay_cb = _adim_onayi if mod == "tam" else None

            for i in list(self.bekleyen):
                # Checkbox ile seçilmemiş takipler açılmaz, atlanır.
                if i not in self.secili_takipler:
                    continue
                self.kontrol.kontrol_noktasi()
                takip = self.takipler[i]
                self.after(0, lambda idx=i: self._takip_basladi(idx))



                # Kontrol modu: veri girişi tamamlanınca kullanıcı formu gözden geçirir
                def _veri_girisi_onayi(dn=takip.dosya_no):
                    return self._onay_iste(
                        f"Dosya {dn}  ·  Veri girişi tamamlandı.\n"
                        "UYAP formunu kontrol edin, ardından onaylayın.",
                        [("✓  Onayla", True, RENK["yesil"], RENK["yesil_koyu"]),
                         ("⏹  Durdur", "_DURDUR_", RENK["kirmizi"],
                          RENK["kirmizi_koyu"])])
                veri_girisi_onay_cb = _veri_girisi_onayi if mod in ("kontrol", "tam") else None

                # Borçlu sorgusunda UYAP hatası çıkarsa akışı durdur ve sor.
                # Tüm modlarda aktif — hata anında körlemesine devam edilmemeli.
                def _borclu_hata_onayi(dosya_no, borclu_ad, mesaj):
                    return self._onay_iste(
                        f"Dosya {dosya_no}  ·  Borçlu '{borclu_ad}' eklenemedi:\n"
                        f"{mesaj}\n"
                        "Bu dosya atlanıp sıradaki dosyaya geçilsin mi?",
                        [("⤼  Bu dosyayı atla", "atla",
                          RENK["turuncu"], RENK["turuncu"]),
                         ("⏹  Durdur", "_DURDUR_", RENK["kirmizi"],
                          RENK["kirmizi_koyu"])])

                # İmza öncesi doğrulama uyarısı: kullanıcı kararı (üç seçenek)
                def _dogrulama_onayi(dosya_no, rapor):
                    return self._onay_iste(
                        f"Dosya {dosya_no}  ·  İMZA ÖNCESİ DOĞRULAMA:\n{rapor}\n"
                        "Yine de imzalansın mı, dosya atlansın mı?",
                        [("⤼  Atla", "atla", RENK["turuncu"], RENK["turuncu"]),
                         ("✓  Yine de imzala", "devam",
                          RENK["yesil"], RENK["yesil_koyu"]),
                         ("⏹  Durdur", "_DURDUR_", RENK["kirmizi"],
                          RENK["kirmizi_koyu"])])

                # Onay/imza adımında UYAP uyarısı: kullanıcı tarayıcıda elle
                # düzeltip karar verir (tekrar dene / durdur).
                def _manuel_mudahale_onayi(dosya_no, adim, mesaj):
                    return self._onay_iste(
                        f"Dosya {dosya_no}  ·  {adim} — UYAP uyarısı:\n{mesaj}\n"
                        "Tarayıcıda elle düzeltin, sonra seçin:",
                        [("🔄  Düzelttim, tekrar dene", "tekrar",
                          RENK["yesil"], RENK["yesil_koyu"]),
                         ("⏹  Durdur", "_DURDUR_", RENK["kirmizi"],
                          RENK["kirmizi_koyu"])])

                # Genel hata oluştuğunda (herhangi bir adımda) kullanıcıya
                # 'Sorunu Çözdüm' / 'Dosyayı Atla' / 'Durdur' seçeneği sun.
                def _genel_hata_onayi(dosya_no, adim, mesaj):
                    # GUI'de 'Sorunu Çözdüm' butonunu göster ve akışı beklet
                    self.after(0, self._cozum_butonlarini_goster)
                    karar = self.kontrol.bekle_manuel_cozum()
                    self.after(0, self._cozum_butonlarini_gizle)
                    return karar

                print(f"\n========== Dosya No: {takip.dosya_no} ==========")
                try:
                    mts.takip_ac(bot, takip, kontrol=self.kontrol,
                                 vekalet_map=self.vekalet_map,
                                 dayanak_map=self.dayanak_map,
                                 il=il, adliye=adliye, onay=onay_cb,
                                 veri_girisi_onay=veri_girisi_onay_cb,
                                 borclu_hata_onay=_borclu_hata_onayi,
                                 dogrulama_onay=_dogrulama_onayi,
                                 manuel_mudahale_onay=_manuel_mudahale_onayi,
                                 genel_hata_onay=_genel_hata_onayi)
                except mts.TakipDurduruldu:
                    raise
                except mts.DosyaAtla as e:
                    # Borçlu hatası — ekranı temizle, dosyayı 'atlandı' işaretle
                    print(f"⤼ Dosya No {takip.dosya_no} atlandı (borçlu hatası): {e}")
                    try:
                        bot.taraflari_temizle()
                    except Exception as ce:
                        print(f"Temizleme sırasında hata (yok sayıldı): {ce}")
                    self.after(0, lambda idx=i: self._takip_atlandi(idx))
                    continue
                except Exception as e:
                    print(f"!!! HATA — Dosya No {takip.dosya_no} hatalılara eklendi: {e}")
                    self.after(0, lambda idx=i, msg=str(e):
                               self._takip_bitti(idx, False, hata_mesaji=msg))
                    continue
                self.after(0, lambda idx=i: self._takip_bitti(idx, True))

            print("\n✓ Tüm takipler tamamlandı.")
            self.after(0, lambda: self._calisma_bitti("Tamamlandı", RENK["yesil"]))
        except mts.TakipDurduruldu:
            print("\n⏹ Otomasyon durduruldu.")
            self.after(0, lambda: self._calisma_bitti("Durduruldu", RENK["kirmizi"]))
        except Exception as e:
            print(f"\n!!! Beklenmeyen hata: {e}")
            import traceback
            traceback.print_exc()
            self.after(0, lambda: self._calisma_bitti("Hata", RENK["kirmizi"]))

    def _takip_basladi(self, idx):
        self.aktif_index = idx
        if self.takip_durum.get(idx) != "atlandi":
            self.takip_durum[idx] = "aktif"
        self.secili_index = idx
        self._listeleri_ciz()
        self._detay_goster(idx)
        self._durum(f"Dosya {self.takipler[idx].dosya_no}", RENK["turuncu"])

    def _takip_bitti(self, idx, basarili, hata_mesaji=None):
        self.takip_durum[idx] = "tamam" if basarili else "hata"
        if basarili:
            self.hata_mesajlari.pop(idx, None)
        elif hata_mesaji:
            self.hata_mesajlari[idx] = hata_mesaji
        self.secili_takipler.discard(idx)
        if idx in self.bekleyen:
            self.bekleyen.remove(idx)
        if idx not in self.acilan:
            self.acilan.append(idx)
        if self.aktif_index == idx:
            self.aktif_index = None
        self._listeleri_ciz()
        self._ilerleme_guncelle()
        if self.secili_index == idx:
            self._detay_goster(idx)

    def _takip_atlandi(self, idx):
        # Bekleyen listede kalır ama 'atlandı' olarak işaretlenir
        self.takip_durum[idx] = "atlandi"
        if self.aktif_index == idx:
            self.aktif_index = None
        self._listeleri_ciz()
        if self.secili_index == idx:
            self._detay_goster(idx)

    def _ilerleme_guncelle(self):
        toplam = len(self.takipler)
        biten = len(self.acilan)
        self.ilerleme.configure(maximum=max(toplam, 1), value=biten)
        self.ilerleme_etiketi.configure(text=f"{biten} / {toplam}")

    def _calisma_bitti(self, durum, renk):
        self._calisiyor = False
        self.kontrol = None
        self.worker = None
        self.aktif_index = None
        self._onay_bekliyor = False
        self._manuel_beklemede = False
        self._onay_bar_gizle()
        self._cozum_butonlarini_gizle()
        self._listeleri_ciz()
        self._durum(durum, renk)
        self._butonlari_guncelle()
        self._detay_goster(self.secili_index)

    def _duraklat_devam(self):
        if not self._calisiyor or not self.kontrol:
            return
        if self.kontrol.duraklatildi_mi:
            self.kontrol.devam_et()
            self._durum("Çalışıyor...", RENK["yesil"])
        else:
            self.kontrol.duraklat()
            self._durum("Duraklatıldı", RENK["turuncu"])
        self._butonlari_guncelle()

    def _durdur(self):
        if not self._calisiyor or not self.kontrol:
            return
        # Onay çubuğu beklerken onay kutusu sormadan anında durdur
        if self._onay_bekliyor:
            self.kontrol.durdur()
            self._onay_ver("_DURDUR_")
            self._durum("Durduruluyor...", RENK["kirmizi"])
            self._butonlari_guncelle()
            return
        # 'Sorunu Çözdüm' beklerken de anında durdur
        if self._manuel_beklemede:
            self.kontrol.durdur()           # KontrolDurumu._durdur() → manuel_bekle.set()
            self._cozum_butonlarini_gizle()
            self._durum("Durduruluyor...", RENK["kirmizi"])
            self._butonlari_guncelle()
            return
        if messagebox.askyesno("Durdur",
                               "Otomasyon durdurulsun mu?\n"
                               "Mevcut adım bittikten sonra duracak."):
            self.kontrol.durdur()
            # Olası bekleyen onayı da serbest bırak
            if self._onay_bekliyor:
                self._onay_ver("_DURDUR_")
            self._durum("Durduruluyor...", RENK["kirmizi"])
            self._butonlari_guncelle()

    # ------------------------------------------------------------------
    #  Buton durumları
    # ------------------------------------------------------------------
    def _butonlari_guncelle(self):
        calisiyor = self._calisiyor
        duraklatildi = bool(self.kontrol and self.kontrol.duraklatildi_mi)
        secili_var = any(i in self.secili_takipler for i in self.bekleyen)
        self._buton_durum(self.baslat_btn,
                          not calisiyor and bool(self.bekleyen) and secili_var)
        self._buton_durum(self.hepsi_sec_btn, not calisiyor and bool(self.bekleyen))
        self._buton_durum(self.hepsi_kaldir_btn, not calisiyor and bool(self.bekleyen))
        self._buton_durum(self.sec_btn, not calisiyor)
        self._buton_durum(self.kaldir_btn, not calisiyor and bool(self.takipler))
        self._buton_durum(self.duraklat_btn, calisiyor)
        self._buton_durum(self.durdur_btn, calisiyor)
        self._buton_durum(self.vekalet_btn, not calisiyor)
        self._buton_durum(self.dayanak_btn, not calisiyor and bool(self.takipler))
        self._buton_durum(self.dayanak_onizle_btn,
                          not calisiyor and bool(self.dayanak_map))
        self.duraklat_btn.configure(
            text="▶  Devam" if duraklatildi else "⏸  Duraklat")
        if hasattr(self, "tekrar_btn"):
            self._tekrar_butonu_guncelle()

    def _cozum_butonlarini_goster(self):
        """Otomasyon bir hatayla takılınca 'Sorunu Çözdüm' panelini gösterir."""
        self._manuel_beklemede = True
        self._cozum_cerceve.pack(fill="x", pady=(8, 0))
        self._durum("⚠  Takıldı — Sorunu Çöz", RENK["turuncu"])
        # Durdur butonu da aktif kalmalı ki kullanıcı tamamen durdurabilsin
        self._buton_durum(self.cozum_btn, True)
        self._buton_durum(self.atla_cozum_btn, True)

    def _cozum_butonlarini_gizle(self):
        """Bekleme sona erince 'Sorunu Çözdüm' panelini gizler."""
        self._manuel_beklemede = False
        self._cozum_cerceve.pack_forget()

    def _manuel_cozuldu(self, karar="devam"):
        """'Sorunu Çözdüm' veya 'Bu Dosyayı Atla' butonuna basılınca çağrılır."""
        if self.kontrol and self.kontrol.manuel_beklemede_mi:
            self._cozum_butonlarini_gizle()
            if karar == "devam":
                self._durum("Calışıyor...", RENK["yesil"])
                self._log("✅ Kullanıcı sorunu çözdü — devam ediliyor...\n")
            else:
                self._log("↼ Kullanıcı bu dosyayı atladı.\n")
            self.kontrol.manuel_cozuldu(karar)

    def _buton_durum(self, buton, etkin):
        if etkin:
            buton.configure(state="normal", bg=buton._ana_renk, fg=buton._fg)
        else:
            buton.configure(state="disabled", bg="#475569", fg="#94a3b8")

    # ------------------------------------------------------------------
    #  Günlük / durum
    # ------------------------------------------------------------------
    def _log(self, metin):
        self._log_kuyrugu.put(metin)

    def _log_kuyrugunu_isle(self):
        yazildi = False
        try:
            while True:
                metin = self._log_kuyrugu.get_nowait()
                if not yazildi:
                    self.log_kutusu.configure(state="normal")
                    yazildi = True
                self.log_kutusu.insert("end", metin)
        except queue.Empty:
            pass
        if yazildi:
            self.log_kutusu.see("end")
            self.log_kutusu.configure(state="disabled")
        self.after(120, self._log_kuyrugunu_isle)

    def _log_temizle(self):
        self.log_kutusu.configure(state="normal")
        self.log_kutusu.delete("1.0", "end")
        self.log_kutusu.configure(state="disabled")

    def _durum(self, metin, renk=None):
        self.durum_etiketi.configure(text="●  " + metin,
                                     fg=renk or RENK["yan_soluk"])

    # ------------------------------------------------------------------
    #  Kapanış
    # ------------------------------------------------------------------
    def _kapat(self):
        if self._calisiyor:
            if not messagebox.askyesno(
                    "Çıkış",
                    "Otomasyon çalışıyor. Yine de kapatılsın mı?\n"
                    "(İşlem durdurulacak.)"):
                return
            if self.kontrol:
                self.kontrol.durdur()
        sys.stdout = self._gercek_stdout
        self.destroy()


def main():
    app = MtsGuiApp()
    app.after(60, app._mod_degisti)   # mod kartı vurgusunu ilk kez çiz
    app.mainloop()


if __name__ == "__main__":
    main()
