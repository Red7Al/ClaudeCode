@ECHO OFF
REM ======================================================================================
REM Local dossier launcher (TRACKED — must contain NO secrets).
REM Slack creds for local --check-slack / posting tests live in launcher_secrets.cmd, a
REM SIBLING file that is gitignored. Create it with:
REM     SET SLACK_TWITTER=...
REM     SET SLACK_BOT_TOKEN=...
REM     SET SLACK_TWITTER_CHANNEL_ID=...
REM Production posting always uses GitHub Secrets, never local files
REM (memory: secrets_and_x_delivery). NEVER hardcode a secret in this tracked file.
REM ======================================================================================
IF EXIST "%~dp0launcher_secrets.cmd" CALL "%~dp0launcher_secrets.cmd"

ECHO Testing setup first ....
REM pause

@ECHO ON
python instrument_dossier.py --check-slack

python instrument_dossier.py TLN
python instrument_dossier.py COHR
pause
