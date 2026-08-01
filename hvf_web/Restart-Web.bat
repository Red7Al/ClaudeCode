@echo off
REM ======================================================================================================
REM  Restart the Squeeze web app + ngrok public share. NO rebuild (starts immediately).
REM  Double-click this file to run it (user 2026-06-29: restart without Claude credits).
REM
REM  - Opens TWO windows that must stay open: the web server, and the ngrok share.
REM  - Local address:  http://127.0.0.1:5057
REM  - Public address:  shown in the ngrok window (https://...ngrok-free.dev)
REM  - To ALSO rebuild the snapshot (scans ~600 instruments, ~25 min) run run.bat instead.
REM ======================================================================================================

cd /d "%~dp0.."

REM Keep Python's __pycache__ OUT of the OneDrive-synced repo
set PYTHONPYCACHEPREFIX=%TEMP%\hvf_pycache

REM ======================================================================================================
REM Check all python libraries are installed
REM ======================================================================================================

call :CheckLibrary 1 json
call :CheckLibrary 1 base64
call :CheckLibrary 1 ssl
call :CheckLibrary 1 random

call :CheckLibrary 1 sys
call :CheckLibrary 1 io
call :CheckLibrary 1 re

call :CheckLibrary 1 datetime
call :CheckLibrary 1 time
call :CheckLibrary 1 tempfile
call :CheckLibrary 1 logging

call :CheckLibrary 0 requests
call :CheckLibrary 0 python-dotenv
call :CheckLibrary 0 pg8000
call :CheckLibrary 0 matplotlib
call :CheckLibrary 0 yfinance

REM db_pool is a LOCAL project module (db_pool.py at repo root), NOT a PyPI package -- flag 1 to skip so it is never pip-installed
call :CheckLibrary 1 db_pool

call :CheckLibrary 0 numpy
call :CheckLibrary 0 pandas

call :CheckLibrary 0 tweepy

timeout /t 30

REM ======================================================================================================
REM Start Web APP
REM ======================================================================================================

echo Starting the Squeeze web server on http://127.0.0.1:5057 ...
start "Squeeze Server" cmd /k python -m hvf_web.server

REM give the server a few seconds to bind port 5057 before ngrok connects
timeout /t 5 >nul

cd C:\Users\eahin\OneDrive\ngrok
echo Starting ngrok public share on port 5057 ...
start "ngrok share" cmd /k nGrok-Start-SqueezeScanner.bat
REM ngrok http 5057 --pooling-enabled

echo.
echo ============================================================
echo  Both started. Keep BOTH windows open.
echo  Local:  http://127.0.0.1:5057
echo  Public: see the "Forwarding" line in the ngrok window.
echo ============================================================

pause
goto :eof

REM ======================================================================================================
REM Function: CheckLibrary
REM ======================================================================================================
:CheckLibrary
set "TSYS=%~1"
set "TLIB=%~2"

echo.
echo ==========================================================================================
echo [%DATE% %TIME:~0,8%] Checking Python library: %TLIB%
echo ==========================================================================================

if "%TSYS%"=="1" ( echo Status : Skip this system library & goto :eof )

python -m pip show "%TLIB%" >nul 2>&1
if errorlevel 1 (
    echo Status : NOT INSTALLED
    echo Action : Installing...
    python -m pip install "%TLIB%"
    if errorlevel 1 (
        echo Result : FAILED
    ) else (
        echo Result : SUCCESS
    )
) else (
    echo Status : Already installed
)

REM echo.
goto :eof