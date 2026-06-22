@echo off
REM ============================================================================
REM  HVF-Analysis-AH-RW.cmd  (Alex Hind, 2026-06-22)
REM
REM  Runs BOTH HVF analyses for one instrument and compares them, via Claude Code:
REM    AH = the automated EndToEndTrading engine (price_action.py)
REM    RW = the manual Francis Hunt / Market Sniper ruleset (RW-hvf-analysis.skill)
REM
REM  Usage:
REM    HVF-Analysis-AH-RW.cmd            -> analyses ABF.L (default)
REM    HVF-Analysis-AH-RW.cmd SNR.L      -> analyses any ticker you pass
REM
REM  Opens an interactive Claude session in this folder so you can read the
REM  comparison and approve the read-only tool calls (Python + reading the skill).
REM  It does NOT trade or publish anything.
REM ============================================================================
setlocal
set "TICKER=%~1"
if "%TICKER%"=="" set "TICKER=ABF.L"
cd /d "%~dp0"

echo.
echo  Running AH + RW HVF analysis for %TICKER% ...
echo.

claude "Run a two-method HVF (Hunt Volatility Funnel) analysis for %TICKER% and present a side-by-side comparison. METHOD 1 (AH, automated): use the EndToEndTrading engine - run Python: import price_action; call get_hvf_signal_mtf('%TICKER%', trend_hint=get_trend_structure('%TICKER%')); report signal, type, entry (H3), stop, target, R:R, quality, convergence, bars_since_h3, and funnel_span_weeks. METHOD 2 (RW, manual): read RW-hvf-analysis.skill and apply the Francis Hunt five-rule ruleset to %TICKER% - clear prior trend (>=20%, matching direction), three confirmed swings, tightness (H3-L3)/AMP1 <= 35%%, AMP1 = H1-L1 with target Mid +/- AMP1, R:R >= 3 - then assign its output category (Recommended / Near threshold / Developing / Watchlist / Rejected). Finish with a comparison table and a combined verdict, including whether the consolidation is prolonged. Read-only: do NOT place trades, publish to X/Slack, or modify any files."

endlocal
pause
