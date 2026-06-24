@echo off
cd /d "%~dp0"

REM Virtuelle Umgebung aktivieren
IF EXIST ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) ELSE IF EXIST "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

cd app

REM Overlay-Manager startet den Telemetry-Server intern als Thread
start "iDash" pythonw overlay_manager.py

