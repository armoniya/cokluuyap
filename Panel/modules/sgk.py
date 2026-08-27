# -*- coding: utf-8 -*-
"""
Modül: SGK Sorgu
================
SAKİN tarzda yeni arayüz; ÇALIŞMA MANTIĞI orijinal koddan AYNEN kullanılır
(sgk_core → orijinal sgk_sorgu_gui.SorguMotoru + özetleyiciler + sabitler).

Sorgu/Excel/kaydetme orkestrasyonu sgk_core.SgkBatch içinde (algoritma birebir);
bu panel yalnızca onu sürer ve sonuçları sakin bir tabloda gösterir. UYAP
istekleri yine 127.0.0.1:8800 ofis proxy'sine gider (UYAP Bağlantısı açık olmalı).
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from theme import C, RoundButton
from . import sgk_core

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import toplu_is_dialog as _tid  # noqa: E402


class SgkPanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.batch = sgk_core.SgkBatch()
        self.since_log = 0
        self.since_rev = 0
        self.iid_row = {}     # tree iid -> excel satır no
        self.row_iid = {}     # excel satır no -> tree iid
        self.filtre = ""

        self._init_style()
        self._build()
        self._poll()

    # ─────────────────────────── ttk Treeview için sakin stil ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Sgk.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.configure("Sgk.Treeview.Heading", background=C.SIDEBAR, foreground=C.INK,
                        relief="flat", font=self.app.f_nav_b, padding=6)
        style.map("Sgk.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])
        style.map("Sgk.Treeview.Heading", background=[("active", C.SAGE_TINT)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="SGK Sorgu", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Excel listesindeki dosyalar için toplu SGK sorgusu. Sonuçlar "
                            "kök dosyaya değil, yanındaki “…_yapilanlar.xlsx” dosyalarına yazılır.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 16))

        # ── araç çubuğu ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x")
        self.load_btn = self._btn(bar, "Excel Yükle", self.excel_yukle, "primary")
        self.load_btn.pack(side="left", ipadx=4)
        self.sorgula_btn = self._btn(bar, "Sorgula", lambda: self.sorgu_baslat("normal"), "ghost")
        self.sorgula_btn.pack(side="left", padx=(8, 0), ipadx=4)
        self.retry_btn = self._btn(bar, "Hatalıları Tekrar Dene",
                                   lambda: self.sorgu_baslat("retry"), "ghost")
        self.retry_btn.pack(side="left", padx=(8, 0), ipadx=4)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=4)
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=4)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")
        self.dosya_lbl = tk.Label(bar, text="Dosya seçilmedi", bg=C.BG, fg=C.INK_FAINT,
                                  font=self.app.f_small)
        self.dosya_lbl.pack(side="right", padx=10)

        # ── sorgulanacak sütunlar ──
        sb = tk.Frame(wrap, bg=C.BG)
        sb.pack(fill="x", pady=(12, 0))
        tk.Label(sb, text="Sorgulanacak sütunlar:", bg=C.BG, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(side="left", padx=(0, 8))
        self.secili = {}
        for anahtar, baslik in sgk_core.SORGU_BASLIKLARI:
            var = tk.BooleanVar(value=True)
            self.secili[anahtar] = var
            tk.Checkbutton(sb, text=baslik, variable=var, bg=C.BG, fg=C.INK,
                           activebackground=C.BG, activeforeground=C.INK,
                           selectcolor=C.CARD, font=self.app.f_small, bd=0,
                           highlightthickness=0, cursor="hand2").pack(side="left", padx=4)

        # ── filtre ──
        fb = tk.Frame(wrap, bg=C.BG)
        fb.pack(fill="x", pady=(8, 0))
        tk.Label(fb, text="Filtre (içerir):", bg=C.BG, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(side="left", padx=(0, 6))
        self.filtre_entry = tk.Entry(fb, bg="#FFFFFF", fg=C.INK, relief="flat",
                                     insertbackground=C.INK, font=self.app.f_body,
                                     highlightthickness=1, highlightbackground=C.LINE,
                                     highlightcolor=C.SAGE, width=30)
        self.filtre_entry.pack(side="left", ipady=4)
        self.filtre_entry.bind("<Return>", lambda e: self.filtrele())
        self._btn(fb, "Uygula", self.filtrele, "ghost").pack(side="left", padx=(6, 0), ipadx=2)
        self._btn(fb, "Temizle", self.filtre_temizle, "ghost").pack(side="left", padx=(6, 0), ipadx=2)

        # ── orta: tablo + detay ──
        pan = tk.PanedWindow(wrap, orient="horizontal", bg=C.BG, bd=0,
                             sashwidth=8, sashrelief="flat")
        pan.pack(fill="both", expand=True, pady=(12, 0))

        sol = tk.Frame(pan, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        self.tree = ttk.Treeview(sol, columns=self.batch.kolonlar, show="headings",
                                 style="Sgk.Treeview")
        for k in self.batch.kolonlar:
            self.tree.heading(k, text=k)
            w = 200 if k in ("Ad Soyad", "Birim") else 150
            if k == "No":
                w = 50
            elif k == "Dosya No":
                w = 95
            self.tree.column(k, width=w, anchor="w")
        ysb = tk.Scrollbar(sol, command=self.tree.yview)
        xsb = tk.Scrollbar(sol, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        sol.rowconfigure(0, weight=1)
        sol.columnconfigure(0, weight=1)
        pan.add(sol, minsize=460, width=720)

        self.tree.tag_configure("tamam", background=C.SAGE_TINT)
        self.tree.tag_configure("sirket", background="#EFEFEA")
        self.tree.bind("<ButtonRelease-1>", self.hucre_tikla)

        sag = tk.Frame(pan, bg=C.BG)
        tk.Label(sag, text="Hücre Detayı (satıra tıklayın)", bg=C.BG, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(anchor="w", pady=(0, 4))
        dbox = tk.Frame(sag, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        dbox.pack(fill="both", expand=True)
        self.detay = tk.Text(dbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                             font=self.app.f_mono, wrap="word", width=40,
                             padx=12, pady=10, state="disabled", highlightthickness=0)
        self.detay.pack(side="left", fill="both", expand=True)
        dsb = tk.Scrollbar(dbox, command=self.detay.yview)
        dsb.pack(side="right", fill="y")
        self.detay.config(yscrollcommand=dsb.set)
        pan.add(sag, minsize=240, width=320)

        # ── günlük ──
        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(12, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=6,
                           padx=12, pady=8, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

        self._set_running(False)

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind,
                           font=self.app.f_nav_b, height=36)

    # ─────────────────────────── Excel ───────────────────────────
    def excel_yukle(self):
        if self.batch.running:
            messagebox.showwarning("Çalışıyor", "Önce sorguyu durdurun.")
            return
        yol = filedialog.askopenfilename(
            title="Excel dosyası seç",
            filetypes=[("Excel", "*.xlsx *.xlsm"), ("Tümü", "*.*")])
        if not yol:
            return
        try:
            n = self.batch.load(yol)
        except Exception as e:
            messagebox.showerror("Hata", f"Excel açılamadı:\n{e}")
            return
        # tabloyu sıfırla ve tüm satırları kur
        self.tree.delete(*self.tree.get_children())
        self.iid_row, self.row_iid = {}, {}
        self.since_log, self.since_rev = 0, 0
        snap = self.batch.snapshot(0, 0)
        for row in snap["rows"]:
            self._upsert_row(row)
        self.since_rev = snap["rev"]
        self.dosya_lbl.config(text=f"{n} satır · {yol.split('/')[-1].split(chr(92))[-1]}")
        self.sorgula_btn.config(state="normal")
        self.retry_btn.config(state="normal")
        self._drain_logs(snap)

    def _upsert_row(self, row):
        r = row["r"]
        tag = (row["tag"],) if row["tag"] else ()
        iid = self.row_iid.get(r)
        if iid is None:
            iid = self.tree.insert("", "end", values=row["values"], tags=tag)
            self.row_iid[r] = iid
            self.iid_row[iid] = r
        else:
            self.tree.item(iid, values=row["values"], tags=tag)
        self._satir_gorunurluk(iid, row["values"])

    # ─────────────────────────── filtre ───────────────────────────
    def filtrele(self):
        self.filtre = self.filtre_entry.get().strip().lower()
        for iid in list(self.iid_row.keys()):
            vals = self.tree.item(iid, "values")
            self._satir_gorunurluk(iid, vals)

    def filtre_temizle(self):
        self.filtre_entry.delete(0, "end")
        self.filtre = ""
        for iid in list(self.iid_row.keys()):
            self.tree.reattach(iid, "", "end")

    def _satir_gorunurluk(self, iid, vals):
        if not self.filtre:
            self.tree.reattach(iid, "", "end")
            return
        hay = " ".join(str(v) for v in vals).lower()
        if self.filtre in hay:
            self.tree.reattach(iid, "", "end")
        else:
            self.tree.detach(iid)

    # ─────────────────────────── tıklama → detay ───────────────────────────
    def hucre_tikla(self, event):
        iid = self.tree.identify_row(event.y)
        kol = self.tree.identify_column(event.x)
        if not iid or not kol:
            return
        try:
            kol_index = int(kol.replace("#", "")) - 1
        except ValueError:
            return
        r = self.iid_row.get(iid)
        if r is None:
            return
        baslik = self.batch.kolonlar[kol_index] if kol_index < len(self.batch.kolonlar) else ""
        deger = self.batch.hucre_tam_deger(r, kol_index)
        self.detay.config(state="normal")
        self.detay.delete("1.0", "end")
        self.detay.insert("1.0", f"【 {baslik} 】\n\n{deger}")
        self.detay.config(state="disabled")

    # ─────────────────────────── sorgu kontrolleri ───────────────────────────
    def _secili_anahtarlar(self):
        return {a for a, v in self.secili.items() if v.get()}

    def sorgu_baslat(self, mode):
        if self.batch.running:
            return
        secili = self._secili_anahtarlar()
        if not secili:
            messagebox.showinfo("Bilgi", "En az bir sorgu sütunu seçin.")
            return
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.batch.kontrol.ad, self.batch.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.batch.kontrol.ad, self.batch.kontrol,
                lambda: self._gercek_baslat(mode, secili),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._gercek_baslat(mode, secili)

    def _gercek_baslat(self, mode, secili):
        if not self.batch.start(mode, secili):
            if mode == "retry":
                messagebox.showinfo("Bilgi", "Tekrar denenecek hatalı hücre yok.")
            _tid.KAYIT_DEFTERI.sil(self.batch.kontrol.ad)
            return
        self._set_running(True)

    def duraklat_toggle(self):
        if not self.batch.running:
            return
        paused = self.batch.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self.batch.running:
            self.batch.stop()

    def _set_running(self, running):
        self._running_ui = running
        st = "disabled" if running else "normal"
        self.sorgula_btn.set_state(st)
        self.retry_btn.set_state(st)
        self.load_btn.set_state(st)
        self.duraklat_btn.set_state("normal" if running else "disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal" if running else "disabled")

    # ─────────────────────────── günlük / polling ───────────────────────────
    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _drain_logs(self, snap):
        for ln in snap["logs"]:
            self._append_log(ln)
        self.since_log = snap["log_n"]

    def _poll(self):
        try:
            snap = self.batch.snapshot(self.since_log, self.since_rev)
            self._drain_logs(snap)
            if snap["rows"]:
                for row in snap["rows"]:
                    self._upsert_row(row)
                self.since_rev = snap["rev"]
            if snap["status"]:
                self.durum_lbl.config(text=snap["status"])
            # çalışma durumu değiştiyse düğmeleri eşitle
            if not snap["running"] and getattr(self, "_running_ui", False):
                self._set_running(False)
        except Exception:
            pass
        self.app.after(200, self._poll)
