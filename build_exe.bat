@echo off
REM Rebuild BohemianWindQC.exe (the data checks alone, step 1) on Windows. Needs Python 3.12 (64-bit) on PATH.
REM The full application starts with "Start Bohemian Wind.bat" and needs no build.
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-exe.txt
pyinstaller --noconfirm --clean --onedir --windowed --name BohemianWindQC --icon icon.ico --add-data "icon.ico;." --exclude-module pytest --exclude-module tests --exclude-module IPython --exclude-module py_wake --exclude-module scipy --exclude-module xarray qc_main.py
echo.
echo Built: %~dp0dist\BohemianWindQC\BohemianWindQC.exe
pause
