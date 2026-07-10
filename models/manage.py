#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UYAP İcra veri katmanı — Django yönetim aracı.

Çalıştırma (venv ile), bu klasörden:
    python manage.py makemigrations icra_models
    python manage.py migrate
    python manage.py shell
PostgreSQL bağlantısı UYAP_DB_* ortam değişkenlerinden okunur (bkz. uyapdata/settings.py).
"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django bulunamadı. Sanal ortamı etkinleştirdiniz mi?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
