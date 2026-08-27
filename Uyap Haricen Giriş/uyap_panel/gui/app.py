#!/usr/bin/env python3
"""
uyap_panel.gui.app — UYAP Ağ Geçidi · ttkbootstrap masaüstü paneli.

Sade ve şık bir kontrol paneli. Mevcut uyap_app.py ile AYNI çekirdeği
(uyap_panel.core) kullanır; yalnızca arayüz katmanıdır.

Ekranlar:
  • Giriş            — bulut hesabı (kullanıcı adı / parola).
  • Pano
       Bağlantı      — Paylaş (ofis, e-imza) · Al (istemci) · tarayıcıda aç · günlük.
       İcra Takip    — XML/Excel → klasik İcra Takip Talebi (icra_takip_ac).
       MTS Takip     — XML/Excel → Merkezi Takip Sistemi (coklu_takip_ac).
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog
from tkinter.scrolledtext import ScrolledText

try:
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

import ttkbootstrap as ttk
from ttkbootstrap.constants import (
    BOTH, X, Y, LEFT, RIGHT, TOP, BOTTOM, W, E, EW, NSEW, CENTER, END, VERTICAL,
)
from ttkbootstrap.dialogs import Messagebox

from uyap_panel import core
from uyap_panel.core import (
    load_config, save_config, encrypt_secret, decrypt_secret,
    verify_credentials, reset_request, get_manager, TakipService, PORT,
    SgkBatch, SORGULAR, KOLONLAR, ANAHTARLAR,
)

THEME = "darkly"
APP_TITLE = "UYAP Ağ Geçidi"

# Web paneliyle (style.css) BİREBİR aynı palet. Özel ttkbootstrap teması olarak
# kaydedilir; her iki ön yüz aynı renkleri kullanır.
UYAP_THEME = "uyapdark"
UYAP_COLORS = {
    "primary":   "#3ea6e0",   # --accent
    "secondary": "#5b6b7d",
    "success":   "#34c759",   # --ok
    "info":      "#3ea6e0",
    "warning":   "#f0a72c",
    "danger":    "#ef4d56",   # --danger
    "light":     "#dce4ec",
    "dark":      "#161b25",   # --card
    "bg":        "#0b0f17",   # --bg
    "fg":        "#dce4ec",   # --text
    "selectbg":  "#2b8cc4",   # --accent2
    "selectfg":  "#ffffff",
    "border":    "#283142",   # --line
    "inputfg":   "#dce4ec",
    "inputbg":   "#11151c",   # --bg2
    "active":    "#1b2230",   # --card2
}


def _apply_theme(window):
    """style.css paletini taşıyan özel temayı uygula; olmazsa darkly'de kal."""
    try:
        from ttkbootstrap.style import ThemeDefinition
        style = window.style
        style.register_theme(ThemeDefinition(UYAP_THEME, UYAP_COLORS, "dark"))
        style.theme_use(UYAP_THEME)
    except Exception:
        pass  # fallback: darkly (yakın bir koyu tema)
    # Ağaç menü satırları biraz ferah olsun.
    try:
        window.style.configure("Treeview", rowheight=36, font=("Segoe UI", 11),
                                borderwidth=0)
        window.style.configure("Treeview.Heading", borderwidth=0)
    except Exception:
        pass


# Web panelindeki ".card" görünümü: 14px yuvarlatılmış köşe + ince kenarlık.
CARD_BG = "#0b0f17"      # --bg : yüzey, sayfa arka planıyla aynı (alt bileşenler birebir uyumlu kalır)
CARD_LINE = "#283142"    # --line
CARD_RADIUS = 14         # --radius


class RoundedCard(ttk.Frame):
    """style.css '.card' karşılığı: yuvarlatılmış köşeli, çizgi kenarlı kart.

    ttk köşe yuvarlatmayı desteklemediğinden zemin bir Canvas üzerine PIL ile
    (yumuşatılmış) çizilir; içerik ``.inner`` çerçevesine yerleştirilir. Yüzey
    rengi sayfa arka planıyla aynı tutulur; böylece içerideki etiket/anahtar/
    radyo düğmeleri tema rengiyle birebir uyumlu kalır, yalnızca köşeler ve
    kenarlık değişir. PIL yoksa düz (köşeli) kenarlığa düşer.
    """

    def __init__(self, master, title=None, padding=18, radius=CARD_RADIUS,
                 fill=CARD_BG, outline=CARD_LINE, bg=CARD_BG,
                 title_bootstyle="info", **kw):
        super().__init__(master, **kw)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._img = None
        self._last_size = (0, 0)
        inner_pad = max(padding - radius, 4)

        # Zemin: köşe üçgenleri saydam kalır, parent arka planını gösterir.
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # İçerik: köşe yaylarına taşmasın diye kenarlardan ``radius`` kadar içeride.
        self.inner = ttk.Frame(self, padding=inner_pad)
        self.inner.grid(row=0, column=0, padx=radius, pady=radius, sticky=NSEW)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.lift()  # zemin (canvas) içeriğin arkasında kalsın

        if title:
            ttk.Label(self.inner, text=title, font=("Segoe UI Semibold", 12),
                      bootstyle=title_bootstyle).pack(anchor=W, pady=(0, 10))

        self._canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event):
        w, h = event.width, event.height
        if w < 4 or h < 4 or (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        self._canvas.delete("all")
        if _HAS_PIL:
            ss = 4  # üst örnekleme → yumuşak (anti-aliased) kenar
            lw = max(1, round(1.2 * ss))
            im = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle(
                [lw, lw, w * ss - lw, h * ss - lw], radius=self._radius * ss,
                fill=self._fill, outline=self._outline, width=lw)
            im = im.resize((w, h), Image.LANCZOS)
            self._img = ImageTk.PhotoImage(im)
            self._canvas.create_image(0, 0, image=self._img, anchor="nw")
        else:
            self._canvas.create_rectangle(1, 1, w - 1, h - 1,
                                          fill=self._fill, outline=self._outline)


class UyapPanelApp:
    def __init__(self):
        self.window = ttk.Window(themename=THEME, title=APP_TITLE, size=(960, 720),
                                 minsize=(840, 640))
        _apply_theme(self.window)
        self.window.place_window_center()
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cfg = load_config()
        self.manager = get_manager()
        self.server_url = self.cfg.get("server_url", core.DEFAULT_SERVER_URL)
        self.username = ""
        self.password = ""
        self.office_code = ""
        self.role = "member"
        self.office_label = ""
        self._log_index = 0
        self.log_widget = None

        self.container = ttk.Frame(self.window, padding=0)
        self.container.pack(fill=BOTH, expand=True)

        self.show_login()
        self._poll_logs()

    # ── yardımcılar ──
    def _clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    # ════════════════════════════ GİRİŞ ════════════════════════════
    def show_login(self):
        self._clear()
        self.log_widget = None

        wrap = ttk.Frame(self.container)
        wrap.pack(fill=BOTH, expand=True)

        card_w = RoundedCard(wrap, padding=36)
        card_w.place(relx=0.5, rely=0.5, anchor=CENTER)
        card = card_w.inner

        ttk.Label(card, text="UYAP Ağ Geçidi", font=("Segoe UI Semibold", 22),
                  bootstyle="info").pack(pady=(0, 2))
        ttk.Label(card, text="Bulut hesabınızla giriş yapın",
                  bootstyle="secondary").pack(pady=(0, 22))

        ttk.Label(card, text="Ofis Kodu").pack(anchor=W)
        self.office_var = ttk.StringVar(value=self.cfg.get("office_code", ""))
        ttk.Entry(card, textvariable=self.office_var, width=38).pack(fill=X, pady=(2, 12))

        ttk.Label(card, text="Kullanıcı Adı").pack(anchor=W)
        self.user_var = ttk.StringVar(value=self.cfg.get("username", ""))
        ttk.Entry(card, textvariable=self.user_var, width=38).pack(fill=X, pady=(2, 12))

        ttk.Label(card, text="Parola").pack(anchor=W)
        saved_pass = ""
        if self.cfg.get("remember") and self.cfg.get("password_enc"):
            saved_pass = decrypt_secret(self.cfg.get("password_enc"))
        self.pass_var = ttk.StringVar(value=saved_pass)
        pe = ttk.Entry(card, textvariable=self.pass_var, show="•", width=38)
        pe.pack(fill=X, pady=(2, 12))
        pe.bind("<Return>", lambda e: self.on_login())

        self.remember_var = ttk.BooleanVar(value=self.cfg.get("remember", False))
        ttk.Checkbutton(card, text="Bilgileri hatırla", variable=self.remember_var,
                        bootstyle="round-toggle").pack(anchor=W, pady=(0, 18))

        self.login_btn = ttk.Button(card, text="Giriş Yap", bootstyle="info",
                                     command=self.on_login)
        self.login_btn.pack(fill=X)

        link = ttk.Label(card, text="Parolamı unuttum", bootstyle="info",
                         cursor="hand2")
        link.pack(pady=(10, 0))
        link.bind("<Button-1>", lambda e: self.on_forgot())

        self.login_status = ttk.Label(card, text="", wraplength=320, justify=CENTER,
                                       bootstyle="secondary")
        self.login_status.pack(pady=(10, 0))

    def on_login(self):
        office = self.office_var.get().strip()
        user = self.user_var.get().strip()
        pw = self.pass_var.get().strip()
        if not office or not user or not pw:
            self.login_status.configure(text="Lütfen tüm alanları doldurun.", bootstyle="danger")
            return
        self.login_btn.configure(state="disabled")
        self.login_status.configure(text="Sunucuya bağlanılıyor, bilgiler doğrulanıyor…",
                                    bootstyle="info")

        def worker():
            ok, msg, info = verify_credentials(self.server_url, user, pw, office_code=office)
            self.window.after(0, lambda: self._login_done(ok, msg, office, user, pw, info))

        threading.Thread(target=worker, daemon=True).start()

    def _login_done(self, ok, msg, office, user, pw, info=None):
        if not ok:
            self.login_btn.configure(state="normal")
            self.login_status.configure(text=f"Giriş başarısız: {msg}", bootstyle="danger")
            return
        info = info or {}
        self.office_code = office
        # Depo anahtarı kapsamlıdır (kullanici@ofis_kodu); sonraki API çağrıları için sakla.
        self.username, self.password = user, pw
        self.role = info.get("role", "member")
        self.office_label = info.get("office_label", "")
        self.cfg["server_url"] = self.server_url
        self.cfg["office_code"] = office
        self.cfg["username"] = user
        self.cfg["remember"] = self.remember_var.get()
        self.cfg["password_enc"] = encrypt_secret(pw) if self.remember_var.get() else ""
        save_config(self.cfg)
        self.show_dashboard()

    def on_forgot(self):
        dlg = ttk.Toplevel(title="Parola Sıfırlama")
        dlg.transient(self.window)
        frm = ttk.Frame(dlg, padding=20)
        frm.pack(fill=BOTH, expand=True)
        ttk.Label(frm, text="Parola Sıfırlama", font=("Segoe UI Semibold", 14),
                  bootstyle="info").pack(anchor=W)
        ttk.Label(frm, text="Kullanıcı adınızı ya da e-postanızı girin. Sıfırlama bağlantısı "
                  "ofis ayarına göre size ya da büronuzun master kullanıcısına gönderilir.",
                  wraplength=380, justify=LEFT, bootstyle="secondary").pack(anchor=W, pady=(4, 10))
        ttk.Label(frm, text="Ofis kodu (e-posta giriyorsanız boş bırakın)",
                  bootstyle="secondary").pack(anchor=W)
        office_var = ttk.StringVar(value=self.office_var.get().strip())
        ttk.Entry(frm, textvariable=office_var, width=40).pack(fill=X, pady=(2, 8))
        ttk.Label(frm, text="Kullanıcı adı veya e-posta", bootstyle="secondary").pack(anchor=W)
        var = ttk.StringVar(value=self.user_var.get().strip())
        ttk.Entry(frm, textvariable=var, width=40).pack(fill=X)
        status = ttk.Label(frm, text="", wraplength=380, bootstyle="secondary")
        status.pack(anchor=W, pady=(8, 8))

        def send():
            ident = var.get().strip()
            if not ident:
                status.configure(text="Kullanıcı adı veya e-posta girin.", bootstyle="danger")
                return
            status.configure(text="Gönderiliyor…", bootstyle="info")

            def worker():
                data, err = reset_request(self.server_url, ident, office_code=office_var.get().strip())
                def show():
                    if err:
                        status.configure(text=err, bootstyle="danger")
                    else:
                        extra = " (TEST modu)" if data.get("test_mode") else ""
                        status.configure(
                            text=f"Sıfırlama bağlantısı {data.get('sent_to','')} adresine gönderildi.{extra}",
                            bootstyle="success")
                self.window.after(0, show)
            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(frm, text="Sıfırlama Bağlantısı Gönder", bootstyle="info",
                   command=send).pack(fill=X)

    # ════════════════════════════ PANO ════════════════════════════
    def show_dashboard(self):
        self._clear()

        # Üst şerit
        header = ttk.Frame(self.container, padding=(20, 14, 20, 12))
        header.pack(fill=X)
        ttk.Label(header, text=APP_TITLE, font=("Segoe UI Semibold", 16),
                  bootstyle="info").pack(side=LEFT)
        ttk.Button(header, text="Çıkış", bootstyle="secondary-outline",
                   command=self.on_logout).pack(side=RIGHT)
        ttk.Label(header, text=f"  {self.username}", bootstyle="secondary").pack(side=RIGHT, padx=(0, 8))
        ttk.Separator(self.container).pack(fill=X)

        body = ttk.Frame(self.container)
        body.pack(fill=BOTH, expand=True)

        # ── Sol ağaç menü (web paneliyle aynı düzen) ──
        side = ttk.Frame(body, padding=(12, 16, 6, 16))
        side.pack(side=LEFT, fill=Y)
        tree = ttk.Treeview(side, show="tree", selectmode="browse", height=8)
        tree.column("#0", width=210, stretch=False)
        tree.pack(fill=Y, expand=True)
        tree.insert("", END, iid="conn", text=" 🔗  UYAP Bağlantısı")
        grp = tree.insert("", END, iid="takip", text=" 📂  Takip Açılış", open=True)
        tree.insert(grp, END, iid="mts", text="MTS Takip Açılış")
        tree.insert(grp, END, iid="icra", text="XML Takip Açılış")
        grp2 = tree.insert("", END, iid="sorgular", text=" 🔎  Sorgular", open=True)
        tree.insert(grp2, END, iid="sgk", text="SGK Sorgu")
        self.nav_tree = tree

        ttk.Separator(body, orient=VERTICAL).pack(side=LEFT, fill=Y)

        # ── İçerik alanı: aynı anda tek panel görünür ──
        content = ttk.Frame(body)
        content.pack(side=LEFT, fill=BOTH, expand=True)

        self._panels = {}
        conn = ttk.Frame(content, padding=18)
        self._build_connection_tab(conn)
        self._panels["conn"] = conn

        mts = ttk.Frame(content, padding=18)
        self.mts_tab = TakipTab(self, mts, kind="mts", title="MTS Takip Açılış")
        self._panels["mts"] = mts

        icra = ttk.Frame(content, padding=18)
        self.icra_tab = TakipTab(self, icra, kind="icra", title="XML Takip Açılış")
        self._panels["icra"] = icra

        sgk = ttk.Frame(content, padding=18)
        self.sgk_tab = SgkTab(self, sgk)
        self._panels["sgk"] = sgk

        tree.bind("<<TreeviewSelect>>", self._on_nav)
        tree.selection_set("conn")
        self._show_panel("conn")

    def _on_nav(self, event=None):
        sel = self.nav_tree.selection()
        if not sel:
            return
        name = sel[0]
        if name == "takip":            # üst başlık seçilirse ilk çocuğa yönlendir
            self.nav_tree.selection_set("mts")
            return
        if name == "sorgular":
            self.nav_tree.selection_set("sgk")
            return
        self._show_panel(name)

    def _show_panel(self, name):
        for frame in self._panels.values():
            frame.pack_forget()
        self._panels[name].pack(fill=BOTH, expand=True)

    # ── Bağlantı sekmesi ──
    def _build_connection_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=X)
        top.columnconfigure(0, weight=1, uniform="conn")
        top.columnconfigure(1, weight=1, uniform="conn")

        # PAYLAŞ (ofis)
        share_c = RoundedCard(top, title="Bağlantı Paylaş (Ofis)", padding=14)
        share_c.grid(row=0, column=0, sticky=NSEW, padx=(0, 8))
        share = share_c.inner
        ttk.Label(share, text="E-imza PIN Kodu").pack(anchor=W)
        self.pin_var = ttk.StringVar(value=decrypt_secret(self.cfg.get("pin_enc", "")))
        ttk.Entry(share, textvariable=self.pin_var, show="•").pack(fill=X, pady=(2, 10))
        ttk.Label(share, text="Sertifika ID (opsiyonel)").pack(anchor=W)
        self.cert_var = ttk.StringVar(value=self.cfg.get("cert_id", ""))
        ttk.Entry(share, textvariable=self.cert_var).pack(fill=X, pady=(2, 12))
        self.share_btn = ttk.Button(share, text="Bağlantıyı Paylaş", bootstyle="success",
                                    command=self.on_share)
        self.share_btn.pack(fill=X, pady=(0, 6))
        self.share_browser_btn = ttk.Button(share, text="Tarayıcıyı Aç (Kendim Gir)",
                                            bootstyle="secondary-outline", state="disabled",
                                            command=self.open_browser)
        self.share_browser_btn.pack(fill=X)
        self.share_status = ttk.Label(share, text="● Paylaşım durduruldu", bootstyle="secondary")
        self.share_status.pack(anchor=CENTER, pady=(10, 0))

        # AL (istemci)
        recv_c = RoundedCard(top, title="Bağlantı Al (İstemci)", padding=14)
        recv_c.grid(row=0, column=1, sticky=NSEW, padx=(8, 0))
        recv = recv_c.inner
        ttk.Label(recv, text="Ofis bilgisayarına bağlanmak için kullanın. Aynı ağdaysanız "
                  "yerel ağdan, değilseniz dış ağdan otomatik bağlanır.",
                  wraplength=360, justify=LEFT, bootstyle="secondary").pack(anchor=W, pady=(0, 14))
        self.recv_btn = ttk.Button(recv, text="Bağlantıyı Al", bootstyle="success",
                                   command=self.on_receive)
        self.recv_btn.pack(fill=X, pady=(0, 6))
        self.recv_browser_btn = ttk.Button(recv, text="Tarayıcıyı Aç",
                                           bootstyle="secondary-outline", state="disabled",
                                           command=self.open_browser)
        self.recv_browser_btn.pack(fill=X)
        self.recv_status = ttk.Label(recv, text="● Bağlantı yok", bootstyle="secondary")
        self.recv_status.pack(anchor=CENTER, pady=(10, 0))

        # Günlük
        logf_c = RoundedCard(parent, title="İşlem Günlüğü", padding=12,
                             title_bootstyle="secondary")
        logf_c.pack(fill=BOTH, expand=True, pady=(14, 0))
        logf = logf_c.inner
        bar = ttk.Frame(logf)
        bar.pack(fill=X)
        ttk.Button(bar, text="Temizle", bootstyle="secondary-link",
                   command=self._clear_log).pack(side=RIGHT)
        self.log_widget = ScrolledText(logf, height=12, font=("Consolas", 9), wrap="word",
                                       background="#11151c", foreground="#d7e2ea",
                                       insertbackground="#4ea1d3", borderwidth=0)
        self.log_widget.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.log_widget.configure(state="disabled")
        # Birikmiş günlüğü ekrana bas.
        self._log_index = 0

    # ── Bağlantı eylemleri ──
    def on_share(self):
        if self.manager.is_sharing:
            self.manager.stop_sharing()
            self._refresh_conn_buttons()
            return
        pin = self.pin_var.get().strip()
        if not pin:
            Messagebox.show_error("E-imza PIN kodunu girin.", "Eksik bilgi", parent=self.window)
            return
        self.cfg["pin_enc"] = encrypt_secret(pin)
        self.cfg["cert_id"] = self.cert_var.get().strip()
        save_config(self.cfg)
        ok, msg = self.manager.start_sharing(
            server_url=self.server_url, username=self.username, password=self.password,
            pin=pin, cert_id=self.cert_var.get().strip() or None, office_code=self.office_code)
        if not ok:
            Messagebox.show_error(msg, "Paylaşım", parent=self.window)
        self._refresh_conn_buttons()

    def on_receive(self):
        if self.manager.is_receiving:
            self.manager.stop_receiving()
            self._refresh_conn_buttons()
            return
        ok, msg = self.manager.start_receiving(
            server_url=self.server_url, username=self.username, password=self.password,
            office_code=self.office_code)
        if not ok:
            Messagebox.show_error(msg, "Bağlantı", parent=self.window)
        self._refresh_conn_buttons()

    def open_browser(self):
        self.manager.open_browser()

    def _refresh_conn_buttons(self):
        if not (self.share_btn and self.share_btn.winfo_exists()):
            return
        if self.manager.is_sharing:
            self.share_btn.configure(text="Paylaşımı Durdur", bootstyle="danger")
            self.share_browser_btn.configure(state="normal")
            self.share_status.configure(text="● Paylaşım aktif", bootstyle="success")
        else:
            self.share_btn.configure(text="Bağlantıyı Paylaş", bootstyle="success")
            self.share_browser_btn.configure(state="disabled")
            self.share_status.configure(text="● Paylaşım durduruldu", bootstyle="secondary")
        if self.manager.is_receiving:
            self.recv_btn.configure(text="Bağlantıyı Kes", bootstyle="danger")
            self.recv_browser_btn.configure(state="normal")
            self.recv_status.configure(text="● Bağlantı aktif", bootstyle="success")
        else:
            self.recv_btn.configure(text="Bağlantıyı Al", bootstyle="success")
            self.recv_browser_btn.configure(state="disabled")
            self.recv_status.configure(text="● Bağlantı yok", bootstyle="secondary")

    def _clear_log(self):
        self.manager.clear_logs()
        if self.log_widget and self.log_widget.winfo_exists():
            self.log_widget.configure(state="normal")
            self.log_widget.delete("1.0", END)
            self.log_widget.configure(state="disabled")
        self._log_index = 0

    # ── günlük poll ──
    def _poll_logs(self):
        total, new = self.manager.logs_since(self._log_index)
        if new and self.log_widget and self.log_widget.winfo_exists():
            self.log_widget.configure(state="normal")
            for line in new:
                self.log_widget.insert(END, line + "\n")
            self.log_widget.see(END)
            self.log_widget.configure(state="disabled")
        self._log_index = total
        # Beklenmedik kopmada düğmeleri eşitle.
        if hasattr(self, "share_btn") and self.share_btn and self.share_btn.winfo_exists():
            self._refresh_conn_buttons()
        self.window.after(400, self._poll_logs)

    # ── çıkış / kapatma ──
    def on_logout(self):
        if Messagebox.yesno("Oturumu kapatıp çalışan tüm süreçleri durdurmak "
                            "istiyor musunuz?", "Çıkış", parent=self.window) == "Yes":
            self.manager.shutdown()
            self.username = self.password = ""
            self.show_login()

    def on_close(self):
        try:
            self.manager.shutdown()
        except Exception:
            pass
        try:
            self.window.quit()
            self.window.destroy()
        except Exception:
            pass
        os._exit(0)

    def run(self):
        self.window.mainloop()


# ════════════════════════════ TAKİP SEKMESİ (İcra & MTS ortak) ════════════════════════════
class TakipTab:
    """İcra ve MTS için ortak takip-açılış sekmesi. kind ile davranış belirlenir."""

    def __init__(self, app: UyapPanelApp, parent, kind, title):
        self.app = app
        self.kind = kind
        self.takipler = []
        self.vekalet = None
        self.dayanak = None
        self.job_id = None
        self.polling = False
        self._approval_open = False
        self._log_n = 0

        ttk.Label(parent, text=title, font=("Segoe UI Semibold", 14),
                  bootstyle="info").pack(anchor=W)
        ttk.Label(parent, text="XML/Excel seçin; takipler bu bilgisayarda ayrıştırılıp "
                  "ofisteki canlı UYAP oturumu üzerinden açılır. Onay seçtiğiniz moda göre "
                  "burada alınır.", wraplength=860, justify=LEFT,
                  bootstyle="secondary").pack(anchor=W, pady=(2, 12))

        # 1) Kaynak
        src_c = RoundedCard(parent, title="1 · Kaynak (XML / Excel)", padding=12)
        src_c.pack(fill=X)
        src = src_c.inner
        r = ttk.Frame(src); r.pack(fill=X)
        ttk.Button(r, text="Dosya Seç…", bootstyle="info-outline",
                   command=self.pick_source).pack(side=LEFT)
        self.src_lbl = ttk.Label(r, text="Seçilmedi", bootstyle="secondary")
        self.src_lbl.pack(side=LEFT, padx=12)

        # 2) Ayarlar
        cfgf_c = RoundedCard(parent, title="2 · Ayarlar", padding=12)
        cfgf_c.pack(fill=X, pady=(10, 0))
        cfgf = cfgf_c.inner
        row = ttk.Frame(cfgf); row.pack(fill=X)
        ttk.Label(row, text="İl:").pack(side=LEFT)
        self.il_var = ttk.StringVar(value=app.cfg.get("il", "İzmir"))
        ttk.Entry(row, textvariable=self.il_var, width=14).pack(side=LEFT, padx=(4, 14))
        ttk.Label(row, text="Adliye:").pack(side=LEFT)
        self.adliye_var = ttk.StringVar(value=app.cfg.get("adliye", "İzmir"))
        ttk.Entry(row, textvariable=self.adliye_var, width=14).pack(side=LEFT, padx=4)

        row2 = ttk.Frame(cfgf); row2.pack(fill=X, pady=(10, 0))
        ttk.Label(row2, text="Onay modu:").pack(side=LEFT, padx=(0, 8))
        self.onay_var = ttk.StringVar(value=app.cfg.get("onay_modu", "tek_tek"))
        for val, lbl in (("yok", "Onaysız (hepsini aç)"),
                         ("tek_tek", "Her takipte onayla"),
                         ("toplu", "Toplu önizle, tek onay")):
            ttk.Radiobutton(row2, text=lbl, value=val, variable=self.onay_var,
                            bootstyle="info").pack(side=LEFT, padx=6)

        # 2.5) Ödeme & Tebligat (yalnız MTS) — takip AÇILDIKTAN SONRA, ayrı onay turlarıyla.
        self.odeme_aktif_var = ttk.BooleanVar(value=app.cfg.get("odeme_aktif", True))
        self.odeme_onay_var = ttk.StringVar(value=app.cfg.get("odeme_onay_modu", "yok"))
        self.tebligat_aktif_var = ttk.BooleanVar(value=app.cfg.get("tebligat_aktif", False))
        self.tebligat_onay_var = ttk.StringVar(value=app.cfg.get("tebligat_onay_modu", "yok"))
        if kind == "mts":
            ot_c = RoundedCard(parent, title="3 · Ödeme & Tebligat (dosya açıldıktan SONRA)",
                               padding=12)
            ot_c.pack(fill=X, pady=(10, 0))
            ot = ot_c.inner
            row = ttk.Frame(ot); row.pack(fill=X)
            odeme_col = ttk.Frame(row); odeme_col.pack(side=LEFT, fill=X, expand=True)
            ttk.Checkbutton(odeme_col, text="Harç ödemesini dene", variable=self.odeme_aktif_var,
                            bootstyle="info").pack(anchor=W)
            for val, lbl in (("yok", "Onaysız (hepsini öde)"), ("tek_tek", "Her dosyada onayla"),
                             ("toplu", "Toplu önizle, tek onay")):
                ttk.Radiobutton(odeme_col, text=lbl, value=val, variable=self.odeme_onay_var,
                                bootstyle="info").pack(anchor=W, padx=(18, 0))
            tebligat_col = ttk.Frame(row); tebligat_col.pack(side=LEFT, fill=X, expand=True, padx=(24, 0))
            ttk.Checkbutton(tebligat_col, text="Tebligat göndermeyi dene", variable=self.tebligat_aktif_var,
                            bootstyle="info").pack(anchor=W)
            for val, lbl in (("yok", "Onaysız (hepsine gönder)"), ("tek_tek", "Her dosyada onayla"),
                             ("toplu", "Toplu önizle, tek onay")):
                ttk.Radiobutton(tebligat_col, text=lbl, value=val, variable=self.tebligat_onay_var,
                                bootstyle="info").pack(anchor=W, padx=(18, 0))

        # 3) Belgeler
        belf_c = RoundedCard(parent, title=("4" if kind == "mts" else "3") +
                             " · Belgeler (opsiyonel; tümüne uygulanır)",
                             padding=12)
        belf_c.pack(fill=X, pady=(10, 0))
        belf = belf_c.inner
        br = ttk.Frame(belf); br.pack(fill=X)
        ttk.Button(br, text="Vekalet PDF…", bootstyle="secondary-outline",
                   command=lambda: self.pick_doc("vekalet")).pack(side=LEFT)
        self.vekalet_lbl = ttk.Label(br, text="—", bootstyle="secondary")
        self.vekalet_lbl.pack(side=LEFT, padx=8)
        ttk.Button(br, text="Dayanak PDF…", bootstyle="secondary-outline",
                   command=lambda: self.pick_doc("dayanak")).pack(side=LEFT, padx=(14, 0))
        self.dayanak_lbl = ttk.Label(br, text="—", bootstyle="secondary")
        self.dayanak_lbl.pack(side=LEFT, padx=8)

        # Eylemler + ilerleme
        act = ttk.Frame(parent); act.pack(fill=X, pady=(12, 4))
        self.start_btn = ttk.Button(act, text="İşi Başlat", bootstyle="success",
                                    command=self.start)
        self.start_btn.pack(side=LEFT)
        self.cancel_btn = ttk.Button(act, text="İptal", bootstyle="danger",
                                     state="disabled", command=self.cancel)
        self.cancel_btn.pack(side=LEFT, padx=8)
        self.status_lbl = ttk.Label(act, text="", bootstyle="secondary")
        self.status_lbl.pack(side=RIGHT)

        self.progress = ttk.Progressbar(parent, bootstyle="success-striped", mode="determinate")
        self.progress.pack(fill=X, pady=(4, 6))

        self.log = ScrolledText(parent, height=9, font=("Consolas", 9), wrap="word",
                                background="#11151c", foreground="#d7e2ea", borderwidth=0)
        self.log.pack(fill=BOTH, expand=True)
        self.log.configure(state="disabled")

    # ── yardımcılar ──
    def _log_yaz(self, text):
        if self.log.winfo_exists():
            self.log.configure(state="normal")
            self.log.insert(END, text + "\n")
            self.log.see(END)
            self.log.configure(state="disabled")

    def _service(self):
        return TakipService(self.app.username, self.app.password, PORT)

    # ── kaynak / belge seçimi ──
    def pick_source(self):
        path = filedialog.askopenfilename(
            title="UYAP XML veya Excel seçin",
            filetypes=[("XML / Excel", "*.xml *.xlsx"), ("XML", "*.xml"), ("Excel", "*.xlsx")])
        if not path:
            return
        self.src_lbl.configure(text="Ayrıştırılıyor…")

        def worker():
            takipler, err = TakipService.parse_source(path, kind=self.kind)
            def show():
                if err:
                    self.takipler = []
                    self.src_lbl.configure(text="Hata!", bootstyle="danger")
                    self._log_yaz(f"[HATA] Dosya ayrıştırılamadı: {err}")
                    return
                self.takipler = takipler
                self.src_lbl.configure(text=f"{os.path.basename(path)} — {len(takipler)} takip",
                                       bootstyle="secondary")
                onek = ", ".join(str(t.get('dosya_no')) for t in takipler[:20])
                self._log_yaz(f"[BİLGİ] {len(takipler)} takip bulundu: {onek}"
                              + (" …" if len(takipler) > 20 else ""))
            self.app.window.after(0, show)
        threading.Thread(target=worker, daemon=True).start()

    def pick_doc(self, which):
        path = filedialog.askopenfilename(title=f"{which.title()} PDF seçin",
                                          filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        att = TakipService.attachment(path)
        if which == "vekalet":
            self.vekalet = att
            self.vekalet_lbl.configure(text=os.path.basename(path))
        else:
            self.dayanak = att
            self.dayanak_lbl.configure(text=os.path.basename(path))

    # ── iş yaşam döngüsü ──
    def start(self):
        if not self.takipler:
            Messagebox.show_warning("Önce bir XML/Excel kaynağı seçin.", "Eksik",
                                    parent=self.app.window)
            return
        if not self.app.manager.is_connected():
            Messagebox.show_warning("Önce 'Bağlantı' sekmesinden Paylaş ya da Al ile "
                                    "bağlantıyı başlatın.", "Bağlantı yok", parent=self.app.window)
            return
        # Ayarları hatırla.
        self.app.cfg["il"] = self.il_var.get().strip() or "İzmir"
        self.app.cfg["adliye"] = self.adliye_var.get().strip() or "İzmir"
        self.app.cfg["onay_modu"] = self.onay_var.get()
        odeme_aktif = odeme_onay_modu = tebligat_aktif = tebligat_onay_modu = None
        if self.kind == "mts":
            odeme_aktif = self.odeme_aktif_var.get()
            odeme_onay_modu = self.odeme_onay_var.get()
            tebligat_aktif = self.tebligat_aktif_var.get()
            tebligat_onay_modu = self.tebligat_onay_var.get()
            self.app.cfg.update(odeme_aktif=odeme_aktif, odeme_onay_modu=odeme_onay_modu,
                               tebligat_aktif=tebligat_aktif, tebligat_onay_modu=tebligat_onay_modu)
        save_config(self.app.cfg)

        svc = self._service()
        params = svc.build_params(self.takipler, il=self.app.cfg["il"],
                                  adliye=self.app.cfg["adliye"], onay_modu=self.onay_var.get(),
                                  odeme_aktif=odeme_aktif, odeme_onay_modu=odeme_onay_modu,
                                  tebligat_aktif=tebligat_aktif, tebligat_onay_modu=tebligat_onay_modu,
                                  vekalet=self.vekalet, dayanak=self.dayanak)
        self.start_btn.configure(state="disabled")
        self.status_lbl.configure(text="İş gönderiliyor…")
        self.progress.configure(value=0)
        self._log_yaz(f"[BİLGİ] {len(self.takipler)} takip gönderiliyor "
                      f"(mod: {self.onay_var.get()}).")

        def worker():
            job_id, err = svc.submit(self.kind, params)
            def show():
                if err:
                    self.start_btn.configure(state="normal")
                    self.status_lbl.configure(text="Başlatılamadı.")
                    self._log_yaz(f"[HATA] {err}")
                    return
                self.job_id = job_id
                self._log_n = 0
                self.polling = True
                self.cancel_btn.configure(state="normal")
                self._log_yaz(f"[BİLGİ] İş başlatıldı (id: {job_id}).")
                self._poll()
            self.app.window.after(0, show)
        threading.Thread(target=worker, daemon=True).start()

    def cancel(self):
        if not self.job_id:
            return
        jid = self.job_id
        threading.Thread(target=lambda: self._service().cancel(jid), daemon=True).start()
        self._log_yaz("[BİLGİ] İptal isteği gönderildi.")

    def _poll(self):
        if not self.polling or not self.job_id:
            return

        def worker():
            job, err = self._service().get(self.job_id)
            self.app.window.after(0, lambda: self._on_poll(job, err))
        threading.Thread(target=worker, daemon=True).start()

    def _on_poll(self, job, err):
        if not self.polling:
            return
        if err:
            self._log_yaz(f"[HATA] Durum alınamadı: {err}")
            self.app.window.after(2500, self._poll)
            return
        job = job or {}
        st = job.get("status", "?")
        prog = job.get("progress") or {}
        done, total = prog.get("done", 0), prog.get("total", 0)
        self.status_lbl.configure(text=f"Durum: {st} · {done}/{total or '?'} · {prog.get('message','')}")
        if total:
            self.progress.configure(maximum=total, value=done)

        for entry in (job.get("logs") or [])[self._log_n:]:
            self._log_yaz(entry.get("line", ""))
        self._log_n = len(job.get("logs") or [])

        if st == "awaiting_approval" and not self._approval_open:
            self._approval_dialog(job.get("pending_approval") or {})

        if st in ("done", "error", "cancelled"):
            self.polling = False
            self.start_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            res = job.get("result") or {}
            if st == "done":
                self._log_yaz(f"[BİTTİ] {res.get('basari',0)} tamam, "
                              f"{res.get('atlanan',0)} atlandı, {res.get('hata',0)} hata.")
            elif st == "error":
                self._log_yaz(f"[HATA] İş hata ile bitti: {job.get('error')}")
            else:
                self._log_yaz("[BİLGİ] İş iptal edildi.")
            return
        self.app.window.after(1500, self._poll)

    # ── onay ──
    def _send_approval(self, decision, selection=None):
        jid = self.job_id
        threading.Thread(
            target=lambda: self._service().approve(jid, decision, selection),
            daemon=True).start()

    def _approval_dialog(self, pending):
        self._approval_open = True
        mod = pending.get("mod")
        win = ttk.Toplevel(title="UYAP Takip — Onay")
        win.transient(self.app.window)
        win.geometry("660x580")
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill=BOTH, expand=True)

        def close_with(decision, selection=None):
            self._send_approval(decision, selection)
            self._approval_open = False
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", lambda: None)

        def tek_tek_dialog(title, metin, reject_label, reject_decision, approve_label):
            """Tek kalem onayı: Atla (yalnız BU AŞAMAYI atlar, öncekini geri almaz) / Onayla."""
            ttk.Label(frm, text=title, font=("Segoe UI Semibold", 13),
                      bootstyle="info").pack(anchor=W)
            txt = ScrolledText(frm, height=20, font=("Consolas", 9),
                               background="#11151c", foreground="#d7e2ea")
            txt.pack(fill=BOTH, expand=True, pady=8)
            txt.insert(END, metin)
            txt.configure(state="disabled")
            bf = ttk.Frame(frm); bf.pack(fill=X)
            ttk.Button(bf, text=reject_label, bootstyle="danger",
                       command=lambda: close_with(reject_decision)).pack(side=LEFT)
            ttk.Button(bf, text=approve_label, bootstyle="success",
                       command=lambda: close_with("approve")).pack(side=RIGHT, padx=4)
            if reject_decision != "skip":
                ttk.Button(bf, text="Bu Dosyayı Atla", bootstyle="secondary-outline",
                           command=lambda: close_with("skip")).pack(side=RIGHT, padx=4)

        def toplu_dialog(title, kalemler, satir_fn, reject_label, reject_selection):
            """Toplu önizleme: reject_selection=None -> sol buton GERÇEK iptal (takip-açma);
            [] -> sol buton 'hiçbirini yapma' (ödeme/tebligat aşaması — önceki kazanım kalır)."""
            ttk.Label(frm, text=title, font=("Segoe UI Semibold", 13),
                      bootstyle="info").pack(anchor=W)
            from ttkbootstrap.scrolled import ScrolledFrame
            sf = ScrolledFrame(frm, autohide=True)
            sf.pack(fill=BOTH, expand=True, pady=8)
            secimler = {}
            for o in kalemler:
                dn = str(o.get("dosya_no"))
                var = ttk.BooleanVar(value=True)
                secimler[dn] = var
                ttk.Checkbutton(sf, text=satir_fn(o), variable=var, bootstyle="info").pack(anchor=W, pady=1)

            def onayla():
                secili = [dn for dn, v in secimler.items() if v.get()]
                close_with("approve", selection=secili)

            def reddet():
                if reject_selection is None:
                    close_with("cancel")
                else:
                    close_with("approve", selection=reject_selection)

            bf = ttk.Frame(frm); bf.pack(fill=X)
            ttk.Button(bf, text=reject_label, bootstyle="danger", command=reddet).pack(side=LEFT)
            ttk.Button(bf, text="Seçilenlerle Devam Et", bootstyle="success",
                       command=onayla).pack(side=RIGHT, padx=4)

        if mod == "tek_tek":
            ozet = pending.get("takip") or {}
            tek_tek_dialog(f"Takip onayı — Dosya No: {ozet.get('dosya_no')}", _ozet_metni(ozet),
                           "Tümünü İptal Et", "cancel", "Onayla ve Aç")
        elif mod == "toplu":
            takipler = pending.get("takipler") or []
            toplu_dialog(f"Toplu önizleme — {len(takipler)} takip. Açılacakları seçin:", takipler,
                        lambda o: (f"Dosya {o.get('dosya_no')} · {o.get('alacakli','')} · "
                                  f"{len(o.get('borclular',[]))} borçlu · Toplam {o.get('toplam',0)} TL"),
                        "Tümünü İptal Et", None)
        elif mod == "odeme_tek_tek":
            kalem = pending.get("kalem") or {}
            tek_tek_dialog(f"Ödeme onayı — Dosya No: {kalem.get('dosya_no')}", _harc_metni(kalem),
                           "Tümünü İptal Et (kalanları durdur)", "cancel", "Onayla")
        elif mod == "odeme_toplu":
            kalemler = pending.get("kalemler") or []
            toplu_dialog(f"Toplu ödeme önizleme — {len(kalemler)} dosya. Ödenecekleri seçin:", kalemler,
                        lambda o: (f"Dosya {o.get('dosya_no')} · {o.get('alacakli','')} · "
                                  f"Harç toplamı {o.get('toplam_harc',0)} TL"),
                        "Hiçbirini Ödeme", [])
        elif mod == "tebligat_tek_tek":
            kalem = pending.get("kalem") or {}
            tek_tek_dialog(f"Tebligat onayı — Dosya No: {kalem.get('dosya_no')}", _tebligat_metni(kalem),
                           "Tümünü İptal Et (kalanları durdur)", "cancel", "Onayla")
        elif mod == "tebligat_toplu":
            kalemler = pending.get("kalemler") or []
            toplu_dialog(f"Toplu tebligat önizleme — {len(kalemler)} dosya. Gönderilecekleri seçin:", kalemler,
                        lambda o: f"Dosya {o.get('dosya_no')} · {o.get('alacakli','')} · {len(o.get('borclular',[]))} taraf",
                        "Hiçbirine Gönderme", [])
        else:
            ttk.Label(frm, text=f"Bilinmeyen onay türü: {mod}", bootstyle="danger").pack(anchor=W)
            ttk.Button(frm, text="Kapat", bootstyle="secondary",
                       command=lambda: close_with("cancel")).pack(anchor=W, pady=8)


def _ozet_metni(ozet):
    s = ["=" * 60,
         f"DOSYA NO   : {ozet.get('dosya_no')}",
         f"ALACAKLI   : {ozet.get('alacakli')}",
         f"IBAN       : {ozet.get('iban')}",
         f"ABONE NO   : {ozet.get('abone_no')}",
         "=" * 60, "BORÇLULAR:"]
    for i, b in enumerate(ozet.get("borclular", []), 1):
        s.append(f"  {i}. {b.get('ad')} {b.get('soyad')} (TC: {b.get('kimlik')})")
    s.append("=" * 60)
    s.append("ALACAK KALEMLERİ:")
    for i, k in enumerate(ozet.get("kalemler", []), 1):
        s.append(f"  {i}. {k.get('ad')}: {k.get('tutar')} TL")
    s.append(f"TOPLAM ALACAK : {ozet.get('toplam')} TL")
    s.append("=" * 60)
    return "\n".join(s)


def _harc_metni(kalem):
    s = [f"DOSYA NO   : {kalem.get('dosya_no')}", f"ALACAKLI   : {kalem.get('alacakli')}",
         "-" * 40, "HARÇ/MASRAF:"]
    for h in kalem.get("harclar", []) or []:
        s.append(f"  · {h.get('ad')}: {h.get('miktar', 0)} TL")
    s.append("-" * 40)
    s.append(f"TOPLAM     : {kalem.get('toplam_harc', 0)} TL")
    return "\n".join(s)


def _tebligat_metni(kalem):
    s = [f"DOSYA NO   : {kalem.get('dosya_no')}", f"ALACAKLI   : {kalem.get('alacakli')}",
         "-" * 40, "TARAFLAR:"]
    for i, b in enumerate(kalem.get("borclular", []) or [], 1):
        s.append(f"  {i}. {b.get('ad')} {b.get('soyad')}")
    return "\n".join(s)


# ════════════════════════════ SGK SORGU ════════════════════════════
class SgkTab:
    """SGK toplu sorgu paneli. core.SgkBatch motorunu kullanır; durumu
    snapshot() ile periyodik okuyup tabloyu/günlüğü tazeler (poll deseni)."""

    def __init__(self, app: UyapPanelApp, parent):
        self.app = app
        self.batch = None
        self._seq = 0
        self._logn = 0
        self._iid_row = {}     # excel_r -> tree iid
        self._row_tam = {}     # excel_r -> [tam metin]
        self._poll_id = None

        ttk.Label(parent, text="SGK Sorgu", font=("Segoe UI Semibold", 14),
                  bootstyle="info").pack(anchor=W)
        ttk.Label(parent, text="Excel yükleyin (A: No · B: Ad Soyad · C: Birim · D: Dosya "
                  "Yıl/No). Sonuçlar dosyanın yanındaki '*_yapilanlar.xlsx' ve özet "
                  "dosyasına yazılır; varsa kaldığı yerden devam eder. Ofis bağlantısı "
                  "(Paylaş/Al) açık olmalı.", wraplength=900, justify=LEFT,
                  bootstyle="secondary").pack(anchor=W, pady=(2, 10))

        # Araç çubuğu
        bar = ttk.Frame(parent); bar.pack(fill=X)
        ttk.Button(bar, text="📂 Excel Yükle", bootstyle="info-outline",
                   command=self.yukle).pack(side=LEFT)
        self.start_btn = ttk.Button(bar, text="▶ Sorgula", bootstyle="success",
                                    state="disabled", command=lambda: self.basla("normal"))
        self.start_btn.pack(side=LEFT, padx=4)
        self.retry_btn = ttk.Button(bar, text="↻ Hatalıları Tekrar Dene",
                                    bootstyle="warning-outline", state="disabled",
                                    command=lambda: self.basla("retry"))
        self.retry_btn.pack(side=LEFT, padx=4)
        self.pause_btn = ttk.Button(bar, text="⏸ Duraklat", bootstyle="secondary-outline",
                                    state="disabled", command=self.duraklat_toggle)
        self.pause_btn.pack(side=LEFT, padx=4)
        self.stop_btn = ttk.Button(bar, text="⏹ Durdur", bootstyle="danger-outline",
                                   state="disabled", command=self.durdur)
        self.stop_btn.pack(side=LEFT, padx=4)
        self.dosya_lbl = ttk.Label(bar, text="Dosya seçilmedi", bootstyle="secondary")
        self.dosya_lbl.pack(side=LEFT, padx=10)
        self.durum_lbl = ttk.Label(bar, text="", bootstyle="secondary")
        self.durum_lbl.pack(side=RIGHT)

        # Sorgulanacak sütunlar
        sb_c = RoundedCard(parent, title="Sorgulanacak sütunlar", padding=10)
        sb_c.pack(fill=X, pady=(10, 0))
        sb = sb_c.inner
        self.secili = {}
        for baslik, _, anahtar in SORGULAR:
            var = ttk.BooleanVar(value=True)
            self.secili[anahtar] = var
            ttk.Checkbutton(sb, text=baslik, variable=var, bootstyle="info-round-toggle").pack(
                side=LEFT, padx=5)

        # Filtre
        fb = ttk.Frame(parent); fb.pack(fill=X, pady=(10, 0))
        ttk.Label(fb, text="Filtre:").pack(side=LEFT)
        self.filtre_kol = ttk.Combobox(fb, state="readonly", width=15,
                                       values=["Tüm Sütunlar"] + KOLONLAR)
        self.filtre_kol.set("Tüm Sütunlar"); self.filtre_kol.pack(side=LEFT, padx=4)
        self.filtre_txt = ttk.Entry(fb, width=22); self.filtre_txt.pack(side=LEFT, padx=4)
        self.filtre_txt.bind("<Return>", lambda e: self.filtrele())
        ttk.Button(fb, text="Uygula", bootstyle="secondary",
                   command=self.filtrele).pack(side=LEFT, padx=2)
        ttk.Button(fb, text="Temizle", bootstyle="secondary-outline",
                   command=self.filtre_temizle).pack(side=LEFT, padx=2)
        self.olumlu_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(fb, text="Sadece SGK olumlu", variable=self.olumlu_var,
                        bootstyle="success-round-toggle",
                        command=self.filtrele).pack(side=LEFT, padx=12)
        self.filtre_bilgi = ttk.Label(fb, text="", bootstyle="secondary")
        self.filtre_bilgi.pack(side=RIGHT)

        # Tablo + detay
        pan = ttk.Panedwindow(parent, orient="horizontal")
        pan.pack(fill=BOTH, expand=True, pady=(10, 0))
        sol = ttk.Frame(pan)
        self.tree = ttk.Treeview(sol, columns=KOLONLAR, show="headings", height=12)
        for k in KOLONLAR:
            self.tree.heading(k, text=k)
            gen = 190 if k in ("Ad Soyad", "Birim") else 150
            if k == "No":
                gen = 50
            elif k == "Dosya No":
                gen = 90
            self.tree.column(k, width=gen, anchor=W)
        ysb = ttk.Scrollbar(sol, orient=VERTICAL, command=self.tree.yview)
        xsb = ttk.Scrollbar(sol, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky=NSEW)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky=EW)
        sol.rowconfigure(0, weight=1); sol.columnconfigure(0, weight=1)
        self.tree.tag_configure("tamam", background="#16321d")
        self.tree.tag_configure("sirket", background="#2a2f38")
        self.tree.tag_configure("hata", background="#3a1d20")
        self.tree.bind("<ButtonRelease-1>", self._hucre_tikla)
        pan.add(sol, weight=4)

        sag = ttk.Frame(pan)
        ttk.Label(sag, text="Hücre Detayı (tıklayın)", bootstyle="secondary").pack(anchor=W)
        self.detay_txt = ScrolledText(sag, width=42, wrap="word", font=("Consolas", 9),
                                      background="#11151c", foreground="#d7e2ea", borderwidth=0)
        self.detay_txt.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.detay_txt.configure(state="disabled")
        pan.add(sag, weight=2)

        # Günlük
        self.log = ScrolledText(parent, height=6, font=("Consolas", 9), wrap="word",
                                background="#11151c", foreground="#d7e2ea", borderwidth=0)
        self.log.pack(fill=X, pady=(10, 0))
        self.log.configure(state="disabled")

    # ── yardımcılar ──
    def _log_yaz(self, satirlar):
        if not satirlar:
            return
        self.log.configure(state="normal")
        for s in satirlar:
            self.log.insert(END, s + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")

    def _secili_set(self):
        return {a for a, v in self.secili.items() if v.get()}

    def _butonlar(self, calisiyor):
        st_run = "disabled" if calisiyor else "normal"
        st_act = "normal" if calisiyor else "disabled"
        self.start_btn.configure(state=st_run)
        self.retry_btn.configure(state=st_run)
        self.pause_btn.configure(state=st_act, text="⏸ Duraklat")
        self.stop_btn.configure(state=st_act)

    # ── yükleme ──
    def yukle(self):
        if self.batch and self.batch.running:
            Messagebox.show_warning("Önce sorguyu durdurun.", "Çalışıyor", parent=self.app.window)
            return
        path = filedialog.askopenfilename(
            title="Excel dosyası seç",
            filetypes=[("Excel", "*.xlsx *.xlsm"), ("Tümü", "*.*")])
        if not path:
            return
        try:
            self.batch = SgkBatch(path, port=PORT)
            rows = self.batch.yukle()
        except Exception as e:
            Messagebox.show_error(f"Excel açılamadı:\n{e}", "Hata", parent=self.app.window)
            self.batch = None
            return
        self._seq = 0
        self._logn = 0
        self.dosya_lbl.configure(text=f"{os.path.basename(path)} — {len(rows)} satır")
        self._tabloyu_doldur(rows)
        self.start_btn.configure(state="normal")
        self.retry_btn.configure(state="normal")
        if self._poll_id is None:   # poll döngüsünü yalnızca bir kez başlat
            self._poll()

    def _tabloyu_doldur(self, rows):
        self.tree.delete(*self.tree.get_children())
        self._iid_row = {}
        self._row_tam = {}
        for row in rows:
            self._satir_yaz(row)

    def _satir_yaz(self, row):
        r = row["r"]
        degerler = [row["no"], row["ad"], row["birim"], row["dosya"]] + row["sonuc"]
        etiket = (row["durum"],) if row["durum"] in ("tamam", "sirket", "hata") else ()
        iid = self._iid_row.get(r)
        if iid is None:
            iid = self.tree.insert("", END, values=degerler, tags=etiket)
            self._iid_row[r] = iid
        else:
            self.tree.item(iid, values=degerler, tags=etiket)
        self._row_tam[r] = row["tam"]
        self._satiri_filtrele(iid)

    # ── çalıştırma ──
    def basla(self, mode):
        if not self.batch:
            return
        if not self.app.manager.is_connected():
            Messagebox.show_warning("Önce 'UYAP Bağlantısı'ndan Paylaş ya da Al ile bağlantıyı "
                                    "başlatın.", "Bağlantı yok", parent=self.app.window)
            return
        secili = self._secili_set()
        if not secili:
            Messagebox.show_warning("En az bir sorgu sütunu seçin.", "Eksik", parent=self.app.window)
            return
        if mode == "retry" and self.batch.hata_sayisi(secili) == 0:
            Messagebox.show_info("Tekrar denenecek hatalı hücre yok.", "Bilgi", parent=self.app.window)
            return
        if self.batch.basla(secili, mode=mode):
            self._butonlar(calisiyor=True)

    def duraklat_toggle(self):
        if not self.batch or not self.batch.running:
            return
        if self.batch.paused:
            self.batch.devam()
            self.pause_btn.configure(text="⏸ Duraklat")
        else:
            self.batch.duraklat()
            self.pause_btn.configure(text="▶ Devam")

    def durdur(self):
        if self.batch:
            self.batch.durdur()

    # ── poll: snapshot oku, arayüzü tazele ──
    def _poll(self):
        if self.batch:
            snap = self.batch.snapshot(since_seq=self._seq, since_log=self._logn)
            self._seq = snap["seq"]
            self._logn = snap["total_logs"]
            self._log_yaz(snap["logs"])
            for row in snap["updates"]:
                self._satir_yaz(row)
            self.durum_lbl.configure(text=snap["status"])
            if snap["bitti"] and (self.stop_btn["state"] != "disabled"):
                self._butonlar(calisiyor=False)
        self._poll_id = self.app.window.after(700, self._poll)

    # ── tıklama → detay ──
    def _hucre_tikla(self, event):
        iid = self.tree.identify_row(event.y)
        kol = self.tree.identify_column(event.x)
        if not iid or not kol:
            return
        try:
            ki = int(kol.replace("#", "")) - 1
        except ValueError:
            return
        r = next((rr for rr, ii in self._iid_row.items() if ii == iid), None)
        baslik = KOLONLAR[ki] if 0 <= ki < len(KOLONLAR) else ""
        if ki >= 4 and r is not None:
            deger = (self._row_tam.get(r) or [""] * len(SORGULAR))[ki - 4]
        else:
            deger = self.tree.set(iid, baslik) if baslik else ""
        self.detay_txt.configure(state="normal")
        self.detay_txt.delete("1.0", END)
        self.detay_txt.insert("1.0", f"【 {baslik} 】\n\n{deger}")
        self.detay_txt.configure(state="disabled")

    # ── filtre ──
    def _satir_metni(self, iid, kol=None):
        vals = self.tree.item(iid, "values")
        if kol and kol != "Tüm Sütunlar":
            idx = KOLONLAR.index(kol)
            return str(vals[idx]).lower() if idx < len(vals) else ""
        return " ".join(str(v) for v in vals).lower()

    def _satir_olumlu(self, iid):
        vals = self.tree.item(iid, "values")
        for i, anahtar in enumerate(ANAHTARLAR):
            if not self.secili[anahtar].get():
                continue
            v = str(vals[4 + i]).strip().lower() if 4 + i < len(vals) else ""
            if v.startswith("olumlu"):
                return True
        return False

    def _gorunur_mu(self, iid):
        if self.olumlu_var.get() and not self._satir_olumlu(iid):
            return False
        ara = self.filtre_txt.get().strip().lower()
        if ara and ara not in self._satir_metni(iid, self.filtre_kol.get()):
            return False
        return True

    def _satiri_filtrele(self, iid):
        if self._gorunur_mu(iid):
            self.tree.reattach(iid, "", END)
        else:
            self.tree.detach(iid)

    def filtrele(self):
        toplam = len(self._iid_row)
        gor = 0
        for iid in list(self._iid_row.values()):
            self.tree.detach(iid)
        for iid in list(self._iid_row.values()):
            if self._gorunur_mu(iid):
                self.tree.reattach(iid, "", END)
                gor += 1
        aktif = self.filtre_txt.get().strip() or self.olumlu_var.get()
        self.filtre_bilgi.configure(text=f"{gor} / {toplam} satır" if aktif else "")

    def filtre_temizle(self):
        self.filtre_txt.delete(0, END)
        self.filtre_kol.set("Tüm Sütunlar")
        self.olumlu_var.set(False)
        self.filtrele()


def main():
    UyapPanelApp().run()


if __name__ == "__main__":
    main()
