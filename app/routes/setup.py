import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, models
from app.config import settings
from app.database import get_db
from app.security import create_teacher_session
from app.services.logger_service import log_teacher_action
from app.templating import templates

router = APIRouter(tags=["setup"])


@router.get("/setup", response_class=HTMLResponse)
@router.get("/setup/", response_class=HTMLResponse)
async def setup_page(request: Request, db: Session = Depends(get_db)):
    """
    Майстер початкового налаштування.
    Доступний виключно тоді, коли в базі даних немає жодного користувача.
    """
    if crud.count_teachers(db) > 0:
        return RedirectResponse(url="/teacher/login", status_code=303)

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "full_name": "Адміністратор",
            "username": "admin",
            "subject": "",
            "classes": "",
            "load_sample_test": True,
            "error": None,
        },
    )


@router.post("/setup")
@router.post("/setup/")
async def setup_submit(
    request: Request,
    response: Response,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    subject: Optional[str] = Form(None),
    classes: Optional[str] = Form(None),
    load_sample_test: bool = Form(False),
    db: Session = Depends(get_db),
):
    """
    Обробка створення першого адміністратора під час першого запуску.
    """
    if crud.count_teachers(db) > 0:
        raise HTTPException(status_code=403, detail="Початкове налаштування вже завершено.")

    full_name = full_name.strip()
    username = username.strip().lower()
    subject = subject.strip() if subject else ""
    classes = classes.strip() if classes else ""

    def render_error(msg: str):
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "full_name": full_name,
                "username": username,
                "subject": subject,
                "classes": classes,
                "load_sample_test": load_sample_test,
                "error": msg,
            },
            status_code=400,
        )

    if len(full_name) < 2:
        return render_error("Вкажіть коректне ім'я користувача (щонайменше 2 символи).")

    if not re.match(r"^[a-zA-Z0-9_.-]{3,32}$", username):
        return render_error("Логін має містити від 3 до 32 символів (латинські літери, цифри, дефіс або підкреслення).")

    if len(password) < 6:
        return render_error("Пароль повинен містити щонайменше 6 символів.")

    if password != password_confirm:
        return render_error("Паролі не співпадають. Перевірте введення.")

    # Створюємо першого вчителя-адміністратора
    teacher = crud.create_teacher(
        db,
        username=username,
        full_name=full_name,
        password=password,
        subject=subject or None,
        classes=classes or None,
        is_active=True,
    )

    # Опціональне завантаження зразкового тесту
    if load_sample_test:
        sample_path = Path(settings.SAMPLE_DATA_DIR) / "sample_test.json"
        if sample_path.exists():
            from app.services.import_export_service import import_test_from_json
            try:
                import_test_from_json(db, teacher.id, sample_path.read_text(encoding="utf-8"))
                print(f"[Setup] Завантажено зразковий тест для {teacher.username}")
            except Exception as exc:
                print(f"[Setup] Помилка завантаження зразкового тесту: {exc}")

    # Аудит-лог
    log_teacher_action(
        db,
        teacher.username,
        teacher.full_name,
        "setup",
        "Створено першого користувача (адміністратора) під час первинного налаштування",
        teacher.id,
    )

    # Автоматичний вхід користувача
    redirect = RedirectResponse(url="/teacher/dashboard", status_code=303)
    create_teacher_session(redirect, teacher.id, teacher.username)
    return redirect
