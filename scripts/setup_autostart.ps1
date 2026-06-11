# scripts/setup_autostart.ps1 - registra metalconf.exe en el Task Scheduler
# Requiere ejecutar como Administrador.
# Ejecutar DESPUES de build_exe.ps1.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_autostart.ps1

$ROOT    = Split-Path $PSScriptRoot -Parent
$EXE     = Join-Path $ROOT "dist\metalconf\metalconf.exe"
$TASK    = "MetalconfInspeccion"

Write-Host "=== MetalConf — Autoarranque ===" -ForegroundColor Cyan

if (-not (Test-Path $EXE)) {
    Write-Host "ERROR: no se encontro el ejecutable en: $EXE" -ForegroundColor Red
    Write-Host "Ejecutar build_exe.ps1 primero."
    exit 1
}

# Eliminar tarea existente si hay una
$existing = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Eliminando tarea existente '$TASK'..."
    Unregister-ScheduledTask -TaskName $TASK -Confirm:$false
}

# Accion: ejecutar el .exe con el directorio raiz como WorkingDirectory
$action = New-ScheduledTaskAction `
    -Execute $EXE `
    -WorkingDirectory $ROOT

# Trigger: al iniciar sesion del usuario actual
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Configuracion: sin limite de tiempo, reintentar 3 veces si falla
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

# Principal: usuario actual, nivel mas alto (para acceso a camara/PLC)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TASK `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host ""
Write-Host "=== TAREA REGISTRADA ===" -ForegroundColor Green
Write-Host "  Nombre:       $TASK"
Write-Host "  Ejecutable:   $EXE"
Write-Host "  Iniciar en:   $ROOT"
Write-Host "  Disparador:   Al iniciar sesion de $env:USERNAME"
Write-Host "  Reintentos:   3 (cada 1 minuto si falla)"
Write-Host ""
Write-Host "Para verificar:"
Write-Host "  Get-ScheduledTask -TaskName '$TASK' | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "Para desregistrar:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TASK' -Confirm:`$false"
