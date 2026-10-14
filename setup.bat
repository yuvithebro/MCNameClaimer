@echo off
rem Creates .venv on first run and (re)installs requirements whenever they change.
rem Usage: setup.bat          app requirements only
rem        setup.bat dev      also installs test requirements
setlocal
cd /d "%~dp0"
set "VENV=.venv"

if exist "%VENV%\Scripts\python.exe" goto :install

call :find_python
if errorlevel 1 goto :no_python
echo Creating a private Python environment in .venv (first run only)...
%PYTHON% -m venv "%VENV%"
if errorlevel 1 goto :failed

:install
call :ensure requirements.txt || goto :failed
if /i "%~1"=="dev" call :ensure requirements-dev.txt || goto :failed
exit /b 0

:ensure
fc /b "%~1" "%VENV%\%~1.installed" >nul 2>&1
if not errorlevel 1 exit /b 0
echo Installing %~1 ...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "%~1"
if errorlevel 1 exit /b 1
copy /y "%~1" "%VENV%\%~1.installed" >nul
exit /b 0

:find_python
py -3 -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (set "PYTHON=py -3" & exit /b 0)
python -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (set "PYTHON=python" & exit /b 0)
exit /b 1

:no_python
echo.
echo Python 3.10 or newer (with tkinter) was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo then run this again.
exit /b 1

:failed
echo.
echo Setup failed. Check your internet connection and try again.
exit /b 1
