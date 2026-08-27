# -*- coding: utf-8 -*-
"""
Panel/modules/normal_tebligat_gonder.py — 21/2 taramasında "farklı yöntem
gerekli (mernis'e çıkmamış)" çıkan dosyalar için NORMAL (21/2 şerhsiz)
tebligat GERÇEK GÖNDERİMİ (ücretli) için gömülü panel + is_kuyrugu istemcisi.
==============================================================================
teblig_21_2_gonder.py'nin BİREBİR aynı yapısı (kullanıcı isteği, 2026-08-17:
bu iki gönderim akışı ARTIK PARALEL yaşıyor, birbirine DOKUNMUYOR) — o modülün
kanıtlı desenini (ayrı pencere AÇMAZ, çağıranın verdiği container'a gömülür,
çakışma kontrolü, her dosya AYRI onay) yalnız farklı bir job_type
("normal_tebligat_gonder", bkz. job_handlers.py) ile tekrarlar.

DÜZELTME (kullanıcı bulgusu, 2026-08-17): İLK sürüm yanlışlıkla MTS'in tarafa-
tebligat-gönder ekranını kullanıyordu. Doğrusu: ofis tarafındaki gerçek iş
mantığı uyap_core.teblig_212.gonder.prepare/finalize — teblig_212_gonder'ın
KENDİSİ (avukatIcraTalepEvrakiGonder.ajx, "İcra/Ödeme Emrinin Tebliğe
Çıkartılması" talebi), yalnızca "tebligatTuru" alanı 2 ("T.K.21/2 Şerhli")
yerine 1 ("Normal Tebligat") — bkz. o modüldeki TEBLIGAT_TURU_KODLARI
(kullanıcının UYAP'tan CANLI paylaştığı referans liste). O modülün asıl
21/2 akışı (varsayılan parametrelerle) HİÇ ETKİLENMEDİ.

Her dosya kullanıcının AYRI AYRI onayından geçer (Onayla/Atla/İptal) — tek
butonla toplu/sessiz gönderim YOK; onaylanmayan hiçbir dosya için ücret
alınmaz.
"""

import threading
import tkinter as tk
from tkinter import messagebox

from theme import C, RoundButton
from . import is_kuyrugu

MASRAF_TL = 300  # yalnız GÖRÜNTÜ İÇİN (ön bilgilendirme, bkz. mts.takip._tebligat_gonder
                 # modül başlığı — 2026-08-11 canlı örnekte 317.20 TL) — gerçek tutarı
                 # iş her dosya için ayrı bildirir, burada yalnız kaba bir üst sınır.

_AKTIF_DURUMLAR = {"queued", "running", "awaiting_approval"}


def _bekleyen_cakisma_var_mi(kalemler):
    """kalemler'deki herhangi bir dosyaNo için ZATEN aktif (bitmemiş) bir
    normal_tebligat_gonder işi var mı? Varsa o dosyaNo'yu döner, yoksa None.
    teblig_21_2_gonder._bekleyen_cakisma_var_mi İLE AYNI desen, yalnız job
    türü farklı."""
    hedef = {k.get("dosyaNo") for k in kalemler}
    try:
        isler = is_kuyrugu.is_liste()
    except Exception:
        return None
    for is_ in isler:
        if is_.get("type") != "normal_tebligat_gonder" or is_.get("status") not in _AKTIF_DURUMLAR:
            continue
        for k in (is_.get("params") or {}).get("kalemler") or []:
            if k.get("dosyaNo") in hedef:
                return k.get("dosyaNo")
    return None


def gonderim_baslat(container, app, kalemler, on_bitti=None):
    """`container`: rapor penceresinde önceden hazırlanmış, boş bir tk.Frame.
    kalemler: [{"birim":..., "dosyaNo":..., "borclu":...}, ...] — kullanıcının
    rapor penceresinden elle seçtiği, kategori=="farklı yöntem gerekli
    (mernis'e çıkmamış)" (ya da "Yine de Gönder" ile bilerek seçilmiş başka)
    satırlar. `on_bitti(sonuclar)`: iş done/error/cancelled olduğunda ÇAĞRILIR
    — çağıran BUNUN İÇİNDE butonunu yeniden aktif etmeli.

    Döner: True (başlatıldı) / False (başlatılmadı — çağıran butonunu HEMEN
    yeniden aktif etmeli, on_bitti bu durumda ÇAĞRILMAZ)."""
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
            f"{n} dosya için NORMAL (21/2 şerhsiz) tebligat — icra/ödeme emrinin mernis "
            "adresine tebliğe çıkarılması — gönderilecek.\n\n"
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
        for w in list(self.root.winfo_children()):
            w.destroy()

        tk.Label(self.root, text=f"{len(kalemler)} dosya işlenecek — GERÇEK gönderim, "
                                 "GERÇEK ücret. Her dosya ayrı onay ister.",
                 bg=C.CARD, fg=C.CLAY, font=app.f_card_t, wraplength=1100, justify="left"
                 ).pack(anchor="w", padx=4, pady=(4, 6))

        self.durum_lbl = tk.Label(self.root, text="Başlatılıyor…", bg=C.CARD, fg=C.INK_SOFT,
                                  font=app.f_small)
        self.durum_lbl.pack(anchor="w", padx=4)

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

    def _baslat(self):
        def isi():
            try:
                job = is_kuyrugu.is_baslat("normal_tebligat_gonder", {"kalemler": self.kalemler})
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

    def _onay_goster(self, pending):
        self._onay_aktif = True
        ozet = (pending or {}).get("ozet") or {}
        self.onay_mesaj.config(text=(
            f"Dosya: {ozet.get('dosyaNo', '?')}   ({ozet.get('birim', '')})\n"
            f"Borçlu: {ozet.get('borclu', '?')}\n"
            f"Tebligat Türü: {ozet.get('tebligatTuru', 'Normal Tebligat')} — "
            "İcra/Ödeme Emrinin Tebliğe Çıkartılması, Adres Türü: Mernis Adresi\n\n"
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
