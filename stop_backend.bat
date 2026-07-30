@echo off
setlocal EnableExtensions

if "%BID_AGENT_BACKEND_PORT%"=="" set "BID_AGENT_BACKEND_PORT=7860"
set "BID_AGENT_FOUND="

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%BID_AGENT_BACKEND_PORT% .*LISTENING"') do (
  set "BID_AGENT_FOUND=1"
  echo Stopping PID %%P on port %BID_AGENT_BACKEND_PORT%...
  taskkill /PID %%P /F
)

if not defined BID_AGENT_FOUND echo No process is listening on port %BID_AGENT_BACKEND_PORT%.
