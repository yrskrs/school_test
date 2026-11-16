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


def start_new_session(db: Session, test_id: int, lesson_date: Optional[str] = None) -> models.TestSession:
    from fastapi import HTTPException
    from sqlalchemy import func
    from app.services.roster_service import resolve_roster_class
    from app.time_utils import stored_now, kyiv_time
    test = crud.get_test_by_id(db, test_id)
    group, error = resolve_roster_class(db, test)
    if error:
        raise HTTPException(409, error)
    code = generate_access_code(db)
    if not lesson_date:
        lesson_date = kyiv_time(stored_now()).date().isoformat()
    subject_id = None
    if test and test.subject:
        subj = db.query(models.RosterSubject).filter(
            func.lower(func.trim(models.RosterSubject.name)) == func.lower(func.trim(test.subject))
        ).first()
        if subj:
            subject_id = subj.id
    return crud.create_session(
        db,
        test_id=test_id,
        access_code=code,
        roster_class_id=group.id if group else None,
        lesson_date=lesson_date,
        roster_subject_id=subject_id,
    )


def find_active_session(db: Session, access_code: str) -> Optional[models.TestSession]:
    return crud.get_session_by_code(db, access_code)
