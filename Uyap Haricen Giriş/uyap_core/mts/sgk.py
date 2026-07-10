"""
uyap_core.mts.sgk — SGK toplu sorgu motoru + formatlayıcılar (tarayıcısız)
=========================================================================
Kararlı/Sorgu/SGK Sorgu/sgk_sorgu_gui.py'deki SorguMotoru + veri özetleme
mantığının, "güçlü bağlantı" (jobs.JobContext.uyap → uyap_proxy.gw) üzerinde
çalışacak hâli. Playwright `context.request.fetch` → `await ctx.uyap(...)`.

SALT-OKUMA: tüm sorgular write=False ile gider (yazma kilidine girmez), bu yüzden
bireysel kullanıcıları etkilemez. Sonuçlar metin döner; imza/indirme/onay yok.

Bu modül hem ofiste (job_handlers.sgk_toplu_sorgu) hem de istemcide (formatlayıcılar
ve sabitler; Excel I/O ayrıca sgk_excel.py'de) import edilebilir.
"""

import re
import json
import asyncio


BIRIM_TURU2 = "1101"   # İcra Dairesi
BIRIM_TURU3 = "2"      # İcra

# Sorgu tanımları: (kolon_basligi, endpoint, ozetleyici_anahtar) — sütun sırası SABİT.
SORGULAR = [
    ("Kamu Çalışan",   "borclu_bilgileri_goruntule_sgk_kamuCalisaniBilgileri.ajx",   "kamuCalisani"),
    ("Kamu Emekli",    "borclu_bilgileri_goruntule_sgk_kamuEmekliBilgileri.ajx",     "kamuEmekli"),
    ("SSK Çalışan",    "borclu_bilgileri_goruntule_sgk_sskCalisaniBilgileri.ajx",    "sskCalisani"),
    ("SSK Emekli",     "borclu_bilgileri_goruntule_sgk_sskEmekliBilgileri.ajx",      "sskEmekli"),
    ("Bağkur Çalışan", "borclu_bilgileri_goruntule_sgk_bagkurCalisaniBilgileri.ajx", "bagkurCalisani"),
    ("Bağkur Emekli",  "borclu_bilgileri_goruntule_sgk_bagkurEmekliBilgileri.ajx",   "bagkurEmekli"),
    ("SSK İş Yeri",    "borclu_bilgileri_goruntule_sgk_isYeriBilgileri.ajx",         "isYeri"),
]
# anahtar -> sütun ofseti (E=0, F=1, ...) ve başlık
ANAHTAR_OFFSET = {a: i for i, (_, _, a) in enumerate(SORGULAR)}
ANAHTAR_BASLIK = {a: b for (b, _, a) in SORGULAR}
TUM_ANAHTARLAR = [a for (_, _, a) in SORGULAR]

HATA_ON = "[HATA]"
SIRKET_NOT = "ŞİRKET - sorgulanmadı"
SIRKET_ANAHTARLARI = {"STI", "SIRKETI", "SIRKET", "LTD"}

# Varsayılan hız/yük ayarları (UYAP throttle'ını azaltmak için).
VARSAYILAN_SORGU_ARASI = 1.5
VARSAYILAN_SATIR_ARASI = 0.2
VARSAYILAN_MOLA_HATA_ESIGI = 3
VARSAYILAN_MOLA_SURESI = 60

NOISE_KEYS = {"isNew", "isMock", "hasError", "hasData", "metadata",
              "yeniBirimEkle", "orgKoduDegisti", "isTumunuKopyala", "testMi"}


# --------------------------------------------------------------------------- #
#  Türkçe normalizasyon + özetleme/dökme yardımcıları (orijinalden birebir)
# --------------------------------------------------------------------------- #
def tr_normalize(s):
    if s is None:
        return ""
    s = str(s)
    cevrim = str.maketrans("ıİiİğĞüÜşŞöÖçÇ", "IIIIGGUUSSOOCC")
    s = s.translate(cevrim).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _temizle(v):
    return "" if v is None else str(v).strip()


def _ilk_hata(dvo):
    for k in ("hataMesaji", "anaHataMesaji", "sonucMesaj", "mesaj", "islemSonucAciklama"):
        if isinstance(dvo, dict) and dvo.get(k):
            return _temizle(dvo[k])
    return ""


def _g(d, *yol):
    for k in yol:
        if isinstance(d, dict):
            d = d.get(k)
        elif isinstance(d, list) and isinstance(k, int) and -len(d) <= k < len(d):
            d = d[k]
        else:
            return None
    return d


def _parcala(*ciftler):
    out = []
    for et, dg in ciftler:
        dg = _temizle(dg)
        if dg:
            out.append(f"{et}: {dg}" if et else dg)
    return " | ".join(out)


def sirket_mi(isim):
    """İsim tüzel kişi (ŞTİ/Şirketi/Ltd) mi? -> SGK sorgusu yapılmaz."""
    if not isim:
        return False
    return bool(set(tr_normalize(isim).split()) & SIRKET_ANAHTARLARI)


def degerlendir_ssk_calisani(dvo):
    """(durum, ozet, veri_var). durum = 'Olumlu' | 'Olumsuz'. (orijinalden birebir)"""
    if not isinstance(dvo, dict):
        s = _temizle(dvo)
        return ("Olumlu" if s else "Olumsuz"), s, bool(s)
    try:
        sskBilgi = dvo.get("sskBilgi") or {}
        tescil_list = sskBilgi.get("tescilKaydi4AList") or []
        isyeri_sonuc = _g(dvo, "isYeriBilgileri", "data", "sonuc") or {}
        ozet_array = isyeri_sonuc.get("isyeriTescil4AOzetArray") or []
        sig = _g(dvo, "sskBilgiDetay", "sgkIsyeriDVO", "sigortaliBilgi") or {}

        b = isyeri_sonuc.get("basarili")
        guncel_hizmet = (b is True) or (str(b).strip().lower() == "true")

        oz = ozet_array[0] if ozet_array else {}
        unvan = _temizle(sig.get("isyeriUnvan")) or _temizle(oz.get("unvan"))
        vergi = _temizle(oz.get("teVergiNo"))
        donem = _temizle(sig.get("donemYilAy"))
        if not donem and oz.get("sonDonemYil"):
            donem = f"{_temizle(oz.get('sonDonemYil'))}/{_temizle(oz.get('sonDonemAy'))}"
        gun = oz.get("sonDonemHizmetGun")
        sicil = _temizle(sig.get("sicilNo")) or _temizle(_g(tescil_list, 0, "sicilNo"))
        giris = (_temizle(sig.get("ilkIseGirisTarihi"))
                 or _temizle(_g(tescil_list, 0, "kimlikBilgileri", "iseGirisTarihi")))
        adres = _temizle(sig.get("isyeriAdresi"))
        cikis = _temizle(sig.get("istenCikisTarihi"))

        gun_s = ""
        if gun not in (None, ""):
            gun_s = f"son dönem {gun} gün"
            try:
                if int(gun) < 30:
                    gun_s += " (ay içinde çıkış olabilir)"
            except (TypeError, ValueError):
                pass

        if guncel_hizmet and not cikis:
            ozet = _parcala((None, "Çalışıyor"), ("İşyeri", unvan), ("VKN", vergi),
                            ("Son dönem", donem), (None, gun_s), ("Adres", adres),
                            ("Sicil", sicil), ("İşe giriş", giris))
            return "Olumlu", ozet, True

        mesaj = (_temizle(isyeri_sonuc.get("mesaj"))
                 or _temizle(sskBilgi.get("sonucMesaji"))
                 or _temizle(sskBilgi.get("anaHataMesaji")))
        if cikis:
            ozet = _parcala(("İşten çıkış", cikis), ("Son işyeri", unvan), ("VKN", vergi),
                            ("Son dönem", donem), (None, gun_s), ("Adres", adres),
                            ("Sicil", sicil), ("İşe giriş", giris))
            return "Olumsuz", ozet, True
        if unvan or donem or sicil:
            son = _parcala(("Son işyeri", unvan), ("Son dönem", donem),
                           ("Adres", adres), ("Sicil", sicil), ("İşe giriş", giris))
            ozet = mesaj or "Güncel 4a hizmeti bulunamadı"
            if son:
                ozet = f"{ozet} | {son}"
            return "Olumsuz", ozet, True
        if tescil_list:
            kb = _g(tescil_list, 0, "kimlikBilgileri") or {}
            ek = _parcala(("Eski sicil", _temizle(kb.get("sicilNo"))),
                          ("İlk işe giriş", _temizle(kb.get("iseGirisTarihi"))))
            ozet = mesaj or "Güncel 4a hizmeti bulunamadı"
            if ek:
                ozet = f"{ozet} ({ek})"
            return "Olumsuz", ozet, True
        return "Olumsuz", (mesaj or "Kayıt bulunamadı"), False
    except Exception as e:
        return "Olumsuz", f"[değerlendirme hatası: {e}]", False


def ozetle(anahtar, dvo):
    """Hücrenin ilk satırı: kısa özet. (orijinalden birebir)"""
    try:
        if not isinstance(dvo, dict):
            return _temizle(dvo)
        if anahtar == "kamuCalisani":
            if dvo.get("durumAciklamasi4C") or dvo.get("kurum"):
                parcalar = [_temizle(dvo.get("durumAciklamasi4C")),
                            _temizle(dvo.get("kurum")), _temizle(dvo.get("unvani"))]
                yer = "/".join(p for p in (_temizle(dvo.get("kurumIl")),
                                           _temizle(dvo.get("kurumIlce"))) if p)
                if yer:
                    parcalar.append(yer)
                if dvo.get("iseBaslamaTarihi"):
                    parcalar.append("İşe baş: " + _temizle(dvo.get("iseBaslamaTarihi")))
                return " | ".join(p for p in parcalar if p)
            return _ilk_hata(dvo) or "Kayıt yok"
        if anahtar == "kamuEmekli":
            return _ilk_hata(dvo) or "Kayıt yok"
        if anahtar == "sskCalisani":
            parcalar = []
            ssk = dvo.get("sskBilgi") or {}
            liste = ssk.get("tescilKaydi4AList") or []
            if liste:
                kb = (liste[0] or {}).get("kimlikBilgileri") or {}
                sicil = _temizle(kb.get("sicilNo"))
                giris = _temizle(kb.get("iseGirisTarihi"))
                bilgi = "Tescil var"
                if sicil:
                    bilgi += f" (Sicil {sicil}" + (f", işe giriş {giris}" if giris else "") + ")"
                parcalar.append(bilgi)
            isy = (((dvo.get("isYeriBilgileri") or {}).get("data") or {}).get("sonuc") or {})
            if isy.get("mesaj"):
                parcalar.append(_temizle(isy.get("mesaj")))
            detay = dvo.get("sskBilgiDetay") or {}
            if "aktif" in detay:
                parcalar.append("Aktif: " + ("Evet" if detay.get("aktif") else "Hayır"))
            return " | ".join(parcalar) if parcalar else (_ilk_hata(dvo) or "Kayıt yok")
        if anahtar == "sskEmekli":
            liste = dvo.get("emekliKaydi4AList") or []
            if liste:
                return f"Emekli kaydı var ({len(liste)} kayıt)"
            return _ilk_hata(dvo) or "Kayıt yok"
        if anahtar == "bagkurCalisani":
            liste = dvo.get("tescilKaydi4BList") or []
            if liste:
                t = liste[0] or {}
                parcalar = [_temizle(dvo.get("islemSonucAciklama")) or "Kayıt bulundu"]
                if _temizle(t.get("bagNo")):
                    parcalar.append("Bağ-No " + _temizle(t.get("bagNo")))
                if _temizle(t.get("terkAciklama")):
                    sterk = "Terk: " + _temizle(t.get("terkAciklama"))
                    if t.get("terkTarihi"):
                        sterk += f" ({t.get('terkTarihi')})"
                    parcalar.append(sterk)
                return " | ".join(p for p in parcalar if p)
            return _ilk_hata(dvo) or "Kayıt yok"
        if anahtar in ("bagkurEmekli", "isYeri"):
            return _ilk_hata(dvo) or "Kayıt yok"
        return _ilk_hata(dvo) or "Veri var"
    except Exception as e:
        return f"[özet hatası: {e}]"


def tam_metin(veri, girinti=0):
    """sorguSonucDVO'nun TAMAMINI okunabilir, girintili metne döker."""
    satirlar = []
    ond = "    " * girinti
    if isinstance(veri, dict):
        for k, v in veri.items():
            if k in NOISE_KEYS or v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                alt = tam_metin(v, girinti + 1)
                if alt.strip():
                    satirlar.append(f"{ond}{k}:")
                    satirlar.append(alt)
            else:
                satirlar.append(f"{ond}{k}: {str(v).strip()}")
    elif isinstance(veri, list):
        for i, e in enumerate(veri, 1):
            if isinstance(e, (dict, list)):
                alt = tam_metin(e, girinti + 1)
                if alt.strip():
                    satirlar.append(f"{ond}[{i}]")
                    satirlar.append(alt)
            elif e not in (None, ""):
                satirlar.append(f"{ond}- {str(e).strip()}")
    else:
        satirlar.append(f"{ond}{str(veri).strip()}")
    return "\n".join(satirlar)


def hucre_metni(anahtar, dvo):
    """Hücreye yazılacak nihai metin: kısa özet + ayraç + TAM veri."""
    if anahtar == "sskCalisani":
        durum, ozet, veri_var = degerlendir_ssk_calisani(dvo)
        bas = f"{durum} | {ozet}" if ozet else durum
        if not veri_var:
            return bas
        tam = tam_metin(dvo)
        return f"{bas}\n──────────\n{tam}" if tam.strip() else bas
    ozet = ozetle(anahtar, dvo)
    tam = tam_metin(dvo)
    if tam.strip() and tam.strip() != ozet.strip():
        return f"{ozet}\n──────────\n{tam}"
    return ozet


def borclu_sec(borclular, isim):
    """İsimle eşleşen borçluyu seç (yoksa ilk borçlu). (borclu, eslesti) döner."""
    if not borclular:
        return None, False
    if len(borclular) == 1:
        return borclular[0], True
    hedef = tr_normalize(isim)
    if hedef:
        for b in borclular:
            tam = tr_normalize(b["adi"] + " " + b["soyadi"])
            if tam and (tam == hedef or tam in hedef or hedef in tam):
                return b, True
        hedef_kelime = set(hedef.split())
        for b in borclular:
            tam_kelime = set(tr_normalize(b["adi"] + " " + b["soyadi"]).split())
            if tam_kelime and tam_kelime <= hedef_kelime:
                return b, True
    return borclular[0], False


# --------------------------------------------------------------------------- #
#  Async sorgu motoru (ctx.uyap üzerinden; hepsi salt-okuma)
# --------------------------------------------------------------------------- #
_SORGU_HEADERS = {"Referer": "https://avukat.uyap.gov.tr/dosya-sorgulama",
                  "Accept": "application/json, text/plain, */*"}


async def _post_json(ctx, endpoint, payload):
    """UYAP'a POST atıp (status, parsed) döndürür. write=False: yazma kilidine girmez."""
    resp = await ctx.uyap("POST", endpoint, json=payload, headers=_SORGU_HEADERS, write=False)
    text = resp.text
    try:
        return resp.status_code, json.loads(text)
    except Exception:
        return resp.status_code, text


async def dosya_id_bul(ctx, yil, sira):
    payload = {
        "dosyaDurumKod": 0, "pageSize": 500, "pageNumber": 1,
        "dosyaYil": int(yil), "dosyaSira": int(sira),
        "birimId": "", "birimTuru2": BIRIM_TURU2, "birimTuru3": BIRIM_TURU3,
    }
    _, veri = await _post_json(ctx, "search_phrase_detayli.ajx", payload)
    try:
        kayitlar = veri[0]
        if kayitlar:
            return kayitlar[0].get("dosyaId"), kayitlar[0].get("dosyaNo")
    except Exception:
        pass
    return None, None


async def borclular(ctx, dosya_id):
    _, veri = await _post_json(ctx, "dosya_borclu_list.ajx", {"dosyaId": dosya_id})
    sonuc = []
    if isinstance(veri, list):
        for b in veri:
            kb = (b or {}).get("kisiTumDVO") or {}
            sonuc.append({
                "kisiKurumId": b.get("kisiKurumId"),
                "adi": _temizle(kb.get("adi")),
                "soyadi": _temizle(kb.get("soyadi")),
                "tc": _temizle(kb.get("tcKimlikNo")),
            })
    return sonuc


async def sgk_sorgula(ctx, dosya_id, kisi_kurum_id, anahtarlar, sorgu_arasi=VARSAYILAN_SORGU_ARASI):
    """Yalnızca 'anahtarlar' içindeki SGK sorgularını çalıştırır.
    Döner: {anahtar: (basarili_mi, metin)}. Her sorgu arası kısa bekleme."""
    sonuc = {}
    payload = {"dosyaId": dosya_id, "kisiKurumId": kisi_kurum_id}
    for _, endpoint, anahtar in SORGULAR:
        if anahtar not in anahtarlar:
            continue
        ctx.check_cancel()
        try:
            durum, veri = await _post_json(ctx, endpoint, payload)
            if isinstance(veri, dict):
                dvo = veri.get("sorguSonucDVO", veri)
                sonuc[anahtar] = (True, hucre_metni(anahtar, dvo))
            else:
                sonuc[anahtar] = (False, f"{HATA_ON} HTTP {durum}")
        except Exception as e:
            sonuc[anahtar] = (False, f"{HATA_ON} {e}")
        await asyncio.sleep(sorgu_arasi)
    return sonuc
