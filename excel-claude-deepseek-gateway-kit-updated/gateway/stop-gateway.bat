@echo off
for /f "tokens=2" %%a in ('netstat -ano ^| findstr ":8787" ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
echo Gateway on port 8787 stopped (if it was running).
