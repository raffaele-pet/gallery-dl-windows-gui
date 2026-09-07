@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Gallery-DL - Installazione e aggiornamento
color 0F

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_GDL=%VENV_DIR%\Scripts\gallery-dl.exe"
set "VENV_YTDLP=%VENV_DIR%\Scripts\yt-dlp.exe"
set "CHECK_ONLY=0"
if /I "%~1"=="--check" set "CHECK_ONLY=1"

echo ================================================================
echo                    GALLERY-DL INSTALLER
echo ================================================================
echo Cartella progetto: %PROJECT_DIR%
echo.

if not exist "%PROJECT_DIR%app.py" (
    call :fatal "Manca app.py."
    exit /b 1
)
if not exist "%PROJECT_DIR%RUN.vbs" (
    call :fatal "Manca RUN.vbs."
    exit /b 1
)
if not exist "%PROJECT_DIR%assets\gallery-dl-logo.png" (
    call :fatal "Manca il logo dell'applicazione."
    exit /b 1
)

call :find_python
call :find_ffmpeg

if "%CHECK_ONLY%"=="1" (
    echo [CONTROLLO] Python: !PYTHON_STATUS!
    if exist "%VENV_GDL%" (
        for /f "delims=" %%V in ('"%VENV_GDL%" --version 2^>nul') do set "GDL_VERSION=%%V"
        echo [OK] gallery-dl !GDL_VERSION!
    ) else (
        echo [MANCANTE] Ambiente locale gallery-dl
    )
    if "!FFMPEG_OK!"=="1" (echo [OK] FFmpeg) else (echo [MANCANTE] FFmpeg)
    if defined PYTHON_LAUNCHER if exist "%VENV_GDL%" if "!FFMPEG_OK!"=="1" exit /b 0
    exit /b 2
)

where winget.exe >nul 2>&1
if errorlevel 1 (
    call :fatal "WinGet non è disponibile. Installa 'App Installer' dal Microsoft Store e riprova."
    exit /b 1
)

echo [1/6] Controllo di Python...
if not defined PYTHON_LAUNCHER (
    echo [INSTALLA] Python 3.12
    winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        call :fatal "Installazione di Python non riuscita."
        exit /b 1
    )
    set "PYTHON_LAUNCHER=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not exist "!PYTHON_LAUNCHER!" call :find_python
)
if not defined PYTHON_LAUNCHER (
    call :fatal "Python non è stato trovato dopo l'installazione."
    exit /b 1
)
echo [OK] !PYTHON_STATUS!

echo.
echo [2/6] Preparazione dell'ambiente isolato...
if not exist "%VENV_PY%" (
    "!PYTHON_LAUNCHER!" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        call :fatal "Creazione dell'ambiente virtuale non riuscita."
        exit /b 1
    )
) else (
    echo [OK] Ambiente virtuale già presente.
)
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    call :fatal "Aggiornamento degli strumenti Python non riuscito."
    exit /b 1
)

echo.
echo [3/6] Installazione o aggiornamento di gallery-dl e integrazioni...
"%VENV_PY%" -m pip install --upgrade gallery-dl yt-dlp requests PySocks brotli zstandard PyYAML truststore Jinja2
if errorlevel 1 (
    call :fatal "Installazione dei pacchetti Python non riuscita."
    exit /b 1
)

echo.
echo [4/6] Controllo di FFmpeg per video HLS/DASH e Pixiv Ugoira...
call :find_ffmpeg
if "!FFMPEG_OK!"=="0" (
    winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [INFO] Pacchetto principale non disponibile, provo il pacchetto FFmpeg per yt-dlp...
        winget install --id yt-dlp.FFmpeg --exact --accept-package-agreements --accept-source-agreements
    )
    call :find_ffmpeg
    if "!FFMPEG_OK!"=="0" echo [AVVISO] FFmpeg non è visibile nel PATH corrente. Potrebbe diventarlo dopo il riavvio.
) else (
    echo [OK] FFmpeg già disponibile.
    winget upgrade --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements >nul 2>&1
    winget upgrade --id yt-dlp.FFmpeg --exact --accept-package-agreements --accept-source-agreements >nul 2>&1
)

echo.
echo [5/6] Verifica finale...
if not exist "%VENV_GDL%" (
    call :fatal "gallery-dl.exe non è stato creato."
    exit /b 1
)
if not exist "%VENV_YTDLP%" (
    call :fatal "yt-dlp.exe non è stato creato."
    exit /b 1
)
"%VENV_GDL%" --version
if errorlevel 1 (
    call :fatal "gallery-dl non supera la verifica."
    exit /b 1
)
"%VENV_YTDLP%" --version
if errorlevel 1 (
    call :fatal "yt-dlp non supera la verifica."
    exit /b 1
)
"%VENV_PY%" -m py_compile "%PROJECT_DIR%app.py"
if errorlevel 1 (
    call :fatal "app.py contiene un errore di sintassi."
    exit /b 1
)

echo.
echo [6/6] Creazione del collegamento sul Desktop...
set "GDL_PROJECT=%PROJECT_DIR%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$project=$env:GDL_PROJECT.TrimEnd('\'); $desktop=[Environment]::GetFolderPath('Desktop'); $shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Gallery-DL.lnk')); $shortcut.TargetPath=(Join-Path $env:SystemRoot 'System32\wscript.exe'); $shortcut.Arguments='""'+(Join-Path $project 'RUN.vbs')+'""'; $shortcut.WorkingDirectory=$project; $shortcut.Description='Gallery-DL - Image and gallery downloader'; $shortcut.IconLocation=(Join-Path $env:SystemRoot 'System32\imageres.dll')+',67'; $shortcut.Save()"
if errorlevel 1 (
    echo [AVVISO] Non è stato possibile creare il collegamento. RUN.vbs rimane utilizzabile.
) else (
    echo [OK] Collegamento Gallery-DL creato sul Desktop.
)

echo.
echo ================================================================
echo Installazione e aggiornamento completati.
echo Avvia l'app con RUN.vbs o con il collegamento Gallery-DL.
echo ================================================================
pause
exit /b 0

:find_python
set "PYTHON_LAUNCHER="
set "PYTHON_STATUS=non disponibile"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_LAUNCHER=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) else (
    for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYTHON_LAUNCHER set "PYTHON_LAUNCHER=%%P"
)
if defined PYTHON_LAUNCHER for /f "delims=" %%V in ('"!PYTHON_LAUNCHER!" --version 2^>^&1') do set "PYTHON_STATUS=%%V"
exit /b 0

:find_ffmpeg
set "FFMPEG_OK=0"
where ffmpeg.exe >nul 2>&1 && set "FFMPEG_OK=1"
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "FFMPEG_OK=1"
exit /b 0

:fatal
echo.
echo ================================================================
echo [ERRORE] %~1
echo ================================================================
if "%CHECK_ONLY%"=="0" pause
exit /b 1
