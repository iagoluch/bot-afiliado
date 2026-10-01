@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente ausente. Execute scripts\install.bat primeiro.
  exit /b 1
)
if not defined DRY_RUN set "DRY_RUN=true"
if not defined DATABASE_PATH set "DATABASE_PATH=data\affiliate.db"
if not defined WEB_HOST set "WEB_HOST=127.0.0.1"
call ".venv\Scripts\python.exe" -m app.cli init-db
if errorlevel 1 exit /b 1
call ".venv\Scripts\python.exe" -m uvicorn app.web:app --host "%WEB_HOST%" --port 8000
