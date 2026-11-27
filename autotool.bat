@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [AutoTool] Virtual environment not found. Please run: uv sync --python 3.10 --extra dev
    exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
"%VENV_PY%" -m autotool %*
exit /b %ERRORLEVEL%
