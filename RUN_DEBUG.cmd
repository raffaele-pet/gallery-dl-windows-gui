@echo off
setlocal
chcp 65001 >nul
title Gallery-DL - Debug
set "PROJECT_DIR=%~dp0"
if not exist "%PROJECT_DIR%.venv\Scripts\python.exe" (
    echo Gallery-DL non è installato. Esegui prima INSTALL.bat.
    pause
    exit /b 1
)
cd /d "%PROJECT_DIR%"
"%PROJECT_DIR%.venv\Scripts\python.exe" "%PROJECT_DIR%app.py"
if errorlevel 1 pause
