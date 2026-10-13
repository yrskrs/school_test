"""Shared limits and persisted termination reason; compatible with existing logs."""
from app import models

MAX_VIOLATIONS = 3
VIOLATION_STOP_PREFIX = "violation_limit:"
TERMINAL_STATUSES = (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped)


def normalize_attempt_settings(data):
    """Keep legacy allow_retake compatible with the numeric/unlimited setting."""
    if "max_attempts" in data:
        limit = data["max_attempts"]
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 2147483647):
            raise ValueError("Кількість проходжень має бути цілим числом від 1 до 2147483647 або без обмежень.")
        data["allow_retake"] = limit is None or limit > 1
    elif "allow_retake" in data:
        data["max_attempts"] = None if data["allow_retake"] else 1


def lock_session(db, session):
    """Serialize attempt admission across workers on SQLite and PostgreSQL."""
    db.query(models.TestSession).filter_by(id=session.id).update(
        {models.TestSession.is_active: models.TestSession.is_active}, synchronize_session=False,
    )
    db.refresh(session)
    db.refresh(session.test)


def student_attempts(db, session_id, student_name, roster_student_id=None):
    query = db.query(models.StudentAttempt).filter_by(session_id=session_id)
    if roster_student_id is not None:
        return query.filter_by(roster_student_id=roster_student_id).order_by(models.StudentAttempt.id).all()
    name = " ".join(student_name.split()).casefold()
    return [a for a in query.filter_by(roster_student_id=None).order_by(models.StudentAttempt.id)
            if " ".join(a.student_name.split()).casefold() == name]


def has_attempt_capacity(test, attempts):
    return test.attempt_limit is None or len(attempts) < test.attempt_limit


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
