# -*- coding: utf-8 -*-
"""
Panel/modules/teblig_21_2_gonder.py — T.K.21/2 şerhli yeniden tebliğ talebi
GERÇEK GÖNDERİMİ (ücretli) için gömülü panel + is_kuyrugu istemcisi.
==============================================================================
`teblig_21_2_core.py`'ye (tarama-only, kasıtlı kapsam kilidi) BİLEREK
dokunmaz — gönderim tamamen burada, ayrı bir modülde yaşar. Ofis tarafındaki
gerçek iş mantığı: `Uyap Haricen Giriş/uyap_core/teblig_212/gonder.py`
(job_type "teblig_212_gonder", bkz. job_handlers.py) — `potek_takip.py`'nin
kullandığı AYNI is_kuyrugu prepare→onay→finalize mimarisi.

Kullanıcı bulgusu (2026-08-14): bu panel eskiden AYRI bir Toplevel açıyordu;
o pencere rapor penceresinin ARKASINDA kalıp "kayboluyor" gibi görünüyordu —
kullanıcı "hiçbir şey olmadı" sanıp butona TEKRAR TEKRAR bastı, bu da AYNI
dosya için 6 ayrı gönderim işinin sırada birikmesine yol açtı (hiçbiri
onaylanmadığı için ücret harcanmadı, ama karışıklık büyüktü). Artık:
  1. Bu panel AYRI pencere AÇMAZ — çağıranın verdiği mevcut container'a
     (rapor penceresinin İÇİNE) gömülür.
  2. Yeni iş başlatmadan ÖNCE is_kuyrugu.is_liste() ile aynı dosya için
     ZATEN bekleyen/çalışan bir "teblig_212_gonder" işi olup olmadığı
     kontrol edilir — varsa yeni iş AÇILMAZ, kullanıcı uyarılır.
  3. Çağıran (barkod_sorgu_panel.py), "Gönder" butonunu iş başladığı anda
     KALICI OLARAK devre dışı bırakır (bkz. orada _gonder_tetikle) — aynı
     pencereden ikinci bir tıklama iş BAŞLATAMAZ.

Her dosya kullanıcının AYRI AYRI onayından geçer (Onayla/Atla/İptal) — tek
butonla toplu/sessiz gönderim YOK; onaylanmayan hiçbir dosya için ücret
alınmaz (bkz. uyap_core.teblig_212.gonder — ödeme yalnız finalize() içinde,
yalnız onaydan SONRA gerçekleşir).
"""

import threading
import tkinter as tk
from tkinter import messagebox

from theme import C, RoundButton
from . import is_kuyrugu

MASRAF_TL = 265  # yalnız GÖRÜNTÜ İÇİN (ön bilgilendirme) — gerçek tutarı iş her dosya için ayrı bildirir

# Bir işin bu türden "hâlâ meşgul" sayılan durumları — dedup kontrolünde kullanılır.
_AKTIF_DURUMLAR = {"queued", "running", "awaiting_approval"}


def _bekleyen_cakisma_var_mi(kalemler):
    """kalemler'deki herhangi bir dosyaNo için ZATEN aktif (bitmemiş) bir
    teblig_212_gonder işi var mı? Varsa o dosyaNo'yu döner, yoksa None.
    Ağ/ofis hatasında (kontrol edilemedi) SESSİZCE None döner — bu kontrol
    bir KOLAYLIK katmanıdır, olmazsa olmaz güvenlik sınırı değildir (asıl
    güvenlik: her iş kendi TAZE "zaten gönderilmiş mi" kontrolünü ve tek
    tek onayını yapar, bkz. uyap_core.teblig_212.gonder.prepare)."""
    hedef = {k.get("dosyaNo") for k in kalemler}
    try:
        isler = is_kuyrugu.is_liste()
    except Exception:
        return None
    for is_ in isler:
        if is_.get("type") != "teblig_212_gonder" or is_.get("status") not in _AKTIF_DURUMLAR:
            continue
        for k in (is_.get("params") or {}).get("kalemler") or []:
            if k.get("dosyaNo") in hedef:
                return k.get("dosyaNo")
    return None


def gonderim_baslat(container, app, kalemler, on_bitti=None):
    """`container`: rapor penceresinde önceden hazırlanmış, boş bir tk.Frame
    (çağıran bunu gösterip gizleyebilir — bkz. barkod_sorgu_panel.py). Ayrı
    bir pencere AÇMAZ. kalemler: [{"birim":..., "dosyaNo":..., "borclu":...},
    ...] — YALNIZ kullanıcının rapor penceresinden elle seçtiği, kategori==
    "21/2 için uygun" satırlar. `on_bitti(sonuclar)`: iş done/error/cancelled
    olduğunda ÇAĞRILIR (sonuclar boş olabilir) — çağıran BUNUN İÇİNDE
    butonunu yeniden aktif etmeli (kullanıcı bulgusu, 2026-08-14: "bir kere
    gönderince başka dosya seçip tekrar gönderemiyorum" — buton KALICI
    değil, yalnız bu iş bitene kadar kilitli kalmalı) ve gönderilen dosyalar
    için yerel DB'yi/ekranı tazelemeli (bkz. teblig_21_2_core.
    dosyalari_gonderildi_isaretle).

    Döner: True (başlatıldı) / False (başlatılmadı, çakışma ya da kullanıcı
    vazgeçti — çağıran butonunu HEMEN yeniden aktif etmeli, on_bitti bu
    durumda ÇAĞRILMAZ)."""
    if not kalemler:
        return False

    cakisan = _bekleyen_cakisma_var_mi(kalemler)
    if cakisan:
        messagebox.showwarning(
            "Zaten bekleyen bir gönderim var",
            f"Dosya {cakisan} için ZATEN sırada/onay bekleyen bir gönderim işi var. "
            "Aynı dosya için ikinci bir işlem başlatmak çift gönderim/ödeme riski "
            "doğurur — önce mevcut işi tamamlayın ya da iptal edin.")
        return False

    n = len(kalemler)
    if not messagebox.askyesno(
            "Gerçek Gönderim — Ücretli",
            f"{n} dosya için T.K.21/2 şerhli yeniden tebliğ talebi gönderilecek.\n\n"
            f"Olası en yüksek toplam ücret: ~{n * MASRAF_TL} TL — yalnız GERÇEKTEN "
            "gönderilenler için düşülür; zaten gönderilmiş olanlar veya sizin "
            "atladıklarınız için hiçbir ücret alınmaz.\n\n"
            "Her dosya, gönderilmeden HEMEN ÖNCE size ayrı ayrı gösterilip onayınız "
            "istenecek. Onaylamadığınız hiçbir dosya gönderilmez/ödenmez.\n\n"
            "Bu pencere kapanana kadar BEKLEYİN — tekrar tıklamanıza gerek yok, "
            "yalnızca BİR kez gönderim başlatılır.\n\n"
            "Devam edilsin mi?"):
        return False

    _GonderimPaneli(container, app, kalemler, on_bitti=on_bitti)
    return True


class _GonderimPaneli:
    def __init__(self, container, app, kalemler, on_bitti=None):
        self.app = app
        self.kalemler = kalemler
        self.on_bitti = on_bitti
        self.job_id = None
        self._log_sayac = 0
        self._onay_aktif = False
        self._calisiyor = True

        self.root = container
        self.root.configure(bg=C.CARD)
        # Önceki gönderimden kalan widget'lar varsa (aynı container İKİNCİ
        # kez kullanılıyor — kullanıcı başka dosyalar seçip tekrar gönderdi)
        # temizle; her seferinde SIFIRDAN kurulur.
        for w in list(self.root.winfo_children()):
            w.destroy()

        tk.Label(self.root, text=f"{len(kalemler)} dosya işlenecek — GERÇEK gönderim, "
                                 "GERÇEK ücret. Her dosya ayrı onay ister.",
                 bg=C.CARD, fg=C.CLAY, font=app.f_card_t, wraplength=1100, justify="left"
                 ).pack(anchor="w", padx=4, pady=(4, 6))

        self.durum_lbl = tk.Label(self.root, text="Başlatılıyor…", bg=C.CARD, fg=C.INK_SOFT,
                                  font=app.f_small)
        self.durum_lbl.pack(anchor="w", padx=4)

        # ── onay çubuğu (yalnız awaiting_approval'da görünür) ──
        self.onay_bar = tk.Frame(self.root, bg=C.SAGE_TINT, highlightbackground=C.SAGE,
                                 highlightthickness=1)
        ic = tk.Frame(self.onay_bar, bg=C.SAGE_TINT)
        ic.pack(fill="x", padx=14, pady=10)
        self.onay_mesaj = tk.Label(ic, text="", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                                   font=app.f_body, justify="left", anchor="w", wraplength=1060)
        self.onay_mesaj.pack(fill="x")
        btn_cer = tk.Frame(ic, bg=C.SAGE_TINT)
        btn_cer.pack(anchor="w", pady=(8, 0))
        self._btn(btn_cer, "✓ Onayla (Gönder + Öde)",
                  lambda: self._onay_ver({"decision": "approve"}), "primary").pack(side="left")
        self._btn(btn_cer, "Bu Dosyayı Atla",
                  lambda: self._onay_ver({"decision": "skip"}), "ghost").pack(side="left", padx=(8, 0))
        self._btn(btn_cer, "Kalanları İptal Et",
                  lambda: self._onay_ver({"decision": "cancel"}), "ghost").pack(side="left", padx=(8, 0))
        # başlangıçta gizli — yalnız _onay_goster ile pack edilir.

        # ── günlük ──
        lbox = tk.Frame(self.root, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="both", expand=True, padx=0, pady=(8, 4))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat", font=app.f_mono,
                           wrap="word", padx=12, pady=6, state="disabled", highlightthickness=0,
                           height=8)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

        self._baslat()

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind, font=self.app.f_nav_b, height=32)

    def _log_yaz(self, satir):
        self.log.config(state="normal")
        self.log.insert("end", str(satir) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _canli_mi(self):
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    # ─────────────────────────────────────────────────────── başlat / poll
    def _baslat(self):
        def isi():
            try:
                job = is_kuyrugu.is_baslat("teblig_212_gonder", {"kalemler": self.kalemler})
                self.app.after(0, lambda: self._is_basladi(job))
            except Exception as e:
                self.app.after(0, lambda e=e: self._is_baslatilamadi(e))
        threading.Thread(target=isi, daemon=True).start()

    def _is_baslatilamadi(self, e):
        self._calisiyor = False
        self.durum_lbl.config(text="Başlatılamadı.")
        self._log_yaz(f"❌ İş başlatılamadı: {e}")

    def _is_basladi(self, job):
        self.job_id = job.get("id")
        self.durum_lbl.config(text="Çalışıyor…")
        self._log_yaz(f"İş kuyruğuna alındı (id={self.job_id}).")
        self._poll()

    def _poll(self):
        if not self.job_id or not self._canli_mi():
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
        if self._calisiyor and self._canli_mi():
            self.app.after(2000, self._poll)

    def _poll_isle(self, job):
        loglar = job.get("logs") or []
        for ln in loglar[self._log_sayac:]:
            self._log_yaz(ln.get("line", ""))
        self._log_sayac = len(loglar)

        prog = job.get("progress") or {}
        if prog.get("message"):
            self.durum_lbl.config(text=prog["message"])

        status = job.get("status")
        if status == "awaiting_approval" and not self._onay_aktif:
            self._onay_goster(job.get("pending_approval") or {})

        if status in ("done", "error", "cancelled"):
            self._is_bitti(job)
            return
        if self._calisiyor and self._canli_mi():
            self.app.after(900, self._poll)

    # ─────────────────────────────────────────────────────── onay
    def _onay_goster(self, pending):
        self._onay_aktif = True
        ozet = (pending or {}).get("ozet") or {}
        self.onay_mesaj.config(text=(
            f"Dosya: {ozet.get('dosyaNo', '?')}   ({ozet.get('birim', '')})\n"
            f"Borçlu: {ozet.get('borclu', '?')}\n"
            f"Tebligat Türü: {ozet.get('tebligatTuru', '')}\n\n"
            f"⚠️ ONAYLARSANIZ {ozet.get('masraf', MASRAF_TL)} TL ÜCRET UYAP BAKİYENİZDEN "
            "DÜŞÜLECEK ve resmi bir talep gönderilecek. Bu işlem GERİ ALINAMAZ."))
        self.onay_bar.pack(fill="x", pady=(0, 8), before=self.log.master)

    def _onay_ver(self, karar):
        self._onay_aktif = False
        self.onay_bar.pack_forget()
        jid = self.job_id
        if not jid:
            return
        self.durum_lbl.config(text="Karar gönderiliyor…")

        def isi():
            try:
                is_kuyrugu.is_onayla(jid, karar)
            except Exception as e:
                self.app.after(0, lambda e=e: self._log_yaz(f"⚠️ Onay gönderilemedi: {e}"))
        threading.Thread(target=isi, daemon=True).start()

    # ─────────────────────────────────────────────────────── bitiş
    def _is_bitti(self, job):
        self._calisiyor = False
        status = job.get("status")
        sonuc = job.get("result") or {}
        if status == "done":
            basari = sonuc.get("basari", 0)
            toplam = sonuc.get("toplam", len(self.kalemler))
            self.durum_lbl.config(text=f"Tamamlandı: {basari}/{toplam} gönderildi.")
            self._log_yaz(f"✅ Bitti: {basari}/{toplam} gönderildi.")
        elif status == "cancelled":
            self.durum_lbl.config(text="İptal edildi.")
            self._log_yaz("⏹ Kalan dosyalar iptal edildi (gönderilmeyenler için ücret alınmadı).")
        else:
            self.durum_lbl.config(text="Hata.")
            self._log_yaz(f"❌ İş hatası: {job.get('error') or 'bilinmeyen'}")

        if self.on_bitti:
            try:
                self.on_bitti(sonuc.get("sonuclar") or [])
            except Exception as e:
                self._log_yaz(f"⚠️ Ekran/DB tazeleme hatası: {e}")
