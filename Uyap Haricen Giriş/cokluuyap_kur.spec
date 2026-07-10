# -*- mode: python ; coding: utf-8 -*-
# Çoklu UYAP Kurucu — TEK DOSYA (onefile) PyInstaller tanımı.
# Sıra: (1) cokluuyap_app.spec derle → dist/CokluUyap
#       (2) python paket_hazirla.py   → build/payload.zip (dist içeriği)
#       (3) bu spec derle             → dist/CokluUyapKur.exe (müşteriye giden TEK dosya)
import os

HERE = os.path.abspath(SPECPATH)
PAYLOAD = os.path.join(HERE, "build", "payload.zip")
if not os.path.exists(PAYLOAD):
    raise SystemExit("build/payload.zip yok — önce paket_hazirla.py çalıştırın.")

a = Analysis(
    ["kurucu.py"],
    pathex=[HERE],
    binaries=[],
    datas=[(PAYLOAD, ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "matplotlib", "PIL", "numpy", "pandas", "django",
              "fitz", "pymupdf", "openpyxl", "lxml", "aiortc", "av", "cryptography",
              "uvicorn", "websockets", "requests", "pydantic"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CokluUyapKur",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # kurulum penceresi tkinter; konsol açılmaz
    icon=None,
)
