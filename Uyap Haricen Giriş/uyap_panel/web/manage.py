#!/usr/bin/env python3
"""Django yönetim betiği — UYAP Ağ Geçidi yerel paneli (port 8000)."""
import os
import sys


def main():
    # uyap_core ve uyap_panel'i import edilebilir kıl ("Uyap Haricen Giriş" kökü).
    HERE = os.path.dirname(os.path.abspath(__file__))               # .../uyap_panel/web
    UYAP_DIR = os.path.dirname(os.path.dirname(HERE))               # .../Uyap Haricen Giriş
    for p in (HERE, UYAP_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyap_web.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Django bulunamadı. Sanal ortamı etkinleştirip 'pip install django' çalıştırın.") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
