# -*- coding: utf-8 -*-
"""
UYAP Çalışma Paneli — kabuk (shell)
===================================
Tek, büyük ve sakin bir arayüz. Bu dosya YALNIZCA görsel kabuktur:
sol gezinme + üst başlık + içerik alanı + durum çubuğu. Modüllerin kendi
çalışma mantığı burada YOK; her modül ileride kendi orijinal koduyla bu
kabuğun içine "içerik çerçevesi" olarak gömülecek.

Tasarım ilkesi: saatlerce bakılabilecek, rahat ve huzurlu bir yüzey.
- Ilık kâğıt tonu zemin, yumuşak ama renkli vurgular (adaçayı / kil / mavi / altın)
- İnce saç-çizgi ayraçlar, hafif gölgeli yuvarlak kartlar
- Neon/parlama yok, pastel bombardımanı yok, cart renk yok

Çalıştırma:  python panel.py
Kendi kendine test:  python panel.py --selftest
"""
#import playwright

import os
import sys

# ── Açılış akış güvenliği (EN BAŞTA olmalı) ──────────────────────────────────
# Buradaki açılış print/log'ları panel kurulmadan ÖNCE, sistem konsolunda koşar.
# İki ölümcül durum: (1) Türkçe Windows konsolu cp1254'tür ve emoji'yi (ℹ️ ✓ ⚠️ …)
# kodlayamaz → ilk emoji'li print paneli daha açılmadan UnicodeEncodeError ile
# öldürür ("açılır açılmaz kapanır"). (2) pythonw ile (kısayol/.bat, penceresiz)
# açılışta sys.stdout/err = None olur → print yine çöker. İkisini de güvene al.
for _ad in ("stdout", "stderr"):
    _akis = getattr(sys, _ad, None)
    try:
        if _akis is None:
            setattr(sys, _ad, open(os.devnull, "w", encoding="utf-8"))
        else:
            _akis.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import font as tkfont

from theme import C, round_rect, RoundButton, accent as _accent
from modules.baglanti import BaglantiPanel
from modules.udf import UdfPanel
from modules.sgk import SgkPanel
from modules.icra_dosyalarim import IcraDosyalarimPanel
from modules.mts_takip import MtsTakipPanel
from modules.logger import LoggerPanel
from modules.ayarlar import AyarlarPanel, auto_connect_mode
from modules.senkron_kapsami import SenkronKapsamiPanel
from modules.dosyalarim_genel import DosyalarimGenelPanel
from modules.uretilmis_runner import UretilmisRunner
from modules.magaza import MagazaPanel
from modules import logger_core
from modules import magaza_core
from modules import dosya_core
from modules.tray import SystemTray


# ─────────────────────────────────────────────────────────────────────────────
# Sol gezinme — katlanır ağaç (yaprak satırı + grup satırı)
# ─────────────────────────────────────────────────────────────────────────────
class LeafRow(tk.Frame):
    """Tıklanınca panel açan yaprak satırı (hover + seçili durumu)."""
    def __init__(self, parent, app, key, text, accent, depth):
        super().__init__(parent, bg=C.SIDEBAR, cursor="hand2")
        self.app, self.key, self.accent = app, key, accent
        self.selected = False

        self.bar = tk.Frame(self, bg=C.SIDEBAR, width=3)
        self.bar.pack(side="left", fill="y")

        body = tk.Frame(self, bg=C.SIDEBAR)
        body.pack(side="left", fill="both", expand=True,
                  padx=(13 + depth * 14, 12), pady=3)

        self.dot = tk.Canvas(body, width=10, height=10, bg=C.SIDEBAR,
                             highlightthickness=0)
        self.dot.create_oval(2, 2, 8, 8, fill=accent, outline="")
        self.dot.pack(side="left", padx=(0, 10))

        self.lbl = tk.Label(body, text=text, bg=C.SIDEBAR, fg=C.INK,
                            font=app.f_nav, anchor="w")
        self.lbl.pack(side="left", fill="x", expand=True)

        for w in (self, body, self.dot, self.lbl):
            w.bind("<Button-1>", lambda e: app.select(key))
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _paint(self, bg, fg, bar):
        self.config(bg=bg)
        self.lbl.master.config(bg=bg)
        self.lbl.config(bg=bg, fg=fg)
        self.dot.config(bg=bg)
        self.bar.config(bg=bar)

    def _hover(self, on):
        if self.selected:
            return
        self._paint(C.CARD if on else C.SIDEBAR,
                    C.INK if on else C.INK, C.SIDEBAR)

    def set_selected(self, on):
        self.selected = on
        if on:
            self._paint(C.SAGE_TINT, C.SAGE_DK, self.accent)
            self.lbl.config(font=self.app.f_nav_sel)
        else:
            self._paint(C.SIDEBAR, C.INK, C.SIDEBAR)
            self.lbl.config(font=self.app.f_nav)


class GroupRow(tk.Frame):
    """Katlanır grup başlığı: renkli top + belirgin başlık + sağda chevron.
    Tıklanınca alt öğelerini gösterir/gizler (yaprak satırlarıyla aynı belirginlik)."""
    def __init__(self, parent, app, text, depth, accent, kids_frame, expanded=True):
        super().__init__(parent, bg=C.SIDEBAR, cursor="hand2")
        self.app, self.kids = app, kids_frame
        self.expanded = expanded

        tk.Frame(self, bg=C.SIDEBAR, width=3).pack(side="left", fill="y")
        body = tk.Frame(self, bg=C.SIDEBAR)
        body.pack(side="left", fill="both", expand=True,
                  padx=(13 + depth * 14, 12), pady=3)

        # başlık adından önce renkli top (yapraklarla aynı)
        self.dot = tk.Canvas(body, width=10, height=10, bg=C.SIDEBAR,
                             highlightthickness=0)
        self.dot.create_oval(2, 2, 8, 8, fill=accent, outline="")
        self.dot.pack(side="left", padx=(0, 10))

        self.lbl = tk.Label(body, text=text, bg=C.SIDEBAR, fg=C.INK,
                            font=app.f_nav, anchor="w")
        self.lbl.pack(side="left", fill="x", expand=True)

        # katlama göstergesi (sağda, sade)
        self.chev = tk.Label(body, text="▾" if expanded else "▸", bg=C.SIDEBAR,
                             fg=C.INK_FAINT, font=app.f_small)
        self.chev.pack(side="right")

        for w in (self, body, self.dot, self.lbl, self.chev):
            w.bind("<Button-1>", lambda e: self.toggle())
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, on):
        bg = C.CARD if on else C.SIDEBAR
        for w in (self, self.lbl.master, self.dot, self.lbl, self.chev):
            w.config(bg=bg)

    def toggle(self):
        self.expanded = not self.expanded
        self.chev.config(text="▾" if self.expanded else "▸")
        if self.expanded:
            self.kids.pack(fill="x")
        else:
            self.kids.pack_forget()


class SlimScrollbar(tk.Canvas):
    """İnce, temaya uygun, yalnızca gerektiğinde beliren kaydırma çubuğu.
    tk.Scrollbar arabirimini taklit eder (command + set), böylece canvas'a
    doğrudan bağlanır. İçerik sığdığında kendini gizler (place_forget)."""

    def __init__(self, parent, command, width=6):
        super().__init__(parent, width=width + 4, bg=C.SIDEBAR,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._command = command            # canvas.yview
        self._w = width
        self._first, self._last = 0.0, 1.0
        self._hover = False
        self._drag = None
        self._visible = False
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    # canvas yscrollcommand -> buraya (first, last) gelir
    def set(self, first, last):
        if not self.winfo_exists():       # yıkım sırasında gelen geç çağrıyı yut
            return
        first, last = float(first), float(last)
        self._first, self._last = first, last
        need = not (first <= 0.0 and last >= 1.0)
        if need and not self._visible:
            self._visible = True
            self.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0)
        elif not need and self._visible:
            self._visible = False
            self.place_forget()
        if self._visible:
            self._redraw()

    def _set_hover(self, on):
        self._hover = on
        self._redraw()

    def _thumb(self, h):
        top, bot = self._first * h, self._last * h
        if bot - top < 28:                 # asgari tutamaç boyu
            mid = (top + bot) / 2
            top, bot = mid - 14, mid + 14
            top = max(0, min(top, h - 28))
            bot = top + 28
        return top, bot

    def _redraw(self):
        if not self.winfo_exists():       # widget yıkıldıysa geç (callback yarışı)
            return
        self.delete("all")
        h = self.winfo_height()
        w = int(self["width"])
        if h < 4:
            return
        top, bot = self._thumb(h)
        col = C.INK_SOFT if self._hover else C.INK_FAINT
        x1, x2 = w - self._w - 1, w - 1
        r = self._w / 2
        # yuvarlak uçlu ince tutamaç
        self.create_oval(x1, top, x2, top + self._w, fill=col, outline="")
        self.create_oval(x1, bot - self._w, x2, bot, fill=col, outline="")
        self.create_rectangle(x1, top + r, x2, bot - r, fill=col, outline="")

    def _press(self, e):
        h = self.winfo_height()
        top, bot = self._thumb(h)
        if top <= e.y <= bot:
            self._drag = e.y - top         # tutamaç içinde kavrama noktası
        else:                              # boşluğa tıkla → o noktaya atla
            self._command("moveto", max(0.0, min(1.0, e.y / h)))
            self._drag = (bot - top) / 2
            self._motion(e)

    def _motion(self, e):
        if self._drag is None:
            return
        h = self.winfo_height()
        frac = (e.y - self._drag) / h
        self._command("moveto", max(0.0, min(1.0, frac)))


# ─────────────────────────────────────────────────────────────────────────────
# Sağ-alt hızlı menü (yüzen toplar) — ana topa tıkla → 4 renkli kısayol açılır
# ─────────────────────────────────────────────────────────────────────────────
class FabMenu:
    """Pencerenin sağ-alt köşesinde yüzen hızlı menü. Ana top tema rengindedir;
    tıklayınca animasyonla 4 ayrı renkte kısayol topu yukarı doğru açılır.
    Kısayol eylemleri şimdilik YER TUTUCUDUR (durum çubuğuna yazar)."""

    ITEM_COLORS = [C.BLUE, C.GOLD, C.CLAY, "#9B8AC4"]   # 4 ayrı renk (anadan farklı)
    ITEM_GLYPHS = ["①", "②", "③", "④"]
    ITEM_LABELS = ["Kısayol 1", "Kısayol 2", "Kısayol 3", "Kısayol 4"]

    MAIN = 54        # ana top çapı
    ITEM = 44        # kısayol topu çapı
    BASE_Y = 44      # alt kenardan ana topun yüksekliği (durum çubuğunun üstünde)

    def __init__(self, app):
        self.app = app
        self.opened = False
        self._anim = None
        self.f_main = tkfont.Font(family="Segoe UI", size=22)
        self.f_item = tkfont.Font(family="Segoe UI", size=16)

        self.items = []
        for i in range(4):
            b = self._ball(self.ITEM, self.ITEM_COLORS[i], self.ITEM_GLYPHS[i],
                           self.f_item)
            b.bind("<Button-1>", lambda e, idx=i: self._on_item(idx))
            self.items.append(b)

        self.main = self._ball(self.MAIN, C.SAGE, "＋", self.f_main)
        self._main_glyph = "＋"
        self.main.bind("<Button-1>", self._toggle)
        self.main.place(relx=1.0, rely=1.0, x=-24, y=-self.BASE_Y, anchor="se")

        # dışarı tıkla / ESC ile kapat (yıkılmışsa sessiz geç)
        app.bind("<Button-1>", self._maybe_close, add="+")
        app.bind("<Escape>", lambda e: self.close(), add="+")

    def _ball(self, size, color, glyph, font):
        parent = getattr(self.app, "app_container", self.app)
        cv = tk.Canvas(parent, width=size, height=size, bg=C.BG,
                       highlightthickness=0, bd=0, cursor="hand2")
        cv.create_oval(3, 5, size - 1, size, fill=C.SHADOW, outline="")   # yumuşak gölge
        cv.create_oval(1, 1, size - 3, size - 3, fill=color, outline="")
        cv._txt = cv.create_text((size - 2) / 2, (size - 2) / 2, text=glyph,
                                 fill="#FFFFFF", font=font)
        return cv

    def _on_item(self, idx):
        # YER TUTUCU eylem — ileride buraya gerçek kısayol bağlanır
        try:
            if hasattr(self.app, "status") and self.app.status.winfo_exists():
                self.app.status.config(text=f"{self.ITEM_LABELS[idx]} (yer tutucu) seçildi")
        except Exception:
            pass
        self.close()

    def _toggle(self, _e=None):
        self.close() if self.opened else self.open()

    def open(self):
        if self.opened or not self.main.winfo_exists():
            return
        self.opened = True
        self.main.itemconfig(self.main._txt, text="×")
        for b in self.items:
            tk.Misc.lift(b)
        tk.Misc.lift(self.main)
        self._animate(True)

    def close(self):
        if not self.opened or not self.main.winfo_exists():
            return
        self.opened = False
        try:
            self.main.itemconfig(self.main._txt, text="＋")
        except Exception:
            pass
        self._animate(False)

    def _target_y(self, i):
        # ana topun üstüne istiflenir (BASE_Y + ana çap + boşluk + i*aralık)
        return self.BASE_Y + self.MAIN + 8 + i * (self.ITEM + 8)

    def _animate(self, opening):
        if self._anim:
            try:
                self.app.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None
        steps = 8

        def step(n):
            if not self.main.winfo_exists():
                return
            t = n / steps
            ease = 1 - (1 - t) ** 3                 # yumuşak yavaşlama
            prog = ease if opening else (1 - ease)
            for i, b in enumerate(self.items):
                y = self.BASE_Y + (self._target_y(i) - self.BASE_Y) * prog
                b.place(relx=1.0, rely=1.0, x=-29, y=-y, anchor="se")
            if n < steps:
                self._anim = self.app.after(16, lambda: step(n + 1))
            else:
                self._anim = None
                if not opening:
                    for b in self.items:
                        b.place_forget()
        step(1)

    def _maybe_close(self, e):
        if not self.opened or not self.main.winfo_exists():
            return
        if e.widget is self.main or e.widget in self.items:
            return
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Ana uygulama
# ─────────────────────────────────────────────────────────────────────────────
class Panel(tk.Tk):
    # Çekirdek yapraklar — HER ZAMAN menüde (kullanıcı UYAP oturum özelliğini
    # default alır; Mağaza ile diğer özellikleri etkinleştirir; Ayarlar). Menünün
    # ALTINA eklenir; satın alınan app'ler üstteki gruplara yerleşir.
    CEKIRDEK_NAV = [
        {"key": "baglanti", "label": "UYAP Bağlantısı", "accent": C.BLUE},
        {"key": "magaza", "label": "Uygulama Mağazası", "accent": C.SAGE},
        {"key": "dosyalarim_genel", "label": "Dosyalarım (Tümü)", "accent": C.SAGE},
        {"key": "senkron_kapsami", "label": "Senkron Kapsamı", "accent": C.CLAY},
        {"key": "ayarlar", "label": "Ayarlar", "accent": C.INK_FAINT},
    ]

    # Menü gruplarının (katlanır başlık) rengi — katalogdaki menu_yolu adlarına göre.
    GRUP_ACCENT = {
        "Dosyalarım": C.SAGE, "İcra": C.CLAY, "Toplu Takip Aç": C.SAGE,
        "Araçlar": C.GOLD, "Raporlar": C.BLUE, "Üretilen Modüller": C.GOLD,
    }

    def _grup_accent(self, ad):
        return self.GRUP_ACCENT.get(ad, C.GOLD)

    # ── Sahip olunan app'lerden dinamik gezinme ağacı kur ──
    def _yaprak_yerlestir(self, nav, yol, yaprak):
        """`yol` (grup adları, üstten alta) boyunca bul-veya-oluştur ilerleyip
        `yaprak`ı en alttaki grubun çocuklarına ekler (kopyasını)."""
        dugumler = nav
        for grup_ad in yol:
            hedef = next((n for n in dugumler
                          if n.get("label") == grup_ad and "children" in n), None)
            if hedef is None:
                hedef = {"label": grup_ad, "accent": self._grup_accent(grup_ad),
                         "children": []}
                dugumler.append(hedef)
            dugumler = hedef["children"]
        if not any(n.get("key") == yaprak.get("key") for n in dugumler):
            # Katalog accent'i bir İSİMDİR ("sage" vb.); tkinter için hex'e çöz.
            kopya = dict(yaprak)
            kopya["accent"] = _accent(kopya.get("accent"))
            dugumler.append(kopya)

    def _aktif_nav(self):
        """Sol menü ağacı = sahip olunan app yaprakları (üstte, gruplara yerleşir)
        + çekirdek üçlü (altta, her zaman). Sahiplik magaza_core'dan okunur."""
        nav = []
        sahip = magaza_core.sahip_olunanlar()
        for app in magaza_core.katalog():
            if app["kimlik"] not in sahip:
                continue
            for yaprak in app.get("yapraklar", []):
                self._yaprak_yerlestir(nav, app.get("menu_yolu", []), yaprak)
        for leaf in self.CEKIRDEK_NAV:
            nav.append(dict(leaf))
        return nav

    def _tum_etiketler(self):
        """Yaprak anahtarı -> etiket. Çekirdek + katalogdaki TÜM app yaprakları
        (sahip olunmasa da; başlık/durum çubuğu için)."""
        out = {l["key"]: l["label"] for l in self.CEKIRDEK_NAV}
        for app in magaza_core.katalog():
            for y in app.get("yapraklar", []):
                out[y["key"]] = y["label"]
        return out

    def __init__(self):
        super().__init__()
        self.title("UYAP Çalışma Paneli")
        self._drag_x = 0
        self._drag_y = 0
        self._setting_taskbar_icon = False

        # Sürükle-bırak (tkdnd) — yalnızca kuruluysa. UDF "İmzala" kartında
        # .udf dosyasının pencereye bırakılmasını sağlar; yoksa tıkla-seç çalışır.
        self.dnd_ok = False
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            TkinterDnD._require(self)
            self.DND_FILES = DND_FILES
            self.dnd_ok = True
        except Exception:
            self.dnd_ok = False

        # yazı tipleri
        self.f_h1     = tkfont.Font(family="Segoe UI Semibold", size=20)
        self.f_sub    = tkfont.Font(family="Segoe UI", size=11)
        self.f_grp    = tkfont.Font(family="Segoe UI Semibold", size=8)
        self.f_nav    = tkfont.Font(family="Segoe UI", size=11)
        self.f_nav_b  = tkfont.Font(family="Segoe UI Semibold", size=9)
        self.f_nav_sel= tkfont.Font(family="Segoe UI Semibold", size=11)
        self.f_card_t = tkfont.Font(family="Segoe UI Semibold", size=13)
        self.f_card_d = tkfont.Font(family="Segoe UI", size=10)
        self.f_body   = tkfont.Font(family="Segoe UI", size=11)
        self.f_small  = tkfont.Font(family="Segoe UI", size=9)
        self.f_mono   = tkfont.Font(family="Consolas", size=9)

        self.nav_leaves = {}
        self.labels = self._tum_etiketler()        # çekirdek + katalog yaprak etiketleri
        self.panels = {}
        self.current = None
        self.username = ""
        self._password = ""
        self.log_queue = queue.Queue()   # modüllerin (ör. Bağlantı) ortak günlüğü
        self._stdout_redirected = False

        # ── Giriş (login) altyapısı ──
        # Doğrulama mantığı YENİDEN YAZILMAZ; orijinal "Uyap Haricen Giriş\uyap_app.py"
        # içindeki fonksiyonlar import edilip aynen çağrılır.
        self.login_queue = queue.Queue()
        self.login_in_progress = False
        self._auth = None          # uyap_app modülü (arka planda yüklenir, ~5 sn)
        self._auth_err = None
        self._pending = None
        if "--selftest" not in sys.argv:
            self.after(100, lambda: threading.Thread(target=self._load_auth, daemon=True).start())
            try:
                dosya_core.senkron_zamanlayici_baslat(
                    log_fn=lambda m: self.log_queue.put(str(m) + "\n"))
            except Exception as e:
                self.log_queue.put(f"[HATA] Senkron zamanlayıcı başlatılamadı: {e}\n")

        self._show_login()

    # ── Orijinal login modülünü arka planda yükle ──
    def _load_auth(self):
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            uhg = os.path.normpath(os.path.join(here, "..", "Uyap Haricen Giriş"))
            if uhg not in sys.path:
                sys.path.insert(0, uhg)
            import uyap_app
            self._auth = uyap_app
            self.after(0, self._prefill_login)
        except Exception as e:
            self._auth_err = str(e)
            # NOT: except değişkeni blok bitince silinir; lambda'ya varsayılan
            # argümanla bağlanmazsa after() çalıştığında NameError olur.
            self.after(0, lambda m=str(e): self._set_login_status(
                "Giriş modülü yüklenemedi: %s" % m, err=True))

    def _prefill_login(self):
        if not self._auth or not hasattr(self, "user_var"):
            return
        try:
            cfg = self._auth.load_config()
            self.user_var.set(cfg.get("username", ""))
            if cfg.get("remember") and cfg.get("password_enc"):
                self.pw_var.set(self._auth.decrypt_secret(cfg.get("password_enc")))
                self.remember_var.set(True)
            self._set_login_status("")
        except Exception:
            pass

    # ── Giriş ekranı (sakin) ──
    # ── Giriş ekranı (sakin) ──
    def _show_login(self):
        self.overrideredirect(True)
        self.wm_attributes("-transparentcolor", "#123456")
        self.configure(bg="#123456")

        try:
            self._set_taskbar_icon()
        except Exception:
            pass

        # Size of the login card + margins for shadow
        width, height = 412, 460
        self.geometry(f"{width}x{height}")
        self._center(width, height)

        self.login_root = tk.Frame(self, bg="#123456")
        self.login_root.pack(fill="both", expand=True)

        # Canvas for rounded corners and shadow
        self.login_bg = tk.Canvas(self.login_root, bg="#123456", highlightthickness=0, bd=0)
        self.login_bg.pack(fill="both", expand=True)

        # Draw shadow
        round_rect(self.login_bg, 8, 8, width - 8, height - 8, 16, fill=C.SHADOW, outline="")
        # Draw card
        round_rect(self.login_bg, 6, 6, width - 10, height - 10, 16, fill=C.CARD, outline=C.CARD_EDGE)

        # Inner content frame
        inner = tk.Frame(self.login_bg, bg=C.CARD)
        inner.place(x=26, y=26, width=width-52, height=height-52)

        # Close button in login screen (top right of the card)
        close_btn = tk.Label(inner, text="×", bg=C.CARD, fg=C.INK_SOFT,
                             font=tkfont.Font(family="Segoe UI", size=14),
                             cursor="hand2")
        close_btn.place(relx=1.0, rely=0.0, x=0, y=-5, anchor="ne")
        close_btn.bind("<Button-1>", lambda e: self.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=C.CLAY))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=C.INK_SOFT))

        top = tk.Frame(inner, bg=C.CARD)
        top.pack(anchor="w")
        mk = tk.Canvas(top, width=34, height=34, bg=C.CARD, highlightthickness=0)
        round_rect(mk, 1, 1, 33, 33, 9, fill=C.SAGE, outline="")
        mk.create_text(17, 17, text="U", fill="#FFFFFF",
                       font=tkfont.Font(family="Segoe UI Semibold", size=14))
        mk.pack(side="left")
        tk.Label(top, text="UYAP Çalışma Paneli", bg=C.CARD, fg=C.INK,
                 font=self.f_card_t).pack(side="left", padx=11)
        tk.Label(inner, text="Hesabınızla giriş yapın", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.f_body).pack(anchor="w", pady=(12, 20))

        self.user_var = tk.StringVar()
        self.pw_var = tk.StringVar()
        self.remember_var = tk.BooleanVar(value=False)

        self._login_field(inner, "Kullanıcı adı", self.user_var)
        pw_entry = self._login_field(inner, "Parola", self.pw_var, show="•")

        tk.Checkbutton(inner, text="Bilgilerimi hatırla", variable=self.remember_var,
                       bg=C.CARD, fg=C.INK_SOFT, activebackground=C.CARD,
                       activeforeground=C.INK_SOFT, selectcolor=C.CARD,
                       font=self.f_small, bd=0, highlightthickness=0,
                       anchor="w", cursor="hand2").pack(fill="x", pady=(2, 16))

        self.login_btn = RoundButton(inner, "Giriş Yap", command=self._on_login,
                                     kind="primary", font=self.f_nav_sel, height=42)
        self.login_btn.pack(fill="x")

        self.login_status = tk.Label(inner, text="", bg=C.CARD, fg=C.CLAY,
                                     font=self.f_small, wraplength=320, justify="center")
        self.login_status.pack(pady=(14, 0))

        pw_entry.bind("<Return>", lambda e: self._on_login())

        # Drag bindings for login screen (click-drag anywhere on non-interactive parts)
        self._bind_drag_recursive(self.login_root)

    def _login_field(self, parent, label, var, show=None):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.INK_SOFT,
                 font=self.f_small).pack(anchor="w", pady=(0, 4))
        e = tk.Entry(parent, textvariable=var, show=(show or ""), bg="#FFFFFF",
                     fg=C.INK, relief="flat", insertbackground=C.INK,
                     font=self.f_body, highlightthickness=1,
                     highlightbackground=C.LINE, highlightcolor=C.SAGE)
        e.pack(fill="x", ipady=7, pady=(0, 15))
        return e

    def _set_login_status(self, text, err=False):
        if hasattr(self, "login_status") and self.login_status.winfo_exists():
            self.login_status.config(text=text, fg=(C.CLAY if err else C.SAGE_DK))

    # ── Giriş akışı (orijinal verify_credentials_thread'i kullanır) ──
    def _on_login(self):
        if self.login_in_progress:
            return
        user = self.user_var.get().strip()
        pw = self.pw_var.get().strip()
        if not user or not pw:
            self._set_login_status("Lütfen kullanıcı adı ve parolayı girin.", err=True)
            return
        if self._auth_err:
            self._set_login_status("Giriş modülü yüklenemedi: %s" % self._auth_err, err=True)
            return
        self._pending = (user, pw)
        self.login_in_progress = True
        self.login_btn.set_text("Bağlanılıyor…")
        self.login_btn.set_state("disabled")
        self._set_login_status("Sunucuya bağlanılıyor, bilgiler doğrulanıyor…")
        threading.Thread(target=self._login_bg, args=(user, pw), daemon=True).start()
        self.after(120, self._poll_login)

    def _login_bg(self, user, pw):
        # Orijinal login modülü henüz yüklenmediyse bekle
        while self._auth is None and self._auth_err is None:
            time.sleep(0.1)
        if self._auth_err:
            self.login_queue.put((False, "Giriş modülü yüklenemedi."))
            return
        # ORİJİNAL kod: uyap_app.verify_credentials_thread (websocket doğrulama)
        self._auth.verify_credentials_thread(
            self._auth.DEFAULT_SERVER_URL, user, pw, self.login_queue)

    def _poll_login(self):
        try:
            ok, msg = self.login_queue.get_nowait()
        except queue.Empty:
            if self.login_in_progress:
                self.after(120, self._poll_login)
            return
        self.login_in_progress = False
        if ok:
            self._save_login()
            self.username = self._pending[0]
            self._password = self._pending[1]   # paylaş/al için bellekte tutulur
            self._enter_app()
        else:
            self.login_btn.set_state("normal")
            self.login_btn.set_text("Giriş Yap")
            self._set_login_status("Giriş başarısız: %s" % msg, err=True)

    def _save_login(self):
        # ORİJİNAL config + DPAPI fonksiyonlarıyla kaydet
        user, pw = self._pending
        try:
            cfg = self._auth.load_config()
            cfg["server_url"] = self._auth.DEFAULT_SERVER_URL
            cfg["username"] = user
            cfg["remember"] = bool(self.remember_var.get())
            cfg["password_enc"] = (self._auth.encrypt_secret(pw)
                                   if self.remember_var.get() else "")
            self._auth.save_config(cfg)
        except Exception:
            pass

    def _enter_app(self):
        self.login_root.destroy()
        # Modül çıktıları (office_agent/home_client) günlüğe düşsün diye stdout'u
        # orijinal QueueWriteStream ile log kuyruğuna yönlendir.
        if self._auth and not self._stdout_redirected:
            try:
                sys.stdout = self._auth.QueueWriteStream(self.log_queue)
                sys.stderr = self._auth.QueueWriteStream(self.log_queue)
                self._stdout_redirected = True
            except Exception:
                pass

        # Mağazadan etkin (satın alınmış) bir app yoksa panel SADECE UYAP
        # bağlantısı sağlıyor demektir → daha kompakt, küçük bir pencereyle aç.
        # App etkinleştirilince (uygulamalari_yenile) pencere büyütülür.
        self._compact = not bool(magaza_core.sahip_olunanlar())
        if self._compact:
            self.minsize(720, 520)
            width, height = 860, 600
        else:
            self.minsize(980, 640)
            width, height = 1220, 780

        # Set main app window size and center
        self.geometry(f"{width}x{height}")
        self._center(width, height)

        # Pencere durumları (kare/yuvarlak kırpma + büyüt/eski boyut için)
        self._is_max = False
        self._minimized = False
        self._restore_geom = None

        # Setup main container
        self.main_root = tk.Frame(self, bg=C.BG)
        self.main_root.pack(fill="both", expand=True)

        self.main_bg = tk.Canvas(self.main_root, bg=C.BG, highlightthickness=0, bd=0)
        self.main_bg.pack(fill="both", expand=True)

        self.app_container = tk.Frame(self.main_bg, bg=C.BG)
        self.app_container.place(x=0, y=0, width=width, height=height)

        # Bind window resize to dynamically update background and container dimensions
        self.bind("<Configure>", self._on_resize)
        # Görev çubuğundan geri yükleme (minimize sonrası çerçeveyi yeniden gizle)
        self.bind("<Map>", self._on_map)

        self._build_main()

        # Yuvarlak köşeleri (pencere kırpma) ilk kez uygula
        self._on_resize(None)
        self._apply_round_region()

        # Make the header draggable recursively
        if hasattr(self, "header_frame"):
            self._bind_drag_recursive(self.header_frame)

        # Sistem tepsisi (system tray) — pencere gizlenince simgeden geri yüklenir.
        self.tray = SystemTray(self, "UYAP Çalışma Paneli", on_restore=self._from_tray)
        self.tray.start()

    def _on_resize(self, event=None):
        if event is not None and event.widget != self:
            return
        w = event.width if event is not None else self.winfo_width()
        h = event.height if event is not None else self.winfo_height()
        if w < 2 or h < 2:
            return
        # İçerik tüm pencereyi kaplar; yuvarlak köşeler pencere bölgesiyle
        # (SetWindowRgn) gerçek olarak kırpılır — böylece dikdörtgen çerçeveler
        # köşeleri kare göstermez.
        self.main_bg.delete("all")
        self.app_container.place(x=0, y=0, width=w, height=h)
        self._apply_round_region(w, h)

    def _set_taskbar_icon(self):
        if not sys.platform.startswith("win"):
            return
        if getattr(self, "_setting_taskbar_icon", False):
            return
        self._setting_taskbar_icon = True
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if hwnd:
                if hasattr(ctypes.windll.user32, "GetWindowLongPtrW"):
                    get_window_long = ctypes.windll.user32.GetWindowLongPtrW
                    set_window_long = ctypes.windll.user32.SetWindowLongPtrW
                else:
                    get_window_long = ctypes.windll.user32.GetWindowLongW
                    set_window_long = ctypes.windll.user32.SetWindowLongW

                # Set taskbar window style: remove toolwindow, add appwindow
                style = get_window_long(hwnd, -20) # GWL_EXSTYLE = -20
                style = (style & ~0x00000080) | 0x00040000 # ~WS_EX_TOOLWINDOW | WS_EX_APPWINDOW
                set_window_long(hwnd, -20, style)
                
                # Withdraw and deiconify to apply the taskbar style cleanly
                self.withdraw()
                self.deiconify()
        except Exception:
            pass
        finally:
            self._setting_taskbar_icon = False

    # ── Yuvarlak köşeler: pencereyi yuvarlatılmış dikdörtgen bölgeye kırp ──
    def _apply_round_region(self, w=None, h=None):
        """Windows'ta pencereyi yuvarlatılmış köşeli bir bölgeye kırpar.
        Büyütülmüş (maximize) durumda köşeler kare olur (ekranı tam kaplar)."""
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            if w is None or h is None:
                self.update_idletasks()
                w, h = self.winfo_width(), self.winfo_height()
            if w < 2 or h < 2:
                return
            hwnd = self.winfo_id()
            if getattr(self, "_is_max", False):
                rgn = ctypes.windll.gdi32.CreateRectRgn(0, 0, w, h)
            else:
                r = 30   # köşe yuvarlaklık yarıçapı
                rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r)
            # SetWindowRgn pencerenin bölge sahipliğini alır; tekrar silmeye gerek yok
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:
            pass

    # ── Çerçeve yeniden ışında (görev çubuğundan geri dönünce) ──
    def _reassert_chrome(self):
        """overrideredirect kapatılıp açıldıktan sonra şeffaf renk + görev
        çubuğu simgesi + yuvarlak köşeleri yeniden uygular."""
        try:
            self.wm_attributes("-transparentcolor", "#123456")
        except Exception:
            pass
        try:
            self._set_taskbar_icon()
        except Exception:
            pass
        self._apply_round_region()

    def _on_map(self, event=None):
        # Yalnızca ana pencere haritalandığında ve minimize'den dönüşte çalış
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_minimized", False):
            self._minimized = False
            try:
                self.overrideredirect(True)
            except Exception:
                pass
            self.update_idletasks()
            self._reassert_chrome()

    # ── Simge durumuna küçült (minimize) ──
    def _minimize(self):
        # Çerçevesiz (overrideredirect) pencerede Tk'nin iconify'ı Windows'ta
        # "override-redirect flag is set" hatasıyla SESSİZCE başarısız olur (buton
        # çalışmıyor gibi görünür). Bu yüzden doğrudan Windows ShowWindow(SW_MINIMIZE)
        # kullanılır; görev çubuğu simgesi (WS_EX_APPWINDOW) zaten var, geri yükleme
        # <Map> ile çalışır.
        self._minimized = True
        if sys.platform.startswith("win"):
            try:
                import ctypes
                self.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
                ctypes.windll.user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
                return
            except Exception:
                pass
        # Diğer platformlar / ctypes yoksa: çerçeveyi geçici aç, iconify et
        try:
            self.overrideredirect(False)
            self.update_idletasks()
            self.iconify()
        except Exception:
            self._minimized = False

    # ── Sistem tepsisine indir (görev çubuğunda yer kaplamasın) ──
    def _to_tray(self):
        """Pencereyi tamamen gizleyip sistem tepsisine (system tray) indirir.
        Görev çubuğunda yer kaplamaz; tepsi simgesine tıklayınca geri gelir.
        Tepsi kullanılamıyorsa (Windows dışı / hata) normal simge durumuna düşer."""
        tray = getattr(self, "tray", None)
        if not tray or not tray.available:
            self._minimize()
            return
        # Simge gerçekten eklendiyse pencereyi gizle; eklenemediyse (tutamaç
        # geçersiz vb.) pencereyi kaybetmemek için normal simge durumuna düş.
        if not tray.show():
            self._minimize()
            return
        try:
            self.withdraw()   # görev çubuğundan da kaldırır
        except Exception:
            pass

    def _from_tray(self):
        """Tepsi simgesine tıklanınca pencereyi geri yükler (tepsi → ekran)."""
        tray = getattr(self, "tray", None)
        if tray:
            tray.hide()
        try:
            self.deiconify()
            self.update_idletasks()
            self._reassert_chrome()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    # ── Tam ekran / eski boyut arasında geçiş (maximize/restore) ──
    def _work_area(self):
        """Görev çubuğunu örtmeyen çalışma alanı (left, top, right, bottom)."""
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                       ctypes.byref(rect), 0)
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            return None

    def _toggle_maximize(self):
        if getattr(self, "_is_max", False):
            self._restore_size()
        else:
            self._maximize()

    def _maximize(self):
        self._restore_geom = self.geometry()
        self._is_max = True
        work = self._work_area()
        if work:
            l, t, r, b = work
            self.geometry(f"{r - l}x{b - t}+{l}+{t}")
        else:
            self.state("zoomed")
        if hasattr(self, "_max_btn") and self._max_btn.winfo_exists():
            self._max_btn.config(text="❐")
        self.update_idletasks()
        self._apply_round_region()

    def _restore_size(self):
        self._is_max = False
        if getattr(self, "_restore_geom", None):
            self.geometry(self._restore_geom)
        else:
            self.state("normal")
        if hasattr(self, "_max_btn") and self._max_btn.winfo_exists():
            self._max_btn.config(text="□")
        self.update_idletasks()
        self._apply_round_region()

    # ── Uygulamayı kapat (arka plan görevlerini durdurup süreci sonlandır) ──
    def _close(self):
        try:
            if getattr(self, "tray", None):
                self.tray.stop()
        except Exception:
            pass
        try:
            if getattr(self, "baglanti", None):
                self.baglanti.stop_sharing()
                self.baglanti.stop_receiving()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    def _make_draggable(self, widget):
        widget.bind("<Button-1>", self._on_drag_start, add="+")
        widget.bind("<B1-Motion>", self._on_drag_motion, add="+")

    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event):
        if getattr(self, "_is_max", False):
            return
        x = self.winfo_x() - self._drag_x + event.x
        y = self.winfo_y() - self._drag_y + event.y
        self.geometry(f"+{x}+{y}")

    def _bind_drag_recursive(self, widget):
        try:
            if isinstance(widget, (tk.Entry, RoundButton)):
                return
            if widget.cget("cursor") == "hand2":
                return
        except Exception:
            pass
        widget.bind("<Button-1>", self._on_drag_start, add="+")
        widget.bind("<B1-Motion>", self._on_drag_motion, add="+")
        for child in widget.winfo_children():
            self._bind_drag_recursive(child)

    def _logout(self):
        # Unbind configure event to avoid resize calls in login screen
        self.unbind("<Configure>")
        # Tepsi simgesini/iş parçacığını kapat (giriş ekranına dönülüyor)
        if getattr(self, "tray", None):
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None
        # Çalışan paylaşım/alma görevlerini orijinal mantıkla durdur
        if getattr(self, "baglanti", None):
            try:
                self.baglanti.stop_sharing()
                self.baglanti.stop_receiving()
            except Exception:
                pass
            self.baglanti = None
        for w in self.winfo_children():
            w.destroy()
        self.nav_items, self.panels, self.current = {}, {}, None
        self.login_in_progress = False
        self.login_queue = queue.Queue()
        self._show_login()
        self.after(0, self._prefill_login)

    # ── Ana kabuğu kur (giriş başarılı olunca) ──
    def _build_main(self):
        self._build_header()
        self._build_body()       # sidebar + content
        self._build_statusbar()
        # Pano kaldırıldı: açılış, her kullanıcıda var olan çekirdek ekranda (UYAP Bağlantısı)
        self.select("baglanti")
        self.fab = FabMenu(self)  # sağ-alt yüzen hızlı menü (yer tutucu kısayollar)
        # Ayarlarda seçiliyse UYAP bağlantısını açılışta otomatik başlat
        if "--selftest" not in sys.argv:
            self.after(900, self._maybe_autoconnect)

    def _maybe_autoconnect(self):
        """Ayar 'share'/'receive' ise UYAP bağlantısını otomatik başlatır.
        Bağlantı mantığı BaglantiPanel'in (dolayısıyla orijinal office_agent/
        home_client) üzerinden çağrılır; burada yeni mantık yoktur."""
        if not getattr(self, "baglanti", None):
            return
        mode = auto_connect_mode(self._auth)
        try:
            if mode == "share" and not self.baglanti.is_sharing:
                self.status.config(text="Otomatik bağlantı: paylaşım başlatılıyor…")
                self.baglanti.start_sharing()
            elif mode == "receive" and not self.baglanti.is_receiving:
                self.status.config(text="Otomatik bağlantı: ofise bağlanılıyor…")
                self.baglanti.start_receiving()
        except Exception as e:
            try:
                self.log_queue.put(f"[HATA] Otomatik bağlantı başlatılamadı: {e}\n")
            except Exception:
                pass

    # ── pencereyi ortala ──
    def _center(self, w, h):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── üst başlık ──
    def _build_header(self):
        self.header_frame = tk.Frame(self.app_container, bg=C.HEADER, height=74)
        self.header_frame.pack(side="top", fill="x")
        self.header_frame.pack_propagate(False)

        # Double click to maximize/restore
        self.header_frame.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

        left = tk.Frame(self.header_frame, bg=C.HEADER)
        left.pack(side="left", padx=26, pady=2)

        # küçük marka işareti
        mark = tk.Canvas(left, width=30, height=30, bg=C.HEADER,
                         highlightthickness=0)
        round_rect(mark, 2, 2, 28, 28, 8, fill=C.SAGE, outline="")
        mark.create_text(15, 15, text="U", fill="#FFFFFF",
                         font=tkfont.Font(family="Segoe UI Semibold", size=13))
        mark.pack(side="left", pady=18, padx=(0, 12))

        tt = tk.Frame(left, bg=C.HEADER)
        tt.pack(side="left", pady=14)
        tk.Label(tt, text="UYAP Çalışma Paneli", bg=C.HEADER, fg=C.INK,
                 font=tkfont.Font(family="Segoe UI Semibold", size=14)).pack(anchor="w")
        tk.Label(tt, text="Hukuk bürosu otomasyonu", bg=C.HEADER, fg=C.INK_SOFT,
                 font=self.f_small).pack(anchor="w")

        # Title bar controls (Minimize, Maximize, Close) on the far right (top right) using place
        btn_frame = tk.Frame(self.header_frame, bg=C.HEADER)
        btn_frame.place(relx=1.0, rely=0.0, x=0, y=0, anchor="ne")
        btn_frame._is_titlebar_btn = True

        btn_opts = {
            "bg": C.HEADER,
            "fg": C.INK,
            "font": tkfont.Font(family="Segoe UI", size=10),
            "width": 5,
            "cursor": "hand2",
            "bd": 0,
            "relief": "flat",
            "pady": 6
        }

        close_btn = tk.Label(btn_frame, text="×", **btn_opts)
        close_btn.pack(side="right")
        close_btn._is_titlebar_btn = True
        close_btn.bind("<Button-1>", lambda e: self._close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(bg="#E81123", fg="#FFFFFF"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(bg=C.HEADER, fg=C.INK))

        self._max_btn = tk.Label(btn_frame, text="□", **btn_opts)
        self._max_btn.pack(side="right")
        self._max_btn._is_titlebar_btn = True
        self._max_btn.bind("<Button-1>", lambda e: self._toggle_maximize())
        self._max_btn.bind("<Enter>", lambda e: self._max_btn.config(bg=C.LINE))
        self._max_btn.bind("<Leave>", lambda e: self._max_btn.config(bg=C.HEADER, fg=C.INK))

        min_btn = tk.Label(btn_frame, text="—", **btn_opts)
        min_btn.pack(side="right")
        min_btn._is_titlebar_btn = True
        min_btn.bind("<Button-1>", lambda e: self._minimize())
        min_btn.bind("<Enter>", lambda e: min_btn.config(bg=C.LINE))
        min_btn.bind("<Leave>", lambda e: min_btn.config(bg=C.HEADER, fg=C.INK))

        # Sistem tepsisine indir — görev çubuğunda yer kaplamadan arka plana al.
        tray_btn = tk.Label(btn_frame, text="⤓", **btn_opts)
        tray_btn.pack(side="right")
        tray_btn._is_titlebar_btn = True
        tray_btn.bind("<Button-1>", lambda e: self._to_tray())
        tray_btn.bind("<Enter>", lambda e: tray_btn.config(bg=C.LINE))
        tray_btn.bind("<Leave>", lambda e: tray_btn.config(bg=C.HEADER, fg=C.INK))

        # sağ: çıkış + kullanıcı + bağlantı rozeti (sade)
        # We give logout right padding of 160px so it packs cleanly without overlapping with btn_frame
        logout = tk.Label(self.header_frame, text="Çıkış", bg=C.HEADER, fg=C.INK_SOFT,
                          font=self.f_small, cursor="hand2")
        logout.pack(side="right", padx=(0, 160))
        logout._is_titlebar_btn = True
        logout.bind("<Button-1>", lambda e: self._logout())
        logout.bind("<Enter>", lambda e: logout.config(fg=C.CLAY))
        logout.bind("<Leave>", lambda e: logout.config(fg=C.INK_SOFT))

        if self.username:
            tk.Label(self.header_frame, text=self.username, bg=C.HEADER, fg=C.INK,
                     font=self.f_nav_b).pack(side="right", padx=(0, 6))
        tk.Frame(self.header_frame, bg=C.LINE, width=1, height=22).pack(side="right", padx=18)

        self.conn_chip = tk.Label(self.header_frame, text="●  Bağlantı bekleniyor",
                                  bg=C.HEADER, fg=C.WAIT, font=self.f_small)
        self.conn_chip.pack(side="right")

        tk.Frame(self.app_container, bg=C.LINE, height=1).pack(side="top", fill="x")

    # ── gövde: sol gezinme + içerik ──
    def _build_body(self):
        body = tk.Frame(self.app_container, bg=C.BG)
        body.pack(side="top", fill="both", expand=True)

        # sol panel — kaydırılabilir (derin ağaç için)
        side = tk.Frame(body, bg=C.SIDEBAR, width=246)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(body, bg=C.LINE, width=1).pack(side="left", fill="y")

        canvas = tk.Canvas(side, bg=C.SIDEBAR, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        # İnce, temaya uygun, gerektiğinde beliren kaydırma çubuğu (sağ kenara bindirir)
        vsb = SlimScrollbar(side, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)

        scroll = tk.Frame(canvas, bg=C.SIDEBAR)
        win = canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        scroll.bind("<Configure>",
                    lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Kaydırma çerçevesi referansı — menü canlı yeniden çizilir (yenile).
        self._nav_scroll = scroll

        # içerik alanı
        self.content = tk.Frame(body, bg=C.BG)
        self.content.pack(side="left", fill="both", expand=True)

        # Modül referansları (panel tembel kurulunca atanır)
        self.baglanti = None
        self.udf = None
        self.sgk = None
        self.icra = None
        self.logger = None
        self.ayarlar = None
        self.magaza = None

        # Sol menüyü ilk kez çiz (sahip olunan app'ler + çekirdek üçlü)
        self._render_sidebar()

        # UYAP Bağlantısı panelini ŞİMDİ kur: açılışta otomatik bağlantı (_maybe_
        # autoconnect) ona ihtiyaç duyar. Diğer paneller ilk açılışta (select) kurulur.
        self._panel_kur("baglanti")

    # ── sol menüyü (yeniden) çiz: kaydırma çerçevesini temizle + ağacı kur ──
    def _render_sidebar(self):
        scroll = self._nav_scroll
        for w in scroll.winfo_children():
            w.destroy()
        self.nav_leaves = {}
        tk.Frame(scroll, bg=C.SIDEBAR, height=8).pack(fill="x")
        self._render_nav(scroll, self._aktif_nav(), depth=0)
        tk.Frame(scroll, bg=C.SIDEBAR, height=10).pack(fill="x")
        # seçili yaprağın vurgusunu koru
        for k, item in self.nav_leaves.items():
            item.set_selected(k == self.current)

    # ── bir yaprağın içerik panelini TEMBEL kur (zaten varsa dokunma) ──
    def _panel_kur(self, key):
        """İlgili modülün panelini oluşturup self.panels[key]'e koyar. Modül
        mantığı AYNEN ilgili panel sınıfından gelir; burada yeniden yazılmaz."""
        if key in self.panels:
            return self.panels[key]
        uretilmis = {m["key"]: m for m in logger_core.registry_oku()}
        MODUL = {"icra_dosyalarim", "mts", "baglanti", "udf", "sgk", "logger",
                 "ayarlar", "magaza", "senkron_kapsami", "dosyalarim_genel"}
        if key in MODUL or key in uretilmis:
            frame = tk.Frame(self.content, bg=C.BG)
            if key == "icra_dosyalarim":
                self.icra = IcraDosyalarimPanel(frame, self)
            elif key == "mts":
                self.mts = MtsTakipPanel(frame, self)
            elif key == "baglanti":
                self.baglanti = BaglantiPanel(frame, self)
            elif key == "udf":
                self.udf = UdfPanel(frame, self)
            elif key == "sgk":
                self.sgk = SgkPanel(frame, self)
            elif key == "logger":
                self.logger = LoggerPanel(frame, self)
            elif key == "ayarlar":
                self.ayarlar = AyarlarPanel(frame, self)
            elif key == "senkron_kapsami":
                self.senkron_kapsami = SenkronKapsamiPanel(frame, self)
            elif key == "dosyalarim_genel":
                self.dosyalarim_genel = DosyalarimGenelPanel(frame, self)
            elif key == "magaza":
                self.magaza = MagazaPanel(frame, self)
            elif key in uretilmis:
                m = uretilmis[key]
                try:
                    UretilmisRunner(frame, self, m["core"], m.get("label", key))
                except Exception as e:
                    tk.Label(frame, text=f"Üretilen modül yüklenemedi:\n{m.get('core')}\n\n{e}",
                             bg=C.BG, fg=C.CLAY, font=self.f_body, justify="left",
                             wraplength=600).pack(padx=40, pady=40, anchor="w")
            self.panels[key] = frame
        else:
            # Henüz entegre edilmemiş yaprak (ör. mts/xml) → sakin boş-durum
            self.panels[key] = self._empty_panel(key)
        return self.panels[key]

    # ── mağazadan etkinleştir/kaldır sonrası menü + panelleri canlı tazele ──
    def uygulamalari_yenile(self):
        """Sahiplik değişince çağrılır. Menüyü yeniden çizer ve artık sahip
        olunmayan app panellerini yıkar. Çekirdek panellere (özellikle çalışan
        UYAP bağlantısına) DOKUNMAZ."""
        self._render_sidebar()

        # Kompakt moddayken (yalnız UYAP bağlantısı) bir app etkinleştirildiyse
        # pencereyi tam boyuta büyüt; son app de kaldırıldıysa kompakta geri dön.
        self._uygula_kompakt_boyut()

        aktif = set()

        def topla(nodes):
            for n in nodes:
                if "children" in n:
                    topla(n["children"])
                else:
                    aktif.add(n["key"])
        topla(self._aktif_nav())

        for key in list(self.panels.keys()):
            if key in aktif:
                continue
            try:
                self.panels[key].destroy()
            except Exception:
                pass
            self.panels.pop(key, None)
            for ad in ("icra", "mts", "udf", "sgk", "logger"):
                if getattr(self, ad, None) is not None and key == {
                        "icra": "icra_dosyalarim", "mts": "mts", "udf": "udf",
                        "sgk": "sgk", "logger": "logger"}.get(ad):
                    setattr(self, ad, None)

        # Seçili panel kaldırıldıysa mağazaya dön
        if self.current not in aktif:
            self.current = None
            self.select("magaza")

    # ── kompakt/tam pencere boyutunu sahipliğe göre uygula ──
    def _uygula_kompakt_boyut(self):
        """Sahip olunan app yoksa kompakt (küçük), varsa tam pencere. Yalnızca
        durum gerçekten değiştiyse boyutu güncelle (kullanıcı elle büyütmüşse
        gereksiz zıplama olmasın). Büyütülmüş (maximize) pencereye dokunma."""
        if getattr(self, "_is_max", False):
            return
        kompakt = not bool(magaza_core.sahip_olunanlar())
        if kompakt == getattr(self, "_compact", None):
            return
        self._compact = kompakt
        if kompakt:
            self.minsize(720, 520)
            width, height = 860, 600
        else:
            self.minsize(980, 640)
            width, height = 1220, 780
        self._center(width, height)
        self.update_idletasks()
        self._apply_round_region()

    # ── gezinme ağacını özyinelemeli kur (katlanır gruplar) ──
    def _render_nav(self, container, nodes, depth):
        for node in nodes:
            if "children" in node:
                grp = tk.Frame(container, bg=C.SIDEBAR)
                grp.pack(fill="x")
                kids = tk.Frame(grp, bg=C.SIDEBAR)   # alt öğeler (katlanır)
                # Başlangıçta tüm gruplar kapalı; kullanıcı tıklayınca açılır.
                header = GroupRow(grp, self, node["label"], depth,
                                  node["accent"], kids, expanded=False)
                header.pack(fill="x")
                # kids paketlenmez — açılış GroupRow.toggle() ile yapılır
                self._render_nav(kids, node["children"], depth + 1)
            else:
                leaf = LeafRow(container, self, node["key"], node["label"],
                               node["accent"], depth)
                leaf.pack(fill="x")
                self.nav_leaves[node["key"]] = leaf

    # ── boş modül paneli (içerik ileride gelecek) ──
    def _empty_panel(self, key):
        title = self.labels.get(key, key)
        p = tk.Frame(self.content, bg=C.BG)

        head = tk.Frame(p, bg=C.BG)
        head.pack(fill="x", padx=40, pady=(34, 0))
        tk.Label(head, text=title, bg=C.BG, fg=C.INK,
                 font=self.f_h1).pack(anchor="w")
        tk.Frame(p, bg=C.LINE, height=1).pack(fill="x", padx=40, pady=(18, 0))

        # sakin boş-durum
        mid = tk.Frame(p, bg=C.BG)
        mid.pack(fill="both", expand=True)
        box = tk.Frame(mid, bg=C.BG)
        box.place(relx=0.5, rely=0.46, anchor="center")

        ic = tk.Canvas(box, width=64, height=64, bg=C.BG, highlightthickness=0)
        ic.create_oval(8, 8, 56, 56, fill=C.SAGE_TINT, outline="")
        ic.create_oval(24, 24, 40, 40, fill=C.SAGE, outline="")
        ic.pack()
        tk.Label(box, text="Bu modül buraya eklenecek", bg=C.BG, fg=C.INK,
                 font=tkfont.Font(family="Segoe UI Semibold", size=13)).pack(pady=(14, 4))
        tk.Label(box, text="Kabuk hazır. Modülün kendi orijinal kodu, mantığı "
                           "bozulmadan bu alana yerleştirilecek.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.f_body,
                 wraplength=420, justify="center").pack()
        return p

    # ── durum çubuğu ──
    def _build_statusbar(self):
        tk.Frame(self.app_container, bg=C.LINE, height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(self.app_container, bg=C.HEADER, height=30)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="Hazır", bg=C.HEADER, fg=C.INK_SOFT,
                               font=self.f_small)
        self.status.pack(side="left", padx=20)
        tk.Label(bar, text="127.0.0.1 · yerel", bg=C.HEADER, fg=C.INK_FAINT,
                 font=self.f_small).pack(side="right", padx=20)

    # ── gezinme ──
    def select(self, key):
        if key == self.current:
            return
        if key not in self.panels:          # panel TEMBEL kurulur (ilk açılışta)
            self._panel_kur(key)
        if self.current and self.current in self.panels:
            self.panels[self.current].pack_forget()
        self.panels[key].pack(fill="both", expand=True)
        for k, item in self.nav_leaves.items():
            item.set_selected(k == key)
        self.current = key
        label = self.labels.get(key, "")
        self.status.config(text=f"{label} görüntüleniyor")


def _venv_python(penceresiz=False):
    """Proje .venv python yolu (psycopg + Django burada kurulu). penceresiz=True
    ise GUI için konsolsuz pythonw.exe döner. Bulunamazsa None."""
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Panel'in üstü = kök
    ad = "pythonw.exe" if penceresiz else "python.exe"
    py = os.path.join(kok, "Uyap Haricen Giriş", ".venv", "Scripts", ad)
    return py if os.path.isfile(py) else None


def _dogru_python_sagla():
    """GUI yanlış Python ile açıldıysa (psycopg yok → 'Error loading psycopg'),
    kendini .venv python'ı altında YENİDEN BAŞLATIR ve bu (yanlış) süreçten çıkar.
    Doğru python ise hiçbir şey yapmaz.

    Not: Windows'ta os.execv GUI yeniden başlatmada güvenilmez (yeni pencere
    gelmeyebilir/süreç sessizce ölebilir); bunun yerine yeni süreci başlatıp bu
    süreci kapatıyoruz. GUI için konsolsuz pythonw tercih edilir (boş konsol
    penceresi çıkmasın)."""
    try:
        import psycopg  # noqa: F401  — doğru ortamdayız, dokunma
        return
    except Exception:
        pass
    venv_py = _venv_python(penceresiz=True) or _venv_python(penceresiz=False)
    if not venv_py:
        return  # .venv bulunamadı; mevcut python ile devam (DB pas geçilebilir)
    # Döngü koruması: zaten .venv\Scripts altındaki bir python (python.exe ya da
    # pythonw.exe) ile koşuyorsak tekrar başlatma.
    venv_scripts = os.path.normcase(os.path.dirname(venv_py))
    bu_dizin = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    if bu_dizin == venv_scripts:
        return
    print("ℹ️ Panel doğru Python (.venv) ile yeniden başlatılıyor…")
    try:
        DETACHED = 0x00000008  # DETACHED_PROCESS — yeni süreç kapanan konsola bağlı kalmasın
        subprocess.Popen(
            [venv_py, os.path.abspath(__file__)] + sys.argv[1:],
            close_fds=True,
            creationflags=DETACHED if sys.platform.startswith("win") else 0,
        )
    except Exception as e:
        print(f"⚠️ .venv ile yeniden başlatılamadı, mevcut python ile devam: {e}")
        return
    raise SystemExit(0)  # bu yanlış süreçten temiz çık (GUI artık .venv'de açılıyor)


def _veritabani_hazirla():
    """Açılışta gömülü PostgreSQL'i başlatır + migrate eder. Hata olursa GUI yine
    açılır (sorgular canlı UYAP'a düşer; db_baslat zaten bunu loglar)."""
    try:
        kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if kok not in sys.path:
            sys.path.insert(0, kok)
        import db_baslat
        db_baslat.ensure_db()
    except Exception as e:
        print(f"⚠️ Veritabanı hazırlanamadı (sorgular canlı UYAP'a düşecek): {e}")


def main():
    # GUI'yi her zaman .venv python'ı altında çalıştır (psycopg/Django orada).
    _dogru_python_sagla()
    # Gömülü PostgreSQL'i ayağa kaldır + şemayı güncelle.
    _veritabani_hazirla()
    # Windows'ta aiortc/aiohttp/uvicorn (office_agent/home_client) için Proactor döngüsü.
    if sys.platform.startswith("win"):
        try:
            import asyncio
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass
    app = Panel()
    if "--selftest" in sys.argv:
        app.update_idletasks()
        app.update()
        # giriş ekranını atla, doğrudan kabuğu kur ve sekmeleri gez
        app.username = "test"
        app._enter_app()
        app.update()
        # Dinamik menü kapsamı: birkaç app'i etkinleştir, menüyü canlı tazele,
        # sonra hepsini geri al (kullanıcının sahiplik dosyasını kirletme).
        _test_apps = ["mts_takip_acma", "xml_takip", "sgk_sorgu",
                      "icra_dosyalarim", "udf_donusturucu", "oturum_kaydi"]
        _onceki_sahip = magaza_core.sahip_olunanlar()
        for _k in _test_apps:
            magaza_core.sahip_ekle(_k)
        app.uygulamalari_yenile()
        app.update()
        for k in list(app.labels.keys()):    # tüm yaprakları gez (panel tembel kurulur)
            app.select(k)
            app.update()
        # sahiplik dosyasını test öncesi haline döndür
        for _k in _test_apps:
            if _k not in _onceki_sahip:
                magaza_core.sahip_cikar(_k)
        app.uygulamalari_yenile()
        app.update()
        # Test FabMenu toggle (open/close)
        app.fab._toggle()
        app.update()
        app.fab._toggle()
        app.update()
        print("SELFTEST OK — paneller:", list(app.panels.keys()))
        app.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
