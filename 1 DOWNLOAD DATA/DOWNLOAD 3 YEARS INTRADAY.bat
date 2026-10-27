@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Downloads THREE YEARS of 15-minute candles instead of one.
REM  It writes to a NEW file - intraday_bars_3y.json - so the data
REM  you already have is untouched and nothing can be lost.
REM
REM  It takes roughly 30-45 minutes. You can stop it and run it
REM  again later: it keeps the stocks it has already finished and
REM  carries on from where it stopped.
REM ---------------------------------------------------------------
title Download 3 years of intraday data
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "DOWNLOAD 3 YEARS INTRADAY.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "DOWNLOAD 3 YEARS INTRADAY.py"
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
