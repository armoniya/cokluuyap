# -*- coding: utf-8 -*-
"""
Modül: UYAP Oturum Kaydı (Logger)
=================================
SAKİN tarzda arayüz; ÇALIŞMA MANTIĞI orijinal Logger/Uyap_session_logger.py'den
AYNEN gelir (logger_core.SessionLogger).

SGK Sorgu ile AYNI bağlantı: tarayıcı kendi e-imza login'ini yapmaz; panelin
açtığı yerel ofis proxy'sine (127.0.0.1:8800/giris) gider. UYAP'ı normal
kullanırken tüm tıklamalar ve ağ trafiği yakalanıp kaydedilir.

İKİ SEKME:
  • Canlı Günlük     — yakalama akarken sakin metin günlüğü (orijinal davranış).
  • Endpoint İnceleme — yakalanan çağrıları endpoint'e göre gruplar; her endpoint için
        İSTEK (payload → kova: 🔒 sabit / ✏️ girdi / ⚙️ iç) ve
        YANIT (response → modül ekranında gösterilecek kolonlar) kararlarını alır,
        sonra `taslak_<endpoint>_core.py` iskeletini üretir (logger_core.modul_taslagi_uret).
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from theme import C, RoundButton
from . import logger_core


_KOVA_GOSTER = {
    logger_core.KOVA_SABIT: "🔒 sabit",
    logger_core.KOVA_GIRDI: "✏️ girdi",
    logger_core.KOVA_IC:    "⚙️ iç",
}
_KOVA_SIRA = [logger_core.KOVA_SABIT, logger_core.KOVA_GIRDI, logger_core.KOVA_IC]
_MAX_KOL = 5   # İSTEK tablosunda en çok kaç çağrı sütunu gösterilsin


def _kisa(v, n=20):
    s = "" if v is None else str(v)
    s = s.replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


class LoggerPanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.engine = logger_core.SessionLogger(base_url=logger_core.OFFICE_BASE)
        self.since_log = 0
        self._running_ui = False

        # Endpoint İnceleme durumu (kullanıcı kararları sekme yenilense de korunur)
        self._gruplar = {}        # {endpoint: [kayit, ...]}
        self._ep_sirali = []      # listbox sırası
        self._secili_ep = None
        self._kova = {}           # {ep: {alan: kova}}
        self._param = {}          # {ep: {alan: parametre_adi}}
        self._goster = {}         # {ep: {alan: bool}}
        self._baslik = {}         # {ep: {alan: ekran_basligi}}
        self._yanit_sira = {}     # {ep: [alan, ...]}  (yanıt alan sırası)

        self._init_style()
        self._build()
        self._poll()

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Logger.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.map("Logger.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])
        style.configure("Logger.Treeview.Heading", background=C.HEADER, foreground=C.INK_SOFT,
                        relief="flat", font=self.app.f_nav_b)
        style.map("Logger.Treeview.Heading", background=[("active", C.SAGE_TINT)])
        style.configure("Logger.TNotebook", background=C.BG, borderwidth=0)
        style.configure("Logger.TNotebook.Tab", background=C.CARD, foreground=C.INK_SOFT,
                        padding=(16, 7), font=self.app.f_nav_b)
        style.map("Logger.TNotebook.Tab",
                  background=[("selected", C.SAGE_TINT)],
                  foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="UYAP Oturum Kaydı", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap,
                 text="Tarayıcı, panelin açtığı UYAP bağlantısına bağlanır (ayrı giriş "
                      "gerekmez). UYAP'ı normal kullandıkça tüm tıklamalar ve ağ trafiği "
                      "kaydedilir — endpoint çözümlemesi ve modül üretimi için.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub,
                 wraplength=820, justify="left").pack(anchor="w", pady=(6, 16))

        # ── araç çubuğu ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x")
        self.baslat_btn = self._btn(bar, "Başlat", self.baslat, "primary")
        self.baslat_btn.pack(side="left", ipadx=4)
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=4)
        self._btn(bar, "Çıktı Klasörü", self.klasor_ac, "ghost").pack(side="left", padx=(8, 0), ipadx=4)
        self._btn(bar, "Günlüğü Temizle", self.gunluk_temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=4)
        self.durum_lbl = tk.Label(bar, text="● Hazır", bg=C.BG, fg=C.INK_FAINT,
                                  font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── sekmeler ──
        nb = ttk.Notebook(wrap, style="Logger.TNotebook")
        nb.pack(fill="both", expand=True, pady=(14, 0))
        nb.bind("<<NotebookTabChanged>>", self._sekme_degisti)

        self._tab_gunluk = tk.Frame(nb, bg=C.BG)
        self._tab_inceleme = tk.Frame(nb, bg=C.BG)
        nb.add(self._tab_gunluk, text="Canlı Günlük")
        nb.add(self._tab_inceleme, text="Endpoint İnceleme")

        self._build_gunluk(self._tab_gunluk)
        self._build_inceleme(self._tab_inceleme)

        self._set_running(False)

    def _build_gunluk(self, parent):
        lbox = tk.Frame(parent, bg="#FBFAF7", highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        lbox.pack(fill="both", expand=True)
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word",
                           padx=12, pady=8, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

    def _build_inceleme(self, parent):
        # küçük araç çubuğu
        ibar = tk.Frame(parent, bg=C.BG)
        ibar.pack(fill="x", pady=(0, 8))
        self._btn(ibar, "Yenile", self._yenile_endpointler, "ghost").pack(side="left", ipadx=4)
        self._btn(ibar, "Kayıtları Sıfırla", self._kayit_sifirla, "ghost").pack(side="left", padx=(8, 0), ipadx=4)
        self.session_btn = self._btn(ibar, "Tüm session'u modüle çevir",
                                     self._session_uret, "primary")
        self.session_btn.pack(side="left", padx=(8, 0), ipadx=6)
        tk.Label(ibar, text="Tek endpoint için aşağıdan; tüm oturum için bu düğme.",
                 bg=C.BG, fg=C.INK_FAINT, font=self.app.f_small).pack(side="left", padx=(12, 0))

        body = tk.Frame(parent, bg=C.BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=240)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ── sol: endpoint listesi ──
        sol = tk.Frame(body, bg=C.BG)
        sol.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        tk.Label(sol, text="Yakalanan Endpointler", bg=C.BG, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w", pady=(0, 4))
        lf = tk.Frame(sol, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lf.pack(fill="both", expand=True)
        self.ep_list = tk.Listbox(lf, bg=C.CARD, fg=C.INK, font=self.app.f_body,
                                  relief="flat", highlightthickness=0, activestyle="none",
                                  selectbackground=C.SAGE_TINT, selectforeground=C.INK,
                                  borderwidth=0)
        self.ep_list.pack(side="left", fill="both", expand=True)
        epsb = tk.Scrollbar(lf, command=self.ep_list.yview)
        epsb.pack(side="right", fill="y")
        self.ep_list.config(yscrollcommand=epsb.set)
        self.ep_list.bind("<<ListboxSelect>>", self._endpoint_sec)

        # ── sağ: istek + yanıt + üret ──
        sag = tk.Frame(body, bg=C.BG)
        sag.grid(row=0, column=1, sticky="nsew")
        sag.columnconfigure(0, weight=1)
        sag.rowconfigure(1, weight=3)
        sag.rowconfigure(3, weight=2)

        tk.Label(sag, text="İSTEK — kullanıcı ne verir?  (Kova sütununa tıkla: 🔒↔✏️↔⚙️ · Parametre'ye çift tık: adlandır)",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.istek_tree = self._tree(sag, row=1)
        self.istek_tree.bind("<Button-1>", self._istek_tik)
        self.istek_tree.bind("<Double-1>", self._istek_cift_tik)

        tk.Label(sag, text="YANIT — modül ekranında ne gösterilsin?  (Göster sütununa tıkla: ☑↔☐ · Başlık'a çift tık: yeniden adlandır)",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small).grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.yanit_tree = self._tree(sag, row=3)
        self.yanit_tree.bind("<Button-1>", self._yanit_tik)
        self.yanit_tree.bind("<Double-1>", self._yanit_cift_tik)

        alt = tk.Frame(sag, bg=C.BG)
        alt.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.uret_btn = self._btn(alt, "Bu endpoint'ten taslak üret", self._uret, "primary")
        self.uret_btn.pack(side="left", ipadx=6)
        self.uret_lbl = tk.Label(alt, text="", bg=C.BG, fg=C.INK_FAINT, font=self.app.f_small)
        self.uret_lbl.pack(side="left", padx=(12, 0))
        # üretim sırasında birlikte kilitlenip açılan düğmeler
        self._uret_btns = [self.session_btn, self.uret_btn]

    def _tree(self, parent, row):
        holder = tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        holder.grid(row=row, column=0, sticky="nsew")
        tree = ttk.Treeview(holder, columns=("alan",), show="headings",
                            style="Logger.Treeview", height=4)
        tree.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(holder, command=tree.yview)
        sb.pack(side="right", fill="y")
        tree.config(yscrollcommand=sb.set)
        return tree

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind,
                           font=self.app.f_nav_b, height=36)

    # ─────────────────────────── kontroller ───────────────────────────
    def baslat(self):
        if self.engine.running:
            return
        if self.engine.start():
            self._set_running(True)

    def durdur(self):
        if self.engine.running:
            self.engine.stop()

    def klasor_ac(self):
        try:
            os.startfile(self.engine.log_dir)
        except Exception:
            pass

    def gunluk_temizle(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _set_running(self, running):
        self._running_ui = running
        self.baslat_btn.set_state("disabled" if running else "normal")
        self.durdur_btn.set_state("normal" if running else "disabled")

    # ─────────────────────────── Endpoint İnceleme ───────────────────────────
    def _sekme_degisti(self, event):
        try:
            if event.widget.tab(event.widget.select(), "text") == "Endpoint İnceleme":
                self._yenile_endpointler()
        except Exception:
            pass

    def _kayit_sifirla(self):
        if not messagebox.askyesno("Kayıtları sıfırla",
                                    "Yakalanan tüm yapılandırılmış kayıtlar silinsin mi?\n"
                                    "(Canlı günlük etkilenmez.)"):
            return
        self.engine.records_temizle()
        self._gruplar, self._ep_sirali, self._secili_ep = {}, [], None
        for d in (self._kova, self._param, self._goster, self._baslik, self._yanit_sira):
            d.clear()
        self._yenile_endpointler()
        self.istek_tree.delete(*self.istek_tree.get_children())
        self.yanit_tree.delete(*self.yanit_tree.get_children())
        self.uret_lbl.config(text="")

    def _yenile_endpointler(self):
        self._gruplar = self.engine.records_snapshot()
        self._ep_sirali = list(self._gruplar.keys())
        sel = self._secili_ep
        self.ep_list.delete(0, "end")
        for ep in self._ep_sirali:
            self.ep_list.insert("end", f"{ep}   ({len(self._gruplar[ep])})")
        if sel in self._ep_sirali:
            i = self._ep_sirali.index(sel)
            self.ep_list.selection_set(i)
            self.ep_list.see(i)

    def _endpoint_sec(self, *_):
        sel = self.ep_list.curselection()
        if not sel:
            return
        ep = self._ep_sirali[sel[0]]
        self._secili_ep = ep
        self._tablo_doldur(ep)
        self.uret_lbl.config(text="")

    def _tablo_doldur(self, ep):
        kayitlar = self._gruplar.get(ep, [])
        pay_recs = [k for k in kayitlar if isinstance(k.get("payload"), dict)]

        # alan sırası (ilk görülme) + diff önerisi
        alanlar = []
        for k in pay_recs:
            for a in k["payload"]:
                if a not in alanlar:
                    alanlar.append(a)
        oneri = logger_core._kova_diff_oner(kayitlar)
        kova_ep = self._kova.setdefault(ep, {})
        param_ep = self._param.setdefault(ep, {})
        for a in alanlar:
            kova_ep.setdefault(a, oneri.get(a, logger_core.KOVA_SABIT))
            if kova_ep[a] == logger_core.KOVA_GIRDI:
                param_ep.setdefault(a, logger_core._kimlik(a, "p"))

        # İSTEK tablosu: dinamik sütunlar
        gosterilecek = pay_recs[:_MAX_KOL]
        cols = ["alan"] + [f"c{i}" for i in range(len(gosterilecek))] + ["kova", "param"]
        self.istek_tree["columns"] = cols
        self.istek_tree.heading("alan", text="Alan")
        self.istek_tree.column("alan", width=170, anchor="w", stretch=False)
        for i, _rec in enumerate(gosterilecek):
            self.istek_tree.heading(f"c{i}", text=f"Çağrı {i + 1}")
            self.istek_tree.column(f"c{i}", width=110, anchor="w", stretch=False)
        self.istek_tree.heading("kova", text="Kova")
        self.istek_tree.column("kova", width=90, anchor="w", stretch=False)
        self.istek_tree.heading("param", text="Parametre")
        self.istek_tree.column("param", width=150, anchor="w", stretch=True)
        self.istek_tree.delete(*self.istek_tree.get_children())
        for a in alanlar:
            degerler = [_kisa(r["payload"].get(a)) for r in gosterilecek]
            self.istek_tree.insert("", "end", iid=a, values=self._istek_row(ep, a, degerler))

        # YANIT tablosu
        _yol, alan_ornek = logger_core.yanit_alanlari(kayitlar)
        self._yanit_sira[ep] = [a for a, _ in alan_ornek]
        goster_ep = self._goster.setdefault(ep, {})
        baslik_ep = self._baslik.setdefault(ep, {})
        for a, _ in alan_ornek:
            goster_ep.setdefault(a, True)
            baslik_ep.setdefault(a, a)
        ycols = ("alan", "ornek", "goster", "baslik")
        self.yanit_tree["columns"] = ycols
        for c, t, w, st in (("alan", "Yanıt Alanı", 180, False),
                            ("ornek", "Örnek Değer", 200, False),
                            ("goster", "Göster?", 70, False),
                            ("baslik", "Ekran Başlığı", 180, True)):
            self.yanit_tree.heading(c, text=t)
            self.yanit_tree.column(c, width=w, anchor="w", stretch=st)
        self.yanit_tree.delete(*self.yanit_tree.get_children())
        for a, ornek in alan_ornek:
            self.yanit_tree.insert("", "end", iid=a,
                                   values=(a, _kisa(ornek, 30),
                                           "☑" if goster_ep[a] else "☐", baslik_ep[a]))

    def _istek_row(self, ep, alan, degerler):
        kova = self._kova[ep].get(alan, logger_core.KOVA_SABIT)
        param = self._param[ep].get(alan, "") if kova == logger_core.KOVA_GIRDI else "—"
        return [alan] + degerler + [_KOVA_GOSTER.get(kova, kova), param]

    # ── İSTEK etkileşimleri ──
    def _col_id(self, tree, x):
        col = tree.identify_column(x)
        try:
            idx = int(col[1:]) - 1
        except Exception:
            return None
        cols = tree["columns"]
        return cols[idx] if 0 <= idx < len(cols) else None

    def _istek_tik(self, event):
        if self.istek_tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.istek_tree.identify_row(event.y)
        if not row or not self._secili_ep:
            return
        if self._col_id(self.istek_tree, event.x) == "kova":
            ep = self._secili_ep
            simdi = self._kova[ep].get(row, logger_core.KOVA_SABIT)
            yeni = _KOVA_SIRA[(_KOVA_SIRA.index(simdi) + 1) % len(_KOVA_SIRA)]
            self._kova[ep][row] = yeni
            if yeni == logger_core.KOVA_GIRDI:
                self._param[ep].setdefault(row, logger_core._kimlik(row, "p"))
            self._istek_satir_tazele(ep, row)

    def _istek_cift_tik(self, event):
        if self.istek_tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.istek_tree.identify_row(event.y)
        if not row or not self._secili_ep:
            return
        ep = self._secili_ep
        if self._col_id(self.istek_tree, event.x) == "param" \
                and self._kova[ep].get(row) == logger_core.KOVA_GIRDI:
            col = self.istek_tree.identify_column(event.x)
            self._hucre_duzenle(self.istek_tree, row, col,
                                self._param[ep].get(row, ""),
                                lambda v: self._param_yaz(ep, row, v))

    def _param_yaz(self, ep, alan, val):
        self._param[ep][alan] = logger_core._kimlik(val, "p") if val else logger_core._kimlik(alan, "p")
        self._istek_satir_tazele(ep, alan)

    def _istek_satir_tazele(self, ep, alan):
        if not self.istek_tree.exists(alan):
            return
        mevcut = list(self.istek_tree.item(alan, "values"))
        degerler = mevcut[1:-2]   # alan + ...çağrılar... + kova + param
        self.istek_tree.item(alan, values=self._istek_row(ep, alan, degerler))

    # ── YANIT etkileşimleri ──
    def _yanit_tik(self, event):
        if self.yanit_tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.yanit_tree.identify_row(event.y)
        if not row or not self._secili_ep:
            return
        if self._col_id(self.yanit_tree, event.x) == "goster":
            ep = self._secili_ep
            self._goster[ep][row] = not self._goster[ep].get(row, True)
            self._yanit_satir_tazele(ep, row)

    def _yanit_cift_tik(self, event):
        if self.yanit_tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.yanit_tree.identify_row(event.y)
        if not row or not self._secili_ep:
            return
        if self._col_id(self.yanit_tree, event.x) == "baslik":
            ep = self._secili_ep
            col = self.yanit_tree.identify_column(event.x)
            self._hucre_duzenle(self.yanit_tree, row, col,
                                self._baslik[ep].get(row, row),
                                lambda v: self._baslik_yaz(ep, row, v))

    def _baslik_yaz(self, ep, alan, val):
        self._baslik[ep][alan] = val.strip() or alan
        self._yanit_satir_tazele(ep, alan)

    def _yanit_satir_tazele(self, ep, alan):
        if not self.yanit_tree.exists(alan):
            return
        mevcut = list(self.yanit_tree.item(alan, "values"))
        self.yanit_tree.item(alan, values=(mevcut[0], mevcut[1],
                                           "☑" if self._goster[ep].get(alan, True) else "☐",
                                           self._baslik[ep].get(alan, alan)))

    # ── ortak hücre düzenleme (üstüne Entry yerleştir) ──
    def _hucre_duzenle(self, tree, row, col, ilk, commit):
        try:
            x, y, w, h = tree.bbox(row, col)
        except Exception:
            return
        if not w:
            return
        ent = tk.Entry(tree, font=self.app.f_body, relief="flat",
                       bg="#FFFFFF", fg=C.INK, highlightthickness=1,
                       highlightbackground=C.SAGE, highlightcolor=C.SAGE)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, ilk)
        ent.focus_set()
        ent.select_range(0, "end")

        def bitir(_e=None):
            val = ent.get()
            try:
                ent.destroy()
            except Exception:
                pass
            commit(val)

        ent.bind("<Return>", bitir)
        ent.bind("<FocusOut>", bitir)
        ent.bind("<Escape>", lambda e: ent.destroy())

    # ── üret ──
    def _grup_sec(self, ad):
        """Üretilen modülün menüde hangi grupta görüneceğini sorar (mevcut gruplar +
        yeni 'Üretilen Modüller'). Seçilen grup etiketini ya da iptalde None döner."""
        gruplar = [n["label"] for n in getattr(self.app, "NAV", []) if "children" in n]
        if "Üretilen Modüller" not in gruplar:
            gruplar.append("Üretilen Modüller")
        secim = {"v": None}
        dlg = tk.Toplevel(self.parent)
        dlg.title("Menüde yeri")
        dlg.configure(bg=C.BG)
        try:
            dlg.transient(self.parent.winfo_toplevel())
        except Exception:
            pass
        tk.Label(dlg, text=f"“{ad}” sol menüde hangi grup altında görünsün?",
                 bg=C.BG, fg=C.INK, font=self.app.f_body,
                 wraplength=360, justify="left").pack(padx=20, pady=(18, 10), anchor="w")
        var = tk.StringVar(value="Araçlar" if "Araçlar" in gruplar else gruplar[0])
        ttk.Combobox(dlg, textvariable=var, values=gruplar, state="readonly",
                     font=self.app.f_body, width=34).pack(padx=20, pady=(0, 14))
        btnf = tk.Frame(dlg, bg=C.BG)
        btnf.pack(padx=20, pady=(0, 16), fill="x")

        def ok():
            secim["v"] = var.get()
            dlg.destroy()

        self._btn(btnf, "Tamam", ok, "primary").pack(side="left", ipadx=6)
        self._btn(btnf, "İptal", dlg.destroy, "ghost").pack(side="left", padx=(8, 0))
        dlg.update_idletasks()
        try:
            dlg.grab_set()
        except Exception:
            pass
        self.parent.wait_window(dlg)
        return secim["v"]

    def _uret(self):
        ep = self._secili_ep
        if not ep:
            messagebox.showinfo("Endpoint seç", "Önce soldan bir endpoint seç.")
            return
        ad = simpledialog.askstring(
            "Modül adı", "Üretilecek modülün adı (menüde de bu görünür):",
            initialvalue=f"{ep.replace('.ajx', '')} sorgu", parent=self.parent)
        if not ad:
            return
        grup = self._grup_sec(ad)
        if not grup:
            return
        kayitlar = self._gruplar.get(ep, [])
        kovalar = dict(self._kova.get(ep, {}))
        param = dict(self._param.get(ep, {}))
        kolonlar = [(a, self._baslik.get(ep, {}).get(a, a))
                    for a in self._yanit_sira.get(ep, [])
                    if self._goster.get(ep, {}).get(a, True)]
        try:
            sonuc = logger_core.modul_taslagi_uret(ep, kayitlar, kovalar, param,
                                                   kolonlar, dosya_adi=ad)
        except Exception as e:
            messagebox.showerror("Üretim hatası", str(e))
            return
        self._kaydet_ve_dogrula(sonuc, ad, grup, endpoint=ep)

    def _session_uret(self):
        """Yakalanan TÜM oturumu sıralı tek modüle çevirir; ad + menü grubu sorar."""
        kayitlar = self.engine.records_list()
        if not kayitlar:
            messagebox.showinfo(
                "Kayıt yok", "Önce Başlat'a basıp UYAP'ta birkaç işlem yap; "
                "ardından tüm oturumu modüle çevir.")
            return
        ad = simpledialog.askstring(
            "Session modülü",
            f"{len(kayitlar)} adımlık oturum TEK modüle çevrilecek.\n"
            "Modülün adı (menüde de bu görünür):",
            initialvalue="oturum akışı", parent=self.parent)
        if not ad:
            return
        grup = self._grup_sec(ad)
        if not grup:
            return
        try:
            sonuc = logger_core.oturum_akisi_uret(kayitlar, ad)
        except Exception as e:
            messagebox.showerror("Üretim hatası", str(e))
            return
        self._kaydet_ve_dogrula(sonuc, ad, grup, endpoint="(oturum akışı)")

    def _kaydet_ve_dogrula(self, sonuc, ad, grup, endpoint=""):
        """Üretilen modülü registry'ye (menü grubuyla) yazar, sonra canlı doğrular."""
        kayit = {"key": sonuc["key"], "label": ad, "core": sonuc["core"],
                 "grup": grup, "endpoint": endpoint}
        if sonuc.get("adim_sayisi"):
            kayit["adim_sayisi"] = sonuc["adim_sayisi"]
        try:
            logger_core.registry_ekle(kayit)
        except Exception as e:
            messagebox.showerror("Kayıt hatası", f"Registry'ye yazılamadı:\n{e}")
            return
        sonuc["grup"] = grup
        self._uret_dogrula_baslat(sonuc)

    def _uret_dogrula_baslat(self, sonuc):
        """Üretilen modülü CANLI doğrula: 8800'e gerçek istek atar → arayüzü
        dondurmamak için ayrı thread'de çalıştır, sonucu after() ile geri taşı."""
        for b in self._uret_btns:
            b.set_state("disabled")
        self.uret_lbl.config(text=f"✓ {sonuc['core']}.py · canlı doğrulanıyor…")

        def isi():
            d = self._dogrula_calistir(sonuc)
            self.app.after(0, lambda: self._uret_bitti(sonuc, d))

        threading.Thread(target=isi, daemon=True).start()

    @staticmethod
    def _dogrula_calistir(sonuc):
        """Taze yazılan core modülünü import edip _kendini_dogrula()'yı çalıştırır.
        Bu, yakalanan tıklamanın ağ çağrısını ÜRETİLEN kodla tekrar oynatır."""
        try:
            import importlib
            mod = importlib.import_module(f"modules.{sonuc['core']}")
            importlib.reload(mod)   # diske yeni yazılan dosya kesin okunsun
            return mod._kendini_dogrula(lambda *a, **k: None)
        except Exception as e:
            return {"ok": False, "mesaj": f"Doğrulama çalıştırılamadı: {e}", "satir": 0}

    def _uret_bitti(self, sonuc, d):
        for b in self._uret_btns:
            b.set_state("normal")
        isaret = "✓" if d.get("ok") else "⚠️"
        self.uret_lbl.config(text=f"{isaret} {sonuc['core']}.py · {d.get('mesaj', '')}")
        adim = sonuc.get("adim_sayisi")
        ust = (f"{adim} adımlık oturum, tek ağ-sorgu modülüne çevrildi:\n" if adim
               else "Ağ-sorgu modülü üretildi:\n")
        grup = sonuc.get("grup", "")
        mesaj = (
            ust +
            f"  • {sonuc['core']}.py\n\n"
            f"Panel yeniden başlatıldığında sol menüde “{grup}” altında, kendi "
            "çalıştır-gör ekranıyla görünür.\n"
            "İstekleri 127.0.0.1:8800 ofis proxy'sine gönderir (UYAP Bağlantısı açık olmalı).\n"
            f"Doğrudan da çalışır:  python {sonuc['core']}.py\n\n"
            f"Canlı doğrulama: {isaret} {d.get('mesaj', '')}\n\n"
            "Üretim klasörünü açayım mı?"
        )
        if messagebox.askyesno("Modül üretildi ve menüye eklendi", mesaj):
            try:
                os.startfile(os.path.dirname(sonuc["core_yol"]))
            except Exception:
                pass

    # ─────────────────────────── günlük / polling ───────────────────────────
    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll(self):
        try:
            snap = self.engine.snapshot(self.since_log)
            for ln in snap["logs"]:
                self._append_log(ln)
            self.since_log = snap["log_n"]
            if snap["status"]:
                renk = C.SAGE_DK if snap["running"] else C.INK_FAINT
                self.durum_lbl.config(text=f"● {snap['status']}", fg=renk)
            if not snap["running"] and self._running_ui:
                self._set_running(False)
        except Exception:
            pass
        self.app.after(300, self._poll)
