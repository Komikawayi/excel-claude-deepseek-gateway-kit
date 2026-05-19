@echo off
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8788" ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
echo Gateway on port 8788 stopped (if it was running).
