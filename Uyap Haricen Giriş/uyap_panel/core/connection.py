"""
uyap_panel.core.connection — Bağlantı (PAYLAŞ / AL) yaşam döngüsü yöneticisi.

ConnectionManager, uyap_app.py'deki start/stop_sharing + start/stop_receiving
mantığını UI'dan bağımsız, tek bir sınıfta toplar:

  • paylaş()  → office_agent.run_office  (ofis: e-imza ile UYAP'a girer, LAN + WebRTC paylaşır)
  • al()      → home_client.amain        (istemci: yereldeki 8800'ü ofise tüneller)
  • Her ikisi de ayrı bir iş parçacığında kendi asyncio olay döngüsünde koşar.
  • Alt katman print() çıktıları bir tampona (deque) yazılır; GUI ve Django bu
    tamponu poll'lar (poll_logs / /api/logs).

Tek örnek (singleton): get_manager(). Yerel localhost paneli tek süreç olduğundan
global stdout/stderr yönlendirmesi güvenlidir (özgün akış korunur — tee).
"""

import os
import sys
import asyncio
import argparse
import threading
import webbrowser
from collections import deque

from . import config
from .config import PORT, free_port_if_busy

# aiortc/uvicorn için Windows'ta Proactor döngüsü gerekir (uyap_app ile aynı).
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


class _LogBuffer:
    """İş parçacığı-güvenli halka tampon + sys.stdout/stderr 'tee'.

    Alt katman (office_agent/home_client/uvicorn) print() ile yazar. Bu tampon
    hem özgün akışa yazmayı sürdürür (terminal görünür kalsın) hem de satırları
    biriktirir; ön yüzler kümülatif metni `since(index)` ile artımlı okur.
    """

    def __init__(self, maxlen=4000):
        self._lines = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._installed = False
        self._orig_out = None
        self._orig_err = None
        self._partial = ""

    def install(self):
        with self._lock:
            if self._installed:
                return
            self._orig_out = sys.stdout
            self._orig_err = sys.stderr
            sys.stdout = _Tee(self._orig_out, self)
            sys.stderr = _Tee(self._orig_err, self)
            self._installed = True

    def write(self, text):
        if not text:
            return
        with self._lock:
            self._partial += text
            while "\n" in self._partial:
                line, self._partial = self._partial.split("\n", 1)
                self._lines.append(line)

    def snapshot(self):
        """(toplam_satır_sayısı, satır_listesi_kopyası)."""
        with self._lock:
            return len(self._lines), list(self._lines)

    def since(self, index):
        """index'ten itibaren yeni satırlar + güncel toplam sayıyı döndürür."""
        with self._lock:
            total = len(self._lines)
            if index < 0:
                index = 0
            # deque budanmış olabilir; index toplamı aşarsa baştan ver.
            if index > total:
                index = total
            return total, list(self._lines)[index:]

    def clear(self):
        with self._lock:
            self._lines.clear()
            self._partial = ""


class _Tee:
    """Özgün akışa ve _LogBuffer'a aynı anda yazan ince sarmalayıcı."""

    def __init__(self, original, buffer):
        self._original = original
        self._buffer = buffer

    def write(self, message):
        try:
            if self._original is not None:
                self._original.write(message)
        except Exception:
            pass
        self._buffer.write(message)

    def flush(self):
        try:
            if self._original is not None:
                self._original.flush()
        except Exception:
            pass

    # uvicorn log formatlayıcısı isatty/fileno arar.
    def isatty(self):
        try:
            return bool(self._original and self._original.isatty())
        except Exception:
            return False

    def fileno(self):
        if self._original is not None and hasattr(self._original, "fileno"):
            return self._original.fileno()
        raise OSError("Tee: fileno yok")


class ConnectionManager:
    """PAYLAŞ ve AL süreçlerini yönetir. Tek örnek için get_manager() kullanın."""

    def __init__(self):
        self.log = _LogBuffer()
        self.log.install()

        self.is_sharing = False
        self.is_receiving = False

        self._share_loop = None
        self._share_task = None
        self._share_thread = None
        self._recv_loop = None
        self._recv_task = None
        self._recv_thread = None

        # Beklenmedik çıkışta ön yüzün haberdar olması için durum bayrakları.
        self.last_share_error = None
        self.last_recv_error = None

    # ── Günlük ──
    def emit(self, text):
        """Üst katman (servis/UI) mesajını günlüğe yaz."""
        if not text.endswith("\n"):
            text += "\n"
        self.log.write(text)

    def logs_since(self, index):
        return self.log.since(index)

    def clear_logs(self):
        self.log.clear()

    # ── PAYLAŞ (Ofis) ──
    def start_sharing(self, *, server_url, username, password, pin, cert_id=None,
                      interval=5, port=PORT, office_code="", lan_share=None):
        if self.is_sharing:
            return False, "Paylaşım zaten aktif."
        if not pin:
            return False, "E-imza PIN kodu gerekli."

        # LAN-direct tercihi (None = ortam değişkenine dokunma, mevcut UYAP_LAN_SHARE geçerli).
        # uyap_proxy._resolve_bind_host bunu okur; office_agent açıkken LAN IP + bilet ilan eder.
        if lan_share is not None:
            os.environ["UYAP_LAN_SHARE"] = "1" if lan_share else "0"

        self.last_share_error = None
        self.is_sharing = True
        free_port_if_busy(port, self.log.write)
        self.emit("[SİSTEM] UYAP oturumu kuruluyor; LAN + dış ağ paylaşımı başlatılıyor...")

        from uyap_core import office_agent
        self._share_loop = asyncio.new_event_loop()
        args = argparse.Namespace(
            signaling=server_url, room=username, office_code=office_code, password=password,
            pin=pin, cert_id=cert_id or None, no_verify=False, interval=interval,
            proxy_port=port, host="0.0.0.0", user=username,
        )

        def runner():
            asyncio.set_event_loop(self._share_loop)
            self._share_task = self._share_loop.create_task(office_agent.run_office(args))
            try:
                self._share_loop.run_until_complete(self._share_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.last_share_error = str(e)
                self.emit(f"[HATA] Paylaşım durdu: {e}")
            finally:
                self.is_sharing = False
                self.emit("[SİSTEM] Paylaşım sonlandı.")
                try:
                    self._share_loop.close()
                except Exception:
                    pass

        self._share_thread = threading.Thread(target=runner, daemon=True)
        self._share_thread.start()

        ips = config.detect_ips()
        addrs = [f"http://127.0.0.1:{port}/giris"] + [f"http://{ip}:{port}/giris" for ip in ips]
        self.emit("[SİSTEM] Yerel ağ adresleri: " + "  |  ".join(addrs))
        self.emit(f"[SİSTEM] Dış ağ: '{username}' kullanıcısıyla {server_url} üzerinden paylaşılıyor.")
        return True, "Paylaşım başlatıldı."

    def stop_sharing(self):
        if not (self._share_loop and not self._share_loop.is_closed()):
            self.is_sharing = False
            return
        if self._share_task:
            try:
                self._share_loop.call_soon_threadsafe(self._share_task.cancel)
            except Exception as e:
                self.emit(f"[SİSTEM] Paylaşım görevi iptal edilirken hata: {e}")
        self.is_sharing = False

    # ── AL (İstemci) ──
    def start_receiving(self, *, server_url, username, password, port=PORT, office_code=""):
        if self.is_receiving:
            return False, "Bağlantı zaten aktif."

        self.last_recv_error = None
        self.is_receiving = True
        free_port_if_busy(port, self.log.write)
        self.emit("[SİSTEM] İstemci başlatılıyor (önce yerel ağ, olmazsa dış ağ)...")

        from uyap_core import home_client
        self._recv_loop = asyncio.new_event_loop()
        args = argparse.Namespace(
            signaling=server_url, room=username, office_code=office_code, password=password,
            host="127.0.0.1", port=port,
        )

        def runner():
            asyncio.set_event_loop(self._recv_loop)
            self._recv_task = self._recv_loop.create_task(home_client.amain(args))
            try:
                self._recv_loop.run_until_complete(self._recv_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.last_recv_error = str(e)
                self.emit(f"[HATA] İstemci durdu: {e}")
            finally:
                self.is_receiving = False
                self.emit("[SİSTEM] İstemci sonlandı.")
                try:
                    self._recv_loop.close()
                except Exception:
                    pass

        self._recv_thread = threading.Thread(target=runner, daemon=True)
        self._recv_thread.start()
        return True, "Bağlantı başlatıldı."

    def stop_receiving(self):
        if not (self._recv_loop and not self._recv_loop.is_closed()):
            self.is_receiving = False
            return
        if self._recv_task:
            try:
                self._recv_loop.call_soon_threadsafe(self._recv_task.cancel)
            except Exception as e:
                self.emit(f"[SİSTEM] Alıcı görevi iptal edilirken hata: {e}")
        self.is_receiving = False

    # ── Ortak ──
    def is_connected(self):
        """Toplu/İcra/MTS işleri için canlı bir UYAP yolu var mı?"""
        return self.is_sharing or self.is_receiving

    def open_browser(self, port=PORT):
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/giris")
            return True, f"http://127.0.0.1:{port}/giris"
        except Exception as e:
            self.emit(f"[HATA] Tarayıcı açılamadı: {e}")
            return False, str(e)

    def giris_url(self, port=PORT):
        return f"http://127.0.0.1:{port}/giris"

    def shutdown(self):
        self.stop_sharing()
        self.stop_receiving()

    def status(self):
        return {
            "sharing": self.is_sharing,
            "receiving": self.is_receiving,
            "connected": self.is_connected(),
            "share_error": self.last_share_error,
            "recv_error": self.last_recv_error,
        }


_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> ConnectionManager:
    """Süreç genelinde tek ConnectionManager örneği döndürür."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = ConnectionManager()
    return _MANAGER
