@ECHO OFF
color 0F

REM ========================================================================================
REM 	Author: 	Alex Hind
REM 	Created:	20260628
REM 	Purpose:	Run ngrok with admin check
REM		v1.00	20260628	Alex Hind		Intial copy
REM
REM ==============================================================================================

IF NOT "%COMPUTERNAME%"== "EAH-SURFACEPR04" (
	ECHO W: Configured to run on EAH-SURFACEPR04
	PAUSE
	REM EXIT /B 1
)


REM ---- Check for Administrator privileges ----
net session >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    REM Admin detected - Update ngrok
    REM C:\ProgramData\chocolatey\lib\ngrok\tools\
    ngrok update
)

C:

timeout /t 5 > nul

REM Start tunnel
REM C:\ProgramData\chocolatey\lib\ngrok\tools\
ngrok http 5057

ECHO Waiting for 10 seconds
timeout /t 10 > nul

EXIT /B 0