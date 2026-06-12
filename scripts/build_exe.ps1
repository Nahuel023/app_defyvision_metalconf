# scripts/build_exe.ps1 - compila metalconf.exe con PyInstaller
# Ejecutar desde el directorio raiz del proyecto:
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

Write-Host "=== MetalConf - Build EXE ===" -ForegroundColor Cyan
Write-Host "Directorio: $ROOT"

# Verificar que existe el venv
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "ERROR: no se encontro .venv. Ejecutar setup_windows.ps1 primero." -ForegroundColor Red
    exit 1
}

# Instalar PyInstaller si no esta
Write-Host "`nInstalando / verificando PyInstaller..." -ForegroundColor Yellow
.\.venv\Scripts\pip.exe install pyinstaller pyinstaller-hooks-contrib --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR al instalar PyInstaller." -ForegroundColor Red
    exit 1
}

# Limpiar build anterior
if (Test-Path "dist\metalconf") {
    Write-Host "Limpiando dist\metalconf anterior..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "dist\metalconf"
}
if (Test-Path "build") {
    Remove-Item -Recurse -Force "build"
}

# Compilar
Write-Host "`nCompilando..." -ForegroundColor Yellow
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm metalconf.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR en la compilacion." -ForegroundColor Red
    exit 1
}

Write-Host "`n=== BUILD OK ===" -ForegroundColor Green
Write-Host "Ejecutable: $ROOT\dist\metalconf\metalconf.exe"
Write-Host ""
Write-Host "IMPORTANTE: el .exe necesita leer config/ y data/ del directorio raiz."
Write-Host "El Task Scheduler debe iniciar con 'Iniciar en': $ROOT"
Write-Host ""
Write-Host "Para registrar el autoarranque, ejecutar:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\setup_autostart.ps1"
