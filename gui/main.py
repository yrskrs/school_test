import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from gui.main_window import MainWindow
from gui.settings_manager import SettingsManager

def load_stylesheet() -> str:
    """Завантажує файл стилів QSS."""
    sm = SettingsManager()
    theme_name = sm.get_theme()
    if theme_name == "Світла":
        style_path = os.path.join(os.path.dirname(__file__), "styles", "light.qss")
    else:
        style_path = os.path.join(os.path.dirname(__file__), "styles", "dark.qss")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def main():
    # Налаштування для коректного відображення на різних екранах
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        from PyQt6.QtCore import Qt
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        
    app = QApplication(sys.argv)
    app.setApplicationName("ШколярТест — Менеджер сервера")
    
    # Спроба застосувати стилі
    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)
        
    # Спроба завантажити іконку
    icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "static", "favicon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
