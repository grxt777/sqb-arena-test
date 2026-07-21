@echo off
echo ========================================
echo   ATM Monitor - Запуск сервера
echo ========================================

:: Kill old process on port 8000
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo Stopping old process: %%p
    taskkill /PID %%p /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: Install deps
echo Installing dependencies...
py -m pip install -r requirements.txt

:: Set PYTHONPATH
set PYTHONPATH=%~dp0api;%PYTHONPATH%

echo.
echo Server:   http://localhost:8000
echo Dashboard: http://localhost:8000/dashboard/index.html
echo Swagger:  http://localhost:8000/docs
echo Import XLSX: POST /api/atms/import
echo ========================================
echo.

cd /d "%~dp0"
py -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
