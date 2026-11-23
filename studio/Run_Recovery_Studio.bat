@echo off
title Aether Recon Studio
cd /d "%~dp0"
:: Starts desktop_shell.py (local server + app window). pythonw avoids a console window.
set "PYTHON_EXE="
if exist "%~dp0..\pyenv\Scripts\pythonw.exe" set "PYTHON_EXE=%~dp0..\pyenv\Scripts\pythonw.exe"
if not defined PYTHON_EXE for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if defined PYTHON_EXE (
  start "" "%PYTHON_EXE%" "%~dp0desktop_shell.py"
  exit /b 0
)
where pyw >nul 2>&1
if not errorlevel 1 (
  start "" pyw -3 "%~dp0desktop_shell.py"
  exit /b 0
)
echo Python was not found. Install Python 3.10 or later from python.org, then run this again.
pause
exit /b 1
