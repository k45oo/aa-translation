@echo off
chcp 65001 >nul
title AA Workbench Restart
cd /d "%~dp0"

rem ===== port: AA_PORT (from .env, else real env, else 8765) =====
rem stage 5 (2026-10-08): port is configurable, so start and kill both follow AA_PORT.
if defined AA_PORT set "AA_PORT=%AA_PORT%"
if not defined AA_PORT set "AA_PORT=8765"
if exist "%~dp0.env" for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do if /i "%%a"=="AA_PORT" set "AA_PORT=%%b"

rem ===== kill old process on that port =====
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%AA_PORT% ^| findstr LISTENING') do (
    taskkill /f /pid %%a >nul 2>nul
)

rem ===== kill leftover aa_web.py (stale instance may not listen, double scheduler hog CPU) =====
for /f "tokens=2 delims=," %%b in ('wmic process where "name='python.exe' and commandline like '%%aa_web.py%%'" get processid /format:csv ^| findstr /r "[0-9]"') do (
    taskkill /f /pid %%b >nul 2>nul
)

rem ===== wait port release =====
ping -n 3 127.0.0.1 >nul

rem ===== start =====
set "PYTHONPATH="
echo Starting AA Workbench at http://127.0.0.1:%AA_PORT%/
".venv\Scripts\python.exe" scripts\tools\aa_web.py
echo.
echo Service stopped.
echo Closing in 3 seconds...
timeout /t 3 >nul
exit
