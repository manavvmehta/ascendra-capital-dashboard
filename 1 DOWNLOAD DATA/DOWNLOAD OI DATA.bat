@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Downloads OPEN INTEREST for every F&O stock, three years back,
REM  into a NEW file: DATA\oi_daily.json
REM
REM  Nothing you already have is touched.
REM
REM  Run connect.py in "5 LIVE FEED" FIRST, so today's login is fresh.
REM  It takes about four minutes.
REM ---------------------------------------------------------------
title Download open interest data
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "DOWNLOAD OI DATA.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "DOWNLOAD OI DATA.py"
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
