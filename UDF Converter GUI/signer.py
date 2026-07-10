import sys
import zipfile
# AKIS akisp11.dll, C_Initialize'ı yalnızca NULL pInitArgs ile kabul eder; PyKCS11 ise
# OS-locking'li bir yapı gönderdiğinden CKR_ARGUMENTS_BAD ile patlar. Bu yüzden PyKCS11
# yerine, C_Initialize(NULL) çağıran drop-in ctypes ikamesini kullanıyoruz (API aynı).
import akis_pkcs11 as PyKCS11
from cryptography import x509
import cades

def _sign_content(lib_path, pin, content_bytes, serial_no=None):
    """
    Akıllı karta PKCS#11 ile bağlanır, content_bytes'ı CAdES-BES profili ile
    imzalar ve imza zarfını (sign.sgn) bytes olarak döndürür.
    UDF paketlemesini (ZIP) çağıran taraf yapar.
    """
    pkcs11 = PyKCS11.PyKCS11Lib()
    try:
        pkcs11.load(lib_path)
    except Exception as e:
        raise RuntimeError(f"PKCS#11 kütüphanesi yüklenemedi: {str(e)}\nLütfen DLL yolunu kontrol edin.")

    slots = pkcs11.getSlotList(tokenPresent=True)
    if not slots:
        raise RuntimeError("Kart okuyucu veya takılı akıllı kart bulunamadı. Kartın takılı olduğundan emin olun.")

    # Open read-write session
    session = pkcs11.openSession(slots[0], PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION)
    try:
        try:
            session.login(pin)
        except Exception as e:
            raise RuntimeError(f"Karta giriş yapılamadı (PIN hatalı olabilir). Hata detayı: {str(e)}")

        # Find certificates
        certs = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE)])
        if not certs:
            raise RuntimeError("Kart içerisinde herhangi bir sertifika bulunamadı.")

        cert_der = None
        cid = None

        for c in certs:
            try:
                attrs = session.getAttributeValue(c, [PyKCS11.CKA_VALUE, PyKCS11.CKA_ID])
                der = bytes(attrs[0])
                c_id = attrs[1]

                xc = x509.load_der_x509_certificate(der)

                # Check Key Usage (Digital Signature or Content Commitment / Non-Repudiation)
                try:
                    ku = xc.extensions.get_extension_for_class(x509.KeyUsage).value
                    ok = ku.digital_signature or ku.content_commitment
                except Exception:
                    ok = True  # Fallback if extension not found

                if serial_no and str(xc.serial_number) != str(serial_no):
                    continue

                if ok:
                    cert_der = der
                    cid = c_id
                    break
            except Exception:
                continue

        if cert_der is None:
            # Try first cert as fallback
            try:
                attrs = session.getAttributeValue(certs[0], [PyKCS11.CKA_VALUE, PyKCS11.CKA_ID])
                cert_der = bytes(attrs[0])
                cid = attrs[1]
            except Exception:
                raise RuntimeError("Uyumlu imzalama sertifikası bulunamadı veya karttan okunamadı.")

        # Find matching private key
        priv_objects = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY), (PyKCS11.CKA_ID, cid)])
        if not priv_objects:
            priv_objects = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY)])
        if not priv_objects:
            raise RuntimeError("Kart içerisinde özel anahtar (Private Key) bulunamadı.")

        priv = priv_objects[0]

        # Check key type (RSA or ECDSA)
        key_type = session.getAttributeValue(priv, [PyKCS11.CKA_KEY_TYPE])[0]
        is_ecdsa = key_type in (PyKCS11.CKK_ECDSA, PyKCS11.CKK_EC)

        # Build signed attributes using CAdES-BES profile
        signed_attrs, _, _ = cades.to_be_signed(content_bytes, cert_der)

        # Hash attributes
        import hashlib
        h = hashlib.sha256(signed_attrs).digest()

        if is_ecdsa:
            to_sign = h
            mech = PyKCS11.Mechanism(PyKCS11.CKM_ECDSA, None)
        else:
            # RSA requires DigestInfo prefix prefixing the SHA-256 hash
            prefix = b'\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20'
            to_sign = prefix + h
            mech = PyKCS11.Mechanism(PyKCS11.CKM_RSA_PKCS, None)

        # Sign on card
        signature = bytes(session.sign(priv, to_sign, mech))

        # ECDSA: IEEE P1363 (r||s) -> DER SEQUENCE
        if is_ecdsa and len(signature) % 2 == 0:
            signature = _p1363_to_der(signature)

        # Build final CMS structure (sign.sgn)
        sgn = cades.build_cms(content_bytes, cert_der, signed_attrs, signature, use_ecdsa=is_ecdsa)
        return sgn

    finally:
        try:
            session.logout()
        except Exception:
            pass
        session.closeSession()


def sign_udf(lib_path, pin, content_bytes, output_udf_path, serial_no=None):
    """
    content_bytes (content.xml) içeriğini imzalar ve content.xml + sign.sgn
    olarak yeni bir UDF (ZIP) paketi üretir.
    """
    sgn = _sign_content(lib_path, pin, content_bytes, serial_no)

    # Package content.xml and sign.sgn into UDF (ZIP)
    with zipfile.ZipFile(output_udf_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", content_bytes)
        z.writestr("sign.sgn", sgn)


def sign_existing_udf(lib_path, pin, input_udf_path, output_udf_path, serial_no=None):
    """
    Var olan bir UDF dosyasını OLDUĞU GİBİ alıp imzalar.
    İçindeki content.xml ve diğer ek dosyalar (görseller vb.) korunur; yalnızca
    imza zarfı (sign.sgn) güncellenip eklenir. Dönüştürme yapılmaz.
    """
    # UDF aslında bir ZIP arşividir. İçeriği belleğe okuyup kaynağı kapatıyoruz
    # (böylece çıkış == giriş olsa bile güvenle üzerine yazılabilir).
    try:
        with zipfile.ZipFile(input_udf_path, "r") as zin:
            names = zin.namelist()
            content_name = next((n for n in names if n.lower() == "content.xml"), None)
            if content_name is None:
                raise RuntimeError(
                    "Seçilen dosya geçerli bir UDF değil (içinde content.xml bulunamadı)."
                )
            content_bytes = zin.read(content_name)
            # content.xml ve eski imza hariç tüm ek girdileri sakla
            extras = {
                n: zin.read(n)
                for n in names
                if n.lower() not in ("content.xml", "sign.sgn")
            }
    except zipfile.BadZipFile:
        raise RuntimeError("Seçilen dosya geçerli bir UDF/ZIP arşivi değil.")

    # content.xml'i olduğu gibi imzala
    sgn = _sign_content(lib_path, pin, content_bytes, serial_no)

    # Yeni UDF'i yaz: content.xml + (varsa) ek girdiler + yeni sign.sgn
    with zipfile.ZipFile(output_udf_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", content_bytes)
        for name, data in extras.items():
            z.writestr(name, data)
        z.writestr("sign.sgn", sgn)


def _p1363_to_der(sig):
    """ECDSA r||s -> DER SEQUENCE(INTEGER r, INTEGER s)."""
    n = len(sig) // 2
    r = int.from_bytes(sig[:n], "big")
    s = int.from_bytes(sig[n:], "big")
    return cades.seq(cades.integer(r), cades.integer(s))
