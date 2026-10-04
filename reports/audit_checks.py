"""Reproduce the audit on synthetic data only; never connect to the project DB.

Run from the project root: .venv/bin/python reports/audit_checks.py
Output is JSON. All generated databases and files live in a temporary directory.
Historical baseline for revision b6d74a8. Findings may no longer reproduce after
fixes; use tests/test_flows.py for regression checks.
"""

import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timedelta
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = []


def record(name, reproduced, evidence):
    RESULTS.append({"check": name, "reproduced": bool(reproduced), "evidence": evidence})


async def run_audit(work):
    os.environ["DATABASE_URL"] = f"sqlite:///{work / 'audit.db'}"
    os.environ["SECRET_KEY"] = "synthetic-audit-key-only"
    os.environ["DEBUG"] = "False"
    os.chdir(work)
    (work / "app/static").mkdir(parents=True)
    from app.config import settings
    settings.STATIC_DIR = str(work / "app/static")
    from app.main import app
    from app import crud, models
    from app.database import SessionLocal, engine
    from app.security import create_teacher_session
    from app.services import result_service, test_service, test_file_service, import_export_service
    from fastapi.responses import Response
    from sqlalchemy import text

    async def http(method, path, data=None, cookie=None, form=False, content_type=None):
        headers = [(b"host", b"audit.local"), (b"accept", b"application/json")]
        payload = b""
        if data is not None:
            payload = data if isinstance(data, bytes) else (urlencode(data).encode() if form else json.dumps(data).encode())
            media = content_type or ("application/x-www-form-urlencoded" if form else "application/json")
            headers.append((b"content-type", media.encode()))
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        path, _, query = path.partition("?")
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
                 "headers": headers, "client": ("127.0.0.1", 1), "server": ("audit.local", 80)}
        sent, consumed = [], False
        completed = asyncio.Event()

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await completed.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed.set()

        error = None
        try:
            await asyncio.wait_for(app(scope, receive, send), timeout=10)
        except Exception as exc:
            error = type(exc).__name__
        start = next((m for m in sent if m["type"] == "http.response.start"), {})
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        if error:
            print(f"ASGI probe {method} {path}: {error}, status={start.get('status')}, messages={len(sent)}", file=sys.stderr)
        return start.get("status"), body.decode(errors="replace"), dict(start.get("headers", [])), error

    async with app.router.lifespan_context(app):
        status, _, _, _ = await http("GET", "/")
        record("fresh_start", status == 303, {"http_status": status})
        status, _, _, _ = await http("POST", "/setup", {
            "full_name": "Synthetic Owner", "username": "owner", "password": "audit-password",
            "password_confirm": "audit-password"}, form=True)
        db = SessionLocal()
        owner = crud.get_teacher_by_username(db, "owner")
        response = Response()
        create_teacher_session(response, owner.id, owner.username)
        owner_cookie = response.headers["set-cookie"].split(";", 1)[0]
        status2, body, _, _ = await http("POST", "/teacher/profile/create-teacher", {
            "username": "another", "full_name": "Synthetic Teacher", "password": "audit-password"},
            cookie=owner_cookie, form=True)
        record("setup_custom_username_has_no_admin_rights", status == 303 and '"status":"error"' in body,
               {"setup_status": status, "create_teacher_status": status2, "response": json.loads(body)})
        other = crud.create_teacher(db, "another", "Synthetic Teacher", "audit-password")

        test = crud.create_test(db, owner.id, {"title": "Synthetic Audit", "time_limit_minutes": 1,
            "time_limit_per_question": 10, "max_grade": 5, "allow_retake": True,
            "allow_partial_grading": True, "use_fuzzy_matching": True,
            "random_questions_limit": 3, "shuffle_questions": True, "shuffle_options": True,
            "questions": [
                {"question_text": "Pick", "question_type": "single_choice", "order_index": 0,
                 "options": [{"option_text": "Yes", "is_correct": True}, {"option_text": "No"}]},
                {"question_text": "Text", "question_type": "short_text", "order_index": 1,
                 "options": [{"option_text": "synthetic-secret-answer", "is_correct": True}]},
                {"question_text": "Pairs", "question_type": "matching", "order_index": 2,
                 "options": [{"option_text": "A", "matching_text": "Right A"}, {"option_text": "B", "matching_text": "Right B"}]},
                {"question_text": "Order", "question_type": "sequence", "order_index": 3,
                 "options": [{"option_text": "First", "order_index": 0}, {"option_text": "Second", "order_index": 1}]},
                {"question_text": "Hotspot", "question_type": "hotspot", "order_index": 4,
                 "options": [{"option_text": "(0,0)-(100,0)-(100,100)-(0,100)", "is_correct": True}]}
            ]})
        foreign_test = crud.create_test(db, other.id, {"title": "Foreign Test", "questions": [
            {"question_text": "Foreign Q", "question_type": "single_choice", "points": 100,
             "options": [{"option_text": "Foreign Answer", "is_correct": True}]}]})
        session = crud.create_session(db, test.id, "AUDIT1")

        def attempt(name):
            item = crud.create_attempt(db, session.id, name)
            return crud.start_attempt(db, item)

        first = attempt("Synthetic Student")
        status, body, _, _ = await http("GET", f"/api/session-status/{session.id}")
        record("anonymous_api_data", status == 200 and "Synthetic Student" in body,
               {"http_status": status, "name_disclosed": "Synthetic Student" in body, "code_disclosed": "AUDIT1" in body})
        status, _, _, _ = await http("GET", f"/student/test/{first.id}")
        record("anonymous_student_page", status == 200, {"http_status": status})
        status, _, headers, _ = await http("POST", "/student/login", {
            "student_name": "Synthetic Student", "access_code": "AUDIT1"}, form=True)
        record("same_name_takes_existing_attempt", status == 303 and str(first.id).encode() in headers.get(b"location", b""),
               {"http_status": status, "location": headers.get(b"location", b"").decode()})

        # Use a separate attempt with no random selection to inspect all question types.
        test.random_questions_limit = None
        db.commit()
        payload_attempt = attempt("Payload Student")
        payload1 = test_service.build_test_payload(db, payload_attempt, shuffle=True)
        payload2 = test_service.build_test_payload(db, payload_attempt, shuffle=True)
        record("deterministic_payload", payload1 == payload2, {"same_on_two_loads": payload1 == payload2})
        text_q = next(q for q in payload1["questions"] if q["question_type"] == "short_text")
        match_q = next(q for q in payload1["questions"] if q["question_type"] == "matching")
        seq_q = next(q for q in payload1["questions"] if q["question_type"] == "sequence")
        spot_q = next(q for q in payload1["questions"] if q["question_type"] == "hotspot")
        record("payload_answer_keys", text_q["options"][0]["option_text"] == "synthetic-secret-answer",
               {"short_text_answer": text_q["options"][0]["option_text"],
                "matching_pairs": bool(match_q["options"][0]["matching_text"]),
                "sequence_indices": [o["order_index"] for o in seq_q["options"]],
                "hotspot_region": spot_q["options"][0]["option_text"]})

        q = test.questions[0]
        data = {"question_id": q.id, "selected_options": [q.options[0].id]}
        status, _, _, _ = await http("POST", f"/student/test/{payload_attempt.id}/save-answer", data)
        status2, _, _, _ = await http("POST", f"/student/test/{payload_attempt.id}/save-answer", data)
        record("anonymous_answer_write", status == 200, {"http_status": status})
        record("sequential_resubmit_blocked", status2 == 400, {"second_http_status": status2})

        injected = attempt("Injected Student")
        fq = foreign_test.questions[0]
        status, _, _, _ = await http("POST", f"/student/test/{injected.id}/save-answer", {
            "question_id": fq.id, "selected_options": [fq.options[0].id]})
        db.expire_all()
        injected_payload = test_service.build_test_payload(db, injected, shuffle=True)
        record("foreign_question_injection", status == 200 and injected_payload["question_count"] == 1,
               {"http_status": status, "resulting_question_ids": [q["id"] for q in injected_payload["questions"]]})
        status, _, _, _ = await http("POST", f"/student/test/{injected.id}/finish", {})
        db.expire_all()
        record("anonymous_finish", status == 200, {"http_status": status, "score": injected.score, "max_score": injected.max_score})

        overdue = attempt("Overdue Student")
        overdue.started_at = datetime.now() - timedelta(hours=1)
        session.is_active = False
        db.commit()
        status, _, _, _ = await http("POST", f"/student/test/{overdue.id}/save-answer", data)
        record("overdue_closed_session_write", status == 200, {"http_status": status, "session_active": False, "elapsed_minutes": 60, "limit_minutes": 1})
        status, _, _, _ = await http("POST", f"/student/test/{overdue.id}/finish")
        db.expire_all()
        record("expired_attempt_marked_finished", overdue.status == models.AttemptStatus.finished,
               {"http_status": status, "actual_status": overdue.status.value})
        session.is_active = True
        db.commit()

        malformed = attempt("Malformed Student")
        status, _, _, error = await http("POST", f"/student/test/{malformed.id}/save-answer", {
            "question_id": q.id, "selected_options": 123})
        db.expire_all()
        bad_answer = crud.get_answer_by_attempt_and_question(db, malformed.id, q.id)
        retry_status, _, _, _ = await http("POST", f"/student/test/{malformed.id}/save-answer", data)
        record("malformed_answer_persisted_and_locked", status == 500 and retry_status == 400,
               {"first_status": status, "exception": error, "stored_json": bad_answer.selected_options_json,
                "valid_retry_status": retry_status})

        hidden = crud.create_test(db, owner.id, {"title": "Hidden Result", "show_result_after_finish": False})
        status, _, _, _ = await http("POST", f"/teacher/tests/{hidden.id}/edit", {
            "title": "Hidden Result", "questions_json": "[]"}, cookie=owner_cookie, form=True)
        db.expire_all()
        record("unchecked_result_checkbox_reenabled", status in (200, 303) and hidden.show_result_after_finish,
               {"http_status": status, "before": False, "after": hidden.show_result_after_finish})

        test_file_service.save_test_locally(test)
        url = f"/static/tests/{test_file_service.get_test_folder_name(test)}/test.json"
        status, body, _, _ = await http("GET", url)
        record("public_test_json", status == 200 and '"is_correct": true' in body,
               {"http_status": status, "has_correct_answer_flags": '"is_correct": true' in body})

        # The export traversal probe reads only our synthetic marker file.
        (work / "audit-marker.txt").write_text("synthetic-marker-only")
        q.image_url = "../audit-marker.txt"
        db.commit()
        status, body, _, _ = await http("GET", f"/teacher/tests/{test.id}/export", cookie=owner_cookie)
        exported = json.loads(body)
        encoded = exported.get("embedded_images", {}).get("audit-marker.txt")
        record("export_file_path_escape", status == 200 and encoded is not None and base64.b64decode(encoded) == b"synthetic-marker-only",
               {"http_status": status, "synthetic_marker_embedded": encoded is not None})

        multipart = (b"--audit-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"probe.html\"\r\n"
                     b"Content-Type: text/html\r\n\r\n<!doctype html><p>Synthetic upload only</p>\r\n--audit-boundary--\r\n")
        status, upload_body, _, _ = await http("POST", f"/teacher/upload-image?test_id={foreign_test.id}",
            multipart, cookie=owner_cookie, content_type="multipart/form-data; boundary=audit-boundary")
        upload_url = json.loads(upload_body).get("url", "")
        public_status, public_body, public_headers, _ = await http("GET", upload_url)
        record("upload_foreign_test_and_active_content", status == 200 and public_status == 200,
               {"upload_status": status, "public_status": public_status,
                "content_type": public_headers.get(b"content-type", b"").decode(),
                "foreign_test_used": True, "html_served": "Synthetic upload only" in public_body})

        q.question_text = '</script><p>Synthetic markup only</p>'
        db.commit()
        status, edit_body, _, _ = await http("GET", f"/teacher/tests/{test.id}/edit", cookie=owner_cookie)
        record("unescaped_script_closing_tag", status == 200 and q.question_text in edit_body,
               {"http_status": status, "unescaped_closing_tag_present": q.question_text in edit_body,
                "javascript_executed": False})

        prior_score, prior_max = result_service.grade_all_answers(db, payload_attempt)
        q.points = 50
        db.commit()
        new_score, new_max = result_service.grade_all_answers(db, payload_attempt)
        record("live_edits_change_attempt_grading", new_score != prior_score,
               {"before": [prior_score, prior_max], "after": [new_score, new_max]})

        status, event_body, _, _ = await http("POST", f"/student/event/{payload_attempt.id}", {"event_type": "fullscreen_exit"})
        record("fullscreen_events_ignored", '"unknown_event"' in event_body,
               {"http_status": status, "response": json.loads(event_body)})

        imported = import_export_service.import_test_from_json(db, owner.id, json.dumps(exported))
        lost = {key: {"before": getattr(test, key), "after": getattr(imported, key)}
                for key in ("time_limit_per_question", "max_grade", "allow_retake", "allow_partial_grading", "use_fuzzy_matching")
                if getattr(test, key) != getattr(imported, key)}
        record("json_roundtrip_loses_settings", bool(lost), lost)

        for label, importer, source in (
            ("sample_xml_import", import_export_service.import_test_from_mytestx_xml,
             (ROOT / "sample_data/sample_test.xml").read_text(encoding="utf-8")),
            ("sample_docx_import", import_export_service.import_test_from_docx,
             (ROOT / "sample_data/sample_test.docx").read_bytes()),
        ):
            try:
                sample = importer(db, owner.id, source, temp_session_id=f"audit_{label}")
                record(label, bool(sample.questions), {"question_count": len(sample.questions)})
            except Exception as exc:
                db.rollback()
                record(label, False, {"exception": type(exc).__name__, "message": str(exc)})
        record("sqlite_foreign_keys_disabled", db.execute(text("PRAGMA foreign_keys")).scalar() == 0,
               {"pragma_foreign_keys": db.execute(text("PRAGMA foreign_keys")).scalar()})

        numeric = models.Question(question_text="Number", question_type=models.QuestionType.short_text,
                                  points=1, test=test, options=[models.AnswerOption(option_text="-5", is_correct=True)])
        correct, points = result_service.grade_answer(numeric, "5", None)
        record("numeric_sign_removed", correct, {"expected": "-5", "given": "5", "correct": correct, "points": points})
        test.use_fuzzy_matching = False
        numeric.options[0].option_text = "1.5"
        correct, points = result_service.grade_answer(numeric, "15", None)
        record("decimal_separator_removed", correct, {"expected": "1.5", "given": "15", "correct": correct, "points": points})
        db.rollback()
        db.close()
    engine.dispose()

    # Exercise actual ASGI WebSocket routing without cookies or external sockets.
    async def websocket_probe(path):
        received = [{"type": "websocket.connect"}, {"type": "websocket.disconnect", "code": 1000}]
        sent = []
        async def receive():
            return received.pop(0)
        async def send(message):
            sent.append(message)
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [],
                 "client": ("127.0.0.1", 1), "server": ("audit.local", 80), "subprotocols": []}
        await app(scope, receive, send)
        return any(m["type"] == "websocket.accept" for m in sent)
    monitor = await websocket_probe("/ws/monitor/999999")
    student = await websocket_probe("/ws/student/999999")
    record("anonymous_websockets", monitor and student,
           {"nonexistent_monitor_accepted": monitor, "nonexistent_student_accepted": student})


async def main(work):
    # Keep the loop responsive when the execution sandbox restricts the
    # thread-to-loop wakeup socket. This does not modify application behavior.
    async def tick():
        while True:
            await asyncio.sleep(0.01)
    ticker = asyncio.create_task(tick())
    try:
        await run_audit(work)
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="schooltest-audit-") as directory:
        previous = Path.cwd()
        try:
            asyncio.run(main(Path(directory)))
        finally:
            os.chdir(previous)
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
