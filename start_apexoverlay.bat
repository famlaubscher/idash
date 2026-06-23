@echo off
REM Projekt-Root auf aktuellen Ordner setzen (dort wo diese BAT liegt)
cd /d "%~dp0"

REM Virtuelle Umgebung aktivieren (falls du venv\ hast)
IF EXIST "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

REM In den app-Ordner wechseln, damit die Imports (hud_builder etc.) funktionieren
cd app

REM Telemetry-Server starten (eigenes Fenster)
start "Telemetry Server" python telemetry_server.py

REM Overlay-Manager starten (zweites Fenster)
start "Overlay Manager" python overlay_manager.py

REM Konsole offen lassen, damit du Fehler siehst, falls was crasht
pause
