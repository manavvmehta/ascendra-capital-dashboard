@echo off
title ASCENDRA - Formations Live
cd /d "%~dp0"
REM Use the same Python that runs the desk, whichever launcher this PC has.
where py >nul 2>nul && (py -3 "FORMATIONS LIVE.py" & goto :done)
python "FORMATIONS LIVE.py"
:done
if errorlevel 1 (
  echo.
  echo   It stopped with an error. Copy the lines above and send them to Claude.
  pause
)
