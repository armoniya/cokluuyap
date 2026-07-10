# -*- coding: utf-8 -*-
"""dist/CokluUyap içeriğini build/payload.zip'e sıkıştırır (kurucuya gömülür).
Zip KÖKÜ doğrudan uygulama dosyalarıdır (CokluUyap.exe, _internal/…) — kurucu
hedef klasöre açar açmaz çalışır durumda olur."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dist", "CokluUyap")
OUT = os.path.join(HERE, "build", "payload.zip")

if not os.path.isdir(SRC):
    raise SystemExit("dist/CokluUyap yok — önce cokluuyap_app.spec derleyin.")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
n = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for kok, _dirs, dosyalar in os.walk(SRC):
        for ad in dosyalar:
            tam = os.path.join(kok, ad)
            z.write(tam, os.path.relpath(tam, SRC))
            n += 1
print(f"payload.zip hazır: {n} dosya, {os.path.getsize(OUT)/1e6:.1f} MB -> {OUT}")
