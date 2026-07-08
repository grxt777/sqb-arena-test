@echo off
echo ========================================
echo   ATM Monitor - Запуск сервера
echo ========================================

:: Убиваем старые процессы на порту 8000
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo Останавливаем старый процесс: %%p
    taskkill /PID %%p /F >nul 2>&1
)

timeout /t 2 /nobreak >nul

:: Запускаем сервер
echo Запуск сервера на http://localhost:8000
echo Дашборд: http://localhost:8000/dashboard/index.html
echo.
echo Для остановки нажмите CTRL+C
echo ========================================

cd /d "%~dp0api"
py main.py
