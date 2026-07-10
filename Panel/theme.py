# -*- coding: utf-8 -*-
"""
UYAP Çalışma Paneli — ortak tema
================================
Sakin, ılık-nötr palet + soft renkli vurgular. Hem ana kabuk (panel.py) hem de
modüller (modules/*.py) bu paleti paylaşır; renk/şekil tek kaynaktan gelir.
"""

import tkinter as tk
from tkinter import font as tkfont


class C:
    BG        = "#F1EEE8"   # ana zemin (ılık kâğıt)
    SIDEBAR   = "#EAE5DC"   # sol panel (bir tık daha derin)
    HEADER    = "#EFEBE3"   # üst başlık
    CARD      = "#FCFBF8"   # kart yüzeyi
    CARD_EDGE = "#E8E2D7"   # kart kenarı
    SHADOW    = "#E0D9CD"   # yumuşak gölge

    INK       = "#43423D"   # ana metin (ılık koyu gri, saf siyah değil)
    INK_SOFT  = "#8C867B"   # ikincil metin
    INK_FAINT = "#B6AFA2"   # en silik metin
    LINE      = "#E3DDD2"   # saç-çizgi ayraç

    SAGE      = "#7C9A7E"   # birincil vurgu — adaçayı
    SAGE_DK   = "#5E7D63"
    SAGE_TINT = "#E4EBE0"   # seçili öğe zemini

    CLAY      = "#C18A66"   # ikincil — kil/terracotta (soft)
    BLUE      = "#7C93AB"   # üçüncül — dusty mavi
    GOLD      = "#C8A66A"   # dördüncül — sıcak altın

    OK        = "#7C9A7E"
    WAIT      = "#C8A66A"
    OFF       = "#B6AFA2"


ACCENTS = [C.SAGE, C.CLAY, C.BLUE, C.GOLD]

# Vurgu ismi → hex. magaza_core (sunum-bağımsız çekirdek) accent'i İSİM olarak
# tutar ("sage"/"clay"/"blue"/"gold"); tkinter tarafı bunu hex'e çözer. Çözücü
# idempotenttir: zaten "#..." hex verilirse aynen döner (çekirdek menü güvende).
ACCENT_BY_NAME = {
    "sage": C.SAGE, "clay": C.CLAY, "blue": C.BLUE, "gold": C.GOLD,
    "ink_faint": C.INK_FAINT,
}


def accent(name, default=C.SAGE):
    """Vurgu ismini (ör. 'sage') hex renge çevirir. Hex zaten verildiyse aynen
    döndürür, böylece isim ve hex karışık geçen yerlerde güvenle çağrılabilir."""
    if isinstance(name, str) and name.startswith("#"):
        return name
    return ACCENT_BY_NAME.get(name, default)


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """Tuval üzerinde yuvarlak köşeli dikdörtgen."""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


# Buton tipleri: (zemin, hover_zemin, yazı, kenar | None)
_BTN_KINDS = {
    "primary": (C.SAGE, C.SAGE_DK, "#FFFFFF", None),
    "ghost":   (C.CARD, C.SAGE_TINT, C.INK_SOFT, C.LINE),
    "stop":    (C.CLAY, "#A9744F", "#FFFFFF", None),
}


class RoundButton(tk.Canvas):
    """Yumuşak köşeli, hafif gölgeli buton (tkinter native yerine tuval tabanlı).
    Davranış aynı; yalnızca görünüm yumuşatılır. Genişlik fill='x' ile esner,
    aksi halde metne göre ayarlanır."""

    def __init__(self, parent, text, command=None, kind="primary", font=None,
                 height=36, radius=11, pad=18):
        try:
            pbg = parent.cget("bg")
        except Exception:
            pbg = C.BG
        super().__init__(parent, height=height, highlightthickness=0, bd=0,
                         bg=pbg, cursor="hand2")
        self._text = text
        self._cmd = command
        self._kind = kind
        self._font = font
        self._radius = radius
        self._pad = pad
        self._state = "normal"
        self._hover = False
        if font is not None:
            self.config(width=font.measure(text) + pad * 2)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def _palette(self):
        if self._state == "disabled":
            return (C.LINE, C.LINE, C.INK_FAINT, C.LINE)
        return _BTN_KINDS.get(self._kind, _BTN_KINDS["primary"])

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        if w < 4 or h < 4:
            return
        fill, hover, fg, border = self._palette()
        bg = fill if (self._hover and self._state == "normal") else fill
        if self._hover and self._state == "normal":
            bg = hover if self._kind != "ghost" else fill
        # hafif gölge (alt-sağ, yumuşak)
        round_rect(self, 2, 4, w - 1, h - 1, self._radius, fill=C.SHADOW, outline="")
        # gövde
        round_rect(self, 1, 1, w - 2, h - 3, self._radius, fill=bg,
                   outline=(border or bg))
        # ghost hover: zemini hafif tint, yazıyı koyulaştır
        if self._kind == "ghost" and self._hover and self._state == "normal":
            round_rect(self, 1, 1, w - 2, h - 3, self._radius, fill=C.SAGE_TINT,
                       outline=C.SAGE)
            fg = C.INK
        self.create_text((w - 1) / 2, (h - 1) / 2, text=self._text, fill=fg,
                         font=self._font)

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _on_click(self, _e=None):
        if self._state == "normal" and self._cmd:
            self._cmd()

    # ── davranışı koruyan basit API ──
    def set_text(self, text):
        self._text = text
        if self._font is not None:
            self.config(width=self._font.measure(text) + self._pad * 2)
        self._draw()

    def set_kind(self, kind):
        self._kind = kind
        self._draw()

    def set_command(self, cmd):
        self._cmd = cmd

    def set_state(self, state):
        self._state = "disabled" if state == "disabled" else "normal"
        self.config(cursor="" if self._state == "disabled" else "hand2")
        self._draw()


def make_fonts():
    """Kabuğun kullandığı yazı tiplerini üretir (panel.py ve modüller paylaşır)."""
    return {
        "h1":      tkfont.Font(family="Segoe UI Semibold", size=20),
        "sub":     tkfont.Font(family="Segoe UI", size=11),
        "grp":     tkfont.Font(family="Segoe UI Semibold", size=8),
        "nav":     tkfont.Font(family="Segoe UI", size=11),
        "nav_b":   tkfont.Font(family="Segoe UI Semibold", size=9),
        "nav_sel": tkfont.Font(family="Segoe UI Semibold", size=11),
        "card_t":  tkfont.Font(family="Segoe UI Semibold", size=13),
        "card_d":  tkfont.Font(family="Segoe UI", size=10),
        "body":    tkfont.Font(family="Segoe UI", size=11),
        "small":   tkfont.Font(family="Segoe UI", size=9),
        "mono":    tkfont.Font(family="Consolas", size=9),
    }
