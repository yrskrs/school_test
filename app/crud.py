import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models
from app.security import get_password_hash


# ---------------------------------------------------------------------------
# Teacher CRUD
# ---------------------------------------------------------------------------

def get_teacher_by_username(db: Session, username: str) -> Optional[models.Teacher]:
    return db.query(models.Teacher).filter(models.Teacher.username == username).first()


def get_teacher_by_id(db: Session, teacher_id: int) -> Optional[models.Teacher]:
    return db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()


def get_all_teachers(db: Session) -> List[models.Teacher]:
    return db.query(models.Teacher).order_by(models.Teacher.id.asc()).all()


def create_teacher(
    db: Session,
    username: str,
    full_name: str,
    password: str,
    subject: Optional[str] = None,
    classes: Optional[str] = None,
    is_active: bool = True,
) -> models.Teacher:
    teacher = models.Teacher(
        username=username,
        full_name=full_name,
        hashed_password=get_password_hash(password),
        subject=subject,
        classes=classes,
        is_active=is_active,
    )
    db.add(teacher)
    db.commit()
    db.refresh(teacher)
    return teacher


def update_teacher_password(db: Session, teacher_id: int, new_password: str) -> Optional[models.Teacher]:
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        return None
    teacher.hashed_password = get_password_hash(new_password)
    db.commit()
    db.refresh(teacher)
    return teacher


def update_teacher(
    db: Session,
    teacher_id: int,
    full_name: Optional[str] = None,
    subject: Optional[str] = None,
    classes: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> Optional[models.Teacher]:
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        return None
    if full_name is not None:
        teacher.full_name = full_name
    if subject is not None:
        teacher.subject = subject
    if classes is not None:
        teacher.classes = classes
    if is_active is not None:
        teacher.is_active = is_active
    db.commit()
    db.refresh(teacher)
    return teacher


def delete_teacher(db: Session, teacher_id: int) -> bool:
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        return False
    db.delete(teacher)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Test CRUD
# ---------------------------------------------------------------------------

def get_tests_by_teacher(db: Session, teacher_id: int, is_archived: bool = False) -> List[models.Test]:
    return (
        db.query(models.Test)
        .filter(models.Test.teacher_id == teacher_id, models.Test.is_archived == is_archived)
        .order_by(models.Test.created_at.desc())
        .all()
    )


def get_test_by_id(db: Session, test_id: int) -> Optional[models.Test]:
    return db.query(models.Test).filter(models.Test.id == test_id).first()


def create_test(db: Session, teacher_id: int, data: dict) -> models.Test:
    questions_data = data.pop("questions", [])
    test = models.Test(teacher_id=teacher_id, **data)
    db.add(test)
    db.flush()  # отримуємо test.id без commit

    for q_data in questions_data:
        options_data = q_data.pop("options", [])
        question = models.Question(test_id=test.id, **q_data)
        db.add(question)
        db.flush()
        for o_data in options_data:
            option = models.AnswerOption(question_id=question.id, **o_data)
            db.add(option)

    db.commit()
    db.refresh(test)
    return test


def update_test(db: Session, test: models.Test, data: dict) -> models.Test:
    questions_data = data.pop("questions", [])

    for key, value in data.items():
        setattr(test, key, value)

    existing_q_map = {q.id: q for q in test.questions}
    kept_q_ids = set()

    for q_data in questions_data:
        q_id = q_data.pop("id", None)
        options_data = q_data.pop("options", [])
        
        if q_id and q_id in existing_q_map:
            question = existing_q_map[q_id]
            for k, v in q_data.items():
                setattr(question, k, v)
            kept_q_ids.add(q_id)
        else:
            question = models.Question(test_id=test.id, **q_data)
            db.add(question)
            
        db.flush() # ensure question has ID before adding options
            
        existing_o_map = {o.id: o for o in question.options}
        kept_o_ids = set()

        for o_data in options_data:
            o_id = o_data.pop("id", None)
            if o_id and o_id in existing_o_map:
                option = existing_o_map[o_id]
                for k, v in o_data.items():
                    setattr(option, k, v)
                kept_o_ids.add(o_id)
            else:
                option = models.AnswerOption(question_id=question.id, **o_data)
                db.add(option)
                
        for old_o in question.options:
            if old_o.id not in kept_o_ids:
                db.delete(old_o)

    for old_q in test.questions:
        if old_q.id not in kept_q_ids:
            # Delete any student answers associated to avoid NOT NULL constraint failure
            for ans in old_q.student_answers:
                db.delete(ans)
            db.delete(old_q)

    db.commit()
    db.refresh(test)
    return test


def delete_test(db: Session, test: models.Test) -> None:
    db.delete(test)
    db.commit()


# ---------------------------------------------------------------------------
# TestSession CRUD
# ---------------------------------------------------------------------------

def create_session(db: Session, test_id: int, access_code: str) -> models.TestSession:
    session = models.TestSession(test_id=test_id, access_code=access_code)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session_by_id(db: Session, session_id: int) -> Optional[models.TestSession]:
    return db.query(models.TestSession).filter(models.TestSession.id == session_id).first()


def get_session_by_code(db: Session, access_code: str) -> Optional[models.TestSession]:
    return (
        db.query(models.TestSession)
        .filter(models.TestSession.access_code == access_code, models.TestSession.is_active == True)
        .first()
    )


def get_all_sessions(db: Session, teacher_id: int, is_archived: bool = False) -> List[models.TestSession]:
    return (
        db.query(models.TestSession)
        .join(models.Test)
        .filter(models.Test.teacher_id == teacher_id, models.TestSession.is_archived == is_archived)
        .order_by(models.TestSession.started_at.desc())
        .all()
    )


def get_active_sessions(db: Session, teacher_id: int) -> List[models.TestSession]:
    return (
        db.query(models.TestSession)
        .join(models.Test)
        .filter(models.Test.teacher_id == teacher_id, models.TestSession.is_active == True)
        .all()
    )


def close_session(db: Session, session: models.TestSession) -> models.TestSession:
    session.is_active = False
    session.ended_at = datetime.now()
    db.commit()
    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# StudentAttempt CRUD
# ---------------------------------------------------------------------------

def create_attempt(db: Session, session_id: int, student_name: str) -> models.StudentAttempt:
    attempt = models.StudentAttempt(
        session_id=session_id,
        student_name=student_name,
        status=models.AttemptStatus.not_started,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def get_attempt_by_id(db: Session, attempt_id: int) -> Optional[models.StudentAttempt]:
    return db.query(models.StudentAttempt).filter(models.StudentAttempt.id == attempt_id).first()


def get_attempts_by_session(db: Session, session_id: int) -> List[models.StudentAttempt]:
    return (
        db.query(models.StudentAttempt)
        .filter(models.StudentAttempt.session_id == session_id)
        .order_by(models.StudentAttempt.started_at)
        .all()
    )


def get_all_finished_attempts(db: Session, teacher_id: int, is_archived: bool = False) -> List[models.StudentAttempt]:
    return (
        db.query(models.StudentAttempt)
        .join(models.TestSession)
        .join(models.Test)
        .filter(
            models.Test.teacher_id == teacher_id,
            models.StudentAttempt.status.in_([
                models.AttemptStatus.finished,
                models.AttemptStatus.timeout,
                models.AttemptStatus.stopped,
            ]),
            models.StudentAttempt.is_archived == is_archived
        )
        .order_by(models.StudentAttempt.finished_at.desc())
        .all()
    )


def start_attempt(db: Session, attempt: models.StudentAttempt) -> models.StudentAttempt:
    attempt.status = models.AttemptStatus.in_progress
    attempt.started_at = datetime.now()
    db.commit()
    db.refresh(attempt)
    return attempt


def finish_attempt(
    db: Session,
    attempt: models.StudentAttempt,
    score: float,
    max_score: float,
    status: models.AttemptStatus = models.AttemptStatus.finished,
) -> models.StudentAttempt:
    attempt.status = status
    attempt.finished_at = datetime.now()
    attempt.score = score
    attempt.max_score = max_score
    db.commit()
    db.refresh(attempt)
    return attempt


# ---------------------------------------------------------------------------
# StudentAnswer CRUD
# ---------------------------------------------------------------------------

def get_answer_by_attempt_and_question(
    db: Session, attempt_id: int, question_id: int
) -> Optional[models.StudentAnswer]:
    return (
        db.query(models.StudentAnswer)
        .filter(
            models.StudentAnswer.attempt_id == attempt_id,
            models.StudentAnswer.question_id == question_id,
        )
        .first()
    )


def upsert_answer(
    db: Session,
    attempt_id: int,
    question_id: int,
    answer_text: Optional[str],
    selected_options_json: Optional[str],
) -> models.StudentAnswer:
    answer = get_answer_by_attempt_and_question(db, attempt_id, question_id)
    if answer:
        answer.answer_text = answer_text
        answer.selected_options_json = selected_options_json
        answer.answered_at = datetime.now()
    else:
        answer = models.StudentAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
            answer_text=answer_text,
            selected_options_json=selected_options_json,
        )
        db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def get_answers_by_attempt(db: Session, attempt_id: int) -> List[models.StudentAnswer]:
    return (
        db.query(models.StudentAnswer)
        .filter(models.StudentAnswer.attempt_id == attempt_id)
        .all()
    )


def save_graded_answer(
    db: Session,
    answer: models.StudentAnswer,
    is_correct: bool,
    awarded_points: float,
) -> models.StudentAnswer:
    answer.is_correct = is_correct
    answer.awarded_points = awarded_points
    db.commit()
    db.refresh(answer)
    return answer


# ---------------------------------------------------------------------------
# EventLog CRUD
# ---------------------------------------------------------------------------

def create_event_log(
    db: Session,
    attempt_id: int,
    event_type: models.EventType,
    details: Optional[str] = None,
) -> models.EventLog:
    log = models.EventLog(
        attempt_id=attempt_id,
        event_type=event_type,
        details=details,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_event_logs_by_attempt(db: Session, attempt_id: int) -> List[models.EventLog]:
    return (
        db.query(models.EventLog)
        .filter(models.EventLog.attempt_id == attempt_id)
        .order_by(models.EventLog.event_time)
        .all()
    )
