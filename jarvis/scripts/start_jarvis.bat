@echo off
REM Starts the JARVIS backend + HUD.
REM Run from anywhere; it locates the project root itself.

setlocal
REM %~dp0 = this script's folder (jarvis\scripts). Project root is two up.
set "PROJECT_ROOT=%~dp0..\.."
cd /d "%PROJECT_ROOT%"

set "PORT=8765"
if defined JARVIS_PORT set "PORT=%JARVIS_PORT%"

echo Starting JARVIS on http://127.0.0.1:%PORT% ...
start "" http://127.0.0.1:%PORT%
python -m uvicorn jarvis.backend.server:app --host 127.0.0.1 --port %PORT%

endlocal
