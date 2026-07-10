"""
cades.py — UYAP UDF imzasi icin saf-Python CAdES-BES (detached CMS) olusturucu.

Harici ASN.1 bagimliligi YOK. Yalnizca standart kutuphane.
Kart erisimi cagiran tarafta (signer fonksiyonu) yapilir; bu modul kriptografi
yapmaz, sadece dogru ASN.1 yapisini kurar.

Gercek UYAP sign.sgn'inden tersine muhendislikle dogrulanan profil:
  - CMS SignedData v1, DER, DETACHED (eContent yok)
  - digestAlgorithm: SHA-256
  - signedAttrs: contentType, signingTime, messageDigest, signingCertificateV2
  - signatureAlgorithm: RSA (sha256WithRSAEncryption) — karta gore ECDSA da olabilir
  - sertifikalar: yalnizca imzalayan
"""
from __future__ import annotations
import hashlib
import datetime
from typing import Callable

# ----------------------------------------------------------------------------
# Minimal DER kodlayici
# ----------------------------------------------------------------------------

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b

def _tlv(tag: int, val: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(val)) + val

def seq(*items: bytes) -> bytes:      return _tlv(0x30, b"".join(items))
def set_of(*items: bytes) -> bytes:   return _tlv(0x31, b"".join(items))
def octet(b: bytes) -> bytes:         return _tlv(0x04, b)
def integer(n: int) -> bytes:
    if n == 0:
        return _tlv(0x02, b"\x00")
    b = n.to_bytes((n.bit_length() + 8) // 8, "big")  # +8 => isaret biti emniyeti
    return _tlv(0x02, b)

def explicit(tag_no: int, content: bytes) -> bytes:
    return _tlv(0xA0 | tag_no, content)

def utctime(dt: datetime.datetime) -> bytes:
    return _tlv(0x17, dt.strftime("%y%m%d%H%M%SZ").encode("ascii"))

def oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    enc = [40 * parts[0] + parts[1]]
    body = bytearray()
    for p in parts[2:]:
        if p < 0x80:
            body.append(p)
        else:
            stack = [p & 0x7f]
            p >>= 7
            while p:
                stack.append((p & 0x7f) | 0x80)
                p >>= 7
            body.extend(reversed(stack))
    head = bytes(enc) + bytes(body)
    return _tlv(0x06, head)

# OID sabitleri
OID_CONTENT_TYPE   = oid("1.2.840.113549.1.9.3")
OID_MESSAGE_DIGEST = oid("1.2.840.113549.1.9.4")
OID_SIGNING_TIME   = oid("1.2.840.113549.1.9.5")
OID_PKCS7_DATA     = oid("1.2.840.113549.1.7.1")
OID_PKCS7_SIGNED   = oid("1.2.840.113549.1.7.2")
OID_SIGNING_CERT_V2= oid("1.2.840.113549.1.9.16.2.47")
OID_SHA256         = oid("2.16.840.1.101.3.4.2.1")
OID_RSA_ENC        = oid("1.2.840.113549.1.1.1")          # rsaEncryption
OID_SHA256_RSA     = oid("1.2.840.113549.1.1.11")         # sha256WithRSAEncryption
OID_ECDSA_SHA256   = oid("1.2.840.10045.4.3.2")           # ecdsa-with-SHA256

def algid(o: bytes, params: bytes | None = b"\x05\x00") -> bytes:
    # RSA imza alg-id'leri NULL parametre tasir; ECDSA tasimaz.
    return seq(o, params) if params is not None else seq(o)

def digest_algid() -> bytes:
    # SHA-256 digestAlgorithm: gercek UYAP imzasi parametreyi (NULL dahil)
    # ATLIYOR -> SEQUENCE { OID } seklinde, parametresiz.
    return seq(OID_SHA256)


# ----------------------------------------------------------------------------
# Sertifika ayristirma (issuer DER + serialNumber cikarmak icin)
# cryptography varsa onu kullaniriz; yoksa kullanici DER issuer/serial verir.
# ----------------------------------------------------------------------------

def cert_issuer_and_serial(cert_der: bytes):
    """Sertifika DER'inden (issuer_der_bytes, serial_int) dondurur."""
    from cryptography import x509
    c = x509.load_der_x509_certificate(cert_der)
    issuer_der = c.issuer.public_bytes()  # RDNSequence DER
    return issuer_der, c.serial_number


# ----------------------------------------------------------------------------
# SignedAttributes
# ----------------------------------------------------------------------------

def _attr(attr_oid: bytes, value_der: bytes) -> bytes:
    # Attribute ::= SEQUENCE { type OID, values SET OF ANY }
    return seq(attr_oid, set_of(value_der))

def build_signed_attributes(content: bytes,
                            cert_der: bytes,
                            signing_time: datetime.datetime) -> bytes:
    """
    SignedAttributes'i DER SET olarak dondurur (imzalanacak hali).
    DIKKAT: imzalanirken bu yapinin tag'i 0x31 (SET OF) olmalidir; CMS'te
    SignerInfo icine konurken ayni baytlar [0] IMPLICIT ile (0xA0) yer alir.
    """
    digest = hashlib.sha256(content).digest()

    # signingCertificateV2: SEQUENCE { certs SEQUENCE OF ESSCertIDv2 }
    # ESSCertIDv2 ::= SEQUENCE { hashAlgorithm DEFAULT sha256 (atlanir),
    #                            certHash OCTET STRING,
    #                            issuerSerial IssuerSerial OPTIONAL }
    cert_hash = hashlib.sha256(cert_der).digest()
    issuer_der, serial = cert_issuer_and_serial(cert_der)
    # IssuerSerial ::= SEQUENCE { issuer GeneralNames, serial INTEGER }
    # GeneralName directoryName = [4] EXPLICIT Name
    general_name = explicit(4, issuer_der)
    general_names = seq(general_name)
    issuer_serial = seq(general_names, integer(serial))
    # hashAlgorithm sha256 DEFAULT'tur. Gercek UYAP imzasi bu alani ATLIYOR
    # (DER'de DEFAULT degerler yazilmaz). Birebir uyum icin biz de atliyoruz;
    # boylece ESSCertIDv2 ::= SEQUENCE { certHash, issuerSerial }.
    ess_cert_id = seq(octet(cert_hash), issuer_serial)
    signing_cert_v2 = seq(seq(ess_cert_id))

    attrs = [
        _attr(OID_CONTENT_TYPE, OID_PKCS7_DATA),
        _attr(OID_SIGNING_TIME, utctime(signing_time)),
        _attr(OID_MESSAGE_DIGEST, octet(digest)),
        _attr(OID_SIGNING_CERT_V2, signing_cert_v2),
    ]
    # NOT: DER kurali SET OF uyelerinin siralanmasini ister, ANCAK gercek UYAP
    # imzasi oznitelikleri EKLEME SIRASINDA tutuyor (contentType, signingTime,
    # messageDigest, signingCertificateV2) ve kart bu baytlar uzerinden imzaliyor.
    # Imzanin dogrulanabilmesi icin imzalanan baytlar birebir ayni olmali, bu
    # yuzden siralamiyoruz; UYAP'in uygulama davranisini bilerek koruyoruz.
    return set_of(*attrs)


# ----------------------------------------------------------------------------
# CMS SignedData montaji
# ----------------------------------------------------------------------------

def build_cms(content: bytes,
              cert_der: bytes,
              signed_attrs_der_set: bytes,
              signature: bytes,
              use_ecdsa: bool = False) -> bytes:
    """
    Detached CMS SignedData DER uretir.
    signed_attrs_der_set: build_signed_attributes ciktisi (0x31 SET tag'li).
    signature: kartin, signed_attrs uzerinde urettigi ham imza baytlari.
    """
    # SignerInfo icindeki signedAttrs [0] IMPLICIT olur: ilk bayti 0x31 -> 0xA0
    implicit_attrs = b"\xA0" + signed_attrs_der_set[1:]

    sid_issuer_der, serial = cert_issuer_and_serial(cert_der)
    sid = seq(sid_issuer_der, integer(serial))      # IssuerAndSerialNumber

    sig_algid = algid(OID_ECDSA_SHA256, params=None) if use_ecdsa else algid(OID_SHA256_RSA)

    signer_info = seq(
        integer(1),                                  # version
        sid,
        digest_algid(),                           # digestAlgorithm
        implicit_attrs,                              # [0] signedAttrs
        sig_algid,                                   # signatureAlgorithm
        octet(signature),                            # signature
    )

    encap_content_info = seq(OID_PKCS7_DATA)         # DETACHED: eContent yok

    signed_data = seq(
        integer(1),                                  # version
        set_of(digest_algid()),                   # digestAlgorithms
        encap_content_info,
        explicit(0, cert_der),                       # [0] certificates (yalniz imzalayan)
        set_of(signer_info),                         # signerInfos
    )

    content_info = seq(OID_PKCS7_SIGNED, explicit(0, signed_data))
    return content_info


def to_be_signed(content: bytes, cert_der: bytes,
                 signing_time: datetime.datetime | None = None):
    """
    Karta gonderilecek 'imzalanacak veri'yi hazirlar.
    Doner: (tbs_bytes, signed_attrs_der_set, signing_time)
    Kart bu tbs_bytes'in SHA-256 ozeti uzerinde RSA/PKCS1 imza uretmelidir.
    """
    if signing_time is None:
        signing_time = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    sa = build_signed_attributes(content, cert_der, signing_time)
    # CMS kurali: imza, signedAttrs'in DER SET OF (0x31) kodlamasi uzerinden alinir.
    return sa, sa, signing_time
