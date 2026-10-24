@echo off
rem Stops the AskBase gateway (python -m gateway.server) by owning process of port 8010.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8010 .*LISTENING"') do (
  echo Stopping gateway PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
