# -*- coding: utf-8 -*-
"""
Modül: Uygulama Mağazası
========================
SAKİN tarzda görünür mağaza ekranı. Katalogdaki her app için bir kart gösterir:
ad + açıklama + fiyat + durum (Sahip / Sahip değil) ve bir düğme
(Etkinleştir / Kaldır). Düğme yerel sahiplik deposunu (magaza_core) günceller ve
`app.uygulamalari_yenile()` ile sol menüyü CANLI yeniler — özellik satın alındıkça
menüye eklenir, kaldırılınca çıkar.

Buradaki "satın alma" şimdilik yerel bir aç/kapa düğmesidir; gerçek ödeme/sunucu
en sona kalır (bkz. docs/EKLENTI_MIMARISI.md).
"""

import tkinter as tk

from theme import C, RoundButton, accent as _accent
from . import magaza_core


class MagazaPanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self._build()

    # ─────────────────────────────── arayüz ───────────────────────────────
    def _build(self):
        self.wrap = tk.Frame(self.parent, bg=C.BG)
        self.wrap.pack(fill="both", expand=True)

        # başlık (sabit)
        head = tk.Frame(self.wrap, bg=C.BG)
        head.pack(fill="x", padx=40, pady=(34, 0))
        tk.Label(head, text="Uygulama Mağazası", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(head, text="İhtiyacınız olan özelliği etkinleştirin; sol menüye anında "
                            "eklenir. İstemediğinizde kaldırın — çekirdek (UYAP Bağlantısı, "
                            "Mağaza, Ayarlar) her zaman kalır.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub,
                 wraplength=820, justify="left").pack(anchor="w", pady=(6, 0))
        tk.Frame(self.wrap, bg=C.LINE, height=1).pack(fill="x", padx=40, pady=(18, 0))

        # kart ızgarası (kaydırılabilir — app sayısı arttıkça)
        govde = tk.Frame(self.wrap, bg=C.BG)
        govde.pack(fill="both", expand=True)
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

        self._kartlari_ciz()

    # ── katalog kartlarını (yeniden) çiz ──
    def _kartlari_ciz(self):
        for w in self.grid.winfo_children():
            w.destroy()

        ic = tk.Frame(self.grid, bg=C.BG)
        ic.pack(fill="both", expand=True, padx=40, pady=28)
        ic.grid_columnconfigure(0, weight=1, uniform="m")
        ic.grid_columnconfigure(1, weight=1, uniform="m")

        sahip = magaza_core.sahip_olunanlar()
        for i, app_kayit in enumerate(magaza_core.katalog()):
            self._kart(ic, app_kayit, app_kayit["kimlik"] in sahip,
                       row=i // 2, col=i % 2)

    def _kart(self, parent, app_kayit, sahip_mi, row, col):
        # Katalog accent'i İSİMDİR ("sage" vb.) — tkinter için hex'e çöz.
        accent = _accent(app_kayit["yapraklar"][0].get("accent")) \
            if app_kayit.get("yapraklar") else C.SAGE

        card = tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

        # sol renk şeridi
        tk.Frame(card, bg=accent, width=5).pack(side="left", fill="y")

        body = tk.Frame(card, bg=C.CARD)
        body.pack(side="left", fill="both", expand=True, padx=18, pady=16)

        ust = tk.Frame(body, bg=C.CARD)
        ust.pack(fill="x")
        tk.Label(ust, text=app_kayit["ad"], bg=C.CARD, fg=C.INK,
                 font=self.app.f_card_t).pack(side="left")
        # durum rozeti
        if sahip_mi:
            rozet_txt, rozet_fg = "● Sahip", C.SAGE_DK
        else:
            rozet_txt, rozet_fg = "○ Sahip değil", C.INK_FAINT
        tk.Label(ust, text=rozet_txt, bg=C.CARD, fg=rozet_fg,
                 font=self.app.f_small).pack(side="right")

        tk.Label(body, text=app_kayit.get("aciklama", ""), bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_card_d, wraplength=300, justify="left",
                 anchor="w").pack(fill="x", pady=(8, 12))

        alt = tk.Frame(body, bg=C.CARD)
        alt.pack(fill="x")
        tk.Label(alt, text=app_kayit.get("fiyat", ""), bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_small).pack(side="left")

        if sahip_mi:
            RoundButton(alt, "Kaldır", command=lambda: self._toggle(app_kayit, True),
                        kind="ghost", font=self.app.f_nav_b, height=32, pad=14).pack(side="right")
        else:
            RoundButton(alt, "Etkinleştir", command=lambda: self._toggle(app_kayit, False),
                        kind="primary", font=self.app.f_nav_b, height=32, pad=14).pack(side="right")

    # ── etkinleştir / kaldır ──
    def _toggle(self, app_kayit, sahip_mi):
        kimlik = app_kayit["kimlik"]
        if sahip_mi:
            magaza_core.sahip_cikar(kimlik)
        else:
            magaza_core.sahip_ekle(kimlik)
        # Sol menüyü canlı yenile (çekirdek panellere dokunmaz), sonra kartları tazele.
        try:
            self.app.uygulamalari_yenile()
        except Exception:
            pass
        self._kartlari_ciz()
        try:
            durum = "etkinleştirildi" if not sahip_mi else "kaldırıldı"
            self.app.status.config(text=f"{app_kayit['ad']} {durum}")
        except Exception:
            pass
