@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Watches sixteen MCX commodities on the daily chart and writes a
REM  call when the rule that passed testing fires:
REM
REM    the day closes above its 20-day high, TRIX positive, the
REM    WEEKLY Choppiness under 50, and a strong breakout candle
REM    -> buy the next open, stop at the 20-day low, target twice
REM       the risk, give up after forty days, long only
REM
REM  It places NO orders. It keeps its own record in DATA\cmd_log.json
REM  and fills the Commodities tab on your desk.
REM
REM  Run connect.py in "5 LIVE FEED" FIRST, then leave this open.
REM  Daily candles only, so it asks Zerodha once an hour.
REM ---------------------------------------------------------------
title Commodities - live
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "COMMODITIES LIVE.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "COMMODITIES LIVE.py"
  ) else (
    echo.
    echo   PYTHON IS NOT ON THIS COMPUTER'S PATH.
    echo   Tell Claude you saw this message.
    echo.
  )
)

echo.
echo  ----------------------------------------------------------------
echo   Finished. Read anything above this line before closing.
echo  ----------------------------------------------------------------
pause
