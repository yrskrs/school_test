import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db
from app.deps import get_current_attempt
from app.security import clear_student_cookie, set_student_cookie
from app.services import event_log_service, result_service, session_service, test_service
from app.templating import templates

router = APIRouter()


# ---------------------------------------------------------------------------
# Root — redirect to student login
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def root(request: Request, db: Session = Depends(get_db)):
    if crud.count_teachers(db) == 0:
        return RedirectResponse(url="/setup", status_code=303)
    return RedirectResponse(url="/student/login")


# ---------------------------------------------------------------------------
# Student login
# ---------------------------------------------------------------------------

@router.get("/student/login", response_class=HTMLResponse)
async def student_login_page(request: Request, code: Optional[str] = None, db: Session = Depends(get_db)):
    if crud.count_teachers(db) == 0:
        return RedirectResponse(url="/setup", status_code=303)
    auto_session = None
    if not code:
        # Шукаємо закріплену сесію (головну)
        auto_session = db.query(models.TestSession).filter(
            models.TestSession.is_active == True,
            models.TestSession.is_pinned == True
        ).first()
        
        if auto_session:
            code = auto_session.access_code

    return templates.TemplateResponse(
        request,
        "student_login.html", {
            "prefilled_code": code,
            "auto_session": auto_session
        }
    )


@router.post("/student/login")
async def student_login(
    request: Request,
    response: Response,
    student_name: str = Form(...),
    access_code: str = Form(...),
    db: Session = Depends(get_db),
):
    student_name = student_name.strip()
    access_code = access_code.strip().upper()

    if not student_name:
        return templates.TemplateResponse(
        request,
        "student_login.html", {
            "error": "Введіть ваше ім'я та прізвище",
        })

    session = session_service.find_active_session(db, access_code)
    if not session:
        return templates.TemplateResponse(
        request,
        "student_login.html", {
            "error": "Невірний код доступу або сесія завершена",
            "student_name": student_name,
        })

    # Перевіряємо, чи є вже спроба від цього учня в цій сесії
    existing_attempts = crud.get_attempts_by_session(db, session.id)
    student_attempts = [a for a in existing_attempts if a.student_name.lower() == student_name.lower()]
    
    existing = None
    if student_attempts:
        existing = sorted(student_attempts, key=lambda x: x.id, reverse=True)[0]

    if existing and existing.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        if not session.test.allow_retake:
            return templates.TemplateResponse(
            request,
            "student_login.html", {
                "error": "Ви вже завершили цей тест",
                "student_name": student_name,
            })
        else:
            existing = None  # Створюємо нову спробу

    if existing:
        attempt = existing
    else:
        attempt = crud.create_attempt(db, session_id=session.id, student_name=student_name)
        event_log_service.log_event(db, attempt.id, models.EventType.login, f"name={student_name}")
        try:
            from app.websocket_manager import ws_manager
            await ws_manager.broadcast(attempt.session_id, {
                "event": "login",
                "student_name": attempt.student_name,
                "attempt_id": attempt.id,
            })
        except Exception:
            pass

    redirect = RedirectResponse(url=f"/student/instruction/{attempt.id}", status_code=303)
    set_student_cookie(redirect, attempt.id)
    return redirect


# ---------------------------------------------------------------------------
# Instruction page
# ---------------------------------------------------------------------------

@router.get("/student/instruction/{attempt_id}", response_class=HTMLResponse)
async def student_instruction(
    request: Request,
    attempt_id: int,
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    if attempt.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        return RedirectResponse(url=f"/student/test/{attempt_id}/finished")

    test = attempt.session.test
    
    # Calculate actual question count
    existing_answers_count = db.query(models.StudentAnswer).filter_by(attempt_id=attempt.id).count()
    if existing_answers_count > 0:
        question_count = existing_answers_count
    else:
        excluded = []
        if test.excluded_topics:
            try:
                excluded = json.loads(test.excluded_topics)
            except Exception:
                pass
        all_questions = [q for q in test.questions if (q.topic or "") not in excluded]
        limit = test.random_questions_limit
        if limit and 0 < limit < len(all_questions):
            question_count = limit
        else:
            question_count = len(all_questions)

    return templates.TemplateResponse(
        request,
        "student_instruction.html", {
        "attempt": attempt,
        "test": test,
        "question_count": question_count,
    })


# ---------------------------------------------------------------------------
# Take test
# ---------------------------------------------------------------------------

@router.get("/student/test/{attempt_id}", response_class=HTMLResponse)
async def take_test(
    request: Request,
    attempt_id: int,
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    is_new_start = False
    if attempt.status == models.AttemptStatus.not_started:
        attempt = crud.start_attempt(db, attempt)
        event_log_service.log_event(db, attempt.id, models.EventType.start_test)
        is_new_start = True

    if attempt.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        return RedirectResponse(url=f"/student/test/{attempt_id}/finished")

    test = attempt.session.test
    test_payload = test_service.build_test_payload(db, attempt, shuffle=True)

    if is_new_start:
        try:
            from app.websocket_manager import ws_manager
            sorted_answers = sorted(attempt.answers, key=lambda a: a.question.order_index if a.question else 0)
            assigned_questions = [
                {"question_id": ans.question_id, "is_correct": ans.is_correct}
                for ans in sorted_answers
            ]
            await ws_manager.broadcast(attempt.session_id, {
                "event": "start_test",
                "student_name": attempt.student_name,
                "attempt_id": attempt.id,
                "assigned_questions": assigned_questions,
            })
        except Exception:
            pass

    # Підтягуємо збережені відповіді
    existing_answers = crud.get_answers_by_attempt(db, attempt_id)
    saved_answers = {}
    for ans in existing_answers:
        if ans.selected_options_json:
            saved_answers[ans.question_id] = json.loads(ans.selected_options_json)
        elif ans.answer_text is not None:
            saved_answers[ans.question_id] = ans.answer_text

    # Отримуємо раніше пропущені питання, які досі не мають відповідей
    skipped_event_logs = db.query(models.EventLog).filter(
        models.EventLog.attempt_id == attempt_id,
        models.EventLog.event_type == models.EventType.question_skipped
    ).all()
    skipped_question_ids = []
    for log in skipped_event_logs:
        if log.details and log.details.startswith("question_id="):
            try:
                qid = int(log.details.split("=")[1])
                if qid not in saved_answers and qid not in skipped_question_ids:
                    skipped_question_ids.append(qid)
            except ValueError:
                pass

    # Розрахунок залишку часу
    time_remaining_seconds = None
    if test.time_limit_minutes and attempt.started_at:
        elapsed = (datetime.now() - attempt.started_at).total_seconds()
        time_remaining_seconds = max(0, test.time_limit_minutes * 60 - int(elapsed))

    return templates.TemplateResponse(
        request,
        "take_test.html", {
        "attempt": attempt,
        "test": test,
        "test_payload": test_payload,
        "saved_answers": saved_answers,
        "skipped_question_ids": skipped_question_ids,
        "time_remaining_seconds": time_remaining_seconds,
        "session_id": attempt.session_id,
    })


# ---------------------------------------------------------------------------
# Save answer (AJAX)
# ---------------------------------------------------------------------------

@router.post("/student/test/{attempt_id}/save-answer")
async def save_answer(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt or attempt.status not in (
        models.AttemptStatus.in_progress, models.AttemptStatus.not_started
    ):
        raise HTTPException(status_code=400, detail="Неможливо зберегти відповідь")

    body = await request.json()
    question_id = body.get("question_id")
    answer_text = body.get("answer_text")
    selected_options = body.get("selected_options")

    if not question_id:
        raise HTTPException(status_code=400, detail="question_id є обов'язковим")

    selected_json = json.dumps(selected_options) if selected_options is not None else None

    answer = crud.upsert_answer(
        db,
        attempt_id=attempt_id,
        question_id=question_id,
        answer_text=answer_text,
        selected_options_json=selected_json,
    )

    from app.services import result_service
    is_correct, awarded = result_service.grade_answer(answer.question, answer_text, selected_json)
    crud.save_graded_answer(db, answer, is_correct, awarded)

    event_log_service.log_event(
        db, attempt.id, models.EventType.answer_saved,
        f"question_id={question_id}"
    )

    # Broadcast через WebSocket
    from app.websocket_manager import ws_manager
    await ws_manager.broadcast(attempt.session_id, {
        "event": "answer_saved",
        "student_name": attempt.student_name,
        "attempt_id": attempt_id,
        "question_id": question_id,
        "is_correct": is_correct,
    })

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Finish test
# ---------------------------------------------------------------------------

@router.post("/student/test/{attempt_id}/finish")
async def finish_test(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    if attempt.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        return {"status": "already_finished", "redirect": f"/student/test/{attempt_id}/finished"}

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    is_timeout = body.get("timeout", False)

    score, max_score = result_service.grade_all_answers(db, attempt)
    status = models.AttemptStatus.timeout if is_timeout else models.AttemptStatus.finished
    crud.finish_attempt(db, attempt, score=score, max_score=max_score, status=status)

    event_type = models.EventType.timeout_auto_submit if is_timeout else models.EventType.test_finished
    event_log_service.log_event(db, attempt.id, event_type, f"score={score}/{max_score}")

    try:
        from app.services.result_export_service import generate_result_html
        from app.services.test_file_service import save_student_result_html
        html_content = generate_result_html(db, attempt)
        save_student_result_html(attempt.session.test, attempt, html_content)
    except Exception as e:
        print(f"Error saving HTML result: {e}")

    from app.websocket_manager import ws_manager
    await ws_manager.broadcast(attempt.session_id, {
        "event": "test_finished",
        "student_name": attempt.student_name,
        "attempt_id": attempt_id,
        "score": score,
        "max_score": max_score,
        "status": status.value,
    })

    return {"status": "ok", "redirect": f"/student/test/{attempt_id}/finished"}


# ---------------------------------------------------------------------------
# Finished page
# ---------------------------------------------------------------------------

@router.get("/student/test/{attempt_id}/finished", response_class=HTMLResponse)
async def test_finished_page(
    request: Request,
    attempt_id: int,
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    test = attempt.session.test
    answers = crud.get_answers_by_attempt(db, attempt_id)
    answers = sorted(answers, key=lambda a: a.question.order_index if a.question else 0)
    answers_map = {a.question_id: a for a in answers}

    percent = 0
    if attempt.max_score and attempt.max_score > 0:
        percent = round(attempt.score / attempt.max_score * 100, 1)

    return templates.TemplateResponse(
        request,
        "test_finished.html", {
        "attempt": attempt,
        "test": test,
        "answers": answers,
        "answers_map": answers_map,
        "percent": percent,
    })


# ---------------------------------------------------------------------------
# Log browser event (AJAX)
# ---------------------------------------------------------------------------

@router.post("/student/event/{attempt_id}")
async def log_browser_event(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        return {"status": "ignored"}

    body = await request.json()
    event_type_str = body.get("event_type", "")

    try:
        event_type = models.EventType(event_type_str)
    except ValueError:
        return {"status": "unknown_event"}

    event_log_service.log_event(db, attempt_id, event_type, body.get("details"))

    if event_type in (
        models.EventType.connection_lost,
        models.EventType.tab_blur,
        models.EventType.tab_focus,
        models.EventType.question_skipped,
        models.EventType.question_returned
    ):
        from app.websocket_manager import ws_manager
        question_id = None
        details_str = body.get("details") or ""
        if details_str.startswith("question_id="):
            try:
                question_id = int(details_str.split("=")[1])
            except ValueError:
                pass

        await ws_manager.broadcast(attempt.session_id, {
            "event": event_type.value,
            "student_name": attempt.student_name,
            "attempt_id": attempt_id,
            "question_id": question_id,
            "details": body.get("details"),
        })

    return {"status": "ok"}
