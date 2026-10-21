@echo off
rem SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
rem Copyright (C) 2026 korxspyre
rem Opens the NanoBorealis client. The first run sets up its own Python environment;
rem later runs update it whenever the client needs new packages.
setlocal
set "VENV=%LOCALAPPDATA%\NanoBorealis\client-venv"
rem A client set up before the rename keeps its environment instead of downloading a second one.
if not exist "%VENV%\Scripts\pythonw.exe" if exist "%LOCALAPPDATA%\NanoAurora\client-venv\Scripts\pythonw.exe" set "VENV=%LOCALAPPDATA%\NanoAurora\client-venv"
rem Bump DEPS whenever the package list below changes, so existing installs pick it up.
set "DEPS=2"
set "STAMP=%VENV%\nanoborealis-deps.txt"
set "HAVE="
if exist "%STAMP%" set /p HAVE=<"%STAMP%"
if exist "%VENV%\Scripts\pythonw.exe" if "%HAVE%"=="%DEPS%" goto :run

set "FIRST="
if not exist "%VENV%\Scripts\pythonw.exe" (
  set "FIRST=1"
  echo Setting up the NanoBorealis client. This happens once and takes a minute...
  py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%" || goto :fail
) else (
  echo Updating the NanoBorealis client's packages...
)
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check "flet[desktop]==1.0.1" "websockets>=14" "zeroconf>=0.130" || goto :fail
>"%STAMP%" echo %DEPS%
if not defined FIRST goto :run
rem The first launch downloads Flet's window runtime; run it here so the progress shows.
"%VENV%\Scripts\python.exe" "%~dp0src\main.py"
exit /b 0

:run
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0src\main.py"
exit /b 0

:fail
echo.
echo Setup failed. The client needs Python 3.10 or newer: https://www.python.org/downloads/
pause
exit /b 1
