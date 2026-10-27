@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS.
REM
REM  Cuts the 1.3 GB three-year data file into pieces small enough
REM  to send to Claude. Your original file is not touched.
REM  Takes a few minutes.
REM ---------------------------------------------------------------
title Split the 3-year file for Claude
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "SPLIT 3Y FOR CLAUDE.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "SPLIT 3Y FOR CLAUDE.py"
  ) else (
    echo.
    echo   PYTHON IS NOT ON THIS COMPUTER'S PATH.
    echo.
  )
)

echo.
echo  ----------------------------------------------------------------
echo   Finished. Read anything above this line before closing.
echo  ----------------------------------------------------------------
pause
