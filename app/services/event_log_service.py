from typing import Optional

from sqlalchemy.orm import Session

from app import crud, models


def log_event(
    db: Session,
    attempt_id: int,
    event_type: models.EventType,
    details: Optional[str] = None,
) -> models.EventLog:
    """Записує подію в журнал. Не кидає виняток при помилці."""
    try:
        return crud.create_event_log(db, attempt_id, event_type, details)
    except Exception:
        db.rollback()
        return None
