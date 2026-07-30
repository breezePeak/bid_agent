@echo off
setlocal EnableExtensions

if "%BID_AGENT_BACKEND_PORT%"=="" set "BID_AGENT_BACKEND_PORT=7860"
set "BID_AGENT_PID="

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%BID_AGENT_BACKEND_PORT% .*LISTENING"') do (
  set "BID_AGENT_PID=%%P"
  echo Stopping PID %%P on port %BID_AGENT_BACKEND_PORT%...
  taskkill /PID %%P /F >nul 2>&1
)

if defined BID_AGENT_PID timeout /t 1 /nobreak >nul

echo Starting BidAgent backend on http://127.0.0.1:%BID_AGENT_BACKEND_PORT%...
start "BidAgent Backend" /min "%ComSpec%" /c call "%~dp0scripts\run_backend.bat" "%BID_AGENT_BACKEND_PORT%"
