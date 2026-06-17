# scripts/build_exe.ps1 - compila metalconf.exe con Cython + PyInstaller
# Ejecutar desde el directorio raiz del proyecto:
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

Write-Host "=== MetalConf - Build EXE ===" -ForegroundColor Cyan
Write-Host "Directorio: $ROOT"

# Verificar venv
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "ERROR: no se encontro .venv. Ejecutar setup_windows.ps1 primero." -ForegroundColor Red
    exit 1
}

# Dependencias de build
Write-Host "`nVerificando dependencias de build..." -ForegroundColor Yellow
.\.venv\Scripts\pip.exe install pyinstaller pyinstaller-hooks-contrib cython --quiet

# Limpiar builds anteriores
if (Test-Path "dist\metalconf") { Remove-Item -Recurse -Force "dist\metalconf" }
if (Test-Path "build")          { Remove-Item -Recurse -Force "build" }

# ── Paso 1: Compilar módulos críticos con Cython ──────────────────────────
Write-Host "`nCompilando módulos protegidos con Cython..." -ForegroundColor Yellow

$vcvarsall = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if (-not (Test-Path $vcvarsall)) {
    Write-Host "AVISO: No se encontro MSVC — saltando Cython (el exe no tendra proteccion nativa)." -ForegroundColor Yellow
} else {
    $cythonOk = $false
    cmd /c "`"$vcvarsall`" x64 && set DISTUTILS_USE_SDK=1 && set MSSdk=1 && .\.venv\Scripts\python.exe scripts\cython_setup.py build_ext 2>&1"
    if ($LASTEXITCODE -eq 0) { $cythonOk = $true }

    # Copiar .pyd a src/utils/
    $pyd = Get-ChildItem "build" -Recurse -Filter "license*.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyd) {
        Copy-Item $pyd.FullName "src\utils\$($pyd.Name)" -Force
        Write-Host "  license.pyd compilado OK: $($pyd.Name)" -ForegroundColor Green
        $cythonOk = $true
    } else {
        Write-Host "  AVISO: No se pudo compilar license.pyd — continuando sin proteccion Cython." -ForegroundColor Yellow
    }
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
}

# ── Paso 2: Ocultar .py protegidos para que PyInstaller solo vea el .pyd ──
$hidden = @()
if (Test-Path "src\utils\license.cp*.pyd") {
    Rename-Item "src\utils\license.py" "src\utils\license.py.bak"
    $hidden += "src\utils\license.py"
    Write-Host "  license.py ocultado temporalmente." -ForegroundColor Gray
}

# ── Paso 3: Compilar con PyInstaller ─────────────────────────────────────
Write-Host "`nCompilando exe con PyInstaller..." -ForegroundColor Yellow
$buildOk = $false
try {
    .\.venv\Scripts\pyinstaller.exe --clean --noconfirm metalconf.spec
    if ($LASTEXITCODE -eq 0) { $buildOk = $true }
} finally {
    # ── Paso 4: Restaurar .py originales ─────────────────────────────────
    foreach ($f in $hidden) {
        if (Test-Path "$f.bak") { Rename-Item "$f.bak" $f }
    }
    # Limpiar .pyd temporales del directorio src/
    Get-ChildItem "src\utils" -Filter "license*.pyd" -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Write-Host "Archivos originales restaurados." -ForegroundColor Gray
}

if (-not $buildOk) {
    Write-Host "`nERROR en la compilacion." -ForegroundColor Red
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
