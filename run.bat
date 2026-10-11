@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM  browser-hotwords-collector - launcher (Windows)
REM
REM  Double-click        = show menu
REM  run.bat 1           = collect one round
REM  run.bat 2           = persistence ranking (long term)
REM  run.bat 3           = emergence ranking   (breaking now)
REM  run.bat 4           = cross-source ranking
REM  run.bat 5           = build + open dashboard
REM  run.bat 6           = overview + alerts
REM  run.bat 7           = scheduler (keep running)
REM  run.bat test        = run tests
REM
REM  Python interpreter (priority order):
REM    1) %HOTWORDS_PY% if already set
REM    2) .venv\Scripts\python.exe   (project virtualenv -- recommended)
REM    3) venv\Scripts\python.exe
REM    4) `python` on PATH           (fallback)
REM
REM  If you see "No module named 'yaml'", the wrong interpreter was picked:
REM  create a .venv in this folder, or set HOTWORDS_PY to the right python.exe.
REM ============================================================
setlocal
if not defined HOTWORDS_PY if exist "%~dp0.venv\Scripts\python.exe" set "HOTWORDS_PY=%~dp0.venv\Scripts\python.exe"
if not defined HOTWORDS_PY if exist "%~dp0venv\Scripts\python.exe" set "HOTWORDS_PY=%~dp0venv\Scripts\python.exe"
if not defined HOTWORDS_PY set HOTWORDS_PY=python
set PY=%HOTWORDS_PY%
cd /d %~dp0

if "%~1"==""       goto menu
if "%~1"=="1"      goto once
if "%~1"=="2"      goto track
if "%~1"=="3"      goto emergence
if "%~1"=="4"      goto cross
if "%~1"=="5"      goto trend
if "%~1"=="6"      goto report
if "%~1"=="7"      goto run
if "%~1"=="emergence" goto emergence
if "%~1"=="run"    goto run
if "%~1"=="test"   goto test
if "%~1"=="report" goto report
if "%~1"=="cross"  goto cross
if "%~1"=="track"  goto track
if "%~1"=="trend"  goto trend
if "%~1"=="retrack" goto retrack
if "%~1"=="help"   goto help
goto once

:menu
echo.
echo    ==========  HOTWORDS  ==========
echo.
echo      [1]  Collect one round        (~5s)
echo      [2]  Persistence ranking      (long term)
echo      [3]  Emergence ranking        (breaking now)
echo      [4]  Cross-source ranking     (multi-platform)
echo      [5]  Trend dashboard          (open in browser)
echo      [6]  Overview + alerts        (health check)
echo      [7]  Run scheduler            (Ctrl+C to stop)
echo      [0]  Exit
echo.
set /p CH=  Input number then Enter:
if "%CH%"=="1" goto once
if "%CH%"=="2" goto track
if "%CH%"=="3" goto emergence
if "%CH%"=="4" goto cross
if "%CH%"=="5" goto trend
if "%CH%"=="6" goto report
if "%CH%"=="7" goto run
if "%CH%"=="0" goto end
echo   Invalid choice.
pause
goto end

:once
echo.
"%PY%" -m hotwords once
goto done

:track
echo.
"%PY%" -m hotwords track --limit 20
goto done

:emergence
echo.
"%PY%" -m hotwords emergence --limit 20
goto done

:cross
echo.
"%PY%" -m hotwords cross
goto done

:trend
echo.
"%PY%" -m hotwords trend --out dashboard.html
start "" dashboard.html
goto done

:report
echo.
"%PY%" -m hotwords report
echo.
echo   ---- recent alerts ----
"%PY%" -m hotwords alerts
goto done

:run
echo.
echo   Running scheduler (full round, Ctrl+C to stop).
echo   Log: logs\scheduler.log
"%PY%" -m hotwords --log-file logs\scheduler.log run
set "RC=%ERRORLEVEL%"
echo   [scheduler exited] exit code=%RC%
REM NOTE: the space between %RC% and >> is required. Written as "%RC%>>file"
REM cmd does NOT recognize the redirect: output goes to the console, the variable
REM expands to nothing, and the file stays empty (verified empirically).
REM %DATE:~0,10% instead of %DATE%: the latter appends a localized weekday
REM (e.g. a CJK string) that lands in the file as mojibake.
echo %DATE:~0,10% %TIME%  exit code=%RC% >>"logs\last_exit.txt"
goto done

:test
echo.
"%PY%" tests\test_basic.py
goto done

:retrack
echo.
"%PY%" -m hotwords retrack
goto done

:done
echo.
echo   ==================================
echo    Done. Press any key to close.
echo   ==================================
echo    interpreter: %PY%
pause >nul
goto end

:help
echo.
echo   run.bat          collect one round
echo   run.bat track    persistence ranking
echo   run.bat emergence   emergence ranking
echo   run.bat cross    cross-source ranking
echo   run.bat trend    build and open dashboard
echo   run.bat report   overview + alerts
echo   run.bat run      scheduler (keep running)
echo   run.bat test     run tests
echo.
pause

:end
endlocal
