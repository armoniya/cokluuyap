# -*- coding: utf-8 -*-
"""
Modül: Ayarlar
==============
SAKİN tarzda ayar ekranı. Tercihler orijinal config altyapısına (uyap_app.
load_config / save_config — DPAPI'li ortak uyap_app_config.json) yazılır;
yeni bir ayar deposu KURULMAZ.

Şimdilik tek ayar: "Program açıldığında UYAP bağlantısını otomatik kur".
  config["auto_connect"] = "off" | "share" | "receive"
    off     : otomatik bağlanma (varsayılan)
    share   : Bağlantı Paylaş (ofis, e-imza) — kayıtlı PIN gerekir
    receive : Bağlantı Al (istemci) — PIN gerekmez
"""

import tkinter as tk

from theme import C


AUTO_SECENEKLER = [
    ("off",     "Kapalı",
     "Program açıldığında bağlantı kurulmaz; gerektiğinde elle başlatırsınız."),
    ("share",   "Bağlantıyı Paylaş (Ofis · e-imza)",
     "Açılışta e-imza oturumu paylaşılır. Kayıtlı E-imza PIN kodu gerekir "
     "(UYAP Bağlantısı ekranında “Bilgilerimi hatırla” ile kaydedilir)."),
    ("receive", "Bağlantıyı Al (İstemci)",
     "Açılışta ofise istemci olarak bağlanılır. PIN gerekmez."),
]
AUTO_VARSAYILAN = "off"


def auto_connect_mode(auth):
    """config'ten otomatik bağlantı kipini güvenle döndürür."""
    try:
        cfg = auth.load_config() if auth else {}
        deger = cfg.get("auto_connect", AUTO_VARSAYILAN)
        if deger in {k for k, _, _ in AUTO_SECENEKLER}:
            return deger
    except Exception:
        pass
    return AUTO_VARSAYILAN


class AyarlarPanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.auto_var = tk.StringVar(value=auto_connect_mode(self._auth()))
        self._build()

    def _auth(self):
        return getattr(self.app, "_auth", None)

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="Ayarlar", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Tercihler bu bilgisayarda güvenle saklanır.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 18))

        # ── Bağlantı kartı ──
        kart = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        kart.pack(fill="x")
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=20, pady=18)

        tk.Label(ic, text="BAĞLANTI", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                 font=self.app.f_small, padx=9, pady=2).pack(anchor="w")
        tk.Label(ic, text="Program açıldığında UYAP bağlantısı", bg=C.CARD, fg=C.INK,
                 font=self.app.f_card_t).pack(anchor="w", pady=(10, 2))
        tk.Label(ic, text="Açılışta otomatik olarak hangi bağlantının kurulacağını seçin.",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_body).pack(anchor="w", pady=(0, 12))

        for kod, baslik, aciklama in AUTO_SECENEKLER:
            satir = tk.Frame(ic, bg=C.CARD)
            satir.pack(fill="x", pady=(0, 8))
            rb = tk.Radiobutton(satir, text=baslik, value=kod, variable=self.auto_var,
                                bg=C.CARD, fg=C.INK, activebackground=C.CARD,
                                activeforeground=C.INK, selectcolor=C.CARD,
                                font=self.app.f_nav_b, bd=0, highlightthickness=0,
                                cursor="hand2", anchor="w", command=self._kaydet)
            rb.pack(anchor="w")
            tk.Label(satir, text=aciklama, bg=C.CARD, fg=C.INK_FAINT,
                     font=self.app.f_small, wraplength=560, justify="left",
                     anchor="w").pack(anchor="w", padx=(24, 0))

        self.durum_lbl = tk.Label(ic, text="", bg=C.CARD, fg=C.SAGE_DK,
                                  font=self.app.f_small)
        self.durum_lbl.pack(anchor="w", pady=(4, 0))

    # ─────────────────────────── kaydet ───────────────────────────
    def _kaydet(self):
        auth = self._auth()
        if not auth:
            self.durum_lbl.config(text="Ayar kaydedilemedi (giriş modülü yüklenmedi).",
                                  fg=C.CLAY)
            return
        try:
            cfg = auth.load_config()
            cfg["auto_connect"] = self.auto_var.get()
            auth.save_config(cfg)
            self.durum_lbl.config(text="✔ Kaydedildi.", fg=C.SAGE_DK)
        except Exception as e:
            self.durum_lbl.config(text="Kaydedilemedi: %s" % e, fg=C.CLAY)
