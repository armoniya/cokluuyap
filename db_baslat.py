# -*- coding: utf-8 -*-
"""
Gömülü (portable) PostgreSQL başlatıcı — program açılışında DB'yi hazır eder.
============================================================================
Hiçbir kurulum/servis/admin gerektirmez. Tüm parçalar proje klasöründe durur:

    <kök>/pgsql    -> PostgreSQL 16 binaries (initdb, pg_ctl, postgres, psql...)
    <kök>/pgdata   -> veri kümesi (cluster) — ilk açılışta otomatik oluşturulur
    <kök>/models   -> Django (manage.py migrate buradan koşar)

ensure_db() çağrısı her açılışta GÜVENLE tekrar çalıştırılabilir:
  1. pgdata yoksa  -> initdb (UTF-8, scram-sha-256 auth: rastgele üretilen parola,
     bkz. %LOCALAPPDATA%/UyapIcra/dbpass.secret; aksi halde bu makinedeki HERHANGİ
     BİR kullanıcı/süreç parolasız bağlanıp dava verilerini okuyabilirdi)
  2. 5432 dinlenmiyorsa -> pg_ctl start (arka planda, penceresiz)
  3. eski 'trust' modda kurulmuş bir veri kümesi varsa -> veri kaybı olmadan
     parolalı erişime yükseltilir (guvenlik_yukselt)
  4. uyap_icra veritabanı yoksa -> oluştur
  5. Django migrate (şema güncel değilse uygular)

Dönüş: True (DB hazır) / False (bir adım başarısız — çağıran canlı UYAP'a düşebilir).
Tek başına da çalışır:  python db_baslat.py
"""
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time

# Türkçe Windows konsolu (cp1254) emoji/✓/⛔ gibi karakterleri yazamaz; çökmesin.
for _akis in (sys.stdout, sys.stderr):
    try:
        _akis.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

KOK = os.path.dirname(os.path.abspath(__file__))

# ÖNEMLİ: PostgreSQL'in C binary'leri ASCII-DIŞI yol (ör. "Kararlı"daki "ı" =
# cp1254 0xFD) ile bootstrap'ta çöker ("invalid byte sequence for UTF8 0xfd").
# Bu yüzden binary + veri kümesi proje klasöründe DEĞİL, garanti-ASCII olan
# %LOCALAPPDATA%\UyapIcra altında tutulur. Django/Python tarafı (MODELS_DIR)
# unicode yolu sorunsuz işler, orada kalır.
DB_BASE = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "UyapIcra"
)
PG_HOME = os.path.join(DB_BASE, "pgsql")
PG_BIN = os.path.join(PG_HOME, "bin")
PGDATA = os.path.join(DB_BASE, "pgdata")
MODELS_DIR = os.path.join(KOK, "models")
LOG_DOSYA = os.path.join(PGDATA, "server.log")
# 'postgres' kullanıcısının parolası ilk açılışta üretilip buraya yazılır (bkz. _db_parolasi).
# Aynı Windows makinesindeki başka bir kullanıcı/süreç artık parolasız (trust) bağlanamaz.
PAROLA_DOSYA = os.path.join(DB_BASE, "dbpass.secret")

def _guvenli_sql_kimlik(deger, env_adi):
    """Veritabanı/kullanıcı adı SQL'e f-string ile gömülüyor (bulgu #12); env değişkeninden
    geldiği için yalnızca harf/rakam/alt çizgi kabul edilir — aksi halde SQL enjeksiyonuna
    açık bir değer sessizce kullanılmak yerine erken ve net hata verilir."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", deger):
        raise ValueError(
            f"Geçersiz {env_adi}: {deger!r} (yalnızca harf/rakam/alt çizgi, harfle/altçizgiyle başlamalı)"
        )
    return deger


def _sql_literal_kacir(deger):
    """SQL string literalindeki tek tırnağı kaçırır (standart SQL escaping)."""
    return deger.replace("'", "''")


DB_ADI = _guvenli_sql_kimlik(os.environ.get("UYAP_DB_NAME", "uyap_icra"), "UYAP_DB_NAME")
DB_USER = _guvenli_sql_kimlik(os.environ.get("UYAP_DB_USER", "postgres"), "UYAP_DB_USER")
DB_HOST = os.environ.get("UYAP_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("UYAP_DB_PORT", "5432")


def _db_parolasi():
    """'postgres' kullanıcısı için kalıcı, rastgele parola (ilk çağrıda üretilir).

    initdb --auth-host=trust yerine scram-sha-256 kullanabilmek için gerekli: aksi
    halde bu Windows makinesindeki HERHANGİ BİR kullanıcı/süreç 127.0.0.1:5432'ye
    bağlanıp dava/icra verilerini parolasız okuyup yazabilirdi.
    """
    env_parola = os.environ.get("UYAP_DB_PASSWORD")
    if env_parola:
        return env_parola
    if os.path.isfile(PAROLA_DOSYA):
        with open(PAROLA_DOSYA, "r", encoding="utf-8") as f:
            mevcut = f.read().strip()
        if mevcut:
            # Süreç genelinde tek kaynak: bundan sonra bu process'ten türeyen (ör.
            # Panel'in ayrı 'manage.py runserver' alt süreci gibi) os.environ.copy()
            # yapan her alt süreç parolayı otomatik devralsın.
            os.environ["UYAP_DB_PASSWORD"] = mevcut
            return mevcut
    parola = secrets.token_urlsafe(24)
    os.makedirs(DB_BASE, exist_ok=True)
    with open(PAROLA_DOSYA, "w", encoding="utf-8") as f:
        f.write(parola)
    try:
        os.chmod(PAROLA_DOSYA, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["icacls", PAROLA_DOSYA, "/inheritance:r",
                 "/grant:r", f"{os.environ.get('USERNAME', '')}:F"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            pass
    os.environ["UYAP_DB_PASSWORD"] = parola
    return parola

# Windows'ta alt süreçler için konsol penceresi açma.
_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def _exe(ad):
    return os.path.join(PG_BIN, ad + (".exe" if sys.platform.startswith("win") else ""))


def _calistir(args, **kw):
    """Sessiz subprocess; çıktı (returncode, stdout+stderr) döner.

    stdin HER ZAMAN DEVNULL: GUI (pythonw, konsolsuz) altında çağrıldığında
    inherited stdin geçersiz/kapalı olabilir; psql gibi bir araç parola için
    interaktif prompt'a düşerse bu olmadan sonsuza dek okumaya çalışıp
    panel.py'yi pencere hiç açılmadan asılı bırakırdı (canlı görüldü)."""
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.STDOUT)
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    if _NO_WINDOW:
        kw.setdefault("creationflags", _NO_WINDOW)
    p = subprocess.run(args, **kw)
    return p.returncode, (p.stdout or "")


def kurulumu_dogrula(log):
    if not os.path.isfile(_exe("postgres")):
        log(f"⛔ PostgreSQL binaries bulunamadı: {PG_BIN}")
        return False
    return True


def sunucu_ayakta():
    """5432 TCP'de biri dinliyor mu? (hızlı, UYAP'a hiç gitmez)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        return s.connect_ex((DB_HOST, int(DB_PORT))) == 0
    finally:
        s.close()


def cluster_olustur(log):
    if os.path.isfile(os.path.join(PGDATA, "postgresql.conf")):
        return True
    log("… veri kümesi (pgdata) ilk kez oluşturuluyor (initdb)")
    os.makedirs(PGDATA, exist_ok=True)
    _db_parolasi()  # PAROLA_DOSYA'yı initdb'den ÖNCE üret; --pwfile bunu okur
    rc, out = _calistir([
        _exe("initdb"), "-D", PGDATA, "-U", DB_USER,
        "--encoding=UTF8", "--locale=C",
        "--auth-local=scram-sha-256", "--auth-host=scram-sha-256",
        f"--pwfile={PAROLA_DOSYA}",
    ])
    if rc != 0:
        log("⛔ initdb başarısız:\n" + out)
        return False
    log("✓ veri kümesi oluşturuldu (parolalı erişim)")
    return True


def guvenlik_yukselt(log):
    """Daha önce 'trust' (parolasız) modda kurulmuş bir veri kümesini veri KAYBI
    OLMADAN parolalı (scram-sha-256) erişime yükseltir.

    'trust' modda bu Windows makinesindeki HERHANGİ BİR kullanıcı/süreç
    127.0.0.1:5432'ye bağlanıp dava/icra verilerini parolasız okuyup
    yazabiliyordu. Akış: (1) sunucu hâlâ trust moddayken parola ata,
    (2) pg_hba.conf'u scram-sha-256'ya çevir, (3) 'reload' ile devreye al.
    """
    hba = os.path.join(PGDATA, "pg_hba.conf")
    if not os.path.isfile(hba):
        return True
    with open(hba, "r", encoding="utf-8") as f:
        icerik = f.read()
    # Yalnızca aktif (yorum/boş olmayan) satırların METHOD sütununu kontrol et.
    # Stok pg_hba.conf'un üretilen açıklama başlığı örnek olarak "trust" kelimesini
    # YORUM satırında geçirir (`# METHOD can be "trust", ...`) — saf "trust" in icerik
    # araması buna yanlış-pozitif verip her açılışta gereksiz yükseltme denemesi
    # başlatıyordu; bu deneme de psql'in parola isteyip konsolsuz ortamda sonsuza
    # dek asılı kalmasına yol açıyordu (panel.py penceresi hiç açılmıyordu).
    trust_var_mi = False
    for satir in icerik.splitlines():
        satir = satir.split("#", 1)[0].strip()
        if not satir:
            continue
        if satir.split()[-1] == "trust":
            trust_var_mi = True
            break
    if not trust_var_mi:
        return True  # zaten yükseltilmiş
    log("… mevcut veri kümesi 'trust' (parolasız) modda; parolalı erişime yükseltiliyor")
    parola = _db_parolasi()
    rc, out = _calistir([
        _exe("psql"), "-U", DB_USER, "-h", DB_HOST, "-p", DB_PORT, "-d", "postgres",
        "-c", f"ALTER USER {DB_USER} WITH PASSWORD '{_sql_literal_kacir(parola)}'",
    ], env=dict(os.environ, PGPASSWORD=parola))
    if rc != 0:
        log("⛔ parola ataması başarısız:\n" + out)
        return False
    yeni = icerik.replace("trust", "scram-sha-256")
    with open(hba, "w", encoding="utf-8") as f:
        f.write(yeni)
    rc, out = _calistir([_exe("pg_ctl"), "-D", PGDATA, "reload"])
    if rc != 0:
        log("⛔ pg_ctl reload başarısız:\n" + out)
        return False
    log("✓ veri kümesi parolalı erişime yükseltildi")
    return True


def sunucu_baslat(log):
    if sunucu_ayakta():
        return True
    log("… PostgreSQL başlatılıyor (pg_ctl start)")
    rc, out = _calistir([
        _exe("pg_ctl"), "-D", PGDATA, "-l", LOG_DOSYA, "-w", "-t", "30",
        "-o", f"-p {DB_PORT}", "start",
    ])
    # pg_ctl -w bağlantı kurulana dek bekler; yine de kısa bir doğrulama
    for _ in range(20):
        if sunucu_ayakta():
            log("✓ PostgreSQL ayakta (127.0.0.1:%s)" % DB_PORT)
            return True
        time.sleep(0.5)
    log("⛔ PostgreSQL başlatılamadı:\n" + out)
    return False


def veritabani_olustur(log):
    env = dict(os.environ, PGPASSWORD=_db_parolasi())
    rc, out = _calistir([
        _exe("psql"), "-U", DB_USER, "-h", DB_HOST, "-p", DB_PORT,
        "-d", "postgres", "-tAc",
        f"SELECT 1 FROM pg_database WHERE datname='{DB_ADI}'",
    ], env=env)
    if rc != 0:
        log("⛔ veritabanı kontrolü başarısız:\n" + out)
        return False
    if out.strip() == "1":
        return True
    log(f"… '{DB_ADI}' veritabanı oluşturuluyor")
    rc, out = _calistir([
        _exe("createdb"), "-U", DB_USER, "-h", DB_HOST, "-p", DB_PORT,
        "-E", "UTF8", DB_ADI,
    ], env=env)
    if rc != 0:
        log(f"⛔ '{DB_ADI}' oluşturulamadı:\n" + out)
        return False
    log(f"✓ '{DB_ADI}' oluşturuldu")
    return True


def migrate(log):
    """manage.py migrate — şema yoksa kurar, güncel değilse uygular."""
    manage = os.path.join(MODELS_DIR, "manage.py")
    if not os.path.isfile(manage):
        log(f"⛔ manage.py yok: {manage}")
        return False
    log("… Django migrate")
    env = dict(os.environ, UYAP_DB_PASSWORD=_db_parolasi())
    rc, out = _calistir(
        [sys.executable, manage, "migrate", "--noinput"],
        cwd=MODELS_DIR, env=env,
    )
    if rc != 0:
        log("⛔ migrate başarısız:\n" + out)
        return False
    log("✓ migrate tamam")
    return True


def ensure_db(log=print):
    """Program açılışında çağrılır. DB'yi uçtan uca hazır eder. True/False döner."""
    try:
        if not kurulumu_dogrula(log):
            return False
        if not cluster_olustur(log):
            return False
        if not sunucu_baslat(log):
            return False
        if not guvenlik_yukselt(log):
            return False
        if not veritabani_olustur(log):
            return False
        if not migrate(log):
            return False
        log("✅ Veritabanı hazır.")
        return True
    except Exception as e:
        log(f"⛔ Veritabanı başlatma hatası: {e}")
        return False


if __name__ == "__main__":
    ok = ensure_db()
    sys.exit(0 if ok else 1)
