@echo off
chcp 65001 >nul
title UYAP Ofis Ajani (web panel)
cd /d "%~dp0"

rem Ofis ajani bagimliliklari (aiortc/websockets) "Uyap Haricen Giris" venv'indedir.
rem Klasor adindaki Turkce karakter sorun cikarmasin diye joker ile bulunur.
set "PY=python"
for /d %%D in ("..\..\Uyap Haricen Giri*") do (
    if exist "%%~fD\.venv\Scripts\python.exe" set "PY=%%~fD\.venv\Scripts\python.exe"
)

echo UYAP Calisma Paneli (web) baslatiliyor: http://127.0.0.1:8090
echo Kayitli kimlik + PIN varsa paylasim otomatik baslar (giris gerekmez).
start "" "http://127.0.0.1:8090"
"%PY%" server.py

rem Hata olursa pencere kapanmasin ki mesaji gorebilesin
if errorlevel 1 (
    echo.
    echo [HATA] Panel beklenmedik sekilde kapandi. Yukaridaki mesaja bak.
    pause
)
