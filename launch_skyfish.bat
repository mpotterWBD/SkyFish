@echo off
cd /d "%~dp0"

REM Windows Smart App Control blocks the unsigned launcher stubs that uv writes
REM into .venv\Scripts (skyfish.exe, python.exe), so 'uv run skyfish' fails with
REM os error 4551. Instead the .venv is created with the python.org interpreter,
REM whose venv python.exe is signed by the Python Software Foundation, and
REM SkyFish is started with 'python -m skyfish' rather than the skyfish.exe stub.
REM (Same approach as oilIQ's launch_oiliq.bat.)
set "BASE_PYTHON=C:\Users\mpott\AppData\Local\Programs\Python\Python312\python.exe"
set "VENV_PYTHON=.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    where uv >nul 2>&1
    if errorlevel 1 (
        echo.
        echo Could not find 'uv' on PATH. Install it from https://docs.astral.sh/uv/
        echo then reopen this launcher.
        echo.
        pause
        exit /b 1
    )
    if not exist "%BASE_PYTHON%" (
        echo.
        echo Could not find python.org Python 3.12 at:
        echo   %BASE_PYTHON%
        echo.
        pause
        exit /b 1
    )
    REM First run - may take a minute while dependencies download.
    echo Creating SkyFish environment...
    "%BASE_PYTHON%" -m venv .venv || goto :setup_failed
    uv sync --python "%VENV_PYTHON%" || goto :setup_failed
)

echo Starting SkyFish Code Analytics...
"%VENV_PYTHON%" -m skyfish
if errorlevel 1 (
    echo.
    echo SkyFish exited with an error - see above.
    pause
)
exit /b

:setup_failed
echo.
echo Failed to set up the SkyFish environment - see above.
pause
exit /b 1
