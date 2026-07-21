@echo off
title ATM Monitor Setup and Start
echo ===================================================
echo   ATM Monitor - Автоматическая настройка нового ПК
echo ===================================================
echo.

:: 1. Проверка наличия Python в системе
where python >nul 2>&1
if not errorlevel 1 set PY_CMD=python
if not errorlevel 1 goto python_found

where py >nul 2>&1
if not errorlevel 1 set PY_CMD=py
if not errorlevel 1 goto python_found

if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set PY_CMD="%LocalAppData%\Programs\Python\Python310\python.exe"
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" goto python_found

echo [!] Python не найден в системе.
echo Скачиваю установщик Python 3.10...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe' -OutFile 'python_installer.exe'"

if not exist python_installer.exe echo [!] Ошибка при скачивании установщика Python.
if not exist python_installer.exe pause
if not exist python_installer.exe exit /b 1

echo.
echo [+] Скачивание завершено.
echo [+] Установка Python локально (БЕЗ ПРАВ АДМИНИСТРАТОРА)...
echo [!] Пожалуйста, подождите, идет установка (появится окно с прогресс-баром)...

:: Запуск без UAC: InstallAllUsers=0, Include_launcher=0
start /wait python_installer.exe /passive InstallAllUsers=0 Include_launcher=0 PrependPath=1
del python_installer.exe

:: Проверяем локальный путь после установки
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set PY_CMD="%LocalAppData%\Programs\Python\Python310\python.exe"
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" goto python_found

echo [!] Ошибка: Не удалось установить Python или найти его исполняемый файл.
echo Попробуйте установить Python вручную с официального сайта.
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

:: 3. Установка pip и зависимостей (используем --user для гарантии отсутствия запроса прав)
echo.
echo [+] Проверка и обновление pip...
%PY_CMD% -m pip install --user --upgrade pip

echo.
echo [+] Установка библиотек из requirements.txt...
%PY_CMD% -m pip install --user -r requirements.txt

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
