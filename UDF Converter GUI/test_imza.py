# -*- coding: utf-8 -*-
"""
test_imza.py — E-imza akışını UÇTAN UCA dener (AKIS/akisp11 dahil).

Ne yapar?
  1) Kart modülünü (akisp11.dll) C_Initialize(NULL) ile yükler — AKIS uyumlu shim.
  2) Takılı kartla küçük bir content.xml'i imzalar (signer.sign_udf).
  3) Ürettiği .udf'i verify.verify_udf_log ile doğrular.

PIN burada, bu makinede getpass ile sorulur; HİÇBİR YERE gönderilmez/yazılmaz.

Çalıştırma (proje .venv python'ı ile):
  "../Uyap Haricen Giriş/.venv/Scripts/python.exe" test_imza.py
"""
import os
import sys
import getpass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signer
import verify


def pick_lib():
    for p in (r"C:\Windows\System32\akisp11.dll",
              r"C:\Windows\System32\etpkcs11.dll"):
        if os.path.exists(p):
            return p
    raise SystemExit("PKCS#11 DLL bulunamadı (akisp11/etpkcs11).")


SAMPLE_CONTENT = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<template format_id="1.0"><content><![CDATA[E-imza testi]]></content>'
    b'<elements><paragraph><content start_offset="0" length="12"/></paragraph></elements>'
    b'</template>'
)


def main():
    lib = pick_lib()
    print("PKCS#11 modülü:", lib)
    pin = getpass.getpass("Kart PIN (ekrana yazılmaz): ").strip()
    if not pin:
        raise SystemExit("PIN boş.")

    out = os.path.join(HERE, "_test_imzali.udf")
    print("İmzalanıyor...")
    signer.sign_udf(lib, pin, SAMPLE_CONTENT, out)
    print("İmzalı UDF yazıldı:", out)

    ok, logs = verify.verify_udf_log(out)
    print("\n--- Doğrulama günlüğü ---")
    for line in logs:
        print(" ", line)
    print("\nSONUÇ:", "✓ İMZA GEÇERLİ" if ok else "✗ İMZA DOĞRULANAMADI")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
