import os
from PyQt6.QtCore import QSettings

class SettingsManager:
    """Клас для керування налаштуваннями додатку за допомогою QSettings."""
    def __init__(self):
        self.settings = QSettings("SchoolTest", "AdminGUI")
        
    def get(self, key: str, default=None):
        """Отримати значення за ключем."""
        return self.settings.value(key, default)
        
    def set(self, key: str, value):
        """Зберегти значення за ключем."""
        self.settings.setValue(key, value)
        
    def get_port(self) -> int:
        return int(self.get("server/port", 8000))
        
    def set_port(self, port: int):
        self.set("server/port", port)
        
    def get_auto_start(self) -> bool:
        val = self.get("server/auto_start", False)
        if isinstance(val, str):
            return val.lower() == 'true'
        return bool(val)
        
    def set_auto_start(self, auto_start: bool):
        self.set("server/auto_start", auto_start)
        
    def get_theme(self) -> str:
        return self.get("ui/theme", "dark")
        
    def set_theme(self, theme: str):
        self.set("ui/theme", theme)

    def get_last_ip(self) -> str:
        return self.get("network/last_ip", "")

    def set_last_ip(self, ip: str):
        self.set("network/last_ip", ip)

    def get_last_teacher_id(self) -> int:
        return self.get("appearance/last_teacher_id", None)
        
    def set_last_teacher_id(self, teacher_id: int):
        self.set("appearance/last_teacher_id", teacher_id)
