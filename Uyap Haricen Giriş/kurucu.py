# -*- coding: utf-8 -*-
"""
Çoklu UYAP Kurucu (CokluUyapKur.exe) — tek dosyalık kurulum programı
====================================================================
Müşteri sitesinden TEK dosya indirir, çift tıklar; başka hiçbir şey yapmaz:

  1. Gömülü uygulama paketini (dist/CokluUyap → payload.zip) %LOCALAPPDATA%\\CokluUyap
     altına açar (~140 MB; İcra/MTS araçları ve gömülü PostgreSQL BU PAKETTE YOKTUR —
     kalıcı disk yükü asla GB'lara çıkmaz).
  2. Masaüstüne "Çoklu UYAP" kısayolu koyar.
  3. Uygulamayı başlatır → tek ekranlık giriş (ofis kodu + kullanıcı + parola + PIN)
     paylaşımı kendiliğinden kurar; Başlangıç kısayolunu uygulama kendisi yönetir.

WinRAR/zip/elle kısayol yok. Yeniden çalıştırmak = güncelleme (üstüne yazar).

Derleme (cokluuyap_app.spec ÇIKTISINDAN sonra):
  .\\.venv\\Scripts\\python.exe -m PyInstaller cokluuyap_kur.spec --noconfirm

Sınama anahtarları (kurulumun kendisini bozmadan):
  CokluUyapKur.exe --hedef D:\\deneme --sessiz --baslatma
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

APP_ADI = "Çoklu UYAP"
EXE_ADI = "CokluUyap.exe"
KLASOR = "CokluUyap"


def payload_yolu():
    """Gömülü paket: PyInstaller onefile açılımında (sys._MEIPASS) payload.zip."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "payload.zip")


def varsayilan_hedef():
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, KLASOR)


def kisayol(lnk_yolu, hedef_exe):
    ps = ("$w = New-Object -ComObject WScript.Shell; "
          "$s = $w.CreateShortcut('%s'); "
          "$s.TargetPath = '%s'; $s.WorkingDirectory = '%s'; $s.Save()") % (
        lnk_yolu.replace("'", "''"),
        hedef_exe.replace("'", "''"),
        os.path.dirname(hedef_exe).replace("'", "''"))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                   timeout=30, check=True)


def kur(hedef, durum=lambda s: None, kisayollar=True):
    zpath = payload_yolu()
    if not os.path.exists(zpath):
        raise RuntimeError("Gömülü paket (payload.zip) bulunamadı — kurucu bozuk derlenmiş.")

    durum("Dosyalar açılıyor…")
    # Önce yan klasöre aç, sonra değiştir: yarım kurulum bırakma.
    yeni = hedef + ".yeni"
    if os.path.exists(yeni):
        shutil.rmtree(yeni, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(yeni)

    durum("Eski sürüm değiştiriliyor…")
    if os.path.exists(hedef):
        # Çalışan örnek varsa exe kilitli olabilir → önce nazikçe kapat.
        subprocess.run(["taskkill", "/IM", EXE_ADI, "/F"],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       capture_output=True)
        shutil.rmtree(hedef, ignore_errors=True)
    os.replace(yeni, hedef)

    exe = os.path.join(hedef, EXE_ADI)
    if not os.path.exists(exe):
        # payload kök klasör içeriyorsa (CokluUyap/CokluUyap.exe) bir seviye içeri bak
        ic = os.path.join(hedef, KLASOR, EXE_ADI)
        if os.path.exists(ic):
            exe = ic
        else:
            raise RuntimeError(f"{EXE_ADI} paket içinde bulunamadı.")

    if kisayollar:
        durum("Kısayollar oluşturuluyor…")
        masaustu = os.path.join(os.path.expanduser("~"), "Desktop")
        try:
            kisayol(os.path.join(masaustu, APP_ADI + ".lnk"), exe)
        except Exception:
            pass  # OneDrive-yönlendirmeli masaüstü vb.; kurulum yine geçerli
        baslat_menu = os.path.join(os.environ.get("APPDATA", ""),
                                   "Microsoft", "Windows", "Start Menu", "Programs")
        try:
            kisayol(os.path.join(baslat_menu, APP_ADI + ".lnk"), exe)
        except Exception:
            pass

    return exe


def _gui(hedef, baslat):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title(APP_ADI + " Kurulumu")
    root.geometry("420x170")
    root.resizable(False, False)
    root.configure(bg="#f6f7f4")

    tk.Label(root, text=APP_ADI + " kuruluyor…", bg="#f6f7f4", fg="#3b4a3f",
             font=("Segoe UI", 13, "bold")).pack(pady=(22, 6))
    durum_lbl = tk.Label(root, text="Hazırlanıyor…", bg="#f6f7f4", fg="#5c6b60",
                         font=("Segoe UI", 10))
    durum_lbl.pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=340)
    bar.pack(pady=14)
    bar.start(12)

    sonuc = {}

    def calis():
        try:
            sonuc["exe"] = kur(hedef, durum=lambda s: root.after(
                0, lambda: durum_lbl.configure(text=s)))
        except Exception as e:
            sonuc["hata"] = str(e)
        root.after(0, bitti)

    def bitti():
        bar.stop()
        if "hata" in sonuc:
            messagebox.showerror(APP_ADI, "Kurulum başarısız:\n" + sonuc["hata"])
            root.destroy()
            sys.exit(1)
        durum_lbl.configure(text="Kurulum tamamlandı.")
        root.destroy()
        if baslat:
            subprocess.Popen([sonuc["exe"]], cwd=os.path.dirname(sonuc["exe"]),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    import threading
    threading.Thread(target=calis, daemon=True).start()
    root.mainloop()


def main():
    p = argparse.ArgumentParser(description=APP_ADI + " kurucu")
    p.add_argument("--hedef", default=varsayilan_hedef(),
                   help="Kurulum klasörü (varsayılan: %%LOCALAPPDATA%%\\CokluUyap)")
    p.add_argument("--sessiz", action="store_true", help="Pencere göstermeden kur")
    p.add_argument("--baslatma", action="store_true", help="Kurulum sonrası uygulamayı açma")
    p.add_argument("--kisayolsuz", action="store_true", help="Masaüstü/Başlat kısayolu kurma")
    args = p.parse_args()

    if args.sessiz:
        exe = kur(args.hedef, durum=lambda s: print(s), kisayollar=not args.kisayolsuz)
        print("Kuruldu: " + exe)
        if not args.baslatma:
            subprocess.Popen([exe], cwd=os.path.dirname(exe),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        _gui(args.hedef, baslat=not args.baslatma)


if __name__ == "__main__":
    main()
