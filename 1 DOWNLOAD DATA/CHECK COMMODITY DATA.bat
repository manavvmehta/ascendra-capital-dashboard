@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Asks Zerodha why the commodity download came back empty.
REM  It downloads nothing and writes nothing. Twenty seconds.
REM
REM  Run connect.py in "5 LIVE FEED" first, so today's login is fresh.
REM  Then send Claude a photo of the window.
REM ---------------------------------------------------------------
title Check MCX commodity data
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "CHECK COMMODITY DATA.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "CHECK COMMODITY DATA.py"
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
