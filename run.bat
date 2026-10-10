@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM  browser-hotwords-collector - launcher (Windows)
REM
REM  Double-click        = show menu
REM  run.bat 1           = collect one round
REM  run.bat 2           = persistence ranking
REM  run.bat 3           = cross-source ranking
REM  run.bat 4           = build + open dashboard
REM  run.bat 5           = overview + alerts
REM  run.bat 6           = scheduler (keep running)
REM  run.bat test        = run tests
REM
REM  Python interpreter:
REM    defaults to `python` on PATH -- activate your venv first
REM    (see README "Quick start"). To pin a specific interpreter:
REM        set HOTWORDS_PY=C:\path\to\python.exe
REM ============================================================
setlocal
if not defined HOTWORDS_PY set HOTWORDS_PY=python
set PY=%HOTWORDS_PY%
cd /d %~dp0

if "%~1"==""       goto menu
if "%~1"=="1"      goto once
if "%~1"=="2"      goto track
if "%~1"=="3"      goto cross
if "%~1"=="4"      goto trend
if "%~1"=="5"      goto report
if "%~1"=="6"      goto run
if "%~1"=="run"    goto run
if "%~1"=="test"   goto test
if "%~1"=="report" goto report
if "%~1"=="cross"  goto cross
if "%~1"=="track"  goto track
if "%~1"=="trend"  goto trend
if "%~1"=="help"   goto help
goto once

:menu
echo.
echo    ==========  HOTWORDS  ==========
echo.
echo      [1]  Collect one round        (~5s)
echo      [2]  Persistence ranking      (real heat)
echo      [3]  Cross-source ranking     (multi-platform)
echo      [4]  Trend dashboard          (open in browser)
echo      [5]  Overview + alerts        (health check)
echo      [6]  Run scheduler            (Ctrl+C to stop)
echo      [0]  Exit
echo.
set /p CH=  Input number then Enter:
if "%CH%"=="1" goto once
if "%CH%"=="2" goto track
if "%CH%"=="3" goto cross
if "%CH%"=="4" goto trend
if "%CH%"=="5" goto report
if "%CH%"=="6" goto run
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
echo   Running scheduler. Press Ctrl+C to stop.
"%PY%" -m hotwords run
goto done

:test
echo.
"%PY%" tests\test_basic.py
goto done

:done
echo.
echo   ==================================
echo    Done. Press any key to close.
echo   ==================================
pause >nul
goto end

:help
echo.
echo   run.bat          collect one round
echo   run.bat track    persistence ranking
echo   run.bat cross    cross-source ranking
echo   run.bat trend    build and open dashboard
echo   run.bat report   overview + alerts
echo   run.bat run      scheduler (keep running)
echo   run.bat test     run tests
echo.
pause

:end
endlocal
