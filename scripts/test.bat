@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente ausente. Execute scripts\install.bat primeiro.
  exit /b 1
)
set "DRY_RUN=true"
call ".venv\Scripts\python.exe" -m pytest -q
exit /b %errorlevel%
