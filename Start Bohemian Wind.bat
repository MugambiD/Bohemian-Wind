@echo off
REM Double-click to start Bohemian Wind on Windows.
REM The first start sets up a private copy of the Python packages it needs (a few minutes, needs the internet once).
setlocal
cd /d "%~dp0"
title Bohemian Wind
set "HOME_DIR=%LOCALAPPDATA%\BohemianWind"
set "VENV=%HOME_DIR%\venv"

REM Prefer a Python version the software is tested with; fall back to whatever is installed.
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY ( py -3 -c "import sys" >nul 2>nul && set "PY=py -3" )
if not defined PY ( python -c "import sys" >nul 2>nul && set "PY=python" )
if not defined PY goto :nopython

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || goto :oldpython

if not exist "%VENV%\Scripts\python.exe" goto :setup
fc /b requirements.txt "%HOME_DIR%\requirements.txt" >nul 2>nul || goto :setup
goto :run

:setup
echo.
echo  Setting up Bohemian Wind. This happens once and takes a few minutes.
echo.
if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"
if not exist "%VENV%\Scripts\python.exe" ( %PY% -m venv "%VENV%" || goto :fail )
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
copy /y requirements.txt "%HOME_DIR%\requirements.txt" >nul

:run
"%VENV%\Scripts\python.exe" main.py %*
set "CODE=%ERRORLEVEL%"
REM Started by double-click (no arguments): keep the window open if something went wrong.
if not "%CODE%"=="0" if "%~1"=="" pause
exit /b %CODE%

:nopython
echo.
echo  Python was not found on this computer.
echo  Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo  then double-click this file again.
echo.
pause
goto :eof

:oldpython
echo.
echo  Python 3.10 or newer was not found on this computer.
echo  Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo  then double-click this file again.
echo.
pause
goto :eof

:fail
echo.
echo  The setup did not finish. Check the internet connection, then double-click this file again.
echo.
pause
