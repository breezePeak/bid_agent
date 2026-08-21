@echo off
setlocal EnableExtensions

set "BID_AGENT_BACKEND_PORT=%~1"
if "%BID_AGENT_BACKEND_PORT%"=="" set "BID_AGENT_BACKEND_PORT=7860"

cd /d "%~dp0.."
if "%BID_AGENT_PYTHON%"=="" if exist ".runtime\backend_python.txt" set /p BID_AGENT_PYTHON=<".runtime\backend_python.txt"
if "%BID_AGENT_PYTHON%"=="" (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined BID_AGENT_PYTHON (
      "%%P" -c "import uvicorn" >nul 2>&1 && set "BID_AGENT_PYTHON=%%P"
    )
  )
)
if "%BID_AGENT_PYTHON%"=="" (
  echo No Python runtime with Uvicorn was found.
  echo Set BID_AGENT_PYTHON to the backend Python executable, then restart.
  exit /b 1
)
echo BidAgent Python runtime: %BID_AGENT_PYTHON%
"%BID_AGENT_PYTHON%" -m uvicorn api.v3_app:app --app-dir src --host 127.0.0.1 --port %BID_AGENT_BACKEND_PORT%
