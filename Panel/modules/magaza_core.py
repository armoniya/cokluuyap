# -*- coding: utf-8 -*-
"""
Modül çekirdeği: Uygulama Mağazası (katalog + sahiplik)
======================================================
ARAYÜZSÜZ mantık. İki şeyi tutar:

  1) KATALOG — satılabilir "app"lerin (eklenti) listesi. Her app, sahip olununca
     sol menüye HANGİ grubun altına HANGİ yaprak(lar) olarak ekleneceğini bildirir
     (`menu_yolu` + `yapraklar`). Bu, panel.py'nin dinamik menü kurması için tek
     veri kaynağıdır.
  2) Sahiplik deposu — kullanıcının etkinleştirdiği app kimlikleri. Yerel, yazılabilir
     bir JSON'da tutulur (%LOCALAPPDATA%\\UyapIcra\\sahip_olunan.json). Ödeme/sunucu
     EN SONA kalır; şimdilik mağaza ekranındaki "Etkinleştir/Kaldır" düğmesi burayı yazar.

İlke: çekirdek (UYAP Bağlantısı, Mağaza, Ayarlar) HER ZAMAN gelir; diğer her özellik
mağazadan etkinleştirildikçe menüye eklenir. Bir app kaldırılınca çekirdek çalışmaya
devam eder. Bkz. docs/EKLENTI_MIMARISI.md.
"""

import os
import json


# ─────────────────────────────────────────────────────────────────────────────
# Bu çekirdek ARAYÜZSÜZ ve SUNUM-BAĞIMSIZDIR: tkinter/theme import ETMEZ. Vurgu
# (accent) bir HEX renk değil, bir İSİMDİR ("sage"/"clay"/"blue"/"gold"). Böylece
# aynı katalog hem masaüstü (tkinter — theme.accent ile hex'e çözer) hem de web
# (CSS değişkeni --sage vb.) tarafından paylaşılır. Tek doğruluk kaynağı budur.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Katalog — satılır app'ler (şimdilik elle; ileride mağaza sunucusundan çekilecek)
# Her giriş:
#   kimlik     : benzersiz app kimliği (sahiplik deposunda bu tutulur)
#   ad         : kullanıcının gördüğü ad
#   aciklama   : kısa açıklama (mağaza kartı)
#   fiyat      : gösterim amaçlı metin (mock); ödeme henüz yok
#   menu_yolu  : sol menüde hangi grup(lar) altına gireceği (üstten alta)
#   yapraklar  : eklenecek yaprak(lar) — panel/web key'leriyle birebir;
#                accent = renk İSMİ ("sage"/"clay"/"blue"/"gold")
# ─────────────────────────────────────────────────────────────────────────────
KATALOG = [
    {
        "kimlik": "mts_takip_acma",
        "ad": "MTS Takip Açma",
        "aciklama": "Merkezi Takip Sistemi üzerinden toplu icra takibi açılışı.",
        "fiyat": "Aylık",
        "menu_yolu": ["Dosyalarım", "İcra", "Toplu Takip Aç"],
        "yapraklar": [{"key": "mts", "label": "MTS", "accent": "sage"}],
    },
    {
        "kimlik": "xml_takip",
        "ad": "XML Takip Açma",
        "aciklama": "İcra takip talebini XML ile hazırlayıp UYAP'a gönderme.",
        "fiyat": "Aylık",
        "menu_yolu": ["Dosyalarım", "İcra", "Toplu Takip Aç"],
        "yapraklar": [{"key": "xml", "label": "XML", "accent": "clay"}],
    },
    {
        "kimlik": "potek_takip_acma",
        "ad": "İpotek Takip Açma",
        "aciklama": "İpoteğin Paraya Çevrilmesi Yolu İle İcra Takibi açılışı (Mersis/TCKN/IBAN "
                    "girişi + Excel'den alacak kalemleri).",
        "fiyat": "Aylık",
        "menu_yolu": ["Dosyalarım", "İcra", "Toplu Takip Aç"],
        "yapraklar": [{"key": "potek_takip", "label": "İpotek Takip", "accent": "sage"}],
    },
    {
        "kimlik": "sgk_sorgu",
        "ad": "Toplu Sorgu (SGK)",
        "aciklama": "Excel listesinden toplu SGK / çalışan sorgulaması.",
        "fiyat": "Aylık",
        "menu_yolu": ["Dosyalarım", "İcra"],
        "yapraklar": [{"key": "sgk", "label": "Toplu Sorgu", "accent": "blue"}],
    },
    {
        "kimlik": "icra_dosyalarim",
        "ad": "İcra Dosyalarım",
        "aciklama": "İcra dosyalarınızı arayın, borçlu ve birim bilgilerini görün.",
        "fiyat": "Aylık",
        "menu_yolu": ["Dosyalarım", "İcra"],
        "yapraklar": [{"key": "icra_dosyalarim", "label": "Dosyalarım", "accent": "clay"}],
    },
    {
        "kimlik": "hukuk_dosyalarim",
        "ad": "Hukuk Dosyalarım",
        "aciklama": "Hukuk (dava) dosyalarınızı arayın, mahkeme ve taraf bilgilerini görün.",
        "fiyat": "Aylık",
        "menu_yolu": ["Dosyalarım", "Hukuk"],
        "yapraklar": [{"key": "hukuk_dosyalarim", "label": "Dosyalarım", "accent": "blue"}],
    },
    {
        "kimlik": "udf_donusturucu",
        "ad": "UDF Dönüştürücü",
        "aciklama": "Word/PDF belgelerini UYAP UDF biçimine dönüştürün ve e-imzalayın.",
        "fiyat": "Aylık",
        "menu_yolu": ["Araçlar"],
        "yapraklar": [{"key": "udf", "label": "UDF Dönüştürücü", "accent": "gold"}],
    },
    {
        "kimlik": "oturum_kaydi",
        "ad": "Oturum Kaydı",
        "aciklama": "UYAP oturumundaki tıklama ve ağ trafiğini kaydedin (modül üretimi).",
        "fiyat": "Aylık",
        "menu_yolu": ["Araçlar"],
        "yapraklar": [{"key": "logger", "label": "Oturum Kaydı", "accent": "blue"}],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Sahiplik deposu (yerel JSON)
# Varsayılan BOŞTUR: kullanıcı hiç ek özellik almadıysa sol menüde yalnızca
# çekirdek (UYAP Bağlantısı, Mağaza, Ayarlar) kalır. Logger'ın ürettiği modüller
# de birer mağaza app'idir; etkinleştirilene kadar menüde görünmez.
# ─────────────────────────────────────────────────────────────────────────────
def _depo_yolu():
    """Yazılabilir sahiplik dosyası. %LOCALAPPDATA%\\UyapIcra altında (gömülü
    PostgreSQL ile aynı kök). LOCALAPPDATA yoksa kullanıcı dizinine düşer."""
    kok = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    klasor = os.path.join(kok, "UyapIcra")
    try:
        os.makedirs(klasor, exist_ok=True)
    except Exception:
        pass
    return os.path.join(klasor, "sahip_olunan.json")


def sahip_olunanlar():
    """Etkin app kimliklerinin kümesi (varsayılan boş)."""
    try:
        with open(_depo_yolu(), encoding="utf-8") as f:
            veri = json.load(f)
        if isinstance(veri, list):
            return {str(x) for x in veri}
    except Exception:
        pass
    return set()


def _yaz(sahip):
    """Sahiplik kümesini diske yazar."""
    try:
        with open(_depo_yolu(), "w", encoding="utf-8") as f:
            json.dump(sorted(sahip), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def sahip_ekle(kimlik):
    """Bir app'i etkinleştirir (sahip yapar)."""
    sahip = sahip_olunanlar()
    sahip.add(str(kimlik))
    _yaz(sahip)


def sahip_cikar(kimlik):
    """Bir app'i kaldırır (sahipliği bırakır)."""
    sahip = sahip_olunanlar()
    sahip.discard(str(kimlik))
    _yaz(sahip)


# ─────────────────────────────────────────────────────────────────────────────
# Katalog (statik + üretilmiş birleşik)
# ─────────────────────────────────────────────────────────────────────────────
def katalog():
    """Mağaza kataloğu: statik KATALOG + Logger'ın ürettiği modüller. Her giriş
    aynı şemayı paylaşır (kimlik/ad/aciklama/fiyat/menu_yolu/yapraklar)."""
    liste = [dict(app) for app in KATALOG]
    try:
        # logger_core sadece stdlib import eder (headless-güvenli). Hem masaüstü
        # (`from modules import logger_core`) hem web sunucusu (modules klasörü
        # doğrudan path'te → `import logger_core`) için iki yollu, tembel import.
        try:
            from modules import logger_core
        except Exception:
            import logger_core
        for m in logger_core.registry_oku():
            grup = m.get("grup") or "Üretilen Modüller"
            liste.append({
                "kimlik": f"uretilmis::{m['key']}",
                "ad": m.get("label", m["key"]),
                "aciklama": "Logger ile üretilmiş ağ-sorgu modülü.",
                "fiyat": "Üretilmiş",
                "menu_yolu": [grup],
                "yapraklar": [{"key": m["key"],
                               "label": m.get("label", m["key"]),
                               "accent": "blue"}],
                "uretilmis": True,
                "core": m.get("core"),
            })
    except Exception:
        pass
    return liste
