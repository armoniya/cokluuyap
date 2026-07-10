import json
import os
import time
import re as _re
import tkinter as tk
from tkinter import ttk, messagebox

# Import the original script's models and helper functions
from mts_takip_acan import (
    UyapBot, Takip, Borclu, AlacakKalemi,
    _temiz, _virgullu, indirmeyi_yakala, pdf_dayanak_tara,
    excel_to_takipler, kaynaktan_takipler, DosyaAtla, TakipDurduruldu,
    KontrolDurumu, kalemleri_birlestir, pencereyi_one_al
)
import win32gui


# Helper function to convert Turkish chars to ASCII
def tr_to_ascii(text):
    if not text:
        return ""
    tr_map = {
        'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'I': 'I', 'İ': 'I', 'ö': 'o', 'Ö': 'O',
        'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U', ' ': '_'
    }
    for tr_char, eng_char in tr_map.items():
        text = text.replace(tr_char, eng_char)
    return _re.sub(r'[^a-zA-Z0-9_]', '', text)


def format_date_with_slashes(date_str):
    if not date_str:
        return time.strftime("%d/%m/%Y")
    clean_date = _re.sub(r'[^0-9]', '', str(date_str)).strip()
    if len(clean_date) == 8:
        # If it starts with a year (e.g. 20260609)
        if clean_date.startswith(("19", "20")) and int(clean_date[4:6]) <= 12 and int(clean_date[6:]) <= 31:
            return f"{clean_date[6:]}/{clean_date[4:6]}/{clean_date[:4]}"
        else:
            return f"{clean_date[:2]}/{clean_date[2:4]}/{clean_date[4:]}"
    elif len(clean_date) == 6:
        return f"{clean_date[:2]}/{clean_date[2:4]}/20{clean_date[4:]}"
    try:
        from datetime import datetime
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(str(date_str).strip(), fmt)
                return dt.strftime("%d/%m/%Y")
            except ValueError:
                pass
    except Exception:
        pass
    return time.strftime("%d/%m/%Y")


def show_validation_dialog(takip, adliye_ad, harclar=None):
    """Veri girişi tamamlandıktan sonra kullanıcıya onay ekranı açar."""
    result = {"approved": False}
    
    root = tk.Tk()
    root.title("MTS Takip Veri Kontrolü")
    root.geometry("650x580")
    root.attributes("-topmost", True)
    
    # Simple styling
    style = ttk.Style()
    style.theme_use('clam')
    
    main_frame = ttk.Frame(root, padding="20")
    main_frame.pack(fill=tk.BOTH, expand=True)
    
    title_label = ttk.Label(main_frame, text="UYAP MTS TAKİP VERİ KONTROLÜ", font=("Helvetica", 12, "bold"))
    title_label.pack(pady=10)
    
    text_area = tk.Text(main_frame, wrap=tk.WORD, width=75, height=22, font=("Courier New", 9))
    text_area.pack(fill=tk.BOTH, expand=True, pady=10)
    
    # Populate info text
    def parse_float(val):
        if not val:
            return 0.0
        val_str = str(val).replace(".", "").replace(",", ".").replace(" TL", "").strip()
        try:
            return float(val_str)
        except ValueError:
            return 0.0

    info = []
    info.append("="*70)
    info.append(f"DOSYA NO      : {takip.dosya_no}")
    info.append(f"ADLİYE        : {adliye_ad}")
    info.append(f"ALACAKLI      : {takip.alacakli}")
    info.append(f"ALACAKLI IBAN : {takip.iban}")
    info.append(f"ABONE NO      : {takip.abone_no or takip.hizmet_abone_no}")
    info.append("="*70)
    info.append("BORÇLULAR:")
    for idx, b in enumerate(takip.borclular, 1):
        info.append(f"  {idx}. {b.ad} {b.soyad} (TC: {b.kimlik})")
    info.append("="*70)
    info.append("ALACAK KALEMLERİ:")
    total = 0.0
    for idx, ak in enumerate(takip.alacak_kalemleri, 1):
        t_val = parse_float(ak.tutar)
        total += t_val
        info.append(f"  {idx}. {ak.ad}: {ak.tutar} TL (Faiz: %{ak.faiz_oran or '0'}, Tür: {ak.faiz_tur or 'Yok'})")
    info.append(f"TOPLAM ALACAK : {total:.2f} TL".replace(".", ","))
    
    if harclar:
        info.append("="*70)
        info.append("UYAP TARAFINDAN HESAPLANAN HARÇ VE MASRAFLAR:")
        for h in harclar:
            # Eğer h string ise JSON olarak parse etmeye çalışalım
            if isinstance(h, str):
                try:
                    h = json.loads(h)
                except Exception:
                    pass
            
            if isinstance(h, dict):
                name = h.get("harcMasrafAdi") or h.get("aciklama") or "Masraf"
                mikt = h.get("hesapMiktar") or h.get("harcMasrafBedel") or 0.0
                if isinstance(mikt, (int, float)):
                    mikt_str = f"{mikt:.2f} TL".replace(".", ",")
                else:
                    mikt_str = f"{mikt} TL"
                info.append(f"  {name:<30}: {mikt_str}")
            else:
                info.append(f"  {h}")
            
    info.append("="*70)
    info.append("\nYukarıdaki bilgilerle UYAP MTS taslak takibi oluşturulacaktır.")
    info.append("Lütfen verileri kontrol edin. Onaylıyor musunuz?")
    
    text_area.insert(tk.END, "\n".join(info))
    text_area.configure(state=tk.DISABLED)
    
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill=tk.X, pady=10)
    
    def on_approve():
        result["approved"] = True
        root.destroy()
        
    def on_cancel():
        result["approved"] = False
        root.destroy()
        
    cancel_btn = ttk.Button(btn_frame, text="İPTAL ET / DOSYAYI ATLA", command=on_cancel)
    cancel_btn.pack(side=tk.LEFT, padx=10)
    
    approve_btn = ttk.Button(btn_frame, text="ONAYLA VE DEVAM ET", command=on_approve)
    approve_btn.pack(side=tk.RIGHT, padx=10)
    
    root.mainloop()
    return result["approved"]


def call_uyap_api(page, url, method="POST", payload=None, is_multipart=False):
    """Playwright tarayıcı oturumu içinde fetch kullanarak UYAP API isteklerini atar."""
    import json
    if is_multipart:
        js_code = """
        async (args) => {
            const { url, payloadStr } = args;
            const payload = JSON.parse(payloadStr);
            const formData = new FormData();
            for (const key in payload) {
                if (typeof payload[key] === 'object') {
                    formData.append(key, JSON.stringify(payload[key]));
                } else {
                    formData.append(key, payload[key]);
                }
            }
            const response = await fetch(url, {
                method: 'POST',
                body: formData
            });
            if (!response.ok) {
                throw new Error('HTTP status ' + response.status);
            }
            return await response.text();
        }
        """
        payload_str = json.dumps(payload)
        return page.evaluate(js_code, {"url": url, "payloadStr": payload_str})
    else:
        js_code = """
        async (args) => {
            const { url, method, payloadStr } = args;
            const headers = {
                'Content-Type': 'application/json',
                'accept': 'application/json, text/plain, */*'
            };
            const options = {
                method: method,
                headers: headers
            };
            if (payloadStr) {
                options.body = payloadStr;
            }
            const response = await fetch(url, options);
            if (!response.ok) {
                throw new Error('HTTP status ' + response.status);
            }
            return await response.text();
        }
        """
        payload_str = json.dumps(payload) if payload is not None else None
        return page.evaluate(js_code, {"url": url, "method": method, "payloadStr": payload_str})


def parse_amount(val):
    if not val:
        return 0.0
    val_str = str(val).replace(".", "").replace(",", ".").replace(" TL", "").strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0


def takip_ac(bot, takip, kontrol=None, vekalet_map=None, il="İzmir", adliye="İzmir",
             onay=None, dayanak_map=None, veri_girisi_onay=None,
             borclu_hata_onay=None, dogrulama_onay=None, manuel_mudahale_onay=None,
             genel_hata_onay=None):
    """Tek bir takibi API istekleri ile UYAP'ta oluşturup, evrak yükleme ve imza adımlarıyla tamamlar."""
    if kontrol is None:
        from mts_takip_acan import KontrolDurumu
        kontrol = KontrolDurumu()

    vekalet_map = vekalet_map or {}
    dayanak_map = dayanak_map or {}

    print(f"\n--- API Akışı Başlıyor (Dosya No: {takip.dosya_no}) ---")

    # 1) UYAP MTS Takip Açılış ekranını bir kez açtırıp oturumun aktifleşmesini sağlayalım
    bot.MTS_takip_acilis()
    time.sleep(1.5)

    try:
        # 3) Avukat ID ve Kurum Bilgilerini API üzerinden sorgula
        print("Avukat bilgileri sorgulanıyor...")
        avukat_id = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/get_avukat_id.ajx", method="POST", payload={})
        avukat_id = avukat_id.strip().replace('"', '')

        print("Yetkili kurumlar listeleniyor...")
        kurumlar_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/mtsAvukatYetkiliKurumlar_brd.ajx", method="POST", payload={})
        kurumlar = json.loads(kurumlar_raw)

        # Excel'den gelen alacaklı adını kurum listesinde eşleştir
        selected_kurum = None
        alacakli_clean = takip.alacakli.upper().replace("İ", "I").replace("ı", "I").strip()
        for k in kurumlar:
            k_name = k.get("kurumAdi", "").upper().replace("İ", "I").replace("ı", "I")
            if alacakli_clean in k_name or k_name in alacakli_clean:
                selected_kurum = k
                break

        if not selected_kurum:
            if len(kurumlar) == 1:
                selected_kurum = kurumlar[0]
            else:
                raise ValueError(f"UYAP yetkili kurum listesinde alacaklı bulunamadı: {takip.alacakli}")

        kisi_kurum_id = selected_kurum.get("kisiKurumId")
        print(f"Alacaklı: {selected_kurum.get('kurumAdi')} (ID: {kisi_kurum_id})")

        # Alacaklı iletişim ve adres bilgilerini çek
        print("Alacaklı detayları çekiliyor...")
        adresler_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/getAdresListesi_brd.ajx", method="POST",
                                    payload={"kisiKurumId": kisi_kurum_id, "tarafTur": 2})
        adresler = json.loads(adresler_raw)
        
        # Seçili adresi belirle
        selected_address = None
        if adresler:
            selected_address = adresler[0]
            selected_address["isSelected"] = True

        iletisim_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/mtsAlacakliIletisimBilgisi.ajx", method="POST",
                                     payload={"kisiKurumId": kisi_kurum_id})
        iletisim = json.loads(iletisim_raw)

        # Avukat / Alacaklı IBAN bilgisini sorgula ve doğrula
        # UYAP IBAN'ı "TR" ve boşluk OLMADAN, yalnızca rakamlarla bekler.
        print("IBAN bilgisi sorgulanıyor...")
        iban_temiz = _re.sub(r'[^0-9]', '', str(takip.iban))
        iban_details_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/geIbanDetails.ajx", method="POST",
                                         payload={"iban": iban_temiz, "isSansurlenecek": False})
        iban_details = json.loads(iban_details_raw)

        # Yanıt yapısı: {"type":"success","value":{"bankaAdi":...,"ibanNumarasi":...,"hesapGenel":...}}
        # Banka adı/iban "value" altındadır; eskiden top-level okunduğu için boş kalıyordu.
        iban_val = iban_details.get("value", {}) if isinstance(iban_details, dict) else {}

        selected_iban = []
        if iban_val:
            selected_iban = [{
                "bankaAdi": iban_val.get("bankaAdi", ""),
                "ibanNumarasi": iban_val.get("ibanNumarasi", iban_temiz),
                "hesapGenel": iban_val.get("hesapGenel", True),
                "isSelected": True,
                "id": 0,
                "ibanTuru": "alacakliIban"
            }]
        else:
            raise ValueError(f"UYAP IBAN detayını döndürmedi (iban: {iban_temiz}). Yanıt: {iban_details_raw}")

        # 4) Borçlu bilgilerini T.C. üzerinden Mernis/UYAP'tan çek
        print("Borçlu bilgileri sorgulanıyor...")

        def _es_zamanli_hata_mi(veri):
            """UYAP 'eş zamanlı sorgulama' hatası mı? (PRTL_GNL_10001-61)"""
            if not isinstance(veri, dict):
                return False
            kod = str(veri.get("errorCode", ""))
            mesaj = str(veri.get("error", ""))
            return "10001-61" in kod or "zamanlı" in mesaj.lower()

        def _kisi_sorgula(_b, ad, soyad):
            raw = call_uyap_api(
                bot.page, "https://avukat.uyap.gov.tr/kisiSorgulaWithAdSoyad.ajx",
                method="POST",
                payload={"tcKimlikNo": _b.kimlik, "ad": (ad or "").upper(),
                         "soyad": (soyad or "").upper()})
            return json.loads(raw)

        kisi_list = []
        for b in takip.borclular:
            kisi_info = _kisi_sorgula(b, b.ad, b.soyad)

            # (a) Eş zamanlı sorgulama hatası → kullanıcıya bildir, dosya hatalılara.
            if _es_zamanli_hata_mi(kisi_info):
                raise ValueError(
                    "Eş zamanlı sorgulama hatası: UYAP aynı anda birden fazla "
                    "sorguya izin vermiyor. Kısa bir süre bekleyip bu dosyayı "
                    f"'Tekrar Dene' ile açın. (Borçlu: {b.ad} {b.soyad})")

            # (b) Soyadı sonradan değişmişse UYAP success:false döner ama güncel
            #     soyadı yanıtında verir; o soyadla bir kez daha sorguluyoruz.
            if isinstance(kisi_info, dict) and kisi_info.get("success") is False:
                yeni_soyad = (kisi_info.get("soyad") or "").strip()
                if yeni_soyad and yeni_soyad.upper() != (b.soyad or "").upper():
                    print(f"  Soyadı değişmiş: '{b.soyad}' → '{yeni_soyad}'. "
                          "Güncel soyadla tekrar sorgulanıyor...")
                    kisi_info = _kisi_sorgula(b, b.ad, yeni_soyad)
                    if _es_zamanli_hata_mi(kisi_info):
                        raise ValueError(
                            "Eş zamanlı sorgulama hatası (soyad güncellemesi sırasında). "
                            f"Bu dosyayı sonra 'Tekrar Dene' ile açın. (Borçlu: {b.ad} {yeni_soyad})")

            # (c) Hâlâ başarısızsa borçlu bulunamadı.
            if (not isinstance(kisi_info, dict) or kisi_info.get("success") is False
                    or not kisi_info.get("tcKimlikNo")):
                raise ValueError(f"Borçlu T.C. sorgulaması başarısız: {b.ad} {b.soyad} ({b.kimlik})")

            # UYAP'taki güncel soyadı borçlu nesnesine yansıt (dosya adı vb. için).
            guncel_soyad = (kisi_info.get("soyadi") or "").strip()
            if guncel_soyad and guncel_soyad.upper() != (b.soyad or "").upper():
                b.soyad = guncel_soyad

            # Mernis adres kontrolü — yanıt 'false' ise MERNİS'te kayıtlı adres YOK.
            mernis_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/mtsMernisAdresiKontrol_brd.ajx", method="POST",
                                       payload={"tcKimlikNo": b.kimlik})
            try:
                mernis_veri = json.loads(mernis_raw)
            except Exception:
                mernis_veri = mernis_raw
            if _es_zamanli_hata_mi(mernis_veri):
                raise ValueError(
                    "Eş zamanlı sorgulama hatası (mernis adres kontrolünde). "
                    f"Bu dosyayı sonra 'Tekrar Dene' ile açın. (Borçlu: {b.ad} {b.soyad})")
            if mernis_veri is False or mernis_veri == "false":
                raise ValueError(
                    f"MERNİS adres hatası: '{kisi_info.get('adi')} {kisi_info.get('soyadi')}' "
                    f"(TC: {b.kimlik}) için MERNİS'te kayıtlı adres bulunamadı. "
                    "Adresi UYAP'ta elle ekleyip dosyayı 'Tekrar Dene' ile açabilirsiniz.")

            # UYAP formatına uygun borçlu objesi
            kisi_list.append({
                "id": f"kisi_{len(kisi_list)}",
                "tarafTuru": "KISI",
                "isVekil": False,
                "mernisAdresiKullan": True,
                "tarafAdi": f"{kisi_info.get('adi')} {kisi_info.get('soyadi')}",
                "temelBilgiler": kisi_info,
                "adresList": [],
                "rolGirisBilgisi": {"rolID": 22, "rolAdi": "BORÇLU", "sanikStatusu": "E", "davaliDavaciGrubu": "L"},
                "viewId": f"kisi_{len(kisi_list)}"
            })
            print(f"  Borçlu Bulundu: {kisi_info.get('adi')} {kisi_info.get('soyadi')}")

        # 5) Alacak Kalemlerini derle
        # Çalışan UI akışıyla (mts_takip_acan.py) aynı: önce aynı ad+faiz oranlı
        # kalemleri birleştir, tutarı 0 olanları ele. Aksi halde UYAP
        # "Alacak Kalem Tutarı 0 Dan Büyük Bir Değer Olmalıdır" hatası verir.
        print("Alacak kalemleri derleniyor...")
        birlesik_kalemler = kalemleri_birlestir(takip.alacak_kalemleri)
        print(f"  {len(takip.alacak_kalemleri)} kalem -> "
              f"{len(birlesik_kalemler)} birleşik kaleme indirgendi.")

        def _kalem_dict(ad, tutar_float, faiz_oran="", faiz_tur="", idx=0):
            """Tek bir alacak kalemi JSON'ı kurar.

            faizBilgileri YALNIZCA Asıl Alacak (value 3) için doldurulur; ileriye
            dönük (işleyen) faiz asıl alacağa bağlanır. Geçmiş gün faizi ve masraf
            kalemleri faizBilgileri'ni BOŞ ({}) gönderir — bkz. ag_kaydi.log'daki
            gerçek başarılı istek."""
            ad_l = (ad or "").lower()
            if "faiz" in ad_l:
                ak_val, ak_display = 6, "Faiz Alacağı"
            elif "masraf" in ad_l:
                ak_val, ak_display = 5, "Masraf Alacağı"
            else:
                ak_val, ak_display = 3, "Asıl Alacağı"

            kalem = {
                "selectedTarafHashKeyList": ["kurum_0", "kisi_0"],
                "temelBilgiler": {
                    "tutarTL": tutar_float,
                    "alacakTutariTL": tutar_float,
                    "selectedParaBirimi": {"tktId": "PRBRMTL", "kod": "TL", "aciklama": "TL-Türk Lirası"},
                    "KDV": False,
                    "aciklama": ad,
                    "selectedAlacakKalemKodu": {"name": ak_display, "value": ak_val}
                },
                "id": idx
            }

            if ak_val == 3:
                faiz_oran_float = 0.0
                try:
                    faiz_oran_float = float(str(faiz_oran).replace(",", "."))
                except Exception:
                    pass
                faiz_tur_tkt, faiz_tur_desc = "FAIZT00007", "Reeskont Avans"
                if faiz_tur and "yasal" in faiz_tur.lower():
                    faiz_tur_tkt, faiz_tur_desc = "FAIZT00001", "Yasal Faiz"
                kalem["faizBilgileri"] = {
                    "selectedFaizTuru": {"tktId": faiz_tur_tkt, "kod": faiz_tur_tkt.replace("FAIZT", ""), "aciklama": faiz_tur_desc, "kodTuru": "FAIZT"},
                    "faizOraniKurus": faiz_oran_float,
                    "selectedFaizSureTipi": {"id": "2", "adi": "Yıllık"}
                }
            else:
                kalem["faizBilgileri"] = {}
            return kalem

        alacak_kalemleri = []
        for ak in birlesik_kalemler:
            tutar_float = parse_amount(ak.tutar)
            if tutar_float <= 0:
                print(f"  Bilgi: '{ak.ad}' kalemi tutar=0 — atlandı.")
                continue
            alacak_kalemleri.append(
                _kalem_dict(ak.ad, tutar_float, ak.faiz_oran, ak.faiz_tur,
                            idx=len(alacak_kalemleri)))

        tarih_str = format_date_with_slashes(takip.fatura_tarihi)
        muaccel_str = format_date_with_slashes(takip.odeme_tarihi)

        # İlamsız üst tutar = XML'deki ilamsız/fatura tutarı; kalemlerin toplamı
        # DEĞİL. (Bkz. ag_kaydi.log: üst 'tutar' 7240.31, kalem toplamı 8729.75.)
        ilamsiz_tutar_float = parse_amount(takip.ilamsiz_tutar)
        if ilamsiz_tutar_float <= 0:
            ilamsiz_tutar_float = sum(k["temelBilgiler"]["tutarTL"] for k in alacak_kalemleri)

        def _ilamsiz_list_kur():
            return [{
                "id": 0,
                "no": takip.abone_no or takip.hizmet_abone_no or "",
                "tutar": f"{ilamsiz_tutar_float:.2f} TL",   # UYAP nokta bekler (örn '7240.31 TL')
                "tutarTL": ilamsiz_tutar_float,
                "tur": "TL",
                "tarih": tarih_str,
                "muacceliyetTarihi": muaccel_str,
                "aciklama": takip.aciklama or "",
                "selectedParaBirimi": {"tktId": "PRBRMTL", "kod": "TL", "aciklama": "TL-Türk Lirası"},
                "alacakKalemleri": alacak_kalemleri,
                "alacakKalemi": ""
            }]

        ilamsiz_list = _ilamsiz_list_kur()

        # Taraf listesini (kişi + kurum) HARÇ ÇAĞRISINDAN ÖNCE kur. UYAP harç
        # hesaplarken alacakKalemleri'ndeki selectedTarafHashKeyList
        # ("kurum_0"/"kisi_0") referanslarını tarafList üzerinden çözer; bu alan
        # eksik gönderilirse alacağı bir tarafla ilişkilendiremeyip
        # "Alacak Bilgisinin Konusu Girilmelidir" (ICR_DNMK_10000) hatası verir.
        taraf_list = {
            "kisiList": kisi_list,
            "kurumList": [{
                "id": "kurum_0",
                "tarafTuru": "KURUM",
                "isVekil": False,
                "mernisAdresiKullan": False,
                "temelBilgiler": selected_kurum,
                "tarafAdi": selected_kurum.get("kurumAdi"),
                "adresList": [selected_address] if selected_address else [],
                "rolGirisBilgisi": {"rolID": 21, "rolAdi": "ALACAKLI", "sanikStatusu": "H", "davaliDavaciGrubu": "N"},
                "selectedIban": selected_iban,
                "currentStatus": {
                    "tarafHesapBilgisi": {"selectedHesapBilgileriDVOList": []},
                    "vekilHesapBilgisi": {"selectedHesapBilgileriDVOList": selected_iban}
                },
                "alacakliIletisimTelefon": iletisim.get("telefon") or "4441444",
                "alacakliIletisimEPosta": iletisim.get("ePosta") or "iletisim@turktelekom.com.tr",
                "viewId": "kurum_0"
            }]
        }

        # 6) Harçları UYAP API'ye hesaplat
        def _harc_hesapla():
            raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/mtsHarcList_brd.ajx", method="POST",
                                payload={"ilamsizList": ilamsiz_list, "tarafList": taraf_list}, is_multipart=True)
            h = json.loads(raw)
            if isinstance(h, dict) and "error" in h:
                raise ValueError(f"UYAP harç/masraf hesaplayamadı. Hata: {h.get('error')} (Kod: {h.get('errorCode')})")
            
            # Vekalet pulu bedelini al ve harçlara ekle (eğer listede yoksa)
            if isinstance(h, list) and not any(it.get("harcMasrafAdi") == "Vekalet Pulu" for it in h if isinstance(it, dict)):
                try:
                    pulu_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/vekaletPuluBedeli.ajx", method="POST", payload={})
                    pulu_val = float(str(pulu_raw).strip())
                    if pulu_val > 0:
                        h.append({"harcMasrafAdi": "Vekalet Pulu", "hesapMiktar": pulu_val})
                except Exception as e:
                    print(f"  Uyarı: Vekalet pulu bedeli alınamadı/eklenemedi: {e}")
            return h

        print("Harç ve masraflar hesaplanıyor...")
        harclar = _harc_hesapla()

        # 6b) Masraf alacağı kalemi:
        # UI akışında 'veri girişini onayla' sayfasındaki güncel toplam harç/masraf
        # tutarı okunur, üzerine 317 TL eklenir ve bir önceki sayfaya dönülerek
        # 'Masraf' alacak kalemi olarak girilir. Toplamı GÖRMEDEN 317 eklenmez.
        # API'de bu ekran toplamı = harç kalemlerinin hesapMiktar toplamıdır.
        def _harc_ekran_toplami(h):
            toplam = 0.0
            for it in (h or []):
                if isinstance(it, dict):
                    m = it.get("hesapMiktar")
                    if isinstance(m, (int, float)):
                        toplam += m
            return toplam

        masraf_ekran = _harc_ekran_toplami(harclar)
        if masraf_ekran > 0:
            masraf_tutar = round(masraf_ekran + 317, 2)
            print(f"  Masraf alacağı ekleniyor: {masraf_tutar} TL "
                  f"(ekran toplamı {masraf_ekran} + 317)")
            alacak_kalemleri.append(
                _kalem_dict("Masraf", masraf_tutar, idx=len(alacak_kalemleri)))
            # Kalemler değişti: ilamsız listeyi ve harcı, masraf kalemi eklenmiş
            # haliyle yeniden kur (golden'da son harç çağrısı bu haldedir).
            ilamsiz_list = _ilamsiz_list_kur()
            harclar = _harc_hesapla()
        else:
            print("  Bilgi: Harç/masraf toplamı 0 — masraf alacağı eklenmedi.")

        # 7) İl ve Adliye Eşleştirmesi (İzmir / İzmir vb.)
        # Şehir kodları
        iller_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/illeri_getirJSON.ajx", method="POST", payload={})
        iller = json.loads(iller_raw)
        
        selected_il = None
        il_clean = il.upper().replace("İ", "I").replace("ı", "I").strip()
        for i_obj in iller:
            i_name = i_obj.get("ad", "").upper().replace("İ", "I").replace("ı", "I")
            if il_clean in i_name or i_name in il_clean:
                selected_il = i_obj
                break
        
        if not selected_il:
            selected_il = {"il": 35, "ad": "İZMİR"} # default İzmir
            
        # Adliye kodu
        adliyeler_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/icraTakipAdliyeler.ajx", method="POST",
                                       payload={"ilKodu": selected_il.get("il")})
        adliyeler = json.loads(adliyeler_raw)
        
        selected_adliye = None
        adliye_clean = adliye.upper().replace("İ", "I").replace("ı", "I").strip()
        for a_obj in adliyeler:
            a_name = a_obj.get("adliyeIsmi", "").upper().replace("İ", "I").replace("ı", "I")
            if adliye_clean in a_name or a_name in adliye_clean:
                selected_adliye = a_obj
                break
        
        if not selected_adliye:
            raise ValueError(f"UYAP Adliye listesinde adliye bulunamadı: {adliye}")

        # UYAP'ta taslak oluşturulması öncesinde kontrol ekranını göstermiyoruz, doğrudan taslağı kaydediyoruz.

        # 8) MTS Takip Talebi Oluştur (Taslak Kaydet)
        # taraf_list yukarıda (harç çağrısından önce) kuruldu, tekrar kurmuyoruz.
        print("UYAP MTS üzerinde taslak oluşturuluyor...")
        harc_bilgileri = {
            "selectedTarafHashKeyList": ["kurum_0"],
            "selectedTarafHashList": harclar
        }

        dosya_bilgileri = {
            "dosyaAciklama": takip.aciklama or "",
            "takipIlKodu": selected_il.get("il"),
            "adliye": selected_adliye.get("adliyeBirimID"),
            "mahiyet": 2007,  # Cep telefonu alacağı default
            "tereke": False,
            "dosyaAciklama_48_4": takip.aciklama or "",
            "mahiyetList": [{"mahiyetId": 2007, "kod": "CEPTEL", "takipSekliKod": 7, "mahiyetAdi": "Telefon(Cep)", "gecerlimi": "E", "zorunlu": False, "degistirilemez": False}],
            "dosyaKriterList": [{"kod": "bk", "mahiyetAdi": "B.K. 100.Madde", "zorunlu": True, "degistirilemez": True, "isSelected": True, "disabled": True}],
            "terekemi": "H",
            "selectedIl": selected_il,
            "selectedBirim": {"birimId": selected_adliye.get("adliyeBirimID"), "birimAd": selected_adliye.get("adliyeIsmi")}
        }

        # Taslağı POST et
        taslak_olustur_raw = call_uyap_api(bot.page, "https://avukat.uyap.gov.tr/mtsTakipTalebiOlustur_brd.ajx", method="POST",
                                           payload={
                                               "MTS": "1",
                                               "ilamsizList": ilamsiz_list,
                                               "tarafList": taraf_list,
                                               "MTSHarcBilgileri": harc_bilgileri,
                                               "MTSDosyaBilgileri": dosya_bilgileri
                                           }, is_multipart=True)
        taslak_olustur = json.loads(taslak_olustur_raw)
        dosya_id = taslak_olustur.get("dosyaId")
        if not dosya_id:
            raise ValueError(f"UYAP taslak oluşturamadı. Yanıt: {taslak_olustur_raw}")
        
        # UYAP'tan dönen dosyaId değerinde tırnak (ör. "+enP...") varsa temizle
        if isinstance(dosya_id, str):
            dosya_id = dosya_id.strip().strip('"').strip("'")
            
        print(f"Taslak Başarıyla Oluşturuldu! Dosya ID: {dosya_id}")

        # 9) UDF Belgesini indirmek için hazırlık (Taslak oluştuktan sonra doğrudan UDF indirilip e-imzalanır)


        # 10) Kontrol ve Onay Ekranı (Ekranda masraflar ve yükleme arayüzü açıkken!)
        if veri_girisi_onay is not None:
            onaylandi = show_validation_dialog(takip, adliye, harclar)
            if not onaylandi:
                print(f"Kullanıcı onayı verilmedi, dosya atlanıyor: {takip.dosya_no}")
                raise DosyaAtla("Kullanıcı veri kontrolünde dosyayı atladı.")

        # 11) Onay verildiyse UDF Belgesini indirmek için hazırla
        print("Takip talebi belgesi indiriliyor...")
        import urllib.parse
        encoded_dosya_id = urllib.parse.quote(dosya_id)
        udf_url = f"https://avukat.uyap.gov.tr/mtsTakipTalebiHazirla_brd.avukat?dosyaId={encoded_dosya_id}"
        _b0 = takip.borclular[0] if takip.borclular else None
        _abone = (takip.abone_no or takip.hizmet_abone_no or "").strip()
        if _b0:
            _prefix = f"{_b0.ad}_{_b0.soyad}_{_abone}" if _abone else f"{_b0.ad}_{_b0.soyad}"
        else:
            _prefix = _abone or "UYAP_takip"
            
        guvenli_isim = tr_to_ascii(_prefix) + ".udf"
        kayit_yolu = os.path.join(os.path.join(os.path.expanduser("~"), "Downloads"), guvenli_isim)

        # AJAX / fetch ile GET request atıp dosyayı base64 olarak indiriyoruz (dosya bozulmasını önlemek için)
        # Hata durumlarında (ör. oturum düşmesi veya hatalı URL) HTML dönmesini kontrol ediyoruz.
        js_get = """
        async (args) => {
            const { url } = args;
            const r = await fetch(url);
            if (!r.ok) {
                throw new Error('HTTP status ' + r.status);
            }
            const contentType = r.headers.get('content-type') || '';
            if (contentType.includes('text/html')) {
                throw new Error('Response is HTML (login or error page), not binary file.');
            }
            const blob = await r.blob();
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => {
                    const base64data = reader.result.split(',')[1];
                    resolve(base64data);
                };
                reader.onerror = () => reject(new Error('FileReader failed'));
                reader.readAsDataURL(blob);
            });
        }
        """
        try:
            udf_base64 = bot.page.evaluate(js_get, {"url": udf_url})
            import base64
            udf_data = base64.b64decode(udf_base64)
            with open(kayit_yolu, "wb") as f:
                f.write(udf_data)
            print(f"UDF taslağı diske kaydedildi: {kayit_yolu}")
        except Exception as eval_err:
            print(f"⚠️ Fetch ile UDF indirme hatası: {eval_err}")
            print("Natif tarayıcı indirme yöntemi deneniyor...")
            
            # Fallback: Playwright'ın kendi native indirme yöntemiyle dene!
            with bot.page.expect_download(timeout=30000) as download_info:
                bot.page.evaluate("""
                    (url) => {
                        const link = document.createElement('a');
                        link.href = url;
                        link.download = '';
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                    }
                """, udf_url)
            download = download_info.value
            download.save_as(kayit_yolu)
            print(f"UDF taslağı native indirme ile diske kaydedildi: {kayit_yolu}")

        # 12) İmzalama programını aç ve otomatik imzalat (F7 akışı)
        class MockDownload:
            def __init__(self, path, name):
                self.path = path
                self.suggested_filename = name
            def save_as(self, p):
                pass

        # Get active window as chrome_hwnd before launching startfile
        chrome_hwnd = None
        try:
            chrome_hwnd = win32gui.GetForegroundWindow()
        except Exception:
            pass

        mock_download = MockDownload(kayit_yolu, guvenli_isim)
        imzalanan_dosya = indirmeyi_yakala(mock_download, kayit_adi=tr_to_ascii(_prefix))
        
        if not imzalanan_dosya or not os.path.exists(imzalanan_dosya):
            raise ValueError("Takip talebi belgesi imzalanamadı.")
        print(f"Dosya başarıyla imzalandı: {imzalanan_dosya}")

        # Tarayıcıyı tekrar öne al
        if win32gui.IsWindow(chrome_hwnd):
            pencereyi_one_al(chrome_hwnd)
        time.sleep(1)

        # 13) İmzalanan Takip Talebi + Vekaletname + Dayanak belgelerini PROGRAMATİK yükle
        # Eski UI tabanlı yükleme (evrak_turu_*_sec + imzali_dosya_yukle + ...) yerine
        # davaAcilisEvrakGonderme_brd.ajx isteğini tek seferde multipart olarak kurup
        # sayfanın kendi fetch'i üzerinden gönderiyoruz (bkz. mts_evrak_yukle.py).
        # Sıra orijinal istekle aynı: file_1=Takip Talebi, file_2=Vekaletname, file_3=Dayanak.
        from mts_evrak_yukle import evrak_gonder_page

        # Vekalet ve dayanak belgeleri: önce map'ten, yoksa bu klasördeki sabit dosyalardan.
        _KLASOR = os.path.dirname(os.path.abspath(__file__))
        dayanak_yolu = dayanak_map.get(takip.dosya_no) or os.path.join(_KLASOR, "DAYANAK BELGE.pdf")
        vekalet_yolu = vekalet_map.get(takip.alacakli) or os.path.join(_KLASOR, "VEKALET.pdf")

        evraklar = [{"tur": "ICR_TAKIP_TLP", "yol": imzalanan_dosya}]
        if vekalet_yolu and os.path.exists(vekalet_yolu):
            evraklar.append({"tur": "CZM_VEKALETNAME", "yol": vekalet_yolu})
        else:
            print(f"⚠️ Vekaletname bulunamadı (alacaklı: {takip.alacakli}), atlanıyor.")
        if dayanak_yolu and os.path.exists(dayanak_yolu):
            evraklar.append({"tur": "MTS_TAKIBIN_DAYANAGI", "yol": dayanak_yolu})
        else:
            print(f"⚠️ Dayanak belge bulunamadı (dosya: {takip.dosya_no}), atlanıyor.")

        print(f"Evraklar programatik gönderiliyor ({len(evraklar)} adet)...")
        sonuc = evrak_gonder_page(bot.page, dosya_id, evraklar)
        if not isinstance(sonuc, dict) or sonuc.get("type") != "success":
            raise ValueError(f"Evrak gönderme başarısız. UYAP yanıtı: {sonuc}")
        print("Tüm evraklar başarıyla gönderildi.")

        print(f"--- Takip tamamlandı (Dosya No: {takip.dosya_no}) ---")

    except Exception as e:
        print(f"HATA OLUŞTU (Dosya {takip.dosya_no}): {e}")
        import traceback
        traceback.print_exc()
        if genel_hata_onay is not None:
            genel_hata_onay(takip.dosya_no, "API_Takip_Ac", str(e))
        raise e


def main(kaynak=None):
    """API tabanlı takip açma runner'ı (TEST MODU).

    Excel/XML seçtirir, her Dosya No için takip_ac çağırır. Evrak yükleme adımıyla
    biter — ÖDEME/KESİNLEŞTİRME YAPILMAZ. Tüm takipler işlendikten sonra tarayıcı
    açık bırakılır (Enter'a basılana kadar beklenir) ki sonucu kontrol edebilesin.
    """
    if not kaynak:
        import tkinter as tk
        from tkinter import filedialog
        kok = tk.Tk()
        kok.withdraw()
        kaynak = filedialog.askopenfilename(
            title="UYAP XML veya Excel seçin",
            filetypes=[("XML / Excel", "*.xml *.xlsx"), ("XML", "*.xml"), ("Excel", "*.xlsx")],
        )
        kok.destroy()
    if not kaynak:
        print("Dosya seçilmedi, çıkılıyor.")
        return

    # XML ise önce Excel'e çevir
    if kaynak.lower().endswith(".xml"):
        from mts_takip_acan import xml_to_excel
        excel_yolu = xml_to_excel(kaynak)
    else:
        excel_yolu = kaynak

    takipler = excel_to_takipler(excel_yolu)
    print(f"{len(takipler)} takip bulundu: {[t.dosya_no for t in takipler]}")
    print("⚠️ TEST MODU: belgeler yüklenecek, ödeme/kesinleştirme YAPILMAYACAK.\n")

    bot = UyapBot()
    bot.oturumla_baglan()

    for i, takip in enumerate(takipler, 1):
        print(f"\n========== {i}/{len(takipler)} (Dosya No: {takip.dosya_no}) ==========")
        try:
            takip_ac(bot, takip)
        except DosyaAtla as e:
            print(f"⤼ Dosya No {takip.dosya_no} atlandı: {e}")
            continue
        except Exception as e:
            print(f"!!! HATA — Dosya No {takip.dosya_no} atlanıyor: {e}")
            continue

    print("\n✅ TEST tamamlandı. Belgeler yüklendi, ödeme YAPILMADI.")
    print("UYAP ekranını kontrol edebilirsin. Tarayıcı açık bırakıldı.")
    try:
        input("Kapatmak için Enter'a basın...")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
