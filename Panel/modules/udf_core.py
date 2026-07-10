# -*- coding: utf-8 -*-
"""
UDF Dönüştürücü — paylaşılan motor sarmalayıcı (GUI + web ortak)
================================================================
ÇALIŞMA MANTIĞI orijinal koddan AYNEN kullanılır; burada yeniden yazılmaz.

Orijinal modüller ("Kararlı/UDF Converter GUI" klasöründen, dokunulmadan):
  • converter.convert_to_udf_xml(input_path)            → docx/doc/pdf → content.xml (bytes)
  • signer.sign_udf(dll, pin, content_bytes, out_path)  → e-imza ile imzalı UDF paketi
  • verify.verify_udf_log(udf_path)                      → (başarılı_mı, [günlük satırları])

Bu dosya yalnızca orijinal app.py'deki "run_convert_only" / "run_convert_and_sign"
akışlarını (zip'leme + imzalama + doğrulama) sarmalar. Mantık birebir aynıdır.
"""

import os
import sys
import zipfile

# Orijinal UDF modüllerinin bulunduğu klasörü import yoluna ekle
_HERE = os.path.dirname(os.path.abspath(__file__))
_UDF_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "UDF Converter GUI"))
if _UDF_DIR not in sys.path:
    sys.path.insert(0, _UDF_DIR)

import converter  # noqa: E402  (orijinal — değiştirilmez)
import verify     # noqa: E402  (orijinal — değiştirilmez)
# NOT: signer, PyKCS11'e bağımlıdır ve yalnızca e-imzalı akışta gerekir; bu yüzden
# tembel (lazy) import edilir. Böylece PyKCS11 kurulu olmasa da dönüştürme çalışır.


# Orijinal app.py'deki find_common_dlls ile aynı liste (sadece yol tespiti — mantık değil)
DEFAULT_DLLS = [
    r"C:\Windows\System32\akisp11.dll",
    r"C:\Program Files\Akis Kart Izleme Araci\akisp11.dll",
    r"C:\Program Files (x86)\Akis Kart Izleme Araci\akisp11.dll",
    r"C:\Windows\System32\etpkcs11.dll",
    r"C:\Program Files\SafeNet\Authentication\SAC\x64\IDPrimePKCS1164.dll",
    r"C:\Program Files\Sentrigo\SentrigoPKCS11\sentrigop11.dll",
    r"C:\Windows\System32\kisp11.dll",
]


def find_common_dlls():
    """Sistemde kurulu standart PKCS#11 DLL'lerini döndürür (orijinaldeki ile aynı,
    hızlı: yalnızca bilinen sabit yollar)."""
    return [p for p in DEFAULT_DLLS if os.path.exists(p)]


# Derin aramada eşleştirilecek bilinen PKCS#11 sürücü dosya adları
_DLL_NAMES = {os.path.basename(p).lower() for p in DEFAULT_DLLS} | {
    "akisp11.dll", "etpkcs11.dll", "idprimepkcs1164.dll", "idprimepkcs11.dll",
    "sentrigop11.dll", "kisp11.dll", "aetpkss1.dll", "asepkcs.dll",
    "ncryptoki.dll", "opensc-pkcs11.dll", "softhsm2.dll", "ykcs11.dll",
    "gclib.dll", "cmp11.dll", "ekutp11.dll", "akisp11_64.dll",
}


def find_dlls_deep(roots=None, max_seconds=10):
    """Önce hızlı sabit yolları, bulamazsa bilgisayarda BİLİNEN sürücü adlarını arar.
    Süreyle sınırlıdır (varsayılan 10 sn). Döner: bulunan DLL yollarının listesi."""
    import time
    found = list(find_common_dlls())
    seen = {p.lower() for p in found}
    if roots is None:
        win = os.environ.get("WINDIR", r"C:\Windows")
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.join(win, "System32"),
            os.path.join(win, "SysWOW64"),
        ]
    deadline = time.time() + max_seconds
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            if time.time() > deadline:
                break
            for fn in files:
                if fn.lower() in _DLL_NAMES:
                    full = os.path.join(dirpath, fn)
                    if full.lower() not in seen:
                        seen.add(full.lower())
                        found.append(full)
        if time.time() > deadline:
            break
    return found


def convert_only(input_path, output_path):
    """Orijinal app.run_convert_only akışı: dönüştür + imzasız UDF (zip) olarak yaz."""
    xml_bytes = converter.convert_to_udf_xml(input_path)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", xml_bytes)
    return output_path


def convert_and_sign(input_path, output_path, dll_path, pin, serial_no=None):
    """Orijinal app.run_convert_and_sign akışı: dönüştür → e-imzala → doğrula.
    Döner: (basarili_mi, [doğrulama günlüğü satırları])."""
    import signer  # tembel import — PyKCS11 yalnızca burada gerekir (orijinal — değiştirilmez)
    xml_bytes = converter.convert_to_udf_xml(input_path)
    signer.sign_udf(dll_path, pin, xml_bytes, output_path, serial_no=serial_no)
    success, logs = verify.verify_udf_log(output_path)
    return success, logs


def sign_existing(input_udf_path, output_path, dll_path, pin, serial_no=None):
    """Var olan bir UDF dosyasını OLDUĞU GİBİ (dönüştürmeden) e-imzalar; sonra doğrular.
    İçindeki content.xml ve ek dosyalar korunur, yalnızca imza (sign.sgn) güncellenir.
    Döner: (basarili_mi, [doğrulama günlüğü satırları])."""
    import signer  # tembel import — PyKCS11 yalnızca burada gerekir (orijinal — değiştirilmez)
    signer.sign_existing_udf(dll_path, pin, input_udf_path, output_path, serial_no=serial_no)
    success, logs = verify.verify_udf_log(output_path)
    return success, logs
