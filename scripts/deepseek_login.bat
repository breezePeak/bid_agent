@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
if "%BID_AGENT_PYTHON%"=="" if exist ".runtime\backend_python.txt" set /p BID_AGENT_PYTHON=<".runtime\backend_python.txt"
if "%BID_AGENT_PYTHON%"=="" (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined BID_AGENT_PYTHON (
      "%%P" -c "import playwright" >nul 2>&1 && set "BID_AGENT_PYTHON=%%P"
    )
  )
)
if "%BID_AGENT_PYTHON%"=="" (
  echo No Python runtime with Playwright was found.
  echo Set BID_AGENT_PYTHON to the backend Python executable, then retry.
  exit /b 1
)
echo DeepSeek login Python runtime: %BID_AGENT_PYTHON%
"%BID_AGENT_PYTHON%" scripts\deepseek_login.py
