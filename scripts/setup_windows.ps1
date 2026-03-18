Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass | Out-Null
$ErrorActionPreference = "Stop"

Write-Host "== Setup app_defyvision_metalconf =="

function Refresh-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Ensure-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Host "ffmpeg already available."
        return
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Warning "winget not found. Install ffmpeg manually and rerun this script if needed."
        return
    }

    Write-Host "ffmpeg not found. Installing via winget..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Refresh-SessionPath

    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Host "ffmpeg installed successfully."
    } else {
        Write-Warning "ffmpeg install finished but is still not visible in this shell. Open a new terminal and run 'ffmpeg -version'."
    }
}

if (-not (Test-Path ".\.venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    py -m venv .venv
}

Write-Host "Installing Python dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Ensure-Ffmpeg

Write-Host ""
Write-Host "Setup complete."
Write-Host "Run operator UI with:"
Write-Host "  .\.venv\Scripts\python.exe -m src.main operator-ui"
Write-Host "Run service UI with:"
Write-Host "  .\.venv\Scripts\python.exe -m src.main gui"
