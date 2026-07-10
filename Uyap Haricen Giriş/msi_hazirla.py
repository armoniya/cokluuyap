# -*- coding: utf-8 -*-
"""dist/CokluUyap içeriğinden gerçek Windows Installer paketi (CokluUyapKur.msi) üretir.
=======================================================================================
Neden MSI? İmzasız tek-exe kurucuyu Chrome/SmartScreen "şüpheli" diye engelliyordu;
MSI standart kurulum biçimidir, ayrıca Program Ekle/Kaldır'da görünür ve düzgün
güncellenir/kaldırılır (bkz. ux md/masaustu_kurulum_ve_arayuz_sorunlari.md, madde 1).

Sıra:
  1) .venv\\Scripts\\python.exe -m PyInstaller cokluuyap_app.spec --noconfirm  → dist/CokluUyap
  2) .venv\\Scripts\\python.exe msi_hazirla.py                                → dist/CokluUyapKur.msi

Özellikler:
  • Kullanıcı-başına kurulum (%LOCALAPPDATA%\\CokluUyap) → UAC/yönetici izni İSTEMEZ.
  • Masaüstü + Başlat menüsü kısayolu; kurulum bitince uygulama kendiliğinden açılır.
  • Aynı MSI'yı tekrar çalıştırmak = güncelleme (MajorUpgrade; sürüm YY.M.G tarihten türetilir).
  • Çalışan CokluUyap.exe kurulumdan önce nazikçe kapatılır (util:CloseApplication).
  • WiX 3.11 ikilileri araclar/wix311 altında; yoksa GitHub'dan bir kez indirilir.

Kod imzalama sertifikası alınırsa signtool adımı bu dosyanın sonuna eklenecek.
"""
import datetime
import os
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dist", "CokluUyap")
BUILD = os.path.join(HERE, "build")
MSI = os.path.join(HERE, "dist", "CokluUyapKur.msi")
WIX = os.path.join(HERE, "araclar", "wix311")
WIX_URL = ("https://github.com/wixtoolset/wix3/releases/download/"
           "wix3112rtm/wix311-binaries.zip")

APP_ADI = "Çoklu UYAP"
EXE_ADI = "CokluUyap.exe"
# SABİT kalmalı: Windows bu GUID ile "aynı ürün" olduğunu anlar ve üstüne günceller.
UPGRADE_CODE = "{B6E1B3A4-6C3F-4D2A-9C1E-7A5D0C4E8F21}"


def surum():
    """MSI ProductVersion: YY.A.G (ör. 26.7.8). Alanlar 255.255.65535 sınırında kalır;
    her günkü derleme bir öncekinin 'yükseltmesi' sayılır (AllowSameVersionUpgrades
    aynı gün yeniden derlemeyi de örter)."""
    t = datetime.date.today()
    return f"{t.year % 100}.{t.month}.{t.day}"


def wix_hazirla():
    """WiX 3.11 ikililerini garanti eder (yoksa indirir; .NET Framework 3.5 gerekir)."""
    if os.path.exists(os.path.join(WIX, "candle.exe")):
        return
    os.makedirs(WIX, exist_ok=True)
    zpath = os.path.join(WIX, "wix311-binaries.zip")
    print("WiX araçları indiriliyor (bir kereye mahsus, ~33 MB)…")
    urllib.request.urlretrieve(WIX_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(WIX)
    os.remove(zpath)


def calistir(*komut):
    r = subprocess.run(list(komut), capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write((r.stdout or "") + (r.stderr or ""))
        raise SystemExit(f"Komut başarısız ({os.path.basename(komut[0])}, kod {r.returncode})")
    return r.stdout


URUN_WXS = """<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi"
     xmlns:util="http://schemas.microsoft.com/wix/UtilExtension">
  <Product Id="*" Name="%(ad)s" Language="1055" Codepage="1254" Version="%(surum)s"
           Manufacturer="%(ad)s" UpgradeCode="%(upgrade)s">
    <!-- perUser + limited: %%LOCALAPPDATA%% altına kurar, UAC penceresi ÇIKMAZ. -->
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perUser"
             InstallPrivileges="limited" Description="%(ad)s Kurulumu" />
    <MajorUpgrade AllowSameVersionUpgrades="yes"
                  DowngradeErrorMessage="Daha yeni bir sürüm zaten kurulu." />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />
    <Property Id="MSIFASTINSTALL" Value="7" />
    <Property Id="ARPNOMODIFY" Value="1" />

    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="LocalAppDataFolder">
        <Directory Id="INSTALLDIR" Name="CokluUyap" />
      </Directory>
      <Directory Id="DesktopFolder" />
      <Directory Id="ProgramMenuFolder" />
    </Directory>

    <!-- Kısayollar: perUser kurulumda keypath HKCU kayıt değeri olmak zorunda. -->
    <DirectoryRef Id="DesktopFolder">
      <Component Id="MasaustuKisayol" Guid="{2F8A1D07-93B4-4E5C-A1D2-0B6C3E7F9A44}">
        <Shortcut Id="MasaustuLnk" Name="%(ad)s" Target="[INSTALLDIR]%(exe)s"
                  WorkingDirectory="INSTALLDIR" />
        <RegistryValue Root="HKCU" Key="Software\\CokluUyap" Name="masaustu_kisayol"
                       Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </DirectoryRef>
    <DirectoryRef Id="ProgramMenuFolder">
      <Component Id="BaslatKisayol" Guid="{5C0D2E19-74A6-4B8F-B3E4-1D7F5A2C8B66}">
        <Shortcut Id="BaslatLnk" Name="%(ad)s" Target="[INSTALLDIR]%(exe)s"
                  WorkingDirectory="INSTALLDIR" />
        <RegistryValue Root="HKCU" Key="Software\\CokluUyap" Name="baslat_kisayol"
                       Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </DirectoryRef>

    <!-- Güncellemede çalışan örneği nazikçe kapat (eski kurucudaki taskkill'in dengi). -->
    <util:CloseApplication Id="CalisaniKapat" Target="%(exe)s" CloseMessage="yes"
                           RebootPrompt="no" />

    <!-- Kurulum bitince uygulamayı başlat (eski kurucu davranışı korunur).
         WixShellExecTarget çalışma anında biçimlenir; CNDL1077 uyarısı kasıtlı/zararsız. -->
    <Property Id="WixShellExecTarget" Value="[INSTALLDIR]%(exe)s" />
    <CustomAction Id="UygulamayiBaslat" BinaryKey="WixCA" DllEntry="WixShellExec"
                  Impersonate="yes" Return="ignore" />
    <InstallExecuteSequence>
      <Custom Action="UygulamayiBaslat" After="InstallFinalize">NOT Installed OR REINSTALL OR UPGRADINGPRODUCTCODE</Custom>
    </InstallExecuteSequence>

    <Feature Id="Ana" Title="%(ad)s" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="MasaustuKisayol" />
      <ComponentRef Id="BaslatKisayol" />
    </Feature>
  </Product>
</Wix>
"""


def main():
    if not os.path.isdir(SRC):
        raise SystemExit("dist/CokluUyap yok — önce cokluuyap_app.spec derleyin.")
    if not os.path.exists(os.path.join(SRC, EXE_ADI)):
        raise SystemExit(f"dist/CokluUyap içinde {EXE_ADI} yok — derleme eksik.")
    wix_hazirla()
    os.makedirs(BUILD, exist_ok=True)
    v = surum()

    # 1) Dosya ağacını hasat et (heat): INSTALLDIR köküne, otomatik-kararlı GUID'lerle.
    dosyalar_wxs = os.path.join(BUILD, "msi_dosyalar.wxs")
    print(f"[1/3] Dosyalar taranıyor ({SRC}) …")
    calistir(os.path.join(WIX, "heat.exe"), "dir", SRC,
             "-cg", "AppFiles", "-dr", "INSTALLDIR",
             "-srd", "-sfrag", "-ag", "-sreg", "-scom",
             "-var", "var.SourceDir", "-nologo", "-out", dosyalar_wxs)

    urun_wxs = os.path.join(BUILD, "msi_urun.wxs")
    with open(urun_wxs, "w", encoding="utf-8") as f:
        f.write(URUN_WXS % {"ad": APP_ADI, "exe": EXE_ADI, "surum": v,
                            "upgrade": UPGRADE_CODE})

    # 2) Derle (candle).
    print(f"[2/3] Derleniyor (sürüm {v}) …")
    calistir(os.path.join(WIX, "candle.exe"), "-nologo",
             "-ext", "WixUtilExtension", f"-dSourceDir={SRC}",
             "-out", BUILD + os.sep, urun_wxs, dosyalar_wxs)

    # 3) Bağla (light). -sval: ICE doğrulaması atlanır — perUser dosya bileşenleri
    # ICE38'e takılır (bilinen ve kasıtlı durum), 1700+ dosyada doğrulama çok yavaş.
    print("[3/3] MSI paketleniyor (sıkıştırma birkaç dakika sürebilir) …")
    calistir(os.path.join(WIX, "light.exe"), "-nologo", "-sval",
             "-ext", "WixUtilExtension",
             "-out", MSI,
             os.path.join(BUILD, "msi_urun.wixobj"),
             os.path.join(BUILD, "msi_dosyalar.wixobj"))

    print(f"MSI hazır: {MSI} ({os.path.getsize(MSI)/1e6:.1f} MB, sürüm {v})")
    print("Dağıtım: MSI'yı downloads/CokluUyapKur.msi olarak kopyalayıp "
          "vendor_deploy'a commit + PUSH edin.")


if __name__ == "__main__":
    main()
