@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%src"
set "PYTHON=py -3"

if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if "%~1"=="" goto usage
%PYTHON% -m yjparse %*
exit /b %ERRORLEVEL%

:usage
echo Usage:
echo   run.cmd doctor
echo   run.cmd parse --input data\raw --out data\out [--primary pymupdf] [--reference auto]
echo   run.cmd verify --out data\out
echo   run.cmd report --out data\out
exit /b 1
