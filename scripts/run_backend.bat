@echo off
setlocal EnableExtensions

set "BID_AGENT_BACKEND_PORT=%~1"
if "%BID_AGENT_BACKEND_PORT%"=="" set "BID_AGENT_BACKEND_PORT=7860"

cd /d "%~dp0.."
python -m uvicorn api.v3_app:app --app-dir src --host 127.0.0.1 --port %BID_AGENT_BACKEND_PORT%
