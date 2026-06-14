@echo off
REM ===================================================================
REM  Easiest way to launch the app on a machine that has Python.
REM  Installs dependencies (first run only) and opens the dashboard.
REM ===================================================================
setlocal

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed. Get it from https://www.python.org/downloads/
    echo During install, tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Installing/updating dependencies (first run can take a minute)...
python -m pip install -r requirements.txt

echo.
echo Starting Portfolio Backtester... a browser tab will open.
echo Close this window to stop the app.
python -m streamlit run app.py

pause
