# Satıcı Sunucusunu Bedava Yayına Alma (alan adı GEREKMEZ)

Uzaktan erişim için internette **tek bir HTTPS adresi** gerekir: `vendor_server.py`
(statik web uygulaması + `/ws` signaling, tek serviste). Bu sunucu **UYAP verisi taşımaz**
— o veri ofis ile tarayıcı arasında doğrudan (P2P) akar. Bu yüzden bedava bir kutuda
çalışır ve kendi alan adınız olmadan, hazır `*.onrender.com` / `*.fly.dev` adresiyle
HTTPS alırsınız. (Service Worker HTTPS ister; localhost dışında bu şart.)

Ofis ajanı (`office_agent.py`, e-imza kartının takılı olduğu makine) **kendi ofisinizde**
çalışır; buluta gitmez.

---

## Seçenek A — Render.com (kart istemez, en kolay)

1. Bu projeyi bir GitHub deposuna koyun (özel depo olabilir).
   `.dockerignore` zaten ofis tarafı kodunu/sırları imaja sokmaz; yalnızca
   `vendor_server.py` + `webapp/` yüklenir.
2. https://render.com → ücretsiz hesap → **New + > Blueprint** → deponuzu seçin.
   Render `render.yaml`'ı okuyup Docker'dan derler. (Ya da **New + > Web Service** seçip
   Runtime: Docker; başka ayar gerekmez.)
3. Birkaç dakikada adresiniz hazır: `https://www.cokluuyap.com` (isim değişebilir).
4. Bağlantıyı kurun:
   - **Ofis** (e-imza takılı makine):
     ```
     python office_agent.py --signaling wss://www.cokluuyap.com/ws --room <UZUN_ODA_ANAHTARI> --pin <PIN> --cert-id <ID>
     ```
   - **Müvekkil/avukat** (herhangi bir cihaz, tarayıcı):
     ```
     https://www.cokluuyap.com/?room=<UZUN_ODA_ANAHTARI>
     ```

> Render ücretsiz katmanı boştayken uykuya geçebilir; ama ofis ajanı `/ws`'e kalıcı
> bağlı kaldığı için servis uyanık kalır. İlk açılış birkaç saniye gecikebilir.

---

## Seçenek B — Fly.io (biraz daha hızlı, kart doğrulaması ister)

```
# flyctl kurun, sonra proje klasöründe:
fly launch --no-deploy        # uygulama adı sorar; Dockerfile'ı algılar
fly deploy
```
Adresiniz: `https://<uygulama-adi>.fly.dev`. Bağlantı komutları A ile aynı (alan adını
değiştirin).

---

## Özel alan adı + TLS (Kalem 5 — opsiyonel)

Alan adı **şart değildir**: `*.onrender.com` / `*.fly.dev` adresi otomatik, ücretsiz TLS
(HTTPS) ile gelir; Service Worker da çalışır. Ama markalı bir adres ist/erseniz
(ör. `panel.buronuz.av.tr`):

1. **DNS kaydı** (alan adı sağlayıcınızın panelinde):
   - Alt alan (ör. `panel`) için **CNAME** → Render'ın verdiği `uyap-vendor.onrender.com`.
   - Apex/kök alan (ör. `buronuz.av.tr`) kullanacaksanız Render'ın gösterdiği **A/ALIAS**
     kaydını girin (Render panelde tam değeri söyler).
2. **Render → servis → Settings → Custom Domains → Add** ile alanı ekleyin. Render alanı
   doğrular ve **Let's Encrypt TLS sertifikasını otomatik** üretir/yeniler. Elle sertifika
   yönetimi YOK. (Fly.io'da: `fly certs add panel.buronuz.av.tr`.)
3. **Tekilleştirme (önerilir):** `UYAP_CANONICAL_HOST=panel.buronuz.av.tr` ayarlayın. Sunucu
   tüm GET/HEAD gezinmelerini bu adrese `308` ile yönlendirir; eski `*.onrender.com` adresi
   ve apex↔www ikiliği tek adrese toplanır. (Health-check, `/ws`, `/ice`, `/odeme/webhook`
   ve yerel adresler yönlendirmeden muaftır — deploy/entegrasyon kırılmaz.)
4. Bağlantı komutlarında adresi yeni alanla değiştirin:
   `wss://panel.buronuz.av.tr/ws` ve `https://panel.buronuz.av.tr/`.

**HTTPS otomatikleri:** İstek TLS üzerinden geldiğinde (PaaS `X-Forwarded-Proto: https`)
sunucu **HSTS** (`Strict-Transport-Security`) başlığını ekler ve oturum çerezini **Secure**
işaretler. TLS'i kendiniz sonlandırmıyorsanız `UYAP_HSTS=0` ile kapatabilirsiniz. Ayrıca her
yanıta `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN` (webapp'in same-origin
iframe'i etkilenmez) ve `Referrer-Policy: no-referrer` eklenir.

---

## Oda anahtarı = lisans

`--room` değeri ofis ile tarayıcı arasındaki ortak gizli anahtardır; aynı zamanda
kiracı/lisans kimliğidir. **Uzun ve rastgele** seçin (ör. `kemal-buro-7f3a9c1e...`).
İptal/lisans listesi için depoya `signaling_config.json` ekleyin:
```json
{ "allowed_rooms": ["kemal-buro-7f3a9c1e...", "ikinci-musteri-..."] }
```
Dosya varsa yalnızca listedeki odalar kabul edilir.

---

## Güvenlik notları

- Satıcı sunucusu yalnızca statik JS + SDP taşır; UYAP/müvekkil verisi buradan **geçmez**.
  Denetlendi: `ws_handler` yalnızca eş arasında SDP metnini iletir, hiçbir gövdeyi **diske
  yazmaz/loglamaz** (yalnızca oda anahtarının ilk 8 karakterini basar). UYAP verisi ofis↔
  tarayıcı DataChannel'ında DTLS-şifreli akar; önbellek bile **ofiste** tutulur, satıcıda değil.
- HTTPS/WSS PaaS tarafından sağlanır; kendi sertifika yönetiminiz yok.
- E-imza PIN'i artık kaynakta sabit DEĞİL: yalnızca `--pin` argümanı ya da `UYAP_PIN`
  ortam değişkeninden okunur; verilmezse program uyarıp durur. (Yayın temizliği yapıldı.)
- **Oda anahtarı = lisans.** `signaling_config.json` içinde `allowed_rooms` verilirse SADECE
  o anahtarlar buluşabilir; hem ofis hem tarayıcı rolü bu listeye karşı denetlenir. Her
  müşteriye uzun/rastgele bir anahtar verin, iptal için listeden çıkarın. Örnek:
  `signaling_config.example.json`'ı `signaling_config.json` olarak kopyalayın. Dosya yoksa
  anahtarı bilen her çift buluşur (allowlist kapalı).
- Yerel hassas dosyalar (`uyap_session_cookies.json`, `uyap_gui_config.json`, `*.log`,
  `static_cache/`) `.gitignore`'da; satıcı imajına/deposuna **girmez**.
- TURN: taraflardan biri simetrik NAT/CGNAT ardındaysa P2P kurulamayabilir; o nadir durum
  için `UYAP_ICE` ile bir TURN sunucusu ekleyin (TURN bile yalnızca DTLS-şifreli baytı görür).
