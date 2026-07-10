# -*- coding: utf-8 -*-
"""
Veritabanı Sıfırlayıcı (sifirla_veritabani.py) — YAYIN ÖNCESİ TEMİZ BAŞLANGIÇ
============================================================================
İKİ AYRI veritabanını sıfırlar (ikisi birbirinden bağımsızdır, karışmaz):

  1. HESAP DEPOSU (bizim) — ofisler + kullanıcılar + siparişler.
       • Yerel: `Uyap Haricen Giriş/accounts.json` (+ vendor_deploy kopyası).
       • Bulut: DATABASE_URL ayarlıysa PostgreSQL `uyap_kv` tablosundaki k='accounts' satırı.
     NOT: 'utku' owner hesabı da burada tutulur; sıfırlama onu da SİLER → sunucuyu tekrar
     UYAP_OWNER_PASSWORD ile başlatıp owner'ı yeniden bootstrap edin (yeni TOTP anahtarı üretilir).

  2. İCRA VERİSİ (kullanıcının) — gömülü PostgreSQL `uyap_icra` (Django modelleri).
       • %LOCALAPPDATA%/UyapIcra altındaki veri kümesi. DROP DATABASE + yeniden oluştur + migrate.

Kullanım (repo kökünden):
    python sifirla_veritabani.py                 # KURU ÇALIŞMA: ne yapılacağını yazar, DOKUNMAZ
    python sifirla_veritabani.py --sil           # gerçekten sıfırlar (onay ister)
    python sifirla_veritabani.py --sil -y        # sorgusuz sıfırlar (otomasyon)
    python sifirla_veritabani.py --sil --sadece-hesap   # yalnız hesap deposu
    python sifirla_veritabani.py --sil --sadece-icra    # yalnız uyap_icra
    python sifirla_veritabani.py --sil --flush          # icra'yı DROP yerine `manage.py flush` ile

ÖNEMLİ: icra (uyap_icra) migrate adımı Django + psycopg2 gerektirir → betiği PROJE .venv'iyle
çalıştırın:  "Uyap Haricen Giriş\\.venv\\Scripts\\python.exe" sifirla_veritabani.py --sil -y
(Sistem python'ında psycopg2 yoksa DROP/oluştur çalışır ama migrate başarısız olur.)

GERİ ALINAMAZ. Paylaşım/yedek yoksa çalıştırmadan önce iyi düşünün.
"""

import os
import sys
import subprocess

KOK = os.path.dirname(os.path.abspath(__file__))
UYAP_DIR = os.path.join(KOK, "Uyap Haricen Giriş")

# db_baslat yardımcılarını (uyap_icra) ödünç al.
sys.path.insert(0, KOK)
import db_baslat  # noqa: E402

ACCOUNTS_DOSYALARI = [
    os.path.join(UYAP_DIR, "accounts.json"),
    os.path.join(UYAP_DIR, "vendor_deploy", "accounts.json"),
]
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def _log(m=""):
    print(m)


# ── 1. Hesap deposu ────────────────────────────────────────────────────────────────────────
def hesap_deposu_plani():
    """Ne silineceğini (dosya + varsa PostgreSQL satırı) tarif eden satırlar döndürür."""
    plan = []
    for p in ACCOUNTS_DOSYALARI:
        if os.path.isfile(p):
            plan.append(("DOSYA", p))
    if DATABASE_URL:
        plan.append(("PG-SATIR", f"uyap_kv[k='accounts'] @ {_dsn_maske(DATABASE_URL)}"))
    return plan


def _dsn_maske(dsn):
    """DSN'deki parolayı maskeler (log'a düz parola yazmamak için)."""
    try:
        import re
        return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", dsn)
    except Exception:
        return "(dsn)"


def hesap_deposu_sifirla():
    silinen = 0
    for p in ACCOUNTS_DOSYALARI:
        if os.path.isfile(p):
            try:
                os.remove(p)
                silinen += 1
                _log(f"  silindi: {os.path.relpath(p, KOK)}")
            except OSError as e:
                _log(f"  HATA: {os.path.relpath(p, KOK)} -> {e}")
    if DATABASE_URL:
        try:
            import psycopg2
            with psycopg2.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM uyap_kv WHERE k = %s", ("accounts",))
                conn.commit()
            _log("  silindi: PostgreSQL uyap_kv[k='accounts']")
            silinen += 1
        except Exception as e:
            _log(f"  HATA: PostgreSQL hesap satırı silinemedi -> {e}")
    if not silinen:
        _log("  (hesap deposu zaten boş — silinecek bir şey yok)")
    return silinen


# ── 2. İcra verisi (uyap_icra) ───────────────────────────────────────────────────────────────
def icra_verisi_sifirla(flush=False):
    """uyap_icra'yı sıfırlar: DROP DATABASE + yeniden oluştur + migrate (varsayılan) ya da
    `manage.py flush` (şemayı korur). PostgreSQL ayakta değilse önce başlatmayı dener."""
    if not db_baslat.kurulumu_dogrula(_log):
        _log("  ⛔ PostgreSQL binaries bulunamadı; icra sıfırlama atlandı.")
        return False
    if not db_baslat.sunucu_ayakta():
        db_baslat.cluster_olustur(_log)
        if not db_baslat.sunucu_baslat(_log):
            _log("  ⛔ PostgreSQL başlatılamadı; icra sıfırlama atlandı.")
            return False

    if flush:
        manage = os.path.join(db_baslat.MODELS_DIR, "manage.py")
        env = dict(os.environ, UYAP_DB_PASSWORD=db_baslat._db_parolasi())
        rc, out = db_baslat._calistir([sys.executable, manage, "flush", "--noinput"],
                                      cwd=db_baslat.MODELS_DIR, env=env)
        if rc != 0:
            _log("  ⛔ flush başarısız:\n" + out)
            return False
        _log(f"  ✓ '{db_baslat.DB_ADI}' içeriği boşaltıldı (flush; şema korundu).")
        return True

    # DROP DATABASE bir transaction bloğu İÇİNDE çalışamaz; bu yüzden aktif bağlantıları kesme
    # (SELECT) ile DROP'u AYRI psql çağrılarında yürüt (aksi halde psql ikisini tek transaction'a sarar).
    _psql_env = dict(os.environ, PGPASSWORD=db_baslat._db_parolasi())

    def _psql(sql):
        return db_baslat._calistir([
            db_baslat._exe("psql"), "-U", db_baslat.DB_USER, "-h", db_baslat.DB_HOST,
            "-p", db_baslat.DB_PORT, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", sql,
        ], env=_psql_env)

    _psql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
          f"WHERE datname='{db_baslat.DB_ADI}' AND pid<>pg_backend_pid();")  # bağlantıları kes
    rc, out = _psql(f"DROP DATABASE IF EXISTS {db_baslat.DB_ADI};")
    if rc != 0:
        _log("  ⛔ DROP DATABASE başarısız:\n" + out)
        return False
    _log(f"  ✓ '{db_baslat.DB_ADI}' silindi.")
    if not db_baslat.veritabani_olustur(_log):
        return False
    if not db_baslat.migrate(_log):
        return False
    _log(f"  ✓ '{db_baslat.DB_ADI}' temiz şema ile yeniden oluşturuldu.")
    return True


def main(argv):
    sil = "--sil" in argv
    sorgusuz = "-y" in argv or "--yes" in argv
    sadece_hesap = "--sadece-hesap" in argv
    sadece_icra = "--sadece-icra" in argv
    flush = "--flush" in argv

    yap_hesap = not sadece_icra
    yap_icra = not sadece_hesap

    _log("Veritabanı sıfırlama planı (repo kökü: %s)\n" % KOK)
    if yap_hesap:
        _log("1) HESAP DEPOSU (ofisler + kullanıcılar + siparişler + owner 'utku'):")
        plan = hesap_deposu_plani()
        for tur, p in plan:
            _log(f"   [{tur}] {p if tur=='PG-SATIR' else os.path.relpath(p, KOK)}")
        if not plan:
            _log("   (zaten boş)")
        _log("")
    if yap_icra:
        yontem = "manage.py flush (şema korunur)" if flush else "DROP DATABASE + yeniden oluştur + migrate"
        _log("2) İCRA VERİSİ (uyap_icra @ %s):" % db_baslat.DB_BASE)
        _log(f"   yöntem: {yontem}")
        _log("")

    if not sil:
        _log("KURU ÇALIŞMA — hiçbir şey değiştirilmedi. Sıfırlamak için: "
             "python sifirla_veritabani.py --sil")
        return 0

    if not sorgusuz:
        try:
            onay = input("Yukarıdakileri KALICI olarak sıfırlamak istiyor musunuz? (evet/h): ").strip().lower()
        except EOFError:
            onay = ""
        if onay not in ("evet", "e", "yes", "y"):
            _log("İptal edildi; hiçbir şey değiştirilmedi.")
            return 1

    ok = True
    if yap_hesap:
        _log("\n[1] Hesap deposu sıfırlanıyor…")
        hesap_deposu_sifirla()
    if yap_icra:
        _log("\n[2] İcra verisi (uyap_icra) sıfırlanıyor…")
        ok = icra_verisi_sifirla(flush=flush) and ok

    _log("\n" + ("✅ Sıfırlama tamamlandı." if ok else "⚠️ Bazı adımlar başarısız oldu (yukarı bakın)."))
    if yap_hesap:
        _log("Hatırlatma: owner 'utku' silindi → sunucuyu UYAP_OWNER_PASSWORD ile bir kez başlatıp "
             "yeni TOTP anahtarını authenticator'a ekleyin.")
    return 0 if ok else 1


if __name__ == "__main__":
    for _a in (sys.stdout, sys.stderr):
        try:
            _a.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main(sys.argv[1:]))
