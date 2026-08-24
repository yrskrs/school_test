import os
import subprocess
import psutil
import time
from PyQt6.QtCore import QThread, pyqtSignal

class ServerManager(QThread):
    """Менеджер для запуску та відстеження FastAPI сервера у фоні."""
    
    # Сигнали для взаємодії з UI
    server_output = pyqtSignal(str)
    server_status_changed = pyqtSignal(bool)
    server_stats_updated = pyqtSignal(dict)
    
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host
        self.port = port
        self.process = None
        self.is_running = False
        
        # Шлях до директорії проєкту та скрипта запуску
        self.root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.venv_python = os.path.join(self.root_dir, ".venv", "bin", "python")
        self.run_script = os.path.join(self.root_dir, "run.py")
        
        # Якщо .venv/bin/python немає (наприклад Windows), шукаємо .venv/Scripts/python.exe
        if not os.path.exists(self.venv_python):
            self.venv_python = os.path.join(self.root_dir, ".venv", "Scripts", "python.exe")
            
        self.stats_timer = None
        
    def start_server(self, host: str, port: int):
        """Запуск сервера з вказаними параметрами."""
        if self.is_running:
            return
            
        self.host = host
        self.port = port
        self.start() # Запускає run() в окремому потоці
        
    def stop_server(self):
        """Зупинка сервера."""
        if self.process:
            try:
                # Вбиваємо процес і всі дочірні процеси (uvicorn)
                parent = psutil.Process(self.process.pid)
                for child in parent.children(recursive=True):
                    child.terminate()
                parent.terminate()
                parent.wait(timeout=3)
            except Exception as e:
                try:
                    self.process.kill()
                except:
                    pass
                    
        self.is_running = False
        self.server_status_changed.emit(False)
        self.server_output.emit("Сервер зупинено.")
        
    def run(self):
        """Головний цикл потоку: запуск процесу та читання його виводу."""
        try:
            # Створюємо оточення (env)
            env = os.environ.copy()
            env["HOST"] = self.host
            env["PORT"] = str(self.port)
            
            # Налаштовуємо створення без консольного вікна на Windows
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            self.server_output.emit(f"Запуск сервера на {self.host}:{self.port}...")
            
            self.process = subprocess.Popen(
                [self.venv_python, self.run_script],
                cwd=self.root_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                startupinfo=startupinfo,
                bufsize=1,
                universal_newlines=True
            )
            
            self.is_running = True
            self.server_status_changed.emit(True)
            self.server_output.emit("Сервер успішно запущено!")
            
            # Читання виводу сервера
            while self.is_running and self.process.poll() is None:
                line = self.process.stdout.readline()
                if line:
                    self.server_output.emit(line.strip())
                else:
                    time.sleep(0.1)
                    
            if self.is_running:
                self.is_running = False
                self.server_status_changed.emit(False)
                self.server_output.emit("Процес сервера несподівано завершився.")
                
        except Exception as e:
            self.is_running = False
            self.server_status_changed.emit(False)
            self.server_output.emit(f"Помилка запуску: {str(e)}")

    def get_stats(self) -> dict:
        """Отримує використання CPU та RAM сервером."""
        stats = {"cpu": 0.0, "ram_mb": 0.0}
        if not self.is_running or not self.process:
            return stats
            
        try:
            parent = psutil.Process(self.process.pid)
            cpu_percent = 0.0
            ram_mb = 0.0
            
            # Включаємо дочірні процеси, бо uvicorn може спавнити воркерів
            processes = [parent] + parent.children(recursive=True)
            
            for p in processes:
                try:
                    cpu_percent += p.cpu_percent(interval=None)
                    ram_mb += p.memory_info().rss / (1024 * 1024)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
            stats["cpu"] = round(cpu_percent, 1)
            stats["ram_mb"] = round(ram_mb, 1)
        except Exception:
            pass
            
        return stats
