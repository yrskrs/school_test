@echo off
chcp 65001 >nul
title Запуск SchoolTest Реєстрація

:: Перехід у директорію скрипта
cd /d "%~dp0"

:: Перевірка наявності віртуального середовища. Якщо відсутнє — запускаємо автоналаштування
if not exist ".venv\Scripts\python.exe" if not exist "venv\Scripts\python.exe" (
    echo [WARNING] Віртуальне середовище не знайдено!
    echo [ACTION] Запуск автоматичного налаштування середовища...
    call setup_env.bat
)

:: Визначення шляху до pythonw у venv
set "VENV_PYTHONW=.venv\Scripts\pythonw.exe"
if not exist "%VENV_PYTHONW%" (
    set "VENV_PYTHONW=venv\Scripts\pythonw.exe"
)

if not exist "%VENV_PYTHONW%" (
    echo [ERROR] Не вдалося знайти або створити віртуальне середовище!
    pause
    exit /b 1
)

:: Запуск GUI інтерфейсу реєстрації без консольного вікна
echo [ACTION] Запуск вікна реєстрації SchoolTest...
start "" "%VENV_PYTHONW%" run_register.py
exit
