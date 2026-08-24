from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QObject

class TrayManager(QObject):
    """Менеджер для роботи з системним треєм."""
    
    def __init__(self, main_window, app):
        super().__init__()
        self.main_window = main_window
        self.app = app
        
        # Створення іконки трею
        self.tray_icon = QSystemTrayIcon(self)
        # Використовуємо стандартну іконку, якщо власної немає (TODO: додати власну іконку)
        icon = self.main_window.style().standardIcon(self.main_window.style().StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("ШколярТест — Менеджер сервера")
        
        # Створення контекстного меню
        self.tray_menu = QMenu()
        
        self.action_show = QAction("Відкрити програму", self)
        self.action_show.triggered.connect(self.main_window.showNormal)
        
        self.action_start = QAction("Запустити сервер", self)
        self.action_start.triggered.connect(self.main_window.start_server)
        
        self.action_stop = QAction("Зупинити сервер", self)
        self.action_stop.triggered.connect(self.main_window.stop_server)
        self.action_stop.setEnabled(False)
        
        self.action_web = QAction("Відкрити веб-інтерфейс", self)
        self.action_web.triggered.connect(self.main_window.open_browser)
        
        self.action_exit = QAction("Завершити програму", self)
        self.action_exit.triggered.connect(self.app.quit)
        
        self.tray_menu.addAction(self.action_show)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.action_start)
        self.tray_menu.addAction(self.action_stop)
        self.tray_menu.addAction(self.action_web)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.action_exit)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        
        # Обробка кліку на іконку
        self.tray_icon.activated.connect(self.on_tray_activated)
        
    def show(self):
        self.tray_icon.show()
        
    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.main_window.isHidden():
                self.main_window.showNormal()
            else:
                self.main_window.hide()
                
    def update_server_status(self, is_running: bool):
        self.action_start.setEnabled(not is_running)
        self.action_stop.setEnabled(is_running)
        status = "Запущений" if is_running else "Зупинений"
        self.tray_icon.setToolTip(f"ШколярТест — Менеджер сервера\nСтатус: {status}")
        
    def show_message(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information, timeout=3000):
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(title, message, icon, timeout)
