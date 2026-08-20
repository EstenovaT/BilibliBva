@echo off
rem ============================================
rem  Bilibili Resolve - Local Launcher (Windows)
rem  Double-click to start server + open browser
rem  Logs are written to launch.log
rem ============================================
cd /d "%~dp0"
echo [launch] %date% %time% >> launch.log

rem Prefer py launcher (avoids Microsoft Store python alias issue)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0start_local.py"
    goto :done
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.7+ and add it to PATH.
    echo [ERROR] Python not found >> launch.log
    goto :done
)

python "%~dp0start_local.py"

:done
pause
