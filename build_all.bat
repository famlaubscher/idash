@echo off
setlocal
cd /d "%~dp0"

echo === iDash Build ===

if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) else (
    echo WARNUNG: venv nicht gefunden.
)

pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo FEHLER: PyInstaller nicht gefunden. Bitte: pip install pyinstaller
    goto :fail
)

echo.
echo Baue iDash.exe ...
pyinstaller --noconfirm --clean --onefile --windowed --name iDash --distpath dist --icon "app\idash_logo.ico" --hidden-import PyQt5.QtWebEngineWidgets --hidden-import PyQt5.QtWebEngineCore --hidden-import PyQt5.QtWebChannel --hidden-import PyQt5.QtNetwork --hidden-import irsdk --hidden-import websockets --hidden-import asyncio --add-data "overlays;overlays" --add-data "app\idash_logo.png;app" --add-data "app\idash_logo.ico;app" app\overlay_manager.py

if errorlevel 1 (
    echo FEHLER: Build fehlgeschlagen.
    goto :fail
)

echo.
echo === Build erfolgreich! ===
echo Portabler Ordner: dist\iDash\
echo Starten:          dist\iDash\iDash.exe
goto :end

:fail
echo Build fehlgeschlagen.

:end
endlocal
