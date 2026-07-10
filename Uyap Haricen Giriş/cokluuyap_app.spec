# -*- mode: python ; coding: utf-8 -*-
# Çoklu UYAP — masaüstü ofis uygulaması (uyap_app.py) PyInstaller tanımı.
# Müşteriye dağıtılan program BUDUR (panel.py tarzı arayüz: giriş → otomatik paylaşım).
# Derleme ("Uyap Haricen Giriş" içinde):
#   .\.venv\Scripts\python.exe -m PyInstaller cokluuyap_app.spec --noconfirm
# Çıktı: dist/CokluUyap/ — tek başına dağıtılmaz; CokluUyapKur.exe (kurucu) içine gömülür.
import os
from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.abspath(SPECPATH)

a = Analysis(
    ["uyap_app.py"],
    pathex=[HERE],
    binaries=[],
    datas=[
        # UYAP statik önbelleği (office_agent/home_client CACHE_DIR)
        (os.path.join(HERE, "uyap_core", "static_cache"), "uyap_core/static_cache"),
        # E-imza giriş modülü: adı Türkçe karakter içerdiğinden uyap_proxy bunu dosya
        # yolundan (importlib) yükler → PyInstaller çözümlemesi göremez, elle taşınır.
        (os.path.join(HERE, "uyap_core", "uyap_giris_dışarıdan.py"), "uyap_core"),
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
    # Çekirdek paylaşım için gereksiz ağır bağımlılıklar dışlanır (mts modülleri salt
    # stdlib kullanır, doğrulandı). Paket ~140 MB açık / ~60 MB sıkışık kalır;
    # gömülü PostgreSQL / İcra araçları BU PAKETE ASLA GİRMEZ (2 GB şişme yasağı).
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
    name="CokluUyap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # masaüstü arayüz; log zaten pencere içindeki günlükte
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CokluUyap",
)
