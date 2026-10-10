import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db
from app.deps import get_owned_attempt
from app.security import get_student_attempt_id, set_student_cookie
from app.services import event_log_service, result_service, session_service, test_service
from app.templating import templates
from app.services.testing_policy import MAX_VIOLATIONS, VIOLATION_STOP_PREFIX, lock_attempt, stop_reason, violation_count

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

    previous_id = get_student_attempt_id(request)
    previous = crud.get_attempt_by_id(db, previous_id) if previous_id else None
    return templates.TemplateResponse(
        request,
        "student_login.html", {
            "prefilled_code": code,
            "auto_session": auto_session,
            "student_name": previous.student_name if previous else "",
        }
    )


@router.post("/student/login")
async def student_login(
    request: Request,
    response: Response,
    student_name: str = Form(...),
    access_code: str = Form(...),
    roster_student_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    student_name = student_name.strip()
    access_code = access_code.strip().upper()

    if not student_name or (len(student_name) > 200 and not roster_student_id):
        return templates.TemplateResponse(
        request,
        "student_login.html", {
            "error": "Введіть ім'я та прізвище (до 200 символів)",
        })

    session = session_service.find_active_session(db, access_code)
    if not session:
        return templates.TemplateResponse(
        request,
        "student_login.html", {
            "error": "Невірний код доступу або сесія завершена",
            "student_name": student_name,
        })

    if session.roster_class_id:
        pupil = db.query(models.RosterStudent).filter_by(id=roster_student_id, class_id=session.roster_class_id, active=True).first()
        if not pupil or not db.get(models.RosterClass, session.roster_class_id).active:
            return templates.TemplateResponse(request, "student_login.html", {"error": "Оберіть свій запис зі списку цього класу.", "prefilled_code": access_code}, status_code=400)
        student_name = pupil.full_name()[:200]
    elif roster_student_id:
        raise HTTPException(400, 'Сесію ще не прив’язано до класу.')

    # Перевіряємо, чи є вже спроба від цього учня в цій сесії
    existing_attempts = crud.get_attempts_by_session(db, session.id)
    student_attempts = [a for a in existing_attempts if a.roster_student_id == roster_student_id] if roster_student_id else [a for a in existing_attempts if a.roster_student_id is None and a.student_name.lower() == student_name.lower()]
    
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

    if existing and get_student_attempt_id(request) != existing.id:
        return templates.TemplateResponse(request, "student_login.html", {
            "error": "Цей учень уже проходить тест. Продовжіть у тому самому браузері або зверніться до вчителя.",
            "student_name": student_name, "prefilled_code": access_code,
        }, status_code=409)

    if existing:
        attempt = existing
    else:
        attempt = crud.create_attempt(db, session_id=session.id, student_name=student_name, roster_student_id=roster_student_id)
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
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):

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
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):

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
    locked_question_ids = []  # питання, на які вже є реальна відповідь
    for ans in existing_answers:
        if ans.selected_options_json:
            saved_answers[ans.question_id] = json.loads(ans.selected_options_json)
            locked_question_ids.append(ans.question_id)
        elif ans.answer_text is not None:
            saved_answers[ans.question_id] = ans.answer_text
            # Порожній текст ("") — означає закінчився час, теж блокуємо
            locked_question_ids.append(ans.question_id)

    # Отримуємо раніше пропущені питання, які досі не мають відповідей
    skipped_event_logs = db.query(models.EventLog).filter(
        models.EventLog.attempt_id == attempt_id,
        models.EventLog.event_type == models.EventType.question_skipped
    ).all()
    skipped_question_ids = []
    for log in skipped_event_logs:
        if log.details and log.details.startswith("question_id="):
            try:
                qid = int(log.details.split("=", 1)[1].split()[0])
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
        "locked_question_ids": locked_question_ids,
        "skipped_question_ids": skipped_question_ids,
        "time_remaining_seconds": time_remaining_seconds,
        "session_id": attempt.session_id,
        "violation_count": db.query(models.EventLog).filter_by(
            attempt_id=attempt.id, event_type=models.EventType.tab_blur
        ).count(),
    })


# ---------------------------------------------------------------------------
# Save answer (AJAX)
# ---------------------------------------------------------------------------

@router.post("/student/test/{attempt_id}/save-answer")
async def save_answer(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):
    from app.services.answer_service import same_submission, validate_submission
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="Невірний JSON відповіді")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Відповідь має бути об'єктом")
    question_id = body.get("question_id")
    if type(question_id) is not int or question_id <= 0:
        raise HTTPException(status_code=400, detail="question_id є обов'язковим")
    lock_attempt(db, attempt)
    existing_answer = crud.get_answer_by_attempt_and_question(db, attempt_id, question_id)
    if not existing_answer or existing_answer.question.test_id != attempt.session.test_id:
        raise HTTPException(status_code=404, detail="Питання не призначене цій спробі")
    answer_text, selected_json = validate_submission(existing_answer.question, body)
    if existing_answer and (
        existing_answer.selected_options_json is not None
        or existing_answer.answer_text is not None
    ):
        if same_submission(existing_answer, answer_text, selected_json):
            return {"status": "already_saved"}
        raise HTTPException(status_code=409, detail="Відповідь вже збережена і не може бути змінена")
    if attempt.status != models.AttemptStatus.in_progress:
        raise HTTPException(status_code=409, detail="Тест призупинено або завершено")

    is_correct, awarded = result_service.grade_answer(existing_answer.question, answer_text, selected_json)
    existing_answer.answer_text = answer_text
    existing_answer.selected_options_json = selected_json
    existing_answer.answered_at = datetime.now()
    existing_answer.is_correct = is_correct
    existing_answer.awarded_points = awarded
    db.commit()

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
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    if not isinstance(body, dict) or type(body.get("timeout", False)) is not bool:
        raise HTTPException(422, "Невірний формат завершення тесту")
    is_timeout = body.get("timeout", False)
    lock_attempt(db, attempt)
    if attempt.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        return {"status": "already_finished", "redirect": f"/student/test/{attempt_id}/finished"}

    score, max_score = result_service.grade_all_answers(db, attempt, commit=False)
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
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):
    if attempt.status not in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        return RedirectResponse(f"/student/test/{attempt_id}", status_code=303)
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
        "confirmed_question_ids": [a.question_id for a in answers if a.answer_text is not None or a.selected_options_json is not None],
        "percent": percent,
        "stop_reason": stop_reason(db, attempt_id),
    })


# ---------------------------------------------------------------------------
# Log browser event (AJAX)
# ---------------------------------------------------------------------------

@router.post("/student/event/{attempt_id}")
async def log_browser_event(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "Невірний JSON події")
    if not isinstance(body, dict):
        raise HTTPException(422, "Невірний формат події")
    event_type_str = body.get("event_type", "")

    try:
        event_type = models.EventType(event_type_str)
    except ValueError:
        return {"status": "unknown_event"}

    allowed = {models.EventType.tab_blur, models.EventType.tab_focus,
               models.EventType.connection_lost, models.EventType.reconnect,
               models.EventType.question_skipped, models.EventType.question_returned,
               models.EventType.question_changed}
    if event_type not in allowed:
        return {"status": "ignored"}
    details = body.get("details")
    if details is not None and (not isinstance(details, str) or len(details) > 2000):
        raise HTTPException(422, "Невірний опис події")
    client_event_id = body.get("client_event_id")
    suffix = ""
    if client_event_id:
        import re
        if not isinstance(client_event_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", client_event_id):
            raise HTTPException(422, "Невірний ідентифікатор події")
        suffix = f" [event:{client_event_id}]"
    stored_details = (details or "") + suffix

    # Serialize event insertion, retry detection and stopping for this attempt.
    # A no-op UPDATE also takes a write lock on SQLite (FOR UPDATE does not).
    lock_attempt(db, attempt)
    duplicate = bool(suffix and db.query(models.EventLog).filter(
        models.EventLog.attempt_id == attempt_id,
        models.EventLog.details.endswith(suffix, autoescape=True),
    ).first())
    terminal = attempt.status in (
        models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped,
    )
    ignored = event_type == models.EventType.tab_blur and (terminal or attempt.status == models.AttemptStatus.not_started)
    if not duplicate and not ignored:
        db.add(models.EventLog(attempt_id=attempt_id, event_type=event_type, details=stored_details))
        db.flush()
    count = violation_count(db, attempt_id)
    stopped_now = not terminal and attempt.status != models.AttemptStatus.not_started and count >= MAX_VIOLATIONS
    if stopped_now:
        score, max_score = result_service.grade_all_answers(db, attempt, commit=False)
        attempt.status = models.AttemptStatus.stopped
        attempt.finished_at = datetime.now()
        attempt.score, attempt.max_score = score, max_score
        db.add(models.EventLog(attempt_id=attempt_id, event_type=models.EventType.stop_test,
            details=f"{VIOLATION_STOP_PREFIX} Автоматична зупинка після {count} порушень; score={score}/{max_score}"))
    db.commit()

    from app.websocket_manager import ws_manager
    if not duplicate and not ignored:
        question_id = None
        if (details or "").startswith("question_id="):
            try:
                question_id = int(details.split("=", 1)[1].split(";", 1)[0])
            except ValueError:
                pass
        await ws_manager.broadcast(attempt.session_id, {
            "event": event_type.value, "student_name": attempt.student_name,
            "attempt_id": attempt_id, "question_id": question_id, "details": details,
            "violation_count": count if event_type == models.EventType.tab_blur else None,
        })
    if stopped_now:
        try:
            from app.services.result_export_service import generate_result_html
            from app.services.test_file_service import save_student_result_html
            save_student_result_html(attempt.session.test, attempt, generate_result_html(db, attempt))
        except Exception as exc:
            print(f"Error saving stopped HTML result: {exc}")
        await ws_manager.send_to_student(attempt_id, {"event": "stop", "reason": "violations"})
        await ws_manager.broadcast(attempt.session_id, {
            "event": "stop", "student_name": attempt.student_name, "attempt_id": attempt_id,
            "status": "stopped", "reason": "violations", "violation_count": count,
            "details": "Автоматична зупинка після третього порушення",
            "score": attempt.score, "max_score": attempt.max_score,
        })
    return {"status": "already_saved" if duplicate else "ignored" if ignored else "ok",
            "attempt_status": attempt.status.value, "violation_count": count,
            "stop_reason": stop_reason(db, attempt_id) if attempt.status == models.AttemptStatus.stopped else None}


@router.post("/student/test/{attempt_id}/retake")
async def retake_test(
    attempt_id: int,
    db: Session = Depends(get_db),
    attempt: models.StudentAttempt = Depends(get_owned_attempt),
):
    if not attempt.session.is_active or not attempt.session.test.allow_retake:
        raise HTTPException(403, "Повторне проходження недоступне")
    if attempt.status not in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
        raise HTTPException(409, "Спочатку завершіть поточну спробу")
    new_attempt = crud.create_attempt(db, attempt.session_id, attempt.student_name, roster_student_id=attempt.roster_student_id)
    event_log_service.log_event(db, new_attempt.id, models.EventType.login, "Повторне проходження")
    redirect = RedirectResponse(f"/student/instruction/{new_attempt.id}", status_code=303)
    set_student_cookie(redirect, new_attempt.id)
    return redirect
