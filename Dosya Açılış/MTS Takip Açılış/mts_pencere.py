# -*- coding: utf-8 -*-
"""
MTS Pencere Yönetimi (Windows)
==============================
mts_takip_acan.py'den AYRILDI (2026-06-26 modülerleştirme).

Burada SADECE Windows pencere işlemleri bulunur (win32gui/win32con/
win32process/win32api). Playwright veya UYAP mantığı YOKTUR. Bu yüzden
güvenle her yerden import edilebilir.

İçerik:
  - _pencere_basligi(hwnd)        -> pencere başlığını güvenle okur
  - pencereyi_one_al(hwnd, ...)   -> pencereyi foreground'a getirir
"""

import time
import win32gui
import win32con
import win32process
import win32api


def _pencere_basligi(hwnd):
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def pencereyi_one_al(hwnd, deneme=6):
    """
    Bir pencereyi gerçekten öne alır.
    Windows'un 'foreground lock' kısıtlamasını AttachThreadInput + Alt tuşu
    hilesiyle aşar. Pencere gerçekten öne geldiyse True döner.
    """
    for _ in range(deneme):
        try:
            if not win32gui.IsWindow(hwnd):
                return False

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            fg = win32gui.GetForegroundWindow()
            cur_thread = win32api.GetCurrentThreadId()
            fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            hedef_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

            # Alt tuşuna basıp bırakmak Windows'un foreground kilidini gevşetir
            try:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            except Exception:
                pass

            attached = []
            try:
                if fg_thread and fg_thread != cur_thread:
                    win32process.AttachThreadInput(cur_thread, fg_thread, True)
                    attached.append(fg_thread)
                if hedef_thread != cur_thread:
                    win32process.AttachThreadInput(cur_thread, hedef_thread, True)
                    attached.append(hedef_thread)

                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            finally:
                for t in attached:
                    try:
                        win32process.AttachThreadInput(cur_thread, t, False)
                    except Exception:
                        pass

            time.sleep(0.4)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception as e:
            print(f"  Öne alma denemesi hata verdi (önemsiz): {e}")
            time.sleep(0.4)

    return win32gui.GetForegroundWindow() == hwnd
