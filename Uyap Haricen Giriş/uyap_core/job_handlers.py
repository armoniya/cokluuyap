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
import traceback

from .jobs import job_type, JobCancelled


def _hata_ayrintisi(e):
    """str(e) BOŞ dönebilen istisnalar için (ör. mesajsız asyncio.CancelledError,
    bare AssertionError) hâlâ tanılanabilir bir log satırı üretir: tip + mesaj +
    tam traceback. 'HATA: ' yazıp hiçbir şey göstermemek yerine kullanılır."""
    tip = type(e).__name__
    detay = str(e).strip() or "(mesaj yok)"
    tb = traceback.format_exc()
    return f"{tip}: {detay}", tb


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


async def _asama_onay(ctx, kalemler, onay_modu, asama_adi, ozet_fn):
    """3 aşamalı onay akışının (takip aç / öde / tebligat) ORTAK onay mantığı.

    kalemler: [(takip, dosya_id, ozet, state), ...] — bir ÖNCEKİ aşamayı BAŞARIYLA
    geçmiş dosyalar (ör. ödeme aşaması için: evrakı gönderilmiş, henüz ödenmemiş
    dosyalar). onay_modu: 'yok' (hepsi otomatik devam), 'tek_tek' (her dosya için ayrı
    onay/atla), 'toplu' (tek ekranda tümü önizlenir, devam edecekler seçilir).

    ÖNEMLİ: 'toplu' burada takip-açmadaki gibi "iptal" ANLAMINA GELMEZ — bu aşamaya
    giren dosyalar bir ÖNCEKİ aşamada zaten gerçekleşmiş (dosya açık / ödenmiş) durumda;
    reddedilen bir kalem YALNIZCA BU aşamayı atlar, önceki kazanım geri alınmaz.

    Dönüş: kalemler ile aynı şekilde, kullanıcının bu aşamaya devam etmesini
    onayladığı alt küme."""
    onay_modu = (onay_modu or "yok").lower()
    if onay_modu == "yok" or not kalemler:
        return kalemler
    if onay_modu == "tek_tek":
        secilenler = []
        for (t, dosya_id, ozet, state) in kalemler:
            ctx.check_cancel()
            karar = await ctx.request_approval({"mod": f"{asama_adi}_tek_tek",
                                                "kalem": ozet_fn(t, ozet, state)})
            if karar.get("decision") == "approve":
                secilenler.append((t, dosya_id, ozet, state))
            else:
                ctx.log(f"[{t.dosya_no}] {asama_adi}: kullanıcı bu aşamayı atladı.")
        return secilenler
    # toplu
    karar = await ctx.request_approval({"mod": f"{asama_adi}_toplu",
                                        "kalemler": [ozet_fn(t, o, s) for (t, _, o, s) in kalemler]})
    secim = karar.get("selection")
    if secim is None:
        return kalemler
    sec_set = set(str(x) for x in secim)
    return [k for k in kalemler if str(k[0].dosya_no) in sec_set]


@job_type("coklu_takip_ac")
async def _coklu_takip_ac(ctx):
    """UYAP MTS çoklu takip açma — Kararlı/MTS programının canlı bağlantı üzerindeki hâli.

    params:
      takipler : [ {dosya_no, alacakli, iban, abone_no, ilamsiz_tutar, aciklama,
                    fatura_tarihi, odeme_tarihi, hizmet_abone_no,
                    borclular:[{ad,soyad,kimlik}],
                    alacak_kalemleri:[{ad,tutar,faiz_oran,faiz_tur}]}, ... ]   (zorunlu)
      il, adliye : varsayılan "İzmir".
      onay_modu          : "yok" | "tek_tek" | "toplu"  (varsayılan "yok") — AŞAMA 1:
                            takip aç (taslak oluştur + evrak gönder).
      odeme_aktif         : bool (varsayılan True)  — AŞAMA 2: harç ödemesi denensin mi?
      odeme_onay_modu     : "yok" | "tek_tek" | "toplu"  (varsayılan "yok") — ödeme onayı.
      tebligat_aktif      : bool (varsayılan False) — AŞAMA 3: tebligat gönderilsin mi?
      tebligat_onay_modu  : "yok" | "tek_tek" | "toplu"  (varsayılan "yok") — tebligat onayı.
      vekalet    : { "<alacakli>": {"filename":..., "b64":...} }  (opsiyonel)
      dayanak    : { "<dosya_no>": {"filename":..., "b64":...} }  (opsiyonel)

    ÜÇ AYRI aşama, ÜÇ AYRI onay turu — her aşama yalnızca bir öncekini BAŞARIYLA geçen
    dosyalarda çalışır (evrakı gönderilmemiş dosyaya ödeme, ödenmemiş dosyaya tebligat
    denenmez). *_aktif=False olan aşama tamamen atlanır; dosya bir önceki durumda
    (açık / ödenmemiş, ya da açık+ödenmiş / tebligatsız) kalır, UYAP'tan elle
    ilerletilebilir. Onay, SUNUCUDA değil işi başlatan kullanıcının ekranında alınır:
    işleyici ctx.request_approval(...) ile duraklar, istemci /approve ile karar verir.

    NOT (kullanıcı talebi, 2026-08-11): ödeme/tebligatın otomatik mi yoksa onaylı mı
    çalışacağı artık mts.takip.HARC_ODEME_AKTIF/TEBLIGAT_GONDER_AKTIF gibi kaynak-kodu
    sabitleriyle DEĞİL, buradaki *_aktif + *_onay_modu parametreleriyle (panelden
    kullanıcının seçtiği) belirlenir.
    """
    from .mts.models import takipler_from_params
    from .mts.takip import prepare, finalize, _odeme_yap, _tebligat_gonder

    p = ctx.params
    takipler = takipler_from_params(p.get("takipler"))
    if not takipler:
        raise ValueError("params.takipler boş — açılacak takip yok.")
    il = p.get("il") or "İzmir"
    adliye = p.get("adliye") or "İzmir"
    onay_modu = (p.get("onay_modu") or "yok").lower()
    odeme_aktif = bool(p.get("odeme_aktif", True))
    odeme_onay_modu = (p.get("odeme_onay_modu") or "yok").lower()
    tebligat_aktif = bool(p.get("tebligat_aktif", False))
    tebligat_onay_modu = (p.get("tebligat_onay_modu") or "yok").lower()
    vekalet_map = p.get("vekalet") or {}
    dayanak_map = p.get("dayanak") or {}

    def belgeler(t):
        return {"vekalet": _belge_coz(vekalet_map, t.alacakli),
                "dayanak": _belge_coz(dayanak_map, t.dosya_no)}

    n = len(takipler)
    sonuc_by_no = {}   # dosya_no(str) -> sonuç dict; aşamalar ilerledikçe güncellenir

    def _sonuc(t):
        return sonuc_by_no.setdefault(str(t.dosya_no), {"dosya_no": t.dosya_no})

    ctx.progress(done=0, total=n, message="başlıyor")

    # ══ AŞAMA 1: Takip aç (taslak + evrak) ══════════════════════════════════════
    acilan = []   # [(takip, dosya_id, ozet, state)] — evrakı BAŞARIYLA gönderilenler
    if onay_modu == "toplu":
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
                mesaj, tb = _hata_ayrintisi(e)
                ctx.log(f"[{t.dosya_no}] Hazırlık hatası: {mesaj}")
                ctx.log(f"[{t.dosya_no}] Traceback:\n{tb}")
                _sonuc(t).update(durum="hata", mesaj=mesaj)

        if hazir:
            karar = await ctx.request_approval({"mod": "toplu",
                                                "takipler": [o for (_, _, o) in hazir]})
            secim = karar.get("selection")
            sec_set = set(str(x) for x in secim) if secim is not None else None

            for i, (t, state, ozet) in enumerate(hazir, 1):
                ctx.check_cancel()
                if sec_set is not None and str(t.dosya_no) not in sec_set:
                    _sonuc(t).update(durum="atlandı")
                    ctx.progress(done=i)
                    continue
                try:
                    ctx.progress(message=f"işleniyor {i}/{len(hazir)}: {t.dosya_no}")
                    r = await finalize(ctx, t, state, **belgeler(t))
                    _sonuc(t).update(durum="acildi", dosya_id=r["dosya_id"],
                                     evrak_sonuc=r["evrak_sonuc"])
                    acilan.append((t, r["dosya_id"], ozet, state))
                except JobCancelled:
                    raise
                except Exception as e:
                    mesaj, tb = _hata_ayrintisi(e)
                    ctx.log(f"[{t.dosya_no}] HATA: {mesaj}")
                    ctx.log(f"[{t.dosya_no}] Traceback:\n{tb}")
                    _sonuc(t).update(durum="hata", mesaj=mesaj)
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
                        _sonuc(t).update(durum="atlandı")
                        ctx.progress(done=i)
                        continue
                r = await finalize(ctx, t, state, **belgeler(t))
                _sonuc(t).update(durum="acildi", dosya_id=r["dosya_id"], evrak_sonuc=r["evrak_sonuc"])
                acilan.append((t, r["dosya_id"], ozet, state))
            except JobCancelled:
                raise
            except Exception as e:
                mesaj, tb = _hata_ayrintisi(e)
                ctx.log(f"[{t.dosya_no}] HATA: {mesaj}")
                ctx.log(f"[{t.dosya_no}] Traceback:\n{tb}")
                _sonuc(t).update(durum="hata", mesaj=mesaj)
            ctx.progress(done=i)

    ctx.progress(message=f"takip açma bitti: {len(acilan)}/{n} dosya açıldı")

    # ══ AŞAMA 2: Ödeme (opsiyonel) ═══════════════════════════════════════════════
    odenen = []   # [(takip, güncel_dosya_id)] — harcı BAŞARIYLA ödenenler
    if not acilan:
        pass
    elif not odeme_aktif:
        ctx.log(f"Ödeme aşaması KAPALI (odeme_aktif=false) — {len(acilan)} açık dosya "
                "ödenmeden bırakıldı, UYAP'tan 'Tamamlanmayan Dosyalar' ekranından elle ödenebilir.")
    else:
        def _odeme_ozet(t, ozet, _state):
            harclar = (ozet or {}).get("harclar", [])
            return {"dosya_no": t.dosya_no, "alacakli": t.alacakli, "harclar": harclar,
                    "toplam_harc": round(sum((h.get("miktar") or 0) for h in harclar), 2)}

        odemeye_gidenler = await _asama_onay(ctx, acilan, odeme_onay_modu, "odeme", _odeme_ozet)
        kaydedilecekler = []   # [(takip, gercek_dosya_no), ...] — DB kaydı AŞAMA 2 BİTTİKTEN
        # SONRA, ayrı bir geçişte yapılır (bkz. altındaki not). Ödeme döngüsünün İÇİNE
        # konursa (önceki sürümde olduğu gibi) her ödemeden sonra 2 ekstra UYAP isteği +
        # 1 bloklayan DB yazımı araya girip BİR SONRAKİ takibin ilk isteğinden önceki
        # boşluğu uzatıyor — kullanıcı bulgusu (2026-08-14, canlı çöküş, ReadError):
        # bu boşluk UYAP/proxy bağlantısını "bayat" bırakıp SONRAKİ dosyanın (ödemeyle
        # İLGİSİZ, prepare() akışındaki) ilk isteğini ReadError ile çökertiyordu. Ödeme
        # döngüsü (asıl PARA hareketi) artık BUNDAN TAMAMEN İZOLE — DB kaydı gecikmesi
        # bu kritik akışın ZAMANLAMASINA hiç karışmaz.
        for (t, dosya_id, ozet, state) in odemeye_gidenler:
            ctx.check_cancel()
            try:
                odeme = await _odeme_yap(ctx, t, dosya_id, state)
                guncel_dosya_id = odeme.get("dosya_id") or dosya_id
                _sonuc(t).update(durum="odendi", dosya_id=guncel_dosya_id,
                                 odeme_sonuc=odeme["odeme_sonuc"],
                                 gercek_dosya_no=odeme["gercek_dosya_no"])
                if odeme["gercek_dosya_no"]:
                    kaydedilecekler.append((t, odeme["gercek_dosya_no"]))
                odenen.append((t, guncel_dosya_id))
            except JobCancelled:
                raise
            except Exception as e:
                mesaj, tb = _hata_ayrintisi(e)
                ctx.log(f"[{t.dosya_no}] ⚠️ Ödeme hatası (dosya AÇIK kaldı, ÖDENMEDİ): {mesaj}")
                ctx.log(f"[{t.dosya_no}] Traceback:\n{tb}")
                _sonuc(t).update(odeme_hata=mesaj)

        if kaydedilecekler:
            from . import dosya_kaydet as _dosya_kaydet
            ctx.log(f"Veritabanı senkronu: {len(kaydedilecekler)} ödenmiş dosya kaydediliyor...")
            for t, gercek_no in kaydedilecekler:
                ctx.check_cancel()
                taraflar = []
                if t.alacakli:
                    taraflar.append(("alacakli", {"tur": "tuzel", "unvan": t.alacakli,
                                                  "mersis_no": None, "vergi_no": "",
                                                  "tckn": None, "ad": "", "soyad": ""}))
                for b in (t.borclular or []):
                    taraflar.append(("borclu", {"tur": "gercek", "ad": b.ad or "",
                                                "soyad": b.soyad or "", "tckn": b.kimlik or None,
                                                "unvan": "", "vergi_no": "", "mersis_no": None}))
                await _dosya_kaydet.kaydet(ctx, gercek_no, taraflar=taraflar, log=ctx.log)

    # ══ AŞAMA 3: Tebligat (opsiyonel, yalnız ödenmiş dosyalarda) ════════════════
    if not odenen:
        pass
    elif not tebligat_aktif:
        ctx.log(f"Tebligat aşaması KAPALI (tebligat_aktif=false) — {len(odenen)} ödenmiş "
                "dosyada tebligat gönderilmedi, UYAP'tan elle gönderilebilir.")
    else:
        def _tebligat_ozet(t, _ozet, _state):
            return {"dosya_no": t.dosya_no, "alacakli": t.alacakli,
                    "borclular": [{"ad": b.ad, "soyad": b.soyad} for b in t.borclular]}

        tebligat_kalemleri = [(t, dosya_id, None, None) for (t, dosya_id) in odenen]
        tebligata_gidenler = await _asama_onay(ctx, tebligat_kalemleri, tebligat_onay_modu,
                                               "tebligat", _tebligat_ozet)
        for (t, dosya_id, _o, _s) in tebligata_gidenler:
            ctx.check_cancel()
            try:
                tebligat_sonuc = await _tebligat_gonder(ctx, t, dosya_id)
                _sonuc(t).update(tebligat_sonuc=tebligat_sonuc)
            except JobCancelled:
                raise
            except Exception as e:
                mesaj, tb = _hata_ayrintisi(e)
                ctx.log(f"[{t.dosya_no}] ⚠️ Tebligat hatası (dosya AÇIK ve ÖDENMİŞ kaldı): {mesaj}")
                ctx.log(f"[{t.dosya_no}] Traceback:\n{tb}")
                _sonuc(t).update(tebligat_hata=mesaj)

    sonuclar = [sonuc_by_no[str(t.dosya_no)] for t in takipler]
    basari = sum(1 for s in sonuclar if s.get("durum") in ("acildi", "odendi"))
    atlanan = sum(1 for s in sonuclar if s.get("durum") == "atlandı")
    hata = sum(1 for s in sonuclar if s.get("durum") == "hata")
    ctx.progress(done=n, message=f"bitti: {basari} tamam, {atlanan} atlandı, {hata} hata")
    return {"toplam": n, "basari": basari, "atlanan": atlanan, "hata": hata, "sonuclar": sonuclar}


@job_type("mts_odenmis_dosyalari_bul")
async def _mts_odenmis_dosyalari_bul(ctx):
    """UYAP'ın 'Tamamlanmayan Dosyalar' ekranını SORGULAR (YALNIZCA OKUMA — hiçbir şey
    göndermez/ödemez) ve verilen takipleri borçlu ad soyadıyla eşleştirip ödemesi
    tamamlanmış (gerçek esas no atanmış) olanları bildirir. MTS 'Excel'e Aktar'
    özelliği için kullanılır (bkz. Panel/modules/mts_takip.py).

    params:
      takipler : coklu_takip_ac ile AYNI şekil (yalnız dosya_no/borclular kullanılır).

    Dönüş: {"sonuclar": [{"dosya_no":..., "gercek_dosya_no": "2026/894734" | None}, ...]}
    """
    from .mts.models import takipler_from_params
    from .mts.takip import odenmis_dosyalari_bul

    p = ctx.params
    takipler = takipler_from_params(p.get("takipler"))
    if not takipler:
        raise ValueError("params.takipler boş.")
    ctx.progress(message="UYAP 'Tamamlanmayan Dosyalar' listesi sorgulanıyor...")
    eslesme = await odenmis_dosyalari_bul(ctx, takipler)
    sonuclar = [{"dosya_no": t.dosya_no, "gercek_dosya_no": eslesme.get(str(t.dosya_no))}
               for t in takipler]
    ctx.progress(message="Sorgu tamamlandı.")
    return {"sonuclar": sonuclar}


@job_type("mts_odeme_tamamla")
async def _mts_odeme_tamamla(ctx):
    """KURTARMA: prepare()+finalize() zaten çalışmış (taslak oluşmuş, evrak
    gönderilmiş) ama ödeme YANLIŞ uç noktayla (davaAcilisOdemeIslemleri_brd.ajx,
    artık terk edildi) EKSİK/YARIM kalmış (2026-08-11 canlı deneme #1 ve #2, dosya_no=23,
    her ikisinde de sadece ₺164 Vekalet Pulu ödendi, ₺836 asıl harç ÖDENMEDİ) bir MTS
    takibi için, YENİ bir taslak OLUŞTURMADAN (yeni ücrete yol açmadan), var olan
    dosya_id'ye karşı DOĞRU uç noktayla (mtsDavaAcilis_brd.ajx, bkz. takip.py _odeme_yap)
    ödemeyi tekrar dener.

    ⚠️ UYARI: mtsDavaAcilis_brd.ajx'in KISMİ ÖDENMİŞ bir dosyaId'ye karşı nasıl davrandığı
    (kalan tutarı mı tahsil ediyor, yoksa TÜM harcı yeniden mi deneyip Vekalet Pulu'nu
    İKİNCİ KEZ mi ücretlendiriyor) HENÜZ BİLİNMİYOR — bu, endpoint'in normal (taslak→
    tek seferde tam ödeme) akışta doğrulanmış olmasından FARKLI bir senaryo. Kullanıcıdan
    açık onay ALINMADAN ve tercihen önce UYAP arayüzünde o dosyanın 'Ödeme Yap' ekranına
    hâlâ girilebildiği (sürecin gerçekten 'ödeme aşamasında' kaldığı) elle doğrulanmadan
    ÇALIŞTIRILMAMALI.

    prepare() taze bir state kurar (harç/taraf — OKUMA ağırlıklı, taslak
    oluşturmaz); _odeme_yap ise verilen VAR OLAN dosya_id'ye karşı çalışır.
    dosya_id'nin süresi dolmuşsa (opak token rotasyonlu, bkz. xml_takip modül
    başlığı) UYAP güvenli şekilde hata döner, YENİ ücret oluşmaz.

    params: {takip: {...tek bir takip dict, coklu_takip_ac.params.takipler[i] ile
             AYNI şekil...}, dosya_id: "<mevcut opak token>", il, adliye}
    Dönüş: {"dosya_id":..., "odeme_sonuc":..., "gercek_dosya_no":...}
    """
    from .mts.models import takip_from_dict
    from .mts.takip import prepare, _odeme_yap

    p = ctx.params
    t = takip_from_dict(p.get("takip"))
    dosya_id = p.get("dosya_id")
    if not t.dosya_no or not dosya_id:
        raise ValueError("params.takip ve params.dosya_id gerekli.")
    il = p.get("il") or "İzmir"
    adliye = p.get("adliye") or "İzmir"

    ctx.log(f"[{t.dosya_no}] KURTARMA: hazırlık (harç/taraf) yeniden hesaplanıyor "
            f"(dosya_id={dosya_id!r} için)...")
    ozet, state = await prepare(ctx, t, il=il, adliye=adliye)

    ctx.log(f"[{t.dosya_no}] Hazırlık tamam. Mevcut dosya_id ile ödeme deneniyor...")
    sonuc = await _odeme_yap(ctx, t, dosya_id, state)
    return {"dosya_id": dosya_id, **sonuc}


@job_type("mts_tebligat_gonder")
async def _mts_tebligat_gonder(ctx):
    """TEK BAŞINA/TEST: Zaten harcı ÖDENMİŞ (mtsDavaAcilis_brd.ajx başarıyla dönmüş) bir
    MTS dosyasına, YENİ bir taslak/ödeme OLUŞTURMADAN, tebligat gönderir (bkz. takip.py
    _tebligat_gonder). coklu_takip_ac bu adımı artık params.tebligat_aktif=true olduğunda
    (panelden kullanıcının seçtiği onay moduyla) kendi içinde çalıştırır; bu iş, kullanıcı
    o seçeneği ilk kez açmadan ÖNCE var olan (zaten ödenmiş) tek bir dosyada izole denemek
    veya coklu_takip_ac içindeki gönderim başarısız olduğunda elle kurtarmak içindir.

    params.dosya_id İSTEĞE BAĞLI: verilmezse, takip.borclular'daki ad-soyadla UYAP
    'Tamamlanmayan Dosyalar' listesinden GÜNCEL opak token otomatik bulunur (bkz.
    takip.py _guncel_dosya_id_bul — xml_takip'teki AYNI, CANLI DOĞRULANMIŞ desen).
    Elle verilirse, ödeme SONRASI UYAP'ın verdiği GÜNCEL token olmalı (taslak oluşturma
    anındaki eski dosya_id DEĞİL) — bkz. takip.py _odeme_yap notu.

    params: {takip: {...tek bir takip dict (en az dosya_no + borclular)...},
             dosya_id: "<opsiyonel, biliniyorsa güncel opak token>"}
    Dönüş: {"dosya_id":..., "ucret":..., "kisi_kurum_idler":..., "sonuc":...} ya da
    gönderilecek taraf yoksa None alanlarıyla.
    """
    from .mts.models import takip_from_dict
    from .mts.takip import _tebligat_gonder, _guncel_dosya_id_bul

    p = ctx.params
    t = takip_from_dict(p.get("takip"))
    if not t.dosya_no:
        raise ValueError("params.takip gerekli (en az dosya_no).")
    dosya_id = p.get("dosya_id")
    if not dosya_id:
        ctx.log(f"[{t.dosya_no}] dosya_id verilmedi — 'Tamamlanmayan Dosyalar' listesinden "
                "borçlu ad-soyadıyla güncel dosya aranıyor...")
        dosya_id = await _guncel_dosya_id_bul(ctx, t)
        ctx.log(f"[{t.dosya_no}] Güncel dosya bulundu.")

    ctx.log(f"[{t.dosya_no}] TEST/KURTARMA: tebligat gönderimi deneniyor (dosya_id={dosya_id!r})...")
    sonuc = await _tebligat_gonder(ctx, t, dosya_id)
    return {"dosya_id": dosya_id, **(sonuc or {"ucret": None, "kisi_kurum_idler": [], "sonuc": None})}


@job_type("normal_tebligat_gonder")
async def _normal_tebligat_gonder(ctx):
    """21/2 UYGUNLUK TARAMASINDA "farklı yöntem gerekli (mernis'e çıkmamış)" çıkan
    dosyalar için NORMAL (21/2 şerhsiz) tebligat TOPLU gönderimi (ÜCRETLİ).

    DÜZELTME (kullanıcı bulgusu, 2026-08-17): İLK sürüm burada YANLIŞLIKLA
    uyap_core.mts.takip._tebligat_gonder'i (MTS'in TARAFA tebligat gönderme
    ekranı, MtsTebligatGonder_brd.ajx) kullanıyordu — bu YANLIŞ mekanizma.
    Doğrusu, teblig_212_gonder'ın (avukatIcraTalepEvrakiGonder.ajx üzerinden
    "İcra/Ödeme Emrinin Tebliğe Çıkartılması" TALEBİ) BİREBİR AYNI akışı,
    yalnızca "tebligatTuru" alanı 2 ("T.K.21/2 Şerhli") yerine 1 ("Normal
    Tebligat") — bkz. uyap_core.teblig_212.gonder.TEBLIGAT_TURU_KODLARI,
    kullanıcının getPortalAvukatTalepTebligatTuruList_brd.ajx CANLI yanıtından
    paylaştığı referans liste. teblig_212.gonder.prepare()/finalize()
    PARAMETRELENDİRİLDİ (varsayılanları ESKİ 21/2 davranışıyla BİREBİR aynı
    kalacak şekilde) — bu iş yalnızca "normal" değerlerini AÇIKÇA geçirir,
    teblig_212_gonder job'ı hiç ETKİLENMEZ.

    teblig_212_gonder ile AYNI mimari: HER dosya için AYRI AYRI: prepare
    (dosyaId/kisiKurumId çöz + taze "zaten gönderilmiş mi" kontrolü — burada
    "zaten gönderilmiş" AYNI evrak türüne (Avukat Portal Tebligat Talebi)
    bakar, tebligat türünden BAĞIMSIZDIR) → zaten gönderilmişse onay bile
    istemeden atla → ctx.request_approval (dosya no + borçlu + GERÇEK ücret
    gösteren özet) → yalnız "approve" ise finalize (UDF indir + e-imza +
    evrak gönder, ücret burada düşer).

    params: {"kalemler": [{"birim":..., "dosyaNo":"2026/98353", "borclu":...}, ...]}
    (Barkod Sorgu'nun 21/2 tarama raporundan, kullanıcının ELLE seçtiği,
    "farklı yöntem gerekli (mernis'e çıkmamış)" kategorisindeki satırlar —
    teblig_212_gonder'daki kalemler İLE AYNI şekil).
    """
    from .teblig_212.gonder import prepare, finalize, TEBLIGAT_TURU_KODLARI

    normal = TEBLIGAT_TURU_KODLARI["normal"]

    kalemler = ctx.params.get("kalemler") or []
    if not kalemler:
        raise ValueError("params.kalemler boş — gönderilecek dosya yok.")
    n = len(kalemler)
    sonuclar = []
    for i, kalem in enumerate(kalemler, 1):
        ctx.check_cancel()
        dosya_no = kalem.get("dosyaNo", "?")
        ctx.progress(done=i - 1, total=n, message=f"{i}/{n}: {dosya_no} hazırlanıyor")
        try:
            ozet, state = await prepare(ctx, kalem, tebligat_turu_id=normal["id"],
                                        tebligat_turu_aciklama=normal["aciklama"],
                                        masraf=normal["masraf"])
        except JobCancelled:
            raise
        except Exception as e:
            mesaj, tb = _hata_ayrintisi(e)
            ctx.log(f"[{dosya_no}] Hazırlık hatası: {mesaj}")
            ctx.log(f"[{dosya_no}] Traceback:\n{tb}")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "hata", "mesaj": mesaj})
            ctx.progress(done=i)
            continue

        if ozet.get("kategori") == "zaten_gonderilmis":
            ctx.log(f"[{dosya_no}] Zaten gönderilmiş — ATLANDI (ücret harcanmadı).")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "atlandi_zaten_var", "ozet": ozet})
            ctx.progress(done=i)
            continue

        karar = await ctx.request_approval({"mod": "normal_tebligat_tek_dosya", "ozet": ozet})
        if karar.get("decision") == "skip":
            ctx.log(f"[{dosya_no}] Kullanıcı atladı — ücret harcanmadı.")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "atlandi_kullanici", "ozet": ozet})
            ctx.progress(done=i)
            continue

        try:
            sonuc = await finalize(ctx, state)
            sonuclar.append({"dosyaNo": dosya_no, "durum": "gonderildi", "ozet": ozet, **sonuc})
        except JobCancelled:
            raise
        except Exception as e:
            mesaj, tb = _hata_ayrintisi(e)
            ctx.log(f"[{dosya_no}] Gönderim hatası: {mesaj}")
            ctx.log(f"[{dosya_no}] Traceback:\n{tb}")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "hata", "mesaj": mesaj, "ozet": ozet})
        ctx.progress(done=i)

    basari = sum(1 for s in sonuclar if s["durum"] == "gonderildi")
    ctx.progress(done=n, total=n, message=f"bitti: {basari}/{n} gönderildi")
    return {"toplam": n, "basari": basari, "sonuclar": sonuclar}


@job_type("ipotek_takip_ac")
async def _ipotek_takip_ac(ctx):
    """İpotek (İlamlı Takip → İpoteğin Paraya Çevrilmesi) TEK dosya açma.

    params: bkz. uyap_core.ipotek.takip.prepare docstring (mersis_no, tckn_list,
    iban, tapu/ilam bilgileri, vekalet, dayanak_listesi, kalemler — Excel'den
    okunan alacak kalemleri listesi).

    Akış: prepare (sorgu + harç hesabı, TEVZİ ALMAZ) → ctx.request_approval
    (alacak kalemi tutarlarını TEK TEK gösteren özet — kullanıcı ondalık ayraç
    hatası vb. burada YAKALASIN) → yalnızca onaylanırsa finalize (tevzi +
    UDF indir + e-imza + evrak gönder). Onaylanmazsa tevzi numarası ALINMAZ,
    yani dosya AÇILMAZ.

    NOT (kullanıcı bulgusu, 2026-08-14): İpotek akışında harç ödemesi OTOMATİK
    DEĞİL (kullanıcı UYAP'tan elle öder) — bu yüzden finalize() dönüşünde
    GERÇEK esas no ("2026/xxxxxx") henüz UYAP tarafından atanmamış olabilir.
    Yine de burada esas_no.bul() ile bir kez denenir (kullanıcı bu işten önce
    aynı borçluya elle ödeme yapmışsa hemen görünür); bulunamazsa dosya yine
    de AÇIKTIR, yalnızca esas no UYAP'tan ödeme SONRASI elle doğrulanmalı.
    """
    from .ipotek.takip import prepare, finalize
    from . import esas_no as _esas_no
    from . import dosya_kaydet as _dosya_kaydet

    ctx.progress(done=0, total=3, message="hazırlanıyor (sorgular + harç hesabı)")
    ozet, state = await prepare(ctx, ctx.params)
    ctx.progress(done=1, message="onay bekleniyor")
    karar = await ctx.request_approval({"mod": "ipotek_tek_dosya", "ozet": ozet})
    if karar.get("decision") != "approve":
        ctx.log("Kullanıcı onaylamadı — dosya AÇILMADI.")
        return {"durum": "atlandi", "ozet": ozet}
    ctx.progress(done=2, message="tevzi + e-imza + evrak gönderiliyor")
    sonuc = await finalize(ctx, state)
    ctx.log(f"✓ Dosya UYAP'ta açıldı (Dosya ID: {sonuc['dosya_id']}).")

    hedef_adlar = [f"{b.get('ad', '')} {b.get('soyad', '')}".strip()
                   for b in (ozet.get("borclular") or [])]
    hedef_adlar = [a for a in hedef_adlar if a]
    gercek_dosya_no = None
    try:
        gercek_dosya_no = await _esas_no.bul(ctx, hedef_adlar)
    except Exception as e:
        ctx.log(f"⚠️ Esas no doğrulanamadı: {e}")
    if gercek_dosya_no:
        ctx.log(f"✓ Esas No: {gercek_dosya_no}")
        taraflar = []
        if ozet.get("alacakli"):
            taraflar.append(("alacakli", {"tur": "tuzel", "unvan": ozet["alacakli"],
                                          "mersis_no": ozet.get("mersis_no") or None,
                                          "vergi_no": "", "tckn": None, "ad": "", "soyad": ""}))
        for b in (ozet.get("borclular") or []):
            taraflar.append(("borclu", {"tur": "gercek", "ad": b.get("ad") or "",
                                        "soyad": b.get("soyad") or "", "tckn": b.get("tckn") or None,
                                        "unvan": "", "vergi_no": "", "mersis_no": None}))
        await _dosya_kaydet.kaydet(ctx, gercek_dosya_no, taraflar=taraflar, log=ctx.log)
    else:
        ctx.log("ℹ️ Esas no henüz UYAP'ta atanmadı — harç ödemesi UYAP'tan ELLE yapıldıktan "
                "SONRA 'Tamamlanmayan Dosyalar' ekranından görünür.")

    ctx.progress(done=3, message="tamamlandı")
    return {"durum": "tamam", "dosya_id": sonuc["dosya_id"], "evrak_sonuc": sonuc["evrak_sonuc"],
            "gercek_dosya_no": gercek_dosya_no}


@job_type("teblig_212_gonder")
async def _teblig_212_gonder(ctx):
    """T.K.21/2 şerhli yeniden tebliğ talebi GÖNDERİMİ (ÜCRETLİ — bkz.
    uyap_core.teblig_212.gonder.MASRAF_TL, GERÇEK PARA UYAP bakiyesinden düşer).

    params: {"kalemler": [{"birim":..., "dosyaNo":"2026/98353", "borclu":...}, ...]}
    (Panel/Barkod Sorgu ekranındaki 21/2 tarama raporundan, kullanıcının ELLE
    seçtiği "uygun" satırlar).

    Akış — HER dosya için AYRI AYRI: prepare (dosyaId/kisiKurumId çöz + taze
    "zaten gönderilmiş mi" kontrolü, salt-okunur) → zaten gönderilmişse onay
    bile istemeden atla (çift ödeme YAPILAMAZ) → ctx.request_approval (dosya
    no + borçlu + ücreti gösteren özet, kullanıcı Onayla/Atla/İptal seçer) →
    yalnız "approve" ise finalize (UDF indir + e-imza + evrak gönder, ücret
    burada düşer). "cancel" kararı ctx.request_approval içinde JobCancelled
    fırlatır ve KALAN TÜM dosyalar hiç denenmeden iş biter.
    """
    from .teblig_212.gonder import prepare, finalize

    kalemler = ctx.params.get("kalemler") or []
    if not kalemler:
        raise ValueError("params.kalemler boş — gönderilecek dosya yok.")
    n = len(kalemler)
    sonuclar = []
    for i, kalem in enumerate(kalemler, 1):
        ctx.check_cancel()
        dosya_no = kalem.get("dosyaNo", "?")
        ctx.progress(done=i - 1, total=n, message=f"{i}/{n}: {dosya_no} hazırlanıyor")
        try:
            ozet, state = await prepare(ctx, kalem)
        except JobCancelled:
            raise
        except Exception as e:
            mesaj, tb = _hata_ayrintisi(e)
            ctx.log(f"[{dosya_no}] Hazırlık hatası: {mesaj}")
            ctx.log(f"[{dosya_no}] Traceback:\n{tb}")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "hata", "mesaj": mesaj})
            ctx.progress(done=i)
            continue

        if ozet.get("kategori") == "zaten_gonderilmis":
            ctx.log(f"[{dosya_no}] Zaten gönderilmiş — ATLANDI (ücret harcanmadı).")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "atlandi_zaten_var", "ozet": ozet})
            ctx.progress(done=i)
            continue

        karar = await ctx.request_approval({"mod": "teblig_212_tek_dosya", "ozet": ozet})
        if karar.get("decision") == "skip":
            ctx.log(f"[{dosya_no}] Kullanıcı atladı — ücret harcanmadı.")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "atlandi_kullanici", "ozet": ozet})
            ctx.progress(done=i)
            continue

        try:
            sonuc = await finalize(ctx, state)
            sonuclar.append({"dosyaNo": dosya_no, "durum": "gonderildi", "ozet": ozet, **sonuc})
        except JobCancelled:
            raise
        except Exception as e:
            mesaj, tb = _hata_ayrintisi(e)
            ctx.log(f"[{dosya_no}] Gönderim hatası: {mesaj}")
            ctx.log(f"[{dosya_no}] Traceback:\n{tb}")
            sonuclar.append({"dosyaNo": dosya_no, "durum": "hata", "mesaj": mesaj, "ozet": ozet})
        ctx.progress(done=i)

    basari = sum(1 for s in sonuclar if s["durum"] == "gonderildi")
    ctx.progress(done=n, total=n, message=f"bitti: {basari}/{n} gönderildi")
    return {"toplam": n, "basari": basari, "sonuclar": sonuclar}


@job_type("xml_takip_ac")
async def _xml_takip_ac(ctx):
    """UYAP "İcra Takip Açılış - XML" (Kota Tipi: Banka Dosyası) — bir XML dosyasındaki
    BİRDEN FAZLA <dosya> (takip) girişini coklu_takip_ac ile AYNI onay mimarisiyle işler.

    params:
      xml       : {"filename":..., "b64":...}  (ZORUNLU — UYAP'ın exchangeData formatı)
      il, adliye: varsayılan "İzmir" (isimle-arama — il_kodu/adliye_birim_id verilmezse kullanılır).
      il_kodu, adliye_birim_id: Panel'in canlı İl/Adliye dropdown'larından seçilen ID'ler —
                  verilirse isimle-arama ATLANIR (bkz. uyap_core.xml_takip.takip.prepare).
      kota_tipi : "banka" (varsayılan) | "gayrimenkul" — bkz. uyap_core.xml_takip.takip.
                  KOTA_TIPI_DESTEKLENEN. Desteklenmeyen bir değer prepare() içinde
                  ValueError ile reddedilir (bkz. KOTA_TIPI_DESTEKSIZ).
      onay_modu : "tek_tek" | "toplu"  (varsayılan "tek_tek" — bkz. uyap_core.xml_takip.takip
                  modül başlığındaki güvenilirlik notu: harç hesaplama adımı henüz canlı
                  doğrulanmadığı için "yok" modu BİLEREK desteklenmiyor, her takip mutlaka
                  kullanıcı onayından geçmeli).
      odeme_tipi: "7" (e-Barobirlik Kart, varsayılan) | "4" (Vakıfbank).
      vekalet   : {"<dosya_belirleyicisi>": {"filename":..., "b64":...}}  (opsiyonel)
      dayanak   : {"<dosya_belirleyicisi>": {"filename":..., "b64":...}}  (opsiyonel)
    """
    from .xml_takip import parse as xml_parse
    from .xml_takip.takip import prepare, finalize, KOTA_TIPI_VARSAYILAN
    from . import esas_no as _esas_no
    from . import dosya_kaydet as _dosya_kaydet

    p = ctx.params
    xml_ham = p.get("xml")
    if not isinstance(xml_ham, dict) or not xml_ham.get("b64"):
        raise ValueError("params.xml boş — açılacak XML dosyası yok.")
    try:
        xml_belge = {"filename": xml_ham.get("filename"), "bytes": base64.b64decode(xml_ham["b64"])}
    except Exception as e:
        raise ValueError(f"params.xml çözülemedi (base64 hatası): {e}")
    dosyalar = xml_parse.xml_metninden_oku(xml_belge["bytes"])
    if not dosyalar:
        raise ValueError("XML içinde açılacak takip (<dosya>) bulunamadı.")

    il = p.get("il") or "İzmir"
    adliye = p.get("adliye") or "İzmir"
    il_kodu = p.get("il_kodu")
    adliye_birim_id = p.get("adliye_birim_id")
    kota_tipi = (p.get("kota_tipi") or KOTA_TIPI_VARSAYILAN).lower()
    onay_modu = (p.get("onay_modu") or "tek_tek").lower()
    odeme_tipi = str(p.get("odeme_tipi") or "7")
    vekalet_map = p.get("vekalet") or {}
    dayanak_map = p.get("dayanak") or {}

    def belgeler(dosya):
        return {"vekalet": _belge_coz(vekalet_map, dosya.dosya_belirleyicisi),
                "dayanak": _belge_coz(dayanak_map, dosya.dosya_belirleyicisi)}

    def _xml_taraf_bilgisi(t):
        """TarafBilgisi -> dosya_kaydet._taraf_upsert'in beklediği normalize
        dict. KURUM ise unvan/vergi_no/mersis_no, KİŞİ ise ad/soyad/tckn
        (ad tek alanda geliyor — bkz. xml_takip.parse.TarafBilgisi.ad, boşlukla
        ayrılmış SON kelime soyad sayılır, parse_taraf_from_uyap'taki AYNI
        sadeleştirme)."""
        if getattr(t, "tur", "") == "KURUM":
            return {"tur": "tuzel", "unvan": t.ad or "", "vergi_no": t.vergi_no or "",
                    "mersis_no": t.mersis_no or None, "tckn": None, "ad": "", "soyad": ""}
        ad_soyad = (t.ad or "").strip()
        parcalar = ad_soyad.rsplit(None, 1)
        ad, soyad = (parcalar[0], parcalar[1]) if len(parcalar) == 2 else (ad_soyad, "")
        return {"tur": "gercek", "ad": ad, "soyad": soyad, "tckn": (t.tckn or None),
                "unvan": "", "vergi_no": "", "mersis_no": None}

    async def _esas_no_dogrula_ve_kaydet(dosya):
        """TÜM dosyalar açılıp bittikten SONRA (epilogue'da) çağrılır — bkz.
        alttaki not. Gerçek esas no'yu ('Tamamlanmayan Dosyalar' listesinden,
        taraf adlarıyla eşleştirerek) doğrular, açıkça loglar ve bulunursa
        veritabanına (Birim+Dosya+Alacaklı/Borçlu) kaydeder. Bulunamazsa/hata
        olursa dosya no'nun UYAP'ta AÇIK kalması bundan ETKİLENMEZ — yalnızca
        bilgilendirici bir doğrulama adımıdır.

        Esas no eşleştirmesi SADECE BORÇLU adlarıyla yapılır
        (mts.takip.odenmis_dosyalari_bul ile AYNI, canlı doğrulanmış desen) —
        ALACAKLI'yı da eşleştirmeye dahil etmek (ilk sürümün hatası, kullanıcı
        bulgusu 2026-08-14) kurumsal alacaklılarda (banka vb.) UYAP'ın özet
        metnindeki kısaltılmış/farklı yazılmış unvan yüzünden TÜM eşleşmeyi
        başarısız kılıyordu — dosya aslında listede olsa bile hiç
        bulunamıyordu. DB'ye yazılan taraf listesi ise (aşağıda) TÜM
        taraflardan (alacaklı+borçlu) kurulur — bu, eşleştirmeden BAĞIMSIZ."""
        borclular = [t for t in (dosya.taraflar or [])
                    if getattr(t, "rol_id", None) == 22 or "BORÇLU" in (getattr(t, "rol", "") or "").upper()]
        hedef_adlar = [t.ad for t in borclular if getattr(t, "ad", None)]
        gercek_no = None
        try:
            gercek_no = await _esas_no.bul(ctx, hedef_adlar)
        except Exception as e:
            ctx.log(f"[{dosya.dosya_belirleyicisi}] ⚠️ Esas no doğrulanamadı: {e}")
        if gercek_no:
            ctx.log(f"[{dosya.dosya_belirleyicisi}] ✓ Esas No: {gercek_no}")
            taraflar = []
            for t in (dosya.taraflar or []):
                rol = "alacakli" if (getattr(t, "rol_id", None) == 21
                                     or "ALACAKLI" in (getattr(t, "rol", "") or "").upper()) else "borclu"
                taraflar.append((rol, _xml_taraf_bilgisi(t)))
            await _dosya_kaydet.kaydet(ctx, gercek_no, taraflar=taraflar, log=ctx.log)
        else:
            ctx.log(f"[{dosya.dosya_belirleyicisi}] ⚠️ Esas no 'Tamamlanmayan Dosyalar' "
                    "listesinde henüz görünmüyor — UYAP'tan elle doğrulayın.")
        return gercek_no

    n = len(dosyalar)
    sonuclar = []
    basari = atlanan = hata = 0
    # [(dosya, sonuc_dict), ...] — esas no doğrulama + DB kaydı BİLEREK bu ana
    # döngünün İÇİNE konmuyor (kullanıcı bulgusu, 2026-08-14, canlı çöküş,
    # ReadError): her başarılı dosyadan sonra 2 ekstra UYAP isteği + 1
    # bloklayan DB yazımı araya girip SONRAKİ dosyanın prepare() akışındaki
    # (bununla İLGİSİZ) ilk isteğinden önceki boşluğu uzatıyor, bu da UYAP/proxy
    # bağlantısını "bayat" bırakıp ReadError'a yol açıyordu. Asıl takip açma
    # döngüsü artık BUNDAN TAMAMEN İZOLE — doğrulama/kayıt TÜM dosyalar
    # açıldıktan SONRA, ayrı bir geçişte yapılır (aşağıda).
    basarili_dosyalar = []
    ctx.progress(done=0, total=n, message="başlıyor")

    if onay_modu == "toplu":
        hazir = []
        for i, dosya in enumerate(dosyalar, 1):
            ctx.check_cancel()
            ctx.progress(done=i - 1, message=f"hazırlanıyor {i}/{n}: {dosya.dosya_belirleyicisi}")
            try:
                ozet, state = await prepare(ctx, dosya, il=il, adliye=adliye,
                                            il_kodu=il_kodu, adliye_birim_id=adliye_birim_id,
                                            kota_tipi=kota_tipi)
                hazir.append((dosya, state, ozet))
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                ctx.log(f"[{dosya.dosya_belirleyicisi}] Hazırlık hatası: {e}")
                sonuclar.append({"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "hata", "mesaj": str(e)})
        if not hazir:
            return {"toplam": n, "basari": 0, "atlanan": 0, "hata": hata,
                    "sonuclar": sonuclar, "ozet": "Hiçbir takip hazırlanamadı."}

        karar = await ctx.request_approval({"mod": "toplu", "takipler": [o for (_, _, o) in hazir]})
        secim = karar.get("selection")
        sec_set = set(str(x) for x in secim) if secim is not None else None

        for i, (dosya, state, ozet) in enumerate(hazir, 1):
            ctx.check_cancel()
            if sec_set is not None and str(dosya.dosya_belirleyicisi) not in sec_set:
                atlanan += 1
                sonuclar.append({"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "atlandı"})
                ctx.progress(done=i)
                continue
            try:
                ctx.progress(message=f"işleniyor {i}/{len(hazir)}: {dosya.dosya_belirleyicisi}")
                r = await finalize(ctx, state, odeme_tipi=odeme_tipi, **belgeler(dosya))
                basari += 1
                sonuc_dict = {"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "tamam",
                             "dosya_id": r["dosya_id"], "gercek_dosya_no": None}
                sonuclar.append(sonuc_dict)
                basarili_dosyalar.append((dosya, sonuc_dict))
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                mesaj, tb = _hata_ayrintisi(e)
                ctx.log(f"[{dosya.dosya_belirleyicisi}] HATA: {mesaj}")
                ctx.log(f"[{dosya.dosya_belirleyicisi}] Traceback:\n{tb}")
                sonuclar.append({"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "hata", "mesaj": mesaj})
            ctx.progress(done=i)
    else:
        for i, dosya in enumerate(dosyalar, 1):
            ctx.check_cancel()
            ctx.progress(done=i - 1, message=f"{i}/{n}: {dosya.dosya_belirleyicisi}")
            try:
                ozet, state = await prepare(ctx, dosya, il=il, adliye=adliye,
                                            il_kodu=il_kodu, adliye_birim_id=adliye_birim_id,
                                            kota_tipi=kota_tipi)
                karar = await ctx.request_approval({"mod": "tek_tek", "takip": ozet})
                if karar.get("decision") != "approve":
                    atlanan += 1
                    sonuclar.append({"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "atlandı"})
                    ctx.progress(done=i)
                    continue
                r = await finalize(ctx, state, odeme_tipi=odeme_tipi, **belgeler(dosya))
                basari += 1
                sonuc_dict = {"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "tamam",
                             "dosya_id": r["dosya_id"], "gercek_dosya_no": None}
                sonuclar.append(sonuc_dict)
                basarili_dosyalar.append((dosya, sonuc_dict))
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                mesaj, tb = _hata_ayrintisi(e)
                ctx.log(f"[{dosya.dosya_belirleyicisi}] HATA: {mesaj}")
                ctx.log(f"[{dosya.dosya_belirleyicisi}] Traceback:\n{tb}")
                sonuclar.append({"dosya_belirleyicisi": dosya.dosya_belirleyicisi, "durum": "hata", "mesaj": mesaj})
            ctx.progress(done=i)

    if basarili_dosyalar:
        ctx.log(f"Esas no doğrulanıyor ve veritabanına kaydediliyor ({len(basarili_dosyalar)} dosya)...")
        for dosya, sonuc_dict in basarili_dosyalar:
            ctx.check_cancel()
            sonuc_dict["gercek_dosya_no"] = await _esas_no_dogrula_ve_kaydet(dosya)

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
