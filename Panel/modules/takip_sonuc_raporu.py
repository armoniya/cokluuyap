# -*- coding: utf-8 -*-
"""
Takip Sonuç Raporu — ortak Excel dışa aktarma yardımcısı
==========================================================
XML/İpotek/MTS toplu takip açma panellerinin (ve ileride Tüm Dosyalarım/İcra
Dosyalarım/Barkod Sorgulama'nın) iş sonucu satırlarını (job.result.sonuclar)
TEK TİP bir .xlsx dosyasına yazan paylaşılan fonksiyon — kullanıcı isteği
(2026-08-14): her modülde ayrı ayrı Excel kodu tekrarlanmasın, tek yerden
bakım yapılabilsin. MTS'in mevcut ayrı (UYAP'ı yeniden sorgulayan) 'Excel'e
Aktar' butonuna DOKUNULMAZ — bu yalnız BİR İŞİN kendi sonucunu (zaten
biliniyorsa gerçek esas no dahil) doğrudan dışa aktarmak içindir.
"""
from tkinter import filedialog, messagebox


def sonuclari_excel_yaz(log_fn, sonuclar, kolonlar, varsayilan_isim, sheet_title="Sonuçlar"):
    """sonuclar: [{...}, ...] — job.result.sonuclar (ya da tek dosyalık bir
    işte [tek_sonuc_dict]).
    kolonlar: [(baslik, anahtar_veya_fn), ...] — anahtar_veya_fn bir str ise
    dict.get(anahtar) ile, çağrılabilirse fn(satir) ile hücre değeri üretilir.
    log_fn: panelin _log_yaz metodu (sonuç panelin log kutusuna da yazılsın).

    Döner: kaydedilen dosya yolu, ya da kullanıcı iptal ettiyse/hata olduysa None."""
    if not sonuclar:
        messagebox.showinfo("Excel'e Aktar", "Aktarılacak sonuç yok.")
        return None

    yol = filedialog.asksaveasfilename(
        title="Excel'i kaydet", defaultextension=".xlsx",
        filetypes=[("Excel", "*.xlsx")], initialfile=varsayilan_isim)
    if not yol:
        return None

    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_title
        ws.append([baslik for baslik, _ in kolonlar])
        for satir in sonuclar:
            degerler = []
            for _, anahtar in kolonlar:
                deger = anahtar(satir) if callable(anahtar) else satir.get(anahtar)
                degerler.append(deger if deger is not None else "")
            ws.append(degerler)
        for i in range(len(kolonlar)):
            harf = openpyxl.utils.get_column_letter(i + 1)
            ws.column_dimensions[harf].width = 26
        wb.save(yol)
    except Exception as e:
        log_fn(f"❌ Excel yazılamadı: {e}")
        messagebox.showerror("Excel'e Aktar", str(e))
        return None

    log_fn(f"✓ Excel kaydedildi ({len(sonuclar)} satır): {yol}")
    messagebox.showinfo("Excel'e Aktar", f"{len(sonuclar)} kayıt yazıldı:\n{yol}")
    return yol
