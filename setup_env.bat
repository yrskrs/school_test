@echo off
chcp 65001 >nul
title Налаштування віртуального середовища SchoolTest

:: Перехід у директорію скрипта
cd /d "%~dp0"

echo ===================================================
echo   Перевірка та налаштування середовища SchoolTest
echo ===================================================
echo.

:: Змінна прапорця, чи було створено нове середовище
set ENV_CREATED=0

:: Перевірка наявності віртуального середовища
if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Віртуальне середовище (.venv) вже існує.
) else if exist "venv\Scripts\activate.bat" (
    echo [INFO] Віртуальне середовище (venv) вже існує.
) else (
    echo [INFO] Віртуальне середовище не знайдено. Створення нового .venv...
    
    :: Визначення доступної команди python
    set PYTHON_CMD=
    where py >nul 2>nul
    if %errorlevel% equ 0 (
        set PYTHON_CMD=py -3
    ) else (
        where python >nul 2>nul
        if %errorlevel% equ 0 (
            set PYTHON_CMD=python
        )
    )

    if "%PYTHON_CMD%"=="" (
        echo [ERROR] Python не знайдено в системі! Переконайтеся, що Python встановлено та додано до PATH.
        pause
        exit /b 1
    )

    echo [ACTION] Створення віртуального середовища за допомогою %PYTHON_CMD%...
    %PYTHON_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Помилка під час створення віртуального середовища.
        pause
        exit /b 1
    )
    set ENV_CREATED=1
    echo [SUCCESS] Віртуальне середовище успішно створено.
)

:: Визначення шляху до інтерпретатора у venv
set VENV_PYTHON=.venv\Scripts\python.exe
if not exist "%VENV_PYTHON%" (
    set VENV_PYTHON=venv\Scripts\python.exe
)

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Не вдалося знайти Python у віртуальному середовищі.
    pause
    exit /b 1
)

:: Встановлення необхідних бібліотек
if exist "requirements.txt" (
    echo.
    echo [ACTION] Встановлення / оновлення бібліотек з requirements.txt...
    "%VENV_PYTHON%" -m pip install --upgrade pip
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Виникла помилка під час встановлення бібліотек.
        pause
        exit /b 1
    )
) else (
    echo [WARNING] Файл requirements.txt не знайдено. Встановлення бібліотек пропущено.
)

echo.
echo ===================================================
echo   [SUCCESS] Віртуальне середовище налаштоване!
echo ===================================================
echo.
pause
