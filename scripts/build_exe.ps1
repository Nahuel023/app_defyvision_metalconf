# scripts/build_exe.ps1
# Ejecutar: powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1

$ROOT = Split-Path $PSScriptRoot -Parent
Set-Location $ROOT
$ErrorActionPreference = "Stop"

Write-Host "=== MetalConf - Build EXE ===" -ForegroundColor Cyan
Write-Host "Directorio: $ROOT"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "ERROR: no se encontro .venv." -ForegroundColor Red
    exit 1
}

Write-Host "`nVerificando dependencias..." -ForegroundColor Yellow
.\.venv\Scripts\pip.exe install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    throw "No se pudieron instalar/verificar las dependencias de produccion."
}
.\.venv\Scripts\pip.exe install pyinstaller pyinstaller-hooks-contrib cython --quiet
if ($LASTEXITCODE -ne 0) {
    throw "No se pudieron instalar/verificar las dependencias de build."
}

if (Test-Path "dist\metalconf") { Remove-Item -Recurse -Force "dist\metalconf" }
if (Test-Path "build")          { Remove-Item -Recurse -Force "build" }

# Paso 1: Cython
Write-Host "`nCompilando modulos protegidos con Cython..." -ForegroundColor Yellow
Get-ChildItem "src\utils" -Filter "license*.pyd" -ErrorAction SilentlyContinue | Remove-Item -Force
cmd /c "scripts\cython_build.bat"
if ($LASTEXITCODE -ne 0) {
    throw "Cython/MSVC fallo. Build cancelado: no se permite generar una entrega sin proteccion nativa."
}
$pyd = Get-ChildItem "build" -Recurse -Filter "license*.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pyd) {
    Copy-Item $pyd.FullName "src\utils\$($pyd.Name)" -Force
    Write-Host "  license.pyd OK: $($pyd.Name)" -ForegroundColor Green
} else {
    throw "Cython no genero license*.pyd. Build cancelado."
}
Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue

# Paso 2: Ocultar .py protegidos
$hidden = @()
$pydExists = Get-ChildItem "src\utils" -Filter "license*.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pydExists) {
    Move-Item "src\utils\license.py" "src\utils\license.py.bak" -Force
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
        if (Test-Path "$f.bak") { Move-Item "$f.bak" $f -Force }
    }
    Get-ChildItem "src\utils" -Filter "license*.pyd" -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Write-Host "Archivos originales restaurados." -ForegroundColor Gray
}

if (-not $buildOk) {
    Write-Host "`nERROR en la compilacion." -ForegroundColor Red
    exit 1
}

# Verificacion obligatoria: el entregable debe contener la extension Cython y
# nunca el fuente Python del modulo de licencia.
$cythonInDist = Get-ChildItem "$ROOT\dist\metalconf" -Recurse -Filter "license*.pyd" -ErrorAction SilentlyContinue
if (-not $cythonInDist) {
    throw "Build invalido: no se encontro license*.pyd dentro de dist\metalconf."
}
$licenseSourceInDist = Get-ChildItem "$ROOT\dist\metalconf" -Recurse -Filter "license.py" -ErrorAction SilentlyContinue
if ($licenseSourceInDist) {
    throw "Build invalido: se encontro license.py sin compilar dentro del entregable."
}
Write-Host "  Cython verificado dentro del entregable: $($cythonInDist[0].FullName)" -ForegroundColor Green

$hungarianInDist = Get-ChildItem "$ROOT\dist\metalconf" -Recurse -Filter "_lsap*.pyd" -ErrorAction SilentlyContinue
if (-not $hungarianInDist) {
    throw "Build invalido: falta scipy.optimize._lsap; Hungarian no funcionaria en produccion."
}
Write-Host "  Hungarian/SciPy verificado: $($hungarianInDist[0].FullName)" -ForegroundColor Green

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

$commit = (git rev-parse HEAD).Trim()
$buildTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
@"
DEFYVISION METALCONF
Build: $buildTime
Commit: $commit
Cython: OBLIGATORIO - src.utils.license compilado como $($cythonInDist[0].Name)
Hungarian: scipy.optimize._lsap incluido
Entrada: metalconf.exe
"@ | Set-Content -LiteralPath "$dist\BUILD_INFO.txt" -Encoding UTF8

Write-Host "`nEjecutando smoke test seguro del EXE..." -ForegroundColor Yellow
$previousSmoke = $env:DEFYVISION_BUILD_SMOKE_TEST
try {
    $env:DEFYVISION_BUILD_SMOKE_TEST = "1"
    $smoke = Start-Process -FilePath "$dist\metalconf.exe" -WorkingDirectory $dist `
        -WindowStyle Hidden -Wait -PassThru
    if ($smoke.ExitCode -ne 0) {
        throw "Smoke test del EXE fallo con codigo $($smoke.ExitCode)."
    }
    $startupLog = "$dist\startup.log"
    if (-not (Test-Path $startupLog) -or
        -not (Select-String -LiteralPath $startupLog -SimpleMatch "BUILD_SMOKE_OK" -Quiet)) {
        throw "Smoke test sin confirmacion BUILD_SMOKE_OK en startup.log."
    }
    Write-Host "  EXE congelado: imports criticos OK, sin conexion a PLC/camaras." -ForegroundColor Green
} finally {
    $env:DEFYVISION_BUILD_SMOKE_TEST = $previousSmoke
}

Write-Host "`n=== BUILD OK ===" -ForegroundColor Green
Write-Host "`nCarpeta para el cliente:" -ForegroundColor White
Write-Host "  $dist" -ForegroundColor Cyan
Write-Host "`nContenido:"
Get-ChildItem $dist | ForEach-Object { Write-Host "  $($_.Name)" }
