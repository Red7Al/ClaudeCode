@echo off
REM ======================================================================================
REM  Restart the Squeeze web app + ngrok public share. NO rebuild (starts immediately).
REM  Double-click this file to run it (user 2026-06-29: restart without Claude credits).
REM
REM  - Opens TWO windows that must stay open: the web server, and the ngrok share.
REM  - Local address:  http://127.0.0.1:5057
REM  - Public address:  shown in the ngrok window (https://...ngrok-free.dev)
REM  - To ALSO rebuild the snapshot (scans ~600 instruments, ~25 min) run run.bat instead.
REM ======================================================================================

cd /d "%~dp0.."

echo Starting the Squeeze web server on http://127.0.0.1:5057 ...
start "Squeeze Server" cmd /k python -m hvf_web.server

REM give the server a few seconds to bind port 5057 before ngrok connects
timeout /t 5 >nul

echo Starting ngrok public share on port 5057 ...
start "ngrok share" cmd /k C:\ProgramData\chocolatey\bin\ngrok http 5057

echo.
echo ============================================================
echo  Both started. Keep BOTH windows open.
echo  Local:  http://127.0.0.1:5057
echo  Public: see the "Forwarding" line in the ngrok window.
echo ============================================================
pause
