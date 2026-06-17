# scripts/build_exe.ps1
# Ejecutar: powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT

Write-Host "=== MetalConf - Build EXE ===" -ForegroundColor Cyan
Write-Host "Directorio: $ROOT"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "ERROR: no se encontro .venv." -ForegroundColor Red
    exit 1
}

Write-Host "`nVerificando dependencias..." -ForegroundColor Yellow
.\.venv\Scripts\pip.exe install pyinstaller pyinstaller-hooks-contrib cython --quiet

if (Test-Path "dist\metalconf") { Remove-Item -Recurse -Force "dist\metalconf" }
if (Test-Path "build")          { Remove-Item -Recurse -Force "build" }

# Paso 1: Cython
Write-Host "`nCompilando modulos protegidos con Cython..." -ForegroundColor Yellow
cmd /c "scripts\cython_build.bat"
$pyd = Get-ChildItem "build" -Recurse -Filter "license*.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pyd) {
    Copy-Item $pyd.FullName "src\utils\$($pyd.Name)" -Force
    Write-Host "  license.pyd OK: $($pyd.Name)" -ForegroundColor Green
} else {
    Write-Host "  AVISO: Cython fallo, continuando sin proteccion nativa." -ForegroundColor Yellow
}
Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue

# Paso 2: Ocultar .py protegidos
$hidden = @()
$pydExists = Get-ChildItem "src\utils" -Filter "license*.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pydExists) {
    Rename-Item "src\utils\license.py" "src\utils\license.py.bak"
    $hidden += "src\utils\license.py"
    Write-Host "  license.py ocultado." -ForegroundColor Gray
}

# Paso 3: PyInstaller
Write-Host "`nCompilando exe con PyInstaller..." -ForegroundColor Yellow
$buildOk = $false
try {
    .\.venv\Scripts\pyinstaller.exe --clean --noconfirm metalconf.spec
    if ($LASTEXITCODE -eq 0) { $buildOk = $true }
} finally {
    foreach ($f in $hidden) {
        if (Test-Path "$f.bak") { Rename-Item "$f.bak" $f }
    }
    Get-ChildItem "src\utils" -Filter "license*.pyd" -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Write-Host "Archivos originales restaurados." -ForegroundColor Gray
}

if (-not $buildOk) {
    Write-Host "`nERROR en la compilacion." -ForegroundColor Red
    exit 1
}

# Paso 4: Copiar carpetas junto al exe
Write-Host "`nCopiando datos a dist\metalconf\..." -ForegroundColor Yellow
$dist = "$ROOT\dist\metalconf"

if (Test-Path "$ROOT\config") {
    Copy-Item "$ROOT\config" "$dist\config" -Recurse -Force
    Write-Host "  config\" -ForegroundColor Gray
}
if (Test-Path "$ROOT\data\patterns") {
    New-Item -ItemType Directory -Force "$dist\data\patterns" | Out-Null
    Copy-Item "$ROOT\data\patterns\*" "$dist\data\patterns\" -Recurse -Force
    Write-Host "  data\patterns\" -ForegroundColor Gray
}
foreach ($folder in @("logos", "assets")) {
    if (Test-Path "$ROOT\$folder") {
        Copy-Item "$ROOT\$folder" "$dist\$folder" -Recurse -Force
        Write-Host "  $folder\" -ForegroundColor Gray
    }
}

Write-Host "`n=== BUILD OK ===" -ForegroundColor Green
Write-Host "`nCarpeta para el cliente:" -ForegroundColor White
Write-Host "  $dist" -ForegroundColor Cyan
Write-Host "`nContenido:"
Get-ChildItem $dist | ForEach-Object { Write-Host "  $($_.Name)" }
