@echo off
rem Runs the automated test suite. Makes no network requests.
setlocal
cd /d "%~dp0"
call "%~dp0setup.bat" dev
if errorlevel 1 goto :end
".venv\Scripts\python.exe" -m pytest
:end
pause
