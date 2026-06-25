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
    echo FEHLER: PyInstaller nicht gefunden. Bitte: pip install -r requirements-build.txt
    goto :fail
)

REM Version aus der einzigen Quelle der Wahrheit lesen
for /f "delims=" %%v in ('python -c "import app._version as v; print(v.__version__)"') do set "VER=%%v"
if "%VER%"=="" set "VER=0.0.0"
echo Version: %VER%

echo.
echo Baue iDash (--onedir, Velopack-kompatibel) ...
REM WICHTIG: --onedir (kein --onefile) — Velopack aktualisiert ein Verzeichnis.
pyinstaller --noconfirm --clean --windowed --name iDash --distpath dist --icon "app\idash_logo.ico" --hidden-import PyQt5.QtWebEngineWidgets --hidden-import PyQt5.QtWebEngineCore --hidden-import PyQt5.QtWebChannel --hidden-import PyQt5.QtNetwork --hidden-import irsdk --hidden-import websockets --hidden-import asyncio --add-data "overlays;overlays" --add-data "app\idash_logo.png;app" --add-data "app\idash_logo.ico;app" app\overlay_manager.py

if errorlevel 1 (
    echo FEHLER: Build fehlgeschlagen.
    goto :fail
)

echo.
echo === PyInstaller-Build erfolgreich ===
echo Portabler Ordner: dist\iDash\

REM ── Velopack-Paket (optional; benoetigt .NET SDK + 'dotnet tool install -g vpk') ──
vpk --version >nul 2>&1
if errorlevel 1 (
    echo Hinweis: 'vpk' nicht gefunden - ueberspringe Velopack-Pack.
    echo          Installieren mit: dotnet tool install -g vpk
    goto :end
)

echo.
echo Packe Velopack-Release %VER% ...
vpk pack --packId ApexOverlay.iDash --packTitle iDash --packVersion %VER% --packDir dist\iDash --mainExe iDash.exe --icon "app\idash_logo.ico" --outputDir releases
if errorlevel 1 (
    echo FEHLER: vpk pack fehlgeschlagen.
    goto :fail
)
echo === Velopack-Release in releases\ erstellt ===
goto :end

:fail
echo Build fehlgeschlagen.

:end
endlocal
