#!/usr/bin/env python3
import sys
import os

# Додаємо кореневу директорію до PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from gui.register_window import RegisterWindow
from gui.settings_manager import SettingsManager

def load_stylesheet() -> str:
    """Завантажує файл стилів QSS відповідно до збереженої теми."""
    sm = SettingsManager()
    theme_name = sm.get_theme()
    if theme_name == "Світла":
        style_path = os.path.join(os.path.dirname(__file__), "gui", "styles", "light.qss")
    else:
        style_path = os.path.join(os.path.dirname(__file__), "gui", "styles", "dark.qss")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def main():
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        from PyQt6.QtCore import Qt
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        
    app = QApplication(sys.argv)
    app.setApplicationName("ШколярТест — Реєстрація та користувачі")
    
    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)
        
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "static", "favicon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        
    window = RegisterWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
