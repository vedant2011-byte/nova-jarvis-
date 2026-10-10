# JARVIS setup (PowerShell).
# Installs Python dependencies, builds the frontend, prepares config.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  JARVIS setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python not found. Install Python 3.10+ from python.org" -ForegroundColor Red
    exit 1
}

Write-Host "[1/4] Installing Python dependencies..."
python -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] pip install failed." -ForegroundColor Red; exit 1 }

Write-Host "[2/4] Building the frontend (requires Node.js)..."
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "[WARN] npm not found. Install Node.js 18+ from nodejs.org and build later:"
    Write-Host "       cd frontend; npm install; npm run build"
} else {
    Push-Location frontend
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] Frontend build failed." -ForegroundColor Red; exit 1 }
}

Write-Host "[3/4] Preparing configuration..."
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "      created .env from .env.example"
} else {
    Write-Host "      .env already exists - leaving it untouched"
}

Write-Host "[4/4] Optional extras (install anytime):"
Write-Host "   - Local speech recognition : python -m pip install faster-whisper"
Write-Host "   - Local AI reasoning       : install Ollama, then: ollama pull llama3.2:3b"
Write-Host "   - Browser automation       : python -m pip install playwright; python -m playwright install chromium"

Write-Host ""
Write-Host "Setup complete. Start JARVIS with: scripts\start_jarvis.bat" -ForegroundColor Green
