#!/usr/bin/env python3
"""
Ofis tarafı paylaşılan çalışma-zamanı durumu
--------------------------------------------
UYAP'a giden DEĞİŞTİREN istekleri tek tek (FIFO) işlemek için TEK bir yazma kilidi.
Hem anlık proxy istekleri (office_agent.handle_uyap_request, uyap_proxy.proxy) hem de iş
kuyruğu işleyicileri (jobs.JobContext.uyap) AYNI kilidi paylaşır; böylece bir kullanıcının
formu, bir başkasının çok adımlı sihirbazı ve arka plandaki toplu iş (ör. çoklu takip açma)
birbirinin UYAP sunucu-tarafı oturum durumunu BOZMAZ. GET'ler (okuma) kilitlenmez.

Ayrı bir modülde durması, office_agent ↔ jobs arasında döngüsel import oluşmadan ikisinin
de aynı kilit nesnesine erişmesini sağlar.

── ADİL DAĞITIM (bireysel-öncelikli geçit) ───────────────────────────────────────────────
Toplu iş (batch), bireysel kullanıcıları kilitlememeli. Çözüm: yazma kilidini iki tür
"geçit" üzerinden veriyoruz:

  • interactive_write()  — bireysel kullanıcı (anlık proxy/tünel isteği). YÜKSEK öncelik:
        gelince "bekleyen bireysel" sayacını artırır, sonra kilidi normal alır.
  • batch_write()        — toplu iş (jobs.JobContext.uyap). DÜŞÜK öncelik: kilidi almadan
        ÖNCE, bekleyen bir bireysel istek varsa o(lar) bitene kadar bekler (drenaj).

Toplu iş her yazma ADIMINI ayrı bir batch_write() ile geçtiği için, bir kullanıcı isteği
geldiğinde toplu iş sıradaki adımdan ÖNCE duraklar, kullanıcının işi bitince devam eder.
GET'ler hiçbir geçide girmediği için bireysel okuma asla beklemez.

UYARI: kesintisiz bireysel yazma akışında toplu iş teorik olarak uzun süre bekleyebilir
(starvation). Ürün gereksinimi "bireysel kullanıcı toplu işten etkilenmesin" yönünde
olduğundan bu kabul edilmiştir; gerekirse ileride batch'e bir taban ilerleme garantisi
eklenebilir.
"""

import asyncio
from contextlib import asynccontextmanager

# Tek tek (sırayla) işlenmesi gereken HTTP metotları.
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Süreç boyunca tek örnek. asyncio.Lock'un olayı (event loop) ilk kullanımda bağlanır;
# çalışan döngü içinde kullanıldığı sürece modül düzeyinde oluşturulması güvenlidir.
UYAP_WRITE_LOCK = asyncio.Lock()

# ── Bireysel-öncelikli geçit durumu ──────────────────────────────────────────────────────
# Bekleyen/işlenen bireysel (interactive) yazma sayısı ve "hiç bireysel kalmadı" olayı.
_interactive_pending = 0
_no_interactive = asyncio.Event()
_no_interactive.set()   # başlangıçta bekleyen bireysel yok


@asynccontextmanager
async def interactive_write():
    """Bireysel kullanıcının yazma isteği için yüksek öncelikli geçit.
    Sayacı artırır (batch'in görmesi için), sonra yazma kilidini alır."""
    global _interactive_pending
    _interactive_pending += 1
    _no_interactive.clear()
    try:
        async with UYAP_WRITE_LOCK:
            yield
    finally:
        _interactive_pending -= 1
        if _interactive_pending <= 0:
            _interactive_pending = 0
            _no_interactive.set()


@asynccontextmanager
async def batch_write():
    """Toplu işin yazma adımı için düşük öncelikli geçit. Kilidi almadan ÖNCE, bekleyen
    bireysel istek kalmayana kadar bekler; böylece her adımdan önce kullanıcıya yol verir."""
    # Bekleyen bireysel istek varsa drenajı bekle. Uyandıktan sonra yeni bir bireysel
    # gelmiş olabilir; bu yüzden döngüde tekrar kontrol et (bireysel önceliği korunur).
    while _interactive_pending > 0:
        await _no_interactive.wait()
    async with UYAP_WRITE_LOCK:
        yield


# ── İstemci "okuma ipucu" başlığı (adil sıralama) ──────────────────────────────────────────
# Bazı UYAP sorguları durum DEĞİŞTİRMEZ (yalnızca okur) ama UYAP onları POST ile yaptırır
# (ör. icra/SGK dosya sorgusu). Varsayılan sınıflandırma POST'u "yazma" sayıp yüksek öncelikli
# yazma kilidine sokar; toplu bir sorgu (binlerce dosya) bu kilidi sürekli tutarsa DİĞER
# kullanıcıların bireysel istekleri kuyrukta bekler. İstemci böyle bir POST'u bu başlıkla
# "beni okuma say, kilide sokma" diye işaretleyebilir (jobs tarafındaki write=False'un
# proxy/tünel-tarafı karşılığı). Bilinmeyen başlık eski ofislerce yok sayılır → geriye uyumlu.
READ_HINT_HEADER = "x-uyap-read"


def is_read_hint(headers):
    """headers (dict ya da Starlette Headers) içinde okuma-ipucu başlığı VAR ve doğru mu?
    Büyük/küçük harf duyarsız; değer '0'/'false'/'no'/'' ise yok sayılır."""
    if not headers:
        return False
    val = None
    getter = getattr(headers, "get", None)
    if callable(getter):
        try:
            val = headers.get(READ_HINT_HEADER)
        except Exception:
            val = None
    if val is None:
        try:
            for k, v in headers.items():
                if str(k).lower() == READ_HINT_HEADER:
                    val = v
                    break
        except Exception:
            pass
    if val is None:
        return False
    return str(val).strip().lower() not in ("", "0", "false", "no")
