@echo off
rem AskBase Qwen3 llamafile server. Keep this window open while using AskBase.
rem Requires OfflineAI folder (model + llamafile) at the path below; edit if moved.

set OFFLINEAI_DIR=C:\Users\Aaditya Joshi\Desktop\OfflineAI
if not exist "%OFFLINEAI_DIR%\llamafile-0.10.5.exe" (
  echo [ERROR] llamafile not found in "%OFFLINEAI_DIR%" - edit OFFLINEAI_DIR in this script.
  pause & exit /b 1
)

cd /d "%OFFLINEAI_DIR%"
echo Starting Qwen3-8B on http://127.0.0.1:8080 (context 4096, full GPU offload)...
echo P3 latency experiment: replace the line below with the -fa/-ctk variant:
echo   llamafile-0.10.5.exe -m Qwen3-8B-Q4_K_M.gguf -ngl 99 -c 4096 -fa 1 -ctk q8 -ctv q8 --host 127.0.0.1 --port 8080

llamafile-0.10.5.exe -m Qwen3-8B-Q4_K_M.gguf -ngl 99 -c 4096 --host 127.0.0.1 --port 8080

pause
