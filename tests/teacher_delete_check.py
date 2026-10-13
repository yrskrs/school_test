"""Account removal and API-key cleanup on the disposable PostgreSQL only."""
import hashlib
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import text
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if url.host != 'postgres' or url.database != 'schooltest_runtime_check' or url.username != 'runtime_check':
    raise RuntimeError('Only the disposable PostgreSQL check database is allowed')

from app import crud, models
from app.database import SessionLocal
from app.security import create_teacher_session
from app.services.roster_backend import operate
from app.services.test_service import build_test_payload
from fastapi.responses import Response

TOKEN = 'synthetic-delete-audit-key'


def remove():
    with SessionLocal() as db:
        admin = db.query(models.Teacher).filter_by(username='admin').one()
        response = Response()
        create_teacher_session(response, admin.id, admin.username)
        cookie = response.headers['set-cookie'].split(';', 1)[0]
    request = Request('http://app:8000/teacher/profile/delete-teacher',
        data=urlencode({'username': 'delete_audit'}).encode(),
        headers={'Cookie': cookie, 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def seed_and_check_baseline(expect_error=True):
    with SessionLocal() as db:
        if not db.query(models.Teacher).filter_by(username='admin').first():
            crud.create_teacher(db, 'admin', 'Synthetic Admin', 'synthetic-password')
        target = crud.create_teacher(db, 'delete_audit', 'Synthetic Deleted Teacher', 'synthetic-password')
        test = crud.create_test(db, target.id, {'title': 'Synthetic account removal', 'class_name': '9-X', 'questions': [
            {'question_type': 'single_choice', 'question_text': 'Synthetic question',
             'options': [{'option_text': 'A', 'is_correct': True}, {'option_text': 'B'}]},
        ]})
        session = crud.create_session(db, test.id, 'DELETE1')
        attempt = crud.start_attempt(db, crud.create_attempt(db, session.id, 'Synthetic Removed Attempt'))
        build_test_payload(db, attempt)
        group = models.RosterClass(name='9-X', grade_level=9)
        grant = models.GradeExportGrant(teacher_id=target.id, token_hash=hashlib.sha256(TOKEN.encode()).hexdigest())
        db.add_all([group, grant]); db.flush()
        pupil = models.RosterStudent(class_id=group.id, first_name='First', last_name='Synthetic')
        db.add(pupil); db.flush()
        ids = {'teacher': target.id, 'test': test.id, 'session': session.id, 'attempt': attempt.id,
               'group': group.id, 'pupil': pupil.id, 'grant': grant.id}
        db.add(models.TeacherLog(teacher_id=target.id, teacher_username=target.username, teacher_name=target.full_name,
            action='audit', details='Synthetic historical teacher log'))
        def metadata(engine, store):
            store.meta.setdefault('journal_imports', {})[f'{target.id}:synthetic:class'] = {
                'class_id': group.id, 'students': {}, 'class_names': ['9-X']}
            store.meta.setdefault('journal_api_stats', {})[str(grant.id)] = {'requests': 1}
        operate(metadata, db)
        db.execute(text('CREATE TABLE audit_delete_snapshot (snapshot TEXT NOT NULL)'))
        db.execute(text('INSERT INTO audit_delete_snapshot VALUES (:value)'), {'value': json.dumps(ids)})
        db.commit()
    if not expect_error:
        print('Prepared synthetic account, API grant, attempts, independent roster and historical log')
        return
    result = remove()
    assert result['status'] == 'error', 'Baseline unexpectedly removed an owner with a referencing API grant'
    with SessionLocal() as db:
        assert db.get(models.Teacher, ids['teacher']) is not None
        assert db.get(models.Test, ids['test']) is not None
        assert db.get(models.GradeExportGrant, ids['grant']) is not None
    print('EXPECTED BASELINE FAILURE: PostgreSQL refuses teacher deletion with an API key; rollback preserves data')


def verify():
    with SessionLocal() as db:
        ids = json.loads(db.execute(text('SELECT snapshot FROM audit_delete_snapshot')).scalar_one())
    result = remove()
    assert result['status'] == 'ok', result['status']
    with SessionLocal() as db:
        for model, key in ((models.Teacher, 'teacher'), (models.Test, 'test'), (models.TestSession, 'session'),
                           (models.StudentAttempt, 'attempt'), (models.GradeExportGrant, 'grant')):
            assert db.get(model, ids[key]) is None, key
        assert db.get(models.RosterClass, ids['group']) is not None
        assert db.get(models.RosterStudent, ids['pupil']) is not None
        log = db.query(models.TeacherLog).filter_by(teacher_username='delete_audit').one()
        assert log.teacher_id is None
        meta = db.get(models.RosterState, 1).data
        assert not any(key.startswith(str(ids['teacher']) + ':') for key in meta.get('journal_imports', {}))
        assert str(ids['grant']) not in meta.get('journal_api_stats', {})
    try:
        urlopen(Request('http://app:8000/api/v2/journal/roster/', headers={
            'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json'}), timeout=10)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError('Deleted API key still works')
    print('PASS PostgreSQL: account/tests/attempts/keys removed atomically; independent roster and historical log preserved; old key rejected')


if __name__ == '__main__':
    {'baseline': seed_and_check_baseline, 'prepare': lambda: seed_and_check_baseline(False), 'verify': verify}[sys.argv[1]]()
