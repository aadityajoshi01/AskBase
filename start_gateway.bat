@echo off
rem AskBase gateway — the API that connects Agents 1-2-3 for the Agent 4 UI.
rem Start the model server first (start_server.bat), then run this.
rem Requires the repository .venv; from repo root: .venv\Scripts\python.exe -m venv .venv && .venv\Scripts\pip install -r requirements.txt

cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found in repo root - create it with: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause & exit /b 1
)

netstat -ano | findstr /R /C:":8010 .*LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo Gateway is already running on http://127.0.0.1:8010 - nothing to do.
  pause & exit /b 0
)

echo Starting AskBase gateway on http://127.0.0.1:8010 (keep this window open)...
echo UI: start ui-evaluation\start.bat and open http://127.0.0.1:4173
.venv\Scripts\python.exe -m gateway.server
pause
