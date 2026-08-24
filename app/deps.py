from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db
from app.security import get_teacher_session, get_student_attempt_id


# ---------------------------------------------------------------------------
# Database dependency (re-exported for convenience)
# ---------------------------------------------------------------------------

def get_database() -> Session:
    """Alias для використання в роутах через Depends."""
    return next(get_db())


# ---------------------------------------------------------------------------
# Teacher auth dependency
# ---------------------------------------------------------------------------

def get_current_teacher(
    request: Request,
    db: Session = Depends(get_db),
) -> models.Teacher:
    """
    Перевіряє cookie-сесію вчителя.
    Якщо сесія невалідна — кидає HTTPException 401.
    """
    session_data = get_teacher_session(request)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Необхідна авторизація вчителя",
        )
    teacher = crud.get_teacher_by_id(db, teacher_id=session_data["id"])
    if not teacher or not teacher.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Обліковий запис вчителя не знайдено або деактивовано",
        )
    return teacher


def get_optional_teacher(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[models.Teacher]:
    """Повертає вчителя або None (не кидає виняток)."""
    session_data = get_teacher_session(request)
    if not session_data:
        return None
    return crud.get_teacher_by_id(db, teacher_id=session_data["id"])


# ---------------------------------------------------------------------------
# Student attempt dependency
# ---------------------------------------------------------------------------

def get_current_attempt(
    request: Request,
    db: Session = Depends(get_db),
) -> models.StudentAttempt:
    """
    Читає attempt_id учня з cookie і повертає об'єкт спроби.
    Кидає 401, якщо cookie відсутній або спроба не знайдена.
    """
    attempt_id = get_student_attempt_id(request)
    if not attempt_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сесія учня не знайдена. Будь ласка, увійдіть знову.",
        )
    attempt = crud.get_attempt_by_id(db, attempt_id=attempt_id)
    if not attempt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Спробу не знайдено.",
        )
    return attempt
