@echo off
setlocal
cd /d "%~dp0"
set PORT=%~1
if "%PORT%"=="" set PORT=8010
setlocal enabledelayedexpansion

:find_port
netstat -ano -p TCP | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo [WARN] Port %PORT% is already in use. Trying next port...
  set /a PORT+=1
  goto find_port
)

echo [INFO] Starting app on http://127.0.0.1:%PORT%
python scripts\build_gis_runtime_cache.py --check-only >nul 2>nul
if errorlevel 1 (
  echo [INFO] GIS runtime artifact is missing or stale. Prebuilding cache...
  python scripts\build_gis_runtime_cache.py
  if errorlevel 1 (
    echo [ERROR] Failed to prebuild GIS runtime artifact.
    exit /b 1
  )
)
python -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --reload
