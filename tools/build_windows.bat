@echo off
chcp 65001 >nul
title RecapAI - Windows paketi

cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
    set PYTHON=".venv\Scripts\python.exe"
) else (
    set PYTHON=python
)

echo RecapAI Windows paketi olusturuluyor...
%PYTHON% -m pip install --upgrade pip pyinstaller
%PYTHON% -m PyInstaller --noconfirm --clean tools\recapai.spec
if %errorlevel% neq 0 (
    echo [HATA] PyInstaller basarisiz.
    pause
    exit /b 1
)
echo.
echo Cikti: dist\RecapAI\RecapAI.exe
pause
