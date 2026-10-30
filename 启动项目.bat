@echo off
setlocal
title OSINT Multi-Agent Platform
cd /d "%~dp0"

set "RUNTIME_PY=%LOCALAPPDATA%\Doubao\User Data\sandbox_runtime\bases\c98c5042338ed152c6f10ecd8591889f\python\python.exe"

if exist "%RUNTIME_PY%" goto useruntime
where py >nul 2>nul && goto usepy
where python >nul 2>nul && goto usepython
echo [ERROR] Python not found. Run this script inside Doubao, or install Python 3.10+.
pause
exit /b 1

:useruntime
set "PYCMD="%RUNTIME_PY%""
goto check

:usepy
set "PYCMD=py -3"
goto check

:usepython
set "PYCMD=python"
goto check

:check
echo Using Python: %PYCMD%
%PYCMD% -c "import fastapi,uvicorn,mcp; from agent_framework.openai import OpenAIChatClient; from openai import AsyncAzureOpenAI" 2>nul
if not errorlevel 1 goto ready
if /i "%~1"=="--check" exit /b 1
echo Dependencies missing, installing via Aliyun mirror ...
%PYCMD% -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

if errorlevel 1 (
  echo [ERROR] Dependency install failed. Please check the output above.
  exit /b 1
)


:ready
if /i "%~1"=="--check" (
  %PYCMD% -B launch.py --check
  if errorlevel 1 exit /b 1
  exit /b 0
)
%PYCMD% -B launch.py
set "LAUNCH_EXIT=%errorlevel%"
echo.
echo Service stopped.
pause
exit /b %LAUNCH_EXIT%
