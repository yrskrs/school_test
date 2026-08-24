@echo off
chcp 65001 >nul
title SchoolTest Launcher

:: Перехід у директорію скрипта
cd /d "%~dp0"

:: Запуск бат-файлу запуску GUI
call run_gui.bat
