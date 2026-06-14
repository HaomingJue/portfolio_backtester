@echo off
REM ===================================================================
REM  Build PortfolioBacktester.exe with PyInstaller (Windows).
REM  Output: dist\PortfolioBacktester\  -> zip the whole folder and send it.
REM ===================================================================
setlocal

echo.
echo === Portfolio Backtester :: EXE builder ===
echo.

REM 1. Make sure the app's own dependencies are installed.
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

REM 2. Make sure PyInstaller is installed.
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto :error
)

REM 3. Build from the spec (clean rebuild).
echo.
echo Building... this can take a few minutes and produce a large folder.
python -m PyInstaller --noconfirm --clean portfolio_backtester.spec
if errorlevel 1 goto :error

echo.
echo === DONE ===
echo Your single-file app is:  dist\PortfolioBacktester.exe
echo Send your friend that ONE file. The first launch takes ~20-40s
echo (it unpacks), then a browser tab opens with the app.
echo.
goto :eof

:error
echo.
echo *** Build failed. See the messages above. ***
echo If you are on Python 3.14, PyInstaller may not support it yet --
echo build inside a Python 3.12 or 3.13 virtual environment instead
echo (see README "Package as an .exe").
echo.
exit /b 1
