# -*- coding: utf-8 -*-
"""
modules.tray — Windows sistem tepsisi (system tray) yardımcısı.

Saf ctypes (Shell_NotifyIcon) ile, EK BAĞIMLILIK olmadan bir tepsi simgesi
oluşturur. Panel pencereyi görev çubuğundan gizleyip tepsiye indirebilir;
tepsi simgesine tıklayınca pencere geri yüklenir.

Yalnızca Windows'ta etkindir. Başka platformda ya da herhangi bir hata olursa
``available`` False kalır ve panel normal "simge durumuna küçült"e düşer.

Tk thread-safe DEĞİLDİR: tepsi olayları arka plandaki kendi mesaj döngüsünde
(message pump) yakalanır, ardından eylem ``root.after(0, ...)`` ile Tk ana iş
parçacığına aktarılır.
"""

import sys
import threading

available_platform = sys.platform.startswith("win")

if available_platform:
    import ctypes
    from ctypes import wintypes

    # ── Win32 sabitleri ──
    WM_DESTROY        = 0x0002
    WM_CLOSE          = 0x0010
    WM_COMMAND        = 0x0111
    WM_USER           = 0x0400
    WM_TRAYICON       = WM_USER + 20
    WM_LBUTTONUP      = 0x0202
    WM_LBUTTONDBLCLK  = 0x0203
    WM_RBUTTONUP      = 0x0205

    NIM_ADD    = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002

    NIF_MESSAGE = 0x00000001
    NIF_ICON    = 0x00000002
    NIF_TIP     = 0x00000004

    IDI_APPLICATION = 32512
    HWND_MESSAGE = -3

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    # ── Fonksiyon prototipleri ──
    # 64-bit'te pointer (HWND/HICON/HMODULE) döndüren fonksiyonların restype'ı
    # belirtilmezse ctypes dönüşü 32-bit int'e KIRPAR → bozuk tutamaç → tepsi
    # simgesi geçersiz pencereye bağlanır ve görünmez. Bu yüzden açıkça tanımla.
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _shell32 = ctypes.windll.shell32

    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    _user32.DefWindowProcW.restype = LRESULT
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]

    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.RegisterClassW.argtypes = [ctypes.c_void_p]

    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]

    # 2. parametre bir kaynak kimliği (MAKEINTRESOURCE/IDI_APPLICATION) olabilir;
    # LPCWSTR'a tam sayı geçmek ArgumentError verir → c_void_p kullan.
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]

    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadImageW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p,
                                   wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]

    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]

    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]

    LR_DEFAULTSIZE   = 0x00000040
    LR_SHARED        = 0x00008000
    IMAGE_ICON       = 1

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
        ]

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]


class SystemTray:
    """Pencereyi tepsiye indirip simgesinden geri yükleyen sistem tepsisi.

    Kullanım:
        tray = SystemTray(root, "UYAP Çalışma Paneli", on_restore=panel._from_tray)
        tray.start()          # arka plan mesaj döngüsünü başlatır
        tray.show()           # tepsi simgesini ekler  (panel pencereyi gizler)
        ... simgeye tıklanınca on_restore çağrılır ...
        tray.hide()           # tepsi simgesini kaldırır (panel pencereyi geri yükler)
        tray.stop()           # kapanışta temizle
    """

    def __init__(self, root, title="UYAP Çalışma Paneli", on_restore=None):
        self.root = root
        self.title = title[:127]
        self.on_restore = on_restore
        self.available = available_platform
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._shown = False
        self._wndproc = None      # GC'ye yedirmemek için referans tut
        self._nid = None

    # ── genel arayüz ──
    def start(self):
        """Arka planda gizli mesaj penceresini ve döngüsünü kurar (bir kez)."""
        if not self.available or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._pump, name="systray",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)   # pencere hazır olana dek kısa bekle

    def show(self):
        """Tepsi simgesini ekler. Pencerenin gizlenmesi panel tarafında yapılır.

        Simge eklenemezse (örn. tutamaç geçersiz) ``False`` döner; panel bu
        durumda pencereyi gizlemeyip normal davranabilsin diye sonucu döndürür.
        """
        if not self.available or self._hwnd is None:
            return False
        if self._shown:
            return True
        try:
            ok = ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD,
                                                          ctypes.byref(self._nid))
            self._shown = bool(ok)
            return self._shown
        except Exception:
            return False

    def hide(self):
        """Tepsi simgesini kaldırır."""
        if not self.available or self._hwnd is None or not self._shown:
            return
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except Exception:
            pass
        self._shown = False

    def stop(self):
        """Simgeyi kaldırır ve mesaj döngüsünü sonlandırır (kapanışta)."""
        self.hide()
        if self.available and self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ── tepsi olayı → Tk ana iş parçacığı ──
    def _on_click(self):
        if callable(self.on_restore):
            try:
                self.root.after(0, self.on_restore)
            except Exception:
                pass

    # ── arka plan: gizli pencere + mesaj döngüsü ──
    def _pump(self):
        try:
            hinst = ctypes.windll.kernel32.GetModuleHandleW(None)

            def _proc(hwnd, msg, wparam, lparam):
                if msg == WM_TRAYICON:
                    if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP):
                        self._on_click()
                    return 0
                if msg == WM_DESTROY:
                    ctypes.windll.user32.PostQuitMessage(0)
                    return 0
                return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc = WNDPROC(_proc)

            wc = WNDCLASS()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "UyapPanelTrayWindow"
            ctypes.windll.user32.RegisterClassW(ctypes.byref(wc))

            self._hwnd = ctypes.windll.user32.CreateWindowExW(
                0, wc.lpszClassName, self.title, 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, hinst, None)

            # Tepsi simge verisi (varsayılan uygulama simgesi + ipucu metni)
            hicon = ctypes.windll.user32.LoadIconW(None, IDI_APPLICATION)
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = hicon
            nid.szTip = self.title
            self._nid = nid
        except Exception:
            self.available = False
            self._ready.set()
            return

        self._ready.set()

        try:
            msg = wintypes.MSG()
            user32 = ctypes.windll.user32
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
