@echo off
title ATM Monitor Setup and Start
echo ===================================================
echo   ATM Monitor - Автоматическая настройка нового ПК
echo ===================================================
echo.

:: 1. Проверка наличия Python в системе (глобальный)
where python >nul 2>&1
if not errorlevel 1 set PY_CMD=python
if not errorlevel 1 goto python_found

where py >nul 2>&1
if not errorlevel 1 set PY_CMD=py
if not errorlevel 1 goto python_found

:: 2. Поиск Python в пользовательской директории LocalAppData
if exist "%LocalAppData%\Programs\Python" (
    for /r "%LocalAppData%\Programs\Python" %%i in (python.exe) do (
        if exist "%%i" (
            set PY_CMD="%%i"
            goto python_found
        )
    )
)

:: 3. Если Python не найден - скачиваем и устанавливаем
echo [!] Python не найден в системе.
echo Скачиваю установщик Python 3.10 (это может занять около минуты)...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe', 'python_installer.exe')"

if not exist python_installer.exe (
    echo.
    echo [!] Ошибка: Не удалось скачать установщик Python.
    echo Пожалуйста, скачайте и установите Python 3.10 (64-bit) вручную:
    echo https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe
    echo При установке ОБЯЗАТЕЛЬНО поставьте галочку "Add Python to PATH".
    pause
    exit /b 1
)

echo.
echo [+] Скачивание завершено.
echo [+] Установка Python (БЕЗ ПРАВ АДМИНИСТРАТОРА)...
echo [!] Пожалуйста, подождите, идет установка (появится окно с прогресс-баром)...

:: Запуск тихой установки для текущего пользователя без прав админа
start /wait "" python_installer.exe /passive InstallAllUsers=0 Include_launcher=0 PrependPath=1
del python_installer.exe

:: Повторный поиск Python в LocalAppData после установки
if exist "%LocalAppData%\Programs\Python" (
    for /r "%LocalAppData%\Programs\Python" %%i in (python.exe) do (
        if exist "%%i" (
            set PY_CMD="%%i"
            echo [+] Python успешно установлен локально!
            goto python_found
        )
    )
)

echo.
echo [!] Не удалось обнаружить Python после установки.
echo Пожалуйста, установите Python 3.10 вручную с официального сайта.
pause
exit /b 1

:python_found
echo [+] Использование Python: %PY_CMD%

:: 4. Останавливаем процессы на порту 8000
echo.
echo [+] Освобождаем порт 8000...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: 5. Установка pip и зависимостей (используем --user для гарантии отсутствия запроса прав)
echo.
echo [+] Проверка и обновление pip...
%PY_CMD% -m pip install --user --upgrade pip

echo.
echo [+] Установка библиотек из requirements.txt...
%PY_CMD% -m pip install --user -r requirements.txt

:: 6. Настройка PYTHONPATH
set PYTHONPATH=%~dp0api;%PYTHONPATH%

:: 7. Запуск
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
