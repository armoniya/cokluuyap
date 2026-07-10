# -*- coding: utf-8 -*-
"""
Modül: UDF İşlemleri
====================
SAKİN tarzda yeni arayüz; ÇALIŞMA MANTIĞI orijinal koddan AYNEN kullanılır
(udf_core üzerinden: converter / signer / verify — yeniden yazılmaz).

İki kart:
  • Sol  "Dönüştür" → Word/PDF → UDF
        - "Sadece Dönüştür"     → udf_core.convert_only
        - "Dönüştür ve İmzala"  → udf_core.convert_and_sign (e-imza + otomatik doğrulama)
  • Sağ  "İmzala"    → hazır bir UDF'i OLDUĞU GİBİ imzalar (dönüştürmeden)
        - dosya sürükle-bırak ALANI (tkinterdnd2 varsa) ya da tıklayıp seç
        - udf_core.sign_existing (e-imza + otomatik doğrulama)

Akıllı kart ayarları (DLL + PIN) bir butonla açılan açılır pencerededir (modal).
İşlem ayrı bir thread'de döner; sonuç/günlük kendi kuyruğuyla arayüze taşınır.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog

from theme import C, RoundButton
from . import udf_core


class UdfPanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.running = False
        self.q = queue.Queue()
        self.dlls = []
        self._card_win = None          # açık akıllı kart ayarları penceresi (modal)
        # e-imza ayarları arayüzden bağımsız yaşasın diye değişkenlerde tutulur
        self.dll_var = tk.StringVar()
        self.pin_var = tk.StringVar()
        self.show_pin = tk.BooleanVar(value=False)
        try:
            self.dlls = udf_core.find_common_dlls()
        except Exception:
            self.dlls = []
        if self.dlls:
            self.dll_var.set(self.dlls[0])

        self._build()
        self._intro()
        self._poll()

    # ─────────────────────────────── arayüz ───────────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=34)

        # başlık satırı + sağda "Akıllı Kart Ayarları" düğmesi
        head = tk.Frame(wrap, bg=C.BG)
        head.pack(fill="x")
        titles = tk.Frame(head, bg=C.BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="UDF İşlemleri", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(titles, text="Word/PDF belgelerini UYAP UDF biçimine dönüştürün ya da "
                              "hazır bir UDF'i e-imza ile imzalayın.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 0))
        RoundButton(head, "⚙  Akıllı Kart Ayarları", command=self._open_card_settings,
                    kind="ghost", font=self.app.f_nav_b, height=36, pad=14).pack(
                    side="right", anchor="n", pady=(2, 0))

        cards = tk.Frame(wrap, bg=C.BG)
        cards.pack(fill="x", pady=(22, 0))
        cards.grid_columnconfigure(0, weight=1, uniform="c")
        cards.grid_columnconfigure(1, weight=1, uniform="c")

        self._build_convert_card(cards)
        self._build_sign_card(cards)

        # ── günlük ──
        loghead = tk.Frame(wrap, bg=C.BG)
        loghead.pack(fill="x", pady=(22, 6))
        tk.Label(loghead, text="İşlem Günlüğü ve Doğrulama Raporu", bg=C.BG, fg=C.INK,
                 font=self.app.f_card_t).pack(side="left")
        clr = tk.Label(loghead, text="Temizle", bg=C.BG, fg=C.INK_SOFT,
                       font=self.app.f_small, cursor="hand2")
        clr.pack(side="right")
        clr.bind("<Button-1>", lambda e: self._clear_log())

        box = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE,
                       highlightthickness=1)
        box.pack(fill="both", expand=True)
        self.log = tk.Text(box, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=9,
                           padx=12, pady=10, insertbackground=C.INK,
                           highlightthickness=0, borderwidth=0, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(box, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)
        # renkli etiketler (orijinal app.py ile aynı anlam)
        self.log.tag_config("info", foreground=C.BLUE)
        self.log.tag_config("warn", foreground=C.GOLD)
        self.log.tag_config("error", foreground=C.CLAY)
        self.log.tag_config("ok", foreground=C.SAGE_DK)

    # ── kart iskeleti ──
    def _card(self, parent, col, legend, title):
        holder = tk.Frame(parent, bg=C.BG)
        holder.grid(row=0, column=col, sticky="nsew",
                    padx=(0, 9) if col == 0 else (9, 0))
        card = tk.Frame(holder, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=C.CARD)
        inner.pack(fill="both", expand=True, padx=20, pady=18)
        tk.Label(inner, text=legend, bg=C.SAGE_TINT, fg=C.SAGE_DK,
                 font=self.app.f_small, padx=9, pady=2).pack(anchor="w")
        tk.Label(inner, text=title, bg=C.CARD, fg=C.INK,
                 font=self.app.f_card_t).pack(anchor="w", pady=(10, 14))
        return inner

    def _labeled_entry(self, parent, label, textvariable=None, show=None):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(anchor="w", pady=(0, 4))
        e = tk.Entry(parent, textvariable=textvariable, show=(show or ""),
                     bg="#FFFFFF", fg=C.INK, relief="flat", insertbackground=C.INK,
                     font=self.app.f_body, highlightthickness=1,
                     highlightbackground=C.LINE, highlightcolor=C.SAGE)
        e.pack(fill="x", ipady=6, pady=(0, 13))
        return e

    def _entry_with_browse(self, parent, label, cmd):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(parent, bg=C.CARD)
        row.pack(fill="x", pady=(0, 13))
        e = tk.Entry(row, bg="#FFFFFF", fg=C.INK, relief="flat",
                     insertbackground=C.INK, font=self.app.f_body,
                     highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE)
        e.pack(side="left", fill="x", expand=True, ipady=6)
        b = RoundButton(row, "Gözat", command=cmd, kind="ghost",
                        font=self.app.f_small, height=32, pad=12)
        b.pack(side="left", padx=(8, 0))
        return e

    def _btn(self, parent, text, cmd, kind="primary"):
        return RoundButton(parent, text, command=cmd, kind=kind,
                           font=self.app.f_nav_b, height=38)

    # ── sol kart: dönüştür (+imzala) ──
    def _build_convert_card(self, parent):
        inner = self._card(parent, 0, "BELGE → UDF", "Dönüştür")
        self.input_entry = self._entry_with_browse(
            inner, "Giriş belgesi (.docx, .doc, .pdf)", self.browse_input)
        self.output_entry = self._entry_with_browse(
            inner, "Çıkış UDF dosyası (.udf)", self.browse_output)

        actions = tk.Frame(inner, bg=C.CARD)
        actions.pack(fill="x", pady=(4, 0))
        self.sign_btn = self._btn(actions, "Dönüştür ve İmzala", self.on_convert_sign, "primary")
        self.sign_btn.pack(side="left", ipadx=4)
        self.conv_btn = self._btn(actions, "Sadece Dönüştür", self.on_convert, "ghost")
        self.conv_btn.pack(side="left", padx=(8, 0), ipadx=4)

    # ── sağ kart: hazır UDF'i imzala (drop alanlı) ──
    def _build_sign_card(self, parent):
        inner = self._card(parent, 1, "HAZIR UDF", "İmzala")

        # sürükle-bırak / tıkla-seç alanı
        self.drop = tk.Frame(inner, bg="#FBFAF7", highlightbackground=C.LINE,
                             highlightthickness=2, height=84, cursor="hand2")
        self.drop.pack(fill="x", pady=(0, 13))
        self.drop.pack_propagate(False)
        self.drop_lbl = tk.Label(
            self.drop, bg="#FBFAF7", fg=C.INK_SOFT, font=self.app.f_small,
            justify="center",
            text=("UDF dosyasını buraya sürükleyin\nveya tıklayıp seçin"
                  if getattr(self.app, "dnd_ok", False)
                  else "UDF seçmek için tıklayın"))
        self.drop_lbl.pack(expand=True)
        for w in (self.drop, self.drop_lbl):
            w.bind("<Button-1>", lambda e: self.browse_sign_input())
        # native OS sürükle-bırak (yalnızca tkdnd yüklüyse)
        if getattr(self.app, "dnd_ok", False):
            try:
                self.drop.drop_target_register(self.app.DND_FILES)
                self.drop.dnd_bind("<<Drop>>", self.on_drop)
                self.drop.dnd_bind("<<DropEnter>>", lambda e: self._drop_active(True))
                self.drop.dnd_bind("<<DropLeave>>", lambda e: self._drop_active(False))
            except Exception:
                pass

        self.sign_input_entry = self._entry_with_browse(
            inner, "İmzalanacak UDF (.udf)", self.browse_sign_input)
        self.sign_output_entry = self._entry_with_browse(
            inner, "Çıkış (imzalı) UDF (.udf)", self.browse_sign_output)

        actions = tk.Frame(inner, bg=C.CARD)
        actions.pack(fill="x", pady=(4, 0))
        self.signudf_btn = self._btn(actions, "UDF'i İmzala", self.on_sign_existing, "primary")
        self.signudf_btn.pack(side="left", ipadx=4)

    def _drop_active(self, on):
        if self.drop.winfo_exists():
            self.drop.config(highlightbackground=C.SAGE if on else C.LINE)

    # ── akıllı kart ayarları (modal açılır pencere) ──
    def _open_card_settings(self):
        if self._card_win is not None and self._card_win.winfo_exists():
            self._card_win.lift()
            return
        win = tk.Toplevel(self.app)
        self._card_win = win
        win.title("Akıllı Kart Ayarları")
        win.configure(bg=C.CARD)
        win.transient(self.app)
        win.resizable(False, False)

        inner = tk.Frame(win, bg=C.CARD)
        inner.pack(fill="both", expand=True, padx=24, pady=22)
        tk.Label(inner, text="E-İMZA", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                 font=self.app.f_small, padx=9, pady=2).pack(anchor="w")
        tk.Label(inner, text="Akıllı Kart Ayarları", bg=C.CARD, fg=C.INK,
                 font=self.app.f_card_t).pack(anchor="w", pady=(10, 4))
        tk.Label(inner, text="İmzalama işlemleri için PKCS#11 sürücüsü ve kart PIN'i.",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small).pack(anchor="w", pady=(0, 14))

        # DLL satırı (değişkene bağlı)
        tk.Label(inner, text="PKCS#11 DLL yolu", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(anchor="w", pady=(0, 4))
        dll_row = tk.Frame(inner, bg=C.CARD)
        dll_row.pack(fill="x", pady=(0, 13))
        dll_e = tk.Entry(dll_row, textvariable=self.dll_var, bg="#FFFFFF", fg=C.INK,
                         relief="flat", insertbackground=C.INK, font=self.app.f_body,
                         highlightthickness=1, highlightbackground=C.LINE,
                         highlightcolor=C.SAGE, width=30)
        dll_e.pack(side="left", fill="x", expand=True, ipady=6)
        RoundButton(dll_row, "Gözat", command=self.browse_dll, kind="ghost",
                    font=self.app.f_small, height=32, pad=12).pack(side="left", padx=(8, 0))

        # _labeled_entry CARD arka planı varsayar; modal da CARD.
        pin_e = self._labeled_entry(inner, "Kart PIN kodu",
                                    textvariable=self.pin_var,
                                    show=("" if self.show_pin.get() else "•"))
        tk.Checkbutton(inner, text="PIN'i göster", variable=self.show_pin,
                       command=lambda: pin_e.config(show="" if self.show_pin.get() else "•"),
                       bg=C.CARD, fg=C.INK_SOFT, activebackground=C.CARD,
                       activeforeground=C.INK_SOFT, selectcolor=C.CARD,
                       font=self.app.f_small, bd=0, highlightthickness=0,
                       anchor="w", cursor="hand2").pack(anchor="w", pady=(0, 16))

        RoundButton(inner, "Tamam", command=win.destroy, kind="primary",
                    font=self.app.f_nav_b, height=38).pack(anchor="e")

        def _closed():
            self._card_win = None
            try:
                win.destroy()
            except Exception:
                pass
        win.protocol("WM_DELETE_WINDOW", _closed)
        # ortala + modal yap
        win.update_idletasks()
        try:
            px, py = self.app.winfo_rootx(), self.app.winfo_rooty()
            pw, ph = self.app.winfo_width(), self.app.winfo_height()
            w, h = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + (pw - w)//2}+{py + (ph - h)//2}")
        except Exception:
            pass
        win.grab_set()
        dll_e.focus_set()

    def _require_card(self):
        """DLL + PIN dolu mu? Değilse ayar penceresini aç ve False döndür."""
        if self.dll_var.get().strip() and self.pin_var.get().strip():
            return True
        self._log("[BİLGİ] İmzalama için akıllı kart ayarları gerekli; pencere açılıyor…\n", "info")
        self._open_card_settings()
        return False

    def _toggle_pin(self):
        pass  # geriye dönük uyum (modal kendi içinde yönetir)

    # ─────────────────────────────── dosya seçimi ───────────────────────────────
    def browse_input(self):
        path = filedialog.askopenfilename(
            title="Giriş belgesi",
            filetypes=[("Belgeler", "*.docx *.doc *.pdf"),
                       ("Word", "*.docx *.doc"), ("PDF", "*.pdf"), ("Tümü", "*.*")])
        if path:
            self._set(self.input_entry, path)
            base, _ = os.path.splitext(path)
            if not self.output_entry.get().strip():
                self._set(self.output_entry, base + ".udf")
            self._log(f"[BİLGİ] Giriş belgesi: {path}\n", "info")

    def browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Çıkış UDF", defaultextension=".udf",
            filetypes=[("UYAP Dokümanı", "*.udf")])
        if path:
            self._set(self.output_entry, path)

    def browse_sign_input(self):
        path = filedialog.askopenfilename(
            title="İmzalanacak UDF",
            filetypes=[("UYAP Dokümanı", "*.udf"), ("Tümü", "*.*")])
        if path:
            self._accept_sign_input(path)

    def browse_sign_output(self):
        path = filedialog.asksaveasfilename(
            title="İmzalı UDF", defaultextension=".udf",
            filetypes=[("UYAP Dokümanı", "*.udf")])
        if path:
            self._set(self.sign_output_entry, path)

    def on_drop(self, event):
        self._drop_active(False)
        try:
            paths = self.app.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        if not paths:
            return
        p = paths[0]
        if not p.lower().endswith(".udf"):
            self._log("[HATA] Yalnızca .udf dosyası bırakabilirsiniz.\n", "error")
            return
        self._accept_sign_input(p)

    def _accept_sign_input(self, path):
        self._set(self.sign_input_entry, path)
        base, _ = os.path.splitext(path)
        if not self.sign_output_entry.get().strip():
            self._set(self.sign_output_entry, base + "_imzali.udf")
        self.drop_lbl.config(text=os.path.basename(path), fg=C.SAGE_DK)
        self._log(f"[BİLGİ] İmzalanacak UDF: {path}\n", "info")

    def browse_dll(self):
        path = filedialog.askopenfilename(
            title="PKCS#11 DLL", filetypes=[("DLL", "*.dll"), ("Tümü", "*.*")])
        if path:
            self.dll_var.set(path)

    @staticmethod
    def _set(entry, value):
        entry.delete(0, "end")
        entry.insert(0, value)

    # ─────────────────────────────── eylemler (orijinal mantık) ───────────────────────────────
    def on_convert(self):
        inp = self.input_entry.get().strip()
        out = self.output_entry.get().strip()
        if not inp or not out:
            self._log("[HATA] Lütfen giriş ve çıkış dosyalarını seçin.\n", "error")
            return
        self._start()
        threading.Thread(target=self._run_convert, args=(inp, out), daemon=True).start()

    def on_convert_sign(self):
        inp = self.input_entry.get().strip()
        out = self.output_entry.get().strip()
        if not inp or not out:
            self._log("[HATA] Lütfen giriş ve çıkış dosyalarını seçin.\n", "error")
            return
        if not self._require_card():
            return
        self._start()
        threading.Thread(target=self._run_sign, args=(
            inp, out, self.dll_var.get().strip(), self.pin_var.get().strip()),
            daemon=True).start()

    def on_sign_existing(self):
        inp = self.sign_input_entry.get().strip()
        out = self.sign_output_entry.get().strip()
        if not inp:
            self._log("[HATA] Lütfen imzalanacak UDF dosyasını seçin (ya da sürükleyin).\n", "error")
            return
        if not out:
            base, _ = os.path.splitext(inp)
            out = base + "_imzali.udf"
            self._set(self.sign_output_entry, out)
        if not self._require_card():
            return
        self._start()
        threading.Thread(target=self._run_sign_existing, args=(
            inp, out, self.dll_var.get().strip(), self.pin_var.get().strip()),
            daemon=True).start()

    def _run_convert(self, inp, out):
        try:
            self.q.put(("log", (f"[BİLGİ] Dönüştürülüyor: {os.path.basename(inp)}…\n", "info")))
            udf_core.convert_only(inp, out)
            self.q.put(("log", (f"[BAŞARILI] İmzasız UDF kaydedildi: {out}\n", "ok")))
        except Exception as e:
            self.q.put(("log", (f"[HATA] Dönüştürme hatası: {e}\n", "error")))
        finally:
            self.q.put(("done", None))

    def _run_sign(self, inp, out, dll, pin):
        try:
            self.q.put(("log", (f"[BİLGİ] Dönüştürülüyor: {os.path.basename(inp)}…\n", "info")))
            self.q.put(("log", ("[BİLGİ] E-imza kartına bağlanılıyor ve imzalanıyor…\n", "info")))
            success, logs = udf_core.convert_and_sign(inp, out, dll, pin)
            self._emit_sign_result(out, success, logs)
        except Exception as e:
            self.q.put(("log", (f"[HATA] İşlem hatası: {e}\n", "error")))
        finally:
            self.q.put(("done", None))

    def _run_sign_existing(self, inp, out, dll, pin):
        try:
            self.q.put(("log", (f"[BİLGİ] UDF imzalanıyor (dönüştürmeden): "
                                f"{os.path.basename(inp)}…\n", "info")))
            self.q.put(("log", ("[BİLGİ] E-imza kartına bağlanılıyor ve imzalanıyor…\n", "info")))
            success, logs = udf_core.sign_existing(inp, out, dll, pin)
            self._emit_sign_result(out, success, logs)
        except Exception as e:
            self.q.put(("log", (f"[HATA] İmzalama hatası: {e}\n", "error")))
        finally:
            self.q.put(("done", None))

    def _emit_sign_result(self, out, success, logs):
        self.q.put(("log", (f"[BAŞARILI] İmzalı UDF kaydedildi: {out}\n", "ok")))
        for ln in logs:
            if "HATA" in ln:
                self.q.put(("log", (ln + "\n", "error")))
            elif "OK" in ln or "TEBRİKLER" in ln:
                self.q.put(("log", (ln + "\n", "ok")))
            else:
                self.q.put(("log", (ln + "\n", None)))
        if success:
            self.q.put(("log", ("[BAŞARILI] Belge imzalandı ve doğrulamadan geçti.\n", "ok")))
        else:
            self.q.put(("log", ("[UYARI] Belge imzalandı fakat doğrulamadan geçemedi.\n", "warn")))

    # ─────────────────────────────── durum / günlük ───────────────────────────────
    def _start(self):
        self.running = True
        for b in (self.sign_btn, self.conv_btn, self.signudf_btn):
            b.set_state("disabled")

    def _finish(self):
        self.running = False
        for b in (self.sign_btn, self.conv_btn, self.signudf_btn):
            b.set_state("normal")

    def _intro(self):
        if self.dlls:
            self._log(f"[BAŞARILI] {len(self.dlls)} adet PKCS#11 DLL otomatik bulundu.\n", "ok")
            return
        # Hızlı yollarda yok → bilgisayarda derin ara, kullanıcıya bildir
        self._log("[BİLGİ] PKCS#11 DLL bilinen yollarda yok; bilgisayarda aranıyor…\n", "info")
        threading.Thread(target=self._search_dlls_bg, daemon=True).start()

    def _search_dlls_bg(self):
        try:
            dlls = udf_core.find_dlls_deep()
        except Exception as e:
            self.q.put(("dll", ([], str(e))))
            return
        self.q.put(("dll", (dlls, None)))

    def _log(self, text, tag=None):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(*payload)
                elif kind == "done":
                    self._finish()
                elif kind == "dll":
                    dlls, err = payload
                    if dlls:
                        self.dlls = dlls
                        if not self.dll_var.get().strip():
                            self.dll_var.set(dlls[0])
                        self._log(f"[BAŞARILI] {len(dlls)} PKCS#11 DLL bulundu: "
                                  f"{dlls[0]}\n", "ok")
                    else:
                        self._log("[UYARI] DLL otomatik bulunamadı. 'Akıllı Kart Ayarları'ndan "
                                  "sürücü dosyasını (ör. akisp11.dll) elle seçin.\n", "warn")
        except queue.Empty:
            pass
        self.app.after(120, self._poll)
