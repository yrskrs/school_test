import base64
import mimetypes
import os
from sqlalchemy.orm import Session
from app import crud, models
from app.templating import templates

def get_base64_image(image_url: str) -> str:
    """
    Конвертує локальне зображення (яке починається з /static/...) у base64 data URI.
    """
    if not image_url:
        return ""
        
    if not image_url.startswith("/static/"):
        return image_url  # Якщо це зовнішній URL, повертаємо як є

    # Перетворюємо /static/... на app/static/...
    filepath = os.path.join("app", image_url.lstrip("/"))
    
    if not os.path.exists(filepath):
        return ""
        
    try:
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = "image/png"
            
        with open(filepath, "rb") as f:
            encoded_string = base64.b64encode(f.read()).decode("utf-8")
            
        return f"data:{mime_type};base64,{encoded_string}"
    except Exception as e:
        print(f"Error converting image to base64: {e}")
        return ""

def generate_result_html(db: Session, attempt: models.StudentAttempt) -> str:
    """
    Генерує standalone HTML-файл для збереження результатів учня.
    """
    test = attempt.session.test
    answers = crud.get_answers_by_attempt(db, attempt.id)
    answers = sorted(answers, key=lambda a: a.question.order_index if a.question else 0)

    # Додаємо base64 зображення до питань та варіантів
    for ans in answers:
        q = ans.question
        if q and q.image_url:
            q.base64_image = get_base64_image(q.image_url)
        if q and q.options:
            for opt in q.options:
                if opt.image_url:
                    opt.base64_image = get_base64_image(opt.image_url)

    # Рендеримо шаблон (виклик render, а не TemplateResponse)
    template = templates.get_template("result_export.html")
    html_content = template.render(
        attempt=attempt,
        test=test,
        answers=answers
    )
    
    return html_content
