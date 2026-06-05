param(
    [string]$Message = "Codex termino la tarea.",
    [ValidateSet("success", "warn", "error")]
    [string]$Level = "success"
)

$ErrorActionPreference = "Stop"

function Invoke-BeepPattern {
    param([string]$Kind)

    try {
        switch ($Kind) {
            "success" {
                [console]::Beep(880, 180)
                Start-Sleep -Milliseconds 70
                [console]::Beep(1175, 220)
            }
            "warn" {
                [console]::Beep(740, 220)
                Start-Sleep -Milliseconds 80
                [console]::Beep(740, 220)
            }
            "error" {
                [console]::Beep(440, 260)
                Start-Sleep -Milliseconds 90
                [console]::Beep(330, 320)
            }
        }
        return
    }
    catch {
        Add-Type -AssemblyName System
        switch ($Kind) {
            "success" { [System.Media.SystemSounds]::Asterisk.Play() }
            "warn"    { [System.Media.SystemSounds]::Exclamation.Play() }
            "error"   { [System.Media.SystemSounds]::Hand.Play() }
        }
    }
}

Write-Host ""
Write-Host ("=" * 56) -ForegroundColor DarkGray
Write-Host ("CODex aviso [{0}] - {1}" -f $Level.ToUpperInvariant(), $Message) -ForegroundColor Cyan
Write-Host ("=" * 56) -ForegroundColor DarkGray

Invoke-BeepPattern -Kind $Level
