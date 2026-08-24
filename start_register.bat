@echo off
chcp 65001 >nul
title SchoolTest Register Launcher

:: Перехід у директорію скрипта
cd /d "%~dp0"

:: Запуск бат-файлу запуску вікна реєстрації
call run_register_gui.bat
