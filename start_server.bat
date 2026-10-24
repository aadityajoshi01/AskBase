@echo off
rem AskBase Qwen3 llamafile server. Keep this window open while using AskBase.
rem Safe to re-run: exits if a server is already listening on 8080.
rem Requires OfflineAI folder (model + llamafile) at the path below; edit if moved.

set OFFLINEAI_DIR=C:\Users\Aaditya Joshi\Desktop\OfflineAI

netstat -ano | findstr /R /C:":8080 .*LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo Qwen3 server is already running on http://127.0.0.1:8080 - nothing to do.
  pause & exit /b 0
)

if not exist "%OFFLINEAI_DIR%\llamafile-0.10.5.exe" (
  echo [ERROR] llamafile not found in "%OFFLINEAI_DIR%" - edit OFFLINEAI_DIR in this script.
  pause & exit /b 1
)

cd /d "%OFFLINEAI_DIR%"
echo Starting Qwen3-8B on http://127.0.0.1:8080 (context 4096, full GPU offload)...
echo Note: empty completions are prevented in code via the Qwen3 /no_think soft
echo switch in the system prompts - no server flag needed for that.
echo Optional P3 latency experiment: add "-fa 1 -ctk q8 -ctv q8" to the line below.

llamafile-0.10.5.exe -m Qwen3-8B-Q4_K_M.gguf -ngl 99 -c 4096 --host 127.0.0.1 --port 8080

pause
