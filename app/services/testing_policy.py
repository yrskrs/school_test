"""Shared limits and persisted termination reason; compatible with existing logs."""
from app import models

MAX_VIOLATIONS = 3
VIOLATION_STOP_PREFIX = "violation_limit:"


def lock_attempt(db, attempt):
    """Serialize saves, completion and events across processes on SQLite/PG."""
    db.query(models.StudentAttempt).filter_by(id=attempt.id).update(
        {models.StudentAttempt.status: models.StudentAttempt.status}, synchronize_session=False,
    )
    db.refresh(attempt)


def violation_count(db, attempt_id):
    return db.query(models.EventLog).filter_by(
        attempt_id=attempt_id, event_type=models.EventType.tab_blur,
    ).count()


def stop_reason(db, attempt_id):
    automatic_stop = db.query(models.EventLog).filter(
        models.EventLog.attempt_id == attempt_id,
        models.EventLog.event_type == models.EventType.stop_test,
        models.EventLog.details.startswith(VIOLATION_STOP_PREFIX),
    ).first()
    return "violations" if automatic_stop else None
