Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass | Out-Null
$ErrorActionPreference = "Stop"

Write-Host "== Update app_defyvision_metalconf =="

function Refresh-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Ensure-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        return
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Warning "winget not found. ffmpeg may need manual installation."
        return
    }

    Write-Host "ffmpeg not found. Installing via winget..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Refresh-SessionPath
}

Write-Host "Pulling latest changes..."
git pull --ff-only

if (-not (Test-Path ".\.venv")) {
    Write-Host "Virtual environment missing. Creating .venv..."
    py -m venv .venv
}

Write-Host "Refreshing Python dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Ensure-Ffmpeg

Write-Host ""
Write-Host "Update complete."
Write-Host "Run operator UI with:"
Write-Host "  .\.venv\Scripts\python.exe -m src.main operator-ui"
