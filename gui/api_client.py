import time
import json
import base64
import hmac
import hashlib
import urllib.request
import urllib.error

# Імпортуємо налаштування напряму
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings

class ApiClient:
    @staticmethod
    def _sign(payload: str) -> str:
        sig = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    @staticmethod
    def generate_teacher_cookie(teacher_id: int, username: str) -> str:
        payload = json.dumps({"id": teacher_id, "u": username, "t": int(time.time())})
        encoded = base64.b64encode(payload.encode()).decode()
        return ApiClient._sign(encoded)

    @staticmethod
    def control_attempt(base_url: str, attempt_id: int, action: str, teacher_id: int, username: str) -> bool:
        """
        Відправляє POST-запит до FastAPI додатку для керування спробою (pause, resume, stop).
        """
        cookie = ApiClient.generate_teacher_cookie(teacher_id, username)
        url = f"{base_url}/teacher/attempts/{attempt_id}/{action}"
        req = urllib.request.Request(url, method="POST")
        req.add_header("Cookie", f"{settings.SESSION_COOKIE_NAME}={cookie}")
        try:
            with urllib.request.urlopen(req) as response:
                return response.status == 200
        except urllib.error.URLError as e:
            print(f"API Client Error: {e}")
            return False
