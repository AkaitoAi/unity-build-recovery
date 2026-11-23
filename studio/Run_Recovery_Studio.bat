@echo off
title Aether Recon Studio
cd /d "%~dp0"
:: Starts desktop_shell.py (local server + plain window). pythonw avoids a console window.
set "PYTHON_EXE=pythonw"
if exist "%~dp0..\pyenv\Scripts\pythonw.exe" set "PYTHON_EXE=%~dp0..\pyenv\Scripts\pythonw.exe"
start "" "%PYTHON_EXE%" "%~dp0desktop_shell.py"
exit /b 0
