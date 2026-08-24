@echo off
chcp 65001 >nul
title Запуск SchoolTest GUI

:: Перехід у директорію скрипта
cd /d "%~dp0"

:: Перевірка наявності віртуального середовища. Якщо відсутнє — запускаємо автоналаштування
if not exist ".venv\Scripts\activate.bat" if not exist "venv\Scripts\activate.bat" (
    echo [WARNING] Віртуальне середовище не знайдено!
    echo [ACTION] Запуск автоматичного налаштування середовища...
    call setup_env.bat
)

:: Активація віртуального середовища
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] Не вдалося знайти або створити віртуальне середовище!
    pause
    exit /b 1
)

:: Запуск GUI інтерфейсу (pythonw приховує вікно консолі Python)
echo [ACTION] Запуск GUI...
start "" pythonw run_gui.py
exit
