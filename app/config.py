import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings:
    APP_NAME: str = "ШколярТест — Система шкільного тестування"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'school_testing.db'}")

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-secret-key-in-production-please")
    SESSION_COOKIE_NAME: str = "teacher_session"
    STUDENT_COOKIE_NAME: str = "student_attempt"
    SESSION_MAX_AGE: int = 60 * 60 * 8  # 8 годин

    # Default teacher credentials (password is hashed at startup)
    DEFAULT_TEACHER_USERNAME: str = "admin"
    DEFAULT_TEACHER_PASSWORD: str = "admin123"
    DEFAULT_TEACHER_NAME: str = "Адміністратор"

    # Access code settings
    ACCESS_CODE_LENGTH: int = 6

    # Paths
    TEMPLATES_DIR: str = str(BASE_DIR / "app" / "templates")
    STATIC_DIR: str = str(BASE_DIR / "app" / "static")
    SAMPLE_DATA_DIR: str = str(BASE_DIR / "sample_data")


settings = Settings()
