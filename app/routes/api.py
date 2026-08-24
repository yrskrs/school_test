from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db

router = APIRouter(prefix="/api")


@router.get("/session-status/{session_id}")
async def session_status(session_id: int, db: Session = Depends(get_db)):
    session = crud.get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")

    attempts = crud.get_attempts_by_session(db, session_id)
    attempts_data = []
    for a in attempts:
        sorted_answers = sorted(a.answers, key=lambda ans: ans.question.order_index if ans.question else 0)
        attempts_data.append({
            "id": a.id,
            "student_name": a.student_name,
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
async def get_attempt(attempt_id: int, db: Session = Depends(get_db)):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    return {
        "id": attempt.id,
        "student_name": attempt.student_name,
        "status": attempt.status.value,
        "score": attempt.score,
        "max_score": attempt.max_score,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        "session_id": attempt.session_id,
    }


@router.get("/test/{test_id}")
async def get_test(test_id: int, db: Session = Depends(get_db)):
    test = crud.get_test_by_id(db, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Тест не знайдено")

    return {
        "id": test.id,
        "title": test.title,
        "subject": test.subject,
        "class_name": test.class_name,
        "time_limit_minutes": test.time_limit_minutes,
        "question_count": len(test.questions),
    }
