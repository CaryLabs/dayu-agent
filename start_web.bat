@echo off
setlocal
REM ============================================================
REM  Start dayu-agent Web UI (Streamlit) on http://localhost:8521
REM  Double-click to run, or run in cmd / Git Bash.
REM ============================================================
cd /d "%~dp0"

set "PY=C:\Users\carys\AppData\Local\Temp\dayu-min-venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] Python environment not found:
    echo   %PY%
    echo.
    echo Please rebuild a full env first, for example:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -e ".[web]"
    echo Then update the PY variable in this script to that python.exe.
    exit /b 1
)

REM Clear WorkBuddy-injected vars so the safe-delete shim does not
REM intercept filing writes (this caused "download failed" before).
set "CODEBUDDY_SESSION_ID="
set "CLAUDE_SESSION_ID="
set "CODEBUDDY_SAFE_DELETE_ENABLED="
set "CODEBUDDY_SAFE_DELETE_SANDBOX="
set "CODEBUDDY_FILE_BACKUP_ENABLED="
set "DEEPSEEK_API_KEY="

echo Starting dayu-agent Web UI at http://localhost:8521 ...
echo Press Ctrl+C, or run stop_web.bat in another window, to stop.
"%PY%" -m streamlit run dayu\web\streamlit_app.py --server.port 8521 --server.headless true
