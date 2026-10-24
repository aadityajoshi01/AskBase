@echo off
setlocal
cd /d "%~dp0"
set "APP_URL=http://127.0.0.1:4173"
set "PID_FILE=.askbase-ui.pid"

where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js 20 or newer is required and must be available in PATH.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri '%APP_URL%' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
  echo AskBase is already running at %APP_URL%.
  start "" "%APP_URL%"
  exit /b 0
)

if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>&1

echo Starting AskBase at %APP_URL% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$serverPath = Join-Path $PWD.Path 'server.mjs'; $process = Start-Process -FilePath 'node.exe' -ArgumentList ('\"' + $serverPath + '\"') -WorkingDirectory $PWD.Path -WindowStyle Hidden -RedirectStandardOutput '.askbase-ui.log' -RedirectStandardError '.askbase-ui.error.log' -PassThru; Set-Content -Path '%PID_FILE%' -Value $process.Id -NoNewline; for ($attempt = 0; $attempt -lt 40; $attempt++) { try { $response = Invoke-WebRequest -UseBasicParsing -Uri '%APP_URL%' -TimeoutSec 1; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; if ($process.HasExited) { Get-Content '.askbase-ui.error.log' -ErrorAction SilentlyContinue; exit 1 }; Start-Sleep -Milliseconds 250 }; Write-Error 'AskBase did not start within 10 seconds. Check .askbase-ui.error.log.'; exit 1"
if errorlevel 1 (
  echo [ERROR] AskBase could not be started.
  pause
  exit /b 1
)

echo AskBase started. Run stop.bat to stop the website.
start "" "%APP_URL%"
exit /b 0
