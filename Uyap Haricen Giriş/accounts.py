#!/usr/bin/env python3
"""
Hesap / Lisans Deposu (accounts.py) — v2: Ofis + Kullanıcı (master/üye) modeli
------------------------------------------------------------------------------
Satıcı sunucusunun (vendor_server.py) kullandığı küçük, bağımlılıksız hesap deposu.

İKİ KATMANLI KİMLİK MODELİ
  • OFİS (office)  = bir lisans + bir tünel/oda. Bir e-imza, bir UYAP oturumu. Sabit bir
    iç kimliği (office_id) ve DÖNEN bir public jetonu (room_key) vardır.
  • KULLANICI (user) = bir kişi. Kendi KULLANICI ADI + PAROLASI ile giriş yapar; bir ofise
    (office_id) bağlıdır ve rolü vardır: "master" (ofis sahibi) ya da "member" (alt kullanıcı).

Neden böyle:
  • Giriş ARTIK oda anahtarıyla değil, KULLANICI ADI + PAROLA ile yapılır. Sunucu kullanıcıyı
    doğrular → ait olduğu ofisin GÜNCEL room_key'ini kendi çözer → tünele bağlar. Alt kullanıcı
    ham oda anahtarını hiç görmez/yazmaz.
  • room_key bir İÇ/DÖNEN jetondur: kimse yazmadığı için düzensiz (asimetrik) aralıklarla
    DÖNDÜRÜLEBİLİR (rotate_room_key); sızsa bile kısa sürede geçersizleşir. Giriş sonrası
    gerekirse kopyalanabilir (hızlı paylaşım linki vb.).
  • Master kendi üyelerini yönetir (ekle/sil/parola sıfırla/iptal); vendor yalnızca ofisi +
    master'ı oluşturur.

Tasarım notları
  • Parola pbkdf2-hmac-sha256 (stdlib) + hesap başına rastgele tuz; düz parola DİSKTE TUTULMAZ.
    Doğrulama sabit zamanlı.
  • Kullanıcı adı GLOBAL benzersizdir (girişte ofis ipucu gerekmesin diye). Güvenlik geçişinde
    yeniden değerlendirilecek.
  • Kalıcılık TAKILABİLİR: DATABASE_URL varsa PostgreSQL (uyap_kv tablosu, tek JSONB satırı
    k='accounts', artık TÜM dokümanı {offices,users} tutar), yoksa tek JSON dosyası (yerel test).
  • Bellek içi sözlük çalışma kopyasıdır; doğrulama bellekten okur (DB gecikmesi sıcak yola
    binmez). Yazımlar (oluştur/sıfırla/sil/döndür) seyrektir, her birinde depoya basılır.
"""

import os
import re
import json
import time
import base64
import hmac
import struct
import hashlib
import secrets

DATA_DIR = os.environ.get("UYAP_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")

# Ayarlıysa dosya yerine PostgreSQL kullanılır (Neon/Supabase bağlantı dizesi).
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_PBKDF2_ITERS = 200_000


# ──────────────────────────────────────────────────────────────────────────────────────
# Parola / jeton yardımcıları
# ──────────────────────────────────────────────────────────────────────────────────────
def _hash_password(password: str, salt: bytes = None, iters: int = _PBKDF2_ITERS) -> dict:
    """Parolayı tuzlu pbkdf2-hmac-sha256 ile özetler. Saklanabilir bir sözlük döndürür."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return {
        "algo": "pbkdf2_sha256",
        "iters": iters,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(dk).decode("ascii"),
    }


def _verify_password(password: str, rec: dict) -> bool:
    """Düz parolayı saklanan özetle sabit zamanlı karşılaştırır."""
    if not rec or rec.get("algo") != "pbkdf2_sha256":
        return False
    try:
        salt = base64.b64decode(rec["salt"])
        expected = base64.b64decode(rec["hash"])
        iters = int(rec.get("iters", _PBKDF2_ITERS))
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(dk, expected)


def generate_room_key() -> str:
    """Tahmin edilemez, URL/JSON güvenli bir oda anahtarı (dönen public jeton)."""
    return "uyap_" + secrets.token_urlsafe(18)


def generate_office_id() -> str:
    """Sabit, iç ofis kimliği. room_key dönse de bu DEĞİŞMEZ; users buna bağlanır."""
    return "off_" + secrets.token_urlsafe(9)


def generate_password() -> str:
    """İnsan-paylaşılabilir, makul güçte rastgele bir parola üretir."""
    return secrets.token_urlsafe(9)


def generate_order_id() -> str:
    """Satın alma talebi (sipariş) kimliği. Ödeme akışında referans olarak kullanılır."""
    return "ord_" + secrets.token_urlsafe(9)


# ──────────────────────────────────────────────────────────────────────────────────────
# TOTP (RFC 6238) — 'utku' owner hesabının iki-adımlı doğrulaması. Bağımlılıksız (stdlib):
# base32 secret + HMAC-SHA1 + zaman penceresi. İstemci tarafı standart authenticator
# uygulamalarıyla (Google/Microsoft Authenticator, Authy…) uyumludur.
# ──────────────────────────────────────────────────────────────────────────────────────
TOTP_STEP = 30      # sn: kod geçerlilik penceresi
TOTP_DIGITS = 6
TOTP_ISSUER = "UYAP"


def generate_totp_secret() -> str:
    """Yeni, tahmin edilemez bir base32 TOTP gizli anahtarı (20 bayt = 160 bit)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_at(secret_b32: str, counter: int) -> str:
    """Belirli bir zaman sayacı için TOTP kodunu üretir."""
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad, casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** TOTP_DIGITS)
    return str(code).zfill(TOTP_DIGITS)


def totp_verify(secret_b32: str, code: str, last_counter: int = -1):
    """TOTP kodunu doğrular (±1 pencere saat kaymasına tolerans). Sabit-zamanlı karşılaştırma
    + REPLAY koruması: kullanılan sayaç 'last_counter' olarak döner; çağıran saklayıp aynı ya
    da daha eski sayacı reddeder. (ok: bool, used_counter: int) döndürür."""
    code = (code or "").strip()
    if not secret_b32 or not code.isdigit() or len(code) != TOTP_DIGITS:
        return False, last_counter
    now = int(time.time()) // TOTP_STEP
    for counter in (now - 1, now, now + 1):
        if counter <= last_counter:
            continue  # replay: bu sayaç zaten kullanıldı
        if hmac.compare_digest(_totp_at(secret_b32, counter), code):
            return True, counter
    return False, last_counter


def totp_uri(secret_b32: str, account: str, issuer: str = TOTP_ISSUER) -> str:
    """Authenticator uygulamasına eklemek için standart otpauth:// URI'si (QR/manuel)."""
    from urllib.parse import quote
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret_b32}"
            f"&issuer={quote(issuer)}&digits={TOTP_DIGITS}&period={TOTP_STEP}")


# Telefon: yalnızca görsel/temizlik amaçlı normalize (KVKK/işletme verisi; kimlik değil).
def normalize_phone(phone: str) -> str:
    """Telefon numarasını sadeleştirir: baştaki '+' korunur, kalan yalnızca rakam. Boş → ''."""
    phone = (phone or "").strip()
    if not phone:
        return ""
    plus = phone.startswith("+")
    digits = re.sub(r"\D", "", phone)
    return ("+" if plus else "") + digits


# 'utku' owner (platform sahibi) hesabının çıplak kullanıcı adı. Ofis kodu GEREKMEZ; depoda
# doğrudan bu ad ('@' içermez) anahtarıyla saklanır. Ortamdan değiştirilebilir.
OWNER_USERNAME = (os.environ.get("UYAP_OWNER_USERNAME", "utku").strip() or "utku")


# Ofis kodu (slug) ve kapsamlı kullanıcı adı yardımcıları
# ----------------------------------------------------------------------------------------
# Kullanıcı adı ofis-kapsamlıdır: "kullanici@ofis_slug" (ör. "ahmet@kemalburo"). ofis_slug bir
# İŞLETME ADIDIR (kişisel veri değil → KVKK kapsamı dışında); e-posta DEĞİLDİR. Birleşik metin
# global benzersiz kaldığı için self.users sözlük anahtarı olarak doğrudan kullanılır.
_TR_MAP = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
    "Ç": "c", "Ğ": "g", "İ": "i", "I": "i", "Ö": "o", "Ş": "s", "Ü": "u",
})


def slugify(text: str) -> str:
    """İşletme adını güvenli bir ofis koduna indirger: yalnızca [a-z0-9-]. Türkçe karakterler
    ASCII'ye eşlenir. Boş/uygunsuz girdide '' döner (çağıran yedek üretir)."""
    s = (text or "").strip().translate(_TR_MAP).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def compose_username(office_code: str, bare_username: str) -> str:
    """Giriş ekranındaki iki alanı (ofis kodu + kullanıcı) tek depo anahtarına birleştirir:
    'ahmet' + 'kemalburo' → 'ahmet@kemalburo'. authenticate bu birleşik anahtarı bekler."""
    return f"{(bare_username or '').strip()}@{slugify(office_code)}"


# Kullanıcı/master'ın BELİRLEDİĞİ parolalar için politika (güvenlik raporu #8): en az 8 karakter
# + en az bir büyük harf, bir küçük harf, bir rakam ve bir özel işaret. Otomatik üretilen parolalar
# (generate_password → token_urlsafe(9) ≈ 12 karakter) zaten güçlüdür ve bu kontrole tabi DEĞİLDİR;
# yalnızca insanın seçtiği parolalara uygulanır. Uzunluk ortamdan ayarlanabilir; taban 8.
MIN_PASSWORD_LENGTH = max(8, int(os.environ.get("UYAP_MIN_PASSWORD_LEN", "8") or "8"))

# UI formlarında gösterilecek tek-satır politika özeti (form ipuçları buradan beslensin ki
# kural değişince metinler kendiliğinden güncellensin).
PASSWORD_POLICY_HINT = (f"en az {MIN_PASSWORD_LENGTH} karakter; en az bir büyük harf, "
                        f"bir küçük harf, bir rakam ve bir özel işaret")


def password_policy_error(password: str):
    """İnsan-seçimli parola politikayı sağlıyor mu? Sağlıyorsa None, değilse hata mesajı döndürür."""
    p = (password or "").strip()
    if len(p) < MIN_PASSWORD_LENGTH:
        return f"Parola en az {MIN_PASSWORD_LENGTH} karakter olmalı."
    if not any(c.islower() for c in p):
        return "Parola en az bir küçük harf içermeli."
    if not any(c.isupper() for c in p):
        return "Parola en az bir büyük harf içermeli."
    if not any(c.isdigit() for c in p):
        return "Parola en az bir rakam içermeli."
    if not any(not c.isalnum() for c in p):
        return "Parola en az bir özel işaret içermeli (ör. ! ? . - _ *)."
    return None


# Kullanıcı YOKKEN de PBKDF2 maliyetini ödeyip yanıt süresini eşitlemek için kukla kayıt.
# Böylece "kullanıcı yok" ile "parola yanlış" zamanlama (timing) üzerinden ayırt EDİLEMEZ.
_DUMMY_PW_REC = _hash_password(secrets.token_urlsafe(16))
# Giriş başarısızlıklarında DIŞARI verilen tek-tip mesaj (kullanıcı adı enumeration savunması).
_GENERIC_AUTH_FAIL = "Kullanıcı adı veya parola hatalı."


# ──────────────────────────────────────────────────────────────────────────────────────
# Kalıcılık arka uçları (backend). İkisi de TÜM dokümanı ({offices, users}) tek parça
# okur/yazar; küçük veri için yeterli.
# ──────────────────────────────────────────────────────────────────────────────────────
class _FileBackend:
    """Tek JSON dosyası (atomik yazım). Yerel geliştirme/test için."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, doc: dict):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)  # atomik


class _PostgresBackend:
    """PostgreSQL (Neon/Supabase). Tüm doküman `uyap_kv` tablosunda tek JSONB satırında
    (k='accounts') tutulur. Driver (psycopg2) yalnızca burada, tembel import edilir."""

    _KEY = "accounts"
    _TABLE = "uyap_kv"

    def __init__(self, dsn: str):
        import psycopg2  # lazy: yalnızca DB modunda gerekir
        self._psycopg2 = psycopg2
        self.dsn = dsn
        self._ensure_table()

    def _connect(self):
        return self._psycopg2.connect(self.dsn)

    def _ensure_table(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._TABLE} "
                    "(k TEXT PRIMARY KEY, v JSONB NOT NULL)")
            conn.commit()

    def load(self) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT v FROM {self._TABLE} WHERE k = %s", (self._KEY,))
                row = cur.fetchone()
        if not row or not row[0]:
            return {}
        v = row[0]  # psycopg2 JSONB'yi otomatik dict'e çevirir
        return v if isinstance(v, dict) else {}

    def save(self, doc: dict):
        payload = json.dumps({} if doc is None else doc, ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._TABLE} (k, v) VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                    (self._KEY, payload))
            conn.commit()


def _make_backend():
    if DATABASE_URL:
        print("[+] Hesap deposu: PostgreSQL (kalıcı).")
        return _PostgresBackend(DATABASE_URL)
    print(f"[!] Hesap deposu: DOSYA ({ACCOUNTS_PATH}). DATABASE_URL yok — PaaS'ta EFEMERAL olabilir!")
    return _FileBackend(ACCOUNTS_PATH)


class AccountError(Exception):
    """Hesap işlemi hatası (ör. kullanıcı adı dolu, ofis yok). Mesaj kullanıcıya gösterilebilir."""


class AccountStore:
    """Bellek içi ofis + kullanıcı tablosu (çalışma kopyası) + takılabilir kalıcı depo.

    offices: office_id -> {label, room_key, active, created, rotated}
    users:   username  -> {office_id, role, password, label, active, created}
    """

    def __init__(self, path: str = ACCOUNTS_PATH, backend=None):
        self.path = path
        self.backend = backend if backend is not None else _make_backend()
        self.offices = {}     # office_id -> kayıt
        self.users = {}       # username  -> kayıt
        self.orders = {}      # order_id  -> satın alma talebi (ödeme/provizyon akışı)
        self._room_index = {}  # room_key -> office_id (bellek içi indeks)
        self._slug_index = {}  # slug     -> office_id (bellek içi indeks)
        # Parola sıfırlama jetonları (kısa ömürlü, BELLEK İÇİ — kalıcı değil; sunucu yeniden
        # başlarsa bekleyen sıfırlama bağlantıları düşer, kullanıcı tekrar talep eder).
        self.reset_tokens = {}  # token -> {username, exp}
        self.load()

    # ── Kalıcılık ───────────────────────────────────────────────────────────────────
    def load(self):
        try:
            doc = self.backend.load() or {}
        except Exception as e:
            print(f"[!] Hesap deposu yüklenemedi: {e}")
            doc = {}
        # v1 (eski) format tespiti: top-level "accounts" vardı, "offices"/"users" yoktu.
        if "offices" not in doc and "users" not in doc and "accounts" in doc:
            print("[!] Eski (v1) hesap formatı bulundu; v2'ye geçiş için yeni ofis/kullanıcı "
                  "oluşturun. Eski kayıtlar yok sayıldı.")
            doc = {}
        self.offices = doc.get("offices", {}) if isinstance(doc, dict) else {}
        self.users = doc.get("users", {}) if isinstance(doc, dict) else {}
        self.orders = doc.get("orders", {}) if isinstance(doc, dict) else {}
        if self._backfill_slugs():
            # Slug'suz eski ofislere kod atandı → kalıcılaştır (bir kerelik geçiş).
            try:
                self.save()
            except Exception as e:
                print(f"[!] Slug geçişi kaydedilemedi: {e}")
        self._reindex()

    def _backfill_slugs(self) -> bool:
        """Slug alanı olmayan (eski) ofislere benzersiz bir ofis kodu atar. Değişiklik
        yapıldıysa True döner. Mevcut kullanıcı adları DEĞİŞTİRİLMEZ (girişleri bozulmasın)."""
        changed = False
        used = {o["slug"] for o in self.offices.values() if o.get("slug")}
        for oid, o in self.offices.items():
            if o.get("slug"):
                continue
            base = slugify(o.get("label", "")) or "ofis"
            slug = base
            i = 2
            while slug in used:
                slug = f"{base}-{i}"
                i += 1
            o["slug"] = slug
            used.add(slug)
            changed = True
        return changed

    def _reindex(self):
        self._room_index = {o["room_key"]: oid for oid, o in self.offices.items() if o.get("room_key")}
        self._slug_index = {o["slug"]: oid for oid, o in self.offices.items() if o.get("slug")}

    def _unique_slug(self, base_text: str) -> str:
        """base_text'ten benzersiz bir ofis kodu üretir (çakışmada -2, -3… ekler)."""
        base = slugify(base_text) or "ofis"
        used = {o.get("slug") for o in self.offices.values() if o.get("slug")}
        if base not in used:
            return base
        i = 2
        while f"{base}-{i}" in used:
            i += 1
        return f"{base}-{i}"

    def save(self):
        self.backend.save({"offices": self.offices, "users": self.users, "orders": self.orders})

    # ── Sorgular ────────────────────────────────────────────────────────────────────
    def is_empty(self) -> bool:
        """Hiç OFİS kullanıcısı yoksa True (signaling 'serbest/dev' moduna düşer). 'owner' (utku)
        bir platform-yönetim hesabıdır, tünele katılmaz → bu sayıma DAHİL EDİLMEZ; aksi halde
        yalnızca owner bootstrap'lanınca tünel açık-mod davranışı sessizce değişirdi."""
        return not any(r.get("role") != "owner" for r in self.users.values())

    def get_office(self, office_id: str) -> dict:
        return self.offices.get(office_id)

    def office_by_room_key(self, room_key: str) -> dict:
        oid = self._room_index.get(room_key)
        return self.offices.get(oid) if oid else None

    def office_by_slug(self, slug: str) -> dict:
        oid = self._slug_index.get(slugify(slug))
        return self.offices.get(oid) if oid else None

    # ── Ofis işlemleri (vendor /admin) ────────────────────────────────────────────────
    def create_office(self, label: str, master_username: str, master_password: str = None,
                      room_key: str = None, master_email: str = "", reset_to_master: bool = True,
                      slug: str = None, master_password_hash: dict = None, phone: str = "") -> dict:
        """Yeni ofis (lisans) + master kullanıcı oluşturur. Düz master parolasını (bir kez
        gösterilmek üzere), oda anahtarını, office_id'yi ve ofis kodunu (slug) döndürür.

        master_username: kullanıcının yazdığı ÇIPLAK ad (ör. 'ahmet'); '@' içeremez. Depoda
                         'ahmet@ofis_slug' olarak kapsamlı saklanır.
        slug: istenen ofis kodu (verilmezse label'dan türetilir); her hâlde benzersizleştirilir.
        master_email: master'ın e-postası (parola sıfırlama için).
        reset_to_master: ofis politikası — alt kullanıcının parola sıfırlama maili master'a (True)
                         mı yoksa kullanıcının kendisine (False) mı gitsin. Master sonra değiştirir.
        master_password_hash: ÖNCEDEN HASH'lenmiş parola kaydı (sipariş akışında kullanıcı üyelikte
                         parolasını seçer; düz metin hiç saklanmaz). Verilirse master_password yok
                         sayılır ve dönüşte password=None olur (kullanıcı parolasını zaten bilir).
        phone: master telefonu (opsiyonel, işletme iletişim verisi)."""
        master_username = (master_username or "").strip()
        if not master_username:
            raise AccountError("Master kullanıcı adı gerekli.")
        if "@" in master_username:
            raise AccountError("Kullanıcı adı '@' içeremez (e-posta değil, çıplak ad girin).")

        office_slug = self._unique_slug(slug or label or master_username)
        scoped_master = compose_username(office_slug, master_username)
        if scoped_master in self.users:
            raise AccountError(f"Kullanıcı adı dolu: {scoped_master}")

        office_id = generate_office_id()
        while office_id in self.offices:
            office_id = generate_office_id()
        if not room_key:
            room_key = generate_room_key()
        while room_key in self._room_index:
            room_key = generate_room_key()

        now = int(time.time())
        self.offices[office_id] = {
            "label": label or "",
            "slug": office_slug,
            "room_key": room_key,
            "active": True,
            "created": now,
            "rotated": now,
            "reset_to_master": bool(reset_to_master),
        }
        if master_password_hash:  # önceden hash'lenmiş (üyelik akışı) → düz parola üretilmez/gösterilmez
            pw_rec = master_password_hash
            plain_pw = None
        else:
            plain_pw = master_password or generate_password()
            if master_password:  # insan-seçimli parola → güç politikası (otomatik üretilen muaf)
                err = password_policy_error(plain_pw)
                if err:
                    raise AccountError(err)
            pw_rec = _hash_password(plain_pw)
        self.users[scoped_master] = {
            "office_id": office_id,
            "role": "master",
            "password": pw_rec,
            "label": label or "",
            "email": (master_email or "").strip(),
            "phone": normalize_phone(phone),
            "active": True,
            "created": now,
        }
        self._reindex()
        self.save()
        return {"office_id": office_id, "room_key": room_key, "office_code": office_slug,
                "master_username": scoped_master, "master_login": master_username,
                "password": plain_pw, "label": label or ""}

    def mark_agent_seen(self, office_id: str) -> None:
        """Ofis ajanı (sunucu rolü) buluşturmaya bağlandı ya da ayrıldı: ofis kaydına son
        görülme zamanını yazar. Web panosundaki "bu ofiste hiç sunucu çalıştı mı / en son
        ne zaman görüldü" sorusunun KALICI kaynağıdır (ROOMS bellek-içi ve uçucudur)."""
        o = self.offices.get(office_id)
        if not o:
            return
        o["agent_last_seen"] = int(time.time())
        self.save()

    def agent_last_seen(self, office_id: str):
        """Ofis ajanının buluşturmada en son görüldüğü zaman (epoch sn). Hiç bağlanmamışsa
        None — pano bu durumda 'programı indirin' akışına yönlendirir."""
        o = self.offices.get(office_id) or {}
        return o.get("agent_last_seen")

    def set_office_active(self, office_id: str, active: bool) -> bool:
        o = self.offices.get(office_id)
        if not o:
            return False
        o["active"] = bool(active)
        self.save()
        return True

    def delete_office(self, office_id: str) -> bool:
        """Ofisi ve ona bağlı TÜM kullanıcıları siler."""
        if office_id not in self.offices:
            return False
        del self.offices[office_id]
        for uname in [u for u, rec in self.users.items() if rec.get("office_id") == office_id]:
            del self.users[uname]
        self._reindex()
        self.save()
        return True

    def rotate_room_key(self, office_id: str) -> str:
        """Ofisin public oda anahtarını yeni, tahmin edilemez bir jetona DÖNDÜRÜR. Kullanıcılar
        kullanıcı adıyla giriş yaptığından bu işlem onları ETKİLEMEZ. Yeni room_key döner."""
        o = self.offices.get(office_id)
        if not o:
            return None
        new_key = generate_room_key()
        while new_key in self._room_index:
            new_key = generate_room_key()
        o["room_key"] = new_key
        o["rotated"] = int(time.time())
        self._reindex()
        self.save()
        return new_key

    # ── Siparişler (satın alma / ödeme akışı — Kalem 4) ───────────────────────────────
    # Sipariş, bir ödemenin ONAYLANMASINI bekleyen bir satın alma talebidir. Ofisi HENÜZ
    # oluşturmaz; ödeme onaylanınca (webhook ya da admin butonu) çağıran create_office()'i
    # çalıştırıp update_order(...) ile siparişi "provisioned" işaretler. Parola BURADA
    # TUTULMAZ (create_office düz parolayı bir kez döndürür, teslim çağıran katmanda yapılır).
    def create_order(self, label: str, master_username: str, email: str = "", plan: str = "",
                     amount: str = "", provider: str = "manual", phone: str = "",
                     password: str = None, slug: str = None) -> dict:
        """Bekleyen bir satın alma talebi kaydeder ve kaydı döndürür.

        Üyelik akışı: kullanıcı üyelikte ofis adını (slug), telefonunu ve PAROLASINI seçer. Parola
        DÜZ METİN SAKLANMAZ — hemen hash'lenip 'password_hash' olarak tutulur; provision'da doğrudan
        master kaydına yazılır (kullanıcı ilk girişte kendi parolasını kullanır). Slug/kullanıcı adı
        çakışması ÇAĞIRAN tarafından (mevcut ofisler + bekleyen siparişler) ayrıca kontrol edilir;
        burada yalnız temel doğrulama yapılır."""
        master_username = (master_username or "").strip()
        if not master_username:
            raise AccountError("Kullanıcı adı gerekli.")
        if "@" in master_username:
            raise AccountError("Kullanıcı adı '@' içeremez (e-posta değil, çıplak ad girin).")
        pw_hash = None
        if password:
            err = password_policy_error(password)
            if err:
                raise AccountError(err)
            pw_hash = _hash_password(password)
        oid = generate_order_id()
        while oid in self.orders:
            oid = generate_order_id()
        now = int(time.time())
        rec = {
            "order_id": oid,
            "status": "pending",
            "label": (label or "").strip(),
            "master_username": master_username,
            "slug": (slugify(slug) if slug else "") or "",
            "email": (email or "").strip(),
            "phone": normalize_phone(phone),
            "password_hash": pw_hash,  # None ise provision'da otomatik parola üretilir
            "plan": (plan or "").strip(),
            "amount": (str(amount or "")).strip(),
            "provider": (provider or "manual").strip(),
            "created": now,
            "updated": now,
            "office_id": "",
            "office_code": "",
        }
        self.orders[oid] = rec
        self.save()
        return rec

    def slug_taken(self, slug: str) -> bool:
        """Ofis kodu (slug) mevcut bir ofiste YA DA bekleyen bir siparişte kullanılıyor mu?
        Üyelikte ofis adı çakışmasını erken yakalamak için (provision'da tekrar denetlenir)."""
        s = slugify(slug)
        if not s:
            return False
        if s in self._slug_index:
            return True
        for o in self.orders.values():
            if o.get("status") == "pending" and slugify(o.get("slug") or o.get("label") or "") == s:
                return True
        return False

    def get_order(self, order_id: str) -> dict:
        return self.orders.get((order_id or "").strip())

    def update_order(self, order_id: str, **fields) -> dict:
        rec = self.orders.get((order_id or "").strip())
        if not rec:
            return None
        rec.update(fields)
        rec["updated"] = int(time.time())
        self.save()
        return rec

    def delete_order(self, order_id: str) -> bool:
        if (order_id or "").strip() not in self.orders:
            return False
        del self.orders[(order_id or "").strip()]
        self.save()
        return True

    def listing_orders(self):
        """En yeni önce sıralı sipariş listesi (admin paneli için)."""
        return sorted(self.orders.values(), key=lambda r: r.get("created", 0), reverse=True)

    # ── Kullanıcı işlemleri (master /ofis paneli + desktop sekmesi) ────────────────────
    def create_user(self, office_id: str, username: str, password: str = None,
                    role: str = "member", label: str = "", email: str = "", phone: str = "") -> dict:
        """Bir ofise yeni kullanıcı ekler. username ÇIPLAK ad ('ahmet'); '@' içeremez. Depoda
        'ahmet@ofis_slug' olarak kapsamlı saklanır. Düz parolayı (bir kez gösterilmek üzere),
        kapsamlı kullanıcı adını ve ofis kodunu döndürür."""
        username = (username or "").strip()
        if not username:
            raise AccountError("Kullanıcı adı gerekli.")
        if "@" in username:
            raise AccountError("Kullanıcı adı '@' içeremez (e-posta değil, çıplak ad girin).")
        office = self.offices.get(office_id)
        if not office:
            raise AccountError("Ofis bulunamadı.")
        office_slug = office.get("slug")
        if not office_slug:  # eski/slug'suz ofis → anında ata
            office_slug = self._unique_slug(office.get("label") or office_id)
            office["slug"] = office_slug
            self._reindex()
        scoped = compose_username(office_slug, username)
        if scoped in self.users:
            raise AccountError(f"Kullanıcı adı dolu: {scoped}")
        if role not in ("master", "member"):
            role = "member"
        plain_pw = password or generate_password()
        if password:  # insan-seçimli parola → güç politikası (otomatik üretilen muaf)
            err = password_policy_error(plain_pw)
            if err:
                raise AccountError(err)
        self.users[scoped] = {
            "office_id": office_id,
            "role": role,
            "password": _hash_password(plain_pw),
            "label": label or "",
            "email": (email or "").strip(),
            "phone": normalize_phone(phone),
            "active": True,
            "created": int(time.time()),
        }
        self.save()
        return {"username": scoped, "login": username, "office_code": office_slug,
                "password": plain_pw, "role": role}

    def set_user_email(self, username: str, email: str) -> bool:
        u = self.users.get(username)
        if not u:
            return False
        u["email"] = (email or "").strip()
        self.save()
        return True

    def set_office_reset_policy(self, office_id: str, reset_to_master: bool) -> bool:
        """Ofis politikası: alt kullanıcı parola sıfırlama maili master'a mı (True) yoksa
        kullanıcının kendisine mi (False) gitsin. Yalnızca master değiştirir."""
        o = self.offices.get(office_id)
        if not o:
            return False
        o["reset_to_master"] = bool(reset_to_master)
        self.save()
        return True

    def set_user_active(self, username: str, active: bool) -> bool:
        u = self.users.get(username)
        if not u:
            return False
        if u.get("role") == "owner":
            raise AccountError("Owner (platform sahibi) hesabı pasifleştirilemez.")
        u["active"] = bool(active)
        self.save()
        return True

    def bump_sess_epoch(self, username: str) -> None:
        """Kullanıcının oturum epoch'unu artırır: bu andan önce verilmiş TÜM oturum çerezleri
        (parola değişimi VE logout dahil, bulgu #14) artık geçersiz sayılır. Kaydeder."""
        u = self.users.get(username)
        if not u:
            return
        u["sess_epoch"] = u.get("sess_epoch", 0) + 1
        self.save()

    def reset_user_password(self, username: str, password: str = None) -> str:
        u = self.users.get(username)
        if not u:
            return None
        plain_pw = password or generate_password()
        if password:  # insan-seçimli parola → güç politikası (otomatik üretilen muaf)
            err = password_policy_error(plain_pw)
            if err:
                raise AccountError(err)
        u["password"] = _hash_password(plain_pw)
        # Parola değişince önceden verilmiş oturum çerezleri geçersiz olsun (bulgu #14):
        # session token'a gömülen epoch artık eşleşmiyor → çalınmış eski çerez işe yaramaz.
        u["sess_epoch"] = u.get("sess_epoch", 0) + 1
        self.save()
        return plain_pw

    def delete_user(self, username: str) -> bool:
        if username in self.users:
            # Son master'ı silmeye izin verme (ofis yönetilemez kalmasın).
            u = self.users[username]
            if u.get("role") == "owner":
                raise AccountError("Owner (platform sahibi) hesabı silinemez.")
            if u.get("role") == "master":
                oid = u.get("office_id")
                masters = [n for n, r in self.users.items()
                           if r.get("office_id") == oid and r.get("role") == "master"]
                if len(masters) <= 1:
                    raise AccountError("Ofisin tek master'ı silinemez.")
            del self.users[username]
            self.save()
            return True
        return False

    # ── Owner (platform sahibi 'utku') — env'den bootstrap, TOTP 2FA ────────────────────
    def get_owner(self, username: str = None) -> dict:
        """Owner kaydını döndürür (yoksa None). username verilmezse OWNER_USERNAME kullanılır."""
        return self.users.get((username or OWNER_USERNAME))

    def ensure_owner(self, password: str, username: str = None, totp_secret: str = None):
        """Owner hesabı yoksa oluşturur (env bootstrap). Owner: ofis kodu gerekmez ('@'siz çıplak
        ad), tüm platform yetkisine sahip, tünele katılmaz. Parola güç politikasına tabidir; düz
        metin SAKLANMAZ. Yeni oluşturulduysa {username, totp_secret, created:True} döner; zaten
        varsa {username, created:False} döner (mevcut secret KORUNUR, sızdırılmaz)."""
        username = (username or OWNER_USERNAME).strip()
        if "@" in username:
            raise AccountError("Owner kullanıcı adı '@' içeremez.")
        if username in self.users:
            u = self.users[username]
            if u.get("role") != "owner":
                raise AccountError(f"'{username}' zaten owner olmayan bir hesap; owner bootstrap iptal.")
            return {"username": username, "created": False}
        err = password_policy_error(password or "")
        if err:
            raise AccountError(err)
        secret = totp_secret or generate_totp_secret()
        self.users[username] = {
            "office_id": "",           # owner bir ofise bağlı DEĞİL
            "role": "owner",
            "password": _hash_password(password),
            "label": "Platform Sahibi",
            "email": "",
            "phone": "",
            "totp_secret": secret,
            "totp_last": -1,           # replay koruması (son kullanılan TOTP sayacı)
            "active": True,
            "created": int(time.time()),
        }
        self.save()
        return {"username": username, "totp_secret": secret, "created": True}

    def sync_owner_password(self, username: str, password: str) -> bool:
        """Owner parolasını env'deki değerle EŞİTLER (açılış bootstrap'ı; env = kaynak-doğruluk).
        Parola zaten uyuşuyorsa dokunmaz (False), güncellediyse True döndürür. Politika uygulanır."""
        u = self.users.get(username)
        if not u or u.get("role") != "owner":
            return False
        if _verify_password(password or "", u.get("password") or {}):
            return False  # zaten aynı
        err = password_policy_error(password or "")
        if err:
            raise AccountError(err)
        u["password"] = _hash_password(password)
        # Bkz. reset_user_password: parola değişince eski oturum çerezleri geçersiz olsun (bulgu #14).
        u["sess_epoch"] = u.get("sess_epoch", 0) + 1
        self.save()
        return True

    def reactivate_owner(self, username: str) -> bool:
        """Owner hesabı pasifse AKTİF eder (açılış bootstrap'ı; env = kaynak-doğruluk).
        Pasif owner kendini panelden düzeltemez → tek kurtarma yolu bu. True = aktifleştirildi."""
        u = self.users.get(username)
        if not u or u.get("role") != "owner":
            return False
        if u.get("active", True):
            return False
        u["active"] = True
        self.save()
        return True

    def set_owner_totp_secret(self, username: str, secret_b32: str) -> bool:
        """Owner'ın TOTP gizli anahtarını DEĞİŞTİRİR (authenticator kaydı kaybolduğunda kurtarma;
        açılışta UYAP_OWNER_TOTP_SECRET env'i ile uygulanır). Replay sayacı sıfırlanır."""
        u = self.users.get(username)
        if not u or u.get("role") != "owner":
            return False
        s = (secret_b32 or "").strip().replace(" ", "").upper()
        try:
            if len(base64.b32decode(s + "=" * (-len(s) % 8))) < 10:
                raise ValueError
        except Exception:
            raise AccountError("TOTP anahtarı geçerli base32 değil ya da çok kısa (en az 16 karakter).")
        u["totp_secret"] = s
        u["totp_last"] = -1
        self.save()
        return True

    def set_owner_totp_last(self, username: str, counter: int) -> bool:
        """Owner'ın son kullanılan TOTP sayacını kalıcılaştırır (replay koruması)."""
        u = self.users.get(username)
        if not u or u.get("role") != "owner":
            return False
        u["totp_last"] = int(counter)
        self.save()
        return True

    def verify_owner_totp(self, username: str, code: str):
        """Owner TOTP kodunu doğrular + kabul edilen sayacı kalıcılaştırır (replay). ok döndürür."""
        u = self.users.get(username)
        if not u or u.get("role") != "owner":
            return False
        ok, used = totp_verify(u.get("totp_secret", ""), code, int(u.get("totp_last", -1)))
        if ok:
            self.set_owner_totp_last(username, used)
        return ok

    # ── Doğrulama (signaling) ─────────────────────────────────────────────────────────
    def authenticate(self, username: str, password: str):
        """Kullanıcı adı + parola ile giriş doğrular ve kullanıcının ofisini çözer.
        (ok: bool, reason: str, info: dict|None) döndürür. info: office_id, room_key, role…"""
        u = self.users.get((username or "").strip())
        # Parola doğrulamasını HER ZAMAN çalıştır: kullanıcı yoksa kukla kayda karşı. Böylece
        # ne yanıt SÜRESİ ne de MESAJ "kullanıcı yok" ile "parola yanlış"ı ayırır (enumeration +
        # timing savunması). Mevcut/yanlış parola ayrımı tek-tip _GENERIC_AUTH_FAIL ile gizlenir.
        rec = (u.get("password") if u else None) or _DUMMY_PW_REC
        pw_ok = _verify_password(password or "", rec)
        if not u or not pw_ok:
            return False, _GENERIC_AUTH_FAIL, None
        # Buradan sonrası: parola DOĞRU → hesap/ofis durumu yalnızca meşru sahibe açıklanır
        # (bu mesajları görmek için zaten geçerli parola gerekir → enumeration değil).
        if not u.get("active", True):
            return False, "Kullanıcı pasif (askıda/iptal).", None
        # Owner (utku): ofise bağlı değildir, tünele katılmaz. Parola doğru → owner bilgisi döner
        # (TOTP ayrı adımda vendor katmanında doğrulanır). room_key/office_id boştur.
        if u.get("role") == "owner":
            return True, "Başarılı", {
                "username": username, "office_id": "", "room_key": "",
                "role": "owner", "office_label": "", "office_code": "",
            }
        office = self.offices.get(u.get("office_id"))
        if not office:
            return False, "Bağlı ofis bulunamadı.", None
        if not office.get("active", True):
            return False, "Ofis lisansı pasif.", None
        return True, "Başarılı", {
            "username": username,
            "office_id": u["office_id"],
            "room_key": office["room_key"],
            "role": u.get("role", "member"),
            "office_label": office.get("label", ""),
            "office_code": office.get("slug", ""),
        }

    # ── Parola sıfırlama (jeton tabanlı; e-posta GÖNDERİMİ vendor_server'da, şimdilik STUB) ──
    def request_password_reset(self, identifier: str):
        """Kullanıcı adı VEYA e-posta ile sıfırlama talebi başlatır. Ofis politikasına göre
        ALICI'yı (master ya da kullanıcının kendisi) çözer, kısa ömürlü bir jeton üretir.
        (info, error) döndürür. info: token, target_username, recipient_email, recipient_username,
        to_master. Asıl mail GÖNDERİMİ çağıran katmanda (vendor_server) yapılır."""
        identifier = (identifier or "").strip()
        if not identifier:
            return None, "Kullanıcı adı veya e-posta gerekli."
        uname, user = None, self.users.get(identifier)
        if user:
            uname = identifier
        else:
            for n, r in self.users.items():
                if (r.get("email") or "").strip().lower() == identifier.lower():
                    uname, user = n, r
                    break
        if not user:
            return None, "Kullanıcı bulunamadı."

        office = self.offices.get(user.get("office_id")) or {}
        to_master = office.get("reset_to_master", True)
        if to_master:
            master_name = next((n for n, r in self.users.items()
                                if r.get("office_id") == user.get("office_id")
                                and r.get("role") == "master"), None)
            master = self.users.get(master_name) if master_name else None
            recipient_email = (master.get("email") if master else "") or ""
            recipient_username = master_name or ""
        else:
            recipient_email = user.get("email") or ""
            recipient_username = uname

        if not recipient_email:
            return None, "Alıcı e-posta adresi tanımlı değil (yöneticiden e-posta eklemesini isteyin)."

        token = secrets.token_urlsafe(24)
        self.reset_tokens[token] = {"username": uname, "exp": int(time.time()) + 3600}  # 1 saat
        return {"token": token, "target_username": uname,
                "recipient_email": recipient_email, "recipient_username": recipient_username,
                "to_master": bool(to_master)}, None

    def peek_reset_token(self, token: str):
        """Jeton geçerliyse hedef kullanıcı adını döndürür (formu göstermeden önce doğrulama)."""
        rec = self.reset_tokens.get((token or "").strip())
        if not rec or rec.get("exp", 0) < int(time.time()):
            return None
        return rec.get("username")

    def reset_password_with_token(self, token: str, new_password: str):
        """Geçerli jetonla parolayı belirler. (ok: bool, reason_or_username: str) döndürür."""
        token = (token or "").strip()
        rec = self.reset_tokens.get(token)
        if not rec or rec.get("exp", 0) < int(time.time()):
            self.reset_tokens.pop(token, None)
            return False, "Sıfırlama bağlantısı geçersiz ya da süresi dolmuş."
        new_password = (new_password or "").strip()
        err = password_policy_error(new_password)
        if err:
            return False, err
        uname = rec.get("username")
        if uname not in self.users:
            self.reset_tokens.pop(token, None)
            return False, "Kullanıcı bulunamadı."
        self.users[uname]["password"] = _hash_password(new_password)
        # Bkz. reset_user_password: parola değişince eski oturum çerezleri geçersiz olsun (bulgu #14).
        self.users[uname]["sess_epoch"] = self.users[uname].get("sess_epoch", 0) + 1
        self.reset_tokens.pop(token, None)
        self.save()
        return True, uname

    # ── Listeler (yönetim arayüzleri) ─────────────────────────────────────────────────
    def listing_offices(self):
        out = []
        for oid, o in sorted(self.offices.items(), key=lambda kv: kv[1].get("created", 0), reverse=True):
            members = [u for u, r in self.users.items() if r.get("office_id") == oid]
            master = next((u for u, r in self.users.items()
                           if r.get("office_id") == oid and r.get("role") == "master"), "")
            master_email = (self.users.get(master, {}) or {}).get("email", "") if master else ""
            out.append({
                "office_id": oid,
                "label": o.get("label", ""),
                "slug": o.get("slug", ""),
                "room_key": o.get("room_key", ""),
                "master_username": master,
                "master_email": master_email,
                "reset_to_master": o.get("reset_to_master", True),
                "active": o.get("active", True),
                "created": o.get("created", 0),
                "rotated": o.get("rotated", 0),
                "user_count": len(members),
            })
        return out

    def listing_users(self, office_id: str):
        out = []
        for uname, r in sorted(self.users.items(), key=lambda kv: kv[1].get("created", 0), reverse=True):
            if r.get("office_id") != office_id:
                continue
            out.append({
                "username": uname,
                "label": r.get("label", ""),
                "email": r.get("email", ""),
                "phone": r.get("phone", ""),
                "role": r.get("role", "member"),
                "active": r.get("active", True),
                "created": r.get("created", 0),
            })
        return out
