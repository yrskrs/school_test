import os
import shutil
import json
import re
from datetime import datetime
from sqlalchemy.orm import Session
from app import models

def generate_new_folder_name(test: models.Test) -> str:
    if not test.created_at:
        dt_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    else:
        dt_str = test.created_at.strftime("%Y-%m-%d_%H-%M-%S")
        
    title_slug = re.sub(r"\s+", "_", test.title.strip())
    title_slug = re.sub(r"[^\w\-]", "", title_slug, flags=re.UNICODE)
    title_slug = re.sub(r"_+", "_", title_slug).strip("_")
    if not title_slug:
        title_slug = "test"
        
    class_str = ""
    if getattr(test, "class_name", None) and test.class_name.strip():
        c_slug = re.sub(r"\s+", "_", test.class_name.strip())
        c_slug = re.sub(r"[^\w\-]", "", c_slug, flags=re.UNICODE)
        c_slug = re.sub(r"_+", "_", c_slug).strip("_")
        if c_slug:
            class_str = f"_{c_slug}"
            
    return f"{title_slug}_{dt_str}{class_str}"

def get_test_folder_name(test: models.Test) -> str:
    if getattr(test, 'folder_name', None):
        return test.folder_name

    if getattr(test, '_folder_name', None):
        return test._folder_name

    if not test.created_at:
        date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = test.created_at.strftime("%Y-%m-%d")
        
    # Replace whitespace and keep alphanumeric/cyrillic/dashes/underscores
    folder_name = re.sub(r"\s+", "_", test.title.strip())
    # Keep Ukrainian, English letters, numbers, underscores, and dashes
    folder_name = re.sub(r"[^\w\-]", "", folder_name, flags=re.UNICODE)
    folder_name = re.sub(r"_+", "_", folder_name)
    folder_name = folder_name.strip("_")
    
    if not folder_name:
        folder_name = "test"
        
    legacy_name = f"{folder_name}_{date_str}_{test.id}"
    
    teacher_username = ""
    if getattr(test, "teacher", None) and test.teacher.username:
        teacher_username = test.teacher.username
        
    new_name = f"{teacher_username}_{folder_name}_{date_str}_{test.id}" if teacher_username else legacy_name
    
    # Backward compatibility: if legacy folder exists and new doesn't, return legacy.
    legacy_dir = os.path.join("app", "static", "tests", legacy_name)
    new_dir = os.path.join("app", "static", "tests", new_name)
    if os.path.exists(legacy_dir) and not os.path.exists(new_dir):
        return legacy_name
        
    return new_name

def test_to_dict(test: models.Test) -> dict:
    return {
        "id": test.id,
        "title": test.title,
        "subject": test.subject,
        "class_name": test.class_name,
        "description": test.description,
        "time_limit_minutes": test.time_limit_minutes,
        "shuffle_questions": test.shuffle_questions,
        "shuffle_options": test.shuffle_options,
        "show_result_after_finish": test.show_result_after_finish,
        "show_correct_answers": test.show_correct_answers,
        "is_formative": test.is_formative,
        "allow_partial_grading": test.allow_partial_grading,
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

def save_test_locally(test: models.Test):
    folder_name = get_test_folder_name(test)
    test_dir = os.path.join("app", "static", "tests", folder_name)
    os.makedirs(test_dir, exist_ok=True)
    
    test_data = test_to_dict(test)
    file_path = os.path.join(test_dir, "test.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)

def delete_test_locally(test: models.Test):
    folder_name = get_test_folder_name(test)
    test_dir = os.path.join("app", "static", "tests", folder_name)
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)

def rename_test_folder(test: models.Test, old_folder_name: str, new_folder_name: str, db: Session):
    if old_folder_name == new_folder_name:
        return
        
    old_dir = os.path.join("app", "static", "tests", old_folder_name)
    new_dir = os.path.join("app", "static", "tests", new_folder_name)
    
    if os.path.exists(old_dir):
        # Create parent directory just in case
        os.makedirs(os.path.dirname(new_dir), exist_ok=True)
        try:
            shutil.move(old_dir, new_dir)
        except Exception:
            pass
        
    # Rewrite database URLs
    db_updated = False
    old_prefix = f"/static/tests/{old_folder_name}/"
    new_prefix = f"/static/tests/{new_folder_name}/"
    
    for q in test.questions:
        if q.image_url and q.image_url.startswith(old_prefix):
            q.image_url = q.image_url.replace(old_prefix, new_prefix)
            db_updated = True
        for o in q.options:
            if o.image_url and o.image_url.startswith(old_prefix):
                o.image_url = o.image_url.replace(old_prefix, new_prefix)
                db_updated = True
                
    if db_updated:
        db.commit()
        db.refresh(test)

def move_temp_images_to_test(test: models.Test, upload_session_id: str, db: Session):
    if not upload_session_id:
        return
        
    # Sanitization to prevent directory traversal
    if not re.match(r"^[a-zA-Z0-9_\-]+$", upload_session_id):
        return

    temp_dir = os.path.join("app", "static", "tests", "temp", upload_session_id)
    if not os.path.exists(temp_dir):
        return

    folder_name = get_test_folder_name(test)
    test_dir = os.path.join("app", "static", "tests", folder_name)
    os.makedirs(test_dir, exist_ok=True)

    # Move files from temp to test directory
    for filename in os.listdir(temp_dir):
        src_path = os.path.join(temp_dir, filename)
        dest_path = os.path.join(test_dir, filename)
        if os.path.isfile(src_path):
            try:
                shutil.move(src_path, dest_path)
            except Exception:
                pass

    # Clean up temp folder
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    # Rewrite database URLs
    db_updated = False
    old_prefix = f"/static/tests/temp/{upload_session_id}/"
    new_prefix = f"/static/tests/{folder_name}/"

    for q in test.questions:
        if q.image_url and q.image_url.startswith(old_prefix):
            q.image_url = q.image_url.replace(old_prefix, new_prefix)
            db_updated = True
        for o in q.options:
            if o.image_url and o.image_url.startswith(old_prefix):
                o.image_url = o.image_url.replace(old_prefix, new_prefix)
                db_updated = True

    if db_updated:
        db.commit()
        db.refresh(test)

def cleanup_unused_images(test: models.Test):
    folder_name = get_test_folder_name(test)
    test_dir = os.path.join("app", "static", "tests", folder_name)
    if not os.path.exists(test_dir):
        return
    
    referenced_filenames = set()
    
    def extract_filename(url):
        if not url:
            return None
        parts = url.split('/')
        return parts[-1] if parts else None
        
    for q in test.questions:
        fname = extract_filename(q.image_url)
        if fname:
            referenced_filenames.add(fname)
        for o in q.options:
            fname = extract_filename(o.image_url)
            if fname:
                referenced_filenames.add(fname)
                
    for name in os.listdir(test_dir):
        if name == "test.json":
            continue
        if name not in referenced_filenames:
            file_path = os.path.join(test_dir, name)
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

def save_student_result_html(test: models.Test, attempt: models.StudentAttempt, html_content: str):
    folder_name = get_test_folder_name(test)
    # Зберігаємо в data/results/Назва_тесту/...
    base_dir = os.path.join("data", "results", folder_name)
    os.makedirs(base_dir, exist_ok=True)
    
    # Форматуємо ім'я файлу (видаляємо небажані символи)
    student_name = re.sub(r"[^\w\-\s]", "", attempt.student_name).strip()
    student_name = re.sub(r"\s+", "_", student_name)
    
    dt_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"{student_name}_{attempt.id}_{dt_str}.html"
    
    file_path = os.path.join(base_dir, file_name)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    return file_path
