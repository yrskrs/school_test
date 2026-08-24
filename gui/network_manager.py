import socket
from typing import List

class NetworkManager:
    """Менеджер мережевих з'єднань для отримання локальних IP-адрес."""
    
    @staticmethod
    def get_local_ips() -> List[str]:
        """Отримує всі локальні IPv4 адреси пристрою."""
        ips = ["127.0.0.1"] # Завжди додаємо localhost
        try:
            # Отримуємо ім'я хоста
            hostname = socket.gethostname()
            # Отримуємо всі IP адреси, пов'язані з цим хостом
            _, _, ipaddrlist = socket.gethostbyname_ex(hostname)
            
            for ip in ipaddrlist:
                if ip not in ips and not ip.startswith("169.254"):
                    ips.append(ip)
                    
            # Додатковий спосіб для Linux/Mac (підключення до зовнішнього сервера без фактичної передачі)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                primary_ip = s.getsockname()[0]
                s.close()
                
                if primary_ip not in ips:
                    ips.insert(1, primary_ip) # Додаємо після localhost
                elif primary_ip != "127.0.0.1":
                    # Переміщуємо основний IP на перше місце (після localhost)
                    ips.remove(primary_ip)
                    ips.insert(1, primary_ip)
            except Exception:
                pass
                
        except Exception:
            pass
            
        return ips
