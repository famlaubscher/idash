@echo off
cd /d "%~dp0"

IF EXIST "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

cd app

set IDASH_RECORD=1
python overlay_manager.py

pause
