# -*- coding: utf-8 -*-
"""
akis_pkcs11.py — PyKCS11 yerine geçen, bağımlılıksız ctypes PKCS#11 istemcisi.

NEDEN VAR?
    AKIS `akisp11.dll`, `C_Initialize`'ı YALNIZCA NULL pInitArgs ile kabul eder.
    Herhangi bir CK_C_INITIALIZE_ARGS yapısı (CKF_OS_LOCKING_OK dâhil) verildiğinde
    CKR_ARGUMENTS_BAD (0x07) döndürür. PyKCS11 (>=1.5) ise `load()` sırasında DAİMA
    OS-locking'li bir yapı gönderir; bu yüzden AKIS kartıyla yükleme/imzalama
    "CKR_ARGUMENTS_BAD" ile başarısız olur. Bu modül C_Initialize'ı doğrudan NULL
    ile çağırarak sorunu kökten çözer (etkilenen tek nokta kart başlatmadır).

UYUMLULUK
    Bu projede kullanılan PyKCS11 ALT KÜMESİYLE birebir uyumludur; tüketici dosyalar
    `import PyKCS11` yerine `import akis_pkcs11 as PyKCS11` yazarak DEĞİŞMEDEN çalışır:
      PyKCS11Lib().load(path) / getSlotList(tokenPresent=) / openSession(slot, flags)
      session.login(pin) / logout() / closeSession()
      session.findObjects([(CKA_CLASS, CKO_CERTIFICATE), ...])
      session.getAttributeValue(obj, [CKA_VALUE, CKA_ID, ...])
      session.sign(priv, data, Mechanism(CKM_..., None))
      sabitler: CKA_*, CKO_*, CKK_*, CKM_*, CKF_*  ve  PyKCS11Error
"""
from __future__ import annotations
import ctypes
from ctypes import c_void_p, c_ulong, c_ubyte, byref, cast

CK_ULONG = c_ulong                       # PKCS#11 CK_ULONG = platform 'unsigned long'
_ULONG_SIZE = ctypes.sizeof(CK_ULONG)
_INVALID_LEN = (1 << (8 * _ULONG_SIZE)) - 1   # C_GetAttributeValue'nun (CK_ULONG)-1 işareti

# ---- Sabitler (tüketicilerin kullandıkları) ----
CKR_OK               = 0x00000000
CKR_ALREADY_INIT     = 0x00000191
CKU_USER             = 0x00000001
CKF_SERIAL_SESSION   = 0x00000004
CKF_RW_SESSION       = 0x00000002
CKO_CERTIFICATE      = 0x00000001
CKO_PRIVATE_KEY      = 0x00000003
CKA_CLASS            = 0x00000000
CKA_LABEL            = 0x00000003
CKA_VALUE            = 0x00000011
CKA_CERTIFICATE_TYPE = 0x00000080
CKA_KEY_TYPE         = 0x00000100
CKA_ID               = 0x00000102
CKK_RSA              = 0x00000000
CKK_EC               = 0x00000003
CKK_ECDSA            = 0x00000003        # CKK_EC ile aynı değer (eski ad)
CKM_RSA_PKCS         = 0x00000001
CKM_ECDSA            = 0x00001041

# getAttributeValue'da CK_ULONG (int) olarak dönecek öznitelikler; geri kalan = bayt dizisi
_ULONG_ATTRS = {CKA_CLASS, CKA_KEY_TYPE, CKA_CERTIFICATE_TYPE}

# load()'da hazırlanacak fonksiyonlar (hepsi CK_RV = CK_ULONG döndürür)
_FUNCS = ("C_Initialize", "C_Finalize", "C_GetSlotList", "C_OpenSession",
          "C_CloseSession", "C_Login", "C_Logout", "C_FindObjectsInit",
          "C_FindObjects", "C_FindObjectsFinal", "C_GetAttributeValue",
          "C_SignInit", "C_Sign")


# DİKKAT: Windows Cryptoki başlıkları struct'ları `#pragma pack(push, cryptoki, 1)` ile
# 1-BAYT PAKETLER. Varsayılan ctypes hizalaması pValue'yu offset 8'e, ulValueLen'i offset
# 16'ya koyar (sizeof=24); akisp11.dll ise pValue'yu offset 4, ulValueLen'i offset 12 bekler
# (sizeof=16). Bu uyumsuzlukta DLL pValue/ulValueLen'i ÇÖP ofsetten okur/yazar: öznitelik
# okuma (C_GetAttributeValue len=0 döner) ve şablonlu arama (C_FindObjectsInit hiçbir şey
# eşleştirmez) bozulur → "Kartta sertifika bulunamadı". _pack_=1 hizalamayı DLL'e uydurur.
class CK_ATTRIBUTE(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("type", CK_ULONG), ("pValue", c_void_p), ("ulValueLen", CK_ULONG)]


class CK_MECHANISM(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("mechanism", CK_ULONG), ("pParameter", c_void_p), ("ulParameterLen", CK_ULONG)]


class PyKCS11Error(Exception):
    """PyKCS11.PyKCS11Error ile aynı rolü oynar (CKR_* hata kodunu taşır)."""
    def __init__(self, value, fn=""):
        self.value = value
        self.fn = fn
        super().__init__(str(self))

    def __str__(self):
        return "CKR 0x%08X%s" % (self.value & 0xFFFFFFFF, (" (%s)" % self.fn) if self.fn else "")


def _ck(fn, rv):
    if rv != CKR_OK:
        raise PyKCS11Error(rv, fn)


class Mechanism:
    """PyKCS11.Mechanism ikamesi (mekanizma türü taşır; bu profilde parametre yok)."""
    def __init__(self, mechanism, param=None):
        self.mechanism = mechanism
        self.param = param


def _encode_template(template):
    """[(type, value), ...] -> (CK_ATTRIBUTE dizisi, GC'den korunacak tamponlar).
    int değer -> CK_ULONG (CKA_CLASS gibi); bytes/list/tuple -> bayt dizisi (CKA_ID gibi)."""
    n = len(template)
    arr = (CK_ATTRIBUTE * n)()
    keep = []
    for i, (t, v) in enumerate(template):
        arr[i].type = t
        if v is None:
            arr[i].pValue = None
            arr[i].ulValueLen = 0
        elif isinstance(v, int):
            cv = CK_ULONG(v)
            keep.append(cv)
            arr[i].pValue = cast(byref(cv), c_void_p)
            arr[i].ulValueLen = _ULONG_SIZE
        else:
            vb = bytes(v)
            if vb:
                cb = (c_ubyte * len(vb)).from_buffer_copy(vb)
                keep.append(cb)
                arr[i].pValue = cast(cb, c_void_p)
            else:
                arr[i].pValue = None
            arr[i].ulValueLen = len(vb)
    return arr, keep


class Session:
    def __init__(self, lib, handle):
        self._dll = lib._dll
        self._h = CK_ULONG(handle)

    def login(self, pin, user_type=CKU_USER):
        pin_b = pin.encode("utf-8") if isinstance(pin, str) else bytes(pin)
        buf = (c_ubyte * len(pin_b)).from_buffer_copy(pin_b)
        _ck("C_Login", self._dll.C_Login(self._h, CK_ULONG(user_type),
                                         cast(buf, c_void_p), CK_ULONG(len(pin_b))))

    def logout(self):
        self._dll.C_Logout(self._h)

    def closeSession(self):
        self._dll.C_CloseSession(self._h)

    def findObjects(self, template=None):
        template = list(template or [])
        arr, _keep = _encode_template(template)
        _ck("C_FindObjectsInit", self._dll.C_FindObjectsInit(self._h, arr, CK_ULONG(len(template))))
        out = []
        try:
            batch = (CK_ULONG * 64)()
            cnt = CK_ULONG(0)
            while True:
                _ck("C_FindObjects", self._dll.C_FindObjects(self._h, batch, CK_ULONG(64), byref(cnt)))
                if not cnt.value:
                    break
                out.extend(int(batch[i]) for i in range(cnt.value))
                if cnt.value < 64:
                    break
        finally:
            try:
                self._dll.C_FindObjectsFinal(self._h)
            except Exception:
                pass
        return out

    def _get_one(self, obj, attr_type):
        a = (CK_ATTRIBUTE * 1)()
        a[0].type = attr_type
        a[0].pValue = None
        a[0].ulValueLen = 0
        _ck("C_GetAttributeValue", self._dll.C_GetAttributeValue(self._h, CK_ULONG(obj), a, CK_ULONG(1)))
        ln = int(a[0].ulValueLen)
        if ln == 0 or ln == _INVALID_LEN:
            return b""
        buf = (c_ubyte * ln)()
        a[0].pValue = cast(buf, c_void_p)
        _ck("C_GetAttributeValue", self._dll.C_GetAttributeValue(self._h, CK_ULONG(obj), a, CK_ULONG(1)))
        return bytes(buf[:int(a[0].ulValueLen)])

    def getAttributeValue(self, obj, types):
        """PyKCS11 ile aynı: istenen her tür için bir değer döndürür.
        CK_ULONG öznitelikleri int; diğerleri PyKCS11'deki gibi int LİSTESİ döner
        (bytes() ile çevrilebilir ve findObjects şablonuna geri verilebilir)."""
        result = []
        for t in types:
            raw = self._get_one(obj, t)
            if t in _ULONG_ATTRS:
                result.append(int.from_bytes(raw, "little") if raw else None)
            else:
                result.append(list(raw))
        return result

    def sign(self, key, data, mechanism):
        mech_type = mechanism.mechanism if isinstance(mechanism, Mechanism) else int(mechanism)
        mech = CK_MECHANISM(mech_type, None, 0)
        _ck("C_SignInit", self._dll.C_SignInit(self._h, byref(mech), CK_ULONG(key)))
        data_b = (c_ubyte * len(data)).from_buffer_copy(bytes(data))
        siglen = CK_ULONG(0)
        _ck("C_Sign", self._dll.C_Sign(self._h, data_b, CK_ULONG(len(data)), None, byref(siglen)))
        sig = (c_ubyte * siglen.value)()
        _ck("C_Sign", self._dll.C_Sign(self._h, data_b, CK_ULONG(len(data)), sig, byref(siglen)))
        return bytes(sig[:siglen.value])


class PyKCS11Lib:
    def __init__(self):
        self._dll = None

    def load(self, pkcs11dll_filename):
        try:
            self._dll = ctypes.CDLL(pkcs11dll_filename)
        except OSError as e:
            raise PyKCS11Error(-1, "DLL yuklenemedi: %s (%s)" % (pkcs11dll_filename, e))
        for name in _FUNCS:
            try:
                getattr(self._dll, name).restype = CK_ULONG
            except AttributeError:
                raise PyKCS11Error(-1, "PKCS#11 modulunde eksik fonksiyon: %s" % name)
        # AKIS YALNIZCA NULL kabul eder. Aynı süreçte ikinci yüklemede modül zaten
        # başlatılmış olabilir; ALREADY_INITIALIZED'i hoş görüyoruz.
        rv = self._dll.C_Initialize(None)
        if rv not in (CKR_OK, CKR_ALREADY_INIT):
            raise PyKCS11Error(rv, "C_Initialize")
        return self

    def getSlotList(self, tokenPresent=False):
        flag = 1 if tokenPresent else 0
        cnt = CK_ULONG(0)
        _ck("C_GetSlotList", self._dll.C_GetSlotList(flag, None, byref(cnt)))
        if not cnt.value:
            return []
        arr = (CK_ULONG * cnt.value)()
        _ck("C_GetSlotList", self._dll.C_GetSlotList(flag, arr, byref(cnt)))
        return [int(arr[i]) for i in range(cnt.value)]

    def openSession(self, slot, flags=CKF_SERIAL_SESSION):
        flags |= CKF_SERIAL_SESSION
        h = CK_ULONG(0)
        _ck("C_OpenSession", self._dll.C_OpenSession(
            CK_ULONG(slot), CK_ULONG(flags), None, None, byref(h)))
        return Session(self, h.value)
