@echo off
setlocal
cd /d "%~dp0"
set "PID_FILE=.askbase-ui.pid"

if not exist "%PID_FILE%" (
  echo No AskBase server started by start.bat was found.
  pause
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$serverProcessId = [int](Get-Content '%PID_FILE%' -Raw); $process = Get-CimInstance Win32_Process -Filter \"ProcessId = $serverProcessId\"; if ($null -eq $process) { Remove-Item '%PID_FILE%' -Force; Write-Output 'AskBase is not running. Removed the stale process file.'; exit 0 }; $serverPath = Join-Path $PWD.Path 'server.mjs'; if ($process.Name -ne 'node.exe' -or $process.CommandLine -notlike ('*' + $serverPath + '*')) { Write-Error 'The saved process no longer belongs to AskBase. Nothing was stopped.'; exit 1 }; Stop-Process -Id $serverProcessId -Force -ErrorAction Stop; Remove-Item '%PID_FILE%' -Force; Write-Output 'AskBase stopped.'"
if errorlevel 1 (
  echo [ERROR] AskBase could not be stopped safely.
  pause
  exit /b 1
)

pause
exit /b 0
