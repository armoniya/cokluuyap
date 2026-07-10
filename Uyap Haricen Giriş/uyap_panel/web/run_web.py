#!/usr/bin/env python3
"""
UYAP Ağ Geçidi — Django YEREL panelini başlatır (127.0.0.1:8000) ve tarayıcıyı açar.

ÖNEMLİ: --noreload ile TEK süreç çalışır. Panel, UYAP bağlantısını (ConnectionManager)
kendi süreci içinde yönettiğinden iki süreç (autoreload) durum tutarsızlığı yaratırdı.
UYAP oturumu ayrı port 8800'de kalır; bu panel onunla çakışmaz.

Çalıştırma (Uyap Haricen Giriş klasöründen):
    .venv\\Scripts\\python.exe uyap_panel\\web\\run_web.py
"""
import os
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))           # .../uyap_panel/web
UYAP_DIR = os.path.dirname(os.path.dirname(HERE))           # .../Uyap Haricen Giriş
for p in (HERE, UYAP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyap_web.settings")

ADDR = "127.0.0.1:8000"

if __name__ == "__main__":
    # Sunucu ayağa kalkınca tarayıcıyı aç.
    threading.Timer(1.5, lambda: webbrowser.open(f"http://{ADDR}/")).start()
    from django.core.management import execute_from_command_line
    # --insecure: DEBUG artık varsayılan False (güvenlik bulgu #6); runserver
    # bu durumda statikleri servis etmez. Yerel panelin arayüzü çalışsın diye
    # statik dosya servisini açıkça etkinleştiriyoruz (yalnızca 127.0.0.1).
    execute_from_command_line(["manage.py", "runserver", ADDR, "--noreload", "--insecure"])
