import json
import re
from typing import Optional

from fastapi import (
    APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile, File
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import crud, models
from app.config import settings
from app.database import get_db
from app.deps import get_current_teacher
from app.security import (
    clear_teacher_session,
    create_teacher_session,
    verify_password,
)
from app.services import (
    import_export_service,
    session_service,
    test_service,
)
from app.templating import templates
import os
import shutil
import uuid

from app.services.test_file_service import (
    save_test_locally,
    delete_test_locally,
    move_temp_images_to_test,
    cleanup_unused_images,
    get_test_folder_name,
    rename_test_folder,
    generate_new_folder_name,
)

from app.services.logger_service import log_teacher_action, check_and_create_archive

router = APIRouter(prefix="/teacher")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def teacher_login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "teacher_login.html", {})


@router.post("/login")
async def teacher_login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    teacher = crud.get_teacher_by_username(db, username)
    if not teacher or not verify_password(password, teacher.hashed_password):
        log_teacher_action(db, username, "Невідомий користувач", "login_failed", f"Невдала спроба входу для логіну: '{username}'", None)
        return templates.TemplateResponse(
            request,
            "teacher_login.html",
            {"error": "Невірний логін або пароль"},
            status_code=401,
        )
    redirect = RedirectResponse(url="/teacher/dashboard", status_code=303)
    create_teacher_session(redirect, teacher.id, teacher.username)
    
    # Log successful login
    log_teacher_action(db, teacher.username, teacher.full_name, "login", "Вхід у систему", teacher.id)
    
    # Auto-archive check if logged in as admin
    if teacher.username == "admin":
        check_and_create_archive(db, password)
        
    return redirect


@router.get("/logout")
async def teacher_logout(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    redirect = RedirectResponse(url="/teacher/login", status_code=303)
    clear_teacher_session(redirect)
    log_teacher_action(db, teacher.username, teacher.full_name, "logout", "Вихід із системи", teacher.id)
    return redirect


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_class=HTMLResponse)
async def teacher_dashboard(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    if teacher.username == "admin":
        active_sessions = []
        finished_attempts = db.query(models.StudentAttempt).filter(models.StudentAttempt.finished_at != None).all()
        test_count = db.query(models.Test).count()
    else:
        active_sessions = crud.get_active_sessions(db, teacher.id)
        finished_attempts = crud.get_all_finished_attempts(db, teacher.id)
        tests = crud.get_tests_by_teacher(db, teacher.id)
        test_count = len(tests)

    from datetime import datetime, timedelta

    # 1. Activity last 7 days
    today = datetime.now().date()
    activity_dates = [(today - timedelta(days=i)).strftime("%d.%m") for i in range(6, -1, -1)]
    activity_counts = {d: 0 for d in activity_dates}

    for a in finished_attempts:
        if a.finished_at:
            d_str = a.finished_at.strftime("%d.%m")
            if d_str in activity_counts:
                activity_counts[d_str] += 1

    chart_activity_labels = list(activity_counts.keys())
    chart_activity_data = list(activity_counts.values())

    # 2. Average score by test (top 5 tests, 12-point system)
    test_scores = {}
    for a in finished_attempts:
        if a.score is not None and a.max_score and a.max_score > 0:
            test_title = a.session.test.title
            max_grade = a.session.test.max_grade or 12
            score_12 = (a.score / a.max_score) * max_grade
            if test_title not in test_scores:
                test_scores[test_title] = []
            test_scores[test_title].append(score_12)
    
    test_stats = []
    for t_title, scores in test_scores.items():
        test_stats.append({
            "title": t_title[:25] + ("..." if len(t_title)>25 else ""),
            "avg": round(sum(scores)/len(scores), 1),
            "count": len(scores)
        })
    test_stats.sort(key=lambda x: x["count"], reverse=True)
    test_stats = test_stats[:5]

    chart_tests_labels = [ts["title"] for ts in test_stats]
    chart_tests_data = [ts["avg"] for ts in test_stats]

    # 3. Students by class statistics
    class_data = {}
    for a in finished_attempts:
        c_name = a.session.test.class_name or "Без класу"
        s_name = a.student_name
        if c_name not in class_data:
            class_data[c_name] = {"students": set(), "scores": [], "attempts": 0}
        
        if s_name:
            class_data[c_name]["students"].add(s_name)
        
        class_data[c_name]["attempts"] += 1
        if a.score is not None and a.max_score and a.max_score > 0:
            max_grade = a.session.test.max_grade or 12
            score_12 = (a.score / a.max_score) * max_grade
            class_data[c_name]["scores"].append(score_12)
    
    class_stats = []
    for c_name, data in class_data.items():
        if len(data["students"]) > 0:
            avg = round(sum(data["scores"])/len(data["scores"]), 1) if data["scores"] else 0
            class_stats.append({
                "class_name": c_name,
                "student_count": len(data["students"]),
                "attempt_count": data["attempts"],
                "avg_score": avg
            })
    class_stats.sort(key=lambda x: x["class_name"])
    
    chart_classes_labels = [cs["class_name"] for cs in class_stats]
    chart_classes_data = [cs["avg_score"] for cs in class_stats]

    admin_data = None
    if teacher.username == "admin":
        # 1. Teachers info
        all_teachers = db.query(models.Teacher).all()
        teachers_info = []
        for t in all_teachers:
            latest_log = db.query(models.TeacherLog).filter(models.TeacherLog.teacher_id == t.id).order_by(models.TeacherLog.created_at.desc()).first()
            active_tests = db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == t.id, models.TestSession.is_active == True).count()
            teachers_info.append({
                "teacher": t,
                "latest_action": latest_log.action if latest_log else "Немає активності",
                "latest_time": latest_log.created_at if latest_log else None,
                "active_tests": active_tests
            })
            
        # 2. All active sessions
        all_active_sessions = db.query(models.TestSession).filter(models.TestSession.is_active == True).order_by(models.TestSession.started_at.desc()).all()
        
        admin_data = {
            "teachers_info": teachers_info,
            "all_active_sessions": all_active_sessions
        }

    return templates.TemplateResponse(
        request,
        "teacher_dashboard.html", {
        "teacher": teacher,
        "active_sessions": active_sessions,
        "test_count": test_count,
        "class_stats": class_stats,
        "chart_activity_labels": json.dumps(chart_activity_labels),
        "chart_activity_data": json.dumps(chart_activity_data),
        "chart_tests_labels": json.dumps(chart_tests_labels),
        "chart_tests_data": json.dumps(chart_tests_data),
        "chart_classes_labels": json.dumps(chart_classes_labels),
        "chart_classes_data": json.dumps(chart_classes_data),
        "admin_data": admin_data,
    })


# ---------------------------------------------------------------------------
# Notifications API
# ---------------------------------------------------------------------------

@router.get("/notifications")
async def get_notifications(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func

    # 1. Знайдемо дублікати варіантів відповідей в межах одного питання
    duplicates = db.query(
        models.Test.id.label("test_id"),
        models.Test.title.label("test_title"),
        models.Question.id.label("question_id"),
        models.Question.question_text.label("question_text"),
        models.AnswerOption.option_text,
        func.count(models.AnswerOption.id).label("count")
    ).join(models.Question, models.Test.id == models.Question.test_id) \
     .join(models.AnswerOption, models.Question.id == models.AnswerOption.question_id) \
     .filter(models.Test.teacher_id == teacher.id) \
     .filter(models.AnswerOption.option_text != None) \
     .filter(func.trim(models.AnswerOption.option_text) != "") \
     .group_by(models.Question.id, func.lower(func.trim(models.AnswerOption.option_text))) \
     .having(func.count(models.AnswerOption.id) > 1) \
     .all()

    notifications = []
    for dup in duplicates:
        notifications.append({
            "id": f"opt_{dup.question_id}_{dup.option_text[:10]}",
            "test_id": dup.test_id,
            "test_title": dup.test_title,
            "question_id": dup.question_id,
            "question_text": dup.question_text or "Без тексту",
            "problem_type": "Дублювання варіанту відповідей",
            "problem_detail": f"У питанні повторюється однакова відповідь: «{dup.option_text}»",
            "message": f"Тест '{dup.test_title}': дублювання варіанту «{dup.option_text}» у питанні",
            "link": f"/teacher/tests/{dup.test_id}/edit#question-{dup.question_id}"
        })

    # 2. Знайдемо дублікати ПРАВИХ частин (matching_text) у питаннях на відповідність
    matching_duplicates = db.query(
        models.Test.id.label("test_id"),
        models.Test.title.label("test_title"),
        models.Question.id.label("question_id"),
        models.Question.question_text.label("question_text"),
        models.AnswerOption.matching_text,
        func.count(models.AnswerOption.id).label("count")
    ).join(models.Question, models.Test.id == models.Question.test_id) \
     .join(models.AnswerOption, models.Question.id == models.AnswerOption.question_id) \
     .filter(models.Test.teacher_id == teacher.id) \
     .filter(models.Question.question_type == models.QuestionType.matching) \
     .filter(models.AnswerOption.matching_text != None) \
     .filter(func.trim(models.AnswerOption.matching_text) != "") \
     .group_by(models.Question.id, func.lower(func.trim(models.AnswerOption.matching_text))) \
     .having(func.count(models.AnswerOption.id) > 1) \
     .all()

    for dup in matching_duplicates:
        notifications.append({
            "id": f"match_{dup.question_id}_{dup.matching_text[:10]}",
            "test_id": dup.test_id,
            "test_title": dup.test_title,
            "question_id": dup.question_id,
            "question_text": dup.question_text or "Без тексту",
            "problem_type": "Дублювання відповідностей",
            "problem_detail": f"У питанні на відповідність дублюється права частина «{dup.matching_text}»",
            "message": f"Тест '{dup.test_title}': дублювання відповідності «{dup.matching_text}»",
            "link": f"/teacher/tests/{dup.test_id}/edit#question-{dup.question_id}"
        })

    return {"status": "ok", "count": len(notifications), "notifications": notifications}


# ---------------------------------------------------------------------------
# Tests list
# ---------------------------------------------------------------------------

@router.get("/tests", response_class=HTMLResponse)
async def teacher_tests_list(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    tab: str = "active",
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    is_archived = (tab == "archive")
    tests = crud.get_tests_by_teacher(db, teacher.id, is_archived=is_archived)
    tests_with_count = []
    for t in tests:
        tests_with_count.append({
            "test": t,
            "question_count": len(t.questions),
        })
    
    import math
    total_tests = len(tests_with_count)
    total_pages = 1
    if per_page > 0:
        total_pages = max(1, math.ceil(total_tests / per_page))
        if page < 1: page = 1
        elif page > total_pages: page = total_pages
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_tests = tests_with_count[start_idx:end_idx]
    else:
        paginated_tests = tests_with_count
        
    return templates.TemplateResponse(
        request,
        "test_sessions.html", {
        "teacher": teacher,
        "tests_with_count": paginated_tests,
        "page": "tests",
        "current_page": page,
        "total_pages": total_pages,
        "total_tests": total_tests,
        "per_page": per_page,
        "tab": tab,
    })


# ---------------------------------------------------------------------------
# Create test
# ---------------------------------------------------------------------------

@router.get("/tests/create", response_class=HTMLResponse)
async def create_test_page(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
):
    teacher_classes = [c.strip() for c in teacher.classes.split(",") if c.strip()] if teacher.classes else []
    return templates.TemplateResponse(
        request,
        "create_test.html", {
        "teacher": teacher,
        "teacher_classes": teacher_classes,
    })


@router.post("/tests/create")
async def create_test(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    title: str = Form(...),
    subject: str = Form(""),
    class_name: str = Form(""),
    description: str = Form(""),
    time_limit_minutes: Optional[str] = Form(None),
    time_limit_per_question: Optional[str] = Form(None),
    shuffle_questions: bool = Form(False),
    shuffle_options: bool = Form(False),
    show_result_after_finish: bool = Form(True),
    show_correct_answers: bool = Form(False),
    is_formative: bool = Form(False),
    use_fuzzy_matching: bool = Form(False),
    allow_partial_grading: bool = Form(False),
    allow_retake: bool = Form(False),
    max_grade: int = Form(12),
    random_questions_limit: Optional[int] = Form(None),
    excluded_topics: Optional[str] = Form("[]"),
    questions_json: str = Form("[]"),
    upload_session_id: Optional[str] = Form(None),
):
    try:
        questions_data = json.loads(questions_json)
    except json.JSONDecodeError:
        return templates.TemplateResponse(
        request,
        "create_test.html", {
            "teacher": teacher,
            "error": "Невірний формат питань (JSON)",
        })

    time_limit = int(time_limit_minutes) if time_limit_minutes and time_limit_minutes.strip() else None
    time_limit_q = int(time_limit_per_question) if time_limit_per_question and time_limit_per_question.strip() else None

    test_data = {
        "title": title,
        "subject": (subject.strip() if subject else None) or teacher.subject or None,
        "class_name": class_name or None,
        "description": description or None,
        "time_limit_minutes": time_limit,
        "time_limit_per_question": time_limit_q,
        "shuffle_questions": shuffle_questions,
        "shuffle_options": shuffle_options,
        "show_result_after_finish": show_result_after_finish,
        "show_correct_answers": show_correct_answers,
        "is_formative": is_formative,
        "use_fuzzy_matching": use_fuzzy_matching,
        "allow_partial_grading": allow_partial_grading,
        "allow_retake": allow_retake,
        "max_grade": max_grade,
        "random_questions_limit": random_questions_limit,
        "excluded_topics": excluded_topics,
        "questions": questions_data,
    }

    test = crud.create_test(db, teacher_id=teacher.id, data=test_data)
    test.folder_name = generate_new_folder_name(test)
    db.commit()
    move_temp_images_to_test(test, upload_session_id, db)
    save_test_locally(test)
    log_teacher_action(db, teacher.username, teacher.full_name, "create_test", f"Створено тест '{test.title}' (ID: {test.id})", teacher.id)
    return RedirectResponse(url=f"/teacher/tests/{test.id}/edit?created=1", status_code=303)


# ---------------------------------------------------------------------------
# Edit test
# ---------------------------------------------------------------------------

@router.get("/tests/{test_id}/edit", response_class=HTMLResponse)
async def edit_test_page(
    request: Request,
    test_id: int,
    created: Optional[str] = None,
    imported: Optional[str] = None,
    folder_path: Optional[str] = None,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")

    test_json = json.dumps(_test_to_dict(test), ensure_ascii=False)
    teacher_classes = [c.strip() for c in teacher.classes.split(",") if c.strip()] if teacher.classes else []
    
    success_msg = None
    if imported == "1" and folder_path:
        success_msg = f"Тест успішно імпортовано! Збережено у папку: {folder_path}"
    elif created == "1":
        success_msg = "Тест успішно створено! Тепер додайте питання."
        
    return templates.TemplateResponse(
        request,
        "edit_test.html", {
        "teacher": teacher,
        "test": test,
        "test_json": test_json,
        "teacher_classes": teacher_classes,
        "success": success_msg,
    })


@router.post("/tests/{test_id}/edit")
async def edit_test(
    request: Request,
    test_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    title: str = Form(...),
    subject: str = Form(""),
    class_name: str = Form(""),
    description: str = Form(""),
    time_limit_minutes: Optional[str] = Form(None),
    time_limit_per_question: Optional[str] = Form(None),
    shuffle_questions: bool = Form(False),
    shuffle_options: bool = Form(False),
    show_result_after_finish: bool = Form(True),
    show_correct_answers: bool = Form(False),
    is_formative: bool = Form(False),
    use_fuzzy_matching: bool = Form(False),
    allow_partial_grading: bool = Form(False),
    allow_retake: bool = Form(False),
    max_grade: int = Form(12),
    random_questions_limit: Optional[int] = Form(None),
    excluded_topics: Optional[str] = Form("[]"),
    questions_json: str = Form("[]"),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")

    old_folder_name = get_test_folder_name(test)

    try:
        questions_data = json.loads(questions_json)
    except json.JSONDecodeError:
        if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest":
            raise HTTPException(status_code=400, detail="Невірний формат питань (JSON)")
        test_json = json.dumps(_test_to_dict(test), ensure_ascii=False)
        return templates.TemplateResponse(
        request,
        "edit_test.html", {
            "teacher": teacher,
            "test": test,
            "test_json": test_json,
            "error": "Невірний формат питань (JSON)",
        })

    time_limit = int(time_limit_minutes) if time_limit_minutes and time_limit_minutes.strip() else None
    time_limit_q = int(time_limit_per_question) if time_limit_per_question and time_limit_per_question.strip() else None

    update_data = {
        "title": title,
        "subject": (subject.strip() if subject else None) or teacher.subject or None,
        "class_name": class_name or None,
        "description": description or None,
        "time_limit_minutes": time_limit,
        "time_limit_per_question": time_limit_q,
        "shuffle_questions": shuffle_questions,
        "shuffle_options": shuffle_options,
        "show_result_after_finish": show_result_after_finish,
        "show_correct_answers": show_correct_answers,
        "is_formative": is_formative,
        "use_fuzzy_matching": use_fuzzy_matching,
        "allow_partial_grading": allow_partial_grading,
        "allow_retake": allow_retake,
        "max_grade": max_grade,
        "random_questions_limit": random_questions_limit,
        "excluded_topics": excluded_topics,
        "questions": questions_data,
    }

    crud.update_test(db, test, update_data)
    
    new_folder_name = generate_new_folder_name(test)
    if old_folder_name != new_folder_name:
        rename_test_folder(test, old_folder_name, new_folder_name, db)
        test.folder_name = new_folder_name
        db.commit()
        
    save_test_locally(test)
    cleanup_unused_images(test)
    log_teacher_action(db, teacher.username, teacher.full_name, "edit_test", f"Редаговано тест '{test.title}' (ID: {test.id})", teacher.id)

    if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return {"status": "ok", "message": "Тест успішно збережено!"}

    return RedirectResponse(url="/teacher/tests", status_code=303)


# ---------------------------------------------------------------------------
# Import test from XML
# ---------------------------------------------------------------------------

@router.post("/tests/import/xml")
async def import_test_xml(
    request: Request,
    file: UploadFile = File(...),
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".xml"):
        raise HTTPException(status_code=400, detail="Файл має бути формату .xml")

    content = await file.read()
    try:
        xml_str = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            xml_str = content.decode("cp1251")
        except Exception:
            raise HTTPException(status_code=400, detail="Помилка кодування файлу")

    try:
        from app.services.import_export_service import import_test_from_mytestx_xml
        temp_session_id = f"import_xml_{uuid.uuid4().hex}"
        test = import_test_from_mytestx_xml(db, teacher.id, xml_str, temp_session_id=temp_session_id, fallback_title=file.filename)
        folder_name = getattr(test, '_folder_name', None) or get_test_folder_name(test)
        folder_path = os.path.join("app", "static", "tests", folder_name)
        log_teacher_action(db, teacher.username, teacher.full_name, "import_test_xml", f"Імпортовано тест з XML '{test.title}' (ID: {test.id})", teacher.id)
        return {
            "status": "ok",
            "test_id": test.id,
            "test_title": test.title,
            "folder_name": folder_name,
            "folder_path": folder_path,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Помилка імпорту: {str(e)}")


# ---------------------------------------------------------------------------
# Import test from Word (.docx)
# ---------------------------------------------------------------------------

@router.post("/tests/import/docx")
async def import_test_docx(
    request: Request,
    file: UploadFile = File(...),
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Файл має бути формату .docx")

    content = await file.read()

    try:
        from app.services.import_export_service import import_test_from_docx
        temp_session_id = f"import_docx_{uuid.uuid4().hex}"
        
        fallback_title = file.filename
        if fallback_title.lower().endswith(".docx"):
            fallback_title = fallback_title[:-5]
            
        test = import_test_from_docx(db, teacher.id, content, temp_session_id=temp_session_id, fallback_title=fallback_title)
        folder_name = getattr(test, '_folder_name', None) or get_test_folder_name(test)
        folder_path = os.path.join("app", "static", "tests", folder_name)
        log_teacher_action(db, teacher.username, teacher.full_name, "import_test_docx", f"Імпортовано тест з Word DOCX '{test.title}' (ID: {test.id})", teacher.id)
        return {
            "status": "ok",
            "test_id": test.id,
            "test_title": test.title,
            "folder_name": folder_name,
            "folder_path": folder_path,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Помилка імпорту: {str(e)}")


# ---------------------------------------------------------------------------
# Delete test
# ---------------------------------------------------------------------------

@router.post("/tests/{test_id}/delete")
async def delete_test(
    test_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")
    delete_test_locally(test)
    test_title = test.title
    crud.delete_test(db, test)
    log_teacher_action(db, teacher.username, teacher.full_name, "delete_test", f"Видалено тест '{test_title}' (ID: {test_id})", teacher.id)
    return RedirectResponse(url="/teacher/tests", status_code=303)


# ---------------------------------------------------------------------------
# Pin/Unpin session
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/pin")
async def pin_session(
    request: Request,
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
    
    if not session.is_active:
        raise HTTPException(status_code=400, detail="Можна закріпити лише активну сесію")

    # Unpin all other sessions for this teacher
    active_sessions = db.query(models.TestSession).join(models.Test).filter(
        models.Test.teacher_id == teacher.id,
        models.TestSession.is_active == True,
        models.TestSession.is_pinned == True
    ).all()
    
    for s in active_sessions:
        s.is_pinned = False
        
    session.is_pinned = True
    db.commit()
    
    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url="/teacher/sessions?tab=active", status_code=303)

@router.post("/sessions/{session_id}/unpin")
async def unpin_session(
    request: Request,
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
    
    session.is_pinned = False
    db.commit()
    
    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url="/teacher/sessions?tab=active", status_code=303)

# ---------------------------------------------------------------------------
# Start session
# ---------------------------------------------------------------------------

@router.post("/tests/{test_id}/start-session")
async def start_session(
    test_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")

    error = test_service.validate_test_for_launch(test)
    if error:
        return RedirectResponse(
            url=f"/teacher/tests/{test_id}/edit?launch_error={error}",
            status_code=303,
        )

    session = session_service.start_new_session(db, test_id=test_id)
    log_teacher_action(db, teacher.username, teacher.full_name, "start_session", f"Запущено сесію для тесту '{test.title}' (ID: {test_id}), код доступу: {session.access_code}", teacher.id)
    return RedirectResponse(url=f"/teacher/sessions/{session.id}/monitor", status_code=303)


# ---------------------------------------------------------------------------
# Sessions list
# ---------------------------------------------------------------------------

@router.get("/sessions", response_class=HTMLResponse)
async def sessions_list(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    tab: str = "active",
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    is_archived = (tab == "archive")
    sessions = crud.get_all_sessions(db, teacher.id, is_archived=is_archived)
    sessions_data = []
    for s in sessions:
        sessions_data.append({
            "session": s,
            "attempt_count": len(s.attempts),
            "finished_count": sum(
                1 for a in s.attempts
                if a.status in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped)
            ),
        })
        
    import math
    total_sessions = len(sessions_data)
    total_pages = 1
    if per_page > 0:
        total_pages = max(1, math.ceil(total_sessions / per_page))
        if page < 1: page = 1
        elif page > total_pages: page = total_pages
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_sessions = sessions_data[start_idx:end_idx]
    else:
        paginated_sessions = sessions_data

    return templates.TemplateResponse(
        request,
        "test_sessions.html", {
        "teacher": teacher,
        "sessions_data": paginated_sessions,
        "page": "sessions",
        "current_page": page,
        "total_pages": total_pages,
        "total_sessions": total_sessions,
        "per_page": per_page,
        "tab": tab,
    })


@router.post("/sessions/{session_id}/archive")
async def archive_session(
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
    if session.is_active:
        raise HTTPException(status_code=400, detail="Неможливо архівувати активну сесію")
        
    session.is_archived = True
    db.commit()
    return RedirectResponse(url="/teacher/sessions?tab=active", status_code=303)


@router.post("/sessions/{session_id}/unarchive")
async def unarchive_session(
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
        
    session.is_archived = False
    db.commit()
    return RedirectResponse(url="/teacher/sessions?tab=archive", status_code=303)


# ---------------------------------------------------------------------------
# Monitor session
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/monitor", response_class=HTMLResponse)
async def monitor_session(
    request: Request,
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")

    attempts = crud.get_attempts_by_session(db, session_id)
    return templates.TemplateResponse(
        request,
        "monitor_session.html", {
        "teacher": teacher,
        "session": session,
        "attempts": attempts,
        "test": session.test,
    })


@router.post("/sessions/{session_id}/close")
async def close_session(
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
    crud.close_session(db, session)
    log_teacher_action(db, teacher.username, teacher.full_name, "close_session", f"Закрито сесію (ID: {session_id}, Тест: '{session.test.title}')", teacher.id)
    return RedirectResponse(url="/teacher/sessions", status_code=303)


# ---------------------------------------------------------------------------
# Mass Control (Pause / Resume / Stop All)
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/pause-all")
async def pause_all_attempts(
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")

    attempts = crud.get_attempts_by_session(db, session_id)
    from app.services import event_log_service
    from app.websocket_manager import ws_manager

    count = 0
    for attempt in attempts:
        if attempt.status == models.AttemptStatus.in_progress:
            attempt.status = models.AttemptStatus.paused
            event_log_service.log_event(db, attempt.id, models.EventType.pause_test, "Призупинено для всіх")
            
            # Fire and forget websocket notifications
            import asyncio
            asyncio.create_task(ws_manager.send_to_student(attempt.id, {"event": "pause"}))
            count += 1
            
    if count > 0:
        db.commit()
        import asyncio
        asyncio.create_task(ws_manager.broadcast(session_id, {"event": "mass_pause"}))

    return {"status": "ok", "paused_count": count}


@router.post("/sessions/{session_id}/resume-all")
async def resume_all_attempts(
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")

    attempts = crud.get_attempts_by_session(db, session_id)
    from app.services import event_log_service
    from app.websocket_manager import ws_manager

    count = 0
    for attempt in attempts:
        if attempt.status == models.AttemptStatus.paused:
            attempt.status = models.AttemptStatus.in_progress
            event_log_service.log_event(db, attempt.id, models.EventType.resume_test, "Відновлено для всіх")
            
            import asyncio
            asyncio.create_task(ws_manager.send_to_student(attempt.id, {"event": "resume"}))
            count += 1
            
    if count > 0:
        db.commit()
        import asyncio
        asyncio.create_task(ws_manager.broadcast(session_id, {"event": "mass_resume"}))

    return {"status": "ok", "resumed_count": count}


@router.post("/sessions/{session_id}/stop-all")
async def stop_all_attempts(
    session_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    session = crud.get_session_by_id(db, session_id)
    if not session or session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")

    attempts = crud.get_attempts_by_session(db, session_id)
    from app.services import event_log_service, result_service, result_export_service, test_file_service
    from app.websocket_manager import ws_manager

    count = 0
    for attempt in attempts:
        if attempt.status in (models.AttemptStatus.in_progress, models.AttemptStatus.paused):
            score, max_score = result_service.grade_all_answers(db, attempt)
            crud.finish_attempt(db, attempt, score, max_score, models.AttemptStatus.stopped)
            event_log_service.log_event(db, attempt.id, models.EventType.stop_test, "Зупинено для всіх")
            
            import asyncio
            asyncio.create_task(ws_manager.send_to_student(attempt.id, {"event": "stop", "score": score, "max_score": max_score}))
            
            try:
                html_content = result_export_service.generate_result_html(db, attempt)
                test_file_service.save_student_result_html(attempt.session.test, attempt, html_content)
            except Exception as e:
                print(f"Error generating mass HTML export: {e}")
                
            count += 1

    if count > 0:
        import asyncio
        asyncio.create_task(ws_manager.broadcast(session_id, {"event": "mass_stop"}))

    return {"status": "ok", "stopped_count": count}

# ---------------------------------------------------------------------------
# Individual Attempt Control (Pause / Resume / Stop)
# ---------------------------------------------------------------------------

@router.post("/attempts/{attempt_id}/pause")
async def pause_attempt(
    attempt_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt or attempt.session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    if attempt.status != models.AttemptStatus.in_progress:
        raise HTTPException(status_code=400, detail="Спроба не в процесі тестування")

    attempt.status = models.AttemptStatus.paused
    db.commit()

    from app.services import event_log_service
    event_log_service.log_event(db, attempt.id, models.EventType.pause_test, "Призупинено вчителем")

    # Notify student
    from app.websocket_manager import ws_manager
    await ws_manager.send_to_student(attempt_id, {"event": "pause"})

    # Broadcast to teachers
    await ws_manager.broadcast(attempt.session_id, {
        "event": "pause",
        "student_name": attempt.student_name,
        "attempt_id": attempt_id,
        "status": "paused",
    })

    return {"status": "ok"}


@router.post("/attempts/{attempt_id}/resume")
async def resume_attempt(
    attempt_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt or attempt.session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    if attempt.status != models.AttemptStatus.paused:
        raise HTTPException(status_code=400, detail="Спроба не на паузі")

    attempt.status = models.AttemptStatus.in_progress
    db.commit()

    from app.services import event_log_service
    event_log_service.log_event(db, attempt.id, models.EventType.resume_test, "Відновлено вчителем")

    # Notify student
    from app.websocket_manager import ws_manager
    await ws_manager.send_to_student(attempt_id, {"event": "resume"})

    # Broadcast to teachers
    await ws_manager.broadcast(attempt.session_id, {
        "event": "resume",
        "student_name": attempt.student_name,
        "attempt_id": attempt_id,
        "status": "in_progress",
    })

    return {"status": "ok"}


@router.post("/attempts/{attempt_id}/stop")
async def stop_attempt(
    attempt_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt or attempt.session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")

    if attempt.status not in (models.AttemptStatus.in_progress, models.AttemptStatus.paused):
        raise HTTPException(status_code=400, detail="Спробу неможливо зупинити")

    from app.services import result_service
    from datetime import datetime
    score, max_score = result_service.grade_all_answers(db, attempt)

    attempt.status = models.AttemptStatus.stopped
    attempt.finished_at = datetime.now()
    attempt.score = score
    attempt.max_score = max_score
    db.commit()

    from app.services import event_log_service
    event_log_service.log_event(db, attempt.id, models.EventType.stop_test, f"Зупинено вчителем. Балів: {score}/{max_score}")

    # Notify student
    from app.websocket_manager import ws_manager
    await ws_manager.send_to_student(attempt_id, {"event": "stop"})

    # Broadcast to teachers
    await ws_manager.broadcast(attempt.session_id, {
        "event": "stop",
        "student_name": attempt.student_name,
        "attempt_id": attempt_id,
        "status": "stopped",
        "score": score,
        "max_score": max_score,
    })

    return {"status": "ok", "score": score, "max_score": max_score}



# ---------------------------------------------------------------------------
# Session Analytics
# ---------------------------------------------------------------------------

@router.get("/tests/{test_id}/analytics", response_class=HTMLResponse)
async def test_analytics(
    request: Request,
    test_id: int,
    session_id: Optional[str] = None,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")
        
    # Збираємо завершені спроби для цього тесту
    selected_session_id_int = None
    if session_id and session_id.strip() and session_id.isdigit():
        selected_session_id_int = int(session_id)
        sessions = [s for s in test.sessions if s.id == selected_session_id_int]
    else:
        sessions = test.sessions
        
    attempts = []
    for s in sessions:
        attempts.extend([a for a in s.attempts if a.status == models.AttemptStatus.finished])
        
    total_attempts = len(attempts)
    
    excluded = []
    if test.excluded_topics:
        try:
            import json
            excluded = json.loads(test.excluded_topics)
        except Exception:
            pass
    
    questions_stats = []
    for idx, q in enumerate(test.questions, start=1):
        if (q.topic or "") in excluded:
            continue
            
        q_answers = [ans for a in attempts for ans in a.answers if ans.question_id == q.id]
        answered_count = len(q_answers)
        
        # Skip questions that were never asked
        if answered_count == 0:
            continue
            
        correct_count = sum(1 for ans in q_answers if ans.is_correct)
        correct_pct = (correct_count / answered_count) * 100
        avg_points = sum((ans.awarded_points or 0.0) for ans in q_answers) / answered_count
            
        options_dist = []
        if q.question_type in (models.QuestionType.single_choice, models.QuestionType.multiple_choice, models.QuestionType.image_choice, models.QuestionType.true_false):
            for opt in q.options:
                count = 0
                for ans in q_answers:
                    if ans.selected_options_json:
                        try:
                            sel_ids = json.loads(ans.selected_options_json)
                            if opt.id in sel_ids:
                                count += 1
                        except: pass
                
                text_to_show = opt.option_text
                if not text_to_show and opt.image_url:
                    text_to_show = "[Зображення]"
                    
                options_dist.append({
                    "text": text_to_show,
                    "is_correct": opt.is_correct,
                    "count": count
                })
        elif q.question_type == models.QuestionType.short_text:
            text_counts = {}
            for ans in q_answers:
                t = (ans.answer_text or "").strip()
                if t:
                    key = t.lower()
                    if key not in text_counts:
                        text_counts[key] = {"text": t, "count": 0, "is_correct": ans.is_correct}
                    text_counts[key]["count"] += 1
                    # If any of the answers for this key was marked correct, keep it correct
                    if ans.is_correct:
                        text_counts[key]["is_correct"] = True
                        
            top_texts = sorted(text_counts.values(), key=lambda x: x["count"], reverse=True)[:5]
            options_dist = top_texts
            
        questions_stats.append({
            "index": idx,
            "question": q,
            "answered_count": answered_count,
            "correct_count": correct_count,
            "correct_pct": correct_pct,
            "average_points": avg_points,
            "options_dist": options_dist
        })
        
    avg_score = sum(a.score for a in attempts if a.score is not None) / total_attempts if total_attempts > 0 else 0
    
    # Calculate average max_score to handle cases with random subsets of questions
    avg_max_score = sum(a.max_score for a in attempts if a.max_score is not None) / total_attempts if total_attempts > 0 else 0
    avg_pct = (avg_score / avg_max_score * 100) if avg_max_score > 0 else 0

    return templates.TemplateResponse(
        request,
        "session_analytics.html", {
        "teacher": teacher,
        "test": test,
        "all_sessions": test.sessions,
        "selected_session_id": selected_session_id_int,
        "total_attempts": total_attempts,
        "questions_stats": questions_stats,
        "avg_score": avg_score,
        "max_score": avg_max_score,
        "avg_pct": avg_pct,
    })


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@router.get("/results", response_class=HTMLResponse)
async def results_list(
    request: Request,
    page: int = 1,
    session_id: Optional[str] = None,
    class_name: Optional[str] = None,
    student_name: Optional[str] = None,
    tab: str = "active",
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    is_archived = (tab == "archive")
    all_attempts = crud.get_all_finished_attempts(db, teacher.id, is_archived=is_archived)
    sessions = crud.get_all_sessions(db, teacher.id)
    tests = crud.get_tests_by_teacher(db, teacher.id)

    # Класи беремо з профілю вчителя
    filter_classes = [c.strip() for c in teacher.classes.split(",") if c.strip()] if teacher.classes else []
    if class_name and class_name not in filter_classes:
        filter_classes.append(class_name)

    # Унікальні імена учнів для автокомпліту
    student_names = sorted(list({a.student_name for a in all_attempts if a.student_name}))

    attempts = all_attempts
    if session_id and session_id.isdigit():
        attempts = [a for a in attempts if a.session_id == int(session_id)]
    if class_name:
        attempts = [a for a in attempts if a.session.test.class_name == class_name]
    if student_name and student_name.strip():
        s_name_lower = student_name.strip().lower()
        attempts = [a for a in attempts if s_name_lower in a.student_name.lower()]

    # Пагінація
    import math
    page_size = 20
    total_attempts = len(attempts)
    total_pages = max(1, math.ceil(total_attempts / page_size))
    
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_attempts = attempts[start_idx:end_idx]

    return templates.TemplateResponse(
        request,
        "results.html", {
        "teacher": teacher,
        "attempts": paginated_attempts,
        "sessions": sessions,
        "filter_classes": filter_classes,
        "student_names": student_names,
        "tab": tab,
        "selected_session_id": int(session_id) if session_id and session_id.isdigit() else None,
        "selected_class_name": class_name,
        "selected_student_name": student_name,
        "page": page,
        "total_pages": total_pages,
        "total_attempts": total_attempts,
    })

# ---------------------------------------------------------------------------
# Tests Archiving
# ---------------------------------------------------------------------------

@router.post("/tests/{test_id}/archive")
async def archive_test(
    test_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")
    test.is_archived = True
    db.commit()
    return RedirectResponse(url="/teacher/tests", status_code=303)


@router.post("/tests/{test_id}/unarchive")
async def unarchive_test(
    test_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")
    test.is_archived = False
    db.commit()
    return RedirectResponse(url="/teacher/tests?tab=archive", status_code=303)


@router.post("/tests/archive-bulk")
async def archive_tests_bulk(
    start_date: str = Form(...),
    end_date: str = Form(...),
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    from datetime import datetime, time
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        ed = datetime.combine(ed, time.max)
    except Exception:
        raise HTTPException(status_code=400, detail="Невірний формат дати")

    tests = db.query(models.Test).filter(
        models.Test.teacher_id == teacher.id,
        models.Test.created_at >= sd,
        models.Test.created_at <= ed,
        models.Test.is_archived == False
    ).all()

    for t in tests:
        t.is_archived = True
    db.commit()
    return RedirectResponse(url="/teacher/tests", status_code=303)


# ---------------------------------------------------------------------------
# Results Archiving
# ---------------------------------------------------------------------------

@router.post("/results/{attempt_id}/archive")
async def archive_result(
    attempt_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    attempt = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.StudentAttempt.id == attempt_id,
        models.Test.teacher_id == teacher.id
    ).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Результат не знайдено")
    attempt.is_archived = True
    db.commit()
    log_teacher_action(db, teacher.username, teacher.full_name, "archive_result", f"Архівовано результат спроби учня '{attempt.student_name}' (ID: {attempt_id})", teacher.id)
    return RedirectResponse(url="/teacher/results", status_code=303)


@router.post("/results/{attempt_id}/unarchive")
async def unarchive_result(
    attempt_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    attempt = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.StudentAttempt.id == attempt_id,
        models.Test.teacher_id == teacher.id
    ).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Результат не знайдено")
    attempt.is_archived = False
    db.commit()
    return RedirectResponse(url="/teacher/results?tab=archive", status_code=303)


@router.post("/results/archive-bulk")
async def archive_results_bulk(
    start_date: str = Form(...),
    end_date: str = Form(...),
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    from datetime import datetime, time
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        ed = datetime.combine(ed, time.max)
    except Exception:
        raise HTTPException(status_code=400, detail="Невірний формат дати")

    attempts = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.Test.teacher_id == teacher.id,
        models.StudentAttempt.finished_at >= sd,
        models.StudentAttempt.finished_at <= ed,
        models.StudentAttempt.is_archived == False
    ).all()

    for a in attempts:
        a.is_archived = True
    db.commit()
    return RedirectResponse(url="/teacher/results", status_code=303)


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

@router.get("/results/export", response_class=StreamingResponse)
async def export_csv(
    session_id: Optional[str] = None,
    class_name: Optional[str] = None,
    student_name: Optional[str] = None,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    all_attempts = crud.get_all_finished_attempts(db, teacher.id)
    
    attempts = all_attempts
    if session_id and session_id.strip() and session_id.isdigit():
        attempts = [a for a in attempts if a.session_id == int(session_id)]
        
    if class_name and class_name.strip():
        attempts = [a for a in attempts if a.session and a.session.test and a.session.test.class_name == class_name]

    if student_name and student_name.strip():
        s_name_lower = student_name.strip().lower()
        attempts = [a for a in attempts if s_name_lower in a.student_name.lower()]

    csv_content = import_export_service.export_results_to_csv(attempts)

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=results.csv"},
    )


# ---------------------------------------------------------------------------
# Result Detail
# ---------------------------------------------------------------------------

@router.get("/results/{attempt_id}", response_class=HTMLResponse)
async def result_detail(
    request: Request,
    attempt_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    attempt = crud.get_attempt_by_id(db, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Спробу не знайдено")
    if attempt.session.test.teacher_id != teacher.id:
        raise HTTPException(status_code=403, detail="Доступ заборонено")

    answers = crud.get_answers_by_attempt(db, attempt_id)
    answers = sorted(answers, key=lambda a: a.question.order_index if a.question else 0)
    event_logs = crud.get_event_logs_by_attempt(db, attempt_id)
    grouped_logs = []
    for log in event_logs:
        if grouped_logs and grouped_logs[-1].event_type == log.event_type and grouped_logs[-1].details == log.details:
            grouped_logs[-1].count = getattr(grouped_logs[-1], 'count', 1) + 1
            grouped_logs[-1].event_time = log.event_time
        else:
            log.count = 1
            grouped_logs.append(log)
    event_logs = grouped_logs
    
    test = attempt.session.test

    answers_map = {a.question_id: a for a in answers}
    question_map = {q.id: q.question_text for q in test.questions}

    return templates.TemplateResponse(
        request,
        "result_detail.html", {
        "teacher": teacher,
        "attempt": attempt,
        "test": test,
        "answers": answers,
        "answers_map": answers_map,
        "question_map": question_map,
        "event_logs": event_logs,
    })


# ---------------------------------------------------------------------------
# School Journal
# ---------------------------------------------------------------------------

@router.get("/journal", response_class=HTMLResponse)
async def school_journal(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    class_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    # Retrieve all unique class names for this teacher
    filter_classes = [
        c[0] for c in db.query(models.Test.class_name)
        .filter(models.Test.teacher_id == teacher.id, models.Test.class_name != None)
        .distinct()
        .order_by(models.Test.class_name)
    ]

    # Query finished attempts
    query = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.Test.teacher_id == teacher.id,
        models.StudentAttempt.status.in_([
            models.AttemptStatus.finished,
            models.AttemptStatus.timeout,
            models.AttemptStatus.stopped
        ])
    )

    if class_name:
        query = query.filter(models.Test.class_name == class_name)

    from datetime import datetime
    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(models.TestSession.started_at >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(models.TestSession.started_at <= dt.replace(hour=23, minute=59, second=59))
        except ValueError:
            pass

    attempts = query.all()

    from collections import defaultdict
    sessions_map = {}
    students_map = defaultdict(dict)

    for a in attempts:
        sid = a.session_id
        if sid not in sessions_map:
            sessions_map[sid] = {
                'id': sid,
                'test_title': a.session.test.title,
                'date': a.session.started_at,
                'max_grade': a.session.test.max_grade or 12
            }
        
        grade = None
        if a.score is not None and a.max_score and a.max_score > 0:
            grade = round((a.score / a.max_score) * sessions_map[sid]['max_grade'])
        elif a.score is not None:
            grade = 0
            
        student_name = a.student_name
        if sid in students_map[student_name]:
            if grade is not None and (students_map[student_name][sid] is None or grade > students_map[student_name][sid]):
                students_map[student_name][sid] = grade
        else:
            students_map[student_name][sid] = grade

    # Sort columns by date (oldest first)
    columns = sorted(sessions_map.values(), key=lambda x: x['date'] if x['date'] else datetime.min)
    
    # Sort students alphabetically
    students = sorted(students_map.keys())
    
    journal_data = []
    for s in students:
        row = {'name': s, 'grades': {}}
        for col in columns:
            row['grades'][col['id']] = students_map[s].get(col['id'])
        journal_data.append(row)

    return templates.TemplateResponse(
        request,
        "journal.html", {
        "teacher": teacher,
        "filter_classes": filter_classes,
        "selected_class_name": class_name,
        "date_from": date_from,
        "date_to": date_to,
        "columns": columns,
        "journal_data": journal_data,
    })


@router.get("/journal/export", response_class=StreamingResponse)
async def school_journal_export(
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    class_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    query = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.Test.teacher_id == teacher.id,
        models.StudentAttempt.status.in_([
            models.AttemptStatus.finished,
            models.AttemptStatus.timeout,
            models.AttemptStatus.stopped
        ])
    )

    if class_name:
        query = query.filter(models.Test.class_name == class_name)

    from datetime import datetime
    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(models.TestSession.started_at >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(models.TestSession.started_at <= dt.replace(hour=23, minute=59, second=59))
        except ValueError:
            pass

    attempts = query.all()

    from collections import defaultdict
    sessions_map = {}
    students_map = defaultdict(dict)

    for a in attempts:
        sid = a.session_id
        if sid not in sessions_map:
            sessions_map[sid] = {
                'id': sid,
                'test_title': a.session.test.title,
                'date': a.session.started_at,
                'max_grade': a.session.test.max_grade or 12
            }
        
        grade = None
        if a.score is not None and a.max_score and a.max_score > 0:
            grade = round((a.score / a.max_score) * sessions_map[sid]['max_grade'])
        elif a.score is not None:
            grade = 0
            
        student_name = a.student_name
        if sid in students_map[student_name]:
            if grade is not None and (students_map[student_name][sid] is None or grade > students_map[student_name][sid]):
                students_map[student_name][sid] = grade
        else:
            students_map[student_name][sid] = grade

    columns = sorted(sessions_map.values(), key=lambda x: x['date'] if x['date'] else datetime.min)
    students = sorted(students_map.keys())

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    
    header = ["Учень"] + [f"{col['test_title']} ({col['date'].strftime('%d.%m.%Y') if col['date'] else ''})" for col in columns]
    writer.writerow(header)
    
    for s in students:
        row = [s]
        for col in columns:
            val = students_map[s].get(col['id'])
            row.append(str(val) if val is not None else "")
        writer.writerow(row)
        
    output.seek(0)
    
    import urllib.parse
    filename = "journal_export.csv"
    if class_name:
        filename = f"journal_{class_name}.csv"
        
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
    )


# ---------------------------------------------------------------------------
# Import test from JSON
# ---------------------------------------------------------------------------

@router.get("/tests/import", response_class=HTMLResponse)
async def import_test_page(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
):
    return templates.TemplateResponse(
        request,
        "create_test.html", {
        "teacher": teacher,
        "import_mode": True,
    })


@router.post("/tests/import-json")
async def import_test_json(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    json_file: UploadFile = File(None),
    json_text: str = Form(""),
):
    if json_file and json_file.filename:
        raw = await json_file.read()
        json_data = raw.decode("utf-8")
    elif json_text.strip():
        json_data = json_text
    else:
        return templates.TemplateResponse(
        request,
        "create_test.html", {
            "teacher": teacher,
            "import_mode": True,
            "error": "Завантажте файл або вставте JSON у текстове поле",
        })

    try:
        test = import_export_service.import_test_from_json(db, teacher.id, json_data)
    except ValueError as exc:
        return templates.TemplateResponse(
        request,
        "create_test.html", {
            "teacher": teacher,
            "import_mode": True,
            "error": str(exc),
        })

    log_teacher_action(db, teacher.username, teacher.full_name, "import_test_json", f"Імпортовано тест з JSON '{test.title}' (ID: {test.id})", teacher.id)
    return RedirectResponse(url=f"/teacher/tests/{test.id}/edit?created=1", status_code=303)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _test_to_dict(test: models.Test) -> dict:
    return {
        "id": test.id,
        "title": test.title,
        "subject": test.subject,
        "class_name": test.class_name,
        "description": test.description,
        "time_limit_minutes": test.time_limit_minutes,
        "time_limit_per_question": test.time_limit_per_question,
        "shuffle_questions": test.shuffle_questions,
        "shuffle_options": test.shuffle_options,
        "show_result_after_finish": test.show_result_after_finish,
        "show_correct_answers": test.show_correct_answers,
        "is_formative": test.is_formative,
        "allow_partial_grading": test.allow_partial_grading,
        "allow_retake": test.allow_retake,
        "max_grade": test.max_grade,
        "random_questions_limit": test.random_questions_limit,
        "excluded_topics": test.excluded_topics or "[]",
        "questions": [
            {
                "id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type.value,
                "points": q.points,
                "topic": q.topic,
                "difficulty": q.difficulty.value if q.difficulty else "medium",
                "explanation": q.explanation,
                "order_index": q.order_index,
                "image_url": q.image_url,
                "options": [
                    {
                        "id": o.id,
                        "option_text": o.option_text,
                        "is_correct": o.is_correct,
                        "order_index": o.order_index,
                        "image_url": o.image_url,
                        "matching_text": o.matching_text,
                    }
                    for o in q.options
                ],
            }
            for q in test.questions
        ],
    }


# ---------------------------------------------------------------------------
# Export test to JSON
# ---------------------------------------------------------------------------

@router.get("/tests/{test_id}/export")
async def export_test_json(
    test_id: int,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    test = crud.get_test_by_id(db, test_id)
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Тест не знайдено")
        
    test_dict = _test_to_dict(test)
    
    import base64
    import os
    embedded_images = {}
    
    def add_image_to_embedded(url):
        if not url: return
        local_path = os.path.join("app", url.lstrip("/"))
        if os.path.exists(local_path) and os.path.isfile(local_path):
            filename = os.path.basename(local_path)
            if filename not in embedded_images:
                try:
                    with open(local_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode('utf-8')
                        embedded_images[filename] = b64
                except Exception:
                    pass
    
    for q in test_dict.get("questions", []):
        add_image_to_embedded(q.get("image_url"))
        for o in q.get("options", []):
            add_image_to_embedded(o.get("image_url"))
            
    if embedded_images:
        test_dict["embedded_images"] = embedded_images

    json_data = json.dumps(test_dict, ensure_ascii=False, indent=2)
    
    import urllib.parse
    filename = f"test_{test_id}_export.json"
    encoded_filename = urllib.parse.quote(filename)
    
    return StreamingResponse(
        iter([json_data]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
    )

# ---------------------------------------------------------------------------
# Upload Image
# ---------------------------------------------------------------------------

@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    test_id: Optional[int] = None,
    session_id: Optional[str] = None,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не вибрано")
    
    ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    
    if test_id:
        test = crud.get_test_by_id(db, test_id)
        if not test:
            raise HTTPException(status_code=404, detail="Тест не знайдено")
        folder_name = get_test_folder_name(test)
        upload_dir = os.path.join("app", "static", "tests", folder_name)
        url_path = f"/static/tests/{folder_name}/{unique_filename}"
    elif session_id:
        if not re.match(r"^[a-zA-Z0-9_\-]+$", session_id):
            raise HTTPException(status_code=400, detail="Невірний формат сесії завантаження")
        upload_dir = os.path.join("app", "static", "tests", "temp", session_id)
        url_path = f"/static/tests/temp/{session_id}/{unique_filename}"
    else:
        upload_dir = os.path.join("app", "static", "uploads")
        url_path = f"/static/uploads/{unique_filename}"
        
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, unique_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"url": url_path}


# ---------------------------------------------------------------------------
# Teacher Profile
# ---------------------------------------------------------------------------

@router.get("/profile", response_class=HTMLResponse)
async def get_profile(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    all_teachers = db.query(models.Teacher).filter(models.Teacher.is_active == True).all()
    return templates.TemplateResponse(
        request,
        "profile.html", {
        "teacher": teacher,
        "all_teachers": all_teachers,
    })


@router.post("/profile")
async def update_profile(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    full_name: str = Form(...),
    subject: str = Form(""),
    classes: str = Form(""),
):
    teacher.full_name = full_name
    teacher.subject = subject.strip() if subject.strip() else None
    teacher.classes = classes.strip() if classes.strip() else None
    db.commit()
    db.refresh(teacher)
    log_teacher_action(db, teacher.username, teacher.full_name, "edit_profile", f"Оновлено профіль. Ім'я: '{full_name}', Предмет: '{subject}', Класи: '{classes}'", teacher.id)
    
    if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return {"status": "ok", "message": "Профіль успішно оновлено!"}

    all_teachers = db.query(models.Teacher).filter(models.Teacher.is_active == True).all()
    return templates.TemplateResponse(
        request,
        "profile.html", {
        "teacher": teacher,
        "all_teachers": all_teachers,
        "success": "Профіль успішно оновлено!",
    })


@router.post("/profile/create-teacher")
async def create_teacher_profile(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    username: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
):
    if teacher.username != "admin":
        return {"status": "error", "message": "Створення нових профілів дозволено тільки адміністратору"}
        
    existing = db.query(models.Teacher).filter(models.Teacher.username == username).first()
    if existing:
        return {"status": "error", "message": "Користувач з таким логіном вже існує"}
        
    try:
        from app.crud import create_teacher
        new_teacher = create_teacher(db, username=username, full_name=full_name, password=password)
        log_teacher_action(db, teacher.username, teacher.full_name, "create_teacher", f"Створено профіль вчителя: @{username} ({full_name})", teacher.id)
        return {"status": "ok", "message": "Новий профіль вчителя успішно створено!"}
    except Exception as e:
        return {"status": "error", "message": f"Помилка створення: {str(e)}"}


@router.post("/profile/switch-teacher")
async def switch_teacher_profile(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    from app.security import verify_password
    teacher = db.query(models.Teacher).filter(models.Teacher.username == username).first()
    
    if not teacher or not verify_password(password, teacher.hashed_password):
        log_teacher_action(db, username, "Невідомий користувач", "switch_teacher_failed", f"Спроба перемикання на профіль: '{username}'", None)
        return {"status": "error", "message": "Невірний логін або пароль"}
        
    from fastapi.responses import JSONResponse
    response = JSONResponse({"status": "ok", "message": "Перемикання успішне!"})
    from app.security import create_teacher_session
    create_teacher_session(response, teacher.id, teacher.username)
    
    # Log successful profile switch
    log_teacher_action(db, teacher.username, teacher.full_name, "switch_teacher", "Перемикання профілю", teacher.id)
    
    # Auto-archive check if switched to admin
    if teacher.username == "admin":
        check_and_create_archive(db, password)
        
    return response


@router.get("/api/list-teachers")
async def api_list_teachers(
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db)
):
    all_teachers = db.query(models.Teacher).filter(models.Teacher.is_active == True).all()
    teachers_data = [
        {"id": t.id, "username": t.username, "full_name": t.full_name}
        for t in all_teachers if t.id != teacher.id
    ]
    return {"teachers": teachers_data}


@router.post("/profile/delete-teacher")
async def delete_teacher_profile(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    username: str = Form(...),
):
    if teacher.username != "admin":
        return {"status": "error", "message": "Лише адміністратор може видаляти користувачів."}
        
    if username == "admin":
        return {"status": "error", "message": "Неможливо видалити основного адміністратора."}
        
    target_teacher = db.query(models.Teacher).filter(models.Teacher.username == username).first()
    if not target_teacher:
        return {"status": "error", "message": "Вчителя не знайдено."}
        
    try:
        # Delete tests manually to avoid foreign key constraints if cascade isn't set up
        tests = db.query(models.Test).filter(models.Test.teacher_id == target_teacher.id).all()
        for t in tests:
            db.delete(t)
        
        db.delete(target_teacher)
        db.commit()
        log_teacher_action(db, teacher.username, teacher.full_name, "delete_teacher", f"Видалено профіль вчителя: @{username}", teacher.id)
        return {"status": "ok", "message": f"Вчителя @{username} успішно видалено."}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": f"Помилка видалення: {str(e)}"}


@router.get("/profile/archive-teacher/{username}")
async def archive_teacher_profile(
    username: str,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    if teacher.username != "admin":
        raise HTTPException(status_code=403, detail="Тільки адміністратор може завантажувати архів іншого вчителя")
        
    target_teacher = db.query(models.Teacher).filter(models.Teacher.username == username).first()
    if not target_teacher:
        raise HTTPException(status_code=404, detail="Вчителя не знайдено")
        
    tests = db.query(models.Test).filter(models.Test.teacher_id == target_teacher.id).all()
    
    import io
    import zipfile
    import json
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Add all tests as JSON files
        for t in tests:
            test_dict = _test_to_dict(t)
            test_json = json.dumps(test_dict, ensure_ascii=False, indent=2)
            safe_title = "".join([c for c in t.title if c.isalpha() or c.isdigit() or c == ' ']).rstrip()
            if not safe_title:
                safe_title = f"test_{t.id}"
            filename = f"tests/{safe_title}_{t.id}.json"
            zip_file.writestr(filename, test_json.encode('utf-8'))
            
        # Add basic info about the teacher
        info = {
            "username": target_teacher.username,
            "full_name": target_teacher.full_name,
            "subject": target_teacher.subject,
            "classes": target_teacher.classes,
            "tests_count": len(tests)
        }
        zip_file.writestr("teacher_info.json", json.dumps(info, ensure_ascii=False, indent=2).encode('utf-8'))
        
    zip_buffer.seek(0)
    log_teacher_action(db, teacher.username, teacher.full_name, "archive_teacher", f"Завантажено архів вчителя: @{username}", teacher.id)
    
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=archive_{username}.zip"}
    )


@router.post("/profile/change-teacher-password")
async def change_teacher_password(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    username: str = Form(...),
    new_password: str = Form(...),
):
    if teacher.username != "admin":
        return {"status": "error", "message": "Лише адміністратор може змінювати паролі іншим вчителям."}
        
    if len(new_password) < 4:
        return {"status": "error", "message": "Пароль має бути не менше 4 символів."}
        
    target_teacher = db.query(models.Teacher).filter(models.Teacher.username == username).first()
    if not target_teacher:
        return {"status": "error", "message": "Вчителя не знайдено."}
        
    from app.security import get_password_hash
    target_teacher.hashed_password = get_password_hash(new_password)
    db.commit()
    
    # Log password change action
    log_teacher_action(
        db,
        teacher.username,
        teacher.full_name,
        "reset_teacher_password",
        f"Змінено пароль для вчителя: @{username}",
        teacher.id
    )
    
    return {"status": "ok", "message": f"Пароль для вчителя @{username} успішно змінено."}


@router.get("/logs", response_class=HTMLResponse)
async def view_teacher_logs(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    page: int = 1,
    limit: int = 50,
):
    if teacher.username != "admin":
        raise HTTPException(status_code=403, detail="Доступ заборонено. Лише адміністратор може переглядати логи подій.")
        
    # Get total count
    total_count = db.query(models.TeacherLog).count()
    
    # Paginate logs
    logs = (
        db.query(models.TeacherLog)
        .order_by(models.TeacherLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    
    # Calculate pages
    total_pages = (total_count + limit - 1) // limit
    
    # Get last archive status
    last_archive_str = "Ніколи"
    import os, time
    from datetime import datetime
    LAST_ARCHIVE_FILE = "data/last_archive_time.txt"
    if os.path.exists(LAST_ARCHIVE_FILE):
        try:
            with open(LAST_ARCHIVE_FILE, "r") as f:
                last_time = float(f.read().strip())
                last_archive_str = datetime.fromtimestamp(last_time).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
            
    # List existing archives
    archives_list = []
    ARCHIVE_DIR = "data/archives"
    if os.path.exists(ARCHIVE_DIR):
        try:
            for f in os.listdir(ARCHIVE_DIR):
                if f.endswith(".zip"):
                    path = os.path.join(ARCHIVE_DIR, f)
                    stat = os.stat(path)
                    archives_list.append({
                        "filename": f,
                        "size": stat.st_size,
                        "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    })
            archives_list.sort(key=lambda x: x["filename"], reverse=True)
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "teacher": teacher,
            "logs": logs,
            "page": page,
            "total_pages": total_pages,
            "last_archive_time": last_archive_str,
            "archives": archives_list,
        }
    )


@router.post("/logs/archive")
async def manual_archive_logs(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
    password: str = Form(...),
):
    if teacher.username != "admin":
        return {"status": "error", "message": "Лише адміністратор може архівувати логи."}
        
    from app.security import verify_password
    if not verify_password(password, teacher.hashed_password):
        return {"status": "error", "message": "Невірний пароль адміністратора."}
        
    success, msg = check_and_create_archive(db, password, force=True)
    if success:
        return {"status": "ok", "message": msg}
    else:
        return {"status": "error", "message": msg}


@router.get("/logs/download/{filename}")
async def download_archive(
    filename: str,
    teacher: models.Teacher = Depends(get_current_teacher),
):
    if teacher.username != "admin":
        raise HTTPException(status_code=403, detail="Лише адміністратор може завантажувати архіви.")
        
    import os
    archive_path = os.path.join("data/archives", filename)
    if not os.path.exists(archive_path) or not filename.endswith(".zip"):
        raise HTTPException(status_code=404, detail="Файл не знайдено.")
        
    from fastapi.responses import FileResponse
    return FileResponse(archive_path, media_type="application/zip", filename=filename)


@router.post("/logs/view-archive")
async def view_archive(
    teacher: models.Teacher = Depends(get_current_teacher),
    archive_file: Optional[UploadFile] = File(None),
    archive_filename: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
):
    if teacher.username != "admin":
        raise HTTPException(status_code=403, detail="Доступ заборонено. Лише адміністратор може переглядати логи подій.")

    import zipfile
    import json
    import io

    content = None
    source_name = ""

    if archive_file and archive_file.filename:
        source_name = archive_file.filename
        raw_data = await archive_file.read()
        if archive_file.filename.endswith(".zip"):
            if not password:
                return {"status": "error", "message": "Для відкриття ZIP-архіву потрібен пароль."}
            try:
                zip_buffer = io.BytesIO(raw_data)
                with zipfile.ZipFile(zip_buffer) as zf:
                    json_files = [name for name in zf.namelist() if name.endswith(".json")]
                    if not json_files:
                        return {"status": "error", "message": "В ZIP-архіві не знайдено JSON-файлів з логами."}
                    json_name = json_files[0]
                    try:
                        content = zf.read(json_name, pwd=password.encode('utf-8'))
                    except RuntimeError:
                        # Fallback for encoding
                        content = zf.read(json_name, pwd=password.encode('cp437'))
            except (zipfile.BadZipFile, RuntimeError):
                return {"status": "error", "message": "Невірний пароль або пошкоджений архів."}
        elif archive_file.filename.endswith(".json"):
            content = raw_data
        else:
            return {"status": "error", "message": "Підтримуються тільки файли .zip та .json."}
            
    elif archive_filename:
        source_name = archive_filename
        if not password:
            return {"status": "error", "message": "Вкажіть пароль для дешифрування."}
        import os
        archive_path = os.path.join("data/archives", archive_filename)
        if not os.path.exists(archive_path) or not archive_filename.endswith(".zip"):
            return {"status": "error", "message": "Архів не знайдено на сервері."}
            
        try:
            with zipfile.ZipFile(archive_path) as zf:
                json_files = [name for name in zf.namelist() if name.endswith(".json")]
                if not json_files:
                    return {"status": "error", "message": "В архіві не знайдено JSON-файлів з логами."}
                json_name = json_files[0]
                try:
                    content = zf.read(json_name, pwd=password.encode('utf-8'))
                except RuntimeError:
                    content = zf.read(json_name, pwd=password.encode('cp437'))
        except (zipfile.BadZipFile, RuntimeError):
            return {"status": "error", "message": "Невірний пароль або пошкоджений архів."}
    else:
        return {"status": "error", "message": "Не надано файл для перегляду."}

    # Parse JSON
    try:
        logs_list = json.loads(content.decode("utf-8"))
        if not isinstance(logs_list, list):
            return {"status": "error", "message": "Невірний формат даних логів. Очікувався список."}
    except Exception as e:
        return {"status": "error", "message": f"Помилка розпізнавання JSON: {str(e)}"}

    return {"status": "ok", "filename": source_name, "logs": logs_list}


