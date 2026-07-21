@echo off
title ATM Monitor Setup ^& Start
echo ===================================================
echo   ATM Monitor - Автоматическая настройка нового ПК
echo ===================================================
echo.

:: 1. Проверка наличия Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    py --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [!] Python не найден в системе.
        echo Скачиваю установщик Python 3.10...
        powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe' -OutFile 'python_installer.exe'"
        
        echo.
        echo [!] Скачивание завершено. Запускаю установку.
        echo [ВАЖНО] В появившемся окне ОБЯЗАТЕЛЬНО поставьте галочку:
        echo        "Add Python 3.10 to PATH" (Добавить Python в PATH)
        echo.
        echo Ожидаю завершения установки...
        start /wait python_installer.exe
        del python_installer.exe
        echo.
        echo [+] Установка Python завершена!
        echo [!] Пожалуйста, закройте это окно консоли и запустите start_new_pc.bat снова,
        echo     чтобы обновились переменные окружения PATH.
        pause
        exit
    ) else (
        set PY_CMD=py
    )
) else (
    set PY_CMD=python
)

:: 2. Останавливаем процессы на порту 8000
echo.
echo [+] Проверяем порт 8000...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo [!] Обнаружен старый процесс на порту 8000 (PID: %%p). Завершаю...
    taskkill /PID %%p /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: 3. Установка pip и зависимостей
echo.
echo [+] Проверка и обновление pip...
%PY_CMD% -m pip install --upgrade pip

echo.
echo [+] Установка библиотек из requirements.txt...
%PY_CMD% -m pip install -r requirements.txt

:: 4. Настройка PYTHONPATH
set PYTHONPATH=%~dp0api;%PYTHONPATH%

:: 5. Запуск
echo.
echo ===================================================
echo   Сервер готов к запуску!
echo   Dashboard: http://localhost:8000/dashboard/index.html
echo   Swagger:  http://localhost:8000/docs
echo ===================================================
echo.

cd /d "%~dp0"
%PY_CMD% -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
pause
