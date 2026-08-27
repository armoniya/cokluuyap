# -*- coding: utf-8 -*-
"""
İpotek Takip Açma — Gömülü Panel
=================================
uyap_core.ipotek (parse + takip.prepare/finalize) üzerinden TEK bir ipotek
takibi (İlamlı Takip → İpoteğin Paraya Çevrilmesi Yolu İle Takip) açar.
mts_takip.py ile aynı mimari (panelin canlı UYAP bağlantısı + is_kuyrugu iş
kuyruğu + onay akışı) ama tek dosyalık olduğu için basit: mersis/tckn/iban +
tapu/ipotek bilgileri ENTRY alanları, alacak kalemleri Excel'den.

Excel sütun eşlemesi (1. satır başlık, veri 2. satırdan):
    C = faiz oranı (%)          → yalnızca D'ye uygulanır
    D = asıl alacak tutarı      → faiz türü "Diğer", süre tipi "Yıllık"
    E = geçmiş gün faizi tutarı → faiz uygulanmaz
    G = BSMV tutarı             → faiz uygulanmaz
    H = masraf                  → KULLANILMIYOR (Excel akışında kullanıcı kararıyla atlandı)

ALTERNATİF kaynak — "📄 XML'den Veri Çek" butonu: UYAP "exchangeData" XML'i
(İLAMLI/İpotek biçimi, <ilam> düğümlü) verilirse dosya/taraf/alacak kalemi
alanlarını OTOMATİK doldurur (bkz. uyap_core.ipotek.xml_parse). Excel'in aksine
XML'deki HER alacak kalemi (asıl alacak/geçmiş gün faizi/BSMV/diğer faiz
alacağı/masraf) AYRI AYRI, birleştirilmeden aktarılır — masraf ve diğer faiz
alacağı kodları CANLI UYAP referansından (icraTakipAlacakGirisBilgileri.ajx)
isim eşleşmesiyle bulunur, uydurulmaz (2026-08-12, kullanıcı kararı).

ÖNEMLİ: prepare() harç hesabına kadar çalışır, TEVZİ NUMARASI ALMAZ (dosya
açmaz). Onay ekranında alacak kalemi tutarları TEK TEK gösterilir — yalnızca
"Onayla" derseniz tevzi + e-imza + evrak gönderme (finalize) devam eder.
Bu, iş sahibinin (av. Utku Alpaslan) ondalık ayraç (nokta/virgül) karışıklığı
riskine karşı istediği son-kontrol adımıdır. XML'den otomatik doldurulan
alanlar da bu son-kontrolden MUAF DEĞİLDİR.

Eklenti: mağazadan 'potek_takip_acma' etkin değilse menüde görünmez.
"""

import os
import sys
import json
import threading
import urllib.request
import urllib.error

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from theme import C, RoundButton
from . import is_kuyrugu
from . import takip_sonuc_raporu

_OFFICE_BASE = "http://127.0.0.1:8800"


def _tl_bicimle(tutar):
    """1234.5 -> '1.234,50' (TR biçimi) — yalnızca GÜNLÜK/log görüntüleme içindir."""
    return f"{tutar:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _uyap_core_ekle():
    """`Uyap Haricen Giriş` klasörünü sys.path'e ekler (uyap_core orada)."""
    here = os.path.dirname(os.path.abspath(__file__))
    kok = os.path.dirname(os.path.dirname(here))
    uhg = os.path.join(kok, "Uyap Haricen Giriş")
    if uhg not in sys.path:
        sys.path.insert(0, uhg)
    return uhg


def _yerel_yetki_jetonu_opener_kur():
    """Bağımsız süreçler ofisin salt-okunur UYAP .ajx uç noktalarına (is_kuyrugu'nun
    kullandığı /__uyap_agent__/jobs kontrol düzleminden FARKLI olarak) erişmek için
    X-Uyap-Local-Token gerektirir; bkz. barkod_sorgu.py — aynı desen."""
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


def _referans_getir(endpoint, payload=None):
    """Salt-okunur bir UYAP referans listesi (ör. icra_takip_yolu.ajx) çeker.
    Canlı UYAP oturumu gerektirir; hata durumunda ValueError fırlatır."""
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(_OFFICE_BASE + "/" + endpoint, data=body, method="POST")
    for k, v in {"accept": "application/json, text/plain, */*",
                "content-type": "application/json", "cache-control": "no-cache"}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"{endpoint}: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise ValueError(f"Ofise ulaşılamadı: {getattr(e, 'reason', e)}")


class IpotekTakipPanel:
    BASLIK = "İpotek Takip Açma"

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app

        self.excel_yolu = None
        self.xml_yolu = None
        self.kalemler = []          # excel_kalemlerini_oku / xml_parse çıktısı (aynı satır şekli)
        self.job_id = None
        self._calisiyor = False
        self._log_sayac = 0
        self._onay_aktif = False
        self._son_sonuc = {}
        self._giris = {}            # {alan_adi: tk.StringVar | tk.Text}

        # Takip türü/yolu/şekli/ilam türü — canlı UYAP'tan çekilen (ad, değer) listeleri
        self._turu_list = []
        self._yolu_list = []
        self._sekli_list = []
        self._ilam_list = []
        # XML'den okunan takip_turu/takip_yolu/takip_sekli/ilam_turu HAM değerleri
        # (varsa) — combobox'lar canlı yüklenince isim eşleşmesi yerine bu değerle
        # seçilir (bkz. _combobox_doldur hedef_deger).
        self._xml_deger = {}

        self._build()
        self._baglanti_kontrol()
        self._takip_secimlerini_yukle()

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
        tk.Label(ic, text="İlamlı Takip → İpoteğin Paraya Çevrilmesi Yolu İle Takip — "
                          "panelin UYAP bağlantısı üzerinden (tarayıcısız).",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 0))
        self.baglanti_lbl = tk.Label(ic, text="● Bağlantı kontrol ediliyor…", bg=C.BG,
                                     fg=C.INK_FAINT, font=self.app.f_small)
        self.baglanti_lbl.pack(anchor="w", pady=(4, 0))
        tk.Frame(ic, bg=C.LINE, height=1).pack(fill="x", pady=(16, 0))

        self._giris_kur(ic)
        self._takip_secim_kur(ic)
        self._evrak_bar_kur(ic)
        self._excel_bar_kur(ic)
        self._onay_bar_kur(ic)
        self._eylem_bar_kur(ic)
        self._log_kur(ic)

        self._butonlar_guncelle()

    def _kart(self, parent):
        return tk.Frame(parent, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)

    def _alan(self, parent, etiket, anahtar, genislik=30, satir=1):
        row = tk.Frame(parent, bg=C.CARD)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=etiket, bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=24, anchor="w").pack(side="left", anchor="n" if satir > 1 else "w")
        if satir > 1:
            txt = tk.Text(row, height=satir, bg="#FFFFFF", fg=C.INK, relief="flat",
                         font=self.app.f_body, wrap="word", highlightthickness=1,
                         highlightbackground=C.LINE, highlightcolor=C.SAGE)
            txt.pack(side="left", fill="x", expand=True, ipady=2)
            self._giris[anahtar] = txt
        else:
            var = tk.StringVar()
            e = tk.Entry(row, textvariable=var, width=genislik, bg="#FFFFFF", fg=C.INK,
                        relief="flat", font=self.app.f_body, highlightthickness=1,
                        highlightbackground=C.LINE, highlightcolor=C.SAGE)
            e.pack(side="left", fill="x", expand=True, ipady=3)
            self._giris[anahtar] = var
        return row

    def _giris_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="DOSYA BİLGİLERİ", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")

        xml_bar = tk.Frame(ic, bg=C.CARD)
        xml_bar.pack(fill="x", pady=(6, 0))
        self.xml_btn = RoundButton(xml_bar, "📄  XML'den Veri Çek", command=self._xml_sec,
                                   kind="primary", font=self.app.f_nav_b, height=32)
        self.xml_btn.pack(side="left")
        self.xml_lbl = tk.Label(xml_bar, text="Seçilmedi.", bg=C.CARD, fg=C.INK_SOFT,
                                font=self.app.f_body)
        self.xml_lbl.pack(side="left", padx=10)
        tk.Label(ic, text="UYAP 'exchangeData' XML'i (İLAMLI/İpotek biçimi) — aşağıdaki alanları "
                          "VE alacak kalemlerini otomatik doldurur. Otomatik doldurulan HER ALANI "
                          "göndermeden önce MUTLAKA gözden geçirin; İl/Adliye, evraklar ve İpotek/"
                          "Rehin Açıklaması (XML'de genelde boş gelir) elle girilmeli.",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small, wraplength=760,
                 justify="left").pack(anchor="w", pady=(2, 8))
        tk.Frame(ic, bg=C.LINE, height=1).pack(fill="x", pady=(2, 10))

        self._alan(ic, "Alacaklı Mersis No", "mersis_no")
        self._alan(ic, "Alacaklı IBAN", "iban")
        self._alan(ic, "Borçlu TCKN(ler)", "tckn_list", satir=2)
        tk.Label(ic, text="(birden fazla borçlu varsa virgülle veya alt alta yazın)",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small).pack(anchor="w", padx=(0, 0))
        self._alan(ic, "İl", "il")
        self._giris["il"].set("İzmir")
        self._alan(ic, "Adliye", "adliye")
        self._giris["adliye"].set("İzmir")

        tk.Frame(ic, bg=C.LINE, height=1).pack(fill="x", pady=(10, 6))
        tk.Label(ic, text="TAPU / İPOTEK BİLGİLERİ (Excel'den gelmez, elle girilir)",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_nav_b).pack(anchor="w")
        self._alan(ic, "Tapu Müdürlüğü Adı", "tapu_muduru_adi")
        self._alan(ic, "İlam/Tescil Tarihi", "ilam_tarihi")
        self._alan(ic, "Yevmiye Yılı", "yevmiye_yil", genislik=10)
        self._alan(ic, "Yevmiye Sırası", "yevmiye_sira", genislik=10)
        self._alan(ic, "İpotek/Rehin Açıklaması", "ipotek_rehin_aciklama", satir=3)

        tk.Frame(ic, bg=C.LINE, height=1).pack(fill="x", pady=(10, 6))
        tk.Label(ic, text="TAKİP TALEBİ FORMU AÇIKLAMALARI (UYAP form maddeleri)",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_nav_b).pack(anchor="w")
        self._alan(ic, "1/9 Açıklaması (Takip Yolu)", "aciklama_48_9")
        self._giris["aciklama_48_9"].set("İpoteğin Paraya Çevrilmesi")
        self._alan(ic, "1/5 Açıklaması (Üçüncü Şahıs Rehni — varsa)", "aciklama_48_5", satir=3)
        tk.Label(ic, text="(Rehnedilen taşınmaz üçüncü bir şahıs tarafından verilmiş/mülkiyeti "
                          "üçüncü şahsa geçmişse doldurun; yoksa boş bırakın.)",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small).pack(anchor="w")

        row = tk.Frame(ic, bg=C.CARD)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text="1/4 Açıklaması (Alacak/Faiz)", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small, width=24, anchor="w").pack(side="left", anchor="n")
        self._giris["aciklama_48_4"] = tk.Text(
            row, height=6, bg="#FFFFFF", fg=C.INK, relief="flat", font=self.app.f_body,
            wrap="word", highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        self._giris["aciklama_48_4"].pack(side="left", fill="x", expand=True, ipady=2)
        RoundButton(ic, "🔄  Excel'den Taslak Oluştur", command=self._aciklama_48_4_taslak,
                   kind="ghost", font=self.app.f_small, height=28).pack(anchor="e", pady=(4, 0))
        tk.Label(ic, text="(Excel'deki asıl alacak tutarlarını faiz oranına göre gruplayıp taslak "
                          "metin üretir — göndermeden önce MUTLAKA okuyup gerekirse düzenleyin.)",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small, wraplength=760,
                 justify="left").pack(anchor="w")

    def _aciklama_48_4_taslak(self):
        if not self.kalemler:
            messagebox.showinfo("Excel eksik", "Önce alacak kalemleri Excel dosyasını seçin.")
            return
        try:
            _uyap_core_ekle()
            from uyap_core.ipotek import taslak_aciklama_48_4
            taslak = taslak_aciklama_48_4(self.kalemler)
        except Exception as e:
            messagebox.showerror("Taslak oluşturulamadı", str(e))
            return
        w = self._giris["aciklama_48_4"]
        w.delete("1.0", "end")
        w.insert("1.0", taslak)

    def _takip_secim_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="TAKİP TÜRÜ / YOLU / ŞEKLİ / İLAM TÜRÜ (canlı UYAP'tan)",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_nav_b).pack(anchor="w")
        tk.Label(ic, text="Varsayılan İpotek kombinasyonu önceden seçili gelir; farklı bir "
                          "seçim yaparsanız aşağıdaki tapu/dosya alanları yine de İpotek "
                          "takibine özgü kalır — UYAP'ın reddetme ihtimaline dikkat edin.",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small, wraplength=760,
                 justify="left").pack(anchor="w", pady=(2, 8))

        kota_row = tk.Frame(ic, bg=C.CARD)
        kota_row.pack(fill="x", pady=(0, 8))
        tk.Label(kota_row, text="Kota Tipi", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                 width=16, anchor="w").pack(side="left")
        tk.Label(kota_row, text="Gayrimenkul Dosyası  (İpotek takibi için sabit — Avukat/Kurum/"
                                "Banka/Takip seçenekleri bu modülde desteklenmiyor)",
                 bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_body).pack(side="left")

        self._secim_durum = tk.Label(ic, text="Yükleniyor…", bg=C.CARD, fg=C.INK_FAINT,
                                     font=self.app.f_small)
        self._secim_durum.pack(anchor="w", pady=(0, 6))

        def _satir(etiket):
            row = tk.Frame(ic, bg=C.CARD)
            row.pack(fill="x", pady=(4, 0))
            tk.Label(row, text=etiket, bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                     width=16, anchor="w").pack(side="left")
            cb = ttk.Combobox(row, state="readonly", width=55)
            cb.pack(side="left", fill="x", expand=True)
            return cb

        self.turu_cb = _satir("Takip Türü")
        self.yolu_cb = _satir("Takip Yolu")
        self.sekli_cb = _satir("Takip Şekli")
        self.ilam_cb = _satir("İlam Türü")

        self.turu_cb.bind("<<ComboboxSelected>>", lambda e: self._yolu_yukle())
        self.yolu_cb.bind("<<ComboboxSelected>>", lambda e: self._sekli_yukle())

    def _secili_deger(self, cb, liste):
        idx = cb.current()
        if idx < 0 or idx >= len(liste):
            return None
        return liste[idx].get("value")

    def _combobox_doldur(self, cb, liste, hedef_alt_metin=None, hedef_deger=None):
        """hedef_deger (XML'den gelen HAM takip_turu/takip_yolu/takip_sekli/ilam_turu
        kodu) varsa ve listede eşleşen bir 'value' bulunursa ONA öncelik verilir —
        bulunamazsa hedef_alt_metin (isim alt-metni) ile eski davranışa düşülür."""
        cb["values"] = [it.get("name", "") for it in liste]
        secim_idx = 0
        bulundu = False
        if hedef_deger is not None and liste:
            for i, it in enumerate(liste):
                if it.get("value") == hedef_deger:
                    secim_idx = i
                    bulundu = True
                    break
        if not bulundu and hedef_alt_metin and liste:
            hedef = hedef_alt_metin.upper()
            for i, it in enumerate(liste):
                if hedef in (it.get("name") or "").upper():
                    secim_idx = i
                    break
        if liste:
            cb.current(secim_idx)

    def _takip_secimlerini_yukle(self):
        def isi():
            try:
                turu_list = _referans_getir("icra_takip_turu.ajx", {})
                ilam_list = _referans_getir("icra_takip_ilam_turleri.ajx", {})
                self.app.after(0, lambda: self._turu_yuklendi(turu_list, ilam_list))
            except Exception as e:
                self.app.after(0, lambda e=e: self._secim_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _turu_yuklendi(self, turu_list, ilam_list):
        self._turu_list = turu_list if isinstance(turu_list, list) else []
        self._ilam_list = ilam_list if isinstance(ilam_list, list) else []
        self._combobox_doldur(self.turu_cb, self._turu_list, "İlamlı",
                              hedef_deger=self._xml_deger.get("takip_turu"))
        self._combobox_doldur(self.ilam_cb, self._ilam_list, "Diğer",
                              hedef_deger=self._xml_deger.get("ilam_turu"))
        self._yolu_yukle()

    def _yolu_yukle(self):
        takip_turu = self._secili_deger(self.turu_cb, self._turu_list)
        if takip_turu is None:
            return
        self._secim_durum.config(text="Takip yolu yükleniyor…")

        def isi():
            try:
                yolu_list = _referans_getir("icra_takip_yolu.ajx", {"takipTuru": takip_turu})
                self.app.after(0, lambda: self._yolu_yuklendi(yolu_list))
            except Exception as e:
                self.app.after(0, lambda e=e: self._secim_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _yolu_yuklendi(self, yolu_list):
        self._yolu_list = yolu_list if isinstance(yolu_list, list) else []
        self._combobox_doldur(self.yolu_cb, self._yolu_list, "Para ve Teminat Verilmesi Hakkındaki",
                              hedef_deger=self._xml_deger.get("takip_yolu"))
        self._sekli_yukle()

    def _sekli_yukle(self):
        takip_turu = self._secili_deger(self.turu_cb, self._turu_list)
        takip_yolu = self._secili_deger(self.yolu_cb, self._yolu_list)
        if takip_turu is None or takip_yolu is None:
            return
        self._secim_durum.config(text="Takip şekli yükleniyor…")

        def isi():
            try:
                sekli_list = _referans_getir(
                    "icra_takip_sekli.ajx", {"takipTuru": takip_turu, "takipYolu": takip_yolu})
                self.app.after(0, lambda: self._sekli_yuklendi(sekli_list))
            except Exception as e:
                self.app.after(0, lambda e=e: self._secim_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _sekli_yuklendi(self, sekli_list):
        self._sekli_list = sekli_list if isinstance(sekli_list, list) else []
        self._combobox_doldur(self.sekli_cb, self._sekli_list, "İpoteğin Paraya Çevrilmesi",
                              hedef_deger=self._xml_deger.get("takip_sekli"))
        self._secim_durum.config(text="✓ Seçenekler yüklendi (UYAP'tan canlı).")

    def _secim_hata(self, e):
        self._secim_durum.config(text=f"⚠️ Seçenekler yüklenemedi: {e}")

    def _evrak_bar_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="EVRAKLAR", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")
        tk.Label(ic, text="Vekaletname ZORUNLU. Takibin dayanağı (tapu senedi/ipotek tescil "
                          "belgesi) en az 1 dosya ZORUNLU — birden fazla seçilebilir.",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small, wraplength=760,
                 justify="left").pack(anchor="w", pady=(2, 8))

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
        self.dayanak_btn = RoundButton(row2, "📎  Dayanak Belge(ler) Seç", command=self._dayanak_sec,
                                       kind="primary", font=self.app.f_nav_b, height=32)
        self.dayanak_btn.pack(side="left")
        self.dayanak_lbl = tk.Label(row2, text="Seçilmedi.", bg=C.CARD, fg=C.CLAY,
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
            title="Takibin dayanağı belge(ler)ini seçin",
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
        import base64
        with open(yol, "rb") as f:
            return {"filename": os.path.basename(yol), "b64": base64.b64encode(f.read()).decode("ascii")}

    def _excel_bar_kur(self, parent):
        kart = self._kart(parent)
        kart.pack(fill="x", pady=(14, 0))
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="x", padx=18, pady=14)
        tk.Label(ic, text="ALACAK KALEMLERİ (Excel)", bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_nav_b).pack(anchor="w")
        tk.Label(ic, text="Sütunlar: C=faiz oranı, D=asıl alacak, E=geçmiş gün faizi, G=BSMV. "
                          "(H=masraf kullanılmıyor.) 1. satır başlık, veri 2. satırdan başlar.",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small, wraplength=760,
                 justify="left").pack(anchor="w", pady=(2, 8))
        bar = tk.Frame(ic, bg=C.CARD)
        bar.pack(fill="x")
        self.excel_btn = RoundButton(bar, "📂  Excel Seç", command=self._excel_sec,
                                     kind="primary", font=self.app.f_nav_b, height=34)
        self.excel_btn.pack(side="left")
        self.excel_lbl = tk.Label(bar, text="Henüz dosya seçilmedi.", bg=C.CARD, fg=C.INK_SOFT,
                                  font=self.app.f_body)
        self.excel_lbl.pack(side="left", padx=12)

        self.kalem_tv = ttk.Treeview(
            ic, columns=("satir", "oran", "asil", "faiz", "bsmv", "diger_faiz", "masraf"),
            show="headings", height=6)
        for s, b, g in (("satir", "Satır", 50), ("oran", "Faiz Oranı %", 90),
                        ("asil", "Asıl Alacak", 100), ("faiz", "Geçmiş Gün Faizi", 120),
                        ("bsmv", "BSMV", 90), ("diger_faiz", "Diğer Faiz Alacağı", 130),
                        ("masraf", "Masraf", 90)):
            self.kalem_tv.heading(s, text=b)
            self.kalem_tv.column(s, width=g, anchor="e" if s != "satir" else "center")
        self.kalem_tv.pack(fill="x", pady=(10, 0))

    def _onay_bar_kur(self, parent):
        self.onay_bar = tk.Frame(parent, bg=C.SAGE_TINT, highlightbackground=C.SAGE,
                                 highlightthickness=1)
        ic = tk.Frame(self.onay_bar, bg=C.SAGE_TINT)
        ic.pack(fill="x", padx=14, pady=10)
        self.onay_mesaj = tk.Label(ic, text="", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                                   font=self.app.f_body, justify="left", anchor="w", wraplength=780)
        self.onay_mesaj.pack(fill="x")
        self.onay_kalem_tv = ttk.Treeview(ic, columns=("ad", "tutar", "oran"),
                                         show="headings", height=8)
        for s, b, g in (("ad", "Kalem", 170), ("tutar", "Tutar (TL)", 140),
                        ("oran", "Faiz Oranı %", 110)):
            self.onay_kalem_tv.heading(s, text=b)
            self.onay_kalem_tv.column(s, width=g, anchor="e" if s != "ad" else "w")
        self.onay_kalem_tv.pack(fill="x", pady=(8, 8))
        btn_cer = tk.Frame(ic, bg=C.SAGE_TINT)
        btn_cer.pack(anchor="w")
        RoundButton(btn_cer, "✓ Kontrol Ettim, Onayla (Tevzi + İmza + Gönder)",
                   command=lambda: self._onay_ver({"decision": "approve"}),
                   kind="primary", font=self.app.f_nav_b, height=34).pack(side="left")
        RoundButton(btn_cer, "✕ İptal (Dosya AÇILMAZ)",
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
        RoundButton(bas, "📊  Sonucu Excel'e Aktar", command=self._sonuclari_excel_aktar,
                   kind="ghost", font=self.app.f_small, height=28).pack(side="right", padx=(0, 8))
        cer = tk.Frame(kart, bg="#FBFAF7")
        cer.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.log = tk.Text(cer, bg="#FBFAF7", fg=C.INK, relief="flat", font=self.app.f_mono,
                           wrap="word", height=10, padx=4, pady=2, state="disabled",
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
        sonuc = self._son_sonuc or {}
        satirlar = [sonuc] if sonuc.get("durum") == "tamam" else []
        kolonlar = [
            ("Durum", "durum"),
            ("Esas No", "gercek_dosya_no"),
            ("Dosya ID", "dosya_id"),
        ]
        takip_sonuc_raporu.sonuclari_excel_yaz(
            self._log_yaz, satirlar, kolonlar,
            "ipotek_takip_sonucu.xlsx", sheet_title="İpotek Takip Sonucu")

    def _durum_yaz(self, metin):
        if self.durum_lbl.winfo_exists():
            self.durum_lbl.config(text=metin)

    def _metin_al(self, anahtar):
        w = self._giris.get(anahtar)
        if isinstance(w, tk.StringVar):
            return w.get().strip()
        if isinstance(w, tk.Text):
            return w.get("1.0", "end").strip()
        return ""

    def _metin_yaz(self, anahtar, deger):
        w = self._giris.get(anahtar)
        if isinstance(w, tk.StringVar):
            w.set(deger or "")
        elif isinstance(w, tk.Text):
            w.delete("1.0", "end")
            w.insert("1.0", deger or "")

    def _butonlar_guncelle(self):
        self.baslat_btn.set_state("disabled" if self._calisiyor else "normal")
        self.durdur_btn.set_state("normal" if self._calisiyor else "disabled")

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

    # ─────────────────────────────────────────────────────── excel seç
    def _excel_sec(self):
        if self._calisiyor:
            return
        yol = filedialog.askopenfilename(
            title="Alacak kalemleri Excel dosyasını seçin",
            filetypes=[("Excel", "*.xlsx *.xls"), ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.excel_lbl.config(text="Okunuyor…")
        self.excel_btn.set_state("disabled")

        def isi():
            try:
                _uyap_core_ekle()
                from uyap_core.ipotek.parse import excel_kalemlerini_oku
                kalemler = excel_kalemlerini_oku(yol)
                self.app.after(0, lambda: self._excel_yuklendi(yol, kalemler))
            except Exception as e:
                self.app.after(0, lambda e=e: self._excel_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _excel_yuklendi(self, yol, kalemler):
        self.excel_btn.set_state("normal")
        if not kalemler:
            self.excel_lbl.config(text="Excel'de okunacak alacak kalemi bulunamadı.")
            return
        self.excel_yolu = yol
        self.xml_yolu = None
        self._xml_deger = {}
        self.kalemler = kalemler
        self.excel_lbl.config(text=f"{os.path.basename(yol)} · {len(kalemler)} satır")
        self._log_yaz(f"✓ {len(kalemler)} satır okundu ({os.path.basename(yol)}).")
        self._kalem_tv_doldur(kalemler)

    def _excel_hata(self, e):
        self.excel_btn.set_state("normal")
        self.excel_lbl.config(text="Okuma hatası.")
        self._log_yaz(f"❌ Excel okuma hatası: {e}")
        messagebox.showerror("Excel okunamadı", str(e))

    def _kalem_tv_doldur(self, kalemler):
        """Excel VE XML akışı ORTAK kullanır — kalemler her ikisinde de AYNI 7
        anahtarlı satır şeklinde (satir/faiz_orani/asil_alacak/gecmis_gun_faizi/
        bsmv/diger_faiz_alacagi/masraf)."""
        self.kalem_tv.delete(*self.kalem_tv.get_children())
        for k in kalemler:
            self.kalem_tv.insert("", "end", values=(
                k["satir"], f"{k['faiz_orani']:.2f}", f"{k['asil_alacak']:.2f}",
                f"{k['gecmis_gun_faizi']:.2f}", f"{k['bsmv']:.2f}",
                f"{k.get('diger_faiz_alacagi', 0.0):.2f}", f"{k.get('masraf', 0.0):.2f}"))

    # ─────────────────────────────────────────────────────── xml seç
    def _xml_sec(self):
        if self._calisiyor:
            return
        yol = filedialog.askopenfilename(
            title="UYAP takip XML dosyasını seçin (İLAMLI/İpotek biçimi)",
            filetypes=[("XML", "*.xml"), ("Tüm dosyalar", "*.*")])
        if not yol:
            return
        self.xml_lbl.config(text="Okunuyor…", fg=C.INK_SOFT)
        self.xml_btn.set_state("disabled")

        def isi():
            try:
                _uyap_core_ekle()
                from uyap_core.ipotek.xml_parse import xml_dosyasindan_oku
                dosyalar = xml_dosyasindan_oku(yol)
                self.app.after(0, lambda: self._xml_yuklendi(yol, dosyalar))
            except Exception as e:
                self.app.after(0, lambda e=e: self._xml_hata(e))
        threading.Thread(target=isi, daemon=True).start()

    def _xml_yuklendi(self, yol, dosyalar):
        self.xml_btn.set_state("normal")
        if not dosyalar:
            self.xml_lbl.config(text="XML'de okunacak <dosya> bulunamadı.", fg=C.CLAY)
            return
        if len(dosyalar) > 1:
            self._log_yaz(f"⚠️ XML'de {len(dosyalar)} <dosya> var — yalnızca İLKİ kullanıldı, "
                         "diğerleri YOK SAYILDI.")
        d = dosyalar[0]

        self.xml_yolu = yol
        self.excel_yolu = None
        self.xml_lbl.config(text=f"{os.path.basename(yol)} · {d.dosya_belirleyicisi}", fg=C.SAGE_DK)

        self._metin_yaz("mersis_no", d.alacakli.mersis_no)
        self._metin_yaz("iban", d.alacakli.iban)
        self._metin_yaz("tckn_list", "\n".join(b.tckn for b in d.borclular))
        self._metin_yaz("tapu_muduru_adi", d.tapu_muduru_adi)
        self._metin_yaz("ilam_tarihi", d.ilam_tarihi)
        self._metin_yaz("yevmiye_yil", d.yevmiye_yil)
        self._metin_yaz("yevmiye_sira", d.yevmiye_sira)
        if d.ipotek_rehin_aciklama:
            self._metin_yaz("ipotek_rehin_aciklama", d.ipotek_rehin_aciklama)
        if d.aciklama_48_9:
            self._metin_yaz("aciklama_48_9", d.aciklama_48_9)

        self.kalemler = d.kalemler
        self.excel_lbl.config(text=f"XML'den alındı: {os.path.basename(yol)} · "
                                   f"{len(d.kalemler)} kalem", fg=C.SAGE_DK)
        self._kalem_tv_doldur(d.kalemler)

        # takip_turu/yolu/sekli/ilam_turu comboboxları — canlı liste zaten yüklendiyse
        # HEMEN yeniden seç (hedef_deger ile); henüz yükleniyorsa, o yükleme bitince
        # zaten güncel self._xml_deger'i okuyacak.
        self._xml_deger = {"takip_turu": d.takip_turu, "takip_yolu": d.takip_yolu,
                           "takip_sekli": d.takip_sekli, "ilam_turu": d.ilam_turu_kodu}
        if self._turu_list:
            self._turu_yuklendi(self._turu_list, self._ilam_list)

        try:
            _uyap_core_ekle()
            from uyap_core.ipotek import taslak_aciklama_48_4
            taslak = taslak_aciklama_48_4(d.kalemler)
            w = self._giris["aciklama_48_4"]
            w.delete("1.0", "end")
            w.insert("1.0", taslak)
        except Exception as e:
            self._log_yaz(f"⚠️ 1/4 açıklaması taslağı otomatik üretilemedi: {e}")

        self._log_yaz(f"\n=== XML'DEN VERİ ÇEKİLDİ ({os.path.basename(yol)}) ===")
        self._log_yaz(f"Alacaklı: {d.alacakli.ad} (Mersis: {d.alacakli.mersis_no}, "
                     f"IBAN: {d.alacakli.iban})")
        for b in d.borclular:
            self._log_yaz(f"Borçlu: {b.ad} (TCKN: {b.tckn})")
        ad_map = [("asil_alacak", "Asıl Alacak"), ("gecmis_gun_faizi", "Geçmiş Gün Faizi"),
                 ("bsmv", "BSMV"), ("diger_faiz_alacagi", "Diğer Faiz Alacağı"),
                 ("masraf", "Masraf")]
        for tur, ad in ad_map:
            toplam = sum(k.get(tur, 0.0) for k in d.kalemler)
            if toplam > 0:
                self._log_yaz(f"  {ad}: {_tl_bicimle(toplam)} TL")
        self._log_yaz(f"Toplam (XML'in kendi beyanı): {_tl_bicimle(d.xml_toplam_tutar)} TL — "
                     f"panele aktarılan: {_tl_bicimle(d.kalemler_toplam_tutar)} TL")
        if abs(d.xml_toplam_tutar - d.kalemler_toplam_tutar) > 0.01:
            self._log_yaz("⚠️ XML toplamıyla panele aktarılan toplam FARKLI — MUTLAKA kontrol edin.")
        for uyari in d.uyarilar:
            self._log_yaz(f"⚠️ {uyari}")
        self._log_yaz("⚠️ Otomatik doldurulan TÜM alanları ve alacak kalemi tablosunu göndermeden "
                     "önce MUTLAKA gözden geçirin — İl/Adliye, Vekaletname ve Dayanak Belge(ler)i "
                     "hâlâ elle seçilmeli; 1/4 açıklaması taslaktır, düzenlenebilir. 'Diğer Faiz "
                     "Alacağı'/'Masraf' kodları 'Hazırla' adımında CANLI UYAP referansından "
                     "bulunacak — bulunamazsa o adımda net bir hata alırsınız.")

        if d.uyarilar:
            messagebox.showwarning(
                "XML'den veri çekildi — dikkat",
                "Bazı alanlar otomatik doldurulamadı ya da kontrol gerektiriyor:\n\n" +
                "\n".join(f"• {u}" for u in d.uyarilar) +
                "\n\nGünlükteki (log) özeti de MUTLAKA okuyun.")
        else:
            messagebox.showinfo(
                "XML'den veri çekildi",
                f"{len(d.kalemler)} alacak kalemi ve dosya bilgileri dolduruldu. Göndermeden önce "
                "TÜM alanları (özellikle İl/Adliye ve 1/4 açıklaması) gözden geçirin.")

    def _xml_hata(self, e):
        self.xml_btn.set_state("normal")
        self.xml_lbl.config(text="Okuma hatası.", fg=C.CLAY)
        self._log_yaz(f"❌ XML okuma hatası: {e}")
        messagebox.showerror("XML okunamadı", str(e))

    # ─────────────────────────────────────────────────────── başlat / durdur
    def _baslat(self):
        if self._calisiyor:
            return
        if not self.kalemler:
            messagebox.showwarning("Excel eksik", "Önce alacak kalemleri Excel dosyasını seçin.")
            return
        mersis_no = self._metin_al("mersis_no")
        iban = self._metin_al("iban")
        tckn_metin = self._metin_al("tckn_list")
        tckn_list = [t.strip() for t in tckn_metin.replace(",", "\n").splitlines() if t.strip()]
        tapu_muduru_adi = self._metin_al("tapu_muduru_adi")
        ilam_tarihi = self._metin_al("ilam_tarihi")
        ipotek_rehin_aciklama = self._metin_al("ipotek_rehin_aciklama")
        aciklama_48_4 = self._metin_al("aciklama_48_4")
        aciklama_48_9 = self._metin_al("aciklama_48_9")
        aciklama_48_5 = self._metin_al("aciklama_48_5")
        if not mersis_no or not iban or not tckn_list:
            messagebox.showwarning("Eksik bilgi", "Mersis No, IBAN ve en az bir Borçlu TCKN girin.")
            return
        if not tapu_muduru_adi or not ilam_tarihi or not ipotek_rehin_aciklama:
            messagebox.showwarning(
                "Eksik bilgi", "Tapu Müdürlüğü Adı, İlam/Tescil Tarihi ve İpotek/Rehin "
                              "Açıklaması alanlarını doldurun (UYAP bunları boş kabul etmiyor).")
            return
        if not aciklama_48_4:
            messagebox.showwarning(
                "Eksik bilgi", "1/4 Açıklaması boş — 'Excel'den Taslak Oluştur' ile taslak "
                              "üretip gözden geçirin, sonra devam edin.")
            return
        if not self.vekalet_yolu:
            messagebox.showwarning("Eksik evrak", "Vekaletname yüklenmedi (UYAP zorunlu istiyor).")
            return
        if not self.dayanak_yollari:
            messagebox.showwarning(
                "Eksik evrak", "Takibin dayanağı belgesi (tapu senedi/ipotek belgesi) yüklenmedi.")
            return

        takip_turu = self._secili_deger(self.turu_cb, self._turu_list)
        takip_yolu = self._secili_deger(self.yolu_cb, self._yolu_list)
        takip_sekli = self._secili_deger(self.sekli_cb, self._sekli_list)
        ilam_turu = self._secili_deger(self.ilam_cb, self._ilam_list)
        if None in (takip_turu, takip_yolu, takip_sekli, ilam_turu):
            messagebox.showwarning(
                "Seçenekler yüklenmedi", "Takip Türü/Yolu/Şekli/İlam Türü seçenekleri henüz "
                                        "UYAP'tan yüklenmedi — biraz bekleyip tekrar deneyin.")
            return

        self._calisiyor = True
        self._log_sayac = 0
        self._durum_yaz("Evraklar okunuyor, iş gönderiliyor…")
        self._log_yaz("\n=== İPOTEK TAKİP AÇMA ===")
        self._butonlar_guncelle()

        def isi():
            try:
                params = {
                    "mersis_no": mersis_no, "iban": iban, "tckn_list": tckn_list,
                    "il": self._metin_al("il") or "İzmir", "adliye": self._metin_al("adliye") or "İzmir",
                    "takip_turu": takip_turu, "takip_yolu": takip_yolu,
                    "takip_sekli": takip_sekli, "ilam_turu": ilam_turu,
                    "tapu_muduru_adi": tapu_muduru_adi,
                    "ilam_tarihi": ilam_tarihi,
                    "yevmiye_yil": self._metin_al("yevmiye_yil"),
                    "yevmiye_sira": self._metin_al("yevmiye_sira"),
                    "ipotek_rehin_aciklama": ipotek_rehin_aciklama,
                    "aciklama_48_4": aciklama_48_4, "aciklama_48_9": aciklama_48_9,
                    "aciklama_48_5": aciklama_48_5,
                    "kalemler": self.kalemler,
                    "vekalet": self._dosya_b64(self.vekalet_yolu),
                    "dayanak_listesi": [self._dosya_b64(y) for y in self.dayanak_yollari],
                }
                job = is_kuyrugu.is_baslat("ipotek_takip_ac", params)
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
            if sonuc.get("durum") == "tamam":
                esas_no = sonuc.get("gercek_dosya_no")
                if esas_no:
                    self._durum_yaz(f"Tamamlandı. Esas No: {esas_no}")
                    self._log_yaz(f"✓ Dosya açıldı. Esas No: {esas_no}")
                else:
                    self._durum_yaz(f"Tamamlandı (esas no henüz yok). Dosya ID: {sonuc.get('dosya_id')}")
                    self._log_yaz(f"✓ Dosya açıldı, henüz esas no atanmadı (harç ödemesi UYAP'tan "
                                  f"elle yapılmalı). Dosya ID: {sonuc.get('dosya_id')}")
            else:
                self._durum_yaz("Onaylanmadı — dosya açılmadı.")
                self._log_yaz("İşlem iptal edildi, dosya açılmadı.")
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
        ozet = pending.get("ozet") or {}
        borc = ", ".join(f"{b.get('ad','')} {b.get('soyad','')}".strip() or (b.get('tckn') or "?")
                         for b in (ozet.get("borclular") or [])) or "-"
        harc = "; ".join(f"{h.get('ad')}: {h.get('miktar')} TL" for h in (ozet.get("harclar") or []))
        self.onay_mesaj.config(text=(
            f"Alacaklı: {ozet.get('alacakli','')}  (Mersis: {ozet.get('mersis_no','')})\n"
            f"Borçlu: {borc}\n"
            f"IBAN: {ozet.get('iban','')}\n"
            f"Toplam Alacak: {ozet.get('toplam_alacak','-')} TL   ·   Harç/Masraf: {harc}\n\n"
            "AŞAĞIDAKİ TUTARLARI DİKKATLİCE KONTROL EDİN (ondalık ayraç hatası olabilir):"))
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
