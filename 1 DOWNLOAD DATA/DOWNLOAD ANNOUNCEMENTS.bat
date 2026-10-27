@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Asks NSE for the last week of company announcements, board
REM  meetings and the results calendar, and writes them to
REM  DATA\announcements.json
REM
REM  Nothing you already have is touched. No Zerodha login needed.
REM  It takes about ten seconds.
REM
REM  Read what the window prints before closing it - if NSE refused,
REM  the message matters.
REM ---------------------------------------------------------------
title Download company announcements
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "DOWNLOAD ANNOUNCEMENTS.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "DOWNLOAD ANNOUNCEMENTS.py"
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
