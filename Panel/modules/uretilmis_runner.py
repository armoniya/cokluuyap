# -*- coding: utf-8 -*-
"""
Modül: Üretilen Modül Çalıştırıcı (Runner)
==========================================
TEK, ortak bir "sade çalıştır-gör" ekranı. Logger'ın ÜRETTİĞİ herhangi bir ağ-sorgu
modülünü (core) sürer: core.PARAMETRELER'den girdi kutularını kurar, "Çalıştır"a
basınca core.calistir(girdi, log) ile isteği 8800 ofis proxy'sine gönderir ve yanıtı
gösterir (liste ise tablo, değilse JSON metni). Her üretilen modül için AYRI GUI
dosyası YOK — hepsi bu tek bileşenle çalışır.
"""

import inspect
import json
import re
import sys
import threading
import importlib
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from theme import C, RoundButton

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE_TIK = os.path.dirname(os.path.abspath(__file__))
if _HERE_TIK not in sys.path:
    sys.path.insert(0, _HERE_TIK)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402

# Üretilen modül adı için KATI desen (güvenlik raporu #11): tek parça python tanımlayıcısı.
# Nokta/slash/".." içeremez → paket-kaçışı (modules.os gibi) ve yol-gezinmesi engellenir.
# Logger zaten _kimlik() ile bu biçimde üretir; bu, diskteki kayıt defteri kurcalansa bile
# rastgele kod import edilmesini önleyen savunma-derinliği katmanıdır.
_GECERLI_MODUL_ADI = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _modul_adi_guvenli_mi(ad: str) -> bool:
    """`ad` güvenli bir üretilmiş modül adı mı? Hem desen hem de gerçek dosyanın modules/
    altında bulunması doğrulanır (sembolik bağ/yol gezinmesi kaçışlarına karşı)."""
    if not ad or not _GECERLI_MODUL_ADI.match(ad):
        return False
    modules_dir = os.path.dirname(os.path.abspath(__file__))
    hedef = os.path.realpath(os.path.join(modules_dir, ad + ".py"))
    try:
        return (os.path.commonpath([os.path.realpath(modules_dir), hedef]) ==
                os.path.realpath(modules_dir)) and os.path.isfile(hedef)
    except (ValueError, OSError):
        return False


class UretilmisRunner:
    def __init__(self, parent, app, core_modul, baslik=None):
        self.parent = parent
        self.app = app
        self.core_modul = core_modul
        self.baslik = baslik or core_modul
        self._calisiyor = False
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad=self.baslik)
        self._entries = {}
        self._core = None
        self._hata = None
        if not _modul_adi_guvenli_mi(core_modul):
            # Güvenli olmayan/bilinmeyen ad → import ETME (rastgele kod çalıştırma yüzeyi).
            self._hata = ValueError(
                f"Güvenli olmayan ya da bulunamayan modül adı reddedildi: {core_modul!r}")
        else:
            try:
                self._core = importlib.import_module(f"modules.{core_modul}")
            except Exception as e:
                self._hata = e
        self._init_style()
        self._build()

    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Runner.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.configure("Runner.Treeview.Heading", background=C.SIDEBAR, foreground=C.INK,
                        relief="flat", font=self.app.f_nav_b, padding=6)
        style.map("Runner.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text=self.baslik, bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")

        if self._hata is not None or self._core is None:
            tk.Label(wrap, text=f"Modül yüklenemedi: {self.core_modul}\n\n{self._hata}",
                     bg=C.BG, fg=C.CLAY, font=self.app.f_body, justify="left",
                     wraplength=700).pack(anchor="w", pady=(16, 0))
            return

        tk.Label(wrap, text="Logger'ın ürettiği ağ-sorgu modülü. İstek 127.0.0.1:8800 ofis "
                            "proxy'sine gider (UYAP Bağlantısı açık olmalı). Girdileri "
                            "doldur, Çalıştır'a bas; yanıt aşağıda görünür.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub,
                 wraplength=820, justify="left").pack(anchor="w", pady=(6, 16))

        # ── girdi formu (core.PARAMETRELER'den) ──
        parametreler = list(getattr(self._core, "PARAMETRELER", []) or [])
        form = tk.Frame(wrap, bg=C.BG)
        form.pack(fill="x")
        for param, etiket, ornek in parametreler:
            satir = tk.Frame(form, bg=C.BG)
            satir.pack(fill="x", pady=3)
            tk.Label(satir, text=str(etiket) + ":", bg=C.BG, fg=C.INK_SOFT,
                     font=self.app.f_small, width=26, anchor="w").pack(side="left")
            ent = tk.Entry(satir, bg="#FFFFFF", fg=C.INK, relief="flat",
                           insertbackground=C.INK, font=self.app.f_body,
                           highlightthickness=1, highlightbackground=C.LINE,
                           highlightcolor=C.SAGE, width=46)
            ent.pack(side="left", ipady=4)
            if ornek is not None:
                ent.insert(0, str(ornek))
            self._entries[param] = ent
        if not parametreler:
            tk.Label(form, text="(Bu sorgu girdi almıyor — tüm alanlar sabit.)",
                     bg=C.BG, fg=C.INK_FAINT, font=self.app.f_small).pack(anchor="w")

        # ── (opsiyonel) Excel/dosya toplu girdisi (core.EXCEL_GIRDI varsa) ──
        # Modül EXCEL_GIRDI = {"etiket","fonksiyon","uzanti"} bildirirse burada bir
        # dosya seçici çıkar; dosya seçiliyse Çalıştır o fonksiyonu (örn. excel_isle)
        # seçilen yol ile çağırır. Bildirmeyen modüller bu bloğu hiç görmez.
        self._excel_girdi = getattr(self._core, "EXCEL_GIRDI", None)
        self._secili_dosya = None
        if self._excel_girdi:
            ex = tk.Frame(wrap, bg=C.BG)
            ex.pack(fill="x", pady=(12, 0))
            tk.Label(ex, text=str(self._excel_girdi.get("etiket", "Dosya")) + ":",
                     bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small,
                     width=26, anchor="w").pack(side="left")
            self.dosya_sec_btn = RoundButton(ex, "Dosya Seç…", command=self._dosya_sec,
                                             kind="ghost", font=self.app.f_nav_b, height=32)
            self.dosya_sec_btn.pack(side="left")
            self.dosya_lbl = tk.Label(ex, text="(dosya seçilmedi)", bg=C.BG,
                                      fg=C.INK_FAINT, font=self.app.f_small)
            self.dosya_lbl.pack(side="left", padx=(12, 0))

        # ── (opsiyonel) çıktı klasörü seçimi (core.KLASOR_GIRDI varsa) ──
        # Modül KLASOR_GIRDI = {"etiket"} bildirirse bir klasör seçici çıkar;
        # EXCEL_GIRDI fonksiyonu (dosya, klasor, log_fn=...) biçiminde çağrılır
        # (klasor seçilmemişse None geçer — modül kendi varsayılanını kullanır).
        self._klasor_girdi = getattr(self._core, "KLASOR_GIRDI", None)
        self._secili_klasor = None
        if self._klasor_girdi:
            kx = tk.Frame(wrap, bg=C.BG)
            kx.pack(fill="x", pady=(8, 0))
            tk.Label(kx, text=str(self._klasor_girdi.get("etiket", "Çıktı klasörü")) + ":",
                     bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small,
                     width=26, anchor="w").pack(side="left")
            self.klasor_sec_btn = RoundButton(kx, "Klasör Seç…", command=self._klasor_sec,
                                              kind="ghost", font=self.app.f_nav_b, height=32)
            self.klasor_sec_btn.pack(side="left")
            self.klasor_lbl = tk.Label(kx, text="(seçilmezse Excel yanında oluşturulur)",
                                       bg=C.BG, fg=C.INK_FAINT, font=self.app.f_small)
            self.klasor_lbl.pack(side="left", padx=(12, 0))

        # ── araç çubuğu ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self.calistir_btn = RoundButton(bar, "Çalıştır", command=self.calistir,
                                        kind="primary", font=self.app.f_nav_b, height=36)
        self.calistir_btn.pack(side="left", ipadx=6)
        # Duraklat/Durdur yalnız Excel/toplu modda (EXCEL_GIRDI) anlamlıdır —
        # tekil sorgu modunda gösterilmez.
        self.duraklat_btn = None
        self.durdur_btn = None
        if self._excel_girdi:
            self.duraklat_btn = RoundButton(bar, "Duraklat", command=self.duraklat_toggle,
                                            kind="ghost", font=self.app.f_nav_b, height=36)
            self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
            self.duraklat_btn.set_state("disabled")
            self.durdur_btn = RoundButton(bar, "Durdur", command=self.durdur,
                                          kind="ghost", font=self.app.f_nav_b, height=36)
            self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
            self.durdur_btn.set_state("disabled")
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── sonuç alanı (çalıştırınca tablo veya JSON doldurulur) ──
        self.sonuc_kutu = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                                   highlightthickness=1)
        self.sonuc_kutu.pack(fill="both", expand=True, pady=(12, 0))
        self._sonuc_bos("Henüz çalıştırılmadı.")

        # ── günlük ──
        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(12, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=5,
                           padx=12, pady=8, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

    # ─────────────────────────── yardımcılar ───────────────────────────
    def _log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", str(text) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _sonuc_temizle(self):
        for w in self.sonuc_kutu.winfo_children():
            w.destroy()

    def _sonuc_bos(self, mesaj):
        self._sonuc_temizle()
        tk.Label(self.sonuc_kutu, text=mesaj, bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_body).pack(padx=16, pady=16, anchor="w")

    # ─────────────────────────── dosya seçici ───────────────────────────
    def _dosya_sec(self):
        ft = self._excel_girdi.get("uzanti") or [("Excel", "*.xlsx"), ("Tümü", "*.*")]
        yol = filedialog.askopenfilename(title="Dosya seç", filetypes=ft)
        if yol:
            self._secili_dosya = yol
            self.dosya_lbl.config(text=os.path.basename(yol), fg=C.INK)

    def _klasor_sec(self):
        yol = filedialog.askdirectory(title="Çıktı klasörü seç")
        if yol:
            self._secili_klasor = yol
            self.klasor_lbl.config(text=yol, fg=C.INK)

    # ─────────────────────────── çalıştır ───────────────────────────
    def calistir(self):
        if self._calisiyor:
            return
        # Dosya seçiliyse Excel/toplu modu, değilse normal tek-girdi modu.
        excel_modu = bool(self._excel_girdi and self._secili_dosya)
        if not excel_modu:
            self._gercek_calistir(excel_modu=False)
            return
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._gercek_calistir(excel_modu=True),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._gercek_calistir(excel_modu=True)

    def _gercek_calistir(self, excel_modu):
        girdi = {p: e.get() for p, e in self._entries.items()}
        dosya = self._secili_dosya
        self._calisiyor = True
        self._son_excel_modu = excel_modu
        self.calistir_btn.set_state("disabled")
        self.durum_lbl.config(text="Çalışıyor…")
        if excel_modu:
            self.kontrol.sifirla()
            if self.duraklat_btn is not None:
                self.duraklat_btn.set_state("normal")
                self.duraklat_btn.set_text("Duraklat")
                self.durdur_btn.set_state("normal")

        def isi():
            try:
                if excel_modu:
                    fn_adi = self._excel_girdi.get("fonksiyon", "excel_isle")
                    fn = getattr(self._core, fn_adi)
                    ekstra = {}
                    if "kontrol" in inspect.signature(fn).parameters:
                        ekstra["kontrol"] = self.kontrol
                    if self._klasor_girdi:
                        sonuc = fn(dosya, self._secili_klasor, log_fn=self._log, **ekstra)
                    else:
                        sonuc = fn(dosya, log_fn=self._log, **ekstra)
                else:
                    sonuc = self._core.calistir(girdi, self._log)
                self.app.after(0, lambda: self._bitti(sonuc))
            except Exception as e:
                # except değişkeni blok bitince silinir; varsayılan argümanla bağla.
                self.app.after(0, lambda h=e: self._hata_goster(h))

        threading.Thread(target=isi, daemon=True).start()

    def duraklat_toggle(self):
        if not self._calisiyor:
            return
        paused = self.kontrol.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self._calisiyor:
            self.kontrol.durdur()

    def _calisma_bitti_ui(self):
        self._calisiyor = False
        self.calistir_btn.set_state("normal")
        if getattr(self, "_son_excel_modu", False) and self.duraklat_btn is not None:
            self.duraklat_btn.set_state("disabled")
            self.duraklat_btn.set_text("Duraklat")
            self.durdur_btn.set_state("disabled")
            toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    def _bitti(self, sonuc):
        self._calisma_bitti_ui()
        if isinstance(sonuc, dict) and sonuc.get("_hata"):
            self.durum_lbl.config(text="Hata")
            self._sonuc_bos(f"Hata: {sonuc.get('_hata')}")
            return
        self._goster_sonuc(sonuc)

    def _hata_goster(self, e):
        self._calisma_bitti_ui()
        self.durum_lbl.config(text="Hata")
        self._log(f"❌ {e}")
        messagebox.showerror("Hata", str(e))

    def _goster_sonuc(self, sonuc):
        """Liste-sözlük ise tablo; değilse JSON/metin gösterir."""
        self._sonuc_temizle()
        # Liste içinde sözlükler → tablo
        if isinstance(sonuc, list) and sonuc and all(isinstance(x, dict) for x in sonuc):
            kolonlar = []
            for satir in sonuc:
                for k in satir.keys():
                    if k not in kolonlar:
                        kolonlar.append(k)
            tree = ttk.Treeview(self.sonuc_kutu, columns=kolonlar, show="headings",
                                style="Runner.Treeview")
            for k in kolonlar:
                tree.heading(k, text=str(k))
                tree.column(k, width=160, anchor="w")
            for satir in sonuc:
                tree.insert("", "end", values=[satir.get(k, "") for k in kolonlar])
            ysb = tk.Scrollbar(self.sonuc_kutu, command=tree.yview)
            xsb = tk.Scrollbar(self.sonuc_kutu, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
            tree.grid(row=0, column=0, sticky="nsew")
            ysb.grid(row=0, column=1, sticky="ns")
            xsb.grid(row=1, column=0, sticky="ew")
            self.sonuc_kutu.rowconfigure(0, weight=1)
            self.sonuc_kutu.columnconfigure(0, weight=1)
            self.durum_lbl.config(text=f"{len(sonuc)} satır")
            return
        # Aksi halde: JSON / ham metin
        if isinstance(sonuc, (dict, list)):
            metin = json.dumps(sonuc, ensure_ascii=False, indent=2, default=str)
        else:
            metin = str(sonuc)
        txt = tk.Text(self.sonuc_kutu, bg=C.CARD, fg=C.INK, relief="flat",
                      font=self.app.f_mono, wrap="word", padx=12, pady=10,
                      highlightthickness=0)
        txt.insert("1.0", metin)
        txt.config(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(self.sonuc_kutu, command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.config(yscrollcommand=sb.set)
        self.durum_lbl.config(text="Yanıt alındı")
