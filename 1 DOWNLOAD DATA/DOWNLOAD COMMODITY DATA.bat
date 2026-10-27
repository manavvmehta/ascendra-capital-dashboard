@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Downloads daily candles for the MCX commodities - gold, silver,
REM  crude, gas, copper, zinc, aluminium, lead, nickel - eight years
REM  back, into a NEW file: DATA\mcx_daily.json
REM
REM  Nothing you already have is touched.
REM
REM  Run connect.py in "5 LIVE FEED" first, so today's login is fresh.
REM  It takes about a minute.
REM ---------------------------------------------------------------
title Download MCX commodity data
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "DOWNLOAD COMMODITY DATA.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "DOWNLOAD COMMODITY DATA.py"
  ) else (
    echo.
    echo   PYTHON IS NOT ON THIS COMPUTER'S PATH.
    echo   That is why the window flashed and closed.
    echo.
    echo   Tell Claude you saw this message.
    echo.
  )
)

echo.
echo  ----------------------------------------------------------------
echo   Finished. Read anything above this line before closing.
echo  ----------------------------------------------------------------
pause
