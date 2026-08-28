# -*- coding: utf-8 -*-
"""
Evrak İndirici — seçili dosya(lar)ın TÜM evraklarını indirir + birleştirir
============================================================================
Canlı doğrulandı (2026-08-27, gerçek oturum, 8 evraklı gerçek bir Hukuk
dosyası): `view_document_brd.uyap` evrak türüne göre FARKLI ham format döner
— 7/8 evrak `application/pdf` (gerçek, doğrudan kullanılabilir PDF baytları,
`%PDF` imzalı), yalnızca "Vekalet Pulu Makbuzu" `text/html` (bkz.
baro_pulu_makbuzu_indiren.py'nin zaten bildiği format). `ekEvrakListesi`
alt-evrakları PARENT'TEN FARKLI, ayrı içerikli belgelerdir (canlı doğrulandı:
aynı istekle farklı boyut/bayt döndü) — bu yüzden ayrı birer evrak kaydı
olarak indirilir. İKİNCİ canlı doğrulama (2026-08-27, kullanıcının kendi
gerçek bir dosyası, 64 evrak): 60 PDF, 3 HTML, 1 TIFF (`image/tiff` —
`_gorsel_pdf_ekle` ile Pillow üzerinden PDF'e çevrilip birleştirilir), 0 UDF
— bu iki canlı örnekte UDF hiç gözlenmedi (bkz. `udf_pdf.py` — yine de
kullanıcı isteğiyle 2026-08-27 UDF->PDF desteği eklendi, `content-type` ne
olursa olsun ZIP imzasına bakarak tespit edilir, güvenlik ağı olarak).
Tanınmayan/dönüştürülemeyen bir format gelirse ham dosya YİNE DE kaydedilir,
birleştirilmiş PDF'e yalnız bir 'dönüştürülemedi' işaret sayfası eklenir
(uydurma render YAPILMAZ).

`dosya_core.evrak_html_indir` KULLANILMAZ — o fonksiyon içeriği utf-8 ile
decode eder, bu da ikili (PDF/görüntü) evrakı geri dönülemez biçimde bozar
(bkz. barkod_sorgu.py'deki AYNI ders, `_evrak_pdf_indir`). Burada kendi
ham-bayt indiricimiz var, dosya_core.py'ye DOKUNULMADI.

DB'deki `dosyaId` OTURUMLUK/eskimiş olabileceğinden (bkz. dosya_core.py
modül başlığı) her dosya için TAZE dosyaId, `birim_id`+`dosyaNo` üzerinden
`search_phrase_detayli.ajx`'e yeniden gidilerek çözülür — DB'deki değere
GÜVENİLMEZ (bkz. baro_pulu_makbuzu_indiren.py/barkod_sorgu.py'deki AYNI
desen).
"""

import io
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from pypdf import PdfReader, PdfWriter

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from . import dosya_core
from . import udf_pdf

SATIR_ARASI_SN = 1.0

_ICERIK_UZANTI = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "image/tiff": ".tiff",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
_GORSEL_TURLERI = ("image/tiff", "image/jpeg", "image/png")


def _guvenli_ad(s, max_len=120):
    """Windows dosya adında yasak karakterleri temizler (bkz.
    baro_pulu_makbuzu_indiren.py'deki AYNI yardımcı) + uzun adları kırpar."""
    temiz = re.sub(r'[\\/:*?"<>|]', "-", str(s or "")).strip().rstrip(". ")
    return (temiz[:max_len] or "adsiz")


def _dosya_id_taze_coz(motor, birim_id, dosya_no, log):
    """(birim_id, dosya_no) -> TAZE dosyaId. Yargı türü/birimi kodu, verilen
    `birim_id`'den DB'deki `Birim` kaydı üzerinden çözülür (çağıran bunları
    ayrıca bilmek zorunda kalmaz)."""
    dosya_core._django_hazirla()
    from icra_models.models import Birim
    try:
        birim = Birim.objects.get(birim_id=birim_id)
    except Birim.DoesNotExist:
        return None
    m = re.match(r"^\s*(\d{4})\s*/\s*(\d+)\s*$", str(dosya_no or ""))
    if not m:
        return None
    values = {"dosyaYil": m.group(1), "dosyaNo": dosya_no}
    payload = dosya_core.build_payload_genel(values, birim.yargi_turu, birim.turu2, 0, birim_id=birim_id)
    veri = dosya_core._post_eszamanli_korumali(dosya_core._arka_plan_istek, motor, dosya_core.ENDPOINT, payload, log)
    try:
        kayitlar = veri[0] if isinstance(veri, list) else []
        return kayitlar[0].get("dosyaId") if kayitlar else None
    except Exception:
        return None


def _evrak_ham_indir(motor, dosya_id, evrak_id, log_fn, timeout=90):
    """`dosya_core.evrak_html_indir`'in ham-bayt eşdeğeri (bkz. modül başlığı
    ve barkod_sorgu.py::_evrak_pdf_indir — AYNI istek sırası/desen).
    Döner: (content_type:str, ham_bayt:bytes)."""
    sarici = dosya_core._arka_plan_istek
    dosya_core._post_eszamanli_korumali(sarici, motor, dosya_core.DOC_VIEWER_HAZIRLIK_ENDPOINT, {}, log_fn)
    qs = urllib.parse.urlencode({"evrakId": evrak_id, "dosyaId": dosya_id})
    url = f"{motor.base}/{dosya_core.EVRAK_ICERIK_YOLU}?{qs}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "*/*")
    req.add_header("referer", f"{motor.base}/dosya-sorgulama")
    with sarici():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = r.read()
    if status >= 400:
        raise RuntimeError(f"Evrak indirilemedi (HTTP {status})")
    return content_type, data


def _evrak_listesi_duzlestir(evraklar):
    """`ekEvrakListesi` alt-evraklarını (bkz. modül başlığı — PARENT'TEN
    FARKLI, ayrı içerikli belgeler) ana listeye kendi evrakId'siyle AYRI
    birer kayıt olarak katar; `birimEvrakNo`'ya '-ekN' eklenir ki dosya
    adında parent'la ÇAKIŞMASIN."""
    duz = []
    for e in evraklar:
        duz.append(e)
        for ek in (e.get("ekEvrakListesi") or []):
            if not isinstance(ek, dict) or not ek.get("evrakId"):
                continue
            duz.append({
                "evrakId": ek["evrakId"],
                "tur": f"{e.get('tur', '')} (ek)".strip(),
                "birimEvrakNo": f"{e.get('birimEvrakNo', '')}-ek{ek.get('sira', '')}",
                "onaylandigiTarih": e.get("onaylandigiTarih"),
            })
    return duz


def _pdf_ekle(writer, ham_bayt, ad, log_fn):
    try:
        writer.append(PdfReader(io.BytesIO(ham_bayt)))
        return True
    except Exception as e:
        log_fn(f"  ⚠️ '{ad}' PDF'e eklenemedi (bozuk/okunamayan PDF): {e}")
        return False


def _html_pdf_ekle(writer, sayfa, html, ad, gecici_klasor, log_fn):
    """baro_pulu_makbuzu_indiren.py::_html_pdf_yap İLE AYNI Playwright
    deseni — HTML'i geçici dosyaya yazıp file:// ile açıp PDF'e basar."""
    fd, tmp_html = tempfile.mkstemp(suffix=".html", dir=gecici_klasor)
    tmp_pdf = tmp_html[:-5] + ".pdf"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        sayfa.goto("file:///" + tmp_html.replace(os.sep, "/"), wait_until="load")
        sayfa.pdf(path=tmp_pdf, print_background=True, format="A4")
        with open(tmp_pdf, "rb") as f:
            return _pdf_ekle(writer, f.read(), ad, log_fn)
    except Exception as e:
        log_fn(f"  ⚠️ '{ad}' HTML->PDF çevrilemedi: {e}")
        return False
    finally:
        for p in (tmp_html, tmp_pdf):
            try:
                os.remove(p)
            except Exception:
                pass


def _gorsel_pdf_ekle(writer, ham_bayt, ad, log_fn):
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(ham_bayt)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PDF")
        return _pdf_ekle(writer, buf.getvalue(), ad, log_fn)
    except Exception as e:
        log_fn(f"  ⚠️ '{ad}' görüntü PDF'e çevrilemedi: {e}")
        return False


def _yer_tutucu_ekle(writer, ad, content_type, log_fn):
    """Tanınmayan/dönüştürülemeyen bir evrak için birleştirilmiş PDF'e TEK
    sayfalık bir işaret sayfası ekler — UYDURMA render YAPILMAZ (bkz. modül
    başlığı); orijinal ham dosya yine de klasöre kaydedilmiş olur."""
    try:
        import fitz  # PyMuPDF — projede zaten bağımlılık (UDF Converter GUI/converter.py)
        doc = fitz.open()
        sayfa = doc.new_page()
        metin = (f"Bu evrak PDF'e otomatik dönüştürülemedi.\n\n"
                 f"Ad: {ad}\nİçerik türü: {content_type or 'bilinmiyor'}\n\n"
                 "Orijinal dosya, bu dosyanın klasöründe ayrıca duruyor.")
        sayfa.insert_text((50, 72), metin, fontsize=11)
        buf = doc.tobytes()
        doc.close()
        return _pdf_ekle(writer, buf, ad, log_fn)
    except Exception as e:
        log_fn(f"  ⚠️ İşaret sayfası eklenemedi ({ad}): {e}")
        return False


def _evrak_kumesini_isle(motor, sayfa, dosya_id, evraklar, klasor, log, kontrol=None):
    """TEK bir dosyanın verilen evrak alt-kümesini indirir + `PdfWriter`'a
    ekler (bkz. modül başlığı — format-özel dal seçimi). `calistir` (TÜM
    evrak, çoklu dosya) ve `evrak_kumesini_indir_ve_birlestir` (kullanıcının
    işaretlediği alt küme, TEK dosya — Dosya Görüntüle'nin 'Evraklar'
    sekmesi) bu ÇEKİRDEĞİ paylaşır. Döner: (indirilen:int, writer:PdfWriter)."""
    os.makedirs(klasor, exist_ok=True)
    writer = PdfWriter()
    indirilen = 0
    for evrak in evraklar:
        if kontrol:
            kontrol.tur_bitti()
            if not kontrol.nokta():
                break
        evrak_id = evrak.get("evrakId")
        if not evrak_id:
            continue
        no = evrak.get("birimEvrakNo", "")
        ad_govde = f"{_guvenli_ad(evrak.get('tur') or 'evrak')} ({no})" if no \
            else _guvenli_ad(evrak.get("tur") or "evrak")
        try:
            content_type, ham = _evrak_ham_indir(motor, dosya_id, evrak_id, log)
        except Exception as e:
            log(f"  ⚠️ '{ad_govde}' indirilemedi: {e}")
            continue

        uzanti = _ICERIK_UZANTI.get(content_type)
        udf_mu = False
        if not uzanti:
            if ham[:4] == b"%PDF":
                uzanti = ".pdf"
            elif udf_pdf.udf_zip_mi(ham):
                uzanti = ".udf"
                udf_mu = True
            else:
                uzanti = ".bin"
        ham_yol = os.path.join(klasor, f"{ad_govde}{uzanti}")
        with open(ham_yol, "wb") as f:
            f.write(ham)
        indirilen += 1
        log(f"  ✅ {os.path.basename(ham_yol)}")

        if uzanti == ".pdf":
            _pdf_ekle(writer, ham, ad_govde, log)
        elif content_type == "text/html":
            _html_pdf_ekle(writer, sayfa, ham.decode("utf-8", "replace"), ad_govde, klasor, log)
        elif content_type in _GORSEL_TURLERI:
            _gorsel_pdf_ekle(writer, ham, ad_govde, log)
        elif udf_mu:
            # UDF (zip+content.xml) -> PDF (bkz. udf_pdf.py modül başlığı — bu
            # HİÇ CANLI GÖZLENMEDİ şu ana kadar, yalnız güvenlik ağı).
            try:
                _pdf_ekle(writer, udf_pdf.udf_pdf_uret(ham), ad_govde, log)
            except Exception as e:
                log(f"  ⚠️ '{ad_govde}' UDF->PDF çevrilemedi: {e}")
                _yer_tutucu_ekle(writer, ad_govde, content_type, log)
        else:
            _yer_tutucu_ekle(writer, ad_govde, content_type, log)

        time.sleep(0.3)
    return indirilen, writer


def _birlesik_pdf_yaz(writer, klasor, klasor_adi, log):
    if len(writer.pages) == 0:
        return None
    birlesik_yol = os.path.join(klasor, f"{klasor_adi} - Birlesik.pdf")
    with open(birlesik_yol, "wb") as f:
        writer.write(f)
    log(f"  📎 Birleştirilmiş PDF: {os.path.basename(birlesik_yol)} ({len(writer.pages)} sayfa)")
    return birlesik_yol


def calistir(secili_kayitlar, hedef_kok, log_fn=None, kontrol=None):
    """`secili_kayitlar`: `dosyalarim_db_listele`'nin döndürdüğü kayıt
    sözlükleri (en az `birimId`/`dosyaNo`/`birimAdi` taşımalı — Hukuk
    Dosyalarım ekranındaki seçili satırlar). `hedef_kok`: kullanıcının
    seçtiği klasör — HER dosya için altında kendi alt klasörü açılır, TÜM
    evrakları oradan orijinal formatında indirilir + tek bir 'Birlesik.pdf'
    içinde birleştirilir. Döner: satır başına sonuç sözlüğü listesi."""
    from playwright.sync_api import sync_playwright

    log = log_fn or print
    os.makedirs(hedef_kok, exist_ok=True)
    motor = dosya_core.SorguMotoru(log)
    sonuclar = []

    # Faz 1: TÜM taze dosyaId'leri Playwright AÇILMADAN ÖNCE çöz. Canlı testte
    # bulundu (2026-08-27): Playwright'ın sync API'si aynı thread'te bir
    # asyncio/greenlet bağlamı kuruyor; o bağlam İÇİNDE Django ORM çağrısı
    # (`_dosya_id_taze_coz` -> `Birim.objects.get`) "SynchronousOnlyOperation"
    # ile reddediliyor. `evrak_listesi_getir`/indirme HTTP çağrıları Django'ya
    # dokunmadığından bunlar Playwright bağlamı İÇİNDE sorunsuz kalabilir.
    taze_id = {}
    for rec in secili_kayitlar:
        birim_id = rec.get("birimId")
        taze_id[id(rec)] = _dosya_id_taze_coz(motor, birim_id, rec.get("dosyaNo"), log) if birim_id else None

    with sync_playwright() as pw:
        tarayici = pw.chromium.launch(headless=True)
        sayfa = tarayici.new_page()
        try:
            for i, rec in enumerate(secili_kayitlar, 1):
                if kontrol:
                    kontrol.tur_bitti()
                    if not kontrol.nokta():
                        log("⏹ Durduruldu.")
                        break
                birim_adi = rec.get("birimAdi", "")
                dosya_no = rec.get("dosyaNo", "")
                birim_id = rec.get("birimId")
                log(f"\n▶ [{i}/{len(secili_kayitlar)}] {birim_adi} {dosya_no}")
                sonuc = {"Mahkeme": birim_adi, "Dosya No": dosya_no, "Durum": "", "Detay": ""}
                try:
                    if not birim_id:
                        sonuc.update(Durum="⚠️ Atlandı", Detay="birim id yok")
                        sonuclar.append(sonuc)
                        continue

                    dosya_id = taze_id.get(id(rec))
                    if not dosya_id:
                        log("  ⚠️ Dosya UYAP'ta bulunamadı.")
                        sonuc.update(Durum="⚠️ Atlandı", Detay="dosya bulunamadı")
                        sonuclar.append(sonuc)
                        continue

                    evraklar_ham = dosya_core.evrak_listesi_getir(
                        dosya_id, log_fn=log, istek_sarici=dosya_core._arka_plan_istek)
                    evraklar = _evrak_listesi_duzlestir(evraklar_ham)
                    if not evraklar:
                        log("  ℹ️ Evrak yok.")
                        sonuc.update(Durum="ℹ️ Evrak yok", Detay="")
                        sonuclar.append(sonuc)
                        continue

                    klasor_adi = f"{_guvenli_ad(birim_adi)} {_guvenli_ad(dosya_no)}"
                    klasor = os.path.join(hedef_kok, klasor_adi)
                    indirilen, writer = _evrak_kumesini_isle(motor, sayfa, dosya_id, evraklar, klasor, log, kontrol)
                    _birlesik_pdf_yaz(writer, klasor, klasor_adi, log)

                    sonuc.update(Durum="✅ İndirildi",
                                 Detay=f"{indirilen}/{len(evraklar)} evrak — {klasor}")
                    sonuclar.append(sonuc)
                except Exception as e:
                    log(f"  ❌ Hata: {e}")
                    sonuc.update(Durum="❌ Hata", Detay=str(e))
                    sonuclar.append(sonuc)
                time.sleep(SATIR_ARASI_SN)
        finally:
            tarayici.close()

    basarili = sum(1 for s in sonuclar if s["Durum"].startswith("✅"))
    log(f"\n{'=' * 60}\n✅ Tamamlandı: {basarili}/{len(secili_kayitlar)} dosya. Klasör: {hedef_kok}")
    return sonuclar


def evrak_kumesini_indir_ve_birlestir(dosya_id, evraklar, klasor, klasor_adi, log_fn=None, kontrol=None):
    """`calistir`'in TEK dosya + kullanıcının İŞARETLEDİĞİ evrak alt-kümesi
    hâli — "Dosya Görüntüle" penceresindeki 'Evraklar' sekmesi için (kullanıcı
    isteği, 2026-08-27: "checkbox ile indirip indirmemeye karar vereceğim").
    `dosya_id` ÇAĞIRAN tarafından zaten çözülmüş/taze olmalı (bkz. modül
    başlığı — Dosya Görüntüle akışı zaten `rec['dosyaId']`'yi canlı sorguda
    doğrulamış oluyor). Döner: (indirilen:int, toplam:int, birlesik_pdf_yolu|None)."""
    from playwright.sync_api import sync_playwright

    log = log_fn or print
    motor = dosya_core.SorguMotoru(log)
    with sync_playwright() as pw:
        tarayici = pw.chromium.launch(headless=True)
        sayfa = tarayici.new_page()
        try:
            indirilen, writer = _evrak_kumesini_isle(motor, sayfa, dosya_id, evraklar, klasor, log, kontrol)
        finally:
            tarayici.close()
    birlesik_yol = _birlesik_pdf_yaz(writer, klasor, klasor_adi, log)
    log(f"✅ Tamamlandı: {indirilen}/{len(evraklar)} evrak. Klasör: {klasor}")
    return indirilen, len(evraklar), birlesik_yol


def evrak_onizle(dosya_id, evrak, log_fn=None):
    """Tek bir evrakı geçici bir dosyaya indirip OS'un varsayılan
    görüntüleyicisiyle açar (`os.startfile`). UDF ise ÖNCE PDF'e çevrilir
    (Windows'ta .udf açacak bir program YOKTUR) — çevrilemezse ham .udf
    yine de açılmaya ÇALIŞILIR (kullanıcı en azından dosyanın var olduğunu
    görsün, sessizce hiçbir şey yapmamaktansa)."""
    log = log_fn or print
    motor = dosya_core.SorguMotoru(log)
    content_type, ham = _evrak_ham_indir(motor, dosya_id, evrak.get("evrakId"), log)
    uzanti = _ICERIK_UZANTI.get(content_type)
    if not uzanti:
        if ham[:4] == b"%PDF":
            uzanti = ".pdf"
        elif udf_pdf.udf_zip_mi(ham):
            try:
                ham = udf_pdf.udf_pdf_uret(ham)
                uzanti = ".pdf"
            except Exception as e:
                log(f"⚠️ UDF önizleme için PDF'e çevrilemedi, ham .udf açılacak: {e}")
                uzanti = ".udf"
        else:
            uzanti = ".bin"
    ad = _guvenli_ad(evrak.get("tur") or "evrak")
    fd, yol = tempfile.mkstemp(suffix=f" {ad}{uzanti}", prefix="uyap_evrak_")
    with os.fdopen(fd, "wb") as f:
        f.write(ham)
    os.startfile(yol)
    return yol
