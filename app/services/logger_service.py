import os
import time
import json
import subprocess
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app import models

LAST_ARCHIVE_FILE = "data/last_archive_time.txt"
ARCHIVE_DIR = "data/archives"

def log_teacher_action(
    db: Session,
    username: str,
    name: str,
    action: str,
    details: Optional[str] = None,
    teacher_id: Optional[int] = None
):
    """
    Logs an action performed by a teacher or administrator to the database.
    """
    try:
        log_entry = models.TeacherLog(
            teacher_id=teacher_id,
            teacher_username=username,
            teacher_name=name,
            action=action,
            details=details
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Logger Error] Failed to log action for {username} ({action}): {e}")


def check_and_create_archive(db: Session, password: str, force: bool = False) -> Tuple[bool, str]:
    """
    Checks if 2 months (60 days) have passed since the last archive.
    If so, or if force is True, archives all current logs into a password-protected zip file
    under data/archives/ and deletes them from the active database.
    """
    now = time.time()
    should_archive = False
    
    if force:
        should_archive = True
    else:
        if not os.path.exists(LAST_ARCHIVE_FILE):
            # If no archive has ever been made, check if there are any logs to archive.
            should_archive = True
        else:
            try:
                with open(LAST_ARCHIVE_FILE, "r") as f:
                    last_time = float(f.read().strip())
                # 2 months = 60 days
                if now - last_time >= 60 * 24 * 60 * 60:
                    should_archive = True
            except Exception:
                should_archive = True

    if not should_archive:
        return False, "Час для планового архівування ще не настав."

    # 1. Fetch logs from database
    logs = db.query(models.TeacherLog).all()
    if not logs:
        # If there are no logs to archive, update the timestamp and return
        try:
            os.makedirs(os.path.dirname(LAST_ARCHIVE_FILE), exist_ok=True)
            with open(LAST_ARCHIVE_FILE, "w") as f:
                f.write(str(now))
        except Exception:
            pass
        return False, "Немає логів для архівування."

    temp_json_path = None
    try:
        # Create directories
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        temp_dir = "data/temp_archive"
        os.makedirs(temp_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_json_path = os.path.join(temp_dir, f"logs_{timestamp}.json")
        
        # Serialize logs to JSON
        logs_data = []
        for log in logs:
            logs_data.append({
                "id": log.id,
                "teacher_id": log.teacher_id,
                "teacher_username": log.teacher_username,
                "teacher_name": log.teacher_name,
                "action": log.action,
                "details": log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None
            })
            
        with open(temp_json_path, "w", encoding="utf-8") as f:
            json.dump(logs_data, f, ensure_ascii=False, indent=2)
            
        archive_name = f"logs_archive_{timestamp}.zip"
        archive_path = os.path.join(ARCHIVE_DIR, archive_name)
        
        # Run zip command with password protection
        # -j stores only the file name (junk paths), -P encrypts with password
        result = subprocess.run(
            ["zip", "-j", "-P", password, archive_path, temp_json_path],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Clean up temporary JSON file
        if os.path.exists(temp_json_path):
            os.remove(temp_json_path)
            
        # Delete archived logs from database
        log_ids = [l.id for l in logs]
        db.query(models.TeacherLog).filter(models.TeacherLog.id.in_(log_ids)).delete(synchronize_session=False)
        db.commit()
        
        # Write last archive time
        with open(LAST_ARCHIVE_FILE, "w") as f:
            f.write(str(now))
            
        # Log this archive creation action in the newly cleared logs database
        log_teacher_action(
            db,
            "admin",
            "Адміністратор",
            "archive_logs",
            f"Створено зашифрований архів логів: {archive_name} (заархівовано записів: {len(logs)})",
            None
        )
        
        return True, f"Успішно створено зашифрований архів {archive_name}. Заархівовано записів: {len(logs)}."
    except Exception as e:
        # Clean up in case of error
        if temp_json_path and os.path.exists(temp_json_path):
            try:
                os.remove(temp_json_path)
            except Exception:
                pass
        return False, f"Помилка створення архіву: {str(e)}"
