import random
import string
from typing import Optional

from sqlalchemy.orm import Session

from app import crud, models
from app.config import settings


def generate_access_code(db: Session) -> str:
    """
    Генерує унікальний цифровий код доступу до тесту.
    Довжина визначається в config.ACCESS_CODE_LENGTH.
    """
    for _ in range(20):
        code = "".join(
            random.choices(string.digits, k=settings.ACCESS_CODE_LENGTH)
        )
        if not crud.get_session_by_code(db, code):
            return code
    # Fallback: додаємо літери якщо всі числові зайняті
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def start_new_session(db: Session, test_id: int) -> models.TestSession:
    code = generate_access_code(db)
    return crud.create_session(db, test_id=test_id, access_code=code)


def find_active_session(db: Session, access_code: str) -> Optional[models.TestSession]:
    return crud.get_session_by_code(db, access_code)
