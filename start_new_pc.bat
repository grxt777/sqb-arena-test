@echo off
title ATM Monitor Setup and Start
echo ===================================================
echo   ATM Monitor - Автоматическая настройка нового ПК
echo   (Портативный запуск без прав администратора)
echo ===================================================
echo.

:: 1. Проверка наличия Python в системе
where python >nul 2>&1
if not errorlevel 1 set PY_CMD=python
if not errorlevel 1 goto python_found

where py >nul 2>&1
if not errorlevel 1 set PY_CMD=py
if not errorlevel 1 goto python_found

if exist "%~dp0python_embed\python.exe" (
    set PY_CMD="%~dp0python_embed\python.exe"
    echo [+] Найдена портативная версия Python.
    goto python_found
)

echo [!] Python не найден в системе.
echo [+] Начинаю скачивание портативной версии Python (без установки)...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip', 'python_embed.zip')"

if not exist python_embed.zip (
    echo [!] Ошибка: Не удалось скачать архивы Python. Проверьте интернет-соединение.
    pause
    exit /b 1
)

echo [+] Распаковка портативного Python...
powershell -Command "Expand-Archive -Path 'python_embed.zip' -DestinationPath 'python_embed' -Force"
del python_embed.zip

:: Активация импорта внешних модулей в портативном Python (необходимо для pip)
echo. >> python_embed\python310._pth
echo import site >> python_embed\python310._pth

echo [+] Скачивание менеджера пакетов pip...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py', 'get-pip.py')"

if not exist get-pip.py (
    echo [!] Ошибка: Не удалось скачать get-pip.py.
    pause
    exit /b 1
)

echo [+] Установка pip...
python_embed\python.exe get-pip.py --no-warn-script-location
del get-pip.py

if exist "%~dp0python_embed\python.exe" (
    set PY_CMD="%~dp0python_embed\python.exe"
    echo [+] Портативный Python успешно настроен!
    goto python_found
)

echo [!] Ошибка: Не удалось настроить портативный Python.
pause
exit /b 1

:python_found
echo [+] Использование Python: %PY_CMD%

:: 2. Останавливаем процессы на порту 8000
echo.
echo [+] Освобождаем порт 8000...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
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
