@echo off
REM ======================================================================================================
REM  Publish the website to IONOS (https://www.squeezescanner.cloud/) from cmd.exe.
REM  Created 2026-08-15 (Claude) -- deploy_ionos.sh is a bash script; cmd cannot run it directly.
REM
REM  Usage:
REM     Deploy-IONOS.cmd --find-dir     locate the domain directory (read-only; do this first)
REM     Deploy-IONOS.cmd --dry-run      build + safety checks, no upload
REM     Deploy-IONOS.cmd                build, upload, extract, verify
REM
REM  Host / user / port come from .ionos.env (gitignored). You are prompted for the password once.
REM
REM  NOTE: this file must stay CRLF + ASCII. With LF endings cmd mis-parses every line and you get
REM  "'EM' is not recognized as an internal or external command".
REM ======================================================================================================

setlocal

REM Git Bash specifically. `bash` on PATH is C:\Windows\system32\bash.exe (WSL) -- a DIFFERENT
REM filesystem where this repo is not at the same path, so the script would fail confusingly.
set "GITBASH=C:\Program Files\Git\bin\bash.exe"
if not exist "%GITBASH%" set "GITBASH=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
if not exist "%GITBASH%" (
    echo ERROR: Git Bash not found. Looked for:
    echo        C:\Program Files\Git\bin\bash.exe
    echo        %%LOCALAPPDATA%%\Programs\Git\bin\bash.exe
    echo Install Git for Windows, or run ./deploy_ionos.sh from a Git Bash prompt.
    exit /b 1
)

if not exist "%~dp0.ionos.env" (
    echo WARNING: .ionos.env not found - IONOS_HOST/IONOS_USER/IONOS_DIR must already be set.
    echo.
)

REM Default the live-page marker so a successful deploy is verified, not assumed. The quoted-set form
REM protects the ">" from being treated as redirection.
if "%VERIFY_STRING%"=="" set "VERIFY_STRING=>125 trades"

"%GITBASH%" -lc "cd \"$(cygpath -u '%~dp0')\" && ./deploy_ionos.sh %*"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo Deploy exited with code %RC% - review the output above before trusting the release.
)
exit /b %RC%
