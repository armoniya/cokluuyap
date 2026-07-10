# -*- coding: utf-8 -*-
"""
Çoklu UYAP Ofis Ajanı — paket giriş noktası (PyInstaller)
=========================================================
Müşteriye dağıtılan exe budur: web panelini (server.py, 127.0.0.1:8090) başlatır ve
tarayıcıyı açar. Kayıtlı kimlik + PIN varsa paylaşım kendiliğinden kurulur
(server.boot_autoconnect); yoksa kullanıcı panelden bir kez giriş yapıp kaydeder.

Geliştirmede de çalışır:  python ofis_ajani.py
Paket derleme:            pyinstaller ofis_ajani.spec
"""
import threading
import time
import webbrowser

import server  # sys.path'i (UHG + modules) kendisi kurar

# PyInstaller çözümlemesi için açık importlar — server bunları çalışma anında dinamik
# yükler (import uyap_app), o yüzden burada anılmazlarsa pakete girmezler.
import uyap_app          # noqa: F401  (giriş doğrulama + DPAPI + office_agent köprüsü)
import uyap_core         # noqa: F401
from uyap_core import office_agent, home_client, uyap_proxy  # noqa: F401


def _open_browser():
    time.sleep(1.5)  # sunucu porta bağlansın
    try:
        webbrowser.open("http://127.0.0.1:8090")
    except Exception:
        pass


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    server.main()
