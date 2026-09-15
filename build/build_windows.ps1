$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Build = Join-Path $Root 'build\windows'
if (-not (Test-Path (Join-Path $Root 'app.py'))) {
    throw "Project root detection failed. Expected app.py at $Root\app.py"
}
$Venv = Join-Path $Build 'venv'
$BackendOut = Join-Path $Build 'backend'
$DenoDir = Join-Path $Build 'deno'

New-Item -ItemType Directory -Force -Path $Build,$DenoDir | Out-Null
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    py -3 -m venv $Venv
}
$Py = Join-Path $Venv 'Scripts\python.exe'
& $Py -m pip install --disable-pip-version-check -U pip
& $Py -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
& $Py -m pip install --disable-pip-version-check pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.7

$Deno = Join-Path $DenoDir 'deno.exe'
if (-not (Test-Path $Deno)) {
    $zip = Join-Path $DenoDir 'deno.zip'
    Invoke-WebRequest -Uri 'https://dl.deno.land/release/v2.9.4/deno-x86_64-pc-windows-msvc.zip' -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath (Join-Path $DenoDir 'extract') -Force
    Move-Item -Force (Join-Path $DenoDir 'extract\deno.exe') $Deno
    Remove-Item -Recurse -Force (Join-Path $DenoDir 'extract')
    Remove-Item -Force $zip
}

Remove-Item -Recurse -Force (Join-Path $Build 'pyi-build') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $BackendOut -ErrorAction SilentlyContinue
& $Py -m PyInstaller (Join-Path $Root 'build\YouScraperBackend.spec') --noconfirm --clean --distpath $BackendOut --workpath (Join-Path $Build 'pyi-build')

$BackendExe = Join-Path $BackendOut 'YouScraperBackend\YouScraperBackend.exe'
if (-not (Test-Path $BackendExe)) { throw "PyInstaller completed but YouScraperBackend.exe was not produced at $BackendExe" }
if (-not (Test-Path $Deno)) { throw "Deno runtime is missing at $Deno" }

$Desktop = Join-Path $Root 'desktop'
Push-Location $Desktop
try {
    npm install --no-audit --no-fund
    npm run dist
} finally {
    Pop-Location
}
