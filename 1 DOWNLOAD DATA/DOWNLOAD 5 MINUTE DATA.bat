@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Downloads FIVE-minute candles for the F&O stocks, one year back.
REM  It writes to a NEW folder - DATA\5m_parts\ - so nothing you
REM  already have is touched and nothing can be lost.
REM
REM  Run connect.py in "5 LIVE FEED" first, so today's login is fresh.
REM
REM  It takes about six minutes. You can stop it and run it again
REM  later: it keeps the stocks it has already finished and carries
REM  on from where it stopped.
REM ---------------------------------------------------------------
title Download 5 minute data
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "DOWNLOAD 5 MINUTE DATA.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "DOWNLOAD 5 MINUTE DATA.py"
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
