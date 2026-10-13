import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import Response
from starlette.requests import HTTPConnection

from app.config import settings


# ---------------------------------------------------------------------------
# Password hashing (bcrypt-free, using SHA-256 + HMAC for local MVP)
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Хешує пароль за допомогою PBKDF2-HMAC-SHA256."""
    import hashlib, os, base64
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return base64.b64encode(salt + key).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Перевіряє пароль проти збереженого хешу."""
    import hashlib, base64
    try:
        raw = base64.b64decode(hashed_password.encode())
        salt = raw[:16]
        stored_key = raw[16:]
        key = hashlib.pbkdf2_hmac("sha256", plain_password.encode(), salt, 260_000)
        return hmac.compare_digest(key, stored_key)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    return _hash_password(password)


# ---------------------------------------------------------------------------
# Session helpers — signed cookie (teacher)
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    """Підписує рядок HMAC-SHA256 із SECRET_KEY."""
    sig = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_sign(signed: str) -> Optional[str]:
    """Перевіряє підпис і повертає оригінальний payload або None."""
    try:
        payload, sig = signed.rsplit(".", 1)
        expected = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return payload
    except Exception:
        pass
    return None


def create_teacher_session(response: Response, teacher_id: int, username: str) -> None:
    """Встановлює підписану cookie-сесію вчителя."""
    from app.database import SessionLocal
    from app import models
    with SessionLocal() as db:
        nonce = db.query(models.Teacher.auth_nonce).filter_by(id=teacher_id).scalar()
    payload = json.dumps({"id": teacher_id, "u": username, "t": int(time.time()), "n": nonce})
    import base64
    encoded = base64.b64encode(payload.encode()).decode()
    signed = _sign(encoded)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=signed,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def get_teacher_session(request: HTTPConnection) -> Optional[dict]:
    """Читає і перевіряє cookie вчителя. Повертає dict або None."""
    import base64
    signed = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not signed:
        return None
    payload_encoded = _verify_sign(signed)
    if not payload_encoded:
        return None
    try:
        payload = json.loads(base64.b64decode(payload_encoded.encode()).decode())
        age = int(time.time()) - payload.get("t", 0)
        if age > settings.SESSION_MAX_AGE:
            return None
        from app.database import SessionLocal
        from app import models
        with SessionLocal() as db:
            teacher = db.query(models.Teacher.username, models.Teacher.auth_nonce).filter_by(id=payload['id']).first()
            if not teacher or teacher.username != payload.get('u') or teacher.auth_nonce != payload.get('n'):
                return None
        return payload
    except Exception:
        return None


def clear_teacher_session(response: Response) -> None:
    response.delete_cookie(key=settings.SESSION_COOKIE_NAME)


# ---------------------------------------------------------------------------
# Session helpers — student attempt cookie
# ---------------------------------------------------------------------------

def set_student_cookie(response: Response, attempt_id: int) -> None:
    """Зберігає attempt_id учня у підписаній cookie."""
    from app.database import SessionLocal
    from app import models
    with SessionLocal() as db:
        nonce = db.query(models.StudentAttempt.auth_nonce).filter_by(id=attempt_id).scalar()
    payload = str(attempt_id) + ('.' + nonce if nonce else '')
    signed = _sign(payload)
    response.set_cookie(
        key=settings.STUDENT_COOKIE_NAME,
        value=signed,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def get_student_attempt_id(request: HTTPConnection) -> Optional[int]:
    """Читає attempt_id учня з cookie. Повертає int або None."""
    signed = request.cookies.get(settings.STUDENT_COOKIE_NAME)
    if not signed:
        return None
    payload = _verify_sign(signed)
    if not payload:
        return None
    try:
        raw_id, separator, nonce = payload.partition('.')
        attempt_id = int(raw_id)
        if not 0 < attempt_id <= 9223372036854775807:
            return None
        from app.database import SessionLocal
        from app import models
        with SessionLocal() as db:
            row = db.query(models.StudentAttempt.auth_nonce).filter_by(id=attempt_id).first()
            if row is None or row.auth_nonce != (nonce if separator else None):
                return None
        return attempt_id
    except (ValueError, TypeError):
        return None


def clear_student_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.STUDENT_COOKIE_NAME)
