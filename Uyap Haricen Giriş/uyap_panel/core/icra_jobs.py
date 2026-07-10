"""
uyap_panel.core.icra_jobs — İcra takip açılış işi (uzantı noktası).

MTS (coklu_takip_ac) AYRI bir akıştır; bu modül klasik **İcra Takip Talebi**
(icra dairesi) açılışını "icra_takip_ac" iş türü olarak kaydeder. İş kuyruğu
orkestrasyonu (ilerleme / iptal / onay modları) coklu_takip_ac ile birebir
aynıdır; SADECE asıl UYAP adımları (prepare/finalize) farklıdır ve şu an bir
uzantı noktasıdır:

    uyap_core/icra/takip.py  →  async def prepare(ctx, takip, *, il, adliye)
                                 async def finalize(ctx, takip, state, *, vekalet, dayanak)

Bu modül import edildiğinde işleyici otomatik kaydolur (uyap_panel.core.__init__
bunu yapar). `uyap_core.icra.takip` henüz yoksa, iş net bir "backend eklenmedi"
mesajıyla biter — ön yüz (GUI/Django) akışı şimdiden uçtan uca kurulabilir.
"""

from uyap_core.jobs import job_type, JobCancelled
from uyap_core.job_handlers import _belge_coz


def _icra_flow():
    """uyap_core.icra.takip.prepare/finalize'i döndürür; yoksa açık hata fırlatır."""
    try:
        from uyap_core.icra.takip import prepare, finalize
        return prepare, finalize
    except ImportError as e:
        raise NotImplementedError(
            "İcra takip backend'i henüz eklenmedi. UYAP klasik icra takip uçları "
            "belirlenince 'uyap_core/icra/takip.py' içine prepare()/finalize() yazılacak; "
            "bu arayüz ve iş kuyruğu akışı hazır. (Ayrıntı: %s)" % e
        )


def _takipler_from_params(takipler):
    """İcra modeli varsa onu, yoksa MTS modelini kullanır (XML şeması büyük ölçüde ortak)."""
    try:
        from uyap_core.icra.models import takipler_from_params
    except ImportError:
        from uyap_core.mts.models import takipler_from_params
    return takipler_from_params(takipler)


@job_type("icra_takip_ac")
async def _icra_takip_ac(ctx):
    """Klasik İcra Takip Talebi (XML) ile çoklu takip açma.

    params şeması coklu_takip_ac ile aynıdır:
      takipler, il, adliye, onay_modu ("yok"|"tek_tek"|"toplu"), vekalet, dayanak.
    Onay, işi başlatan kullanıcının ekranında alınır (ctx.request_approval).
    """
    prepare, finalize = _icra_flow()  # backend yoksa burada net hata verir

    p = ctx.params
    takipler = _takipler_from_params(p.get("takipler"))
    if not takipler:
        raise ValueError("params.takipler boş — açılacak İcra takibi yok.")
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
        hazir = []
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
                    "sonuclar": sonuclar, "ozet": "Hiçbir İcra takibi hazırlanamadı."}

        karar = await ctx.request_approval({"mod": "toplu",
                                            "takipler": [o for (_, _, o) in hazir]})
        secim = karar.get("selection")
        sec_set = set(str(x) for x in secim) if secim is not None else None

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
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "tamam",
                                 "dosya_id": r.get("dosya_id")})
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                ctx.log(f"[{t.dosya_no}] HATA: {e}")
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "hata", "mesaj": str(e)})
            ctx.progress(done=i)
    else:
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
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "tamam",
                                 "dosya_id": r.get("dosya_id")})
            except JobCancelled:
                raise
            except Exception as e:
                hata += 1
                ctx.log(f"[{t.dosya_no}] HATA: {e}")
                sonuclar.append({"dosya_no": t.dosya_no, "durum": "hata", "mesaj": str(e)})
            ctx.progress(done=i)

    ctx.progress(done=n, message=f"bitti: {basari} tamam, {atlanan} atlandı, {hata} hata")
    return {"toplam": n, "basari": basari, "atlanan": atlanan, "hata": hata, "sonuclar": sonuclar}
