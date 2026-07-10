#!/usr/bin/env python3
"""
UYAP Ağ Geçidi — ttkbootstrap masaüstü panelini başlatır.

Çalıştırma (Uyap Haricen Giriş klasöründen):
    .venv\\Scripts\\python.exe uyap_panel\\run_gui.py
"""
import os
import sys

# "Uyap Haricen Giriş" kökünü yola ekle (uyap_panel + uyap_core import edilebilsin).
UYAP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if UYAP_DIR not in sys.path:
    sys.path.insert(0, UYAP_DIR)

from uyap_panel.gui.app import main

if __name__ == "__main__":
    main()
