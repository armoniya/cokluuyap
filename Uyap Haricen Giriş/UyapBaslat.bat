@echo off
chcp 65001 >nul
title UYAP Ag Gecidi
cd /d "%~dp0"

rem venv varsa onu kullan, yoksa sistem python'u
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo UYAP Ag Gecidi baslatiliyor...
"%PY%" uyap_app.py

rem Hata olursa pencere kapanmasin ki mesaji gorebilesin
if errorlevel 1 (
    echo.
    echo [HATA] Uygulama beklenmedik sekilde kapandi. Yukaridaki mesaja bak.
    pause
)
