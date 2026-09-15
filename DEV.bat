@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title YouScraper Developer Mode

echo.
echo ============================================
echo           YouScraper Developer Mode
echo ============================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python Launcher ^(py^) was not found.
    echo Install Python 3.10+ from https://www.python.org/downloads/windows/
    echo Make sure the Python Launcher is enabled during installation.
    pause
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js was not found.
    echo Install Node.js LTS from https://nodejs.org/
    pause
    exit /b 1
)

if not exist "%~dp0build\windows\venv\Scripts\python.exe" (
    echo [1/4] Creating Python development environment...
    py -3 -m venv "%~dp0build\windows\venv"
    if errorlevel 1 (
        echo [ERROR] Could not create the Python virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Python development environment already exists.
)

set "PY=%~dp0build\windows\venv\Scripts\python.exe"

echo [2/4] Checking Python dependencies...
"%PY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Python dependency setup failed.
    pause
    exit /b 1
)

if not exist "%~dp0build\windows\deno\deno.exe" (
    echo [3/4] Downloading the development Deno runtime...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference='Stop'; $dir='%~dp0build\windows\deno'; New-Item -ItemType Directory -Force -Path $dir | Out-Null; $zip=Join-Path $dir 'deno.zip'; Invoke-WebRequest -Uri 'https://dl.deno.land/release/v2.9.4/deno-x86_64-pc-windows-msvc.zip' -OutFile $zip; $extract=Join-Path $dir 'extract'; Expand-Archive -Path $zip -DestinationPath $extract -Force; Move-Item -Force (Join-Path $extract 'deno.exe') (Join-Path $dir 'deno.exe'); Remove-Item -Recurse -Force $extract; Remove-Item -Force $zip"
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not download Deno.
        pause
        exit /b 1
    )
) else (
    echo [3/4] Deno development runtime already exists.
)

echo [4/4] Checking Electron dependencies...
pushd "%~dp0desktop"
call npm install --no-audit --no-fund
if errorlevel 1 (
    popd
    echo.
    echo [ERROR] Electron dependency setup failed.
    pause
    exit /b 1
)

set "YOUSCRAPER_DEV=1"
call npm start
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] YouScraper Developer Mode exited with code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

endlocal
