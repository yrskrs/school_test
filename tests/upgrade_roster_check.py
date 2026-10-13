"""Seed 1.5.9, then verify the real 1.6.1 upgrade on disposable PostgreSQL.

Never run this against a working installation. The URL guard is mandatory.
"""
import hashlib
import json
import os
import sys
import uuid
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if url.host != 'postgres' or url.database != 'schooltest_runtime_check' or url.username != 'runtime_check':
    raise RuntimeError('Only the disposable runtime-check PostgreSQL database is allowed')

from app import crud, models
from app.database import Base, SessionLocal
from app.version import __version__

PASSWORD = 'synthetic-roster-password'
KEY = 'synthetic-upgrade-roster-key'
client = build_opener(HTTPCookieProcessor(CookieJar()))


def http(path, fields=None, *, json_body=False, headers=None):
    headers = {'Accept': 'application/json', **(headers or {})}
    body = None
    if fields is not None:
        body = (json.dumps(fields).encode() if json_body else urlencode(fields).encode())
        headers['Content-Type'] = 'application/json' if json_body else 'application/x-www-form-urlencoded'
    with client.open(Request('http://app:8000' + path, data=body, headers=headers), timeout=15) as response:
        return response.read().decode(), response.geturl()


def snapshot(db, expected=None):
    result = {}
    for table in Base.metadata.sorted_tables:
        columns = expected[table.name]['columns'] if expected else [c['name'] for c in inspect(db.bind).get_columns(table.name)]
        quoted = ', '.join('"' + c + '"' for c in columns)
        order = ', '.join('"' + c.name + '"' for c in table.primary_key.columns)
        rows = [dict(row) for row in db.execute(text(f'SELECT {quoted} FROM "{table.name}" ORDER BY {order}')).mappings()]
        result[table.name] = {'columns': columns, 'rows': rows}
    return json.loads(json.dumps(result, default=str, sort_keys=True))


def seed():
    assert __version__ in ('1.5.9', '1.6.0'), __version__
    with SessionLocal() as db:
        assert db.query(models.Teacher).count() == 0
        owner = crud.create_teacher(db, 'roster_audit', 'Synthetic Upgrade Teacher', PASSWORD, classes='10-А, 11-А')
        test = crud.create_test(db, owner.id, {
            'title': 'Synthetic original 11-А', 'class_name': '11-А', 'allow_retake': True,
            'questions': [{'question_text': '2 + 2?', 'question_type': 'single_choice', 'points': 2,
                'options': [{'option_text': '4', 'is_correct': True}, {'option_text': '5'}]}],
        })
        session = crud.create_session(db, test.id, 'SEED01')
        session.is_pinned = True
        attempt = crud.start_attempt(db, crud.create_attempt(db, session.id, 'Synthetic Historical Pupil'))
        from app.services.test_service import build_test_payload
        build_test_payload(db, attempt)
        answer = attempt.answers[0]
        answer.selected_options_json = json.dumps([test.questions[0].options[0].id])
        answer.is_correct, answer.awarded_points = True, 2
        attempt.status, attempt.score, attempt.max_score = models.AttemptStatus.finished, 2, 2
        db.add(models.GradeExportGrant(teacher_id=owner.id, token_hash=hashlib.sha256(KEY.encode()).hexdigest()))
        db.commit()
    namespace = uuid.UUID('11111111-1111-4111-8111-111111111111')
    payload = {'schema_version': 2, 'journal_instance_id': str(namespace),
        'class': {'id': str(uuid.uuid5(namespace, 'class')), 'name': '10-В', 'grade_level': 10, 'letter': 'В'},
        'subject': {'name': 'Synthetic Subject'},
        'students': [{'id': str(uuid.uuid5(namespace, str(i))),
            'first_name': hashlib.sha256(f'first{i}'.encode()).hexdigest()[:12],
            'last_name': hashlib.sha256(f'last{i}'.encode()).hexdigest()[:12]} for i in range(1, 18)]}
    response, _ = http('/api/v2/journal/roster/', payload, json_body=True, headers={'Authorization': 'Bearer ' + KEY})
    assert json.loads(response)['counts']['created'] == 17
    with SessionLocal() as db:
        saved = snapshot(db)
        db.execute(text('CREATE TABLE audit_upgrade_snapshot (snapshot TEXT NOT NULL)'))
        db.execute(text('INSERT INTO audit_upgrade_snapshot VALUES (:value)'), {'value': json.dumps(saved, ensure_ascii=False)})
        db.commit()
    print(f'SEEDED {__version__}: profile, test, pinned session, historical answer, API key and 17 imported pupils')


def verify():
    assert __version__ == '1.6.1', __version__
    with SessionLocal() as db:
        saved = json.loads(db.execute(text('SELECT snapshot FROM audit_upgrade_snapshot')).scalar_one())
        actual = snapshot(db, saved)
        for table in saved:
            assert actual[table] == saved[table], f'Upgrade changed existing rows: {table}'
        test = db.query(models.Test).filter_by(title='Synthetic original 11-А').one()
        assert test.allow_retake and test.attempt_limit is None
        assert not test.allow_outside_class
        assert db.query(models.RosterStudent).count() == 17
    print('PASS upgrade: every old column/row/ID, profile, key, roster mapping, answer and result preserved')
    # Real legacy rows retain NULL markers, so old signed cookies survive the
    # additive upgrade. New rows get distinct markers and cannot inherit them.
    import base64
    import time
    from app.security import _sign
    with SessionLocal() as db:
        owner = db.query(models.Teacher).filter_by(username='roster_audit').one()
        legacy = db.query(models.StudentAttempt).filter_by(student_name='Synthetic Historical Pupil').one()
        assert owner.auth_nonce is None and legacy.auth_nonce is None
        payload = base64.b64encode(json.dumps({'id': owner.id, 'u': owner.username, 't': int(time.time())}).encode()).decode()
        teacher_cookie = 'teacher_session=' + _sign(payload)
        student_cookie = 'student_attempt=' + _sign(str(legacy.id))
        legacy_id = legacy.id
    page, _ = http('/teacher/tests/create', headers={'Cookie': teacher_cookie})
    assert '<option value="10-В">' in page
    finished, _ = http(f'/student/test/{legacy_id}/finished', headers={'Cookie': student_cookie})
    assert 'Synthetic Historical Pupil' in finished
    print('PASS upgrade: legacy teacher/student cookies still authorize their original records')
    from app.services.roster_backend import operate
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    operate(lambda engine, store: store.meta.update(audit_counter=0))
    barrier = threading.Barrier(2)
    def update_metadata(_):
        barrier.wait(timeout=5)
        def change(engine, store):
            value = store.meta['audit_counter']
            time.sleep(.1)
            store.meta['audit_counter'] = value + 1
        operate(change)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update_metadata, range(2)))
    with SessionLocal() as db:
        assert db.get(models.RosterState, 1).data['audit_counter'] == 2
    print('PASS PostgreSQL: competing roster-state writes preserve both metadata updates')
    body, _ = http('/api/version')
    assert json.loads(body)['version'] == __version__
    http('/teacher/login', {'username': 'roster_audit', 'password': PASSWORD})
    page, _ = http('/teacher/tests/create')
    assert '<option value="10-В">' in page
    roster, _ = http('/api/session-roster?code=SEED01')
    assert not json.loads(roster)['available']  # Original test is still 11-А.
    questions = [{'question_text': '2 + 2?', 'question_type': 'single_choice', 'points': 2,
        'options': [{'option_text': '4', 'is_correct': True}, {'option_text': '5'}]}]
    _, edit_url = http('/teacher/tests/create', {
        'title': 'Synthetic upgraded 10-В', 'class_name': '10-В', 'attempt_limit_mode': 'limited',
        'max_attempts': '2', 'show_result_after_finish': 'true', 'questions_json': json.dumps(questions),
    })
    test_id = int(edit_url.split('/tests/')[1].split('/')[0])
    _, monitor_url = http(f'/teacher/tests/{test_id}/start-session', {})
    session_id = int(monitor_url.rsplit('/', 2)[1])
    with SessionLocal() as db:
        session = db.get(models.TestSession, session_id)
        assert session.roster_class_id is not None
        code = session.access_code
        owner_id = session.test.teacher_id
    data, _ = http('/api/session-roster?code=' + code)
    roster = json.loads(data)
    assert len(roster['students']) == 17 and roster['class_name'] == '10-В'
    assert roster['required'] and not roster['allow_outside_class']
    with SessionLocal() as db:
        # PostgreSQL admission lock: two HTTP clients for one pupil cannot
        # create parallel unfinished attempts or evade a one-attempt limit.
        db.get(models.Test, test_id).max_attempts = 1
        db.commit()
    from concurrent.futures import ThreadPoolExecutor
    from urllib.error import HTTPError
    def enter(_):
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        try:
            with opener.open(Request('http://app:8000/student/login', data=urlencode({
                'access_code': code, 'roster_student_id': roster['students'][0]['id'],
            }).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=15) as response:
                return response.status
        except HTTPError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(enter, range(2)))
    assert results == [200, 409], results
    with SessionLocal() as db:
        assert db.query(models.StudentAttempt).filter_by(session_id=session_id).count() == 1
        db.get(models.Test, test_id).max_attempts = 2
        # A deterministic code for the isolated browser suite.
        session = db.get(models.TestSession, session_id)
        session.access_code = 'BROWS1'
        db.commit()
    print('PASS PostgreSQL: create/start/select 10-В, 17 pupils, scoped API and competing login lock')


if __name__ == '__main__':
    {'seed': seed, 'verify': verify}[sys.argv[1]]()
