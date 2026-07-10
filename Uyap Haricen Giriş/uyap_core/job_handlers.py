#!/usr/bin/env python3
"""
UYAP İş Türü İşleyicileri
-------------------------
Gerçek UYAP iş mantığı BURADA yaşar. Her işleyici @job_type("ad") ile kaydedilir ve bir
JobContext alır:

    @job_type("benim_isim")
    async def _benim_isim(ctx):
        ctx.progress(total=N)
        for i, item in enumerate(ctx.params["liste"], 1):
            ctx.check_cancel()                     # iptal noktası
            resp = await ctx.uyap("POST", "bir_endpoint.ajx", json={...}, write=True)
            ctx.progress(done=i, message=f"{i}/{N}")
            ctx.log(f"{item}: {resp.status_code}")
        return {"ozet": "..."}                      # JSON'a çevrilebilir sonuç

Kurallar:
  • Uzun döngülerde DÜZENLİ ctx.check_cancel() çağır (kullanıcı iptal edebilsin).
  • Durumu değiştiren UYAP çağrılarında write=True ver (sıraya girer, oturumu bozmaz).
  • Sonuç JSON-serileştirilebilir olmalı (dict/list/str/sayı). Büyük binary döndürme.
"""

import base64
import asyncio

from .jobs import job_type, JobCancelled


# ─────────────────────────────────────────────────────────────────────────────
# DEMO / sağlık kontrolü: gerçek UYAP'a dokunmaz. Kuyruğun uçtan uca çalıştığını
# (ilerleme + iptal + log) test etmek için. GUI entegrasyonunu bununla deneyebilirsin.
# ─────────────────────────────────────────────────────────────────────────────
@job_type("bekle")
async def _bekle(ctx):
    """params: {adim: int=5, saniye: float=1.0}. Her adımda ilerleme bildirir, iptal edilebilir."""
    adim = int(ctx.params.get("adim", 5))
    saniye = float(ctx.params.get("saniye", 1.0))
    ctx.progress(done=0, total=adim, message="başlıyor")
    for i in range(1, adim + 1):
        ctx.check_cancel()
        await asyncio.sleep(saniye)
        ctx.progress(done=i, message=f"{i}/{adim} adım")
        ctx.log(f"adım {i} tamam")
    return {"tamamlanan_adim": adim}


# ─────────────────────────────────────────────────────────────────────────────
# GERÇEK MODÜLLER — İSKELET. Aşağıdakiler UYAP endpoint'leri belirlenince doldurulacak.
# Şimdilik beklenen parametre şeklini belgeler ve "henüz uygulanmadı" döndürür; böylece
# GUI tarafı kuyruk akışını (gönder→durum→sonuç) bunlarla baştan kurabilir.
# ─────────────────────────────────────────────────────────────────────────────
def _belge_coz(map_, anahtar):
    """vekalet/dayanak haritasından bir belge çözer: {filename, b64} → {filename, bytes}.
    Anahtar hem str hem orijinal tiple aranır (JSON'da anahtarlar string olabilir)."""
    if not isinstance(map_, dict):
        return None
    d = map_.get(str(anahtar)) or map_.get(anahtar)
    if not isinstance(d, dict) or not d.get("b64"):
        return None
    try:
        return {"filename": d.get("filename"), "bytes": base64.b64decode(d["b64"])}
    except Exception:
        return None


@job_type("coklu_takip_ac")
async def _coklu_takip_ac(ctx):
    """UYAP MTS çoklu takip açma — Kararlı/MTS programının canlı bağlantı üzerindeki hâli.

    params:
      takipler : [ {dosya_no, alacakli, iban, abone_no, ilamsiz_tutar, aciklama,
                    fatura_tarihi, odeme_tarihi, hizmet_abone_no,
                    borclular:[{ad,soyad,kimlik}],
                    alacak_kalemleri:[{ad,tutar,faiz_oran,faiz_tur}]}, ... ]   (zorunlu)
      il, adliye : varsayılan "İzmir".
      onay_modu  : "yok" | "tek_tek" | "toplu"  (varsayılan "yok").
      vekalet    : { "<alacakli>": {"filename":..., "b64":...} }  (opsiyonel)
      dayanak    : { "<dosya_no>": {"filename":..., "b64":...} }  (opsiyonel)

    Onay, SUNUCUDA değil işi başlatan kullanıcının ekranında alınır: işleyici
    ctx.request_approval(...) ile duraklar, istemci /approve ile karar verir.
    Akış evrak yüklemede biter (ödeme/kesinleştirme YOK).
    """
    from .mts.models import takipler_from_params
    from .mts.takip import prepare, finalize

    p = ctx.params
    takipler = takipler_from_params(p.get("takipler"))
    if not takipler:
        raise ValueError("params.takipler boş — açılacak takip yok.")
    il = p.get("il") or "İzmir"
    adliye = p.get("adliye") or "İzmir"
    onay_modu = (p.get("onay_modu") or "yok").lower()
    vekalet_map = p.get("vekalet") or {}
    dayanak_map = p.get("dayanak") or {}

    def belgeler(t):
        return {"vekalet": _belge_coz(vekalet_map, t.alacakli),
                "dayanak": _belge_coz(dayanak_map, t.dosya_no)}

    n = len(takipler)
    sonuclar = []
    basari = atlanan = hata = 0
    ctx.progress(done=0, total=n, message="başlıyor")

    if onay_modu == "toplu":
        # 1) Hepsini hazırla (sorgu + harç), özetleri topla.
        hazir = []   # [(takip, state, ozet)]
        for i, t in enumerate(takipler, 1):
            ctx.check_cancel()
            ctx.progress(done=i - 1, message=f"hazırlanıyor {i}/{n}: {t.dosya_no}")
            try:
                ozet, state = await prepare(ctx, t, il=il, adliye=adliye)
                hazir.append((t, state, ozet))
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                ctx.log(f"[{t.dosya_no}] Hazırlık hatası: {e}")
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "hata", "mesaj": str(e)})
        if not hazir:
            return {"toplam": n, "basari": 0, "atlanan": 0, "hata": hata,
                    "sonuclar": sonuclar, "ozet": "Hiçbir takip hazırlanamadı."}

        # 2) Tek seferde onay (kullanıcı ekranında); selection = işlenecek dosya_no listesi.
        karar = await ctx.request_approval({"mod": "toplu",
                                            "takipler": [o for (_, _, o) in hazir]})
        secim = karar.get("selection")
        sec_set = set(str(x) for x in secim) if secim is not None else None

        # 3) Seçilenleri tamamla.
        for i, (t, state, ozet) in enumerate(hazir, 1):
            ctx.check_cancel()
            if sec_set is not None and str(t.dosya_no) not in sec_set:
                atlanan += 1
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "atlandı"})
                ctx.progress(done=i)
                continue
            try:
                ctx.progress(message=f"işleniyor {i}/{len(hazir)}: {t.dosya_no}")
                r = await finalize(ctx, t, state, **belgeler(t))
                basari += 1
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "tamam", "dosya_id": r["dosya_id"]})
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                ctx.log(f"[{t.dosya_no}] HATA: {e}")
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "hata", "mesaj": str(e)})
            ctx.progress(done=i)
    else:
        # "yok" veya "tek_tek": her takip için prepare → (gerekiyorsa onay) → finalize.
        for i, t in enumerate(takipler, 1):
            ctx.check_cancel()
            ctx.progress(done=i - 1, message=f"{i}/{n}: {t.dosya_no}")
            try:
                ozet, state = await prepare(ctx, t, il=il, adliye=adliye)
                if onay_modu == "tek_tek":
                    karar = await ctx.request_approval({"mod": "tek_tek", "takip": ozet})
                    if karar.get("decision") == "skip":
                        atlanan += 1
                        sonuclar.append({"dosya_no": t.dosya_no, "durum": "atlandı"})
                        ctx.progress(done=i)
                        continue
                r = await finalize(ctx, t, state, **belgeler(t))
                basari += 1
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "tamam", "dosya_id": r["dosya_id"]})
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                ctx.log(f"[{t.dosya_no}] HATA: {e}")
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "hata", "mesaj": str(e)})
            ctx.progress(done=i)

    ctx.progress(done=n, message=f"bitti: {basari} tamam, {atlanan} atlandı, {hata} hata")
    return {"toplam": n, "basari": basari, "atlanan": atlanan, "hata": hata, "sonuclar": sonuclar}


@job_type("dava_ac")
async def _dava_ac(ctx):
    """params: {davalar: [ {mahkeme, taraf, dava_turu, evraklar?, ...}, ... ]}
    Her dava için UYAP dava açılış sihirbazı (yazma) sırayla yürütülecek.
    """
    raise NotImplementedError(
        "dava_ac henüz uygulanmadı: UYAP dava açılış endpoint'leri eklenecek.")


@job_type("dosya_sorgula_indir")
async def _dosya_sorgula_indir(ctx):
    """params: {sorgu: {...}, indir: bool=false}
    UYAP dosya sorgulama (okuma) + isteğe bağlı evrak/UDF indirme. İndirilen dosyalar
    ileride 'dosya transfer' mesaj tipiyle alan tarafa akacak; şimdilik yalnız sorgu meta'sı.
    """
    raise NotImplementedError(
        "dosya_sorgula_indir henüz uygulanmadı: UYAP dosya sorgu endpoint'i + indirme akışı eklenecek.")


@job_type("sgk_toplu_sorgu")
async def _sgk_toplu_sorgu(ctx):
    """UYAP SGK toplu sorgu — Kararlı/SGK Sorgu programının canlı bağlantı üzerindeki hâli.

    SALT-OKUMA: yazma yok, imza yok, onay yok. Her satır için dosyaId bulunur,
    borçlu seçilir ve seçilen SGK sorguları (7 tür) çalıştırılıp metne dökülür.

    params:
      satirlar : [ {"id": <satır kimliği>, "ad_soyad": "...", "dosya_no": "2025/144223",
                    "gereken": ["kamuCalisani","sskCalisani",...]} , ... ]   (zorunlu)
                 gereken verilmezse 7 sorgunun tümü çalışır.
      sorgu_arasi, satir_arasi, mola_hata_esigi, mola_suresi : opsiyonel hız ayarları.

    Sonuç (artımsal; istemci poll sırasında result.satirlar'ı okuyup Excel'e yazabilir):
      {"satirlar": [ {"id","durum":"tamam"|"sirket"|"hata","sonuclar":{anahtar:metin},...} ],
       "toplam", "basari"}
    """
    from .mts import sgk

    p = ctx.params
    satirlar = p.get("satirlar") or []
    if not satirlar:
        raise ValueError("params.satirlar boş — sorgulanacak satır yok.")
    sorgu_arasi = float(p.get("sorgu_arasi", sgk.VARSAYILAN_SORGU_ARASI))
    satir_arasi = float(p.get("satir_arasi", sgk.VARSAYILAN_SATIR_ARASI))
    mola_esigi = int(p.get("mola_hata_esigi", sgk.VARSAYILAN_MOLA_HATA_ESIGI))
    mola_suresi = float(p.get("mola_suresi", sgk.VARSAYILAN_MOLA_SURESI))

    n = len(satirlar)
    cikti = []                              # artımsal sonuç listesi
    ctx.job.result = {"satirlar": cikti, "toplam": n, "basari": 0}  # poll sırasında görünür
    hata_sayaci = 0
    ctx.progress(done=0, total=n, message="başlıyor")

    async def _mola_gerekiyorsa():
        nonlocal hata_sayaci
        if hata_sayaci >= mola_esigi:
            hata_sayaci = 0
            ctx.log(f"{mola_esigi} hata oluştu — UYAP yükünü azaltmak için {mola_suresi:.0f} sn mola.")
            kalan = mola_suresi
            while kalan > 0:
                ctx.check_cancel()
                await asyncio.sleep(min(1.0, kalan))
                kalan -= 1

    for i, s in enumerate(satirlar, 1):
        ctx.check_cancel()
        sid = s.get("id")
        ad = s.get("ad_soyad") or ""
        dosya_no = s.get("dosya_no") or ""
        gereken = list(s.get("gereken") or sgk.TUM_ANAHTARLAR)
        ctx.progress(done=i - 1, message=f"{i}/{n}: {dosya_no} {ad}")
        await _mola_gerekiyorsa()

        def _hata_satiri(metin):
            return {"id": sid, "durum": "hata", "sonuclar": {a: metin for a in gereken}}

        # Tüzel kişi (şirket) — SGK sorgusu yapılmaz
        if sgk.sirket_mi(ad):
            cikti.append({"id": sid, "durum": "sirket",
                          "sonuclar": {a: sgk.SIRKET_NOT for a in gereken}})
            ctx.log(f"[{dosya_no}] '{ad}' şirket (tüzel kişi) — sorgulanmadı.")
            ctx.progress(done=i)
            continue

        if not dosya_no or "/" not in str(dosya_no):
            cikti.append(_hata_satiri(f"{sgk.HATA_ON} Geçersiz dosya no: {dosya_no}"))
            hata_sayaci += 1
            ctx.progress(done=i)
            continue

        yil, _, sira = str(dosya_no).partition("/")
        try:
            dosya_id, _ = await sgk.dosya_id_bul(ctx, yil.strip(), sira.strip())
        except JobCancelled:
            raise
        except Exception as e:
            cikti.append(_hata_satiri(f"{sgk.HATA_ON} arama: {e}"))
            hata_sayaci += 1
            ctx.progress(done=i)
            continue
        if not dosya_id:
            cikti.append(_hata_satiri(f"{sgk.HATA_ON} Dosya bulunamadı"))
            ctx.log(f"[{dosya_no}] dosya bulunamadı.")
            hata_sayaci += 1
            ctx.progress(done=i)
            continue

        try:
            blist = await sgk.borclular(ctx, dosya_id)
        except JobCancelled:
            raise
        except Exception as e:
            cikti.append(_hata_satiri(f"{sgk.HATA_ON} borçlu: {e}"))
            hata_sayaci += 1
            ctx.progress(done=i)
            continue
        borclu, eslesti = sgk.borclu_sec(blist, ad)
        if not borclu:
            cikti.append(_hata_satiri(f"{sgk.HATA_ON} Borçlu bulunamadı"))
            hata_sayaci += 1
            ctx.progress(done=i)
            continue

        ctx.log(f"[{dosya_no}] -> {borclu['adi']} {borclu['soyadi']}"
                + ("" if eslesti else "  (⚠ isim eşleşmedi, ilk borçlu)"))
        ozetler = await sgk.sgk_sorgula(ctx, dosya_id, borclu["kisiKurumId"],
                                        set(gereken), sorgu_arasi)
        sonuclar = {}
        for a, (ok, metin) in ozetler.items():
            sonuclar[a] = metin
            if not ok:
                hata_sayaci += 1
        cikti.append({"id": sid, "durum": "tamam", "eslesti": eslesti,
                      "borclu": f"{borclu['adi']} {borclu['soyadi']}", "sonuclar": sonuclar})
        ctx.job.result["basari"] = sum(1 for c in cikti if c["durum"] == "tamam")
        ctx.progress(done=i)
        await asyncio.sleep(satir_arasi)

    basari = sum(1 for c in cikti if c["durum"] == "tamam")
    ctx.progress(done=n, message=f"bitti: {basari}/{n} satır sorgulandı")
    return {"satirlar": cikti, "toplam": n, "basari": basari}
