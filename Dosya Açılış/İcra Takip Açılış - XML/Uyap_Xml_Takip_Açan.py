import subprocess
import os
import time
from playwright.sync_api import sync_playwright

def chrome_hazirla():
    """Chrome portla açık mı kontrol et, değilse yeniden başlat."""
    import socket
    
    # 9222 portu boşta mı? (Bağlantı reddedildi hatası varsa port boş demektir)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 9222))
    sock.close()
    
    if result != 0:
        print("Chrome 9222 portunda açık değil. Yeniden başlatılıyor...")
        # Tüm chrome süreçlerini öldür
        os.system("taskkill /f /im chrome.exe")
        time.sleep(1)
        
        # Chrome'u portla başlat
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        subprocess.Popen([
            chrome_path, 
            "--remote-debugging-port=9222",
            "--restore-last-session"
        ])
        time.sleep(4) # Açılması için bekle
    else:
        print("Chrome zaten portla açık, bağlanılıyor...")

def main():
    # Önce port kontrolü yap
    chrome_hazirla()
    
    with sync_playwright() as p:
        # Şimdi bağlanmayı dene
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        # ... geri kalan kodunuz ...
