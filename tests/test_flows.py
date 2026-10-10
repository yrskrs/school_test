"""Regression checks on synthetic data. Run: .venv/bin/python tests/test_flows.py"""
import asyncio
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = tempfile.TemporaryDirectory(prefix="schooltest-regression-")
os.environ["DATABASE_URL"] = f"sqlite:///{WORK.name}/checks.db"
os.environ["SECRET_KEY"] = "synthetic-regression-key"
os.environ["DEBUG"] = "False"
os.chdir(WORK.name)
from app.main import app
from app import crud, models
from app.database import Base, SessionLocal, engine
from app.security import create_teacher_session, set_student_cookie
from app.services import test_service, teacher_list_service
from fastapi.responses import Response

if engine.url.get_backend_name() != "sqlite" or not Path(engine.url.database).resolve().is_relative_to(Path(WORK.name).resolve()):
    raise RuntimeError("Regression tests require a separate process and their own temporary database")


def cookie_for(function, *args):
    response = Response()
    function(response, *args)
    return response.headers["set-cookie"].split(";", 1)[0]


async def http(method, path, data=None, cookie=None, form=False, content_type=None, authorization=None):
    headers = [(b"host", b"checks.local"), (b"accept", b"application/json")]
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    payload = b""
    if data is not None:
        payload = data if isinstance(data, bytes) else (urlencode(data).encode() if form else json.dumps(data).encode())
        headers.append((b"content-type", content_type.encode() if content_type else (b"application/x-www-form-urlencoded" if form else b"application/json")))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    path, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 1), "server": ("checks.local", 80)}
    messages, consumed = [], False
    completed = asyncio.Event()

    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await completed.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            completed.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body").decode()
    return start["status"], body, dict(start["headers"])


class Flows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # This environment needs a periodic wake-up for worker-thread callbacks.
        async def ticker():
            while True:
                await asyncio.sleep(.01)
        self.ticker = asyncio.create_task(ticker())
        Base.metadata.drop_all(engine)
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.db = SessionLocal()
        self.owner = crud.create_teacher(self.db, "owner", "Synthetic Teacher", "synthetic-password")
        self.other = crud.create_teacher(self.db, "other", "Other Teacher", "synthetic-password")
        self.teacher_cookie = cookie_for(create_teacher_session, self.owner.id, self.owner.username)
        self.other_cookie = cookie_for(create_teacher_session, self.other.id, self.other.username)
        definitions = [
            ("single_choice", [{"option_text": "Yes", "is_correct": True}, {"option_text": "No"}]),
            ("multiple_choice", [{"option_text": "A", "is_correct": True}, {"option_text": "B", "is_correct": True}]),
            ("true_false", [{"option_text": "True", "is_correct": True}, {"option_text": "False"}]),
            ("image_choice", [{"option_text": "Image A", "is_correct": True}, {"option_text": "Image B"}]),
            ("short_text", [{"option_text": "private-text-answer", "is_correct": True}]),
            ("matching", [{"option_text": "A", "matching_text": "Right A"}, {"option_text": "B", "matching_text": "Right B"}]),
            ("sequence", [{"option_text": "First", "order_index": 0}, {"option_text": "Second", "order_index": 1}]),
            ("hotspot", [{"option_text": "(0,0)-(100,0)-(100,100)-(0,100)", "is_correct": True}]),
        ]
        self.test = crud.create_test(self.db, self.owner.id, {"title": "Synthetic Test", "class_name": "7-A",
            "allow_retake": True, "show_result_after_finish": False,
            "questions": [{"question_type": kind, "question_text": kind, "order_index": i, "options": opts}
                          for i, (kind, opts) in enumerate(definitions)]})
        self.session = crud.create_session(self.db, self.test.id, "CHECK1")
        self.attempt = crud.start_attempt(self.db, crud.create_attempt(self.db, self.session.id, "Student Example"))
        self.payload = test_service.build_test_payload(self.db, self.attempt, shuffle=True)
        self.student_cookie = cookie_for(set_student_cookie, self.attempt.id)
        self.questions = {q.question_type.value: q for q in self.test.questions}

    async def asyncTearDown(self):
        self.db.close()
        await self.lifespan.__aexit__(None, None, None)
        self.ticker.cancel()
        try:
            await self.ticker
        except asyncio.CancelledError:
            pass

    async def submit(self, q, selected=None, text=None):
        return await http("POST", f"/student/test/{self.attempt.id}/save-answer",
                          {"question_id": q.id, "answer_text": text, "selected_options": selected}, self.student_cookie)

    async def test_browser_ownership_and_websockets(self):
        for path in (f"/student/test/{self.attempt.id}", f"/api/attempt/{self.attempt.id}",
                     f"/api/session-status/{self.session.id}"):
            self.assertEqual((await http("GET", path))[0], 401)
        second = crud.create_attempt(self.db, self.session.id, "Another Student")
        self.assertEqual((await http("GET", f"/student/test/{second.id}", cookie=self.student_cookie))[0], 404)
        self.assertEqual((await http("GET", f"/api/session-status/{self.session.id}", cookie=self.other_cookie))[0], 404)
        self.assertEqual((await http("GET", f"/api/session-status/{self.session.id}", cookie=self.teacher_cookie))[0], 200)
        for path in (f"/ws/student/{self.attempt.id}", f"/ws/monitor/{self.session.id}"):
            messages = []
            async def receive(): return {"type": "websocket.connect"}
            async def send(message): messages.append(message)
            await app({"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": path,
                       "query_string": b"", "headers": [(b"host", b"checks.local")],
                       "client": ("127.0.0.1", 1), "server": ("checks.local", 80)}, receive, send)
            self.assertEqual(messages[0], {"type": "websocket.close", "code": 1008, "reason": ""})

    async def test_all_question_types_and_lost_ack_retry(self):
        for kind, q in self.questions.items():
            selected, text = None, None
            if kind == "short_text": text = "private-text-answer"
            elif kind == "matching": selected = {str(o.id): o.matching_text for o in q.options}
            elif kind == "hotspot": selected = {"x": 50, "y": 50}
            elif kind in ("sequence", "multiple_choice"): selected = [o.id for o in q.options]
            else: selected = [q.options[0].id]
            first = await self.submit(q, selected, text)
            self.assertEqual(first[0], 200, first[1])
            retry = await self.submit(q, selected, text)
            self.assertEqual(json.loads(retry[1])["status"], "already_saved")
            self.db.expire_all()
            saved = crud.get_answer_by_attempt_and_question(self.db, self.attempt.id, q.id)
            self.assertTrue(saved.is_correct, kind)
        q = self.questions["single_choice"]
        self.assertEqual((await self.submit(q, [q.options[1].id]))[0], 409)
        status, body, _ = await http("POST", f"/student/test/{self.attempt.id}/finish", {"timeout": False}, self.student_cookie)
        self.assertEqual(status, 200, body)
        self.db.expire_all()
        self.assertEqual(self.attempt.score, self.attempt.max_score)
        finished_page = await http('GET', f'/student/test/{self.attempt.id}/finished', cookie=self.student_cookie)
        self.assertEqual(finished_page[0], 200)
        recovery_config = json.loads(re.search(r'<script type="application/json" id="recovery-config">(.*?)</script>', finished_page[1], re.S).group(1))
        self.assertEqual(len(recovery_config['confirmed_ids']), 8)
        self.assertIn('Результат доступний учителю.', finished_page[1])
        self.assertIsNone(json.loads((await http("GET", f"/api/attempt/{self.attempt.id}", cookie=self.student_cookie))[1])["score"])
        self.assertEqual((await http("POST", f"/student/test/{self.attempt.id}/finish", {}, self.student_cookie))[0], 200)

    async def test_invalid_answers_do_not_change_database(self):
        q = self.questions["single_choice"]
        for selected in ([999999], [q.options[0].id, q.options[0].id], "string", [True], {"x": 1}):
            self.assertEqual((await self.submit(q, selected))[0], 422)
        foreign = crud.create_test(self.db, self.other.id, {"title": "Foreign", "questions": [
            {"question_text": "Foreign Q", "options": [{"option_text": "Foreign A", "is_correct": True}]}]})
        self.assertEqual((await self.submit(foreign.questions[0], [foreign.questions[0].options[0].id]))[0], 404)
        self.db.expire_all()
        self.assertIsNone(crud.get_answer_by_attempt_and_question(self.db, self.attempt.id, q.id).selected_options_json)
        self.assertEqual(self.db.query(models.StudentAnswer).filter_by(attempt_id=self.attempt.id).count(), 8)
        self.assertEqual((await http("POST", f"/student/test/{self.attempt.id}/finish", [], self.student_cookie))[0], 422)

    async def test_pause_resume_and_explicit_empty_answer(self):
        for action in ("pause-all", "resume-all"):
            status, body, _ = await http("POST", f"/teacher/sessions/{self.session.id}/{action}", cookie=self.teacher_cookie)
            self.assertEqual(status, 200, body)
            q = self.questions["single_choice"]
            answer = await self.submit(q, [q.options[0].id])
            self.assertEqual(answer[0], 409 if action == "pause-all" else 200)
        q = self.questions["short_text"]
        self.assertEqual((await self.submit(q, text=""))[0], 200)
        self.assertEqual((await self.submit(q, text=""))[0], 200)

    async def test_name_resume_retake_and_settings(self):
        form = {"student_name": self.attempt.student_name, "access_code": self.session.access_code}
        self.assertEqual((await http("POST", "/student/login", form, form=True))[0], 409)
        self.assertEqual((await http("POST", "/student/login", form, self.student_cookie, form=True))[0], 303)
        self.assertEqual((await http("GET", f"/student/test/{self.attempt.id}/finished", cookie=self.student_cookie))[0], 303)
        await http("POST", f"/student/test/{self.attempt.id}/finish", {}, self.student_cookie)
        status, _, headers = await http("POST", f"/student/test/{self.attempt.id}/retake", cookie=self.student_cookie)
        self.assertEqual(status, 303)
        self.db.expire_all()
        newer = self.db.query(models.StudentAttempt).order_by(models.StudentAttempt.id.desc()).first()
        self.assertNotEqual(newer.id, self.attempt.id)
        self.assertEqual(newer.student_name, self.attempt.student_name)
        self.assertEqual(newer.status, models.AttemptStatus.not_started)
        self.assertIn(b"set-cookie", headers)
        self.test.allow_retake = False
        self.db.commit()
        self.assertEqual((await http("POST", f"/student/test/{self.attempt.id}/retake", cookie=self.student_cookie))[0], 403)

    async def test_event_retry_count_and_skip_restore(self):
        event = {"event_type": "tab_blur", "details": "fullscreen_exit", "client_event_id": "unique-event-1"}
        for _ in range(2):
            self.assertEqual((await http("POST", f"/student/event/{self.attempt.id}", event, self.student_cookie))[0], 200)
        event['details'] = 'Retry with different text'
        self.assertEqual(json.loads((await http("POST", f"/student/event/{self.attempt.id}", event, self.student_cookie))[1])['status'], 'already_saved')
        self.db.expire_all()
        self.assertEqual(self.db.query(models.EventLog).filter_by(attempt_id=self.attempt.id, event_type=models.EventType.tab_blur).count(), 1)
        data = json.loads((await http("GET", f"/api/session-status/{self.session.id}", cookie=self.teacher_cookie))[1])
        self.assertEqual(data["attempts"][0]["violation_count"], 1)
        page = await http("GET", f"/teacher/sessions/{self.session.id}/monitor", cookie=self.teacher_cookie)
        self.assertEqual(page[0], 200, page[1][:500])
        self.assertIn(f'id="warning-{self.attempt.id}"', page[1])
        qid = self.questions["single_choice"].id
        await http("POST", f"/student/event/{self.attempt.id}", {"event_type": "question_skipped", "details": f"question_id={qid}", "client_event_id": "skip-1"}, self.student_cookie)
        page = await http("GET", f"/student/test/{self.attempt.id}", cookie=self.student_cookie)
        self.assertEqual(page[0], 200)
        config = json.loads(re.search(r'<script type="application/json" id="page-config">(.*?)</script>', page[1], re.S).group(1))
        self.assertEqual(config['skipped_questions'], [qid])

    async def test_third_violation_stops_only_owned_attempt_and_survives_retry(self):
        other_attempt = crud.start_attempt(self.db, crud.create_attempt(self.db, self.session.id, "Unaffected Student"))
        q = self.questions["single_choice"]
        self.assertEqual((await self.submit(q, [q.options[0].id]))[0], 200)
        for i in range(1, 4):
            event = {"event_type": "tab_blur", "details": "fullscreen_exit", "client_event_id": f"exit-{i}"}
            status, body, _ = await http("POST", f"/student/event/{self.attempt.id}", event, self.student_cookie)
            data = json.loads(body)
            self.assertEqual(status, 200, body)
            self.assertEqual(data["violation_count"], i)
            self.assertEqual(data["attempt_status"], "stopped" if i == 3 else "in_progress")
        # The acknowledgement can be lost: replay must neither count nor grade twice.
        retry = json.loads((await http("POST", f"/student/event/{self.attempt.id}", event, self.student_cookie))[1])
        self.assertEqual(retry["status"], "already_saved")
        self.assertEqual(retry["attempt_status"], "stopped")
        self.assertEqual(retry["stop_reason"], "violations")
        self.db.expire_all()
        self.assertEqual(self.attempt.status, models.AttemptStatus.stopped)
        self.assertIsNotNone(self.attempt.finished_at)
        self.assertGreater(self.attempt.score, 0)
        self.assertEqual(other_attempt.status, models.AttemptStatus.in_progress)
        self.assertEqual(self.db.query(models.EventLog).filter_by(attempt_id=self.attempt.id, event_type=models.EventType.stop_test).count(), 1)
        late_event = {**event, "client_event_id": "exit-4"}
        data = json.loads((await http("POST", f"/student/event/{self.attempt.id}", late_event, self.student_cookie))[1])
        self.assertEqual(data["violation_count"], 3)
        self.assertEqual((await self.submit(self.questions["short_text"], text="late"))[0], 409)
        self.assertEqual((await http("GET", f"/student/test/{self.attempt.id}", cookie=self.student_cookie))[0], 307)
        page = await http("GET", f"/student/test/{self.attempt.id}/finished", cookie=self.student_cookie)
        self.assertIn("Автоматична зупинка після третього порушення", page[1])
        state = json.loads((await http("GET", f"/api/attempt/{self.attempt.id}", cookie=self.student_cookie))[1])
        self.assertEqual(state["stop_reason"], "violations")
        monitor = json.loads((await http("GET", f"/api/session-status/{self.session.id}", cookie=self.teacher_cookie))[1])
        self.assertEqual(next(a for a in monitor["attempts"] if a["id"] == self.attempt.id)["violation_count"], 3)

    async def test_concurrent_event_retries_count_once_and_stop_once(self):
        path = f"/student/event/{self.attempt.id}"
        event = {"event_type": "tab_blur", "details": "fullscreen_exit", "client_event_id": "concurrent-1"}
        replies = await asyncio.gather(*(http("POST", path, event, self.student_cookie) for _ in range(3)))
        self.assertTrue(all(r[0] == 200 for r in replies))
        self.assertEqual(sorted(json.loads(r[1])["status"] for r in replies), ["already_saved", "already_saved", "ok"])
        replies = await asyncio.gather(*(http("POST", path, {**event, "client_event_id": f"concurrent-{i}"}, self.student_cookie) for i in range(2, 5)))
        self.assertTrue(all(r[0] == 200 for r in replies))
        self.db.expire_all()
        self.assertEqual(self.attempt.status, models.AttemptStatus.stopped)
        self.assertEqual(self.db.query(models.EventLog).filter_by(attempt_id=self.attempt.id, event_type=models.EventType.tab_blur).count(), 3)
        self.assertEqual(self.db.query(models.EventLog).filter_by(attempt_id=self.attempt.id, event_type=models.EventType.stop_test).count(), 1)

    async def test_site_version_and_asset_cache_busting(self):
        from app.version import __version__
        status, body, headers = await http("GET", "/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], __version__)
        self.assertEqual(headers[b"x-app-version"].decode(), __version__)
        page = await http("GET", f"/student/test/{self.attempt.id}", cookie=self.student_cookie)
        self.assertIn(f"v{__version__}", page[1])
        about_status, about_body, _ = await http("GET", "/teacher/about", cookie=self.teacher_cookie)
        self.assertEqual(about_status, 200)
        self.assertIn(f"версія {__version__}", about_body)
        for asset in ("student.js", "student-cache.js", "styles.css"):
            self.assertIn(f"{asset}?v={__version__}", page[1])

    async def test_payload_has_no_answer_keys_and_private_files(self):
        for q in self.payload["questions"]:
            for option in q["options"]:
                self.assertNotIn("is_correct", option)
                self.assertNotIn("matching_text", option)
                self.assertNotIn("order_index", option)
            if q["question_type"] in ("short_text", "hotspot"):
                self.assertEqual(q["options"], [])
        self.assertNotIn("private-text-answer", json.dumps(self.payload))
        from app.services.test_file_service import save_test_locally, resolve_static_image
        save_test_locally(self.test)
        self.assertTrue(list(Path("data/tests").rglob("test.json")))
        self.assertFalse(list(Path("app/static").rglob("test.json")))
        self.assertIsNone(resolve_static_image("/static/../../.env.png"))
        self.assertEqual((await http("GET", "/static/tests/legacy/test.json"))[0], 404)

    async def test_teacher_lists_search_filter_pagination_and_counts(self):
        for i in range(25):
            crud.create_test(self.db, self.owner.id, {"title": f"History {i}", "class_name": "8-B", "questions": []})
        crud.create_test(self.db, self.other.id, {"title": "History private", "class_name": "8-B", "questions": []})
        def listing(kind="tests", **kwargs):
            return teacher_list_service.build_list(self.db, self.owner.id, kind, **{
                "page": 1, "per_page": 10, "tab": "active", "search": "", "class_filter": "", "state": "all", **kwargs})
        data = listing(search="History", class_filter="8-B", page=2)
        self.assertEqual(data["total_items"], 25)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(len(data["tests_with_count"]), 10)
        self.assertEqual(listing(search="%_")["total_items"], 0)
        self.test.title = 'Алгебра'
        self.db.commit()
        self.assertEqual(listing(search='АЛГЕБРА')["total_items"], 1)
        self.assertEqual(listing(search='алгебра')["total_items"], 1)
        from sqlalchemy import event
        statements = []
        def track(*args): statements.append(args[2])
        event.listen(engine, 'before_cursor_execute', track)
        try:
            with SessionLocal() as isolated:
                for kind, maximum in [('tests', 7), ('sessions', 6)]:
                    statements.clear()
                    teacher_list_service.build_list(isolated, self.owner.id, kind, page=1, per_page=10,
                                                    tab='active', search='', class_filter='', state='all')
                    self.assertLessEqual(len(statements), maximum, kind)
        finally:
            event.remove(engine, 'before_cursor_execute', track)

        self.test.is_archived = True
        self.db.commit()
        self.assertEqual(listing(tab="archive")["total_items"], 1)
        self.assertEqual(listing(tab="archive")["tests_with_count"][0]["question_count"], 8)
        self.assertEqual(listing("sessions", search="CHECK1", state="running")["sessions_data"][0]["attempt_count"], 1)
        for url in ("/teacher/tests?q=History&class_filter=8-B&page=2&per_page=10", "/teacher/sessions?state=running", "/teacher/tests?tab=archive"):
            status, body, headers = await http("GET", url, cookie=self.teacher_cookie)
            self.assertEqual(status, 200, body[:300])
            self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertNotIn("History private", body)

    async def test_unassigned_question_from_same_test_is_rejected(self):
        self.test.random_questions_limit = 2
        self.db.commit()
        new_attempt = crud.start_attempt(self.db, crud.create_attempt(self.db, self.session.id, 'Subset Student'))
        payload = test_service.build_test_payload(self.db, new_attempt)
        assigned = {q['id'] for q in payload['questions']}
        unassigned = next(q for q in self.test.questions if q.id not in assigned)
        cookie = cookie_for(set_student_cookie, new_attempt.id)
        response = await http('POST', f'/student/test/{new_attempt.id}/save-answer',
                              {'question_id': unassigned.id, 'answer_text': '', 'selected_options': None}, cookie)
        self.assertEqual(response[0], 404)
        self.assertEqual(self.db.query(models.StudentAnswer).filter_by(attempt_id=new_attempt.id).count(), 2)

    async def test_checkbox_and_safe_editor_json(self):
        title = 'Safe </script><script>alert(1)</script>'
        status, body, _ = await http('POST', '/teacher/tests/create', {'title': title}, self.teacher_cookie, form=True)
        self.assertEqual(status, 303, body)
        self.db.expire_all()
        created = self.db.query(models.Test).order_by(models.Test.id.desc()).first()
        self.assertFalse(created.show_result_after_finish)
        page = await http('GET', f'/teacher/tests/{created.id}/edit', cookie=self.teacher_cookie)
        self.assertEqual(page[0], 200)
        self.assertNotIn('</script><script>alert(1)</script>', page[1])
        created.show_result_after_finish = True
        self.db.commit()
        result = await http('POST', f'/teacher/tests/{created.id}/edit', {'title': title}, self.teacher_cookie, form=True)
        self.assertEqual(result[0], 200, result[1])
        self.db.expire_all()
        self.assertFalse(created.show_result_after_finish)

    async def test_upload_ownership_formats_and_embedded_images(self):
        import base64
        from app.services.import_export_service import save_base64_image
        from app.services.media_service import MAX_IMAGE_BYTES, validate_image
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aNjcAAAAASUVORK5CYII=')
        def multipart(filename, content):
            return b'--checkboundary\r\nContent-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"\r\nContent-Type: application/octet-stream\r\n\r\n' + content + b'\r\n--checkboundary--\r\n'
        async def upload(filename, content, cookie):
            return await http('POST', f'/teacher/upload-image?test_id={self.test.id}', multipart(filename, content), cookie,
                              content_type='multipart/form-data; boundary=checkboundary')
        self.assertEqual((await upload('image.png', png, self.other_cookie))[0], 404)
        self.assertEqual((await upload('page.html', b'<html>data</html>', self.teacher_cookie))[0], 415)
        self.assertEqual((await upload('fake.png', b'Not PNG', self.teacher_cookie))[0], 415)
        self.assertEqual((await upload('image.PNG', png, self.teacher_cookie))[0], 200)
        with self.assertRaises(ValueError):
            validate_image(b'x' * (MAX_IMAGE_BYTES + 1), '.png')
        with self.assertRaises(ValueError):
            save_base64_image(base64.b64encode(b'<html>data</html>').decode(), '.html', 'check-images')
        self.assertTrue(save_base64_image(base64.b64encode(png).decode(), '.png', 'check-images').endswith('.png'))

    async def test_export_import_preserves_test_settings(self):
        from app.routes.teacher import _test_to_dict
        from app.services.import_export_service import import_test_from_json
        self.test.time_limit_per_question = 45
        self.test.use_fuzzy_matching = True
        self.test.random_questions_limit = 5
        self.test.max_grade = 10
        self.db.commit()
        copied = import_test_from_json(self.db, self.owner.id, json.dumps(_test_to_dict(self.test)))
        for field in ("time_limit_per_question", "use_fuzzy_matching", "random_questions_limit", "max_grade", "allow_retake"):
            self.assertEqual(getattr(copied, field), getattr(self.test, field), field)

    async def test_mtf_http_import_and_grading(self):
        from mtf_fixture import build_mtf
        from app.services.result_service import grade_answer as evaluate_answer
        from app.services.test_file_service import resolve_static_image

        def upload(data, filename='synthetic.MTF'):
            boundary = 'schooltest-mtf-check'
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode() + data
            body += f'\r\n--{boundary}--\r\n'.encode()
            return body, f'multipart/form-data; boundary={boundary}'

        payload, content_type = upload(build_mtf())
        status, _, _ = await http('POST', '/teacher/tests/import/mtf', payload, content_type=content_type)
        self.assertIn(status, (302, 303, 401, 403))
        status, body, _ = await http('POST', '/teacher/tests/import/mtf', payload, self.teacher_cookie, content_type=content_type)
        self.assertEqual(status, 200, body)
        imported = crud.get_test_by_id(self.db, json.loads(body)['test_id'])
        self.assertEqual(imported.title, 'Synthetic MTF')
        self.assertEqual(imported.teacher_id, self.owner.id)
        self.assertEqual(len(imported.questions), 8)
        questions = sorted(imported.questions, key=lambda q: q.order_index)
        self.assertEqual([o.is_correct for o in questions[0].options], [False, True])
        self.assertEqual([o.option_text for o in questions[2].options], ['First', 'Second'])
        self.assertEqual([o.matching_text for o in questions[3].options], ['Right A', 'Right B'])
        for answer in ('2,0', '2.0'):
            self.assertEqual(evaluate_answer(questions[4], answer, None), (True, 2))
        self.assertEqual(evaluate_answer(questions[4], 'wrong', None), (False, 0))
        self.assertEqual(evaluate_answer(questions[5], '16', None), (True, 2))
        from unittest.mock import patch
        with patch('app.config.settings.STATIC_DIR', str(Path('app/static').resolve())):
            self.assertTrue(resolve_static_image(questions[6].image_url).exists())
            self.assertTrue(all(resolve_static_image(o.image_url).exists() for o in questions[7].options))
        self.assertTrue(all(q.topic == 'Topic' for q in questions))
        status, body, _ = await http('GET', f'/teacher/tests/{imported.id}/edit', cookie=self.teacher_cookie)
        self.assertEqual(status, 200, body)
        for path in ('/teacher/dashboard', '/teacher/tests'):
            status, body, _ = await http('GET', path, cookie=self.teacher_cookie)
            self.assertEqual(status, 200, body)
            self.assertIn('importMtfInput', body)
            self.assertIn('test-import.js', body)
        before = self.db.query(models.Test).count()
        for data, filename in [(b'broken', 'broken.mtf'), (build_mtf()[:-20], 'broken.mtf'), (build_mtf(), 'wrong.xml')]:
            payload, content_type = upload(data, filename)
            status, body, _ = await http('POST', '/teacher/tests/import/mtf', payload, self.teacher_cookie, content_type=content_type)
            self.assertEqual(status, 400, body)
        self.assertEqual(self.db.query(models.Test).count(), before)


    def bind_roster(self):
        import datetime as dt
        import hashlib
        group=models.RosterClass(name='7-А',grade_level=7,letter='А');self.db.add(group);self.db.flush()
        subject=models.RosterSubject(name='Вигаданий предмет');self.db.add(subject);self.db.flush()
        pupils=[models.RosterStudent(class_id=group.id,first_name='Учень',last_name='Вигаданий') for _ in range(2)]
        self.db.add_all(pupils);self.db.flush()
        self.session.roster_class_id=group.id;self.session.roster_subject_id=subject.id;self.session.lesson_date='2026-09-05'
        self.attempt.roster_student_id=pupils[0].id;self.attempt.status=models.AttemptStatus.finished
        self.attempt.score=8;self.attempt.max_score=12;self.attempt.finished_at=dt.datetime(2026,9,5,12)
        self.db.add(models.GradeExportGrant(teacher_id=self.owner.id,token_hash=hashlib.sha256(b'synthetic-v2').hexdigest()))
        self.db.commit()
        return group,subject,pupils

    async def test_v2_scoped_final_grades_and_retake_id(self):
        import datetime as dt
        group,subject,pupils=self.bind_roster()
        path=f'/api/v2/journal/grades/?class_id={group.id}&subject_id={subject.id}&date_from=2026-09-01&date_to=2026-09-30'
        status,body,_=await http('GET',path,authorization='Bearer synthetic-v2');self.assertEqual(status,200)
        first=json.loads(body)['grades'][0];self.assertEqual(first['value'],'8');self.assertEqual(first['student_id'],pupils[0].id)
        new=crud.create_attempt(self.db,self.session.id,pupils[0].full_name(),roster_student_id=pupils[0].id)
        self.assertEqual(json.loads((await http('GET',path,authorization='Bearer synthetic-v2'))[1])['grades'],[])
        new.score=9;new.max_score=12;new.status=models.AttemptStatus.finished;new.finished_at=dt.datetime(2026,9,5,13);self.db.commit()
        row=json.loads((await http('GET',path,authorization='Bearer synthetic-v2'))[1])['grades'][0]
        self.assertEqual(row['id'],first['id']);self.assertEqual(row['value'],'9')
        self.assertEqual((await http('GET',path))[0],401)
        self.assertEqual(json.loads((await http('GET',path.replace(f'subject_id={subject.id}','subject_id=999'),authorization='Bearer synthetic-v2'))[1])['grades'],[])

    async def test_v2_same_names_choose_distinct_ids(self):
        group,subject,pupils=self.bind_roster()
        response=await http('GET','/api/session-roster?code=CHECK1');self.assertEqual(response[0],200)
        self.assertEqual(len(json.loads(response[1])['students']),2)
        result=await http('POST','/student/login',{'student_name':'Вигаданий Учень','access_code':'CHECK1','roster_student_id':pupils[1].id},form=True)
        self.assertEqual(result[0],303)
        self.db.expire_all();latest=self.db.query(models.StudentAttempt).order_by(models.StudentAttempt.id.desc()).first()
        self.assertEqual(latest.roster_student_id,pupils[1].id)
        result=await http('POST','/student/login',{'student_name':'Вигаданий Учень','access_code':'CHECK1','roster_student_id':999},form=True)
        self.assertEqual(result[0],400)

    async def test_integration_pages_csrf_and_owner_scope(self):
        self.bind_roster()
        status,body,_=await http('GET','/teacher/integrations',cookie=self.teacher_cookie);self.assertEqual(status,200)
        token=re.search(r'name="csrf" value="([^"]+)"',body).group(1)
        self.assertEqual((await http('GET','/teacher/roster-sync',cookie=self.teacher_cookie))[0],200)
        self.assertEqual((await http('POST','/teacher/integrations',{'action':'key'},self.teacher_cookie,form=True))[0],403)
        result=await http('POST','/teacher/integrations',{'action':'bind','session_id':self.session.id,'csrf':token},self.other_cookie,form=True)
        self.assertEqual(result[0],403)
        self.assertEqual((await http('GET','/api/v2/roster-sync/'))[0],401)

    async def test_simple_connection_code_prepares_names_but_does_not_merge_students(self):
        import html
        self.test.subject = 'Вигаданий предмет'
        self.db.commit()
        page = await http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        token = re.search(r'name="csrf" value="([^"]+)"', page[1]).group(1)
        old_grant = models.GradeExportGrant(teacher_id=self.owner.id, token_hash='synthetic-existing-grant-hash', active=True)
        self.db.add(old_grant)
        self.db.commit()
        result = await http('POST', '/teacher/integrations', {'action': 'simple_key', 'csrf': token}, self.teacher_cookie, form=True)
        self.assertEqual(result[0], 200)
        code = json.loads(html.unescape(re.search(r'id="journal-connection-code"[^>]*>(.*?)</textarea>', result[1], re.S).group(1)))
        self.assertEqual(code['source'], 'schooltest')
        plain = html.unescape(re.search(r'id="journal-api-key"[^>]*>(.*?)</textarea>', result[1], re.S).group(1))
        self.assertEqual(plain, code['api_key'])
        self.assertFalse(plain.startswith('{'))
        roster = await http('GET', '/api/v2/journal/roster/', authorization='Bearer ' + code['api_key'])
        self.assertEqual(roster[0], 200)
        self.assertEqual(json.loads(roster[1])['classes'][0]['name'], '7-A')
        self.assertEqual(json.loads(roster[1])['subjects'][0]['name'], 'Вигаданий предмет')
        self.db.expire_all()
        self.assertIsNone(self.db.get(models.StudentAttempt, self.attempt.id).roster_student_id)
        self.assertEqual(self.db.query(models.RosterStudent).count(), 0)
        self.db.refresh(old_grant)
        self.assertTrue(old_grant.active)
        self.db.refresh(self.session)
        self.assertIsNone(self.session.roster_class_id)
        self.assertIsNone(self.session.roster_subject_id)
        page = await http('GET', '/api/session-roster?code=' + self.session.access_code)
        self.assertFalse(json.loads(page[1])['required'])
        login = await http('POST', '/student/login', {'student_name': 'Інший Вигаданий', 'access_code': self.session.access_code}, form=True)
        self.assertEqual(login[0], 303, login[1])
        self.db.expire_all()
        self.assertIsNone(self.db.query(models.StudentAttempt).order_by(models.StudentAttempt.id.desc()).first().roster_student_id)
        import datetime as dt
        self.attempt.status = models.AttemptStatus.finished
        self.attempt.score, self.attempt.max_score = 6, 12
        self.attempt.finished_at = dt.datetime(2026, 9, 5, 12)
        self.db.commit()
        rows = json.loads(roster[1])
        exported = await http('GET', '/api/v2/journal/grades/?' + urlencode({'class_id': rows['classes'][0]['id'], 'subject_id': rows['subjects'][0]['id'], 'date_from': '2026-09-01', 'date_to': '2026-09-30'}), authorization='Bearer ' + code['api_key'])
        self.assertEqual(exported[0], 200)
        self.assertEqual(len(json.loads(exported[1])['grades']), 1)
        self.assertIsNone(json.loads(exported[1])['grades'][0]['student_id'])

    async def test_api_key_keeps_explicit_roster_login_requirement(self):
        self.bind_roster()
        original_class = self.session.roster_class_id
        page = await http('GET', '/teacher/integrations', cookie=self.teacher_cookie)
        token = re.search(r'name="csrf" value="([^"]+)"', page[1]).group(1)
        await http('POST', '/teacher/integrations', {'action': 'simple_key', 'csrf': token}, self.teacher_cookie, form=True)
        self.db.refresh(self.session)
        self.assertEqual(self.session.roster_class_id, original_class)
        roster = await http('GET', '/api/session-roster?code=' + self.session.access_code)
        self.assertTrue(json.loads(roster[1])['required'])
        attempts = self.db.query(models.StudentAttempt).count()
        login = await http('POST', '/student/login', {'student_name': 'Інший Вигаданий', 'access_code': self.session.access_code}, form=True)
        self.assertEqual(login[0], 400)
        self.assertEqual(self.db.query(models.StudentAttempt).count(), attempts)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        engine.dispose()
        WORK.cleanup()
