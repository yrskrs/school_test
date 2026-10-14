"""Seed/verify only the disposable PostgreSQL browser-review environment."""
import copy
import os
import sys
import uuid

from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if (url.host, url.database, url.username) != ('postgres', 'schooltest_runtime_check', 'runtime_check'):
    raise RuntimeError('Only the disposable roster-review PostgreSQL database is allowed')

from app import crud, models
from app.database import SessionLocal
from app.services.roster_backend import operate
from school_sync.core import Engine
from school_sync.tests.test_protocol import Native, Store


def seed():
    source = Engine(Store(), Native())
    group_id = source.native.create('class', {'name': '7-А', 'grade_level': 7, 'letter': 'А', 'active': True})
    names = [('Коваленко', 'Ірина'), ('Шевченко', 'Олег'), ('Новий', 'Учень')]
    for last, first in names:
        source.native.create('student', {'name': last+' '+first, 'first_name': first,
            'last_name': last, 'middle_name': '', 'native_class_id': group_id, 'active': True})
    source.share('class', group_id)
    with SessionLocal() as db:
        owner = crud.create_teacher(db, 'review_audit', 'Synthetic Review Teacher', 'synthetic-review-password')
        group = models.RosterClass(name='7-А', grade_level=7, letter='А')
        other = models.RosterClass(name='9-Б', grade_level=9, letter='Б')
        db.add_all([group, other]); db.flush()
        pupils = [models.RosterStudent(class_id=group.id, last_name=last, first_name=first) for last, first in names[:2]]
        db.add_all([*pupils, models.RosterStudent(class_id=other.id, first_name='Учень', last_name='Чужий')]); db.flush()
        test = crud.create_test(db, owner.id, {'title': 'Synthetic review history', 'class_name': '7-А',
            'questions': [{'question_text': '2+2?', 'options': [{'option_text': '4', 'is_correct': True}, {'option_text': '5'}]}]})
        session = crud.create_session(db, test.id, 'REVIEW1')
        attempt = crud.create_attempt(db, session.id, pupils[0].full_name(), roster_student_id=pupils[0].id)
        attempt.status, attempt.score, attempt.max_score = models.AttemptStatus.finished, 1, 1
        attempt_id, pupil_id = attempt.id, pupils[0].id
        for archive in (False, True):
            outside = crud.create_attempt(db, session.id, 'Архівний Учень' if archive else 'Окремий Учень', outside_student_id=str(uuid.uuid4()))
            outside.status, outside.score, outside.max_score, outside.is_archived = models.AttemptStatus.finished, 1, 1, archive
        db.commit()
        def receive(engine, store):
            engine.receive(source.snapshot())
            store.meta['audit_review'] = {'attempt_id': attempt_id, 'pupil_id': pupil_id,
                'source_origin': source.origin, 'student_count': db.query(models.RosterStudent).count()}
        operate(receive, db)
    print('Seeded synthetic class, two matches, new pupil, foreign class, historical result and active/archive outside results')


def verify():
    with SessionLocal() as db:
        def check(engine, store):
            audit = store.meta['audit_review']
            rows = [row for row in store.all() if row['kind'] != 'alias']
            assert all(row['status'] == 'ready' for row in rows)
            assert db.query(models.RosterStudent).count() == audit['student_count'] + 1
            historical = db.get(models.StudentAttempt, audit['attempt_id'])
            assert historical.roster_student_id == audit['pupil_id'] and historical.score == 1
            pupil = engine.native_record('student', audit['pupil_id'])
            updated = copy.deepcopy(pupil)
            updated['payload'].update(name='Коваленко Іринка', first_name='Іринка')
            updated['clock'][audit['source_origin']] += 1
            snapshot = {'schema_version': 2, 'origin': audit['source_origin'], 'records': [
                {key: updated[key] for key in ('ref', 'kind', 'payload', 'clock')}]}
            engine.receive(snapshot); engine.receive(snapshot)
            assert db.get(models.RosterStudent, audit['pupil_id']).first_name == 'Іринка'
            assert historical.roster_student_id == audit['pupil_id']
        operate(check, db)
    print('PASS PostgreSQL review: exact matches preserve IDs/results, explicit create adds only one pupil, repeated remote update is automatic and idempotent')


{'seed': seed, 'verify': verify}[sys.argv[1]]()
