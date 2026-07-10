# -*- mode: python ; coding: utf-8 -*-
# Çoklu UYAP Ofis Ajanı — PyInstaller tanımı.
# Derleme (Panel/web içinde, UHG venv'iyle):
#   "..\..\Uyap Haricen Giriş\.venv\Scripts\python.exe" -m PyInstaller ofis_ajani.spec --noconfirm
# Çıktı: dist/CokluUyapOfis/ (klasör paketi; müşteriye zip olarak dağıtılır)
import os
from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.abspath(SPECPATH)
UHG = os.path.normpath(os.path.join(HERE, "..", "..", "Uyap Haricen Giriş"))
MODULES = os.path.normpath(os.path.join(HERE, "..", "modules"))

a = Analysis(
    ["ofis_ajani.py"],
    pathex=[HERE, UHG, MODULES],
    binaries=[],
    datas=[
        # Web kabuğu (server.py frozen'da bunları _MEIPASS/Panel/web altında arar)
        (os.path.join(HERE, "index.html"), "Panel/web"),
        (os.path.join(HERE, "login.html"), "Panel/web"),
        (os.path.join(HERE, "static"), "Panel/web/static"),
        # UYAP statik önbelleği (office_agent/home_client CACHE_DIR)
        (os.path.join(UHG, "uyap_core", "static_cache"), "uyap_core/static_cache"),
        # E-imza giriş modülü: adı Türkçe karakter içerdiğinden uyap_proxy bunu dosya
        # yolundan (importlib) yükler → PyInstaller çözümlemesi göremez, elle taşınır.
        (os.path.join(UHG, "uyap_core", "uyap_giris_dışarıdan.py"), "uyap_core"),
    ],
    hiddenimports=(
        collect_submodules("uvicorn")
        + collect_submodules("websockets")
        # uyap_giris_dışarıdan.py dinamik yüklendiği için bağımlılıkları da elle:
        + ["requests", "urllib3"]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Ağır ve çekirdek-paylaşım için GEREKSİZ bağımlılıklar dışlanır (hepsi tembel import;
    # yalnızca İcra/UDF/PDF sekmeleri kullanınca lazım — v1 ofis ajanı bunları taşımaz).
    # Böylece paket 100 MB (GitHub tek-dosya sınırı) altına iner.
    excludes=["playwright", "matplotlib", "PIL", "numpy", "pandas",
              "django", "fitz", "pymupdf", "openpyxl", "lxml"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CokluUyapOfis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # log penceresi görünür kalsın (destek için); sonraki sürümde tepsiye alınabilir
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CokluUyapOfis",
)
