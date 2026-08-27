# -*- coding: utf-8 -*-
"""
XML Takip Açma — Gömülü Panel
==============================
uyap_core.xml_takip (parse + takip.prepare/finalize) üzerinden UYAP'ın "İcra Takip
Açılış - XML" (Kota Tipi: Banka Dosyası) ekranını otomatikleştirir. potek_takip.py/
mts_takip.py ile AYNI mimari (panelin canlı UYAP bağlantısı + is_kuyrugu iş kuyruğu +
onay akışı) ama veri kaynağı Excel DEĞİL, UYAP'ın kendi "exchangeData" XML formatı —
bir XML dosyası BİRDEN FAZLA takip (<dosya>) içerebilir, hepsi TEK TEK onaydan geçer.

ÖNEMLİ (bkz. uyap_core.xml_takip.takip modül başlığı): harç hesaplama adımının
(icra_harc_hesaplama_islemleri.ajx) İlamsızList zarfı henüz CANLI DOĞRULANMADI —
ilk denemelerde "Hazırlanamadı" hatası beklenir, dosya AÇILMAZ, risk yoktur.

Eklenti: mağazadan 'xml_takip' etkin değilse menüde görünmez.
"""

import os
import sys
import json
import base64
import threading
import urllib.request
import urllib.error

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from theme import C, RoundButton
from . import is_kuyrugu
from . import takip_sonuc_raporu

_OFFICE_BASE = "http://127.0.0.1:8800"

ODEME_TIPLERI = [("7", "e-Barobirlik Kart"), ("4", "Vakıfbank")]

# Kota / Daire Tevzi Tipi — UYAP'ın "Takip Açılış - XML" ekranındaki ilk dropdown'ın 5
# seçeneği. "avukat"/"kurum"/"takip_tipi" için gönderim şekli uyap_core.xml_takip.takip
# içinde HENÜZ canlı doğrulanmadı (bkz. o modülün KOTA_TIPI_DESTEKSIZ) — burada da AYNI
# liste tekrarlanır ki kullanıcı seçmeden önce arayüzden uyarılsın; iş kuyruğuna
# gönderilse bile prepare() zaten reddedecektir, bu yalnız erken/anlaşılır bir uyarı.
KOTA_SECENEKLERI = [
    ("banka", "Banka Dosyası"), ("gayrimenkul", "Gayrimenkul Dosyası"),
    ("avukat", "Avukat"), ("kurum", "Kurum"), ("takip_tipi", "Takip Tipleri"),
]
_KOTA_TIPI_DESTEKSIZ_GUI = {"avukat", "kurum", "takip_tipi"}


def _ajx(endpoint, payload):
    """İl/Adliye/Kota-Tevzi-Tipi dropdown'larını CANLI dolduran yardımcı — panelin UYAP
    bağlantısı (8800 ofis proxy) üzerinden, tarayıcısız. Her tıklamada bir sonraki
    dropdown'ı doldurmak için kullanılır (illerIlcelerGetir.ajx / icraTakipAdliyeler.ajx /
    tevziSiraTipleri.ajx) — toplu bir önbellek TUTULMAZ, her adliye seçiminde yeniden
    sorgulanır (kullanıcı tercihi: veriler her zaman güncel kalsın)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{_OFFICE_BASE}/{endpoint}", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("accept", "application/json, text/plain, */*")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _uyap_core_ekle():
    """`Uyap Haricen Giriş` klasörünü sys.path'e ekler (uyap_core orada)."""
    here = os.path.dirname(os.path.abspath(__file__))
    kok = os.path.dirname(os.path.dirname(here))
    uhg = os.path.join(kok, "Uyap Haricen Giriş")
    if uhg not in sys.path:
        sys.path.insert(0, uhg)
    return uhg


def _yerel_yetki_jetonu_opener_kur():
    """bkz. Panel/modules/potek_takip.py — aynı desen (X-Uyap-Local-Token)."""
    token_yolu = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UyapIcra", "gw_local_token")
    try:
        with open(token_yolu, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        return
    if not token:
        return

    class _JetonEnjektor(urllib.request.BaseHandler):
        def http_request(self, req):
            try:
                if req.host and req.host.split(":")[0] in ("127.0.0.1", "localhost") \
                        and not req.has_header("X-uyap-local-token"):
                    req.add_unredirected_header("X-Uyap-Local-Token", token)
            except Exception:
                pass
            return req
        https_request = http_request

    urllib.request.install_opener(urllib.request.build_opener(_JetonEnjektor()))


_yerel_yetki_jetonu_opener_kur()


class XmlTakipPanel:
    BASLIK = "XML Takip Açma"

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app

        self.xml_yolu = None
        self.dosyalar = []          # uyap_core.xml_takip.parse.Dosya listesi
        self.vekalet_yolu = None
        self.dayanak_yollari = []
        self.job_id = None
        self._calisiyor = False
        self._log_sayac = 0
        self._onay_aktif = False
        self._son_sonuc = {}

        self._build()
        self._baglanti_kontrol()

    # ─────────────────────────────────────────────────────── arayüz iskeleti
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=C.BG, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        vsb = tk.Scrollbar(wrap, command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=vsb.set)
        body = tk.Frame(canvas, bg=C.BG)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        ic = tk.Frame(body, bg=C.BG)
        ic.pack(fill="both", expand=True, padx=40, pady=(30, 24))

        tk.Label(ic, text=self.BASLIK, bg=C.BG, fg=C.INK, font=self.app.f_h1).pack(anchor="w")
        tk.Label(ic, text="İcra Takip Açılış - XML (Kota Tipi: Banka Dosyası) — UYAP'ın kendi "
                          "değişim formatındaki XML'i panelin UYAP bağlantısı üzerinden açar "
                          "(tarayıcısız). Bir XML birden fazla takip içerebilir, her biri "
                          "TEK TEK onaydan geçer.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=820,
                 justify="left").pack(anchor="w", pady=(6, 0))
        self.baglanti_lbl = tk.Label(ic, text="● Bağlantı kontrol ediliyor…", bg=C.BG,
                                     fg=C.INK_FAINT, font=self.app.f_small)
        self.baglanti_lbl.pack(anchor="w", pady=(4, 0))
        tk.Frame(ic, bg=C.LINE, height=1).pack(fill="x", pady=(16, 0))

        self._giris_kur(ic)
        self._xml_bar_kur(ic)
        self._evrak_bar_kur(ic)
        self._onay_bar_kur(ic)
        self._eylem_bar_kur(ic)
        self._log_kur(ic)

        self._butonlar_guncelle()

    def _kart(self, parent):
        return tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)

    def _giris_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="DOSYA BİLGİLERİ", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")

        self._iller = []
        self._adliyeler = []
        self._tevzi_liste = []
        self.il_kodu = None
        self.adliye_birim_id = None
        self.takip_tipi_kod = None
        self.takip_tipi_adi = None

        row1 = tk.Frame(ic, bg=C.CARD)
        row1.pack(fill="x", pady=(8, 0))
        tk.Label(row1, text="İl", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=18, anchor="w").pack(side="left")
        self.il_cb = ttk.Combobox(row1, state="disabled", width=24, values=["Yükleniyor…"])
        self.il_cb.current(0)
        self.il_cb.pack(side="left")
        self.il_cb.bind("<<ComboboxSelected>>", self._il_secildi)

        row2 = tk.Frame(ic, bg=C.CARD)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="Adliye", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=18, anchor="w").pack(side="left")
        self.adliye_cb = ttk.Combobox(row2, state="disabled", width=24, values=["Önce il seçin"])
        self.adliye_cb.current(0)
        self.adliye_cb.pack(side="left")
        self.adliye_cb.bind("<<ComboboxSelected>>", self._adliye_secildi)

        row3 = tk.Frame(ic, bg=C.CARD)
        row3.pack(fill="x", pady=(8, 0))
        tk.Label(row3, text="Kota / Daire Tevzi Tipi", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=18, anchor="w").pack(side="left")
        self.kota_cb = ttk.Combobox(row3, state="disabled", width=24,
                                    values=[ad for _, ad in KOTA_SECENEKLERI])
        self.kota_cb.current(0)
        self.kota_cb.pack(side="left")
        self.kota_cb.bind("<<ComboboxSelected>>", self._kota_secildi)
        self.kota_uyari_lbl = tk.Label(row3, text="", bg=C.CARD, fg=C.CLAY, font=self.app.f_small,
                                       wraplength=420, justify="left")
        self.kota_uyari_lbl.pack(side="left", padx=(10, 0))

        row4 = tk.Frame(ic, bg=C.CARD)
        row4.pack(fill="x", pady=(8, 0))
        tk.Label(row4, text="Takip Tipleri", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=18, anchor="w").pack(side="left")
        self.takip_tipi_cb = ttk.Combobox(row4, state="disabled", width=24, values=["—"])
        self.takip_tipi_cb.current(0)
        self.takip_tipi_cb.pack(side="left")
        self.takip_tipi_cb.bind("<<ComboboxSelected>>", self._takip_tipi_secildi)

        row5 = tk.Frame(ic, bg=C.CARD)
        row5.pack(fill="x", pady=(8, 0))
        tk.Label(row5, text="Ödeme Tipi (Harç)", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=18, anchor="w").pack(side="left")
        self.odeme_cb = ttk.Combobox(row5, state="readonly", width=28,
                                     values=[ad for _, ad in ODEME_TIPLERI])
        self.odeme_cb.current(0)
        self.odeme_cb.pack(side="left")

        self._il_listesi_yukle()

    # ─────────────────────────────────────────────────── İl/Adliye/Kota (canlı, kademeli)
    def _il_listesi_yukle(self):
        def isi():
            try:
                data = _ajx("illerIlcelerGetir.ajx", {})
                iller = sorted((data or []), key=lambda i: i.get("ad") or "")
            except Exception as e:
                self.app.after(0, lambda e=e: self._log_yaz(f"⚠️ İl listesi alınamadı: {e}"))
                return
            self.app.after(0, lambda: self._il_listesi_geldi(iller))
        threading.Thread(target=isi, daemon=True).start()

    def _il_listesi_geldi(self, iller):
        self._iller = iller
        if not self.il_cb.winfo_exists():
            return
        self.il_cb.config(state="readonly", values=[i.get("ad", "") for i in iller])
        self.il_cb.set("")

    def _il_secildi(self, _evt=None):
        idx = self.il_cb.current()
        if idx < 0 or idx >= len(self._iller):
            return
        self.il_kodu = self._iller[idx].get("il")
        self.adliye_birim_id = None
        self._adliyeler = []
        self._tevzi_liste = []
        self.adliye_cb.config(state="disabled", values=["Yükleniyor…"])
        self.adliye_cb.current(0)
        self.kota_cb.config(state="disabled")
        self.kota_uyari_lbl.config(text="")
        self.takip_tipi_cb.config(state="disabled", values=["—"])
        self.takip_tipi_cb.current(0)
        self.takip_tipi_kod = None
        self.takip_tipi_adi = None
        self._butonlar_guncelle()

        il_kodu = self.il_kodu

        def isi():
            try:
                data = _ajx("icraTakipAdliyeler.ajx", {"ilKodu": il_kodu})
                adliyeler = sorted((data or []), key=lambda a: a.get("adliyeIsmi") or "")
            except Exception as e:
                self.app.after(0, lambda e=e: self._log_yaz(f"⚠️ Adliye listesi alınamadı: {e}"))
                return
            self.app.after(0, lambda: self._adliye_listesi_geldi(adliyeler))
        threading.Thread(target=isi, daemon=True).start()

    def _adliye_listesi_geldi(self, adliyeler):
        self._adliyeler = adliyeler
        if not self.adliye_cb.winfo_exists():
            return
        if not adliyeler:
            self.adliye_cb.config(state="disabled", values=["Bu ilde adliye bulunamadı"])
            self.adliye_cb.current(0)
            return
        self.adliye_cb.config(state="readonly", values=[a.get("adliyeIsmi", "") for a in adliyeler])
        self.adliye_cb.set("")

    def _adliye_secildi(self, _evt=None):
        idx = self.adliye_cb.current()
        if idx < 0 or idx >= len(self._adliyeler):
            return
        self.adliye_birim_id = self._adliyeler[idx].get("adliyeBirimID")
        self._tevzi_liste = []
        self.kota_cb.config(state="readonly")
        self.kota_cb.current(0)   # varsayılan: Banka Dosyası
        self.takip_tipi_cb.config(state="disabled", values=["Yükleniyor…"])
        self.takip_tipi_cb.current(0)
        self.takip_tipi_kod = None
        self.takip_tipi_adi = None
        self._kota_secildi()
        self._butonlar_guncelle()

        adliye_birim_id = self.adliye_birim_id

        def isi():
            try:
                data = _ajx("tevziSiraTipleri.ajx", {"birimId": adliye_birim_id})
                tevzi = data if isinstance(data, list) else []
            except Exception as e:
                self.app.after(0, lambda e=e: self._log_yaz(f"⚠️ Tevzi tipi listesi alınamadı: {e}"))
                return
            self.app.after(0, lambda: self._tevzi_listesi_geldi(tevzi, adliye_birim_id))
        threading.Thread(target=isi, daemon=True).start()

    def _tevzi_listesi_geldi(self, tevzi, adliye_birim_id):
        if adliye_birim_id != self.adliye_birim_id:
            return  # kullanıcı bu yanıt gelmeden başka bir adliye seçti — eskisini yoksay
        self._tevzi_liste = tevzi
        if self.kota_cb.winfo_exists() and self.kota_cb.get() == "Takip Tipleri":
            self._takip_tipi_listesini_doldur()
        elif self.takip_tipi_cb.winfo_exists() and self.takip_tipi_cb.cget("state") == "disabled":
            self.takip_tipi_cb.config(values=["—"])
            self.takip_tipi_cb.current(0)

    def _kota_tipi_kodu(self):
        secim = self.kota_cb.get()
        for kod, ad in KOTA_SECENEKLERI:
            if ad == secim:
                return kod
        return "banka"

    def _kota_secildi(self, _evt=None):
        kod = self._kota_tipi_kodu()
        if kod in _KOTA_TIPI_DESTEKSIZ_GUI:
            self.kota_uyari_lbl.config(
                text="⚠ Bu kota tipi için gönderim henüz canlı doğrulanmadı — 'Hazırla' devre dışı kalır.")
        else:
            self.kota_uyari_lbl.config(text="")
        if kod == "takip_tipi":
            self._takip_tipi_listesini_doldur()
        else:
            self.takip_tipi_cb.config(state="disabled", values=["—"])
            self.takip_tipi_cb.current(0)
            self.takip_tipi_kod = None
            self.takip_tipi_adi = None
        self._butonlar_guncelle()

    def _takip_tipi_listesini_doldur(self):
        if not self._tevzi_liste:
            self.takip_tipi_cb.config(state="disabled", values=["Bu adliyede tanımlı takip tipi yok"])
            self.takip_tipi_cb.current(0)
            return
        adlar = [t.get("tevziSiraTakipTipAdi", "") for t in self._tevzi_liste]
        self.takip_tipi_cb.config(state="readonly", values=adlar)
        self.takip_tipi_cb.set("")

    def _takip_tipi_secildi(self, _evt=None):
        idx = self.takip_tipi_cb.current()
        if idx < 0 or idx >= len(self._tevzi_liste):
            return
        it = self._tevzi_liste[idx]
        self.takip_tipi_kod = it.get("tevziSiraTakipTip")
        self.takip_tipi_adi = it.get("tevziSiraTakipTipAdi")
        self._butonlar_guncelle()

    def _xml_bar_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="TAKİP XML'İ", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")
        tk.Label(ic, text="UYAP'ın 'exchangeData' değişim formatındaki XML dosyasını seçin — "
                          "içindeki her <dosya> ayrı bir takip olarak listelenir.",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small, wraplength=780,
                 justify="left").pack(anchor="w", pady=(2, 8))
        bar = tk.Frame(ic, bg=C.CARD)
        bar.pack(fill="x")
        self.xml_btn = RoundButton(bar, "📂  XML Seç", command=self._xml_sec,
                                   kind="primary", font=self.app.f_nav_b, height=34)
        self.xml_btn.pack(side="left")
        self.xml_lbl = tk.Label(bar, text="Henüz dosya seçilmedi.", bg=C.CARD, fg=C.INK_SOFT,
                                font=self.app.f_body)
        self.xml_lbl.pack(side="left", padx=12)

        self.dosya_tv = ttk.Treeview(
            ic, columns=("id", "alacakli", "borclu", "toplam"), show="headings", height=6)
        for s, b, g in (("id", "Dosya Belirleyici", 180), ("alacakli", "Alacaklı", 220),
                        ("borclu", "Borçlu", 200), ("toplam", "Toplam (TL)", 110)):
            self.dosya_tv.heading(s, text=b)
            self.dosya_tv.column(s, width=g, anchor="e" if s == "toplam" else "w")
        self.dosya_tv.pack(fill="x", pady=(10, 0))

    def _evrak_bar_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="EVRAKLAR (TÜM takiplere uygulanır)", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")
        tk.Label(ic, text="Vekaletname ZORUNLU. Dayanak belgesi (sözleşme vb.) OPSİYONEL.",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small).pack(anchor="w", pady=(2, 8))

        self.vekalet_yolu = None
        row1 = tk.Frame(ic, bg=C.CARD)
        row1.pack(fill="x", pady=(4, 0))
        self.vekalet_btn = RoundButton(row1, "📎  Vekaletname Seç", command=self._vekalet_sec,
                                       kind="primary", font=self.app.f_nav_b, height=32)
        self.vekalet_btn.pack(side="left")
        self.vekalet_lbl = tk.Label(row1, text="Seçilmedi.", bg=C.CARD, fg=C.CLAY,
                                    font=self.app.f_body)
        self.vekalet_lbl.pack(side="left", padx=10)

        self.dayanak_yollari = []
        row2 = tk.Frame(ic, bg=C.CARD)
        row2.pack(fill="x", pady=(8, 0))
        self.dayanak_btn = RoundButton(row2, "📎  Dayanak Belge(ler) Seç (opsiyonel)",
                                       command=self._dayanak_sec, kind="ghost",
                                       font=self.app.f_nav_b, height=32)
        self.dayanak_btn.pack(side="left")
        self.dayanak_lbl = tk.Label(row2, text="Seçilmedi.", bg=C.CARD, fg=C.INK_FAINT,
                                    font=self.app.f_body)
        self.dayanak_lbl.pack(side="left", padx=10)

    def _vekalet_sec(self):
        if self._calisiyor:
            return
        yol = filedialog.askopenfilename(
            title="Vekaletname seçin",
            filetypes=[("Vekalet/İmzalı", "*.udf *.pdf"), ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.vekalet_yolu = yol
        self.vekalet_lbl.config(text=os.path.basename(yol), fg=C.SAGE_DK)

    def _dayanak_sec(self):
        if self._calisiyor:
            return
        yollar = filedialog.askopenfilenames(
            title="Dayanak belge(ler)ini seçin",
            filetypes=[("PDF/Resim", "*.pdf *.jpg *.jpeg *.png *.tif *.tiff"),
                      ("Tüm dosyalar", "*.*")])
        if not yollar:
            return
        self.dayanak_yollari = list(yollar)
        self.dayanak_lbl.config(
            text=f"{len(self.dayanak_yollari)} dosya: " +
                 ", ".join(os.path.basename(y) for y in self.dayanak_yollari),
            fg=C.SAGE_DK)

    @staticmethod
    def _dosya_b64(yol):
        with open(yol, "rb") as f:
            return {"filename": os.path.basename(yol), "b64": base64.b64encode(f.read()).decode("ascii")}

    def _onay_bar_kur(self, parent):
        self.onay_bar = tk.Frame(parent, bg=C.SAGE_TINT, highlightbackground=C.SAGE,
                                 highlightthickness=1)
        ic = tk.Frame(self.onay_bar, bg=C.SAGE_TINT)
        ic.pack(fill="x", padx=14, pady=10)
        self.onay_mesaj = tk.Label(ic, text="", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                                   font=self.app.f_body, justify="left", anchor="w", wraplength=780)
        self.onay_mesaj.pack(fill="x")
        self.onay_kalem_tv = ttk.Treeview(ic, columns=("ad", "tutar", "oran"),
                                         show="headings", height=6)
        for s, b, g in (("ad", "Kalem", 200), ("tutar", "Tutar (TL)", 130),
                        ("oran", "Faiz Oranı %", 110)):
            self.onay_kalem_tv.heading(s, text=b)
            self.onay_kalem_tv.column(s, width=g, anchor="e" if s != "ad" else "w")
        self.onay_kalem_tv.pack(fill="x", pady=(8, 8))
        btn_cer = tk.Frame(ic, bg=C.SAGE_TINT)
        btn_cer.pack(anchor="w")
        RoundButton(btn_cer, "✓ Onayla (Tevzi + İmza + Evrak + Harç Ödeme)",
                   command=lambda: self._onay_ver({"decision": "approve"}),
                   kind="primary", font=self.app.f_nav_b, height=34).pack(side="left")
        RoundButton(btn_cer, "⤼ Bu Dosyayı Atla",
                   command=lambda: self._onay_ver({"decision": "skip"}),
                   kind="ghost", font=self.app.f_nav_b, height=34).pack(side="left", padx=(8, 0))
        RoundButton(btn_cer, "⏹ Tümünü Durdur",
                   command=lambda: self._onay_ver({"decision": "cancel"}),
                   kind="stop", font=self.app.f_nav_b, height=34).pack(side="left", padx=(8, 0))
        # başlangıçta gizli (pack edilmez)

    def _eylem_bar_kur(self, parent):
        bar = tk.Frame(parent, bg=C.BG)
        bar.pack(fill="x", pady=(14, 0))
        self.baslat_btn = RoundButton(bar, "▶  Hazırla ve Kontrol Ekranını Göster",
                                      command=self._baslat, kind="primary",
                                      font=self.app.f_nav_b, height=38)
        self.baslat_btn.pack(side="left", ipadx=8)
        self.durdur_btn = RoundButton(bar, "⏹  Durdur", command=self._durdur,
                                      kind="stop", font=self.app.f_nav_b, height=38)
        self.durdur_btn.pack(side="left", padx=(8, 0))
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="left", padx=14)

    def _log_kur(self, parent):
        kart = tk.Frame(parent, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        kart.pack(fill="x", pady=(14, 0))
        bas = tk.Frame(kart, bg="#FBFAF7")
        bas.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(bas, text="📜  Günlük", bg="#FBFAF7", fg=C.INK, font=self.app.f_nav_b).pack(side="left")
        RoundButton(bas, "Temizle", command=self._log_temizle, kind="ghost",
                   font=self.app.f_small, height=28).pack(side="right")
        RoundButton(bas, "📊  Sonuçları Excel'e Aktar", command=self._sonuclari_excel_aktar,
                   kind="ghost", font=self.app.f_small, height=28).pack(side="right", padx=(0, 8))
        cer = tk.Frame(kart, bg="#FBFAF7")
        cer.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.log = tk.Text(cer, bg="#FBFAF7", fg=C.INK, relief="flat", font=self.app.f_mono,
                           wrap="word", height=12, padx=4, pady=2, state="disabled",
                           highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(cer, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)

    # ─────────────────────────────────────────────────────── yardımcılar
    def _log_yaz(self, metin):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", str(metin).rstrip("\n") + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _log_temizle(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _sonuclari_excel_aktar(self):
        kolonlar = [
            ("Dosya Belirleyici", "dosya_belirleyicisi"),
            ("Durum", "durum"),
            ("Esas No", "gercek_dosya_no"),
            ("Dosya ID", "dosya_id"),
            ("Hata", "mesaj"),
        ]
        takip_sonuc_raporu.sonuclari_excel_yaz(
            self._log_yaz, self._son_sonuc.get("sonuclar") or [], kolonlar,
            "xml_takip_sonuclari.xlsx", sheet_title="XML Takip Sonuçları")

    def _durum_yaz(self, metin):
        if self.durum_lbl.winfo_exists():
            self.durum_lbl.config(text=metin)

    def _secim_hazir_mi(self):
        if self._calisiyor:
            return False
        if not self.il_kodu or not self.adliye_birim_id:
            return False
        kod = self._kota_tipi_kodu()
        if kod in _KOTA_TIPI_DESTEKSIZ_GUI:
            return False
        if kod == "takip_tipi" and not self.takip_tipi_kod:
            return False
        return True

    def _butonlar_guncelle(self):
        self.baslat_btn.set_state("normal" if self._secim_hazir_mi() else "disabled")
        self.durdur_btn.set_state("normal" if self._calisiyor else "disabled")

    def _odeme_tipi_deger(self):
        idx = self.odeme_cb.current()
        if idx < 0 or idx >= len(ODEME_TIPLERI):
            return "7"
        return ODEME_TIPLERI[idx][0]

    # ─────────────────────────────────────────────────────── bağlantı kontrol
    def _baglanti_kontrol(self):
        def isi():
            try:
                ok, mesaj = is_kuyrugu.ofis_erisilebilir()
            except Exception as e:
                ok, mesaj = False, str(e)
            self.app.after(0, lambda: self._baglanti_yaz(ok, mesaj))
        threading.Thread(target=isi, daemon=True).start()

    def _baglanti_yaz(self, ok, mesaj):
        if not self.baglanti_lbl.winfo_exists():
            return
        if ok:
            self.baglanti_lbl.config(text="● Ofis bağlantısı hazır (iş kuyruğu erişilebilir).",
                                     fg=C.SAGE_DK)
        else:
            self.baglanti_lbl.config(
                text="● Ofis bağlantısı yok — UYAP Bağlantısı (Paylaş/Al) başlatın.", fg=C.CLAY)

    # ─────────────────────────────────────────────────────── XML seç
    def _xml_sec(self):
        if self._calisiyor:
            return
        yol = filedialog.askopenfilename(
            title="Takip XML dosyasını seçin", filetypes=[("XML", "*.xml"), ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.xml_lbl.config(text="Okunuyor…")
        self.xml_btn.set_state("disabled")

        def isi():
            try:
                _uyap_core_ekle()
                from uyap_core.xml_takip.parse import xml_dosyasindan_oku, dosya_ozet
                dosyalar = xml_dosyasindan_oku(yol)
                ozetler = [dosya_ozet(d) for d in dosyalar]
                self.app.after(0, lambda: self._xml_yuklendi(yol, dosyalar, ozetler))
            except Exception as e:
                self.app.after(0, lambda e=e: self._xml_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _xml_yuklendi(self, yol, dosyalar, ozetler):
        self.xml_btn.set_state("normal")
        if not dosyalar:
            self.xml_lbl.config(text="XML'de açılacak takip bulunamadı.")
            return
        self.xml_yolu = yol
        self.dosyalar = dosyalar
        self.xml_lbl.config(text=f"{os.path.basename(yol)} · {len(dosyalar)} takip")
        self._log_yaz(f"✓ {len(dosyalar)} takip okundu ({os.path.basename(yol)}).")
        self.dosya_tv.delete(*self.dosya_tv.get_children())
        for oz in ozetler:
            alacakli = ", ".join(a.get("ad", "") for a in oz.get("alacaklilar", [])) or "-"
            borclu = ", ".join(b.get("ad", "") for b in oz.get("borclular", [])) or "-"
            self.dosya_tv.insert("", "end", values=(
                oz.get("dosya_belirleyicisi"), alacakli, borclu, f"{oz.get('toplam', 0):.2f}"))

    def _xml_hata(self, e):
        self.xml_btn.set_state("normal")
        self.xml_lbl.config(text="Okuma hatası.")
        self._log_yaz(f"❌ XML okuma hatası: {e}")
        messagebox.showerror("XML okunamadı", str(e))

    # ─────────────────────────────────────────────────────── başlat / durdur
    def _baslat(self):
        if self._calisiyor:
            return
        if not self.dosyalar:
            messagebox.showwarning("XML eksik", "Önce takip XML dosyasını seçin.")
            return
        if not self.vekalet_yolu:
            messagebox.showwarning("Eksik evrak", "Vekaletname yüklenmedi (UYAP zorunlu istiyor).")
            return
        if not self.il_kodu or not self.adliye_birim_id:
            messagebox.showwarning("Eksik seçim", "Önce İl ve Adliye seçin.")
            return
        kota_kodu = self._kota_tipi_kodu()
        if kota_kodu in _KOTA_TIPI_DESTEKSIZ_GUI:
            messagebox.showwarning(
                "Desteklenmeyen kota tipi",
                f"'{self.kota_cb.get()}' kota tipi için UYAP'a gönderim şekli henüz canlı "
                "doğrulanmadı. Bu takibi UYAP'ın kendi 'Takip Açılış - XML' ekranından elle açın.")
            return
        if kota_kodu == "takip_tipi" and not self.takip_tipi_kod:
            messagebox.showwarning("Eksik seçim", "'Takip Tipleri' kategorisi için alt listeden bir takip tipi seçin.")
            return

        # Dayanak belgesi (sözleşme vb.) HER takip için FARKLI olabilir — tek bir
        # dosyayı XML'deki BİRDEN FAZLA takibe (farklı borçlulara) aynen uygulamak
        # yanlış olabilir. Bu yüzden yalnız TEK takipli XML'lerde otomatik uygulanır;
        # birden fazla takip varsa kullanıcı bilgilendirilip dayanak GÖNDERİLMEZ.
        dayanak_b64_list = [self._dosya_b64(y) for y in self.dayanak_yollari]
        if dayanak_b64_list and len(self.dosyalar) > 1:
            if not messagebox.askyesno(
                    "Dayanak belgesi tek takiple sınırlı",
                    f"Bu XML'de {len(self.dosyalar)} takip var, ama dayanak belgesi HER takip "
                    "için farklı olabileceğinden (ör. farklı borçluların farklı sözleşmeleri) "
                    "yalnız TEK takipli XML'lerde otomatik gönderilir. Bu çalıştırmada dayanak "
                    "belgesi HİÇBİR takibe eklenmeden devam edilsin mi?"):
                return
            dayanak_b64_list = []

        self._calisiyor = True
        self._log_sayac = 0
        self._durum_yaz("XML gönderiliyor, iş başlatılıyor…")
        self._log_yaz(f"\n=== XML TAKİP AÇMA ({len(self.dosyalar)} takip) ===")
        self._butonlar_guncelle()

        vekalet_b64 = self._dosya_b64(self.vekalet_yolu)
        vekalet_map = {d.dosya_belirleyicisi: vekalet_b64 for d in self.dosyalar}
        dayanak_map = ({d.dosya_belirleyicisi: dayanak_b64_list[0] for d in self.dosyalar}
                       if dayanak_b64_list else {})

        def isi():
            try:
                with open(self.xml_yolu, "rb") as f:
                    xml_b64 = base64.b64encode(f.read()).decode("ascii")
                params = {
                    "xml": {"filename": os.path.basename(self.xml_yolu), "b64": xml_b64},
                    "il": self.il_cb.get(), "adliye": self.adliye_cb.get(),
                    "il_kodu": self.il_kodu, "adliye_birim_id": self.adliye_birim_id,
                    "kota_tipi": kota_kodu,
                    "onay_modu": "tek_tek",
                    "odeme_tipi": self._odeme_tipi_deger(),
                    "vekalet": vekalet_map, "dayanak": dayanak_map,
                }
                job = is_kuyrugu.is_baslat("xml_takip_ac", params)
                self.app.after(0, lambda: self._is_basladi(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._is_baslatilamadi(e))
        threading.Thread(target=isi, daemon=True).start()

    def _is_baslatilamadi(self, e):
        self._calisiyor = False
        self._durum_yaz("Başlatılamadı.")
        self._log_yaz(f"❌ İş başlatılamadı: {e}")
        messagebox.showerror("Başlatılamadı", str(e))
        self._butonlar_guncelle()

    def _is_basladi(self, job):
        self.job_id = job.get("id")
        self._durum_yaz("Çalışıyor…")
        self._log_yaz(f"İş kuyruğuna alındı (id={self.job_id}).")
        self._poll()

    # ─────────────────────────────────────────────────────── poll
    def _poll(self):
        if not self.job_id:
            return

        def isi():
            try:
                job = is_kuyrugu.is_durum(self.job_id)
                self.app.after(0, lambda: self._poll_isle(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._poll_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _poll_hata(self, e):
        self._log_yaz(f"⚠️ Durum alınamadı: {e}")
        if self._calisiyor:
            self.app.after(2000, self._poll)

    def _poll_isle(self, job):
        loglar = job.get("logs") or []
        for ln in loglar[self._log_sayac:]:
            self._log_yaz(ln.get("line", ""))
        self._log_sayac = len(loglar)

        prog = job.get("progress") or {}
        if prog.get("message"):
            self._durum_yaz(prog["message"])

        status = job.get("status")
        if status == "awaiting_approval" and not self._onay_aktif:
            self._onay_goster(job.get("pending_approval") or {})

        if status in ("done", "error", "cancelled"):
            self._is_bitti(job)
            return
        if self._calisiyor:
            self.app.after(900, self._poll)

    def _is_bitti(self, job):
        self._calisiyor = False
        self.job_id = None
        self._onay_gizle()
        status = job.get("status")
        sonuc = job.get("result") or {}
        self._son_sonuc = sonuc
        if status == "done":
            self._durum_yaz(
                f"Bitti: {sonuc.get('basari', 0)} tamam, {sonuc.get('atlanan', 0)} atlandı, "
                f"{sonuc.get('hata', 0)} hata.")
            self._log_yaz("✓ İş tamamlandı.")
        elif status == "cancelled":
            self._durum_yaz("Durduruldu.")
            self._log_yaz("⏹ İş durduruldu.")
        else:
            self._durum_yaz("Hata.")
            self._log_yaz(f"❌ İş hatası: {job.get('error') or 'bilinmeyen'}")
        self._butonlar_guncelle()

    def _durdur(self):
        if not self.job_id:
            return
        self._durum_yaz("Durduruluyor…")
        jid = self.job_id

        def isi():
            try:
                is_kuyrugu.is_iptal(jid)
            except Exception:
                pass
        threading.Thread(target=isi, daemon=True).start()

    # ─────────────────────────────────────────────────────── onay ekranı
    def _onay_goster(self, pending):
        self._onay_aktif = True
        ozet = pending.get("takip") or {}
        taraflar = ", ".join(f"{t.get('ad','')} ({t.get('rol','')})" for t in (ozet.get("taraflar") or []))
        harclar = ozet.get("harclar") or []
        if harclar:
            harc_satiri = "Harç/Masraf: " + "; ".join(
                f"{h.get('ad')}: {h.get('miktar')} TL" for h in harclar)
        else:
            harc_satiri = ozet.get("harc_notu") or ""
        self.onay_mesaj.config(text=(
            f"Dosya: {ozet.get('dosya_belirleyicisi','')}  ·  Mahiyet: {ozet.get('mahiyet','')}\n"
            f"Taraflar: {taraflar}\n"
            f"Toplam Alacak (XML'in kendi beyanı): {ozet.get('toplam_alacak','-')} TL\n"
            f"{harc_satiri}\n\n"
            "AŞAĞIDAKİ TUTARLARI DİKKATLİCE KONTROL EDİN:"))
        self.onay_kalem_tv.delete(*self.onay_kalem_tv.get_children())
        for k in (ozet.get("kalemler") or []):
            oran = k.get("faiz_orani")
            self.onay_kalem_tv.insert("", "end", values=(
                k.get("ad"), f"{k.get('tutar', 0):.2f}", f"{oran:.2f}" if oran is not None else "—"))
        try:
            self.onay_bar.pack(fill="x", pady=(14, 0))
        except Exception:
            pass

    def _onay_ver(self, karar):
        self._onay_gizle()
        jid = self.job_id
        if not jid:
            return
        self._durum_yaz("Onay gönderiliyor…")

        def isi():
            try:
                is_kuyrugu.is_onayla(jid, karar)
            except Exception as e:
                self.app.after(0, lambda e=e: self._log_yaz(f"⚠️ Onay gönderilemedi: {e}"))
        threading.Thread(target=isi, daemon=True).start()

    def _onay_gizle(self):
        self._onay_aktif = False
        try:
            self.onay_bar.pack_forget()
        except Exception:
            pass
