param(
    [string]$Message = "Codex termino la tarea.",
    [ValidateSet("success", "warn", "error")]
    [string]$Level = "success"
)

$ErrorActionPreference = "Stop"

function Show-DesktopNotification {
    param(
        [string]$Title,
        [string]$Text,
        [string]$Kind
    )

    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = [System.Drawing.SystemIcons]::Information
        $icon.Visible = $true

        switch ($Kind) {
            "success" { $tipIcon = [System.Windows.Forms.ToolTipIcon]::Info }
            "warn"    { $tipIcon = [System.Windows.Forms.ToolTipIcon]::Warning }
            "error"   { $tipIcon = [System.Windows.Forms.ToolTipIcon]::Error }
            default   { $tipIcon = [System.Windows.Forms.ToolTipIcon]::None }
        }

        $icon.BalloonTipTitle = $Title
        $icon.BalloonTipText = $Text
        $icon.BalloonTipIcon = $tipIcon
        $icon.ShowBalloonTip(5000)

        Start-Sleep -Milliseconds 5500
        $icon.Dispose()
        return $true
    }
    catch {
        return $false
    }
}

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
[void](Show-DesktopNotification -Title "Codex termino" -Text $Message -Kind $Level)
