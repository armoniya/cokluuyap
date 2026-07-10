import os
import zipfile
import hashlib
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ec
from cryptography.hazmat.primitives import hashes

def parse_der_with_raw(data, offset=0):
    start = offset
    if offset >= len(data):
        return None
    tag = data[offset]
    offset += 1
    
    if offset >= len(data):
        return None
    length_byte = data[offset]
    offset += 1
    if length_byte & 0x80:
        num_len_bytes = length_byte & 0x7F
        if offset + num_len_bytes > len(data):
            return None
        length = int.from_bytes(data[offset:offset+num_len_bytes], "big")
        offset += num_len_bytes
    else:
        length = length_byte
        
    if offset + length > len(data):
        length = len(data) - offset
    value_bytes = data[offset:offset+length]
    offset += length
    
    is_constructed = bool(tag & 0x20)
    if is_constructed and tag not in (0x04, 0x03):
        value = []
        child_offset = 0
        while child_offset < len(value_bytes):
            child = parse_der_with_raw(value_bytes, child_offset)
            if child is None:
                break
            value.append(child)
            child_offset += child[4]
    else:
        value = value_bytes
        
    raw_bytes = data[start:offset]
    return (tag, value, raw_bytes, value_bytes, offset - start)

def find_signer_info(node):
    tag, val, _, _, _ = node
    if tag == 0x30 and isinstance(val, list) and len(val) >= 6:
        if val[3][0] == 0xA0:
            return val
    if isinstance(val, list):
        for child in val:
            res = find_signer_info(child)
            if res is not None:
                return res
    return None

def get_oid_string(val_bytes):
    if not val_bytes:
        return ""
    parts = []
    first = val_bytes[0]
    parts.append(str(first // 40))
    parts.append(str(first % 40))
    
    i = 1
    while i < len(val_bytes):
        val = 0
        while i < len(val_bytes):
            b = val_bytes[i]
            i += 1
            val = (val << 7) | (b & 0x7F)
            if not (b & 0x80):
                break
        parts.append(str(val))
    return ".".join(parts)

def parse_attributes(signed_attrs_node):
    attrs = {}
    for attr in signed_attrs_node[1]:
        if attr[0] == 0x30 and isinstance(attr[1], list) and len(attr[1]) >= 2:
            oid_node = attr[1][0]
            val_set = attr[1][1]
            oid_str = get_oid_string(oid_node[3])
            
            if val_set[0] == 0x31 and isinstance(val_set[1], list) and len(val_set[1]) > 0:
                attrs[oid_str] = val_set[1][0][3]
    return attrs

def verify_udf_log(udf_path):
    """
    Verifies the UDF package.
    Returns (success: bool, logs: list of strings)
    """
    logs = []
    def log(msg):
        logs.append(msg)

    log(f"=== UDF Doğrulama Başlatıldı: {os.path.basename(udf_path)} ===")
    
    if not zipfile.is_zipfile(udf_path):
        log("HATA: Belirtilen dosya geçerli bir ZIP/UDF dosyası değil.")
        return False, logs
        
    try:
        with zipfile.ZipFile(udf_path, "r") as z:
            filenames = z.namelist()
            if "content.xml" not in filenames or "sign.sgn" not in filenames:
                log("HATA: UDF paketinde content.xml veya sign.sgn bulunamadı.")
                return False, logs
            content_bytes = z.read("content.xml")
            sgn_bytes = z.read("sign.sgn")
    except Exception as e:
        log(f"HATA: UDF paketi okunurken hata: {str(e)}")
        return False, logs
        
    log("OK: content.xml ve sign.sgn başarıyla UDF dosyasından çıkarıldı.")
    
    # Load signer certificate
    try:
        certs = pkcs7.load_der_pkcs7_certificates(sgn_bytes)
        if not certs:
            log("HATA: sign.sgn içerisinde imzalayan sertifikası bulunamadı.")
            return False, logs
        cert = certs[0]
        log(f"OK: Imzalayan Sertifikası Alındı:")
        log(f"  Konu (Subject): {cert.subject.rfc4514_string()}")
        log(f"  Seri Numarası : {cert.serial_number}")
    except Exception as e:
        log(f"HATA: Sertifika ayrıştırılamadı: {e}")
        return False, logs
        
    # Parse ASN.1 structure
    root = parse_der_with_raw(sgn_bytes)
    if not root:
        log("HATA: sign.sgn DER ayrıştırması başarısız oldu.")
        return False, logs
        
    signer_info = find_signer_info(root)
    if not signer_info:
        log("HATA: SignerInfo yapısı bulunamadı (Detached veya CAdES-BES uyumsuz).")
        return False, logs
        
    # Get signedAttrs and signature
    signed_attrs_node = signer_info[3]
    sig_alg_node = signer_info[4]
    signature_node = signer_info[5]
    
    tbs_bytes = b"\x31" + signed_attrs_node[2][1:]
    signature_bytes = signature_node[3]
    
    # Determine algorithm
    sig_alg_oid = get_oid_string(sig_alg_node[1][0][3])
    log(f"Imza Algoritma OID: {sig_alg_oid}")
    
    attrs = parse_attributes(signed_attrs_node)
    
    # Message Digest Verification
    msg_digest_oid = "1.2.840.113549.1.9.4"
    if msg_digest_oid not in attrs:
        log("HATA: signedAttrs içerisinde messageDigest bulunamadı.")
        return False, logs
        
    calculated_digest = hashlib.sha256(content_bytes).digest()
    embedded_digest = attrs[msg_digest_oid]
    
    if len(embedded_digest) > 32 and embedded_digest[0] == 0x04:
        emb_len = embedded_digest[1]
        embedded_digest = embedded_digest[2:2+emb_len]
        
    if calculated_digest != embedded_digest:
        log("HATA: content.xml özeti (hash) ile imzalı özet uyuşmuyor!")
        log(f"  Hesaplanan SHA256: {calculated_digest.hex().upper()}")
        log(f"  Imzalanan SHA256 : {embedded_digest.hex().upper()}")
        return False, logs
    log("OK: content.xml özeti (hash) imzalı messageDigest ile eşleşiyor.")
    
    # Cryptographic signature verification
    pub_key = cert.public_key()
    try:
        if isinstance(pub_key, rsa.RSAPublicKey):
            pub_key.verify(
                signature_bytes,
                tbs_bytes,
                padding.PKCS1v15(),
                hashes.SHA256()
            )
        elif isinstance(pub_key, ec.EllipticCurvePublicKey):
            pub_key.verify(
                signature_bytes,
                tbs_bytes,
                ec.ECDSA(hashes.SHA256())
            )
        else:
            log("HATA: Desteklenmeyen anahtar tipi.")
            return False, logs
        log("OK: Kriptografik imza doğrulaması BAŞARILI!")
    except Exception as e:
        log(f"HATA: Imza doğrulaması başarısız: {e}")
        return False, logs
        
    log("TEBRİKLER: UDF belgesi kriptografik ve yapısal olarak tamamen GEÇERLİ.")
    return True, logs
