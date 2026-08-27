# -*- coding: utf-8 -*-
"""
Modül: Barkod Sorgu (Kapalı Tebligat — PTT)
=============================================
`barkod_sorgu.py`'nin (çekirdek mantık: PDF'ten barkod çıkarma + PTT sorgusu +
TebligatBarkod'a DB kaydı) jenerik `uretilmis_runner` YERİNE özel ekranı.
`dosyalarim_genel.py` ile AYNI desende iki bölüm sunar (kullanıcı isteği,
2026-08-03): "Dosyalarım (Tümü)" ekranındaki gibi yerel DB'den İcra
dosyalarını filtrelenip sıralanabilir bir tabloda listeler — kullanıcı satır
seçip "Seçilenleri Sorgula" ile o dosyalar için barkod_sorgu'yu tetikler
(Excel yükleme/tarih aralığı girmeye ALTERNATİF, üçüncü bir giriş yolu — bkz.
barkod_sorgu.calistir'in "seçili dosyalar modu"). Alt bölümde ise DB'ye daha
önce yazılmış TebligatBarkod sonuçları (barkod/PTT durumu/mazbata) bir geçmiş
raporu olarak gösterilir.

`icra_dosyalarim.py`/`dosyalarim_genel.py` AYRIMINDAKİ AYNI karar (paralel
yaz, ortak soyutlamaya zorlama) burada da geçerli — bu dosya kendi başına,
`dosyalarim_genel.py`'nin sütun-başlığı canlı filtre/sıralama deseni
KOPYALANARAK (paylaşılan bir taban sınıfa çıkarılmadan) uygulanmıştır.
"""

import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import openpyxl

from theme import C, RoundButton
from . import dosya_core, barkod_sorgu, teblig_21_2_core, teblig_21_2_gonder, normal_tebligat_gonder

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE_TIK = os.path.dirname(os.path.abspath(__file__))
if _HERE_TIK not in sys.path:
    sys.path.insert(0, _HERE_TIK)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402

_ACILIS_TARIHI_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_EVRAK_TARIHI_RE = re.compile(r"^(\d{2})[./](\d{2})[./](\d{4})$")
_TARIH_SAAT_RE = re.compile(r"(\d{2})[./](\d{2})[./](\d{4})(?:[ T](\d{2}):(\d{2}))?")


def _evrak_tarihi_anahtari(tarih):
    """'GG/AA/YYYY' ya da 'GG.AA.YYYY' -> sıralanabilir 'YYYYAAGG'. Ayrıştırılamazsa
    boş dize döner (en küçük/en eski sayılır — bkz. `_barkod_ozetini_birlestir`)."""
    m = _EVRAK_TARIHI_RE.match(str(tarih or "").strip())
    if not m:
        return ""
    return f"{m.group(3)}{m.group(2)}{m.group(1)}"


def _tarih_saat_anahtari(deger):
    """'GG/AA/YYYY[ SS:DD]' ya da 'GG.AA.YYYY[ SS:DD]' -> sıralanabilir
    'YYYYAAGGSSDD'. `barkod_sorgu.py`'nin ürettiği "Tebliğ/İade Tarihi"/"Son
    İşlem Tarihi"/"Sorgu Zamanı" sütunları GG/AA/YYYY (gün önce) biçiminde
    geldiğinden düz metin sıralaması AY'ı yok sayıp yalnız GÜN basamağına
    bakıyordu (kullanıcı bulgusu, 2026-08-03: eskiden-yeniye sıralamada
    '01/07' '25/02'den önceymiş gibi görünüyordu — ASCII sıralamada '0' < '2'
    olduğundan gün ay'dan bağımsız karşılaştırılıyordu). `re.search` kullanır
    (yalnız `re.match` DEĞİL) — böylece metnin BAŞINDA olması gerekmez,
    sondaki fazladan metni de tolere eder. Ayrıştırılamazsa boş dize döner
    (en küçük/en eski sayılır — `_evrak_tarihi_anahtari` ile AYNI disiplin)."""
    m = _TARIH_SAAT_RE.search(str(deger or ""))
    if not m:
        return ""
    gg, aa, yyyy, ss, dd = m.groups()
    return f"{yyyy}{aa}{gg}{ss or '00'}{dd or '00'}"


class BarkodSorguPanel:
    # ── Bölüm 1: dosya seçici (yalnız İcra — dosyalarim_genel'in İcra-daraltılmış hâli) ──
    # Kullanıcı bulgusu (2026-08-03, dördüncü tur): dosya seçip sorguladıktan
    # sonra sonucu görmek için ayrıca aşağıdaki "Geçmiş Sonuçlar" bölümüne
    # bakmak gerekiyordu — bu sütunlar artık BURADA da gösterilir (dosya
    # başına EN SON TebligatBarkod kaydı, bkz. `_barkod_ozetini_birlestir`).
    # "Tebligat Türü" (beşinci tur) barkod_sorgu._tebligat_turunu_belirle
    # ile sınıflandırılır (Ödeme Emri/Banka/Maaş/103 Davetiyesi/...).
    KOLONLAR = [
        ("birimAdi", "İcra Dairesi", 220),
        ("dosyaNo", "Dosya No", 90),
        ("dosyaTur", "Dosya Türü", 110),
        ("dosyaDurum", "Durum", 70),
        ("acilisTarihi", "Açılış Tarihi", 90),
        ("alacakli", "Alacaklı", 200),
        ("borclu", "Borçlu", 200),
        ("barkod", "Barkod No", 110),
        ("tebligatTuru", "Tebligat Türü", 160),
        ("pttDurumu", "Tebliğ / Son Durum", 180),
        ("sonIslemTarihi", "Tebliğ/İade Tarihi", 110),
        ("tebligMazbatasiVar", "Mazbata Döndü mü", 110),
        ("yenidenTebligTalebi", "Yeniden Tebliğ Talebi", 130),
    ]
    HEAD_H = 52

    # ── Bölüm 2: geçmiş TebligatBarkod sonuçları ──
    GECMIS_KOLONLAR = [
        ("birimAdi", "İcra Dairesi", 200),
        ("dosyaNo", "Dosya No", 90),
        ("barkod", "Barkod", 120),
        ("tebligatTuru", "Tebligat Türü", 160),
        ("elektronikTebligat", "Elektronik mi", 90),
        ("pttDurumu", "PTT Durumu", 200),
        ("sonIslemTarihi", "Son İşlem Tarihi", 110),
        ("tebligMazbatasiVar", "Tebliğ Mazbatası", 100),
        ("kapaliTebligMazbatasiVar", "Kapalı Mazbata", 100),
        ("yenidenTebligTalebi", "Yeniden Tebliğ Talebi", 130),
        ("sorguZamani", "Sorgu Zamanı", 130),
    ]
    GECMIS_HEAD_H = 30

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Barkod Sorgulama")
        self.result_q = queue.Queue()

        self.kayitlar_ham = []   # Bölüm 1: DB'den gelen (sunucu filtreli) TAM liste
        self.kayitlar = []       # Bölüm 1: ekrandaki (yerel filtre+sıralama uygulanmış) liste
        self._by_uid = {}        # Bölüm 1: _uid -> kayıt (Treeview iid'si buradan üretilir)
        self.col_vars = {k: tk.StringVar() for k, _l, _w in self.KOLONLAR}
        self.col_combos = {}
        self.col_labels = {}
        self.sort_key = None
        self.sort_reverse = False

        self.gecmis_ham = []     # Bölüm 2
        self.gecmis = []
        self.gecmis_sort_key = None
        self.gecmis_sort_reverse = False

        self.birim_var = tk.StringVar(value="Tümü")
        self.durum_var = tk.StringVar(value="Tümü")
        self.tarih_bas_var = tk.StringVar()
        self.tarih_bit_var = tk.StringVar()
        self.alacakli_var = tk.StringVar()
        self.borclu_var = tk.StringVar()
        self._birim_kod = {}
        self._durum_kod = {}

        self._calisiyor = False

        self._init_style()
        self._build()
        threading.Thread(target=self._alanlar_yukle_bg, daemon=True).start()
        self.app.after(200, self._poll)
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_dongusu(self):
        """20 sn'de bir mevcut filtreyle YEREL veritabanını (dosya seçici
        bölümü) sessizce yeniden okur (UYAP'a GİTMEZ) — kullanıcı isteği
        (2026-08-14): bir modülde yapılan güncelleme diğerinde ELLE
        Filtrele'ye basmadan görünsün. Canlı bir Sorgula/Barkod işlemi
        SÜRERKEN (self._calisiyor) araya girmez."""
        try:
            if not self._calisiyor and self.parent.winfo_exists():
                threading.Thread(target=self._oto_yenile_bg,
                                 args=(self._filtreleri_topla(),), daemon=True).start()
        except Exception:
            pass
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_bg(self, filtreler):
        try:
            kayitlar = dosya_core.dosyalarim_db_listele(filtreler)
        except Exception:
            return
        # dosyalarim_db_listele HATA durumunda da [] döner — BOŞ sonuç
        # göndermiyoruz ki geçici bir DB kesintisi ekrandaki DOLU tabloyu
        # SESSİZCE boşaltmasın (kullanıcı bulgusu, 2026-08-14).
        if kayitlar:
            self._barkod_ozetini_birlestir(kayitlar)
            self.result_q.put(("kayitlar", kayitlar))

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Barkod.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.map("Barkod.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(28, 20))

        tk.Label(wrap, text="Barkod Sorgu (Kapalı Tebligat — PTT)", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Aşağıdan İcra dosyalarınızı (yerel veritabanından, "
                            "\"Dosyalarım (Tümü)\" ile aynı kaynaktan) filtreleyin. "
                            "\"Tümünü Sorgula\" filtrelenmiş listedeki TÜM dosyaları tek "
                            "seferde sorgular — tek tek satır seçmenize gerek yok. Belirli "
                            "dosyaları seçip sorgulamak isterseniz Ctrl/Shift ile satır(lar) "
                            "seçip \"Seçilenleri Sorgula\"yı kullanın. Her iki yolda da her "
                            "dosyadaki 'Kapalı Tebligat' evrakının barkodu PTT'de sorgulanır "
                            "ve sonuç veritabanına kaydedilir.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=820,
                 justify="left").pack(anchor="w", pady=(6, 14))

        # ── Bölüm 1: filtre çubuğu ──
        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        self.birim_cb = self._combo(ui, "İcra Dairesi", self.birim_var, ["Tümü"])
        self.birim_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.durum_cb = self._combo(ui, "Durum", self.durum_var, ["Tümü"])
        self.durum_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())

        tk.Label(ui, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui, self.tarih_bas_var)
        tk.Label(ui, text="–", bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui, self.tarih_bit_var)

        tk.Label(ui, text="Alacaklı", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        alacakli_e = tk.Entry(ui, textvariable=self.alacakli_var, bg="#FFFFFF", fg=C.INK,
                              relief="flat", insertbackground=C.INK, font=self.app.f_body,
                              width=16, highlightthickness=1, highlightbackground=C.LINE,
                              highlightcolor=C.SAGE)
        alacakli_e.pack(side="left", ipady=3)
        alacakli_e.bind("<Return>", lambda ev: self._filtrele())

        tk.Label(ui, text="Borçlu", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        borclu_e = tk.Entry(ui, textvariable=self.borclu_var, bg="#FFFFFF", fg=C.INK,
                            relief="flat", insertbackground=C.INK, font=self.app.f_body,
                            width=16, highlightthickness=1, highlightbackground=C.LINE,
                            highlightcolor=C.SAGE)
        borclu_e.pack(side="left", ipady=3)
        borclu_e.bind("<Return>", lambda ev: self._filtrele())

        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self._btn(bar, "Filtrele", self._filtrele, "primary").pack(side="left", ipadx=6)
        self._btn(bar, "Temizle", self._temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.tumunu_sorgula_btn = self._btn(bar, "Tümünü Sorgula", self._tumunu_sorgula, "primary")
        self.tumunu_sorgula_btn.pack(side="left", padx=(8, 0), ipadx=6)
        self.sorgula_btn = self._btn(bar, "Seçilenleri Sorgula", self._secilenleri_sorgula, "ghost")
        self.sorgula_btn.pack(side="left", padx=(8, 0), ipadx=2)
        # Dosya seçip TOPLU 21/2 uygunluk taraması (kullanıcı isteği,
        # 2026-08-13 — "barkod sorgu menüsünde dosya seçerek toplu 21/2 talep
        # etme butonu"): YALNIZ tarar/raporlar, hiçbir talep GÖNDERMEZ/ÖDEME
        # YAPMAZ (bkz. teblig_21_2_core.py modül başlığı). Yukarıdaki dosya
        # seçici tablodan (Bölüm 1) seçilen satırlar üzerinde çalışır —
        # Geçmiş bölümündeki (Bölüm 2, aşağıda) aynı işlevin ayrı bir girişi.
        self.t212_btn1 = self._btn(bar, "21/2 Uygunluk Taraması (Seçili Dosyalar)",
                                    self._t212_tara_bolum1, "ghost")
        self.t212_btn1.pack(side="left", padx=(8, 0), ipadx=2)
        # Kullanıcı bulgusu (2026-08-13): tarama sonucu penceresi kapatılınca
        # tekrar görmek için sıfırdan sorgu gerekiyordu — sonuçlar artık HER
        # taramada veritabanına da yazılıyor (bkz. teblig_21_2_core.
        # son_tarama_sonuclarini_getir), bu buton yeniden sorgu ATMADAN,
        # DB'den okuyarak açar; program yeniden başlasa bile kaybolmaz.
        self.t212_rapor_btn1 = self._btn(bar, "Son 21/2 Raporu", self._t212_son_raporu_goster, "ghost")
        self.t212_rapor_btn1.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn.set_state("disabled")
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durdur_btn.set_state("disabled")
        self.excel_btn = self._btn(bar, "Excel'e Aktar", self._excel_aktar, "ghost")
        self.excel_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(14, 0))
        self.head_canvas = tk.Canvas(card, bg=C.SIDEBAR, highlightthickness=0, height=self.HEAD_H)
        self.head_inner = tk.Frame(self.head_canvas, bg=C.SIDEBAR)
        self.head_win = self.head_canvas.create_window((0, 0), window=self.head_inner, anchor="nw")
        self.head_canvas.grid(row=0, column=0, sticky="ew")

        cols = [k for k, _l, _w in self.KOLONLAR]
        self.tree = ttk.Treeview(card, columns=cols, show="", height=9,
                                 selectmode="extended", style="Barkod.Treeview")
        self.tree.grid(row=1, column=0, sticky="nsew")
        ysb = tk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        xsb = tk.Scrollbar(card, orient="horizontal")
        xsb.grid(row=2, column=0, sticky="ew")

        def _xset(first, last):
            xsb.set(first, last)
            try:
                self.head_canvas.xview_moveto(float(first))
            except Exception:
                pass
        xsb.configure(command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=_xset)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        self._basligi_kur()

        # ── günlük ──
        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(10, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=4,
                           padx=12, pady=6, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

        # ── Bölüm 2: geçmiş sonuçlar ──
        tk.Label(wrap, text="Geçmiş Barkod Sorgu Sonuçları", bg=C.BG, fg=C.INK,
                 font=self.app.f_card_t).pack(anchor="w", pady=(20, 4))
        tk.Label(wrap, text="Veritabanına daha önce kaydedilmiş sonuçlar (barkod/PTT durumu/mazbata).",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small).pack(anchor="w", pady=(0, 8))

        gbar = tk.Frame(wrap, bg=C.BG)
        gbar.pack(fill="x")
        self.gecmis_yenile_btn = self._btn(gbar, "Geçmişi Yenile", self._gecmis_yenile, "ghost")
        self.gecmis_yenile_btn.pack(side="left", ipadx=2)
        # 21/2 şerhli yeniden tebliğ talebi UYGUNLUK TARAMASI (kullanıcı isteği,
        # 2026-08-13) — YALNIZ tarar/raporlar, hiçbir talep GÖNDERMEZ/ÖDEME
        # YAPMAZ (bkz. teblig_21_2_core.py modül başlığı). Seçili "İADE"
        # satırları üzerinde çalışır.
        self.t212_btn = self._btn(gbar, "21/2 Uygunluk Taraması", self._t212_tara, "ghost")
        self.t212_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.gecmis_durum_lbl = tk.Label(gbar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.gecmis_durum_lbl.pack(side="right")

        gcard = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        gcard.pack(fill="both", expand=True, pady=(8, 0))
        gcols = [k for k, _l, _w in self.GECMIS_KOLONLAR]
        self.gecmis_tree = ttk.Treeview(gcard, columns=gcols, show="headings", height=8,
                                        style="Barkod.Treeview")
        for k, lbl, w in self.GECMIS_KOLONLAR:
            self.gecmis_tree.heading(k, text=lbl, command=lambda col=k: self._gecmis_sirala(col))
            self.gecmis_tree.column(k, width=w, anchor="w", stretch=False)
        self.gecmis_tree.grid(row=0, column=0, sticky="nsew")
        gysb = tk.Scrollbar(gcard, orient="vertical", command=self.gecmis_tree.yview)
        gysb.grid(row=0, column=1, sticky="ns")
        gxsb = tk.Scrollbar(gcard, orient="horizontal", command=self.gecmis_tree.xview)
        gxsb.grid(row=1, column=0, sticky="ew")
        self.gecmis_tree.configure(yscrollcommand=gysb.set, xscrollcommand=gxsb.set)
        gcard.rowconfigure(0, weight=1)
        gcard.columnconfigure(0, weight=1)

    def _combo(self, parent, label, var, values):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", values=values,
                          font=self.app.f_body, width=18)
        cb.pack(side="left", padx=(0, 18))
        return cb

    def _tarih_entry(self, parent, var):
        e = tk.Entry(parent, textvariable=var, bg="#FFFFFF", fg=C.INK, relief="flat",
                    insertbackground=C.INK, font=self.app.f_body, width=11,
                    highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda ev: self._filtrele())
        return e

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind, font=self.app.f_nav_b, height=36)

    # ─────────────────────────── Bölüm 1: başlık şeridi (canlı filtre + sıralama) ──
    # dosyalarim_genel.py'deki AYNI desen (bkz. modül başlığı — bilerek kopyalandı).
    def _basligi_kur(self):
        for w in self.head_inner.winfo_children():
            w.destroy()
        self.col_combos = {}
        self.col_labels = {}
        total = 0
        for i, (k, lbl, w) in enumerate(self.KOLONLAR):
            total += w
            cell = tk.Frame(self.head_inner, bg=C.SIDEBAR, width=w, height=self.HEAD_H)
            cell.grid(row=0, column=i, sticky="nsew")
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)

            baslik = tk.Label(cell, text=lbl, bg=C.SIDEBAR, fg=C.INK,
                              font=self.app.f_nav_b, anchor="w", cursor="hand2")
            baslik.grid(row=0, column=0, sticky="ew", padx=6, pady=(5, 2))
            baslik.bind("<Button-1>", lambda e, col=k: self._sirala(col))
            self.col_labels[k] = (baslik, lbl)

            cb = ttk.Combobox(cell, textvariable=self.col_vars[k], state="normal",
                              font=self.app.f_body, width=4)
            cb.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
            cb.bind("<KeyRelease>", lambda ev: self._yerel_uygula())
            cb.bind("<<ComboboxSelected>>", lambda ev: self._yerel_uygula())
            cb.bind("<Return>", lambda ev: self._yerel_uygula())
            self.col_combos[k] = cb

            self.tree.column(k, width=w, anchor="w", stretch=False)
        self._sirala_etiket_guncelle()
        self.head_canvas.itemconfigure(self.head_win, width=total, height=self.HEAD_H)
        self.head_canvas.configure(scrollregion=(0, 0, total, self.HEAD_H))

    def _yerel_filtreleri_sifirla(self):
        for var in self.col_vars.values():
            var.set("")
        self.sort_key = None
        self.sort_reverse = False
        self._sirala_etiket_guncelle()

    def _sirala_etiket_guncelle(self):
        for k, (widget, taban) in self.col_labels.items():
            ok = ""
            if k == self.sort_key:
                ok = "  ▲" if not self.sort_reverse else "  ▼"
            widget.config(text=taban + ok)

    def _sirala(self, col):
        if self.sort_key == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = col
            self.sort_reverse = False
        self._sirala_etiket_guncelle()
        self._yerel_uygula()

    def _combolari_doldur(self):
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is None:
                continue
            degerler = sorted({str(r.get(k, "") or "") for r in self.kayitlar_ham} - {""},
                               key=lambda s: dosya_core.tr_lower(s))
            cb["values"] = degerler

    def _yerel_kriterler(self):
        out = {}
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is not None:
                q = dosya_core.tr_lower(cb.get().strip())
                if q:
                    out[k] = q
        return out

    def _yerel_uygula(self):
        kayitlar = self.kayitlar_ham
        kriter = self._yerel_kriterler()
        if kriter:
            def uyar(r):
                for k, q in kriter.items():
                    if q not in dosya_core.tr_lower(str(r.get(k, "") or "")):
                        return False
                return True
            kayitlar = [r for r in kayitlar if uyar(r)]
        if self.sort_key:
            col = self.sort_key
            if col == "acilisTarihi":
                def gecerli_mi(r):
                    m = _ACILIS_TARIHI_RE.match(str(r.get(col, "") or "").strip())
                    return f"{m.group(3)}{m.group(2)}{m.group(1)}" if m else None
            elif col == "sonIslemTarihi":
                def gecerli_mi(r):
                    return _tarih_saat_anahtari(r.get(col, "")) or None
            else:
                def gecerli_mi(r):
                    v = str(r.get(col, "") or "").strip()
                    if not v:
                        return None
                    try:
                        return (0, float(v.replace(",", ".")))
                    except ValueError:
                        return (1, dosya_core.tr_lower(v))
            gecerliler, gecersizler = [], []
            for r in kayitlar:
                a = gecerli_mi(r)
                (gecersizler if a is None else gecerliler).append((a, r))
            gecerliler.sort(key=lambda p: p[0], reverse=self.sort_reverse)
            kayitlar = [r for _a, r in gecerliler] + [r for _a, r in gecersizler]

        self.kayitlar = kayitlar
        secili_iid = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for rec in kayitlar:
            vals = [rec.get(k, "") for k, _l, _w in self.KOLONLAR]
            iid = str(rec["_uid"])
            self.tree.insert("", "end", iid=iid, values=vals)
            if iid in secili_iid:
                self.tree.selection_add(iid)
        toplam = len(self.kayitlar_ham)
        if not toplam:
            self.durum_lbl.config(text="")
        elif len(kayitlar) != toplam:
            self.durum_lbl.config(text=f"{len(kayitlar)} / {toplam} dosya")
        else:
            self.durum_lbl.config(text=f"{toplam} dosya")

    # ─────────────────────────── Bölüm 2: sıralama ───────────────────────────
    def _gecmis_sirala(self, col):
        if self.gecmis_sort_key == col:
            self.gecmis_sort_reverse = not self.gecmis_sort_reverse
        else:
            self.gecmis_sort_key = col
            self.gecmis_sort_reverse = False
        self._gecmis_uygula()

    def _gecmis_uygula(self):
        kayitlar = list(self.gecmis_ham)
        if self.gecmis_sort_key:
            col = self.gecmis_sort_key
            if col in ("sorguZamani", "sonIslemTarihi"):
                def anahtar(r):
                    return _tarih_saat_anahtari(r.get(col, ""))
            else:
                def anahtar(r):
                    return dosya_core.tr_lower(str(r.get(col, "") or ""))
            kayitlar.sort(key=anahtar, reverse=self.gecmis_sort_reverse)
        self.gecmis = kayitlar
        self.gecmis_tree.delete(*self.gecmis_tree.get_children())
        for i, rec in enumerate(kayitlar):
            vals = [rec.get(k, "") for k, _l, _w in self.GECMIS_KOLONLAR]
            self.gecmis_tree.insert("", "end", iid=str(i), values=vals)
        for k, lbl, _w in self.GECMIS_KOLONLAR:
            ok = ""
            if k == self.gecmis_sort_key:
                ok = "  ▲" if not self.gecmis_sort_reverse else "  ▼"
            self.gecmis_tree.heading(k, text=lbl + ok)
        self.gecmis_durum_lbl.config(text=f"{len(kayitlar)} sonuç" if kayitlar else "")

    # ─────────────────────────── günlük ───────────────────────────
    def _log(self, mesaj):
        self.result_q.put(("log", str(mesaj)))

    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ─────────────────────────── veri yükleme: Bölüm 1 ───────────────────────────
    def _alanlar_yukle_bg(self):
        try:
            birimler = dosya_core.mahkeme_secenekleri(dosya_core.YARGI_TURU_ICRA, None)
            durumlar = dosya_core.dosya_durum_secenekleri()
        except Exception as e:
            self._log(f"⚠️ Alanlar yüklenemedi: {e}")
            birimler, durumlar = [], []
        self.result_q.put(("alanlar", (birimler, durumlar)))
        self._filtrele_bg({})
        self._gecmis_yukle_bg()

    def _filtreleri_topla(self):
        f = {"yargi_turu": dosya_core.YARGI_TURU_ICRA}
        birim_ad = self.birim_var.get()
        if birim_ad != "Tümü":
            f["mahkeme_id"] = self._birim_kod.get(birim_ad)
        durum_ad = self.durum_var.get()
        if durum_ad != "Tümü":
            f["durum_kod"] = self._durum_kod.get(durum_ad)
        if self.tarih_bas_var.get().strip():
            f["tarih_baslangic"] = self.tarih_bas_var.get().strip()
        if self.tarih_bit_var.get().strip():
            f["tarih_bitis"] = self.tarih_bit_var.get().strip()
        if self.alacakli_var.get().strip():
            f["alacakli_adi"] = self.alacakli_var.get().strip()
        if self.borclu_var.get().strip():
            f["borclu_adi"] = self.borclu_var.get().strip()
        return f

    def _filtrele(self):
        self.durum_lbl.config(text="Yükleniyor…")
        self._filtrele_bg(self._filtreleri_topla())

    def _filtrele_bg(self, filtreler):
        def bg():
            try:
                kayitlar = dosya_core.dosyalarim_db_listele(filtreler)
            except Exception as e:
                self._log(f"⚠️ Liste okunamadı: {e}")
                kayitlar = []
            self._barkod_ozetini_birlestir(kayitlar)
            self.result_q.put(("kayitlar", kayitlar))
        threading.Thread(target=bg, daemon=True).start()

    def _barkod_ozetini_birlestir(self, kayitlar):
        """Her kayda (dosyaya) EN SON TebligatBarkod sonucunu (barkod/PTT
        durumu/tarih/mazbata) ekler — kullanıcı bulgusu (2026-08-03, dördüncü
        tur): bu bilgi ayrı bir "Geçmiş Sonuçlar" bölümündeydi, dosya seçici
        listede de görünsün istendi.

        KULLANICI BULGUSU (2026-08-03, altıncı tur — CANLI DB'de doğrulandı):
        önceki sürüm "dosya başına `tebligat_barkod_gecmisi_listele`'nin
        (-sorgu_zamani sıralı) döndürdüğü İLK kayıt en günceldir" varsayımına
        dayanıyordu. Ama bir dosyada birden fazla 'Kapalı Tebligat' evrakı
        varsa (iade sonrası yeniden tebligat — HEPSİ ayrı birim_evrak_no ile
        DB'ye AYRI satır olarak zaten doğru yazılıyor, bkz. barkod_sorgu.py),
        toplu taramada bu evraklar UYAP'tan hangi sırayla gelirse DB'ye o
        sırayla yazılıyor; gerçek dosyalarda (ör. dosyaId=270) bu sıra
        KRONOLOJİK DEĞİL — en eski/iade olmuş evrak birkaç milisaniye DAHA
        GEÇ yazıldığından `sorgu_zamani`'ye göre yanlışlıkla "en son" seçilip
        ekranda İADE takılı kalıyordu, oysa asıl en yeni tebligat teslim
        edilmişti. Artık dosya başına evrak TARİHİ (`evrakTarihi`, GG/AA/YYYY
        — bkz. `_evrak_tarihi_anahtari`) en yeni olan kayıt seçilir; tarih
        ayrıştırılamazsa o kayıt en eski sayılır, başka bir kaydı EZMEZ.
        `kayitlar` YERİNDE değiştirilir."""
        if not kayitlar:
            return
        try:
            gecmis = dosya_core.tebligat_barkod_gecmisi_listele()
        except Exception as e:
            self._log(f"⚠️ Barkod özeti yüklenemedi: {e}")
            gecmis = []
        ozet = {}
        for g in gecmis:
            did = g.get("dosyaId")
            if did is None:
                continue
            mevcut = ozet.get(did)
            if mevcut is None or (_evrak_tarihi_anahtari(g.get("evrakTarihi"))
                                   > _evrak_tarihi_anahtari(mevcut.get("evrakTarihi"))):
                ozet[did] = g
        for r in kayitlar:
            g = ozet.get(r.get("dosyaId"))
            r["barkod"] = g.get("barkod", "") if g else ""
            r["tebligatTuru"] = g.get("tebligatTuru", "") if g else ""
            r["pttDurumu"] = g.get("pttDurumu", "") if g else ""
            r["sonIslemTarihi"] = g.get("sonIslemTarihi", "") if g else ""
            r["tebligMazbatasiVar"] = g.get("tebligMazbatasiVar", "") if g else ""
            r["yenidenTebligTalebi"] = g.get("yenidenTebligTalebi", "") if g else ""

    def _temizle(self):
        self.birim_var.set("Tümü")
        self.durum_var.set("Tümü")
        self.tarih_bas_var.set("")
        self.tarih_bit_var.set("")
        self.alacakli_var.set("")
        self.borclu_var.set("")
        self._yerel_filtreleri_sifirla()
        self._filtrele()

    def _tabloyu_doldur(self, kayitlar):
        # Treeview satır kimliği (iid) DAHA ÖNCE listedeki SIRA konumuydu
        # (str(i)) — bu, kullanıcı satır(lar) seçtikten SONRA bir sütun
        # filtresine yazdığında `_yerel_uygula` yeniden çizerken aynı konuma
        # düşen FARKLI bir kaydı "seçili" gösteriyordu (kullanıcı bulgusu,
        # 2026-08-03: filtrelemeden sonra "Seçilenleri Sorgula" yalnızca tek
        # dosyayı işliyordu). Bunun yerine kayda SABİT bir kimlik (`_uid`,
        # ham listedeki özgün konum) verilip iid HER ZAMAN bu kimlikten
        # üretilir — filtre/sıralama kaydın iid'sini asla değiştirmez.
        for i, r in enumerate(kayitlar):
            r["_uid"] = i
        self.kayitlar_ham = kayitlar
        self._by_uid = {r["_uid"]: r for r in kayitlar}
        self._combolari_doldur()
        self._yerel_uygula()

    # ─────────────────────────── Excel'e Aktar (Bölüm 1) ───────────────────────────
    # dosyalarim_genel.py'deki AYNI desen (bkz. modül başlığı — bilerek kopyalandı).
    def _excel_aktar(self):
        """O an EKRANDA GÖRÜNEN listeyi (sütun-başlığı filtreleri + sıralama
        DAHİL — `self.kayitlar`, Barkod/PTT/Mazbata sütunları dahil) Excel'e
        yazar."""
        if not self.kayitlar:
            self.durum_lbl.config(text="Aktarılacak kayıt yok — önce filtreleyin.")
            return
        varsayilan_ad = f"Barkod_Sorgu_Dosyalar_{time.strftime('%Y%m%d_%H%M')}.xlsx"
        yol = filedialog.asksaveasfilename(
            title="Excel'e Aktar", defaultextension=".xlsx",
            initialfile=varsayilan_ad, filetypes=[("Excel", "*.xlsx")])
        if not yol:
            return
        self.excel_btn.set_state("disabled")
        self.durum_lbl.config(text="Excel'e yazılıyor…")
        threading.Thread(target=self._excel_aktar_bg, args=(yol, list(self.kayitlar)), daemon=True).start()

    def _excel_aktar_bg(self, yol, kayitlar):
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Barkod Sorgu Dosyalar"
            ws.append([lbl for _k, lbl, _w in self.KOLONLAR])
            for rec in kayitlar:
                ws.append([rec.get(k, "") for k, _l, _w in self.KOLONLAR])
            for i, (_k, _l, w) in enumerate(self.KOLONLAR, start=1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(10, min(w // 6, 45))
            wb.save(yol)
        except PermissionError:
            self.result_q.put(("excel_hata", "Dosya başka bir programda (ör. Excel'de) açık olabilir, kapatıp tekrar deneyin."))
            return
        except Exception as e:
            self.result_q.put(("excel_hata", str(e)))
            return
        self.result_q.put(("excel_tamam", (yol, len(kayitlar))))

    def _excel_aktarildi(self, veri):
        yol, adet = veri
        self.excel_btn.set_state("normal")
        self.durum_lbl.config(text=f"✔ {adet} kayıt Excel'e aktarıldı: {os.path.basename(yol)}")
        try:
            os.startfile(yol)
        except Exception:
            pass

    def _excel_hata(self, hata):
        self.excel_btn.set_state("normal")
        self.durum_lbl.config(text=f"Excel'e aktarılamadı: {hata}")

    # ─────────────────────────── veri yükleme: Bölüm 2 ───────────────────────────
    def _gecmis_yenile(self):
        self.gecmis_durum_lbl.config(text="Yükleniyor…")
        self._gecmis_yukle_bg()

    def _gecmis_yukle_bg(self):
        def bg():
            try:
                kayitlar = dosya_core.tebligat_barkod_gecmisi_listele()
            except Exception as e:
                self._log(f"⚠️ Geçmiş okunamadı: {e}")
                kayitlar = []
            self.result_q.put(("gecmis", kayitlar))
        threading.Thread(target=bg, daemon=True).start()

    # ───────────────── 21/2 Uygunluk Taraması (yalnız tarar/raporlar) ─────────────────
    # İki giriş noktası var: Bölüm 1 (dosya seçici, yukarıda) ve Bölüm 2
    # (Geçmiş/İADE listesi, aşağıda) — ikisi de AYNI arka plan taramasını
    # (teblig_21_2_core.iade_tarama) çağırır, yalnız seçili satırların
    # kaynağı farklıdır. `iade_tarama` zaten yalnız `ptt_durumu="İADE"` olan
    # adayları işlediğinden Bölüm 1'den (genel dosya listesi) seçilen
    # kayıtlar arasında İADE olmayanlar taramada otomatik elenir.
    def _t212_tara_bolum1(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(
                text="Önce listeden en az bir dosya seçin (İADE tebligatlı olanlar taranır).")
            return
        try:
            secili_kayitlar = [self._by_uid[int(iid)] for iid in secim]
        except (ValueError, KeyError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        self._t212_baslat(secili_kayitlar, self.t212_btn1, self.durum_lbl)

    def _t212_tara(self):
        secim = self.gecmis_tree.selection()
        if not secim:
            self.gecmis_durum_lbl.config(
                text="Önce geçmiş listeden en az bir dosya seçin (İADE olanlar taranır).")
            return
        try:
            secili = [self.gecmis[int(iid)] for iid in secim]
        except (ValueError, IndexError):
            self.gecmis_durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        self._t212_baslat(secili, self.t212_btn, self.gecmis_durum_lbl)

    def _t212_baslat(self, secili, btn, durum_lbl):
        self._t212_btns = (self.t212_btn1, self.t212_btn)
        for b in self._t212_btns:
            b.set_state("disabled")
        durum_lbl.config(text="21/2 uygunluk taraması yapılıyor… (canlı UYAP sorguları)")
        threading.Thread(target=self._t212_tara_bg, args=(secili,), daemon=True).start()

    def _t212_tara_bg(self, secili):
        try:
            sonuc = teblig_21_2_core.iade_tarama(secili_kayitlar=secili, log_fn=self._log)
        except Exception as e:
            self.result_q.put(("t212_hata", str(e)))
            return
        self.result_q.put(("t212_sonuc", sonuc))

    def _t212_hata(self, hata):
        for b in getattr(self, "_t212_btns", (self.t212_btn1, self.t212_btn)):
            b.set_state("normal")
        self.gecmis_durum_lbl.config(text=f"Tarama sırasında hata: {hata}")
        self.durum_lbl.config(text=f"Tarama sırasında hata: {hata}")

    def _t212_sonuc_goster(self, sonuc):
        for b in getattr(self, "_t212_btns", (self.t212_btn1, self.t212_btn)):
            b.set_state("normal")
        self.gecmis_durum_lbl.config(text=f"Tarama tamam: {len(sonuc)} sonuç")
        self.durum_lbl.config(text=f"Tarama tamam: {len(sonuc)} sonuç")
        if not sonuc:
            return
        self._t212_rapor_penceresi_ac(sonuc)

    def _t212_son_raporu_goster(self):
        # Kullanıcı bulgusu (2026-08-14): "son 21/2 raporu silindi" — eskiden
        # bu buton yalnız Panel BELLEĞİNDEKİ son sonucu gösteriyordu, program
        # yeniden başlayınca kayboluyordu. Artık veritabanından okur (bkz.
        # teblig_21_2_core.son_tarama_sonuclarini_getir — iade_tarama HER
        # taramada oraya yazıyor) — kalıcı, yeniden başlatmadan etkilenmez.
        self.durum_lbl.config(text="Son 21/2 raporu yükleniyor…")

        def bg():
            try:
                sonuc = teblig_21_2_core.son_tarama_sonuclarini_getir()
            except Exception as e:
                self.result_q.put(("t212_son_rapor_hata", str(e)))
                return
            self.result_q.put(("t212_son_rapor", sonuc))
        threading.Thread(target=bg, daemon=True).start()

    def _t212_son_rapor_goster(self, sonuc):
        if not sonuc:
            self.durum_lbl.config(text="Veritabanında henüz bir 21/2 tarama sonucu yok; önce tarama yapın.")
            return
        self.durum_lbl.config(text=f"Son 21/2 raporu yüklendi: {len(sonuc)} sonuç (veritabanından).")
        self._t212_rapor_penceresi_ac(sonuc)

    def _t212_rapor_penceresi_ac(self, sonuc):
        top = tk.Toplevel(self.app)
        top.title("21/2 Uygunluk Tarama Sonuçları")
        top.configure(bg=C.CARD)
        top.transient(self.app)
        top.geometry("1180x480")

        tk.Label(top, text="Bu pencere YALNIZ bir rapordur — hiçbir talep gönderilmedi, "
                            "ücret harcanmadı.", bg=C.CARD, fg=C.CLAY,
                 font=self.app.f_card_t).pack(anchor="w", padx=16, pady=(14, 8))

        kolonlar = [
            ("birim", "İcra Dairesi", 200), ("dosyaNo", "Dosya No", 90),
            ("borclu", "Borçlu", 180), ("iadeTarihi", "İade Tarihi", 90),
            ("kategori", "Sonuç", 230),
            ("tebligAdresi", "Tebligat Adresi", 220), ("mernisAdresi", "Mernis Adresi", 220),
        ]
        cols = [k for k, _l, _w in kolonlar]
        tree = ttk.Treeview(top, columns=cols, show="headings", height=14)
        for k, lbl, w in kolonlar:
            tree.heading(k, text=lbl)
            tree.column(k, width=w, anchor="w", stretch=False)
        tree.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        ysb = tk.Scrollbar(top, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)

        for i, s in enumerate(sonuc):
            tree.insert("", "end", iid=str(i), values=[s.get(k, "") for k in cols])

        # GERÇEK gönderim (kullanıcı isteği, 2026-08-13): rapor penceresinden
        # "21/2 için uygun" satırlar seçilip GERÇEKTEN gönderilebilsin —
        # bkz. teblig_21_2_gonder.py (ücretli, ayrı bir modül; bu rapor
        # penceresi ve teblig_21_2_core.py hâlâ yalnız TARAR/raporlar).
        #
        # Kullanıcı bulgusu (2026-08-14, ilk tur): eskiden bu AYRI bir
        # Toplevel açıyordu; o pencere bu rapor penceresinin ARKASINDA kalıp
        # "kayboluyor" gibi görünmüştü — kullanıcı "olmadı" sanıp butona
        # TEKRAR TEKRAR bastı, aynı dosya için 6 ayrı gönderim işi sıraya
        # girdi (hiçbiri onaylanmadığı için ücret harcanmadı, ama karışıklık
        # büyüktü). Artık AYRI pencere YOK — gönderim paneli bu pencerenin
        # İÇİNE, alt tarafa gömülür.
        #
        # Kullanıcı bulgusu (2026-08-14, ikinci tur): buton bir gönderimden
        # sonra KALICI olarak kilitli kalıyordu — başka bir dosya seçip
        # TEKRAR gönderilemiyordu. Artık yalnız o AN çalışan bir iş varken
        # kilitli (bkz. _bitti — iş done/error/cancelled olunca yeniden
        # aktif eder); aynı anda İKİ iş aynı pencereden başlatılamaz ama
        # ardışık gönderimler serbesttir.
        #
        # Kullanıcı bulgusu (2026-08-14, üçüncü tur): "gönderildi ama Barkod
        # Sorgu tablosunda Yeniden Tebliğ Talebi hâlâ Yok görünüyor, rapor
        # penceresinde de hâlâ '21/2 için uygun' (yapılacak iş) gibi
        # duruyor" — _bitti artık (a) yerel DB'yi tazeler (bkz.
        # teblig_21_2_core.dosyalari_gonderildi_isaretle — CANLI SORGU
        # ATMAZ, finalize'ın kendi başarı yanıtına güvenir), (b) Barkod
        # Sorgu ekranının HER İKİ tablosunu (Bölüm 1 + Geçmiş) tazeler,
        # (c) BU rapor penceresindeki satırın kategorisini "gönderildi"
        # olarak günceller ki yapılmış iş, yapılacak iş gibi görünüp kafa
        # karıştırmasın.
        alt = tk.Frame(top, bg=C.CARD)
        alt.pack(fill="x", padx=16, pady=(0, 8))

        gonder_kapsayici = tk.Frame(top, bg=C.CARD)
        gonder_kapsayici.pack(fill="both", expand=False, padx=16, pady=(0, 14))

        gonderim_aktif = False

        def _bitti(sonuclar):
            nonlocal gonderim_aktif
            gonderim_aktif = False
            for b in _tum_gonder_btnlari():
                b.set_state("normal")

            gonderilenler = [s.get("ozet") or {} for s in sonuclar if s.get("durum") == "gonderildi"]
            if not gonderilenler:
                return
            gonderilen_anahtarlar = {(g.get("dosyaNo"), g.get("borclu")) for g in gonderilenler}
            for i, s in enumerate(sonuc):
                if (s.get("dosyaNo"), s.get("borclu")) in gonderilen_anahtarlar:
                    s["kategori"] = "✅ gönderildi"
                    try:
                        tree.item(str(i), values=[s.get(k, "") for k in cols])
                    except Exception:
                        pass
            try:
                n = teblig_21_2_core.dosyalari_gonderildi_isaretle(gonderilenler)
                # Sessiz "0 eşleşme" artık GÖRÜNÜR (kullanıcı bulgusu, 2026-08-17:
                # "gönderme çalıştı ama durum değişmedi" hiçbir hata/log OLMADAN
                # oluyordu — eşleşme 0 çıksa bile önceden sessizce geçilirdi).
                if n < len(gonderilenler):
                    self._log(f"⚠️ Yeniden Tebliğ Talebi: {len(gonderilenler)} gönderilen dosyadan "
                              f"yalnız {n} tanesi yerel DB'de eşleşti/güncellendi.")
            except Exception as e:
                self._log(f"⚠️ Yerel DB tazelenemedi (Yeniden Tebliğ Talebi): {e}")
            # Barkod Sorgu ekranının HER İKİ tablosu da (o anki filtre
            # KORUNARAK) tazelenir — kullanıcı "yenidenTebligTalebi"
            # sütununu hemen "Var" görsün diye (bkz. _sorgu_bitti — AYNI
            # tazeleme çağrıları, bulk sorgu tamamlanınca da kullanılıyor).
            self._gecmis_yukle_bg()
            self._filtrele_bg(self._filtreleri_topla())

        def _tum_gonder_btnlari():
            return (gonder_btn, yine_de_btn, normal_tebligat_btn, yine_de_normal_btn)

        def _gonderimi_baslat(kalemler, modul, on_bitti):
            nonlocal gonderim_aktif
            for b in _tum_gonder_btnlari():
                b.set_state("disabled")
            top.geometry("1180x780")
            basladi = modul.gonderim_baslat(
                gonder_kapsayici, self.app, kalemler, on_bitti=on_bitti)
            if basladi:
                gonderim_aktif = True
            else:
                for b in _tum_gonder_btnlari():
                    b.set_state("normal")
                top.geometry("1180x480")

        def _gonder_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            uygun = [s for s in secili if s.get("kategori") == "21/2 için uygun"]
            uygun_olmayan = len(secili) - len(uygun)
            if not uygun:
                messagebox.showinfo(
                    "Uygun satır yok",
                    "Seçtiğiniz satırların hiçbiri \"21/2 için uygun\" değil — yalnız bu "
                    "kategorideki satırlar gönderilebilir. Adresin aslında aynı olduğuna "
                    "eminseniz \"Yine de Gönder\" düğmesini kullanın.")
                return
            if uygun_olmayan:
                if not messagebox.askyesno(
                        "Bazı satırlar atlanacak",
                        f"Seçtiğiniz {len(secili)} satırdan {uygun_olmayan} tanesi "
                        "\"21/2 için uygun\" değil ve gönderilmeyecek. Kalan "
                        f"{len(uygun)} uygun dosya için devam edilsin mi?"):
                    return
            kalemler = [{"birim": s.get("birim", ""), "dosyaNo": s.get("dosyaNo", ""),
                        "borclu": s.get("borclu", "")} for s in uygun]
            _gonderimi_baslat(kalemler, teblig_21_2_gonder, _bitti)

        # "Yine de Gönder" (kullanıcı isteği, 2026-08-17): adres kıyaslaması
        # büyük/küçük harf ya da boşluk farkı gibi küçük biçim farklılıkları
        # yüzünden otomatik EŞLEŞMEYEBİLİR (bkz. teblig_21_2_core._adresler_
        # eslesiyor_mu — normalize eder ama HER varyasyonu YAKALAYAMAZ),
        # oysa adresler gözle bakıldığında GERÇEKTEN aynı olabilir. Bu düğme
        # "21/2 için uygun" DIŞINDAKİ (zaten gönderilmiş/başarıyla gönderilmiş
        # HARİÇ) seçili satırları, kullanıcı GÖZÜYLE kontrol edip onayladıktan
        # SONRA aynı gönderim işine (teblig_21_2_gonder) sokar — o iş kendi
        # başına, HER dosya için AYRICA "zaten gönderilmiş mi" diye CANLI
        # kontrol eder ve göndermeden önce yine TEK TEK kullanıcı onayı ister
        # (bkz. job_handlers._teblig_212_gonder) — yani bu düğme otomatik
        # adres kontrolünü ATLAR, ama tekrarlı/mükerrer gönderim ve son onay
        # güvenliğini ATLAMAZ.
        _GONDERILMEYECEK_KATEGORILER = {"zaten gönderilmiş", "✅ gönderildi",
                                        "✅ gönderildi (normal tebligat)"}

        def _yine_de_gonder_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            adaylar = [s for s in secili if s.get("kategori") not in _GONDERILMEYECEK_KATEGORILER]
            if not adaylar:
                messagebox.showinfo(
                    "Gönderilecek satır yok",
                    "Seçtiğiniz satırların tamamı zaten gönderilmiş görünüyor.")
                return
            if not messagebox.askyesno(
                    "Adres otomatik eşleşmedi — yine de gönderilsin mi?",
                    f"Seçtiğiniz {len(adaylar)} dosya \"21/2 için uygun\" olarak "
                    "İŞARETLENMEDİ (büyük/küçük harf, boşluk gibi bir biçim farkı "
                    "yüzünden olabilir). Tebligat Adresi ve Mernis Adresi sütunlarını "
                    "GÖZÜNÜZLE kontrol edip aynı adres olduğuna eminseniz devam edin.\n\n"
                    "Devam ederseniz her dosya için ayrıca UYAP'tan \"zaten gönderilmiş "
                    "mi\" kontrol edilecek ve gerçek gönderimden ÖNCE yine tek tek "
                    "onayınız istenecek."):
                return
            kalemler = [{"birim": s.get("birim", ""), "dosyaNo": s.get("dosyaNo", ""),
                        "borclu": s.get("borclu", "")} for s in adaylar]
            _gonderimi_baslat(kalemler, teblig_21_2_gonder, _bitti)

        # "Normal Tebligat Gönder" (kullanıcı isteği, 2026-08-17): "farklı yöntem
        # gerekli (mernis'e çıkmamış)" kategorisindeki dosyalar 21/2 şerhli YENİDEN
        # tebliğ talebine uygun DEĞİL — bunlara ilk/normal tebligat (icra/ödeme
        # emrinin mernis adresine tebliğe çıkarılması) gönderilmeli. Ayrı bir iş
        # türü (normal_tebligat_gonder, bkz. job_handlers.py) ve ayrı bir panel
        # modülü (normal_tebligat_gonder.py) kullanır — teblig_212_gonder'a
        # DOKUNULMAZ, "21/2 için uygun" ile "normal tebligat gerekiyor" FARKLI
        # belge türleri, aynı akıştan geçirilirse (yanlışlıkla 21/2 şerhli talep
        # gönderilirse) hukuken YANLIŞ bir belge UYAP'a gider.
        _NORMAL_TEBLIGAT_KATEGORISI = "farklı yöntem gerekli (mernis'e çıkmamış)"

        def _bitti_normal(sonuclar):
            nonlocal gonderim_aktif
            gonderim_aktif = False
            for b in _tum_gonder_btnlari():
                b.set_state("normal")

            gonderilenler = [s for s in sonuclar if s.get("durum") == "gonderildi"]
            if not gonderilenler:
                return
            gonderilen_dosya_nolari = {g.get("dosyaNo") for g in gonderilenler}
            for i, s in enumerate(sonuc):
                if s.get("dosyaNo") in gonderilen_dosya_nolari:
                    s["kategori"] = "✅ gönderildi (normal tebligat)"
                    try:
                        tree.item(str(i), values=[s.get(k, "") for k in cols])
                    except Exception:
                        pass
            # DÜZELTME (kullanıcı bulgusu, 2026-08-17): önceki sürüm burada
            # dosyalari_gonderildi_isaretle'i BİLEREK çağırmıyordu ("bu
            # gönderim 21/2 türünden değil" gerekçesiyle) — ama UYAP'ta HER
            # avukat portal talebi (21/2 DE, Normal Tebligat DA) AYNI evrak
            # türünü ("Avukat Portal Tebligat Talebi", bkz. TALEP_EVRAK_TUR)
            # üretir; "Yeniden Tebliğ Talebi" sütunu bu evrak türünün VARLIĞINI
            # gösterir, tebligat türünü AYIRT ETMEZ. Yani bir sonraki Barkod
            # Sorgu taraması bu alanı ZATEN "Var"a çevirecekti — burada da
            # işaretleyip kullanıcının ANINDA görmesini sağlıyoruz.
            ozetler = [g.get("ozet") or {} for g in gonderilenler]
            try:
                n = teblig_21_2_core.dosyalari_gonderildi_isaretle(ozetler)
                if n < len(ozetler):
                    self._log(f"⚠️ Yeniden Tebliğ Talebi: {len(ozetler)} gönderilen dosyadan "
                              f"yalnız {n} tanesi yerel DB'de eşleşti/güncellendi.")
            except Exception as e:
                self._log(f"⚠️ Yerel DB tazelenemedi (Yeniden Tebliğ Talebi): {e}")
            # Barkod Sorgu ekranının tabloları (dosya durumu değişmiş olabilir
            # diye) tazelenir.
            self._gecmis_yukle_bg()
            self._filtrele_bg(self._filtreleri_topla())

        def _normal_tebligat_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            uygun = [s for s in secili if s.get("kategori") == _NORMAL_TEBLIGAT_KATEGORISI]
            uygun_olmayan = len(secili) - len(uygun)
            if not uygun:
                messagebox.showinfo(
                    "Uygun satır yok",
                    f"Seçtiğiniz satırların hiçbiri \"{_NORMAL_TEBLIGAT_KATEGORISI}\" "
                    "değil — normal tebligat yalnız bu kategorideki dosyalar içindir.")
                return
            if uygun_olmayan:
                if not messagebox.askyesno(
                        "Bazı satırlar atlanacak",
                        f"Seçtiğiniz {len(secili)} satırdan {uygun_olmayan} tanesi "
                        f"\"{_NORMAL_TEBLIGAT_KATEGORISI}\" değil ve gönderilmeyecek. "
                        f"Kalan {len(uygun)} dosya için devam edilsin mi?"):
                    return
            kalemler = [{"birim": s.get("birim", ""), "dosyaNo": s.get("dosyaNo", ""),
                        "borclu": s.get("borclu", "")} for s in uygun]
            _gonderimi_baslat(kalemler, normal_tebligat_gonder, _bitti_normal)

        # "Yine de Gönder" (Normal Tebligat) — kullanıcı isteği (2026-08-17):
        # bazı satırlar "farklı yöntem gerekli (mernis'e çıkmamış)" DEĞİL,
        # "elle inceleyin — adres ayrıştırılamadı" gibi BAŞKA bir kategoride
        # çıkıyor (ör. giden tebligattaki adres PDF'ten ayrıştırılamadı —
        # bkz. teblig_21_2_core._teblig_adresi_cikar, desen uymazsa None
        # döner) — oysa kullanıcı dosyayı GÖZÜYLE kontrol edip mernis
        # adresinin doğru olduğunu, normal tebligat gönderilebileceğini
        # görebilir. "21/2 Yine de Gönder" ile AYNI mantık — otomatik
        # kategori kontrolünü ATLAR, mükerrer gönderim/son onay güvenliğini
        # ATLAMAZ (aynı prepare()+ctx.request_approval zinciri).
        def _yine_de_normal_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            adaylar = [s for s in secili if s.get("kategori") not in _GONDERILMEYECEK_KATEGORILER]
            if not adaylar:
                messagebox.showinfo(
                    "Gönderilecek satır yok",
                    "Seçtiğiniz satırların tamamı zaten gönderilmiş görünüyor.")
                return
            if not messagebox.askyesno(
                    "Kategori uygun değil — yine de normal tebligat gönderilsin mi?",
                    f"Seçtiğiniz {len(adaylar)} dosya \"{_NORMAL_TEBLIGAT_KATEGORISI}\" "
                    "kategorisinde İŞARETLENMEDİ (ör. adres ayrıştırılamadığı için "
                    "\"elle inceleyin\" çıkmış olabilir). Mernis Adresi sütununu "
                    "GÖZÜNÜZLE kontrol edip normal tebligat gönderilebileceğinden "
                    "eminseniz devam edin.\n\n"
                    "Devam ederseniz her dosya için ayrıca UYAP'tan \"zaten gönderilmiş "
                    "mi\" kontrol edilecek ve gerçek (ücretli) gönderimden ÖNCE yine "
                    "tek tek onayınız istenecek."):
                return
            kalemler = [{"birim": s.get("birim", ""), "dosyaNo": s.get("dosyaNo", ""),
                        "borclu": s.get("borclu", "")} for s in adaylar]
            _gonderimi_baslat(kalemler, normal_tebligat_gonder, _bitti_normal)

        gonder_btn = self._btn(alt, "Seçilenler İçin GERÇEKTEN Gönder (Ücretli)",
                               _gonder_tetikle, "primary")
        gonder_btn.pack(side="left")
        yine_de_btn = self._btn(alt, "⚠ Yine de Gönder (Uygun İşaretlenmeyenler)",
                                _yine_de_gonder_tetikle, "stop")
        yine_de_btn.pack(side="left", padx=(8, 0))
        normal_tebligat_btn = self._btn(
            alt, "📨 Normal Tebligat Gönder (Mernis'e Çıkmamışlar)",
            _normal_tebligat_tetikle, "ghost")
        normal_tebligat_btn.pack(side="left", padx=(8, 0))
        yine_de_normal_btn = self._btn(
            alt, "⚠ Yine de Gönder (Normal Tebligat)",
            _yine_de_normal_tetikle, "stop")
        yine_de_normal_btn.pack(side="left", padx=(8, 0))
        self._btn(alt, "Kapat", top.destroy, "ghost").pack(side="left", padx=(8, 0))
        top.grab_set()

    # ─────────────────────────── Tümünü / Seçilenleri Sorgula ───────────────────────────
    def _tumunu_sorgula(self):
        # Kullanıcı bulgusu (2026-08-03, ikinci tur): "hala tek tek dosya
        # seçtirmeye çalışıyor" — Ctrl/Shift ile elle seçim yeterli değil,
        # kullanıcı FİLTRELENMİŞ listedeki TÜM dosyaları hiç seçim yapmadan
        # tek tuşla toplu sorgulatabilmek istiyor. `self.kayitlar` o an
        # ekranda görünen (yerel filtre+sıralama uygulanmış) TAM liste —
        # Treeview seçimine hiç bakmadan doğrudan bunu kullanır.
        if not self.kayitlar:
            self.durum_lbl.config(text="Sorgulanacak dosya yok; önce filtreleyin.")
            return
        self._sorgu_baslat(list(self.kayitlar), "filtrelenmiş listedeki TÜM")

    def _secilenleri_sorgula(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden en az bir dosya seçin (ya da \"Tümünü Sorgula\"yı kullanın).")
            return
        try:
            secili_kayitlar = [self._by_uid[int(iid)] for iid in secim]
        except (ValueError, KeyError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        self._sorgu_baslat(secili_kayitlar, "seçili")

    def _sorgu_baslat(self, kayitlar, aciklama):
        if self._calisiyor:
            return
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._sorgu_gercek_baslat(kayitlar, aciklama),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._sorgu_gercek_baslat(kayitlar, aciklama)

    def _sorgu_gercek_baslat(self, kayitlar, aciklama):
        self.kontrol.sifirla()
        self._calisiyor = True
        self.tumunu_sorgula_btn.set_state("disabled")
        self.sorgula_btn.set_state("disabled")
        self.duraklat_btn.set_state("normal")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal")
        self.durum_lbl.config(text=f"{len(kayitlar)} dosya sorgulanıyor…")
        self._append_log(f"▶ {len(kayitlar)} {aciklama} dosya için barkod sorgusu başlıyor…")

        def bg():
            try:
                sonuc = barkod_sorgu.calistir(kayitlar, self._log, kontrol=self.kontrol)
                self.result_q.put(("sorgu_bitti", sonuc))
            except Exception as e:
                self.result_q.put(("sorgu_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def duraklat_toggle(self):
        if not self._calisiyor:
            return
        paused = self.kontrol.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self._calisiyor:
            self.kontrol.durdur()

    def _sorgu_bitti_ui(self):
        self._calisiyor = False
        self.tumunu_sorgula_btn.set_state("normal")
        self.sorgula_btn.set_state("normal")
        self.duraklat_btn.set_state("disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("disabled")
        toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    def _sorgu_bitti(self, sonuc):
        self._sorgu_bitti_ui()
        adet = len(sonuc) if sonuc else 0
        self.durum_lbl.config(text=f"✔ Sorgu tamamlandı ({adet} satır sonuç).")
        self._append_log(f"✅ Sorgu tamamlandı — {adet} satır sonuç.")
        self._gecmis_yukle_bg()  # yeni yazılan sonuçlar geçmiş bölümüne yansısın
        # … ve üstteki dosya seçici listedeki Barkod/Durum sütunlarına da
        # (bkz. `_barkod_ozetini_birlestir`) — o anki filtre KORUNARAK.
        self._filtrele_bg(self._filtreleri_topla())

    def _sorgu_hata(self, hata):
        self._sorgu_bitti_ui()
        self.durum_lbl.config(text="Sorgu sırasında hata oluştu.")
        self._append_log(f"❌ Hata: {hata}")

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                try:
                    if tip == "alanlar":
                        birimler, durumlar = veri
                        self._birim_kod = {b.get("ad", b.get("birimId", "")): b.get("birimId", "") for b in birimler}
                        self.birim_cb["values"] = ["Tümü"] + list(self._birim_kod.keys())
                        self._durum_kod = {ad: kod for kod, ad in durumlar}
                        self.durum_cb["values"] = ["Tümü"] + list(self._durum_kod.keys())
                    elif tip == "kayitlar":
                        self._tabloyu_doldur(veri)
                    elif tip == "gecmis":
                        self.gecmis_ham = veri
                        self._gecmis_uygula()
                    elif tip == "t212_sonuc":
                        self._t212_sonuc_goster(veri)
                    elif tip == "t212_hata":
                        self._t212_hata(veri)
                    elif tip == "t212_son_rapor":
                        self._t212_son_rapor_goster(veri)
                    elif tip == "t212_son_rapor_hata":
                        self.durum_lbl.config(text=f"Son rapor yüklenemedi: {veri}")
                    elif tip == "sorgu_bitti":
                        self._sorgu_bitti(veri)
                    elif tip == "sorgu_hata":
                        self._sorgu_hata(veri)
                    elif tip == "excel_tamam":
                        self._excel_aktarildi(veri)
                    elif tip == "excel_hata":
                        self._excel_hata(veri)
                    elif tip == "log":
                        self._append_log(str(veri))
                except Exception as e:
                    self._append_log(f"⚠️ Ekran güncellenemedi ({tip}): {e}")
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
