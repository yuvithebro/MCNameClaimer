@echo off
rem Double-click to start. The first run sets up Python packages (about a minute).
setlocal
cd /d "%~dp0"
call "%~dp0setup.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m mcclaimer
