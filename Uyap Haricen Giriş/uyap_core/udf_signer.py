#!/usr/bin/env python3
"""
UDF e-imza köprüsü (ofis tarafı)
--------------------------------
İstemci tarayıcıdan gelen bir `.udf` (ya da düz content.xml) belgesini, ofis makinesine
TAKILI e-imza KARTIYLA (PKCS#11) HEADLESS olarak imzalar ve imzalı `.udf`'i geri döndürür.

İmza motoru: `UDF imzalayan/` klasöründeki KANITLI kod (PyKCS11 + `cades.py`). cades.py,
gerçek UYAP `sign.sgn` dosyasından tersine mühendislikle doğrulanmış CAdES-BES (detached
CMS) profilini birebir kurar. Özel anahtar kartı TERK ETMEZ; imza kart içinde üretilir.
(Daha önce denenen ArkSigner :5975 yolu, ürettiği CMS profilinin UYAP'ça kabulü belirsiz
olduğu için bırakıldı; kart erişimi kanıtlı PKCS#11 ile yapılıyor.)

Bir `.udf` çoğunlukla ZIP'tir (içinde `content.xml` + imzalıysa `sign.sgn`); bazı UYAP
belgeleri ise ZIP'lenmeden DÜZ content.xml olarak iner — ikisi de desteklenir. İmzalama:
    1. content.xml elde edilir (ZIP'ten çıkarılır ya da gövde düz XML ise doğrudan),
    2. cades.to_be_signed ile signedAttrs kurulur, kart sha256(signedAttrs)'i imzalar,
    3. cades.build_cms ile detached CMS (`sign.sgn`) üretilir,
    4. content.xml + sign.sgn (+ varsa korunan ekler) yeni `.udf` ZIP'i olarak paketlenir.
"""

import io
import os
import hashlib
import zipfile

# PKCS#11 modülü (kart sürücüsüyle gelir). Ortam değişkeniyle geçersiz kılınabilir.
# Windows'ta tipik konum System32; AKIS=akisp11.dll, TURKTRUST/SafeNet=etpkcs11.dll.
_PKCS11_CANDIDATES = ("akisp11.dll", "etpkcs11.dll", "libakisp11.so", "akisp11_64.dll")


class SignError(RuntimeError):
    """İmzalama akışında beklenen, kullanıcıya gösterilebilir hata."""
    pass


def _diag(body):
    """Gelen baytları teşhis için kısa, okunabilir bir özete çevirir (boyut + ilk baytlar).
    'Geçersiz UDF' gibi hatalarda gerçekte NE geldiğini (HTML, boş, base64 metin, vs.) görmek için."""
    n = len(body or b"")
    head = (body or b"")[:16]
    hexs = head.hex(" ")
    try:
        txt = head.decode("latin-1").replace("\n", "\\n").replace("\r", "\\r")
    except Exception:
        txt = "?"
    kind = "?"
    if body[:2] == b"PK":
        kind = "ZIP/PK"
    elif body[:5].lstrip().lower().startswith(b"<!doc") or body[:6].lstrip().lower().startswith(b"<html"):
        kind = "HTML"
    elif body[:1] in (b"{", b"["):
        kind = "JSON"
    elif body[:4] == b"%PDF":
        kind = "PDF"
    return "boyut=%d bayt, ilk16=[%s] ('%s'), tür≈%s" % (n, hexs, txt, kind)


def _detect_pkcs11_lib():
    """PKCS#11 modül yolunu bulur: önce UYAP_PKCS11_LIB ortam değişkeni, sonra Windows
    System32/SysWOW64 içindeki bilinen kart DLL'leri (akisp11/etpkcs11)."""
    env = os.environ.get("UYAP_PKCS11_LIB")
    if env and os.path.exists(env):
        return env
    search_dirs = []
    win = os.environ.get("SystemRoot", r"C:\Windows")
    search_dirs += [os.path.join(win, "System32"), os.path.join(win, "SysWOW64")]
    search_dirs += ["/usr/lib", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu"]
    for d in search_dirs:
        for name in _PKCS11_CANDIDATES:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


def card_present():
    """Ofis makinesine takılı, sertifikası olan bir kart var mı? (headless ön kontrol)"""
    lib = _detect_pkcs11_lib()
    if not lib:
        return False
    try:
        from . import akis_pkcs11 as PyKCS11  # AKIS uyumlu (C_Initialize NULL) drop-in ikame
        pkcs11 = PyKCS11.PyKCS11Lib()
        pkcs11.load(lib)
        return bool(pkcs11.getSlotList(tokenPresent=True))
    except Exception:
        return False


def _p1363_to_der(sig):
    """ECDSA r||s ham imzasını DER SEQUENCE(INTEGER r, INTEGER s)'e çevirir (CMS bunu ister)."""
    from . import cades
    n = len(sig) // 2
    r = int.from_bytes(sig[:n], "big")
    s = int.from_bytes(sig[n:], "big")
    return cades.seq(cades.integer(r), cades.integer(s))


def _make_sign_sgn(content_xml, pin, prefer_serial=None):
    """content.xml'i ofise takılı KARTLA (PKCS#11) imzalar ve detached CMS (`sign.sgn`)
    baytlarını döndürür. İmza profili = `cades.py` (kanıtlı UYAP CAdES-BES).
    Özel anahtar kartı terk etmez; imza kart içinde üretilir."""
    if not pin:
        raise SignError("Kart PIN'i bulunamadı (ofis henüz giriş yapmamış olabilir).")
    lib = _detect_pkcs11_lib()
    if not lib:
        raise SignError(
            "Kart sürücüsü (PKCS#11 DLL: akisp11.dll/etpkcs11.dll) bulunamadı. "
            "UYAP_PKCS11_LIB ortam değişkeniyle yolu belirtebilirsiniz.")

    try:
        from . import akis_pkcs11 as PyKCS11  # AKIS uyumlu (C_Initialize NULL) drop-in ikame
        from cryptography import x509
    except Exception as e:
        raise SignError("İmzalama kütüphaneleri yüklü değil (cryptography). Detay: %s" % e)

    from . import cades

    pkcs11 = PyKCS11.PyKCS11Lib()
    pkcs11.load(lib)
    slots = pkcs11.getSlotList(tokenPresent=True)
    if not slots:
        raise SignError("Kartı olan okuyucu bulunamadı. E-imza kartı takılı mı?")

    session = pkcs11.openSession(slots[0], PyKCS11.CKF_SERIAL_SESSION)
    try:
        try:
            session.login(str(pin))
        except PyKCS11.PyKCS11Error as e:
            raise SignError("Karta giriş yapılamadı (PIN hatalı ya da kart kilitli olabilir). Detay: %s" % e)

        # İmzaya uygun sertifikayı seç
        certs = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE)])
        cert_der = None
        cid = None
        for c in certs:
            a = session.getAttributeValue(c, [PyKCS11.CKA_VALUE, PyKCS11.CKA_ID])
            der = bytes(a[0])
            try:
                xc = x509.load_der_x509_certificate(der)
                ku = xc.extensions.get_extension_for_class(x509.KeyUsage).value
                ok = ku.digital_signature or ku.content_commitment
            except Exception:
                ok = True
            if prefer_serial and str(getattr(xc, "serial_number", "")) != str(prefer_serial):
                continue
            if ok:
                cert_der, cid = der, a[1]
                break
        if cert_der is None and certs:
            a = session.getAttributeValue(certs[0], [PyKCS11.CKA_VALUE, PyKCS11.CKA_ID])
            cert_der, cid = bytes(a[0]), a[1]
        if cert_der is None:
            raise SignError("Kartta sertifika bulunamadı.")

        # signedAttrs'i bu sertifikaya göre kur (cades.py kanıtlı profil)
        signed_attrs, _, _ = cades.to_be_signed(content_xml, cert_der)

        # Eşleşen özel anahtarı bul
        priv = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY), (PyKCS11.CKA_ID, cid)]) \
            or session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY)])
        if not priv:
            raise SignError("Kartta özel anahtar bulunamadı.")
        priv = priv[0]
        ktype = session.getAttributeValue(priv, [PyKCS11.CKA_KEY_TYPE])[0]
        is_ecdsa = ktype in (PyKCS11.CKK_ECDSA, PyKCS11.CKK_EC)

        h = hashlib.sha256(signed_attrs).digest()
        if is_ecdsa:
            to_sign = h
            mech = PyKCS11.Mechanism(PyKCS11.CKM_ECDSA, None)
        else:
            prefix = b'\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20'
            to_sign = prefix + h
            mech = PyKCS11.Mechanism(PyKCS11.CKM_RSA_PKCS, None)

        signature = bytes(session.sign(priv, to_sign, mech))
    finally:
        try:
            session.logout()
        except Exception:
            pass
        try:
            session.closeSession()
        except Exception:
            pass

    if is_ecdsa and len(signature) % 2 == 0:
        signature = _p1363_to_der(signature)

    return cades.build_cms(content_xml, cert_der, signed_attrs, signature, use_ecdsa=is_ecdsa)


def _sign_content(content_xml, certificate_id, pin, preserved=()):
    """content.xml baytlarını KARTLA e-imzalar ve imzalı `.udf` (ZIP) baytları döndürür.

    `preserved`: korunacak ek ZIP girdileri [(ad, bayt), ...] (ör. orijinal UDF'deki
    gömülü görseller). sign.sgn taze imzayla yazılır. certificate_id yok sayılır;
    kart sertifikası PKCS#11'den seçilir (PIN ile).
    """
    sgn = _make_sign_sgn(content_xml, pin)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in preserved:
            if name != "sign.sgn":
                zout.writestr(name, data)
        if not any(n == "content.xml" for n, _ in preserved):
            zout.writestr("content.xml", content_xml)
        zout.writestr("sign.sgn", sgn)
    return out.getvalue()


def sign_udf_bytes(udf_bytes, certificate_id, pin):
    """Ham `.udf` baytlarını imzalar ve imzalı `.udf` baytlarını döndürür.

    Orijinal paketteki TÜM girdiler (content.xml, varsa ekli görseller/öznitelikler)
    korunur; yalnızca `sign.sgn` (taze imza) eklenir/değiştirilir.
    """
    if not pin:
        raise SignError("Kart PIN'i bulunamadı (ofis henüz giriş yapmamış olabilir).")

    # Bir `.udf` çoğunlukla ZIP'tir; AMA bazı UYAP belgeleri ZIP'lenmeden DÜZ content.xml
    # olarak iner. O durumda tüm gövdeyi content.xml kabul edip paketleyerek imzalarız.
    head = (udf_bytes or b"").lstrip()[:5].lower()
    if not udf_bytes[:2] == b"PK" and head.startswith(b"<?xml"):
        return _sign_content(udf_bytes, certificate_id, pin)

    try:
        zin = zipfile.ZipFile(io.BytesIO(udf_bytes))
    except zipfile.BadZipFile:
        # ZIP değil; XML olabilir mi? (BOM/boşluk sonrası <?xml ya da kök etiket)
        if b"<" in udf_bytes[:64] and (b"<?xml" in udf_bytes[:64].lower() or b"<template" in udf_bytes[:256].lower()):
            return _sign_content(udf_bytes, certificate_id, pin)
        raise SignError("Geçersiz UDF: ZIP olarak açılamadı. (%s)" % _diag(udf_bytes))

    with zin:
        names = zin.namelist()
        if "content.xml" not in names:
            raise SignError("UDF içinde content.xml bulunamadı; bu bir UYAP belgesi olmayabilir.")
        content = zin.read("content.xml")
        # sign.sgn dışındaki TÜM orijinal girdileri (content.xml dâhil) aynen sakla.
        preserved = [(n, zin.read(n)) for n in names if n != "sign.sgn"]

    return _sign_content(content, certificate_id, pin, preserved=preserved)


# Sunucuda dönüştürme için gereken dosya uzantıları (UDF dışı).
CONVERTIBLE_EXTS = (".docx", ".doc", ".pdf")


def _convert_to_content_xml(body, ext):
    """Word/PDF baytlarını UDF content.xml baytlarına çevirir (ofis tarafı, geçici dosya ile).
    Dönüştürme kütüphaneleri (python-docx, pypdf, Pillow, PyMuPDF) kurulu değilse açık hata verir."""
    import os
    import tempfile
    try:
        from . import udf_converter
    except Exception as e:
        raise SignError(
            "Word/PDF dönüştürme kütüphaneleri ofiste kurulu değil "
            "(python-docx, pypdf, Pillow, PyMuPDF). Detay: %s" % e)

    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        try:
            return udf_converter.convert_to_udf_xml(path)
        except Exception as e:
            raise SignError("Belge UDF'ye çevrilemedi: %s" % e)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def sign_document(body, filename_or_ext, certificate_id, pin):
    """Yüklenen bir belgeyi (UDF/Word/PDF) gerekiyorsa UDF'ye çevirip e-imzalar.

    filename_or_ext: dosya adı (".udf"/".docx"/...) ya da doğrudan uzantı.
    .udf  → olduğu gibi imzalanır (içerik korunur).
    .docx/.doc/.pdf → content.xml'e çevrilip yeni UDF olarak paketlenir, sonra imzalanır.
    """
    if not pin:
        raise SignError("Kart PIN'i bulunamadı (ofis henüz giriş yapmamış olabilir).")
    if not body:
        raise SignError("Boş belge.")

    name = (filename_or_ext or "").strip().lower()
    ext = name if name.startswith(".") and "." not in name[1:] else os.path.splitext(name)[1]

    # ZIP (PK) ya da düz XML gövdeler UDF olarak işlenir (UYAP belgeleri ikisi de olabilir).
    looks_xml = body.lstrip()[:5].lower().startswith(b"<?xml")
    if ext == ".udf" or (not ext and (body[:2] == b"PK" or looks_xml)):
        return sign_udf_bytes(body, certificate_id, pin)
    if ext in CONVERTIBLE_EXTS:
        content_xml = _convert_to_content_xml(body, ext)
        return _sign_content(content_xml, certificate_id, pin)
    raise SignError(
        "Desteklenmeyen dosya türü: %s — yalnızca .udf, .docx, .doc, .pdf kabul edilir. (%s)"
        % (ext or "?", _diag(body)))
