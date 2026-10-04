"""Real HTTP/PostgreSQL validation, exclusively in compose.runtime.yaml."""
import json
import os
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

url = make_url(os.environ["DATABASE_URL"])
if url.host != "postgres" or url.database != "schooltest_runtime_check" or url.username != "runtime_check":
    raise RuntimeError("This check may only write to its isolated validation database")

# Connect using both drivers and the default selected by the installed SQLAlchemy.
for dialect in ("postgresql", "postgresql+psycopg", "postgresql+psycopg2"):
    engine = create_engine(url.set(drivername=dialect))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    engine.dispose()
    print(f"PostgreSQL connection OK: {dialect}")

from app import crud, models
from app.database import SessionLocal, engine as app_engine
from app.security import set_student_cookie, create_teacher_session
from app.services.test_service import build_test_payload
from app.version import __version__
from fastapi.responses import Response


def cookie_for(function, *args):
    response = Response()
    function(response, *args)
    return response.headers["set-cookie"].split(";", 1)[0]


def http(path, body=None, cookie=None):
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(f"http://app:8000{path}", data=payload, headers=headers), timeout=10) as response:
        assert response.status == 200
        return response.read().decode(), response.headers


body, headers = http("/api/version")
assert json.loads(body)["version"] == __version__
assert headers["X-App-Version"] == __version__
http("/")  # Fresh installation: setup page, database and template rendering.
with SessionLocal() as db:
    teacher = crud.create_teacher(db, "runtime_check", "Runtime Teacher", "synthetic-password")
    test = crud.create_test(db, teacher.id, {"title": "PostgreSQL runtime check", "questions": [
        {"question_type": "single_choice", "question_text": "Synthetic question", "points": 2,
         "options": [{"option_text": "Correct", "is_correct": True}, {"option_text": "Other"}]},
    ]})
    session = crud.create_session(db, test.id, "RUN123")
    attempt = crud.start_attempt(db, crud.create_attempt(db, session.id, "Runtime Student"))
    unaffected = crud.start_attempt(db, crud.create_attempt(db, session.id, "Unaffected Student"))
    build_test_payload(db, attempt, shuffle=True)
    question = test.questions[0]
    student_cookie = cookie_for(set_student_cookie, attempt.id)
    teacher_cookie = cookie_for(create_teacher_session, teacher.id, teacher.username)
    http("/")  # Existing teachers: student login page renders too.
    http(f"/student/test/{attempt.id}", cookie=student_cookie)
    http(f"/teacher/sessions/{session.id}/monitor", cookie=teacher_cookie)
    http(f"/student/test/{attempt.id}/save-answer", {
        "question_id": question.id, "answer_text": None, "selected_options": [question.options[0].id],
    }, student_cookie)
    for number in range(1, 4):
        event = {"event_type": "tab_blur", "details": "fullscreen_exit", "client_event_id": f"runtime-exit-{number}"}
        result = json.loads(http(f"/student/event/{attempt.id}", event, student_cookie)[0])
        assert result["violation_count"] == number
        assert result["attempt_status"] == ("stopped" if number == 3 else "in_progress")
    retried = json.loads(http(f"/student/event/{attempt.id}", event, student_cookie)[0])
    assert retried["status"] == "already_saved" and retried["violation_count"] == 3
    assert retried["stop_reason"] == "violations"
    db.expire_all()
    assert attempt.status == models.AttemptStatus.stopped and attempt.score == 2
    assert unaffected.status == models.AttemptStatus.in_progress
    finished, _ = http(f"/student/test/{attempt.id}/finished", cookie=student_cookie)
    assert "Автоматична зупинка після третього порушення" in finished
    monitor = json.loads(http(f"/api/session-status/{session.id}", cookie=teacher_cookie)[0])
    assert next(a for a in monitor["attempts"] if a["id"] == attempt.id)["violation_count"] == 3
# Reproduce an older schema: subject exists, but the following columns do not.
# These writes are confined to the synthetic DB validated above.
from app.services.startup_schema import ensure_legacy_columns
with app_engine.begin() as connection:
    connection.execute(text("ALTER TABLE teachers DROP COLUMN classes"))
    connection.execute(text("ALTER TABLE tests DROP COLUMN use_fuzzy_matching"))
ensure_legacy_columns(app_engine)
ensure_legacy_columns(app_engine)
assert "classes" in {c["name"] for c in inspect(app_engine).get_columns("teachers")}
with app_engine.connect() as connection:
    assert connection.execute(text("SELECT use_fuzzy_matching FROM tests")).scalar_one() is False
http("/")
print("PostgreSQL legacy schema upgrade and repeated startup OK")
print(f"PASS: Python 3.11 image, all PostgreSQL drivers, real HTTP startup/rendering/answers/stop/retry ({__version__})")
