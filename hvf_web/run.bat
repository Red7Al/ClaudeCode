@echo off
REM HVF Scanner website (user 2026-06-27). Run from the EndToEndTrading repo root.
cd /d "%~dp0.."

if "%~1"=="" ( 
    echo "Building HVF snapshot (scans the universe; a few minutes)..."
    python -m hvf_web.build_snapshot
)

echo Starting HVF site on http://127.0.0.1:5057  (run "ngrok http 5057" in another window to share)
python -m hvf_web.server
