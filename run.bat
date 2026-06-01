@echo off
chcp 65001 >nul
title RecapAI - Manhwa Recap Studio

cd /d "%~dp0"

:: .venv kontrolu
if exist ".venv\Scripts\python.exe" (
    set PYTHON=".venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set PYTHON="venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if %errorlevel% == 0 (
        set PYTHON=python
    ) else (
        echo [HATA] Python bulunamadi!
        echo Lutfen bir sanal ortam olusturun:
        echo   python -m venv .venv
        echo   .venv\Scripts\pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo RecapAI baslatiliyor...
%PYTHON% main.py

if %errorlevel% neq 0 (
    echo.
    echo [HATA] Uygulama hatayla kapandi. Cikis kodu: %errorlevel%
    pause
)