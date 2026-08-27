# -*- coding: utf-8 -*-
"""
Baro Pulu Makbuzu — toplu indirici
===================================
Excel'deki (A: icra dairesi/birim adı, B: dosya no) her satır için:
  1. birim adı        -> birimId          (icra_core.birim_id_bul, önbellekli)
  2. birimId+yıl+sıra  -> dosyaId          (search_phrase_detayli.ajx)
  3. dosyaId           -> evrak listesi    (dosya_core.evrak_listesi_getir, sayfalı)
  4. "tur" alanı == "Vekalet Pulu Makbuzu" olan evrak(lar) filtrelenir
     (bir dosyada birden fazla olabilir — HEPSİ indirilir, kullanıcı kararı)
  5. her eşleşen evrak için ham HTML indirilir (dosya_core.evrak_html_indir)
  6. HTML, Playwright (headless Chromium) ile PDF'e basılır

Akış, Logger'in "Session'u module çevir" ile yakaladığı canlı UYAP oturumundan
(2026-07-23, `Panel/modules/logger_data/ag_kaydi.log`) doğrulandı. Tüm istekler
127.0.0.1:8800 yerel ofis proxy'sine gider; ofis (uyap_app.py) canlı e-imza
UYAP oturumuyla iletir — bu betiğin kendisi tarayıcı/Playwright'ı YALNIZCA
indirilen HTML'i PDF'e basmak için kullanır (UYAP'a Playwright ile gidilmez).

Doğrudan çalıştır:  python baro_pulu_makbuzu_indiren.py ["Dosya Sorgulama.xlsx"]

Panel içinden (Üretilen Modüller): EXCEL_GIRDI + KLASOR_GIRDI sayesinde
uretilmis_runner.py otomatik olarak "Dosya Seç…" + "Klasör Seç…" düğmelerini
gösterir; Çalıştır'a basınca excel_isle(excel_yolu, klasor, log_fn=...) çağrılır.
"""

import os
import re
import sys
import tempfile
import time

import openpyxl

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import icra_core  # noqa: E402  (birim_id_bul, birim_listesi_getir, SorguMotoru, BIRIM_TURU2/3)
import dosya_core  # noqa: E402  (evrak_listesi_getir, evrak_html_indir, öncelik geçidi)

SorguMotoru = icra_core.SorguMotoru
BIRIM_TURU2 = icra_core.BIRIM_TURU2
BIRIM_TURU3 = icra_core.BIRIM_TURU3


def _yerel_yetki_jetonunu_kur():
    """Bu betik Panel'in İÇİNDE (uyap_app ile aynı süreçte) çalışıyorsa ofis
    zaten süreç-global bir urllib opener kurup jetonu otomatik ekliyordur
    (bkz. uyap_proxy.py::_install_local_token_opener) — bu durumda burada
    yapılan da onunla aynı jetonu taşıyacağından ZARARSIZDIR. Ama bu betik
    BAĞIMSIZ bir `python ...py` süreci olarak çalıştırılırsa (bu dosyanın
    normal kullanım şekli) o opener hiç kurulmamıştır; ofis reddeder:
    'Yerel yetki jetonu gerekli (tarayıcı-dışı yerel istemci)'. Çözüm: ofisin
    kullanıcıya özel jeton dosyasını (%LOCALAPPDATA%\\UyapIcra\\gw_local_token)
    okuyup aynı başlığı (X-Uyap-Local-Token) ekleyen bir opener kur — bu,
    uyap_proxy.py'nin ayrı-süreç istemciler için zaten belgelediği yoldur."""
    import urllib.request

    token_yolu = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UyapIcra", "gw_local_token")
    try:
        with open(token_yolu, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        return
    if not token:
        return

    class _JetonEnjektor(urllib.request.BaseHandler):
        def http_request(self, req):
            try:
                if req.host and req.host.split(":")[0] in ("127.0.0.1", "localhost") \
                        and not req.has_header("X-uyap-local-token"):
                    req.add_unredirected_header("X-Uyap-Local-Token", token)
            except Exception:
                pass
            return req

        https_request = http_request

    urllib.request.install_opener(urllib.request.build_opener(_JetonEnjektor()))


_yerel_yetki_jetonunu_kur()

# Panel runner'ı (modules/uretilmis_runner.py) bu iki sözlüğü görünce sırasıyla
# "Dosya Seç…" ve "Klasör Seç…" düğmelerini otomatik kurar; Çalıştır'a basılınca
# EXCEL_GIRDI["fonksiyon"] (excel_isle) seçilen excel yolu + seçilen klasörle
# çağrılır (klasör seçilmediyse None geçer, excel_isle kendi varsayılanını kurar).
EXCEL_GIRDI = {
    "etiket": "Dosya Sorgulama Excel'i (A: birim, B: dosya no)",
    "fonksiyon": "excel_isle",
    "uzanti": [("Excel dosyası", "*.xlsx"), ("Tüm dosyalar", "*.*")],
}
KLASOR_GIRDI = {
    "etiket": "PDF'lerin ineceği klasör",
}

HEDEF_TUR = "Vekalet Pulu Makbuzu"

# Dosyalar arası bekleme — UYAP'ı toplu turda yormamak için (bkz. uyap_core/
# job_handlers.py::_sgk_toplu_sorgu'daki aynı desen). Ayrıca her istek zaten
# `dosya_core._arka_plan_istek` üzerinden gider: bir kullanıcı Panel'de o
# sırada tıklarsa öncelik ona geçer, toplu tur otomatik bekler.
SATIR_ARASI_SN = 2.0


def _dosya_no_ayir(dosya_no):
    """'2026/94270' -> (2026, 94270). Format uymazsa (None, None)."""
    m = re.match(r"^\s*(\d{4})\s*/\s*(\d+)\s*$", str(dosya_no or ""))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _guvenli_ad(s):
    """Windows dosya adında yasak karakterleri temizler."""
    return re.sub(r'[\\/:*?"<>|]', "-", str(s or "")).strip()


def _dosya_id_coz(motor, birim_id, yil, sira, log):
    """birimId+yıl+sıra -> dosyaId (search_phrase_detayli.ajx). birim_id VERİLEREK
    aranır (motor.dosya_id_bul'un aksine) — aynı yıl/sıra farklı birimlerde de
    olabileceğinden birimsiz arama YANLIŞ dosyayı döndürebilir."""
    payload = {
        "dosyaDurumKod": 0, "pageSize": 500, "pageNumber": 1,
        "dosyaYil": yil, "dosyaSira": sira,
        "birimId": birim_id, "birimTuru2": BIRIM_TURU2, "birimTuru3": BIRIM_TURU3,
    }
    veri = dosya_core._post_eszamanli_korumali(
        dosya_core._arka_plan_istek, motor, icra_core.ENDPOINT, payload, log)
    try:
        kayitlar = veri[0] if isinstance(veri, list) else []
        if kayitlar:
            return kayitlar[0].get("dosyaId")
    except Exception:
        pass
    return None


def excel_oku(xlsx_yolu):
    """A: Birim/İcra Dairesi, B: Dosya No sütunlarını okur (başlık satırına göre
    kolon bulunur — 'Birim'/'Dosya No' içeren başlıklar aranır; bulunamazsa ilk
    iki sütun kullanılır). Döner: list[(birim_adi, dosya_no)]."""
    wb = openpyxl.load_workbook(xlsx_yolu, read_only=True, data_only=True)
    ws = wb.active
    satirlar = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    if not satirlar:
        return []
    basliklar = [str(h or "").strip().lower() for h in satirlar[0]]
    try:
        bi = next(i for i, h in enumerate(basliklar) if "birim" in h or "daire" in h)
    except StopIteration:
        bi = 0
    try:
        di = next(i for i, h in enumerate(basliklar) if "dosya no" in h)
    except StopIteration:
        di = 1

    sonuc = []
    for row in satirlar[1:]:
        if bi >= len(row) or di >= len(row):
            continue
        birim, dosya_no = row[bi], row[di]
        if not birim or not dosya_no:
            continue
        sonuc.append((str(birim).strip(), str(dosya_no).strip()))
    return sonuc


def calistir(xlsx_yolu, cikti_klasoru=None, log_fn=None, kontrol=None):
    """Excel'deki her (birim, dosya no) için 'Vekalet Pulu Makbuzu' türündeki
    TÜM evrakları HTML olarak indirir ve PDF'e çevirir.
    Döner: satır başına bir sözlük listesi (Panel'de tablo olarak gösterilir):
        [{"Birim": ..., "Dosya No": ..., "Durum": ..., "Detay": ...}, ...]"""
    from playwright.sync_api import sync_playwright

    log = log_fn or print
    cikti_klasoru = cikti_klasoru or os.path.join(
        os.path.dirname(os.path.abspath(xlsx_yolu)), "Baro Pulu Makbuzlari")
    os.makedirs(cikti_klasoru, exist_ok=True)

    satirlar = excel_oku(xlsx_yolu)
    log(f"📄 {len(satirlar)} satır okundu -> {cikti_klasoru}")

    icra_core.birim_listesi_getir(log)  # birim adı -> id önbelleğini doldur
    motor = SorguMotoru(log)
    sonuclar = []
    sayac = {"basarili": 0, "atlanan": 0, "hatali": 0}

    def _isaretle(satir, durum, detay, anahtar):
        satir["Durum"], satir["Detay"] = durum, detay
        sayac[anahtar] += 1
        sonuclar.append(satir)

    with sync_playwright() as pw:
        tarayici = pw.chromium.launch(headless=True)
        sayfa = tarayici.new_page()
        try:
            for i, (birim_adi, dosya_no) in enumerate(satirlar, 1):
                if kontrol:
                    kontrol.tur_bitti()
                    if not kontrol.nokta():
                        log("⏹ Durduruldu.")
                        break
                log(f"\n▶ [{i}/{len(satirlar)}] {birim_adi} {dosya_no}")
                satir = {"Birim": birim_adi, "Dosya No": dosya_no, "Durum": "", "Detay": ""}
                try:
                    yil, sira = _dosya_no_ayir(dosya_no)
                    if yil is None:
                        log(f"  ⚠️ Dosya no formatı tanınmadı: {dosya_no!r}")
                        _isaretle(satir, "⚠️ Atlandı", "dosya no formatı", "atlanan")
                        continue

                    birim_id = icra_core.birim_id_bul(birim_adi)
                    if not birim_id:
                        log(f"  ⚠️ Birim bulunamadı: {birim_adi!r}")
                        _isaretle(satir, "⚠️ Atlandı", "birim bulunamadı", "atlanan")
                        continue

                    dosya_id = _dosya_id_coz(motor, birim_id, yil, sira, log)
                    if not dosya_id:
                        log("  ⚠️ Dosya bulunamadı.")
                        _isaretle(satir, "⚠️ Atlandı", "dosya bulunamadı", "atlanan")
                        continue

                    evraklar = dosya_core.evrak_listesi_getir(
                        dosya_id, log_fn=log, istek_sarici=dosya_core._arka_plan_istek)
                    eslesen = [e for e in evraklar if e.get("tur") == HEDEF_TUR]
                    if not eslesen:
                        log(f"  ℹ️ '{HEDEF_TUR}' evrakı yok ({len(evraklar)} evrak tarandı).")
                        _isaretle(satir, "⚠️ Atlandı", "evrak yok", "atlanan")
                        continue

                    dosya_adlari = []
                    for evrak in eslesen:
                        evrak_id = evrak.get("evrakId")
                        tarih = (evrak.get("onaylandigiTarih") or "").replace("/", ".")
                        no = evrak.get("birimEvrakNo", "")

                        html = dosya_core.evrak_html_indir(
                            dosya_id, evrak_id, log_fn=log,
                            istek_sarici=dosya_core._arka_plan_istek)

                        ad = (f"{_guvenli_ad(birim_adi)} {_guvenli_ad(dosya_no)} "
                              f"Vekalet Pulu Makbuzu {tarih} ({no}).pdf")
                        hedef = os.path.join(cikti_klasoru, ad)
                        _html_pdf_yap(sayfa, html, hedef)

                        log(f"  ✅ {ad}")
                        dosya_adlari.append(ad)

                    _isaretle(satir, "✅ İndirildi", "; ".join(dosya_adlari), "basarili")

                except Exception as e:
                    log(f"  ❌ Hata: {e}")
                    _isaretle(satir, "❌ Hata", str(e), "hatali")

                time.sleep(SATIR_ARASI_SN)
        finally:
            tarayici.close()

    log(f"\n{'=' * 60}\n"
        f"✅ Başarılı: {sayac['basarili']}   "
        f"⚠️ Atlanan: {sayac['atlanan']}   "
        f"❌ Hatalı: {sayac['hatali']}\n"
        f"Klasör: {cikti_klasoru}")
    return sonuclar


def excel_isle(excel_yolu, cikti_klasoru=None, log_fn=print, kontrol=None):
    """Panel runner'ının (EXCEL_GIRDI/KLASOR_GIRDI) çağırdığı giriş noktası —
    calistir() ile birebir aynı, yalnızca imza runner'ın çağrı biçimiyle uyumlu."""
    return calistir(excel_yolu, cikti_klasoru, log_fn, kontrol=kontrol)


def _html_pdf_yap(sayfa, html, hedef_pdf_yolu):
    """Ham evrak HTML'ini geçici bir .html dosyasına yazıp file:// ile açar
    (page.set_content yerine — evrak içindeki IndexedDB/sessionStorage
    kullanan enjekte script'in about:blank yerine gerçek bir origin'de
    çalışması için), sonra PDF'e basar."""
    fd, tmp_html = tempfile.mkstemp(suffix=".html")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        sayfa.goto("file:///" + tmp_html.replace(os.sep, "/"), wait_until="load")
        sayfa.pdf(path=hedef_pdf_yolu, print_background=True, format="A4")
    finally:
        try:
            os.remove(tmp_html)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _varsayilan_xlsx = os.path.normpath(os.path.join(_HERE, "..", "..", "Dosya Sorgulama.xlsx"))
    _xlsx = sys.argv[1] if len(sys.argv) > 1 else _varsayilan_xlsx
    calistir(_xlsx, log_fn=print)
