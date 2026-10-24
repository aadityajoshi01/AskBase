@echo off
rem Stops the AskBase llamafile server started via start_server.bat / OfflineAI start.bat
taskkill /IM llamafile-0.10.5.exe /F >nul 2>&1
if %errorlevel%==0 (echo Qwen3 server stopped.) else (echo No llamafile server was running.)
pause
