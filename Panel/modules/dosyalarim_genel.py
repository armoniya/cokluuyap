# -*- coding: utf-8 -*-
"""
Modül: Dosyalarım (Tümü)
=========================
Yalnız İcra'ya değil, `SenkronKapsami`'nin (bkz. senkron_kapsami.py) kapsadığı
TÜM yargı türü/birimindeki dosyaları tek ekranda yargı türü/birimi/dosya
türü/durum/açılış tarihi aralığına göre filtreleyerek listeler (bkz.
docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 Faz 6). `icra_dosyalarim.py`'nin
AKSİNE bu ekran canlı UYAP sorgusu YAPMAZ — yalnız yerel DB'yi okur
(`dosya_core.dosyalarim_db_listele`); "Yenile" düğmesi arka planda
`DosyaSorgu.calistir`'i (arka plan zamanlayıcısıyla AYNI mantık) HEMEN
çalıştırıp DB'yi tazeler. Çalışma mantığı TAMAMEN `dosya_core.py`'dedir;
`icra_dosyalarim.py`'ye KESİNLİKLE dokunulmaz (bkz. plan §4 kararı — kanıtlı
modülü değiştirme, paralel yaz).
"""

import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

import openpyxl

from theme import C, RoundButton
from . import dosya_core, barkod_sorgu

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE_TIK = os.path.dirname(os.path.abspath(__file__))
if _HERE_TIK not in sys.path:
    sys.path.insert(0, _HERE_TIK)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402

_ACILIS_TARIHI_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")

# Küçük log kutusu (3 satır) okunamıyor/kopyalanamıyor şikayeti (kullanıcı
# bulgusu, 2026-07-13): tüm satırlar artık ayrıca kalıcı bir txt dosyasına da
# yazılır — kullanıcı Notepad ile açıp doğrudan buraya (Claude'a) kopyalayabilir.
_LOG_DOSYA_YOLU = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "UyapIcra",
    "dosyalarim_genel.log")


class DosyalarimGenelPanel:
    KOLONLAR = [
        ("yargi_turu_adi", "Yargı Türü", 80),
        ("birimAdi", "Yargı Birimi / Mahkeme", 220),
        ("dosyaNo", "Dosya No", 90),
        ("dosyaTur", "Dosya Türü", 110),
        ("dosyaDurum", "Durum", 70),
        ("acilisTarihi", "Açılış Tarihi", 90),
        # İcra Dosyalarım'daki gibi kendi başına sütun — kullanıcı bulgusu,
        # 2026-07-13: alacaklı/borçlu taraf1..4 genel sütunlarında gömülüydü
        # ama tek bakışta ayırt edilemiyordu ("bütünlük yok"). İcra dışı
        # yargı türlerinde bu rol'ler oluşmadığından hücre boş kalır.
        ("alacakli", "Alacaklı", 200),
        ("borclu", "Borçlu", 200),
        ("taraf1", "Taraf 1", 160),
        ("taraf2", "Taraf 2", 160),
        ("taraf3", "Taraf 3", 160),
        ("taraf4", "Taraf 4", 160),
        # Kesinleşme Durumu / Tebliğ Durumu — hem burada hem İcra Dosyalarım'da
        # gösterilir (kullanıcı isteği, 2026-08-14). Veri kaynağı ikisinde de
        # AYNI (dosya_core._dosya_barkod_ozetlerini_ekle).
        ("kesinlesme_durumu", "Kesinleşme Durumu", 140),
        ("tebligat_durumu", "Tebliğ Durumu", 200),
    ]
    HEAD_H = 52   # başlık+filtre-kutusu şeridi yüksekliği (px) — İcra Dosyalarım'daki
                  # sütun-başlığı canlı filtre/sıralama arayüzünden taşındı (2026-07-12).

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Dosyalarım (Tümü) Yenile")
        # "Seçilenlere İşlem Yap" (barkod sorgu) — barkod_sorgu_panel.py'nin
        # KENDİ işiyle AYNI ad ("Barkod Sorgulama") kullanılır ki
        # toplu_is_kontrol.KAYIT_DEFTERI iki ekrandan aynı anda başlatılan
        # barkod sorgularını da (ekranlar farklı olsa bile) çakışma olarak
        # yakalasın — bkz. toplu_is_kontrol.AktifIsKayitDefteri.basvur.
        self.barkod_kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Barkod Sorgulama")
        # O an ÇALIŞAN işin kontrol nesnesi (Yenile YA DA Barkod İşlem) —
        # Duraklat/Durdur düğmeleri TEK ÇİFT, hangi iş aktifse ONA yönlenir
        # (bkz. duraklat_toggle/durdur) — aynı anda ikisi birden çalışamaz
        # zaten (KAYIT_DEFTERI tek işe izin verir), bu yüzden ayrı düğme
        # çifti gerekmez.
        self._aktif_kontrol = None
        self.result_q = queue.Queue()
        self.kayitlar_ham = []      # DB'den gelen, sunucu filtreleriyle daralmış TAM liste
        self.kayitlar = []          # ekrandaki (sütun-filtresi+sıralama uygulanmış) kayıtlar, iid -> index
        # Sütun başlığı canlı filtre kutuları — kalıcı StringVar'lar, böylece
        # sıralama değişince başlık yeniden çizilse bile yazılan metin kaybolmaz.
        self.col_vars = {k: tk.StringVar() for k, _l, _w in self.KOLONLAR}
        self.col_combos = {}        # key -> ttk.Combobox (başlık şeridindeki filtre kutusu)
        self.col_labels = {}        # key -> (Label, taban_etiket) — sıralama okunu güncellemek için
        self.sort_key = None
        self.sort_reverse = False

        self.tur_var = tk.StringVar(value="Tümü")
        self.birim_var = tk.StringVar(value="Tümü")
        self.mahkeme_var = tk.StringVar(value="Tümü")
        self.dosya_tur_var = tk.StringVar(value="Tümü")
        self.durum_var = tk.StringVar(value="Tümü")
        self.tarih_bas_var = tk.StringVar()
        self.tarih_bit_var = tk.StringVar()
        self.taraf_var = tk.StringVar()
        # İcra dosyalarında rol'e (alacaklı/borçlu) göre ayrı arama — kullanıcı
        # bulgusu, 2026-07-12: genel "Taraf Adı" rol ayırt etmiyordu, İcra'da
        # özellikle alacaklı/borçlu'ya göre aranamıyordu.
        self.alacakli_var = tk.StringVar()
        self.borclu_var = tk.StringVar()

        self._tur_kod = {}          # etiket -> kod
        self._birim_kod = {}        # etiket -> kod (seçili türe göre yeniden doldurulur)
        # "Yargı Birimi" mahkeme TÜRÜNÜ süzer (ör. Asliye Hukuk Mahkemesi);
        # "Mahkeme" ise aynı türdeki BELİRLİ mahkemeyi süzer (ör. "ANKARA 4.
        # ASLİYE HUKUK MAHKEMESİ") — kullanıcı bulgusu, 2026-07-12: "yargı
        # türü ve yargı birimi var fakat mahkeme ile filtreleme yok". Yargı
        # Türü/Yargı Birimi seçimine göre kademeli doldurulur.
        self._mahkeme_kod = {}      # etiket (birim adı) -> birimId
        self._dosya_tur_kod = {}    # etiket -> kod (seçili türe göre yeniden doldurulur)
        self._dosya_turleri_tum = []  # filtresiz ("Tümü") tam liste — Temizle bunu geri yükler
        self._durum_kod = {}

        self._init_style()
        self._build()
        self._icra_extra_guncelle()
        threading.Thread(target=self._alanlar_yukle_bg, daemon=True).start()
        self.app.after(200, self._poll)
        self.app.after(20000, self._oto_yenile_dongusu)

    def _oto_yenile_dongusu(self):
        """20 sn'de bir mevcut filtreyle YEREL veritabanını sessizce yeniden
        okur (UYAP'a GİTMEZ — Filtrele'nin yaptığının periyodik tekrarı) —
        kullanıcı isteği (2026-08-14): "bir modülde yapılan güncelleme diğer
        modülde ANINDA görünsün", elle Filtrele'ye basmadan. Canlı bir Yenile/
        Barkod işlemi SÜRERKEN (butonlar disabled) araya girmez."""
        try:
            mesgul = getattr(self.yenile_btn, "_state", "normal") == "disabled"
            if not mesgul and self.parent.winfo_exists():
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
        # dosyalarim_db_listele HATA durumunda da [] döner (kendi try/except'i)
        # — BOŞ sonuç göndermiyoruz ki geçici bir DB kesintisi ekrandaki DOLU
        # tabloyu SESSİZCE boşaltmasın (kullanıcı bulgusu, 2026-08-14: "var
        # olan veri gitti" — bkz. icra_dosyalarim._birlestir düzeltmesiyle
        # AYNI endişe).
        if kayitlar:
            self.result_q.put(("kayitlar", kayitlar))

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dosya.TNotebook", background=C.CARD, borderwidth=0)
        style.configure("Dosya.TNotebook.Tab", background=C.SIDEBAR, foreground=C.INK_SOFT,
                        padding=(16, 7), font=self.app.f_nav_b)
        style.map("Dosya.TNotebook.Tab",
                  background=[("selected", C.SAGE_TINT)],
                  foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="Dosyalarım (Tümü)", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Senkron Kapsamı'nda seçilen tüm yargı türü/birimlerindeki "
                 "dosyalar. Bu liste yerel veritabanından gelir; UYAP'tan tazelemek "
                 "için “Yenile”'yi kullanın.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=760,
                 justify="left").pack(anchor="w", pady=(6, 14))

        # ── filtre çubuğu ──
        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        self.tur_cb = self._combo(ui, "Yargı Türü", self.tur_var, ["Tümü"])
        self.tur_cb.bind("<<ComboboxSelected>>", self._tur_degisti)
        self.birim_cb = self._combo(ui, "Yargı Birimi", self.birim_var, ["Tümü"])
        self.birim_cb.bind("<<ComboboxSelected>>", self._birim_degisti)
        self.mahkeme_cb = self._combo(ui, "Mahkeme", self.mahkeme_var, ["Tümü"])
        self.mahkeme_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.dosya_tur_cb = self._combo(ui, "Dosya Türü", self.dosya_tur_var, ["Tümü"])
        self.dosya_tur_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.durum_cb = self._combo(ui, "Durum", self.durum_var, ["Tümü"])
        self.durum_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())

        # ── ikinci satır: Açılış Tarihi + Taraf Adı ──
        # Beş açılır kutunun ("Yargı Türü".."Durum") olduğu ilk satıra
        # eklenince Açılış Tarihi kutuları dar/kompakt pencerede (bkz.
        # panel.py _compact, genişlik 860px) sağa taşıp görünür alanın
        # dışına çıkıyordu — kullanıcı bulgusu, 2026-08-04: "açılış tarih
        # aralığı giremiyorum". tk.Frame/pack satır sarmıyor, bu yüzden web
        # sürümündeki (index.html) iki ayrı <div class="icra-row"> deseni
        # burada da ayrı bir satıra taşındı.
        ui_tarih = tk.Frame(ust, bg=C.CARD)
        ui_tarih.pack(fill="x", padx=18, pady=(0, 12))

        tk.Label(ui_tarih, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui_tarih, self.tarih_bas_var)
        tk.Label(ui_tarih, text="–", bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui_tarih, self.tarih_bit_var)

        tk.Label(ui_tarih, text="Taraf Adı", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        taraf_e = tk.Entry(ui_tarih, textvariable=self.taraf_var, bg="#FFFFFF", fg=C.INK, relief="flat",
                           insertbackground=C.INK, font=self.app.f_body, width=20,
                           highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        taraf_e.pack(side="left", ipady=3)
        taraf_e.bind("<Return>", lambda ev: self._filtrele())

        # ── üçüncü satır: İcra'ya özgü rol bazlı arama (Alacaklı/Borçlu) ──
        # Yalnız Yargı Türü "İcra" seçiliyken görünür (kullanıcı bulgusu,
        # 2026-07-12: bu satır önceden HER yargı türünde görünüyordu, kafa
        # karıştırıyordu) — bkz. _icra_ekstra_guncelle. Bu yüzden hem çizgi
        # hem içerik TEK bir sarmalayıcı çerçevede tutulur ki tek pack/
        # pack_forget çağrısıyla ikisi birden gizlenebilsin.
        self.icra_extra = tk.Frame(ust, bg=C.CARD)
        tk.Frame(self.icra_extra, bg=C.LINE, height=1).pack(fill="x", padx=18)
        ui2 = tk.Frame(self.icra_extra, bg=C.CARD)
        ui2.pack(fill="x", padx=18, pady=12)

        tk.Label(ui2, text="Alacaklı", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self.alacakli_cb = ttk.Combobox(ui2, textvariable=self.alacakli_var, state="normal",
                                        values=[], font=self.app.f_body, width=20)
        self.alacakli_cb.pack(side="left", ipady=2)
        self.alacakli_cb.bind("<Return>", lambda ev: self._filtrele())

        tk.Label(ui2, text="Borçlu", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        self.borclu_cb = ttk.Combobox(ui2, textvariable=self.borclu_var, state="normal",
                                      values=[], font=self.app.f_body, width=20)
        self.borclu_cb.pack(side="left", ipady=2)
        self.borclu_cb.bind("<Return>", lambda ev: self._filtrele())
        # Başlangıçta gizli — varsayılan Yargı Türü "Tümü"dür, _tur_degisti
        # (ve __init__ sonunda ilk kurulum) görünürlüğü ayarlar.

        # ── düğmeler ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self._btn(bar, "Filtrele", self._filtrele, "primary").pack(side="left", ipadx=6)
        self._btn(bar, "Temizle", self._temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.yenile_btn = self._btn(bar, "Yenile (UYAP'tan Güncelle)", self._yenile, "ghost")
        self.yenile_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.yenile_tumu_btn = self._btn(bar, "Tüm Dosyaları Güncelle", self._yenile_tumu, "ghost")
        self.yenile_tumu_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn.set_state("disabled")
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durdur_btn.set_state("disabled")
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.excel_btn = self._btn(bar, "Excel'e Aktar", self._excel_aktar, "ghost")
        self.excel_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.barkod_islem_btn = self._btn(bar, "Seçilenlere Barkod Sorgu",
                                           self._secilenlere_islem_yap, "ghost")
        self.barkod_islem_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── sonuç tablosu: başlık şeridi (label + canlı filtre kutusu) + veri ──
        # Başlık artık ttk.Treeview'ın kendi "headings" bandı değil, ayrı bir
        # Canvas/Frame şeridi (İcra Dosyalarım'daki tasarımdan taşındı,
        # 2026-07-12): her sütunun altına o sütunu CANLI (DB'ye gitmeden,
        # yerelde) daraltan bir kutu + başlığa tıklayınca sırala eklenir.
        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(14, 0))

        self.head_canvas = tk.Canvas(card, bg=C.SIDEBAR, highlightthickness=0, height=self.HEAD_H)
        self.head_inner = tk.Frame(self.head_canvas, bg=C.SIDEBAR)
        self.head_win = self.head_canvas.create_window((0, 0), window=self.head_inner, anchor="nw")
        self.head_canvas.grid(row=0, column=0, sticky="ew")

        cols = [k for k, _l, _w in self.KOLONLAR]
        self.tree = ttk.Treeview(card, columns=cols, show="", height=16)
        self.tree.grid(row=1, column=0, sticky="nsew")
        # Çift tık = "Dosya Görüntüle" düğmesiyle aynı işlem (kullanıcı
        # bulgusu, 2026-07-12: bir dosyaya çift tıklayınca ayrıntı açılsın).
        self.tree.bind("<Double-1>", lambda e: self._dosya_goruntule())
        ysb = tk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        # xsb tree'yi kaydırırken başlık şeridini de AYNI ANDA kaydırır (_xset) —
        # aksi halde sütun başlıkları veriyle hizadan kayar.
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

        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(10, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=3,
                           padx=12, pady=6, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        # Kutu dar/salt-okunur olduğu için kopyalanamıyor (kullanıcı bulgusu,
        # 2026-07-13) — her satır ayrıca txt dosyasına yazılıyor, bu düğme onu açar.
        self._btn(lbox, "Günlüğü Aç", self._log_dosyasini_ac, "ghost").pack(
            side="right", padx=8, pady=6, ipadx=2)

    def _combo(self, parent, label, var, values):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", values=values,
                          font=self.app.f_body, width=16)
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

    # ─────────────────────────── sütun başlığı: canlı filtre + sıralama ───────────────────────────
    def _basligi_kur(self):
        """Başlık şeridini KOLONLAR'a göre bir kez kurar: her sütun için
        (tıklanınca sıralayan) etiket + altında canlı filtre kutusu."""
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
        """Yargı Türü/Birim (üst çubuk) değişince çağrılır: sütun-başlığı
        filtreleri ve sıralama SIFIRLANIR — aksi halde eski kapsamda yazılmış
        görünmeyen bir filtre yeni kapsamdaki kayıtları sessizce eleyip boş
        tablo gösterebilir (bkz. `_icra_extra_guncelle`'deki AYNI ders,
        alacaklı/borçlu için — kullanıcı bulgusu, 2026-07-12). "Yenile" ile
        AYNI kapsamı tazelerken bu ÇAĞRILMAZ — kullanıcının filtresi kalır."""
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
        """Her sütunun filtre kutusuna, o an yüklü kayıtlardaki (DB
        filtrelerinden sonraki) benzersiz değerleri açılır liste olarak koyar."""
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is None:
                continue
            degerler = sorted({str(r.get(k, "") or "") for r in self.kayitlar_ham} - {""},
                               key=lambda s: dosya_core.tr_lower(s))
            cb["values"] = degerler

    def _yerel_kriterler(self):
        # tr_lower (Türkçe-duyarlı) kullanılır — düz .lower() 'İ/I/ı/Ş/Ğ/Ü/Ö/Ç'
        # harflerinde YANLIŞ eşleştirir (bkz. dosya_core._taraf_isim_eslesir
        # ile AYNI ders, kullanıcı bulgusu 2026-07-12).
        out = {}
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is not None:
                q = dosya_core.tr_lower(cb.get().strip())
                if q:
                    out[k] = q
        return out

    def _yerel_uygula(self):
        """Sunucudan gelmiş `kayitlar_ham`'a sütun-başlığı filtrelerini ve
        sıralamayı DB'ye gitmeden, yerelde uygular ve tabloyu yeniden çizer."""
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
                # "GG.AA.YYYY" düz METİN olarak sıralanırsa GÜN önce geldiği
                # için kronolojik sıra YANLIŞ çıkar (ör. "05.12.2026" metin-
                # sırasında "31.01.2025"ten önce gelir) — "YYYYAAGG"
                # karşılaştırılabilir anahtara çevrilir.
                def gecerli_mi(r):
                    m = _ACILIS_TARIHI_RE.match(str(r.get(col, "") or "").strip())
                    return f"{m.group(3)}{m.group(2)}{m.group(1)}" if m else None
            else:
                def gecerli_mi(r):
                    v = str(r.get(col, "") or "").strip()
                    if not v:
                        return None
                    try:
                        return (0, float(v.replace(",", ".")))
                    except ValueError:
                        return (1, dosya_core.tr_lower(v))
            # Boş/biçimsiz değerler HER İKİ yönde de (artan/azalan) SONA
            # atılır — kullanıcı bulgusu, 2026-07-13: eskiden bu (0,değer)/
            # (1,"") etiketiyle TEK sorted(reverse=...) çağrısına veriliyordu;
            # reverse=True etiketi de ters çevirdiğinden azalan sırada boş
            # satırlar başa zıplıyordu. Artık geçerli/geçersiz ayrı tutulup
            # yalnız geçerliler ters çevrilir, geçersizler HER ZAMAN sonda
            # sabit kalır — bu davranış tüm sütunlarda (sayısal/metin/tarih)
            # aynı şekilde uygulanır.
            gecerliler, gecersizler = [], []
            for r in kayitlar:
                a = gecerli_mi(r)
                (gecersizler if a is None else gecerliler).append((a, r))
            gecerliler.sort(key=lambda p: p[0], reverse=self.sort_reverse)
            kayitlar = [r for _a, r in gecerliler] + [r for _a, r in gecersizler]

        self.kayitlar = kayitlar
        self.tree.delete(*self.tree.get_children())
        for i, rec in enumerate(kayitlar):
            vals = [rec.get(k, "") for k, _l, _w in self.KOLONLAR]
            self.tree.insert("", "end", iid=str(i), values=vals)
        toplam = len(self.kayitlar_ham)
        if not toplam:
            self.durum_lbl.config(text="")
        elif len(kayitlar) != toplam:
            self.durum_lbl.config(text=f"{len(kayitlar)} / {toplam} dosya")
        else:
            self.durum_lbl.config(text=f"{toplam} dosya")

    # ─────────────────────────── günlük ───────────────────────────
    def _log(self, mesaj):
        self.result_q.put(("log", str(mesaj)))

    def _append_log(self, text):
        self._log_dosyaya_yaz(text)
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    @staticmethod
    def _log_dosyaya_yaz(text):
        """Küçük log kutusuna yazılan HER satırı, kalıcı bir txt dosyasına da
        (%LOCALAPPDATA%\\UyapIcra\\dosyalarim_genel.log) ekler — kutu dar/salt
        okunur olduğu için kopyalanamıyor; dosya Notepad ile açılıp paylaşılabilir."""
        try:
            os.makedirs(os.path.dirname(_LOG_DOSYA_YOLU), exist_ok=True)
            with open(_LOG_DOSYA_YOLU, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except Exception:
            pass

    def _log_dosyasini_ac(self):
        try:
            os.makedirs(os.path.dirname(_LOG_DOSYA_YOLU), exist_ok=True)
            if not os.path.exists(_LOG_DOSYA_YOLU):
                open(_LOG_DOSYA_YOLU, "a", encoding="utf-8").close()
            os.startfile(_LOG_DOSYA_YOLU)
        except Exception as e:
            self._append_log(f"⚠️ Log dosyası açılamadı: {e}")

    # ─────────────────────────── veri yükleme ───────────────────────────
    def _alanlar_yukle_bg(self):
        try:
            turler = dosya_core.YARGI_TURLERI
            dosya_turleri = dosya_core.dosya_tur_secenekleri()
            durumlar = dosya_core.dosya_durum_secenekleri()
        except Exception as e:
            self._log(f"⚠️ Alanlar yüklenemedi: {e}")
            return
        self.result_q.put(("alanlar", (turler, dosya_turleri, durumlar)))
        self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(None, None)))
        try:
            alacaklilar = dosya_core.taraf_adi_secenekleri("alacakli", dosya_core.YARGI_TURU_ICRA)
            borclular = dosya_core.taraf_adi_secenekleri("borclu", dosya_core.YARGI_TURU_ICRA)
        except Exception:
            alacaklilar, borclular = [], []
        self.result_q.put(("taraf_secenekleri", (alacaklilar, borclular)))
        self._filtrele_bg({})

    def _alanlari_doldur(self, turler, dosya_turleri, durumlar):
        self._tur_kod = {ad: kod for kod, ad in turler}
        self.tur_cb["values"] = ["Tümü"] + [ad for _k, ad in turler]
        self._dosya_turleri_tum = dosya_turleri   # "Tümü" seçiliyken (filtresiz) tam liste — Temizle bunu geri yükler
        self._dosya_tur_kod = {ad: kod for kod, ad in dosya_turleri}
        self.dosya_tur_cb["values"] = ["Tümü"] + [ad for _k, ad in dosya_turleri]
        self._durum_kod = {ad: kod for kod, ad in durumlar}
        self.durum_cb["values"] = ["Tümü"] + [ad for _k, ad in durumlar]

    def _icra_extra_guncelle(self):
        """Alacaklı/Borçlu satırını yalnız Yargı Türü "İcra" seçiliyken
        gösterir (kullanıcı bulgusu, 2026-07-12). Gizlenirken doldurulmuş
        değerler de temizlenir — aksi halde başka bir türe geçildiğinde
        görünmeyen bir filtre sessizce sonuçları etkilemeye devam ederdi."""
        icra_mi = (self.tur_var.get() == "İcra")
        if icra_mi:
            self.icra_extra.pack(fill="x")
        else:
            self.icra_extra.pack_forget()
            self.alacakli_var.set("")
            self.borclu_var.set("")

    def _tur_degisti(self, _event=None):
        self.birim_var.set("Tümü")
        self._birim_kod = {}
        self.birim_cb["values"] = ["Tümü"]
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self.dosya_tur_var.set("Tümü")
        self._icra_extra_guncelle()
        self._yerel_filtreleri_sifirla()
        secili = self.tur_var.get()
        kod = self._tur_kod.get(secili)

        def bg():
            try:
                birimler = dosya_core.yargi_birimleri_getir_veya_db(kod, self._log) if kod is not None else []
            except Exception:
                birimler = []
            self.result_q.put(("birimler", birimler))
            try:
                dosya_turleri = dosya_core.dosya_tur_secenekleri(kod)
            except Exception:
                dosya_turleri = []
            self.result_q.put(("dosya_turleri", dosya_turleri))
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(kod, None)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _birim_degisti(self, _event=None):
        """'Yargı Birimi' (mahkeme türü) değişince 'Mahkeme' listesi o türe
        göre daraltılarak yeniden yüklenir (kademeli — bkz. __init__ notu)."""
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self._yerel_filtreleri_sifirla()
        tur_kod = self._tur_kod.get(self.tur_var.get())
        birim_ad = self.birim_var.get()
        birim_kod = self._birim_kod.get(birim_ad) if birim_ad != "Tümü" else None

        def bg():
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(tur_kod, birim_kod)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _filtreleri_topla(self):
        f = {}
        tur_ad = self.tur_var.get()
        if tur_ad != "Tümü":
            f["yargi_turu"] = self._tur_kod.get(tur_ad)
        birim_ad = self.birim_var.get()
        if birim_ad != "Tümü":
            f["yargi_birimi_kod"] = self._birim_kod.get(birim_ad)
        mahkeme_ad = self.mahkeme_var.get()
        if mahkeme_ad != "Tümü":
            f["mahkeme_id"] = self._mahkeme_kod.get(mahkeme_ad)
        dt_ad = self.dosya_tur_var.get()
        if dt_ad != "Tümü":
            f["tur_kod"] = self._dosya_tur_kod.get(dt_ad)
        durum_ad = self.durum_var.get()
        if durum_ad != "Tümü":
            f["durum_kod"] = self._durum_kod.get(durum_ad)
        if self.tarih_bas_var.get().strip():
            f["tarih_baslangic"] = self.tarih_bas_var.get().strip()
        if self.tarih_bit_var.get().strip():
            f["tarih_bitis"] = self.tarih_bit_var.get().strip()
        if self.taraf_var.get().strip():
            f["taraf_adi"] = self.taraf_var.get().strip()
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
            self.result_q.put(("kayitlar", kayitlar))
        threading.Thread(target=bg, daemon=True).start()

    def _temizle(self):
        self.tur_var.set("Tümü")
        self.birim_var.set("Tümü")
        self.birim_cb["values"] = ["Tümü"]
        self.mahkeme_var.set("Tümü")
        self._mahkeme_kod = {}
        self.mahkeme_cb["values"] = ["Tümü"]
        self.dosya_tur_var.set("Tümü")
        self._dosya_tur_kod = {ad: kod for kod, ad in self._dosya_turleri_tum}
        self.dosya_tur_cb["values"] = ["Tümü"] + list(self._dosya_tur_kod.keys())
        self.durum_var.set("Tümü")
        self.tarih_bas_var.set("")
        self.tarih_bit_var.set("")
        self.taraf_var.set("")
        self.alacakli_var.set("")
        self.borclu_var.set("")
        self._icra_extra_guncelle()
        self._yerel_filtreleri_sifirla()

        def bg():
            self.result_q.put(("mahkemeler", dosya_core.mahkeme_secenekleri(None, None)))
        threading.Thread(target=bg, daemon=True).start()
        self._filtrele()

    def _tabloyu_doldur(self, kayitlar):
        """DB'den yeni bir sonuç seti geldiğinde çağrılır: ham listeyi saklar,
        sütun filtre kutularının açılır listelerini tazeler, sonra yerel
        filtre/sıralamayı uygulayıp tabloyu çizer (bkz. `_yerel_uygula`)."""
        self.kayitlar_ham = kayitlar
        self._combolari_doldur()
        self._yerel_uygula()

    # ─────────────────────────── Yenile (UYAP'tan Güncelle) ───────────────────────────
    def _yenile_baslat(self, **kwargs):
        self.kontrol.sifirla()
        self._aktif_kontrol = self.kontrol
        self.yenile_btn.set_state("disabled")
        self.yenile_tumu_btn.set_state("disabled")
        self.barkod_islem_btn.set_state("disabled")
        self.duraklat_btn.set_state("normal")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal")
        self.durum_lbl.config(text="UYAP'tan güncelleniyor…")

        def bg():
            try:
                toplam, sonuclar = dosya_core.dosyalarim_yenile(self._log, kontrol=self.kontrol, **kwargs)
                self.result_q.put(("yenilendi", (toplam, sonuclar)))
            except Exception as e:
                self.result_q.put(("yenile_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def _yenile_cakisma_kontrolu(self, kwargs):
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._yenile_baslat(**kwargs),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._yenile_baslat(**kwargs)

    def _yenile(self):
        """Ekonomik: yalnız seçili Yargı Türü/Birim taranır. Hiçbiri seçili
        değilse (Tümü/Tümü) SenkronKapsami'ye döner — TÜM türleri/birimleri
        taramak için 'Tüm Dosyaları Güncelle' kullanılmalı.

        Kullanıcı bulgusu (2026-08-04): "filtreledikten sonra Yenile dedim
        ama Ceza dahil tüm dosyaları taradı" — Mahkeme/Dosya Türü/Durum/
        Tarih/Taraf gibi diğer TÜM filtreler yalnız YEREL görünüme uygulanır
        (bkz. _yerel_uygula), Yenile'nin canlı UYAP kapsamını HİÇ etkilemez;
        yalnız Yargı Türü/Yargı Birimi bu kapsamı daraltır. Yargı Türü
        "Tümü" bırakılmışken (başka filtreler seçili olsa bile) Yenile
        SenkronKapsami'deki NE VARSA (Ceza dahil olabilir) tarar — sürpriz
        olmasın diye bu durumda açıkça onay istenir."""
        kwargs = {}
        tur_ad = self.tur_var.get()
        if tur_ad != "Tümü":
            kwargs["yargi_turu"] = self._tur_kod.get(tur_ad)
            birim_ad = self.birim_var.get()
            if birim_ad != "Tümü":
                kwargs["yargi_birimi_kod"] = self._birim_kod.get(birim_ad)
        else:
            try:
                kapsamlar = dosya_core.senkron_kapsamlari_getir()
            except Exception:
                kapsamlar = None
            if kapsamlar:
                turler = sorted({dosya_core.YARGI_TURU_ADI.get(t, str(t)) for t, _b in kapsamlar})
                detay = "Senkron Kapsamı'ndaki tüm yargı türleri taranacak: " + ", ".join(turler) + "."
            else:
                detay = "Senkron Kapsamı'ndaki tüm yargı türleri taranacak."
            if not self._yenile_kapsam_onayi(detay):
                return
        self._yenile_cakisma_kontrolu(kwargs)

    def _yenile_kapsam_onayi(self, detay):
        """Yargı Türü='Tümü' iken 'Yenile'nin gerçek kapsamını (SenkronKapsami,
        diğer filtrelerden bağımsız) açıkça onaylatan modal — bkz. _yenile
        yorumu. True: kullanıcı 'Devam Et' dedi."""
        sonuc = {"devam": False}
        top = tk.Toplevel(self.app)
        top.title("Yenile — Kapsam Onayı")
        top.configure(bg=C.CARD)
        top.transient(self.app)
        top.resizable(False, False)

        tk.Label(top, text="Bu, birden çok yargı türünü tarayacak", bg=C.CARD, fg=C.CLAY,
                 font=self.app.f_card_t, anchor="w", justify="left").pack(anchor="w", padx=18, pady=(16, 6))
        metin = (f"{detay}\n\nÜstteki Yargı Türü \"Tümü\" olduğu için Yenile, diğer "
                 "filtrelerden (Mahkeme/Dosya Türü/Durum/Tarih/Taraf vb.) BAĞIMSIZ "
                 "olarak çalışır — bu filtreler yalnız ekrandaki görünümü daraltır, "
                 "UYAP'tan neyin çekileceğini etkilemez. Yalnız belirli bir yargı "
                 "türünü taramak isterseniz Vazgeç'e basıp üstteki Yargı Türü "
                 "kutusundan seçim yapın.")
        txt = tk.Text(top, bg=C.CARD, fg=C.INK, relief="flat", font=self.app.f_body,
                      wrap="word", width=64, height=8, padx=4, pady=4,
                      highlightthickness=0, state="normal")
        txt.insert("1.0", metin)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=18)

        def _devam():
            sonuc["devam"] = True
            top.destroy()

        btn_row = tk.Frame(top, bg=C.CARD)
        btn_row.pack(pady=14)
        self._btn(btn_row, "Vazgeç", top.destroy, "ghost").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Devam Et", _devam, "primary").pack(side="left")

        top.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - top.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - top.winfo_height()) // 2
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_set()
        top.wait_window()
        return sonuc["devam"]

    def _yenile_tumu(self):
        self._yenile_cakisma_kontrolu({"tum_turler": True})

    def duraklat_toggle(self):
        if not self._aktif_kontrol:
            return
        paused = self._aktif_kontrol.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self._aktif_kontrol:
            self._aktif_kontrol.durdur()

    def _yenile_bitti_ui(self):
        self._aktif_kontrol = None
        self.yenile_btn.set_state("normal")
        self.yenile_tumu_btn.set_state("normal")
        self.barkod_islem_btn.set_state("normal")
        self.duraklat_btn.set_state("disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("disabled")
        toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    # ─────────────────────────── Dosya Görüntüle ───────────────────────────
    def _dosya_goruntule(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden bir dosya seçin.")
            return
        try:
            rec = self.kayitlar[int(secim[0])]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satır bulunamadı; listeyi yenileyin.")
            return
        self.detay_btn.set_state("disabled")
        self.durum_lbl.config(text="Dosya ayrıntısı alınıyor…")
        threading.Thread(target=self._dosya_goruntule_bg, args=(rec,), daemon=True).start()

    def _dosya_goruntule_bg(self, rec):
        # bkz. icra_dosyalarim.py'deki AYNI stale-while-revalidate GERİ ALMA
        # gerekçesi (kullanıcı bulgusu, 2026-07-13) — okunabilir takibin
        # türü/şekli/yolu metni yalnız canlı yanıtta var, önbellekte YOK.
        sonuc = dosya_core.dosya_detay_goster_ve_kaydet(rec, log_fn=self._log)
        # Kullanıcı bulgusu (2026-08-03): "Dosya Görüntüle" penceresinde
        # barkod_sorgu.py'nin yazdığı barkod/tebliğ/mazbata dönüş bilgileri
        # hiç görünmüyordu — ayrı bir sorgu ile eklenir. DOĞAL ANAHTAR
        # (birim_id+dosya_no) İLE filtrelenir, salt dosyaId İLE DEĞİL: dosyaId
        # oturuma göre değiştiğinden, bu ekrandaki `rec` DB'den okunmuş ESKİ
        # bir dosyaId taşıyabilir; aradan Barkod Sorgu modülü aynı dosyayı
        # YENİ bir oturumla çözüp Dosya.dosya_id'yi güncellemiş olabilir —
        # bu durumda eski dosyaId'yle filtrelemek barkod DB'de var olduğu
        # hâlde YANLIŞLIKLA BOŞ SONUÇ döndürüyordu (kullanıcı bulgusu, 2026-08-03).
        try:
            barkodlar = dosya_core.tebligat_barkod_gecmisi_listele(
                dosya_id=rec.get("dosyaId"), birim_id=rec.get("birimId"),
                dosya_no=rec.get("dosyaNo"), dosya_tur_kod=rec.get("dosyaTurKod"))
        except Exception:
            barkodlar = []
        self.result_q.put(("detay", (rec, sonuc, barkodlar)))

    # ────────────────── Seçilenlere İşlem Yap (Barkod Sorgu) ──────────────────
    # Kullanıcı isteği (2026-08-04): "sadece açık dosyalara işlem yapmak
    # için" — Durum filtresiyle (zaten var olan) daraltılmış listeden istenen
    # satırlar (Ctrl/Shift ile çoklu seçim) işaretlenip tek düğmeyle
    # barkod_sorgu.calistir'in "seçili dosyalar modu"na (bkz. barkod_sorgu.py
    # calistir docstring'i — en az {"birimAdi","dosyaNo"} bekler, bu ekranın
    # `self.kayitlar` kayıtlarıyla AYNI biçim) gönderilir. barkod_sorgu_panel.py
    # ekranındaki "Seçilenleri Sorgula" ile AYNI çekirdek çağrı — o ekran
    # kendi (İcra'ya daraltılmış) dosya listesini AYRICA tutar (bkz. o
    # dosyanın başlığı, "paralel yaz" kararı); burada var olan zengin
    # filtreli (mahkeme/taraf/durum/kesinleşme) listeden DOĞRUDAN
    # tetiklenebilsin diye BURADA da eklenir.
    def _secilenlere_islem_yap(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden en az bir dosya seçin (Ctrl/Shift ile çoklu seçim).")
            return
        try:
            secili_kayitlar = [self.kayitlar[int(iid)] for iid in secim]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        # Yalnız İcra dosyalarında anlamlı — barkod_sorgu Kapalı Tebligat
        # evrakını arar, bu yalnız İcra dosyalarında olur.
        icra_disi = [r for r in secili_kayitlar if r.get("yargi_turu") != dosya_core.YARGI_TURU_ICRA]
        if icra_disi and len(icra_disi) == len(secili_kayitlar):
            self.durum_lbl.config(text="Barkod Sorgu yalnız İcra dosyalarında çalışır; seçimde İcra dosyası yok.")
            return
        secili_kayitlar = [r for r in secili_kayitlar if r.get("yargi_turu") == dosya_core.YARGI_TURU_ICRA]
        if icra_disi:
            self._log(f"ℹ️ Seçimdeki {len(icra_disi)} İcra-dışı dosya atlandı (Barkod Sorgu yalnız İcra'da çalışır).")
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.barkod_kontrol.ad, self.barkod_kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.barkod_kontrol.ad, self.barkod_kontrol,
                lambda: self._barkod_islem_baslat(secili_kayitlar),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._barkod_islem_baslat(secili_kayitlar)

    def _barkod_islem_baslat(self, kayitlar):
        self.barkod_kontrol.sifirla()
        self._aktif_kontrol = self.barkod_kontrol
        self.yenile_btn.set_state("disabled")
        self.yenile_tumu_btn.set_state("disabled")
        self.barkod_islem_btn.set_state("disabled")
        self.duraklat_btn.set_state("normal")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal")
        self.durum_lbl.config(text=f"{len(kayitlar)} dosya için barkod sorgusu çalışıyor…")
        self._log(f"▶ {len(kayitlar)} seçili dosya için barkod sorgusu başlıyor…")

        def bg():
            try:
                sonuc = barkod_sorgu.calistir(kayitlar, self._log, kontrol=self.barkod_kontrol)
                self.result_q.put(("barkod_islem_bitti", sonuc))
            except Exception as e:
                self.result_q.put(("barkod_islem_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def _barkod_islem_bitti_ui(self):
        self._aktif_kontrol = None
        self.yenile_btn.set_state("normal")
        self.yenile_tumu_btn.set_state("normal")
        self.barkod_islem_btn.set_state("normal")
        self.duraklat_btn.set_state("disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("disabled")
        toplu_is_kontrol.KAYIT_DEFTERI.sil(self.barkod_kontrol.ad)

    # ─────────────────────────── Excel'e Aktar ───────────────────────────
    def _excel_aktar(self):
        """O an EKRANDA GÖRÜNEN listeyi (sütun-başlığı filtreleri + sıralama
        DAHİL — `self.kayitlar`, `_yerel_uygula`'nın son ürettiği hâli) Excel'e
        yazar. Üst çubuktaki filtreler de zaten `self.kayitlar`'ı daralttığından
        (bkz. `_tabloyu_doldur` -> `_yerel_uygula`) ayrıca bir ayrım yapmaya
        gerek yok: kullanıcı ne görüyorsa dosyaya o gider."""
        if not self.kayitlar:
            self.durum_lbl.config(text="Aktarılacak kayıt yok — önce filtreleyin.")
            return
        varsayilan_ad = f"Dosyalarim_{time.strftime('%Y%m%d_%H%M')}.xlsx"
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
            ws.title = "Dosyalarım"
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

    def _sessiz_dialog(self, baslik, metin, hata=False):
        """messagebox.showinfo/showerror YERİNE: Windows'ta native mesaj
        kutusu ikonu (bilgi/hata) sistem sesi çalar — kullanıcı bulgusu,
        2026-07-12: çift tıkla sık açılan bu pencere ses çıkarmamalı. Kendi
        Toplevel'imiz Win32 MessageBox API'sini hiç çağırmadığından sessizdir."""
        top = tk.Toplevel(self.app)
        top.title(baslik)
        top.configure(bg=C.CARD)
        top.transient(self.app)
        top.resizable(False, False)

        renk = C.CLAY if hata else C.SAGE_DK
        tk.Label(top, text=baslik, bg=C.CARD, fg=renk, font=self.app.f_card_t,
                 anchor="w", justify="left").pack(anchor="w", padx=18, pady=(16, 6))
        satir_sayisi = metin.count("\n") + 2
        txt = tk.Text(top, bg=C.CARD, fg=C.INK, relief="flat", font=self.app.f_body,
                      wrap="word", width=64, height=min(max(satir_sayisi, 4), 22),
                      padx=4, pady=4, highlightthickness=0, state="normal")
        txt.insert("1.0", metin)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=18)
        self._btn(top, "Tamam", top.destroy, "primary").pack(pady=14)

        top.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - top.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - top.winfo_height()) // 2
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_set()

    def _detay_goster(self, veri):
        rec, sonuc, barkodlar = veri
        self.detay_btn.set_state("normal")
        ham, aile, kaydedildi, hata, taraflar = sonuc
        if hata:
            self.durum_lbl.config(text="Dosya ayrıntısı alınamadı")
            self._sessiz_dialog("Dosya Görüntüle", hata, hata=True)
            return
        self.durum_lbl.config(text="Dosya ayrıntısı kaydedildi")
        baslik = f"Dosya Bilgileri — {rec.get('dosyaNo', '') or ''}".rstrip(" —")
        self._detay_pencere(baslik, rec, ham, aile, taraflar, kaydedildi, barkodlar)
        if kaydedildi:
            # Kullanıcı bulgusu (2026-07-12): "Dosya Görüntüle" yeni taraf/ayrıntı
            # verisini DB'ye kaydediyordu ama listbox'ı hiç yenilemiyordu —
            # kullanıcı elle "Filtrele"ye basmadan yeni veriyi göremiyordu.
            self._filtrele()

    # ─────────────────────────── Dosya Görüntüle: sekmeli ayrıntı penceresi ──
    # Kullanıcı isteği (2026-07-13): entry değil LABEL — daha kompakt, tek
    # sekmede toplanmış "şimdiye kadar çekilen veri" özeti. İleride buraya çok
    # sayıda yeni sekme eklenecek (bkz. _detay_pencere), bu yüzden alan
    # yerleşimi iki sütunlu (yatayda sıkışık, dikeyde kısa) tasarlandı.
    def _alan_satirlari(self, parent, alanlar, sutun_sayisi=2):
        """`alanlar` = [(etiket, değer), ...] — label:değer çiftlerini iki
        grup hâlinde (yan yana) ızgaraya döşer, `_readonly_entry`'nin eski
        Entry tabanlı hâlinden çok daha az dikey yer kaplar."""
        for g in range(sutun_sayisi):
            parent.columnconfigure(g * 2 + 1, weight=1)
        for i, (lbl, val) in enumerate(alanlar):
            row, grup = divmod(i, sutun_sayisi)
            col = grup * 2
            tk.Label(parent, text=lbl, bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_small,
                     anchor="w").grid(row=row, column=col, sticky="w",
                                      padx=(18 if col == 0 else 24, 8), pady=4)
            tk.Label(parent, text=str(val) if val else "—", bg=C.CARD, fg=C.INK,
                     font=self.app.f_body, anchor="w", justify="left",
                     wraplength=240).grid(row=row, column=col + 1, sticky="w",
                                          padx=(0, 18), pady=4)

    def _ozet_sekmesi(self, parent, rec, ham, aile):
        """Künye + yargı-türüne özgü ayrıntı — TEK sekmede, kompakt etiket
        satırları (kullanıcı isteği: veri çok sekmeye dağılmasın)."""
        alanlar = [
            ("Yargı Türü", rec.get("yargi_turu_adi", "")),
            ("Yargı Birimi", rec.get("birimAdi", "")),
            ("Dosya No", rec.get("dosyaNo", "")),
            ("Dosya Türü", rec.get("dosyaTur", "")),
            ("Durum", rec.get("dosyaDurum", "")),
            ("Açılış Tarihi", rec.get("acilisTarihi", "")),
        ]
        if aile == "icra":
            alanlar += [
                ("Takibin Türü", ham.get("takibinTuru_metin") or ham.get("takibinTuru", "")),
                ("Takibin Şekli", ham.get("takibinSekli_metin") or ham.get("takibinSekli", "")),
                ("Takibin Yolu", ham.get("takibinYolu_metin") or ham.get("takibinYolu", "")),
                ("Alacak Kalemi Toplam", ham.get("alacakKalemToplamTutar", "")),
                ("Vekalet Ücreti", ham.get("vekaletUcreti", "")),
                ("Tahsil Harcı", ham.get("tahsilHarci", "")),
            ]
        elif aile == "hukuk":
            alanlar += [
                ("Dava Açılış Türü", ham.get("davaAcilisTuru", "")),
                ("Dava Türleri", ham.get("davaTurleriStr", "")),
                ("İlgili Dava Listesi", ham.get("ilgiliDavaListesiStr", "")),
                ("Duruşma Tarihi", ham.get("durusmaTarihi", "")),
            ]
        self._alan_satirlari(parent, alanlar)
        if aile not in ("icra", "hukuk"):
            tk.Label(parent, text="Bu yargı türü için henüz ek ayrıntı görüntüleme desteklenmiyor.",
                     bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small, anchor="w",
                     justify="left", wraplength=500).grid(row=99, column=0, columnspan=4,
                                                          sticky="w", padx=18, pady=(10, 4))

    def _taraflar_sekmesi(self, parent, taraflar):
        # Kesinleşme Durumu/Tebliğ Durumu (kullanıcı isteği, 2026-08-04:
        # "Dosyaya tıkladığımızda açılan pencerede tebligat ve kesinleşme
        # durumunu da görmek istiyorum") — yalnız borçlu satırlarında dolu
        # olur (bkz. dosya_core._taraflar_kesinlesme_bilgisi_ekle), diğer
        # rollerde (alacaklı/davacı/davalı vb.) boş "—" gösterilir.
        parent.columnconfigure(1, weight=1)
        for c, baslik in enumerate(("Rol", "Ad / Unvan", "Vekil", "Kesinleşme Durumu", "Tebliğ Durumu")):
            tk.Label(parent, text=baslik, bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_nav_b,
                     anchor="w").grid(row=0, column=c, sticky="w",
                                      padx=(18 if c == 0 else 0, 18 if c == 4 else 10),
                                      pady=(12, 4))
        for i, t in enumerate(taraflar, start=1):
            tk.Label(parent, text=t.get("rol", "") or "—", bg=C.CARD, fg=C.INK,
                     font=self.app.f_body, anchor="w").grid(row=i, column=0, sticky="w",
                                                            padx=(18, 10), pady=3)
            for c, val in ((1, t.get("adi", "")), (2, (t.get("vekil") or "").strip("[]")),
                           (3, t.get("kesinlesmeDurumu", "")), (4, t.get("tebligatDurumu", ""))):
                tk.Label(parent, text=val or "—", bg=C.CARD, fg=C.INK, font=self.app.f_body,
                         anchor="w", justify="left", wraplength=200).grid(
                    row=i, column=c, sticky="w", padx=(0, 18 if c == 4 else 10), pady=3)

    def _barkod_sekmesi(self, parent, barkodlar):
        """Barkod Sorgu (Kapalı Tebligat — PTT) modülünün DB'ye yazdığı
        sonuçları gösterir — kullanıcı bulgusu (2026-08-03): "Dosya
        Görüntüle" bu bilgiyi hiç göstermiyordu, ayrı bir ekranda (Barkod
        Sorgu modülü, Bölüm 2 "Geçmiş Sonuçlar") aranması gerekiyordu.
        Yalnız o dosya için, en yeniden eskiye, salt-okunur bir tablo."""
        parent.columnconfigure(0, weight=1)
        cols = [("evrakAciklama", "Evrak Açıklaması", 220), ("barkod", "Barkod", 110),
                ("pttDurumu", "PTT Durumu", 160), ("sonIslemTarihi", "Son İşlem Tarihi", 100),
                ("tebligMazbatasiVar", "Tebliğ Mazbatası", 100),
                ("kapaliTebligMazbatasiVar", "Kapalı Mazbata", 100),
                ("sorguZamani", "Sorgu Zamanı", 120)]
        tree = ttk.Treeview(parent, columns=[c[0] for c in cols], show="headings", height=8)
        for k, lbl, w in cols:
            tree.heading(k, text=lbl)
            tree.column(k, width=w, anchor="w", stretch=False)
        for rec in barkodlar:
            tree.insert("", "end", values=[rec.get(k, "") for k, _l, _w in cols])
        tree.grid(row=0, column=0, sticky="nsew", padx=18, pady=12)
        ysb = tk.Scrollbar(parent, orient="vertical", command=tree.yview)
        ysb.grid(row=0, column=1, sticky="ns", pady=12)
        tree.configure(yscrollcommand=ysb.set)
        parent.rowconfigure(0, weight=1)

    # Aynı anda birden çok ayrıntı penceresi açılabilsin diye (kullanıcı
    # isteği, 2026-07-13) her yeni pencere hafifçe kaydırılmış konumda açılır
    # — üst üste tam binmesinler.
    _detay_pencere_sayaci = 0

    def _detay_pencere(self, baslik, rec, ham, aile, taraflar, kaydedildi, barkodlar=None):
        """Dosya Görüntüle sonucu: messagebox/metin bloğu YERİNE gerçek,
        BAĞIMSIZ bir pencere (kullanıcı isteği, 2026-07-13): büyütülebilir/
        simge durumuna küçültülebilir standart pencere, modal DEĞİL — kullanıcı
        aynı anda birden çok dosyanın ayrıntı penceresini açık tutabilmeli."""
        top = tk.Toplevel(self.app)
        top.title(baslik)
        top.configure(bg=C.CARD)
        top.geometry("760x560")
        top.minsize(620, 420)
        top.resizable(True, True)
        # BİLEREK: transient/grab_set YOK — pencere kendi başına büyütülüp
        # küçültülebilsin (görev çubuğunda kendi simgesi) ve ana pencereyi ya
        # da başka bir ayrıntı penceresini KİLİTLEMESİN.

        tk.Label(top, text=baslik, bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_card_t,
                 anchor="w", justify="left").pack(anchor="w", padx=18, pady=(16, 10))

        # Canlı sorgu başarısız olup DB önbelleğine düşüldüyse (bkz.
        # dosya_core._ham_db_den_olustur, kullanıcı bulgusu 2026-07-14)
        # kullanıcı bunun GÜNCEL olmayabileceğini bilsin.
        if (ham or {}).get("_onbellekten"):
            tk.Label(top, text="⚠️ Canlı sorgu şu an başarısız — daha önce kaydedilmiş veri gösteriliyor (güncel olmayabilir).",
                     bg=C.CARD, fg=C.CLAY, font=self.app.f_small, anchor="w",
                     justify="left", wraplength=700).pack(anchor="w", padx=18, pady=(0, 6))

        nb = ttk.Notebook(top, style="Dosya.TNotebook")
        nb.pack(fill="both", expand=True, padx=18)

        tab_ozet = tk.Frame(nb, bg=C.CARD)
        nb.add(tab_ozet, text="Genel Bilgiler")
        self._ozet_sekmesi(tab_ozet, rec, ham, aile)

        if taraflar:
            tab_taraf = tk.Frame(nb, bg=C.CARD)
            nb.add(tab_taraf, text="Taraflar")
            self._taraflar_sekmesi(tab_taraf, taraflar)

        if barkodlar:
            tab_barkod = tk.Frame(nb, bg=C.CARD)
            nb.add(tab_barkod, text="Barkod / Tebligat")
            self._barkod_sekmesi(tab_barkod, barkodlar)

        alt = tk.Frame(top, bg=C.CARD)
        alt.pack(fill="x", padx=18, pady=(10, 16))
        if kaydedildi:
            tk.Label(alt, text="Yerel veritabanına kaydedildi.", bg=C.CARD, fg=C.INK_FAINT,
                     font=self.app.f_small).pack(side="left")
        self._btn(alt, "Kapat", top.destroy, "primary").pack(side="right")

        top.update_idletasks()
        kaydirma = (DosyalarimGenelPanel._detay_pencere_sayaci % 8) * 28
        DosyalarimGenelPanel._detay_pencere_sayaci += 1
        x = self.app.winfo_rootx() + 40 + kaydirma
        y = self.app.winfo_rooty() + 40 + kaydirma
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.focus_set()

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                # Her mesaj kendi try/except'i içinde işlenir — biri (ör.
                # _detay_goster) beklenmedik bir istisna atarsa bile döngünün
                # sonundaki self.app.after(300, self._poll) HER ZAMAN çalışır.
                # Aksi halde tek bir istisna zamanlayıcıyı kalıcı olarak
                # durdurur ve listbox bir daha HİÇ güncellenmez (kullanıcı
                # bulgusu, 2026-07-12).
                try:
                    if tip == "alanlar":
                        self._alanlari_doldur(*veri)
                    elif tip == "birimler":
                        self._birim_kod = {b.get("ad", b.get("kod", "")): b.get("kod", "") for b in veri}
                        self.birim_cb["values"] = ["Tümü"] + list(self._birim_kod.keys())
                    elif tip == "mahkemeler":
                        self._mahkeme_kod = {m.get("ad", m.get("birimId", "")): m.get("birimId", "") for m in veri}
                        self.mahkeme_cb["values"] = ["Tümü"] + list(self._mahkeme_kod.keys())
                    elif tip == "dosya_turleri":
                        self._dosya_tur_kod = {ad: kod for kod, ad in veri}
                        self.dosya_tur_cb["values"] = ["Tümü"] + list(self._dosya_tur_kod.keys())
                    elif tip == "taraf_secenekleri":
                        alacaklilar, borclular = veri
                        self.alacakli_cb["values"] = alacaklilar
                        self.borclu_cb["values"] = borclular
                    elif tip == "kayitlar":
                        self._tabloyu_doldur(veri)
                    elif tip == "yenilendi":
                        self._yenile_bitti_ui()
                        toplam, sonuclar = veri
                        self.durum_lbl.config(text=f"✔ Güncellendi ({toplam} kayıt, {len(sonuclar)} kapsam).")
                        self._filtrele()
                    elif tip == "yenile_hata":
                        self._yenile_bitti_ui()
                        self.durum_lbl.config(text=f"Güncellenemedi: {veri}")
                    elif tip == "detay":
                        self._detay_goster(veri)
                    elif tip == "barkod_islem_bitti":
                        self._barkod_islem_bitti_ui()
                        adet = len(veri) if veri else 0
                        self.durum_lbl.config(text=f"✔ Barkod sorgusu tamamlandı ({adet} satır sonuç).")
                        self._log(f"✅ Barkod sorgusu tamamlandı — {adet} satır sonuç.")
                    elif tip == "barkod_islem_hata":
                        self._barkod_islem_bitti_ui()
                        self.durum_lbl.config(text=f"Barkod sorgusu başarısız: {veri}")
                    elif tip == "excel_tamam":
                        self._excel_aktarildi(veri)
                    elif tip == "excel_hata":
                        self.excel_btn.set_state("normal")
                        self.durum_lbl.config(text=f"Excel'e aktarılamadı: {veri}")
                    elif tip == "log":
                        self._append_log(str(veri))
                except Exception as e:
                    self._append_log(f"⚠️ Ekran güncellenemedi ({tip}): {e}")
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
