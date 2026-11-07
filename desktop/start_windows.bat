@echo off
chcp 65001 >nul
title SchoolTest Launcher

:: Перехід у директорію скрипта
cd /d "%~dp0.."

:: Запуск бат-файлу запуску GUI
call "%~dp0run_gui.bat"
