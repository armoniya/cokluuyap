# -*- coding: utf-8 -*-
"""
Modül: Senkron Kapsamı
=======================
Kullanıcının hangi Yargı Türü + Yargı Birimi kombinasyonlarının arka planda
taranıp DB'ye indirileceğini seçtiği ayar ekranı (bkz.
docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §0/§2.4). Çalışma mantığı ve DB erişimi
TAMAMEN `dosya_core.py`'dedir (`senkron_kapsami_durumu_getir`,
`senkron_kapsami_kaydet`, `yargi_birimleri_db_den_yukle`/`_getir`); burada
yalnızca sunum vardır. Burada kaydedilen seçim hem masaüstü hem web
panelinde AYNI `SenkronKapsami` tablosu üzerinden geçerlidir — iki arayüz
ayrı depo tutmaz, aynı Django ORM/DB'yi paylaşır.
"""

import queue
import threading
import tkinter as tk

from theme import C, RoundButton
from . import dosya_core


class SenkronKapsamiPanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.result_q = queue.Queue()

        self.tumu_vars = {}     # yargi_turu -> BooleanVar ("tüm tür")
        self.birim_frames = {}  # yargi_turu -> tk.Frame (birim checkbox listesi)

        self._build()
        threading.Thread(target=self._ilk_yukleme_bg, daemon=True).start()
        self.app.after(200, self._poll)

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="Senkron Kapsamı", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Hangi yargı türü ve yargı biriminin dosyaları arka "
                 "planda taranıp indirilsin? Hiçbir kutu işaretlenmezse senkron "
                 "çalışmaz. Burada kaydedilen seçim web panelinde de geçerlidir.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=720,
                 justify="left").pack(anchor="w", pady=(6, 14))

        ust = tk.Frame(wrap, bg=C.BG)
        ust.pack(fill="x")
        self.kaydet_btn = RoundButton(ust, "Kaydet", command=self._kaydet,
                                      kind="primary", font=self.app.f_nav_b, height=36)
        self.kaydet_btn.pack(side="left", ipadx=8)
        self.durum_lbl = tk.Label(ust, text="", bg=C.BG, fg=C.SAGE_DK, font=self.app.f_small)
        self.durum_lbl.pack(side="left", padx=(14, 0))

        # ── kaydırılabilir gövde (yargı türü kartları) ──
        govde = tk.Frame(wrap, bg=C.BG)
        govde.pack(fill="both", expand=True, pady=(14, 0))
        self.canvas = tk.Canvas(govde, bg=C.BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(govde, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.grid = tk.Frame(self.canvas, bg=C.BG)
        win = self.canvas.create_window((0, 0), window=self.grid, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(win, width=e.width))
        self.grid.bind("<Configure>",
                       lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        def _wheel(e):
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", _wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        sb.pack(side="right", fill="y")

        for kod, ad in dosya_core.YARGI_TURLERI:
            self._turu_karti_kur(kod, ad)

    def _turu_karti_kur(self, yargi_turu, ad):
        kart = tk.Frame(self.grid, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        kart.pack(fill="x", pady=(0, 10))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)

        ust = tk.Frame(ic, bg=C.CARD)
        ust.pack(fill="x")
        tk.Label(ust, text=ad, bg=C.CARD, fg=C.INK, font=self.app.f_card_t).pack(side="left")

        tumu_var = tk.BooleanVar(value=False)
        self.tumu_vars[yargi_turu] = tumu_var
        tk.Checkbutton(ust, text="Tümü (bu yargı türünün tamamı)", variable=tumu_var,
                       bg=C.CARD, fg=C.INK, activebackground=C.CARD,
                       activeforeground=C.INK, selectcolor=C.CARD,
                       font=self.app.f_body, bd=0, highlightthickness=0,
                       cursor="hand2", command=lambda t=yargi_turu: self._tumu_degisti(t)
                       ).pack(side="left", padx=(16, 0))

        birim_frame = tk.Frame(ic, bg=C.CARD)
        birim_frame.pack(fill="x", pady=(8, 0))
        self.birim_frames[yargi_turu] = birim_frame
        tk.Label(birim_frame, text="Yükleniyor…", bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_small).pack(anchor="w")

    def _birim_widgets(self, yargi_turu):
        """(yargi_turu, kod) -> (BooleanVar, Checkbutton) çiftlerini üretir
        (o türün çizilmiş birim kutuları)."""
        frame = self.birim_frames.get(yargi_turu)
        if frame is None:
            return
        for w in frame.winfo_children():
            var = getattr(w, "_kapsam_var", None)
            kod = getattr(w, "_kapsam_kod", None)
            if var is not None and kod is not None:
                yield kod, (var, w)

    def _tumu_degisti(self, yargi_turu):
        """'Tümü' işaretlenince o türün tekil birim kutuları pasif görünsün
        (kayıtta 'Tümü' zaten hepsini kapsar, çakışan seçim gösterilmesin)."""
        acik = not self.tumu_vars[yargi_turu].get()
        for _kod, (_var, chk) in self._birim_widgets(yargi_turu):
            chk.config(state=("normal" if acik else "disabled"))

    # ─────────────────────────── veri yükleme ───────────────────────────
    def _log(self, mesaj):
        self.result_q.put(("log", str(mesaj)))

    def _ilk_yukleme_bg(self):
        try:
            durum = dosya_core.senkron_kapsami_durumu_getir()
        except Exception as e:
            self._log(f"⚠️ Mevcut senkron kapsamı okunamadı: {e}")
            durum = []
        aktif_kumesi = {(d["yargi_turu"], d["yargi_birimi_kod"]) for d in durum if d["aktif"]}

        for kod, _ad in dosya_core.YARGI_TURLERI:
            birimler = dosya_core.yargi_birimleri_db_den_yukle(kod)
            self.result_q.put(("birimler", (kod, birimler, aktif_kumesi)))

    def _birim_listesini_yenile(self, yargi_turu):
        """Kullanıcı bu türün Yargı Birimi listesini canlı UYAP'tan yeniden
        çekmek isterse (DB'de hiç yoksa veya güncellemek isterse)."""
        def bg():
            try:
                birimler = dosya_core.yargi_birimleri_getir(yargi_turu, self._log)
            except Exception as e:
                self._log(f"⚠️ Liste çekilemedi: {e}")
                birimler = []
            try:
                durum = dosya_core.senkron_kapsami_durumu_getir()
                aktif_kumesi = {(d["yargi_turu"], d["yargi_birimi_kod"]) for d in durum if d["aktif"]}
            except Exception:
                aktif_kumesi = set()
            self.result_q.put(("birimler", (yargi_turu, birimler, aktif_kumesi)))
        threading.Thread(target=bg, daemon=True).start()

    def _birimleri_ciz(self, yargi_turu, birimler, aktif_kumesi):
        frame = self.birim_frames.get(yargi_turu)
        if frame is None or not frame.winfo_exists():
            return
        for w in frame.winfo_children():
            w.destroy()

        tumu_secili = (yargi_turu, "") in aktif_kumesi
        self.tumu_vars[yargi_turu].set(tumu_secili)

        if not birimler:
            tk.Label(frame, text="Yargı birimi listesi henüz alınmadı (ofis bağlantısı "
                     "kapalı olabilir).", bg=C.CARD, fg=C.INK_FAINT,
                     font=self.app.f_small).pack(anchor="w")
        for b in birimler:
            kod, ad = b.get("kod", ""), b.get("ad", "")
            if not kod:
                continue
            var = tk.BooleanVar(value=(yargi_turu, kod) in aktif_kumesi)
            chk = tk.Checkbutton(frame, text=f"{ad}  ({kod})", variable=var,
                                 bg=C.CARD, fg=C.INK, activebackground=C.CARD,
                                 activeforeground=C.INK, selectcolor=C.CARD,
                                 font=self.app.f_body, bd=0, highlightthickness=0,
                                 cursor="hand2", anchor="w",
                                 state=("disabled" if tumu_secili else "normal"))
            chk._kapsam_var = var
            chk._kapsam_kod = kod
            chk.pack(anchor="w")

        yenile = tk.Button(frame, text="↻ Listeyi UYAP'tan Getir/Yenile", bd=0,
                           bg=C.CARD, fg=C.BLUE, font=self.app.f_small,
                           cursor="hand2", activebackground=C.CARD, relief="flat",
                           command=lambda t=yargi_turu: self._birim_listesini_yenile(t))
        yenile.pack(anchor="w", pady=(6, 0))

    # ─────────────────────────── kaydet ───────────────────────────
    def _kaydet(self):
        secimler = []
        for yargi_turu, tumu_var in self.tumu_vars.items():
            if tumu_var.get():
                secimler.append((yargi_turu, ""))
                continue
            for kod, (var, _chk) in self._birim_widgets(yargi_turu):
                if var.get():
                    secimler.append((yargi_turu, kod))
        self.durum_lbl.config(text="Kaydediliyor…", fg=C.INK_SOFT)

        def bg():
            try:
                dosya_core.senkron_kapsami_kaydet(secimler)
                self.result_q.put(("kaydedildi", len(secimler)))
            except Exception as e:
                self.result_q.put(("kaydet_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                if tip == "birimler":
                    kod, birimler, aktif_kumesi = veri
                    self._birimleri_ciz(kod, birimler, aktif_kumesi)
                elif tip == "kaydedildi":
                    self.durum_lbl.config(text=f"✔ Kaydedildi ({veri} kapsam aktif).", fg=C.SAGE_DK)
                elif tip == "kaydet_hata":
                    self.durum_lbl.config(text=f"Kaydedilemedi: {veri}", fg=C.CLAY)
                elif tip == "log":
                    self.durum_lbl.config(text=str(veri), fg=C.INK_SOFT)
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
