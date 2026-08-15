@echo off
REM HVF Scanner website (user 2026-06-27). Run from the EndToEndTrading repo root.
cd /d "%~dp0.."

REM Keep Python's __pycache__ out of the OneDrive-synced repo (user 2026-07-10).
set PYTHONPYCACHEPREFIX=%TEMP%\hvf_pycache

if "%~1"=="" ( 
    echo "Building HVF snapshot (scans the universe; a few minutes)..."
    python -m hvf_web.build_snapshot
)

REM Local development instance only. The live site is https://www.squeezescanner.cloud/ (IONOS) — the
REM laptop + ngrok public share was retired 2026-08-15 (user: "ngrok is not required").
echo Starting HVF site on http://127.0.0.1:5057  (local development only)
python -m hvf_web.server
