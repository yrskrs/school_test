from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db
from app.services.testing_policy import violation_count, stop_reason
from app.deps import get_current_teacher
from app.security import get_student_attempt_id, get_teacher_session

router = APIRouter(prefix="/api")


@router.get("/version")
async def site_version():
    from app.config import settings
    return {"version": settings.APP_VERSION}


@router.get("/session-status/{session_id}")
async def session_status(session_id: int, response: Response, db: Session = Depends(get_db),
                         teacher: models.Teacher = Depends(get_current_teacher)):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")

    response.headers["Cache-Control"] = "no-store"
    attempts = crud.get_attempts_by_session(db, session_id)
    from sqlalchemy import func
    violation_counts = dict(db.query(models.EventLog.attempt_id, func.count(models.EventLog.id)).join(
        models.StudentAttempt
    ).filter(models.StudentAttempt.session_id == session_id,
             models.EventLog.event_type == models.EventType.tab_blur).group_by(models.EventLog.attempt_id).all())
    attempts_data = []
    for a in attempts:
        sorted_answers = sorted(a.answers, key=lambda ans: ans.question.order_index if ans.question else 0)
        attempts_data.append({
            "id": a.id,
            "student_name": a.student_name,
            "violation_count": violation_counts.get(a.id, 0),
            "status": a.status.value,
            "score": a.score,
            "max_score": a.max_score,
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "finished_at": a.finished_at.isoformat() if a.finished_at else None,
            "assigned_questions": [
                {
                    "question_id": ans.question_id,
                    "is_correct": ans.is_correct,
                }
                for ans in sorted_answers
            ]
        })

    return {
        "session_id": session.id,
        "is_active": session.is_active,
        "access_code": session.access_code,
        "test_title": session.test.title if session.test else "",
        "attempts": attempts_data,
    }


@router.get("/attempt/{attempt_id}")
async def get_attempt(attempt_id: int, request: Request, response: Response, db: Session = Depends(get_db)):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    own_student = get_student_attempt_id(request) == attempt_id
    teacher_session = get_teacher_session(request)
    teacher = crud.get_teacher_by_id(db, teacher_session["id"]) if teacher_session else None
    own_teacher = teacher and teacher.is_active and attempt.session.test.teacher_id == teacher.id
    if not own_student and not own_teacher:
        raise HTTPException(401, "Сесія учня не знайдена")
    response.headers["Cache-Control"] = "no-store"
    visible_score = own_teacher or (
        attempt.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped)
        and attempt.session.test.show_result_after_finish
    )
    return {
        "id": attempt.id,
        "student_name": attempt.student_name,
        "status": attempt.status.value,
        "score": attempt.score if visible_score else None,
        "max_score": attempt.max_score if visible_score else None,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        "session_id": attempt.session_id,
        "violation_count": violation_count(db, attempt_id),
        "stop_reason": stop_reason(db, attempt_id) if attempt.status == models.AttemptStatus.stopped else None,
    }


@router.get("/test/{test_id}")
async def get_test(test_id: int, db: Session = Depends(get_db),
                   teacher: models.Teacher = Depends(get_current_teacher)):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")

    return {
        "id": test.id,
        "title": test.title,
        "subject": test.subject,
        "class_name": test.class_name,
        "time_limit_minutes": test.time_limit_minutes,
        "question_count": len(test.questions),
    }
