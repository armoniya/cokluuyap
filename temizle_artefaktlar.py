# -*- coding: utf-8 -*-
"""
Hassas Artefakt Temizleyici (temizle_artefaktlar.py)
====================================================
Güvenlik raporu (docs/guvenlik_analizi.md) #13: tarayıcı profilleri/cache'leri, oturum
çerezleri, ayar/sır dosyaları ve üretilen artefaktlar UYAP'tan çekilmiş kişisel/dosya verisi
ve oturum kimlik bilgileri içerir. PAYLAŞIMDAN / yedekten / arşivlemeden ÖNCE bunları silin.

Kullanım (repo kökünden):
    python temizle_artefaktlar.py            # KURU ÇALIŞMA: yalnızca bulunanları listeler
    python temizle_artefaktlar.py --sil      # gerçekten siler (onay ister)
    python temizle_artefaktlar.py --sil -y   # sorgusuz siler (script/otomasyon için)

NOT: .venv, .git ve site-packages atlanır. Yalnızca aşağıdaki hassas desenler hedeflenir.
"""

import os
import sys
import shutil

KOK = os.path.dirname(os.path.abspath(__file__))

# Atlanacak dizinler (içine hiç girilmez).
ATLA = {".venv", "venv", "env", ".git", "site-packages", "__pycache__"}

# Silinecek DİZİN adları (her yerde aranır).
HASSAS_DIZIN_ADLARI = {"chrome_profile", "uyap_profil", "static_cache", "logger_data"}

# Silinecek DOSYA adları (her yerde aranır).
HASSAS_DOSYA_ADLARI = {
    "uyap_session_cookies.json",   # canlı oturum çerezleri (#1)
    "uyap_app_config.json",        # UYAP parolası + e-imza PIN (#9)
    "accounts.json",               # parola hash'leri, sıfırlama jetonları
    "gw_local_token",              # yerel-yetki jetonu
}


def _hedefleri_bul():
    """Silinecek (dizin, dosya) yollarını toplar. Atlanan ağaçlara hiç inmez."""
    dizinler, dosyalar = [], []
    for kok, alt_dizinler, dosya_listesi in os.walk(KOK):
        # Atlanacak alt ağaçlara inme.
        alt_dizinler[:] = [d for d in alt_dizinler if d not in ATLA]
        for d in list(alt_dizinler):
            if d in HASSAS_DIZIN_ADLARI:
                dizinler.append(os.path.join(kok, d))
                alt_dizinler.remove(d)  # içine ayrıca inme (zaten silinecek)
        for f in dosya_listesi:
            if f in HASSAS_DOSYA_ADLARI:
                dosyalar.append(os.path.join(kok, f))
    return dizinler, dosyalar


def _boyut(yol):
    try:
        if os.path.isdir(yol):
            t = 0
            for k, _, fs in os.walk(yol):
                for f in fs:
                    try:
                        t += os.path.getsize(os.path.join(k, f))
                    except OSError:
                        pass
            return t
        return os.path.getsize(yol)
    except OSError:
        return 0


def main(argv):
    sil = "--sil" in argv
    sorgusuz = "-y" in argv or "--yes" in argv

    dizinler, dosyalar = _hedefleri_bul()
    hepsi = [(p, True) for p in dizinler] + [(p, False) for p in dosyalar]

    if not hepsi:
        print("Temiz: silinecek hassas artefakt bulunamadı.")
        return 0

    toplam = 0
    print("Bulunan hassas artefaktlar (repo kökü: %s):\n" % KOK)
    for yol, is_dir in hepsi:
        b = _boyut(yol)
        toplam += b
        tur = "DİZİN" if is_dir else "DOSYA"
        print(f"  [{tur}] {os.path.relpath(yol, KOK)}  ({b/1024:.0f} KB)")
    print(f"\nToplam: {len(hepsi)} öğe, ~{toplam/1024/1024:.1f} MB")

    if not sil:
        print("\nKURU ÇALIŞMA — hiçbir şey silinmedi. Silmek için: python temizle_artefaktlar.py --sil")
        return 0

    if not sorgusuz:
        try:
            onay = input("\nBunların TÜMÜNÜ kalıcı olarak silmek istiyor musunuz? (evet/h): ").strip().lower()
        except EOFError:
            onay = ""
        if onay not in ("evet", "e", "yes", "y"):
            print("İptal edildi; hiçbir şey silinmedi.")
            return 1

    silinen = 0
    for yol, is_dir in hepsi:
        try:
            if is_dir:
                shutil.rmtree(yol, ignore_errors=False)
            else:
                os.remove(yol)
            silinen += 1
            print(f"  silindi: {os.path.relpath(yol, KOK)}")
        except OSError as e:
            print(f"  HATA (silinemedi): {os.path.relpath(yol, KOK)} -> {e}")
    print(f"\nTamam: {silinen}/{len(hepsi)} öğe silindi.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv[1:]))
