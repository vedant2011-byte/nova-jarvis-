@echo off
REM JARVIS setup script — safe, readable, idempotent.
REM Installs Python dependencies, builds the frontend, prepares config.

setlocal
cd /d "%~dp0.."
echo ============================================
echo   JARVIS setup
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from python.org
    echo         and check "Add python.exe to PATH".
    pause & exit /b 1
)

echo [1/4] Installing Python dependencies...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. See messages above.
    pause & exit /b 1
)

echo [2/4] Building the frontend (requires Node.js)...
where npm >nul 2>nul
if errorlevel 1 (
    echo [WARN] npm not found. The HUD needs Node.js 18+ from nodejs.org.
    echo        Skipping frontend build - build it later with:
    echo        cd frontend ^&^& npm install ^&^& npm run build
) else (
    pushd frontend
    call npm install --no-audit --no-fund
    call npm run build
    popd
    if errorlevel 1 (
        echo [ERROR] Frontend build failed.
        pause & exit /b 1
    )
)

echo [3/4] Preparing configuration...
if not exist .env (
    copy .env.example .env >nul
    echo   created .env from .env.example
) else (
    echo   .env already exists - leaving it untouched
)

echo [4/4] Optional extras (can be installed later):
echo   - Local speech recognition : python -m pip install faster-whisper
echo   - Local AI reasoning       : install Ollama, then: ollama pull llama3.2:3b
echo   - Browser automation       : python -m pip install playwright ^&^& python -m playwright install chromium
echo.
echo Setup complete. Start JARVIS with: scripts\start_jarvis.bat
pause
