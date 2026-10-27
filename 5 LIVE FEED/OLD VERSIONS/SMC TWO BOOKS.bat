@echo off
REM ---------------------------------------------------------------
REM  Double-click THIS, not the .py file.
REM
REM  Records the two SMC rules that passed testing:
REM
REM    SHORT TERM  15-minute chart, yesterday's high or low swept,
REM                closed the same day
REM    LONG TERM   daily chart, last week's high or low swept,
REM                held up to ten days, traded as a stock future
REM
REM  It places NO orders. It keeps its own record in
REM  DATA\smc2_log.json and serves it on port 5057. Your existing
REM  SMC tab and every other engine are untouched.
REM
REM  Run connect.py in "5 LIVE FEED" FIRST so today's login is fresh,
REM  then leave this window open beside the desk.
REM ---------------------------------------------------------------
title SMC - the two books
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "SMC TWO BOOKS.py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python "SMC TWO BOOKS.py"
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
